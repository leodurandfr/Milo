# backend/tests/test_mpv_controller.py
"""
Behaviour tests for MpvController, driven against a real Unix-socket fake of
mpv's JSON IPC. Nothing inside the controller is patched: what is asserted is
what reached the socket, what came back to the caller, and what a subscriber
was told.

- One reader task per link routes replies by `request_id` and hands every event
  to the subscribers. Before it, events were read only while a reply was being
  awaited and were thrown away, and a command held a lock for its whole
  round-trip. TestReplyRouting and TestEvents guard the replacement.
- A link is an object, and a new connect makes a new one: that identity is the
  generation token the sources will compare against. TestLinkIdentity.
- connect() runs inside _do_start for the four mpv sources, which itself runs
  under AudioStateMachine.TRANSITION_TIMEOUT. TestConnectBudget guards that its
  budget is time, not attempts.
- Reads observe the link and only a play command re-opens it. TestLinkOwnership.
- This is the only file that drives the real controller: Radio, Podcast, CD and
  Music Library all swap it for a Mock, so the classes from TestTransportCommands
  down pin the frame each public method sends. A renamed property or an inverted
  boolean here is wrong audio on four sources at once and on nothing else.
"""
import asyncio
import json
import logging
import os
import re
import time
from unittest.mock import Mock

import pytest

from backend.core.log_handler import WebSocketLogHandler
from backend.shared.mpv import MpvController


class FakeMpv:
    """Real Unix-socket stand-in for mpv's JSON IPC.

    Answers every request the way mpv 0.40 does on this unit (measured: a
    `loadfile` reply carries `data.playlist_entry_id`, `stop` answers
    `data: null`), and records the accepted connections and every command frame
    it received. Every value a test asserts is produced here, never written by
    the test.

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
        # Whole reply bodies for a command name, for the error shapes mpv sends.
        self.replies = {}
        # Event lines written before each reply, the way mpv interleaves
        # `start-file` / `playback-restart` with the replies of a stream load.
        self.events_before_reply = []
        # Raw bytes written before each reply (a line mpv would never send).
        self.raw_before_reply = []
        # Commands answered by hanging up mid-request instead of replying.
        self.close_on = set()
        # Commands the fake never answers at all.
        self.silent_on = set()
        # `loadfile` frames for these URLs are never answered.
        self.silent_urls = set()
        # Command name -> seconds before its reply is written. The fake keeps
        # reading meanwhile, so later requests can overtake it.
        self.delay_on = {}
        # When set, replies are held until this many requests are in, then
        # written newest first.
        self.reverse_every = 0
        self._held = []
        self._next_entry_id = 0
        self._server = None
        self._peers = []

    def _reply(self, command):
        """What mpv answers for one command frame."""
        name = command[0]
        if name in self.replies:
            return dict(self.replies[name])
        if name in self.fail_commands:
            return {"error": "unsupported format"}
        if name == "get_property":
            return {"error": "success", "data": self._read(command[1])}
        if name == "loadfile":
            self._next_entry_id += 1
            return {"error": "success", "data": {"playlist_entry_id": self._next_entry_id}}
        return {"error": "success", "data": None}

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

    @staticmethod
    def _write(writer, message):
        if not writer.is_closing():
            writer.write((json.dumps(message) + "\n").encode())

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
                command = request["command"]
                self.received.append(command)
                name = command[0]
                if name in self.close_on:
                    writer.close()
                    return
                if name in self.silent_on or (
                    name == "loadfile" and command[1] in self.silent_urls
                ):
                    continue
                for event in self.events_before_reply:
                    self._write(writer, event)
                for raw in self.raw_before_reply:
                    writer.write(raw)
                reply = self._reply(command)
                reply["request_id"] = request["request_id"]
                if self.reverse_every:
                    self._held.append(reply)
                    if len(self._held) == self.reverse_every:
                        for held in reversed(self._held):
                            self._write(writer, held)
                        self._held.clear()
                elif name in self.delay_on:
                    asyncio.get_running_loop().call_later(
                        self.delay_on[name], self._write, writer, reply
                    )
                else:
                    self._write(writer, reply)
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            return
        finally:
            writer.close()

    def push(self, event):
        """An event line nobody asked for, the way mpv announces a track end."""
        for writer in self._peers:
            self._write(writer, event)

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


async def _until(predicate, bound=2.0):
    """Wait for something the reader task delivers; the bound only stops a hang."""
    deadline = time.monotonic() + bound
    while not predicate() and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    return predicate()


def _first(frames, name):
    """Index of the first frame whose command is `name`, or -1."""
    for index, frame in enumerate(frames):
        if frame and frame[0] == name:
            return index
    return -1


def _recorder():
    """A subscriber that keeps (event, link) pairs in arrival order."""
    seen = []

    def on_event(event, link):
        seen.append((event, link))

    return seen, on_event


@pytest.fixture
def controller():
    return MpvController(ipc_socket_path="/nonexistent/milo-test-ipc.sock")


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
    await controller.disconnect()
    await fake.stop()


@pytest.fixture
def short_command_timeout(monkeypatch):
    """A reply deadline a test can afford to wait out."""
    monkeypatch.setattr("backend.shared.mpv.COMMAND_TIMEOUT", 0.2)


class TestReplyRouting:
    """Replies are matched to their request by id, not by arrival order."""

    async def test_a_slow_reply_does_not_hold_up_a_fast_one(self, live_mpv):
        """The monitor tick reads a property every second. Under the old
        per-command lock, a read issued while another command was waiting on its
        reply queued behind it for as long as that reply took.
        """
        controller, fake = live_mpv
        fake.delay_on = {"stop": 0.5}
        fake.properties["time-pos"] = 12.5
        finished = []

        async def run(name, coro):
            await coro
            finished.append(name)

        await asyncio.gather(
            run("stop", controller.stop()),
            run("read", controller.get_property("time-pos")),
        )

        assert finished == ["read", "stop"]

    async def test_replies_that_arrive_out_of_order_reach_their_own_caller(self, live_mpv):
        controller, fake = live_mpv
        fake.reverse_every = 3
        fake.properties.update({"volume": 11.0, "speed": 1.5, "time-pos": 42.0})

        values = await asyncio.gather(
            controller.get_property("volume"),
            controller.get_property("speed"),
            controller.get_property("time-pos"),
        )

        assert values == [11.0, 1.5, 42.0]

    async def test_an_event_flood_does_not_cost_a_reply(self, live_mpv):
        """mpv bursts events during a stream load or a fast station change. A
        reply that comes after hundreds of them is still the caller's."""
        controller, fake = live_mpv
        fake.events_before_reply = [{"event": "audio-reconfig"}] * 300
        fake.properties.update({"volume": 11.0, "speed": 1.5})

        values = await asyncio.gather(
            controller.get_property("volume"), controller.get_property("speed")
        )

        assert values == [11.0, 1.5]

    async def test_events_before_the_reply_are_never_returned_as_it(self, live_mpv):
        """A reader that returned the first line would hand `playback-restart`
        back as the answer to `loadfile`."""
        controller, fake = live_mpv
        fake.events_before_reply = [
            {"event": "start-file", "playlist_entry_id": 1},
            {"event": "playback-restart"},
        ]

        assert await controller.loadfile("http://example.test/s", mode="replace") == 1

    async def test_a_late_reply_to_a_timed_out_request_answers_nobody(
        self, live_mpv, short_command_timeout
    ):
        """A reply that arrives after its caller gave up must not be handed to
        the next caller: matched by position, a station change would answer with
        the previous station's result."""
        controller, fake = live_mpv
        fake.delay_on = {"get_property": 0.4}
        fake.properties["volume"] = 11.0

        assert await controller.get_property("volume") is None
        fake.delay_on = {}
        fake.properties["volume"] = 99.0
        await asyncio.sleep(0.4)             # the stale reply is now on the wire

        assert await controller.get_property("volume") == 99.0

    async def test_a_reply_that_never_comes_costs_its_deadline(
        self, live_mpv, caplog, short_command_timeout
    ):
        """The bound must be the deadline: nothing else ends the wait."""
        controller, fake = live_mpv
        fake.silent_on = {"get_property"}

        with caplog.at_level(logging.DEBUG, logger="backend.shared.mpv"):
            assert await controller.get_property("volume") is None

        assert "Timeout waiting for mpv response" in caplog.text

    async def test_a_silent_link_does_not_look_disconnected(
        self, live_mpv, short_command_timeout
    ):
        """The control for the test above. A timeout is not a death — mpv can be
        busy opening a slow stream — and dropping the link on one would make
        every slow station change re-connect."""
        controller, fake = live_mpv
        fake.silent_on = {"get_property"}

        await controller.get_property("volume")

        assert controller.is_connected is True

    async def test_a_line_that_is_not_json_does_not_end_the_link(self, live_mpv, caplog):
        """mpv never sends one, so if one arrives it is noise to skip. Tearing
        the link down on it would drop every command until a play re-attached."""
        controller, fake = live_mpv
        fake.raw_before_reply = [b"not json\n"]
        fake.properties["volume"] = 11.0

        with caplog.at_level(logging.WARNING, logger="backend.shared.mpv"):
            assert await controller.get_property("volume") == 11.0

        assert controller.is_connected is True
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


class TestEvents:
    """Every event mpv sends reaches the subscribers, in order."""

    async def test_an_event_reaches_a_subscriber_while_no_command_is_waiting(
        self, live_mpv
    ):
        """The case the old reader could never see: a track that ends while the
        source is idle between two monitor ticks."""
        controller, fake = live_mpv
        seen, on_event = _recorder()
        controller.subscribe(on_event)

        end = {"event": "end-file", "reason": "eof", "playlist_entry_id": 4}
        fake.push(end)

        assert await _until(lambda: seen)
        assert seen[0][0] == end

    async def test_events_interleaved_with_replies_are_delivered_not_skipped(
        self, live_mpv
    ):
        controller, fake = live_mpv
        seen, on_event = _recorder()
        controller.subscribe(on_event)
        burst = [
            {"event": "start-file", "playlist_entry_id": 1},
            {"event": "file-loaded"},
            {"event": "playback-restart"},
        ]
        fake.events_before_reply = burst

        await controller.get_property("volume")

        assert [event for event, _ in seen] == burst

    async def test_events_carry_the_link_they_arrived_on(self, live_mpv):
        """The link is the generation token: an event is only as current as the
        link that delivered it."""
        controller, fake = live_mpv
        seen, on_event = _recorder()
        controller.subscribe(on_event)

        fake.push({"event": "idle"})

        assert await _until(lambda: seen)
        assert seen[0][1] is controller.link

    async def test_unsubscribing_stops_delivery(self, live_mpv):
        controller, fake = live_mpv
        seen, on_event = _recorder()
        unsubscribe = controller.subscribe(on_event)
        unsubscribe()

        fake.push({"event": "idle"})
        await controller.get_property("volume")      # the event is behind us

        assert seen == []

    async def test_a_subscriber_that_raises_does_not_stop_the_reader(
        self, live_mpv, caplog
    ):
        """The reader is the only thing routing replies. A bug in one consumer
        must cost that consumer, not every command on the link."""
        controller, fake = live_mpv
        seen, on_event = _recorder()

        def broken(event, link):
            raise RuntimeError("consumer bug")

        controller.subscribe(broken)
        controller.subscribe(on_event)
        fake.events_before_reply = [{"event": "idle"}]
        fake.properties["volume"] = 11.0

        with caplog.at_level(logging.ERROR, logger="backend.shared.mpv"):
            assert await controller.get_property("volume") == 11.0

        assert [event for event, _ in seen] == [{"event": "idle"}]
        assert "consumer bug" in caplog.text


class TestLinkLost:
    """The end of a link is announced once, and nobody is left waiting."""

    async def test_mpv_dying_fails_every_request_in_flight_at_once(self, live_mpv):
        """Each would otherwise wait out its own deadline, one after another."""
        controller, fake = live_mpv
        fake.silent_on = {"get_property"}

        pending = asyncio.gather(
            *(controller.get_property(name) for name in ("volume", "speed", "time-pos"))
        )
        await _settle()
        started = time.monotonic()
        await fake.drop_peers()

        assert await pending == [None, None, None]
        assert time.monotonic() - started < 1.0

    async def test_mpv_dying_is_announced_once_with_the_dead_link(self, live_mpv):
        from backend.shared.mpv import LINK_LOST

        controller, fake = live_mpv
        seen, on_event = _recorder()
        controller.subscribe(on_event)
        link = controller.link

        await fake.drop_peers()
        await _settle()

        assert [(event["event"], lost) for event, lost in seen] == [(LINK_LOST, link)]
        assert controller.is_connected is False

    async def test_a_deliberate_disconnect_is_announced_too(self, live_mpv):
        """A subscriber cannot tell who ended the link, and does not need to:
        either way nothing more will arrive on it."""
        from backend.shared.mpv import LINK_LOST

        controller, fake = live_mpv
        seen, on_event = _recorder()
        controller.subscribe(on_event)

        await controller.disconnect()
        await controller.disconnect()

        assert [event["event"] for event, _ in seen] == [LINK_LOST]

    async def test_a_socket_closed_mid_request_drops_the_link_quietly(
        self, live_mpv, caplog
    ):
        """mpv dying between the write and the reply is what a restart looks like.

        ERROR reaches the `WebSocketLogHandler` banner, so a routine mpv restart
        reported there would put a red banner in front of the user on every
        source switch.
        """
        controller, fake = live_mpv
        fake.close_on = {"get_property"}

        # Named logger, not the root: `backend/main.py` raises
        # `backend.shared.mpv` to INFO at import time, and a logger-level floor
        # is applied before any handler — so a bare `caplog.at_level(DEBUG)`
        # captures nothing here once main.py has been imported.
        with caplog.at_level(logging.DEBUG, logger="backend.shared.mpv"):
            assert await controller.get_property("volume") is None

        assert controller.is_connected is False
        assert "mpv socket closed while awaiting request" in caplog.text
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR], \
            "a routine mpv restart was reported at ERROR, which reaches the UI banner"

    async def test_a_write_that_raises_drops_the_link(self, live_mpv):
        """A writer whose transport is gone raises rather than timing out;
        keeping the link would leave the controller believing in a socket the
        kernel has already reaped."""
        controller, fake = live_mpv
        controller.link.writer.write = Mock(side_effect=OSError("broken pipe"))

        assert await controller.get_property("volume") is None

        assert controller.is_connected is False


class TestLinkIdentity:
    """A link is an object; a new connection is a new one."""

    async def test_a_reconnect_gives_a_new_identity(self, live_mpv):
        controller, fake = live_mpv
        first = controller.link

        await fake.drop_peers()
        assert controller.link is None
        assert await controller.ensure_connected() is True

        assert controller.link is not None
        assert controller.link is not first

    async def test_events_after_a_reconnect_carry_the_new_link(self, live_mpv):
        controller, fake = live_mpv
        seen, on_event = _recorder()
        controller.subscribe(on_event)
        first = controller.link
        await fake.drop_peers()
        await controller.ensure_connected()
        second = controller.link

        fake.push({"event": "idle"})

        assert await _until(lambda: any(e["event"] == "idle" for e, _ in seen))
        assert [link for event, link in seen if event["event"] == "idle"] == [second]
        assert second is not first

    async def test_connect_over_a_live_link_closes_the_old_one(self, live_mpv):
        """connect() used to open a second socket and leak the first — now with
        a reader task attached to each, a leak would be a task too."""
        from backend.shared.mpv import LINK_LOST

        controller, fake = live_mpv
        seen, on_event = _recorder()
        controller.subscribe(on_event)
        first = controller.link

        assert await controller.connect(timeout=2.0, retry_delay=0.1) is True
        await _settle()

        assert [(event["event"], link) for event, link in seen] == [(LINK_LOST, first)]
        assert len([w for w in fake._peers if not w.is_closing()]) == 1


class TestObserve:
    """Property observation: mpv pushes the value instead of being polled."""

    async def test_observe_returns_an_id_and_changes_arrive_as_events(self, live_mpv):
        controller, fake = live_mpv
        seen, on_event = _recorder()
        controller.subscribe(on_event)

        observe_id = await controller.observe("pause")

        assert fake.received == [["observe_property", observe_id, "pause"]]
        change = {"event": "property-change", "id": observe_id, "name": "pause", "data": True}
        fake.push(change)
        assert await _until(lambda: seen)
        assert seen[0][0] == change

    async def test_an_observation_survives_a_reconnect(self, live_mpv):
        """mpv forgets observations with the connection. A source that had to
        re-observe after every re-attach would miss the pause of the load that
        re-attached it — the load is what calls ensure_connected."""
        controller, fake = live_mpv
        observe_id = await controller.observe("pause")
        await fake.drop_peers()
        fake.received.clear()

        assert await controller.ensure_connected() is True

        assert ["observe_property", observe_id, "pause"] in fake.received

    async def test_observing_twice_is_one_observation(self, live_mpv):
        controller, fake = live_mpv

        first = await controller.observe("pause")
        second = await controller.observe("pause")

        assert first == second
        assert len([f for f in fake.received if f[0] == "observe_property"]) == 1

    async def test_an_observation_mpv_refused_is_not_replayed(self, live_mpv):
        """Replayed on every reconnect, a refused name would be refused forever
        and look, to the caller, like a live observation."""
        controller, fake = live_mpv
        fake.fail_commands = {"observe_property"}

        assert await controller.observe("no-such-property") is None

        fake.fail_commands = set()
        await fake.drop_peers()
        fake.received.clear()
        await controller.ensure_connected()
        assert not [f for f in fake.received if f[0] == "observe_property"]


class TestLoadfile:
    """`loadfile` hands back the playlist entry id mpv assigned — the token an
    `end-file` names — in the one-command form measured on mpv 0.40."""

    async def test_it_returns_the_entry_id_mpv_assigned(self, live_mpv):
        controller, fake = live_mpv

        first = await controller.loadfile("http://example.test/a", mode="replace")
        second = await controller.loadfile("http://example.test/b", mode="append")

        assert first is not None and second is not None
        assert second != first

    async def test_the_start_rides_on_the_load(self, live_mpv):
        """Measured on this unit's mpv 0.40: this frame lands on 12.0 s in one
        command — which is what retires the wait-then-seek dance. The index
        slot is mandatory once options follow it."""
        controller, fake = live_mpv

        await controller.loadfile("http://example.test/a", start_s=12, mode="replace")

        loads = [f for f in fake.received if f[0] == "loadfile"]
        assert loads == [["loadfile", "http://example.test/a", "replace", -1, "start=12"]]

    async def test_a_plain_load_sends_no_options(self, live_mpv):
        controller, fake = live_mpv

        await controller.loadfile("http://example.test/a", mode="append")

        loads = [f for f in fake.received if f[0] == "loadfile"]
        assert loads == [["loadfile", "http://example.test/a", "append"]]

    async def test_a_load_mpv_refused_has_no_entry(self, live_mpv, caplog):
        controller, fake = live_mpv
        fake.fail_commands = {"loadfile"}

        with caplog.at_level(logging.ERROR):
            assert await controller.loadfile("http://example.test/a", mode="replace") is None

        assert "loadfile failed with error" in caplog.text

    async def test_it_re_attaches_before_loading(self, live_mpv):
        """A load is a play command, the one act that picks a restarted mpv
        back up."""
        controller, fake = live_mpv
        await fake.drop_peers()

        assert await controller.loadfile("http://example.test/a", mode="replace") is not None


class TestConnectBudget:
    """connect() must give up inside its timeout, whatever mpv is doing.

    `can_retry` refuses to start an attempt it cannot afford to finish, so the
    loop returns at least `retry_delay` plus that attempt's own cost before the
    deadline, by construction and not by luck. The bound is still the weaker half
    of what is asserted, so the regression these were written for — an attempt
    counter — is also pinned without the clock, on the probe count.
    """

    async def test_missing_socket_gives_up_within_timeout(self, controller):
        started = time.monotonic()
        result = await controller.connect(timeout=2.0, retry_delay=0.5)

        assert result is False
        assert time.monotonic() - started < 2.0

    async def test_unresponsive_mpv_gives_up_within_timeout(self, tmp_path, monkeypatch):
        """Socket accepts the connection but mpv never answers the probe.

        Each probe burns its whole reply deadline, which is what made the old
        attempt counter unbounded: 10 attempts × the deadline, whatever the
        caller's budget. It is also what shows the probe runs on PROBE_TIMEOUT:
        on the command deadline, the first probe alone would outlast the budget.
        """
        monkeypatch.setattr("backend.shared.mpv.PROBE_TIMEOUT", 0.4)
        fake = FakeMpv(tmp_path / "ipc.sock")
        fake.silent_on = {"get_property"}
        await fake.start()
        controller = MpvController(ipc_socket_path=fake.path)

        started = time.monotonic()
        assert await controller.connect(timeout=2.0, retry_delay=0.1) is False
        elapsed = time.monotonic() - started
        await fake.stop()

        assert elapsed < 2.0
        # More than one probe proves the loop retried, fewer than five that it
        # stopped on the deadline rather than on an attempt count.
        assert 1 < fake.connections < 5

    async def test_default_budget_fits_a_source_start(self):
        """Both claims TRANSITION_TIMEOUT can actually make, and no more: one
        systemd call fits, and so does the mpv connect after the settle delay."""
        from backend.core.state import AudioStateMachine
        from backend.core.systemd import CONTROL_TIMEOUT
        from backend.shared.mpv import CONNECT_TIMEOUT, PROBE_TIMEOUT

        assert CONTROL_TIMEOUT < AudioStateMachine.TRANSITION_TIMEOUT
        # _start_service_and_wait settles for 0.5s before connect() is called.
        assert (
            CONNECT_TIMEOUT + PROBE_TIMEOUT + 0.5
        ) < AudioStateMachine.TRANSITION_TIMEOUT


class TestReserveIsPerBranch:
    """What `can_retry` holds back is the cost of the attempt it authorises.

    One reserve for all three branches charged the cheapest of them — does this
    path exist — for a probe it never runs. Measured on the appliance: the boot
    of 2026-09-01 abandoned a cold mpv 5.08s into a 6.0s budget.
    """

    async def test_the_cheap_branch_outlasts_the_expensive_one(
        self, controller, tmp_path, monkeypatch
    ):
        """Same budget, two branches, and the counts are the loop's own.

        The probe fails fast (mpv hangs up on it) while its branch still holds a
        whole PROBE_TIMEOUT in reserve, so only the reserve separates the two
        counts — which is exactly what a shared reserve made equal.
        """
        budget, retry_delay = 2.0, 0.5
        stats = {"n": 0}

        class CountingPath:
            def __init__(self, _path):
                pass

            def exists(self):
                stats["n"] += 1
                return False

        monkeypatch.setattr("backend.shared.mpv.Path", CountingPath)
        assert await controller.connect(budget, retry_delay) is False
        monkeypatch.undo()

        fake = FakeMpv(tmp_path / "ipc.sock")
        fake.close_on = {"get_property"}
        await fake.start()
        wedged = MpvController(ipc_socket_path=fake.path)
        assert await wedged.connect(budget, retry_delay) is False
        await fake.stop()

        assert stats["n"] > fake.connections

    async def test_a_missing_socket_is_waited_for_to_the_deadline(self, controller, caplog):
        """Read off the give-up line, which is what an operator reading the
        journal after a failed boot would see."""
        from backend.shared.mpv import PROBE_TIMEOUT

        budget, retry_delay = 3.0, 0.5
        with caplog.at_level(logging.WARNING, logger="backend.shared.mpv"):
            assert await controller.connect(budget, retry_delay) is False

        waited = float(re.search(r"in ([\d.]+)s", caplog.records[-1].getMessage()).group(1))
        assert budget - retry_delay - PROBE_TIMEOUT < waited <= budget

    async def test_a_re_attach_is_still_one_attempt(self, controller, monkeypatch):
        """A play command gets an honest immediate answer, not a frozen button."""
        stats = {"n": 0}

        class CountingPath:
            def __init__(self, _path):
                pass

            def exists(self):
                stats["n"] += 1
                return False

        monkeypatch.setattr("backend.shared.mpv.Path", CountingPath)
        assert await controller.ensure_connected() is False

        assert stats["n"] == 1


class TestGiveUpIsNotABanner:
    """A start that ran out of patience must not raise a UI banner of its own.

    `backend.shared.mpv` sits under the `backend` hierarchy, where main.py
    attaches WebSocketLogHandler(ERROR); an ERROR from connect() would race the
    typed SystemErrorEvent the state machine already broadcasts for the same
    failure, for App.vue's single banner slot. The handler is the real one and
    the state machine is the fake, so what these assert is what a viewer saw.
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

    async def test_running_out_of_patience_raises_no_banner(self, controller, caplog):
        with caplog.at_level(logging.WARNING, logger="backend.shared.mpv"):
            result, banners = await self._banners(
                controller.connect(timeout=1.0, retry_delay=0.2)
            )

        assert result is False
        assert banners == []
        # Still above errors.log's WARNING floor: demoting it must not make a
        # failing start invisible to whoever debugs the boot.
        gave_up = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(gave_up) == 1
        assert controller.ipc_socket_path in gave_up[0].getMessage()

    async def test_the_give_up_says_how_long_it_waited(self, controller, caplog):
        """The elapsed time is the only evidence a budget could be re-sized from."""
        budget, retry_delay = 2.0, 0.2
        with caplog.at_level(logging.WARNING, logger="backend.shared.mpv"):
            assert await controller.connect(budget, retry_delay) is False

        waited = float(re.search(r"in ([\d.]+)s", caplog.records[-1].getMessage()).group(1))
        assert retry_delay < waited <= budget

    async def test_an_unexpected_fault_still_raises_one(self, controller, monkeypatch):
        """The demotion covers patience, not breakage."""
        monkeypatch.setattr(
            "backend.shared.mpv.Path", Mock(side_effect=RuntimeError("boom"))
        )
        result, banners = await self._banners(controller.connect(timeout=1.0))

        assert result is False
        assert len(banners) == 1
        assert "boom" in banners[0]


class TestConnectFailureArms:
    """What `connect()` does when the socket is there but the connection is not."""

    async def test_a_refused_socket_is_retried_within_the_budget(self, tmp_path, monkeypatch):
        """mpv creates its socket before it is ready to accept, so a refusal at
        boot is normal and transient."""
        fake = FakeMpv(tmp_path / "ipc.sock")
        (tmp_path / "ipc.sock").write_bytes(b"")
        controller = MpvController(ipc_socket_path=str(tmp_path / "ipc.sock"))
        attempts = {"n": 0}
        real_open = asyncio.open_unix_connection

        async def _open(path, **kwargs):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise ConnectionRefusedError("not accepting yet")
            (tmp_path / "ipc.sock").unlink()
            await fake.start()
            return await real_open(path, **kwargs)

        monkeypatch.setattr("asyncio.open_unix_connection", _open)
        assert await controller.connect(timeout=3.0, retry_delay=0.05) is True

        assert attempts["n"] == 3
        await controller.disconnect()
        await fake.stop()

    async def test_a_socket_that_never_accepts_gives_up_and_says_so(
        self, tmp_path, caplog, monkeypatch
    ):
        (tmp_path / "ipc.sock").write_bytes(b"")
        controller = MpvController(ipc_socket_path=str(tmp_path / "ipc.sock"))

        async def _refuse(path, **kwargs):
            raise ConnectionRefusedError("nothing there")

        monkeypatch.setattr("asyncio.open_unix_connection", _refuse)
        with caplog.at_level(logging.WARNING):
            assert await controller.connect(timeout=0.3, retry_delay=0.05) is False

        assert "Failed to connect to mpv in" in caplog.text

    async def test_an_unexpected_error_is_not_retried(self, tmp_path, caplog, monkeypatch):
        """Only a refusal and a missing file are transient."""
        (tmp_path / "ipc.sock").write_bytes(b"")
        controller = MpvController(ipc_socket_path=str(tmp_path / "ipc.sock"))
        attempts = {"n": 0}

        async def _boom(path, **kwargs):
            attempts["n"] += 1
            raise RuntimeError("bad socket type")

        monkeypatch.setattr("asyncio.open_unix_connection", _boom)
        with caplog.at_level(logging.ERROR):
            assert await controller.connect(timeout=3.0, retry_delay=0.05) is False

        assert attempts["n"] == 1
        assert "Unexpected error connecting to mpv" in caplog.text


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

    async def test_a_stale_link_is_seen_without_a_round_trip(self, tmp_path):
        """mpv can die and be replaced with no command in between: three of the
        four monitor ticks issue no mpv I/O on an idle source. Consumers:
        ensure_connected() on every play command, and _monitor_loop's
        disconnect fallback."""
        controller, fake = await self._connected(tmp_path)
        await fake.stop()

        restarted = FakeMpv(fake.path)          # systemd, same socket path
        await restarted.start()
        await _settle()                         # no command issued, ever

        assert controller.is_connected is False
        assert await controller.loadfile("http://example.test/s", mode="replace") is not None
        assert _first(restarted.received, "loadfile") >= 0

        await controller.disconnect()
        await restarted.stop()

    async def test_reads_after_the_link_dies_do_not_reopen_it(self, tmp_path):
        """A read that reconnects can succeed against the *fresh idle* mpv
        systemd restarts: the rest of the tick then answers from it, which is how
        podcast persisted a two-minutes-in episode as completed."""
        controller, fake = await self._connected(tmp_path)
        opened = fake.connections
        await fake.drop_peers()                 # mpv gone; the socket still accepts

        for _ in range(3):
            assert await controller.get_property("time-pos") is None

        assert fake.connections == opened
        assert controller.is_connected is False

        await fake.stop()

    async def test_starting_playback_re_attaches_to_a_restarted_mpv(self, tmp_path):
        """The guard on the over-correction: a "make reads fail fast everywhere"
        edit would trade a stall for a source that never comes back."""
        controller, fake = await self._connected(tmp_path)
        await fake.stop()

        restarted = FakeMpv(fake.path)
        await restarted.start()
        assert await controller.get_property("time-pos") is None    # link is down
        opened = restarted.connections

        assert await controller.loadfile("http://example.test/s", mode="replace") is not None

        assert restarted.connections == opened + 1

        await controller.disconnect()
        await restarted.stop()


class TestTransportCommands:
    """The frame each transport command puts on the socket."""

    async def test_pause_sets_the_pause_property(self, live_mpv):
        controller, fake = live_mpv
        assert await controller.pause() is True
        assert fake.received == [["set_property", "pause", True]]

    async def test_resume_clears_it(self, live_mpv):
        controller, fake = live_mpv
        assert await controller.resume() is True
        assert fake.received == [["set_property", "pause", False]]

    async def test_seek_is_absolute(self, live_mpv):
        """Swapped, a jump to 42s becomes a 42s jump *forward*."""
        controller, fake = live_mpv
        assert await controller.seek(42.5) is True
        assert fake.received == [["seek", 42.5, "absolute"]]

    async def test_stop_is_one_frame(self, live_mpv):
        controller, fake = live_mpv
        assert await controller.stop() is True
        assert fake.received == [["stop"]]

    async def test_an_mpv_error_is_a_failure_not_a_success(self, live_mpv):
        """Callers gate state on the return value, so a refusal that reads as
        success is a UI showing a transport that never happened."""
        controller, fake = live_mpv
        fake.fail_commands.add("stop")

        assert await controller.stop() is False
        assert fake.received == [["stop"]]

    async def test_a_down_link_sends_nothing(self, live_mpv):
        """After disconnect() every command is dropped for ensure_connected()
        to repair, instead of being written into a dead socket."""
        controller, fake = live_mpv

        await controller.disconnect()

        assert controller.is_connected is False
        assert await controller.stop() is False
        assert fake.received == []


class TestPropertyReads:
    """What a read gives back, and what it refuses to invent."""

    async def test_get_property_returns_what_mpv_holds(self, live_mpv):
        controller, fake = live_mpv
        fake.properties["volume"] = 87.5

        assert await controller.get_property("volume") == 87.5
        assert fake.received == [["get_property", "volume"]]

    async def test_a_refused_read_is_not_a_dead_link(self, live_mpv):
        """mpv refuses `chapter` on a stream several times a minute on the
        monitor tick. That is an answer, not a socket failure."""
        controller, fake = live_mpv
        fake.replies = {"get_property": {"error": "property unavailable"}}

        assert await controller.get_property("chapter") is None
        assert controller.is_connected is True

        fake.replies = {}
        fake.properties["volume"] = 12.0
        assert await controller.get_property("volume") == 12.0

    async def test_metadata_keys_are_lowercased_and_values_are_strings(self, live_mpv):
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

    async def test_metadata_is_empty_when_mpv_reports_none(self, live_mpv):
        controller, fake = live_mpv
        fake.properties["metadata"] = None

        assert await controller.get_metadata() == {}


class TestLoadVerdicts:
    """`loadfile` answers with an entry only when mpv took the load."""

    async def test_a_down_link_with_no_mpv_is_refused(self, tmp_path):
        controller = MpvController(ipc_socket_path=str(tmp_path / "gone.sock"))

        assert await controller.loadfile("http://example.invalid/s.mp3", mode="replace") is None

    async def test_the_link_is_re_attached_before_the_stream_options_are_sent(
        self, live_mpv
    ):
        """Run before the reconnect, the HLS switch is dropped on the dead link
        and the stream hangs in "loading" with the reconnect options in force.
        Both orders end in a successful load; only the frames separate them."""
        controller, fake = live_mpv
        await fake.drop_peers()
        fake.received.clear()

        assert await controller.loadfile("https://example.invalid/live.m3u8", mode="replace") is not None

        assert _first(fake.received, "set_property") >= 0
        assert _first(fake.received, "set_property") < _first(fake.received, "loadfile")

    async def test_a_loadfile_that_answers_nothing_is_a_failure(
        self, live_mpv, caplog, short_command_timeout
    ):
        """Read as success, the source waits on an entry id mpv never gave."""
        controller, fake = live_mpv
        fake.silent_on = {"loadfile"}

        with caplog.at_level(logging.INFO):
            assert await controller.loadfile("http://example.invalid/s.mp3", mode="replace") is None

        assert "loadfile returned None" in caplog.text

    @pytest.mark.parametrize("error", [None, "null", "property unavailable"])
    async def test_the_transient_errors_of_a_fast_station_change_stay_quiet(
        self, live_mpv, caplog, error
    ):
        """No entry, but not the station's fault either: logged below error, so
        a quick station change raises no banner."""
        controller, fake = live_mpv
        fake.replies = {"loadfile": {"error": error}}

        with caplog.at_level(logging.INFO):
            assert await controller.loadfile("http://example.invalid/s.mp3", mode="replace") is None

        assert "answered without an entry" in caplog.text
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    async def test_the_query_string_never_reaches_the_log(self, live_mpv, caplog):
        """Navidrome's stream URL carries the Subsonic token and the salt that
        cracks it, once per track."""
        controller, fake = live_mpv

        with caplog.at_level(logging.INFO, logger="backend.shared.mpv"):
            await controller.loadfile("http://nas.test/rest/stream?u=milo&t=abc&s=salt", mode="replace")

        assert "t=abc" not in caplog.text
        assert "/rest/stream" in caplog.text


class TestStreamOptions:
    """The HLS reconnect switch — two lines that decide whether a stream hangs."""

    async def test_an_hls_url_has_the_reconnect_options_cleared(self, live_mpv):
        controller, fake = live_mpv

        await controller.loadfile("https://example.invalid/live.m3u8?token=1", mode="replace")

        sets = [f for f in fake.received if f[:2] == ["set_property", "stream-lavf-o"]]
        assert sets == [["set_property", "stream-lavf-o", ""]]

    async def test_a_plain_url_gets_the_launch_options_back(self, tmp_path):
        """Captured on connect, restored for every non-HLS stream — otherwise one
        HLS station leaves every later Icecast stream without reconnects."""
        fake = FakeMpv(tmp_path / "ipc.sock")
        fake.properties["stream-lavf-o"] = {"reconnect": "1"}
        await fake.start()
        controller = MpvController(ipc_socket_path=fake.path)
        await controller.connect(timeout=2.0, retry_delay=0.1)
        fake.received.clear()

        await controller.loadfile("http://example.invalid/icecast.mp3", mode="replace")

        sets = [f for f in fake.received if f[:2] == ["set_property", "stream-lavf-o"]]
        assert sets == [["set_property", "stream-lavf-o", {"reconnect": "1"}]]
        await controller.disconnect()
        await fake.stop()

    async def test_the_options_are_sent_only_when_the_scope_changes(self, tmp_path):
        """A Music Library queue appends one entry per track: re-sending
        unchanged options doubled the round-trips before the first sound. They
        go out again when the scope flips, and on every new link (a new mpv
        starts from its launch options)."""
        fake = FakeMpv(tmp_path / "ipc.sock")
        fake.properties["stream-lavf-o"] = {"reconnect": "1"}
        await fake.start()
        controller = MpvController(ipc_socket_path=fake.path)
        await controller.connect(timeout=2.0, retry_delay=0.1)
        fake.received.clear()

        for n in range(3):
            await controller.loadfile(f"http://nav.test/{n}.flac", mode="append")
        await controller.loadfile("https://example.invalid/live.m3u8", mode="replace")
        await controller.loadfile("http://example.invalid/icecast.mp3", mode="replace")

        sets = [f[2] for f in fake.received if f[:2] == ["set_property", "stream-lavf-o"]]
        assert sets == [{"reconnect": "1"}, "", {"reconnect": "1"}]
        await controller.disconnect()
        await fake.stop()


class TestPlaylist:
    """The gapless queue: a jump and a removal are one frame each, by index."""

    async def test_play_index_starts_an_entry_by_index(self, live_mpv):
        controller, fake = live_mpv
        assert await controller.play_index(3) is True
        assert fake.received == [["playlist-play-index", 3]]

    async def test_remove_entry_removes_one_entry_by_index(self, live_mpv):
        controller, fake = live_mpv
        assert await controller.remove_entry(2) is True
        assert fake.received == [["playlist-remove", 2]]

    async def test_a_refused_jump_answers_false(self, live_mpv):
        controller, fake = live_mpv
        fake.fail_commands = {"playlist-play-index"}
        assert await controller.play_index(9) is False
