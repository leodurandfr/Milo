# backend/core/multiroom/client_registry.py
"""
ClientRegistryService - Single Source of Truth for client/zone/equalizer management.

All services that need client information MUST query this service.
This service is the ONLY place where client state is mutated.

Architecture:
- mac_id is the single unique identifier for clients (always a MAC address)
- Local client is identified by ip == "127.0.0.1" or client.is_local
- Zones share equalizer settings, standalone clients have individual equalizer
"""
import asyncio
import logging
from typing import Dict, List, Optional, Callable, Awaitable, Any

from backend.core.multiroom.models import (
    Client,
    Zone,
    EqualizerSettings,
    RegistryState,
    RegistryEventType,
    ReconnectionContext,
    SpeakerType,
    DEFAULT_SPEAKER_TYPE,
    DEFAULT_VOLUME_DB,
    DEFAULT_CROSSOVER_FREQUENCIES,
)

# Map registry event types to standardized multiroom WebSocket event types.
# Kept exported so external broadcasters (e.g. SnapcastWebSocketService) can
# translate registry events into wire-level "multiroom" categories.
REGISTRY_EVENT_TYPE_MAP = {
    RegistryEventType.CLIENT_CONNECTED: "client_state_changed",
    RegistryEventType.CLIENT_DISCONNECTED: "client_state_changed",
    RegistryEventType.CLIENT_UPDATED: "client_state_changed",
    RegistryEventType.SPEAKER_TYPE_CHANGED: "client_state_changed",
    RegistryEventType.VOLUME_CHANGED: "client_state_changed",
    RegistryEventType.ZONE_CREATED: "zone_changed",
    RegistryEventType.ZONE_UPDATED: "zone_changed",
    RegistryEventType.ZONE_DELETED: "zone_changed",
    RegistryEventType.ZONE_CLIENT_ADDED: "zone_changed",
    RegistryEventType.ZONE_CLIENT_REMOVED: "zone_changed",
    RegistryEventType.EQUALIZER_SETTINGS_CHANGED: "equalizer_changed",
}


class ClientRegistryService:
    """
    Central registry for all multiroom clients and zones.

    Responsibilities:
    - Track all clients with complete metadata
    - Manage zone configuration and equalizer settings
    - Track online/offline status (single source)
    - Notify subscribers on state changes (broadcasting is owned by callers)
    - Persist configuration to settings
    - Manage standalone equalizer settings
    """

    def __init__(self, settings_service=None):
        self.logger = logging.getLogger(__name__)
        self._settings_service = settings_service

        # Core state - protected by lock
        self._clients: Dict[str, Client] = {}
        self._zones: Dict[str, Zone] = {}
        self._client_equalizer: Dict[str, EqualizerSettings] = {}
        self._lock = asyncio.Lock()

        # Subscriber callbacks for event handling (incl. WebSocket broadcasting)
        self._subscribers: List[Callable[[str, Dict], Awaitable[None]]] = []

        self._initialized = False

    async def initialize(self) -> bool:
        """Load persisted state from settings."""
        try:
            self.logger.info("Initializing ClientRegistryService...")

            if self._settings_service:
                await self._load_persisted_state()

            self._initialized = True
            self.logger.info("ClientRegistryService initialized successfully")
            return True

        except Exception as e:
            self.logger.error(f"Failed to initialize ClientRegistryService: {e}")
            return False

    # === CLIENT MANAGEMENT ===

    async def register_client(
        self,
        mac_id: str,
        name: str,
        ip: str,
        host: str = "",
        speaker_type: SpeakerType = DEFAULT_SPEAKER_TYPE,
        volume_control: Optional[bool] = None
    ) -> Client:
        """
        Register a new client or update existing one.

        Args:
            mac_id: Primary identifier (MAC address, format xx:xx:xx:xx:xx:xx)
            name: Display name
            ip: IP address (127.0.0.1 for local client)
            host: Hostname from Snapcast
            speaker_type: Speaker type for crossover (default: bookshelf)
            volume_control: True if Milo manages volume, False for DAC (external amp).
                None preserves existing value for known clients, defaults to True for new ones.

        Returns:
            The registered or updated client
        """
        async with self._lock:
            existing = self._clients.get(mac_id)

            if existing:
                # Update connection info only - preserve user-set name and speaker_type
                if not existing.name:
                    existing.name = name
                existing.ip = ip
                existing.host = host
                if volume_control is not None:
                    existing.volume_control = volume_control
                client = existing
                event_type = RegistryEventType.CLIENT_UPDATED
            else:
                # Create new client (offline by default until Snapcast confirms)
                client = Client(
                    mac_id=mac_id,
                    name=name,
                    ip=ip,
                    host=host,
                    online=False,
                    zone_id=None,
                    volume_db=DEFAULT_VOLUME_DB,
                    mute=False,
                    speaker_type=speaker_type,
                    volume_control=volume_control if volume_control is not None else True
                )
                self._clients[mac_id] = client
                event_type = RegistryEventType.CLIENT_CONNECTED

            self.logger.info(f"Client {event_type}: {mac_id} ({name})")

        # Persist and emit event outside lock
        await self._persist_clients()
        await self._emit_event(event_type, {
            "mac_id": mac_id,
            "client": client.to_dict()
        })

        return client

    async def unregister_client(self, mac_id: str) -> bool:
        """
        Remove a client from the registry completely.

        Also removes from zones and cleans up standalone equalizer settings.

        Args:
            mac_id: The client's mac_id

        Returns:
            True if client was removed, False if not found
        """
        zones_modified = []
        zones_deleted = []

        async with self._lock:
            if mac_id not in self._clients:
                return False

            client = self._clients[mac_id]
            del self._clients[mac_id]

            # Remove from zone if in one
            if client.zone_id and client.zone_id in self._zones:
                zone = self._zones[client.zone_id]
                if mac_id in zone.client_ids:
                    zone.client_ids.remove(mac_id)
                    # Delete zone if less than 2 clients remain
                    if not zone.is_valid():
                        zones_deleted.append((zone.id, self.zone_to_enriched_dict(zone)))
                        del self._zones[zone.id]
                        self.logger.info(f"Zone {zone.id} deleted (less than 2 clients)")
                    else:
                        zones_modified.append((zone.id, self.zone_to_enriched_dict(zone)))

            # Clean up standalone equalizer
            if mac_id in self._client_equalizer:
                del self._client_equalizer[mac_id]

            self.logger.info(f"Client unregistered: {mac_id}")

        # Persist changes
        await self._persist_state()

        # Emit zone events before client event
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

        await self._emit_event(RegistryEventType.CLIENT_DISCONNECTED, {
            "mac_id": mac_id
        })

        return True

    async def set_client_online(self, mac_id: str, online: bool) -> None:
        """
        Update client online status.

        Args:
            mac_id: The client's mac_id
            online: New online status
        """
        async with self._lock:
            client = self._clients.get(mac_id)
            if not client:
                self.logger.warning(f"Cannot set online: client {mac_id} not found")
                return

            if client.online == online:
                return

            client.online = online
            client_dict = client.to_dict()

        self.logger.info(f"Client {mac_id} online status: {online}")

        event_type = RegistryEventType.CLIENT_CONNECTED if online else RegistryEventType.CLIENT_DISCONNECTED
        await self._emit_event(event_type, {
            "mac_id": mac_id,
            "client": client_dict
        })

    async def update_client(
        self,
        mac_id: str,
        name: Optional[str] = None,
        speaker_type: Optional[SpeakerType] = None,
        volume_control: Optional[bool] = None
    ) -> Optional[Client]:
        """
        Update client properties.

        Args:
            mac_id: The client's mac_id
            name: New display name (optional)
            speaker_type: New speaker type (optional)
            volume_control: True if Milo manages volume, False for DAC (optional)

        Returns:
            Updated client or None if not found
        """
        zone_to_update = None

        async with self._lock:
            client = self._clients.get(mac_id)
            if not client:
                self.logger.warning(f"Cannot update: client {mac_id} not found")
                return None

            speaker_type_changed = speaker_type is not None and speaker_type != client.speaker_type
            volume_control_changed = volume_control is not None and volume_control != client.volume_control

            if name is not None:
                client.name = name
            if speaker_type is not None:
                client.speaker_type = speaker_type
            if volume_control is not None:
                client.volume_control = volume_control

            client_dict = client.to_dict()

            # If speaker type or volume_control changed and client is in a zone, re-broadcast zone
            # (zone's all_external_volume and crossover_frequency depend on client properties)
            if (speaker_type_changed or volume_control_changed) and client.zone_id:
                zone = self._zones.get(client.zone_id)
                if zone:
                    zone_to_update = (client.zone_id, self.zone_to_enriched_dict(zone))

        await self._persist_clients()
        await self._emit_event(RegistryEventType.CLIENT_UPDATED, {
            "mac_id": mac_id,
            "client": client_dict
        })

        # Emit zone update if properties affecting zone state changed
        if zone_to_update:
            await self._emit_event(RegistryEventType.ZONE_UPDATED, {
                "zone_id": zone_to_update[0],
                "zone": zone_to_update[1]
            })

        return client

    async def update_speaker_type(
        self,
        mac_id: str,
        speaker_type: SpeakerType,
        crossover_frequency: Optional[int] = None
    ) -> Optional[Client]:
        """
        Update client speaker type.

        Used by CrossoverService. Emits
        SPEAKER_TYPE_CHANGED event for other services to react.

        Args:
            mac_id: The client's mac_id
            speaker_type: New speaker type (satellite, bookshelf, tower, subwoofer)
            crossover_frequency: Optional crossover frequency in Hz

        Returns:
            Updated client or None if not found
        """
        async with self._lock:
            client = self._clients.get(mac_id)
            if not client:
                self.logger.warning(f"Cannot update speaker type: client {mac_id} not found")
                return None

            client.speaker_type = speaker_type
            if crossover_frequency is not None:
                client.crossover_frequency = crossover_frequency

        await self._persist_clients()
        await self._emit_event(RegistryEventType.SPEAKER_TYPE_CHANGED, {
            "mac_id": mac_id,
            "client": client.to_dict()
        })

        return client

    async def update_volume(
        self,
        mac_id: str,
        volume_db: Optional[float] = None,
        mute: Optional[bool] = None
    ) -> None:
        """
        Update client volume state.

        Args:
            mac_id: The client's mac_id
            volume_db: New volume in dB (optional)
            mute: New mute status (optional)
        """
        async with self._lock:
            client = self._clients.get(mac_id)
            if not client:
                return

            if volume_db is not None:
                client.volume_db = volume_db
            if mute is not None:
                client.mute = mute

        await self._emit_event(RegistryEventType.VOLUME_CHANGED, {
            "mac_id": mac_id,
            "client": client.to_dict()
        })

    # === CLIENT QUERIES ===

    def get_client(self, mac_id: str) -> Optional[Client]:
        """Get a client by mac_id."""
        return self._clients.get(mac_id)

    def is_local_client(self, mac_id: str) -> bool:
        """Check if a client is the local device (ip == 127.0.0.1)."""
        client = self._clients.get(mac_id)
        return client.is_local if client else False

    def get_all_clients(self) -> Dict[str, Client]:
        """Get all registered clients."""
        return self._clients.copy()

    def get_online_clients(self) -> List[Client]:
        """Get only online clients."""
        return [c for c in self._clients.values() if c.online]

    def is_client_online(self, mac_id: str) -> bool:
        """Check if a specific client is online."""
        client = self._clients.get(mac_id)
        return client.online if client else False

    def get_client_ids(self) -> List[str]:
        """Get list of all client mac_ids."""
        return list(self._clients.keys())

    def get_online_client_ids(self) -> List[str]:
        """Get list of online client mac_ids."""
        return [c.mac_id for c in self._clients.values() if c.online]

    def get_client_speaker_type(self, mac_id: str) -> SpeakerType:
        """Get speaker type for a client."""
        client = self._clients.get(mac_id)
        return client.speaker_type if client else DEFAULT_SPEAKER_TYPE

    # === ZONE MANAGEMENT ===

    async def create_zone(
        self,
        zone_id: str,
        name: str,
        client_ids: List[str],
    ) -> Zone:
        """
        Create a new zone. Requires at least 2 clients.

        Membership only — a zone holds no EQ of its own (the unified per-client
        model). Each member keeps its own EQ record; the caller neutralises the
        zone's EQ via MultiroomEqualizerService.set_zone_eq().

        Args:
            zone_id: Unique zone identifier
            name: Display name
            client_ids: List of mac_ids to include (minimum 2)

        Returns:
            The created zone

        Raises:
            ValueError: If zone exists, less than 2 clients, or client not found
        """
        if len(client_ids) < 2:
            raise ValueError("Zone requires at least 2 clients")

        async with self._lock:
            if zone_id in self._zones:
                raise ValueError(f"Zone {zone_id} already exists")

            # Validate all clients exist
            for cid in client_ids:
                if cid not in self._clients:
                    raise ValueError(f"Client {cid} not found")

            zone = Zone(
                id=zone_id,
                name=name,
                client_ids=client_ids.copy(),
            )
            self._zones[zone_id] = zone

            # Update client zone_id references (EQ records are left untouched —
            # the access layer applies the neutral zone EQ to each member)
            for cid in client_ids:
                self._clients[cid].zone_id = zone_id

        await self._persist_state()
        await self._emit_event(RegistryEventType.ZONE_CREATED, {
            "zone_id": zone_id,
            "zone": self.zone_to_enriched_dict(zone)
        })

        self.logger.info(f"Zone created: {zone_id} with clients {client_ids}")
        return zone

    def _make_clients_standalone(self, mac_ids) -> None:
        """Detach clients from their zone (membership only).

        EQ is a no-op here: each client already owns its EQ record, which it
        keeps when the zone goes away. Must be called inside self._lock.
        """
        for mac_id in mac_ids:
            if mac_id in self._clients:
                self._clients[mac_id].zone_id = None

    async def delete_zone(self, zone_id: str) -> bool:
        """
        Delete a zone. Clients become standalone and keep current equalizer.

        Args:
            zone_id: The zone's ID

        Returns:
            True if zone was deleted, False if not found
        """
        async with self._lock:
            if zone_id not in self._zones:
                return False

            zone = self._zones[zone_id]
            zone_dict = self.zone_to_enriched_dict(zone)

            self._make_clients_standalone(zone.client_ids)
            del self._zones[zone_id]

        await self._persist_state()
        await self._emit_event(RegistryEventType.ZONE_DELETED, {
            "zone_id": zone_id,
            "zone": zone_dict
        })

        self.logger.info(f"Zone deleted: {zone_id}")
        return True

    async def update_zone(
        self,
        zone_id: str,
        name: Optional[str] = None,
        crossover_frequency: Optional[int] = None,
        crossover_enabled: Optional[bool] = None
    ) -> Optional[Zone]:
        """
        Update zone properties.

        Args:
            zone_id: The zone's ID
            name: New name (optional)
            crossover_frequency: Crossover frequency in Hz (optional)
            crossover_enabled: Whether crossover is enabled (optional, None = auto)

        Returns:
            The updated zone or None if not found
        """
        async with self._lock:
            zone = self._zones.get(zone_id)
            if not zone:
                return None

            if name is not None:
                zone.name = name
            if crossover_frequency is not None:
                zone.crossover_frequency = crossover_frequency
            if crossover_enabled is not None:
                zone.crossover_enabled = crossover_enabled

            zone_dict = self.zone_to_enriched_dict(zone)

        await self._persist_zones()
        await self._emit_event(RegistryEventType.ZONE_UPDATED, {
            "zone_id": zone_id,
            "zone": zone_dict
        })

        return zone

    async def add_client_to_zone(self, zone_id: str, mac_id: str) -> bool:
        """
        Add a client to a zone. Client's equalizer is replaced by zone's equalizer.

        If the client's previous zone becomes invalid (< 2 members), that zone is
        deleted and its remaining clients become standalone with the zone's EQ.

        Args:
            zone_id: The zone's ID
            mac_id: The client's mac_id

        Returns:
            True if client was added, False if zone/client not found
        """
        old_zone_deleted = False
        old_zone_id = None
        old_zone_dict = None

        async with self._lock:
            zone = self._zones.get(zone_id)
            if not zone:
                self.logger.warning(f"Cannot add client: zone {zone_id} not found")
                return False

            client = self._clients.get(mac_id)
            if not client:
                self.logger.warning(f"Cannot add client: {mac_id} not found")
                return False

            if mac_id in zone.client_ids:
                return False

            # Remove from current zone if in one
            if client.zone_id and client.zone_id in self._zones:
                old_zone = self._zones[client.zone_id]
                old_zone_id = client.zone_id
                # Snapshot before removal for consistent ZONE_DELETED payload
                old_zone_dict = self.zone_to_enriched_dict(old_zone)
                if mac_id in old_zone.client_ids:
                    old_zone.client_ids.remove(mac_id)
                # Clean up orphan zone (< 2 members)
                if not old_zone.is_valid():
                    old_zone_deleted = True
                    self._make_clients_standalone(old_zone.client_ids)
                    del self._zones[old_zone_id]

            zone.client_ids.append(mac_id)
            client.zone_id = zone_id
            # EQ record is left as-is here; the caller adopts the zone's current
            # EQ onto the new member via MultiroomEqualizerService.set_client_eq().

        await self._persist_state()

        if old_zone_deleted:
            await self._emit_event(RegistryEventType.ZONE_DELETED, {
                "zone_id": old_zone_id,
                "zone": old_zone_dict
            })
            self.logger.info(f"Zone {old_zone_id} deleted (less than 2 clients after move)")

        await self._emit_event(RegistryEventType.ZONE_UPDATED, {
            "zone_id": zone_id,
            "zone": self.zone_to_enriched_dict(zone)
        })

        self.logger.info(f"Client {mac_id} added to zone {zone_id}")
        return True

    async def remove_client_from_zone(self, zone_id: str, mac_id: str) -> bool:
        """
        Remove a client from a zone. Client keeps current equalizer as standalone.

        If zone has less than 2 clients after removal, zone is deleted.

        Args:
            zone_id: The zone's ID
            mac_id: The client's mac_id

        Returns:
            True if client was removed, False if not found
        """
        zone_deleted = False

        async with self._lock:
            zone = self._zones.get(zone_id)
            if not zone:
                return False

            if mac_id not in zone.client_ids:
                return False

            # Client keeps its own EQ record when leaving the zone
            self._make_clients_standalone([mac_id])
            zone.client_ids.remove(mac_id)

            # Delete zone if less than 2 clients
            if not zone.is_valid():
                zone_deleted = True
                zone_dict = self.zone_to_enriched_dict(zone)
                self._make_clients_standalone(zone.client_ids)
                del self._zones[zone_id]

        await self._persist_state()

        # Notify crossover service to disable filters on the removed client
        await self._emit_event(RegistryEventType.ZONE_CLIENT_REMOVED, {
            "zone_id": zone_id,
            "mac_id": mac_id
        })

        if zone_deleted:
            await self._emit_event(RegistryEventType.ZONE_DELETED, {
                "zone_id": zone_id,
                "zone": zone_dict
            })
            self.logger.info(f"Zone {zone_id} deleted (less than 2 clients)")
        else:
            await self._emit_event(RegistryEventType.ZONE_UPDATED, {
                "zone_id": zone_id,
                "zone": self.zone_to_enriched_dict(zone)
            })

        self.logger.info(f"Client {mac_id} removed from zone {zone_id}")
        return True

    # === ZONE QUERIES ===

    def get_zone(self, zone_id: str) -> Optional[Zone]:
        """Get a zone by ID."""
        return self._zones.get(zone_id)

    def get_all_zones(self) -> Dict[str, Zone]:
        """Get all zones."""
        return self._zones.copy()

    def get_zone_for_client(self, mac_id: str) -> Optional[Zone]:
        """Get the zone a client belongs to, if any."""
        client = self._clients.get(mac_id)
        if client and client.zone_id:
            return self._zones.get(client.zone_id)
        return None

    def get_zone_clients(self, zone_id: str) -> List[Client]:
        """Get all clients in a zone."""
        zone = self._zones.get(zone_id)
        if not zone:
            return []
        return [self._clients[cid] for cid in zone.client_ids if cid in self._clients]

    def get_online_zone_clients(self, zone_id: str) -> List[Client]:
        """Get only online clients in a zone."""
        zone = self._zones.get(zone_id)
        if not zone:
            return []
        return [
            self._clients[cid]
            for cid in zone.client_ids
            if cid in self._clients and self._clients[cid].online
        ]

    def get_other_online_zone_clients(self, mac_id: str) -> List[Client]:
        """
        Get online clients in the same zone, excluding the specified client.

        Used for IN_ZONE reconnection context detection (FR7, FR8).

        Args:
            mac_id: The client's mac_id to exclude from results

        Returns:
            List of online zone members excluding the specified client.
            Empty list if client is not in a zone or no other members online.
        """
        client = self._clients.get(mac_id)
        if not client or not client.zone_id:
            return []

        zone = self._zones.get(client.zone_id)
        if not zone:
            return []

        return [
            self._clients[cid]
            for cid in zone.client_ids
            if cid != mac_id and cid in self._clients and self._clients[cid].online
        ]

    def get_other_online_clients(self, mac_id: str) -> List[Client]:
        """
        Get all online clients globally, excluding the specified client.

        Used for STANDALONE reconnection context detection (FR9, FR10).

        Args:
            mac_id: The client's mac_id to exclude from results

        Returns:
            List of all online clients excluding the specified client.
        """
        return [
            c for c in self._clients.values()
            if c.mac_id != mac_id and c.online
        ]

    def get_reconnection_context(self, mac_id: str) -> ReconnectionContext:
        """
        Determine the reconnection context for a client.

        This is the first step of the reconnection sync process. The context
        determines which volume and equalizer sources to use when syncing a
        reconnecting client.

        The 4 possible contexts are:
        - IN_ZONE_OTHERS_ONLINE (FR7): Client in zone, other zone members online
        - IN_ZONE_ALL_OFFLINE (FR8): Client in zone, all other zone members offline
        - STANDALONE_OTHERS_ONLINE (FR9): Standalone client, other clients online
        - STANDALONE_ALONE (FR10): Standalone client, no other clients online

        Args:
            mac_id: The reconnecting client's mac_id

        Returns:
            One of the 4 ReconnectionContext enum values
        """
        client = self._clients.get(mac_id)

        if not client:
            # Unknown client - treat as standalone alone (safest default)
            self.logger.warning(f"Unknown client {mac_id} - treating as STANDALONE_ALONE")
            return ReconnectionContext.STANDALONE_ALONE

        if client.zone_id:
            # Client is in a zone - check for other online zone members
            other_online_zone_clients = self.get_other_online_zone_clients(mac_id)

            if other_online_zone_clients:
                # FR7: Other zone members are online
                self.logger.debug(
                    f"Client {mac_id} reconnection context: IN_ZONE_OTHERS_ONLINE "
                    f"({len(other_online_zone_clients)} other zone members online)"
                )
                return ReconnectionContext.IN_ZONE_OTHERS_ONLINE
            else:
                # FR8: All other zone members are offline
                self.logger.debug(
                    f"Client {mac_id} reconnection context: IN_ZONE_ALL_OFFLINE "
                    f"(no other zone members online)"
                )
                return ReconnectionContext.IN_ZONE_ALL_OFFLINE

        # Client is standalone - check for any other online clients globally
        other_online_clients = self.get_other_online_clients(mac_id)

        if other_online_clients:
            # FR9: Other clients are online globally
            self.logger.debug(
                f"Client {mac_id} reconnection context: STANDALONE_OTHERS_ONLINE "
                f"({len(other_online_clients)} other clients online)"
            )
            return ReconnectionContext.STANDALONE_OTHERS_ONLINE
        else:
            # FR10: No other clients online - this is the first/only client
            self.logger.debug(
                f"Client {mac_id} reconnection context: STANDALONE_ALONE "
                f"(no other clients online)"
            )
            return ReconnectionContext.STANDALONE_ALONE

    def get_zone_average_volume(
        self,
        zone_id: str,
        exclude_mac_id: Optional[str] = None
    ) -> Optional[float]:
        """
        Calculate average volume of ONLINE zone clients.

        Used for IN_ZONE_OTHERS_ONLINE reconnection sync (FR7).
        Only includes clients that are currently ONLINE.

        Args:
            zone_id: The zone ID
            exclude_mac_id: Client to exclude (typically the reconnecting client)

        Returns:
            Average volume in dB, or None if no ONLINE clients
        """
        zone = self._zones.get(zone_id)
        if not zone:
            return None

        online_volumes = []
        for mac_id in zone.client_ids:
            if mac_id == exclude_mac_id:
                continue
            client = self._clients.get(mac_id)
            if client and client.online:
                online_volumes.append(client.volume_db)

        if not online_volumes:
            return None

        return sum(online_volumes) / len(online_volumes)

    def get_global_average_volume(
        self,
        exclude_mac_id: Optional[str] = None
    ) -> Optional[float]:
        """
        Calculate average volume of ALL ONLINE clients globally.

        Used for STANDALONE_OTHERS_ONLINE reconnection sync (FR9).
        Includes all online clients regardless of zone membership.

        Args:
            exclude_mac_id: Client to exclude (typically the reconnecting client)

        Returns:
            Average volume in dB, or None if no ONLINE clients
        """
        online_volumes = []
        for mac_id, client in self._clients.items():
            if mac_id == exclude_mac_id:
                continue
            if client.online:
                online_volumes.append(client.volume_db)

        if not online_volumes:
            return None

        return sum(online_volumes) / len(online_volumes)

    def zone_to_enriched_dict(self, zone: Zone) -> Dict[str, Any]:
        """
        Convert zone to dict with computed fields for API responses.

        Adds online_client_count, has_subwoofer, crossover_enabled, and
        crossover_frequency (computed from speaker types) to the base zone dict.
        Sorts client_ids with local client first.

        Args:
            zone: The zone to convert

        Returns:
            Zone dict with computed fields
        """
        base = zone.to_dict()

        # Sort client_ids: local client first, then others
        sorted_client_ids = sorted(
            zone.client_ids,
            key=lambda mac_id: 0 if (client := self._clients.get(mac_id)) and client.is_local else 1
        )
        base['client_ids'] = sorted_client_ids

        # Compute derived fields
        online_count = 0
        has_subwoofer = False
        speaker_frequencies = []

        for mac_id in sorted_client_ids:
            client = self._clients.get(mac_id)
            if client:
                if client.online:
                    online_count += 1
                if client.speaker_type == 'subwoofer':
                    if client.online:
                        has_subwoofer = True
                else:
                    # Collect crossover frequencies from non-subwoofer speakers
                    freq = DEFAULT_CROSSOVER_FREQUENCIES.get(client.speaker_type)
                    if freq:
                        speaker_frequencies.append(freq)

        base['online_client_count'] = online_count
        base['has_subwoofer'] = has_subwoofer

        # All clients use external amplifier (DAC mode)
        all_external_volume = all(
            not self._clients[cid].volume_control
            for cid in sorted_client_ids
            if cid in self._clients
        ) if sorted_client_ids else False
        base['all_external_volume'] = all_external_volume

        # Use auto-calculated crossover frequency only if zone has no custom value
        if speaker_frequencies and zone.crossover_frequency is None:
            base['crossover_frequency'] = min(speaker_frequencies)

        # Crossover is enabled when: zone.crossover_enabled is explicitly True,
        # OR when it's None (auto) and there's an online subwoofer in the zone
        if zone.crossover_enabled is not None:
            # Explicit setting takes precedence, but still requires subwoofer to be effective
            base['crossover_enabled'] = zone.crossover_enabled and has_subwoofer
        else:
            # Auto mode: enable when there's an online subwoofer
            base['crossover_enabled'] = has_subwoofer and online_count > 0

        return base

    # === CLIENT EQUALIZER MANAGEMENT ===

    def get_client_equalizer(self, mac_id: str) -> Optional[EqualizerSettings]:
        """Get the stored equalizer settings for a client."""
        return self._client_equalizer.get(mac_id)

    async def set_client_equalizer(self, mac_id: str, settings: EqualizerSettings, broadcast: bool = True) -> None:
        """
        Set the stored equalizer settings for a client.

        Stores the record for any registered client — including zone members,
        which own their record (a zone's EQ is the identical EQ of its members).

        Args:
            mac_id: The client's mac_id
            settings: Equalizer settings to store
            broadcast: Whether to emit the EQUALIZER_SETTINGS_CHANGED event
        """
        async with self._lock:
            client = self._clients.get(mac_id)
            if not client:
                self.logger.warning(f"Cannot set equalizer: client {mac_id} not found")
                return

            # Store a copy so the registry owns its records — callers can never
            # mutate the stored object through a reference they still hold.
            self._client_equalizer[mac_id] = EqualizerSettings.from_dict(settings.to_dict())

        await self._persist_client_equalizer()
        if broadcast:
            await self._emit_event(RegistryEventType.EQUALIZER_SETTINGS_CHANGED, {
                "target_type": "client",
                "target_id": mac_id,
                # Wire shape (freq/type) — the frontend WS handler reads freq/type.
                "equalizer_settings": settings.to_wire_dict()
            })

    # === STATE SNAPSHOT ===

    def get_state(self) -> RegistryState:
        """Get complete registry state snapshot."""
        return RegistryState(
            clients=self._clients.copy(),
            zones=self._zones.copy(),
            client_equalizer=self._client_equalizer.copy()
        )

    # === EVENT SYSTEM ===

    def subscribe(self, callback: Callable[[str, Dict], Awaitable[None]]) -> None:
        """Subscribe to registry events."""
        self._subscribers.append(callback)

    async def _emit_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Notify all subscribers of a registry state change.

        The registry itself does no IO here; subscribers (including the
        WebSocket broadcaster wired in SnapcastWebSocketService) decide how
        to react.
        """
        for callback in self._subscribers:
            try:
                await callback(event_type, data)
            except Exception as e:
                self.logger.error(f"Error in registry subscriber: {e}")

    # === PERSISTENCE ===

    async def _load_persisted_state(self) -> None:
        """Load state from settings.json."""
        if not self._settings_service:
            return

        try:
            # Load clients
            clients_data = await self._settings_service.get_setting("multiroom.clients")
            if clients_data:
                for mac_id, client_data in clients_data.items():
                    client_data["mac_id"] = mac_id
                    client = Client.from_dict(client_data)
                    client.online = False  # Always start offline until Snapcast confirms
                    self._clients[mac_id] = client
                self.logger.info(f"Loaded {len(self._clients)} clients from settings")

            # Load zones
            zones_data = await self._settings_service.get_setting("multiroom.zones")
            if zones_data:
                for zone_id, zone_data in zones_data.items():
                    zone_data["id"] = zone_id
                    zone = Zone.from_dict(zone_data)
                    self._zones[zone_id] = zone
                self.logger.info(f"Loaded {len(self._zones)} zones from settings")

            # Load client equalizer
            client_eq_data = await self._settings_service.get_setting("multiroom.client_equalizer")
            if client_eq_data:
                for mac_id, equalizer_data in client_eq_data.items():
                    self._client_equalizer[mac_id] = EqualizerSettings.from_dict(equalizer_data)
                self.logger.info(f"Loaded {len(self._client_equalizer)} client equalizer configs")

        except Exception as e:
            self.logger.error(f"Failed to load persisted state: {e}")

    async def _persist_state(self) -> None:
        """Persist all state to settings."""
        await self._persist_clients()
        await self._persist_zones()
        await self._persist_client_equalizer()

    async def _persist_clients(self) -> None:
        """Save client configuration to settings."""
        if not self._settings_service:
            return

        try:
            # Only persist non-runtime fields (exclude online status)
            clients_data = {}
            for mac_id, client in self._clients.items():
                clients_data[mac_id] = {
                    "mac_id": client.mac_id,
                    "name": client.name,
                    "ip": client.ip,
                    "zone_id": client.zone_id,
                    "speaker_type": client.speaker_type,
                    "crossover_frequency": client.crossover_frequency,
                    "volume_control": client.volume_control
                    # Note: online, volume_db, mute are runtime state, not persisted
                }
            await self._settings_service.set_setting("multiroom.clients", clients_data)
        except Exception as e:
            self.logger.error(f"Failed to persist clients: {e}")

    async def _persist_zones(self) -> None:
        """Save zone configuration to settings."""
        if not self._settings_service:
            return

        try:
            zones_data = {
                zone_id: zone.to_dict()
                for zone_id, zone in self._zones.items()
            }
            await self._settings_service.set_setting("multiroom.zones", zones_data)
        except Exception as e:
            self.logger.error(f"Failed to persist zones: {e}")

    async def _persist_client_equalizer(self) -> None:
        """Save client equalizer settings to settings."""
        if not self._settings_service:
            return

        try:
            equalizer_data = {
                mac_id: settings.to_dict()
                for mac_id, settings in self._client_equalizer.items()
            }
            await self._settings_service.set_setting("multiroom.client_equalizer", equalizer_data)
        except Exception as e:
            self.logger.error(f"Failed to persist client equalizer: {e}")

    # === UTILITY ===

    @staticmethod
    def get_local_mac() -> Optional[str]:
        """MAC of the snapclient's primary interface (eth0 → wlan0 fallback), matching the --hostID flag."""
        for iface in ('eth0', 'wlan0'):
            try:
                with open(f'/sys/class/net/{iface}/address') as f:
                    return f.read().strip()
            except FileNotFoundError:
                continue
        return None

    @staticmethod
    def is_stale_local_client(client_id: str, ip: str) -> bool:
        """True for a Snapcast client at 127.0.0.1 whose id doesn't match the current local MAC."""
        if ip != "127.0.0.1":
            return False
        local_mac = ClientRegistryService.get_local_mac()
        return bool(local_mac) and client_id != local_mac

    @staticmethod
    def compute_mac_id(hostname: str, ip: str, mac: str = "") -> str:
        """
        Return the MAC address as mac_id.

        For local client (127.0.0.1), read MAC from system interface.
        For remote clients, MAC is provided by Snapcast.

        Args:
            hostname: Hostname from Snapcast (for logging only)
            ip: IP address from Snapcast
            mac: MAC address from Snapcast (for remote clients)

        Returns:
            MAC address in format xx:xx:xx:xx:xx:xx

        Raises:
            RuntimeError: If local MAC cannot be determined
            ValueError: If remote client has no MAC address
        """
        # Local client: always read MAC from system interface
        # Snapcast's host.mac for loopback may differ from the actual primary interface
        # (e.g. wlan0 instead of eth0), so we ignore it and read directly from /sys
        # to stay consistent with the --hostID flag in the snapclient service.
        if ip == "127.0.0.1":
            local_mac = ClientRegistryService.get_local_mac()
            if not local_mac:
                raise RuntimeError("Cannot determine local MAC address")
            return local_mac

        # Remote clients: use MAC provided by Snapcast
        if mac and mac != "00:00:00:00:00:00":
            return mac

        raise ValueError(f"No MAC address for client {hostname} at {ip}")

