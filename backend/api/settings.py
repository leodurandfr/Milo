# backend/api/settings.py
"""
Settings Routes – Version with app deactivation and process stopping
"""
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from typing import Any, Callable, Dict, Optional, TYPE_CHECKING
from backend.core.models.audio_state import AudioSource
from backend.api.route_helpers import api_error_handler, coerce_audio_source_or_none
from backend.api.responses import BulkSettingsResponse
from backend.config.constants import AUDIO_SOURCE_APPS
from backend.api.models import (
    LanguageRequest,
    VolumeLimitsRequest,
    VolumeStartupRequest,
    VolumeStepsRequest,
    RotaryStepsRequest,
    BtRemoteStepsRequest,
    IrRemoteStepsRequest,
    DockAppsRequest,
    AudioStopRequest,
    ScreenTimeoutRequest,
    ScreenBrightnessRequest,
    ScreenScreensaverRequest,
    ScreenUiScaleRequest,
    ScreenColorFilterRequest,
    MacRocConfigRequest,
    RadioSettingsRequest,
    MusicLibrarySettingsRequest,
    QobuzSettingsRequest,
    SpotifySettingsRequest,
    HardwareConfigRequest,
)
from backend.core.models.ws_events import (
    AudioStopChanged,
    AudioStopConfig,
    BtRemoteStepsChanged,
    BtRemoteStepsConfig,
    DockAppsChanged,
    DockAppsConfig,
    IrRemoteStepsChanged,
    IrRemoteStepsConfig,
    LanguageChanged,
    MacRocChanged,
    MacRocConfig,
    RadioSettingsChanged,
    RadioSettingsConfig,
    MusicLibrarySettingsChanged,
    MusicLibrarySettingsConfig,
    QobuzSettingsChanged,
    QobuzSettingsConfig,
    SpotifySettingsChanged,
    SpotifySettingsConfig,
    RotaryStepsChanged,
    RotaryStepsConfig,
    ScreenBrightnessChanged,
    ScreenBrightnessConfig,
    ScreenColorFilterChanged,
    ScreenColorFilterConfig,
    ScreenScreensaverChanged,
    ScreenScreensaverConfig,
    ScreenTimeoutChanged,
    ScreenTimeoutConfig,
    ScreenUiScaleChanged,
    ScreenUiScaleConfig,
    SettingsEvent,
    VolumeLimitsChanged,
    VolumeLimitsConfig,
    VolumeStartupChanged,
    VolumeStartupConfig,
    VolumeStepsChanged,
    VolumeStepsConfig,
)
from backend.core.multiroom.routing import MacEnv
import logging
import asyncio

if TYPE_CHECKING:
    from backend.core.equalizer.multiroom_service import MultiroomEqualizerService
    from backend.core.multiroom.routing import AudioRoutingService
    from backend.core.settings import SettingsService
    from backend.core.state import AudioStateMachine
    from backend.core.systemd import SystemdServiceManager
    from backend.core.volume.service import VolumeService
    from backend.hardware.screen import ScreenController
    from backend.hardware.service import HardwareService


logger = logging.getLogger(__name__)

def create_settings_router(
    volume_service: "VolumeService",
    state_machine: "AudioStateMachine",
    screen_controller: "ScreenController",
    systemd_manager: "SystemdServiceManager",
    routing_service: "AudioRoutingService",
    hardware_service: "HardwareService",
    settings_service: "SettingsService",
    multiroom_equalizer_service: "MultiroomEqualizerService"
):
    """Settings router with proper app deactivation"""
    router = APIRouter()
    settings = settings_service

    async def _handle_setting_update(
        payload: Dict[str, Any],
        validator: Callable[[Any], bool],
        setter: Callable,
        event: SettingsEvent,
        reload_callback: Optional[Callable] = None
    ) -> Dict[str, Any]:
        """Unified pattern for all settings routes – supports async setters"""
        async with api_error_handler(f"Error updating setting ({event.TYPE})", logger):
            if not validator(payload):
                raise HTTPException(status_code=400, detail="Invalid payload")

            setter_result = setter()
            if asyncio.iscoroutine(setter_result):
                success = await setter_result
            else:
                success = setter_result

            if not success:
                raise HTTPException(status_code=500, detail="Failed to save")

            settings.invalidate_cache()

            reload_success = True
            if reload_callback:
                try:
                    reload_success = await reload_callback()
                except Exception as e:
                    logger.error(f"reload_callback failed for {event.TYPE}: {e}")
                    reload_success = False

            # reload_success stays in the HTTP response only (useSettingsAPI reads
            # it there); the broadcast carries just the new config.
            await state_machine.broadcast(event)

            return {
                "status": "success",
                **event.model_dump(exclude={"source"}),
                "reload_success": reload_success,
            }

    def _get_services_for_source(source: str) -> list:
        """Return the list of systemd services for an audio source"""
        services_map = {
            'spotify': ['milo-spotify.service'],
            'mac': ['milo-mac.service'],
            'bluetooth': ['milo-bluealsa-aplay.service', 'milo-bluealsa.service'],
            'radio': ['milo-radio.service'],
            'podcast': ['milo-podcast.service'],
            'airplay': ['milo-airplay.service']
        }
        return services_map.get(source, [])

    # Bulk settings (all categories in one response)
    @router.get("/bulk", response_model=BulkSettingsResponse)
    async def get_bulk_settings():
        """Return all settings categories in a single response.

        A pure projection of the stored settings: every key below is guaranteed
        by `SettingsService._validate_and_merge`, which emits each declared
        section unconditionally, so there is no fallback here. There must never
        be one — a default restated at this layer can only ever disagree with
        the one `SettingsService.defaults` declares, and it would do so silently,
        in the direction of showing a stale default as if it were the stored
        value. `tests/architecture/test_settings_defaults.py` holds that.
        """
        all_settings = await settings.get_all_settings()

        vol = all_settings['volume']
        screen = all_settings['screen']
        mac = all_settings['mac']

        timeout_seconds = screen['timeout_seconds']

        return {
            "status": "success",
            "language": all_settings['language'],
            "volume_limits": {
                "min_db": vol['limit_min_db'],
                "max_db": vol['limit_max_db']
            },
            "volume_startup": {
                "startup_volume_db": vol['startup_volume_db'],
                "restore_last_volume": vol['restore_last_volume']
            },
            "rotary_steps": {"step_rotary_db": vol['step_rotary_db']},
            "bt_remote_steps": {"step_bt_remote_db": vol['step_bt_remote_db']},
            "ir_remote_steps": {"step_ir_remote_db": vol['step_ir_remote_db']},
            "dock_apps": {"enabled_apps": all_settings['dock']['enabled_apps']},
            "audio_stop": {"auto_stop_delay": all_settings['audio']['auto_stop_delay']},
            "screen_timeout": {
                "screen_timeout_enabled": timeout_seconds != 0,
                "screen_timeout_seconds": timeout_seconds
            },
            "screen_brightness": {"brightness_on": screen['brightness_on']},
            "screen_ui_scale": {"ui_scale": screen['ui_scale']},
            "screen_screensaver": {
                "screensaver_enabled": screen['screensaver_enabled'],
                "screensaver_delay_seconds": screen['screensaver_delay_seconds']
            },
            "screen_color_filter": {
                "enabled": screen['color_filter_enabled'],
                "warmth": screen['color_filter_warmth']
            },
            "radio_settings": {"shazam_enabled": all_settings['radio']['shazam_enabled']},
            "music_library_settings": {
                "separate_storages": all_settings['music_library']['separate_storages']
            },
            "qobuz_settings": {"allow_app_volume": all_settings['qobuz']['allow_app_volume']},
            "spotify_settings": {
                "crossfade_duration": all_settings['spotify']['crossfade_duration']
            },
            "mac_roc": {
                "target_latency_ms": mac['target_latency_ms'],
                "latency_profile": mac['latency_profile'],
                "frame_length_ms": mac['frame_length_ms']
            }
        }

    # Language
    @router.get("/language")
    async def get_language():
        return {"status": "success", "language": await settings.get_setting('language') or 'english'}

    @router.put("/language")
    async def set_language(payload: LanguageRequest):
        return await _handle_setting_update(
            payload,
            validator=lambda p: True,  # Validated by Pydantic
            setter=lambda: settings.set_setting('language', payload.language),
            event=LanguageChanged(language=payload.language)
        )

    # Volume limits (in dB)
    @router.put("/volume-limits")
    async def set_volume_limits(payload: VolumeLimitsRequest):
        return await _handle_setting_update(
            payload,
            validator=lambda p: True,  # Validated by Pydantic
            setter=lambda: settings.set_settings({
                'volume.limit_min_db': payload.min_db,
                'volume.limit_max_db': payload.max_db,
            }),
            event=VolumeLimitsChanged(
                limits=VolumeLimitsConfig(min_db=payload.min_db, max_db=payload.max_db)
            ),
            reload_callback=volume_service.reload_volume_limits
        )

    # Volume startup (in dB)
    @router.put("/volume-startup")
    async def set_volume_startup(payload: VolumeStartupRequest):
        return await _handle_setting_update(
            payload,
            validator=lambda p: True,  # Validated by Pydantic
            setter=lambda: settings.set_settings({
                'volume.startup_volume_db': payload.startup_volume_db,
                'volume.restore_last_volume': payload.restore_last_volume,
            }),
            event=VolumeStartupChanged(config=VolumeStartupConfig(
                startup_volume_db=payload.startup_volume_db,
                restore_last_volume=payload.restore_last_volume,
            )),
            reload_callback=volume_service.reload_startup_config
        )

    # Volume steps (mobile, in dB)
    @router.put("/volume-steps")
    async def set_volume_steps(payload: VolumeStepsRequest):
        return await _handle_setting_update(
            payload,
            validator=lambda p: True,  # Validated by Pydantic
            setter=lambda: settings.set_setting('volume.step_mobile_db', payload.step_mobile_db),
            event=VolumeStepsChanged(
                config=VolumeStepsConfig(step_mobile_db=payload.step_mobile_db)
            ),
            reload_callback=volume_service.reload_volume_steps_config
        )

    # Rotary steps (in dB)
    @router.put("/rotary-steps")
    async def set_rotary_steps(payload: RotaryStepsRequest):
        return await _handle_setting_update(
            payload,
            validator=lambda p: True,  # Validated by Pydantic
            setter=lambda: settings.set_setting('volume.step_rotary_db', payload.step_rotary_db),
            event=RotaryStepsChanged(
                config=RotaryStepsConfig(step_rotary_db=payload.step_rotary_db)
            ),
            reload_callback=volume_service.reload_steps_config
        )

    # BT remote steps (in dB)
    @router.put("/bt-remote-steps")
    async def set_bt_remote_steps(payload: BtRemoteStepsRequest):
        return await _handle_setting_update(
            payload,
            validator=lambda p: True,  # Validated by Pydantic
            setter=lambda: settings.set_setting('volume.step_bt_remote_db', payload.step_bt_remote_db),
            event=BtRemoteStepsChanged(
                config=BtRemoteStepsConfig(step_bt_remote_db=payload.step_bt_remote_db)
            ),
            reload_callback=volume_service.reload_steps_config
        )

    # IR remote steps (in dB)
    @router.put("/ir-remote-steps")
    async def set_ir_remote_steps(payload: IrRemoteStepsRequest):
        return await _handle_setting_update(
            payload,
            validator=lambda p: True,  # Validated by Pydantic
            setter=lambda: settings.set_setting('volume.step_ir_remote_db', payload.step_ir_remote_db),
            event=IrRemoteStepsChanged(
                config=IrRemoteStepsConfig(step_ir_remote_db=payload.step_ir_remote_db)
            ),
            reload_callback=volume_service.reload_steps_config
        )

    # Dock apps – VERSION WITH PROCESS DEACTIVATION
    @router.put("/dock-apps")
    async def set_dock_apps(payload: DockAppsRequest):
        """
        Update the enabled apps in the dock.
        If an app is disabled, stop the associated processes.
        If an app is enabled, start the associated processes (multiroom/equalizer).
        Strict approach: one error = full rollback.
        """
        async with api_error_handler("Unexpected error in dock-apps update", logger):
            enabled_apps = payload.enabled_apps
            # Validation done by Pydantic

            old_settings = await settings.load_settings()
            old_enabled_apps = old_settings.get("dock", {}).get("enabled_apps", [])

            disabled_apps = set(old_enabled_apps) - set(enabled_apps)
            enabled_apps_new = set(enabled_apps) - set(old_enabled_apps)

            if not disabled_apps and not enabled_apps_new:
                # No change, just save
                success = await settings.set_setting("dock.enabled_apps", enabled_apps)
                if success:
                    await state_machine.broadcast(DockAppsChanged(
                        config=DockAppsConfig(enabled_apps=enabled_apps)
                    ))
                    return {"status": "success", "config": {"enabled_apps": enabled_apps}}
                else:
                    raise HTTPException(status_code=500, detail="Failed to save settings")

            # Operations log for debugging
            operations_log = []

            try:
                # === HANDLE DISABLES ===
                for app in disabled_apps:
                    logger.info(f"Processing disable for app: {app}")

                    # === AUDIO SOURCES ===
                    if app in AUDIO_SOURCE_APPS:
                        current_source = state_machine.system_state.active_source.value

                        if app == current_source:
                            # Active source: transition to none (automatically stops)
                            operations_log.append(f"Transitioning {app} to none")
                            logger.info(f"Transitioning active source {app} to none")
                            success = await state_machine.transition_to_source(AudioSource.NONE)
                            if not success:
                                raise ValueError(f"Failed to transition from {app} to none")
                        else:
                            # Inactive source: stop services directly
                            services_to_stop = _get_services_for_source(app)
                            for service in services_to_stop:
                                operations_log.append(f"Stopping service {service}")
                                logger.info(f"Stopping service {service}")
                                success = await systemd_manager.stop(service)
                                if not success:
                                    raise ValueError(f"Failed to stop service {service}")

                    # === MULTIROOM ===
                    elif app == 'multiroom':
                        # Get the active source for source restart
                        current_state = state_machine.get_current_state()
                        active_source = coerce_audio_source_or_none(current_state["active_source"])

                        # set_multiroom_enabled owns the full transition: source restart,
                        # snapcast stop, settings + routing.env writes, and broadcast.
                        operations_log.append("Disabling multiroom routing and switching to direct mode")
                        logger.info(f"Disabling multiroom routing for active source: {active_source.value if active_source else 'none'}")
                        await routing_service.set_multiroom_enabled(False, active_source)

                    # === EQUALIZER ===
                    elif app == 'equalizer':
                        # Get the active source for logging
                        current_state = state_machine.get_current_state()
                        active_source = coerce_audio_source_or_none(current_state["active_source"])

                        operations_log.append("Disabling equalizer effects")
                        logger.info(f"Disabling equalizer effects for active source: {active_source.value if active_source else 'none'}")
                        await multiroom_equalizer_service.set_local_equalizer_effects_enabled(False)

                # === HANDLE ENABLES ===
                for app in enabled_apps_new:
                    logger.info(f"Processing enable for app: {app}")

                    # === AUDIO SOURCES: DO NOTHING ===
                    if app in AUDIO_SOURCE_APPS:
                        operations_log.append(f"App {app} enabled (no service start needed)")
                        logger.info(f"App {app} enabled in dock (services will start on source change)")

                    # === MULTIROOM ===
                    elif app == 'multiroom':
                        # Get the active source for source restart
                        current_state = state_machine.get_current_state()
                        active_source = coerce_audio_source_or_none(current_state["active_source"])

                        # set_multiroom_enabled owns the full transition: source restart,
                        # snapcast start, settings + routing.env writes, and broadcast.
                        operations_log.append("Enabling multiroom routing and switching to multiroom mode")
                        logger.info(f"Enabling multiroom routing for active source: {active_source.value if active_source else 'none'}")
                        await routing_service.set_multiroom_enabled(True, active_source)

                    # === EQUALIZER ===
                    elif app == 'equalizer':
                        # Get the active source for logging
                        current_state = state_machine.get_current_state()
                        active_source = coerce_audio_source_or_none(current_state["active_source"])

                        operations_log.append("Enabling equalizer effects")
                        logger.info(f"Enabling equalizer effects for active source: {active_source.value if active_source else 'none'}")
                        await multiroom_equalizer_service.set_local_equalizer_effects_enabled(True)

                operations_log.append("Saving new settings")
                logger.info("All operations successful, saving settings")
                success = await settings.set_setting("dock.enabled_apps", enabled_apps)
                if not success:
                    raise ValueError("Failed to save settings")

                await state_machine.broadcast(DockAppsChanged(
                    config=DockAppsConfig(enabled_apps=enabled_apps)
                ))

                return {
                    "status": "success",
                    "config": {"enabled_apps": enabled_apps},
                    "operations": operations_log
                }

            except Exception as e:
                # ROLLBACK: any error = full cancellation
                logger.error(f"Error during dock-apps update: {e}")
                logger.error(f"Operations completed before error: {operations_log}")

                raise HTTPException(
                    status_code=500,
                    detail={
                        "message": f"Failed to update apps: {str(e)}",
                        "operations_log": operations_log
                    }
                )

    # Global audio auto-stop (applies to every eligible source)
    @router.put("/audio-stop")
    async def set_audio_stop(payload: AudioStopRequest):
        delay = payload.auto_stop_delay

        return await _handle_setting_update(
            payload,
            validator=lambda p: True,  # Validated by Pydantic
            setter=lambda: settings.set_setting('audio.auto_stop_delay', delay),
            event=AudioStopChanged(config=AudioStopConfig(auto_stop_delay=delay)),
            reload_callback=state_machine.reload_auto_stop_for_all_sources
        )

    # Screen timeout
    @router.put("/screen-timeout")
    async def set_screen_timeout(payload: ScreenTimeoutRequest):
        return await _handle_setting_update(
            payload,
            validator=lambda p: True,  # Validated by Pydantic
            setter=lambda: settings.set_setting('screen.timeout_seconds', payload.screen_timeout_seconds),
            event=ScreenTimeoutChanged(config=ScreenTimeoutConfig(
                screen_timeout_enabled=payload.screen_timeout_enabled,
                screen_timeout_seconds=payload.screen_timeout_seconds,
            )),
            reload_callback=screen_controller.reload_timeout_config
        )

    # Screen brightness
    @router.put("/screen-brightness")
    async def set_screen_brightness(payload: ScreenBrightnessRequest):
        return await _handle_setting_update(
            payload,
            validator=lambda p: True,  # Validated by Pydantic
            setter=lambda: settings.set_setting('screen.brightness_on', payload.brightness_on),
            event=ScreenBrightnessChanged(
                config=ScreenBrightnessConfig(brightness_on=payload.brightness_on)
            ),
            reload_callback=screen_controller.reload_timeout_config
        )

    @router.post("/screen-brightness/apply")
    async def apply_brightness_instantly(payload: ScreenBrightnessRequest):
        """Instant brightness application + restart timeout"""
        async with api_error_handler("Error applying brightness", logger):
            await screen_controller.apply_screen_config(payload.brightness_on)
            return {
                "status": "success",
                "brightness_applied": payload.brightness_on,
                "screen_type": screen_controller.screen_type,
                "timeout_restarted": True,
            }

    # Screen screensaver
    @router.put("/screen-screensaver")
    async def set_screen_screensaver(payload: ScreenScreensaverRequest):
        def setter():
            updates = {}
            if payload.screensaver_enabled is not None:
                updates['screen.screensaver_enabled'] = payload.screensaver_enabled
            if payload.screensaver_delay_seconds is not None:
                updates['screen.screensaver_delay_seconds'] = payload.screensaver_delay_seconds
            return settings.set_settings(updates)

        screen = await settings.get_setting('screen') or {}
        config = {
            "screensaver_enabled": payload.screensaver_enabled if payload.screensaver_enabled is not None else screen.get("screensaver_enabled", True),
            "screensaver_delay_seconds": payload.screensaver_delay_seconds if payload.screensaver_delay_seconds is not None else screen.get("screensaver_delay_seconds", 120)
        }

        return await _handle_setting_update(
            payload,
            validator=lambda p: True,
            setter=setter,
            event=ScreenScreensaverChanged(config=ScreenScreensaverConfig(**config))
        )

    # Screen UI scale
    @router.put("/screen-ui-scale")
    async def set_screen_ui_scale(payload: ScreenUiScaleRequest):
        return await _handle_setting_update(
            payload,
            validator=lambda p: True,
            setter=lambda: settings.set_setting('screen.ui_scale', payload.ui_scale),
            event=ScreenUiScaleChanged(
                config=ScreenUiScaleConfig(ui_scale=payload.ui_scale)
            )
        )

    # Screen warm color filter
    @router.put("/screen-color-filter")
    async def set_screen_color_filter(payload: ScreenColorFilterRequest):
        def setter():
            updates = {}
            if payload.enabled is not None:
                updates['screen.color_filter_enabled'] = payload.enabled
            if payload.warmth is not None:
                updates['screen.color_filter_warmth'] = payload.warmth
            return settings.set_settings(updates)

        screen = await settings.get_setting('screen') or {}
        config = {
            "enabled": payload.enabled if payload.enabled is not None else screen.get("color_filter_enabled", False),
            "warmth": payload.warmth if payload.warmth is not None else screen.get("color_filter_warmth", 50)
        }

        return await _handle_setting_update(
            payload,
            validator=lambda p: True,
            setter=setter,
            event=ScreenColorFilterChanged(config=ScreenColorFilterConfig(**config))
        )

    @router.post("/screen-activity")
    async def notify_screen_activity(request: Request):
        """Wake the physical screen on activity from the *local* Pi kiosk only.

        The same frontend runs on the Pi's touchscreen and on remote browsers
        (milo.local from a Mac/iPhone), so we must not let a remote interaction
        wake the Pi. The kiosk loads http://localhost and thus reaches the backend
        over loopback; nginx sets X-Real-IP to the real client address
        authoritatively (remote clients cannot spoof it). Non-loopback requests are
        acknowledged but ignored.
        """
        async with api_error_handler("Error notifying screen activity", logger):
            client_ip = request.headers.get("x-real-ip") or (
                request.client.host if request.client else ""
            )
            if client_ip not in ("127.0.0.1", "::1"):
                return {"status": "success", "activity_time_reset": False}
            await screen_controller.on_touch_detected()
            return {"status": "success", "activity_time_reset": True}

    # Hardware info
    @router.get("/hardware-info")
    async def get_hardware_info():
        """Retrieve hardware information (screen type, resolution, etc.)"""
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

    # Hardware configuration (full config + registry options for dropdowns)
    @router.get("/hardware-config")
    async def get_hardware_config():
        """Retrieve full hardware config and available options for the Hardware settings page."""
        from backend.hardware.registry import AUDIO_CARDS, SCREENS
        from backend.config.constants import SELECTABLE_GPIO_PINS
        async with api_error_handler("Error getting hardware config", logger):
            current = hardware_service.get_full_config()

            # Ensure volume_control is always present (resolved from hardware or auto-detected)
            if "audio" in current and "volume_control" not in current["audio"]:
                current["audio"]["volume_control"] = hardware_service.get_volume_control()

            # Build dropdown options from registry
            audio_options = [
                {"value": card_id, "label": card["label"], "category": card.get("category")}
                for card_id, card in AUDIO_CARDS.items()
            ]
            screen_options = [
                {"value": screen_id, "label": screen["label"]}
                for screen_id, screen in SCREENS.items()
            ]
            # Selectable BCM GPIO pins — single source of truth shared with the
            # rotary/IR validators so the frontend dropdown can never offer a pin
            # the backend would reject with HTTP 422.
            gpio_pin_options = [
                {"value": pin, "label": f"GPIO {pin}"}
                for pin in SELECTABLE_GPIO_PINS
            ]

            return {
                "status": "success",
                "current": current,
                "options": {
                    "audio_cards": audio_options,
                    "screens": screen_options,
                    "gpio_pins": gpio_pin_options,
                }
            }

    @router.put("/hardware-config")
    async def set_hardware_config(payload: HardwareConfigRequest, background_tasks: BackgroundTasks):
        """
        Save hardware config, apply to config.txt, and reboot.

        Resolves full audio card properties from registry before saving,
        then runs the privileged milo-apply-hardware script.
        """
        from backend.hardware.registry import AUDIO_CARDS, SCREENS

        async with api_error_handler("Error setting hardware config", logger):
            card = AUDIO_CARDS[payload.audio.id]
            screen = SCREENS[payload.screen.type]

            from backend.hardware.registry import is_dac_card
            audio_config = {"id": payload.audio.id}
            if card["overlay"]:
                audio_config.update({
                    "card_name": card["card_name"],
                    "overlay": card["overlay"],
                })
            # Persist volume_control: explicit override or auto-detect from card category
            volume_control = payload.audio.volume_control if payload.audio.volume_control is not None else not is_dac_card(payload.audio.id)
            audio_config["volume_control"] = volume_control

            config = {
                "audio": audio_config,
                "screen": {
                    "type": payload.screen.type,
                    "resolution": screen["resolution"],
                },
                "rotary_encoder": {
                    "enabled": payload.rotary_encoder.enabled,
                    "clk_pin": payload.rotary_encoder.clk_pin,
                    "dt_pin": payload.rotary_encoder.dt_pin,
                    "sw_pin": payload.rotary_encoder.sw_pin,
                },
                "ir_remote": {
                    "enabled": payload.ir_remote.enabled,
                    "gpio_pin": payload.ir_remote.gpio_pin,
                },
            }

            await hardware_service.save_config(config)
            logger.info(f"Hardware config saved: audio={payload.audio.id}, screen={payload.screen.type}")

            # Apply config.txt changes and reboot (after HTTP response is sent)
            async def _delayed_apply():
                await asyncio.sleep(1)  # Allow HTTP response to flush to the client
                try:
                    await hardware_service.apply_and_reboot()
                except Exception as e:
                    logger.error(f"Hardware apply/reboot failed: {e}")

            background_tasks.add_task(_delayed_apply)

            return {"status": "rebooting"}

    # Mac ROC Streaming configuration
    @router.put("/mac-roc")
    async def set_mac_roc_config(payload: MacRocConfigRequest):
        """
        Update Mac ROC streaming configuration and restart the service.

        This endpoint:
        1. Saves settings to settings.json
        2. Regenerates mac.env from the saved settings (does NOT touch routing.env)
        3. Restarts milo-mac.service to apply changes
        """
        async with api_error_handler("Error updating Mac ROC config", logger):
            target_latency_ms = payload.target_latency_ms
            latency_profile = payload.latency_profile
            frame_length_ms = payload.frame_length_ms

            mac_config = {
                'target_latency_ms': target_latency_ms,
                'latency_profile': latency_profile,
                'frame_length_ms': frame_length_ms
            }
            success = await settings.set_setting('mac', mac_config)
            if not success:
                raise HTTPException(status_code=500, detail="Failed to save Mac ROC settings")

            MacEnv.regenerate(mac_config)

            restart_success = await systemd_manager.restart("milo-mac.service")
            if not restart_success:
                logger.warning("Failed to restart milo-mac.service, settings saved but not applied")

            # service_restarted stays in the HTTP response only.
            await state_machine.broadcast(MacRocChanged(config=MacRocConfig(**mac_config)))

            return {
                "status": "success",
                "config": mac_config,
                "service_restarted": restart_success
            }

    # Radio settings (Shazam recognition)
    @router.put("/radio-settings")
    async def set_radio_settings(payload: RadioSettingsRequest):
        radio_config = {
            'shazam_enabled': payload.shazam_enabled
        }

        async def apply_to_radio():
            try:
                source = state_machine.get_source(AudioSource.RADIO)
                if source:
                    return await source.on_shazam_setting_changed(payload.shazam_enabled)
                return True
            except Exception as e:
                logger.error(f"Error applying radio settings: {e}")
                return False

        return await _handle_setting_update(
            payload,
            validator=lambda p: True,  # Validated by Pydantic
            setter=lambda: settings.set_setting('radio', radio_config),
            event=RadioSettingsChanged(config=RadioSettingsConfig(**radio_config)),
            reload_callback=apply_to_radio
        )

    # Music Library settings (one tab per storage space, or all merged)
    @router.put("/music-library-settings")
    async def set_music_library_settings(payload: MusicLibrarySettingsRequest):
        ml_config = {'separate_storages': payload.separate_storages}
        return await _handle_setting_update(
            payload,
            validator=lambda p: True,  # Validated by Pydantic
            setter=lambda: settings.set_setting('music_library', ml_config),
            event=MusicLibrarySettingsChanged(
                config=MusicLibrarySettingsConfig(**ml_config)
            ),
        )

    # Qobuz settings (allow the mobile app to control volume)
    @router.put("/qobuz-settings")
    async def set_qobuz_settings(payload: QobuzSettingsRequest):
        qobuz_config = {
            'allow_app_volume': payload.allow_app_volume
        }

        async def apply_to_qobuz():
            try:
                source = state_machine.get_source(AudioSource.QOBUZ)
                if source:
                    return await source.on_allow_app_volume_changed(payload.allow_app_volume)
                return True
            except Exception as e:
                logger.error(f"Error applying qobuz settings: {e}")
                return False

        return await _handle_setting_update(
            payload,
            validator=lambda p: True,  # Validated by Pydantic
            setter=lambda: settings.set_setting('qobuz', qobuz_config),
            event=QobuzSettingsChanged(config=QobuzSettingsConfig(**qobuz_config)),
            reload_callback=apply_to_qobuz
        )

    # Spotify settings (crossfade duration, written into go-librespot's config)
    @router.put("/spotify-settings")
    async def set_spotify_settings(payload: SpotifySettingsRequest):
        spotify_config = {'crossfade_duration': payload.crossfade_duration}

        async def apply_to_spotify():
            source = state_machine.get_source(AudioSource.SPOTIFY)
            if not source:
                return False
            return await source.on_spotify_settings_changed(payload.apply_now)

        return await _handle_setting_update(
            payload,
            validator=lambda p: True,  # Validated by Pydantic
            setter=lambda: settings.set_setting('spotify', spotify_config),
            event=SpotifySettingsChanged(config=SpotifySettingsConfig(**spotify_config)),
            reload_callback=apply_to_spotify
        )

    return router
