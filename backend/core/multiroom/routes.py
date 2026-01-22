# backend/core/multiroom/routes.py
"""
API routes for Snapcast and multiroom functionality.
"""
import time
import logging
from fastapi import APIRouter, HTTPException

from backend.api.models import (
    SnapcastClientNameRequest,
    SnapcastServerConfigRequest
)

logger = logging.getLogger(__name__)


def create_snapcast_router(routing_service, snapcast_service, state_machine, dsp_service=None, proxy_service=None):
    """Create Snapcast router with all endpoints."""
    router = APIRouter(prefix="/api/routing/snapcast", tags=["snapcast"])

    # === WebSocket utility functions ===

    async def _publish_snapcast_update():
        """Publish Snapcast update notification via WebSocket."""
        try:
            await state_machine.broadcast_event("system", "state_changed", {
                "snapcast_update": True,
                "source": "snapcast"
            })
        except Exception as e:
            logger.error("Error publishing Snapcast update: %s", e)

    # === Base routes ===

    @router.get("/status")
    async def get_snapcast_status():
        """Get Snapcast status."""
        try:
            available = await snapcast_service.is_available()
            clients = await snapcast_service.get_clients() if available else []
            routing_state = routing_service.get_state()

            return {
                "available": available,
                "client_count": len(clients),
                "multiroom_active": routing_state.get('multiroom_enabled', False)
            }
        except Exception as e:
            return {"available": False, "error": str(e)}

    @router.get("/clients")
    async def get_snapcast_clients():
        """Get Snapcast clients."""
        try:
            routing_state = routing_service.get_state()
            if not routing_state.get('multiroom_enabled', False):
                return {"clients": [], "message": "Multiroom not active"}

            clients = await snapcast_service.get_clients()
            return {"clients": clients}
        except Exception as e:
            return {"clients": [], "error": str(e)}

    @router.post("/client/{client_id}/name")
    async def set_client_name(client_id: str, payload: SnapcastClientNameRequest):
        """Set client name."""
        try:
            success = await snapcast_service.set_client_name(client_id, payload.name)

            if success:
                await _publish_snapcast_update()

            return {"status": "success" if success else "error"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # === Monitoring routes ===

    @router.get("/monitoring")
    async def get_snapcast_monitoring():
        """Get Snapcast monitoring information."""
        try:
            routing_state = routing_service.get_state()
            if not routing_state.get('multiroom_enabled', False):
                return {
                    "available": False,
                    "message": "Multiroom not active",
                    "clients": [],
                    "server_config": {}
                }

            available = await snapcast_service.is_available()
            if not available:
                return {
                    "available": False,
                    "message": "Snapcast server not available",
                    "clients": [],
                    "server_config": {}
                }

            clients = await snapcast_service.get_detailed_clients()
            server_config = await snapcast_service.get_server_config()

            return {
                "available": True,
                "clients": clients,
                "server_config": server_config,
                "timestamp": time.time()
            }
        except Exception as e:
            return {
                "available": False,
                "error": str(e),
                "clients": [],
                "server_config": {}
            }

    @router.get("/server-config")
    async def get_snapcast_server_config():
        """Get server configuration."""
        try:
            available = await snapcast_service.is_available()
            if not available:
                return {"config": None, "error": "Snapcast server not available"}

            config = await snapcast_service.get_server_config()
            return {"config": config}
        except Exception as e:
            logger.error(f"Error getting server config: {e}")
            return {"config": None, "error": str(e)}

    # === Server configuration routes ===

    @router.post("/server/config")
    async def update_server_config(payload: SnapcastServerConfigRequest):
        """Update server configuration."""
        try:
            success = await snapcast_service.update_server_config(payload.config)

            if success:
                await _publish_snapcast_update()
                return {
                    "status": "success",
                    "message": "Configuration updated and server restarted"
                }
            else:
                return {"status": "error", "message": "Update failed"}

        except Exception as e:
            return {"status": "error", "message": str(e)}

    return router


def setup_multiroom_routes(app, routing_service, snapcast_service, state_machine, dsp_service=None, proxy_service=None):
    """Set up all multiroom routes on the FastAPI app."""
    router = create_snapcast_router(
        routing_service=routing_service,
        snapcast_service=snapcast_service,
        state_machine=state_machine,
        dsp_service=dsp_service,
        proxy_service=proxy_service
    )
    app.include_router(router)
    return router


# Default router for direct import
router = APIRouter(prefix="/api/routing/snapcast", tags=["snapcast"])
