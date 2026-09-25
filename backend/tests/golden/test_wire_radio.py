"""Radio's wire, scenario by scenario (see harness.py for the rules)."""
import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from backend.core import audio_source
from backend.core.models.audio_state import AudioSource
from backend.shared import mpv_audio_source
from backend.sources.radio import source as radio_module
from backend.sources.radio.source import RadioSource
from backend.tests.golden.harness import (
    AsyncioProxy, EventMpv, TickGate, Wire, check_recording, instant_short_sleep,
    make_settings, make_state_machine, make_systemd, settle,
)

FIP = {
    "id": "fip", "name": "FIP", "url": "http://stream.example/fip.mp3",
    "country": "France", "genre": "eclectic", "favicon": "https://img.example/fip.png",
    "bitrate": 128, "codec": "MP3",
}
NOVA = {
    "id": "nova", "name": "Radio Nova", "url": "http://stream.example/nova.aac",
    "country": "France", "genre": "", "favicon": "/api/radio/images/nova.jpg",
    "bitrate": 64, "codec": "AAC",
}


class FakeShazam:
    """The recognition service: nothing is heard unless a scenario says so."""

    def __init__(self, settings_service=None, on_track_changed=None):
        self.on_track_changed = on_track_changed
        self.current_track = None
        self.is_running = False

    async def is_enabled(self) -> bool:
        return True

    async def start(self, stream_url, preroll_skip=0):
        self.is_running = True

    async def stop(self):
        self.is_running = False
        self.current_track = None


class _NoPrerollProbe:
    """ffprobe on a station with no pre-roll ad: exits 0 with no tags."""

    returncode = 0

    async def communicate(self):
        return b"{}", b""


class _RadioAsyncio(AsyncioProxy):
    """The radio module's asyncio: real, except the ffprobe it would spawn."""

    def __init__(self):
        super().__init__(asyncio.sleep)

    async def create_subprocess_exec(self, *args, **kwargs):
        return _NoPrerollProbe()


class Radio:
    """Adapter: how each outside-world stimulus reaches RadioSource today."""

    def __init__(self, monkeypatch, settings=None):
        self.mpv = EventMpv()
        self.gate = TickGate()
        self.gate.clocks.append(self.mpv.elapse)
        monkeypatch.setattr(mpv_audio_source, "MpvController", lambda **_: self.mpv)
        monkeypatch.setattr(mpv_audio_source, "asyncio", AsyncioProxy(self.gate.sleep))
        monkeypatch.setattr(audio_source, "asyncio", AsyncioProxy(instant_short_sleep))
        monkeypatch.setattr(radio_module, "ShazamRecognitionService", FakeShazam)
        monkeypatch.setattr(radio_module, "asyncio", _RadioAsyncio())
        self.machine, recorder = make_state_machine()
        self.wire = Wire(self.machine, recorder)
        self.source = RadioSource(
            {"mpv_socket": "/nonexistent/radio.sock"},
            state_machine=self.machine,
            settings_service=make_settings(settings),
            systemd_manager=make_systemd(),
        )
        data = Mock()
        data.initialize = AsyncMock()
        data.favorite_ids = ["fip", "nova"]
        data.is_favorite = Mock(side_effect=lambda sid: sid in ("fip", "nova"))
        data.get_favorite_metadata_local = Mock(
            side_effect=lambda sid: {"fip": FIP, "nova": NOVA}.get(sid)
        )
        data.is_station_shazam_enabled = Mock(return_value=True)
        self.source._station_data = data
        api = Mock()
        api.get_station_by_id = AsyncMock(return_value=None)
        api.increment_station_clicks = AsyncMock()
        self.source._radio_api = api
        self.source._artwork = Mock()
        self.source._artwork.resolve = AsyncMock(return_value="https://art.example/cover.jpg")
        self.machine.register_source(AudioSource.RADIO, self.source)

    async def select(self):
        await self.machine.transition_to_source(AudioSource.RADIO)
        await settle()

    async def deselect(self):
        await self.machine.transition_to_source(AudioSource.NONE)
        await settle()

    async def command(self, cmd, data=None):
        await self.source.command(cmd, data)
        await settle()

    async def tick(self, times=1):
        for _ in range(times):
            await self.mpv.time_passes()
            await settle()                   # what mpv said is handled first
            await self.gate.tick()

    async def stream_title(self, title):
        self.mpv.metadata = {"icy-title": title}

    async def shazam_hears(self, track):
        self.source._shazam.current_track = track
        await self.source._shazam.on_track_changed(track)
        await settle()


@pytest.fixture
def radio(monkeypatch):
    return Radio(monkeypatch)


async def test_select_and_leave(radio):
    await radio.select()
    await radio.wire.snapshot_rest()
    await radio.deselect()
    check_recording("radio", "select_and_leave", radio.wire)


async def test_tune_play_title_stop_resume(radio):
    await radio.select()
    await radio.command("play_station", {"station_id": "fip"})
    await radio.wire.snapshot_rest()
    await radio.tick()                       # the stream starts
    await radio.stream_title("Nina Simone - Feeling Good")
    await radio.tick(4)                      # one in-band poll
    await radio.wire.snapshot_rest()
    await radio.command("stop")
    await radio.wire.snapshot_rest()
    await radio.command("resume_playback")
    await radio.tick()
    await radio.deselect()
    check_recording("radio", "tune_play_title_stop_resume", radio.wire)


async def test_shazam_names_a_silent_stream(radio):
    await radio.select()
    await radio.command("play_station", {"station_id": "nova"})
    await radio.tick()
    await radio.tick(4 * 8)                  # in-band stays empty past the grace
    await radio.shazam_hears({"title": "Sweet Jane", "artist": "Cowboy Junkies",
                              "artwork": "https://art.example/jane.jpg"})
    await radio.wire.snapshot_rest()
    await radio.deselect()
    check_recording("radio", "shazam_names_a_silent_stream", radio.wire)


async def test_stream_that_never_starts(radio):
    await radio.select()
    radio.mpv.props["idle-active"] = True
    await radio.command("play_station", {"station_id": "fip"})
    await radio.tick(5)
    await radio.wire.snapshot_rest()
    await radio.deselect()
    check_recording("radio", "stream_that_never_starts", radio.wire)


async def test_mpv_dies_mid_play(radio):
    await radio.select()
    await radio.command("play_station", {"station_id": "fip"})
    await radio.tick()
    radio.mpv.is_connected = False
    await radio.tick()
    await radio.wire.snapshot_rest()
    await radio.deselect()
    check_recording("radio", "mpv_dies_mid_play", radio.wire)


async def test_step_through_favorites(radio):
    await radio.select()
    await radio.command("next")
    await radio.tick()
    await radio.command("next")
    await radio.tick()
    await radio.command("prev")
    await radio.wire.snapshot_rest()
    await radio.deselect()
    check_recording("radio", "step_through_favorites", radio.wire)


async def test_mpv_pause_auto_stops(monkeypatch):
    radio = Radio(monkeypatch, settings={"audio.auto_stop_delay": 1})
    await radio.select()
    await radio.command("play_station", {"station_id": "fip"})
    await radio.tick()
    radio.mpv.props["pause"] = True
    await radio.tick()                       # pause edge arms the 1 s timer, which fires
    await radio.wire.snapshot_rest()
    await radio.deselect()
    check_recording("radio", "mpv_pause_auto_stops", radio.wire)
