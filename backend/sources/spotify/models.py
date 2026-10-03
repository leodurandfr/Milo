# backend/sources/spotify/models.py
"""
Pydantic command-parameter models for the Spotify audio source.

These validate the `data` of `/api/audio/control/spotify` commands at the
command() boundary; see SpotifySource.COMMANDS.
"""
from typing import Literal, Optional
from pydantic import BaseModel, Field


class SeekParams(BaseModel):
    """Params for the `seek` command (absolute position in milliseconds)."""
    position_ms: float = Field(ge=0)


class NextPrevParams(BaseModel):
    """Params for `next`/`prev` (optional target track URI)."""
    uri: Optional[str] = None


class PlayContextParams(BaseModel):
    """Params for `play_context`: play a context (playlist, album, artist, the
    account's Liked Songs collection) from its first track, or from
    `skip_to_uri`. `shuffle` is the shuffle state the context plays in; the
    browser starts a shuffled play on a track it picks at random, as
    `skip_to_uri` (see SpotifySource._play_context)."""
    uri: str = Field(min_length=1)
    skip_to_uri: Optional[str] = None
    shuffle: bool = False


class SetRepeatParams(BaseModel):
    """Params for `set_repeat`: the target repeat mode (the player's button
    cycles off → context → track and sends where it lands)."""
    mode: Literal["off", "context", "track"]


# === Browser route bodies ===

class RenameProfileRequest(BaseModel):
    """`PATCH /api/spotify/profiles/{username}`: null goes back to the Spotify name."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=64)


class ActiveProfileRequest(BaseModel):
    """`PUT /api/spotify/active-profile`: the profile to sign go-librespot in as."""
    username: str = Field(min_length=1)
