# backend/sources/cd/models.py
"""
Pydantic models for the CD audio source.
"""
from typing import Optional, List
from pydantic import BaseModel, Field


# === Command-parameter models (validated at the command() boundary) ===

class PlayTrackParams(BaseModel):
    """Params for `play_track` (1-based; disc upper bound is state-checked in the handler)."""
    track_number: int = Field(ge=1)


class SeekParams(BaseModel):
    """Params for `seek` (absolute position in milliseconds)."""
    position_ms: float = Field(ge=0)


class TrackInfo(BaseModel):
    """Information about a single CD track."""
    number: int  # 1-based
    title: str
    duration: int  # seconds


class DiscInfo(BaseModel):
    """Metadata for a CD disc."""
    disc_id: str
    album: Optional[str] = None
    artist: Optional[str] = None
    year: Optional[str] = None
    cover_url: Optional[str] = None  # local URL: /api/cd/cover/{disc_id}
    track_count: int
    total_duration: int  # seconds
    tracks: List[TrackInfo] = []
