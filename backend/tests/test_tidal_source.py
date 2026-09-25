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

  - **The event → state mapping** (`TidalSource`). The frames are what a live
    session pushes, fed through the daemon of `tests/tidal_world.py`; what is
    asserted is what the wire says, which is what the shared player draws, and
    what the daemon received. The session scenarios live in
    `test_tidal_sessions.py`.
"""
import asyncio
import contextlib
import json
import logging
import struct
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from backend.core.models.ws_events import SourceErrorReason
from backend.sources.tidal.controller_socket import TidalControllerSocket
from backend.tests.tidal_world import HYPNOTIZE, STILL_DRE, TidalWorld, media, status

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




# === The source, driven through the daemon (tests/tidal_world.py) ===

@pytest.fixture
async def world(monkeypatch, tmp_path):
    w = TidalWorld(monkeypatch, tmp_path)
    await w.select()
    yield w
    await w.source.shutdown()


# A media frame shaped like the daemon's, with the three covers out of width order.
MAD_AGAIN = {
    "command": "notifyMediaChanged",
    "mediaInfo": {
        "mediaId": "mad-again",
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
        },
    },
}


def transport(world):
    """(playing, loading), as the shared player draws the transport."""
    return world.playing(), world.buffering()


class TestEventMapping:
    """tisoc frame → what the shared player is told."""

    async def test_a_track_is_published_active_with_the_widest_cover(self, world):
        await world.mac_picks_the_speaker()
        await world.sends(MAD_AGAIN, status("PAUSED", 0, 237000))

        assert world.active()
        session = world.session()
        assert session["title"] == "Mad Again"
        assert session["artist"] == "BunnaB, Guest"
        assert session["album"] == "Ice Cream Summer"
        assert session["artwork"] == "https://cdn/1280.jpg"
        # No sender name: with the transport on screen the player draws it.
        assert session["senders"] == []

    async def test_a_media_frame_alone_does_not_claim_buffering(self, world):
        """Buffering belongs to the player status. A track running into the
        next is announced by its media alone (measured); a media frame that
        latched the spinner on would draw it over a track that is audibly
        playing."""
        await world.mac_plays(STILL_DRE)

        await world.sends(media(HYPNOTIZE))

        assert world.session()["title"] == HYPNOTIZE
        assert transport(world) == (True, False)

    async def test_player_status_drives_playing_and_buffering(self, world):
        await world.mac_picks_the_speaker()
        await world.sends(MAD_AGAIN)

        seen = {}
        for state in ("BUFFERING", "PLAYING", "PAUSED", "IDLE"):
            await world.sends(status(state, 500))
            seen[state] = world.phase()

        assert seen == {
            "BUFFERING": "loading",
            "PLAYING": "playing",
            "PAUSED": "paused",
            # A skip's first millisecond, or nothing left to play: not playing.
            "IDLE": "paused",
        }
        assert world.active()

    async def test_a_playback_error_takes_the_transport_off_the_track(self, world):
        """A track that failed to play is not playing and is not loading.

        The error frame is the only thing tisoc sends — the protocol has no
        status query — so a source that only raised the banner left
        AudioPlayerFull drawing a pause button and useSourceProgress advancing
        a playhead over a track that never started.
        """
        await world.mac_plays(STILL_DRE)
        await world.plays_on(1)
        assert world.session()["position"] is not None

        await world.playback_fails(4)

        assert transport(world) == (False, False)
        assert world.session()["position"] is None
        assert "pause" not in world.state()["controls"]
        # The session survives: the phone is still attached and the card stays
        # actionable, so the track it failed on is still named.
        assert world.active()
        assert world.session()["title"] == STILL_DRE
        assert world.errors() == [SourceErrorReason.PLAYBACK_FAILED]

    async def test_a_moved_playhead_alone_is_not_broadcast(self, world):
        """The daemon ticks about twice a second. A broadcast per tick would
        push to every client at that rate; the anchor already says where the
        playhead is, so a tick that agrees with it sends nothing at all."""
        await world.mac_plays(STILL_DRE)
        publishes = len(world.published())

        await world.plays_on(3)

        assert len(world.published()) == publishes
        assert world.positions() == []
        assert world.position_ms() == world.daemon.progress

    async def test_a_state_that_cannot_be_read_leaves_the_screen_alone(self, world):
        """`releaseResources` and `notifySessionState 0` are what the end of a
        session is read from. A session frame with no usable state must not be
        the thing that wipes a playing track off the screen."""
        await world.mac_plays(STILL_DRE)
        publishes = len(world.published())

        await world.sends({"command": "notifySessionState"})
        await world.sends({"command": "notifySessionState", "state": "gone"})

        assert len(world.published()) == publishes
        assert world.playing() and world.session()["title"] == STILL_DRE

    async def test_an_explicit_zero_ends_the_session(self, world):
        await world.mac_plays(STILL_DRE)

        await world.sends({"command": "notifySessionState", "state": 0})

        assert world.session() is None
        assert world.errors() == []

    async def test_released_resources_end_the_session(self, world):
        await world.mac_plays(STILL_DRE)

        await world.sends({"command": "releaseResources"})

        assert world.session() is None
        assert world.errors() == []


class TestCommands:
    """Milō's vocabulary → the daemon's own spelling."""

    @pytest.mark.parametrize("cmd, spelling", [
        ("pause", "pause"), ("resume", "play"), ("next", "next"), ("prev", "previous"),
    ])
    async def test_a_command_reaches_the_daemon_under_its_own_name(self, world, cmd, spelling):
        await world.mac_plays(STILL_DRE)
        heard = len(world.daemon.received)

        assert (await world.command(cmd))["success"] is True

        assert world.daemon.received[heard:] == [spelling]

    async def test_an_unregistered_command_never_reaches_the_daemon(self, world):
        """`command()` rejects the name before dispatch, which is what makes an
        unregistered arm unreachable rather than half-wired."""
        await world.mac_plays(STILL_DRE)
        heard = len(world.daemon.received)

        assert (await world.command("seek", {"position": 10}))["success"] is False

        assert world.daemon.received[heard:] == []
