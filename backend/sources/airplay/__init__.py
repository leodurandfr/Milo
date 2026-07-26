# backend/sources/airplay/__init__.py
"""AirPlay 2 audio source via shairport-sync (Family B — passive player).

Real-time metadata parsing off shairport-sync's metadata pipe, plus the binary
artwork the sender pushes as PICT chunks — served by routes.py, since the
frontend can't read the pipe itself.
"""
from backend.sources.airplay.source import AirPlaySource

__all__ = ["AirPlaySource"]
