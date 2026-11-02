"""SnapcastService's transport to snapserver's JSON-RPC: one connection, kept.

What breaks when these fail: snapserver logs every control session the client
closes as `[Error] (ControlSessionHTTP) Failed to shudown socket: system:107`.
A session per request made the 30 s reconcile sweep alone close ~2 900 of them
a day, ~5 000 error lines, half the unit's journal: a triage looking for
snapserver's errors found thousands of false ones over any real stream or codec
failure, and the noise ate the journald budget that keeps an incident's history.

The snapserver here is a local HTTP/1.1 keep-alive stand-in that counts the
connections it accepts, on a port of its own: never 1780, where the live one
answers on this machine.
"""
import asyncio
import json

import pytest

from backend.core.multiroom.snapcast import SnapcastService
from backend.core.multiroom.websocket import SnapcastWebSocketService


class FakeSnapserver:
    """Answers every JSON-RPC POST with an empty result and keeps the connection
    open for the next one, as snapserver's HTTP control port does."""

    def __init__(self):
        self.port = 0
        self.connections = 0
        self.requests = 0
        self._server = None
        self._writers = []

    async def start(self):
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", self.port)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self):
        self._server.close()
        for writer in self._writers:
            writer.close()
        await self._server.wait_closed()

    async def restart(self):
        """A snapserver restart: every connection closed, then a new process on
        the same port, which has seen nobody yet."""
        await self.stop()
        self._writers = []
        self.connections = 0
        self.requests = 0
        await self.start()

    async def _serve(self, reader, writer):
        self.connections += 1
        self._writers.append(writer)
        try:
            while True:
                head = await reader.readuntil(b"\r\n\r\n")
                length = next(
                    int(line.split(b":", 1)[1])
                    for line in head.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                )
                request = json.loads(await reader.readexactly(length))
                self.requests += 1
                reply = json.dumps({"id": request["id"], "jsonrpc": "2.0", "result": {}}).encode()
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    b"Content-Length: %d\r\n\r\n" % len(reply) + reply
                )
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass  # the client closed its end
        finally:
            writer.close()


@pytest.fixture
async def snapserver():
    server = FakeSnapserver()
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
async def service(snapserver):
    svc = SnapcastService(systemd_manager=None, host="127.0.0.1", port=snapserver.port)
    yield svc
    await svc.cleanup()


async def test_successive_requests_reuse_one_connection(service, snapserver):
    """Three sweeps' worth of `Server.GetStatus` must cost snapserver one
    control session, not three: each one the client closes is two error lines
    in its journal."""
    for _ in range(3):
        await service.get_server_status()

    assert snapserver.requests == 3
    assert snapserver.connections == 1


async def test_a_snapserver_restart_between_two_requests_costs_no_request(
    service, snapserver
):
    """A settings write restarts snapserver under a kept connection. The pool
    must drop the dead one and dial the new daemon, not fail the first request
    after it: `wait_until_available` reads one failed probe as "not up yet",
    and a sweep landing there would read it as every client gone."""
    assert await service.is_available()

    await snapserver.restart()

    assert await service.is_available()
    assert snapserver.connections == 1


async def test_a_request_after_shutdown_fails_instead_of_reopening(service, snapserver):
    """The lifespan closes the session once its callers are torn down. One that
    still asks later must get a failure, not a fresh session nothing will close:
    asyncio reports that one as an error on the way out of every restart."""
    assert await service.is_available()
    await service.cleanup()

    assert not await service.is_available()
    assert snapserver.connections == 1


def test_the_keepalive_outlives_the_reconcile_sweep():
    """The sweep is the steady caller. A connection the pool closes for being
    idle before the next sweep arrives is a session closed per sweep again,
    which is what aiohttp's 15 s default does against a 30 s sweep."""
    assert SnapcastService.KEEPALIVE_S > SnapcastWebSocketService.RECONCILE_INTERVAL_S
