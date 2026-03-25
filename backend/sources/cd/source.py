# backend/sources/cd/source.py
"""
CD audio source using mpv with cache-based architecture.

Architecture:
- Disc watcher detects disc → reads TOC, broadcasts presence (no auto-activation)
- User activates CD from dock → _do_start() reads metadata + loads cdda:// stream
- cdda:// loaded muted + playing → cache fills silently in background
- Source stays in WAITING until chapter offsets are ready (CD fully spun up)
- Disc inserted while source active → auto-plays track 1 when ready
- Source activated with disc already present → waits for user to press play
- Track navigation via set_property("chapter", N) → instant from cache (~30ms)
- Play = unmute + seek to chapter → instant (data already cached)

Key rules:
- NEVER call load_stream() except in _load_disc_stream()
- NEVER set is_playing=True except in command handlers
- Monitor tick only tracks position and detects album end
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional

from backend.config.constants import CD_DEVICE
from backend.core.models.audio_state import AudioSource, SourceState
from backend.sources.cd.data import CdDataService
from backend.sources.cd.models import DiscInfo, TrackInfo
from backend.shared.decorators import handle_errors
from backend.shared.mpv import MpvController
from backend.shared.mpv_audio_source import MpvAudioSource

logger = logging.getLogger(__name__)


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

        # Disc watcher (permanent, from initialize)
        self._disc_watcher_task: Optional[asyncio.Task] = None
        self._watcher_first_check_done = False

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
        self._is_paused = False
        self._chapter_offsets: List[float] = []
        self._stream_loaded = False
        self._cache_ready = False  # True when chapter offsets loaded = CD fully ready
        self._auto_play_on_ready = False  # True only when disc inserted while source active

    def _reset_playback_state(self) -> None:
        """Reset CD-specific playback fields (called by _do_restart)."""
        super()._reset_playback_state()
        self._current_track = None
        self._track_position = 0
        self._track_duration = 0
        self._album_finished = False
        self._is_paused = False
        self._chapter_offsets = []
        self._stream_loaded = False
        self._cache_ready = False
        self._auto_play_on_ready = False

    # =========================================================================
    # LIFECYCLE
    # =========================================================================

    async def _after_restart_restore(self) -> None:
        """Reload CD stream after restart so cache refills and monitor can proceed."""
        if self._disc_present and self._current_disc:
            await self._load_disc_stream()

    @handle_errors(default=False)
    async def initialize(self) -> bool:
        """Initialize data service and start disc watcher."""
        await self._data_service.initialize()
        self._disc_watcher_task = asyncio.create_task(self._disc_watcher_loop())
        self._logger.info("CD source initialized, disc watcher started")
        return await super().initialize()

    async def _do_start(self) -> bool:
        """Start mpv service, connect IPC, load disc if present.
        If _pre_start_service already ran, reuses the existing connection."""
        try:
            if self._mpv and self._mpv.is_connected and self._stream_loaded:
                self._logger.info("Reusing pre-started mpv connection")
            else:
                if not await self._start_service_and_wait():
                    return False
                self._mpv = MpvController(ipc_socket_path=self._mpv_socket)
                if not await self._mpv.connect():
                    self._logger.error("Failed to connect to mpv IPC")
                    return False

                if self._disc_present:
                    # Disc already in drive: user must press play (no auto-play)
                    self._auto_play_on_ready = False
                    # Metadata lookup if not already done (disc inserted while inactive)
                    if not self._current_disc and self._last_disc_id:
                        result = await self._data_service.read_disc()
                        if result:
                            disc_id, toc_string, toc_tracks = result
                            disc_info = await self._data_service.lookup_metadata(
                                disc_id, toc_string, toc_tracks
                            )
                            self._current_disc = disc_info
                            self._tracks = disc_info.tracks
                    await self._load_disc_stream()

            self._start_monitor()
            self._update_connection_state()

            return True

        except Exception as e:
            self._logger.error(f"Start failed: {e}")
            await self._cleanup()
            return False

    async def _load_disc_stream(self) -> bool:
        """Load cdda:// into mpv muted + playing. CD spins up, cache fills."""
        success = await self._mpv.load_stream(f"cdda://{CD_DEVICE}")
        if not success:
            self._logger.error("Failed to load CD stream")
            return False

        # Mute + play: CD reads at full speed, cache fills, no audio output
        await self._mpv.set_property("mute", True)
        await self._mpv.set_property("pause", False)
        self._stream_loaded = True
        self._cache_ready = False

        self._logger.info("CD stream loaded (muted+playing), cache filling")
        return True

    @handle_errors(default=False)
    async def _do_stop(self) -> bool:
        """Stop playback and service."""
        await self._cleanup()
        self._reset_playback_state()
        return await self._stop_service()

    async def _cleanup(self) -> None:
        """Clean up mpv resources. Does NOT stop disc watcher or clear disc info."""
        self._stop_monitor()
        if self._mpv:
            await self._mpv.disconnect()
            self._mpv = None
        self._is_playing = False
        self._is_paused = False
        self._is_buffering = False
        self._stream_loaded = False

    # =========================================================================
    # DISC WATCHER (runs permanently, independent of source lifecycle)
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
                await self._handle_disc_removed()
                return

        if not self._drive_connected:
            return

        was_present = self._disc_present
        self._disc_present = await asyncio.to_thread(
            self._data_service.check_disc_present
        )

        if self._disc_present and not was_present:
            if not self._watcher_first_check_done:
                self._watcher_first_check_done = True
                await self._handle_disc_detected_at_boot()
            else:
                await self._handle_disc_inserted()
        elif not self._disc_present and was_present:
            await self._handle_disc_removed()
        elif not self._watcher_first_check_done:
            self._watcher_first_check_done = True

    async def _handle_disc_detected_at_boot(self) -> None:
        """Disc already present at boot: read TOC and broadcast presence."""
        self._logger.info("Disc already present at boot")

        result = await self._data_service.read_disc()
        if not result:
            self._logger.warning("Failed to read disc TOC at boot")
            return

        disc_id, toc_string, toc_tracks = result
        self._last_disc_id = disc_id
        self._disc_present = True

        self._logger.info(f"Boot disc detected: {disc_id}, {len(toc_tracks)} tracks")

        await self.state_machine.broadcast_event(
            "system", "cd_drive_status", {
                "source": "cd",
                "drive_connected": self._drive_connected,
                "disc_present": True,
            }
        )

    async def _handle_disc_inserted(self) -> None:
        """New disc inserted: read TOC and broadcast presence.
        Does NOT auto-activate CD source — user must select it from the dock."""
        self._logger.info("Disc inserted, reading TOC...")

        result = await self._data_service.read_disc()
        if not result:
            self._logger.warning("Failed to read disc TOC")
            return

        disc_id, toc_string, toc_tracks = result

        if disc_id == self._last_disc_id:
            self._logger.info(f"Same disc re-detected: {disc_id}")
            return

        self._last_disc_id = disc_id
        self._disc_present = True
        self._logger.info(f"New disc: {disc_id}, {len(toc_tracks)} tracks")

        await self.state_machine.broadcast_event(
            "system", "cd_drive_status", {
                "source": "cd",
                "drive_connected": self._drive_connected,
                "disc_present": True,
            }
        )

        # If CD is already the active source, start loading and auto-play when ready
        if (
            self.state_machine
            and self.state_machine.system_state.active_source == AudioSource.CD
        ):
            self._auto_play_on_ready = True
            await self._pre_start_service()
            disc_info = await self._data_service.lookup_metadata(
                disc_id, toc_string, toc_tracks
            )
            self._current_disc = disc_info
            self._tracks = disc_info.tracks
            self.set_state(SourceState.WAITING, self._build_metadata())

    async def _pre_start_service(self) -> None:
        """Start milo-cd service and load stream early to keep CD spinning.
        Called from disc watcher before source activation."""
        try:
            if self._mpv and self._mpv.is_connected and self._stream_loaded:
                return  # Already pre-started

            if not await self._start_service_and_wait():
                self._logger.warning("Pre-start: service failed")
                return

            self._mpv = MpvController(ipc_socket_path=self._mpv_socket)
            if not await self._mpv.connect():
                self._logger.warning("Pre-start: mpv connect failed")
                return

            await self._load_disc_stream()
            self._logger.info("Pre-start complete: cache filling in background")

        except Exception as e:
            self._logger.warning(f"Pre-start failed: {e}")

    async def _handle_disc_removed(self) -> None:
        """Disc removal: stop playback if active, clear disc state."""
        self._logger.info("Disc removed")

        is_active = (
            self.state_machine
            and self.state_machine.system_state.active_source == AudioSource.CD
        )

        # Stop playback only if CD is the active source with mpv running
        if is_active and self._is_playing and self._mpv:
            try:
                await self._mpv.set_property("mute", True)
            except Exception as e:
                self._logger.error(f"Error muting on disc removal: {e}")

        self._reset_playback_state()
        # Clear disc-level state (not covered by _reset_playback_state)
        self._current_disc = None
        self._tracks = []
        self._last_disc_id = None
        self._disc_present = False

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
        """Play a specific track. Unmutes + seeks to chapter (instant from cache)."""
        if not self._mpv:
            return self.error_response("CD not active")

        track_number = data.get("track_number")
        if track_number is None:
            return self.error_response("track_number required")
        if not self._tracks or track_number < 1 or track_number > len(self._tracks):
            return self.error_response(f"Invalid track number: {track_number}")

        if not self._cache_ready:
            return self.error_response("CD still loading, please wait")

        # Reload stream only if album finished
        if self._album_finished or not self._stream_loaded:
            if not await self._load_disc_stream():
                return self.error_response("Failed to load CD")
            # Wait for chapter offsets after reload
            await self._read_chapter_offsets()
            if self._chapter_offsets:
                await self._recalc_track_durations()

        try:
            self._current_track = track_number
            self._track_position = 0
            self._track_duration = self._tracks[track_number - 1].duration
            self._is_playing = True
            self._is_paused = False
            self._album_finished = False

            await self._mpv.set_property("chapter", track_number - 1)
            await self._mpv.set_property("mute", False)
            await self._mpv.resume()

            # Check if mpv is still seeking (uncached track = physical CD seek)
            await asyncio.sleep(0.15)
            seeking = await self._mpv.get_property("seeking")
            self._is_buffering = bool(seeking)

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
            self._update_connection_state()
            return self.success_response("Paused")
        except Exception as e:
            return self.error_response(str(e))

    async def _handle_resume(self) -> Dict[str, Any]:
        if not self._mpv:
            return self.error_response("CD not active")
        try:
            if self._album_finished:
                return await self._handle_play_track({"track_number": 1})

            # Resume from pause — keep current position
            if self._is_paused:
                await self._mpv.resume()
                self._is_playing = True
                self._is_paused = False
                self._update_connection_state()
                return self.success_response("Resumed")

            # First play or resume from stop → go through play_track
            if not self._is_playing:
                track = self._current_track or 1
                return await self._handle_play_track({"track_number": track})

            return self.success_response("Already playing")
        except Exception as e:
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
        # Support both position (seconds) and position_ms (milliseconds, from useSourceProgress)
        position = data.get("position")
        if position is None and data.get("position_ms") is not None:
            position = data["position_ms"] / 1000
        if position is None:
            return self.error_response("position required")
        try:
            position = int(position)
            if self._chapter_offsets and self._current_track:
                chapter_idx = self._current_track - 1
                if chapter_idx < len(self._chapter_offsets):
                    absolute_pos = self._chapter_offsets[chapter_idx] + position
                    await self._mpv.seek(absolute_pos)
                    self._track_position = position
                    self._update_connection_state()
                    return self.success_response(f"Seeked to {position}s")
            return self.error_response("Chapter offsets not available")
        except Exception as e:
            return self.error_response(str(e))

    async def _handle_stop_playback(self) -> Dict[str, Any]:
        """Stop playback by muting. Keeps stream playing to preserve cache."""
        try:
            if self._mpv:
                await self._mpv.set_property("mute", True)
            self._is_playing = False
            self._is_paused = False
            self._current_track = None
            self._track_position = 0
            self._album_finished = False
            self._update_connection_state()
            return self.success_response("Playback stopped")
        except Exception as e:
            return self.error_response(str(e))

    async def _handle_eject(self) -> Dict[str, Any]:
        try:
            if self._is_playing and self._mpv:
                await self._mpv.pause()
                self._is_playing = False
                self._is_paused = False

            proc = await asyncio.create_subprocess_exec(
                "eject", CD_DEVICE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()
            if proc.returncode != 0:
                stderr = (await proc.stderr.read()).decode().strip()
                self._logger.error(f"Eject failed (rc={proc.returncode}): {stderr}")
                return self.error_response(f"Eject failed: {stderr}")
            return self.success_response("Disc ejected")
        except Exception as e:
            self._logger.error(f"Eject error: {e}")
            return self.error_response(str(e))

    # =========================================================================
    # MONITOR
    # =========================================================================

    async def _on_monitor_tick(self) -> None:
        """Track position, detect album end, and promote READY → CONNECTED."""
        if not self._current_disc or not self._stream_loaded:
            return

        # Phase 1: Wait for chapter offsets (CD spinning up)
        if not self._chapter_offsets:
            await self._read_chapter_offsets(retries=1)
            if self._chapter_offsets:
                await self._recalc_track_durations()
                self._cache_ready = True
                if self._auto_play_on_ready:
                    self._logger.info(
                        f"CD ready: {len(self._chapter_offsets)} chapters, "
                        f"auto-playing track 1"
                    )
                    await self._handle_play_track({"track_number": 1})
                else:
                    self._logger.info(
                        f"CD ready: {len(self._chapter_offsets)} chapters, "
                        f"waiting for user to press play"
                    )
                    self._update_connection_state()
            return

        # Phase 2: Track position during playback
        if not self._is_playing:
            return

        chapter = await self._mpv.get_property("chapter")
        time_pos = await self._mpv.get_property("time-pos")
        seeking = await self._mpv.get_property("seeking")

        # Album end: time-pos becomes None when mpv finishes the disc
        if (
            time_pos is None
            and self._current_track
            and self._current_track >= len(self._tracks)
        ):
            self._logger.info("Album finished")
            self._album_finished = True
            self._is_playing = False
            self._is_paused = False
            self._track_position = self._track_duration
            self._update_connection_state()
            return

        if time_pos is None or chapter is None:
            return

        # Buffering detection via mpv's seeking property
        state_changed = False
        if seeking and not self._is_buffering:
            self._is_buffering = True
            self._update_connection_state()
            return
        if not seeking and self._is_buffering:
            self._is_buffering = False
            state_changed = True

        # Track auto-advance (mpv crossed a chapter boundary naturally)
        new_track = int(chapter) + 1
        if new_track != self._current_track and 1 <= new_track <= len(self._tracks):
            self._current_track = new_track
            self._track_duration = self._tracks[new_track - 1].duration
            state_changed = True

        # Position within track
        if self._current_track:
            chapter_idx = self._current_track - 1
            if chapter_idx < len(self._chapter_offsets):
                self._track_position = max(
                    0, float(time_pos) - self._chapter_offsets[chapter_idx]
                )

        # Full broadcast on state changes (track advance, buffering transitions).
        # Position-only: lightweight sync every POSITION_SYNC_INTERVAL ticks.
        if state_changed:
            self._update_connection_state()
        elif self._position_sync_due():
            self.broadcast_position_update(
                int(self._track_position * 1000),
                int(self._track_duration * 1000),
            )

    async def _on_mpv_disconnect(self) -> None:
        self._reset_playback_state()

    # =========================================================================
    # HELPERS
    # =========================================================================

    async def _read_chapter_offsets(self, retries: int = 5) -> None:
        """Read chapter start times from mpv for position calculations."""
        for attempt in range(retries):
            chapter_list = await self._mpv.get_property("chapter-list")
            if chapter_list and isinstance(chapter_list, list) and len(chapter_list) > 0:
                self._chapter_offsets = [
                    ch.get("time", 0) for ch in chapter_list
                ]
                return
            if attempt < retries - 1:
                await asyncio.sleep(0.5)

    async def _recalc_track_durations(self) -> None:
        """Recalculate track durations from chapter offsets for accurate progress."""
        if not self._chapter_offsets or not self._tracks:
            return
        offsets = self._chapter_offsets
        for i, track in enumerate(self._tracks):
            if i + 1 < len(offsets):
                track.duration = round(offsets[i + 1] - offsets[i])
        # Last track: use total disc duration from mpv
        if self._mpv and len(self._tracks) == len(offsets):
            total = await self._mpv.get_property("duration")
            if total is not None and total > offsets[-1]:
                self._tracks[-1].duration = round(total - offsets[-1])

    def _build_metadata(self) -> Dict[str, Any]:
        metadata: Dict[str, Any] = {
            "drive_connected": self._drive_connected,
            "disc_present": self._disc_present,
            "is_playing": self._is_playing,
            "is_buffering": self._is_buffering,
            "album_finished": self._album_finished,
            "cache_ready": self._cache_ready,
        }

        if self._current_disc:
            # Default to track 1 when no track is playing
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
                    "track_position": int(self._track_position),
                    "track_duration": int(self._track_duration),
                    # Millisecond values for AudioPlayerFull / useSourceProgress
                    "position": int(self._track_position * 1000),
                    "duration": int(self._track_duration * 1000),
                })

        return metadata

    def _update_connection_state(self) -> None:
        has_disc = bool(self._current_disc)
        metadata = self._build_metadata()
        self._set_active_or_waiting(
            has_disc,
            metadata,
            {"is_playing": False, "is_buffering": False, "ready": True,
             "drive_connected": self._drive_connected, "disc_present": self._disc_present},
        )

    async def _get_status(self) -> Dict[str, Any]:
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
