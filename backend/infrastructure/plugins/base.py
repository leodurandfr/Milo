# backend/infrastructure/plugins/base.py
"""
Optimized base class for plugins - Clean version without EventBus
"""
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

from backend.application.interfaces.audio_source import AudioSourcePlugin
from backend.domain.audio_state import PluginState, AudioSource
from backend.infrastructure.services.systemd_manager import SystemdServiceManager

class UnifiedAudioPlugin(AudioSourcePlugin, ABC):
    """Base class for audio plugins - Clean version"""

    def __init__(self, name: str, state_machine=None):
        self.name = name
        self.logger = logging.getLogger(f"plugin.{name}")
        self.state_machine = state_machine
        self.service_manager = SystemdServiceManager()
        self._initialized = False

    def _get_audio_source(self) -> AudioSource:
        """Returns the AudioSource enum corresponding to this plugin"""
        sources = {
            'librespot': AudioSource.SPOTIFY,
            'roc': AudioSource.MAC,
            'bluetooth': AudioSource.BLUETOOTH,
            'radio': AudioSource.RADIO,
            'podcast': AudioSource.PODCAST
        }
        return sources.get(self.name, AudioSource.NONE)
    
    @property
    def current_state(self) -> PluginState:
        """Gets the current state from the state machine (source of truth)"""
        if self.state_machine and self.is_active_plugin():
            return self.state_machine.system_state.plugin_state
        return PluginState.INACTIVE
    
    @property
    def current_metadata(self) -> Dict[str, Any]:
        """Gets the metadata from the state machine (source of truth)"""
        if self.state_machine and self.is_active_plugin():
            return self.state_machine.system_state.metadata
        return {}
    
    def is_active_plugin(self) -> bool:
        """Checks if this plugin is the currently active one"""
        if not self.state_machine:
            return False
        return self.state_machine.system_state.active_source == self._get_audio_source()
    
    async def notify_state_change(self, new_state: PluginState, metadata: Dict[str, Any] = None) -> None:
        """Notifies the state machine of a plugin state change"""
        if self.state_machine:
            await self.state_machine.update_plugin_state(
                source=self._get_audio_source(),
                new_state=new_state,
                metadata=metadata or {}
            )
    
    async def control_service(self, service_name: str, action: str) -> bool:
        """Controls a systemd service (start, stop, restart)"""
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

    def format_response(self, success: bool, message: str = None, error: str = None, **kwargs) -> Dict[str, Any]:
        """Formats a standard response for commands"""
        response = {"success": success}
        
        if success and message:
            response["message"] = message
        elif not success and error:
            response["error"] = error
            
        return {**response, **kwargs}
    
    async def initialize(self) -> bool:
        """Initializes the plugin with idempotence"""
        if self._initialized:
            return True

        try:
            success = await self._do_initialize()
            if success:
                self._initialized = True
            return success
        except Exception as e:
            self.logger.error(f"Initialization error {self.name}: {e}")
            return False

    @abstractmethod
    async def _do_initialize(self) -> bool:
        """Specific implementation of initialization"""
        pass

    async def start(self) -> bool:
        """Starts the audio source with state management"""
        if not self._initialized and not await self.initialize():
            await self.notify_state_change(PluginState.ERROR, {"error": "Initialization failed"})
            return False

        try:
            success = await self._do_start()

            if success:
                await self.notify_state_change(PluginState.READY)
            else:
                await self.notify_state_change(PluginState.ERROR, {"error": "Start failed"})

            return success
        except Exception as e:
            self.logger.error(f"Start error {self.name}: {e}")
            await self.notify_state_change(PluginState.ERROR, {"error": str(e)})
            return False

    @abstractmethod
    async def _do_start(self) -> bool:
        """Specific implementation of start"""
        pass

    async def restart(self) -> bool:
        """Restarts the systemd service - Base version"""
        try:
            success = await self.control_service(self.service_name, "restart")
            return success
        except Exception as e:
            self.logger.error(f"Error restarting service: {e}")
            return False

    @abstractmethod
    async def stop(self) -> bool:
        """Stops the audio source"""
        pass

    @abstractmethod
    async def get_status(self) -> Dict[str, Any]:
        """Gets the current state of the audio source"""
        pass

    @abstractmethod
    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Processes a command for this source"""
        pass

    async def get_initial_state(self) -> Dict[str, Any]:
        """Initial state for new WebSocket connections"""
        return await self.get_status()