"""
Routes API spécifiques pour le plugin snapclient.
"""
from fastapi import APIRouter, HTTPException, Depends, Body
from typing import Dict, Any

# Créer un router dédié pour snapclient
router = APIRouter(
    prefix="/snapclient",
    tags=["snapclient"],
    responses={404: {"description": "Not found"}},
)

# Référence au plugin snapclient (sera injectée via l'injection de dépendances)
snapclient_plugin_dependency = None

def setup_snapclient_routes(plugin_provider):
    """Configure les routes snapclient avec une référence au plugin."""
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

# Helper pour formater les réponses d'erreur de manière cohérente
def error_response(message: str) -> Dict[str, Any]:
    return {"status": "error", "message": message}

@router.get("/status")
async def get_snapclient_status(plugin = Depends(get_snapclient_plugin)):
    """Récupère le statut actuel du client Snapcast"""
    try:
        status = await plugin.get_status()
        connection_info = await plugin.get_connection_info()
        
        return {
            "status": "ok",
            "is_active": plugin.is_active,
            "device_connected": connection_info.get("device_connected", False),
            "host": connection_info.get("host"),
            "device_name": connection_info.get("device_name"),
            "device_info": status.get("metadata", {}),
            "discovered_servers": status.get("discovered_servers", []),
            "blacklisted_servers": status.get("blacklisted_servers", [])
            # Supprimer "pending_requests" qui n'est pas utilisé
        }
    except Exception as e:
        return error_response(f"Erreur lors de la récupération du statut snapclient: {str(e)}")

@router.post("/discover")
async def discover_snapcast_servers(plugin = Depends(get_snapclient_plugin)):
    """Force une découverte des serveurs Snapcast sur le réseau"""
    try:
        result = await plugin.handle_command("discover", {})
        
        return {
            "status": "success",
            "servers": result.get("servers", []),
            "count": result.get("count", 0),
            "message": result.get("message")
            # Supprimer "action" qui n'est pas utilisé
        }
    except Exception as e:
        return error_response(f"Erreur lors de la découverte des serveurs: {str(e)}")

@router.post("/connect/{host}")
async def connect_to_snapcast_server(host: str, plugin = Depends(get_snapclient_plugin)):
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
            return error_response(result.get("error", f"Impossible de se connecter au serveur {host}"))
    except Exception as e:
        return error_response(f"Erreur lors de la connexion au serveur: {str(e)}")

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
            return error_response(result.get("error", "Impossible de se déconnecter du serveur"))
    except Exception as e:
        return error_response(f"Erreur lors de la déconnexion du serveur: {str(e)}")

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
            return error_response(result.get("error", "Impossible de redémarrer le processus snapclient"))
    except Exception as e:
        return error_response(f"Erreur lors du redémarrage du processus: {str(e)}")

