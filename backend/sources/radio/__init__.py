# backend/sources/radio/__init__.py
"""
Radio audio source feature using MPV.

This module provides internet radio streaming via MPV player
with RadioBrowser API integration, station management, and
playback control.

Usage:
    from backend.sources.radio import RadioSource, router

    # Create source
    source = RadioSource(config=config, state_machine=state_machine)

    # Include router in FastAPI app
    app.include_router(router, prefix="/api")
"""
from backend.sources.radio.source import RadioSource
from backend.sources.radio.routes import router, setup_radio_routes
from backend.sources.radio.data import StationDataService, ImageManager
from backend.sources.radio.models import (
    PlayStationRequest,
    FavoriteRequest,
    StationSearchResult
)

__all__ = [
    "RadioSource",
    "router",
    "setup_radio_routes",
    "StationDataService",
    "ImageManager",
    "PlayStationRequest",
    "FavoriteRequest",
    "StationSearchResult"
]
