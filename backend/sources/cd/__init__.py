# backend/sources/cd/__init__.py
"""CD audio source via direct ioctl sector reading (Family C — active player).

Playback from a USB CD drive (e.g. Apple SuperDrive) with automatic disc
detection, MusicBrainz metadata + cover art, and instant playback via
CDROMREADAUDIO ioctl piped into mpv through a FIFO.
"""
from backend.sources.cd.source import CdSource

__all__ = ["CdSource"]
