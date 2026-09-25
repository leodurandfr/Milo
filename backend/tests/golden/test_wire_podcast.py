"""Podcast's wire, scenario by scenario (see harness.py for the rules)."""
import copy
from unittest.mock import AsyncMock

import pytest

from backend.core import audio_source
from backend.core.models.audio_state import AudioSource
from backend.shared import mpv_audio_source
from backend.sources.podcast.source import PodcastSource
from backend.tests.golden.harness import (
    AsyncioProxy, EventMpv, TickGate, Wire, check_recording, instant_short_sleep,
    make_settings, make_state_machine, make_systemd, settle,
)

SHOW = {
    "uuid": "1200361736", "name": "The Daily",
    "image_url": "https://img.example/daily.jpg",
}
# The shape rss_parser._episode builds; `duration` is the feed's itunes:duration.
EPISODE_A = {
    "uuid": "1200361736:a1b2c3", "guid": "daily-0921",
    "name": "The Sunday Read", "description": "A long story, read aloud.",
    "date_published": 1758441600, "duration": 1800,
    "audio_url": "https://cdn.example/daily-0921.mp3",
    "image_url": "https://img.example/daily-0921.jpg", "episode_type": "full",
    "season_number": None, "episode_number": 412, "is_explicit": False,
    "website_url": "https://daily.example/0921", "file_length": 28800000,
    "file_type": "audio/mpeg", "podcast": SHOW,
}
# A feed that publishes no itunes:duration: mpv is the first to know it.
EPISODE_B = {
    **EPISODE_A,
    "uuid": "1200361736:d4e5f6", "guid": "daily-0922", "name": "A Quiet Tuesday",
    "description": "", "duration": 0, "audio_url": "https://cdn.example/daily-0922.mp3",
    "image_url": SHOW["image_url"], "episode_number": 413,
}
EPISODES = {EPISODE_A["uuid"]: EPISODE_A, EPISODE_B["uuid"]: EPISODE_B}


class MemoryPodcastData:
    """PodcastDataService with its JSON file replaced by a dict.

    The real service writes through aiofiles and a worker thread, which
    `settle()` cannot wait for; what the source reads back (the saved position
    of an episode, the speed preference) is all that reaches the wire.
    """

    def __init__(self) -> None:
        self.progress = {}
        self.settings = {"playback_speed": 1.0}
        self.completed = []

    async def initialize(self) -> None:
        return None

    async def get_setting(self, key, default=None):
        return self.settings.get(key, default)

    async def set_setting(self, key, value) -> bool:
        self.settings[key] = value
        return True

    async def get_playback_progress(self, episode_uuid):
        return self.progress.get(episode_uuid)

    async def update_playback_progress(self, episode_uuid, position, duration, **_):
        self.progress[episode_uuid] = {"position": position, "duration": duration}
        return True

    async def mark_episode_completed(self, episode_uuid) -> bool:
        self.completed.append(episode_uuid)
        return True

    def __getattr__(self, name):
        raise AttributeError(f"MemoryPodcastData has no '{name}' — add it")


class FakeCatalog:
    """The catalogue (Apple lookup + publisher RSS): answers the two episodes."""

    def __init__(self) -> None:
        self.countries = []

    async def get_episode(self, episode_uuid, country="us"):
        self.countries.append(country)
        episode = EPISODES.get(episode_uuid)
        return copy.deepcopy(episode) if episode else None

    def __getattr__(self, name):
        raise AttributeError(f"FakeCatalog has no '{name}' — add it")


class Podcast:
    """Adapter: how each outside-world stimulus reaches PodcastSource today."""

    def __init__(self, monkeypatch, settings=None):
        self.mpv = EventMpv()
        self.gate = TickGate()
        self.gate.clocks.append(self.mpv.elapse)
        self._ending = False
        monkeypatch.setattr(mpv_audio_source, "MpvController", lambda **_: self.mpv)
        monkeypatch.setattr(mpv_audio_source, "asyncio", AsyncioProxy(self.gate.sleep))
        monkeypatch.setattr(audio_source, "asyncio", AsyncioProxy(instant_short_sleep))
        self.machine, recorder = make_state_machine()
        self.wire = Wire(self.machine, recorder)
        settings_service = make_settings(settings)
        settings_service.load_settings = AsyncMock(return_value={"language": "french"})
        self.source = PodcastSource(
            {"mpv_socket": "/nonexistent/podcast.sock"},
            state_machine=self.machine,
            settings_service=settings_service,
            systemd_manager=make_systemd(),
        )
        self.data = MemoryPodcastData()
        self.source._podcast_data = self.data
        self.source._podcast_api = FakeCatalog()
        self.machine.register_source(AudioSource.PODCAST, self.source)

    async def select(self):
        await self.machine.transition_to_source(AudioSource.PODCAST)
        await settle()

    async def deselect(self):
        await self.machine.transition_to_source(AudioSource.NONE)
        await settle()

    async def command(self, cmd, data=None):
        await self.source.command(cmd, data)
        await settle()

    async def play(self, episode):
        await self.command("play_episode", {"episode_uuid": episode["uuid"]})
        await self.mpv.time_passes()         # mpv opens the file
        await settle()

    async def tick(self, times=1):
        for _ in range(times):
            if self._ending:
                self._ending = False
                await self.mpv.ends("eof")
            await self.mpv.time_passes()
            await settle()                   # what mpv said is handled first
            await self.gate.tick()

    def playhead(self, seconds, duration=None):
        """mpv is playing the loaded file at `seconds` (duration as mpv knows it)."""
        self.mpv.props.update({
            "idle-active": False, "pause": False,
            "playback-time": seconds, "time-pos": seconds, "duration": duration,
        })

    def mpv_paused(self):
        """Paused from outside Milō's commands (mpv's own pause property)."""
        self.mpv.props["pause"] = True

    def file_ended(self):
        """The file plays out: mpv's end-file eof, seen by the next tick."""
        self._ending = True


@pytest.fixture
def podcast(monkeypatch):
    return Podcast(monkeypatch)


async def test_select_and_leave(podcast):
    await podcast.select()
    await podcast.wire.snapshot_rest()
    await podcast.deselect()
    check_recording("podcast", "select_and_leave", podcast.wire)


async def test_play_pause_resume(podcast):
    await podcast.select()
    podcast.playhead(0, 1800.0)
    await podcast.play(EPISODE_A)            # buffering, then playing, in the command
    await podcast.wire.snapshot_rest()
    podcast.playhead(7, 1800.0)
    await podcast.tick(3)
    await podcast.command("pause")
    await podcast.wire.snapshot_rest()
    await podcast.command("resume")
    podcast.playhead(12, 1800.0)
    await podcast.tick()
    await podcast.deselect()
    check_recording("podcast", "play_pause_resume", podcast.wire)


async def test_seek_and_speed(podcast):
    await podcast.select()
    await podcast.command("set_speed", {"speed": 1.5})   # stored while idle
    podcast.playhead(0, 1800.0)
    await podcast.play(EPISODE_A)
    await podcast.tick()
    await podcast.command("seek", {"position_ms": 600000})
    await podcast.tick()
    await podcast.command("seek", {"position": 1200})
    await podcast.command("set_speed", {"speed": 1.3})   # snapped to 1.25
    await podcast.wire.snapshot_rest()
    await podcast.deselect()
    check_recording("podcast", "seek_and_speed", podcast.wire)


async def test_duration_learned_from_mpv_and_periodic_sync(podcast):
    await podcast.select()
    podcast.playhead(0, None)
    await podcast.play(EPISODE_B)            # the feed gave no duration
    await podcast.tick()
    podcast.playhead(3, 2400.5)
    await podcast.tick()                     # duration edge → position push
    podcast.playhead(33, 2400.5)
    await podcast.tick(30)                   # one periodic drift correction
    await podcast.wire.snapshot_rest()
    await podcast.deselect()
    check_recording("podcast", "duration_learned_from_mpv_and_periodic_sync", podcast.wire)


async def test_episode_plays_to_the_end(podcast):
    await podcast.select()
    podcast.playhead(0, 1800.0)
    await podcast.play(EPISODE_A)
    podcast.playhead(1795, 1800.0)
    await podcast.tick()
    podcast.file_ended()
    await podcast.tick()                     # idle-active → episode_ended READY
    await podcast.wire.snapshot_rest()
    await podcast.deselect()
    check_recording("podcast", "episode_plays_to_the_end", podcast.wire)


async def test_mpv_pause_auto_stops_then_resume_reloads(monkeypatch):
    podcast = Podcast(monkeypatch, settings={"audio.auto_stop_delay": 1})
    await podcast.select()
    podcast.playhead(0, 1800.0)
    await podcast.play(EPISODE_A)
    podcast.playhead(42, 1800.0)
    await podcast.tick()
    podcast.mpv_paused()
    await podcast.tick()                     # pause edge arms the 1 s timer, which fires
    await podcast.wire.snapshot_rest()
    podcast.playhead(0, 1800.0)
    await podcast.command("resume")          # reloads the episode, seeks to 42 s
    await podcast.tick()
    await podcast.wire.snapshot_rest()
    await podcast.deselect()
    check_recording("podcast", "mpv_pause_auto_stops_then_resume_reloads", podcast.wire)


async def test_mpv_dies_mid_play_and_return(podcast):
    await podcast.select()
    podcast.playhead(0, 1800.0)
    await podcast.play(EPISODE_A)
    podcast.playhead(95, 1800.0)
    await podcast.tick()
    podcast.mpv.is_connected = False
    await podcast.tick()
    await podcast.wire.snapshot_rest()
    await podcast.deselect()
    await podcast.select()                   # the resume slot outlives the session
    await podcast.wire.snapshot_rest()
    await podcast.deselect()
    check_recording("podcast", "mpv_dies_mid_play_and_return", podcast.wire)


async def test_leave_mid_play_and_play_again(podcast):
    await podcast.select()
    podcast.playhead(0, 1800.0)
    await podcast.play(EPISODE_A)
    podcast.playhead(300, 1800.0)
    await podcast.tick()
    await podcast.deselect()                 # progress saved at 300 s
    await podcast.select()
    await podcast.wire.snapshot_rest()
    podcast.playhead(0, 1800.0)
    await podcast.play(EPISODE_A)            # resumes from the saved row
    await podcast.tick()
    podcast.playhead(0, None)
    await podcast.play(EPISODE_B)            # switch episode while playing
    await podcast.wire.snapshot_rest()
    await podcast.deselect()
    check_recording("podcast", "leave_mid_play_and_play_again", podcast.wire)
