"""
Gestion des événements WebSocket - Version OPTIM avec uniquement les événements standardisés
"""
import logging
from backend.application.event_bus import EventBus
from backend.presentation.websockets.manager import WebSocketManager
from backend.domain.events import StandardEvent, EventCategory

class WebSocketEventHandler:
    """Relais entre le bus d'événements et les WebSockets - Version simplifiée"""
    
    def __init__(self, event_bus: EventBus, ws_manager: WebSocketManager):
        self.event_bus = event_bus
        self.ws_manager = ws_manager
        self.logger = logging.getLogger(__name__)
        
        # Abonnement direct aux catégories d'événements
        event_bus.subscribe_to_category(EventCategory.SYSTEM, self._handle_event)
        event_bus.subscribe_to_category(EventCategory.PLUGIN, self._handle_event)
        event_bus.subscribe_to_category(EventCategory.AUDIO, self._handle_event)
    
    async def _handle_event(self, event: StandardEvent) -> None:
        """Diffuse un événement standardisé"""
        try:
            await self.ws_manager.broadcast(event)
        except Exception as e:
            self.logger.error(f"Error broadcasting event: {e}")