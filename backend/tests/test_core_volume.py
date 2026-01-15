# backend/tests/test_core_volume.py
"""
Unit tests for core.volume module.

Tests the migrated VolumeService, VolumeStateStore, VolumeConfigService,
and DSPController in the new core/volume/ location.
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
import asyncio

from backend.core.volume import (
    VolumeService,
    VolumeStateStore,
    VolumeConfigService,
    DSPController
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
# DSPController Tests
# ============================================================================

class TestDSPController:
    """Tests for DSPController."""

    @pytest.fixture
    def mock_dsp_service(self):
        """Create mock CamillaDSP service."""
        dsp = Mock()
        dsp.set_volume = AsyncMock(return_value=True)
        dsp.get_volume = AsyncMock(return_value=-30.0)
        dsp.set_mute = AsyncMock(return_value=True)
        dsp.wait_for_connection = AsyncMock(return_value=True)
        return dsp

    @pytest.fixture
    def mock_proxy_service(self):
        """Create mock proxy service."""
        proxy = Mock()
        proxy.request = AsyncMock(return_value={"status": "success"})
        proxy.check_available = AsyncMock(return_value=True)
        return proxy

    @pytest.fixture
    def controller(self, mock_dsp_service, mock_proxy_service):
        """Create DSPController."""
        return DSPController(mock_dsp_service, mock_proxy_service)

    @pytest.mark.asyncio
    async def test_set_local_volume(self, controller, mock_dsp_service):
        """Test setting local DSP volume."""
        result = await controller.set_dsp_volume("local", -25.0)

        assert result is True
        mock_dsp_service.set_volume.assert_called_once_with(-25.0)

    @pytest.mark.asyncio
    async def test_set_remote_volume(self, controller, mock_proxy_service):
        """Test setting remote client volume."""
        result = await controller.set_dsp_volume("milo-client-01", -27.0)

        assert result is True
        mock_proxy_service.request.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_dsp_mute_local(self, controller, mock_dsp_service):
        """Test setting local DSP mute."""
        result = await controller.set_dsp_mute("local", True)

        assert result is True
        mock_dsp_service.set_mute.assert_called_once_with(True)

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
    async def test_wait_for_client_ready_local(self, controller, mock_dsp_service):
        """Test waiting for local client ready."""
        result = await controller.wait_for_client_ready("local", max_wait=1.0)
        assert result is True
        mock_dsp_service.wait_for_connection.assert_called_once()


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
    def mock_dsp_service(self):
        """Create mock CamillaDSP service."""
        dsp = Mock()
        dsp.set_volume = AsyncMock(return_value=True)
        dsp.get_volume = AsyncMock(return_value=-30.0)
        dsp.set_mute = AsyncMock(return_value=True)
        dsp.is_volume_control_available = Mock(return_value=True)
        dsp.wait_for_connection = AsyncMock(return_value=True)
        return dsp

    @pytest.fixture
    def mock_proxy_service(self):
        """Create mock proxy service."""
        proxy = Mock()
        proxy.request = AsyncMock(return_value={"status": "success"})
        proxy.check_available = AsyncMock(return_value=True)
        return proxy

    @pytest.fixture
    def service(self, mock_state_machine, mock_snapcast_service, mock_settings,
                mock_dsp_service, mock_proxy_service):
        """Create VolumeService with mocks."""
        return VolumeService(
            state_machine=mock_state_machine,
            snapcast_service=mock_snapcast_service,
            settings_service=mock_settings,
            camilladsp_service=mock_dsp_service,
            dsp_client_proxy_service=mock_proxy_service
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
                              mock_settings, mock_dsp_service, mock_proxy_service):
        """Test that custom EventBus is used when provided."""
        custom_bus = EventBus()
        service = VolumeService(
            state_machine=mock_state_machine,
            snapcast_service=mock_snapcast_service,
            settings_service=mock_settings,
            camilladsp_service=mock_dsp_service,
            dsp_client_proxy_service=mock_proxy_service,
            event_bus=custom_bus
        )
        assert service.event_bus is custom_bus

    def test_is_multiroom_enabled_false(self, service, mock_state_machine):
        """Test multiroom disabled check."""
        mock_state_machine.routing_service.get_state.return_value = {'multiroom_enabled': False}
        assert service._is_multiroom_enabled() is False

    def test_is_multiroom_enabled_true(self, service, mock_state_machine):
        """Test multiroom enabled check."""
        mock_state_machine.routing_service.get_state.return_value = {'multiroom_enabled': True}
        assert service._is_multiroom_enabled() is True

    def test_is_dsp_available(self, service, mock_dsp_service):
        """Test DSP availability check."""
        mock_dsp_service.is_volume_control_available.return_value = True
        assert service._is_dsp_available() is True

        mock_dsp_service.is_volume_control_available.return_value = False
        assert service._is_dsp_available() is False

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
    async def test_set_volume_db_direct_mode(self, service, mock_dsp_service, mock_state_machine):
        """Test setting volume in direct mode."""
        mock_state_machine.routing_service.get_state.return_value = {'multiroom_enabled': False}

        result = await service.set_volume_db(-25.0)

        assert result is True
        mock_dsp_service.set_volume.assert_called()

    @pytest.mark.asyncio
    async def test_adjust_volume_db(self, service, mock_dsp_service, mock_state_machine, mock_settings):
        """Test adjusting volume by delta."""
        mock_state_machine.routing_service.get_state.return_value = {'multiroom_enabled': False}
        mock_settings.get_setting = AsyncMock(return_value=False)
        service._state_store.set_local_volume(-30.0)

        result = await service.adjust_volume_db(3.0)

        assert result is True
        mock_dsp_service.set_volume.assert_called()

    @pytest.mark.asyncio
    async def test_get_status(self, service, mock_settings):
        """Test getting service status."""
        mock_settings.get_setting = AsyncMock(return_value=False)

        status = await service.get_status()

        assert isinstance(status, dict)
        assert "volume_db" in status
        assert "multiroom_enabled" in status
        assert "dsp_available" in status

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
    async def test_reload_volume_limits(self, service, mock_settings, mock_dsp_service, mock_state_machine):
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
