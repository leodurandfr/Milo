"""
Podcast plugin for Milo - Podcast playback via Taddy API and mpv
"""
import asyncio
import logging
from typing import Dict, Any, Optional

from backend.infrastructure.plugins.base import UnifiedAudioPlugin
from backend.domain.audio_state import PluginState
from backend.infrastructure.plugins.radio.mpv_controller import MpvController
from backend.infrastructure.plugins.podcast.taddy_api import TaddyAPI
from backend.infrastructure.services.podcast_data_service import PodcastDataService


class PodcastPlugin(UnifiedAudioPlugin):
    """
    Podcast plugin for Milo

    Follows the pattern of Radio plugin:
    - Controls mpv for audio playback
    - Manages podcast subscriptions and favorites
    - Tracks playback progress for resume functionality

    States:
        INACTIVE → service stopped
        READY → service started (mpv in idle)
        CONNECTED → episode playing
    """

    def __init__(self, config: Dict[str, Any], state_machine=None, settings_service=None):
        super().__init__("podcast", state_machine)

        self.config = config
        self.service_name = config.get("service_name", "milo-podcast.service")
        self.ipc_socket_path = config.get("ipc_socket", "/run/milo/podcast-ipc.sock")
        self.settings_service = settings_service

        # Taddy API credentials
        taddy_user_id = config.get("taddy_user_id", "3671")
        taddy_api_key = config.get("taddy_api_key", "")

        # Create services
        self.podcast_data_service = PodcastDataService()
        self.taddy_api = TaddyAPI(
            user_id=taddy_user_id,
            api_key=taddy_api_key,
            cache_duration_minutes=60
        )

        # mpv controller (reuse radio's mpv controller)
        self.mpv = MpvController(self.ipc_socket_path)

        # Current state
        self.current_episode: Optional[Dict[str, Any]] = None
        self._is_playing = False
        self._is_buffering = False
        self._metadata = {}
        self._current_position = 0  # Current playback position in seconds
        self._current_duration = 0  # Total duration in seconds
        self._playback_speed = 1.0  # Playback speed (0.5x - 2x)

        # Monitoring task
        self._monitor_task: Optional[asyncio.Task] = None
        self._stopping = False

        # Progress save task
        self._progress_save_task: Optional[asyncio.Task] = None

    async def _do_initialize(self) -> bool:
        """Podcast plugin initialization"""
        try:
            # Check that service exists
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "list-unit-files", self.service_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()

            if proc.returncode != 0 or self.service_name not in stdout.decode():
                raise RuntimeError(
                    f"Service {self.service_name} not found. "
                    f"Please create the systemd service for podcast playback."
                )

            # Check that mpv is installed
            proc = await asyncio.create_subprocess_exec(
                "which", "mpv",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.wait()

            if proc.returncode != 0:
                raise RuntimeError("mpv is not installed")

            self.logger.info("Podcast plugin initialized")
            return True

        except Exception as e:
            self.logger.error(f"Podcast initialization error: {e}")
            return False

    async def _do_start(self) -> bool:
        """Starting Podcast service"""
        try:
            # Start systemd service (mpv)
            if not await self.control_service(self.service_name, "start"):
                return False

            # Wait for service to be ready
            await asyncio.sleep(1)

            # Check that service is active
            is_active = await self.service_manager.is_active(self.service_name)
            if not is_active:
                self.logger.error("mpv service started but not active")
                return False

            # Connect to mpv IPC socket
            if not await self.mpv.connect(max_retries=10, retry_delay=0.5):
                self.logger.error("Unable to connect to mpv IPC socket")
                return False

            # Start mpv state monitoring
            self._stopping = False
            self._monitor_task = asyncio.create_task(self._monitor_playback())

            # Notify READY state
            await self.notify_state_change(PluginState.READY, {
                "ready": True,
                "mpv_connected": self.mpv.is_connected
            })

            self.logger.info("Podcast service started and ready")
            return True

        except Exception as e:
            self.logger.error(f"Podcast start error: {e}")
            return False

    async def stop(self) -> bool:
        """Stops Podcast plugin"""
        try:
            self.logger.info("Stopping Podcast plugin")

            # Stop monitoring
            self._stopping = True
            if self._monitor_task:
                self._monitor_task.cancel()
                try:
                    await self._monitor_task
                except asyncio.CancelledError:
                    pass

            # Stop progress save task
            if self._progress_save_task:
                self._progress_save_task.cancel()
                try:
                    await self._progress_save_task
                except asyncio.CancelledError:
                    pass

            # Save current progress before stopping
            if self.current_episode and self._current_position > 0:
                await self._save_progress()

            # Stop playback
            if self._is_playing:
                await self.mpv.stop()

            # Disconnect mpv
            await self.mpv.disconnect()

            # Close Taddy API
            await self.taddy_api.close()

            # Stop service (if not radio fallback)
            if self.service_name != "milo-radio.service":
                await self.control_service(self.service_name, "stop")

            # Reset state
            self.current_episode = None
            self._is_playing = False
            self._is_buffering = False
            self._metadata = {}
            self._current_position = 0
            self._current_duration = 0

            await self.notify_state_change(PluginState.INACTIVE)
            self.logger.info("Podcast plugin stopped")
            return True

        except Exception as e:
            self.logger.error(f"Podcast stop error: {e}")
            return False

    async def _monitor_playback(self) -> None:
        """
        Monitors mpv playback state and saves progress periodically
        """
        try:
            self.logger.info("[MONITOR] Playback monitoring started")
            while not self._stopping:
                try:
                    # Check playback state
                    is_playing = await self.mpv.is_playing()

                    # Update playback position (even during buffering to get initial duration)
                    if self.current_episode:
                        position = await self.mpv.get_property("playback-time")
                        duration = await self.mpv.get_property("duration")
                        pause_state = await self.mpv.get_property("pause")

                        position_changed = False
                        if position is not None:
                            new_position = int(position)
                            if new_position != self._current_position:
                                self._current_position = new_position
                                position_changed = True

                        if duration is not None:
                            self._current_duration = int(duration)

                        # Check if state changed from buffering to playing
                        if self._is_buffering and is_playing:
                            self.logger.info(f"[MONITOR] Buffering → Playing transition detected at {self._current_position}s")
                            self._is_buffering = False
                            self._is_playing = True

                            # Notify state change
                            await self.notify_state_change(
                                PluginState.CONNECTED,
                                self._build_metadata()
                            )
                        # Broadcast position updates during playback
                        elif self._is_playing and is_playing and position_changed:
                            await self.notify_state_change(
                                PluginState.CONNECTED,
                                self._build_metadata()
                            )
                        # Detect stuck state: is_playing=True but mpv not actually playing
                        elif self._is_playing and not is_playing and not self._is_buffering:
                            self.logger.warning(f"[MONITOR] WARNING: is_playing=True but mpv.is_playing()=False! Position: {position}, Duration: {duration}")

                        # Detect stuck at position 0 with pause=True
                        if self._is_playing and is_playing and position == 0.0 and pause_state is True:
                            self.logger.warning(f"[MONITOR] WARNING: Stuck at 0.0 with pause=True! Forcing unpause...")
                            await self.mpv.set_property("pause", False)

                    # Check if episode ended
                    if (self._is_playing and not is_playing and
                        self.current_episode and
                        self._current_duration > 0 and
                        self._current_position >= self._current_duration - 5):  # Within 5 seconds of end

                        self.logger.info("Episode finished")

                        # Clear progress (mark as completed)
                        await self.podcast_data_service.clear_playback_progress(
                            self.current_episode['uuid']
                        )

                        # Notify episode end
                        await self.notify_state_change(
                            PluginState.READY,
                            {"episode_ended": True}
                        )

                        self.current_episode = None
                        self._is_playing = False
                        self._current_position = 0
                        self._current_duration = 0

                    await asyncio.sleep(1)  # Check every second

                except Exception as e:
                    self.logger.error(f"Error in playback monitor: {e}")
                    await asyncio.sleep(2)

        except asyncio.CancelledError:
            self.logger.debug("Playback monitor cancelled")

    async def _save_progress(self) -> None:
        """Save current playback progress with full metadata"""
        if self.current_episode and self._current_position > 0:
            # Extract podcast info
            podcast_info = self.current_episode.get('podcast', {})

            await self.podcast_data_service.update_playback_progress(
                episode_uuid=self.current_episode['uuid'],
                position=self._current_position,
                duration=self._current_duration,
                podcast_uuid=podcast_info.get('uuid', ''),
                episode_name=self.current_episode.get('name', ''),
                podcast_name=podcast_info.get('name', ''),
                image_url=self.current_episode.get('image_url', '')
            )
            self.logger.debug(
                f"Saved progress: {self._current_position}/{self._current_duration}s"
            )

    async def _periodic_progress_save(self) -> None:
        """Periodically save playback progress (every 10 seconds)"""
        try:
            while not self._stopping:
                await asyncio.sleep(10)
                if self._is_playing and self.current_episode:
                    await self._save_progress()

        except asyncio.CancelledError:
            self.logger.debug("Progress save task cancelled")

    def _build_metadata(self) -> Dict[str, Any]:
        """Build metadata dict for state notifications"""
        if not self.current_episode:
            return {}

        metadata = {
            "episode_uuid": self.current_episode.get('uuid'),
            "episode_name": self.current_episode.get('name'),
            "description": self.current_episode.get('description'),
            "image_url": self.current_episode.get('image_url'),
            "position": self._current_position,
            "duration": self._current_duration,
            "is_playing": self._is_playing,
            "is_buffering": self._is_buffering,
            "playback_speed": self._playback_speed,
            # Include full episode object for frontend store
            "current_episode": self.current_episode,
        }

        # Add podcast info if available
        if 'podcast' in self.current_episode:
            metadata['podcast_name'] = self.current_episode['podcast'].get('name')
            metadata['podcast_uuid'] = self.current_episode['podcast'].get('uuid')

        return metadata

    async def play_episode(self, episode_uuid: str) -> bool:
        """
        Play an episode

        Args:
            episode_uuid: Episode UUID

        Returns:
            True if successful
        """
        try:
            self.logger.info(f"[PLAY] Starting playback for episode: {episode_uuid}")
            self.logger.info(f"[PLAY] Initial state - is_playing: {self._is_playing}, is_buffering: {self._is_buffering}, plugin_state: {self.current_state}")

            # Get episode details from Taddy API
            episode = await self.taddy_api.get_episode(episode_uuid)

            if not episode:
                self.logger.error(f"[PLAY] Episode not found: {episode_uuid}")
                return False

            audio_url = episode.get('audio_url')
            if not audio_url:
                self.logger.error(f"[PLAY] No audio URL for episode: {episode_uuid}")
                return False

            self.logger.info(f"[PLAY] Episode found: {episode.get('name', 'Unknown')}, audio_url: {audio_url[:100]}...")

            # Stop current playback if any
            if self._is_playing:
                self.logger.info(f"[PLAY] Stopping current playback")
                # Save progress before stopping to preserve position when switching episodes
                await self._save_progress()
                await self.mpv.stop()

            # Check for saved progress
            progress = await self.podcast_data_service.get_playback_progress(episode_uuid)
            start_position = 0
            if progress and progress.get('position', 0) > 10:  # Resume if > 10 seconds
                start_position = progress['position']
                self.logger.info(f"[PLAY] Resuming from {start_position}s")

            # Update state BEFORE loading stream to prevent race condition
            self.current_episode = episode
            self._is_buffering = True
            self._is_playing = False
            self._current_position = start_position
            self._current_duration = episode.get('duration', 0)

            self.logger.info(f"[PLAY] State updated before load - is_playing: False, is_buffering: True, duration: {self._current_duration}")

            # Play episode with mpv
            self.logger.info(f"[PLAY] Calling mpv.load_stream()...")
            success = await self.mpv.load_stream(audio_url)
            self.logger.info(f"[PLAY] mpv.load_stream() returned: {success}")

            if not success:
                self.logger.error("[PLAY] mpv load_stream failed")
                return False

            # Check if mpv is paused after loading and unpause if needed
            pause_state = await self.mpv.get_property("pause")
            if pause_state is True:
                self.logger.warning(f"[PLAY] mpv is paused after load_stream! Forcing unpause...")
                await self.mpv.set_property("pause", False)

            # Wait for stream to be ready before seeking (if resuming from saved position)
            if start_position > 0:
                self.logger.info(f"[PLAY] Need to resume from {start_position}s, waiting for stream to be seekable...")

                # Poll for duration to become available (indicates stream is ready/seekable)
                max_wait = 10  # seconds
                poll_interval = 0.2  # seconds
                elapsed = 0
                stream_ready = False

                while elapsed < max_wait:
                    duration = await self.mpv.get_property("duration")
                    if duration is not None and duration > 0:
                        stream_ready = True
                        self.logger.info(f"[PLAY] Stream ready (duration={duration}s), seeking to {start_position}s")
                        await self.mpv.seek(start_position)

                        # Verify seek succeeded
                        await asyncio.sleep(0.3)
                        actual_position = await self.mpv.get_property("playback-time")
                        if actual_position is not None:
                            self.logger.info(f"[PLAY] Seek completed successfully, position: {int(actual_position)}s")
                        else:
                            self.logger.warning(f"[PLAY] Seek may have failed, position unavailable")
                        break

                    await asyncio.sleep(poll_interval)
                    elapsed += poll_interval

                if not stream_ready:
                    self.logger.warning(f"[PLAY] Timeout waiting for stream to be ready after {max_wait}s, starting from beginning")

            # Mark as playing immediately to prevent race condition
            # Keep buffering=True until monitor loop confirms playback started
            # This allows UI to show loading state while ensuring is_playing is correct
            self._is_playing = True
            # _is_buffering stays True, monitor loop will set it to False once playing

            self.logger.info(f"[PLAY] State updated after load - is_playing: True, is_buffering: True")

            # Cache episode data
            await self.podcast_data_service.cache_episode(episode_uuid, episode)

            # Start progress save task
            if self._progress_save_task:
                self._progress_save_task.cancel()
            self._progress_save_task = asyncio.create_task(self._periodic_progress_save())

            # Notify playing state with buffering indicator
            self.logger.info(f"[PLAY] Broadcasting state change to CONNECTED")
            await self.notify_state_change(
                PluginState.CONNECTED,
                self._build_metadata()
            )

            self.logger.info(f"[PLAY] Playback started successfully - waiting for monitor loop to confirm")

            return True

        except Exception as e:
            self.logger.error(f"[PLAY] Error playing episode: {e}", exc_info=True)
            return False

    async def pause(self) -> bool:
        """Pause playback"""
        try:
            if self._is_playing:
                await self.mpv.pause()
                self._is_playing = False

                # Save progress
                await self._save_progress()

                await self.notify_state_change(
                    PluginState.CONNECTED,
                    {**self._build_metadata(), "is_playing": False}
                )

            return True

        except Exception as e:
            self.logger.error(f"Error pausing: {e}")
            return False

    async def resume(self) -> bool:
        """Resume playback"""
        try:
            if not self._is_playing and self.current_episode:
                await self.mpv.resume()
                self._is_playing = True

                await self.notify_state_change(
                    PluginState.CONNECTED,
                    {**self._build_metadata(), "is_playing": True}
                )

            return True

        except Exception as e:
            self.logger.error(f"Error resuming: {e}")
            return False

    async def seek(self, position: int) -> bool:
        """
        Seek to position

        Args:
            position: Position in seconds

        Returns:
            True if successful
        """
        try:
            await self.mpv.seek(position)
            self._current_position = position

            # Save progress immediately after seek to prevent loss on episode change
            await self._save_progress()

            await self.notify_state_change(
                PluginState.CONNECTED,
                self._build_metadata()
            )

            return True

        except Exception as e:
            self.logger.error(f"Error seeking: {e}")
            return False

    async def set_speed(self, speed: float) -> bool:
        """
        Set playback speed

        Args:
            speed: Speed multiplier (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)

        Returns:
            True if successful
        """
        try:
            # Validate speed
            valid_speeds = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
            if speed not in valid_speeds:
                self.logger.warning(f"Invalid speed {speed}, using nearest valid")
                speed = min(valid_speeds, key=lambda x: abs(x - speed))

            # Set mpv speed property
            await self.mpv.set_property("speed", speed)
            self._playback_speed = speed

            # Save speed preference
            await self.podcast_data_service.set_setting("playbackSpeed", speed)

            await self.notify_state_change(
                PluginState.CONNECTED,
                self._build_metadata()
            )

            self.logger.info(f"Playback speed set to {speed}x")
            return True

        except Exception as e:
            self.logger.error(f"Error setting speed: {e}")
            return False

    async def get_status(self) -> Dict[str, Any]:
        """Get current plugin status"""
        return {
            "state": self.current_state.value,
            "current_episode": self.current_episode,
            "is_playing": self._is_playing,
            "is_buffering": self._is_buffering,
            "position": self._current_position,
            "duration": self._current_duration,
            "playback_speed": self._playback_speed,
            "metadata": self._metadata
        }

    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle plugin-specific commands

        Commands:
            - play_episode: Play an episode
            - pause: Pause playback
            - resume: Resume playback
            - seek: Seek to position
            - stop: Stop playback
        """
        try:
            if command == "play_episode":
                episode_uuid = data.get("episode_uuid")
                if not episode_uuid:
                    return {"success": False, "error": "episode_uuid required"}

                success = await self.play_episode(episode_uuid)
                return {"success": success}

            elif command == "pause":
                success = await self.pause()
                return {"success": success}

            elif command == "resume":
                success = await self.resume()
                return {"success": success}

            elif command == "seek":
                position = data.get("position")
                if position is None:
                    return {"success": False, "error": "position required"}

                success = await self.seek(int(position))
                return {"success": success}

            elif command == "stop":
                if self.current_episode:
                    await self._save_progress()
                    await self.mpv.stop()
                    self.current_episode = None
                    self._is_playing = False
                    self._is_buffering = False

                    await self.notify_state_change(PluginState.READY, {})

                return {"success": True}

            elif command == "set_speed":
                speed = data.get("speed")
                if speed is None:
                    return {"success": False, "error": "speed required"}

                success = await self.set_speed(float(speed))
                return {"success": success, "speed": self._playback_speed}

            else:
                return {"success": False, "error": f"Unknown command: {command}"}

        except Exception as e:
            self.logger.error(f"Error handling command {command}: {e}")
            return {"success": False, "error": str(e)}
