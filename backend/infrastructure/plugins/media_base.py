# backend/infrastructure/plugins/media_base.py
"""
Base class for mpv-based media plugins (Radio, Podcast)

Provides common functionality for plugins that use mpv for audio playback:
- systemd service management
- mpv IPC socket connection
- Playback state monitoring
- Common start/stop/restart patterns
"""
import asyncio
import logging
from abc import abstractmethod
from typing import Dict, Any, Optional

from backend.infrastructure.plugins.base import UnifiedAudioPlugin
from backend.infrastructure.plugins.controllers.mpv_controller import MpvController
from backend.domain.audio_state import AudioSource, PluginState


class BaseMediaPlugin(UnifiedAudioPlugin):
    """
    Base class for mpv-based media plugins.

    Subclasses must implement:
        - _build_playback_metadata(): Return metadata dict for current item
        - _on_playback_state_changed(is_playing): Handle playback state changes
        - _cleanup_resources(): Plugin-specific cleanup before stop
        - _initialize_components(): Plugin-specific component initialization

    States:
        STARTING → service starting
        READY → service started (mpv in idle)
        CONNECTED → media playing
        ERROR → service error
    """

    def __init__(
        self,
        source: AudioSource,
        config: Dict[str, Any],
        state_machine=None,
        settings_service=None,
        default_ipc_socket: str = "/tmp/milo-media-ipc.sock"
    ):
        super().__init__(
            source=source,
            config=config,
            state_machine=state_machine,
            settings_service=settings_service
        )

        # IPC socket configuration
        self.ipc_socket_path = config.get("ipc_socket", default_ipc_socket)

        # mpv controller
        self.mpv = MpvController(self.ipc_socket_path)

        # Common playback state
        self._is_playing = False
        self._is_buffering = False
        self._metadata: Dict[str, Any] = {}

        # Monitoring
        self._monitor_task: Optional[asyncio.Task] = None
        self._stopping = False

        # ALSA device identifier
        self._current_device = f"milo_{source.value}"

    async def _do_initialize(self) -> bool:
        """
        Common media plugin initialization.

        Checks that:
        1. systemd service exists
        2. mpv is installed
        3. Plugin-specific components are ready (via _initialize_components)
        """
        try:
            # Check that service exists
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "list-unit-files", self.service_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()

            if proc.returncode != 0 or self.service_name not in stdout.decode():
                raise RuntimeError(f"Service {self.service_name} not found")

            # Check that mpv is installed
            proc = await asyncio.create_subprocess_exec(
                "which", "mpv",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.wait()

            if proc.returncode != 0:
                raise RuntimeError("mpv is not installed")

            # Plugin-specific initialization
            if not await self._initialize_components():
                return False

            self.logger.info(f"{self.source.value.capitalize()} plugin initialized")
            return True

        except Exception as e:
            self.logger.error(f"{self.source.value.capitalize()} initialization error: {e}")
            return False

    async def _initialize_components(self) -> bool:
        """
        Initialize plugin-specific components.

        Override in subclasses to initialize data services, API clients, etc.
        Default implementation returns True (no additional components).
        """
        return True

    async def _do_start(self) -> bool:
        """Common media plugin startup sequence."""
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

            self.logger.info(f"{self.source.value.capitalize()} service started and ready")
            return True

        except Exception as e:
            self.logger.error(f"{self.source.value.capitalize()} start error: {e}")
            return False

    async def restart(self) -> bool:
        """
        Common media plugin restart sequence.

        1. Saves state if needed (via _save_state_before_restart)
        2. Stops monitoring
        3. Disconnects mpv
        4. Restarts systemd service
        5. Reconnects mpv
        6. Resumes monitoring
        """
        try:
            self.logger.info(f"Restarting {self.source.value.capitalize()} service")

            # Allow subclass to save state before restart
            await self._save_state_before_restart()

            # Stop monitoring
            self._stopping = True
            if self._monitor_task:
                self._monitor_task.cancel()
                try:
                    await self._monitor_task
                except asyncio.CancelledError:
                    pass
                self._monitor_task = None

            # Disconnect mpv
            await self.mpv.disconnect()

            # Reset playback state
            self._is_playing = False
            self._is_buffering = False
            self._metadata = {}

            # Allow subclass to reset additional state
            await self._reset_playback_state()

            # Restart service
            success = await self.control_service(self.service_name, "restart")

            if not success:
                self.logger.error(f"Service restart failed: {self.service_name}")
                return False

            # Wait for service to be ready
            await asyncio.sleep(1)

            # IPC reconnection
            if not await self.mpv.connect(max_retries=10, retry_delay=0.5):
                self.logger.error("Unable to reconnect to IPC socket after restart")
                return False

            # Restart monitoring
            self._stopping = False
            self._monitor_task = asyncio.create_task(self._monitor_playback())

            # Notify READY state (delayed to ensure monitoring is active)
            async def notify_ready_state():
                await asyncio.sleep(0.1)
                await self.notify_state_change(PluginState.READY, {"ready": True})

            asyncio.create_task(notify_ready_state())

            self.logger.info(f"{self.source.value.capitalize()} service restarted")
            return True

        except Exception as e:
            self.logger.error(f"{self.source.value.capitalize()} restart error: {e}")
            return False

    async def _save_state_before_restart(self) -> None:
        """
        Save state before restart (e.g., playback progress).

        Override in subclasses if needed.
        """
        pass

    async def _reset_playback_state(self) -> None:
        """
        Reset plugin-specific playback state during restart.

        Override in subclasses to reset current_station, current_episode, etc.
        """
        pass

    async def stop(self) -> bool:
        """Common media plugin stop sequence."""
        try:
            self.logger.info(f"Stopping {self.source.value.capitalize()} plugin")

            # Stop monitoring
            self._stopping = True
            if self._monitor_task:
                self._monitor_task.cancel()
                try:
                    await self._monitor_task
                except asyncio.CancelledError:
                    pass
                self._monitor_task = None

            # Plugin-specific cleanup (save progress, close APIs, etc.)
            await self._cleanup_resources()

            # Stop playback
            if self._is_playing:
                await self.mpv.stop()

            # Disconnect mpv
            await self.mpv.disconnect()

            # Stop service
            await self.control_service(self.service_name, "stop")

            # Reset state
            self._is_playing = False
            self._is_buffering = False
            self._metadata = {}

            # Reset plugin-specific state
            await self._reset_playback_state()

            self.logger.info(f"{self.source.value.capitalize()} plugin stopped")
            return True

        except Exception as e:
            self.logger.error(f"{self.source.value.capitalize()} stop error: {e}")
            return False

    async def _cleanup_resources(self) -> None:
        """
        Clean up plugin-specific resources before stopping.

        Override in subclasses to:
        - Save playback progress
        - Close API clients
        - Release other resources
        """
        pass

    async def _monitor_playback(self) -> None:
        """
        Monitor mpv playback state.

        Base implementation handles common state transitions.
        Subclasses can override _on_playback_state_changed for specific handling.
        """
        try:
            while not self._stopping:
                try:
                    # Check playback state
                    is_playing = await self.mpv.is_playing()

                    # Detect state change
                    if is_playing != self._is_playing:
                        old_playing = self._is_playing
                        self._is_playing = is_playing

                        self.logger.info(
                            f"Playback state changed: {'playing' if is_playing else 'stopped'}"
                        )

                        # Handle buffering → playing transition
                        if is_playing and self._is_buffering:
                            self._is_buffering = False
                            self.logger.info("Buffering completed, stream playing")

                        # Notify subclass of state change
                        await self._on_playback_state_changed(
                            is_playing=is_playing,
                            was_playing=old_playing
                        )

                    # Periodic update (subclass can override)
                    await self._monitor_update()

                    # Polling interval
                    await asyncio.sleep(self._get_monitor_interval())

                except Exception as e:
                    self.logger.error(f"Playback monitoring error: {e}")
                    await asyncio.sleep(5)

        except asyncio.CancelledError:
            self.logger.debug("Playback monitoring cancelled")
        except Exception as e:
            self.logger.error(f"Critical monitoring error: {e}")

    async def _on_playback_state_changed(
        self, is_playing: bool, was_playing: bool
    ) -> None:
        """
        Called when playback state changes.

        Override in subclasses for specific handling (e.g., update metadata,
        handle episode end, etc.)

        Args:
            is_playing: Current playback state
            was_playing: Previous playback state
        """
        pass

    async def _monitor_update(self) -> None:
        """
        Called on each monitor loop iteration.

        Override in subclasses for periodic updates (e.g., update position,
        check metadata changes, etc.)
        """
        pass

    def _get_monitor_interval(self) -> float:
        """
        Get monitoring loop interval in seconds.

        Override in subclasses if different interval is needed.
        Default: 0.5 seconds
        """
        return 0.5

    @abstractmethod
    def _build_playback_metadata(self) -> Dict[str, Any]:
        """
        Build metadata dict for state notifications.

        Must be implemented by subclasses to return appropriate metadata
        for the current playing item (station, episode, etc.)
        """
        pass

    async def get_status(self) -> Dict[str, Any]:
        """Get current plugin status."""
        try:
            service_status = await self.service_manager.get_status(self.service_name)
            mpv_status = await self.mpv.get_status()

            return {
                "service_active": service_status.get("active", False),
                "mpv_connected": mpv_status.get("connected", False),
                "is_playing": self._is_playing,
                "is_buffering": self._is_buffering,
                "metadata": self._metadata,
                "current_device": self._current_device
            }

        except Exception as e:
            self.logger.error(f"Status error: {e}")
            return {
                "service_active": False,
                "mpv_connected": False,
                "is_playing": False,
                "is_buffering": False,
                "metadata": {},
                "current_device": self._current_device,
                "error": str(e)
            }

    async def get_initial_state(self) -> Dict[str, Any]:
        """Initial state for WebSockets."""
        return await self.get_status()

    def is_active_plugin(self) -> bool:
        """Check if plugin is currently playing or buffering."""
        return self._is_playing or self._is_buffering
