# backend/features/cd/source.py
"""
CD audio source using MPV.

This source handles CD playback via a USB CD drive with:
- Automatic disc detection via ioctl polling
- MusicBrainz metadata lookup with local cache
- Track navigation via mpv chapters (cdda:// protocol)
- Cover art from MusicBrainz Cover Art Archive
- Auto-switch to CD source on disc insertion

The disc watcher runs permanently from initialize(), independent of
start/stop lifecycle, to detect disc insertion/removal at any time.
"""
import asyncio
from typing import Any, Dict, List, Optional

from backend.config.constants import CD_DEVICE
from backend.core.models.audio_state import AudioSource, PluginState
from backend.features.cd.data import CdDataService
from backend.features.cd.models import DiscInfo, TrackInfo
from backend.shared.decorators import handle_errors
from backend.shared.mpv import MpvController
from backend.shared.mpv_audio_source import MpvAudioSource


class CdSource(MpvAudioSource):
    """
    CD audio source using MPV with cdda:// protocol.

    Implements AudioSource Protocol with:
    - start(): Start MPV service, connect IPC, load disc
    - stop(): Stop playback and service
    - restart(): Restart service with state reset
    - status(): Get current status with disc/track metadata
    - command(): Handle playback and navigation commands

    The disc watcher task polls /dev/sr0 every 2 seconds and runs
    permanently from initialize(), independent of the start/stop lifecycle.
    """

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

        # Data service (initialized immediately, persists across start/stop)
        self._data_service = CdDataService()

        # Disc watcher task (runs permanently from initialize)
        self._disc_watcher_task: Optional[asyncio.Task] = None

        # Disc state (persists across start/stop for UI)
        self._current_disc: Optional[DiscInfo] = None
        self._tracks: List[TrackInfo] = []
        self._drive_connected = False
        self._disc_present = False
        self._last_disc_id: Optional[str] = None

        # Playback state (reset on stop)
        self._current_track: Optional[int] = None  # 1-based
        self._track_position: float = 0
        self._track_duration: float = 0
        self._album_finished = False
        self._loading = False  # Guards monitor tick during stream loading
        self._chapter_offsets: List[float] = []  # Start time of each chapter

    # =========================================================================
    # LIFECYCLE
    # =========================================================================

    @handle_errors(default=False)
    async def initialize(self) -> bool:
        """Initialize data service and start disc watcher."""
        await self._data_service.initialize()
        # Start disc watcher only after data service is ready
        self._disc_watcher_task = asyncio.create_task(self._disc_watcher_loop())
        self._logger.info("CD source initialized, disc watcher started")
        return await super().initialize()

    async def _do_start(self) -> bool:
        """Start mpv service and begin playback if disc is present."""
        try:
            # 1. Start milo-cd.service
            if not await self._start_service_and_wait():
                return False

            # 2. Connect to mpv IPC
            self._mpv = MpvController(ipc_socket_path=self._mpv_socket)
            if not await self._mpv.connect():
                self._logger.error("Failed to connect to mpv IPC")
                return False

            # 3. Load disc if present
            if self._disc_present and self._current_disc:
                self._loading = True
                success = await self._mpv.load_stream(f"cdda://{CD_DEVICE}")
                if success:
                    self._is_playing = True
                    self._album_finished = False
                    self._current_track = 1
                    # Wait briefly for mpv to parse chapters
                    await asyncio.sleep(0.5)
                    await self._read_chapter_offsets()
                    if self._tracks:
                        self._track_duration = self._tracks[0].duration
                self._loading = False

            # 4. Start monitor
            self._start_monitor()

            # 5. Update state
            self._update_connection_state()

            return True

        except Exception as e:
            self._logger.error(f"Start failed: {e}")
            await self._cleanup()
            return False

    @handle_errors(default=False)
    async def _do_stop(self) -> bool:
        """Stop playback and service."""
        await self._cleanup()

        # Reset playback state but keep disc info for UI
        self._current_track = None
        self._track_position = 0
        self._track_duration = 0
        self._album_finished = False
        self._loading = False
        self._chapter_offsets = []

        return await self._stop_service()

    async def _cleanup(self) -> None:
        """Clean up mpv resources. Does NOT stop disc watcher or clear disc info."""
        self._stop_monitor()

        if self._mpv:
            await self._mpv.disconnect()
            self._mpv = None

        self._is_playing = False
        self._is_buffering = False

    # =========================================================================
    # DISC WATCHER (runs permanently)
    # =========================================================================

    async def _disc_watcher_loop(self) -> None:
        """Poll for disc drive and disc presence every 2 seconds."""
        while True:
            try:
                await asyncio.sleep(2.0)
                await self._check_drive_and_disc()
            except asyncio.CancelledError:
                return
            except Exception as e:
                self._logger.error(f"Disc watcher error: {e}")

    async def _check_drive_and_disc(self) -> None:
        """Check drive connection and disc insertion status."""
        # Check drive presence
        was_connected = self._drive_connected
        self._drive_connected = await asyncio.to_thread(
            self._data_service.check_drive_present
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
                # Drive removed: clear everything
                await self._handle_disc_removed()
                return

        if not self._drive_connected:
            return

        # Check disc presence
        was_present = self._disc_present
        self._disc_present = await asyncio.to_thread(
            self._data_service.check_disc_present
        )

        if self._disc_present and not was_present:
            await self._handle_disc_inserted()
        elif not self._disc_present and was_present:
            await self._handle_disc_removed()

    async def _handle_disc_inserted(self) -> None:
        """Handle a newly inserted disc: read TOC, lookup metadata, auto-switch."""
        self._logger.info("Disc inserted, reading TOC...")

        result = await self._data_service.read_disc()
        if not result:
            self._logger.warning("Failed to read disc TOC")
            return

        disc_id, toc_string, toc_tracks = result

        # Skip if same disc (re-insertion detection)
        if disc_id == self._last_disc_id and self._current_disc:
            self._logger.info(f"Same disc re-detected: {disc_id}")
            return

        self._last_disc_id = disc_id
        self._logger.info(f"New disc: {disc_id}, {len(toc_tracks)} tracks")

        # Lookup metadata (cache or MusicBrainz)
        disc_info = await self._data_service.lookup_metadata(
            disc_id, toc_string, toc_tracks
        )
        self._current_disc = disc_info
        self._tracks = disc_info.tracks

        self._logger.info(
            f"Disc metadata: {disc_info.artist} - {disc_info.album} "
            f"({disc_info.track_count} tracks)"
        )

        # Auto-switch to CD source and start playback
        await self.state_machine.transition_to_source(AudioSource.CD)

    async def _handle_disc_removed(self) -> None:
        """Handle disc removal: stop playback and clear state."""
        self._logger.info("Disc removed")

        # Stop playback if CD is active source
        if (
            self.state_machine
            and self.state_machine.system_state.active_source == AudioSource.CD
        ):
            try:
                await self.state_machine.transition_to_source(AudioSource.NONE)
            except Exception as e:
                self._logger.error(f"Error stopping CD source: {e}")

        # Clear disc state
        self._current_disc = None
        self._tracks = []
        self._last_disc_id = None
        self._disc_present = False
        self._current_track = None
        self._track_position = 0
        self._track_duration = 0
        self._album_finished = False
        self._chapter_offsets = []

        # Broadcast disc removed
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
        """Handle CD-specific commands."""
        if cmd == "play_track":
            return await self._handle_play_track(data)
        if cmd == "pause":
            return await self._handle_pause()
        if cmd == "resume":
            return await self._handle_resume()
        if cmd == "next_track":
            return await self._handle_next_track()
        if cmd == "prev_track":
            return await self._handle_prev_track()
        if cmd == "seek":
            return await self._handle_seek(data)
        if cmd == "stop_playback":
            return await self._handle_stop_playback()
        if cmd == "eject":
            return await self._handle_eject()
        if cmd == "get_tracks":
            return self.success_response(
                "Tracks retrieved",
                tracks=[t.model_dump() for t in self._tracks],
            )
        return self.error_response(f"Unknown command: {cmd}")

    async def _handle_play_track(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Play a specific track by number (1-based)."""
        if not self._mpv:
            return self.error_response("CD not active")

        track_number = data.get("track_number")
        if track_number is None:
            return self.error_response("track_number required")

        if not self._tracks or track_number < 1 or track_number > len(self._tracks):
            return self.error_response(f"Invalid track number: {track_number}")

        try:
            if self._album_finished or not self._is_playing:
                # Reload the disc stream
                success = await self._mpv.load_stream(f"cdda://{CD_DEVICE}")
                if not success:
                    return self.error_response("Failed to load CD")
                await asyncio.sleep(0.5)
                await self._read_chapter_offsets()

            # Set chapter (0-based in mpv)
            await self._mpv.set_property("chapter", track_number - 1)
            await self._mpv.resume()

            self._current_track = track_number
            self._track_position = 0
            self._track_duration = self._tracks[track_number - 1].duration
            self._is_playing = True
            self._album_finished = False

            self._update_connection_state()
            self.broadcast_error_cleared()

            return self.success_response(f"Playing track {track_number}")

        except Exception as e:
            self._logger.error(f"Play track error: {e}")
            return self.error_response(str(e))

    async def _handle_pause(self) -> Dict[str, Any]:
        """Pause playback."""
        if not self._mpv:
            return self.error_response("CD not active")
        try:
            if self._is_playing:
                await self._mpv.pause()
                self._is_playing = False
                self._update_connection_state()
            return self.success_response("Paused")
        except Exception as e:
            return self.error_response(str(e))

    async def _handle_resume(self) -> Dict[str, Any]:
        """Resume playback. If album finished, restart from track 1."""
        if not self._mpv:
            return self.error_response("CD not active")
        try:
            if self._album_finished:
                return await self._handle_play_track({"track_number": 1})

            if not self._is_playing:
                await self._mpv.resume()
                self._is_playing = True
                self._update_connection_state()

            return self.success_response("Resumed")
        except Exception as e:
            return self.error_response(str(e))

    async def _handle_next_track(self) -> Dict[str, Any]:
        """Skip to next track. No-op if on last track."""
        if not self._mpv:
            return self.error_response("CD not active")
        if not self._current_track or not self._tracks:
            return self.error_response("No disc loaded")

        if self._current_track >= len(self._tracks):
            return self.success_response("Already on last track")

        try:
            response = await self._mpv.command("add", "chapter", 1)
            if response is not None and response.get("error") == "success":
                self._current_track += 1
                self._track_position = 0
                self._track_duration = self._tracks[self._current_track - 1].duration
                if not self._is_playing:
                    self._is_playing = True
                    await self._mpv.resume()
                self._album_finished = False
                self._update_connection_state()
            return self.success_response(f"Track {self._current_track}")
        except Exception as e:
            return self.error_response(str(e))

    async def _handle_prev_track(self) -> Dict[str, Any]:
        """Skip to previous track."""
        if not self._mpv:
            return self.error_response("CD not active")
        if not self._current_track or not self._tracks:
            return self.error_response("No disc loaded")

        if self._current_track <= 1:
            # Restart current track from beginning
            await self._handle_seek({"position": 0})
            return self.success_response("Restarted track 1")

        try:
            response = await self._mpv.command("add", "chapter", -1)
            if response is not None and response.get("error") == "success":
                self._current_track -= 1
                self._track_position = 0
                self._track_duration = self._tracks[self._current_track - 1].duration
                if not self._is_playing:
                    self._is_playing = True
                    await self._mpv.resume()
                self._album_finished = False
                self._update_connection_state()
            return self.success_response(f"Track {self._current_track}")
        except Exception as e:
            return self.error_response(str(e))

    async def _handle_seek(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Seek within the current track."""
        position = data.get("position")
        if position is None:
            return self.error_response("position required")

        try:
            position = int(position)
            # Calculate absolute seek position = chapter start + position in track
            if self._chapter_offsets and self._current_track:
                chapter_idx = self._current_track - 1
                if chapter_idx < len(self._chapter_offsets):
                    absolute_pos = self._chapter_offsets[chapter_idx] + position
                    await self._mpv.seek(absolute_pos)
                    self._track_position = position
                    self._update_connection_state()
                    return self.success_response(f"Seeked to {position}s")

            # Fallback: direct seek
            await self._mpv.seek(position)
            self._track_position = position
            self._update_connection_state()
            return self.success_response(f"Seeked to {position}s")
        except Exception as e:
            return self.error_response(str(e))

    async def _handle_stop_playback(self) -> Dict[str, Any]:
        """Stop playback."""
        try:
            if self._mpv:
                await self._mpv.stop()
            self._is_playing = False
            self._is_buffering = False
            self._current_track = None
            self._track_position = 0
            self._album_finished = False
            self.set_state(
                PluginState.READY,
                {"is_playing": False, "is_buffering": False, "ready": True},
            )
            return self.success_response("Playback stopped")
        except Exception as e:
            return self.error_response(str(e))

    async def _handle_eject(self) -> Dict[str, Any]:
        """Eject the disc."""
        try:
            # Stop playback first
            if self._is_playing and self._mpv:
                await self._mpv.stop()
                self._is_playing = False

            # Eject the disc
            proc = await asyncio.create_subprocess_exec(
                "eject", "/dev/sr0",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()

            # The disc watcher will detect removal and handle state cleanup
            return self.success_response("Disc ejected")
        except Exception as e:
            self._logger.error(f"Eject error: {e}")
            return self.error_response(str(e))

    # =========================================================================
    # MONITOR HOOKS
    # =========================================================================

    async def _on_monitor_tick(self) -> None:
        """Track playback position and detect track/album transitions."""
        if not self._current_disc or self._loading:
            return

        # Get current chapter (0-based)
        chapter = await self._mpv.get_property("chapter")
        time_pos = await self._mpv.get_property("time-pos")

        # Detect album end: time-pos becomes None when mpv finishes
        if (
            self._is_playing
            and time_pos is None
            and self._current_track
            and self._tracks
            and self._current_track >= len(self._tracks)
        ):
            self._logger.info("Album finished")
            self._album_finished = True
            self._is_playing = False
            # Keep last track displayed with position at max
            self._track_position = self._track_duration
            self._update_connection_state()
            return

        if time_pos is None or chapter is None:
            return

        # Update current track (convert 0-based chapter to 1-based track)
        new_track = int(chapter) + 1
        track_changed = new_track != self._current_track

        if track_changed and 1 <= new_track <= len(self._tracks):
            self._current_track = new_track
            self._track_duration = self._tracks[new_track - 1].duration
            self._logger.debug(f"Track changed to {new_track}")

        # Calculate position within track
        if self._chapter_offsets and self._current_track:
            chapter_idx = self._current_track - 1
            if chapter_idx < len(self._chapter_offsets):
                self._track_position = max(
                    0, float(time_pos) - self._chapter_offsets[chapter_idx]
                )
        else:
            self._track_position = float(time_pos)

        # Check if playing
        was_playing = self._is_playing
        pause_state = await self._mpv.get_property("pause")
        self._is_playing = pause_state is not True and time_pos is not None

        # Broadcast on track change or position change
        if track_changed or self._is_playing or (was_playing and not self._is_playing):
            self._update_connection_state()

    async def _on_mpv_disconnect(self) -> None:
        """Handle unexpected mpv disconnect."""
        self._is_playing = False
        self._is_buffering = False
        self._current_track = None
        self._track_position = 0
        self._album_finished = False
        self._loading = False

    # =========================================================================
    # HELPERS
    # =========================================================================

    async def _read_chapter_offsets(self) -> None:
        """Read chapter start times from mpv for seek calculations."""
        chapter_list = await self._mpv.get_property("chapter-list")
        if chapter_list and isinstance(chapter_list, list):
            self._chapter_offsets = [
                ch.get("time", 0) for ch in chapter_list
            ]
            self._logger.debug(f"Chapter offsets: {self._chapter_offsets}")
        else:
            self._chapter_offsets = []

    def _build_metadata(self) -> Dict[str, Any]:
        """Build metadata dict for current disc and track state."""
        metadata: Dict[str, Any] = {
            "drive_connected": self._drive_connected,
            "disc_present": self._disc_present,
            "is_playing": self._is_playing,
            "is_buffering": self._is_buffering,
            "album_finished": self._album_finished,
        }

        if self._current_disc:
            metadata.update({
                "disc_id": self._current_disc.disc_id,
                "album": self._current_disc.album,
                "artist": self._current_disc.artist,
                "year": self._current_disc.year,
                "cover_url": self._current_disc.cover_url,
                "track_count": self._current_disc.track_count,
                "tracks": [t.model_dump() for t in self._tracks],
            })

        if self._current_track and self._tracks:
            track_idx = self._current_track - 1
            if 0 <= track_idx < len(self._tracks):
                metadata.update({
                    "current_track": self._current_track,
                    "track_title": self._tracks[track_idx].title,
                    "track_position": int(self._track_position),
                    "track_duration": int(self._track_duration),
                })

        return metadata

    def _update_connection_state(self) -> None:
        """Update state based on disc and playback status."""
        has_disc = bool(self._current_disc)
        metadata = self._build_metadata()
        self._set_connected_or_ready(
            has_disc,
            metadata,
            {"is_playing": False, "is_buffering": False, "ready": True,
             "drive_connected": self._drive_connected, "disc_present": self._disc_present},
        )

    async def _get_status(self) -> Dict[str, Any]:
        """Get CD-specific status."""
        return self._build_metadata()

    # =========================================================================
    # PUBLIC API (for routes)
    # =========================================================================

    @property
    def data_service(self) -> CdDataService:
        return self._data_service

    @property
    def current_disc(self) -> Optional[DiscInfo]:
        return self._current_disc

    @property
    def tracks(self) -> List[TrackInfo]:
        return self._tracks

    @property
    def drive_connected(self) -> bool:
        return self._drive_connected

    @property
    def disc_present(self) -> bool:
        return self._disc_present
