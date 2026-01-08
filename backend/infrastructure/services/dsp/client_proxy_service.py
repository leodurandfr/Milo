# backend/infrastructure/services/dsp_client_proxy_service.py
"""
DSP Client Proxy Service - Handles communication with remote milo-client DSP APIs.

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
from fastapi import HTTPException

from backend.config.constants import (
    CLIENT_API_PORT,
    HEALTH_CHECK_TIMEOUT,
    CLIENT_REQUEST_TIMEOUT,
)


class DspClientProxyService:
    """
    Service for proxying DSP requests to remote milo-client instances.

    Used in multiroom setups to communicate with satellite devices
    running milo-client for DSP control.
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

    @staticmethod
    def is_ip_address(hostname: str) -> bool:
        """Check if hostname is an IP address (don't add .local suffix for IPs)."""
        try:
            ipaddress.ip_address(hostname)
            return True
        except ValueError:
            return False

    def _get_host(self, hostname: str) -> str:
        """Get the full host address, adding .local suffix if needed."""
        return hostname if self.is_ip_address(hostname) else f"{hostname}.local"

    async def check_available(self, hostname: str) -> bool:
        """
        Check if a client's DSP API is available.

        Args:
            hostname: The client hostname or IP address

        Returns:
            True if the client's health endpoint responds with 200, False otherwise
        """
        try:
            host = self._get_host(hostname)
            timeout = aiohttp.ClientTimeout(total=HEALTH_CHECK_TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                url = f"http://{host}:{CLIENT_API_PORT}/health"
                async with session.get(url) as response:
                    return response.status == 200
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
        Proxy a request to a client's DSP API.

        Args:
            hostname: The client hostname or IP address
            method: HTTP method (GET, PUT, POST)
            path: API path (e.g., "/dsp/volume")
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
            self.logger.error(f"Error proxying request to {hostname}: {e}")
            raise HTTPException(status_code=503, detail=f"Cannot reach client {hostname}")
        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Unexpected error proxying to {hostname}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    async def get_dsp_levels(self, hostname: str) -> Optional[Dict[str, Any]]:
        """
        Get DSP levels from a client.

        Args:
            hostname: The client hostname or IP address

        Returns:
            The levels response or None if unavailable
        """
        try:
            host = self._get_host(hostname)
            timeout = aiohttp.ClientTimeout(total=1.0)  # Short timeout for levels polling
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"http://{host}:{CLIENT_API_PORT}/dsp/levels") as resp:
                    if resp.status == 200:
                        return await resp.json()
        except Exception as e:
            self.logger.debug(f"Failed to get DSP levels from {hostname}: {e}")
        return None
