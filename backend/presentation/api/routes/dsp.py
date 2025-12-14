# backend/presentation/api/routes/dsp.py
"""
API routes for CamillaDSP parametric equalizer
Replaces the old equalizer routes with full DSP capabilities
Supports multi-client DSP control for multiroom setups
"""
from typing import Optional
import aiohttp
import asyncio
import json
import logging
import os
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
    DspLinkedClientsRequest
)

logger = logging.getLogger(__name__)

# Client DSP settings persistence file
CLIENT_DSP_FILE = "/var/lib/milo/client_dsp.json"
_client_dsp_lock = asyncio.Lock()


async def _load_client_dsp_settings() -> dict:
    """Load client DSP settings from disk"""
    try:
        if os.path.exists(CLIENT_DSP_FILE):
            with open(CLIENT_DSP_FILE, "r") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Error loading client DSP settings: {e}")
    return {}


async def _save_client_dsp_settings(settings: dict):
    """Save client DSP settings to disk atomically"""
    async with _client_dsp_lock:
        try:
            os.makedirs(os.path.dirname(CLIENT_DSP_FILE), exist_ok=True)
            temp_file = CLIENT_DSP_FILE + ".tmp"
            with open(temp_file, "w") as f:
                json.dump(settings, f, indent=2)
            os.replace(temp_file, CLIENT_DSP_FILE)
        except Exception as e:
            logger.error(f"Error saving client DSP settings: {e}")


async def _get_client_settings(hostname: str) -> dict:
    """Get saved DSP settings for a specific client"""
    settings = await _load_client_dsp_settings()
    return settings.get(hostname, {})


async def _update_client_settings(hostname: str, category: str, data: dict):
    """Update and persist DSP settings for a client"""
    settings = await _load_client_dsp_settings()
    if hostname not in settings:
        settings[hostname] = {}
    settings[hostname][category] = data
    await _save_client_dsp_settings(settings)
    logger.info(f"Saved {category} settings for client {hostname}")


async def _sync_dsp_settings(source_client: str, target_clients: list):
    """
    Sync DSP settings from source client to target clients.
    Gets compressor, loudness, delay, and filters from source and pushes to targets.
    """
    synced = []
    errors = []

    # Get settings from source client
    try:
        if source_client == 'local':
            # Get from local Milo DSP
            from backend.config.container import container
            dsp_svc = container.camilladsp_service()
            source_settings = {
                'compressor': await dsp_svc.get_compressor(),
                'loudness': await dsp_svc.get_loudness(),
                'delay': await dsp_svc.get_delay(),
                'filters': await dsp_svc.get_filters()
            }
        else:
            # Get from remote client
            source_settings = {}
            try:
                source_settings['compressor'] = await _proxy_client_request(source_client, "GET", "/dsp/compressor")
            except Exception:
                pass
            try:
                source_settings['loudness'] = await _proxy_client_request(source_client, "GET", "/dsp/loudness")
            except Exception:
                pass
            try:
                source_settings['delay'] = await _proxy_client_request(source_client, "GET", "/dsp/delay")
            except Exception:
                pass
            try:
                filters_resp = await _proxy_client_request(source_client, "GET", "/dsp/filters")
                source_settings['filters'] = filters_resp.get('filters', [])
            except Exception:
                pass
    except Exception as e:
        logger.error(f"Error getting settings from source {source_client}: {e}")
        return {"synced": [], "errors": [f"Failed to get source settings: {e}"]}

    # Push settings to each target client
    for target in target_clients:
        if target == source_client:
            continue

        target_synced = []

        # Sync compressor
        if 'compressor' in source_settings and source_settings['compressor']:
            try:
                if target == 'local':
                    from backend.config.container import container
                    dsp_svc = container.camilladsp_service()
                    await dsp_svc.set_compressor(**source_settings['compressor'])
                else:
                    await _proxy_client_request(target, "PUT", "/dsp/compressor", source_settings['compressor'])
                    await _update_client_settings(target, "compressor", source_settings['compressor'])
                target_synced.append("compressor")
            except Exception as e:
                errors.append(f"{target}/compressor: {e}")

        # Sync loudness
        if 'loudness' in source_settings and source_settings['loudness']:
            try:
                if target == 'local':
                    from backend.config.container import container
                    dsp_svc = container.camilladsp_service()
                    await dsp_svc.set_loudness(**source_settings['loudness'])
                else:
                    await _proxy_client_request(target, "PUT", "/dsp/loudness", source_settings['loudness'])
                    await _update_client_settings(target, "loudness", source_settings['loudness'])
                target_synced.append("loudness")
            except Exception as e:
                errors.append(f"{target}/loudness: {e}")

        # Sync delay
        if 'delay' in source_settings and source_settings['delay']:
            try:
                if target == 'local':
                    from backend.config.container import container
                    dsp_svc = container.camilladsp_service()
                    await dsp_svc.set_delay(**source_settings['delay'])
                else:
                    await _proxy_client_request(target, "PUT", "/dsp/delay", source_settings['delay'])
                    await _update_client_settings(target, "delay", source_settings['delay'])
                target_synced.append("delay")
            except Exception as e:
                errors.append(f"{target}/delay: {e}")

        # Sync filters
        if 'filters' in source_settings and source_settings['filters']:
            for flt in source_settings['filters']:
                filter_id = flt.get('id')
                if not filter_id:
                    continue
                filter_data = {
                    'freq': flt.get('freq'),
                    'gain': flt.get('gain'),
                    'q': flt.get('q'),
                    'filter_type': flt.get('type')
                }
                try:
                    if target == 'local':
                        from backend.config.container import container
                        dsp_svc = container.camilladsp_service()
                        await dsp_svc.set_filter(filter_id, **filter_data)
                    else:
                        await _proxy_client_request(target, "PUT", f"/dsp/filter/{filter_id}", filter_data)
                    target_synced.append(f"filter:{filter_id}")
                except Exception as e:
                    errors.append(f"{target}/filter:{filter_id}: {e}")

        if target_synced:
            synced.append({"target": target, "settings": target_synced})

    logger.info(f"Synced DSP settings from {source_client} to {len(synced)} targets")
    if errors:
        logger.warning(f"Sync errors: {errors}")

    return {"synced": synced, "errors": errors if errors else None}


def create_dsp_router(dsp_service, state_machine, settings_service=None):
    """Creates DSP router with injected dependencies"""
    router = APIRouter(prefix="/api/dsp", tags=["dsp"])

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

    # === Volume Control ===

    @router.get("/volume")
    async def get_volume():
        """Get current DSP volume settings"""
        try:
            volume = await dsp_service.get_volume()
            return volume
        except Exception as e:
            return {"main": 0, "mute": False, "error": str(e)}

    @router.put("/volume")
    async def set_volume(payload: DspVolumeRequest):
        """Set DSP volume in dB"""
        try:
            success = await dsp_service.set_volume(payload.volume)

            if success:
                await state_machine.broadcast_event("dsp", "volume_changed", {"volume": payload.volume})
                return {"status": "success", "volume": payload.volume}

            return {"status": "error", "message": "Failed to set volume"}

        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.put("/mute")
    async def set_mute(payload: DspMuteRequest):
        """Set DSP mute state"""
        try:
            success = await dsp_service.set_mute(payload.muted)

            if success:
                await state_machine.broadcast_event("dsp", "mute_changed", {"muted": payload.muted})
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
                    client_name = client.get("name", hostname)

                    # Check if client has DSP available (port 8001)
                    available = await _check_client_dsp_available(hostname)

                    targets.append({
                        "id": hostname,
                        "name": client_name,
                        "host": hostname,
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
                    all_clients = merged_ids
                    await settings_service.set_setting("dsp.linked_groups", linked_groups)
                    await state_machine.broadcast_event("dsp", "links_changed", {"linked_groups": linked_groups})

                    # Sync settings from source to all clients in merged group
                    sync_result = await _sync_dsp_settings(source_client, all_clients)

                    return {
                        "status": "success",
                        "message": "Merged with existing group",
                        "linked_groups": linked_groups,
                        "sync": sync_result
                    }

            # Create new group
            new_group = {
                "id": f"group_{len(linked_groups) + 1}",
                "client_ids": payload.client_ids
            }
            linked_groups.append(new_group)
            await settings_service.set_setting("dsp.linked_groups", linked_groups)
            await state_machine.broadcast_event("dsp", "links_changed", {"linked_groups": linked_groups})

            # Sync settings from source to all other clients
            sync_result = await _sync_dsp_settings(source_client, all_clients)

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
                else:
                    updated_groups.append(group)

            if not found:
                raise HTTPException(status_code=404, detail=f"Client {client_id} not found in any linked group")

            await settings_service.set_setting("dsp.linked_groups", updated_groups)
            await state_machine.broadcast_event("dsp", "links_changed", {"linked_groups": updated_groups})
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

    # === Client DSP Proxy Routes ===

    @router.get("/client/{hostname}/status")
    async def get_client_dsp_status(hostname: str):
        """Proxy DSP status request to client"""
        return await _proxy_client_request(hostname, "GET", "/dsp/status")

    @router.get("/client/{hostname}/filters")
    async def get_client_dsp_filters(hostname: str):
        """Proxy DSP filters request to client"""
        return await _proxy_client_request(hostname, "GET", "/dsp/filters")

    @router.put("/client/{hostname}/filter/{filter_id}")
    async def update_client_dsp_filter(hostname: str, filter_id: str, request: Request):
        """Proxy filter update to client and persist settings"""
        body = await request.json()
        result = await _proxy_client_request(hostname, "PUT", f"/dsp/filter/{filter_id}", body)
        # Save filter settings to Milo after successful update
        if result.get("status") == "success":
            # Load existing filters and update the specific one
            settings = await _load_client_dsp_settings()
            if hostname not in settings:
                settings[hostname] = {}
            if "filters" not in settings[hostname]:
                settings[hostname]["filters"] = {}
            filter_data = {k: v for k, v in result.items() if k != "status"}
            settings[hostname]["filters"][filter_id] = filter_data
            await _save_client_dsp_settings(settings)
        return result

    @router.post("/client/{hostname}/reset")
    async def reset_client_dsp_filters(hostname: str):
        """Proxy filter reset to client and clear saved filter settings"""
        result = await _proxy_client_request(hostname, "POST", "/dsp/reset")
        # Clear saved filters for this client
        if result.get("status") == "success":
            settings = await _load_client_dsp_settings()
            if hostname in settings and "filters" in settings[hostname]:
                settings[hostname]["filters"] = {}
                await _save_client_dsp_settings(settings)
        return result

    @router.get("/client/{hostname}/compressor")
    async def get_client_compressor(hostname: str):
        """Proxy compressor GET to client"""
        return await _proxy_client_request(hostname, "GET", "/dsp/compressor")

    @router.put("/client/{hostname}/compressor")
    async def update_client_compressor(hostname: str, request: Request):
        """Proxy compressor update to client and persist settings"""
        body = await request.json()
        result = await _proxy_client_request(hostname, "PUT", "/dsp/compressor", body)
        # Save settings to Milo after successful update
        if result.get("status") == "success":
            compressor_data = {k: v for k, v in result.items() if k != "status"}
            await _update_client_settings(hostname, "compressor", compressor_data)
        return result

    @router.get("/client/{hostname}/loudness")
    async def get_client_loudness(hostname: str):
        """Proxy loudness GET to client"""
        return await _proxy_client_request(hostname, "GET", "/dsp/loudness")

    @router.put("/client/{hostname}/loudness")
    async def update_client_loudness(hostname: str, request: Request):
        """Proxy loudness update to client and persist settings"""
        body = await request.json()
        result = await _proxy_client_request(hostname, "PUT", "/dsp/loudness", body)
        # Save settings to Milo after successful update
        if result.get("status") == "success":
            loudness_data = {k: v for k, v in result.items() if k != "status"}
            await _update_client_settings(hostname, "loudness", loudness_data)
        return result

    @router.get("/client/{hostname}/delay")
    async def get_client_delay(hostname: str):
        """Proxy delay GET to client"""
        return await _proxy_client_request(hostname, "GET", "/dsp/delay")

    @router.put("/client/{hostname}/delay")
    async def update_client_delay(hostname: str, request: Request):
        """Proxy delay update to client and persist settings"""
        body = await request.json()
        result = await _proxy_client_request(hostname, "PUT", "/dsp/delay", body)
        # Save settings to Milo after successful update
        if result.get("status") == "success":
            delay_data = {k: v for k, v in result.items() if k != "status"}
            await _update_client_settings(hostname, "delay", delay_data)
        return result

    # === Client Settings Persistence ===

    @router.get("/client/{hostname}/saved-settings")
    async def get_client_saved_settings(hostname: str):
        """Get Milo's saved DSP settings for a client"""
        settings = await _get_client_settings(hostname)
        return {"hostname": hostname, "settings": settings}

    @router.post("/client/{hostname}/restore")
    async def restore_client_settings(hostname: str):
        """Restore saved DSP settings to a client"""
        saved = await _get_client_settings(hostname)
        if not saved:
            return {"status": "success", "message": "No saved settings to restore", "restored": []}

        restored = []
        errors = []

        # Restore compressor settings
        if "compressor" in saved:
            try:
                await _proxy_client_request(hostname, "PUT", "/dsp/compressor", saved["compressor"])
                restored.append("compressor")
            except Exception as e:
                errors.append(f"compressor: {e}")

        # Restore loudness settings
        if "loudness" in saved:
            try:
                await _proxy_client_request(hostname, "PUT", "/dsp/loudness", saved["loudness"])
                restored.append("loudness")
            except Exception as e:
                errors.append(f"loudness: {e}")

        # Restore delay settings
        if "delay" in saved:
            try:
                await _proxy_client_request(hostname, "PUT", "/dsp/delay", saved["delay"])
                restored.append("delay")
            except Exception as e:
                errors.append(f"delay: {e}")

        # Restore filter settings
        if "filters" in saved:
            for filter_id, filter_data in saved["filters"].items():
                try:
                    await _proxy_client_request(hostname, "PUT", f"/dsp/filter/{filter_id}", filter_data)
                    restored.append(f"filter:{filter_id}")
                except Exception as e:
                    errors.append(f"filter:{filter_id}: {e}")

        logger.info(f"Restored settings for {hostname}: {restored}")
        if errors:
            logger.warning(f"Errors restoring settings for {hostname}: {errors}")

        return {
            "status": "success" if not errors else "partial",
            "restored": restored,
            "errors": errors if errors else None
        }

    return router


# === Helper functions for client proxy ===

async def _check_client_dsp_available(hostname: str) -> bool:
    """Check if a client's DSP API is available"""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as session:
            url = f"http://{hostname}.local:8001/health"
            async with session.get(url) as response:
                return response.status == 200
    except Exception:
        return False


async def _proxy_client_request(hostname: str, method: str, path: str, body: dict = None):
    """Proxy a request to a client's DSP API"""
    try:
        url = f"http://{hostname}.local:8001{path}"
        timeout = aiohttp.ClientTimeout(total=10)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            if method == "GET":
                async with session.get(url) as response:
                    if response.status == 200:
                        return await response.json()
                    raise HTTPException(status_code=response.status, detail=f"Client error: {response.status}")
            elif method == "PUT":
                async with session.put(url, json=body) as response:
                    if response.status == 200:
                        return await response.json()
                    raise HTTPException(status_code=response.status, detail=f"Client error: {response.status}")
            elif method == "POST":
                async with session.post(url, json=body) as response:
                    if response.status == 200:
                        return await response.json()
                    raise HTTPException(status_code=response.status, detail=f"Client error: {response.status}")

    except aiohttp.ClientError as e:
        logger.error(f"Error proxying request to {hostname}: {e}")
        raise HTTPException(status_code=503, detail=f"Cannot reach client {hostname}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error proxying to {hostname}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
