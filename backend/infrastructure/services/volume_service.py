# backend/infrastructure/services/volume_service.py
"""
Volume management service - CamillaDSP always active.

All volume values are in decibels (-80 to 0 dB).
ALSA is set to 100% passthrough - volume control is entirely via CamillaDSP.

REFACTORED ARCHITECTURE (Phase 5):
- VolumeStateStore: Single source of truth for all volume state
- DSPController: Hardware abstraction for parallel volume updates
- VolumeService: Orchestration layer only
"""
import asyncio
import logging
from typing import Optional, Dict, Any

from backend.infrastructure.services.settings_service import SettingsService
from backend.infrastructure.services.volume_converter_service import VolumeConverterService
from backend.infrastructure.services.volume_config_service import VolumeConfigService
from backend.infrastructure.services.volume_storage_service import VolumeStorageService
from backend.infrastructure.services.volume_state_store import VolumeStateStore
from backend.infrastructure.services.dsp_controller import DSPController
from backend.domain.volume_state import VolumeState, ClientVolume


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
        VolumeService: Orchestration (API ↔ State ↔ Hardware)
    """

    def __init__(self, state_machine, snapcast_service, settings_service=None,
                 camilladsp_service=None, dsp_client_proxy_service=None):
        self.state_machine = state_machine
        self.snapcast_service = snapcast_service
        self.settings_service = settings_service if settings_service is not None else SettingsService()
        self._dsp_service = camilladsp_service
        self._proxy_service = dsp_client_proxy_service
        self.logger = logging.getLogger(__name__)
        self._volume_lock = asyncio.Lock()

        # Initialize sub-services
        self._config_service = VolumeConfigService(self.settings_service)
        self._converter = VolumeConverterService()
        self._storage = VolumeStorageService()

        # NEW ARCHITECTURE: VolumeStateStore (SSOT) + DSPController (hardware abstraction)
        self._state_store = VolumeStateStore(self.settings_service)
        self._dsp_controller = DSPController(self._dsp_service, self._proxy_service)

    # ============================================================================
    # EXPOSED SUB-SERVICES
    # ============================================================================

    @property
    def converter(self) -> VolumeConverterService:
        """Access to volume converter service."""
        return self._converter

    @property
    def config(self) -> VolumeConfigService:
        """Access to volume configuration service."""
        return self._config_service

    @property
    def storage(self) -> VolumeStorageService:
        """Access to volume storage service for persistence."""
        return self._storage

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

    # ============================================================================
    # CONFIGURATION LOADING
    # ============================================================================

    async def _load_volume_config(self) -> None:
        """Load volume configuration from settings asynchronously."""
        await self._config_service.load()
        self._converter.update_limits(
            self._config_service.config.limit_min_db,
            self._config_service.config.limit_max_db
        )

    def _save_last_volume(self, volume_db: float) -> None:
        """Save last volume in background."""
        self._storage.save(volume_db, self.config.config.restore_last_volume)

    def _determine_startup_volume_db(self) -> float:
        """Determine startup volume in dB (restored or default)."""
        return self._storage.get_startup_volume(
            self.config.config.startup_volume_db,
            self.config.config.restore_last_volume
        )

    async def reload_volume_limits(self) -> bool:
        """Reload volume limits from settings and adjust current volume if needed."""
        try:
            volume_state = await self._state_store.get_complete_state()
            current_db = volume_state.display_volume_db
            old_min_db, old_max_db = await self._config_service.reload_limits()

            self._converter.update_limits(
                self._config_service.config.limit_min_db,
                self._config_service.config.limit_max_db
            )

            # No change, nothing to do
            if (old_min_db == self.config.config.limit_min_db and
                    old_max_db == self.config.config.limit_max_db):
                return True

            # Check if current volume is outside new limits
            new_min = self.config.config.limit_min_db
            new_max = self.config.config.limit_max_db

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

    def invalidate_client_caches(self) -> None:
        """
        Invalidate client caches (called when toggling multiroom).

        NOTE: With VolumeStateStore, this is a no-op since there are no caches.
        State is always consistent and computed on-demand.
        """
        self.logger.debug("invalidate_client_caches called (no-op with VolumeStateStore)")

    # ============================================================================
    # CLIENT VOLUME MANAGEMENT (New architecture using VolumeStateStore)
    # ============================================================================

    async def initialize_new_client_volume(self, client_id: str) -> bool:
        """Initialize new client and apply startup volume in multiroom mode."""
        if not self._is_multiroom_enabled():
            return True

        try:
            startup_db = self._determine_startup_volume_db()
            await self._state_store.register_client(client_id, volume_db=startup_db, available=True)

            # Apply volume to hardware
            success = await self._dsp_controller.set_dsp_volume(client_id, startup_db)
            if not success:
                self.logger.warning(f"Failed to apply volume to new client {client_id}")

            await self._broadcast_volume_state(show_bar=False)
            return success
        except Exception as e:
            self.logger.error(f"Error initializing new client {client_id}: {e}")
            return False

    async def sync_existing_client_from_snapcast(self, client_id: str) -> bool:
        """
        Sync reconnected client: apply correct volume to DSP.

        Volume selection priority:
        1. If client is in a zone -> ALWAYS use zone's current average (consistency)
        2. If client not in zone -> use persisted volume or display volume
        """
        if not self._is_multiroom_enabled():
            return True

        try:
            volume_state = await self._state_store.get_complete_state()

            # FIRST: Check if client is in any zone
            client_zone_id = None
            for zone_id, zone_data in volume_state.zones.items():
                if client_id in zone_data.client_ids:
                    client_zone_id = zone_id
                    break

            if client_zone_id:
                # Client is in a zone - ALWAYS use zone average for consistency
                expected_volume = volume_state.zones[client_zone_id].average_volume_db
                self.logger.info(f"Reconnecting client {client_id} in zone '{client_zone_id}', using zone volume: {expected_volume:.1f}dB")
            else:
                # Client not in a zone - use persisted volume or display volume
                expected_volume = self._state_store.get_client_volume(client_id)

                if expected_volume is None:
                    expected_volume = volume_state.display_volume_db
                    self.logger.info(f"New client {client_id}, applying display volume: {expected_volume:.1f}dB")
                else:
                    self.logger.info(f"Reconnected client {client_id} (no zone), applying persisted volume: {expected_volume:.1f}dB")

            # PUSH the correct volume to client DSP
            await self._dsp_controller.set_dsp_volume(client_id, expected_volume)

            # Register client with the applied volume
            await self._state_store.register_client(client_id, volume_db=expected_volume, available=True)
            await self._broadcast_volume_state(show_bar=False)
            return True
        except Exception as e:
            self.logger.error(f"Error syncing existing client {client_id}: {e}")
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
        Push current local volume to all multiroom clients.
        Called when multiroom is activated to ensure uniform volume.
        """
        try:
            # Get local DSP volume
            dsp_state = await self._dsp_service.get_volume()
            if dsp_state and "main" in dsp_state:
                local_volume = dsp_state["main"]
                self.logger.info(f"Pushing local volume {local_volume:.1f} dB to all clients")
            else:
                local_volume = -30.0
                self.logger.warning(f"Could not read local volume, using default {local_volume:.1f} dB")

            # Get all clients
            clients = await self.snapcast_service.get_clients()
            updates = {}
            for client in clients:
                client_id = client.get("dsp_id", "")
                if client_id and client.get("available", True):
                    updates[client_id] = local_volume

            if not updates:
                return True

            # Apply to all clients in parallel
            results = await self._dsp_controller.apply_volumes_parallel(updates)

            # Update state store
            successful_updates = {
                hostname: volume
                for hostname, volume in updates.items()
                if results.get(hostname, False)
            }

            for hostname, volume in successful_updates.items():
                await self._state_store.set_client_volume(hostname, volume)

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

            # Broadcast if requested
            if broadcast:
                await self._broadcast_volume_state(show_bar=False)

        except Exception as e:
            self.logger.error(f"Error setting client {client_id} mute: {e}")

    # ============================================================================
    # ATOMIC ZONE OPERATIONS (Phase 2-4 refactoring)
    # ============================================================================

    async def apply_zone_volume_delta(self, zone_id: str, delta_db: float) -> float:
        """
        Apply volume delta to entire zone atomically.

        This is the NEW refactored method that uses VolumeStateStore + DSPController.
        It calculates updates for all clients, applies them in parallel, then broadcasts once.

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
                # 1. Calculate volume updates for all clients in zone
                updates = await self._state_store.apply_zone_delta(zone_id, delta_db)

                if not updates:
                    self.logger.warning(f"No clients to update in zone {zone_id}")
                    return self._state_store.compute_zone_average(zone_id)

                # 2. Apply updates to hardware in parallel
                self.logger.info(f"Applying zone delta: {zone_id} Δ{delta_db:+.1f}dB → {len(updates)} clients")
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

            # Set ALSA to 100% passthrough - permanent (volume is via CamillaDSP)
            await self._set_alsa_passthrough()
            self.logger.info("ALSA set to 100% passthrough mode")

            # Initialize client availability from Snapcast
            await self.initialize_client_availability()

            # Delayed initial broadcast
            asyncio.create_task(self._delayed_initial_broadcast())
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize: {e}")
            return False

    async def _delayed_initial_broadcast(self):
        """Send initial volume broadcast after short delay."""
        try:
            await asyncio.sleep(0.5)
            await self._broadcast_volume_state(show_bar=False)
        except Exception as e:
            self.logger.error(f"Error in delayed broadcast: {e}")

    # ============================================================================
    # PUBLIC API (all in dB)
    # ============================================================================

    async def get_volume_db(self) -> float:
        """Get current volume in dB (average of non-muted clients in multiroom mode)."""
        volume_state = await self._state_store.get_complete_state()
        return volume_state.display_volume_db

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
                        clamped_db = self._converter.clamp_db(volume_db)

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
                clients = await self.snapcast_service.get_clients()
                updates = {}
                for client in clients:
                    client_id = client.get("dsp_id", "")
                    if client_id and client.get("available", True):
                        updates[client_id] = volume_db

                if not updates:
                    return True

                # Apply in parallel
                results = await self._dsp_controller.apply_volumes_parallel(updates)

                # Update state store
                for hostname, volume in updates.items():
                    if results.get(hostname, False):
                        await self._state_store.set_client_volume(hostname, volume)

                return all(results.values())
            else:
                # LOCAL: Direct CamillaDSP control
                return await self._dsp_service.set_volume(volume_db)

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
                            self._save_last_volume(volume_state.display_volume_db)
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
                clients = await self.snapcast_service.get_clients()
                updates = {}

                volume_state = await self._state_store.get_complete_state()
                for client in clients:
                    client_id = client.get("dsp_id", "")
                    if client_id and client.get("available", True):
                        current = volume_state.clients.get(client_id)
                        if current:
                            new_vol = self._converter.clamp_db(current.volume_db + delta_db)
                            updates[client_id] = new_vol

                if not updates:
                    return True

                # Apply in parallel
                results = await self._dsp_controller.apply_volumes_parallel(updates)

                # Update state store
                for hostname, volume in updates.items():
                    if results.get(hostname, False):
                        await self._state_store.set_client_volume(hostname, volume)

                return all(results.values())
            else:
                # LOCAL: Apply delta to CamillaDSP
                volume_state = await self._state_store.get_complete_state()
                new_db = self._converter.clamp_db(volume_state.display_volume_db + delta_db)
                success = await self._dsp_service.set_volume(new_db)
                if success:
                    await self._state_store.set_client_volume('local', new_db)
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
                    self.logger.debug(f"Initialized availability: {dsp_id} → {available}")
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

            await self.state_machine.broadcast_event("volume", "volume_changed", event_data)
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
                "volume_db": volume_state.display_volume_db,
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

    def get_client_volume(self, hostname: str) -> dict:
        """
        Get volume for a specific client (works in both modes).

        Returns: {"main": volume_db, "mute": bool}

        Note: This is a synchronous wrapper for compatibility with existing code.
        Consider migrating callers to async get_volume_state() instead.
        """
        # This is a synchronous method but needs async data
        # Create a task to get the data
        async def _get():
            volume_state = await self._state_store.get_complete_state()
            client = volume_state.clients.get(hostname)
            if client:
                return {"main": client.volume_db, "mute": client.mute}
            return {"main": -30.0, "mute": False}

        # Run in event loop
        try:
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(_get())
        except Exception as e:
            self.logger.error(f"Error getting client volume: {e}")
            return {"main": -30.0, "mute": False}

    async def cleanup(self) -> None:
        """Clean up and wait for pending tasks to complete."""
        try:
            await self._storage.cleanup()
            self.logger.info("VolumeService cleanup completed")
        except Exception as e:
            self.logger.error(f"Error during volume service cleanup: {e}")
