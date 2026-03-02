# backend/features/bluetooth/routes.py
"""
FastAPI routes for Bluetooth audio source.

Provides REST API endpoints for:
- Status: Get current Bluetooth source status
- Disconnect: Disconnect current device

Usage:
    from backend.features.bluetooth import router, BluetoothSource

    source = BluetoothSource(event_bus, config)
    setup_bluetooth_routes(lambda: source)
    app.include_router(router, prefix="/api")
"""
from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any

from backend.api.source_dependency import make_source_dependency
from backend.features.bluetooth.source import BluetoothSource

router = APIRouter(
    prefix="/bluetooth",
    tags=["bluetooth"],
    responses={404: {"description": "Not found"}},
)

set_source_provider, get_source = make_source_dependency("Bluetooth")


def setup_bluetooth_routes(source_provider) -> APIRouter:
    """Configure routes with source provider."""
    set_source_provider(source_provider)
    return router


@router.get("/status")
async def get_status(source: BluetoothSource = Depends(get_source)) -> Dict[str, Any]:
    """Get current Bluetooth source status."""
    try:
        status = await source.status()

        return {
            "status": "ok",
            "state": status.get("state", "unknown"),
            "service_active": status.get("service_active", False),
            "device_connected": status.get("device_connected", False),
            "device_name": status.get("device_name"),
            "device_address": status.get("device_address"),
            "bluetooth_running": status.get("bluetooth_running", False),
            "bluealsa_running": status.get("bluealsa_running", False),
            "aplay_running": status.get("aplay_running", False),
            "auto_agent": status.get("auto_agent", True)
        }

    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "state": "error"
        }


@router.post("/disconnect")
async def disconnect_device(source: BluetoothSource = Depends(get_source)) -> Dict[str, Any]:
    """Disconnect current Bluetooth device."""
    try:
        result = await source.command("disconnect", {})

        if not result.get("success"):
            raise HTTPException(
                status_code=400,
                detail=result.get("error", result.get("message", "Disconnect failed"))
            )

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Disconnect error: {str(e)}")
