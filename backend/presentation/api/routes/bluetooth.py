"""
API routes for Bluetooth plugin
"""
from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any
from backend.domain.audio_state import PluginState

router = APIRouter(
    prefix="/api/bluetooth",
    tags=["bluetooth"],
    responses={404: {"description": "Not found"}},
)

bluetooth_plugin_dependency = None

def setup_bluetooth_routes(plugin_provider):
    """
    Configures bluetooth routes with plugin reference

    Args:
        plugin_provider: Function that returns bluetooth plugin instance
    """
    global bluetooth_plugin_dependency
    bluetooth_plugin_dependency = plugin_provider
    return router

def get_bluetooth_plugin():
    """Dependency to get bluetooth plugin"""
    if bluetooth_plugin_dependency is None:
        raise HTTPException(
            status_code=500,
            detail="Bluetooth plugin not initialized. Call setup_bluetooth_routes first."
        )
    return bluetooth_plugin_dependency()

@router.get("/status")
async def get_bluetooth_status(plugin = Depends(get_bluetooth_plugin)):
    """Gets current Bluetooth status"""
    try:
        status = await plugin.get_status()
        return {
            "status": "ok",
            "is_active": plugin.current_state == PluginState.CONNECTED,
            "plugin_state": plugin.current_state.value,
            "device_connected": status.get("device_connected", False),
            "device_name": status.get("device_name"),
            "device_address": status.get("device_address"),
            "playback_running": status.get("playback_running", False)
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "plugin_state": plugin.current_state.value if plugin else "unknown"
        }

@router.post("/disconnect")
async def disconnect_bluetooth_device(plugin = Depends(get_bluetooth_plugin)):
    """Disconnects current Bluetooth device"""
    try:
        result = await plugin.handle_command("disconnect", {})
        if result.get("success"):
            return {
                "status": "success",
                "message": result.get("message", "Device disconnected successfully")
            }
        else:
            return {
                "status": "error",
                "message": result.get("error", "Disconnection failed")
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Error: {str(e)}"
        }

@router.post("/restart-audio")
async def restart_bluetooth_audio(plugin = Depends(get_bluetooth_plugin)):
    """Restarts audio playback for connected Bluetooth device"""
    try:
        result = await plugin.handle_command("restart_audio", {})

        return {
            "status": "success" if result.get("success") else "error",
            "message": result.get("message", "Audio playback restarted"),
            "details": result
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Audio restart error: {str(e)}"
        }