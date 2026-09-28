# backend/core/volume/state.py
"""
VolumeStateStore - Single Source of Truth for Volume State

This service is the ONLY place where volume state is stored and mutated.
All volume operations must go through this store to ensure consistency.

Architecture: "Gros" VolumeStateStore (Option A)
- Integrates persistence, validation, and limits inline
- No external dependency but the client registry it reads
- Autonomous, testable, simple

CONSOLIDATED: Includes all persistence logic (formerly VolumeStorageService)

Integration with ClientRegistryService:
- The registry answers who is online and what the zones are; the store keeps
  no copy of either, and reads them when it answers
- Subscribes to registry events only to learn of a client (seeded at the
  startup level) and of its removal (its level is forgotten)
"""

import asyncio
import contextlib
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, TYPE_CHECKING
from dataclasses import dataclass
import aiofiles

from backend.shared.background import BackgroundTaskSet
from backend.shared.decorators import handle_errors

# Use existing domain models
from backend.core.models.volume import VolumeConfig
from backend.core.models.volume_state import VolumeState, ClientVolume, ZoneVolume
from backend.config.constants import DEFAULT_VOLUME_DB, MIN_VOLUME_DB, MAX_VOLUME_DB

if TYPE_CHECKING:
    from backend.core.multiroom.client_registry import ClientRegistryService


@dataclass
class StoredLevel:
    """What the store owns of one client: the level it plays at, and its mute.

    Whether it is online and whether Milo controls its volume are the
    registry's, read when a snapshot is built — never kept here.
    """
    volume_db: float
    mute: bool = False


class VolumeStateStore:
    """
    Single Source of Truth for all volume state.

    Responsibilities:
    - Track client volumes and mutes — the one thing it owns
    - Calculate zone averages over the members the registry reports online
    - Validate volume limits
    - Persist state to disk (CONSOLIDATED - no separate storage service)
    - Thread-safe with async locks
    """

    # Persistence
    STORAGE_PATH = Path("/var/lib/milo/last_volume.json")

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)

        # Client registry reference (set via set_registry after construction)
        self._registry: Optional["ClientRegistryService"] = None

        self._clients: Dict[str, StoredLevel] = {}

        # Local client mac_id. Loaded from disk at init (if previously persisted)
        # so _clients[_local_mac_id].volume_db can serve as SSOT immediately;
        # otherwise set by the registry CLIENT_CONNECTED event (ip=127.0.0.1).
        self._local_mac_id: Optional[str] = None

        # VolumeConfig reference (set via set_volume_config from VolumeService)
        self._volume_config: Optional[VolumeConfig] = None

        # Volume control flag (False = DAC mode, external amp manages volume)
        self._volume_control: bool = True

        # Debounced persistence (prevent rapid disk writes during volume sweeps)
        self._persist_debounce_task: Optional[asyncio.Task] = None
        self._bg = BackgroundTaskSet(self.logger, "volume")
        self._PERSIST_DEBOUNCE_S = 2.0

        # Concurrency control
        self._lock = asyncio.Lock()

        # Ensure storage directory exists
        self._ensure_storage_directory()

        self.logger.info("VolumeStateStore initialized (SSOT for volume)")

    def set_volume_config(self, config: VolumeConfig) -> None:
        """Set VolumeConfig reference for clamping (called by VolumeService after config load)."""
        self._volume_config = config
        self.logger.debug(f"VolumeConfig set: limits={config.limit_min_db:.1f}/{config.limit_max_db:.1f} dB")

    def set_volume_control(self, enabled: bool) -> None:
        """Set volume control flag (False = DAC mode, external amp manages volume)."""
        self._volume_control = enabled

    def set_registry(self, registry: "ClientRegistryService") -> None:
        """
        Set the client registry and subscribe to availability events.

        Args:
            registry: ClientRegistryService instance
        """
        self._registry = registry
        registry.subscribe(self._handle_registry_event)
        self.logger.info("VolumeStateStore subscribed to ClientRegistryService events")

    async def _handle_registry_event(self, event_type: str, data: dict) -> None:
        """Handle events from ClientRegistryService."""
        from backend.core.multiroom.models import RegistryEventType

        if event_type in (RegistryEventType.CLIENT_CONNECTED, RegistryEventType.CLIENT_UPDATED):
            mac_id = data.get("mac_id")
            if not mac_id:
                return
            if data.get("client", {}).get("ip") == "127.0.0.1":
                self._local_mac_id = mac_id
            if mac_id not in self._clients:
                # Seed at the configured startup level, never at
                # DEFAULT_VOLUME_DB: _resolve_target_volume reads this store
                # to decide what a client comes back at, so a fabricated -45
                # here *is* the level the speaker gets — and it shadowed the
                # startup_volume_db branch that exists for exactly this case.
                await self.register_client(
                    mac_id,
                    volume_db=(
                        self._volume_config.startup_volume_db
                        if self._volume_config else DEFAULT_VOLUME_DB
                    ),
                )

        elif event_type == RegistryEventType.CLIENT_DISCONNECTED:
            # Offline is the registry's to say; only a client removed from the
            # registry takes its level with it.
            mac_id = data.get("mac_id")
            if mac_id in self._clients and not (self._registry and self._registry.get_client(mac_id)):
                async with self._lock:
                    del self._clients[mac_id]
                    self._schedule_persist()
                self.logger.info(f"Deleted client {mac_id} from volume state")

    @handle_errors(default=None)
    def _ensure_storage_directory(self) -> None:
        """Create storage directory if it doesn't exist."""
        self.STORAGE_PATH.parent.mkdir(parents=True, exist_ok=True)

    # ========== Lifecycle ==========

    async def cleanup(self) -> None:
        """Flush pending volume state to disk on shutdown."""
        if self._persist_debounce_task and not self._persist_debounce_task.done():
            self._persist_debounce_task.cancel()
            await self._persist_state_async()
            self.logger.info("Flushed pending volume state on shutdown")
        await self._bg.cancel_all()

    async def initialize(self) -> None:
        """
        Initialize store from settings and persistent storage.

        Must be called after construction. Volume config (and thus clamping limits)
        is already set via set_volume_config() before this is called.
        """
        async with self._lock:
            await self._load_persisted_state()

            limits_info = (f"{self._volume_config.limit_min_db:.1f}/{self._volume_config.limit_max_db:.1f}"
                          if self._volume_config else "not set")
            self.logger.info(f"VolumeStateStore initialized: clients={len(self._clients)}, "
                           f"local_volume={self.local_volume_db:.1f}dB, "
                           f"limits={limits_info}dB")

    def set_local_volume(self, volume_db: float) -> None:
        """
        Set local client volume in memory with debounced persistence.
        Used for direct mode where volume changes are frequent.
        """
        local_mac_id = self._local_mac_id
        if not local_mac_id:
            self.logger.warning(
                "set_local_volume called before local mac_id is known; ignored"
            )
            return

        volume_db = self._clamp_db(volume_db)
        if local_mac_id in self._clients:
            self._clients[local_mac_id].volume_db = volume_db
        else:
            self._clients[local_mac_id] = StoredLevel(volume_db=volume_db, mute=False)
        self._schedule_persist()

    def ensure_local_client(self, mac_id: str, volume_db: float) -> None:
        """Seed the local client identity + volume when not yet resolved.

        On a fresh direct-mode boot the local client is known via neither Snapcast
        (multiroom off) nor persistence (no prior session), so _local_mac_id stays
        None and set_local_volume() silently drops writes — leaving direct-mode
        volume tracking pinned at DEFAULT. Seeding from the system MAC (which equals
        the snapclient --hostID, so it stays consistent if multiroom is later
        enabled) fixes tracking from the first boot. Idempotent: never overrides an
        already-resolved local mac or an existing client entry.
        """
        if self._local_mac_id or not mac_id:
            return
        self._local_mac_id = mac_id
        if mac_id not in self._clients:
            self._clients[mac_id] = StoredLevel(volume_db=self._clamp_db(volume_db), mute=False)
        self.logger.info(f"Seeded local client {mac_id} at {self.local_volume_db:.1f}dB")

    async def _load_persisted_state(self) -> None:
        """
        Load persisted volume state from disk.

        Format: {"local_mac_id": str | null, "clients": {...}}
        """
        try:
            if not self.STORAGE_PATH.exists():
                self.logger.debug("No persisted volume state found")
                return

            async with aiofiles.open(self.STORAGE_PATH, 'r') as f:
                data = json.loads(await f.read())

            # Restore local mac_id so local_volume_db property works before the
            # registry CLIENT_CONNECTED event fires.
            local_mac_id = data.get("local_mac_id")
            if isinstance(local_mac_id, str) and local_mac_id:
                self._local_mac_id = local_mac_id

            # Restore client volumes. With restore_last_volume off, the levels
            # of the previous run are forgotten here, once: this *is* the
            # startup, and every later reader — a satellite coming back after
            # an update or a reboot — then gets the level it was left at in
            # this run rather than being sent back to the startup level.
            forget = self._volume_config is not None and not self._volume_config.restore_last_volume
            clients_data = data.get("clients", {})
            for mac_id, client_data in clients_data.items():
                volume_db = (
                    self._volume_config.startup_volume_db if forget
                    else client_data.get("volume_db", DEFAULT_VOLUME_DB)
                )
                volume_db = self._clamp_db(volume_db)

                self._clients[mac_id] = StoredLevel(
                    volume_db=volume_db,
                    mute=client_data.get("mute", False),
                )

            self.logger.info(f"Restored volume state: local={self.local_volume_db:.1f}dB, {len(self._clients)} clients")

        except Exception as e:
            self.logger.error(f"Error loading persisted volume state: {e}", exc_info=True)

    def _schedule_persist(self) -> None:
        """Schedule a debounced persist (2s after last change). Safe to call rapidly."""
        if self._persist_debounce_task and not self._persist_debounce_task.done():
            self._persist_debounce_task.cancel()

        async def _debounced():
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.sleep(self._PERSIST_DEBOUNCE_S)
                await self._persist_state_async()

        self._persist_debounce_task = self._bg.spawn(_debounced(), label="persist_state")

    async def _persist_state_async(self) -> None:
        """Persist current volume state to disk using async I/O."""
        try:
            data = {
                "local_mac_id": self._local_mac_id,
                "clients": {
                    mac_id: {
                        "volume_db": client.volume_db,
                        "mute": client.mute
                    }
                    for mac_id, client in self._clients.items()
                }
            }

            temp_path = self.STORAGE_PATH.with_suffix(".tmp")
            self.STORAGE_PATH.parent.mkdir(parents=True, exist_ok=True)

            async with aiofiles.open(temp_path, 'w') as f:
                await f.write(json.dumps(data, indent=2))

            temp_path.replace(self.STORAGE_PATH)
            self.logger.debug(f"Persisted volume state: local={self.local_volume_db:.1f}dB, {len(self._clients)} clients")

        except Exception as e:
            self.logger.error(f"Error persisting volume state: {e}", exc_info=True)

    # ========== Client Management ==========

    async def register_client(self, mac_id: str, volume_db: Optional[float] = None) -> None:
        """
        Register a client, or update its level.

        Args:
            mac_id: Client MAC identifier
            volume_db: Volume in dB (None = keep existing or use default)
        """
        async with self._lock:
            if mac_id in self._clients:
                if volume_db is not None:
                    self._clients[mac_id].volume_db = self._clamp_db(volume_db)
                    self._schedule_persist()
                self.logger.debug(f"Updated client: {mac_id} -> volume_db={self._clients[mac_id].volume_db:.1f}dB")
            else:
                if volume_db is None:
                    volume_db = DEFAULT_VOLUME_DB

                volume_db = self._clamp_db(volume_db)

                self._clients[mac_id] = StoredLevel(volume_db=volume_db)

                self.logger.info(f"Registered client: {mac_id} at {volume_db:.1f}dB")

    async def set_client_mute(self, mac_id: str, mute: bool) -> None:
        """
        Set client mute state.

        Args:
            mac_id: Client MAC identifier
            mute: New mute state
        """
        async with self._lock:
            if mac_id in self._clients:
                self._clients[mac_id].mute = mute
                self._schedule_persist()
                self.logger.debug(f"Client mute: {mac_id} -> {mute}")
            else:
                self.logger.warning(f"Cannot mute unknown client: {mac_id}")

    async def set_client_volume(self, mac_id: str, volume_db: float) -> float:
        """
        Set individual client volume.

        Args:
            mac_id: Client MAC address identifier
            volume_db: New volume in dB

        Returns:
            Clamped volume that was actually set
        """
        async with self._lock:
            volume_db = self._clamp_db(volume_db)

            if mac_id in self._clients:
                self._clients[mac_id].volume_db = volume_db
                self._schedule_persist()
                self.logger.debug(f"Client volume: {mac_id} -> {volume_db:.1f}dB")
            else:
                # Auto-register client inline (avoid deadlock with register_client's lock)
                self._clients[mac_id] = StoredLevel(volume_db=volume_db, mute=False)
                self.logger.info(f"Auto-registered client: {mac_id} at {volume_db:.1f}dB")

        return volume_db

    def get_client_volume(self, mac_id: str) -> Optional[float]:
        """Get persisted volume for a client, or None if not registered."""
        if mac_id in self._clients:
            return self._clients[mac_id].volume_db
        return None

    def get_client_mute(self, mac_id: str) -> bool:
        """Get mute state for a client. Returns False if not registered."""
        client = self._clients.get(mac_id)
        return client.mute if client else False

    def has_client(self, mac_id: str) -> bool:
        """Check if a client is registered in the volume state."""
        return mac_id in self._clients

    def is_client_available(self, mac_id: str, multiroom: bool = True) -> bool:
        """Whether a client plays now — the one rule the snapshot, the zones
        and the moves all count by.

        Multiroom: online in the registry (without one, a store built on its
        own, no client is). Direct: the local speaker alone, whatever the
        registry says — no snapclient runs, and the registry still holds the
        satellites online after a switch from multiroom, and every client
        offline after a boot in direct mode, the local one too.
        """
        if not multiroom:
            return mac_id == self._local_mac_id
        return self._registry is not None and self._registry.is_client_online(mac_id)

    @property
    def local_volume_db(self) -> float:
        """Current local volume in dB (direct mode).

        SSOT is _clients[_local_mac_id].volume_db. Returns DEFAULT_VOLUME_DB
        only at first-ever boot, before any persisted state exists and before
        the local client has registered with snapcast.
        """
        if self._local_mac_id and self._local_mac_id in self._clients:
            return self._clients[self._local_mac_id].volume_db
        return DEFAULT_VOLUME_DB

    @property
    def local_mac_id(self) -> Optional[str]:
        """Cached local client MAC ID (set when local client connects)."""
        return self._local_mac_id

    # ========== Zone Operations ==========

    def has_volume_control(self, mac_id: str) -> bool:
        """Check if a client has volume control (not a DAC with external amp)."""
        if not self._registry:
            return True
        client = self._registry.get_client(mac_id)
        return client.volume_control if client else True

    def _zone_client_ids(self, zone_id: str) -> Optional[List[str]]:
        """A zone's members as the registry holds them; None for an unknown zone."""
        zone = self._registry.get_zone(zone_id) if self._registry else None
        return None if zone is None else list(zone.client_ids)

    def zone_clients(self, zone_id: str) -> List[str]:
        """The zone's members the volume state holds a record for.

        Raises:
            ValueError: If zone not found
        """
        client_ids = self._zone_client_ids(zone_id)
        if client_ids is None:
            raise ValueError(f"Unknown zone: {zone_id}")
        return [client_id for client_id in client_ids if client_id in self._clients]

    def zone_members(self, zone_id: str) -> List[str]:
        """The zone's members a level moves: those with volume control.

        Raises:
            ValueError: If zone not found
        """
        return [c for c in self.zone_clients(zone_id) if self.has_volume_control(c)]

    def _zone_playing(self, zone_id: str, multiroom: bool) -> Dict[str, StoredLevel]:
        """The records of a zone's members that play now, by mac."""
        return {
            client_id: self._clients[client_id]
            for client_id in self._zone_client_ids(zone_id) or []
            if client_id in self._clients and self.is_client_available(client_id, multiroom)
        }

    def set_levels(self, levels: Dict[str, float]) -> None:
        """Write the levels of several known clients in one step.

        Synchronous, and that is the point: a volume move reads the levels,
        computes, and writes here with no await in between, so a second move
        can never read what the first has not written yet. The previous shape
        wrote after the hardware fan-out, and ten +2 dB steps in flight together
        landed as one.
        """
        for mac_id, volume_db in levels.items():
            client = self._clients.get(mac_id)
            if client is not None:
                client.volume_db = self._clamp_db(volume_db)
        if levels:
            self._schedule_persist()

    def client_ids(self) -> List[str]:
        """Every client the volume state holds a level for, reachable or not."""
        return list(self._clients)

    def zone_average_or_none(self, zone_id: str, multiroom: bool = True) -> Optional[float]:
        """Average level of a zone's playing members with volume control.

        None when there is none (or the zone is unknown): a level asked of such
        a zone has nothing to be measured against, and answering a default here
        would move every member off a number nobody set.
        """
        volumes = [
            client.volume_db for mac_id, client in self._zone_playing(zone_id, multiroom).items()
            if self.has_volume_control(mac_id)
        ]
        return sum(volumes) / len(volumes) if volumes else None

    def compute_zone_average(self, zone_id: str, multiroom: bool = True) -> float:
        """The zone's average, or DEFAULT_VOLUME_DB when no member counts."""
        average = self.zone_average_or_none(zone_id, multiroom)
        return DEFAULT_VOLUME_DB if average is None else average

    def _zone_all_muted(self, zone_id: str, multiroom: bool) -> bool:
        """Every playing member muted — a DAC member included: a zone mute
        mutes it too, since a mute is not a level."""
        playing = self._zone_playing(zone_id, multiroom)
        return bool(playing) and all(client.mute for client in playing.values())

    # ========== State Retrieval ==========

    async def get_complete_state(self, multiroom: bool) -> VolumeState:
        """
        Get complete volume state snapshot.

        Args:
            multiroom: The routing mode now — the caller's, read where the
                moves read it, so the figure published and the group a move
                acts on can never be of two different modes.

        Returns:
            VolumeState with all clients and zones
        """
        async with self._lock:
            # One entry per client, `available` and `volume_control` from the
            # registry: they are already the filters the averages below apply,
            # and publishing them means a reader can build the same set instead
            # of guessing which speakers Milo counted (the lock screen draws a
            # slider for exactly those).
            clients = {
                mac_id: ClientVolume(
                    volume_db=client.volume_db,
                    mute=client.mute,
                    available=self.is_client_available(mac_id, multiroom),
                    volume_control=self.has_volume_control(mac_id)
                )
                for mac_id, client in self._clients.items()
            }

            zones = self._registry.get_all_zones() if self._registry else {}
            zone_states = {
                zone_id: ZoneVolume(
                    id=zone_id,
                    name=zone.name,
                    client_ids=list(zone.client_ids),
                    average_volume_db=self.compute_zone_average(zone_id, multiroom),
                    all_muted=self._zone_all_muted(zone_id, multiroom)
                )
                for zone_id, zone in zones.items()
            }

            # Direct: the local speaker's own level. Multiroom: the average of
            # the online clients with volume control (a DAC is excluded).
            online = [c for c in clients.values() if c.available]
            if not multiroom:
                global_volume = self.local_volume_db
            else:
                all_volumes = [c.volume_db for c in online if c.volume_control]
                global_volume = sum(all_volumes) / len(all_volumes) if all_volumes else DEFAULT_VOLUME_DB

            global_mute = all(c.mute for c in online) if online else False

            # any_volume_control: True if at least one device manages volume via Milo
            if self._volume_control:
                any_vol_ctrl = True
            elif multiroom:
                any_vol_ctrl = any(c.volume_control for c in online)
            else:
                any_vol_ctrl = False

            # The span every normalized level in this snapshot is measured
            # over. In production the config is set before any read
            # (`VolumeService._load_volume_config` assigns it in a `finally`);
            # the fallback is for a store built without a service, and the
            # model's own docstring reserves its field defaults for exactly that.
            config = self._volume_config or VolumeConfig()

            return VolumeState(
                mode="multiroom" if multiroom else "direct",
                global_volume_db=global_volume,
                global_mute=global_mute,
                limit_min_db=config.limit_min_db,
                limit_max_db=config.limit_max_db,
                clients=clients,
                zones=zone_states,
                volume_control=self._volume_control,
                any_volume_control=any_vol_ctrl
            )

    # ========== Utilities ==========

    def _clamp_db(self, volume_db: float) -> float:
        """
        Clamp volume using VolumeConfig (user limits + technical hard limits).

        Delegates to VolumeConfig.clamp() which enforces both user-configurable
        limits and technical hard limits. Falls back to simple technical clamping
        if config is not yet set (safety during early init).

        Args:
            volume_db: Volume in dB

        Returns:
            Clamped volume within safe bounds
        """
        if self._volume_config:
            return self._volume_config.clamp(volume_db)
        # Fallback before config is set
        return max(MIN_VOLUME_DB, min(MAX_VOLUME_DB, volume_db))
