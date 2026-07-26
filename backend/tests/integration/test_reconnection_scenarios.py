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
    async def test_context_in_sync_status_response(
        self, mock_settings_service, mock_state_machine
    ):
        """
        E2E: Context is included in sync_status response after reconnection.

        Validates: Context dispatches to correct sync strategy.
        """
        from backend.core.multiroom.client_registry import ClientRegistryService
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        registry = ClientRegistryService(
            settings_service=mock_settings_service
        )
        await registry.initialize()
        attach_registry_broadcaster(registry, mock_state_machine)

        # Setup mock snapcast service
        mock_snapcast = AsyncMock()
        mock_snapcast.set_volume = AsyncMock()
        mock_state_machine.snapcast_service = mock_snapcast

        # Mock volume service to prevent errors
        mock_volume = AsyncMock()
        mock_volume.set_volume_db = AsyncMock(return_value=True)
        mock_state_machine.volume_service = mock_volume

        # Register standalone client
        await registry.register_client("local", "Main", "127.0.0.1")
        await registry.set_client_online("local", False)

        # Create websocket service
        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock(),
        )
        ws_service.set_registry(registry)
        ws_service._snapcast_service = mock_snapcast
        ws_service._volume_service = mock_volume

        # Mock the Equalizer sync methods to avoid errors
        ws_service._sync_client_volume_and_broadcast = AsyncMock(return_value=True)
        ws_service._sync_standalone_equalizer_to_client = AsyncMock(return_value=True)

        # Simulate reconnection (include mac matching registered mac_id)
        client_data = {
            "id": "snapcast-client-123",
            "config": {"name": "Main", "volume": {"percent": 100}},
            "host": {"name": "milo", "ip": "127.0.0.1", "mac": "local"}
        }

        sync_status = await ws_service._sync_existing_client_volume("snapcast-client-123", client_data)

        # Verify context is included in sync_status
        assert "context" in sync_status
        assert sync_status["context"] == ReconnectionContext.STANDALONE_ALONE.value

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
        volume_service.equalizer_controller = AsyncMock()
        sm.volume_service = volume_service

        return sm

    @pytest.mark.asyncio
    async def test_in_zone_others_online_uses_zone_average(
        self, mock_settings_service, mock_state_machine
    ):
        """
        E2E: IN_ZONE_OTHERS_ONLINE sync uses zone average volume.

        Scenario:
        1. Zone with 3 clients at volumes: -20, -30, -40
        2. client-1 offline, client-2 and client-3 online (-30, -40)
        3. client-1 reconnects
        4. client-1 should receive zone average: (-30 + -40) / 2 = -35
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

        # Get target volume using the unified method
        context = registry.get_reconnection_context("client-1")
        target_volume = ws_service._resolve_target_volume("client-1", context)

        # Should use zone average from online clients (client-2, client-3)
        # Zone average = (-30 + -40) / 2 = -35
        assert target_volume == -35.0

    @pytest.mark.asyncio
    async def test_in_zone_all_offline_uses_startup_volume(
        self, mock_settings_service, mock_state_machine
    ):
        """
        E2E: IN_ZONE_ALL_OFFLINE sync uses startup_volume_db.

        Scenario:
        1. Zone with 3 clients, all offline (backend restart)
        2. client-1 reconnects first
        3. client-1 should receive startup_volume_db (-45.0)
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

        # Get target volume using the unified method
        context = registry.get_reconnection_context("client-1")
        assert context == ReconnectionContext.IN_ZONE_ALL_OFFLINE

        target_volume = ws_service._resolve_target_volume("client-1", context)

        # Should use startup_volume_db from config
        assert target_volume == -45.0

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

        # Simulate reconnection (include mac matching registered mac_id)
        client_data = {
            "id": "snapcast-client-123",
            "config": {"name": "Client 1", "volume": {"percent": 100}},
            "host": {"name": "milo-client-01", "ip": "192.168.1.1", "mac": "client-1"}
        }

        await ws_service._sync_existing_client_volume("snapcast-client-123", client_data)

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
        volume_service.equalizer_controller = AsyncMock()
        sm.volume_service = volume_service

        return sm

    @pytest.mark.asyncio
    async def test_standalone_others_online_uses_global_average(
        self, mock_settings_service, mock_state_machine
    ):
        """
        E2E: STANDALONE_OTHERS_ONLINE sync uses global average volume.

        Scenario:
        1. 3 standalone clients at volumes: -20, -30, -40
        2. client-1 offline, client-2 and client-3 online (-30, -40)
        3. client-1 reconnects
        4. client-1 should receive global average: (-30 + -40) / 2 = -35
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

        # Get target volume using the unified method
        context = registry.get_reconnection_context("client-1")
        assert context == ReconnectionContext.STANDALONE_OTHERS_ONLINE

        target_volume = ws_service._resolve_target_volume("client-1", context)

        # Should use global average from online clients (client-2, client-3)
        # Global average = (-30 + -40) / 2 = -35
        assert target_volume == -35.0

    @pytest.mark.asyncio
    async def test_standalone_alone_uses_startup_volume(
        self, mock_settings_service, mock_state_machine
    ):
        """
        E2E: STANDALONE_ALONE sync uses startup_volume_db.

        Scenario:
        1. 3 standalone clients, all offline (backend restart)
        2. client-1 reconnects first
        3. client-1 should receive startup_volume_db (-45.0)
        """
        from backend.core.multiroom.client_registry import ClientRegistryService
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        # Setup registry
        registry = ClientRegistryService(
            settings_service=mock_settings_service
        )
        await registry.initialize()
        attach_registry_broadcaster(registry, mock_state_machine)

        # Register standalone clients (no zone)
        await registry.register_client("client-1", "Client 1", "192.168.1.1")
        await registry.register_client("client-2", "Client 2", "192.168.1.2")
        await registry.register_client("client-3", "Client 3", "192.168.1.3")

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

        # Get target volume using the unified method
        context = registry.get_reconnection_context("client-1")
        assert context == ReconnectionContext.STANDALONE_ALONE

        target_volume = ws_service._resolve_target_volume("client-1", context)

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

        # Simulate reconnection (the snapclient id IS the registered mac_id)
        client_data = {
            "id": "local-main",
            "config": {"name": "Main", "volume": {"percent": 100}},
            "host": {"name": "milo", "ip": "192.168.1.10", "mac": "local-main"}
        }

        sync_status = await ws_service._sync_existing_client_volume("local-main", client_data)

        # Verify sync_context is in sync_status
        assert "context" in sync_status
        assert sync_status["context"] == ReconnectionContext.STANDALONE_OTHERS_ONLINE.value

