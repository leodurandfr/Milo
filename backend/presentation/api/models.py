# backend/presentation/api/models.py
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


# =============================================================================
# SNAPCAST
# =============================================================================

class SnapcastVolumeRequest(BaseModel):
    """Snapcast volume request"""
    volume: int = Field(..., ge=0, le=100)


class SnapcastClientMuteRequest(BaseModel):
    """Snapcast client mute request"""
    muted: bool


class SnapcastGroupStreamRequest(BaseModel):
    """Group stream change request"""
    stream_id: str = Field(..., min_length=1, max_length=100)


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

class EqualizerBandRequest(BaseModel):
    """Equalizer band value request"""
    value: int = Field(..., ge=0, le=100)


class EqualizerResetRequest(BaseModel):
    """Equalizer reset request"""
    value: int = Field(default=50, ge=0, le=100)


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


class VolumeStepsRequest(BaseModel):
    """Mobile volume steps request (in dB)"""
    step_mobile_db: float = Field(..., ge=1, le=6, description="Mobile volume step in dB")


class RotaryStepsRequest(BaseModel):
    """Rotary encoder volume steps request (in dB)"""
    step_rotary_db: float = Field(..., ge=1, le=6, description="Rotary volume step in dB")


# =============================================================================
# SETTINGS - DOCK APPS
# =============================================================================

VALID_DOCK_APPS = {'spotify', 'bluetooth', 'mac', 'radio', 'podcast', 'multiroom', 'equalizer', 'settings'}
AUDIO_SOURCE_APPS = {'spotify', 'bluetooth', 'mac', 'radio', 'podcast'}

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


# =============================================================================
# DSP (CamillaDSP)
# =============================================================================

DSP_FILTER_TYPES = Literal['Peaking', 'Lowshelf', 'Highshelf', 'Lowpass', 'Highpass', 'Notch', 'Allpass']


class DspFilterRequest(BaseModel):
    """DSP filter configuration request"""
    freq: float = Field(..., ge=20, le=20000, description="Filter frequency in Hz")
    gain: float = Field(..., ge=-15, le=15, description="Filter gain in dB")
    q: float = Field(default=1.0, ge=0.1, le=10.0, description="Filter Q factor")
    filter_type: DSP_FILTER_TYPES = Field(default="Peaking", description="Filter type")
    enabled: bool = Field(default=True, description="Whether filter is active")


class DspFilterUpdateRequest(BaseModel):
    """DSP filter update request (partial update allowed)"""
    freq: Optional[float] = Field(None, ge=20, le=20000)
    gain: Optional[float] = Field(None, ge=-15, le=15)
    q: Optional[float] = Field(None, ge=0.1, le=10.0)
    filter_type: Optional[DSP_FILTER_TYPES] = None
    enabled: Optional[bool] = None


class DspPresetRequest(BaseModel):
    """DSP preset save/load request"""
    name: str = Field(..., min_length=1, max_length=50)

    @field_validator('name')
    @classmethod
    def validate_name(cls, v: str) -> str:
        # Allow only alphanumeric, spaces, hyphens, underscores
        cleaned = v.strip()
        if not all(c.isalnum() or c in ' -_' for c in cleaned):
            raise ValueError('Preset name can only contain alphanumeric characters, spaces, hyphens, and underscores')
        return cleaned


class DspVolumeRequest(BaseModel):
    """DSP volume request"""
    volume: float = Field(..., ge=-100, le=0, description="Volume in dB")


class DspMuteRequest(BaseModel):
    """DSP mute request"""
    muted: bool


class DspCompressorRequest(BaseModel):
    """DSP compressor settings request"""
    enabled: Optional[bool] = None
    threshold: Optional[float] = Field(None, ge=-60, le=0, description="Threshold in dB")
    ratio: Optional[float] = Field(None, ge=1, le=20, description="Compression ratio")
    attack: Optional[float] = Field(None, ge=0.1, le=100, description="Attack time in ms")
    release: Optional[float] = Field(None, ge=10, le=1000, description="Release time in ms")
    makeup_gain: Optional[float] = Field(None, ge=0, le=30, description="Makeup gain in dB")


class DspLoudnessRequest(BaseModel):
    """DSP loudness compensation request"""
    enabled: Optional[bool] = None
    reference_level: Optional[int] = Field(None, ge=60, le=100, description="Reference SPL level")
    high_boost: Optional[float] = Field(None, ge=0, le=15, description="High frequency boost in dB")
    low_boost: Optional[float] = Field(None, ge=0, le=15, description="Low frequency boost in dB")


class DspDelayRequest(BaseModel):
    """DSP channel delay request"""
    left: Optional[float] = Field(None, ge=0, le=50, description="Left channel delay in ms")
    right: Optional[float] = Field(None, ge=0, le=50, description="Right channel delay in ms")


class DspLinkedClientsRequest(BaseModel):
    """DSP linked clients request - clients that share the same DSP settings"""
    client_ids: List[str] = Field(..., min_length=2, description="List of client IDs to link together")
    source_client: Optional[str] = Field(None, description="Client whose settings will be pushed to others (defaults to first in list)")

    @field_validator('client_ids')
    @classmethod
    def validate_client_ids(cls, v: List[str]) -> List[str]:
        # Remove duplicates while preserving order
        seen = set()
        result = []
        for client_id in v:
            cleaned = client_id.strip()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                result.append(cleaned)
        if len(result) < 2:
            raise ValueError('At least 2 different clients must be linked')
        return result
