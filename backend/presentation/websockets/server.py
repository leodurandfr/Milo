# backend/presentation/websockets/server.py
"""
Serveur WebSocket - Version OPTIM
"""
import json
from fastapi import WebSocket, WebSocketDisconnect
from backend.presentation.websockets.manager import WebSocketManager
from backend.domain.events import StandardEvent, EventCategory, EventType

class WebSocketServer:
    """Serveur WebSocket simplifié"""
    
    def __init__(self, ws_manager: WebSocketManager, state_machine):
        self.manager = ws_manager
        self.state_machine = state_machine
    
    async def websocket_endpoint(self, websocket: WebSocket):
        """Point d'entrée WebSocket"""
        await self.manager.connect(websocket)
        
        try:
            # Envoyer l'état initial
            current_state = await self.state_machine.get_current_state()
            initial_event = StandardEvent(
                category=EventCategory.SYSTEM,
                type=EventType.STATE_CHANGED,
                source="system",
                data={"full_state": current_state}
            )
            await websocket.send_text(json.dumps(initial_event.to_dict()))
            
            # Maintenir la connexion ouverte
            while True:
                await websocket.receive_text()
                
        except WebSocketDisconnect:
            self.manager.disconnect(websocket)