"""Network error classification utilities."""
import asyncio
import aiohttp


class NetworkUnavailableError(Exception):
    """Raised when an external API call fails due to DNS/connectivity."""


def is_network_error(exc: Exception) -> bool:
    """Return True for transient DNS/connectivity failures."""
    return isinstance(exc, (
        asyncio.TimeoutError,
        aiohttp.ClientConnectorError,
        aiohttp.ServerConnectionError,
        aiohttp.ClientOSError,
    ))
