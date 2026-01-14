# backend/core/volume/service.py
"""
Volume management service - CamillaDSP always active.

All volume values are in decibels (-80 to 0 dB).
ALSA is set to 100% passthrough - volume control is entirely via CamillaDSP.

Architecture:
- VolumeStateStore: Single source of truth for all volume state
- DSPController: Hardware abstraction for parallel volume updates
- VolumeService: Orchestration layer only
"""
import asyncio
import logging
import time
from typing import Optional, Dict, Any

from backend.core.events import EventBus, Events, get_event_bus
from backend.core.volume.config import VolumeConfigService
from backend.core.volume.state import VolumeStateStore
from backend.core.volume.dsp_controller import DSPController
from backend.core.multiroom.snapcast import get_available_client_ids
from backend.core.models.volume_state import VolumeState


class VolumeService:
    """
    System volume management service.

    Volume is ALWAYS controlled via CamillaDSP in dB (-80 to 0).
    - Direct mode: Single local CamillaDSP control
    - Multiroom mode: DSP volume synchronized across all clients

    ALSA Digital mixer is set to 100% passthrough and never changed.

    Architecture:
        VolumeStateStore: Single source of truth (state + zones + clients)
        DSPController: Hardware abstraction (local + remote DSP updates)
        VolumeService: Orchestration (API -> State -> Hardware)
    """

    def __init__(self, state_machine, snapcast_service, settings_service=None,
                 camilladsp_service=None, dsp_client_proxy_service=None,
                 event_bus: EventBus = None):
        """
        Initialize VolumeService.

        Args:
            state_machine: AudioStateMachine for WebSocket broadcasting
            snapcast_service: Service for Snapcast client management
            settings_service: SettingsService for configuration
            camilladsp_service: Service for local CamillaDSP control
            dsp_client_proxy_service: Service for remote client control
            event_bus: EventBus for emitting volume events (optional, uses global singleton)
        """
        self.event_bus = event_bus or get_event_bus()
        self.state_machine = state_machine
        self.snapcast_service = snapcast_service
        self.settings_service = settings_service
        self._dsp_service = camilladsp_service
        self._proxy_service = dsp_client_proxy_service
        self.logger = logging.getLogger(__name__)
        self._volume_lock = asyncio.Lock()

        # Initialize sub-services
        self._config_service = VolumeConfigService(self.settings_service)

        # VolumeStateStore (SSOT) + DSPController (hardware abstraction)
        self._state_store = VolumeStateStore(self.settings_service)
        self._dsp_controller = DSPController(self._dsp_service, self._proxy_service)

        # Snapcast WebSocket service (set via setter to resolve circular dependency)
        self._snapcast_websocket_service = None

        # Event to signal when client availability has been initialized (for WebSocket handshake)
        self._availability_ready = asyncio.Event()

    # ============================================================================
    # EXPOSED SUB-SERVICES
    # ============================================================================

    @property
    def config(self) -> VolumeConfigService:
        """Access to volume configuration service."""
        return self._config_service

    def set_snapcast_websocket_service(self, service) -> None:
        """Set Snapcast WebSocket service reference (circular dependency resolution)."""
        self._snapcast_websocket_service = service

    async def wait_for_availability(self, timeout: float = 5.0) -> bool:
        """
        Wait for client availability initialization to complete.

        Called by WebSocket server before sending initial volume state
        to ensure zone data includes available clients with correct volumes.

        Args:
            timeout: Maximum time to wait in seconds

        Returns:
            True if availability is ready, False if timeout
        """
        try:
            await asyncio.wait_for(self._availability_ready.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            self.logger.warning(f"Availability wait timed out after {timeout}s")
            return False

    # ============================================================================
    # MODE DETECTION & ALSA SETUP
    # ============================================================================

    async def _set_alsa_passthrough(self) -> bool:
        """Set ALSA Digital mixer to 100% passthrough (volume is via CamillaDSP)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "amixer", "-M", "set", "Digital", "100%",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc.communicate()
            return proc.returncode == 0
        except Exception as e:
            self.logger.error(f"Error setting ALSA passthrough: {e}")
            return False

    def _is_multiroom_enabled(self) -> bool:
        """Check if multiroom mode is currently enabled."""
        try:
            if not self.state_machine or not hasattr(self.state_machine, 'routing_service'):
                return False
            routing_state = self.state_machine.routing_service.get_state()
            return routing_state.get('multiroom_enabled', False)
        except Exception:
            return False

    def _is_dsp_available(self) -> bool:
        """Check if CamillaDSP is connected and available for volume control."""
        if not self._dsp_service:
            return False
        return self._dsp_service.is_volume_control_available()

    async def update_volume_mode(self, multiroom_enabled: bool) -> None:
        """
        Update volume mode when multiroom state changes.

        This syncs VolumeStateStore mode and ensures 'local' client is properly
        configured when switching to direct mode.

        Args:
            multiroom_enabled: Whether multiroom is now enabled
        """
        mode = "multiroom" if multiroom_enabled else "direct"
        await self._state_store.set_mode(mode)

        if not multiroom_enabled:
            # Sync local volume from DSP when switching to direct mode
            try:
                current_volume = await self._dsp_service.get_volume()
                if current_volume is not None:
                    self._state_store.set_local_volume(current_volume)
                    self.logger.info(f"Synced local volume from DSP: {current_volume:.1f} dB")
            except Exception as e:
                self.logger.warning(f"Failed to sync local volume from DSP: {e}")

    # ============================================================================
    # CONFIGURATION LOADING
    # ============================================================================

    async def _load_volume_config(self) -> None:
        """Load volume configuration from settings asynchronously."""
        await self._config_service.load()
        # Update state store with new limits (config is SSOT for limits)
        min_db = self._config_service.config.limit_min_db
        max_db = self._config_service.config.limit_max_db
        self._state_store.update_user_limits(min_db, max_db)

    def _save_last_volume(self, volume_db: float) -> None:
        """Save last volume in background (via VolumeStateStore)."""
        self._state_store.save_local_volume(self.config.config.restore_last_volume)

    async def reload_volume_limits(self) -> bool:
        """Reload volume limits from settings and adjust current volume if needed."""
        try:
            volume_state = await self._state_store.get_complete_state()
            current_db = volume_state.global_volume_db
            old_min_db, old_max_db = await self._config_service.reload_limits()

            # Update state store with new limits (config is SSOT for limits)
            new_min = self._config_service.config.limit_min_db
            new_max = self._config_service.config.limit_max_db
            self._state_store.update_user_limits(new_min, new_max)

            # No change, nothing to do
            if old_min_db == new_min and old_max_db == new_max:
                return True

            # Check if current volume is outside new limits
            if current_db < new_min or current_db > new_max:
                # Move to center of new range
                center_db = (new_min + new_max) / 2.0
                await self.set_volume_db(center_db, show_bar=False)
            else:
                await self._broadcast_volume_state(show_bar=False)

            return True
        except Exception as e:
            self.logger.error(f"Error reloading volume limits: {e}")
            return False

    async def reload_startup_config(self) -> bool:
        """Reload startup configuration."""
        try:
            await self._config_service.load()
            return True
        except Exception as e:
            self.logger.error(f"Error reloading startup config: {e}")
            return False

    async def reload_volume_steps_config(self) -> bool:
        """Reload volume step configuration."""
        try:
            await self._config_service.load()
            await self._broadcast_volume_state(show_bar=False)
            return True
        except Exception as e:
            self.logger.error(f"Error reloading volume steps: {e}")
            return False

    async def reload_rotary_steps_config(self) -> bool:
        """Reload rotary encoder step configuration."""
        try:
            await self._config_service.load()
            return True
        except Exception as e:
            self.logger.error(f"Error reloading rotary steps: {e}")
            return False

    # ============================================================================
    # CLIENT VOLUME MANAGEMENT (VolumeStateStore architecture)
    # ============================================================================

    async def sync_existing_client_from_snapcast(self, client_id: str) -> bool:
        """
        Sync reconnected client: apply correct volume to DSP.

        Sequence to prevent volume spike:
        1. Mute DSP first (safety in case client reconnected without CamillaDSP restart)
        2. Set correct volume
        3. Unmute DSP

        Volume selection priority:
        1. If client is in a zone -> ALWAYS use zone's current average (consistency)
        2. If client not in zone -> use persisted volume or display volume
        """
        if not self._is_multiroom_enabled():
            return True

        try:
            self.logger.info(f"[{time.time():.3f}] DSP_SYNC: Start for {client_id}")

            # Wait for client DSP to be ready before sending any commands
            ready = await self._dsp_controller.wait_for_client_ready(client_id, max_wait=10.0)
            if not ready:
                self.logger.error(f"[{time.time():.3f}] DSP_SYNC: Client {client_id} DSP not ready after timeout, skipping sync")
                return False

            # MUTE DSP first to prevent volume spike during sync
            await self._dsp_controller.set_dsp_mute(client_id, True)
            self.logger.info(f"[{time.time():.3f}] DSP_SYNC: Muted DSP for {client_id}")

            volume_state = await self._state_store.get_complete_state()

            # FIRST: Check if client is in any zone
            client_zone_id = None
            for zone_id, zone_data in volume_state.zones.items():
                if client_id in zone_data.client_ids:
                    client_zone_id = zone_id
                    break

            if client_zone_id:
                # Client is in a zone - use cached zone target for consistent initial sync
                target = self._state_store.get_zone_target_volume(client_zone_id)
                if target is not None:
                    expected_volume = target
                    self.logger.info(f"Syncing client {client_id} in zone '{client_zone_id}', using cached zone target: {expected_volume:.1f}dB")
                else:
                    # Fallback to computed average (normal operation after initial sync)
                    expected_volume = volume_state.zones[client_zone_id].average_volume_db
                    self.logger.info(f"Reconnecting client {client_id} in zone '{client_zone_id}', using zone volume: {expected_volume:.1f}dB")
            else:
                # Client not in a zone - use persisted volume or display volume
                expected_volume = self._state_store.get_client_volume(client_id)

                if expected_volume is None:
                    expected_volume = volume_state.global_volume_db
                    self.logger.info(f"New client {client_id}, applying display volume: {expected_volume:.1f}dB")
                else:
                    self.logger.info(f"Reconnected client {client_id} (no zone), applying persisted volume: {expected_volume:.1f}dB")

            # Set the correct volume while DSP is muted
            await self._dsp_controller.set_dsp_volume(client_id, expected_volume)
            self.logger.info(f"[{time.time():.3f}] DSP_SYNC: Volume set to {expected_volume:.1f}dB for {client_id}")

            # Apply persisted mute state (or unmute if no persisted state)
            persisted_mute = False
            if client_id in self._state_store._clients:
                persisted_mute = self._state_store._clients[client_id].mute
            await self._dsp_controller.set_dsp_mute(client_id, persisted_mute)
            self.logger.info(f"[{time.time():.3f}] DSP_SYNC: Unmuted DSP for {client_id} (mute={persisted_mute})")

            # Register client with the applied volume
            await self._state_store.register_client(client_id, volume_db=expected_volume, available=True)
            await self._broadcast_volume_state(show_bar=False)
            return True
        except Exception as e:
            self.logger.error(f"Error syncing existing client {client_id}: {e}")
            # Try to unmute on error to avoid client stuck muted
            try:
                await self._dsp_controller.set_dsp_mute(client_id, False)
            except Exception:
                pass
            return False

    async def sync_client_volume_from_external(self, client_id: str, volume_db: float) -> None:
        """Sync client volume from external change (e.g., MultiroomModal)."""
        if not self._is_multiroom_enabled():
            return

        try:
            # Update state store
            await self._state_store.set_client_volume(client_id, volume_db)

            # Apply to hardware
            await self._dsp_controller.set_dsp_volume(client_id, volume_db)

            # Broadcast updated state
            await self._broadcast_volume_state(show_bar=False)
        except Exception as e:
            self.logger.error(f"Error syncing client {client_id} volume from external: {e}")

    async def sync_all_clients_from_dsp(self) -> bool:
        """
        Sync all client volumes from their DSP state.
        Called when multiroom is enabled to initialize volume offsets.
        """
        if not self._is_multiroom_enabled():
            return True

        try:
            clients = await self.snapcast_service.get_clients()
            for client in clients:
                client_id = client.get("dsp_id", "")
                if not client_id:
                    continue

                # Read DSP volume
                if client_id == 'local':
                    volume_data = await self._dsp_service.get_volume()
                    volume = volume_data.get("main", -30.0) if volume_data else -30.0
                else:
                    result = await self._proxy_service.request(client_id, "GET", "/dsp/volume")
                    volume = result.get("main", -30.0) if result else -30.0

                # Register with state store
                available = client.get("available", True)
                await self._state_store.register_client(client_id, volume_db=volume, available=available)

            self.logger.info(f"Synced {len(clients)} clients from DSP")
            await self._broadcast_volume_state(show_bar=False)
            return True
        except Exception as e:
            self.logger.error(f"Error syncing all clients from DSP: {e}")
            return False

    async def push_volume_to_all_clients(self) -> bool:
        """
        Push volume and mute state to all multiroom clients.
        Called when multiroom is activated to restore client states.

        Respects startup settings:
        - restore_last_volume=true: Use persisted client volumes
        - restore_last_volume=false: Use startup_volume_db for all clients
        """
        try:
            self.logger.info(f"[{time.time():.3f}] PUSH_ALL: Starting push to all clients")

            # Get all available clients
            client_ids = await get_available_client_ids(self.snapcast_service)
            if not client_ids:
                return True

            # Check startup configuration
            restore_enabled = self.config.config.restore_last_volume
            startup_volume = self.config.config.startup_volume_db

            # Build volume updates based on startup settings
            updates = {}
            for client_id in client_ids:
                if restore_enabled and client_id in self._state_store._clients:
                    # Restore mode: use persisted volume
                    updates[client_id] = self._state_store._clients[client_id].volume_db
                elif restore_enabled:
                    # Restore mode but no persisted state: use local DSP volume
                    dsp_state = await self._dsp_service.get_volume()
                    local_volume = dsp_state.get("main", -30.0) if dsp_state else -30.0
                    updates[client_id] = local_volume
                else:
                    # Fixed startup mode: use startup_volume_db
                    updates[client_id] = startup_volume

            if not updates:
                return True

            mode = "persisted" if restore_enabled else f"startup ({startup_volume:.1f}dB)"
            self.logger.info(f"[{time.time():.3f}] PUSH_ALL: Pushing {mode} volumes to {len(updates)} clients")

            # Apply volumes to all clients in parallel
            results = await self._dsp_controller.apply_volumes_parallel(updates)
            self.logger.info(f"[{time.time():.3f}] PUSH_ALL: Applied volumes to {len(updates)} clients")

            # Update state store for successful volume updates
            successful_updates = {
                hostname: volume
                for hostname, volume in updates.items()
                if results.get(hostname, False)
            }

            for hostname, volume in successful_updates.items():
                await self._state_store.set_client_volume(hostname, volume)

            # Apply persisted mute states to clients
            for client_id in client_ids:
                if client_id in self._state_store._clients:
                    mute_state = self._state_store._clients[client_id].mute
                    try:
                        await self._dsp_controller.set_dsp_mute(client_id, mute_state)
                        self.logger.debug(f"Applied mute state to {client_id}: {mute_state}")
                    except Exception as e:
                        self.logger.warning(f"Failed to apply mute to {client_id}: {e}")

            await self._broadcast_volume_state(show_bar=False)

            failures = [h for h, success in results.items() if not success]
            if failures:
                self.logger.warning(f"Failed to push volume to: {failures}")

            return len(failures) == 0
        except Exception as e:
            self.logger.error(f"Error pushing volume to clients: {e}")
            return False

    async def update_client_volume_db(self, client_id: str, volume_db: float, broadcast: bool = True) -> None:
        """
        Update client volume in dB (called from API routes).

        Args:
            client_id: Client hostname ('local' or IP address)
            volume_db: Volume in dB
            broadcast: Whether to broadcast volume change to update VolumeBar
        """
        try:
            # Update state store
            await self._state_store.set_client_volume(client_id, volume_db)

            # Apply to hardware
            await self._dsp_controller.set_dsp_volume(client_id, volume_db)

            # Broadcast if requested
            if broadcast and self._is_multiroom_enabled():
                await self._broadcast_volume_state(show_bar=False)
        except Exception as e:
            self.logger.error(f"Error updating client {client_id} volume: {e}")

    async def set_client_mute(self, client_id: str, mute: bool, broadcast: bool = True) -> None:
        """
        Set mute state for a client.

        Args:
            client_id: Client hostname ('local' or IP address)
            mute: Mute state (True = muted, False = unmuted)
            broadcast: Whether to broadcast mute change
        """
        try:
            # Update state store
            await self._state_store.set_client_mute(client_id, mute)

            # Apply to DSP hardware
            await self._dsp_controller.set_dsp_mute(client_id, mute)

            # Broadcast if requested
            if broadcast:
                await self._broadcast_volume_state(show_bar=False)

        except Exception as e:
            self.logger.error(f"Error setting client {client_id} mute: {e}")

    # ============================================================================
    # ATOMIC ZONE OPERATIONS
    # ============================================================================

    async def apply_zone_volume_delta(self, zone_id: str, delta_db: float) -> float:
        """
        Apply volume delta to entire zone atomically.

        This calculates updates for all clients, applies them in parallel, then broadcasts once.

        Args:
            zone_id: Zone identifier
            delta_db: Volume change in dB

        Returns:
            New zone average volume in dB

        Raises:
            ValueError: If zone not found
        """
        async with self._volume_lock:
            try:
                # Clear initial zone targets cache on first user interaction
                self._state_store.clear_zone_targets()

                # 1. Calculate volume updates for all clients in zone
                updates = await self._state_store.apply_zone_delta(zone_id, delta_db)

                if not updates:
                    self.logger.warning(f"No clients to update in zone {zone_id}")
                    return self._state_store.compute_zone_average(zone_id)

                # 2. Apply updates to hardware in parallel
                self.logger.info(f"Applying zone delta: {zone_id} +{delta_db:+.1f}dB -> {len(updates)} clients")
                results = await self._dsp_controller.apply_volumes_parallel(updates)

                # 3. Update state store with successful updates only
                successful_updates = {
                    hostname: volume
                    for hostname, volume in updates.items()
                    if results.get(hostname, False)
                }

                await self._state_store.apply_zone_updates(successful_updates)

                # Log failures
                failures = [hostname for hostname, success in results.items() if not success]
                if failures:
                    self.logger.warning(f"Failed to update clients: {failures}")

                # 4. Broadcast complete state once (no bar)
                await self._broadcast_volume_state(show_bar=False)

                # 5. Return new zone average
                new_avg = self._state_store.compute_zone_average(zone_id)
                self.logger.info(f"Zone {zone_id} updated: {new_avg:.1f}dB (success: {len(successful_updates)}/{len(updates)})")
                return new_avg

            except Exception as e:
                self.logger.error(f"Error applying zone delta: {e}", exc_info=True)
                raise

    # ============================================================================
    # SERVICE INITIALIZATION
    # ============================================================================

    async def initialize(self) -> bool:
        """
        Initialize volume service.

        Sets ALSA to 100% passthrough and initializes DSP volume.
        """
        try:
            await self._load_volume_config()

            # Initialize VolumeStateStore (loads zones, persisted state)
            await self._state_store.initialize()
            self.logger.info("VolumeStateStore initialized")

            # Apply persisted volume to CamillaDSP (safe startup at -50dB, then restore)
            await self._apply_startup_volume()

            # Set ALSA to 100% passthrough - permanent (volume is via CamillaDSP)
            await self._set_alsa_passthrough()
            self.logger.info("ALSA set to 100% passthrough mode")

            # Start initial broadcast task (waits for Snapcast WebSocket in multiroom mode)
            asyncio.create_task(self._startup_broadcast_after_websocket_ready())
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize: {e}")
            return False

    async def _apply_startup_volume(self) -> None:
        """
        Apply startup volume and mute state to CamillaDSP.

        CamillaDSP starts muted with -m flag (systemd service).
        This method either:
        - Restores the user's last known volume (if restore_last_volume=true)
        - Uses startup_volume_db (if restore_last_volume=false)
        Then applies persisted mute state.
        """
        try:
            self.logger.info(f"[{time.time():.3f}] STARTUP_VOLUME: Starting local volume application")

            # Wait for CamillaDSP to be connected (services initialize in parallel)
            if self._dsp_service:
                self.logger.info(f"[{time.time():.3f}] STARTUP_VOLUME: Waiting for CamillaDSP connection...")
                connected = await self._dsp_service.wait_for_connection(timeout=10.0)
                if not connected:
                    self.logger.warning(f"[{time.time():.3f}] STARTUP_VOLUME: CamillaDSP not connected after 10s, startup volume/mute not applied")
                    return
                self.logger.info(f"[{time.time():.3f}] STARTUP_VOLUME: CamillaDSP connected")

            # Determine target volume based on restore setting
            restore_enabled = self.config.config.restore_last_volume
            startup_volume = self.config.config.startup_volume_db

            if restore_enabled:
                # Use persisted volume from last session
                if 'local' in self._state_store._clients:
                    target_volume = self._state_store._clients['local'].volume_db
                else:
                    # Direct mode fallback
                    target_volume = self._state_store._local_volume_db
                self.logger.info(f"Restoring persisted volume: {target_volume:.1f} dB")
            else:
                # Use configured startup volume
                target_volume = startup_volume
                self.logger.info(f"Using startup volume setting: {target_volume:.1f} dB")

            # Get persisted mute state for local client
            local_mute = False
            if 'local' in self._state_store._clients:
                local_mute = self._state_store._clients['local'].mute

            if target_volume is not None and self._dsp_controller:
                # Set volume while DSP is still muted (from -m flag at startup)
                await self._dsp_controller.set_dsp_volume("local", target_volume)

                # Apply persisted mute state (or unmute if not muted)
                await self._dsp_controller.set_dsp_mute("local", local_mute)
                self.logger.info(f"Applied startup state: volume={target_volume:.1f} dB, mute={local_mute}")
            else:
                self.logger.info("No target volume, applying default unmuted state")
                # Unmute even if no target volume
                if self._dsp_controller:
                    await self._dsp_controller.set_dsp_mute("local", False)
        except Exception as e:
            self.logger.error(f"Failed to apply startup volume: {e}")

    async def _startup_broadcast_after_websocket_ready(self):
        """
        Wait for Snapcast WebSocket and broadcast initial volume state.

        In multiroom mode, waits for WebSocket to be ready before initializing
        client availability and syncing all clients.
        """
        try:
            # Check if multiroom is enabled
            multiroom_enabled = await self.settings_service.get_setting("routing.multiroom_enabled") or False

            if multiroom_enabled and self._snapcast_websocket_service:
                self.logger.info(f"[{time.time():.3f}] STARTUP_BROADCAST: Waiting for Snapcast WebSocket...")
                ws_ready = await self._snapcast_websocket_service.wait_for_ready(timeout=30.0)

                if ws_ready:
                    self.logger.info(f"[{time.time():.3f}] STARTUP_BROADCAST: WebSocket ready, starting client sync")
                    # Initialize client availability NOW that WebSocket is ready
                    await self.initialize_client_availability()

                    # Signal that availability is ready (WebSocket can now send accurate state)
                    self._availability_ready.set()

                    self.logger.info(f"[{time.time():.3f}] STARTUP_BROADCAST: Calling push_volume_to_all_clients")
                    await self.push_volume_to_all_clients()
                    self.logger.info(f"[{time.time():.3f}] STARTUP_BROADCAST: push_volume_to_all_clients complete")
                else:
                    self.logger.warning("Snapcast WebSocket not ready after timeout, broadcasting with available state")
                    # Still signal ready so WebSocket doesn't wait forever
                    self._availability_ready.set()
            else:
                # Direct mode or no WebSocket service - short delay for other services
                await asyncio.sleep(0.5)
                # Signal ready for direct mode
                self._availability_ready.set()

            await self._broadcast_volume_state(show_bar=False)
        except Exception as e:
            self.logger.error(f"Error in startup broadcast: {e}")

    # ============================================================================
    # PUBLIC API (all in dB)
    # ============================================================================

    async def get_volume_db(self) -> float:
        """Get current volume in dB (average of non-muted clients in multiroom mode)."""
        volume_state = await self._state_store.get_complete_state()
        return volume_state.global_volume_db

    async def set_volume_db(self, volume_db: float, show_bar: bool = True) -> bool:
        """
        Set volume to specific level in dB.

        Args:
            volume_db: Target volume in dB (-80 to 0)
            show_bar: Whether to show volume bar in UI

        Returns:
            True if successful
        """
        try:
            async with asyncio.timeout(2.0):
                async with self._volume_lock:
                    try:
                        clamped_db = self._config_service.config.clamp(volume_db)

                        # Check DSP availability (skip in multiroom mode - uses HTTP to clients)
                        if not self._is_multiroom_enabled() and not self._is_dsp_available():
                            self.logger.warning("DSP not available, volume change blocked")
                            await self.state_machine.broadcast_event("volume", "volume_error", {
                                "error": "CamillaDSP not available",
                                "dsp_available": False
                            })
                            return False

                        # Apply volume
                        success = await self._apply_volume_db(clamped_db)

                        if success:
                            self._save_last_volume(clamped_db)
                            await self._broadcast_volume_state(show_bar)

                        return success
                    except Exception as e:
                        self.logger.error(f"Error setting volume: {e}")
                        return False
        except asyncio.TimeoutError:
            self.logger.error("Timeout waiting for volume lock (>2s)")
            return False

    async def _apply_volume_db(self, volume_db: float) -> bool:
        """Apply volume to DSP (local or multiroom)."""
        try:
            if self._is_multiroom_enabled():
                # MULTIROOM: Set absolute volume for all clients
                client_ids = await get_available_client_ids(self.snapcast_service)
                if not client_ids:
                    return True

                updates = {client_id: volume_db for client_id in client_ids}

                # Apply in parallel
                results = await self._dsp_controller.apply_volumes_parallel(updates)

                # Update state store
                for hostname, volume in updates.items():
                    if results.get(hostname, False):
                        await self._state_store.set_client_volume(hostname, volume)

                # Graceful degradation: log warning but don't fail
                if results and not any(results.values()):
                    self.logger.warning(f"All {len(results)} multiroom clients failed volume update")
                return True  # Clients will sync on reconnect
            else:
                # LOCAL: Direct CamillaDSP control
                success = await self._dsp_service.set_volume(volume_db)
                if success:
                    self._state_store.set_local_volume(volume_db)
                return success

        except Exception as e:
            self.logger.error(f"Error applying volume: {e}")
            return False

    async def adjust_volume_db(self, delta_db: float, show_bar: bool = True) -> bool:
        """
        Adjust volume by delta in dB.

        Args:
            delta_db: Volume change in dB (positive = louder, negative = quieter)
            show_bar: Whether to show volume bar in UI

        Returns:
            True if successful
        """
        try:
            async with asyncio.timeout(2.0):
                async with self._volume_lock:
                    try:
                        # Check DSP availability (skip in multiroom mode - uses HTTP to clients)
                        if not self._is_multiroom_enabled() and not self._is_dsp_available():
                            self.logger.warning("DSP not available, volume change blocked")
                            await self.state_machine.broadcast_event("volume", "volume_error", {
                                "error": "CamillaDSP not available",
                                "dsp_available": False
                            })
                            return False

                        # Apply delta
                        success = await self._apply_delta_db(delta_db)

                        if success:
                            volume_state = await self._state_store.get_complete_state()
                            self._save_last_volume(volume_state.global_volume_db)
                            await self._broadcast_volume_state(show_bar)

                        return success
                    except Exception as e:
                        self.logger.error(f"Error adjusting volume: {e}")
                        return False
        except asyncio.TimeoutError:
            self.logger.error("Timeout waiting for volume lock (>2s)")
            return False

    async def _apply_delta_db(self, delta_db: float) -> bool:
        """Apply volume delta in dB."""
        try:
            if self._is_multiroom_enabled():
                # MULTIROOM: Apply delta to all clients
                client_ids = await get_available_client_ids(self.snapcast_service)
                if not client_ids:
                    return True

                volume_state = await self._state_store.get_complete_state()
                updates = {}
                for client_id in client_ids:
                    current = volume_state.clients.get(client_id)
                    if current:
                        new_vol = self._config_service.config.clamp(current.volume_db + delta_db)
                        updates[client_id] = new_vol

                if not updates:
                    return True

                # Apply in parallel
                results = await self._dsp_controller.apply_volumes_parallel(updates)

                # Update state store
                for hostname, volume in updates.items():
                    if results.get(hostname, False):
                        await self._state_store.set_client_volume(hostname, volume)

                # Graceful degradation: log warning but don't fail
                if results and not any(results.values()):
                    self.logger.warning(f"All {len(results)} multiroom clients failed volume update")
                return True  # Clients will sync on reconnect
            else:
                # LOCAL: Apply delta to CamillaDSP
                volume_state = await self._state_store.get_complete_state()
                new_db = self._config_service.config.clamp(volume_state.global_volume_db + delta_db)
                success = await self._dsp_service.set_volume(new_db)
                if success:
                    self._state_store.set_local_volume(new_db)
                return success

        except Exception as e:
            self.logger.error(f"Error applying delta: {e}")
            return False

    # ============================================================================
    # WEBSOCKET BROADCASTING
    # ============================================================================

    def _handle_broadcast_task_error(self, task: asyncio.Task) -> None:
        """Handle errors from background broadcast tasks."""
        try:
            # This will raise the exception if the task failed
            task.result()
        except asyncio.CancelledError:
            # Task was cancelled, this is normal during shutdown
            pass
        except Exception as e:
            self.logger.error(f"Background broadcast task failed: {e}", exc_info=True)

    def update_client_availability(self, hostname: str, available: bool) -> None:
        """
        Update client availability from WebSocket event.

        This is called when a client connects/disconnects or availability changes.
        Triggers zone volume recalculation if availability actually changed.
        """
        # Update state store asynchronously
        async def _update():
            try:
                await self._state_store.set_client_availability(hostname, available)
                await self._broadcast_volume_state(show_bar=False)
            except Exception as e:
                self.logger.error(f"Error updating client availability: {e}")

        task = asyncio.create_task(_update())
        task.add_done_callback(self._handle_broadcast_task_error)

    async def initialize_client_availability(self) -> None:
        """Initialize client availability from Snapcast on startup."""
        try:
            clients = await self.snapcast_service.get_clients()
            for client in clients:
                dsp_id = client.get("dsp_id", "")
                available = client.get("available", True)
                if dsp_id:
                    await self._state_store.set_client_availability(dsp_id, available)
                    self.logger.debug(f"Initialized availability: {dsp_id} -> {available}")
            self.logger.info(f"Initialized availability for {len(clients)} clients")
        except Exception as e:
            self.logger.warning(f"Failed to initialize client availability: {e}")

    async def _broadcast_volume_state(self, show_bar: bool = True) -> None:
        """Broadcast volume state immediately to WebSocket clients."""
        try:
            volume_state = await self.get_volume_state()

            event_data = {
                "show_bar": show_bar,
                "step_mobile_db": self.config.config.step_mobile_db,
                "state": volume_state.to_dict()
            }

            # Broadcast via state machine (WebSocket)
            await self.state_machine.broadcast_event("volume", "volume_changed", event_data)

            # Also emit via EventBus for internal listeners
            await self.event_bus.emit(Events.VOLUME_CHANGED, event_data)

            self.logger.debug(f"Volume broadcast completed: {len(volume_state.clients)} clients, {len(volume_state.zones)} zones")
        except Exception as e:
            self.logger.error(f"Error broadcasting volume state: {e}", exc_info=True)
            raise  # Re-raise so task error callback can handle it

    # ============================================================================
    # UTILITY METHODS
    # ============================================================================

    def get_volume_config_public(self) -> Dict[str, Any]:
        """Get current volume configuration."""
        return self._config_service.get_config_dict()

    async def get_status(self) -> dict:
        """Get complete volume service status."""
        try:
            volume_state = await self._state_store.get_complete_state()

            return {
                "volume_db": volume_state.global_volume_db,
                "multiroom_enabled": self._is_multiroom_enabled(),
                "dsp_available": self._is_dsp_available(),
                "config": self.get_volume_config_public(),
                "clients": {
                    hostname: {
                        "volume_db": client.volume_db,
                        "offset_db": client.offset_db,
                        "mute": client.mute,
                        "available": client.available
                    }
                    for hostname, client in volume_state.clients.items()
                },
                "zones": {
                    zone_id: {
                        "name": zone.name,
                        "average_volume_db": zone.average_volume_db,
                        "client_ids": zone.client_ids,
                        "all_muted": zone.all_muted
                    }
                    for zone_id, zone in volume_state.zones.items()
                }
            }
        except Exception as e:
            self.logger.error(f"Error getting status: {e}")
            return {"volume_db": -30.0, "error": str(e)}

    async def get_volume_state(self) -> VolumeState:
        """
        Get unified volume state (single source of truth).

        Returns a VolumeState with all volume data for both direct and multiroom modes.
        """
        return await self._state_store.get_complete_state()

    async def get_client_volume(self, hostname: str) -> dict:
        """
        Get volume for a specific client (works in both modes).

        Returns: {"main": volume_db, "mute": bool}
        """
        try:
            volume_state = await self._state_store.get_complete_state()
            client = volume_state.clients.get(hostname)
            if client:
                return {"main": client.volume_db, "mute": client.mute}
            return {"main": -30.0, "mute": False}
        except Exception as e:
            self.logger.error(f"Error getting client volume: {e}")
            return {"main": -30.0, "mute": False}

    async def cleanup(self) -> None:
        """Clean up resources. Currently a no-op as VolumeStateStore handles its own cleanup."""
        self.logger.info("VolumeService cleanup completed")
