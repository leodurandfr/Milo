# backend/core/multiroom/models.py
"""
Domain models for multiroom client and zone management.

This module defines the core data structures for managing multiroom clients
and zones. These models are the single source of truth for client state
throughout the application.

Architecture: Uses mac_id as the single unique identifier for clients.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Literal, Any
from enum import Enum
import uuid

from backend.config.constants import DEFAULT_VOLUME_DB


# =============================================================================
# DSP Types and Constants
# =============================================================================

class FilterType(str, Enum):
    """Supported filter types for parametric EQ"""
    PEAKING = "Peaking"
    LOWSHELF = "Lowshelf"
    HIGHSHELF = "Highshelf"
    LOWPASS = "Lowpass"
    HIGHPASS = "Highpass"
    NOTCH = "Notch"
    ALLPASS = "Allpass"


# Default EQ frequencies for 10-band parametric equalizer
DEFAULT_EQ_FREQUENCIES = [31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]


# =============================================================================
# EqFilter Model
# =============================================================================

@dataclass
class EqFilter:
    """
    EQ filter configuration for parametric equalizer.

    Represents a single EQ band with frequency, gain, Q factor, and filter type.
    Validation boundaries (enforced at API layer):
    - frequency: 20-20000 Hz
    - gain: -15 to +15 dB
    - Q: 0.1 to 10.0

    Attributes:
        id: Unique filter identifier (e.g., "eq_band_00")
        frequency: Center frequency in Hz
        gain: Gain adjustment in dB
        q: Q factor (bandwidth)
        filter_type: Type of filter (Peaking, Lowshelf, etc.)
        enabled: Whether the filter is active
    """
    id: str
    frequency: int
    gain: float = 0.0
    q: float = 1.41  # Default Q for Butterworth response
    filter_type: FilterType = FilterType.PEAKING
    enabled: bool = True

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "frequency": self.frequency,
            "gain": self.gain,
            "q": self.q,
            "filter_type": self.filter_type.value,
            "enabled": self.enabled
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'EqFilter':
        """
        Create from dictionary.

        Handles backward compatibility with old format:
        - "freq" -> "frequency"
        - "type" -> "filter_type"
        """
        if data is None:
            raise ValueError("Cannot create EqFilter from None")

        # Handle old key names for backward compatibility
        frequency = data.get("frequency", data.get("freq", 1000))
        filter_type_str = data.get("filter_type", data.get("type", "Peaking"))

        # Convert string to FilterType enum
        try:
            filter_type = FilterType(filter_type_str)
        except ValueError:
            filter_type = FilterType.PEAKING

        return cls(
            id=data["id"],
            frequency=frequency,
            gain=data.get("gain", 0.0),
            q=data.get("q", 1.41),
            filter_type=filter_type,
            enabled=data.get("enabled", True)
        )


# =============================================================================
# CompressorSettings Model
# =============================================================================

@dataclass
class CompressorSettings:
    """
    Compressor settings for dynamic range control.

    Default values match CamillaDSPService internal defaults.
    Validation boundaries (enforced at API layer):
    - threshold: -60 to 0 dB
    - ratio: 1 to 20
    - attack: 0.1 to 100 ms
    - release: 10 to 1000 ms
    - makeup_gain: 0 to 30 dB

    Attributes:
        enabled: Whether compressor is active
        threshold: Compression threshold in dB
        ratio: Compression ratio (e.g., 4.0 means 4:1)
        attack: Attack time in milliseconds
        release: Release time in milliseconds
        makeup_gain: Output gain compensation in dB
    """
    enabled: bool = False
    threshold: float = -20.0
    ratio: float = 4.0
    attack: float = 10.0
    release: float = 100.0
    makeup_gain: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "enabled": self.enabled,
            "threshold": self.threshold,
            "ratio": self.ratio,
            "attack": self.attack,
            "release": self.release,
            "makeup_gain": self.makeup_gain
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> 'CompressorSettings':
        """Create from dictionary. Returns default instance for None input."""
        if data is None:
            return cls()
        return cls(
            enabled=data.get("enabled", False),
            threshold=data.get("threshold", -20.0),
            ratio=data.get("ratio", 4.0),
            attack=data.get("attack", 10.0),
            release=data.get("release", 100.0),
            makeup_gain=data.get("makeup_gain", 0.0)
        )


# =============================================================================
# LoudnessSettings Model
# =============================================================================

@dataclass
class LoudnessSettings:
    """
    Loudness compensation settings.

    Applies frequency-dependent boost at low listening levels to compensate
    for reduced ear sensitivity to bass and treble.
    Default values match CamillaDSPService internal defaults.
    Validation boundaries (enforced at API layer):
    - high_boost: 0 to 15 dB
    - low_boost: 0 to 15 dB

    Attributes:
        enabled: Whether loudness compensation is active
        high_boost: Treble boost amount in dB
        low_boost: Bass boost amount in dB
    """
    enabled: bool = False
    high_boost: float = 5.0
    low_boost: float = 8.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "enabled": self.enabled,
            "high_boost": self.high_boost,
            "low_boost": self.low_boost
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> 'LoudnessSettings':
        """Create from dictionary. Returns default instance for None input."""
        if data is None:
            return cls()
        return cls(
            enabled=data.get("enabled", False),
            high_boost=data.get("high_boost", 5.0),
            low_boost=data.get("low_boost", 8.0)
        )


# Speaker types for crossover configuration
SpeakerType = Literal['satellite', 'bookshelf', 'tower', 'subwoofer']

# Valid speaker types list
SPEAKER_TYPES = ['satellite', 'bookshelf', 'tower', 'subwoofer']

# Default values
DEFAULT_SPEAKER_TYPE: SpeakerType = 'bookshelf'

# Zone constants (validation happens in API layer Pydantic models)
MAX_ZONE_NAME_LENGTH = 15

# Default crossover frequencies (highpass) per speaker type in Hz
DEFAULT_CROSSOVER_FREQUENCIES = {
    'satellite': 120,   # Small speakers, limited bass (~120 Hz)
    'bookshelf': 80,    # Medium speakers (THX standard)
    'tower': 50,        # Full-range speakers (~40-50 Hz response)
    'subwoofer': None   # No highpass for subwoofer (receives lowpass)
}


@dataclass
class DspSettings:
    """
    DSP settings for a zone or standalone client.

    Contains all audio processing settings that are shared within a zone
    or stored individually for standalone clients.

    Attributes:
        enabled: Global DSP bypass toggle (True = DSP active, False = bypassed)
        filters: List of EQ filter configurations (typed EqFilter objects)
        compressor: Compressor settings
        loudness: Loudness compensation settings
        active_preset: Currently active EQ preset ID ("manual" or preset name)
    """
    enabled: bool = True
    filters: List[EqFilter] = field(default_factory=list)
    compressor: CompressorSettings = field(default_factory=CompressorSettings)
    loudness: LoudnessSettings = field(default_factory=LoudnessSettings)
    active_preset: Optional[str] = "manual"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "enabled": self.enabled,
            "filters": [f.to_dict() for f in self.filters],
            "compressor": self.compressor.to_dict(),
            "loudness": self.loudness.to_dict(),
            "active_preset": self.active_preset
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> 'DspSettings':
        """
        Create from dictionary.

        Handles backward compatibility with old format where:
        - filters was List[Dict] instead of List[EqFilter]
        - compressor/loudness could be None or Dict
        - 'enabled' field might be missing
        """
        if data is None:
            return cls()

        # Parse filters - handle both old Dict format and new EqFilter format
        filters_data = data.get("filters", [])
        filters = []
        for f_data in filters_data:
            if isinstance(f_data, EqFilter):
                filters.append(f_data)
            elif isinstance(f_data, dict):
                filters.append(EqFilter.from_dict(f_data))

        # Parse compressor and loudness
        compressor = CompressorSettings.from_dict(data.get("compressor"))
        loudness = LoudnessSettings.from_dict(data.get("loudness"))

        return cls(
            enabled=data.get("enabled", True),
            filters=filters,
            compressor=compressor,
            loudness=loudness,
            active_preset=data.get("active_preset", "manual")
        )

    @classmethod
    def default(cls) -> 'DspSettings':
        """
        Create default DSP settings with flat 10-band EQ.

        Returns a "flat" configuration:
        - enabled=True (DSP active)
        - 10 EQ bands at standard frequencies, all at 0 dB gain
        - compressor disabled
        - loudness disabled
        """
        # Create 10-band parametric EQ with flat (0 dB) gains
        filters = [
            EqFilter(
                id=f"eq_band_{i:02d}",
                frequency=freq,
                gain=0.0,
                q=1.41,
                filter_type=FilterType.PEAKING,
                enabled=True
            )
            for i, freq in enumerate(DEFAULT_EQ_FREQUENCIES)
        ]

        return cls(
            enabled=True,
            filters=filters,
            compressor=CompressorSettings(),  # disabled by default
            loudness=LoudnessSettings(),      # disabled by default
            active_preset="manual"            # manual preset by default
        )


@dataclass
class Client:
    """
    Complete client information - single source of truth.

    Attributes:
        mac_id: Primary identifier (MAC address, format xx:xx:xx:xx:xx:xx)
        name: Display name for UI
        ip: IP address (127.0.0.1 for local client)
        online: Connection status (True if connected to Snapcast)
        zone_id: ID of zone membership (None if standalone)
        volume_db: Current volume in dB
        mute: Mute status
        speaker_type: Type of speaker for crossover configuration
        crossover_frequency: Custom crossover frequency in Hz (overrides speaker_type default)

    Properties:
        is_local: True if this is the local client (ip == "127.0.0.1")
    """
    mac_id: str
    name: str
    ip: str
    host: str = ""  # Hostname from Snapcast
    online: bool = False
    zone_id: Optional[str] = None
    volume_db: float = DEFAULT_VOLUME_DB
    mute: bool = False
    speaker_type: SpeakerType = DEFAULT_SPEAKER_TYPE
    crossover_frequency: Optional[int] = None  # None = use speaker_type default

    def to_dict(self, include_runtime: bool = True) -> Dict[str, Any]:
        """
        Convert to dictionary for serialization.

        Args:
            include_runtime: If True, includes runtime fields (online).
                           Set False for persistence (settings.json).

        Returns:
            Complete client dictionary with all fields for WebSocket events,
            or persistence-only fields when include_runtime=False.
        """
        result = {
            "mac_id": self.mac_id,
            "name": self.name,
            "ip": self.ip,
            "host": self.host,
            "zone_id": self.zone_id,
            "volume_db": self.volume_db,
            "mute": self.mute,
            "speaker_type": self.speaker_type,
            "crossover_frequency": self.crossover_frequency
        }
        if include_runtime:
            result["online"] = self.online
            result["is_local"] = self.is_local
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Client':
        """Create from dictionary."""
        return cls(
            mac_id=data["mac_id"],
            name=data.get("name", data["mac_id"]),
            ip=data.get("ip", ""),
            host=data.get("host", ""),
            online=data.get("online", False),
            zone_id=data.get("zone_id"),
            volume_db=data.get("volume_db", DEFAULT_VOLUME_DB),
            mute=data.get("mute", False),
            speaker_type=data.get("speaker_type", DEFAULT_SPEAKER_TYPE),
            crossover_frequency=data.get("crossover_frequency")
        )

    @property
    def is_local(self) -> bool:
        """Check if this is the local client (running on this device)."""
        return self.ip == "127.0.0.1"

    def is_standalone(self) -> bool:
        """Check if client is standalone (not in a zone)."""
        return self.zone_id is None

    def is_in_zone(self) -> bool:
        """Check if client is in a zone."""
        return self.zone_id is not None


@dataclass
class Zone:
    """
    Zone (linked group) configuration.

    A zone groups multiple clients together for synchronized DSP settings.
    Volume remains independent per client within a zone.

    Attributes:
        id: Unique zone identifier (UUID v4, auto-generated if not provided)
        name: Display name for UI (max 15 characters, validated at API boundary)
        client_ids: List of mac_ids belonging to this zone
        dsp_settings: Shared DSP settings for all zone members
        crossover_frequency: Crossover frequency in Hz (default 80Hz THX standard)
        crossover_enabled: Whether crossover filtering is active for this zone
    """
    name: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    client_ids: List[str] = field(default_factory=list)
    dsp_settings: DspSettings = field(default_factory=DspSettings)
    crossover_frequency: Optional[int] = 80  # Default THX standard
    crossover_enabled: Optional[bool] = None  # None = auto (depends on subwoofer presence)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "client_ids": self.client_ids.copy(),
            "dsp_settings": self.dsp_settings.to_dict(),
            "crossover_frequency": self.crossover_frequency,
            "crossover_enabled": self.crossover_enabled
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Zone':
        """Create from dictionary."""
        dsp_data = data.get("dsp_settings")
        dsp_settings = DspSettings.from_dict(dsp_data) if dsp_data else DspSettings()

        return cls(
            name=data.get("name", data["id"]),
            id=data["id"],
            client_ids=data.get("client_ids", []).copy(),
            dsp_settings=dsp_settings,
            crossover_frequency=data.get("crossover_frequency", 80),
            crossover_enabled=data.get("crossover_enabled")
        )

    def has_client(self, mac_id: str) -> bool:
        """Check if a client is in this zone."""
        return mac_id in self.client_ids

    def client_count(self) -> int:
        """Get number of clients in zone."""
        return len(self.client_ids)

    def is_valid(self) -> bool:
        """Check if zone has minimum required clients (2+)."""
        return len(self.client_ids) >= 2


@dataclass
class RegistryState:
    """
    Complete registry state snapshot.

    Used for initial state sync and persistence.
    """
    clients: Dict[str, Client] = field(default_factory=dict)
    zones: Dict[str, Zone] = field(default_factory=dict)
    standalone_dsp: Dict[str, DspSettings] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "clients": {k: v.to_dict() for k, v in self.clients.items()},
            "zones": {k: v.to_dict() for k, v in self.zones.items()},
            "standalone_dsp": {k: v.to_dict() for k, v in self.standalone_dsp.items()}
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'RegistryState':
        """Create from dictionary."""
        clients = {
            k: Client.from_dict(v)
            for k, v in data.get("clients", {}).items()
        }
        zones = {
            k: Zone.from_dict(v)
            for k, v in data.get("zones", {}).items()
        }
        standalone_dsp = {
            k: DspSettings.from_dict(v)
            for k, v in data.get("standalone_dsp", {}).items()
        }
        return cls(clients=clients, zones=zones, standalone_dsp=standalone_dsp)


class ReconnectionContext(str, Enum):
    """
    Context for client reconnection sync strategy selection.

    Determines which volume and DSP sync strategy to apply when a client
    reconnects to the system. Based on FR7-FR10 from PRD.

    Attributes:
        IN_ZONE_OTHERS_ONLINE: Client in zone with other zone members ONLINE (FR7)
            - Volume: zone average from online members
            - DSP: zone.dsp_settings
        IN_ZONE_ALL_OFFLINE: Client in zone but all other zone members OFFLINE (FR8)
            - Volume: startup_volume_db (DEFAULT_VOLUME_DB)
            - DSP: zone.dsp_settings (from persistence)
        STANDALONE_OTHERS_ONLINE: Standalone client with other clients ONLINE globally (FR9)
            - Volume: global average from all online clients
            - DSP: standalone_dsp[mac_id]
        STANDALONE_ALONE: Standalone client with no other clients ONLINE (FR10)
            - Volume: startup_volume_db (DEFAULT_VOLUME_DB)
            - DSP: standalone_dsp[mac_id]
    """
    IN_ZONE_OTHERS_ONLINE = "in_zone_others_online"      # FR7
    IN_ZONE_ALL_OFFLINE = "in_zone_all_offline"          # FR8
    STANDALONE_OTHERS_ONLINE = "standalone_others_online"  # FR9
    STANDALONE_ALONE = "standalone_alone"                # FR10


class RegistryEventType:
    """Registry event type constants."""
    # Client events
    CLIENT_CONNECTED = "client_connected"
    CLIENT_DISCONNECTED = "client_disconnected"
    CLIENT_UPDATED = "client_updated"

    # Zone events
    ZONE_CREATED = "zone_created"
    ZONE_DELETED = "zone_deleted"
    ZONE_UPDATED = "zone_updated"
    ZONE_CLIENT_ADDED = "zone_client_added"
    ZONE_CLIENT_REMOVED = "zone_client_removed"

    # Volume events (emitted for frontend updates)
    VOLUME_CHANGED = "volume_changed"

    # Speaker type event
    SPEAKER_TYPE_CHANGED = "speaker_type_changed"

    # DSP events
    DSP_SETTINGS_CHANGED = "dsp_settings_changed"
    CROSSOVER_CHANGED = "crossover_changed"
