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

from backend.shared.background import BackgroundTaskSet
from backend.shared.decorators import handle_errors
from backend.core.volume.state import VolumeStateStore
from backend.core.volume.equalizer_controller import EqualizerController
from backend.core.multiroom.snapcast import get_online_client_ids
from backend.core.multiroom.identity import get_local_mac
from backend.core.models.volume import VolumeConfig
from backend.core.models.volume_state import VolumeState
from backend.core.models.ws_events import (
    VolumeChanged,
    VolumeStartupChanged,
    VolumeStartupConfig,
)
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
        self._equalizer_router = equalizer_router
        self._hardware_service = hardware_service
        self.logger = logging.getLogger(__name__)
        self._bg = BackgroundTaskSet(self.logger, "volume")
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

    def attach_registry(self, registry):
        """Attach the ClientRegistryService: subscribe the volume state store to
        its availability events and wire registry-dependent helpers (IP lookup
        for EqualizerController).

        Ordering matters — initialize_services calls this BEFORE the snapcast
        WebSocket subscribes, so volume state is current by the time a registry
        event triggers a multiroom broadcast.
        """
        self._client_registry = registry
        self._equalizer_controller.set_registry(registry)
        self._state_store.set_registry(registry)

    def set_routing_service(self, routing_service) -> None:
        """Set routing service reference (circular dependency resolution)."""
        self._routing_service = routing_service

    @property
    def volume_control(self) -> bool:
        """Whether the local device handles volume (False = external DAC/amp)."""
        return self._volume_control

    @property
    def state_store(self) -> VolumeStateStore:
        """Volume state store (single source of truth for volume state)."""
        return self._state_store

    @property
    def equalizer_controller(self) -> EqualizerController:
        """Hardware abstraction used to apply volume/mute to clients."""
        return self._equalizer_controller

    # ============================================================================
    # HELPERS
    # ============================================================================

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
            # Direct mode: record-intent + reconcile. The state store already holds
            # the target (set in _compute_multiroom_updates). If CamillaDSP is not
            # connected yet (cold boot / reconnect window — e.g. the wizard reboot
            # just applied the DAC overlay), the apply is *deferred*, not failed:
            # the reconnect callback (reapply_current_volume) pushes the stored
            # volume once the daemon is back. Fail open instead of returning 500.
            if not self._is_equalizer_available():  # also covers _camilladsp_service is None
                # Only report success if the intent was durably recorded — i.e. the
                # local client is known, so the reconnect restore has a target to
                # apply. Otherwise (local MAC unresolved — e.g. no eth0/wlan0)
                # surface a failure rather than a false success.
                if self._state_store.local_mac_id is None:
                    self.logger.warning(
                        f"Direct mode: CamillaDSP not ready and local client unknown — "
                        f"volume {target_db:.1f}dB not recorded"
                    )
                    return False
                self.logger.info(
                    f"Direct mode: CamillaDSP not ready — volume {target_db:.1f}dB recorded, "
                    "will apply on reconnect"
                )
                return True
            # Connected: a False here is a genuine command failure, surface it.
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
            await self.broadcast_volume_state(show_bar=False)
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

            await self.broadcast_volume_state(show_bar=False)
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
                step_ir_remote_db=volume_settings.get("step_ir_remote_db", 2.0),
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

        if old_min == new_min and old_max == new_max:
            return True

        # Check if current volume is outside new limits
        if current_db < new_min or current_db > new_max:
            # Move to center of new range
            center_db = (new_min + new_max) / 2.0
            await self.set_volume_db(center_db, show_bar=False)
        else:
            await self.broadcast_volume_state(show_bar=False)

        return True

    # ============================================================================
    # STARTUP VOLUME AUTO-UPDATE
    # ============================================================================

    @handle_errors(default=None)
    async def _update_startup_volume_if_needed(self, volume_db: float) -> None:
        """
        Auto-update startup_volume_db to track current volume.

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

        await self._load_volume_config()

        # Broadcast the actual persisted value from config (ensures consistency)
        persisted_value = self._volume_config.startup_volume_db
        await self._broadcast_startup_volume_changed(persisted_value)

        self.logger.debug(f"Auto-updated startup_volume_db to {persisted_value:.1f} dB")

    @handle_errors(default=None)
    async def _broadcast_startup_volume_changed(self, volume_db: float) -> None:
        """
        Broadcast startup volume change via WebSocket.

        Args:
            volume_db: The new startup volume in dB
        """
        await self.state_machine.broadcast(VolumeStartupChanged(
            config=VolumeStartupConfig(
                startup_volume_db=volume_db,
                restore_last_volume=self._volume_config.restore_last_volume
            )
        ))

    @handle_errors(default=False)
    async def _reload_config(self, broadcast: bool = False) -> bool:
        """Helper: reload config with optional broadcast."""
        await self._load_volume_config()
        if broadcast:
            await self.broadcast_volume_state(show_bar=False)
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
            # Read equalizer volume via the router, which owns local/remote
            # dispatch (local CamillaDSP vs satellite proxy) — VolumeService no
            # longer reaches a satellite directly.
            client_info = registry.get_client(cid) if registry else None
            if not (client_info and client_info.ip):
                self.logger.warning(f"Cannot sync client {cid}: no IP address in registry")
                continue
            if cid == self._state_store.local_mac_id:
                # SSOT: the local volume lives in the state store (last_volume.json).
                # Never reconstruct it from the live CamillaDSP — that inverts the
                # data flow and races the boot restore.
                volume = self._state_store.get_client_volume(cid)
                if volume is None:
                    volume = self._volume_config.startup_volume_db
            else:
                # Remote: read the satellite's own value via the proxy, but if it is
                # unreachable/not ready (boot race), keep the last persisted value
                # (SSOT) rather than clobbering it with the -45 dB default — the later
                # push restores that value to the satellite.
                vol_data = await self._equalizer_router.get_volume(cid)
                volume = vol_data.get("main") if vol_data else None
                if volume is None:
                    volume = self._state_store.get_client_volume(cid)
                    if volume is None:
                        volume = DEFAULT_VOLUME_DB
            await self._state_store.register_client(cid, volume_db=volume, available=client.get("available", True))

        self.logger.info(f"Synced {len(clients)} clients from equalizer")
        await self.broadcast_volume_state(show_bar=False)
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
            # Benign boot-ordering case: the snapserver WS is ready but the local
            # snapclient has not registered yet. Push is a no-op (returns True) and
            # the CLIENT_CONNECT handler + delayed sync apply volumes once it joins.
            self.logger.info("PUSH_VOLUME: No online clients yet — nothing to push (will sync on client connect)")
            return True
        self.logger.info(f"PUSH_VOLUME: Found {len(client_ids)} online clients: {client_ids}")

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

        await self.broadcast_volume_state(show_bar=False)
        return len(failures) == 0

    @handle_errors(default=None)
    async def update_client_volume_db(self, client_id: str, volume_db: float, broadcast: bool = True) -> None:
        """Update client volume in dB (called from API routes)."""
        await self._state_store.set_client_volume(client_id, volume_db)
        await self._equalizer_controller.set_equalizer_volume(client_id, volume_db)
        if broadcast and self._is_multiroom_enabled():
            await self.broadcast_volume_state(show_bar=False)

    @handle_errors(default=None)
    async def set_client_mute(self, client_id: str, mute: bool, broadcast: bool = True) -> None:
        """Set mute state for a client."""
        await self._state_store.set_client_mute(client_id, mute)
        await self._equalizer_controller.set_equalizer_mute(client_id, mute)
        if broadcast:
            await self.broadcast_volume_state(show_bar=False)

    # ============================================================================
    # ATOMIC ZONE OPERATIONS
    # ============================================================================

    async def apply_zone_volume_delta(self, zone_id: str, delta_db: float) -> float:
        """Apply volume delta to entire zone atomically. Returns new zone average in dB."""
        # Phase A: compute updates under lock (no hardware I/O)
        try:
            async with asyncio.timeout(2.0):
                async with self._volume_lock:
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

        # Startup-volume tracking + broadcast
        local_mac_id = self._state_store.local_mac_id
        local_volume = updates.get(local_mac_id) if local_mac_id else None
        local_volume = local_volume or self._state_store.local_volume_db
        await self._update_startup_volume_if_needed(local_volume)
        await self.broadcast_volume_state(show_bar=False)

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

            # Seed the local client on a fresh direct-mode boot (no Snapcast, no
            # persisted state) so volume tracking works before multiroom is ever
            # enabled. No-op once the mac is resolved via Snapcast or persistence.
            self._seed_local_client_if_needed()

            # Apply persisted volume to CamillaDSP (safe startup at -50dB, then restore)
            await self._apply_startup_volume()

            # Set ALSA to 100% passthrough - permanent (volume is via CamillaDSP)
            await self._set_alsa_passthrough()
            self.logger.info("ALSA set to 100% passthrough mode")

            # Start initial broadcast task (waits for Snapcast WebSocket in multiroom mode)
            self._bg.spawn(self._startup_broadcast_after_websocket_ready(), label="startup_broadcast")
            return True
        except Exception as e:
            self.logger.error(f"Volume service initialization failed: {e}")
            self._availability_ready.set()
            return False

    def _seed_local_client_if_needed(self) -> None:
        """Resolve and seed the local client identity when not yet known.

        On a truly-fresh direct-mode boot the local mac is set via neither Snapcast
        nor persisted state, so the state store can't track local volume. The system
        MAC (eth0→wlan0) equals the snapclient --hostID, so seeding it stays
        consistent if multiroom is later enabled. No-op once the mac is resolved.
        """
        if self._state_store.local_mac_id is not None:
            return
        local_mac = get_local_mac()
        if not local_mac:
            self.logger.warning("Could not resolve local MAC — direct-mode volume tracking degraded until Snapcast registers it")
            return
        self._state_store.ensure_local_client(local_mac, self._volume_config.startup_volume_db)

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
        await self.broadcast_volume_state(show_bar=False)

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
        local_mac_id = self._state_store.local_mac_id
        if local_mac_id is None or not self._state_store.has_client(local_mac_id):
            # Boot race: CamillaDSP connected before the state store was restored.
            # Don't clobber it with DEFAULT_VOLUME_DB — the startup path
            # (_apply_startup_volume / push_volume_to_all_clients) applies the
            # correct local value once the store is ready.
            self.logger.debug("reapply skipped: local client not yet known")
            return
        volume_db = self._state_store.local_volume_db
        local_mute = self._state_store.get_client_mute(local_mac_id)
        await self._camilladsp_service.set_volume(volume_db)
        await self._camilladsp_service.set_mute(local_mute)
        self.logger.info(f"Re-applied volume after CamillaDSP reconnect: {volume_db:.1f}dB, mute={local_mute}")

    async def _apply_startup_volume(self) -> None:
        """
        Apply startup volume and mute state to CamillaDSP.

        Volume source is determined by restore_last_volume setting:
        - True: the local client's OWN persisted per-client volume (state store,
          restored from last_volume.json before this runs). In multiroom
          startup_volume_db tracks the GLOBAL AVERAGE, which is wrong for the
          local client; in direct mode the two are equal anyway.
        - False: the user-configured fixed startup_volume_db.

        SSOT: the state store is the single source of truth for the local volume;
        we apply store -> CamillaDSP here and never read CamillaDSP back into it.
        """
        # Wait for CamillaDSP connection
        if self._camilladsp_service:
            if not await self._camilladsp_service.wait_for_connection(timeout=10.0):
                self.logger.warning("CamillaDSP not connected after 10s, startup volume not applied")
                return

        # DAC mode: pin CamillaDSP at 0 dB (external amp manages volume)
        if not self._volume_control:
            if self._camilladsp_service:
                await self._camilladsp_service.set_volume(0.0)
                await self._camilladsp_service.set_mute(False)
            self.logger.info("DAC mode: CamillaDSP pinned at 0 dB")
            return

        local_mac_id = self._state_store.local_mac_id

        # In restore mode, the local client's own persisted volume is authoritative
        # (in multiroom startup_volume_db tracks the global AVERAGE — wrong for the
        # local client). Before the local client is resolved (fresh boot), fall back
        # to the configured startup volume rather than the -45 dB hard default.
        # In fixed mode, the user-configured value applies to all clients.
        if (self._volume_config.restore_last_volume
                and local_mac_id is not None
                and self._state_store.has_client(local_mac_id)):
            target_volume = self._state_store.get_client_volume(local_mac_id)
        else:
            target_volume = self._volume_config.startup_volume_db
        self.logger.info(f"Applying startup volume: {target_volume:.1f} dB")

        # Get persisted mute state from local client (False if no client registered yet)
        local_mute = self._state_store.get_client_mute(local_mac_id) if local_mac_id else False

        # Apply directly to local CamillaDSP (at startup, registry not yet populated)
        if target_volume is not None and self._camilladsp_service:
            await self._camilladsp_service.set_volume(target_volume)
            await self._camilladsp_service.set_mute(local_mute)
            self.logger.info(f"Startup state applied - volume={target_volume:.1f}dB, mute={local_mute}")
        elif self._camilladsp_service:
            await self._camilladsp_service.set_mute(False)
            self.logger.warning("No target volume, only unmuted CamillaDSP")

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

        await self.broadcast_volume_state(show_bar=False)

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
            await self.broadcast_volume_state(show_bar)
        return success

    async def adjust_volume_db(self, delta_db: float, show_bar: bool = True) -> bool:
        """Adjust volume by delta in dB (positive = louder, negative = quieter)."""
        if not self._volume_control and not self._is_multiroom_enabled():
            return True  # Direct + DAC: no clients to control
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
        """Schedule the startup-volume tracking check and the broadcast in the background."""
        async def _post_update():
            await self._update_startup_volume_if_needed(target_db)
            await self.broadcast_volume_state(show_bar)
        self._bg.spawn(_post_update(), label="post_volume_update")

    # ============================================================================
    # WEBSOCKET BROADCASTING
    # ============================================================================

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

    async def broadcast_volume_state(self, show_bar: bool = True) -> None:
        """Broadcast volume state immediately to WebSocket clients."""
        try:
            volume_state = await self.get_volume_state()

            await self.state_machine.broadcast(VolumeChanged(
                show_bar=show_bar,
                step_mobile_db=self._volume_config.step_mobile_db,
                multiroom_enabled=volume_state.mode == "multiroom",
                state=volume_state.to_dict()
            ))

            self.logger.debug(f"Volume broadcast completed: {len(volume_state.clients)} clients, {len(volume_state.zones)} zones")
        except Exception as e:
            self.logger.error(f"Error broadcasting volume state: {e}", exc_info=True)
            raise  # Re-raise so task error callback can handle it

    # ============================================================================
    # UTILITY METHODS
    # ============================================================================

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
        await self._bg.cancel_all()
        await self._state_store.cleanup()
        self.logger.info("VolumeService cleanup completed")
