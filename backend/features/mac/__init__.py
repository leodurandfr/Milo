# backend/features/mac/__init__.py
"""
Mac audio source feature using ROC toolkit.

This module provides streaming audio from Mac computers via ROC
(Roc Opus Codec) with support for multiple simultaneous connections.

Usage:
    from backend.features.mac import MacSource, router

    # Create source
    source = MacSource(config=config, state_machine=state_machine)

    # Include router in FastAPI app
    app.include_router(router, prefix="/api")
"""
from backend.features.mac.source import MacSource
from backend.features.mac.routes import router, setup_mac_routes

__all__ = ["MacSource", "router", "setup_mac_routes"]
