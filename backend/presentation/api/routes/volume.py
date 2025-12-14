"""
API routes for volume management - All values in dB (-80 to 0)
"""
from fastapi import APIRouter, HTTPException
from backend.presentation.api.models import VolumeSetRequest, VolumeAdjustRequest


def create_volume_router(volume_service):
    """Creates volume router with dependency injection"""
    router = APIRouter(prefix="/api/volume", tags=["volume"])

    @router.get("/status")
    async def get_volume_status():
        """Gets current volume status"""
        try:
            status = await volume_service.get_status()
            return {"status": "success", "data": status}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @router.get("/")
    async def get_current_volume():
        """Gets current volume in dB"""
        try:
            volume_db = await volume_service.get_volume_db()
            return {"status": "success", "volume_db": volume_db}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/set")
    async def set_volume(request: VolumeSetRequest):
        """Sets volume in dB (-80 to 0)"""
        try:
            success = await volume_service.set_volume_db(request.volume_db, show_bar=request.show_bar)

            if success:
                volume_db = await volume_service.get_volume_db()
                return {"status": "success", "volume_db": volume_db}
            else:
                raise HTTPException(status_code=500, detail="Failed to set volume")

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/adjust")
    async def adjust_volume(request: VolumeAdjustRequest):
        """Adjusts volume by delta in dB"""
        try:
            success = await volume_service.adjust_volume_db(request.delta_db, show_bar=request.show_bar)

            if success:
                volume_db = await volume_service.get_volume_db()
                return {"status": "success", "volume_db": volume_db, "delta_db": request.delta_db}
            else:
                raise HTTPException(status_code=500, detail="Failed to adjust volume")

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/increase")
    async def increase_volume():
        """Increases volume by configured step (default 3 dB)"""
        try:
            step_db = volume_service.config.config.step_mobile_db
            success = await volume_service.adjust_volume_db(step_db)
            if success:
                volume_db = await volume_service.get_volume_db()
                return {"status": "success", "volume_db": volume_db, "delta_db": step_db}
            else:
                raise HTTPException(status_code=500, detail="Failed to increase volume")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/decrease")
    async def decrease_volume():
        """Decreases volume by configured step (default 3 dB)"""
        try:
            step_db = volume_service.config.config.step_mobile_db
            success = await volume_service.adjust_volume_db(-step_db)
            if success:
                volume_db = await volume_service.get_volume_db()
                return {"status": "success", "volume_db": volume_db, "delta_db": -step_db}
            else:
                raise HTTPException(status_code=500, detail="Failed to decrease volume")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    return router
