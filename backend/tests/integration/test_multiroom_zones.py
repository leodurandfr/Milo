# backend/tests/integration/test_multiroom_zones.py
"""
Integration tests for multiroom zone management.

These tests validate the contracts for zone creation, client management,
and Equalizer settings synchronization for the multiroom/Equalizer architecture.
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock

from backend.core.multiroom.client_registry import ClientRegistryService
from backend.core.volume.state import VolumeStateStore
from backend.core.multiroom.models import Client, Zone, EqualizerSettings, RegistryEventType
from backend.core.models.volume_state import VolumeState

from .conftest import WebSocketEventCollector


# ==============================================================================
# FIXTURES
# ==============================================================================


@pytest.fixture
def mock_settings_service():
    """Mock settings service for registry persistence."""
    service = Mock()
    service.invalidate_cache = Mock()

    # In-memory storage for new structure
    storage = {
        "multiroom.clients": {},
        "multiroom.zones": {},
        "multiroom.standalone_equalizer": {}
    }

    async def mock_get_setting(key):
        return storage.get(key)

    async def mock_set_setting(key, value):
        storage[key] = value
        return True

    service.get_setting = AsyncMock(side_effect=mock_get_setting)
    service.set_setting = AsyncMock(side_effect=mock_set_setting)

    return service


@pytest.fixture
def mock_event_bus():
    """Mock event bus for events."""
    bus = Mock()
    bus.emit = AsyncMock()
    return bus


@pytest.fixture
def mock_state_machine(websocket_collector: WebSocketEventCollector):
    """Mock state machine with WebSocket event collection."""
    sm = Mock()

    async def mock_broadcast(category, event_type, data):
        await websocket_collector.handle_event({
            "category": category,
            "type": event_type,
            "source": "registry",
            "data": data,
            "timestamp": asyncio.get_running_loop().time()
        })

    sm.broadcast_event = AsyncMock(side_effect=mock_broadcast)
    return sm


@pytest.fixture
async def registry_service(mock_settings_service, mock_state_machine, mock_event_bus):
    """ClientRegistryService with mocked dependencies."""
    service = ClientRegistryService(
        settings_service=mock_settings_service,
        event_bus=mock_event_bus
    )
    service.set_state_machine(mock_state_machine)
    await service.initialize()
    return service


@pytest.fixture
async def registry_with_clients(registry_service):
    """Registry with pre-registered clients for zone tests."""
    # Register local client
    await registry_service.register_client(
        mac_id="local",
        name="Local",
        ip="127.0.0.1"
    )
    await registry_service.set_client_online("local", True)

    # Register bedroom client
    await registry_service.register_client(
        mac_id="bedroom",
        name="Bedroom",
        ip="192.168.1.101"
    )
    await registry_service.set_client_online("bedroom", True)

    # Register kitchen client
    await registry_service.register_client(
        mac_id="kitchen",
        name="Kitchen",
        ip="192.168.1.102"
    )
    await registry_service.set_client_online("kitchen", True)

    return registry_service


@pytest.fixture
async def volume_state_store_with_registry(mock_settings_service, registry_with_clients):
    """VolumeStateStore integrated with registry for zone volume tests."""
    store = VolumeStateStore(mock_settings_service)
    store.set_registry(registry_with_clients)
    await store.initialize()

    # Register clients in volume store
    await store.register_client("local", volume_db=-30.0, available=True)
    await store.register_client("bedroom", volume_db=-25.0, available=True)
    await store.register_client("kitchen", volume_db=-35.0, available=True)

    return store


# ==============================================================================
# AC1: Test Zone Creation
# ==============================================================================


class TestZoneCreation:
    """Tests for AC1: Zone creation with multiple clients."""

    @pytest.mark.asyncio
    async def test_create_zone_success(
        self,
        registry_with_clients: ClientRegistryService,
        websocket_collector: WebSocketEventCollector
    ):
        """
        Test zone creation with valid clients.

        Validates:
        - create_zone returns Zone object
        - Zone contains correct clients
        - Zone is retrievable
        - Minimum 2 clients required
        """
        websocket_collector.clear()

        zone = await registry_with_clients.create_zone(
            zone_id="living_room",
            name="Living Room",
            client_ids=["local", "bedroom"]
        )

        assert zone is not None
        assert zone.id == "living_room"
        assert zone.name == "Living Room"
        assert set(zone.client_ids) == {"local", "bedroom"}
        assert zone.equalizer_settings is not None

    @pytest.mark.asyncio
    async def test_create_zone_requires_minimum_2_clients(
        self,
        registry_with_clients: ClientRegistryService
    ):
        """
        Test zone creation requires at least 2 clients.

        Validates:
        - ValueError raised for single client zone
        """
        with pytest.raises(ValueError, match="at least 2 clients"):
            await registry_with_clients.create_zone(
                zone_id="living_room",
                name="Living Room",
                client_ids=["local"]
            )

    @pytest.mark.asyncio
    async def test_create_zone_with_invalid_clients_fails(
        self,
        registry_with_clients: ClientRegistryService
    ):
        """
        Test zone creation fails with non-existent clients.

        Validates:
        - ValueError raised for unknown client_id
        """
        with pytest.raises(ValueError, match="not found"):
            await registry_with_clients.create_zone(
                zone_id="invalid_zone",
                name="Invalid Zone",
                client_ids=["local", "nonexistent_client"]
            )

    @pytest.mark.asyncio
    async def test_get_zone_returns_correct_data(
        self,
        registry_with_clients: ClientRegistryService
    ):
        """
        Test get_zone returns the created zone.

        Validates:
        - Zone is retrievable by ID
        - Zone data matches creation parameters
        """
        await registry_with_clients.create_zone(
            zone_id="living_room",
            name="Living Room",
            client_ids=["local", "bedroom"]
        )

        zone = registry_with_clients.get_zone("living_room")

        assert zone is not None
        assert zone.id == "living_room"
        assert zone.name == "Living Room"
        assert "local" in zone.client_ids
        assert "bedroom" in zone.client_ids

    @pytest.mark.asyncio
    async def test_create_duplicate_zone_fails(
        self,
        registry_with_clients: ClientRegistryService
    ):
        """
        Test creating a zone with existing ID fails.

        Validates:
        - ValueError raised for duplicate zone_id
        """
        await registry_with_clients.create_zone(
            zone_id="living_room",
            name="Living Room",
            client_ids=["local", "bedroom"]
        )

        with pytest.raises(ValueError, match="already exists"):
            await registry_with_clients.create_zone(
                zone_id="living_room",
                name="Another Room",
                client_ids=["local", "kitchen"]
            )

    @pytest.mark.asyncio
    async def test_clients_have_zone_id_after_zone_creation(
        self,
        registry_with_clients: ClientRegistryService
    ):
        """
        Test that clients have zone_id set after zone creation.
        """
        await registry_with_clients.create_zone(
            zone_id="living_room",
            name="Living Room",
            client_ids=["local", "bedroom"]
        )

        local_client = registry_with_clients.get_client("local")
        bedroom_client = registry_with_clients.get_client("bedroom")

        assert local_client.zone_id == "living_room"
        assert bedroom_client.zone_id == "living_room"


# ==============================================================================
# AC2: Test Zone Volume Synchronization
# ==============================================================================


class TestZoneVolumeSynchronization:
    """Tests for AC2: Zone volume synchronization."""

    @pytest.mark.asyncio
    async def test_zone_average_volume_calculated_correctly(
        self,
        volume_state_store_with_registry: VolumeStateStore,
        registry_with_clients: ClientRegistryService
    ):
        """
        Test zone average volume calculation.

        Validates:
        - Zone average is computed from available clients
        - Formula: sum(volumes) / count(available_clients)
        """
        store = volume_state_store_with_registry

        # Create zone with clients at different volumes
        # local: -30 dB, bedroom: -25 dB
        await registry_with_clients.create_zone(
            zone_id="living_room",
            name="Living Room",
            client_ids=["local", "bedroom"]
        )

        # Compute zone average
        # Expected: (-30 + -25) / 2 = -27.5 dB
        average = store.compute_zone_average("living_room")

        assert average == -27.5

    @pytest.mark.asyncio
    async def test_unavailable_clients_excluded_from_average(
        self,
        volume_state_store_with_registry: VolumeStateStore,
        registry_with_clients: ClientRegistryService
    ):
        """
        Test unavailable clients are excluded from zone average.

        Validates:
        - Only available clients contribute to average
        """
        store = volume_state_store_with_registry

        await registry_with_clients.create_zone(
            zone_id="living_room",
            name="Living Room",
            client_ids=["local", "bedroom"]
        )

        # Mark bedroom as unavailable
        await store.set_client_availability("bedroom", False)

        # Only local should contribute now
        average = store.compute_zone_average("living_room")

        assert average == -30.0  # Only local's volume

    @pytest.mark.asyncio
    async def test_zone_volume_delta_calculates_updates(
        self,
        volume_state_store_with_registry: VolumeStateStore,
        registry_with_clients: ClientRegistryService
    ):
        """
        Test apply_zone_delta calculates updates for all clients.

        Validates:
        - Delta is applied to each client's volume
        - Returns dictionary of client_id -> new_volume
        """
        store = volume_state_store_with_registry

        # Set limits to allow higher volumes for this test
        store.update_user_limits(-80.0, 0.0)

        await registry_with_clients.create_zone(
            zone_id="living_room",
            name="Living Room",
            client_ids=["local", "bedroom"]
        )

        # Apply +5 dB delta
        updates = await store.apply_zone_delta("living_room", 5.0)

        # Both clients should have updates
        assert len(updates) == 2
        assert updates["local"] == -25.0  # -30 + 5
        assert updates["bedroom"] == -20.0  # -25 + 5

    @pytest.mark.asyncio
    async def test_zone_volume_delta_respects_limits(
        self,
        volume_state_store_with_registry: VolumeStateStore,
        registry_with_clients: ClientRegistryService
    ):
        """
        Test apply_zone_delta respects volume limits.

        Validates:
        - Updates are clamped to min/max limits
        """
        store = volume_state_store_with_registry

        # Set custom limits
        store.update_user_limits(-80.0, -21.0)

        await registry_with_clients.create_zone(
            zone_id="living_room",
            name="Living Room",
            client_ids=["local", "bedroom"]
        )

        # Try to apply delta that would exceed max
        # bedroom at -25, +10 = -15 should clamp to -21
        updates = await store.apply_zone_delta("living_room", 10.0)

        assert updates["bedroom"] == -21.0  # Clamped to max


# ==============================================================================
# AC3: Test Volume Offsets
# ==============================================================================


class TestVolumeOffsets:
    """Tests for AC3: Per-client volume offsets."""

    @pytest.mark.asyncio
    async def test_client_offset_relative_to_zone_average(
        self,
        volume_state_store_with_registry: VolumeStateStore,
        registry_with_clients: ClientRegistryService
    ):
        """
        Test offset_db is calculated relative to zone average.

        Validates:
        - offset_db = client_volume - zone_average
        """
        store = volume_state_store_with_registry

        await registry_with_clients.create_zone(
            zone_id="living_room",
            name="Living Room",
            client_ids=["local", "bedroom"]
        )

        state = await store.get_complete_state()

        # Zone average: (-30 + -25) / 2 = -27.5
        # local offset: -30 - (-27.5) = -2.5
        # bedroom offset: -25 - (-27.5) = +2.5
        local_offset = state.clients["local"].offset_db
        bedroom_offset = state.clients["bedroom"].offset_db

        assert local_offset == pytest.approx(-2.5, abs=0.1)
        assert bedroom_offset == pytest.approx(2.5, abs=0.1)

    @pytest.mark.asyncio
    async def test_clients_with_different_volumes_have_offsets(
        self,
        volume_state_store_with_registry: VolumeStateStore,
        registry_with_clients: ClientRegistryService
    ):
        """
        Test clients with different volumes have different offsets.

        Validates:
        - Offset reflects difference from zone average
        """
        store = volume_state_store_with_registry

        # local: -30, bedroom: -25, kitchen: -35
        await registry_with_clients.create_zone(
            zone_id="all_rooms",
            name="All Rooms",
            client_ids=["local", "bedroom", "kitchen"]
        )

        state = await store.get_complete_state()

        # Zone average: (-30 + -25 + -35) / 3 = -30
        # local offset: -30 - (-30) = 0
        # bedroom offset: -25 - (-30) = +5
        # kitchen offset: -35 - (-30) = -5
        assert state.clients["local"].offset_db == pytest.approx(0.0, abs=0.1)
        assert state.clients["bedroom"].offset_db == pytest.approx(5.0, abs=0.1)
        assert state.clients["kitchen"].offset_db == pytest.approx(-5.0, abs=0.1)

    @pytest.mark.asyncio
    async def test_client_not_in_zone_has_zero_offset(
        self,
        volume_state_store_with_registry: VolumeStateStore,
        registry_with_clients: ClientRegistryService
    ):
        """
        Test clients not in any zone have zero offset.

        Validates:
        - Clients outside zones have offset_db = 0
        """
        store = volume_state_store_with_registry

        # Create zone with only local and bedroom
        await registry_with_clients.create_zone(
            zone_id="living_room",
            name="Living Room",
            client_ids=["local", "bedroom"]
        )

        state = await store.get_complete_state()

        # Kitchen is not in any zone
        assert state.clients["kitchen"].offset_db == 0.0


# ==============================================================================
# AC4: Test Client Add/Remove from Zones
# ==============================================================================


class TestZoneClientManagement:
    """Tests for AC4: Adding and removing clients from zones."""

    @pytest.mark.asyncio
    async def test_add_client_to_zone_success(
        self,
        registry_with_clients: ClientRegistryService
    ):
        """
        Test adding a client to an existing zone.

        Validates:
        - add_client_to_zone returns True on success
        - Client appears in zone's client_ids
        """
        await registry_with_clients.create_zone(
            zone_id="living_room",
            name="Living Room",
            client_ids=["local", "bedroom"]
        )

        result = await registry_with_clients.add_client_to_zone("living_room", "kitchen")

        assert result is True
        zone = registry_with_clients.get_zone("living_room")
        assert "kitchen" in zone.client_ids

        kitchen_client = registry_with_clients.get_client("kitchen")
        assert kitchen_client.zone_id == "living_room"

    @pytest.mark.asyncio
    async def test_remove_client_from_zone_success(
        self,
        registry_with_clients: ClientRegistryService
    ):
        """
        Test removing a client from a zone.

        Validates:
        - remove_client_from_zone returns True on success
        - Client no longer in zone's client_ids
        - Client's zone_id is cleared
        """
        await registry_with_clients.create_zone(
            zone_id="living_room",
            name="Living Room",
            client_ids=["local", "bedroom", "kitchen"]
        )

        result = await registry_with_clients.remove_client_from_zone("living_room", "kitchen")

        assert result is True
        zone = registry_with_clients.get_zone("living_room")
        assert "kitchen" not in zone.client_ids
        assert "local" in zone.client_ids
        assert "bedroom" in zone.client_ids

        kitchen_client = registry_with_clients.get_client("kitchen")
        assert kitchen_client.zone_id is None

    @pytest.mark.asyncio
    async def test_delete_zone_success(
        self,
        registry_with_clients: ClientRegistryService
    ):
        """
        Test deleting a zone.

        Validates:
        - delete_zone returns True on success
        - Zone no longer retrievable
        - Clients have zone_id cleared
        """
        await registry_with_clients.create_zone(
            zone_id="living_room",
            name="Living Room",
            client_ids=["local", "bedroom"]
        )

        result = await registry_with_clients.delete_zone("living_room")

        assert result is True
        assert registry_with_clients.get_zone("living_room") is None

        local_client = registry_with_clients.get_client("local")
        bedroom_client = registry_with_clients.get_client("bedroom")
        assert local_client.zone_id is None
        assert bedroom_client.zone_id is None

    @pytest.mark.asyncio
    async def test_add_invalid_client_to_zone_fails(
        self,
        registry_with_clients: ClientRegistryService
    ):
        """
        Test adding non-existent client to zone fails.

        Validates:
        - add_client_to_zone returns False for unknown client
        """
        await registry_with_clients.create_zone(
            zone_id="living_room",
            name="Living Room",
            client_ids=["local", "bedroom"]
        )

        result = await registry_with_clients.add_client_to_zone(
            "living_room", "nonexistent"
        )

        assert result is False


# ==============================================================================
# Test Equalizer Settings
# ==============================================================================


class TestZoneEqualizerSettings:
    """Tests for zone Equalizer settings management."""

    @pytest.mark.asyncio
    async def test_zone_has_default_equalizer_settings(
        self,
        registry_with_clients: ClientRegistryService
    ):
        """
        Test zone is created with default Equalizer settings.

        Default Equalizer settings include:
        - enabled=True (Equalizer active)
        - 10-band parametric EQ at standard frequencies with 0 dB gain
        - compressor disabled
        - loudness disabled
        """
        from backend.core.multiroom.models import EqFilter, CompressorSettings, LoudnessSettings

        zone = await registry_with_clients.create_zone(
            zone_id="living_room",
            name="Living Room",
            client_ids=["local", "bedroom"]
        )

        assert zone.equalizer_settings is not None
        assert zone.equalizer_settings.enabled is True
        # Default creates 10-band EQ with flat gains
        assert len(zone.equalizer_settings.filters) == 10
        assert all(isinstance(f, EqFilter) for f in zone.equalizer_settings.filters)
        assert all(f.gain == 0.0 for f in zone.equalizer_settings.filters)
        # Compressor and loudness should be disabled by default
        assert isinstance(zone.equalizer_settings.compressor, CompressorSettings)
        assert zone.equalizer_settings.compressor.enabled is False
        assert isinstance(zone.equalizer_settings.loudness, LoudnessSettings)
        assert zone.equalizer_settings.loudness.enabled is False

    @pytest.mark.asyncio
    async def test_zone_created_with_custom_equalizer_settings(
        self,
        registry_with_clients: ClientRegistryService
    ):
        """
        Test zone can be created with custom Equalizer settings.
        """
        from backend.core.multiroom.models import EqFilter, CompressorSettings

        custom_equalizer = EqualizerSettings(
            enabled=True,
            filters=[EqFilter(id="eq_band_00", frequency=1000, gain=3.0)],
            compressor=CompressorSettings(enabled=True, threshold=-20, ratio=4.0)
        )

        zone = await registry_with_clients.create_zone(
            zone_id="living_room",
            name="Living Room",
            client_ids=["local", "bedroom"],
            equalizer_settings=custom_equalizer
        )

        assert len(zone.equalizer_settings.filters) == 1
        assert zone.equalizer_settings.compressor.enabled is True

    @pytest.mark.asyncio
    async def test_standalone_equalizer_cleared_when_joining_zone(
        self,
        registry_with_clients: ClientRegistryService
    ):
        """
        Test that standalone Equalizer settings are cleared when client joins a zone.
        """
        from backend.core.multiroom.models import EqFilter

        # Set standalone Equalizer for local with typed EqFilter
        eq = EqualizerSettings(filters=[EqFilter(id="eq_band_00", frequency=1000)])
        await registry_with_clients.set_standalone_equalizer("local", eq)

        # Verify standalone Equalizer exists
        assert registry_with_clients.get_standalone_equalizer("local") is not None

        # Create zone
        await registry_with_clients.create_zone(
            zone_id="living_room",
            name="Living Room",
            client_ids=["local", "bedroom"]
        )

        # Standalone Equalizer should be cleared
        assert registry_with_clients.get_standalone_equalizer("local") is None


# ==============================================================================
# Test Registry Persistence
# ==============================================================================


class TestRegistryPersistence:
    """Tests for zone persistence via settings."""

    @pytest.mark.asyncio
    async def test_zone_persisted_to_settings(
        self,
        registry_with_clients: ClientRegistryService,
        mock_settings_service
    ):
        """
        Test zone creation persists to settings.

        Validates:
        - set_setting called with zone data
        """
        await registry_with_clients.create_zone(
            zone_id="living_room",
            name="Living Room",
            client_ids=["local", "bedroom"]
        )

        # Verify set_setting was called
        calls = mock_settings_service.set_setting.call_args_list
        zone_calls = [c for c in calls if "multiroom.zones" in str(c)]
        assert len(zone_calls) >= 1


# ==============================================================================
# Test Registry Queries
# ==============================================================================


class TestClientRegistryQueries:
    """Tests for registry query methods."""

    @pytest.mark.asyncio
    async def test_get_zone_clients_returns_client_objects(
        self,
        registry_with_clients: ClientRegistryService
    ):
        """
        Test get_zone_clients returns client objects.
        """
        await registry_with_clients.create_zone(
            zone_id="living_room",
            name="Living Room",
            client_ids=["local", "bedroom"]
        )

        clients = registry_with_clients.get_zone_clients("living_room")

        assert len(clients) == 2
        mac_ids = [c.mac_id for c in clients]
        assert "local" in mac_ids
        assert "bedroom" in mac_ids

    @pytest.mark.asyncio
    async def test_get_online_zone_clients_filters_offline(
        self,
        registry_with_clients: ClientRegistryService
    ):
        """
        Test get_online_zone_clients filters out offline clients.
        """
        await registry_with_clients.create_zone(
            zone_id="living_room",
            name="Living Room",
            client_ids=["local", "bedroom"]
        )

        # Mark bedroom as offline
        await registry_with_clients.set_client_online("bedroom", False)

        clients = registry_with_clients.get_online_zone_clients("living_room")

        assert len(clients) == 1
        assert clients[0].mac_id == "local"

    @pytest.mark.asyncio
    async def test_get_zone_for_client_returns_correct_zone(
        self,
        registry_with_clients: ClientRegistryService
    ):
        """
        Test get_zone_for_client returns the zone containing the client.
        """
        await registry_with_clients.create_zone(
            zone_id="living_room",
            name="Living Room",
            client_ids=["local", "bedroom"]
        )

        zone = registry_with_clients.get_zone_for_client("bedroom")

        assert zone is not None
        assert zone.id == "living_room"

    @pytest.mark.asyncio
    async def test_get_zone_for_client_returns_none_when_not_in_zone(
        self,
        registry_with_clients: ClientRegistryService
    ):
        """
        Test get_zone_for_client returns None for client not in any zone.
        """
        await registry_with_clients.create_zone(
            zone_id="living_room",
            name="Living Room",
            client_ids=["local", "bedroom"]
        )

        zone = registry_with_clients.get_zone_for_client("kitchen")

        assert zone is None
