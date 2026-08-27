# backend/tests/test_spotify_librespot_socket.py
"""`spotify/websocket.py` — the go-librespot event feed, end to end.

44.6% at 39ff9daf, and the shape of the gap is exactly B1-2's on the snapserver
side: `_run_connection` (28 lines) and `_connection_loop` (9) had never run, so
**no frame had ever entered this client**. `test_spotify_source.py` exercises
the event handlers by calling them directly and asserts `on_event ==` for the
wiring; between the socket and those handlers there was nothing.

What lives only here:

* **the reconnect loop.** go-librespot is `Restart=always`; when it comes back
  the socket has to come back with it, and it has to keep trying rather than
  end on the first refusal.
* **`on_connect`, the reconcile.** go-librespot emits events *only on change*,
  so a daemon that restarted while a track was paused sends nothing at all.
  Without this callback the card keeps whatever it last showed, forever. It is
  also why a failing reconcile must not take the connection down with it.
* **the frame loop's exits.** A malformed frame must cost that frame and not
  the feed; an ERROR or CLOSED message must end the connection so the loop can
  reopen it.

The frames are real `aiohttp.WSMessage` values — the type the library hands the
`async for`, not an invented stand-in.
"""
import asyncio
import json

import aiohttp
import pytest
from aiohttp.client_reqrep import ConnectionKey

from backend.sources.spotify.websocket import LibrespotWebSocket

# Captured before any test can replace it. The reconnect tests below patch
# `websocket.asyncio.sleep`, and `websocket.py` does a bare `import asyncio`, so
# that double is GLOBAL — it would otherwise swallow this file's own yields too.
_REAL_SLEEP = asyncio.sleep


def _refused():
    """The exception aiohttp really raises when go-librespot is not listening.

    Built with a real `ConnectionKey`: `ClientConnectorError.__str__` reads
    `self._conn_key.ssl`, so a hand-waved `ClientConnectorError(None, ...)`
    explodes inside the client's own log call rather than in the branch under
    test — an invented shape failing exactly where an invented shape does.
    """
    key = ConnectionKey(
        host="localhost", port=3678, is_ssl=False, ssl=None, proxy=None,
        proxy_auth=None, proxy_headers_hash=None, server_hostname=None,
    )
    return aiohttp.ClientConnectorError(key, OSError(111, "Connection refused"))


def _text(payload):
    return aiohttp.WSMessage(aiohttp.WSMsgType.TEXT, json.dumps(payload), None)


def _error():
    return aiohttp.WSMessage(aiohttp.WSMsgType.ERROR, None, None)


def _closed():
    return aiohttp.WSMessage(aiohttp.WSMsgType.CLOSED, None, None)


class FakeWs:
    """One open websocket: yields the queued frames, then ends the iteration."""

    def __init__(self, frames, exception=None):
        self._frames = list(frames)
        self._exception = exception
        self.closed = False

    def __aiter__(self):
        async def gen():
            for frame in self._frames:
                yield frame
        return gen()

    def exception(self):
        return self._exception

    async def __aenter__(self):
        # A real connect always suspends. Without a true yield here the
        # reconnect loop can spin without ever handing control back, so a
        # mutation that removes its exit condition starves the event loop at
        # 100% CPU instead of failing — T7-1's shape, and on this machine a
        # busy loop is itself the hazard (it is what desynchronises snapcast).
        await _REAL_SLEEP(0)
        return self

    async def __aexit__(self, *_exc):
        self.closed = True
        return False


class TooManyConnects(RuntimeError):
    """The double refusing to keep feeding a loop that will not stop."""


class FakeSession:
    """`aiohttp.ClientSession`, reduced to the one call this client makes.

    Each `ws_connect` yields the next queued outcome — a FakeWs to iterate, or
    an exception to raise — so a test can describe a daemon that refuses once
    and then answers.

    **Bounded on purpose.** `_connection_loop` is a `while` over a condition,
    and a mutation that removes the condition makes it spin with no await that
    sleeps. An unbounded double then grows a list one entry per turn: measured
    on 2026-08-27, that reached 6 GB of RSS in four minutes, put this Pi into
    swap thrash and took the appliance down — mDNS and Tailscale with it — with
    a journal that ends mid-line. The wall-clock timeout was useless; the
    damage lands long before it. Bounding the DOUBLE (the B4 lesson, applied to
    a loop rather than a poll) is what turns that mutation into something a test
    can read.
    """

    MAX_CONNECTS = 50

    def __init__(self, *outcomes):
        self._outcomes = list(outcomes)
        self.connects = []

    def ws_connect(self, url, **kwargs):
        if len(self.connects) >= self.MAX_CONNECTS:
            raise TooManyConnects(
                f"the reconnect loop did not stop after {self.MAX_CONNECTS} connects"
            )
        self.connects.append((url, kwargs))
        outcome = (self._outcomes.pop(0) if len(self._outcomes) > 1
                   else self._outcomes[0])
        if isinstance(outcome, Exception):
            class _Boom:
                async def __aenter__(_self):
                    await _REAL_SLEEP(0)  # same reason as FakeWs.__aenter__
                    raise outcome

                async def __aexit__(_self, *_exc):
                    return False
            return _Boom()
        return outcome


@pytest.fixture
def events():
    seen = []

    async def on_event(event):
        seen.append(event)

    on_event.seen = seen
    return on_event


def client(session, on_event, on_connect=None, url="ws://localhost:3678/events"):
    return LibrespotWebSocket(url, session, on_event, on_connect)


class TestOneConnection:
    async def test_every_text_frame_reaches_the_handler(self, events):
        """Non-triviality first: frames really do travel from the socket to the
        source's dispatcher, which is what nothing had ever exercised."""
        ws = client(FakeSession(FakeWs([
            _text({"type": "playing"}),
            _text({"type": "metadata", "data": {"name": "Breathe"}}),
        ])), events)

        await ws._run_connection()

        assert [e["type"] for e in events.seen] == ["playing", "metadata"]

    async def test_the_configured_url_is_the_one_opened(self, events):
        session = FakeSession(FakeWs([]))
        ws = client(session, events, url="ws://127.0.0.1:9999/events")

        await ws._run_connection()

        assert session.connects[0][0] == "ws://127.0.0.1:9999/events"

    async def test_the_connect_is_bounded(self, events):
        """go-librespot can be up-but-wedged after a restart; an unbounded
        connect would park the reconnect loop on it forever and the source
        would never learn it is deaf."""
        session = FakeSession(FakeWs([]))

        await client(session, events)._run_connection()

        assert session.connects[0][1]["timeout"].total == 5

    async def test_connected_is_true_while_the_socket_is_open(self, events):
        """`connected` is what the source reports as the daemon link; a flag
        that never goes true makes an open feed look dead."""
        seen = {}

        async def on_event(event):
            seen["connected"] = ws.connected

        ws = client(FakeSession(FakeWs([_text({"type": "playing"})])), on_event)

        await ws._run_connection()

        assert seen["connected"] is True

    async def test_connected_is_false_once_the_socket_closes(self, events):
        ws = client(FakeSession(FakeWs([_text({"type": "playing"})])), events)

        await ws._run_connection()

        assert ws.connected is False

    async def test_a_refused_connection_leaves_connected_false(self, events):
        ws = client(FakeSession(_refused()), events)

        await ws._run_connection()

        assert ws.connected is False

    async def test_a_refused_connection_does_not_raise(self, events):
        """go-librespot not being up yet is the normal state during a start;
        raising here would kill the reconnect loop that is meant to wait it
        out."""
        ws = client(FakeSession(_refused()), events)

        await ws._run_connection()

        assert events.seen == []

    async def test_an_unexpected_failure_does_not_raise_either(self, events):
        ws = client(FakeSession(RuntimeError("tls exploded")), events)

        await ws._run_connection()

        assert ws.connected is False


class TestTheReconcileOnEveryConnection:
    """`on_connect` — the callback that exists because the daemon says nothing.

    go-librespot emits events on *change* only, so a daemon that was restarted
    by systemd while a track sat paused publishes nothing at all on the new
    socket. Without this the Spotify card keeps whatever it last showed.
    """

    async def test_the_reconcile_runs_on_connection(self, events):
        called = []

        async def on_connect():
            called.append(True)

        await client(FakeSession(FakeWs([])), events, on_connect)._run_connection()

        assert called == [True]

    async def test_the_reconcile_runs_before_any_frame_is_dispatched(self, events):
        """It sets the ground truth the incoming frames then amend; running it
        after a frame would overwrite the newer state with the older /status."""
        order = []

        async def on_connect():
            order.append("reconcile")

        async def on_event(event):
            order.append("frame")

        ws = client(FakeSession(FakeWs([_text({"type": "playing"})])), on_event,
                    on_connect)

        await ws._run_connection()

        assert order == ["reconcile", "frame"]

    async def test_it_runs_again_on_every_reconnection(self, events):
        """The whole point is the *re*connection — a reconcile that only ran on
        the first connect would leave the state stale after exactly the daemon
        restart it exists for."""
        called = []

        async def on_connect():
            called.append(True)

        ws = client(FakeSession(FakeWs([])), events, on_connect)

        await ws._run_connection()
        await ws._run_connection()

        assert called == [True, True]

    async def test_a_reconcile_that_throws_does_not_close_the_connection(self, events):
        """The reconcile talks to the daemon's HTTP API, which can be slower to
        come up than its WebSocket. Losing the socket over that would drop the
        feed for a transient."""
        async def on_connect():
            raise RuntimeError("status not ready")

        ws = client(FakeSession(FakeWs([_text({"type": "playing"})])), events,
                    on_connect)

        await ws._run_connection()

        assert [e["type"] for e in events.seen] == ["playing"]

    async def test_no_reconcile_is_configured_and_nothing_breaks(self, events):
        """`on_connect` is optional in the signature; a caller that passes none
        must still get its frames."""
        ws = client(FakeSession(FakeWs([_text({"type": "playing"})])), events, None)

        await ws._run_connection()

        assert len(events.seen) == 1


class TestFrameHandling:
    async def test_a_frame_that_is_not_json_costs_only_that_frame(self, events):
        """A single malformed frame must not take the feed down: the next one
        still has to arrive, or one bad payload deafens Spotify until the
        source is restarted."""
        bad = aiohttp.WSMessage(aiohttp.WSMsgType.TEXT, "not json", None)
        ws = client(FakeSession(FakeWs([bad, _text({"type": "playing"})])), events)

        await ws._run_connection()

        assert [e["type"] for e in events.seen] == ["playing"]

    async def test_a_handler_that_throws_costs_only_that_frame(self, events):
        seen = []

        async def on_event(event):
            seen.append(event)
            if len(seen) == 1:
                raise RuntimeError("bad metadata")

        ws = client(FakeSession(FakeWs([
            _text({"type": "metadata"}), _text({"type": "playing"}),
        ])), on_event)

        await ws._run_connection()

        assert [e["type"] for e in seen] == ["metadata", "playing"]

    async def test_an_error_frame_ends_the_connection(self, events):
        """The loop above reopens the socket; carrying on over an errored one
        reads frames from a link the library has already given up on."""
        ws = client(FakeSession(FakeWs(
            [_error(), _text({"type": "playing"})], exception=RuntimeError("ws")
        )), events)

        await ws._run_connection()

        assert events.seen == []

    async def test_a_closed_frame_ends_the_connection(self, events):
        ws = client(FakeSession(FakeWs([_closed(), _text({"type": "playing"})])), events)

        await ws._run_connection()

        assert events.seen == []

    async def test_a_frame_arriving_during_a_stop_is_dropped(self, events):
        """`stop()` sets the flag before cancelling; a frame already buffered
        would otherwise reach a source that is being torn down."""
        ws = client(FakeSession(FakeWs([_text({"type": "playing"})])), events)
        ws._stopping = True

        await ws._run_connection()

        assert events.seen == []


class TestTheReconnectLoop:
    async def test_a_connection_that_ends_is_reopened(self, events, monkeypatch):
        """go-librespot is `Restart=always`. A loop that gave up after one
        connection would leave Spotify permanently deaf after any daemon
        restart, with the source still reporting itself started."""
        waits = []

        async def no_wait(delay):
            waits.append(delay)
            if len(waits) >= 3:
                raise asyncio.CancelledError

        monkeypatch.setattr(
            "backend.sources.spotify.websocket.asyncio.sleep", no_wait
        )
        session = FakeSession(FakeWs([]))
        ws = client(session, events)

        with pytest.raises(asyncio.CancelledError):
            await ws._connection_loop()

        assert len(session.connects) == 3

    async def test_a_reconnection_waits_before_retrying(self, events, monkeypatch):
        """Without the pause, a daemon that is down turns this into a busy loop
        on a Pi that is also decoding audio."""
        waits = []

        async def no_wait(delay):
            waits.append(delay)
            raise asyncio.CancelledError

        monkeypatch.setattr(
            "backend.sources.spotify.websocket.asyncio.sleep", no_wait
        )
        ws = client(FakeSession(_refused()), events)

        with pytest.raises(asyncio.CancelledError):
            await ws._connection_loop()

        assert waits == [2.0]

    async def test_a_connection_that_raises_is_logged_and_retried(
        self, events, monkeypatch
    ):
        """`_run_connection` swallows its own failures, so this arm only fires
        on something it could not — and the loop must survive that too."""
        waits = []

        async def no_wait(delay):
            waits.append(delay)
            raise asyncio.CancelledError

        monkeypatch.setattr(
            "backend.sources.spotify.websocket.asyncio.sleep", no_wait
        )
        ws = client(FakeSession(FakeWs([])), events)

        async def boom():
            raise RuntimeError("unexpected")

        ws._run_connection = boom

        with pytest.raises(asyncio.CancelledError):
            await ws._connection_loop()

        assert waits == [2.0]

    async def test_stopping_ends_the_loop_without_reconnecting(self, events):
        """`stop()` sets the flag and cancels; the flag alone must already end
        the loop, or a stopped source keeps reopening the socket behind a
        source that is being torn down.

        Run as a task and probed with `done()`, not awaited: `_connection_loop`
        catches `CancelledError` and returns normally, so `wait_for` cannot
        bound it (the 20th blind spot) — and a loop that ignored `_stopping`
        would spin here for as long as the test was willing to wait rather than
        fail. Measured: awaiting it directly is what turned that mutation into
        a hung run instead of a red one."""
        session = FakeSession(FakeWs([]))
        ws = client(session, events)
        ws._stopping = True

        task = asyncio.create_task(ws._connection_loop())
        try:
            for _ in range(5):
                if task.done():
                    break
                await asyncio.sleep(0)

            assert task.done()
            assert session.connects == []
        finally:
            task.cancel()


class TestStartAndStop:
    async def test_start_clears_the_stop_flag_left_by_its_own_cleanup(self, events):
        """`start()` calls `stop()` first, and `stop()` sets `_stopping`. If the
        flag were cleared before that call instead of after, every start would
        produce a loop that exits on its first check — a socket that opens
        nothing, with no error anywhere."""
        session = FakeSession(FakeWs([]))
        ws = client(session, events)

        await ws.start()
        try:
            for _ in range(5):
                if session.connects:
                    break
                await asyncio.sleep(0)

            assert ws._stopping is False
            assert session.connects
        finally:
            await ws.stop()

    async def test_starting_twice_leaves_one_task(self, events):
        """A second start without the cleanup leaves the first loop reconnecting
        forever against the same daemon — two feeds into one source."""
        ws = client(FakeSession(FakeWs([])), events)

        await ws.start()
        first = ws._task
        await ws.start()
        try:
            assert first.done()
            assert ws._task is not first
        finally:
            await ws.stop()

    async def test_stop_cancels_the_loop_and_drops_it(self, events):
        ws = client(FakeSession(FakeWs([])), events)
        await ws.start()
        task = ws._task

        await ws.stop()

        assert task.done()
        assert ws._task is None
        assert ws.connected is False

    async def test_stopping_a_client_that_never_started_is_harmless(self, events):
        """`start()` calls it unconditionally, so this is the common case."""
        ws = client(FakeSession(FakeWs([])), events)

        await ws.stop()

        assert ws._task is None
