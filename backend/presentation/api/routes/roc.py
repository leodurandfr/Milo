"""
Routes API spécifiques pour le plugin ROC.
"""
from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any
from backend.domain.audio_state import PluginState

# Créer un router dédié pour ROC
router = APIRouter(
    prefix="/roc",
    tags=["roc"],
    responses={404: {"description": "Not found"}},
)

# Référence au plugin ROC (sera injectée via l'injection de dépendances)
roc_plugin_dependency = None

def setup_roc_routes(plugin_provider):
    """
    Configure les routes ROC avec une référence au plugin.
    
    Args:
        plugin_provider: Fonction qui retourne une instance du plugin ROC
    """
    global roc_plugin_dependency
    roc_plugin_dependency = plugin_provider
    return router

def get_roc_plugin():
    """Dépendance pour obtenir le plugin ROC"""
    if roc_plugin_dependency is None:
        raise HTTPException(
            status_code=500, 
            detail="ROC plugin not initialized. Call setup_roc_routes first."
        )
    return roc_plugin_dependency()

@router.get("/status")
async def get_roc_status(plugin = Depends(get_roc_plugin)):
    """Récupère le statut actuel du récepteur ROC"""
    try:
        status = await plugin.get_status()
        
        return {
            "status": "ok",
            "is_active": plugin.current_state != PluginState.INACTIVE,
            "plugin_state": plugin.current_state.value,
            "service_active": status.get("service_active", False),
            "service_running": status.get("service_running", False),
            "listening": status.get("listening", False),
            "rtp_port": status.get("rtp_port", 10001),
            "rs8m_port": status.get("rs8m_port", 10002),
            "rtcp_port": status.get("rtcp_port", 10003),
            "audio_output": status.get("audio_output", "hw:1,0")
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
    """Redémarre le service ROC"""
    try:
        result = await plugin.handle_command("restart", {})
        
        return {
            "status": "success" if result.get("success") else "error",
            "message": result.get("message", "Service redémarré"),
            "details": result
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Erreur lors du redémarrage: {str(e)}"
        }

@router.get("/logs")
async def get_roc_logs(plugin = Depends(get_roc_plugin)):
    """Récupère les logs du service ROC"""
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
                "message": result.get("error", "Impossible de récupérer les logs")
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Erreur lors de la récupération des logs: {str(e)}"
        }

@router.get("/info")
async def get_roc_info(plugin = Depends(get_roc_plugin)):
    """Récupère les informations de configuration ROC"""
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
            "message": f"Erreur lors de la récupération des informations: {str(e)}"
        }
        
        
@router.get("/connections")
async def get_roc_connections(plugin = Depends(get_roc_plugin)):
    """Récupère la liste des connexions actives"""
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
                "message": result.get("error", "Impossible de récupérer les connexions")
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Erreur lors de la récupération des connexions: {str(e)}"
        }