# backend/core/models/ws_events.py
"""
Typed WebSocket event layer — one class per (category, type) pair.

`CATEGORY`/`TYPE` are class-level, never passed at call sites; the model's own
fields ARE the wire `data` payload. `AudioStateMachine.broadcast(event)`
serializes the model, injects `full_state` for source/system categories, and
wraps it in the `{category, type, origin, data, timestamp}` envelope.

The model is the payload documentation: each class docstring names its
consumers (frontend store/handler, Milo-Mac where applicable).
"""
import time
from typing import Any, ClassVar, Dict, List, Literal, Optional

from pydantic import BaseModel

from backend.core.network.models import NetworkStatus


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

    def to_envelope(self, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """The full wire envelope. `data` lets the state machine pass the
        payload it already enriched with full_state; per-client senders
        (ws/manager handshake) call it bare."""
        return {
            "category": self.CATEGORY,
            "type": self.TYPE,
            "origin": self.origin,
            "data": data if data is not None else self.wire_data(),
            "timestamp": time.time(),
        }


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


class SystemConnectivityChanged(WsEvent):
    """App.vue offline banner — NetworkManager connectivity flips."""
    CATEGORY = "system"
    TYPE = "connectivity_changed"
    INCLUDE_FULL_STATE = False
    source: Literal["system"] = "system"
    online: bool


class SystemHostnameConflictChanged(WsEvent):
    """App.vue hostname-conflict banner (milo.local advertised under another name)."""
    CATEGORY = "system"
    TYPE = "hostname_conflict_changed"
    INCLUDE_FULL_STATE = False
    source: Literal["system"] = "system"
    hostname_conflict: bool
    advertised_name: Optional[str]
    local_ip: Optional[str]
    expected_name: str


class SystemBackendError(WsEvent):
    """App.vue error toast — forwarded backend ERROR log records."""
    CATEGORY = "system"
    TYPE = "backend_error"
    message: str


class SystemCdDriveStatus(WsEvent):
    """full_state carrier — drive/disc state travels in the injected
    full_state metadata, not in data (App.vue → unifiedAudioStore)."""
    CATEGORY = "system"
    TYPE = "cd_drive_status"
    source: Literal["cd"] = "cd"


class SystemInitialState(WsEvent):
    """Handshake reply (ws/manager) — sent to the single ready client, never
    broadcast; carries its own full_state as an explicit field."""
    CATEGORY = "system"
    TYPE = "initial_state"
    INCLUDE_FULL_STATE = False
    full_state: Dict[str, Any]
    setup_completed: bool
    hotspot_active: bool


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


# source/favorite_* is a union discriminated by data.source (radio | podcast).

class RadioFavoriteAdded(WsEvent):
    """radioStore favorites sync."""
    CATEGORY = "source"
    TYPE = "favorite_added"
    source: Literal["radio"] = "radio"
    station_id: str


class RadioFavoriteRemoved(WsEvent):
    """radioStore favorites sync."""
    CATEGORY = "source"
    TYPE = "favorite_removed"
    source: Literal["radio"] = "radio"
    station_id: str


class RadioFavoriteModified(WsEvent):
    """radioStore metadata edit sync; station dict carries id + is_favorite."""
    CATEGORY = "source"
    TYPE = "favorite_modified"
    source: Literal["radio"] = "radio"
    station: Dict[str, Any]


class PodcastFavoriteAdded(WsEvent):
    """podcastStore subscriptions sync; podcast = the subscription dict."""
    CATEGORY = "source"
    TYPE = "favorite_added"
    source: Literal["podcast"] = "podcast"
    podcast: Dict[str, Any]


class PodcastFavoriteRemoved(WsEvent):
    """podcastStore subscriptions sync."""
    CATEGORY = "source"
    TYPE = "favorite_removed"
    source: Literal["podcast"] = "podcast"
    uuid: str


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


class BtRemoteConfig(BaseModel):
    enabled: bool
    device_name_filter: str
    key_map: Dict[str, str]


class BtRemoteConfigChanged(SettingsEvent):
    """BT-remote settings form sync."""
    TYPE = "bt_remote_config_changed"
    config: BtRemoteConfig


class BtRemoteStatusChanged(SettingsEvent):
    """BT-remote settings panel. `paired` is the durable BlueZ-bond signal
    (true even while the remote sleeps/disconnects); the UI uses it to offer
    the "unpair" action."""
    TYPE = "bt_remote_status_changed"
    connected_devices: List[Dict[str, Any]]
    discovering: bool
    paired: bool


class IrRemoteStatusChanged(SettingsEvent):
    """IR-remote settings panel (pairing flow + listener state)."""
    TYPE = "ir_remote_status_changed"
    available: bool
    enabled: bool
    paired: bool
    device_id: Optional[int]
    paired_at: Optional[float]
    listening: bool
    pairing_in_progress: bool


class FanStatusEvent(SettingsEvent):
    """Base for fan events; payload = FanController.get_status() (config +
    live telemetry)."""
    available: bool
    enabled: bool
    mode: str  # auto | manual | target
    manual_percent: int
    target_temp_c: int
    curve: List[Dict[str, Any]]
    temp_c: float
    rpm: int
    pwm_percent: int


class FanConfigChanged(FanStatusEvent):
    """Fan settings form sync (config write)."""
    TYPE = "fan_config_changed"


class FanStatusChanged(FanStatusEvent):
    """Fan settings panel telemetry refresh."""
    TYPE = "fan_status_changed"


class ScreenSleepChanged(WsEvent):
    """Kiosk screen wake/sleep indicator. No `source` field (origin falls back
    to the category, matching the historical wire shape)."""
    CATEGORY = "settings"
    TYPE = "screen_sleep_changed"
    sleeping: bool


# =============================================================================
# EQUALIZER (local CamillaDSP + zone toggles)
# =============================================================================

class EqualizerStateChanged(WsEvent):
    """equalizerStore — CamillaDSP daemon connection state."""
    CATEGORY = "equalizer"
    TYPE = "state_changed"
    state: str


class EqualizerFilterChanged(WsEvent):
    """equalizerStore/ParametricEQ — canonical EQ-filter wire shape
    (EqFilter.to_wire_dict: freq/type, not the frequency/filter_type
    persistence shape)."""
    CATEGORY = "equalizer"
    TYPE = "filter_changed"
    id: str
    freq: float
    gain: float
    q: float
    type: str
    enabled: bool


class EqualizerCompressorChanged(WsEvent):
    """equalizerStore compressor panel sync."""
    CATEGORY = "equalizer"
    TYPE = "compressor_changed"
    enabled: bool
    threshold: float
    ratio: float
    attack: float
    release: float
    makeup_gain: float


class EqualizerLoudnessChanged(WsEvent):
    """equalizerStore loudness panel sync."""
    CATEGORY = "equalizer"
    TYPE = "loudness_changed"
    enabled: bool
    high_boost: float
    low_boost: float


class EqualizerMonoChanged(WsEvent):
    """equalizerStore mono toggle sync."""
    CATEGORY = "equalizer"
    TYPE = "mono_changed"
    enabled: bool


class EqualizerLevels(WsEvent):
    """Zod equalizer.levels → VU meter; output_peak = [left_db, right_db]."""
    CATEGORY = "equalizer"
    TYPE = "levels"
    available: bool
    output_peak: List[float]


class EqualizerEnabledChanged(WsEvent):
    """equalizerStore global effects toggle (bypass/restore)."""
    CATEGORY = "equalizer"
    TYPE = "enabled_changed"
    enabled: bool


class EqualizerZoneEnabledChanged(WsEvent):
    """equalizerStore per-zone effects toggle."""
    CATEGORY = "equalizer"
    TYPE = "zone_enabled_changed"
    zone_id: str
    enabled: bool


# =============================================================================
# MULTIROOM (client/zone registry + crossover + pending clients)
# =============================================================================

class MultiroomClientStateChanged(WsEvent):
    """multiroomStore client list sync; `client` is absent (not null) on
    unregister, where only the mac_id remains meaningful."""
    CATEGORY = "multiroom"
    TYPE = "client_state_changed"
    EXCLUDE_NONE = True
    mac_id: str
    client: Optional[Dict[str, Any]] = None


class MultiroomZoneChanged(WsEvent):
    """multiroomStore zone sync — union: {zone_id, zone} on create/update/
    delete, {zone_id, mac_id} on zone_client_removed (zone omitted)."""
    CATEGORY = "multiroom"
    TYPE = "zone_changed"
    EXCLUDE_NONE = True
    zone_id: str
    zone: Optional[Dict[str, Any]] = None
    mac_id: Optional[str] = None


class MultiroomEqualizerChanged(WsEvent):
    """equalizerStore targeted EQ sync; equalizer_settings is a PARTIAL wire
    dict (only the changed keys: filters/compressor/loudness/mono/...)."""
    CATEGORY = "multiroom"
    TYPE = "equalizer_changed"
    target_type: str  # "client" | "zone"
    target_id: str
    equalizer_settings: Dict[str, Any]


class MultiroomCrossoverChanged(WsEvent):
    """equalizerStore.handleZoneCrossoverChanged — single canonical zone shape."""
    CATEGORY = "multiroom"
    TYPE = "crossover_changed"
    zone_id: str
    crossover_enabled: bool
    crossover_frequency: int


class MultiroomPendingClientChanged(WsEvent):
    """multiroomStore pending-client list — union discriminated by `action`:
    {action: registered|updated, client} | {action: removed, mac_id}."""
    CATEGORY = "multiroom"
    TYPE = "pending_client_changed"
    EXCLUDE_NONE = True
    action: str
    client: Optional[Dict[str, Any]] = None
    mac_id: Optional[str] = None


# =============================================================================
# ROUTING (multiroom transitions)
# =============================================================================

class RoutingMultiroomEnabling(WsEvent):
    """multiroomStore.handleRoutingEvent — transition spinner on (empty payload)."""
    CATEGORY = "routing"
    TYPE = "multiroom_enabling"


class RoutingMultiroomDisabling(WsEvent):
    """multiroomStore.handleRoutingEvent — transition spinner on (empty payload)."""
    CATEGORY = "routing"
    TYPE = "multiroom_disabling"


class RoutingMultiroomReady(WsEvent):
    """multiroomStore.handleRoutingEvent — clears the transition spinner (empty payload)."""
    CATEGORY = "routing"
    TYPE = "multiroom_ready"


class RoutingMultiroomError(WsEvent):
    """multiroomStore maps `reason` via MULTIROOM_ERROR_KEYS to a localized
    transitionError; also in the Milo-Mac manifest."""
    CATEGORY = "routing"
    TYPE = "multiroom_error"
    reason: str  # "enable_failed" | "disable_failed"


# =============================================================================
# NETWORK
# =============================================================================

class NetworkStatusChanged(NetworkStatus, WsEvent):
    """networkStore — flat NetworkStatus payload (wifi_enabled, ethernet, wifi)."""
    CATEGORY = "network"
    TYPE = "status_changed"


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
