# backend/tests/test_tidal_source.py
"""
Unit tests for the Tidal Connect source and its `tisoc` controller socket.

Two things are covered, both invisible to every other guardrail:

  - **The framing and the handshakes** (`TidalControllerSocket`). The protocol
    is undocumented and was read off a live session; nothing else in the repo
    can tell that a length prefix moved or that `grantResources` stopped being
    answered. Both failures look identical from the phone — "the speaker won't
    connect" — and the daemon wedges until it restarts, so they cannot be
    diagnosed from the appliance either. A real Unix socket stands in for the
    daemon here: what is asserted is the bytes the controller put on the wire
    and what it did with the bytes it got back.

  - **The event → state mapping** (`TidalSource`). The frames below are what a
    live session pushes; what is asserted is what the source published to the
    state machine, which is what the shared player draws.
"""
import asyncio
import contextlib
import json
import logging
import struct
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import pytest

from backend.core.models.audio_state import AudioSource, SourceState
from backend.sources.tidal.controller_socket import TidalControllerSocket
from backend.sources.tidal.source import TidalSource

# The wire format, restated here on purpose: the production encoder is what
# these tests exist to check, so they must not borrow it.
START = b"\xff\x02"
END = b"\xff\x03"


def encode(**frame) -> bytes:
    payload = json.dumps(frame).encode()
    return START + struct.pack(">H", len(payload)) + payload + END


async def decode(reader) -> dict:
    header = await reader.readexactly(4)
    assert header[:2] == START, f"bad start marker: {header[:2]!r}"
    (length,) = struct.unpack(">H", header[2:])
    payload = await reader.readexactly(length)
    assert await reader.readexactly(2) == END, "missing end marker"
    return json.loads(payload)


class FakeDaemon:
    """Stand-in for tidal_connect_application's controller socket.

    Accepts connections, decodes what the controller sends into a queue, and
    pushes frames back. Counts connections so a reconnection is observable.
    """

    def __init__(self, path):
        self.path = str(path)
        self.received = asyncio.Queue()
        self.connections = 0
        self._writer = None
        self._server = None

    async def start(self):
        self._server = await asyncio.start_unix_server(self._serve, self.path)

    async def _serve(self, reader, writer):
        self.connections += 1
        self._writer = writer
        try:
            while True:
                await self.received.put(await decode(reader))
        except (asyncio.IncompleteReadError, ConnectionError, AssertionError):
            return

    async def push(self, **frame):
        self._writer.write(encode(**frame))
        await self._writer.drain()

    async def push_raw(self, raw: bytes):
        self._writer.write(raw)
        await self._writer.drain()

    async def next_frame(self, timeout=2.0) -> dict:
        return await asyncio.wait_for(self.received.get(), timeout=timeout)

    async def stop(self):
        # The accepted connection first: `wait_closed()` waits on the handler
        # task, and the handler sits in a read until its socket goes away.
        if self._writer:
            self._writer.close()
            with contextlib.suppress(Exception):
                await self._writer.wait_closed()

        if self._server:
            self._server.close()
            await self._server.wait_closed()


@asynccontextmanager
async def attached(tmp_path):
    """A fake daemon with a controller attached to it, closed together.

    A context manager and not a fixture: the controller owns a background task
    and an open socket, and both belong to the loop of the test that uses them
    — which is also why the rest of this suite keeps its fixtures synchronous.
    """
    daemon = FakeDaemon(tmp_path / "tisoc.sock")
    await daemon.start()

    events = []

    async def on_event(message):
        events.append(message)

    socket = TidalControllerSocket(socket_path=daemon.path, on_event=on_event)
    await socket.start()
    try:
        yield daemon, socket, events
    finally:
        await socket.stop()
        await daemon.stop()


@pytest.fixture
def tidal():
    """A Tidal source wired to a state machine that records what it publishes."""
    source = TidalSource()
    state_machine = Mock()
    state_machine.broadcast = AsyncMock()
    state_machine.update_source_state = AsyncMock()
    state_machine.update_position_metadata = AsyncMock()
    state_machine.system_state = Mock(active_source=AudioSource.TIDAL)
    source.state_machine = state_machine
    source._bg = Mock()
    source._bg.spawn = Mock(side_effect=lambda coro, **kw: coro.close())
    return source, state_machine


def published(state_machine):
    """The (state, metadata) of the last push to the state machine."""
    _, state, metadata = state_machine.update_source_state.call_args.args
    return state, metadata


class TestFramingAndHandshakes:
    """What the controller puts on the wire, and what it answers."""

    async def test_connect_announces_start_service(self, tmp_path):
        """Until startService lands the daemon rejects every phone session, and
        one rejection wedges it until systemd restarts the unit."""
        async with attached(tmp_path) as (daemon, _socket, _events):
            assert await daemon.next_frame() == {"command": "startService"}

    async def test_request_resources_is_granted(self, tmp_path):
        """Ungranted, the session opens and stalls before a sample is decoded."""
        async with attached(tmp_path) as (daemon, _socket, _events):
            assert await daemon.next_frame() == {"command": "startService"}

            await daemon.push(command="requestResources")
            assert await daemon.next_frame() == {"command": "grantResources"}

    async def test_release_resources_is_revoked(self, tmp_path):
        async with attached(tmp_path) as (daemon, _socket, _events):
            await daemon.next_frame()

            await daemon.push(command="releaseResources")
            assert await daemon.next_frame() == {"command": "revokeResources"}

    async def test_ready_waits_for_the_daemon_to_answer(self, tmp_path):
        """`_do_start` gates on this: reporting the source up before the answer
        would advertise a speaker that refuses the first phone to try it."""
        async with attached(tmp_path) as (daemon, socket, _events):
            assert await socket.wait_ready(timeout=0.2) is False

            await daemon.push(command="notifyServiceStateChanged")
            assert await socket.wait_ready(timeout=2.0) is True

    async def test_every_frame_reaches_the_source(self, tmp_path):
        """Including the handshakes the controller answers itself — the source
        reads session state out of them."""
        async with attached(tmp_path) as (daemon, _socket, events):
            await daemon.next_frame()

            await daemon.push(
                command="notifyMediaChanged", mediaInfo={"metadata": {"title": "Zoo"}}
            )
            await asyncio.sleep(0.1)

            assert events, "no frame was forwarded — the reader is broken"
            assert events[-1]["mediaInfo"]["metadata"]["title"] == "Zoo"

    async def test_oversized_payload_is_refused_before_the_wire(self, tmp_path):
        """The length field is 16 bits: a longer frame would be truncated into a
        stream desync rather than an error."""
        async with attached(tmp_path) as (daemon, socket, _events):
            assert await daemon.next_frame() == {"command": "startService"}

            assert await socket.send("pause", padding="x" * 70_000) is False

            with pytest.raises(asyncio.TimeoutError):
                await daemon.next_frame(timeout=0.3)

    async def test_a_desynchronised_stream_rebuilds_the_connection(self, tmp_path):
        """Every read after a bad marker is garbage, so the only cure is a new
        connection — and it must re-announce itself to be of any use."""
        async with attached(tmp_path) as (daemon, _socket, _events):
            assert await daemon.next_frame() == {"command": "startService"}

            await daemon.push_raw(b"\x00\x00garbage")

            assert await daemon.next_frame(timeout=5.0) == {"command": "startService"}
            assert daemon.connections == 2

    async def test_send_answers_false_once_the_daemon_is_gone(self, tmp_path):
        """`_do_stop` sends through here while a source switch waits on it: a
        dead socket has to answer, not raise."""
        async with attached(tmp_path) as (daemon, socket, _events):
            await daemon.next_frame()

            await socket.stop()

            assert await socket.send("stopService") is False


class TestRefusedHandshakes:
    """A handshake the daemon never receives is silence with no explanation.

    `send` answers False and only warns — journal-only. But a frame just
    arrived on this socket, so a refused write is the wedged daemon (it stops
    reading without closing), not a routine disconnection: ungranted, the
    session opens and stalls, and Tidal sits ACTIVE and mute.
    """

    @pytest.fixture
    def socket(self):
        return TidalControllerSocket("/nonexistent.sock", AsyncMock())

    async def test_a_refused_grant_is_reported_at_error(self, socket, caplog):
        socket.send = AsyncMock(return_value=False)

        with caplog.at_level(logging.ERROR):
            await socket._dispatch({"command": "requestResources"})

        assert "active and silent" in caplog.text

    async def test_a_refused_revoke_is_reported_at_error(self, socket, caplog):
        socket.send = AsyncMock(return_value=False)

        with caplog.at_level(logging.ERROR):
            await socket._dispatch({"command": "releaseResources"})

        assert "keep holding it" in caplog.text

    async def test_a_delivered_grant_says_nothing(self, socket, caplog):
        """The positive control: the handshake is answered on every session, so
        an unconditional error here would banner every phone that connects."""
        socket.send = AsyncMock(return_value=True)

        with caplog.at_level(logging.ERROR):
            await socket._dispatch({"command": "requestResources"})

        assert caplog.text == ""


class TestEventMapping:
    """tisoc frame → what the shared player is told."""

    MEDIA = {
        "command": "notifyMediaChanged",
        "mediaInfo": {
            "metadata": {
                "title": "Mad Again",
                "artists": ["BunnaB", "Guest"],
                "albumTitle": "Ice Cream Summer",
                "duration": 237000,
                "images": {
                    "low": {"url": "https://cdn/320.jpg", "width": 320},
                    "high": {"url": "https://cdn/1280.jpg", "width": 1280},
                    "medium": {"url": "https://cdn/640.jpg", "width": 640},
                },
            }
        },
    }

    async def test_a_track_is_published_active_with_the_widest_cover(self, tidal):
        source, state_machine = tidal

        await source._handle_event(self.MEDIA)

        state, metadata = published(state_machine)
        assert state == SourceState.ACTIVE
        assert metadata["title"] == "Mad Again"
        assert metadata["artist"] == "BunnaB, Guest"
        assert metadata["album"] == "Ice Cream Summer"
        assert metadata["album_art_url"].endswith("1280.jpg")

    async def test_a_media_frame_alone_does_not_claim_buffering(self, tidal):
        """Buffering belongs to the player status. A media frame that latched it
        on would leave a spinner over a track that is audibly playing, since
        nothing else ever clears it."""
        source, state_machine = tidal

        await source._handle_event(self.MEDIA)

        _, metadata = published(state_machine)
        assert metadata["is_buffering"] is False

    async def test_player_status_drives_playing_and_buffering(self, tidal):
        source, state_machine = tidal
        await source._handle_event(self.MEDIA)

        await source._handle_event({
            "command": "notifyPlayerStatusChanged",
            "playerState": "BUFFERING", "progress": 0, "duration": 30066,
        })
        _, metadata = published(state_machine)
        assert (metadata["is_playing"], metadata["is_buffering"]) == (False, True)

        await source._handle_event({
            "command": "notifyPlayerStatusChanged",
            "playerState": "PLAYING", "progress": 500, "duration": 30066,
        })
        _, metadata = published(state_machine)
        assert (metadata["is_playing"], metadata["is_buffering"]) == (True, False)

    async def test_a_moved_playhead_alone_skips_the_full_broadcast(self, tidal):
        """The daemon ticks about twice a second. A full_state per tick would
        push the whole system state to every client at that rate; the frontend
        interpolates locally and only needs the drift correction."""
        source, state_machine = tidal
        playing = {
            "command": "notifyPlayerStatusChanged",
            "playerState": "PLAYING", "duration": 30066,
        }
        await source._handle_event({**playing, "progress": 500})
        publishes = state_machine.update_source_state.call_count

        await source._handle_event({**playing, "progress": 1000})
        await source._handle_event({**playing, "progress": 1500})

        assert state_machine.update_source_state.call_count == publishes
        assert source._bg.spawn.called, "no drift correction was broadcast either"

    async def test_a_state_that_cannot_be_read_leaves_the_screen_alone(self, tidal):
        """`releaseResources` is what the end of a session is read from. A
        session frame with no usable state must not be the thing that wipes a
        playing track off the screen."""
        source, state_machine = tidal
        await source._handle_event(self.MEDIA)
        publishes = state_machine.update_source_state.call_count

        await source._handle_event({"command": "notifySessionState"})

        assert state_machine.update_source_state.call_count == publishes
        assert source.metadata["title"] == "Mad Again"

    async def test_an_explicit_zero_ends_the_session(self, tidal):
        source, state_machine = tidal
        await source._handle_event(self.MEDIA)

        await source._handle_event({"command": "notifySessionState", "state": 0})

        state, metadata = published(state_machine)
        assert state == SourceState.READY
        assert "title" not in metadata

    async def test_released_resources_end_the_session(self, tidal):
        source, state_machine = tidal
        await source._handle_event(self.MEDIA)

        await source._handle_event({"command": "releaseResources"})

        state, _ = published(state_machine)
        assert state == SourceState.READY


class TestCommands:
    """Milō's vocabulary → the daemon's own spelling, over a real socket."""

    async def test_a_command_reaches_the_daemon_under_its_own_name(self, tidal, tmp_path):
        source, _ = tidal
        async with attached(tmp_path) as (daemon, socket, _events):
            source._controller = socket
            assert await daemon.next_frame() == {"command": "startService"}

            assert (await source.command("prev", None))["success"] is True

            assert await daemon.next_frame() == {"command": "previous"}

    async def test_an_unregistered_command_never_reaches_the_daemon(self, tidal, tmp_path):
        """`command()` rejects the name before dispatch, which is what makes an
        unregistered arm unreachable rather than half-wired."""
        source, _ = tidal
        async with attached(tmp_path) as (daemon, socket, _events):
            source._controller = socket
            await daemon.next_frame()

            assert (await source.command("seek", {"position": 10}))["success"] is False

            with pytest.raises(asyncio.TimeoutError):
                await daemon.next_frame(timeout=0.3)
