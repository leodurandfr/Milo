# backend/infrastructure/plugins/base.py
"""
Base class for unified audio plugins.

Provides common functionality for all audio source plugins:
- Systemd service management
- State machine integration
- Response formatting
- Lifecycle management (initialize, start, stop, restart)
"""
import logging
from abc import abstractmethod
from typing import Dict, Any, Optional

from backend.application.interfaces.audio_source import AudioSourcePlugin
from backend.domain.audio_state import PluginState, AudioSource
from backend.infrastructure.services.systemd_manager import SystemdServiceManager


class UnifiedAudioPlugin(AudioSourcePlugin):
    """
    Base class for audio plugins.

    Provides common implementation for the AudioSourcePlugin interface.
    Subclasses must implement:
        - _do_initialize() - Plugin-specific initialization
        - _do_start() - Plugin-specific startup
        - stop() - Plugin-specific shutdown
        - get_status() - Plugin status for API
        - handle_command() - Plugin-specific commands

    Args:
        source: The AudioSource enum this plugin handles
        config: Plugin configuration dictionary
        state_machine: Reference to UnifiedAudioStateMachine
        settings_service: Optional settings service for user preferences
    """

    def __init__(
        self,
        source: AudioSource,
        config: Dict[str, Any],
        state_machine,
        settings_service=None
    ):
        # Required by interface
        self.source = source
        self.service_name = config.get("service_name", "")

        # Dependencies
        self.config = config
        self.state_machine = state_machine
        self.settings_service = settings_service

        # Internal state
        self._initialized = False
        self._metadata: Dict[str, Any] = {}

        # Services
        self.service_manager = SystemdServiceManager()
        self.logger = logging.getLogger(f"plugin.{source.value}")

    @property
    def current_state(self) -> PluginState:
        """Gets the current state from the state machine (source of truth)."""
        if self.state_machine and self.is_active_plugin():
            return self.state_machine.system_state.plugin_state
        return PluginState.READY

    @property
    def current_metadata(self) -> Dict[str, Any]:
        """Gets the metadata from the state machine (source of truth)."""
        if self.state_machine and self.is_active_plugin():
            return self.state_machine.system_state.metadata
        return {}

    def is_active_plugin(self) -> bool:
        """Checks if this plugin is the currently active one."""
        if not self.state_machine:
            return False
        return self.state_machine.system_state.active_source == self.source

    async def notify_state_change(
        self,
        new_state: PluginState,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Notifies the state machine of a plugin state change."""
        if self.state_machine:
            await self.state_machine.update_plugin_state(
                source=self.source,
                new_state=new_state,
                metadata=metadata or {}
            )

    async def control_service(self, service_name: str, action: str) -> bool:
        """
        Controls a systemd service.

        Args:
            service_name: Name of the systemd service
            action: One of "start", "stop", "restart"

        Returns:
            True if action succeeded
        """
        try:
            self.logger.debug(f"{action.capitalize()} service {service_name}")

            actions = {
                "start": self.service_manager.start,
                "stop": self.service_manager.stop,
                "restart": self.service_manager.restart
            }

            if action not in actions:
                self.logger.error(f"Unsupported action: {action}")
                return False

            success = await actions[action](service_name)

            if not success:
                self.logger.error(f"Failed to {action} {service_name}")

            return success
        except Exception as e:
            self.logger.error(f"Error {action} service {service_name}: {e}")
            return False

    def format_response(
        self,
        success: bool,
        message: str = None,
        error: str = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Formats a standard response for commands.

        Args:
            success: Whether the command succeeded
            message: Optional success message
            error: Optional error message (used when success=False)
            **kwargs: Additional fields to include in response

        Returns:
            Dict with success, message/error, and any additional fields
        """
        response = {"success": success}

        if success and message:
            response["message"] = message
        elif not success and error:
            response["error"] = error

        return {**response, **kwargs}

    async def initialize(self) -> bool:
        """
        Initializes the plugin with idempotence.

        Calls _do_initialize() once, subsequent calls return cached result.

        Returns:
            True if initialization successful
        """
        if self._initialized:
            return True

        try:
            success = await self._do_initialize()
            if success:
                self._initialized = True
                self.logger.info(f"{self.source.value} plugin initialized")
            return success
        except Exception as e:
            self.logger.error(f"Initialization error {self.source.value}: {e}")
            return False

    async def _do_initialize(self) -> bool:
        """
        Plugin-specific initialization.

        Override this method to perform plugin-specific initialization.
        Default implementation returns True (no initialization needed).

        Returns:
            True if initialization successful
        """
        return True

    async def start(self) -> bool:
        """
        Starts the audio source with state management.

        Ensures initialization, then calls _do_start().
        Note: _do_start() is responsible for notifying the appropriate state
        (READY, CONNECTED, etc.) - this method only handles ERROR on failure.

        Returns:
            True if start successful
        """
        if not self._initialized and not await self.initialize():
            await self.notify_state_change(
                PluginState.ERROR,
                {"error": "Initialization failed"}
            )
            return False

        try:
            success = await self._do_start()

            if not success:
                await self.notify_state_change(
                    PluginState.ERROR,
                    {"error": "Start failed"}
                )

            # Note: _do_start() is responsible for notifying READY/CONNECTED state
            return success
        except Exception as e:
            self.logger.error(f"Start error {self.source.value}: {e}")
            await self.notify_state_change(PluginState.ERROR, {"error": str(e)})
            return False

    @abstractmethod
    async def _do_start(self) -> bool:
        """
        Plugin-specific startup implementation.

        Called by start() after ensuring initialization.
        Should start systemd service, establish connections, etc.

        Returns:
            True if startup successful
        """
        pass

    async def restart(self) -> bool:
        """
        Restarts the plugin.

        Default implementation: restart systemd service + call _do_start().
        Override this method if custom restart logic is needed.

        Note: _do_start() is responsible for notifying the appropriate state
        (READY, CONNECTED, etc.) so this method does not call notify_state_change()
        on success.

        Returns:
            True if restart successful
        """
        try:
            self.logger.info(f"Restarting {self.source.value} plugin")

            # Restart systemd service
            success = await self.control_service(self.service_name, "restart")
            if not success:
                self.logger.error(f"Failed to restart service {self.service_name}")
                await self.notify_state_change(
                    PluginState.ERROR,
                    {"error": "Restart failed"}
                )
                return False

            # Reinitialize plugin (monitoring, IPC connections, state detection)
            success = await self._do_start()
            if not success:
                await self.notify_state_change(
                    PluginState.ERROR,
                    {"error": "Reinitialization failed"}
                )
                return False

            self.logger.info(f"{self.source.value} plugin restarted")
            return True

        except Exception as e:
            self.logger.error(f"Error restarting {self.source.value}: {e}")
            await self.notify_state_change(PluginState.ERROR, {"error": str(e)})
            return False

    @abstractmethod
    async def stop(self) -> bool:
        """
        Stops the audio source.

        Must be implemented by subclasses to:
        - Stop playback
        - Cancel monitoring tasks
        - Close connections
        - Optionally stop systemd service

        Returns:
            True if stop successful
        """
        pass

    @abstractmethod
    async def get_status(self) -> Dict[str, Any]:
        """
        Gets the current state of the audio source.

        Must return at minimum:
            - state: Current PluginState value
            - service_active: bool

        Returns:
            Dict with plugin status
        """
        pass

    @abstractmethod
    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Processes a command for this source.

        Use format_response() to create the return value.

        Args:
            command: Command name
            data: Command parameters

        Returns:
            Dict with success, message/error, and command-specific data
        """
        pass

    async def get_initial_state(self) -> Dict[str, Any]:
        """Initial state for new WebSocket connections."""
        return await self.get_status()
