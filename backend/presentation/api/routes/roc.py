"""
API routes for ROC plugin
"""
from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any
from backend.domain.audio_state import PluginState

router = APIRouter(
    prefix="/api/roc",
    tags=["roc"],
    responses={404: {"description": "Not found"}},
)

roc_plugin_dependency = None

def setup_roc_routes(plugin_provider):
    """
    Configures ROC routes with plugin reference

    Args:
        plugin_provider: Function that returns ROC plugin instance
    """
    global roc_plugin_dependency
    roc_plugin_dependency = plugin_provider
    return router

def get_roc_plugin():
    """Dependency to get ROC plugin"""
    if roc_plugin_dependency is None:
        raise HTTPException(
            status_code=500,
            detail="ROC plugin not initialized. Call setup_roc_routes first."
        )
    return roc_plugin_dependency()

@router.get("/status")
async def get_roc_status(plugin = Depends(get_roc_plugin)):
    """Gets current ROC receiver status"""
    try:
        status = await plugin.get_status()
        
        return {
            "status": "ok",
            "is_active": plugin.current_state != PluginState.INACTIVE,
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
async def restart_roc_service(plugin = Depends(get_roc_plugin)):
    """Restarts ROC service"""
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
async def get_roc_logs(plugin = Depends(get_roc_plugin)):
    """Gets ROC service logs"""
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
async def get_roc_info(plugin = Depends(get_roc_plugin)):
    """Gets ROC configuration information"""
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
async def get_roc_connections(plugin = Depends(get_roc_plugin)):
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