# backend/sources/cd/__init__.py
"""
CD audio source feature using MPV.

This module provides CD playback via a USB CD drive (e.g., Apple SuperDrive)
with automatic disc detection, MusicBrainz metadata lookup, cover art,
and track navigation via mpv's cdda:// protocol.

Usage:
    from backend.sources.cd import CdSource, router

    # Create source
    source = CdSource(config=config, state_machine=state_machine)

    # Include router in FastAPI app
    app.include_router(router, prefix="/api")
"""
from backend.sources.cd.source import CdSource
from backend.sources.cd.routes import router, setup_cd_routes
from backend.sources.cd.data import CdDataService
from backend.sources.cd.models import (
    PlayTrackRequest,
    SeekRequest,
    DiscInfo,
    TrackInfo,
)

__all__ = [
    "CdSource",
    "router",
    "setup_cd_routes",
    "CdDataService",
    "PlayTrackRequest",
    "SeekRequest",
    "DiscInfo",
    "TrackInfo",
]
