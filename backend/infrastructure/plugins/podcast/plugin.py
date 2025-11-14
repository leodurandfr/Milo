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
        self.ipc_socket_path = config.get("ipc_socket", "/tmp/milo-podcast-ipc.sock")
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
                self.logger.warning(
                    f"Service {self.service_name} not found. "
                    f"Will use Radio service as fallback"
                )
                # Fallback to radio service
                self.service_name = "milo-radio.service"
                self.ipc_socket_path = "/tmp/milo-radio-ipc.sock"
                self.mpv = MpvController(self.ipc_socket_path)

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
            while not self._stopping:
                try:
                    # Check playback state
                    is_playing = await self.mpv.is_playing()

                    # Update playback position
                    if is_playing and self.current_episode:
                        position = await self.mpv.get_property("playback-time")
                        duration = await self.mpv.get_property("duration")

                        if position is not None:
                            self._current_position = int(position)

                        if duration is not None:
                            self._current_duration = int(duration)

                        # Check if state changed
                        if self._is_buffering:
                            self._is_buffering = False
                            self._is_playing = True

                            # Notify state change
                            await self.notify_state_change(
                                PluginState.CONNECTED,
                                self._build_metadata()
                            )

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
        """Save current playback progress"""
        if self.current_episode and self._current_position > 0:
            await self.podcast_data_service.update_playback_progress(
                self.current_episode['uuid'],
                self._current_position,
                self._current_duration
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
            self.logger.info(f"Playing episode: {episode_uuid}")

            # Get episode details from Taddy API
            episode = await self.taddy_api.get_episode(episode_uuid)

            if not episode:
                self.logger.error(f"Episode not found: {episode_uuid}")
                return False

            audio_url = episode.get('audio_url')
            if not audio_url:
                self.logger.error(f"No audio URL for episode: {episode_uuid}")
                return False

            # Stop current playback if any
            if self._is_playing:
                await self.mpv.stop()

            # Check for saved progress
            progress = await self.podcast_data_service.get_playback_progress(episode_uuid)
            start_position = 0
            if progress and progress.get('position', 0) > 10:  # Resume if > 10 seconds
                start_position = progress['position']
                self.logger.info(f"Resuming from {start_position}s")

            # Play episode with mpv
            success = await self.mpv.play(audio_url)

            if not success:
                self.logger.error("mpv play failed")
                return False

            # Seek to saved position if resuming
            if start_position > 0:
                await self.mpv.seek(start_position)

            # Update state
            self.current_episode = episode
            self._is_buffering = True
            self._is_playing = False
            self._current_position = start_position
            self._current_duration = episode.get('duration', 0)

            # Cache episode data
            await self.podcast_data_service.cache_episode(episode_uuid, episode)

            # Start progress save task
            if self._progress_save_task:
                self._progress_save_task.cancel()
            self._progress_save_task = asyncio.create_task(self._periodic_progress_save())

            # Notify buffering state
            await self.notify_state_change(
                PluginState.CONNECTED,
                {**self._build_metadata(), "is_buffering": True}
            )

            return True

        except Exception as e:
            self.logger.error(f"Error playing episode: {e}")
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

            await self.notify_state_change(
                PluginState.CONNECTED,
                self._build_metadata()
            )

            return True

        except Exception as e:
            self.logger.error(f"Error seeking: {e}")
            return False

    async def get_status(self) -> Dict[str, Any]:
        """Get current plugin status"""
        return {
            "state": self.get_state().value,
            "current_episode": self.current_episode,
            "is_playing": self._is_playing,
            "is_buffering": self._is_buffering,
            "position": self._current_position,
            "duration": self._current_duration,
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

            else:
                return {"success": False, "error": f"Unknown command: {command}"}

        except Exception as e:
            self.logger.error(f"Error handling command {command}: {e}")
            return {"success": False, "error": str(e)}
