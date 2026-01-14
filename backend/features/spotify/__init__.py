# backend/features/spotify/__init__.py
"""
Spotify audio source feature using go-librespot.

This module provides Spotify Connect streaming via go-librespot
with real-time WebSocket events and playback control.

Usage:
    from backend.features.spotify import SpotifySource, router

    # Create source
    source = SpotifySource(event_bus, config)

    # Include router in FastAPI app
    app.include_router(router, prefix="/api")
"""
from backend.features.spotify.source import SpotifySource
from backend.features.spotify.routes import router, setup_spotify_routes
from backend.features.spotify.websocket import LibrespotWebSocket

__all__ = [
    "SpotifySource",
    "router",
    "setup_spotify_routes",
    "LibrespotWebSocket"
]
