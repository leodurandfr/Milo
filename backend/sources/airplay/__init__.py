# backend/sources/airplay/__init__.py
"""AirPlay 2 audio source via shairport-sync (Family B — passive player, with
the sender's own transport).

The session off shairport-sync's metadata pipe, the sender's item, commands and
volume over its D-Bus object, and the cover it writes as a file — served by
routes.py, since the frontend can't read either.
"""
from backend.sources.airplay.source import AirPlaySource

__all__ = ["AirPlaySource"]
