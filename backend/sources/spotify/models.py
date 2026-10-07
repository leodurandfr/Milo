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


class NextParams(BaseModel):
    """Params for `next` (optional target track URI)."""
    uri: Optional[str] = None


class PrevParams(BaseModel):
    """Params for `prev`. The transport's button rewinds a track played past
    its first three seconds, as Spotify does; `allow_seeking` false (the
    mini-bar's swipe, which already slid the previous title in) always goes
    to the previous track. Spotify Connect's own name for the option."""
    allow_seeking: bool = True


class PlayContextParams(BaseModel):
    """Params for `play_context`: play a context (playlist, album, artist, the
    account's Liked Songs collection) from its first track, or from
    `skip_to_uri`. `shuffle` is the shuffle state the context plays in; the
    browser starts a shuffled play on a track it picks at random, as
    `skip_to_uri` (see SpotifySource._play_context)."""
    uri: str = Field(min_length=1)
    skip_to_uri: Optional[str] = None
    shuffle: bool = False


# === Browser route bodies ===

class ActiveProfileRequest(BaseModel):
    """`PUT /api/spotify/active-profile`: the profile to sign go-librespot in as."""
    username: str = Field(min_length=1)
