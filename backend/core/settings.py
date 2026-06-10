# backend/core/settings.py
"""
Settings management service - OPTIM version with async I/O
"""
import json
import os
import logging
import aiofiles
import asyncio
from pathlib import Path
from typing import Dict, Any

from backend.config.constants import DEFAULT_VOLUME_DB, VALID_DOCK_APPS, AUDIO_SOURCE_APPS, UTILITY_DOCK_APPS, DEFAULT_DOCK_APPS, SETTINGS_FILE, VALID_LANGUAGES
from backend.shared.decorators import handle_errors
from backend.shared.persistence import (
    SchemaVersionMismatch,
    load_versioned_json,
    save_versioned_json,
)


class SettingsWriteError(RuntimeError):
    """Raised when persisting settings to disk fails.

    Use the strict write APIs (e.g. ``set_setting_strict``) from code paths
    that must not silently desync — they raise this instead of swallowing
    the failure.
    """


class SettingsService:
    """Simplified settings manager with support for 0 = disabled"""

    SCHEMA_VERSION: int = 4

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
                "step_bt_remote_db": 2.0,
                "step_ir_remote_db": 2.0
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
                "auto_stop_delay": 120.0,
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
            },
            "fan": {
                "enabled": True,
                "mode": "auto",
                "manual_percent": 50,
                "curve": [
                    {"temp_c": 55, "percent": 0},
                    {"temp_c": 66, "percent": 22},
                    {"temp_c": 79, "percent": 47},
                    {"temp_c": 82, "percent": 100}
                ]
            }
        }

    async def initialize(self) -> None:
        """Pre-load settings.json so a schema mismatch surfaces at boot.

        Raises SchemaVersionMismatch on version drift; the handler in
        dependencies.py::init_async logs the banner and SystemExit(1)s.
        """
        await self.load_settings()

    async def load_settings(self) -> Dict[str, Any]:
        """Loads and validates settings with async lock.

        Raises SchemaVersionMismatch on schema_version drift (caller is
        responsible for logging the banner and exiting). Corrupted JSON falls
        back to defaults (same behaviour as before).
        """
        try:
            async with self._file_lock:
                data = await load_versioned_json(Path(self.settings_file), self.SCHEMA_VERSION)

            if not data:
                # Fresh install — write defaults stamped with schema_version
                self._cache = self.defaults.copy()
                await self.save_settings(self.defaults)
                return self._cache

            validated = self._validate_and_merge(data)
            self._cache = validated
            return validated

        except SchemaVersionMismatch:
            raise
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
        """Saves with async lock and atomic write (schema_version stamped automatically)."""
        try:
            validated = self._validate_and_merge(settings)

            async with self._file_lock:
                await save_versioned_json(
                    Path(self.settings_file), validated, self.SCHEMA_VERSION
                )

            self._cache = validated
            self.logger.debug("Settings saved successfully")
            return True

        except Exception as e:
            self.logger.error(f"Error saving settings: {e}")
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
        vol['step_ir_remote_db'] = max(1.0, min(6.0, float(vol_input.get('step_ir_remote_db', 2.0))))
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
                'target_latency_ms': max(20, min(500, int(mac_input.get('target_latency_ms', 50)))),
                'latency_profile': mac_input.get('latency_profile', 'responsive') if mac_input.get('latency_profile') in ['responsive', 'gradual', 'intact'] else 'responsive',
                'frame_length_ms': mac_input.get('frame_length_ms', 4) if mac_input.get('frame_length_ms') in [2, 4, 6, 8, 10, 12] else 4
            }

        # Equalizer (saved_bands) - Preserve equalizer section without strict validation
        equalizer_input = settings.get('equalizer', {})
        if equalizer_input:
            # Preserve equalizer section as-is (no strict validation)
            validated['equalizer'] = equalizer_input

        # Audio (auto-stop on pause)
        audio_input = settings.get('audio', {})
        try:
            stop_raw = float(audio_input.get('auto_stop_delay', 120.0))
        except (TypeError, ValueError):
            stop_raw = 120.0
        validated['audio'] = {
            # 0 = disabled, otherwise clamp to [1.0, 9999.0]
            'auto_stop_delay': 0.0 if stop_raw == 0.0 else max(1.0, min(9999.0, stop_raw))
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

        # Fan control (optional — runtime PWM fan curve, see hardware/fan.py)
        fan_input = settings.get('fan', {})
        if fan_input:
            from backend.hardware.fan import VALID_MODES, clamp_target_temp, sanitize_curve
            mode = fan_input.get('mode', 'auto')
            validated['fan'] = {
                'enabled': bool(fan_input.get('enabled', True)),
                'mode': mode if mode in VALID_MODES else 'auto',
                'manual_percent': max(0, min(100, int(fan_input.get('manual_percent', 50)))),
                'target_temp_c': clamp_target_temp(fan_input.get('target_temp_c')),
                'curve': sanitize_curve(fan_input.get('curve'))
            }

        return validated

    def get_setting_sync(self, key_path: str) -> Any:
        """Get a setting by path (SYNCHRONOUS — bootstrap and sync property reads only).

        Prefer the async ``get_setting`` from runtime code. After Phase 4 of
        the multiroom-desync plan, the legitimate callers are:

        * ``AudioRoutingService.regenerate_env_files`` — derives the three
          env-file artifacts from settings during boot (event loop not yet
          running) and during ``_detect_initial_state``.
        * ``AudioRoutingService.multiroom_enabled`` property — hot-path
          sync read used by the state machine when aggregating ``full_state``
          for source/system events.
        * ``PodcastSource.__init__`` — credential read at construction time
          (cache is already populated by the bootstrap helper).

        Loaded data is run through ``_validate_and_merge`` before caching
        so missing keys (e.g. older installs without a ``routing`` block)
        resolve to validated defaults rather than ``None``.
        """
        if not self._cache:
            try:
                if os.path.exists(self.settings_file):
                    with open(self.settings_file, 'r', encoding='utf-8') as f:
                        raw = json.load(f)
                    self._cache = self._validate_and_merge(raw)
                else:
                    self._cache = self.defaults.copy()
            except Exception as e:
                self.logger.warning(f"get_setting_sync fallback to defaults: {e}")
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
        """Sets a setting atomically and invalidates cache (async).

        Lossy variant: swallows exceptions and returns ``False`` on failure.
        Use :meth:`set_setting_strict` from code paths that must not silently
        desync (e.g. multiroom transition).
        """
        async with self._file_lock:
            settings = await self._read_locked()

            keys = key_path.split('.')
            current = settings
            for key in keys[:-1]:
                if key not in current:
                    current[key] = {}
                current = current[key]

            current[keys[-1]] = value

            success = await self._write_locked(settings)

        if success:
            self._cache = None

        return success

    async def set_setting_strict(self, key_path: str, value: Any) -> None:
        """Set a setting atomically; raise on disk-write failure.

        Failure-loud variant of :meth:`set_setting` for code paths where a
        silently-swallowed write would leave the system in a split-brain
        state (settings.json vs derived artifacts vs in-memory caches).
        """
        async with self._file_lock:
            settings = await self._read_locked()

            keys = key_path.split('.')
            current = settings
            for key in keys[:-1]:
                if key not in current:
                    current[key] = {}
                current = current[key]

            current[keys[-1]] = value

            success = await self._write_locked(settings)

        if not success:
            raise SettingsWriteError(f"Failed to persist setting '{key_path}'")

        self._cache = None

    async def _read_locked(self) -> Dict[str, Any]:
        """Read + validate settings. Caller must hold self._file_lock.

        Falls back to defaults on missing/empty/corrupt files so that a write
        operation can recover the file rather than fail.
        """
        if not os.path.exists(self.settings_file):
            return self.defaults.copy()
        try:
            async with aiofiles.open(self.settings_file, 'r', encoding='utf-8') as f:
                content = await f.read()
            if not content.strip():
                return self.defaults.copy()
            return self._validate_and_merge(json.loads(content))
        except json.JSONDecodeError:
            self.logger.warning("settings.json corrupt during locked read; using defaults")
            return self.defaults.copy()

    async def _write_locked(self, settings: Dict[str, Any]) -> bool:
        """Validate + atomically write settings. Caller must hold self._file_lock."""
        try:
            validated = self._validate_and_merge(settings)
            await save_versioned_json(
                Path(self.settings_file), validated, self.SCHEMA_VERSION
            )
            self._cache = validated
            return True
        except Exception as e:
            self.logger.error(f"Error writing settings: {e}")
            return False
