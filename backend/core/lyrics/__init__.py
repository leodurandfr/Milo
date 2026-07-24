# backend/core/lyrics/__init__.py
"""Lyrics resolution (transverse Lyrics app)."""
from backend.core.lyrics.service import LyricsService, LyricsUnavailable

__all__ = ["LyricsService", "LyricsUnavailable"]
