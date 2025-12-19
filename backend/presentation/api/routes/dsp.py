# backend/presentation/api/routes/dsp.py
"""
API routes for CamillaDSP digital signal processing
Full DSP capabilities including EQ, compressor, loudness, and volume control
Supports multi-client DSP control for multiroom setups
"""
import asyncio
import logging
from fastapi import APIRouter, HTTPException, Request

from backend.presentation.api.models import (
    DspFilterRequest,
    DspFilterUpdateRequest,
    DspPresetRequest,
    DspVolumeRequest,
    DspMuteRequest,
    DspCompressorRequest,
    DspLoudnessRequest,
    DspDelayRequest,
    DspLinkedClientsRequest,
    ClientSpeakerTypeRequest,
    ZoneCrossoverRequest,
    CrossoverFilterRequest
)

logger = logging.getLogger(__name__)


def create_dsp_router(
    dsp_service,
    state_machine,
    settings_service=None,
    routing_service=None,
    crossover_service=None,
    proxy_service=None,
    sync_service=None
):
    """Creates DSP router with injected dependencies"""
    router = APIRouter(prefix="/api/dsp", tags=["dsp"])

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
            normalized = 'local' if client_id in ('local', 'milo') else client_id
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
                await state_machine.broadcast_event("dsp", "filters_reset", {})
                return {"status": "success", "message": "All filters reset to flat"}

            return {"status": "error", "message": "Failed to reset filters"}

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # === Preset Management ===

    @router.get("/presets")
    async def list_presets():
        """List all available presets"""
        try:
            presets = await dsp_service.list_presets()
            return {"presets": presets}
        except Exception as e:
            return {"presets": [], "error": str(e)}

    @router.post("/preset")
    async def save_preset(payload: DspPresetRequest):
        """Save current configuration as a preset"""
        try:
            success = await dsp_service.save_preset(payload.name)

            if success:
                await state_machine.broadcast_event("dsp", "preset_saved", {"name": payload.name})
                return {"status": "success", "name": payload.name}

            return {"status": "error", "message": "Failed to save preset"}

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.put("/preset/{preset_name}")
    async def load_preset(preset_name: str):
        """Load a preset configuration"""
        try:
            success = await dsp_service.load_preset(preset_name)

            if success:
                await state_machine.broadcast_event("dsp", "preset_loaded", {"name": preset_name})
                return {"status": "success", "name": preset_name}

            raise HTTPException(status_code=404, detail=f"Preset '{preset_name}' not found")

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete("/preset/{preset_name}")
    async def delete_preset(preset_name: str):
        """Delete a preset"""
        try:
            success = await dsp_service.delete_preset(preset_name)

            if success:
                await state_machine.broadcast_event("dsp", "preset_deleted", {"name": preset_name})
                return {"status": "success", "name": preset_name}

            raise HTTPException(status_code=404, detail=f"Preset '{preset_name}' not found")

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # === Mute Control ===
    # Note: Volume control is handled by /api/volume/* endpoints.
    # Use /api/volume/set for volume changes.

    @router.put("/mute")
    async def set_mute(payload: DspMuteRequest):
        """Set DSP mute state"""
        try:
            success = await dsp_service.set_mute(payload.muted)

            if success:
                await state_machine.broadcast_event("dsp", "mute_changed", {"muted": payload.muted})

                # Update multiroom volume handler mute cache for 'local'
                if hasattr(state_machine, 'volume_service'):
                    volume_service = state_machine.volume_service
                    if hasattr(volume_service, '_multiroom_handler'):
                        volume_service._multiroom_handler.set_client_mute('local', payload.muted)

                return {"status": "success", "muted": payload.muted}

            return {"status": "error", "message": "Failed to set mute"}

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

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

    # === Channel Delay ===

    @router.get("/delay")
    async def get_delay():
        """Get channel delay settings"""
        try:
            delay = await dsp_service.get_delay()
            return delay
        except Exception as e:
            return {"left": 0, "right": 0, "error": str(e)}

    @router.put("/delay")
    async def set_delay(payload: DspDelayRequest):
        """Update channel delay settings"""
        try:
            success = await dsp_service.set_delay(
                left=payload.left,
                right=payload.right
            )

            if success:
                delay = await dsp_service.get_delay()
                await state_machine.broadcast_event("dsp", "delay_changed", delay)
                return {"status": "success", **delay}

            return {"status": "error", "message": "Failed to update delay"}

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
            targets = [
                {"id": "local", "name": "Milo", "host": "local", "available": True}
            ]

            # Get multiroom clients from snapcast
            try:
                from backend.config.container import container
                snapcast_svc = container.snapcast_service()
                clients = await snapcast_svc.get_clients()

                for client in clients:
                    # Skip the main Milo (localhost)
                    if client.get("host") == "milo":
                        continue

                    hostname = client.get("host", "")
                    ip = client.get("ip", "")
                    dsp_id = client.get("dsp_id", ip)  # Use dsp_id for consistency with linked_groups
                    client_name = client.get("name", hostname)

                    # Use dsp_id as target ID (must match what's stored in linked_groups.client_ids)
                    target_id = dsp_id

                    # Check if client has DSP available (proxy uses hostname or IP)
                    proxy_target = hostname if hostname.startswith("milo-client") else ip
                    available = await proxy_service.check_available(proxy_target) if proxy_service else False

                    targets.append({
                        "id": target_id,
                        "name": client_name,
                        "host": hostname,
                        "ip": ip,
                        "available": available
                    })
            except Exception as e:
                logger.warning(f"Error getting multiroom clients for DSP: {e}")

            return {"targets": targets}

        except Exception as e:
            logger.error(f"Error getting DSP targets: {e}")
            return {"targets": [{"id": "local", "name": "Milo", "host": "local", "available": True}]}

    # === Linked Clients Management ===

    @router.get("/links")
    async def get_linked_clients():
        """Get all linked client groups"""
        try:
            if settings_service:
                linked_groups = await settings_service.get_setting("dsp.linked_groups") or []
            else:
                linked_groups = []
            return {"linked_groups": linked_groups}
        except Exception as e:
            logger.error(f"Error getting linked clients: {e}")
            return {"linked_groups": []}

    @router.post("/links")
    async def create_link_group(payload: DspLinkedClientsRequest):
        """Create or update a linked client group and sync settings from source"""
        try:
            if not settings_service:
                raise HTTPException(status_code=500, detail="Settings service not available")

            linked_groups = await settings_service.get_setting("dsp.linked_groups") or []

            # Determine source client (provided or first in list)
            source_client = payload.source_client or payload.client_ids[0]
            all_clients = payload.client_ids

            # Check if any of these clients are already in another group
            new_client_ids = set(payload.client_ids)
            for i, group in enumerate(linked_groups):
                existing_ids = set(group.get("client_ids", []))
                overlap = new_client_ids & existing_ids
                if overlap:
                    # Merge with existing group
                    merged_ids = list(existing_ids | new_client_ids)
                    linked_groups[i]["client_ids"] = merged_ids
                    # Update name if provided
                    if payload.name:
                        linked_groups[i]["name"] = payload.name
                    all_clients = merged_ids
                    await settings_service.set_setting("dsp.linked_groups", linked_groups)
                    await state_machine.broadcast_event("dsp", "links_changed", {"linked_groups": linked_groups})

                    # Sync settings from source to all clients in merged group
                    sync_result = await sync_service.sync_settings(source_client, all_clients) if sync_service else {"synced": [], "errors": ["Sync service not available"]}

                    # Apply crossover if zone has a subwoofer
                    if crossover_service:
                        await crossover_service.on_zone_changed(linked_groups[i]["id"])

                    return {
                        "status": "success",
                        "message": "Merged with existing group",
                        "linked_groups": linked_groups,
                        "sync": sync_result
                    }

            # Create new group
            new_group = {
                "id": f"group_{len(linked_groups) + 1}",
                "client_ids": payload.client_ids,
                "name": payload.name or ""
            }
            linked_groups.append(new_group)
            await settings_service.set_setting("dsp.linked_groups", linked_groups)
            await state_machine.broadcast_event("dsp", "links_changed", {"linked_groups": linked_groups})

            # Sync settings from source to all other clients
            sync_result = await sync_service.sync_settings(source_client, all_clients) if sync_service else {"synced": [], "errors": ["Sync service not available"]}

            # Apply crossover if zone has a subwoofer
            if crossover_service:
                await crossover_service.on_zone_changed(new_group["id"])

            return {
                "status": "success",
                "linked_groups": linked_groups,
                "sync": sync_result
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error creating link group: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete("/links/{client_id}")
    async def unlink_client(client_id: str):
        """Remove a client from its linked group"""
        try:
            if not settings_service:
                raise HTTPException(status_code=500, detail="Settings service not available")

            linked_groups = await settings_service.get_setting("dsp.linked_groups") or []
            updated_groups = []
            found = False
            modified_group_ids = []  # Track groups that were modified and still exist

            for group in linked_groups:
                client_ids = group.get("client_ids", [])
                if client_id in client_ids:
                    found = True
                    # Remove client from group
                    client_ids = [cid for cid in client_ids if cid != client_id]
                    # Keep group only if it still has 2+ clients
                    if len(client_ids) >= 2:
                        group["client_ids"] = client_ids
                        updated_groups.append(group)
                        modified_group_ids.append(group["id"])
                else:
                    updated_groups.append(group)

            if not found:
                raise HTTPException(status_code=404, detail=f"Client {client_id} not found in any linked group")

            await settings_service.set_setting("dsp.linked_groups", updated_groups)
            await state_machine.broadcast_event("dsp", "links_changed", {"linked_groups": updated_groups})

            # Recalculate crossover for modified groups that still exist
            if crossover_service:
                for group_id in modified_group_ids:
                    await crossover_service.on_zone_changed(group_id)

            return {"status": "success", "linked_groups": updated_groups}

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error unlinking client: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete("/links")
    async def clear_all_links():
        """Remove all linked client groups"""
        try:
            if not settings_service:
                raise HTTPException(status_code=500, detail="Settings service not available")

            await settings_service.set_setting("dsp.linked_groups", [])
            await state_machine.broadcast_event("dsp", "links_changed", {"linked_groups": []})
            return {"status": "success", "linked_groups": []}

        except Exception as e:
            logger.error(f"Error clearing links: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete("/links/group/{group_id}")
    async def delete_link_group(group_id: str):
        """Delete an entire linked client group (zone)"""
        try:
            if not settings_service:
                raise HTTPException(status_code=500, detail="Settings service not available")

            linked_groups = await settings_service.get_setting("dsp.linked_groups") or []

            # Find the zone to be deleted
            zone_to_delete = next((g for g in linked_groups if g.get("id") == group_id), None)
            if not zone_to_delete:
                raise HTTPException(status_code=404, detail=f"Group {group_id} not found")

            # Disable crossover filters for all clients in this zone before deletion
            if crossover_service:
                for client_id in zone_to_delete.get("client_ids", []):
                    await crossover_service._set_client_crossover(client_id, enabled=False, frequency=80)
                    await crossover_service._set_client_lowpass(client_id, enabled=False, frequency=80)
                    logger.info(f"Disabled crossover filters for client {client_id} (zone deleted)")

            # Remove the zone from settings
            updated_groups = [g for g in linked_groups if g.get("id") != group_id]
            await settings_service.set_setting("dsp.linked_groups", updated_groups)
            await state_machine.broadcast_event("dsp", "links_changed", {"linked_groups": updated_groups})
            return {"status": "success", "linked_groups": updated_groups}

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error deleting link group: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.put("/links/{group_id}/name")
    async def update_link_group_name(group_id: str, request: Request):
        """Update the name of a linked client group (zone)"""
        try:
            if not settings_service:
                raise HTTPException(status_code=500, detail="Settings service not available")

            body = await request.json()
            name = body.get("name", "")

            linked_groups = await settings_service.get_setting("dsp.linked_groups") or []

            for group in linked_groups:
                if group.get("id") == group_id:
                    group["name"] = name
                    await settings_service.set_setting("dsp.linked_groups", linked_groups)
                    await state_machine.broadcast_event("dsp", "links_changed", {"linked_groups": linked_groups})
                    return {"status": "success", "linked_groups": linked_groups}

            raise HTTPException(status_code=404, detail=f"Group {group_id} not found")

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating link group name: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # === Speaker Type / Crossover Management ===

    @router.get("/client/{client_id}/type")
    async def get_client_type(client_id: str):
        """Get client speaker type"""
        try:
            from backend.config.container import container
            crossover_service = container.crossover_service()
            client_type = await crossover_service.get_client_type(client_id)
            return {"client_id": client_id, **client_type}
        except Exception as e:
            logger.error(f"Error getting client type: {e}")
            return {"client_id": client_id, "speaker_type": "bookshelf"}

    @router.put("/client/{client_id}/speaker-type")
    async def set_client_speaker_type(client_id: str, payload: ClientSpeakerTypeRequest):
        """Set client speaker type (satellite, bookshelf, tower, subwoofer)"""
        try:
            from backend.config.container import container
            crossover_service = container.crossover_service()
            success = await crossover_service.set_client_speaker_type(
                client_id,
                payload.speaker_type
            )

            if success:
                # Get the crossover frequency (default or custom)
                client_type = await crossover_service.get_client_type(client_id)
                return {
                    "status": "success",
                    "client_id": client_id,
                    "speaker_type": payload.speaker_type,
                    "crossover_frequency": client_type.get("crossover_frequency")
                }

            raise HTTPException(status_code=500, detail="Failed to update client speaker type")

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error setting client speaker type: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.put("/client/{client_id}/crossover-frequency")
    async def set_client_crossover_frequency(client_id: str, payload: dict):
        """Set custom crossover frequency for a client"""
        try:
            from backend.config.container import container
            crossover_service = container.crossover_service()

            frequency = payload.get("frequency")
            if frequency is None:
                raise HTTPException(status_code=400, detail="frequency is required")

            success = await crossover_service.set_client_crossover_frequency(
                client_id,
                float(frequency)
            )

            if success:
                client_type = await crossover_service.get_client_type(client_id)
                return {
                    "status": "success",
                    "client_id": client_id,
                    "speaker_type": client_type.get("speaker_type"),
                    "crossover_frequency": client_type.get("crossover_frequency")
                }

            raise HTTPException(status_code=500, detail="Failed to update crossover frequency")

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error setting client crossover frequency: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/client-types")
    async def get_all_client_types():
        """Get all client type configurations"""
        try:
            from backend.config.container import container
            crossover_service = container.crossover_service()
            client_types = await crossover_service.get_all_client_types()
            return {"client_types": client_types}
        except Exception as e:
            logger.error(f"Error getting client types: {e}")
            return {"client_types": {}}

    @router.get("/links/{group_id}/crossover")
    async def get_zone_crossover(group_id: str):
        """Get crossover settings for a zone"""
        try:
            from backend.config.container import container
            crossover_service = container.crossover_service()
            crossover = await crossover_service.get_zone_crossover(group_id)
            return {"zone_id": group_id, **crossover}
        except Exception as e:
            logger.error(f"Error getting zone crossover: {e}")
            return {
                "zone_id": group_id,
                "frequency": 80,
                "enabled": False,
                "has_subwoofer": False
            }

    @router.get("/links/{group_id}/auto-crossover")
    async def get_zone_auto_crossover(group_id: str):
        """Get automatic crossover frequency for a zone (MIN of speaker frequencies)"""
        try:
            from backend.config.container import container
            crossover_service = container.crossover_service()
            frequency = await crossover_service.get_zone_auto_crossover(group_id)
            return {"zone_id": group_id, "frequency": frequency}
        except Exception as e:
            logger.error(f"Error getting zone auto crossover: {e}")
            return {"zone_id": group_id, "frequency": 80}

    @router.put("/links/{group_id}/crossover")
    async def set_zone_crossover(group_id: str, payload: ZoneCrossoverRequest):
        """Set crossover frequency for a zone"""
        try:
            from backend.config.container import container
            crossover_service = container.crossover_service()
            success = await crossover_service.set_zone_crossover_frequency(
                group_id,
                payload.frequency
            )

            if success:
                crossover = await crossover_service.get_zone_crossover(group_id)
                return {"status": "success", "zone_id": group_id, **crossover}

            raise HTTPException(status_code=500, detail="Failed to update zone crossover")

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error setting zone crossover: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/links/{group_id}/crossover/apply")
    async def apply_zone_crossover(group_id: str):
        """Manually apply crossover settings to all clients in a zone"""
        try:
            from backend.config.container import container
            crossover_service = container.crossover_service()
            success = await crossover_service.apply_zone_crossover(group_id)

            if success:
                return {"status": "success", "message": f"Crossover applied to zone {group_id}"}

            raise HTTPException(status_code=500, detail="Failed to apply crossover")

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
        """Proxy DSP status request to client"""
        if not proxy_service:
            raise HTTPException(status_code=503, detail="Proxy service not available")
        return await proxy_service.request(hostname, "GET", "/dsp/status")

    @router.get("/client/{hostname}/filters")
    async def get_client_dsp_filters(hostname: str):
        """Proxy DSP filters request to client"""
        if not proxy_service:
            raise HTTPException(status_code=503, detail="Proxy service not available")
        return await proxy_service.request(hostname, "GET", "/dsp/filters")

    @router.put("/client/{hostname}/filter/{filter_id}")
    async def update_client_dsp_filter(hostname: str, filter_id: str, request: Request):
        """Proxy filter update to client and persist settings"""
        if not proxy_service:
            raise HTTPException(status_code=503, detail="Proxy service not available")
        body = await request.json()
        result = await proxy_service.request(hostname, "PUT", f"/dsp/filter/{filter_id}", body)
        # Save filter settings to Milo after successful update
        if result.get("status") == "success" and sync_service:
            settings = await sync_service.load_settings()
            if hostname not in settings:
                settings[hostname] = {}
            if "filters" not in settings[hostname]:
                settings[hostname]["filters"] = {}
            filter_data = {k: v for k, v in result.items() if k != "status"}
            settings[hostname]["filters"][filter_id] = filter_data
            await sync_service.save_settings(settings)
        return result

    @router.post("/client/{hostname}/reset")
    async def reset_client_dsp_filters(hostname: str):
        """Proxy filter reset to client and clear saved filter settings"""
        if not proxy_service:
            raise HTTPException(status_code=503, detail="Proxy service not available")
        result = await proxy_service.request(hostname, "POST", "/dsp/reset")
        # Clear saved filters for this client
        if result.get("status") == "success" and sync_service:
            settings = await sync_service.load_settings()
            if hostname in settings and "filters" in settings[hostname]:
                settings[hostname]["filters"] = {}
                await sync_service.save_settings(settings)
        return result

    @router.get("/client/{hostname}/compressor")
    async def get_client_compressor(hostname: str):
        """Proxy compressor GET to client"""
        if not proxy_service:
            raise HTTPException(status_code=503, detail="Proxy service not available")
        return await proxy_service.request(hostname, "GET", "/dsp/compressor")

    @router.put("/client/{hostname}/compressor")
    async def update_client_compressor(hostname: str, request: Request):
        """Proxy compressor update to client and persist settings"""
        if not proxy_service:
            raise HTTPException(status_code=503, detail="Proxy service not available")
        body = await request.json()
        result = await proxy_service.request(hostname, "PUT", "/dsp/compressor", body)
        # Save settings to Milo after successful update
        if result.get("status") == "success" and sync_service:
            compressor_data = {k: v for k, v in result.items() if k != "status"}
            await sync_service.update_client_settings(hostname, "compressor", compressor_data)
        return result

    @router.get("/client/{hostname}/loudness")
    async def get_client_loudness(hostname: str):
        """Proxy loudness GET to client"""
        if not proxy_service:
            raise HTTPException(status_code=503, detail="Proxy service not available")
        return await proxy_service.request(hostname, "GET", "/dsp/loudness")

    @router.put("/client/{hostname}/loudness")
    async def update_client_loudness(hostname: str, request: Request):
        """Proxy loudness update to client and persist settings"""
        if not proxy_service:
            raise HTTPException(status_code=503, detail="Proxy service not available")
        body = await request.json()
        result = await proxy_service.request(hostname, "PUT", "/dsp/loudness", body)
        # Save settings to Milo after successful update
        if result.get("status") == "success" and sync_service:
            loudness_data = {k: v for k, v in result.items() if k != "status"}
            await sync_service.update_client_settings(hostname, "loudness", loudness_data)
        return result

    @router.get("/client/{hostname}/delay")
    async def get_client_delay(hostname: str):
        """Proxy delay GET to client"""
        if not proxy_service:
            raise HTTPException(status_code=503, detail="Proxy service not available")
        return await proxy_service.request(hostname, "GET", "/dsp/delay")

    @router.put("/client/{hostname}/delay")
    async def update_client_delay(hostname: str, request: Request):
        """Proxy delay update to client and persist settings"""
        if not proxy_service:
            raise HTTPException(status_code=503, detail="Proxy service not available")
        body = await request.json()
        result = await proxy_service.request(hostname, "PUT", "/dsp/delay", body)
        # Save settings to Milo after successful update
        if result.get("status") == "success" and sync_service:
            delay_data = {k: v for k, v in result.items() if k != "status"}
            await sync_service.update_client_settings(hostname, "delay", delay_data)
        return result

    @router.get("/client/{hostname}/volume")
    async def get_client_volume(hostname: str):
        """Get volume for a specific client (local or remote)."""
        normalized = 'local' if hostname in ('local', 'milo') else hostname

        if normalized == 'local':
            # Return local CamillaDSP volume directly
            try:
                volume = await dsp_service.get_volume()
                return {"main": volume.get("main", -30), "mute": volume.get("mute", False)}
            except Exception:
                # Fallback if DSP not connected
                return {"main": -30, "mute": False}

        # Remote client: proxy
        if not proxy_service:
            raise HTTPException(status_code=503, detail="Proxy service not available")
        return await proxy_service.request(hostname, "GET", "/dsp/volume")

    @router.put("/client/{hostname}/volume")
    async def update_client_volume(hostname: str, request: Request):
        """
        Set volume for a specific client (local or remote).

        For 'local'/'milo': Uses multiroom_handler.set_client_volume_db() to change
        only the local CamillaDSP volume without affecting other clients.

        For remote clients: Proxies to the client's DSP API.
        """
        body = await request.json()
        volume_db = body.get("volume")

        # Normalize hostname: 'milo' -> 'local'
        normalized = 'local' if hostname in ('local', 'milo') else hostname

        if normalized == 'local':
            # Handle local CamillaDSP volume via multiroom handler
            # This sets only this client's volume (updates offset, doesn't affect others)
            if not state_machine:
                raise HTTPException(status_code=500, detail="State machine not available")

            volume_service = getattr(state_machine, 'volume_service', None)
            if not volume_service:
                raise HTTPException(status_code=500, detail="Volume service not available")

            multiroom_handler = volume_service._multiroom_handler
            success = await multiroom_handler.set_client_volume_db('local', volume_db)

            if success:
                # Sync volume_service._current_volume_db for correct broadcast in direct mode
                volume_service._current_volume_db = volume_db
                # Broadcast volume change (will include only this client's change)
                await volume_service._schedule_broadcast(show_bar=False)
                return {"status": "success", "main": volume_db}
            else:
                raise HTTPException(status_code=500, detail="Failed to set local volume")

        # Remote client: proxy to client's DSP API
        if not proxy_service:
            raise HTTPException(status_code=503, detail="Proxy service not available")
        result = await proxy_service.request(hostname, "PUT", "/dsp/volume", body)

        # Save volume to Milo after successful update
        if result.get("status") == "success" and sync_service:
            volume_data = {k: v for k, v in result.items() if k != "status"}
            await sync_service.update_client_settings(hostname, "volume", volume_data)

            # Update multiroom volume cache with actual response value, not request
            # The response 'main' field contains the actual volume set by the client
            actual_volume = result.get("main", result.get("volume", volume_db))
            if actual_volume is not None and state_machine:
                volume_service = getattr(state_machine, 'volume_service', None)
                if volume_service:
                    await volume_service.update_client_volume_db(normalized, actual_volume)
                    # Log if requested vs actual differs (client clamped the value)
                    if actual_volume != volume_db:
                        logger.info(
                            f"Client {hostname} volume clamped: requested {volume_db} dB, actual {actual_volume} dB"
                        )

        return result

    @router.put("/client/{hostname}/mute")
    async def update_client_mute(hostname: str, request: Request):
        """Proxy mute update to client and persist settings"""
        if not proxy_service:
            raise HTTPException(status_code=503, detail="Proxy service not available")
        body = await request.json()
        result = await proxy_service.request(hostname, "PUT", "/dsp/mute", body)
        # Save mute to Milo after successful update
        if result.get("status") == "success" and sync_service:
            muted = result.get("muted", False)
            # Store mute state in volume settings
            settings = await sync_service.load_settings()
            if hostname not in settings:
                settings[hostname] = {}
            if "volume" not in settings[hostname]:
                settings[hostname]["volume"] = {}
            settings[hostname]["volume"]["mute"] = muted
            await sync_service.save_settings(settings)

            # Update multiroom volume handler mute cache for average calculation
            if hasattr(state_machine, 'volume_service'):
                volume_service = state_machine.volume_service
                if hasattr(volume_service, '_multiroom_handler'):
                    volume_service._multiroom_handler.set_client_mute(hostname, muted)
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
            return {"status": "error", "message": "Services not available", "restored": [], "errors": ["Services not available"]}

        saved = await sync_service.get_client_settings(hostname)
        if not saved:
            return {"status": "success", "message": "No saved settings to restore", "restored": []}

        restored = []
        errors = []

        # Restore compressor settings
        if "compressor" in saved:
            try:
                await proxy_service.request(hostname, "PUT", "/dsp/compressor", saved["compressor"])
                restored.append("compressor")
            except Exception as e:
                errors.append(f"compressor: {e}")

        # Restore loudness settings
        if "loudness" in saved:
            try:
                await proxy_service.request(hostname, "PUT", "/dsp/loudness", saved["loudness"])
                restored.append("loudness")
            except Exception as e:
                errors.append(f"loudness: {e}")

        # Restore delay settings
        if "delay" in saved:
            try:
                await proxy_service.request(hostname, "PUT", "/dsp/delay", saved["delay"])
                restored.append("delay")
            except Exception as e:
                errors.append(f"delay: {e}")

        # Restore filter settings
        if "filters" in saved:
            for filter_id, filter_data in saved["filters"].items():
                try:
                    await proxy_service.request(hostname, "PUT", f"/dsp/filter/{filter_id}", filter_data)
                    restored.append(f"filter:{filter_id}")
                except Exception as e:
                    errors.append(f"filter:{filter_id}: {e}")

        # Restore volume settings
        if "volume" in saved:
            vol_settings = saved["volume"]
            # Restore volume level
            if "main" in vol_settings:
                try:
                    await proxy_service.request(hostname, "PUT", "/dsp/volume", {"volume": vol_settings["main"]})
                    restored.append("volume")
                except Exception as e:
                    errors.append(f"volume: {e}")
            # Restore mute state
            if "mute" in vol_settings:
                try:
                    await proxy_service.request(hostname, "PUT", "/dsp/mute", {"muted": vol_settings["mute"]})
                    restored.append("mute")
                except Exception as e:
                    errors.append(f"mute: {e}")

        logger.info(f"Restored settings for {hostname}: {restored}")
        if errors:
            logger.warning(f"Errors restoring settings for {hostname}: {errors}")

        return {
            "status": "success" if not errors else "partial",
            "restored": restored,
            "errors": errors if errors else None
        }

    return router
