# backend/api/routing.py
"""
API routes for audio routing management
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.models.audio_state import AudioSource


class MultiroomRequest(BaseModel):
    """Request to enable/disable multiroom mode."""
    enabled: bool

def create_routing_router(routing_service, state_machine):
    """Creates routing router (multiroom + equalizer)"""
    router = APIRouter(prefix="/api/routing", tags=["routing"])

    @router.get("/status")
    async def get_routing_status():
        """Gets current routing status (including equalizer)"""
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

    @router.put("/multiroom")
    async def set_multiroom_enabled(request: MultiroomRequest):
        """Enables/disables multiroom mode"""
        try:
            multiroom_enabled = request.enabled

            current_state = await state_machine.get_current_state()
            active_source = None

            if current_state["active_source"] != "none":
                try:
                    active_source = AudioSource(current_state["active_source"])
                except ValueError:
                    pass

            success = await routing_service.set_multiroom_enabled(multiroom_enabled, active_source)
            if not success:
                raise HTTPException(status_code=500, detail="Failed to change multiroom state")

            return {
                "status": "success",
                "multiroom_enabled": multiroom_enabled,
                "active_source": current_state["active_source"] if active_source else "none"
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/multiroom/status")
    async def get_multiroom_status():
        """Gets current multiroom status"""
        routing_state = routing_service.get_state()
        return {
            "multiroom_enabled": routing_state.get('multiroom_enabled', False)
        }

    return router