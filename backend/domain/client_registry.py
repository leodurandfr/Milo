# backend/domain/client_registry.py
"""
Domain models for the centralized client/zone registry.

This module defines the core data structures for managing multiroom clients
and zones (linked groups). These models are the single source of truth for
client state throughout the application.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Literal, Any
from datetime import datetime


# Speaker types for crossover configuration
SpeakerType = Literal['satellite', 'bookshelf', 'tower', 'subwoofer']

# Default values
DEFAULT_SPEAKER_TYPE: SpeakerType = 'bookshelf'
DEFAULT_CROSSOVER_FREQUENCY = 80
DEFAULT_VOLUME_DB = -30.0


@dataclass
class RegisteredClient:
    """
    Complete client information - single source of truth.

    Attributes:
        dsp_id: Primary identifier for DSP operations ('local' for main device,
                or hostname/IP for satellites)
        snapcast_id: Snapcast's internal client ID
        name: Display name for UI
        host: Hostname
        ip: IP address
        available: Connection status (True if connected and reachable)
        speaker_type: Type of speaker for crossover configuration
        crossover_frequency: Crossover frequency in Hz (for subwoofer routing)
        volume_db: Current volume in dB
        mute: Mute status
        last_seen: Timestamp of last activity
    """
    dsp_id: str
    snapcast_id: str
    name: str
    host: str
    ip: str
    available: bool = True
    speaker_type: SpeakerType = DEFAULT_SPEAKER_TYPE
    crossover_frequency: int = DEFAULT_CROSSOVER_FREQUENCY
    volume_db: float = DEFAULT_VOLUME_DB
    mute: bool = False
    last_seen: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "dsp_id": self.dsp_id,
            "snapcast_id": self.snapcast_id,
            "name": self.name,
            "host": self.host,
            "ip": self.ip,
            "available": self.available,
            "speaker_type": self.speaker_type,
            "crossover_frequency": self.crossover_frequency,
            "volume_db": self.volume_db,
            "mute": self.mute,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'RegisteredClient':
        """Create from dictionary."""
        last_seen = data.get("last_seen")
        if isinstance(last_seen, str):
            last_seen = datetime.fromisoformat(last_seen)
        elif last_seen is None:
            last_seen = datetime.utcnow()

        return cls(
            dsp_id=data["dsp_id"],
            snapcast_id=data.get("snapcast_id", ""),
            name=data.get("name", data["dsp_id"]),
            host=data.get("host", ""),
            ip=data.get("ip", ""),
            available=data.get("available", True),
            speaker_type=data.get("speaker_type", DEFAULT_SPEAKER_TYPE),
            crossover_frequency=data.get("crossover_frequency", DEFAULT_CROSSOVER_FREQUENCY),
            volume_db=data.get("volume_db", DEFAULT_VOLUME_DB),
            mute=data.get("mute", False),
            last_seen=last_seen
        )


@dataclass
class Zone:
    """
    Zone (linked group) configuration.

    A zone groups multiple clients together for synchronized DSP settings
    and volume control.

    Attributes:
        id: Unique zone identifier
        name: Display name for UI
        client_ids: List of dsp_ids belonging to this zone
        crossover_frequency: Zone-wide crossover frequency
        crossover_enabled: Whether crossover is active for this zone
    """
    id: str
    name: str
    client_ids: List[str] = field(default_factory=list)
    crossover_frequency: int = DEFAULT_CROSSOVER_FREQUENCY
    crossover_enabled: bool = True

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "client_ids": self.client_ids.copy(),
            "crossover_frequency": self.crossover_frequency,
            "crossover_enabled": self.crossover_enabled
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Zone':
        """Create from dictionary."""
        return cls(
            id=data["id"],
            name=data.get("name", data["id"]),
            client_ids=data.get("client_ids", []).copy(),
            crossover_frequency=data.get("crossover_frequency", DEFAULT_CROSSOVER_FREQUENCY),
            crossover_enabled=data.get("crossover_enabled", True)
        )


@dataclass
class RegistryState:
    """
    Complete registry state snapshot.

    Used for initial state sync and persistence.
    """
    clients: Dict[str, RegisteredClient] = field(default_factory=dict)
    zones: Dict[str, Zone] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "clients": {k: v.to_dict() for k, v in self.clients.items()},
            "zones": {k: v.to_dict() for k, v in self.zones.items()}
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'RegistryState':
        """Create from dictionary."""
        clients = {
            k: RegisteredClient.from_dict(v)
            for k, v in data.get("clients", {}).items()
        }
        zones = {
            k: Zone.from_dict(v)
            for k, v in data.get("zones", {}).items()
        }
        return cls(clients=clients, zones=zones)


# Event types emitted by ClientRegistryService
class RegistryEventType:
    """Registry event type constants."""
    CLIENT_REGISTERED = "client_registered"
    CLIENT_UNREGISTERED = "client_unregistered"
    CLIENT_UPDATED = "client_updated"
    AVAILABILITY_CHANGED = "availability_changed"
    VOLUME_CHANGED = "volume_changed"
    SPEAKER_TYPE_CHANGED = "speaker_type_changed"
    ZONE_CREATED = "zone_created"
    ZONE_DELETED = "zone_deleted"
    ZONE_UPDATED = "zone_updated"
    ZONE_CLIENT_ADDED = "zone_client_added"
    ZONE_CLIENT_REMOVED = "zone_client_removed"
