"""
Serveur WebSocket - Version simplifiée
"""
from fastapi import WebSocket, WebSocketDisconnect
from backend.presentation.websockets.manager import WebSocketManager
from backend.application.event_bus import EventBus


class WebSocketServer:
    """Serveur WebSocket pour les connexions clients"""
    
    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.manager = WebSocketManager()
    
    async def websocket_endpoint(self, websocket: WebSocket):
        """Point d'entrée WebSocket"""
        await self.manager.connect(websocket)
        try:
            while True:
                # On attend juste les déconnexions
                # Les messages sortants sont gérés par events.py
                await websocket.receive_text()
        except WebSocketDisconnect:
            self.manager.disconnect(websocket)