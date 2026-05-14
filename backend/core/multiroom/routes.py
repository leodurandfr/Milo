# backend/core/multiroom/routes.py
"""
API routes for Snapcast and multiroom functionality.
"""
import asyncio
import logging
from fastapi import APIRouter

import aiohttp

from backend.api.models import (
    SnapcastServerConfigRequest
)
from backend.config.constants import CLIENT_API_PORT
from backend.core.multiroom.routing import SnapclientEnv, DEFAULT_SNAPCLIENT_CONFIG

logger = logging.getLogger(__name__)


def create_snapcast_router(routing_service, snapcast_service, state_machine, camilladsp_service=None, proxy_service=None, settings_service=None, client_registry_service=None):
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
        """Push snapclient ALSA buffer config to all online remote clients.

        Must run BEFORE the snapserver restart: after the restart, all clients
        briefly disconnect and `registry.get_online_clients()` returns []. Pushing
        while remotes are still connected to the OLD snapserver lets them rewrite
        their env and restart themselves; they then reconnect to the NEW snapserver
        (once it's back) with the new buffer_time already in place.
        """
        if not client_registry_service:
            logger.debug("No client_registry_service available, skipping remote push")
            return

        online_clients = client_registry_service.get_online_clients()
        remote_clients = [c for c in online_clients if c.ip != "127.0.0.1"]
        if not remote_clients:
            logger.debug("No remote clients to push snapclient config to")
            return

        logger.info(
            f"Snapclient config push: buffer_time={buffer_time}ms, fragments={fragments}, "
            f"targeting {len(remote_clients)} remote(s)"
        )

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

    # === Server config routes ===

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

            # Snapclient buffer settings must be propagated to remotes BEFORE
            # the snapserver restart: the restart disconnects every client, and
            # the registry returns no online remotes for ~1 s. Pushing first
            # (while remotes are still connected to the OLD snapserver) lets
            # them rewrite their env and restart themselves; they then reconnect
            # to the NEW snapserver with the updated buffer_time already applied.
            if snapclient_buffer_time is not None:
                # 1. Persist to settings.json (source of truth)
                if settings_service:
                    await settings_service.set_setting('multiroom.snapclient_buffer_time', snapclient_buffer_time)
                    if snapclient_fragments is not None:
                        await settings_service.set_setting('multiroom.snapclient_fragments', snapclient_fragments)

                # Resolve effective fragments value: explicit if provided,
                # otherwise re-read from settings (async), otherwise default.
                effective_fragments = snapclient_fragments
                if effective_fragments is None and settings_service:
                    effective_fragments = await settings_service.get_setting('multiroom.snapclient_fragments')
                if effective_fragments is None:
                    effective_fragments = DEFAULT_SNAPCLIENT_CONFIG['fragments']

                # 2. Regenerate local snapclient.env with the resolved values
                SnapclientEnv.regenerate(snapclient_buffer_time, effective_fragments)

                # 3. Push to remotes and AWAIT — must complete before snapserver restart
                await _push_snapclient_config_to_remotes(snapclient_buffer_time, effective_fragments)

            # 4. Update snapserver.conf and restart snapserver
            success = await snapcast_service.update_server_config(config)

            if success:
                # 5. Restart local snapclient to pick up the new env (after
                # snapserver restart so it reconnects cleanly to the new server)
                if snapclient_buffer_time is not None:
                    if routing_service and routing_service.service_manager:
                        try:
                            await routing_service.service_manager.restart("milo-snapclient-multiroom.service")
                            logger.info(f"Local snapclient restarted with buffer_time={snapclient_buffer_time}ms")
                        except Exception as e:
                            logger.error(f"Failed to restart local snapclient: {e}")

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
