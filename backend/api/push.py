# backend/api/push.py
"""APNs token registration routes.

The iOS app hands Milō three tokens per install — the WidgetKit refresh token,
the push-to-start token, and one token per Now Playing session — and Milō
pushes to them from the LAN over an outbound HTTP/2 connection to Apple.

Registration is the app's job in both directions: iOS reissues tokens without
warning, and nothing on this side can ask for them. There is deliberately no
read route — nothing in the UI shows push state, and the registry is a file an
operator can read directly (/var/lib/milo/push_tokens.json).

`POST /sessions` is the other half of that: the app says which sessions still
exist, because a session token outlives its session and this side cannot see
the difference.
"""
import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException

from backend.api.models import PushLiveSessionsRequest, PushTokenRegisterRequest
from backend.api.responses import StatusResponse
from backend.api.route_helpers import api_error_handler

if TYPE_CHECKING:
    from backend.core.push.token_registry import PushTokenRegistry

logger = logging.getLogger(__name__)


def create_push_router(push_token_registry: "PushTokenRegistry"):
    """Creates the push token router with dependency injection"""
    router = APIRouter(prefix="/api/push", tags=["push"])

    @router.post("/tokens", response_model=StatusResponse)
    async def register_token(request: PushTokenRegisterRequest):
        """Register an APNs device token.

        Idempotent by construction: a token that supersedes one already held —
        the same kind on the same device, or the same session — replaces it,
        so an app that re-registers on every launch does not grow the registry.
        """
        async with api_error_handler("Failed to register push token", logger):
            await push_token_registry.register(
                token=request.token,
                kind=request.kind,
                environment=request.environment,
                device_id=request.device_id,
                session_id=request.session_id,
                boot_time=request.boot_time,
            )
            return {"status": "success"}

    @router.post("/sessions", response_model=StatusResponse)
    async def report_live_sessions(request: PushLiveSessionsRequest):
        """Tell Milō which Now Playing sessions this device still holds.

        Retires the session tokens it does not name. Without this, a session
        that died without the phone restarting leaves a token behind that Milō
        keeps addressing forever: APNs answers 200, the phone discards the
        payload, and no `start` is ever sent because a token exists. Measured
        2026-09-19 — `Could not find the specified now playing client` on the
        phone, `session 2eb3b71b adopted (was None)` on this side, at the same
        minute.

        An empty list is a meaningful report, not a missing one: it is how a
        phone says it holds nothing, which is exactly the case that needs
        clearing. What says nothing is an app that is not running — and an app
        that is not running does not call this.
        """
        async with api_error_handler("Failed to report live sessions", logger):
            await push_token_registry.drop_sessions_absent_from(
                request.device_id, request.session_ids
            )
            return {"status": "success"}

    @router.delete("/tokens/{token}", response_model=StatusResponse)
    async def unregister_token(token: str):
        """Drop a token the app no longer wants pushed to.

        404 on a token that is not held. This is the app withdrawing its own
        token, so not finding it is a disagreement worth reporting — unlike the
        purge path, where APNs naming a token Milō never had is routine.
        """
        async with api_error_handler("Failed to unregister push token", logger):
            if not await push_token_registry.unregister(token):
                logger.error(f"Unregister requested for a token not held: {token[:8]}…")
                raise HTTPException(status_code=404, detail="Token not registered")
            return {"status": "success"}

    return router
