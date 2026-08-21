"""
Integration tests for reconnection scenarios.

When a snapclient reconnects, the backend must decide which volume and which EQ
to push to it, and the answer depends on the client's context: in a zone or
standalone, with other members online or alone. These tests drive
SnapcastWebSocketService end to end for each of the four contexts — detection,
the volume it resolves, the EQ it re-pushes, and the pending-settings queue for
a client that was offline when a change was made.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.tests.conftest import attach_registry_broadcaster
from backend.core.models.volume import VolumeConfig
from backend.core.multiroom.models import ReconnectionContext


class TestReconnectionContextDetectionIntegration:
    """
    Integration tests for reconnection context detection.

    These tests validate the end-to-end flow of context detection when
    a client reconnects via Snapcast WebSocket events.
    """

    @pytest.fixture
    def mock_settings_service(self):
        """Create a mock settings service."""
        service = AsyncMock()
        service.get_setting = AsyncMock(return_value=None)
        service.set_setting = AsyncMock()
        return service

    @pytest.fixture
    def mock_state_machine(self):
        """Create a mock state machine."""
        sm = MagicMock()
        sm.broadcast = AsyncMock()
        sm.snapcast_service = None
        sm.volume_service = None
        sm.crossover_service = None
        sm.equalizer_client_proxy_service = None
        sm.equalizer_settings_sync_service = None
        sm.camilladsp_service = None
        return sm

    @pytest.mark.asyncio
    async def test_context_detection_e2e_in_zone_others_online(
        self, mock_settings_service, mock_state_machine
    ):
        """
        E2E: Client in zone reconnects with others online - detects IN_ZONE_OTHERS_ONLINE.

        Scenario:
        1. Zone with 3 clients: local, client-1 (online), client-2 (offline)
        2. local reconnects
        3. Context should be IN_ZONE_OTHERS_ONLINE
        """
        from backend.core.multiroom.client_registry import ClientRegistryService

        # Setup registry with zone
        registry = ClientRegistryService(
            settings_service=mock_settings_service
        )
        await registry.initialize()
        attach_registry_broadcaster(registry, mock_state_machine)

        # Register clients and create zone
        await registry.register_client("local", "Main", "127.0.0.1")
        await registry.register_client("milo-client-01", "Client 1", "192.168.1.100")
        await registry.register_client("milo-client-02", "Client 2", "192.168.1.101")
        await registry.create_zone("zone-1", "Test Zone", ["local", "milo-client-01", "milo-client-02"])

        # Set online status: client-1 online, others offline
        await registry.set_client_online("local", False)  # Reconnecting
        await registry.set_client_online("milo-client-01", True)  # Online zone member
        await registry.set_client_online("milo-client-02", False)  # Offline

        # Detect context
        context = registry.get_reconnection_context("local")

        # Should be IN_ZONE_OTHERS_ONLINE
        assert context == ReconnectionContext.IN_ZONE_OTHERS_ONLINE

    @pytest.mark.asyncio
    async def test_context_detection_e2e_in_zone_all_offline(
        self, mock_settings_service, mock_state_machine
    ):
        """
        E2E: Client in zone reconnects with all others offline - detects IN_ZONE_ALL_OFFLINE.

        Scenario:
        1. Zone with 3 clients, all offline (e.g., after backend restart)
        2. local reconnects first
        3. Context should be IN_ZONE_ALL_OFFLINE
        """
        from backend.core.multiroom.client_registry import ClientRegistryService

        registry = ClientRegistryService(
            settings_service=mock_settings_service
        )
        await registry.initialize()
        attach_registry_broadcaster(registry, mock_state_machine)

        # Register clients and create zone
        await registry.register_client("local", "Main", "127.0.0.1")
        await registry.register_client("milo-client-01", "Client 1", "192.168.1.100")
        await registry.register_client("milo-client-02", "Client 2", "192.168.1.101")
        await registry.create_zone("zone-1", "Test Zone", ["local", "milo-client-01", "milo-client-02"])

        # All clients offline
        await registry.set_client_online("local", False)
        await registry.set_client_online("milo-client-01", False)
        await registry.set_client_online("milo-client-02", False)

        # Detect context - local reconnects first
        context = registry.get_reconnection_context("local")

        # Should be IN_ZONE_ALL_OFFLINE
        assert context == ReconnectionContext.IN_ZONE_ALL_OFFLINE

    @pytest.mark.asyncio
    async def test_context_detection_e2e_standalone_others_online(
        self, mock_settings_service, mock_state_machine
    ):
        """
        E2E: Standalone client reconnects with others online - detects STANDALONE_OTHERS_ONLINE.

        Scenario:
        1. 3 standalone clients (no zones)
        2. client-1, client-2 online
        3. local reconnects
        4. Context should be STANDALONE_OTHERS_ONLINE
        """
        from backend.core.multiroom.client_registry import ClientRegistryService

        registry = ClientRegistryService(
            settings_service=mock_settings_service
        )
        await registry.initialize()
        attach_registry_broadcaster(registry, mock_state_machine)

        # Register standalone clients (no zones)
        await registry.register_client("local", "Main", "127.0.0.1")
        await registry.register_client("milo-client-01", "Client 1", "192.168.1.100")
        await registry.register_client("milo-client-02", "Client 2", "192.168.1.101")

        # Set online status: others online
        await registry.set_client_online("local", False)  # Reconnecting
        await registry.set_client_online("milo-client-01", True)
        await registry.set_client_online("milo-client-02", True)

        # Detect context
        context = registry.get_reconnection_context("local")

        # Should be STANDALONE_OTHERS_ONLINE
        assert context == ReconnectionContext.STANDALONE_OTHERS_ONLINE

    @pytest.mark.asyncio
    async def test_context_detection_e2e_standalone_alone(
        self, mock_settings_service, mock_state_machine
    ):
        """
        E2E: Standalone client reconnects alone - detects STANDALONE_ALONE.

        Scenario:
        1. 3 standalone clients, all offline (e.g., after backend restart)
        2. local reconnects first
        3. Context should be STANDALONE_ALONE
        """
        from backend.core.multiroom.client_registry import ClientRegistryService

        registry = ClientRegistryService(
            settings_service=mock_settings_service
        )
        await registry.initialize()
        attach_registry_broadcaster(registry, mock_state_machine)

        # Register standalone clients (no zones)
        await registry.register_client("local", "Main", "127.0.0.1")
        await registry.register_client("milo-client-01", "Client 1", "192.168.1.100")
        await registry.register_client("milo-client-02", "Client 2", "192.168.1.101")

        # All offline
        await registry.set_client_online("local", False)
        await registry.set_client_online("milo-client-01", False)
        await registry.set_client_online("milo-client-02", False)

        # Detect context - local reconnects first
        context = registry.get_reconnection_context("local")

        # Should be STANDALONE_ALONE
        assert context == ReconnectionContext.STANDALONE_ALONE

    @pytest.mark.asyncio
    async def test_zone_member_transition_context_change(
        self, mock_settings_service, mock_state_machine
    ):
        """
        E2E: Context changes when client joins/leaves zone.

        Validates that context detection is dynamic based on current state.
        """
        from backend.core.multiroom.client_registry import ClientRegistryService

        registry = ClientRegistryService(
            settings_service=mock_settings_service
        )
        await registry.initialize()
        attach_registry_broadcaster(registry, mock_state_machine)

        # Register 3 clients
        await registry.register_client("local", "Main", "127.0.0.1")
        await registry.register_client("milo-client-01", "Client 1", "192.168.1.100")
        await registry.register_client("milo-client-02", "Client 2", "192.168.1.101")

        # All offline initially
        await registry.set_client_online("local", False)
        await registry.set_client_online("milo-client-01", True)
        await registry.set_client_online("milo-client-02", False)

        # Step 1: local is standalone, client-1 online
        context1 = registry.get_reconnection_context("local")
        assert context1 == ReconnectionContext.STANDALONE_OTHERS_ONLINE

        # Step 2: Create zone - local joins with client-1
        await registry.create_zone("zone-1", "Test Zone", ["local", "milo-client-01"])

        # Step 3: local is now IN_ZONE with client-1 online
        context2 = registry.get_reconnection_context("local")
        assert context2 == ReconnectionContext.IN_ZONE_OTHERS_ONLINE

        # Step 4: client-1 goes offline
        await registry.set_client_online("milo-client-01", False)

        # Step 5: local is still IN_ZONE but all others offline
        context3 = registry.get_reconnection_context("local")
        assert context3 == ReconnectionContext.IN_ZONE_ALL_OFFLINE

        # Step 6: Remove local from zone
        await registry.remove_client_from_zone("zone-1", "local")

        # Step 7: local is back to STANDALONE_ALONE (no one online)
        context4 = registry.get_reconnection_context("local")
        assert context4 == ReconnectionContext.STANDALONE_ALONE


# =============================================================================
# IN_ZONE Reconnection Sync Integration Tests
# =============================================================================


class TestInZoneReconnectionSyncIntegration:
    """
    Integration tests for IN_ZONE reconnection volume sync.

    These tests validate the end-to-end flow of volume sync when
    a client in a zone reconnects via Snapcast WebSocket events.
    """

    @pytest.fixture
    def mock_settings_service(self):
        """Create a mock settings service."""
        service = AsyncMock()
        service.get_setting = AsyncMock(return_value=None)
        service.set_setting = AsyncMock()
        return service

    @pytest.fixture
    def mock_state_machine(self):
        """Create a mock state machine with volume_service."""
        sm = MagicMock()
        sm.broadcast = AsyncMock()
        sm.snapcast_service = None
        sm.crossover_service = None
        sm.equalizer_client_proxy_service = None
        sm.equalizer_settings_sync_service = None
        sm.camilladsp_service = None

        # Mock volume service with config and state store
        volume_service = AsyncMock()
        volume_service.volume_config = VolumeConfig(startup_volume_db=-45.0)
        volume_service.update_client_volume_db = AsyncMock()
        volume_service.broadcast_volume_state = AsyncMock()
        # Mock _state_store._clients to return proper client state objects
        mock_client_state = MagicMock()
        mock_client_state.mute = False
        volume_service.state_store._clients = {
            "client-1": mock_client_state,
            "client-2": mock_client_state,
            "client-3": mock_client_state,
        }
        # The per-client levels VolumeStateStore holds — what an admission now
        # resolves to. Mutable so a test can state its own scenario.
        volume_service.stored_volumes = {
            "client-1": -20.0, "client-2": -30.0, "client-3": -40.0,
        }
        volume_service.state_store.get_client_volume = MagicMock(
            side_effect=volume_service.stored_volumes.get
        )
        volume_service.state_store.get_client_mute = MagicMock(return_value=False)
        volume_service.equalizer_controller = AsyncMock()
        sm.volume_service = volume_service

        return sm

    @pytest.mark.asyncio
    async def test_in_zone_others_online_uses_the_clients_own_level(
        self, mock_settings_service, mock_state_machine
    ):
        """
        E2E: a member rejoining a zone comes back at its own level.

        Scenario:
        1. Zone with 3 clients at volumes: -20, -30, -40
        2. client-1 offline, client-2 and client-3 online (-30, -40)
        3. client-1 reconnects
        4. client-1 receives its own -20, not the room's -35
        """
        from backend.core.multiroom.client_registry import ClientRegistryService
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        # Setup registry
        registry = ClientRegistryService(
            settings_service=mock_settings_service
        )
        await registry.initialize()
        attach_registry_broadcaster(registry, mock_state_machine)

        # Register clients with volumes
        await registry.register_client("client-1", "Client 1", "192.168.1.1")
        await registry.register_client("client-2", "Client 2", "192.168.1.2")
        await registry.register_client("client-3", "Client 3", "192.168.1.3")

        await registry.update_volume("client-1", volume_db=-20.0)
        await registry.update_volume("client-2", volume_db=-30.0)
        await registry.update_volume("client-3", volume_db=-40.0)

        # Create zone
        await registry.create_zone("zone-1", "Test Zone", ["client-1", "client-2", "client-3"])

        # Set online status: client-1 reconnecting, others online
        await registry.set_client_online("client-1", False)
        await registry.set_client_online("client-2", True)
        await registry.set_client_online("client-3", True)

        # Create websocket service
        mock_snapcast = AsyncMock()
        mock_snapcast.set_volume = AsyncMock()
        mock_state_machine.snapcast_service = mock_snapcast

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock(),
        )
        ws_service.set_registry(registry)
        ws_service._snapcast_service = mock_snapcast
        ws_service._volume_service = mock_state_machine.volume_service

        # Mock Equalizer sync to avoid errors
        ws_service._sync_standalone_equalizer_to_client = AsyncMock(return_value=True)

        target_volume = ws_service._resolve_target_volume("client-1")

        # Its own stored level, whatever the online members average to
        assert target_volume == -20.0
        assert target_volume != (-30.0 + -40.0) / 2

    @pytest.mark.asyncio
    async def test_in_zone_restore_disabled_uses_startup_volume(
        self, mock_settings_service, mock_state_machine
    ):
        """
        E2E: with `restore_last_volume` off, a zone member ignores its own level.

        The escape hatch for a fleet that must start at a fixed level: the
        stored value is not read at all, so the boot push and the admission
        cannot disagree about what a client comes back at.

        Scenario:
        1. Zone with 3 clients, all offline (backend restart)
        2. restore_last_volume is off, client-1 has a stored -20
        3. client-1 reconnects and receives startup_volume_db (-45.0)
        """
        from backend.core.multiroom.client_registry import ClientRegistryService
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        # Setup registry
        registry = ClientRegistryService(
            settings_service=mock_settings_service
        )
        await registry.initialize()
        attach_registry_broadcaster(registry, mock_state_machine)

        # Register clients
        await registry.register_client("client-1", "Client 1", "192.168.1.1")
        await registry.register_client("client-2", "Client 2", "192.168.1.2")
        await registry.register_client("client-3", "Client 3", "192.168.1.3")

        # Create zone
        await registry.create_zone("zone-1", "Test Zone", ["client-1", "client-2", "client-3"])

        # All clients offline
        await registry.set_client_online("client-1", False)
        await registry.set_client_online("client-2", False)
        await registry.set_client_online("client-3", False)

        # Create websocket service
        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock(),
        )
        ws_service.set_registry(registry)
        ws_service._volume_service = mock_state_machine.volume_service
        ws_service._volume_service.volume_config = VolumeConfig(
            startup_volume_db=-45.0, restore_last_volume=False
        )

        target_volume = ws_service._resolve_target_volume("client-1")

        # Should use startup_volume_db from config, and never read the store
        assert target_volume == -45.0
        store = mock_state_machine.volume_service.state_store
        store.get_client_volume.assert_not_called()

    @pytest.mark.asyncio
    async def test_zone_average_excludes_reconnecting_client(
        self, mock_settings_service, mock_state_machine
    ):
        """
        E2E: Zone average calculation excludes the reconnecting client.

        Validates that the reconnecting client's old volume doesn't influence
        the average they receive on reconnection.
        """
        from backend.core.multiroom.client_registry import ClientRegistryService

        registry = ClientRegistryService(
            settings_service=mock_settings_service
        )
        await registry.initialize()

        # Register clients with volumes
        await registry.register_client("client-1", "Client 1", "192.168.1.1")
        await registry.register_client("client-2", "Client 2", "192.168.1.2")
        await registry.register_client("client-3", "Client 3", "192.168.1.3")

        await registry.update_volume("client-1", volume_db=-10.0)  # Reconnecting
        await registry.update_volume("client-2", volume_db=-30.0)  # Online
        await registry.update_volume("client-3", volume_db=-50.0)  # Online

        # Create zone
        await registry.create_zone("zone-1", "Test Zone", ["client-1", "client-2", "client-3"])

        # client-1 reconnecting, others online
        await registry.set_client_online("client-1", True)  # Marked online for test
        await registry.set_client_online("client-2", True)
        await registry.set_client_online("client-3", True)

        # Get zone average excluding client-1 (simulating reconnection scenario)
        avg = registry.get_zone_average_volume("zone-1", exclude_mac_id="client-1")

        # Should be average of client-2 and client-3 only
        # (-30 + -50) / 2 = -40
        assert avg == -40.0

        # Without exclusion, would be (-10 + -30 + -50) / 3 = -30
        avg_all = registry.get_zone_average_volume("zone-1")
        assert avg_all == -30.0

    @pytest.mark.asyncio
    async def test_websocket_broadcast_after_sync(
        self, mock_settings_service, mock_state_machine
    ):
        """
        E2E: WebSocket broadcast is sent after volume sync completes.
        """
        from backend.core.multiroom.client_registry import ClientRegistryService
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        # Setup registry
        registry = ClientRegistryService(
            settings_service=mock_settings_service
        )
        await registry.initialize()
        attach_registry_broadcaster(registry, mock_state_machine)

        # Register client in zone
        await registry.register_client("client-1", "Client 1", "192.168.1.1")
        await registry.register_client("client-2", "Client 2", "192.168.1.2")
        await registry.update_volume("client-2", volume_db=-30.0)

        await registry.create_zone("zone-1", "Test Zone", ["client-1", "client-2"])
        await registry.set_client_online("client-1", False)
        await registry.set_client_online("client-2", True)

        # Create websocket service with mocks
        mock_snapcast = AsyncMock()
        mock_snapcast.set_volume = AsyncMock()
        mock_state_machine.snapcast_service = mock_snapcast

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock(),
        )
        ws_service.set_registry(registry)
        ws_service._snapcast_service = mock_snapcast
        ws_service._volume_service = mock_state_machine.volume_service
        ws_service._sync_standalone_equalizer_to_client = AsyncMock(return_value=True)

        # Mock _state_store._clients to return a proper client state object
        volume_service = mock_state_machine.volume_service
        mock_client_state = MagicMock()
        mock_client_state.mute = False
        volume_service.state_store._clients = {"client-1": mock_client_state}
        volume_service.equalizer_controller = AsyncMock()

        await ws_service._sync_reconnecting_client_volume(
            "client-1", max_retries=0, retry_delay=0, snapcast_id="snapcast-client-123"
        )

        # Verify broadcast was called
        volume_service.broadcast_volume_state.assert_called()

    @pytest.mark.asyncio
    async def test_equalizer_sync_uses_member_records(
        self, mock_settings_service, mock_state_machine
    ):
        """
        E2E: zone EQ derives from members — each member owns its EQ record, and the
        zone holds no EQ of its own (unified per-client model).
        """
        from backend.core.multiroom.client_registry import ClientRegistryService
        from backend.core.multiroom.models import EqualizerSettings, EqFilter, FilterType

        registry = ClientRegistryService(
            settings_service=mock_settings_service
        )
        await registry.initialize()

        await registry.register_client("client-1", "Client 1", "192.168.1.1")
        await registry.register_client("client-2", "Client 2", "192.168.1.2")
        await registry.create_zone("zone-1", "Test Zone", ["client-1", "client-2"])

        # The access layer applies the (identical) zone EQ to each member's record.
        equalizer_settings = EqualizerSettings(
            enabled=True,
            filters=[EqFilter(id="eq_band_00", frequency=1000, gain=5.0, filter_type=FilterType.PEAKING)]
        )
        await registry.set_client_equalizer("client-1", equalizer_settings)

        # The zone holds no EQ of its own; the member's record is the source.
        zone = registry.get_zone("zone-1")
        assert not hasattr(zone, "equalizer_settings")
        member_eq = registry.get_client_equalizer("client-1")
        assert member_eq is not None
        assert member_eq.enabled is True
        assert len(member_eq.filters) == 1
        assert member_eq.filters[0].gain == 5.0


# =============================================================================
# - Sync Time Compliance Tests
# =============================================================================


# =============================================================================
# - Pending Settings Queue Tests
# =============================================================================


class TestPendingSettingsQueue:
    """
    Tests for Pending Settings Handling.

    Validates that failed Equalizer settings are queued via queue_pending_settings()
    for later retry when sync fails.
    """

    @pytest.fixture
    def mock_settings_service(self):
        """Create a mock settings service."""
        service = AsyncMock()
        service.get_setting = AsyncMock(return_value=None)
        service.set_setting = AsyncMock()
        return service

    @pytest.fixture
    def mock_state_machine_with_crossover(self):
        """Create a mock state machine with crossover/proxy services for pending settings."""
        sm = MagicMock()
        sm.broadcast = AsyncMock()

        # Mock crossover service with queue_pending_settings
        crossover = AsyncMock()
        crossover.queue_pending_settings = AsyncMock()
        crossover.has_pending_settings = MagicMock(return_value=False)
        sm.crossover_service = crossover

        # Mock Equalizer proxy that will fail
        proxy = AsyncMock()
        proxy.request = AsyncMock(side_effect=Exception("Connection refused"))
        proxy.apply_record = AsyncMock(return_value=False)
        sm.equalizer_client_proxy_service = proxy

        return sm

    @pytest.mark.asyncio
    async def test_a_failed_sync_queues_the_record(
        self, mock_settings_service, mock_state_machine_with_crossover
    ):
        """A satellite that cannot be reached has its whole EQ record requeued.

        One record, one queue entry: replaying it is idempotent and converges the
        client in one shot, where the per-setting queue this replaced could leave
        a satellite with some settings applied and some not.
        """
        from backend.core.multiroom.client_registry import ClientRegistryService
        from backend.core.multiroom.websocket import SnapcastWebSocketService
        from backend.core.multiroom.models import (
            CompressorSettings, EqFilter, EqualizerSettings, FilterType,
        )

        registry = ClientRegistryService(settings_service=mock_settings_service)
        await registry.initialize()
        attach_registry_broadcaster(registry, mock_state_machine_with_crossover)
        await registry.register_client("client-1", "Client 1", "192.168.1.100")
        await registry.register_client("client-2", "Client 2", "192.168.1.101")

        record = EqualizerSettings(
            enabled=True,
            filters=[EqFilter(id="eq_band_00", frequency=100, gain=3.0, filter_type=FilterType.PEAKING)],
            compressor=CompressorSettings(enabled=True, threshold=-20.0, ratio=4.0),
        )
        await registry.set_client_equalizer("client-1", record)

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine_with_crossover,
            routing_service=MagicMock(),
        )
        ws_service.set_registry(registry)
        ws_service._equalizer_client_proxy_service = mock_state_machine_with_crossover.equalizer_client_proxy_service
        ws_service._crossover_service = mock_state_machine_with_crossover.crossover_service

        result = await ws_service._sync_standalone_equalizer_to_client("client-1")

        assert result is False
        crossover = mock_state_machine_with_crossover.crossover_service
        queued = crossover.queue_pending_settings.await_args
        assert queued.args[:2] == ("client-1", "record")
        assert queued.args[2].compressor.threshold == -20.0

    @pytest.mark.asyncio
    async def test_successful_sync_does_not_queue_settings(
        self, mock_settings_service
    ):
        """
        Successful Equalizer sync does NOT queue any pending settings.
        """
        from backend.core.multiroom.client_registry import ClientRegistryService
        from backend.core.multiroom.websocket import SnapcastWebSocketService
        from backend.core.multiroom.models import EqualizerSettings, EqFilter, FilterType

        # Create state machine with successful proxy
        sm = MagicMock()
        sm.broadcast = AsyncMock()
        sm.camilladsp_service = None
        sm.equalizer_settings_sync_service = None

        # Mock crossover service
        crossover = AsyncMock()
        crossover.queue_pending_settings = AsyncMock()
        sm.crossover_service = crossover

        # Mock Equalizer proxy that succeeds
        proxy = AsyncMock()
        proxy.request = AsyncMock(return_value={"success": True})
        sm.equalizer_client_proxy_service = proxy

        # Setup registry
        registry = ClientRegistryService(
            settings_service=mock_settings_service
        )
        await registry.initialize()
        attach_registry_broadcaster(registry, sm)

        # Register clients
        await registry.register_client("client-1", "Client 1", "192.168.1.100")
        await registry.register_client("client-2", "Client 2", "192.168.1.101")

        # Create zone with Equalizer settings
        equalizer_settings = EqualizerSettings(
            enabled=True,
            filters=[EqFilter(id="eq_band_00", frequency=1000, gain=2.0, filter_type=FilterType.PEAKING)]
        )
        await registry.set_client_equalizer("client-1", equalizer_settings)

        # Create websocket service
        ws_service = SnapcastWebSocketService(
            state_machine=sm,
            routing_service=MagicMock(),
        )
        ws_service.set_registry(registry)
        ws_service._equalizer_client_proxy_service = sm.equalizer_client_proxy_service
        ws_service._crossover_service = sm.crossover_service

        # Call _sync_standalone_equalizer_to_client - should succeed
        result = await ws_service._sync_standalone_equalizer_to_client("client-1")

        # Assert: sync succeeded and nothing was queued
        assert result is True
        crossover.queue_pending_settings.assert_not_called()


# =============================================================================
# STANDALONE Reconnection Sync Integration Tests
# =============================================================================


class TestStandaloneReconnectionSyncIntegration:
    """
    Integration tests for STANDALONE reconnection volume sync.

    These tests validate the end-to-end flow of volume sync when
    a standalone client reconnects via Snapcast WebSocket events.
    """

    @pytest.fixture
    def mock_settings_service(self):
        """Create a mock settings service."""
        service = AsyncMock()
        service.get_setting = AsyncMock(return_value=None)
        service.set_setting = AsyncMock()
        return service

    @pytest.fixture
    def mock_state_machine(self):
        """Create a mock state machine with volume_service."""
        sm = MagicMock()
        sm.broadcast = AsyncMock()
        sm.snapcast_service = None
        sm.crossover_service = None
        sm.equalizer_client_proxy_service = None
        sm.equalizer_settings_sync_service = None
        sm.camilladsp_service = None

        # Mock volume service with config and state store
        volume_service = AsyncMock()
        volume_service.volume_config = VolumeConfig(startup_volume_db=-45.0)
        volume_service.update_client_volume_db = AsyncMock()
        volume_service.broadcast_volume_state = AsyncMock()
        # Mock _state_store._clients to return proper client state objects
        mock_client_state = MagicMock()
        mock_client_state.mute = False
        volume_service.state_store._clients = {
            "local-main": mock_client_state,
            "client-1": mock_client_state,
            "client-2": mock_client_state,
            "client-3": mock_client_state,
        }
        # The per-client levels VolumeStateStore holds — what an admission now
        # resolves to. Mutable so a test can state its own scenario.
        volume_service.stored_volumes = {
            "local-main": -55.0,
            "client-1": -20.0, "client-2": -30.0, "client-3": -40.0,
        }
        volume_service.state_store.get_client_volume = MagicMock(
            side_effect=volume_service.stored_volumes.get
        )
        volume_service.state_store.get_client_mute = MagicMock(return_value=False)
        volume_service.equalizer_controller = AsyncMock()
        sm.volume_service = volume_service

        return sm

    @pytest.mark.asyncio
    async def test_standalone_others_online_uses_the_clients_own_level(
        self, mock_settings_service, mock_state_machine
    ):
        """
        E2E: off-zone the rule is the same — the global average is not a target.

        Scenario:
        1. 3 standalone clients at volumes: -20, -30, -40
        2. client-1 offline, client-2 and client-3 online (-30, -40)
        3. client-1 reconnects
        4. client-1 receives its own -20, not the fleet's -35
        """
        from backend.core.multiroom.client_registry import ClientRegistryService
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        # Setup registry
        registry = ClientRegistryService(
            settings_service=mock_settings_service
        )
        await registry.initialize()
        attach_registry_broadcaster(registry, mock_state_machine)

        # Register standalone clients with volumes (no zone)
        await registry.register_client("client-1", "Client 1", "192.168.1.1")
        await registry.register_client("client-2", "Client 2", "192.168.1.2")
        await registry.register_client("client-3", "Client 3", "192.168.1.3")

        await registry.update_volume("client-1", volume_db=-20.0)
        await registry.update_volume("client-2", volume_db=-30.0)
        await registry.update_volume("client-3", volume_db=-40.0)

        # Set online status: client-1 reconnecting, others online
        await registry.set_client_online("client-1", False)
        await registry.set_client_online("client-2", True)
        await registry.set_client_online("client-3", True)

        # Create websocket service
        mock_snapcast = AsyncMock()
        mock_snapcast.set_volume = AsyncMock()
        mock_state_machine.snapcast_service = mock_snapcast

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock(),
        )
        ws_service.set_registry(registry)
        ws_service._snapcast_service = mock_snapcast
        ws_service._volume_service = mock_state_machine.volume_service

        # Mock Equalizer sync to avoid errors
        ws_service._sync_standalone_equalizer_to_client = AsyncMock(return_value=True)

        target_volume = ws_service._resolve_target_volume("client-1")

        # Its own stored level, whatever the online clients average to
        assert target_volume == -20.0
        assert target_volume != (-30.0 + -40.0) / 2

    @pytest.mark.asyncio
    async def test_a_client_the_store_never_saw_uses_startup_volume(
        self, mock_settings_service, mock_state_machine
    ):
        """
        E2E: a speaker Milō holds no level for gets startup_volume_db.

        Scenario:
        1. A standalone client connecting for the first time
        2. The volume store has no record of it
        3. It receives startup_volume_db (-45.0)
        """
        from backend.core.multiroom.client_registry import ClientRegistryService
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        # Setup registry
        registry = ClientRegistryService(
            settings_service=mock_settings_service
        )
        await registry.initialize()
        attach_registry_broadcaster(registry, mock_state_machine)

        # Register a client the volume store has never seen
        await registry.register_client("brand-new", "Brand New", "192.168.1.9")
        await registry.set_client_online("brand-new", False)

        # Create websocket service
        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock(),
        )
        ws_service.set_registry(registry)
        ws_service._volume_service = mock_state_machine.volume_service

        target_volume = ws_service._resolve_target_volume("brand-new")

        # Should use startup_volume_db from config
        assert target_volume == -45.0

    @pytest.mark.asyncio
    async def test_global_average_excludes_reconnecting_client(
        self, mock_settings_service, mock_state_machine
    ):
        """
        E2E: Global average calculation excludes the reconnecting client.

        Validates that the reconnecting client's old volume doesn't influence
        the average they receive on reconnection.
        """
        from backend.core.multiroom.client_registry import ClientRegistryService

        registry = ClientRegistryService(
            settings_service=mock_settings_service
        )
        await registry.initialize()

        # Register standalone clients with volumes
        await registry.register_client("client-1", "Client 1", "192.168.1.1")
        await registry.register_client("client-2", "Client 2", "192.168.1.2")
        await registry.register_client("client-3", "Client 3", "192.168.1.3")

        await registry.update_volume("client-1", volume_db=-10.0)  # Reconnecting
        await registry.update_volume("client-2", volume_db=-30.0)  # Online
        await registry.update_volume("client-3", volume_db=-50.0)  # Online

        # client-1 reconnecting, others online
        await registry.set_client_online("client-1", True)  # Marked online for test
        await registry.set_client_online("client-2", True)
        await registry.set_client_online("client-3", True)

        # Get global average excluding client-1 (simulating reconnection scenario)
        avg = registry.get_global_average_volume(exclude_mac_id="client-1")

        # Should be average of client-2 and client-3 only
        # (-30 + -50) / 2 = -40
        assert avg == -40.0

        # Without exclusion, would be (-10 + -30 + -50) / 3 = -30
        avg_all = registry.get_global_average_volume()
        assert avg_all == -30.0

    @pytest.mark.asyncio
    async def test_global_average_includes_both_zoned_and_standalone(
        self, mock_settings_service, mock_state_machine
    ):
        """
        E2E: Global average includes BOTH zoned and standalone clients.

        Validates requirement that global average considers all online
        clients regardless of zone membership.
        """
        from backend.core.multiroom.client_registry import ClientRegistryService

        registry = ClientRegistryService(
            settings_service=mock_settings_service
        )
        await registry.initialize()

        # Register 4 clients
        await registry.register_client("zone-client-1", "Zone Client 1", "192.168.1.1")
        await registry.register_client("zone-client-2", "Zone Client 2", "192.168.1.2")
        await registry.register_client("standalone-1", "Standalone 1", "192.168.1.3")
        await registry.register_client("standalone-reconnecting", "Reconnecting", "192.168.1.4")

        # Create zone with 2 clients
        await registry.create_zone("zone-1", "Test Zone", ["zone-client-1", "zone-client-2"])

        # Set all online with volumes
        await registry.set_client_online("zone-client-1", True)
        await registry.set_client_online("zone-client-2", True)
        await registry.set_client_online("standalone-1", True)
        await registry.set_client_online("standalone-reconnecting", False)  # Reconnecting

        await registry.update_volume("zone-client-1", volume_db=-10.0)
        await registry.update_volume("zone-client-2", volume_db=-20.0)
        await registry.update_volume("standalone-1", volume_db=-30.0)
        await registry.update_volume("standalone-reconnecting", volume_db=-50.0)  # Old volume

        # Get global average for reconnecting standalone client
        # Should include zone-client-1, zone-client-2, standalone-1
        avg = registry.get_global_average_volume(exclude_mac_id="standalone-reconnecting")

        # Average of -10, -20, -30 = -20
        assert avg == -20.0

    @pytest.mark.asyncio
    async def test_standalone_equalizer_sync_uses_client_settings(
        self, mock_settings_service, mock_state_machine
    ):
        """
        E2E: a lone reconnecting client is detected as STANDALONE — the context that
        drives its per-client EQ restore (the EQ push itself is covered end-to-end by
        test_multiroom_sync.py::TestReconnectSyncAppliesMonoAndEnabled).
        """
        from backend.core.multiroom.client_registry import ClientRegistryService
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        registry = ClientRegistryService(
            settings_service=mock_settings_service
        )
        await registry.initialize()

        # Register standalone client
        await registry.register_client("client-1", "Client 1", "192.168.1.1")

        # Create websocket service
        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock(),
        )
        ws_service.set_registry(registry)

        # Verify context is STANDALONE
        context = registry.get_reconnection_context("client-1")
        assert context == ReconnectionContext.STANDALONE_ALONE

    @pytest.mark.asyncio
    async def test_websocket_broadcast_includes_sync_context(
        self, mock_settings_service, mock_state_machine
    ):
        """
        E2E: WebSocket broadcast includes sync_context after sync.
        """
        from backend.core.multiroom.client_registry import ClientRegistryService
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        # Setup registry
        registry = ClientRegistryService(
            settings_service=mock_settings_service
        )
        await registry.initialize()
        attach_registry_broadcaster(registry, mock_state_machine)

        # Register standalone clients with explicit MAC-based mac_ids
        await registry.register_client("local-main", "Main", "192.168.1.10")
        await registry.register_client("client-1", "Client 1", "192.168.1.1")
        await registry.set_client_online("local-main", False)
        await registry.set_client_online("client-1", True)
        await registry.update_volume("client-1", volume_db=-30.0)

        # Setup mocks
        mock_snapcast = AsyncMock()
        mock_snapcast.set_volume = AsyncMock()
        mock_state_machine.snapcast_service = mock_snapcast

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock(),
        )
        ws_service.set_registry(registry)
        ws_service._snapcast_service = mock_snapcast
        ws_service._volume_service = mock_state_machine.volume_service
        ws_service._sync_standalone_equalizer_to_client = AsyncMock(return_value=True)

        await ws_service._sync_reconnecting_client_volume(
            "local-main", max_retries=0, retry_delay=0, snapcast_id="local-main"
        )

        # The admission resolves to local-main's own stored level, not to
        # client-1's -30 dB and not to the startup volume.
        applied = ws_service._volume_service.equalizer_controller.set_equalizer_volume
        assert applied.await_args.args[1] == -55.0



class TestWhatAReconnectReplays:
    """Which of a client's stored values survive its reconnection.

    Both of them, since the volume-ownership plan's phase 1 — and the pair is
    kept together because they used to answer oppositely. `PATCH
    /api/volume/client/mac/{mac}` on an offline client once recorded a level
    nothing would ever apply, while `/mute` recorded one the reconnect did
    re-apply; that asymmetry is what made the route's log line — *"will be
    applied on reconnection"* — false for half of what it covered (sweep
    finding S17). The volume now follows the mute.

    If the first test goes red, someone made a peer reading the reconnection
    target again: a client that comes back at a level nobody set for it is the
    failure both of these exist to catch.
    """

    @pytest.fixture
    def mock_settings_service(self):
        service = AsyncMock()
        service.get_setting = AsyncMock(return_value=None)
        service.set_setting = AsyncMock()
        return service

    @pytest.fixture
    def mock_state_machine(self):
        sm = MagicMock()
        sm.broadcast = AsyncMock()
        sm.snapcast_service = None
        sm.crossover_service = None
        sm.equalizer_client_proxy_service = None
        sm.equalizer_settings_sync_service = None
        sm.camilladsp_service = None

        volume_service = AsyncMock()
        volume_service.volume_config = VolumeConfig(startup_volume_db=-45.0)
        volume_service.broadcast_volume_state = AsyncMock()
        # The stored per-client values, deliberately unlike every peer level
        # below so a resolver that read them could not coincide with one.
        volume_service.state_store.get_client_volume = MagicMock(return_value=-70.0)
        volume_service.state_store.get_client_mute = MagicMock(return_value=True)
        volume_service.state_store.set_client_volume = AsyncMock()
        volume_service.equalizer_controller = AsyncMock()
        volume_service.equalizer_controller.set_equalizer_volume = AsyncMock(return_value=True)
        volume_service.equalizer_controller.set_equalizer_mute = AsyncMock(return_value=True)
        sm.volume_service = volume_service
        return sm

    async def _zone_with_two_peers_online(self, mock_settings_service, mock_state_machine):
        """client-1 reconnecting into a zone whose two online members sit at -30/-40."""
        from backend.core.multiroom.client_registry import ClientRegistryService
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        registry = ClientRegistryService(settings_service=mock_settings_service)
        await registry.initialize()
        attach_registry_broadcaster(registry, mock_state_machine)

        for i, ip in enumerate(("192.168.1.1", "192.168.1.2", "192.168.1.3"), start=1):
            await registry.register_client(f"client-{i}", f"Client {i}", ip)
        await registry.update_volume("client-2", volume_db=-30.0)
        await registry.update_volume("client-3", volume_db=-40.0)
        await registry.create_zone("zone-1", "Test Zone", ["client-1", "client-2", "client-3"])
        await registry.set_client_online("client-1", False)
        await registry.set_client_online("client-2", True)
        await registry.set_client_online("client-3", True)

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock(),
        )
        ws_service.set_registry(registry)
        ws_service._volume_service = mock_state_machine.volume_service
        return registry, ws_service

    @pytest.mark.asyncio
    async def test_the_stored_volume_is_what_a_reconnect_applies(
        self, mock_settings_service, mock_state_machine
    ):
        """A level set while the client was offline is replayed when it returns."""
        _, ws_service = await self._zone_with_two_peers_online(
            mock_settings_service, mock_state_machine
        )

        target = ws_service._resolve_target_volume("client-1")

        assert target == -70.0, "the client's own stored level is the target"
        assert target != -35.0, "the peers' average is not"
        mock_state_machine.volume_service.state_store.get_client_volume.assert_called_with(
            "client-1"
        )

    @pytest.mark.asyncio
    async def test_the_stored_mute_is_what_a_reconnect_applies(
        self, mock_settings_service, mock_state_machine
    ):
        """Mute is the exception, and the reason the route's old line was half true.

        It is also load-bearing on its own: CamillaDSP starts muted from its -m
        flag, so the reconnect is what decides whether the speaker makes sound.
        """
        _, ws_service = await self._zone_with_two_peers_online(
            mock_settings_service, mock_state_machine
        )

        assert await ws_service._apply_target_volume_to_client("client-1", -35.0) is True

        eq = mock_state_machine.volume_service.equalizer_controller
        eq.set_equalizer_mute.assert_awaited_once_with("client-1", True, force=True)
