# backend/sources/cd/source.py
"""
CD audio source: the drive's sectors, read by ioctl, played by mpv through a FIFO.

Two axes live here (docs: source architecture):
- the device — what is in the drive (`DiscState`), moved by what udev
  announces (drive.py). The drive-status ioctl answers the one question udev
  cannot: a disc spinning up, or no disc — and, while CD is on screen over an
  empty drive, it is asked every 2 s, because udev hears of an inserted disc
  only once it is readable. It outlives sessions: a disc stays read across
  source switches, and its work (a TOC read, a drive timer) carries a
  DeviceToken, which a STOP neither voids nor cuts.
- the session — the disc being played, on MpvAudioSource's machinery: its
  phase is what mpv announces, and it ends for a named reason that decides
  what "play" brings back (RESUME_POLICY).

Playback: the reader thread reads sectors from /dev/sr0 into a FIFO, from a
start LBA to the leadout; mpv plays the FIFO (rawaudio). A track change or a
seek restarts both at the new LBA (~0.5 s gap), and the track the playhead is
in is mapped from mpv's time-pos. mpv sees the reader end — at the leadout or
on a read error — as the same `eof` (measured), so the reader says which.

Rules:
- NEVER start the reader without the matching mpv load (FIFO deadlock).
- NEVER start mpv, run a MusicBrainz lookup, or begin playback while CD is not
  the active source — the drive is read while inactive, nothing else.
- A disc already in the drive when the source opens (or when the drive is
  replugged) is preloaded paused; only a disc *inserted* while CD is the
  active source plays by itself (owner decision, 2026-09-23).
"""
import asyncio
import errno
import os
import time
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel

from backend.config.constants import CD_DEVICE, CD_PREV_RESTART_THRESHOLD_S
from backend.core.audio_source import DeviceToken
from backend.core.models.audio_state import AudioSource
from backend.core.models.session import (
    CommandScope, EndReason, IdlePolicy, Phase, PhaseEvent, ResumePoint, ReroutePolicy,
    ResumePolicy,
)
from backend.core.models.audio_wire import CdDetails, CdDisc, CdTrack, ResumeView
from backend.core.models.commands import SkipParams, skip_target
from backend.core.models.ws_events import SourceErrorReason
from backend.shared.background import BackgroundTaskSet
from backend.shared.decorators import handle_errors
from backend.shared.mpv_audio_source import MpvAudioSource, MpvSession
from backend.sources.cd.data import CdDataService
from backend.sources.cd.drive import (
    CDS_DISC_OK, CDS_DRIVE_NOT_READY, CdDrive, DiscState, DriveEvent,
)
from backend.sources.cd.models import DiscInfo, PlayTrackParams, SeekParams, TrackInfo
from backend.sources.cd.reader import (
    CD_FIFO_PATH, LEAD_IN_SECTORS, SECTORS_PER_SECOND, CdIoctlReader,
)

# Retry MusicBrainz when the lookup fell through to the fallback DiscInfo
# (typically DNS not ready at boot when the disc was first read).
METADATA_RETRY_INTERVAL_S = 60.0

# A disc still unreadable this long after it went in is reported unreadable
# (E36). Measured: 10.2-10.3 s from insertion to a readable TOC, 8.7 s after
# a replug.
READING_TIMEOUT_S = 30.0

# A TOC read that fails is tried again, a few times: the drive can still be
# settling when udev first says the media is readable. A read that never
# returns is the reading watchdog's (READING_TIMEOUT_S).
TOC_READ_ATTEMPTS = 3
TOC_RETRY_S = 2.0

# An eject that succeeded is followed by udev's no-media change (measured,
# every time). Past this without one, the drive is asked directly (E35).
EJECT_CONFIRM_S = 5.0

# The drive-status ioctl's open blocks while a drive spins up (measured: 8 s
# after a replug): a drive that has not answered in this long is spinning.
STATUS_TIMEOUT_S = 2.0

# While CD is on screen and the drive is empty, how often the drive is asked
# whether a disc is spinning up. udev says nothing until the disc is readable
# (~10 s, measured): the kernel's own poll relies on the drive's event report,
# and only an open() makes it ask TEST UNIT READY. Owner decision 2026-09-23:
# this probe can only move EMPTY to READING — it never removes a disc (E37).
INSERTION_PROBE_S = 2.0

# A reader failing on one of these lost the drive or its disc, not a sector.
GONE_ERRNOS = frozenset({errno.ENODEV, errno.ENOMEDIUM, errno.ENXIO})

# The states in which a disc is in the drive.
_HOLDS_A_DISC = frozenset({
    DiscState.READING, DiscState.IDENTIFYING, DiscState.READY, DiscState.EJECTING,
})


@dataclass(frozen=True)
class Toc:
    """A disc's table of contents: libdiscid's rows (for MusicBrainz) and the
    kernel's LBAs (for the reader)."""
    disc_id: str
    toc_string: str
    tracks: List[Dict[str, Any]]
    lbas: List[int]
    end_lba: int


@dataclass(eq=False)
class CdSession(MpvSession):
    """The disc being played. `track` is the track the playhead is in, which
    changes on its own as the reader plays through a track boundary."""
    disc_id: str = ""
    track: int = 1
    track_position: float = 0.0   # seconds into `track`
    start_lba: int = 0            # where the reader's current run began


class CdSource(MpvAudioSource):
    """CD source (Family C — active player): UI-driven playback, rich metadata."""

    IDLE_POLICY = IdlePolicy.AUTO_STOP
    REROUTE = ReroutePolicy.RESTART_AND_RESTORE
    RESUME_POLICY = ResumePolicy(
        capture_on=frozenset({
            EndReason.IDLE_TIMEOUT, EndReason.SOURCE_SWITCH, EndReason.REROUTE,
            # The disc is still in the drive: a crash, a load or a sector that
            # failed leaves the track to play again, where it was (E61).
            EndReason.DAEMON_DIED, EndReason.LOAD_FAILED, EndReason.STREAM_LOST,
        }),
        # The disc played out (back to track 1), was ejected, or left the
        # drive. SENDER_LEFT cannot happen here.
        forget_on=frozenset({
            EndReason.EOF, EndReason.USER_STOP, EndReason.STORAGE_GONE,
            EndReason.SENDER_LEFT,
        }),
        restore_on_start=True,
    )
    SESSION_DAEMON = False

    COMMANDS = {
        "play_track": PlayTrackParams,
        "pause": None,
        "resume": None,
        "next": None,
        "prev": None,
        "seek": SeekParams,
        "skip": SkipParams,
        "eject": None,
    }
    COMMAND_SCOPES = {
        "play_track": CommandScope.CONTENT,
        "pause": CommandScope.SESSION,
        "resume": CommandScope.RESUME,
        "next": CommandScope.RESUME,
        "prev": CommandScope.RESUME,
        # With nothing loaded, a seek moves the resume point (E39).
        "seek": CommandScope.RESUME,
        "skip": CommandScope.RESUME,
        "eject": CommandScope.DEVICE,
    }

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        state_machine=None,
        settings_service=None,
        systemd_manager=None,
    ):
        super().__init__(
            source_id="cd",
            service_name="milo-cd.service",
            state_machine=state_machine,
            systemd_manager=systemd_manager,
            settings_service=settings_service,
            config=config,
        )
        self._data_service = CdDataService()
        self._reader = CdIoctlReader(device=CD_DEVICE)
        self._drive = CdDrive(CD_DEVICE)

        # The device axis.
        self._device_token = DeviceToken()     # udev's news is never stale
        self._device_tasks = BackgroundTaskSet(self._logger, "source.cd.device")
        self._disc_state = DiscState.NO_DRIVE
        self._toc: Optional[Toc] = None
        self._disc: Optional[DiscInfo] = None
        # The disc read now: a result or a timer carrying another one is about
        # a disc that has left.
        self._generation: Optional[DeviceToken] = None
        self._toc_attempts = 0
        self._lookup_for: Optional[DeviceToken] = None
        self._metadata_retry_pending = False
        # Set by an insertion while CD is the active source: that disc plays
        # once it is ready.
        self._play_on_ready = False
        # Discs whose jacket is being fetched. Published as `artwork_pending` so
        # the player veils its placeholder instead of swapping it for the cover
        # a second later.
        self._covers_in_flight: Set[str] = set()

    @property
    def _is_active_source(self) -> bool:
        """True when CD is the source the user is on: the drive is followed
        always, everything else it does waits for this."""
        return bool(
            self.state_machine
            and self.state_machine.system_state.active_source == AudioSource.CD
        )

    # =========================================================================
    # LIFECYCLE
    # =========================================================================

    @handle_errors(default=False)
    async def initialize(self) -> bool:
        """Load the disc cache and start following the drive."""
        await self._data_service.initialize()
        present = self._drive.start(on_event=self._post_drive_event)
        if present is not None:
            self._post_drive_event(present)
        self._logger.info("CD source initialized, following the drive")
        return await super().initialize()

    async def shutdown(self) -> None:
        self._drive.stop()
        await self._device_tasks.cancel_all()
        await super().shutdown()

    async def _do_start(self) -> bool:
        """Start mpv and connect IPC. A disc already read is preloaded paused
        once the transition is over (a posted message, so the card leaves
        "starting" while the drive spins)."""
        try:
            if not await self._start_service_and_wait():
                return False
            if not await self._attach_mpv():
                return False
            await self._listen_to_mpv()
            await self._load_auto_stop_config()

            if self._disc_state is DiscState.READY and self._disc:
                # A jacket the archive could not serve last time: asked again
                # before this first publish, so the player opens veiled.
                self._spawn_cover_fetch(self._disc)
                if self._metadata_retry_pending:
                    self._arm_timer("identify", METADATA_RETRY_INTERVAL_S, self._generation)
                self._post_result(self._restore_on_start)
            elif self._disc_state is DiscState.IDENTIFYING:
                self._identify()
            self._probe_for_insertion()
            self._publish()
            return True

        except Exception as e:
            self._logger.error(f"Start failed: {e}")
            await self._cleanup()
            return False

    @handle_errors(default=False)
    async def _do_stop(self) -> bool:
        """Keep the track and second to resume (SOURCE_SWITCH), release the
        drive and mpv."""
        await self._end_with_position(EndReason.SOURCE_SWITCH)
        await self._cleanup()
        return await self._stop_service()

    @handle_errors(default=False)
    async def _do_release(self) -> bool:
        await self._end_with_position(EndReason.REROUTE)
        await self._cleanup()
        return await self._stop_service()

    async def _restore_on_start(self) -> None:
        """What opening the source brings back: after a multiroom toggle, the
        disc playing where it was (E38); otherwise the resume point, or track
        1, loaded and paused — never played."""
        if self._session is not None or self._disc_state is not DiscState.READY:
            return
        if not self._is_active_source or self._mpv is None:
            return
        track, position = self._resume_track()
        point = self._resume_point
        resume_playing = (
            point is not None and point.reason is EndReason.REROUTE
            and point.phase is not Phase.PAUSED
        )
        await self._play(track, position, paused=not resume_playing)

    async def _end_with_position(self, reason: EndReason) -> None:
        """End the live session, if any, at the playhead mpv reports."""
        session = self._session
        if isinstance(session, CdSession):
            await self._sync_position(session)
            if self._mpv is not None:
                await self._mpv.stop()
            await self.end_session(reason)
        await asyncio.to_thread(self._reader.stop)

    async def _cleanup(self) -> None:
        """Release the drive and close mpv. mpv lets go of the FIFO first: a
        paused mpv holds its read end without draining it, so the reader sits
        blocked in write() and its 3 s join expires (measured: a 3.1 s source
        switch)."""
        if self._mpv is not None:
            await self._mpv.stop()
        await asyncio.to_thread(self._reader.stop)
        await self._detach_mpv()

    # =========================================================================
    # THE DRIVE (the device axis — followed whether CD is active or not)
    # =========================================================================

    def _post_drive_event(self, event: DriveEvent) -> None:
        """udev's callback, on the loop: the drive's news, posted."""
        self._post_result(lambda: self._on_drive_event(event), token=self._device_token)

    async def _on_drive_event(self, event: DriveEvent) -> None:
        if event.action == "remove":
            self._logger.info("CD drive disconnected")
            await self._disc_left(EndReason.STORAGE_GONE)
            self._set_disc_state(DiscState.NO_DRIVE)
            await self._publish_device()
            return

        if self._disc_state is DiscState.NO_DRIVE:
            self._logger.info("CD drive connected")

        if event.media:
            if self._disc_state in (DiscState.IDENTIFYING, DiscState.READY):
                return       # measured: a replug announces the same media twice
            if self._disc_state is DiscState.EMPTY and event.action == "change":
                # A disc arriving in a drive known empty was inserted — with
                # no probe running, udev's first word about it is this one.
                self._play_on_ready = self._is_active_source
            if event.audio_tracks == 0:
                await self._unreadable("the disc has no audio track")
                return
            self._toc_attempts = 0
            await self._read_toc()
            return

        if event.action == "add":
            # A drive that just appeared says what it holds once it has read
            # it (measured: 0.07 s empty, 8.7 s with a disc): until then it
            # is reading, and what it holds was not inserted now.
            self._enter_reading(inserted=False)
            await self._publish_device()
            return

        # No media: the disc left, or one is spinning up. Only the drive can
        # tell those apart.
        if self._disc_state in _HOLDS_A_DISC and self._disc_state is not DiscState.READING:
            ejected = self._disc_state is DiscState.EJECTING
            self._logger.info("Disc ejected" if ejected else "Disc removed")
            await self._disc_left(EndReason.USER_STOP if ejected else EndReason.STORAGE_GONE)
        status = await self._drive_status()
        if status == CDS_DRIVE_NOT_READY:
            if self._disc_state is not DiscState.READING:
                # Already reading (a replug, the probe) keeps what it knew.
                self._logger.info("Disc detected (spinning up)")
                self._enter_reading(inserted=event.action == "change")
        else:
            self._set_disc_state(DiscState.EMPTY)
        await self._publish_device()

    def _probe_for_insertion(self) -> None:
        """Arm the insertion probe, if CD is on screen over an empty drive."""
        if self._disc_state is DiscState.EMPTY and self._is_active_source:
            self._arm_timer("probe", INSERTION_PROBE_S, DeviceToken())

    async def _insertion_probe(self) -> None:
        if self._disc_state is not DiscState.EMPTY or not self._is_active_source:
            return
        if await self._drive_status() == CDS_DRIVE_NOT_READY:
            self._logger.info("Disc detected (spinning up)")
            self._enter_reading(inserted=True)
            await self._publish_device()
            return
        self._probe_for_insertion()

    async def _drive_status(self) -> int:
        try:
            return await asyncio.wait_for(asyncio.to_thread(self._drive.status), STATUS_TIMEOUT_S)
        except asyncio.TimeoutError:
            return CDS_DRIVE_NOT_READY

    def _enter_reading(self, inserted: bool) -> None:
        self._play_on_ready = inserted and self._is_active_source
        self._set_disc_state(DiscState.READING)
        self._arm_timer("reading", READING_TIMEOUT_S, DeviceToken())

    async def _read_toc(self) -> None:
        """The disc is readable: read its TOC off the mailbox — libdiscid can
        struggle for long on a bad disc, and a source switch must not queue
        behind it — then name it (active only)."""
        generation = self._generation = DeviceToken()
        if self._disc_state is not DiscState.READING:
            self._set_disc_state(DiscState.READING)
            await self._publish_device()
        if not self._timer_armed("reading"):
            self._arm_timer("reading", READING_TIMEOUT_S, DeviceToken())
        self._logger.info("Disc ready, reading TOC")

        async def read() -> None:
            result = await self._data_service.read_disc()
            self._post_result(lambda: self._toc_read(generation, result), token=generation)

        self._device_tasks.spawn(read(), label="cd_toc")

    async def _toc_read(self, generation: DeviceToken, result) -> None:
        if generation is not self._generation or self._disc_state is not DiscState.READING:
            return
        self._disarm_timer("reading")
        if result is None:
            self._toc_attempts += 1
            if self._toc_attempts < TOC_READ_ATTEMPTS:
                self._logger.warning("Failed to read disc TOC, trying again")
                self._arm_timer("toc", TOC_RETRY_S, generation)
            else:
                await self._unreadable(f"its TOC did not read in {TOC_READ_ATTEMPTS} attempts")
            return

        disc_id, toc_string, tracks, length = result
        self._toc = Toc(
            disc_id=disc_id, toc_string=toc_string, tracks=tracks,
            lbas=[t["offset"] - LEAD_IN_SECTORS for t in tracks],
            end_lba=length - LEAD_IN_SECTORS,
        )
        self._logger.info(f"New disc: {disc_id}, {len(tracks)} tracks")
        point = self._resume_point
        if point is not None and point.identity != disc_id:
            self._set_resume_point(None)
        self._set_disc_state(DiscState.IDENTIFYING)
        await self._publish_device()
        if self._is_active_source:
            self._identify()

    def _identify(self) -> None:
        """Name the disc (cache, then MusicBrainz) in the background: an
        archive in an outage retries for ~100 s, which must not hold the
        source's mailbox."""
        generation, toc = self._generation, self._toc
        if toc is None or self._lookup_for is generation:
            return
        self._lookup_for = generation

        async def lookup() -> None:
            try:
                info = await self._data_service.lookup_metadata(
                    toc.disc_id, toc.toc_string, toc.tracks
                )
            except Exception as e:
                self._logger.error(f"Disc lookup failed: {e}")
                info = _unnamed(toc)
            self._post_result(lambda: self._adopt_disc(generation, info), token=generation)

        self._device_tasks.spawn(lookup(), label="cd_lookup")

    async def _adopt_disc(self, generation: DeviceToken, info: DiscInfo) -> None:
        if generation is not self._generation:
            return
        self._lookup_for = None
        first = self._disc_state is DiscState.IDENTIFYING
        if not first and info.album is None:
            # Still unknown (the network is not back): ask again later.
            if self._is_active_source:
                self._arm_timer("identify", METADATA_RETRY_INTERVAL_S, generation)
            return
        self._disc = info
        self._metadata_retry_pending = info.album is None
        if self._disc_state is DiscState.IDENTIFYING:
            self._set_disc_state(DiscState.READY)
        if self._is_active_source:
            self._spawn_cover_fetch(info)
            if self._metadata_retry_pending:
                self._arm_timer("identify", METADATA_RETRY_INTERVAL_S, generation)
        await self._publish_device()
        if first:
            await self._disc_ready()
        elif not self._metadata_retry_pending:
            self._logger.info(f"MusicBrainz retry named {info.disc_id}: {info.artist} — {info.album}")

    async def _disc_ready(self) -> None:
        """A disc just became playable: inserted while CD was active, it
        plays; otherwise it is preloaded paused."""
        if not self._is_active_source or self._mpv is None or self._session is not None:
            self._play_on_ready = False
            return
        if self._play_on_ready:
            self._play_on_ready = False
            await self._play(1, 0, paused=False)
            return
        await self._restore_on_start()

    async def _unreadable(self, why: str) -> None:
        self._logger.warning(f"Disc unreadable: {why}")
        self._disarm_timer("reading")
        self._disarm_timer("toc")
        self._toc, self._disc, self._generation = None, None, None
        self._play_on_ready = False
        self._set_disc_state(DiscState.UNREADABLE)
        await self._publish_device()
        if self._is_active_source:
            self.broadcast_error(SourceErrorReason.DISC_UNREADABLE)

    async def _disc_left(self, reason: EndReason) -> None:
        """The disc is out of the drive: its session ends, its resume point is
        forgotten even with no session (the same rule as a storage leaving,
        E51), and nothing read from it survives."""
        if isinstance(self._session, CdSession):
            await self._end_playback(reason, stop_mpv=self._mpv is not None)
        point = self._resume_point
        if point is not None and self._toc is not None and point.identity == self._toc.disc_id:
            self._set_resume_point(None)
        for name in ("reading", "toc", "identify", "eject", "probe"):
            self._disarm_timer(name)
        self._toc, self._disc, self._generation = None, None, None
        self._lookup_for = None
        self._metadata_retry_pending = False
        self._play_on_ready = False

    def _set_disc_state(self, state: DiscState) -> None:
        self._disc_state = state
        if state is DiscState.EMPTY:
            self._probe_for_insertion()
        else:
            self._disarm_timer("probe")

    async def _publish_device(self) -> None:
        """Publish a device change: the drive's state is the CD's
        `availability`, read whether CD is on screen or not; the disc it holds
        is the source's view when it is (E58: one state, never an event
        announcing the one before)."""
        if self._is_active_source:
            self._publish()
        else:
            self._availability_changed()

    async def _on_timer(self, name: str, token: object) -> None:
        if name == "reading":
            if self._disc_state is DiscState.READING:
                await self._unreadable(f"still not readable after {READING_TIMEOUT_S:.0f} s")
        elif name == "toc":
            if token is self._generation and self._disc_state is DiscState.READING:
                await self._read_toc()
        elif name == "identify":
            if token is self._generation and self._metadata_retry_pending and self._is_active_source:
                self._identify()
        elif name == "eject":
            await self._confirm_eject()
        elif name == "probe":
            await self._insertion_probe()
        else:
            await super()._on_timer(name, token)

    async def _confirm_eject(self) -> None:
        """No no-media change came after an eject that succeeded: ask the
        drive what it holds, rather than showing "ejecting" for good (E35)."""
        if self._disc_state is not DiscState.EJECTING:
            return
        status = await self._drive_status()
        self._logger.warning(f"No udev news after the eject; the drive says {status}")
        if status == CDS_DISC_OK and self._toc is not None:
            # The disc never left: udev is silent while a disc sits in the
            # drive, so showing it gone would hide it for good.
            self._set_disc_state(DiscState.READY if self._disc else DiscState.IDENTIFYING)
            await self._publish_device()
            return
        await self._disc_left(EndReason.USER_STOP)
        if status == CDS_DRIVE_NOT_READY:
            self._enter_reading(inserted=False)
        else:
            self._set_disc_state(DiscState.EMPTY)
        await self._publish_device()

    # =========================================================================
    # COVER ART
    # =========================================================================

    def _spawn_cover_fetch(self, disc: DiscInfo) -> None:
        """Fetch `disc`'s jacket in the background, if it lacks one it can get.

        One fetch per disc: a disc read again while its jacket is in flight
        is served by the fetch already running.
        """
        disc_id = disc.disc_id
        if (disc.cover_url is not None or disc_id in self._covers_in_flight
                or not self._data_service.cover_fetchable(disc_id)):
            return
        # Set before the spawn: the caller publishes the disc right after, and
        # that publish must already say a cover is on its way.
        self._covers_in_flight.add(disc_id)
        self._bg.spawn(self._fetch_cover(disc_id, self._generation), label="cd_cover")

    async def _fetch_cover(self, disc_id: str, generation: Optional[DeviceToken]) -> None:
        try:
            url = await self._data_service.fetch_cover(disc_id)
        except asyncio.CancelledError:
            # The source stopping: its own READY carries the cleared flag.
            self._covers_in_flight.discard(disc_id)
            raise
        except Exception as e:
            # Not re-raised: the veil must still lift.
            self._logger.error(f"Cover fetch for disc {disc_id} failed: {e}")
            url = None
        self._post_result(
            lambda: self._cover_arrived(generation, disc_id, url), token=self._device_token
        )

    async def _cover_arrived(self, generation, disc_id: str, url: Optional[str]) -> None:
        """A miss still publishes, to lift the player's veil onto its
        placeholder; a disc swapped meanwhile is not given this jacket."""
        self._covers_in_flight.discard(disc_id)
        disc = self._disc
        if generation is not self._generation or disc is None or disc.disc_id != disc_id:
            return
        if url:
            self._disc = disc.model_copy(update={"cover_url": url})
        if self._is_active_source:
            self._publish()

    # =========================================================================
    # PLAYBACK
    # =========================================================================

    async def _play(self, track: int, position: float, *, paused: bool) -> Dict[str, Any]:
        """Load `track` at `position`: a new session, or the live one moved
        there (a track change, a seek). `paused` loads it parked."""
        session = self._session
        if not isinstance(session, CdSession):
            session = CdSession(phase=Phase.LOADING, disc_id=self._toc.disc_id)
            self.open_session(session)
        elif not paused and session.phase in (Phase.PLAYING, Phase.PAUSED):
            # The sound this session made stops here: it is loading again
            # before the screen is told which track.
            before = session.phase
            session.advance(PhaseEvent.TRACK_CHANGE if before is Phase.PLAYING else PhaseEvent.STALLED)
            self._on_phase_changed(session, before)
        session.track, session.track_position = track, float(position)
        self._anchor_position(int(position * 1000))
        self._publish()

        start_lba = self._lba_for(track, position)

        async def load() -> Optional[int]:
            if not await self._mpv_ready():
                return None
            # mpv lets go of the FIFO before the reader is joined (_cleanup).
            await self._mpv.stop()
            await asyncio.to_thread(self._reader.stop)
            self._reader.start(start_lba, self._toc.end_lba)
            if not await asyncio.to_thread(self._reader.wait_ready, 5.0):
                self._logger.error("Reader not ready within timeout")
                await asyncio.to_thread(self._reader.stop)
                return None
            if not await self._set_mpv_pause(paused):
                await asyncio.to_thread(self._reader.stop)
                return None
            entry = await self._mpv.loadfile(CD_FIFO_PATH, mode="replace")
            if entry is None:
                await asyncio.to_thread(self._reader.stop)
            return entry

        entry = await self._attempt(load)
        if entry is None:
            await self._end_playback(EndReason.LOAD_FAILED, detail="mpv or the reader refused the load")
            return self.error_response("Failed to start playback")
        session.entry, session.link, session.start_lba = entry, self._mpv.link, start_lba
        session.started = session.opened = False
        return self.success_response(f"Track {track}")

    def _resume_track(self) -> tuple:
        """(track, seconds) a press on play restarts from: the resume point
        for this disc, else track 1 at 0:00."""
        point = self._resume_point
        if point is not None and self._toc is not None and point.identity == self._toc.disc_id:
            track = point.content["track"]
            if 1 <= track <= len(self._toc.lbas):
                return track, point.position_ms / 1000
        return 1, 0.0

    def _current_track(self) -> tuple:
        session = self._session
        if isinstance(session, CdSession):
            return session.track, session.track_position
        return self._resume_track()

    def _resume_content(self, session: CdSession):
        return (
            session.disc_id,
            int(session.track_position * 1000),
            {"track": session.track},
        )

    def _playable(self) -> bool:
        return self._disc_state is DiscState.READY and self._toc is not None

    # =========================================================================
    # COMMANDS
    # =========================================================================

    async def _handle_command(self, cmd: str, params: Optional[BaseModel]) -> Dict[str, Any]:
        if cmd == "play_track":
            return await self._handle_play_track(params)
        if cmd == "pause":
            return await self._handle_pause()
        if cmd == "resume":
            return await self._handle_resume()
        if cmd == "next":
            return await self._handle_next_track()
        if cmd == "prev":
            return await self._handle_prev_track()
        if cmd == "seek":
            return await self._handle_seek(params)
        if cmd == "skip":
            return await self._handle_skip(params)
        if cmd == "eject":
            return await self._handle_eject()
        return self.error_response(f"Unhandled command: {cmd}")

    async def _handle_play_track(self, params: PlayTrackParams) -> Dict[str, Any]:
        if not self._playable():
            return self.error_response("Disc not ready")
        if params.track_number > len(self._toc.lbas):
            return self.error_response(f"Invalid track number: {params.track_number}")
        return await self._play(params.track_number, 0, paused=False)

    async def _handle_pause(self) -> Dict[str, Any]:
        session = self._session
        await self._sync_position(session)
        if not await self._mpv.pause():
            return self.mpv_refused("pause")
        return self.success_response("Paused")

    async def _handle_resume(self) -> Dict[str, Any]:
        """Unpause the live session, or reload the resume point (the rotary and
        the IR remote send `resume` and know no other name)."""
        session = self._session
        if session is None:
            if not self._playable():
                return self.error_response("Disc not ready")
            track, position = self._resume_track()
            return await self._play(track, position, paused=False)
        if session.phase is Phase.PAUSED:
            if not await self._mpv.resume():
                return self.mpv_refused("resume")
            return self.success_response("Resumed")
        return self.success_response("Already playing")

    async def _handle_next_track(self) -> Dict[str, Any]:
        if not self._playable():
            return self.error_response("No disc loaded")
        track, _ = self._current_track()
        if track >= len(self._toc.lbas):
            return self.success_response("Already on last track")
        return await self._play(track + 1, 0, paused=False)

    async def _handle_prev_track(self) -> Dict[str, Any]:
        """Restart-then-previous, like Spotify: past the threshold prev
        restarts the track; within its first seconds it steps back."""
        if not self._playable():
            return self.error_response("No disc loaded")
        if isinstance(self._session, CdSession):
            await self._sync_position(self._session)
        track, position = self._current_track()
        target = track if position >= CD_PREV_RESTART_THRESHOLD_S else max(1, track - 1)
        return await self._play(target, 0, paused=False)

    async def _handle_seek(self, params: SeekParams) -> Dict[str, Any]:
        """Move the playhead; never decide to play. A live session reloads at
        the target in its own phase; with nothing loaded, the resume point
        moves and nothing touches the drive (E39)."""
        if not self._playable():
            return self.error_response("No disc loaded")
        position = max(0, int(params.position_ms / 1000))
        session = self._session
        if isinstance(session, CdSession):
            result = await self._play(session.track, position, paused=session.phase is Phase.PAUSED)
            return self.success_response(f"Seeked to {position}s") if result.get("success") else result
        track, _ = self._resume_track()
        point = self._resume_point
        moved = {"position_ms": position * 1000, "content": {"track": track}, "phase": Phase.PAUSED}
        self._set_resume_point(
            replace(point, **moved) if point is not None and point.identity == self._toc.disc_id
            else ResumePoint(
                identity=self._toc.disc_id, captured_at=time.monotonic(),
                reason=EndReason.IDLE_TIMEOUT, **moved,
            )
        )
        self._publish()
        return self.success_response(f"Seeked to {position}s")

    async def _handle_skip(self, params: SkipParams) -> Dict[str, Any]:
        """A seek by `params.seconds` from where the track stands now: mpv's
        playhead when the track is open, else the second the last load or seek
        set (a skip landing while the one before it reloads adds to it), else
        the resume point. The drive is read from a pipe, which mpv cannot seek,
        so every seek is a reload."""
        if not self._playable():
            return self.error_response("No disc loaded")
        session = self._session
        if isinstance(session, CdSession):
            await self._sync_position(session)
            track, position = session.track, session.track_position
        else:
            track, position = self._resume_track()
        target = skip_target(
            int(position * 1000), params.seconds, self._track_fields(track)["duration_ms"],
        )
        return await self._handle_seek(SeekParams(position_ms=target))

    async def _handle_eject(self) -> Dict[str, Any]:
        """Eject the disc. The session ends first (the drive is released); the
        disc stays read until the drive says it is out, so an eject the drive
        refuses leaves it exactly as it was (E34)."""
        if self._disc_state in (DiscState.NO_DRIVE, DiscState.EJECTING):
            return self.error_response("Nothing to eject")
        # What play would bring back if the drive refuses: the session ends
        # first (the drive is released for the eject), and USER_STOP forgets.
        kept = self._resume_point
        session = self._session
        if isinstance(session, CdSession):
            await self._sync_position(session)
            kept = self._capture_resume(session, EndReason.USER_STOP)
            await self._end_with_position(EndReason.USER_STOP)
        before = self._disc_state
        self._set_disc_state(DiscState.EJECTING)
        await self._publish_device()
        try:
            proc = await asyncio.create_subprocess_exec(
                "eject", CD_DEVICE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            returncode = await proc.wait()
            detail = "" if returncode == 0 else (await proc.stderr.read()).decode().strip()
        except asyncio.CancelledError:
            # Cut by a stop while `eject` runs: whatever the drive does, the
            # net settles it (a refusal would otherwise stay "ejecting").
            if self._disc_state is DiscState.EJECTING:
                self._arm_timer("eject", EJECT_CONFIRM_S, DeviceToken())
            raise
        except Exception as e:
            returncode, detail = -1, str(e)
        if returncode != 0:
            self._logger.error(f"Eject failed (rc={returncode}): {detail}")
            if self._disc_state is DiscState.EJECTING:
                self._set_disc_state(before)
                self._set_resume_point(kept)
                await self._publish_device()
            return self.error_response(f"Eject failed: {detail}")
        if self._disc_state is DiscState.EJECTING:
            self._arm_timer("eject", EJECT_CONFIRM_S, DeviceToken())
        return self.success_response("Disc ejected")

    # =========================================================================
    # WHAT MPV ANNOUNCES
    # =========================================================================

    async def _entry_ended(self, session: CdSession, event: Dict[str, Any]) -> None:
        """The FIFO ended. mpv says `eof` whether the reader reached the
        leadout or failed on a sector, so the reader says which."""
        if event.get("playlist_entry_id") != session.entry:
            return
        reason = event.get("reason")
        if reason == "stop":
            return
        if reason == "eof" and self._reader.reached_leadout:
            self._logger.info("Album finished")
            await self._end_playback(EndReason.EOF, stop_mpv=False)
            return
        failure = self._reader.failure
        if failure in GONE_ERRNOS:
            await self._end_playback(EndReason.STORAGE_GONE, stop_mpv=False, detail=os.strerror(failure))
            return
        detail = os.strerror(failure) if failure else (event.get("file_error") or reason)
        await self._end_playback(
            EndReason.STREAM_LOST if session.heard else EndReason.LOAD_FAILED, detail=detail,
        )

    async def _end_playback(self, reason: EndReason, **kwargs) -> None:
        await super()._end_playback(reason, **kwargs)
        await asyncio.to_thread(self._reader.stop)

    async def _mpv_lost(self, session: CdSession) -> None:
        await asyncio.to_thread(self._reader.stop)
        await super()._mpv_lost(session)

    async def _before_idle_end(self, session: CdSession) -> None:
        await self._sync_position(session)

    async def _on_playing_tick(self, session: CdSession) -> None:
        """One second of sound: map mpv's playhead onto the disc. A track
        boundary crossed moves the title (a state); the playhead within the
        track goes to the position axis, which publishes only a jump."""
        await self._sync_position(session)

    async def _sync_position(self, session: Optional[CdSession]) -> bool:
        """Read mpv's playhead into the session's track and position."""
        if not isinstance(session, CdSession) or not session.opened or self._mpv is None:
            return False
        time_pos = await self._mpv.get_property("time-pos")
        if time_pos is None or self._session is not session:
            return False
        session.position = int(time_pos)
        lba = session.start_lba + int(float(time_pos) * SECTORS_PER_SECOND)
        track = self._track_at(lba)
        if track is not None and track != session.track:
            session.track = track
        session.track_position = max(0.0, (lba - self._toc.lbas[session.track - 1]) / SECTORS_PER_SECOND)
        self._observe_position(int(session.track_position * 1000))
        return True

    # =========================================================================
    # DISC GEOMETRY (kernel LBAs)
    # =========================================================================

    def _lba_for(self, track: int, position: float) -> int:
        """The LBA `position` seconds into `track`, kept inside the track."""
        lbas = self._toc.lbas
        start = lbas[track - 1]
        end = lbas[track] if track < len(lbas) else self._toc.end_lba
        return min(start + int(position) * SECTORS_PER_SECOND, end - 1)

    def _track_at(self, lba: int) -> Optional[int]:
        """The track (1-based) an LBA falls in."""
        lbas = self._toc.lbas if self._toc else []
        for i in range(len(lbas) - 1, -1, -1):
            if lba >= lbas[i]:
                return i + 1
        return None

    # =========================================================================
    # PUBLICATION (docs: "le fil")
    # =========================================================================

    _AVAILABILITY = {
        DiscState.NO_DRIVE: "no_drive",
        DiscState.EMPTY: "no_disc",
        DiscState.READING: "reading_disc",
        DiscState.IDENTIFYING: "reading_disc",
        DiscState.UNREADABLE: "unreadable_disc",
        DiscState.EJECTING: "ejecting",
        DiscState.READY: None,
    }

    def availability(self) -> Optional[str]:
        return self._AVAILABILITY[self._disc_state]

    def _track_of(self, track: int) -> Optional[TrackInfo]:
        tracks = self._disc.tracks if self._disc else []
        if 0 < track <= len(tracks):
            return tracks[track - 1]
        return tracks[0] if tracks else None

    def _track_fields(self, track: int) -> Dict[str, Any]:
        disc = self._disc
        info = self._track_of(track)
        return {
            "title": info.title if info else disc.album,
            "artist": disc.artist,
            "album": disc.album,
            "artwork": disc.cover_url,
            "duration_ms": info.duration * 1000 if info else None,
        }

    def _session_fields(self, session: CdSession) -> Dict[str, Any]:
        if self._disc is None:
            return {}
        return self._track_fields(session.track)

    def _resume_view(self) -> Optional[ResumeView]:
        """What play brings back: the resume point for this disc, or track 1
        at 0:00 — a READY disc always has one (D8)."""
        if self._disc_state is not DiscState.READY or self._disc is None:
            return None
        track, position = self._resume_track()
        fields = self._track_fields(track)
        return ResumeView(**fields, position_ms=int(position * 1000))

    def _details(self) -> Optional[CdDetails]:
        """The disc, from the moment it is read (or found unreadable)."""
        state = self._disc_state
        disc = self._disc
        if state is DiscState.UNREADABLE:
            return CdDetails(disc=None, current_track=None, artwork_pending=False)
        if state is not DiscState.READY or disc is None:
            return None
        return CdDetails(
            disc=CdDisc(
                id=disc.disc_id, album=disc.album, artist=disc.artist, year=disc.year,
                cover_url=disc.cover_url,
                tracks=[
                    CdTrack(number=t.number, title=t.title, duration_ms=t.duration * 1000)
                    for t in disc.tracks
                ],
            ),
            current_track=self._current_track()[0],
            artwork_pending=disc.disc_id in self._covers_in_flight,
        )

    def _controls(self) -> List[str]:
        state = self._disc_state
        if not self._playable():
            # A disc the drive holds but Milō cannot play still comes out (E67).
            return ["eject"] if state in (
                DiscState.READING, DiscState.IDENTIFYING, DiscState.UNREADABLE,
            ) else []
        track, _ = self._current_track()
        steps = ["next", "prev"] if track < len(self._toc.lbas) else ["prev"]
        session = self._session
        if session is not None and session.phase is Phase.LOADING:
            return ["pause", *steps, "play_track", "eject"]
        if session is not None and session.phase is Phase.PLAYING:
            return ["pause", "seek", "skip", *steps, "play_track", "eject"]
        return ["resume", "seek", "skip", *steps, "play_track", "eject"]

    async def refresh_metadata(self) -> bool:
        """Re-read the playhead so a (re)connecting client's state carries it."""
        if isinstance(self._session, CdSession):
            await self._sync_position(self._session)
        return True

    @property
    def data_service(self) -> CdDataService:
        return self._data_service


def _unnamed(toc: Toc) -> DiscInfo:
    """The disc with generic titles, from its TOC alone."""
    tracks = [
        TrackInfo(number=t["number"], title=f"Track {t['number']}", duration=t["duration"])
        for t in toc.tracks
    ]
    return DiscInfo(
        disc_id=toc.disc_id, track_count=len(tracks),
        total_duration=sum(t.duration for t in tracks), tracks=tracks,
    )
