# backend/core/audio_source.py
"""
BaseAudioSource - base class for all audio sources.

Standard Status Format:
    {
        "state": "waiting",       # starting, waiting, active, error
        "service_active": True,  # systemd service status
        "metadata": {},          # source-specific data
        "error": None            # error message if state=error
    }
"""
from typing import Dict, Any, Optional
from abc import ABC, abstractmethod
import asyncio
import logging

from backend.core.models.audio_state import AudioSource, SourceState

logger = logging.getLogger(__name__)


class BaseAudioSource(ABC):
    """
    Base implementation for audio sources.

    Provides common functionality:
    - Systemd service management
    - WebSocket broadcasting via state machine
    - Standard response formatting
    - Lifecycle management (initialize, start, stop)

    Subclasses must implement:
    - _do_start(): Source-specific startup logic
    - _do_stop(): Source-specific shutdown logic
    - _get_status(): Source-specific status

    Optional overrides:
    - _do_restart(): Custom restart logic (default: stop + start)
    - _handle_command(): Source-specific commands

    Example:
        class RadioSource(BaseAudioSource):
            async def _do_start(self) -> bool:
                # Start mpv, connect to stream, etc.
                return True

            async def _do_stop(self) -> bool:
                # Stop mpv, cleanup
                return True

            async def _get_status(self) -> Dict[str, Any]:
                return {"station": "BBC Radio 1"}

            async def _handle_command(self, cmd: str, data: Dict) -> Dict:
                if cmd == "tune":
                    # Handle tune command
                    return self.success_response("Tuned to station")
                return self.error_response(f"Unknown command: {cmd}")
    """

    def __init__(
        self,
        source_id: str,
        service_name: str,
        state_machine=None,
        systemd_manager=None,
        settings_service=None,
        config=None
    ):
        """
        Initialize the audio source.

        Args:
            source_id: Unique identifier (e.g., "radio", "spotify")
            service_name: Systemd service name (e.g., "milo-radio")
            state_machine: Optional state machine for state synchronization
            systemd_manager: Optional SystemdServiceManager (injected via DI)
            settings_service: Optional SettingsService for persisting configuration
            config: Optional source-specific configuration dict
        """
        self.source_id = source_id
        self.service_name = service_name
        self.state_machine = state_machine

        self._state = SourceState.WAITING
        self._metadata: Dict[str, Any] = {}
        self._is_playing = False
        self._error: Optional[str] = None
        self._initialized = False

        self._service_manager = systemd_manager
        self._settings_service = settings_service
        self._config = config or {}
        self._logger = logging.getLogger(f"source.{source_id}")

        # Auto-disconnect timer (opt-in, subclasses override _on_auto_disconnect)
        self.auto_disconnect_enabled: bool = False
        self.pause_disconnect_delay: float = 10.0
        self._pause_timer: Optional[asyncio.Task] = None
        self._monitor_task: Optional[asyncio.Task] = None

    @property
    def state(self) -> SourceState:
        """Current state of the source."""
        return self._state

    @property
    def metadata(self) -> Dict[str, Any]:
        """Current metadata."""
        return self._metadata.copy()

    @property
    def is_playing(self) -> bool:
        """Whether the source is currently playing."""
        return self._is_playing

    @property
    def source(self) -> AudioSource:
        """AudioSource enum for this source."""
        return AudioSource(self.source_id)

    async def start(self) -> bool:
        """
        Start the audio source.

        Calls _do_start() for source-specific logic.

        Returns:
            True if start successful
        """
        self._logger.info(f"Starting {self.source_id}")
        self._state = SourceState.STARTING
        self._error = None

        try:
            success = await self._do_start()

            if success:
                # State should be set by _do_start (WAITING or ACTIVE)
                if self._state == SourceState.STARTING:
                    self._state = SourceState.WAITING

                self._logger.info(f"{self.source_id} started successfully")
            else:
                self._state = SourceState.ERROR
                self._error = "Start failed"

            return success

        except Exception as e:
            self._logger.error(f"Error starting {self.source_id}: {e}")
            self._state = SourceState.ERROR
            self._error = str(e)
            return False

    async def stop(self) -> bool:
        """
        Stop the audio source.

        Calls _do_stop() for source-specific logic.

        Returns:
            True if stop successful
        """
        self._logger.info(f"Stopping {self.source_id}")
        self._cancel_pause_timer()

        try:
            success = await self._do_stop()

            if success:
                self._state = SourceState.WAITING
                self._metadata = {}
                self._error = None

                self._logger.info(f"{self.source_id} stopped successfully")
            else:
                self._logger.warning(f"Failed to stop {self.source_id}")

            return success

        except Exception as e:
            self._logger.error(f"Error stopping {self.source_id}: {e}")
            return False

    async def restart(self) -> bool:
        """
        Restart the audio source.

        Default implementation: stop + start.
        Override _do_restart() for custom logic.

        Returns:
            True if restart successful
        """
        self._logger.info(f"Restarting {self.source_id}")

        try:
            # Try custom restart first
            success = await self._do_restart()

            if success:
                self._logger.info(f"{self.source_id} restarted successfully")

            return success

        except Exception as e:
            self._logger.error(f"Error restarting {self.source_id}: {e}")
            self._state = SourceState.ERROR
            self._error = str(e)
            return False

    async def status(self) -> Dict[str, Any]:
        """
        Get current status of the audio source.

        Returns standard format with source-specific additions from _get_status().

        Returns:
            Dict with state, service_active, metadata, error, and custom fields
        """
        service_active = await self._is_service_active()
        custom_status = await self._get_status()

        return {
            "state": self._state.value,
            "service_active": service_active,
            "metadata": self._metadata,
            "error": self._error,
            **custom_status
        }

    async def command(self, cmd: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a source-specific command.

        Delegates to _handle_command() for implementation.

        Args:
            cmd: Command name
            data: Command parameters

        Returns:
            Response dict with success, message/error, and custom data
        """
        self._logger.debug(f"Command: {cmd} with data: {data}")

        try:
            return await self._handle_command(cmd, data)
        except Exception as e:
            self._logger.error(f"Error handling command {cmd}: {e}")
            return self.error_response(str(e))

    # === Abstract methods for subclasses ===

    @abstractmethod
    async def _do_start(self) -> bool:
        """
        Source-specific startup implementation.

        Should:
        - Start systemd service if needed
        - Establish connections
        - Set self._state to WAITING or ACTIVE
        - Update self._metadata with initial data

        Returns:
            True if startup successful
        """
        pass

    async def _cleanup(self) -> None:
        """
        Source-specific resource cleanup (connections, tasks, state).

        Override in subclasses to clean up before service stop.
        Called by the default _do_stop() and can be called from _do_start()
        on failure. The outer stop() method handles exceptions.
        """
        pass

    def _reset_playback_state(self) -> None:
        """Reset playback state to idle defaults.

        Subclasses should call super()._reset_playback_state() then clear
        their own fields (e.g. _is_buffering, _device_connected, _current_station).
        """
        self._is_playing = False
        self._metadata = {}

    async def _do_stop(self) -> bool:
        """
        Stop the source: cleanup resources then stop the service.

        Default implementation calls _cleanup() then _stop_service().
        Override for custom shutdown logic (e.g., saving state before cleanup).
        The outer stop() method handles exceptions.

        Returns:
            True if shutdown successful
        """
        await self._cleanup()
        return await self._stop_service()

    async def _do_restart(self) -> bool:
        """
        Source-specific restart implementation.

        Default: stop + start.
        Override for custom restart logic (e.g., preserve state).

        Returns:
            True if restart successful
        """
        if not await self.stop():
            return False
        return await self.start()

    async def _get_status(self) -> Dict[str, Any]:
        """
        Source-specific status fields.

        Override to add custom fields to status response.
        Default returns empty dict.

        Returns:
            Dict with custom status fields
        """
        return {}

    async def _handle_command(self, cmd: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Source-specific command handling.

        Override to implement source-specific commands.
        Default returns error for unknown command.

        Args:
            cmd: Command name
            data: Command parameters

        Returns:
            Response dict
        """
        return self.error_response(f"Unknown command: {cmd}")

    # === Auto-Disconnect Timer ===

    def _cancel_pause_timer(self) -> None:
        """Cancel auto-disconnect timer."""
        if self._pause_timer:
            self._pause_timer.cancel()
            self._pause_timer = None

    def _start_pause_timer(self) -> None:
        """Start auto-disconnect timer after pause/inactivity."""
        if not self.auto_disconnect_enabled:
            return

        self._cancel_pause_timer()

        async def disconnect_after_delay():
            try:
                await asyncio.sleep(self.pause_disconnect_delay)
                self._logger.info(
                    f"Auto-disconnecting after {self.pause_disconnect_delay}s pause"
                )
                await self._on_auto_disconnect()
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self._logger.error(f"Auto-disconnect failed: {e}")

        self._pause_timer = asyncio.create_task(disconnect_after_delay())

    async def _on_auto_disconnect(self) -> None:
        """
        Called when the auto-disconnect timer fires.

        Default: restart the source. Override for custom behavior.
        """
        await self._do_restart()

    AUTO_DISCONNECT_SETTINGS_KEY = "audio.auto_disconnect_delay"

    async def _load_auto_disconnect_config(self) -> None:
        """Load the global auto-disconnect delay from settings."""
        if not self._settings_service:
            return

        try:
            delay = await self._settings_service.get_setting(self.AUTO_DISCONNECT_SETTINGS_KEY)
            if delay is not None:
                if delay == 0:
                    self.auto_disconnect_enabled = False
                    self.pause_disconnect_delay = 10.0
                else:
                    self.auto_disconnect_enabled = True
                    self.pause_disconnect_delay = float(delay)

            self._logger.info(
                f"Auto-disconnect: enabled={self.auto_disconnect_enabled}, "
                f"delay={self.pause_disconnect_delay}s"
            )
        except Exception as e:
            self._logger.error(f"Auto-disconnect settings load failed: {e}")

    async def reload_auto_disconnect_config(self) -> bool:
        """
        Reload the global auto-disconnect delay and refresh any running timer.

        Called from the settings API when the global delay changes so live
        sources pick up the new value without a restart.
        """
        await self._load_auto_disconnect_config()

        # Refresh a pending timer so the new delay takes effect immediately.
        if self._pause_timer and not self._pause_timer.done():
            self._cancel_pause_timer()
            if self.auto_disconnect_enabled:
                self._start_pause_timer()

        return True

    async def set_auto_disconnect_config(
        self,
        enabled: bool,
        delay: Optional[float] = None,
        save_to_settings: bool = True
    ) -> bool:
        """
        Update the global auto-disconnect configuration.

        Args:
            enabled: Whether auto-disconnect is enabled
            delay: Disconnect delay in seconds (0 = disabled)
            save_to_settings: Whether to persist to settings

        Returns:
            True if configuration succeeded
        """
        old_enabled = self.auto_disconnect_enabled
        old_delay = self.pause_disconnect_delay

        if delay is not None and delay == 0:
            self.auto_disconnect_enabled = False
            self.pause_disconnect_delay = 10.0
        elif delay is not None:
            self.auto_disconnect_enabled = enabled
            self.pause_disconnect_delay = max(1.0, delay)
        else:
            self.auto_disconnect_enabled = enabled

        if save_to_settings and self._settings_service:
            try:
                save_value = 0.0 if not self.auto_disconnect_enabled else self.pause_disconnect_delay
                success = await self._settings_service.set_setting(self.AUTO_DISCONNECT_SETTINGS_KEY, save_value)
                if not success:
                    self.auto_disconnect_enabled = old_enabled
                    self.pause_disconnect_delay = old_delay
                    return False
            except Exception as e:
                self._logger.error(f"Auto-disconnect settings save failed: {e}")
                self.auto_disconnect_enabled = old_enabled
                self.pause_disconnect_delay = old_delay
                return False

        # Manage running timer
        if self._pause_timer and not self._pause_timer.done():
            self._cancel_pause_timer()
            if self.auto_disconnect_enabled:
                self._start_pause_timer()

        return True

    # === Monitor task ===

    def _start_monitor(self) -> None:
        """Start the monitor loop task. Subclasses must implement _monitor_loop()."""
        if self._monitor_task:
            return
        self._monitor_task = asyncio.create_task(self._monitor_loop())

    def _stop_monitor(self) -> None:
        """Stop the monitor loop task."""
        if self._monitor_task:
            self._monitor_task.cancel()
            self._monitor_task = None

    # === Helper methods ===

    async def _start_service(self, service_name: str = None) -> bool:
        """Start a systemd service (defaults to self.service_name)."""
        name = service_name or self.service_name
        if not name:
            return True

        try:
            return await self._service_manager.start(name)
        except Exception as e:
            self._logger.error(f"Failed to start service {name}: {e}")
            return False

    async def _stop_service(self, service_name: str = None) -> bool:
        """Stop a systemd service (defaults to self.service_name)."""
        name = service_name or self.service_name
        if not name:
            return True

        try:
            return await self._service_manager.stop(name)
        except Exception as e:
            self._logger.error(f"Failed to stop service {name}: {e}")
            return False

    async def _restart_service(self, service_name: str = None) -> bool:
        """Restart a systemd service (defaults to self.service_name)."""
        name = service_name or self.service_name
        if not name:
            return True

        try:
            return await self._service_manager.restart(name)
        except Exception as e:
            self._logger.error(f"Failed to restart service {name}: {e}")
            return False

    async def _is_service_active(self, service_name: str = None) -> bool:
        """Check if a systemd service is active (defaults to self.service_name)."""
        name = service_name or self.service_name
        if not name:
            return True

        try:
            return await self._service_manager.is_active(name)
        except Exception:
            return False

    async def _start_service_and_wait(self, settle: float = 0.5) -> bool:
        """Start the systemd service and wait for it to settle."""
        if not await self._start_service():
            return False
        await asyncio.sleep(settle)
        return True

    async def _restart_service_and_wait(self, settle: float = 0.5) -> bool:
        """Restart the systemd service and wait for it to settle."""
        if not await self._restart_service():
            return False
        await asyncio.sleep(settle)
        return True

    async def initialize(self) -> bool:
        """
        Initialize the audio source.

        Called during application startup for sources that need
        early initialization (e.g., loading station data for API access).
        """
        self._initialized = True
        return True

    def set_state(self, state: SourceState, metadata: Optional[Dict[str, Any]] = None) -> None:
        """
        Set state and optionally update metadata.

        Syncs with state_machine if available (active sources only).

        Args:
            state: New state (SourceState enum)
            metadata: Optional metadata to merge
        """
        self._state = state
        if metadata:
            self._metadata.update(metadata)

        if self.state_machine:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    self.state_machine.update_source_state(
                        self.source, state, metadata
                    )
                )
            except RuntimeError:
                pass

    def _set_active_or_waiting(
        self,
        is_connected: bool,
        active_meta: Dict[str, Any],
        waiting_meta: Dict[str, Any]
    ) -> None:
        """Set state to ACTIVE or WAITING based on connection status."""
        self.set_state(
            SourceState.ACTIVE if is_connected else SourceState.WAITING,
            active_meta if is_connected else waiting_meta
        )

    def broadcast_position_update(self, position: int, duration: int) -> None:
        """Broadcast a lightweight position update without full_state.

        Used during steady playback where the frontend interpolates
        locally and only needs periodic drift correction.

        Also keeps system_state.metadata in sync so that initial_state
        sent on new WebSocket connections contains the live position.

        Args:
            position: Current position in milliseconds.
            duration: Total duration in milliseconds.
        """
        if not self.state_machine:
            return

        # Keep system_state.metadata in sync for initial_state on reconnect.
        # Guard: only update if this source is still active (avoids stale
        # writes from a source whose monitor hasn't stopped yet).
        sm = self.state_machine.system_state
        if sm.metadata is not None and sm.active_source == self.source:
            sm.metadata["position"] = position
            sm.metadata["duration"] = duration

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                self.state_machine.broadcast_event(
                    "source",
                    "position_update",
                    {
                        "source": self.source.value,
                        "position": position,
                        "duration": duration,
                    },
                    include_full_state=False,
                )
            )
        except RuntimeError:
            pass

    def broadcast_error(self, error_message: str) -> None:
        """
        Broadcast an error to the UI notification banner.

        Bypasses the active-source filter so errors are always shown
        regardless of which source is currently active.
        """
        if not self.state_machine:
            return

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                self.state_machine.broadcast_event(
                    "source",
                    "state_changed",
                    {
                        "source": self.source.value,
                        "new_state": SourceState.ERROR.value,
                        "metadata": {"error": error_message}
                    }
                )
            )
        except RuntimeError:
            pass

    def broadcast_error_cleared(self) -> None:
        """
        Clear any displayed error for this source.

        Called when an error condition is resolved. The UI will
        automatically dismiss the notification banner.
        """
        if not self.state_machine:
            return

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                self.state_machine.broadcast_event(
                    "source",
                    "error_cleared",
                    {"source": self.source.value}
                )
            )
        except RuntimeError:
            pass

    def success_response(self, message: str = None, **kwargs) -> Dict[str, Any]:
        """
        Create a success response for commands.

        Args:
            message: Optional success message
            **kwargs: Additional fields

        Returns:
            Response dict with success=True
        """
        response = {"success": True}
        if message:
            response["message"] = message
        return {**response, **kwargs}

    def error_response(self, error: str, **kwargs) -> Dict[str, Any]:
        """
        Create an error response for commands.

        Args:
            error: Error message
            **kwargs: Additional fields

        Returns:
            Response dict with success=False
        """
        return {"success": False, "error": error, **kwargs}
