# backend/presentation/websockets/events.py
"""
Gestion des événements WebSocket - Version unifiée
"""
import logging
import time
from typing import Dict, Any, Callable
from backend.application.event_bus import EventBus
from backend.presentation.websockets.manager import WebSocketManager


class WebSocketEventHandler:
    """Relais entre le bus d'événements et les WebSockets"""
    
    def __init__(self, event_bus: EventBus, ws_manager: WebSocketManager):
        self.event_bus = event_bus
        self.ws_manager = ws_manager
        self.logger = logging.getLogger(__name__)
        
        # Mapping des événements unifiés
        self.event_mappings = {
            # Événements d'état global
            "audio.transition_started": "transition_started",
            "audio.transition_completed": "transition_completed",
            "audio.transition_error": "transition_error",
            "audio.plugin_state_changed": "plugin_state_changed",
            
            # Événements de volume (pour plus tard)
            "audio.volume_changed": "volume_changed",
        }
        
        self._register_handlers()
    
    def _register_handlers(self) -> None:
        """Enregistre les handlers pour les événements internes"""
        for internal_event, ws_event in self.event_mappings.items():
            self.event_bus.subscribe(
                internal_event,
                self._create_event_handler(ws_event)
            )
    
    def _create_event_handler(self, ws_event: str) -> Callable:
        """Crée un handler pour un type d'événement spécifique"""
        async def handler(data: Dict[str, Any]) -> None:
            try:
                # Ajouter un timestamp si non présent
                if 'timestamp' not in data:
                    data['timestamp'] = time.time()
                
                # Diffuser l'événement
                await self.ws_manager.broadcast(ws_event, data)
                
                # Logger sélectivement
                if ws_event in ["transition_error", "plugin_state_changed"]:
                    self.logger.info(f"⚡ {ws_event}: {data}")
                else:
                    self.logger.debug(f"Événement {ws_event} diffusé: {data}")
                    
            except Exception as e:
                self.logger.error(f"Erreur lors de la diffusion de l'événement {ws_event}: {e}")
        
        return handler