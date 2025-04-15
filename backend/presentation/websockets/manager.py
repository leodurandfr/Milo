"""
file: backend/presentation/websockets/manager.py
Gestion des connexions WebSocket.
"""
from typing import Dict, Any, List, Set
import logging
import time  # Ajouter l'import manquant
import json
from fastapi import WebSocket, WebSocketDisconnect


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
        try:
            self.active_connections.remove(websocket)
            self.logger.info(f"WebSocket disconnected, active connections: {len(self.active_connections)}")
        except (KeyError, ValueError):
            # La connexion peut avoir déjà été supprimée ou n'était pas dans la liste
            self.logger.warning(f"Tried to disconnect a WebSocket that was not in active connections")
            
    async def broadcast(self, event_type: str, data: Dict[str, Any]) -> None:
        """Diffuse un message à toutes les connexions actives"""
        message = {
            "type": event_type,
            "data": data
        }
        message_str = json.dumps(message)
        
        # Log différent selon le type d'événement
        if event_type in ['snapclient_monitor_disconnected', 'snapclient_server_disappeared']:
            self.logger.info(f"⚡ ENVOI ÉVÉNEMENT WEBSOCKET CRITIQUE: {event_type} pour {data.get('host', 'unknown')}")
            
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