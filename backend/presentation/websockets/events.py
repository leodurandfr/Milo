# backend/presentation/websockets/events.py
"""
Gestion des événements WebSocket - Version unifiée standardisée
"""
import logging
import time
from typing import Dict, Any, Callable
from backend.application.event_bus import EventBus
from backend.presentation.websockets.manager import WebSocketManager
from backend.domain.events import StandardEvent, EventCategory, EventType

class WebSocketEventHandler:
    """Relais entre le bus d'événements et les WebSockets - Version standardisée"""
    
    def __init__(self, event_bus: EventBus, ws_manager: WebSocketManager):
        self.event_bus = event_bus
        self.ws_manager = ws_manager
        self.logger = logging.getLogger(__name__)
        self._register_handlers()
    
    def _register_handlers(self) -> None:
        """Enregistre les handlers pour les événements"""
        # Nouveaux handlers pour événements standardisés
        self.event_bus.subscribe_to_category(EventCategory.SYSTEM, self._handle_standard_event)
        self.event_bus.subscribe_to_category(EventCategory.PLUGIN, self._handle_standard_event)
        self.event_bus.subscribe_to_category(EventCategory.AUDIO, self._handle_standard_event)
        
        # Anciens handlers pour compatibilité
        legacy_events = [
            "audio.transition_started",
            "audio.transition_completed",
            "audio.transition_error",
            "audio.plugin_state_changed",
            "audio.volume_changed",
            "audio.state_sync"
        ]
        
        for event in legacy_events:
            self.event_bus.subscribe(event, self._handle_legacy_event)
    
    async def _handle_standard_event(self, event: StandardEvent) -> None:
        """Traite et diffuse un événement standardisé"""
        try:
            await self.ws_manager.broadcast_standard(event)
            self.logger.debug(f"Standard event broadcast: {event.category.value}.{event.type.value}")
        except Exception as e:
            self.logger.error(f"Error broadcasting standard event: {e}")
    
    async def _handle_legacy_event(self, data: Dict[str, Any]) -> None:
        """Traite les événements legacy pour compatibilité"""
        try:
            if 'timestamp' not in data:
                data['timestamp'] = time.time()
            
            await self.ws_manager.broadcast("state_update", data)
            self.logger.debug(f"Legacy event broadcast: state_update")
        except Exception as e:
            self.logger.error(f"Error broadcasting legacy event: {e}")
    
    async def broadcast_full_state(self, state: Dict[str, Any]) -> None:
        """Diffuse l'état complet du système"""
        try:
            event = StandardEvent(
                category=EventCategory.SYSTEM,
                type=EventType.STATE_CHANGED,
                source="system",
                data={"full_state": state}
            )
            await self.ws_manager.broadcast_standard(event)
        except Exception as e:
            self.logger.error(f"Error broadcasting full state: {e}")