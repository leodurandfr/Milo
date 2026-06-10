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
import logging
from time import monotonic
from typing import Any, Dict, List, Optional

from backend.config.constants import CD_DEVICE
from backend.core.models.audio_state import AudioSource, SourceState
from backend.sources.cd.data import CDS_DISC_OK, CDS_DRIVE_NOT_READY, CdDataService
from backend.sources.cd.models import DiscInfo, TrackInfo
from backend.sources.cd.reader import CD_FIFO_PATH, SECTORS_PER_SECOND, CdIoctlReader
from backend.shared.decorators import handle_errors
from backend.shared.mpv import MpvController
from backend.shared.mpv_audio_source import MpvAudioSource

logger = logging.getLogger(__name__)

# Retry MusicBrainz when the initial lookup fell through to the fallback DiscInfo
# (typically because DNS wasn't ready at boot when the disc was first detected).
METADATA_RETRY_INTERVAL_S = 60.0

# Disc insertion/removal detection latency — one blocking probe per tick.
DISC_POLL_INTERVAL_S = 2.0


class CdSource(MpvAudioSource):

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

        # Disc watcher (permanent, from initialize)
        self._disc_watcher_task: Optional[asyncio.Task] = None
        self._watcher_first_check_done = False

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
        self._album_finished = False
        self._is_paused = False
        self._ejecting = False
        self._play_start_lba: int = 0  # LBA where current FIFO session started

    def _reset_playback_state(self) -> None:
        """Reset CD-specific playback fields (called by _do_stop/_auto_stop_action)."""
        super()._reset_playback_state()
        self._current_track = None
        self._track_position = 0
        self._track_duration = 0
        self._album_finished = False
        self._is_paused = False
        self._ejecting = False
        self._play_start_lba = 0

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

            # Metadata lookup in background if disc present but no metadata yet.
            # Runs AFTER _do_start returns so the transition completes first
            # and the frontend can show the loading-album indicator.
            if self._disc_present and not self._current_disc and self._last_disc_id:
                self._bg.spawn(
                    self._load_disc_metadata(),
                    label="load_disc_metadata_on_start",
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

    @handle_errors(default=False)
    async def _do_stop(self) -> bool:
        """Stop playback and service."""
        await self._cleanup()
        self._reset_playback_state()
        return await self._stop_service()

    async def _cleanup(self) -> None:
        """Clean up reader + mpv resources. Does NOT stop disc watcher or clear disc info."""
        self._stop_monitor()
        await asyncio.to_thread(self._reader.stop)
        if self._mpv:
            await self._mpv.disconnect()
            self._mpv = None
        self._is_playing = False
        self._is_paused = False
        self._is_buffering = False

    # =========================================================================
    # READER + MPV ORCHESTRATION
    # =========================================================================

    async def _start_reader_and_mpv(self, start_lba: int) -> bool:
        """Start ioctl reader at given LBA and connect mpv to the FIFO.

        Sequence: reader opens CD + creates FIFO + blocks on write-open,
        then mpv opens FIFO for reading -> both sides connect.
        """
        self._reader.start(start_lba, self._disc_end_lba)

        if not self._reader.wait_ready(timeout=5.0):
            self._logger.error("Reader not ready within timeout")
            await asyncio.to_thread(self._reader.stop)
            return False

        success = await self._mpv.load_stream(CD_FIFO_PATH)
        if not success:
            self._logger.error("mpv failed to open FIFO")
            await asyncio.to_thread(self._reader.stop)
            return False

        # Ensure mpv is not paused (could be leftover from previous pause command)
        await self._mpv.set_property("pause", False)

        self._play_start_lba = start_lba
        self._logger.info(f"Reader + mpv started at LBA {start_lba}")
        return True

    async def _stop_reader_and_mpv(self) -> None:
        """Stop reader and mpv playback. mpv stop closes FIFO read end -> reader exits."""
        if self._mpv:
            await self._mpv.stop()  # Closes FIFO read end -> BrokenPipeError in reader
        await asyncio.to_thread(self._reader.stop)

    async def _auto_stop_action(self) -> None:
        """Stop playback in place after pause timeout.

        Stops the reader + mpv, clears track-level state. The disc itself
        stays loaded (track list remains visible), so a tap on any track
        resumes immediately without re-reading the TOC.
        """
        await self._stop_reader_and_mpv()
        self._reset_playback_state()
        self._update_connection_state()

    async def _restart_reader_and_mpv(self, start_lba: int) -> bool:
        """Restart reader at a new LBA position and reconnect mpv."""
        await self._stop_reader_and_mpv()
        return await self._start_reader_and_mpv(start_lba)

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
        if not (self.state_machine
                and self.state_machine.system_state.active_source == AudioSource.CD):
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
            await self.state_machine.broadcast_event(
                "system", "cd_drive_status", {
                    "source": "cd",
                    "drive_connected": self._drive_connected,
                    "disc_present": self._disc_present,
                }
            )
            if not self._drive_connected:
                await self._clear_disc_state()
                return
            # Drive newly connected — refresh source metadata so the frontend
            # transitions out of "no_drive" (mirrors the _clear_disc_state path).
            if self.state_machine.system_state.active_source == AudioSource.CD:
                self._update_connection_state()

        if not self._drive_connected:
            return

        disc_detected = status in (CDS_DRIVE_NOT_READY, CDS_DISC_OK)
        disc_ready = status == CDS_DISC_OK

        if not self._watcher_first_check_done:
            self._watcher_first_check_done = True

        # Phase 1: disc newly detected (spinning up or already ready)
        if disc_detected and not self._disc_present:
            self._disc_present = True
            await self._handle_disc_detected()

        # Phase 2: disc became ready (TOC readable)
        if disc_ready and not self._disc_ready:
            self._disc_ready = True
            await self._handle_disc_ready()

        # Disc removed
        if not disc_detected and self._disc_present:
            await self._clear_disc_state()

    async def _handle_disc_detected(self) -> None:
        """Phase 1: disc detected (spinning up or already ready).

        Broadcasts presence immediately so the frontend can show the
        loading-album indicator while the drive spins up.
        """
        self._logger.info("Disc detected (spinning up)")

        is_active = (
            self.state_machine
            and self.state_machine.system_state.active_source == AudioSource.CD
        )

        await self.state_machine.broadcast_event(
            "system", "cd_drive_status", {
                "source": "cd",
                "drive_connected": self._drive_connected,
                "disc_present": True,
            }
        )

        # Show the loading-album indicator immediately
        if is_active:
            self.set_state(SourceState.WAITING, {
                "disc_present": True, "cache_ready": False,
                "drive_connected": self._drive_connected,
                "is_playing": False, "is_buffering": False,
                "album_finished": False,
            })

    async def _handle_disc_ready(self) -> None:
        """Phase 2: disc ready (CDS_DISC_OK).

        Always reads the TOC (local I/O — needed for sector offsets so playback
        can start instantly later). MusicBrainz lookup is deferred to source
        activation: at boot the source may never be opened, and the network
        often isn't ready when the watcher first sees a disc inserted at boot.
        """
        self._logger.info("Disc ready, reading TOC")

        result = await self._data_service.read_disc()
        if not result:
            self._logger.warning("Failed to read disc TOC")
            return

        disc_id, toc_string, toc_tracks, disc_end_lba = result
        self._sector_offsets = [t["offset"] for t in toc_tracks]
        self._disc_end_lba = disc_end_lba

        is_active = (
            self.state_machine
            and self.state_machine.system_state.active_source == AudioSource.CD
        )

        # Same disc re-inserted: reuse cached metadata.
        if disc_id == self._last_disc_id and self._current_disc:
            self._logger.info(f"Same disc re-detected: {disc_id}")
            if is_active and self._mpv and self._mpv.is_connected:
                await self._auto_play_track_1()
            return

        self._last_disc_id = disc_id
        self._logger.info(f"New disc: {disc_id}, {len(toc_tracks)} tracks")

        # Source not active → stop here; _do_start() / _load_disc_metadata()
        # will fetch MusicBrainz when the user opens the CD source.
        if not is_active:
            return

        # Source is active: lookup metadata in parallel with mpv warmup.
        pre_start_task = asyncio.create_task(self._pre_start_service())
        metadata_coro = self._data_service.lookup_metadata(
            disc_id, toc_string, toc_tracks
        )
        disc_info, _ = await asyncio.gather(metadata_coro, pre_start_task)

        # Guard: disc may have been ejected during the gather — refuse to
        # repopulate state for a disc that's no longer in the drive.
        if not self._disc_present or self._last_disc_id != disc_id:
            return
        self._current_disc = disc_info
        self._tracks = disc_info.tracks
        self._metadata_retry_pending = disc_info.album is None

        # Re-check active state after awaits (user could have switched source)
        still_active = (
            self.state_machine
            and self.state_machine.system_state.active_source == AudioSource.CD
        )
        if still_active and self._mpv and self._mpv.is_connected:
            await self._auto_play_track_1()
        elif still_active:
            self.set_state(SourceState.WAITING, self._build_metadata())

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
        """Auto-play track 1 after disc insertion while source is active."""
        if not self._mpv or not self._sector_offsets or not self._tracks:
            return

        if not await self._start_reader_and_mpv(self._sector_offsets[0]):
            self._logger.error("Auto-play: failed to start reader")
            return

        self._current_track = 1
        self._track_position = 0
        self._track_duration = self._tracks[0].duration
        self._is_playing = True
        self._is_paused = False
        self._album_finished = False
        self._update_connection_state()
        self._logger.info("Auto-playing track 1")

    async def _clear_disc_state(self) -> None:
        """Clear in-memory disc state and stop playback. Does NOT eject physically.

        Called when the disc becomes inaccessible — either drive unplugged or
        disc physically removed.
        """
        self._logger.info("Clearing disc state")

        is_active = (
            self.state_machine
            and self.state_machine.system_state.active_source == AudioSource.CD
        )

        if is_active and (self._is_playing or self._is_paused):
            await self._stop_reader_and_mpv()

        self._reset_playback_state()
        # Clear disc-level state (not covered by _reset_playback_state)
        self._current_disc = None
        self._tracks = []
        self._last_disc_id = None
        self._disc_present = False
        self._disc_ready = False
        self._sector_offsets = []
        self._disc_end_lba = 0
        self._metadata_retry_pending = False
        self._metadata_retry_last_attempt = 0.0

        if is_active:
            self._update_connection_state()

        if self.state_machine:
            await self.state_machine.broadcast_event(
                "system", "cd_drive_status", {
                    "source": "cd",
                    "drive_connected": self._drive_connected,
                    "disc_present": False,
                }
            )

    # =========================================================================
    # COMMANDS
    # =========================================================================

    async def _handle_command(self, cmd: str, data: Dict[str, Any]) -> Dict[str, Any]:
        if cmd == "play_track":
            return await self._handle_play_track(data)
        if cmd == "pause":
            return await self._handle_pause()
        if cmd == "resume":
            return await self._handle_resume()
        if cmd in ("next_track", "next"):
            return await self._handle_next_track()
        if cmd in ("prev_track", "prev"):
            return await self._handle_prev_track()
        if cmd == "seek":
            return await self._handle_seek(data)
        if cmd == "eject":
            return await self._handle_eject()
        return self.error_response(f"Unknown command: {cmd}")

    async def _handle_play_track(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Play a specific track by starting ioctl reader at the track's LBA."""
        if not self._mpv:
            return self.error_response("CD not active")

        track_number = data.get("track_number")
        if track_number is None:
            return self.error_response("track_number required")
        if not self._tracks or track_number < 1 or track_number > len(self._tracks):
            return self.error_response(f"Invalid track number: {track_number}")
        if not self._sector_offsets:
            return self.error_response("Disc not ready")

        try:
            start_lba = self._sector_offsets[track_number - 1]

            if not await self._restart_reader_and_mpv(start_lba):
                return self.error_response("Failed to start playback")

            self._current_track = track_number
            self._track_position = 0
            self._track_duration = self._tracks[track_number - 1].duration
            self._is_playing = True
            self._is_paused = False
            self._is_buffering = True  # Until mpv produces audio
            self._album_finished = False
            self._handle_pause_change(False)

            self._update_connection_state()
            self.broadcast_error_cleared()
            return self.success_response(f"Playing track {track_number}")

        except Exception as e:
            self._logger.error(f"Play track error: {e}")
            return self.error_response(str(e))

    async def _handle_pause(self) -> Dict[str, Any]:
        if not self._mpv:
            return self.error_response("CD not active")
        try:
            await self._mpv.pause()
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
            if self._album_finished:
                return await self._handle_play_track({"track_number": 1})

            # Resume from pause — reader unblocks when mpv reads again
            if self._is_paused:
                await self._mpv.resume()
                self._is_playing = True
                self._is_paused = False
                self._handle_pause_change(False)
                self._update_connection_state()
                return self.success_response("Resumed")

            # First play or resume from stop -> go through play_track
            if not self._is_playing:
                track = self._current_track or 1
                return await self._handle_play_track({"track_number": track})

            return self.success_response("Already playing")
        except Exception as e:
            self._logger.error(f"Resume error: {e}")
            return self.error_response(str(e))

    async def _handle_next_track(self) -> Dict[str, Any]:
        if not self._mpv or not self._current_track or not self._tracks:
            return self.error_response("No disc loaded")
        if self._current_track >= len(self._tracks):
            return self.success_response("Already on last track")
        return await self._handle_play_track(
            {"track_number": self._current_track + 1}
        )

    async def _handle_prev_track(self) -> Dict[str, Any]:
        if not self._mpv or not self._current_track or not self._tracks:
            return self.error_response("No disc loaded")
        target = max(1, self._current_track - 1)
        return await self._handle_play_track({"track_number": target})

    async def _handle_seek(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Seek within current track: restart reader at target LBA.

        The frontend is the only caller and always sends position_ms.
        """
        position_ms = data.get("position_ms")
        if position_ms is None:
            return self.error_response("position_ms required")
        position = position_ms / 1000

        if not self._current_track or not self._sector_offsets:
            return self.error_response("No track playing")

        try:
            position = max(0, int(position))
            track_start = self._sector_offsets[self._current_track - 1]
            target_lba = track_start + position * SECTORS_PER_SECOND

            # Clamp to track boundaries
            if self._current_track < len(self._sector_offsets):
                track_end = self._sector_offsets[self._current_track]
            else:
                track_end = self._disc_end_lba
            target_lba = min(target_lba, track_end - 1)

            if not await self._restart_reader_and_mpv(target_lba):
                return self.error_response("Seek failed")

            self._track_position = position
            self._is_buffering = True
            self._update_connection_state()
            return self.success_response(f"Seeked to {position}s")

        except Exception as e:
            self._logger.error(f"Seek error: {e}")
            return self.error_response(str(e))

    async def _handle_eject(self) -> Dict[str, Any]:
        try:
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

    async def _on_monitor_tick(self) -> None:
        """Track position via mpv time-pos mapped to disc LBA, detect auto-advance and album end."""
        if not self._is_playing:
            return

        time_pos = await self._mpv.get_property("time-pos")

        # Album/track end: reader finished or mpv reached EOF
        if time_pos is None:
            if not self._reader.is_running:
                if self._current_track and self._current_track >= len(self._tracks):
                    self._logger.info("Album finished")
                    self._album_finished = True
                    self._is_playing = False
                    self._is_paused = False
                    self._track_position = self._track_duration
                    self._update_connection_state()
                    return
                # Reader stopped mid-album (error or EOF on non-last track)
                # Try auto-advancing if possible
                if self._current_track and self._current_track < len(self._tracks):
                    next_track = self._current_track + 1
                    self._logger.info(f"Auto-advancing to track {next_track}")
                    await self._handle_play_track({"track_number": next_track})
                return
            return

        # Buffering -> playing transition
        state_changed = False
        if self._is_buffering:
            self._is_buffering = False
            state_changed = True

        # Calculate current disc LBA from mpv's perspective
        current_audio_lba = self._play_start_lba + int(float(time_pos) * SECTORS_PER_SECOND)

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

    async def _on_mpv_disconnect(self) -> None:
        await asyncio.to_thread(self._reader.stop)
        self._reset_playback_state()

    # =========================================================================
    # HELPERS
    # =========================================================================

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
            first_track = self._tracks[0] if self._tracks else None
            metadata.update({
                "disc_id": self._current_disc.disc_id,
                "album": self._current_disc.album,
                "artist": self._current_disc.artist,
                "year": self._current_disc.year,
                "album_art_url": self._current_disc.cover_url,
                "track_count": self._current_disc.track_count,
                "tracks": [t.model_dump() for t in self._tracks],
                "current_track": 1,
                "title": first_track.title if first_track else self._current_disc.album,
                "position": 0,
                "duration": int(first_track.duration * 1000) if first_track else 0,
            })

        if self._current_track and self._tracks:
            track_idx = self._current_track - 1
            if 0 <= track_idx < len(self._tracks):
                metadata.update({
                    "current_track": self._current_track,
                    "title": self._tracks[track_idx].title,
                    "position": int(self._track_position * 1000),
                    "duration": int(self._track_duration * 1000),
                })

        return metadata

    def _update_connection_state(self) -> None:
        has_disc = bool(self._current_disc)
        metadata = self._build_metadata()
        self._set_active_or_waiting(has_disc, metadata, metadata)

    async def _get_status(self) -> Dict[str, Any]:
        return self._build_metadata()

    async def _refresh_metadata(self) -> bool:
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
