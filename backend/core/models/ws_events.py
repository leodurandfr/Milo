# backend/core/models/ws_events.py
"""
Typed WebSocket event layer — one class per (category, type) pair.

`CATEGORY`/`TYPE` are class-level, never passed at call sites; the model's own
fields ARE the wire `data` payload. `AudioStateMachine.broadcast(event)`
serializes the model, injects `full_state` for source/system categories, and
wraps it in the `{category, type, origin, data, timestamp}` envelope.

The model is the payload documentation: each class docstring names its
consumers (frontend store/handler, Milo-Mac where applicable). Families not
yet listed here still go through the legacy
`broadcast_event(category, type, data)` until their Phase-5 migration.
"""
from typing import Any, ClassVar, Dict, List, Literal, Optional

from pydantic import BaseModel


class WsEvent(BaseModel):
    """Base WS event; subclasses set CATEGORY/TYPE and declare payload fields."""

    CATEGORY: ClassVar[str]
    TYPE: ClassVar[str]
    # source/system events carry an injected full_state (unifiedAudioStore);
    # lightweight events (position updates) opt out.
    INCLUDE_FULL_STATE: ClassVar[bool] = True
    # True on models whose None fields must be absent from the wire, not null.
    EXCLUDE_NONE: ClassVar[bool] = False

    @property
    def origin(self) -> str:
        """Envelope origin: the event's `source` field, falling back to CATEGORY."""
        return getattr(self, "source", None) or self.CATEGORY

    def wire_data(self) -> Dict[str, Any]:
        """The envelope `data` payload (before full_state injection)."""
        return self.model_dump(exclude_none=self.EXCLUDE_NONE)


# =============================================================================
# SYSTEM (core state machine)
# =============================================================================

class SystemTransitionStart(WsEvent):
    """App.vue → unifiedAudioStore.updateState — reads the injected full_state only."""
    CATEGORY = "system"
    TYPE = "transition_start"
    source: Literal["system"] = "system"


class SystemTransitionComplete(WsEvent):
    """App.vue → unifiedAudioStore.updateState — reads the injected full_state only."""
    CATEGORY = "system"
    TYPE = "transition_complete"
    source: Literal["system"] = "system"


class SystemErrorEvent(WsEvent):
    """Transition-failure banner: App.vue on('system','error') displays the message."""
    CATEGORY = "system"
    TYPE = "error"
    source: str  # audio source id the failed transition targeted
    error: str
    message: str


class SystemStateChanged(WsEvent):
    """Full-state carrier (App.vue → unifiedAudioStore); Milo-Mac keys on the
    multiroom_changed discriminator (absent everywhere but the routing emitter)."""
    CATEGORY = "system"
    TYPE = "state_changed"
    EXCLUDE_NONE = True
    source: str
    multiroom_changed: Optional[bool] = None


# =============================================================================
# SOURCE (all audio sources — never source-specific categories)
# =============================================================================

class SourceStateChanged(WsEvent):
    """App.vue → unifiedAudioStore + error banner (new_state == "error" reads
    metadata.error); podcastStore tracks episode state from it."""
    CATEGORY = "source"
    TYPE = "state_changed"
    source: str
    new_state: str
    metadata: Optional[Dict[str, Any]] = None


class SourceErrorCleared(WsEvent):
    """App.vue dismisses the error banner when data.source matches the displayed error."""
    CATEGORY = "source"
    TYPE = "error_cleared"
    source: str


class SourcePositionUpdate(WsEvent):
    """Zod source.position_update → unifiedAudioStore playback-position drift correction."""
    CATEGORY = "source"
    TYPE = "position_update"
    INCLUDE_FULL_STATE = False
    source: str
    position: int  # milliseconds
    duration: int  # milliseconds


# =============================================================================
# VOLUME
# =============================================================================

class VolumeChanged(WsEvent):
    """unifiedAudioStore (VolumeStateSchema); Milo-Mac reads state.global_volume_db,
    state.mode and multiroom_enabled (mirror of state.mode == "multiroom")."""
    CATEGORY = "volume"
    TYPE = "volume_changed"
    show_bar: bool
    step_mobile_db: float
    multiroom_enabled: bool
    state: Dict[str, Any]  # VolumeState.to_dict()


# =============================================================================
# SETTINGS (envelope: {source: "settings", config|limits|language: …})
# =============================================================================

class SettingsEvent(WsEvent):
    """Base for settings events; category and origin fixed to "settings"."""
    CATEGORY = "settings"
    source: Literal["settings"] = "settings"


class LanguageChanged(SettingsEvent):
    """App.vue switches the i18n locale."""
    TYPE = "language_changed"
    language: str


class VolumeLimitsConfig(BaseModel):
    min_db: float
    max_db: float


class VolumeLimitsChanged(SettingsEvent):
    """App.vue settings listener; Milo-Mac reads limits.min_db/max_db."""
    TYPE = "volume_limits_changed"
    limits: VolumeLimitsConfig


class VolumeStartupConfig(BaseModel):
    startup_volume_db: float
    restore_last_volume: bool


class VolumeStartupChanged(SettingsEvent):
    """App.vue settings listener; also emitted by VolumeService FR11 auto-tracking."""
    TYPE = "volume_startup_changed"
    config: VolumeStartupConfig


class VolumeStepsConfig(BaseModel):
    step_mobile_db: float


class VolumeStepsChanged(SettingsEvent):
    """App.vue settings listener."""
    TYPE = "volume_steps_changed"
    config: VolumeStepsConfig


class RotaryStepsConfig(BaseModel):
    step_rotary_db: float


class RotaryStepsChanged(SettingsEvent):
    """App.vue settings listener."""
    TYPE = "rotary_steps_changed"
    config: RotaryStepsConfig


class BtRemoteStepsConfig(BaseModel):
    step_bt_remote_db: float


class BtRemoteStepsChanged(SettingsEvent):
    """App.vue settings listener."""
    TYPE = "bt_remote_steps_changed"
    config: BtRemoteStepsConfig


class IrRemoteStepsConfig(BaseModel):
    step_ir_remote_db: float


class IrRemoteStepsChanged(SettingsEvent):
    """App.vue settings listener."""
    TYPE = "ir_remote_steps_changed"
    config: IrRemoteStepsConfig


class DockAppsConfig(BaseModel):
    enabled_apps: List[str]


class DockAppsChanged(SettingsEvent):
    """App.vue dock refresh; Milo-Mac reads config.enabled_apps."""
    TYPE = "dock_apps_changed"
    config: DockAppsConfig


class AudioStopConfig(BaseModel):
    auto_stop_delay: float


class AudioStopChanged(SettingsEvent):
    """App.vue settings listener."""
    TYPE = "audio_stop_changed"
    config: AudioStopConfig


class PodcastCredentialsConfig(BaseModel):
    taddy_user_id: str
    taddy_api_key: str


class PodcastCredentialsChanged(SettingsEvent):
    """App.vue settings listener (podcast credentials form sync)."""
    TYPE = "podcast_credentials_changed"
    config: PodcastCredentialsConfig


class ScreenTimeoutConfig(BaseModel):
    screen_timeout_enabled: bool
    screen_timeout_seconds: int


class ScreenTimeoutChanged(SettingsEvent):
    """App.vue settings listener."""
    TYPE = "screen_timeout_changed"
    config: ScreenTimeoutConfig


class ScreenBrightnessConfig(BaseModel):
    brightness_on: int


class ScreenBrightnessChanged(SettingsEvent):
    """App.vue settings listener."""
    TYPE = "screen_brightness_changed"
    config: ScreenBrightnessConfig


class ScreenScreensaverConfig(BaseModel):
    screensaver_enabled: bool
    screensaver_delay_seconds: int


class ScreenScreensaverChanged(SettingsEvent):
    """App.vue settings listener."""
    TYPE = "screen_screensaver_changed"
    config: ScreenScreensaverConfig


class ScreenUiScaleConfig(BaseModel):
    ui_scale: float


class ScreenUiScaleChanged(SettingsEvent):
    """App.vue settings listener."""
    TYPE = "screen_ui_scale_changed"
    config: ScreenUiScaleConfig


class ScreenColorFilterConfig(BaseModel):
    enabled: bool
    warmth: int


class ScreenColorFilterChanged(SettingsEvent):
    """App.vue settings listener."""
    TYPE = "screen_color_filter_changed"
    config: ScreenColorFilterConfig


class MacRocConfig(BaseModel):
    target_latency_ms: int
    latency_profile: str
    frame_length_ms: int


class MacRocChanged(SettingsEvent):
    """App.vue settings listener (Mac ROC latency form sync)."""
    TYPE = "mac_roc_changed"
    config: MacRocConfig


class RadioSettingsConfig(BaseModel):
    shazam_enabled: bool


class RadioSettingsChanged(SettingsEvent):
    """App.vue settings listener."""
    TYPE = "radio_settings_changed"
    config: RadioSettingsConfig


# =============================================================================
# PROGRAMS (update progress/complete; detail is reconstructed over REST)
# =============================================================================

class ProgramsProgressEvent(WsEvent):
    """Base: progress broadcasts carry status only (detail lives in GET /api/programs)."""
    CATEGORY = "programs"
    status: Literal["updating"] = "updating"


class ProgramsCompleteEvent(WsEvent):
    """Base: completion carries success only; the UI refetches versions over REST."""
    CATEGORY = "programs"
    success: bool


class ProgramUpdateProgress(ProgramsProgressEvent):
    """Zod programs.program_update_progress → App.vue update spinner."""
    TYPE = "program_update_progress"
    source: Literal["program_update"] = "program_update"
    program: str


class ProgramUpdateComplete(ProgramsCompleteEvent):
    """Zod programs.program_update_complete → App.vue (spinner off + REST refetch)."""
    TYPE = "program_update_complete"
    source: Literal["program_update"] = "program_update"
    program: str


class SatelliteUpdateProgress(ProgramsProgressEvent):
    """Zod programs.satellite_update_progress → App.vue satellite update spinner."""
    TYPE = "satellite_update_progress"
    source: Literal["satellite_update"] = "satellite_update"
    mac_id: str


class SatelliteUpdateComplete(ProgramsCompleteEvent):
    """Zod programs.satellite_update_complete → App.vue (spinner off + REST refetch)."""
    TYPE = "satellite_update_complete"
    source: Literal["satellite_update"] = "satellite_update"
    mac_id: str


class SatelliteAppUpdateProgress(ProgramsProgressEvent):
    """Zod programs.satellite_app_update_progress → App.vue satellite update spinner."""
    TYPE = "satellite_app_update_progress"
    source: Literal["satellite_update"] = "satellite_update"
    mac_id: str


class SatelliteAppUpdateComplete(ProgramsCompleteEvent):
    """Zod programs.satellite_app_update_complete → App.vue (spinner off + REST refetch)."""
    TYPE = "satellite_app_update_complete"
    source: Literal["satellite_update"] = "satellite_update"
    mac_id: str


class SatelliteCamillaDspUpdateProgress(ProgramsProgressEvent):
    """Zod programs.satellite_camilladsp_update_progress → App.vue satellite update spinner."""
    TYPE = "satellite_camilladsp_update_progress"
    source: Literal["satellite_update"] = "satellite_update"
    mac_id: str


class SatelliteCamillaDspUpdateComplete(ProgramsCompleteEvent):
    """Zod programs.satellite_camilladsp_update_complete → App.vue (spinner off + REST refetch)."""
    TYPE = "satellite_camilladsp_update_complete"
    source: Literal["satellite_update"] = "satellite_update"
    mac_id: str
