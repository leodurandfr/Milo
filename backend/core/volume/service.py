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
    # HELPERS
    # ============================================================================

    async def _with_lock(self, func, *args, **kwargs):
        """Execute func with volume lock and 2s timeout."""
        try:
            async with asyncio.timeout(2.0):
                async with self._volume_lock:
                    return await func(*args, **kwargs)
        except asyncio.TimeoutError:
            self.logger.error("Timeout waiting for volume lock (>2s)")
            return False

    async def _check_dsp_or_error(self) -> bool:
        """Check DSP availability, broadcast error if unavailable. Returns True if OK."""
        if self._is_multiroom_enabled() or self._is_dsp_available():
            return True
        self.logger.warning("DSP not available, volume change blocked")
        await self.state_machine.broadcast_event("volume", "volume_error", {
            "error": "CamillaDSP not available",
            "dsp_available": False
        })
        return False

    async def _apply_to_multiroom_clients(self, updates: Dict[str, float]) -> bool:
        """Apply volume updates to multiroom clients and update state store."""
        if not updates:
            return True
        results = await self._dsp_controller.apply_volumes_parallel(updates)
        for hostname, volume in updates.items():
            if results.get(hostname, False):
                await self._state_store.set_client_volume(hostname, volume)
        if results and not any(results.values()):
            self.logger.warning(f"All {len(results)} multiroom clients failed volume update")
        return True  # Graceful degradation: clients will sync on reconnect

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

    async def _reload_config(self, name: str, broadcast: bool = False) -> bool:
        """Helper: reload config with optional broadcast."""
        try:
            await self._config_service.load()
            if broadcast:
                await self._broadcast_volume_state(show_bar=False)
            return True
        except Exception as e:
            self.logger.error(f"Error reloading {name}: {e}")
            return False

    async def reload_startup_config(self) -> bool:
        """Reload startup configuration."""
        return await self._reload_config("startup config")

    async def reload_volume_steps_config(self) -> bool:
        """Reload volume step configuration."""
        return await self._reload_config("volume steps", broadcast=True)

    async def reload_rotary_steps_config(self) -> bool:
        """Reload rotary encoder step configuration."""
        return await self._reload_config("rotary steps")

    # ============================================================================
    # CLIENT VOLUME MANAGEMENT (VolumeStateStore architecture)
    # ============================================================================

    async def sync_existing_client_from_snapcast(self, client_id: str) -> bool:
        """Sync reconnected client: mute -> set volume -> unmute to prevent volume spike."""
        if not self._is_multiroom_enabled():
            return True
        try:
            # Wait for client DSP to be ready
            if not await self._dsp_controller.wait_for_client_ready(client_id, max_wait=10.0):
                self.logger.error(f"DSP_SYNC: Client {client_id} DSP not ready, skipping")
                return False

            # Mute DSP first to prevent volume spike
            await self._dsp_controller.set_dsp_mute(client_id, True)
            volume_state = await self._state_store.get_complete_state()

            # Find client's zone (if any)
            client_zone_id = next(
                (zid for zid, zdata in volume_state.zones.items() if client_id in zdata.client_ids),
                None
            )

            # Determine target volume
            if client_zone_id:
                target = self._state_store.get_zone_target_volume(client_zone_id)
                expected_volume = target if target is not None else volume_state.zones[client_zone_id].average_volume_db
                self.logger.info(f"Syncing {client_id} in zone '{client_zone_id}': {expected_volume:.1f}dB")
            else:
                expected_volume = self._state_store.get_client_volume(client_id)
                if expected_volume is None:
                    expected_volume = volume_state.global_volume_db
                self.logger.info(f"Syncing {client_id} (no zone): {expected_volume:.1f}dB")

            # Apply volume while muted
            await self._dsp_controller.set_dsp_volume(client_id, expected_volume)

            # Apply persisted mute state
            client_state = self._state_store._clients.get(client_id)
            persisted_mute = client_state.mute if client_state else False
            await self._dsp_controller.set_dsp_mute(client_id, persisted_mute)

            await self._state_store.register_client(client_id, volume_db=expected_volume, available=True)
            await self._broadcast_volume_state(show_bar=False)
            return True
        except Exception as e:
            self.logger.error(f"Error syncing client {client_id}: {e}")
            try:
                await self._dsp_controller.set_dsp_mute(client_id, False)
            except Exception:
                pass
            return False

    async def sync_client_volume_from_external(self, client_id: str, volume_db: float) -> None:
        """Sync client volume from external change (e.g., MultiroomModal)."""
        if self._is_multiroom_enabled():
            await self.update_client_volume_db(client_id, volume_db, broadcast=True)

    async def sync_all_clients_from_dsp(self) -> bool:
        """Sync all client volumes from their DSP state (called when multiroom is enabled)."""
        if not self._is_multiroom_enabled():
            return True
        try:
            clients = await self.snapcast_service.get_clients()
            for client in clients:
                cid = client.get("dsp_id", "")
                if not cid:
                    continue
                # Read DSP volume
                if cid == 'local':
                    vol_data = await self._dsp_service.get_volume()
                else:
                    vol_data = await self._proxy_service.request(cid, "GET", "/dsp/volume")
                volume = vol_data.get("main", -30.0) if vol_data else -30.0
                await self._state_store.register_client(cid, volume_db=volume, available=client.get("available", True))

            self.logger.info(f"Synced {len(clients)} clients from DSP")
            await self._broadcast_volume_state(show_bar=False)
            return True
        except Exception as e:
            self.logger.error(f"Error syncing all clients from DSP: {e}")
            return False

    async def push_volume_to_all_clients(self) -> bool:
        """Push volume and mute state to all multiroom clients (respects startup settings)."""
        try:
            client_ids = await get_available_client_ids(self.snapcast_service)
            if not client_ids:
                return True

            restore_enabled = self.config.config.restore_last_volume
            startup_volume = self.config.config.startup_volume_db

            # Build volume updates based on startup settings
            updates = {}
            local_volume = None  # Lazy-loaded if needed
            for cid in client_ids:
                if restore_enabled and cid in self._state_store._clients:
                    updates[cid] = self._state_store._clients[cid].volume_db
                elif restore_enabled:
                    if local_volume is None:
                        dsp_state = await self._dsp_service.get_volume()
                        local_volume = dsp_state.get("main", -30.0) if dsp_state else -30.0
                    updates[cid] = local_volume
                else:
                    updates[cid] = startup_volume

            if not updates:
                return True

            self.logger.info(f"Pushing {'persisted' if restore_enabled else f'startup ({startup_volume:.1f}dB)'} volumes to {len(updates)} clients")

            # Apply volumes and update state store
            results = await self._dsp_controller.apply_volumes_parallel(updates)
            for hostname, volume in updates.items():
                if results.get(hostname, False):
                    await self._state_store.set_client_volume(hostname, volume)

            # Apply persisted mute states
            for cid in client_ids:
                if cid in self._state_store._clients:
                    try:
                        await self._dsp_controller.set_dsp_mute(cid, self._state_store._clients[cid].mute)
                    except Exception as e:
                        self.logger.warning(f"Failed to apply mute to {cid}: {e}")

            await self._broadcast_volume_state(show_bar=False)

            failures = [h for h, ok in results.items() if not ok]
            if failures:
                self.logger.warning(f"Failed to push volume to: {failures}")
            return len(failures) == 0
        except Exception as e:
            self.logger.error(f"Error pushing volume to clients: {e}")
            return False

    async def update_client_volume_db(self, client_id: str, volume_db: float, broadcast: bool = True) -> None:
        """Update client volume in dB (called from API routes)."""
        try:
            await self._state_store.set_client_volume(client_id, volume_db)
            await self._dsp_controller.set_dsp_volume(client_id, volume_db)
            if broadcast and self._is_multiroom_enabled():
                await self._broadcast_volume_state(show_bar=False)
        except Exception as e:
            self.logger.error(f"Error updating client {client_id} volume: {e}")

    async def set_client_mute(self, client_id: str, mute: bool, broadcast: bool = True) -> None:
        """Set mute state for a client."""
        try:
            await self._state_store.set_client_mute(client_id, mute)
            await self._dsp_controller.set_dsp_mute(client_id, mute)
            if broadcast:
                await self._broadcast_volume_state(show_bar=False)
        except Exception as e:
            self.logger.error(f"Error setting client {client_id} mute: {e}")

    # ============================================================================
    # ATOMIC ZONE OPERATIONS
    # ============================================================================

    async def apply_zone_volume_delta(self, zone_id: str, delta_db: float) -> float:
        """Apply volume delta to entire zone atomically. Returns new zone average in dB."""
        async with self._volume_lock:
            try:
                self._state_store.clear_zone_targets()
                updates = await self._state_store.apply_zone_delta(zone_id, delta_db)

                if not updates:
                    self.logger.warning(f"No clients to update in zone {zone_id}")
                    return self._state_store.compute_zone_average(zone_id)

                self.logger.info(f"Applying zone delta: {zone_id} {delta_db:+.1f}dB -> {len(updates)} clients")
                results = await self._dsp_controller.apply_volumes_parallel(updates)

                # Update state store with successful updates
                successful = {h: v for h, v in updates.items() if results.get(h, False)}
                await self._state_store.apply_zone_updates(successful)

                failures = [h for h, ok in results.items() if not ok]
                if failures:
                    self.logger.warning(f"Failed to update clients: {failures}")

                await self._broadcast_volume_state(show_bar=False)

                new_avg = self._state_store.compute_zone_average(zone_id)
                self.logger.info(f"Zone {zone_id} updated: {new_avg:.1f}dB ({len(successful)}/{len(updates)} success)")
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
        """Apply startup volume and mute state to CamillaDSP (starts muted via -m flag)."""
        try:
            # Wait for CamillaDSP connection
            if self._dsp_service:
                if not await self._dsp_service.wait_for_connection(timeout=10.0):
                    self.logger.warning("CamillaDSP not connected after 10s, startup volume not applied")
                    return

            # Determine target volume
            restore_enabled = self.config.config.restore_last_volume
            if restore_enabled:
                local_client = self._state_store._clients.get('local')
                target_volume = local_client.volume_db if local_client else self._state_store._local_volume_db
                self.logger.info(f"Restoring persisted volume: {target_volume:.1f} dB")
            else:
                target_volume = self.config.config.startup_volume_db
                self.logger.info(f"Using startup volume: {target_volume:.1f} dB")

            # Get persisted mute state
            local_client = self._state_store._clients.get('local')
            local_mute = local_client.mute if local_client else False

            if target_volume is not None and self._dsp_controller:
                await self._dsp_controller.set_dsp_volume("local", target_volume)
                await self._dsp_controller.set_dsp_mute("local", local_mute)
                self.logger.info(f"Startup state applied: {target_volume:.1f} dB, mute={local_mute}")
            elif self._dsp_controller:
                await self._dsp_controller.set_dsp_mute("local", False)
        except Exception as e:
            self.logger.error(f"Failed to apply startup volume: {e}")

    async def _startup_broadcast_after_websocket_ready(self):
        """Wait for Snapcast WebSocket and broadcast initial volume state."""
        try:
            multiroom_enabled = await self.settings_service.get_setting("routing.multiroom_enabled") or False

            if multiroom_enabled and self._snapcast_websocket_service:
                ws_ready = await self._snapcast_websocket_service.wait_for_ready(timeout=30.0)
                if ws_ready:
                    self.logger.info("WebSocket ready, syncing clients")
                    await self.initialize_client_availability()
                    self._availability_ready.set()
                    await self.push_volume_to_all_clients()
                else:
                    self.logger.warning("Snapcast WebSocket not ready after timeout")
                    self._availability_ready.set()
            else:
                await asyncio.sleep(0.5)
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
        """Set volume to specific level in dB (-80 to 0)."""
        async def _do_set():
            try:
                if not await self._check_dsp_or_error():
                    return False
                clamped_db = self._config_service.config.clamp(volume_db)
                success = await self._apply_volume_db(clamped_db)
                if success:
                    self._save_last_volume(clamped_db)
                    await self._broadcast_volume_state(show_bar)
                return success
            except Exception as e:
                self.logger.error(f"Error setting volume: {e}")
                return False
        return await self._with_lock(_do_set)

    async def _apply_volume_db(self, volume_db: float) -> bool:
        """Apply volume to DSP (local or multiroom)."""
        try:
            if self._is_multiroom_enabled():
                client_ids = await get_available_client_ids(self.snapcast_service)
                updates = {cid: volume_db for cid in client_ids} if client_ids else {}
                return await self._apply_to_multiroom_clients(updates)
            else:
                success = await self._dsp_service.set_volume(volume_db)
                if success:
                    self._state_store.set_local_volume(volume_db)
                return success
        except Exception as e:
            self.logger.error(f"Error applying volume: {e}")
            return False

    async def adjust_volume_db(self, delta_db: float, show_bar: bool = True) -> bool:
        """Adjust volume by delta in dB (positive = louder, negative = quieter)."""
        async def _do_adjust():
            try:
                if not await self._check_dsp_or_error():
                    return False
                success = await self._apply_delta_db(delta_db)
                if success:
                    volume_state = await self._state_store.get_complete_state()
                    self._save_last_volume(volume_state.global_volume_db)
                    await self._broadcast_volume_state(show_bar)
                return success
            except Exception as e:
                self.logger.error(f"Error adjusting volume: {e}")
                return False
        return await self._with_lock(_do_adjust)

    async def _apply_delta_db(self, delta_db: float) -> bool:
        """Apply volume delta in dB."""
        try:
            volume_state = await self._state_store.get_complete_state()
            if self._is_multiroom_enabled():
                client_ids = await get_available_client_ids(self.snapcast_service)
                updates = {}
                for cid in client_ids or []:
                    current = volume_state.clients.get(cid)
                    if current:
                        updates[cid] = self._config_service.config.clamp(current.volume_db + delta_db)
                return await self._apply_to_multiroom_clients(updates)
            else:
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
            vs = await self._state_store.get_complete_state()
            return {
                "volume_db": vs.global_volume_db,
                "multiroom_enabled": self._is_multiroom_enabled(),
                "dsp_available": self._is_dsp_available(),
                "config": self.get_volume_config_public(),
                "clients": {h: {"volume_db": c.volume_db, "offset_db": c.offset_db, "mute": c.mute, "available": c.available}
                           for h, c in vs.clients.items()},
                "zones": {zid: {"name": z.name, "average_volume_db": z.average_volume_db, "client_ids": z.client_ids, "all_muted": z.all_muted}
                          for zid, z in vs.zones.items()}
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
