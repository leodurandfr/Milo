# backend/core/state.py
"""
Simplified Audio State Machine using EventBus for decoupled communication.

This state machine manages audio source transitions and emits events via EventBus.
It preserves SystemAudioState structure for frontend compatibility.

Usage:
    from backend.core.events import EventBus
    from backend.core.state import AudioStateMachine

    event_bus = EventBus()
    state_machine = AudioStateMachine(event_bus)

    # Activate a source
    await state_machine.activate_source(AudioSource.RADIO)

    # Listen to events
    event_bus.on(Events.SOURCE_STARTED, handle_source_started)
"""
import asyncio
import time
import logging
from time import monotonic
from typing import Dict, Any, Optional

from backend.core.models.audio_state import AudioSource, PluginState, SystemAudioState
from backend.core.audio_source import AudioSource as AudioSourceProtocol
from backend.core.events import EventBus, Events

logger = logging.getLogger(__name__)


class AudioStateMachine:
    """
    Audio state machine using EventBus for internal communication
    and WebSocketManager for frontend broadcasting.
    """

    TRANSITION_TIMEOUT = 10.0
    INACTIVITY_TIMEOUT = 7200  # 2 hours in seconds

    # Only plugin/system events include full_state (used by unifiedAudioStore).
    # Other categories (volume, equalizer, multiroom, settings) send only
    # their specific data — their frontend stores don't read full_state.
    _FULL_STATE_CATEGORIES = frozenset({"plugin", "system"})

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.system_state = SystemAudioState()
        self.plugins: Dict[AudioSource, Optional[AudioSourceProtocol]] = {
            source: None for source in AudioSource
            if source != AudioSource.NONE
        }
        self._transition_lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()

        # Inactivity monitor
        self._inactivity_timeout: int = self.INACTIVITY_TIMEOUT
        self._last_activity_time: float = monotonic()
        self._inactivity_monitor_task: Optional[asyncio.Task] = None

        # Set after creation in dependencies.py (circular dependency resolution)
        self.ws_manager = None
        self.routing_service = None

    def register_plugin(self, source: AudioSource, plugin: AudioSourceProtocol) -> None:
        """Register a plugin for a specific source."""
        if source in self.plugins:
            self.plugins[source] = plugin
            logger.info(f"Plugin registered for source: {source.value}")

    def get_plugin(self, source: AudioSource) -> Optional[AudioSourceProtocol]:
        """Get plugin for a specific source."""
        return self.plugins.get(source)

    def get_plugin_metadata(self, source: AudioSource) -> Dict[str, Any]:
        """Get metadata for the active source."""
        if source == self.system_state.active_source:
            return self.system_state.metadata
        return {}

    def get_plugin_state(self, source: AudioSource) -> PluginState:
        """Get state of the active source."""
        if source == self.system_state.active_source:
            return self.system_state.plugin_state
        return PluginState.READY

    def get_state(self) -> Dict[str, Any]:
        """Return current system state as dict."""
        return self.system_state.to_dict()

    async def get_current_state(self) -> Dict[str, Any]:
        """Return current system state (async for compatibility)."""
        return self.system_state.to_dict()

    async def activate_source(self, source: AudioSource) -> bool:
        """
        Activate a source, stopping any currently active source.

        Emits:
        - Events.SOURCE_STOPPED (if stopping a source)
        - Events.SOURCE_STARTED (if starting a source)
        """
        return await self.transition_to_source(source)

    async def deactivate_source(self) -> bool:
        """Deactivate the current source."""
        return await self.transition_to_source(AudioSource.NONE)

    async def transition_to_source(self, target_source: AudioSource) -> bool:
        """Perform transition to new source with timeout."""
        async with self._transition_lock:
            logger.debug(
                "START TRANSITION: %s -> %s",
                self.system_state.active_source.value,
                target_source.value
            )

            if self.system_state.active_source == target_source and \
               self.system_state.plugin_state != PluginState.ERROR:
                logger.info(f"Already on source {target_source.value}")
                return True

            if target_source != AudioSource.NONE and target_source not in self.plugins:
                logger.error(f"No plugin registered for source: {target_source.value}")
                return False

            try:
                async with asyncio.timeout(self.TRANSITION_TIMEOUT):
                    async with self._state_lock:
                        old_source = self.system_state.active_source
                        self.system_state.transitioning = True
                        self.system_state.active_source = target_source
                        self.system_state.plugin_state = (
                            PluginState.STARTING if target_source != AudioSource.NONE
                            else PluginState.READY
                        )
                        self.system_state.metadata = {}

                    # Emit transition start via EventBus
                    await self.event_bus.emit(Events.TRANSITION_START, {
                        "from_source": old_source.value,
                        "to_source": target_source.value
                    })

                    await self.broadcast_event("system", "transition_start", {
                        "from_source": old_source.value,
                        "to_source": target_source.value,
                        "source": "system"
                    })

                    # Stop old source
                    if old_source != AudioSource.NONE:
                        await self._stop_source(old_source)

                    # Start new source
                    if target_source != AudioSource.NONE:
                        success = await self._start_source(target_source)
                        if not success:
                            raise ValueError(f"Failed to start {target_source.value}")

                    async with self._state_lock:
                        self.system_state.transitioning = False
                        # Set state to READY after successful start
                        if target_source != AudioSource.NONE:
                            self.system_state.plugin_state = PluginState.READY

                    # Emit source events via EventBus
                    if old_source != AudioSource.NONE:
                        await self.event_bus.emit(Events.SOURCE_STOPPED, {
                            "source": old_source.value
                        })

                    if target_source != AudioSource.NONE:
                        await self.event_bus.emit(Events.SOURCE_STARTED, {
                            "source": target_source.value,
                            "old_source": old_source.value
                        })

                    await self.broadcast_event("system", "transition_complete", {
                        "active_source": target_source.value,
                        "plugin_state": self.system_state.plugin_state.value,
                        "source": "system"
                    })

                    # Reset inactivity timer on source change
                    self._last_activity_time = monotonic()

                    logger.info(f"Transition completed: {target_source.value}")
                    return True

            except asyncio.TimeoutError:
                logger.error(f"Transition timeout after {self.TRANSITION_TIMEOUT}s")
                async with self._state_lock:
                    self.system_state.transitioning = False
                    self.system_state.error = "Transition timeout"

                # Broadcast error to WebSocket before emergency_stop clears state
                await self.broadcast_event("system", "error", {
                    "source": target_source.value,
                    "error": "Transition timeout",
                    "message": f"Transition timeout after {self.TRANSITION_TIMEOUT}s"
                })

                await self._emergency_stop()

                await self.event_bus.emit(Events.SOURCE_ERROR, {
                    "source": target_source.value,
                    "error": "Transition timeout"
                })
                return False

            except Exception as e:
                logger.error(f"Transition error: {e}")
                async with self._state_lock:
                    self.system_state.transitioning = False
                    self.system_state.error = str(e)

                # Broadcast error to WebSocket before emergency_stop clears state
                await self.broadcast_event("system", "error", {
                    "source": target_source.value,
                    "error": str(e),
                    "message": str(e)
                })

                await self._emergency_stop()

                await self.event_bus.emit(Events.SOURCE_ERROR, {
                    "source": target_source.value,
                    "error": str(e)
                })
                return False

    async def update_plugin_state(
        self,
        source: AudioSource,
        new_state: PluginState,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Update plugin state and emit events."""
        async with self._state_lock:
            if source != self.system_state.active_source:
                logger.debug(f"Ignoring state update from inactive source: {source.value}")
                return

            if self.system_state.transitioning:
                logger.debug(f"Ignoring state update during transition: {source.value}")
                return

            old_state = self.system_state.plugin_state
            self.system_state.plugin_state = new_state

            if metadata:
                self.system_state.metadata.update(metadata)

            if new_state == PluginState.ERROR:
                self.system_state.error = metadata.get("error") if metadata else "Unknown"
            else:
                self.system_state.error = None

            # Reset inactivity timer when plugin becomes active
            if new_state == PluginState.CONNECTED:
                self._last_activity_time = monotonic()

        # Emit via EventBus
        await self.event_bus.emit(Events.SOURCE_STATE_CHANGED, {
            "source": source.value,
            "old_state": old_state.value,
            "new_state": new_state.value,
            "metadata": metadata
        })

        await self.broadcast_event("plugin", "state_changed", {
            "source": source.value,
            "old_state": old_state.value,
            "new_state": new_state.value,
            "metadata": metadata
        })

    async def update_multiroom_state(self, enabled: bool) -> None:
        """Update multiroom state."""
        async with self._state_lock:
            old_state = self.system_state.multiroom_enabled
            self.system_state.multiroom_enabled = enabled

        await self.event_bus.emit(Events.ROUTING_MODE_CHANGED, {
            "multiroom_enabled": enabled
        })

        await self.broadcast_event("system", "state_changed", {
            "old_state": old_state,
            "new_state": enabled,
            "multiroom_changed": True,
            "multiroom_enabled": enabled,
            "source": "routing"
        })

    async def update_equalizer_effects_state(self, enabled: bool) -> None:
        """Update equalizer effects state."""
        async with self._state_lock:
            old_state = self.system_state.equalizer_effects_enabled
            self.system_state.equalizer_effects_enabled = enabled

        await self.event_bus.emit(Events.EQUALIZER_CONFIG_CHANGED, {
            "equalizer_effects_enabled": enabled
        })

        await self.broadcast_event("system", "state_changed", {
            "old_state": old_state,
            "new_state": enabled,
            "equalizer_effects_changed": True,
            "source": "equalizer"
        })

    async def refresh_active_metadata(self) -> bool:
        """Refresh metadata from the active plugin."""
        if self.system_state.active_source == AudioSource.NONE:
            return False

        plugin = self.plugins.get(self.system_state.active_source)
        if not plugin or not hasattr(plugin, '_refresh_metadata'):
            return False

        try:
            if await plugin._refresh_metadata() and hasattr(plugin, '_metadata'):
                async with self._state_lock:
                    self.system_state.metadata = plugin._metadata.copy()
                return True
        except Exception as e:
            logger.warning(f"Failed to refresh metadata: {e}")

        return False

    async def _stop_source(self, source: AudioSource) -> None:
        """Stop specified source."""
        plugin = self.plugins.get(source)
        if plugin:
            try:
                await plugin.stop()
            except Exception as e:
                logger.error(f"Error stopping {source.value}: {e}")

    async def _start_source(self, source: AudioSource) -> bool:
        """Start specified source."""
        plugin = self.plugins.get(source)
        if not plugin:
            return False

        try:
            if not getattr(plugin, '_initialized', False):
                if await plugin.initialize():
                    plugin._initialized = True
                else:
                    return False

            return await plugin.start()
        except Exception as e:
            logger.error(f"Error starting {source.value}: {e}")
            return False

    async def _emergency_stop(self) -> None:
        """Emergency stop all plugins."""
        for plugin in self.plugins.values():
            if plugin:
                try:
                    await plugin.stop()
                except Exception as e:
                    logger.error(f"Emergency stop error: {e}")

        async with self._state_lock:
            self.system_state.active_source = AudioSource.NONE
            self.system_state.plugin_state = PluginState.READY
            self.system_state.metadata = {}
            self.system_state.error = None

    # === Inactivity Monitor ===

    def start_inactivity_monitor(self, timeout: int = INACTIVITY_TIMEOUT) -> None:
        """Start the background task that deactivates idle sources."""
        self._inactivity_timeout = timeout
        if self._inactivity_monitor_task is None:
            self._inactivity_monitor_task = asyncio.create_task(
                self._monitor_inactivity()
            )
            logger.info(
                "Inactivity monitor started (timeout: %s)",
                f"{self._inactivity_timeout}s" if self._inactivity_timeout > 0 else "disabled"
            )

    async def reload_inactivity_config(self, timeout: int) -> bool:
        """Update inactivity timeout (called by settings API)."""
        self._inactivity_timeout = timeout
        self._last_activity_time = monotonic()
        logger.info(
            "Inactivity timeout updated: %s",
            f"{timeout}s" if timeout > 0 else "disabled"
        )
        return True

    async def _monitor_inactivity(self) -> None:
        """Deactivate source after inactivity timeout without CONNECTED state."""
        try:
            while True:
                await asyncio.sleep(60)

                if (
                    self._inactivity_timeout > 0
                    and self.system_state.active_source != AudioSource.NONE
                    and self.system_state.plugin_state == PluginState.READY
                    and not self.system_state.transitioning
                    and (monotonic() - self._last_activity_time) >= self._inactivity_timeout
                ):
                    source = self.system_state.active_source
                    elapsed = monotonic() - self._last_activity_time
                    logger.info(
                        "Deactivating idle source %s after %.0fs of inactivity",
                        source.value,
                        elapsed
                    )
                    await self.deactivate_source()

        except asyncio.CancelledError:
            pass

    def cleanup(self) -> None:
        """Cancel background tasks."""
        if self._inactivity_monitor_task:
            self._inactivity_monitor_task.cancel()
            self._inactivity_monitor_task = None

    # === WebSocket Broadcasting ===

    async def broadcast_event(
        self,
        category: str,
        event_type: str,
        data: Dict[str, Any]
    ) -> None:
        """
        Broadcast event to all connected WebSocket clients.

        Plugin/system events include full_state for unifiedAudioStore.
        Other categories (volume, equalizer, multiroom, settings) send
        only their specific data.
        """
        if not self.ws_manager:
            return

        event_payload = dict(data)
        if category in self._FULL_STATE_CATEGORIES:
            event_payload["full_state"] = self.system_state.to_dict()

        await self.ws_manager.broadcast_dict({
            "category": category,
            "type": event_type,
            "source": data.get("source", category),
            "data": event_payload,
            "timestamp": time.time()
        })
