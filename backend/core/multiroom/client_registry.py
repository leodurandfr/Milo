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
import contextlib
import logging
from typing import Dict, List, Optional, Callable, Awaitable, Any, Type, Union

from backend.shared.background import BackgroundTaskSet

from backend.core.models.ws_events import (
    MultiroomClientStateChanged,
    MultiroomEqualizerChanged,
    MultiroomZoneChanged,
    WsEvent,
)
from backend.core.multiroom.models import (
    Client,
    Zone,
    EqualizerSettings,
    RegistryState,
    RegistryEventType,
    SpeakerType,
    DEFAULT_SPEAKER_TYPE,
    DEFAULT_CROSSOVER_FREQUENCIES,
    DEFAULT_CROSSOVER_FREQUENCY,
)

# Map registry event types to the typed multiroom WS event classes; each
# registry payload dict maps 1:1 onto the class's fields (cls(**data)).
# Kept exported so external broadcasters (e.g. SnapcastWebSocketService) can
# translate registry events into wire-level "multiroom" events.
REGISTRY_EVENT_CLASSES: Dict[str, Type[WsEvent]] = {
    RegistryEventType.CLIENT_CONNECTED: MultiroomClientStateChanged,
    RegistryEventType.CLIENT_DISCONNECTED: MultiroomClientStateChanged,
    RegistryEventType.CLIENT_UPDATED: MultiroomClientStateChanged,
    RegistryEventType.ZONE_CREATED: MultiroomZoneChanged,
    RegistryEventType.ZONE_UPDATED: MultiroomZoneChanged,
    RegistryEventType.ZONE_DELETED: MultiroomZoneChanged,
    RegistryEventType.ZONE_CLIENT_REMOVED: MultiroomZoneChanged,
    RegistryEventType.EQUALIZER_SETTINGS_CHANGED: MultiroomEqualizerChanged,
}



class _Unset:
    """Sentinel for "argument not supplied".

    `None` is a value for the crossover fields — it means *auto* — so it cannot
    double as "not supplied" the way it does for `name`. Without this, a zone
    that had ever been given an explicit frequency could never be handed back
    to auto: `update_zone` skipped every None it was passed.
    """
    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


_UNSET = _Unset()


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

    # Debounce for the one write path that streams: an EQ band drag emits 20
    # requests a second and each one used to rewrite the whole of settings.json.
    # Same value as CamillaDSPService.PERSIST_DEBOUNCE_S, which collapses the
    # local half of that very gesture.
    PERSIST_DEBOUNCE_S = 1.0

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

        # Debounced persistence, used by the EQ drag only (see set_clients_equalizer)
        self._bg = BackgroundTaskSet(self.logger, "client_registry")
        self._persist_debounce_task: Optional[asyncio.Task] = None

        self._initialized = False

    async def cleanup(self) -> None:
        """Flush a pending debounced persist, then drain the task set."""
        if self._persist_debounce_task and not self._persist_debounce_task.done():
            self._persist_debounce_task.cancel()
            await self._persist_state()
            self.logger.info("Flushed pending multiroom state on shutdown")
        await self._bg.cancel_all()

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
                    speaker_type=speaker_type,
                    volume_control=volume_control if volume_control is not None else True
                )
                self._clients[mac_id] = client
                event_type = RegistryEventType.CLIENT_CONNECTED

            self.logger.info(f"Client {event_type}: {mac_id} ({name})")

        # Persist and emit event outside lock
        await self._persist_state()
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
                        self._make_clients_standalone(zone.client_ids)
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
                "action": "updated",
                "zone_id": zone_id,
                "zone": zone_dict
            })
        for zone_id, zone_dict in zones_deleted:
            await self._emit_event(RegistryEventType.ZONE_DELETED, {
                "action": "deleted",
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

        await self._persist_state()
        await self._emit_event(RegistryEventType.CLIENT_UPDATED, {
            "mac_id": mac_id,
            "client": client_dict
        })

        # Emit zone update if properties affecting zone state changed
        if zone_to_update:
            await self._emit_event(RegistryEventType.ZONE_UPDATED, {
                "action": "updated",
                "zone_id": zone_to_update[0],
                "zone": zone_to_update[1]
            })

        return client

    async def set_client_eq_independent(self, mac_id: str, enabled: bool) -> Optional[Client]:
        """Set a client's eq_independent override flag.

        Persists and broadcasts CLIENT_UPDATED so the flag reaches the frontend
        (which regroups the EQ tab strip from it). The EQ re-adoption on detach/
        reattach is the caller's concern (MultiroomEqualizerService); this only
        owns the flag on the client record.

        Returns the updated client, or None if not found.
        """
        async with self._lock:
            client = self._clients.get(mac_id)
            if not client:
                self.logger.warning(f"Cannot set eq_independent: client {mac_id} not found")
                return None
            client.eq_independent = enabled
            client_dict = client.to_dict()

        await self._persist_state()
        await self._emit_event(RegistryEventType.CLIENT_UPDATED, {
            "mac_id": mac_id,
            "client": client_dict
        })
        return client

    async def set_client_delay(self, mac_id: str, delay_ms: int) -> Optional[Client]:
        """Set a client's per-client playback delay (native Snapcast latency).

        Persists and broadcasts CLIENT_UPDATED so the value reaches the frontend.
        Applying it to snapserver (Client.SetLatency) is the caller's concern —
        the registry only owns the source-of-truth value on the client record.

        Returns the updated client, or None if not found.
        """
        async with self._lock:
            client = self._clients.get(mac_id)
            if not client:
                self.logger.warning(f"Cannot set delay: client {mac_id} not found")
                return None
            client.delay_ms = delay_ms
            client_dict = client.to_dict()

        await self._persist_state()
        await self._emit_event(RegistryEventType.CLIENT_UPDATED, {
            "mac_id": mac_id,
            "client": client_dict
        })
        return client

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
            "action": "created",
            "zone_id": zone_id,
            "zone": self.zone_to_enriched_dict(zone)
        })

        self.logger.info(f"Zone created: {zone_id} with clients {client_ids}")
        return zone

    def _make_clients_standalone(self, mac_ids) -> None:
        """Detach clients from their zone (membership only).

        EQ is a no-op here: each client already owns its EQ record, which it
        keeps when the zone goes away. The eq_independent override is cleared,
        though — it only means anything relative to a zone the client is in.
        Must be called inside self._lock.
        """
        for mac_id in mac_ids:
            if mac_id in self._clients:
                self._clients[mac_id].zone_id = None
                self._clients[mac_id].eq_independent = False

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
            "action": "deleted",
            "zone_id": zone_id,
            "zone": zone_dict
        })

        self.logger.info(f"Zone deleted: {zone_id}")
        return True

    async def update_zone(
        self,
        zone_id: str,
        name: Optional[str] = None,
        crossover_frequency: Union[int, None, _Unset] = _UNSET,
        crossover_enabled: Union[bool, None, _Unset] = _UNSET
    ) -> Optional[Zone]:
        """
        Update zone properties.

        Args:
            zone_id: The zone's ID
            name: New name (omit to leave unchanged)
            crossover_frequency: Frequency in Hz, or None for auto (omit to
                leave unchanged — None is a value here, not an absence)
            crossover_enabled: Whether crossover is enabled, or None for auto
                (omit to leave unchanged)

        Returns:
            The updated zone or None if not found
        """
        async with self._lock:
            zone = self._zones.get(zone_id)
            if not zone:
                return None

            if name is not None:
                zone.name = name
            if crossover_frequency is not _UNSET:
                zone.crossover_frequency = crossover_frequency
            if crossover_enabled is not _UNSET:
                zone.crossover_enabled = crossover_enabled

            zone_dict = self.zone_to_enriched_dict(zone)

        await self._persist_state()
        await self._emit_event(RegistryEventType.ZONE_UPDATED, {
            "action": "updated",
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
            # A fresh member adopts the zone's shared EQ, so any prior independent
            # override no longer applies.
            client.eq_independent = False
            # EQ record is left as-is here; the caller adopts the zone's current
            # EQ onto the new member via MultiroomEqualizerService.set_client_eq().

        await self._persist_state()

        if old_zone_deleted:
            await self._emit_event(RegistryEventType.ZONE_DELETED, {
                "action": "deleted",
                "zone_id": old_zone_id,
                "zone": old_zone_dict
            })
            self.logger.info(f"Zone {old_zone_id} deleted (less than 2 clients after move)")

        await self._emit_event(RegistryEventType.ZONE_UPDATED, {
            "action": "updated",
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
            "action": "client_removed",
            "zone_id": zone_id,
            "mac_id": mac_id
        })

        if zone_deleted:
            await self._emit_event(RegistryEventType.ZONE_DELETED, {
                "action": "deleted",
                "zone_id": zone_id,
                "zone": zone_dict
            })
            self.logger.info(f"Zone {zone_id} deleted (less than 2 clients)")
        else:
            await self._emit_event(RegistryEventType.ZONE_UPDATED, {
                "action": "updated",
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

    def auto_crossover_frequency(self, zone: Zone) -> int:
        """The crossover frequency a zone's own speakers imply.

        The *highest* of the members' declared frequencies, not the lowest. One
        highpass serves every non-subwoofer member, so it has to sit at or above
        what the weakest speaker can reproduce: taking the minimum hands a
        satellite (120 Hz) the tower's 50 Hz and asks it for a band its speaker
        type declares it cannot deliver — and the subwoofer, cut at that same
        50 Hz, does not fill it either, so the band is simply missing.

        Subwoofers contribute nothing: the table gives them None because they
        receive the lowpass, not the highpass.
        """
        frequencies = [
            freq
            for mac_id in zone.client_ids
            if (client := self._clients.get(mac_id))
            and (freq := DEFAULT_CROSSOVER_FREQUENCIES.get(client.speaker_type))
        ]
        return max(frequencies) if frequencies else DEFAULT_CROSSOVER_FREQUENCY

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

        for mac_id in sorted_client_ids:
            client = self._clients.get(mac_id)
            if client:
                if client.online:
                    online_count += 1
                if client.speaker_type == 'subwoofer' and client.online:
                    has_subwoofer = True

        base['online_client_count'] = online_count
        base['has_subwoofer'] = has_subwoofer

        # All clients use external amplifier (DAC mode)
        all_external_volume = all(
            not self._clients[cid].volume_control
            for cid in sorted_client_ids
            if cid in self._clients
        ) if sorted_client_ids else False
        base['all_external_volume'] = all_external_volume

        # The wire always carries a usable number plus the flag saying where it
        # came from, so the UI shows "auto" without re-deriving anything.
        base['crossover_auto'] = zone.crossover_frequency is None
        if zone.crossover_frequency is None:
            base['crossover_frequency'] = self.auto_crossover_frequency(zone)

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
        await self.set_clients_equalizer({mac_id: settings}, broadcast=broadcast)

    async def set_clients_equalizer(
        self,
        records: Dict[str, EqualizerSettings],
        broadcast: bool = True,
        defer_persist: bool = False,
    ) -> None:
        """Store several clients' equalizer records in **one** settings.json write.

        The zone fan-out writes the same record to every member, and
        ``_persist_state`` rewrites the whole file each time: a 3 s EQ drag over
        a two-member zone measured 61 full rewrites + fsyncs on the SD card, one
        per member per throttled request. Persistence is a property of the batch,
        not of the individual record, so the loop belongs here rather than at the
        call site.

        ``defer_persist`` debounces that write (see :meth:`_schedule_persist`),
        which is what removes the other half of the cost — batching alone changes
        nothing for a zone holding a single satellite, and one write per request
        at 20 requests a second is still 61 rewrites for a 3 s drag. It is for the
        streamed path only: a deliberate one-shot write (a preset, a save) stays
        immediate, exactly as the local client's equalizer.json does.

        An empty mapping is a no-op — no write, no event.
        """
        if not records:
            return

        stored: Dict[str, EqualizerSettings] = {}
        async with self._lock:
            for mac_id, settings in records.items():
                if not self._clients.get(mac_id):
                    self.logger.warning(f"Cannot set equalizer: client {mac_id} not found")
                    continue
                # Store a copy so the registry owns its records — callers can never
                # mutate the stored object through a reference they still hold.
                self._client_equalizer[mac_id] = EqualizerSettings.from_dict(settings.to_dict())
                stored[mac_id] = settings

        if not stored:
            return

        if defer_persist:
            self._schedule_persist()
        else:
            await self._persist_state()
        if broadcast:
            for mac_id, settings in stored.items():
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

    def _clients_data(self) -> dict:
        """Build the persistable client map (Client.PERSISTED_FIELDS owns the shape)."""
        return {
            mac_id: client.to_dict(include_runtime=False)
            for mac_id, client in self._clients.items()
        }

    def _zones_data(self) -> dict:
        """Build the persistable zone map."""
        return {zone_id: zone.to_dict() for zone_id, zone in self._zones.items()}

    def _client_equalizer_data(self) -> dict:
        """Build the persistable client-equalizer map."""
        return {
            mac_id: settings.to_dict()
            for mac_id, settings in self._client_equalizer.items()
        }

    def _schedule_persist(self) -> None:
        """Schedule a debounced persist (~1 s after the last change).

        Only :meth:`set_clients_equalizer` uses it, and only when its caller asks
        for it: every other mutation here is a discrete event (a client arrives,
        a zone is renamed) that must survive an immediate power cut, while an EQ
        drag is a stream of 20 requests a second whose intermediate values nobody
        needs on disk. Correct by construction whatever the interleaving, because
        :meth:`_persist_state` always serialises the *current* in-memory state —
        so an immediate persist supersedes a pending one, and cancels it.
        """
        if self._persist_debounce_task and not self._persist_debounce_task.done():
            self._persist_debounce_task.cancel()

        async def _debounced():
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.sleep(self.PERSIST_DEBOUNCE_S)
                await self._persist_state()

        self._persist_debounce_task = self._bg.spawn(_debounced(), label="persist_state")

    async def _persist_state(self) -> None:
        """Persist all multiroom state to settings in one atomic write.

        The only persist path, deliberately: clients, zones and equalizer are
        logically coupled (a zone references client macs, an EQ record
        references a client), so a torn write that persisted clients without
        their zones would desync the registry, and every mutation here can
        touch more than the section it names — unregistering a client deletes
        a zone and an EQ record with it. ``set_setting`` is itself
        ``set_settings({k: v})``, one read-modify-write of the whole file, so
        writing three keys instead of one costs nothing.
        """
        # This write already carries everything a pending debounced one would.
        # `is not current_task` is load-bearing, not defensive: the debounced
        # task reaches this line through its own timer, and without the guard it
        # cancels itself here and the write never happens — the record then only
        # ever landed on the shutdown flush. Found on the unit, not in the suite,
        # because a test that flushes through cleanup() cancels from another task.
        pending = self._persist_debounce_task
        if pending and not pending.done() and pending is not asyncio.current_task():
            pending.cancel()

        if not self._settings_service:
            return

        try:
            await self._settings_service.set_settings({
                "multiroom.clients": self._clients_data(),
                "multiroom.zones": self._zones_data(),
                "multiroom.client_equalizer": self._client_equalizer_data(),
            })
        except Exception as e:
            self.logger.error(f"Failed to persist multiroom state: {e}")

