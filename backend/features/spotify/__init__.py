# backend/features/spotify/__init__.py
"""
Spotify audio source feature using go-librespot.

This module provides Spotify Connect streaming via go-librespot
with real-time WebSocket events and playback control.
"""
from backend.features.spotify.source import SpotifySource
from backend.features.spotify.websocket import LibrespotWebSocket

__all__ = [
    "SpotifySource",
    "LibrespotWebSocket"
]
