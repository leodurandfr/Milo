# backend/api/route_helpers.py
"""
Shared helpers for API route error handling.

Provides:
- run_source_command(): Wraps source.command() with standard success check + error handling
- api_error_handler(): Async context manager for the common try/except HTTPException/Exception pattern
- parse_audio_source(): Parse user-provided source name to enum, or raise HTTP 400
- coerce_audio_source_or_none(): Coerce a trusted source name (e.g. from state) to enum, or None
"""
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import HTTPException

from backend.core.equalizer.client_proxy import SatelliteUnreachable
from backend.core.models.audio_state import AudioSource


async def run_source_command(source, cmd: str, data: dict, context: str = "Command"):
    """
    Call source.command() with standard success check and error handling.

    On success: returns the result dict.
    On command failure (result["success"] == False): raises HTTP 400.
    On unexpected exception: raises HTTP 500.

    Args:
        source: Audio source instance
        cmd: Command name to send
        data: Command data dict
        context: Human-readable context for error messages
    """
    try:
        result = await source.command(cmd, data)
        if not result.get("success"):
            raise HTTPException(
                status_code=400,
                detail=result.get("error", f"{context} failed")
            )
        return result
    except HTTPException:
        raise
    except SatelliteUnreachable as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{context}: {str(e)}")


@asynccontextmanager
async def api_error_handler(context: str, log=None):
    """
    Async context manager for the standard API route error pattern.

    Handles:
    - HTTPException: re-raised as-is (passthrough for 400/404/etc.)
    - SatelliteUnreachable: mapped to HTTPException with the carried status_code
      (the one place the core's satellite domain error becomes a web error)
    - Exception: optionally logged, then raised as HTTP 500

    Args:
        context: Human-readable context for the error message/log
        log: Optional logger instance. If provided, logs error before raising.

    Usage:
        async with api_error_handler("Error getting clients", logger):
            clients = registry_service.get_all_clients()
            return {"clients": clients}
    """
    try:
        yield
    except HTTPException:
        raise
    except SatelliteUnreachable as e:
        if log:
            log.error(f"{context}: {e.detail}")
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    except Exception as e:
        if log:
            log.error(f"{context}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def parse_audio_source(name: str) -> AudioSource:
    """Parse user-provided audio source name to enum, or raise HTTP 400.

    Use in route handlers that receive a source name from path/query/body.
    For defensive coercion of trusted state values, use coerce_audio_source_or_none.
    """
    try:
        return AudioSource(name)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown audio source: '{name}'",
        )


def coerce_audio_source_or_none(name: Optional[str]) -> Optional[AudioSource]:
    """Coerce a trusted source name (e.g. from state) to enum, or None.

    Returns None for the 'none' sentinel and for any invalid value (logs a
    warning on the latter — if state holds an invalid name, something is broken
    upstream and we want visibility without crashing the caller).
    """
    if not name or name == "none":
        return None
    try:
        return AudioSource(name)
    except ValueError:
        logging.getLogger(__name__).warning(
            "Unexpected source name in state: %r — coerced to None", name
        )
        return None
