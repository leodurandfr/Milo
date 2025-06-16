# backend/presentation/websockets/events.py
"""
Gestion des événements WebSocket - Version OPTIM simplifiée
"""
import logging
from typing import Dict, Any
from backend.presentation.websockets.manager import WebSocketManager

class WebSocketEventHandler:
    """Gestionnaire d'événements WebSocket simplifié - Reçoit directement des dicts"""
    
    def __init__(self, ws_manager: WebSocketManager):
        self.ws_manager = ws_manager
        self.logger = logging.getLogger(__name__)
    
    async def handle_event(self, event_data: Dict[str, Any]) -> None:
        """Traite et diffuse un événement - Version OPTIM"""
        try:
            await self.ws_manager.broadcast_dict(event_data)
        except Exception as e:
            self.logger.error(f"Error broadcasting event: {e}")