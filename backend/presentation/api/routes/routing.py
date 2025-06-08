"""
Routes API pour la gestion du routage audio - Version avec gestion du plugin actif et Snapcast synchronisé.
"""
from fastapi import APIRouter
from typing import Dict, Any
from backend.domain.audio_routing import AudioRoutingMode
from backend.domain.audio_state import AudioSource
from backend.domain.events import StandardEvent, EventCategory, EventType

def create_routing_router(routing_service, state_machine, snapcast_service):
    """Crée le router de routage avec les dépendances injectées"""
    router = APIRouter(prefix="/api/routing", tags=["routing"])
    
    @router.get("/status")
    async def get_routing_status():
        """Récupère l'état actuel du routage"""
        routing_state = routing_service.get_state()
        snapcast_status = await routing_service.get_snapcast_status()
        
        return {
            "routing": routing_state.to_dict(),
            "snapcast": snapcast_status
        }
    
    @router.get("/services")
    async def get_services_status():
        """Récupère l'état de tous les services"""
        services_status = await routing_service.get_available_services()
        return {
            "services": services_status
        }
    
    @router.post("/mode/{mode}")
    async def set_routing_mode(mode: str):
        """Change le mode de routage"""
        try:
            routing_mode = AudioRoutingMode(mode)
            
            # Récupérer le plugin actuellement actif
            current_state = await state_machine.get_current_state()
            active_source = None
            
            if current_state["active_source"] != "none":
                try:
                    active_source = AudioSource(current_state["active_source"])
                except ValueError:
                    # Source inconnue, on continue sans plugin actif
                    pass
            
            # Changer le mode de routage avec le plugin actif
            success = await routing_service.set_routing_mode(routing_mode, active_source)
            if not success:
                return {"status": "error", "message": "Failed to change routing mode"}
            
            # Mettre à jour l'état de la machine à états
            await state_machine.update_routing_mode(mode)
            
            return {
                "status": "success", 
                "mode": mode,
                "active_source": current_state["active_source"] if active_source else "none"
            }
        except ValueError:
            return {"status": "error", "message": f"Invalid mode: {mode}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    # === ROUTES SNAPCAST AVEC NOTIFICATIONS WEBSOCKET ===
    
    async def _publish_snapcast_update():
        """Publie une notification de mise à jour Snapcast via le WebSocket oakOS"""
        try:
            # Utiliser le bus d'événements existant pour notifier les clients
            event_bus = state_machine.event_bus
            
            event = StandardEvent(
                category=EventCategory.SYSTEM,
                type=EventType.STATE_CHANGED,
                source="snapcast",
                data={
                    "snapcast_update": True,
                    "timestamp": __import__('time').time()
                }
            )
            
            await event_bus.publish_event(event)
        except Exception as e:
            print(f"Error publishing Snapcast update: {e}")
    
    @router.get("/snapcast/status")
    async def get_snapcast_status():
        """État de Snapcast"""
        try:
            available = await snapcast_service.is_available()
            clients = await snapcast_service.get_clients() if available else []
            routing_state = routing_service.get_state()
            
            return {
                "available": available,
                "client_count": len(clients),
                "multiroom_active": routing_state.mode.value == "multiroom"
            }
        except Exception as e:
            return {"available": False, "error": str(e)}
    
    @router.get("/snapcast/clients")
    async def get_snapcast_clients():
        """Récupère les clients Snapcast"""
        try:
            routing_state = routing_service.get_state()
            if routing_state.mode.value != "multiroom":
                return {"clients": [], "message": "Multiroom not active"}
            
            clients = await snapcast_service.get_clients()
            return {"clients": clients}
        except Exception as e:
            return {"clients": [], "error": str(e)}
    
    @router.post("/snapcast/client/{client_id}/volume")
    async def set_snapcast_volume(client_id: str, payload: Dict[str, Any]):
        """Change le volume d'un client et notifie les autres devices"""
        try:
            volume = payload.get("volume")
            if not isinstance(volume, int) or not (0 <= volume <= 100):
                return {"status": "error", "message": "Invalid volume (0-100)"}
            
            success = await snapcast_service.set_volume(client_id, volume)
            
            if success:
                # Notifier tous les devices connectés via WebSocket
                await _publish_snapcast_update()
            
            return {"status": "success" if success else "error"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    @router.post("/snapcast/client/{client_id}/mute")
    async def set_snapcast_mute(client_id: str, payload: Dict[str, Any]):
        """Mute/unmute un client et notifie les autres devices"""
        try:
            muted = payload.get("muted")
            if not isinstance(muted, bool):
                return {"status": "error", "message": "Invalid muted state (true/false)"}
            
            success = await snapcast_service.set_mute(client_id, muted)
            
            if success:
                # Notifier tous les devices connectés via WebSocket
                await _publish_snapcast_update()
            
            return {"status": "success" if success else "error"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    return router