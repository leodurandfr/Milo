# backend/core/multiroom/routes.py
"""
API routes for Snapcast and multiroom functionality.
"""
import asyncio
import time
import logging
from fastapi import APIRouter, HTTPException

import aiohttp

from backend.api.models import (
    SnapcastClientNameRequest,
    SnapcastServerConfigRequest
)
from backend.config.constants import CLIENT_API_PORT
from backend.core.multiroom.routing import RoutingEnvironment

logger = logging.getLogger(__name__)


def create_snapcast_router(routing_service, snapcast_service, state_machine, dsp_service=None, proxy_service=None, settings_service=None):
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

    # === Remote client propagation ===

    async def _push_snapclient_config_to_remotes(buffer_time: int, fragments: int):
        """Push snapclient ALSA buffer config to all online remote clients."""
        registry = getattr(state_machine, 'client_registry', None)
        if not registry:
            return

        online_clients = registry.get_online_clients()
        remote_clients = [c for c in online_clients if c.ip != "127.0.0.1"]
        if not remote_clients:
            return

        payload = {"buffer_time": buffer_time, "fragments": fragments}
        timeout = aiohttp.ClientTimeout(total=10)

        async def _push_to_client(client):
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    url = f"http://{client.ip}:{CLIENT_API_PORT}/snapclient/config"
                    async with session.put(url, json=payload) as response:
                        if response.status == 200:
                            logger.info(f"Snapclient config pushed to {client.name} ({client.ip})")
                        else:
                            body = await response.text()
                            logger.warning(f"Failed to push config to {client.name}: {response.status} {body}")
            except Exception as e:
                logger.warning(f"Could not reach {client.name} ({client.ip}): {e}")

        await asyncio.gather(*[_push_to_client(c) for c in remote_clients], return_exceptions=True)

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

            # Add snapclient_buffer_time from settings
            if settings_service and config:
                snapclient_buffer_time = await settings_service.get_setting('multiroom.snapclient_buffer_time')
                if snapclient_buffer_time is None:
                    snapclient_buffer_time = 80  # Default value
                config["snapclient_buffer_time"] = snapclient_buffer_time

            return {"config": config}
        except Exception as e:
            logger.error(f"Error getting server config: {e}")
            return {"config": None, "error": str(e)}

    # === Server configuration routes ===

    @router.post("/server/config")
    async def update_server_config(payload: SnapcastServerConfigRequest):
        """Update server configuration."""
        try:
            config = payload.config.copy()

            # Extract snapclient config (not part of snapserver.conf)
            snapclient_buffer_time = config.pop("snapclient_buffer_time", None)
            snapclient_fragments = config.pop("snapclient_fragments", None)

            # Update snapserver.conf and restart snapserver
            success = await snapcast_service.update_server_config(config)

            if success:
                # Update snapclient buffer settings if provided
                if snapclient_buffer_time is not None:
                    # Update routing.env with new snapclient config
                    snapclient_config = {
                        "buffer_time": snapclient_buffer_time,
                        "fragments": snapclient_fragments if snapclient_fragments is not None else 4
                    }
                    RoutingEnvironment.update_snapclient_config(snapclient_config)

                    # Save to settings for persistence
                    if settings_service:
                        await settings_service.set_setting('multiroom.snapclient_buffer_time', snapclient_buffer_time)
                        if snapclient_fragments is not None:
                            await settings_service.set_setting('multiroom.snapclient_fragments', snapclient_fragments)

                    # Restart local snapclient to apply new buffer settings
                    if routing_service and routing_service.service_manager:
                        try:
                            await routing_service.service_manager.restart("milo-snapclient-multiroom.service")
                            logger.info(f"Local snapclient restarted with buffer_time={snapclient_buffer_time}ms")
                        except Exception as e:
                            logger.error(f"Failed to restart local snapclient: {e}")

                    # Propagate to remote clients (fire-and-forget)
                    asyncio.create_task(
                        _push_snapclient_config_to_remotes(snapclient_buffer_time, snapclient_config.get("fragments", 4))
                    )

                await _publish_snapcast_update()
                return {
                    "status": "success",
                    "message": "Configuration updated and services restarted"
                }
            else:
                return {"status": "error", "message": "Update failed"}

        except Exception as e:
            logger.error(f"Error updating server config: {e}")
            return {"status": "error", "message": str(e)}

    return router


def setup_multiroom_routes(app, routing_service, snapcast_service, state_machine, dsp_service=None, proxy_service=None, settings_service=None):
    """Set up all multiroom routes on the FastAPI app."""
    router = create_snapcast_router(
        routing_service=routing_service,
        snapcast_service=snapcast_service,
        state_machine=state_machine,
        dsp_service=dsp_service,
        proxy_service=proxy_service,
        settings_service=settings_service
    )
    app.include_router(router)
    return router


# Default router for direct import
router = APIRouter(prefix="/api/routing/snapcast", tags=["snapcast"])
