"""
Routes API principales pour la gestion audio - Version OPTIM
"""
from fastapi import APIRouter, HTTPException
from typing import Dict, Any
from backend.domain.audio_state import AudioSource

def create_router(state_machine):
    """Crée le router avec les dépendances injectées"""
    router = APIRouter(prefix="/api/audio", tags=["audio"])
    
    @router.get("/state")
    async def get_current_state():
        """Récupère l'état actuel du système audio"""
        return await state_machine.get_current_state()
    
    @router.post("/source/{source_name}")
    async def change_audio_source(source_name: str):
        """Change la source audio active"""
        try:
            source = AudioSource(source_name)
            success = await state_machine.transition_to_source(source)
            return {"status": "success" if success else "error"}
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid source: {source_name}")
    
    @router.post("/control/{source_name}")
    async def control_source(source_name: str, payload: Dict[str, Any]):
        """Envoie une commande à une source spécifique"""
        try:
            source = AudioSource(source_name)
            plugin = state_machine.plugins.get(source)
            
            if not plugin:
                raise HTTPException(status_code=404, detail=f"Plugin not found: {source_name}")
            
            result = await plugin.handle_command(payload["command"], payload.get("data", {}))
            return {"status": "success" if result.get("success") else "error", "result": result}
            
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid source: {source_name}")
    
    return router