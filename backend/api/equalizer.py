# backend/api/equalizer.py
"""
API routes for CamillaDSP digital signal processing
Full equalizer capabilities including EQ, compressor, loudness, and volume control
Supports multi-client equalizer control for multiroom setups
"""
import logging
from typing import Optional, TYPE_CHECKING
from fastapi import APIRouter, HTTPException, Request

from backend.api.route_helpers import api_error_handler

from backend.api.responses import (
    EqualizerEnabledResponse,
    EqualizerPresetsResponse,
    EqualizerRecordResponse,
    StatusResponse,
    TargetFilterResponse,
    TargetMonoResponse,
    TargetPresetResponse,
    TargetSaveCustomResponse,
    TargetStatusResponse,
)
from backend.api.models import (
    EqualizerFilterUpdateRequest,
    EqualizerCompressorRequest,
    EqualizerLoudnessRequest,
    LevelsMonitorRequest,
    ZoneCrossoverRequest,
    EqualizerPresetRequest
)
from backend.core.equalizer.presets import DEFAULT_CUSTOM_GAINS

if TYPE_CHECKING:
    from backend.core.equalizer.levels_monitor import LevelsMonitor
    from backend.core.equalizer.multiroom_service import MultiroomEqualizerService
    from backend.core.equalizer.service import CamillaDSPService
    from backend.core.multiroom.client_registry import ClientRegistryService
    from backend.core.multiroom.crossover import CrossoverService
    from backend.core.multiroom.equalizer_router import EqualizerRouter
    from backend.core.multiroom.routing import AudioRoutingService

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
    camilladsp_service: "CamillaDSPService",
    routing_service: Optional["AudioRoutingService"] = None,
    crossover_service: Optional["CrossoverService"] = None,
    client_registry_service: Optional["ClientRegistryService"] = None,
    equalizer_router_service: Optional["EqualizerRouter"] = None,
    multiroom_equalizer_service: Optional["MultiroomEqualizerService"] = None,
    levels_monitor: Optional["LevelsMonitor"] = None
):
    """Creates equalizer router with injected dependencies"""
    router = APIRouter(prefix="/api/equalizer", tags=["equalizer"])

    # === Audio Levels ===

    @router.post("/levels/monitor", response_model=StatusResponse)
    async def keepalive_levels_monitor(payload: LevelsMonitorRequest):
        """Arm the WS levels push (`equalizer`/`levels`) for LevelsMonitor.KEEPALIVE_TTL.

        The push runs at LevelsMonitor.SAMPLE_INTERVAL and only when the reading
        changed.

        Open EQ views re-POST this keepalive every few seconds while visible;
        the monitor stops by itself once the last keepalive expires.
        client_ids selects the clients to aggregate (empty = local DAC).
        """
        async with api_error_handler("Error arming levels monitor", logger):
            levels_monitor.keepalive(payload.client_ids)
            return {"status": "success"}

    # === Preset Catalog ===

    @router.get("/presets", response_model=EqualizerPresetsResponse, response_model_exclude_none=True)
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

    # === Unified Per-Target Routes (one grammar for local / remote / zone) ===
    # Zone CRUD lives at /api/multiroom/zones, speaker-type at
    # PATCH /api/multiroom/clients/{mac_id}.

    @router.get("/target/{target}", response_model=EqualizerRecordResponse)
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
                # A record carries custom_gains only once a custom curve has been
                # saved; "not saved yet" reads as flat here, because the response
                # model — and the curve the UI draws — need ten numbers. Resolved
                # per target rather than through resolve_preset_gains, whose
                # fallback is the LOCAL unit's curve: a satellite must not be shown
                # wearing the server's.
                "custom_gains": record.custom_gains or DEFAULT_CUSTOM_GAINS,
                "filters": [f.to_wire_dict() for f in record.filters],
                "state": status.get("state", "disconnected"),
                "sample_rate": status.get("sample_rate"),
                "available": status.get("available", False),
            }

    @router.put("/target/{target}/filter/{filter_id}", response_model=TargetFilterResponse)
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

    @router.put("/target/{target}/compressor", response_model=TargetStatusResponse)
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

    @router.put("/target/{target}/loudness", response_model=TargetStatusResponse)
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

    @router.put("/target/{target}/mono", response_model=TargetMonoResponse)
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

    @router.put("/target/{target}/crossover")
    async def set_target_crossover(target: str, payload: ZoneCrossoverRequest):
        """Set the crossover frequency. Zone targets only.

        A crossover is a property of how a zone splits its members' bands, so
        `local` and a bare `<mac>` have nothing to set — they get a 400 rather
        than silently doing nothing. Deliberately untyped: the frequency mixes
        int/float/None and a response_model would coerce 80 to 80.0.
        """
        async with api_error_handler(f"Error setting crossover for target {target}", logger):
            target_type, zone_id = _resolve_target(target)
            if target_type != "zone":
                logger.error("Crossover requested for a non-zone target: %s", target)
                raise HTTPException(
                    status_code=400,
                    detail="Crossover applies to zone targets only (zone:<id>)",
                )
            if not await crossover_service.set_zone_crossover_frequency(zone_id, payload.frequency):
                logger.error("Crossover update rejected for zone %s", zone_id)
                raise HTTPException(status_code=500, detail="Failed to update zone crossover")
            return {
                "status": "success",
                "zone_id": zone_id,
                **await crossover_service.get_zone_crossover(zone_id),
            }

    @router.put("/target/{target}/enabled", response_model=EqualizerEnabledResponse)
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

    @router.post("/target/{target}/preset", response_model=TargetPresetResponse)
    async def load_target_preset(target: str, payload: EqualizerPresetRequest):
        """Load a preset for any target; returns resolved gains for immediate UI apply."""
        async with api_error_handler(f"Error loading preset for target {target}", logger):
            target_type, target_id = _resolve_target(target)
            try:
                success, gains = await multiroom_equalizer_service.load_preset(
                    target_type, target_id, payload.preset_id
                )
            except ValueError as e:
                logger.error(f"Preset load failed for target {target}: {e}")
                raise HTTPException(status_code=404, detail=str(e))
            return {
                "status": "success" if success else "error",
                "target": target,
                "preset_id": payload.preset_id,
                "gains": gains,
            }

    @router.post("/target/{target}/save-custom", response_model=TargetSaveCustomResponse)
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
