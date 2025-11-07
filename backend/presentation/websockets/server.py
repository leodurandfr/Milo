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
        """WebSocket entry point with fresh initial state and heartbeat"""
        await self.manager.connect(websocket)

        ping_task = asyncio.create_task(self._send_ping(websocket))

        try:
            current_state = await self.state_machine.get_current_state()

            if current_state['active_source'] != 'none':
                active_source = AudioSource(current_state['active_source'])
                active_plugin = self.state_machine.plugins.get(active_source)

                if active_plugin and hasattr(active_plugin, '_refresh_metadata'):
                    await active_plugin._refresh_metadata()

                    plugin_status = await active_plugin.get_initial_state()

                    current_state['metadata'] = plugin_status.get('metadata', {})
                    current_state['device_connected'] = plugin_status.get('device_connected', False)
                    current_state['ws_connected'] = plugin_status.get('ws_connected', False)
                    current_state['is_playing'] = plugin_status.get('is_playing', False)

            initial_event = {
                "category": "system",
                "type": "state_changed",
                "source": "system",
                "data": {"full_state": current_state},
                "timestamp": current_state.get("timestamp", 0)
            }

            await websocket.send_text(json.dumps(initial_event))

            while True:
                await websocket.receive_text()

        except WebSocketDisconnect:
            pass
        finally:
            ping_task.cancel()
            self.manager.disconnect(websocket)