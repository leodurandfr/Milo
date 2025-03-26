"""
Routes API spécifiques pour le plugin snapclient.
"""
from fastapi import APIRouter, HTTPException, Query, Depends, Body
from typing import Dict, Any, Optional, List

# Créer un router dédié pour snapclient
router = APIRouter(
    prefix="/snapclient",
    tags=["snapclient"],
    responses={404: {"description": "Not found"}},
)

# Référence au plugin snapclient (sera injectée via l'injection de dépendances)
snapclient_plugin_dependency = None

def setup_snapclient_routes(plugin_provider):
    """
    Configure les routes snapclient avec une référence au plugin.
    
    Args:
        plugin_provider: Fonction qui retourne une instance du plugin snapclient
    """
    global snapclient_plugin_dependency
    snapclient_plugin_dependency = plugin_provider
    return router

def get_snapclient_plugin():
    """Dépendance pour obtenir le plugin snapclient"""
    if snapclient_plugin_dependency is None:
        raise HTTPException(
            status_code=500, 
            detail="Snapclient plugin not initialized. Call setup_snapclient_routes first."
        )
    return snapclient_plugin_dependency()

@router.get("/status")
async def get_snapclient_status(plugin = Depends(get_snapclient_plugin)):
    """Récupère le statut actuel du client Snapcast"""
    try:
        # Récupérer les informations de statut
        status = await plugin.get_status()
        
        # Récupérer les informations de connexion
        connection_info = await plugin.get_connection_info()
        
        return {
            "status": "ok",
            "is_active": plugin.is_active,
            "device_connected": connection_info.get("device_connected", False),
            "host": connection_info.get("host"),
            "device_name": connection_info.get("device_name"),
            "device_info": status.get("metadata", {}),
            "discovered_servers": status.get("discovered_servers", []),
            "pending_requests": status.get("pending_requests", []),
            "blacklisted_servers": status.get("blacklisted_servers", [])
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Erreur lors de la récupération du statut snapclient: {str(e)}",
            "device_connected": False
        }

@router.post("/discover")
async def discover_snapcast_servers(plugin = Depends(get_snapclient_plugin)):
    """Force une découverte des serveurs Snapcast sur le réseau"""
    try:
        result = await plugin.handle_command("discover", {})
        
        return {
            "status": "success",
            "servers": result.get("servers", []),
            "count": result.get("count", 0),
            "action": result.get("action"),
            "message": result.get("message")
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Erreur lors de la découverte des serveurs: {str(e)}"
        }

@router.post("/connect/{host}")
async def connect_to_snapcast_server(
    host: str,
    plugin = Depends(get_snapclient_plugin)
):
    """Se connecte à un serveur Snapcast spécifique"""
    try:
        result = await plugin.handle_command("connect", {"host": host})
        
        if result.get("success", False):
            return {
                "status": "success",
                "message": f"Connecté au serveur {host}",
                "server": result.get("server")
            }
        else:
            return {
                "status": "error",
                "message": result.get("error", f"Impossible de se connecter au serveur {host}")
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Erreur lors de la connexion au serveur: {str(e)}"
        }

@router.post("/disconnect")
async def disconnect_from_snapcast_server(plugin = Depends(get_snapclient_plugin)):
    """Se déconnecte du serveur Snapcast actuel"""
    try:
        result = await plugin.handle_command("disconnect", {})
        
        if result.get("success", False):
            return {
                "status": "success",
                "message": "Déconnecté du serveur"
            }
        else:
            return {
                "status": "error",
                "message": result.get("error", "Impossible de se déconnecter du serveur")
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Erreur lors de la déconnexion du serveur: {str(e)}"
        }

@router.post("/accept-request")
async def accept_connection_request(
    data: Dict[str, Any] = Body(...),
    plugin = Depends(get_snapclient_plugin)
):
    """Accepte une demande de connexion entrante"""
    try:
        # Récupérer l'ID de demande ou l'hôte
        request_id = data.get("request_id")
        host = data.get("host")
        
        if not request_id and not host:
            return {
                "status": "error",
                "message": "Veuillez fournir un request_id ou un host"
            }
        
        command_data = {}
        if request_id:
            command_data["request_id"] = request_id
        if host:
            command_data["host"] = host
        
        result = await plugin.handle_command("accept_connection", command_data)
        
        if result.get("success", False):
            return {
                "status": "success",
                "message": result.get("message", "Demande de connexion acceptée"),
                "server": result.get("server")
            }
        else:
            return {
                "status": "error",
                "message": result.get("error", "Impossible d'accepter la demande de connexion")
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Erreur lors de l'acceptation de la demande: {str(e)}"
        }

@router.post("/reject-request")
async def reject_connection_request(
    data: Dict[str, Any] = Body(...),
    plugin = Depends(get_snapclient_plugin)
):
    """Rejette une demande de connexion entrante"""
    try:
        # Récupérer l'ID de demande ou l'hôte
        request_id = data.get("request_id")
        host = data.get("host")
        
        if not request_id and not host:
            return {
                "status": "error",
                "message": "Veuillez fournir un request_id ou un host"
            }
        
        command_data = {}
        if request_id:
            command_data["request_id"] = request_id
        if host:
            command_data["host"] = host
        
        result = await plugin.handle_command("reject_connection", command_data)
        
        if result.get("success", False):
            return {
                "status": "success",
                "message": result.get("message", "Demande de connexion rejetée")
            }
        else:
            return {
                "status": "error",
                "message": result.get("error", "Impossible de rejeter la demande de connexion")
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Erreur lors du rejet de la demande: {str(e)}"
        }

@router.post("/restart")
async def restart_snapclient(plugin = Depends(get_snapclient_plugin)):
    """Redémarre le processus snapclient"""
    try:
        result = await plugin.handle_command("restart", {})
        
        if result.get("success", False):
            return {
                "status": "success",
                "message": result.get("message", "Processus snapclient redémarré")
            }
        else:
            return {
                "status": "error",
                "message": result.get("error", "Impossible de redémarrer le processus snapclient")
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Erreur lors du redémarrage du processus: {str(e)}"
        }

@router.post("/test-audio")
async def test_audio(plugin = Depends(get_snapclient_plugin)):
    """Exécute un test audio pour vérifier que le son fonctionne"""
    try:
        result = await plugin.handle_command("test_audio", {})
        
        if result.get("success", False):
            return {
                "status": "success",
                "message": "Test audio lancé avec succès"
            }
        else:
            return {
                "status": "error",
                "message": result.get("error", "Impossible de lancer le test audio")
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Erreur lors du test audio: {str(e)}"
        }