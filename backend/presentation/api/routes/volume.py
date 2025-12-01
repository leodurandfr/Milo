"""
API routes for volume management - Display volume (0-100%) with validation
"""
from fastapi import APIRouter, HTTPException
from backend.presentation.api.models import VolumeSetRequest, VolumeAdjustRequest

def create_volume_router(volume_service):
    """Creates volume router with dependency injection"""
    router = APIRouter(prefix="/api/volume", tags=["volume"])

    @router.get("/status")
    async def get_volume_status():
        """Gets current volume status (display volume)"""
        try:
            status = await volume_service.get_status()
            return {"status": "success", "data": status}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @router.get("/")
    async def get_current_volume():
        """Gets current display volume (0-100%)"""
        try:
            volume = await volume_service.get_display_volume()
            return {"status": "success", "volume": volume}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/set")
    async def set_volume(request: VolumeSetRequest):
        """Sets display volume (0-100%) with validation"""
        try:
            success = await volume_service.set_display_volume(request.volume, show_bar=request.show_bar)

            if success:
                return {"status": "success", "volume": request.volume}
            else:
                raise HTTPException(status_code=500, detail="Failed to set volume")

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/adjust")
    async def adjust_volume(request: VolumeAdjustRequest):
        """Adjusts display volume by delta with validation"""
        try:
            success = await volume_service.adjust_display_volume(request.delta, show_bar=request.show_bar)

            if success:
                current_volume = await volume_service.get_display_volume()
                return {"status": "success", "volume": current_volume, "delta": request.delta}
            else:
                raise HTTPException(status_code=500, detail="Failed to adjust volume")

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/increase")
    async def increase_volume():
        """Increases display volume by 5%"""
        try:
            success = await volume_service.adjust_display_volume(5)
            if success:
                current_volume = await volume_service.get_display_volume()
                return {"status": "success", "volume": current_volume}
            else:
                raise HTTPException(status_code=500, detail="Failed to increase volume")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/decrease")
    async def decrease_volume():
        """Decreases display volume by 5%"""
        try:
            success = await volume_service.adjust_display_volume(-5)
            if success:
                current_volume = await volume_service.get_display_volume()
                return {"status": "success", "volume": current_volume}
            else:
                raise HTTPException(status_code=500, detail="Failed to decrease volume")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    return router