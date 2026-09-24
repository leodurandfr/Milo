"""Tidal's old wire, scenario by scenario (see harness.py for the rules).

The outside world is tidal_connect_application's `tisoc` controller socket.
FakeTisoc stands in for the Unix socket itself (a real StreamReader fed with
framed JSON, a writer that decodes what the controller sends), so the real
TidalControllerSocket frames, hand-shakes and reconnects; a stimulus is one
frame the daemon pushes.
"""
import asyncio
import json
import struct

import pytest

from backend.core import audio_source
from backend.core.models.audio_state import AudioSource
from backend.sources.tidal import controller_socket as controller_module
from backend.sources.tidal import source as tidal_module
from backend.sources.tidal.source import TidalSource
from backend.tests.golden.harness import (
    AsyncioProxy, LiveProcessWatch, TickGate, Wire, check_recording, instant_short_sleep,
    make_state_machine, make_systemd, settle,
)

# The tisoc framing, restated rather than borrowed from the module under test.
START, END = b"\xff\x02", b"\xff\x03"


def frame(message):
    payload = json.dumps(message).encode()
    return START + struct.pack(">H", len(payload)) + payload + END


def media(title, artists, album, duration, cover):
    return {
        "command": "notifyMediaChanged",
        "mediaInfo": {
            "metadata": {
                "title": title,
                "artists": artists,
                "albumTitle": album,
                "duration": duration,
                "images": {
                    "low": {"url": f"{cover}/320.jpg", "width": 320},
                    "high": {"url": f"{cover}/1280.jpg", "width": 1280},
                    "medium": {"url": f"{cover}/640.jpg", "width": 640},
                },
            }
        },
    }


def status(player_state, progress=None, duration=None):
    message = {"command": "notifyPlayerStatusChanged", "playerState": player_state}
    if progress is not None:
        message["progress"] = progress
    if duration is not None:
        message["duration"] = duration
    return message


MAD_AGAIN = media("Mad Again", ["BunnaB", "Guest"], "Ice Cream Summer", 237000,
                  "https://resources.tidal.example/mad")
TEARDROP = media("Teardrop", ["Massive Attack"], "Mezzanine", 330000,
                 "https://resources.tidal.example/teardrop")


class _Writer:
    """The controller's end of one connection: every frame it writes is decoded
    and handed to the daemon."""

    def __init__(self, daemon):
        self._daemon = daemon
        self._closing = False

    def write(self, data):
        (length,) = struct.unpack(">H", data[2:4])
        self._daemon.heard(json.loads(data[4:4 + length]))

    async def drain(self):
        return None

    def is_closing(self):
        return self._closing

    def close(self):
        self._closing = True

    async def wait_closed(self):
        return None


class FakeTisoc:
    """tidal_connect_application's controller socket.

    It answers `startService` with `notifyServiceStateChanged`, as the daemon
    always does — that acknowledgement is the protocol's connect signal, not a
    scenario event. Everything else it says is the scenario's line.
    """

    def __init__(self):
        self.received = []
        self.connections = 0
        self._reader = None

    async def open_unix_connection(self, path, *a, **k):
        self.connections += 1
        self._reader = asyncio.StreamReader()
        return self._reader, _Writer(self)

    def heard(self, message):
        self.received.append(message["command"])
        if message["command"] == "startService":
            self.push({"command": "notifyServiceStateChanged"})

    def push(self, message):
        self._reader.feed_data(frame(message))

    def hang_up(self):
        self._reader.feed_eof()


class FakeLoopClock:
    """The loop clock tidal/source.py reads to pace its position updates.

    The real one is the host's monotonic clock, so whether a drift correction
    goes out would depend on when the test ran; this one moves only when the
    scenario says time passed.
    """

    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now


class Tidal:
    """Adapter: how each outside-world stimulus reaches TidalSource today."""

    def __init__(self, monkeypatch):
        self.daemon = FakeTisoc()
        self.gate = TickGate()
        self.clock = FakeLoopClock()
        socket_asyncio = AsyncioProxy(self.gate.sleep)
        socket_asyncio.open_unix_connection = self.daemon.open_unix_connection
        monkeypatch.setattr(controller_module, "asyncio", socket_asyncio)
        source_asyncio = AsyncioProxy(asyncio.sleep)
        source_asyncio.get_running_loop = lambda: self.clock
        monkeypatch.setattr(tidal_module, "asyncio", source_asyncio)
        monkeypatch.setattr(audio_source, "asyncio", AsyncioProxy(instant_short_sleep))
        # The daemon's process: a restart underneath is its death, which the
        # session's pidfd watch hears.
        self.watches = []
        watches = self.watches

        class Watch(LiveProcessWatch):
            def __init__(self, pid, on_exit, *a, **k):
                super().__init__(pid, on_exit)
                self.on_exit = on_exit
                watches.append(self)

            def close(self):
                if self in watches:
                    watches.remove(self)

        monkeypatch.setattr(audio_source, "ProcessWatch", Watch)
        self.machine, recorder = make_state_machine()
        self.wire = Wire(self.machine, recorder)
        self.source = TidalSource(
            {"socket_path": "/nonexistent/tidal-controller.sock"},
            state_machine=self.machine,
            settings_service=None,
            systemd_manager=make_systemd(),
        )
        self.machine.register_source(AudioSource.TIDAL, self.source)

    async def select(self):
        await self.machine.transition_to_source(AudioSource.TIDAL)
        await settle()

    async def deselect(self):
        await self.machine.transition_to_source(AudioSource.NONE)
        await settle()

    async def command(self, cmd, data=None):
        await self.source.command(cmd, data)
        await settle()

    async def says(self, *messages):
        """The daemon pushes these frames, one after the other."""
        for message in messages:
            self.daemon.push(message)
            await settle()

    async def hangs_up_and_returns(self):
        """The socket closes (daemon restart); the retry delay passes."""
        for watch in list(self.watches):
            watch.on_exit()
        self.daemon.hang_up()
        await settle()
        await self.gate.tick()

    def time_passes(self, seconds):
        self.clock.now += seconds


@pytest.fixture
def tidal(monkeypatch):
    return Tidal(monkeypatch)


async def _phone_starts(tidal, track=MAD_AGAIN):
    """A phone opens a session and plays a track, frame by frame as tisoc does."""
    await tidal.says(
        {"command": "requestResources"},
        {"command": "notifySessionState", "state": 1},
        {"command": "notifySessionState", "state": 2},
        track,
        status("BUFFERING", 0, 30066),
        status("PLAYING", 500, 30066),
    )


async def test_select_and_leave(tidal):
    await tidal.select()
    await tidal.wire.snapshot_rest()
    await tidal.deselect()
    assert tidal.daemon.received == ["startService", "stopService"]
    check_recording("tidal", "select_and_leave", tidal.wire)


async def test_session_opens_and_plays(tidal):
    await tidal.select()
    await _phone_starts(tidal)
    await tidal.wire.snapshot_rest()
    await tidal.says(status("PLAYING", 1000, 30066))    # first drift correction
    await tidal.says(status("PLAYING", 1500, 30066))    # inside the interval: nothing
    tidal.time_passes(10)
    await tidal.says(status("PLAYING", 11500, 30066))   # next drift correction
    await tidal.wire.snapshot_rest()
    await tidal.deselect()
    assert tidal.daemon.received[:2] == ["startService", "grantResources"]
    check_recording("tidal", "session_opens_and_plays", tidal.wire)


async def test_pause_resume_from_milo_and_phone(tidal):
    await tidal.select()
    await _phone_starts(tidal)
    await tidal.command("pause")
    await tidal.says(status("PAUSED", 4000, 30066))
    await tidal.wire.snapshot_rest()
    await tidal.command("resume")
    await tidal.says(status("PLAYING", 4000, 30066))
    await tidal.says(status("PAUSED", 9000, 30066))     # paused from the phone
    await tidal.says(status("PLAYING", 9000, 30066))    # resumed from the phone
    await tidal.deselect()
    assert "pause" in tidal.daemon.received and "play" in tidal.daemon.received
    check_recording("tidal", "pause_resume_from_milo_and_phone", tidal.wire)


async def test_track_changes(tidal):
    await tidal.select()
    await _phone_starts(tidal)
    await tidal.command("next")
    await tidal.says(TEARDROP, status("BUFFERING", 0, 330000), status("PLAYING", 200, 330000))
    await tidal.wire.snapshot_rest()
    await tidal.says(status("IDLE"))                   # the track ran out
    await tidal.wire.snapshot_rest()
    await tidal.command("prev")
    await tidal.says(MAD_AGAIN, status("BUFFERING", 0, 237000), status("PLAYING", 0, 237000))
    await tidal.deselect()
    assert "next" in tidal.daemon.received and "previous" in tidal.daemon.received
    check_recording("tidal", "track_changes", tidal.wire)


async def test_phone_ends_the_session(tidal):
    await tidal.select()
    await _phone_starts(tidal)
    await tidal.says(status("PAUSED", 8000, 30066))
    await tidal.says({"command": "releaseResources"})
    await tidal.wire.snapshot_rest()
    await tidal.says({"command": "notifySessionState", "state": 0})
    await tidal.deselect()
    assert "revokeResources" in tidal.daemon.received
    check_recording("tidal", "phone_ends_the_session", tidal.wire)


async def test_playback_error_mid_track(tidal):
    await tidal.select()
    await _phone_starts(tidal)
    await tidal.says({"command": "notifyPlaybackError", "errorCode": 4})
    await tidal.wire.snapshot_rest()
    await tidal.deselect()
    check_recording("tidal", "playback_error_mid_track", tidal.wire)


async def test_controller_socket_drops(tidal):
    await tidal.select()
    await _phone_starts(tidal)
    await tidal.hangs_up_and_returns()                  # daemon restarted underneath
    await tidal.wire.snapshot_rest()
    assert tidal.daemon.connections == 2
    await _phone_starts(tidal, TEARDROP)                # the phone comes back
    await tidal.deselect()
    check_recording("tidal", "controller_socket_drops", tidal.wire)
