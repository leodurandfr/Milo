"""
Routes API spécifiques pour le plugin Bluetooth.
"""
from fastapi import APIRouter, HTTPException, Depends
from typing import Dict, Any
from backend.domain.audio_state import PluginState

# Créer un router dédié pour bluetooth avec le préfixe corrigé
router = APIRouter(
    prefix="/api/bluetooth",
    tags=["bluetooth"],
    responses={404: {"description": "Not found"}},
)

# Référence au plugin bluetooth (sera injectée via l'injection de dépendances)
bluetooth_plugin_dependency = None

def setup_bluetooth_routes(plugin_provider):
    """
    Configure les routes bluetooth avec une référence au plugin.
    
    Args:
        plugin_provider: Fonction qui retourne une instance du plugin bluetooth
    """
    global bluetooth_plugin_dependency
    bluetooth_plugin_dependency = plugin_provider
    return router

def get_bluetooth_plugin():
    """Dépendance pour obtenir le plugin bluetooth"""
    if bluetooth_plugin_dependency is None:
        raise HTTPException(
            status_code=500, 
            detail="Bluetooth plugin not initialized. Call setup_bluetooth_routes first."
        )
    return bluetooth_plugin_dependency()

# Endpoint pour récupérer le statut du plugin
@router.get("/status")
async def get_bluetooth_status(plugin = Depends(get_bluetooth_plugin)):
    """Récupère le statut actuel du Bluetooth"""
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

# Endpoint pour déconnecter un périphérique
@router.post("/disconnect")
async def disconnect_bluetooth_device(plugin = Depends(get_bluetooth_plugin)):
    """Déconnecte le périphérique Bluetooth actuel"""
    try:
        result = await plugin.handle_command("disconnect", {})
        if result.get("success"):
            return {
                "status": "success",
                "message": result.get("message", "Périphérique déconnecté avec succès")
            }
        else:
            return {
                "status": "error",
                "message": result.get("error", "Échec de la déconnexion")
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Erreur: {str(e)}"
        }

# Endpoint pour redémarrer la lecture audio
@router.post("/restart-audio")
async def restart_bluetooth_audio(plugin = Depends(get_bluetooth_plugin)):
    """Redémarre uniquement la lecture audio pour le périphérique Bluetooth connecté"""
    try:
        result = await plugin.handle_command("restart_audio", {})
        
        return {
            "status": "success" if result.get("success") else "error",
            "message": result.get("message", "Lecture audio redémarrée"),
            "details": result
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Erreur de redémarrage audio: {str(e)}"
        }