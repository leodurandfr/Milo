# backend/tests/test_tidal_failure_arms.py
"""What the Tidal controller does when the daemon misbehaves.

`test_tidal_source.py` drives the framing and the handshakes against a real
Unix socket, and the event mapping against the source. Every *failure* arm
under those was uncovered at 39ff9daf: `send` refusing on a dead writer,
`_run_connection`'s three early returns, `_read_frame`'s framing violations,
the reconnect loop's body guard, and the source's boot and stop paths.

The reason these matter more than usual is in the module's own docstring: the
`tisoc` protocol is undocumented and was read off a live session, and every
failure here looks identical from the phone — "the speaker won't connect" —
while the daemon wedges its SessionManager until it restarts. There is nothing
on the appliance to diagnose it from.

Two arms carry the sharpest consequences:

* **a desynchronised stream must drop the connection.** Once a length prefix is
  wrong, every later read is garbage; carrying on dispatches noise into the
  state machine forever.
* **`startService` refused must abandon the connection**, not proceed. The
  daemon stays in STARTING and rejects the phone session that follows, which is
  the wedge `_do_start`'s ordering comment exists to avoid.

The wire format is restated here rather than imported: the production encoder
is part of what is under test.
"""
import asyncio
import json
import struct

import pytest
from unittest.mock import AsyncMock, MagicMock, Mock

from backend.sources.tidal.controller_socket import TidalControllerSocket
from backend.sources.tidal.source import TidalSource

START = b"\xff\x02"
END = b"\xff\x03"


def frame(**body) -> bytes:
    payload = json.dumps(body).encode()
    return START + struct.pack(">H", len(payload)) + payload + END


def reader_of(*chunks) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    for chunk in chunks:
        reader.feed_data(chunk)
    reader.feed_eof()
    return reader


def controller(tmp_path, on_event=None):
    return TidalControllerSocket(
        socket_path=str(tmp_path / "tisoc.sock"),
        on_event=on_event or AsyncMock(),
    )


class TestSendingOnASocketThatIsGone:
    """`send` — 6 uncovered lines, all of them the refusals."""

    async def test_a_send_with_no_writer_is_refused_not_crashed(self, tmp_path, caplog):
        """`_connection_loop` drops the writer on every disconnection, and
        `_do_stop` sends `stopService` on the way out. Without the guard a
        routine reconnection turns the source stop into an AttributeError on
        None."""
        ctl = controller(tmp_path)
        ctl._writer = None

        with caplog.at_level("WARNING", logger="backend.sources.tidal.controller_socket"):
            assert await ctl.send("stopService") is False

    async def test_a_send_on_a_closing_writer_is_refused(self, tmp_path):
        """`is_closing()` is true for a whole event-loop turn after `close()`;
        writing into that window raises rather than returning an error."""
        writer = MagicMock()
        writer.is_closing = Mock(return_value=True)
        ctl = controller(tmp_path)
        ctl._writer = writer

        assert await ctl.send("stopService") is False
        writer.write.assert_not_called()

    async def test_a_reconnection_during_the_drain_does_not_fail_the_send(
        self, tmp_path
    ):
        """A reconnection drops `self._writer` while the send is suspended in
        `drain()`; the send must still report what it did, not raise.

        Measured constat on the guard above it: the source's comment says the
        local `writer` reference exists because "re-reading it after an await is
        how a routine reconnection turns into an AttributeError on None" — and
        today nothing reads it after the await (`drain()` is evaluated before
        the suspension, and only `return True` follows). So the reference is
        documentary, and no assertion can separate it from `self._writer` as
        the code stands. It earns its keep against the next edit that adds a
        second await; what is asserted here is the behaviour, which is what the
        source stop depends on."""
        writer = MagicMock()
        writer.is_closing = Mock(return_value=False)

        async def drain():
            ctl._writer = None  # a reconnection lands mid-send

        writer.drain = drain
        ctl = controller(tmp_path)
        ctl._writer = writer

        assert await ctl.send("startService") is True

    async def test_a_daemon_that_stopped_reading_times_the_send_out(
        self, tmp_path, caplog
    ):
        """The wedged daemon the send docstring describes: it stops reading
        without closing, so the write never drains. Unbounded, this parks the
        source stop — and with it the whole source switch waiting on it."""
        writer = MagicMock()
        writer.is_closing = Mock(return_value=False)
        writer.drain = AsyncMock(side_effect=asyncio.TimeoutError)
        ctl = controller(tmp_path)
        ctl._writer = writer

        with caplog.at_level("WARNING", logger="backend.sources.tidal.controller_socket"):
            assert await ctl.send("grantResources") is False

        assert "timed out" in caplog.text
        assert "grantResources" in caplog.text

    async def test_a_broken_pipe_is_refused_not_raised(self, tmp_path):
        writer = MagicMock()
        writer.is_closing = Mock(return_value=False)
        writer.drain = AsyncMock(side_effect=BrokenPipeError("daemon gone"))
        ctl = controller(tmp_path)
        ctl._writer = writer

        assert await ctl.send("stopService") is False

    async def test_the_send_is_bounded(self, tmp_path):
        """Non-triviality for the timeout arm above: a healthy send succeeds,
        so a False elsewhere is the guard and not a broken double."""
        writer = MagicMock()
        writer.is_closing = Mock(return_value=False)
        writer.drain = AsyncMock()
        ctl = controller(tmp_path)
        ctl._writer = writer

        assert await ctl.send("startService") is True
        assert writer.write.call_args.args[0].startswith(START)


class TestReadingAFrame:
    """`_read_frame` — the framing violations, none of which had run."""

    async def test_a_well_formed_frame_is_decoded(self, tmp_path):
        ctl = controller(tmp_path)

        assert await ctl._read_frame(
            reader_of(frame(command="notifyServiceStateChanged"))
        ) == {"command": "notifyServiceStateChanged"}

    async def test_a_bad_start_marker_is_a_framing_error(self, tmp_path):
        """Anything but the two magic bytes means the stream is not where the
        reader thinks it is — every later read is garbage."""
        ctl = controller(tmp_path)
        bad = b"\x00\x00" + struct.pack(">H", 2) + b"{}" + END

        with pytest.raises(ValueError, match="bad start marker"):
            await ctl._read_frame(reader_of(bad))

    async def test_a_bad_end_marker_is_a_framing_error(self, tmp_path):
        """A length prefix that lies puts the trailer somewhere else. This is
        the exact failure a protocol drift produces, and it is invisible from
        the phone."""
        ctl = controller(tmp_path)
        payload = b'{"command":"x"}'
        bad = START + struct.pack(">H", len(payload) - 1) + payload + END

        with pytest.raises(ValueError, match="bad end marker"):
            await ctl._read_frame(reader_of(bad))

    async def test_an_undecodable_payload_is_a_framing_error(self, tmp_path):
        ctl = controller(tmp_path)
        payload = b"not json"
        bad = START + struct.pack(">H", len(payload)) + payload + END

        with pytest.raises(ValueError, match="undecodable payload"):
            await ctl._read_frame(reader_of(bad))

    async def test_a_truncated_frame_is_an_incomplete_read_not_a_value_error(
        self, tmp_path
    ):
        """The two are handled by different arms in `_run_connection`: a short
        read is the daemon closing (info), a framing violation is a desync
        (error). Collapsing them logs a protocol drift as a routine
        disconnection."""
        ctl = controller(tmp_path)

        with pytest.raises(asyncio.IncompleteReadError):
            await ctl._read_frame(reader_of(START + struct.pack(">H", 99)))


class TestTheConnectionThatWillNotOpen:
    async def test_a_socket_that_is_not_there_yet_is_not_an_error(
        self, tmp_path, caplog
    ):
        """The controller attaches inside the source's start sequence, before
        the daemon has necessarily created its socket. At error level this
        would raise the UI banner on every ordinary Tidal start."""
        ctl = controller(tmp_path)

        with caplog.at_level("DEBUG", logger="backend.sources.tidal.controller_socket"):
            await ctl._run_connection()

        assert not [r for r in caplog.records if r.levelname in ("ERROR", "WARNING")]

    async def test_a_refused_start_service_abandons_the_connection(self, tmp_path):
        """`startService` is what lifts the daemon out of STARTING. Carrying on
        without it leaves a controller pumping frames at a daemon that will
        reject the next phone session and wedge its SessionManager."""
        ctl = controller(tmp_path)
        opened = {}

        async def open_connection(path):
            reader = reader_of(frame(command="notifyServiceStateChanged"))
            opened["reader"] = reader
            return reader, MagicMock(is_closing=Mock(return_value=False))

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "backend.sources.tidal.controller_socket.asyncio.open_unix_connection",
                open_connection,
            )
            ctl.send = AsyncMock(return_value=False)

            await ctl._run_connection()

        ctl.send.assert_awaited_once_with("startService")
        # The frame that was waiting was never read.
        assert not opened["reader"].at_eof() or opened["reader"]._buffer


class TestTheReconnectLoop:
    async def test_a_failure_inside_a_connection_does_not_end_the_loop(
        self, tmp_path, caplog, monkeypatch
    ):
        """Background-loop doctrine. Without the body guard, one unexpected
        error stops the controller reconnecting and Tidal silently stops
        answering the phone until the source is restarted."""
        ctl = controller(tmp_path)
        turns = []

        async def boom():
            turns.append(1)
            if len(turns) >= 2:
                ctl._stopping = True
            raise RuntimeError("unexpected")

        ctl._run_connection = boom
        monkeypatch.setattr(
            "backend.sources.tidal.controller_socket.asyncio.sleep", AsyncMock()
        )

        with caplog.at_level("ERROR", logger="backend.sources.tidal.controller_socket"):
            await asyncio.wait_for(ctl._connection_loop(), timeout=5)

        assert len(turns) == 2
        assert "unexpected" in caplog.text

    async def test_the_ready_flag_is_cleared_between_connections(
        self, tmp_path, monkeypatch
    ):
        """`wait_ready()` is what `_do_start` gates on. A flag left set from the
        previous connection makes a reconnecting controller report ready before
        the new daemon has answered `startService`."""
        ctl = controller(tmp_path)
        ctl._ready.set()
        turns = []

        async def once():
            turns.append(1)
            ctl._stopping = True

        ctl._run_connection = once
        monkeypatch.setattr(
            "backend.sources.tidal.controller_socket.asyncio.sleep", AsyncMock()
        )

        await asyncio.wait_for(ctl._connection_loop(), timeout=5)

        assert not ctl._ready.is_set()

    async def test_a_cancelled_loop_ends_without_reconnecting(self, tmp_path):
        ctl = controller(tmp_path)

        async def cancelled():
            raise asyncio.CancelledError

        ctl._run_connection = cancelled

        await asyncio.wait_for(ctl._connection_loop(), timeout=5)


class TestDispatchingToTheSource:
    async def test_an_event_handler_that_throws_does_not_drop_the_socket(
        self, tmp_path, caplog
    ):
        """The handler is the source's state mapping. A frame it cannot map
        must cost that frame, not the connection — otherwise one unexpected
        payload disconnects the phone."""
        ctl = controller(tmp_path, on_event=AsyncMock(side_effect=RuntimeError("bad map")))

        with caplog.at_level("ERROR", logger="backend.sources.tidal.controller_socket"):
            await ctl._dispatch({"command": "notifyMediaChanged"})

        assert "bad map" in caplog.text


class TestTheSourceBoot:
    def source(self):
        src = TidalSource()
        src._service_manager = Mock()
        src._service_manager.start = AsyncMock(return_value=True)
        src._service_manager.stop = AsyncMock(return_value=True)
        src._service_manager.is_active = AsyncMock(return_value=True)
        src.emit_connection_state = Mock()
        return src

    async def test_a_daemon_that_will_not_start_stops_the_boot(self):
        src = self.source()
        src._start_service_and_wait = AsyncMock(return_value=False)

        assert await src._do_start() is False

    async def test_a_daemon_that_never_becomes_ready_is_torn_down(self, caplog):
        """`wait_ready()` failing means `startService` was never answered: the
        daemon is still in STARTING and would reject every phone session. The
        source must not report started over that."""
        src = self.source()
        src._start_service_and_wait = AsyncMock(return_value=True)
        ctl = MagicMock(start=AsyncMock(), wait_ready=AsyncMock(return_value=False))
        src._cleanup = AsyncMock()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("backend.sources.tidal.source.TidalControllerSocket",
                       lambda **kw: ctl)
            with caplog.at_level("ERROR", logger="source.tidal"):
                assert await src._do_start() is False

        src._cleanup.assert_awaited_once()
        assert "never became ready" in caplog.text

    async def test_the_controller_is_attached_inside_the_start_sequence(self):
        """Ordering is load-bearing, not tidy: the daemon advertises over mDNS
        as soon as it is up, and a phone session arriving before `startService`
        is rejected AND wedges the SessionManager until a restart."""
        src = self.source()
        order = []
        src._start_service_and_wait = AsyncMock(
            side_effect=lambda: order.append("service") or True
        )
        ctl = MagicMock(
            start=AsyncMock(side_effect=lambda: order.append("attach")),
            wait_ready=AsyncMock(return_value=True),
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("backend.sources.tidal.source.TidalControllerSocket",
                       lambda **kw: ctl)
            assert await src._do_start() is True

        assert order == ["service", "attach"]

    async def test_a_crash_mid_start_tears_down_what_was_built(self):
        src = self.source()
        src._start_service_and_wait = AsyncMock(side_effect=RuntimeError("systemd busy"))
        src._cleanup = AsyncMock()

        assert await src._do_start() is False
        src._cleanup.assert_awaited_once()


class TestTheSourceStop:
    def source(self):
        src = TidalSource()
        src._service_manager = Mock()
        src._service_manager.stop = AsyncMock(return_value=True)
        src.emit_connection_state = Mock()
        src._cleanup = AsyncMock()
        src._stop_service = AsyncMock(return_value=True)
        return src

    async def test_the_speaker_is_withdrawn_before_the_unit_stops(self):
        """`stopService` is what makes the speaker disappear from the phone
        instead of timing out on it."""
        src = self.source()
        src._controller = MagicMock(connected=True, send=AsyncMock(return_value=True))

        assert await src._do_stop() is True

        src._controller.send.assert_awaited_once_with("stopService")

    async def test_a_daemon_that_will_not_answer_does_not_block_the_stop(self, caplog):
        """The comment above the arm is the contract: the notification is a
        courtesy, the unit stop is the actual obligation. A source switch is
        waiting on this, and a source that never stops holds the loopback."""
        src = self.source()
        src._controller = MagicMock(
            connected=True, send=AsyncMock(side_effect=RuntimeError("socket gone"))
        )

        with caplog.at_level("ERROR", logger="source.tidal"):
            assert await src._do_stop() is True

        src._stop_service.assert_awaited_once()
        assert "stopService was not delivered" in caplog.text

    async def test_a_disconnected_controller_is_not_asked_to_send(self):
        src = self.source()
        src._controller = MagicMock(connected=False, send=AsyncMock())

        await src._do_stop()

        src._controller.send.assert_not_awaited()
        src._stop_service.assert_awaited_once()

    async def test_a_source_that_never_attached_still_stops_the_unit(self):
        src = self.source()
        src._controller = None

        assert await src._do_stop() is True
        src._stop_service.assert_awaited_once()

    async def test_cleanup_drops_the_controller(self):
        """Left behind, its reconnect loop keeps reopening the socket of a
        source that is no longer selected."""
        src = TidalSource()
        src.emit_connection_state = Mock()
        stopped = AsyncMock()
        src._controller = MagicMock(stop=stopped)

        await src._cleanup()

        stopped.assert_awaited_once()
        assert src._controller is None
