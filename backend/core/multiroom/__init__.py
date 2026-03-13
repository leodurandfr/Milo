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
    EqualizerSettings,
    RegistryState,
    RegistryEventType,
    SpeakerType,
    SPEAKER_TYPES,
    DEFAULT_SPEAKER_TYPE,
    DEFAULT_CROSSOVER_FREQUENCIES,
    DEFAULT_VOLUME_DB,
)
from backend.core.multiroom.client_registry import ClientRegistryService
from backend.core.multiroom.pending_clients import PendingClientsService
from backend.core.multiroom.snapcast import (
    SnapcastService,
    get_online_clients,
    get_online_client_ids,
)
from backend.core.multiroom.websocket import SnapcastWebSocketService
from backend.core.multiroom.crossover import CrossoverService
from backend.core.multiroom.routing import AudioRoutingService
from backend.core.multiroom.routes import create_snapcast_router

__all__ = [
    # Models
    "Client",
    "Zone",
    "EqualizerSettings",
    "RegistryState",
    "RegistryEventType",
    "SpeakerType",
    "SPEAKER_TYPES",
    "DEFAULT_SPEAKER_TYPE",
    "DEFAULT_CROSSOVER_FREQUENCIES",
    "DEFAULT_VOLUME_DB",
    # Services
    "ClientRegistryService",
    "PendingClientsService",
    "SnapcastService",
    "SnapcastWebSocketService",
    "CrossoverService",
    "AudioRoutingService",
    # Helpers
    "get_online_clients",
    "get_online_client_ids",
    # Routes
    "create_snapcast_router",
]
