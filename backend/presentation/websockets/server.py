# backend/presentation/websockets/server.py
"""
Serveur WebSocket - Version standardisée
"""
import time
from fastapi import WebSocket, WebSocketDisconnect
from backend.presentation.websockets.manager import WebSocketManager
from backend.application.event_bus import EventBus


class WebSocketServer:
    """Serveur WebSocket pour les connexions clients"""
    
    def __init__(self, event_bus: EventBus, state_machine=None):
        self.event_bus = event_bus
        self.manager = WebSocketManager()
        self.state_machine = state_machine
    
    async def websocket_endpoint(self, websocket: WebSocket):
        """Point d'entrée WebSocket"""
        await self.manager.connect(websocket)
        
        try:
            # Envoyer l'état initial à la connexion
            if self.state_machine:
                current_state = await self.state_machine.get_current_state()
                await websocket.send_json({
                    "type": "state_update",
                    "data": {
                        "event_type": "initial_sync",
                        "full_state": current_state,
                        "timestamp": time.time()
                    }
                })
            
            # Boucle de réception (on garde la connexion ouverte)
            while True:
                # On attend juste les déconnexions
                await websocket.receive_text()
                
        except WebSocketDisconnect:
            self.manager.disconnect(websocket)