"""
Main API routes for audio management
"""
import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException
from backend.api.models import AudioControlRequest
from backend.api.responses import AudioCommandsResponse, AudioStateResponse, StatusResponse
from backend.api.route_helpers import parse_audio_source, run_source_command
from backend.core.models.audio_state import AudioSource

if TYPE_CHECKING:
    from backend.core.state import AudioStateMachine


logger = logging.getLogger(__name__)

def _describe_params(model) -> dict:
    """Param name -> {required, type} for a command's Pydantic model.

    Read off `model_fields` rather than the JSON schema: the schema inlines
    validators, aliases and $defs, and a client only needs to know what to send
    and whether it may leave it out. `None` (a param-less command) is `{}`.
    """
    if model is None:
        return {}
    return {
        name: {
            "required": field.is_required(),
            "type": getattr(field.annotation, "__name__", str(field.annotation)),
        }
        for name, field in model.model_fields.items()
    }


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

    @router.get("/commands", response_model=AudioCommandsResponse)
    async def list_commands():
        """Which commands each source accepts, and the params each one takes.

        `POST /control/{source}` validates against the source's own `COMMANDS`
        map, but nothing described that map: OpenAPI shows only
        `{command: string, data: object}`, so every client discovered the table
        by sending names and reading the 400s back. That finds only what the
        author thought to try — Milo-iOS' survey missed `playpause`, `seek`,
        `set_speed`, `set_shuffle`, `eject` and `disconnect` across six sources,
        and could not tell a name absent from the table from one refused on
        state without matching "Unknown command" in a string.

        Derived from `COMMANDS` and its Pydantic models, never restated: a
        command added to a source appears here with no edit, which is the only
        version of this that cannot drift. A source with an empty table (the
        receivers — AirPlay, DLNA, Qobuz, Mac) is listed with `{}` rather than
        omitted, so "this source takes nothing" is an answer and not a gap.
        """
        commands = {}
        for source in AudioSource:
            instance = state_machine.sources.get(source)
            if instance is None:
                continue
            commands[source.value] = {
                name: _describe_params(model)
                for name, model in instance.COMMANDS.items()
            }
        return {"status": "success", "commands": commands}

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
