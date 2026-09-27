# backend/tests/test_core_volume.py
"""
Unit tests for core.volume module.

Tests the migrated VolumeService, VolumeStateStore,
and EqualizerController in the new core/volume/ location.
"""
import logging
import pytest
from unittest.mock import Mock, AsyncMock, patch, call
import asyncio

from backend.core.multiroom.equalizer_router import EqualizerRouter
from backend.core.volume import (
    VolumeService,
    VolumeStateStore,
    EqualizerController
)
from backend.core.models.volume import (
    VolumeConfig,
    denormalize_volume,
    normalize_volume,
)
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
# The dB <-> 0..1 scale
# ============================================================================

class TestVolumeScale:
    """The two conversions Milō owns, so no client re-implements them.

    What breaks when these fail is a slider that does not mean what it shows:
    Milo-iOS converted on its own cached -80..-21 while the unit ran -78..-8,
    and a gesture worth 12 dB landed as 3, with no error anywhere.
    """

    def test_the_span_is_the_operator_limits_not_the_technical_range(self):
        """A slider calibrated on a hardcoded -80..0 sits at the wrong place on
        every unit: this one is limited to -78..-8, where the midpoint is -43,
        not -40. Normalizing on the client is what would bake that in."""
        assert normalize_volume(-43.0, -78.0, -8.0) == 0.5

    @pytest.mark.parametrize("db,expected", [(-78.0, 0.0), (-8.0, 1.0)])
    def test_the_limits_map_to_the_ends(self, db, expected):
        assert normalize_volume(db, -78.0, -8.0) == expected

    def test_a_level_outside_the_limits_is_clamped(self):
        """A client can sit below the floor through its per-client offset. A
        slider at -0.03 is not a thing iOS can draw."""
        assert normalize_volume(-90.0, -78.0, -8.0) == 0.0
        assert normalize_volume(0.0, -78.0, -8.0) == 1.0

    def test_a_degenerate_span_does_not_divide_by_zero(self):
        assert normalize_volume(-40.0, -40.0, -40.0) == 0.0

    @pytest.mark.parametrize("level,expected", [(0.0, -78.0), (1.0, -8.0), (0.5, -43.0)])
    def test_the_ends_of_the_slider_are_the_limits_exactly(self, level, expected):
        """Not "close to": the top of the slider must denormalize to
        limit_max_db itself, because the per-client route compares against that
        bound and a value a hair above it would be refused."""
        assert denormalize_volume(level, -78.0, -8.0) == expected

    def test_a_level_outside_zero_one_is_clamped(self):
        assert denormalize_volume(-0.5, -78.0, -8.0) == -78.0
        assert denormalize_volume(1.5, -78.0, -8.0) == -8.0

    def test_a_degenerate_span_answers_the_floor(self):
        """Symmetric with the 0.0 the forward direction answers: the silent end
        is the only safe answer when there is no span to place a level in."""
        assert denormalize_volume(0.9, -40.0, -40.0) == -40.0

    @pytest.mark.parametrize("limits", [(-78.0, -8.0), (-80.0, -20.0), (-60.0, -10.0)])
    @pytest.mark.parametrize("level", [0.0, 0.0001, 0.43, 0.5, 0.6, 0.9999, 1.0])
    def test_the_two_directions_are_reciprocal(self, limits, level):
        """A round trip through Milō must give a phone back the position it
        sent. The forward direction rounds to four decimals, which is the
        tolerance — anything coarser would make the slider creep on every
        update it echoes back."""
        min_db, max_db = limits
        assert normalize_volume(denormalize_volume(level, min_db, max_db), min_db, max_db) == level

    def test_the_config_spans_its_own_limits(self):
        """The methods exist so a caller holding a config never has to name the
        bounds a second time — that second naming is how a client came to
        convert on limits the unit had left."""
        config = VolumeConfig(limit_min_db=-78.0, limit_max_db=-8.0)
        assert config.normalize(-43.0) == 0.5
        assert config.denormalize(0.5) == -43.0


# ============================================================================
# EqualizerController Tests
# ============================================================================

class TestEqualizerController:
    """Tests for EqualizerController (delegates to EqualizerRouter)."""

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
        """A registry: the controller only checks that one is wired."""
        return Mock()

    @pytest.fixture
    def controller(self, mock_router, mock_registry):
        """Create EqualizerController."""
        return EqualizerController(equalizer_router=mock_router, client_registry=mock_registry)

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
    async def test_set_volume_no_router(self):
        """Test set volume returns False without router."""
        ctrl = EqualizerController()
        result = await ctrl.set_equalizer_volume("local", -25.0)
        assert result is False

    @pytest.mark.asyncio
    async def test_set_mute_delegates_to_router(self, controller, mock_router):
        """Test setting mute delegates to EqualizerRouter."""
        result = await controller.set_equalizer_mute("local", True)

        assert result is True
        mock_router.set_mute.assert_called_once_with("local", True, force=False)

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

        Both refusals arrive as `skipped` and they are opposites: on a DAC
        client the level trim is something Milo does not own, so there was
        nothing to send, while an offline client never heard the command.
        Counting the second as success is what let VolumeService commit a level
        to a satellite it had not reached.

        The volume half of this pair no longer exists: a DAC client is routed
        at unity rather than skipped, because a fader nobody writes is a fader
        left at whatever its unit started it on.
        """
        mock_router.set_volume = AsyncMock(
            return_value={"status": "skipped", "reason": "client_offline"}
        )
        assert await controller.set_equalizer_volume("milo-client-01", -25.0) is False

        mock_router.set_gain = AsyncMock(
            return_value={"status": "skipped", "reason": "external_volume_control"}
        )
        assert await controller.set_equalizer_gain("milo-client-01", 3.0) is True

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
    async def test_the_snapshot_carries_the_span_its_levels_were_measured_over(
        self, state_store, mock_settings
    ):
        """A normalized level means nothing without the limits it spans, and the
        limits move. Sending them apart — in a settings call a client caches —
        is how Milo-iOS came to convert on a span this unit had left."""
        mock_settings.get_setting = AsyncMock(return_value=False)
        state_store.set_volume_config(VolumeConfig(limit_min_db=-78.0, limit_max_db=-8.0))
        state_store.ensure_local_client("dc:a6:32:7e:d3:43", -43.0)

        state = await state_store.get_complete_state()

        assert (state.limit_min_db, state.limit_max_db) == (-78.0, -8.0)
        assert state.to_dict()["global_volume"] == 0.5

    @pytest.mark.asyncio
    async def test_a_dac_client_says_so_in_its_own_entry(self, state_store, mock_settings):
        """`volume_control` is already the filter the global average applies, so
        a reader that gets it can build the same set. Without it the lock screen
        drew a slider for a speaker Milō does not count, and the phone's idea of
        the global level and Milō's named different numbers."""
        mock_settings.get_setting = AsyncMock(return_value={})
        await state_store.set_mode("multiroom")
        await state_store.register_client("dac-client", volume_db=-30.0, available=True)

        registry = Mock()
        registry.get_client = Mock(return_value=Mock(volume_control=False))
        registry.get_all_zones = Mock(return_value={})
        state_store._registry = registry

        state = await state_store.get_complete_state()

        assert state.clients["dac-client"].volume_control is False
        assert state.to_dict()["clients"]["dac-client"]["volume_control"] is False

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
        """Create VolumeService with mocks.

        The router is the real one over the mocked daemon, as in production:
        every level reaches a speaker through it, and with no registry it sends
        everything to the local CamillaDSP.
        """
        return VolumeService(
            state_machine=mock_state_machine,
            snapcast_service=mock_snapcast_service,
            settings_service=mock_settings,
            camilladsp_service=mock_camilladsp_service,
            equalizer_client_proxy_service=mock_proxy_service,
            equalizer_router=EqualizerRouter(
                client_registry=None,
                camilladsp_service=mock_camilladsp_service,
                proxy_service=mock_proxy_service,
            ),
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
    async def test_a_multiroom_local_speaker_whose_dsp_is_down_is_deferred_too(
        self, service, mock_camilladsp_service
    ):
        """The local CamillaDSP not being up is not a failure, in either mode.

        Cold-boot / reconnect window (e.g. the post-wizard reboot): the level is
        stored and reapply_current_volume puts it on the daemon once it is back.
        Direct mode always did this; multiroom answered 500 instead, and kept
        the old level in the store, so the reconnect restored the old one.
        """
        mock_camilladsp_service.is_volume_control_available.return_value = False
        service._routing_service = Mock()
        service._routing_service.get_state.return_value = {'multiroom_enabled': True}
        service._state_store._local_mac_id = "aa:bb"
        service._state_store._clients["aa:bb"] = ClientVolume(
            volume_db=-40.0, offset_db=0.0, mute=False, available=True
        )

        assert await service.adjust_volume_db(2.0) is True
        assert service._state_store.get_client_volume("aa:bb") == -38.0
        mock_camilladsp_service.set_volume.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_direct_set_with_the_local_client_unknown_fails(self, service, mock_camilladsp_service):
        """Reports failure (not false success) if the local client isn't known,
        since there is no level to record and nothing for the reconnect to apply."""
        service._state_store._local_mac_id = None  # truly-fresh first boot

        assert await service.set_volume_db(-40.0) is False
        mock_camilladsp_service.set_volume.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_direct_set_the_daemon_refuses_surfaces(self, service, mock_camilladsp_service):
        """When CamillaDSP IS connected, a set_volume failure is a genuine error."""
        mock_camilladsp_service.is_volume_control_available.return_value = True
        mock_camilladsp_service.set_volume = AsyncMock(return_value=False)
        service._state_store.ensure_local_client("aa:bb", -50.0)

        assert await service.set_volume_db(-40.0) is False  # route → 500
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
    async def test_set_volume_db_direct_mode(self, service, mock_camilladsp_service, mock_state_machine):
        """Test setting volume in direct mode."""
        mock_state_machine.routing_service.get_state.return_value = {'multiroom_enabled': False}
        service._state_store.ensure_local_client("aa:bb", -50.0)

        result = await service.set_volume_db(-25.0)

        assert result is True
        mock_camilladsp_service.set_volume.assert_awaited_once_with(-25.0)

    @pytest.mark.asyncio
    async def test_adjust_volume_db(self, service, mock_camilladsp_service, mock_state_machine, mock_settings):
        """Test adjusting volume by delta."""
        mock_state_machine.routing_service.get_state.return_value = {'multiroom_enabled': False}
        mock_settings.get_setting = AsyncMock(return_value=False)
        service._state_store.ensure_local_client("aa:bb", -30.0)

        result = await service.adjust_volume_db(3.0)

        assert result is True
        mock_camilladsp_service.set_volume.assert_awaited_once_with(-27.0)

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

    @staticmethod
    def _multiroom(service, online, latency=0.0):
        """Multiroom on, `online` reachable; `service.sent` records what each
        speaker was sent last, as the router received it."""
        service._routing_service = Mock()
        service._routing_service.get_state.return_value = {'multiroom_enabled': True}
        service._client_registry = Mock()
        service._client_registry.get_online_client_ids.return_value = list(online)
        service.sent = {}

        async def set_volume(mac_id, volume_db, force=False):
            if latency:
                await asyncio.sleep(latency)
            service.sent[mac_id] = volume_db
            return {"status": "success"}

        service._equalizer_controller._router = Mock(set_volume=set_volume)
        service.broadcast_volume_state = AsyncMock()

    @staticmethod
    async def _settle():
        """Let the per-speaker senders drain (they run as their own tasks)."""
        for _ in range(5):
            await asyncio.sleep(0)

    async def test_a_raised_minimum_lifts_each_quiet_room_to_it_and_no_further(
        self, service, mock_settings
    ):
        """Raising the floor moves the rooms under it to the floor, and nothing else.

        Consumer: PUT /api/settings/volume-limits. Measured on the unit with
        limits -78..-8 and rooms at -78/-75/-40: the reload used to recentre on
        the middle of the range, -39 dB, a +39 dB jump on a room set to -78.
        Fails if a room is sent anywhere but the nearest limit, or if a room the
        limits still allow is sent anything.
        """
        service._volume_config = VolumeConfig(limit_min_db=-78.0, limit_max_db=-8.0)
        service._state_store.set_volume_config(service._volume_config)
        for mac, level in (("a", -78.0), ("b", -75.0), ("c", -40.0)):
            service._state_store._clients[mac] = ClientVolume(
                volume_db=level, offset_db=0.0, mute=False, available=True
            )
        self._multiroom(service, online=["a", "b", "c"])
        mock_settings.get_setting = AsyncMock(
            return_value=self._volume_section(limit_min_db=-70.0, limit_max_db=-8.0)
        )

        assert await service.reload_volume_limits() is True
        await self._settle()

        assert service.sent == {"a": -70.0, "b": -70.0}
        levels = {mac: service._state_store.get_client_volume(mac) for mac in "abc"}
        assert levels == {"a": -70.0, "b": -70.0, "c": -40.0}
        service.broadcast_volume_state.assert_awaited_once()

    async def test_a_lowered_maximum_brings_the_direct_speaker_down_to_it(
        self, service, mock_settings, mock_camilladsp_service
    ):
        """Direct mode: the local speaker above a lowered ceiling plays at it.

        Consumer: the same PUT, in direct mode, where only the local CamillaDSP
        plays. Fails if the level leaves the store without reaching the daemon,
        or reaches it anywhere but the new ceiling.
        """
        local_mac = "2c:cf:67:b9:46:6f"
        service._volume_config = VolumeConfig(limit_min_db=-78.0, limit_max_db=-8.0)
        service._state_store.set_volume_config(service._volume_config)
        service._state_store._local_mac_id = local_mac
        service._state_store._clients[local_mac] = ClientVolume(
            volume_db=-10.0, offset_db=0.0, mute=False, available=True
        )
        mock_settings.get_setting = AsyncMock(
            return_value=self._volume_section(limit_min_db=-78.0, limit_max_db=-20.0)
        )
        service.broadcast_volume_state = AsyncMock()

        assert await service.reload_volume_limits() is True

        mock_camilladsp_service.set_volume.assert_awaited_once_with(-20.0)
        assert service._state_store.local_volume_db == -20.0

    async def test_a_room_that_is_away_gets_the_new_limit_in_the_store_only(
        self, service, mock_settings
    ):
        """An offline room above a lowered ceiling is brought to it without a hardware call.

        Consumer: the reconnection sync, which puts the stored level back on the
        speaker. Sending it now would reach nothing; not storing it would bring
        the room back louder than the ceiling the operator just lowered. Fails
        either way.
        """
        service._volume_config = VolumeConfig(limit_min_db=-78.0, limit_max_db=-8.0)
        service._state_store.set_volume_config(service._volume_config)
        service._state_store._clients["here"] = ClientVolume(
            volume_db=-10.0, offset_db=0.0, mute=False, available=True
        )
        service._state_store._clients["away"] = ClientVolume(
            volume_db=-10.0, offset_db=0.0, mute=False, available=False
        )
        self._multiroom(service, online=["here"])
        mock_settings.get_setting = AsyncMock(
            return_value=self._volume_section(limit_min_db=-78.0, limit_max_db=-20.0)
        )

        assert await service.reload_volume_limits() is True
        await self._settle()

        assert service.sent == {"here": -20.0}
        assert service._state_store.get_client_volume("away") == -20.0

    @staticmethod
    def _zone(service, levels, online):
        """One zone 'z' holding `levels` ({mac: dB}), `online` reachable."""
        from backend.core.volume.state import ZoneConfig
        for mac, level in levels.items():
            service._state_store._clients[mac] = ClientVolume(
                volume_db=level, offset_db=0.0, mute=False, available=mac in online
            )
        service._state_store._zones["z"] = ZoneConfig(zone_id="z", name="Z", client_ids=list(levels))

    async def test_a_zone_level_lands_the_average_on_it(self, service):
        """A level moves every member by one delta, measured against the average.

        Consumer: PATCH /api/volume/zone/{id} with `volume_db`, the web slider.
        Fails if the rooms stop moving together or the average misses the level.
        """
        self._zone(service, {"a": -40.0, "b": -50.0}, online=["a", "b"])
        self._multiroom(service, online=["a", "b"])

        average, delta = await service.set_zone_volume("z", -30.0)
        await self._settle()

        assert (average, delta) == (-30.0, 15.0)
        assert service.sent == {"a": -25.0, "b": -35.0}
        assert service._state_store.get_client_volume("a") == -25.0
        assert service._state_store.get_client_volume("b") == -35.0

    async def test_two_sends_of_one_position_move_the_zone_once(self, service):
        """A drag re-sends the thumb's level while the first send is in flight.

        Consumer: the web slider, throttled at 80 ms and fire-and-forget. Its
        deltas were measured against an average it captured itself, so each send
        in flight added the same delta again. Fails if two concurrent sends of
        one level leave the zone anywhere but that level.
        """
        self._zone(service, {"a": -40.0, "b": -50.0}, online=["a", "b"])
        self._multiroom(service, online=["a", "b"], latency=0.01)

        await asyncio.gather(service.set_zone_volume("z", -30.0), service.set_zone_volume("z", -30.0))
        _, repeated = await service.set_zone_volume("z", -30.0)

        assert repeated == 0.0
        assert service._state_store.get_client_volume("a") == -25.0
        assert service._state_store.get_client_volume("b") == -35.0

    async def test_a_level_asked_of_a_zone_with_no_room_online_moves_nothing(self, service):
        """No member online means no average to aim at, so nothing moves.

        Consumer: the same route. The average of nobody used to read as the
        -45 default, and a level measured against it would shift every stored
        level by a number nobody set. Fails if anything is written or sent.
        """
        self._zone(service, {"a": -40.0, "b": -50.0}, online=[])
        self._multiroom(service, online=[])

        _, delta = await service.set_zone_volume("z", -30.0)

        await self._settle()
        assert delta == 0.0
        assert service.sent == {}
        assert service._state_store.get_client_volume("a") == -40.0
        assert service._state_store.get_client_volume("b") == -50.0

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
    async def test_a_move_reaches_the_clients_the_screens_count(
        self, service, mock_snapcast_service
    ):
        """Reachable means available in the volume state — the set every average
        on screen is computed over — and snapserver is never asked.

        A level aimed at a slider is measured against the average under the
        thumb; measuring it over another set of clients moved the rooms by a
        delta nobody asked for.
        """
        service.set_routing_service(
            Mock(get_state=Mock(return_value={'multiroom_enabled': True}))
        )
        for mac, available in (("aa:bb", True), ("cc:dd", True), ("ee:ff", False)):
            service._state_store._clients[mac] = ClientVolume(
                volume_db=-40.0, offset_db=0.0, mute=False, available=available
            )

        members, reachable = service._global_members()

        assert members == ["aa:bb", "cc:dd", "ee:ff"]
        assert reachable == ["aa:bb", "cc:dd"]
        mock_snapcast_service.get_clients.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_client_that_is_not_available_is_not_driven(self, service):
        """An unavailable client moves in the store only; nothing is sent to it."""
        service.set_routing_service(
            Mock(get_state=Mock(return_value={'multiroom_enabled': True}))
        )
        service._state_store._clients["aa:bb"] = ClientVolume(
            volume_db=-40.0, offset_db=0.0, mute=False, available=False
        )
        assert service._global_members() == (["aa:bb"], [])

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
        sent = {}

        def submit(mac_id, volume_db, force=False):
            sent[mac_id] = volume_db
            answer = asyncio.get_running_loop().create_future()
            answer.set_result(True)
            return answer

        service._equalizer_controller = Mock(submit_volume=Mock(side_effect=submit))
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
        assert sent == {mac: -42.0}

    @pytest.mark.asyncio
    async def test_a_refused_boot_push_fails_and_keeps_the_level_asked_for(self, service):
        """A client that refused the boot push is reported, and stored at the
        level it was sent — the level its own cache will apply at its reconnect.

        Consumer: the multiroom boot sync. The store is written before the send,
        as every move does, so a move landing while the push is in flight is
        never overwritten with the push's older value. Fails if a refusal stops
        failing the push.
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
        def submit(mac_id, volume_db, force=False):
            answer = asyncio.get_running_loop().create_future()
            answer.set_result(mac_id == took)
            return answer

        service._equalizer_controller = Mock(submit_volume=Mock(side_effect=submit))
        service._equalizer_controller.set_equalizer_mute = AsyncMock()
        service.broadcast_volume_state = AsyncMock()

        result = await service.push_volume_to_all_clients()

        # Non-triviality first: @handle_errors(default=False) makes False the crash
        # value too, so the refusal below is only meaningful once the push has run.
        assert service._equalizer_controller.submit_volume.call_count == 2
        assert result is False                                            # one client refused
        assert service._state_store.get_client_volume(took) == -30.0      # took the push
        assert service._state_store.get_client_volume(refused) == -30.0   # kept as asked

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
        # Untrimmed, so the DAC flip has no level trim to clear — that branch is
        # covered in test_volume_dac_and_boot.py::TestDacMode.
        from backend.core.multiroom.models import Client
        service._client_registry.get_client = Mock(return_value=Client(
            mac_id=mac, name="Main", ip="127.0.0.1"
        ))
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
            equalizer_client_proxy_service=mock_proxy_service,
            equalizer_router=EqualizerRouter(
                client_registry=None,
                camilladsp_service=mock_camilladsp_service,
                proxy_service=mock_proxy_service,
            ),
        )
        svc._state_store.ensure_local_client("aa:bb:cc:dd:ee:ff", -60.0)
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

        for target in range(-60, -50):
            await service.set_volume_db(float(target))
        await service._flush_startup_volume()

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
        """Create VolumeService with mocks.

        The startup level reaches the daemon through the real controller and
        router, as in production, so these tests watch the daemon itself.
        """
        return VolumeService(
            state_machine=mock_state_machine,
            snapcast_service=mock_snapcast_service,
            settings_service=mock_settings,
            camilladsp_service=mock_camilladsp_service,
            equalizer_client_proxy_service=mock_proxy_service,
            equalizer_router=EqualizerRouter(
                client_registry=None,
                camilladsp_service=mock_camilladsp_service,
                proxy_service=mock_proxy_service,
            ),
        )

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
# Concurrent Moves
# ============================================================================

class TestConcurrentMovesKeepEveryStep:
    """Moves in flight together all land, and none of them waits on a satellite.

    Scenario: BT remote presses, the rotary and a phone at once in multiroom,
    with a satellite that is slow to answer. The store used to be written only
    after the fan-out answered, outside the lock, so every move in flight read
    the same levels: ten +2 dB steps together landed as one. And each move
    waited for the slowest satellite before returning, so the rotary's next
    batch did too.

    The satellite here never answers until the moves have all returned — that
    they return at all is the second half of the assertion.
    """

    LOCAL = "local-mac"
    SATELLITE = "satellite-mac"

    @pytest.fixture
    def mock_registry(self):
        registry = Mock()
        registry.get_online_client_ids = Mock(
            return_value=[TestConcurrentMovesKeepEveryStep.LOCAL, TestConcurrentMovesKeepEveryStep.SATELLITE]
        )
        registry.get_client = Mock(return_value=Mock(volume_control=True))
        registry.is_client_online = Mock(return_value=True)
        registry.get_all_zones = Mock(return_value={})
        registry.subscribe = Mock()
        return registry

    @pytest.fixture
    def satellite_gate(self):
        return asyncio.Event()

    @pytest.fixture
    def service(self, mock_registry, satellite_gate):
        settings = Mock()
        settings.invalidate_cache = Mock()
        settings.get_setting = AsyncMock(return_value=None)
        settings.set_setting = AsyncMock()
        svc = VolumeService(
            state_machine=Mock(broadcast=AsyncMock()),
            snapcast_service=Mock(),
            settings_service=settings,
            camilladsp_service=Mock(is_volume_control_available=Mock(return_value=True)),
        )
        svc._volume_config = VolumeConfig(
            limit_min_db=-80.0, limit_max_db=0.0,
            startup_volume_db=-40.0, restore_last_volume=False,
        )
        svc._state_store.set_volume_config(svc._volume_config)
        svc._routing_service = Mock(get_state=Mock(return_value={'multiroom_enabled': True}))
        svc._client_registry = mock_registry
        svc._state_store.set_registry(mock_registry)
        svc._equalizer_controller.set_registry(mock_registry)
        svc._state_store._local_mac_id = self.LOCAL
        svc._state_store._clients = {
            self.LOCAL: ClientVolume(volume_db=-40.0, offset_db=0.0, mute=False, available=True),
            self.SATELLITE: ClientVolume(volume_db=-40.0, offset_db=0.0, mute=False, available=True),
        }
        svc.satellite_received = []

        async def set_volume(mac_id, volume_db, force=False):
            if mac_id == self.SATELLITE:
                await satellite_gate.wait()
                svc.satellite_received.append(volume_db)
            return {"status": "success"}

        svc._equalizer_controller._router = Mock(set_volume=set_volume)
        return svc

    async def _release(self, service, gate):
        gate.set()
        for _ in range(10):
            await asyncio.sleep(0)

    @pytest.mark.asyncio
    async def test_rapid_volume_changes_keep_every_step(self, service, satellite_gate):
        """Five BT-remote presses in flight at once move the house by five steps.

        Launched concurrently on purpose: five sequential `await`s cannot overlap
        at all, so they could not fail on the regression.
        """
        results = await asyncio.gather(*[service.adjust_volume_db(2.0) for _ in range(5)])

        assert all(results), f"Expected all True, got {results}"
        assert service._state_store.get_client_volume(self.LOCAL) == -30.0
        assert service._state_store.get_client_volume(self.SATELLITE) == -30.0

        await self._release(service, satellite_gate)
        assert service.satellite_received[-1] == -30.0
        assert len(service.satellite_received) <= 2, "a burst reaches a speaker as at most two commands"

    @pytest.mark.asyncio
    async def test_concurrent_volume_sources_keep_every_step(self, service, satellite_gate):
        """BT remote + rotary + a phone step, all three in flight together."""
        results = await asyncio.gather(
            service.adjust_volume_db(2.0),
            service.adjust_volume_db(1.0),
            service.adjust_volume_db(-1.0),
        )

        assert all(results), f"Expected all True, got {results}"
        assert service._state_store.get_client_volume(self.SATELLITE) == -38.0

        await self._release(service, satellite_gate)
        assert service.satellite_received[-1] == -38.0

    @pytest.mark.asyncio
    async def test_a_zone_move_and_a_global_move_both_land(self, service, mock_registry, satellite_gate):
        """A zone delta and a global adjust in flight together both count.

        Each once measured its delta against levels the other had not written
        yet, and whichever wrote last erased the other.
        """
        from backend.core.volume.state import ZoneConfig
        zone = ZoneConfig(zone_id="zone-1", name="Test", client_ids=[self.LOCAL, self.SATELLITE])
        service._state_store._zones = {"zone-1": zone}
        # get_complete_state() reloads zones from the registry, wiping any the
        # test planted directly; the global move's broadcast goes through it.
        mock_registry.get_all_zones.return_value = {"zone-1": zone}

        results = await asyncio.gather(
            service.apply_zone_volume_delta("zone-1", 3.0),
            service.adjust_volume_db(2.0),
        )

        assert isinstance(results[0], tuple)
        assert results[1] is True
        assert service._state_store.get_client_volume(self.LOCAL) == -35.0
        assert service._state_store.get_client_volume(self.SATELLITE) == -35.0

        await self._release(service, satellite_gate)
        assert service.satellite_received[-1] == -35.0

class TestPerClientApplyVerdict:
    """A level an online client refused is reported as refused, and kept as asked.

    `PATCH /api/volume/client/mac/{mac}` answers 502 when the speaker refused,
    never 200. The store keeps the level asked for — which is what the
    speaker will play: a satellite caches the value before calling its
    CamillaDSP and applies it when the daemon comes back, and the local unit is
    re-applied from the store on reconnect. Keeping the old value instead is
    what made the server show a level the satellite was about to leave.
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
    async def test_an_online_client_that_refused_keeps_the_level_asked_for(self, service):
        """The refusal decides the verdict; the store keeps the level asked for."""
        assert await service.update_client_volume_db(self.REFUSING, -25.0) is False

        assert service.state_store.get_client_volume(self.REFUSING) == -25.0
        assert service.state_store.get_client_volume(self.ACCEPTING) == -40.0

    @pytest.mark.asyncio
    async def test_the_broadcast_carries_the_level_the_speaker_will_play(
        self, service, mock_state_machine
    ):
        """volume_changed shows the level the refusing satellite applies at its reconnect."""
        await service.update_client_volume_db(self.REFUSING, -25.0)

        mock_state_machine.broadcast.assert_awaited()
        event = mock_state_machine.broadcast.await_args_list[-1].args[0]
        assert event.state["clients"][self.REFUSING]["volume_db"] == -25.0

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
    async def test_an_online_client_that_refused_the_mute_keeps_the_mute_asked_for(
        self, service, caplog
    ):
        """Mute travels the same path and answers the same way."""
        assert await service.set_client_mute(self.REFUSING, True) is False

        assert service.state_store.get_client_mute(self.REFUSING) is True

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
    can carry it with no hardware and no replay queue. A *reachable* client that
    refused is sent the level and keeps it as asked, like its own cache does —
    the other half of each test here.
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

        def submit(mac_id, volume, force=False):
            attempted[mac_id] = volume
            answer = asyncio.get_running_loop().create_future()
            answer.set_result(mac_id != TestAbsentClientKeepsItsPlaceInTheRoom.REFUSING)
            return answer

        controller.submit_volume = Mock(side_effect=submit)
        controller.set_equalizer_volume = AsyncMock(side_effect=apply)
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
    async def test_a_zone_delta_reaches_a_member_that_refuses_and_keeps_its_level(self, zoned):
        """Reaching a speaker and being refused is not the same as not reaching it:
        it is sent the level, and keeps it as asked for its reconnect."""
        await zoned.apply_zone_volume_delta('salon', -6.0)

        assert zoned.state_store.get_client_volume(self.ONLINE) == -36.0
        assert zoned.state_store.get_client_volume(self.REFUSING) == -46.0
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
    async def test_a_global_delta_reaches_a_client_that_refuses_and_keeps_its_level(self, service):
        """A client the move reached and that refused keeps the level asked for."""
        await service.set_volume_db(-41.0)

        assert service.state_store.get_client_volume(self.ONLINE) == -36.0
        assert service.state_store.get_client_volume(self.REFUSING) == -46.0
        assert service.equalizer_controller.attempted[self.REFUSING] == -46.0


class TestEqualizerControllerRegistryInjection:
    """`EqualizerController.set_registry` — the injection `VolumeService` does.

    `test_service_wiring` only proves a production caller exists, never that
    the injection lands. What the registry decides at the door is whether a
    refusal is news: a speaker that went offline during the call is not
    refusing anything, and reporting it would put an error banner on screen
    each time a satellite is switched off mid-gesture.
    """

    @pytest.fixture
    def controller(self):
        router = Mock(set_volume=AsyncMock(return_value={"status": "error", "message": "refused"}))
        return EqualizerController(equalizer_router=router)

    async def test_without_a_registry_a_refusal_is_reported(self, controller, caplog):
        with caplog.at_level(logging.ERROR):
            assert await controller.set_equalizer_volume("aa:bb", -20.0) is False

        assert "Volume not applied to aa:bb" in caplog.text

    async def test_once_injected_an_offline_speaker_s_refusal_is_not_reported(self, controller, caplog):
        controller.set_registry(Mock(is_client_online=Mock(return_value=False)))

        with caplog.at_level(logging.ERROR):
            assert await controller.set_equalizer_volume("aa:bb", -20.0) is False

        assert caplog.text == ""
