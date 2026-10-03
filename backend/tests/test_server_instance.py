"""`shared/instance.py` — the id that names one backend process.

What breaks when this fails: App.vue reveals the dock when the backend it is
connected to has restarted, and tells that from an ordinary reconnect (a phone
back from the background, a dropped socket) only by this id changing. An id
that survived a restart would make a restart invisible; one drawn per
handshake would make every reconnect look like one.
"""
import asyncio
import contextlib
import json
import runpy
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.health import create_health_router
from backend.core.state import AudioStateMachine
from backend.ws.manager import WebSocketManager, WebSocketServer

INSTANCE_PY = Path(__file__).resolve().parents[1] / "shared" / "instance.py"


def test_every_backend_process_gets_its_own_id():
    """Each run of the module is what a process start does."""
    first = runpy.run_path(str(INSTANCE_PY))["SERVER_INSTANCE"]
    second = runpy.run_path(str(INSTANCE_PY))["SERVER_INSTANCE"]

    assert len(first) >= 16
    assert first != second


async def test_one_process_names_itself_the_same_on_every_handshake_and_path():
    """What App.vue relies on: two handshakes with the same backend name the same
    process — or every reconnect (a phone back from the background) reads as a
    restart and throws the dock up — and the HTTP fallback names it the same, or
    a captive-portal boot misses a restart that follows it."""
    real = AudioStateMachine()
    state_machine = Mock()
    state_machine.get_current_state = real.get_current_state
    state_machine.refresh_active_view = AsyncMock(return_value=False)
    manager = Mock(spec=WebSocketManager)
    manager.connect = AsyncMock()
    manager.disconnect = Mock()
    manager.active_connections = set()
    server = WebSocketServer(manager, state_machine)

    handshakes = []
    for _ in range(2):
        websocket = AsyncMock()
        websocket.receive_text = AsyncMock(
            side_effect=['{"type": "ready"}', asyncio.CancelledError()]
        )
        with contextlib.suppress(asyncio.CancelledError):
            await server.websocket_endpoint(websocket)
        envelope = json.loads(websocket.send_text.call_args_list[0][0][0])
        handshakes.append(envelope["data"]["server_instance"])

    app = FastAPI()
    settings = Mock(get_setting=AsyncMock(return_value=True))
    network = Mock(hotspot_active=False)
    app.include_router(create_health_router(state_machine, Mock(), settings, network, Mock(), Mock()))
    fallback = TestClient(app).get("/api/initial-state").json()["server_instance"]

    assert handshakes[0]
    assert handshakes == [handshakes[0], handshakes[0]]
    assert fallback == handshakes[0]
