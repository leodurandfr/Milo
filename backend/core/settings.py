# backend/core/settings.py
"""
Settings management service - OPTIM version with async I/O
"""
import copy
import json
import os
import logging
import re
import aiofiles
import asyncio
from pathlib import Path
from typing import Dict, Any

from backend.config.constants import (
    ALLOWED_FRAME_LENGTHS,
    ALLOWED_LATENCY_PROFILES,
    AUDIO_SOURCE_APPS,
    DEFAULT_DOCK_APPS,
    DEFAULT_ROC_CONFIG,
    DEFAULT_VOLUME_DB,
    MAX_VOLUME_DB,
    MIN_VOLUME_DB,
    SETTINGS_FILE,
    UTILITY_DOCK_APPS,
    VALID_DOCK_APPS,
    VALID_LANGUAGES,
)
from backend.hardware.fan import (
    DEFAULT_CURVE,
    TARGET_TEMP_DEFAULT_C,
    VALID_MODES,
    clamp_target_temp,
    sanitize_curve,
)
from backend.shared.decorators import handle_errors
from backend.shared.persistence import (
    SchemaVersionMismatch,
    check_schema_version,
    load_versioned_json,
    load_versioned_json_sync,
    save_versioned_json,
)


# A dotted version, as the update flows read them out of a GitHub tag. Shape
# only: whether a stored version exists upstream is the update flow's business.
VERSION_PATTERN = re.compile(r"^\d+(?:\.\d+)*$")


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

        # The single declaration of every settings default. `_validate_and_merge`
        # reads its fallback operands from here rather than restating them, so a
        # value changes in one place; `tests/architecture/test_settings_defaults.py`
        # holds that property.
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
            "music_library": {
                # True → one tab per storage space (a USB key, a NAS share) in
                # the library view. False → every space merged into one catalog.
                # Separate by default: a key and a NAS hold different collections,
                # and merging them is the deliberate choice, not the neutral one.
                "separate_storages": True
            },
            "qobuz": {
                # False → the local qobuz-proxy backend stays at unity gain and the
                # Qobuz app's volume slider is inert (CamillaDSP owns volume). True →
                # the app slider controls qobuz-proxy's software volume.
                "allow_app_volume": False
            },
            "spotify": {
                # Crossfade between consecutive tracks, in ms. 0 disables it and
                # keeps go-librespot's original gapless read path untouched.
                # SpotifySource writes it into go-librespot's config.yml.
                "crossfade_duration": 0
            },
            "wifi": {
                "country": ""
            },
            "updates": {
                # {program_key: version} — a version deliberately installed past
                # the one `dependencies.env` declares, to try it before the set
                # is bumped. Empty is the normal state: the manifest is what a
                # unit runs. `VersionService.get_forced_versions` resolves it.
                "forced_versions": {}
            },
            # The `mac` section is the macOS/ROC sender's tuning, not a MAC
            # address. It used to be the one section with defaults that
            # `_validate_and_merge` emitted conditionally, which is why it was
            # absent from every settings.json until someone opened the Mac panel
            # — and why GET /bulk needed fallbacks at all.
            "mac": copy.deepcopy(DEFAULT_ROC_CONFIG),
            "fan": {
                "enabled": True,
                "mode": "auto",
                "manual_percent": 50,
                # The setpoint and the curve belong to the controller that acts on
                # them (hardware/fan.py owns the thermal band); this is where the
                # *settings* layer says which of its values is the default.
                "target_temp_c": TARGET_TEMP_DEFAULT_C,
                "curve": copy.deepcopy(DEFAULT_CURVE)
            }
        }

    def _default_settings(self) -> Dict[str, Any]:
        """A private copy of the defaults, safe to hand out and to mutate.

        Deep, not shallow: callers write into the returned dict (``_read_locked``
        feeds it straight to ``_apply_key``), and a shallow copy shares every
        section dict with ``self.defaults`` — so one write with no settings.json
        on disk turned the defaults themselves into the written value for the
        rest of the process.
        """
        return copy.deepcopy(self.defaults)

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
                self._cache = self._default_settings()
                await self.save_settings(self.defaults)
                return self._cache

            validated = self._validate_and_merge(data)
            self._cache = validated
            return validated

        except SchemaVersionMismatch:
            raise
        except json.JSONDecodeError as e:
            self.logger.error(f"JSON decode error in settings file: {e}")
            if os.path.exists(self.settings_file):
                async with aiofiles.open(self.settings_file, 'r', encoding='utf-8') as src:
                    content = await src.read()
                await self._backup_corrupted_file(content)
            self._cache = self._default_settings()
            await self.save_settings(self.defaults)
            return self._cache
        except Exception as e:
            self.logger.error(f"Error loading settings: {e}")
            self._cache = self._default_settings()
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
        """Validation and merge with defaults - Support 0 = disabled

        Every fallback operand below reads from ``self.defaults``: that dict is
        the single declaration of what a missing key resolves to. The clamp
        bounds stay literal on purpose — a tolerance is not a default, and it is
        deliberately wider than the matching request model's ``ge``/``le`` in
        ``api/models.py`` (a stored value outside the write range must be
        reported, not rejected; see the header of ``models/settings_config.py``).
        """
        d = self.defaults
        validated = {}

        # Setup completed flag (first-boot wizard)
        validated['setup_completed'] = bool(settings.get('setup_completed', d['setup_completed']))

        # Language
        validated['language'] = settings.get('language') if settings.get('language') in VALID_LANGUAGES else d['language']

        # Volume (all values in dB, -80 to 0 range)
        vol_input = settings.get('volume', {})
        vol_d = d['volume']
        vol = {}

        # Limits in dB, clamped to the technical range the volume domain declares
        vol['limit_min_db'] = max(MIN_VOLUME_DB, min(MAX_VOLUME_DB, float(vol_input.get('limit_min_db', vol_d['limit_min_db']))))
        vol['limit_max_db'] = max(MIN_VOLUME_DB, min(MAX_VOLUME_DB, float(vol_input.get('limit_max_db', vol_d['limit_max_db']))))

        # Guarantee minimum gap of 6 dB
        if vol['limit_max_db'] - vol['limit_min_db'] < 6.0:
            vol['limit_max_db'] = vol['limit_min_db'] + 6.0
            if vol['limit_max_db'] > 0.0:
                vol['limit_max_db'] = 0.0
                vol['limit_min_db'] = -6.0

        vol['restore_last_volume'] = bool(vol_input.get('restore_last_volume', vol_d['restore_last_volume']))
        vol['startup_volume_db'] = max(vol['limit_min_db'], min(vol['limit_max_db'], float(vol_input.get('startup_volume_db', vol_d['startup_volume_db']))))
        # The four step sizes share one rule; only the input they read differs.
        for step_key in ('step_mobile_db', 'step_rotary_db', 'step_bt_remote_db', 'step_ir_remote_db'):
            vol[step_key] = max(1.0, min(6.0, float(vol_input.get(step_key, vol_d[step_key]))))
        validated['volume'] = vol

        # Screen - MODIFIED: Accept 0 for timeout_seconds (disabled)
        screen_input = settings.get('screen', {})
        screen_d = d['screen']
        timeout_seconds_raw = int(screen_input.get('timeout_seconds', screen_d['timeout_seconds']))

        validated['screen'] = {
            # 0 = disabled, otherwise minimum 3 seconds
            'timeout_seconds': 0 if timeout_seconds_raw == 0 else max(3, min(9999, timeout_seconds_raw)),
            'brightness_on': max(1, min(10, int(screen_input.get('brightness_on', screen_d['brightness_on'])))),
            'screensaver_enabled': bool(screen_input.get('screensaver_enabled', screen_d['screensaver_enabled'])),
            'screensaver_delay_seconds': max(5, min(1800, int(screen_input.get('screensaver_delay_seconds', screen_d['screensaver_delay_seconds'])))),
            'ui_scale': max(0.5, min(2.0, float(screen_input.get('ui_scale', screen_d['ui_scale'])))),
            'color_filter_enabled': bool(screen_input.get('color_filter_enabled', screen_d['color_filter_enabled'])),
            'color_filter_warmth': max(0, min(100, int(screen_input.get('color_filter_warmth', screen_d['color_filter_warmth']))))
        }

        # Dock with validation for at least one audio source
        dock_input = settings.get('dock', {})

        enabled_apps = dock_input.get('enabled_apps', d['dock']['enabled_apps'])
        filtered_apps = [app for app in enabled_apps if app in VALID_DOCK_APPS]

        # Check that at least one audio source is enabled
        enabled_audio_sources = [app for app in filtered_apps if app in AUDIO_SOURCE_APPS]
        if not enabled_audio_sources:
            # Force at least spotify if no audio source
            filtered_apps = ['spotify'] + [app for app in filtered_apps if app in UTILITY_DOCK_APPS]

        # No empty-list fallback: the rule above seeds `filtered_apps` with
        # spotify whenever it holds no audio source, so it cannot be empty here.
        validated['dock'] = {'enabled_apps': filtered_apps}

        # Routing (multiroom + equalizer effects)
        routing_input = settings.get('routing', {})
        routing_d = d['routing']
        validated['routing'] = {
            'multiroom_enabled': bool(routing_input.get('multiroom_enabled', routing_d['multiroom_enabled'])),
            'equalizer_effects_enabled': bool(routing_input.get('equalizer_effects_enabled', routing_d['equalizer_effects_enabled']))
        }

        # Mac ROC streaming settings
        mac_input = settings.get('mac', {})
        mac_d = d['mac']
        profile = mac_input.get('latency_profile', mac_d['latency_profile'])
        frame_length = mac_input.get('frame_length_ms', mac_d['frame_length_ms'])
        validated['mac'] = {
            'target_latency_ms': max(20, min(500, int(mac_input.get('target_latency_ms', mac_d['target_latency_ms'])))),
            'latency_profile': profile if profile in ALLOWED_LATENCY_PROFILES else mac_d['latency_profile'],
            'frame_length_ms': frame_length if frame_length in ALLOWED_FRAME_LENGTHS else mac_d['frame_length_ms']
        }

        # Audio (auto-stop on pause)
        audio_input = settings.get('audio', {})
        try:
            stop_raw = float(audio_input.get('auto_stop_delay', d['audio']['auto_stop_delay']))
        except (TypeError, ValueError):
            stop_raw = d['audio']['auto_stop_delay']
        validated['audio'] = {
            # 0 = disabled, otherwise clamp to [1.0, 9999.0]
            'auto_stop_delay': 0.0 if stop_raw == 0.0 else max(1.0, min(9999.0, stop_raw))
        }

        # Radio settings
        radio_input = settings.get('radio', {})
        validated['radio'] = {
            'shazam_enabled': bool(radio_input.get('shazam_enabled', d['radio']['shazam_enabled']))
        }

        # Music Library settings
        ml_input = settings.get('music_library', {})
        validated['music_library'] = {
            'separate_storages': bool(ml_input.get(
                'separate_storages', d['music_library']['separate_storages']
            ))
        }

        # Qobuz settings
        qobuz_input = settings.get('qobuz', {})
        validated['qobuz'] = {
            'allow_app_volume': bool(qobuz_input.get('allow_app_volume', d['qobuz']['allow_app_volume']))
        }

        # Spotify settings — clamp wider than SpotifySettingsRequest's 0..12000
        # so a stored out-of-range crossfade surfaces on the settings page
        # instead of being rejected on load.
        spotify_input = settings.get('spotify', {})
        validated['spotify'] = {
            'crossfade_duration': max(0, min(60000, int(
                spotify_input.get('crossfade_duration', d['spotify']['crossfade_duration'])
            )))
        }

        # WiFi regulatory domain
        wifi_input = settings.get('wifi', {})
        country_raw = str(wifi_input.get('country', d['wifi']['country']))
        country_valid = len(country_raw) == 2 and country_raw.isalpha() and country_raw.isupper()
        validated['wifi'] = {
            'country': country_raw if country_valid else d['wifi']['country']
        }

        # Forced program versions. Shape only — which keys name a program is the
        # update catalog's to say, and the route that writes them checks against
        # it; a malformed version is dropped rather than clamped, since there is
        # no nearest valid version to fall back to.
        forced_input = settings.get('updates', {}).get(
            'forced_versions', d['updates']['forced_versions']
        )
        validated['updates'] = {
            'forced_versions': {
                str(key): version
                for key, version in forced_input.items()
                if isinstance(version, str) and VERSION_PATTERN.match(version)
            } if isinstance(forced_input, dict) else dict(d['updates']['forced_versions'])
        }

        # Multiroom (client_types for crossover) - Preserve multiroom section without strict validation
        multiroom_input = settings.get('multiroom', {})
        if multiroom_input:
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
        fan_d = d['fan']
        mode = fan_input.get('mode', fan_d['mode'])
        validated['fan'] = {
            'enabled': bool(fan_input.get('enabled', fan_d['enabled'])),
            'mode': mode if mode in VALID_MODES else fan_d['mode'],
            'manual_percent': max(0, min(100, int(fan_input.get('manual_percent', fan_d['manual_percent'])))),
            'target_temp_c': clamp_target_temp(fan_input.get('target_temp_c', fan_d['target_temp_c'])),
            'curve': sanitize_curve(fan_input.get('curve', fan_d['curve']))
        }

        return validated

    def get_setting_sync(self, key_path: str) -> Any:
        """Get a setting by path (SYNCHRONOUS — bootstrap and sync property reads only).

        Prefer the async ``get_setting`` from runtime code. The legitimate
        callers are:

        * ``AudioRoutingService.regenerate_env_files`` — derives the three
          env-file artifacts from settings during boot (event loop not yet
          running) and during ``_detect_initial_state``.
        * ``AudioRoutingService.multiroom_enabled`` property — hot-path
          sync read used by the state machine when aggregating ``full_state``
          for source/system events.

        Loaded data is run through ``_validate_and_merge`` before caching
        so missing keys (e.g. older installs without a ``routing`` block)
        resolve to validated defaults rather than ``None``.

        Raises SchemaVersionMismatch on version drift, exactly like
        ``load_settings``: this is the first reader of settings.json on every
        boot, and a stale shape consumed here is a stale shape written into the
        three env files before any consumer gets the chance to refuse it.
        """
        if not self._cache:
            try:
                data = load_versioned_json_sync(
                    Path(self.settings_file), self.SCHEMA_VERSION
                )
                self._cache = self._validate_and_merge(data) if data else self._default_settings()
            except SchemaVersionMismatch:
                raise
            except Exception as e:
                self.logger.warning(f"get_setting_sync fallback to defaults: {e}")
                self._cache = self._default_settings()

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

    @staticmethod
    def _apply_key(settings: Dict[str, Any], key_path: str, value: Any) -> None:
        """Assign ``value`` at a dotted ``key_path``, creating intermediate dicts."""
        keys = key_path.split('.')
        current = settings
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]
        current[keys[-1]] = value

    async def set_setting(self, key_path: str, value: Any) -> bool:
        """Sets a setting atomically and invalidates cache (async).

        Lossy variant: swallows exceptions and returns ``False`` on failure.
        Use :meth:`set_setting_strict` from code paths that must not silently
        desync (e.g. multiroom transition).
        """
        return await self.set_settings({key_path: value})

    async def set_setting_strict(self, key_path: str, value: Any) -> None:
        """Set a setting atomically; raise on disk-write failure.

        Failure-loud variant of :meth:`set_setting` for code paths where a
        silently-swallowed write would leave the system in a split-brain
        state (settings.json vs derived artifacts vs in-memory caches).
        """
        await self.set_settings_strict({key_path: value})

    @handle_errors(default=False)
    async def set_settings(self, updates: Dict[str, Any]) -> bool:
        """Atomically set multiple related settings in one read-modify-write.

        ``updates`` maps dotted key paths to values. All keys land in a single
        ``os.replace`` (or none do), closing the torn-write window that AND-ing
        independent :meth:`set_setting` calls leaves open — a crash between two
        such calls would persist half a logically-coupled pair (e.g. a volume
        ``limit_min_db`` without its ``limit_max_db``).

        An empty ``updates`` is a no-op (no write, cache untouched) so callers
        that conditionally build the map don't rewrite the file for nothing.

        Lossy variant: swallows exceptions and returns ``False`` on failure.
        """
        if not updates:
            return True

        async with self._file_lock:
            settings = await self._read_locked()
            for key_path, value in updates.items():
                self._apply_key(settings, key_path, value)
            success = await self._write_locked(settings)

        if success:
            self._cache = None

        return success

    async def set_settings_strict(self, updates: Dict[str, Any]) -> None:
        """Atomic multi-key write; raise on disk-write failure.

        Failure-loud variant of :meth:`set_settings`.
        """
        if not updates:
            return

        async with self._file_lock:
            settings = await self._read_locked()
            for key_path, value in updates.items():
                self._apply_key(settings, key_path, value)
            success = await self._write_locked(settings)

        if not success:
            raise SettingsWriteError(
                f"Failed to persist settings {sorted(updates)}"
            )

        self._cache = None

    async def _backup_corrupted_file(self, content: str) -> str:
        """Snapshot corrupt settings content to a sibling ``.corrupted`` file.

        Never clobbers an existing backup: falls through to ``.corrupted.1``,
        ``.corrupted.2`` … so the first (often most recoverable) snapshot is
        preserved across repeated corruption events. Returns the path written.
        """
        backup = self.settings_file + '.corrupted'
        n = 1
        while os.path.exists(backup):
            backup = f"{self.settings_file}.corrupted.{n}"
            n += 1
        async with aiofiles.open(backup, 'w', encoding='utf-8') as dst:
            await dst.write(content)
        self.logger.warning(f"Corrupted settings snapshot saved to: {backup}")
        return backup

    async def _read_locked(self) -> Dict[str, Any]:
        """Read + validate settings. Caller must hold self._file_lock.

        Falls back to defaults on missing/empty/corrupt files so that a write
        operation can recover the file rather than fail. On corruption the raw
        content is snapshotted to ``.corrupted`` first — otherwise the caller's
        subsequent ``_write_locked`` would overwrite the (possibly recoverable)
        corrupt file with defaults, silently losing every setting.

        A version it did not verify is the one thing it must not fall back on:
        ``_write_locked`` re-stamps whatever this returns at the current
        ``SCHEMA_VERSION``, so consuming a drifted file here would migrate it in
        silence. Raises SchemaVersionMismatch instead, and the write is refused.
        """
        if not os.path.exists(self.settings_file):
            return self._default_settings()
        try:
            async with aiofiles.open(self.settings_file, 'r', encoding='utf-8') as f:
                content = await f.read()
            if not content.strip():
                return self._default_settings()
            data = json.loads(content)
            check_schema_version(Path(self.settings_file), data, self.SCHEMA_VERSION)
            return self._validate_and_merge(data)
        except json.JSONDecodeError:
            self.logger.error("settings.json corrupt during locked read; backing up and using defaults")
            await self._backup_corrupted_file(content)
            return self._default_settings()

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
