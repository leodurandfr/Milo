# backend/tests/test_core_multiroom.py
"""
Unit tests for the core.multiroom module.

Tests:
- Models (RegisteredClient, Zone, RegistryState, RegistryEventType)
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
    RegisteredClient,
    Zone,
    RegistryState,
    RegistryEventType,
    SpeakerType,
    SPEAKER_TYPES,
    DEFAULT_SPEAKER_TYPE,
    DEFAULT_CROSSOVER_FREQUENCY,
    DEFAULT_CROSSOVER_FREQUENCIES,
    DEFAULT_VOLUME_DB,
)
from backend.core.multiroom.registry import ClientRegistryService
from backend.core.multiroom.snapcast import (
    SnapcastService,
    get_available_clients,
    get_available_client_ids,
    normalize_client_id,
)
from backend.core.multiroom.crossover import CrossoverService, is_ip_address


# =============================================================================
# Model Tests
# =============================================================================

class TestRegisteredClient:
    """Tests for RegisteredClient model."""

    def test_create_client_with_defaults(self):
        """Test creating a client with default values."""
        client = RegisteredClient(
            dsp_id="local",
            snapcast_id="snap123",
            name="Main Speaker",
            host="milo",
            ip="127.0.0.1"
        )

        assert client.dsp_id == "local"
        assert client.snapcast_id == "snap123"
        assert client.name == "Main Speaker"
        assert client.available is True
        assert client.speaker_type == DEFAULT_SPEAKER_TYPE
        assert client.crossover_frequency == DEFAULT_CROSSOVER_FREQUENCY
        assert client.volume_db == DEFAULT_VOLUME_DB
        assert client.mute is False

    def test_client_to_dict(self):
        """Test converting client to dictionary."""
        client = RegisteredClient(
            dsp_id="192.168.1.100",
            snapcast_id="snap456",
            name="Kitchen",
            host="milo-client-kitchen",
            ip="192.168.1.100",
            speaker_type="satellite",
            crossover_frequency=120
        )

        data = client.to_dict()

        assert data["dsp_id"] == "192.168.1.100"
        assert data["name"] == "Kitchen"
        assert data["speaker_type"] == "satellite"
        assert data["crossover_frequency"] == 120

    def test_client_from_dict(self):
        """Test creating client from dictionary."""
        data = {
            "dsp_id": "milo-client-living",
            "snapcast_id": "snap789",
            "name": "Living Room",
            "host": "milo-client-living",
            "ip": "192.168.1.101",
            "speaker_type": "tower",
            "crossover_frequency": 50
        }

        client = RegisteredClient.from_dict(data)

        assert client.dsp_id == "milo-client-living"
        assert client.speaker_type == "tower"
        assert client.crossover_frequency == 50


class TestZone:
    """Tests for Zone model."""

    def test_create_zone(self):
        """Test creating a zone."""
        zone = Zone(
            id="zone1",
            name="Living Room Zone",
            client_ids=["local", "192.168.1.100"]
        )

        assert zone.id == "zone1"
        assert zone.name == "Living Room Zone"
        assert len(zone.client_ids) == 2
        assert zone.crossover_enabled is True

    def test_zone_to_dict(self):
        """Test converting zone to dictionary."""
        zone = Zone(
            id="zone2",
            name="Kitchen Zone",
            client_ids=["milo-client-kitchen"],
            crossover_frequency=100,
            crossover_enabled=False
        )

        data = zone.to_dict()

        assert data["id"] == "zone2"
        assert data["crossover_frequency"] == 100
        assert data["crossover_enabled"] is False

    def test_zone_from_dict(self):
        """Test creating zone from dictionary."""
        data = {
            "id": "zone3",
            "name": "Bedroom Zone",
            "client_ids": ["192.168.1.102"],
            "crossover_frequency": 60
        }

        zone = Zone.from_dict(data)

        assert zone.id == "zone3"
        assert zone.crossover_frequency == 60


class TestRegistryState:
    """Tests for RegistryState model."""

    def test_empty_state(self):
        """Test creating empty state."""
        state = RegistryState()

        assert len(state.clients) == 0
        assert len(state.zones) == 0

    def test_state_to_dict(self):
        """Test converting state to dictionary."""
        client = RegisteredClient(
            dsp_id="local",
            snapcast_id="snap1",
            name="Main",
            host="milo",
            ip="127.0.0.1"
        )
        zone = Zone(id="zone1", name="Zone 1", client_ids=["local"])

        state = RegistryState(
            clients={"local": client},
            zones={"zone1": zone}
        )

        data = state.to_dict()

        assert "local" in data["clients"]
        assert "zone1" in data["zones"]


class TestRegistryEventType:
    """Tests for RegistryEventType constants."""

    def test_event_types_exist(self):
        """Test that all event types are defined."""
        assert RegistryEventType.CLIENT_REGISTERED == "client_registered"
        assert RegistryEventType.AVAILABILITY_CHANGED == "availability_changed"
        assert RegistryEventType.ZONE_CREATED == "zone_created"
        assert RegistryEventType.ZONE_UPDATED == "zone_updated"


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
        bus.emit = MagicMock()
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

        client = await registry.register_client({
            "dsp_id": "local",
            "snapcast_id": "snap123",
            "name": "Main",
            "host": "milo",
            "ip": "127.0.0.1"
        })

        assert client.dsp_id == "local"
        assert registry.get_client("local") is not None

    @pytest.mark.asyncio
    async def test_register_client_requires_dsp_id(self, registry):
        """Test that dsp_id is required."""
        await registry.initialize()

        with pytest.raises(ValueError, match="dsp_id is required"):
            await registry.register_client({"name": "Test"})

    @pytest.mark.asyncio
    async def test_update_existing_client(self, registry):
        """Test updating an existing client."""
        await registry.initialize()

        await registry.register_client({
            "dsp_id": "local",
            "snapcast_id": "snap123",
            "name": "Main",
            "host": "milo",
            "ip": "127.0.0.1"
        })

        # Update the same client
        client = await registry.register_client({
            "dsp_id": "local",
            "name": "Updated Main"
        })

        assert client.name == "Updated Main"

    @pytest.mark.asyncio
    async def test_unregister_client(self, registry):
        """Test unregistering a client."""
        await registry.initialize()

        await registry.register_client({
            "dsp_id": "local",
            "snapcast_id": "snap123",
            "name": "Main",
            "host": "milo",
            "ip": "127.0.0.1"
        })

        result = await registry.unregister_client("local")
        assert result is True
        assert registry.get_client("local") is None

    @pytest.mark.asyncio
    async def test_unregister_nonexistent_client(self, registry):
        """Test unregistering a client that doesn't exist."""
        await registry.initialize()
        result = await registry.unregister_client("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_update_availability(self, registry):
        """Test updating client availability."""
        await registry.initialize()

        await registry.register_client({
            "dsp_id": "local",
            "snapcast_id": "snap123",
            "name": "Main",
            "host": "milo",
            "ip": "127.0.0.1"
        })

        await registry.update_availability("local", False)
        client = registry.get_client("local")
        assert client.available is False

    @pytest.mark.asyncio
    async def test_get_available_clients(self, registry):
        """Test getting only available clients."""
        await registry.initialize()

        await registry.register_client({
            "dsp_id": "local",
            "snapcast_id": "snap1",
            "name": "Main",
            "host": "milo",
            "ip": "127.0.0.1",
            "available": True
        })

        await registry.register_client({
            "dsp_id": "remote",
            "snapcast_id": "snap2",
            "name": "Remote",
            "host": "milo-client",
            "ip": "192.168.1.100",
            "available": False
        })

        available = registry.get_available_clients()
        assert len(available) == 1
        assert available[0].dsp_id == "local"

    @pytest.mark.asyncio
    async def test_create_zone(self, registry):
        """Test creating a zone."""
        await registry.initialize()

        await registry.register_client({
            "dsp_id": "local",
            "snapcast_id": "snap1",
            "name": "Main",
            "host": "milo",
            "ip": "127.0.0.1"
        })

        zone = await registry.create_zone("zone1", "Test Zone", ["local"])

        assert zone.id == "zone1"
        assert zone.name == "Test Zone"
        assert "local" in zone.client_ids

    @pytest.mark.asyncio
    async def test_create_zone_duplicate_id(self, registry):
        """Test creating a zone with duplicate ID."""
        await registry.initialize()

        await registry.register_client({
            "dsp_id": "local",
            "snapcast_id": "snap1",
            "name": "Main",
            "host": "milo",
            "ip": "127.0.0.1"
        })

        await registry.create_zone("zone1", "Test Zone", ["local"])

        with pytest.raises(ValueError, match="already exists"):
            await registry.create_zone("zone1", "Another Zone", ["local"])

    @pytest.mark.asyncio
    async def test_delete_zone(self, registry):
        """Test deleting a zone."""
        await registry.initialize()

        await registry.register_client({
            "dsp_id": "local",
            "snapcast_id": "snap1",
            "name": "Main",
            "host": "milo",
            "ip": "127.0.0.1"
        })

        await registry.create_zone("zone1", "Test Zone", ["local"])
        result = await registry.delete_zone("zone1")

        assert result is True
        assert registry.get_zone("zone1") is None

    @pytest.mark.asyncio
    async def test_get_zone_for_client(self, registry):
        """Test getting the zone a client belongs to."""
        await registry.initialize()

        await registry.register_client({
            "dsp_id": "local",
            "snapcast_id": "snap1",
            "name": "Main",
            "host": "milo",
            "ip": "127.0.0.1"
        })

        await registry.create_zone("zone1", "Test Zone", ["local"])

        zone = registry.get_zone_for_client("local")
        assert zone is not None
        assert zone.id == "zone1"

    @pytest.mark.asyncio
    async def test_add_client_to_zone(self, registry):
        """Test adding a client to a zone."""
        await registry.initialize()

        await registry.register_client({
            "dsp_id": "local",
            "snapcast_id": "snap1",
            "name": "Main",
            "host": "milo",
            "ip": "127.0.0.1"
        })

        await registry.register_client({
            "dsp_id": "remote",
            "snapcast_id": "snap2",
            "name": "Remote",
            "host": "milo-client",
            "ip": "192.168.1.100"
        })

        await registry.create_zone("zone1", "Test Zone", ["local"])
        result = await registry.add_client_to_zone("zone1", "remote")

        assert result is True
        zone = registry.get_zone("zone1")
        assert "remote" in zone.client_ids

    def test_compute_dsp_id_local(self):
        """Test computing dsp_id for local client."""
        dsp_id = ClientRegistryService.compute_dsp_id("milo", "127.0.0.1")
        assert dsp_id == "local"

    def test_compute_dsp_id_milo_client(self):
        """Test computing dsp_id for milo-client."""
        dsp_id = ClientRegistryService.compute_dsp_id("milo-client-kitchen", "192.168.1.100")
        assert dsp_id == "milo-client-kitchen"

    def test_compute_dsp_id_ip_fallback(self):
        """Test computing dsp_id with IP fallback."""
        dsp_id = ClientRegistryService.compute_dsp_id("unknown-host", "192.168.1.200")
        assert dsp_id == "192.168.1.200"


# =============================================================================
# SnapcastService Tests
# =============================================================================

class TestSnapcastService:
    """Tests for SnapcastService."""

    @pytest.fixture
    def mock_event_bus(self):
        """Create a mock event bus."""
        bus = MagicMock()
        bus.emit = MagicMock()
        return bus

    @pytest.fixture
    def snapcast_service(self, mock_event_bus):
        """Create a SnapcastService instance."""
        return SnapcastService(event_bus=mock_event_bus)

    def test_get_stable_dsp_id_local(self, snapcast_service):
        """Test getting stable dsp_id for local client."""
        dsp_id = snapcast_service._get_stable_dsp_id("milo", "127.0.0.1")
        assert dsp_id == "local"

    def test_get_stable_dsp_id_milo_client(self, snapcast_service):
        """Test getting stable dsp_id for milo-client."""
        dsp_id = snapcast_service._get_stable_dsp_id("milo-client-kitchen", "192.168.1.100")
        assert dsp_id == "milo-client-kitchen"

    def test_get_stable_dsp_id_ip_fallback(self, snapcast_service):
        """Test getting stable dsp_id with IP fallback."""
        dsp_id = snapcast_service._get_stable_dsp_id("unknown", "192.168.1.200")
        assert dsp_id == "192.168.1.200"

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

    def test_normalize_client_id_local(self):
        """Test normalizing 'local' hostname."""
        assert normalize_client_id("local") == "local"

    def test_normalize_client_id_milo(self):
        """Test normalizing 'milo' hostname."""
        assert normalize_client_id("milo") == "local"

    def test_normalize_client_id_ip(self):
        """Test normalizing IP address."""
        assert normalize_client_id("192.168.1.100") == "192.168.1.100"

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
    def mock_dsp_service(self):
        """Create a mock DSP service."""
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
        registry.is_client_available = MagicMock(return_value=True)
        registry.subscribe = MagicMock()
        return registry

    @pytest.fixture
    def mock_event_bus(self):
        """Create a mock event bus."""
        bus = MagicMock()
        bus.emit = MagicMock()
        return bus

    @pytest.fixture
    def crossover_service(self, mock_settings_service, mock_dsp_service, mock_event_bus):
        """Create a CrossoverService instance."""
        return CrossoverService(
            settings_service=mock_settings_service,
            dsp_service=mock_dsp_service,
            event_bus=mock_event_bus
        )

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
        assert crossover_service.has_pending_settings("local") is False

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
    async def test_set_client_crossover_local(self, crossover_service, mock_dsp_service):
        """Test setting crossover on local client."""
        result = await crossover_service._set_client_crossover("local", True, 80)
        assert result is True
        mock_dsp_service.set_crossover_filter.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_client_lowpass_local(self, crossover_service, mock_dsp_service):
        """Test setting lowpass on local client."""
        result = await crossover_service._set_client_lowpass("local", True, 80)
        assert result is True
        mock_dsp_service.set_lowpass_filter.assert_called_once()

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

        mock_dsp = AsyncMock()
        mock_dsp.set_crossover_filter = AsyncMock(return_value=True)
        mock_dsp.set_lowpass_filter = AsyncMock(return_value=True)

        mock_bus = MagicMock()
        mock_bus.emit = MagicMock()

        registry = ClientRegistryService(settings_service=mock_settings, event_bus=mock_bus)
        crossover = CrossoverService(settings_service=mock_settings, dsp_service=mock_dsp, event_bus=mock_bus)

        # Initialize
        await registry.initialize()
        await crossover.initialize()

        # Set registry on crossover
        crossover.set_registry(registry)

        # Register a client
        await registry.register_client({
            "dsp_id": "local",
            "snapcast_id": "snap1",
            "name": "Main",
            "host": "milo",
            "ip": "127.0.0.1"
        })

        # Verify client is in registry
        client = registry.get_client("local")
        assert client is not None
        assert client.speaker_type == DEFAULT_SPEAKER_TYPE
