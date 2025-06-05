"""
Routes API pour la gestion du routage audio - Version avec gestion du plugin actif.
"""
from fastapi import APIRouter
from typing import Dict, Any
from backend.domain.audio_routing import AudioRoutingMode
from backend.domain.audio_state import AudioSource

def create_routing_router(routing_service, state_machine):
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
    
    return router