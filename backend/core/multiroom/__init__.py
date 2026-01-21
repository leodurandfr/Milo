# backend/core/multiroom/__init__.py
"""
Multiroom module for Milo audio system.

This module provides:
- ClientRegistryService: Central registry for clients and zones
- SnapcastService: REST commands to Snapcast server
- SnapcastWebSocketService: WebSocket notifications from Snapcast
- CrossoverService: Speaker type and crossover management
- Routes: FastAPI router for multiroom API
"""

from backend.core.multiroom.models import (
    Client,
    Zone,
    DspSettings,
    RegistryState,
    RegistryEventType,
    SpeakerType,
    SPEAKER_TYPES,
    DEFAULT_SPEAKER_TYPE,
    DEFAULT_CROSSOVER_FREQUENCIES,
    DEFAULT_VOLUME_DB,
)
from backend.core.multiroom.registry import ClientRegistryService
from backend.core.multiroom.snapcast import (
    SnapcastService,
    get_available_clients,
    get_available_client_ids,
)
from backend.core.multiroom.websocket import SnapcastWebSocketService
from backend.core.multiroom.crossover import CrossoverService
from backend.core.multiroom.routing import AudioRoutingService
from backend.core.multiroom.routes import (
    router,
    create_snapcast_router,
    setup_multiroom_routes,
)

__all__ = [
    # Models
    "Client",
    "Zone",
    "DspSettings",
    "RegistryState",
    "RegistryEventType",
    "SpeakerType",
    "SPEAKER_TYPES",
    "DEFAULT_SPEAKER_TYPE",
    "DEFAULT_CROSSOVER_FREQUENCIES",
    "DEFAULT_VOLUME_DB",
    # Services
    "ClientRegistryService",
    "SnapcastService",
    "SnapcastWebSocketService",
    "CrossoverService",
    "AudioRoutingService",
    # Helpers
    "get_available_clients",
    "get_available_client_ids",
    # Routes
    "router",
    "create_snapcast_router",
    "setup_multiroom_routes",
]
