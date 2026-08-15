# backend/hardware/fan_routes.py
"""
REST API routes for the runtime PWM fan controller.

`PUT /config` persists the curve/mode to settings.json and applies it to the
hardware live (no reboot). The controller broadcasts `fan_status_changed`
telemetry over WS on its own; the PUT route additionally broadcasts
`fan_config_changed` (same payload shape) so other clients reflect the change.
"""
import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException

from backend.api.models import FanConfigRequest, FanTestRequest
from backend.api.route_helpers import api_error_handler

if TYPE_CHECKING:
    from backend.core.settings import SettingsService
    from backend.hardware.fan import FanController


logger = logging.getLogger(__name__)


def create_fan_router(fan_controller: "FanController", settings_service: "SettingsService"):
    """Create the fan control API router."""
    router = APIRouter(prefix="/api/fan", tags=["fan"])

    @router.get("/status")
    async def get_status():
        """Live telemetry + current config (temperature, RPM, PWM%, mode, curve)."""
        async with api_error_handler("Error reading fan status", logger):
            return {"status": "success", **await fan_controller.read_status()}

    @router.get("/config")
    async def get_config():
        """Persisted fan configuration (enabled, mode, manual_percent, target_temp_c, curve)."""
        async with api_error_handler("Error reading fan config", logger):
            cfg = await settings_service.get_setting("fan")
            return {"status": "success", "config": cfg}

    @router.put("/config")
    async def set_config(payload: FanConfigRequest):
        """Persist and apply a full fan configuration (idempotent, no reboot)."""
        async with api_error_handler("Error updating fan config", logger):
            cfg = payload.model_dump()
            saved = await settings_service.set_setting("fan", cfg)
            if not saved:
                logger.error("Failed to persist fan config")
                raise HTTPException(status_code=500, detail="Failed to persist fan config")
            await fan_controller.reload_config(cfg)
            return {"status": "success", **fan_controller.get_status()}

    @router.post("/test")
    async def test_speed(payload: FanTestRequest):
        """Momentarily drive the fan at a given speed (manual preview)."""
        async with api_error_handler("Error testing fan speed", logger):
            await fan_controller.test_speed(payload.percent)
            return {"status": "success", "percent": payload.percent}

    return router
