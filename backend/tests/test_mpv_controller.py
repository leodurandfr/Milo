# backend/tests/test_mpv_controller.py
"""
Unit tests for MpvController's connect budget and for who owns the IPC link.

Two subjects, both about a link nobody is answering:

- connect() runs inside _do_start for the four mpv sources, which itself runs
  under AudioStateMachine.TRANSITION_TIMEOUT. It used to retry a fixed number of
  times, so a socket that existed but never answered cost max_retries × the full
  command deadline (~55s) under a 10s caller budget — the transition timed out
  and the whole source switch was reset. TestConnectBudget guards that the budget
  is time-bounded, not attempt-bounded.
- A property read used to re-open the link on its own. TestLinkOwnership guards
  that it no longer does, that a stale link is *visible* without a round-trip,
  and that starting playback still re-attaches.
"""
import asyncio
import json
import os
import time
from unittest.mock import AsyncMock, Mock, patch

import pytest

from backend.shared.mpv import MpvController


@pytest.fixture
def controller():
    return MpvController(ipc_socket_path="/nonexistent/milo-test-ipc.sock")


class FakeMpv:
    """Real Unix-socket stand-in for mpv's JSON IPC.

    Answers every request with error=success and records both the accepted
    connections and the command frames it received, so "did a read re-open the
    link" and "did the loadfile actually leave the process" are observable
    without measuring elapsed time. Every value a test asserts is produced here,
    never written by the test.

    drop_peers() kills the client connections but keeps listening — the shape
    where a reconnect *would* succeed, so a reconnect that happens is countable.
    stop() also unlinks the path so a replacement can take it over, which is what
    systemd does after RestartSec.
    """

    def __init__(self, path):
        self.path = str(path)
        self.connections = 0
        self.received = []
        self._server = None
        self._peers = []

    async def start(self):
        self._server = await asyncio.start_unix_server(self._serve, self.path)

    async def _serve(self, reader, writer):
        self.connections += 1
        self._peers.append(writer)
        try:
            while True:
                line = await reader.readline()
                if not line:
                    return
                request = json.loads(line)
                self.received.append(request["command"])
                writer.write(
                    (
                        json.dumps(
                            {
                                "error": "success",
                                "data": 0,
                                "request_id": request["request_id"],
                            }
                        )
                        + "\n"
                    ).encode()
                )
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            return

    async def drop_peers(self):
        for writer in self._peers:
            writer.close()
        self._peers.clear()
        await _settle()

    async def stop(self):
        await self.drop_peers()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if os.path.exists(self.path):
            os.unlink(self.path)


async def _settle():
    """Let the event loop deliver the transport callbacks (eof_received).

    Not a timing budget: the peer's death is delivered by the loop, not by a
    read, and that delivery is the whole point of these tests.
    """
    await asyncio.sleep(0.05)


def _first(frames, name):
    """Index of the first frame whose command is `name`, or -1."""
    for index, frame in enumerate(frames):
        if frame and frame[0] == name:
            return index
    return -1


class TestConnectBudget:
    """connect() must give up inside its timeout, whatever mpv is doing."""

    @pytest.mark.asyncio
    async def test_missing_socket_gives_up_within_timeout(self, controller):
        """Socket never appears: retries, then gives up inside the budget."""
        started = time.monotonic()
        result = await controller.connect(timeout=2.0, retry_delay=0.1)
        elapsed = time.monotonic() - started

        assert result is False
        assert elapsed < 2.0

    @pytest.mark.asyncio
    async def test_unresponsive_mpv_gives_up_within_timeout(self, controller):
        """Socket accepts the connection but mpv never answers the probe.

        The regression case. Each probe is made expensive (a wedged mpv burns
        its whole reply deadline), which is what made the old attempt counter
        unbounded: 10 attempts × the full deadline, no matter the caller's
        budget. A time-bounded loop stops at the deadline instead.
        """
        probe_cost = 0.4

        async def wedged_probe(*args, **kwargs):
            await asyncio.sleep(probe_cost)
            return None

        with patch("backend.shared.mpv.Path") as mock_path, \
             patch("asyncio.open_unix_connection", new_callable=AsyncMock) as mock_open, \
             patch.object(MpvController, "_send_command", side_effect=wedged_probe) as mock_cmd:
            mock_path.return_value.exists.return_value = True
            mock_open.return_value = (Mock(), Mock())

            started = time.monotonic()
            # Budget must exceed retry_delay + PROBE_TIMEOUT, or the loop
            # correctly refuses to start a probe it cannot afford to finish.
            result = await controller.connect(timeout=2.0, retry_delay=0.1)
            elapsed = time.monotonic() - started

        assert result is False
        # An attempt-counter implementation would have run 10 × probe_cost = 4s
        # here regardless of the 2s budget.
        assert elapsed < 2.0
        assert mock_cmd.await_count > 1

    @pytest.mark.asyncio
    async def test_probe_uses_short_timeout(self, controller):
        """The liveness probe must not inherit the full command deadline."""
        from backend.shared.mpv import COMMAND_TIMEOUT, PROBE_TIMEOUT

        assert PROBE_TIMEOUT < COMMAND_TIMEOUT

        with patch("backend.shared.mpv.Path") as mock_path, \
             patch("asyncio.open_unix_connection", new_callable=AsyncMock) as mock_open, \
             patch.object(MpvController, "_send_command", new_callable=AsyncMock) as mock_cmd:
            mock_path.return_value.exists.return_value = True
            mock_open.return_value = (Mock(), Mock())
            mock_cmd.return_value = {"error": "success", "data": False}

            assert await controller.connect(timeout=2.0, retry_delay=0.1) is True

        probe_call = mock_cmd.await_args_list[0]
        assert probe_call.args == ("get_property", "idle-active")
        assert probe_call.kwargs["timeout"] == PROBE_TIMEOUT

    @pytest.mark.asyncio
    async def test_default_budget_fits_a_source_start(self):
        """connect()'s default must leave room under TRANSITION_TIMEOUT."""
        from backend.core.state import AudioStateMachine
        from backend.shared.mpv import CONNECT_TIMEOUT, PROBE_TIMEOUT

        # _start_service_and_wait settles for 0.5s before connect() is called.
        assert CONNECT_TIMEOUT + PROBE_TIMEOUT + 0.5 < AudioStateMachine.TRANSITION_TIMEOUT


class TestLinkOwnership:
    """Reads observe the link; only starting playback re-opens it."""

    @staticmethod
    async def _connected(tmp_path):
        """A live controller on a live fake, ready to have mpv die under it."""
        fake = FakeMpv(tmp_path / "ipc.sock")
        await fake.start()
        controller = MpvController(ipc_socket_path=fake.path)
        assert await controller.connect(timeout=2.0, retry_delay=0.1) is True
        return controller, fake

    @pytest.mark.asyncio
    async def test_a_stale_link_is_seen_without_a_round_trip(self, tmp_path):
        """mpv can die and be replaced with no command in between.

        Three of the four monitor ticks return before issuing any mpv I/O
        (podcast on no episode, music_library on an empty queue, CD when not
        playing), so a source that is active but idle never writes to the socket.
        is_connected gated only on writer.is_closing(), which asyncio leaves
        False after the peer dies (eof_received half-closes the transport) — so
        the link read as up for as long as nobody touched it, and the one act
        that re-opens it saw nothing to repair. Consumers: ensure_connected() on
        every play command, and _monitor_loop's disconnect fallback.
        """
        controller, fake = await self._connected(tmp_path)
        await fake.stop()

        restarted = FakeMpv(fake.path)          # systemd, same socket path
        await restarted.start()
        await _settle()                         # no command issued, ever

        assert controller.is_connected is False
        assert await controller.load_stream("http://example.test/s") is True
        assert _first(restarted.received, "loadfile") >= 0

        await restarted.stop()

    @pytest.mark.asyncio
    async def test_the_priming_pause_survives_a_stale_link(self, tmp_path):
        """load_playlist must not lose the pause that hides entry 0.

        The queue is loaded paused so the first entry cannot blip before the jump
        to start_index. On a stale link that set_property is the round-trip that
        discovers the death, and if the re-attach happens only afterwards the
        queue loads *unpaused* — audibly, with no error anywhere. CD has the same
        shape in _start_reader_and_mpv, where the lost pause means audio during
        the FIFO handshake.
        """
        controller, fake = await self._connected(tmp_path)
        await fake.stop()

        restarted = FakeMpv(fake.path)
        await restarted.start()
        await _settle()

        urls = [f"http://example.test/{n}" for n in range(3)]
        assert await controller.load_playlist(urls, start_index=2) is True

        paused = _first(restarted.received, "set_property")
        loaded = _first(restarted.received, "loadfile")
        assert paused >= 0 and loaded >= 0
        assert restarted.received[paused] == ["set_property", "pause", True]
        assert paused < loaded

        await restarted.stop()

    @pytest.mark.asyncio
    async def test_reads_after_the_link_dies_do_not_reopen_it(self, tmp_path):
        """A property read is an observation, not a repair.

        A read that reconnects can succeed against the *fresh idle* mpv systemd
        restarts 5s later: is_connected then reads True, MpvAudioSource's
        disconnect fallback never fires, and the rest of the tick answers from
        that idle mpv — which is how podcast's `idle_active is True` branch
        persisted a two-minutes-in episode as completed, and how music_library
        raised a false queue_finished.
        """
        controller, fake = await self._connected(tmp_path)
        opened = fake.connections
        await fake.drop_peers()                 # mpv gone; the socket still accepts

        for _ in range(3):
            assert await controller.get_property("time-pos") is None

        assert fake.connections == opened
        assert controller.is_connected is False

        await fake.stop()

    @pytest.mark.asyncio
    async def test_starting_playback_re_attaches_to_a_restarted_mpv(self, tmp_path):
        """The guard on the over-correction, not on the bug — green either way.

        Every explicit connect() is a _do_start step and no _on_mpv_disconnect
        hook reconnects or nulls the controller, so the user's next play lands on
        a dropped link. Without this test, a later "make reads fail fast
        everywhere" edit would trade a stall for a source that never comes back,
        and nothing in the suite would notice.
        """
        controller, fake = await self._connected(tmp_path)
        await fake.stop()

        restarted = FakeMpv(fake.path)
        await restarted.start()
        assert await controller.get_property("time-pos") is None    # link is down
        opened = restarted.connections

        assert await controller.load_stream("http://example.test/s") is True

        assert restarted.connections == opened + 1
        assert _first(restarted.received, "loadfile") >= 0

        await restarted.stop()
