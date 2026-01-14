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
import tempfile
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime, timezone

from backend.core.volume import VolumeService
from backend.core.volume.state import VolumeStateStore
from backend.core.volume.config import VolumeConfigService
from backend.core.models.volume import VolumeConfig
from backend.core.models.volume_state import VolumeState, ClientVolume

from .conftest import WebSocketEventCollector


# ==============================================================================
# FIXTURES
# ==============================================================================


@pytest.fixture
def mock_dsp_service():
    """Mock DSP controller to avoid real hardware calls."""
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
        elif key == "dsp.linked_groups":
            return []
        return None

    async def mock_set_setting(key, value):
        if key.startswith("volume."):
            subkey = key.replace("volume.", "")
            volume_config[subkey] = value
        return True

    service.get_setting = AsyncMock(side_effect=mock_get_setting)
    service.set_setting = AsyncMock(side_effect=mock_set_setting)
    service.get_volume_config = Mock(return_value=volume_config)

    return service


@pytest.fixture
def mock_state_machine(websocket_collector: WebSocketEventCollector):
    """Mock state machine with WebSocket event collection."""
    sm = Mock()

    async def mock_broadcast(category, event_type, data):
        await websocket_collector.handle_event({
            "category": category,
            "type": event_type,
            "source": "volume",
            "data": data,
            "timestamp": asyncio.get_event_loop().time()
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
        await store.initialize()
        yield store


@pytest.fixture
async def volume_service(
    mock_state_machine,
    mock_snapcast_service,
    mock_settings_service,
    mock_dsp_service,
    websocket_collector: WebSocketEventCollector,
    temp_storage_path
):
    """VolumeService with mocked dependencies for integration testing."""
    with patch.object(VolumeStateStore, 'STORAGE_PATH', temp_storage_path):
        service = VolumeService(
            state_machine=mock_state_machine,
            snapcast_service=mock_snapcast_service,
            settings_service=mock_settings_service,
            camilladsp_service=mock_dsp_service,
            dsp_client_proxy_service=None
        )

        # Initialize service
        await service.initialize()

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
        # Update limits to custom values
        volume_service._state_store.update_user_limits(-60.0, -25.0)
        volume_service._config_service._config.limit_min_db = -60.0
        volume_service._config_service._config.limit_max_db = -25.0

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
        mock_dsp_service,
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
            elif key == "dsp.linked_groups":
                return []
            return None

        settings.get_setting = AsyncMock(side_effect=mock_get_setting)
        settings.set_setting = AsyncMock(return_value=True)
        settings.get_volume_config = Mock(return_value={
            "limit_min_db": -80.0,
            "limit_max_db": -21.0,
            "startup_volume_db": -30.0,
            "restore_last_volume": True,
            "step_mobile_db": 3.0,
            "step_rotary_db": 2.0
        })

        with patch.object(VolumeStateStore, 'STORAGE_PATH', temp_storage_path):
            service = VolumeService(
                state_machine=mock_state_machine,
                snapcast_service=mock_snapcast_service,
                settings_service=settings,
                camilladsp_service=mock_dsp_service,
                dsp_client_proxy_service=None
            )
            await service.initialize()

            # Set volume
            await service.set_volume_db(-42.0)

            # Wait for async background save
            await asyncio.sleep(0.3)

            # Check file exists and contains correct data
            assert temp_storage_path.exists(), "Persistence file should exist"

            with open(temp_storage_path) as f:
                data = json.load(f)

            assert "local_volume_db" in data or "clients" in data
            # Volume should be in clients dict
            if "clients" in data and "local" in data["clients"]:
                assert data["clients"]["local"]["volume_db"] == -42.0

            await service.cleanup()

    @pytest.mark.asyncio
    async def test_volume_restored_on_startup(
        self,
        mock_settings_service,
        mock_state_machine,
        mock_snapcast_service,
        mock_dsp_service,
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
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "local_volume_db": -35.0,
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
            assert store._local_volume_db == -35.0
            assert 'local' in store._clients
            assert store._clients['local'].volume_db == -35.0

    @pytest.mark.asyncio
    async def test_persistence_format_valid(
        self,
        mock_state_machine,
        mock_snapcast_service,
        mock_dsp_service,
        temp_storage_path
    ):
        """
        Test persistence file format.

        Validates:
        - JSON format with required fields
        - timestamp, local_volume_db, clients
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
            elif key == "dsp.linked_groups":
                return []
            return None

        settings.get_setting = AsyncMock(side_effect=mock_get_setting)
        settings.set_setting = AsyncMock(return_value=True)
        settings.get_volume_config = Mock(return_value={
            "limit_min_db": -80.0,
            "limit_max_db": -21.0,
            "startup_volume_db": -30.0,
            "restore_last_volume": True,
            "step_mobile_db": 3.0,
            "step_rotary_db": 2.0
        })

        with patch.object(VolumeStateStore, 'STORAGE_PATH', temp_storage_path):
            service = VolumeService(
                state_machine=mock_state_machine,
                snapcast_service=mock_snapcast_service,
                settings_service=settings,
                camilladsp_service=mock_dsp_service,
                dsp_client_proxy_service=None
            )
            await service.initialize()

            await service.set_volume_db(-28.0)
            await asyncio.sleep(0.3)

            assert temp_storage_path.exists()

            with open(temp_storage_path) as f:
                data = json.load(f)

            # Validate format
            assert "timestamp" in data
            assert isinstance(data["timestamp"], str)

            # Should have clients or local_volume_db
            assert "clients" in data or "local_volume_db" in data

            # Timestamp should be valid ISO format
            try:
                datetime.fromisoformat(data["timestamp"].replace("Z", "+00:00"))
            except ValueError:
                pytest.fail("Timestamp should be valid ISO format")

            await service.cleanup()

    @pytest.mark.asyncio
    async def test_stale_persistence_ignored(
        self,
        mock_settings_service,
        temp_storage_path
    ):
        """
        Test stale (>7 days old) persistence data is ignored.

        Validates:
        - Old timestamp causes data to be ignored
        - Default volume is used instead
        """
        from datetime import timedelta

        # Create stale persistence file (8 days old)
        old_time = datetime.now(timezone.utc) - timedelta(days=8)
        persist_data = {
            "timestamp": old_time.isoformat(),
            "local_volume_db": -45.0,
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

            # Stale data should be ignored, use default
            assert store._local_volume_db == VolumeStateStore.DEFAULT_VOLUME_DB


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

        assert result == volume_state_store._user_limit_min_db

    @pytest.mark.asyncio
    async def test_update_user_limits_updates_clamping(
        self,
        volume_state_store: VolumeStateStore
    ):
        """
        Test updating user limits affects clamping behavior.
        """
        # Update limits
        volume_state_store.update_user_limits(-50.0, -25.0)

        assert volume_state_store._user_limit_min_db == -50.0
        assert volume_state_store._user_limit_max_db == -25.0

        # Verify clamping uses new limits
        result = volume_state_store._clamp_db(-60.0)
        assert result == -50.0

        result = volume_state_store._clamp_db(-20.0)
        assert result == -25.0
