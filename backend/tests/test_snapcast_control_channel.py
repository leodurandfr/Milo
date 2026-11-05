"""snapserver's control JSON-RPC rides the one WebSocket the backend keeps open.

What breaks when these fail: every command and query used to open an HTTP
connection of its own, and snapserver logs each one the client closes as
`[Error] (ControlSessionHTTP) Failed to shudown socket`: the 30 s reconcile
sweep alone wrote ~5 000 such lines a day, half the unit's journal, over any
real stream failure. Keeping one HTTP connection alive was tried and reverted:
snapserver 0.35 garbles two requests in flight on a kept connection (`bad
method`, `Broken pipe`, empty bodies), and the admissions gave up on every
satellite. The WebSocket frames each message, so requests can share it, and
each answer is paired back to its request by id.

The outside world is the fake snapserver of `tests/snapserver_world.py`, served
on an ephemeral loopback port, counting the HTTP requests and WebSocket sessions
that reach it. `SnapcastService` and `SnapcastWebSocketService` are the real
ones.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.config.constants import MULTIROOM_STREAMS
from backend.core.models.audio_state import AudioSource
from backend.core.multiroom.snapcast import SnapcastRequestError, SnapcastService
from backend.core.multiroom.websocket import SnapcastWebSocketService
from backend.tests.snapserver_world import BUREAU, CANAPE, CLIENT_IPS, LOCAL, FakeSnapserver, serving

RADIO = MULTIROOM_STREAMS[AudioSource.RADIO].name


def _registry():
    """A registry that knows nobody, and records who it is asked to admit."""
    registry = MagicMock()
    registry.get_client_ids = MagicMock(return_value=[])
    registry.get_client = MagicMock(return_value=None)
    registry.get_all_clients = MagicMock(return_value={})
    registry.register_client = AsyncMock(return_value=True)
    registry.set_clients_offline = AsyncMock(return_value=[])
    return registry


@pytest.fixture
async def snapserver():
    async with serving(FakeSnapserver()) as fake:
        yield fake


@pytest.fixture
async def connected(snapserver):
    """The two services as `dependencies.py` wires them, connected to the fake."""
    snapcast = SnapcastService(systemd_manager=MagicMock(), host="127.0.0.1", port=snapserver.port)
    routing = MagicMock()
    routing.multiroom_enabled = True
    control = SnapcastWebSocketService(
        state_machine=MagicMock(), routing_service=routing,
        host="127.0.0.1", port=snapserver.port, snapcast_service=snapcast,
    )
    control._registry = _registry()
    snapcast.set_control_channel(control)
    await control.initialize()
    try:
        assert await control.wait_for_ready(timeout=5.0)
        yield snapcast, control
    finally:
        await control.cleanup()


async def test_the_sweep_and_commands_open_no_http_connection(snapserver, connected):
    """The sweep's read, a volume and a stream switch: one WebSocket, no HTTP, and
    the rooms are where the commands put them."""
    snapcast, control = connected

    assert snapcast.extract_clients(await snapcast.get_server_status())
    assert await snapcast.set_volume(CANAPE, 40)
    assert await snapcast.point_groups_at(RADIO)

    assert snapserver.http_requests == 0
    assert snapserver.ws_sessions == 1
    assert snapserver.client(CANAPE)["config"]["volume"]["percent"] == 40
    assert [snapserver.stream_of(c) for c in CLIENT_IPS] == [RADIO] * 3


async def test_concurrent_requests_each_get_their_own_answer(snapserver, connected):
    """Answers leave out of order (the status is the slowest): each request still
    reads its own, never its neighbor's."""
    _, control = connected
    snapserver.delay["Server.GetStatus"] = 0.05

    status, *volumes = await asyncio.gather(
        control.call("Server.GetStatus"),
        *(
            control.call("Client.SetVolume", {"id": client, "volume": {"percent": percent, "muted": False}})
            for client, percent in ((LOCAL, 10), (CANAPE, 20), (BUREAU, 30))
        ),
    )

    assert "groups" in status["server"]
    assert [v["volume"]["percent"] for v in volumes] == [10, 20, 30]


async def test_a_notification_handler_may_query_snapserver(snapserver, connected):
    """Server.OnUpdate reconciles from a status read on the very socket that
    carried the notification: the reader must not be the one waiting."""
    _, control = connected
    registry = control.registry
    admitted_at_connect = registry.register_client.await_count
    assert admitted_at_connect, "the connect sweep admitted nobody to compare against"

    await snapserver.notify("Server.OnUpdate")

    async def _reconciled():
        while registry.register_client.await_count < 2 * admitted_at_connect:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_reconciled(), timeout=5.0)


async def test_a_dropped_socket_fails_pending_requests_by_name(snapserver, connected):
    """snapserver going down mid-request: the request fails at once, naming the
    socket, and nothing falls back to HTTP."""
    snapcast, control = connected
    snapserver.hang = True

    pending = asyncio.create_task(snapcast.get_server_status())
    request = asyncio.create_task(control.call("Server.GetStatus"))
    while sum(1 for e in snapserver.journal if e[:2] == ("rpc", "Server.GetStatus")) < 3:
        await asyncio.sleep(0.01)
    await snapserver.drop_sockets()

    with pytest.raises(SnapcastRequestError, match="WebSocket"):
        await asyncio.wait_for(request, timeout=2.0)
    assert await asyncio.wait_for(pending, timeout=2.0) == {}
    with pytest.raises(SnapcastRequestError, match="WebSocket not connected"):
        await control.call("Server.GetStatus")
    assert snapserver.http_requests == 0


async def test_an_error_answer_is_a_typed_failure_naming_it(snapserver, connected):
    """snapserver answers an unknown client with a JSON-RPC error: read as its
    empty result, a refused command would pass for an applied one."""
    _, control = connected
    snapserver.refuse["Client.SetLatency"] = None

    with pytest.raises(SnapcastRequestError, match="Client.SetLatency: .*refused"):
        await control.call("Client.SetLatency", {"id": CANAPE, "latency": 40})


async def test_a_hung_snapserver_costs_a_request_its_timeout_only(snapserver, connected):
    """Each request is bounded on its own: a wedged daemon cannot hold the sweep,
    a route or a transition, and the next request is still answered."""
    _, control = connected
    snapserver.hang = True

    with pytest.raises(SnapcastRequestError, match="no answer"):
        await control.call("Server.GetStatus", timeout=0.05)

    snapserver.hang = False
    assert "groups" in (await control.call("Server.GetStatus"))["server"]
