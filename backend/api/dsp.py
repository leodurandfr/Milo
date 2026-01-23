# backend/presentation/api/routes/dsp.py
"""
API routes for CamillaDSP digital signal processing
Full DSP capabilities including EQ, compressor, loudness, and volume control
Supports multi-client DSP control for multiroom setups
"""
import asyncio
import logging
from fastapi import APIRouter, HTTPException, Request

from backend.api.models import (
    DspFilterRequest,
    DspFilterUpdateRequest,
    DspVolumeRequest,
    DspMuteRequest,
    DspCompressorRequest,
    DspLoudnessRequest,
    ZoneCrossoverRequest,
    CrossoverFilterRequest,
    DspPresetRequest
)

logger = logging.getLogger(__name__)


def create_dsp_router(
    dsp_service,
    state_machine,
    settings_service=None,
    routing_service=None,
    crossover_service=None,
    proxy_service=None,
    sync_service=None,
    client_registry_service=None,
    dsp_router_service=None,
    multiroom_dsp_service=None
):
    """Creates DSP router with injected dependencies"""
    router = APIRouter(prefix="/api/dsp", tags=["dsp"])

    # === Internal Helpers ===

    def _get_client_ip(identifier: str):
        """Get client IP from registry, or None if not found or offline."""
        if not client_registry_service:
            return None
        client = client_registry_service.get_client(identifier)
        # Only return IP if client exists, has a valid IP, and is ONLINE
        if client and client.ip and client.ip != "127.0.0.1" and client.online:
            return client.ip
        return None

    def _get_volume_service():
        """Get volume_service from state_machine or raise 500."""
        if state_machine:
            vs = getattr(state_machine, 'volume_service', None)
            if vs:
                return vs
        raise HTTPException(status_code=500, detail="Volume service not available")

    # === DSP Enable/Disable ===

    @router.get("/enabled")
    async def get_dsp_effects_enabled():
        """Get DSP effects enabled state from settings (EQ, compressor, loudness)"""
        try:
            if settings_service:
                enabled = await settings_service.get_setting("dsp.effects_enabled")
                return {"enabled": enabled if enabled is not None else True}
            return {"enabled": True}
        except Exception as e:
            logger.error(f"Error getting DSP effects enabled state: {e}")
            return {"enabled": True}

    @router.put("/enabled")
    async def set_dsp_effects_enabled(request: Request):
        """Set DSP effects enabled state (EQ, compressor, loudness). Volume always works."""
        try:
            body = await request.json()
            enabled = body.get("enabled", True)

            # Get active source for potential restart
            active_source = None
            if state_machine:
                async with state_machine._state_lock:
                    active_source = state_machine.system_state.active_source

            # Use routing_service to toggle DSP effects
            if not routing_service:
                return {"status": "error", "message": "Routing service not available"}

            success = await routing_service.set_dsp_effects_enabled(enabled, active_source)
            if success:
                logger.info(f"DSP effects enabled state set to: {enabled}")
                return {"status": "success", "enabled": enabled}
            else:
                return {"status": "error", "message": "Failed to change DSP effects state"}
        except Exception as e:
            logger.error(f"Error setting DSP effects enabled state: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # === Status & Connection ===

    @router.get("/status")
    async def get_dsp_status():
        """Get complete DSP status including filters and state"""
        try:
            status = await dsp_service.get_status()
            return status
        except Exception as e:
            return {
                "available": False,
                "state": "disconnected",
                "error": str(e)
            }

    @router.get("/levels")
    async def get_dsp_levels():
        """Get real-time audio levels (peak/RMS)"""
        try:
            levels = await dsp_service.get_levels()
            return levels
        except Exception as e:
            return {"available": False, "error": str(e)}

    @router.get("/levels/zone/{client_ids}")
    async def get_zone_levels(client_ids: str):
        """Get aggregated (MAX) audio levels for multiple clients in a zone."""
        ids = client_ids.split(",")

        async def get_client_levels(client_id: str):
            """Get levels from a single client using dsp_router_service."""
            try:
                # dsp_router_service.get_levels handles MAC → IP routing automatically
                return await dsp_router_service.get_levels(client_id)
            except Exception as e:
                logger.debug(f"Failed to get DSP levels for {client_id}: {e}")
                return None

        # Poll all clients in parallel
        results = await asyncio.gather(*[get_client_levels(cid) for cid in ids])

        # Aggregate: MAX of all available readings
        input_peak = [-80.0, -80.0]
        output_peak = [-80.0, -80.0]
        available = False

        for r in results:
            if r and r.get("available"):
                available = True
                inp = r.get("input_peak", [-80.0, -80.0])
                out = r.get("output_peak", [-80.0, -80.0])
                input_peak = [max(input_peak[0], inp[0]), max(input_peak[1], inp[1])]
                output_peak = [max(output_peak[0], out[0]), max(output_peak[1], out[1])]

        return {"available": available, "input_peak": input_peak, "output_peak": output_peak}

    @router.post("/connect")
    async def connect_dsp():
        """Manually trigger connection to CamillaDSP daemon"""
        try:
            success = await dsp_service.connect()
            if success:
                return {"status": "success", "message": "Connected to CamillaDSP"}
            return {"status": "error", "message": "Failed to connect"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/disconnect")
    async def disconnect_dsp():
        """Disconnect from CamillaDSP daemon"""
        try:
            await dsp_service.disconnect()
            return {"status": "success", "message": "Disconnected from CamillaDSP"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # === Filter Management ===

    @router.get("/filters")
    async def get_all_filters():
        """Get all filter bands with their current configuration"""
        try:
            filters = await dsp_service.get_filters()
            return {"filters": filters}
        except Exception as e:
            return {"filters": [], "error": str(e)}

    @router.post("/filter")
    async def add_filter(payload: DspFilterRequest):
        """Add a new filter band"""
        try:
            # Generate unique filter ID
            existing_filters = await dsp_service.get_filters()
            filter_num = len(existing_filters)
            filter_id = f"eq_band_{filter_num:02d}"

            success = await dsp_service.add_filter(
                filter_id=filter_id,
                freq=payload.freq,
                gain=payload.gain,
                q=payload.q,
                filter_type=payload.filter_type
            )

            if success:
                await state_machine.broadcast_event("dsp", "filter_added", {
                    "id": filter_id,
                    "freq": payload.freq,
                    "gain": payload.gain,
                    "q": payload.q,
                    "type": payload.filter_type
                })
                return {"status": "success", "id": filter_id}

            return {"status": "error", "message": "Failed to add filter"}

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.put("/filter/{filter_id}")
    async def update_filter(filter_id: str, payload: DspFilterUpdateRequest):
        """Update an existing filter band"""
        try:
            # Get current filter to merge with updates
            filters = await dsp_service.get_filters()
            current_filter = next((f for f in filters if f["id"] == filter_id), None)

            if not current_filter:
                raise HTTPException(status_code=404, detail=f"Filter {filter_id} not found")

            # Merge current values with updates
            freq = payload.freq if payload.freq is not None else current_filter["freq"]
            gain = payload.gain if payload.gain is not None else current_filter["gain"]
            q = payload.q if payload.q is not None else current_filter.get("q", 1.0)
            filter_type = payload.filter_type if payload.filter_type is not None else current_filter.get("type", "Peaking")
            enabled = payload.enabled if payload.enabled is not None else current_filter.get("enabled", True)

            success = await dsp_service.set_filter(
                filter_id=filter_id,
                freq=freq,
                gain=gain,
                q=q,
                filter_type=filter_type,
                enabled=enabled
            )

            if success:
                await state_machine.broadcast_event("dsp", "filter_changed", {
                    "id": filter_id,
                    "freq": freq,
                    "gain": gain,
                    "q": q,
                    "type": filter_type
                })
                return {
                    "status": "success",
                    "id": filter_id,
                    "freq": freq,
                    "gain": gain,
                    "q": q,
                    "type": filter_type
                }

            return {"status": "error", "message": "Failed to update filter"}

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete("/filter/{filter_id}")
    async def delete_filter(filter_id: str):
        """Remove a filter band"""
        try:
            success = await dsp_service.remove_filter(filter_id)

            if success:
                await state_machine.broadcast_event("dsp", "filter_removed", {"id": filter_id})
                return {"status": "success", "id": filter_id}

            raise HTTPException(status_code=404, detail=f"Filter {filter_id} not found")

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/reset")
    async def reset_all_filters():
        """Reset all filters to flat (0 dB gain)"""
        try:
            success = await dsp_service.reset_filters()

            if success:
                # Note: filters_reset event is already broadcast by dsp_service.reset_filters()
                return {"status": "success", "message": "All filters reset to flat"}

            return {"status": "error", "message": "Failed to reset filters"}

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # === Preset Management ===

    @router.get("/presets")
    async def get_presets():
        """Get all builtin presets with their gains, manual gains, and active preset ID."""
        try:
            presets = dsp_service.get_presets()
            active_preset = await dsp_service.get_active_preset()
            manual_gains = await dsp_service.get_manual_gains()
            return {
                "presets": presets,
                "manual_gains": manual_gains,
                "active_preset": active_preset
            }
        except Exception as e:
            return {"presets": [], "manual_gains": [0]*10, "active_preset": None, "error": str(e)}

    @router.put("/preset/{preset_id}")
    async def load_preset(preset_id: str):
        """Load a builtin preset by ID."""
        try:
            success = await dsp_service.load_preset(preset_id)

            if success:
                # Note: preset_loaded event is already broadcast by dsp_service.load_preset()
                return {"status": "success", "id": preset_id}

            raise HTTPException(status_code=404, detail=f"Preset '{preset_id}' not found")

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/zone/{zone_id}/preset")
    async def load_preset_for_zone(zone_id: str, payload: DspPresetRequest):
        """
        Load a preset for all clients in a zone.

        Applies the preset to all ONLINE zone members. OFFLINE clients will
        receive settings on reconnection via sync service.
        """
        try:
            success = await multiroom_dsp_service.load_zone_preset(zone_id, payload.preset_id)
            return {
                "status": "success" if success else "error",
                "zone_id": zone_id,
                "preset_id": payload.preset_id
            }
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            logger.error(f"Error loading preset for zone {zone_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.patch("/zone/{zone_id}/filter/{filter_id}")
    async def update_zone_filter(zone_id: str, filter_id: str, payload: DspFilterUpdateRequest):
        """
        Update a filter for all clients in a zone.

        Applies the filter change to all ONLINE zone members. OFFLINE clients will
        receive settings on reconnection via sync service.
        """
        try:
            success = await multiroom_dsp_service.update_filter(
                target_type="zone",
                target_id=zone_id,
                filter_id=filter_id,
                frequency=payload.freq,
                gain=payload.gain,
                q=payload.q,
                filter_type=payload.filter_type,
                enabled=payload.enabled
            )
            return {
                "status": "success" if success else "error",
                "zone_id": zone_id,
                "filter_id": filter_id
            }
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            logger.error(f"Error updating filter for zone {zone_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.patch("/zone/{zone_id}/compressor")
    async def update_zone_compressor(zone_id: str, payload: DspCompressorRequest):
        """
        Update compressor settings for all clients in a zone.

        Applies compressor changes to all ONLINE zone members. OFFLINE clients will
        receive settings on reconnection via sync service.
        """
        try:
            success = await multiroom_dsp_service.update_compressor(
                target_type="zone",
                target_id=zone_id,
                enabled=payload.enabled,
                threshold=payload.threshold,
                ratio=payload.ratio,
                attack=payload.attack,
                release=payload.release,
                makeup_gain=payload.makeup_gain
            )
            return {"status": "success" if success else "error", "zone_id": zone_id}
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            logger.error(f"Error updating compressor for zone {zone_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.patch("/zone/{zone_id}/loudness")
    async def update_zone_loudness(zone_id: str, payload: DspLoudnessRequest):
        """
        Update loudness settings for all clients in a zone.

        Applies loudness changes to all ONLINE zone members. OFFLINE clients will
        receive settings on reconnection via sync service.
        """
        try:
            success = await multiroom_dsp_service.update_loudness(
                target_type="zone",
                target_id=zone_id,
                enabled=payload.enabled,
                high_boost=payload.high_boost,
                low_boost=payload.low_boost
            )
            return {"status": "success" if success else "error", "zone_id": zone_id}
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            logger.error(f"Error updating loudness for zone {zone_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.patch("/zone/{zone_id}/enabled")
    async def update_zone_dsp_enabled(zone_id: str, request: Request):
        """
        Enable/disable DSP effects for all clients in a zone.

        When disabled, DSP effects (EQ, compressor, loudness) are bypassed but
        volume control remains active. Crossover filters are NOT affected.
        OFFLINE clients will receive settings on reconnection via sync service.
        """
        try:
            body = await request.json()
            enabled = body.get("enabled")

            if enabled is None:
                raise HTTPException(status_code=400, detail="'enabled' field is required")

            success = await multiroom_dsp_service.set_zone_dsp_effects_enabled(zone_id, enabled)
            return {
                "status": "success" if success else "error",
                "zone_id": zone_id,
                "enabled": enabled
            }
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            logger.error(f"Error updating DSP enabled for zone {zone_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/client/{mac_id}/preset")
    async def load_preset_for_client(mac_id: str, payload: DspPresetRequest):
        """
        Load a preset for a standalone client by MAC ID.

        Applies the preset to the client. For zone clients, use the zone preset endpoint.
        """
        try:
            success = await multiroom_dsp_service.load_client_preset(mac_id, payload.preset_id)
            return {
                "status": "success" if success else "error",
                "client_id": mac_id,
                "preset_id": payload.preset_id
            }
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            logger.error(f"Error loading preset for client {mac_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # === Mute Control ===
    # Note: Volume control is handled by /api/volume/* endpoints.
    # Use /api/volume/set for volume changes.

    @router.put("/mute")
    async def set_mute(payload: DspMuteRequest):
        """
        Mute/unmute local CamillaDSP.

        In multiroom mode, this only mutes the local client without affecting others.
        """
        # Update local DSP hardware
        result = await dsp_service.set_mute(payload.muted)

        if not result:
            raise HTTPException(status_code=500, detail="Failed to set mute")

        # Update volume state store
        if state_machine:
            volume_service = getattr(state_machine, 'volume_service', None)
            if volume_service:
                await volume_service.set_client_mute('local', payload.muted, broadcast=True)

        return {"status": "success", "mute": payload.muted}

    # === Compressor ===

    @router.get("/compressor")
    async def get_compressor():
        """Get compressor settings"""
        try:
            compressor = await dsp_service.get_compressor()
            return compressor
        except Exception as e:
            return {"enabled": False, "error": str(e)}

    @router.put("/compressor")
    async def set_compressor(payload: DspCompressorRequest):
        """Update compressor settings"""
        try:
            success = await dsp_service.set_compressor(
                enabled=payload.enabled,
                threshold=payload.threshold,
                ratio=payload.ratio,
                attack=payload.attack,
                release=payload.release,
                makeup_gain=payload.makeup_gain
            )

            if success:
                compressor = await dsp_service.get_compressor()
                # Note: WebSocket broadcast is handled by dsp_service.set_compressor()
                return {"status": "success", **compressor}

            return {"status": "error", "message": "Failed to update compressor"}

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # === Loudness Compensation ===

    @router.get("/loudness")
    async def get_loudness():
        """Get loudness compensation settings"""
        try:
            loudness = await dsp_service.get_loudness()
            return loudness
        except Exception as e:
            return {"enabled": False, "error": str(e)}

    @router.put("/loudness")
    async def set_loudness(payload: DspLoudnessRequest):
        """Update loudness compensation settings"""
        try:
            success = await dsp_service.set_loudness(
                enabled=payload.enabled,
                high_boost=payload.high_boost,
                low_boost=payload.low_boost
            )

            if success:
                loudness = await dsp_service.get_loudness()
                # Note: WebSocket broadcast is handled by dsp_service.set_loudness()
                return {"status": "success", **loudness}

            return {"status": "error", "message": "Failed to update loudness"}

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # === Configuration Persistence ===

    @router.post("/save")
    async def save_configuration():
        """Save current configuration to disk"""
        try:
            success = await dsp_service.save_current_config()

            if success:
                return {"status": "success", "message": "Configuration saved"}

            return {"status": "error", "message": "Failed to save configuration"}

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # === Multi-client DSP Support ===

    @router.get("/targets")
    async def get_dsp_targets():
        """Get available DSP targets (local Milo + remote clients)"""
        try:
            targets = []

            # Get all clients from Snapcast (includes local Milo with custom name)
            try:
                from backend.dependencies import get_service
                snapcast_svc = get_service("snapcast_service")
                clients = await snapcast_svc.get_clients()

                for client in clients:
                    hostname = client.get("host", "")
                    ip = client.get("ip", "")
                    dsp_id = client.get("dsp_id", ip)
                    client_name = client.get("name", hostname)

                    # Local Milo (main device)
                    if hostname == "milo":
                        targets.insert(0, {  # Insert first
                            "id": "local",
                            "name": client_name,  # Use custom name from Snapcast
                            "host": "local",
                            "available": True
                        })
                    else:
                        # Remote client - check if DSP available via proxy
                        proxy_target = hostname if hostname.startswith("milo-client") else ip
                        available = await proxy_service.check_available(proxy_target) if proxy_service else False

                        targets.append({
                            "id": dsp_id,
                            "name": client_name,
                            "host": hostname,
                            "ip": ip,
                            "available": available
                        })
            except Exception as e:
                logger.warning(f"Error getting multiroom clients for DSP: {e}")
                # Fallback to hardcoded local if Snapcast fails
                if not targets:
                    targets = [{"id": "local", "name": "Milo", "host": "local", "available": True}]

            return {"targets": targets}

        except Exception as e:
            logger.error(f"Error getting DSP targets: {e}")
            return {"targets": [{"id": "local", "name": "Milo", "host": "local", "available": True}]}

    # === Speaker Type / Crossover Management ===
    # Note: Zone CRUD moved to /api/multiroom/zones, speaker-type to /api/multiroom/clients

    def _get_crossover_svc():
        from backend.dependencies import get_service
        return get_service("crossover_service")

    @router.get("/client/{client_id}/type")
    async def get_client_type(client_id: str):
        """Get client speaker type"""
        try:
            return {"client_id": client_id, **await _get_crossover_svc().get_client_type(client_id)}
        except Exception as e:
            logger.error(f"Error getting client type: {e}")
            return {"client_id": client_id, "speaker_type": "bookshelf"}

    # Note: PUT /client/{client_id}/speaker-type moved to PATCH /api/multiroom/clients/{mac_id}

    @router.put("/client/{client_id}/crossover-frequency")
    async def set_client_crossover_frequency(client_id: str, payload: dict):
        """Set custom crossover frequency for a client"""
        try:
            freq = payload.get("frequency")
            if freq is None:
                raise HTTPException(status_code=400, detail="frequency is required")
            cs = _get_crossover_svc()
            if not await cs.set_client_crossover_frequency(client_id, float(freq)):
                raise HTTPException(status_code=500, detail="Failed to update crossover frequency")
            ct = await cs.get_client_type(client_id)
            return {"status": "success", "client_id": client_id, "speaker_type": ct.get("speaker_type"),
                    "crossover_frequency": ct.get("crossover_frequency")}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error setting client crossover frequency: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/client-types")
    async def get_all_client_types():
        """Get all client type configurations"""
        try:
            return {"client_types": await _get_crossover_svc().get_all_client_types()}
        except Exception as e:
            logger.error(f"Error getting client types: {e}")
            return {"client_types": {}}

    @router.get("/links/{zone_id}/crossover")
    async def get_zone_crossover(zone_id: str):
        """Get crossover settings for a zone"""
        try:
            return {"zone_id": zone_id, **await _get_crossover_svc().get_zone_crossover(zone_id)}
        except Exception as e:
            logger.error(f"Error getting zone crossover: {e}")
            return {"zone_id": zone_id, "frequency": 80, "enabled": False, "has_subwoofer": False}

    @router.get("/links/{zone_id}/auto-crossover")
    async def get_zone_auto_crossover(zone_id: str):
        """Get automatic crossover frequency for a zone (MIN of speaker frequencies)"""
        try:
            return {"zone_id": zone_id, "frequency": await _get_crossover_svc().get_zone_auto_crossover(zone_id)}
        except Exception as e:
            logger.error(f"Error getting zone auto crossover: {e}")
            return {"zone_id": zone_id, "frequency": 80}

    @router.put("/links/{zone_id}/crossover")
    async def set_zone_crossover(zone_id: str, payload: ZoneCrossoverRequest):
        """Set crossover frequency for a zone"""
        try:
            cs = _get_crossover_svc()
            if not await cs.set_zone_crossover_frequency(zone_id, payload.frequency):
                raise HTTPException(status_code=500, detail="Failed to update zone crossover")
            return {"status": "success", "zone_id": zone_id, **await cs.get_zone_crossover(zone_id)}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error setting zone crossover: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/links/{zone_id}/crossover/apply")
    async def apply_zone_crossover(zone_id: str):
        """Manually apply crossover settings to all clients in a zone"""
        try:
            if not await _get_crossover_svc().apply_zone_crossover(zone_id):
                raise HTTPException(status_code=500, detail="Failed to apply crossover")
            return {"status": "success", "message": f"Crossover applied to zone {zone_id}"}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error applying zone crossover: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/crossover")
    async def get_local_crossover():
        """Get local crossover filter settings"""
        try:
            crossover = await dsp_service.get_crossover_filter()
            return crossover
        except Exception as e:
            logger.error(f"Error getting local crossover: {e}")
            return {"enabled": False, "frequency": 80, "q": 0.707}

    @router.put("/crossover")
    async def set_local_crossover(payload: CrossoverFilterRequest):
        """Set local crossover filter (direct control)"""
        try:
            success = await dsp_service.set_crossover_filter(
                enabled=payload.enabled,
                frequency=payload.frequency,
                q=payload.q
            )

            if success:
                crossover = await dsp_service.get_crossover_filter()
                return {"status": "success", **crossover}

            raise HTTPException(status_code=500, detail="Failed to update crossover")

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error setting local crossover: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # === Client DSP Proxy Routes ===

    @router.get("/client/{hostname}/status")
    async def get_client_dsp_status(hostname: str):
        """Get DSP status for a specific client with consistent volume."""
        # Get base status via DspRouter
        status = await dsp_router_service.get_status(hostname)

        # Inject volume from volume_service (source of truth)
        if state_machine:
            volume_service = getattr(state_machine, 'volume_service', None)
            if volume_service:
                vol = await volume_service.get_client_volume(hostname)
                if 'volume' not in status:
                    status['volume'] = {}
                status['volume']['main'] = vol['main']
                status['volume']['mute'] = vol['mute']

        return status

    @router.get("/client/{hostname}/filters")
    async def get_client_dsp_filters(hostname: str):
        """Proxy DSP filters request to client"""
        return await dsp_router_service.get_filters(hostname)

    @router.put("/client/{hostname}/filter/{filter_id}")
    async def update_client_dsp_filter(hostname: str, filter_id: str, request: Request):
        """Proxy filter update to client and persist settings"""
        body = await request.json()
        result = await dsp_router_service.update_filter(hostname, filter_id, body)

        if result.get("status") == "success":
            if dsp_router_service.is_local_client(hostname):
                # Local: return request body with status
                return {"status": "success", "id": filter_id, **body}
            # Remote: persist to sync_service
            if sync_service:
                settings = await sync_service.load_settings()
                settings.setdefault(hostname, {}).setdefault("filters", {})[filter_id] = body
                await sync_service.save_settings(settings)

        return result

    @router.post("/client/{hostname}/reset")
    async def reset_client_dsp_filters(hostname: str):
        """Proxy filter reset to client and clear saved filter settings"""
        result = await dsp_router_service.reset_filters(hostname)

        if result.get("status") == "success":
            # Remote: clear saved filter settings
            if not dsp_router_service.is_local_client(hostname) and sync_service:
                settings = await sync_service.load_settings()
                if hostname in settings:
                    settings[hostname]["filters"] = {}
                    await sync_service.save_settings(settings)

        return result

    @router.get("/client/{hostname}/compressor")
    async def get_client_compressor(hostname: str):
        """Proxy compressor GET to client"""
        return await dsp_router_service.get_compressor(hostname)

    @router.put("/client/{hostname}/compressor")
    async def update_client_compressor(hostname: str, request: Request):
        """Proxy compressor update to client and persist settings"""
        body = await request.json()
        result = await dsp_router_service.set_compressor(hostname, body)

        if result.get("status") == "success":
            if dsp_router_service.is_local_client(hostname):
                # Local: return full compressor state
                compressor = await dsp_service.get_compressor()
                return {"status": "success", **compressor}
            # Remote: persist to sync_service
            if sync_service:
                await sync_service.update_client_settings(hostname, "compressor", {k: v for k, v in result.items() if k != "status"})

        return result

    @router.get("/client/{hostname}/loudness")
    async def get_client_loudness(hostname: str):
        """Proxy loudness GET to client"""
        return await dsp_router_service.get_loudness(hostname)

    @router.put("/client/{hostname}/loudness")
    async def update_client_loudness(hostname: str, request: Request):
        """Proxy loudness update to client and persist settings"""
        body = await request.json()
        result = await dsp_router_service.set_loudness(hostname, body)

        if result.get("status") == "success":
            if dsp_router_service.is_local_client(hostname):
                # Local: return full loudness state
                loudness = await dsp_service.get_loudness()
                return {"status": "success", **loudness}
            # Remote: persist to sync_service
            if sync_service:
                await sync_service.update_client_settings(hostname, "loudness", {k: v for k, v in result.items() if k != "status"})

        return result

    @router.get("/client/{hostname}/enabled")
    async def get_client_dsp_enabled(hostname: str):
        """Get DSP effects enabled state for a specific client"""
        return await dsp_router_service.get_dsp_enabled(hostname, routing_service)

    @router.put("/client/{hostname}/enabled")
    async def update_client_dsp_enabled(hostname: str, request: Request):
        """Set DSP effects enabled state for a specific client (local or remote).

        When enabled=False, DSP effects (EQ, compressor, loudness) are bypassed.
        Volume control remains active regardless of this setting.
        """
        body = await request.json()
        enabled = body.get("enabled")
        result = await dsp_router_service.set_dsp_enabled(hostname, enabled, routing_service)

        if result.get("status") == "success":
            # Remote: persist to sync_service
            if not dsp_router_service.is_local_client(hostname) and sync_service:
                await sync_service.update_client_settings(hostname, "enabled", {"enabled": enabled})

        return result

    @router.get("/client/{hostname}/volume")
    async def get_client_volume(hostname: str):
        """Get volume for a specific client (consistent with multiroom model)."""
        # Prefer volume_service as source of truth
        if state_machine:
            vs = getattr(state_machine, 'volume_service', None)
            if vs:
                return await vs.get_client_volume(hostname)
        # Fallback to DspRouter
        return await dsp_router_service.get_volume(hostname)

    @router.put("/client/{hostname}/volume")
    async def update_client_volume(hostname: str, request: Request):
        """Set volume for a specific client (local or remote)."""
        body = await request.json()
        volume_db = body.get("volume")

        if dsp_router_service.is_local_client(hostname):
            vs = _get_volume_service()
            await vs.update_client_volume_db(hostname, volume_db, broadcast=True)
            return {"status": "success", "main": volume_db}

        # Remote client via DspRouter
        result = await dsp_router_service.set_volume(hostname, volume_db)

        if result.get("status") == "success":
            if sync_service:
                await sync_service.update_client_settings(hostname, "volume", {k: v for k, v in result.items() if k != "status"})
            actual = result.get("main", result.get("volume", volume_db))
            if actual is not None:
                vs = getattr(state_machine, 'volume_service', None) if state_machine else None
                if vs:
                    await vs.update_client_volume_db(hostname, actual)
                    if actual != volume_db:
                        logger.info(f"Client {hostname} volume clamped: {volume_db} -> {actual} dB")
        return result

    @router.put("/client/{hostname}/mute")
    async def update_client_mute(hostname: str, request: Request):
        """Set mute for a specific client (local or remote)."""
        body = await request.json()
        muted = body.get("muted")

        if dsp_router_service.is_local_client(hostname):
            vs = _get_volume_service()
            if not await dsp_service.set_mute(muted):
                raise HTTPException(status_code=500, detail="Failed to set local mute")
            await vs.set_client_mute(hostname, muted, broadcast=True)
            return {"status": "success", "mute": muted}

        # Remote client via DspRouter
        result = await dsp_router_service.set_mute(hostname, muted)

        if result.get("status") == "success":
            vs = getattr(state_machine, 'volume_service', None) if state_machine else None
            if vs:
                await vs.set_client_mute(hostname, muted, broadcast=True)
        return result

    # === Client Settings Persistence ===

    @router.get("/client/{hostname}/saved-settings")
    async def get_client_saved_settings(hostname: str):
        """Get Milo's saved DSP settings for a client"""
        if not sync_service:
            return {"hostname": hostname, "settings": {}}
        settings = await sync_service.get_client_settings(hostname)
        return {"hostname": hostname, "settings": settings}

    @router.post("/client/{hostname}/restore")
    async def restore_client_settings(hostname: str):
        """Restore saved DSP settings to a client"""
        if not sync_service or not proxy_service:
            return {"status": "error", "restored": [], "errors": ["Services not available"]}

        # Local client: settings are applied directly via dsp_service
        if dsp_router_service.is_local_client(hostname):
            return {"status": "skipped", "message": "Local client settings are managed directly", "restored": []}

        # Get IP for remote client
        client_ip = _get_client_ip(hostname)
        if not client_ip:
            return {"status": "error", "restored": [], "errors": [f"Client {hostname} not found or offline"]}

        saved = await sync_service.get_client_settings(hostname)
        if not saved:
            return {"status": "success", "message": "No saved settings to restore", "restored": []}

        restored, errors = [], []

        async def try_restore(name: str, path: str, data):
            try:
                await proxy_service.request(client_ip, "PUT", path, data)
                restored.append(name)
            except Exception as e:
                errors.append(f"{name}: {e}")

        if "compressor" in saved:
            await try_restore("compressor", "/dsp/compressor", saved["compressor"])
        if "loudness" in saved:
            await try_restore("loudness", "/dsp/loudness", saved["loudness"])
        for fid, fdata in saved.get("filters", {}).items():
            # Transform saved filter data to match DspFilterUpdateRequest schema:
            # - Remove 'id' (it's in the URL)
            # - Rename 'type' to 'filter_type' (Pydantic model uses filter_type)
            filter_payload = {k: v for k, v in fdata.items() if k != "id"}
            if "type" in filter_payload:
                filter_payload["filter_type"] = filter_payload.pop("type")
            await try_restore(f"filter:{fid}", f"/dsp/filter/{fid}", filter_payload)
        if "main" in saved.get("volume", {}):
            await try_restore("volume", "/dsp/volume", {"volume": saved["volume"]["main"]})
        if "mute" in saved.get("volume", {}):
            await try_restore("mute", "/dsp/mute", {"muted": saved["volume"]["mute"]})

        logger.info(f"Restored settings for {hostname}: {restored}")
        if errors:
            logger.warning(f"Errors restoring settings for {hostname}: {errors}")
        return {"status": "success" if not errors else "partial", "restored": restored, "errors": errors or None}

    return router
