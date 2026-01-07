"""
VolumeStateStore - Single Source of Truth for Volume State

This service is the ONLY place where volume state is stored and mutated.
All volume operations must go through this store to ensure consistency.

Architecture: "Gros" VolumeStateStore (Option A)
- Integrates persistence, validation, and limits inline
- Minimal external dependencies (only SettingsService)
- Autonomous, testable, simple
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass

# Use existing domain models
from backend.domain.volume_state import VolumeState, ClientVolume, ZoneVolume


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
    - Persist state to disk
    - Thread-safe with async locks
    """

    # Volume limits (dB)
    MIN_DB = -80.0
    MAX_DB = 0.0
    DEFAULT_VOLUME_DB = -30.0

    # Persistence
    STORAGE_PATH = Path("/var/lib/milo/last_volume.json")
    MAX_AGE_DAYS = 7

    def __init__(self, settings_service):
        """
        Initialize VolumeStateStore.

        Args:
            settings_service: For reading dsp.linked_groups and routing.mode
        """
        self.logger = logging.getLogger(self.__class__.__name__)
        self.settings_service = settings_service

        # State storage
        self._clients: Dict[str, ClientVolume] = {}
        self._zones: Dict[str, ZoneConfig] = {}
        self._mode: str = "multiroom"  # 'direct' or 'multiroom'

        # Concurrency control
        self._lock = asyncio.Lock()

        self.logger.info("VolumeStateStore initialized (SSOT for volume)")

    # ========== Initialization ==========

    async def initialize(self) -> None:
        """
        Initialize store from settings and persistent storage.

        Must be called after construction.
        """
        async with self._lock:
            # Load routing mode
            self._mode = await self.settings_service.get_setting("routing.mode") or "multiroom"

            # Load zone configurations
            await self._load_zones()

            # Load persisted volume state
            await self._load_persisted_state()

            self.logger.info(f"VolumeStateStore initialized: mode={self._mode}, "
                           f"zones={len(self._zones)}, clients={len(self._clients)}")

    async def _load_zones(self) -> None:
        """Load zone configurations from settings."""
        linked_groups = await self.settings_service.get_setting("dsp.linked_groups") or []

        self._zones.clear()
        for group in linked_groups:
            zone_id = group.get("id")
            if zone_id:
                self._zones[zone_id] = ZoneConfig(
                    zone_id=zone_id,
                    name=group.get("name", zone_id),
                    client_ids=group.get("client_ids", [])
                )

        self.logger.debug(f"Loaded {len(self._zones)} zones from settings")

    async def _load_persisted_state(self) -> None:
        """Load persisted volume state from disk."""
        try:
            if not self.STORAGE_PATH.exists():
                self.logger.debug("No persisted volume state found")
                return

            with open(self.STORAGE_PATH, 'r') as f:
                data = json.load(f)

            # Validate age
            timestamp = data.get("timestamp")
            if timestamp:
                # Handle different timestamp formats
                if isinstance(timestamp, (int, float)):
                    # Unix timestamp
                    saved_time = datetime.fromtimestamp(timestamp, tz=timezone.utc)
                elif isinstance(timestamp, str):
                    # ISO format string
                    saved_time = datetime.fromisoformat(timestamp)
                    # Ensure timezone aware
                    if saved_time.tzinfo is None:
                        saved_time = saved_time.replace(tzinfo=timezone.utc)
                else:
                    self.logger.warning(f"Unknown timestamp format: {type(timestamp)}")
                    return

                age_days = (datetime.now(timezone.utc) - saved_time).days

                if age_days > self.MAX_AGE_DAYS:
                    self.logger.warning(f"Persisted volume state is {age_days} days old (max {self.MAX_AGE_DAYS}), ignoring")
                    return

            # Restore client volumes
            clients_data = data.get("clients", {})
            for hostname, client_data in clients_data.items():
                volume_db = client_data.get("volume_db", self.DEFAULT_VOLUME_DB)
                volume_db = self._clamp_db(volume_db)

                self._clients[hostname] = ClientVolume(
                    volume_db=volume_db,
                    offset_db=0.0,  # Offsets computed on demand
                    mute=client_data.get("mute", False),
                    available=False  # Availability set by snapcast events
                )

            self.logger.info(f"Restored volume state for {len(self._clients)} clients from disk")

        except Exception as e:
            self.logger.error(f"Error loading persisted volume state: {e}", exc_info=True)

    async def _persist_state(self) -> None:
        """Persist current volume state to disk."""
        try:
            # Prepare data
            data = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "clients": {
                    hostname: {
                        "volume_db": client.volume_db,
                        "mute": client.mute
                    }
                    for hostname, client in self._clients.items()
                }
            }

            # Atomic write (write to temp, then rename)
            temp_path = self.STORAGE_PATH.with_suffix(".tmp")
            self.STORAGE_PATH.parent.mkdir(parents=True, exist_ok=True)

            with open(temp_path, 'w') as f:
                json.dump(data, f, indent=2)

            temp_path.replace(self.STORAGE_PATH)

            self.logger.debug(f"Persisted volume state for {len(self._clients)} clients")

        except Exception as e:
            self.logger.error(f"Error persisting volume state: {e}", exc_info=True)

    # ========== Client Management ==========

    async def register_client(self, hostname: str, volume_db: Optional[float] = None,
                             mute: bool = False, available: bool = False) -> None:
        """
        Register or update a client.

        Args:
            hostname: Client hostname (e.g., 'local', 'milo-client-01')
            volume_db: Volume in dB (None = keep existing or use default)
            mute: Initial mute state
            available: Initial availability
        """
        async with self._lock:
            if hostname in self._clients:
                # Update availability and volume if provided
                self._clients[hostname].available = available
                if volume_db is not None:
                    self._clients[hostname].volume_db = self._clamp_db(volume_db)
                    await self._persist_state()
                self.logger.debug(f"Updated client: {hostname} → available={available}, volume_db={self._clients[hostname].volume_db:.1f}dB")
            else:
                # New client
                if volume_db is None:
                    volume_db = self.DEFAULT_VOLUME_DB

                volume_db = self._clamp_db(volume_db)

                self._clients[hostname] = ClientVolume(
                    volume_db=volume_db,
                    offset_db=0.0,  # Offsets computed on demand
                    mute=mute,
                    available=available
                )

                self.logger.info(f"Registered client: {hostname} at {volume_db:.1f}dB")

    async def set_client_availability(self, hostname: str, available: bool) -> None:
        """
        Update client availability status.

        Args:
            hostname: Client hostname
            available: New availability state
        """
        async with self._lock:
            if hostname in self._clients:
                self._clients[hostname].available = available
                self.logger.debug(f"Client availability: {hostname} → {available}")
            else:
                self.logger.warning(f"Cannot set availability for unknown client: {hostname}")

    async def set_client_mute(self, hostname: str, mute: bool) -> None:
        """
        Set client mute state.

        Args:
            hostname: Client hostname
            mute: New mute state
        """
        async with self._lock:
            if hostname in self._clients:
                self._clients[hostname].mute = mute
                await self._persist_state()
                self.logger.debug(f"Client mute: {hostname} → {mute}")
            else:
                self.logger.warning(f"Cannot mute unknown client: {hostname}")

    async def set_client_volume(self, hostname: str, volume_db: float) -> float:
        """
        Set individual client volume.

        Args:
            hostname: Client hostname
            volume_db: New volume in dB

        Returns:
            Clamped volume that was actually set
        """
        async with self._lock:
            volume_db = self._clamp_db(volume_db)

            if hostname in self._clients:
                self._clients[hostname].volume_db = volume_db
                await self._persist_state()
                self.logger.debug(f"Client volume: {hostname} → {volume_db:.1f}dB")
            else:
                # Auto-register client if not exists
                await self.register_client(hostname, volume_db=volume_db, available=True)

            return volume_db

    def get_client_volume(self, hostname: str) -> Optional[float]:
        """Get persisted volume for a client, or None if not registered."""
        if hostname in self._clients:
            return self._clients[hostname].volume_db
        return None

    # ========== Zone Operations ==========

    async def apply_zone_delta(self, zone_id: str, delta_db: float) -> Dict[str, float]:
        """
        Calculate volume updates for all clients in a zone.

        This is ATOMIC: calculates delta and returns all updates at once.
        Caller must apply these to hardware in parallel.

        Args:
            zone_id: Zone identifier
            delta_db: Volume change in dB

        Returns:
            Dict mapping hostname → new_volume_db for all available clients

        Raises:
            ValueError: If zone not found
        """
        async with self._lock:
            if zone_id not in self._zones:
                raise ValueError(f"Unknown zone: {zone_id}")

            zone = self._zones[zone_id]
            updates = {}

            # Apply delta to each available client
            for client_id in zone.client_ids:
                if client_id in self._clients:
                    client = self._clients[client_id]

                    # Only update available, unmuted clients
                    if client.available and not client.mute:
                        new_volume = self._clamp_db(client.volume_db + delta_db)
                        updates[client_id] = new_volume

            self.logger.debug(f"Zone delta: {zone_id} Δ{delta_db:+.1f}dB → {len(updates)} clients")
            return updates

    async def apply_zone_updates(self, updates: Dict[str, float]) -> None:
        """
        Apply volume updates after hardware changes succeed.

        Args:
            updates: Dict mapping hostname → volume_db
        """
        async with self._lock:
            for hostname, volume_db in updates.items():
                if hostname in self._clients:
                    self._clients[hostname].volume_db = volume_db

            await self._persist_state()
            self.logger.debug(f"Applied {len(updates)} volume updates")

    def compute_zone_average(self, zone_id: str) -> float:
        """
        Compute average volume for a zone.

        Uses a two-tier approach:
        1. If any clients are unmuted, average ONLY those (exclude muted clients)
        2. If ALL clients are muted, average ALL available clients (show real volume)
        3. If no clients available, return default volume

        Args:
            zone_id: Zone identifier

        Returns:
            Average volume in dB (or DEFAULT_VOLUME_DB if no valid clients)
        """
        if zone_id not in self._zones:
            self.logger.warning(f"Cannot compute average for unknown zone: {zone_id}")
            return self.DEFAULT_VOLUME_DB

        zone = self._zones[zone_id]

        # Collect volumes in two categories
        unmuted_volumes = []
        all_volumes = []

        for client_id in zone.client_ids:
            if client_id in self._clients:
                client = self._clients[client_id]

                if client.available:
                    all_volumes.append(client.volume_db)

                    # Collect unmuted clients separately
                    if not client.mute:
                        unmuted_volumes.append(client.volume_db)

        # First tier: If there are unmuted clients, use only those
        if unmuted_volumes:
            average = sum(unmuted_volumes) / len(unmuted_volumes)
            self.logger.debug(f"Zone {zone_id} average: {average:.1f}dB from {len(unmuted_volumes)} unmuted clients")
            return average

        # Second tier: All clients are muted, use all available to show real volume
        if all_volumes:
            average = sum(all_volumes) / len(all_volumes)
            self.logger.debug(f"Zone {zone_id} average (all muted): {average:.1f}dB from {len(all_volumes)} clients")
            return average

        # Final fallback: No available clients at all
        self.logger.debug(f"Zone {zone_id} has no available clients, returning default {self.DEFAULT_VOLUME_DB}dB")
        return self.DEFAULT_VOLUME_DB

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
            for hostname, client in self._clients.items():
                # Find which zone this client belongs to
                zone_avg = None
                for zone_id, zone_config in self._zones.items():
                    if hostname in zone_config.client_ids:
                        zone_avg = self.compute_zone_average(zone_id)
                        break

                # Calculate offset
                if zone_avg is not None and client.available and not client.mute:
                    offset = client.volume_db - zone_avg
                else:
                    offset = 0.0

                # Create client with computed offset
                clients_with_offsets[hostname] = ClientVolume(
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

            # Calculate global volume (average of all available, unmuted clients)
            all_volumes = [
                client.volume_db
                for client in self._clients.values()
                if client.available and not client.mute
            ]
            global_volume = sum(all_volumes) / len(all_volumes) if all_volumes else self.DEFAULT_VOLUME_DB

            # Calculate display volume (same as global for multiroom, or local for direct)
            display_volume = global_volume

            # Check if all available clients are muted
            available_clients = [c for c in self._clients.values() if c.available]
            global_mute = all(c.mute for c in available_clients) if available_clients else False

            return VolumeState(
                mode=self._mode,
                global_volume_db=global_volume,
                global_mute=global_mute,
                display_volume_db=display_volume,
                clients=clients_with_offsets,
                zones=zone_states
            )

    def _zone_all_muted(self, zone_id: str) -> bool:
        """Check if all available clients in a zone are muted."""
        if zone_id not in self._zones:
            return False

        zone = self._zones[zone_id]
        available_clients = [
            self._clients[cid]
            for cid in zone.client_ids
            if cid in self._clients and self._clients[cid].available
        ]

        if not available_clients:
            return False

        return all(client.mute for client in available_clients)

    # ========== Utilities ==========

    def _clamp_db(self, volume_db: float) -> float:
        """
        Clamp volume to valid range.

        Args:
            volume_db: Volume in dB

        Returns:
            Clamped volume
        """
        return max(self.MIN_DB, min(self.MAX_DB, volume_db))

    async def get_volume_limits(self) -> Dict[str, float]:
        """
        Get volume limits from settings.

        Returns:
            Dict with min_db, max_db, step_mobile_db, step_button_db
        """
        min_db = await self.settings_service.get_setting("volume.min_db") or self.MIN_DB
        max_db = await self.settings_service.get_setting("volume.max_db") or self.MAX_DB
        step_mobile_db = await self.settings_service.get_setting("volume.step_mobile_db") or 3.0
        step_button_db = await self.settings_service.get_setting("volume.step_button_db") or 5.0

        return {
            "min_db": min_db,
            "max_db": max_db,
            "step_mobile_db": step_mobile_db,
            "step_button_db": step_button_db
        }
