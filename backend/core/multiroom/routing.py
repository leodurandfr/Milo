# backend/core/multiroom/routing.py
"""
Audio routing service for Milo - UNIFIED version with SystemAudioState as single source of truth
"""
import logging
import asyncio
import os
import time
from typing import Dict, Any, Callable, Optional, Literal
from backend.core.models.audio_state import AudioSource, PluginState
from backend.core.systemd import SystemdServiceManager
from backend.shared.decorators import handle_errors


# =============================================================================
# Routing Environment (consolidated from routing_env.py)
# =============================================================================

class RoutingEnvironment:
    """
    Manages the routing environment file for ALSA configuration.

    Environment variables written:
    - MILO_MODE: "direct" or "multiroom"
    - MILO_SNAPCLIENT_SOUNDCARD: always "camilladsp"
    - MILO_SNAPCLIENT_BUFFER_TIME: Snapclient ALSA buffer time in ms (default 80)
    - MILO_SNAPCLIENT_FRAGMENTS: Snapclient ALSA buffer fragments (default 4)
    - ROC_TARGET_LATENCY: ROC target latency (e.g., "10ms")
    - ROC_LATENCY_PROFILE: ROC latency profile (responsive/gradual/intact)
    - ROC_FRAME_LENGTH: ROC frame length (e.g., "4ms")

    Note: MILO_EQUALIZER was removed - CamillaDSP is always in the audio path.
    Equalizer effects (EQ, compressor, loudness) are controlled via CamillaDSP bypass,
    not via ALSA routing.
    """

    ENVIRONMENT_FILE = "/var/lib/milo/routing.env"
    ALLOWED_MODES = frozenset(["direct", "multiroom"])
    ALLOWED_LATENCY_PROFILES = frozenset(["responsive", "gradual", "intact"])
    ALLOWED_FRAME_LENGTHS = frozenset([2, 4, 7, 8, 12])

    # Default ROC settings (aligned with roc-streaming official defaults)
    DEFAULT_ROC_CONFIG = {
        "target_latency_ms": 200,
        "latency_profile": "responsive",
        "frame_length_ms": 7
    }

    # Default Snapclient settings
    DEFAULT_SNAPCLIENT_CONFIG = {
        "buffer_time": 80,
        "fragments": 4
    }

    # Class-level ROC config cache (updated via update_roc_config)
    _roc_config = None

    # Class-level Snapclient config cache
    _snapclient_config = None

    @classmethod
    def update(cls, multiroom_enabled: bool, roc_config: Dict[str, Any] = None, snapclient_config: Dict[str, Any] = None) -> None:
        """
        Update routing environment file atomically.

        Args:
            multiroom_enabled: Whether multiroom mode is active
            roc_config: Optional ROC configuration dict with:
                - target_latency_ms: int (5-500)
                - latency_profile: str (responsive/gradual/intact)
                - frame_length_ms: int (2/4/7/8/12)
            snapclient_config: Optional Snapclient configuration dict with:
                - buffer_time: int (10-200)
                - fragments: int (2-8)
        """
        logger = logging.getLogger(__name__)
        mode_value = "multiroom" if multiroom_enabled else "direct"

        if mode_value not in cls.ALLOWED_MODES:
            raise ValueError(f"Invalid mode value: {mode_value}")

        temp_file = cls.ENVIRONMENT_FILE + ".tmp"

        try:
            snapclient_soundcard = "camilladsp"

            # Use provided ROC config, cached config, or defaults
            roc = roc_config or cls._roc_config or cls.DEFAULT_ROC_CONFIG
            target_latency = roc.get("target_latency_ms", 200)
            latency_profile = roc.get("latency_profile", "responsive")
            frame_length = roc.get("frame_length_ms", 7)

            # Validate ROC settings
            if latency_profile not in cls.ALLOWED_LATENCY_PROFILES:
                latency_profile = "responsive"
            if frame_length not in cls.ALLOWED_FRAME_LENGTHS:
                frame_length = 7
            target_latency = max(5, min(500, target_latency))

            # Use provided Snapclient config, cached config, or defaults
            snapclient = snapclient_config or cls._snapclient_config or cls.DEFAULT_SNAPCLIENT_CONFIG
            buffer_time = snapclient.get("buffer_time", 80)
            fragments = snapclient.get("fragments", 4)

            # Validate Snapclient settings
            buffer_time = max(10, min(200, buffer_time))
            fragments = max(2, min(8, fragments))

            with open(temp_file, 'w') as f:
                f.write("# Milo Audio Routing Environment Variables\n")
                f.write("# This file is automatically modified by Milo backend\n")
                f.write("# Do not edit manually\n\n")
                f.write(f"MILO_MODE={mode_value}\n")
                f.write(f"MILO_SNAPCLIENT_SOUNDCARD={snapclient_soundcard}\n")
                f.write("\n# Snapclient ALSA Buffer Configuration\n")
                f.write(f"MILO_SNAPCLIENT_BUFFER_TIME={buffer_time}\n")
                f.write(f"MILO_SNAPCLIENT_FRAGMENTS={fragments}\n")
                f.write("\n# ROC Streaming Configuration\n")
                f.write(f"ROC_TARGET_LATENCY={target_latency}ms\n")
                f.write(f"ROC_LATENCY_PROFILE={latency_profile}\n")
                f.write(f"ROC_FRAME_LENGTH={frame_length}ms\n")
                f.flush()
                os.fsync(f.fileno())

            os.replace(temp_file, cls.ENVIRONMENT_FILE)
            os.environ["MILO_MODE"] = mode_value

            logger.info(f"Updated routing.env: MODE={mode_value}, SNAPCLIENT buffer_time={buffer_time}ms/fragments={fragments}, ROC latency={target_latency}ms/{latency_profile}")

        except Exception as e:
            logger.error(f"Failed to update environment file: {e}")
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except Exception:
                pass
            raise RuntimeError(f"Failed to update environment file: {e}")

    @classmethod
    def update_roc_config(cls, roc_config: Dict[str, Any]) -> None:
        """
        Update ROC configuration and regenerate routing.env.

        Args:
            roc_config: ROC configuration dict
        """
        cls._roc_config = roc_config
        # Read current mode and regenerate file with new ROC config
        current_mode = cls.get_mode()
        cls.update(current_mode == "multiroom", roc_config, cls._snapclient_config)

    @classmethod
    def update_snapclient_config(cls, snapclient_config: Dict[str, Any]) -> None:
        """
        Update Snapclient configuration and regenerate routing.env.

        Args:
            snapclient_config: Snapclient configuration dict with:
                - buffer_time: int (10-200)
                - fragments: int (2-8)
        """
        cls._snapclient_config = snapclient_config
        # Read current mode and regenerate file with new Snapclient config
        current_mode = cls.get_mode()
        cls.update(current_mode == "multiroom", cls._roc_config, snapclient_config)

    @classmethod
    def get_mode(cls) -> Literal["direct", "multiroom"]:
        """Get current routing mode from environment."""
        return os.environ.get("MILO_MODE", "direct")

class AudioRoutingService:
    """
    Audio routing service - UNIFIED version

    IMPORTANT: This service no longer has its own state. It directly uses
    state_machine.system_state as the single source of truth for multiroom_enabled
    and equalizer_effects_enabled. This eliminates desynchronization risks.
    """

    def __init__(self, get_plugin_callback: Optional[Callable] = None, settings_service=None, systemd_manager=None):
        self.logger = logging.getLogger(__name__)
        self.service_manager = systemd_manager
        self.get_plugin = get_plugin_callback
        self.settings_service = settings_service
        self._initial_detection_done = False

        self.snapcast_websocket_service = None
        self.snapcast_service = None
        self.state_machine = None
        self.camilladsp_service = None
        self.volume_service = None

        # Lock to guarantee atomicity of routing operations
        self._routing_lock = asyncio.Lock()

        # Services snapcast
        self.snapserver_service = "milo-snapserver-multiroom.service"
        self.snapclient_service = "milo-snapclient-multiroom.service"
    
    def set_snapcast_websocket_service(self, service) -> None:
        """Set SnapcastWebSocketService dependency."""
        self.snapcast_websocket_service = service

    def set_snapcast_service(self, service) -> None:
        """Set SnapcastService dependency."""
        self.snapcast_service = service

    def set_state_machine(self, state_machine) -> None:
        """Set state machine for event broadcasting."""
        self.state_machine = state_machine

    def set_camilladsp_service(self, service) -> None:
        """Set CamillaDSPService dependency."""
        self.camilladsp_service = service

    def set_volume_service(self, service) -> None:
        """Set VolumeService dependency."""
        self.volume_service = service

    def set_plugin_callback(self, callback: Callable) -> None:
        """Set callback to access audio source plugins."""
        if not self.get_plugin:
            self.get_plugin = callback

    # === Helper methods ===

    @staticmethod
    def _to_bool(value) -> bool:
        """Convert various types to boolean safely."""
        if value is None:
            return False
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ('true', '1', 'yes', 'on', 'enabled')
        return bool(value)

    # === Properties to access unified state (state_machine.system_state) ===

    async def _get_multiroom_enabled(self) -> bool:
        """Read multiroom state (safe in asyncio single-threaded)."""
        if not self.state_machine:
            return False
        return self.state_machine.system_state.multiroom_enabled

    async def _set_multiroom_state(self, value: bool, silent: bool = True) -> None:
        """Set multiroom state via state_machine public method."""
        if self.state_machine:
            await self.state_machine.update_multiroom_state(value, silent=silent)

    async def _get_equalizer_effects_enabled(self) -> bool:
        """Read equalizer effects state (safe in asyncio single-threaded)."""
        if not self.state_machine:
            return False
        return self.state_machine.system_state.equalizer_effects_enabled

    async def _set_equalizer_effects_state(self, value: bool, silent: bool = True) -> None:
        """Set equalizer effects state via state_machine public method."""
        if self.state_machine:
            await self.state_machine.update_equalizer_effects_state(value, silent=silent)

    # Synchronous properties for compatibility (read-only, may be slightly out of sync)
    @property
    def multiroom_enabled(self) -> bool:
        """Accesses multiroom state (FAST READ - may be slightly out of sync)"""
        if not self.state_machine:
            return False
        return self.state_machine.system_state.multiroom_enabled

    @property
    def equalizer_effects_enabled(self) -> bool:
        """Accesses equalizer effects state (FAST READ - may be slightly out of sync)"""
        if not self.state_machine:
            return False
        return self.state_machine.system_state.equalizer_effects_enabled
    
    async def initialize(self) -> None:
        """Initializes service state"""
        if not self._initial_detection_done:
            await self._detect_initial_state()
    
    async def _detect_initial_state(self):
        """Initializes and detects initial state"""
        try:
            self.logger.info("Initializing routing state with persistence...")

            # Load state from SettingsService
            if self.settings_service:
                multiroom = await self.settings_service.get_setting('routing.multiroom_enabled')
                equalizer_effects = await self.settings_service.get_setting('equalizer.effects_enabled')
                await self._set_multiroom_state(self._to_bool(multiroom))
                await self._set_equalizer_effects_state(self._to_bool(equalizer_effects))
                self.logger.info(f"Loaded state from settings: multiroom={self.multiroom_enabled}, equalizer_effects={self.equalizer_effects_enabled}")
            else:
                self.logger.warning("SettingsService not available, using defaults")
                await self._set_multiroom_state(False)
                await self._set_equalizer_effects_state(False)

            await self._update_systemd_environment()
            await self._sync_snapcast_state()
            await self._initialize_camilladsp()

            self._initial_detection_done = True
            self.logger.info(f"Routing initialized: multiroom={self.multiroom_enabled}, equalizer_effects={self.equalizer_effects_enabled}")

            if self.multiroom_enabled:
                asyncio.create_task(self._delayed_multiroom_sync())

        except Exception as e:
            # Do NOT reset multiroom/equalizer state here — routing.env was already
            # written correctly from settings.json above. Resetting would overwrite
            # MILO_MODE=multiroom with MILO_MODE=direct, causing ALSA device conflicts.
            self.logger.error(f"Error during initial state detection: {e}")
            self._initial_detection_done = True

    async def _sync_snapcast_state(self) -> None:
        """Reconcile running Snapcast services with persisted multiroom state."""
        snapcast_status = await self.get_snapcast_status()
        services_running = snapcast_status.get("multiroom_available", False)

        if self.multiroom_enabled and not services_running:
            self.logger.info("Persisted state requires multiroom, starting snapcast services")
            await self._start_snapcast()
        elif not self.multiroom_enabled and services_running:
            self.logger.info("Persisted state requires direct mode, stopping snapcast services")
            await self._stop_snapcast()
        else:
            mode = "multiroom" if self.multiroom_enabled else "direct"
            self.logger.info(f"Snapcast services already in correct state for {mode} mode")

    async def _initialize_camilladsp(self) -> None:
        """Ensure CamillaDSP is running, connected, and effects state is applied."""
        # CamillaDSP ALWAYS runs - volume is always controlled via DSP
        camilladsp_running = await self.service_manager.is_active("milo-camilladsp.service")
        if not camilladsp_running:
            self.logger.info("Starting CamillaDSP service (always required for volume control)")
            await self.service_manager.start("milo-camilladsp.service")
            await asyncio.sleep(1.0)  # Give daemon time to start

        if self.camilladsp_service and not self.camilladsp_service.connected:
            connected = await self.camilladsp_service.connect()
            if connected:
                self.logger.info("Backend connected to CamillaDSP daemon")
                current_equalizer_effects = await self._get_equalizer_effects_enabled()
                if current_equalizer_effects:
                    self.logger.info("Equalizer effects enabled, restoring from settings")
                    await self.camilladsp_service.restore_effects()
                else:
                    self.logger.info("Equalizer effects disabled, bypassing all effects")
                    await self.camilladsp_service.bypass_effects()
            else:
                self.logger.warning("Failed to connect to CamillaDSP daemon on startup")

    @handle_errors(default=None)
    async def _delayed_multiroom_sync(self):
        """Sync client volumes from equalizer after startup delay (ensures all services ready)."""
        self.logger.info(f"[{time.time():.3f}] DELAYED_SYNC: Waiting 3s before startup sync...")
        # Wait for all services to be fully initialized
        await asyncio.sleep(3.0)

        # Check multiroom is still enabled
        if not await self._get_multiroom_enabled():
            self.logger.info(f"[{time.time():.3f}] DELAYED_SYNC: Multiroom disabled, skipping sync")
            return

        # Sync volumes from equalizer
        if self.volume_service:
            self.logger.info(f"[{time.time():.3f}] DELAYED_SYNC: Starting sync_all_clients_from_equalizer")
            await self.volume_service.sync_all_clients_from_equalizer()
            self.logger.info(f"[{time.time():.3f}] DELAYED_SYNC: sync_all_clients_from_equalizer complete")
        else:
            self.logger.warning("VolumeService not available for equalizer sync")

    async def _guarded_state_transition(
        self,
        get_fn,
        set_fn,
        desired: bool,
        operation_name: str,
        body_fn,
    ) -> bool:
        """
        Execute a state transition with lock, idempotency check, and rollback.

        Args:
            get_fn: async () -> bool — read current state
            set_fn: async (bool) -> None — set state + any side effects (e.g., systemd env)
            desired: Target state value
            operation_name: For logging (e.g., "multiroom", "equalizer_effects")
            body_fn: async (bool) -> bool — the transition body, returns success
        """
        async with self._routing_lock:
            current = await get_fn()
            if current == desired:
                self.logger.info(f"{operation_name} already {'enabled' if desired else 'disabled'}")
                return True

            old_state = current
            try:
                await set_fn(desired)
                success = await body_fn(desired)
                if not success:
                    await set_fn(old_state)
                    self.logger.error(f"Failed to transition {operation_name} to {desired}, reverting to {old_state}")
                    return False
                return True
            except Exception as e:
                await set_fn(old_state)
                self.logger.error(f"Error changing {operation_name} state: {e}")
                return False

    async def set_multiroom_enabled(self, enabled: bool, active_source: AudioSource = None) -> bool:
        """Enables/disables multiroom mode with early notification"""
        if not self._initial_detection_done:
            await self._detect_initial_state()

        async def set_multiroom_with_env(value: bool) -> None:
            await self._set_multiroom_state(value)
            await self._update_systemd_environment()

        async def body(enabled: bool) -> bool:
            self.logger.info(f"Changing multiroom to {enabled}")
            await self._broadcast_transition_event(enabled)

            if enabled:
                success = await self._transition_to_multiroom(active_source)
            else:
                success = await self._transition_to_direct(active_source)

            if not success:
                return False

            await self._post_transition_setup(enabled)
            self.logger.info(f"Multiroom state changed and saved: {enabled}")
            return True

        success = await self._guarded_state_transition(
            self._get_multiroom_enabled, set_multiroom_with_env,
            enabled, "multiroom", body,
        )
        # Broadcast final state after successful transition
        if success and self.state_machine:
            await self.state_machine.update_multiroom_state(enabled)
        return success

    async def _broadcast_transition_event(self, enabled: bool) -> None:
        """Broadcast pre-transition event to let frontend react."""
        if not self.state_machine:
            self.logger.warning("state_machine not available, cannot broadcast event")
            return
        event_type = "multiroom_enabling" if enabled else "multiroom_disabling"
        self.logger.info(f"Broadcasting {event_type} event")
        await self.state_machine.broadcast_event("routing", event_type, {"reason": "user_action"})
        await asyncio.sleep(0.1)  # Let frontend react

    async def _post_transition_setup(self, enabled: bool) -> None:
        """Handle WebSocket lifecycle, volume sync, and settings persistence after transition.

        Exceptions propagate to caller for proper rollback handling.
        """
        # WebSocket connection lifecycle
        if self.snapcast_websocket_service:
            if enabled:
                await self.snapcast_websocket_service.start_connection()
            else:
                await self.snapcast_websocket_service.stop_connection()

        # Wait for WebSocket readiness
        if enabled and self.snapcast_websocket_service:
            self.logger.info("Waiting for Snapcast WebSocket to be ready...")
            ws_ready = await self.snapcast_websocket_service.wait_for_ready(timeout=15.0)
            if ws_ready:
                self.logger.info("Snapcast WebSocket is ready")
            else:
                self.logger.warning("Snapcast WebSocket not ready after timeout, proceeding anyway")

        # Update volume mode and push to clients
        if self.state_machine:
            target_volume = None
            if self.volume_service:
                target_volume = await self.volume_service.update_volume_mode(enabled)

            if enabled:
                if self.volume_service and target_volume is not None:
                    self.logger.info(f"Pushing volume ({target_volume:.1f}dB) to all clients...")
                    await self.volume_service.push_volume_to_all_clients(target_volume)
                self.logger.info("Broadcasting multiroom_ready event")
                await self.state_machine.broadcast_event("routing", "multiroom_ready", {})

        # Persist setting
        if self.settings_service:
            await self.settings_service.set_setting('routing.multiroom_enabled', enabled)
    
    async def set_equalizer_effects_enabled(self, enabled: bool, active_source: AudioSource = None) -> bool:
        """
        Enables/disables equalizer effects (not the service itself).

        CamillaDSP service stays ALWAYS running. This toggle only controls:
        - EQ filters (enabled/bypassed)
        - Compressor (enabled/bypassed)
        - Loudness (enabled/bypassed)

        Volume control via CamillaDSP is ALWAYS active regardless of this setting.
        """
        async def body(enabled: bool) -> bool:
            self.logger.info(f"{'Enabling' if enabled else 'Bypassing'} Equalizer effects")

            success = True
            if self.camilladsp_service:
                if enabled:
                    success = await self.camilladsp_service.restore_effects()
                    self.logger.info("Equalizer effects restored from settings" if success
                                     else "Failed to restore Equalizer effects")
                else:
                    success = await self.camilladsp_service.bypass_effects()
                    self.logger.info("Equalizer effects bypassed (volume unchanged)" if success
                                     else "Failed to bypass Equalizer effects")

                if not success:
                    return False

            if self.state_machine:
                await self.state_machine.broadcast_event("equalizer", "enabled_changed", {
                    "enabled": enabled,
                    "effects_bypassed": not enabled,
                })

            if self.settings_service:
                await self.settings_service.set_setting('equalizer.effects_enabled', enabled)

            self.logger.info(f"Equalizer effects {'enabled' if enabled else 'bypassed'}")
            return True

        success = await self._guarded_state_transition(
            self._get_equalizer_effects_enabled, self._set_equalizer_effects_state,
            enabled, "equalizer_effects", body,
        )
        # Broadcast final state after successful transition
        if success and self.state_machine:
            await self.state_machine.update_equalizer_effects_state(enabled)
        return success
    
    async def _update_systemd_environment(self) -> None:
        """Updates ALSA environment variables via static routing.env file."""
        RoutingEnvironment.update(self.multiroom_enabled)
    
    async def _transition_to_multiroom(self, active_source: AudioSource = None) -> bool:
        """Transition to multiroom mode."""
        return await self._transition("multiroom", active_source)

    async def _transition_to_direct(self, active_source: AudioSource = None) -> bool:
        """Transition to direct mode."""
        return await self._transition("direct", active_source)

    async def _transition(
        self,
        target_mode: Literal["direct", "multiroom"],
        active_source: AudioSource = None,
    ) -> bool:
        """
        Unified transition to target routing mode.

        Acquires state_machine._transition_lock to prevent race conditions
        with concurrent source transitions (transition_to_source).

        Args:
            target_mode: Target routing mode ("direct" or "multiroom")
            active_source: Currently active audio source (if any)

        Returns:
            True if transition successful, False otherwise
        """
        if not self.state_machine:
            self.logger.error("State machine not available for routing transition")
            return False

        try:
            plugin = None

            if active_source and self.get_plugin:
                plugin = self.get_plugin(active_source)

            # Acquire transition lock to prevent concurrent plugin lifecycle
            # operations with transition_to_source(). Lock order is always:
            # _routing_lock (held by caller) -> _transition_lock (acquired here)
            async with self.state_machine._transition_lock:

                # Step 1: Notify STARTING state to show loading UI
                if plugin:
                    await self.state_machine.update_plugin_state(
                        source=active_source,
                        new_state=PluginState.STARTING,
                        metadata={"reason": "routing_change"}
                    )

                # Step 2: Stop plugin FIRST to release ALSA device before routing change
                # This is critical: in direct mode, the plugin holds camilladsp device
                # which snapclient needs in multiroom mode
                if plugin:
                    self.logger.info(f"Stopping plugin {active_source.value} to release ALSA device")
                    await plugin.stop()
                    await asyncio.sleep(0.5)  # Wait for ALSA to release

                # Step 3: Start/stop Snapcast services based on target mode
                if target_mode == "multiroom":
                    self.logger.info("Starting snapcast services")
                    snapcast_success = await self._start_snapcast()
                    if not snapcast_success:
                        # Try to restart plugin even if Snapcast failed
                        if plugin:
                            self.logger.info(f"Snapcast failed, restarting plugin {active_source.value}")
                            await plugin.start()
                        return False
                else:
                    await self._stop_snapcast()

                # Step 4: Restart plugin with new routing
                if plugin:
                    mode_label = "multiroom" if target_mode == "multiroom" else "direct"
                    self.logger.info(f"Starting plugin {active_source.value} for {mode_label} mode")
                    start_success = await plugin.start()
                    if not start_success:
                        self.logger.error(f"Plugin {active_source.value} start failed after {mode_label} transition")

            return True

        except Exception as e:
            self.logger.error(f"Error in {target_mode} transition: {e}")
            return False
    
    @handle_errors(default=False)
    async def _start_snapcast(self) -> bool:
        """Starts snapcast services"""
        success = await self.service_manager.start(self.snapserver_service)
        if not success:
            return False

        await asyncio.sleep(0.5)
        success = await self.service_manager.start(self.snapclient_service)
        return success
    
    @handle_errors(default=None)
    async def _stop_snapcast(self) -> None:
        """Stops snapcast services"""
        await self.service_manager.stop(self.snapclient_service)
        await self.service_manager.stop(self.snapserver_service)
    
    def get_state(self) -> Dict[str, bool]:
        """
        Gets current routing state from single source of truth

        NEW: Returns a dict instead of AudioRoutingState (which no longer exists)
        """
        return {
            "multiroom_enabled": self.multiroom_enabled,
            "equalizer_effects_enabled": self.equalizer_effects_enabled
        }
    
    async def get_snapcast_status(self) -> Dict[str, Any]:
        """Gets snapcast services status"""
        try:
            server_active = await self.service_manager.is_active(self.snapserver_service)
            client_active = await self.service_manager.is_active(self.snapclient_service)
            
            return {
                "server_active": server_active,
                "client_active": client_active,
                "multiroom_available": server_active and client_active
            }
        except Exception as e:
            self.logger.error(f"Error getting snapcast status: {e}")
            return {"server_active": False, "client_active": False, "multiroom_available": False}
    
    async def get_available_services(self) -> Dict[str, bool]:
        """Gets list of available services"""
        services_status = {}
        
        services_to_check = [
            "milo-spotify.service", "milo-mac.service", 
            "milo-bluealsa-aplay.service", self.snapserver_service, self.snapclient_service
        ]
        
        for service in services_to_check:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "systemctl", "list-unit-files", service,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
                )
                stdout, _ = await proc.communicate()
                
                exists = proc.returncode == 0 and service in stdout.decode()
                is_active = False
                
                if exists:
                    is_active = await self.service_manager.is_active(service)
                
                services_status[service] = {"exists": exists, "active": is_active}
                
            except Exception as e:
                self.logger.error(f"Error checking service {service}: {e}")
                services_status[service] = {"exists": False, "active": False, "error": str(e)}
        
        return services_status