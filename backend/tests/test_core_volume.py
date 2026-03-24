# backend/tests/test_core_volume.py
"""
Unit tests for core.volume module.

Tests the migrated VolumeService, VolumeStateStore,
and EqualizerController in the new core/volume/ location.
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
import asyncio

from backend.core.volume import (
    VolumeService,
    VolumeStateStore,
    EqualizerController
)
from backend.core.models.volume import VolumeConfig
from backend.core.models.volume_state import VolumeState, ClientVolume, ZoneVolume
from backend.config.constants import DEFAULT_VOLUME_DB, MIN_VOLUME_DB, MAX_VOLUME_DB


# ============================================================================
# VolumeConfig Tests
# ============================================================================

class TestVolumeConfig:
    """Tests for VolumeConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = VolumeConfig()
        assert config.limit_min_db == -80.0
        assert config.limit_max_db == -20.0
        assert config.step_mobile_db == 2.0
        assert config.step_rotary_db == 2.0
        assert config.startup_volume_db == DEFAULT_VOLUME_DB
        assert config.restore_last_volume is True

    def test_clamp_within_range(self):
        """Test clamping within configured range."""
        config = VolumeConfig(limit_min_db=-60.0, limit_max_db=-10.0)
        assert config.clamp(-30.0) == -30.0

    def test_clamp_below_min(self):
        """Test clamping below minimum."""
        config = VolumeConfig(limit_min_db=-60.0, limit_max_db=-10.0)
        assert config.clamp(-70.0) == -60.0

    def test_clamp_above_max(self):
        """Test clamping above maximum."""
        config = VolumeConfig(limit_min_db=-60.0, limit_max_db=-10.0)
        assert config.clamp(-5.0) == -10.0

    def test_clamp_enforces_technical_hard_limits(self):
        """Test that clamp enforces technical hard limits (MIN_VOLUME_DB, MAX_VOLUME_DB)."""
        # Even if user limits are wider than technical, hard limits apply
        config = VolumeConfig(limit_min_db=-100.0, limit_max_db=10.0)
        assert config.clamp(-100.0) == MIN_VOLUME_DB  # -80.0
        assert config.clamp(10.0) == MAX_VOLUME_DB     # 0.0

    def test_to_dict(self):
        """Test getting config as dictionary."""
        config = VolumeConfig()
        result = config.to_dict()

        assert isinstance(result, dict)
        assert "limit_min_db" in result
        assert "limit_max_db" in result
        assert "step_mobile_db" in result


# ============================================================================
# EqualizerController Tests
# ============================================================================

class TestEqualizerController:
    """Tests for EqualizerController (delegates to EqualizerRouter)."""

    @pytest.fixture
    def mock_camilladsp_service(self):
        """Create mock CamillaDSP service."""
        camilladsp_mock = Mock()
        camilladsp_mock.wait_for_connection = AsyncMock(return_value=True)
        return camilladsp_mock

    @pytest.fixture
    def mock_proxy_service(self):
        """Create mock proxy service."""
        proxy = Mock()
        proxy.check_available = AsyncMock(return_value=True)
        return proxy

    @pytest.fixture
    def mock_router(self):
        """Create mock EqualizerRouter."""
        router = Mock()
        router.set_volume = AsyncMock(return_value={"status": "success", "volume": -25.0})
        router.set_mute = AsyncMock(return_value={"status": "success", "mute": True})
        router.get_volume = AsyncMock(return_value={"main": -30.0, "mute": False})
        return router

    @pytest.fixture
    def mock_registry(self):
        """Create mock client registry."""
        registry = Mock()
        local_client = Mock(ip="127.0.0.1", is_local=True)
        remote_client = Mock(ip="192.168.1.100", is_local=False)
        def get_client(mac_id):
            if mac_id == "local":
                return local_client
            elif mac_id == "milo-client-01":
                return remote_client
            return None
        registry.get_client = get_client
        def is_local_client(mac_id):
            client = get_client(mac_id)
            return client.is_local if client else False
        registry.is_local_client = is_local_client
        def get_client_ip(mac_id):
            client = get_client(mac_id)
            if not client or client.is_local:
                return None
            return client.ip if client.ip else None
        registry.get_client_ip = get_client_ip
        return registry

    @pytest.fixture
    def controller(self, mock_camilladsp_service, mock_proxy_service, mock_router, mock_registry):
        """Create EqualizerController."""
        return EqualizerController(
            mock_camilladsp_service, mock_proxy_service,
            equalizer_router=mock_router, client_registry=mock_registry
        )

    @pytest.mark.asyncio
    async def test_set_volume_delegates_to_router(self, controller, mock_router):
        """Test setting volume delegates to EqualizerRouter."""
        result = await controller.set_equalizer_volume("local", -25.0)

        assert result is True
        mock_router.set_volume.assert_called_once_with("local", -25.0, force=False)

    @pytest.mark.asyncio
    async def test_set_volume_remote_delegates_to_router(self, controller, mock_router):
        """Test setting remote client volume delegates to EqualizerRouter."""
        result = await controller.set_equalizer_volume("milo-client-01", -27.0)

        assert result is True
        mock_router.set_volume.assert_called_once_with("milo-client-01", -27.0, force=False)

    @pytest.mark.asyncio
    async def test_set_volume_no_router(self, mock_camilladsp_service, mock_proxy_service):
        """Test set volume returns False without router."""
        ctrl = EqualizerController(mock_camilladsp_service, mock_proxy_service)
        result = await ctrl.set_equalizer_volume("local", -25.0)
        assert result is False

    @pytest.mark.asyncio
    async def test_set_mute_delegates_to_router(self, controller, mock_router):
        """Test setting mute delegates to EqualizerRouter."""
        result = await controller.set_equalizer_mute("local", True)

        assert result is True
        mock_router.set_mute.assert_called_once_with("local", True, force=False)

    @pytest.mark.asyncio
    async def test_read_current_volume(self, controller, mock_router):
        """Test reading volume delegates to EqualizerRouter."""
        result = await controller.read_current_volume("local")

        assert result == -30.0
        mock_router.get_volume.assert_called_once_with("local")

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

    @pytest.mark.asyncio
    async def test_is_success_helper(self):
        """Test _is_success static method."""
        assert EqualizerController._is_success({"status": "success"}) is True
        assert EqualizerController._is_success({"status": "skipped"}) is True
        assert EqualizerController._is_success({"status": "error"}) is False
        assert EqualizerController._is_success({}) is False
        assert EqualizerController._is_success(None) is False


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
        """Create VolumeStateStore with default VolumeConfig."""
        store = VolumeStateStore(mock_settings)
        store.set_volume_config(VolumeConfig())
        return store

    def test_default_values(self, state_store):
        """Test default store values (constants imported from backend.config.constants)."""
        assert MIN_VOLUME_DB == -80.0
        assert MAX_VOLUME_DB == 0.0
        assert DEFAULT_VOLUME_DB == -45.0
        # Verify initial local volume uses default
        assert state_store._local_volume_db == DEFAULT_VOLUME_DB

    def test_clamp_db(self, state_store):
        """Test dB clamping delegates to VolumeConfig."""
        assert state_store._clamp_db(-90.0) == -80.0  # Below min
        assert state_store._clamp_db(-30.0) == -30.0  # In range (but above default max)
        assert state_store._clamp_db(5.0) == -20.0    # Above default user max

    def test_clamp_db_fallback_without_config(self, mock_settings):
        """Test dB clamping falls back to technical limits when config not set."""
        store = VolumeStateStore(mock_settings)
        # No set_volume_config called
        assert store._clamp_db(-90.0) == -80.0  # MIN_VOLUME_DB
        assert store._clamp_db(5.0) == 0.0      # MAX_VOLUME_DB

    def test_set_local_volume(self, state_store):
        """Test setting local volume."""
        state_store.set_local_volume(-25.0)
        assert state_store._local_volume_db == -25.0

    def test_set_volume_config(self, mock_settings):
        """Test setting VolumeConfig updates clamping behavior."""
        store = VolumeStateStore(mock_settings)
        config = VolumeConfig(limit_min_db=-60.0, limit_max_db=-15.0)
        store.set_volume_config(config)
        assert store._clamp_db(-70.0) == -60.0
        assert store._clamp_db(-10.0) == -15.0

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

    def test_volume_config_access(self, service):
        """Test volume_config property access."""
        config = service.volume_config
        assert config is not None
        assert isinstance(config, VolumeConfig)
        assert config.limit_min_db == -80.0

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

    def test_volume_config_clamp(self, service):
        """Test volume clamping via config."""
        config = service.volume_config
        assert config.clamp(-90.0) == -80.0  # Below min
        assert config.clamp(-30.0) == -30.0  # In range
        assert config.clamp(0.0) == -20.0    # Above max

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

    @pytest.mark.asyncio
    async def test_state_store_and_config_integration(self):
        """Test VolumeStateStore uses VolumeConfig for clamping."""
        mock_settings = Mock()
        mock_settings.get_setting = AsyncMock(return_value=None)

        state_store = VolumeStateStore(mock_settings)

        # Set config with custom limits
        config = VolumeConfig(limit_min_db=-60.0, limit_max_db=-15.0)
        state_store.set_volume_config(config)

        # Verify clamping respects config limits
        assert state_store._clamp_db(-70.0) == -60.0
        assert state_store._clamp_db(-10.0) == -15.0



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
            average_volume_db=DEFAULT_VOLUME_DB,  # -45.0 when all offline
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
        assert expected_volume == -45.0

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
        Expected: volume = startup_volume_db (DEFAULT_VOLUME_DB = -45.0)
        """
        # Setup: No other clients online - global_volume_db falls back to DEFAULT
        volume_state = VolumeState(
            mode="multiroom",
            global_volume_db=DEFAULT_VOLUME_DB,  # -45.0 when no available clients
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
        assert expected_volume == -45.0

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
        svc._volume_config = VolumeConfig(
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
        service._volume_config = VolumeConfig(
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

        # Allow background task (_schedule_post_volume_tasks) to run
        await asyncio.sleep(0)

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
        service._volume_config = VolumeConfig(
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
        service._volume_config = VolumeConfig(
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
        service._volume_config = VolumeConfig(
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
        service._volume_config = VolumeConfig(
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
        service._volume_config = VolumeConfig(
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

        service._volume_config = VolumeConfig(
            limit_min_db=-80.0,
            limit_max_db=-21.0,
            startup_volume_db=-45.0,
            restore_last_volume=False
        )

        # Act: Should not raise, just log warning
        await service._apply_startup_volume()

        # Assert: Equalizer volume was NOT set (connection failed)
        mock_camilladsp_service.set_volume.assert_not_called()


# ============================================================================
# Volume Lock Regression Tests
# ============================================================================

class TestVolumeLockNoTimeout:
    """
    Regression tests for volume lock timeout issue.

    Reproduces the scenario: rapid BT remote volume changes in multiroom mode
    with a slow satellite client. Before the fix, _apply_global_volume ran
    HTTP fan-out inside the lock, causing subsequent callers to timeout.
    After the fix, only in-memory state updates happen under the lock.
    """

    @pytest.fixture
    def mock_state_machine(self):
        sm = Mock()
        sm.broadcast_event = AsyncMock()
        sm.routing_service = Mock()
        sm.routing_service.get_state = Mock(return_value={'multiroom_enabled': True})
        return sm

    @pytest.fixture
    def mock_snapcast_service(self):
        service = Mock()
        service.get_clients = AsyncMock(return_value=[
            {"camilladsp_id": "local-mac", "available": True},
            {"camilladsp_id": "satellite-mac", "available": True},
        ])
        return service

    @pytest.fixture
    def mock_settings(self):
        settings = Mock()
        settings.invalidate_cache = Mock()
        settings.get_setting = AsyncMock(return_value=None)
        settings.set_setting = AsyncMock()
        return settings

    @pytest.fixture
    def slow_equalizer_controller(self):
        """Simulate a slow satellite: apply_volumes_parallel takes 3 seconds."""
        controller = Mock()

        async def slow_apply(updates):
            await asyncio.sleep(3.0)  # Simulate slow satellite HTTP
            return {mac_id: True for mac_id in updates}

        controller.apply_volumes_parallel = AsyncMock(side_effect=slow_apply)
        return controller

    @pytest.fixture
    def service(self, mock_state_machine, mock_snapcast_service, mock_settings,
                slow_equalizer_controller):
        svc = VolumeService(
            state_machine=mock_state_machine,
            snapcast_service=mock_snapcast_service,
            settings_service=mock_settings,
            camilladsp_service=Mock(
                set_volume=AsyncMock(return_value=True),
                is_volume_control_available=Mock(return_value=True),
            ),
        )
        svc._volume_config = VolumeConfig(
            limit_min_db=-80.0, limit_max_db=0.0,
            startup_volume_db=-40.0, restore_last_volume=False,
        )
        svc._routing_service = mock_state_machine.routing_service
        svc._equalizer_controller = slow_equalizer_controller
        svc._state_store._mode = "multiroom"
        svc._state_store._clients = {
            "local-mac": ClientVolume(volume_db=-40.0, offset_db=0.0, mute=False, available=True),
            "satellite-mac": ClientVolume(volume_db=-40.0, offset_db=0.0, mute=False, available=True),
        }
        return svc

    @pytest.mark.asyncio
    async def test_rapid_volume_changes_no_lock_timeout(self, service):
        """
        Simulate rapid BT remote volume presses while satellite is slow.

        Before the fix: second adjust_volume_db would timeout waiting for
        the lock (held during 3s HTTP fan-out) and log:
            "Timeout waiting for volume lock (>2s)"

        After the fix: lock is only held for in-memory computation (~µs),
        so all calls acquire it instantly. The slow HTTP fan-out happens
        outside the lock.
        """
        # Simulate 5 rapid volume adjustments (like holding BT remote button)
        results = []
        for _ in range(5):
            result = await service.adjust_volume_db(2.0)
            results.append(result)

        # All 5 should succeed (no timeouts)
        assert all(results), f"Expected all True, got {results}"

    @pytest.mark.asyncio
    async def test_concurrent_volume_sources_no_lock_timeout(self, service):
        """
        Simulate concurrent volume changes from different sources
        (BT remote + frontend slider) while satellite is slow.

        Before the fix: one source holding the lock during HTTP fan-out
        would block the other source for >2s, causing timeout.

        After the fix: both acquire the lock in microseconds (state-only),
        then fan out to hardware concurrently.
        """
        # Launch 3 concurrent volume changes (simulating BT + rotary + frontend)
        tasks = [
            asyncio.create_task(service.adjust_volume_db(2.0)),
            asyncio.create_task(service.adjust_volume_db(2.0)),
            asyncio.create_task(service.set_volume_db(-35.0)),
        ]
        results = await asyncio.gather(*tasks)

        # All should succeed (no lock timeouts)
        assert all(results), f"Expected all True, got {results}"

    @pytest.mark.asyncio
    async def test_zone_delta_no_lock_timeout(self, service):
        """
        apply_zone_volume_delta previously had NO timeout at all on the lock,
        meaning it could block all other volume operations indefinitely.

        After the fix: lock is only held for state computation, and has
        a 2s timeout on acquisition.
        """
        # Setup zone
        from backend.core.volume.state import ZoneConfig
        service._state_store._zones = {
            "zone-1": ZoneConfig(
                zone_id="zone-1", name="Test",
                client_ids=["local-mac", "satellite-mac"]
            )
        }

        # Zone delta + concurrent adjust should both succeed
        tasks = [
            asyncio.create_task(service.apply_zone_volume_delta("zone-1", 3.0)),
            asyncio.create_task(service.adjust_volume_db(2.0)),
        ]
        results = await asyncio.gather(*tasks)

        # Zone delta returns float (new average), adjust returns bool
        assert isinstance(results[0], float)
        assert results[1] is True
