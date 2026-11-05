# backend/sources/cd/models.py
"""
Pydantic models for the CD audio source.
"""
from typing import Optional, List
from pydantic import BaseModel, Field

from backend.core.models.commands import StrictModel


# === Command-parameter models (validated at the command() boundary) ===

class PlayTrackParams(StrictModel):
    """Params for `play_track` (1-based; disc upper bound is state-checked in the handler)."""
    track_number: int = Field(ge=1)


class SeekParams(StrictModel):
    """Params for `seek` (absolute position in milliseconds)."""
    position_ms: float = Field(ge=0)


class TrackInfo(BaseModel):
    """Information about a single CD track."""
    number: int  # 1-based
    title: Optional[str]  # None when nothing names it: each screen numbers it
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
    # MusicBrainz named the disc. Not `album is not None`: a release it names
    # may still leave the album out, and that disc must not be asked again.
    identified: bool = False
