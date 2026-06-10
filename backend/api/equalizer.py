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
    routing_service=None,
    crossover_service=None,
    client_registry_service=None,
    equalizer_router_service=None,
    multiroom_equalizer_service=None,
    volume_service=None
):
    """Creates equalizer router with injected dependencies"""
    router = APIRouter(prefix="/api/equalizer", tags=["equalizer"])

    # === Internal Helpers ===

    def _get_local_client_mac():
        """Get the MAC address of the local client from registry."""
        if not client_registry_service:
            return None
        for client in client_registry_service.get_all_clients().values():
            if client.is_local:
                return client.mac_id
        return None

    # === Audio Levels ===

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

    # === Preset Catalog ===

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
