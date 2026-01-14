# backend/features/bluetooth/routes.py
"""
FastAPI routes for Bluetooth audio source.

Provides REST API endpoints for:
- Status: Get current Bluetooth source status
- Disconnect: Disconnect current device
- Restart Audio: Restart audio playback

Usage:
    from backend.features.bluetooth import router, BluetoothSource

    source = BluetoothSource(event_bus, config)
    setup_bluetooth_routes(lambda: source)
    app.include_router(router, prefix="/api")
"""
from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any, Callable, Optional

from backend.features.bluetooth.source import BluetoothSource

router = APIRouter(
    prefix="/bluetooth",
    tags=["bluetooth"],
    responses={404: {"description": "Not found"}},
)

# Source provider function
_source_provider: Optional[Callable[[], BluetoothSource]] = None


def setup_bluetooth_routes(source_provider: Callable[[], BluetoothSource]) -> APIRouter:
    """
    Configure routes with source provider.

    Args:
        source_provider: Function returning BluetoothSource instance

    Returns:
        Configured router
    """
    global _source_provider
    _source_provider = source_provider
    return router


def get_source() -> BluetoothSource:
    """Dependency to get BluetoothSource instance."""
    if _source_provider is None:
        raise HTTPException(
            status_code=500,
            detail="Bluetooth source not initialized. Call setup_bluetooth_routes first."
        )
    return _source_provider()


@router.get("/status")
async def get_status(source: BluetoothSource = Depends(get_source)) -> Dict[str, Any]:
    """
    Get current Bluetooth source status.

    Returns:
        Status dict with state, device info, and service status
    """
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
    """
    Disconnect current Bluetooth device.

    Returns:
        Result of disconnect operation
    """
    try:
        result = await source.command("disconnect", {})

        return {
            "status": "success" if result.get("success") else "error",
            "message": result.get("message", result.get("error", "Unknown error"))
        }

    except Exception as e:
        return {
            "status": "error",
            "message": f"Disconnect error: {str(e)}"
        }


@router.post("/restart-audio")
async def restart_audio(source: BluetoothSource = Depends(get_source)) -> Dict[str, Any]:
    """
    Restart audio playback for connected device.

    Returns:
        Result of restart operation
    """
    try:
        result = await source.command("restart_audio", {})

        return {
            "status": "success" if result.get("success") else "error",
            "message": result.get("message", "Audio playback restarted"),
            "error": result.get("error")
        }

    except Exception as e:
        return {
            "status": "error",
            "message": f"Restart error: {str(e)}"
        }


@router.post("/restart")
async def restart_service(source: BluetoothSource = Depends(get_source)) -> Dict[str, Any]:
    """
    Restart the Bluetooth source (full restart).

    Returns:
        Result of restart operation
    """
    try:
        result = await source.command("restart_audio", {})

        return {
            "status": "success" if result.get("success") else "error",
            "message": result.get("message", "Service restarted"),
            "error": result.get("error")
        }

    except Exception as e:
        return {
            "status": "error",
            "message": f"Restart error: {str(e)}"
        }


@router.post("/restart-bluealsa")
async def restart_bluealsa(source: BluetoothSource = Depends(get_source)) -> Dict[str, Any]:
    """
    Restart BlueALSA service specifically.

    Returns:
        Result of restart operation
    """
    try:
        result = await source.command("restart_bluealsa", {})

        return {
            "status": "success" if result.get("success") else "error",
            "message": result.get("message", "BlueALSA restarted"),
            "error": result.get("error")
        }

    except Exception as e:
        return {
            "status": "error",
            "message": f"Restart error: {str(e)}"
        }


@router.post("/toggle-agent")
async def toggle_agent(source: BluetoothSource = Depends(get_source)) -> Dict[str, Any]:
    """
    Toggle auto-pairing agent on/off.

    Returns:
        Result with new agent state
    """
    try:
        result = await source.command("toggle_agent", {})

        return {
            "status": "success" if result.get("success") else "error",
            "message": result.get("message", "Agent toggled"),
            "auto_agent": result.get("auto_agent"),
            "error": result.get("error")
        }

    except Exception as e:
        return {
            "status": "error",
            "message": f"Toggle error: {str(e)}"
        }


@router.get("/info")
async def get_info(source: BluetoothSource = Depends(get_source)) -> Dict[str, Any]:
    """
    Get Bluetooth source configuration information.

    Returns:
        Configuration and service details
    """
    try:
        status = await source.status()

        return {
            "status": "ok",
            "source_id": source.source_id,
            "service_name": source.service_name,
            "services": {
                "bluetooth": {
                    "name": source.bluetooth_service,
                    "active": status.get("bluetooth_running", False)
                },
                "bluealsa": {
                    "name": source.bluealsa_service,
                    "active": status.get("bluealsa_running", False)
                },
                "aplay": {
                    "name": source.bluealsa_aplay_service,
                    "active": status.get("aplay_running", False)
                }
            },
            "auto_agent": status.get("auto_agent", True)
        }

    except Exception as e:
        return {
            "status": "error",
            "message": f"Error: {str(e)}"
        }
