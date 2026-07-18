# backend/sources/music_library/__init__.py
"""Music Library audio source (Family C — active player).

Plays the user's own music (USB key in Phase 1, SMB/NFS shares in Phase 2),
indexed by a Navidrome sidecar and streamed to mpv over localhost HTTP — the
same shape as the Podcast source, with Navidrome standing in for Podcast Index.

Phase 0 status: the source skeleton exists — it registers and appears in the
dock (placeholder status card, no audio). The ALSA trio and
milo-music-library.service ship in P0-2; the Navidrome Subsonic client (P0-3)
lands here too. REST routes, browse UI and playback wiring follow in later
phases. Full plan: docs/plans/music-library.md.

Usage:
    from backend.sources.music_library import MusicLibrarySource, NavidromeClient

    source = MusicLibrarySource(config=config, state_machine=state_machine)
    client = NavidromeClient.from_cred_file()
"""
from backend.sources.music_library.navidrome_client import (
    NavidromeAuthError,
    NavidromeClient,
    load_navidrome_credentials,
)
from backend.sources.music_library.source import MusicLibrarySource

__all__ = [
    "MusicLibrarySource",
    "NavidromeClient",
    "NavidromeAuthError",
    "load_navidrome_credentials",
]
