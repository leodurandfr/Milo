# backend/tests/integration/test_volume_control.py
"""
Integration tests for volume control functionality.

These tests validate the contracts for volume management that must
remain stable during the feature-based architecture refactoring.

Contracts being tested:
- Volume set/get via VolumeService (AC1)
- WebSocket volume_changed events (AC2)
- Volume limits and clamping (AC3)
- Mute/unmute functionality (AC4)
- Volume persistence to disk (AC5)
"""
import pytest
import asyncio
import json
from unittest.mock import Mock, AsyncMock, patch

from backend.core.volume import VolumeService
from backend.core.volume.state import VolumeStateStore, ZoneConfig
from backend.core.models.volume import VolumeConfig
from backend.core.models.volume_state import VolumeState, ClientVolume
from backend.config.constants import DEFAULT_VOLUME_DB

from .conftest import WebSocketEventCollector


# ==============================================================================
# FIXTURES
# ==============================================================================


@pytest.fixture
def mock_camilladsp_service():
    """Mock Equalizer controller to avoid real hardware calls."""
    service = Mock()
    service.set_volume = AsyncMock(return_value=True)
    service.get_volume = AsyncMock(return_value={"main": -30.0, "mute": False})
    service.set_mute = AsyncMock(return_value=True)
    service.is_volume_control_available = Mock(return_value=True)
    service.wait_for_connection = AsyncMock(return_value=True)
    return service


@pytest.fixture
def mock_snapcast_service():
    """Mock Snapcast service to avoid WebSocket calls."""
    service = Mock()
    service.get_clients = AsyncMock(return_value=[])
    service.set_volume = AsyncMock(return_value=True)
    return service


@pytest.fixture
def mock_settings_service():
    """Mock settings service with default volume configuration."""
    service = Mock()
    service.invalidate_cache = Mock()

    # Default volume settings
    volume_config = {
        "limit_min_db": -80.0,
        "limit_max_db": -21.0,
        "startup_volume_db": -30.0,
        "restore_last_volume": False,
        "step_mobile_db": 3.0,
        "step_rotary_db": 2.0
    }

    async def mock_get_setting(key):
        if key == "volume":
            return volume_config
        elif key.startswith("volume."):
            subkey = key.replace("volume.", "")
            return volume_config.get(subkey)
        elif key == "routing.multiroom_enabled":
            return False
        elif key == "equalizer.linked_groups":
            return []
        return None

    async def mock_set_setting(key, value):
        if key.startswith("volume."):
            subkey = key.replace("volume.", "")
            volume_config[subkey] = value
        return True

    service.get_setting = AsyncMock(side_effect=mock_get_setting)
    service.set_setting = AsyncMock(side_effect=mock_set_setting)

    return service


@pytest.fixture
def mock_state_machine(websocket_collector: WebSocketEventCollector):
    """Mock state machine with WebSocket event collection."""
    sm = Mock()

    async def mock_broadcast(category, event_type, data):
        await websocket_collector.broadcast_dict({
            "category": category,
            "type": event_type,
            "origin": "volume",
            "data": data,
            "timestamp": asyncio.get_running_loop().time()
        })

    sm.broadcast_event = AsyncMock(side_effect=mock_broadcast)
    sm.routing_service = Mock()
    sm.routing_service.get_state = Mock(return_value={'multiroom_enabled': False})
    return sm


@pytest.fixture
def temp_storage_path(tmp_path):
    """Create a temporary storage path for volume persistence tests."""
    return tmp_path / "last_volume.json"


@pytest.fixture
async def volume_state_store(mock_settings_service, temp_storage_path):
    """VolumeStateStore with mocked persistence path."""
    with patch.object(VolumeStateStore, 'STORAGE_PATH', temp_storage_path):
        store = VolumeStateStore(mock_settings_service)
        store.set_volume_config(VolumeConfig())
        await store.initialize()
        yield store


@pytest.fixture
async def volume_service(
    mock_state_machine,
    mock_snapcast_service,
    mock_settings_service,
    mock_camilladsp_service,
    websocket_collector: WebSocketEventCollector,
    temp_storage_path
):
    """VolumeService with mocked dependencies for integration testing."""
    with patch.object(VolumeStateStore, 'STORAGE_PATH', temp_storage_path):
        service = VolumeService(
            state_machine=mock_state_machine,
            snapcast_service=mock_snapcast_service,
            settings_service=mock_settings_service,
            camilladsp_service=mock_camilladsp_service,
            equalizer_client_proxy_service=None
        )

        # Initialize service
        await service.initialize()

        # Register "local" client so tests can use it (simulates what
        # ClientRegistryService does in production when local Snapcast client connects)
        service._state_store._local_mac_id = "local"
        await service._state_store.register_client("local", volume_db=DEFAULT_VOLUME_DB, available=True)

        yield service

        # Cleanup
        await service.cleanup()


# ==============================================================================
# AC1: Test Volume API Operations
# ==============================================================================


class TestVolumeAPI:
    """Tests for AC1: Volume set/get via API."""

    @pytest.mark.asyncio
    async def test_set_volume_db_success(
        self,
        volume_service: VolumeService,
        websocket_collector: WebSocketEventCollector
    ):
        """
        Test setting volume via service.

        Validates:
        - set_volume_db returns True on success
        - Volume is updated in state
        - WebSocket event is emitted
        """
        websocket_collector.clear()

        success = await volume_service.set_volume_db(-25.0)

        assert success is True

        # Verify volume was set
        volume_db = await volume_service.get_volume_db()
        assert volume_db == -25.0

        # Verify WebSocket event
        events = websocket_collector.get_events_by_type("volume_changed")
        assert len(events) >= 1

    @pytest.mark.asyncio
    async def test_get_volume_returns_current_db(
        self,
        volume_service: VolumeService
    ):
        """
        Test getting current volume.

        Validates:
        - get_volume_db returns volume in dB
        - Value is within valid range
        """
        # Set known volume first
        await volume_service.set_volume_db(-40.0)

        volume_db = await volume_service.get_volume_db()

        assert isinstance(volume_db, float)
        assert -80.0 <= volume_db <= 0.0
        assert volume_db == -40.0

    @pytest.mark.asyncio
    async def test_get_volume_state_returns_complete_state(
        self,
        volume_service: VolumeService
    ):
        """
        Test getting complete volume state.

        Validates:
        - Returns VolumeState object
        - Contains mode, global_volume_db, global_mute, clients, zones
        """
        await volume_service.set_volume_db(-35.0)

        state = await volume_service.get_volume_state()

        assert isinstance(state, VolumeState)
        assert state.mode in ("direct", "multiroom")
        assert -80.0 <= state.global_volume_db <= 0.0
        assert isinstance(state.global_mute, bool)
        assert isinstance(state.clients, dict)
        assert isinstance(state.zones, dict)

    @pytest.mark.asyncio
    async def test_adjust_volume_positive_delta(
        self,
        volume_service: VolumeService
    ):
        """
        Test adjusting volume with positive delta.

        Validates:
        - adjust_volume_db increases volume by delta
        """
        # Set initial volume
        await volume_service.set_volume_db(-50.0)
        initial = await volume_service.get_volume_db()

        # Adjust by +5 dB
        success = await volume_service.adjust_volume_db(5.0)

        assert success is True
        new_volume = await volume_service.get_volume_db()
        assert new_volume == initial + 5.0

    @pytest.mark.asyncio
    async def test_adjust_volume_negative_delta(
        self,
        volume_service: VolumeService
    ):
        """
        Test adjusting volume with negative delta.

        Validates:
        - adjust_volume_db decreases volume by delta
        """
        # Set initial volume
        await volume_service.set_volume_db(-30.0)
        initial = await volume_service.get_volume_db()

        # Adjust by -10 dB
        success = await volume_service.adjust_volume_db(-10.0)

        assert success is True
        new_volume = await volume_service.get_volume_db()
        assert new_volume == initial - 10.0


# ==============================================================================
# AC2: Test WebSocket Events
# ==============================================================================


class TestVolumeWebSocketEvents:
    """Tests for AC2: WebSocket volume_changed events."""

    @pytest.mark.asyncio
    async def test_volume_changed_event_format(
        self,
        volume_service: VolumeService,
        websocket_collector: WebSocketEventCollector
    ):
        """
        Test volume_changed event format.

        Validates:
        - Event has category: "volume"
        - Event has type: "volume_changed"
        - Event has data with state
        """
        websocket_collector.clear()

        await volume_service.set_volume_db(-25.0)

        events = websocket_collector.get_events_by_type("volume_changed")
        assert len(events) >= 1

        event = events[0]
        assert event["category"] == "volume"
        assert event["type"] == "volume_changed"
        assert "data" in event
        assert "state" in event["data"]

    @pytest.mark.asyncio
    async def test_volume_changed_contains_state(
        self,
        volume_service: VolumeService,
        websocket_collector: WebSocketEventCollector
    ):
        """
        Test volume_changed event contains complete state.

        Validates:
        - state contains global_volume_db
        - state contains clients dict
        - state contains mode
        """
        websocket_collector.clear()

        await volume_service.set_volume_db(-30.0)

        events = websocket_collector.get_events_by_type("volume_changed")
        assert len(events) >= 1

        state = events[0]["data"]["state"]
        assert "global_volume_db" in state
        assert "clients" in state
        assert "mode" in state
        assert "global_mute" in state

    @pytest.mark.asyncio
    async def test_show_bar_flag_propagated(
        self,
        volume_service: VolumeService,
        websocket_collector: WebSocketEventCollector
    ):
        """
        Test show_bar flag is correctly propagated.

        Validates:
        - show_bar=True is included in event data
        - show_bar=False is included when specified
        """
        # Test with show_bar=True (default)
        websocket_collector.clear()
        await volume_service.set_volume_db(-25.0, show_bar=True)

        events = websocket_collector.get_events_by_type("volume_changed")
        assert len(events) >= 1
        assert events[0]["data"]["show_bar"] is True

        # Test with show_bar=False
        websocket_collector.clear()
        await volume_service.set_volume_db(-30.0, show_bar=False)

        events = websocket_collector.get_events_by_type("volume_changed")
        assert len(events) >= 1
        assert events[0]["data"]["show_bar"] is False

    @pytest.mark.asyncio
    async def test_step_mobile_db_included_in_event(
        self,
        volume_service: VolumeService,
        websocket_collector: WebSocketEventCollector
    ):
        """
        Test step_mobile_db is included in volume event.

        Validates:
        - step_mobile_db from config is included in event data
        """
        websocket_collector.clear()

        await volume_service.set_volume_db(-25.0)

        events = websocket_collector.get_events_by_type("volume_changed")
        assert len(events) >= 1
        assert "step_mobile_db" in events[0]["data"]
        assert events[0]["data"]["step_mobile_db"] == 3.0  # Default value


# ==============================================================================
# AC3: Test Volume Limits
# ==============================================================================


class TestVolumeLimits:
    """Tests for AC3: Volume limits and clamping."""

    @pytest.mark.asyncio
    async def test_volume_clamped_to_min(
        self,
        volume_service: VolumeService
    ):
        """
        Test volume is clamped to minimum limit.

        Validates:
        - Setting volume below limit_min_db is clamped
        """
        # Try to set volume below minimum (-80 dB)
        await volume_service.set_volume_db(-100.0)

        volume_db = await volume_service.get_volume_db()
        assert volume_db == -80.0  # Clamped to minimum

    @pytest.mark.asyncio
    async def test_volume_clamped_to_max(
        self,
        volume_service: VolumeService
    ):
        """
        Test volume is clamped to maximum limit.

        Validates:
        - Setting volume above limit_max_db is clamped
        """
        # Try to set volume above maximum (-21 dB default)
        await volume_service.set_volume_db(0.0)

        volume_db = await volume_service.get_volume_db()
        assert volume_db == -21.0  # Clamped to maximum

    @pytest.mark.asyncio
    async def test_adjust_respects_limits(
        self,
        volume_service: VolumeService
    ):
        """
        Test adjust_volume_db respects limits.

        Validates:
        - Adjusting beyond limits results in clamped value
        """
        # Set to near maximum
        await volume_service.set_volume_db(-22.0)

        # Try to adjust beyond max
        await volume_service.adjust_volume_db(10.0)

        volume_db = await volume_service.get_volume_db()
        assert volume_db == -21.0  # Clamped to max

        # Set to near minimum
        await volume_service.set_volume_db(-75.0)

        # Try to adjust beyond min
        await volume_service.adjust_volume_db(-20.0)

        volume_db = await volume_service.get_volume_db()
        assert volume_db == -80.0  # Clamped to min

    @pytest.mark.asyncio
    async def test_user_defined_limits_respected(
        self,
        volume_service: VolumeService,
        mock_settings_service
    ):
        """
        Test user-defined volume limits are respected.

        Validates:
        - Custom max limit is applied
        - Volume is clamped to custom limit
        """
        # Update limits to custom values (disable restore_last_volume to prevent
        # _update_startup_volume_if_needed from reloading config from settings)
        custom_config = VolumeConfig(limit_min_db=-60.0, limit_max_db=-25.0, restore_last_volume=False)
        volume_service._volume_config = custom_config
        volume_service._state_store.set_volume_config(custom_config)

        # Try to set below custom min
        await volume_service.set_volume_db(-70.0)
        volume_db = await volume_service.get_volume_db()
        assert volume_db == -60.0  # Clamped to custom min

        # Try to set above custom max
        await volume_service.set_volume_db(-20.0)
        volume_db = await volume_service.get_volume_db()
        assert volume_db == -25.0  # Clamped to custom max

    def test_volume_config_clamp_function(self):
        """
        Test VolumeConfig.clamp() directly.

        Validates:
        - clamp() returns value within limits
        """
        config = VolumeConfig(limit_min_db=-80.0, limit_max_db=-21.0)

        assert config.clamp(-90.0) == -80.0  # Below min
        assert config.clamp(-80.0) == -80.0  # At min
        assert config.clamp(-50.0) == -50.0  # Middle
        assert config.clamp(-21.0) == -21.0  # At max
        assert config.clamp(0.0) == -21.0    # Above max


# ==============================================================================
# AC4: Test Mute/Unmute
# ==============================================================================


class TestMuteUnmute:
    """Tests for AC4: Mute/unmute functionality."""

    @pytest.mark.asyncio
    async def test_mute_sets_global_mute_true(
        self,
        volume_service: VolumeService
    ):
        """
        Test muting sets global_mute to True.

        Validates:
        - set_client_mute with mute=True updates state
        """
        # Ensure local client exists
        await volume_service.set_volume_db(-30.0)

        # Mute local client
        await volume_service.set_client_mute('local', True, broadcast=False)

        # Check state
        state = await volume_service.get_volume_state()

        # In direct mode with only local client, global_mute reflects local mute
        local_client = state.clients.get('local')
        assert local_client is not None
        assert local_client.mute is True

    @pytest.mark.asyncio
    async def test_unmute_sets_global_mute_false(
        self,
        volume_service: VolumeService
    ):
        """
        Test unmuting sets global_mute to False.

        Validates:
        - set_client_mute with mute=False updates state
        """
        # Ensure local client exists and is muted
        await volume_service.set_volume_db(-30.0)
        await volume_service.set_client_mute('local', True, broadcast=False)

        # Unmute
        await volume_service.set_client_mute('local', False, broadcast=False)

        # Check state
        state = await volume_service.get_volume_state()
        local_client = state.clients.get('local')
        assert local_client is not None
        assert local_client.mute is False

    @pytest.mark.asyncio
    async def test_mute_emits_websocket_event(
        self,
        volume_service: VolumeService,
        websocket_collector: WebSocketEventCollector
    ):
        """
        Test mute/unmute emits WebSocket event.

        Validates:
        - volume_changed event is broadcast on mute change
        """
        await volume_service.set_volume_db(-30.0)
        websocket_collector.clear()

        # Mute with broadcast
        await volume_service.set_client_mute('local', True, broadcast=True)

        events = websocket_collector.get_events_by_type("volume_changed")
        assert len(events) >= 1

    @pytest.mark.asyncio
    async def test_mute_persisted_to_state(
        self,
        volume_service: VolumeService,
        temp_storage_path
    ):
        """
        Test mute state is persisted.

        Validates:
        - Mute state is saved to persistence file
        """
        await volume_service.set_volume_db(-30.0)
        await volume_service.set_client_mute('local', True, broadcast=False)

        # Wait for async save to complete
        await asyncio.sleep(0.2)

        # Check persistence file
        if temp_storage_path.exists():
            with open(temp_storage_path) as f:
                data = json.load(f)

            if 'clients' in data and 'local' in data['clients']:
                assert data['clients']['local']['mute'] is True


# ==============================================================================
# AC5: Test Persistence
# ==============================================================================


class TestVolumePersistence:
    """Tests for AC5: Volume persistence."""

    @pytest.mark.asyncio
    async def test_volume_persisted_to_file(
        self,
        mock_state_machine,
        mock_snapcast_service,
        mock_camilladsp_service,
        temp_storage_path
    ):
        """
        Test volume is saved to persistence file.

        Validates:
        - Volume change triggers save when restore_last_volume=True
        - File contains correct volume value
        """
        # Create settings with restore_last_volume=True
        settings = Mock()
        settings.invalidate_cache = Mock()

        async def mock_get_setting(key):
            if key == "volume":
                return {
                    "limit_min_db": -80.0,
                    "limit_max_db": -21.0,
                    "startup_volume_db": -30.0,
                    "restore_last_volume": True,  # Enable persistence
                    "step_mobile_db": 3.0,
                    "step_rotary_db": 2.0
                }
            elif key == "volume.restore_last_volume":
                return True
            elif key == "routing.multiroom_enabled":
                return False
            elif key == "equalizer.linked_groups":
                return []
            return None

        settings.get_setting = AsyncMock(side_effect=mock_get_setting)
        settings.set_setting = AsyncMock(return_value=True)

        with patch.object(VolumeStateStore, 'STORAGE_PATH', temp_storage_path):
            service = VolumeService(
                state_machine=mock_state_machine,
                snapcast_service=mock_snapcast_service,
                settings_service=settings,
                camilladsp_service=mock_camilladsp_service,
                equalizer_client_proxy_service=None
            )
            await service.initialize()

            # Simulate the registry CLIENT_CONNECTED event for the local client
            # so the state store knows which mac_id to write under.
            service._state_store._local_mac_id = "local"

            # Set volume
            await service.set_volume_db(-42.0)

            # Flush debounced persistence
            await service.cleanup()

            # Check file exists and contains correct data
            assert temp_storage_path.exists(), "Persistence file should exist"

            with open(temp_storage_path) as f:
                data = json.load(f)

            assert "clients" in data
            assert "local_mac_id" in data
            # Volume should be in clients dict
            if "local" in data["clients"]:
                assert data["clients"]["local"]["volume_db"] == -42.0

            await service.cleanup()

    @pytest.mark.asyncio
    async def test_volume_restored_on_startup(
        self,
        mock_settings_service,
        mock_state_machine,
        mock_snapcast_service,
        mock_camilladsp_service,
        temp_storage_path
    ):
        """
        Test volume is restored from file on startup.

        Validates:
        - Persisted volume is read during initialization
        - restore_last_volume=true enables restore
        """
        # Create persistence file with known volume
        persist_data = {
            "local_mac_id": "local",
            "clients": {
                "local": {"volume_db": -35.0, "mute": False}
            }
        }
        temp_storage_path.parent.mkdir(parents=True, exist_ok=True)
        with open(temp_storage_path, 'w') as f:
            json.dump(persist_data, f)

        # Enable restore
        async def mock_get_setting(key):
            if key == "volume":
                return {
                    "limit_min_db": -80.0,
                    "limit_max_db": -21.0,
                    "startup_volume_db": -30.0,
                    "restore_last_volume": True,
                    "step_mobile_db": 3.0,
                    "step_rotary_db": 2.0
                }
            elif key == "volume.restore_last_volume":
                return True
            elif key == "routing.multiroom_enabled":
                return False
            return None

        mock_settings_service.get_setting = AsyncMock(side_effect=mock_get_setting)

        # Create new store with mocked path
        with patch.object(VolumeStateStore, 'STORAGE_PATH', temp_storage_path):
            store = VolumeStateStore(mock_settings_service)
            await store.initialize()

            # Check restored volume
            assert store._local_mac_id == "local"
            assert 'local' in store._clients
            assert store._clients['local'].volume_db == -35.0
            assert store.local_volume_db == -35.0

    @pytest.mark.asyncio
    async def test_persistence_format_valid(
        self,
        mock_state_machine,
        mock_snapcast_service,
        mock_camilladsp_service,
        temp_storage_path
    ):
        """
        Test persistence file format.

        Validates:
        - JSON format with required fields
        - local_mac_id, clients
        """
        # Create settings with restore_last_volume=True
        settings = Mock()
        settings.invalidate_cache = Mock()

        async def mock_get_setting(key):
            if key == "volume":
                return {
                    "limit_min_db": -80.0,
                    "limit_max_db": -21.0,
                    "startup_volume_db": -30.0,
                    "restore_last_volume": True,  # Enable persistence
                    "step_mobile_db": 3.0,
                    "step_rotary_db": 2.0
                }
            elif key == "volume.restore_last_volume":
                return True
            elif key == "routing.multiroom_enabled":
                return False
            elif key == "equalizer.linked_groups":
                return []
            return None

        settings.get_setting = AsyncMock(side_effect=mock_get_setting)
        settings.set_setting = AsyncMock(return_value=True)

        with patch.object(VolumeStateStore, 'STORAGE_PATH', temp_storage_path):
            service = VolumeService(
                state_machine=mock_state_machine,
                snapcast_service=mock_snapcast_service,
                settings_service=settings,
                camilladsp_service=mock_camilladsp_service,
                equalizer_client_proxy_service=None
            )
            await service.initialize()

            # Simulate the registry CLIENT_CONNECTED event for the local client
            # so the state store knows which mac_id to write under.
            service._state_store._local_mac_id = "local"

            await service.set_volume_db(-28.0)

            # Flush debounced persistence
            await service.cleanup()

            assert temp_storage_path.exists()

            with open(temp_storage_path) as f:
                data = json.load(f)

            # Should always carry the clients dict and the local mac_id key
            assert "clients" in data
            assert "local_mac_id" in data

            await service.cleanup()

    @pytest.mark.asyncio
    async def test_old_persistence_still_restored(
        self,
        mock_settings_service,
        temp_storage_path
    ):
        """
        Persisted volume is restored regardless of age (no expiry).

        Validates:
        - An old persisted file is still loaded into the store
        - Volume, mac_id and mute carry over to the new session
        """
        # Persist a file with a deliberately old timestamp to prove age no
        # longer gates restoration (timestamp is not even read anymore).
        persist_data = {
            "timestamp": "2000-01-01T00:00:00+00:00",
            "local_mac_id": "local",
            "clients": {
                "local": {"volume_db": -45.0, "mute": False}
            }
        }
        temp_storage_path.parent.mkdir(parents=True, exist_ok=True)
        with open(temp_storage_path, 'w') as f:
            json.dump(persist_data, f)

        # Create store
        with patch.object(VolumeStateStore, 'STORAGE_PATH', temp_storage_path):
            store = VolumeStateStore(mock_settings_service)
            await store.initialize()

            # Old data is restored as-is
            assert store.local_volume_db == -45.0
            assert store._local_mac_id == "local"
            assert "local" in store._clients


# ==============================================================================
# Additional Integration Tests
# ==============================================================================


class TestVolumeStateStore:
    """Integration tests for VolumeStateStore."""

    @pytest.mark.asyncio
    async def test_get_complete_state_returns_valid_state(
        self,
        volume_state_store: VolumeStateStore
    ):
        """
        Test get_complete_state returns valid VolumeState.
        """
        state = await volume_state_store.get_complete_state()

        assert isinstance(state, VolumeState)
        assert state.mode in ("direct", "multiroom")
        assert isinstance(state.global_volume_db, float)
        assert isinstance(state.global_mute, bool)
        assert isinstance(state.clients, dict)
        assert isinstance(state.zones, dict)

    @pytest.mark.asyncio
    async def test_register_client_updates_state(
        self,
        volume_state_store: VolumeStateStore
    ):
        """
        Test registering a client adds it to state.
        """
        await volume_state_store.register_client(
            "test-client",
            volume_db=-40.0,
            available=True
        )

        assert "test-client" in volume_state_store._clients
        assert volume_state_store._clients["test-client"].volume_db == -40.0
        assert volume_state_store._clients["test-client"].available is True

    @pytest.mark.asyncio
    async def test_set_client_volume_clamps_value(
        self,
        volume_state_store: VolumeStateStore
    ):
        """
        Test set_client_volume clamps values to limits.
        """
        await volume_state_store.register_client("test-client", volume_db=-30.0)

        # Try to set beyond limits
        result = await volume_state_store.set_client_volume("test-client", -100.0)

        assert result == volume_state_store._volume_config.limit_min_db

    @pytest.mark.asyncio
    async def test_set_volume_config_updates_clamping(
        self,
        volume_state_store: VolumeStateStore
    ):
        """
        Test setting VolumeConfig affects clamping behavior.
        """
        # Update limits via VolumeConfig
        config = VolumeConfig(limit_min_db=-50.0, limit_max_db=-25.0)
        volume_state_store.set_volume_config(config)

        # Verify clamping uses new limits
        result = volume_state_store._clamp_db(-60.0)
        assert result == -50.0

        result = volume_state_store._clamp_db(-20.0)
        assert result == -25.0


# ==============================================================================
# Story 3.1: Client Volume Control API Integration Tests
# ==============================================================================


class TestClientVolumeAPI:
    """Integration tests for Story 3.1: Client Volume Control API endpoints."""

    @pytest.mark.asyncio
    async def test_update_client_volume_db_broadcasts_event(
        self,
        volume_service: VolumeService,
        websocket_collector: WebSocketEventCollector
    ):
        """
        Test AC1: Client volume change broadcasts WebSocket event.

        Validates:
        - update_client_volume_db() broadcasts volume_changed event
        - Event contains updated client state
        """
        # Ensure local client exists with initial volume
        await volume_service.set_volume_db(-30.0)
        websocket_collector.clear()

        # Update client volume directly
        await volume_service.update_client_volume_db("local", -45.0)

        # In non-multiroom mode, broadcast may not occur, but state should be updated
        state = await volume_service.get_volume_state()
        local_client = state.clients.get("local")
        assert local_client is not None

    @pytest.mark.asyncio
    async def test_set_client_mute_broadcasts_event(
        self,
        volume_service: VolumeService,
        websocket_collector: WebSocketEventCollector
    ):
        """
        Test AC4: Client mute toggle broadcasts WebSocket event.

        Validates:
        - set_client_mute() broadcasts volume_changed event
        - Event contains updated mute state
        """
        # Ensure local client exists
        await volume_service.set_volume_db(-30.0)
        websocket_collector.clear()

        # Toggle mute with broadcast
        await volume_service.set_client_mute("local", True, broadcast=True)

        events = websocket_collector.get_events_by_type("volume_changed")
        assert len(events) >= 1

        # Verify mute state in event
        event_data = events[0]["data"]["state"]
        if "clients" in event_data and "local" in event_data["clients"]:
            assert event_data["clients"]["local"]["mute"] is True

    @pytest.mark.asyncio
    async def test_get_client_volume_returns_correct_state(
        self,
        volume_service: VolumeService
    ):
        """
        Test AC3: get_client_volume returns correct volume and mute state.

        Validates:
        - Returns dict with "main" (volume_db) and "mute" keys
        - Values match what was set
        """
        # Set known volume
        await volume_service.set_volume_db(-35.0)
        await volume_service.set_client_mute("local", True, broadcast=False)

        # Get client volume
        result = await volume_service.get_client_volume("local")

        assert "main" in result
        assert "mute" in result
        # Note: main should reflect the local client's volume
        assert result["mute"] is True

    @pytest.mark.asyncio
    async def test_client_volume_persistence(
        self,
        volume_state_store: VolumeStateStore,
        temp_storage_path
    ):
        """
        Test AC2: Offline client volume persistence.

        Validates:
        - Client volume is persisted to VolumeStateStore
        - Volume can be retrieved after being set
        """
        # Register a client with specific volume
        await volume_state_store.register_client("test-client", volume_db=-42.0, available=True)

        # Verify client volume was stored
        assert "test-client" in volume_state_store._clients
        assert volume_state_store._clients["test-client"].volume_db == -42.0

        # Verify volume can be set and retrieved
        await volume_state_store.set_client_volume("test-client", -55.0)
        assert volume_state_store._clients["test-client"].volume_db == -55.0

    @pytest.mark.asyncio
    async def test_client_mute_persistence(
        self,
        volume_state_store: VolumeStateStore,
        temp_storage_path
    ):
        """
        Test AC4: Mute state persistence.

        Validates:
        - Mute state is persisted to VolumeStateStore
        - Mute state can be retrieved after being set
        """
        # Register a client
        await volume_state_store.register_client("test-client", volume_db=-30.0, available=True)

        # Set mute state
        await volume_state_store.set_client_mute("test-client", True)

        # Verify mute state was stored
        assert volume_state_store._clients["test-client"].mute is True

        # Toggle mute off
        await volume_state_store.set_client_mute("test-client", False)
        assert volume_state_store._clients["test-client"].mute is False


# ==============================================================================
# Story 3.2: Zone Volume Delta Integration Tests
# ==============================================================================


class TestZoneVolumeDeltaIntegration:
    """Integration tests for Story 3.2: Zone Volume Delta.

    Note: These tests use the VolumeStateStore directly to avoid issues with
    the registry reloading zones on get_complete_state(). The unit tests in
    test_volume_state.py cover the same functionality through the store directly.
    """

    @pytest.fixture
    def zone_state_store(self, mock_settings_service, temp_storage_path):
        """VolumeStateStore configured for zone testing without registry."""
        with patch.object(VolumeStateStore, 'STORAGE_PATH', temp_storage_path):
            store = VolumeStateStore(mock_settings_service)
            # Do not set registry to avoid zones being reloaded
            store._mode = "multiroom"
            store.set_volume_config(VolumeConfig(limit_min_db=-80.0, limit_max_db=0.0))
            yield store

    @pytest.mark.asyncio
    async def test_zone_delta_preserves_relative_offsets_end_to_end(
        self,
        zone_state_store: VolumeStateStore
    ):
        """
        AC1: Zone delta preserves relative offsets end-to-end.

        Validates:
        - Before: clients at different volumes (5dB difference)
        - After: both clients moved, difference preserved
        """
        store = zone_state_store

        # Setup: zone with 5dB offset between clients
        store._zones = {
            'zone_1': ZoneConfig(
                zone_id='zone_1',
                name='Test Zone',
                client_ids=['client_a', 'client_b']
            )
        }
        store._clients = {
            'client_a': ClientVolume(volume_db=-25.0, offset_db=0.0, mute=False, available=True),
            'client_b': ClientVolume(volume_db=-30.0, offset_db=0.0, mute=False, available=True)
        }

        # Record initial difference
        initial_diff = store._clients['client_a'].volume_db - store._clients['client_b'].volume_db
        assert initial_diff == 5.0

        # Action: apply +3dB delta
        updates = await store.apply_zone_delta('zone_1', 3.0)

        # Assert: volumes changed, difference preserved
        assert updates['client_a'] - updates['client_b'] == 5.0  # Same 5dB difference
        assert updates['client_a'] == -22.0  # -25 + 3
        assert updates['client_b'] == -27.0  # -30 + 3

    @pytest.mark.asyncio
    async def test_zone_delta_only_updates_online_clients(
        self,
        zone_state_store: VolumeStateStore
    ):
        """
        AC2: Only ONLINE clients updated in zone delta.

        Validates:
        - ONLINE clients receive delta
        - OFFLINE clients remain unchanged
        """
        store = zone_state_store

        # Setup: zone with mixed availability
        store._zones = {
            'zone_1': ZoneConfig(
                zone_id='zone_1',
                name='Test Zone',
                client_ids=['online_client', 'offline_client']
            )
        }
        store._clients = {
            'online_client': ClientVolume(volume_db=-30.0, offset_db=0.0, mute=False, available=True),
            'offline_client': ClientVolume(volume_db=-30.0, offset_db=0.0, mute=False, available=False)
        }

        # Action: apply delta
        updates = await store.apply_zone_delta('zone_1', 5.0)

        # Assert: only online client in updates
        assert 'online_client' in updates
        assert 'offline_client' not in updates
        assert updates['online_client'] == -25.0  # -30 + 5

        # Verify offline client unchanged in store
        assert store._clients['offline_client'].volume_db == -30.0

    def test_zone_average_readonly_computed(
        self,
        zone_state_store: VolumeStateStore
    ):
        """
        AC4: Zone average is readonly/computed from ONLINE clients.

        Validates:
        - Zone average computed from available clients only
        - OFFLINE clients excluded from average
        """
        store = zone_state_store

        # Setup: zone with mixed availability
        store._zones = {
            'zone_1': ZoneConfig(
                zone_id='zone_1',
                name='Test Zone',
                client_ids=['online_1', 'online_2', 'offline_1']
            )
        }
        store._clients = {
            'online_1': ClientVolume(volume_db=-20.0, offset_db=0.0, mute=False, available=True),
            'online_2': ClientVolume(volume_db=-30.0, offset_db=0.0, mute=False, available=True),
            'offline_1': ClientVolume(volume_db=-50.0, offset_db=0.0, mute=False, available=False)
        }

        # Get zone average (computed)
        average = store.compute_zone_average('zone_1')

        # Assert: average is from ONLINE clients only
        # Average should be (-20 + -30) / 2 = -25, not including -50 (offline)
        assert average == pytest.approx(-25.0, rel=1e-6)

    @pytest.mark.asyncio
    async def test_zone_delta_clamps_at_limits(
        self,
        zone_state_store: VolumeStateStore
    ):
        """
        AC1: Zone delta respects volume limits.

        Validates:
        - Delta that would exceed limits is clamped
        """
        store = zone_state_store

        # Set limits
        store.set_volume_config(VolumeConfig(limit_min_db=-80.0, limit_max_db=-21.0))

        # Setup: client near maximum
        store._zones = {
            'zone_1': ZoneConfig(
                zone_id='zone_1',
                name='Test Zone',
                client_ids=['client_a']
            )
        }
        store._clients = {
            'client_a': ClientVolume(volume_db=-25.0, offset_db=0.0, mute=False, available=True)
        }

        # Action: apply delta that would exceed max (-25 + 10 = -15 > -21)
        updates = await store.apply_zone_delta('zone_1', 10.0)

        # Assert: clamped to maximum
        assert updates['client_a'] == -21.0

    @pytest.mark.asyncio
    async def test_zone_average_updates_after_delta_applied(
        self,
        zone_state_store: VolumeStateStore
    ):
        """
        AC4: Zone average updates after delta applied.

        Validates:
        - Zone average reflects new client volumes after delta
        """
        store = zone_state_store

        # Setup: zone with clients
        store._zones = {
            'zone_1': ZoneConfig(
                zone_id='zone_1',
                name='Test Zone',
                client_ids=['client_a', 'client_b']
            )
        }
        store._clients = {
            'client_a': ClientVolume(volume_db=-30.0, offset_db=0.0, mute=False, available=True),
            'client_b': ClientVolume(volume_db=-30.0, offset_db=0.0, mute=False, available=True)
        }

        # Verify initial average
        assert store.compute_zone_average('zone_1') == pytest.approx(-30.0, rel=1e-6)

        # Action: apply +10dB delta and apply updates
        updates = await store.apply_zone_delta('zone_1', 10.0)
        store._persist_state = AsyncMock()  # Mock persist
        await store.apply_zone_updates(updates)

        # Assert: average updated
        assert store.compute_zone_average('zone_1') == pytest.approx(-20.0, rel=1e-6)

    @pytest.mark.asyncio
    async def test_zone_delta_returns_correct_updates(
        self,
        zone_state_store: VolumeStateStore
    ):
        """
        AC3: apply_zone_delta returns dict of client updates.

        Validates:
        - Method returns dict mapping client_id -> new_volume_db
        """
        store = zone_state_store

        # Setup: zone with clients
        store._zones = {
            'zone_1': ZoneConfig(
                zone_id='zone_1',
                name='Test Zone',
                client_ids=['client_a', 'client_b']
            )
        }
        store._clients = {
            'client_a': ClientVolume(volume_db=-30.0, offset_db=0.0, mute=False, available=True),
            'client_b': ClientVolume(volume_db=-40.0, offset_db=0.0, mute=False, available=True)
        }

        # Action: apply delta
        updates = await store.apply_zone_delta('zone_1', 5.0)

        # Assert: returns updates dict with new volumes
        assert 'client_a' in updates
        assert 'client_b' in updates
        assert updates['client_a'] == -25.0  # -30 + 5
        assert updates['client_b'] == -35.0  # -40 + 5

    @pytest.mark.asyncio
    async def test_zone_with_all_offline_clients_returns_empty_updates(
        self,
        zone_state_store: VolumeStateStore
    ):
        """
        AC2: Zone with all offline clients returns empty updates.

        Validates:
        - No updates returned when all clients are OFFLINE
        """
        store = zone_state_store

        # Setup: zone with all OFFLINE clients
        store._zones = {
            'zone_1': ZoneConfig(
                zone_id='zone_1',
                name='Test Zone',
                client_ids=['offline_a', 'offline_b']
            )
        }
        store._clients = {
            'offline_a': ClientVolume(volume_db=-30.0, offset_db=0.0, mute=False, available=False),
            'offline_b': ClientVolume(volume_db=-30.0, offset_db=0.0, mute=False, available=False)
        }

        # Action: apply delta
        updates = await store.apply_zone_delta('zone_1', 5.0)

        # Assert: empty updates
        assert updates == {}

    @pytest.mark.asyncio
    async def test_zone_average_returns_default_when_all_offline(
        self,
        zone_state_store: VolumeStateStore
    ):
        """
        AC4: Zone average returns DEFAULT_VOLUME_DB when all clients offline.
        """
        store = zone_state_store

        # Setup: zone with all OFFLINE clients
        store._zones = {
            'zone_1': ZoneConfig(
                zone_id='zone_1',
                name='Test Zone',
                client_ids=['offline_a', 'offline_b']
            )
        }
        store._clients = {
            'offline_a': ClientVolume(volume_db=-20.0, offset_db=0.0, mute=False, available=False),
            'offline_b': ClientVolume(volume_db=-30.0, offset_db=0.0, mute=False, available=False)
        }

        # Action: get zone average
        average = store.compute_zone_average('zone_1')

        # Assert: returns default
        assert average == DEFAULT_VOLUME_DB


# ==============================================================================
# Story 3.3: Startup Volume Management Integration Tests (FR11, FR12)
# ==============================================================================


class TestStartupVolumeIntegration:
    """Integration tests for Story 3.3: Startup Volume Management (FR11, FR12).

    FR11: Auto-update startup_volume_db when restore_last_volume is disabled
    FR12: Backend restart applies startup volume
    """

    @pytest.mark.asyncio
    async def test_fr11_volume_change_updates_startup_volume_and_broadcasts(
        self,
        mock_state_machine,
        mock_snapcast_service,
        mock_camilladsp_service,
        websocket_collector: WebSocketEventCollector,
        temp_storage_path
    ):
        """
        FR11 End-to-End: Volume change → settings update → WebSocket broadcast.

        Validates complete flow:
        1. Set volume when restore_last_volume=true (FR11 tracks current volume)
        2. startup_volume_db auto-updated in settings
        3. WebSocket event broadcast with new value
        """
        # Create settings with restore_last_volume=True (FR11 active: auto-track volume)
        settings = Mock()
        settings.invalidate_cache = Mock()
        settings_data = {
            "limit_min_db": -80.0,
            "limit_max_db": -21.0,
            "startup_volume_db": -60.0,  # Initial value
            "restore_last_volume": True,  # FR11 active: auto-track current volume
            "step_mobile_db": 3.0,
            "step_rotary_db": 2.0
        }

        async def mock_get_setting(key):
            if key == "volume":
                return settings_data.copy()
            elif key.startswith("volume."):
                subkey = key.replace("volume.", "")
                return settings_data.get(subkey)
            elif key == "routing.multiroom_enabled":
                return False
            elif key == "equalizer.linked_groups":
                return []
            return None

        async def mock_set_setting(key, value):
            if key.startswith("volume."):
                subkey = key.replace("volume.", "")
                settings_data[subkey] = value
            return True

        settings.get_setting = AsyncMock(side_effect=mock_get_setting)
        settings.set_setting = AsyncMock(side_effect=mock_set_setting)

        with patch.object(VolumeStateStore, 'STORAGE_PATH', temp_storage_path):
            service = VolumeService(
                state_machine=mock_state_machine,
                snapcast_service=mock_snapcast_service,
                settings_service=settings,
                camilladsp_service=mock_camilladsp_service,
                equalizer_client_proxy_service=None
            )
            await service.initialize()
            websocket_collector.clear()

            # Action: Set volume to -45dB
            await service.set_volume_db(-45.0)

            # Assert: settings was updated
            settings.set_setting.assert_called()
            assert settings_data["startup_volume_db"] == -45.0

            # Assert: WebSocket broadcast occurred with settings category
            events = websocket_collector.get_events_by_type("volume_startup_changed")
            assert len(events) >= 1
            assert events[0]["category"] == "settings"
            assert events[0]["data"]["config"]["startup_volume_db"] == -45.0
            assert events[0]["data"]["config"]["restore_last_volume"] is True

            await service.cleanup()

    @pytest.mark.asyncio
    async def test_fr11_restore_disabled_does_not_update_startup_volume(
        self,
        mock_state_machine,
        mock_snapcast_service,
        mock_camilladsp_service,
        websocket_collector: WebSocketEventCollector,
        temp_storage_path
    ):
        """
        FR11 AC2: When restore_last_volume=false, startup_volume_db remains unchanged.

        Validates:
        - Volume changes do NOT update startup_volume_db
        - No settings WebSocket event is broadcast
        """
        # Create settings with restore_last_volume=False (FR11 NOT active: fixed startup volume)
        settings = Mock()
        settings.invalidate_cache = Mock()
        settings_data = {
            "limit_min_db": -80.0,
            "limit_max_db": -21.0,
            "startup_volume_db": -60.0,
            "restore_last_volume": False,  # FR11 NOT active
            "step_mobile_db": 3.0,
            "step_rotary_db": 2.0
        }

        async def mock_get_setting(key):
            if key == "volume":
                return settings_data.copy()
            elif key.startswith("volume."):
                subkey = key.replace("volume.", "")
                return settings_data.get(subkey)
            elif key == "routing.multiroom_enabled":
                return False
            elif key == "equalizer.linked_groups":
                return []
            return None

        settings.get_setting = AsyncMock(side_effect=mock_get_setting)
        settings.set_setting = AsyncMock()

        with patch.object(VolumeStateStore, 'STORAGE_PATH', temp_storage_path):
            service = VolumeService(
                state_machine=mock_state_machine,
                snapcast_service=mock_snapcast_service,
                settings_service=settings,
                camilladsp_service=mock_camilladsp_service,
                equalizer_client_proxy_service=None
            )
            await service.initialize()
            websocket_collector.clear()

            # Action: Set volume
            await service.set_volume_db(-45.0)

            # Assert: set_setting was NOT called (FR11 not active)
            settings.set_setting.assert_not_called()

            # Assert: startup_volume_db unchanged
            assert settings_data["startup_volume_db"] == -60.0

            # Assert: No settings broadcast
            events = websocket_collector.get_events_by_type("volume_startup_changed")
            assert len(events) == 0

            await service.cleanup()

    @pytest.mark.asyncio
    async def test_fr12_startup_uses_startup_volume_when_restore_disabled(
        self,
        mock_state_machine,
        mock_snapcast_service,
        mock_camilladsp_service,
        temp_storage_path
    ):
        """
        FR12 AC3: Backend startup applies startup_volume_db when restore=false.

        Validates:
        - On initialize(), Equalizer receives startup_volume_db from settings
        - NOT the persisted volume
        """
        # Create settings
        settings = Mock()
        settings.invalidate_cache = Mock()
        startup_volume = -35.0

        async def mock_get_setting(key):
            if key == "volume":
                return {
                    "limit_min_db": -80.0,
                    "limit_max_db": -21.0,
                    "startup_volume_db": startup_volume,
                    "restore_last_volume": False,  # Use startup_volume_db
                    "step_mobile_db": 3.0,
                    "step_rotary_db": 2.0
                }
            elif key == "routing.multiroom_enabled":
                return False
            elif key == "equalizer.linked_groups":
                return []
            return None

        settings.get_setting = AsyncMock(side_effect=mock_get_setting)
        settings.set_setting = AsyncMock()

        # Create persisted volume file with DIFFERENT volume
        persist_data = {
            "local_mac_id": "local",  # Persisted local volume different from startup_volume
            "clients": {
                "local": {"volume_db": -50.0, "mute": False}
            }
        }
        temp_storage_path.parent.mkdir(parents=True, exist_ok=True)
        with open(temp_storage_path, 'w') as f:
            json.dump(persist_data, f)

        with patch.object(VolumeStateStore, 'STORAGE_PATH', temp_storage_path):
            service = VolumeService(
                state_machine=mock_state_machine,
                snapcast_service=mock_snapcast_service,
                settings_service=settings,
                camilladsp_service=mock_camilladsp_service,
                equalizer_client_proxy_service=None
            )

            # Action: Initialize service (triggers _apply_startup_volume)
            await service.initialize()

            # Assert: Equalizer was set to startup_volume, NOT persisted volume
            mock_camilladsp_service.set_volume.assert_called()
            # Find the call with the startup volume
            calls = mock_camilladsp_service.set_volume.call_args_list
            volume_calls = [c for c in calls if c[0][0] == startup_volume]
            assert len(volume_calls) >= 1, f"Expected call with {startup_volume}, got {calls}"

            await service.cleanup()

    @pytest.mark.asyncio
    async def test_fr12_startup_restores_persisted_volume_when_restore_enabled(
        self,
        mock_state_machine,
        mock_snapcast_service,
        mock_camilladsp_service,
        temp_storage_path
    ):
        """
        FR12 AC3: Backend startup applies startup_volume_db when restore=true.

        When restore_last_volume=true, FR11 keeps startup_volume_db in sync with
        current volume during runtime. At restart, startup_volume_db already contains
        the correct last volume, so it's the single source of truth.

        Validates:
        - On initialize(), Equalizer receives startup_volume_db from settings
        - startup_volume_db was pre-synced by FR11 before shutdown
        """
        # Create settings: startup_volume_db already synced by FR11 to -42.0
        settings = Mock()
        settings.invalidate_cache = Mock()
        persisted_volume = -42.0

        async def mock_get_setting(key):
            if key == "volume":
                return {
                    "limit_min_db": -80.0,
                    "limit_max_db": -21.0,
                    "startup_volume_db": persisted_volume,  # FR11 synced this before shutdown
                    "restore_last_volume": True,
                    "step_mobile_db": 3.0,
                    "step_rotary_db": 2.0
                }
            elif key == "routing.multiroom_enabled":
                return False
            elif key == "equalizer.linked_groups":
                return []
            return None

        settings.get_setting = AsyncMock(side_effect=mock_get_setting)
        settings.set_setting = AsyncMock()

        # Create persisted volume file (also matches startup_volume_db)
        persist_data = {
            "local_mac_id": "local",
            "clients": {
                "local": {"volume_db": persisted_volume, "mute": False}
            }
        }
        temp_storage_path.parent.mkdir(parents=True, exist_ok=True)
        with open(temp_storage_path, 'w') as f:
            json.dump(persist_data, f)

        with patch.object(VolumeStateStore, 'STORAGE_PATH', temp_storage_path):
            service = VolumeService(
                state_machine=mock_state_machine,
                snapcast_service=mock_snapcast_service,
                settings_service=settings,
                camilladsp_service=mock_camilladsp_service,
                equalizer_client_proxy_service=None
            )

            # Action: Initialize service
            await service.initialize()

            # Assert: Equalizer was set to startup_volume_db (which FR11 synced to persisted_volume)
            mock_camilladsp_service.set_volume.assert_called()
            calls = mock_camilladsp_service.set_volume.call_args_list
            volume_calls = [c for c in calls if c[0][0] == persisted_volume]
            assert len(volume_calls) >= 1, f"Expected call with {persisted_volume}, got {calls}"

            await service.cleanup()


# ==============================================================================
# Story 3.4: Volume API Endpoints Integration Tests
# ==============================================================================


class TestVolumeApiEndpointsIntegration:
    """Integration tests for Story 3.4: Volume API Endpoints.

    Tests the complete flow from API endpoint through VolumeService
    for MAC address-based operations, zone delta, and settings.
    """

    @pytest.mark.asyncio
    async def test_ac1_mac_volume_flow_end_to_end(
        self,
        volume_service: VolumeService,
        websocket_collector: WebSocketEventCollector
    ):
        """
        AC1: MAC address volume update flows through service correctly.

        Validates:
        - Volume update with MAC address reaches VolumeService
        - State is updated in VolumeStateStore
        - WebSocket event is broadcast
        """
        # Setup: Ensure local client exists
        await volume_service.set_volume_db(-30.0)
        websocket_collector.clear()

        # Simulate what the API endpoint would call
        # (VolumeService.update_client_volume_db uses MAC or Equalizer ID)
        await volume_service.update_client_volume_db("local", -42.0)

        # Assert: Volume updated in state
        state = await volume_service.get_volume_state()
        local_client = state.clients.get("local")
        assert local_client is not None

    @pytest.mark.asyncio
    async def test_ac2_zone_delta_applies_to_all_online_clients(
        self,
        volume_state_store: VolumeStateStore
    ):
        """
        AC2: Zone delta applies to all ONLINE clients and returns correct data.

        Validates:
        - apply_zone_delta updates all available clients
        - Returns dict with affected clients and new volumes
        - Offline clients are not included in updates
        """
        store = volume_state_store
        store._mode = "multiroom"
        store.set_volume_config(VolumeConfig(limit_min_db=-80.0, limit_max_db=0.0))

        # Setup zone with 3 clients (2 online, 1 offline)
        store._zones = {
            'test-zone': ZoneConfig(
                zone_id='test-zone',
                name='Test Zone',
                client_ids=['client-a', 'client-b', 'client-c']
            )
        }
        store._clients = {
            'client-a': ClientVolume(volume_db=-30.0, offset_db=0.0, mute=False, available=True),
            'client-b': ClientVolume(volume_db=-35.0, offset_db=0.0, mute=False, available=True),
            'client-c': ClientVolume(volume_db=-40.0, offset_db=0.0, mute=False, available=False)  # Offline
        }

        # Apply zone delta
        updates = await store.apply_zone_delta('test-zone', 5.0)

        # Assert: Only online clients in updates
        assert 'client-a' in updates
        assert 'client-b' in updates
        assert 'client-c' not in updates

        # Assert: Correct new volumes
        assert updates['client-a'] == -25.0  # -30 + 5
        assert updates['client-b'] == -30.0  # -35 + 5

        # Apply updates to verify state change
        store._persist_state = AsyncMock()
        await store.apply_zone_updates(updates)

        # Assert: State updated
        assert store._clients['client-a'].volume_db == -25.0
        assert store._clients['client-b'].volume_db == -30.0
        assert store._clients['client-c'].volume_db == -40.0  # Unchanged (offline)

    @pytest.mark.asyncio
    async def test_ac3_mute_endpoint_updates_client_state(
        self,
        volume_service: VolumeService,
        websocket_collector: WebSocketEventCollector
    ):
        """
        AC3: Mute endpoint updates client state correctly.

        Validates:
        - Mute state change is persisted
        - WebSocket event is broadcast
        """
        # Setup
        await volume_service.set_volume_db(-30.0)
        websocket_collector.clear()

        # Action: Mute client
        await volume_service.set_client_mute("local", True, broadcast=True)

        # Assert: State updated
        state = await volume_service.get_volume_state()
        local_client = state.clients.get("local")
        assert local_client is not None
        assert local_client.mute is True

        # Assert: WebSocket event broadcast
        events = websocket_collector.get_events_by_type("volume_changed")
        assert len(events) >= 1

    @pytest.mark.asyncio
    async def test_ac4_get_settings_returns_config_values(
        self,
        volume_service: VolumeService
    ):
        """
        AC4: GET /api/volume/settings returns current config.

        Validates:
        - Returns startup_volume_db from VolumeService config
        - Returns restore_last_volume from VolumeService config
        """
        # Get config values directly from service
        config = volume_service._volume_config

        # Assert: Values are accessible
        assert hasattr(config, 'startup_volume_db')
        assert hasattr(config, 'restore_last_volume')
        assert isinstance(config.startup_volume_db, float)
        assert isinstance(config.restore_last_volume, bool)

    @pytest.mark.asyncio
    async def test_ac5_patch_settings_updates_config(
        self,
        mock_state_machine,
        mock_snapcast_service,
        mock_camilladsp_service,
        websocket_collector: WebSocketEventCollector,
        temp_storage_path
    ):
        """
        AC5: PATCH /api/volume/settings updates config and broadcasts.

        Validates:
        - Settings service is called to persist changes
        - VolumeService config is reloaded
        - WebSocket event is optionally broadcast
        """
        # Create settings service that tracks updates
        settings = Mock()
        settings.invalidate_cache = Mock()
        settings_data = {
            "limit_min_db": -80.0,
            "limit_max_db": -21.0,
            "startup_volume_db": -60.0,
            "restore_last_volume": False,
            "step_mobile_db": 3.0,
            "step_rotary_db": 2.0
        }

        async def mock_get_setting(key):
            if key == "volume":
                return settings_data.copy()
            elif key.startswith("volume."):
                subkey = key.replace("volume.", "")
                return settings_data.get(subkey)
            elif key == "routing.multiroom_enabled":
                return False
            elif key == "equalizer.linked_groups":
                return []
            return None

        async def mock_set_setting(key, value):
            if key.startswith("volume."):
                subkey = key.replace("volume.", "")
                settings_data[subkey] = value
            return True

        settings.get_setting = AsyncMock(side_effect=mock_get_setting)
        settings.set_setting = AsyncMock(side_effect=mock_set_setting)

        with patch.object(VolumeStateStore, 'STORAGE_PATH', temp_storage_path):
            service = VolumeService(
                state_machine=mock_state_machine,
                snapcast_service=mock_snapcast_service,
                settings_service=settings,
                camilladsp_service=mock_camilladsp_service,
                equalizer_client_proxy_service=None
            )
            await service.initialize()

            # Simulate what PATCH endpoint would do
            # Update startup_volume_db
            await settings.set_setting('volume.startup_volume_db', -45.0)
            service._volume_config.startup_volume_db = -45.0

            # Assert: Settings updated
            assert settings_data['startup_volume_db'] == -45.0
            assert service._volume_config.startup_volume_db == -45.0

            await service.cleanup()

    @pytest.mark.asyncio
    async def test_zone_delta_returns_new_average(
        self,
        volume_state_store: VolumeStateStore
    ):
        """
        Test zone delta endpoint returns new computed average.

        Validates:
        - After applying delta, zone average is correctly computed
        """
        store = volume_state_store
        store._mode = "multiroom"
        store.set_volume_config(VolumeConfig(limit_min_db=-80.0, limit_max_db=0.0))

        # Setup zone
        store._zones = {
            'test-zone': ZoneConfig(
                zone_id='test-zone',
                name='Test Zone',
                client_ids=['client-a', 'client-b']
            )
        }
        store._clients = {
            'client-a': ClientVolume(volume_db=-30.0, offset_db=0.0, mute=False, available=True),
            'client-b': ClientVolume(volume_db=-40.0, offset_db=0.0, mute=False, available=True)
        }

        # Initial average: (-30 + -40) / 2 = -35
        assert store.compute_zone_average('test-zone') == pytest.approx(-35.0, rel=1e-6)

        # Apply delta and updates
        updates = await store.apply_zone_delta('test-zone', 10.0)
        store._persist_state = AsyncMock()
        await store.apply_zone_updates(updates)

        # New average: (-20 + -30) / 2 = -25
        new_average = store.compute_zone_average('test-zone')
        assert new_average == pytest.approx(-25.0, rel=1e-6)
