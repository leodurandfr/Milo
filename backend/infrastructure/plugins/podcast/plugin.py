# backend/infrastructure/plugins/podcast/plugin.py
"""
Podcast plugin for Milo - Podcast playback via Taddy API and mpv
"""
import asyncio
from typing import Dict, Any, Optional

from backend.infrastructure.plugins.media_base import BaseMediaPlugin
from backend.domain.audio_state import AudioSource, PluginState
from backend.infrastructure.plugins.podcast.taddy_api import TaddyAPI
from backend.infrastructure.services.podcast_data_service import PodcastDataService


class PodcastPlugin(BaseMediaPlugin):
    """
    Podcast plugin for Milo

    Extends BaseMediaPlugin with podcast-specific functionality:
    - Taddy API integration for podcast discovery
    - Playback progress tracking and resume
    - Playback speed control (0.5x - 2.0x)
    - Subscription management

    States:
        STARTING → service starting
        READY → service started (mpv in idle)
        CONNECTED → episode playing
        ERROR → service error
    """

    def __init__(self, config: Dict[str, Any], state_machine=None, settings_service=None):
        super().__init__(
            source=AudioSource.PODCAST,
            config=config,
            state_machine=state_machine,
            settings_service=settings_service,
            default_ipc_socket="/run/milo/podcast-ipc.sock"
        )

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

        # Current episode state
        self.current_episode: Optional[Dict[str, Any]] = None
        self._current_position = 0  # Current playback position in seconds
        self._current_duration = 0  # Total duration in seconds
        self._playback_speed = 1.0  # Playback speed (0.5x - 2x)

        # Progress save task
        self._progress_save_task: Optional[asyncio.Task] = None

    def _get_monitor_interval(self) -> float:
        """Podcast needs faster monitoring for position updates."""
        return 1.0  # Check every second

    async def _cleanup_resources(self) -> None:
        """Clean up podcast-specific resources."""
        # Stop progress save task
        if self._progress_save_task:
            self._progress_save_task.cancel()
            try:
                await self._progress_save_task
            except asyncio.CancelledError:
                pass
            self._progress_save_task = None

        # Save current progress before stopping
        if self.current_episode and self._current_position > 0:
            await self._save_progress()

        # Close Taddy API
        if self.taddy_api:
            await self.taddy_api.close()

    async def _save_state_before_restart(self) -> None:
        """Save progress before restart."""
        if self.current_episode and self._current_position > 0:
            await self._save_progress()

    async def _reset_playback_state(self) -> None:
        """Reset podcast-specific playback state."""
        self.current_episode = None
        self._current_position = 0
        self._current_duration = 0

        # Stop progress save task
        if self._progress_save_task:
            self._progress_save_task.cancel()
            try:
                await self._progress_save_task
            except asyncio.CancelledError:
                pass
            self._progress_save_task = None

    def _build_playback_metadata(self) -> Dict[str, Any]:
        """Build metadata dict for current episode."""
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

    async def _on_playback_state_changed(
        self, is_playing: bool, was_playing: bool
    ) -> None:
        """Handle playback state changes for podcast."""
        if self.current_episode:
            self._metadata = self._build_playback_metadata()
            await self.notify_state_change(PluginState.CONNECTED, self._metadata)

    async def _monitor_update(self) -> None:
        """Periodic monitoring update for podcast - handles position tracking and end detection."""
        if not self.current_episode:
            return

        # Update playback position
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

        # Broadcast position updates during playback
        if self._is_playing and position_changed:
            self._metadata = self._build_playback_metadata()
            await self.notify_state_change(PluginState.CONNECTED, self._metadata)

        # Detect stuck at position 0 with pause=True
        if self._is_playing and position == 0.0 and pause_state is True:
            self.logger.warning("Stuck at 0.0 with pause=True! Forcing unpause...")
            await self.mpv.set_property("pause", False)

        # Check if episode ended
        is_playing = await self.mpv.is_playing()
        if (self._is_playing and not is_playing and
            self._current_duration > 0 and
            self._current_position >= self._current_duration - 5):  # Within 5 seconds of end

            self.logger.info("Episode finished")

            # Clear progress (mark as completed)
            await self.podcast_data_service.clear_playback_progress(
                self.current_episode['uuid']
            )

            # Reset state before notifying
            self.current_episode = None
            self._is_playing = False
            self._current_position = 0
            self._current_duration = 0

            # Notify episode end
            await self.notify_state_change(
                PluginState.READY,
                {"episode_ended": True}
            )

    async def _save_progress(self) -> None:
        """Save current playback progress with full metadata."""
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
        """Periodically save playback progress (every 10 seconds)."""
        try:
            while not self._stopping:
                await asyncio.sleep(10)
                if self._is_playing and self.current_episode:
                    await self._save_progress()

        except asyncio.CancelledError:
            self.logger.debug("Progress save task cancelled")

    async def play_episode(self, episode_uuid: str) -> bool:
        """
        Play an episode

        Args:
            episode_uuid: Episode UUID

        Returns:
            True if successful
        """
        try:
            self.logger.info(f"Starting playback for episode: {episode_uuid}")

            # Get episode details from Taddy API
            episode = await self.taddy_api.get_episode(episode_uuid)

            if not episode:
                self.logger.error(f"Episode not found: {episode_uuid}")
                return False

            audio_url = episode.get('audio_url')
            if not audio_url:
                self.logger.error(f"No audio URL for episode: {episode_uuid}")
                return False

            self.logger.info(f"Episode found: {episode.get('name', 'Unknown')}")

            # Stop current playback if any
            if self._is_playing:
                self.logger.info("Stopping current playback")
                # Save progress before stopping to preserve position when switching episodes
                await self._save_progress()
                await self.mpv.stop()

            # Check for saved progress
            progress = await self.podcast_data_service.get_playback_progress(episode_uuid)
            start_position = 0
            if progress and progress.get('position', 0) > 10:  # Resume if > 10 seconds
                start_position = progress['position']
                self.logger.info(f"Resuming from {start_position}s")

            # Update state BEFORE loading stream to prevent race condition
            self.current_episode = episode
            self._is_buffering = True
            self._is_playing = False
            self._current_position = start_position
            self._current_duration = episode.get('duration', 0)

            # Play episode with mpv
            self.logger.info("Loading stream in mpv...")
            success = await self.mpv.load_stream(audio_url)

            if not success:
                self.logger.error("mpv load_stream failed")
                return False

            # Check if mpv is paused after loading and unpause if needed
            pause_state = await self.mpv.get_property("pause")
            if pause_state is True:
                self.logger.warning("mpv is paused after load_stream! Forcing unpause...")
                await self.mpv.set_property("pause", False)

            # Wait for stream to be ready before seeking (if resuming from saved position)
            if start_position > 0:
                self.logger.info(f"Waiting for stream to be seekable for resume to {start_position}s")

                # Poll for duration to become available (indicates stream is ready/seekable)
                max_wait = 10  # seconds
                poll_interval = 0.2  # seconds
                elapsed = 0
                stream_ready = False

                while elapsed < max_wait:
                    duration = await self.mpv.get_property("duration")
                    if duration is not None and duration > 0:
                        stream_ready = True
                        self.logger.info(f"Stream ready (duration={duration}s), seeking to {start_position}s")
                        await self.mpv.seek(start_position)

                        # Verify seek succeeded
                        await asyncio.sleep(0.3)
                        actual_position = await self.mpv.get_property("playback-time")
                        if actual_position is not None:
                            self.logger.info(f"Seek completed, position: {int(actual_position)}s")
                        break

                    await asyncio.sleep(poll_interval)
                    elapsed += poll_interval

                if not stream_ready:
                    self.logger.warning(f"Timeout waiting for stream to be ready, starting from beginning")

            # Mark as playing (buffering will be cleared by monitor loop)
            self._is_playing = True

            # Cache episode data
            await self.podcast_data_service.cache_episode(episode_uuid, episode)

            # Start progress save task
            if self._progress_save_task:
                self._progress_save_task.cancel()
            self._progress_save_task = asyncio.create_task(self._periodic_progress_save())

            # Notify playing state with buffering indicator
            self._metadata = self._build_playback_metadata()
            await self.notify_state_change(PluginState.CONNECTED, self._metadata)

            self.logger.info("Playback started successfully")
            return True

        except Exception as e:
            self.logger.error(f"Error playing episode: {e}", exc_info=True)
            return False

    async def pause(self) -> bool:
        """Pause playback."""
        try:
            if self._is_playing:
                await self.mpv.pause()
                self._is_playing = False

                # Save progress
                await self._save_progress()

                self._metadata = self._build_playback_metadata()
                await self.notify_state_change(PluginState.CONNECTED, self._metadata)

            return True

        except Exception as e:
            self.logger.error(f"Error pausing: {e}")
            return False

    async def resume(self) -> bool:
        """Resume playback."""
        try:
            if not self._is_playing and self.current_episode:
                await self.mpv.resume()
                self._is_playing = True

                self._metadata = self._build_playback_metadata()
                await self.notify_state_change(PluginState.CONNECTED, self._metadata)

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

            # Save progress immediately after seek
            await self._save_progress()

            self._metadata = self._build_playback_metadata()
            await self.notify_state_change(PluginState.CONNECTED, self._metadata)

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

            self._metadata = self._build_playback_metadata()
            await self.notify_state_change(PluginState.CONNECTED, self._metadata)

            self.logger.info(f"Playback speed set to {speed}x")
            return True

        except Exception as e:
            self.logger.error(f"Error setting speed: {e}")
            return False

    async def reload_credentials(self, user_id: str, api_key: str) -> bool:
        """
        Reload Taddy API credentials without restarting the plugin

        Args:
            user_id: New Taddy API user ID
            api_key: New Taddy API key

        Returns:
            True if credentials reloaded successfully
        """
        try:
            self.logger.info("Reloading Taddy API credentials")

            # Close old TaddyAPI instance
            if self.taddy_api:
                await self.taddy_api.close()

            # Create new TaddyAPI instance with new credentials
            self.taddy_api = TaddyAPI(
                user_id=user_id,
                api_key=api_key,
                cache_duration_minutes=60
            )

            self.logger.info("Taddy API credentials reloaded successfully")
            return True

        except Exception as e:
            self.logger.error(f"Failed to reload Taddy credentials: {e}")
            return False

    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle plugin-specific commands

        Commands:
            - play_episode: Play an episode
            - pause: Pause playback
            - resume: Resume playback
            - seek: Seek to position
            - stop: Stop playback
            - set_speed: Set playback speed
        """
        try:
            if command == "play_episode":
                episode_uuid = data.get("episode_uuid")
                if not episode_uuid:
                    return self.format_response(False, error="episode_uuid required")

                success = await self.play_episode(episode_uuid)
                return self.format_response(success)

            elif command == "pause":
                success = await self.pause()
                return self.format_response(success)

            elif command == "resume":
                success = await self.resume()
                return self.format_response(success)

            elif command == "seek":
                position = data.get("position")
                if position is None:
                    return self.format_response(False, error="position required")

                success = await self.seek(int(position))
                return self.format_response(success)

            elif command == "stop":
                if self.current_episode:
                    await self._save_progress()
                    await self.mpv.stop()
                    self.current_episode = None
                    self._is_playing = False
                    self._is_buffering = False

                    await self.notify_state_change(PluginState.READY, {})

                return self.format_response(True)

            elif command == "set_speed":
                speed = data.get("speed")
                if speed is None:
                    return self.format_response(False, error="speed required")

                success = await self.set_speed(float(speed))
                return self.format_response(success, speed=self._playback_speed)

            else:
                return self.format_response(False, error=f"Unknown command: {command}")

        except Exception as e:
            self.logger.error(f"Error handling command {command}: {e}")
            return self.format_response(False, error=str(e))

    async def get_status(self) -> Dict[str, Any]:
        """Get current plugin status."""
        try:
            # Get base status
            base_status = await super().get_status()

            return {
                **base_status,
                "state": self.current_state.value,
                "current_episode": self.current_episode,
                "position": self._current_position,
                "duration": self._current_duration,
                "playback_speed": self._playback_speed
            }

        except Exception as e:
            self.logger.error(f"Status error: {e}")
            return {
                "service_active": False,
                "mpv_connected": False,
                "is_playing": False,
                "is_buffering": False,
                "current_episode": None,
                "position": 0,
                "duration": 0,
                "playback_speed": 1.0,
                "current_device": self._current_device,
                "error": str(e)
            }
