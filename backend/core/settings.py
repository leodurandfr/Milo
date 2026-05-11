# backend/infrastructure/services/settings_service.py
"""
Settings management service - OPTIM version with async I/O
"""
import json
import os
import logging
import aiofiles
import asyncio
from typing import Dict, Any

from backend.config.constants import DEFAULT_VOLUME_DB, VALID_DOCK_APPS, AUDIO_SOURCE_APPS, UTILITY_DOCK_APPS, DEFAULT_DOCK_APPS, SETTINGS_FILE, VALID_LANGUAGES
from backend.shared.decorators import handle_errors

class SettingsService:
    """Simplified settings manager with support for 0 = disabled"""

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.settings_file = str(SETTINGS_FILE)
        self._cache = None
        self._file_lock = asyncio.Lock()  # Native async lock instead of fcntl.flock

        self.defaults = {
            "setup_completed": False,
            "language": "english",
            "volume": {
                "limit_min_db": -80.0,
                "limit_max_db": -20.0,
                "restore_last_volume": True,
                "startup_volume_db": DEFAULT_VOLUME_DB,
                "step_mobile_db": 2.0,
                "step_rotary_db": 2.0,
                "step_bt_remote_db": 2.0
            },
            "screen": {
                "timeout_seconds": 120,
                "brightness_on": 5,
                "screensaver_enabled": True,
                "screensaver_delay_seconds": 120,
                "ui_scale": 1.0,
                "color_filter_enabled": False,
                "color_filter_warmth": 50
            },
            "audio": {
                "auto_disconnect_delay": 120.0,
            },
            "podcast": {
                "taddy_user_id": "",
                "taddy_api_key": ""
            },
            "routing": {
                "multiroom_enabled": False,
                "equalizer_effects_enabled": False
            },
            "dock": {
                "enabled_apps": list(DEFAULT_DOCK_APPS)
            },
            "radio": {
                "shazam_enabled": True
            },
            "wifi": {
                "country": ""
            }
        }

    async def load_settings(self) -> Dict[str, Any]:
        """Loads and validates settings with async lock"""
        try:
            if os.path.exists(self.settings_file):
                async with self._file_lock:
                    async with aiofiles.open(self.settings_file, 'r', encoding='utf-8') as f:
                        content = await f.read()

                    settings = json.loads(content)

                    # Merge with defaults and validate
                    validated = self._validate_and_merge(settings)

                    self._cache = validated
                    return validated
            else:
                # Create file with defaults
                self._cache = self.defaults.copy()
                await self.save_settings(self.defaults)
                return self._cache

        except json.JSONDecodeError as e:
            self.logger.error(f"JSON decode error in settings file: {e}")
            # Save corrupted file
            if os.path.exists(self.settings_file):
                backup_corrupted = self.settings_file + '.corrupted'
                async with aiofiles.open(self.settings_file, 'r', encoding='utf-8') as src:
                    content = await src.read()
                async with aiofiles.open(backup_corrupted, 'w', encoding='utf-8') as dst:
                    await dst.write(content)
                self.logger.warning(f"Corrupted JSON saved to: {backup_corrupted}")
            self._cache = self.defaults.copy()
            await self.save_settings(self.defaults)
            return self._cache
        except Exception as e:
            self.logger.error(f"Error loading settings: {e}")
            self._cache = self.defaults.copy()
            return self._cache

    async def save_settings(self, settings: Dict[str, Any]) -> bool:
        """Saves with async lock and atomic write"""
        try:
            validated = self._validate_and_merge(settings)

            async with self._file_lock:
                # Atomic write via temp file
                temp_file = self.settings_file + '.tmp'

                # Generate JSON
                json_content = json.dumps(validated, ensure_ascii=False, indent=2)

                # Write content
                async with aiofiles.open(temp_file, 'w', encoding='utf-8') as f:
                    await f.write(json_content)
                    await f.write('\n')
                    await f.flush()
                    os.fsync(f.fileno())

                # Atomic rename
                os.replace(temp_file, self.settings_file)

            self._cache = validated
            self.logger.debug("Settings saved successfully")
            return True

        except Exception as e:
            self.logger.error(f"Error saving settings: {e}")
            # Clean up temp file on failure
            try:
                if os.path.exists(self.settings_file + '.tmp'):
                    os.remove(self.settings_file + '.tmp')
            except Exception as cleanup_error:
                self.logger.warning(f"Failed to clean up temp file: {cleanup_error}")
            return False

    def _validate_and_merge(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        """Validation and merge with defaults - Support 0 = disabled"""
        validated = {}

        # Setup completed flag (first-boot wizard)
        validated['setup_completed'] = bool(settings.get('setup_completed', False))

        # Language
        validated['language'] = settings.get('language') if settings.get('language') in VALID_LANGUAGES else 'english'

        # Volume (all values in dB, -80 to 0 range)
        vol_input = settings.get('volume', {})
        vol = {}

        # Limits in dB (-80 to 0)
        vol['limit_min_db'] = max(-80.0, min(0.0, float(vol_input.get('limit_min_db', -80.0))))
        vol['limit_max_db'] = max(-80.0, min(0.0, float(vol_input.get('limit_max_db', -20.0))))

        # Guarantee minimum gap of 6 dB
        if vol['limit_max_db'] - vol['limit_min_db'] < 6.0:
            vol['limit_max_db'] = vol['limit_min_db'] + 6.0
            if vol['limit_max_db'] > 0.0:
                vol['limit_max_db'] = 0.0
                vol['limit_min_db'] = -6.0

        vol['restore_last_volume'] = bool(vol_input.get('restore_last_volume', True))
        vol['startup_volume_db'] = max(vol['limit_min_db'], min(vol['limit_max_db'], float(vol_input.get('startup_volume_db', DEFAULT_VOLUME_DB))))
        vol['step_mobile_db'] = max(1.0, min(6.0, float(vol_input.get('step_mobile_db', 2.0))))
        vol['step_rotary_db'] = max(1.0, min(6.0, float(vol_input.get('step_rotary_db', 2.0))))
        vol['step_bt_remote_db'] = max(1.0, min(6.0, float(vol_input.get('step_bt_remote_db', 2.0))))
        validated['volume'] = vol

        # Screen - MODIFIED: Accept 0 for timeout_seconds (disabled)
        screen_input = settings.get('screen', {})
        timeout_seconds_raw = int(screen_input.get('timeout_seconds', 120))

        validated['screen'] = {
            # 0 = disabled, otherwise minimum 3 seconds
            'timeout_seconds': 0 if timeout_seconds_raw == 0 else max(3, min(9999, timeout_seconds_raw)),
            'brightness_on': max(1, min(10, int(screen_input.get('brightness_on', 5)))),
            'screensaver_enabled': bool(screen_input.get('screensaver_enabled', True)),
            'screensaver_delay_seconds': max(5, min(1800, int(screen_input.get('screensaver_delay_seconds', 120)))),
            'ui_scale': max(0.5, min(2.0, float(screen_input.get('ui_scale', 1.0)))),
            'color_filter_enabled': bool(screen_input.get('color_filter_enabled', False)),
            'color_filter_warmth': max(0, min(100, int(screen_input.get('color_filter_warmth', 50))))
        }

        # Podcast credentials
        podcast_input = settings.get('podcast', {})
        validated['podcast'] = {
            'taddy_user_id': str(podcast_input.get('taddy_user_id', '')),
            'taddy_api_key': str(podcast_input.get('taddy_api_key', ''))
        }
        # Preserve credentials_validated_at if present
        if 'credentials_validated_at' in podcast_input:
            validated['podcast']['credentials_validated_at'] = int(podcast_input['credentials_validated_at'])

        # Dock with validation for at least one audio source
        dock_input = settings.get('dock', {})

        enabled_apps = dock_input.get('enabled_apps', [])
        filtered_apps = [app for app in enabled_apps if app in VALID_DOCK_APPS]

        # Check that at least one audio source is enabled
        enabled_audio_sources = [app for app in filtered_apps if app in AUDIO_SOURCE_APPS]
        if not enabled_audio_sources:
            # Force at least spotify if no audio source
            filtered_apps = ['spotify'] + [app for app in filtered_apps if app in UTILITY_DOCK_APPS]

        validated['dock'] = {
            'enabled_apps': filtered_apps if filtered_apps else self.defaults['dock']['enabled_apps'].copy()
        }

        # Routing (multiroom + equalizer effects)
        routing_input = settings.get('routing', {})
        validated['routing'] = {
            'multiroom_enabled': bool(routing_input.get('multiroom_enabled', False)),
            'equalizer_effects_enabled': bool(routing_input.get('equalizer_effects_enabled', False))
        }

        # Mac ROC streaming settings
        mac_input = settings.get('mac', {})
        if mac_input:
            validated['mac'] = {
                'target_latency_ms': max(5, min(500, int(mac_input.get('target_latency_ms', 200)))),
                'latency_profile': mac_input.get('latency_profile', 'responsive') if mac_input.get('latency_profile') in ['responsive', 'gradual', 'intact'] else 'responsive',
                'frame_length_ms': mac_input.get('frame_length_ms', 7) if mac_input.get('frame_length_ms') in [2, 4, 7, 8, 12] else 7
            }

        # Equalizer (saved_bands) - Preserve equalizer section without strict validation
        equalizer_input = settings.get('equalizer', {})
        if equalizer_input:
            # Preserve equalizer section as-is (no strict validation)
            validated['equalizer'] = equalizer_input

        # Audio (auto-disconnect on pause)
        # Migration: legacy spotify.auto_disconnect_delay / airplay.auto_disconnect_delay
        # are folded into audio.auto_disconnect_delay (max of both if both present),
        # then dropped from the validated output.
        audio_input = settings.get('audio', {})
        legacy_spotify_delay = settings.get('spotify', {}).get('auto_disconnect_delay')
        legacy_airplay_delay = settings.get('airplay', {}).get('auto_disconnect_delay')

        def _coerce_delay(value):
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        if 'auto_disconnect_delay' in audio_input:
            disconnect_raw = _coerce_delay(audio_input.get('auto_disconnect_delay'))
            if disconnect_raw is None:
                disconnect_raw = 120.0
        else:
            legacy_values = [
                v for v in (_coerce_delay(legacy_spotify_delay), _coerce_delay(legacy_airplay_delay))
                if v is not None
            ]
            disconnect_raw = max(legacy_values) if legacy_values else 120.0

        validated['audio'] = {
            # 0 = disabled, otherwise clamp to [1.0, 9999.0]
            'auto_disconnect_delay': 0.0 if disconnect_raw == 0.0 else max(1.0, min(9999.0, disconnect_raw))
        }

        # Radio settings
        radio_input = settings.get('radio', {})
        validated['radio'] = {
            'shazam_enabled': bool(radio_input.get('shazam_enabled', True))
        }

        # WiFi regulatory domain
        wifi_input = settings.get('wifi', {})
        country_raw = str(wifi_input.get('country', ''))
        validated['wifi'] = {
            'country': country_raw if len(country_raw) == 2 and country_raw.isalpha() and country_raw.isupper() else ''
        }

        # Multiroom (client_types for crossover) - Preserve multiroom section without strict validation
        multiroom_input = settings.get('multiroom', {})
        if multiroom_input:
            # Preserve multiroom section as-is (no strict validation)
            validated['multiroom'] = multiroom_input

        # Hardware (optional hardware feature settings)
        hardware_input = settings.get('hardware', {})
        if hardware_input:
            validated_hardware = {}
            bt_remote_input = hardware_input.get('bt_remote', {})
            if bt_remote_input:
                validated_hardware['bt_remote'] = {
                    'enabled': bool(bt_remote_input.get('enabled', False)),
                    'device_name_filter': str(bt_remote_input.get('device_name_filter', 'ANTICATER'))[:64],
                    'key_map': bt_remote_input.get('key_map', {}) if isinstance(bt_remote_input.get('key_map'), dict) else {}
                }
            ir_remote_input = hardware_input.get('ir_remote', {})
            if ir_remote_input:
                raw_device_id = ir_remote_input.get('device_id')
                device_id = raw_device_id if isinstance(raw_device_id, int) and 0 <= raw_device_id <= 0xFF else None
                raw_paired_at = ir_remote_input.get('paired_at')
                paired_at = float(raw_paired_at) if isinstance(raw_paired_at, (int, float)) else None
                validated_hardware['ir_remote'] = {
                    'enabled': bool(ir_remote_input.get('enabled', False)),
                    'device_id': device_id,
                    'paired_at': paired_at,
                }
            if validated_hardware:
                validated['hardware'] = validated_hardware

        return validated

    def get_setting_sync(self, key_path: str) -> Any:
        """Gets a setting by path (SYNCHRONOUS - uses cache or blocking load)"""
        if not self._cache:
            # Load synchronously if needed (blocking but rare)
            try:
                if os.path.exists(self.settings_file):
                    with open(self.settings_file, 'r', encoding='utf-8') as f:
                        self._cache = json.load(f)
                else:
                    self._cache = self.defaults.copy()
            except Exception:
                self._cache = self.defaults.copy()

        try:
            keys = key_path.split('.')
            value = self._cache
            for key in keys:
                value = value[key]
            return value
        except (KeyError, TypeError):
            return None

    async def get_setting(self, key_path: str) -> Any:
        """Gets a setting by path (async)"""
        if not self._cache:
            self._cache = await self.load_settings()

        try:
            keys = key_path.split('.')
            value = self._cache
            for key in keys:
                value = value[key]
            return value
        except (KeyError, TypeError):
            return None

    async def get_all_settings(self) -> Dict[str, Any]:
        """Return the full settings dict."""
        if not self._cache:
            self._cache = await self.load_settings()
        return dict(self._cache)

    def invalidate_cache(self) -> None:
        """Invalidates cache to force a reload"""
        self._cache = None

    @handle_errors(default=False)
    async def set_setting(self, key_path: str, value: Any) -> bool:
        """Sets a setting and invalidates cache (async)"""
        settings = await self.load_settings()

        keys = key_path.split('.')
        current = settings
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]

        current[keys[-1]] = value

        success = await self.save_settings(settings)

        # Invalidate cache to force reload
        if success:
            self._cache = None

        return success

    def get_volume_config(self) -> Dict[str, Any]:
        """Synchronous helper method (uses cache only)"""
        volume_settings = self._cache.get('volume', {}) if self._cache else {}
        return {
            "limit_min_db": volume_settings.get("limit_min_db", -80.0),
            "limit_max_db": volume_settings.get("limit_max_db", -20.0),
            "startup_volume_db": volume_settings.get("startup_volume_db", DEFAULT_VOLUME_DB),
            "restore_last_volume": volume_settings.get("restore_last_volume", True),
            "step_mobile_db": volume_settings.get("step_mobile_db", 2.0),
            "step_rotary_db": volume_settings.get("step_rotary_db", 2.0),
            "step_bt_remote_db": volume_settings.get("step_bt_remote_db", 2.0)
        }

    async def get_volume_config_async(self) -> Dict[str, Any]:
        """Async helper method to get volume config"""
        volume_settings = await self.get_setting('volume') or {}
        return {
            "limit_min_db": volume_settings.get("limit_min_db", -80.0),
            "limit_max_db": volume_settings.get("limit_max_db", -20.0),
            "startup_volume_db": volume_settings.get("startup_volume_db", DEFAULT_VOLUME_DB),
            "restore_last_volume": volume_settings.get("restore_last_volume", True),
            "step_mobile_db": volume_settings.get("step_mobile_db", 2.0),
            "step_rotary_db": volume_settings.get("step_rotary_db", 2.0),
            "step_bt_remote_db": volume_settings.get("step_bt_remote_db", 2.0)
        }
