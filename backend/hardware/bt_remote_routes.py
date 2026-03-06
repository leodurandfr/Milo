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
