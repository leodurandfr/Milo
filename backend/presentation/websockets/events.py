# backend/presentation/websockets/events.py
"""
WebSocket event handling
"""
import logging
from typing import Dict, Any
from backend.presentation.websockets.manager import WebSocketManager

class WebSocketEventHandler:
    """WebSocket event handler - Receives dict objects directly"""

    def __init__(self, ws_manager: WebSocketManager):
        self.ws_manager = ws_manager
        self.logger = logging.getLogger(__name__)

    async def handle_event(self, event_data: Dict[str, Any]) -> None:
        """Processes and broadcasts an event"""
        try:
            await self.ws_manager.broadcast_dict(event_data)
        except Exception as e:
            self.logger.error(f"Error broadcasting event: {e}")