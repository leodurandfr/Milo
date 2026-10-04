# backend/core/models/settings_config.py
"""One model per settings category — the shape both wire layers share.

A settings category has exactly one payload shape, and it travels over two
surfaces: the `config`/`limits` object of its `settings/<name>_changed` WS event
(`core/models/ws_events.py`) and the matching key of `GET /api/settings/bulk`
(`api/responses.py::BulkSettingsResponse`). Declaring it once here is what keeps
those two from drifting — the previous three parallel declarations (request,
response, event) meant adding a field took three edits and silently tolerated
two.

The *request* models stay in `api/models.py`: they carry the validators and
range constraints that guard the write path, which a response must not enforce
(a stored value outside the request's range should be reported, not 500).
"""
from typing import Dict, List

from pydantic import BaseModel

from backend.config.constants import SCREEN_THEMES


class VolumeLimitsConfig(BaseModel):
    min_db: float
    max_db: float


class VolumeStartupConfig(BaseModel):
    startup_volume_db: float
    restore_last_volume: bool


class VolumeStepsConfig(BaseModel):
    step_mobile_db: float


class RotaryStepsConfig(BaseModel):
    step_rotary_db: float


class BtRemoteStepsConfig(BaseModel):
    step_bt_remote_db: float


class IrRemoteStepsConfig(BaseModel):
    step_ir_remote_db: float


class DockAppsConfig(BaseModel):
    enabled_apps: List[str]


class AudioStopConfig(BaseModel):
    auto_stop_delay: float


class ScreenTimeoutConfig(BaseModel):
    screen_timeout_enabled: bool
    screen_timeout_seconds: int


class ScreenBrightnessConfig(BaseModel):
    brightness_on: int


class ScreenScreensaverConfig(BaseModel):
    screensaver_enabled: bool
    screensaver_delay_seconds: int


class ScreenUiScaleConfig(BaseModel):
    ui_scale: float


class ScreenColorFilterConfig(BaseModel):
    enabled: bool
    warmth: int


class ScreenThemeConfig(BaseModel):
    theme: SCREEN_THEMES


class MacRocConfig(BaseModel):
    """Both halves of the ROC link: roc-recv here (the first three), the Mac's
    roc-vad sender (the other four). Milo-Mac reads the sender half from
    `/bulk` and from `settings/mac_roc_changed`, and applies it."""
    target_latency_ms: int
    latency_profile: str
    frame_length_ms: int
    packet_length_ms: int
    fec_block_source: int
    fec_block_repair: int
    packet_interleaving: bool


class RadioSettingsConfig(BaseModel):
    shazam_enabled: bool


class QobuzSettingsConfig(BaseModel):
    allow_app_volume: bool


class SpotifySettingsConfig(BaseModel):
    """Crossfade between tracks, in ms (0 = disabled, gapless untouched), and
    whether the Spotify app's volume slider scales the samples.

    SpotifySource writes both into go-librespot's config.yml, which is read once
    at daemon start — hence the settings page's "restart to apply" button.
    """
    crossfade_duration: int
    allow_app_volume: bool


class MusicLibrarySettingsConfig(BaseModel):
    """True → one browsable tab per storage space; False → all of them merged."""
    separate_storages: bool


class BtRemoteConfig(BaseModel):
    """No `/bulk` key: the BT-remote panel reads its config from the WS event only."""
    enabled: bool
    device_name_filter: str
    key_map: Dict[str, str]
