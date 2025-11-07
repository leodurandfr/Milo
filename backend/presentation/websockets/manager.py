# backend/presentation/websockets/manager.py
"""
WebSocket connection management with parallel broadcast
"""
from typing import Set, Dict, Any
import logging
import json
import asyncio
from fastapi import WebSocket

class WebSocketManager:
    """WebSocket connection manager"""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self.logger = logging.getLogger(__name__)

    async def connect(self, websocket: WebSocket) -> None:
        """Establishes WebSocket connection"""
        await websocket.accept()
        self.active_connections.add(websocket)
        self.logger.info(f"WebSocket connected, total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket) -> None:
        """Closes WebSocket connection"""
        self.active_connections.discard(websocket)
        self.logger.info(f"WebSocket disconnected, total: {len(self.active_connections)}")

    async def broadcast_dict(self, event_data: Dict[str, Any]) -> None:
        """Broadcasts event to all connections in parallel with timeout"""
        if not self.active_connections:
            self.logger.debug("No active connections to broadcast to")
            return

        message = json.dumps(event_data)
        category = event_data.get("category", "unknown")
        event_type = event_data.get("type", "unknown")

        async def send_to_client(connection: WebSocket):
            """Sends message to client with timeout"""
            try:
                await asyncio.wait_for(connection.send_text(message), timeout=1.0)
                return connection, None
            except asyncio.TimeoutError:
                self.logger.warning(f"Timeout sending to client (>1s)")
                return connection, "timeout"
            except Exception as e:
                self.logger.warning(f"Failed to send to client: {e}")
                return connection, str(e)

        results = await asyncio.gather(
            *[send_to_client(conn) for conn in self.active_connections],
            return_exceptions=True
        )

        disconnected = set()
        for result in results:
            if isinstance(result, tuple):
                connection, error = result
                if error:
                    disconnected.add(connection)

        if disconnected:
            self.active_connections -= disconnected
            self.logger.info(f"Removed {len(disconnected)} dead connection(s)")