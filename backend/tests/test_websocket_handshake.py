"""The two daemon WebSockets' handshake, against a real aiohttp over loopback.

What breaks when these fail: `LibrespotWebSocket` (go-librespot's /events) and
`SnapcastWebSocketService` (snapserver's control socket) are each the only
thing that hears their daemon. Their other test files drive them through a
`FakeSession`, which accepts any keyword and so cannot see how aiohttp reads
the arguments it is given. Measured on aiohttp 3.14: both clients passed
`timeout=aiohttp.ClientTimeout(total=5)` to `ws_connect`, which expects a
`ClientWSTimeout` there. aiohttp warned on every connect and filed the object
as the *close* timeout, so the five seconds bounded nothing: the handshake runs
under the session's timeout, and snapserver's session had none of its own —
aiohttp's default is 300 s, so a snapserver that accepts and never answers held
the reconnect loop for five minutes. Each client now owns its bound.

Every server here is this file's own, on an ephemeral loopback port — never
snapserver's 1780 or go-librespot's 3678, which answer on this machine and
reach speakers in occupied rooms.
"""
import asyncio
import logging
import warnings
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from aiohttp import web

from backend.core.multiroom.websocket import SnapcastWebSocketService
from backend.sources.spotify.websocket import LibrespotWebSocket

# Captured at import: the reconnect test patches `websocket.asyncio.sleep`, which
# is the global `asyncio.sleep`, and this file's own waits must not go through it.
_REAL_SLEEP = asyncio.sleep


@pytest.fixture
async def ws_server():
    """A WebSocket endpoint that sends one frame, then reads until the peer closes."""
    runners = []

    async def start(path: str) -> int:
        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await ws.send_str('{"type": "active"}')
            async for _msg in ws:
                pass
            return ws

        app = web.Application()
        app.router.add_get(path, handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        runners.append(runner)
        return site._server.sockets[0].getsockname()[1]

    yield start
    for runner in runners:
        await runner.cleanup()


@pytest.fixture
async def silent_server():
    """Accepts TCP connections and never answers the HTTP upgrade: a wedged daemon."""
    held = []

    async def _hold(reader, writer):
        held.append(writer)
        await asyncio.Event().wait()

    server = await asyncio.start_server(_hold, "127.0.0.1", 0)
    yield server.sockets[0].getsockname()[1], held
    for writer in held:
        writer.close()
    server.close()


@pytest.fixture
def deprecations():
    """Every DeprecationWarning raised while the test runs, whatever the global filters say."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        yield lambda: [w for w in caught if issubclass(w.category, DeprecationWarning)]


async def _until(predicate, timeout=3.0):
    async with asyncio.timeout(timeout):
        while not predicate():
            await _REAL_SLEEP(0.01)


def _snapcast_service(port: int) -> SnapcastWebSocketService:
    state_machine = MagicMock()
    state_machine.broadcast = AsyncMock()
    routing = MagicMock()
    routing.multiroom_enabled = False  # initialize() opens the session, dials nothing
    return SnapcastWebSocketService(
        state_machine=state_machine,
        routing_service=routing,
        snapcast_service=None,
        host="127.0.0.1",
        port=port,
    )


class TestLibrespotSocket:
    async def test_connect_and_stop_use_the_api_aiohttp_supports(self, ws_server, deprecations):
        """A deprecated argument is a connect that breaks on the next aiohttp."""
        port = await ws_server("/events")
        events = []

        async def on_event(event):
            events.append(event)

        async with aiohttp.ClientSession() as session:
            client = LibrespotWebSocket(f"ws://127.0.0.1:{port}/events", session, on_event)
            await client.start()
            await _until(lambda: events)
            await client.stop()

        assert events == [{"type": "active"}]
        assert deprecations() == []

    async def test_a_daemon_that_never_answers_is_reported_and_retried(
        self, silent_server, monkeypatch, caplog
    ):
        """go-librespot can be up but wedged after a restart. The client bounds
        its own handshake — whatever session it is handed — gives up on it, says
        so, and dials again rather than parking on it. The bound is shortened to
        keep the test fast; the session here has aiohttp's 300 s default."""
        port, held = silent_server
        monkeypatch.setattr(LibrespotWebSocket, "CONNECT_TIMEOUT_S", 0.2, raising=False)
        monkeypatch.setattr(
            "backend.sources.spotify.websocket.asyncio.sleep",
            lambda _delay: _REAL_SLEEP(0),  # the 2 s reconnect pause, granted at once
        )

        async def on_event(_event):
            pass

        with caplog.at_level(logging.WARNING, logger="source.spotify.websocket"):
            async with aiohttp.ClientSession() as session:
                client = LibrespotWebSocket(f"ws://127.0.0.1:{port}/events", session, on_event)
                await client.start()
                try:
                    await _until(lambda: len(held) >= 2)
                finally:
                    await client.stop()

        assert client.connected is False
        assert [r for r in caplog.records if "did not answer" in r.getMessage()]


class TestSnapcastSocket:
    async def test_connect_and_disable_use_the_api_aiohttp_supports(self, ws_server, deprecations):
        port = await ws_server("/jsonrpc")
        service = _snapcast_service(port)
        await service.initialize()
        try:
            await service.start_connection()
            assert await service.wait_for_ready(timeout=3.0)
            await service.stop_connection()
        finally:
            await service.cleanup()

        assert deprecations() == []

    async def test_a_snapserver_that_never_answers_releases_the_connect(
        self, silent_server, monkeypatch, caplog
    ):
        """The handshake is bounded, so the reconnect loop gets to try again.

        The bound is shortened to keep the test fast; what is asserted is that
        it applies at all — unbounded, the attempt outlives the outer guard.
        """
        port, _held = silent_server
        monkeypatch.setattr(SnapcastWebSocketService, "CONNECT_TIMEOUT_S", 0.2, raising=False)
        service = _snapcast_service(port)
        await service.initialize()
        try:
            with caplog.at_level(logging.INFO, logger="backend.core.multiroom.websocket"):
                async with asyncio.timeout(3.0):
                    await service._connect_and_listen()

            assert service.websocket is None
            assert not service._ready_event.is_set()
            [record] = [r for r in caplog.records if "did not answer" in r.getMessage()]
            assert record.levelno == logging.WARNING
        finally:
            await service.cleanup()
