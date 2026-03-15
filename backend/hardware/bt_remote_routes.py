# backend/hardware/bt_remote_routes.py
"""
REST API routes for BT remote controller.

Provides endpoints for status and configuration.
Discovery and pairing are handled automatically in the background.
"""
import logging

from fastapi import APIRouter, HTTPException

from backend.api.models import BtRemoteConfigRequest

logger = logging.getLogger(__name__)


def create_bt_remote_router(bt_remote_controller):
    """Create the BT remote API router."""
    router = APIRouter(prefix="/api/bt-remote", tags=["bt-remote"])

    @router.get("/status")
    async def get_status():
        """Get BT remote controller status (includes config fields)."""
        return {
            "status": "success",
            **bt_remote_controller.get_status()
        }

    @router.get("/battery")
    async def get_battery():
        """Read battery level for connected BT remote devices (on-demand)."""
        seen_macs = set()
        devices = []
        for path in list(bt_remote_controller._monitored_paths):
            info = bt_remote_controller._device_info.get(path, {})
            address = info.get("address", "")
            if not address or address.upper() in seen_macs:
                continue
            seen_macs.add(address.upper())
            level = await bt_remote_controller.read_battery_level(address)
            devices.append({
                "address": address,
                "name": info.get("name", ""),
                "battery_percentage": level
            })
        return {"status": "success", "devices": devices}

    @router.post("/discover")
    async def trigger_discovery():
        """Trigger an immediate BT device discovery + pair attempt."""
        result = await bt_remote_controller.trigger_discovery()
        return result

    @router.patch("/config")
    async def update_config(payload: BtRemoteConfigRequest):
        """Update BT remote configuration (partial update)."""
        try:
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
        except Exception as e:
            logger.error("Error updating BT remote config: %s", e)
            raise HTTPException(status_code=500, detail=str(e))

    return router
