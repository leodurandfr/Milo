# backend/sources/podcast/source.py
"""
Podcast audio source using MPV.

This source handles podcast playback with progress tracking, speed control,
and Taddy API integration for discovery and search.

Features:
- MPV IPC for playback control
- Progress tracking with auto-save
- Playback speed control (0.5x - 2.0x)
- Resume from last position
- TaddyAPI for podcast discovery
"""
import asyncio
from typing import Dict, Any, Optional

from backend.core.models.audio_state import SourceState
from backend.core.models.source_metadata import PlaybackMetadata
from backend.sources.podcast.data import PodcastDataService
from backend.shared.decorators import handle_errors
from backend.shared.mpv import MpvController
from backend.shared.mpv_audio_source import MpvAudioSource
from backend.sources.podcast.taddy_api import TaddyAPI

VALID_PLAYBACK_SPEEDS: list[float] = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]


class PodcastSource(MpvAudioSource):
    """
    Podcast audio source using MPV.

    Family C (active player): controlled from Milō's UI. Extends MpvAudioSource
    (BaseAudioSource subclass) — implements playback and data commands.
    """

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        state_machine=None,
        settings_service=None,
        systemd_manager=None
    ):
        super().__init__(
            source_id="podcast",
            service_name="milo-podcast.service",
            state_machine=state_machine,
            systemd_manager=systemd_manager,
            settings_service=settings_service,
            config=config
        )

        # Podcast data service - initialized immediately for routes access
        self._podcast_data = PodcastDataService(
            state_machine=state_machine
        )

        # Taddy API - load credentials from settings, initialized immediately for routes access
        taddy_user_id = (settings_service.get_setting_sync("podcast.taddy_user_id") or "") if settings_service else ""
        taddy_api_key = (settings_service.get_setting_sync("podcast.taddy_api_key") or "") if settings_service else ""
        self._taddy_api = TaddyAPI(
            user_id=taddy_user_id,
            api_key=taddy_api_key,
            cache_duration_minutes=60
        )

        # State
        self._metadata: Dict[str, Any] = {}
        self._current_episode: Optional[Dict[str, Any]] = None
        self._position = 0
        self._duration = 0
        self._playback_speed = 1.0
        self._loading = False  # Guards monitor tick during stream loading

        # Tasks
        self._progress_save_task: Optional[asyncio.Task] = None

    async def initialize(self) -> bool:
        """Pre-load podcast_data.json so a schema mismatch surfaces at boot."""
        await self._podcast_data.initialize()
        self._initialized = True
        return True

    def _reset_playback_state(self) -> None:
        super()._reset_playback_state()
        self._current_episode = None
        self._position = 0
        self._duration = 0
        self._loading = False

    async def _do_start(self) -> bool:
        """Start MPV service and initialize components."""
        try:
            # 1. Start service
            if not await self._start_service_and_wait():
                return False

            # 2. Connect to MPV IPC
            self._mpv = MpvController(ipc_socket_path=self._mpv_socket)
            if not await self._mpv.connect():
                self._logger.error("Failed to connect to MPV IPC")
                return False

            # 3. Load saved playback speed
            saved_speed = await self._podcast_data.get_setting("playback_speed", 1.0)
            self._playback_speed = saved_speed

            # 4. Reset state and load auto-stop config
            self._reset_playback_state()
            await self._load_auto_stop_config()

            # 5. Start monitor task
            self._start_monitor()

            # 6. Update state
            self._update_connection_state()

            return True

        except Exception as e:
            self._logger.error(f"Start failed: {e}")
            await self._cleanup()
            return False

    @handle_errors(default=False)
    async def _do_stop(self) -> bool:
        """Stop MPV and cleanup."""
        if self._current_episode and self._position > 0:
            await self._save_progress()

        await self._cleanup()
        return await self._stop_service()

    async def _get_status(self) -> Dict[str, Any]:
        """Get Podcast-specific status."""
        mpv_connected = self._mpv.is_connected if self._mpv else False
        mpv_playing = await self._mpv.is_playing() if self._mpv and mpv_connected else False

        return {
            "mpv_connected": mpv_connected,
            "is_playing": mpv_playing,
            "is_buffering": self._is_buffering,
            "current_episode": self._current_episode,
            "position": self._position,
            "duration": self._duration,
            "playback_speed": self._playback_speed,
            "metadata": self._metadata
        }

    async def _refresh_metadata(self) -> bool:
        """Pull live position/duration from mpv so the WebSocket initial_state
        sent to a (re)connecting client reflects the current playhead — not
        the last value broadcast via the 30s periodic sync.

        Called by state.refresh_active_metadata() from the WS handshake.
        """
        if not self._current_episode or not self._mpv or not self._mpv.is_connected:
            return False

        position = await self._mpv.get_property("playback-time")
        duration = await self._mpv.get_property("duration")
        pause_state = await self._mpv.get_property("pause")
        if position is not None:
            self._position = int(position)
        if duration is not None:
            self._duration = int(duration)
        # Sync play state from mpv too: reading position live but trusting a
        # cached _is_playing would stamp a stale flag if a reconnect races a
        # pause command still in flight. Skip while buffering (mpv reports
        # pause=False before the stream is ready).
        if pause_state is not None and not self._is_buffering:
            self._is_playing = not bool(pause_state)

        self._metadata = self._build_playback_metadata()
        return True

    async def _handle_command(self, cmd: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle Podcast-specific commands."""
        if cmd == "play_episode":
            return await self._handle_play_episode(data)

        if cmd == "pause":
            return await self._handle_pause()

        if cmd == "resume":
            return await self._handle_resume()

        if cmd == "seek":
            return await self._handle_seek(data)

        if cmd == "stop":
            return await self._handle_stop_playback()

        if cmd == "set_speed":
            return await self._handle_set_speed(data)

        return self.error_response(f"Unknown command: {cmd}")

    # === Command Handlers ===

    async def _handle_play_episode(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Play an episode."""
        episode_uuid = data.get('episode_uuid')
        if not episode_uuid:
            return self.error_response("episode_uuid required")

        try:
            self._logger.info(f"Starting playback for episode: {episode_uuid}")

            episode = await self._taddy_api.get_episode(episode_uuid)
            if not episode:
                return self.error_response(f"Episode not found: {episode_uuid}")

            audio_url = episode.get('audio_url')
            if not audio_url:
                return self.error_response(f"No audio URL for episode: {episode_uuid}")

            self._logger.info(f"Episode found: {episode.get('name', 'Unknown')}")

            # Stop current playback if any
            if self._is_playing:
                await self._save_progress()
                await self._mpv.stop()

            # Check for saved progress
            progress = await self._podcast_data.get_playback_progress(episode_uuid)
            start_position = 0
            if progress and progress.get('position', 0) > 10:  # Resume if > 10 seconds
                start_position = progress['position']
                self._logger.info(f"Resuming from {start_position}s")

            # Guard: prevent monitor tick from reading inconsistent state
            # during the async loading phase below
            self._loading = True

            # Update state BEFORE loading stream
            self._current_episode = episode
            self._is_buffering = True
            self._is_playing = False
            self._position = start_position
            self._duration = episode.get('duration', 0)

            # Notify buffering state
            self._update_connection_state()

            self._logger.info("Loading stream in mpv...")
            success = await self._mpv.load_stream(audio_url)

            if not success:
                self._loading = False
                self._is_buffering = False
                self._current_episode = None
                error_msg = "Failed to load stream"
                self.broadcast_error(error_msg)
                return self.error_response(error_msg)

            # Check if mpv is paused after loading and unpause if needed
            pause_state = await self._mpv.get_property("pause")
            if pause_state is True:
                self._logger.info("mpv is paused after load_stream, forcing unpause")
                await self._mpv.set_property("pause", False)

            # Wait for stream to be ready before seeking (if resuming)
            if start_position > 0:
                await self._wait_and_seek(start_position)

            # Apply saved playback speed
            await self._mpv.set_property("speed", self._playback_speed)

            # Mark as playing
            self._is_playing = True
            self._is_buffering = False
            self._loading = False

            self._start_progress_save()

            # Notify playing state (flips is_playing false→true after buffering).
            self._update_connection_state()

            # Clear any previous error now that playback is successful
            self.broadcast_error_cleared()

            self._logger.info("Playback started successfully")
            return self.success_response(f"Playing {episode.get('name', 'Unknown')}")

        except Exception as e:
            self._logger.error(f"Episode playback error: {e}")
            self._loading = False
            self._is_buffering = False
            self.broadcast_error(str(e))
            return self.error_response(str(e))

    async def _wait_and_seek(self, position: int) -> None:
        """Wait for stream to be ready, then seek."""
        self._logger.info(f"Waiting for stream to be seekable for resume to {position}s")

        max_wait = 10
        poll_interval = 0.2
        elapsed = 0

        while elapsed < max_wait:
            duration = await self._mpv.get_property("duration")
            if duration is not None and duration > 0:
                self._logger.info(f"Stream ready (duration={duration}s), seeking to {position}s")
                await self._mpv.seek(position)

                # Verify seek succeeded
                await asyncio.sleep(0.3)
                actual_position = await self._mpv.get_property("playback-time")
                if actual_position is not None:
                    self._logger.info(f"Seek completed, position: {int(actual_position)}s")
                return

            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        self._logger.info("Timeout waiting for stream to be ready, starting from beginning")

    async def _handle_pause(self) -> Dict[str, Any]:
        """Pause playback."""
        try:
            if self._is_playing:
                await self._mpv.pause()
                self._is_playing = False

                await self._save_progress()

                self._update_connection_state()

            return self.success_response("Paused")

        except Exception as e:
            return self.error_response(str(e))

    async def _handle_resume(self) -> Dict[str, Any]:
        """Resume playback."""
        try:
            if not self._is_playing and self._current_episode:
                await self._mpv.resume()
                self._is_playing = True

                self._update_connection_state()

            return self.success_response("Resumed")

        except Exception as e:
            return self.error_response(str(e))

    async def _handle_seek(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Seek to position. Accepts `position` (seconds) or `position_ms`.

        position_ms is the wire convention used by useSourceProgress.seekTo
        (shared with Spotify/AirPlay); `position` (seconds) is kept for the
        internal resume-seek in the /play route.
        """
        position = data.get('position')
        if position is None and data.get('position_ms') is not None:
            position = data['position_ms'] / 1000
        if position is None:
            return self.error_response("position required")

        try:
            await self._mpv.seek(int(position))
            self._position = int(position)

            # Save progress immediately after seek
            await self._save_progress()

            self._update_connection_state()

            return self.success_response(f"Seeked to {position}s")

        except Exception as e:
            return self.error_response(str(e))

    async def _auto_stop_action(self) -> None:
        """Save progress and stop playback in place after pause timeout."""
        await self._handle_stop_playback()

    async def _handle_stop_playback(self) -> Dict[str, Any]:
        """Stop playback."""
        try:
            self._loading = False
            # Stop is an explicit user action — drop any pending pause timer
            # (mpv's pause property can stay True after `stop`).
            self._handle_pause_change(False)
            if self._current_episode:
                await self._save_progress()
                await self._mpv.stop()

            self._stop_progress_save()
            self._current_episode = None
            self._is_playing = False
            self._is_buffering = False
            self._position = 0
            self._duration = 0
            self._metadata = {
                "is_playing": False,
                "is_buffering": False,
                "ready": True
            }

            self.set_state(SourceState.WAITING, self._metadata)

            return self.success_response("Playback stopped")

        except Exception as e:
            return self.error_response(str(e))

    async def _handle_set_speed(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Set playback speed."""
        speed = data.get('speed')
        if speed is None:
            return self.error_response("speed required")

        try:
            speed = float(speed)
            if speed not in VALID_PLAYBACK_SPEEDS:
                self._logger.info(f"Invalid speed {speed}, using nearest valid")
                speed = min(VALID_PLAYBACK_SPEEDS, key=lambda x: abs(x - speed))

            await self._mpv.set_property("speed", speed)
            self._playback_speed = speed

            await self._podcast_data.set_setting("playback_speed", speed)

            self._update_connection_state()

            self._logger.info(f"Playback speed set to {speed}x")
            return self.success_response(f"Speed set to {speed}x", speed=speed)

        except Exception as e:
            return self.error_response(str(e))

    # === Helpers ===

    def _build_playback_metadata(self) -> Dict[str, Any]:
        """Build metadata dict for current episode.

        position/duration are emitted in milliseconds to match the wire
        convention used by the other audio sources (Spotify, AirPlay, CD) and
        by broadcast_position_update. Internal state stays in seconds.
        """
        if not self._current_episode:
            return {}

        metadata = {
            "episode_uuid": self._current_episode.get('uuid'),
            "episode_name": self._current_episode.get('name'),
            "description": self._current_episode.get('description'),
            "image_url": self._current_episode.get('image_url'),
            "position": self._position * 1000,
            "duration": self._duration * 1000,
            "is_playing": self._is_playing,
            "is_buffering": self._is_buffering,
            "playback_speed": self._playback_speed,
            "current_episode": self._current_episode,
        }

        if 'podcast' in self._current_episode:
            metadata['podcast_name'] = self._current_episode['podcast'].get('name')
            metadata['podcast_uuid'] = self._current_episode['podcast'].get('uuid')

        return metadata

    def _update_connection_state(self) -> None:
        """Update state based on playback.

        position/duration in the metadata are in milliseconds (see
        _build_playback_metadata).
        """
        core, extras = PlaybackMetadata.split(self._build_playback_metadata())
        self.emit_connection_state(bool(self._current_episode), core, extras)

    async def _save_progress(self) -> None:
        """Save current playback progress with full metadata."""
        if self._current_episode and self._position > 0:
            podcast_info = self._current_episode.get('podcast', {})

            await self._podcast_data.update_playback_progress(
                episode_uuid=self._current_episode['uuid'],
                position=self._position,
                duration=self._duration,
                podcast_uuid=podcast_info.get('uuid', ''),
                episode_name=self._current_episode.get('name', ''),
                podcast_name=podcast_info.get('name', ''),
                image_url=self._current_episode.get('image_url', '')
            )
            self._logger.debug(
                f"Saved progress: {self._position}/{self._duration}s"
            )

    async def _cleanup(self) -> None:
        """Clean up resources.

        Note: _podcast_data and _taddy_api are NOT cleaned up here because
        they need to remain available for routes (subscriptions, search, etc.)
        even when the source is stopped.
        """
        self._stop_monitor()
        self._stop_progress_save()

        if self._mpv:
            await self._mpv.disconnect()
            self._mpv = None

        self._reset_playback_state()

    # === Monitor hooks ===

    async def _on_mpv_disconnect(self) -> None:
        """Handle unexpected mpv disconnect: save progress and clear state."""
        if self._current_episode and self._position > 0:
            await self._save_progress()
        self._is_playing = False
        self._is_buffering = False
        self._current_episode = None
        self._position = 0
        self._duration = 0
        self._metadata = {}
        self._stop_progress_save()

    async def _on_monitor_tick(self) -> None:
        """Track playback position, detect stuck state and episode completion."""
        if not self._current_episode or self._loading:
            return

        # Update playback position
        position = await self._mpv.get_property("playback-time")
        duration = await self._mpv.get_property("duration")
        pause_state = await self._mpv.get_property("pause")
        idle_active = await self._mpv.get_property("idle-active")

        if position is not None:
            new_position = int(position)
            if new_position != self._position:
                self._position = new_position

        # Edge-trigger: Taddy may return duration=null (→ self._duration is
        # initialized to 0 in _handle_play_episode). Once mpv reports the real
        # duration, broadcast immediately so the frontend ProgressBar appears
        # without waiting up to POSITION_SYNC_INTERVAL seconds for the next
        # periodic sync.
        duration_just_known = False
        if duration is not None:
            new_duration = int(duration)
            duration_just_known = self._duration == 0 and new_duration > 0
            self._duration = new_duration

        if duration_just_known:
            self.broadcast_position_update(
                self._position * 1000, self._duration * 1000
            )

        # Only broadcast position periodically (not every tick). Skip when the
        # eager duration push already covered this tick to avoid a redundant
        # paired broadcast on the rare overlap.
        if (
            self._is_playing
            and self._position_sync_due()
            and not duration_just_known
        ):
            self.broadcast_position_update(
                self._position * 1000, self._duration * 1000
            )

        # Auto-stop: arm/cancel timer based on the pause we already polled.
        if pause_state is not None:
            self._handle_pause_change(bool(pause_state))

        # Detect stuck at position 0 with pause=True
        if self._is_playing and position == 0.0 and pause_state is True:
            self._logger.info("Stuck at 0.0 with pause=True, forcing unpause")
            await self._mpv.set_property("pause", False)

        # Episode finished: mpv unloaded the file (keep-open=no) and returned to
        # idle. `idle-active` is the authoritative EOF signal — unlike a
        # position-vs-duration heuristic it doesn't depend on accurate podcast
        # duration metadata (frequently over-reported for VBR MP3 streams, which
        # would otherwise leave the player stuck on the last frame), and it stays
        # False during mid-stream cache stalls where playback-time momentarily
        # reads None.
        if self._is_playing and idle_active is True:
            self._logger.info("Episode finished (mpv idle)")

            finished_uuid = self._current_episode['uuid']

            # Persist completion so the episode shows "already listened" and drops
            # out of the in-progress queue. Write a final row first (covers a short
            # clip that never hit a periodic save), then force completed: _save_progress
            # routes through the position>=duration-30 heuristic, which yields False
            # when mpv over-reports VBR duration (see above) — the explicit mark is
            # what actually guarantees completion. Wrapped so a persistence error
            # can't strand the source as "playing".
            try:
                await self._save_progress()
                await self._podcast_data.mark_episode_completed(finished_uuid)
            except Exception as e:
                self._logger.error(f"Failed to persist episode completion: {e}")

            self._stop_progress_save()
            self._current_episode = None
            self._is_playing = False
            self._is_buffering = False
            self._position = 0
            self._duration = 0

            # WAITING metadata: {episode_ended: bool, episode_uuid: str, completed: bool}.
            # episode_uuid + completed let the frontend flip the just-finished card to
            # "already listened" reactively, without a re-fetch.
            self.set_state(
                SourceState.WAITING,
                {"episode_ended": True, "episode_uuid": finished_uuid, "completed": True}
            )

    # === Progress Save ===

    def _start_progress_save(self) -> None:
        """Start periodic progress save task."""
        if self._progress_save_task:
            self._progress_save_task.cancel()
        self._progress_save_task = asyncio.create_task(self._progress_save_loop())

    def _stop_progress_save(self) -> None:
        """Stop periodic progress save task."""
        if self._progress_save_task:
            self._progress_save_task.cancel()
            self._progress_save_task = None

    async def _progress_save_loop(self) -> None:
        """Periodically save playback progress (every 10 seconds)."""
        try:
            while True:
                await asyncio.sleep(10)
                try:
                    if self._is_playing and self._current_episode:
                        await self._save_progress()
                except Exception as e:
                    # Loop body try/except keeps the task alive on transient I/O
                    # errors (disk full, lock contention) — otherwise a single
                    # _save_progress failure would silently kill periodic saves
                    # until the next backend restart.
                    self._logger.error(f"Progress save failed (loop continues): {e}")

        except asyncio.CancelledError:
            self._logger.debug("Progress save task cancelled")

    # === Public API ===

    async def reload_credentials(self, user_id: str, api_key: str) -> bool:
        """Hot-reload Taddy API credentials without restarting the source."""
        self._taddy_api.user_id = user_id
        self._taddy_api.api_key = api_key
        # Close existing session so _ensure_session() recreates it with new headers
        await self._taddy_api.close()
        # Clear caches that may contain error responses from old credentials
        self._taddy_api.clear_cache()
        self._logger.info("Taddy API credentials reloaded")
        return True

    @property
    def podcast_data(self) -> Optional[PodcastDataService]:
        """Get podcast data service."""
        return self._podcast_data

    @property
    def taddy_api(self) -> Optional[TaddyAPI]:
        """Get Taddy API client."""
        return self._taddy_api

    @property
    def position(self) -> int:
        """Get current position in seconds."""
        return self._position

    @property
    def duration(self) -> int:
        """Get episode duration in seconds."""
        return self._duration

    @property
    def playback_speed(self) -> float:
        """Get current playback speed."""
        return self._playback_speed

