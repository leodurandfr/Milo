# backend/api/responses.py
"""Typed response models for the API's outward wire contract.

Phase 1 covers the routes pinned in the Milo-Mac wire contract
(backend/tests/contracts/milo_mac_contract.json). Each model is a faithful
whitelist of the keys a route already emits: `response_model` documents the
shape in OpenAPI and fails loud (500) if a handler ever drifts from it.

`response_model_exclude_none` is set on a route ONLY where the current ad-hoc
dict already omits a key conditionally, so the wire is reproduced exactly:
  - volume/state: success carries `data` (no `message`), error carries
    `message` (no `data`).
  - radio/stations: `network_error` appears only on a degraded search.
No contract field carries a meaningful null (verified against both the Swift
consumer and the frontend Zod schemas), so dropping nulls is lossless.

Opaque payloads (source `metadata`, enriched station dicts) are typed
`Dict[str, Any]` / `List[Dict[str, Any]]` so `response_model` never strips or
re-shapes their sub-keys — Milo-Mac treats `metadata` as opaque by contract.
"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from backend.core.network.models import NetworkStatus, SavedNetwork, WifiNetwork


class StatusResponse(BaseModel):
    """Bare success/error envelope: POST /api/audio/source/{source}."""
    status: str


class AudioStateResponse(BaseModel):
    """GET /api/audio/state — AudioStateMachine.get_current_state()."""
    active_source: str
    source_state: str
    transitioning: bool
    metadata: Dict[str, Any]
    error: Optional[str] = None
    multiroom_enabled: bool
    equalizer_effects_enabled: bool


class MultiroomSetResponse(BaseModel):
    """PUT /api/routing/multiroom."""
    status: str
    multiroom_enabled: bool
    active_source: str


# --- volume/state (GET /api/volume/state) ----------------------------------
class VolumeClientModel(BaseModel):
    """ClientVolume.to_dict()."""
    volume_db: float
    offset_db: float
    mute: bool
    available: bool


class VolumeZoneModel(BaseModel):
    """ZoneVolume.to_dict()."""
    id: str
    name: str
    client_ids: List[str]
    average_volume_db: float
    all_muted: bool


class VolumeStateModel(BaseModel):
    """VolumeState.to_dict() — the inner `data` object."""
    mode: str
    global_volume_db: float
    global_mute: bool
    volume_control: bool
    any_volume_control: bool
    clients: Dict[str, VolumeClientModel]
    zones: Dict[str, VolumeZoneModel]


class VolumeStateEnvelope(BaseModel):
    """GET /api/volume/state — resilience envelope (HTTP 200 + status).

    Served with response_model_exclude_none: the success branch omits
    `message`, the error branch omits `data`.
    """
    status: str
    data: Optional[VolumeStateModel] = None
    message: Optional[str] = None


class VolumeAdjustResponse(BaseModel):
    """POST /api/volume/adjust."""
    status: str
    volume_db: float
    delta_db: float


class EqualizerEnabledResponse(BaseModel):
    """PUT /api/equalizer/target/{target}/enabled."""
    status: str
    target: str
    enabled: bool


class RadioStationsResponse(BaseModel):
    """GET /api/radio/stations.

    Served with response_model_exclude_none so `network_error` (emitted only
    on a degraded RadioBrowser search) is absent on success. Station dicts are
    opaque (enriched with favorite status) — typed Dict to preserve sub-keys.
    """
    stations: List[Dict[str, Any]]
    total: int
    network_error: Optional[bool] = None


# --- settings/bulk (GET /api/settings/bulk) --------------------------------
class VolumeLimitsModel(BaseModel):
    min_db: float
    max_db: float


class VolumeStartupModel(BaseModel):
    startup_volume_db: float
    restore_last_volume: bool


class RotaryStepsModel(BaseModel):
    step_rotary_db: float


class BtRemoteStepsModel(BaseModel):
    step_bt_remote_db: float


class IrRemoteStepsModel(BaseModel):
    step_ir_remote_db: float


class DockAppsModel(BaseModel):
    enabled_apps: List[str]


class AudioStopModel(BaseModel):
    auto_stop_delay: float


class PodcastCredentialsModel(BaseModel):
    taddy_user_id: str
    taddy_api_key: str


class ScreenTimeoutModel(BaseModel):
    screen_timeout_enabled: bool
    screen_timeout_seconds: int


class ScreenBrightnessModel(BaseModel):
    brightness_on: int


class ScreenUiScaleModel(BaseModel):
    ui_scale: float


class ScreenScreensaverModel(BaseModel):
    screensaver_enabled: bool
    screensaver_delay_seconds: int


class ScreenColorFilterModel(BaseModel):
    enabled: bool
    warmth: int


class RadioSettingsModel(BaseModel):
    shazam_enabled: bool


class MacRocModel(BaseModel):
    target_latency_ms: int
    latency_profile: str
    frame_length_ms: int


class BulkSettingsResponse(BaseModel):
    """GET /api/settings/bulk — every settings category in one payload."""
    status: str
    language: str
    volume_limits: VolumeLimitsModel
    volume_startup: VolumeStartupModel
    rotary_steps: RotaryStepsModel
    bt_remote_steps: BtRemoteStepsModel
    ir_remote_steps: IrRemoteStepsModel
    dock_apps: DockAppsModel
    audio_stop: AudioStopModel
    podcast_credentials: PodcastCredentialsModel
    screen_timeout: ScreenTimeoutModel
    screen_brightness: ScreenBrightnessModel
    screen_ui_scale: ScreenUiScaleModel
    screen_screensaver: ScreenScreensaverModel
    screen_color_filter: ScreenColorFilterModel
    radio_settings: RadioSettingsModel
    mac_roc: MacRocModel


# --- Phase 2: network + remaining volume routes ----------------------------
# These raise HTTPException on error (no error-in-body) → always the success
# envelope, no exclude_none. Network `data` reuses the domain models directly
# (one source of truth); their nulls when disconnected are preserved.

# --- network (GET/POST/PUT /api/network/*) ---------------------------------
class NetworkStatusEnvelope(BaseModel):
    """GET /status, POST /wifi/connect, PUT /wifi/radio."""
    status: str
    data: NetworkStatus


class WifiNetworksEnvelope(BaseModel):
    """GET /wifi/networks — scan results."""
    status: str
    data: List[WifiNetwork]


class WifiSavedEnvelope(BaseModel):
    """GET /wifi/saved."""
    status: str
    data: List[SavedNetwork]


class WifiSsidData(BaseModel):
    ssid: str


class WifiSaveEnvelope(BaseModel):
    """POST /wifi/save."""
    status: str
    data: WifiSsidData


class WifiCountryData(BaseModel):
    country_code: str


class WifiCountryEnvelope(BaseModel):
    """GET/PUT /wifi/country."""
    status: str
    data: WifiCountryData


# --- volume, remaining routes (PATCH /api/volume/*) ------------------------
class ZoneVolumeDeltaResponse(BaseModel):
    """PATCH /api/volume/zone/{zone_id}."""
    status: str
    zone_id: str
    new_average_db: float
    delta_db: float
    applied_to: List[str]
    offline_clients: List[str]


class ClientVolumeSetResponse(BaseModel):
    """PATCH /api/volume/client/mac/{mac_url}."""
    status: str
    mac_id: str
    volume_db: float


class ClientMuteSetResponse(BaseModel):
    """PATCH /api/volume/client/mac/{mac_url}/mute."""
    status: str
    mac_id: str
    mute: bool


class VolumeControlResponse(BaseModel):
    """PATCH /api/volume/volume-control."""
    status: str
    volume_control: bool


# --- Phase 3: multiroom client/zone CRUD -----------------------------------
# Client/Zone dataclasses carry computed/conditional keys, so their payloads
# stay opaque Dict[str, Any] (metadata/stations precedent). The two either/or
# routes use exclude_none to reproduce their single-key branches.
# (GET /clients/{mac_id}/hardware stays untyped — it proxies satellite JSON.)
class MultiroomStateResponse(BaseModel):
    """GET /api/multiroom/state — the full registry sync."""
    clients: Dict[str, Any]
    zones: Dict[str, Any]


class MultiroomPendingClientsResponse(BaseModel):
    """GET /api/multiroom/pending-clients."""
    clients: Dict[str, Any]


class ClientMutationResponse(BaseModel):
    """PATCH /api/multiroom/clients/{mac_id} — updated client (with `online`)."""
    status: str
    client: Dict[str, Any]


class ZoneMutationResponse(BaseModel):
    """POST/PATCH zone routes returning the enriched zone."""
    status: str
    zone: Dict[str, Any]


class MultiroomMessageResponse(BaseModel):
    """Multiroom routes returning a bare human-readable message."""
    status: str
    message: str


class ZoneOrMessageResponse(BaseModel):
    """DELETE /api/multiroom/zones/{zone_id}/clients/{mac_id}.

    Returns the enriched zone if it survives, else a deletion message.
    Served with exclude_none so each branch emits only its own key.
    """
    status: str
    zone: Optional[Dict[str, Any]] = None
    message: Optional[str] = None


class RegisterClientResponse(BaseModel):
    """POST /api/multiroom/register-client (called by milo-client satellites).

    Returns `message` (hardware-configured / reconnect path) or `client` (the
    staged pending record). Served with exclude_none per single-key branch.
    """
    status: str
    message: Optional[str] = None
    client: Optional[Dict[str, Any]] = None


# --- Phase 4: equalizer read + per-target mutations ------------------------
# The GET record's structured sub-objects (compressor/loudness/filters) stay
# opaque Dict/List so response_model preserves their sub-keys. The 2 crossover
# routes are intentionally left untyped: they mix int/float/None frequencies
# that response_model would coerce (80 -> 80.0), a subtle wire change.
class EqualizerRecordResponse(BaseModel):
    """GET /api/equalizer/target/{target} — the full per-target EQ record."""
    enabled: bool
    active_preset: Optional[str] = None
    mono: bool
    compressor: Dict[str, Any]
    loudness: Dict[str, Any]
    custom_gains: List[float]
    filters: List[Dict[str, Any]]
    state: str
    sample_rate: Optional[int] = None
    available: bool


class EqualizerPresetsResponse(BaseModel):
    """GET /api/equalizer/presets. `error` appears only on the degraded branch;
    served with exclude_none so the success path omits it."""
    presets: List[Dict[str, Any]]
    custom_gains: List[float]
    active_preset: Optional[str] = None
    error: Optional[str] = None


class TargetStatusResponse(BaseModel):
    """PUT /target/{target}/compressor and /loudness."""
    status: str
    target: str


class TargetFilterResponse(BaseModel):
    """PUT /target/{target}/filter/{filter_id}."""
    status: str
    target: str
    filter_id: str


class TargetMonoResponse(BaseModel):
    """PUT /target/{target}/mono."""
    status: str
    target: str
    mono: bool


class TargetPresetResponse(BaseModel):
    """POST /target/{target}/preset — resolved gains for immediate UI apply."""
    status: str
    target: str
    preset_id: str
    gains: List[float]


class TargetSaveCustomResponse(BaseModel):
    """POST /target/{target}/save-custom."""
    status: str
    target: str
    preset_id: str
