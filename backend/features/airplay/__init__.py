# backend/features/airplay/__init__.py
"""
AirPlay 2 audio source feature using shairport-sync.

This module provides AirPlay 2 streaming via shairport-sync
with real-time metadata parsing and artwork handling.

Usage:
    from backend.features.airplay import AirPlaySource, router

    # Create source
    source = AirPlaySource(config=config, state_machine=state_machine)

    # Include router in FastAPI app
    app.include_router(router, prefix="/api")
"""
from backend.features.airplay.source import AirPlaySource
from backend.features.airplay.routes import router, setup_airplay_routes

__all__ = [
    "AirPlaySource",
    "router",
    "setup_airplay_routes",
]
