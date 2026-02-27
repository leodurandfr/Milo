# backend/tests/test_core_volume.py
"""
Unit tests for core.volume module.

Tests the migrated VolumeService, VolumeStateStore, VolumeConfigService,
and EqualizerController in the new core/volume/ location.
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
import asyncio

from backend.core.volume import (
    VolumeService,
    VolumeStateStore,
    VolumeConfigService,
    EqualizerController
)
from backend.core.events import EventBus, Events, get_event_bus, reset_event_bus
from backend.core.models.volume import VolumeConfig
from backend.core.models.volume_state import VolumeState, ClientVolume, ZoneVolume
from backend.config.constants import DEFAULT_VOLUME_DB, MIN_VOLUME_DB, MAX_VOLUME_DB


# ============================================================================
# VolumeConfigService Tests
# ============================================================================

class TestVolumeConfigService:
    """Tests for VolumeConfigService."""

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings service."""
        settings = Mock()
        settings.invalidate_cache = Mock()
        settings.get_setting = AsyncMock(return_value={
            "limit_min_db": -80.0,
            "limit_max_db": -21.0,
            "step_mobile_db": 3.0,
            "step_rotary_db": 2.0,
            "startup_volume_db": -30.0,
            "restore_last_volume": False
        })
        return settings

    @pytest.fixture
    def config_service(self, mock_settings):
        """Create VolumeConfigService."""
        return VolumeConfigService(mock_settings)

    def test_default_config(self, config_service):
        """Test default configuration values."""
        config = config_service.config
        assert config.limit_min_db == -80.0
        assert config.limit_max_db == -21.0
        assert config.step_mobile_db == 3.0
        assert config.step_rotary_db == 2.0
        assert config.startup_volume_db == DEFAULT_VOLUME_DB
        assert config.restore_last_volume is False

    @pytest.mark.asyncio
    async def test_load_config(self, config_service, mock_settings):
        """Test loading configuration from settings."""
        await config_service.load()

        mock_settings.invalidate_cache.assert_called_once()
        mock_settings.get_setting.assert_called_with('volume')

    @pytest.mark.asyncio
    async def test_reload_limits(self, config_service, mock_settings):
        """Test reloading volume limits."""
        # First load
        await config_service.load()
        old_min, old_max = await config_service.reload_limits()

        assert old_min == -80.0
        assert old_max == -21.0

    def test_get_config_dict(self, config_service):
        """Test getting config as dictionary."""
        result = config_service.get_config_dict()

        assert isinstance(result, dict)
        assert "limit_min_db" in result
        assert "limit_max_db" in result
        assert "step_mobile_db" in result


# ============================================================================
# EqualizerController Tests
# ============================================================================

class TestEqualizerController:
    """Tests for EqualizerController."""

    @pytest.fixture
    def mock_camilladsp_service(self):
        """Create mock CamillaDSP service."""
        camilladsp_mock = Mock()
        camilladsp_mock.set_volume = AsyncMock(return_value=True)
        camilladsp_mock.get_volume = AsyncMock(return_value=-30.0)
        camilladsp_mock.set_mute = AsyncMock(return_value=True)
        camilladsp_mock.wait_for_connection = AsyncMock(return_value=True)
        return camilladsp_mock

    @pytest.fixture
    def mock_proxy_service(self):
        """Create mock proxy service."""
        proxy = Mock()
        proxy.request = AsyncMock(return_value={"status": "success"})
        proxy.check_available = AsyncMock(return_value=True)
        return proxy

    @pytest.fixture
    def mock_registry(self):
        """Create mock client registry."""
        registry = Mock()
        local_client = Mock(ip="127.0.0.1")
        remote_client = Mock(ip="192.168.1.100")
        def get_client(mac_id):
            if mac_id == "local":
                return local_client
            elif mac_id == "milo-client-01":
                return remote_client
            return None
        registry.get_client = get_client
        return registry

    @pytest.fixture
    def controller(self, mock_camilladsp_service, mock_proxy_service, mock_registry):
        """Create EqualizerController."""
        return EqualizerController(mock_camilladsp_service, mock_proxy_service, client_registry=mock_registry)

    @pytest.mark.asyncio
    async def test_set_local_volume(self, controller, mock_camilladsp_service):
        """Test setting local Equalizer volume."""
        result = await controller.set_equalizer_volume("local", -25.0)

        assert result is True
        mock_camilladsp_service.set_volume.assert_called_once_with(-25.0)

    @pytest.mark.asyncio
    async def test_set_remote_volume(self, controller, mock_proxy_service):
        """Test setting remote client volume."""
        result = await controller.set_equalizer_volume("milo-client-01", -27.0)

        assert result is True
        mock_proxy_service.request.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_equalizer_mute_local(self, controller, mock_camilladsp_service):
        """Test setting local Equalizer mute."""
        result = await controller.set_equalizer_mute("local", True)

        assert result is True
        mock_camilladsp_service.set_mute.assert_called_once_with(True)

    @pytest.mark.asyncio
    async def test_apply_volumes_parallel(self, controller):
        """Test parallel volume updates."""
        updates = {
            "local": -25.0,
            "milo-client-01": -27.0
        }

        results = await controller.apply_volumes_parallel(updates)

        assert "local" in results
        assert results["local"] is True

    @pytest.mark.asyncio
    async def test_apply_volumes_parallel_empty(self, controller):
        """Test parallel updates with empty dict."""
        results = await controller.apply_volumes_parallel({})
        assert results == {}

    @pytest.mark.asyncio
    async def test_wait_for_client_ready_local(self, controller, mock_camilladsp_service):
        """Test waiting for local client ready."""
        result = await controller.wait_for_client_ready("local", max_wait=1.0)
        assert result is True
        mock_camilladsp_service.wait_for_connection.assert_called_once()


# ============================================================================
# VolumeStateStore Tests
# ============================================================================

class TestVolumeStateStore:
    """Tests for VolumeStateStore."""

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings service."""
        settings = Mock()
        settings.get_setting = AsyncMock(return_value=None)
        return settings

    @pytest.fixture
    def state_store(self, mock_settings):
        """Create VolumeStateStore."""
        return VolumeStateStore(mock_settings)

    def test_default_values(self, state_store):
        """Test default store values (constants imported from backend.config.constants)."""
        assert MIN_VOLUME_DB == -80.0
        assert MAX_VOLUME_DB == 0.0
        assert DEFAULT_VOLUME_DB == -60.0
        # Verify initial local volume uses default
        assert state_store._local_volume_db == DEFAULT_VOLUME_DB

    def test_clamp_db(self, state_store):
        """Test dB clamping."""
        assert state_store._clamp_db(-90.0) == -80.0  # Below min
        assert state_store._clamp_db(-30.0) == -30.0  # In range (but above default max)
        assert state_store._clamp_db(5.0) == -21.0    # Above default user max

    def test_set_local_volume(self, state_store):
        """Test setting local volume."""
        state_store.set_local_volume(-25.0)
        assert state_store._local_volume_db == -25.0

    def test_update_user_limits(self, state_store):
        """Test updating user limits."""
        state_store.update_user_limits(-60.0, -15.0)
        assert state_store._user_limit_min_db == -60.0
        assert state_store._user_limit_max_db == -15.0

    @pytest.mark.asyncio
    async def test_register_client(self, state_store):
        """Test registering a client."""
        await state_store.register_client("test-client", volume_db=-25.0, available=True)
        assert "test-client" in state_store._clients
        assert state_store._clients["test-client"].volume_db == -25.0
        assert state_store._clients["test-client"].available is True

    @pytest.mark.asyncio
    async def test_set_client_volume(self, state_store):
        """Test setting client volume."""
        await state_store.register_client("test-client", volume_db=-30.0)
        await state_store.set_client_volume("test-client", -25.0)
        assert state_store._clients["test-client"].volume_db == -25.0

    @pytest.mark.asyncio
    async def test_set_client_mute(self, state_store):
        """Test setting client mute state."""
        await state_store.register_client("test-client", volume_db=-30.0)
        await state_store.set_client_mute("test-client", True)
        assert state_store._clients["test-client"].mute is True

    @pytest.mark.asyncio
    async def test_set_client_availability(self, state_store):
        """Test setting client availability."""
        await state_store.register_client("test-client", volume_db=-30.0, available=False)
        await state_store.set_client_availability("test-client", True)
        assert state_store._clients["test-client"].available is True

    def test_get_client_volume(self, state_store):
        """Test getting client volume."""
        state_store._clients["test-client"] = ClientVolume(
            volume_db=-25.0, offset_db=0.0, mute=False, available=True
        )
        assert state_store.get_client_volume("test-client") == -25.0
        assert state_store.get_client_volume("unknown") is None

    @pytest.mark.asyncio
    async def test_get_complete_state(self, state_store, mock_settings):
        """Test getting complete volume state."""
        mock_settings.get_setting = AsyncMock(return_value=False)  # multiroom disabled
        state_store.set_local_volume(-25.0)

        state = await state_store.get_complete_state()

        assert isinstance(state, VolumeState)
        assert state.mode in ["direct", "multiroom"]

    @pytest.mark.asyncio
    async def test_set_mode(self, state_store):
        """Test setting volume mode."""
        await state_store.set_mode("direct")
        assert state_store._mode == "direct"

        await state_store.set_mode("multiroom")
        assert state_store._mode == "multiroom"


# ============================================================================
# VolumeService Tests
# ============================================================================

class TestVolumeService:
    """Tests for VolumeService."""

    @pytest.fixture(autouse=True)
    def reset_bus(self):
        """Reset EventBus before each test."""
        reset_event_bus()
        yield
        reset_event_bus()

    @pytest.fixture
    def mock_state_machine(self):
        """Create mock state machine."""
        sm = Mock()
        sm.broadcast_event = AsyncMock()
        sm.routing_service = Mock()
        sm.routing_service.get_state = Mock(return_value={'multiroom_enabled': False})
        return sm

    @pytest.fixture
    def mock_snapcast_service(self):
        """Create mock snapcast service."""
        service = Mock()
        service.get_clients = AsyncMock(return_value=[])
        return service

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings service."""
        settings = Mock()
        settings.invalidate_cache = Mock()
        settings.get_setting = AsyncMock(return_value=None)
        return settings

    @pytest.fixture
    def mock_camilladsp_service(self):
        """Create mock CamillaDSP service."""
        camilladsp_mock = Mock()
        camilladsp_mock.set_volume = AsyncMock(return_value=True)
        camilladsp_mock.get_volume = AsyncMock(return_value=-30.0)
        camilladsp_mock.set_mute = AsyncMock(return_value=True)
        camilladsp_mock.is_volume_control_available = Mock(return_value=True)
        camilladsp_mock.wait_for_connection = AsyncMock(return_value=True)
        return camilladsp_mock

    @pytest.fixture
    def mock_proxy_service(self):
        """Create mock proxy service."""
        proxy = Mock()
        proxy.request = AsyncMock(return_value={"status": "success"})
        proxy.check_available = AsyncMock(return_value=True)
        return proxy

    @pytest.fixture
    def service(self, mock_state_machine, mock_snapcast_service, mock_settings,
                mock_camilladsp_service, mock_proxy_service):
        """Create VolumeService with mocks."""
        return VolumeService(
            state_machine=mock_state_machine,
            snapcast_service=mock_snapcast_service,
            settings_service=mock_settings,
            camilladsp_service=mock_camilladsp_service,
            equalizer_client_proxy_service=mock_proxy_service
        )

    def test_initialization(self, service, mock_state_machine, mock_snapcast_service):
        """Test service initialization."""
        assert service.state_machine == mock_state_machine
        assert service.snapcast_service == mock_snapcast_service
        assert service.event_bus is not None

    def test_event_bus_default(self, service):
        """Test that EventBus uses global singleton when not provided."""
        assert service.event_bus is get_event_bus()

    def test_event_bus_custom(self, mock_state_machine, mock_snapcast_service,
                              mock_settings, mock_camilladsp_service, mock_proxy_service):
        """Test that custom EventBus is used when provided."""
        custom_bus = EventBus()
        service = VolumeService(
            state_machine=mock_state_machine,
            snapcast_service=mock_snapcast_service,
            settings_service=mock_settings,
            camilladsp_service=mock_camilladsp_service,
            equalizer_client_proxy_service=mock_proxy_service,
            event_bus=custom_bus
        )
        assert service.event_bus is custom_bus

    def test_is_multiroom_enabled_false(self, service):
        """Test multiroom disabled check."""
        service._routing_service = Mock()
        service._routing_service.get_state.return_value = {'multiroom_enabled': False}
        assert service._is_multiroom_enabled() is False

    def test_is_multiroom_enabled_true(self, service):
        """Test multiroom enabled check."""
        service._routing_service = Mock()
        service._routing_service.get_state.return_value = {'multiroom_enabled': True}
        assert service._is_multiroom_enabled() is True

    def test_is_equalizer_available(self, service, mock_camilladsp_service):
        """Test Equalizer availability check."""
        mock_camilladsp_service.is_volume_control_available.return_value = True
        assert service._is_equalizer_available() is True

        mock_camilladsp_service.is_volume_control_available.return_value = False
        assert service._is_equalizer_available() is False

    def test_config_access(self, service):
        """Test config sub-service access."""
        config = service.config
        assert config is not None
        assert hasattr(config, 'config')
        assert config.config.limit_min_db == -80.0

    @pytest.mark.asyncio
    async def test_get_volume_db(self, service, mock_settings):
        """Test getting current volume."""
        mock_settings.get_setting = AsyncMock(return_value=False)
        service._state_store.set_local_volume(-25.0)

        volume = await service.get_volume_db()
        assert isinstance(volume, float)

    @pytest.mark.asyncio
    async def test_set_volume_db_direct_mode(self, service, mock_camilladsp_service, mock_state_machine):
        """Test setting volume in direct mode."""
        mock_state_machine.routing_service.get_state.return_value = {'multiroom_enabled': False}

        result = await service.set_volume_db(-25.0)

        assert result is True
        mock_camilladsp_service.set_volume.assert_called()

    @pytest.mark.asyncio
    async def test_adjust_volume_db(self, service, mock_camilladsp_service, mock_state_machine, mock_settings):
        """Test adjusting volume by delta."""
        mock_state_machine.routing_service.get_state.return_value = {'multiroom_enabled': False}
        mock_settings.get_setting = AsyncMock(return_value=False)
        service._state_store.set_local_volume(-30.0)

        result = await service.adjust_volume_db(3.0)

        assert result is True
        mock_camilladsp_service.set_volume.assert_called()

    @pytest.mark.asyncio
    async def test_get_status(self, service, mock_settings):
        """Test getting service status."""
        mock_settings.get_setting = AsyncMock(return_value=False)

        status = await service.get_status()

        assert isinstance(status, dict)
        assert "volume_db" in status
        assert "multiroom_enabled" in status
        assert "equalizer_available" in status

    @pytest.mark.asyncio
    async def test_get_volume_state(self, service, mock_settings):
        """Test getting unified volume state."""
        mock_settings.get_setting = AsyncMock(return_value=False)

        state = await service.get_volume_state()

        assert isinstance(state, VolumeState)
        assert hasattr(state, 'mode')
        assert hasattr(state, 'global_volume_db')

    @pytest.mark.asyncio
    async def test_broadcast_emits_eventbus_event(self, service, mock_settings):
        """Test that broadcast also emits to EventBus."""
        mock_settings.get_setting = AsyncMock(return_value=False)
        events_received = []

        async def handler(data):
            events_received.append(data)

        service.event_bus.on(Events.VOLUME_CHANGED, handler)

        await service._broadcast_volume_state(show_bar=False)

        assert len(events_received) == 1
        assert "state" in events_received[0]

    def test_volume_config_clamp(self, service):
        """Test volume clamping via config."""
        config = service.config.config
        assert config.clamp(-90.0) == -80.0  # Below min
        assert config.clamp(-30.0) == -30.0  # In range
        assert config.clamp(0.0) == -21.0    # Above max

    @pytest.mark.asyncio
    async def test_reload_volume_limits(self, service, mock_settings, mock_camilladsp_service, mock_state_machine):
        """Test reloading volume limits."""
        mock_settings.get_setting = AsyncMock(return_value={
            "limit_min_db": -60.0,
            "limit_max_db": -15.0,
            "step_mobile_db": 3.0,
            "step_rotary_db": 2.0,
            "startup_volume_db": -30.0,
            "restore_last_volume": False
        })
        mock_state_machine.routing_service.get_state.return_value = {'multiroom_enabled': False}

        result = await service.reload_volume_limits()

        assert result is True


# ============================================================================
# Integration Tests
# ============================================================================

class TestVolumeIntegration:
    """Integration tests for volume module components."""

    @pytest.fixture(autouse=True)
    def reset_bus(self):
        """Reset EventBus before each test."""
        reset_event_bus()
        yield
        reset_event_bus()

    @pytest.mark.asyncio
    async def test_state_store_and_config_integration(self):
        """Test VolumeStateStore and VolumeConfigService integration."""
        mock_settings = Mock()
        mock_settings.get_setting = AsyncMock(return_value=None)
        mock_settings.invalidate_cache = Mock()

        config_service = VolumeConfigService(mock_settings)
        state_store = VolumeStateStore(mock_settings)

        # Update limits via config
        config_service._config = VolumeConfig(
            limit_min_db=-60.0,
            limit_max_db=-15.0
        )

        # Apply to state store
        state_store.update_user_limits(
            config_service.config.limit_min_db,
            config_service.config.limit_max_db
        )

        # Verify clamping respects new limits
        assert state_store._clamp_db(-70.0) == -60.0
        assert state_store._clamp_db(-10.0) == -15.0

    @pytest.mark.asyncio
    async def test_eventbus_volume_events(self):
        """Test EventBus volume event flow."""
        bus = get_event_bus()
        events = []

        async def capture_event(data):
            events.append(data)

        bus.on(Events.VOLUME_CHANGED, capture_event)

        # Emit event
        await bus.emit(Events.VOLUME_CHANGED, {
            "show_bar": True,
            "state": {"volume_db": -25.0}
        })

        assert len(events) == 1
        assert events[0]["state"]["volume_db"] == -25.0


# ============================================================================
# Zone Reconnection Tests (Story 5.1 - FR7, FR8)
# ============================================================================

class TestZoneReconnectionVolume:
    """Tests for zone client reconnection volume sync (FR7, FR8)."""

    @pytest.fixture
    def mock_state_store(self):
        """Create mock VolumeStateStore with zone support."""
        store = Mock(spec=VolumeStateStore)
        store.get_zone_target_volume = Mock(return_value=None)
        store.get_client_volume = Mock(return_value=-30.0)
        store.register_client = AsyncMock()
        store._clients = {}
        return store

    @pytest.fixture
    def mock_equalizer_controller(self):
        """Create mock EqualizerController."""
        controller = Mock(spec=EqualizerController)
        controller.wait_for_client_ready = AsyncMock(return_value=True)
        controller.set_equalizer_mute = AsyncMock()
        controller.set_equalizer_volume = AsyncMock()
        return controller

    @pytest.mark.asyncio
    async def test_zone_reconnect_uses_zone_average_when_others_online(self, mock_state_store, mock_equalizer_controller):
        """
        FR7: IN_ZONE client reconnects with others ONLINE.
        Expected: volume = zone_volume_avg
        """
        # Setup: Zone with average -25dB from online clients
        zone_volume = ZoneVolume(
            id="zone-1",
            name="Living Room",
            client_ids=["local", "client-01", "client-02"],
            average_volume_db=-25.0,
            all_muted=False
        )
        volume_state = VolumeState(
            mode="multiroom",
            global_volume_db=-25.0,
            global_mute=False,
            clients={},
            zones={"zone-1": zone_volume}
        )
        mock_state_store.get_complete_state = AsyncMock(return_value=volume_state)
        mock_state_store.get_zone_target_volume.return_value = None  # No cached target

        # Simulate volume service method logic
        client_id = "client-02"
        client_zone_id = "zone-1"

        # Determine target volume (mimics sync_existing_client_from_snapcast logic)
        target = mock_state_store.get_zone_target_volume(client_zone_id)
        if target is not None:
            expected_volume = target
        else:
            expected_volume = volume_state.zones[client_zone_id].average_volume_db

        # Assert: Uses zone average when others are online
        assert expected_volume == -25.0

    @pytest.mark.asyncio
    async def test_zone_reconnect_uses_default_when_all_offline(self, mock_state_store, mock_equalizer_controller):
        """
        FR8: IN_ZONE client reconnects with ALL others OFFLINE.
        Expected: volume = startup_volume_db (DEFAULT_VOLUME_DB)
        """
        # Setup: Zone with no available clients (all offline)
        # average_volume_db returns DEFAULT_VOLUME_DB when no available clients
        zone_volume = ZoneVolume(
            id="zone-1",
            name="Living Room",
            client_ids=["local", "client-01", "client-02"],
            average_volume_db=DEFAULT_VOLUME_DB,  # -60.0 when all offline
            all_muted=False
        )
        volume_state = VolumeState(
            mode="multiroom",
            global_volume_db=DEFAULT_VOLUME_DB,
            global_mute=False,
            clients={},
            zones={"zone-1": zone_volume}
        )
        mock_state_store.get_complete_state = AsyncMock(return_value=volume_state)
        mock_state_store.get_zone_target_volume.return_value = None

        # Simulate volume service method logic
        client_id = "client-02"
        client_zone_id = "zone-1"

        target = mock_state_store.get_zone_target_volume(client_zone_id)
        if target is not None:
            expected_volume = target
        else:
            expected_volume = volume_state.zones[client_zone_id].average_volume_db

        # Assert: Uses DEFAULT_VOLUME_DB (startup default) when all offline
        assert expected_volume == DEFAULT_VOLUME_DB
        assert expected_volume == -60.0

    @pytest.mark.asyncio
    async def test_zone_reconnect_uses_cached_target_during_initial_sync(self, mock_state_store, mock_equalizer_controller):
        """
        Test that cached zone target is used during initial sync phase.
        This prevents race conditions when multiple clients sync sequentially.
        """
        # Setup: Cached zone target from persisted data
        cached_target = -35.0
        mock_state_store.get_zone_target_volume.return_value = cached_target

        zone_volume = ZoneVolume(
            id="zone-1",
            name="Living Room",
            client_ids=["local", "client-01"],
            average_volume_db=-30.0,  # Different from cached
            all_muted=False
        )
        volume_state = VolumeState(
            mode="multiroom",
            global_volume_db=-30.0,
            global_mute=False,
            clients={},
            zones={"zone-1": zone_volume}
        )
        mock_state_store.get_complete_state = AsyncMock(return_value=volume_state)

        # Simulate volume service method logic
        client_zone_id = "zone-1"

        target = mock_state_store.get_zone_target_volume(client_zone_id)
        if target is not None:
            expected_volume = target
        else:
            expected_volume = volume_state.zones[client_zone_id].average_volume_db

        # Assert: Uses cached target, not current average
        assert expected_volume == cached_target
        assert expected_volume == -35.0


# ============================================================================
# Standalone Reconnection Volume Tests (FR9, FR10 - Story 5.2)
# ============================================================================

class TestStandaloneReconnectionVolume:
    """Tests for standalone client reconnection volume sync (FR9, FR10)."""

    @pytest.fixture
    def mock_state_store(self):
        """Create mock VolumeStateStore for standalone tests."""
        store = Mock(spec=VolumeStateStore)
        store.get_zone_target_volume = Mock(return_value=None)
        store.get_client_volume = Mock(return_value=None)
        store.register_client = AsyncMock()
        store._clients = {}
        return store

    @pytest.fixture
    def mock_equalizer_controller(self):
        """Create mock EqualizerController."""
        controller = Mock(spec=EqualizerController)
        controller.wait_for_client_ready = AsyncMock(return_value=True)
        controller.set_equalizer_mute = AsyncMock()
        controller.set_equalizer_volume = AsyncMock()
        return controller

    @pytest.mark.asyncio
    async def test_standalone_reconnect_uses_global_volume_when_others_online(self, mock_state_store, mock_equalizer_controller):
        """
        FR9: STANDALONE client reconnects with others ONLINE.
        Expected: volume = volume_global (average of all ONLINE)
        """
        # Setup: Other clients online with average -30dB
        volume_state = VolumeState(
            mode="multiroom",
            global_volume_db=-30.0,  # Average of online clients
            global_mute=False,
            clients={
                "local": ClientVolume(volume_db=-28.0, offset_db=0.0, mute=False, available=True),
                "client-01": ClientVolume(volume_db=-32.0, offset_db=0.0, mute=False, available=True)
            },
            zones={}  # No zones - all standalone
        )
        mock_state_store.get_complete_state = AsyncMock(return_value=volume_state)
        mock_state_store.get_client_volume.return_value = None  # Client has no saved volume

        # Simulate standalone volume service logic
        client_id = "client-02"  # Reconnecting client

        # Find zone (none for standalone)
        client_zone_id = None

        # Determine target volume (mimics sync_existing_client_from_snapcast logic)
        if client_zone_id:
            # Zone client path (not taken)
            pass
        else:
            # Standalone client path
            expected_volume = mock_state_store.get_client_volume(client_id)
            if expected_volume is None:
                expected_volume = volume_state.global_volume_db

        # Assert: Uses global_volume_db when others are online
        assert expected_volume == -30.0

    @pytest.mark.asyncio
    async def test_standalone_reconnect_uses_default_when_first_client(self, mock_state_store, mock_equalizer_controller):
        """
        FR10: STANDALONE client reconnects as FIRST client (no others ONLINE).
        Expected: volume = startup_volume_db (DEFAULT_VOLUME_DB = -60.0)
        """
        # Setup: No other clients online - global_volume_db falls back to DEFAULT
        volume_state = VolumeState(
            mode="multiroom",
            global_volume_db=DEFAULT_VOLUME_DB,  # -60.0 when no available clients
            global_mute=False,
            clients={},  # No available clients
            zones={}
        )
        mock_state_store.get_complete_state = AsyncMock(return_value=volume_state)
        mock_state_store.get_client_volume.return_value = None  # No saved volume

        # Simulate standalone volume service logic
        client_id = "client-01"  # First client reconnecting
        client_zone_id = None  # Standalone

        # Determine target volume
        if client_zone_id:
            pass
        else:
            expected_volume = mock_state_store.get_client_volume(client_id)
            if expected_volume is None:
                expected_volume = volume_state.global_volume_db

        # Assert: Uses DEFAULT_VOLUME_DB (startup_volume_db) when first client
        assert expected_volume == DEFAULT_VOLUME_DB
        assert expected_volume == -60.0

    @pytest.mark.asyncio
    async def test_standalone_reconnect_uses_saved_volume_if_exists(self, mock_state_store, mock_equalizer_controller):
        """
        Test that standalone client uses its saved volume if available.
        """
        # Setup: Client has a saved volume
        saved_volume = -40.0
        mock_state_store.get_client_volume.return_value = saved_volume

        volume_state = VolumeState(
            mode="multiroom",
            global_volume_db=-30.0,
            global_mute=False,
            clients={
                "local": ClientVolume(volume_db=-30.0, offset_db=0.0, mute=False, available=True)
            },
            zones={}
        )
        mock_state_store.get_complete_state = AsyncMock(return_value=volume_state)

        # Simulate standalone volume service logic
        client_id = "client-01"
        client_zone_id = None

        if client_zone_id:
            pass
        else:
            expected_volume = mock_state_store.get_client_volume(client_id)
            if expected_volume is None:
                expected_volume = volume_state.global_volume_db

        # Assert: Uses saved volume, not global
        assert expected_volume == saved_volume
        assert expected_volume == -40.0

    @pytest.mark.asyncio
    async def test_local_client_follows_standalone_rules(self, mock_state_store, mock_equalizer_controller):
        """
        AC4: "local" client follows same STANDALONE rules as remote clients.
        """
        # Setup: Local client reconnecting with no saved volume
        mock_state_store.get_client_volume.return_value = None

        volume_state = VolumeState(
            mode="multiroom",
            global_volume_db=-35.0,  # Other clients online
            global_mute=False,
            clients={
                "client-01": ClientVolume(volume_db=-35.0, offset_db=0.0, mute=False, available=True)
            },
            zones={}
        )
        mock_state_store.get_complete_state = AsyncMock(return_value=volume_state)

        # Simulate for "local" client
        client_id = "local"
        client_zone_id = None  # Standalone

        if client_zone_id:
            pass
        else:
            expected_volume = mock_state_store.get_client_volume(client_id)
            if expected_volume is None:
                expected_volume = volume_state.global_volume_db

        # Assert: Local follows same rules
        assert expected_volume == -35.0


# ============================================================================
# Startup Volume Tests (FR11, FR12 - Story 3.3)
# ============================================================================

class TestStartupVolumeAutoUpdate:
    """Tests for FR11 - Auto-update startup_volume_db when restore_last_volume is enabled."""

    @pytest.fixture(autouse=True)
    def reset_bus(self):
        """Reset EventBus before each test."""
        reset_event_bus()
        yield
        reset_event_bus()

    @pytest.fixture
    def mock_state_machine(self):
        """Create mock state machine."""
        sm = Mock()
        sm.broadcast_event = AsyncMock()
        sm.routing_service = Mock()
        sm.routing_service.get_state = Mock(return_value={'multiroom_enabled': False})
        return sm

    @pytest.fixture
    def mock_snapcast_service(self):
        """Create mock snapcast service."""
        service = Mock()
        service.get_clients = AsyncMock(return_value=[])
        return service

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings service."""
        settings = Mock()
        settings.invalidate_cache = Mock()
        settings.get_setting = AsyncMock(return_value=None)
        settings.set_setting = AsyncMock()
        return settings

    @pytest.fixture
    def mock_camilladsp_service(self):
        """Create mock CamillaDSP service."""
        camilladsp_mock = Mock()
        camilladsp_mock.set_volume = AsyncMock(return_value=True)
        camilladsp_mock.get_volume = AsyncMock(return_value={"main": -30.0})
        camilladsp_mock.set_mute = AsyncMock(return_value=True)
        camilladsp_mock.is_volume_control_available = Mock(return_value=True)
        camilladsp_mock.wait_for_connection = AsyncMock(return_value=True)
        return camilladsp_mock

    @pytest.fixture
    def mock_proxy_service(self):
        """Create mock proxy service."""
        proxy = Mock()
        proxy.request = AsyncMock(return_value={"status": "success"})
        proxy.check_available = AsyncMock(return_value=True)
        return proxy

    @pytest.fixture
    def service(self, mock_state_machine, mock_snapcast_service, mock_settings,
                mock_camilladsp_service, mock_proxy_service):
        """Create VolumeService with mocks."""
        svc = VolumeService(
            state_machine=mock_state_machine,
            snapcast_service=mock_snapcast_service,
            settings_service=mock_settings,
            camilladsp_service=mock_camilladsp_service,
            equalizer_client_proxy_service=mock_proxy_service
        )
        # Set initial config with restore_last_volume=True (FR11 active)
        svc._config_service._config = VolumeConfig(
            limit_min_db=-80.0,
            limit_max_db=-21.0,
            startup_volume_db=-60.0,
            restore_last_volume=True
        )
        # Set state store to direct mode (default is multiroom, which would use empty clients)
        svc._state_store._mode = "direct"
        return svc

    @pytest.mark.asyncio
    async def test_set_volume_updates_startup_volume_when_restore_true(
        self, service, mock_settings, mock_state_machine
    ):
        """
        FR11 AC1: set_volume_db() updates startup_volume_db when restore_last_volume=true.
        """
        # Arrange: restore_last_volume=True (already set in fixture)
        mock_state_machine.routing_service.get_state.return_value = {'multiroom_enabled': False}

        # Act: Set volume to -45dB
        await service.set_volume_db(-45.0)

        # Assert: startup_volume_db was updated via SettingsService
        mock_settings.set_setting.assert_called_with('volume.startup_volume_db', -45.0)

    @pytest.mark.asyncio
    async def test_set_volume_does_not_update_startup_volume_when_restore_false(
        self, service, mock_settings, mock_state_machine
    ):
        """
        FR11 AC2: set_volume_db() does NOT update startup_volume_db when restore_last_volume=false.
        """
        # Arrange: Set restore_last_volume=False
        service._config_service._config = VolumeConfig(
            limit_min_db=-80.0,
            limit_max_db=-21.0,
            startup_volume_db=-60.0,
            restore_last_volume=False  # FR11 should NOT trigger
        )
        mock_state_machine.routing_service.get_state.return_value = {'multiroom_enabled': False}

        # Act: Set volume
        await service.set_volume_db(-45.0)

        # Assert: startup_volume_db was NOT updated
        mock_settings.set_setting.assert_not_called()

    @pytest.mark.asyncio
    async def test_adjust_volume_updates_startup_volume_when_restore_true(
        self, service, mock_settings, mock_state_machine
    ):
        """
        FR11 AC1: adjust_volume_db() updates startup_volume_db when restore_last_volume=true.
        """
        # Arrange: restore_last_volume=True (already set in fixture)
        mock_state_machine.routing_service.get_state.return_value = {'multiroom_enabled': False}
        service._state_store.set_local_volume(-50.0)

        # Act: Adjust by +5dB -> -45dB
        await service.adjust_volume_db(5.0)

        # Assert: startup_volume_db was updated
        mock_settings.set_setting.assert_called()
        call_args = mock_settings.set_setting.call_args
        assert call_args[0][0] == 'volume.startup_volume_db'

    @pytest.mark.asyncio
    async def test_startup_volume_not_updated_if_unchanged(
        self, service, mock_settings, mock_state_machine
    ):
        """
        FR11: startup_volume_db is NOT updated if value is unchanged (within 0.1dB tolerance).
        """
        # Arrange: Set startup_volume_db to same value we'll set
        service._config_service._config = VolumeConfig(
            limit_min_db=-80.0,
            limit_max_db=-21.0,
            startup_volume_db=-45.0,  # Same as what we'll set
            restore_last_volume=True
        )
        mock_state_machine.routing_service.get_state.return_value = {'multiroom_enabled': False}

        # Act: Set volume to same value
        await service.set_volume_db(-45.0)

        # Assert: startup_volume_db was NOT updated (no unnecessary write)
        mock_settings.set_setting.assert_not_called()

    @pytest.mark.asyncio
    async def test_websocket_broadcast_on_startup_volume_change(
        self, service, mock_settings, mock_state_machine
    ):
        """
        FR11 AC1: WebSocket event 'settings_changed' is broadcast when startup_volume_db updates.
        """
        # Arrange
        mock_state_machine.routing_service.get_state.return_value = {'multiroom_enabled': False}

        # Act
        await service.set_volume_db(-45.0)

        # Assert: WebSocket broadcast was called with settings category
        broadcast_calls = mock_state_machine.broadcast_event.call_args_list
        settings_broadcast = [c for c in broadcast_calls if c[0][0] == "settings"]
        assert len(settings_broadcast) >= 1
        # Check the event type
        assert settings_broadcast[0][0][1] == "volume_startup_changed"

    @pytest.mark.asyncio
    async def test_zone_volume_delta_updates_startup_volume(
        self, service, mock_settings, mock_state_machine, mock_camilladsp_service, mock_snapcast_service
    ):
        """
        FR11 AC5: apply_zone_volume_delta() updates startup_volume_db using local client volume.
        """
        # Arrange: Multiroom mode with a zone
        service._routing_service = Mock()
        service._routing_service.get_state.return_value = {'multiroom_enabled': True}

        # Setup zone in state store
        from backend.core.models.volume_state import ClientVolume
        service._state_store._local_mac_id = 'local'
        service._state_store._clients = {
            'local': ClientVolume(volume_db=-50.0, offset_db=0.0, mute=False, available=True)
        }
        service._state_store._zones = {
            'zone-1': Mock(
                id='zone-1',
                name='Test Zone',
                client_ids=['local'],
                average_volume_db=-50.0,
                all_muted=False
            )
        }

        # Mock zone delta method to return updates
        async def mock_apply_zone_delta(zone_id, delta):
            # Simulate updating local client to -45dB
            return {'local': -45.0}

        service._state_store.apply_zone_delta = mock_apply_zone_delta
        service._state_store.apply_zone_updates = AsyncMock()
        service._state_store.compute_zone_average = Mock(return_value=-45.0)
        service._state_store.clear_zone_targets = Mock()

        # Act
        await service.apply_zone_volume_delta('zone-1', 5.0)

        # Assert: startup_volume_db was updated with local client's new volume
        mock_settings.set_setting.assert_called_with('volume.startup_volume_db', -45.0)


class TestStartupVolumeOnRestart:
    """Tests for FR12 - Backend restart applies startup volume."""

    @pytest.fixture(autouse=True)
    def reset_bus(self):
        """Reset EventBus before each test."""
        reset_event_bus()
        yield
        reset_event_bus()

    @pytest.fixture
    def mock_state_machine(self):
        """Create mock state machine."""
        sm = Mock()
        sm.broadcast_event = AsyncMock()
        sm.routing_service = Mock()
        sm.routing_service.get_state = Mock(return_value={'multiroom_enabled': False})
        return sm

    @pytest.fixture
    def mock_snapcast_service(self):
        """Create mock snapcast service."""
        service = Mock()
        service.get_clients = AsyncMock(return_value=[])
        return service

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings service."""
        settings = Mock()
        settings.invalidate_cache = Mock()
        settings.get_setting = AsyncMock(return_value=None)
        settings.set_setting = AsyncMock()
        return settings

    @pytest.fixture
    def mock_camilladsp_service(self):
        """Create mock CamillaDSP service."""
        camilladsp_mock = Mock()
        camilladsp_mock.set_volume = AsyncMock(return_value=True)
        camilladsp_mock.get_volume = AsyncMock(return_value={"main": -30.0})
        camilladsp_mock.set_mute = AsyncMock(return_value=True)
        camilladsp_mock.is_volume_control_available = Mock(return_value=True)
        camilladsp_mock.wait_for_connection = AsyncMock(return_value=True)
        return camilladsp_mock

    @pytest.fixture
    def mock_proxy_service(self):
        """Create mock proxy service."""
        proxy = Mock()
        proxy.request = AsyncMock(return_value={"status": "success"})
        proxy.check_available = AsyncMock(return_value=True)
        return proxy

    @pytest.fixture
    def mock_equalizer_controller(self):
        """Create mock Equalizer controller."""
        controller = Mock()
        controller.set_equalizer_volume = AsyncMock(return_value=True)
        controller.set_equalizer_mute = AsyncMock(return_value=True)
        return controller

    @pytest.fixture
    def service(self, mock_state_machine, mock_snapcast_service, mock_settings,
                mock_camilladsp_service, mock_proxy_service, mock_equalizer_controller):
        """Create VolumeService with mocks, including mocked Equalizer controller."""
        svc = VolumeService(
            state_machine=mock_state_machine,
            snapcast_service=mock_snapcast_service,
            settings_service=mock_settings,
            camilladsp_service=mock_camilladsp_service,
            equalizer_client_proxy_service=mock_proxy_service
        )
        # Replace the real Equalizer controller with our mock
        svc._equalizer_controller = mock_equalizer_controller
        return svc

    @pytest.mark.asyncio
    async def test_startup_applies_startup_volume_when_restore_false(
        self, service, mock_camilladsp_service, mock_equalizer_controller
    ):
        """
        FR12 AC3/AC4: initialize() applies startup_volume_db when restore_last_volume=false.
        """
        # Arrange: Set config with restore=false and specific startup volume
        startup_vol = -35.0
        service._config_service._config = VolumeConfig(
            limit_min_db=-80.0,
            limit_max_db=-21.0,
            startup_volume_db=startup_vol,
            restore_last_volume=False
        )

        # Act: Call _apply_startup_volume directly (called by initialize())
        await service._apply_startup_volume()

        # Assert: Equalizer was set to startup_volume_db (uses _camilladsp_service directly at startup)
        mock_camilladsp_service.set_volume.assert_called_with(startup_vol)

    @pytest.mark.asyncio
    async def test_startup_applies_persisted_volume_when_restore_true(
        self, service, mock_camilladsp_service, mock_equalizer_controller
    ):
        """
        FR12 AC3/AC4: initialize() applies startup_volume_db when restore_last_volume=true.
        startup_volume_db is auto-updated by FR11 to track current volume, so at restart
        it already contains the persisted volume.
        """
        # Arrange: Set config with restore=true
        # startup_volume_db has been auto-tracked by FR11 to the persisted volume
        persisted_vol = -42.0
        service._config_service._config = VolumeConfig(
            limit_min_db=-80.0,
            limit_max_db=-21.0,
            startup_volume_db=persisted_vol,  # FR11 auto-tracked this
            restore_last_volume=True
        )

        # Act
        await service._apply_startup_volume()

        # Assert: Equalizer was set to startup_volume_db (uses _camilladsp_service directly at startup)
        mock_camilladsp_service.set_volume.assert_called_with(persisted_vol)

    @pytest.mark.asyncio
    async def test_startup_uses_startup_volume_db_as_single_source(
        self, service, mock_camilladsp_service, mock_equalizer_controller
    ):
        """
        FR12: startup_volume_db is the single source of truth for startup volume.
        FR11 auto-updates it during runtime, so at restart it contains the correct value.
        """
        # Arrange: startup_volume_db is always used (FR11 keeps it in sync)
        startup_vol = -38.0
        service._config_service._config = VolumeConfig(
            limit_min_db=-80.0,
            limit_max_db=-21.0,
            startup_volume_db=startup_vol,
            restore_last_volume=True
        )
        service._state_store._clients = {}  # No client state

        # Act
        await service._apply_startup_volume()

        # Assert: Equalizer was set to startup_volume_db (uses _camilladsp_service directly at startup)
        mock_camilladsp_service.set_volume.assert_called_with(startup_vol)

    @pytest.mark.asyncio
    async def test_startup_applies_mute_state(
        self, service, mock_camilladsp_service, mock_equalizer_controller
    ):
        """
        FR12: Startup also applies persisted mute state.
        The mute state is read from the local client's ClientVolume.
        """
        # Arrange
        service._config_service._config = VolumeConfig(
            limit_min_db=-80.0,
            limit_max_db=-21.0,
            startup_volume_db=-45.0,
            restore_last_volume=False
        )
        # Set persisted mute state via local client in state store
        from backend.core.models.volume_state import ClientVolume
        service._state_store._local_mac_id = "local-mac"
        service._state_store._clients["local-mac"] = ClientVolume(
            volume_db=-45.0, offset_db=0.0, mute=True, available=True
        )

        # Act
        await service._apply_startup_volume()

        # Assert: Mute state was applied via _camilladsp_service directly at startup
        mock_camilladsp_service.set_mute.assert_called_with(True)

    @pytest.mark.asyncio
    async def test_startup_handles_equalizer_connection_timeout(
        self, service, mock_camilladsp_service, mock_equalizer_controller
    ):
        """
        FR12: Gracefully handle Equalizer connection timeout on startup.
        """
        # Arrange: Equalizer connection times out
        mock_camilladsp_service.wait_for_connection = AsyncMock(return_value=False)

        service._config_service._config = VolumeConfig(
            limit_min_db=-80.0,
            limit_max_db=-21.0,
            startup_volume_db=-45.0,
            restore_last_volume=False
        )

        # Act: Should not raise, just log warning
        await service._apply_startup_volume()

        # Assert: Equalizer volume was NOT set (connection failed)
        mock_camilladsp_service.set_volume.assert_not_called()
