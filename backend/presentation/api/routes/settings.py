# backend/presentation/api/routes/settings.py
"""
Routes Settings - Version OPTIM avec pattern unifié
"""
from fastapi import APIRouter, HTTPException
from typing import Dict, Any, Callable, Optional
from backend.infrastructure.services.settings_service import SettingsService

def create_settings_router(ws_manager, volume_service, state_machine, screen_controller):
    """Router settings avec pattern unifié"""
    router = APIRouter()
    settings = SettingsService()
    
    async def _handle_setting_update(
        payload: Dict[str, Any], 
        validator: Callable[[Any], bool],
        setter: Callable[[], bool],
        event_type: str,
        event_data: Dict[str, Any],
        reload_callback: Optional[Callable] = None
    ) -> Dict[str, Any]:
        """Pattern unifié pour toutes les routes settings"""
        try:
            if not validator(payload):
                raise HTTPException(status_code=400, detail="Invalid payload")
            
            if not setter():
                raise HTTPException(status_code=500, detail="Failed to save")
            
            reload_success = True
            if reload_callback:
                try:
                    reload_success = await reload_callback()
                except Exception:
                    reload_success = False
            
            await ws_manager.broadcast_dict({
                "category": "settings",
                "type": event_type,
                "source": "settings",
                "data": {**event_data, "reload_success": reload_success}
            })
            
            return {"status": "success", **event_data, "reload_success": reload_success}
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    # Language
    @router.get("/language")
    async def get_language():
        return {"status": "success", "language": settings.get_setting('language') or 'french'}
    
    @router.post("/language")
    async def set_language(payload: Dict[str, Any]):
        new_language = payload.get('language')
        
        return await _handle_setting_update(
            payload,
            validator=lambda p: p.get('language') in ['french', 'english', 'spanish', 'hindi', 'chinese', 'portuguese'],
            setter=lambda: settings.set_setting('language', new_language),
            event_type="language_changed",
            event_data={"language": new_language}
        )
    
    # Volume limits
    @router.get("/volume-limits")
    async def get_volume_limits():
        vol = settings.get_setting('volume') or {}
        return {
            "status": "success",
            "limits": {
                "alsa_min": vol.get("alsa_min", 0),
                "alsa_max": vol.get("alsa_max", 65),
                "limits_enabled": vol.get("limits_enabled", True)
            }
        }
    
    @router.post("/volume-limits")
    async def set_volume_limits(payload: Dict[str, Any]):
        alsa_min = payload.get('alsa_min')
        alsa_max = payload.get('alsa_max')
        
        return await _handle_setting_update(
            payload,
            validator=lambda p: (
                p.get('alsa_min') is not None and p.get('alsa_max') is not None and
                0 <= p['alsa_min'] <= 100 and 0 <= p['alsa_max'] <= 100 and
                p['alsa_max'] - p['alsa_min'] >= 10
            ),
            setter=lambda: (
                settings.set_setting('volume.alsa_min', alsa_min) and
                settings.set_setting('volume.alsa_max', alsa_max)
            ),
            event_type="volume_limits_changed",
            event_data={"limits": {"alsa_min": alsa_min, "alsa_max": alsa_max}},
            reload_callback=volume_service.reload_volume_limits
        )
    
    @router.post("/volume-limits/toggle")
    async def toggle_volume_limits(payload: Dict[str, Any]):
        enabled = payload.get('enabled')
        
        return await _handle_setting_update(
            payload,
            validator=lambda p: isinstance(p.get('enabled'), bool),
            setter=lambda: settings.set_setting('volume.limits_enabled', enabled),
            event_type="volume_limits_toggled",
            event_data={"limits_enabled": enabled},
            reload_callback=volume_service.reload_volume_limits
        )
    
    # Volume startup
    @router.get("/volume-startup")
    async def get_volume_startup():
        vol = settings.get_setting('volume') or {}
        return {
            "status": "success",
            "config": {
                "startup_volume": vol.get("startup_volume", 37),
                "restore_last_volume": vol.get("restore_last_volume", False)
            }
        }
    
    @router.post("/volume-startup")
    async def set_volume_startup(payload: Dict[str, Any]):
        startup_volume = payload.get('startup_volume')
        restore_last_volume = payload.get('restore_last_volume')
        
        return await _handle_setting_update(
            payload,
            validator=lambda p: (
                p.get('startup_volume') is not None and p.get('restore_last_volume') is not None and
                0 <= p['startup_volume'] <= 100 and isinstance(p['restore_last_volume'], bool)
            ),
            setter=lambda: (
                settings.set_setting('volume.startup_volume', startup_volume) and
                settings.set_setting('volume.restore_last_volume', restore_last_volume)
            ),
            event_type="volume_startup_changed",
            event_data={"config": {"startup_volume": startup_volume, "restore_last_volume": restore_last_volume}},
            reload_callback=volume_service.reload_startup_config
        )
    
    # Spotify
    @router.get("/spotify-disconnect")
    async def get_spotify_disconnect():
        spotify = settings.get_setting('spotify') or {}
        return {
            "status": "success",
            "config": {"auto_disconnect_delay": spotify.get("auto_disconnect_delay", 10.0)}
        }
    
    @router.post("/spotify-disconnect")
    async def set_spotify_disconnect(payload: Dict[str, Any]):
        delay = payload.get('auto_disconnect_delay')
        
        async def apply_to_plugin():
            try:
                from backend.domain.audio_state import AudioSource
                plugin = state_machine.get_plugin(AudioSource.LIBRESPOT)
                if plugin:
                    return await plugin.set_auto_disconnect_config(enabled=True, delay=delay, save_to_settings=False)
            except Exception:
                pass
            return False
        
        return await _handle_setting_update(
            payload,
            validator=lambda p: p.get('auto_disconnect_delay') is not None and 1.0 <= p['auto_disconnect_delay'] <= 300.0,
            setter=lambda: settings.set_setting('spotify.auto_disconnect_delay', delay),
            event_type="spotify_disconnect_changed",
            event_data={"config": {"auto_disconnect_delay": delay}},
            reload_callback=apply_to_plugin
        )
    
    # Screen timeout
    @router.get("/screen-timeout")
    async def get_screen_timeout():
        screen = settings.get_setting('screen') or {}
        return {
            "status": "success",
            "config": {"screen_timeout_seconds": screen.get("timeout_seconds", 10)}
        }
    
    @router.post("/screen-timeout")
    async def set_screen_timeout(payload: Dict[str, Any]):
        timeout_seconds = payload.get('screen_timeout_seconds')
        
        return await _handle_setting_update(
            payload,
            validator=lambda p: p.get('screen_timeout_seconds') is not None and 3 <= p['screen_timeout_seconds'] <= 3600,
            setter=lambda: settings.set_setting('screen.timeout_seconds', timeout_seconds),
            event_type="screen_timeout_changed",
            event_data={"config": {"screen_timeout_seconds": timeout_seconds}},
            reload_callback=screen_controller.reload_timeout_config
        )
    
    # Screen brightness
    @router.get("/screen-brightness")
    async def get_screen_brightness():
        screen = settings.get_setting('screen') or {}
        return {
            "status": "success",
            "config": {"brightness_on": screen.get("brightness_on", 5)}
        }
    
    @router.post("/screen-brightness")
    async def set_screen_brightness(payload: Dict[str, Any]):
        brightness_on = payload.get('brightness_on')
        
        return await _handle_setting_update(
            payload,
            validator=lambda p: p.get('brightness_on') is not None and 1 <= p['brightness_on'] <= 10,
            setter=lambda: settings.set_setting('screen.brightness_on', brightness_on),
            event_type="screen_brightness_changed",
            event_data={"config": {"brightness_on": brightness_on}},
            reload_callback=screen_controller.reload_timeout_config
        )
    
    @router.post("/screen-brightness/apply")
    async def apply_brightness_instantly(payload: Dict[str, Any]):
        """Application instantanée de luminosité"""
        try:
            brightness_on = payload.get('brightness_on')
            
            if not brightness_on or not (1 <= brightness_on <= 10):
                raise HTTPException(status_code=400, detail="brightness_on must be between 1 and 10")
            
            import asyncio
            import os
            
            backlight_binary = os.path.expanduser("~/RPi-USB-Brightness/64/lite/Raspi_USB_Backlight_nogui -b")
            cmd = f"sudo {backlight_binary} {brightness_on}"
            
            process = await asyncio.create_subprocess_shell(
                cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode == 0:
                return {
                    "status": "success",
                    "brightness_applied": brightness_on,
                    "command_output": stdout.decode().strip()
                }
            else:
                return {
                    "status": "error",
                    "message": f"Command failed: {stderr.decode().strip()}"
                }
            
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    
    return router