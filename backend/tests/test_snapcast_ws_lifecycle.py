"""The snapserver control socket's own lifecycle: open, listen, back off, tear down.

What breaks when these fail: `SnapcastWebSocketService` is the only thing that
hears snapserver. Every satellite that was already up when the backend started —
i.e. the whole fleet after a power cut — is admitted from `_connect_and_listen`
and nowhere else, and every later arrival comes through the message loop this
file drives. Measured 2026-08-27, none of it had ever run: the suite called the
handlers directly, so `initialize`, `_connection_loop`, `_connect_and_listen`,
`wait_for_ready` and `cleanup` were 72 uncovered lines between a snapserver
restart and a speaker appearing in the room.

What lives only here, in order of what it costs:

* the admission order. `_ready_event` is set *after* `_initialize_existing_clients`
  returns, and `wait_for_ready` is what `AudioRoutingService` waits on before it
  declares multiroom ready. Setting it earlier would let the routing transition
  finish against an empty registry — every satellite missing from the UI until
  something else happened to notify.
* the reconnect delay. It grows 5 → 30 s and resets on a successful connection;
  a delay that never resets makes every reconnection cost half a minute, and one
  that never grows hammers a dead snapserver at 5 s intervals forever.
* `cleanup` capturing the socket *before* draining the tasks. Its twin
  `stop_connection` carries the same one-line invariant in a comment because it
  paid for it: `_connect_and_listen` nulls `self.websocket` in its `finally` as
  the task unwinds, so a close guarded on the attribute after `cancel_all()` can
  never fire and the TCP connection to snapserver leaks.

The `never_the_real_snapserver` fixture below is why this file can exist at all:
snapserver's control socket answers on `0.0.0.0:1780` on this machine, the
service's default host is `localhost`, and the suite's network guard lets
loopback through on purpose. A test that lost its double would not merely read —
`_initialize_existing_clients` admits every live client and each admission
syncs volume, EQ and snapclient buffer config to a speaker in an occupied room.
"""
import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from backend.core.multiroom.websocket import SnapcastWebSocketService

# Captured at import: a neighbouring test may replace asyncio.sleep globally,
# and a double that yields through the replacement is no longer a double.
_REAL_SLEEP = asyncio.sleep


class ReachedTheLiveSnapserver(BaseException):
    """Raised when a double was bypassed and a real aiohttp session was opening.

    Derived from BaseException on purpose: both `initialize` and
    `_connect_and_listen` wrap their whole body in `except Exception`, so an
    ordinary error would be swallowed, logged, and the run would stay green with
    the guard crossed.
    """


@pytest.fixture(autouse=True)
def never_the_real_snapserver(monkeypatch):
    """Make a real aiohttp session impossible to build for the duration of a test."""

    class _Forbidden:
        def __init__(self, *args, **kwargs):
            raise ReachedTheLiveSnapserver(
                "a test built a real aiohttp.ClientSession; snapserver listens on "
                "0.0.0.0:1780 on this machine and admitting its clients pushes to "
                "the satellites"
            )

    monkeypatch.setattr(aiohttp, "ClientSession", _Forbidden)


class FakeMessage:
    """One frame off the control socket."""

    def __init__(self, type_, data=""):
        self.type = type_
        self.data = data


class FakeWebSocket:
    """The snapserver control socket, doubled.

    Bounded by construction: the frame list is finite and `park` is drained by
    the cancellation `cleanup`/`stop_connection` issue. A double that could
    iterate forever turns a mutation into a hung suite instead of a red one.
    """

    def __init__(self, frames=(), park: bool = False, error=None):
        self._frames = list(frames)
        self._park = park
        self.closed = False
        self.sent: list[str] = []
        self.close_calls = 0
        self._error = error

    async def send_str(self, payload: str) -> None:
        await _REAL_SLEEP(0)
        self.sent.append(payload)

    def exception(self):
        return self._error

    async def close(self):
        self.close_calls += 1
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        await _REAL_SLEEP(0)
        if self._frames:
            return self._frames.pop(0)
        if self._park:
            await asyncio.Event().wait()  # cancelled by the teardown under test
        raise StopAsyncIteration


class FakeSession:
    """Stands in for `aiohttp.ClientSession`, and records the URL it was asked for."""

    def __init__(self, websocket=None, connect_error=None):
        self.websocket = websocket if websocket is not None else FakeWebSocket()
        self.connect_error = connect_error
        self.ws_urls: list[str] = []
        self.close_calls = 0

    async def ws_connect(self, url, **kwargs):
        # Real I/O always yields; a double that never suspends represents a state
        # the world cannot produce, and cancellation becomes undeliverable.
        await _REAL_SLEEP(0)
        self.ws_urls.append(url)
        if self.connect_error is not None:
            raise self.connect_error
        return self.websocket

    async def close(self):
        self.close_calls += 1


def make_service(*, multiroom_enabled=True, snapcast=None, registry=None):
    state_machine = MagicMock()
    state_machine.broadcast = AsyncMock()
    routing = MagicMock()
    routing.multiroom_enabled = multiroom_enabled
    service = SnapcastWebSocketService(
        state_machine=state_machine,
        routing_service=routing,
        snapcast_service=snapcast,
    )
    if registry is not None:
        service._registry = registry
    return service


@pytest.fixture
def session_factory(monkeypatch):
    """Install a constructible session double and hand back the instances built."""
    built: list[FakeSession] = []

    def install(websocket=None, connect_error=None):
        def _factory(*args, **kwargs):
            session = FakeSession(websocket=websocket, connect_error=connect_error)
            built.append(session)
            return session

        monkeypatch.setattr(aiohttp, "ClientSession", _factory)
        return built

    return install


@pytest.fixture
def instant_delays(monkeypatch):
    """Record every wait the unit asks for, and grant it immediately.

    Carried on the *magnitude*, not on the module: the production waits here are
    5-30 s reconnect backoffs and a 30 s reconcile period, and a test that
    actually served them would be a wall-clock budget. Everything shorter is
    delegated to the real primitive so the suite's own scheduling is untouched.
    """
    delays: list[float] = []

    async def _sleep(delay, *args, **kwargs):
        if delay >= 1:
            delays.append(delay)
            return await _REAL_SLEEP(0)
        return await _REAL_SLEEP(delay, *args, **kwargs)

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    return delays


class TestInitialize:
    """The boot decision: whether this service listens at all."""

    async def test_a_session_is_opened_and_the_service_marked_running(self, session_factory):
        """Nothing else opens it; `_connect_and_listen` uses `self.session` directly."""
        built = session_factory()
        service = make_service(multiroom_enabled=False)

        assert await service.initialize() is True
        assert len(built) == 1, "initialize must open exactly one session"
        assert service.session is built[0]
        assert service.running is True

    async def test_multiroom_already_on_starts_both_loops(self, session_factory):
        """A backend restart with multiroom on must reconnect without anyone asking.

        Both loops are due, not one: the connection loop hears live notifications,
        the reconcile sweep is the only thing that notices a satellite that
        vanished without a TCP FIN.
        """
        session_factory()
        service = make_service(multiroom_enabled=True)

        await service.initialize()
        try:
            assert service.should_connect is True
            assert service.reconnect_task is not None
            assert service.reconcile_task is not None
        finally:
            await service._bg.cancel_all()

    async def test_multiroom_off_starts_nothing_and_still_succeeds(self, session_factory):
        """Direct mode must not hold a socket open to a snapserver that is stopped."""
        session_factory()
        service = make_service(multiroom_enabled=False)

        assert await service.initialize() is True
        assert service.should_connect is False
        assert service.reconnect_task is None
        assert service.reconcile_task is None

    async def test_the_routing_service_is_the_only_authority_on_multiroom(self, session_factory):
        """No fallback chain: absent routing service means multiroom is off, not unknown."""
        session_factory()
        service = make_service(multiroom_enabled=True)
        service.routing_service = None

        await service.initialize()
        assert service.should_connect is False

    async def test_a_session_that_cannot_open_leaves_the_service_down(self, monkeypatch):
        """Fail closed: `running` gates `start_connection`, so a half-up service
        would accept a multiroom enable and then never connect."""

        def _boom(*args, **kwargs):
            raise OSError("no sockets left")

        monkeypatch.setattr(aiohttp, "ClientSession", _boom)
        service = make_service(multiroom_enabled=True)

        assert await service.initialize() is False
        assert service.running is False
        assert service.reconnect_task is None


class TestConnectAndListen:
    """One connection, from the URL to the frame loop to the socket being dropped."""

    async def test_it_dials_the_configured_control_socket(self, session_factory):
        built = session_factory()
        service = make_service(multiroom_enabled=False)
        await service.initialize()

        await service._connect_and_listen()

        assert built[0].ws_urls == [service.ws_url]

    async def test_a_stale_ready_flag_is_cleared_before_dialling(self, session_factory):
        """A True left by the previous connection would let callers proceed
        against a socket that is already dead."""
        session_factory(connect_error=aiohttp.ClientConnectorError(MagicMock(), OSError()))
        service = make_service(multiroom_enabled=False)
        await service.initialize()
        service._ready_event.set()

        await service._connect_and_listen()

        assert not service._ready_event.is_set()

    async def test_ready_is_announced_only_after_the_existing_clients_are_admitted(
        self, session_factory
    ):
        """Order, not presence: `wait_for_ready` returning early lets the routing
        transition declare multiroom ready against an empty registry."""
        session_factory(websocket=FakeWebSocket())
        service = make_service(multiroom_enabled=False)
        await service.initialize()

        seen = []

        async def _admit():
            seen.append(("admitting", service._ready_event.is_set()))

        service._initialize_existing_clients = _admit

        await service._connect_and_listen()

        assert seen == [("admitting", False)], "ready was set before admission ran"
        assert service._ready_event.is_set()

    async def test_the_rpc_ping_goes_out_before_any_frame_is_read(self, session_factory):
        """The first send is what proves the socket is a snapserver and not just open."""
        ws = FakeWebSocket(frames=[FakeMessage(aiohttp.WSMsgType.CLOSE)])
        session_factory(websocket=ws)
        service = make_service(multiroom_enabled=False)
        await service.initialize()
        service._initialize_existing_clients = AsyncMock()

        await service._connect_and_listen()

        assert len(ws.sent) == 1
        assert json.loads(ws.sent[0])["method"] == "Server.GetRPCVersion"

    async def test_text_frames_reach_the_message_handler(self, session_factory):
        """The only route a snapserver notification has into this service."""
        frames = [
            FakeMessage(aiohttp.WSMsgType.TEXT, json.dumps({"method": "Client.OnConnect"})),
            FakeMessage(aiohttp.WSMsgType.TEXT, json.dumps({"method": "Server.OnUpdate"})),
        ]
        session_factory(websocket=FakeWebSocket(frames=frames))
        service = make_service(multiroom_enabled=False)
        await service.initialize()
        service._initialize_existing_clients = AsyncMock()
        handled = []
        service._handle_message = AsyncMock(side_effect=lambda d: handled.append(d["method"]))

        await service._connect_and_listen()

        assert handled == ["Client.OnConnect", "Server.OnUpdate"]

    async def test_one_unparseable_frame_does_not_end_the_stream(self, session_factory, caplog):
        """snapweb shares this socket; a frame Milō cannot parse must cost one
        frame, not the connection and every notification after it."""
        frames = [
            FakeMessage(aiohttp.WSMsgType.TEXT, "{not json"),
            FakeMessage(aiohttp.WSMsgType.TEXT, json.dumps({"method": "Client.OnConnect"})),
        ]
        session_factory(websocket=FakeWebSocket(frames=frames))
        service = make_service(multiroom_enabled=False)
        await service.initialize()
        service._initialize_existing_clients = AsyncMock()
        handled = []
        service._handle_message = AsyncMock(side_effect=lambda d: handled.append(d["method"]))

        with caplog.at_level(logging.ERROR, logger="backend.core.multiroom.websocket"):
            await service._connect_and_listen()

        assert handled == ["Client.OnConnect"], "the frame after the bad one was dropped"
        assert any("Invalid JSON" in r.message for r in caplog.records)

    async def test_an_error_frame_ends_the_stream(self, session_factory):
        """Returning to the connection loop is what gets a fresh socket; staying
        in the loop would read from a socket that answers nothing."""
        ws = FakeWebSocket(
            frames=[
                FakeMessage(aiohttp.WSMsgType.ERROR),
                FakeMessage(aiohttp.WSMsgType.TEXT, json.dumps({"method": "Client.OnConnect"})),
            ],
            error=RuntimeError("socket died"),
        )
        session_factory(websocket=ws)
        service = make_service(multiroom_enabled=False)
        await service.initialize()
        service._initialize_existing_clients = AsyncMock()
        service._handle_message = AsyncMock()

        await service._connect_and_listen()

        service._handle_message.assert_not_called()

    async def test_a_close_frame_ends_the_stream(self, session_factory):
        """snapserver going down with multiroom is the ordinary case."""
        ws = FakeWebSocket(
            frames=[
                FakeMessage(aiohttp.WSMsgType.CLOSE),
                FakeMessage(aiohttp.WSMsgType.TEXT, json.dumps({"method": "Client.OnConnect"})),
            ]
        )
        session_factory(websocket=ws)
        service = make_service(multiroom_enabled=False)
        await service.initialize()
        service._initialize_existing_clients = AsyncMock()
        service._handle_message = AsyncMock()

        await service._connect_and_listen()

        service._handle_message.assert_not_called()

    async def test_the_socket_reference_is_dropped_when_the_stream_ends(self, session_factory):
        """`connected` and every send guard read this attribute; a stale reference
        makes a dead socket report itself as the live control channel."""
        session_factory(websocket=FakeWebSocket())
        service = make_service(multiroom_enabled=False)
        await service.initialize()
        service._initialize_existing_clients = AsyncMock()

        await service._connect_and_listen()

        assert service.websocket is None
        assert service.connected is False

    async def test_a_refused_connect_is_reported_as_normal_and_leaves_no_socket(
        self, session_factory, caplog
    ):
        """snapserver is stopped whenever multiroom is off, so this is expected
        traffic — logging it as an error would raise the UI's fault banner on
        every direct-mode boot."""
        session_factory(connect_error=aiohttp.ClientConnectorError(MagicMock(), OSError()))
        service = make_service(multiroom_enabled=False)
        await service.initialize()

        with caplog.at_level(logging.DEBUG, logger="backend.core.multiroom.websocket"):
            await service._connect_and_listen()

        assert service.websocket is None
        assert not service._ready_event.is_set()
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    async def test_the_admission_burst_is_flagged_and_the_flag_is_cleared_after(
        self, session_factory, instant_delays
    ):
        """The flag is what keeps a whole fleet's re-admission out of the operator
        log at INFO on every reconnection."""
        session_factory(websocket=FakeWebSocket())
        service = make_service(multiroom_enabled=False)
        await service.initialize()
        during = []
        service._initialize_existing_clients = AsyncMock(
            side_effect=lambda: during.append(service._is_initializing)
        )

        await service._connect_and_listen()
        assert during == [True]

        await asyncio.gather(*[t for t in service._bg._tasks])
        assert service._is_initializing is False
        assert 2.0 in instant_delays


class TestConnectionLoop:
    """Reconnection: the only thing that gets multiroom back after snapserver dies."""

    def _bounded(self, service, *, calls, outcome):
        """Replace one connection attempt with a bounded double.

        Bounded on *every* path the mutation can open, not just the green one: a
        `while True:` here would otherwise spin at full CPU on this machine, which
        is itself what desynchronises snapcast.
        """
        state = {"n": 0}

        async def _attempt():
            state["n"] += 1
            if state["n"] > calls:
                raise ReachedTheLiveSnapserver("connection loop ran past its bound")
            await _REAL_SLEEP(0)
            outcome(state["n"])

        service._connect_and_listen = _attempt
        return state

    async def test_the_loop_stops_when_multiroom_is_switched_off(self, instant_delays):
        """`stop_connection` flips the flag and drains; a loop that ignored it
        would immediately reopen the socket it was just told to drop."""
        service = make_service(multiroom_enabled=False)
        service.running = True
        service.should_connect = True

        def _outcome(n):
            service.should_connect = False

        state = self._bounded(service, calls=5, outcome=_outcome)

        await service._connection_loop()

        assert state["n"] == 1
        assert instant_delays == [], "it slept before noticing it had been stopped"

    async def test_the_delay_grows_after_each_failure_and_is_capped(self, instant_delays):
        """A flat 5 s retry hammers a snapserver that is down for maintenance;
        an uncapped one would reach hours."""
        service = make_service(multiroom_enabled=False)
        service.running = True
        service.should_connect = True

        def _outcome(n):
            if n >= 12:
                service.should_connect = False
            raise OSError("refused")

        self._bounded(service, calls=14, outcome=_outcome)

        await service._connection_loop()

        assert instant_delays[0] == 5
        assert instant_delays[1] == 7.5
        assert instant_delays[2] == 11.25
        assert instant_delays[-1] == 30
        assert max(instant_delays) == 30

    async def test_a_connection_that_worked_resets_the_delay(self, instant_delays):
        """Without the reset, a fleet that reconnects after a long outage keeps
        paying the 30 s backoff on every later blip."""
        service = make_service(multiroom_enabled=False)
        service.running = True
        service.should_connect = True

        def _outcome(n):
            if n >= 6:
                service.should_connect = False
            if n in (4, 5):
                return  # the connection held, then dropped cleanly
            raise OSError("refused")

        self._bounded(service, calls=8, outcome=_outcome)

        await service._connection_loop()

        assert instant_delays[:3] == [5, 7.5, 11.25]
        assert instant_delays[3] == 5, "the delay was not reset by a good connection"

    async def test_cancellation_leaves_the_loop_without_logging_a_fault(
        self, instant_delays, caplog
    ):
        """The teardown cancels this task by design; treating that as a connection
        error would put a fault line in the operator log on every shutdown."""
        service = make_service(multiroom_enabled=False)
        service.running = True
        service.should_connect = True

        def _outcome(n):
            raise asyncio.CancelledError()

        self._bounded(service, calls=3, outcome=_outcome)

        with caplog.at_level(logging.ERROR, logger="backend.core.multiroom.websocket"):
            await service._connection_loop()

        assert instant_delays == []
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


class TestWaitForReady:
    """The rendezvous `AudioRoutingService` uses before it declares multiroom up."""

    async def test_it_returns_once_the_connection_announces_itself(self):
        service = make_service(multiroom_enabled=False)
        service._ready_event.set()

        assert await service.wait_for_ready(timeout=1.0) is True

    async def test_a_connection_that_never_arrives_is_reported_not_awaited_forever(
        self, caplog
    ):
        """It is called on the request path (PUT /api/routing/multiroom); an
        unbounded wait would hold the HTTP response open on a dead snapserver."""
        service = make_service(multiroom_enabled=False)

        with caplog.at_level(logging.WARNING, logger="backend.core.multiroom.websocket"):
            assert await service.wait_for_ready(timeout=0.01) is False

        assert any("Timeout waiting" in r.message for r in caplog.records)


class TestCleanup:
    """The lifespan teardown. T16-8's last open item, re-submitted 2026-08-27."""

    async def _connected_service(self, session_factory):
        ws = FakeWebSocket(park=True)
        built = session_factory(websocket=ws)
        service = make_service(multiroom_enabled=False)
        await service.initialize()
        service._initialize_existing_clients = AsyncMock()
        service.running = True
        service.should_connect = True
        service._bg.spawn(service._connect_and_listen(), label="connection_loop")
        await service.wait_for_ready(timeout=2.0)
        assert service.websocket is ws, "the fixture never reached a connected state"
        return service, ws, built[0]

    async def test_the_socket_is_captured_before_the_tasks_are_drained(self, session_factory):
        """The whole point of the line. `_connect_and_listen` nulls
        `self.websocket` in its `finally` as cancellation unwinds it, so reading
        the attribute after `cancel_all()` yields None and the close never fires —
        the connection to snapserver then leaks on every backend shutdown, and
        the backend is restarted by every source unit's `PartOf`.
        """
        service, ws, _ = await self._connected_service(session_factory)

        await service.cleanup()

        assert ws.close_calls == 1, "the captured socket was never closed"
        assert service.websocket is None

    async def test_the_http_session_is_closed_too(self, session_factory):
        """The socket is one connection; the session owns the connector pool."""
        service, _, session = await self._connected_service(session_factory)

        await service.cleanup()

        assert session.close_calls == 1

    async def test_the_loops_are_told_to_stop_and_drained(self, session_factory):
        """Both flags gate the two loops. Leaving either set means a task that
        outlives the service and reopens a socket during teardown."""
        service, _, _ = await self._connected_service(session_factory)

        await service.cleanup()

        assert service.running is False
        assert service.should_connect is False
        assert service._bg._tasks == set()
