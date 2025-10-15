# backend/presentation/websockets/manager.py
"""
Gestion des connexions WebSocket - Version OPTIM avec broadcast parallèle
"""
from typing import Set, Dict, Any
import logging
import json
import asyncio
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
        """Diffuse un événement à toutes les connexions en parallèle avec timeout"""
        if not self.active_connections:
            self.logger.debug("No active connections to broadcast to")
            return

        message = json.dumps(event_data)
        category = event_data.get("category", "unknown")
        event_type = event_data.get("type", "unknown")

        # self.logger.info(f"Broadcasting event: {category}.{event_type} to {len(self.active_connections)} clients")

        async def send_to_client(connection: WebSocket):
            """Envoie un message à un client avec timeout"""
            try:
                await asyncio.wait_for(connection.send_text(message), timeout=1.0)
                return connection, None
            except asyncio.TimeoutError:
                self.logger.warning(f"Timeout sending to client (>1s)")
                return connection, "timeout"
            except Exception as e:
                self.logger.warning(f"Failed to send to client: {e}")
                return connection, str(e)

        # Envoi parallèle à tous les clients
        results = await asyncio.gather(
            *[send_to_client(conn) for conn in self.active_connections],
            return_exceptions=True
        )

        # Nettoyer les connexions en erreur
        disconnected = set()
        for result in results:
            if isinstance(result, tuple):
                connection, error = result
                if error:
                    disconnected.add(connection)

        if disconnected:
            self.active_connections -= disconnected
            self.logger.info(f"Removed {len(disconnected)} dead connection(s)")