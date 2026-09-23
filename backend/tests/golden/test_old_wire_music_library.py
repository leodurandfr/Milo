"""Music Library's old wire, scenario by scenario (see harness.py for the rules)."""
import copy

import pytest

from backend.core import audio_source
from backend.core.models.audio_state import AudioSource
from backend.shared import mpv_audio_source
from backend.sources.music_library import source as library_module
from backend.sources.music_library.source import MusicLibrarySource
from backend.tests.golden.harness import (
    AsyncioProxy, EventMpv, TickGate, Wire, check_recording, instant_short_sleep,
    make_settings, make_state_machine, make_systemd, settle,
)


def _song(song_id, track, title, duration, **extra):
    """A Subsonic song dict as getAlbum returns it (echoed verbatim as the queue)."""
    return {
        "id": song_id, "parent": "al-kob", "isDir": False, "title": title,
        "album": "Kind of Blue", "artist": "Miles Davis", "track": track,
        "year": 1959, "genre": "Jazz", "coverArt": "al-kob", "size": 30_000_000,
        "contentType": "audio/flac", "suffix": "flac", "duration": duration,
        "bitRate": 900, "path": f"Miles Davis/Kind of Blue/0{track} {title}.flac",
        "discNumber": 1, "albumId": "al-kob", "artistId": "ar-miles",
        "type": "music", **extra,
    }


ALBUM = [
    _song("tr-1", 1, "So What", 562),
    _song("tr-2", 2, "Freddie Freeloader", 586),
    # A file whose tags carry no length: Subsonic omits `duration`, mpv learns it.
    {k: v for k, v in _song("tr-3", 3, "Blue in Green", 0).items() if k != "duration"},
]
# What mpv reports once each file is open.
LENGTHS = {"tr-1": 562.3, "tr-2": 586.1, "tr-3": 337.8}

USB_KEY = {
    "kind": "usb", "id": "7A3F-19C2", "name": "Jazz key", "label": "JAZZ",
    "mountpoint": "/media/milo/JAZZ", "mounted": True, "library_id": 3,
    "track_count": 412, "album_count": 38, "missing_count": 0,
}


class FakeNavidrome:
    """The Subsonic client as playback uses it: stream URLs and scrobbles."""

    def __init__(self) -> None:
        self.scrobbles = []

    @classmethod
    def from_cred_file(cls, *a, **k):
        return cls()

    def stream_url(self, song_id):
        return f"http://127.0.0.1:4533/rest/stream?id={song_id}&format=raw"

    async def scrobble(self, song_id, submission=True):
        self.scrobbles.append((song_id, submission))
        return True

    def __getattr__(self, name):
        raise AttributeError(f"FakeNavidrome has no '{name}' — add it")


class FakeShares:
    """The storage layer (shares + USB watcher): one USB key, plugged in."""

    def __init__(self, navidrome_provider, on_catalog_changed, on_storages_changed):
        self.on_storages_changed = on_storages_changed
        self.entries = [dict(USB_KEY)]
        self.scan_requests = 0

    async def initialize(self):
        return None

    async def request_scan(self):
        self.scan_requests += 1

    async def storages(self):
        return copy.deepcopy(self.entries)

    async def storages_with_stats(self):
        return copy.deepcopy(self.entries)

    def scan_state(self):
        return {"scanning": False, "catalog_ready": True}

    def __getattr__(self, name):
        raise AttributeError(f"FakeShares has no '{name}' — add it")


def PlaylistMpv():
    """The event mpv, with each file's length known once it opens (LENGTHS)."""
    mpv = EventMpv()
    mpv.default_duration = None
    for song_id, seconds in LENGTHS.items():
        mpv.durations[f"id={song_id}&"] = seconds
    return mpv


class Library:
    """Adapter: how each outside-world stimulus reaches MusicLibrarySource today."""

    def __init__(self, monkeypatch, settings=None):
        self.mpv = PlaylistMpv()
        self.gate = TickGate()
        self._next_event = None
        monkeypatch.setattr(mpv_audio_source, "MpvController", lambda **_: self.mpv)
        monkeypatch.setattr(mpv_audio_source, "asyncio", AsyncioProxy(self.gate.sleep))
        monkeypatch.setattr(audio_source, "asyncio", AsyncioProxy(instant_short_sleep))
        monkeypatch.setattr(library_module, "NavidromeClient", FakeNavidrome)
        monkeypatch.setattr(library_module, "NetworkShareService", FakeShares)
        self.machine, recorder = make_state_machine()
        self.wire = Wire(self.machine, recorder)
        self.source = MusicLibrarySource(
            {"mpv_socket": "/nonexistent/music_library.sock"},
            state_machine=self.machine,
            settings_service=make_settings(settings),
            systemd_manager=make_systemd(),
        )
        self.shares = self.source.shares
        self.machine.register_source(AudioSource.MUSIC_LIBRARY, self.source)

    async def select(self):
        await self.machine.transition_to_source(AudioSource.MUSIC_LIBRARY)
        await settle()

    async def deselect(self):
        await self.machine.transition_to_source(AudioSource.NONE)
        await settle()

    async def command(self, cmd, data=None):
        await self.source.command(cmd, data)
        await settle()

    async def play_album(self, start_index=0, library_id=None):
        data = {"tracks": ALBUM, "start_index": start_index}
        if library_id is not None:
            data["library_id"] = library_id
        await self.command("play_context", data)

    async def tick(self, times=1):
        for _ in range(times):
            if self._next_event is not None:
                event, self._next_event = self._next_event, None
                await event()
            await self.mpv.time_passes()
            await settle()                   # what mpv said is handled first
            await self.gate.tick()

    def playhead(self, seconds):
        self.mpv.position = seconds

    def mpv_moves_on(self):
        """Gapless: mpv steps to the next playlist entry by itself."""
        async def moves_on():
            await self.mpv.ends("eof")
            await self.mpv.opens()
            self.playhead(0.4)
        self._next_event = moves_on

    def queue_played_out(self):
        """Past the last entry mpv ends it and idles."""
        async def played_out():
            await self.mpv.ends("eof")
        self._next_event = played_out

    async def key_pulled(self):
        """The USB watcher sees the key leave and calls the storages hook."""
        for entry in self.shares.entries:
            entry["mounted"] = False
        await self.shares.on_storages_changed()
        await settle()


@pytest.fixture
def library(monkeypatch):
    return Library(monkeypatch)


async def test_select_and_leave(library):
    await library.select()
    await library.wire.snapshot_rest()
    await library.deselect()
    check_recording("music_library", "select_and_leave", library.wire)


async def test_play_pause_resume(library):
    await library.select()
    await library.play_album()               # buffering until the playhead moves
    await library.wire.snapshot_rest()
    library.playhead(1.2)
    await library.tick()                     # buffering → playing
    library.playhead(4.9)
    await library.tick(3)
    await library.command("pause")
    await library.wire.snapshot_rest()
    await library.command("resume")
    library.playhead(6.0)
    await library.tick()
    await library.deselect()
    check_recording("music_library", "play_pause_resume", library.wire)


async def test_transport_next_prev_index_seek(library):
    await library.select()
    await library.play_album()
    library.playhead(2.0)
    await library.tick()
    await library.command("next")
    library.playhead(10.5)
    await library.tick()
    await library.command("prev")            # past 3 s: restarts the track
    library.playhead(1.0)
    await library.command("prev")            # within 3 s: previous track
    library.playhead(0.8)
    await library.tick()
    await library.command("play_index", {"index": 2})
    library.playhead(1.5)
    await library.tick()
    await library.command("seek", {"position_ms": 90000})
    await library.wire.snapshot_rest()
    await library.deselect()
    check_recording("music_library", "transport_next_prev_index_seek", library.wire)


async def test_gapless_advance_to_queue_end(library):
    await library.select()
    await library.play_album(start_index=1)
    library.playhead(1.0)
    await library.tick()
    library.playhead(585.5)
    await library.tick(30)                   # one periodic drift correction
    library.mpv_moves_on()                   # into the track with no tagged length
    await library.tick()
    await library.wire.snapshot_rest()
    library.queue_played_out()
    await library.tick()                     # idle-active → queue_ended READY
    await library.wire.snapshot_rest()
    await library.deselect()
    check_recording("music_library", "gapless_advance_to_queue_end", library.wire)


async def test_pause_auto_stops_then_resume_reopens(monkeypatch):
    library = Library(monkeypatch, settings={"audio.auto_stop_delay": 1})
    await library.select()
    await library.play_album(start_index=1)
    library.playhead(1.0)
    await library.tick()
    library.playhead(75.0)
    await library.tick()
    await library.command("pause")           # arms the 1 s timer, which fires
    await library.wire.snapshot_rest()
    await library.command("resume")          # reloads the saved queue at 75 s
    library.playhead(76.0)
    await library.tick()
    await library.wire.snapshot_rest()
    await library.deselect()
    check_recording("music_library", "pause_auto_stops_then_resume_reopens", library.wire)


async def test_leave_and_return_restores_paused(library):
    await library.select()
    await library.play_album()
    library.playhead(1.0)
    await library.tick()
    library.playhead(42.0)
    await library.tick()
    await library.deselect()                 # the session is snapshotted
    await library.select()                   # restored paused at 42 s
    await library.wire.snapshot_rest()
    await library.command("stop")            # an explicit Stop forgets it
    await library.wire.snapshot_rest()
    await library.deselect()
    check_recording("music_library", "leave_and_return_restores_paused", library.wire)


async def test_mpv_dies_mid_play_and_return(library):
    await library.select()
    await library.play_album(start_index=1)
    library.playhead(1.0)
    await library.tick()
    library.playhead(120.0)
    await library.tick()
    library.mpv.is_connected = False
    await library.tick()
    await library.wire.snapshot_rest()
    await library.deselect()
    await library.select()                   # the snapshot taken at the drop reopens
    await library.wire.snapshot_rest()
    await library.deselect()
    check_recording("music_library", "mpv_dies_mid_play_and_return", library.wire)


async def test_usb_key_pulled_stops_playback(library):
    await library.select()
    await library.play_album(library_id=3)
    library.playhead(1.0)
    await library.tick()
    await library.key_pulled()
    await library.wire.snapshot_rest()
    await library.deselect()
    await library.select()                   # nothing to reopen
    await library.deselect()
    check_recording("music_library", "usb_key_pulled_stops_playback", library.wire)
