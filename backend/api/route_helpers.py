# backend/api/route_helpers.py
"""
Shared helpers for API route error handling.

Provides:
- run_source_command(): Wraps source.command() with standard success check + error handling
- api_error_handler(): Async context manager for the common try/except HTTPException/Exception pattern
"""
from contextlib import asynccontextmanager
from fastapi import HTTPException


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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{context}: {str(e)}")


@asynccontextmanager
async def api_error_handler(context: str, log=None):
    """
    Async context manager for the standard API route error pattern.

    Handles:
    - HTTPException: re-raised as-is (passthrough for 400/404/etc.)
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
    except Exception as e:
        if log:
            log.error(f"{context}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
