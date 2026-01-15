"""
Pydantic models for API request validation.
"""
from pydantic import BaseModel
from typing import Optional


class FilterUpdate(BaseModel):
    """Model for filter update request."""
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
    reference_level: Optional[int] = None
    high_boost: Optional[float] = None
    low_boost: Optional[float] = None


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
