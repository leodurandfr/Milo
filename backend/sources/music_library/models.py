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
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class StarRequest(BaseModel):
    """Body for ``POST /music-library/star`` and ``/unstar``.

    ``kind`` selects the id param Subsonic expects: a song id, an ``albumId`` or
    an ``artistId`` — starring an album/artist is distinct from starring a song.
    """

    id: str = Field(min_length=1)
    kind: Literal["song", "album", "artist"] = "song"


# === Playlist create/edit requests (Phase 3) ===

class CreatePlaylistRequest(BaseModel):
    """Body for ``POST /music-library/playlists`` (Subsonic ``createPlaylist``).

    Optionally seed the new playlist with an ordered list of song ids (the
    "add these tracks to a new playlist" flow); omit for an empty playlist.
    """

    name: str = Field(min_length=1, max_length=255)
    song_ids: Optional[List[str]] = None


class UpdatePlaylistRequest(BaseModel):
    """Body for ``PUT /music-library/playlist/{id}`` — exactly one edit per call:

    - ``name``            → rename (Subsonic ``updatePlaylist``).
    - ``song_ids_to_add`` → append tracks (the ⋯ "add to playlist" action).
    - ``track_ids``       → replace the whole ordered list (reorder/remove; an
      empty list clears the playlist). ``None`` means "not this operation" —
      distinct from ``[]`` — so removing the last track is expressible.
    """

    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    song_ids_to_add: Optional[List[str]] = None
    track_ids: Optional[List[str]] = None

    @model_validator(mode="after")
    def _exactly_one_edit(self) -> "UpdatePlaylistRequest":
        provided = sum(
            field is not None
            for field in (self.name, self.song_ids_to_add, self.track_ids)
        )
        if provided != 1:
            raise ValueError(
                "provide exactly one of: name, song_ids_to_add, track_ids"
            )
        return self


# === Network-share requests (Phase 2) ===

class ShareRequest(BaseModel):
    """Body for ``POST /music-library/shares`` and ``PUT .../shares/{id}``.

    A CIFS or NFS network share to mount read-only under /media/milo. ``path`` is
    the SMB share name (optionally with a subpath) or the NFS export path.
    Credentials are optional — a public share needs none. The password is never
    stored here or echoed back over the API; it is handed to milo-mount on stdin
    and persisted only to a root-only cred file. On a ``PUT`` that omits the
    password, the existing cred file is kept.

    ``host``/``path`` become arguments to the mount syscall, so they are rejected
    if they carry whitespace or control characters (defense-in-depth — milo-mount
    re-validates independently); the credential fields may not carry control
    characters (a newline would inject an extra key into the cred file).
    """

    type: Literal["cifs", "nfs"]
    host: str = Field(min_length=1, max_length=255)
    path: str = Field(min_length=1, max_length=1024)
    name: str = Field(min_length=1, max_length=128)
    username: Optional[str] = Field(default=None, max_length=128)
    password: Optional[str] = Field(default=None, max_length=256)
    domain: Optional[str] = Field(default=None, max_length=128)

    @field_validator("host")
    @classmethod
    def _host_no_whitespace(cls, value: str) -> str:
        value = value.strip()
        if not value or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("host must not be empty or contain whitespace/control characters")
        return value

    @field_validator("path")
    @classmethod
    def _path_no_control(cls, value: str) -> str:
        # The path may contain spaces (real folder names like "My Music"); it is
        # passed to milo-mount as a single argument, so only control characters
        # (which could inject a mount option) are rejected.
        value = value.strip()
        if not value:
            raise ValueError("path must not be empty")
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("path must not contain control characters")
        return value

    @field_validator("name", "username", "password", "domain")
    @classmethod
    def _no_control_chars(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("must not contain control characters")
        return value

    @model_validator(mode="after")
    def _nfs_takes_no_credentials(self) -> "ShareRequest":
        # Plain NFS (AUTH_SYS) authorizes by UID/host, not a client username or
        # password — accepting credentials here would set a misleading
        # has_credentials flag and mount identically. Reject them outright.
        if self.type == "nfs" and (self.username or self.password or self.domain):
            raise ValueError("NFS shares do not take username/password/domain")
        return self


class ShareBrowseRequest(BaseModel):
    """Body for ``POST /music-library/shares/browse`` (the add-share wizard).

    Walks a server one level deep *without mounting* to build a share config:
    ``path`` empty → the top level (SMB shares / NFS exports); non-empty (SMB
    only) → the folders under ``<share>/<subpath>``. CIFS credentials feed the
    ``smbclient`` call (and let the backend report ``auth_required``); they are
    used transiently and never persisted here. ``path`` may contain spaces (real
    folder names do) but no control characters (it reaches a subprocess).
    """

    type: Literal["cifs", "nfs"]
    host: str = Field(min_length=1, max_length=255)
    path: str = Field(default="", max_length=1024)
    username: Optional[str] = Field(default=None, max_length=128)
    password: Optional[str] = Field(default=None, max_length=256)
    domain: Optional[str] = Field(default=None, max_length=128)

    @field_validator("host")
    @classmethod
    def _host_no_whitespace(cls, value: str) -> str:
        value = value.strip()
        if not value or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("invalid host")
        return value

    @field_validator("path", "username", "password", "domain")
    @classmethod
    def _no_control_chars(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("must not contain control characters")
        return value


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


class SetShuffleParams(BaseModel):
    """Params for ``set_shuffle``: the desired shuffle state (the player's toggle
    sends the target, not a flip, so a stale click can't invert it)."""

    shuffle: bool
