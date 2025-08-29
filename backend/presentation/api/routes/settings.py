# backend/presentation/api/routes/settings.py
"""
Routes API pour la gestion des settings unifiés - Version avec startup volume
"""
from fastapi import APIRouter, HTTPException
from typing import Dict, Any
from backend.infrastructure.services.settings_service import SettingsService

def create_settings_router(ws_manager, volume_service):
    """Crée le router settings avec injection de dépendances"""
    router = APIRouter()
    settings_service = SettingsService()
    
    @router.get("/language")
    async def get_current_language():
        """Récupère la langue actuelle"""
        try:
            language = settings_service.get_setting('language')
            return {"status": "success", "language": language or 'french'}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    @router.post("/language")
    async def set_language(payload: Dict[str, Any]):
        """Change la langue avec diffusion WebSocket"""
        try:
            new_language = payload.get('language')
            if not new_language:
                raise HTTPException(status_code=400, detail="Language required")
            
            # Valider la langue
            valid_languages = ['french', 'english', 'spanish', 'hindi', 'chinese', 'portuguese']
            if new_language not in valid_languages:
                raise HTTPException(status_code=400, detail="Invalid language")
            
            # Sauvegarder
            success = settings_service.set_setting('language', new_language)
            if not success:
                raise HTTPException(status_code=500, detail="Failed to save language")
            
            # Diffusion WebSocket
            await ws_manager.broadcast_dict({
                "category": "settings",
                "type": "language_changed",
                "source": "settings",
                "data": {"language": new_language}
            })
            
            return {"status": "success", "language": new_language}
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.get("/volume-limits")
    async def get_volume_limits():
        """Récupère les limites de volume actuelles"""
        try:
            volume_config = settings_service.get_volume_config()
            return {
                "status": "success",
                "limits": {
                    "alsa_min": volume_config["alsa_min"],
                    "alsa_max": volume_config["alsa_max"]
                }
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    @router.post("/volume-limits")
    async def set_volume_limits(payload: Dict[str, Any]):
        """Définit les limites de volume avec rechargement à chaud"""
        try:
            alsa_min = payload.get('alsa_min')
            alsa_max = payload.get('alsa_max')
            
            if alsa_min is None or alsa_max is None:
                raise HTTPException(status_code=400, detail="alsa_min and alsa_max required")
            
            # Validation
            if not (0 <= alsa_min <= 100) or not (0 <= alsa_max <= 100):
                raise HTTPException(status_code=400, detail="Volume limits must be between 0 and 100")
            
            if alsa_max - alsa_min < 10:
                raise HTTPException(status_code=400, detail="Volume range must be at least 10")
            
            # Sauvegarder dans les settings
            success = settings_service.set_volume_limits(alsa_min, alsa_max)
            if not success:
                raise HTTPException(status_code=500, detail="Failed to save volume limits")
            
            # Rechargement à chaud du VolumeService (pour les limites)
            reload_success = await volume_service.reload_volume_limits()
            
            # Diffusion WebSocket
            await ws_manager.broadcast_dict({
                "category": "settings",
                "type": "volume_limits_changed",
                "source": "settings",
                "data": {
                    "limits": {"alsa_min": alsa_min, "alsa_max": alsa_max},
                    "reload_success": reload_success
                }
            })
            
            return {
                "status": "success",
                "limits": {"alsa_min": alsa_min, "alsa_max": alsa_max},
                "reload_success": reload_success
            }
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.get("/volume-startup")
    async def get_volume_startup_config():
        """Récupère la configuration du volume au démarrage"""
        try:
            volume_config = settings_service.get_volume_config()
            return {
                "status": "success",
                "config": {
                    "startup_volume": volume_config["startup_volume"],
                    "restore_last_volume": volume_config["restore_last_volume"]
                }
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    @router.post("/volume-startup")
    async def set_volume_startup_config(payload: Dict[str, Any]):
        """Configure le volume au démarrage avec rechargement à chaud"""
        try:
            startup_volume = payload.get('startup_volume')
            restore_last_volume = payload.get('restore_last_volume')
            
            if startup_volume is None or restore_last_volume is None:
                raise HTTPException(status_code=400, detail="startup_volume and restore_last_volume required")
            
            # Validation startup_volume
            if not (0 <= startup_volume <= 100):
                raise HTTPException(status_code=400, detail="startup_volume must be between 0 and 100")
            
            # Validation restore_last_volume
            if not isinstance(restore_last_volume, bool):
                raise HTTPException(status_code=400, detail="restore_last_volume must be boolean")
            
            # Sauvegarder les settings
            success = (
                settings_service.set_setting('volume.startup_volume', startup_volume) and
                settings_service.set_setting('volume.restore_last_volume', restore_last_volume)
            )
            
            if not success:
                raise HTTPException(status_code=500, detail="Failed to save startup volume config")
            
            # Rechargement à chaud de la config startup (sans toucher au volume actuel)
            reload_success = await volume_service.reload_startup_config()
            
            # Diffusion WebSocket
            await ws_manager.broadcast_dict({
                "category": "settings",
                "type": "volume_startup_changed",
                "source": "settings",
                "data": {
                    "config": {
                        "startup_volume": startup_volume,
                        "restore_last_volume": restore_last_volume
                    },
                    "reload_success": reload_success
                }
            })
            
            return {
                "status": "success",
                "config": {
                    "startup_volume": startup_volume,
                    "restore_last_volume": restore_last_volume
                },
                "reload_success": reload_success
            }
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    return router