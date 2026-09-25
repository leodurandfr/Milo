"""The CD's outside world, as measured on the unit (docs: source architecture,
phase 2): the drive, the disc, udev, the drive-status ioctl, `eject`, the
sector reader and mpv.

One world, two ways to observe it. The drive answers CDROM_DRIVE_STATUS (what
a poller reads) *and* announces itself the way udev was measured to (what a
listener hears), so a scenario states a physical gesture once and holds
whichever implementation of the source reads it — which is what lets a gap be
seen red on the code before its fix. What was measured, and is modeled here:

- insert: nothing while the drive spins (ioctl DRIVE_NOT_READY) — unless
  someone opens the drive, which makes the kernel ask and announce a `change`
  with no media — then a `change` with media and its audio track count ~10 s
  later (ioctl DISC_OK);
- eject: the command returns, then one `change` with no media; an empty slot
  drive answers TRAY_OPEN; pulling the disc out says nothing more;
- unplug: `remove`; replug with a disc in: `add`, then a `change` with media
  once it is readable — no spinning step;
- the TOC's offsets are libdiscid's, which count the 150-sector lead-in; the
  kernel (and CDROMREADAUDIO) counts from 0 (E66);
- mpv on the FIFO: the reader ending, at the leadout or on an error, is an
  `end-file reason=eof` either way.

Time is a VirtualClock the scenario advances: the source's timers, a poller's
interval and mpv's one-second loop all wait on it.
"""
import asyncio
import errno
from typing import Any, Dict, List, Optional

from backend.core import audio_source
from backend.core.models.audio_state import AudioSource
from backend.shared import mpv_audio_source
from backend.sources.cd import source as cd_module
from backend.sources.cd.models import DiscInfo, TrackInfo
from backend.sources.cd.source import CdSource
from backend.tests.golden.harness import (
    AsyncioProxy, VirtualClock, WireReader, instant_short_sleep, make_settings, make_state_machine, make_systemd, settle, use_virtual_wall,
)
from backend.tests.mpv_sim import MpvSim

# CDROM_DRIVE_STATUS answers.
TRAY_OPEN, DRIVE_NOT_READY, DISC_OK = 2, 3, 4

LEAD_IN = 150
# Longer than the old disc watcher's interval (2 s).
POLL_S = 2.1
DISC_ID = "world-disc-1"
TOC_STRING = "1 8 60150 150 15150 26400 35400 42900 48900 52650 57150"
# libdiscid offsets (lead-in included); 75 sectors a second.
OFFSETS = [150, 15150, 26400, 35400, 42900, 48900, 52650, 57150]
LENGTH = 60150
DURATIONS = [200, 150, 120, 100, 80, 50, 60, 40]
TITLES = ["One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight"]


class CdWorld(WireReader):
    """The CD source on a real state machine, in a world the scenario drives."""

    def __init__(self, monkeypatch, settings: Optional[Dict[str, Any]] = None):
        world = self
        self.clock = VirtualClock()
        use_virtual_wall(monkeypatch, self.clock)
        self.mpv = MpvSim()

        # --- the drive ---
        self.drive = False           # plugged in
        self.media: Optional[str] = None   # None | "audio" | "data"
        self.spinning = False        # a disc is in and not readable yet
        self.spin_announced = False  # the kernel has announced the spin
        self.status_asks = 0         # drive-status ioctls the source issued
        self.probe_glitches = 0      # drive-status ioctls that fail, once each
        self.eject_fails = False
        self.named = True            # MusicBrainz knows the disc
        self.listeners: List[Any] = []
        self.eject_silent = False    # the eject succeeds and udev says nothing
        self.eject_keeps_disc = False  # `eject` answers 0 and the disc stays in
        self.eject_gate: Optional[asyncio.Event] = None   # holds `eject` running
        self.toc_gate: Optional[asyncio.Event] = None     # holds a TOC read
        self.toc_failures = 0        # TOC reads that fail before one works
        self.lookups = 0             # MusicBrainz lookups started
        self.lookup_gate: Optional[asyncio.Event] = None
        self.lookup_raises = False
        # The Cover Art Archive: does it know the disc, what does it answer
        # (a URL, None for a miss, an exception), and when (a gate).
        self.cover_known = False
        self.cover_answer: Any = "/api/cd/cover/world-disc-1"
        self.cover_gate: Optional[asyncio.Event] = None
        self.cover_fetches = 0
        self.log: List[str] = []     # the order mpv and the reader were driven in

        class Reader:
            """The sector reader thread, without the thread."""

            def __init__(self, device: str = "") -> None:
                self.running = False
                self.outcome: Any = None
                world.reader = self
                self.starts: List[tuple] = []

            @property
            def reached_leadout(self) -> bool:
                return self.outcome == "leadout"

            @property
            def failure(self) -> Optional[int]:
                return self.outcome if isinstance(self.outcome, int) else None

            ready = True

            def start(self, start_lba: int, end_lba: int) -> None:
                self.running, self.outcome = True, None
                self.starts.append((start_lba, end_lba))
                world.log.append("reader.start")

            def wait_ready(self, timeout: float = 5.0) -> bool:
                world.log.append("reader.ready")
                return self.ready

            def stop(self) -> None:
                if self.running:
                    world.log.append("reader.stop")
                self.running = False

        class Data:
            """libdiscid, MusicBrainz and the archive."""

            async def initialize(self) -> None:
                return None

            async def read_disc(self):
                if world.media != "audio" or world.spinning:
                    return None
                if world.toc_gate is not None:
                    await world.toc_gate.wait()
                if world.toc_failures:
                    world.toc_failures -= 1
                    return None
                return DISC_ID, TOC_STRING, [
                    {"number": i + 1, "duration": d, "offset": o}
                    for i, (d, o) in enumerate(zip(DURATIONS, OFFSETS))
                ], LENGTH

            async def lookup_metadata(self, disc_id, toc_string, tracks) -> DiscInfo:
                world.lookups += 1
                if world.lookup_gate is not None:
                    await world.lookup_gate.wait()
                if world.lookup_raises:
                    raise OSError("the cache file could not be written")
                titles = TITLES if world.named else [f"Track {t['number']}" for t in tracks]
                info = [TrackInfo(number=t["number"], title=title, duration=t["duration"])
                        for t, title in zip(tracks, titles)]
                return DiscInfo(
                    disc_id=disc_id,
                    album="Eight" if world.named else None,
                    artist="The Octet" if world.named else None,
                    track_count=len(info), total_duration=sum(t.duration for t in info),
                    tracks=info,
                )

            def cover_fetchable(self, disc_id: str) -> bool:
                return world.cover_known

            async def fetch_cover(self, disc_id: str) -> Optional[str]:
                world.cover_fetches += 1
                if world.cover_gate is not None:
                    await world.cover_gate.wait()
                if isinstance(world.cover_answer, Exception):
                    raise world.cover_answer
                return world.cover_answer

        class Drive:
            """udev's view of sr0 and the drive-status ioctl."""

            def __init__(self, device: str = "") -> None:
                pass

            def start(self, on_event):
                from backend.sources.cd.drive import DriveEvent
                world.listeners.append(on_event)
                return DriveEvent.from_udev("present", world._props()) if world.drive else None

            def stop(self) -> None:
                world.listeners.clear()

            def status(self) -> int:
                world.status_asks += 1
                # An open() during the spin makes the kernel look, and say so.
                if world.spinning and not world.spin_announced:
                    world.spin_announced = True
                    world._announce("change", {})
                if world.probe_glitches:
                    world.probe_glitches -= 1
                    return -1                # CdDrive.status: the ioctl failed
                return world.status()

        async def sleep(delay: float, *a: Any, **k: Any) -> Any:
            if delay <= 0.5:                   # a unit's settle delay
                return await instant_short_sleep(delay)
            return await self.clock.sleep(delay)

        class CdAsyncio(AsyncioProxy):
            async def to_thread(self, fn, *a: Any, **k: Any) -> Any:
                return fn(*a, **k)

            async def create_subprocess_exec(self, *argv: Any, **k: Any):
                return world._eject(argv)

        mpv_stop = self.mpv.stop

        async def stop() -> bool:
            self.log.append("mpv.stop")
            return await mpv_stop()

        mpv_loadfile = self.mpv.loadfile

        async def loadfile(url, **k):
            self.log.append("mpv.loadfile")
            return await mpv_loadfile(url, **k)

        self.mpv.stop, self.mpv.loadfile = stop, loadfile
        monkeypatch.setattr(mpv_audio_source, "MpvController", lambda **_: self.mpv)
        monkeypatch.setattr(mpv_audio_source, "asyncio", AsyncioProxy(self.clock.sleep))
        monkeypatch.setattr(audio_source, "asyncio", AsyncioProxy(sleep))
        monkeypatch.setattr(cd_module, "asyncio", CdAsyncio(sleep))
        monkeypatch.setattr(cd_module, "CdIoctlReader", Reader)
        monkeypatch.setattr(cd_module, "CdDataService", Data)
        monkeypatch.setattr(cd_module, "CdDrive", Drive, raising=False)
        self.machine, self.recorder = make_state_machine()
        self.settings = make_settings(settings)
        self.systemd = make_systemd()
        self.source = CdSource(
            {"mpv_socket": "/nonexistent/cd.sock"}, state_machine=self.machine,
            settings_service=self.settings, systemd_manager=self.systemd,
        )
        self.machine.register_source(AudioSource.CD, self.source)

    # === Physical gestures and what the world then announces ===

    def status(self) -> int:
        if self.media is None:
            return TRAY_OPEN
        return DRIVE_NOT_READY if self.spinning else DISC_OK

    def _props(self) -> Dict[str, Any]:
        if self.media is None or self.spinning:
            return {}
        audio = len(OFFSETS) if self.media == "audio" else 0
        return {"ID_CDROM_MEDIA": "1", "ID_CDROM_MEDIA_TRACK_COUNT_AUDIO": str(audio)}

    def _announce(self, action: str, props: Optional[Dict[str, Any]] = None) -> None:
        if not self.listeners:
            return
        from backend.sources.cd.drive import DriveEvent
        event = DriveEvent.from_udev(action, {} if props is None else props)
        for listener in list(self.listeners):
            listener(event)

    async def boot(self) -> None:
        await self.source.initialize()
        await self._observed()

    async def plug(self, disc: Optional[str] = None) -> None:
        """Plug the drive in, empty or with a disc already in the slot."""
        self.drive, self.media, self.spinning = True, disc, False
        self._announce("add")
        self._announce("change", self._props())
        await self._observed()

    async def unplug(self) -> None:
        self.drive = False
        if self.reader_running():
            await self._reader_ends(errno.ENODEV)
        self._announce("remove")
        await self._observed()

    async def insert(self, disc: str = "audio", readable: bool = True) -> None:
        """Push a disc into the slot: it spins, silently for udev; `spun_up()`
        makes it readable."""
        self.media, self.spinning, self.spin_announced = disc, True, False
        await self._observed()
        if readable:
            await self.spun_up()

    async def spun_up(self) -> None:
        self.spinning = False
        self._announce("change", self._props())
        await self._observed()

    async def _observed(self) -> None:
        """Let a poller's interval pass, so a poller has seen it too."""
        await settle()
        await self.advance(POLL_S)

    def _eject(self, argv):
        world = self

        class Proc:
            returncode = 1 if world.eject_fails else 0

            async def wait(self) -> int:
                if world.eject_gate is not None:
                    await world.eject_gate.wait()
                if world.eject_keeps_disc:
                    return self.returncode
                if not world.eject_fails:
                    world.media, world.spinning = None, False
                    if not world.eject_silent:
                        world._announce("change", {})
                return self.returncode

            @property
            def stderr(self):
                class _Err:
                    async def read(self) -> bytes:
                        return b"eject: unable to eject"
                return _Err()

        return Proc()

    # === mpv and the reader ===

    def reader_running(self) -> bool:
        reader = getattr(self, "reader", None)
        return bool(reader and reader.running)

    def playhead(self, seconds_into_the_load: float) -> None:
        """mpv's time-pos: seconds since the reader's current run started."""
        self.mpv.playhead(seconds_into_the_load)

    async def _reader_ends(self, outcome) -> None:
        self.reader.outcome = outcome
        self.reader.running = False
        await self.mpv.ends("eof")

    async def disc_runs_out(self) -> None:
        await self._reader_ends("leadout")
        await self.advance(1.1)            # a one-second poller's next look

    async def read_error(self, code: int = errno.EIO) -> None:
        await self._reader_ends(code)
        await self.advance(1.1)

    async def mpv_dies(self) -> None:
        await self.mpv.dies()
        await settle()

    # === Time, lifecycle, commands ===

    async def advance(self, seconds: float) -> None:
        await self.clock.advance(seconds)

    async def select(self) -> None:
        await self.machine.transition_to_source(AudioSource.CD)
        await settle()
        await self.advance(0.1)

    async def leave(self) -> None:
        await self.machine.transition_to_source(AudioSource.NONE)
        await settle()

    async def reroute(self) -> None:
        async def apply_mode() -> None:
            return None
        await self.machine.reroute_active_source(apply_mode)
        await settle()
        await self.advance(0.1)

    async def command(self, cmd: str, data: Optional[dict] = None) -> dict:
        result = await self.source.command(cmd, data)
        await settle()
        await self.advance(0.1)
        return result

    # === What the wire says ===

    def track(self) -> Optional[int]:
        details = self.state()["details"]
        return details["current_track"] if details else None

    def position_s(self) -> float:
        return (self.position_ms() or 0) / 1000

    def availability(self) -> Optional[str]:
        """The drive's state as the wire says it (null: a disc ready to play)."""
        return self.state()["availability"]["cd"]

    def disc(self) -> Optional[Dict[str, Any]]:
        """The disc the CD's details carry, or None."""
        details = self.state()["details"]
        return details["disc"] if details else None

    def resume_ms(self) -> Optional[int]:
        """Where play would start in the track, with no session running."""
        resume = self.state()["resume"]
        return resume["position_ms"] if resume else None


def kernel_lba(track: int, seconds: float = 0) -> int:
    """Where CDROMREADAUDIO must start for `track` at `seconds` (E66)."""
    return OFFSETS[track - 1] - LEAD_IN + int(seconds) * 75
