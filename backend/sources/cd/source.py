# backend/sources/cd/source.py
"""
CD audio source using direct ioctl sector reading + FIFO.

Architecture:
- Disc watcher (2s poll) detects disc -> broadcasts presence, then reads TOC
- TOC read via libdiscid provides sector offsets for each track (instant, no mpv)
- Playback: ioctl reader thread reads sectors from /dev/sr0, writes PCM to FIFO
- mpv reads FIFO with --demuxer=rawaudio (44100Hz, s16le, stereo)
- Track navigation: stop reader, restart at target LBA, re-loadfile on mpv (~0.5s gap)
- Auto-advance: reader plays through track boundaries, monitor detects via LBA position
- Seek: same restart mechanism as track change

Concurrency:
- self._mpv_lock protects mpv creation/connection (shared by _do_start and _pre_start_service)
- Reader thread is stopped via stop_event + FIFO unblock before restart
- Position tracking uses mpv time-pos mapped to disc LBA for accuracy

Key rules:
- NEVER start reader without corresponding mpv loadfile (FIFO deadlock)
- NEVER set is_playing=True except in command handlers
- NEVER start mpv, run a MusicBrainz lookup, or begin playback while CD is
  not the active source — at boot the watcher only reads the local TOC.
"""
import asyncio
from time import monotonic
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from backend.config.constants import CD_DEVICE, CD_PREV_RESTART_THRESHOLD_S
from backend.core.models.audio_state import AudioSource, SourceState
from backend.core.models.ws_events import SystemCdDriveStatus
from backend.sources.cd.data import CDS_DISC_OK, CDS_DRIVE_NOT_READY, CdDataService
from backend.sources.cd.models import DiscInfo, PlayTrackParams, SeekParams, TrackInfo
from backend.sources.cd.reader import CD_FIFO_PATH, SECTORS_PER_SECOND, CdIoctlReader
from backend.shared.decorators import handle_errors
from backend.shared.mpv import MpvController
from backend.shared.mpv_audio_source import MpvAudioSource

# Retry MusicBrainz when the initial lookup fell through to the fallback DiscInfo
# (typically because DNS wasn't ready at boot when the disc was first detected).
METADATA_RETRY_INTERVAL_S = 60.0

# Disc insertion/removal detection latency — one blocking probe per tick.
DISC_POLL_INTERVAL_S = 2.0

# How many watcher ticks may fail to read the TOC of a disc the drive already
# reports as CDS_DISC_OK before we stop asking. A read can fail while the drive
# is still settling, so one failure must not brick the disc; a genuinely
# unreadable one must not re-spin the drive every tick for as long as it sits in
# the tray either.
TOC_READ_ATTEMPTS = 3


class CdSource(MpvAudioSource):
    """CD source (Family C — active player): UI-driven playback, rich metadata."""

    # Broadcast the live position on every monitor tick (~1s) rather than the
    # base class's 30-tick default. Unlike streaming sources, CD navigation
    # (prev/seek) restarts a track at position 0; a fresh authoritative position
    # in the store is what lets the frontend detect the jump-to-0 and reset its
    # interpolated progress bar (a 0->0 no-op would otherwise be missed).
    POSITION_SYNC_INTERVAL = 1

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

        # Data service (persists across start/stop)
        self._data_service = CdDataService()

        # Ioctl reader (persists across start/stop, thread is started/stopped per playback)
        self._reader = CdIoctlReader(device=CD_DEVICE)

        # Lock protecting self._mpv creation/connection (shared by _do_start and _pre_start_service)
        self._mpv_lock = asyncio.Lock()

        # Serializes whole playback GESTURES, not just the reader+mpv restart:
        # every command handler that mutates a playback flag takes it, and it
        # spans announce -> restart -> _settle_after_restart. Held only by the
        # restart it used to be, it protected nothing a user could reach — a
        # pause arriving mid-restart was applied to the mpv about to be
        # replaced, then silently un-paused by the restart itself.
        # Deliberately NOT taken by the teardown paths (_do_stop, _cleanup,
        # _clear_disc_state): those react to a source switch or to a disc that
        # is already gone, where prompt beats ordered.
        self._playback_lock = asyncio.Lock()

        # Disc watcher (permanent, from initialize)
        self._disc_watcher_task: Optional[asyncio.Task] = None
        self._toc_read_attempts = 0

        # Disc state (persists across start/stop for UI)
        self._current_disc: Optional[DiscInfo] = None
        self._tracks: List[TrackInfo] = []
        self._drive_connected = False
        self._disc_present = False  # True as soon as disc detected (spinning up)
        self._disc_ready = False  # True when drive reports CDS_DISC_OK (TOC readable)
        self._last_disc_id: Optional[str] = None

        # Sector offsets from libdiscid (persists across start/stop)
        self._sector_offsets: List[int] = []  # LBA start of each track
        self._disc_end_lba: int = 0  # total sectors (leadout)

        # MusicBrainz retry: set when lookup_metadata fell through to fallback
        # (album/artist/year=None). Throttled retry from the watcher loop until
        # the network comes back and the lookup succeeds.
        self._metadata_retry_pending = False
        self._metadata_retry_last_attempt: float = 0.0

        # Playback state (reset on stop)
        self._current_track: Optional[int] = None  # 1-based
        self._track_position: float = 0
        self._track_duration: float = 0
        self._is_paused = False
        self._ejecting = False
        self._play_start_lba: int = 0  # LBA where current FIFO session started
        # Set while a reader+mpv restart is in flight (play_track/seek/resume).
        # The monitor tick shares no lock with these and would otherwise act on
        # stale _current_track/_play_start_lba mid-restart (spurious album-end /
        # LBA recompute races), so it skips its body while this is set.
        self._restarting = False

    def _reset_playback_state(self) -> None:
        """Reset CD-specific playback fields (called by _do_stop/_auto_stop_action)."""
        super()._reset_playback_state()
        self._current_track = None
        self._track_position = 0
        self._track_duration = 0
        self._is_paused = False
        self._ejecting = False
        self._play_start_lba = 0

    def _idle_metadata(self) -> Dict[str, Any]:
        """A stopped CD still has a disc to show, so publish the full projection
        (same reason _update_connection_state sends it in both states)."""
        return self._build_metadata()

    @property
    def _is_active_source(self) -> bool:
        """True when CD is the source the user is on.

        The watcher runs permanently, so this gates everything it does that is
        only correct for a visible source: broadcasting state, reaching the
        network for metadata, auto-playing an inserted disc.
        """
        return bool(
            self.state_machine
            and self.state_machine.system_state.active_source == AudioSource.CD
        )

    # =========================================================================
    # LIFECYCLE
    # =========================================================================

    @handle_errors(default=False)
    async def initialize(self) -> bool:
        """Initialize data service and start disc watcher."""
        await self._data_service.initialize()
        self._disc_watcher_task = asyncio.create_task(self._disc_watcher_loop())
        self._logger.info("CD source initialized, disc watcher started")
        return await super().initialize()

    async def _do_start(self) -> bool:
        """Start mpv service, connect IPC.
        If _pre_start_service already ran, reuses the existing connection.
        Metadata lookup runs in the background so the frontend can show
        the loading-album indicator during the load."""
        try:
            async with self._mpv_lock:
                if self._mpv and self._mpv.is_connected:
                    self._logger.info("Reusing pre-started mpv connection")
                else:
                    if not await self._start_service_and_wait():
                        return False
                    self._mpv = MpvController(ipc_socket_path=self._mpv_socket)
                    if not await self._mpv.connect():
                        self._logger.error("Failed to connect to mpv IPC")
                        return False

            await self._load_auto_stop_config()
            self._start_monitor()
            self._update_connection_state()

            # On activation with a disc inserted, load it (if needed) and
            # *preload* track 1 in the background (reader + mpv loaded, paused)
            # so the first play starts fast — without auto-playing. Runs AFTER
            # _do_start returns so the transition completes and the frontend
            # shows the loader while the drive spins up.
            if self._disc_present:
                self._bg.spawn(
                    self._preload_on_start(),
                    label="cd_preload_on_start",
                )

            return True

        except Exception as e:
            self._logger.error(f"Start failed: {e}")
            await self._cleanup()
            return False

    async def _load_disc_metadata(self) -> None:
        """Background metadata lookup when activating CD with a disc already present."""
        try:
            result = await self._data_service.read_disc()
            if not result:
                return

            disc_id, toc_string, toc_tracks, disc_end_lba = result
            self._sector_offsets = [t["offset"] for t in toc_tracks]
            self._disc_end_lba = disc_end_lba

            disc_info = await self._data_service.lookup_metadata(
                disc_id, toc_string, toc_tracks
            )
            # Guard: disc may have been ejected during the await — refuse to
            # repopulate state for a disc that's no longer in the drive.
            if not self._disc_present or self._last_disc_id != disc_id:
                return
            self._current_disc = disc_info
            self._tracks = disc_info.tracks
            self._metadata_retry_pending = disc_info.album is None
            self._update_connection_state()

        except Exception as e:
            self._logger.error(f"Background metadata lookup failed: {e}")

    async def _preload_on_start(self) -> None:
        """On activation with a disc inserted, load it (if needed) then preload
        track 1 so the first play is fast — without auto-playing.

        Holds is_buffering=True from activation through the metadata lookup and
        the preload so the play button reads as a loader the whole time (no
        flash to a play icon in between). Skips — and clears the loader — if the
        user already started something while the lookup was in flight.
        """
        try:
            self._is_buffering = True
            self._update_connection_state()

            if not self._current_disc and self._last_disc_id:
                await self._load_disc_metadata()

            still_active = self._is_active_source
            if (still_active and self._sector_offsets and self._tracks
                    and not self._is_playing and not self._is_paused):
                await self._preload_track_1()
            else:
                self._is_buffering = False
                self._update_connection_state()
        except Exception as e:
            self._is_buffering = False
            self._update_connection_state()
            self._logger.error(f"CD preload on start failed: {e}")

    async def _preload_track_1(self) -> None:
        """Preload track 1: reader + mpv loaded and *paused* (never un-paused, so
        no audio), primed for an instant resume. Shows the loader while the drive
        spins up and mpv loads, then parks in a paused/ready state (play tap ->
        resume). Auto-stop is armed so the drive releases if the user never plays.
        """
        if not self._mpv or not self._sector_offsets or not self._tracks:
            return

        async with self._playback_lock:
            self._current_track = 1
            self._track_position = 0
            self._track_duration = self._tracks[0].duration
            self._is_playing = False
            self._is_paused = False
            self._is_buffering = True  # loader while the drive spins up + mpv loads
            self._update_connection_state()

            if not await self._restart_reader_and_mpv(
                self._sector_offsets[0], autostart=False
            ):
                self._is_buffering = False
                self._update_connection_state()
                self._logger.warning("Preload: failed to load track 1")
                return

            # Parked paused & primed — a play tap resumes instantly.
            self._is_paused = True
            self._is_buffering = False
            self._handle_pause_change(True)
            self._update_connection_state()
        self._logger.info("Preloaded track 1 (paused)")

    @handle_errors(default=False)
    async def _do_stop(self) -> bool:
        """Stop playback and service."""
        await self._cleanup()
        self._reset_playback_state()
        return await self._stop_service()

    async def _cleanup(self) -> None:
        """Clean up reader + mpv resources. Does NOT stop disc watcher or clear disc info.

        Tears down through _stop_reader_and_mpv rather than stopping the reader
        on its own: a paused mpv still holds the FIFO read end open without
        draining it, so the reader thread sits blocked in write() and its
        3 s join expires — measured on a unit as a 3.1 s source switch plus a
        warning. reader.stop()'s own unblock trick cannot help, it exists for a
        writer still blocked on *opening* the FIFO. mpv must let go first.
        """
        self._stop_monitor()
        await self._stop_reader_and_mpv()
        if self._mpv:
            await self._mpv.disconnect()
            self._mpv = None
        self._is_playing = False
        self._is_paused = False
        self._is_buffering = False

    # =========================================================================
    # READER + MPV ORCHESTRATION
    # =========================================================================

    async def _start_reader_and_mpv(self, start_lba: int, autostart: bool = True) -> bool:
        """Start ioctl reader at given LBA and connect mpv to the FIFO.

        Sequence: reader opens CD + creates FIFO + blocks on write-open,
        then mpv opens FIFO for reading -> both sides connect.

        mpv is loaded paused so it doesn't emit audio while the loadfile/FIFO
        handshake and drive spin-up settle. With autostart (the default) it is
        then un-paused and playback is *confirmed* by wait_until_advancing —
        mpv's time-pos sits at 0 through a ~1s output-startup latency — keeping
        that latency out of the audible path so the intro isn't clipped. With
        autostart=False it is left paused and primed (preload): a later resume
        un-pauses it for a fast start.
        """
        # Before the drive spins and before the priming pause below: a
        # set_property dropped on a down link would let mpv load UNPAUSED and
        # emit audio through the loadfile/FIFO handshake, which is exactly what
        # that pause exists to prevent. Also avoids reader.start() plus a 5 s
        # wait_ready for an mpv that is not there to drain the FIFO.
        if not await self._mpv.ensure_connected():
            self._logger.error("mpv link down, cannot start playback")
            return False

        self._reader.start(start_lba, self._disc_end_lba)

        if not await asyncio.to_thread(self._reader.wait_ready, 5.0):
            self._logger.error("Reader not ready within timeout")
            await asyncio.to_thread(self._reader.stop)
            return False

        # Load paused so mpv doesn't emit audio during the loadfile/FIFO
        # handshake; also clears any leftover pause from a previous pause command.
        await self._mpv.set_property("pause", True)

        success = await self._mpv.load_stream(CD_FIFO_PATH)
        if not success:
            self._logger.error("mpv failed to open FIFO")
            await asyncio.to_thread(self._reader.stop)
            return False

        self._play_start_lba = start_lba

        if not autostart:
            # Preload only: leave mpv paused and primed for an instant resume.
            self._logger.info(f"Reader + mpv preloaded (paused) at LBA {start_lba}")
            return True

        await self._mpv.set_property("pause", False)

        # Block until playback actually advances. mpv's audio output has a ~1s
        # startup latency after un-pause during which time-pos stays 0; the
        # caller keeps is_buffering=True across this so the progress bar stays at
        # 0 instead of interpolating ahead of the real (not-yet-moving) playhead.
        if not await self._mpv.wait_until_advancing(timeout=3.0):
            self._logger.warning("mpv playback did not advance within timeout")

        self._logger.info(f"Reader + mpv started at LBA {start_lba}")
        return True

    async def _stop_reader_and_mpv(self) -> None:
        """Stop reader and mpv playback. mpv stop closes FIFO read end -> reader exits."""
        if self._mpv:
            await self._mpv.stop()  # Closes FIFO read end -> BrokenPipeError in reader
        await asyncio.to_thread(self._reader.stop)

    async def _auto_stop_action(self) -> None:
        """Light teardown after pause timeout — releases the drive but keeps
        the resume point.

        Stops the reader + mpv playback and clears the playing/paused flags so
        the source drops to READY (screen can sleep, disc stays visible), but
        keeps _current_track + _track_position so a tap on play resumes the same
        track at the same position. mpv stays connected for a cheap restart.
        """
        async with self._playback_lock:
            await self._stop_reader_and_mpv()
            self._is_playing = False
            self._is_paused = False
            self._is_buffering = False
            self._update_connection_state()

    async def _restart_reader_and_mpv(self, start_lba: int, autostart: bool = True) -> bool:
        """Restart reader at a new LBA position and reconnect mpv.

        **The caller must hold ``_playback_lock``.** The lock used to be taken
        here, which covered the mutation and not the gesture: no command handler
        took it, so a `pause` arriving mid-restart set pause=True and
        _is_playing=False while this function's own `set_property("pause",
        False)` un-paused the new mpv underneath. The disc then played behind a
        UI showing paused, and _on_monitor_tick's first line (`if not
        self._is_playing: return`) guaranteed nothing ever corrected it. Holding
        it in the caller is also what covers _settle_after_restart, which used to
        run after the lock was released.

        Guards the monitor tick out for the whole restart: _play_start_lba and
        _current_track are momentarily inconsistent here, and a tick reading the
        old mpv's time-pos against them would emit a bogus position/album-end.
        autostart=False loads paused (preload) instead of starting playback.
        """
        self._restarting = True
        try:
            await self._stop_reader_and_mpv()
            return await self._start_reader_and_mpv(start_lba, autostart=autostart)
        finally:
            self._restarting = False

    async def _settle_after_restart(self, restarted: bool) -> bool:
        """Post-restart UI settle shared by play/seek/resume.

        On success, sync the true playhead — it advances during mpv's ~1s
        startup latency, so the pre-announced target is already stale — and
        clear is_buffering so the bar resumes from the real position. On
        failure, clear the playing/buffering flags so the UI doesn't hang.
        Broadcasts the resulting state either way; returns `restarted`.
        """
        if restarted:
            await self._sync_position_from_mpv()
        else:
            self._is_playing = False
        self._is_buffering = False
        self._update_connection_state()
        return restarted

    # =========================================================================
    # DISC WATCHER (runs permanently, independent of source lifecycle)
    # =========================================================================

    async def _disc_watcher_loop(self) -> None:
        """Poll for disc drive and disc presence every DISC_POLL_INTERVAL_S."""
        while True:
            try:
                await asyncio.sleep(DISC_POLL_INTERVAL_S)
                await self._check_drive_and_disc()
                await self._retry_metadata_if_pending()
            except asyncio.CancelledError:
                return
            except Exception as e:
                self._logger.error(f"Disc watcher error: {e}")

    async def _retry_metadata_if_pending(self) -> None:
        """Re-run MusicBrainz when an earlier lookup fell through to fallback.

        Only fires while CD is the active source — we never touch the network
        for a source the user hasn't opened. Throttled to one attempt per
        METADATA_RETRY_INTERVAL_S so a permanently-unknown disc doesn't spam.
        """
        if not self._metadata_retry_pending or not self._last_disc_id:
            return
        if not self._is_active_source:
            return

        now = monotonic()
        if now - self._metadata_retry_last_attempt < METADATA_RETRY_INTERVAL_S:
            return
        self._metadata_retry_last_attempt = now

        result = await self._data_service.read_disc()
        if not result:
            return
        disc_id, toc_string, toc_tracks, _ = result
        if disc_id != self._last_disc_id:
            return  # Disc swapped — let the watcher's normal flow handle it.

        disc_info = await self._data_service.lookup_metadata(
            disc_id, toc_string, toc_tracks
        )
        if disc_info.album is None:
            return  # Still fallback — try again next cycle.

        # Guard: disc may have been ejected during the await.
        if not self._disc_present or self._last_disc_id != disc_id:
            return
        self._current_disc = disc_info
        self._tracks = disc_info.tracks
        self._metadata_retry_pending = False
        self._logger.info(
            "MusicBrainz retry succeeded for %s: %s — %s",
            disc_id, disc_info.artist, disc_info.album,
        )
        self._update_connection_state()

    async def _check_drive_and_disc(self) -> None:
        """Check drive connection and disc status (two-phase detection).

        Phase 1: CDS_DRIVE_NOT_READY — disc detected, spinning up.
                 Immediately broadcast presence so the frontend shows the loading-album indicator.
        Phase 2: CDS_DISC_OK — disc ready, TOC readable.
                 Read TOC, metadata lookup, auto-play.
        """
        was_connected = self._drive_connected
        self._drive_connected, status = await asyncio.to_thread(
            self._data_service.probe_drive_and_disc
        )

        if self._drive_connected != was_connected:
            self._logger.info(
                f"CD drive {'connected' if self._drive_connected else 'disconnected'}"
            )
            await self.state_machine.broadcast(SystemCdDriveStatus())
            if not self._drive_connected:
                await self._clear_disc_state()
                return
            # Drive newly connected — refresh source metadata so the frontend
            # transitions out of "no_drive" (mirrors the _clear_disc_state path).
            if self._is_active_source:
                self._update_connection_state()

        if not self._drive_connected:
            return

        disc_detected = status in (CDS_DRIVE_NOT_READY, CDS_DISC_OK)
        disc_ready = status == CDS_DISC_OK

        # Phase 1: disc newly detected (spinning up or already ready)
        if disc_detected and not self._disc_present:
            self._disc_present = True
            await self._handle_disc_detected()

        # Phase 2: disc became ready (TOC readable). Latch only once the TOC
        # was actually read: latching first left a disc whose read failed once
        # marked as handled forever — no sector offsets, so never playable,
        # until the user ejected it. Capped by TOC_READ_ATTEMPTS.
        if disc_ready and not self._disc_ready:
            if await self._handle_disc_ready():
                self._disc_ready = True
            else:
                self._toc_read_attempts += 1
                if self._toc_read_attempts >= TOC_READ_ATTEMPTS:
                    self._logger.error(
                        f"Giving up on disc TOC after {TOC_READ_ATTEMPTS} attempts"
                    )
                    self._disc_ready = True

        # Disc removed
        if not disc_detected and self._disc_present:
            await self._clear_disc_state()

    async def _handle_disc_detected(self) -> None:
        """Phase 1: disc detected (spinning up or already ready).

        Broadcasts presence immediately so the frontend can show the
        loading-album indicator while the drive spins up.
        """
        self._logger.info("Disc detected (spinning up)")

        is_active = self._is_active_source

        await self.state_machine.broadcast(SystemCdDriveStatus())

        # Show the loading-album indicator immediately
        if is_active:
            self.set_state(SourceState.READY, self._build_metadata())

    async def _handle_disc_ready(self) -> bool:
        """Phase 2: disc ready (CDS_DISC_OK).

        Always reads the TOC (local I/O — needed for sector offsets so playback
        can start instantly later). MusicBrainz lookup is deferred to source
        activation: at boot the source may never be opened, and the network
        often isn't ready when the watcher first sees a disc inserted at boot.

        Returns False only when the TOC read itself failed, so the watcher can
        retry on a later tick rather than latch the disc as handled.
        """
        self._logger.info("Disc ready, reading TOC")

        result = await self._data_service.read_disc()
        if not result:
            self._logger.warning("Failed to read disc TOC")
            return False

        disc_id, toc_string, toc_tracks, disc_end_lba = result
        self._sector_offsets = [t["offset"] for t in toc_tracks]
        self._disc_end_lba = disc_end_lba

        is_active = self._is_active_source

        # Same disc re-inserted: reuse cached metadata.
        if disc_id == self._last_disc_id and self._current_disc:
            self._logger.info(f"Same disc re-detected: {disc_id}")
            if is_active and self._mpv and self._mpv.is_connected:
                await self._auto_play_track_1()
            return True

        self._last_disc_id = disc_id
        self._logger.info(f"New disc: {disc_id}, {len(toc_tracks)} tracks")

        # Source not active → stop here; _do_start() / _load_disc_metadata()
        # will fetch MusicBrainz when the user opens the CD source.
        if not is_active:
            return True

        # Source is active: lookup metadata in parallel with mpv warmup.
        pre_start_task = asyncio.create_task(self._pre_start_service())
        metadata_coro = self._data_service.lookup_metadata(
            disc_id, toc_string, toc_tracks
        )
        disc_info, _ = await asyncio.gather(metadata_coro, pre_start_task)

        # Guard: disc may have been ejected during the gather — refuse to
        # repopulate state for a disc that's no longer in the drive.
        if not self._disc_present or self._last_disc_id != disc_id:
            return True
        self._current_disc = disc_info
        self._tracks = disc_info.tracks
        self._metadata_retry_pending = disc_info.album is None

        # Re-check active state after awaits (user could have switched source)
        still_active = self._is_active_source
        if still_active and self._mpv and self._mpv.is_connected:
            await self._auto_play_track_1()
        elif still_active:
            self.set_state(SourceState.READY, self._build_metadata())
        return True

    async def _pre_start_service(self) -> None:
        """Start milo-cd service and connect IPC (no stream loading)."""
        try:
            async with self._mpv_lock:
                if self._mpv and self._mpv.is_connected:
                    return

                if not await self._start_service_and_wait():
                    self._logger.warning("Pre-start: service failed")
                    return

                self._mpv = MpvController(ipc_socket_path=self._mpv_socket)
                if not await self._mpv.connect():
                    self._logger.warning("Pre-start: mpv connect failed")
                    return

        except Exception as e:
            self._logger.warning(f"Pre-start failed: {e}")

    async def _auto_play_track_1(self) -> None:
        """Auto-play track 1 after disc insertion while source is active.

        Delegates to the shared play path (loader freeze, advance gate, settle,
        locked restart) so insertion behaves exactly like a track selection.
        """
        await self._handle_play_track(PlayTrackParams(track_number=1))

    async def _clear_disc_state(self) -> None:
        """Clear in-memory disc state and stop playback. Does NOT eject physically.

        Called when the disc becomes inaccessible — either drive unplugged or
        disc physically removed.
        """
        self._logger.info("Clearing disc state")

        is_active = self._is_active_source

        if is_active and (self._is_playing or self._is_paused):
            await self._stop_reader_and_mpv()

        self._reset_playback_state()
        # Clear disc-level state (not covered by _reset_playback_state)
        self._current_disc = None
        self._tracks = []
        self._last_disc_id = None
        self._disc_present = False
        self._disc_ready = False
        self._toc_read_attempts = 0
        self._sector_offsets = []
        self._disc_end_lba = 0
        self._metadata_retry_pending = False
        self._metadata_retry_last_attempt = 0.0

        if is_active:
            self._update_connection_state()

        if self.state_machine:
            await self.state_machine.broadcast(SystemCdDriveStatus())

    # =========================================================================
    # COMMANDS
    # =========================================================================

    COMMANDS = {
        "play_track": PlayTrackParams,
        "pause": None,
        "resume": None,
        "next": None,
        "prev": None,
        "seek": SeekParams,
        "eject": None,
    }

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
        if cmd == "eject":
            return await self._handle_eject()
        return self.error_response(f"Unhandled command: {cmd}")

    async def _handle_play_track(self, params: PlayTrackParams) -> Dict[str, Any]:
        """Play a specific track by starting ioctl reader at the track's LBA."""
        if not self._mpv:
            return self.error_response("CD not active")

        track_number = params.track_number
        if not self._tracks or track_number > len(self._tracks):
            return self.error_response(f"Invalid track number: {track_number}")
        if not self._sector_offsets:
            return self.error_response("Disc not ready")

        try:
            # The whole gesture is one critical section: announce, restart,
            # settle. A pause landing between the restart and the settle would
            # otherwise be broadcast over playing audio (see
            # _restart_reader_and_mpv).
            async with self._playback_lock:
                start_lba = self._sector_offsets[track_number - 1]
                # Snapshot so a failed restart rolls back to the prior track
                # instead of leaving the source pointing at one that never
                # started.
                prev = (self._current_track, self._track_duration, self._track_position)

                # Announce the (re)load *before* the blocking restart: publish the
                # target track at position 0 with is_buffering=True so the frontend
                # snaps its progress bar to 0 and freezes it immediately. Without
                # this the bar keeps interpolating the outgoing position during the
                # ~1s restart and only resets once the post-restart broadcast lands —
                # the visible "bar starts, then jumps back to 0:00".
                self._current_track = track_number
                self._track_position = 0
                self._track_duration = self._tracks[track_number - 1].duration
                self._is_playing = True
                self._is_paused = False
                self._is_buffering = True  # Until mpv produces audio
                self._handle_pause_change(False)
                self._update_connection_state()

                restarted = await self._restart_reader_and_mpv(start_lba)
                if not restarted:
                    self._current_track, self._track_duration, self._track_position = prev
                if not await self._settle_after_restart(restarted):
                    return self.error_response("Failed to start playback")

            self.broadcast_error_cleared()
            return self.success_response(f"Playing track {track_number}")

        except Exception as e:
            self._logger.error(f"Play track error: {e}")
            return self.error_response(str(e))

    async def _handle_pause(self) -> Dict[str, Any]:
        if not self._mpv:
            return self.error_response("CD not active")
        try:
            # Waits behind an in-flight reader+mpv restart (up to ~8 s), and
            # _is_playing is re-read on the far side of that wait. Pressed
            # during a restart, the pause is now late instead of lost: it used
            # to be applied to the mpv the restart was about to replace, and
            # then un-paused by the restart's own set_property("pause", False).
            async with self._playback_lock:
                if not self._is_playing:
                    # Nothing to pause. Marking the source paused here would park
                    # it in the paused state with no stream loaded, and
                    # _handle_resume's paused branch un-pauses mpv in place rather
                    # than restarting the reader — so the next play tap would
                    # report playing while staying silent, with no way back except
                    # another pause.
                    return self.success_response("Not playing")

                # Snapshot the live playhead before pausing so the broadcast lands
                # exactly where the disc stopped — _track_position from the monitor
                # tick can be up to ~1s stale, which snaps the progress bar back.
                await self._sync_position_from_mpv()
                if not await self._mpv.pause():
                    return self.mpv_refused("pause")
                self._is_playing = False
                self._is_paused = True
                self._handle_pause_change(True)
                self._update_connection_state()
                return self.success_response("Paused")
        except Exception as e:
            self._logger.error(f"Pause error: {e}")
            return self.error_response(str(e))

    async def _handle_resume(self) -> Dict[str, Any]:
        if not self._mpv:
            return self.error_response("CD not active")
        try:
            # Same critical section as pause and play_track: the branch is
            # decided on flags read under the lock, so a resume pressed during a
            # restart cannot act on the state the restart is about to replace.
            async with self._playback_lock:
                # Short pause / preload — mpv still loaded, unblock the reader in
                # place. A cold preload (loaded but never advanced) incurs the ~1s
                # output-startup latency on un-pause, so freeze the bar and gate on
                # real advancement like play_track; a warm mid-track resume already
                # has time-pos > 0, so wait_until_advancing returns immediately.
                if self._is_paused:
                    self._is_buffering = True
                    self._update_connection_state()
                    if not await self._mpv.resume():
                        # Unfreeze the bar the announce above froze: the disc is
                        # still where it was paused, not about to advance.
                        self._is_buffering = False
                        self._update_connection_state()
                        return self.mpv_refused("resume")
                    if not await self._mpv.wait_until_advancing(timeout=3.0):
                        self._logger.warning("Resume: mpv playback did not advance in time")
                    await self._sync_position_from_mpv()
                    self._is_playing = True
                    self._is_paused = False
                    self._is_buffering = False
                    self._handle_pause_change(False)
                    self._update_connection_state()
                    return self.success_response("Resumed")

                if self._is_playing:
                    return self.success_response("Already playing")

                # Idle (auto-stopped or album-finished): no stream loaded. Restart
                # the reader at the LBA computed from the saved track + position
                # (same math as _handle_seek). Auto-stop resumes track N where it
                # left off; album-finished resumes track 1 at 0:00 (reset there).
                if not self._sector_offsets or not self._tracks:
                    return self.error_response("Disc not ready")
                track = self._current_track or 1
                if track > len(self._tracks):
                    track = 1
                target_lba = self._track_position_to_lba(track, self._track_position)

                # Freeze the bar at the resume point during the blocking restart,
                # then settle to the real (already-advanced) playhead.
                self._current_track = track
                self._track_duration = self._tracks[track - 1].duration
                self._is_playing = True
                self._is_paused = False
                self._is_buffering = True
                self._handle_pause_change(False)
                self._update_connection_state()

                restarted = await self._restart_reader_and_mpv(target_lba)
                if not await self._settle_after_restart(restarted):
                    return self.error_response("Failed to resume")
            self.broadcast_error_cleared()
            return self.success_response("Resumed")
        except Exception as e:
            self._logger.error(f"Resume error: {e}")
            return self.error_response(str(e))

    async def _handle_next_track(self) -> Dict[str, Any]:
        if not self._mpv or not self._current_track or not self._tracks:
            return self.error_response("No disc loaded")
        if self._current_track >= len(self._tracks):
            return self.success_response("Already on last track")
        return await self._handle_play_track(
            PlayTrackParams(track_number=self._current_track + 1)
        )

    async def _handle_prev_track(self) -> Dict[str, Any]:
        if not self._mpv or not self._current_track or not self._tracks:
            return self.error_response("No disc loaded")
        # Restart-then-previous (mirrors Spotify/go-librespot): past the
        # threshold, prev restarts the current track; within the first few
        # seconds it steps to the previous track. Read the exact live playhead
        # first — _track_position from the monitor tick can be up to ~1s stale.
        await self._sync_position_from_mpv()
        if self._track_position >= CD_PREV_RESTART_THRESHOLD_S:
            target = self._current_track
        else:
            target = max(1, self._current_track - 1)
        return await self._handle_play_track(PlayTrackParams(track_number=target))

    async def _handle_seek(self, params: SeekParams) -> Dict[str, Any]:
        """Seek within current track: restart reader at target LBA.

        The frontend is the only caller and always sends position_ms.
        """
        position = params.position_ms / 1000

        if not self._current_track or not self._sector_offsets:
            return self.error_response("No track playing")

        try:
            async with self._playback_lock:
                position = max(0, int(position))
                target_lba = self._track_position_to_lba(self._current_track, position)

                # Seek moves the playhead; it never decides to play. Restart the
                # reader at the target and resume audio only if audio was already
                # running. With the default autostart, a seek from any non-playing
                # state — paused, the preload's parked state where the user has
                # never pressed play, or auto-stopped — un-paused mpv while these
                # flags stayed put: the disc played behind a UI showing the play
                # button, and the monitor tick, gated on _is_playing, froze the bar.
                # Read under the lock, so a pause that arrived during an earlier
                # restart has already been applied here.
                was_playing = self._is_playing

                # Freeze the bar at the seek target during the blocking restart,
                # then settle to the real (already-advanced) playhead.
                self._track_position = position
                self._is_buffering = True
                self._update_connection_state()

                restarted = await self._restart_reader_and_mpv(
                    target_lba, autostart=was_playing
                )
                if not restarted:
                    # Nothing is loaded any more — leave the paused state so a play
                    # tap takes the idle full-restart path instead of un-pausing a
                    # dead mpv (_settle_after_restart only clears _is_playing).
                    self._is_paused = False
                if not await self._settle_after_restart(restarted):
                    return self.error_response("Seek failed")
                return self.success_response(f"Seeked to {position}s")

        except Exception as e:
            self._logger.error(f"Seek error: {e}")
            return self.error_response(str(e))

    async def _handle_eject(self) -> Dict[str, Any]:
        try:
            async with self._playback_lock:
                if self._is_playing or self._is_paused:
                    await self._stop_reader_and_mpv()

                # Immediately clear disc state and show "ejecting" in the UI
                self._reset_playback_state()
                self._current_disc = None
                self._tracks = []
                self._ejecting = True
                self._update_connection_state()

            proc = await asyncio.create_subprocess_exec(
                "eject", CD_DEVICE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()
            if proc.returncode != 0:
                stderr = (await proc.stderr.read()).decode().strip()
                self._logger.error(f"Eject failed (rc={proc.returncode}): {stderr}")
                self._ejecting = False
                return self.error_response(f"Eject failed: {stderr}")
            return self.success_response("Disc ejected")
        except Exception as e:
            self._logger.error(f"Eject error: {e}")
            self._ejecting = False
            return self.error_response(str(e))

    # =========================================================================
    # MONITOR
    # =========================================================================

    def _mpv_swap_in_progress(self) -> bool:
        return self._restarting

    async def _on_monitor_tick(self) -> None:
        """Track position via mpv time-pos mapped to disc LBA, detect auto-advance and album end."""
        if not self._is_playing or self._restarting:
            return

        time_pos = await self._mpv.get_property("time-pos")

        # A restart may have started during the await above (it swaps mpv +
        # _play_start_lba out from under us); bail so we don't map the old
        # session's time-pos onto the new LBA state.
        if self._restarting or not self._is_playing:
            return

        # Same for mpv dying under the read: `time_pos is None` below means "the
        # disc ran out", and a dead link is not that. Continuing auto-advances
        # into a reader+mpv restart against a socket nobody is listening on, and
        # — worse — settles the source to READY, which shuts the ACTIVE gate
        # _monitor_loop's fallback needs, so CD alone never showed the disconnect
        # banner. Leave it to the loop. is_connected is a sound discriminator
        # here *because* a read no longer re-attaches: the loop only enters a
        # tick with the link up, so False at this point can only mean the read
        # above killed it. A legitimate FIFO EOF cannot look like this — the unit
        # carries --idle=yes, so mpv survives it and stays connected.
        if not self._mpv.is_connected:
            return

        # Album/track end: reader finished or mpv reached EOF
        if time_pos is None:
            if not self._reader.is_running:
                if self._current_track and self._current_track >= len(self._tracks):
                    self._logger.info("Album finished")
                    self._is_playing = False
                    self._is_paused = False
                    self._current_track = 1
                    self._track_position = 0
                    self._update_connection_state()  # -> READY, screen can sleep
                    return
                # Reader stopped mid-album (error or EOF on non-last track)
                # Try auto-advancing if possible
                if self._current_track and self._current_track < len(self._tracks):
                    next_track = self._current_track + 1
                    self._logger.info(f"Auto-advancing to track {next_track}")
                    await self._handle_play_track(
                        PlayTrackParams(track_number=next_track)
                    )
                return
            return

        # Buffering -> playing transition
        state_changed = False
        if self._is_buffering:
            self._is_buffering = False
            state_changed = True

        # Calculate current disc LBA from mpv's perspective
        current_audio_lba = self._time_pos_to_lba(time_pos)

        # Track auto-advance (reader plays through track boundaries)
        new_track = self._lba_to_track(current_audio_lba)
        if new_track and new_track != self._current_track and new_track <= len(self._tracks):
            self._current_track = new_track
            self._track_duration = self._tracks[new_track - 1].duration
            state_changed = True

        # Position within current track
        if self._current_track:
            self._track_position = max(
                0, self._lba_to_track_position(current_audio_lba, self._current_track)
            )

        if state_changed:
            self._update_connection_state()
        elif self._position_sync_due():
            self.broadcast_position_update(
                int(self._track_position * 1000),
                int(self._track_duration * 1000),
            )

    async def _sync_position_from_mpv(self) -> None:
        """Refresh _track_position from mpv's live time-pos (sub-tick precision).

        Same LBA math as the monitor tick, but on demand — used by pause so the
        broadcast reflects the exact playhead rather than the last tick's value.
        """
        if not self._current_track:
            return
        time_pos = await self._mpv.get_property("time-pos")
        if time_pos is None:
            return
        current_audio_lba = self._time_pos_to_lba(time_pos)
        self._track_position = max(
            0, self._lba_to_track_position(current_audio_lba, self._current_track)
        )

    async def _on_mpv_disconnect(self) -> None:
        await asyncio.to_thread(self._reader.stop)
        self._reset_playback_state()

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _time_pos_to_lba(self, time_pos: float) -> int:
        """Disc LBA for mpv's time-pos (seconds into the current FIFO session)."""
        return self._play_start_lba + int(float(time_pos) * SECTORS_PER_SECOND)

    def _lba_to_track(self, lba: int) -> Optional[int]:
        """Find which track (1-based) an LBA falls in."""
        for i in range(len(self._sector_offsets) - 1, -1, -1):
            if lba >= self._sector_offsets[i]:
                return i + 1
        return None

    def _lba_to_track_position(self, lba: int, track: int) -> float:
        """Calculate position (seconds) within a track from an LBA."""
        if track < 1 or track > len(self._sector_offsets):
            return 0
        return (lba - self._sector_offsets[track - 1]) / SECTORS_PER_SECOND

    def _track_position_to_lba(self, track: int, position: float) -> int:
        """Map a (1-based track, position seconds) pair to a clamped disc LBA.

        Shared by seek and resume-from-saved-position.
        """
        track_start = self._sector_offsets[track - 1]
        target_lba = track_start + int(position) * SECTORS_PER_SECOND
        track_end = (
            self._sector_offsets[track] if track < len(self._sector_offsets)
            else self._disc_end_lba
        )
        return min(target_lba, track_end - 1)

    def _build_metadata(self) -> Dict[str, Any]:
        metadata: Dict[str, Any] = {
            "drive_connected": self._drive_connected,
            "disc_present": self._disc_present,
            "is_playing": self._is_playing,
            "is_buffering": self._is_buffering,
            "ejecting": self._ejecting,
            "cache_ready": bool(self._sector_offsets),
        }

        if self._current_disc:
            current = self._current_track or 1
            track_idx = current - 1
            playing_track = (
                self._tracks[track_idx]
                if self._tracks and 0 <= track_idx < len(self._tracks)
                else (self._tracks[0] if self._tracks else None)
            )

            # Disc identity — persistent extras (survive READY): a disc is a
            # hardware fact, kept visible while idle and across a WS reconnect.
            metadata.update({
                "disc_id": self._current_disc.disc_id,
                "disc_album": self._current_disc.album,
                "disc_artist": self._current_disc.artist,
                "disc_year": self._current_disc.year,
                "disc_cover_url": self._current_disc.cover_url,
                "track_count": self._current_disc.track_count,
                "tracks": [t.model_dump() for t in self._tracks],
                "current_track": current,
            })

            # Now-playing projection consumed by AudioPlayerFull. Always the
            # current track's title (default track 1) + the disc artist — never
            # the album standing in as the title. Position/duration are only
            # published once a session is live/paused (incl. preload), so the
            # progress bar stays hidden until the track is ready to play.
            session_active = self._is_playing or self._is_paused
            metadata.update({
                "album": self._current_disc.album,
                "artist": self._current_disc.artist,
                "album_art_url": self._current_disc.cover_url,
                "title": playing_track.title if playing_track
                else self._current_disc.album,
                "position": int(self._track_position * 1000) if session_active else 0,
                "duration": int((playing_track.duration if playing_track else 0) * 1000)
                if session_active else 0,
            })

        return metadata

    def _update_connection_state(self) -> None:
        # ACTIVE iff there's a live playback session; a merely-inserted or
        # auto-stopped/finished disc is READY so the screen can sleep. Unlike
        # the generic emit_connection_state (which drops the now-playing core in
        # READY), a loaded CD stays fully visible while idle — its album,
        # artist and cover are a real thing to show in the player — so publish
        # the whole metadata dict in both states. _build_metadata already
        # projects the idle view (album as title, no progress) vs the playing one.
        connected = self._is_playing or self._is_buffering or self._is_paused
        state = SourceState.ACTIVE if connected else SourceState.READY
        self.set_state(state, self._build_metadata())

    async def refresh_metadata(self) -> bool:
        """Refresh metadata so WebSocket initial_state contains live position."""
        self._metadata = self._build_metadata()
        return True

    # =========================================================================
    # PUBLIC API (for routes)
    # =========================================================================

    @property
    def data_service(self) -> CdDataService:
        return self._data_service

    @property
    def tracks(self) -> List[TrackInfo]:
        return self._tracks

    @property
    def drive_connected(self) -> bool:
        return self._drive_connected

    @property
    def disc_present(self) -> bool:
        return self._disc_present
