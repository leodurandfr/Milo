"""
Routes API principales pour la gestion audio - Version unifiée
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, Any
from backend.domain.audio_state import AudioSource

class VolumeRequest(BaseModel):
    volume: int

def create_router(state_machine, event_bus):
    """Crée le router avec les dépendances injectées"""
    router = APIRouter(prefix="/api/audio", tags=["audio"])
    
    @router.get("/state")
    async def get_current_state():
        """Récupère l'état actuel du système audio"""
        try:
            state = await state_machine.get_current_state()
            return state
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/source/{source_name}")
    async def change_audio_source(source_name: str):
        """Change la source audio active"""
        try:
            # Convertir le nom en AudioSource
            source = AudioSource(source_name)
            
            # Effectuer la transition
            success = await state_machine.transition_to_source(source)
            
            if success:
                return {"status": "success", "message": f"Transition vers {source_name} réussie"}
            else:
                return {"status": "error", "message": f"Échec de la transition vers {source_name}"}
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Source invalide: {source_name}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/control/{source_name}")
    async def control_source(source_name: str, command: Dict[str, Any]):
        """Envoie une commande à une source spécifique"""
        try:
            source = AudioSource(source_name)
            plugin = state_machine.plugins.get(source)
            
            if not plugin:
                raise HTTPException(status_code=404, detail=f"Plugin non trouvé pour {source_name}")
            
            result = await plugin.handle_command(command["command"], command.get("data", {}))
            return {"status": "success" if result.get("success") else "error", "result": result}
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Source invalide: {source_name}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    # TODO: Ajouter la gestion du volume quand le service sera implémenté
    
    return router