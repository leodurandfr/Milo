"""
Equalizer control routes for Milo Client.
"""
import logging
from fastapi import APIRouter, HTTPException

from services.equalizer import EqualizerService
from models import (
    FilterUpdate, FiltersBatchUpdate, CompressorUpdate, LoudnessUpdate,
    DelayUpdate, VolumeUpdate, MuteUpdate,
    CrossoverUpdate, LowpassUpdate, EqualizerEnabledUpdate
)

logger = logging.getLogger(__name__)


def create_equalizer_router(equalizer_service: EqualizerService) -> APIRouter:
    """Creates equalizer router with injected dependencies."""
    router = APIRouter(prefix="/equalizer", tags=["equalizer"])

    # === Status ===

    @router.get("/status")
    async def get_equalizer_status():
        """Get equalizer status and filter configuration."""
        try:
            status = await equalizer_service.get_status()
            return status
        except Exception as e:
            logger.error(f"Error getting equalizer status: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.put("/enabled")
    async def set_equalizer_enabled(update: EqualizerEnabledUpdate):
        """Enable or disable equalizer effects (compressor, loudness)."""
        try:
            success = await equalizer_service.set_equalizer_enabled(update.enabled)
            if success:
                return {"status": "success", "enabled": update.enabled}
            else:
                raise HTTPException(status_code=400, detail="Failed to set equalizer enabled")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error setting equalizer enabled: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/filters")
    async def get_equalizer_filters():
        """Get current EQ filter configuration."""
        try:
            filters = await equalizer_service.get_filters()
            return {"filters": filters}
        except Exception as e:
            logger.error(f"Error getting filters: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/levels")
    async def get_levels():
        """Get real-time audio levels (peak/RMS)."""
        return await equalizer_service.get_levels()

    # === Filters ===

    @router.put("/filter/{filter_id}")
    async def update_equalizer_filter(filter_id: str, update: FilterUpdate):
        """Update a single EQ filter band."""
        try:
            success = await equalizer_service.set_filter(
                filter_id=filter_id,
                gain=update.gain,
                freq=update.freq,
                q=update.q
            )
            if success:
                return {"status": "success", "filter_id": filter_id}
            else:
                raise HTTPException(status_code=400, detail="Failed to update filter")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating filter: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.put("/filters")
    async def update_equalizer_filters_batch(update: FiltersBatchUpdate):
        """Update multiple EQ filter bands in one request (single disk save)."""
        try:
            result = await equalizer_service.set_filters_batch(update.filters)
            if result.get("success"):
                return {"status": "success", "applied": result["applied"]}
            else:
                raise HTTPException(status_code=400, detail=result.get("error", "Failed"))
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error in batch filter update: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/reset")
    async def reset_equalizer_filters():
        """Reset all EQ filters to flat (0 dB gain)."""
        try:
            filters = await equalizer_service.get_filters()
            for f in filters:
                await equalizer_service.set_filter(f["id"], gain=0.0)
            return {"status": "success", "message": "All filters reset to flat"}
        except Exception as e:
            logger.error(f"Error resetting filters: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # === Volume ===

    @router.get("/volume")
    async def get_volume():
        """Get equalizer volume settings."""
        return await equalizer_service.get_volume()

    @router.put("/volume")
    async def update_volume(update: VolumeUpdate):
        """Update equalizer volume."""
        try:
            success = await equalizer_service.set_volume(update.volume)
            if success:
                return {"status": "success", **equalizer_service.volume_state}
            else:
                raise HTTPException(status_code=400, detail="Failed to update volume")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating volume: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.put("/mute")
    async def update_mute(update: MuteUpdate):
        """Update equalizer mute state."""
        try:
            success = await equalizer_service.set_mute(update.muted)
            if success:
                return {"status": "success", "muted": equalizer_service.volume_state["mute"]}
            else:
                raise HTTPException(status_code=400, detail="Failed to update mute")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating mute: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # === Compressor ===

    @router.get("/compressor")
    async def get_compressor():
        """Get compressor settings."""
        return equalizer_service.compressor

    @router.put("/compressor")
    async def update_compressor(update: CompressorUpdate):
        """Update compressor settings."""
        try:
            success = await equalizer_service.set_compressor(
                enabled=update.enabled,
                threshold=update.threshold,
                ratio=update.ratio,
                attack=update.attack,
                release=update.release,
                makeup_gain=update.makeup_gain
            )
            if success:
                return {"status": "success", **equalizer_service.compressor}
            else:
                raise HTTPException(status_code=400, detail="Failed to update compressor")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating compressor: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # === Loudness ===

    @router.get("/loudness")
    async def get_loudness():
        """Get loudness compensation settings."""
        return equalizer_service.loudness

    @router.put("/loudness")
    async def update_loudness(update: LoudnessUpdate):
        """Update loudness compensation settings."""
        try:
            success = await equalizer_service.set_loudness(
                enabled=update.enabled,
                high_boost=update.high_boost,
                low_boost=update.low_boost
            )
            if success:
                return {"status": "success", **equalizer_service.loudness}
            else:
                raise HTTPException(status_code=400, detail="Failed to update loudness")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating loudness: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # === Delay ===

    @router.get("/delay")
    async def get_delay():
        """Get channel delay settings."""
        return equalizer_service.delay

    @router.put("/delay")
    async def update_delay(update: DelayUpdate):
        """Update channel delay settings."""
        try:
            success = await equalizer_service.set_delay(left=update.left, right=update.right)
            if success:
                return {"status": "success", **equalizer_service.delay}
            else:
                raise HTTPException(status_code=400, detail="Failed to update delay")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating delay: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # === Crossover ===

    @router.get("/crossover")
    async def get_crossover():
        """Get crossover highpass filter settings."""
        return equalizer_service.crossover

    @router.put("/crossover")
    async def update_crossover(update: CrossoverUpdate):
        """Update crossover highpass filter settings."""
        try:
            success = await equalizer_service.set_crossover(
                enabled=update.enabled,
                frequency=update.frequency,
                q=update.q
            )
            if success:
                return {"status": "success", **equalizer_service.crossover}
            else:
                raise HTTPException(status_code=400, detail="Failed to update crossover")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating crossover: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/lowpass")
    async def get_lowpass():
        """Get lowpass filter settings (for subwoofers)."""
        return equalizer_service.lowpass

    @router.put("/lowpass")
    async def update_lowpass(update: LowpassUpdate):
        """Update lowpass filter settings (for subwoofers)."""
        try:
            success = await equalizer_service.set_lowpass(
                enabled=update.enabled,
                frequency=update.frequency,
                q=update.q
            )
            if success:
                return {"status": "success", **equalizer_service.lowpass}
            else:
                raise HTTPException(status_code=400, detail="Failed to update lowpass")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating lowpass: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    return router
