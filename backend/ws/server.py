# backend/ws/server.py
"""
WebSocket server with fresh initial state and ping/pong
"""
import json
import asyncio
import logging
import time
from fastapi import WebSocket, WebSocketDisconnect
from backend.ws.manager import WebSocketManager
from backend.core.models.audio_state import AudioSource

logger = logging.getLogger(__name__)


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

    async def _send_volume_state(self, websocket: WebSocket):
        """Send volume state after availability is ready (non-blocking)."""
        try:
            # Wait for client availability (with timeout)
            await self.state_machine.volume_service.wait_for_availability(timeout=5.0)

            # Send current volume state
            volume_state = await self.state_machine.volume_service.get_volume_state()
            volume_event = {
                "category": "volume",
                "type": "volume_changed",
                "source": "volume",
                "data": {
                    "show_bar": False,
                    "state": volume_state.to_dict()
                },
                "timestamp": time.time()
            }
            await websocket.send_text(json.dumps(volume_event))
        except Exception as e:
            # Client may have disconnected - ignore
            logger.debug(f"Failed to send volume state: {e}")

    async def websocket_endpoint(self, websocket: WebSocket):
        """WebSocket entry point with client-ready handshake.

        Flow:
        1. Client connects and registers event listeners
        2. Client sends {"type": "ready"} when ready to receive state
        3. Server responds with initial_state immediately (no blocking)
        4. Server sends volume_changed in background (non-blocking)
        5. Server continues broadcasting real-time updates

        This handshake prevents race conditions where WebSocket state arrives
        before Vue components register their event listeners.
        """
        await self.manager.connect(websocket)

        ping_task = asyncio.create_task(self._send_ping(websocket))
        volume_task = None

        try:
            # Pre-refresh metadata while client sets up event listeners
            await self.state_machine.refresh_active_metadata()

            # Wait for client ready signal
            message = await websocket.receive_text()
            client_msg = json.loads(message)

            if client_msg.get("type") == "ready":
                # Client is ready - send state IMMEDIATELY (no blocking waits)
                current_state = await self.state_machine.get_current_state()
                initial_event = {
                    "category": "system",
                    "type": "initial_state",
                    "source": "system",
                    "data": {"full_state": current_state},
                    "timestamp": time.time()
                }
                await websocket.send_text(json.dumps(initial_event))

                # Send volume state in background (non-blocking)
                # This allows the frontend to show UI immediately
                volume_task = asyncio.create_task(self._send_volume_state(websocket))

            # Continue listening for future client messages (if any)
            while True:
                await websocket.receive_text()

        except WebSocketDisconnect:
            pass
        except RuntimeError as e:
            # Handle "WebSocket is not connected" errors gracefully
            logger.debug(f"WebSocket runtime error: {e}")
        except Exception as e:
            logger.warning(f"WebSocket error: {e}")
        finally:
            ping_task.cancel()
            if volume_task:
                volume_task.cancel()
            self.manager.disconnect(websocket)