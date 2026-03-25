# backend/features/cd/models.py
"""
Pydantic models for the CD audio source.
"""
from typing import Optional, List
from pydantic import BaseModel


class PlayTrackRequest(BaseModel):
    """Request to play a specific track on the CD."""
    track_number: int  # 1-based


class SeekRequest(BaseModel):
    """Request to seek within the current track."""
    position: int  # seconds within current track


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
