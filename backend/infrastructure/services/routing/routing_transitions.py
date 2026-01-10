# backend/infrastructure/services/routing/routing_transitions.py
"""
Routing transition logic for switching between direct and multiroom modes.

This module consolidates the formerly duplicated _transition_to_multiroom()
and _transition_to_direct() methods into a single unified transition flow.
"""
import asyncio
import logging
from typing import Dict, Any, Optional, Callable, Literal

from backend.domain.audio_state import AudioSource, PluginState

logger = logging.getLogger(__name__)


class RoutingTransitions:
    """
    Handles routing mode transitions between direct and multiroom.

    Transition flow:
    1. Save current playback state
    2. Notify STARTING state to show loading UI
    3. Start/stop Snapcast services based on target mode
    4. Restart plugin with new routing
    5. Resume playback if it was active
    """

    def __init__(
        self,
        get_plugin: Optional[Callable] = None,
        state_machine=None,
        start_snapcast: Callable = None,
        stop_snapcast: Callable = None,
    ):
        """
        Initialize transitions handler.

        Args:
            get_plugin: Callback to get plugin instance by AudioSource
            state_machine: Reference to state machine for state updates
            start_snapcast: Async callable to start snapcast services
            stop_snapcast: Async callable to stop snapcast services
        """
        self.get_plugin = get_plugin
        self.state_machine = state_machine
        self._start_snapcast = start_snapcast
        self._stop_snapcast = stop_snapcast

    def set_callbacks(
        self,
        get_plugin: Callable = None,
        state_machine=None,
        start_snapcast: Callable = None,
        stop_snapcast: Callable = None,
    ) -> None:
        """Update callbacks after initialization (for circular dependency resolution)."""
        if get_plugin:
            self.get_plugin = get_plugin
        if state_machine:
            self.state_machine = state_machine
        if start_snapcast:
            self._start_snapcast = start_snapcast
        if stop_snapcast:
            self._stop_snapcast = stop_snapcast

    async def transition(
        self,
        target_mode: Literal["direct", "multiroom"],
        active_source: AudioSource = None,
    ) -> bool:
        """
        Unified transition to target routing mode.

        Args:
            target_mode: Target routing mode ("direct" or "multiroom")
            active_source: Currently active audio source (if any)

        Returns:
            True if transition successful, False otherwise
        """
        try:
            plugin = None
            was_playing = False
            playback_metadata = {}

            if active_source and self.get_plugin:
                plugin = self.get_plugin(active_source)

            # Step 1: Save current playback state
            if plugin and self.state_machine:
                was_playing, playback_metadata = self._capture_playback_state(active_source)

            # Step 2: Notify STARTING state to show loading UI
            if plugin and self.state_machine:
                await self.state_machine.update_plugin_state(
                    source=active_source,
                    new_state=PluginState.STARTING,
                    metadata={"reason": "routing_change"}
                )

            # Step 3: Stop plugin FIRST to release ALSA device before routing change
            # This is critical: in direct mode, the plugin holds camilladsp device
            # which snapclient needs in multiroom mode
            if plugin:
                logger.info(f"Stopping plugin {active_source.value} to release ALSA device")
                await plugin.stop()
                await asyncio.sleep(0.5)  # Wait for ALSA to release

            # Step 4: Start/stop Snapcast services based on target mode
            if target_mode == "multiroom":
                snapcast_success = await self._handle_multiroom_snapcast()
                if not snapcast_success:
                    # Try to restart plugin even if Snapcast failed
                    if plugin:
                        logger.info(f"Snapcast failed, restarting plugin {active_source.value}")
                        await plugin.start()
                    return False
            else:
                await self._stop_snapcast()

            # Step 5: Restart plugin with new routing
            if plugin:
                await self._restart_plugin(plugin, active_source, target_mode)

            # Step 5: Resume playback if it was active
            if was_playing and plugin and playback_metadata:
                await asyncio.sleep(2.0)  # Wait for plugin to be ready
                await self._resume_playback(active_source, plugin, playback_metadata)

            return True

        except Exception as e:
            logger.error(f"Error in {target_mode} transition: {e}")
            return False

    def _capture_playback_state(self, active_source: AudioSource) -> tuple[bool, Dict[str, Any]]:
        """Capture current playback state before transition."""
        current_state = self.state_machine.system_state.plugin_state
        current_metadata = self.state_machine.system_state.metadata.copy()
        was_playing = current_state == PluginState.CONNECTED or current_metadata.get("is_playing", False)

        if was_playing:
            logger.info(f"Saving playback state: {active_source.value} was playing")
            return True, current_metadata

        return False, {}

    async def _handle_multiroom_snapcast(self) -> bool:
        """Start snapcast services for multiroom mode."""
        logger.info("Starting snapcast services")
        return await self._start_snapcast()

    async def _restart_plugin(
        self,
        plugin,
        active_source: AudioSource,
        target_mode: str,
    ) -> None:
        """
        Restart plugin with new routing configuration.

        Note: The plugin was already stopped in the main transition() method,
        so we use start() instead of restart() to bring it back up with all
        underlying services.
        """
        mode_label = "multiroom" if target_mode == "multiroom" else "direct"

        # Plugin was already stopped in transition(), use start() to bring it back up
        logger.info(f"Starting plugin {active_source.value} for {mode_label} mode")
        start_success = await plugin.start()

        if not start_success:
            logger.error(f"Plugin {active_source.value} start failed after {mode_label} transition")
            if self.state_machine:
                await self.state_machine.broadcast_event("routing", "plugin_restart_failed", {
                    "source": active_source.value,
                    "message": f"Failed to start {active_source.value} after {mode_label} transition"
                })

    async def _resume_playback(
        self,
        source: AudioSource,
        plugin,
        metadata: Dict[str, Any],
    ) -> None:
        """Resume playback after routing change."""
        try:
            if source == AudioSource.RADIO:
                station_id = metadata.get("station_id")
                if station_id:
                    logger.info(f"Resuming radio playback: {metadata.get('station_name', station_id)}")
                    result = await plugin.handle_command("play_station", {"station_id": station_id})
                    logger.info(f"Resume result: {result}")
            # Podcast: playback stops cleanly during routing change, user can restart manually
            # Spotify and Bluetooth manage their own playback state
        except Exception as e:
            logger.warning(f"Could not resume playback for {source.value}: {e}")
