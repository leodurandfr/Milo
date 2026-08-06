# backend/core/models/source_metadata.py
"""Canonical playback metadata shared across audio sources.

These fields are the "now playing" projection consumed *generically* by the
frontend shared player (AudioPlayerFull), screensaver, and progress
composables — the only part of source metadata that is a cross-source
contract. Source-specific fields (station/episode/disc/device) ride alongside
as plain-dict "extras" assembled by BaseAudioSource.emit_connection_state().
"""
from typing import Any, Dict, Optional, Tuple

from pydantic import BaseModel, ConfigDict, field_validator


class PlaybackMetadata(BaseModel):
    """Typed projection of playback status common to media sources.

    Mute receivers without a playback concept (Mac) don't use this — they pass
    playback=None to emit_connection_state and carry only extras.
    is_playing/is_buffering always serialize (so every media source emits them
    consistently); position/duration are milliseconds.
    """
    model_config = ConfigDict(extra="ignore")

    is_playing: bool = False
    is_buffering: bool = False
    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    album_art_url: Optional[str] = None
    position: Optional[int] = None
    duration: Optional[int] = None

    @field_validator("position", "duration", mode="before")
    @classmethod
    def _coerce_ms_to_int(cls, v: Any) -> Any:
        """Tolerate float milliseconds — sources compute these differently
        (frame math, playback-time, FP rounding); the wire convention is int
        ms. Non-numeric values fall through to normal validation."""
        return int(v) if isinstance(v, (int, float)) else v

    @classmethod
    def split(cls, data: Dict[str, Any]) -> Tuple["PlaybackMetadata", Dict[str, Any]]:
        """Split a raw metadata dict into (typed core, leftover extras).

        Canonical keys populate the typed core (unknown keys ignored); every
        other key is returned as extras. Lets a source build one rich dict
        (e.g. _build_playback_metadata) and hand it off without re-listing
        which keys are canonical.
        """
        core = cls.model_validate(data)
        extras = {k: v for k, v in data.items() if k not in cls.model_fields}
        return core, extras
