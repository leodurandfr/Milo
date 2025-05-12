"""
Routes API spécifiques pour le plugin librespot.
À placer dans backend/presentation/api/routes/librespot.py
"""
from fastapi import APIRouter, HTTPException, Query, Depends
import subprocess
import asyncio
from typing import Dict, Any, Optional
from backend.domain.audio_state import PluginState  # Ajout de l'import manquant

# Créer un router dédié pour librespot
router = APIRouter(
    prefix="/librespot",
    tags=["librespot"],
    responses={404: {"description": "Not found"}},
)

# Référence au plugin librespot (sera injectée via l'injection de dépendances)
librespot_plugin_dependency = None

def setup_librespot_routes(plugin_provider):
    """
    Configure les routes librespot avec une référence au plugin.
    
    Args:
        plugin_provider: Fonction qui retourne une instance du plugin librespot
    """
    global librespot_plugin_dependency
    librespot_plugin_dependency = plugin_provider
    return router

def get_librespot_plugin():
    """Dépendance pour obtenir le plugin librespot"""
    if librespot_plugin_dependency is None:
        raise HTTPException(
            status_code=500, 
            detail="Librespot plugin not initialized. Call setup_librespot_routes first."
        )
    return librespot_plugin_dependency()

@router.get("/status")
async def get_librespot_status(plugin = Depends(get_librespot_plugin)):
    """Récupère le statut actuel de go-librespot avec toutes les métadonnées"""
    try:
        # Force toujours une mise à jour depuis l'API pour avoir l'état le plus récent
        if plugin.session:
            # Utiliser la méthode correcte pour rafraîchir les métadonnées
            await plugin._refresh_metadata()  # Remplacer _get_api_data() par _refresh_metadata()
        
        # Récupérer les informations de statut complètes
        status = await plugin.get_status()
        
        # S'assurer que toutes les données sont présentes
        return {
            "status": "ok",
            "is_active": plugin.current_state == PluginState.CONNECTED,
            "plugin_state": plugin.current_state.value,
            "device_connected": status.get("device_connected", False),
            "is_playing": status.get("is_playing", False),
            "metadata": status.get("metadata", {}), 
            "ws_connected": status.get("ws_connected", False)
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "plugin_state": plugin.current_state.value if plugin else "unknown",
            "metadata": {},
            "is_playing": False,
            "device_connected": False,
            "ws_connected": False
        }

@router.post("/connect")
async def restart_librespot_connection(plugin = Depends(get_librespot_plugin)):
    """Redémarre la connexion avec go-librespot"""
    try:
        # Forcer un redémarrage de la connexion WebSocket
        result = await plugin.handle_command("refresh_metadata", {})
        
        if result.get("success"):
            return {
                "status": "success",
                "message": "Connexion à go-librespot redémarrée avec succès",
                "details": result
            }
        else:
            return {
                "status": "warning",
                "message": f"Problème lors du rafraîchissement des métadonnées: {result.get('error')}",
                "details": result
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Erreur lors du redémarrage de la connexion: {str(e)}"
        }

@router.post("/restart")
async def restart_go_librespot(plugin = Depends(get_librespot_plugin)):
    """Redémarre complètement le processus go-librespot"""
    try:
        # Utiliser la commande de redémarrage du plugin
        result = await plugin.handle_command("restart", {})
        
        return {
            "status": "success" if result.get("success") else "error",
            "message": result.get("message", "Redémarrage terminé"),
            "details": result
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Erreur inattendue: {str(e)}"
        }


@router.post("/force-disconnect")
async def force_librespot_disconnect(plugin = Depends(get_librespot_plugin)):
    """Force l'envoi d'un événement de déconnexion pour librespot"""
    try:
        # Utiliser la commande force_disconnect du plugin
        result = await plugin.handle_command("force_disconnect", {})
        
        return {
            "status": "success" if result.get("success") else "error",
            "message": result.get("message", "Déconnexion forcée"),
            "details": result
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Erreur lors de l'envoi de l'événement de déconnexion: {str(e)}"
        }
        
        
@router.get("/logs")
async def get_librespot_logs(
    lines: int = Query(20, gt=0, le=200),
    plugin = Depends(get_librespot_plugin)
):
    """Récupère les dernières lignes des logs de go-librespot"""
    try:
        # Vérifier si le processus est en cours d'exécution
        process_info = await plugin.get_process_info()
        
        if not process_info.get("running"):
            return {
                "status": "error",
                "message": "Le processus go-librespot n'est pas en cours d'exécution"
            }
        
        # Utiliser le process_manager pour obtenir la sortie
        # Note: Ici, il faudrait ajouter une méthode pour obtenir la sortie dans le plugin
        return {
            "status": "warning",
            "message": "La récupération des logs n'est pas encore implémentée dans la nouvelle structure",
            "process_info": process_info
        }
    
    except Exception as e:
        return {
            "status": "error",
            "message": f"Erreur inattendue: {str(e)}"
        }