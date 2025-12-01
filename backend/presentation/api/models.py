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
    """Volume set request"""
    volume: int = Field(..., ge=0, le=100)
    show_bar: bool = Field(default=True)


class VolumeAdjustRequest(BaseModel):
    """Volume adjustment request"""
    delta: int = Field(..., ge=-100, le=100)
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
# SETTINGS - VOLUME
# =============================================================================

class VolumeLimitsRequest(BaseModel):
    """Volume limits request"""
    alsa_min: int = Field(..., ge=0, le=100)
    alsa_max: int = Field(..., ge=0, le=100)

    @model_validator(mode='after')
    def validate_range(self):
        if self.alsa_max - self.alsa_min < 10:
            raise ValueError('Range between alsa_min and alsa_max must be at least 10')
        return self


class VolumeLimitsToggleRequest(BaseModel):
    """Volume limits toggle request"""
    enabled: bool


class VolumeStartupRequest(BaseModel):
    """Volume startup configuration request"""
    startup_volume: int = Field(..., ge=0, le=100)
    restore_last_volume: bool


class VolumeStepsRequest(BaseModel):
    """Mobile volume steps request"""
    mobile_volume_steps: int = Field(..., ge=1, le=10)


class RotaryStepsRequest(BaseModel):
    """Rotary encoder volume steps request"""
    rotary_volume_steps: int = Field(..., ge=1, le=10)


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
