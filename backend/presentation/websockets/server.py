# backend/presentation/websockets/server.py
"""
WebSocket server with fresh initial state and ping/pong
"""
import json
import asyncio
import time
from fastapi import WebSocket, WebSocketDisconnect
from backend.presentation.websockets.manager import WebSocketManager
from backend.domain.audio_state import AudioSource

class WebSocketServer:
    """WebSocket server with correct initial state and heartbeat"""

    PING_INTERVAL = 30

    def __init__(self, ws_manager: WebSocketManager, state_machine):
        self.manager = ws_manager
        self.state_machine = state_machine

    async def _send_ping(self, websocket: WebSocket):
        """Sends periodic pings to maintain connection"""
        while True:
            try:
                await asyncio.sleep(self.PING_INTERVAL)
                ping_message = {
                    "category": "system",
                    "type": "ping",
                    "timestamp": time.time()
                }
                await websocket.send_text(json.dumps(ping_message))
            except Exception:
                break

    async def websocket_endpoint(self, websocket: WebSocket):
        """WebSocket entry point with client-ready handshake.

        Flow:
        1. Client connects and registers event listeners
        2. Client sends {"type": "ready"} when ready to receive state
        3. Server responds with initial_state containing full_state
        4. Server continues broadcasting real-time updates

        This handshake prevents race conditions where WebSocket state arrives
        before Vue components register their event listeners.
        """
        await self.manager.connect(websocket)

        ping_task = asyncio.create_task(self._send_ping(websocket))

        try:
            # Wait for client ready signal
            message = await websocket.receive_text()
            client_msg = json.loads(message)

            if client_msg.get("type") == "ready":
                # Refresh active plugin metadata for fresh position data
                await self.state_machine.refresh_active_metadata()
                # Client is ready - send initial state
                current_state = await self.state_machine.get_current_state()
                initial_event = {
                    "category": "system",
                    "type": "initial_state",
                    "source": "system",
                    "data": {"full_state": current_state},
                    "timestamp": time.time()
                }
                await websocket.send_text(json.dumps(initial_event))

            # Continue listening for future client messages (if any)
            while True:
                await websocket.receive_text()

        except WebSocketDisconnect:
            pass
        finally:
            ping_task.cancel()
            self.manager.disconnect(websocket)