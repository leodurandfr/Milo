# backend/core/volume/service.py
"""
Volume management service - CamillaDSP always active.

All volume values are in decibels (-80 to 0 dB).
ALSA is set to 100% passthrough - volume control is entirely via CamillaDSP.

Architecture:
- VolumeStateStore: Single source of truth for all volume state
- EqualizerController: Hardware abstraction for parallel volume updates
- VolumeService: Orchestration layer only
"""
import asyncio
import logging
from typing import Optional, Dict

from backend.shared.decorators import handle_errors
from backend.core.volume.state import VolumeStateStore
from backend.core.volume.equalizer_controller import EqualizerController
from backend.core.multiroom.snapcast import get_online_client_ids
from backend.core.models.volume import VolumeConfig
from backend.core.models.volume_state import VolumeState
from backend.config.constants import DEFAULT_VOLUME_DB


class VolumeService:
    """
    System volume management service.

    Volume is ALWAYS controlled via CamillaDSP in dB (-80 to 0).
    - Direct mode: Single local CamillaDSP control
    - Multiroom mode: CamillaDSP volume synchronized across all clients

    ALSA Digital mixer is set to 100% passthrough and never changed.

    Architecture:
        VolumeStateStore: Single source of truth (state + zones + clients)
        EqualizerController: Hardware abstraction (local + remote equalizer updates)
        VolumeService: Orchestration (API -> State -> Hardware)
    """

    def __init__(self, state_machine, snapcast_service, settings_service=None,
                 camilladsp_service=None, equalizer_client_proxy_service=None,
                 hardware_service=None, equalizer_router=None):
        self.state_machine = state_machine
        self.snapcast_service = snapcast_service
        self.settings_service = settings_service
        self._camilladsp_service = camilladsp_service
        self._proxy_service = equalizer_client_proxy_service
        self._hardware_service = hardware_service
        self.logger = logging.getLogger(__name__)
        self._volume_lock = asyncio.Lock()
        self._push_lock = asyncio.Lock()

        # Volume configuration (loaded from settings in _load_volume_config)
        self._volume_config = VolumeConfig()

        # Volume control flag (False = DAC mode, external amp manages volume)
        self._volume_control: bool = True

        # VolumeStateStore (SSOT) + EqualizerController (hardware abstraction)
        self._state_store = VolumeStateStore(self.settings_service)
        self._equalizer_controller = EqualizerController(
            self._camilladsp_service, self._proxy_service, equalizer_router=equalizer_router
        )

        # Injected via setters to resolve circular dependencies
        self._snapcast_websocket_service = None
        self._client_registry = None
        self._routing_service = None

        # Event to signal when client availability has been initialized (for WebSocket handshake)
        self._availability_ready = asyncio.Event()

    def set_client_registry(self, registry):
        """Set client registry (for dependency injection after init)."""
        self._client_registry = registry
        self._equalizer_controller.set_registry(registry)

    def set_routing_service(self, routing_service) -> None:
        """Set routing service reference (circular dependency resolution)."""
        self._routing_service = routing_service

    # ============================================================================
    # HELPERS
    # ============================================================================

    async def _check_equalizer_or_error(self) -> bool:
        """Check equalizer availability. Returns True if OK."""
        if self._is_multiroom_enabled() or self._is_equalizer_available():
            return True
        self.logger.warning("Equalizer not available, volume change blocked")
        return False

    async def _get_controllable_client_ids(self) -> list:
        """Fetch online client IDs that have volume control (excludes DAC clients)."""
        client_ids = await get_online_client_ids(self.snapcast_service) if self._is_multiroom_enabled() else []
        return [cid for cid in client_ids if self._state_store.has_volume_control(cid)]

    async def _compute_multiroom_updates(self, target_db: float,
                                         client_ids: list) -> Optional[Dict[str, float]]:
        """Compute per-client volume updates for multiroom mode.

        Must be called with _volume_lock held. Reads state and computes
        deltas but does NOT write to memory (state is committed after
        hardware application succeeds, via set_client_volume).

        Args:
            target_db: Target global volume in dB.
            client_ids: Online client IDs (fetched before lock acquisition).

        Returns:
            Dict of {mac_id: volume_db} for multiroom, None for direct mode.
        """
        if not self._is_multiroom_enabled():
            self._state_store.set_local_volume(target_db)
            return None

        if not client_ids:
            return {}

        volume_state = await self._state_store.get_complete_state()
        delta = target_db - volume_state.global_volume_db
        updates = {}
        for cid in client_ids:
            current = volume_state.clients.get(cid)
            if current:
                updates[cid] = self._volume_config.clamp(current.volume_db + delta)
        return updates

    async def _apply_volume_to_hardware(self, target_db: float, updates: Optional[Dict[str, float]]) -> bool:
        """Apply volume to hardware outside the lock.

        Args:
            target_db: Target volume in dB (used for direct mode CamillaDSP call).
            updates: Per-client updates from _compute_multiroom_updates, or None for direct mode.
        """
        if updates is None:
            # Direct mode: apply to local CamillaDSP
            success = await self._camilladsp_service.set_volume(target_db)
            if not success:
                self.logger.warning(f"Direct mode: CamillaDSP set_volume({target_db:.1f}dB) failed — audio may be silent")
            return success
        if not updates:
            return True
        # Multiroom: fan-out to all clients, commit state only on success
        results = await self._equalizer_controller.apply_volumes_parallel(updates)
        failed = []
        for hostname, volume in updates.items():
            if results.get(hostname, False):
                await self._state_store.set_client_volume(hostname, volume)
            else:
                failed.append(hostname)
        if failed:
            self.logger.warning(f"Multiroom volume update failed for {len(failed)}/{len(results)} clients: {failed}")
        # Local client failure is critical — server audio may be silent
        local_mac = self._state_store.local_mac_id
        local_failed = local_mac and local_mac in updates and not results.get(local_mac, False)
        if local_failed:
            self.logger.error(f"LOCAL server volume update failed — server audio may be silent")
            return False
        return True  # Remote failures degrade gracefully: clients will sync on reconnect

    # ============================================================================
    # EXPOSED SUB-SERVICES
    # ============================================================================

    @property
    def volume_config(self) -> VolumeConfig:
        """Access to volume configuration."""
        return self._volume_config

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
        """Set ALSA mixer to 100% passthrough (volume is via CamillaDSP).

        Reads the mixer control name from hardware.json (set during installation).
        Falls back to trying common mixer names if not configured.
        """
        # Try configured mixer from hardware.json first
        configured_control = None
        if self._hardware_service:
            configured_control = self._hardware_service.get_alsa_control()

        if configured_control:
            controls_to_try = [configured_control]
        else:
            controls_to_try = ["Digital", "DAC", "Master"]

        for control in controls_to_try:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "amixer", "-M", "set", control, "100%",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
                )
                await proc.communicate()
                if proc.returncode == 0:
                    self.logger.info(f"ALSA passthrough set via '{control}' mixer")
                    return True
            except Exception as e:
                self.logger.warning(f"Failed to set ALSA passthrough via '{control}': {e}")

        self.logger.error("Could not set ALSA passthrough (no working mixer control found)")
        return False

    def _is_multiroom_enabled(self) -> bool:
        """Check if multiroom mode is currently enabled."""
        try:
            if not self._routing_service:
                return False
            return self._routing_service.get_state().get('multiroom_enabled', False)
        except Exception as e:
            self.logger.warning(f"Failed to check multiroom state: {e}")
            return False

    def _is_equalizer_available(self) -> bool:
        """Check if CamillaDSP is connected and available for volume control."""
        if not self._camilladsp_service:
            return False
        return self._camilladsp_service.is_volume_control_available()

    async def update_volume_mode(self, multiroom_enabled: bool) -> float:
        """
        Update volume mode when multiroom state changes.

        Ensures volume consistency when switching modes:
        - TO multiroom: returns current local volume to use for all clients
        - TO direct: sets local volume to current global (average of clients)

        Args:
            multiroom_enabled: Whether multiroom is now enabled

        Returns:
            The volume to use for the new mode (for multiroom: local volume to push)
        """
        # DAC mode: switch mode and broadcast (any_volume_control depends on mode)
        if not self._volume_control:
            await self._state_store.set_mode("multiroom" if multiroom_enabled else "direct")
            await self._broadcast_volume_state(show_bar=False)
            return None

        if multiroom_enabled:
            # Switching TO multiroom: get current local volume BEFORE mode change
            current_local = self._state_store.local_volume_db
            self.logger.info(f"Switching to multiroom: using local volume {current_local:.1f} dB for all clients")

            await self._state_store.set_mode("multiroom")
            return current_local
        else:
            # Switching TO direct: get current global volume BEFORE mode change
            volume_state = await self._state_store.get_complete_state()
            current_global = volume_state.global_volume_db
            self.logger.info(f"Switching to direct: using global volume {current_global:.1f} dB for local")

            await self._state_store.set_mode("direct")

            # Set local volume to the previous global
            self._state_store.set_local_volume(current_global)

            # Apply to CamillaDSP (volume + unmute to ensure sound works after multiroom)
            try:
                await self._camilladsp_service.set_volume(current_global)
                await self._camilladsp_service.set_mute(False)
                self.logger.info(f"Applied volume {current_global:.1f} dB to CamillaDSP (unmuted)")
            except Exception as e:
                self.logger.warning(f"Failed to apply volume/mute to CamillaDSP: {e}")

            await self._broadcast_volume_state(show_bar=False)
            return current_global

    # ============================================================================
    # CONFIGURATION LOADING
    # ============================================================================

    async def _load_volume_config(self) -> None:
        """Load volume configuration from settings."""
        try:
            self.settings_service.invalidate_cache()
            volume_settings = await self.settings_service.get_setting('volume') or {}

            self._volume_config = VolumeConfig(
                limit_min_db=volume_settings.get("limit_min_db", -80.0),
                limit_max_db=volume_settings.get("limit_max_db", -21.0),
                step_mobile_db=volume_settings.get("step_mobile_db", 3.0),
                step_rotary_db=volume_settings.get("step_rotary_db", 2.0),
                step_bt_remote_db=volume_settings.get("step_bt_remote_db", 2.0),
                startup_volume_db=volume_settings.get("startup_volume_db", DEFAULT_VOLUME_DB),
                restore_last_volume=volume_settings.get("restore_last_volume", False)
            )
        except Exception as e:
            self.logger.error(f"Error loading volume config: {e}")
        finally:
            # Always sync state store even on partial failure
            self._state_store.set_volume_config(self._volume_config)

    @handle_errors(default=False)
    async def reload_volume_limits(self) -> bool:
        """Reload volume limits from settings and adjust current volume if needed."""
        volume_state = await self._state_store.get_complete_state()
        current_db = volume_state.global_volume_db
        old_min = self._volume_config.limit_min_db
        old_max = self._volume_config.limit_max_db

        await self._load_volume_config()

        new_min = self._volume_config.limit_min_db
        new_max = self._volume_config.limit_max_db

        # No change, nothing to do
        if old_min == new_min and old_max == new_max:
            return True

        # Check if current volume is outside new limits
        if current_db < new_min or current_db > new_max:
            # Move to center of new range
            center_db = (new_min + new_max) / 2.0
            await self.set_volume_db(center_db, show_bar=False)
        else:
            await self._broadcast_volume_state(show_bar=False)

        return True

    # ============================================================================
    # STARTUP VOLUME AUTO-UPDATE (FR11)
    # ============================================================================

    @handle_errors(default=None)
    async def _update_startup_volume_if_needed(self, volume_db: float) -> None:
        """
        Auto-update startup_volume_db to track current volume (FR11).

        When restore_last_volume is enabled, startup_volume_db tracks the current volume
        so it can be restored correctly at startup/restart (direct and multiroom).
        When disabled, startup_volume_db stays at the user-configured fixed value.

        Args:
            volume_db: The new volume level in dB to potentially save as startup volume
        """
        if not self._volume_config.restore_last_volume:
            return

        current_startup = self._volume_config.startup_volume_db
        # Skip if unchanged (avoid unnecessary writes) - 0.1 dB tolerance
        if abs(current_startup - volume_db) < 0.1:
            return

        # Update setting atomically via SettingsService
        await self.settings_service.set_setting('volume.startup_volume_db', volume_db)

        # Reload config to get fresh value
        await self._load_volume_config()

        # Broadcast the actual persisted value from config (ensures consistency)
        persisted_value = self._volume_config.startup_volume_db
        await self._broadcast_startup_volume_changed(persisted_value)

        self.logger.info(f"FR11: Auto-updated startup_volume_db to {persisted_value:.1f} dB")

    @handle_errors(default=None)
    async def _broadcast_startup_volume_changed(self, volume_db: float) -> None:
        """
        Broadcast startup volume change via WebSocket (FR11).

        Args:
            volume_db: The new startup volume in dB
        """
        await self.state_machine.broadcast_event(
            "settings",
            "volume_startup_changed",
            {
                "config": {
                    "startup_volume_db": volume_db,
                    "restore_last_volume": self._volume_config.restore_last_volume
                }
            }
        )

    @handle_errors(default=False)
    async def _reload_config(self, broadcast: bool = False) -> bool:
        """Helper: reload config with optional broadcast."""
        await self._load_volume_config()
        if broadcast:
            await self._broadcast_volume_state(show_bar=False)
        return True

    async def reload_startup_config(self) -> bool:
        """Reload startup configuration."""
        return await self._reload_config()

    async def reload_volume_steps_config(self) -> bool:
        """Reload volume step configuration."""
        return await self._reload_config(broadcast=True)

    async def reload_steps_config(self) -> bool:
        """Reload hardware step configuration (rotary encoder, BT remote)."""
        return await self._reload_config()

    # ============================================================================
    # CLIENT VOLUME MANAGEMENT (VolumeStateStore architecture)
    # ============================================================================

    @handle_errors(default=False)
    async def sync_all_clients_from_equalizer(self) -> bool:
        """Sync all client volumes from their equalizer state (called when multiroom is enabled)."""
        if not self._is_multiroom_enabled():
            return True

        registry = self._client_registry
        clients = await self.snapcast_service.get_clients()
        for client in clients:
            cid = client.get("mac_id", "")
            if not cid:
                continue
            # Read equalizer volume (local client uses local CamillaDSP, others use proxy)
            client_info = registry.get_client(cid) if registry else None
            if client_info and client_info.ip == "127.0.0.1":
                vol_data = await self._camilladsp_service.get_volume()
            elif client_info and client_info.ip:
                # Use IP address for proxy request (never mac_id)
                vol_data = await self._proxy_service.request(client_info.ip, "GET", "/equalizer/volume")
            else:
                self.logger.warning(f"Cannot sync client {cid}: no IP address in registry")
                continue
            volume = vol_data.get("main", DEFAULT_VOLUME_DB) if vol_data else DEFAULT_VOLUME_DB
            await self._state_store.register_client(cid, volume_db=volume, available=client.get("available", True))

        self.logger.info(f"Synced {len(clients)} clients from equalizer")
        await self._broadcast_volume_state(show_bar=False)
        return True

    @handle_errors(default=False)
    async def push_volume_to_all_clients(self, target_volume_db: Optional[float] = None) -> bool:
        """
        Push volume and mute state to all multiroom clients.

        Args:
            target_volume_db: If provided, use this volume for ALL clients (mode switch).
                             If None, respect startup settings (restore/startup volume).
        """
        try:
            async with asyncio.timeout(10.0):
                async with self._push_lock:
                    return await self._do_push_volume_to_all_clients(target_volume_db)
        except asyncio.TimeoutError:
            self.logger.warning("Timeout waiting for push lock (>10s)")
            return False

    async def _do_push_volume_to_all_clients(self, target_volume_db: Optional[float] = None) -> bool:
        """Internal push implementation (called under _push_lock)."""
        client_ids = await get_online_client_ids(self.snapcast_service)
        if not client_ids:
            self.logger.warning("PUSH_VOLUME: No online clients found — nothing to push")
            return True
        self.logger.info(f"PUSH_VOLUME: Found {len(client_ids)} online clients: {client_ids}")

        # Build volume updates
        updates = {}

        if target_volume_db is not None:
            # Mode switch: use target volume for all clients
            for cid in client_ids:
                updates[cid] = target_volume_db
            self.logger.info(f"Pushing mode-switch volume ({target_volume_db:.1f}dB) to {len(updates)} clients")
        else:
            # Startup: respect restore/startup settings
            restore_enabled = self._volume_config.restore_last_volume
            startup_volume = self._volume_config.startup_volume_db

            local_volume = None  # Lazy-loaded if needed
            for cid in client_ids:
                persisted = self._state_store.get_client_volume(cid) if restore_enabled else None
                if persisted is not None:
                    updates[cid] = persisted
                elif restore_enabled:
                    if local_volume is None:
                        volume_state = await self._camilladsp_service.get_volume()
                        local_volume = volume_state.get("main", DEFAULT_VOLUME_DB) if volume_state else DEFAULT_VOLUME_DB
                    updates[cid] = local_volume
                else:
                    updates[cid] = startup_volume

            self.logger.info(f"Pushing {'persisted' if restore_enabled else f'startup ({startup_volume:.1f}dB)'} volumes to {len(updates)} clients")

        if not updates:
            return True

        # Apply volumes and update state store
        results = await self._equalizer_controller.apply_volumes_parallel(updates)
        succeeded = [h for h, ok in results.items() if ok]
        failures = [h for h, ok in results.items() if not ok]

        for hostname, volume in updates.items():
            if results.get(hostname, False):
                await self._state_store.set_client_volume(hostname, volume)

        if succeeded:
            self.logger.info(f"PUSH_VOLUME: Succeeded for {len(succeeded)} clients: {succeeded}")
        if failures:
            self.logger.warning(f"PUSH_VOLUME: FAILED for {len(failures)} clients: {failures} — these clients may be desynchronized")

        # Apply persisted mute states
        for cid in client_ids:
            if self._state_store.has_client(cid):
                try:
                    await self._equalizer_controller.set_equalizer_mute(cid, self._state_store.get_client_mute(cid))
                except Exception as e:
                    self.logger.warning(f"PUSH_VOLUME: Failed to apply mute to {cid}: {e}")

        await self._broadcast_volume_state(show_bar=False)
        return len(failures) == 0

    @handle_errors(default=None)
    async def update_client_volume_db(self, client_id: str, volume_db: float, broadcast: bool = True) -> None:
        """Update client volume in dB (called from API routes)."""
        await self._state_store.set_client_volume(client_id, volume_db)
        await self._equalizer_controller.set_equalizer_volume(client_id, volume_db)
        if broadcast and self._is_multiroom_enabled():
            await self._broadcast_volume_state(show_bar=False)

    @handle_errors(default=None)
    async def set_client_mute(self, client_id: str, mute: bool, broadcast: bool = True) -> None:
        """Set mute state for a client."""
        await self._state_store.set_client_mute(client_id, mute)
        await self._equalizer_controller.set_equalizer_mute(client_id, mute)
        if broadcast:
            await self._broadcast_volume_state(show_bar=False)

    # ============================================================================
    # ATOMIC ZONE OPERATIONS
    # ============================================================================

    async def apply_zone_volume_delta(self, zone_id: str, delta_db: float) -> float:
        """Apply volume delta to entire zone atomically. Returns new zone average in dB."""
        # Phase A: compute updates under lock (no hardware I/O)
        try:
            async with asyncio.timeout(2.0):
                async with self._volume_lock:
                    self._state_store.clear_zone_targets()
                    updates = await self._state_store.apply_zone_delta(zone_id, delta_db)
        except asyncio.TimeoutError:
            self.logger.warning("Timeout waiting for volume lock (>2s) for zone delta")
            return self._state_store.compute_zone_average(zone_id)

        if not updates:
            self.logger.warning(f"No clients to update in zone {zone_id}")
            return self._state_store.compute_zone_average(zone_id)

        # Phase B: hardware fan-out outside lock
        self.logger.info(f"Applying zone delta: {zone_id} {delta_db:+.1f}dB -> {len(updates)} clients")
        results = await self._equalizer_controller.apply_volumes_parallel(updates)

        successful = {h: v for h, v in updates.items() if results.get(h, False)}
        await self._state_store.apply_zone_updates(successful)

        failures = [h for h, ok in results.items() if not ok]
        if failures:
            self.logger.warning(f"Failed to update clients: {failures}")

        # FR11 + broadcast
        local_mac_id = self._state_store.local_mac_id
        local_volume = updates.get(local_mac_id) if local_mac_id else None
        local_volume = local_volume or self._state_store.local_volume_db
        await self._update_startup_volume_if_needed(local_volume)
        await self._broadcast_volume_state(show_bar=False)

        new_avg = self._state_store.compute_zone_average(zone_id)
        self.logger.info(f"Zone {zone_id} updated: {new_avg:.1f}dB ({len(successful)}/{len(updates)} success)")
        return new_avg

    # ============================================================================
    # SERVICE INITIALIZATION
    # ============================================================================

    async def initialize(self) -> bool:
        """
        Initialize volume service.

        Sets ALSA to 100% passthrough and initializes CamillaDSP volume.
        """
        try:
            await self._load_volume_config()

            # Read volume control flag from hardware (DAC mode detection)
            if self._hardware_service:
                self._volume_control = self._hardware_service.get_volume_control()
            self._state_store.set_volume_control(self._volume_control)
            if not self._volume_control:
                self.logger.info("DAC mode: volume managed by external amplifier")

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
            self.logger.error(f"Volume service initialization failed: {e}")
            self._availability_ready.set()
            return False

    async def set_local_volume_control(self, enabled: bool) -> None:
        """Update local device's volume_control at runtime (persists + broadcasts)."""
        if self._hardware_service:
            await self._hardware_service.set_volume_control(enabled)
        self._volume_control = enabled
        self._state_store.set_volume_control(enabled)
        # Apply volume change to CamillaDSP immediately
        if self._camilladsp_service:
            if not enabled:
                # DAC mode: pin CamillaDSP at 0dB (external amp manages volume)
                await self._camilladsp_service.set_volume(0.0)
                await self._camilladsp_service.set_mute(False)
                self.logger.info("DAC mode: CamillaDSP pinned at 0 dB")
            else:
                # Restore managed volume from state
                await self.reapply_current_volume()
        # Sync to registry so zone all_external_volume and WS events stay accurate
        if self._client_registry and self._state_store.local_mac_id:
            await self._client_registry.update_client(
                self._state_store.local_mac_id, volume_control=enabled
            )
        self.logger.info(f"Local volume_control set to {enabled}")
        await self._broadcast_volume_state(show_bar=False)

    @handle_errors(default=None)
    async def reapply_current_volume(self) -> None:
        """Re-apply current volume and mute state to CamillaDSP (after reconnection)."""
        if not self._camilladsp_service:
            return
        if not self._volume_control:
            await self._camilladsp_service.set_volume(0.0)
            await self._camilladsp_service.set_mute(False)
            self.logger.info("DAC mode: re-pinned CamillaDSP at 0 dB after reconnect")
            return
        volume_db = self._state_store.local_volume_db
        local_mac_id = self._state_store.local_mac_id
        local_mute = self._state_store.get_client_mute(local_mac_id) if local_mac_id else False
        await self._camilladsp_service.set_volume(volume_db)
        await self._camilladsp_service.set_mute(local_mute)
        self.logger.info(f"Re-applied volume after CamillaDSP reconnect: {volume_db:.1f}dB, mute={local_mute}")

    async def _apply_startup_volume(self) -> None:
        """
        Apply startup volume and mute state to CamillaDSP (FR12).

        Volume source is determined by restore_last_volume setting:
        - True: Use persisted volume from last_volume.json
        - False: Use startup_volume_db from settings.json

        Note: At startup, registry may not have the local client yet, so we use
        _local_volume_db and direct CamillaDSP service calls.
        """
        # Wait for CamillaDSP connection
        if self._camilladsp_service:
            if not await self._camilladsp_service.wait_for_connection(timeout=10.0):
                self.logger.warning("FR12: CamillaDSP not connected after 10s, startup volume not applied")
                return

        # DAC mode: pin CamillaDSP at 0 dB (external amp manages volume)
        if not self._volume_control:
            if self._camilladsp_service:
                await self._camilladsp_service.set_volume(0.0)
                await self._camilladsp_service.set_mute(False)
            self.logger.info("DAC mode: CamillaDSP pinned at 0 dB")
            return

        # startup_volume_db is the single source of truth:
        # - restore_last_volume=true: auto-updated by FR11 to track current volume
        # - restore_last_volume=false: user-configured fixed value
        target_volume = self._volume_config.startup_volume_db
        self.logger.info(f"FR12: Applying startup_volume_db: {target_volume:.1f} dB")

        # Get persisted mute state from local client (False if no client registered yet)
        local_mac_id = self._state_store.local_mac_id
        local_mute = self._state_store.get_client_mute(local_mac_id) if local_mac_id else False

        # Apply directly to local CamillaDSP (at startup, registry not yet populated)
        if target_volume is not None and self._camilladsp_service:
            await self._camilladsp_service.set_volume(target_volume)
            await self._camilladsp_service.set_mute(local_mute)
            self.logger.info(f"FR12: Startup state applied - volume={target_volume:.1f}dB, mute={local_mute}")
        elif self._camilladsp_service:
            await self._camilladsp_service.set_mute(False)
            self.logger.warning("FR12: No target volume, only unmuted CamillaDSP")

    @handle_errors(default=None)
    async def _startup_broadcast_after_websocket_ready(self):
        """Wait for Snapcast WebSocket and broadcast initial volume state.

        Availability is signaled immediately so frontend WebSocket connections
        receive local volume state without waiting for Snapcast sync.
        Multiroom client data is broadcast when Snapcast becomes ready.
        """
        # Signal availability immediately — local volume state is ready
        self._availability_ready.set()

        multiroom_enabled = await self.settings_service.get_setting("routing.multiroom_enabled") or False

        if multiroom_enabled and self._snapcast_websocket_service:
            ws_ready = await self._snapcast_websocket_service.wait_for_ready(timeout=30.0)
            if ws_ready:
                self.logger.info("Snapcast WebSocket ready, syncing clients")
                await self.initialize_client_availability()
                await self.push_volume_to_all_clients()
            else:
                self.logger.warning("Snapcast WebSocket not ready after timeout")
        else:
            await asyncio.sleep(0.5)

        await self._broadcast_volume_state(show_bar=False)

    # ============================================================================
    # PUBLIC API (all in dB)
    # ============================================================================

    async def get_volume_db(self) -> float:
        """Get current volume in dB (average of non-muted clients in multiroom mode)."""
        volume_state = await self._state_store.get_complete_state()
        return volume_state.global_volume_db

    async def set_volume_db(self, volume_db: float, show_bar: bool = True) -> bool:
        """Set volume to specific level in dB (-80 to 0)."""
        if not self._volume_control and not self._is_multiroom_enabled():
            return True  # Direct + DAC: no clients to control
        if not await self._check_equalizer_or_error():
            return False
        target_db = self._volume_config.clamp(volume_db)
        client_ids = await self._get_controllable_client_ids()
        try:
            async with asyncio.timeout(2.0):
                async with self._volume_lock:
                    updates = await self._compute_multiroom_updates(target_db, client_ids)
        except asyncio.TimeoutError:
            self.logger.warning("Timeout waiting for volume lock (>2s)")
            return False

        success = await self._apply_volume_to_hardware(target_db, updates)
        if success:
            await self._update_startup_volume_if_needed(target_db)
            await self._broadcast_volume_state(show_bar)
        return success

    async def adjust_volume_db(self, delta_db: float, show_bar: bool = True) -> bool:
        """Adjust volume by delta in dB (positive = louder, negative = quieter)."""
        if not self._volume_control and not self._is_multiroom_enabled():
            return True  # Direct + DAC: no clients to control
        if not await self._check_equalizer_or_error():
            return False
        client_ids = await self._get_controllable_client_ids()
        try:
            async with asyncio.timeout(2.0):
                async with self._volume_lock:
                    volume_state = await self._state_store.get_complete_state()
                    target_db = self._volume_config.clamp(volume_state.global_volume_db + delta_db)
                    updates = await self._compute_multiroom_updates(target_db, client_ids)
        except asyncio.TimeoutError:
            self.logger.warning("Timeout waiting for volume lock (>2s)")
            return False

        success = await self._apply_volume_to_hardware(target_db, updates)
        if success:
            self._schedule_post_volume_tasks(target_db, show_bar)
        return success

    def _schedule_post_volume_tasks(self, target_db: float, show_bar: bool) -> None:
        """Schedule FR11 check and WebSocket broadcast as background tasks."""
        async def _post_update():
            await self._update_startup_volume_if_needed(target_db)
            await self._broadcast_volume_state(show_bar)
        task = asyncio.create_task(_post_update())
        task.add_done_callback(self._handle_broadcast_task_error)

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

    @handle_errors(default=None, level='warning')
    async def initialize_client_availability(self) -> None:
        """Initialize client availability from Snapcast on startup."""
        clients = await self.snapcast_service.get_clients()
        for client in clients:
            mac_id = client.get("mac_id", "")
            available = client.get("available", True)
            if mac_id:
                await self._state_store.set_client_availability(mac_id, available)
                self.logger.debug(f"Initialized availability: {mac_id} -> {available}")
        self.logger.info(f"Initialized availability for {len(clients)} clients")

    async def _broadcast_volume_state(self, show_bar: bool = True) -> None:
        """Broadcast volume state immediately to WebSocket clients."""
        try:
            volume_state = await self.get_volume_state()

            event_data = {
                "show_bar": show_bar,
                "step_mobile_db": self._volume_config.step_mobile_db,
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

    async def get_status(self) -> dict:
        """Get complete volume service status."""
        try:
            vs = await self._state_store.get_complete_state()
            return {
                "volume_db": vs.global_volume_db,
                "multiroom_enabled": self._is_multiroom_enabled(),
                "equalizer_available": self._is_equalizer_available(),
                "config": self._volume_config.to_dict(),
                "clients": {h: {"volume_db": c.volume_db, "offset_db": c.offset_db, "mute": c.mute, "available": c.available}
                           for h, c in vs.clients.items()},
                "zones": {zid: {"name": z.name, "average_volume_db": z.average_volume_db, "client_ids": z.client_ids, "all_muted": z.all_muted}
                          for zid, z in vs.zones.items()}
            }
        except Exception as e:
            self.logger.error(f"Error getting status: {e}")
            return {"volume_db": DEFAULT_VOLUME_DB, "error": str(e)}

    async def get_volume_state(self) -> VolumeState:
        """
        Get unified volume state (single source of truth).

        Returns a VolumeState with all volume data for both direct and multiroom modes.
        """
        return await self._state_store.get_complete_state()

    @handle_errors(default={"main": DEFAULT_VOLUME_DB, "mute": False})
    async def get_client_volume(self, hostname: str) -> dict:
        """
        Get volume for a specific client (works in both modes).

        Returns: {"main": volume_db, "mute": bool}
        """
        volume_state = await self._state_store.get_complete_state()
        client = volume_state.clients.get(hostname)
        if client:
            return {"main": client.volume_db, "mute": client.mute}
        return {"main": DEFAULT_VOLUME_DB, "mute": False}

    async def cleanup(self) -> None:
        """Clean up resources. Flushes pending volume state to disk."""
        await self._state_store.cleanup()
        self.logger.info("VolumeService cleanup completed")
