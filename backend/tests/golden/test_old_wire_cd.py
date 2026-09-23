"""CD's old wire, scenario by scenario (see harness.py for the rules)."""
import asyncio
from typing import Any, Dict, List, Optional

import pytest

from backend.core import audio_source
from backend.core.models.audio_state import AudioSource
from backend.shared import mpv_audio_source
from backend.sources.cd import source as cd_module
from backend.sources.cd.data import CDS_DISC_OK, CDS_DRIVE_NOT_READY
from backend.sources.cd.models import DiscInfo, TrackInfo
from backend.sources.cd.source import CdSource
from backend.tests.golden.harness import (
    AsyncioProxy, FakeMpv, TickGate, Wire, check_recording, instant_short_sleep,
    make_settings, make_state_machine, make_systemd, settle,
)

# CDROM_DRIVE_STATUS answers the watcher reads besides the two the source names.
CDS_NO_DISC = 1
CDS_TRAY_OPEN = 2

# One disc, three tracks: the TOC as libdiscid reads it (offsets in sectors,
# 75 per second, the first track after the 150-sector lead-in).
DISC_ID = "golden-disc-1"
TOC_STRING = "1 3 39900 150 15150 26400"
TOC = [
    {"number": 1, "duration": 200, "offset": 150},
    {"number": 2, "duration": 150, "offset": 15150},
    {"number": 3, "duration": 180, "offset": 26400},
]
DISC_END_LBA = 39900
TITLES = ["Blue", "California", "River"]


class CdMpv(FakeMpv):
    """mpv as CD drives it: a FIFO load restarts the clock, a stop idles it."""

    async def ensure_connected(self) -> bool:
        return self.is_connected

    async def load_stream(self, url: str, *a: Any, **k: Any) -> bool:
        loaded = await super().load_stream(url)
        if loaded:
            self.props["time-pos"] = 0
            self.props["playback-time"] = 0
        return loaded

    async def stop(self) -> bool:
        self.props["time-pos"] = None
        self.props["playback-time"] = None
        return True


class FakeReader:
    """The ioctl reader thread, without the thread: the FIFO opens at once."""

    def __init__(self, device: str = "") -> None:
        self.running = False
        self.starts: List[int] = []

    @property
    def is_running(self) -> bool:
        return self.running

    def start(self, start_lba: int, end_lba: int) -> None:
        self.running = True
        self.starts.append(start_lba)

    def wait_ready(self, timeout: float = 5.0) -> bool:
        return True

    def stop(self) -> None:
        self.running = False


class FakeCdData:
    """The drive (presence + CDROM_DRIVE_STATUS), libdiscid, MusicBrainz and
    the Cover Art Archive, at the boundary the source calls them through."""

    def __init__(self) -> None:
        self.drive = False
        self.status = CDS_NO_DISC
        self.disc_loaded = False
        self.known: Dict[str, Dict[str, Any]] = {}
        self.on_disk: set = set()
        self.archive = asyncio.Event()

    async def initialize(self) -> None:
        return None

    def probe_drive_and_disc(self):
        return (True, self.status) if self.drive else (False, -1)

    async def read_disc(self):
        if not self.disc_loaded:
            return None
        return DISC_ID, TOC_STRING, [dict(t) for t in TOC], DISC_END_LBA

    async def lookup_metadata(self, disc_id, toc_string, tracks) -> DiscInfo:
        named = self.known.get(disc_id)
        titles = named["titles"] if named else [f"Track {t['number']}" for t in tracks]
        info = [
            TrackInfo(number=t["number"], title=title, duration=t["duration"])
            for t, title in zip(tracks, titles)
        ]
        return DiscInfo(
            disc_id=disc_id,
            album=named["album"] if named else None,
            artist=named["artist"] if named else None,
            year=named["year"] if named else None,
            cover_url=self._cover_url(disc_id),
            track_count=len(info),
            total_duration=sum(t.duration for t in info),
            tracks=info,
        )

    def cover_fetchable(self, disc_id: str) -> bool:
        return disc_id in self.known

    async def fetch_cover(self, disc_id: str) -> Optional[str]:
        await self.archive.wait()
        self.on_disk.add(disc_id)
        return self._cover_url(disc_id)

    def _cover_url(self, disc_id: str) -> Optional[str]:
        return f"/api/cd/cover/{disc_id}" if disc_id in self.on_disk else None


class _Proc:
    """`eject`'s process: the tray opens and the command answers 0."""

    returncode = 0

    async def wait(self) -> int:
        return 0


class CdAsyncio(AsyncioProxy):
    """The cd module's `asyncio`: the watcher's poll is a gate, threads run
    inline (a real thread finishing after `settle()` returned would make the
    recording depend on the scheduler), and `eject` is the drive's."""

    def __init__(self, gate: TickGate, eject) -> None:
        super().__init__(self._sleep)
        self._gate = gate
        self._eject = eject

    async def _sleep(self, delay: float, *a: Any, **k: Any) -> Any:
        if delay == cd_module.DISC_POLL_INTERVAL_S:
            return await self._gate.sleep(delay)
        return await instant_short_sleep(delay, *a, **k)

    async def to_thread(self, fn, *a: Any, **k: Any) -> Any:
        return fn(*a, **k)

    async def create_subprocess_exec(self, *argv: Any, **k: Any) -> _Proc:
        return self._eject(argv)


class Cd:
    """Adapter: how each outside-world stimulus reaches CdSource today."""

    def __init__(self, monkeypatch, settings=None):
        self.data = FakeCdData()
        self.mpv: Optional[CdMpv] = None
        self.monitor_gate = TickGate()
        self.drive_gate = TickGate()
        self.ejected: List[tuple] = []
        # The MusicBrainz retry is throttled on monotonic(); a fixed clock the
        # scenario advances makes that throttle a step instead of wall time.
        self.clock = 1000.0
        monkeypatch.setattr(cd_module, "monotonic", lambda: self.clock)
        monkeypatch.setattr(mpv_audio_source, "MpvController", self._new_mpv)
        monkeypatch.setattr(mpv_audio_source, "asyncio", AsyncioProxy(self.monitor_gate.sleep))
        monkeypatch.setattr(audio_source, "asyncio", AsyncioProxy(instant_short_sleep))
        monkeypatch.setattr(cd_module, "asyncio", CdAsyncio(self.drive_gate, self._eject))
        monkeypatch.setattr(cd_module, "CdIoctlReader", FakeReader)
        monkeypatch.setattr(cd_module, "CdDataService", lambda: self.data)
        self.machine, recorder = make_state_machine()
        self.wire = Wire(self.machine, recorder)
        self.source = CdSource(
            {"mpv_socket": "/nonexistent/cd.sock"},
            state_machine=self.machine,
            settings_service=make_settings(settings),
            systemd_manager=make_systemd(),
        )
        self.reader: FakeReader = self.source._reader
        self.machine.register_source(AudioSource.CD, self.source)

    def _new_mpv(self, **_: Any) -> CdMpv:
        # One per attach: every source start is a fresh milo-cd process.
        self.mpv = CdMpv()
        return self.mpv

    def _eject(self, argv) -> _Proc:
        self.ejected.append(argv)
        self.data.status = CDS_TRAY_OPEN
        self.data.disc_loaded = False
        return _Proc()

    # --- lifecycle ---------------------------------------------------------

    async def boot(self):
        """App startup: initialize() starts the permanent disc watcher."""
        await self.source.initialize()
        await settle()

    async def select(self):
        await self.machine.transition_to_source(AudioSource.CD)
        await settle()

    async def deselect(self):
        await self.machine.transition_to_source(AudioSource.NONE)
        await settle()

    async def command(self, cmd, data=None):
        await self.source.command(cmd, data)
        await settle()

    # --- the drive ---------------------------------------------------------

    def plug_drive(self, status=CDS_NO_DISC):
        self.data.drive = True
        self.data.status = status

    def unplug_drive(self):
        self.data.drive = False

    def insert_disc(self, status):
        self.data.status = status
        self.data.disc_loaded = status == CDS_DISC_OK

    def musicbrainz_knows_the_disc(self):
        self.data.known[DISC_ID] = {
            "album": "Blue", "artist": "Joni Mitchell", "year": "1971",
            "titles": TITLES,
        }

    async def archive_answers(self):
        self.data.archive.set()
        await settle()

    async def drive_tick(self, times=1):
        await self.drive_gate.tick(times)

    def advance_clock(self, seconds):
        self.clock += seconds

    # --- mpv and the reader ------------------------------------------------

    async def tick(self, times=1):
        await self.monitor_gate.tick(times)

    def playhead(self, seconds):
        self.mpv.props["time-pos"] = seconds
        self.mpv.props["playback-time"] = seconds

    def disc_runs_out(self):
        """The reader reached the leadout; mpv drained the FIFO and idles."""
        self.reader.running = False
        self.playhead(None)


@pytest.fixture
async def make_cd(monkeypatch):
    made: List[Cd] = []

    async def factory(settings=None, drive=False, status=CDS_NO_DISC, named=True):
        cd = Cd(monkeypatch, settings=settings)
        if named:
            cd.musicbrainz_knows_the_disc()
        if drive:
            cd.plug_drive(status)
            cd.data.disc_loaded = status == CDS_DISC_OK
        made.append(cd)
        await cd.boot()
        return cd

    yield factory
    # The watcher is permanent; it is the adapter's to end, not the scenario's.
    for cd in made:
        task = cd.source._disc_watcher_task
        if task:
            task.cancel()
    await settle()


async def _disc_ready_before_select(make_cd, **kw):
    """A known disc sat in the drive at boot: the watcher read its TOC only."""
    cd = await make_cd(drive=True, status=CDS_DISC_OK, **kw)
    await cd.drive_tick()
    return cd


async def test_no_drive_then_empty_drive(make_cd):
    cd = await make_cd()
    await cd.select()
    await cd.drive_tick()                    # still no drive: nothing to say
    await cd.wire.snapshot_rest()
    cd.plug_drive(CDS_NO_DISC)
    await cd.drive_tick()                    # drive appears, tray empty
    await cd.wire.snapshot_rest()
    cd.unplug_drive()
    await cd.drive_tick()
    await cd.deselect()
    check_recording("cd", "no_drive_then_empty_drive", cd.wire)


async def test_unknown_disc_inserted_while_selected_then_named(make_cd):
    cd = await make_cd(drive=True, named=False)
    await cd.drive_tick()                    # drive seen before the source opens
    await cd.select()
    cd.insert_disc(CDS_DRIVE_NOT_READY)
    await cd.drive_tick()                    # spinning up: presence shown
    cd.insert_disc(CDS_DISC_OK)
    await cd.drive_tick()                    # TOC read, fallback titles, auto-play
    cd.playhead(3)
    await cd.tick()
    await cd.wire.snapshot_rest()
    cd.musicbrainz_knows_the_disc()
    cd.advance_clock(60)
    await cd.drive_tick()                    # the throttled retry names it
    await cd.archive_answers()               # then its jacket arrives
    await cd.wire.snapshot_rest()
    await cd.deselect()
    check_recording("cd", "unknown_disc_inserted_while_selected_then_named", cd.wire)


async def test_select_with_disc_preloads_then_play_pause_resume(make_cd):
    cd = await _disc_ready_before_select(make_cd)
    await cd.select()                        # lookup, then track 1 parked paused
    await cd.wire.snapshot_rest()
    await cd.archive_answers()
    await cd.command("resume")
    cd.playhead(12.5)
    await cd.tick()
    await cd.command("pause")
    await cd.wire.snapshot_rest()
    await cd.command("resume")
    cd.playhead(14)
    await cd.tick()
    await cd.deselect()
    check_recording("cd", "select_with_disc_preloads_then_play_pause_resume", cd.wire)


async def test_next_prev_seek(make_cd):
    cd = await _disc_ready_before_select(make_cd)
    await cd.select()
    await cd.archive_answers()
    await cd.command("resume")
    cd.playhead(10)
    await cd.tick()
    await cd.command("next")                 # track 2
    cd.playhead(10)
    await cd.tick()
    await cd.command("prev")                 # past the threshold: restart track 2
    await cd.command("prev")                 # at 0: step back to track 1
    await cd.command("seek", {"position_ms": 60000})
    cd.playhead(1)
    await cd.tick()
    await cd.wire.snapshot_rest()
    await cd.command("pause")
    await cd.command("seek", {"position_ms": 90000})   # moves the playhead, stays paused
    await cd.wire.snapshot_rest()
    await cd.deselect()
    check_recording("cd", "next_prev_seek", cd.wire)


async def test_auto_advance_and_end_of_disc(make_cd):
    cd = await _disc_ready_before_select(make_cd)
    await cd.select()
    await cd.archive_answers()
    await cd.command("play_track", {"track_number": 2})
    cd.playhead(1)
    await cd.tick()
    cd.playhead(150)                         # the reader crosses into track 3
    await cd.tick()
    cd.playhead(200)
    await cd.tick()
    cd.disc_runs_out()
    await cd.tick()                          # album finished: back to track 1, idle
    await cd.wire.snapshot_rest()
    await cd.command("resume")               # replays from track 1
    await cd.deselect()
    check_recording("cd", "auto_advance_and_end_of_disc", cd.wire)


async def test_eject_while_playing(make_cd):
    cd = await _disc_ready_before_select(make_cd)
    await cd.select()
    await cd.archive_answers()
    await cd.command("resume")
    cd.playhead(5)
    await cd.tick()
    await cd.command("eject")
    await cd.wire.snapshot_rest()
    await cd.drive_tick()                    # the tray is open: the disc is gone
    await cd.wire.snapshot_rest()
    await cd.deselect()
    check_recording("cd", "eject_while_playing", cd.wire)


async def test_paused_disc_auto_stops_and_resumes(make_cd):
    cd = await _disc_ready_before_select(make_cd, settings={"audio.auto_stop_delay": 1})
    await cd.select()                        # the parked preload times out at once
    await cd.archive_answers()
    await cd.command("resume")               # idle: full restart at track 1, 0:00
    cd.playhead(30)
    await cd.tick()
    await cd.command("pause")                # arms the 1 s timer, which fires
    await cd.wire.snapshot_rest()
    await cd.command("resume")               # restarts at the kept resume point
    await cd.deselect()
    check_recording("cd", "paused_disc_auto_stops_and_resumes", cd.wire)


async def test_mpv_dies_mid_play(make_cd):
    cd = await _disc_ready_before_select(make_cd)
    await cd.select()
    await cd.archive_answers()
    await cd.command("resume")
    cd.playhead(8)
    await cd.tick()
    cd.mpv.is_connected = False
    await cd.tick()
    await cd.wire.snapshot_rest()
    await cd.deselect()
    check_recording("cd", "mpv_dies_mid_play", cd.wire)
