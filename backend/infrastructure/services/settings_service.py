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

class SettingsService:
    """Simplified settings manager with support for 0 = disabled"""

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.settings_file = '/var/lib/milo/settings.json'
        self._cache = None
        self._file_lock = asyncio.Lock()  # Native async lock instead of fcntl.flock
        
        self.defaults = {
            "language": "english",
            "volume": {
                "limit_min_db": -80.0,
                "limit_max_db": -21.0,
                "restore_last_volume": False,
                "startup_volume_db": -30.0,
                "step_mobile_db": 3.0,
                "step_rotary_db": 2.0
            },
            "screen": {
                "timeout_enabled": True,
                "timeout_seconds": 10,
                "brightness_on": 5
            },
            "spotify": {
                "auto_disconnect_delay": 10.0
            },
            "podcast": {
                "taddy_user_id": "",
                "taddy_api_key": ""
            },
            "routing": {
                "multiroom_enabled": False,
                "dsp_effects_enabled": False
            },
            "dock": {
                "enabled_apps": ["spotify", "bluetooth", "mac", "radio", "podcast", "multiroom", "dsp", "settings"]
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

                    # Migration display → screen
                    if 'display' in settings:
                        display_config = settings.pop('display')
                        if 'screen' not in settings:
                            settings['screen'] = {
                                'timeout_seconds': display_config.get('screen_timeout_seconds', 10),
                                'brightness_on': display_config.get('brightness_on', 5)
                            }

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
        
        # Language
        valid_languages = ['french', 'english', 'spanish', 'hindi', 'chinese', 'portuguese', 'italian', 'german']
        validated['language'] = settings.get('language') if settings.get('language') in valid_languages else 'english'
        
        # Volume (all values in dB, -80 to 0 range)
        vol_input = settings.get('volume', {})
        vol = {}

        # Limits in dB (-80 to 0)
        vol['limit_min_db'] = max(-80.0, min(0.0, float(vol_input.get('limit_min_db', -80.0))))
        vol['limit_max_db'] = max(-80.0, min(0.0, float(vol_input.get('limit_max_db', -21.0))))

        # Guarantee minimum gap of 6 dB
        if vol['limit_max_db'] - vol['limit_min_db'] < 6.0:
            vol['limit_max_db'] = vol['limit_min_db'] + 6.0
            if vol['limit_max_db'] > 0.0:
                vol['limit_max_db'] = 0.0
                vol['limit_min_db'] = -6.0

        vol['restore_last_volume'] = bool(vol_input.get('restore_last_volume', False))
        vol['startup_volume_db'] = max(vol['limit_min_db'], min(vol['limit_max_db'], float(vol_input.get('startup_volume_db', -30.0))))
        vol['step_mobile_db'] = max(1.0, min(6.0, float(vol_input.get('step_mobile_db', 3.0))))
        vol['step_rotary_db'] = max(1.0, min(6.0, float(vol_input.get('step_rotary_db', 2.0))))
        validated['volume'] = vol
        
        # Screen - MODIFIED: Accept 0 for timeout_seconds (disabled)
        screen_input = settings.get('screen', {})
        timeout_seconds_raw = int(screen_input.get('timeout_seconds', 10))

        validated['screen'] = {
            # 0 = disabled, otherwise minimum 3 seconds
            'timeout_seconds': 0 if timeout_seconds_raw == 0 else max(3, min(9999, timeout_seconds_raw)),
            'brightness_on': max(1, min(10, int(screen_input.get('brightness_on', 5))))
        }
        
        # Spotify - MODIFIED: Accept 0 for auto_disconnect_delay (disabled)
        spotify_input = settings.get('spotify', {})
        disconnect_delay_raw = float(spotify_input.get('auto_disconnect_delay', 10.0))

        validated['spotify'] = {
            # 0 = disabled, otherwise minimum 1.0 second, maximum 1h (3600s)
            'auto_disconnect_delay': 0.0 if disconnect_delay_raw == 0.0 else max(1.0, min(9999.0, disconnect_delay_raw))
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
        all_valid_apps = ["spotify", "bluetooth", "mac", "radio", "podcast", "multiroom", "equalizer", "settings"]
        audio_sources = ["spotify", "bluetooth", "mac", "radio", "podcast"]
        other_apps = ["multiroom", "equalizer", "settings"]

        enabled_apps = dock_input.get('enabled_apps', [])
        filtered_apps = [app for app in enabled_apps if app in all_valid_apps]

        # Check that at least one audio source is enabled
        enabled_audio_sources = [app for app in filtered_apps if app in audio_sources]
        if not enabled_audio_sources:
            # Force at least spotify if no audio source
            filtered_apps = ['spotify'] + [app for app in filtered_apps if app in other_apps]
        
        validated['dock'] = {
            'enabled_apps': filtered_apps if filtered_apps else self.defaults['dock']['enabled_apps'].copy()
        }

        # Routing (multiroom + equalizer)
        routing_input = settings.get('routing', {})
        validated['routing'] = {
            'multiroom_enabled': bool(routing_input.get('multiroom_enabled', False)),
            'equalizer_enabled': bool(routing_input.get('equalizer_enabled', False))
        }

        # Equalizer (saved_bands) - Preserve equalizer section without strict validation
        equalizer_input = settings.get('equalizer', {})
        if equalizer_input:
            # Preserve equalizer section as-is (no strict validation)
            validated['equalizer'] = equalizer_input

        # Radio (favorites + broken_stations) - Preserve radio section without strict validation
        radio_input = settings.get('radio', {})
        if radio_input:
            # Preserve radio section as-is (no strict validation)
            validated['radio'] = radio_input

        # DSP (linked_groups, presets) - Preserve DSP section without strict validation
        dsp_input = settings.get('dsp', {})
        if dsp_input:
            # Preserve DSP section as-is (no strict validation)
            validated['dsp'] = dsp_input

        # Multiroom (client_types for crossover) - Preserve multiroom section without strict validation
        multiroom_input = settings.get('multiroom', {})
        if multiroom_input:
            # Preserve multiroom section as-is (no strict validation)
            validated['multiroom'] = multiroom_input

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

    def invalidate_cache(self) -> None:
        """Invalidates cache to force a reload"""
        self._cache = None

    async def set_setting(self, key_path: str, value: Any) -> bool:
        """Sets a setting and invalidates cache (async)"""
        try:
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

        except Exception as e:
            self.logger.error(f"Error setting {key_path}: {e}")
            return False
    
    def get_volume_config(self) -> Dict[str, Any]:
        """Synchronous helper method (uses cache only)"""
        volume_settings = self._cache.get('volume', {}) if self._cache else {}
        return {
            "limit_min_db": volume_settings.get("limit_min_db", -80.0),
            "limit_max_db": volume_settings.get("limit_max_db", -21.0),
            "startup_volume_db": volume_settings.get("startup_volume_db", -30.0),
            "restore_last_volume": volume_settings.get("restore_last_volume", False),
            "step_mobile_db": volume_settings.get("step_mobile_db", 3.0),
            "step_rotary_db": volume_settings.get("step_rotary_db", 2.0)
        }

    async def get_volume_config_async(self) -> Dict[str, Any]:
        """Async helper method to get volume config"""
        volume_settings = await self.get_setting('volume') or {}
        return {
            "limit_min_db": volume_settings.get("limit_min_db", -80.0),
            "limit_max_db": volume_settings.get("limit_max_db", -21.0),
            "startup_volume_db": volume_settings.get("startup_volume_db", -30.0),
            "restore_last_volume": volume_settings.get("restore_last_volume", False),
            "step_mobile_db": volume_settings.get("step_mobile_db", 3.0),
            "step_rotary_db": volume_settings.get("step_rotary_db", 2.0)
        }