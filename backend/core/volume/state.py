# backend/core/volume/state.py
"""
VolumeStateStore - Single Source of Truth for Volume State

This service is the ONLY place where volume state is stored and mutated.
All volume operations must go through this store to ensure consistency.

Architecture: "Gros" VolumeStateStore (Option A)
- Integrates persistence, validation, and limits inline
- Minimal external dependencies (only SettingsService)
- Autonomous, testable, simple

CONSOLIDATED: Includes all persistence logic (formerly VolumeStorageService)

Integration with ClientRegistryService:
- Subscribes to registry availability events
- Keeps volume/mute state locally, syncs availability from registry
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
class ZoneConfig:
    """Configuration for a multiroom zone (internal)."""
    zone_id: str
    name: str
    client_ids: List[str]


class VolumeStateStore:
    """
    Single Source of Truth for all volume state.

    Responsibilities:
    - Track client volumes, mutes, availability
    - Track zone configurations
    - Calculate zone averages (excluding muted/unavailable)
    - Calculate offsets on demand
    - Validate volume limits
    - Persist state to disk (CONSOLIDATED - no separate storage service)
    - Thread-safe with async locks
    """

    # Volume constants imported from backend.config.constants:
    # - DEFAULT_VOLUME_DB = -60.0 (default volume for new clients)
    # - MIN_VOLUME_DB = -80.0 (technical minimum)
    # - MAX_VOLUME_DB = 0.0 (technical maximum)

    # Persistence
    STORAGE_PATH = Path("/var/lib/milo/last_volume.json")

    def __init__(self, settings_service):
        """
        Initialize VolumeStateStore.

        Args:
            settings_service: For reading routing.mode and volume limits
        """
        self.logger = logging.getLogger(self.__class__.__name__)
        self.settings_service = settings_service

        # Client registry reference (set via set_registry after construction)
        self._registry: Optional["ClientRegistryService"] = None

        # State storage
        self._clients: Dict[str, ClientVolume] = {}
        self._zones: Dict[str, ZoneConfig] = {}
        self._mode: str = "multiroom"  # 'direct' or 'multiroom'

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

        if event_type == RegistryEventType.CLIENT_CONNECTED:
            # Handle client connected - register if new or update availability
            mac_id = data.get("mac_id")
            client_data = data.get("client", {})
            if mac_id:
                is_local = client_data.get("ip") == "127.0.0.1"

                if is_local:
                    self._local_mac_id = mac_id

                if mac_id not in self._clients:
                    await self.register_client(
                        mac_id,
                        volume_db=None,  # Use default
                        available=True
                    )
                else:
                    await self.set_client_availability(mac_id, True)

        elif event_type == RegistryEventType.CLIENT_DISCONNECTED:
            # Handle client disconnected - check if deleted or just offline
            mac_id = data.get("mac_id")
            if mac_id and mac_id in self._clients:
                # Check if client was deleted from registry (vs just went offline)
                client_still_exists = self._registry and self._registry.get_client(mac_id) is not None

                if client_still_exists:
                    # Client just went offline temporarily - keep volume state
                    await self.set_client_availability(mac_id, False)
                else:
                    # Client was deleted from registry - remove from volume state
                    async with self._lock:
                        del self._clients[mac_id]
                        self._schedule_persist()
                    self.logger.info(f"Deleted client {mac_id} from volume state")

        elif event_type == RegistryEventType.CLIENT_UPDATED:
            mac_id = data.get("mac_id")
            client_data = data.get("client", {})
            if mac_id and mac_id not in self._clients:
                await self.register_client(
                    mac_id,
                    volume_db=None,  # Use default
                    available=client_data.get("online", True)
                )

        elif event_type == RegistryEventType.ZONE_CREATED:
            zone_data = data.get("zone", {})
            zone_id = data.get("zone_id")
            if zone_id and zone_data:
                async with self._lock:
                    self._zones[zone_id] = ZoneConfig(
                        zone_id=zone_id,
                        name=zone_data.get("name", zone_id),
                        client_ids=zone_data.get("client_ids", [])
                    )
                self.logger.info(f"Zone {zone_id} added to volume state")

        elif event_type == RegistryEventType.ZONE_UPDATED:
            zone_data = data.get("zone", {})
            zone_id = data.get("zone_id")
            if zone_id and zone_data:
                async with self._lock:
                    self._zones[zone_id] = ZoneConfig(
                        zone_id=zone_id,
                        name=zone_data.get("name", zone_id),
                        client_ids=zone_data.get("client_ids", [])
                    )
                self.logger.debug(f"Zone {zone_id} updated in volume state")

        elif event_type == RegistryEventType.ZONE_DELETED:
            zone_id = data.get("zone_id")
            if zone_id:
                async with self._lock:
                    self._zones.pop(zone_id, None)
                self.logger.info(f"Zone {zone_id} removed from volume state")

        elif event_type == RegistryEventType.ZONE_CLIENT_ADDED:
            zone_id = data.get("zone_id")
            camilladsp_id = data.get("camilladsp_id")
            if zone_id and camilladsp_id and zone_id in self._zones:
                async with self._lock:
                    if camilladsp_id not in self._zones[zone_id].client_ids:
                        self._zones[zone_id].client_ids.append(camilladsp_id)
                self.logger.debug(f"Client {camilladsp_id} added to zone {zone_id} in volume state")

        elif event_type == RegistryEventType.ZONE_CLIENT_REMOVED:
            zone_id = data.get("zone_id")
            camilladsp_id = data.get("camilladsp_id")
            if zone_id and camilladsp_id and zone_id in self._zones:
                async with self._lock:
                    if camilladsp_id in self._zones[zone_id].client_ids:
                        self._zones[zone_id].client_ids.remove(camilladsp_id)
                self.logger.debug(f"Client {camilladsp_id} removed from zone {zone_id} in volume state")


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
            # Load routing mode from multiroom_enabled setting
            multiroom_enabled = await self.settings_service.get_setting("routing.multiroom_enabled")
            self._mode = "multiroom" if multiroom_enabled else "direct"

            # Load zone configurations
            await self._load_zones()

            # Load persisted volume state
            await self._load_persisted_state()

            limits_info = (f"{self._volume_config.limit_min_db:.1f}/{self._volume_config.limit_max_db:.1f}"
                          if self._volume_config else "not set")
            self.logger.info(f"VolumeStateStore initialized: mode={self._mode}, "
                           f"zones={len(self._zones)}, clients={len(self._clients)}, "
                           f"local_volume={self.local_volume_db:.1f}dB, "
                           f"limits={limits_info}dB")

    async def set_mode(self, mode: str) -> None:
        """
        Update volume mode at runtime (called when multiroom is toggled).

        Args:
            mode: 'direct' or 'multiroom'
        """
        async with self._lock:
            if mode not in ("direct", "multiroom"):
                self.logger.warning(f"Invalid volume mode: {mode}, ignoring")
                return
            old_mode = self._mode
            self._mode = mode
            self.logger.info(f"Volume mode changed: {old_mode} -> {mode}")

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
            self._clients[local_mac_id] = ClientVolume(
                volume_db=volume_db,
                offset_db=0.0,
                mute=False,
                available=True,
            )
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
            self._clients[mac_id] = ClientVolume(
                volume_db=self._clamp_db(volume_db),
                offset_db=0.0,
                mute=False,
                available=True,
            )
        self.logger.info(f"Seeded local client {mac_id} at {self.local_volume_db:.1f}dB")

    async def _load_zones(self) -> None:
        """Load zone configurations from registry."""
        self._zones.clear()

        if self._registry:
            # Load zones from registry (single source of truth)
            zones = self._registry.get_all_zones()
            for zone_id, zone in zones.items():
                self._zones[zone_id] = ZoneConfig(
                    zone_id=zone_id,
                    name=zone.name,
                    client_ids=zone.client_ids.copy()
                )
            self.logger.debug(f"Loaded {len(self._zones)} zones from registry")
        else:
            self.logger.warning("Registry not available, zones not loaded")

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

            # Restore client volumes
            clients_data = data.get("clients", {})
            for mac_id, client_data in clients_data.items():
                volume_db = client_data.get("volume_db", DEFAULT_VOLUME_DB)
                volume_db = self._clamp_db(volume_db)

                self._clients[mac_id] = ClientVolume(
                    volume_db=volume_db,
                    offset_db=0.0,  # Offsets computed on demand
                    mute=client_data.get("mute", False),
                    available=False  # Availability set by snapcast events
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

    async def register_client(self, mac_id: str, volume_db: Optional[float] = None,
                             mute: bool = False, available: bool = False) -> None:
        """
        Register or update a client.

        Args:
            mac_id: Client MAC identifier
            volume_db: Volume in dB (None = keep existing or use default)
            mute: Initial mute state
            available: Initial availability
        """
        async with self._lock:
            if mac_id in self._clients:
                # Update availability and volume if provided
                self._clients[mac_id].available = available
                if volume_db is not None:
                    self._clients[mac_id].volume_db = self._clamp_db(volume_db)
                    self._schedule_persist()
                self.logger.debug(f"Updated client: {mac_id} -> available={available}, volume_db={self._clients[mac_id].volume_db:.1f}dB")
            else:
                if volume_db is None:
                    volume_db = DEFAULT_VOLUME_DB

                volume_db = self._clamp_db(volume_db)

                self._clients[mac_id] = ClientVolume(
                    volume_db=volume_db,
                    offset_db=0.0,  # Offsets computed on demand
                    mute=mute,
                    available=available
                )

                self.logger.info(f"Registered client: {mac_id} at {volume_db:.1f}dB")

    async def set_client_availability(self, mac_id: str, available: bool) -> None:
        """
        Update client availability status.

        Args:
            mac_id: Client MAC identifier
            available: New availability state
        """
        async with self._lock:
            if mac_id in self._clients:
                self._clients[mac_id].available = available
                self.logger.debug(f"Client availability: {mac_id} -> {available}")
            else:
                self.logger.warning(f"Cannot set availability for unknown client: {mac_id}")

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
                self._clients[mac_id] = ClientVolume(
                    volume_db=volume_db,
                    offset_db=0.0,
                    mute=False,
                    available=True
                )
                self.logger.info(f"Auto-registered client: {mac_id} at {volume_db:.1f}dB")

        # Sync to ClientRegistry for reconnection context (FR7)
        if self._registry:
            await self._registry.update_volume(mac_id, volume_db=volume_db)

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

    async def apply_zone_delta(self, zone_id: str, delta_db: float) -> Dict[str, float]:
        """
        Calculate volume updates for all clients in a zone.

        This is ATOMIC: calculates delta and returns all updates at once.
        Caller must apply these to hardware in parallel.

        Args:
            zone_id: Zone identifier
            delta_db: Volume change in dB

        Returns:
            Dict mapping mac_id -> new_volume_db for all available clients

        Raises:
            ValueError: If zone not found
        """
        async with self._lock:
            if zone_id not in self._zones:
                raise ValueError(f"Unknown zone: {zone_id}")

            zone = self._zones[zone_id]
            updates = {}

            # Apply delta to each available client with volume control (skip DAC clients)
            for client_id in zone.client_ids:
                if client_id in self._clients:
                    client = self._clients[client_id]

                    if client.available and self.has_volume_control(client_id):
                        new_volume = self._clamp_db(client.volume_db + delta_db)
                        updates[client_id] = new_volume

            self.logger.debug(f"Zone delta: {zone_id} +{delta_db:+.1f}dB -> {len(updates)} clients")
            return updates

    async def apply_zone_updates(self, updates: Dict[str, float]) -> None:
        """
        Apply volume updates after hardware changes succeed.

        Args:
            updates: Dict mapping mac_id -> volume_db
        """
        async with self._lock:
            for mac_id, volume_db in updates.items():
                if mac_id in self._clients:
                    self._clients[mac_id].volume_db = volume_db

            self._schedule_persist()
            self.logger.debug(f"Applied {len(updates)} volume updates")

        # Sync to ClientRegistry for reconnection context (FR7)
        if self._registry:
            for mac_id, volume_db in updates.items():
                await self._registry.update_volume(mac_id, volume_db=volume_db)

    def compute_zone_average(self, zone_id: str) -> float:
        """
        Compute average volume for a zone (all available clients).

        Args:
            zone_id: Zone identifier

        Returns:
            Average volume in dB (or DEFAULT_VOLUME if no available clients)
        """
        if zone_id not in self._zones:
            self.logger.warning(f"Cannot compute average for unknown zone: {zone_id}")
            return DEFAULT_VOLUME_DB

        zone = self._zones[zone_id]
        volumes = []

        for client_id in zone.client_ids:
            if client_id in self._clients:
                client = self._clients[client_id]
                if client.available and self.has_volume_control(client_id):
                    volumes.append(client.volume_db)

        if volumes:
            average = sum(volumes) / len(volumes)
            self.logger.debug(f"Zone {zone_id} average: {average:.1f}dB from {len(volumes)} clients")
            return average

        self.logger.debug(f"Zone {zone_id} has no available clients, returning default {DEFAULT_VOLUME_DB}dB")
        return DEFAULT_VOLUME_DB

    # ========== State Retrieval ==========

    async def get_complete_state(self) -> VolumeState:
        """
        Get complete volume state snapshot.

        Returns:
            VolumeState with all clients and zones
        """
        async with self._lock:
            # Refresh zones from settings (in case they changed)
            await self._load_zones()

            # Compute offsets for clients (offset = client_volume - zone_average)
            clients_with_offsets = {}
            for mac_id, client in self._clients.items():
                # Find which zone this client belongs to
                zone_avg = None
                for zone_id, zone_config in self._zones.items():
                    if mac_id in zone_config.client_ids:
                        zone_avg = self.compute_zone_average(zone_id)
                        break

                # Calculate offset
                if zone_avg is not None and client.available:
                    offset = client.volume_db - zone_avg
                else:
                    offset = 0.0

                # Create client with computed offset
                clients_with_offsets[mac_id] = ClientVolume(
                    volume_db=client.volume_db,
                    offset_db=offset,
                    mute=client.mute,
                    available=client.available
                )

            # Compute zone states
            zone_states = {}
            for zone_id, zone_config in self._zones.items():
                zone_states[zone_id] = ZoneVolume(
                    id=zone_id,
                    name=zone_config.name,
                    client_ids=zone_config.client_ids,
                    average_volume_db=self.compute_zone_average(zone_id),
                    all_muted=self._zone_all_muted(zone_id)
                )

            # Calculate global volume (mode-aware)
            # - Direct mode: use local client's volume
            # - Multiroom mode: average of all available, unmuted clients
            if self._mode == "direct":
                global_volume = self.local_volume_db
            else:
                # Multiroom: average of all available clients with volume control (exclude DAC)
                all_volumes = [
                    client.volume_db
                    for mac_id, client in self._clients.items()
                    if client.available and self.has_volume_control(mac_id)
                ]
                global_volume = sum(all_volumes) / len(all_volumes) if all_volumes else DEFAULT_VOLUME_DB

            # Check if all available clients are muted
            available_clients = [c for c in self._clients.values() if c.available]
            global_mute = all(c.mute for c in available_clients) if available_clients else False

            # any_volume_control: True if at least one device manages volume via Milo
            if self._volume_control:
                any_vol_ctrl = True
            elif self._mode == "multiroom":
                any_vol_ctrl = any(
                    self.has_volume_control(mac_id)
                    for mac_id, client in self._clients.items()
                    if client.available
                )
            else:
                any_vol_ctrl = False

            return VolumeState(
                mode=self._mode,
                global_volume_db=global_volume,
                global_mute=global_mute,
                clients=clients_with_offsets,
                zones=zone_states,
                volume_control=self._volume_control,
                any_volume_control=any_vol_ctrl
            )

    def _zone_all_muted(self, zone_id: str) -> bool:
        """Check if all available clients with volume control in a zone are muted."""
        if zone_id not in self._zones:
            return False

        zone = self._zones[zone_id]
        controllable_clients = [
            self._clients[cid]
            for cid in zone.client_ids
            if cid in self._clients and self._clients[cid].available
            and self.has_volume_control(cid)
        ]

        if not controllable_clients:
            return False

        return all(client.mute for client in controllable_clients)

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
