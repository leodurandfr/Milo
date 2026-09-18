# backend/api/push.py
"""APNs token registration routes.

The iOS app hands Milō three tokens per install — the WidgetKit refresh token,
the push-to-start token, and one token per Now Playing session — and Milō
pushes to them from the LAN over an outbound HTTP/2 connection to Apple.

Registration is the app's job in both directions: iOS reissues tokens without
warning, and nothing on this side can ask for them. There is deliberately no
read route — nothing in the UI shows push state, and the registry is a file an
operator can read directly (/var/lib/milo/push_tokens.json).
"""
import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException

from backend.api.models import PushTokenRegisterRequest
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
