"""
file: backend/presentation/websockets/manager.py
Gestion des connexions WebSocket.
"""
from typing import Dict, Any, List, Set
import logging
from fastapi import WebSocket, WebSocketDisconnect
import json


class WebSocketManager:
    """Gestionnaire de connexions WebSocket"""
    
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self.logger = logging.getLogger(__name__)
    
    async def connect(self, websocket: WebSocket) -> None:
        """Établit une connexion WebSocket"""
        try:
            await websocket.accept()
            self.active_connections.add(websocket)
            self.logger.info(f"WebSocket connected, active connections: {len(self.active_connections)}")
        except Exception as e:
            self.logger.error(f"Error accepting WebSocket connection: {str(e)}")
            raise
    
    def disconnect(self, websocket: WebSocket) -> None:
        """Ferme une connexion WebSocket"""
        self.active_connections.remove(websocket)
        self.logger.info(f"WebSocket disconnected, active connections: {len(self.active_connections)}")
    
    async def broadcast(self, event_type: str, data: Dict[str, Any]) -> None:
        """Diffuse un message à toutes les connexions actives"""
        message = {
            "type": event_type,
            "data": data
        }
        message_str = json.dumps(message)
        
        # Log différent selon le type d'événement
        if event_type.startswith('snapclient_'):
            self.logger.info(f"⚡ Diffusion WebSocket: {event_type} à {len(self.active_connections)} clients")
        else:
            self.logger.debug(f"Diffusion WebSocket: {event_type} à {len(self.active_connections)} clients")
        
        disconnected = set()
        for connection in self.active_connections:
            try:
                await connection.send_text(message_str)
            except Exception as e:
                self.logger.error(f"Erreur lors de l'envoi du message: {str(e)}")
                disconnected.add(connection)
        
        # Retirer les connexions déconnectées
        for connection in disconnected:
            self.disconnect(connection)
            
    async def cleanup_stale_connections(self):
        """Nettoie les connexions inactives ou zombies."""
        original_count = len(self.active_connections)
        
        to_remove = set()
        for conn in self.active_connections:
            try:
                # Envoyer un ping pour vérifier si la connexion est toujours active
                await conn.send_text(json.dumps({"type": "ping", "data": {"timestamp": time.time()}}))
            except Exception:
                # Si on ne peut pas envoyer, la connexion est morte
                to_remove.add(conn)
        
        # Supprimer les connexions mortes
        for conn in to_remove:
            self.disconnect(conn)
        
        removed = original_count - len(self.active_connections)
        if removed > 0:
            self.logger.warning(f"🧹 Nettoyage: {removed} connexions zombies supprimées, {len(self.active_connections)} actives")
        
        return removed