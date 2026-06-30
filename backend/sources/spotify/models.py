# backend/sources/spotify/models.py
"""
Pydantic command-parameter models for the Spotify audio source.

These validate the `data` of `/api/audio/control/spotify` commands at the
command() boundary; see SpotifySource.COMMANDS.
"""
from typing import Optional
from pydantic import BaseModel, Field


class SeekParams(BaseModel):
    """Params for the `seek` command (absolute position in milliseconds)."""
    position_ms: float = Field(ge=0)


class NextPrevParams(BaseModel):
    """Params for `next`/`prev` (optional target track URI)."""
    uri: Optional[str] = None
