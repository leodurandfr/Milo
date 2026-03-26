# backend/core/equalizer/client_proxy.py
"""
Equalizer Client Proxy Service - Handles communication with remote milo-client equalizer APIs.

This service abstracts the complexity of proxying requests to satellite clients
in a multiroom setup, including:
- Health checks to verify client availability
- Request proxying (GET, PUT, POST) with proper error handling
- Multiroom mode validation before sending requests
"""
import ipaddress
import logging
from typing import Optional, Dict, Any

import aiohttp
from fastapi import HTTPException

from backend.config.constants import (
    CLIENT_API_PORT,
    HEALTH_CHECK_TIMEOUT,
)


# =============================================================================
# Shared utility function (used by crossover_service, settings_sync_service)
# =============================================================================

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

    def set_routing_service(self, routing_service) -> None:
        """Set the routing service (for dependency injection after init)."""
        self.routing_service = routing_service

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
        # If it's an IP address, use directly
        if is_ip_address(identifier):
            return identifier

        # Assume it's a hostname, add .local suffix for mDNS
        return f"{identifier}.local"

    async def check_available(self, hostname: str) -> bool:
        """
        Check if a client's equalizer API is available AND equalizer is ready.

        Args:
            hostname: The client hostname or IP address

        Returns:
            True if the client's health endpoint responds with 200 AND equalizer_ready is true
        """
        try:
            host = self._get_host(hostname)
            timeout = aiohttp.ClientTimeout(total=HEALTH_CHECK_TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                url = f"http://{host}:{CLIENT_API_PORT}/health"
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        # Check equalizer_ready flag (default True for backward compatibility)
                        return data.get("equalizer_ready", True)
                    return False
        except Exception:
            return False

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
            HTTPException: If the request fails or multiroom is disabled
        """
        # Check if multiroom is disabled - skip remote client requests
        if not skip_multiroom_check:
            try:
                if self.routing_service and not await self.routing_service._get_multiroom_enabled():
                    self.logger.warning(f"Skipping proxy request to {hostname} - multiroom is disabled")
                    raise HTTPException(
                        status_code=503,
                        detail=f"Multiroom is disabled, cannot reach {hostname}"
                    )
            except HTTPException:
                raise
            except Exception as e:
                # Log but continue if we can't check multiroom status
                self.logger.debug(f"Could not check multiroom status: {e}")

        try:
            host = self._get_host(hostname)
            url = f"http://{host}:{CLIENT_API_PORT}{path}"
            timeout = aiohttp.ClientTimeout(total=10)

            async with aiohttp.ClientSession(timeout=timeout) as session:
                if method == "GET":
                    async with session.get(url) as response:
                        if response.status == 200:
                            return await response.json()
                        raise HTTPException(
                            status_code=response.status,
                            detail=f"Client error: {response.status}"
                        )
                elif method == "PUT":
                    async with session.put(url, json=body) as response:
                        if response.status == 200:
                            return await response.json()
                        raise HTTPException(
                            status_code=response.status,
                            detail=f"Client error: {response.status}"
                        )
                elif method == "POST":
                    async with session.post(url, json=body) as response:
                        if response.status == 200:
                            return await response.json()
                        raise HTTPException(
                            status_code=response.status,
                            detail=f"Client error: {response.status}"
                        )
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")

        except aiohttp.ClientError as e:
            self.logger.warning(f"Cannot reach client {hostname}: {e}")
            raise HTTPException(status_code=503, detail=f"Cannot reach client {hostname}")
        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Unexpected error proxying to {hostname}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

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
            timeout = aiohttp.ClientTimeout(total=1.0)  # Short timeout for levels polling
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"http://{host}:{CLIENT_API_PORT}/equalizer/levels") as resp:
                    if resp.status == 200:
                        return await resp.json()
        except Exception as e:
            self.logger.debug(f"Failed to get equalizer levels from {hostname}: {e}")
        return None
