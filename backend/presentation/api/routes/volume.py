# backend/presentation/api/routes/volume.py
"""
Routes API pour la gestion du volume - Version volume affiché (0-100%)
"""
from fastapi import APIRouter, HTTPException
from typing import Dict, Any

def create_volume_router(volume_service):
    """Crée le router volume avec injection de dépendances"""
    router = APIRouter(prefix="/api/volume", tags=["volume"])
    
    @router.get("/status")
    async def get_volume_status():
        """Récupère l'état actuel du volume (en volume affiché)"""
        try:
            status = await volume_service.get_status()
            return {"status": "success", "data": status}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    @router.get("/")
    async def get_current_volume():
        """Récupère le volume affiché actuel (0-100%)"""
        try:
            volume = await volume_service.get_display_volume()
            return {"status": "success", "volume": volume}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/set")
    async def set_volume(payload: Dict[str, Any]):
        """Définit le volume affiché (0-100%)"""
        try:
            volume = payload.get("volume")
            show_bar = payload.get("show_bar", True)
            
            if volume is None or not isinstance(volume, (int, float)):
                raise HTTPException(status_code=400, detail="Invalid volume value")
            
            if not (0 <= volume <= 100):
                raise HTTPException(status_code=400, detail="Volume must be between 0 and 100")
            
            success = await volume_service.set_display_volume(int(volume), show_bar=show_bar)
            
            if success:
                return {"status": "success", "volume": int(volume)}
            else:
                raise HTTPException(status_code=500, detail="Failed to set volume")
                
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/adjust")
    async def adjust_volume(payload: Dict[str, Any]):
        """Ajuste le volume affiché par delta"""
        try:
            delta = payload.get("delta")
            show_bar = payload.get("show_bar", True)
            
            if delta is None or not isinstance(delta, (int, float)):
                raise HTTPException(status_code=400, detail="Invalid delta value")
            
            success = await volume_service.adjust_display_volume(int(delta), show_bar=show_bar)
            
            if success:
                current_volume = await volume_service.get_display_volume()
                return {"status": "success", "volume": current_volume, "delta": int(delta)}
            else:
                raise HTTPException(status_code=500, detail="Failed to adjust volume")
                
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/increase")
    async def increase_volume():
        """Augmente le volume affiché de 5%"""
        try:
            success = await volume_service.increase_display_volume(5)
            if success:
                current_volume = await volume_service.get_display_volume()
                return {"status": "success", "volume": current_volume}
            else:
                raise HTTPException(status_code=500, detail="Failed to increase volume")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/decrease")
    async def decrease_volume():
        """Diminue le volume affiché de 5%"""
        try:
            success = await volume_service.decrease_display_volume(5)
            if success:
                current_volume = await volume_service.get_display_volume()
                return {"status": "success", "volume": current_volume}
            else:
                raise HTTPException(status_code=500, detail="Failed to decrease volume")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    return router