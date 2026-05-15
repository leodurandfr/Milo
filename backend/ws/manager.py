# backend/ws/manager.py
"""
WebSocket connection management, broadcasting, and endpoint lifecycle.

Single module handling the full WebSocket lifecycle:
- Connection accept/close with per-client ping heartbeat
- Parallel broadcast with timeout and dead connection cleanup
- Client-ready handshake with initial state delivery

Performance: events reach frontend within 100ms via parallel broadcast
using asyncio.gather(). Slow clients (>1s) are closed and cleaned up.
"""
import json
import asyncio
import logging
import time
from typing import Set, Dict, Any

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

PING_INTERVAL = 30
SEND_TIMEOUT = 1.0


class WebSocketManager:
    """WebSocket connection manager and endpoint handler."""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and register a WebSocket connection."""
        await websocket.accept()
        self.active_connections.add(websocket)
        logger.debug(f"WebSocket connected, total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket) -> None:
        """Unregister a WebSocket connection."""
        self.active_connections.discard(websocket)
        logger.debug(f"WebSocket disconnected, total: {len(self.active_connections)}")

    async def broadcast_dict(self, event_data: Dict[str, Any]) -> None:
        """Broadcast event to all connections in parallel with timeout.

        Connections that fail or timeout are closed and removed.
        Slow/idle clients (background tabs, sleeping devices) are expected
        and handled silently — logged at DEBUG to avoid noise.
        """
        if not self.active_connections:
            return

        message = json.dumps(event_data)

        async def send_to_client(connection: WebSocket):
            try:
                await asyncio.wait_for(
                    connection.send_text(message), timeout=SEND_TIMEOUT
                )
                return connection, None
            except asyncio.TimeoutError:
                logger.debug("Slow client, closing connection")
            except Exception as e:
                logger.debug(f"Send to client failed: {e}")
            # Close dead connection so the client detects disconnect immediately
            try:
                await connection.close()
            except Exception:
                pass
            return connection, "failed"

        results = await asyncio.gather(
            *[send_to_client(conn) for conn in set(self.active_connections)],
            return_exceptions=True,
        )

        disconnected = set()
        for result in results:
            if isinstance(result, tuple):
                connection, error = result
                if error:
                    disconnected.add(connection)

        if disconnected:
            self.active_connections -= disconnected
            logger.debug(f"Removed {len(disconnected)} dead connection(s)")


class WebSocketServer:
    """WebSocket endpoint handler with handshake and heartbeat."""

    def __init__(self, ws_manager: WebSocketManager, state_machine,
                 volume_service=None, settings_service=None, network_service=None):
        self.manager = ws_manager
        self.state_machine = state_machine
        self.volume_service = volume_service
        self.settings_service = settings_service
        self.network_service = network_service

    async def _send_ping(self, websocket: WebSocket):
        """Send periodic pings to keep the connection alive."""
        while True:
            await asyncio.sleep(PING_INTERVAL)
            if websocket not in self.manager.active_connections:
                break
            try:
                await websocket.send_text(json.dumps({
                    "category": "system",
                    "type": "ping",
                    "timestamp": time.time(),
                }))
            except Exception:
                break

    async def _send_volume_state(self, websocket: WebSocket):
        """Send volume state after availability is ready (non-blocking)."""
        try:
            await self.volume_service.wait_for_availability(timeout=5.0)
            volume_state = await self.volume_service.get_volume_state()
            await websocket.send_text(json.dumps({
                "category": "volume",
                "type": "volume_changed",
                "origin": "volume",
                "data": {
                    "show_bar": False,
                    "state": volume_state.to_dict(),
                },
                "timestamp": time.time(),
            }))
        except Exception as e:
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
                current_state = self.state_machine.get_current_state()

                setup_completed = False
                if self.settings_service:
                    setup_completed = bool(
                        await self.settings_service.get_setting("setup_completed")
                    )

                hotspot_active = (
                    self.network_service.hotspot_active if self.network_service else False
                )

                await websocket.send_text(json.dumps({
                    "category": "system",
                    "type": "initial_state",
                    "origin": "system",
                    "data": {
                        "full_state": current_state,
                        "setup_completed": setup_completed,
                        "hotspot_active": hotspot_active,
                    },
                    "timestamp": time.time(),
                }))

                volume_task = asyncio.create_task(
                    self._send_volume_state(websocket)
                )

            # Keep listening for future client messages
            while True:
                await websocket.receive_text()

        except WebSocketDisconnect:
            pass
        except RuntimeError as e:
            logger.debug(f"WebSocket runtime error: {e}")
        except Exception as e:
            logger.warning(f"WebSocket error: {e}")
        finally:
            ping_task.cancel()
            if volume_task:
                volume_task.cancel()
            self.manager.disconnect(websocket)
