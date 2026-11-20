# backend/hardware/bt_remote_routes.py
"""
REST API routes for BT remote controller.

Provides endpoints for status, configuration, the Search button (discover)
and unpairing. Past the startup attempts, pairing a new remote is only ever
the Search button's.

A controller answer whose status is `error` is a failure and is raised as
one (`raise_on_error_status`); every other status is flow control, under 200.
"""
import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException

from backend.api.models import BtRemoteConfigRequest
from backend.api.route_helpers import api_error_handler, raise_on_error_status

if TYPE_CHECKING:
    from backend.hardware.bt_remote import BtRemoteController


logger = logging.getLogger(__name__)


def create_bt_remote_router(bt_remote_controller: "BtRemoteController"):
    """Create the BT remote API router."""
    router = APIRouter(prefix="/api/bt-remote", tags=["bt-remote"])

    @router.get("/status")
    async def get_status():
        """Get BT remote controller status (includes config + pairing state)."""
        async with api_error_handler("Error reading BT remote status", logger):
            return {
                "status": "success",
                **bt_remote_controller.get_status(),
                "paired": await bt_remote_controller.is_paired(),
            }

    @router.get("/battery")
    async def get_battery():
        """Read battery level for connected BT remote devices (on-demand)."""
        async with api_error_handler("Error reading BT remote battery", logger):
            devices = []
            for d in bt_remote_controller.get_device_info():
                level = await bt_remote_controller.read_battery_level(d["address"])
                devices.append({
                    "address": d["address"],
                    "name": d["name"],
                    "battery_percentage": level,
                })
            return {"status": "success", "devices": devices}

    @router.post("/discover")
    async def trigger_discovery():
        """Trigger an immediate BT device reconnect, else discovery + pair attempt.

        `success`, `already_connected`, `not_found` and `cancelled` answer 200;
        a refusal is not, which is what lets the settings panel drop its
        `discovering` spinner — a refusal broadcasts no status that would. A
        remote disabled (from another screen, since this one hides the button)
        is a 409 at warning: the request met a state it did not expect, nothing
        failed. Every other `error` (evdev missing) is a 500.
        """
        async with api_error_handler("Error discovering BT remote", logger):
            if not bt_remote_controller.enabled:
                logger.warning("BT discovery refused: the remote is disabled")
                raise HTTPException(status_code=409, detail="BT remote is disabled")
            result = await bt_remote_controller.trigger_discovery()
            return raise_on_error_status(result, "BT discovery failed", logger)

    @router.delete("/pairing")
    async def unpair():
        """Forget the paired BT remote (disconnect + remove the BlueZ bond)."""
        async with api_error_handler("Error unpairing BT remote", logger):
            result = await bt_remote_controller.forget_remote()
            return raise_on_error_status(result, "Unpair failed", logger)

    @router.patch("/config")
    async def update_config(payload: BtRemoteConfigRequest):
        """Update BT remote configuration (partial update)."""
        async with api_error_handler("Error updating BT remote config", logger):
            update = payload.model_dump(exclude_unset=True)
            await bt_remote_controller.update_config(update)
            return {
                "status": "success",
                "config": {
                    "enabled": bt_remote_controller.enabled,
                    "device_name_filter": bt_remote_controller.device_name_filter,
                    "key_map": bt_remote_controller.key_map
                }
            }

    return router
