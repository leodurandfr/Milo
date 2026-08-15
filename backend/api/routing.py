# backend/api/routing.py
"""
API routes for audio routing management — the whole /api/routing namespace.

Multiroom on/off and the snapserver server-config surface live together
because they are one namespace. They used to be two routers in two layers,
one serving a sub-prefix of the other's, and a quarter of the commits
touching either touched both.
"""
import asyncio
import logging
from typing import TYPE_CHECKING, Optional

import aiohttp
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.api.models import SnapcastServerConfigRequest
from backend.api.responses import MultiroomSetResponse
from backend.api.route_helpers import api_error_handler, coerce_audio_source_or_none
from backend.config.constants import CLIENT_API_PORT
from backend.core.models.ws_events import SystemStateChanged
from backend.core.multiroom.routing import (
    SNAPCLIENT_LIMITS,
    SnapclientEnv,
    resolve_snapclient_config,
)
from backend.core.multiroom.snapcast import NETWORK_PRESETS, SUPPORTED_CODECS

if TYPE_CHECKING:
    from backend.core.multiroom.client_registry import ClientRegistryService
    from backend.core.multiroom.routing import AudioRoutingService
    from backend.core.multiroom.snapcast import SnapcastService
    from backend.core.settings import SettingsService
    from backend.core.state import AudioStateMachine


logger = logging.getLogger(__name__)


class MultiroomRequest(BaseModel):
    """Request to enable/disable multiroom mode."""
    enabled: bool


def _validate_snapclient_value(key: str, value) -> None:
    """Reject a snapclient value outside SNAPCLIENT_LIMITS, or pass silently.

    This is the only gate the pair passes through. Both keys leave `config`
    before it reaches SnapcastService, so the validators there never saw them:
    an out-of-range buffer_time used to be persisted, written to snapclient.env
    and pushed to every satellite, each end clamping it its own way.
    """
    if value is None:
        return
    low, high = SNAPCLIENT_LIMITS[key]
    if not isinstance(value, int) or not low <= value <= high:
        logger.error(f"Invalid snapclient_{key}: {value}")
        raise HTTPException(
            status_code=400,
            detail=f"snapclient_{key} must be an integer between {low} and {high}"
        )


def create_routing_router(
    routing_service: "AudioRoutingService",
    state_machine: "AudioStateMachine",
    snapcast_service: "SnapcastService",
    settings_service: Optional["SettingsService"] = None,
    client_registry_service: Optional["ClientRegistryService"] = None,
):
    """Creates the /api/routing router (multiroom mode + snapcast server config)."""
    router = APIRouter(prefix="/api/routing", tags=["routing"])

    # === Multiroom mode ===

    @router.put("/multiroom", response_model=MultiroomSetResponse)
    async def set_multiroom_enabled(request: MultiroomRequest):
        """Enables/disables multiroom mode"""
        async with api_error_handler("Error changing multiroom state", logger):
            multiroom_enabled = request.enabled

            current_state = state_machine.get_current_state()
            active_source = coerce_audio_source_or_none(current_state["active_source"])

            success = await routing_service.set_multiroom_enabled(multiroom_enabled, active_source)
            if not success:
                logger.error("Failed to change multiroom state to %s", multiroom_enabled)
                raise HTTPException(status_code=500, detail="Failed to change multiroom state")

            return {
                "status": "success",
                "multiroom_enabled": multiroom_enabled,
                "active_source": current_state["active_source"] if active_source else "none"
            }

    # === WebSocket utility functions ===

    async def _publish_snapcast_update():
        """Publish Snapcast update notification via WebSocket."""
        try:
            await state_machine.broadcast(SystemStateChanged(source="snapcast"))
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

    # === Snapcast server config ===

    @router.get("/snapcast/server-config")
    async def get_snapcast_server_config():
        """Get server configuration + static capabilities (codec list, presets).

        Capabilities are the single source the frontend builds its codec
        options and quality presets from — they are returned even when the
        snapserver itself is unavailable.
        """
        capabilities = {"codecs": SUPPORTED_CODECS, "presets": NETWORK_PRESETS}
        try:
            available = await snapcast_service.is_available()
            if not available:
                return {"config": None, "capabilities": capabilities,
                        "error": "Snapcast server not available"}

            config = await snapcast_service.get_server_config()

            # Add snapclient_buffer_time from settings — reported as it will be
            # applied (defaulted and clamped), not as it happens to be stored.
            if config:
                snapclient_buffer_time, _ = await resolve_snapclient_config(settings_service)
                config["snapclient_buffer_time"] = snapclient_buffer_time

            return {"config": config, "capabilities": capabilities}
        except Exception as e:
            logger.error(f"Error getting server config: {e}")
            return {"config": None, "capabilities": capabilities, "error": str(e)}

    @router.put("/snapcast/server-config")
    async def update_server_config(payload: SnapcastServerConfigRequest):
        """Replace the server configuration (idempotent full write)."""
        async with api_error_handler("Error updating server config", logger):
            config = payload.config.copy()

            # Extract snapclient config (not part of snapserver.conf)
            snapclient_buffer_time = config.pop("snapclient_buffer_time", None)
            snapclient_fragments = config.pop("snapclient_fragments", None)
            _validate_snapclient_value("buffer_time", snapclient_buffer_time)
            _validate_snapclient_value("fragments", snapclient_fragments)

            # Snapclient buffer settings must be propagated to remotes BEFORE
            # the snapserver restart: the restart disconnects every client, and
            # the registry returns no online remotes for ~1 s. Pushing first
            # (while remotes are still connected to the OLD snapserver) lets
            # them rewrite their env and restart themselves; they then reconnect
            # to the NEW snapserver with the updated buffer_time already applied.
            if snapclient_buffer_time is not None:
                # 1. Persist to settings.json (source of truth) — buffer_time and
                # fragments land together so a crash can't split the pair.
                if settings_service:
                    updates = {'multiroom.snapclient_buffer_time': snapclient_buffer_time}
                    if snapclient_fragments is not None:
                        updates['multiroom.snapclient_fragments'] = snapclient_fragments
                    await settings_service.set_settings(updates)

                # Resolve the effective fragments through the one clamped read
                # path. An explicit value was validated above; a *stored* one
                # used to be passed through raw, and the two consumers below
                # disagreed about it: SnapclientEnv clamps it to 8 for the local
                # speaker while the satellites received it unbounded and
                # answered 422 — one house on two ALSA buffer settings.
                effective_fragments = snapclient_fragments
                if effective_fragments is None:
                    _, effective_fragments = await resolve_snapclient_config(settings_service)

                # 2. Regenerate local snapclient.env with the resolved values
                SnapclientEnv.regenerate(snapclient_buffer_time, effective_fragments)

                # 3. Push to remotes and AWAIT — must complete before snapserver restart
                await _push_snapclient_config_to_remotes(snapclient_buffer_time, effective_fragments)

            # 4. Update snapserver.conf and restart snapserver
            if not await snapcast_service.update_server_config(config):
                logger.error("snapserver rejected the config update")
                raise HTTPException(status_code=502, detail="Snapserver config update failed")

            # 5. Restart local snapclient to pick up the new env (after
            # snapserver restart so it reconnects cleanly to the new server).
            # A refused restart is not cosmetic: every satellite is now on the
            # new buffer_time and the local speaker is still on the old one, so
            # it must reach the caller rather than a log line under a "services
            # restarted" answer.
            if snapclient_buffer_time is not None:
                if routing_service and routing_service.service_manager:
                    unit = routing_service.snapclient_service
                    if not await routing_service.service_manager.restart(unit):
                        logger.error(f"Failed to restart {unit} with buffer_time={snapclient_buffer_time}ms")
                        raise HTTPException(
                            status_code=502,
                            detail=f"Configuration saved but {unit} did not restart"
                        )
                    logger.info(f"Local snapclient restarted with buffer_time={snapclient_buffer_time}ms")

            await _publish_snapcast_update()
            return {
                "status": "success",
                "message": "Configuration updated and services restarted"
            }

    return router
