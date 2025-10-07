"""
Routes API principales pour la gestion audio - Version OPTIM avec validation et rate limiting
"""
from fastapi import APIRouter, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from backend.domain.audio_state import AudioSource
from backend.presentation.api.models import AudioControlRequest

# Limiter pour les endpoints critiques
limiter = Limiter(key_func=get_remote_address)

def create_router(state_machine):
    """Crée le router avec les dépendances injectées"""
    router = APIRouter(prefix="/api/audio", tags=["audio"])

    @router.get("/state")
    async def get_current_state():
        """Récupère l'état actuel du système audio"""
        return await state_machine.get_current_state()

    @router.post("/source/{source_name}")
    @limiter.limit("20/minute")  # Max 20 changements de source par minute
    async def change_audio_source(request: Request, source_name: str):
        """Change la source audio active avec rate limiting"""
        try:
            source = AudioSource(source_name)
            success = await state_machine.transition_to_source(source)
            return {"status": "success" if success else "error"}
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid source: {source_name}")

    @router.post("/control/{source_name}")
    @limiter.limit("60/minute")  # Max 60 commandes par minute
    async def control_source(http_request: Request, source_name: str, request: AudioControlRequest):
        """Envoie une commande à une source spécifique avec validation et rate limiting"""
        try:
            source = AudioSource(source_name)
            plugin = state_machine.plugins.get(source)

            if not plugin:
                raise HTTPException(status_code=404, detail=f"Plugin not found: {source_name}")

            result = await plugin.handle_command(request.command, request.data)
            return {"status": "success" if result.get("success") else "error", "result": result}

        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    return router