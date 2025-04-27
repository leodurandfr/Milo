"""
Gestion des connexions WebSocket - Version OPTIM
"""
from typing import Set
import logging
import json
from fastapi import WebSocket
from backend.domain.events import StandardEvent

class WebSocketManager:
    """Gestionnaire de connexions WebSocket simplifié"""
    
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self.logger = logging.getLogger(__name__)
    
    async def connect(self, websocket: WebSocket) -> None:
        """Établit une connexion WebSocket"""
        await websocket.accept()
        self.active_connections.add(websocket)
        self.logger.info(f"WebSocket connected, total: {len(self.active_connections)}")
    
    def disconnect(self, websocket: WebSocket) -> None:
        """Ferme une connexion WebSocket"""
        self.active_connections.discard(websocket)
        self.logger.info(f"WebSocket disconnected, total: {len(self.active_connections)}")
    
    async def broadcast(self, event: StandardEvent) -> None:
        """Diffuse un événement standardisé à toutes les connexions"""
        if not self.active_connections:
            self.logger.debug("No active connections to broadcast to")
            return
        
        message = json.dumps(event.to_dict())
        self.logger.info(f"Broadcasting event: {event.category.value}.{event.type.value} to {len(self.active_connections)} clients")
        
        disconnected = set()
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception as e:
                self.logger.warning(f"Failed to send to client: {e}")
                disconnected.add(connection)
        
        # Nettoyer les connexions mortes
        self.active_connections -= disconnected