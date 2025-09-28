# backend/presentation/api/routes/settings.py
"""
Routes Settings - Version COMPLÈTE avec mises à jour Phase 2A
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks
from typing import Dict, Any, Callable, Optional
from backend.infrastructure.services.settings_service import SettingsService
from backend.infrastructure.services.dependency_version_service import DependencyVersionService
from backend.infrastructure.services.dependency_update_service import DependencyUpdateService
import asyncio

def create_settings_router(ws_manager, volume_service, state_machine, screen_controller):
    """Router settings avec pattern unifié + support 0 = désactivé + température + dépendances + mises à jour"""
    router = APIRouter()
    settings = SettingsService()
    dependency_service = DependencyVersionService()
    update_service = DependencyUpdateService()
    
    # Store pour suivre les mises à jour en cours
    active_updates = {}
    
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
    
    # [... toutes les routes existantes restent identiques ...]
    
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
        
        if enabled:
            current_min = settings.get_setting('volume.alsa_min') or 0
            current_max = settings.get_setting('volume.alsa_max') or 65
        else:
            current_min = 0
            current_max = 100
        
        return await _handle_setting_update(
            payload,
            validator=lambda p: isinstance(p.get('enabled'), bool),
            setter=lambda: (
                settings.set_setting('volume.limits_enabled', enabled) and
                settings.set_setting('volume.alsa_min', current_min) and
                settings.set_setting('volume.alsa_max', current_max)
            ),
            event_type="volume_limits_toggled",
            event_data={
                "limits_enabled": enabled, 
                "limits": {
                    "alsa_min": current_min, 
                    "alsa_max": current_max
                }
            },
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
    
    # Volume steps
    @router.get("/volume-steps")
    async def get_volume_steps():
        vol = settings.get_setting('volume') or {}
        return {
            "status": "success",
            "config": {"mobile_volume_steps": vol.get("mobile_volume_steps", 5)}
        }
    
    @router.post("/volume-steps")
    async def set_volume_steps(payload: Dict[str, Any]):
        steps = payload.get('mobile_volume_steps')
        
        return await _handle_setting_update(
            payload,
            validator=lambda p: p.get('mobile_volume_steps') is not None and 1 <= p['mobile_volume_steps'] <= 10,
            setter=lambda: settings.set_setting('volume.mobile_volume_steps', steps),
            event_type="volume_steps_changed",
            event_data={"config": {"mobile_volume_steps": steps}},
            reload_callback=volume_service.reload_volume_steps_config
        )
    
    # Dock apps
    @router.get("/dock-apps")
    async def get_dock_apps():
        dock = settings.get_setting('dock') or {}
        enabled_apps = dock.get('enabled_apps', ["spotify", "bluetooth", "roc", "multiroom", "equalizer"])
        
        return {
            "status": "success",
            "config": {"enabled_apps": enabled_apps}
        }
    
    @router.post("/dock-apps")
    async def set_dock_apps(payload: Dict[str, Any]):
        enabled_apps = payload.get('enabled_apps', [])
        
        def validate_dock_apps(p):
            apps = p.get('enabled_apps', [])
            if not isinstance(apps, list):
                return False
            
            valid_apps = ["librespot", "bluetooth", "roc", "multiroom", "equalizer"]
            if not all(app in valid_apps for app in apps):
                return False
            
            audio_sources = ["librespot", "bluetooth", "roc"]
            enabled_audio_sources = [app for app in apps if app in audio_sources]
            return len(enabled_audio_sources) > 0
        
        return await _handle_setting_update(
            payload,
            validator=validate_dock_apps,
            setter=lambda: settings.set_setting('dock.enabled_apps', enabled_apps),
            event_type="dock_apps_changed",
            event_data={"config": {"enabled_apps": enabled_apps}}
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
                    enabled = delay != 0
                    return await plugin.set_auto_disconnect_config(enabled=enabled, delay=delay, save_to_settings=False)
            except Exception:
                pass
            return False
        
        return await _handle_setting_update(
            payload,
            validator=lambda p: (
                p.get('auto_disconnect_delay') is not None and
                (p['auto_disconnect_delay'] == 0 or (1.0 <= p['auto_disconnect_delay'] <= 9999))
            ),
            setter=lambda: settings.set_setting('spotify.auto_disconnect_delay', delay),
            event_type="spotify_disconnect_changed",
            event_data={"config": {"auto_disconnect_delay": delay}},
            reload_callback=apply_to_plugin
        )
    
    # Screen timeout
    @router.get("/screen-timeout")
    async def get_screen_timeout():
        screen = settings.get_setting('screen') or {}
        timeout_seconds = screen.get("timeout_seconds", 10)
        
        timeout_enabled = timeout_seconds != 0
        
        return {
            "status": "success",
            "config": {
                "screen_timeout_enabled": timeout_enabled,
                "screen_timeout_seconds": timeout_seconds
            }
        }
    
    @router.post("/screen-timeout")
    async def set_screen_timeout(payload: Dict[str, Any]):
        timeout_enabled = payload.get('screen_timeout_enabled')
        timeout_seconds = payload.get('screen_timeout_seconds')
        
        return await _handle_setting_update(
            payload,
            validator=lambda p: (
                p.get('screen_timeout_enabled') is not None and isinstance(p['screen_timeout_enabled'], bool) and
                p.get('screen_timeout_seconds') is not None and
                (p['screen_timeout_seconds'] == 0 or (3 <= p['screen_timeout_seconds'] <= 3600))
            ),
            setter=lambda: settings.set_setting('screen.timeout_seconds', timeout_seconds),
            event_type="screen_timeout_changed",
            event_data={"config": {"screen_timeout_enabled": timeout_enabled, "screen_timeout_seconds": timeout_seconds}},
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
        """Application instantanée de luminosité + restart timeout"""
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
                from time import monotonic
                screen_controller.last_activity_time = monotonic()
                screen_controller.screen_on = True
                
                return {
                    "status": "success",
                    "brightness_applied": brightness_on,
                    "command_output": stdout.decode().strip(),
                    "timeout_restarted": True
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
    
    # Température système + Throttling
    @router.get("/system-temperature")
    async def get_system_temperature():
        """Récupère la température du Raspberry Pi et le statut de throttling"""
        try:
            import asyncio
            
            temp_process = asyncio.create_subprocess_shell(
                "vcgencmd measure_temp",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            throttle_process = asyncio.create_subprocess_shell(
                "vcgencmd get_throttled",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            temp_proc, throttle_proc = await asyncio.gather(temp_process, throttle_process)
            temp_stdout, temp_stderr = await temp_proc.communicate()
            throttle_stdout, throttle_stderr = await throttle_proc.communicate()
            
            result = {"status": "success"}
            
            # Parser température
            if temp_proc.returncode == 0:
                temp_output = temp_stdout.decode().strip()
                if temp_output.startswith("temp=") and temp_output.endswith("'C"):
                    temp_str = temp_output.replace("temp=", "").replace("'C", "")
                    result["temperature"] = float(temp_str)
                    result["unit"] = "°C"
                else:
                    result["temperature"] = None
            else:
                result["temperature"] = None
            
            # Parser throttling
            throttle_status = {"code": "0x0", "current": [], "past": [], "severity": "ok"}
            
            if throttle_proc.returncode == 0:
                throttle_output = throttle_stdout.decode().strip()
                if throttle_output.startswith("throttled="):
                    throttle_code = throttle_output.replace("throttled=", "").strip()
                    throttle_status["code"] = throttle_code
                    
                    try:
                        throttle_value = int(throttle_code, 16)
                        
                        if throttle_value & 0x1:
                            throttle_status["current"].append("Sous-tension")
                        if throttle_value & 0x2:
                            throttle_status["current"].append("Surchauffe")
                        if throttle_value & 0x4:
                            throttle_status["current"].append("Fréquence réduite (alimentation)")
                        if throttle_value & 0x8:
                            throttle_status["current"].append("Fréquence réduite (température)")
                        
                        if throttle_value & 0x80000:
                            throttle_status["past"].append("Sous-tension détectée")
                        if throttle_value & 0x100000:
                            throttle_status["past"].append("Surchauffe détectée")
                        if throttle_value & 0x200000:
                            throttle_status["past"].append("Fréquence réduite (alimentation)")
                        if throttle_value & 0x400000:
                            throttle_status["past"].append("Fréquence réduite (température)")
                        
                        if throttle_status["current"]:
                            throttle_status["severity"] = "critical"
                        elif throttle_status["past"]:
                            throttle_status["severity"] = "warning"
                        else:
                            throttle_status["severity"] = "ok"
                            
                    except ValueError:
                        pass
            
            result["throttling"] = throttle_status
            return result
            
        except Exception as e:
            return {
                "status": "error", 
                "message": str(e),
                "temperature": None,
                "throttling": {"code": "error", "current": [], "past": [], "severity": "error"}
            }
    
    # === ROUTES DÉPENDANCES (Phase 1) ===
    
    @router.get("/dependencies")
    async def get_all_dependencies():
        """Récupère le statut de toutes les dépendances (installées + GitHub)"""
        try:
            results = await dependency_service.get_all_dependency_status()
            return {
                "status": "success",
                "dependencies": results,
                "count": len(results)
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "dependencies": {},
                "count": 0
            }
    
    @router.get("/dependencies/list")
    async def get_dependency_list():
        """Récupère la liste des dépendances configurées"""
        try:
            dependencies = dependency_service.get_dependency_list()
            return {
                "status": "success",
                "dependencies": dependencies
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "dependencies": []
            }
    
    @router.get("/dependencies/{dependency_key}")
    async def get_dependency_details(dependency_key: str):
        """Récupère les détails d'une dépendance spécifique"""
        try:
            result = await dependency_service._get_dependency_full_status(dependency_key)
            return {
                "status": "success",
                "dependency": result
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "dependency": None
            }
    
    @router.get("/dependencies/{dependency_key}/installed")
    async def get_dependency_installed_version(dependency_key: str):
        """Récupère uniquement la version installée d'une dépendance"""
        try:
            result = await dependency_service.get_installed_version(dependency_key)
            return {
                "status": "success",
                "installed": result
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "installed": None
            }
    
    @router.get("/dependencies/{dependency_key}/latest")
    async def get_dependency_latest_version(dependency_key: str):
        """Récupère uniquement la dernière version depuis GitHub"""
        try:
            result = await dependency_service.get_latest_github_version(dependency_key)
            return {
                "status": "success",
                "latest": result
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "latest": None
            }
    
    # === NOUVELLES ROUTES MISES À JOUR (Phase 2A) ===
    
    @router.get("/dependencies/{dependency_key}/can-update")
    async def check_can_update_dependency(dependency_key: str):
        """Vérifie si une dépendance peut être mise à jour"""
        try:
            result = await update_service.can_update_dependency(dependency_key)
            return {
                "status": "success",
                **result
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "can_update": False
            }
    
    @router.post("/dependencies/{dependency_key}/update")
    async def update_dependency(dependency_key: str, background_tasks: BackgroundTasks):
        """Lance la mise à jour d'une dépendance en arrière-plan"""
        
        # Vérifier si une mise à jour est déjà en cours
        if dependency_key in active_updates:
            return {
                "status": "error",
                "message": "Update already in progress for this dependency"
            }
        
        # Vérifier que la mise à jour est possible
        can_update = await update_service.can_update_dependency(dependency_key)
        if not can_update.get("can_update"):
            return {
                "status": "error", 
                "message": can_update.get("reason", "Cannot update")
            }
        
        # Marquer comme en cours
        active_updates[dependency_key] = {
            "status": "starting",
            "progress": 0,
            "message": "Initializing update..."
        }
        
        # Callback de progression
        async def progress_callback(message: str, progress: int):
            active_updates[dependency_key] = {
                "status": "updating",
                "progress": progress,
                "message": message
            }
            
            # Broadcast WebSocket
            await ws_manager.broadcast_dict({
                "category": "settings",
                "type": "dependency_update_progress",
                "source": "dependency_update",
                "data": {
                    "dependency": dependency_key,
                    "progress": progress,
                    "message": message,
                    "status": "updating"
                }
            })
        
        # Fonction de mise à jour en arrière-plan
        async def do_update():
            try:
                result = await update_service.update_dependency(dependency_key, progress_callback)
                
                if result["success"]:
                    # Mise à jour réussie
                    del active_updates[dependency_key]
                    
                    await ws_manager.broadcast_dict({
                        "category": "settings",
                        "type": "dependency_update_complete",
                        "source": "dependency_update",
                        "data": {
                            "dependency": dependency_key,
                            "success": True,
                            "message": result.get("message", "Update completed"),
                            "old_version": result.get("old_version"),
                            "new_version": result.get("new_version")
                        }
                    })
                else:
                    # Mise à jour échouée
                    del active_updates[dependency_key]
                    
                    await ws_manager.broadcast_dict({
                        "category": "settings",
                        "type": "dependency_update_complete",
                        "source": "dependency_update",
                        "data": {
                            "dependency": dependency_key,
                            "success": False,
                            "error": result.get("error", "Update failed")
                        }
                    })
                    
            except Exception as e:
                # Erreur inattendue
                if dependency_key in active_updates:
                    del active_updates[dependency_key]
                
                await ws_manager.broadcast_dict({
                    "category": "settings",
                    "type": "dependency_update_complete",
                    "source": "dependency_update",
                    "data": {
                        "dependency": dependency_key,
                        "success": False,
                        "error": str(e)
                    }
                })
        
        # Lancer en arrière-plan
        background_tasks.add_task(do_update)
        
        return {
            "status": "success",
            "message": f"Update started for {dependency_key}",
            "available_version": can_update.get("available_version")
        }
    
    @router.get("/dependencies/{dependency_key}/update-status")
    async def get_update_status(dependency_key: str):
        """Récupère le statut de mise à jour d'une dépendance"""
        if dependency_key in active_updates:
            return {
                "status": "success",
                "updating": True,
                **active_updates[dependency_key]
            }
        else:
            return {
                "status": "success",
                "updating": False,
                "message": "No update in progress"
            }
    
    @router.post("/dependencies/{dependency_key}/cancel-update")
    async def cancel_update(dependency_key: str):
        """Annule une mise à jour en cours (si possible)"""
        if dependency_key in active_updates:
            # Note: L'annulation n'est pas implémentée dans cette version
            # pour des raisons de sécurité (éviter les états incohérents)
            return {
                "status": "error", 
                "message": "Update cancellation not supported. Please wait for completion."
            }
        else:
            return {
                "status": "success",
                "message": "No update in progress"
            }
    
    return router