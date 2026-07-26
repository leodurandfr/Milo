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


class MacRocConfig(BaseModel):
    target_latency_ms: int
    latency_profile: str
    frame_length_ms: int


class RadioSettingsConfig(BaseModel):
    shazam_enabled: bool


class QobuzSettingsConfig(BaseModel):
    allow_app_volume: bool


class BtRemoteConfig(BaseModel):
    """No `/bulk` key: the BT-remote panel reads its config from the WS event only."""
    enabled: bool
    device_name_filter: str
    key_map: Dict[str, str]
