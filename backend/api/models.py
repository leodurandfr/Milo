# backend/api/models.py
"""
Pydantic models for API request validation
"""
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, Dict, Any, List, Literal

from backend.config.constants import (
    DEFAULT_ROC_CONFIG,
    GPIO_MAX_PIN,
    GPIO_MIN_PIN,
    ROC_FRAME_LENGTHS,
    ROC_LATENCY_PROFILES,
)


# =============================================================================
# AUDIO CONTROL
# =============================================================================

class AudioControlRequest(BaseModel):
    """Audio control request"""
    command: str = Field(..., min_length=1, max_length=50)
    data: Optional[Dict[str, Any]] = Field(default_factory=dict)

    @field_validator('command')
    @classmethod
    def validate_command(cls, v: str) -> str:
        """Validates that command contains only allowed characters"""
        if not v.replace('_', '').replace('-', '').isalnum():
            raise ValueError('Command must contain only alphanumeric characters, hyphens, and underscores')
        return v


# =============================================================================
# VOLUME
# =============================================================================

class VolumeAdjustRequest(BaseModel):
    """Volume adjustment request (in dB)"""
    delta_db: float = Field(..., ge=-60, le=60, description="Volume delta in dB")
    show_bar: bool = Field(default=True)


class ClientVolumeRequest(BaseModel):
    """Client volume request (in dB)"""
    volume_db: float = Field(..., ge=-80, le=0, description="Client volume in dB")


class ClientMuteRequest(BaseModel):
    """Client mute request"""
    mute: bool = Field(..., description="Mute state")


# =============================================================================
# SNAPCAST
# =============================================================================

class SnapcastServerConfigRequest(BaseModel):
    """Snapcast server configuration request"""
    config: Dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# SETTINGS - LANGUAGE
# =============================================================================

SUPPORTED_LANGUAGES = Literal['french', 'english', 'spanish', 'hindi', 'chinese', 'portuguese', 'italian', 'german']

class LanguageRequest(BaseModel):
    """Language setting request"""
    language: SUPPORTED_LANGUAGES


# =============================================================================
# SETTINGS - VOLUME (all values in dB)
# =============================================================================

class VolumeLimitsRequest(BaseModel):
    """Volume limits request (in dB)"""
    min_db: float = Field(..., ge=-80, le=0, description="Minimum volume in dB")
    max_db: float = Field(..., ge=-80, le=0, description="Maximum volume in dB")

    @model_validator(mode='after')
    def validate_range(self):
        if self.max_db - self.min_db < 6:
            raise ValueError('Range between min_db and max_db must be at least 6 dB')
        if self.max_db <= self.min_db:
            raise ValueError('max_db must be greater than min_db')
        return self


class VolumeStartupRequest(BaseModel):
    """Volume startup configuration request (in dB)"""
    startup_volume_db: float = Field(..., ge=-80, le=0, description="Startup volume in dB")
    restore_last_volume: bool


class VolumeControlRequest(BaseModel):
    """Toggle local device volume control (DAC mode)."""
    volume_control: bool


class VolumeStepsRequest(BaseModel):
    """Mobile volume steps request (in dB)"""
    step_mobile_db: float = Field(..., ge=1, le=6, description="Mobile volume step in dB")


class RotaryStepsRequest(BaseModel):
    """Rotary encoder volume steps request (in dB)"""
    step_rotary_db: float = Field(..., ge=1, le=6, description="Rotary volume step in dB")


class BtRemoteStepsRequest(BaseModel):
    """BT remote volume steps request (in dB)"""
    step_bt_remote_db: float = Field(..., ge=1, le=6, description="BT remote volume step in dB")


class IrRemoteStepsRequest(BaseModel):
    """IR remote volume steps request (in dB)"""
    step_ir_remote_db: float = Field(..., ge=1, le=6, description="IR remote volume step in dB")


class BtRemoteConfigRequest(BaseModel):
    """BT remote partial config update request."""
    enabled: Optional[bool] = None
    device_name_filter: Optional[str] = Field(None, max_length=64)
    key_map: Optional[dict] = None


class IrRemoteConfigRequest(BaseModel):
    """IR remote partial config update request."""
    enabled: Optional[bool] = None


class FanCurvePoint(BaseModel):
    """One point of the fan temperature→speed curve."""
    temp_c: int = Field(..., ge=20, le=110, description="Temperature in °C")
    percent: int = Field(..., ge=0, le=100, description="Fan speed in %")


class FanConfigRequest(BaseModel):
    """Fan control configuration (idempotent full update)."""
    enabled: bool = Field(..., description="Fan on; off = fan stopped (0%)")
    mode: Literal['auto', 'manual', 'target'] = Field(default='auto')
    manual_percent: int = Field(..., ge=0, le=100, description="Speed for manual mode")
    target_temp_c: int = Field(..., ge=55, le=76, description="Setpoint for target mode in °C")
    curve: List[FanCurvePoint] = Field(..., min_length=2, description="Auto-mode curve")

    @model_validator(mode='after')
    def validate_curve_increasing(self):
        temps = [p.temp_c for p in self.curve]
        if temps != sorted(temps) or len(set(temps)) != len(temps):
            raise ValueError('curve temperatures must be strictly increasing')
        return self


class FanTestRequest(BaseModel):
    """Momentary fan speed test/preview request."""
    percent: int = Field(..., ge=0, le=100)


# =============================================================================
# SETTINGS - DOCK APPS
# =============================================================================

from backend.config.constants import VALID_DOCK_APPS, AUDIO_SOURCE_APPS

class DockAppsRequest(BaseModel):
    """Dock apps configuration request"""
    enabled_apps: List[str]

    @field_validator('enabled_apps')
    @classmethod
    def validate_apps(cls, v: List[str]) -> List[str]:
        invalid_apps = set(v) - VALID_DOCK_APPS
        if invalid_apps:
            raise ValueError(f'Invalid apps: {invalid_apps}. Valid apps: {VALID_DOCK_APPS}')

        # At least one audio source must be enabled
        enabled_audio_sources = set(v) & AUDIO_SOURCE_APPS
        if not enabled_audio_sources:
            raise ValueError('At least one audio source must be enabled')

        return v


# =============================================================================
# SETTINGS - AUDIO AUTO-STOP
# =============================================================================

class AudioStopRequest(BaseModel):
    """Global audio auto-stop delay request (applies to all sources)"""
    auto_stop_delay: float = Field(..., ge=0, le=9999)

    @field_validator('auto_stop_delay')
    @classmethod
    def validate_delay(cls, v: float) -> float:
        # 0 means disabled, otherwise must be >= 1.0
        if v != 0 and v < 1.0:
            raise ValueError('Delay must be 0 (disabled) or >= 1.0 seconds')
        return v


# =============================================================================
# SETTINGS - SCREEN
# =============================================================================

class ScreenTimeoutRequest(BaseModel):
    """Screen timeout configuration request"""
    screen_timeout_enabled: bool
    screen_timeout_seconds: int = Field(..., ge=0, le=3600)

    @field_validator('screen_timeout_seconds')
    @classmethod
    def validate_timeout(cls, v: int) -> int:
        # 0 means disabled, otherwise must be >= 3
        if v != 0 and v < 3:
            raise ValueError('Timeout must be 0 (disabled) or >= 3 seconds')
        return v


class ScreenBrightnessRequest(BaseModel):
    """Screen brightness request"""
    brightness_on: int = Field(..., ge=1, le=10)


class ScreenScreensaverRequest(BaseModel):
    """Screen screensaver configuration request"""
    screensaver_enabled: Optional[bool] = None
    screensaver_delay_seconds: Optional[int] = Field(None, ge=5, le=1800)


class ScreenUiScaleRequest(BaseModel):
    """Screen UI scale request"""
    ui_scale: float = Field(..., ge=0.9, le=1.15)


class ScreenColorFilterRequest(BaseModel):
    """Screen warm color filter request"""
    enabled: Optional[bool] = None
    warmth: Optional[int] = Field(None, ge=0, le=100)


# =============================================================================
# DSP (CamillaDSP)
# =============================================================================

EQUALIZER_FILTER_TYPES = Literal['Peaking', 'Lowshelf', 'Highshelf', 'Lowpass', 'Highpass', 'Notch', 'Allpass']


class EqualizerFilterUpdateRequest(BaseModel):
    """Equalizer filter update request (partial update allowed)"""
    freq: Optional[float] = Field(None, ge=20, le=20000)
    gain: Optional[float] = Field(None, ge=-15, le=15)
    q: Optional[float] = Field(None, ge=0.1, le=10.0)
    filter_type: Optional[EQUALIZER_FILTER_TYPES] = None
    enabled: Optional[bool] = None


class EqualizerCompressorRequest(BaseModel):
    """Equalizer compressor settings request"""
    enabled: Optional[bool] = None
    threshold: Optional[float] = Field(None, ge=-60, le=0, description="Threshold in dB")
    ratio: Optional[float] = Field(None, ge=1, le=20, description="Compression ratio")
    attack: Optional[float] = Field(None, ge=0.1, le=100, description="Attack time in ms")
    release: Optional[float] = Field(None, ge=10, le=1000, description="Release time in ms")
    makeup_gain: Optional[float] = Field(None, ge=0, le=30, description="Makeup gain in dB")


class EqualizerLoudnessRequest(BaseModel):
    """Equalizer loudness compensation request"""
    enabled: Optional[bool] = None
    high_boost: Optional[float] = Field(None, ge=0, le=15, description="High frequency boost in dB")
    low_boost: Optional[float] = Field(None, ge=0, le=15, description="Low frequency boost in dB")


class LevelsMonitorRequest(BaseModel):
    """Keepalive for the WS levels push; client MACs to aggregate (empty = local DAC)."""
    client_ids: List[str] = Field(default_factory=list)


# =============================================================================
# ZONE MANAGEMENT
# =============================================================================

# Import from domain model to avoid duplication (single source of truth)
from backend.core.multiroom.models import MAX_ZONE_NAME_LENGTH


class ZoneCreate(BaseModel):
    """Request model for zone creation."""
    name: str = Field(..., min_length=1, max_length=MAX_ZONE_NAME_LENGTH)
    client_ids: List[str] = Field(..., min_length=2)

    @field_validator('name')
    @classmethod
    def validate_name(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError('Zone name cannot be empty')
        return stripped

    @field_validator('client_ids')
    @classmethod
    def validate_client_ids(cls, v: List[str]) -> List[str]:
        seen = set()
        result = []
        for client_id in v:
            cleaned = client_id.strip()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                result.append(cleaned)
        if len(result) < 2:
            raise ValueError('At least 2 different clients are required for a zone')
        return result


class ZoneUpdate(BaseModel):
    """Request model for zone updates (PATCH operations)."""
    name: Optional[str] = Field(None, min_length=1, max_length=MAX_ZONE_NAME_LENGTH)

    @field_validator('name')
    @classmethod
    def validate_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        stripped = v.strip()
        if not stripped:
            raise ValueError('Zone name cannot be empty')
        return stripped


class ZoneAddClient(BaseModel):
    """Request model for adding a client to a zone."""
    mac_id: str = Field(..., min_length=1, description="MAC address of client to add")

    @field_validator('mac_id')
    @classmethod
    def validate_mac_id(cls, v: str) -> str:
        return v.strip()


# =============================================================================
# Speaker Types / Crossover Integration
# =============================================================================

class ZoneCrossoverRequest(BaseModel):
    """Zone crossover frequency configuration"""
    frequency: float = Field(
        default=80.0,
        ge=40,
        le=200,
        description="Crossover frequency in Hz (highpass cutoff for speakers)"
    )


class EqualizerPresetRequest(BaseModel):
    """Equalizer preset loading request"""
    preset_id: str = Field(..., min_length=1, max_length=50, description="Preset ID to load")

    @field_validator('preset_id')
    @classmethod
    def validate_preset_id(cls, v: str) -> str:
        # Preset IDs should be alphanumeric with underscores
        cleaned = v.strip().lower()
        if not cleaned.replace('_', '').isalnum():
            raise ValueError('Preset ID must contain only alphanumeric characters and underscores')
        return cleaned


# =============================================================================
# SETTINGS - MAC ROC STREAMING
# =============================================================================
# The accepted values and the defaults live in config/constants.py, shared with
# the settings validator and with MacEnv (which writes them to mac.env).


class RadioSettingsRequest(BaseModel):
    """Radio settings request"""
    shazam_enabled: bool


class QobuzSettingsRequest(BaseModel):
    """Qobuz settings request"""
    allow_app_volume: bool


class MusicLibrarySettingsRequest(BaseModel):
    """Music Library settings request"""
    separate_storages: bool


class SpotifySettingsRequest(BaseModel):
    """Spotify settings request.

    `apply_now` is request-only — it is an action, not a stored value, so it
    stays out of SpotifySettingsConfig (the shape /bulk and the WS event share).
    True restarts go-librespot so the new crossfade takes effect immediately;
    False just stores it for the daemon's next start. Ceiling matches the
    Spotify app's own 0-12 s range.
    """
    crossfade_duration: int = Field(ge=0, le=12000)
    apply_now: bool = False


# =============================================================================
# HARDWARE CONFIGURATION
# =============================================================================

class HardwareAudioRequest(BaseModel):
    """Audio card selection"""
    id: str = Field(..., min_length=1, max_length=50)
    volume_control: Optional[bool] = None  # Override DAC auto-detection (None = derive from card category)

    @field_validator('id')
    @classmethod
    def validate_audio_id(cls, v: str) -> str:
        from backend.hardware.registry import AUDIO_CARDS
        if v not in AUDIO_CARDS:
            raise ValueError(f"Unknown audio card '{v}'. Valid: {list(AUDIO_CARDS.keys())}")
        return v


class HardwareScreenRequest(BaseModel):
    """Screen type selection"""
    type: str = Field(..., min_length=1, max_length=50)

    @field_validator('type')
    @classmethod
    def validate_screen_type(cls, v: str) -> str:
        from backend.hardware.registry import SCREENS
        if v not in SCREENS:
            raise ValueError(f"Unknown screen type '{v}'. Valid: {list(SCREENS.keys())}")
        return v


class HardwareRotaryEncoderRequest(BaseModel):
    """Rotary encoder configuration (enabled flag + GPIO pins)"""
    enabled: bool = True
    clk_pin: int = Field(default=22, ge=GPIO_MIN_PIN, le=GPIO_MAX_PIN)
    dt_pin: int = Field(default=27, ge=GPIO_MIN_PIN, le=GPIO_MAX_PIN)
    sw_pin: int = Field(default=23, ge=GPIO_MIN_PIN, le=GPIO_MAX_PIN)

    @model_validator(mode='after')
    def validate_unique_pins(self):
        if self.enabled:
            pins = [self.clk_pin, self.dt_pin, self.sw_pin]
            if len(set(pins)) != len(pins):
                raise ValueError('All GPIO pins must be different')
        return self


class HardwareIrRemoteRequest(BaseModel):
    """IR remote configuration (enabled flag + TSOP4838 data line GPIO pin)"""
    enabled: bool = True
    gpio_pin: int = Field(default=17, ge=GPIO_MIN_PIN, le=GPIO_MAX_PIN)


class HardwareConfigRequest(BaseModel):
    """Full hardware configuration request"""
    audio: HardwareAudioRequest
    screen: HardwareScreenRequest
    rotary_encoder: HardwareRotaryEncoderRequest
    ir_remote: HardwareIrRemoteRequest

    @model_validator(mode='after')
    def validate_no_pin_collision(self):
        """Two peripherals, one GPIO header. The rotary encoder rejects its own
        duplicates but knows nothing of the IR data line, and this route reboots
        the unit — an accepted collision comes back as a dead remote (or a dead
        encoder) with nothing to point at."""
        if self.rotary_encoder.enabled and self.ir_remote.enabled:
            rotary_pins = {
                self.rotary_encoder.clk_pin,
                self.rotary_encoder.dt_pin,
                self.rotary_encoder.sw_pin,
            }
            if self.ir_remote.gpio_pin in rotary_pins:
                raise ValueError(
                    f'GPIO {self.ir_remote.gpio_pin} cannot be the IR data line: '
                    'the rotary encoder already uses it'
                )
        return self


class MacRocConfigRequest(BaseModel):
    """Mac ROC streaming configuration request"""
    target_latency_ms: int = Field(default=DEFAULT_ROC_CONFIG['target_latency_ms'], ge=20, le=500, description="Target latency in milliseconds")
    latency_profile: ROC_LATENCY_PROFILES = Field(default=DEFAULT_ROC_CONFIG['latency_profile'], description="Latency tuning profile")
    frame_length_ms: ROC_FRAME_LENGTHS = Field(default=DEFAULT_ROC_CONFIG['frame_length_ms'], description="Internal frame length in milliseconds")


# =============================================================================
# MULTIROOM - CLIENT
# =============================================================================

# Import from domain model to avoid duplication (single source of truth)
from backend.core.multiroom.models import SPEAKER_TYPES


class ClientUpdateRequest(BaseModel):
    """Request to update client properties (name, speaker_type, volume_control)."""
    name: Optional[str] = None
    speaker_type: Optional[Literal['satellite', 'bookshelf', 'tower', 'subwoofer']] = None
    volume_control: Optional[bool] = None  # True = Milo manages volume, False = external amp

    @field_validator('speaker_type')
    @classmethod
    def validate_speaker_type(cls, v):
        """Validate speaker_type against allowed values."""
        if v is not None and v not in SPEAKER_TYPES:
            raise ValueError(
                f"Invalid speaker_type '{v}'. "
                f"Must be one of: {', '.join(SPEAKER_TYPES)}"
            )
        return v


class ClientEqIndependentRequest(BaseModel):
    """Toggle a zone member's EQ independence (its EQ detaches from the zone)."""
    enabled: bool


class ClientDelayRequest(BaseModel):
    """Set a client's playback delay (native Snapcast latency, in ms)."""
    delay_ms: int = Field(..., ge=0, le=100, description="Playback delay in milliseconds")


class RegisterClientRequest(BaseModel):
    """Request from a milo-client to register as a pending speaker."""
    mac_id: str = Field(..., min_length=17, max_length=17)
    ip: str = Field(..., min_length=7)
    hardware_configured: bool
    audio_id: str = Field(default="none")
    volume_control: bool = Field(default=True)  # False for DAC cards (external amp)
    # Identity carried by wifi-adopted clients so the server can pre-fill the
    # registry without waiting for a separate configure step.
    name: Optional[str] = Field(default=None, max_length=64)
    speaker_type: Optional[Literal['satellite', 'bookshelf', 'tower', 'subwoofer']] = None

    @field_validator('mac_id')
    @classmethod
    def validate_mac_id(cls, v):
        """Validate MAC address format (xx:xx:xx:xx:xx:xx)."""
        import re
        if not re.match(r'^([0-9a-f]{2}:){5}[0-9a-f]{2}$', v, re.IGNORECASE):
            raise ValueError(f"Invalid MAC address format: '{v}'")
        return v.lower()

    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        if v is None:
            return v
        stripped = v.strip()
        return stripped or None

    @field_validator('speaker_type')
    @classmethod
    def validate_speaker_type(cls, v):
        if v is not None and v not in SPEAKER_TYPES:
            raise ValueError(f"Invalid speaker_type '{v}'. Must be one of: {', '.join(SPEAKER_TYPES)}")
        return v


def _validate_configurable_audio_id(v: str) -> str:
    """Shared validator for audio_id fields that exclude 'none'."""
    from backend.hardware.registry import AUDIO_CARDS
    if v not in AUDIO_CARDS or v == 'none':
        valid = [k for k in AUDIO_CARDS if k != 'none']
        raise ValueError(f"Invalid audio_id '{v}'. Must be one of: {', '.join(valid)}")
    return v


class ConfigureClientAudioRequest(BaseModel):
    """Request to change audio card on a registered milo-client and reboot it."""
    audio_id: str = Field(..., min_length=1)
    volume_control: Optional[bool] = None  # Override auto-detection (None = derive from card category)

    @field_validator('audio_id')
    @classmethod
    def validate_audio_id(cls, v):
        return _validate_configurable_audio_id(v)


class ConfigurePendingClientRequest(BaseModel):
    """Request to configure a pending client's audio and reboot it."""
    name: Optional[str] = None
    speaker_type: Optional[Literal['satellite', 'bookshelf', 'tower', 'subwoofer']] = Field(default='bookshelf')
    audio_id: str = Field(..., min_length=1)
    volume_control: Optional[bool] = None  # Override auto-detection (None = derive from card category)

    @field_validator('speaker_type')
    @classmethod
    def validate_speaker_type(cls, v):
        if v is not None and v not in SPEAKER_TYPES:
            raise ValueError(f"Invalid speaker_type '{v}'. Must be one of: {', '.join(SPEAKER_TYPES)}")
        return v

    @field_validator('audio_id')
    @classmethod
    def validate_audio_id(cls, v):
        return _validate_configurable_audio_id(v)
