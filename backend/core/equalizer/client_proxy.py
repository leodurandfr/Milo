# backend/core/equalizer/client_proxy.py
"""
Equalizer Client Proxy Service - Handles communication with remote milo-client equalizer APIs.

This service abstracts the complexity of proxying requests to satellite clients
in a multiroom setup, including:
- Health checks to verify client availability
- Request proxying (GET, PUT, POST) with proper error handling
- Multiroom mode validation before sending requests
"""
import asyncio
import ipaddress
import logging
from typing import Optional, Dict, Any

import aiohttp

from backend.config.constants import CLIENT_API_PORT


class SatelliteUnreachable(Exception):
    """A request to a remote milo-client satellite failed.

    Raised by the core proxy layer so it never depends on a web-layer
    exception (the error-handling doctrine forbids raising HTTPException
    from a service). The api/ layer maps this to an HTTPException with the
    carried ``status_code`` (api_error_handler in route_helpers.py); the
    background sync paths catch it via their generic ``except Exception``.
    """

    def __init__(self, hostname: str, detail: str, status_code: int = 503):
        self.hostname = hostname
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


def is_ip_address(hostname: str) -> bool:
    """
    Check if hostname is an IP address.

    Used to determine whether to add .local suffix for mDNS resolution.
    Supports both IPv4 and IPv6 addresses.

    Args:
        hostname: The hostname or IP to check

    Returns:
        True if hostname is a valid IP address, False otherwise
    """
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        return False


class EqualizerClientProxyService:
    """
    Service for proxying equalizer requests to remote milo-client instances.

    Used in multiroom setups to communicate with satellite devices
    running milo-client for equalizer control.

    IMPORTANT: This service expects IP addresses or hostnames, NOT MAC addresses.
    Callers must look up the IP from the client registry before calling proxy methods.
    """

    def __init__(self, routing_service=None):
        """
        Initialize the proxy service.

        Args:
            routing_service: Optional routing service for multiroom status checks.
                           If None, multiroom checks are skipped.
        """
        self.routing_service = routing_service
        self.logger = logging.getLogger(__name__)
        self._session: Optional[aiohttp.ClientSession] = None

    def _get_session(self) -> aiohttp.ClientSession:
        """Return the shared HTTP session, lazily creating it on first use.

        Reusing a single session enables TCP keep-alive across requests,
        which cuts handshake latency when rapidly fanning out volume / EQ
        commands to satellites.
        """
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session

    async def cleanup(self) -> None:
        """Close the shared HTTP session. Called from app shutdown."""
        if self._session is not None:
            await self._session.close()
            self._session = None

    def _get_host(self, identifier: str) -> str:
        """
        Get the full host address for a client.

        Args:
            identifier: IP address or hostname (NOT a MAC address)

        Returns:
            Host address suitable for HTTP requests

        The identifier can be:
        - IP address (e.g., "192.168.1.100") -> use directly
        - Hostname (e.g., "milo-client") -> add .local suffix

        IMPORTANT: MAC addresses are NOT supported. Callers must look up
        the IP from the client registry before calling proxy methods.
        """
        if is_ip_address(identifier):
            return identifier

        # Assume it's a hostname, add .local suffix for mDNS
        return f"{identifier}.local"

    async def request(
        self,
        hostname: str,
        method: str,
        path: str,
        body: Optional[Dict[str, Any]] = None,
        skip_multiroom_check: bool = False
    ) -> Dict[str, Any]:
        """
        Proxy a request to a client's equalizer API.

        Args:
            hostname: The client hostname or IP address
            method: HTTP method (GET, PUT, POST)
            path: API path (e.g., "/equalizer/volume")
            body: Optional request body for PUT/POST
            skip_multiroom_check: If True, skip the multiroom enabled check

        Returns:
            The JSON response from the client

        Raises:
            SatelliteUnreachable: If the request fails or multiroom is disabled.
                Mapped to an HTTPException in the api/ layer only.
        """
        # Check if multiroom is disabled - skip remote client requests
        if not skip_multiroom_check:
            try:
                if self.routing_service and not self.routing_service.multiroom_enabled:
                    self.logger.warning(f"Skipping proxy request to {hostname} - multiroom is disabled")
                    raise SatelliteUnreachable(
                        hostname, f"Multiroom is disabled, cannot reach {hostname}", status_code=503
                    )
            except SatelliteUnreachable:
                raise
            except Exception as e:
                # Log but continue if we can't check multiroom status
                self.logger.debug(f"Could not check multiroom status: {e}")

        try:
            host = self._get_host(hostname)
            url = f"http://{host}:{CLIENT_API_PORT}{path}"
            timeout = aiohttp.ClientTimeout(total=10)
            session = self._get_session()

            if method == "GET":
                ctx = session.get(url, timeout=timeout)
            elif method == "PUT":
                ctx = session.put(url, json=body, timeout=timeout)
            elif method == "POST":
                ctx = session.post(url, json=body, timeout=timeout)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            async with ctx as response:
                if response.status == 200:
                    return await response.json()
                raise SatelliteUnreachable(
                    hostname, f"Client error: {response.status}", status_code=response.status
                )

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            self.logger.warning(f"Cannot reach client {hostname}: {e}")
            raise SatelliteUnreachable(hostname, f"Cannot reach client {hostname}", status_code=503)
        except SatelliteUnreachable:
            raise
        except Exception as e:
            self.logger.error(f"Unexpected error proxying to {hostname}: {e}")
            raise SatelliteUnreachable(hostname, str(e), status_code=500)

    async def try_request(
        self,
        hostname: str,
        method: str,
        path: str,
        body: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ) -> int:
        """Non-raising request variant for background callers with their own
        retry / queue-pending semantics (e.g. CrossoverService).

        Returns the HTTP status code, or 0 if the client is unreachable. Reuses
        the shared keep-alive session instead of a throwaway per-request
        ClientSession, so it benefits from the same TCP keep-alive as request().

        Args:
            hostname: The client hostname or IP address
            method: HTTP method (GET, PUT, POST)
            path: API path (e.g. "/equalizer/crossover")
            body: Optional request body for PUT/POST
            timeout: Total request timeout in seconds
        """
        try:
            host = self._get_host(hostname)
            url = f"http://{host}:{CLIENT_API_PORT}{path}"
            client_timeout = aiohttp.ClientTimeout(total=timeout)
            session = self._get_session()

            if method == "GET":
                ctx = session.get(url, timeout=client_timeout)
            elif method == "PUT":
                ctx = session.put(url, json=body, timeout=client_timeout)
            elif method == "POST":
                ctx = session.post(url, json=body, timeout=client_timeout)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            async with ctx as response:
                return response.status

        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            self.logger.debug(f"Cannot reach client {hostname}: {e}")
            return 0

    async def apply_record(self, hostname: str, settings) -> bool:
        """Send a satellite one complete EQ record, in the canonical order.

        The single way a whole ``EqualizerSettings`` reaches a client: the live
        write (MultiroomEqualizerService), the reconnection sync
        (SnapcastWebSocketService) and the pending replay (CrossoverService) all
        come through here, so a client can never end up holding a record that was
        assembled differently depending on which path delivered it.

        Bands carry tuning only — their presence in the pipeline is what the
        master toggle switches — and ``enabled`` goes last, after the effects it
        gates. Returns False if any leg failed; callers own the retry policy.
        """
        try:
            await self.request(hostname, "PUT", "/equalizer/filters", {
                "filters": [
                    {
                        "id": f.id,
                        "gain": f.gain,
                        "freq": f.frequency,
                        "q": f.q,
                        "filter_type": f.filter_type.value,
                    }
                    for f in settings.filters
                ],
            })
            await self.request(hostname, "PUT", "/equalizer/compressor",
                               settings.compressor.to_dict())
            await self.request(hostname, "PUT", "/equalizer/loudness",
                               settings.loudness.to_dict())
            await self.request(hostname, "PUT", "/equalizer/mono",
                               {"enabled": settings.mono})
            await self.request(hostname, "PUT", "/equalizer/enabled",
                               {"enabled": settings.enabled})
            return True
        except Exception as e:
            self.logger.warning(f"Failed to apply equalizer record to {hostname}: {e}")
            return False

    async def get_equalizer_levels(self, hostname: str) -> Optional[Dict[str, Any]]:
        """
        Get equalizer levels from a client.

        Args:
            hostname: The client hostname or IP address

        Returns:
            The levels response or None if unavailable
        """
        try:
            host = self._get_host(hostname)
            url = f"http://{host}:{CLIENT_API_PORT}/equalizer/levels"
            timeout = aiohttp.ClientTimeout(total=1.0)  # Short timeout for levels polling
            async with self._get_session().get(url, timeout=timeout) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            self.logger.debug(f"Failed to get equalizer levels from {hostname}: {e}")
        return None
