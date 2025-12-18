# backend/presentation/api/routes/mac.py
"""
API routes for Mac audio plugin (uses ROC toolkit internally)
"""
from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any
from backend.domain.audio_state import PluginState

router = APIRouter(
    prefix="/mac",
    tags=["mac"],
    responses={404: {"description": "Not found"}},
)

mac_plugin_dependency = None

def setup_mac_routes(plugin_provider):
    """
    Configures Mac audio routes with plugin reference

    Args:
        plugin_provider: Function that returns Mac audio plugin instance
    """
    global mac_plugin_dependency
    mac_plugin_dependency = plugin_provider
    return router

def get_mac_plugin():
    """Dependency to get Mac audio plugin"""
    if mac_plugin_dependency is None:
        raise HTTPException(
            status_code=500,
            detail="Mac audio plugin not initialized. Call setup_mac_routes first."
        )
    return mac_plugin_dependency()

@router.get("/status")
async def get_mac_status(plugin = Depends(get_mac_plugin)):
    """Gets current Mac audio receiver status"""
    try:
        status = await plugin.get_status()

        return {
            "status": "ok",
            "is_active": plugin.is_active_plugin(),
            "plugin_state": plugin.current_state.value,
            "service_active": status.get("service_active", False),
            "listening": status.get("listening", False),
            "rtp_port": status.get("rtp_port", 10001),
            "rs8m_port": status.get("rs8m_port", 10002),
            "rtcp_port": status.get("rtcp_port", 10003),
            "audio_output": status.get("audio_output", "hw:1,0"),
            "is_connected": status.get("connected", False),
            "client_name": status.get("client_name"),
            "connection_status": "connected" if status.get("connected", False) else "ready"
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "plugin_state": plugin.current_state.value if plugin else "unknown",
            "listening": False
        }

@router.post("/restart")
async def restart_mac_service(plugin = Depends(get_mac_plugin)):
    """Restarts Mac audio service"""
    try:
        result = await plugin.handle_command("restart", {})

        return {
            "status": "success" if result.get("success") else "error",
            "message": result.get("message", "Service restarted"),
            "details": result
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Restart error: {str(e)}"
        }

@router.get("/logs")
async def get_mac_logs(plugin = Depends(get_mac_plugin)):
    """Gets Mac audio service logs"""
    try:
        result = await plugin.handle_command("get_logs", {})

        if result.get("success"):
            return {
                "status": "success",
                "logs": result.get("logs", [])
            }
        else:
            return {
                "status": "error",
                "message": result.get("error", "Unable to retrieve logs")
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Log retrieval error: {str(e)}"
        }

@router.get("/info")
async def get_mac_info(plugin = Depends(get_mac_plugin)):
    """Gets Mac audio configuration information"""
    try:
        status = await plugin.get_status()

        return {
            "status": "ok",
            "configuration": {
                "rtp_port": status.get("rtp_port", 10001),
                "rs8m_port": status.get("rs8m_port", 10002),
                "rtcp_port": status.get("rtcp_port", 10003),
                "audio_output": status.get("audio_output", "hw:1,0")
            },
            "service_info": {
                "name": plugin.service_name,
                "active": status.get("service_active", False),
                "running": status.get("service_running", False)
            }
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Information retrieval error: {str(e)}"
        }


@router.get("/connections")
async def get_mac_connections(plugin = Depends(get_mac_plugin)):
    """Gets list of active connections"""
    try:
        result = await plugin.handle_command("get_connections", {})

        if result.get("success"):
            return {
                "status": "success",
                "connections": result.get("connections", {}),
                "connection_count": result.get("connection_count", 0)
            }
        else:
            return {
                "status": "error",
                "message": result.get("error", "Unable to retrieve connections")
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Connection retrieval error: {str(e)}"
        }
