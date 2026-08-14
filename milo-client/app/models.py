"""
Pydantic models for API request validation.
"""
from pydantic import BaseModel, Field
from typing import Optional, List


class FilterUpdate(BaseModel):
    """Model for filter update request (tuning only).

    Carries no `enabled`: pipeline membership is owned by the master toggle
    (PUT /equalizer/enabled), so editing a band never un-bypasses the client.
    """
    gain: float
    freq: Optional[float] = None
    q: Optional[float] = None
    filter_type: Optional[str] = None


class CompressorUpdate(BaseModel):
    """Model for compressor update request."""
    enabled: Optional[bool] = None
    threshold: Optional[float] = None
    ratio: Optional[float] = None
    attack: Optional[float] = None
    release: Optional[float] = None
    makeup_gain: Optional[float] = None


class LoudnessUpdate(BaseModel):
    """Model for loudness update request."""
    enabled: Optional[bool] = None
    high_boost: Optional[float] = None
    low_boost: Optional[float] = None


class MonoUpdate(BaseModel):
    """Model for mono mixing update request."""
    enabled: bool


class DelayUpdate(BaseModel):
    """Model for delay update request."""
    left: Optional[float] = None
    right: Optional[float] = None


class VolumeUpdate(BaseModel):
    """Model for volume update request."""
    volume: float


class MuteUpdate(BaseModel):
    """Model for mute update request."""
    muted: bool


class CrossoverUpdate(BaseModel):
    """Model for crossover highpass filter update."""
    enabled: bool
    frequency: Optional[float] = 80.0
    q: Optional[float] = 0.707


class LowpassUpdate(BaseModel):
    """Model for lowpass filter update (for subwoofers)."""
    enabled: bool
    frequency: Optional[float] = 80.0
    q: Optional[float] = 0.707


class FiltersBatchUpdate(BaseModel):
    """Model for batch filter update request."""
    filters: List[dict]  # [{id: "eq_band_00", gain: 5.0, freq: 31, q: 1.41}, ...]


class EqualizerEnabledUpdate(BaseModel):
    """Model for equalizer enabled state update."""
    enabled: bool


class SnapclientConfigUpdate(BaseModel):
    """Model for snapclient ALSA buffer configuration update.

    Both fields are required and bounded here rather than clamped in the
    handler: the server sends the pair on every push, already resolved against
    the same range (`SNAPCLIENT_LIMITS` in the backend's multiroom/routing.py,
    which a satellite tarball does not carry). A value outside it means the two
    halves disagree — worth a loud 422 the server logs, not a satellite quietly
    running a different ALSA buffer than the rest of the house.
    """
    buffer_time: int = Field(..., ge=60, le=300)
    fragments: int = Field(..., ge=2, le=8)
