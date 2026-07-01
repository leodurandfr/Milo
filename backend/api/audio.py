"""
Main API routes for audio management
"""
from fastapi import APIRouter, HTTPException
from backend.api.models import AudioControlRequest
from backend.api.responses import AudioStateResponse, StatusResponse
from backend.api.route_helpers import parse_audio_source

def create_router(state_machine):
    """Creates router with injected dependencies"""
    router = APIRouter(prefix="/api/audio", tags=["audio"])

    @router.get("/state", response_model=AudioStateResponse)
    async def get_current_state():
        """Gets current audio system state with refreshed metadata"""
        await state_machine.refresh_active_metadata()
        return state_machine.get_current_state()

    @router.post("/source/{source_name}", response_model=StatusResponse)
    async def change_audio_source(source_name: str):
        """Changes active audio source"""
        source = parse_audio_source(source_name)
        success = await state_machine.transition_to_source(source)
        return {"status": "success" if success else "error"}

    @router.post("/control/{source_name}")
    async def control_source(source_name: str, control_request: AudioControlRequest):
        """Sends command to specific source with validation"""
        source = parse_audio_source(source_name)
        source_instance = state_machine.sources.get(source)

        if not source_instance:
            raise HTTPException(status_code=404, detail=f"Source not found: {source_name}")

        result = await source_instance.command(control_request.command, control_request.data)
        return {"status": "success" if result.get("success") else "error", "result": result}

    return router
