# backend/core/multiroom/registry.py
"""
ClientRegistryService - Single Source of Truth for client/zone/availability management.

All services that need client information MUST query this service.
This service is the ONLY place where client state is mutated.
"""
import asyncio
import logging
from typing import Dict, List, Optional, Callable, Awaitable, Any
from datetime import datetime

from backend.core.events import EventBus, get_event_bus
from backend.core.multiroom.models import (
    RegisteredClient,
    Zone,
    RegistryState,
    RegistryEventType,
    SpeakerType,
    DEFAULT_SPEAKER_TYPE,
    DEFAULT_CROSSOVER_FREQUENCY
)


class ClientRegistryService:
    """
    Central registry for all multiroom clients and zones.

    Responsibilities:
    - Track all clients with complete metadata
    - Manage zone configuration
    - Track availability (single source)
    - Emit events on state changes
    - Persist configuration to settings
    - Validate operations before execution
    """

    def __init__(self, settings_service=None, event_bus: EventBus = None):
        self.logger = logging.getLogger(__name__)
        self._settings_service = settings_service
        self._state_machine = None
        self.event_bus = event_bus or get_event_bus()

        # Core state - protected by lock
        self._clients: Dict[str, RegisteredClient] = {}
        self._zones: Dict[str, Zone] = {}
        self._lock = asyncio.Lock()

        # Subscriber callbacks for local event handling
        self._subscribers: List[Callable[[str, Dict], Awaitable[None]]] = []

        self._initialized = False

    def set_state_machine(self, state_machine) -> None:
        """Set state machine for event broadcasting."""
        self._state_machine = state_machine

    async def initialize(self) -> bool:
        """Load persisted state from settings."""
        try:
            self.logger.info("Initializing ClientRegistryService...")

            if self._settings_service:
                # Load zones (linked groups) from settings
                zones_data = await self._settings_service.get_setting("multiroom.linked_groups")
                if zones_data:
                    for zone_data in zones_data:
                        zone = Zone.from_dict(zone_data)
                        self._zones[zone.id] = zone
                    self.logger.info(f"Loaded {len(self._zones)} zones from settings")

                # Load client types from settings
                client_types = await self._settings_service.get_setting("multiroom.client_types")
                if client_types:
                    # Store for later when clients register
                    self._persisted_client_types = client_types
                    self.logger.info(f"Loaded {len(client_types)} client type configurations")
                else:
                    self._persisted_client_types = {}

            self._initialized = True
            self.logger.info("ClientRegistryService initialized successfully")
            return True

        except Exception as e:
            self.logger.error(f"Failed to initialize ClientRegistryService: {e}")
            return False

    # === CLIENT MANAGEMENT ===

    async def register_client(self, client_data: Dict[str, Any]) -> RegisteredClient:
        """
        Register or update a client. Returns the client object.

        Args:
            client_data: Dictionary with client information:
                - dsp_id: Primary identifier (required)
                - snapcast_id: Snapcast's internal ID
                - name: Display name
                - host: Hostname
                - ip: IP address
                - available: Connection status (default: True)
                - volume_db: Current volume
                - mute: Mute status

        Returns:
            The registered or updated client
        """
        dsp_id = client_data.get("dsp_id")
        if not dsp_id:
            raise ValueError("dsp_id is required")

        async with self._lock:
            existing = self._clients.get(dsp_id)

            if existing:
                # Update existing client
                for key, value in client_data.items():
                    if hasattr(existing, key) and value is not None:
                        setattr(existing, key, value)
                existing.last_seen = datetime.utcnow()
                client = existing
                event_type = RegistryEventType.CLIENT_UPDATED
            else:
                # Create new client
                # Apply persisted speaker type if available
                speaker_type = DEFAULT_SPEAKER_TYPE
                crossover_freq = DEFAULT_CROSSOVER_FREQUENCY
                if hasattr(self, '_persisted_client_types'):
                    type_config = self._persisted_client_types.get(dsp_id, {})
                    speaker_type = type_config.get("type", DEFAULT_SPEAKER_TYPE)
                    crossover_freq = type_config.get("crossover", DEFAULT_CROSSOVER_FREQUENCY)

                client = RegisteredClient(
                    dsp_id=dsp_id,
                    snapcast_id=client_data.get("snapcast_id", ""),
                    name=client_data.get("name", dsp_id),
                    host=client_data.get("host", ""),
                    ip=client_data.get("ip", ""),
                    available=client_data.get("available", True),
                    speaker_type=speaker_type,
                    crossover_frequency=crossover_freq,
                    volume_db=client_data.get("volume_db", -30.0),
                    mute=client_data.get("mute", False)
                )
                self._clients[dsp_id] = client
                event_type = RegistryEventType.CLIENT_REGISTERED

            self.logger.info(f"Client {event_type}: {dsp_id} (available={client.available})")

        # Emit event outside lock
        await self._emit_event(event_type, {
            "dsp_id": dsp_id,
            "client": client.to_dict()
        })

        return client

    async def unregister_client(self, dsp_id: str) -> bool:
        """
        Remove a client from the registry.
        Cleans up all persisted data (zones, speaker type) so the client
        is treated as new on reconnect.

        Args:
            dsp_id: The client's dsp_id

        Returns:
            True if client was removed, False if not found
        """
        zones_modified = []  # List of (zone_id, zone_dict) for updated zones
        zones_deleted = []   # List of (zone_id, zone_dict) for deleted zones

        async with self._lock:
            if dsp_id not in self._clients:
                return False

            del self._clients[dsp_id]

            # Remove from any zones and delete invalid zones (less than 2 clients)
            zones_to_delete = []
            for zone_id, zone in self._zones.items():
                if dsp_id in zone.client_ids:
                    zone.client_ids.remove(dsp_id)
                    # Mark zone for deletion if less than 2 clients remain
                    if len(zone.client_ids) < 2:
                        zones_to_delete.append((zone_id, zone.to_dict()))
                    else:
                        # Zone still valid, capture updated state for event
                        zones_modified.append((zone_id, zone.to_dict()))

            # Delete invalid zones
            for zone_id, zone_dict in zones_to_delete:
                del self._zones[zone_id]
                zones_deleted.append((zone_id, zone_dict))
                self.logger.info(f"Zone {zone_id} deleted (less than 2 clients remaining)")

            # Clean up persisted client types
            if hasattr(self, '_persisted_client_types') and dsp_id in self._persisted_client_types:
                del self._persisted_client_types[dsp_id]

            self.logger.info(f"Client unregistered: {dsp_id}")

        # Persist changes outside lock to avoid deadlock
        if zones_modified or zones_deleted:
            await self._persist_zones()
        await self._persist_client_types()

        # Emit zone events BEFORE client unregistered so frontend updates zones first
        for zone_id, zone_dict in zones_modified:
            await self._emit_event(RegistryEventType.ZONE_UPDATED, {
                "zone_id": zone_id,
                "zone": zone_dict
            })

        for zone_id, zone_dict in zones_deleted:
            await self._emit_event(RegistryEventType.ZONE_DELETED, {
                "zone_id": zone_id,
                "zone": zone_dict
            })

        await self._emit_event(RegistryEventType.CLIENT_UNREGISTERED, {
            "dsp_id": dsp_id
        })

        return True

    async def update_availability(self, dsp_id: str, available: bool) -> None:
        """
        Update client availability - emits event if changed.

        Args:
            dsp_id: The client's dsp_id
            available: New availability status
        """
        async with self._lock:
            client = self._clients.get(dsp_id)
            if not client:
                self.logger.warning(f"Cannot update availability: client {dsp_id} not found")
                return

            if client.available == available:
                return  # No change

            client.available = available
            client.last_seen = datetime.utcnow()
            client_dict = client.to_dict()

        self.logger.info(f"Client {dsp_id} availability changed: {available}")

        await self._emit_event(RegistryEventType.AVAILABILITY_CHANGED, {
            "dsp_id": dsp_id,
            "available": available,
            "client": client_dict
        })

    async def update_volume(self, dsp_id: str, volume_db: float, mute: Optional[bool] = None) -> None:
        """
        Update client volume state.

        Args:
            dsp_id: The client's dsp_id
            volume_db: New volume in dB
            mute: New mute status (optional)
        """
        async with self._lock:
            client = self._clients.get(dsp_id)
            if not client:
                return

            client.volume_db = volume_db
            if mute is not None:
                client.mute = mute
            client.last_seen = datetime.utcnow()

        await self._emit_event(RegistryEventType.VOLUME_CHANGED, {
            "dsp_id": dsp_id,
            "volume_db": volume_db,
            "mute": mute if mute is not None else client.mute
        })

    async def update_speaker_type(
        self,
        dsp_id: str,
        speaker_type: SpeakerType,
        crossover_freq: Optional[int] = None
    ) -> None:
        """
        Update client speaker type.

        Args:
            dsp_id: The client's dsp_id
            speaker_type: New speaker type
            crossover_freq: New crossover frequency (optional)
        """
        async with self._lock:
            client = self._clients.get(dsp_id)
            if not client:
                self.logger.warning(f"Cannot update speaker type: client {dsp_id} not found")
                return

            client.speaker_type = speaker_type
            if crossover_freq is not None:
                client.crossover_frequency = crossover_freq

        # Persist to settings
        await self._persist_client_types()

        await self._emit_event(RegistryEventType.SPEAKER_TYPE_CHANGED, {
            "dsp_id": dsp_id,
            "speaker_type": speaker_type,
            "crossover_frequency": crossover_freq or client.crossover_frequency
        })

    # === CLIENT QUERIES ===

    def get_client(self, dsp_id: str) -> Optional[RegisteredClient]:
        """Get a client by dsp_id."""
        return self._clients.get(dsp_id)

    def get_all_clients(self) -> Dict[str, RegisteredClient]:
        """Get all registered clients."""
        return self._clients.copy()

    def get_available_clients(self) -> List[RegisteredClient]:
        """Get only available clients."""
        return [c for c in self._clients.values() if c.available]

    def is_client_available(self, dsp_id: str) -> bool:
        """Check if a specific client is available."""
        client = self._clients.get(dsp_id)
        return client.available if client else False

    def get_client_ids(self) -> List[str]:
        """Get list of all client dsp_ids."""
        return list(self._clients.keys())

    def get_available_client_ids(self) -> List[str]:
        """Get list of available client dsp_ids."""
        return [c.dsp_id for c in self._clients.values() if c.available]

    # === ZONE MANAGEMENT ===

    async def create_zone(self, zone_id: str, name: str, client_ids: List[str]) -> Zone:
        """
        Create a new zone. Validates all client_ids exist.

        Args:
            zone_id: Unique zone identifier
            name: Display name
            client_ids: List of dsp_ids to include

        Returns:
            The created zone
        """
        async with self._lock:
            if zone_id in self._zones:
                raise ValueError(f"Zone {zone_id} already exists")

            # Validate all clients exist
            for cid in client_ids:
                if cid not in self._clients:
                    raise ValueError(f"Client {cid} not found")

            zone = Zone(id=zone_id, name=name, client_ids=client_ids.copy())
            self._zones[zone_id] = zone

        # Persist to settings
        await self._persist_zones()

        await self._emit_event(RegistryEventType.ZONE_CREATED, {
            "zone_id": zone_id,
            "zone": zone.to_dict()
        })

        self.logger.info(f"Zone created: {zone_id} with clients {client_ids}")
        return zone

    async def delete_zone(self, zone_id: str) -> bool:
        """
        Delete a zone.

        Args:
            zone_id: The zone's ID

        Returns:
            True if zone was deleted, False if not found
        """
        async with self._lock:
            if zone_id not in self._zones:
                return False

            # Capture zone data BEFORE deletion for event
            zone_dict = self._zones[zone_id].to_dict()
            del self._zones[zone_id]

        # Persist to settings
        await self._persist_zones()

        # Include zone data in event so CrossoverService can disable filters
        await self._emit_event(RegistryEventType.ZONE_DELETED, {
            "zone_id": zone_id,
            "zone": zone_dict
        })

        self.logger.info(f"Zone deleted: {zone_id}")
        return True

    async def update_zone(self, zone_id: str, **kwargs) -> Optional[Zone]:
        """
        Update zone properties.

        Args:
            zone_id: The zone's ID
            **kwargs: Properties to update (name, crossover_frequency, crossover_enabled)

        Returns:
            The updated zone or None if not found
        """
        async with self._lock:
            zone = self._zones.get(zone_id)
            if not zone:
                return None

            for key, value in kwargs.items():
                if hasattr(zone, key) and key not in ('id', 'client_ids'):
                    setattr(zone, key, value)

            zone_dict = zone.to_dict()

        # Persist to settings
        await self._persist_zones()

        await self._emit_event(RegistryEventType.ZONE_UPDATED, {
            "zone_id": zone_id,
            "zone": zone_dict
        })

        return zone

    async def add_client_to_zone(self, zone_id: str, dsp_id: str) -> bool:
        """
        Add a client to a zone. Validates client exists.

        Args:
            zone_id: The zone's ID
            dsp_id: The client's dsp_id

        Returns:
            True if client was added, False if zone not found or client already in zone
        """
        async with self._lock:
            zone = self._zones.get(zone_id)
            if not zone:
                self.logger.warning(f"Cannot add client: zone {zone_id} not found")
                return False

            if dsp_id not in self._clients:
                self.logger.warning(f"Cannot add client: {dsp_id} not found")
                return False

            if dsp_id in zone.client_ids:
                return False  # Already in zone

            zone.client_ids.append(dsp_id)

        # Persist to settings
        await self._persist_zones()

        await self._emit_event(RegistryEventType.ZONE_CLIENT_ADDED, {
            "zone_id": zone_id,
            "dsp_id": dsp_id
        })

        self.logger.info(f"Client {dsp_id} added to zone {zone_id}")
        return True

    async def remove_client_from_zone(self, zone_id: str, dsp_id: str) -> bool:
        """
        Remove a client from a zone.

        Args:
            zone_id: The zone's ID
            dsp_id: The client's dsp_id

        Returns:
            True if client was removed, False if not found
        """
        async with self._lock:
            zone = self._zones.get(zone_id)
            if not zone:
                return False

            if dsp_id not in zone.client_ids:
                return False

            zone.client_ids.remove(dsp_id)

        # Persist to settings
        await self._persist_zones()

        await self._emit_event(RegistryEventType.ZONE_CLIENT_REMOVED, {
            "zone_id": zone_id,
            "dsp_id": dsp_id
        })

        self.logger.info(f"Client {dsp_id} removed from zone {zone_id}")
        return True

    async def set_zone_clients(self, zone_id: str, client_ids: List[str]) -> Optional[Zone]:
        """
        Set the complete client list for a zone.

        Args:
            zone_id: The zone's ID
            client_ids: New list of client dsp_ids

        Returns:
            The updated zone or None if not found
        """
        async with self._lock:
            zone = self._zones.get(zone_id)
            if not zone:
                return None

            # Validate all clients exist
            for cid in client_ids:
                if cid not in self._clients:
                    raise ValueError(f"Client {cid} not found")

            zone.client_ids = client_ids.copy()
            zone_dict = zone.to_dict()

        # Persist to settings
        await self._persist_zones()

        await self._emit_event(RegistryEventType.ZONE_UPDATED, {
            "zone_id": zone_id,
            "zone": zone_dict
        })

        return zone

    # === ZONE QUERIES ===

    def get_zone(self, zone_id: str) -> Optional[Zone]:
        """Get a zone by ID."""
        return self._zones.get(zone_id)

    def get_all_zones(self) -> Dict[str, Zone]:
        """Get all zones."""
        return self._zones.copy()

    def get_zone_for_client(self, dsp_id: str) -> Optional[Zone]:
        """Get the zone a client belongs to, if any."""
        for zone in self._zones.values():
            if dsp_id in zone.client_ids:
                return zone
        return None

    def get_zone_clients(self, zone_id: str) -> List[RegisteredClient]:
        """Get all clients in a zone."""
        zone = self._zones.get(zone_id)
        if not zone:
            return []
        return [self._clients[cid] for cid in zone.client_ids if cid in self._clients]

    def get_available_zone_clients(self, zone_id: str) -> List[RegisteredClient]:
        """Get only available clients in a zone."""
        zone = self._zones.get(zone_id)
        if not zone:
            return []
        return [
            self._clients[cid]
            for cid in zone.client_ids
            if cid in self._clients and self._clients[cid].available
        ]

    def has_available_subwoofer(self, zone_id: str) -> bool:
        """Check if zone has an available subwoofer."""
        clients = self.get_available_zone_clients(zone_id)
        return any(c.speaker_type == 'subwoofer' for c in clients)

    def get_zone_ids(self) -> List[str]:
        """Get list of all zone IDs."""
        return list(self._zones.keys())

    # === STATE SNAPSHOT ===

    def get_state(self) -> RegistryState:
        """Get complete registry state snapshot."""
        return RegistryState(
            clients=self._clients.copy(),
            zones=self._zones.copy()
        )

    def get_state_dict(self) -> Dict[str, Any]:
        """Get complete registry state as dictionary."""
        return {
            "clients": {k: v.to_dict() for k, v in self._clients.items()},
            "zones": {k: v.to_dict() for k, v in self._zones.items()}
        }

    # === EVENT SYSTEM ===

    def subscribe(self, callback: Callable[[str, Dict], Awaitable[None]]) -> None:
        """Subscribe to registry events."""
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable) -> None:
        """Unsubscribe from registry events."""
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    async def _emit_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Emit event to all subscribers and broadcast via WebSocket."""
        # Broadcast via state machine (WebSocket to frontend)
        if self._state_machine:
            await self._state_machine.broadcast_event("registry", event_type, data)

        # Emit via EventBus
        if self.event_bus:
            self.event_bus.emit(f"multiroom.{event_type}", data)

        # Notify local subscribers
        for callback in self._subscribers:
            try:
                await callback(event_type, data)
            except Exception as e:
                self.logger.error(f"Error in registry subscriber: {e}")

    # === PERSISTENCE ===

    async def _persist_zones(self) -> None:
        """Save zone configuration to settings."""
        if not self._settings_service:
            return

        try:
            zones_data = [zone.to_dict() for zone in self._zones.values()]
            await self._settings_service.set_setting("multiroom.linked_groups", zones_data)
        except Exception as e:
            self.logger.error(f"Failed to persist zones: {e}")

    async def _persist_client_types(self) -> None:
        """Save client speaker types to settings."""
        if not self._settings_service:
            return

        try:
            client_types = {
                dsp_id: {
                    "type": client.speaker_type,
                    "crossover": client.crossover_frequency
                }
                for dsp_id, client in self._clients.items()
            }
            await self._settings_service.set_setting("multiroom.client_types", client_types)

            # Update in-memory cache for future client registrations
            self._persisted_client_types = client_types

        except Exception as e:
            self.logger.error(f"Failed to persist client types: {e}")

    # === UTILITY ===

    @staticmethod
    def compute_dsp_id(host: str, ip: str) -> str:
        """
        Compute stable dsp_id from host and IP.
        This is the canonical method - all other code should use this.

        Args:
            host: Hostname
            ip: IP address

        Returns:
            Stable dsp_id
        """
        # Local snapclient (127.0.0.1) maps to "local"
        if ip == "127.0.0.1":
            return "local"
        # Use hostname if it looks like a valid milo-client hostname
        if host and host.startswith("milo-client"):
            return host
        # Otherwise use IP (for clients without proper hostname)
        return ip
