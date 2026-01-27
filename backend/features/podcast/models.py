# backend/features/podcast/models.py
"""
Pydantic models for Podcast API requests and responses.
"""
from pydantic import BaseModel
from typing import Optional, List, Dict, Any


class PlayEpisodeRequest(BaseModel):
    """Request to play an episode."""
    episode_uuid: str
    position: Optional[int] = None  # Resume position


class SeekRequest(BaseModel):
    """Request to seek to a position."""
    position: int


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
    safeMode: Optional[bool] = None
    playbackSpeed: Optional[float] = None


class Podcast(BaseModel):
    """Podcast series model."""
    uuid: str
    name: str
    description: str = ""
    image_url: str = ""
    publisher: str = ""
    total_episodes: int = 0
    genres: List[str] = []
    language: str = ""
    is_subscribed: bool = False


class Episode(BaseModel):
    """Podcast episode model."""
    uuid: str
    name: str
    description: str = ""
    audio_url: str
    image_url: str = ""
    duration: int = 0
    date_published: Optional[int] = None
    podcast: Optional[Dict[str, Any]] = None
    playback_progress: Optional[Dict[str, Any]] = None


class Subscription(BaseModel):
    """Subscription model with metadata."""
    uuid: str
    name: str
    imageUrl: str = ""
    childrenHash: str = ""
    addedAt: int = 0
    lastChecked: int = 0


class PlaybackProgress(BaseModel):
    """Playback progress model."""
    position: int = 0
    duration: int = 0
    lastPlayed: int = 0
    completed: bool = False
    podcastUuid: str = ""
    episodeName: str = ""
    podcastName: str = ""
    imageUrl: str = ""


class SearchResult(BaseModel):
    """Search result model."""
    podcasts: List[Dict[str, Any]] = []
    episodes: List[Dict[str, Any]] = []
    pagination: Dict[str, Any] = {}
