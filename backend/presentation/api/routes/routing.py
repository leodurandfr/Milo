# backend/presentation/api/routes/routing.py
"""
API routes for audio routing management
"""
from fastapi import APIRouter
from backend.domain.audio_state import AudioSource

def create_routing_router(routing_service, state_machine):
    """Creates routing router (multiroom + DSP)"""
    router = APIRouter(prefix="/api/routing", tags=["routing"])

    @router.get("/status")
    async def get_routing_status():
        """Gets current routing status (including DSP)"""
        routing_state = routing_service.get_state()
        snapcast_status = await routing_service.get_snapcast_status()

        return {
            "routing": routing_state,
            "snapcast": snapcast_status
        }

    @router.get("/services")
    async def get_services_status():
        """Gets status of all services"""
        services_status = await routing_service.get_available_services()
        return {
            "services": services_status
        }

    @router.post("/multiroom/{enabled}")
    async def set_multiroom_enabled(enabled: str):
        """Enables/disables multiroom mode"""
        try:
            multiroom_enabled = enabled.lower() in ("true", "1", "on", "enabled")

            current_state = await state_machine.get_current_state()
            active_source = None

            if current_state["active_source"] != "none":
                try:
                    active_source = AudioSource(current_state["active_source"])
                except ValueError:
                    pass

            success = await routing_service.set_multiroom_enabled(multiroom_enabled, active_source)
            if not success:
                return {"status": "error", "message": "Failed to change multiroom state"}

            await state_machine.update_multiroom_state(multiroom_enabled)

            return {
                "status": "success",
                "multiroom_enabled": multiroom_enabled,
                "active_source": current_state["active_source"] if active_source else "none"
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @router.post("/dsp/{enabled}")
    async def set_dsp_effects_enabled(enabled: str):
        """Enables/disables DSP effects (EQ, compressor, loudness). Volume always works via DSP."""
        try:
            dsp_effects_enabled = enabled.lower() in ("true", "1", "on", "enabled")

            current_state = await state_machine.get_current_state()
            active_source = None

            if current_state["active_source"] != "none":
                try:
                    active_source = AudioSource(current_state["active_source"])
                except ValueError:
                    pass

            success = await routing_service.set_dsp_effects_enabled(dsp_effects_enabled, active_source)
            if not success:
                return {"status": "error", "message": "Failed to change DSP effects state"}

            await state_machine.update_dsp_effects_state(dsp_effects_enabled)

            return {
                "status": "success",
                "dsp_effects_enabled": dsp_effects_enabled,
                "active_source": current_state["active_source"] if active_source else "none"
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @router.get("/multiroom/status")
    async def get_multiroom_status():
        """Gets current multiroom status"""
        routing_state = routing_service.get_state()
        return {
            "multiroom_enabled": routing_state.get('multiroom_enabled', False)
        }

    @router.get("/dsp/status")
    async def get_dsp_effects_status():
        """Gets current DSP effects status"""
        routing_state = routing_service.get_state()
        return {
            "dsp_effects_enabled": routing_state.get('dsp_effects_enabled', False)
        }

    return router