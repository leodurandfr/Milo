# backend/hardware/ir_remote_routes.py
"""
REST API routes for the IR remote controller (Apple Remote via TSOP4838).

All write operations broadcast `ir_remote_status_changed` via the state
machine when state mutates (the controller does this internally) — routes
do not broadcast directly.
"""
import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException

from backend.api.models import IrRemoteConfigRequest

if TYPE_CHECKING:
    from backend.hardware.ir_remote import IrRemoteController


logger = logging.getLogger(__name__)


def create_ir_remote_router(ir_remote_controller: "IrRemoteController"):
    """Create the IR remote API router."""
    router = APIRouter(prefix="/api/ir-remote", tags=["ir-remote"])

    @router.get("/status")
    async def get_status():
        """Return current IR controller status (enabled, paired, listening, etc.)."""
        return {"status": "success", **ir_remote_controller.get_status()}

    @router.patch("/config")
    async def update_config(payload: IrRemoteConfigRequest):
        """Partial config update — currently only the `enabled` flag."""
        try:
            update = payload.model_dump(exclude_unset=True)
            await ir_remote_controller.update_config(update)
            return {"status": "success", **ir_remote_controller.get_status()}
        except Exception as e:
            logger.error("Error updating IR remote config: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/pair")
    async def start_pairing():
        """Capture one Apple scancode (or timeout after 15 s), save device_id,
        regenerate the rc-core keymap, and resume the runtime listener.

        Returns the pairing result; HTTP 200 in all flow-control cases
        (success/timeout/cancelled/unsupported). HTTP 500 only on internal
        errors (helper script failure, kernel reload failure, etc.).
        """
        try:
            result = await ir_remote_controller.start_pairing()
            if result.get("status") == "error":
                # Surface as 500 — the wizard distinguishes flow-control vs failure
                logger.error("IR pairing internal error: %s", result.get("message"))
                raise HTTPException(status_code=500, detail=result.get("message"))
            return result
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Error starting IR pairing: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/pair/cancel")
    async def cancel_pairing():
        """Abort an in-flight pairing capture."""
        try:
            cancelled = await ir_remote_controller.cancel_pairing()
            return {"status": "success", "cancelled": cancelled}
        except Exception as e:
            logger.error("Error cancelling IR pairing: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete("/pair")
    async def unpair():
        """Forget the paired remote, clear the rc-core keymap, disable the controller."""
        try:
            await ir_remote_controller.unpair()
            return {"status": "success", **ir_remote_controller.get_status()}
        except Exception as e:
            logger.error("Error unpairing IR remote: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    return router
