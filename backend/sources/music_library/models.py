# backend/sources/music_library/models.py
"""Pydantic models for the Music Library source.

Two groups:
- REST request bodies (P1-5): the favorite (star) toggle. Browse/search/genres/
  playlists are GET endpoints validated with FastAPI Query params, and the
  Subsonic response shapes are proxied through as-is (raw dicts) — the frontend
  reads Subsonic fields directly — so there are no response models here.
- Command params (P1-6): the typed contract for the playback commands dispatched
  through the generic ``/api/audio/control/music_library`` path and validated at
  the source's ``command()`` boundary (play_context / play_index / seek).
"""
from typing import Any, Dict, List, Literal

from pydantic import BaseModel, Field, field_validator


class StarRequest(BaseModel):
    """Body for ``POST /music-library/star`` and ``/unstar``.

    ``kind`` selects the id param Subsonic expects: a song id, an ``albumId`` or
    an ``artistId`` — starring an album/artist is distinct from starring a song.
    """

    id: str = Field(min_length=1)
    kind: Literal["song", "album", "artist"] = "song"


# === Command-parameter models (validated at the command() boundary) ===

class PlayContextParams(BaseModel):
    """Params for ``play_context``: an ordered set of songs → gapless mpv queue.

    ``tracks`` are the Subsonic song dicts the frontend already holds from
    browsing a context (album/genre/playlist/search results); each needs at
    least an ``id`` (its stream URL is derived from it). They are stored and
    echoed back verbatim as the now-playing queue, so their Subsonic shape is
    passed through untouched (the frontend reads Subsonic fields directly).
    ``start_index`` is the entry to begin on; ``shuffle`` randomizes the rest
    behind it (the picked track still plays first).
    """

    tracks: List[Dict[str, Any]] = Field(min_length=1)
    start_index: int = Field(default=0, ge=0)
    shuffle: bool = False

    @field_validator("tracks")
    @classmethod
    def _tracks_need_ids(cls, tracks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        for i, track in enumerate(tracks):
            if not track.get("id"):
                raise ValueError(f"tracks[{i}] is missing an 'id'")
        return tracks


class PlayIndexParams(BaseModel):
    """Params for ``play_index``: jump to a 0-based position in the current queue
    (the queue-view row tap). The upper bound is state-checked in the handler."""

    index: int = Field(ge=0)


class SeekParams(BaseModel):
    """Params for ``seek`` (absolute position in milliseconds — the shared
    player's ``useSourceProgress.seekTo`` wire convention)."""

    position_ms: float = Field(ge=0)
