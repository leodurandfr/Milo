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
    DspLinkedClientsRequest,
    ClientSpeakerTypeRequest,
    ZoneCrossoverRequest,
    CrossoverFilterRequest
)
from backend.core.multiroom.snapcast import normalize_client_id

logger = logging.getLogger(__name__)


def create_dsp_router(
    dsp_service,
    state_machine,
    settings_service=None,
    routing_service=None,
    crossover_service=None,
    proxy_service=None,
    sync_service=None,
    client_registry_service=None
):
    """Creates DSP router with injected dependencies"""
    router = APIRouter(prefix="/api/dsp", tags=["dsp"])

    # === Internal Helpers ===

    def _require_proxy():
        """Raise 503 if proxy_service unavailable."""
        if not proxy_service:
            raise HTTPException(status_code=503, detail="Proxy service not available")

    def _require_registry():
        """Raise 500 if client_registry_service unavailable."""
        if not client_registry_service:
            raise HTTPException(status_code=500, detail="Registry service not available")

    def _get_volume_service():
        """Get volume_service from state_machine or raise 500."""
        if state_machine:
            vs = getattr(state_machine, 'volume_service', None)
            if vs:
                return vs
        raise HTTPException(status_code=500, detail="Volume service not available")

    async def _check_client_or_skip(hostname: str, action: str):
        """Check client availability, return skip response if unavailable, None otherwise."""
        if proxy_service and not await proxy_service.check_available(hostname):
            logger.warning(f"Client {hostname} is not available, skipping {action}")
            return {"status": "skipped", "reason": "client_unavailable"}
        return None

    async def _broadcast_links():
        """Broadcast links_changed event with current zones."""
        zones = client_registry_service.get_all_zones()
        linked_groups = [z.to_dict() for z in zones.values()]
        await state_machine.broadcast_event("dsp", "links_changed", {"linked_groups": linked_groups})
        return linked_groups

    # === DSP Enable/Disable ===

    @router.get("/enabled")
    async def get_dsp_effects_enabled():
        """Get DSP effects enabled state from settings (EQ, compressor, loudness)"""
        try:
            if settings_service:
                # Support both new and legacy setting key
                enabled = await settings_service.get_setting("dsp.effects_enabled")
                if enabled is None:
                    enabled = await settings_service.get_setting("dsp.enabled")
                # Default to True if not set
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

            # Use routing_service to properly toggle DSP effects
            if routing_service:
                success = await routing_service.set_dsp_effects_enabled(enabled, active_source)
                if success:
                    logger.info(f"DSP effects enabled state set to: {enabled}")
                    return {"status": "success", "enabled": enabled}
                else:
                    return {"status": "error", "message": "Failed to change DSP effects state"}

            # Fallback: just save setting if no routing_service (should not happen)
            if settings_service:
                await settings_service.set_setting("dsp.effects_enabled", enabled)
                logger.info(f"DSP effects enabled state set to: {enabled} (fallback, no routing_service)")
                await state_machine.broadcast_event("dsp", "enabled_changed", {"enabled": enabled})
                return {"status": "success", "enabled": enabled}

            return {"status": "error", "message": "Settings service not available"}
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
            """Get levels from a single client."""
            normalized = normalize_client_id(client_id)
            if normalized == 'local':
                try:
                    return await dsp_service.get_levels()
                except Exception as e:
                    logger.debug(f"Failed to get local DSP levels: {e}")
                    return None
            else:
                # Proxy to remote client using proxy_service
                if proxy_service:
                    return await proxy_service.get_dsp_levels(client_id)
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
                await state_machine.broadcast_event("dsp", "compressor_changed", compressor)
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
                reference_level=payload.reference_level,
                high_boost=payload.high_boost,
                low_boost=payload.low_boost
            )

            if success:
                loudness = await dsp_service.get_loudness()
                await state_machine.broadcast_event("dsp", "loudness_changed", loudness)
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

    # === Linked Clients Management ===

    @router.get("/links")
    async def get_linked_clients():
        """Get all linked client groups (zones) from registry"""
        try:
            if client_registry_service:
                zones = client_registry_service.get_all_zones()
                return {"linked_groups": [z.to_dict() for z in zones.values()]}
            return {"linked_groups": []}
        except Exception as e:
            logger.error(f"Error getting linked clients: {e}")
            return {"linked_groups": []}

    @router.post("/links")
    async def create_link_group(payload: DspLinkedClientsRequest):
        """Create or update a linked client group (zone) via registry"""
        try:
            _require_registry()
            source_client = payload.source_client or payload.client_ids[0]
            all_clients = payload.client_ids
            new_client_ids = set(payload.client_ids)

            # Helper to sync settings
            async def do_sync(target_clients):
                if not sync_service:
                    return {"synced": [], "errors": ["Sync service not available"]}
                if client_registry_service.get_client(source_client):
                    return await sync_service.sync_settings(source_client, target_clients)
                return {"synced": [], "errors": [f"Source client '{source_client}' not available"]}

            # Check for overlap with existing zones
            for zone in client_registry_service.get_all_zones().values():
                if new_client_ids & set(zone.client_ids):
                    # Merge with existing zone
                    for cid in new_client_ids - set(zone.client_ids):
                        await client_registry_service.add_client_to_zone(zone.id, cid)
                    if payload.name:
                        await client_registry_service.update_zone(zone.id, name=payload.name)
                    updated = client_registry_service.get_zone(zone.id)
                    all_clients = updated.client_ids if updated else list(set(zone.client_ids) | new_client_ids)
                    linked_groups = await _broadcast_links()
                    return {"status": "success", "message": "Merged with existing group",
                            "linked_groups": linked_groups, "sync": await do_sync(all_clients)}

            # Create new zone
            import uuid
            await client_registry_service.create_zone(
                zone_id=f"zone_{uuid.uuid4().hex[:8]}",
                name=payload.name or "",
                client_ids=payload.client_ids
            )
            linked_groups = await _broadcast_links()
            return {"status": "success", "linked_groups": linked_groups, "sync": await do_sync(all_clients)}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error creating link group: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete("/links/{client_id}")
    async def unlink_client(client_id: str):
        """Remove a client from its linked group (zone) via registry"""
        try:
            _require_registry()
            zone = client_registry_service.get_zone_for_client(client_id)
            if not zone:
                raise HTTPException(status_code=404, detail=f"Client {client_id} not found in any linked group")

            await client_registry_service.remove_client_from_zone(zone.id, client_id)
            # Delete zone if fewer than 2 clients remain (zone.client_ids already updated in-place)
            if len(zone.client_ids) < 2:
                await client_registry_service.delete_zone(zone.id)

            return {"status": "success", "linked_groups": await _broadcast_links()}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error unlinking client: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete("/links")
    async def clear_all_links():
        """Remove all linked client groups (zones) via registry"""
        try:
            _require_registry()
            for zone_id in list(client_registry_service.get_all_zones().keys()):
                await client_registry_service.delete_zone(zone_id)
            await state_machine.broadcast_event("dsp", "links_changed", {"linked_groups": []})
            return {"status": "success", "linked_groups": []}
        except Exception as e:
            logger.error(f"Error clearing links: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete("/links/group/{group_id}")
    async def delete_link_group(group_id: str):
        """Delete an entire linked client group (zone) via registry"""
        try:
            _require_registry()
            if not client_registry_service.get_zone(group_id):
                raise HTTPException(status_code=404, detail=f"Group {group_id} not found")
            await client_registry_service.delete_zone(group_id)
            return {"status": "success", "linked_groups": await _broadcast_links()}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error deleting link group: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.put("/links/{group_id}/name")
    async def update_link_group_name(group_id: str, request: Request):
        """Update the name of a linked client group (zone) via registry"""
        try:
            _require_registry()
            body = await request.json()
            if not await client_registry_service.update_zone(group_id, name=body.get("name", "")):
                raise HTTPException(status_code=404, detail=f"Group {group_id} not found")
            return {"status": "success", "linked_groups": await _broadcast_links()}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating link group name: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # === Speaker Type / Crossover Management ===

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

    @router.put("/client/{client_id}/speaker-type")
    async def set_client_speaker_type(client_id: str, payload: ClientSpeakerTypeRequest):
        """Set client speaker type (satellite, bookshelf, tower, subwoofer)"""
        try:
            cs = _get_crossover_svc()
            if not await cs.set_client_speaker_type(client_id, payload.speaker_type):
                raise HTTPException(status_code=500, detail="Failed to update client speaker type")
            ct = await cs.get_client_type(client_id)
            return {"status": "success", "client_id": client_id, "speaker_type": payload.speaker_type,
                    "crossover_frequency": ct.get("crossover_frequency")}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error setting client speaker type: {e}")
            raise HTTPException(status_code=500, detail=str(e))

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

    @router.get("/links/{group_id}/crossover")
    async def get_zone_crossover(group_id: str):
        """Get crossover settings for a zone"""
        try:
            return {"zone_id": group_id, **await _get_crossover_svc().get_zone_crossover(group_id)}
        except Exception as e:
            logger.error(f"Error getting zone crossover: {e}")
            return {"zone_id": group_id, "frequency": 80, "enabled": False, "has_subwoofer": False}

    @router.get("/links/{group_id}/auto-crossover")
    async def get_zone_auto_crossover(group_id: str):
        """Get automatic crossover frequency for a zone (MIN of speaker frequencies)"""
        try:
            return {"zone_id": group_id, "frequency": await _get_crossover_svc().get_zone_auto_crossover(group_id)}
        except Exception as e:
            logger.error(f"Error getting zone auto crossover: {e}")
            return {"zone_id": group_id, "frequency": 80}

    @router.put("/links/{group_id}/crossover")
    async def set_zone_crossover(group_id: str, payload: ZoneCrossoverRequest):
        """Set crossover frequency for a zone"""
        try:
            cs = _get_crossover_svc()
            if not await cs.set_zone_crossover_frequency(group_id, payload.frequency):
                raise HTTPException(status_code=500, detail="Failed to update zone crossover")
            return {"status": "success", "zone_id": group_id, **await cs.get_zone_crossover(group_id)}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error setting zone crossover: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/links/{group_id}/crossover/apply")
    async def apply_zone_crossover(group_id: str):
        """Manually apply crossover settings to all clients in a zone"""
        try:
            if not await _get_crossover_svc().apply_zone_crossover(group_id):
                raise HTTPException(status_code=500, detail="Failed to apply crossover")
            return {"status": "success", "message": f"Crossover applied to zone {group_id}"}
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
        normalized = normalize_client_id(hostname)

        # Get base status
        if normalized == 'local':
            status = await dsp_service.get_status()
        else:
            if not proxy_service:
                raise HTTPException(status_code=503, detail="Proxy service not available")
            status = await proxy_service.request(hostname, "GET", "/dsp/status")

        # Inject volume from volume_service (source of truth)
        if state_machine:
            volume_service = getattr(state_machine, 'volume_service', None)
            if volume_service:
                vol = await volume_service.get_client_volume(normalized)
                if 'volume' not in status:
                    status['volume'] = {}
                status['volume']['main'] = vol['main']
                status['volume']['mute'] = vol['mute']

        return status

    @router.get("/client/{hostname}/filters")
    async def get_client_dsp_filters(hostname: str):
        """Proxy DSP filters request to client"""
        _require_proxy()
        return await proxy_service.request(hostname, "GET", "/dsp/filters")

    @router.put("/client/{hostname}/filter/{filter_id}")
    async def update_client_dsp_filter(hostname: str, filter_id: str, request: Request):
        """Proxy filter update to client and persist settings"""
        _require_proxy()
        body = await request.json()
        skip = await _check_client_or_skip(hostname, "filter update")
        if skip:
            return {**skip, "id": filter_id, **body}

        result = await proxy_service.request(hostname, "PUT", f"/dsp/filter/{filter_id}", body)
        if result.get("status") == "success" and sync_service:
            settings = await sync_service.load_settings()
            # Save the request body (contains full filter data) instead of response
            # milo-client only returns {"status": "success", "filter_id": ...}
            settings.setdefault(hostname, {}).setdefault("filters", {})[filter_id] = body
            await sync_service.save_settings(settings)
        return result

    @router.post("/client/{hostname}/reset")
    async def reset_client_dsp_filters(hostname: str):
        """Proxy filter reset to client and clear saved filter settings"""
        _require_proxy()
        skip = await _check_client_or_skip(hostname, "filter reset")
        if skip:
            return skip

        result = await proxy_service.request(hostname, "POST", "/dsp/reset")
        if result.get("status") == "success" and sync_service:
            settings = await sync_service.load_settings()
            if hostname in settings:
                settings[hostname]["filters"] = {}
                await sync_service.save_settings(settings)
        return result

    @router.get("/client/{hostname}/compressor")
    async def get_client_compressor(hostname: str):
        """Proxy compressor GET to client"""
        _require_proxy()
        return await proxy_service.request(hostname, "GET", "/dsp/compressor")

    @router.put("/client/{hostname}/compressor")
    async def update_client_compressor(hostname: str, request: Request):
        """Proxy compressor update to client and persist settings"""
        _require_proxy()
        body = await request.json()
        skip = await _check_client_or_skip(hostname, "compressor update")
        if skip:
            return {**skip, **body}

        result = await proxy_service.request(hostname, "PUT", "/dsp/compressor", body)
        if result.get("status") == "success" and sync_service:
            await sync_service.update_client_settings(hostname, "compressor", {k: v for k, v in result.items() if k != "status"})
        return result

    @router.get("/client/{hostname}/loudness")
    async def get_client_loudness(hostname: str):
        """Proxy loudness GET to client"""
        _require_proxy()
        return await proxy_service.request(hostname, "GET", "/dsp/loudness")

    @router.put("/client/{hostname}/loudness")
    async def update_client_loudness(hostname: str, request: Request):
        """Proxy loudness update to client and persist settings"""
        _require_proxy()
        body = await request.json()
        skip = await _check_client_or_skip(hostname, "loudness update")
        if skip:
            return {**skip, **body}

        result = await proxy_service.request(hostname, "PUT", "/dsp/loudness", body)
        if result.get("status") == "success" and sync_service:
            await sync_service.update_client_settings(hostname, "loudness", {k: v for k, v in result.items() if k != "status"})
        return result

    @router.get("/client/{hostname}/volume")
    async def get_client_volume(hostname: str):
        """Get volume for a specific client (consistent with multiroom model)."""
        normalized = normalize_client_id(hostname)
        if state_machine:
            vs = getattr(state_machine, 'volume_service', None)
            if vs:
                return await vs.get_client_volume(normalized)
        if normalized == 'local':
            try:
                vol = await dsp_service.get_volume()
                return {"main": vol.get("main", -30), "mute": vol.get("mute", False)}
            except Exception:
                return {"main": -30, "mute": False}
        _require_proxy()
        return await proxy_service.request(hostname, "GET", "/dsp/volume")

    @router.put("/client/{hostname}/volume")
    async def update_client_volume(hostname: str, request: Request):
        """Set volume for a specific client (local or remote)."""
        body = await request.json()
        volume_db = body.get("volume")
        normalized = normalize_client_id(hostname)

        if normalized == 'local':
            vs = _get_volume_service()
            await vs.update_client_volume_db('local', volume_db, broadcast=True)
            return {"status": "success", "main": volume_db}

        _require_proxy()
        if not await proxy_service.check_available(hostname):
            raise HTTPException(status_code=503, detail=f"Client {hostname} is not available")

        try:
            result = await proxy_service.request(hostname, "PUT", "/dsp/volume", body)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Cannot reach client {hostname}: {e}")

        if result.get("status") == "success":
            if sync_service:
                await sync_service.update_client_settings(hostname, "volume", {k: v for k, v in result.items() if k != "status"})
            actual = result.get("main", result.get("volume", volume_db))
            if actual is not None:
                vs = getattr(state_machine, 'volume_service', None) if state_machine else None
                if vs:
                    await vs.update_client_volume_db(normalized, actual)
                    if actual != volume_db:
                        logger.info(f"Client {hostname} volume clamped: {volume_db} -> {actual} dB")
        return result

    @router.put("/client/{hostname}/mute")
    async def update_client_mute(hostname: str, request: Request):
        """Set mute for a specific client (local or remote)."""
        body = await request.json()
        muted = body.get("muted")
        normalized = normalize_client_id(hostname)

        if normalized == 'local':
            vs = _get_volume_service()
            if not await dsp_service.set_mute(muted):
                raise HTTPException(status_code=500, detail="Failed to set local mute")
            await vs.set_client_mute('local', muted, broadcast=True)
            return {"status": "success", "mute": muted}

        _require_proxy()
        if not await proxy_service.check_available(hostname):
            raise HTTPException(status_code=503, detail=f"Client {hostname} is not available")

        try:
            result = await proxy_service.request(hostname, "PUT", "/dsp/mute", body)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Cannot reach client {hostname}: {e}")

        if result.get("status") == "success":
            vs = getattr(state_machine, 'volume_service', None) if state_machine else None
            if vs:
                await vs.set_client_mute(normalized, muted, broadcast=True)
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

        saved = await sync_service.get_client_settings(hostname)
        if not saved:
            return {"status": "success", "message": "No saved settings to restore", "restored": []}

        restored, errors = [], []

        async def try_restore(name: str, path: str, data):
            try:
                await proxy_service.request(hostname, "PUT", path, data)
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
