# backend/tests/integration/test_volume_control.py
"""
Integration tests for volume control functionality.

These tests validate the contracts for volume management that must
remain stable during the feature-based architecture refactoring.

Contracts being tested:
- Volume set/get via VolumeService
- WebSocket volume_changed events
- Volume limits and clamping
- Mute/unmute functionality
- Volume persistence to disk
"""
import pytest
import asyncio
import json
from unittest.mock import Mock, AsyncMock, patch

from backend.core.settings import SettingsService
from backend.core.multiroom.equalizer_router import EqualizerRouter
from backend.core.volume import VolumeService
from backend.core.volume.state import StoredLevel, VolumeStateStore
from backend.core.models.volume import VolumeConfig
from backend.core.models.volume_state import VolumeState
from backend.config.constants import DEFAULT_VOLUME_DB

from backend.tests.conftest import WebSocketEventCollector
from backend.tests.volume_world import world


# ==============================================================================
# FIXTURES
# ==============================================================================


@pytest.fixture
def mock_camilladsp_service():
    """Mock Equalizer controller to avoid real hardware calls."""
    service = Mock()
    service.set_volume = AsyncMock(return_value=True)
    service.set_mute = AsyncMock(return_value=True)
    service.is_volume_control_available = Mock(return_value=True)
    service.wait_for_connection = AsyncMock(return_value=True)
    return service


@pytest.fixture
def mock_snapcast_service():
    """Mock Snapcast service to avoid WebSocket calls."""
    service = Mock()
    service.set_volume = AsyncMock(return_value=True)
    return service


def volume_section(**overrides):
    """A complete `volume` section, defaulted from the one declaration.

    `_load_volume_config` reads every key directly — the fallback operands it
    used to carry were a third declaration of these values and had drifted from
    `SettingsService.defaults`. A partial section is a settings.json
    `_validate_and_merge` cannot produce, so a test handing one over is pinning
    a state that cannot occur. Each call below still spells out the values it
    depends on; only the keys it does not care about come from the defaults.
    """
    return {**SettingsService().defaults["volume"], **overrides}


@pytest.fixture
def mock_settings_service():
    """Mock settings service with default volume configuration."""
    service = Mock()
    service.invalidate_cache = Mock()

    # Default volume settings
    volume_config = volume_section(
        limit_min_db=-80.0,
        limit_max_db=-21.0,
        startup_volume_db=-30.0,
        restore_last_volume=False,
        step_mobile_db=3.0,
        step_rotary_db=2.0,
    )

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

    async def mock_broadcast(event):
        await websocket_collector.broadcast_dict({
            "category": event.CATEGORY,
            "type": event.TYPE,
            "origin": event.origin,
            "data": event.wire_data(),
            "timestamp": asyncio.get_running_loop().time()
        })

    sm.broadcast = AsyncMock(side_effect=mock_broadcast)
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
        store = VolumeStateStore()
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
            equalizer_client_proxy_service=None,
            equalizer_router=EqualizerRouter(
                client_registry=None,
                camilladsp_service=mock_camilladsp_service,
                proxy_service=None,
            ),
        )

        # Initialize service
        await service.initialize()

        # Register "local" client so tests can use it (simulates what
        # ClientRegistryService does in production when local Snapcast client connects)
        service._state_store._local_mac_id = "local"
        await service._state_store.register_client("local", volume_db=DEFAULT_VOLUME_DB)
        world(service._state_store).online.add("local")

        yield service

        # Cleanup
        await service.cleanup()


# ==============================================================================
# Test Volume API Operations
# ==============================================================================


class TestVolumeAPI:
    """Tests for Volume set/get via API."""

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
# Test WebSocket Events
# ==============================================================================


class TestVolumeWebSocketEvents:
    """Tests for WebSocket volume_changed events."""

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


# ==============================================================================
# Test Volume Limits
# ==============================================================================


class TestVolumeLimits:
    """Tests for Volume limits and clamping."""

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
        # Update limits to custom values
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
# Test Mute/Unmute
# ==============================================================================


class TestMuteUnmute:
    """Tests for Mute/unmute functionality."""

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
# Test Persistence
# ==============================================================================


class TestVolumePersistence:
    """Tests for Volume persistence."""

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
                return volume_section(
                    limit_min_db=-80.0,
                    limit_max_db=-21.0,
                    startup_volume_db=-30.0,
                    restore_last_volume=True,  # Enable persistence
                    step_mobile_db=3.0,
                    step_rotary_db=2.0,
                )
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
                equalizer_client_proxy_service=None,
                equalizer_router=EqualizerRouter(
                    client_registry=None,
                    camilladsp_service=mock_camilladsp_service,
                    proxy_service=None,
                ),
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
                return volume_section(
                    limit_min_db=-80.0,
                    limit_max_db=-21.0,
                    startup_volume_db=-30.0,
                    restore_last_volume=True,
                    step_mobile_db=3.0,
                    step_rotary_db=2.0,
                )
            elif key == "volume.restore_last_volume":
                return True
            elif key == "routing.multiroom_enabled":
                return False
            return None

        mock_settings_service.get_setting = AsyncMock(side_effect=mock_get_setting)

        # Create new store with mocked path
        with patch.object(VolumeStateStore, 'STORAGE_PATH', temp_storage_path):
            store = VolumeStateStore()
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
                return volume_section(
                    limit_min_db=-80.0,
                    limit_max_db=-21.0,
                    startup_volume_db=-30.0,
                    restore_last_volume=True,  # Enable persistence
                    step_mobile_db=3.0,
                    step_rotary_db=2.0,
                )
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
                equalizer_client_proxy_service=None,
                equalizer_router=EqualizerRouter(
                    client_registry=None,
                    camilladsp_service=mock_camilladsp_service,
                    proxy_service=None,
                ),
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
            store = VolumeStateStore()
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
        state = await volume_state_store.get_complete_state(multiroom=True)

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
        await volume_state_store.register_client("test-client", volume_db=-40.0)

        assert "test-client" in volume_state_store._clients
        assert volume_state_store._clients["test-client"].volume_db == -40.0

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
# Client Volume Control API Integration Tests
# ==============================================================================


class TestClientVolumeAPI:
    """Integration Tests for Client Volume Control API endpoints."""

    @pytest.mark.asyncio
    async def test_update_client_volume_db_broadcasts_event(
        self,
        volume_service: VolumeService,
        websocket_collector: WebSocketEventCollector
    ):
        """
        Test: Client volume change broadcasts WebSocket event.

        Validates:
        - update_client_volume_db broadcasts volume_changed event
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
        Test: Client mute toggle broadcasts WebSocket event.

        Validates:
        - set_client_mute broadcasts volume_changed event
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
        Test: get_client_volume returns correct volume and mute state.

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
        Test: Offline client volume persistence.

        Validates:
        - Client volume is persisted to VolumeStateStore
        - Volume can be retrieved after being set
        """
        # Register a client with specific volume
        await volume_state_store.register_client("test-client", volume_db=-42.0)
        world(volume_state_store).online.add("test-client")

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
        Test: Mute state persistence.

        Validates:
        - Mute state is persisted to VolumeStateStore
        - Mute state can be retrieved after being set
        """
        # Register a client
        await volume_state_store.register_client("test-client", volume_db=-30.0)
        world(volume_state_store).online.add("test-client")

        # Set mute state
        await volume_state_store.set_client_mute("test-client", True)

        # Verify mute state was stored
        assert volume_state_store._clients["test-client"].mute is True

        # Toggle mute off
        await volume_state_store.set_client_mute("test-client", False)
        assert volume_state_store._clients["test-client"].mute is False


# ==============================================================================
# Zone Volume Delta Integration Tests
# ==============================================================================


async def _moved(store, zone_id, delta_db):
    """Move a zone through VolumeService over `store`; answer (stored levels of
    its members, what each reachable speaker was sent)."""
    service = VolumeService(state_machine=Mock(broadcast=AsyncMock()), snapcast_service=Mock(),
                            settings_service=Mock(get_setting=AsyncMock(return_value=None)))
    service._volume_config = store._volume_config
    service._state_store = store
    service._client_registry = world(store)
    service.broadcast_volume_state = AsyncMock()
    sent = {}

    def submit(mac_id, volume_db, force=False):
        sent[mac_id] = volume_db
        done = asyncio.get_running_loop().create_future()
        done.set_result(True)
        return done

    service._equalizer_controller.submit_volume = submit
    service._routing_service = Mock(get_state=Mock(return_value={"multiroom_enabled": True}))  # zones move in multiroom
    await service.apply_zone_volume_delta(zone_id, delta_db)
    members = world(store).zones[zone_id].client_ids
    return {mac: store.get_client_volume(mac) for mac in members}, sent


class TestZoneVolumeDeltaIntegration:
    """Integration Tests for Zone Volume Delta.

    The unit tests in test_volume_state.py cover the same functionality.
    """

    @pytest.fixture
    def zone_state_store(self, mock_settings_service, temp_storage_path):
        """VolumeStateStore configured for zone testing."""
        with patch.object(VolumeStateStore, 'STORAGE_PATH', temp_storage_path):
            store = VolumeStateStore()
            store.set_volume_config(VolumeConfig(limit_min_db=-80.0, limit_max_db=0.0))
            yield store

    @pytest.mark.asyncio
    async def test_zone_delta_preserves_relative_offsets_end_to_end(
        self,
        zone_state_store: VolumeStateStore
    ):
        """
        Zone delta preserves relative offsets end-to-end.

        Validates:
        - Before: clients at different volumes (5dB difference)
        - After: both clients moved, difference preserved
        """
        store = zone_state_store

        # Setup: zone with 5dB offset between clients
        world(store).zone('zone_1', ['client_a', 'client_b'], name='Test Zone')
        store._clients = {
            'client_a': StoredLevel(volume_db=-25.0, mute=False),
            'client_b': StoredLevel(volume_db=-30.0, mute=False)
        }
        world(store).online |= {'client_a', 'client_b'}

        # Record initial difference
        initial_diff = store._clients['client_a'].volume_db - store._clients['client_b'].volume_db
        assert initial_diff == 5.0

        # Action: apply +3dB delta
        updates, _ = await _moved(store, 'zone_1', 3.0)

        # Assert: volumes changed, difference preserved
        assert updates['client_a'] - updates['client_b'] == 5.0  # Same 5dB difference
        assert updates['client_a'] == -22.0  # -25 + 3
        assert updates['client_b'] == -27.0  # -30 + 3

    @pytest.mark.asyncio
    async def test_zone_delta_covers_offline_clients_but_sends_them_nothing(
        self,
        zone_state_store: VolumeStateStore
    ):
        """
        Zone delta moves every member, and sends only to the reachable ones.

        Validates:
        - ONLINE and OFFLINE members both receive the delta in the store
        - Only the ONLINE member is sent a command
        """
        store = zone_state_store

        # Setup: zone with mixed availability
        world(store).zone('zone_1', ['online_client', 'offline_client'], name='Test Zone')
        store._clients = {
            'online_client': StoredLevel(volume_db=-30.0, mute=False),
            'offline_client': StoredLevel(volume_db=-30.0, mute=False)
        }
        world(store).online |= {'online_client'}

        # Action: apply delta
        updates, sent = await _moved(store, 'zone_1', 5.0)

        # Assert: both members moved
        assert updates['online_client'] == -25.0  # -30 + 5
        assert updates['offline_client'] == -25.0  # -30 + 5

        # Only the reachable one was sent anything
        assert sent == {'online_client': -25.0}

    def test_zone_average_readonly_computed(
        self,
        zone_state_store: VolumeStateStore
    ):
        """
        Zone average is readonly/computed from ONLINE clients.

        Validates:
        - Zone average computed from available clients only
        - OFFLINE clients excluded from average
        """
        store = zone_state_store

        # Setup: zone with mixed availability
        world(store).zone('zone_1', ['online_1', 'online_2', 'offline_1'], name='Test Zone')
        store._clients = {
            'online_1': StoredLevel(volume_db=-20.0, mute=False),
            'online_2': StoredLevel(volume_db=-30.0, mute=False),
            'offline_1': StoredLevel(volume_db=-50.0, mute=False)
        }
        world(store).online |= {'online_1', 'online_2'}

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
        Zone delta respects volume limits.

        Validates:
        - Delta that would exceed limits is clamped
        """
        store = zone_state_store

        # Set limits
        store.set_volume_config(VolumeConfig(limit_min_db=-80.0, limit_max_db=-21.0))

        # Setup: client near maximum
        world(store).zone('zone_1', ['client_a'], name='Test Zone')
        store._clients = {
            'client_a': StoredLevel(volume_db=-25.0, mute=False)
        }
        world(store).online |= {'client_a'}

        # Action: apply delta that would exceed max (-25 + 10 = -15 > -21)
        updates, _ = await _moved(store, 'zone_1', 10.0)

        # Assert: clamped to maximum
        assert updates['client_a'] == -21.0

    @pytest.mark.asyncio
    async def test_zone_average_updates_after_delta_applied(
        self,
        zone_state_store: VolumeStateStore
    ):
        """
        Zone average updates after delta applied.

        Validates:
        - Zone average reflects new client volumes after delta
        """
        store = zone_state_store

        # Setup: zone with clients
        world(store).zone('zone_1', ['client_a', 'client_b'], name='Test Zone')
        store._clients = {
            'client_a': StoredLevel(volume_db=-30.0, mute=False),
            'client_b': StoredLevel(volume_db=-30.0, mute=False)
        }
        world(store).online |= {'client_a', 'client_b'}

        # Verify initial average
        assert store.compute_zone_average('zone_1') == pytest.approx(-30.0, rel=1e-6)

        # Action: apply +10dB delta
        await _moved(store, 'zone_1', 10.0)

        # Assert: average updated
        assert store.compute_zone_average('zone_1') == pytest.approx(-20.0, rel=1e-6)

    @pytest.mark.asyncio
    async def test_zone_delta_returns_correct_updates(
        self,
        zone_state_store: VolumeStateStore
    ):
        """
        A zone delta moves each member by the delta.

        Validates:
        - Each member's stored level is its old one plus the delta
        """
        store = zone_state_store

        # Setup: zone with clients
        world(store).zone('zone_1', ['client_a', 'client_b'], name='Test Zone')
        store._clients = {
            'client_a': StoredLevel(volume_db=-30.0, mute=False),
            'client_b': StoredLevel(volume_db=-40.0, mute=False)
        }
        world(store).online |= {'client_a', 'client_b'}

        # Action: apply delta
        updates, _ = await _moved(store, 'zone_1', 5.0)

        # Assert: the members' new volumes
        assert 'client_a' in updates
        assert 'client_b' in updates
        assert updates['client_a'] == -25.0  # -30 + 5
        assert updates['client_b'] == -35.0  # -40 + 5

    @pytest.mark.asyncio
    async def test_zone_with_all_offline_clients_still_records_the_delta(
        self,
        zone_state_store: VolumeStateStore
    ):
        """
        Zone with all offline clients still returns the delta for each member.

        Validates:
        - Availability decides the hardware fan-out, never the arithmetic
        """
        store = zone_state_store

        # Setup: zone with all OFFLINE clients
        world(store).zone('zone_1', ['offline_a', 'offline_b'], name='Test Zone')
        store._clients = {
            'offline_a': StoredLevel(volume_db=-30.0, mute=False),
            'offline_b': StoredLevel(volume_db=-30.0, mute=False)
        }

        # Action: apply delta
        updates, sent = await _moved(store, 'zone_1', 5.0)

        # Assert: every member moved, and nothing was sent
        assert sent == {}
        assert updates == {'offline_a': -25.0, 'offline_b': -25.0}

    @pytest.mark.asyncio
    async def test_zone_average_returns_default_when_all_offline(
        self,
        zone_state_store: VolumeStateStore
    ):
        """
        Zone average returns DEFAULT_VOLUME_DB when all clients offline.
        """
        store = zone_state_store

        # Setup: zone with all OFFLINE clients
        world(store).zone('zone_1', ['offline_a', 'offline_b'], name='Test Zone')
        store._clients = {
            'offline_a': StoredLevel(volume_db=-20.0, mute=False),
            'offline_b': StoredLevel(volume_db=-30.0, mute=False)
        }

        # Action: get zone average
        average = store.compute_zone_average('zone_1')

        # Assert: returns default
        assert average == DEFAULT_VOLUME_DB


# ==============================================================================
# Startup Volume Management Integration Tests
# ==============================================================================


class TestStartupVolumeIntegration:
    """Integration Tests for Startup Volume Management.

    Auto-update startup_volume_db when restore_last_volume is disabled
    Backend restart applies startup volume
    """

    @pytest.mark.asyncio
    async def test_a_volume_change_leaves_the_startup_setting_alone(
        self,
        mock_state_machine,
        mock_snapcast_service,
        mock_camilladsp_service,
        websocket_collector: WebSocketEventCollector,
        temp_storage_path
    ):
        """startup_volume_db is the operator's setting, and a volume change is not one.

        It used to track the last volume when restore_last_volume was on: every
        step rewrote the setting (debounced) and sent `volume_startup_changed`
        to every screen — a second event per rotary detent — while
        last_volume.json already held each room's level. Fails if a volume
        change writes the setting or announces it.
        """
        # Create settings with restore_last_volume=True (active: auto-track volume)
        settings = Mock()
        settings.invalidate_cache = Mock()
        settings_data = volume_section(
            limit_min_db=-80.0,
            limit_max_db=-21.0,
            startup_volume_db=-60.0,  # Initial value
            restore_last_volume=True,  # active: auto-track current volume
            step_mobile_db=3.0,
            step_rotary_db=2.0,
        )

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
                equalizer_client_proxy_service=None,
                equalizer_router=EqualizerRouter(
                    client_registry=None,
                    camilladsp_service=mock_camilladsp_service,
                    proxy_service=None,
                ),
            )
            await service.initialize()
            websocket_collector.clear()

            # Action: Set volume to -45dB
            await service.set_volume_db(-45.0)

            await service.cleanup()
            assert websocket_collector.get_events_by_type("volume_startup_changed") == []
            assert websocket_collector.get_events_by_type("volume_changed"), "the move itself was announced"
            settings.set_setting.assert_not_called()
            assert settings_data["startup_volume_db"] == -60.0

    @pytest.mark.asyncio
    async def test_restore_disabled_does_not_update_startup_volume(
        self,
        mock_state_machine,
        mock_snapcast_service,
        mock_camilladsp_service,
        websocket_collector: WebSocketEventCollector,
        temp_storage_path
    ):
        """
        When restore_last_volume=false, startup_volume_db remains unchanged.

        Validates:
        - Volume changes do NOT update startup_volume_db
        - No settings WebSocket event is broadcast
        """
        # Create settings with restore_last_volume=False (NOT active: fixed startup volume)
        settings = Mock()
        settings.invalidate_cache = Mock()
        settings_data = volume_section(
            limit_min_db=-80.0,
            limit_max_db=-21.0,
            startup_volume_db=-60.0,
            restore_last_volume=False,  # NOT active
            step_mobile_db=3.0,
            step_rotary_db=2.0,
        )

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
                equalizer_client_proxy_service=None,
                equalizer_router=EqualizerRouter(
                    client_registry=None,
                    camilladsp_service=mock_camilladsp_service,
                    proxy_service=None,
                ),
            )
            await service.initialize()
            websocket_collector.clear()

            # Action: Set volume
            await service.set_volume_db(-45.0)

            # Assert: set_setting was NOT called (not active)
            settings.set_setting.assert_not_called()

            # Assert: startup_volume_db unchanged
            assert settings_data["startup_volume_db"] == -60.0

            # Assert: No settings broadcast
            events = websocket_collector.get_events_by_type("volume_startup_changed")
            assert len(events) == 0

            await service.cleanup()

    @pytest.mark.asyncio
    async def test_startup_uses_startup_volume_when_restore_disabled(
        self,
        mock_state_machine,
        mock_snapcast_service,
        mock_camilladsp_service,
        temp_storage_path
    ):
        """
        Backend startup applies startup_volume_db when restore=false.

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
                return volume_section(
                    limit_min_db=-80.0,
                    limit_max_db=-21.0,
                    startup_volume_db=startup_volume,
                    restore_last_volume=False,  # Use startup_volume_db
                    step_mobile_db=3.0,
                    step_rotary_db=2.0,
                )
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
                equalizer_client_proxy_service=None,
                equalizer_router=EqualizerRouter(
                    client_registry=None,
                    camilladsp_service=mock_camilladsp_service,
                    proxy_service=None,
                ),
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
    async def test_startup_restores_persisted_volume_when_restore_enabled(
        self,
        mock_state_machine,
        mock_snapcast_service,
        mock_camilladsp_service,
        temp_storage_path
    ):
        """
        Backend startup applies startup_volume_db when restore=true.

        When restore_last_volume=true, keeps startup_volume_db in sync with
        current volume during runtime. At restart, startup_volume_db already contains
        the correct last volume, so it's the single source of truth.

        Validates:
        - On initialize, Equalizer receives startup_volume_db from settings
        - startup_volume_db was pre-synced by before shutdown
        """
        # Create settings: startup_volume_db already synced by to -42.0
        settings = Mock()
        settings.invalidate_cache = Mock()
        persisted_volume = -42.0

        async def mock_get_setting(key):
            if key == "volume":
                return volume_section(
                    limit_min_db=-80.0,
                    limit_max_db=-21.0,
                    startup_volume_db=persisted_volume,  # synced this before shutdown
                    restore_last_volume=True,
                    step_mobile_db=3.0,
                    step_rotary_db=2.0,
                )
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
                equalizer_client_proxy_service=None,
                equalizer_router=EqualizerRouter(
                    client_registry=None,
                    camilladsp_service=mock_camilladsp_service,
                    proxy_service=None,
                ),
            )

            # Action: Initialize service
            await service.initialize()

            # Assert: Equalizer was set to startup_volume_db (which synced to persisted_volume)
            mock_camilladsp_service.set_volume.assert_called()
            calls = mock_camilladsp_service.set_volume.call_args_list
            volume_calls = [c for c in calls if c[0][0] == persisted_volume]
            assert len(volume_calls) >= 1, f"Expected call with {persisted_volume}, got {calls}"

            await service.cleanup()


# ==============================================================================
# Volume API Endpoints Integration Tests
# ==============================================================================


class TestVolumeApiEndpointsIntegration:
    """Integration Tests for Volume API Endpoints.

    Tests the complete flow from API endpoint through VolumeService
    for MAC address-based operations, zone delta, and settings.
    """

    @pytest.mark.asyncio
    async def test_mac_volume_flow_end_to_end(
        self,
        volume_service: VolumeService,
        websocket_collector: WebSocketEventCollector
    ):
        """
        MAC address volume update flows through service correctly.

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
    async def test_zone_delta_applies_to_every_member_of_the_zone(
        self,
        volume_state_store: VolumeStateStore
    ):
        """
        Zone delta applies to every member and returns correct data.

        Validates:
        - a zone move moves each member with volume control by the delta
        - Returns dict with affected clients and new volumes
        - An offline member is included, so the delta is in its level on return
        """
        store = volume_state_store
        store._mode = "multiroom"
        store.set_volume_config(VolumeConfig(limit_min_db=-80.0, limit_max_db=0.0))

        # Setup zone with 3 clients (2 online, 1 offline)
        world(store).zone('test-zone', ['client-a', 'client-b', 'client-c'], name='Test Zone')
        store._clients = {
            'client-a': StoredLevel(volume_db=-30.0, mute=False),
            'client-b': StoredLevel(volume_db=-35.0, mute=False),
            'client-c': StoredLevel(volume_db=-40.0, mute=False)  # Offline
        }
        world(store).online |= {'client-a', 'client-b'}

        # Apply zone delta
        updates, sent = await _moved(store, 'test-zone', 5.0)

        # Assert: every member moved, offline one included; only the online sent
        assert set(updates) == {'client-a', 'client-b', 'client-c'}
        assert sent == {'client-a': -25.0, 'client-b': -30.0}

        # Assert: State updated
        assert store._clients['client-a'].volume_db == -25.0
        assert store._clients['client-b'].volume_db == -30.0
        assert store._clients['client-c'].volume_db == -35.0  # moved while offline

    @pytest.mark.asyncio
    async def test_mute_endpoint_updates_client_state(
        self,
        volume_service: VolumeService,
        websocket_collector: WebSocketEventCollector
    ):
        """
        Mute endpoint updates client state correctly.

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
    async def test_patch_settings_updates_config(
        self,
        mock_state_machine,
        mock_snapcast_service,
        mock_camilladsp_service,
        websocket_collector: WebSocketEventCollector,
        temp_storage_path
    ):
        """
        PATCH /api/volume/settings updates config and broadcasts.

        Validates:
        - Settings service is called to persist changes
        - VolumeService config is reloaded
        - WebSocket event is optionally broadcast
        """
        # Create settings service that tracks updates
        settings = Mock()
        settings.invalidate_cache = Mock()
        settings_data = volume_section(
            limit_min_db=-80.0,
            limit_max_db=-21.0,
            startup_volume_db=-60.0,
            restore_last_volume=False,
            step_mobile_db=3.0,
            step_rotary_db=2.0,
        )

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
                equalizer_client_proxy_service=None,
                equalizer_router=EqualizerRouter(
                    client_registry=None,
                    camilladsp_service=mock_camilladsp_service,
                    proxy_service=None,
                ),
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
        world(store).zone('test-zone', ['client-a', 'client-b'], name='Test Zone')
        store._clients = {
            'client-a': StoredLevel(volume_db=-30.0, mute=False),
            'client-b': StoredLevel(volume_db=-40.0, mute=False)
        }
        world(store).online |= {'client-a', 'client-b'}

        # Initial average: (-30 + -40) / 2 = -35
        assert store.compute_zone_average('test-zone') == pytest.approx(-35.0, rel=1e-6)

        # Apply delta
        await _moved(store, 'test-zone', 10.0)

        # New average: (-20 + -30) / 2 = -25
        new_average = store.compute_zone_average('test-zone')
        assert new_average == pytest.approx(-25.0, rel=1e-6)
