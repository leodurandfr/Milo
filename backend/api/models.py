# backend/api/models.py
"""
Pydantic models for API request validation
"""
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Optional, Dict, Any, List, Literal


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

class VolumeSetRequest(BaseModel):
    """Volume set request (in dB)"""
    volume_db: float = Field(..., ge=-80, le=0, description="Volume in dB")
    show_bar: bool = Field(default=True)


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

class SnapcastClientNameRequest(BaseModel):
    """Snapcast client name request"""
    name: str = Field(..., min_length=1, max_length=100)

    @field_validator('name')
    @classmethod
    def validate_name(cls, v: str) -> str:
        return v.strip()


class SnapcastServerConfigRequest(BaseModel):
    """Snapcast server configuration request"""
    config: Dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# EQUALIZER
# =============================================================================

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


class VolumeSettingsPatchRequest(BaseModel):
    """Partial update request for volume settings (AC4, AC5)"""
    startup_volume_db: Optional[float] = Field(None, ge=-80, le=0, description="Startup volume in dB")
    restore_last_volume: Optional[bool] = Field(None, description="Whether to restore last volume on startup")


class VolumeStepsRequest(BaseModel):
    """Mobile volume steps request (in dB)"""
    step_mobile_db: float = Field(..., ge=1, le=6, description="Mobile volume step in dB")


class RotaryStepsRequest(BaseModel):
    """Rotary encoder volume steps request (in dB)"""
    step_rotary_db: float = Field(..., ge=1, le=6, description="Rotary volume step in dB")


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
        # Check all apps are valid
        invalid_apps = set(v) - VALID_DOCK_APPS
        if invalid_apps:
            raise ValueError(f'Invalid apps: {invalid_apps}. Valid apps: {VALID_DOCK_APPS}')

        # At least one audio source must be enabled
        enabled_audio_sources = set(v) & AUDIO_SOURCE_APPS
        if not enabled_audio_sources:
            raise ValueError('At least one audio source must be enabled')

        return v


# =============================================================================
# SETTINGS - SPOTIFY
# =============================================================================

class SpotifyDisconnectRequest(BaseModel):
    """Spotify auto-disconnect delay request"""
    auto_disconnect_delay: float = Field(..., ge=0, le=9999)

    @field_validator('auto_disconnect_delay')
    @classmethod
    def validate_delay(cls, v: float) -> float:
        # 0 means disabled, otherwise must be >= 1.0
        if v != 0 and v < 1.0:
            raise ValueError('Delay must be 0 (disabled) or >= 1.0 seconds')
        return v


# =============================================================================
# SETTINGS - PODCAST
# =============================================================================

class PodcastCredentialsRequest(BaseModel):
    """Podcast Taddy API credentials request"""
    taddy_user_id: str
    taddy_api_key: str

    @field_validator('taddy_user_id', 'taddy_api_key')
    @classmethod
    def strip_whitespace(cls, v: str) -> str:
        return v.strip()


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


# =============================================================================
# DSP (CamillaDSP)
# =============================================================================

EQUALIZER_FILTER_TYPES = Literal['Peaking', 'Lowshelf', 'Highshelf', 'Lowpass', 'Highpass', 'Notch', 'Allpass']


class EqualizerFilterRequest(BaseModel):
    """Equalizer filter configuration request"""
    freq: float = Field(..., ge=20, le=20000, description="Filter frequency in Hz")
    gain: float = Field(..., ge=-15, le=15, description="Filter gain in dB")
    q: float = Field(default=1.0, ge=0.1, le=10.0, description="Filter Q factor")
    filter_type: EQUALIZER_FILTER_TYPES = Field(default="Peaking", description="Filter type")
    enabled: bool = Field(default=True, description="Whether filter is active")


class EqualizerFilterUpdateRequest(BaseModel):
    """Equalizer filter update request (partial update allowed)"""
    freq: Optional[float] = Field(None, ge=20, le=20000)
    gain: Optional[float] = Field(None, ge=-15, le=15)
    q: Optional[float] = Field(None, ge=0.1, le=10.0)
    filter_type: Optional[EQUALIZER_FILTER_TYPES] = None
    enabled: Optional[bool] = None


class EqualizerMuteRequest(BaseModel):
    """Equalizer mute request"""
    muted: bool


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


class CrossoverFilterRequest(BaseModel):
    """Direct crossover filter configuration (for milo-client API)"""
    enabled: bool = Field(..., description="Enable or disable crossover highpass filter")
    frequency: float = Field(default=80.0, ge=40, le=200, description="Crossover frequency in Hz")
    q: float = Field(default=0.707, ge=0.5, le=1.5, description="Filter Q factor (0.707 = Butterworth)")


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

ROC_LATENCY_PROFILES = Literal['responsive', 'gradual', 'intact']
ROC_FRAME_LENGTHS = Literal[2, 4, 7, 8, 12]


class InactivityTimeoutRequest(BaseModel):
    """Audio inactivity timeout request"""
    inactivity_timeout: int = Field(..., ge=0, le=86400)

    @field_validator('inactivity_timeout')
    @classmethod
    def validate_timeout(cls, v: int) -> int:
        # 0 means disabled, otherwise must be >= 300 (5 min)
        if v != 0 and v < 300:
            raise ValueError('Timeout must be 0 (disabled) or >= 300 seconds (5 min)')
        return v


class RadioSettingsRequest(BaseModel):
    """Radio settings request"""
    shazam_enabled: bool


class MacRocConfigRequest(BaseModel):
    """Mac ROC streaming configuration request"""
    target_latency_ms: int = Field(default=200, ge=5, le=500, description="Target latency in milliseconds")
    latency_profile: ROC_LATENCY_PROFILES = Field(default='responsive', description="Latency tuning profile")
    frame_length_ms: ROC_FRAME_LENGTHS = Field(default=7, description="Internal frame length in milliseconds")
