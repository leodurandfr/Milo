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
import logging
from typing import Callable, List, Optional, Tuple

from backend.shared.background import BackgroundTaskSet
from backend.shared.decorators import handle_errors
from backend.core.volume.state import VolumeStateStore
from backend.core.volume.equalizer_controller import EqualizerController
from backend.core.multiroom.identity import get_local_mac
from backend.core.models.volume import VolumeConfig
from backend.core.models.volume_state import VolumeState
from backend.core.models.ws_events import (
    VolumeChanged,
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
        self._push_lock = asyncio.Lock()

        # Volume configuration (loaded from settings in _load_volume_config)
        self._volume_config = VolumeConfig()

        # Volume control flag (False = DAC mode, external amp manages volume)
        self._volume_control: bool = True

        # VolumeStateStore (SSOT) + EqualizerController (hardware abstraction)
        self._state_store = VolumeStateStore()
        self._equalizer_controller = EqualizerController(
            equalizer_router=equalizer_router,
            clamp=lambda volume_db: self._volume_config.clamp(volume_db),
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

    def _global_members(self) -> Optional[Tuple[List[str], List[str]]]:
        """(members, reachable) of a global move, or None when there is nothing to move.

        Multiroom: every client with volume control, and the reachable ones
        among them. Direct: the local speaker alone, always reachable — a
        CamillaDSP that is not up yet is handled by `_move`, not here. None only
        when the local speaker is not known at all (no MAC to key a level on).
        """
        if self._is_multiroom_enabled():
            members = [
                mac_id for mac_id in self._state_store.client_ids()
                if self._state_store.has_volume_control(mac_id)
            ]
            return members, self._reachable(members)
        local_mac = self._state_store.local_mac_id
        if local_mac is None:
            self.logger.warning("Direct mode: local client unknown — volume not recorded")
            return None
        if not self._state_store.has_client(local_mac):
            # Known by MAC, with no level yet: seeded at the startup level, as a
            # client the registry announces for the first time is.
            self._state_store.set_local_volume(self._volume_config.startup_volume_db)
        return [local_mac], [local_mac]

    def _reachable(self, members: List[str]) -> List[str]:
        """The members a move counts as reachable: available in the volume state.

        The same flag the averages every screen shows are computed over, so a
        level aimed at a zone's slider is measured against the average under
        the thumb. A member the router then finds offline is skipped at the
        door, and stored like any absent room.
        """
        multiroom = self._is_multiroom_enabled()
        return [m for m in members if self._state_store.is_client_available(m, multiroom)]

    def _submit_levels(self, mac_ids: List[str]) -> Optional[asyncio.Future]:
        """Send each speaker its stored level, through the door; answer the local one's future.

        A local CamillaDSP that is not up yet gets nothing: the reconnect
        callback puts the stored level on it, so that is not a failure.
        """
        local_mac = self._state_store.local_mac_id
        local_future = None
        for mac_id in mac_ids:
            level = self._state_store.get_client_volume(mac_id)
            if level is None:
                continue
            if mac_id == local_mac:
                if not self._is_equalizer_available():
                    self.logger.info(
                        f"CamillaDSP not ready — local volume {level:.1f}dB recorded, "
                        "will apply on reconnect"
                    )
                    continue
                local_future = self._equalizer_controller.submit_volume(mac_id, level)
            else:
                self._equalizer_controller.submit_volume(mac_id, level)
        return local_future

    async def _move(self, members: List[str], reachable: List[str],
                    resolve: Callable[[Optional[float]], Optional[float]]) -> Tuple[bool, Optional[float]]:
        """Move a group of rooms together — the one primitive behind every volume move.

        Global and zone, relative and absolute, direct and multiroom. `resolve`
        receives the average level of the reachable members (None when there is
        none) and answers the delta, or None to move nothing: a relative move
        ignores the average, an absolute one needs it.

        Everything up to the submissions is one synchronous step: read the
        levels, bound the delta, write them, submit them. With no await in it,
        no other move can read a level this one has not written yet, which is
        how ten +2 dB steps in flight together used to land as one — the store
        was written only after the hardware answered. The speakers are
        submitted in that same step, so the order of submissions is the order
        of writes, and the last level each speaker receives is its stored one.

        The group moves as a block (`VolumeConfig.bound_block_delta`): it stops
        when its loudest reachable room reaches the maximum or its quietest the
        minimum, so the rooms keep their distances and no level is ever stored
        outside the limits. A room that is away moves by the same delta, and so
        comes back where its room went; it does not bound the block (a speaker
        unplugged at -10 would freeze the whole house), so it is clamped to the
        limits instead, and stored only. With no room reachable at all, the
        block is bounded by all of them.

        A room that refuses keeps the level asked for, like a satellite does
        with its own cache and the local unit with `reapply_current_volume`. The
        local speaker is the only one awaited: a slow satellite converges on its
        own and delays nobody. A local CamillaDSP that is not up yet is not a
        failure: the reconnect callback puts the stored level on it.

        Returns:
            (ok, delta) — ok is False only when the local speaker refused; delta
            is None when nothing moved.
        """
        levels = {m: self._state_store.get_client_volume(m) for m in members}
        levels = {m: level for m, level in levels.items() if level is not None}
        reach = [m for m in reachable if m in levels]
        average = sum(levels[m] for m in reach) / len(reach) if reach else None
        delta = resolve(average)
        if delta is None:
            return True, None
        # Bounded by the reachable rooms; a group with none reachable (every
        # speaker away) still moves as a block, bounded by all its rooms.
        bounding = [levels[m] for m in (reach or levels)]
        if bounding:
            delta = self._volume_config.bound_block_delta(delta, max(bounding), min(bounding))
        self._state_store.set_levels({m: level + delta for m, level in levels.items()})
        local_future = self._submit_levels(reach)

        if local_future is not None and not await local_future:
            self.logger.error("LOCAL server volume update failed — server audio may be silent")
            return False, delta
        return True, delta

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
        # The unmute is stored first, like every mute: published muted while it
        # plays, the speaker would also be muted again by the next CamillaDSP
        # reconnect and the next boot, both of which apply the stored mute.
        local_mac = self._state_store.local_mac_id
        if not multiroom_enabled and local_mac:
            await self._state_store.set_client_mute(local_mac, False)

        # DAC mode makes no CamillaDSP call at all here: reapply_current_volume
        # pins it at 0 dB and unmuted, and the external amp owns the rest.
        if self._volume_control and not multiroom_enabled:
            # Through the door, ordered after any mute still in flight to it.
            if await self._equalizer_controller.set_equalizer_mute(local_mac, False, force=True):
                self.logger.info("Switched to direct: CamillaDSP unmuted, no level changed")
            else:
                self.logger.warning("Failed to unmute CamillaDSP on the way to direct mode")

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
        try:
            self.settings_service.invalidate_cache()
            volume_settings = await self.settings_service.get_setting('volume')

            self._volume_config = VolumeConfig(
                limit_min_db=volume_settings["limit_min_db"],
                limit_max_db=volume_settings["limit_max_db"],
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
        """Reload the volume limits and bring every level they now exclude to them.

        Silent when the limits did not move: this runs on every save of the
        volume settings, and a step-size edit must not push a volume frame.
        """
        old_limits = (self._volume_config.limit_min_db, self._volume_config.limit_max_db)

        await self._load_volume_config()

        if (self._volume_config.limit_min_db, self._volume_config.limit_max_db) == old_limits:
            return True

        await self._bring_levels_into_limits()
        await self.broadcast_volume_state(show_bar=False)
        return True

    async def _bring_levels_into_limits(self) -> None:
        """Move each level the limits exclude to the nearest limit, room by room.

        Never through the average. Measured on the unit: limits -78..-8, rooms
        at -78/-75/-40, the minimum raised to -70. The average (-64.3) sat
        inside the new window, so nothing moved and the two quiet rooms were
        clamped on their next write only. When the average fell outside, the
        whole house was sent to the middle of the range, -39 dB, a jump of
        +39 dB on a speaker set to -78. Now the two quiet rooms go to -70 and
        -40 stays where it is.

        Only the rooms that moved are sent anything, and only the reachable
        ones; an absent room gets its new level at its next admission.
        """
        moved = {}
        for mac_id in self._state_store.client_ids():
            # DAC clients too: their level is only a record while the amp owns
            # it, but it is a record a later switch back to managed would play.
            level = self._state_store.get_client_volume(mac_id)
            nearest = self._volume_config.clamp(level)
            if nearest != level:
                moved[mac_id] = nearest
        self._state_store.set_levels(moved)

        if self._is_multiroom_enabled():
            reachable = [m for m in self._reachable(list(moved)) if self._state_store.has_volume_control(m)]
        else:
            local_mac = self._state_store.local_mac_id
            reachable = [local_mac] if local_mac in moved and self._volume_control else []
        local_future = self._submit_levels(reachable)
        if local_future is not None and not await local_future:
            self.logger.error("LOCAL server volume update failed — server audio may be silent")

    @handle_errors(default=False)
    async def reload_config(self) -> bool:
        """Reload the volume section after a startup or step setting changed.

        Nothing is broadcast: no level moved, and each setting reaches the
        screens through its own `settings/*_changed` event. The limits are the
        exception, with their own `reload_volume_limits`.
        """
        await self._load_volume_config()
        return True

    # ============================================================================
    # CLIENT VOLUME MANAGEMENT (VolumeStateStore architecture)
    # ============================================================================

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
            # its admission applies its level once it joins.
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

        # Stored, then sent, in one step — the order every move follows. Writing
        # after the answer put back the level read before it, over a move that
        # had landed in between, while the speaker kept the move's level.
        self._state_store.set_levels(updates)
        answers = {
            cid: self._equalizer_controller.submit_volume(
                cid, self._state_store.get_client_volume(cid) if self._state_store.has_client(cid) else volume
            )
            for cid, volume in updates.items()
        }
        results = dict(zip(answers, await asyncio.gather(*answers.values())))
        succeeded = [h for h, ok in results.items() if ok]
        failures = [h for h, ok in results.items() if not ok]

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

        # And each client's level trim, which is level state like the other two.
        # The local unit needs it because its CamillaDSP came up with the graph
        # its config file holds; a satellite does not (its own file carries the
        # trim) but gets it anyway, which also catches a trim changed while it
        # was away. A trim of 0 removes nothing that is not already absent.
        for cid in client_ids:
            gain_db = self._recorded_gain(cid)
            try:
                await self._equalizer_controller.set_equalizer_gain(cid, gain_db, force=True)
            except Exception as e:
                self.logger.warning(f"PUSH_VOLUME: Failed to apply the level trim to {cid}: {e}")

        await self.broadcast_volume_state(show_bar=False)
        return len(failures) == 0

    def _refused(self, client_id: str, applied: bool) -> bool:
        """Did a client that is *still online* refuse the command?

        EqualizerController answers False for two opposite reasons: the router
        short-circuited an offline client — a skip, since a switched-off speaker
        refusing nothing is not a failure anyone can act on — or the client
        answered and refused. The registry, read *after* the call, separates
        them: a client that is still online and did not take the command is the
        one nothing will ever replay it to, and the only one the operator has to
        be told about. The level is error, so the banner fires.

        The stored value keeps what was asked either way: the reconnect
        replays both the stored mute and the stored volume, and a satellite that
        refused has cached it for its own reconnect. The refusal itself is
        logged once by EqualizerController, so nothing is logged here.
        """
        if applied or not self._client_registry:
            return False
        return self._client_registry.is_client_online(client_id)

    @handle_errors(default=False)
    async def update_client_volume_db(self, client_id: str, volume_db: float, broadcast: bool = True) -> bool:
        """Update client volume in dB (called from API routes).

        The level is stored, then sent — the same order as every other write,
        so no move can read a level older than the one this sends. False when
        an online client refused it; the store keeps the level asked for, as
        the satellite's own cache does, and the reconnect applies it.
        """
        await self._state_store.set_client_volume(client_id, volume_db)
        applied = await self._equalizer_controller.set_equalizer_volume(client_id, volume_db)
        refused = self._refused(client_id, applied)

        if broadcast and self._is_multiroom_enabled():
            await self.broadcast_volume_state(show_bar=False)
        return not refused

    @handle_errors(default=False)
    async def set_client_mute(self, client_id: str, mute: bool, broadcast: bool = True) -> bool:
        """Set mute state for a client. False when an online client refused it.

        Stored, then sent, like a level; the store keeps the mute asked for.
        """
        await self._state_store.set_client_mute(client_id, mute)
        applied = await self._equalizer_controller.set_equalizer_mute(client_id, mute)
        refused = self._refused(client_id, applied)

        if broadcast:
            await self.broadcast_volume_state(show_bar=False)
        return not refused

    # ============================================================================
    # ATOMIC ZONE OPERATIONS
    # ============================================================================

    async def set_zone_mute(self, zone_id: str, mute: bool) -> List[str]:
        """Mute or unmute a whole zone in one request and one broadcast.

        The web UI sent one PATCH per member and got one broadcast per member.
        Every member's mute is stored, the absent ones included — the same rule
        as the client route: a speaker that is away takes it at its admission.
        The reachable ones are sent it. A member whose amp owns its level is
        muted too: a mute is not a level, and the router mutes its DSP like any
        other. Returns the reachable members that refused it.

        Raises:
            ValueError: unknown zone.
        """
        members = self._state_store.zone_clients(zone_id)
        reachable = set(self._reachable(members))
        answers = {}
        for mac_id in members:
            await self._state_store.set_client_mute(mac_id, mute)
            if mac_id in reachable:
                answers[mac_id] = self._equalizer_controller.submit_mute(mac_id, mute)
        refused = [
            mac_id for mac_id, answer in answers.items()
            if self._refused(mac_id, await answer)
        ]
        await self.broadcast_volume_state(show_bar=False)
        return refused

    async def apply_zone_volume_delta(self, zone_id: str, delta_db: float) -> Tuple[float, float]:
        """Move a whole zone by `delta_db`. Returns (new zone average, delta applied).

        Every member's stored level moves; only the reachable ones are pushed to
        hardware. A member that was away during the adjustment therefore comes
        back at the level its room moved to, not the one it left. An entirely
        offline zone therefore moves too: a delta needs no average. The delta
        applied is less than the one asked when the zone's loudest room meets a
        limit first.
        """
        return await self._move_zone(zone_id, lambda average: delta_db)

    async def set_zone_volume(self, zone_id: str, target_db: float) -> Tuple[float, float]:
        """Move a zone so its average lands on `target_db`. Returns (average, delta).

        The delta is measured by `_move`, against the average the store holds at
        that moment, and never by the caller: the web slider used to subtract
        an average it had captured itself, and each send made while the
        previous one was in flight reused that stale base, so a drag added its
        deltas up. A zone with no member online has no average to aim at, and
        moves nothing.
        """
        target_db = self._volume_config.clamp(target_db)
        return await self._move_zone(
            zone_id,
            lambda average: None if average is None else target_db - average,
        )

    async def _move_zone(self, zone_id: str,
                         resolve: Callable[[Optional[float]], Optional[float]]) -> Tuple[float, float]:
        """Move a zone's members by the delta `resolve` derives from their average.

        Returns (new average, delta applied).

        Raises:
            ValueError: unknown zone.
        """
        members = self._state_store.zone_members(zone_id)
        reachable = self._reachable(members)
        _, delta_db = await self._move(members, reachable, resolve)
        new_avg = self._state_store.compute_zone_average(zone_id, self._is_multiroom_enabled())
        if delta_db is None:
            return new_avg, 0.0

        self.logger.debug(
            f"Zone {zone_id} moved {delta_db:+.1f}dB -> {new_avg:.1f}dB "
            f"({len(reachable)}/{len(members)} reachable)"
        )
        await self.broadcast_volume_state(show_bar=False)
        return new_avg, delta_db

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

            # Initialize VolumeStateStore (loads the persisted levels)
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
                # DAC mode: pin CamillaDSP at 0dB (external amp manages volume).
                # A command still in flight to it would land after the pin
                # otherwise, and attenuate a path the amp now owns.
                if self._state_store.local_mac_id:
                    await self._equalizer_controller.idle(self._state_store.local_mac_id)
                await self._camilladsp_service.set_volume(0.0)
                await self._camilladsp_service.set_mute(False)
                self.logger.info("DAC mode: CamillaDSP pinned at 0 dB")
                # A level trim is attenuation too: left in the graph it would
                # make that unity pin a lie. Cleared in the record as well —
                # a stored trim on a speaker whose amp owns the level is a value
                # nothing applies again, waiting to come back when the flag flips.
                await self._clear_local_gain()
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

    def _recorded_gain(self, mac_id: str) -> float:
        """The level trim the registry holds for a client (0.0 when unknown)."""
        client = self._client_registry.get_client(mac_id) if self._client_registry else None
        return client.gain_db if client else 0.0

    async def sync_local_gain(self) -> None:
        """Apply the local unit's recorded level trim to its DSP — or 0 in direct mode.

        The trim has one durable home, `Client.gain_db`, and the server's
        CamillaDSP config file carries nothing across a restart — so something
        has to re-derive it. Three disjoint events can leave the local graph
        without it, and none of them sees the other two:

        * a daemon restart (boot, a CamillaDSP update, a crash) fires the
          reconnect callback and no client event — `reapply_current_volume`;
        * a multiroom mode switch fires client events and does NOT restart the
          daemon (measured: its ActiveEnterTimestamp is unchanged across
          off/on) — AudioRoutingService's post-transition step;
        * the boot push restores every online client's level, and the trim rides
          with it there for the satellites too.

        Direct mode pushes 0: the trim balances this speaker against the others,
        and there are no others. The record is untouched, so coming back to
        multiroom re-applies it.

        What does NOT cover any of this is the admission sync: its sweep only
        runs the full recipe for a client the registry has never seen, and the
        registry is persisted — so after the first boot no client is ever new.
        """
        mac_id = self._state_store.local_mac_id
        if not mac_id or not self._equalizer_controller:
            return
        target = self._recorded_gain(mac_id) if self._is_multiroom_enabled() else 0.0
        if not await self._equalizer_controller.set_equalizer_gain(mac_id, target, force=True):
            self.logger.warning(f"Could not apply the local level trim ({target:+.1f} dB)")

    async def _clear_local_gain(self) -> None:
        """Drop the local unit's level trim, DSP and record, if it holds one.

        Called on the way into DAC mode only — and before the flag moves, since
        EqualizerRouter.set_gain skips a client already declared DAC.
        """
        mac_id = self._state_store.local_mac_id
        if not mac_id or not self._client_registry:
            return
        client = self._client_registry.get_client(mac_id)
        if not client or client.gain_db == 0.0:
            return
        self.logger.info("DAC mode: clearing the local level trim")
        await self._equalizer_controller.set_equalizer_gain(mac_id, 0.0)
        await self._client_registry.set_client_gain(mac_id, 0.0)

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
        # Through the door like every other write: clamped to the limits, and
        # ordered after any level a move already has in flight to this daemon.
        await self._equalizer_controller.set_equalizer_volume(local_mac_id, volume_db, force=True)
        await self._equalizer_controller.set_equalizer_mute(local_mac_id, local_mute, force=True)
        self.logger.info(f"Re-applied volume after CamillaDSP reconnect: {volume_db:.1f}dB, mute={local_mute}")
        # The daemon came back with the graph its config file holds, which carries
        # no trim — see sync_local_gain for why nothing else covers this event.
        await self.sync_local_gain()

    async def _apply_startup_volume(self) -> None:
        """
        Apply startup volume and mute state to CamillaDSP.

        Volume source is determined by restore_last_volume setting:
        - True: the local client's OWN persisted per-client volume (state store,
          restored from last_volume.json before this runs).
        - False: the user-configured fixed startup_volume_db.

        startup_volume_db is only ever the operator's setting: every level a
        room was left at is in last_volume.json already, so nothing rewrites
        the setting as the volume moves.

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

        # In restore mode, the local client's own persisted volume is authoritative.
        # Before the local client is resolved (fresh boot), fall back to the
        # configured startup volume rather than the -45 dB hard default. In fixed
        # mode, the user-configured value applies to all clients.
        if (self._volume_config.restore_last_volume
                and local_mac_id is not None
                and self._state_store.has_client(local_mac_id)):
            target_volume = self._state_store.get_client_volume(local_mac_id)
        else:
            target_volume = self._volume_config.startup_volume_db
        self.logger.info(f"Applying startup volume: {target_volume:.1f} dB")

        # Get persisted mute state from local client (False if no client registered yet)
        local_mute = self._state_store.get_client_mute(local_mac_id) if local_mac_id else False

        # Through the door, which clamps: startup_volume_db is checked against the
        # limits only when it is saved, and a later change of limits leaves it
        # wherever it was. The router reaches the local daemon even before the
        # registry knows the local client. Keyed by the local MAC, like every
        # later command to that daemon, so they stay ordered; on a first boot
        # whose MAC is not resolved yet the key is None, and the fallback to the
        # startup level matters more than ordering against moves that cannot
        # have started.
        if target_volume is not None and self._camilladsp_service:
            await self._equalizer_controller.set_equalizer_volume(local_mac_id, target_volume, force=True)
            await self._equalizer_controller.set_equalizer_mute(local_mac_id, local_mute, force=True)
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
        """Get current volume in dB (average of the reachable clients in multiroom mode)."""
        volume_state = await self._state_store.get_complete_state(self._is_multiroom_enabled())
        return volume_state.global_volume_db

    async def set_volume_db(self, volume_db: float, show_bar: bool = True) -> bool:
        """Move the whole house so its average lands on `volume_db`."""
        if not self._volume_control and not self._is_multiroom_enabled():
            return True  # Direct + DAC: no clients to control
        group = self._global_members()
        if group is None:
            return False
        target_db = self._volume_config.clamp(volume_db)
        success, _ = await self._move(
            *group, lambda average: None if average is None else target_db - average
        )
        # Even when the local speaker refused: the levels were written and the
        # satellites were sent theirs, and every screen must show them.
        await self.broadcast_volume_state(show_bar)
        return success

    async def adjust_volume_db(self, delta_db: float, show_bar: bool = True) -> bool:
        """Move the whole house by `delta_db` (positive = louder, negative = quieter)."""
        if not self._volume_control and not self._is_multiroom_enabled():
            return True  # Direct + DAC: no clients to control
        group = self._global_members()
        if group is None:
            return False
        success, _ = await self._move(*group, lambda average: delta_db)
        # In the background: the rotary's accumulator awaits this call before
        # sending its next batch, and the snapshot a broadcast builds has no
        # business pacing the knob.
        self._bg.spawn(self.broadcast_volume_state(show_bar), label="post_volume_update")
        return success

    # ============================================================================
    # WEBSOCKET BROADCASTING
    # ============================================================================

    async def volume_event(self, show_bar: bool) -> VolumeChanged:
        """The `volume_changed` event for the state as it is now.

        The one place it is built: the broadcast after every change and the
        snapshot a new WebSocket connection receives both send this, so a
        field added to one is never missing from the other.
        """
        volume_state = await self.get_volume_state()
        return VolumeChanged(
            show_bar=show_bar,
            multiroom_enabled=volume_state.mode == "multiroom",
            state=volume_state.to_dict()
        )

    async def broadcast_volume_state(self, show_bar: bool = True) -> None:
        """Broadcast volume state immediately to WebSocket clients."""
        try:
            await self.state_machine.broadcast(await self.volume_event(show_bar))

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
        return await self._state_store.get_complete_state(self._is_multiroom_enabled())

    @handle_errors(default={"main": DEFAULT_VOLUME_DB, "mute": False})
    async def get_client_volume(self, hostname: str) -> dict:
        """
        Get volume for a specific client (works in both modes).

        Returns: {"main": volume_db, "mute": bool}
        """
        volume_state = await self._state_store.get_complete_state(self._is_multiroom_enabled())
        client = volume_state.clients.get(hostname)
        if client:
            return {"main": client.volume_db, "mute": client.mute}
        return {"main": DEFAULT_VOLUME_DB, "mute": False}

    async def cleanup(self) -> None:
        """Clean up resources. Flushes pending volume state to disk."""
        await self._bg.cancel_all()
        await self._equalizer_controller.cleanup()
        await self._state_store.cleanup()
        self.logger.info("VolumeService cleanup completed")
