"""
DSP control routes for Milo Client.
"""
import logging
from fastapi import APIRouter, HTTPException

from services.dsp import DSPService
from models import (
    FilterUpdate, CompressorUpdate, LoudnessUpdate,
    DelayUpdate, VolumeUpdate, MuteUpdate,
    CrossoverUpdate, LowpassUpdate
)

logger = logging.getLogger(__name__)


def create_dsp_router(dsp_service: DSPService) -> APIRouter:
    """Creates DSP router with injected dependencies."""
    router = APIRouter(prefix="/dsp", tags=["dsp"])

    # === Status ===

    @router.get("/status")
    async def get_dsp_status():
        """Get DSP status and filter configuration."""
        try:
            status = await dsp_service.get_status()
            return status
        except Exception as e:
            logger.error(f"Error getting DSP status: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/filters")
    async def get_dsp_filters():
        """Get current EQ filter configuration."""
        try:
            filters = await dsp_service.get_filters()
            return {"filters": filters}
        except Exception as e:
            logger.error(f"Error getting filters: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/levels")
    async def get_levels():
        """Get real-time audio levels (peak/RMS)."""
        return await dsp_service.get_levels()

    # === Filters ===

    @router.put("/filter/{filter_id}")
    async def update_dsp_filter(filter_id: str, update: FilterUpdate):
        """Update a single EQ filter band."""
        try:
            success = await dsp_service.set_filter(
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

    @router.post("/reset")
    async def reset_dsp_filters():
        """Reset all EQ filters to flat (0 dB gain)."""
        try:
            filters = await dsp_service.get_filters()
            for f in filters:
                await dsp_service.set_filter(f["id"], gain=0.0)
            return {"status": "success", "message": "All filters reset to flat"}
        except Exception as e:
            logger.error(f"Error resetting filters: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # === Volume ===

    @router.get("/volume")
    async def get_volume():
        """Get DSP volume settings."""
        return await dsp_service.get_volume()

    @router.put("/volume")
    async def update_volume(update: VolumeUpdate):
        """Update DSP volume."""
        try:
            success = await dsp_service.set_volume(update.volume)
            if success:
                return {"status": "success", **dsp_service.volume_state}
            else:
                raise HTTPException(status_code=400, detail="Failed to update volume")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating volume: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.put("/mute")
    async def update_mute(update: MuteUpdate):
        """Update DSP mute state."""
        try:
            success = await dsp_service.set_mute(update.muted)
            if success:
                return {"status": "success", "muted": dsp_service.volume_state["mute"]}
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
        return dsp_service.compressor

    @router.put("/compressor")
    async def update_compressor(update: CompressorUpdate):
        """Update compressor settings."""
        try:
            success = await dsp_service.set_compressor(
                enabled=update.enabled,
                threshold=update.threshold,
                ratio=update.ratio,
                attack=update.attack,
                release=update.release,
                makeup_gain=update.makeup_gain
            )
            if success:
                return {"status": "success", **dsp_service.compressor}
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
        return dsp_service.loudness

    @router.put("/loudness")
    async def update_loudness(update: LoudnessUpdate):
        """Update loudness compensation settings."""
        try:
            success = await dsp_service.set_loudness(
                enabled=update.enabled,
                reference_level=update.reference_level,
                high_boost=update.high_boost,
                low_boost=update.low_boost
            )
            if success:
                return {"status": "success", **dsp_service.loudness}
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
        return dsp_service.delay

    @router.put("/delay")
    async def update_delay(update: DelayUpdate):
        """Update channel delay settings."""
        try:
            success = await dsp_service.set_delay(left=update.left, right=update.right)
            if success:
                return {"status": "success", **dsp_service.delay}
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
        return dsp_service.crossover

    @router.put("/crossover")
    async def update_crossover(update: CrossoverUpdate):
        """Update crossover highpass filter settings."""
        try:
            success = await dsp_service.set_crossover(
                enabled=update.enabled,
                frequency=update.frequency,
                q=update.q
            )
            if success:
                return {"status": "success", **dsp_service.crossover}
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
        return dsp_service.lowpass

    @router.put("/lowpass")
    async def update_lowpass(update: LowpassUpdate):
        """Update lowpass filter settings (for subwoofers)."""
        try:
            success = await dsp_service.set_lowpass(
                enabled=update.enabled,
                frequency=update.frequency,
                q=update.q
            )
            if success:
                return {"status": "success", **dsp_service.lowpass}
            else:
                raise HTTPException(status_code=400, detail="Failed to update lowpass")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating lowpass: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    return router
