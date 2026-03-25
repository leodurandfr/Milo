# backend/core/state.py
"""
Audio State Machine - single source of truth for audio state.

Manages audio source transitions and broadcasts state changes
to WebSocket clients via WebSocketManager.

Usage:
    from backend.core.state import AudioStateMachine

    state_machine = AudioStateMachine()

    # Activate a source
    await state_machine.transition_to_source(AudioSource.RADIO)
"""
import asyncio
import time
import logging
from time import monotonic
from typing import Dict, Any, Optional

from backend.core.models.audio_state import AudioSource, PluginState, SystemAudioState
from backend.core.audio_source import BaseAudioSource
from backend.shared.decorators import handle_errors

logger = logging.getLogger(__name__)


class AudioStateMachine:
    """
    Audio state machine - WebSocketManager for frontend broadcasting.
    """

    TRANSITION_TIMEOUT = 10.0
    INACTIVITY_TIMEOUT = 7200  # 2 hours in seconds

    # Only plugin/system events include full_state (used by unifiedAudioStore).
    # Other categories (volume, equalizer, multiroom, settings) send only
    # their specific data — their frontend stores don't read full_state.
    _FULL_STATE_CATEGORIES = frozenset({"plugin", "system"})

    def __init__(self):
        self.system_state = SystemAudioState()
        self.plugins: Dict[AudioSource, Optional[BaseAudioSource]] = {
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

    def register_plugin(self, source: AudioSource, plugin: BaseAudioSource) -> None:
        """Register a plugin for a specific source."""
        if source in self.plugins:
            self.plugins[source] = plugin
            logger.info(f"Plugin registered for source: {source.value}")

    def get_plugin(self, source: AudioSource) -> Optional[BaseAudioSource]:
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
        return PluginState.WAITING

    async def get_current_state(self) -> Dict[str, Any]:
        """Return current system state as dict."""
        return self.system_state.to_dict()

    async def transition_to_source(
        self,
        target_source: AudioSource,
        expected_source: Optional[AudioSource] = None
    ) -> bool:
        """Perform transition to new source with timeout.

        Args:
            target_source: The source to transition to.
            expected_source: If set, the transition is skipped when the current
                active source no longer matches (CAS guard for the inactivity
                monitor — prevents deactivating a source that a user just
                activated between the decision and the lock acquisition).
        """
        async with self._transition_lock:
            logger.debug(
                "START TRANSITION: %s -> %s",
                self.system_state.active_source.value,
                target_source.value
            )

            # CAS guard: abort if active source changed since caller's decision
            if expected_source is not None and self.system_state.active_source != expected_source:
                logger.info(
                    "Transition skipped: expected %s but active is %s",
                    expected_source.value,
                    self.system_state.active_source.value
                )
                return False

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
                            else PluginState.WAITING
                        )
                        self.system_state.metadata = {}

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
                        if target_source != AudioSource.NONE:
                            # Sync with plugin's actual post-start state
                            # (_do_start may have set CONNECTED with metadata)
                            plugin = self.plugins.get(target_source)
                            if plugin:
                                self.system_state.plugin_state = plugin.state
                                self.system_state.metadata = plugin.metadata
                            else:
                                self.system_state.plugin_state = PluginState.WAITING

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
                return False

    async def update_plugin_state(
        self,
        source: AudioSource,
        new_state: PluginState,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Update plugin state and broadcast via WebSocket."""
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
            if new_state == PluginState.ACTIVE:
                self._last_activity_time = monotonic()

        await self.broadcast_event("plugin", "state_changed", {
            "source": source.value,
            "old_state": old_state.value,
            "new_state": new_state.value,
            "metadata": metadata
        })

    async def update_multiroom_state(self, enabled: bool, silent: bool = False) -> None:
        """Update multiroom state.

        Args:
            enabled: New multiroom state
            silent: If True, skip broadcasting (used during transitions or startup
                    where an intermediate state should not be exposed to the frontend)
        """
        async with self._state_lock:
            old_state = self.system_state.multiroom_enabled
            self.system_state.multiroom_enabled = enabled

        if not silent:
            await self.broadcast_event("system", "state_changed", {
                "old_state": old_state,
                "new_state": enabled,
                "multiroom_changed": True,
                "multiroom_enabled": enabled,
                "source": "routing"
            })

    async def update_equalizer_effects_state(self, enabled: bool, silent: bool = False) -> None:
        """Update equalizer effects state.

        Args:
            enabled: New equalizer effects state
            silent: If True, skip broadcasting (used during transitions or startup
                    where an intermediate state should not be exposed to the frontend)
        """
        async with self._state_lock:
            old_state = self.system_state.equalizer_effects_enabled
            self.system_state.equalizer_effects_enabled = enabled

        if not silent:
            await self.broadcast_event("system", "state_changed", {
                "old_state": old_state,
                "new_state": enabled,
                "equalizer_effects_changed": True,
                "source": "equalizer"
            })

    @handle_errors(default=False, level='warning')
    async def refresh_active_metadata(self) -> bool:
        """Refresh metadata from the active plugin."""
        if self.system_state.active_source == AudioSource.NONE:
            return False

        plugin = self.plugins.get(self.system_state.active_source)
        if not plugin or not hasattr(plugin, '_refresh_metadata'):
            return False

        if await plugin._refresh_metadata() and hasattr(plugin, '_metadata'):
            async with self._state_lock:
                self.system_state.metadata = plugin._metadata.copy()
            return True

        return False

    @handle_errors(default=None)
    async def _stop_source(self, source: AudioSource) -> None:
        """Stop specified source."""
        plugin = self.plugins.get(source)
        if plugin:
            await plugin.stop()

    @handle_errors(default=False)
    async def _start_source(self, source: AudioSource) -> bool:
        """Start specified source."""
        plugin = self.plugins.get(source)
        if not plugin:
            return False

        if not getattr(plugin, '_initialized', False):
            if await plugin.initialize():
                plugin._initialized = True
            else:
                return False

        return await plugin.start()

    async def _emergency_stop(self) -> None:
        """Emergency stop all plugins and broadcast the reset state."""
        for plugin in self.plugins.values():
            if plugin:
                try:
                    await plugin.stop()
                except Exception as e:
                    logger.error(f"Emergency stop error: {e}")

        async with self._state_lock:
            self.system_state.active_source = AudioSource.NONE
            self.system_state.plugin_state = PluginState.WAITING
            self.system_state.metadata = {}
            self.system_state.error = None

        # Broadcast the reset state so frontend knows system is stable again
        await self.broadcast_event("system", "state_changed", {
            "source": "system",
            "reason": "emergency_stop",
        })

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
        """Deactivate source after inactivity timeout without ACTIVE state."""
        try:
            while True:
                await asyncio.sleep(60)

                # Atomic snapshot under lock
                async with self._state_lock:
                    source = self.system_state.active_source
                    plugin_state = self.system_state.plugin_state
                    transitioning = self.system_state.transitioning

                if (
                    self._inactivity_timeout > 0
                    and source != AudioSource.NONE
                    and plugin_state == PluginState.WAITING
                    and not transitioning
                    and (monotonic() - self._last_activity_time) >= self._inactivity_timeout
                ):
                    elapsed = monotonic() - self._last_activity_time
                    logger.info(
                        "Deactivating idle source %s after %.0fs of inactivity",
                        source.value,
                        elapsed
                    )
                    # CAS guard: if active source changed between snapshot
                    # and lock acquisition, transition_to_source will skip
                    await self.transition_to_source(
                        AudioSource.NONE, expected_source=source
                    )

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
        data: Dict[str, Any],
        include_full_state: bool = True,
    ) -> None:
        """
        Broadcast event to all connected WebSocket clients.

        Plugin/system events include full_state for unifiedAudioStore
        unless include_full_state=False (used for lightweight position updates).
        Other categories send only their specific data.
        """
        if not self.ws_manager:
            return

        event_payload = dict(data)
        if include_full_state and category in self._FULL_STATE_CATEGORIES:
            event_payload["full_state"] = self.system_state.to_dict()

        await self.ws_manager.broadcast_dict({
            "category": category,
            "type": event_type,
            "source": data.get("source", category),
            "data": event_payload,
            "timestamp": time.time()
        })
