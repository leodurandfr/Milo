# backend/sources/dlna/__init__.py
"""DLNA / UPnP Media Renderer (DMR) via gmediarender (Family B — passive player).

Milō appears as a DLNA renderer; a local UPnP control-point bridge
(metadata_reader.py) feeds title/artist/album/artwork/state/position back from
the renderer. Playback is controlled by the external sender.
"""
from backend.sources.dlna.source import DlnaSource

__all__ = ["DlnaSource"]
