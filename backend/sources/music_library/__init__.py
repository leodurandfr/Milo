# backend/sources/music_library/__init__.py
"""Music Library audio source (Family C — active player).

Plays the user's own music from a USB key or an SMB/NFS share, indexed by a
Navidrome sidecar and streamed to mpv over localhost HTTP — the same shape as
the Podcast source, with Navidrome standing in for Podcast Index.
"""
from backend.sources.music_library.source import MusicLibrarySource

__all__ = ["MusicLibrarySource"]
