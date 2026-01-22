# backend/core/multiroom/registry.py
"""
ClientRegistryService - Single Source of Truth for client/zone/DSP management.

All services that need client information MUST query this service.
This service is the ONLY place where client state is mutated.

Architecture:
- mac_id is the single unique identifier for clients (always a MAC address)
- Local client is identified by ip == "127.0.0.1" or client.is_local
- Zones share DSP settings, standalone clients have individual DSP
"""
import asyncio
import logging
from typing import Dict, List, Optional, Callable, Awaitable, Any

from backend.core.events import EventBus, get_event_bus
from backend.core.multiroom.models import (
    Client,
    Zone,
    DspSettings,
    RegistryState,
    RegistryEventType,
    ReconnectionContext,
    SpeakerType,
    DEFAULT_SPEAKER_TYPE,
    DEFAULT_VOLUME_DB,
    DEFAULT_CROSSOVER_FREQUENCIES,
)


class ClientRegistryService:
    """
    Central registry for all multiroom clients and zones.

    Responsibilities:
    - Track all clients with complete metadata
    - Manage zone configuration and DSP settings
    - Track online/offline status (single source)
    - Emit events on state changes
    - Persist configuration to settings
    - Manage standalone DSP settings
    """

    def __init__(self, settings_service=None, event_bus: EventBus = None):
        self.logger = logging.getLogger(__name__)
        self._settings_service = settings_service
        self._state_machine = None
        self.event_bus = event_bus or get_event_bus()

        # Core state - protected by lock
        self._clients: Dict[str, Client] = {}
        self._zones: Dict[str, Zone] = {}
        self._standalone_dsp: Dict[str, DspSettings] = {}
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
        speaker_type: SpeakerType = DEFAULT_SPEAKER_TYPE
    ) -> Client:
        """
        Register a new client or update existing one.

        Args:
            mac_id: Primary identifier (MAC address, format xx:xx:xx:xx:xx:xx)
            name: Display name
            ip: IP address (127.0.0.1 for local client)
            speaker_type: Speaker type for crossover (default: bookshelf)

        Returns:
            The registered or updated client
        """
        async with self._lock:
            existing = self._clients.get(mac_id)

            if existing:
                # Update existing client (keep online status and zone)
                existing.name = name
                existing.ip = ip
                # Don't overwrite speaker_type if already set (persisted preference)
                client = existing
                event_type = RegistryEventType.CLIENT_UPDATED
            else:
                # Create new client (offline by default until Snapcast confirms)
                client = Client(
                    mac_id=mac_id,
                    name=name,
                    ip=ip,
                    online=False,
                    zone_id=None,
                    volume_db=DEFAULT_VOLUME_DB,
                    mute=False,
                    speaker_type=speaker_type
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

        Also removes from zones and cleans up standalone DSP settings.

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

            # Clean up standalone DSP
            if mac_id in self._standalone_dsp:
                del self._standalone_dsp[mac_id]

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

    async def delete_client(self, mac_id: str) -> bool:
        """Alias for unregister_client for API consistency."""
        return await self.unregister_client(mac_id)

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
                return  # No change

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
        speaker_type: Optional[SpeakerType] = None
    ) -> Optional[Client]:
        """
        Update client properties.

        Args:
            mac_id: The client's mac_id
            name: New display name (optional)
            speaker_type: New speaker type (optional)

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

            if name is not None:
                client.name = name
            if speaker_type is not None:
                client.speaker_type = speaker_type

            client_dict = client.to_dict()

            # If speaker type changed and client is in a zone, prepare zone update
            # (zone's crossover_frequency depends on speaker types)
            if speaker_type_changed and client.zone_id:
                zone = self._zones.get(client.zone_id)
                if zone:
                    zone_to_update = (client.zone_id, self.zone_to_enriched_dict(zone))

        await self._persist_clients()
        await self._emit_event(RegistryEventType.CLIENT_UPDATED, {
            "mac_id": mac_id,
            "client": client_dict
        })

        # Emit zone update if speaker type changed (for crossover_frequency recalculation)
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

    def get_client_by_dsp_id(self, dsp_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a client by DSP ID (mac_id).

        In Milo architecture, mac_id IS the DSP ID for volume/DSP operations.

        Args:
            dsp_id: Client mac_id (MAC address)

        Returns:
            Client data dict if found, None otherwise
        """
        client = self._clients.get(dsp_id)
        return client.to_dict() if client else None

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
        dsp_settings: Optional[DspSettings] = None
    ) -> Zone:
        """
        Create a new zone. Requires at least 2 clients.

        Args:
            zone_id: Unique zone identifier
            name: Display name
            client_ids: List of mac_ids to include (minimum 2)
            dsp_settings: Initial DSP settings (optional)

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

            # Create zone
            zone = Zone(
                id=zone_id,
                name=name,
                client_ids=client_ids.copy(),
                dsp_settings=dsp_settings or DspSettings.default()
            )
            self._zones[zone_id] = zone

            # Update client zone_id references
            for cid in client_ids:
                self._clients[cid].zone_id = zone_id
                # Move standalone DSP to zone (first client's DSP becomes zone DSP)
                if cid in self._standalone_dsp:
                    del self._standalone_dsp[cid]

        await self._persist_state()
        await self._emit_event(RegistryEventType.ZONE_CREATED, {
            "zone_id": zone_id,
            "zone": self.zone_to_enriched_dict(zone)
        })

        self.logger.info(f"Zone created: {zone_id} with clients {client_ids}")
        return zone

    async def delete_zone(self, zone_id: str) -> bool:
        """
        Delete a zone. Clients become standalone and keep current DSP.

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

            # Clients keep zone DSP as their standalone DSP
            for mac_id in zone.client_ids:
                if mac_id in self._clients:
                    self._clients[mac_id].zone_id = None
                    self._standalone_dsp[mac_id] = DspSettings.from_dict(
                        zone.dsp_settings.to_dict()
                    )

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
        Add a client to a zone. Client's DSP is replaced by zone's.

        Args:
            zone_id: The zone's ID
            mac_id: The client's mac_id

        Returns:
            True if client was added, False if zone/client not found
        """
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
                return False  # Already in zone

            # Remove from current zone if in one
            if client.zone_id and client.zone_id in self._zones:
                old_zone = self._zones[client.zone_id]
                if mac_id in old_zone.client_ids:
                    old_zone.client_ids.remove(mac_id)

            zone.client_ids.append(mac_id)
            client.zone_id = zone_id

            # Remove standalone DSP (client now uses zone's DSP)
            if mac_id in self._standalone_dsp:
                del self._standalone_dsp[mac_id]

        await self._persist_state()
        await self._emit_event(RegistryEventType.ZONE_UPDATED, {
            "zone_id": zone_id,
            "zone": self.zone_to_enriched_dict(zone)
        })

        self.logger.info(f"Client {mac_id} added to zone {zone_id}")
        return True

    async def remove_client_from_zone(self, zone_id: str, mac_id: str) -> bool:
        """
        Remove a client from a zone. Client keeps current DSP as standalone.

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

            # Client keeps zone DSP as standalone
            client = self._clients.get(mac_id)
            if client:
                client.zone_id = None
                self._standalone_dsp[mac_id] = DspSettings.from_dict(
                    zone.dsp_settings.to_dict()
                )

            zone.client_ids.remove(mac_id)

            # Delete zone if less than 2 clients
            if not zone.is_valid():
                zone_deleted = True
                zone_dict = self.zone_to_enriched_dict(zone)
                # Remaining clients also become standalone
                for remaining_mac_id in zone.client_ids:
                    if remaining_mac_id in self._clients:
                        self._clients[remaining_mac_id].zone_id = None
                        self._standalone_dsp[remaining_mac_id] = DspSettings.from_dict(
                            zone.dsp_settings.to_dict()
                        )
                del self._zones[zone_id]

        await self._persist_state()

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

    async def set_zone_clients(self, zone_id: str, client_ids: List[str]) -> Optional[Zone]:
        """
        Set the complete client list for a zone.

        Replaces all zone members in one operation. Handles DSP transitions:
        - Clients leaving zone keep zone DSP as standalone
        - Clients joining zone have standalone DSP cleared

        Args:
            zone_id: The zone ID to update
            client_ids: Complete list of client mac_ids for the zone

        Returns:
            Updated zone or None if zone not found

        Raises:
            ValueError: If fewer than 2 clients or any client not found
        """
        async with self._lock:
            zone = self._zones.get(zone_id)
            if not zone:
                return None

            # Validate minimum clients
            if len(client_ids) < 2:
                raise ValueError("Zone requires at least 2 clients")

            # Validate all clients exist
            for mac_id in client_ids:
                if mac_id not in self._clients:
                    raise ValueError(f"Client {mac_id} not found")

            # Determine clients leaving and joining
            old_client_ids = set(zone.client_ids)
            new_client_ids = set(client_ids)
            leaving = old_client_ids - new_client_ids
            joining = new_client_ids - old_client_ids

            # Handle clients leaving zone - keep DSP as standalone
            for mac_id in leaving:
                if mac_id in self._clients:
                    self._clients[mac_id].zone_id = None
                    self._standalone_dsp[mac_id] = DspSettings.from_dict(
                        zone.dsp_settings.to_dict()
                    )

            # Handle clients joining zone - DSP replaced by zone's
            for mac_id in joining:
                if mac_id in self._clients:
                    self._clients[mac_id].zone_id = zone_id
                    # Clear standalone DSP (zone takes over)
                    self._standalone_dsp.pop(mac_id, None)

            # Update zone
            zone.client_ids = client_ids
            zone_dict = self.zone_to_enriched_dict(zone)

        # Persist all changes
        await self._persist_zones()
        await self._persist_clients()
        await self._persist_standalone_dsp()

        # Emit event
        await self._emit_event(RegistryEventType.ZONE_UPDATED, {
            "zone_id": zone_id,
            "zone": zone_dict
        })

        self.logger.info(f"Zone {zone_id} clients updated: {client_ids}")
        return zone

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

    def has_online_subwoofer(self, zone_id: str) -> bool:
        """Check if zone has an online subwoofer."""
        clients = self.get_online_zone_clients(zone_id)
        return any(c.speaker_type == 'subwoofer' for c in clients)

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
        determines which volume and DSP sources to use when syncing a
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

    def get_zone_ids(self) -> List[str]:
        """Get list of all zone IDs."""
        return list(self._zones.keys())

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

        # Compute crossover frequency from speaker types (use min frequency)
        if speaker_frequencies:
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

    # === STANDALONE DSP MANAGEMENT ===

    def get_standalone_dsp(self, mac_id: str) -> Optional[DspSettings]:
        """Get standalone DSP settings for a client."""
        return self._standalone_dsp.get(mac_id)

    async def set_standalone_dsp(self, mac_id: str, settings: DspSettings) -> None:
        """
        Set standalone DSP settings for a client.

        Args:
            mac_id: The client's mac_id
            settings: DSP settings to store
        """
        async with self._lock:
            client = self._clients.get(mac_id)
            if not client:
                self.logger.warning(f"Cannot set DSP: client {mac_id} not found")
                return

            if client.zone_id:
                self.logger.warning(f"Client {mac_id} is in zone, use zone DSP instead")
                return

            self._standalone_dsp[mac_id] = settings

        await self._persist_standalone_dsp()
        await self._emit_event(RegistryEventType.DSP_SETTINGS_CHANGED, {
            "target_type": "client",
            "target_id": mac_id,
            "dsp_settings": settings.to_dict()
        })

    def get_client_dsp_settings(self, mac_id: str) -> Optional[DspSettings]:
        """
        Get DSP settings for a client (from zone or standalone).

        Args:
            mac_id: The client's mac_id

        Returns:
            DSP settings or None if not found
        """
        client = self._clients.get(mac_id)
        if not client:
            return None

        # If in zone, return zone's DSP
        if client.zone_id:
            zone = self._zones.get(client.zone_id)
            if zone:
                return zone.dsp_settings

        # Otherwise return standalone DSP
        return self._standalone_dsp.get(mac_id)

    async def set_zone_dsp(self, zone_id: str, settings: DspSettings) -> bool:
        """
        Set DSP settings for a zone.

        Updates zone.dsp_settings and persists to settings.json.

        Args:
            zone_id: The zone's ID
            settings: DSP settings to store

        Returns:
            True if successful, False if zone not found
        """
        async with self._lock:
            zone = self._zones.get(zone_id)
            if not zone:
                self.logger.warning(f"Cannot set DSP: zone {zone_id} not found")
                return False

            zone.dsp_settings = settings

        await self._persist_zones()
        await self._emit_event(RegistryEventType.DSP_SETTINGS_CHANGED, {
            "target_type": "zone",
            "target_id": zone_id,
            "dsp_settings": settings.to_dict()
        })
        return True

    # === STATE SNAPSHOT ===

    def get_state(self) -> RegistryState:
        """Get complete registry state snapshot."""
        return RegistryState(
            clients=self._clients.copy(),
            zones=self._zones.copy(),
            standalone_dsp=self._standalone_dsp.copy()
        )

    def get_state_dict(self) -> Dict[str, Any]:
        """Get complete registry state as dictionary."""
        return {
            "clients": {k: v.to_dict() for k, v in self._clients.items()},
            "zones": {k: v.to_dict() for k, v in self._zones.items()},
            "standalone_dsp": {k: v.to_dict() for k, v in self._standalone_dsp.items()}
        }

    # === EVENT SYSTEM ===

    def subscribe(self, callback: Callable[[str, Dict], Awaitable[None]]) -> None:
        """Subscribe to registry events."""
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable) -> None:
        """Unsubscribe from registry events."""
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    def _map_event_type(self, event_type: str) -> str:
        """
        Map registry event types to standardized multiroom event types.

        This mapping aligns with the architecture spec for WebSocket events:
        - Client events → client_state_changed
        - Zone events → zone_changed
        - DSP events → dsp_changed
        """
        client_events = {
            RegistryEventType.CLIENT_CONNECTED,
            RegistryEventType.CLIENT_DISCONNECTED,
            RegistryEventType.CLIENT_UPDATED,
            RegistryEventType.SPEAKER_TYPE_CHANGED,
            RegistryEventType.VOLUME_CHANGED,
        }
        zone_events = {
            RegistryEventType.ZONE_CREATED,
            RegistryEventType.ZONE_UPDATED,
            RegistryEventType.ZONE_DELETED,
            RegistryEventType.ZONE_CLIENT_ADDED,
            RegistryEventType.ZONE_CLIENT_REMOVED,
        }
        dsp_events = {
            RegistryEventType.DSP_SETTINGS_CHANGED,
        }

        if event_type in client_events:
            return "client_state_changed"
        elif event_type in zone_events:
            return "zone_changed"
        elif event_type in dsp_events:
            return "dsp_changed"
        else:
            # Fallback: use original event type in snake_case
            return event_type.lower()

    async def _emit_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Emit event to all subscribers and broadcast via WebSocket."""
        # Broadcast via state machine (WebSocket to frontend)
        if self._state_machine:
            mapped_type = self._map_event_type(event_type)
            await self._state_machine.broadcast_event("multiroom", mapped_type, data)

        # Emit via EventBus
        if self.event_bus:
            mapped_type = self._map_event_type(event_type)
            await self.event_bus.emit(f"multiroom.{mapped_type}", data)

        # Notify local subscribers
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
                    client_data["mac_id"] = mac_id  # Ensure mac_id is set
                    client = Client.from_dict(client_data)
                    client.online = False  # Always start offline until Snapcast confirms
                    self._clients[mac_id] = client
                self.logger.info(f"Loaded {len(self._clients)} clients from settings")

            # Load zones
            zones_data = await self._settings_service.get_setting("multiroom.zones")
            if zones_data:
                for zone_id, zone_data in zones_data.items():
                    zone_data["id"] = zone_id  # Ensure id is set
                    zone = Zone.from_dict(zone_data)
                    self._zones[zone_id] = zone
                self.logger.info(f"Loaded {len(self._zones)} zones from settings")

            # Load standalone DSP
            standalone_data = await self._settings_service.get_setting("multiroom.standalone_dsp")
            if standalone_data:
                for mac_id, dsp_data in standalone_data.items():
                    self._standalone_dsp[mac_id] = DspSettings.from_dict(dsp_data)
                self.logger.info(f"Loaded {len(self._standalone_dsp)} standalone DSP configs")

        except Exception as e:
            self.logger.error(f"Failed to load persisted state: {e}")

    async def _persist_state(self) -> None:
        """Persist all state to settings."""
        await self._persist_clients()
        await self._persist_zones()
        await self._persist_standalone_dsp()

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
                    "crossover_frequency": client.crossover_frequency
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

    async def _persist_standalone_dsp(self) -> None:
        """Save standalone DSP settings to settings."""
        if not self._settings_service:
            return

        try:
            dsp_data = {
                mac_id: settings.to_dict()
                for mac_id, settings in self._standalone_dsp.items()
            }
            await self._settings_service.set_setting("multiroom.standalone_dsp", dsp_data)
        except Exception as e:
            self.logger.error(f"Failed to persist standalone DSP: {e}")

    # === UTILITY ===

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
        # MAC provided by Snapcast (remote clients)
        if mac and mac != "00:00:00:00:00:00":
            return mac

        # Local client: read MAC from system interface
        if ip == "127.0.0.1":
            for iface in ['eth0', 'wlan0']:
                try:
                    with open(f'/sys/class/net/{iface}/address') as f:
                        return f.read().strip()
                except FileNotFoundError:
                    continue
            # Last resort (should never happen on a real system)
            raise RuntimeError("Cannot determine local MAC address")

        # Remote without MAC (Snapcast error)
        raise ValueError(f"No MAC address for client {hostname} at {ip}")

