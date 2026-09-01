# backend/tests/test_core_volume.py
"""
Unit tests for core.volume module.

Tests the migrated VolumeService, VolumeStateStore,
and EqualizerController in the new core/volume/ location.
"""
import contextlib
import logging
import pytest
from unittest.mock import Mock, AsyncMock, patch, call
import asyncio

from backend.core.volume import (
    VolumeService,
    VolumeStateStore,
    EqualizerController
)
from backend.core.models.volume import VolumeConfig
from backend.core.models.volume_state import VolumeState, ClientVolume
from backend.core.models.ws_events import VolumeStartupChanged
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
        assert config.step_bt_remote_db == 2.0
        assert config.step_ir_remote_db == 2.0
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
        assert "step_bt_remote_db" in result
        assert "step_ir_remote_db" in result

    def test_step_ir_remote_db_custom(self):
        """Custom step_ir_remote_db value is preserved through to_dict()."""
        config = VolumeConfig(step_ir_remote_db=4.5)
        assert config.step_ir_remote_db == 4.5
        assert config.to_dict()["step_ir_remote_db"] == 4.5


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
    async def test_is_success_helper(self):
        """Test _is_success static method."""
        assert EqualizerController._is_success({"status": "success"}) is True
        assert EqualizerController._is_success({"status": "skipped"}) is True
        assert EqualizerController._is_success({"status": "error"}) is False
        assert EqualizerController._is_success({}) is False
        assert EqualizerController._is_success(None) is False

    @pytest.mark.asyncio
    async def test_an_offline_client_is_not_reported_as_applied(self, controller, mock_router):
        """A command the router refused to send must not read as applied.

        Both refusals arrive as `skipped` and they are opposites: a DAC client
        owns its own volume so there was nothing to send, while an offline
        client never heard the command. Counting the second as success is what
        let VolumeService commit a level to a satellite it had not reached.
        """
        mock_router.set_volume = AsyncMock(
            return_value={"status": "skipped", "reason": "client_offline"}
        )
        assert await controller.set_equalizer_volume("milo-client-01", -25.0) is False

        mock_router.set_volume = AsyncMock(
            return_value={"status": "skipped", "reason": "external_volume_control"}
        )
        assert await controller.set_equalizer_volume("milo-client-01", -25.0) is True

    @pytest.mark.asyncio
    async def test_the_router_offline_skip_is_the_shape_the_controller_reads(self):
        """Pin the two ends of the skip contract against the real router.

        The controller discriminates on a reason string the router writes; a
        rename on either side would leave both files self-consistent and the
        offline client silently back to reading as applied.
        """
        from backend.core.multiroom.equalizer_router import EqualizerRouter

        registry = Mock()
        registry.get_client = Mock(return_value=Mock(
            ip="192.168.1.100", is_local=False, online=False, volume_control=True
        ))
        router = EqualizerRouter(registry, Mock(), Mock())
        result = await router.set_volume("milo-client-01", -25.0)

        assert result["status"] == "skipped"
        assert EqualizerController._is_success(result) is False


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
        """Without a known local mac_id, local_volume_db reports the default."""
        assert state_store.local_volume_db == DEFAULT_VOLUME_DB

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
        """Test setting local volume writes to the local client entry."""
        state_store._local_mac_id = "aa:bb:cc:dd:ee:ff"
        state_store.set_local_volume(-25.0)
        assert state_store.local_volume_db == -25.0
        assert state_store._clients["aa:bb:cc:dd:ee:ff"].volume_db == -25.0

    @pytest.mark.asyncio
    async def test_local_client_reconnect_preserves_volume(self, state_store):
        """When the local client reconnects with its existing MAC, the
        persisted volume is preserved as-is (no reset on reconnect)."""
        from backend.core.multiroom.models import RegistryEventType

        mac = "aa:bb:cc:dd:ee:ff"
        state_store._local_mac_id = mac
        state_store._clients[mac] = ClientVolume(
            volume_db=-30.0, offset_db=0.0, mute=False, available=False,
        )

        await state_store._handle_registry_event(
            RegistryEventType.CLIENT_CONNECTED,
            # `online` is what the real producer sends and what this arm reads:
            # the same event type is emitted at registration with online False.
            {"mac_id": mac, "client": {"ip": "127.0.0.1", "online": True}},
        )

        assert mac in state_store._clients
        assert state_store._clients[mac].volume_db == -30.0
        assert state_store._local_mac_id == mac
        # Availability flips to True via set_client_availability path
        assert state_store._clients[mac].available is True

    @pytest.mark.asyncio
    async def test_local_client_first_connect_registers_the_startup_level(self, state_store):
        """First-ever local client connection (no persisted mac_id): MAC is
        cached and a fresh entry is auto-registered at the *configured* startup
        level, not at DEFAULT_VOLUME_DB.

        The distinction is the whole point: this entry is what
        `SnapcastWebSocketService._resolve_target_volume` reads to decide what the
        speaker comes back at, so seeding a level nobody configured made the
        resolver's `startup_volume_db` branch unreachable.
        """
        from backend.core.multiroom.models import RegistryEventType

        state_store._volume_config.startup_volume_db = -20.0
        mac = "aa:bb:cc:dd:ee:ff"
        assert state_store._local_mac_id is None
        assert state_store._clients == {}

        await state_store._handle_registry_event(
            RegistryEventType.CLIENT_CONNECTED,
            {"mac_id": mac, "client": {"ip": "127.0.0.1", "online": True}},
        )

        assert state_store._local_mac_id == mac
        assert mac in state_store._clients
        assert state_store._clients[mac].volume_db == -20.0

    @pytest.mark.asyncio
    async def test_client_going_offline_keeps_its_level(self, state_store):
        """A client that merely went offline keeps the level it was left at.

        Two producers emit CLIENT_DISCONNECTED and the registry is what tells them
        apart: set_client_online(False) leaves the client in the registry, so this
        arm must only lower availability. Dropping the level here would hand the
        client back at whatever its room drifted to, which is the one thing the
        volume ownership rule forbids.
        """
        from backend.core.multiroom.models import RegistryEventType

        mac = "aa:bb:cc:dd:ee:ff"
        registry = Mock()
        registry.subscribe = Mock()
        registry.get_client = Mock(return_value=Mock(mac_id=mac))
        state_store.set_registry(registry)
        state_store._clients[mac] = ClientVolume(
            volume_db=-22.0, offset_db=0.0, mute=True, available=True,
        )

        await state_store._handle_registry_event(
            RegistryEventType.CLIENT_DISCONNECTED, {"mac_id": mac},
        )

        # The arm ran: availability is what it is allowed to touch...
        assert state_store._clients[mac].available is False
        # ...and the level and mute it is not.
        assert state_store._clients[mac].volume_db == -22.0
        assert state_store._clients[mac].mute is True

    @pytest.mark.asyncio
    async def test_client_deleted_from_registry_loses_its_level(self, state_store):
        """A client the registry no longer knows is dropped from volume state too.

        unregister_client() removes the client before emitting, so get_client()
        answers None — the same event, the opposite outcome. Keeping the entry would
        resurrect a deleted speaker's level in every zone average and in the
        complete-state snapshot.
        """
        from backend.core.multiroom.models import RegistryEventType

        mac = "aa:bb:cc:dd:ee:ff"
        registry = Mock()
        registry.subscribe = Mock()
        registry.get_client = Mock(return_value=None)
        state_store.set_registry(registry)
        state_store._clients[mac] = ClientVolume(
            volume_db=-22.0, offset_db=0.0, mute=False, available=True,
        )

        await state_store._handle_registry_event(
            RegistryEventType.CLIENT_DISCONNECTED, {"mac_id": mac},
        )

        assert mac not in state_store._clients

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
    async def test_any_volume_control_local_manages(self, state_store, mock_settings):
        """Test any_volume_control is True when local device manages volume."""
        mock_settings.get_setting = AsyncMock(return_value=False)
        state_store.set_volume_control(True)

        state = await state_store.get_complete_state()
        assert state.any_volume_control is True

    @pytest.mark.asyncio
    async def test_any_volume_control_direct_dac(self, state_store, mock_settings):
        """Test any_volume_control is False in direct mode with DAC."""
        mock_settings.get_setting = AsyncMock(return_value=False)
        state_store.set_volume_control(False)
        await state_store.set_mode("direct")

        state = await state_store.get_complete_state()
        assert state.any_volume_control is False

    @pytest.mark.asyncio
    async def test_any_volume_control_multiroom_dac_with_remote(self, state_store, mock_settings):
        """Test any_volume_control is True in multiroom when remote client has volume control."""
        mock_settings.get_setting = AsyncMock(return_value={})  # No zones
        state_store.set_volume_control(False)  # Local is DAC
        await state_store.set_mode("multiroom")
        await state_store.register_client("remote-client", volume_db=-30.0, available=True)

        # Mock registry with a non-DAC remote client
        mock_registry = Mock()
        mock_client = Mock()
        mock_client.volume_control = True
        mock_registry.get_client = Mock(return_value=mock_client)
        mock_registry.get_all_zones = Mock(return_value={})
        state_store._registry = mock_registry

        state = await state_store.get_complete_state()
        assert state.any_volume_control is True

    @pytest.mark.asyncio
    async def test_any_volume_control_multiroom_all_dac(self, state_store, mock_settings):
        """Test any_volume_control is False in multiroom when all clients are DAC."""
        mock_settings.get_setting = AsyncMock(return_value={})  # No zones
        state_store.set_volume_control(False)  # Local is DAC
        await state_store.set_mode("multiroom")
        await state_store.register_client("remote-dac", volume_db=-30.0, available=True)

        # Mock registry with a DAC remote client
        mock_registry = Mock()
        mock_client = Mock()
        mock_client.volume_control = False
        mock_registry.get_client = Mock(return_value=mock_client)
        mock_registry.get_all_zones = Mock(return_value={})
        state_store._registry = mock_registry

        state = await state_store.get_complete_state()
        assert state.any_volume_control is False

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
        sm.broadcast = AsyncMock()
        sm.routing_service = Mock()
        sm.routing_service.get_state = Mock(return_value={'multiroom_enabled': False})
        return sm

    @pytest.fixture
    def mock_snapcast_service(self):
        """Create mock snapcast service."""
        service = Mock()
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

    @pytest.mark.asyncio
    async def test_apply_volume_direct_deferred_when_camilladsp_not_ready(self, service, mock_camilladsp_service):
        """Direct mode defers (does not fail) when CamillaDSP is not yet connected.

        Cold-boot / reconnect window (e.g. the post-wizard reboot): the apply is
        deferred — reapply_current_volume pushes the stored volume on reconnect —
        so _apply_volume_to_hardware returns True WITHOUT touching the daemon.
        """
        mock_camilladsp_service.is_volume_control_available.return_value = False
        service._state_store._local_mac_id = "aa:bb:cc:dd:ee:ff"  # intent recordable

        result = await service._apply_volume_to_hardware(-40.0, None, [])

        assert result is True  # deferred, not failed → no HTTP 500
        mock_camilladsp_service.set_volume.assert_not_called()  # nothing pushed while down

    @pytest.mark.asyncio
    async def test_apply_volume_direct_deferred_fails_when_local_unknown(self, service, mock_camilladsp_service):
        """Deferred apply reports failure (not false success) if the local client
        isn't known yet, so the intent could not be recorded for reconnect."""
        mock_camilladsp_service.is_volume_control_available.return_value = False
        service._state_store._local_mac_id = None  # truly-fresh first boot

        result = await service._apply_volume_to_hardware(-40.0, None, [])

        assert result is False  # honest failure — nothing was recorded to reconcile
        mock_camilladsp_service.set_volume.assert_not_called()

    @pytest.mark.asyncio
    async def test_apply_volume_direct_genuine_failure_surfaces(self, service, mock_camilladsp_service):
        """When CamillaDSP IS connected, a set_volume failure is a genuine error."""
        mock_camilladsp_service.is_volume_control_available.return_value = True
        mock_camilladsp_service.set_volume = AsyncMock(return_value=False)

        result = await service._apply_volume_to_hardware(-40.0, None, [])

        assert result is False  # real failure is surfaced (route → 500)
        mock_camilladsp_service.set_volume.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_volume_db_records_intent_when_camilladsp_not_ready(
        self, service, mock_camilladsp_service, mock_state_machine
    ):
        """First volume change on cold boot succeeds optimistically: intent is
        recorded in the state store and broadcast, hardware reconciles on reconnect."""
        mock_camilladsp_service.is_volume_control_available.return_value = False
        service._volume_config.restore_last_volume = False  # don't trigger write
        # Local client must be known for the state store to record local volume
        service._state_store._local_mac_id = "aa:bb:cc:dd:ee:ff"
        service._state_store.set_local_volume(-30.0)

        result = await service.set_volume_db(-50.0)

        assert result is True  # no silent 500 — the press is accepted
        assert service._state_store.local_volume_db == -50.0  # desired state recorded
        mock_camilladsp_service.set_volume.assert_not_called()  # deferred to reconnect
        mock_state_machine.broadcast.assert_called()  # UI reflects it immediately

    @pytest.mark.asyncio
    async def test_adjust_volume_db_defers_when_camilladsp_not_ready(
        self, service, mock_camilladsp_service, mock_settings
    ):
        """adjust_volume_db (relative) also defers (records intent) rather than
        failing when CamillaDSP isn't ready."""
        mock_camilladsp_service.is_volume_control_available.return_value = False
        mock_settings.get_setting = AsyncMock(return_value=False)
        service._volume_config.restore_last_volume = False
        service._state_store._local_mac_id = "aa:bb:cc:dd:ee:ff"
        service._state_store.set_local_volume(-30.0)

        result = await service.adjust_volume_db(2.0)

        assert result is True  # deferred, not a 500
        assert service._state_store.local_volume_db == -28.0  # intent recorded (relative)
        mock_camilladsp_service.set_volume.assert_not_called()

    @pytest.mark.asyncio
    async def test_seed_local_client_when_unresolved(self, service):
        """Fresh direct-mode boot: the local client is seeded from the system MAC,
        so set_local_volume() works (and direct-mode tracking isn't pinned at DEFAULT)."""
        assert service._state_store.local_mac_id is None
        with patch(
            "backend.core.volume.service.get_local_mac",
            return_value="2c:cf:67:8a:87:53",
        ):
            service._seed_local_client_if_needed()

        assert service._state_store.local_mac_id == "2c:cf:67:8a:87:53"
        service._state_store.set_local_volume(-33.0)
        assert service._state_store.local_volume_db == -33.0  # no longer dropped

    @pytest.mark.asyncio
    async def test_seed_local_client_is_idempotent(self, service):
        """Seeding never overrides an already-resolved local mac (Snapcast/persistence)."""
        service._state_store._local_mac_id = "aa:bb:cc:dd:ee:ff"
        with patch(
            "backend.core.volume.service.get_local_mac",
            return_value="11:22:33:44:55:66",
        ) as mock_get:
            service._seed_local_client_if_needed()

        mock_get.assert_not_called()  # short-circuits before resolving
        assert service._state_store.local_mac_id == "aa:bb:cc:dd:ee:ff"

    @pytest.mark.asyncio
    async def test_reapply_current_volume_reconciles_deferred(self, service, mock_camilladsp_service):
        """The reconnect callback pushes the stored local volume — this is what
        reconciles a deferred direct-mode change once CamillaDSP is back."""
        service._state_store._local_mac_id = "aa:bb:cc:dd:ee:ff"
        service._state_store.set_local_volume(-37.0)

        await service.reapply_current_volume()

        mock_camilladsp_service.set_volume.assert_called_once_with(-37.0)

    @pytest.mark.asyncio
    async def test_reapply_current_volume_skips_when_local_unknown(self, service, mock_camilladsp_service):
        """Boot race: CamillaDSP connects before the state store is restored. reapply
        must NOT clobber the daemon with DEFAULT_VOLUME_DB — it skips until the local
        client is known (the startup path applies the correct value)."""
        service._state_store._local_mac_id = None
        service._state_store._clients = {}

        await service.reapply_current_volume()

        mock_camilladsp_service.set_volume.assert_not_called()
        mock_camilladsp_service.set_mute.assert_not_called()

    @pytest.mark.asyncio
    async def test_reapply_current_volume_skips_when_local_has_no_entry(
        self, service, mock_camilladsp_service
    ):
        """Same clobber, other half of the guard: the local mac is known but has no
        volume entry, so local_volume_db would answer DEFAULT_VOLUME_DB.

        The twin above covers `local_mac_id is None`, which short-circuits the `or`
        before has_client() is ever consulted. This state is the durable one: the
        registry's client-deleted branch drops _clients[mac] without clearing
        _local_mac_id, so a CamillaDSP reconnect after a client deletion would push
        -45 dB at the daemon.
        """
        service._state_store._local_mac_id = "aa:bb:cc:dd:ee:ff"
        service._state_store._clients = {}

        await service.reapply_current_volume()

        mock_camilladsp_service.set_volume.assert_not_called()
        mock_camilladsp_service.set_mute.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_does_not_read_local_from_camilladsp(self, service):
        """SSOT: the local client's volume comes from the state store, never re-read
        from the live CamillaDSP (which would race the boot restore)."""
        local_mac = "2c:cf:67:b9:46:6f"
        service._routing_service = Mock()
        service._routing_service.get_state.return_value = {'multiroom_enabled': True}
        service._client_registry = Mock()
        service._client_registry.get_online_clients = Mock(return_value=[
            Mock(mac_id=local_mac, ip="127.0.0.1"),
        ])
        service._equalizer_router = Mock()
        service._equalizer_router.get_volume = AsyncMock(return_value={"main": -10.0})  # would be WRONG
        service.broadcast_volume_state = AsyncMock()
        service._state_store._local_mac_id = local_mac
        service._state_store._clients[local_mac] = ClientVolume(
            volume_db=-40.0, offset_db=0.0, mute=False, available=True
        )

        result = await service.sync_all_clients_from_equalizer()

        # Non-triviality first: @handle_errors turns any crash inside the loop into
        # False, so a body that never ran would satisfy every negative below.
        assert result is True
        service._equalizer_router.get_volume.assert_not_called()  # local never read from hardware
        assert service._state_store.get_client_volume(local_mac) == -40.0  # store value preserved
        assert service._state_store._clients[local_mac].available is True  # the sync did run
        service.broadcast_volume_state.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_keeps_persisted_remote_volume_when_proxy_fails(self, service):
        """SSOT: if the satellite proxy read fails at boot, keep the last persisted
        remote volume instead of clobbering it with the -45 dB default."""
        remote_mac = "dc:a6:32:7e:d3:43"
        service._routing_service = Mock()
        service._routing_service.get_state.return_value = {'multiroom_enabled': True}
        service._client_registry = Mock()
        service._client_registry.get_online_clients = Mock(return_value=[
            Mock(mac_id=remote_mac, ip="192.168.1.50"),
        ])
        service._equalizer_router = Mock()
        service._equalizer_router.get_volume = AsyncMock(return_value=None)  # proxy unreachable
        service.broadcast_volume_state = AsyncMock()
        service._state_store._local_mac_id = "2c:cf:67:b9:46:6f"
        service._state_store._clients[remote_mac] = ClientVolume(
            volume_db=-50.0, offset_db=0.0, mute=False, available=True
        )

        result = await service.sync_all_clients_from_equalizer()

        # Non-triviality first (see the local test above).
        assert result is True
        service._equalizer_router.get_volume.assert_awaited_once()  # the remote branch did run
        assert service._state_store.get_client_volume(remote_mac) == -50.0  # persisted kept, not -45

    # ------------------------------------------------------------------
    # A mode switch moves no level
    # ------------------------------------------------------------------

    @staticmethod
    def _two_clients_apart(service):
        """Local at -75 dB, one satellite at -30 dB: an average (-52.5) nobody set."""
        local_mac = "2c:cf:67:b9:46:6f"
        remote_mac = "dc:a6:32:7e:d3:43"
        service._state_store._local_mac_id = local_mac
        service._state_store._clients = {
            local_mac: ClientVolume(volume_db=-75.0, offset_db=0.0, mute=False, available=True),
            remote_mac: ClientVolume(volume_db=-30.0, offset_db=0.0, mute=False, available=True),
        }
        return local_mac, remote_mac

    @pytest.mark.asyncio
    async def test_leaving_multiroom_leaves_the_local_level_alone(self, service, mock_camilladsp_service):
        """The direct volume IS the local client's level, so deriving it from the
        satellites' average at the mode switch replaced a level the operator set
        with one nobody chose — audibly, on the only speaker still playing."""
        local_mac, remote_mac = self._two_clients_apart(service)

        await service.update_volume_mode(False)

        assert service._state_store.get_client_volume(local_mac) == -75.0
        assert service._state_store.get_client_volume(remote_mac) == -30.0
        mock_camilladsp_service.set_volume.assert_not_called()

    @pytest.mark.asyncio
    async def test_leaving_multiroom_still_unmutes_the_local_client(self, service, mock_camilladsp_service):
        """The one thing the switch must keep doing: direct mode plays on the local
        speaker alone, so a client muted during multiroom would come back to
        silence with nothing on screen to explain it."""
        self._two_clients_apart(service)

        await service.update_volume_mode(False)

        mock_camilladsp_service.set_mute.assert_awaited_once_with(False)

    @pytest.mark.asyncio
    async def test_entering_multiroom_returns_no_target_and_moves_nothing(self, service, mock_camilladsp_service):
        """No target comes back because there is nothing to push: the caller used
        to flatten every client onto the local level."""
        local_mac, remote_mac = self._two_clients_apart(service)

        assert await service.update_volume_mode(True) is None

        assert service._state_store.get_client_volume(local_mac) == -75.0
        assert service._state_store.get_client_volume(remote_mac) == -30.0
        mock_camilladsp_service.set_volume.assert_not_called()
        mock_camilladsp_service.set_mute.assert_not_called()
        assert (await service._state_store.get_complete_state()).mode == "multiroom"

    @pytest.mark.asyncio
    async def test_a_dac_mode_switch_only_changes_the_mode(self, service, mock_camilladsp_service):
        """No local volume control: the mode still has to change (any_volume_control
        reads it), but CamillaDSP stays as reapply_current_volume pinned it."""
        self._two_clients_apart(service)
        service._volume_control = False

        await service.update_volume_mode(False)

        assert (await service._state_store.get_complete_state()).mode == "direct"
        mock_camilladsp_service.set_volume.assert_not_called()
        mock_camilladsp_service.set_mute.assert_not_called()

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
    @staticmethod
    def _volume_section(**overrides):
        """The complete `volume` settings section.

        `_load_volume_config` reads all eight keys with no fallback operand, and
        swallows the KeyError of a short dict into a logged error that leaves the
        old config in place. A test that hands it six keys therefore measures the
        failure path while looking like it measures a reload.
        """
        section = {
            "limit_min_db": -80.0, "limit_max_db": -20.0,
            "step_mobile_db": 2.0, "step_rotary_db": 2.0,
            "step_bt_remote_db": 2.0, "step_ir_remote_db": 2.0,
            "startup_volume_db": -30.0, "restore_last_volume": False,
        }
        section.update(overrides)
        return section

    async def test_reload_volume_limits_moves_a_stranded_volume_into_the_new_window(
        self, service, mock_settings, mock_state_machine
    ):
        """Tightening the limits past the current level must move that level.

        Consumer: PUT /api/settings (volume section) -> reload_volume_limits. The
        operator lowers the ceiling while the system plays above it; leaving the
        level untouched would keep the appliance louder than the limit it now
        declares. Fails if the reload stops loading, or stops recentring.
        """
        local_mac = "2c:cf:67:b9:46:6f"
        service._state_store._local_mac_id = local_mac
        service._state_store._clients[local_mac] = ClientVolume(
            volume_db=-70.0, offset_db=0.0, mute=False, available=True
        )
        mock_settings.get_setting = AsyncMock(
            return_value=self._volume_section(limit_min_db=-60.0, limit_max_db=-15.0)
        )
        mock_state_machine.routing_service.get_state.return_value = {'multiroom_enabled': False}
        service.set_volume_db = AsyncMock()
        service.broadcast_volume_state = AsyncMock()

        result = await service.reload_volume_limits()

        assert result is True
        service.set_volume_db.assert_awaited_once()
        landed = service.set_volume_db.await_args.args[0]
        assert -60.0 <= landed <= -15.0, f"recentred to {landed}, outside the new window"

    async def test_reload_volume_limits_is_silent_when_the_limits_did_not_move(
        self, service, mock_settings, mock_state_machine
    ):
        """An unrelated settings save must not broadcast a volume event.

        Consumer: the same PUT, which fires on every settings change. The early
        return is what keeps a step-size edit from pushing a volume frame to every
        connected client. Fails if that guard is dropped.
        """
        local_mac = "2c:cf:67:b9:46:6f"
        service._state_store._local_mac_id = local_mac
        service._state_store._clients[local_mac] = ClientVolume(
            volume_db=-40.0, offset_db=0.0, mute=False, available=True
        )
        # Same limits as the service's current config, a different step size.
        mock_settings.get_setting = AsyncMock(
            return_value=self._volume_section(step_mobile_db=6.0)
        )
        mock_state_machine.routing_service.get_state.return_value = {'multiroom_enabled': False}
        service.set_volume_db = AsyncMock()
        service.broadcast_volume_state = AsyncMock()

        result = await service.reload_volume_limits()

        assert result is True
        assert service._volume_config.step_mobile_db == 6.0  # the reload did happen
        service.set_volume_db.assert_not_awaited()
        service.broadcast_volume_state.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_liveness_comes_from_the_registry_not_snapserver(
        self, service, mock_snapcast_service
    ):
        """One authority answers "is this client reachable", and it is the registry.

        EqualizerRouter short-circuits on `client.online`, so a volume fan-out
        built from a snapserver round-trip listed clients the router then
        refused — and the store was written for them anyway. Asserting the
        snapserver is not consulted is the half that keeps the second authority
        from growing back.
        """
        service.set_routing_service(
            Mock(get_state=Mock(return_value={'multiroom_enabled': True}))
        )
        registry = Mock()
        registry.get_online_client_ids = Mock(return_value=["aa:bb", "cc:dd"])
        service.attach_registry(registry)
        service._state_store._clients = {}  # no DAC exclusions recorded

        assert service._get_controllable_client_ids() == ["aa:bb", "cc:dd"]
        mock_snapcast_service.get_clients.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_registry_means_no_client_to_drive(self, service):
        """Without a registry there is no authority, so the fan-out is empty.

        It must not silently fall back to a second source: an empty list makes
        the push a logged no-op, where a snapserver-derived list would resume
        writing state for clients nothing can reach.
        """
        service.set_routing_service(
            Mock(get_state=Mock(return_value={'multiroom_enabled': True}))
        )
        assert service._get_controllable_client_ids() == []

    # ------------------------------------------------------------------
    # Boot sync, availability handshake, DAC mode
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_boot_sync_marks_clients_available_then_pushes_their_level(self, service, mock_settings):
        """The multiroom boot sync raises availability and pushes each client's level.

        Consumer: initialize() spawns _startup_broadcast_after_websocket_ready, the
        only path that runs both steps. Three collaborators are wired the way
        dependencies.py wires them, so this fails if the WebSocket reference stops
        being stored, if availability stops being raised, or if the push stops
        reaching the hardware. @handle_errors(default=None) hides a crash here, so
        both assertions are positive by construction.
        """
        mac = "dc:a6:32:7e:d3:43"
        service._state_store._local_mac_id = mac
        service._state_store._clients[mac] = ClientVolume(
            volume_db=-42.0, offset_db=0.0, mute=False, available=False
        )
        service._client_registry = Mock()
        service._client_registry.get_online_client_ids = Mock(return_value=[mac])
        service._equalizer_controller = Mock()
        service._equalizer_controller.apply_volumes_parallel = AsyncMock(return_value={mac: True})
        service._equalizer_controller.set_equalizer_mute = AsyncMock()
        service.broadcast_volume_state = AsyncMock()
        mock_settings.get_setting = AsyncMock(return_value=True)  # routing.multiroom_enabled
        # Injected through the setter, not the attribute: a setter that stops
        # storing leaves the branch below unreachable.
        service.set_snapcast_websocket_service(
            Mock(wait_for_ready=AsyncMock(return_value=True))
        )

        await service._startup_broadcast_after_websocket_ready()

        assert service._state_store._clients[mac].available is True
        service._equalizer_controller.apply_volumes_parallel.assert_awaited_once_with({mac: -42.0})

    @pytest.mark.asyncio
    async def test_push_stores_only_the_levels_the_clients_actually_took(self, service):
        """A client that refused the boot push keeps its stored level.

        Consumer: the multiroom boot sync. Writing the store for a client whose
        hardware refused is how Milō, the WS event and the UI come to agree on a
        level only the speaker disagrees with — the collective twin of the bug
        TestPerClientApplyVerdict pins on the single-client path. Fails if the
        push stops splitting on the per-client verdict.
        """
        took, refused = "aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02"
        service._volume_config = VolumeConfig(restore_last_volume=False, startup_volume_db=-30.0)
        service._state_store.set_volume_config(service._volume_config)
        for mac, db in ((took, -42.0), (refused, -50.0)):
            service._state_store._clients[mac] = ClientVolume(
                volume_db=db, offset_db=0.0, mute=False, available=True
            )
        service._client_registry = Mock()
        service._client_registry.get_online_client_ids = Mock(return_value=[took, refused])
        service._equalizer_controller = Mock()
        service._equalizer_controller.apply_volumes_parallel = AsyncMock(
            return_value={took: True, refused: False}
        )
        service._equalizer_controller.set_equalizer_mute = AsyncMock()
        service.broadcast_volume_state = AsyncMock()

        result = await service.push_volume_to_all_clients()

        # Non-triviality first: @handle_errors(default=False) makes False the crash
        # value too, so the refusal below is only meaningful once the push has run.
        service._equalizer_controller.apply_volumes_parallel.assert_awaited_once()
        assert result is False                                            # one client refused
        assert service._state_store.get_client_volume(took) == -30.0      # took the push
        assert service._state_store.get_client_volume(refused) == -50.0   # kept, not clobbered

    @pytest.mark.asyncio
    async def test_wait_for_availability_returns_true_once_signalled(self, service):
        """The WS handshake proceeds as soon as availability is signalled.

        Consumer: ws/manager.py, which blocks the initial volume frame on this.
        """
        service._availability_ready.set()

        assert await service.wait_for_availability(timeout=5.0) is True

    @pytest.mark.asyncio
    async def test_wait_for_availability_gives_up_rather_than_blocking_forever(self, service):
        """A stalled boot must not hold the WebSocket handshake open.

        Consumer: ws/manager.py — returning False lets it send local state anyway.
        Fails if the timeout is dropped, which would hang the first frame.
        """
        assert service._availability_ready.is_set() is False  # non-triviality

        assert await service.wait_for_availability(timeout=0.01) is False

    @pytest.mark.asyncio
    async def test_dac_mode_pins_camilladsp_at_unity_and_tells_the_registry(
        self, service, mock_camilladsp_service
    ):
        """Turning volume_control off hands attenuation to the external amp.

        Consumer: PATCH /api/volume-control. CamillaDSP is the only attenuation
        stage, so leaving it where it was would keep attenuating under an amp that
        now expects unity. The registry sync is what keeps a zone's
        all_external_volume honest. Fails if either half is dropped.
        """
        mac = "2c:cf:67:b9:46:6f"
        service._state_store._local_mac_id = mac
        service._client_registry = Mock()
        service._client_registry.update_client = AsyncMock()
        service.broadcast_volume_state = AsyncMock()

        await service.set_local_volume_control(False)

        assert service.volume_control is False
        mock_camilladsp_service.set_volume.assert_awaited_once_with(0.0)
        mock_camilladsp_service.set_mute.assert_awaited_once_with(False)
        service._client_registry.update_client.assert_awaited_once_with(mac, volume_control=False)


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
# Startup Volume Tests (-)
# ============================================================================

class TestStartupVolumeAutoUpdate:
    """Tests for Auto-update startup_volume_db when restore_last_volume is enabled."""

    @pytest.fixture
    def mock_state_machine(self):
        """Create mock state machine."""
        sm = Mock()
        sm.broadcast = AsyncMock()
        sm.routing_service = Mock()
        sm.routing_service.get_state = Mock(return_value={'multiroom_enabled': False})
        return sm

    @pytest.fixture
    def mock_snapcast_service(self):
        """Create mock snapcast service."""
        service = Mock()
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
        # Set initial config with restore_last_volume=True (active)
        svc._volume_config = VolumeConfig(
            limit_min_db=-80.0,
            limit_max_db=-21.0,
            startup_volume_db=-60.0,
            restore_last_volume=True
        )
        # Set state store to direct mode (default is multiroom, which would use empty clients)
        svc._state_store._mode = "direct"
        return svc

    @staticmethod
    async def _settled(service):
        """Let the debounced startup-volume write land.

        The write is deferred by STARTUP_VOLUME_DEBOUNCE_S so a rotary turn costs
        one settings.json rewrite instead of one per step; the tests below set
        that delay to 0 and give the task its turns.
        """
        service.STARTUP_VOLUME_DEBOUNCE_S = 0
        for _ in range(5):
            await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_set_volume_updates_startup_volume_when_restore_true(
        self, service, mock_settings, mock_state_machine
    ):
        """
        set_volume_db() updates startup_volume_db when restore_last_volume=true.
        """
        # Arrange: restore_last_volume=True (already set in fixture)
        mock_state_machine.routing_service.get_state.return_value = {'multiroom_enabled': False}
        service.STARTUP_VOLUME_DEBOUNCE_S = 0

        # Act: Set volume to -45dB
        await service.set_volume_db(-45.0)
        await self._settled(service)

        # Assert: startup_volume_db was updated via SettingsService
        mock_settings.set_setting.assert_called_with('volume.startup_volume_db', -45.0)

    @pytest.mark.asyncio
    async def test_set_volume_does_not_update_startup_volume_when_restore_false(
        self, service, mock_settings, mock_state_machine
    ):
        """
        set_volume_db() does NOT update startup_volume_db when restore_last_volume=false.
        """
        # Arrange: Set restore_last_volume=False
        service._volume_config = VolumeConfig(
            limit_min_db=-80.0,
            limit_max_db=-21.0,
            startup_volume_db=-60.0,
            restore_last_volume=False  # should NOT trigger
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
        adjust_volume_db() updates startup_volume_db when restore_last_volume=true.
        """
        # Arrange: restore_last_volume=True (already set in fixture)
        mock_state_machine.routing_service.get_state.return_value = {'multiroom_enabled': False}
        service.STARTUP_VOLUME_DEBOUNCE_S = 0
        service._state_store.set_local_volume(-50.0)

        # Act: Adjust by +5dB -> -45dB
        await service.adjust_volume_db(5.0)

        # Allow background task (_schedule_post_volume_tasks) and the debounced
        # persist to run
        await self._settled(service)

        # Assert: startup_volume_db was updated
        mock_settings.set_setting.assert_called()
        call_args = mock_settings.set_setting.call_args
        assert call_args[0][0] == 'volume.startup_volume_db'

    @pytest.mark.asyncio
    async def test_startup_volume_not_updated_if_unchanged(
        self, service, mock_settings, mock_state_machine
    ):
        """
        startup_volume_db is NOT updated if value is unchanged (within 0.1dB tolerance).
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
        WebSocket event 'settings_changed' is broadcast when startup_volume_db updates.
        """
        # Arrange
        mock_state_machine.routing_service.get_state.return_value = {'multiroom_enabled': False}

        # Act
        await service.set_volume_db(-45.0)

        # Assert: a typed VolumeStartupChanged event was broadcast
        broadcast_calls = mock_state_machine.broadcast.call_args_list
        startup_broadcasts = [
            c for c in broadcast_calls if isinstance(c[0][0], VolumeStartupChanged)
        ]
        assert len(startup_broadcasts) >= 1

    @pytest.mark.asyncio
    async def test_zone_volume_delta_updates_startup_volume(
        self, service, mock_settings, mock_state_machine, mock_camilladsp_service, mock_snapcast_service
    ):
        """
        apply_zone_volume_delta() updates startup_volume_db using local client volume.
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

        # Act
        service.STARTUP_VOLUME_DEBOUNCE_S = 0
        await service.apply_zone_volume_delta('zone-1', 5.0)
        await self._settled(service)

        # Assert: startup_volume_db was updated with local client's new volume
        mock_settings.set_setting.assert_called_with('volume.startup_volume_db', -45.0)

    @pytest.mark.asyncio
    async def test_a_burst_of_steps_writes_nothing_while_it_lasts(
        self, service, mock_settings, mock_state_machine
    ):
        """A rotary turn must not rewrite settings.json once per step.

        Measured on the appliance before this was debounced: ~105 steps over a
        3 s turn produced 104 full rewrites + fsyncs of an 8.6 KB file, 1.72 MB
        of block writes and 9.3 % of one core against 0.53 % at rest. The turn
        must cost the card nothing until it stops, while the tracked value is
        live in memory immediately — everything that reads startup_volume_db
        (initialize, the reconnection sync, GET /volume/startup) reads it there.
        """
        mock_state_machine.routing_service.get_state.return_value = {'multiroom_enabled': False}

        for target in range(-60, -50):
            await service.set_volume_db(float(target))

        assert mock_settings.set_setting.call_args_list == []
        assert service.volume_config.startup_volume_db == -51.0

    @pytest.mark.asyncio
    async def test_the_burst_lands_as_one_write_carrying_the_last_value(
        self, service, mock_settings, mock_state_machine
    ):
        """…and when it settles, exactly one write, with where the knob stopped."""
        mock_state_machine.routing_service.get_state.return_value = {'multiroom_enabled': False}
        service.STARTUP_VOLUME_DEBOUNCE_S = 0

        for target in range(-60, -50):
            await service.set_volume_db(float(target))
        await self._settled(service)

        assert mock_settings.set_setting.call_args_list == [
            call('volume.startup_volume_db', -51.0)
        ]


class TestStartupVolumeOnRestart:
    """Tests for Backend restart applies startup volume."""

    @pytest.fixture
    def mock_state_machine(self):
        """Create mock state machine."""
        sm = Mock()
        sm.broadcast = AsyncMock()
        sm.routing_service = Mock()
        sm.routing_service.get_state = Mock(return_value={'multiroom_enabled': False})
        return sm

    @pytest.fixture
    def mock_snapcast_service(self):
        """Create mock snapcast service."""
        service = Mock()
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
        initialize() applies startup_volume_db when restore_last_volume=false.
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
        in restore mode, the local client's OWN persisted per-client volume is
        applied — NOT startup_volume_db (which tracks the global average in multiroom).
        """
        # Arrange: local persisted at -42, startup_volume_db deliberately different
        persisted_vol = -42.0
        mac = "aa:bb:cc:dd:ee:ff"
        service._volume_config = VolumeConfig(
            limit_min_db=-80.0,
            limit_max_db=-21.0,
            startup_volume_db=-30.0,  # must be ignored in favor of the local's own value
            restore_last_volume=True
        )
        service._state_store._local_mac_id = mac
        service._state_store._clients[mac] = ClientVolume(
            volume_db=persisted_vol, offset_db=0.0, mute=False, available=True
        )

        # Act
        await service._apply_startup_volume()

        # Assert: the local's own persisted volume was applied, not startup_volume_db
        mock_camilladsp_service.set_volume.assert_called_with(persisted_vol)

    @pytest.mark.asyncio
    async def test_startup_falls_back_to_startup_volume_db_when_local_unknown(
        self, service, mock_camilladsp_service, mock_equalizer_controller
    ):
        """
        in restore mode, when the local client is not yet resolved (fresh boot
        before seeding), fall back to the configured startup_volume_db rather than the
        -45 dB hard default.
        """
        # Arrange: restore mode, no local client resolved
        startup_vol = -38.0
        service._volume_config = VolumeConfig(
            limit_min_db=-80.0,
            limit_max_db=-21.0,
            startup_volume_db=startup_vol,
            restore_last_volume=True
        )
        service._state_store._local_mac_id = None
        service._state_store._clients = {}  # No client state

        # Act
        await service._apply_startup_volume()

        # Assert: fell back to startup_volume_db
        mock_camilladsp_service.set_volume.assert_called_with(startup_vol)

    @pytest.mark.asyncio
    async def test_startup_applies_mute_state(
        self, service, mock_camilladsp_service, mock_equalizer_controller
    ):
        """
        Startup also applies persisted mute state.
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
        Gracefully handle Equalizer connection timeout on startup.
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

class _FanOutGate:
    """Holds the satellite fan-out open until every expected caller is inside it.

    This is what makes "the lock is released before the fan-out" observable
    without a clock: several callers can only be inside apply_volumes_parallel
    at the same moment if each of them let go of _volume_lock on its way there.
    `peak` is the assertion. RENDEZVOUS_TIMEOUT_S is a liveness guard, never an
    assertion — a fan-out that serialised under the lock never reaches the
    rendezvous, and the guard is what turns that into a failed assert instead
    of a hung test.
    """

    RENDEZVOUS_TIMEOUT_S = 5.0

    def __init__(self, expected: int):
        self.expected = expected
        self.inside = 0
        self.peak = 0
        self._all_inside = asyncio.Event()
        self._release = asyncio.Event()

    async def enter(self) -> None:
        """Called from inside the mocked fan-out; blocks until the gate opens."""
        self.inside += 1
        self.peak = max(self.peak, self.inside)
        if self.inside >= self.expected:
            self._all_inside.set()
        await self._release.wait()
        self.inside -= 1

    async def open_when_full(self) -> None:
        """Gathered alongside the volume calls; opens once they have all arrived."""
        with contextlib.suppress(asyncio.TimeoutError):
            async with asyncio.timeout(self.RENDEZVOUS_TIMEOUT_S):
                await self._all_inside.wait()
        self._release.set()


class TestVolumeLockNoTimeout:
    """The volume lock is not held across the satellite HTTP fan-out.

    Scenario: rapid BT remote presses in multiroom mode with a slow satellite.
    Before the fix, _apply_global_volume ran the fan-out inside the lock, so the
    next caller sat on `asyncio.timeout(2.0)` and gave up with
    "Timeout waiting for volume lock (>2s)".

    Every test here drives its callers concurrently and asserts they were all
    inside the fan-out at once — the only way that happens is if each released
    the lock before getting there. What the callers must NOT do is reach the
    fan-out one at a time: that is the regression, and `peak` is what states it.
    """

    LOCAL = "local-mac"
    SATELLITE = "satellite-mac"

    @pytest.fixture
    def mock_state_machine(self):
        sm = Mock()
        sm.broadcast = AsyncMock()
        sm.routing_service = Mock()
        sm.routing_service.get_state = Mock(return_value={'multiroom_enabled': True})
        return sm

    @pytest.fixture
    def mock_settings(self):
        settings = Mock()
        settings.invalidate_cache = Mock()
        settings.get_setting = AsyncMock(return_value=None)
        settings.set_setting = AsyncMock()
        return settings

    @pytest.fixture
    def mock_registry(self):
        """The registry answers "which clients are online".

        _get_controllable_client_ids() reads it, and nothing else does — with no
        registry attached it returns [], _compute_multiroom_updates returns {}
        and _apply_volume_to_hardware leaves on `if not updates` without ever
        reaching the satellite. That is what made two of these three tests inert.
        """
        registry = Mock()
        registry.get_online_client_ids = Mock(
            return_value=[TestVolumeLockNoTimeout.LOCAL, TestVolumeLockNoTimeout.SATELLITE]
        )
        registry.get_client = Mock(return_value=Mock(volume_control=True))
        registry.get_all_zones = Mock(return_value={})
        registry.subscribe = Mock()
        return registry

    @pytest.fixture
    def service(self, mock_state_machine, mock_settings, mock_registry):
        svc = VolumeService(
            state_machine=mock_state_machine,
            snapcast_service=Mock(),
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
        svc._state_store.set_volume_config(svc._volume_config)
        svc._routing_service = mock_state_machine.routing_service
        svc._equalizer_controller = Mock()
        svc._client_registry = mock_registry
        svc._state_store.set_registry(mock_registry)
        svc._state_store._mode = "multiroom"
        svc._state_store._clients = {
            self.LOCAL: ClientVolume(volume_db=-40.0, offset_db=0.0, mute=False, available=True),
            self.SATELLITE: ClientVolume(volume_db=-40.0, offset_db=0.0, mute=False, available=True),
        }
        return svc

    @pytest.fixture
    def fan_out_gate(self, service):
        """Factory: make the satellite fan-out block until `expected` callers are in it."""
        def _install(expected: int) -> _FanOutGate:
            gate = _FanOutGate(expected)

            async def gated_apply(updates):
                await gate.enter()
                return {mac_id: True for mac_id in updates}

            service._equalizer_controller.apply_volumes_parallel = AsyncMock(
                side_effect=gated_apply
            )
            return gate
        return _install

    @pytest.mark.asyncio
    async def test_rapid_volume_changes_no_lock_timeout(self, service, fan_out_gate):
        """Five BT-remote presses in flight at once all get through the lock.

        Launched concurrently on purpose: five sequential `await`s cannot
        contend for a lock at all, so the sequential version this replaces could
        not fail on the regression its own docstring names.
        """
        gate = fan_out_gate(5)

        results = await asyncio.gather(
            *[service.adjust_volume_db(2.0) for _ in range(5)],
            gate.open_when_full(),
        )

        assert all(results[:5]), f"Expected all True, got {results[:5]}"
        assert gate.peak == 5, (
            f"only {gate.peak} of 5 callers reached the satellite fan-out at once — "
            "the volume lock is being held across it"
        )

    @pytest.mark.asyncio
    async def test_concurrent_volume_sources_no_lock_timeout(self, service, fan_out_gate):
        """BT remote + rotary + frontend slider, all three in flight together."""
        gate = fan_out_gate(3)

        results = await asyncio.gather(
            service.adjust_volume_db(2.0),
            service.adjust_volume_db(2.0),
            service.set_volume_db(-35.0),
            gate.open_when_full(),
        )

        assert all(results[:3]), f"Expected all True, got {results[:3]}"
        assert gate.peak == 3, (
            f"only {gate.peak} of 3 callers reached the satellite fan-out at once — "
            "the volume lock is being held across it"
        )

    @pytest.mark.asyncio
    async def test_zone_delta_no_lock_timeout(self, service, mock_registry, fan_out_gate):
        """A zone delta and a global adjust reach the satellites together.

        apply_zone_volume_delta once took the lock with no timeout at all, so it
        could block every other volume operation for as long as the fan-out ran.
        """
        from backend.core.volume.state import ZoneConfig
        zone = ZoneConfig(
            zone_id="zone-1", name="Test",
            client_ids=[self.LOCAL, self.SATELLITE],
        )
        service._state_store._zones = {"zone-1": zone}
        # get_complete_state() reloads zones from the registry, wiping any the
        # test planted directly; the concurrent adjust below goes through it.
        mock_registry.get_all_zones.return_value = {"zone-1": zone}

        gate = fan_out_gate(2)

        results = await asyncio.gather(
            service.apply_zone_volume_delta("zone-1", 3.0),
            service.adjust_volume_db(2.0),
            gate.open_when_full(),
        )

        # Zone delta returns float (new average), adjust returns bool
        assert isinstance(results[0], float)
        assert results[1] is True
        assert gate.peak == 2, (
            f"only {gate.peak} of 2 callers reached the satellite fan-out at once — "
            "the volume lock is being held across it"
        )


# ============================================================================
# Per-client volume and mute answer for what the speaker did (sweep S4)
# ============================================================================

class TestPerClientApplyVerdict:
    """A level an online client refused must not be stored, broadcast or reported.

    When these fail, `PATCH /api/volume/client/mac/{mac}` is back to answering
    200 with a dB the speaker never took: the store was written before the
    apply, so Milō, the WS event and the UI all agreed on a value only the
    hardware disagreed with, and the sole trace was a warning.
    """

    ACCEPTING = "aa:bb:cc:dd:ee:01"
    REFUSING = "aa:bb:cc:dd:ee:02"

    @pytest.fixture
    def mock_state_machine(self):
        sm = Mock()
        sm.broadcast = AsyncMock()
        sm.routing_service = Mock()
        sm.routing_service.get_state = Mock(return_value={'multiroom_enabled': True})
        return sm

    @pytest.fixture
    def mock_settings(self):
        settings = Mock()
        settings.invalidate_cache = Mock()
        settings.get_setting = AsyncMock(return_value=None)
        settings.set_setting = AsyncMock()
        return settings

    @pytest.fixture
    def mock_registry(self):
        """Registry standing for the outside world's answer to "is it online?"."""
        registry = Mock()
        registry.is_client_online = Mock(return_value=True)
        registry.get_online_client_ids = Mock(
            return_value=[TestPerClientApplyVerdict.ACCEPTING, TestPerClientApplyVerdict.REFUSING]
        )
        return registry

    @pytest.fixture
    def mock_equalizer_controller(self):
        """The refusing client answers False to both volume and mute."""
        controller = Mock()

        async def apply(mac_id, _value, **kwargs):
            return mac_id != TestPerClientApplyVerdict.REFUSING

        controller.set_equalizer_volume = AsyncMock(side_effect=apply)
        controller.set_equalizer_mute = AsyncMock(side_effect=apply)
        return controller

    @pytest.fixture
    def service(self, mock_state_machine, mock_settings, mock_registry,
                mock_equalizer_controller):
        svc = VolumeService(
            state_machine=mock_state_machine,
            snapcast_service=Mock(),
            settings_service=mock_settings,
            camilladsp_service=Mock(
                set_volume=AsyncMock(return_value=True),
                set_mute=AsyncMock(return_value=True),
                is_volume_control_available=Mock(return_value=True),
            ),
        )
        svc._volume_config = VolumeConfig(
            limit_min_db=-80.0, limit_max_db=0.0,
            startup_volume_db=-40.0, restore_last_volume=True,
        )
        svc._state_store.set_volume_config(svc._volume_config)
        svc._routing_service = mock_state_machine.routing_service
        svc._equalizer_controller = mock_equalizer_controller
        svc._client_registry = mock_registry
        svc._state_store._mode = "multiroom"
        svc._state_store._clients = {
            self.ACCEPTING: ClientVolume(volume_db=-40.0, offset_db=0.0, mute=False, available=True),
            self.REFUSING: ClientVolume(volume_db=-40.0, offset_db=0.0, mute=False, available=True),
        }
        return svc

    @pytest.mark.asyncio
    async def test_a_client_that_took_the_volume_stores_and_reports_it(self, service, caplog):
        """The happy path is unchanged — the level is stored, no noise."""
        with caplog.at_level(logging.ERROR):
            assert await service.update_client_volume_db(self.ACCEPTING, -25.0) is True

        assert service.state_store.get_client_volume(self.ACCEPTING) == -25.0
        assert caplog.text == ""

    @pytest.mark.asyncio
    async def test_an_online_client_that_refused_keeps_its_stored_volume(self, service, caplog):
        """The refusal decides the verdict, and the store keeps what the speaker holds."""
        with caplog.at_level(logging.ERROR):
            assert await service.update_client_volume_db(self.REFUSING, -25.0) is False

        assert service.state_store.get_client_volume(self.REFUSING) == -40.0
        assert self.REFUSING in caplog.text
        assert self.ACCEPTING not in caplog.text

    @pytest.mark.asyncio
    async def test_the_broadcast_carries_the_level_the_speaker_holds(
        self, service, mock_state_machine
    ):
        """A refused dB must not reach the UI through volume_changed."""
        await service.update_client_volume_db(self.REFUSING, -25.0)

        mock_state_machine.broadcast.assert_awaited()
        event = mock_state_machine.broadcast.await_args_list[-1].args[0]
        assert event.state["clients"][self.REFUSING]["volume_db"] == -40.0

    @pytest.mark.asyncio
    async def test_an_offline_client_stores_the_level_for_the_reconnection_replay(
        self, service, mock_registry, caplog
    ):
        """An offline client is a skip, not a refusal.

        EqualizerRouter short-circuits it and the admission re-push replays the
        stored value on reconnection, so the store must be written and the
        route must keep its 200.
        """
        mock_registry.is_client_online.return_value = False

        with caplog.at_level(logging.ERROR):
            assert await service.update_client_volume_db(self.REFUSING, -25.0) is True

        assert service.state_store.get_client_volume(self.REFUSING) == -25.0
        assert caplog.text == ""

    @pytest.mark.asyncio
    async def test_an_online_client_that_refused_the_mute_keeps_its_stored_state(
        self, service, caplog
    ):
        """Mute travels the same path and answers the same way."""
        with caplog.at_level(logging.ERROR):
            assert await service.set_client_mute(self.REFUSING, True) is False

        assert service.state_store.get_client_mute(self.REFUSING) is False
        assert self.REFUSING in caplog.text

    @pytest.mark.asyncio
    async def test_a_client_that_took_the_mute_stores_and_reports_it(self, service, caplog):
        """The happy path is unchanged for mute too."""
        with caplog.at_level(logging.ERROR):
            assert await service.set_client_mute(self.ACCEPTING, True) is True

        assert service.state_store.get_client_mute(self.ACCEPTING) is True
        assert caplog.text == ""

    @pytest.mark.asyncio
    async def test_an_offline_client_stores_the_mute_for_the_reconnection_replay(
        self, service, mock_registry, caplog
    ):
        """Same skip rule as the volume: _do_sync_reconnecting_client_volume replays it."""
        mock_registry.is_client_online.return_value = False

        with caplog.at_level(logging.ERROR):
            assert await service.set_client_mute(self.REFUSING, True) is True

        assert service.state_store.get_client_mute(self.REFUSING) is True
        assert caplog.text == ""


# ============================================================================
# A relative adjustment reaches a client that was absent for it (plan phase 3)
# ============================================================================

class TestAbsentClientKeepsItsPlaceInTheRoom:
    """A zone or global delta made while a client is away must be in its level.

    When these fail, a satellite that was off during an adjustment comes back at
    the level it left — right in absolute terms, wrong relative to the room it
    plays in, and nothing ever corrects it. The delta is relative, so the store
    can carry it with no hardware and no replay queue; what must not happen is
    the store being written for a *reachable* client that refused the level,
    which is the other half of each test here.
    """

    ONLINE = "aa:bb:cc:dd:ee:01"
    REFUSING = "aa:bb:cc:dd:ee:02"
    OFFLINE = "aa:bb:cc:dd:ee:03"

    @pytest.fixture
    def mock_state_machine(self):
        sm = Mock()
        sm.broadcast = AsyncMock()
        sm.routing_service = Mock()
        sm.routing_service.get_state = Mock(return_value={'multiroom_enabled': True})
        return sm

    @pytest.fixture
    def mock_settings(self):
        settings = Mock()
        settings.invalidate_cache = Mock()
        settings.get_setting = AsyncMock(return_value=None)
        settings.set_setting = AsyncMock()
        return settings

    @pytest.fixture
    def mock_registry(self):
        """The registry answers "is it online?" for the global path."""
        registry = Mock()
        registry.is_client_online = Mock(
            side_effect=lambda cid: cid != TestAbsentClientKeepsItsPlaceInTheRoom.OFFLINE
        )
        registry.get_online_client_ids = Mock(return_value=[
            TestAbsentClientKeepsItsPlaceInTheRoom.ONLINE,
            TestAbsentClientKeepsItsPlaceInTheRoom.REFUSING,
        ])
        registry.get_client = Mock(return_value=None)
        return registry

    @pytest.fixture
    def mock_equalizer_controller(self):
        """The refusing client answers False; the absent one is never called."""
        controller = Mock()
        attempted = {}

        async def apply(mac_id, volume, **kwargs):
            attempted[mac_id] = volume
            return mac_id != TestAbsentClientKeepsItsPlaceInTheRoom.REFUSING

        async def apply_parallel(updates):
            return {cid: await apply(cid, vol) for cid, vol in updates.items()}

        controller.set_equalizer_volume = AsyncMock(side_effect=apply)
        controller.apply_volumes_parallel = AsyncMock(side_effect=apply_parallel)
        controller.attempted = attempted
        return controller

    @pytest.fixture
    def service(self, mock_state_machine, mock_settings, mock_registry,
                mock_equalizer_controller):
        svc = VolumeService(
            state_machine=mock_state_machine,
            snapcast_service=Mock(),
            settings_service=mock_settings,
            camilladsp_service=Mock(
                set_volume=AsyncMock(return_value=True),
                is_volume_control_available=Mock(return_value=True),
            ),
        )
        svc._volume_config = VolumeConfig(
            limit_min_db=-80.0, limit_max_db=0.0,
            startup_volume_db=-40.0, restore_last_volume=True,
        )
        svc._state_store.set_volume_config(svc._volume_config)
        svc._routing_service = mock_state_machine.routing_service
        svc._equalizer_controller = mock_equalizer_controller
        svc._client_registry = mock_registry
        svc._state_store._mode = "multiroom"
        svc._state_store._schedule_persist = Mock()
        svc._state_store._clients = {
            self.ONLINE: ClientVolume(volume_db=-30.0, offset_db=0.0, mute=False, available=True),
            self.REFUSING: ClientVolume(volume_db=-40.0, offset_db=0.0, mute=False, available=True),
            self.OFFLINE: ClientVolume(volume_db=-50.0, offset_db=0.0, mute=False, available=False),
        }
        return svc

    @pytest.fixture
    def zoned(self, service):
        """The three clients as one zone."""
        from backend.core.volume.state import ZoneConfig
        service._state_store._zones = {
            'salon': ZoneConfig(zone_id='salon', name='Salon',
                                client_ids=[self.ONLINE, self.REFUSING, self.OFFLINE])
        }
        service._state_store._load_zones = AsyncMock()
        return service

    # ---- 3.1 the zone delta ----

    @pytest.mark.asyncio
    async def test_a_zone_delta_lands_in_an_absent_member_s_stored_level(self, zoned):
        """The absent member's level moves by the delta with no call made for it."""
        await zoned.apply_zone_volume_delta('salon', -6.0)

        assert zoned.state_store.get_client_volume(self.OFFLINE) == -56.0
        assert self.OFFLINE not in zoned.equalizer_controller.attempted

    @pytest.mark.asyncio
    async def test_a_zone_delta_is_still_gated_on_a_reachable_member_s_verdict(self, zoned):
        """Reaching a speaker and being refused is not the same as not reaching it."""
        await zoned.apply_zone_volume_delta('salon', -6.0)

        assert zoned.state_store.get_client_volume(self.ONLINE) == -36.0
        assert zoned.state_store.get_client_volume(self.REFUSING) == -40.0
        assert zoned.equalizer_controller.attempted[self.REFUSING] == -46.0

    # ---- 3.2 the global delta ----

    @pytest.mark.asyncio
    async def test_a_global_delta_lands_in_an_absent_client_s_stored_level(self, service):
        """Same rule on the global path, whose liveness comes from the registry.

        The global average counts the two available clients (-35), so a -6 dB
        target shifts everything by -6.
        """
        await service.set_volume_db(-41.0)

        assert service.state_store.get_client_volume(self.OFFLINE) == -56.0
        assert self.OFFLINE not in service.equalizer_controller.attempted

    @pytest.mark.asyncio
    async def test_a_global_delta_is_still_gated_on_a_reachable_client_s_verdict(self, service):
        """A client the fan-out reached and that refused keeps its stored level."""
        await service.set_volume_db(-41.0)

        assert service.state_store.get_client_volume(self.ONLINE) == -36.0
        assert service.state_store.get_client_volume(self.REFUSING) == -40.0
        assert service.equalizer_controller.attempted[self.REFUSING] == -46.0


class TestEqualizerControllerRegistryInjection:
    """`EqualizerController.set_registry` — the injection `VolumeService` does.

    Green in the Lot A eviscration sweep, and `test_service_wiring` only proves
    a production caller exists (`core/volume/service.py:106`), never that the
    injection lands. Neutralised, the controller keeps no registry and
    `apply_volumes_parallel` short-circuits: every client of a zone reports
    False and nothing is dispatched, so a zone volume change does nothing at
    all while each route still answers.
    """

    @pytest.fixture
    def controller(self):
        return EqualizerController(Mock(), Mock(), equalizer_router=Mock())

    async def test_without_a_registry_no_client_is_dispatched_to(self, controller):
        controller.set_equalizer_volume = AsyncMock(return_value=True)

        results = await controller.apply_volumes_parallel({"aa:bb": -20.0, "cc:dd": -25.0})

        assert results == {"aa:bb": False, "cc:dd": False}
        controller.set_equalizer_volume.assert_not_awaited()

    async def test_once_injected_every_client_is_dispatched_to(self, controller):
        controller.set_registry(Mock())
        controller.set_equalizer_volume = AsyncMock(return_value=True)

        results = await controller.apply_volumes_parallel({"aa:bb": -20.0, "cc:dd": -25.0})

        assert results == {"aa:bb": True, "cc:dd": True}
        assert controller.set_equalizer_volume.await_count == 2
