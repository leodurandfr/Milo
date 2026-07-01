# backend/sources/dlna/__init__.py
"""
DLNA / UPnP Media Renderer (DMR) audio source using gmediarender.

Milō appears as a DLNA renderer; a local UPnP control-point bridge feeds
metadata (title/artist/album/artwork/state/position) from the renderer. Passive
player (Family B) — playback is controlled by the external sender.

Usage:
    from backend.sources.dlna import DlnaSource, router

    source = DlnaSource(config=config, state_machine=state_machine)
    app.include_router(router, prefix="/api")
"""
from backend.sources.dlna.source import DlnaSource
from backend.sources.dlna.routes import router, setup_dlna_routes

__all__ = [
    "DlnaSource",
    "router",
    "setup_dlna_routes",
]
