# backend/features/radio/__init__.py
"""
Radio audio source feature using MPV.

This module provides internet radio streaming via MPV player
with RadioBrowser API integration, station management, and
playback control.

Usage:
    from backend.features.radio import RadioSource, router

    # Create source
    source = RadioSource(event_bus, config)

    # Include router in FastAPI app
    app.include_router(router, prefix="/api")
"""
from backend.features.radio.source import RadioSource
from backend.features.radio.routes import router, setup_radio_routes
from backend.features.radio.data import StationDataService, ImageManager
from backend.features.radio.models import (
    Station,
    PlayStationRequest,
    FavoriteRequest,
    MarkBrokenRequest,
    AddCustomStationRequest,
    RemoveCustomStationRequest,
    StationSearchResult
)

__all__ = [
    "RadioSource",
    "router",
    "setup_radio_routes",
    "StationDataService",
    "ImageManager",
    "Station",
    "PlayStationRequest",
    "FavoriteRequest",
    "MarkBrokenRequest",
    "AddCustomStationRequest",
    "RemoveCustomStationRequest",
    "StationSearchResult"
]
