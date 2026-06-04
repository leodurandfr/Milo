# backend/api/equalizer.py
"""
API routes for CamillaDSP digital signal processing
Full equalizer capabilities including EQ, compressor, loudness, and volume control
Supports multi-client equalizer control for multiroom setups
"""
import asyncio
import logging
from fastapi import APIRouter, HTTPException, Request

from backend.api.route_helpers import api_error_handler

from backend.api.models import (
    EqualizerFilterUpdateRequest,
    EqualizerMuteRequest,
    EqualizerCompressorRequest,
    EqualizerLoudnessRequest,
    ZoneCrossoverRequest,
    EqualizerPresetRequest
)

logger = logging.getLogger(__name__)

ZONE_PREFIX = "zone:"


def _resolve_target(target: str) -> tuple[str, str]:
    """Map a uniform target token to the access-layer (target_type, target_id).

    "local"      → ("client", "local")  — the local DAC (no registry entry needed)
    "zone:<id>"  → ("zone", "<id>")
    "<mac>"      → ("client", "<mac>")   — a remote client
    """
    if target.startswith(ZONE_PREFIX):
        return "zone", target[len(ZONE_PREFIX):]
    return "client", target


def create_equalizer_router(
    camilladsp_service,
    state_machine,
    settings_service=None,
    routing_service=None,
    crossover_service=None,
    proxy_service=None,
    client_registry_service=None,
    equalizer_router_service=None,
    multiroom_equalizer_service=None,
    volume_service=None
):
    """Creates equalizer router with injected dependencies"""
    router = APIRouter(prefix="/api/equalizer", tags=["equalizer"])

    # === Internal Helpers ===

    def _get_online_client_ip(identifier: str):
        """Get client IP from registry, only if the client is online."""
        if not client_registry_service:
            return None
        client = client_registry_service.get_client(identifier)
        if not client or not client.online:
            return None
        return client_registry_service.get_client_ip(identifier)

    def _get_local_client_mac():
        """Get the MAC address of the local client from registry."""
        if not client_registry_service:
            return None
        for client in client_registry_service.get_all_clients().values():
            if client.is_local:
                return client.mac_id
        return None

    # === Equalizer Enable/Disable ===

    @router.get("/enabled")
    async def get_equalizer_effects_enabled():
        """Get equalizer effects enabled state from settings (EQ, compressor, loudness)"""
        try:
            if settings_service:
                enabled = await settings_service.get_setting("routing.equalizer_effects_enabled")
                return {"enabled": enabled if enabled is not None else True}
            return {"enabled": True}
        except Exception as e:
            logger.error(f"Error getting equalizer effects enabled state: {e}")
            return {"enabled": True}

    @router.put("/enabled")
    async def set_equalizer_effects_enabled(request: Request):
        """Set equalizer effects enabled state (EQ, compressor, loudness). Volume always works."""
        async with api_error_handler("Error setting equalizer effects enabled state", logger):
            body = await request.json()
            enabled = body.get("enabled", True)

            active_source = state_machine.system_state.active_source if state_machine else None

            if not routing_service:
                return {"status": "error", "message": "Routing service not available"}

            success = await routing_service.set_equalizer_effects_enabled(enabled, active_source)
            if success:
                logger.info(f"Equalizer effects enabled state set to: {enabled}")
                return {"status": "success", "enabled": enabled}
            else:
                return {"status": "error", "message": "Failed to change equalizer effects state"}

    # === Status & Connection ===

    @router.get("/status")
    async def get_equalizer_status():
        """Get complete equalizer status including filters and state"""
        try:
            status = await camilladsp_service.get_status()
            return status
        except Exception as e:
            return {
                "available": False,
                "state": "disconnected",
                "error": str(e)
            }

    @router.get("/levels")
    async def get_local_levels():
        """Get audio levels directly from local CamillaDSP (no routing/registry needed)."""
        try:
            return await camilladsp_service.get_levels()
        except Exception as e:
            logger.debug(f"Failed to get local levels: {e}")
            return {"available": False, "input_peak": [-80.0, -80.0], "output_peak": [-80.0, -80.0]}

    @router.get("/levels/zone/{client_ids}")
    async def get_zone_levels(client_ids: str):
        """Get aggregated (AVERAGE) audio levels for multiple clients in a zone."""
        ids = client_ids.split(",")

        async def get_client_levels(client_id: str):
            """Get levels from a single client using equalizer_router_service."""
            try:
                # equalizer_router_service.get_levels handles MAC → IP routing automatically
                return await equalizer_router_service.get_levels(client_id)
            except Exception as e:
                logger.debug(f"Failed to get equalizer levels for {client_id}: {e}")
                return None

        # Poll all clients in parallel
        results = await asyncio.gather(*[get_client_levels(cid) for cid in ids])

        # Collect available readings
        input_peaks = []
        output_peaks = []

        for r in results:
            if r and r.get("available"):
                input_peaks.append(r.get("input_peak", [-80.0, -80.0]))
                output_peaks.append(r.get("output_peak", [-80.0, -80.0]))

        # Aggregate: AVERAGE of all available readings
        if input_peaks:
            input_peak = [
                sum(p[0] for p in input_peaks) / len(input_peaks),
                sum(p[1] for p in input_peaks) / len(input_peaks)
            ]
            output_peak = [
                sum(p[0] for p in output_peaks) / len(output_peaks),
                sum(p[1] for p in output_peaks) / len(output_peaks)
            ]
            available = True
        else:
            input_peak = [-80.0, -80.0]
            output_peak = [-80.0, -80.0]
            available = False

        return {"available": available, "input_peak": input_peak, "output_peak": output_peak}

    # === Filter Management ===

    @router.get("/filters")
    async def get_all_filters():
        """Get all filter bands with their current configuration"""
        try:
            filters = await camilladsp_service.get_filters()
            return {"filters": filters}
        except Exception as e:
            return {"filters": [], "error": str(e)}

    @router.put("/filter/{filter_id}")
    async def update_filter(filter_id: str, payload: EqualizerFilterUpdateRequest):
        """Update an existing filter band"""
        async with api_error_handler("Error updating filter"):
            filters = await camilladsp_service.get_filters()
            current_filter = next((f for f in filters if f["id"] == filter_id), None)

            if not current_filter:
                raise HTTPException(status_code=404, detail=f"Filter {filter_id} not found")

            freq = payload.freq if payload.freq is not None else current_filter["freq"]
            gain = payload.gain if payload.gain is not None else current_filter["gain"]
            q = payload.q if payload.q is not None else current_filter.get("q", 1.0)
            filter_type = payload.filter_type if payload.filter_type is not None else current_filter.get("type", "Peaking")
            enabled = payload.enabled if payload.enabled is not None else current_filter.get("enabled", True)

            success = await camilladsp_service.set_filter(
                filter_id=filter_id,
                freq=freq,
                gain=gain,
                q=q,
                filter_type=filter_type,
                enabled=enabled
            )

            if success:
                return {
                    "status": "success",
                    "id": filter_id,
                    "freq": freq,
                    "gain": gain,
                    "q": q,
                    "type": filter_type
                }

            return {"status": "error", "message": "Failed to update filter"}

    # === Preset Management ===

    @router.get("/presets")
    async def get_presets():
        """Get all builtin presets with their gains, custom gains, and active preset ID."""
        try:
            presets = camilladsp_service.get_presets()
            active_preset = await camilladsp_service.get_active_preset()
            custom_gains = await camilladsp_service.get_custom_gains()
            return {
                "presets": presets,
                "custom_gains": custom_gains,
                "active_preset": active_preset
            }
        except Exception as e:
            return {"presets": [], "custom_gains": [0]*10, "active_preset": None, "error": str(e)}

    @router.put("/preset/{preset_id}")
    async def load_preset(preset_id: str):
        """Load a builtin preset by ID."""
        async with api_error_handler("Error loading preset"):
            success = await camilladsp_service.load_preset(preset_id)

            if success:
                return {"status": "success", "id": preset_id}

            raise HTTPException(status_code=404, detail=f"Preset '{preset_id}' not found")

    @router.post("/save-custom")
    async def save_custom_preset():
        """Save current filter gains as the custom preset and activate it."""
        async with api_error_handler("Error saving custom preset", logger):
            await camilladsp_service.save_custom_gains()
            await camilladsp_service.set_active_preset("custom")

            return {"status": "success", "preset_id": "custom"}

    @router.post("/zone/{zone_id}/save-custom")
    async def save_zone_custom_preset(zone_id: str):
        """Save current zone EQ gains as the custom preset and activate it."""
        async with api_error_handler(f"Error saving custom preset for zone {zone_id}", logger):
            try:
                await multiroom_equalizer_service.save_custom_preset("zone", zone_id)
            except ValueError as e:
                raise HTTPException(status_code=404, detail=str(e))

            return {"status": "success", "zone_id": zone_id, "preset_id": "custom"}

    @router.post("/client/{mac_id}/save-custom")
    async def save_client_custom_preset(mac_id: str):
        """Save current client EQ gains as the custom preset and activate it."""
        async with api_error_handler(f"Error saving custom preset for client {mac_id}", logger):
            try:
                await multiroom_equalizer_service.save_custom_preset("client", mac_id)
            except ValueError as e:
                raise HTTPException(status_code=404, detail=str(e))

            return {"status": "success", "client_id": mac_id, "preset_id": "custom"}

    @router.get("/zone/{zone_id}")
    async def get_zone_equalizer(zone_id: str):
        """Get equalizer settings for a zone (source of truth for zone context)."""
        async with api_error_handler(f"Error getting equalizer for zone {zone_id}", logger):
            try:
                settings = await multiroom_equalizer_service.get_equalizer("zone", zone_id)
            except ValueError as e:
                raise HTTPException(status_code=404, detail=str(e))
            if not settings:
                raise HTTPException(status_code=404, detail=f"Zone not found: {zone_id}")
            return settings.to_dict()

    @router.post("/zone/{zone_id}/preset")
    async def load_preset_for_zone(zone_id: str, payload: EqualizerPresetRequest):
        """
        Load a preset for all clients in a zone.

        Applies the preset to all ONLINE zone members. OFFLINE clients will
        receive settings on reconnection via sync service.
        Returns resolved gains so the frontend can apply them immediately.
        """
        async with api_error_handler(f"Error loading preset for zone {zone_id}", logger):
            try:
                current = await multiroom_equalizer_service.get_zone_equalizer(zone_id)
                gains = await multiroom_equalizer_service.resolve_preset_gains(payload.preset_id, current)
                success = await multiroom_equalizer_service.load_zone_preset(zone_id, payload.preset_id)
            except ValueError as e:
                raise HTTPException(status_code=404, detail=str(e))
            return {
                "status": "success" if success else "error",
                "zone_id": zone_id,
                "preset_id": payload.preset_id,
                "gains": gains
            }

    @router.patch("/zone/{zone_id}/filter/{filter_id}")
    async def update_zone_filter(zone_id: str, filter_id: str, payload: EqualizerFilterUpdateRequest):
        """
        Update a filter for all clients in a zone.

        Applies the filter change to all ONLINE zone members. OFFLINE clients will
        receive settings on reconnection via sync service.
        """
        async with api_error_handler(f"Error updating filter for zone {zone_id}", logger):
            try:
                success = await multiroom_equalizer_service.update_filter(
                    target_type="zone",
                    target_id=zone_id,
                    filter_id=filter_id,
                    frequency=payload.freq,
                    gain=payload.gain,
                    q=payload.q,
                    filter_type=payload.filter_type,
                    enabled=payload.enabled
                )
            except ValueError as e:
                raise HTTPException(status_code=404, detail=str(e))
            return {
                "status": "success" if success else "error",
                "zone_id": zone_id,
                "filter_id": filter_id
            }

    @router.patch("/zone/{zone_id}/compressor")
    async def update_zone_compressor(zone_id: str, payload: EqualizerCompressorRequest):
        """
        Update compressor settings for all clients in a zone.

        Applies compressor changes to all ONLINE zone members. OFFLINE clients will
        receive settings on reconnection via sync service.
        """
        async with api_error_handler(f"Error updating compressor for zone {zone_id}", logger):
            try:
                success = await multiroom_equalizer_service.update_compressor(
                    target_type="zone",
                    target_id=zone_id,
                    enabled=payload.enabled,
                    threshold=payload.threshold,
                    ratio=payload.ratio,
                    attack=payload.attack,
                    release=payload.release,
                    makeup_gain=payload.makeup_gain
                )
            except ValueError as e:
                raise HTTPException(status_code=404, detail=str(e))
            return {"status": "success" if success else "error", "zone_id": zone_id}

    @router.patch("/zone/{zone_id}/loudness")
    async def update_zone_loudness(zone_id: str, payload: EqualizerLoudnessRequest):
        """
        Update loudness settings for all clients in a zone.

        Applies loudness changes to all ONLINE zone members. OFFLINE clients will
        receive settings on reconnection via sync service.
        """
        async with api_error_handler(f"Error updating loudness for zone {zone_id}", logger):
            try:
                success = await multiroom_equalizer_service.update_loudness(
                    target_type="zone",
                    target_id=zone_id,
                    enabled=payload.enabled,
                    high_boost=payload.high_boost,
                    low_boost=payload.low_boost
                )
            except ValueError as e:
                raise HTTPException(status_code=404, detail=str(e))
            return {"status": "success" if success else "error", "zone_id": zone_id}

    @router.patch("/zone/{zone_id}/mono")
    async def update_zone_mono(zone_id: str, request: Request):
        """
        Set mono/stereo mixing for all clients in a zone.

        Applies mono change to all ONLINE zone members. OFFLINE clients will
        receive settings on reconnection via sync service.
        """
        async with api_error_handler(f"Error updating mono for zone {zone_id}", logger):
            body = await request.json()
            enabled = body.get("enabled")
            if enabled is None:
                raise HTTPException(status_code=400, detail="'enabled' field is required")
            try:
                success = await multiroom_equalizer_service.update_mono(
                    target_type="zone",
                    target_id=zone_id,
                    enabled=enabled,
                )
            except ValueError as e:
                raise HTTPException(status_code=404, detail=str(e))
            return {"status": "success" if success else "error", "zone_id": zone_id, "mono": enabled}

    @router.patch("/zone/{zone_id}/enabled")
    async def update_zone_equalizer_enabled(zone_id: str, request: Request):
        """
        Enable/disable equalizer effects for all clients in a zone.

        When disabled, Equalizer effects (EQ, compressor, loudness) are bypassed but
        volume control remains active. Crossover filters are NOT affected.
        OFFLINE clients will receive settings on reconnection via sync service.
        """
        async with api_error_handler(f"Error updating equalizer enabled for zone {zone_id}", logger):
            body = await request.json()
            enabled = body.get("enabled")

            if enabled is None:
                raise HTTPException(status_code=400, detail="'enabled' field is required")

            try:
                success = await multiroom_equalizer_service.set_zone_equalizer_effects_enabled(zone_id, enabled)
            except ValueError as e:
                raise HTTPException(status_code=404, detail=str(e))
            return {
                "status": "success" if success else "error",
                "zone_id": zone_id,
                "enabled": enabled
            }

    @router.post("/client/{mac_id}/preset")
    async def load_preset_for_client(mac_id: str, payload: EqualizerPresetRequest):
        """
        Load a preset for a standalone client by MAC ID.

        Applies the preset to the client. For zone clients, use the zone preset endpoint.
        Returns resolved gains so the frontend can apply them immediately.
        """
        async with api_error_handler(f"Error loading preset for client {mac_id}", logger):
            try:
                current = await multiroom_equalizer_service.get_client_equalizer(mac_id)
                gains = await multiroom_equalizer_service.resolve_preset_gains(payload.preset_id, current)
                success = await multiroom_equalizer_service.load_client_preset(mac_id, payload.preset_id)
            except ValueError as e:
                raise HTTPException(status_code=404, detail=str(e))
            return {
                "status": "success" if success else "error",
                "client_id": mac_id,
                "preset_id": payload.preset_id,
                "gains": gains
            }

    # === Mute Control ===

    @router.put("/mute")
    async def set_mute(payload: EqualizerMuteRequest):
        """
        Mute/unmute local CamillaDSP.

        In multiroom mode, this only mutes the local client without affecting others.
        """
        # Update local CamillaDSP hardware
        result = await camilladsp_service.set_mute(payload.muted)

        if not result:
            raise HTTPException(status_code=500, detail="Failed to set mute")

        # Update volume state store
        if volume_service:
            local_mac = _get_local_client_mac()
            if local_mac:
                await volume_service.set_client_mute(local_mac, payload.muted, broadcast=True)

        return {"status": "success", "mute": payload.muted}

    # === Compressor ===

    @router.get("/compressor")
    async def get_compressor():
        """Get compressor settings"""
        try:
            compressor = await camilladsp_service.get_compressor()
            return compressor
        except Exception as e:
            return {"enabled": False, "error": str(e)}

    @router.put("/compressor")
    async def set_compressor(payload: EqualizerCompressorRequest):
        """Update compressor settings"""
        async with api_error_handler("Error updating compressor"):
            success = await camilladsp_service.set_compressor(
                enabled=payload.enabled,
                threshold=payload.threshold,
                ratio=payload.ratio,
                attack=payload.attack,
                release=payload.release,
                makeup_gain=payload.makeup_gain
            )

            if success:
                compressor = await camilladsp_service.get_compressor()
                return {"status": "success", **compressor}

            return {"status": "error", "message": "Failed to update compressor"}

    # === Loudness Compensation ===

    @router.get("/loudness")
    async def get_loudness():
        """Get loudness compensation settings"""
        try:
            loudness = await camilladsp_service.get_loudness()
            return loudness
        except Exception as e:
            return {"enabled": False, "error": str(e)}

    @router.put("/loudness")
    async def set_loudness(payload: EqualizerLoudnessRequest):
        """Update loudness compensation settings"""
        async with api_error_handler("Error updating loudness"):
            success = await camilladsp_service.set_loudness(
                enabled=payload.enabled,
                high_boost=payload.high_boost,
                low_boost=payload.low_boost
            )

            if success:
                loudness = await camilladsp_service.get_loudness()
                return {"status": "success", **loudness}

            return {"status": "error", "message": "Failed to update loudness"}

    # === Speaker Type / Crossover Management ===
    # Note: Zone CRUD moved to /api/multiroom/zones, speaker-type to /api/multiroom/clients

    # Note: PUT /client/{client_id}/speaker-type moved to PATCH /api/multiroom/clients/{mac_id}

    @router.put("/client/{client_id}/crossover-frequency")
    async def set_client_crossover_frequency(client_id: str, payload: dict):
        """Set custom crossover frequency for a client"""
        async with api_error_handler("Error setting client crossover frequency", logger):
            freq = payload.get("frequency")
            if freq is None:
                raise HTTPException(status_code=400, detail="frequency is required")
            cs = crossover_service
            if not await cs.set_client_crossover_frequency(client_id, float(freq)):
                raise HTTPException(status_code=500, detail="Failed to update crossover frequency")
            ct = await cs.get_client_type(client_id)
            return {"status": "success", "client_id": client_id, "speaker_type": ct.get("speaker_type"),
                    "crossover_frequency": ct.get("crossover_frequency")}

    @router.get("/links/{zone_id}/crossover")
    async def get_zone_crossover(zone_id: str):
        """Get crossover settings for a zone"""
        try:
            return {"zone_id": zone_id, **await crossover_service.get_zone_crossover(zone_id)}
        except Exception as e:
            logger.error(f"Error getting zone crossover: {e}")
            return {"zone_id": zone_id, "frequency": 80, "enabled": False, "has_subwoofer": False}

    @router.get("/links/{zone_id}/auto-crossover")
    async def get_zone_auto_crossover(zone_id: str):
        """Get automatic crossover frequency for a zone (MIN of speaker frequencies)"""
        try:
            return {"zone_id": zone_id, "frequency": await crossover_service.get_zone_auto_crossover(zone_id)}
        except Exception as e:
            logger.error(f"Error getting zone auto crossover: {e}")
            return {"zone_id": zone_id, "frequency": 80}

    @router.put("/links/{zone_id}/crossover")
    async def set_zone_crossover(zone_id: str, payload: ZoneCrossoverRequest):
        """Set crossover frequency for a zone"""
        async with api_error_handler("Error setting zone crossover", logger):
            cs = crossover_service
            if not await cs.set_zone_crossover_frequency(zone_id, payload.frequency):
                raise HTTPException(status_code=500, detail="Failed to update zone crossover")
            return {"status": "success", "zone_id": zone_id, **await cs.get_zone_crossover(zone_id)}

    @router.post("/links/{zone_id}/crossover/apply")
    async def apply_zone_crossover(zone_id: str):
        """Manually apply crossover settings to all clients in a zone"""
        async with api_error_handler("Error applying zone crossover", logger):
            if not await crossover_service.apply_zone_crossover(zone_id):
                raise HTTPException(status_code=500, detail="Failed to apply crossover")
            return {"status": "success", "message": f"Crossover applied to zone {zone_id}"}

    # === Client Equalizer Proxy Routes ===

    @router.get("/client/{hostname}/status")
    async def get_client_equalizer_status(hostname: str):
        """Get equalizer status for a specific client with consistent volume."""
        # Get base status via EqualizerRouter
        status = await equalizer_router_service.get_status(hostname)

        # Inject volume from volume_service (source of truth)
        if volume_service:
            vol = await volume_service.get_client_volume(hostname)
            if 'volume' not in status:
                status['volume'] = {}
            status['volume']['main'] = vol['main']
            status['volume']['mute'] = vol['mute']

        # Inject active_preset from the standalone-equalizer store (the satellite
        # has no preset concept) so the UI highlights the correct preset per target.
        if multiroom_equalizer_service:
            client_eq = await multiroom_equalizer_service.get_client_equalizer(hostname)
            if client_eq:
                status['active_preset'] = client_eq.active_preset

        return status

    @router.get("/client/{hostname}/filters")
    async def get_client_equalizer_filters(hostname: str):
        """Proxy equalizer filters request to client"""
        return await equalizer_router_service.get_filters(hostname)

    @router.put("/client/{hostname}/filter/{filter_id}")
    async def update_client_equalizer_filter(hostname: str, filter_id: str, payload: EqualizerFilterUpdateRequest):
        """Update one EQ band for a client through the unified per-client access
        layer (applies to the satellite + persists the record; one write path)."""
        async with api_error_handler(f"Error updating filter for client {hostname}", logger):
            try:
                await multiroom_equalizer_service.update_filter(
                    target_type="client",
                    target_id=hostname,
                    filter_id=filter_id,
                    frequency=payload.freq,
                    gain=payload.gain,
                    q=payload.q,
                    filter_type=payload.filter_type,
                    enabled=payload.enabled,
                )
            except ValueError as e:
                logger.error(f"Filter update failed for client {hostname}: {e}")
                raise HTTPException(status_code=404, detail=str(e))
            return {"status": "success", "id": filter_id}

    @router.put("/client/{hostname}/compressor")
    async def update_client_compressor(hostname: str, payload: EqualizerCompressorRequest):
        """Update compressor for a client through the unified access layer."""
        async with api_error_handler(f"Error updating compressor for client {hostname}", logger):
            try:
                await multiroom_equalizer_service.update_compressor(
                    target_type="client",
                    target_id=hostname,
                    enabled=payload.enabled,
                    threshold=payload.threshold,
                    ratio=payload.ratio,
                    attack=payload.attack,
                    release=payload.release,
                    makeup_gain=payload.makeup_gain,
                )
            except ValueError as e:
                logger.error(f"Compressor update failed for client {hostname}: {e}")
                raise HTTPException(status_code=404, detail=str(e))
            return {"status": "success", "client_id": hostname}

    @router.put("/client/{hostname}/loudness")
    async def update_client_loudness(hostname: str, payload: EqualizerLoudnessRequest):
        """Update loudness for a client through the unified access layer."""
        async with api_error_handler(f"Error updating loudness for client {hostname}", logger):
            try:
                await multiroom_equalizer_service.update_loudness(
                    target_type="client",
                    target_id=hostname,
                    enabled=payload.enabled,
                    high_boost=payload.high_boost,
                    low_boost=payload.low_boost,
                )
            except ValueError as e:
                logger.error(f"Loudness update failed for client {hostname}: {e}")
                raise HTTPException(status_code=404, detail=str(e))
            return {"status": "success", "client_id": hostname}

    @router.put("/client/{hostname}/mono")
    async def update_client_mono(hostname: str, request: Request):
        """Update mono for a client through the unified access layer."""
        async with api_error_handler(f"Error updating mono for client {hostname}", logger):
            body = await request.json()
            enabled = body.get("enabled")
            if enabled is None:
                raise HTTPException(status_code=400, detail="'enabled' field is required")
            try:
                await multiroom_equalizer_service.update_mono(
                    target_type="client",
                    target_id=hostname,
                    enabled=enabled,
                )
            except ValueError as e:
                logger.error(f"Mono update failed for client {hostname}: {e}")
                raise HTTPException(status_code=404, detail=str(e))
            return {"status": "success", "client_id": hostname, "mono": enabled}

    @router.put("/client/{hostname}/enabled")
    async def update_client_equalizer_enabled(hostname: str, request: Request):
        """Set equalizer effects enabled state for a client (local or remote)
        through the unified access layer.

        When enabled=False, Equalizer effects (EQ, compressor, loudness) are
        bypassed. Volume control remains active regardless of this setting.
        """
        async with api_error_handler(f"Error updating equalizer enabled for client {hostname}", logger):
            body = await request.json()
            enabled = body.get("enabled")
            if enabled is None:
                raise HTTPException(status_code=400, detail="'enabled' field is required")
            try:
                success = await multiroom_equalizer_service.set_client_equalizer_effects_enabled(
                    hostname, enabled, routing_service
                )
            except ValueError as e:
                logger.error(f"Equalizer enabled update failed for client {hostname}: {e}")
                raise HTTPException(status_code=404, detail=str(e))
            return {
                "status": "success" if success else "error",
                "client_id": hostname,
                "enabled": enabled,
            }

    @router.get("/client/{hostname}/enabled")
    async def get_client_equalizer_enabled(hostname: str):
        """Get equalizer effects enabled state for a specific client (local or remote).

        Symmetric read for the PUT above so the frontend can show the correct
        per-target enabled state instead of falling back to the local Milo's flag.
        """
        return await equalizer_router_service.get_equalizer_enabled(hostname, routing_service)

    # === Client Settings Persistence ===

    @router.post("/client/{hostname}/restore")
    async def restore_client_settings(hostname: str):
        """Restore a client's persisted standalone equalizer settings.

        Reads the registry standalone-equalizer store (single source of truth)
        and re-pushes filters/compressor/loudness/mono + the master enabled flag.
        Volume/mute are owned by the volume service and synced separately.
        """
        if not proxy_service or not client_registry_service:
            return {"status": "error", "restored": [], "errors": ["Services not available"]}

        # Local client: settings are applied directly via camilladsp_service
        if equalizer_router_service.is_local_client(hostname):
            return {"status": "skipped", "message": "Local client settings are managed directly", "restored": []}

        # Get IP for remote client
        client_ip = _get_online_client_ip(hostname)
        if not client_ip:
            return {"status": "error", "restored": [], "errors": [f"Client {hostname} not found or offline"]}

        eq = client_registry_service.get_client_equalizer(hostname)
        if not eq:
            return {"status": "success", "message": "No saved settings to restore", "restored": []}

        restored, errors = [], []

        async def try_restore(name: str, path: str, data):
            try:
                await proxy_service.request(client_ip, "PUT", path, data)
                restored.append(name)
            except Exception as e:
                errors.append(f"{name}: {e}")

        for flt in eq.filters:
            if not flt.id:
                continue
            await try_restore(f"filter:{flt.id}", f"/equalizer/filter/{flt.id}", {
                "freq": flt.frequency, "gain": flt.gain, "q": flt.q,
                "filter_type": flt.filter_type.value if hasattr(flt.filter_type, "value") else flt.filter_type,
            })
        if eq.compressor:
            await try_restore("compressor", "/equalizer/compressor", eq.compressor.to_dict())
        if eq.loudness:
            await try_restore("loudness", "/equalizer/loudness", eq.loudness.to_dict())
        await try_restore("mono", "/equalizer/mono", {"enabled": eq.mono})
        # Master enabled/bypass LAST, so it bypasses or restores the effects just pushed.
        await try_restore("enabled", "/equalizer/enabled", {"enabled": eq.enabled})

        logger.info(f"Restored settings for {hostname}: {restored}")
        if errors:
            logger.warning(f"Errors restoring settings for {hostname}: {errors}")
        return {"status": "success" if not errors else "partial", "restored": restored, "errors": errors or None}

    # === Unified Per-Target Routes (one grammar for local / remote / zone) ===

    @router.get("/target/{target}")
    async def get_target_equalizer(target: str):
        """Unified per-target EQ read — the complete record for display.

        ``target`` ∈ "local" (the local DAC) · "<mac>" (a remote client) ·
        "zone:<id>" (a zone, derived from its members). Returns one record:
        ``{enabled, active_preset, mono, compressor, loudness, custom_gains,
        filters, state, sample_rate, available}`` — filters in the frontend wire
        shape (``freq``/``type``), a superset of the legacy /status + /filters.
        """
        async with api_error_handler(f"Error getting equalizer for target {target}", logger):
            target_type, target_id = _resolve_target(target)

            # Validate existence up front so an unknown target fails loud (404):
            #  - remote client → must be in the registry (the local sentinel has no
            #    entry; without this the access layer would hand back a neutral
            #    default record and mask the unknown MAC).
            #  - zone → the access layer returns None below for an unknown zone.
            if (
                target_type == "client"
                and target_id != "local"
                and client_registry_service
                and not client_registry_service.get_client(target_id)
            ):
                logger.error(f"Equalizer read for unknown client: {target_id}")
                raise HTTPException(status_code=404, detail=f"Client not found: {target_id}")

            try:
                record = await multiroom_equalizer_service.get_equalizer(target_type, target_id)
            except ValueError as e:
                logger.error(f"Equalizer read failed for target {target}: {e}")
                raise HTTPException(status_code=404, detail=str(e))
            if record is None:
                # Only reachable for an unknown zone — a client always yields a record.
                logger.error(f"Equalizer target not found: {target}")
                raise HTTPException(status_code=404, detail=f"Equalizer target not found: {target}")

            # Live connection state for the UI (the record carries no runtime state).
            # A remote client reads its satellite via the router; local and zone
            # targets read the local CamillaDSP (the zone's representative state).
            if target_type == "client" and target_id != "local":
                status = await equalizer_router_service.get_status(target_id)
            else:
                status = await camilladsp_service.get_status()

            return {
                "enabled": record.enabled,
                "active_preset": record.active_preset,
                "mono": record.mono,
                "compressor": record.compressor.to_dict(),
                "loudness": record.loudness.to_dict(),
                "custom_gains": record.custom_gains,
                "filters": [f.to_wire_dict() for f in record.filters],
                "state": status.get("state", "disconnected"),
                "sample_rate": status.get("sample_rate"),
                "available": status.get("available", False),
            }

    @router.put("/target/{target}/filter/{filter_id}")
    async def update_target_filter(target: str, filter_id: str, payload: EqualizerFilterUpdateRequest):
        """Update one EQ band for any target through the unified access layer."""
        async with api_error_handler(f"Error updating filter for target {target}", logger):
            target_type, target_id = _resolve_target(target)
            try:
                await multiroom_equalizer_service.update_filter(
                    target_type=target_type,
                    target_id=target_id,
                    filter_id=filter_id,
                    frequency=payload.freq,
                    gain=payload.gain,
                    q=payload.q,
                    filter_type=payload.filter_type,
                    enabled=payload.enabled,
                )
            except ValueError as e:
                logger.error(f"Filter update failed for target {target}: {e}")
                raise HTTPException(status_code=404, detail=str(e))
            return {"status": "success", "target": target, "filter_id": filter_id}

    @router.put("/target/{target}/compressor")
    async def update_target_compressor(target: str, payload: EqualizerCompressorRequest):
        """Update the compressor for any target through the unified access layer."""
        async with api_error_handler(f"Error updating compressor for target {target}", logger):
            target_type, target_id = _resolve_target(target)
            try:
                await multiroom_equalizer_service.update_compressor(
                    target_type=target_type,
                    target_id=target_id,
                    enabled=payload.enabled,
                    threshold=payload.threshold,
                    ratio=payload.ratio,
                    attack=payload.attack,
                    release=payload.release,
                    makeup_gain=payload.makeup_gain,
                )
            except ValueError as e:
                logger.error(f"Compressor update failed for target {target}: {e}")
                raise HTTPException(status_code=404, detail=str(e))
            return {"status": "success", "target": target}

    @router.put("/target/{target}/loudness")
    async def update_target_loudness(target: str, payload: EqualizerLoudnessRequest):
        """Update loudness for any target through the unified access layer."""
        async with api_error_handler(f"Error updating loudness for target {target}", logger):
            target_type, target_id = _resolve_target(target)
            try:
                await multiroom_equalizer_service.update_loudness(
                    target_type=target_type,
                    target_id=target_id,
                    enabled=payload.enabled,
                    high_boost=payload.high_boost,
                    low_boost=payload.low_boost,
                )
            except ValueError as e:
                logger.error(f"Loudness update failed for target {target}: {e}")
                raise HTTPException(status_code=404, detail=str(e))
            return {"status": "success", "target": target}

    @router.put("/target/{target}/mono")
    async def update_target_mono(target: str, request: Request):
        """Set mono/stereo for any target through the unified access layer.

        (The legacy local route was missing — toggling Mono on the local device
        used to 404. This uniform route fixes that by construction.)
        """
        async with api_error_handler(f"Error updating mono for target {target}", logger):
            body = await request.json()
            enabled = body.get("enabled")
            if enabled is None:
                raise HTTPException(status_code=400, detail="'enabled' field is required")
            target_type, target_id = _resolve_target(target)
            try:
                await multiroom_equalizer_service.update_mono(
                    target_type=target_type,
                    target_id=target_id,
                    enabled=enabled,
                )
            except ValueError as e:
                logger.error(f"Mono update failed for target {target}: {e}")
                raise HTTPException(status_code=404, detail=str(e))
            return {"status": "success", "target": target, "mono": enabled}

    @router.put("/target/{target}/enabled")
    async def set_target_enabled(target: str, request: Request):
        """Enable/disable equalizer effects for any target (volume stays active)."""
        async with api_error_handler(f"Error updating equalizer enabled for target {target}", logger):
            body = await request.json()
            enabled = body.get("enabled")
            if enabled is None:
                raise HTTPException(status_code=400, detail="'enabled' field is required")
            target_type, target_id = _resolve_target(target)
            try:
                if target_type == "zone":
                    success = await multiroom_equalizer_service.set_zone_equalizer_effects_enabled(
                        target_id, enabled
                    )
                else:
                    success = await multiroom_equalizer_service.set_client_equalizer_effects_enabled(
                        target_id, enabled, routing_service
                    )
            except ValueError as e:
                logger.error(f"Equalizer enabled update failed for target {target}: {e}")
                raise HTTPException(status_code=404, detail=str(e))
            return {"status": "success" if success else "error", "target": target, "enabled": enabled}

    @router.post("/target/{target}/preset")
    async def load_target_preset(target: str, payload: EqualizerPresetRequest):
        """Load a preset for any target; returns resolved gains for immediate UI apply."""
        async with api_error_handler(f"Error loading preset for target {target}", logger):
            target_type, target_id = _resolve_target(target)
            try:
                if target_type == "zone":
                    current = await multiroom_equalizer_service.get_zone_equalizer(target_id)
                    gains = await multiroom_equalizer_service.resolve_preset_gains(payload.preset_id, current)
                    success = await multiroom_equalizer_service.load_zone_preset(target_id, payload.preset_id)
                else:
                    current = await multiroom_equalizer_service.get_client_equalizer(target_id)
                    gains = await multiroom_equalizer_service.resolve_preset_gains(payload.preset_id, current)
                    success = await multiroom_equalizer_service.load_client_preset(target_id, payload.preset_id)
            except ValueError as e:
                logger.error(f"Preset load failed for target {target}: {e}")
                raise HTTPException(status_code=404, detail=str(e))
            return {
                "status": "success" if success else "error",
                "target": target,
                "preset_id": payload.preset_id,
                "gains": gains,
            }

    @router.post("/target/{target}/save-custom")
    async def save_target_custom_preset(target: str):
        """Snapshot the target's current gains as its 'custom' preset and activate it."""
        async with api_error_handler(f"Error saving custom preset for target {target}", logger):
            target_type, target_id = _resolve_target(target)
            try:
                await multiroom_equalizer_service.save_custom_preset(target_type, target_id)
            except ValueError as e:
                logger.error(f"Custom-preset save failed for target {target}: {e}")
                raise HTTPException(status_code=404, detail=str(e))
            return {"status": "success", "target": target, "preset_id": "custom"}

    return router
