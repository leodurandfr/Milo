# backend/presentation/websockets/manager.py
"""
Gestion des connexions WebSocket - Version OPTIM avec support dict
"""
from typing import Set, Dict, Any
import logging
import json
from fastapi import WebSocket

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
    
    async def broadcast_dict(self, event_data: Dict[str, Any]) -> None:
        """Diffuse un dictionnaire d'événement à toutes les connexions - Version OPTIM"""
        if not self.active_connections:
            self.logger.debug("No active connections to broadcast to")
            return
        
        message = json.dumps(event_data)
        category = event_data.get("category", "unknown")
        event_type = event_data.get("type", "unknown")
        
        self.logger.info(f"Broadcasting event: {category}.{event_type} to {len(self.active_connections)} clients")
        
        disconnected = set()
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception as e:
                self.logger.warning(f"Failed to send to client: {e}")
                disconnected.add(connection)
        
        # Nettoyer les connexions mortes
        self.active_connections -= disconnected