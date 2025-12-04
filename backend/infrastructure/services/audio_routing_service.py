# backend/infrastructure/services/audio_routing_service.py
"""
Audio routing service for Milo - UNIFIED version with SystemAudioState as single source of truth
"""
import os
import logging
import asyncio
from typing import Dict, Any, Callable, Optional
from backend.domain.audio_state import AudioSource
from backend.infrastructure.services.systemd_manager import SystemdServiceManager

class AudioRoutingService:
    """
    Audio routing service - UNIFIED version

    IMPORTANT: This service no longer has its own state. It directly uses
    state_machine.system_state as the single source of truth for multiroom_enabled
    and equalizer_enabled. This eliminates desynchronization risks.
    """

    # Strict whitelist for command validation
    ALLOWED_MODES = frozenset(["direct", "multiroom"])
    ALLOWED_EQUALIZER = frozenset(["", "_eq"])

    def __init__(self, get_plugin_callback: Optional[Callable] = None, settings_service=None, equalizer_service=None):
        self.logger = logging.getLogger(__name__)
        self.service_manager = SystemdServiceManager()
        # REMOVED: self.state = AudioRoutingState()  # No longer needed, using state_machine.system_state
        self.get_plugin = get_plugin_callback
        self.settings_service = settings_service
        self.equalizer_service = equalizer_service
        self._initial_detection_done = False

        self.snapcast_websocket_service = None
        self.snapcast_service = None
        self.state_machine = None

        # Lock to guarantee atomicity of routing operations
        self._routing_lock = asyncio.Lock()

        # Services snapcast
        self.snapserver_service = "milo-snapserver-multiroom.service"
        self.snapclient_service = "milo-snapclient-multiroom.service"
    
    def set_snapcast_websocket_service(self, service) -> None:
        """Sets reference to SnapcastWebSocketService"""
        self.snapcast_websocket_service = service
    
    def set_snapcast_service(self, service) -> None:
        """Sets reference to SnapcastService"""
        self.snapcast_service = service
    
    def set_state_machine(self, state_machine) -> None:
        """Sets the reference to StateMachine"""
        self.state_machine = state_machine

    def set_plugin_callback(self, callback: Callable) -> None:
        """Sets the callback to access plugins"""
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
        """Accesses multiroom state in a thread-safe manner"""
        if not self.state_machine:
            return False
        async with self.state_machine._state_lock:
            return self.state_machine.system_state.multiroom_enabled

    async def _set_multiroom_state(self, value: bool) -> None:
        """Modifies multiroom state in a thread-safe manner (internal method)"""
        if self.state_machine:
            async with self.state_machine._state_lock:
                self.state_machine.system_state.multiroom_enabled = value

    async def _get_equalizer_enabled(self) -> bool:
        """Accesses equalizer state in a thread-safe manner"""
        if not self.state_machine:
            return False
        async with self.state_machine._state_lock:
            return self.state_machine.system_state.equalizer_enabled

    async def _set_equalizer_state(self, value: bool) -> None:
        """Modifies equalizer state in a thread-safe manner (internal method)"""
        if self.state_machine:
            async with self.state_machine._state_lock:
                self.state_machine.system_state.equalizer_enabled = value

    # Synchronous properties for compatibility (read-only, may be slightly out of sync)
    @property
    def multiroom_enabled(self) -> bool:
        """Accesses multiroom state (FAST READ - may be slightly out of sync)"""
        if not self.state_machine:
            return False
        return self.state_machine.system_state.multiroom_enabled

    @property
    def equalizer_enabled(self) -> bool:
        """Accesses equalizer state (FAST READ - may be slightly out of sync)"""
        if not self.state_machine:
            return False
        return self.state_machine.system_state.equalizer_enabled
    
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
                equalizer = await self.settings_service.get_setting('routing.equalizer_enabled')
                # Explicit bool conversion for defensive programming
                await self._set_multiroom_state(self._to_bool(multiroom))
                await self._set_equalizer_state(self._to_bool(equalizer))
                current_multiroom = await self._get_multiroom_enabled()
                current_equalizer = await self._get_equalizer_enabled()
                self.logger.info(f"Loaded state from settings: multiroom={current_multiroom}, equalizer={current_equalizer}")
            else:
                self.logger.warning("SettingsService not available, using defaults")
                await self._set_multiroom_state(False)
                await self._set_equalizer_state(False)

            await self._update_systemd_environment()
            self.logger.info(f"ALSA environment initialized: MULTIROOM={self.multiroom_enabled}, EQUALIZER={self.equalizer_enabled}")

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

            self._initial_detection_done = True
            current_multiroom = await self._get_multiroom_enabled()
            current_equalizer = await self._get_equalizer_enabled()
            self.logger.info(f"Routing initialized with persisted state: multiroom={current_multiroom}, equalizer={current_equalizer}")

        except Exception as e:
            self.logger.error(f"Error during initial state detection: {e}")
            await self._set_multiroom_state(False)
            await self._set_equalizer_state(False)
            await self._update_systemd_environment()
            self._initial_detection_done = True
    
    async def set_multiroom_enabled(self, enabled: bool, active_source: AudioSource = None) -> bool:
        """Enables/disables multiroom mode with early notification"""
        async with self._routing_lock:  # Guarantee atomicity of routing operations
            if not self._initial_detection_done:
                await self._detect_initial_state()

            current_state = await self._get_multiroom_enabled()
            if current_state == enabled:
                self.logger.info(f"Multiroom already {'enabled' if enabled else 'disabled'}")
                return True

            try:
                old_state = current_state
                self.logger.info(f"Changing multiroom from {old_state} to {enabled}")

                await self._set_multiroom_state(enabled)
                await self._update_systemd_environment()

                if enabled:
                    # NEW: Send event BEFORE starting services
                    if self.state_machine:
                        self.logger.info("📢 Broadcasting multiroom_enabling event")
                        await self.state_machine.broadcast_event("routing", "multiroom_enabling", {
                            "reason": "user_action"
                        })

                        # Small delay to let frontend react
                        await asyncio.sleep(0.1)
                    else:
                        self.logger.warning("⚠️ state_machine not available, cannot broadcast event")

                    success = await self._transition_to_multiroom(active_source)
                else:
                    # Send event BEFORE stopping services
                    if self.state_machine:
                        self.logger.info("📢 Broadcasting multiroom_disabling event")
                        await self.state_machine.broadcast_event("routing", "multiroom_disabling", {
                            "reason": "user_action"
                        })

                        # Small delay to let frontend react
                        await asyncio.sleep(0.1)
                    else:
                        self.logger.warning("⚠️ state_machine not available, cannot broadcast event")

                    success = await self._transition_to_direct(active_source)

                if not success:
                    await self._set_multiroom_state(old_state)
                    await self._update_systemd_environment()
                    self.logger.error(f"Failed to transition multiroom to {enabled}, reverting to {old_state}")
                    # Broadcast error event to frontend
                    if self.state_machine:
                        await self.state_machine.broadcast_event("routing", "multiroom_error", {
                            "message": f"Failed to {'enable' if enabled else 'disable'} multiroom"
                        })
                    return False

                if enabled and success:
                    await self._auto_configure_multiroom()

                if self.snapcast_websocket_service:
                    if enabled:
                        await self.snapcast_websocket_service.start_connection()
                    else:
                        await self.snapcast_websocket_service.stop_connection()

                # Broadcast multiroom_ready event after services are started
                if enabled and self.state_machine:
                    self.logger.info("📢 Broadcasting multiroom_ready event")
                    await self.state_machine.broadcast_event("routing", "multiroom_ready", {})

                # Save state via SettingsService
                if self.settings_service:
                    await self.settings_service.set_setting('routing.multiroom_enabled', enabled)

                self.logger.info(f"Multiroom state changed and saved: {enabled}")

                return True

            except Exception as e:
                await self._set_multiroom_state(old_state)
                await self._update_systemd_environment()
                self.logger.error(f"Error changing multiroom state: {e}")
                # Broadcast error event to frontend
                if self.state_machine:
                    await self.state_machine.broadcast_event("routing", "multiroom_error", {
                        "message": str(e)
                    })
                return False
    
    async def _auto_configure_multiroom(self):
        """Automatically configures all groups to Multiroom"""
        try:
            for _ in range(10):
                if await self.snapcast_service.is_available():
                    await self.snapcast_service.set_all_groups_to_multiroom()
                    self.logger.info("✅ Groups automatically configured to Multiroom")
                    return
                await asyncio.sleep(1)
            
            self.logger.warning("⚠️ Snapserver not available after 10 seconds")
            
        except Exception as e:
            self.logger.error(f"❌ Auto-configure multiroom failed: {e}")

    async def set_equalizer_enabled(self, enabled: bool, active_source: AudioSource = None) -> bool:
        """Enables/disables equalizer with save/restore of values"""
        async with self._routing_lock:  # Guarantee atomicity of routing operations
            current_state = await self._get_equalizer_enabled()
            if current_state == enabled:
                self.logger.info(f"Equalizer already {'enabled' if enabled else 'disabled'}")
                return True

            try:
                old_state = current_state
                self.logger.info(f"Changing equalizer from {old_state} to {enabled}")

                # === DEACTIVATION: Save then reset to 66 ===
                if not enabled and self.equalizer_service:
                    await self.equalizer_service.save_current_bands()
                    await self.equalizer_service.reset_all_bands(66)

                await self._set_equalizer_state(enabled)
                await self._update_systemd_environment()

                # === ACTIVATION: Restore values BEFORE restarting plugin ===
                if enabled and self.equalizer_service:
                    await self.equalizer_service.restore_saved_bands()

                if active_source and self.get_plugin:
                    plugin = self.get_plugin(active_source)
                    if plugin:
                        self.logger.info(f"Restarting plugin {active_source.value} with equalizer {'enabled' if enabled else 'disabled'}")
                        await plugin.restart()

                # Save state via SettingsService
                if self.settings_service:
                    await self.settings_service.set_setting('routing.equalizer_enabled', enabled)

                self.logger.info(f"Equalizer state changed and saved: {enabled}")

                return True

            except Exception as e:
                await self._set_equalizer_state(old_state)
                await self._update_systemd_environment()
                self.logger.error(f"Error changing equalizer state: {e}")
                return False
    
    async def _update_systemd_environment(self) -> None:
        """
        Updates ALSA environment variables via static file

        NEW: No more runtime sudo! Variables are written to
        /var/lib/milo/routing.env which is read by systemd services.
        """
        mode_value = "multiroom" if self.multiroom_enabled else "direct"
        equalizer_value = "_eq" if self.equalizer_enabled else ""

        # Strict validation
        if mode_value not in self.ALLOWED_MODES:
            raise ValueError(f"Invalid mode value: {mode_value}. Allowed: {self.ALLOWED_MODES}")

        if equalizer_value not in self.ALLOWED_EQUALIZER:
            raise ValueError(f"Invalid equalizer value: {equalizer_value}. Allowed: {self.ALLOWED_EQUALIZER}")

        environment_file = "/var/lib/milo/routing.env"

        try:
            # Atomic write of environment file
            temp_file = environment_file + ".tmp"

            with open(temp_file, 'w') as f:
                f.write("# Milo Audio Routing Environment Variables\n")
                f.write("# This file is automatically modified by Milo backend\n")
                f.write("# Do not edit manually\n\n")
                f.write(f"# Audio routing mode: \"direct\" or \"multiroom\"\n")
                f.write(f"MILO_MODE={mode_value}\n\n")
                f.write(f"# Equalizer: \"\" (disabled) or \"_eq\" (enabled)\n")
                f.write(f"MILO_EQUALIZER={equalizer_value}\n")
                f.flush()
                os.fsync(f.fileno())

            # Atomic rename
            os.replace(temp_file, environment_file)

            # Local update for compatibility
            os.environ["MILO_MODE"] = mode_value
            os.environ["MILO_EQUALIZER"] = equalizer_value

            self.logger.info(f"✅ Updated routing.env: MODE={mode_value}, EQUALIZER={equalizer_value}")

        except Exception as e:
            self.logger.error(f"Failed to update environment file: {e}")
            # Clean up temp file on failure
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except:
                pass
            raise RuntimeError(f"Failed to update environment file: {e}")
    
    async def _transition_to_multiroom(self, active_source: AudioSource = None) -> bool:
        """Transition to multiroom mode"""
        try:
            self.logger.info("Starting snapcast services")
            snapcast_success = await self._start_snapcast()
            if not snapcast_success:
                return False
            
            if active_source and self.get_plugin:
                plugin = self.get_plugin(active_source)
                if plugin:
                    self.logger.info(f"Restarting plugin {active_source.value} for multiroom mode")
                    await plugin.restart()
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error in multiroom transition: {e}")
            return False
    
    async def _transition_to_direct(self, active_source: AudioSource = None) -> bool:
        """Transition to direct mode"""
        try:
            self.logger.info("Stopping snapcast services")
            await self._stop_snapcast()
            
            if active_source and self.get_plugin:
                plugin = self.get_plugin(active_source)
                if plugin:
                    self.logger.info(f"Restarting plugin {active_source.value} for direct mode")
                    await plugin.restart()
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error in direct transition: {e}")
            return False
    
    async def _start_snapcast(self) -> bool:
        """Starts snapcast services"""
        try:
            success = await self.service_manager.start(self.snapserver_service)
            if not success:
                return False
            
            await asyncio.sleep(0.5)
            success = await self.service_manager.start(self.snapclient_service)
            return success
            
        except Exception as e:
            self.logger.error(f"Error starting snapcast: {e}")
            return False
    
    async def _stop_snapcast(self) -> None:
        """Stops snapcast services"""
        try:
            await self.service_manager.stop(self.snapclient_service)
            await self.service_manager.stop(self.snapserver_service)
        except Exception as e:
            self.logger.error(f"Error stopping snapcast: {e}")
    
    def get_state(self) -> Dict[str, bool]:
        """
        Gets current routing state from single source of truth

        NEW: Returns a dict instead of AudioRoutingState (which no longer exists)
        """
        return {
            "multiroom_enabled": self.multiroom_enabled,
            "equalizer_enabled": self.equalizer_enabled
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