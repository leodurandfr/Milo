# backend/presentation/websockets/events.py
"""
Gestion des événements WebSocket - Version unifiée standardisée
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
        
        # Mapping complet des événements unifiés
        self.event_mappings = {
            # Événements d'état global
            "audio.transition_started": "state_update",
            "audio.transition_completed": "state_update", 
            "audio.transition_error": "state_update",
            "audio.plugin_state_changed": "state_update",
            
            # Événements de volume (pour plus tard)
            "audio.volume_changed": "state_update",
            
            # Événement de synchronisation complet
            "audio.state_sync": "state_update"
        }
        
        self._register_handlers()
    
    def _register_handlers(self) -> None:
        """Enregistre les handlers pour les événements internes"""
        # Un seul handler pour tous les événements
        for internal_event in self.event_mappings.keys():
            self.event_bus.subscribe(
                internal_event,
                self._handle_state_update
            )
    
    async def _handle_state_update(self, data: Dict[str, Any]) -> None:
        """Traite tous les événements en diffusant l'état complet"""
        try:
            # Ajouter un timestamp
            if 'timestamp' not in data:
                data['timestamp'] = time.time()
            
            # Diffuser l'état complet
            await self.ws_manager.broadcast("state_update", data)
            
            self.logger.debug(f"État diffusé: {data.get('full_state', {}).get('active_source')}")
                    
        except Exception as e:
            self.logger.error(f"Erreur lors de la diffusion de l'état: {e}")
    
    async def broadcast_full_state(self, state: Dict[str, Any]) -> None:
        """Diffuse l'état complet du système"""
        try:
            await self.ws_manager.broadcast("state_update", {
                "event_type": "full_sync",
                "full_state": state,
                "timestamp": time.time()
            })
        except Exception as e:
            self.logger.error(f"Erreur lors de la diffusion de l'état complet: {e}")