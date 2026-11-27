# backend/core/settings.py
"""
Settings management service - OPTIM version with async I/O
"""
import copy
import logging
import re
import asyncio
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, get_args

from backend.config.constants import (
    ALLOWED_FRAME_LENGTHS,
    ALLOWED_LATENCY_PROFILES,
    ALLOWED_LOG_RETENTION_DAYS,
    ALLOWED_PACKET_LENGTHS,
    ALLOWED_SCREEN_THEMES,
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
from backend.core.models.ws_events import SatelliteUpdateVerdict
from backend.hardware.fan import (
    TARGET_TEMP_DEFAULT_C,
    VALID_MODES,
    clamp_target_temp,
)
from backend.shared.decorators import handle_errors
from backend.shared.persistence import (
    load_versioned_json,
    load_versioned_json_sync,
    save_versioned_json,
    set_aside,
    set_aside_sync,
)


# A dotted version, as the update flows read them out of a GitHub tag. Shape
# only: whether a stored version exists upstream is the update flow's business.
VERSION_PATTERN = re.compile(r"^\d+(?:\.\d+)*$")

# Tells an absent key from one whose stored value is None.
_MISSING = object()

# What `_validate_and_merge` raises on a stored value of the wrong shape: a
# section that is not an object (`"volume": null`), an entry missing a field it
# reads, a number that is a word or a list (`"timeout_seconds": "never"`), one
# no int can hold (an infinite float). A file holding one is set aside: left
# in place, it would read as the defaults and refuse every write, at every
# boot — the setup wizard's last step included.
_VALUE_IT_CANNOT_TAKE = (AttributeError, KeyError, TypeError, ValueError, OverflowError)


class SettingsWriteError(RuntimeError):
    """Raised when persisting settings fails: the disk write, or a key the
    validator would drop (one no section declares).

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
        # True while the cache holds defaults because settings.json could not be
        # read (an OSError on the card, a file that could not be renamed aside):
        # the file may still hold a set-up unit, so `read_failed` says so.
        self._read_failed = False
        self._file_lock = asyncio.Lock()  # Native async lock instead of fcntl.flock

        # The single declaration of every settings default. `_validate_and_merge`
        # reads its fallback operands from here rather than restating them, so a
        # value changes in one place; `tests/architecture/test_settings_defaults.py`
        # holds that property.
        self.defaults = {
            "setup_completed": False,
            # The wizard's choices are applied, and the unit waits for its first
            # link to its network before it counts as set up (`api/setup.py`).
            "setup_awaiting_network": False,
            "language": "english",
            "volume": {
                "limit_min_db": -80.0,
                "limit_max_db": -20.0,
                "restore_last_volume": True,
                "startup_volume_db": DEFAULT_VOLUME_DB,
                "step_interface_db": 2.0,
                "step_rotary_db": 2.0,
                "step_bt_remote_db": 2.0,
                "step_ir_remote_db": 2.0
            },
            "screen": {
                "timeout_seconds": 120,
                "brightness_on": 5,
                "auto_player_enabled": True,
                "auto_player_delay_seconds": 120,
                "ui_scale": 1.0,
                "color_filter_enabled": False,
                "color_filter_warmth": 50,
                "edge_inset_mm": 0.0,
                "theme": "auto"
            },
            "audio": {
                "auto_stop_delay": 120.0,
                # How long a source left with something to resume keeps it
                # (seconds); 0 keeps nothing past the departure, null keeps it
                # for good.
                "resume_window": 600.0,
                # Whether mpv's loudness filter evens out what each mpv source
                # plays, per source (MpvAudioSource applies it). On where the
                # level jumps from one station or show to the next; off where
                # the record is played as it was mastered.
                "normalization": {
                    "radio": True,
                    "podcast": True,
                    "cd": False,
                    "music_library": False,
                },
            },
            "routing": {
                "multiroom_enabled": False,
                "equalizer_effects_enabled": False
            },
            "dock": {
                "enabled_apps": list(DEFAULT_DOCK_APPS),
                # Where the dock offers the volume control, per screen that
                # shows it. Inside the iPhone app it is off: volume already
                # lives on the iPhone's Lock Screen and in its widget there.
                "volume": {
                    "milo_screen": True,
                    "desktop_browser": True,
                    "phone_browser": True,
                    "ios_app": False
                }
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
                "crossfade_duration": 0,
                # go-librespot always leaves the samples at unity (CamillaDSP
                # owns volume). False → the Spotify app's slider is inert.
                # True → it and Milō's volume are one level, both ways.
                "allow_app_volume": False
            },
            "wifi": {
                "country": ""
            },
            "system": {
                # How long the systemd journal is kept on the SD card, in days:
                # 7, 15 or 30 (ALLOWED_LOG_RETENTION_DAYS). Applied by
                # milo-set-journal-retention, and again at each start when the
                # drop-in it owns disagrees (core/system/journal_retention.py).
                # 15 by default (owner decision 2026-11-27): the rootfs file
                # keeps 7, its satellite twin's value, so a fresh server's
                # first start writes the 15-day drop-in.
                "log_retention_days": 15
            },
            "updates": {
                # {program_key: version} — a version deliberately installed past
                # the one `dependencies.env` declares, to try it before the set
                # is bumped. Empty is the normal state: the manifest is what a
                # unit runs. `VersionService.get_forced_versions` resolves it.
                "forced_versions": {},
                # {mac: {payload, verdict, detail, at}} — the last client-app
                # push each satellite did not take, kept until that satellite's
                # next committed push. Remembered here because the satellite
                # that failed may be the one that no longer answers.
                "satellite_app_failures": {}
            },
            # The `mac` section is the macOS/ROC link's tuning — roc-recv here and
            # the Mac's roc-vad sender, applied by Milo-Mac — not a MAC address. It used to be the one section with defaults that
            # `_validate_and_merge` emitted conditionally, which is why it was
            # absent from every settings.json until someone opened the Mac panel
            # — and why GET /bulk needed fallbacks at all.
            "mac": copy.deepcopy(DEFAULT_ROC_CONFIG),
            # The two remote controls, read by hardware/bt_remote.py and
            # hardware/ir_remote.py. Unlike hardware.json (the card and the
            # screen), these are user settings.
            "hardware": {
                "bt_remote": {
                    "enabled": False,
                    # Matched against the BlueZ device name; empty matches nothing.
                    "device_name_filter": "ANTICATER",
                    # Linux keycode (as a string) -> action, for the ANTICATER VK-01.
                    "key_map": {
                        "115": "volume_up",    # KEY_VOLUMEUP (rotation CW)
                        "114": "volume_down",  # KEY_VOLUMEDOWN (rotation CCW)
                        "113": "click",        # KEY_MUTE -> multi-click detection
                    },
                },
                "ir_remote": {
                    "enabled": False,
                    # The paired Apple Remote's address byte; None until paired.
                    "device_id": None,
                    "paired_at": None,
                },
            },
            "fan": {
                "enabled": True,
                "mode": "target",
                "manual_percent": 50,
                # The setpoint belongs to the controller that acts on it
                # (hardware/fan.py owns the thermal band); this is where the
                # *settings* layer says which of its values is the default.
                "target_temp_c": TARGET_TEMP_DEFAULT_C,
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
        """Pre-load settings.json, so the boot reads it before anyone asks.

        Never raises on its content: an unreadable file is set aside by the
        loader and the defaults are written in its place (``load_settings``).
        """
        await self.load_settings()

    async def load_settings(self) -> Dict[str, Any]:
        """Loads and validates settings with async lock.

        No file — a fresh install, or one just set aside as unreadable or as
        holding a value the validator cannot take — is the defaults, written
        at once. A card that cannot be read at all, or a file that cannot be
        renamed aside, is the defaults too, kept in memory only: nothing is
        written over a file that may still be fine.
        """
        try:
            async with self._file_lock:
                validated = await self._load_locked()

            if validated is None:
                self._cache = self._default_settings()
                self._read_failed = False
                await self.save_settings(self.defaults)
                return self._cache

            self._cache = validated
            self._read_failed = False
            return validated

        except Exception as e:
            self.logger.error(f"Error loading settings: {e}")
            self._cache = self._default_settings()
            self._read_failed = True
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
            self._read_failed = False
            self.logger.debug("Settings saved successfully")
            return True

        except Exception as e:
            self.logger.error(f"Error saving settings: {e}")
            return False

    @staticmethod
    def _validated_app_failures(stored: Any, default: Dict[str, Any]) -> Dict[str, Any]:
        """`updates.satellite_app_failures`, entry by entry: a malformed entry
        is dropped rather than repaired. `refused` is whether the satellite
        itself said no to that payload (an answered refusal, a rollback), which
        holds back the push its next admission would make."""
        if not isinstance(stored, dict):
            return dict(default)
        failure_verdicts = set(get_args(SatelliteUpdateVerdict)) - {"committed"}
        return {
            str(mac): {
                "payload": entry["payload"],
                "verdict": entry["verdict"],
                "detail": entry.get("detail") if isinstance(entry.get("detail"), str) else None,
                "at": int(entry["at"]),
                "refused": entry["refused"],
            }
            for mac, entry in stored.items()
            if isinstance(entry, dict)
            # None: the push failed before a payload was built (an unknown MAC).
            and (entry.get("payload") is None or isinstance(entry.get("payload"), str))
            and entry.get("verdict") in failure_verdicts
            and isinstance(entry.get("at"), (int, float))
            and not isinstance(entry.get("at"), bool)
            and isinstance(entry.get("refused"), bool)
        }

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
        validated['setup_awaiting_network'] = bool(
            settings.get('setup_awaiting_network', d['setup_awaiting_network'])
        )

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
        for step_key in ('step_interface_db', 'step_rotary_db', 'step_bt_remote_db', 'step_ir_remote_db'):
            vol[step_key] = max(1.0, min(6.0, float(vol_input.get(step_key, vol_d[step_key]))))
        validated['volume'] = vol

        # Screen - MODIFIED: Accept 0 for timeout_seconds (disabled)
        screen_input = settings.get('screen', {})
        screen_d = d['screen']
        timeout_seconds_raw = int(screen_input.get('timeout_seconds', screen_d['timeout_seconds']))
        # str() so a stored list or dict reads as an unknown theme rather than
        # raising on the frozenset lookup.
        theme_raw = str(screen_input.get('theme', screen_d['theme']))

        validated['screen'] = {
            # 0 = disabled, otherwise minimum 3 seconds
            'timeout_seconds': 0 if timeout_seconds_raw == 0 else max(3, min(9999, timeout_seconds_raw)),
            'brightness_on': max(1, min(10, int(screen_input.get('brightness_on', screen_d['brightness_on'])))),
            'auto_player_enabled': bool(screen_input.get('auto_player_enabled', screen_d['auto_player_enabled'])),
            'auto_player_delay_seconds': max(5, min(9999, int(screen_input.get('auto_player_delay_seconds', screen_d['auto_player_delay_seconds'])))),
            'ui_scale': max(0.5, min(2.0, float(screen_input.get('ui_scale', screen_d['ui_scale'])))),
            'color_filter_enabled': bool(screen_input.get('color_filter_enabled', screen_d['color_filter_enabled'])),
            'color_filter_warmth': max(0, min(100, int(screen_input.get('color_filter_warmth', screen_d['color_filter_warmth'])))),
            'edge_inset_mm': max(0.0, min(10.0, float(screen_input.get('edge_inset_mm', screen_d['edge_inset_mm'])))),
            'theme': theme_raw if theme_raw in ALLOWED_SCREEN_THEMES else screen_d['theme']
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

        # Volume is always placed: the list holds only its position, and
        # dock.volume decides on which screens it shows. Missing, it goes where
        # the default order puts it, just before Settings.
        if 'volume' not in filtered_apps:
            at = filtered_apps.index('settings') if 'settings' in filtered_apps else len(filtered_apps)
            filtered_apps.insert(at, 'volume')

        # No empty-list fallback: the rule above seeds `filtered_apps` with
        # spotify whenever it holds no audio source, so it cannot be empty here.
        volume_input = dock_input.get('volume', {})
        validated['dock'] = {
            'enabled_apps': filtered_apps,
            'volume': {
                surface: bool(volume_input.get(surface, default))
                for surface, default in d['dock']['volume'].items()
            }
        }

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
        packet_length = mac_input.get('packet_length_ms', mac_d['packet_length_ms'])
        # roc's RS8M block holds 255 packets in all, source and repair together.
        fec_source = max(1, min(254, int(mac_input.get('fec_block_source', mac_d['fec_block_source']))))
        validated['mac'] = {
            'target_latency_ms': max(20, min(500, int(mac_input.get('target_latency_ms', mac_d['target_latency_ms'])))),
            'latency_profile': profile if profile in ALLOWED_LATENCY_PROFILES else mac_d['latency_profile'],
            'frame_length_ms': frame_length if frame_length in ALLOWED_FRAME_LENGTHS else mac_d['frame_length_ms'],
            'packet_length_ms': packet_length if packet_length in ALLOWED_PACKET_LENGTHS else mac_d['packet_length_ms'],
            'fec_block_source': fec_source,
            'fec_block_repair': max(1, min(255 - fec_source, int(mac_input.get('fec_block_repair', mac_d['fec_block_repair'])))),
            'packet_interleaving': bool(mac_input.get('packet_interleaving', mac_d['packet_interleaving'])),
        }

        # Audio (auto-stop on pause, the resume window, the normalization)
        audio_input = settings.get('audio', {})
        normalization_input = audio_input.get('normalization', {})
        if not isinstance(normalization_input, dict):
            normalization_input = {}
        validated['audio'] = {
            'auto_stop_delay': self._seconds_or_off(audio_input, 'auto_stop_delay', d['audio'], upper=9999.0),
            # null is no limit: what was left is kept for good.
            'resume_window': self._seconds_or_off(
                audio_input, 'resume_window', d['audio'], upper=86400.0, unlimited=True,
            ),
            'normalization': {
                source: bool(normalization_input.get(source, default))
                for source, default in d['audio']['normalization'].items()
            },
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
            ))),
            'allow_app_volume': bool(spotify_input.get('allow_app_volume', d['spotify']['allow_app_volume']))
        }

        # WiFi regulatory domain
        wifi_input = settings.get('wifi', {})
        country_raw = str(wifi_input.get('country', d['wifi']['country']))
        country_valid = len(country_raw) == 2 and country_raw.isalpha() and country_raw.isupper()
        validated['wifi'] = {
            'country': country_raw if country_valid else d['wifi']['country']
        }

        # Journal retention: one of the three periods offered, else the
        # default, as a theme outside the three is. An int exactly: `15.0`
        # compares equal to 15 and is not a value the helper reads.
        system_input = settings.get('system', {})
        retention = system_input.get('log_retention_days', d['system']['log_retention_days'])
        validated['system'] = {
            'log_retention_days': (
                retention
                if type(retention) is int and retention in ALLOWED_LOG_RETENTION_DAYS
                else d['system']['log_retention_days']
            )
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
            } if isinstance(forced_input, dict) else dict(d['updates']['forced_versions']),
            'satellite_app_failures': self._validated_app_failures(
                settings.get('updates', {}).get(
                    'satellite_app_failures', d['updates']['satellite_app_failures']
                ),
                d['updates']['satellite_app_failures'],
            ),
        }

        # Multiroom (client_types for crossover) - Preserve multiroom section without strict validation
        multiroom_input = settings.get('multiroom', {})
        if multiroom_input:
            validated['multiroom'] = multiroom_input

        # The remote controls. A key_map that is not a non-empty map is
        # replaced whole: the controller adopts a device only if its keys meet
        # the map, so an empty one would strand the remote in silence.
        hardware_input = settings.get('hardware', {})
        bt_input = hardware_input.get('bt_remote', {})
        bt_d = d['hardware']['bt_remote']
        key_map = bt_input.get('key_map', bt_d['key_map'])
        ir_input = hardware_input.get('ir_remote', {})
        ir_d = d['hardware']['ir_remote']
        device_id = ir_input.get('device_id', ir_d['device_id'])
        paired_at = ir_input.get('paired_at', ir_d['paired_at'])
        validated['hardware'] = {
            'bt_remote': {
                'enabled': bool(bt_input.get('enabled', bt_d['enabled'])),
                'device_name_filter': str(bt_input.get('device_name_filter', bt_d['device_name_filter']))[:64],
                'key_map': (
                    {str(code): str(action) for code, action in key_map.items()}
                    if isinstance(key_map, dict) and key_map
                    else copy.deepcopy(bt_d['key_map'])
                ),
            },
            'ir_remote': {
                'enabled': bool(ir_input.get('enabled', ir_d['enabled'])),
                # Written into the kernel keymap by milo-apply-ir-keymap, which
                # refuses anything outside a byte: dropped, it reads as unpaired.
                'device_id': device_id if isinstance(device_id, int) and 0 <= device_id <= 0xFF else None,
                'paired_at': float(paired_at) if isinstance(paired_at, (int, float)) else None,
            },
        }

        # Fan control (optional — runtime PWM fan setpoint, see hardware/fan.py)
        fan_input = settings.get('fan', {})
        fan_d = d['fan']
        mode = fan_input.get('mode', fan_d['mode'])
        validated['fan'] = {
            'enabled': bool(fan_input.get('enabled', fan_d['enabled'])),
            'mode': mode if mode in VALID_MODES else fan_d['mode'],
            'manual_percent': max(0, min(100, int(fan_input.get('manual_percent', fan_d['manual_percent'])))),
            'target_temp_c': clamp_target_temp(fan_input.get('target_temp_c', fan_d['target_temp_c'])),
        }

        return validated

    def get_setting_sync(self, key_path: str) -> Any:
        """Get a setting by path (SYNCHRONOUS — for a reader that does not await).

        Prefer the async ``get_setting`` from runtime code. The legitimate
        callers are:

        * ``AudioRoutingService.regenerate_env_files`` — derives the three
          env-file artifacts from settings at boot (STEP 3b of
          ``initialize_services``, on the running loop, before any source
          unit starts) and during ``_detect_initial_state``.
        * ``AudioRoutingService.multiroom_enabled`` property — hot-path
          sync read used by the state machine when composing the audio
          state; a property cannot await.

        ``regenerate_env_files`` is ``async`` and reads it by choice, the
        one consolidation point of the sync read; the property is the one
        caller that could not do otherwise.

        Both read the cache. It reads the disk only when the cache is empty —
        the boot's first read, or a read that lands while a caller of
        ``invalidate_cache`` awaits its reload — and that read blocks the loop:
        every write leaves the validated dict it wrote in the cache, so none
        sends this method back to the file.

        Loaded data is run through ``_validate_and_merge`` before caching
        so missing keys (e.g. older installs without a ``routing`` block)
        resolve to validated defaults rather than ``None``.

        Reads exactly as ``load_settings`` does: this is the first reader of
        settings.json on every boot, so an unreadable file — or one holding a
        value the validator cannot take — is set aside here, before a shape
        nobody validated is derived into the three env files, which are then
        derived from the defaults.
        """
        if not self._cache:
            try:
                validated = self._load_sync()
                self._cache = validated if validated is not None else self._default_settings()
                self._read_failed = False
            except Exception as e:
                self.logger.warning(f"get_setting_sync fallback to defaults: {e}")
                self._cache = self._default_settings()
                self._read_failed = True

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

    @property
    def read_failed(self) -> bool:
        """Whether the settings served now are defaults held in memory because
        settings.json could not be read — an OSError on the card, or a file that
        could not be renamed aside — rather than what the file holds.

        A fresh install, or a file set aside, is not a failed read: the defaults
        are then what the file holds. `RecoveryAccessPointGuard` counts a failed
        read as set up, since the file it could not read may hold a unit that is.
        """
        return self._read_failed

    def invalidate_cache(self) -> None:
        """Invalidates cache to force a reload"""
        self._cache = None

    @staticmethod
    def _seconds_or_off(
        section: Dict[str, Any], key: str, defaults: Dict[str, Any], upper: float,
        unlimited: bool = False,
    ) -> Optional[float]:
        """A delay in seconds where 0 is the switch off: 0, or clamped to
        [1, upper]; anything that is not a number is the default. `unlimited`:
        a stored null is a choice (no limit), kept as null."""
        if unlimited and key in section and section[key] is None:
            return None
        try:
            raw = float(section.get(key, defaults[key]))
        except (TypeError, ValueError):
            raw = float(defaults[key])
        return 0.0 if raw == 0.0 else max(1.0, min(upper, raw))

    @staticmethod
    def _lookup(settings: Dict[str, Any], key_path: str) -> Any:
        """Value at a dotted ``key_path``, or ``_MISSING`` when it is absent."""
        value = settings
        for key in key_path.split('.'):
            if not isinstance(value, dict) or key not in value:
                return _MISSING
            value = value[key]
        return value

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
        """Sets a setting atomically; the cache holds what was written (async).

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
            success = await self._write_locked(settings, updates.keys())

        # A success leaves the cache `_write_locked` set: the validated dict it
        # just wrote, so no reader goes back to the disk for it.
        return success

    async def set_settings_strict(self, updates: Dict[str, Any]) -> None:
        """Atomic multi-key write; raise on disk-write failure or an undeclared key.

        Failure-loud variant of :meth:`set_settings`.
        """
        if not updates:
            return

        async with self._file_lock:
            settings = await self._read_locked()
            for key_path, value in updates.items():
                self._apply_key(settings, key_path, value)
            success = await self._write_locked(settings, updates.keys())

        if not success:
            raise SettingsWriteError(
                f"Failed to persist settings {sorted(updates)}"
            )

    async def _read_locked(self) -> Dict[str, Any]:
        """Read + validate settings. Caller must hold self._file_lock.

        No file — absent, or set aside as unreadable or as holding a value the
        validator cannot take — is the defaults, so a write recovers the file
        rather than fails, and lands the defaults plus what it writes. The
        set-aside is what makes that safe: the bytes the write replaces are
        kept beside it, never consumed — re-stamping a drifted file at the
        current version would be a migration in silence.
        """
        validated = await self._load_locked()
        return validated if validated is not None else self._default_settings()

    async def _load_locked(self) -> Optional[Dict[str, Any]]:
        """settings.json, validated; None when there is none to take in.
        Caller must hold self._file_lock.

        None is no file, or one set aside: by the loader (not JSON, another
        version), or here, for a value ``_validate_and_merge`` cannot take.
        """
        data = await load_versioned_json(Path(self.settings_file), self.SCHEMA_VERSION)
        if not data:
            return None
        try:
            return self._validate_and_merge(data)
        except _VALUE_IT_CANNOT_TAKE as e:
            await set_aside(Path(self.settings_file), f"a value it cannot take: {e!r}")
            return None

    def _load_sync(self) -> Optional[Dict[str, Any]]:
        """Blocking twin of ``_load_locked``, for ``get_setting_sync``."""
        data = load_versioned_json_sync(Path(self.settings_file), self.SCHEMA_VERSION)
        if not data:
            return None
        try:
            return self._validate_and_merge(data)
        except _VALUE_IT_CANNOT_TAKE as e:
            set_aside_sync(Path(self.settings_file), f"a value it cannot take: {e!r}")
            return None

    async def _write_locked(
        self, settings: Dict[str, Any], written_paths: Iterable[str] = ()
    ) -> bool:
        """Validate + atomically write settings. Caller must hold self._file_lock.

        A ``written_paths`` entry the validator drops (a key no section
        declares) refuses the whole write before anything reaches the disk:
        reporting it written would announce a value gone at the next read.
        """
        try:
            validated = self._validate_and_merge(settings)
            dropped = [
                path for path in written_paths
                if self._lookup(validated, path) is _MISSING
            ]
            if dropped:
                self.logger.error(
                    "Refusing settings write: undeclared key(s) %s dropped by the validator",
                    ", ".join(dropped),
                )
                return False
            await save_versioned_json(
                Path(self.settings_file), validated, self.SCHEMA_VERSION
            )
            self._cache = validated
            self._read_failed = False
            return True
        except Exception as e:
            self.logger.error(f"Error writing settings: {e}")
            return False
