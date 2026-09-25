"""Spotify's wire, scenario by scenario (see harness.py for the rules).

The outside world is go-librespot: its HTTP API (GET /, GET /status, POST
/player/<cmd>) and its /events WebSocket, both answered by FakeLibrespot, plus
journalctl, which stays silent. The real LibrespotWebSocket runs over the fake
transport, so a stimulus is exactly what the daemon sends on /events.
"""
import asyncio
import copy
import json
from types import SimpleNamespace

import aiohttp
import pytest

from backend.core import audio_source
from backend.core.models.audio_state import AudioSource
from backend.sources.spotify import source as spotify_module
from backend.sources.spotify import websocket as websocket_module
from backend.sources.spotify.source import SpotifySource
from backend.tests.golden.harness import (
    AsyncioProxy, LiveProcessWatch, TickGate, Wire, check_recording, instant_short_sleep,
    make_settings, make_state_machine, make_systemd, settle,
)

BREATHE = {
    "uri": "spotify:track:breathe",
    "name": "Breathe",
    "artist_names": ["Telepopmusik", "Angela McCluskey"],
    "album_name": "Genetic World",
    "album_cover_url": "https://i.scdn.co/image/breathe",
    "duration": 275000,
    "position": 0,
}
HOLOCENE = {
    "uri": "spotify:track:holocene",
    "name": "Holocene",
    "artist_names": ["Bon Iver"],
    "album_name": "Bon Iver",
    "album_cover_url": "https://i.scdn.co/image/holocene",
    "duration": 337000,
    "position": 0,
}

_CLOSED = object()


class _Response:
    def __init__(self, status, payload=None):
        self.status = status
        self._payload = payload

    async def json(self):
        return copy.deepcopy(self._payload)


class _Exchange:
    """`async with session.get(...) as resp` for an answer known up front."""

    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc):
        return False


class _EventsSocket:
    """One /events connection: what the daemon pushes, in order, until it closes."""

    def __init__(self):
        self.queue = asyncio.Queue()

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.queue.get()
        if item is _CLOSED:
            # aiohttp ends the iteration on a CLOSE frame, it never yields it.
            raise StopAsyncIteration
        return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(item))

    def exception(self):
        return None


class FakeLibrespot:
    """go-librespot as the source sees it over HTTP and WebSocket.

    Commands change what /status answers the way the daemon does (pause flips
    `paused`, stop drops the session), but no event is ever sent on their
    behalf: what the daemon says on /events is always the scenario's line.
    """

    def __init__(self):
        self.track = None
        self.paused = True
        self.posted = []
        self.socket = None
        self.connections = 0

    # -- aiohttp.ClientSession surface --------------------------------------

    def __call__(self, *a, **k):
        return self

    def get(self, url, *a, **k):
        if url.endswith("/status"):
            # Measured on 0.10.0: 204 when no phone holds the speaker.
            if self.track is None:
                return _Exchange(_Response(204))
            body = {"track": copy.deepcopy(self.track), "paused": self.paused}
            return _Exchange(_Response(200, body))
        return _Exchange(_Response(200, {"playback_ready": self.track is not None}))

    def post(self, url, json=None, **k):
        command = url.rsplit("/player/", 1)[1]
        self.posted.append((command, json or {}))
        if command == "pause":
            self.paused = True
        elif command == "resume":
            self.paused = False
        elif command == "playpause":
            self.paused = not self.paused
        elif command == "seek" and self.track:
            self.track["position"] = json["position"]
        elif command == "stop":
            self.track, self.paused = None, True
        return _Exchange(_Response(200))

    def ws_connect(self, url, *a, **k):
        self.connections += 1
        self.socket = _EventsSocket()
        return _Exchange(self.socket)

    async def close(self):
        return None

    # -- the daemon's side of the story -------------------------------------

    def load(self, track, position=0, paused=False):
        self.track = {**copy.deepcopy(track), "position": position}
        self.paused = paused

    def says(self, event):
        self.socket.queue.put_nowait(event)

    def closes_events(self):
        self.socket.queue.put_nowait(_CLOSED)


class _AiohttpProxy:
    """The spotify module's `aiohttp`, with ClientSession answered by the fake."""

    def __init__(self, session):
        self.ClientSession = session

    def __getattr__(self, name):
        return getattr(aiohttp, name)


def silent_journal(unit, **_):
    async def stream():
        await asyncio.Event().wait()
        yield ""  # pragma: no cover — unreachable, makes this a generator

    return stream()


class Spotify:
    """Adapter: how each outside-world stimulus reaches SpotifySource today."""

    def __init__(self, monkeypatch, tmp_path, settings=None):
        self.daemon = FakeLibrespot()
        self.gate = TickGate()
        monkeypatch.setattr(spotify_module, "aiohttp", _AiohttpProxy(self.daemon))
        monkeypatch.setattr(spotify_module, "follow_unit", silent_journal)
        # The /events reconnect delay becomes a step the scenario takes.
        monkeypatch.setattr(websocket_module, "asyncio", AsyncioProxy(self.gate.sleep))
        monkeypatch.setattr(audio_source, "asyncio", AsyncioProxy(instant_short_sleep))
        monkeypatch.setattr(audio_source, "ProcessWatch", LiveProcessWatch)
        config = tmp_path / "config.yml"
        # crossfade_duration already matches the (absent) setting, so the start
        # path leaves the file alone.
        config.write_text(
            "server:\n  address: localhost\n  port: 3678\ncrossfade_duration: 0\n"
        )
        self.machine, recorder = make_state_machine()
        self.wire = Wire(self.machine, recorder)
        self.source = SpotifySource(
            {"config_path": str(config)},
            state_machine=self.machine,
            settings_service=make_settings(settings),
            systemd_manager=make_systemd(),
        )
        self.machine.register_source(AudioSource.SPOTIFY, self.source)

    async def select(self):
        await self.machine.transition_to_source(AudioSource.SPOTIFY)
        await settle()

    async def deselect(self):
        await self.machine.transition_to_source(AudioSource.NONE)
        await settle()

    async def command(self, cmd, data=None):
        await self.source.command(cmd, data)
        await settle()

    async def says(self, *events):
        """go-librespot pushes these /events frames, one after the other."""
        for event in events:
            self.daemon.says(event)
            await settle()

    async def events_drop_and_return(self):
        """/events closes; the reconnect delay passes; the socket is back."""
        self.daemon.closes_events()
        await settle()
        await self.gate.tick()


@pytest.fixture
def spotify(monkeypatch, tmp_path):
    return Spotify(monkeypatch, tmp_path)


async def _phone_starts(spotify, track=BREATHE):
    """A phone picks the speaker and starts a track, as go-librespot reports it."""
    spotify.daemon.load(track, paused=True)
    await spotify.says({"type": "active"})
    await spotify.says({"type": "metadata", "uri": track["uri"]})
    spotify.daemon.paused = False
    await spotify.says({"type": "playing"})


async def test_select_and_leave(spotify):
    await spotify.select()
    await spotify.wire.snapshot_rest()
    await spotify.deselect()
    check_recording("spotify", "select_and_leave", spotify.wire)


async def test_session_opens_plays_and_seeks(spotify):
    await spotify.select()
    await _phone_starts(spotify)
    await spotify.wire.snapshot_rest()
    spotify.daemon.track["position"] = 61000        # the phone scrubs
    await spotify.says({"type": "seek", "position": 61000, "uri": BREATHE["uri"]})
    await spotify.command("seek", {"position_ms": 120000})
    await spotify.says({"type": "seek", "position": 120000, "uri": BREATHE["uri"]})
    await spotify.wire.snapshot_rest()
    await spotify.deselect()
    check_recording("spotify", "session_opens_plays_and_seeks", spotify.wire)


async def test_pause_resume_from_phone_and_milo(spotify):
    await spotify.select()
    await _phone_starts(spotify)
    spotify.daemon.track["position"] = 30000
    spotify.daemon.paused = True                    # paused from the phone
    await spotify.says({"type": "paused"})
    await spotify.wire.snapshot_rest()
    await spotify.command("resume")                 # resumed from Milō
    await spotify.says({"type": "playing"})
    await spotify.command("pause")                  # paused from Milō
    await spotify.says({"type": "paused"})
    await spotify.command("playpause")              # the rotary's toggle
    await spotify.says({"type": "playing"})
    await spotify.wire.snapshot_rest()
    await spotify.deselect()
    check_recording("spotify", "pause_resume_from_phone_and_milo", spotify.wire)


async def test_track_changes(spotify):
    await spotify.select()
    await _phone_starts(spotify)
    spotify.daemon.track["position"] = 275000
    await spotify.says({"type": "not_playing"})     # the track ran out
    spotify.daemon.load(HOLOCENE, paused=True)
    await spotify.says({"type": "metadata", "uri": HOLOCENE["uri"]})
    await spotify.wire.snapshot_rest()              # loading: buffering spinner
    spotify.daemon.paused = False
    await spotify.says({"type": "playing"})
    await spotify.command("prev", {})                # back to Breathe from Milō
    spotify.daemon.load(BREATHE, paused=True)
    await spotify.says({"type": "metadata", "uri": BREATHE["uri"]})
    spotify.daemon.paused = False
    await spotify.says({"type": "playing"})
    await spotify.wire.snapshot_rest()
    await spotify.deselect()
    check_recording("spotify", "track_changes", spotify.wire)


async def test_context_ends_then_phone_disconnects(spotify):
    await spotify.select()
    await _phone_starts(spotify)
    spotify.daemon.paused = True
    await spotify.says({"type": "stopped"})         # the playlist ended
    await spotify.wire.snapshot_rest()
    spotify.daemon.track = None
    await spotify.says({"type": "inactive"})        # the phone let go
    await spotify.wire.snapshot_rest()
    await spotify.deselect()
    check_recording("spotify", "context_ends_then_phone_disconnects", spotify.wire)


async def test_pause_auto_stops(monkeypatch, tmp_path):
    spotify = Spotify(monkeypatch, tmp_path, settings={"audio.auto_stop_delay": 1})
    await spotify.select()
    await _phone_starts(spotify)
    spotify.daemon.paused = True
    await spotify.says({"type": "paused"})          # arms the 1 s timer, which fires
    assert spotify.daemon.posted[-1] == ("stop", {})
    await spotify.says({"type": "inactive"})        # the daemon ends the session
    await spotify.wire.snapshot_rest()
    await spotify.deselect()
    check_recording("spotify", "pause_auto_stops", spotify.wire)


async def test_events_socket_drops(spotify):
    await spotify.select()
    await _phone_starts(spotify)
    spotify.daemon.track["position"] = 90000
    await spotify.events_drop_and_return()          # blip: the session survived
    await spotify.wire.snapshot_rest()
    spotify.daemon.track = None                     # daemon restarted underneath
    await spotify.events_drop_and_return()
    await spotify.wire.snapshot_rest()
    assert spotify.daemon.connections == 3
    await spotify.deselect()
    check_recording("spotify", "events_socket_drops", spotify.wire)
