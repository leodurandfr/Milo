# backend/tests/test_mpv_controller.py
"""
Unit tests for MpvController: its connect budget, who owns the IPC link, and
the frames its commands put on that link.

- connect() runs inside _do_start for the four mpv sources, which itself runs
  under AudioStateMachine.TRANSITION_TIMEOUT. It used to retry a fixed number of
  times, so a socket that existed but never answered cost max_retries × the full
  command deadline (~55s) under a 10s caller budget — the transition timed out
  and the whole source switch was reset. TestConnectBudget guards that the budget
  is time-bounded, not attempt-bounded.
- A property read used to re-open the link on its own. TestLinkOwnership guards
  that it no longer does, that a stale link is *visible* without a round-trip,
  and that starting playback still re-attaches.
- This is the only file that drives the real controller: Radio, Podcast, CD and
  Music Library all swap it for a Mock, which is right for a collaborator but
  leaves its command surface unwatched — eleven public methods could each be
  replaced by a constant with the whole backend suite green. The classes from
  TestTransportCommands down pin what every one of them sends, against the same
  Unix-socket fake, because a renamed property or an inverted boolean here is
  wrong audio on four sources at once and on nothing else.
"""
import asyncio
import json
import logging
import os
import re
import time
from unittest.mock import AsyncMock, Mock, patch

import pytest

from backend.core.log_handler import WebSocketLogHandler
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
        self.properties = {}
        self.fail_commands = set()
        # Async event lines emitted before each reply, the way mpv bursts
        # `playback-restart` / `file-loaded` during a stream load. They carry no
        # request_id, which is what the reader has to skip past.
        self.events_before_reply = []
        # Commands answered by hanging up mid-request instead of replying.
        self.close_on = set()
        # Commands the fake never answers at all.
        self.silent_on = set()
        self._server = None
        self._peers = []

    def _reply(self, command):
        """What mpv answers for one command frame."""
        if command[0] in self.fail_commands:
            return {"error": "unsupported format"}
        if command[0] == "get_property":
            return {"error": "success", "data": self._read(command[1])}
        return {"error": "success", "data": 0}

    def _read(self, name):
        """The value mpv holds for a property.

        A list is a *script*: one value per read, sticking on the last, which is
        how a playhead that only starts moving on the third poll is expressed.
        No property under test is genuinely list-valued.
        """
        if name not in self.properties:
            return 0
        value = self.properties[name]
        if isinstance(value, list):
            return value.pop(0) if len(value) > 1 else value[0]
        return value

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
                name = request["command"][0]
                if name in self.close_on:
                    writer.close()
                    return
                if name in self.silent_on:
                    continue
                for event in self.events_before_reply:
                    writer.write((json.dumps(event) + "\n").encode())
                reply = self._reply(request["command"])
                reply["request_id"] = request["request_id"]
                writer.write((json.dumps(reply) + "\n").encode())
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


@pytest.fixture
async def live_mpv(tmp_path):
    """A connected controller and the mpv it talks to, with connect()'s own
    frames cleared so a test sees only what its command sent."""
    fake = FakeMpv(tmp_path / "ipc.sock")
    await fake.start()
    controller = MpvController(ipc_socket_path=fake.path)
    assert await controller.connect(timeout=2.0, retry_delay=0.1) is True
    fake.received.clear()
    yield controller, fake
    await fake.stop()


class TestConnectBudget:
    """connect() must give up inside its timeout, whatever mpv is doing.

    On the margin these budgets carry, since a wall-clock assertion on the
    appliance is a fair thing to be suspicious of: `can_retry` refuses to start
    an attempt it cannot afford to finish, so the loop returns at least
    `retry_delay + PROBE_TIMEOUT` -- 1.1s -- before the deadline, by
    construction and not by luck. Both tests below measure 0.90s against a 2.0s
    bound. Failing one would take losing more than a second of scheduling
    inside a 0.9s window, and by then the appliance has worse problems.

    The bound is still the weaker half of what is asserted, so the case that
    matters -- an attempt-counter implementation, which is the regression these
    were written for -- is also pinned without the clock, on the probe count.
    """

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
        # And the same thing without the clock: more than one probe proves the
        # loop retried at all, fewer than five proves it stopped on the deadline
        # rather than on an attempt count. The 2s budget affords two probes; the
        # counter this replaced took ten whatever the caller asked for.
        assert 1 < mock_cmd.await_count < 5

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


class TestGiveUpIsNotABanner:
    """A start that ran out of patience must not raise a UI banner of its own.

    `backend.shared.mpv` is the one logger on the mpv start path that sits under
    the `backend` hierarchy, and main.py attaches WebSocketLogHandler(ERROR)
    there — the source's own logger is rooted at `source` and reaches nothing.
    So an ERROR from connect() is a raw log line racing the typed
    SystemErrorEvent the state machine already broadcasts for the same failure,
    for App.vue's single banner slot. Measured on the unit: mpv publishes its
    IPC socket 0.27s after exec warm and 1.08s with its 51 MB of libraries
    evicted, but under concurrent reads of the same SD card that stretches past
    7s — so a boot where this budget runs out is a slow start, not a broken one,
    and the second attempt succeeds.

    The handler is the real one and the state machine is the fake, so what these
    assert is what a viewer would have seen.
    """

    @staticmethod
    async def _banners(coro):
        """Run `coro` with main.py's banner wiring in place; return the banners."""
        raised = []

        class FakeStateMachine:
            async def broadcast(self, event):
                raised.append(event.message)

        handler = WebSocketLogHandler(level=logging.ERROR)
        handler.set_state_machine(FakeStateMachine())
        backend_logger = logging.getLogger("backend")
        backend_logger.addHandler(handler)
        try:
            result = await coro
            for _ in range(10):      # let the handler's spawned broadcasts run
                await asyncio.sleep(0)
            return result, raised
        finally:
            backend_logger.removeHandler(handler)
            await handler._bg.cancel_all()

    @pytest.mark.asyncio
    async def test_running_out_of_patience_raises_no_banner(self, controller, caplog):
        """The socket never appears: the state machine reports it, not the log."""
        with caplog.at_level(logging.WARNING, logger="backend.shared.mpv"):
            result, banners = await self._banners(
                controller.connect(timeout=1.0, retry_delay=0.2)
            )

        assert result is False
        assert banners == []
        # Still recorded, and still above errors.log's WARNING floor: demoting
        # it must not make a failing start invisible to whoever debugs the boot.
        gave_up = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(gave_up) == 1
        assert controller.ipc_socket_path in gave_up[0].getMessage()

    @pytest.mark.asyncio
    async def test_the_give_up_says_how_long_it_waited(self, controller, caplog):
        """The elapsed time is the only evidence a budget could be re-sized from.

        It exists nowhere else: a failing boot leaves the journal and errors.log,
        and neither says whether mpv was one second short or ten.
        """
        budget, retry_delay = 2.0, 0.2
        with caplog.at_level(logging.WARNING, logger="backend.shared.mpv"):
            assert await controller.connect(budget, retry_delay) is False

        message = caplog.records[-1].getMessage()
        waited = float(re.search(r"in ([\d.]+)s", message).group(1))
        # Inside the budget it was given, and past its first poll: a give-up
        # reporting 0.0s would be reporting the clock rather than the wait.
        assert retry_delay < waited <= budget

    @pytest.mark.asyncio
    async def test_an_unexpected_fault_still_raises_one(self, controller):
        """The demotion covers patience, not breakage.

        A connect that dies on something nobody predicted is a real fault, and
        the arm that reports it must keep reaching the banner — otherwise this
        change trades a false alarm for a silent one.
        """
        with patch("backend.shared.mpv.Path", side_effect=RuntimeError("boom")):
            result, banners = await self._banners(controller.connect(timeout=1.0))

        assert result is False
        assert len(banners) == 1
        assert "boom" in banners[0]


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


class TestTransportCommands:
    """The frame each transport command puts on the socket.

    Every one of these could be replaced by `return False` with the whole
    backend suite green: the four mpv sources swap the controller for a Mock
    (correct — it is a collaborator), so nothing else exercises the real one. A
    renamed property, an inverted boolean or a swapped argument shows up only
    as wrong audio, on four sources at once.
    """

    @pytest.mark.asyncio
    async def test_pause_sets_the_pause_property(self, live_mpv):
        controller, fake = live_mpv
        assert await controller.pause() is True
        assert fake.received == [["set_property", "pause", True]]

    @pytest.mark.asyncio
    async def test_resume_clears_it(self, live_mpv):
        """The one bit that separates the two commands."""
        controller, fake = live_mpv
        assert await controller.resume() is True
        assert fake.received == [["set_property", "pause", False]]

    @pytest.mark.asyncio
    async def test_seek_is_absolute(self, live_mpv):
        """mpv reads the flag as the second argument: swapped, a jump to 42s
        becomes a 42s jump *forward* from wherever the playhead was."""
        controller, fake = live_mpv
        assert await controller.seek(42.5) is True
        assert fake.received == [["seek", 42.5, "absolute"]]

    @pytest.mark.asyncio
    async def test_stop_is_one_frame(self, live_mpv):
        controller, fake = live_mpv
        assert await controller.stop() is True
        assert fake.received == [["stop"]]

    @pytest.mark.asyncio
    async def test_an_mpv_error_is_a_failure_not_a_success(self, live_mpv):
        """The frame left the process and mpv refused it. Callers gate state on
        the return value, so a refusal that reads as success is a UI showing a
        transport that never happened."""
        controller, fake = live_mpv
        fake.fail_commands.add("stop")

        assert await controller.stop() is False
        assert fake.received == [["stop"]]


class TestPropertyReads:
    """What a read gives back, and what it refuses to invent."""

    @pytest.mark.asyncio
    async def test_get_property_returns_what_mpv_holds(self, live_mpv):
        controller, fake = live_mpv
        fake.properties["volume"] = 87.5

        assert await controller.get_property("volume") == 87.5
        assert fake.received == [["get_property", "volume"]]

    @pytest.mark.asyncio
    async def test_a_refused_read_is_not_a_dead_link(self, live_mpv):
        """mpv refuses a property it does not currently have — `chapter` on a
        stream, `playlist-count` before a queue exists — several times a minute
        on the monitor tick. That is an answer, not a socket failure: tearing the
        link down here would drop every later command until a play command
        re-attached, on a link that was never broken."""
        controller, fake = live_mpv
        fake.fail_commands.add("get_property")

        assert await controller.get_property("chapter") is None
        assert controller.is_connected is True

        fake.fail_commands.clear()
        fake.properties["volume"] = 12.0
        assert await controller.get_property("volume") == 12.0

    @pytest.mark.asyncio
    async def test_is_playing_is_the_existence_of_a_playhead(self, live_mpv):
        """playback-time exists from the first decoded frame and stays at 0 for a
        whole buffer's worth of it, then disappears when playback ends. Both
        edges matter: `> 0` calls a just-started stream stopped, and anything
        looser calls a finished one playing — the four mpv sources hang their
        auto-stop off this."""
        controller, fake = live_mpv
        fake.properties["playback-time"] = 0.0

        assert await controller.is_playing() is True
        assert fake.received == [["get_property", "playback-time"]]

        fake.properties["playback-time"] = None
        assert await controller.is_playing() is False

    @pytest.mark.asyncio
    async def test_metadata_keys_are_lowercased_and_values_are_strings(self, live_mpv):
        """Readers index `icy-title` / `icy-name`; mpv's casing follows the
        stream's tags, and HLS surfaces numeric tags the readers would choke on."""
        controller, fake = live_mpv
        fake.properties["metadata"] = {
            "icy-title": "Artist - Song",
            "ICY-NAME": "Some Radio",
            "track": 7,
        }

        assert await controller.get_metadata() == {
            "icy-title": "Artist - Song",
            "icy-name": "Some Radio",
        }
        assert fake.received == [["get_property", "metadata"]]

    @pytest.mark.asyncio
    async def test_metadata_is_empty_when_mpv_reports_none(self, live_mpv):
        """A stream with no tags answers None, not a dict — callers iterate the
        result without checking."""
        controller, fake = live_mpv
        fake.properties["metadata"] = None

        assert await controller.get_metadata() == {}


class TestWaitUntilAdvancing:
    """A loaded file is not a moving playhead."""

    @pytest.mark.asyncio
    async def test_it_waits_for_the_playhead_to_move(self, live_mpv):
        """mpv's audio output takes up to ~1s to start after an unpause, and
        time-pos sits at 0 throughout: returning on the first read is what let a
        progress bar run ahead of silence."""
        controller, fake = live_mpv
        fake.properties["time-pos"] = [0, 0, 2.5]

        assert await controller.wait_until_advancing(timeout=2.0, poll_interval=0.01) is True
        assert len(fake.received) >= 3

    @pytest.mark.asyncio
    async def test_it_gives_up_on_a_stalled_source(self, live_mpv):
        """Bounded, so a source that never starts cannot hang the caller."""
        controller, fake = live_mpv
        fake.properties["time-pos"] = 0

        assert await controller.wait_until_advancing(timeout=0.2, poll_interval=0.01) is False


class TestPlaylistEdits:
    """The gapless queue: what a jump and a re-shuffle put on the socket."""

    @pytest.mark.asyncio
    async def test_set_playlist_pos_jumps_by_index(self, live_mpv):
        controller, fake = live_mpv
        assert await controller.set_playlist_pos(3) is True
        assert fake.received == [["set_property", "playlist-pos", 3]]

    @pytest.mark.asyncio
    async def test_replacing_the_tail_leaves_the_head_and_appends_in_order(self, live_mpv):
        """Removal runs from the end down so the indices it is walking do not
        shift under it, and the entry playing (inside the kept head) is never
        reloaded — that is what makes the live shuffle toggle inaudible.
        """
        controller, fake = live_mpv
        fake.properties["playlist-count"] = 5

        assert await controller.replace_playlist_tail(
            2, ["http://example.test/x", "http://example.test/y"]
        ) is True

        assert fake.received == [
            ["get_property", "playlist-count"],
            ["playlist-remove", 4],
            ["playlist-remove", 3],
            ["playlist-remove", 2],
            ["loadfile", "http://example.test/x", "append"],
            ["loadfile", "http://example.test/y", "append"],
        ]

    @pytest.mark.asyncio
    async def test_an_unreadable_playlist_removes_nothing(self, live_mpv):
        """Without the length there is no tail to identify; guessing would drop
        entries the user is still queued to hear."""
        controller, fake = live_mpv
        fake.properties["playlist-count"] = None

        assert await controller.replace_playlist_tail(2, ["http://example.test/x"]) is False
        assert fake.received == [["get_property", "playlist-count"]]


class TestDisconnect:
    """disconnect() ends the link, it does not merely forget it."""

    @pytest.mark.asyncio
    async def test_the_link_is_down_and_commands_stop_leaving(self, live_mpv):
        """Called from _send_command's own error paths and from source cleanup.
        A disconnect that left the state half-set would leave is_connected True,
        and every later command would be written into a dead socket instead of
        being dropped for ensure_connected() to repair."""
        controller, fake = live_mpv

        await controller.disconnect()

        assert controller.is_connected is False
        assert await controller.stop() is False
        assert fake.received == []


class TestResponseMatching:
    """The read loop inside `_send_command`, which had never run.

    mpv interleaves async event lines — no `request_id` — on the same socket as
    the replies, and bursts them during a stream load or a rapid station change.
    Every frame the loop skips, every way it can end, and the reason the bound is
    a wall-clock deadline rather than a line count: with a count, a burst longer
    than the count loses the reply that came right after it.
    """

    async def test_events_before_the_reply_are_skipped_not_returned(self, live_mpv):
        """A reader that returned the first line would hand `playback-restart`
        back as the answer to `loadfile`, and every command during a station
        change would read as a failure with no error anywhere.
        """
        controller, fake = live_mpv
        fake.events_before_reply = [
            {"event": "start-file"},
            {"event": "playback-restart"},
            {"event": "file-loaded"},
        ]

        response = await controller._send_command("get_property", "idle-active")

        assert response is not None
        assert response.get("error") == "success"
        assert "event" not in response

    async def test_the_reply_is_matched_by_its_own_request_id(self, live_mpv):
        """Two commands in flight cannot cross: `_command_lock` serialises them,
        but a reply to an earlier, timed-out request can still be sitting in the
        buffer. Matched by position instead of id, a station change would answer
        with the previous station's result.
        """
        controller, fake = live_mpv
        fake.events_before_reply = [{"request_id": 999, "error": "success", "data": "stale"}]

        response = await controller._send_command("get_property", "idle-active")

        assert response["request_id"] != 999
        assert response["data"] != "stale"

    async def test_a_socket_closed_mid_request_drops_the_link_quietly(
        self, live_mpv, caplog
    ):
        """mpv dying between the write and the reply is what a restart looks like.

        Without the disconnect the controller keeps `_connected` True over a dead
        writer, and every later command is written into nothing and times out —
        each one paying the full deadline.

        The level is the other half, and the only thing that separates this arm
        from the generic one below it: drop the empty-line check and `json.loads`
        raises a JSONDecodeError on `b""`, which the outer `except Exception`
        catches and reports at ERROR. Same return, same disconnect — but ERROR
        reaches the `WebSocketLogHandler` banner, so a routine mpv restart would
        put a red banner in front of the user on every source switch.
        """
        controller, fake = live_mpv
        fake.close_on = {"get_property"}

        # Named logger, not the root: `backend/main.py` raises
        # `backend.shared.mpv` to INFO at import time so mpv playback lines reach
        # the journal, and a logger-level floor is applied before any handler.
        # A bare `caplog.at_level(DEBUG)` therefore captures nothing here — and
        # only once main.py has been imported, which is to say only in the full
        # suite and never when this file is run alone.
        with caplog.at_level(logging.DEBUG, logger="backend.shared.mpv"):
            assert await controller._send_command("get_property", "idle-active") is None

        assert controller.is_connected is False
        assert "mpv socket closed while awaiting request" in caplog.text
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR], \
            "a routine mpv restart was reported at ERROR, which reaches the UI banner"

    async def test_a_reply_that_never_comes_costs_its_deadline_and_no_more(
        self, live_mpv, caplog
    ):
        """The bound must be the deadline, not the reader: `readline()` on a live
        socket with no data blocks forever, so nothing else ends this.

        Two inert guards sit on this path and are recorded rather than removed,
        because a mutation of either is indistinguishable from the original:

        * `if timeout <= 0: raise asyncio.TimeoutError` — `asyncio.wait_for`
          raises `TimeoutError` on a non-positive timeout by itself (measured on
          3.13), so the early exit only saves building one future.
        * `if 'event' in response: continue` — an mpv event carries no
          `request_id`, so the identity check below already rejects every event
          frame and the loop keeps reading either way.

        Both are readable and cost nothing; the note is here so the next reader
        does not mistake them for untested behaviour.
        """
        controller, fake = live_mpv
        fake.silent_on = {"get_property"}

        with caplog.at_level(logging.DEBUG, logger="backend.shared.mpv"):
            assert await controller._send_command(
                "get_property", "idle-active", timeout=0.2
            ) is None

        assert "Timeout waiting for mpv response" in caplog.text

    async def test_a_silent_link_does_not_look_disconnected(self, live_mpv):
        """The control for the test above. A timeout is not a death — mpv can be
        busy opening a slow stream — and dropping the link on one would make
        every slow station change re-connect.
        """
        controller, fake = live_mpv
        fake.silent_on = {"get_property"}

        await controller._send_command("get_property", "idle-active", timeout=0.2)

        assert controller.is_connected is True

    async def test_a_write_that_raises_drops_the_link(self, live_mpv):
        """The outer arm. A writer whose transport is gone raises rather than
        timing out; keeping the link would leave the controller believing in a
        socket the kernel has already reaped.
        """
        controller, fake = live_mpv
        controller.writer.write = Mock(side_effect=OSError("broken pipe"))

        assert await controller._send_command("get_property", "idle-active") is None

        assert controller.is_connected is False


class TestConnectFailureArms:
    """What `connect()` does when the socket is there but the connection is not."""

    async def test_a_refused_socket_is_retried_within_the_budget(self, tmp_path):
        """mpv creates its socket before it is ready to accept, so a refusal at
        boot is normal and transient. Given up on the first one, every mpv source
        would fail to start on a cold boot and the transition would report ERROR.
        """
        fake = FakeMpv(tmp_path / "ipc.sock")
        # A path that exists but nothing is listening on: exactly the window
        (tmp_path / "ipc.sock").write_bytes(b"")
        controller = MpvController(ipc_socket_path=str(tmp_path / "ipc.sock"))

        attempts = {"n": 0}
        real_open = asyncio.open_unix_connection

        async def _open(path):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise ConnectionRefusedError("not accepting yet")
            (tmp_path / "ipc.sock").unlink()
            fake.path = str(tmp_path / "ipc.sock")
            await fake.start()
            return await real_open(path)

        with patch("asyncio.open_unix_connection", _open):
            assert await controller.connect(timeout=3.0, retry_delay=0.05) is True

        assert attempts["n"] == 3
        await fake.stop()

    async def test_a_socket_that_never_accepts_gives_up_and_says_so(
        self, tmp_path, caplog
    ):
        """The budget is time, not attempts (see TestConnectBudget above); this
        is the arm it lands on. Answered True, `_do_start` would report the source
        started over a controller with no link.

        Warning, not error: see TestGiveUpIsNotABanner for why every give-up on
        this path sits below the banner threshold.
        """
        (tmp_path / "ipc.sock").write_bytes(b"")
        controller = MpvController(ipc_socket_path=str(tmp_path / "ipc.sock"))

        async def _refuse(path):
            raise ConnectionRefusedError("nothing there")

        with patch("asyncio.open_unix_connection", _refuse):
            with caplog.at_level(logging.WARNING):
                assert await controller.connect(timeout=0.3, retry_delay=0.05) is False

        assert "Failed to connect to mpv in" in caplog.text

    async def test_an_unexpected_error_is_not_retried(self, tmp_path, caplog):
        """Only a refusal and a missing file are transient. Retrying a
        programming error would spend the whole transition budget on it and then
        report the same failure anyway.
        """
        (tmp_path / "ipc.sock").write_bytes(b"")
        controller = MpvController(ipc_socket_path=str(tmp_path / "ipc.sock"))
        attempts = {"n": 0}

        async def _boom(path):
            attempts["n"] += 1
            raise RuntimeError("bad socket type")

        with patch("asyncio.open_unix_connection", _boom):
            with caplog.at_level(logging.ERROR):
                assert await controller.connect(timeout=3.0, retry_delay=0.05) is False

        assert attempts["n"] == 1
        assert "Unexpected error connecting to mpv" in caplog.text


class TestLoadStreamVerdicts:
    """`load_stream` decides whether a station is playing. Its three False arms
    had never run, and the difference between them is what the UI shows."""

    async def test_a_down_link_refuses_before_touching_the_stream_options(
        self, tmp_path
    ):
        """`_apply_stream_options` always issues a round-trip; on a link that
        died since the last command, that round-trip is the one that discovers
        the death, and every command after it is dropped. Checking first is the
        documented order.
        """
        controller = MpvController(ipc_socket_path=str(tmp_path / "gone.sock"))

        assert await controller.load_stream("http://example.invalid/s.mp3") is False

    async def test_the_link_is_re_attached_before_the_stream_options_are_sent(
        self, live_mpv
    ):
        """Order, not presence. `_apply_stream_options` always issues a
        round-trip, and on a link that dropped since the last command that
        round-trip is the one that discovers the death — it is answered by the
        "link down, dropping" arm and never leaves the process. Run before the
        reconnect, the HLS switch is silently lost for this load and the stream
        hangs in "loading" with the unit's reconnect options still in force.

        Both orders end in a successful load, so only the frame reaching mpv
        separates them.
        """
        controller, fake = live_mpv
        await fake.drop_peers()
        fake.received.clear()

        assert await controller.load_stream("https://example.invalid/live.m3u8") is True

        assert _first(fake.received, "set_property") >= 0, \
            "the stream options never reached mpv: they were sent over the dead link"
        assert _first(fake.received, "set_property") < _first(fake.received, "loadfile")

    async def test_a_loadfile_that_answers_nothing_is_a_failure(self, live_mpv, caplog):
        """None means the link went away mid-command, not that mpv refused.

        Read as success, the source publishes ACTIVE over a station that never
        loaded, and the card shows a track that is not playing.
        """
        controller, fake = live_mpv
        fake.silent_on = {"loadfile"}

        with caplog.at_level(logging.INFO):
            assert await controller.load_stream("http://example.invalid/s.mp3") is False

        assert "loadfile returned None" in caplog.text

    async def test_a_real_mpv_error_is_a_failure_and_is_logged_loudly(
        self, live_mpv, caplog
    ):
        """A dead stream URL answers with an error string. That is the case the
        user can act on — it is the station, not the appliance — so it is the one
        arm that logs at error.
        """
        controller, fake = live_mpv
        fake.fail_commands = {"loadfile"}

        with caplog.at_level(logging.ERROR):
            assert await controller.load_stream("http://example.invalid/s.mp3") is False

        assert "loadfile failed with error" in caplog.text

    @pytest.mark.parametrize("error", [None, "null", "property unavailable"])
    async def test_the_transient_errors_of_a_fast_station_change_still_succeed(
        self, live_mpv, error
    ):
        """These three arrive when stations are changed quickly, and the load does
        happen. Treated as failures, every fast zap would show an error banner.
        """
        controller, fake = live_mpv
        controller._send_command = AsyncMock(return_value={"error": error})

        assert await controller.load_stream("http://example.invalid/s.mp3") is True


class TestStreamOptions:
    """The HLS reconnect switch — two lines that decide whether a stream hangs."""

    async def test_an_hls_url_has_the_reconnect_options_cleared(self, live_mpv):
        """The systemd unit sets `--stream-lavf-o=reconnect=...`, which makes the
        HLS demuxer's own segment retries fight ffmpeg's: the stream hangs in
        "loading" forever. Clearing them for .m3u8 is the whole fix.
        """
        controller, fake = live_mpv

        await controller._apply_stream_options("https://example.invalid/live.m3u8")

        sets = [f for f in fake.received if f[0] == "set_property" and f[1] == "stream-lavf-o"]
        assert sets and sets[-1][2] == ""

    async def test_an_empty_url_touches_nothing(self, live_mpv):
        """`load_playlist` and the resume path can both reach here with nothing.

        A set_property issued anyway would clear the launch defaults for the
        stream that follows, and that stream would then never reconnect.
        """
        controller, fake = live_mpv

        await controller._apply_stream_options("")

        assert not [f for f in fake.received if f[0] == "set_property"]


class TestPlaylistFailureArms:
    """`load_playlist` / `replace_playlist_tail` — the gapless queue's refusals."""

    async def test_an_empty_queue_is_refused_without_a_round_trip(self, live_mpv):
        controller, fake = live_mpv

        assert await controller.load_playlist([]) is False

        assert fake.received == []

    async def test_a_down_link_refuses_before_the_priming_pause(self, tmp_path):
        """The pause is what stops entry 0 blipping before the jump to
        `start_index`. Dropped on a down link, the queue loads unpaused — audibly,
        with nothing reporting a failure.
        """
        controller = MpvController(ipc_socket_path=str(tmp_path / "gone.sock"))

        assert await controller.load_playlist(["http://a.invalid/1.mp3"]) is False

    async def test_a_first_entry_that_will_not_load_aborts_the_queue(self, live_mpv):
        """Appending onto a failed first entry builds a playlist whose head is
        missing; mpv then plays entry 1 while Milō's queue index says 0, and
        every title after it is off by one."""
        controller, fake = live_mpv
        fake.fail_commands = {"loadfile"}

        assert await controller.load_playlist(
            ["http://a.invalid/1.mp3", "http://a.invalid/2.mp3"]
        ) is False

    async def test_one_failed_append_does_not_abort_the_rest_of_the_queue(
        self, live_mpv, caplog
    ):
        """A queue is dozens of tracks; one unreachable URL must cost that track,
        not the album. Aborting would leave a partial playlist that looks
        complete in the UI.
        """
        controller, fake = live_mpv
        calls = {"n": 0}
        real_send = controller._send_command

        async def _send(command, *args, **kwargs):
            if command == "loadfile" and args[1:] == ("append",):
                calls["n"] += 1
                if calls["n"] == 1:
                    return None
            return await real_send(command, *args, **kwargs)

        controller._send_command = _send

        with caplog.at_level(logging.WARNING):
            assert await controller.load_playlist([
                "http://a.invalid/1.mp3", "http://a.invalid/2.mp3", "http://a.invalid/3.mp3",
            ]) is True

        assert calls["n"] == 2
        assert "playlist append failed for an entry" in caplog.text

    async def test_a_tail_replacement_that_cannot_read_the_length_refuses(
        self, live_mpv
    ):
        """Without the count the removal loop has no upper bound and would leave
        the old tail in place under the new one — the shuffle toggle would then
        double the queue instead of reordering it.
        """
        controller, fake = live_mpv
        controller.get_property = AsyncMock(return_value=None)

        assert await controller.replace_playlist_tail(2, ["http://a.invalid/x.mp3"]) is False

        assert not [f for f in fake.received if f[0] == "playlist-remove"]

    async def test_one_failed_tail_append_is_logged_and_the_rest_go_on(
        self, live_mpv, caplog
    ):
        controller, fake = live_mpv
        controller.get_property = AsyncMock(return_value=3)
        real_send = controller._send_command
        calls = {"n": 0}

        async def _send(command, *args, **kwargs):
            if command == "loadfile":
                calls["n"] += 1
                if calls["n"] == 1:
                    return None
            return await real_send(command, *args, **kwargs)

        controller._send_command = _send

        with caplog.at_level(logging.WARNING):
            assert await controller.replace_playlist_tail(
                1, ["http://a.invalid/x.mp3", "http://a.invalid/y.mp3"]
            ) is True

        assert calls["n"] == 2
        assert "playlist tail append failed for an entry" in caplog.text
