# backend/core/audio_source.py
"""
AudioSource Protocol and BaseAudioSource implementation.

This module defines the standard interface for audio sources using Python's
Protocol for structural subtyping. This allows duck typing while providing
type safety through static analysis.

Usage:
    # Using Protocol for type hints
    def process_source(source: AudioSource) -> None:
        await source.start()

    # Implementing a source
    class RadioSource(BaseAudioSource):
        async def _do_start(self) -> bool:
            # Start radio playback
            return True

Protocol vs ABC:
    Protocol uses structural subtyping (duck typing). A class is considered
    to implement the protocol if it has all required attributes and methods,
    without explicit inheritance. This is more Pythonic and flexible.

Standard Status Format:
    {
        "state": "ready",        # starting, ready, connected, error
        "service_active": True,  # systemd service status
        "metadata": {},          # source-specific data
        "error": None            # error message if state=error
    }
"""
from typing import Protocol, Dict, Any, Optional, runtime_checkable
from abc import abstractmethod
import logging

from backend.core.events import EventBus, Events
from backend.core.systemd import SystemdServiceManager
from backend.core.models.audio_state import PluginState

logger = logging.getLogger(__name__)


# Standard state values
class SourceState:
    """Standard state values for audio sources."""
    STARTING = "starting"
    READY = "ready"
    CONNECTED = "connected"
    ERROR = "error"


@runtime_checkable
class AudioSource(Protocol):
    """
    Protocol defining the interface for audio sources.

    All audio source implementations must provide these attributes and methods.
    Uses structural subtyping - no explicit inheritance required.

    Attributes:
        source_id: Unique identifier for the source (e.g., "radio", "spotify")
        service_name: Name of the systemd service (e.g., "milo-radio")

    Methods:
        start(): Start the audio source
        stop(): Stop the audio source
        restart(): Restart the audio source
        status(): Get current status
        command(cmd, data): Execute a source-specific command

    Example:
        class MySource:
            source_id = "my_source"
            service_name = "milo-my-source"

            async def start(self) -> bool:
                return True

            async def stop(self) -> bool:
                return True

            async def restart(self) -> bool:
                return True

            async def status(self) -> Dict[str, Any]:
                return {"state": "ready", "service_active": True}

            async def command(self, cmd: str, data: Dict[str, Any]) -> Dict[str, Any]:
                return {"success": True}

        # MySource implements AudioSource protocol without inheritance
        source: AudioSource = MySource()
    """

    source_id: str
    service_name: str

    async def start(self) -> bool:
        """
        Start the audio source.

        Should:
        - Start the systemd service if needed
        - Establish connections (WebSocket, IPC, D-Bus, etc.)
        - Begin monitoring tasks

        Returns:
            True if start successful, False otherwise
        """
        ...

    async def stop(self) -> bool:
        """
        Stop the audio source.

        Should:
        - Stop any active playback
        - Cancel monitoring tasks
        - Close connections
        - Optionally stop systemd service

        Returns:
            True if stop successful, False otherwise
        """
        ...

    async def restart(self) -> bool:
        """
        Restart the audio source.

        Used for recovery from errors or service refresh.
        Should preserve user state where possible.

        Returns:
            True if restart successful, False otherwise
        """
        ...

    async def status(self) -> Dict[str, Any]:
        """
        Get current status of the audio source.

        Returns:
            Dict containing at minimum:
                - state: One of starting, ready, connected, error
                - service_active: bool indicating systemd service status
                - metadata: Dict with source-specific data
                - error: Error message if state is error, else None
        """
        ...

    async def command(self, cmd: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a source-specific command.

        Args:
            cmd: Command name (e.g., "play", "pause", "seek")
            data: Command parameters

        Returns:
            Dict containing:
                - success: bool indicating if command succeeded
                - message: Optional success message
                - error: Error message if success=False
                - Additional command-specific data
        """
        ...


class BaseAudioSource:
    """
    Base implementation for audio sources.

    Provides common functionality:
    - Systemd service management
    - EventBus integration for state notifications
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
        event_bus: EventBus,
        state_machine=None,
        systemd_manager=None
    ):
        """
        Initialize the audio source.

        Args:
            source_id: Unique identifier (e.g., "radio", "spotify")
            service_name: Systemd service name (e.g., "milo-radio")
            event_bus: EventBus for state notifications
            state_machine: Optional state machine for state synchronization
            systemd_manager: Optional SystemdServiceManager (injected via DI)
        """
        self.source_id = source_id
        self.service_name = service_name
        self.event_bus = event_bus
        self.state_machine = state_machine

        self._state = SourceState.READY
        self._metadata: Dict[str, Any] = {}
        self._error: Optional[str] = None
        self._initialized = False

        self._service_manager = systemd_manager
        self._logger = logging.getLogger(f"source.{source_id}")

    @property
    def state(self) -> str:
        """Current state of the source (string)."""
        return self._state

    @property
    def current_state(self) -> PluginState:
        """Current state as PluginState enum (for API compatibility)."""
        state_map = {
            SourceState.STARTING: PluginState.STARTING,
            SourceState.READY: PluginState.READY,
            SourceState.CONNECTED: PluginState.CONNECTED,
            SourceState.ERROR: PluginState.ERROR,
        }
        return state_map.get(self._state, PluginState.READY)

    @property
    def metadata(self) -> Dict[str, Any]:
        """Current metadata."""
        return self._metadata.copy()

    @property
    def source(self):
        """
        Backward compatibility: return AudioSource enum.

        Old code uses plugin.source (AudioSource enum).
        New code uses plugin.source_id (string).

        Returns:
            AudioSource enum value
        """
        from backend.core.models.audio_state import AudioSource
        try:
            return AudioSource(self.source_id)
        except ValueError:
            return AudioSource.NONE

    async def start(self) -> bool:
        """
        Start the audio source with EventBus notification.

        Calls _do_start() for source-specific logic.
        Emits SOURCE_STARTED event on success.

        Returns:
            True if start successful
        """
        self._logger.info(f"Starting {self.source_id}")
        self._state = SourceState.STARTING
        self._error = None

        try:
            success = await self._do_start()

            if success:
                # State should be set by _do_start (READY or CONNECTED)
                if self._state == SourceState.STARTING:
                    self._state = SourceState.READY

                await self.event_bus.emit(Events.SOURCE_STARTED, {
                    "source": self.source_id,
                    "state": self._state
                })

                self._logger.info(f"{self.source_id} started successfully")
            else:
                self._state = SourceState.ERROR
                self._error = "Start failed"
                await self._emit_error("Start failed")

            return success

        except Exception as e:
            self._logger.error(f"Error starting {self.source_id}: {e}")
            self._state = SourceState.ERROR
            self._error = str(e)
            await self._emit_error(str(e))
            return False

    async def stop(self) -> bool:
        """
        Stop the audio source with EventBus notification.

        Calls _do_stop() for source-specific logic.
        Emits SOURCE_STOPPED event on success.

        Returns:
            True if stop successful
        """
        self._logger.info(f"Stopping {self.source_id}")

        try:
            success = await self._do_stop()

            if success:
                self._state = SourceState.READY
                self._metadata = {}
                self._error = None

                await self.event_bus.emit(Events.SOURCE_STOPPED, {
                    "source": self.source_id
                })

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
            "state": self._state,
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
        - Set self._state to READY or CONNECTED
        - Update self._metadata with initial data

        Returns:
            True if startup successful
        """
        pass

    @abstractmethod
    async def _do_stop(self) -> bool:
        """
        Source-specific shutdown implementation.

        Should:
        - Stop playback
        - Close connections
        - Cancel tasks
        - Optionally stop systemd service

        Returns:
            True if shutdown successful
        """
        pass

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

    # === Helper methods ===

    async def _start_service(self) -> bool:
        """Start the systemd service."""
        if not self.service_name:
            return True

        try:
            return await self._service_manager.start(self.service_name)
        except Exception as e:
            self._logger.error(f"Failed to start service {self.service_name}: {e}")
            return False

    async def _stop_service(self) -> bool:
        """Stop the systemd service."""
        if not self.service_name:
            return True

        try:
            return await self._service_manager.stop(self.service_name)
        except Exception as e:
            self._logger.error(f"Failed to stop service {self.service_name}: {e}")
            return False

    async def _restart_service(self) -> bool:
        """Restart the systemd service."""
        if not self.service_name:
            return True

        try:
            return await self._service_manager.restart(self.service_name)
        except Exception as e:
            self._logger.error(f"Failed to restart service {self.service_name}: {e}")
            return False

    async def _is_service_active(self) -> bool:
        """Check if systemd service is active."""
        if not self.service_name:
            return True

        try:
            return await self._service_manager.is_active(self.service_name)
        except Exception:
            return False

    async def _emit_error(self, error: str) -> None:
        """Emit error event."""
        await self.event_bus.emit(Events.SOURCE_ERROR, {
            "source": self.source_id,
            "error": error
        })

    # === Public API methods ===
    # These methods are called by API routes and the state machine

    async def initialize(self) -> bool:
        """
        Initialize the audio source.

        Called during application startup by main.py for sources that need
        early initialization (e.g., loading station data for API access).

        Returns:
            True on success
        """
        self._initialized = True
        return True

    async def get_status(self) -> Dict[str, Any]:
        """
        Get current status of the audio source.

        Alias for status() - used by API routes throughout the application.

        Returns:
            Status dict from status()
        """
        return await self.status()

    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle a command for this audio source.

        Used by API routes and routing service for playback control.

        Args:
            command: Command name
            data: Command parameters

        Returns:
            Response dict from command()
        """
        return await self.command(command, data)

    def is_active_plugin(self) -> bool:
        """
        Backward compatibility: check if this plugin is active.

        Checks with state_machine if available.

        Returns:
            True if this is the active source
        """
        if self.state_machine:
            try:
                active = self.state_machine.system_state.active_source
                # Map source_id to AudioSource enum
                from backend.core.models.audio_state import AudioSource
                source_enum = AudioSource(self.source_id.upper()) if self.source_id else None
                return active == source_enum
            except Exception:
                pass
        return False

    def set_state(self, state: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """
        Set state and optionally update metadata.

        Use this in _do_start() to set CONNECTED state with metadata.
        Also syncs with state_machine if available.

        Args:
            state: New state (use SourceState constants)
            metadata: Optional metadata to merge
        """
        self._state = state
        if metadata:
            self._metadata.update(metadata)

        # Sync with state machine if available (active sources only)
        if self.state_machine and hasattr(self, 'source'):
            plugin_state_map = {
                SourceState.STARTING: PluginState.STARTING,
                SourceState.READY: PluginState.READY,
                SourceState.CONNECTED: PluginState.CONNECTED,
                SourceState.ERROR: PluginState.ERROR,
            }
            plugin_state = plugin_state_map.get(state, PluginState.READY)

            import asyncio
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    self.state_machine.update_plugin_state(
                        self.source, plugin_state, metadata
                    )
                )
            except RuntimeError:
                pass

    def broadcast_error(self, error_message: str) -> None:
        """
        Broadcast an error to the UI notification banner.

        This bypasses the active-source filter so errors are always shown
        to the user regardless of which source is currently active.

        Args:
            error_message: Human-readable error message
        """
        if not self.state_machine or not hasattr(self, 'source'):
            return

        import asyncio
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                self.state_machine.broadcast_event(
                    "plugin",
                    "state_changed",
                    {
                        "source": self.source.value,
                        "new_state": PluginState.ERROR.value,
                        "metadata": {"error": error_message}
                    }
                )
            )
        except RuntimeError:
            pass

    def broadcast_error_cleared(self) -> None:
        """
        Clear any displayed error for this source.

        Called when an error condition is resolved (e.g., connection restored,
        stream loaded successfully). The UI will automatically dismiss the
        notification banner.
        """
        if not self.state_machine or not hasattr(self, 'source'):
            return

        import asyncio
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                self.state_machine.broadcast_event(
                    "plugin",
                    "error_cleared",
                    {"source": self.source.value}
                )
            )
            self._logger.debug(f"[{self.source_id}] Error cleared broadcast sent")
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
