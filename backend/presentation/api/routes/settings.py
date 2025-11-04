# backend/presentation/api/routes/settings.py
"""
Routes Settings - Version avec désactivation des apps et arrêt des processus
"""
from fastapi import APIRouter, HTTPException
from typing import Dict, Any, Callable, Optional
from backend.infrastructure.services.settings_service import SettingsService
from backend.domain.audio_state import AudioSource
import logging
import asyncio

logger = logging.getLogger(__name__)

def create_settings_router(
    ws_manager,
    volume_service,
    state_machine,
    screen_controller,
    systemd_manager,
    routing_service,
    hardware_service
):
    """Router settings avec désactivation propre des apps"""
    router = APIRouter()
    settings = SettingsService()
    
    async def _handle_setting_update(
        payload: Dict[str, Any],
        validator: Callable[[Any], bool],
        setter: Callable,
        event_type: str,
        event_data: Dict[str, Any],
        reload_callback: Optional[Callable] = None
    ) -> Dict[str, Any]:
        """Pattern unifié pour toutes les routes settings - supporte les setters async"""
        try:
            if not validator(payload):
                raise HTTPException(status_code=400, detail="Invalid payload")

            # Appeler le setter et l'attendre s'il est async
            setter_result = setter()
            if asyncio.iscoroutine(setter_result):
                success = await setter_result
            else:
                success = setter_result

            if not success:
                raise HTTPException(status_code=500, detail="Failed to save")

            # NOUVEAU : Invalider explicitement le cache avant le reload_callback
            settings._cache = None

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
    
    def _get_services_for_source(source: str) -> list:
        """Retourne la liste des services systemd pour une source audio"""
        services_map = {
            'librespot': ['milo-go-librespot.service'],
            'roc': ['milo-roc.service'],
            'bluetooth': ['milo-bluealsa-aplay.service', 'milo-bluealsa.service'],
            'radio': ['milo-radio.service']
        }
        return services_map.get(source, [])
    
    # Language
    @router.get("/language")
    async def get_language():
        return {"status": "success", "language": await settings.get_setting('language') or 'french'}
    
    @router.post("/language")
    async def set_language(payload: Dict[str, Any]):
        new_language = payload.get('language')

        return await _handle_setting_update(
            payload,
            validator=lambda p: p.get('language') in ['french', 'english', 'spanish', 'hindi', 'chinese', 'portuguese', 'italian', 'german'],
            setter=lambda: settings.set_setting('language', new_language),
            event_type="language_changed",
            event_data={"language": new_language}
        )
    
    # Volume limits
    @router.get("/volume-limits")
    async def get_volume_limits():
        vol = await settings.get_setting('volume') or {}
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

        async def setter():
            return (
                await settings.set_setting('volume.alsa_min', alsa_min) and
                await settings.set_setting('volume.alsa_max', alsa_max)
            )

        return await _handle_setting_update(
            payload,
            validator=lambda p: (
                p.get('alsa_min') is not None and p.get('alsa_max') is not None and
                0 <= p['alsa_min'] <= 100 and 0 <= p['alsa_max'] <= 100 and
                p['alsa_max'] - p['alsa_min'] >= 10
            ),
            setter=setter,
            event_type="volume_limits_changed",
            event_data={"limits": {"alsa_min": alsa_min, "alsa_max": alsa_max}},
            reload_callback=volume_service.reload_volume_limits
        )
    
    @router.post("/volume-limits/toggle")
    async def toggle_volume_limits(payload: Dict[str, Any]):
        enabled = payload.get('enabled')

        if enabled:
            current_min = await settings.get_setting('volume.alsa_min') or 0
            current_max = await settings.get_setting('volume.alsa_max') or 65
        else:
            current_min = 0
            current_max = 100

        async def setter():
            return (
                await settings.set_setting('volume.limits_enabled', enabled) and
                await settings.set_setting('volume.alsa_min', current_min) and
                await settings.set_setting('volume.alsa_max', current_max)
            )

        return await _handle_setting_update(
            payload,
            validator=lambda p: isinstance(p.get('enabled'), bool),
            setter=setter,
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
        vol = await settings.get_setting('volume') or {}
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

        async def setter():
            return (
                await settings.set_setting('volume.startup_volume', startup_volume) and
                await settings.set_setting('volume.restore_last_volume', restore_last_volume)
            )

        return await _handle_setting_update(
            payload,
            validator=lambda p: (
                p.get('startup_volume') is not None and p.get('restore_last_volume') is not None and
                0 <= p['startup_volume'] <= 100 and isinstance(p['restore_last_volume'], bool)
            ),
            setter=setter,
            event_type="volume_startup_changed",
            event_data={"config": {"startup_volume": startup_volume, "restore_last_volume": restore_last_volume}},
            reload_callback=volume_service.reload_startup_config
        )
    
    # Volume steps (mobile)
    @router.get("/volume-steps")
    async def get_volume_steps():
        vol = await settings.get_setting('volume') or {}
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
    
    # Rotary steps
    @router.get("/rotary-steps")
    async def get_rotary_steps():
        vol = await settings.get_setting('volume') or {}
        return {
            "status": "success",
            "config": {"rotary_volume_steps": vol.get("rotary_volume_steps", 2)}
        }
    
    @router.post("/rotary-steps")
    async def set_rotary_steps(payload: Dict[str, Any]):
        steps = payload.get('rotary_volume_steps')
        
        return await _handle_setting_update(
            payload,
            validator=lambda p: p.get('rotary_volume_steps') is not None and 1 <= p['rotary_volume_steps'] <= 10,
            setter=lambda: settings.set_setting('volume.rotary_volume_steps', steps),
            event_type="rotary_steps_changed",
            event_data={"config": {"rotary_volume_steps": steps}},
            reload_callback=volume_service.reload_rotary_steps_config
        )
    
    # Dock apps - VERSION AVEC DÉSACTIVATION DES PROCESSUS
    @router.get("/dock-apps")
    async def get_dock_apps():
        dock = await settings.get_setting('dock') or {}
        enabled_apps = dock.get('enabled_apps', ["librespot", "bluetooth", "roc", "radio", "multiroom", "equalizer", "settings"])

        return {
            "status": "success",
            "config": {"enabled_apps": enabled_apps}
        }
    
    @router.post("/dock-apps")
    async def set_dock_apps(payload: Dict[str, Any]):
        """
        Met à jour les apps activées dans le dock.
        Si une app est désactivée, arrête les processus associés.
        Si une app est activée, démarre les processus associés (multiroom/equalizer).
        Approche stricte : une erreur = rollback complet.
        """
        try:
            enabled_apps = payload.get('enabled_apps', [])
            
            # Validation basique
            valid_apps = ["librespot", "bluetooth", "roc", "radio", "multiroom", "equalizer", "settings"]
            if not isinstance(enabled_apps, list) or not all(app in valid_apps for app in enabled_apps):
                raise HTTPException(status_code=400, detail="Invalid enabled_apps list")

            # Au moins une source audio doit être activée
            audio_sources = ["librespot", "bluetooth", "roc", "radio"]
            enabled_audio_sources = [app for app in enabled_apps if app in audio_sources]
            if not enabled_audio_sources:
                raise HTTPException(status_code=400, detail="At least one audio source must be enabled")
            
            # Charger ancienne config
            old_settings = await settings.load_settings()
            old_enabled_apps = old_settings.get("dock", {}).get("enabled_apps", [])
            
            # Détection des changements
            disabled_apps = set(old_enabled_apps) - set(enabled_apps)
            enabled_apps_new = set(enabled_apps) - set(old_enabled_apps)
            
            if not disabled_apps and not enabled_apps_new:
                # Aucun changement, juste sauvegarder
                success = await settings.set_setting("dock.enabled_apps", enabled_apps)
                if success:
                    await ws_manager.broadcast_dict({
                        "category": "settings",
                        "type": "dock_apps_changed",
                        "source": "settings",
                        "data": {"config": {"enabled_apps": enabled_apps}}
                    })
                    return {"status": "success", "config": {"enabled_apps": enabled_apps}}
                else:
                    raise HTTPException(status_code=500, detail="Failed to save settings")
            
            # Log des opérations pour debug
            operations_log = []
            
            try:
                # === TRAITER LES DÉSACTIVATIONS ===
                for app in disabled_apps:
                    logger.info(f"Processing disable for app: {app}")
                    
                    # === SOURCES AUDIO ===
                    if app in ['librespot', 'bluetooth', 'roc', 'radio']:
                        current_source = state_machine.system_state.active_source.value
                        
                        if app == current_source:
                            # Source active : transition vers none (arrête automatiquement)
                            operations_log.append(f"Transitioning {app} to none")
                            logger.info(f"Transitioning active source {app} to none")
                            success = await state_machine.transition_to_source(AudioSource.NONE)
                            if not success:
                                raise ValueError(f"Failed to transition from {app} to none")
                        else:
                            # Source inactive : arrêter directement les services
                            services_to_stop = _get_services_for_source(app)
                            for service in services_to_stop:
                                operations_log.append(f"Stopping service {service}")
                                logger.info(f"Stopping service {service}")
                                success = await systemd_manager.stop(service)
                                if not success:
                                    raise ValueError(f"Failed to stop service {service}")
                    
                    # === MULTIROOM ===
                    elif app == 'multiroom':
                        # 1. Récupérer la source active pour redémarrer le plugin
                        current_state = await state_machine.get_current_state()
                        active_source = None
                        if current_state["active_source"] != "none":
                            try:
                                active_source = AudioSource(current_state["active_source"])
                            except ValueError:
                                pass

                        # 2. Désactiver le routing (redémarre automatiquement le plugin en mode direct)
                        operations_log.append("Disabling multiroom routing and switching to direct mode")
                        logger.info(f"Disabling multiroom routing for active source: {active_source.value if active_source else 'none'}")
                        await routing_service.set_multiroom_enabled(False, active_source)

                        # 3. Arrêter snapserver
                        operations_log.append("Stopping milo-snapserver-multiroom.service")
                        logger.info("Stopping milo-snapserver-multiroom.service")
                        success = await systemd_manager.stop("milo-snapserver-multiroom.service")
                        if not success:
                            raise ValueError("Failed to stop milo-snapserver-multiroom.service")

                        # 4. Arrêter snapclient
                        operations_log.append("Stopping milo-snapclient-multiroom.service")
                        logger.info("Stopping milo-snapclient-multiroom.service")
                        success = await systemd_manager.stop("milo-snapclient-multiroom.service")
                        if not success:
                            raise ValueError("Failed to stop milo-snapclient-multiroom.service")

                        # 5. Notifier le frontend via WebSocket
                        operations_log.append("Broadcasting multiroom state update")
                        logger.info("Broadcasting multiroom state update to frontend")
                        await state_machine.update_multiroom_state(False)
                    
                    # === EQUALIZER ===
                    elif app == 'equalizer':
                        # Récupérer la source active pour redémarrer le plugin
                        current_state = await state_machine.get_current_state()
                        active_source = None
                        if current_state["active_source"] != "none":
                            try:
                                active_source = AudioSource(current_state["active_source"])
                            except ValueError:
                                pass

                        operations_log.append("Disabling equalizer routing")
                        logger.info(f"Disabling equalizer for active source: {active_source.value if active_source else 'none'}")
                        await routing_service.set_equalizer_enabled(False, active_source)

                        # Notifier le frontend via WebSocket
                        operations_log.append("Broadcasting equalizer state update")
                        logger.info("Broadcasting equalizer state update to frontend")
                        await state_machine.update_equalizer_state(False)
                
                # === TRAITER LES ACTIVATIONS ===
                for app in enabled_apps_new:
                    logger.info(f"Processing enable for app: {app}")
                    
                    # === SOURCES AUDIO : NE RIEN FAIRE ===
                    if app in ['librespot', 'bluetooth', 'roc', 'radio']:
                        operations_log.append(f"App {app} enabled (no service start needed)")
                        logger.info(f"App {app} enabled in dock (services will start on source change)")
                    
                    # === MULTIROOM ===
                    elif app == 'multiroom':
                        # 1. Récupérer la source active pour redémarrer le plugin
                        current_state = await state_machine.get_current_state()
                        active_source = None
                        if current_state["active_source"] != "none":
                            try:
                                active_source = AudioSource(current_state["active_source"])
                            except ValueError:
                                pass

                        # 2. Activer le routing (redémarre automatiquement le plugin en mode multiroom)
                        operations_log.append("Enabling multiroom routing and switching to multiroom mode")
                        logger.info(f"Enabling multiroom routing for active source: {active_source.value if active_source else 'none'}")
                        await routing_service.set_multiroom_enabled(True, active_source)

                        # 3. Démarrer snapserver
                        operations_log.append("Starting milo-snapserver-multiroom.service")
                        logger.info("Starting milo-snapserver-multiroom.service")
                        success = await systemd_manager.start("milo-snapserver-multiroom.service")
                        if not success:
                            raise ValueError("Failed to start milo-snapserver-multiroom.service")

                        # 4. Démarrer snapclient
                        operations_log.append("Starting milo-snapclient-multiroom.service")
                        logger.info("Starting milo-snapclient-multiroom.service")
                        success = await systemd_manager.start("milo-snapclient-multiroom.service")
                        if not success:
                            raise ValueError("Failed to start milo-snapclient-multiroom.service")

                        # 5. Notifier le frontend via WebSocket
                        operations_log.append("Broadcasting multiroom state update")
                        logger.info("Broadcasting multiroom state update to frontend")
                        await state_machine.update_multiroom_state(True)
                    
                    # === EQUALIZER ===
                    elif app == 'equalizer':
                        # Récupérer la source active pour redémarrer le plugin
                        current_state = await state_machine.get_current_state()
                        active_source = None
                        if current_state["active_source"] != "none":
                            try:
                                active_source = AudioSource(current_state["active_source"])
                            except ValueError:
                                pass

                        operations_log.append("Enabling equalizer routing")
                        logger.info(f"Enabling equalizer for active source: {active_source.value if active_source else 'none'}")
                        await routing_service.set_equalizer_enabled(True, active_source)

                        # Notifier le frontend via WebSocket
                        operations_log.append("Broadcasting equalizer state update")
                        logger.info("Broadcasting equalizer state update to frontend")
                        await state_machine.update_equalizer_state(True)
                
                # Toutes les opérations ont réussi → sauvegarder les settings
                operations_log.append("Saving new settings")
                logger.info("All operations successful, saving settings")
                success = await settings.set_setting("dock.enabled_apps", enabled_apps)
                if not success:
                    raise ValueError("Failed to save settings")
                
                # Broadcast WebSocket
                await ws_manager.broadcast_dict({
                    "category": "settings",
                    "type": "dock_apps_changed",
                    "source": "settings",
                    "data": {"config": {"enabled_apps": enabled_apps}}
                })
                
                return {
                    "status": "success",
                    "config": {"enabled_apps": enabled_apps},
                    "operations": operations_log
                }
                
            except Exception as e:
                # ROLLBACK : Une erreur = annulation complète
                logger.error(f"Error during dock-apps update: {e}")
                logger.error(f"Operations completed before error: {operations_log}")
                
                raise HTTPException(
                    status_code=500,
                    detail={
                        "message": f"Failed to update apps: {str(e)}",
                        "operations_log": operations_log
                    }
                )
        
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Unexpected error in dock-apps update: {e}")
            raise HTTPException(status_code=500, detail=str(e))
    
    # Spotify
    @router.get("/spotify-disconnect")
    async def get_spotify_disconnect():
        spotify = await settings.get_setting('spotify') or {}
        return {
            "status": "success",
            "config": {"auto_disconnect_delay": spotify.get("auto_disconnect_delay", 10.0)}
        }
    
    @router.post("/spotify-disconnect")
    async def set_spotify_disconnect(payload: Dict[str, Any]):
        delay = payload.get('auto_disconnect_delay')
        
        async def apply_to_plugin():
            try:
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
        screen = await settings.get_setting('screen') or {}
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
        screen = await settings.get_setting('screen') or {}
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

            # Utiliser screen_controller qui gère les différents types d'écrans
            screen_controller.brightness_on = brightness_on
            screen_controller._update_screen_commands()

            # Exécuter la commande adaptée au type d'écran
            await screen_controller._screen_cmd(screen_controller.screen_on_cmd)

            # Réinitialiser le timer d'inactivité
            from time import monotonic
            screen_controller.last_activity_time = monotonic()
            screen_controller.screen_on = True

            return {
                "status": "success",
                "brightness_applied": brightness_on,
                "screen_type": screen_controller.screen_type,
                "timeout_restarted": True
            }

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/screen-activity")
    async def notify_screen_activity():
        """Endpoint pour notifier l'activité écran depuis le frontend (touch, souris, clavier)"""
        try:
            await screen_controller.on_touch_detected()
            return {"status": "success", "activity_time_reset": True}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # Screen debug
    @router.get("/screen-debug")
    async def get_screen_debug():
        """Endpoint de debug pour visualiser l'état du screen controller en temps réel"""
        from time import monotonic

        time_since_activity = monotonic() - screen_controller.last_activity_time
        time_until_off = max(0, screen_controller.timeout_seconds - time_since_activity) if screen_controller.timeout_seconds > 0 else None

        return {
            "status": "success",
            "debug": {
                "time_since_activity": round(time_since_activity, 1),
                "timeout_seconds": screen_controller.timeout_seconds,
                "screen_on": screen_controller.screen_on,
                "current_plugin_state": screen_controller.current_plugin_state,
                "brightness_on": screen_controller.brightness_on,
                "will_turn_off_in": round(time_until_off, 1) if time_until_off is not None else None
            }
        }

    # Température système
    @router.get("/system-temperature")
    async def get_system_temperature():
        """Récupère la température du Raspberry Pi et le statut de throttling"""
        try:
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

    # Network info (IP address)
    @router.get("/network-info")
    async def get_network_info():
        """Récupère l'adresse IP locale principale du Raspberry Pi"""
        try:
            process = await asyncio.create_subprocess_shell(
                "hostname -I",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                output = stdout.decode().strip()
                # hostname -I retourne toutes les IPs séparées par des espaces
                # On prend la première qui est généralement l'IP principale IPv4
                ips = output.split()
                if ips:
                    # Filtrer pour ne garder que l'IPv4 (format x.x.x.x)
                    ipv4_ips = [ip for ip in ips if ip.count('.') == 3]
                    if ipv4_ips:
                        return {
                            "status": "success",
                            "ip": ipv4_ips[0]
                        }

            return {
                "status": "error",
                "message": "Unable to retrieve IP address",
                "ip": None
            }

        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "ip": None
            }

    # Hardware info
    @router.get("/hardware-info")
    async def get_hardware_info():
        """Récupère les informations hardware (type d'écran, résolution, etc.)"""
        try:
            screen_info = hardware_service.get_screen_info()

            return {
                "status": "success",
                "hardware": {
                    "screen_type": screen_info["type"],
                    "screen_resolution": screen_info["resolution"]
                }
            }
        except Exception as e:
            logger.error(f"Error getting hardware info: {e}")
            return {
                "status": "error",
                "message": str(e),
                "hardware": {
                    "screen_type": "none",
                    "screen_resolution": {"width": None, "height": None}
                }
            }

    return router