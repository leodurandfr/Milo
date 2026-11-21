"""
Equalizer control routes for Milo Client.

A setter of EqualizerService answers False when it cannot apply the request —
CamillaDSP not connected yet, no config to edit, a filter the config does not
hold, loudness without a reference — and a route turns that into a 400: what
the server's EqualizerController reads as a satellite that answered and
refused, and queues for the next sync. Nothing else is caught here: an exception that still escapes is a
bug, and FastAPI answers it 500 with its traceback in the journal.
"""
from fastapi import APIRouter, HTTPException

from services.equalizer import EqualizerService
from models import (
    FilterUpdate, FiltersBatchUpdate, CompressorUpdate, LoudnessUpdate,
    MonoUpdate, GainUpdate, VolumeUpdate, MuteUpdate,
    PassFilterUpdate, EqualizerEnabledUpdate
)


def _refused(detail: str) -> HTTPException:
    return HTTPException(status_code=400, detail=detail)


def create_equalizer_router(equalizer_service: EqualizerService) -> APIRouter:
    """Creates equalizer router with injected dependencies."""
    router = APIRouter(prefix="/equalizer", tags=["equalizer"])

    # === Status ===

    @router.get("/status")
    async def get_equalizer_status():
        """Get equalizer status and filter configuration."""
        return await equalizer_service.get_status()

    @router.put("/enabled")
    async def set_equalizer_enabled(update: EqualizerEnabledUpdate):
        """Enable or disable equalizer effects (compressor, loudness)."""
        if not await equalizer_service.set_equalizer_enabled(update.enabled):
            raise _refused("Failed to set equalizer enabled")
        return {"status": "success", "enabled": update.enabled}

    @router.get("/levels")
    async def get_levels():
        """Real-time playback peak (`output_peak`), polled by the server's LevelsMonitor."""
        return await equalizer_service.get_levels()

    # === Filters ===

    @router.put("/filter/{filter_id}")
    async def update_equalizer_filter(filter_id: str, update: FilterUpdate):
        """Update a single EQ filter band."""
        if not await equalizer_service.set_filter(
            filter_id=filter_id,
            gain=update.gain,
            freq=update.freq,
            q=update.q,
            filter_type=update.filter_type,
            headroom_db=update.headroom_db,
        ):
            raise _refused("Failed to update filter")
        return {"status": "success", "filter_id": filter_id}

    @router.put("/filters")
    async def update_equalizer_filters_batch(update: FiltersBatchUpdate):
        """Update multiple EQ filter bands in one request (single disk save)."""
        if not await equalizer_service.set_filters_batch(update.filters, update.headroom_db):
            raise _refused("Failed to update filters")
        return {"status": "success"}

    # === Volume ===

    @router.put("/volume")
    async def update_volume(update: VolumeUpdate):
        """Update equalizer volume."""
        if not await equalizer_service.set_volume(update.volume):
            raise _refused("Failed to update volume")
        return {"status": "success", **equalizer_service.volume_state}

    @router.put("/mute")
    async def update_mute(update: MuteUpdate):
        """Update equalizer mute state."""
        if not await equalizer_service.set_mute(update.muted):
            raise _refused("Failed to update mute")
        return {"status": "success", "muted": equalizer_service.volume_state["mute"]}

    # === Compressor ===

    @router.put("/compressor")
    async def update_compressor(update: CompressorUpdate):
        """Update compressor settings."""
        if not await equalizer_service.set_compressor(
            enabled=update.enabled,
            threshold=update.threshold,
            ratio=update.ratio,
            attack=update.attack,
            release=update.release,
            makeup_gain=update.makeup_gain
        ):
            raise _refused("Failed to update compressor")
        return {"status": "success", **equalizer_service.compressor}

    # === Loudness ===

    @router.put("/loudness")
    async def update_loudness(update: LoudnessUpdate):
        """Update loudness compensation settings."""
        if not await equalizer_service.set_loudness(
            enabled=update.enabled,
            high_boost=update.high_boost,
            low_boost=update.low_boost,
            reference_level=update.reference_level,
        ):
            raise _refused("Failed to update loudness")
        return {"status": "success", **equalizer_service.loudness}

    # === Mono ===

    @router.put("/mono")
    async def update_mono(update: MonoUpdate):
        """Update mono mixing settings."""
        if not await equalizer_service.set_mono(enabled=update.enabled):
            raise _refused("Failed to update mono")
        return {"status": "success", "enabled": equalizer_service.mono}

    # === Level trim ===

    @router.put("/gain")
    async def update_gain(update: GainUpdate):
        """Set the level trim (a fixed Gain stage, in dB).

        Balances this speaker against the rest of the system. Separate from the
        volume fader on purpose, and outside the master bypass gate — a bypass
        that unbalanced the room would be a bug.
        """
        if not await equalizer_service.set_gain(update.gain_db):
            raise _refused("Failed to update gain")
        return {"status": "success", "gain_db": equalizer_service.gain_db}

    # === Crossover ===

    @router.put("/crossover")
    async def update_crossover(update: PassFilterUpdate):
        """Update crossover highpass filter settings."""
        if not await equalizer_service.set_crossover(
            enabled=update.enabled, frequency=update.frequency, q=update.q
        ):
            raise _refused("Failed to update crossover")
        return {"status": "success", **update.model_dump()}

    @router.put("/lowpass")
    async def update_lowpass(update: PassFilterUpdate):
        """Update lowpass filter settings (for subwoofers)."""
        if not await equalizer_service.set_lowpass(
            enabled=update.enabled, frequency=update.frequency, q=update.q
        ):
            raise _refused("Failed to update lowpass")
        return {"status": "success", **update.model_dump()}

    return router
