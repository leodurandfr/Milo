# backend/api/route_helpers.py
"""
Shared helpers for API route error handling.

Provides:
- run_source_command(): Wraps source.command() with standard success check + error handling
- api_error_handler(): Async context manager for the common try/except HTTPException/Exception pattern
- parse_audio_source(): Parse user-provided source name to enum, or raise HTTP 400
- CONTRACTED: the `openapi_extra` of every operation a companion app's manifest pins
"""
import logging
from contextlib import asynccontextmanager

from fastapi import HTTPException

from backend.core.equalizer.client_proxy import CommandNotApplied, SatelliteUnreachable
from backend.core.models.audio_state import AudioSource

logger = logging.getLogger(__name__)

# Marks an operation Milo-Mac or Milo-iOS calls (tests/contracts/milo_*_contract.json)
# as `x-stability: contracted` in OpenAPI; the set is held equal to the two manifests
# by tests/contracts/test_api_stability.py.
CONTRACTED = {"x-stability": "contracted"}


def _reason(exc: Exception) -> str:
    """The exception's message, or its class when it has none.

    A bare ``TimeoutError()`` stringifies to nothing, so the log line read
    "Network status: " and the client received an empty ``detail``.
    """
    return str(exc) or type(exc).__name__


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
    except CommandNotApplied as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{context}: {_reason(e)}")


@asynccontextmanager
async def api_error_handler(context: str, log=None):
    """
    Async context manager for the standard API route error pattern.

    Handles:
    - HTTPException: re-raised as-is (passthrough for 400/404/etc.)
    - CommandNotApplied (SatelliteUnreachable among them): mapped to
      HTTPException with the carried status_code (the one place the core's
      speaker-command error becomes a web error)
    - Exception: optionally logged, then raised as HTTP 500

    What the carried code means, and the level it is logged at:
    - 503 from a satellite (``SatelliteUnreachable``): it could not be
      reached. Warning, not error: an absent satellite is an expected state
      of a multiroom install, and the frontend already renders it as
      "offline" from the registry. Logging it at error would push it through
      WebSocketLogHandler (level=ERROR) and raise a backend-error banner for
      a speaker that is merely unplugged.
    - 404: a client the registry no longer holds (deleted while the request
      was in flight). Warning too — the caller named a speaker that is gone.
    - 503 from this unit (no DSP or proxy service to carry the command): a
      wiring fault of this unit, kept at error.
    - Anything else: a speaker answered and refused, or the proxy hit
      something unexpected — a real failure, kept at error.

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
    except CommandNotApplied as e:
        if log:
            expected = e.status_code == 404 or (
                isinstance(e, SatelliteUnreachable) and e.status_code == 503
            )
            log_at = log.warning if expected else log.error
            log_at(f"{context}: {e.detail}")
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    except Exception as e:
        reason = _reason(e)
        if log:
            log.error(f"{context}: {reason}")
        raise HTTPException(status_code=500, detail=reason)


def raise_on_error_status(result: dict, context: str, log=None) -> dict:
    """A controller answer whose ``status`` is ``"error"``, raised as HTTP 500.

    The hardware controllers answer flow control (``timeout``, ``cancelled``,
    ``not_found``, ``already_connected``…) and failure in one dict, and only
    ``error`` is a failure. Passed through under a 200, it is a failure every
    client must know to look for — the settings panel does not, and kept its
    Search button spinning. Use inside ``api_error_handler``, which lets the
    HTTPException through. Returns ``result`` unchanged otherwise.
    """
    if result.get("status") == "error":
        detail = result.get("message") or context
        if log:
            log.error(f"{context}: {detail}")
        raise HTTPException(status_code=500, detail=detail)
    return result


def parse_audio_source(name: str) -> AudioSource:
    """Parse user-provided audio source name to enum, or raise HTTP 400.

    Use in route handlers that receive a source name from path/query/body.
    """
    try:
        return AudioSource(name)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown audio source: '{name}'",
        )

