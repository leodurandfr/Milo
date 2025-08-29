# backend/presentation/api/routes/settings.py - Version settings unifiées
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, validator
from typing import Literal, Dict, Any
from backend.infrastructure.services.settings_service import SettingsService

# Modèles de requête
class LanguageRequest(BaseModel):
    language: Literal['french', 'english', 'spanish', 'hindi', 'chinese', 'portuguese']

class VolumeLimitsRequest(BaseModel):
    alsa_min: int
    alsa_max: int
    
    @validator('alsa_min')
    def validate_alsa_min(cls, v):
        if not 0 <= v <= 100:
            raise ValueError('alsa_min must be between 0 and 100')
        return v
    
    @validator('alsa_max')
    def validate_alsa_max(cls, v):
        if not 0 <= v <= 100:
            raise ValueError('alsa_max must be between 0 and 100')
        return v
    
    @validator('alsa_max')
    def validate_volume_range(cls, v, values):
        alsa_min = values.get('alsa_min', 0)
        if v - alsa_min < 10:
            raise ValueError('Volume range must be at least 10 (alsa_max - alsa_min >= 10)')
        return v

def create_settings_router(ws_manager=None, volume_service=None):
    """Créer le router settings unifié"""
    router = APIRouter()
    settings_service = SettingsService()
    
    # === ENDPOINTS GÉNÉRAUX ===
    
    @router.get("/all")
    async def get_all_settings():
        """Récupère toutes les settings"""
        try:
            settings = settings_service.load_settings()
            return {
                "status": "success",
                "settings": settings
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error loading settings: {str(e)}")
    
    # === LANGUE ===
    
    @router.get("/language")
    async def get_current_language():
        """Récupère la langue actuellement configurée"""
        try:
            language = settings_service.get_setting('language')
            return {
                "status": "success",
                "language": language
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error getting language: {str(e)}")

    @router.post("/language")
    async def set_language(request: LanguageRequest):
        """Change la langue et diffuse via WebSocket"""
        try:
            new_language = request.language
            
            # Sauvegarder
            success = settings_service.set_setting('language', new_language)
            if not success:
                raise HTTPException(status_code=500, detail="Failed to save language")
            
            # Diffuser via WebSocket
            if ws_manager and hasattr(ws_manager, 'broadcast_dict'):
                message = {
                    'category': 'settings',
                    'type': 'language_changed',
                    'data': {'language': new_language}
                }
                await ws_manager.broadcast_dict(message)
            
            return {
                "status": "success",
                "message": f"Language changed to {new_language}",
                "language": new_language
            }
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error setting language: {str(e)}")
    
    # === LIMITES DE VOLUME ===
    
    @router.get("/volume-limits")
    async def get_volume_limits():
        """Récupère les limites de volume actuelles"""
        try:
            volume_config = settings_service.get_volume_config()
            return {
                "status": "success",
                "volume_config": volume_config,
                "limits": {
                    "alsa_min": volume_config["alsa_min"],
                    "alsa_max": volume_config["alsa_max"]
                }
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error getting volume limits: {str(e)}")
    
    @router.post("/volume-limits")
    async def set_volume_limits(request: VolumeLimitsRequest):
        """Définit les nouvelles limites de volume"""
        try:
            # Sauvegarder les nouvelles limites
            success = settings_service.set_volume_limits(request.alsa_min, request.alsa_max)
            if not success:
                raise HTTPException(status_code=500, detail="Failed to save volume limits")
            
            # Recharger la configuration volume du VolumeService
            if volume_service:
                try:
                    await volume_service.reload_volume_config()
                except Exception as reload_error:
                    # Log l'erreur mais ne pas faire échouer la requête
                    print(f"Warning: Volume service reload failed: {reload_error}")
            
            # Diffuser via WebSocket
            if ws_manager and hasattr(ws_manager, 'broadcast_dict'):
                volume_config = settings_service.get_volume_config()
                message = {
                    'category': 'settings',
                    'type': 'volume_limits_changed',
                    'data': {
                        'volume_config': volume_config,
                        'limits': {
                            'alsa_min': request.alsa_min,
                            'alsa_max': request.alsa_max
                        }
                    }
                }
                await ws_manager.broadcast_dict(message)
            
            return {
                "status": "success",
                "message": f"Volume limits updated: {request.alsa_min}-{request.alsa_max}",
                "limits": {
                    "alsa_min": request.alsa_min,
                    "alsa_max": request.alsa_max
                }
            }
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error setting volume limits: {str(e)}")
    
    return router