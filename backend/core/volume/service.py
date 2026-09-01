# backend/core/volume/service.py
"""
Volume management service - CamillaDSP always active.

All volume values are in decibels (-80 to 0 dB).
Volume control is entirely via CamillaDSP — the card's own mixer is pinned at
unity outside the backend (see /usr/local/bin/milo-alsa-passthrough).

Architecture:
- VolumeStateStore: Single source of truth for all volume state
- EqualizerController: Hardware abstraction for parallel volume updates
- VolumeService: Orchestration layer only
"""
import asyncio
import contextlib
import logging
from typing import Optional, Dict, Tuple

from backend.shared.background import BackgroundTaskSet
from backend.shared.decorators import handle_errors
from backend.core.volume.state import VolumeStateStore
from backend.core.volume.equalizer_controller import EqualizerController
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

    Architecture:
        VolumeStateStore: Single source of truth (state + zones + clients)
        EqualizerController: Hardware abstraction (local + remote equalizer updates)
        VolumeService: Orchestration (API -> State -> Hardware)
    """

    # Debounce for the startup-volume tracking write. Measured on this
    # appliance: a 3 s rotary turn drives ~105 volume steps, each of which used
    # to rewrite the whole of settings.json (8.6 KB) and fsync it — 104 writes,
    # 1.72 MB of block traffic on the SD card and 9.3 % of one core, against
    # 0.53 % at rest. Nothing coalesced, because every step wrote immediately.
    # Same value as VolumeStateStore's own debounce, which was already
    # collapsing last_volume.json to a single write over that identical burst.
    STARTUP_VOLUME_DEBOUNCE_S = 2.0

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

        # Debounced persistence of volume.startup_volume_db (see the constant above).
        self._startup_volume_pending: Optional[float] = None
        self._startup_persist_task: Optional[asyncio.Task] = None

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

    def _online_client_ids(self) -> list:
        """Online client IDs, read from the registry.

        The registry is the single authority for "is this client reachable":
        it is what EqualizerRouter short-circuits on, so asking snapserver here
        instead produced a list the router then refused to act on — the volume
        was committed to the store for a client the command never reached.
        """
        if not self._client_registry:
            return []
        return self._client_registry.get_online_client_ids()

    def _get_controllable_client_ids(self) -> list:
        """Online client IDs that have volume control (excludes DAC clients)."""
        client_ids = self._online_client_ids() if self._is_multiroom_enabled() else []
        return [cid for cid in client_ids if self._state_store.has_volume_control(cid)]

    @staticmethod
    def _split_on_verdict(updates: Dict[str, float], reachable: Dict[str, float],
                          results: Dict[str, bool]) -> Tuple[Dict[str, float], list]:
        """Split a fan-out's updates into what the store keeps and what it drops.

        One rule, shared by the zone delta and the global one: a client the
        fan-out reached is written only if it accepted the level, while a client
        it never reached has no verdict to wait for and is written
        unconditionally — offline is a skip, not a failure, the same rule as
        _refused. That unconditional write is the whole mechanism by which a
        relative adjustment reaches a client that was absent when it was made.

        Returns:
            (to_commit, refused) — refused holds only reachable clients that
            answered False.
        """
        to_commit, refused = {}, []
        for mac_id, volume_db in updates.items():
            if mac_id not in reachable or results.get(mac_id, False):
                to_commit[mac_id] = volume_db
            else:
                refused.append(mac_id)
        return to_commit, refused

    async def _compute_multiroom_updates(self, target_db: float,
                                         client_ids: list) -> Optional[Dict[str, float]]:
        """Compute per-client volume updates for multiroom mode.

        Must be called with _volume_lock held. The two modes commit at
        different moments, on purpose:

        - multiroom: nothing is written here. Each client's volume is committed
          in _apply_volume_to_hardware, per client, only once its hardware call
          succeeded (set_client_volume).
        - direct: the local target is written here, *before* the CamillaDSP
          call. It is the record-intent half of the record-intent + reconcile
          pattern _apply_volume_to_hardware documents — a volume set while the
          daemon is disconnected must survive to be replayed by the reconnect
          callback.

        The shift is computed for every known client with volume control, not
        only the reachable ones: the operation is relative, so an absent client
        keeps its place in the room by having its stored level moved too.
        _apply_volume_to_hardware still fans out to the online ones alone.

        A client held at a limit absorbs less than the full shift, so the
        resulting average lands slightly off the target. That is accepted and
        not redistributed — correcting it would move speakers nobody touched.

        Args:
            target_db: Target global volume in dB.
            client_ids: Online client IDs (fetched before lock acquisition).
                Empty means nothing is reachable, and the global average the
                shift is measured against would then be a fabricated default —
                so nothing is computed at all.

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
        return {
            cid: self._volume_config.clamp(client.volume_db + delta)
            for cid, client in volume_state.clients.items()
            if self._state_store.has_volume_control(cid)
        }

    async def _apply_volume_to_hardware(self, target_db: float, updates: Optional[Dict[str, float]],
                                        online_ids: list) -> bool:
        """Apply volume to hardware outside the lock.

        Args:
            target_db: Target volume in dB (used for direct mode CamillaDSP call).
            updates: Per-client updates from _compute_multiroom_updates, or None for direct mode.
            online_ids: The clients the fan-out may reach. An update for a client
                absent from it is stored without any hardware call.
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
        # Multiroom: fan out to the reachable clients, commit state on the rule
        online = set(online_ids)
        reachable = {cid: volume for cid, volume in updates.items() if cid in online}
        results = await self._equalizer_controller.apply_volumes_parallel(reachable)
        committed, failed = self._split_on_verdict(updates, reachable, results)
        for hostname, volume in committed.items():
            await self._state_store.set_client_volume(hostname, volume)
        if failed:
            self.logger.warning(f"Multiroom volume update failed for {len(failed)}/{len(reachable)} clients: {failed}")
        # Local client failure is critical — server audio may be silent
        local_mac = self._state_store.local_mac_id
        local_failed = local_mac is not None and local_mac in failed
        if local_failed:
            self.logger.error("LOCAL server volume update failed — server audio may be silent")
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
    # MODE DETECTION
    # ============================================================================

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

    async def update_volume_mode(self, multiroom_enabled: bool) -> None:
        """Switch the volume mode. No client's level moves, so nothing is pushed.

        A mode switch is not an adjustment — nobody asked for a new level. Each
        satellite re-applies its own on admission when snapclient joins, and the
        local client keeps the one it was left at. That last part is why this
        returns nothing: the direct-mode volume *is*
        `_clients[local_mac_id].volume_db`, so deriving it from the satellites'
        average here overwrote the operator's own level with a number nobody
        chose — local -70 with two satellites at -30 came back to direct at -50,
        a +20 dB step on the only speaker still playing.

        The unmute on the way to direct is the one exception, and it is
        load-bearing: direct mode plays on the local speaker alone, so a local
        client muted during multiroom would return to silence with nothing on
        screen to explain it.
        """
        await self._state_store.set_mode("multiroom" if multiroom_enabled else "direct")

        # DAC mode makes no CamillaDSP call at all here: reapply_current_volume
        # pins it at 0 dB and unmuted, and the external amp owns the rest.
        if self._volume_control and not multiroom_enabled:
            try:
                await self._camilladsp_service.set_mute(False)
                self.logger.info("Switched to direct: CamillaDSP unmuted, no level changed")
            except Exception as e:
                self.logger.warning(f"Failed to unmute CamillaDSP: {e}")

        await self.broadcast_volume_state(show_bar=False)

    # ============================================================================
    # CONFIGURATION LOADING
    # ============================================================================

    async def _load_volume_config(self) -> None:
        """Load volume configuration from settings.

        Every key is read directly, with no fallback operand: `_validate_and_merge`
        guarantees the whole `volume` section, so a missing key is a broken
        settings.json and must be logged, not papered over. The operands that used
        to sit here were a third declaration of these defaults and had drifted from
        `SettingsService.defaults` — `limit_max_db` −21 against −20,
        `step_mobile_db` 3 against 2, and `restore_last_volume` False against True,
        so a degraded read silently stopped restoring the volume at startup.
        """
        self._drop_pending_startup_volume()
        try:
            self.settings_service.invalidate_cache()
            volume_settings = await self.settings_service.get_setting('volume')

            self._volume_config = VolumeConfig(
                limit_min_db=volume_settings["limit_min_db"],
                limit_max_db=volume_settings["limit_max_db"],
                step_mobile_db=volume_settings["step_mobile_db"],
                step_rotary_db=volume_settings["step_rotary_db"],
                step_bt_remote_db=volume_settings["step_bt_remote_db"],
                step_ir_remote_db=volume_settings["step_ir_remote_db"],
                startup_volume_db=volume_settings["startup_volume_db"],
                restore_last_volume=volume_settings["restore_last_volume"]
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

        # In memory now, on disk in STARTUP_VOLUME_DEBOUNCE_S. The in-memory
        # value is what the next step compares against and what every reader
        # (initialize, the reconnection sync, GET /volume/startup) uses, so the
        # appliance behaves as if the write had already landed — only the SD
        # card sees one write per turn instead of one per step.
        self._volume_config.startup_volume_db = volume_db
        self._startup_volume_pending = volume_db
        self._schedule_startup_volume_persist()

        await self._broadcast_startup_volume_changed(volume_db)

        self.logger.debug(f"Auto-updated startup_volume_db to {volume_db:.1f} dB")

    def _schedule_startup_volume_persist(self) -> None:
        """Schedule the debounced write of the pending startup volume."""
        if self._startup_persist_task and not self._startup_persist_task.done():
            self._startup_persist_task.cancel()

        async def _debounced():
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.sleep(self.STARTUP_VOLUME_DEBOUNCE_S)
                await self._persist_startup_volume()

        self._startup_persist_task = self._bg.spawn(
            _debounced(), label="persist_startup_volume"
        )

    @handle_errors(default=None, level='error')
    async def _persist_startup_volume(self) -> None:
        """Write the pending startup volume, if any. Idempotent."""
        volume_db = self._startup_volume_pending
        if volume_db is None or not self.settings_service:
            return
        self._startup_volume_pending = None
        await self.settings_service.set_setting('volume.startup_volume_db', volume_db)

    async def _flush_startup_volume(self) -> None:
        """Cancel the debounce and write the pending value now (shutdown path)."""
        if self._startup_persist_task and not self._startup_persist_task.done():
            self._startup_persist_task.cancel()
        await self._persist_startup_volume()

    def _drop_pending_startup_volume(self) -> None:
        """Discard a pending tracking write superseded by a settings reload.

        `_load_volume_config` re-reads the whole `volume` section from disk, so
        anything still only in memory is about to be overwritten by what the
        file says. Writing it afterwards would resurrect it — and the reload's
        own trigger is usually `PUT /api/settings/volume-startup`, i.e. a value
        the user just chose explicitly. The tracked volume itself is not lost:
        `last_volume.json` holds the local client's real level and is what
        `initialize()` restores from whenever `restore_last_volume` is on.
        """
        if self._startup_persist_task and not self._startup_persist_task.done():
            self._startup_persist_task.cancel()
        self._startup_volume_pending = None

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
        if not registry:
            self.logger.warning("Cannot sync client volumes: client registry not attached")
            return False
        clients = registry.get_online_clients()
        for client_info in clients:
            cid = client_info.mac_id
            # Read equalizer volume via the router, which owns local/remote
            # dispatch (local CamillaDSP vs satellite proxy) — VolumeService no
            # longer reaches a satellite directly.
            if not client_info.ip:
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
            # Online in the registry is what "available" means here — there is no
            # second liveness field to read, and the registry is the authority.
            await self._state_store.register_client(cid, volume_db=volume, available=True)

        self.logger.info(f"Synced {len(clients)} clients from equalizer")
        await self.broadcast_volume_state(show_bar=False)
        return True

    @handle_errors(default=False)
    async def push_volume_to_all_clients(self) -> bool:
        """Push each online client's own level and mute state to its hardware.

        There is no target to force on everyone: a mode switch pushes nothing
        now, so the boot sync is the only caller and every client is restored to
        what it owns (restore_last_volume) or to startup_volume_db.
        """
        try:
            async with asyncio.timeout(10.0):
                async with self._push_lock:
                    return await self._do_push_volume_to_all_clients()
        except asyncio.TimeoutError:
            self.logger.warning("Timeout waiting for push lock (>10s)")
            return False

    async def _do_push_volume_to_all_clients(self) -> bool:
        """Internal push implementation (called under _push_lock)."""
        client_ids = self._online_client_ids()
        if not client_ids:
            # Benign boot-ordering case: the snapserver WS is ready but the local
            # snapclient has not registered yet. Push is a no-op (returns True) and
            # the CLIENT_CONNECT handler + delayed sync apply volumes once it joins.
            self.logger.info("PUSH_VOLUME: No online clients yet — nothing to push (will sync on client connect)")
            return True
        self.logger.info(f"PUSH_VOLUME: Found {len(client_ids)} online clients: {client_ids}")

        updates = {}
        restore_enabled = self._volume_config.restore_last_volume
        startup_volume = self._volume_config.startup_volume_db

        for cid in client_ids:
            # Its own level, or the configured startup one — the two answers the
            # docstring names, and no third. Reading the *local* CamillaDSP for a
            # client the store does not know was the last surviving path that
            # derived one speaker's level from another's; the store now seeds an
            # unknown client at startup_volume_db, so it never even ran.
            persisted = self._state_store.get_client_volume(cid) if restore_enabled else None
            updates[cid] = persisted if persisted is not None else startup_volume

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

    def _refused(self, client_id: str, applied: bool, what: str) -> bool:
        """Did a client that is *still online* refuse the command?

        EqualizerController answers False for two opposite reasons: the router
        short-circuited an offline client — a skip, since a switched-off speaker
        refusing nothing is not a failure anyone can act on — or the client
        answered and refused. The registry, read *after* the call, separates
        them: a client that is still online and did not take the command is the
        one nothing will ever replay it to, and the only one the operator has to
        be told about. The level is error, so the banner fires.

        What becomes of the stored value afterwards is not this decision's
        business: the reconnect replays both the stored mute and the stored
        volume. Neither makes this call a failure.
        """
        if applied or not self._client_registry:
            return False
        if not self._client_registry.is_client_online(client_id):
            return False
        self.logger.error(f"{what} not applied to {client_id} — the stored value is unchanged")
        return True

    @handle_errors(default=False)
    async def update_client_volume_db(self, client_id: str, volume_db: float, broadcast: bool = True) -> bool:
        """Update client volume in dB (called from API routes).

        False when an online client refused the level. The store is written
        only for a client that holds it, so the broadcast — and the UI reading
        it — keeps agreeing with the hardware rather than with the request.
        """
        applied = await self._equalizer_controller.set_equalizer_volume(client_id, volume_db)
        refused = self._refused(client_id, applied, f"Volume {volume_db:.1f}dB")

        if not refused:
            await self._state_store.set_client_volume(client_id, volume_db)
        if broadcast and self._is_multiroom_enabled():
            await self.broadcast_volume_state(show_bar=False)
        return not refused

    @handle_errors(default=False)
    async def set_client_mute(self, client_id: str, mute: bool, broadcast: bool = True) -> bool:
        """Set mute state for a client. False when an online client refused it."""
        applied = await self._equalizer_controller.set_equalizer_mute(client_id, mute)
        refused = self._refused(client_id, applied, f"Mute {mute}")

        if not refused:
            await self._state_store.set_client_mute(client_id, mute)
        if broadcast:
            await self.broadcast_volume_state(show_bar=False)
        return not refused

    # ============================================================================
    # ATOMIC ZONE OPERATIONS
    # ============================================================================

    async def apply_zone_volume_delta(self, zone_id: str, delta_db: float) -> float:
        """Apply volume delta to entire zone atomically. Returns new zone average in dB.

        Every member's stored level moves; only the reachable ones are pushed to
        hardware. A member that was away during the adjustment therefore comes
        back at the level its room moved to, not the one it left.
        """
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

        # Phase B: hardware fan-out outside lock, reachable members only
        reachable = {h: v for h, v in updates.items()
                     if self._state_store.is_client_available(h)}
        self.logger.info(
            f"Applying zone delta: {zone_id} {delta_db:+.1f}dB -> {len(updates)} clients "
            f"({len(reachable)} reachable)"
        )
        results = await self._equalizer_controller.apply_volumes_parallel(reachable)

        committed, failures = self._split_on_verdict(updates, reachable, results)
        await self._state_store.apply_zone_updates(committed)

        if failures:
            self.logger.warning(f"Failed to update clients: {failures}")

        # Startup-volume tracking + broadcast
        local_mac_id = self._state_store.local_mac_id
        local_volume = updates.get(local_mac_id) if local_mac_id else None
        local_volume = local_volume or self._state_store.local_volume_db
        await self._update_startup_volume_if_needed(local_volume)
        await self.broadcast_volume_state(show_bar=False)

        new_avg = self._state_store.compute_zone_average(zone_id)
        self.logger.info(f"Zone {zone_id} updated: {new_avg:.1f}dB ({len(committed)}/{len(updates)} stored)")
        return new_avg

    # ============================================================================
    # SERVICE INITIALIZATION
    # ============================================================================

    async def initialize(self) -> bool:
        """
        Initialize volume service.

        Applies the startup volume to CamillaDSP. The card's own mixer is pinned
        at unity by milo-alsa-passthrough (ExecStartPre of milo-camilladsp.service),
        not from here — a satellite runs no backend and needs the same pin.
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

        multiroom_enabled = await self.settings_service.get_setting("routing.multiroom_enabled")

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
        client_ids = self._get_controllable_client_ids()
        try:
            async with asyncio.timeout(2.0):
                async with self._volume_lock:
                    updates = await self._compute_multiroom_updates(target_db, client_ids)
        except asyncio.TimeoutError:
            self.logger.warning("Timeout waiting for volume lock (>2s)")
            return False

        success = await self._apply_volume_to_hardware(target_db, updates, client_ids)
        if success:
            await self._update_startup_volume_if_needed(target_db)
            await self.broadcast_volume_state(show_bar)
        return success

    async def adjust_volume_db(self, delta_db: float, show_bar: bool = True) -> bool:
        """Adjust volume by delta in dB (positive = louder, negative = quieter)."""
        if not self._volume_control and not self._is_multiroom_enabled():
            return True  # Direct + DAC: no clients to control
        client_ids = self._get_controllable_client_ids()
        try:
            async with asyncio.timeout(2.0):
                async with self._volume_lock:
                    volume_state = await self._state_store.get_complete_state()
                    target_db = self._volume_config.clamp(volume_state.global_volume_db + delta_db)
                    updates = await self._compute_multiroom_updates(target_db, client_ids)
        except asyncio.TimeoutError:
            self.logger.warning("Timeout waiting for volume lock (>2s)")
            return False

        success = await self._apply_volume_to_hardware(target_db, updates, client_ids)
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
        """Mark every client the registry reports online as available.

        Belt and braces over the CLIENT_CONNECTED events VolumeStateStore is
        already subscribed to: this runs once the snapcast WebSocket reports
        ready, which is not ordered against the registration sweep that emits
        them. It only ever raises availability — a client that is genuinely gone
        is lowered by CLIENT_DISCONNECTED, never here.
        """
        client_ids = self._online_client_ids()
        for mac_id in client_ids:
            await self._state_store.set_client_availability(mac_id, True)
        self.logger.info(f"Initialized availability for {len(client_ids)} online clients")

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
        await self._flush_startup_volume()
        await self._bg.cancel_all()
        await self._state_store.cleanup()
        self.logger.info("VolumeService cleanup completed")
