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


def describe_network_error(exc: Exception) -> str:
    """One of these rendered so the log line always carries a reason.

    ``asyncio.TimeoutError`` is the most common member of the set above and its
    ``str()`` is **empty**, so every call site interpolating the exception alone
    printed a sentence ending in a colon and nothing else. Measured on the unit:
    seven such lines during a NAS outage, none of them saying what happened —
    and the timeout was the whole story, since Navidrome had answered a second
    after the deadline. The class name is the reason when the message is not.
    """
    message = str(exc)
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__
