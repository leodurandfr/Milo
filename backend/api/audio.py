"""
Main API routes for audio management
"""
import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException
from backend.api.models import AudioControlRequest
from backend.api.responses import AudioStateResponse, StatusResponse
from backend.api.route_helpers import parse_audio_source, run_source_command

if TYPE_CHECKING:
    from backend.core.state import AudioStateMachine


logger = logging.getLogger(__name__)

def create_router(state_machine: "AudioStateMachine"):
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
        """The single transport for every source command.

        Validation happens in `source.command()` against the source's own
        `COMMANDS` map, so an unknown command or bad params is a 400 here, not a
        200 carrying a failure flag — same contract as every other mutation.
        """
        source = parse_audio_source(source_name)
        source_instance = state_machine.sources.get(source)

        if not source_instance:
            logger.error("Command for an unregistered source: %s", source_name)
            raise HTTPException(status_code=404, detail=f"Source not found: {source_name}")

        result = await run_source_command(
            source_instance, control_request.command, control_request.data,
            f"{source_name}/{control_request.command}"
        )
        return {"status": "success", "result": result}

    return router
