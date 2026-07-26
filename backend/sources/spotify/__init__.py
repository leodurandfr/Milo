# backend/sources/spotify/__init__.py
"""Spotify Connect audio source via go-librespot (Family C — active player).

Playback is driven from Milō's UI through go-librespot's local HTTP API, and
its WebSocket feed (websocket.py) pushes track/state changes in real time.
"""
from backend.sources.spotify.source import SpotifySource

__all__ = ["SpotifySource"]
