"""
Abstract interface for audio source plugins.

All audio source plugins must implement this interface to ensure consistent
behavior across the system.
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, ClassVar

from backend.domain.audio_state import AudioSource, PluginState


class AudioSourcePlugin(ABC):
    """
    Abstract interface for audio source plugins.

    All plugins MUST implement these methods to ensure consistent behavior
    across the Milo audio system.

    Lifecycle:
        1. initialize() - Called once at application startup
        2. start() - Called when switching TO this source
        3. stop() - Called when switching AWAY from this source
        4. restart() - Called to restart without full stop/start cycle

    States (PluginState):
        STARTING  - Service is starting up
        READY     - Service running, waiting for connection/playback
        CONNECTED - Active playback or connection established
        ERROR     - Something went wrong

    Required Attributes (must be set in subclass __init__):
        source: AudioSource - The audio source this plugin handles
        service_name: str - Systemd service name for this plugin
    """

    # Subclasses must define these
    source: AudioSource
    service_name: str

    @abstractmethod
    async def initialize(self) -> bool:
        """
        One-time initialization at application startup.

        Called once when the application starts. Use this to:
        - Check dependencies are available
        - Load configuration
        - Initialize components (but don't start services yet)

        Returns:
            True if initialization successful, False otherwise
        """
        pass

    @abstractmethod
    async def start(self) -> bool:
        """
        Start the plugin when switching to this audio source.

        Called when the user switches to this audio source. Should:
        - Start the systemd service if needed
        - Establish connections (WebSocket, IPC, D-Bus, etc.)
        - Begin monitoring tasks
        - Notify state change to READY or CONNECTED

        Returns:
            True if start successful, False otherwise
        """
        pass

    @abstractmethod
    async def stop(self) -> bool:
        """
        Stop the plugin when switching away from this audio source.

        Called when the user switches to a different audio source. Should:
        - Stop any active playback
        - Cancel monitoring tasks
        - Close connections
        - Stop systemd service if configured to do so

        Returns:
            True if stop successful, False otherwise
        """
        pass

    @abstractmethod
    async def restart(self) -> bool:
        """
        Restart the plugin without full stop/start cycle.

        Used for recovery from errors or service refresh.
        Should preserve user state where possible (e.g., current station).

        Returns:
            True if restart successful, False otherwise
        """
        pass

    @abstractmethod
    async def get_status(self) -> Dict[str, Any]:
        """
        Get current plugin status for API responses.

        Returns:
            Dict containing at minimum:
                - state: Current PluginState value
                - service_active: bool indicating if systemd service is running
                - Any plugin-specific status fields
        """
        pass

    @abstractmethod
    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle plugin-specific commands.

        Args:
            command: Command name (e.g., "play", "pause", "disconnect")
            data: Command parameters as a dictionary

        Returns:
            Dict containing:
                - success: bool indicating if command succeeded
                - message: Optional success message
                - error: Error message (if success=False)
                - Additional command-specific data
        """
        pass

    @abstractmethod
    def is_active_plugin(self) -> bool:
        """
        Check if this plugin is the currently active audio source.

        Returns:
            True if this plugin is the active source, False otherwise
        """
        pass

    async def get_initial_state(self) -> Dict[str, Any]:
        """
        Get initial state for WebSocket clients.

        Called when a new WebSocket client connects to send the current state.
        Default implementation returns get_status().

        Override this method if you need custom initial state format.

        Returns:
            Dict with initial state data for WebSocket clients
        """
        return await self.get_status()
