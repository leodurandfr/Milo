# backend/hardware/ir_remote_routes.py
"""
REST API routes for the IR remote controller (Apple Remote via TSOP4838).

All write operations broadcast `ir_remote_status_changed` via the state
machine when state mutates (the controller does this internally) — routes
do not broadcast directly.
"""
import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter

from backend.api.models import IrRemoteConfigRequest
from backend.api.route_helpers import api_error_handler, raise_on_error_status

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
        async with api_error_handler("Error updating IR remote config", logger):
            update = payload.model_dump(exclude_unset=True)
            await ir_remote_controller.update_config(update)
            return {"status": "success", **ir_remote_controller.get_status()}

    @router.post("/pair")
    async def start_pairing():
        """Capture one Apple scancode (or timeout after 15 s), save device_id
        and regenerate the rc-core keymap.

        On success the runtime listener is started; any other outcome leaves
        the controller as it was — the listener resumes if it was running, and
        a refused settings write puts the previous keymap back.

        Returns the pairing result; HTTP 200 in all flow-control cases
        (success/timeout/cancelled/unsupported). HTTP 500 on `error` and on
        internal failures (helper script failure, refused settings write…).
        """
        async with api_error_handler("Error starting IR pairing", logger):
            result = await ir_remote_controller.start_pairing()
            return raise_on_error_status(result, "IR pairing failed", logger)

    @router.post("/pair/cancel")
    async def cancel_pairing():
        """Abort an in-flight pairing capture."""
        async with api_error_handler("Error cancelling IR pairing", logger):
            cancelled = await ir_remote_controller.cancel_pairing()
            return {"status": "success", "cancelled": cancelled}

    @router.delete("/pair")
    async def unpair():
        """Forget the paired remote and clear the rc-core keymap.

        `enabled` is the header master switch and is deliberately left as it
        is: an unpaired-but-enabled remote lands back on the pairing wizard.
        """
        async with api_error_handler("Error unpairing IR remote", logger):
            await ir_remote_controller.unpair()
            return {"status": "success", **ir_remote_controller.get_status()}

    return router
