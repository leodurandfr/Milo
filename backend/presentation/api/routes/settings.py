# backend/presentation/api/routes/settings.py
"""
Routes API pour la gestion des settings unifiés - Version avec Spotify et Screen timeout
"""
from fastapi import APIRouter, HTTPException
from typing import Dict, Any
from backend.infrastructure.services.settings_service import SettingsService
from backend.domain.audio_state import AudioSource

def create_settings_router(ws_manager, volume_service, state_machine, screen_controller):
    """Crée le router settings avec injection de dépendances"""
    router = APIRouter()
    settings_service = SettingsService()
    
    @router.get("/volume-config")
    async def get_volume_config():
        """Récupère la configuration complète du volume pour le frontend"""
        try:
            config = volume_service.get_volume_config_public()
            return {"status": "success", "config": config}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
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
                    "alsa_max": volume_config["alsa_max"],
                    "limits_enabled": volume_config["limits_enabled"]
                }
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    @router.post("/volume-limits")
    async def set_volume_limits(payload: Dict[str, Any]):
        """Définit les limites de volume avec application immédiate"""
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
    
    @router.post("/volume-limits/toggle")
    async def toggle_volume_limits(payload: Dict[str, Any]):
        """Active/désactive les limites de volume avec application immédiate"""
        try:
            enabled = payload.get('enabled')
            
            if enabled is None:
                raise HTTPException(status_code=400, detail="enabled required")
            
            if not isinstance(enabled, bool):
                raise HTTPException(status_code=400, detail="enabled must be boolean")
            
            # Sauvegarder dans les settings
            success = settings_service.set_volume_limits_enabled(enabled)
            if not success:
                raise HTTPException(status_code=500, detail="Failed to save volume limits toggle")
            
            # Rechargement à chaud du VolumeService 
            reload_success = await volume_service.reload_volume_limits()
            
            # Diffusion WebSocket
            await ws_manager.broadcast_dict({
                "category": "settings",
                "type": "volume_limits_toggled",
                "source": "settings",
                "data": {
                    "limits_enabled": enabled,
                    "reload_success": reload_success
                }
            })
            
            return {
                "status": "success",
                "limits_enabled": enabled,
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
            
            # Synchroniser avec Snapcast si multiroom activé
            snapcast_sync_success = False
            try:
                if hasattr(volume_service.state_machine, 'routing_service') and volume_service.state_machine.routing_service:
                    routing_state = volume_service.state_machine.routing_service.get_state()
                    if routing_state.multiroom_enabled:
                        snapcast_sync_success = await volume_service.sync_current_volume_to_all_snapcast_clients()
            except Exception:
                pass
            
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
                    "reload_success": reload_success,
                    "snapcast_sync_success": snapcast_sync_success
                }
            })
            
            return {
                "status": "success",
                "config": {
                    "startup_volume": startup_volume,
                    "restore_last_volume": restore_last_volume
                },
                "reload_success": reload_success,
                "snapcast_sync_success": snapcast_sync_success
            }
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/volume-startup/sync-snapcast")
    async def sync_volume_to_snapcast():
        """Synchronise le volume actuel avec tous les clients Snapcast"""
        try:
            success = await volume_service.sync_current_volume_to_all_snapcast_clients()
            
            if success:
                # Diffusion WebSocket
                await ws_manager.broadcast_dict({
                    "category": "settings",
                    "type": "snapcast_volume_synced",
                    "source": "settings",
                    "data": {"sync_success": True}
                })
                
                return {"status": "success", "message": "Volume synchronized to all Snapcast clients"}
            else:
                return {"status": "error", "message": "Failed to synchronize volume (multiroom may be disabled)"}
                
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    # NOUVEAU : Routes Spotify
    @router.get("/spotify-disconnect")
    async def get_spotify_disconnect_config():
        """Récupère la configuration de déconnexion automatique Spotify"""
        try:
            spotify_config = settings_service.get_spotify_config()
            return {
                "status": "success",
                "config": {
                    "auto_disconnect_delay": spotify_config["auto_disconnect_delay"]
                }
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    @router.post("/spotify-disconnect")
    async def set_spotify_disconnect_config(payload: Dict[str, Any]):
        """Configure la déconnexion automatique Spotify avec application immédiate"""
        try:
            delay = payload.get('auto_disconnect_delay')
            
            if delay is None:
                raise HTTPException(status_code=400, detail="auto_disconnect_delay required")
            
            # Validation : 1 seconde à 5 minutes
            if not (1.0 <= delay <= 300.0):
                raise HTTPException(status_code=400, detail="auto_disconnect_delay must be between 1 and 300 seconds")
            
            # Sauvegarder dans les settings
            success = settings_service.set_spotify_disconnect_delay(delay)
            if not success:
                raise HTTPException(status_code=500, detail="Failed to save Spotify disconnect config")
            
            # Application immédiate sur le plugin librespot (logique agressive)
            apply_success = False
            try:
                librespot_plugin = state_machine.get_plugin(AudioSource.LIBRESPOT)
                if librespot_plugin:
                    apply_success = await librespot_plugin.set_auto_disconnect_config(
                        enabled=True, 
                        delay=delay, 
                        save_to_settings=False  # Déjà sauvé ci-dessus
                    )
            except Exception as e:
                self.logger.error(f"Error applying Spotify config to plugin: {e}")
            
            # Diffusion WebSocket
            await ws_manager.broadcast_dict({
                "category": "settings",
                "type": "spotify_disconnect_changed",
                "source": "settings",
                "data": {
                    "config": {"auto_disconnect_delay": delay},
                    "apply_success": apply_success
                }
            })
            
            return {
                "status": "success",
                "config": {"auto_disconnect_delay": delay},
                "apply_success": apply_success
            }
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    # NOUVEAU : Routes Screen timeout et luminosité
    @router.get("/screen-timeout")
    async def get_screen_timeout_config():
        """Récupère la configuration du timeout d'écran"""
        try:
            screen_config = settings_service.get_screen_config()
            return {
                "status": "success",
                "config": {
                    "screen_timeout_seconds": screen_config["timeout_seconds"]
                }
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    @router.post("/screen-timeout")
    async def set_screen_timeout_config(payload: Dict[str, Any]):
        """Configure le timeout d'écran avec application immédiate"""
        try:
            timeout_seconds = payload.get('screen_timeout_seconds')
            
            if timeout_seconds is None:
                raise HTTPException(status_code=400, detail="screen_timeout_seconds required")
            
            # Validation : 3 secondes à 60 minutes
            if not (3 <= timeout_seconds <= 3600):
                raise HTTPException(status_code=400, detail="screen_timeout_seconds must be between 3 and 3600 seconds")
            
            # Sauvegarder dans les settings
            success = settings_service.set_screen_timeout(timeout_seconds)
            if not success:
                raise HTTPException(status_code=500, detail="Failed to save screen timeout config")
            
            # Application immédiate sur le ScreenController (rechargement à chaud)
            reload_success = False
            try:
                reload_success = await screen_controller.reload_screen_config()
            except Exception as e:
                print(f"Error applying screen timeout to controller: {e}")
            
            # Diffusion WebSocket
            await ws_manager.broadcast_dict({
                "category": "settings",
                "type": "screen_timeout_changed",
                "source": "settings",
                "data": {
                    "config": {"screen_timeout_seconds": timeout_seconds},
                    "reload_success": reload_success
                }
            })
            
            return {
                "status": "success",
                "config": {"screen_timeout_seconds": timeout_seconds},
                "reload_success": reload_success
            }
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.get("/screen-brightness")
    async def get_screen_brightness_config():
        """Récupère la configuration de luminosité d'écran"""
        try:
            screen_config = settings_service.get_screen_config()
            return {
                "status": "success",
                "config": {
                    "brightness_on": screen_config["brightness_on"]
                }
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}
    
    @router.post("/screen-brightness")
    async def set_screen_brightness_config(payload: Dict[str, Any]):
        """Configure la luminosité d'écran avec application immédiate"""
        try:
            brightness_on = payload.get('brightness_on')
            
            if brightness_on is None:
                raise HTTPException(status_code=400, detail="brightness_on required")
            
            # Validation : brightness_on entre 1 et 10
            if not (1 <= brightness_on <= 10):
                raise HTTPException(status_code=400, detail="brightness_on must be between 1 and 10")
            
            # Sauvegarder dans les settings
            success = settings_service.set_screen_brightness(brightness_on)
            if not success:
                raise HTTPException(status_code=500, detail="Failed to save screen brightness config")
            
            # Application immédiate sur le ScreenController
            reload_success = False
            try:
                reload_success = await screen_controller.reload_screen_config()
            except Exception as e:
                print(f"Error applying screen brightness to controller: {e}")
            
            # Diffusion WebSocket
            await ws_manager.broadcast_dict({
                "category": "settings",
                "type": "screen_brightness_changed",
                "source": "settings",
                "data": {
                    "config": {"brightness_on": brightness_on},
                    "reload_success": reload_success
                }
            })
            
            return {
                "status": "success",
                "config": {"brightness_on": brightness_on},
                "reload_success": reload_success
            }
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    @router.post("/screen-brightness/apply")
    async def apply_screen_brightness_instantly(payload: Dict[str, Any]):
        """Applique immédiatement la luminosité avec exécution directe de la commande"""
        try:
            brightness_on = payload.get('brightness_on')
            
            if brightness_on is None:
                raise HTTPException(status_code=400, detail="brightness_on required")
            
            if not (1 <= brightness_on <= 10):
                raise HTTPException(status_code=400, detail="brightness_on must be between 1 and 10")
            
            # Exécution directe de la commande système (comme votre script bash)
            import asyncio
            import os
            
            backlight_binary = os.path.expanduser("~/RPi-USB-Brightness/64/lite/Raspi_USB_Backlight_nogui -b")
            cmd = f"sudo {backlight_binary} {brightness_on}"
            
            print(f"[INSTANT_BRIGHTNESS] Executing: {cmd}")
            
            process = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode == 0:
                print(f"[INSTANT_BRIGHTNESS] Success: {stdout.decode().strip()}")
                
                # Optionnel : Mettre à jour l'état du ScreenController
                try:
                    screen_controller.brightness_on = brightness_on
                    screen_controller.screen_on = True  # Marquer comme allumé
                    screen_controller.last_activity_time = time.monotonic()
                except:
                    pass  # Pas grave si ça échoue
                
                return {
                    "status": "success",
                    "brightness_applied": brightness_on,
                    "command_output": stdout.decode().strip()
                }
            else:
                print(f"[INSTANT_BRIGHTNESS] Failed: {stderr.decode().strip()}")
                return {
                    "status": "error",
                    "message": f"Command failed: {stderr.decode().strip()}"
                }
            
        except HTTPException:
            raise
        except Exception as e:
            print(f"[INSTANT_BRIGHTNESS] Exception: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))
    
    return router