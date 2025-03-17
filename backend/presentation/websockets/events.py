"""
Gestion des événements WebSocket.
"""
import logging
from typing import Dict, Any, Callable
from backend.application.event_bus import EventBus
from backend.presentation.websockets.manager import WebSocketManager


class WebSocketEventHandler:
    """Relais entre le bus d'événements et les WebSockets"""
    
    def __init__(self, event_bus: EventBus, ws_manager: WebSocketManager):
        self.event_bus = event_bus
        self.ws_manager = ws_manager
        self.logger = logging.getLogger(__name__)
        self.event_mappings: Dict[str, str] = {
            # Mapping entre événements internes et événements WebSocket
            "audio_state_changing": "audio_state_changing",
            "audio_state_changed": "audio_state_changed",
            "audio_transition_error": "audio_error",
            "volume_changed": "volume_changed",
            "audio_metadata_updated": "audio_metadata_updated",
            "audio_status_updated": "audio_status_updated"
        }
        self._register_handlers()
    
    def _register_handlers(self) -> None:
        """Enregistre les handlers pour les événements internes"""
        for internal_event, ws_event in self.event_mappings.items():
            self.event_bus.subscribe(
                internal_event,
                self._create_event_handler(ws_event, internal_event)
            )
    
    def _create_event_handler(self, ws_event: str, internal_event: str) -> Callable:
        """Crée un handler pour un type d'événement spécifique"""
        async def handler(data: Dict[str, Any]) -> None:
            try:
                self.logger.debug(f"Événement {internal_event} reçu: {data}")
                
                # Pour les mises à jour de métadonnées, ajoutons des logs détaillés
                if internal_event == "audio_metadata_updated":
                    self.logger.info(f"Métadonnées mises à jour: source={data.get('source')}, "
                                    f"titre={data.get('metadata', {}).get('title')}, "
                                    f"artiste={data.get('metadata', {}).get('artist')}")
                
                await self.ws_manager.broadcast(ws_event, data)
            except Exception as e:
                self.logger.error(f"Erreur lors de la diffusion de l'événement {ws_event}: {e}")
        return handler