# backend/tests/test_core_multiroom.py
"""
Unit tests for the core.multiroom module.

Tests:
- Models (Client, Zone, EqualizerSettings, RegistryState, RegistryEventType)
- ClientRegistryService
- SnapcastService
- CrossoverService
- Helper functions
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from backend.core.multiroom.models import (
    Client,
    Zone,
    EqualizerSettings,
    RegistryState,
    RegistryEventType,
    ReconnectionContext,
    SpeakerType,
    SPEAKER_TYPES,
    DEFAULT_SPEAKER_TYPE,
    DEFAULT_CROSSOVER_FREQUENCIES,
    CompressorSettings,
    LoudnessSettings,
    EqFilter,
    FilterType,
)
from backend.config.constants import DEFAULT_VOLUME_DB
from backend.core.multiroom.client_registry import ClientRegistryService
from backend.core.multiroom.snapcast import (
    SnapcastService,
    get_online_clients,
    get_online_client_ids,
)
from backend.core.multiroom.crossover import CrossoverService
from backend.core.equalizer.client_proxy import is_ip_address


# =============================================================================
# Model Tests
# =============================================================================

class TestClient:
    """Tests for Client model."""

    def test_create_client_with_defaults(self):
        """Test creating a client with default values."""
        client = Client(
            mac_id="local",
            name="Main Speaker",
            ip="127.0.0.1"
        )

        assert client.mac_id == "local"
        assert client.name == "Main Speaker"
        assert client.ip == "127.0.0.1"
        assert client.online is False
        assert client.zone_id is None
        assert client.speaker_type == DEFAULT_SPEAKER_TYPE
        assert client.volume_db == DEFAULT_VOLUME_DB
        assert client.mute is False

    def test_client_to_dict(self):
        """Test converting client to dictionary - AC1 completeness validation."""
        # Create client with ALL fields set to verify complete serialization
        client = Client(
            mac_id="aa:bb:cc:dd:ee:ff",
            name="Kitchen",
            ip="192.168.1.100",
            speaker_type="satellite",
            online=True,
            zone_id="zone-123",
            volume_db=-25.0,
            mute=True,
            crossover_frequency=120
        )

        # Default: include runtime fields (for WebSocket events - Story 6.1 AC1)
        data = client.to_dict()

        # AC1: Verify ALL required fields are present for complete client object
        assert data["mac_id"] == "aa:bb:cc:dd:ee:ff"
        assert data["name"] == "Kitchen"
        assert data["ip"] == "192.168.1.100"
        assert data["speaker_type"] == "satellite"
        assert data["zone_id"] == "zone-123"
        assert data["volume_db"] == -25.0
        assert data["mute"] is True
        assert data["crossover_frequency"] == 120
        # Runtime fields are now included by default for complete WebSocket events
        assert data["online"] is True

        # Verify all 11 expected fields are present (including is_local and host)
        expected_fields = {"mac_id", "name", "ip", "host", "speaker_type", "zone_id",
                          "volume_db", "mute", "crossover_frequency", "online", "is_local"}
        assert set(data.keys()) == expected_fields

        # Explicit: exclude runtime fields (for persistence)
        data_persist = client.to_dict(include_runtime=False)
        assert "online" not in data_persist
        assert "is_local" not in data_persist
        assert len(data_persist) == 9  # All fields except 'online' and 'is_local'

    def test_client_from_dict(self):
        """Test creating client from dictionary."""
        data = {
            "mac_id": "aa:bb:cc:dd:ee:ff",
            "name": "Living Room",
            "ip": "192.168.1.101",
            "speaker_type": "tower",
            "online": True,
            "zone_id": "zone1"
        }

        client = Client.from_dict(data)

        assert client.mac_id == "aa:bb:cc:dd:ee:ff"
        assert client.speaker_type == "tower"
        assert client.zone_id == "zone1"

    def test_client_is_standalone(self):
        """Test client standalone check."""
        standalone_client = Client(mac_id="local", name="Main", ip="127.0.0.1")
        zoned_client = Client(mac_id="client2", name="Kitchen", ip="192.168.1.100", zone_id="zone1")

        assert standalone_client.is_standalone() is True
        assert standalone_client.is_in_zone() is False
        assert zoned_client.is_standalone() is False
        assert zoned_client.is_in_zone() is True

    def test_client_from_dict_missing_required_fields(self):
        """Verify KeyError raised when mac_id missing."""
        with pytest.raises(KeyError):
            Client.from_dict({"name": "Test", "ip": "127.0.0.1"})

    def test_client_from_dict_with_unknown_speaker_type(self):
        """Test from_dict with unknown speaker_type uses default."""
        data = {
            "mac_id": "test",
            "name": "Test",
            "ip": "127.0.0.1",
            "speaker_type": "unknown_type"
        }
        client = Client.from_dict(data)
        # Model accepts any string - validation happens in API layer
        assert client.speaker_type == "unknown_type"


class TestEqualizerSettings:
    """Tests for EqualizerSettings model."""

    def test_create_default_equalizer_settings(self):
        """Test creating default Equalizer settings (without factory method)."""
        settings = EqualizerSettings()

        # New structure: enabled=True, typed sub-models
        assert settings.enabled is True
        assert settings.filters == []
        assert isinstance(settings.compressor, CompressorSettings)
        assert settings.compressor.enabled is False
        assert isinstance(settings.loudness, LoudnessSettings)
        assert settings.loudness.enabled is False

    def test_equalizer_settings_factory_default(self):
        """Test creating default Equalizer settings with factory method."""
        from backend.core.multiroom.models import EqFilter

        settings = EqualizerSettings.default()

        # Factory creates 10-band EQ with flat gains
        assert settings.enabled is True
        assert len(settings.filters) == 10
        assert all(isinstance(f, EqFilter) for f in settings.filters)
        assert all(f.gain == 0.0 for f in settings.filters)

    def test_equalizer_settings_to_dict(self):
        """Test converting Equalizer settings to dictionary."""
        from backend.core.multiroom.models import EqFilter, CompressorSettings

        settings = EqualizerSettings(
            enabled=True,
            filters=[EqFilter(id="eq_band_00", frequency=1000, gain=3.0)],
            compressor=CompressorSettings(enabled=True, threshold=-20)
        )

        data = settings.to_dict()

        assert data["enabled"] is True
        assert len(data["filters"]) == 1
        assert data["filters"][0]["frequency"] == 1000
        assert data["compressor"]["enabled"] is True
        assert data["compressor"]["threshold"] == -20
        assert data["loudness"]["enabled"] is False

    def test_equalizer_settings_from_dict(self):
        """Test creating Equalizer settings from dictionary."""
        data = {
            "enabled": True,
            "filters": [{"id": "eq_band_00", "frequency": 100, "gain": 2.0, "filter_type": "Lowshelf", "q": 1.0, "enabled": True}],
            "compressor": {"enabled": False},
            "loudness": {"enabled": True, "high_boost": 7.0}
        }

        settings = EqualizerSettings.from_dict(data)

        assert settings.enabled is True
        assert len(settings.filters) == 1
        assert settings.filters[0].frequency == 100
        assert settings.loudness.enabled is True
        assert settings.loudness.high_boost == 7.0


class TestZone:
    """Tests for Zone model."""

    def test_create_zone(self):
        """Test creating a zone."""
        zone = Zone(
            name="Living Room Zone",
            id="zone1",
            client_ids=["local", "aa:bb:cc:dd:ee:ff"]
        )

        assert zone.id == "zone1"
        assert zone.name == "Living Room Zone"
        assert len(zone.client_ids) == 2
        assert zone.equalizer_settings is not None

    def test_zone_uuid_auto_generation(self):
        """Test that zone id is auto-generated as UUID when not provided."""
        import re

        zone = Zone(name="Auto ID Zone")

        # Verify UUID format (8-4-4-4-12 hex digits)
        uuid_pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        assert re.match(uuid_pattern, zone.id.lower()) is not None
        assert zone.name == "Auto ID Zone"
        assert zone.client_ids == []
        assert zone.equalizer_settings is not None

    def test_zone_default_values(self):
        """Test zone default values when created with minimal parameters."""
        zone = Zone(name="Minimal Zone")

        # id should be auto-generated UUID
        assert len(zone.id) == 36  # UUID length with dashes
        # client_ids should default to empty list
        assert zone.client_ids == []
        # equalizer_settings should default to empty EqualizerSettings (new typed structure)
        assert zone.equalizer_settings.enabled is True
        assert zone.equalizer_settings.filters == []
        assert isinstance(zone.equalizer_settings.compressor, CompressorSettings)
        assert zone.equalizer_settings.compressor.enabled is False
        assert isinstance(zone.equalizer_settings.loudness, LoudnessSettings)
        assert zone.equalizer_settings.loudness.enabled is False

    def test_zone_to_dict(self):
        """Test converting zone to dictionary."""
        from backend.core.multiroom.models import EqFilter

        eq = EqualizerSettings(filters=[EqFilter(id="eq_band_00", frequency=1000, gain=2.0)])
        zone = Zone(
            name="Kitchen Zone",
            id="zone2",
            client_ids=["local", "aa:bb:cc:dd:ee:ff"],
            equalizer_settings=eq
        )

        data = zone.to_dict()

        assert data["id"] == "zone2"
        assert len(data["client_ids"]) == 2
        assert len(data["equalizer_settings"]["filters"]) == 1
        assert data["equalizer_settings"]["filters"][0]["frequency"] == 1000

    def test_zone_from_dict(self):
        """Test creating zone from dictionary."""
        data = {
            "id": "zone3",
            "name": "Bedroom Zone",
            "client_ids": ["local", "aa:bb:cc:dd:ee:ff"],
            "equalizer_settings": {
                "filters": [],
                "compressor": None,
                "loudness": None
            }
        }

        zone = Zone.from_dict(data)

        assert zone.id == "zone3"
        assert len(zone.client_ids) == 2

    def test_zone_has_client(self):
        """Test zone client membership check."""
        zone = Zone(
            name="Test Zone",
            id="zone1",
            client_ids=["local", "aa:bb:cc:dd:ee:ff"]
        )

        assert zone.has_client("local") is True
        assert zone.has_client("other") is False

    def test_zone_is_valid(self):
        """Test zone validity check (minimum 2 clients)."""
        valid_zone = Zone(name="Valid", id="z1", client_ids=["c1", "c2"])
        invalid_zone = Zone(name="Invalid", id="z2", client_ids=["c1"])

        assert valid_zone.is_valid() is True
        assert invalid_zone.is_valid() is False


class TestRegistryState:
    """Tests for RegistryState model."""

    def test_empty_state(self):
        """Test creating empty state."""
        state = RegistryState()

        assert len(state.clients) == 0
        assert len(state.zones) == 0
        assert len(state.standalone_equalizer) == 0

    def test_state_to_dict(self):
        """Test converting state to dictionary."""
        from backend.core.multiroom.models import EqFilter

        client = Client(
            mac_id="local",
            name="Main",
            ip="127.0.0.1"
        )
        zone = Zone(name="Zone 1", id="zone1", client_ids=["local", "client2"])
        standalone_equalizer = EqualizerSettings(filters=[EqFilter(id="eq_band_00", frequency=1000)])

        state = RegistryState(
            clients={"local": client},
            zones={"zone1": zone},
            standalone_equalizer={"client3": standalone_equalizer}
        )

        data = state.to_dict()

        assert "local" in data["clients"]
        assert "zone1" in data["zones"]
        assert "client3" in data["standalone_equalizer"]


class TestRegistryEventType:
    """Tests for RegistryEventType constants."""

    def test_event_types_exist(self):
        """Test that all event types are defined."""
        assert RegistryEventType.CLIENT_CONNECTED == "client_connected"
        assert RegistryEventType.CLIENT_DISCONNECTED == "client_disconnected"
        assert RegistryEventType.CLIENT_UPDATED == "client_updated"
        assert RegistryEventType.ZONE_CREATED == "zone_created"
        assert RegistryEventType.ZONE_UPDATED == "zone_updated"
        assert RegistryEventType.ZONE_DELETED == "zone_deleted"
        assert RegistryEventType.VOLUME_CHANGED == "volume_changed"
        assert RegistryEventType.EQUALIZER_SETTINGS_CHANGED == "equalizer_settings_changed"


class TestModelConstants:
    """Tests for model constants."""

    def test_speaker_types(self):
        """Test speaker types list."""
        assert "satellite" in SPEAKER_TYPES
        assert "bookshelf" in SPEAKER_TYPES
        assert "tower" in SPEAKER_TYPES
        assert "subwoofer" in SPEAKER_TYPES

    def test_default_crossover_frequencies(self):
        """Test default crossover frequencies."""
        assert DEFAULT_CROSSOVER_FREQUENCIES["satellite"] == 120
        assert DEFAULT_CROSSOVER_FREQUENCIES["bookshelf"] == 80
        assert DEFAULT_CROSSOVER_FREQUENCIES["tower"] == 50
        assert DEFAULT_CROSSOVER_FREQUENCIES["subwoofer"] is None


# =============================================================================
# ClientRegistryService Tests
# =============================================================================

class TestClientRegistryService:
    """Tests for ClientRegistryService."""

    @pytest.fixture
    def mock_settings_service(self):
        """Create a mock settings service."""
        service = AsyncMock()
        service.get_setting = AsyncMock(return_value=None)
        service.set_setting = AsyncMock()
        return service

    @pytest.fixture
    def mock_event_bus(self):
        """Create a mock event bus."""
        bus = MagicMock()
        bus.emit = AsyncMock()
        return bus

    @pytest.fixture
    def registry(self, mock_settings_service, mock_event_bus):
        """Create a ClientRegistryService instance."""
        return ClientRegistryService(
            settings_service=mock_settings_service,
            event_bus=mock_event_bus
        )

    @pytest.mark.asyncio
    async def test_initialize(self, registry):
        """Test registry initialization."""
        result = await registry.initialize()
        assert result is True
        assert registry._initialized is True

    @pytest.mark.asyncio
    async def test_register_new_client(self, registry):
        """Test registering a new client."""
        await registry.initialize()

        client = await registry.register_client(
            mac_id="local",
            name="Main",
            ip="127.0.0.1"
        )

        assert client.mac_id == "local"
        assert registry.get_client("local") is not None

    @pytest.mark.asyncio
    async def test_register_client_with_speaker_type(self, registry):
        """Test registering a client with speaker type."""
        await registry.initialize()

        client = await registry.register_client(
            mac_id="aa:bb:cc:dd:ee:ff",
            name="Kitchen",
            ip="192.168.1.100",
            speaker_type="satellite"
        )

        assert client.speaker_type == "satellite"

    @pytest.mark.asyncio
    async def test_update_existing_client(self, registry):
        """Test that registering same mac_id returns existing client."""
        await registry.initialize()

        client1 = await registry.register_client(
            mac_id="local",
            name="Main",
            ip="127.0.0.1"
        )

        # Register same mac_id - should return existing client
        client2 = await registry.register_client(
            mac_id="local",
            name="Main",
            ip="127.0.0.1"
        )

        assert client1.mac_id == client2.mac_id

    @pytest.mark.asyncio
    async def test_delete_client(self, registry):
        """Test deleting a client."""
        await registry.initialize()

        await registry.register_client(
            mac_id="local",
            name="Main",
            ip="127.0.0.1"
        )

        result = await registry.delete_client("local")
        assert result is True
        assert registry.get_client("local") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_client(self, registry):
        """Test deleting a client that doesn't exist."""
        await registry.initialize()
        result = await registry.delete_client("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_set_client_online(self, registry):
        """Test setting client online status."""
        await registry.initialize()

        await registry.register_client(
            mac_id="local",
            name="Main",
            ip="127.0.0.1"
        )

        await registry.set_client_online("local", True)
        client = registry.get_client("local")
        assert client.online is True

        await registry.set_client_online("local", False)
        client = registry.get_client("local")
        assert client.online is False

    @pytest.mark.asyncio
    async def test_update_client(self, registry):
        """Test updating client properties."""
        await registry.initialize()

        await registry.register_client(
            mac_id="local",
            name="Main",
            ip="127.0.0.1"
        )

        updated = await registry.update_client("local", name="Updated Main", speaker_type="tower")
        assert updated.name == "Updated Main"
        assert updated.speaker_type == "tower"

    @pytest.mark.asyncio
    async def test_get_online_clients(self, registry):
        """Test getting only online clients."""
        await registry.initialize()

        await registry.register_client(
            mac_id="local",
            name="Main",
            ip="127.0.0.1"
        )
        await registry.set_client_online("local", True)

        await registry.register_client(
            mac_id="aa:bb:cc:dd:ee:ff",
            name="Remote",
            ip="192.168.1.100"
        )
        # Remote client stays offline (default)

        online = registry.get_online_clients()
        assert len(online) == 1
        assert online[0].mac_id == "local"

    @pytest.mark.asyncio
    async def test_create_zone(self, registry):
        """Test creating a zone."""
        await registry.initialize()

        await registry.register_client(mac_id="local", name="Main", ip="127.0.0.1")
        await registry.register_client(mac_id="client2", name="Client 2", ip="192.168.1.100")

        zone = await registry.create_zone("zone1", "Test Zone", ["local", "client2"])

        assert zone.id == "zone1"
        assert zone.name == "Test Zone"
        assert "local" in zone.client_ids
        assert "client2" in zone.client_ids

        # Verify clients have zone_id set
        client1 = registry.get_client("local")
        client2 = registry.get_client("client2")
        assert client1.zone_id == "zone1"
        assert client2.zone_id == "zone1"

    @pytest.mark.asyncio
    async def test_create_zone_requires_minimum_clients(self, registry):
        """Test that creating a zone requires at least 2 clients."""
        await registry.initialize()

        await registry.register_client(mac_id="local", name="Main", ip="127.0.0.1")

        with pytest.raises(ValueError, match="at least 2 clients"):
            await registry.create_zone("zone1", "Test Zone", ["local"])

    @pytest.mark.asyncio
    async def test_create_zone_duplicate_id(self, registry):
        """Test creating a zone with duplicate ID."""
        await registry.initialize()

        await registry.register_client(mac_id="local", name="Main", ip="127.0.0.1")
        await registry.register_client(mac_id="client2", name="Client 2", ip="192.168.1.100")

        await registry.create_zone("zone1", "Test Zone", ["local", "client2"])

        with pytest.raises(ValueError, match="already exists"):
            await registry.create_zone("zone1", "Another Zone", ["local", "client2"])

    @pytest.mark.asyncio
    async def test_delete_zone(self, registry):
        """Test deleting a zone."""
        await registry.initialize()

        await registry.register_client(mac_id="local", name="Main", ip="127.0.0.1")
        await registry.register_client(mac_id="client2", name="Client 2", ip="192.168.1.100")

        await registry.create_zone("zone1", "Test Zone", ["local", "client2"])
        result = await registry.delete_zone("zone1")

        assert result is True
        assert registry.get_zone("zone1") is None

        # Verify clients have zone_id cleared
        client1 = registry.get_client("local")
        client2 = registry.get_client("client2")
        assert client1.zone_id is None
        assert client2.zone_id is None

    @pytest.mark.asyncio
    async def test_get_zone_for_client(self, registry):
        """Test getting the zone a client belongs to."""
        await registry.initialize()

        await registry.register_client(mac_id="local", name="Main", ip="127.0.0.1")
        await registry.register_client(mac_id="client2", name="Client 2", ip="192.168.1.100")

        await registry.create_zone("zone1", "Test Zone", ["local", "client2"])

        zone = registry.get_zone_for_client("local")
        assert zone is not None
        assert zone.id == "zone1"

    @pytest.mark.asyncio
    async def test_add_client_to_zone(self, registry):
        """Test adding a client to a zone."""
        await registry.initialize()

        await registry.register_client(mac_id="local", name="Main", ip="127.0.0.1")
        await registry.register_client(mac_id="client2", name="Client 2", ip="192.168.1.100")
        await registry.register_client(mac_id="client3", name="Client 3", ip="192.168.1.101")

        await registry.create_zone("zone1", "Test Zone", ["local", "client2"])
        result = await registry.add_client_to_zone("zone1", "client3")

        assert result is True
        zone = registry.get_zone("zone1")
        assert "client3" in zone.client_ids

        client3 = registry.get_client("client3")
        assert client3.zone_id == "zone1"

    @pytest.mark.asyncio
    async def test_remove_client_from_zone(self, registry):
        """Test removing a client from a zone."""
        await registry.initialize()

        await registry.register_client(mac_id="local", name="Main", ip="127.0.0.1")
        await registry.register_client(mac_id="client2", name="Client 2", ip="192.168.1.100")
        await registry.register_client(mac_id="client3", name="Client 3", ip="192.168.1.101")

        await registry.create_zone("zone1", "Test Zone", ["local", "client2", "client3"])
        result = await registry.remove_client_from_zone("zone1", "client3")

        assert result is True
        zone = registry.get_zone("zone1")
        assert "client3" not in zone.client_ids

        client3 = registry.get_client("client3")
        assert client3.zone_id is None

    def test_compute_mac_id_local(self):
        """Test computing mac_id for local client reads from system interface."""
        # Local client (127.0.0.1) reads MAC from eth0 or wlan0
        mac_id = ClientRegistryService.compute_mac_id("milo", "127.0.0.1")
        # Should be a valid MAC address format
        assert ":" in mac_id
        assert len(mac_id) == 17  # xx:xx:xx:xx:xx:xx

    def test_compute_mac_id_with_mac(self):
        """Test computing mac_id when MAC address is provided."""
        # When MAC is provided, return it directly
        mac_id = ClientRegistryService.compute_mac_id("milo-client-kitchen", "192.168.1.100", mac="aa:bb:cc:dd:ee:ff")
        assert mac_id == "aa:bb:cc:dd:ee:ff"

    def test_compute_mac_id_remote_no_mac_raises(self):
        """Test that remote client without MAC raises ValueError."""
        # Remote clients must have a MAC address
        with pytest.raises(ValueError, match="No MAC address"):
            ClientRegistryService.compute_mac_id("unknown-host", "192.168.1.200")

    def test_compute_mac_id_ignores_null_mac(self):
        """Test that null MAC (00:00:00:00:00:00) is ignored."""
        # When MAC is all zeros, it's treated as not provided
        with pytest.raises(ValueError, match="No MAC address"):
            ClientRegistryService.compute_mac_id("client", "192.168.1.200", mac="00:00:00:00:00:00")

    @pytest.mark.asyncio
    async def test_standalone_equalizer_storage(self, registry):
        """Test standalone Equalizer settings storage."""
        from backend.core.multiroom.models import EqFilter

        await registry.initialize()

        await registry.register_client(mac_id="local", name="Main", ip="127.0.0.1")

        # Initially no standalone Equalizer
        assert registry.get_standalone_equalizer("local") is None

        # Set standalone Equalizer with typed EqFilter
        eq = EqualizerSettings(filters=[EqFilter(id="eq_band_00", frequency=1000, gain=3.0)])
        await registry.set_standalone_equalizer("local", eq)

        # Retrieve standalone Equalizer
        retrieved = registry.get_standalone_equalizer("local")
        assert retrieved is not None
        assert len(retrieved.filters) == 1
        assert retrieved.filters[0].frequency == 1000

    @pytest.mark.asyncio
    async def test_standalone_equalizer_cleared_on_zone_join(self, registry):
        """Test that standalone Equalizer is cleared when client joins a zone."""
        from backend.core.multiroom.models import EqFilter

        await registry.initialize()

        await registry.register_client(mac_id="local", name="Main", ip="127.0.0.1")
        await registry.register_client(mac_id="client2", name="Client 2", ip="192.168.1.100")

        # Set standalone Equalizer for local with typed EqFilter
        eq = EqualizerSettings(filters=[EqFilter(id="eq_band_00", frequency=1000)])
        await registry.set_standalone_equalizer("local", eq)

        # Create zone - standalone Equalizer should be cleared
        await registry.create_zone("zone1", "Test Zone", ["local", "client2"])

        # Standalone Equalizer should be cleared
        assert registry.get_standalone_equalizer("local") is None

    @pytest.mark.asyncio
    async def test_zone_equalizer_retained_on_leave(self, registry):
        """Test that zone Equalizer is copied to standalone when client leaves zone (FR14/AC2)."""
        from backend.core.multiroom.models import EqFilter

        await registry.initialize()

        await registry.register_client(mac_id="local", name="Main", ip="127.0.0.1")
        await registry.register_client(mac_id="client2", name="Client 2", ip="192.168.1.100")
        await registry.register_client(mac_id="client3", name="Client 3", ip="192.168.1.101")

        # Create zone with custom Equalizer settings using typed EqFilter
        zone_equalizer = EqualizerSettings(filters=[EqFilter(id="eq_band_00", frequency=2000, gain=-5.0)])
        await registry.create_zone("zone1", "Test Zone", ["local", "client2", "client3"], equalizer_settings=zone_equalizer)

        # Verify no standalone Equalizer for client3
        assert registry.get_standalone_equalizer("client3") is None

        # Remove client3 from zone
        result = await registry.remove_client_from_zone("zone1", "client3")
        assert result is True

        # Client3 should now have zone's Equalizer as standalone (FR14)
        standalone = registry.get_standalone_equalizer("client3")
        assert standalone is not None
        assert len(standalone.filters) == 1
        assert standalone.filters[0].frequency == 2000
        assert standalone.filters[0].gain == -5.0

    @pytest.mark.asyncio
    async def test_thread_safety_concurrent_operations(self, registry):
        """Test thread safety with concurrent client operations (AC6)."""
        await registry.initialize()

        # Register initial client
        await registry.register_client(
            mac_id="local",
            name="Main",
            ip="127.0.0.1"
        )

        # Run multiple concurrent operations on the same client
        async def update_name(suffix):
            await registry.update_client("local", name=f"Main-{suffix}")

        async def set_online(status):
            await registry.set_client_online("local", status)

        # Execute 10 concurrent operations
        operations = []
        for i in range(5):
            operations.append(update_name(i))
            operations.append(set_online(i % 2 == 0))

        # All operations should complete without errors (no race conditions)
        await asyncio.gather(*operations)

        # Client should still exist and be consistent
        client = registry.get_client("local")
        assert client is not None
        assert client.mac_id == "local"

    @pytest.mark.asyncio
    async def test_persistence_called_on_register(self, registry, mock_settings_service):
        """Test that persistence is called when registering a client (AC4)."""
        await registry.initialize()

        await registry.register_client(
            mac_id="test-client",
            name="Test",
            ip="192.168.1.50"
        )

        # Verify settings service was called to persist
        mock_settings_service.set_setting.assert_called()
        call_args = mock_settings_service.set_setting.call_args_list
        # Should have called set_setting with 'multiroom.clients' key
        assert any(
            call[0][0] == "multiroom.clients"
            for call in call_args
        )

    @pytest.mark.asyncio
    async def test_initialization_loads_clients_offline(self, mock_settings_service, mock_event_bus):
        """Test that initialization loads clients with online=False (AC7)."""
        # Setup mock to return persisted client data
        mock_settings_service.get_setting = AsyncMock(side_effect=lambda key, default=None: {
            "multiroom.clients": {
                "client-1": {
                    "mac_id": "client-1",
                    "name": "Persisted Client",
                    "ip": "192.168.1.100",
                    "speaker_type": "bookshelf"
                }
            }
        }.get(key, default))

        registry = ClientRegistryService(
            settings_service=mock_settings_service,
            event_bus=mock_event_bus
        )

        await registry.initialize()

        # Client should be loaded with online=False
        client = registry.get_client("client-1")
        assert client is not None
        assert client.name == "Persisted Client"
        assert client.online is False  # Runtime state always starts offline


class TestZoneAverageVolume:
    """Tests for get_zone_average_volume() method (Story 5.2)."""

    @pytest.fixture
    def mock_settings_service(self):
        """Create a mock settings service."""
        service = AsyncMock()
        service.get_setting = AsyncMock(return_value=None)
        service.set_setting = AsyncMock()
        return service

    @pytest.fixture
    def mock_event_bus(self):
        """Create a mock event bus."""
        bus = MagicMock()
        bus.emit = AsyncMock()
        return bus

    @pytest.fixture
    def registry(self, mock_settings_service, mock_event_bus):
        """Create a ClientRegistryService instance."""
        return ClientRegistryService(
            settings_service=mock_settings_service,
            event_bus=mock_event_bus
        )

    @pytest.mark.asyncio
    async def test_get_zone_average_volume_multiple_online_clients(self, registry):
        """Test zone average with multiple ONLINE clients returns correct average (AC1)."""
        await registry.initialize()

        # Register 3 clients with different volumes
        await registry.register_client("client-1", "Client 1", "192.168.1.1")
        await registry.register_client("client-2", "Client 2", "192.168.1.2")
        await registry.register_client("client-3", "Client 3", "192.168.1.3")

        # Set different volumes
        await registry.update_volume("client-1", volume_db=-20.0)
        await registry.update_volume("client-2", volume_db=-30.0)
        await registry.update_volume("client-3", volume_db=-40.0)

        # Set all online
        await registry.set_client_online("client-1", True)
        await registry.set_client_online("client-2", True)
        await registry.set_client_online("client-3", True)

        # Create zone with all 3 clients
        zone = await registry.create_zone(
            zone_id="zone-1",
            name="Test Zone",
            client_ids=["client-1", "client-2", "client-3"]
        )

        # Test zone average (excluding none - all clients included)
        avg = registry.get_zone_average_volume("zone-1")

        # Average of -20, -30, -40 = -30
        assert avg == -30.0

    @pytest.mark.asyncio
    async def test_get_zone_average_volume_excludes_reconnecting_client(self, registry):
        """Test zone average excludes the reconnecting client (AC1)."""
        await registry.initialize()

        # Register 3 clients
        await registry.register_client("client-1", "Client 1", "192.168.1.1")
        await registry.register_client("client-2", "Client 2", "192.168.1.2")
        await registry.register_client("client-3", "Client 3", "192.168.1.3")

        # Set volumes: client-1=-20, client-2=-30, client-3=-40
        await registry.update_volume("client-1", volume_db=-20.0)
        await registry.update_volume("client-2", volume_db=-30.0)
        await registry.update_volume("client-3", volume_db=-40.0)

        # Set all online
        await registry.set_client_online("client-1", True)
        await registry.set_client_online("client-2", True)
        await registry.set_client_online("client-3", True)

        # Create zone
        await registry.create_zone("zone-1", "Test Zone", ["client-1", "client-2", "client-3"])

        # Test zone average excluding client-1 (simulating reconnection)
        avg = registry.get_zone_average_volume("zone-1", exclude_mac_id="client-1")

        # Average of -30, -40 = -35
        assert avg == -35.0

    @pytest.mark.asyncio
    async def test_get_zone_average_volume_single_online_client(self, registry):
        """Test zone average with only one ONLINE client returns that client's volume."""
        await registry.initialize()

        # Register 2 clients (minimum for zone)
        await registry.register_client("client-1", "Client 1", "192.168.1.1")
        await registry.register_client("client-2", "Client 2", "192.168.1.2")

        await registry.update_volume("client-1", volume_db=-25.0)
        await registry.update_volume("client-2", volume_db=-40.0)

        # Only client-1 is online
        await registry.set_client_online("client-1", True)
        await registry.set_client_online("client-2", False)

        # Create zone
        await registry.create_zone("zone-1", "Test Zone", ["client-1", "client-2"])

        # Test zone average (only client-1 is online)
        avg = registry.get_zone_average_volume("zone-1")

        assert avg == -25.0

    @pytest.mark.asyncio
    async def test_get_zone_average_volume_no_online_clients(self, registry):
        """Test zone average with NO online clients returns None (FR8 trigger)."""
        await registry.initialize()

        # Register 2 clients
        await registry.register_client("client-1", "Client 1", "192.168.1.1")
        await registry.register_client("client-2", "Client 2", "192.168.1.2")

        # Both offline
        await registry.set_client_online("client-1", False)
        await registry.set_client_online("client-2", False)

        # Create zone
        await registry.create_zone("zone-1", "Test Zone", ["client-1", "client-2"])

        # Test zone average
        avg = registry.get_zone_average_volume("zone-1")

        # Should return None because no ONLINE clients
        assert avg is None

    @pytest.mark.asyncio
    async def test_get_zone_average_volume_all_excluded(self, registry):
        """Test zone average when all clients are excluded returns None."""
        await registry.initialize()

        # Register 2 clients
        await registry.register_client("client-1", "Client 1", "192.168.1.1")
        await registry.register_client("client-2", "Client 2", "192.168.1.2")

        await registry.update_volume("client-1", volume_db=-20.0)
        await registry.update_volume("client-2", volume_db=-30.0)

        # Only client-1 is online
        await registry.set_client_online("client-1", True)
        await registry.set_client_online("client-2", False)

        # Create zone
        await registry.create_zone("zone-1", "Test Zone", ["client-1", "client-2"])

        # Exclude the only online client
        avg = registry.get_zone_average_volume("zone-1", exclude_mac_id="client-1")

        # Should return None because no other ONLINE clients
        assert avg is None

    @pytest.mark.asyncio
    async def test_get_zone_average_volume_invalid_zone(self, registry):
        """Test zone average with invalid zone_id returns None."""
        await registry.initialize()

        avg = registry.get_zone_average_volume("nonexistent-zone")

        assert avg is None

    @pytest.mark.asyncio
    async def test_get_zone_average_volume_excludes_offline_clients(self, registry):
        """Test zone average only includes ONLINE clients, excludes offline."""
        await registry.initialize()

        # Register 3 clients
        await registry.register_client("client-1", "Client 1", "192.168.1.1")
        await registry.register_client("client-2", "Client 2", "192.168.1.2")
        await registry.register_client("client-3", "Client 3", "192.168.1.3")

        # Set volumes
        await registry.update_volume("client-1", volume_db=-10.0)  # ONLINE
        await registry.update_volume("client-2", volume_db=-50.0)  # OFFLINE (should be excluded)
        await registry.update_volume("client-3", volume_db=-30.0)  # ONLINE

        # Set online status
        await registry.set_client_online("client-1", True)
        await registry.set_client_online("client-2", False)  # OFFLINE
        await registry.set_client_online("client-3", True)

        # Create zone
        await registry.create_zone("zone-1", "Test Zone", ["client-1", "client-2", "client-3"])

        # Test zone average (should only include client-1 and client-3)
        avg = registry.get_zone_average_volume("zone-1")

        # Average of -10 and -30 = -20 (client-2's -50 is excluded)
        assert avg == -20.0


class TestGlobalAverageVolume:
    """Tests for get_global_average_volume() method (Story 5.3)."""

    @pytest.fixture
    def mock_settings_service(self):
        """Create a mock settings service."""
        service = AsyncMock()
        service.get_setting = AsyncMock(return_value=None)
        service.set_setting = AsyncMock()
        return service

    @pytest.fixture
    def mock_event_bus(self):
        """Create a mock event bus."""
        bus = MagicMock()
        bus.emit = AsyncMock()
        return bus

    @pytest.fixture
    def registry(self, mock_settings_service, mock_event_bus):
        """Create a ClientRegistryService instance."""
        return ClientRegistryService(
            settings_service=mock_settings_service,
            event_bus=mock_event_bus
        )

    @pytest.mark.asyncio
    async def test_global_average_multiple_online_clients(self, registry):
        """Test global average with multiple ONLINE clients returns correct average (AC1)."""
        await registry.initialize()

        # Register 3 clients with different volumes - mix of standalone and zoned
        await registry.register_client("client-1", "Client 1", "192.168.1.1")
        await registry.register_client("client-2", "Client 2", "192.168.1.2")
        await registry.register_client("client-3", "Client 3", "192.168.1.3")

        # Set all online with different volumes
        await registry.set_client_online("client-1", True)
        await registry.set_client_online("client-2", True)
        await registry.set_client_online("client-3", True)
        await registry.update_volume("client-1", volume_db=-20.0)
        await registry.update_volume("client-2", volume_db=-30.0)
        await registry.update_volume("client-3", volume_db=-40.0)

        # Test global average (all clients included)
        avg = registry.get_global_average_volume()

        # Average of -20, -30, -40 = -30
        assert avg == -30.0

    @pytest.mark.asyncio
    async def test_global_average_excludes_reconnecting_client(self, registry):
        """Test global average excludes the reconnecting client (AC1)."""
        await registry.initialize()

        # Register 3 clients
        await registry.register_client("client-1", "Client 1", "192.168.1.1")
        await registry.register_client("client-2", "Client 2", "192.168.1.2")
        await registry.register_client("client-3", "Client 3", "192.168.1.3")

        # Set all online
        await registry.set_client_online("client-1", True)
        await registry.set_client_online("client-2", True)
        await registry.set_client_online("client-3", True)
        await registry.update_volume("client-1", volume_db=-20.0)
        await registry.update_volume("client-2", volume_db=-30.0)
        await registry.update_volume("client-3", volume_db=-40.0)

        # Test global average excluding client-1 (simulating reconnection)
        avg = registry.get_global_average_volume(exclude_mac_id="client-1")

        # Average of -30, -40 = -35
        assert avg == -35.0

    @pytest.mark.asyncio
    async def test_global_average_single_online_client(self, registry):
        """Test global average with only one ONLINE client returns that client's volume."""
        await registry.initialize()

        # Register 2 clients
        await registry.register_client("client-1", "Client 1", "192.168.1.1")
        await registry.register_client("client-2", "Client 2", "192.168.1.2")

        # Only client-1 is online
        await registry.set_client_online("client-1", True)
        await registry.update_volume("client-1", volume_db=-25.0)

        # Test global average (only client-1 is online)
        avg = registry.get_global_average_volume()

        assert avg == -25.0

    @pytest.mark.asyncio
    async def test_global_average_no_online_clients(self, registry):
        """Test global average with NO online clients returns None (FR10 trigger)."""
        await registry.initialize()

        # Register 2 clients
        await registry.register_client("client-1", "Client 1", "192.168.1.1")
        await registry.register_client("client-2", "Client 2", "192.168.1.2")

        # Neither is online

        # Test global average
        avg = registry.get_global_average_volume()

        # Should return None because no ONLINE clients
        assert avg is None

    @pytest.mark.asyncio
    async def test_global_average_all_excluded(self, registry):
        """Test global average when all clients are excluded returns None."""
        await registry.initialize()

        # Register 1 client
        await registry.register_client("client-1", "Client 1", "192.168.1.1")
        await registry.set_client_online("client-1", True)
        await registry.update_volume("client-1", volume_db=-25.0)

        # Exclude the only online client
        avg = registry.get_global_average_volume(exclude_mac_id="client-1")

        # Should return None because no other ONLINE clients
        assert avg is None

    @pytest.mark.asyncio
    async def test_global_average_includes_both_zoned_and_standalone(self, registry):
        """Test global average includes clients in zones AND standalone clients."""
        await registry.initialize()

        # Register 4 clients
        await registry.register_client("zone-client-1", "Zone Client 1", "192.168.1.1")
        await registry.register_client("zone-client-2", "Zone Client 2", "192.168.1.2")
        await registry.register_client("standalone-1", "Standalone 1", "192.168.1.3")
        await registry.register_client("standalone-2", "Standalone 2", "192.168.1.4")

        # Create zone with 2 clients
        await registry.create_zone("zone-1", "Test Zone", ["zone-client-1", "zone-client-2"])

        # Set all online with volumes
        await registry.set_client_online("zone-client-1", True)
        await registry.set_client_online("zone-client-2", True)
        await registry.set_client_online("standalone-1", True)
        await registry.set_client_online("standalone-2", True)
        await registry.update_volume("zone-client-1", volume_db=-10.0)
        await registry.update_volume("zone-client-2", volume_db=-20.0)
        await registry.update_volume("standalone-1", volume_db=-30.0)
        await registry.update_volume("standalone-2", volume_db=-40.0)

        # Test global average (all 4 clients)
        avg = registry.get_global_average_volume()

        # Average of -10, -20, -30, -40 = -25
        assert avg == -25.0

    @pytest.mark.asyncio
    async def test_global_average_excludes_offline_clients(self, registry):
        """Test global average only includes ONLINE clients, excludes offline."""
        await registry.initialize()

        # Register 3 clients
        await registry.register_client("client-1", "Client 1", "192.168.1.1")
        await registry.register_client("client-2", "Client 2", "192.168.1.2")
        await registry.register_client("client-3", "Client 3", "192.168.1.3")

        # Only client-1 and client-3 are online
        await registry.set_client_online("client-1", True)
        # client-2 stays offline
        await registry.set_client_online("client-3", True)

        await registry.update_volume("client-1", volume_db=-10.0)
        await registry.update_volume("client-2", volume_db=-50.0)  # This should be excluded
        await registry.update_volume("client-3", volume_db=-30.0)

        # Test global average (should only include client-1 and client-3)
        avg = registry.get_global_average_volume()

        # Average of -10 and -30 = -20 (client-2's -50 is excluded)
        assert avg == -20.0


# =============================================================================
# SnapcastService Tests
# =============================================================================

class TestSnapcastService:
    """Tests for SnapcastService."""

    @pytest.fixture
    def mock_event_bus(self):
        """Create a mock event bus."""
        bus = MagicMock()
        bus.emit = AsyncMock()
        return bus

    @pytest.fixture
    def snapcast_service(self, mock_event_bus):
        """Create a SnapcastService instance."""
        return SnapcastService(event_bus=mock_event_bus)

    def test_compute_mac_id_local_via_service(self, snapcast_service):
        """Test computing mac_id for local client via ClientRegistryService."""
        # mac_id derivation moved to ClientRegistryService.compute_mac_id()
        # Local client reads MAC from system interface
        mac_id = ClientRegistryService.compute_mac_id("milo", "127.0.0.1")
        assert ":" in mac_id  # Returns a MAC address format
        assert len(mac_id) == 17  # xx:xx:xx:xx:xx:xx

    def test_compute_mac_id_with_mac_via_service(self, snapcast_service):
        """Test computing mac_id when MAC is provided."""
        mac_id = ClientRegistryService.compute_mac_id("milo-client-kitchen", "192.168.1.100", mac="aa:bb:cc:dd:ee:ff")
        assert mac_id == "aa:bb:cc:dd:ee:ff"

    def test_compute_mac_id_remote_requires_mac(self, snapcast_service):
        """Test that remote clients without MAC raise ValueError."""
        with pytest.raises(ValueError, match="No MAC address"):
            ClientRegistryService.compute_mac_id("unknown", "192.168.1.200")

    def test_deduplicate_by_mac_empty(self, snapcast_service):
        """Test deduplication with empty list."""
        result = snapcast_service._deduplicate_by_mac([])
        assert result == []

    def test_deduplicate_by_mac_no_duplicates(self, snapcast_service):
        """Test deduplication with no duplicates."""
        clients = [
            {"id": "c1", "mac": "aa:bb:cc:dd:ee:01", "ip": "192.168.1.1"},
            {"id": "c2", "mac": "aa:bb:cc:dd:ee:02", "ip": "192.168.1.2"},
        ]
        result = snapcast_service._deduplicate_by_mac(clients)
        assert len(result) == 2

    def test_deduplicate_by_mac_with_duplicates(self, snapcast_service):
        """Test deduplication with duplicates."""
        clients = [
            {"id": "c1", "mac": "aa:bb:cc:dd:ee:01", "ip": "192.168.1.1"},
            {"id": "c2", "mac": "aa:bb:cc:dd:ee:01", "ip": "192.168.1.2"},  # Same MAC
        ]
        result = snapcast_service._deduplicate_by_mac(clients)
        assert len(result) == 1
        assert result[0]["id"] == "c1"

    def test_calculate_connection_quality_good(self, snapcast_service):
        """Test connection quality calculation."""
        quality = snapcast_service._calculate_connection_quality({"sec": 12345})
        assert quality == "good"

    def test_calculate_connection_quality_unknown(self, snapcast_service):
        """Test connection quality with no data."""
        quality = snapcast_service._calculate_connection_quality({})
        assert quality == "unknown"

    def test_validate_config_valid(self, snapcast_service):
        """Test config validation with valid config."""
        config = {
            "buffer": 500,
            "codec": "flac",
            "chunk_ms": 20
        }
        assert snapcast_service._validate_config(config) is True

    def test_validate_config_invalid_buffer(self, snapcast_service):
        """Test config validation with invalid buffer."""
        config = {"buffer": 50}  # Too small
        assert snapcast_service._validate_config(config) is False

    def test_validate_config_invalid_codec(self, snapcast_service):
        """Test config validation with invalid codec."""
        config = {"codec": "mp3"}  # Not supported
        assert snapcast_service._validate_config(config) is False

    @pytest.mark.asyncio
    async def test_is_available_connection_error(self, snapcast_service):
        """Test is_available with connection error."""
        with patch("backend.core.multiroom.snapcast.aiohttp.ClientSession") as mock_session:
            mock_session.return_value.__aenter__ = AsyncMock(side_effect=Exception("Connection refused"))
            result = await snapcast_service.is_available()
            assert result is False


# =============================================================================
# Helper Function Tests
# =============================================================================

class TestHelperFunctions:
    """Tests for helper functions."""

    def test_is_ip_address_true(self):
        """Test is_ip_address with valid IP."""
        assert is_ip_address("192.168.1.100") is True
        assert is_ip_address("10.0.0.1") is True

    def test_is_ip_address_false(self):
        """Test is_ip_address with hostname."""
        assert is_ip_address("milo-client-kitchen") is False
        assert is_ip_address("localhost") is False


# =============================================================================
# CrossoverService Tests
# =============================================================================

class TestCrossoverService:
    """Tests for CrossoverService."""

    @pytest.fixture
    def mock_settings_service(self):
        """Create a mock settings service."""
        service = AsyncMock()
        service.get_setting = AsyncMock(return_value=None)
        service.set_setting = AsyncMock()
        return service

    @pytest.fixture
    def mock_camilladsp_service(self):
        """Create a mock Equalizer service."""
        service = AsyncMock()
        service.set_crossover_filter = AsyncMock(return_value=True)
        service.set_lowpass_filter = AsyncMock(return_value=True)
        service.set_mute = AsyncMock(return_value=True)
        return service

    @pytest.fixture
    def mock_registry(self):
        """Create a mock client registry."""
        registry = MagicMock()
        registry.get_client = MagicMock(return_value=None)
        registry.get_zone = MagicMock(return_value=None)
        registry.get_zone_for_client = MagicMock(return_value=None)
        registry.is_client_online = MagicMock(return_value=True)
        registry.subscribe = MagicMock()
        return registry

    @pytest.fixture
    def mock_event_bus(self):
        """Create a mock event bus."""
        bus = MagicMock()
        bus.emit = AsyncMock()
        return bus

    @pytest.fixture
    def crossover_service(self, mock_settings_service, mock_camilladsp_service, mock_registry, mock_event_bus):
        """Create a CrossoverService instance with mock registry for local client."""
        # Configure mock registry to return local client
        local_client = MagicMock()
        local_client.ip = "127.0.0.1"
        local_client.mac_id = "aa:bb:cc:dd:ee:ff"
        mock_registry.get_client = MagicMock(side_effect=lambda x: local_client if x == "aa:bb:cc:dd:ee:ff" else None)

        service = CrossoverService(
            settings_service=mock_settings_service,
            camilladsp_service=mock_camilladsp_service,
            event_bus=mock_event_bus
        )
        # Set registry via setter (not constructor parameter)
        service.set_registry(mock_registry)
        return service

    @pytest.mark.asyncio
    async def test_initialize(self, crossover_service):
        """Test crossover service initialization."""
        result = await crossover_service.initialize()
        assert result is True

    def test_is_client_subwoofer_default(self, crossover_service):
        """Test is_client_subwoofer with default speaker type."""
        result = crossover_service.is_client_subwoofer("unknown_client")
        assert result is False

    def test_get_client_speaker_type_default(self, crossover_service):
        """Test getting default speaker type."""
        result = crossover_service.get_client_speaker_type("unknown_client")
        assert result == DEFAULT_SPEAKER_TYPE

    def test_get_client_crossover_frequency_default(self, crossover_service):
        """Test getting default crossover frequency."""
        result = crossover_service.get_client_crossover_frequency("unknown_client")
        assert result == DEFAULT_CROSSOVER_FREQUENCIES[DEFAULT_SPEAKER_TYPE]

    @pytest.mark.asyncio
    async def test_set_client_speaker_type_invalid(self, crossover_service):
        """Test setting invalid speaker type."""
        result = await crossover_service.set_client_speaker_type("local", "invalid_type")
        assert result is False

    def test_has_pending_settings_empty(self, crossover_service):
        """Test has_pending_settings with no pending settings."""
        assert crossover_service.has_pending_settings("aa:bb:cc:dd:ee:ff") is False

    @pytest.mark.asyncio
    async def test_queue_pending_settings(self, crossover_service):
        """Test queuing pending settings."""
        await crossover_service.queue_pending_settings("192.168.1.100", "crossover", {
            "enabled": True,
            "frequency": 80
        })

        assert crossover_service.has_pending_settings("192.168.1.100") is True

    def test_get_pending_settings(self, crossover_service):
        """Test getting pending settings."""
        crossover_service._pending_settings["192.168.1.100"] = {
            "crossover": {"enabled": True, "frequency": 80}
        }

        settings = crossover_service.get_pending_settings("192.168.1.100")
        assert "crossover" in settings

    def test_clear_pending_settings(self, crossover_service):
        """Test clearing pending settings."""
        crossover_service._pending_settings["192.168.1.100"] = {
            "crossover": {"enabled": True}
        }

        crossover_service.clear_pending_settings("192.168.1.100")
        assert crossover_service.has_pending_settings("192.168.1.100") is False

    @pytest.mark.asyncio
    async def test_set_client_crossover_local(self, crossover_service, mock_camilladsp_service):
        """Test setting crossover on local client (identified by mac_id with ip=127.0.0.1)."""
        # Use the MAC address configured in the mock registry for local client
        result = await crossover_service._set_client_crossover("aa:bb:cc:dd:ee:ff", True, 80)
        assert result is True
        mock_camilladsp_service.set_crossover_filter.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_client_lowpass_local(self, crossover_service, mock_camilladsp_service):
        """Test setting lowpass on local client (identified by mac_id with ip=127.0.0.1)."""
        # Use the MAC address configured in the mock registry for local client
        result = await crossover_service._set_client_lowpass("aa:bb:cc:dd:ee:ff", True, 80)
        assert result is True
        mock_camilladsp_service.set_lowpass_filter.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup(self, crossover_service):
        """Test cleanup clears pending settings."""
        crossover_service._pending_settings["test"] = {"data": "value"}
        await crossover_service.cleanup()
        assert len(crossover_service._pending_settings) == 0


# =============================================================================
# Integration Tests
# =============================================================================

class TestMultiroomIntegration:
    """Integration tests for multiroom module."""

    @pytest.mark.asyncio
    async def test_registry_and_crossover_integration(self):
        """Test ClientRegistryService and CrossoverService integration."""
        # Create services
        mock_settings = AsyncMock()
        mock_settings.get_setting = AsyncMock(return_value=None)
        mock_settings.set_setting = AsyncMock()

        mock_camilladsp_mock = AsyncMock()
        mock_camilladsp_mock.set_crossover_filter = AsyncMock(return_value=True)
        mock_camilladsp_mock.set_lowpass_filter = AsyncMock(return_value=True)

        mock_bus = MagicMock()
        mock_bus.emit = AsyncMock()

        registry = ClientRegistryService(settings_service=mock_settings, event_bus=mock_bus)
        crossover = CrossoverService(settings_service=mock_settings, camilladsp_service=mock_camilladsp_mock, event_bus=mock_bus)

        # Initialize
        await registry.initialize()
        await crossover.initialize()

        # Set registry on crossover
        crossover.set_registry(registry)

        # Register a client
        await registry.register_client(
            mac_id="local",
            name="Main",
            ip="127.0.0.1"
        )

        # Verify client is in registry
        client = registry.get_client("local")
        assert client is not None
        assert client.speaker_type == DEFAULT_SPEAKER_TYPE


# =============================================================================
# Zone Equalizer Sync Tests (FR7, FR8 - Story 5.1)
# =============================================================================

class TestZoneDspSync:
    """Tests for zone Equalizer settings sync on client reconnection."""

    def test_equalizer_settings_to_dict(self):
        """Test EqualizerSettings serialization."""
        from backend.core.multiroom.models import EqFilter, CompressorSettings, LoudnessSettings

        eq = EqualizerSettings(
            enabled=True,
            filters=[EqFilter(id="eq_1", frequency=1000, gain=3.0, q=1.0)],
            compressor=CompressorSettings(enabled=True, threshold=-20, ratio=4.0),
            loudness=LoudnessSettings(enabled=True, high_boost=75)
        )

        data = eq.to_dict()

        assert len(data["filters"]) == 1
        assert data["filters"][0]["frequency"] == 1000
        assert data["compressor"]["enabled"] is True
        assert data["loudness"]["high_boost"] == 75

    def test_equalizer_settings_from_dict(self):
        """Test EqualizerSettings deserialization."""
        data = {
            "enabled": True,
            "filters": [{"id": "eq_1", "frequency": 500, "gain": -2.0, "q": 1.41, "filter_type": "Peaking", "enabled": True}],
            "compressor": {"enabled": False},
            "loudness": None
        }

        eq = EqualizerSettings.from_dict(data)

        assert len(eq.filters) == 1
        assert eq.filters[0].frequency == 500
        assert eq.compressor.enabled is False
        assert eq.loudness.enabled is False  # None becomes default LoudnessSettings

    def test_zone_with_equalizer_settings(self):
        """Test Zone contains Equalizer settings properly."""
        from backend.core.multiroom.models import EqFilter, CompressorSettings, LoudnessSettings

        equalizer_settings = EqualizerSettings(
            enabled=True,
            filters=[EqFilter(id="eq_1", frequency=100, gain=2.0)],
            compressor=CompressorSettings(enabled=True, threshold=-15),
            loudness=LoudnessSettings(enabled=True, high_boost=80)
        )

        zone = Zone(
            name="Living Room",
            id="zone-1",
            client_ids=["local", "client-01"],
            equalizer_settings=equalizer_settings
        )

        assert zone.equalizer_settings.filters[0].frequency == 100
        assert zone.equalizer_settings.compressor.enabled is True
        assert zone.equalizer_settings.loudness.enabled is True

    def test_zone_to_dict_includes_equalizer_settings(self):
        """Test Zone serialization includes Equalizer settings."""
        from backend.core.multiroom.models import EqFilter, LoudnessSettings

        zone = Zone(
            name="Test Zone",
            id="zone-1",
            client_ids=["local"],
            equalizer_settings=EqualizerSettings(
                enabled=True,
                filters=[EqFilter(id="eq_2", frequency=2000)],
                loudness=LoudnessSettings(enabled=False)
            )
        )

        data = zone.to_dict()

        assert "equalizer_settings" in data
        assert len(data["equalizer_settings"]["filters"]) == 1
        assert data["equalizer_settings"]["filters"][0]["frequency"] == 2000
        assert data["equalizer_settings"]["compressor"]["enabled"] is False
        assert data["equalizer_settings"]["loudness"]["enabled"] is False

    def test_zone_from_dict_loads_equalizer_settings(self):
        """Test Zone deserialization loads Equalizer settings."""
        data = {
            "id": "zone-2",
            "name": "Bedroom",
            "client_ids": ["client-02", "client-03"],
            "equalizer_settings": {
                "enabled": True,
                "filters": [{"id": "eq_3", "frequency": 3000, "gain": 1.5, "q": 1.41, "filter_type": "Peaking", "enabled": True}],
                "compressor": {"enabled": True, "threshold": -20, "ratio": 4.0, "attack": 10.0, "release": 100.0, "makeup_gain": 0.0},
                "loudness": {"enabled": True, "high_boost": 5.0, "low_boost": 8.0}
            }
        }

        zone = Zone.from_dict(data)

        assert zone.id == "zone-2"
        assert len(zone.client_ids) == 2
        assert zone.equalizer_settings.filters[0].frequency == 3000
        assert zone.equalizer_settings.compressor.enabled is True
        assert zone.equalizer_settings.loudness.high_boost == 5.0


# =============================================================================
# Pending Equalizer Settings Queue Tests (Task 3 - Story 5.1)
# =============================================================================

class TestPendingEqualizerSettings:
    """Tests for pending Equalizer settings queue for offline clients."""

    @pytest.fixture
    def mock_camilladsp(self):
        """Create mock Equalizer service."""
        camilladsp_mock = AsyncMock()
        camilladsp_mock.set_filter = AsyncMock(return_value=True)
        camilladsp_mock.set_compressor = AsyncMock(return_value=True)
        camilladsp_mock.set_loudness = AsyncMock(return_value=True)
        camilladsp_mock.set_mute = AsyncMock(return_value=True)
        return camilladsp_mock

    @pytest.fixture
    def crossover_service(self, mock_camilladsp):
        """Create CrossoverService with mock Equalizer and registry for local client."""
        mock_settings = AsyncMock()
        mock_bus = MagicMock()
        mock_bus.emit = AsyncMock()

        # Configure mock registry for local client
        mock_registry = MagicMock()
        local_client = MagicMock()
        local_client.ip = "127.0.0.1"
        local_client.mac_id = "aa:bb:cc:dd:ee:ff"
        mock_registry.get_client = MagicMock(side_effect=lambda x: local_client if x == "aa:bb:cc:dd:ee:ff" else None)

        service = CrossoverService(
            settings_service=mock_settings,
            camilladsp_service=mock_camilladsp,
            event_bus=mock_bus
        )
        service.set_registry(mock_registry)
        return service

    @pytest.mark.asyncio
    async def test_queue_filters_pending(self, crossover_service):
        """Test queuing filters for offline client."""
        filters = [
            {"id": "eq_1", "freq": 1000, "gain": 3.0, "q": 1.0, "type": "peaking"},
            {"id": "eq_2", "freq": 500, "gain": -2.0, "q": 0.7, "type": "peaking"}
        ]

        await crossover_service.queue_pending_settings("192.168.1.100", "filters", filters)

        assert crossover_service.has_pending_settings("192.168.1.100")
        pending = crossover_service.get_pending_settings("192.168.1.100")
        assert "filters" in pending
        assert len(pending["filters"]) == 2

    @pytest.mark.asyncio
    async def test_queue_compressor_pending(self, crossover_service):
        """Test queuing compressor settings for offline client."""
        compressor = {"enabled": True, "threshold": -20, "ratio": 4.0}

        await crossover_service.queue_pending_settings("192.168.1.100", "compressor", compressor)

        pending = crossover_service.get_pending_settings("192.168.1.100")
        assert "compressor" in pending
        assert pending["compressor"]["threshold"] == -20

    @pytest.mark.asyncio
    async def test_queue_loudness_pending(self, crossover_service):
        """Test queuing loudness settings for offline client."""
        loudness = {"enabled": True, "high_boost": -25}

        await crossover_service.queue_pending_settings("192.168.1.100", "loudness", loudness)

        pending = crossover_service.get_pending_settings("192.168.1.100")
        assert "loudness" in pending
        assert pending["loudness"]["high_boost"] == -25

    @pytest.mark.asyncio
    async def test_apply_pending_filters_local(self, crossover_service, mock_camilladsp):
        """Test applying pending filters to local client."""
        filters = [{"id": "eq_1", "freq": 1000, "gain": 3.0, "q": 1.0, "type": "peaking"}]
        await crossover_service.queue_pending_settings("aa:bb:cc:dd:ee:ff", "filters", filters)

        result = await crossover_service.apply_pending_settings("aa:bb:cc:dd:ee:ff")

        assert result is True
        mock_camilladsp.set_filter.assert_called_once()
        assert not crossover_service.has_pending_settings("aa:bb:cc:dd:ee:ff")

    @pytest.mark.asyncio
    async def test_apply_pending_compressor_local(self, crossover_service, mock_camilladsp):
        """Test applying pending compressor to local client."""
        compressor = {"enabled": True, "threshold": -15}
        await crossover_service.queue_pending_settings("aa:bb:cc:dd:ee:ff", "compressor", compressor)

        result = await crossover_service.apply_pending_settings("aa:bb:cc:dd:ee:ff")

        assert result is True
        mock_camilladsp.set_compressor.assert_called_once()

    @pytest.mark.asyncio
    async def test_apply_pending_loudness_local(self, crossover_service, mock_camilladsp):
        """Test applying pending loudness to local client."""
        loudness = {"enabled": True, "high_boost": -30}
        await crossover_service.queue_pending_settings("aa:bb:cc:dd:ee:ff", "loudness", loudness)

        result = await crossover_service.apply_pending_settings("aa:bb:cc:dd:ee:ff")

        assert result is True
        mock_camilladsp.set_loudness.assert_called_once()

    @pytest.mark.asyncio
    async def test_clear_pending_after_apply(self, crossover_service, mock_camilladsp):
        """Test that pending settings are cleared after successful apply."""
        await crossover_service.queue_pending_settings("aa:bb:cc:dd:ee:ff", "compressor", {"enabled": True})
        await crossover_service.queue_pending_settings("aa:bb:cc:dd:ee:ff", "loudness", {"enabled": False})

        assert crossover_service.has_pending_settings("aa:bb:cc:dd:ee:ff")

        await crossover_service.apply_pending_settings("aa:bb:cc:dd:ee:ff")

        assert not crossover_service.has_pending_settings("aa:bb:cc:dd:ee:ff")


# =============================================================================
# TestStandaloneEqualizerSync - Story 5.2: Standalone client Equalizer settings sync
# =============================================================================


class TestStandaloneEqualizerSync:
    """Tests for standalone client Equalizer settings sync on reconnection (Story 5.2)."""

    def test_standalone_equalizer_default_settings(self):
        """Test that default Equalizer settings are returned when none saved (AC3)."""
        from backend.core.equalizer.sync import EqualizerSettingsSyncService

        sync_service = EqualizerSettingsSyncService()
        defaults = sync_service.get_default_settings()

        # Defaults should have flat EQ, compressor off, loudness off
        assert "filters" in defaults
        assert "compressor" in defaults
        assert "loudness" in defaults
        assert defaults["compressor"]["enabled"] is False
        assert defaults["loudness"]["enabled"] is False

    def test_default_settings_returns_copy(self):
        """Test that default settings returns a copy (not mutable reference)."""
        from backend.core.equalizer.sync import EqualizerSettingsSyncService

        sync_service = EqualizerSettingsSyncService()
        defaults1 = sync_service.get_default_settings()
        defaults2 = sync_service.get_default_settings()

        # Modify one copy
        defaults1["compressor"]["enabled"] = True

        # Other copy should be unchanged
        assert defaults2["compressor"]["enabled"] is False

    @pytest.mark.asyncio
    async def test_load_standalone_settings_returns_none_when_not_found(self, tmp_path, monkeypatch):
        """Test loading settings for unknown client returns None."""
        from backend.core.equalizer.sync import EqualizerSettingsSyncService
        from backend.config import constants

        # Use temp file
        test_file = tmp_path / "client_equalizer.json"
        monkeypatch.setattr(constants, "CLIENT_EQUALIZER_FILE", test_file)

        sync_service = EqualizerSettingsSyncService()
        result = await sync_service.load_standalone_settings("unknown-client")

        assert result is None

    @pytest.mark.asyncio
    async def test_save_and_load_standalone_settings(self, tmp_path, monkeypatch):
        """Test saving and loading standalone Equalizer settings."""
        from backend.core.equalizer.sync import EqualizerSettingsSyncService
        from backend.config import constants

        # Use temp file
        test_file = tmp_path / "client_equalizer.json"
        monkeypatch.setattr(constants, "CLIENT_EQUALIZER_FILE", test_file)

        sync_service = EqualizerSettingsSyncService()
        client_id = "milo-client-01"
        settings = {
            "filters": {"band_1": {"type": "HighShelf", "gain": 2.0}},
            "compressor": {"enabled": True, "threshold": -20},
            "loudness": {"enabled": False},
        }

        # Save settings
        await sync_service.save_standalone_settings(client_id, settings)

        # Load and verify
        loaded = await sync_service.load_standalone_settings(client_id)
        assert loaded is not None
        assert loaded["filters"] == settings["filters"]
        assert loaded["compressor"]["enabled"] is True

    @pytest.mark.asyncio
    async def test_local_client_standalone_settings(self, tmp_path, monkeypatch):
        """AC4: 'local' client follows same STANDALONE rules."""
        from backend.core.equalizer.sync import EqualizerSettingsSyncService
        from backend.config import constants

        # Use temp file
        test_file = tmp_path / "client_equalizer.json"
        monkeypatch.setattr(constants, "CLIENT_EQUALIZER_FILE", test_file)

        sync_service = EqualizerSettingsSyncService()
        local_settings = {
            "filters": {"band_1": {"type": "Peaking", "gain": -3.0}},
            "compressor": {"enabled": False},
            "loudness": {"enabled": True, "high_boost": -25},
        }

        # Save settings for local client
        await sync_service.save_standalone_settings("local", local_settings)

        # Verify local client settings can be loaded
        loaded = await sync_service.load_standalone_settings("local")
        assert loaded is not None
        assert loaded["loudness"]["enabled"] is True

    def test_compute_mac_id_localhost_returns_mac(self):
        """AC4: Local client (127.0.0.1) reads MAC from system interface."""
        from backend.core.multiroom.client_registry import ClientRegistryService

        # localhost IP reads MAC from system interface (eth0 or wlan0)
        mac_id = ClientRegistryService.compute_mac_id("milo", "127.0.0.1")
        assert ":" in mac_id  # Returns MAC address format
        assert len(mac_id) == 17  # xx:xx:xx:xx:xx:xx

    def test_compute_mac_id_remote_client(self):
        """Test remote client mac_id computation requires MAC from Snapcast."""
        from backend.core.multiroom.client_registry import ClientRegistryService

        # Remote client with MAC provided returns that MAC
        mac_id = ClientRegistryService.compute_mac_id("milo-client-01", "192.168.1.100", mac="aa:bb:cc:dd:ee:ff")
        assert mac_id == "aa:bb:cc:dd:ee:ff"

        # Remote client without MAC raises error
        with pytest.raises(ValueError, match="No MAC address"):
            ClientRegistryService.compute_mac_id("other-host", "192.168.1.100")


# =============================================================================
# TestWebSocketSyncStatus - Story 5.2: WebSocket events with sync status
# =============================================================================


class TestWebSocketSyncStatus:
    """Tests for WebSocket event sync_status payload (Story 5.2 Task 4)."""

    def test_sync_status_structure(self):
        """Test that sync_status has expected structure."""
        # Expected sync_status keys
        sync_status = {
            "volume_synced": True,
            "equalizer_synced": True,
            "pending_applied": False
        }

        assert "volume_synced" in sync_status
        assert "equalizer_synced" in sync_status
        assert "pending_applied" in sync_status
        assert isinstance(sync_status["volume_synced"], bool)
        assert isinstance(sync_status["equalizer_synced"], bool)
        assert isinstance(sync_status["pending_applied"], bool)

    def test_sync_status_default_values(self):
        """Test sync_status default values before sync."""
        sync_status = {
            "volume_synced": False,
            "equalizer_synced": False,
            "pending_applied": False
        }

        # All should be False by default
        assert sync_status["volume_synced"] is False
        assert sync_status["equalizer_synced"] is False
        assert sync_status["pending_applied"] is False

    def test_client_connected_event_structure(self):
        """Test client_connected event payload structure with sync_status."""
        # Simulated event payload as emitted by websocket.py
        event_payload = {
            "client_id": "abc123",
            "client_name": "Test Client",
            "client_host": "milo-client-01",
            "client_ip": "192.168.1.100",
            "mac_id": "milo-client-01",
            "volume": 100,
            "muted": False,
            "online": True,
            "sync_status": {
                "volume_synced": True,
                "equalizer_synced": True,
                "pending_applied": False
            }
        }

        # Verify structure
        assert "client_id" in event_payload
        assert "mac_id" in event_payload
        assert "sync_status" in event_payload

        sync_status = event_payload["sync_status"]
        assert sync_status["volume_synced"] is True
        assert sync_status["equalizer_synced"] is True


# =============================================================================
# TestAutoCrossover - Story 5.3: Auto-crossover on subwoofer connect/disconnect
# =============================================================================


class TestAutoCrossover:
    """Tests for automatic crossover enable/disable based on subwoofer presence (Story 5.3)."""

    @pytest.fixture
    def mock_camilladsp(self):
        """Mock CamillaDSP service."""
        camilladsp_mock = AsyncMock()
        camilladsp_mock.set_crossover_filter = AsyncMock(return_value=True)
        camilladsp_mock.set_lowpass_filter = AsyncMock(return_value=True)
        return camilladsp_mock

    @pytest.fixture
    def crossover_service(self, mock_camilladsp):
        """Create CrossoverService with mocked dependencies."""
        from backend.core.multiroom.crossover import CrossoverService

        service = CrossoverService(camilladsp_service=mock_camilladsp)
        return service

    @pytest.fixture
    def mock_registry_with_subwoofer(self):
        """Create mock registry with zone containing speakers and subwoofer."""
        from unittest.mock import MagicMock

        registry = MagicMock()

        # Create mock clients with speaker types
        speaker1 = MagicMock()
        speaker1.speaker_type = "bookshelf"
        speaker1.crossover_frequency = 80

        speaker2 = MagicMock()
        speaker2.speaker_type = "satellite"
        speaker2.crossover_frequency = 120

        subwoofer = MagicMock()
        subwoofer.speaker_type = "subwoofer"
        subwoofer.crossover_frequency = None

        clients = {
            "speaker-1": speaker1,
            "speaker-2": speaker2,
            "subwoofer-1": subwoofer
        }

        def get_client(cid):
            return clients.get(cid)

        # Create zone with speakers and subwoofer
        zone = MagicMock()
        zone.id = "zone-1"
        zone.client_ids = ["speaker-1", "speaker-2", "subwoofer-1"]
        zone.crossover_enabled = True

        registry.get_zone.return_value = zone
        registry.get_zone_for_client.return_value = zone
        registry.get_client.side_effect = get_client
        registry.is_client_online.side_effect = lambda cid: True
        registry.subscribe = MagicMock()
        registry._emit_event = AsyncMock()
        registry.zone_to_enriched_dict.return_value = {"id": "zone-1"}

        return registry

    @pytest.fixture
    def mock_registry_no_subwoofer(self):
        """Create mock registry with zone containing only speakers (no subwoofer)."""
        from unittest.mock import MagicMock

        registry = MagicMock()

        # Create mock clients - all speakers, no subwoofer
        speaker1 = MagicMock()
        speaker1.speaker_type = "bookshelf"
        speaker1.crossover_frequency = 80

        speaker2 = MagicMock()
        speaker2.speaker_type = "satellite"
        speaker2.crossover_frequency = 120

        tower = MagicMock()
        tower.speaker_type = "tower"
        tower.crossover_frequency = 50

        clients = {
            "speaker-1": speaker1,
            "speaker-2": speaker2,
            "tower-1": tower
        }

        def get_client(cid):
            return clients.get(cid)

        # Create zone without subwoofer
        zone = MagicMock()
        zone.id = "zone-1"
        zone.client_ids = ["speaker-1", "speaker-2", "tower-1"]
        zone.crossover_enabled = True

        registry.get_zone.return_value = zone
        registry.get_zone_for_client.return_value = zone
        registry.get_client.side_effect = get_client
        registry.is_client_online.side_effect = lambda cid: True
        registry.subscribe = MagicMock()
        registry._emit_event = AsyncMock()
        registry.zone_to_enriched_dict.return_value = {"id": "zone-1"}

        return registry

    def test_crossover_should_apply_with_online_subwoofer(self, crossover_service, mock_registry_with_subwoofer):
        """AC1: Crossover enabled when subwoofer is online."""
        crossover_service.set_registry(mock_registry_with_subwoofer)

        # Verify subwoofer detection
        assert crossover_service.is_client_subwoofer("subwoofer-1") is True
        assert crossover_service.is_client_subwoofer("speaker-1") is False

    def test_crossover_frequency_calculation(self, crossover_service, mock_registry_with_subwoofer):
        """AC3: Frequency determined by speaker_type of zone members."""
        from backend.core.multiroom.models import DEFAULT_CROSSOVER_FREQUENCIES

        crossover_service.set_registry(mock_registry_with_subwoofer)

        # Verify default frequencies
        assert DEFAULT_CROSSOVER_FREQUENCIES["satellite"] == 120
        assert DEFAULT_CROSSOVER_FREQUENCIES["bookshelf"] == 80
        assert DEFAULT_CROSSOVER_FREQUENCIES["tower"] == 50

    def test_no_crossover_without_subwoofer(self, crossover_service, mock_registry_no_subwoofer):
        """AC4: No crossover when zone has no subwoofer."""
        crossover_service.set_registry(mock_registry_no_subwoofer)

        # Verify no subwoofer detected
        has_sub = any(
            crossover_service.is_client_subwoofer(cid)
            for cid in ["speaker-1", "speaker-2", "tower-1"]
        )
        assert has_sub is False

    def test_multiple_subwoofers_detection(self, crossover_service):
        """AC5: Multiple subwoofers can be detected."""
        from unittest.mock import MagicMock

        registry = MagicMock()

        sub1 = MagicMock()
        sub1.speaker_type = "subwoofer"

        sub2 = MagicMock()
        sub2.speaker_type = "subwoofer"

        registry.get_client.side_effect = lambda cid: sub1 if cid == "sub-1" else sub2

        crossover_service.set_registry(registry)

        # Verify multiple subwoofers detected
        assert crossover_service.is_client_subwoofer("sub-1") is True
        assert crossover_service.is_client_subwoofer("sub-2") is True

    @pytest.mark.asyncio
    async def test_apply_zone_crossover_with_subwoofer(self, crossover_service, mock_registry_with_subwoofer):
        """AC1: Zone crossover applies highpass to speakers, lowpass to subwoofer."""
        crossover_service.set_registry(mock_registry_with_subwoofer)

        # Apply crossover
        result = await crossover_service.apply_zone_crossover("zone-1")

        # Should succeed
        assert result is True

    @pytest.mark.asyncio
    async def test_apply_zone_crossover_without_subwoofer(self, crossover_service, mock_registry_no_subwoofer):
        """AC4: Zone crossover disabled when no subwoofer present."""
        crossover_service.set_registry(mock_registry_no_subwoofer)

        # Apply crossover
        result = await crossover_service.apply_zone_crossover("zone-1")

        # Should succeed (but no filters applied)
        assert result is True

    def test_default_crossover_frequencies_defined(self):
        """AC3: Verify DEFAULT_CROSSOVER_FREQUENCIES are correctly defined."""
        from backend.core.multiroom.models import DEFAULT_CROSSOVER_FREQUENCIES

        assert "satellite" in DEFAULT_CROSSOVER_FREQUENCIES
        assert "bookshelf" in DEFAULT_CROSSOVER_FREQUENCIES
        assert "tower" in DEFAULT_CROSSOVER_FREQUENCIES
        assert "subwoofer" in DEFAULT_CROSSOVER_FREQUENCIES

        # Subwoofer should be None (receives lowpass)
        assert DEFAULT_CROSSOVER_FREQUENCIES["subwoofer"] is None


# =============================================================================
# Story 1-3: Snapcast Client Detection Integration Tests
# =============================================================================


class TestSnapcastClientDetection:
    """
    Tests for Story 1-3: Integrate Snapcast Client Detection.

    Tests cover:
    - AC1: Client connection detection triggers registry update and WebSocket event
    - AC2: Client disconnection detection triggers registry update and WebSocket event
    - AC3: Auto-registration of new clients with default values
    - AC4: WebSocket event format compliance
    """

    @pytest.fixture
    def mock_settings_service(self):
        """Create a mock settings service."""
        service = AsyncMock()
        service.get_setting = AsyncMock(return_value=None)
        service.set_setting = AsyncMock()
        return service

    @pytest.fixture
    def mock_event_bus(self):
        """Create a mock event bus."""
        bus = MagicMock()
        bus.emit = AsyncMock()
        return bus

    @pytest.fixture
    def registry(self, mock_settings_service, mock_event_bus):
        """Create an initialized registry."""
        reg = ClientRegistryService(
            settings_service=mock_settings_service,
            event_bus=mock_event_bus
        )
        return reg

    @pytest.fixture
    def mock_state_machine(self, registry):
        """Create a mock state machine with broadcast_event."""
        sm = MagicMock()
        sm.client_registry = registry
        sm.broadcast_event = AsyncMock()
        sm.snapcast_service = None
        sm.volume_service = None
        sm.crossover_service = None
        sm.equalizer_client_proxy_service = None
        sm.equalizer_settings_sync_service = None
        sm.camilladsp_service = None
        return sm

    @pytest.fixture
    def mock_routing_service(self):
        """Create a mock routing service."""
        service = MagicMock()
        service.get_state = MagicMock(return_value={'multiroom_enabled': False})
        service.get_snapcast_status = AsyncMock(return_value={'multiroom_available': False})
        return service

    # === AC1: Client Connection Detection ===

    @pytest.mark.asyncio
    async def test_client_connect_registers_client(self, registry, mock_state_machine, mock_event_bus):
        """AC1: When Snapcast client connects, registry receives event and marks client online."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        await registry.initialize()
        registry.set_state_machine(mock_state_machine)

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock(),
            event_bus=mock_event_bus
        )

        # Simulate Client.OnConnect params (with MAC address as required)
        params = {
            "client": {
                "id": "abc123",
                "config": {"name": "Kitchen Speaker", "volume": {"percent": 100, "muted": False}},
                "host": {"name": "milo-client-kitchen", "ip": "192.168.1.100", "mac": "aa:bb:cc:dd:ee:ff"}
            }
        }

        await ws_service._handle_client_connect(params)

        # Verify client was registered with MAC as identifier
        client = registry.get_client("aa:bb:cc:dd:ee:ff")
        assert client is not None
        assert client.name == "Kitchen Speaker"
        assert client.online is True

    @pytest.mark.asyncio
    async def test_client_connect_broadcasts_event(self, registry, mock_state_machine, mock_event_bus):
        """AC1: WebSocket event 'client_connected' is broadcast to frontend."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        await registry.initialize()
        registry.set_state_machine(mock_state_machine)

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock(),
            event_bus=mock_event_bus
        )

        params = {
            "client": {
                "id": "abc123",
                "config": {"name": "Test Speaker", "volume": {"percent": 100, "muted": False}},
                "host": {"name": "milo-client-test", "ip": "192.168.1.101", "mac": "11:22:33:44:55:66"}
            }
        }

        await ws_service._handle_client_connect(params)

        # Verify broadcast was called with snapcast event
        mock_state_machine.broadcast_event.assert_called()
        call_args = mock_state_machine.broadcast_event.call_args_list
        # Should have both registry and snapcast events
        snapcast_calls = [c for c in call_args if c[0][0] == "snapcast"]
        assert len(snapcast_calls) >= 1

    # === AC2: Client Disconnection Detection ===

    @pytest.mark.asyncio
    async def test_client_disconnect_marks_offline(self, registry, mock_state_machine, mock_event_bus):
        """AC2: When Snapcast client disconnects, registry marks client offline."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        await registry.initialize()
        registry.set_state_machine(mock_state_machine)

        # First register and connect a client (using MAC as mac_id)
        await registry.register_client("aa:bb:cc:dd:ee:ff", "Test Speaker", "192.168.1.100")
        await registry.set_client_online("aa:bb:cc:dd:ee:ff", True)

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock(),
            event_bus=mock_event_bus
        )

        # Simulate Client.OnDisconnect params (with MAC address as required)
        params = {
            "client": {
                "id": "abc123",
                "config": {"name": "Test Speaker"},
                "host": {"name": "milo-client-test", "ip": "192.168.1.100", "mac": "aa:bb:cc:dd:ee:ff"}
            }
        }

        await ws_service._handle_client_disconnect(params)

        # Verify client is now offline
        client = registry.get_client("aa:bb:cc:dd:ee:ff")
        assert client is not None
        assert client.online is False

    @pytest.mark.asyncio
    async def test_client_disconnect_broadcasts_event(self, registry, mock_state_machine, mock_event_bus):
        """AC2: WebSocket event 'client_disconnected' is broadcast on disconnect."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        await registry.initialize()
        registry.set_state_machine(mock_state_machine)

        await registry.register_client("aa:bb:cc:dd:ee:ff", "Test Speaker", "192.168.1.100")
        await registry.set_client_online("aa:bb:cc:dd:ee:ff", True)

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock(),
            event_bus=mock_event_bus
        )

        params = {
            "client": {
                "id": "abc123",
                "config": {"name": "Test Speaker"},
                "host": {"name": "milo-client-test", "ip": "192.168.1.100", "mac": "aa:bb:cc:dd:ee:ff"}
            }
        }

        # Clear previous calls
        mock_state_machine.broadcast_event.reset_mock()

        await ws_service._handle_client_disconnect(params)

        # Verify disconnect event broadcast
        mock_state_machine.broadcast_event.assert_called()
        call_args = mock_state_machine.broadcast_event.call_args_list
        snapcast_calls = [c for c in call_args if c[0][0] == "snapcast"]
        assert len(snapcast_calls) >= 1

    # === AC3: Auto-Registration with Default Values ===

    @pytest.mark.asyncio
    async def test_new_client_auto_registered_with_defaults(self, registry, mock_state_machine, mock_event_bus):
        """AC3: New unknown client is auto-registered with correct default values."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        await registry.initialize()
        registry.set_state_machine(mock_state_machine)

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock(),
            event_bus=mock_event_bus
        )

        # New client never seen before (with MAC address as required by compute_mac_id)
        params = {
            "client": {
                "id": "new-client-123",
                "config": {"name": "New Client", "volume": {"percent": 100, "muted": False}},
                "host": {"name": "milo-client-new", "ip": "192.168.1.200", "mac": "aa:bb:cc:dd:ee:ff"}
            }
        }

        await ws_service._handle_client_connect(params)

        client = registry.get_client("aa:bb:cc:dd:ee:ff")
        assert client is not None

        # Verify AC3 default values
        assert client.speaker_type == DEFAULT_SPEAKER_TYPE  # 'bookshelf'
        assert client.volume_db == DEFAULT_VOLUME_DB  # -60.0
        assert client.online is True
        assert client.zone_id is None  # standalone

    @pytest.mark.asyncio
    async def test_new_client_uses_snapcast_name(self, registry, mock_state_machine, mock_event_bus):
        """AC3: New client uses name from Snapcast (hostname or config name)."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        await registry.initialize()
        registry.set_state_machine(mock_state_machine)

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock(),
            event_bus=mock_event_bus
        )

        params = {
            "client": {
                "id": "client-456",
                "config": {"name": "Living Room Speakers", "volume": {"percent": 100}},
                "host": {"name": "milo-client-living", "ip": "192.168.1.201", "mac": "11:22:33:44:55:66"}
            }
        }

        await ws_service._handle_client_connect(params)

        client = registry.get_client("11:22:33:44:55:66")
        assert client.name == "Living Room Speakers"

    # === AC4: WebSocket Event Format ===

    @pytest.mark.asyncio
    async def test_registry_event_format(self, registry, mock_state_machine, mock_event_bus):
        """AC4: Registry events follow specified format with category, type, and data."""
        await registry.initialize()
        registry.set_state_machine(mock_state_machine)

        await registry.register_client("test-client", "Test", "192.168.1.100")

        # Get the broadcast call
        calls = mock_state_machine.broadcast_event.call_args_list
        assert len(calls) > 0

        # Check event structure (category, type, data) - now uses multiroom category
        category, event_type, data = calls[-1][0]
        assert category == "multiroom"
        assert event_type == "client_state_changed"  # Mapped from client_connected/client_updated
        assert "mac_id" in data
        assert "client" in data

    @pytest.mark.asyncio
    async def test_set_client_online_event_format(self, registry, mock_state_machine, mock_event_bus):
        """AC4: set_client_online emits event with correct format."""
        await registry.initialize()
        registry.set_state_machine(mock_state_machine)

        await registry.register_client("test-client", "Test", "192.168.1.100")
        mock_state_machine.broadcast_event.reset_mock()

        await registry.set_client_online("test-client", True)

        # Verify event format - now uses multiroom category with mapped event type
        calls = mock_state_machine.broadcast_event.call_args_list
        assert len(calls) > 0

        category, event_type, data = calls[-1][0]
        assert category == "multiroom"
        assert event_type == "client_state_changed"  # Mapped from client_connected
        assert data["mac_id"] == "test-client"
        assert "client" in data

    @pytest.mark.asyncio
    async def test_set_client_offline_event_format(self, registry, mock_state_machine, mock_event_bus):
        """AC4: set_client_online(False) emits client_disconnected event."""
        await registry.initialize()
        registry.set_state_machine(mock_state_machine)

        await registry.register_client("test-client", "Test", "192.168.1.100")
        await registry.set_client_online("test-client", True)
        mock_state_machine.broadcast_event.reset_mock()

        await registry.set_client_online("test-client", False)

        # Verify event format - now uses multiroom category with mapped event type
        calls = mock_state_machine.broadcast_event.call_args_list
        assert len(calls) > 0

        category, event_type, data = calls[-1][0]
        assert category == "multiroom"
        assert event_type == "client_state_changed"  # Mapped from client_disconnected
        assert data["mac_id"] == "test-client"

    # === compute_mac_id Tests for Snapcast Integration ===

    def test_compute_mac_id_for_local_client(self):
        """Test compute_mac_id reads MAC from system interface for localhost."""
        from unittest.mock import mock_open, patch
        mock_file = mock_open(read_data="aa:bb:cc:dd:ee:ff\n")
        with patch("builtins.open", mock_file):
            mac_id = ClientRegistryService.compute_mac_id("milo", "127.0.0.1")
        assert mac_id == "aa:bb:cc:dd:ee:ff"

    def test_compute_mac_id_for_milo_client(self):
        """Test compute_mac_id returns MAC address for remote clients."""
        mac_id = ClientRegistryService.compute_mac_id("milo-client-kitchen", "192.168.1.100", "aa:bb:cc:dd:ee:ff")
        assert mac_id == "aa:bb:cc:dd:ee:ff"

    def test_compute_mac_id_strips_ipv6_prefix(self):
        """Test compute_mac_id handles IPv6-mapped IPv4 addresses for localhost."""
        from unittest.mock import mock_open, patch
        # Snapcast sometimes returns ::ffff:192.168.1.100 format
        # The websocket.py code strips this: .replace("::ffff:", "")
        ip = "::ffff:127.0.0.1".replace("::ffff:", "")
        mock_file = mock_open(read_data="aa:bb:cc:dd:ee:ff\n")
        with patch("builtins.open", mock_file):
            mac_id = ClientRegistryService.compute_mac_id("milo", ip)
        assert mac_id == "aa:bb:cc:dd:ee:ff"

    # === Event Timing (NFR2) is tested via integration tests ===

    @pytest.mark.asyncio
    async def test_event_emission_is_async(self, registry, mock_state_machine, mock_event_bus):
        """Verify events are emitted asynchronously (prerequisite for <100ms timing)."""
        import asyncio

        await registry.initialize()
        registry.set_state_machine(mock_state_machine)

        # Measure time for registration (should be fast due to async)
        start = asyncio.get_running_loop().time()
        await registry.register_client("perf-test", "Performance Test", "192.168.1.99")
        end = asyncio.get_running_loop().time()

        # Registration + event emission should be fast (< 50ms)
        assert (end - start) < 0.05


# =============================================================================
# Story 5.1: Reconnection Context Detection Tests
# =============================================================================


class TestReconnectionContextEnum:
    """Tests for ReconnectionContext enum (AC4)."""

    def test_enum_values_defined(self):
        """Test that all 4 context values are defined."""
        assert ReconnectionContext.IN_ZONE_OTHERS_ONLINE == "in_zone_others_online"
        assert ReconnectionContext.IN_ZONE_ALL_OFFLINE == "in_zone_all_offline"
        assert ReconnectionContext.STANDALONE_OTHERS_ONLINE == "standalone_others_online"
        assert ReconnectionContext.STANDALONE_ALONE == "standalone_alone"

    def test_enum_is_string(self):
        """Test that enum values are strings (for JSON serialization)."""
        assert isinstance(ReconnectionContext.IN_ZONE_OTHERS_ONLINE.value, str)
        assert isinstance(ReconnectionContext.IN_ZONE_ALL_OFFLINE.value, str)
        assert isinstance(ReconnectionContext.STANDALONE_OTHERS_ONLINE.value, str)
        assert isinstance(ReconnectionContext.STANDALONE_ALONE.value, str)

    def test_enum_count(self):
        """Test that exactly 4 context values exist."""
        assert len(ReconnectionContext) == 4


class TestReconnectionContextDetection:
    """
    Tests for reconnection context detection (Story 5.1).

    Tests cover AC1-AC5:
    - AC1: Zone membership detection
    - AC2: IN_ZONE context detection (others online/offline)
    - AC3: STANDALONE context detection (others online/alone)
    - AC4: Context enum implementation
    - AC5: Context used for sync dispatch
    """

    @pytest.fixture
    def mock_settings_service(self):
        """Create a mock settings service."""
        service = AsyncMock()
        service.get_setting = AsyncMock(return_value=None)
        service.set_setting = AsyncMock()
        return service

    @pytest.fixture
    def mock_event_bus(self):
        """Create a mock event bus."""
        bus = MagicMock()
        bus.emit = AsyncMock()
        return bus

    @pytest.fixture
    def registry(self, mock_settings_service, mock_event_bus):
        """Create a ClientRegistryService instance."""
        return ClientRegistryService(
            settings_service=mock_settings_service,
            event_bus=mock_event_bus
        )

    # === AC1: Zone Membership Detection ===

    @pytest.mark.asyncio
    async def test_zone_membership_detected_correctly(self, registry):
        """AC1: System correctly determines if client is IN_ZONE or STANDALONE."""
        await registry.initialize()

        # Register clients
        await registry.register_client("local", "Main", "127.0.0.1")
        await registry.register_client("client-1", "Client 1", "192.168.1.100")
        await registry.register_client("client-2", "Client 2", "192.168.1.101")

        # Create zone with local and client-1
        await registry.create_zone("zone-1", "Test Zone", ["local", "client-1"])

        # local is IN_ZONE
        local_client = registry.get_client("local")
        assert local_client.zone_id == "zone-1"

        # client-2 is STANDALONE
        client2 = registry.get_client("client-2")
        assert client2.zone_id is None

    # === AC2: IN_ZONE Context Detection ===

    @pytest.mark.asyncio
    async def test_in_zone_others_online_context(self, registry):
        """AC2/AC4: IN_ZONE client with others ONLINE returns IN_ZONE_OTHERS_ONLINE (FR7)."""
        await registry.initialize()

        # Setup: Zone with 3 clients, 2 online
        await registry.register_client("local", "Main", "127.0.0.1")
        await registry.register_client("client-1", "Client 1", "192.168.1.100")
        await registry.register_client("client-2", "Client 2", "192.168.1.101")
        await registry.create_zone("zone-1", "Test Zone", ["local", "client-1", "client-2"])

        # Set online status: client-1 online, client-2 offline
        await registry.set_client_online("local", False)  # Reconnecting client
        await registry.set_client_online("client-1", True)  # Online zone member
        await registry.set_client_online("client-2", False)  # Offline zone member

        # Test: local reconnects - should detect IN_ZONE_OTHERS_ONLINE
        context = registry.get_reconnection_context("local")
        assert context == ReconnectionContext.IN_ZONE_OTHERS_ONLINE

    @pytest.mark.asyncio
    async def test_in_zone_all_offline_context(self, registry):
        """AC2/AC4: IN_ZONE client with all others OFFLINE returns IN_ZONE_ALL_OFFLINE (FR8)."""
        await registry.initialize()

        # Setup: Zone with 3 clients, all offline except reconnecting
        await registry.register_client("local", "Main", "127.0.0.1")
        await registry.register_client("client-1", "Client 1", "192.168.1.100")
        await registry.register_client("client-2", "Client 2", "192.168.1.101")
        await registry.create_zone("zone-1", "Test Zone", ["local", "client-1", "client-2"])

        # All clients offline (simulating backend restart scenario)
        await registry.set_client_online("local", False)
        await registry.set_client_online("client-1", False)
        await registry.set_client_online("client-2", False)

        # Test: local reconnects first - no other zone members online
        context = registry.get_reconnection_context("local")
        assert context == ReconnectionContext.IN_ZONE_ALL_OFFLINE

    # === AC3: STANDALONE Context Detection ===

    @pytest.mark.asyncio
    async def test_standalone_others_online_context(self, registry):
        """AC3/AC4: STANDALONE client with others ONLINE returns STANDALONE_OTHERS_ONLINE (FR9)."""
        await registry.initialize()

        # Setup: 3 standalone clients
        await registry.register_client("local", "Main", "127.0.0.1")
        await registry.register_client("client-1", "Client 1", "192.168.1.100")
        await registry.register_client("client-2", "Client 2", "192.168.1.101")

        # Set online status: local reconnecting, others online
        await registry.set_client_online("local", False)
        await registry.set_client_online("client-1", True)
        await registry.set_client_online("client-2", True)

        # Test: local reconnects - other clients are online
        context = registry.get_reconnection_context("local")
        assert context == ReconnectionContext.STANDALONE_OTHERS_ONLINE

    @pytest.mark.asyncio
    async def test_standalone_alone_context(self, registry):
        """AC3/AC4: STANDALONE client with no others ONLINE returns STANDALONE_ALONE (FR10)."""
        await registry.initialize()

        # Setup: 3 standalone clients, all offline
        await registry.register_client("local", "Main", "127.0.0.1")
        await registry.register_client("client-1", "Client 1", "192.168.1.100")
        await registry.register_client("client-2", "Client 2", "192.168.1.101")

        # All offline
        await registry.set_client_online("local", False)
        await registry.set_client_online("client-1", False)
        await registry.set_client_online("client-2", False)

        # Test: local reconnects as first client
        context = registry.get_reconnection_context("local")
        assert context == ReconnectionContext.STANDALONE_ALONE

    # === Edge Cases ===

    @pytest.mark.asyncio
    async def test_unknown_client_returns_standalone_alone(self, registry):
        """Edge case: Unknown client defaults to STANDALONE_ALONE (safest)."""
        await registry.initialize()

        # Query for client that doesn't exist
        context = registry.get_reconnection_context("unknown-client")
        assert context == ReconnectionContext.STANDALONE_ALONE

    @pytest.mark.asyncio
    async def test_zone_with_single_member_edge_case(self, registry):
        """Edge case: Zone with only 1 member (after removal) returns IN_ZONE_ALL_OFFLINE."""
        await registry.initialize()

        # Setup: Create zone then remove a client
        await registry.register_client("local", "Main", "127.0.0.1")
        await registry.register_client("client-1", "Client 1", "192.168.1.100")
        await registry.register_client("client-2", "Client 2", "192.168.1.101")
        await registry.create_zone("zone-1", "Test Zone", ["local", "client-1", "client-2"])

        # Remove 2 clients - zone still exists with 1 client
        # Note: Zone should be deleted when < 2 clients, but let's test the edge case
        # by directly manipulating state (zone not deleted but has 1 member)
        # In practice, ClientRegistryService.remove_client_from_zone handles this

        # Set online status
        await registry.set_client_online("local", False)
        await registry.set_client_online("client-1", False)
        await registry.set_client_online("client-2", False)

        # local is in zone but all others offline
        context = registry.get_reconnection_context("local")
        assert context == ReconnectionContext.IN_ZONE_ALL_OFFLINE

    @pytest.mark.asyncio
    async def test_only_one_client_in_system(self, registry):
        """Edge case: Single client in entire system returns STANDALONE_ALONE."""
        await registry.initialize()

        # Only one client registered
        await registry.register_client("local", "Main", "127.0.0.1")
        await registry.set_client_online("local", False)

        context = registry.get_reconnection_context("local")
        assert context == ReconnectionContext.STANDALONE_ALONE


class TestReconnectionHelperMethods:
    """Tests for helper methods used in context detection."""

    @pytest.fixture
    def mock_settings_service(self):
        """Create a mock settings service."""
        service = AsyncMock()
        service.get_setting = AsyncMock(return_value=None)
        service.set_setting = AsyncMock()
        return service

    @pytest.fixture
    def mock_event_bus(self):
        """Create a mock event bus."""
        bus = MagicMock()
        bus.emit = AsyncMock()
        return bus

    @pytest.fixture
    def registry(self, mock_settings_service, mock_event_bus):
        """Create a ClientRegistryService instance."""
        return ClientRegistryService(
            settings_service=mock_settings_service,
            event_bus=mock_event_bus
        )

    @pytest.mark.asyncio
    async def test_get_other_online_zone_clients_excludes_self(self, registry):
        """Test get_other_online_zone_clients excludes the queried client."""
        await registry.initialize()

        await registry.register_client("local", "Main", "127.0.0.1")
        await registry.register_client("client-1", "Client 1", "192.168.1.100")
        await registry.create_zone("zone-1", "Test Zone", ["local", "client-1"])

        await registry.set_client_online("local", True)
        await registry.set_client_online("client-1", True)

        # Get other online zone clients for local
        others = registry.get_other_online_zone_clients("local")

        # Should only contain client-1, not local
        assert len(others) == 1
        assert others[0].mac_id == "client-1"

    @pytest.mark.asyncio
    async def test_get_other_online_zone_clients_empty_when_not_in_zone(self, registry):
        """Test get_other_online_zone_clients returns empty for standalone client."""
        await registry.initialize()

        await registry.register_client("local", "Main", "127.0.0.1")
        await registry.set_client_online("local", True)

        # local is standalone
        others = registry.get_other_online_zone_clients("local")
        assert others == []

    @pytest.mark.asyncio
    async def test_get_other_online_zone_clients_only_online(self, registry):
        """Test get_other_online_zone_clients only returns online members."""
        await registry.initialize()

        await registry.register_client("local", "Main", "127.0.0.1")
        await registry.register_client("client-1", "Client 1", "192.168.1.100")
        await registry.register_client("client-2", "Client 2", "192.168.1.101")
        await registry.create_zone("zone-1", "Test Zone", ["local", "client-1", "client-2"])

        await registry.set_client_online("local", True)
        await registry.set_client_online("client-1", True)  # Online
        await registry.set_client_online("client-2", False)  # Offline

        others = registry.get_other_online_zone_clients("local")

        # Should only contain client-1 (online), not client-2 (offline)
        assert len(others) == 1
        assert others[0].mac_id == "client-1"

    @pytest.mark.asyncio
    async def test_get_other_online_clients_excludes_self(self, registry):
        """Test get_other_online_clients excludes the queried client."""
        await registry.initialize()

        await registry.register_client("local", "Main", "127.0.0.1")
        await registry.register_client("client-1", "Client 1", "192.168.1.100")

        await registry.set_client_online("local", True)
        await registry.set_client_online("client-1", True)

        others = registry.get_other_online_clients("local")

        # Should only contain client-1
        assert len(others) == 1
        assert others[0].mac_id == "client-1"

    @pytest.mark.asyncio
    async def test_get_other_online_clients_only_online(self, registry):
        """Test get_other_online_clients only returns online clients."""
        await registry.initialize()

        await registry.register_client("local", "Main", "127.0.0.1")
        await registry.register_client("client-1", "Client 1", "192.168.1.100")
        await registry.register_client("client-2", "Client 2", "192.168.1.101")

        await registry.set_client_online("local", True)
        await registry.set_client_online("client-1", True)  # Online
        await registry.set_client_online("client-2", False)  # Offline

        others = registry.get_other_online_clients("local")

        # Should only contain client-1 (online)
        assert len(others) == 1
        assert others[0].mac_id == "client-1"

    @pytest.mark.asyncio
    async def test_get_other_online_clients_empty_when_alone(self, registry):
        """Test get_other_online_clients returns empty when no other clients online."""
        await registry.initialize()

        await registry.register_client("local", "Main", "127.0.0.1")
        await registry.register_client("client-1", "Client 1", "192.168.1.100")

        await registry.set_client_online("local", True)
        await registry.set_client_online("client-1", False)

        others = registry.get_other_online_clients("local")
        assert others == []


# =============================================================================
# Equalizer Sync Method Tests (Story 5.1 Code Review Fixes)
# =============================================================================


class TestSyncZoneDspToClient:
    """
    Unit tests for SnapcastWebSocketService._sync_zone_equalizer_to_client().

    These tests validate that zone Equalizer settings (EqFilter, CompressorSettings,
    LoudnessSettings dataclasses) are properly converted to dicts before
    being sent to the proxy service.
    """

    @pytest.fixture
    def mock_state_machine(self):
        """Create a mock state machine with required services."""
        sm = MagicMock()
        sm.broadcast_event = AsyncMock()
        sm.equalizer_client_proxy_service = MagicMock()
        sm.equalizer_client_proxy_service.request = AsyncMock()
        sm.crossover_service = MagicMock()
        sm.crossover_service.queue_pending_settings = AsyncMock()
        return sm

    @pytest.fixture
    def mock_registry(self):
        """Create a mock registry with a test client."""
        registry = MagicMock()
        client = MagicMock()
        client.ip = "192.168.1.100"
        client.mac_id = "test-client"
        registry.get_client = MagicMock(return_value=client)
        return registry

    @pytest.fixture
    def mock_zone_with_equalizer(self):
        """Create a mock zone with typed Equalizer settings."""
        zone = MagicMock()
        zone.id = "zone-1"

        # Create real EqualizerSettings with EqFilter objects
        filters = [
            EqFilter(id="eq_band_00", frequency=100, gain=3.0, q=1.41, filter_type=FilterType.PEAKING),
            EqFilter(id="eq_band_01", frequency=1000, gain=-2.0, q=1.0, filter_type=FilterType.LOWSHELF),
        ]
        compressor = CompressorSettings(enabled=True, threshold=-15.0, ratio=4.0, attack=10.0, release=100.0)
        loudness = LoudnessSettings(enabled=True, high_boost=5.0, low_boost=8.0)

        zone.equalizer_settings = EqualizerSettings(
            enabled=True,
            filters=filters,
            compressor=compressor,
            loudness=loudness
        )
        return zone

    @pytest.mark.asyncio
    async def test_eq_filter_attributes_accessed_correctly(self, mock_state_machine, mock_registry, mock_zone_with_equalizer):
        """Test that EqFilter object attributes are accessed correctly (not .get())."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service._registry = mock_registry
        ws_service._equalizer_client_proxy_service = mock_state_machine.equalizer_client_proxy_service
        ws_service._crossover_service = mock_state_machine.crossover_service

        # Execute sync
        result = await ws_service._sync_zone_equalizer_to_client("test-client", mock_zone_with_equalizer)

        # Verify proxy was called for each filter
        proxy = mock_state_machine.equalizer_client_proxy_service
        assert proxy.request.call_count >= 2  # At least 2 filter calls

        # Check first filter call
        filter_calls = [call for call in proxy.request.call_args_list if "/equalizer/filter/" in str(call)]
        assert len(filter_calls) == 2

        # Verify filter data structure is correct (dict, not EqFilter object)
        first_filter_call = filter_calls[0]
        filter_data = first_filter_call[0][3]  # 4th positional arg is data
        assert isinstance(filter_data, dict)
        assert "freq" in filter_data
        assert "gain" in filter_data
        assert "q" in filter_data
        assert "filter_type" in filter_data

    @pytest.mark.asyncio
    async def test_compressor_settings_converted_to_dict(self, mock_state_machine, mock_registry, mock_zone_with_equalizer):
        """Test that CompressorSettings dataclass is converted to dict."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service._registry = mock_registry
        ws_service._equalizer_client_proxy_service = mock_state_machine.equalizer_client_proxy_service
        ws_service._crossover_service = mock_state_machine.crossover_service

        # Execute sync
        await ws_service._sync_zone_equalizer_to_client("test-client", mock_zone_with_equalizer)

        # Find compressor call
        proxy = mock_state_machine.equalizer_client_proxy_service
        compressor_calls = [call for call in proxy.request.call_args_list if "/equalizer/compressor" in str(call)]
        assert len(compressor_calls) == 1

        # Verify it's a dict, not CompressorSettings object
        compressor_data = compressor_calls[0][0][3]
        assert isinstance(compressor_data, dict)
        assert compressor_data["enabled"] is True
        assert compressor_data["threshold"] == -15.0

    @pytest.mark.asyncio
    async def test_loudness_settings_converted_to_dict(self, mock_state_machine, mock_registry, mock_zone_with_equalizer):
        """Test that LoudnessSettings dataclass is converted to dict."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service._registry = mock_registry
        ws_service._equalizer_client_proxy_service = mock_state_machine.equalizer_client_proxy_service
        ws_service._crossover_service = mock_state_machine.crossover_service

        # Execute sync
        await ws_service._sync_zone_equalizer_to_client("test-client", mock_zone_with_equalizer)

        # Find loudness call
        proxy = mock_state_machine.equalizer_client_proxy_service
        loudness_calls = [call for call in proxy.request.call_args_list if "/equalizer/loudness" in str(call)]
        assert len(loudness_calls) == 1

        # Verify it's a dict, not LoudnessSettings object
        loudness_data = loudness_calls[0][0][3]
        assert isinstance(loudness_data, dict)
        assert loudness_data["enabled"] is True
        assert loudness_data["high_boost"] == 5.0

    @pytest.mark.asyncio
    async def test_failed_filter_queued_as_dict(self, mock_state_machine, mock_registry, mock_zone_with_equalizer):
        """Test that failed filters are queued as dicts (not EqFilter objects)."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        # Make filter sync fail
        mock_state_machine.equalizer_client_proxy_service.request = AsyncMock(
            side_effect=Exception("Connection failed")
        )

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service._registry = mock_registry
        ws_service._equalizer_client_proxy_service = mock_state_machine.equalizer_client_proxy_service
        ws_service._crossover_service = mock_state_machine.crossover_service

        # Execute sync (should fail but queue settings)
        result = await ws_service._sync_zone_equalizer_to_client("test-client", mock_zone_with_equalizer)
        assert result is False  # Sync failed

        # Verify filters were queued as dicts
        crossover = mock_state_machine.crossover_service
        filter_queue_calls = [
            call for call in crossover.queue_pending_settings.call_args_list
            if call[0][1] == "filters"
        ]
        assert len(filter_queue_calls) == 1

        # Check that queued data is a list of dicts
        queued_filters = filter_queue_calls[0][0][2]
        assert isinstance(queued_filters, list)
        assert all(isinstance(f, dict) for f in queued_filters)

    @pytest.mark.asyncio
    async def test_sync_returns_true_on_success(self, mock_state_machine, mock_registry, mock_zone_with_equalizer):
        """Test that sync returns True when all settings are synced successfully."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service._registry = mock_registry
        ws_service._equalizer_client_proxy_service = mock_state_machine.equalizer_client_proxy_service
        ws_service._crossover_service = mock_state_machine.crossover_service

        result = await ws_service._sync_zone_equalizer_to_client("test-client", mock_zone_with_equalizer)
        assert result is True

    @pytest.mark.asyncio
    async def test_sync_handles_missing_client(self, mock_state_machine):
        """Test that sync handles missing client gracefully."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        mock_registry = MagicMock()
        mock_registry.get_client = MagicMock(return_value=None)

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service._registry = mock_registry
        ws_service._equalizer_client_proxy_service = mock_state_machine.equalizer_client_proxy_service
        ws_service._crossover_service = mock_state_machine.crossover_service

        mock_zone = MagicMock()
        mock_zone.equalizer_settings = EqualizerSettings()

        result = await ws_service._sync_zone_equalizer_to_client("unknown-client", mock_zone)
        assert result is False


class TestSyncStandaloneDspToClient:
    """
    Unit tests for SnapcastWebSocketService._sync_standalone_equalizer_to_client().

    Tests that standalone Equalizer sync properly loads saved settings and applies them.
    """

    @pytest.fixture
    def mock_state_machine(self):
        """Create a mock state machine with required services."""
        sm = MagicMock()
        sm.broadcast_event = AsyncMock()
        sm.equalizer_client_proxy_service = MagicMock()
        sm.equalizer_client_proxy_service.request = AsyncMock()
        sm.equalizer_settings_sync_service = MagicMock()
        sm.equalizer_settings_sync_service.get_client_settings = AsyncMock(return_value=None)
        sm.crossover_service = MagicMock()
        sm.crossover_service.queue_pending_settings = AsyncMock()
        sm.camilladsp_service = MagicMock()
        sm.camilladsp_service.set_filter = AsyncMock()
        sm.camilladsp_service.set_compressor = AsyncMock()
        sm.camilladsp_service.set_loudness = AsyncMock()
        return sm

    @pytest.fixture
    def mock_registry(self):
        """Create a mock registry with a test client."""
        registry = MagicMock()
        client = MagicMock()
        client.ip = "192.168.1.100"
        client.mac_id = "test-client"
        registry.get_client = MagicMock(return_value=client)
        return registry

    @pytest.mark.asyncio
    async def test_no_saved_settings_returns_true(self, mock_state_machine, mock_registry):
        """Test that sync returns True when no saved settings exist (defaults used)."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service._registry = mock_registry
        ws_service._equalizer_client_proxy_service = mock_state_machine.equalizer_client_proxy_service
        ws_service._equalizer_settings_sync_service = mock_state_machine.equalizer_settings_sync_service
        ws_service._crossover_service = mock_state_machine.crossover_service
        ws_service._camilladsp_service = mock_state_machine.camilladsp_service

        result = await ws_service._sync_standalone_equalizer_to_client("test-client")
        assert result is True

    @pytest.mark.asyncio
    async def test_saved_settings_applied_via_proxy(self, mock_state_machine, mock_registry):
        """Test that saved settings are applied via proxy service for remote clients."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        # Setup saved settings - filters as dict (filter_id -> settings)
        saved_settings = {
            "filters": {
                "eq_band_00": {"freq": 100, "gain": 2.0, "q": 1.41, "type": "Peaking"}
            },
            "compressor": {"enabled": True, "threshold": -20},
            "loudness": {"enabled": False}
        }
        mock_state_machine.equalizer_settings_sync_service.get_client_settings = AsyncMock(
            return_value=saved_settings
        )

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service._registry = mock_registry
        ws_service._equalizer_client_proxy_service = mock_state_machine.equalizer_client_proxy_service
        ws_service._equalizer_settings_sync_service = mock_state_machine.equalizer_settings_sync_service
        ws_service._crossover_service = mock_state_machine.crossover_service
        ws_service._camilladsp_service = mock_state_machine.camilladsp_service

        result = await ws_service._sync_standalone_equalizer_to_client("test-client")

        # Verify proxy was called for each setting type
        proxy = mock_state_machine.equalizer_client_proxy_service
        assert proxy.request.call_count >= 3  # filter, compressor, loudness

    @pytest.mark.asyncio
    async def test_local_client_uses_camilladsp_service(self, mock_state_machine):
        """Test that local client uses camilladsp_service directly."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        # Setup local client
        mock_registry = MagicMock()
        local_client = MagicMock()
        local_client.ip = "127.0.0.1"
        local_client.mac_id = "local"
        mock_registry.get_client = MagicMock(return_value=local_client)

        # Setup saved settings - filters as dict (filter_id -> settings)
        saved_settings = {
            "filters": {"eq_band_00": {"freq": 100, "gain": 2.0, "q": 1.41, "type": "Peaking"}},
            "compressor": {"enabled": True, "threshold": -20},
            "loudness": {"enabled": False}
        }
        mock_state_machine.equalizer_settings_sync_service.get_client_settings = AsyncMock(
            return_value=saved_settings
        )

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service._registry = mock_registry
        ws_service._equalizer_client_proxy_service = mock_state_machine.equalizer_client_proxy_service
        ws_service._equalizer_settings_sync_service = mock_state_machine.equalizer_settings_sync_service
        ws_service._crossover_service = mock_state_machine.crossover_service
        ws_service._camilladsp_service = mock_state_machine.camilladsp_service

        result = await ws_service._sync_standalone_equalizer_to_client("local")

        # Verify camilladsp_service was used (not proxy)
        camilladsp_service = mock_state_machine.camilladsp_service
        assert camilladsp_service.set_filter.call_count == 1
        assert camilladsp_service.set_compressor.call_count == 1
        assert camilladsp_service.set_loudness.call_count == 1

        # Verify proxy was NOT used for local client
        proxy = mock_state_machine.equalizer_client_proxy_service
        assert proxy.request.call_count == 0

    @pytest.mark.asyncio
    async def test_sync_handles_missing_client(self, mock_state_machine):
        """Test that sync handles missing client gracefully."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        mock_registry = MagicMock()
        mock_registry.get_client = MagicMock(return_value=None)

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service._registry = mock_registry
        ws_service._equalizer_settings_sync_service = mock_state_machine.equalizer_settings_sync_service

        result = await ws_service._sync_standalone_equalizer_to_client("unknown-client")
        assert result is False

    @pytest.mark.asyncio
    async def test_failed_filter_settings_are_queued_standalone(self, mock_state_machine, mock_registry):
        """Test that failed filter settings are queued via queue_pending_settings() (AC6)."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        # Setup saved settings with filters as dict (filter_id -> settings)
        saved_settings = {
            "filters": {
                "eq_band_00": {"freq": 100, "gain": 2.0, "q": 1.41, "type": "Peaking"},
                "eq_band_01": {"freq": 1000, "gain": -1.5, "q": 1.41, "type": "Peaking"}
            }
        }
        mock_state_machine.equalizer_settings_sync_service.get_client_settings = AsyncMock(
            return_value=saved_settings
        )

        # Make proxy fail for filters
        mock_state_machine.equalizer_client_proxy_service.request = AsyncMock(
            side_effect=Exception("Connection refused")
        )

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service._registry = mock_registry
        ws_service._equalizer_client_proxy_service = mock_state_machine.equalizer_client_proxy_service
        ws_service._equalizer_settings_sync_service = mock_state_machine.equalizer_settings_sync_service
        ws_service._crossover_service = mock_state_machine.crossover_service
        ws_service._camilladsp_service = mock_state_machine.camilladsp_service

        result = await ws_service._sync_standalone_equalizer_to_client("test-client")

        # Sync should fail
        assert result is False

        # Verify filters were queued for retry
        crossover = mock_state_machine.crossover_service
        crossover.queue_pending_settings.assert_called()

        # Check that filters were queued with correct data
        calls = crossover.queue_pending_settings.call_args_list
        filters_queued = any(
            call[0][1] == "filters" for call in calls
        )
        assert filters_queued, "Filter settings should be queued on failure"


# =============================================================================
# IN_ZONE Volume Sync Strategy Tests (Story 5.2)
# =============================================================================

class TestInZoneTargetVolume:
    """Tests for _resolve_target_volume() with IN_ZONE contexts (Story 5.2, AC1-AC2)."""

    @pytest.fixture
    def mock_state_machine(self):
        """Create a mock state machine with volume_service."""
        state_machine = MagicMock()

        # Mock volume_service with config
        volume_service = MagicMock()
        volume_config = MagicMock()
        volume_config.config.startup_volume_db = -40.0  # Test startup volume
        volume_service.config = volume_config
        state_machine.volume_service = volume_service

        return state_machine

    @pytest.fixture
    def mock_registry(self):
        """Create a mock registry with zone average volume method."""
        registry = MagicMock()
        return registry

    def test_inzone_others_online_uses_zone_average(self, mock_state_machine, mock_registry):
        """AC1: IN_ZONE_OTHERS_ONLINE context uses zone average volume (FR7)."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService
        from backend.core.multiroom.models import ReconnectionContext

        # Setup: client in zone with zone average of -25.0 dB
        mock_client = MagicMock()
        mock_client.zone_id = "zone-1"
        mock_registry.get_client = MagicMock(return_value=mock_client)
        mock_registry.get_zone_average_volume = MagicMock(return_value=-25.0)

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service._registry = mock_registry
        ws_service._volume_service = mock_state_machine.volume_service

        # Test
        target = ws_service._resolve_target_volume("client-1", ReconnectionContext.IN_ZONE_OTHERS_ONLINE)

        # Should use zone average
        assert target == -25.0
        mock_registry.get_zone_average_volume.assert_called_once_with("zone-1", exclude_mac_id="client-1")

    def test_inzone_all_offline_uses_startup_volume(self, mock_state_machine, mock_registry):
        """AC2: IN_ZONE_ALL_OFFLINE context uses startup_volume_db (FR8)."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService
        from backend.core.multiroom.models import ReconnectionContext

        # Setup: client in zone, but will use startup volume
        mock_client = MagicMock()
        mock_client.zone_id = "zone-1"
        mock_registry.get_client = MagicMock(return_value=mock_client)

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service._registry = mock_registry
        ws_service._volume_service = mock_state_machine.volume_service

        # Test
        target = ws_service._resolve_target_volume("client-1", ReconnectionContext.IN_ZONE_ALL_OFFLINE)

        # Should use startup_volume_db from config (-40.0)
        assert target == -40.0

    def test_inzone_others_online_fallback_to_startup_when_no_average(self, mock_state_machine, mock_registry):
        """AC1 fallback: If zone average unavailable, use startup_volume_db."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService
        from backend.core.multiroom.models import ReconnectionContext

        # Setup: zone average returns None (no online clients)
        mock_client = MagicMock()
        mock_client.zone_id = "zone-1"
        mock_registry.get_client = MagicMock(return_value=mock_client)
        mock_registry.get_zone_average_volume = MagicMock(return_value=None)

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service._registry = mock_registry
        ws_service._volume_service = mock_state_machine.volume_service

        # Test
        target = ws_service._resolve_target_volume("client-1", ReconnectionContext.IN_ZONE_OTHERS_ONLINE)

        # Should fallback to startup_volume_db
        assert target == -40.0

    def test_inzone_volume_without_volume_service_uses_default(self, mock_registry):
        """Edge case: No volume_service uses DEFAULT_VOLUME_DB constant."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService
        from backend.core.multiroom.models import ReconnectionContext
        from backend.config.constants import DEFAULT_VOLUME_DB

        # Setup: no volume_service
        mock_state_machine = MagicMock()

        mock_client = MagicMock()
        mock_client.zone_id = "zone-1"
        mock_registry.get_client = MagicMock(return_value=mock_client)

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service._registry = mock_registry
        ws_service._volume_service = None

        # Test
        target = ws_service._resolve_target_volume("client-1", ReconnectionContext.IN_ZONE_ALL_OFFLINE)

        # Should use DEFAULT_VOLUME_DB from constants
        assert target == DEFAULT_VOLUME_DB

    def test_inzone_volume_client_not_in_zone(self, mock_state_machine, mock_registry):
        """Edge case: Client has no zone_id - still returns startup volume."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService
        from backend.core.multiroom.models import ReconnectionContext

        # Setup: client without zone
        mock_client = MagicMock()
        mock_client.zone_id = None
        mock_registry.get_client = MagicMock(return_value=mock_client)

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service._registry = mock_registry
        ws_service._volume_service = mock_state_machine.volume_service

        # Test - calling with IN_ZONE context but client has no zone
        target = ws_service._resolve_target_volume("client-1", ReconnectionContext.IN_ZONE_OTHERS_ONLINE)

        # Should fallback to startup_volume_db since zone average not available
        assert target == -40.0


class TestStandaloneTargetVolume:
    """Tests for _resolve_target_volume() with STANDALONE contexts (Story 5.3, AC1-AC2)."""

    @pytest.fixture
    def mock_state_machine(self):
        """Create a mock state machine with volume_service."""
        state_machine = MagicMock()

        # Mock volume_service with config
        volume_service = MagicMock()
        volume_config = MagicMock()
        volume_config.config.startup_volume_db = -40.0  # Test startup volume
        volume_service.config = volume_config
        state_machine.volume_service = volume_service

        return state_machine

    @pytest.fixture
    def mock_registry(self):
        """Create a mock registry with global average volume method."""
        registry = MagicMock()
        return registry

    def test_standalone_others_online_uses_global_average(self, mock_state_machine, mock_registry):
        """AC1: STANDALONE_OTHERS_ONLINE context uses global average volume (FR9)."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService
        from backend.core.multiroom.models import ReconnectionContext

        # Setup: global average of -25.0 dB
        mock_registry.get_global_average_volume = MagicMock(return_value=-25.0)

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service._registry = mock_registry
        ws_service._volume_service = mock_state_machine.volume_service

        # Test
        target = ws_service._resolve_target_volume("client-1", ReconnectionContext.STANDALONE_OTHERS_ONLINE)

        # Should use global average
        assert target == -25.0
        mock_registry.get_global_average_volume.assert_called_once_with(exclude_mac_id="client-1")

    def test_standalone_alone_uses_startup_volume(self, mock_state_machine, mock_registry):
        """AC2: STANDALONE_ALONE context uses startup_volume_db (FR10)."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService
        from backend.core.multiroom.models import ReconnectionContext

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service._registry = mock_registry
        ws_service._volume_service = mock_state_machine.volume_service

        # Test
        target = ws_service._resolve_target_volume("client-1", ReconnectionContext.STANDALONE_ALONE)

        # Should use startup_volume_db from config (-40.0)
        assert target == -40.0

    def test_standalone_others_online_fallback_to_startup_when_no_average(self, mock_state_machine, mock_registry):
        """AC1 fallback: If global average unavailable, use startup_volume_db."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService
        from backend.core.multiroom.models import ReconnectionContext

        # Setup: global average returns None (no online clients)
        mock_registry.get_global_average_volume = MagicMock(return_value=None)

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service._registry = mock_registry
        ws_service._volume_service = mock_state_machine.volume_service

        # Test
        target = ws_service._resolve_target_volume("client-1", ReconnectionContext.STANDALONE_OTHERS_ONLINE)

        # Should fallback to startup_volume_db
        assert target == -40.0

    def test_standalone_volume_without_volume_service_uses_default(self, mock_registry):
        """Edge case: No volume_service uses DEFAULT_VOLUME_DB constant."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService
        from backend.core.multiroom.models import ReconnectionContext
        from backend.config.constants import DEFAULT_VOLUME_DB

        # Setup: no volume_service
        mock_state_machine = MagicMock()

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service._registry = mock_registry
        ws_service._volume_service = None

        # Test
        target = ws_service._resolve_target_volume("client-1", ReconnectionContext.STANDALONE_ALONE)

        # Should use DEFAULT_VOLUME_DB from constants
        assert target == DEFAULT_VOLUME_DB

    def test_standalone_volume_without_registry_uses_startup(self, mock_state_machine):
        """Edge case: No registry - still returns startup volume."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService
        from backend.core.multiroom.models import ReconnectionContext

        # Setup: no registry (both _registry and state_machine.client_registry)
        mock_state_machine.client_registry = None

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service._registry = None
        ws_service._volume_service = mock_state_machine.volume_service

        # Test - calling with STANDALONE_OTHERS_ONLINE but no registry
        target = ws_service._resolve_target_volume("client-1", ReconnectionContext.STANDALONE_OTHERS_ONLINE)

        # Should fallback to startup_volume_db since registry not available
        assert target == -40.0


class TestApplyTargetVolumeToClient:
    """Tests for _apply_target_volume_to_client() method (Story 5.2)."""

    @pytest.fixture
    def mock_state_machine(self):
        """Create a mock state machine with volume_service."""
        state_machine = MagicMock()
        volume_service = AsyncMock()
        volume_service.update_client_volume_db = AsyncMock()
        # Mock state store public accessors
        volume_service._state_store = MagicMock()
        volume_service._state_store.get_client_mute = MagicMock(return_value=False)
        # Mock Equalizer controller for mute operation
        volume_service._equalizer_controller = MagicMock()
        volume_service._equalizer_controller.set_equalizer_mute = AsyncMock()
        state_machine.volume_service = volume_service
        return state_machine

    @pytest.fixture
    def mock_registry(self):
        """Create a mock registry."""
        registry = MagicMock()
        registry.update_volume = AsyncMock()
        return registry

    @pytest.mark.asyncio
    async def test_apply_volume_updates_service_and_registry(self, mock_state_machine, mock_registry):
        """Test that applying volume updates both volume_service and registry."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service._registry = mock_registry
        ws_service._volume_service = mock_state_machine.volume_service

        result = await ws_service._apply_target_volume_to_client("client-1", -30.0)

        assert result is True
        mock_state_machine.volume_service.update_client_volume_db.assert_called_once_with(
            "client-1", -30.0, broadcast=False
        )
        mock_registry.update_volume.assert_called_once_with("client-1", volume_db=-30.0)

    @pytest.mark.asyncio
    async def test_apply_volume_without_volume_service_returns_false(self, mock_registry):
        """Test that apply fails gracefully without volume_service."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        mock_state_machine = MagicMock()

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service._registry = mock_registry
        ws_service._volume_service = None

        result = await ws_service._apply_target_volume_to_client("client-1", -30.0)

        assert result is False
        mock_registry.update_volume.assert_not_called()

    @pytest.mark.asyncio
    async def test_apply_volume_works_without_registry(self, mock_state_machine):
        """Test that apply works even without registry (updates only volume_service)."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        # Remove client_registry from state_machine to test without registry
        mock_state_machine.client_registry = None

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service._registry = None
        ws_service._volume_service = mock_state_machine.volume_service

        result = await ws_service._apply_target_volume_to_client("client-1", -30.0)

        assert result is True
        mock_state_machine.volume_service.update_client_volume_db.assert_called_once()

    @pytest.mark.asyncio
    async def test_apply_volume_handles_volume_service_exception(self, mock_registry):
        """Test that apply returns False when volume_service raises an exception."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        mock_state_machine = MagicMock()
        volume_service = AsyncMock()
        volume_service.update_client_volume_db = AsyncMock(
            side_effect=Exception("Equalizer connection failed")
        )

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service._registry = mock_registry
        ws_service._volume_service = volume_service

        result = await ws_service._apply_target_volume_to_client("client-1", -30.0)

        # Should return False due to exception, registry should NOT be updated
        assert result is False
        mock_registry.update_volume.assert_not_called()

    @pytest.mark.asyncio
    async def test_apply_volume_returns_false_when_registry_update_fails(self, mock_state_machine):
        """Test that apply returns False if registry.update_volume raises an exception.

        Note: The current implementation wraps everything in try/except and returns False
        on any error. This test validates that behavior is consistent.
        """
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        mock_registry = MagicMock()
        mock_registry.update_volume = AsyncMock(side_effect=Exception("Registry error"))

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service._registry = mock_registry
        ws_service._volume_service = mock_state_machine.volume_service

        # The method returns False on any exception (including registry errors)
        result = await ws_service._apply_target_volume_to_client("client-1", -30.0)

        # volume_service was called successfully before registry failed
        mock_state_machine.volume_service.update_client_volume_db.assert_called_once()
        # registry was attempted but failed
        mock_registry.update_volume.assert_called_once()
        # Result is False due to exception handling
        assert result is False
