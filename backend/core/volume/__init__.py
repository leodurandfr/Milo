# backend/core/volume/__init__.py
"""
Volume management module for system-wide volume control.

This module provides volume management with support for both direct mode
(single local CamillaDSP) and multiroom mode (multiple Snapcast clients).

Usage:
    from backend.core.volume import VolumeService, router, setup_volume_routes

    # Create service
    service = VolumeService(event_bus, state_machine, snapcast_service, ...)

    # Include router in FastAPI app
    setup_volume_routes(service)
    app.include_router(router, prefix="/api")
"""
from backend.core.volume.service import VolumeService
from backend.core.volume.state import VolumeStateStore
from backend.core.volume.config import VolumeConfigService
from backend.core.volume.equalizer_controller import EqualizerController
from backend.core.volume.routes import router, setup_volume_routes, create_volume_router

__all__ = [
    "VolumeService",
    "VolumeStateStore",
    "VolumeConfigService",
    "EqualizerController",
    "router",
    "setup_volume_routes",
    "create_volume_router"
]
