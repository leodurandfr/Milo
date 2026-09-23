# backend/sources/podcast/models.py
"""
Pydantic models for Podcast API requests and responses.
"""
from pydantic import BaseModel, Field, model_validator
from typing import Optional


class SubscribeRequest(BaseModel):
    """Request to subscribe to a podcast."""
    uuid: str
    name: str
    image_url: str
    children_hash: Optional[str] = ""


# === Command-parameter models (validated at the command() boundary) ===

class PlayEpisodeParams(BaseModel):
    """Params for `play_episode`. `position` (seconds) starts the episode there
    instead of at the second the progress file kept."""
    episode_uuid: str = Field(min_length=1)
    position: Optional[int] = Field(default=None, ge=0)


class SeekParams(BaseModel):
    """Params for `seek`. Accepts `position` (seconds) or `position_ms`.

    position_ms is the wire convention used by useSourceProgress.seekTo
    (shared with Spotify); `position` is seconds. Exactly the raw fields are
    kept; `.seconds` normalizes to a single value.
    """
    position: Optional[float] = None
    position_ms: Optional[float] = None

    @model_validator(mode="after")
    def _require_one(self):
        if self.position is None and self.position_ms is None:
            raise ValueError("position or position_ms required")
        return self

    @property
    def seconds(self) -> float:
        return self.position if self.position is not None else self.position_ms / 1000


class SetSpeedParams(BaseModel):
    """Params for `set_speed` (off-grid values are snapped in the handler)."""
    speed: float
