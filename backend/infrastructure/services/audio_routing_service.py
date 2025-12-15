# backend/infrastructure/services/audio_routing_service.py
"""
Audio routing service for Milo - UNIFIED version with SystemAudioState as single source of truth
"""
import os
import logging
import asyncio
from typing import Dict, Any, Callable, Optional
from backend.domain.audio_state import AudioSource, PluginState
from backend.infrastructure.services.systemd_manager import SystemdServiceManager

class AudioRoutingService:
    """
    Audio routing service - UNIFIED version

    IMPORTANT: This service no longer has its own state. It directly uses
    state_machine.system_state as the single source of truth for multiroom_enabled
    and dsp_effects_enabled. This eliminates desynchronization risks.
    """

    # Strict whitelist for command validation
    ALLOWED_MODES = frozenset(["direct", "multiroom"])
    ALLOWED_EQUALIZER = frozenset(["", "_eq"])

    def __init__(self, get_plugin_callback: Optional[Callable] = None, settings_service=None):
        self.logger = logging.getLogger(__name__)
        self.service_manager = SystemdServiceManager()
        # REMOVED: self.state = AudioRoutingState()  # No longer needed, using state_machine.system_state
        self.get_plugin = get_plugin_callback
        self.settings_service = settings_service
        self._initial_detection_done = False

        self.snapcast_websocket_service = None
        self.snapcast_service = None
        self.state_machine = None
        self.camilladsp_service = None

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

    def set_camilladsp_service(self, service) -> None:
        """Sets reference to CamillaDSPService for connect/disconnect management"""
        self.camilladsp_service = service

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

    async def _get_dsp_effects_enabled(self) -> bool:
        """Accesses DSP effects state in a thread-safe manner"""
        if not self.state_machine:
            return False
        async with self.state_machine._state_lock:
            return self.state_machine.system_state.dsp_effects_enabled

    async def _set_dsp_effects_state(self, value: bool) -> None:
        """Modifies DSP effects state in a thread-safe manner (internal method)"""
        if self.state_machine:
            async with self.state_machine._state_lock:
                self.state_machine.system_state.dsp_effects_enabled = value

    # Synchronous properties for compatibility (read-only, may be slightly out of sync)
    @property
    def multiroom_enabled(self) -> bool:
        """Accesses multiroom state (FAST READ - may be slightly out of sync)"""
        if not self.state_machine:
            return False
        return self.state_machine.system_state.multiroom_enabled

    @property
    def dsp_effects_enabled(self) -> bool:
        """Accesses DSP effects state (FAST READ - may be slightly out of sync)"""
        if not self.state_machine:
            return False
        return self.state_machine.system_state.dsp_effects_enabled
    
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
                # Support both new and legacy setting key
                dsp_effects = await self.settings_service.get_setting('dsp.effects_enabled')
                if dsp_effects is None:
                    dsp_effects = await self.settings_service.get_setting('dsp.enabled')
                # Explicit bool conversion for defensive programming
                await self._set_multiroom_state(self._to_bool(multiroom))
                await self._set_dsp_effects_state(self._to_bool(dsp_effects))
                current_multiroom = await self._get_multiroom_enabled()
                current_dsp_effects = await self._get_dsp_effects_enabled()
                self.logger.info(f"Loaded state from settings: multiroom={current_multiroom}, dsp_effects={current_dsp_effects}")
            else:
                self.logger.warning("SettingsService not available, using defaults")
                await self._set_multiroom_state(False)
                await self._set_dsp_effects_state(False)

            await self._update_systemd_environment()
            self.logger.info(f"ALSA environment initialized: MULTIROOM={self.multiroom_enabled}, DSP_EFFECTS={self.dsp_effects_enabled}")

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

            # CamillaDSP ALWAYS runs - volume is always controlled via DSP
            # The dsp.enabled setting only controls whether effects are bypassed
            camilladsp_running = await self.service_manager.is_active("milo-camilladsp.service")
            if not camilladsp_running:
                self.logger.info("Starting CamillaDSP service (always required for volume control)")
                await self.service_manager.start("milo-camilladsp.service")
                await asyncio.sleep(1.0)  # Give daemon time to start

            # Connect to CamillaDSP daemon
            if self.camilladsp_service and not self.camilladsp_service.connected:
                connected = await self.camilladsp_service.connect()
                if connected:
                    self.logger.info("Backend connected to CamillaDSP daemon")

                    # Apply effect state based on dsp.effects_enabled setting
                    current_dsp_effects = await self._get_dsp_effects_enabled()
                    if current_dsp_effects:
                        self.logger.info("DSP effects enabled, restoring from settings")
                        await self.camilladsp_service.restore_effects()
                    else:
                        self.logger.info("DSP effects disabled, bypassing all effects")
                        await self.camilladsp_service.bypass_effects()
                else:
                    self.logger.warning("Failed to connect to CamillaDSP daemon on startup")

            self._initial_detection_done = True
            current_multiroom = await self._get_multiroom_enabled()
            current_dsp_effects = await self._get_dsp_effects_enabled()
            self.logger.info(f"Routing initialized with persisted state: multiroom={current_multiroom}, dsp_effects={current_dsp_effects}")

            # Schedule delayed DSP sync if multiroom is already enabled at startup
            if current_multiroom:
                asyncio.create_task(self._delayed_multiroom_sync())

        except Exception as e:
            self.logger.error(f"Error during initial state detection: {e}")
            await self._set_multiroom_state(False)
            await self._set_dsp_effects_state(False)
            await self._update_systemd_environment()
            self._initial_detection_done = True

    async def _delayed_multiroom_sync(self):
        """Sync client volumes from DSP after startup delay (ensures all services ready)."""
        try:
            # Wait for all services to be fully initialized
            await asyncio.sleep(3.0)

            # Check multiroom is still enabled
            if not await self._get_multiroom_enabled():
                return

            # Sync volumes from DSP
            volume_service = getattr(self.state_machine, 'volume_service', None) if self.state_machine else None
            if volume_service:
                self.logger.info("📊 Syncing client volumes from DSP (startup sync)...")
                await volume_service.sync_all_clients_from_dsp()
            else:
                self.logger.warning("VolumeService not available for DSP sync")

        except Exception as e:
            self.logger.error(f"Error in delayed multiroom sync: {e}")

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

                    # Push local volume to all clients for uniform volume
                    volume_service = getattr(self.state_machine, 'volume_service', None)
                    if volume_service:
                        self.logger.info("📊 Pushing local volume to all clients...")
                        await volume_service.push_volume_to_all_clients()

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

    async def set_dsp_effects_enabled(self, enabled: bool, active_source: AudioSource = None) -> bool:
        """
        Enables/disables DSP EFFECTS (not the service itself).

        CamillaDSP service stays ALWAYS running. This toggle only controls:
        - EQ filters (enabled/bypassed)
        - Compressor (enabled/bypassed)
        - Loudness (enabled/bypassed)

        Volume control via CamillaDSP is ALWAYS active regardless of this setting.
        """
        async with self._routing_lock:  # Guarantee atomicity of routing operations
            current_state = await self._get_dsp_effects_enabled()
            if current_state == enabled:
                self.logger.info(f"DSP effects already {'enabled' if enabled else 'bypassed'}")
                return True

            try:
                old_state = current_state
                self.logger.info(f"{'Enabling' if enabled else 'Bypassing'} DSP effects")

                await self._set_dsp_effects_state(enabled)

                # Toggle DSP effects (NOT the service!)
                if self.camilladsp_service:
                    if enabled:
                        # Restore all DSP effects from settings
                        success = await self.camilladsp_service.restore_effects()
                        if success:
                            self.logger.info("DSP effects restored from settings")
                        else:
                            self.logger.warning("Failed to restore DSP effects")
                    else:
                        # Bypass all DSP effects (but keep volume!)
                        success = await self.camilladsp_service.bypass_effects()
                        if success:
                            self.logger.info("DSP effects bypassed (volume unchanged)")
                        else:
                            self.logger.warning("Failed to bypass DSP effects")

                # Broadcast DSP state change event
                if self.state_machine:
                    await self.state_machine.broadcast_event("dsp", "enabled_changed", {
                        "enabled": enabled,
                        "effects_bypassed": not enabled
                    })

                # Save state via SettingsService
                if self.settings_service:
                    await self.settings_service.set_setting('dsp.effects_enabled', enabled)

                self.logger.info(f"DSP effects {'enabled' if enabled else 'bypassed'}")
                return True

            except Exception as e:
                await self._set_dsp_effects_state(old_state)
                self.logger.error(f"Error changing DSP effects state: {e}")
                return False
    
    async def _update_systemd_environment(self) -> None:
        """
        Updates ALSA environment variables via static file

        NEW: No more runtime sudo! Variables are written to
        /var/lib/milo/routing.env which is read by systemd services.
        """
        mode_value = "multiroom" if self.multiroom_enabled else "direct"
        # Note: MILO_EQUALIZER env var name kept for ALSA config compatibility
        equalizer_value = "_eq" if self.dsp_effects_enabled else ""

        # Strict validation
        if mode_value not in self.ALLOWED_MODES:
            raise ValueError(f"Invalid mode value: {mode_value}. Allowed: {self.ALLOWED_MODES}")

        if equalizer_value not in self.ALLOWED_EQUALIZER:
            raise ValueError(f"Invalid equalizer value: {equalizer_value}. Allowed: {self.ALLOWED_EQUALIZER}")

        environment_file = "/var/lib/milo/routing.env"

        try:
            # CamillaDSP is ALWAYS active (for volume control)
            # Snapclient always outputs to CamillaDSP loopback
            snapclient_soundcard = "camilladsp"

            # Atomic write of environment file
            temp_file = environment_file + ".tmp"

            with open(temp_file, 'w') as f:
                f.write("# Milo Audio Routing Environment Variables\n")
                f.write("# This file is automatically modified by Milo backend\n")
                f.write("# Do not edit manually\n\n")
                f.write(f"# Audio routing mode: \"direct\" or \"multiroom\"\n")
                f.write(f"MILO_MODE={mode_value}\n\n")
                f.write(f"# Equalizer: \"\" (disabled) or \"_eq\" (enabled)\n")
                f.write(f"MILO_EQUALIZER={equalizer_value}\n\n")
                f.write(f"# Snapclient output soundcard\n")
                f.write(f"MILO_SNAPCLIENT_SOUNDCARD={snapclient_soundcard}\n")
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
        """Transition to multiroom mode

        IMPORTANT: The plugin must be stopped BEFORE starting Snapserver.
        If a plugin (e.g., Radio/mpv) is outputting to the ALSA loopback,
        the format is locked and Snapserver cannot start.

        Order:
        1. Save current playback state (what was playing)
        2. Stop the active plugin (releases ALSA loopback)
        3. Start Snapserver (opens ALSA loopback capture)
        4. Start Snapclient
        5. Restart plugin with new routing
        6. Resume playback if it was active
        """
        try:
            plugin = None
            was_playing = False
            playback_metadata = {}

            if active_source and self.get_plugin:
                plugin = self.get_plugin(active_source)

            # Step 1: Save current playback state before stopping
            if plugin and self.state_machine:
                current_state = self.state_machine.system_state.plugin_state
                current_metadata = self.state_machine.system_state.metadata.copy()
                was_playing = current_state == PluginState.CONNECTED or current_metadata.get("is_playing", False)
                if was_playing:
                    playback_metadata = current_metadata
                    self.logger.info(f"Saving playback state: {active_source.value} was playing")

            # Step 2: Stop active plugin to release ALSA loopback
            if plugin:
                self.logger.info(f"Stopping plugin {active_source.value} before Snapserver start")
                await plugin.stop()
                await asyncio.sleep(0.3)  # Brief delay for ALSA to release

            # Step 3-4: Start Snapcast services
            self.logger.info("Starting snapcast services")
            snapcast_success = await self._start_snapcast()
            if not snapcast_success:
                # Try to restart plugin even if Snapcast failed
                if plugin:
                    self.logger.info(f"Snapcast failed, restarting plugin {active_source.value}")
                    await plugin.start()
                return False

            # Step 5: Restart plugin with new routing
            if plugin:
                self.logger.info(f"Restarting plugin {active_source.value} for multiroom mode")
                await plugin.restart()

            # Step 6: Resume playback if it was active
            if was_playing and plugin and playback_metadata:
                # Wait for plugin to be fully ready after restart
                # The systemd restart takes time, then mpv needs to connect to IPC
                await asyncio.sleep(2.0)
                await self._resume_playback(active_source, plugin, playback_metadata)

            return True

        except Exception as e:
            self.logger.error(f"Error in multiroom transition: {e}")
            return False

    async def _resume_playback(self, source: AudioSource, plugin, metadata: Dict[str, Any]) -> None:
        """Resume playback after routing change"""
        try:
            if source == AudioSource.RADIO:
                station_id = metadata.get("station_id")
                if station_id:
                    self.logger.info(f"Resuming radio playback: {metadata.get('station_name', station_id)}")
                    result = await plugin.handle_command("play_station", {"station_id": station_id})
                    self.logger.info(f"Resume result: {result}")
            elif source == AudioSource.PODCAST:
                episode_id = metadata.get("episode_id")
                if episode_id:
                    self.logger.info(f"Resuming podcast playback: {metadata.get('episode_title', episode_id)}")
                    result = await plugin.handle_command("play_episode", {"episode_id": episode_id})
                    self.logger.info(f"Resume result: {result}")
            # Spotify and Bluetooth manage their own playback state
        except Exception as e:
            self.logger.warning(f"Could not resume playback for {source.value}: {e}")
    
    async def _transition_to_direct(self, active_source: AudioSource = None) -> bool:
        """Transition to direct mode

        Order:
        1. Save current playback state
        2. Stop the active plugin
        3. Stop Snapcast services
        4. Restart plugin with new routing
        5. Resume playback if it was active
        """
        try:
            plugin = None
            was_playing = False
            playback_metadata = {}

            if active_source and self.get_plugin:
                plugin = self.get_plugin(active_source)

            # Step 1: Save current playback state before stopping
            if plugin and self.state_machine:
                current_state = self.state_machine.system_state.plugin_state
                current_metadata = self.state_machine.system_state.metadata.copy()
                was_playing = current_state == PluginState.CONNECTED or current_metadata.get("is_playing", False)
                if was_playing:
                    playback_metadata = current_metadata
                    self.logger.info(f"Saving playback state: {active_source.value} was playing")

            # Step 2: Stop active plugin
            if plugin:
                self.logger.info(f"Stopping plugin {active_source.value} before Snapcast shutdown")
                await plugin.stop()

            # Step 3: Stop Snapcast services
            self.logger.info("Stopping snapcast services")
            await self._stop_snapcast()

            # Step 4: Restart plugin with new routing
            if plugin:
                self.logger.info(f"Restarting plugin {active_source.value} for direct mode")
                await plugin.restart()

            # Step 5: Resume playback if it was active
            if was_playing and plugin and playback_metadata:
                # Wait for plugin to be fully ready after restart
                await asyncio.sleep(2.0)
                await self._resume_playback(active_source, plugin, playback_metadata)

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
            "dsp_effects_enabled": self.dsp_effects_enabled
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