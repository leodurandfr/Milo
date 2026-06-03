# backend/sources/podcast/models.py
"""
Pydantic models for Podcast API requests and responses.
"""
from pydantic import BaseModel
from typing import Optional


class PlayEpisodeRequest(BaseModel):
    """Request to play an episode."""
    episode_uuid: str
    position: Optional[int] = None  # Resume position


class SpeedRequest(BaseModel):
    """Request to set playback speed."""
    speed: float


class SubscribeRequest(BaseModel):
    """Request to subscribe to a podcast."""
    uuid: str
    name: str
    image_url: str
    children_hash: Optional[str] = ""


class SettingsRequest(BaseModel):
    """Request to update podcast settings."""
    playback_speed: Optional[float] = None
