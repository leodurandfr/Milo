"""Radio, Podcast and Music Library on the session model, driven through mpv.

Each scenario is a gap of the source audit (E-numbers, docs: source
architecture) or a decision of the target architecture, observed only through
the outside world: MpvSim for mpv (tests/mpv_sim.py — what mpv 0.40 was
measured to announce), a real AudioStateMachine for the wire, fakes for the
catalogues and the podcast progress file. What mpv says is the truth: a
session plays when mpv says sound started, ends for the reason mpv's end
gives, and survives what the architecture says it survives.
"""
import asyncio
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, Mock

import pytest

from backend.core import audio_source, state as state_module
from backend.core.models.audio_state import AudioSource
from backend.shared import mpv_audio_source
from backend.shared.mpv_audio_source import MpvAudioSource
from backend.sources.music_library import source as library_module
from backend.sources.music_library.source import MusicLibrarySource
from backend.sources.podcast.source import PodcastSource
from backend.sources.radio import source as radio_module
from backend.sources.radio.source import RadioSource
from backend.tests.golden.harness import (
    AsyncioProxy, TickGate, instant_short_sleep, make_settings, make_state_machine,
    make_systemd, settle,
)
from backend.tests.golden.test_old_wire_music_library import (
    ALBUM, FakeNavidrome, FakeShares,
)
from backend.tests.golden.test_old_wire_podcast import (
    EPISODE_A, EPISODE_B, FakeCatalog, MemoryPodcastData,
)
from backend.tests.golden.test_old_wire_radio import FIP, NOVA, FakeShazam, _RadioAsyncio
from backend.tests.mpv_sim import MpvSim

# The loading watchdog, shortened: the timers below 1 s run at once here
# (instant_short_sleep), so a stall that outlives it ends inside settle().
WATCHDOG_S = 0.5


class Rig:
    """One source on a real state machine, mpv simulated, time in steps."""

    source_enum: AudioSource

    def __init__(self, monkeypatch, settings: Optional[Dict[str, Any]] = None):
        self.mpv = MpvSim()
        self.gate = TickGate()
        monkeypatch.setattr(mpv_audio_source, "MpvController", lambda **_: self.mpv)
        monkeypatch.setattr(mpv_audio_source, "asyncio", AsyncioProxy(self.gate.sleep))
        monkeypatch.setattr(audio_source, "asyncio", AsyncioProxy(instant_short_sleep))
        monkeypatch.setattr(MpvAudioSource, "STALL_TIMEOUT_S", WATCHDOG_S, raising=False)
        monkeypatch.setattr(state_module.AudioStateMachine, "ALSA_RELEASE_SETTLE_S", 0)
        self.machine, self.recorder = make_state_machine()
        self.settings = make_settings(settings)
        self.settings.load_settings = AsyncMock(return_value={"language": "english"})
        self.systemd = make_systemd()

    def register(self, source) -> None:
        self.source = source
        self.machine.register_source(self.source_enum, source)

    async def select(self) -> None:
        await self.machine.transition_to_source(self.source_enum)
        await settle()

    async def leave(self) -> None:
        await self.machine.transition_to_source(AudioSource.NONE)
        await settle()

    async def command(self, cmd: str, data: Optional[dict] = None) -> dict:
        result = await self.source.command(cmd, data)
        await settle()
        return result

    async def tick(self, times: int = 1) -> None:
        await self.gate.tick(times)

    async def reroute(self) -> None:
        async def apply_mode() -> None:
            return None
        await self.machine.reroute_active_source(apply_mode)
        await settle()

    def state(self) -> Dict[str, Any]:
        return self.machine.get_current_state()

    def errors(self) -> List[str]:
        return [
            e["data"]["reason"] for e in self.recorder.envelopes
            if e["category"] == "source" and e["type"] == "error"
        ]

    def loads(self) -> List[tuple]:
        return [c for c in self.mpv.sent if c[0] == "loadfile"]


# === Radio ===

class RadioRig(Rig):
    source_enum = AudioSource.RADIO

    def __init__(self, monkeypatch, settings=None):
        super().__init__(monkeypatch, settings)
        monkeypatch.setattr(radio_module, "ShazamRecognitionService", FakeShazam)
        monkeypatch.setattr(radio_module, "asyncio", _RadioAsyncio())
        source = RadioSource(
            {"mpv_socket": "/nonexistent/radio.sock"}, state_machine=self.machine,
            settings_service=self.settings, systemd_manager=self.systemd,
        )
        data = Mock()
        data.initialize = AsyncMock()
        data.favorite_ids = ["fip", "nova"]
        data.is_favorite = Mock(side_effect=lambda sid: sid in ("fip", "nova"))
        data.get_favorite_metadata_local = Mock(
            side_effect=lambda sid: {"fip": FIP, "nova": NOVA}.get(sid)
        )
        data.is_station_shazam_enabled = Mock(return_value=True)
        source._station_data = data
        api = Mock()
        api.get_station_by_id = AsyncMock(return_value=None)
        api.increment_station_clicks = AsyncMock()
        source._radio_api = api
        source._artwork = Mock()
        source._artwork.resolve = AsyncMock(return_value=None)
        self.register(source)

    async def tune(self, station: dict) -> dict:
        return await self.command("play_station", {"station_id": station["id"]})

    def station(self) -> Optional[str]:
        return self.state()["metadata"].get("station_id")


@pytest.fixture
def radio(monkeypatch):
    return RadioRig(monkeypatch)


async def test_radio_stream_that_stalls_ends_and_keeps_the_station(radio):
    """E43: a stream that stops delivering (mpv: paused-for-cache, then nothing
    for minutes) used to stay ACTIVE on silence forever. Past the loading
    watchdog the session ends as a lost stream: a banner, READY, and the station
    kept so a press re-tunes it."""
    await radio.select()
    await radio.tune(FIP)
    await radio.tick()
    assert radio.state()["metadata"]["is_playing"] is True

    await radio.mpv.stalls()
    await radio.tick()
    await settle()

    state = radio.state()
    assert state["source_state"] == "ready"
    assert radio.station() == "fip"
    assert "stream_disconnected" in radio.errors()


async def test_radio_stream_that_ends_after_its_sound_is_a_lost_stream(radio):
    """A live stream has no normal end: mpv's `eof` after the first sound is the
    server going away (measured on the unit: a killed server ends in `eof`).
    Read as a finished content, the station was forgotten with no banner."""
    await radio.select()
    await radio.tune(FIP)
    await radio.mpv.ends("eof")
    await settle()

    assert radio.state()["source_state"] == "ready"
    assert radio.errors() == ["stream_disconnected"]
    assert radio.station() == "fip"


async def test_radio_dead_url_is_reported_when_mpv_says_so(radio):
    """A station whose URL does not open is reported from mpv's own end
    (end-file reason=error, 1-1.3 s measured), not after five polls."""
    radio.mpv.broken["fip.mp3"] = "loading failed"
    await radio.select()
    await radio.tune(FIP)

    assert radio.state()["source_state"] == "ready"
    assert radio.errors() == ["stream_load_failed"]
    assert radio.station() == "fip"


async def test_radio_knob_during_buffering_stops_then_retunes_the_shown_station(radio, monkeypatch):
    """E42: while a station buffers, the knob re-tuned whatever was stopped
    last — not the station on screen. A press on a loading station stops it;
    the next press brings back that same station."""
    from backend.hardware.playback_dispatch import PlaybackDispatcher

    monkeypatch.setattr(MpvAudioSource, "STALL_TIMEOUT_S", 60.0, raising=False)
    radio.mpv.auto_open = False
    await radio.select()
    await radio.tune(NOVA)
    await radio.mpv.opens()
    await radio.command("stop")
    await radio.tune(FIP)                   # buffering, no sound yet
    dispatcher = PlaybackDispatcher(radio.machine)

    await dispatcher.dispatch_play_pause()
    await settle()
    assert [load[1] for load in radio.loads()] == [NOVA["url"], FIP["url"]]
    assert radio.state()["source_state"] == "ready"

    await dispatcher.dispatch_play_pause()
    await settle()
    assert radio.loads()[-1][1] == FIP["url"]


async def test_radio_shazam_stops_with_the_session(radio):
    """E44: after mpv died, Shazam kept recording the station every 20 s."""
    await radio.select()
    await radio.tune(NOVA)                  # no in-band titles
    await radio.tick(1 + 4 * 8)             # past the in-band grace
    await settle()
    shazam = radio.source._shazam
    assert shazam.is_running

    await radio.mpv.dies()
    await radio.tick()
    await settle()

    assert not shazam.is_running
    assert radio.state()["source_state"] == "ready"


async def test_radio_reroute_mid_play_plays_the_station_again(radio):
    """Decision 2026-09-23: a multiroom toggle mid-play resumes playing."""
    await radio.select()
    await radio.tune(FIP)
    await radio.tick()

    await radio.reroute()

    assert radio.loads()[-1][1] == FIP["url"]
    state = radio.state()
    assert state["source_state"] == "active"
    assert state["metadata"]["is_playing"] is True


# === Podcast ===

class PodcastRig(Rig):
    source_enum = AudioSource.PODCAST

    def __init__(self, monkeypatch, settings=None):
        super().__init__(monkeypatch, settings)
        source = PodcastSource(
            {"mpv_socket": "/nonexistent/podcast.sock"}, state_machine=self.machine,
            settings_service=self.settings, systemd_manager=self.systemd,
        )
        self.data = MemoryPodcastData()
        source._podcast_data = self.data
        source._podcast_api = FakeCatalog()
        self.register(source)

    async def play(self, episode: dict) -> dict:
        return await self.command("play_episode", {"episode_uuid": episode["uuid"]})

    def episode(self) -> Optional[str]:
        return (self.state()["metadata"].get("current_episode") or {}).get("uuid")


@pytest.fixture
def podcast(monkeypatch):
    return PodcastRig(monkeypatch)


async def test_podcast_switching_away_keeps_the_episode_to_resume(podcast):
    """E46: a source switch handed nothing to the resume slot — coming back
    showed the episode before, or nothing. The episode left comes back, at the
    second it was left."""
    await podcast.select()
    await podcast.play(EPISODE_A)
    podcast.mpv.playhead(300)
    await podcast.tick()

    await podcast.leave()
    await podcast.select()

    state = podcast.state()
    assert state["source_state"] == "ready"
    assert podcast.episode() == EPISODE_A["uuid"]
    assert state["metadata"]["position"] == 300_000


async def test_podcast_dead_url_is_not_marked_listened(podcast):
    """E47: an episode whose URL does not open was marked "listened" and left
    the in-progress list, with no message."""
    podcast.mpv.broken["daily-0921"] = "loading failed"
    await podcast.select()
    await podcast.play(EPISODE_A)
    await podcast.tick(2)

    assert podcast.data.completed == []
    assert podcast.errors() == ["stream_load_failed"]
    assert podcast.state()["source_state"] == "ready"
    assert podcast.episode() == EPISODE_A["uuid"]


async def test_podcast_outgoing_episode_is_not_marked_listened(podcast, monkeypatch):
    """E48: a playhead read already in flight when another episode starts saw
    mpv idle between the two loads and marked the outgoing one "listened"."""
    await podcast.select()
    await podcast.play(EPISODE_A)
    podcast.mpv.playhead(120)

    read_held, progress_held = asyncio.Event(), asyncio.Event()
    real_read, real_progress = podcast.mpv.get_property, podcast.data.get_playback_progress

    async def held_read(name, timeout=None):
        if not read_held.is_set():
            await read_held.wait()
        return await real_read(name, timeout)

    async def held_progress(uuid):
        await progress_held.wait()
        return await real_progress(uuid)

    podcast.mpv.get_property = held_read
    podcast.data.get_playback_progress = held_progress
    podcast.gate._event.set()                # one monitor pass starts, and waits on mpv
    await settle()
    switching = asyncio.create_task(podcast.source.command(
        "play_episode", {"episode_uuid": EPISODE_B["uuid"]}
    ))
    await settle()                           # the switch stopped mpv, waits on the disk
    read_held.set()
    await settle()
    progress_held.set()
    await switching
    await settle()

    assert EPISODE_A["uuid"] not in podcast.data.completed


async def test_podcast_load_that_raises_is_one_failure_not_a_stuck_spinner(podcast, monkeypatch):
    """The session is open and published before its load runs: an exception
    from the load left it LOADING until the watchdog, and reported the one
    failure twice (playback_failed, then stream_load_failed 30 s later)."""
    monkeypatch.setattr(MpvAudioSource, "STALL_TIMEOUT_S", 60.0, raising=False)
    await podcast.select()

    async def broken_load(*a, **k):
        raise RuntimeError("socket write failed")
    podcast.mpv.loadfile = broken_load

    result = await podcast.play(EPISODE_A)

    assert result["success"] is False
    assert podcast.state()["source_state"] == "ready"
    assert podcast.errors() == ["stream_load_failed"]


async def test_podcast_play_starts_at_the_position_it_carries(podcast, monkeypatch):
    """E57: the resume seek went out before mpv had opened the file, was
    refused, and the request answered 400 over an episode playing from 0:00.
    The load carries the position instead: one command, no seek."""
    monkeypatch.setattr(MpvAudioSource, "STALL_TIMEOUT_S", 60.0, raising=False)
    podcast.mpv.auto_open = False
    await podcast.select()

    answer = await podcast.command(
        "play_episode", {"episode_uuid": EPISODE_A["uuid"], "position": 120}
    )
    await podcast.mpv.opens()
    await settle()

    assert answer["success"] is True
    assert podcast.loads()[-1][3] == 120
    assert not [c for c in podcast.mpv.sent if c[0] == "seek"]
    assert podcast.state()["metadata"]["is_playing"] is True


async def test_podcast_reroute_mid_play_resumes_at_the_same_second(podcast):
    """Decision 2026-09-23: a multiroom toggle mid-play comes back playing, on
    the same episode, at the same second."""
    await podcast.select()
    await podcast.play(EPISODE_A)
    podcast.mpv.playhead(640)
    await podcast.tick()

    await podcast.reroute()

    last = podcast.loads()[-1]
    assert last[1] == EPISODE_A["audio_url"] and last[3] == 640
    state = podcast.state()
    assert state["source_state"] == "active"
    assert state["metadata"]["is_playing"] is True


async def test_podcast_stalled_stream_ends_and_keeps_the_episode(podcast):
    """A stream that stops mid-episode ends as a lost stream past the loading
    watchdog: a banner, the episode kept at its second, and not "listened"."""
    await podcast.select()
    await podcast.play(EPISODE_A)
    podcast.mpv.playhead(900)
    await podcast.tick()

    await podcast.mpv.stalls()
    await podcast.tick()
    await settle()

    state = podcast.state()
    assert state["source_state"] == "ready"
    assert podcast.episode() == EPISODE_A["uuid"]
    assert state["metadata"]["position"] == 900_000
    assert "stream_disconnected" in podcast.errors()
    assert podcast.data.completed == []


# === Music Library ===

class LibraryRig(Rig):
    source_enum = AudioSource.MUSIC_LIBRARY

    def __init__(self, monkeypatch, settings=None):
        super().__init__(monkeypatch, settings)
        monkeypatch.setattr(library_module, "asyncio", AsyncioProxy(instant_short_sleep))
        monkeypatch.setattr(library_module, "NavidromeClient", FakeNavidrome)
        monkeypatch.setattr(library_module, "NetworkShareService", FakeShares)
        source = MusicLibrarySource(
            {"mpv_socket": "/nonexistent/music_library.sock"}, state_machine=self.machine,
            settings_service=self.settings, systemd_manager=self.systemd,
        )
        self.shares = source.shares
        self.register(source)

    async def play_album(self, start_index=0, library_id=None) -> dict:
        data = {"tracks": ALBUM, "start_index": start_index}
        if library_id is not None:
            data["library_id"] = library_id
        return await self.command("play_context", data)

    async def key_pulled(self) -> None:
        for entry in self.shares.entries:
            entry["mounted"] = False
        await self.shares.on_storages_changed()
        await settle()

    def track(self) -> Optional[str]:
        return self.state()["metadata"].get("track_id")


@pytest.fixture
def library(monkeypatch):
    return LibraryRig(monkeypatch)


async def test_library_storage_gone_ends_the_session_not_the_source(library):
    """E01: a storage space leaving mid-play stopped the whole source, which
    stayed selected and dead: nothing played again, from any storage, until
    it was re-selected. Only the session ends; the library plays on."""
    await library.select()
    await library.play_album(library_id=3)
    await library.tick()

    await library.key_pulled()
    assert library.state()["source_state"] == "ready"

    result = await library.play_album()      # another storage, unscoped
    assert result["success"] is True
    assert library.state()["source_state"] == "active"


async def test_library_resume_from_a_storage_gone_since_is_forgotten(library):
    """E51: a queue snapshotted before its storage left was restored on the
    next open, onto tracks that no longer exist."""
    await library.select()
    await library.play_album(library_id=3)
    library.mpv.playhead(42)
    await library.tick()
    await library.leave()

    await library.key_pulled()
    await library.select()

    state = library.state()
    assert state["source_state"] == "ready"
    assert library.track() is None


async def test_library_queue_that_cannot_load_is_reported(library):
    """E53: with Navidrome down every track fails to open, mpv skips them all,
    and the library announced a finished queue with no message."""
    library.mpv.broken["rest/stream"] = "loading failed"
    await library.select()
    await library.play_album()
    await library.tick()

    assert library.state()["source_state"] == "ready"
    assert "queue_ended" not in library.state()["metadata"]
    assert library.errors() == ["playback_failed"]


async def test_library_play_index_from_the_resume_view_plays(monkeypatch):
    """E50: the resume view is drawn like a paused queue, but tapping one of
    its tracks answered "No active queue". A resume-scope command restores
    the queue, then acts."""
    library = LibraryRig(monkeypatch, settings={"audio.auto_stop_delay": 1})
    await library.select()
    await library.play_album()
    library.mpv.playhead(30)
    await library.tick()
    await library.command("pause")            # the idle timeout ends the session
    await settle()
    assert library.state()["source_state"] == "ready"
    assert library.track() == "tr-1"          # the resume view

    result = await library.command("play_index", {"index": 2})

    assert result["success"] is True
    assert library.track() == "tr-3"
    state = library.state()
    assert state["source_state"] == "active"
    assert state["metadata"]["is_playing"] is True


async def test_library_reroute_mid_play_resumes_playing_the_same_track(library):
    """Decision 2026-09-23: a multiroom toggle mid-play comes back playing, at
    the same track and second — it used to come back paused."""
    await library.select()
    await library.play_album(start_index=1)
    library.mpv.playhead(75)
    await library.tick()

    await library.reroute()

    state = library.state()
    assert state["source_state"] == "active"
    assert library.track() == "tr-2"
    assert state["metadata"]["is_playing"] is True
    started = [c for c in library.mpv.sent if c[0] == "loadfile" and c[3]]
    assert started and started[-1][3] == 75


async def test_library_resume_snapshot_expiry_is_published(monkeypatch):
    """The snapshot expired silently: the screen kept offering a queue that a
    return to the library would no longer reopen. Its expiry is published."""
    monkeypatch.setattr(library_module, "RESUME_TTL_S", WATCHDOG_S)
    policy = getattr(MusicLibrarySource, "RESUME_POLICY", None)
    if policy is not None:
        from dataclasses import replace
        monkeypatch.setattr(MusicLibrarySource, "RESUME_POLICY", replace(policy, ttl_s=WATCHDOG_S))
    library = LibraryRig(monkeypatch, settings={"audio.auto_stop_delay": 1})
    await library.select()
    await library.play_album()
    library.mpv.playhead(30)
    await library.tick()

    await library.command("pause")            # idle timeout, then the snapshot's
    await settle()

    state = library.state()
    assert state["source_state"] == "ready"
    assert library.track() is None


# === What mpv last announced is not what it is doing now ===

async def test_library_paused_queue_stays_paused_across_a_reroute(library):
    """A multiroom toggle restarts mpv behind a held mailbox: the new mpv
    starts unpaused while the last announced pause still says paused. Trusting
    it, the restore skipped the pause and the queue the user had paused played
    out loud in every room."""
    await library.select()
    await library.play_album()
    library.mpv.playhead(20)
    await library.tick()
    await library.command("pause")

    async def unit_restarts(name):
        # systemd stops the unit: the mpv that comes back starts unpaused, and
        # has announced nothing yet.
        library.mpv.paused = False
        return True
    library.systemd.stop.side_effect = unit_restarts

    await library.reroute()

    assert library.mpv.paused is True
    state = library.state()
    assert state["source_state"] == "active"
    assert state["metadata"]["is_playing"] is False


async def test_podcast_pause_then_play_another_episode_plays(podcast):
    """A pause whose event is still in the mailbox when the next episode's
    play runs: the play trusted the stale "not paused" and loaded the new
    episode into a paused mpv — silent, while the user had pressed play."""
    await podcast.select()
    await podcast.play(EPISODE_A)
    await podcast.tick()

    await asyncio.gather(
        podcast.source.command("pause", None),
        podcast.source.command("play_episode", {"episode_uuid": EPISODE_B["uuid"]}),
    )
    await settle()

    assert podcast.mpv.paused is False
    assert podcast.episode() == EPISODE_B["uuid"]
    assert podcast.state()["metadata"]["is_playing"] is True


async def test_podcast_resumed_after_idle_end_that_fails_is_a_failed_load(monkeypatch):
    """After an idle end mpv stays paused. A resume opened its session from
    that stale pause (PAUSED, idle timer armed), then went PAUSED → PLAYING →
    LOADING on paper, which counts as heard: a load that then failed was
    reported as a lost stream instead of a stream that would not open."""
    podcast = PodcastRig(monkeypatch, settings={"audio.auto_stop_delay": 1})
    monkeypatch.setattr(MpvAudioSource, "STALL_TIMEOUT_S", 60.0)
    await podcast.select()
    await podcast.play(EPISODE_A)
    podcast.mpv.playhead(300)
    await podcast.tick()
    await podcast.command("pause")
    await settle()
    assert podcast.state()["source_state"] == "ready"

    podcast.mpv.auto_open = False
    await podcast.command("resume")
    assert podcast.state()["metadata"]["is_buffering"] is True
    await podcast.mpv.fails()
    await settle()

    assert podcast.errors()[-1] == "stream_load_failed"


async def test_radio_restart_left_by_the_previous_station_is_not_the_new_ones_sound(radio, monkeypatch):
    """playback-restart names no entry. One left over from the previous
    station, handled after the new session opened, marked the new station as
    heard: its load failing was then reported as a lost stream."""
    monkeypatch.setattr(MpvAudioSource, "STALL_TIMEOUT_S", 60.0)
    radio.mpv.auto_open = False
    await radio.select()
    await radio.tune(NOVA)
    await radio.mpv.opens()
    real_loadfile = radio.mpv.loadfile

    async def loadfile_after_a_stray_restart(url, **kwargs):
        radio.mpv._emit({"event": "playback-restart"})      # the old entry's
        return await real_loadfile(url, **kwargs)
    radio.mpv.loadfile = loadfile_after_a_stray_restart

    await radio.tune(FIP)
    await settle()
    assert radio.state()["metadata"]["is_buffering"] is True
    await radio.mpv.fails()
    await settle()

    assert radio.errors()[-1] == "stream_load_failed"


async def test_library_new_queue_mpv_refuses_leaves_nothing_playing(library):
    """The new queue's load refused before it reached mpv's playlist: the old
    session was already ended, so the old queue played on in mpv with no
    session to pause, next or stop it."""
    await library.select()
    await library.play_album()
    await library.tick()
    real_set = library.mpv.set_property

    async def refuse_pause(name, value):
        if name == "pause":
            return False
        return await real_set(name, value)
    library.mpv.set_property = refuse_pause

    result = await library.play_album(start_index=1)

    assert result["success"] is False
    assert library.mpv.current is None
    assert library.state()["source_state"] == "ready"
