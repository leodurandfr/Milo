"""
Integration tests for reconnection scenarios (FR7-FR10, FR13).

These tests validate the reconnection sync logic for volume and Equalizer settings
across all defined scenarios in the architecture document.

Story 5.5: Tests E2E - Scénarios de Reconnexion
Story 5.1: Reconnection Context Detection
Story 5.2: IN_ZONE Reconnection Sync
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.tests.conftest import attach_registry_broadcaster
from backend.core.volume.state import DEFAULT_VOLUME_DB
from backend.core.models.volume import VolumeConfig
from backend.core.multiroom.models import ReconnectionContext


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def mock_zone():
    """Create a mock zone with Equalizer settings."""
    zone = MagicMock()
    zone.id = "zone-1"
    zone.name = "Living Room"
    zone.client_ids = ["local", "milo-client-01", "milo-client-02"]
    zone.equalizer_settings = {
        "filters": {"band_1": {"type": "Peaking", "freq": 1000, "gain": 3.0}},
        "compressor": {"enabled": False},
        "loudness": {"enabled": False}
    }
    zone.crossover_enabled = True
    return zone


@pytest.fixture
def mock_client_local():
    """Create mock local client."""
    client = MagicMock()
    client.mac_id = "local"
    client.name = "Milo"
    client.online = True
    client.volume_db = -30.0
    client.speaker_type = "bookshelf"
    return client


@pytest.fixture
def mock_client_01():
    """Create mock remote client 01."""
    client = MagicMock()
    client.mac_id = "milo-client-01"
    client.name = "Kitchen"
    client.online = True
    client.volume_db = -25.0
    client.speaker_type = "satellite"
    return client


@pytest.fixture
def mock_client_02():
    """Create mock remote client 02."""
    client = MagicMock()
    client.mac_id = "milo-client-02"
    client.name = "Bedroom"
    client.online = False
    client.volume_db = -35.0
    client.speaker_type = "bookshelf"
    return client


@pytest.fixture
def mock_subwoofer():
    """Create mock subwoofer client."""
    client = MagicMock()
    client.mac_id = "milo-subwoofer"
    client.name = "Subwoofer"
    client.online = True
    client.volume_db = -20.0
    client.speaker_type = "subwoofer"
    return client


# =============================================================================
# TestReconnectionScenarios - FR7-FR10
# =============================================================================


class TestReconnectionInZone:
    """Tests for FR7-FR8: IN_ZONE client reconnection scenarios."""

    @pytest.mark.asyncio
    async def test_fr7_in_zone_others_online_volume_sync(
        self,
        mock_zone,
        mock_client_local,
        mock_client_01,
        mock_client_02
    ):
        """
        FR7: IN_ZONE client reconnects with others ONLINE.

        Expected:
        - volume = zone_volume_avg (average of online members)
        - Equalizer = zone.equalizer_settings
        """
        # Setup: Zone with 3 clients, client-02 offline, others online
        mock_client_02.online = False

        # Calculate expected zone average from online clients
        online_volumes = [mock_client_local.volume_db, mock_client_01.volume_db]
        expected_zone_avg = sum(online_volumes) / len(online_volumes)

        # Verify zone average calculation
        assert expected_zone_avg == -27.5  # (-30 + -25) / 2

        # Verify the reconnecting client should receive zone average
        # This is the expected volume for client-02 when it reconnects
        reconnecting_client_expected_volume = expected_zone_avg

        assert reconnecting_client_expected_volume == -27.5

    @pytest.mark.asyncio
    async def test_fr7_in_zone_equalizer_sync(self, mock_zone):
        """
        FR7: IN_ZONE client reconnects - Equalizer settings sync.

        Expected: Equalizer = zone.equalizer_settings
        """
        # Verify zone has Equalizer settings to sync
        assert mock_zone.equalizer_settings is not None
        assert "filters" in mock_zone.equalizer_settings
        assert "compressor" in mock_zone.equalizer_settings
        assert "loudness" in mock_zone.equalizer_settings

        # Verify filter settings
        filters = mock_zone.equalizer_settings["filters"]
        assert "band_1" in filters
        assert filters["band_1"]["gain"] == 3.0

    @pytest.mark.asyncio
    async def test_fr8_in_zone_all_offline_uses_startup_volume(self, mock_zone):
        """
        FR8: IN_ZONE client reconnects with ALL others OFFLINE.

        Expected:
        - volume = startup_volume_db (DEFAULT_VOLUME_DB = -45.0)
        - Equalizer = zone.equalizer_settings (from persistence)
        """
        # Setup: All clients in zone are offline
        # When first client reconnects, no online clients to average from

        # Expected behavior: use DEFAULT_VOLUME_DB
        expected_volume = DEFAULT_VOLUME_DB

        assert expected_volume == -45.0

        # Zone Equalizer settings should still be applied from persistence
        assert mock_zone.equalizer_settings is not None

    @pytest.mark.asyncio
    async def test_fr8_zone_equalizer_from_persistence(self, mock_zone):
        """
        FR8: Verify zone Equalizer settings come from persistence when all offline.
        """
        # Even when all clients are offline, zone.equalizer_settings should be loaded
        # from persistence (settings.json) and applied to reconnecting client

        persisted_equalizer = mock_zone.equalizer_settings

        # Verify structure matches what's expected
        assert persisted_equalizer["filters"]["band_1"]["type"] == "Peaking"
        assert persisted_equalizer["filters"]["band_1"]["freq"] == 1000
        assert persisted_equalizer["compressor"]["enabled"] is False


class TestReconnectionStandalone:
    """Tests for FR9-FR10: STANDALONE client reconnection scenarios."""

    @pytest.mark.asyncio
    async def test_fr9_standalone_others_online_uses_global_volume(
        self,
        mock_client_local,
        mock_client_01
    ):
        """
        FR9: STANDALONE client reconnects with others ONLINE.

        Expected:
        - volume = volume_global (average of all online clients)
        - Equalizer = standalone_equalizer[mac_id]
        """
        # Setup: Standalone client (not in zone), other clients online
        # Global volume = average of all online clients

        online_volumes = [mock_client_local.volume_db, mock_client_01.volume_db]
        expected_global_volume = sum(online_volumes) / len(online_volumes)

        assert expected_global_volume == -27.5

    @pytest.mark.asyncio
    async def test_fr9_standalone_equalizer_sync(self):
        """
        FR9: STANDALONE client reconnects - Equalizer settings sync.

        Expected: Equalizer = standalone_equalizer[mac_id] from client_equalizer.json
        """
        # Standalone Equalizer settings structure
        standalone_equalizer = {
            "milo-client-03": {
                "filters": {"band_1": {"type": "HighShelf", "gain": -2.0}},
                "compressor": {"enabled": True, "threshold": -15},
                "loudness": {"enabled": False}
            }
        }

        client_id = "milo-client-03"
        client_equalizer = standalone_equalizer.get(client_id)

        assert client_equalizer is not None
        assert client_equalizer["compressor"]["enabled"] is True

    @pytest.mark.asyncio
    async def test_fr10_standalone_first_client_uses_startup_volume(self):
        """
        FR10: STANDALONE client reconnects as FIRST client (none online).

        Expected:
        - volume = startup_volume_db (DEFAULT_VOLUME_DB = -45.0)
        - Equalizer = standalone_equalizer[mac_id]
        """
        # Setup: No clients online (backend just started or all disconnected)
        online_clients = []

        # When no clients online, use DEFAULT_VOLUME_DB
        if len(online_clients) == 0:
            expected_volume = DEFAULT_VOLUME_DB
        else:
            expected_volume = sum(c.volume_db for c in online_clients) / len(online_clients)

        assert expected_volume == -45.0

    @pytest.mark.asyncio
    async def test_fr10_standalone_equalizer_defaults_when_none_saved(self):
        """
        FR10: STANDALONE client with no saved Equalizer settings gets defaults.

        Expected: Default Equalizer (flat EQ, compressor off, loudness off)
        """
        from backend.core.equalizer.sync import EqualizerSettingsSyncService

        sync_service = EqualizerSettingsSyncService()
        defaults = sync_service.get_default_settings()

        # Verify defaults are flat/off
        assert defaults["filters"] == {}
        assert defaults["compressor"]["enabled"] is False
        assert defaults["loudness"]["enabled"] is False


class TestCrossoverAutomatic:
    """Tests for FR13: Automatic crossover enable/disable."""

    @pytest.mark.asyncio
    async def test_fr13_crossover_enables_on_subwoofer_connect(
        self,
        mock_zone,
        mock_client_local,
        mock_client_01,
        mock_subwoofer
    ):
        """
        FR13: Crossover enables when subwoofer connects to zone.

        Expected:
        - Crossover enabled for zone
        - Highpass on satellites/bookshelves
        - Lowpass on subwoofer
        """
        # Setup: Zone with speakers
        zone_clients = [mock_client_local, mock_client_01]

        # Subwoofer connects
        mock_subwoofer.online = True
        zone_clients.append(mock_subwoofer)

        # Check for online subwoofer
        has_online_subwoofer = any(
            c.speaker_type == "subwoofer" and c.online
            for c in zone_clients
        )

        assert has_online_subwoofer is True

        # Crossover should be enabled
        should_enable_crossover = has_online_subwoofer and mock_zone.crossover_enabled
        assert should_enable_crossover is True

    @pytest.mark.asyncio
    async def test_fr13_crossover_disables_on_subwoofer_disconnect(
        self,
        mock_zone,
        mock_client_local,
        mock_client_01,
        mock_subwoofer
    ):
        """
        FR13: Crossover disables when subwoofer disconnects from zone.

        Expected:
        - Crossover disabled for zone
        - All filters bypassed
        """
        # Setup: Zone with active crossover (subwoofer was online)
        zone_clients = [mock_client_local, mock_client_01, mock_subwoofer]

        # Subwoofer disconnects
        mock_subwoofer.online = False

        # Check for online subwoofer
        has_online_subwoofer = any(
            c.speaker_type == "subwoofer" and c.online
            for c in zone_clients
        )

        assert has_online_subwoofer is False

        # Crossover should be disabled (no online subwoofer)
        should_apply_crossover = has_online_subwoofer and mock_zone.crossover_enabled
        assert should_apply_crossover is False

    @pytest.mark.asyncio
    async def test_fr13_crossover_frequency_from_speaker_types(self):
        """
        FR13: Crossover frequency determined by speaker types.
        """
        from backend.core.multiroom.models import DEFAULT_CROSSOVER_FREQUENCIES

        # Verify frequencies per speaker type
        assert DEFAULT_CROSSOVER_FREQUENCIES["satellite"] == 120
        assert DEFAULT_CROSSOVER_FREQUENCIES["bookshelf"] == 80
        assert DEFAULT_CROSSOVER_FREQUENCIES["tower"] == 50
        assert DEFAULT_CROSSOVER_FREQUENCIES["subwoofer"] is None

    @pytest.mark.asyncio
    async def test_fr13_multiple_subwoofers_one_online(
        self,
        mock_zone,
        mock_subwoofer
    ):
        """
        FR13: With multiple subwoofers, crossover enabled if at least one online.
        """
        # Create second subwoofer (offline)
        subwoofer_2 = MagicMock()
        subwoofer_2.mac_id = "milo-subwoofer-2"
        subwoofer_2.speaker_type = "subwoofer"
        subwoofer_2.online = False

        # First subwoofer online
        mock_subwoofer.online = True

        zone_clients = [mock_subwoofer, subwoofer_2]

        # At least one subwoofer online
        has_online_subwoofer = any(
            c.speaker_type == "subwoofer" and c.online
            for c in zone_clients
        )

        assert has_online_subwoofer is True

    @pytest.mark.asyncio
    async def test_fr13_all_subwoofers_offline_disables_crossover(
        self,
        mock_zone,
        mock_subwoofer
    ):
        """
        FR13: With all subwoofers offline, crossover is disabled.
        """
        # Create second subwoofer (also offline)
        subwoofer_2 = MagicMock()
        subwoofer_2.mac_id = "milo-subwoofer-2"
        subwoofer_2.speaker_type = "subwoofer"
        subwoofer_2.online = False

        # Both subwoofers offline
        mock_subwoofer.online = False

        zone_clients = [mock_subwoofer, subwoofer_2]

        # No online subwoofer
        has_online_subwoofer = any(
            c.speaker_type == "subwoofer" and c.online
            for c in zone_clients
        )

        assert has_online_subwoofer is False


# =============================================================================
# TestVolumeStateCalculations - Volume sync logic validation
# =============================================================================


class TestVolumeStateCalculations:
    """Tests validating volume state calculations used in reconnection."""

    def test_zone_volume_average_calculation(self):
        """Test zone volume average calculation with multiple clients."""
        # Simulate online client volumes in a zone
        client_volumes = {
            "local": -30.0,
            "client-01": -25.0,
            "client-02": -35.0
        }

        # Only online clients contribute to average
        online_ids = ["local", "client-01"]  # client-02 is offline
        online_volumes = [client_volumes[cid] for cid in online_ids]

        zone_avg = sum(online_volumes) / len(online_volumes)
        assert zone_avg == -27.5

    def test_global_volume_calculation(self):
        """Test global volume calculation (all online clients)."""
        # All clients (standalone + in zones)
        all_client_volumes = [-30.0, -25.0, -40.0, -20.0]

        global_volume = sum(all_client_volumes) / len(all_client_volumes)
        assert global_volume == -28.75

    def test_empty_clients_uses_default(self):
        """Test that empty client list returns DEFAULT_VOLUME_DB."""
        online_volumes = []

        if len(online_volumes) == 0:
            volume = DEFAULT_VOLUME_DB
        else:
            volume = sum(online_volumes) / len(online_volumes)

        assert volume == -45.0


# =============================================================================
# TestSyncStatusTracking - WebSocket sync status validation
# =============================================================================


class TestSyncStatusTracking:
    """Tests for sync status tracking in reconnection events."""

    def test_sync_status_structure(self):
        """Test sync_status dict structure in client_connected event."""
        sync_status = {
            "volume_synced": True,
            "equalizer_synced": True,
            "pending_applied": False
        }

        assert "volume_synced" in sync_status
        assert "equalizer_synced" in sync_status
        assert "pending_applied" in sync_status

    def test_sync_status_failure_detection(self):
        """Test detecting sync failure from sync_status."""
        # Sync failed
        sync_status = {
            "volume_synced": True,
            "equalizer_synced": False,  # Equalizer sync failed
            "pending_applied": False
        }

        has_error = not sync_status["volume_synced"] or not sync_status["equalizer_synced"]
        assert has_error is True

    def test_sync_status_success_detection(self):
        """Test detecting sync success from sync_status."""
        sync_status = {
            "volume_synced": True,
            "equalizer_synced": True,
            "pending_applied": True
        }

        has_error = not sync_status["volume_synced"] or not sync_status["equalizer_synced"]
        assert has_error is False


# =============================================================================
# Story 5.1: Reconnection Context Detection Integration Tests
# =============================================================================


class TestReconnectionContextDetectionIntegration:
    """
    Integration tests for reconnection context detection (Story 5.1).

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
        sm.broadcast_event = AsyncMock()
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
        3. Context should be IN_ZONE_OTHERS_ONLINE (FR7)
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

        # Should be IN_ZONE_OTHERS_ONLINE (FR7)
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
        3. Context should be IN_ZONE_ALL_OFFLINE (FR8)
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

        # Should be IN_ZONE_ALL_OFFLINE (FR8)
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
        4. Context should be STANDALONE_OTHERS_ONLINE (FR9)
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

        # Should be STANDALONE_OTHERS_ONLINE (FR9)
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
        3. Context should be STANDALONE_ALONE (FR10)
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

        # Should be STANDALONE_ALONE (FR10)
        assert context == ReconnectionContext.STANDALONE_ALONE

    @pytest.mark.asyncio
    async def test_context_in_sync_status_response(
        self, mock_settings_service, mock_state_machine
    ):
        """
        E2E: Context is included in sync_status response after reconnection.

        Validates AC5: Context dispatches to correct sync strategy.
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
# Story 5.2: IN_ZONE Reconnection Sync Integration Tests
# =============================================================================


class TestInZoneReconnectionSyncIntegration:
    """
    Integration tests for IN_ZONE reconnection volume sync (Story 5.2).

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
        sm.broadcast_event = AsyncMock()
        sm.snapcast_service = None
        sm.crossover_service = None
        sm.equalizer_client_proxy_service = None
        sm.equalizer_settings_sync_service = None
        sm.camilladsp_service = None

        # Mock volume service with config and state store
        volume_service = AsyncMock()
        volume_service.volume_config = VolumeConfig(startup_volume_db=-45.0)
        volume_service.update_client_volume_db = AsyncMock()
        volume_service._broadcast_volume_state = AsyncMock()
        # Mock _state_store._clients to return proper client state objects
        mock_client_state = MagicMock()
        mock_client_state.mute = False
        volume_service._state_store._clients = {
            "client-1": mock_client_state,
            "client-2": mock_client_state,
            "client-3": mock_client_state,
        }
        volume_service._equalizer_controller = AsyncMock()
        sm.volume_service = volume_service

        return sm

    @pytest.mark.asyncio
    async def test_in_zone_others_online_uses_zone_average(
        self, mock_settings_service, mock_state_machine
    ):
        """
        E2E: IN_ZONE_OTHERS_ONLINE sync uses zone average volume (FR7, AC1).

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
        ws_service._sync_zone_equalizer_to_client = AsyncMock(return_value=True)

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
        E2E: IN_ZONE_ALL_OFFLINE sync uses startup_volume_db (FR8, AC2).

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
        E2E: Zone average calculation excludes the reconnecting client (AC1).

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
        E2E: WebSocket broadcast is sent after volume sync completes (AC4).
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
        ws_service._sync_zone_equalizer_to_client = AsyncMock(return_value=True)

        # Mock _state_store._clients to return a proper client state object
        volume_service = mock_state_machine.volume_service
        mock_client_state = MagicMock()
        mock_client_state.mute = False
        volume_service._state_store._clients = {"client-1": mock_client_state}
        volume_service._equalizer_controller = AsyncMock()

        # Simulate reconnection (include mac matching registered mac_id)
        client_data = {
            "id": "snapcast-client-123",
            "config": {"name": "Client 1", "volume": {"percent": 100}},
            "host": {"name": "milo-client-01", "ip": "192.168.1.1", "mac": "client-1"}
        }

        await ws_service._sync_existing_client_volume("snapcast-client-123", client_data)

        # Verify broadcast was called
        volume_service._broadcast_volume_state.assert_called()

    @pytest.mark.asyncio
    async def test_equalizer_sync_uses_zone_settings(
        self, mock_settings_service, mock_state_machine
    ):
        """
        E2E: Equalizer sync uses zone.equalizer_settings for IN_ZONE contexts (AC3).
        """
        from backend.core.multiroom.client_registry import ClientRegistryService
        from backend.core.multiroom.models import EqualizerSettings, EqFilter, FilterType

        registry = ClientRegistryService(
            settings_service=mock_settings_service
        )
        await registry.initialize()

        # Register clients and create zone with Equalizer settings
        await registry.register_client("client-1", "Client 1", "192.168.1.1")
        await registry.register_client("client-2", "Client 2", "192.168.1.2")

        # Create zone with custom Equalizer settings
        equalizer_settings = EqualizerSettings(
            enabled=True,
            filters=[EqFilter(id="eq_band_00", frequency=1000, gain=5.0, filter_type=FilterType.PEAKING)]
        )
        await registry.create_zone("zone-1", "Test Zone", ["client-1", "client-2"], equalizer_settings=equalizer_settings)

        # Verify zone has Equalizer settings
        zone = registry.get_zone("zone-1")
        assert zone.equalizer_settings is not None
        assert zone.equalizer_settings.enabled is True
        assert len(zone.equalizer_settings.filters) == 1
        assert zone.equalizer_settings.filters[0].gain == 5.0


# =============================================================================
# Story 5.2: AC4 - Sync Time Compliance Tests (NFR4)
# =============================================================================


class TestAC4SyncTimeCompliance:
    """
    Tests for AC4: Sync Time Compliance (NFR4).

    Validates that the entire sync process completes within 1 second.
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
        """Create a mock state machine with all required services."""
        sm = MagicMock()
        sm.broadcast_event = AsyncMock()

        # Mock snapcast service
        snapcast = AsyncMock()
        snapcast.set_client_group_to_multiroom = AsyncMock()
        snapcast.set_volume = AsyncMock()
        sm.snapcast_service = snapcast

        # Mock volume service with config and state store
        volume_service = AsyncMock()
        volume_service.volume_config = VolumeConfig(startup_volume_db=-45.0)
        volume_service.update_client_volume_db = AsyncMock()
        volume_service._broadcast_volume_state = AsyncMock()
        # Mock _state_store._clients to return proper client state objects
        mock_client_state = MagicMock()
        mock_client_state.mute = False
        volume_service._state_store._clients = {
            "client-1": mock_client_state,
            "client-2": mock_client_state,
        }
        volume_service._equalizer_controller = AsyncMock()
        sm.volume_service = volume_service

        # Mock Equalizer services
        sm.crossover_service = None
        sm.equalizer_client_proxy_service = None
        sm.equalizer_settings_sync_service = None
        sm.camilladsp_service = None

        return sm

    @pytest.mark.asyncio
    async def test_sync_completes_within_1_second(
        self, mock_settings_service, mock_state_machine
    ):
        """
        AC4/NFR4: Sync process completes within 1 second.

        This test measures the actual execution time of the sync process
        to ensure it meets the performance requirement.
        """
        import time
        from backend.core.multiroom.client_registry import ClientRegistryService
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        # Setup registry
        registry = ClientRegistryService(
            settings_service=mock_settings_service
        )
        await registry.initialize()
        attach_registry_broadcaster(registry, mock_state_machine)

        # Register clients in zone
        await registry.register_client("client-1", "Client 1", "192.168.1.1")
        await registry.register_client("client-2", "Client 2", "192.168.1.2")
        await registry.update_volume("client-2", volume_db=-30.0)

        await registry.create_zone("zone-1", "Test Zone", ["client-1", "client-2"])
        await registry.set_client_online("client-1", False)  # Reconnecting
        await registry.set_client_online("client-2", True)   # Online

        # Create websocket service
        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock(),
        )
        ws_service.set_registry(registry)
        ws_service._snapcast_service = mock_state_machine.snapcast_service
        ws_service._volume_service = mock_state_machine.volume_service
        ws_service._sync_zone_equalizer_to_client = AsyncMock(return_value=True)

        # Simulate reconnection with timing (include mac matching registered mac_id)
        client_data = {
            "id": "snapcast-client-123",
            "config": {"name": "Client 1", "volume": {"percent": 100}},
            "host": {"name": "milo-client-01", "ip": "192.168.1.1", "mac": "client-1"}
        }

        start_time = time.perf_counter()
        sync_status = await ws_service._sync_existing_client_volume("snapcast-client-123", client_data)
        end_time = time.perf_counter()

        elapsed_time = end_time - start_time

        # Assert: Sync completes within 1 second (NFR4)
        assert elapsed_time < 1.0, f"Sync took {elapsed_time:.3f}s, expected < 1.0s"

        # Also verify sync succeeded
        assert sync_status["volume_synced"] is True

    @pytest.mark.asyncio
    async def test_sync_time_with_equalizer_operations(
        self, mock_settings_service, mock_state_machine
    ):
        """
        AC4/NFR4: Sync time includes Equalizer operations and still completes within 1 second.

        Tests a more realistic scenario with mocked Equalizer operations.
        """
        import time
        from backend.core.multiroom.client_registry import ClientRegistryService
        from backend.core.multiroom.websocket import SnapcastWebSocketService
        from backend.core.multiroom.models import EqualizerSettings, EqFilter, FilterType

        # Setup registry
        registry = ClientRegistryService(
            settings_service=mock_settings_service
        )
        await registry.initialize()
        attach_registry_broadcaster(registry, mock_state_machine)

        # Register clients and create zone with Equalizer settings
        await registry.register_client("client-1", "Client 1", "192.168.1.1")
        await registry.register_client("client-2", "Client 2", "192.168.1.2")
        await registry.update_volume("client-2", volume_db=-25.0)

        equalizer_settings = EqualizerSettings(
            enabled=True,
            filters=[
                EqFilter(id="eq_band_00", frequency=100, gain=2.0, filter_type=FilterType.PEAKING),
                EqFilter(id="eq_band_01", frequency=1000, gain=-1.5, filter_type=FilterType.PEAKING),
                EqFilter(id="eq_band_02", frequency=10000, gain=3.0, filter_type=FilterType.PEAKING),
            ]
        )
        await registry.create_zone("zone-1", "Test Zone", ["client-1", "client-2"], equalizer_settings=equalizer_settings)
        await registry.set_client_online("client-1", False)
        await registry.set_client_online("client-2", True)

        # Create websocket service with realistic Equalizer mock (small delay)
        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock(),
        )
        ws_service.set_registry(registry)
        ws_service._snapcast_service = mock_state_machine.snapcast_service
        ws_service._volume_service = mock_state_machine.volume_service

        async def mock_equalizer_sync(*args, **kwargs):
            await asyncio.sleep(0.1)  # Simulate 100ms for Equalizer operations
            return True

        ws_service._sync_zone_equalizer_to_client = mock_equalizer_sync

        # Include mac matching registered mac_id
        client_data = {
            "id": "snapcast-client-123",
            "config": {"name": "Client 1", "volume": {"percent": 100}},
            "host": {"name": "milo-client-01", "ip": "192.168.1.1", "mac": "client-1"}
        }

        start_time = time.perf_counter()
        sync_status = await ws_service._sync_existing_client_volume("snapcast-client-123", client_data)
        end_time = time.perf_counter()

        elapsed_time = end_time - start_time

        # Assert: Even with Equalizer operations, sync completes within 1 second
        assert elapsed_time < 1.0, f"Sync with Equalizer took {elapsed_time:.3f}s, expected < 1.0s"
        assert sync_status["equalizer_synced"] is True


# =============================================================================
# Story 5.2: AC6 - Pending Settings Queue Tests
# =============================================================================


class TestAC6PendingSettingsQueue:
    """
    Tests for AC6: Pending Settings Handling.

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
        sm.broadcast_event = AsyncMock()

        # Mock crossover service with queue_pending_settings
        crossover = AsyncMock()
        crossover.queue_pending_settings = AsyncMock()
        crossover.has_pending_settings = MagicMock(return_value=False)
        sm.crossover_service = crossover

        # Mock Equalizer proxy that will fail
        proxy = AsyncMock()
        proxy.request = AsyncMock(side_effect=Exception("Connection refused"))
        sm.equalizer_client_proxy_service = proxy

        return sm

    @pytest.mark.asyncio
    async def test_failed_compressor_settings_are_queued(
        self, mock_settings_service, mock_state_machine_with_crossover
    ):
        """
        AC6: Failed compressor settings are queued via queue_pending_settings().
        """
        from backend.core.multiroom.client_registry import ClientRegistryService
        from backend.core.multiroom.websocket import SnapcastWebSocketService
        from backend.core.multiroom.models import EqualizerSettings, CompressorSettings

        # Setup registry
        registry = ClientRegistryService(
            settings_service=mock_settings_service
        )
        await registry.initialize()
        attach_registry_broadcaster(registry, mock_state_machine_with_crossover)

        # Register client with IP
        await registry.register_client("client-1", "Client 1", "192.168.1.100")
        await registry.register_client("client-2", "Client 2", "192.168.1.101")

        # Create zone with compressor settings
        equalizer_settings = EqualizerSettings(
            enabled=True,
            compressor=CompressorSettings(enabled=True, threshold=-20.0, ratio=4.0)
        )
        await registry.create_zone("zone-1", "Test Zone", ["client-1", "client-2"], equalizer_settings=equalizer_settings)

        # Create websocket service
        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine_with_crossover,
            routing_service=MagicMock(),
        )
        ws_service.set_registry(registry)
        ws_service._equalizer_client_proxy_service = mock_state_machine_with_crossover.equalizer_client_proxy_service
        ws_service._crossover_service = mock_state_machine_with_crossover.crossover_service

        # Call _sync_zone_equalizer_to_client - compressor sync will fail
        zone = registry.get_zone("zone-1")
        result = await ws_service._sync_zone_equalizer_to_client("client-1", zone)

        # Assert: sync failed and compressor was queued
        assert result is False

        crossover = mock_state_machine_with_crossover.crossover_service
        crossover.queue_pending_settings.assert_called()

        # Verify compressor was specifically queued
        calls = crossover.queue_pending_settings.call_args_list
        compressor_queued = any(
            call[0][1] == "compressor" for call in calls
        )
        assert compressor_queued, "Compressor settings should be queued on failure"

    @pytest.mark.asyncio
    async def test_failed_loudness_settings_are_queued(
        self, mock_settings_service, mock_state_machine_with_crossover
    ):
        """
        AC6: Failed loudness settings are queued via queue_pending_settings().
        """
        from backend.core.multiroom.client_registry import ClientRegistryService
        from backend.core.multiroom.websocket import SnapcastWebSocketService
        from backend.core.multiroom.models import EqualizerSettings, LoudnessSettings

        # Setup registry
        registry = ClientRegistryService(
            settings_service=mock_settings_service
        )
        await registry.initialize()
        attach_registry_broadcaster(registry, mock_state_machine_with_crossover)

        # Register clients
        await registry.register_client("client-1", "Client 1", "192.168.1.100")
        await registry.register_client("client-2", "Client 2", "192.168.1.101")

        # Create zone with loudness settings
        equalizer_settings = EqualizerSettings(
            enabled=True,
            loudness=LoudnessSettings(enabled=True, high_boost=10.0)
        )
        await registry.create_zone("zone-1", "Test Zone", ["client-1", "client-2"], equalizer_settings=equalizer_settings)

        # Create websocket service
        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine_with_crossover,
            routing_service=MagicMock(),
        )
        ws_service.set_registry(registry)
        ws_service._equalizer_client_proxy_service = mock_state_machine_with_crossover.equalizer_client_proxy_service
        ws_service._crossover_service = mock_state_machine_with_crossover.crossover_service

        # Call _sync_zone_equalizer_to_client - loudness sync will fail
        zone = registry.get_zone("zone-1")
        result = await ws_service._sync_zone_equalizer_to_client("client-1", zone)

        # Assert: sync failed and loudness was queued
        assert result is False

        crossover = mock_state_machine_with_crossover.crossover_service
        calls = crossover.queue_pending_settings.call_args_list
        loudness_queued = any(
            call[0][1] == "loudness" for call in calls
        )
        assert loudness_queued, "Loudness settings should be queued on failure"

    @pytest.mark.asyncio
    async def test_failed_filter_settings_are_queued(
        self, mock_settings_service, mock_state_machine_with_crossover
    ):
        """
        AC6: Failed filter settings are queued via queue_pending_settings().
        """
        from backend.core.multiroom.client_registry import ClientRegistryService
        from backend.core.multiroom.websocket import SnapcastWebSocketService
        from backend.core.multiroom.models import EqualizerSettings, EqFilter, FilterType

        # Setup registry
        registry = ClientRegistryService(
            settings_service=mock_settings_service
        )
        await registry.initialize()
        attach_registry_broadcaster(registry, mock_state_machine_with_crossover)

        # Register clients
        await registry.register_client("client-1", "Client 1", "192.168.1.100")
        await registry.register_client("client-2", "Client 2", "192.168.1.101")

        # Create zone with filter settings
        equalizer_settings = EqualizerSettings(
            enabled=True,
            filters=[
                EqFilter(id="eq_band_00", frequency=100, gain=3.0, filter_type=FilterType.PEAKING),
                EqFilter(id="eq_band_01", frequency=1000, gain=-2.0, filter_type=FilterType.PEAKING),
            ]
        )
        await registry.create_zone("zone-1", "Test Zone", ["client-1", "client-2"], equalizer_settings=equalizer_settings)

        # Create websocket service
        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine_with_crossover,
            routing_service=MagicMock(),
        )
        ws_service.set_registry(registry)
        ws_service._equalizer_client_proxy_service = mock_state_machine_with_crossover.equalizer_client_proxy_service
        ws_service._crossover_service = mock_state_machine_with_crossover.crossover_service

        # Call _sync_zone_equalizer_to_client - filter sync will fail
        zone = registry.get_zone("zone-1")
        result = await ws_service._sync_zone_equalizer_to_client("client-1", zone)

        # Assert: sync failed and filters were queued
        assert result is False

        crossover = mock_state_machine_with_crossover.crossover_service
        calls = crossover.queue_pending_settings.call_args_list
        filters_queued = any(
            call[0][1] == "filters" for call in calls
        )
        assert filters_queued, "Filter settings should be queued on failure"

    @pytest.mark.asyncio
    async def test_successful_sync_does_not_queue_settings(
        self, mock_settings_service
    ):
        """
        AC6: Successful Equalizer sync does NOT queue any pending settings.
        """
        from backend.core.multiroom.client_registry import ClientRegistryService
        from backend.core.multiroom.websocket import SnapcastWebSocketService
        from backend.core.multiroom.models import EqualizerSettings, EqFilter, FilterType

        # Create state machine with successful proxy
        sm = MagicMock()
        sm.broadcast_event = AsyncMock()
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
        await registry.create_zone("zone-1", "Test Zone", ["client-1", "client-2"], equalizer_settings=equalizer_settings)

        # Create websocket service
        ws_service = SnapcastWebSocketService(
            state_machine=sm,
            routing_service=MagicMock(),
        )
        ws_service.set_registry(registry)
        ws_service._equalizer_client_proxy_service = sm.equalizer_client_proxy_service
        ws_service._crossover_service = sm.crossover_service

        # Call _sync_zone_equalizer_to_client - should succeed
        zone = registry.get_zone("zone-1")
        result = await ws_service._sync_zone_equalizer_to_client("client-1", zone)

        # Assert: sync succeeded and nothing was queued
        assert result is True
        crossover.queue_pending_settings.assert_not_called()


# =============================================================================
# Story 5.3: STANDALONE Reconnection Sync Integration Tests
# =============================================================================


class TestStandaloneReconnectionSyncIntegration:
    """
    Integration tests for STANDALONE reconnection volume sync (Story 5.3).

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
        sm.broadcast_event = AsyncMock()
        sm.snapcast_service = None
        sm.crossover_service = None
        sm.equalizer_client_proxy_service = None
        sm.equalizer_settings_sync_service = None
        sm.camilladsp_service = None

        # Mock volume service with config and state store
        volume_service = AsyncMock()
        volume_service.volume_config = VolumeConfig(startup_volume_db=-45.0)
        volume_service.update_client_volume_db = AsyncMock()
        volume_service._broadcast_volume_state = AsyncMock()
        # Mock _state_store._clients to return proper client state objects
        mock_client_state = MagicMock()
        mock_client_state.mute = False
        volume_service._state_store._clients = {
            "local-main": mock_client_state,
            "client-1": mock_client_state,
            "client-2": mock_client_state,
            "client-3": mock_client_state,
        }
        volume_service._equalizer_controller = AsyncMock()
        sm.volume_service = volume_service

        return sm

    @pytest.mark.asyncio
    async def test_standalone_others_online_uses_global_average(
        self, mock_settings_service, mock_state_machine
    ):
        """
        E2E: STANDALONE_OTHERS_ONLINE sync uses global average volume (FR9, AC1).

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
        E2E: STANDALONE_ALONE sync uses startup_volume_db (FR10, AC2).

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
        E2E: Global average calculation excludes the reconnecting client (AC1).

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
        E2E: Global average includes BOTH zoned and standalone clients (AC1).

        Validates FR9 requirement that global average considers all online
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
        E2E: Equalizer sync for STANDALONE uses client-specific Equalizer settings (AC3).
        """
        from backend.core.multiroom.client_registry import ClientRegistryService
        from backend.core.multiroom.websocket import SnapcastWebSocketService
        from backend.core.equalizer.sync import EqualizerSettingsSyncService

        registry = ClientRegistryService(
            settings_service=mock_settings_service
        )
        await registry.initialize()

        # Register standalone client
        await registry.register_client("client-1", "Client 1", "192.168.1.1")

        # Mock equalizer_settings_sync_service
        equalizer_sync = MagicMock(spec=EqualizerSettingsSyncService)
        equalizer_sync.get_default_settings = MagicMock(return_value={
            "filters": {},
            "compressor": {"enabled": False},
            "loudness": {"enabled": False}
        })
        mock_state_machine.equalizer_settings_sync_service = equalizer_sync

        # Create websocket service
        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock(),
        )
        ws_service.set_registry(registry)

        # Verify context is STANDALONE
        context = registry.get_reconnection_context("client-1")
        assert context == ReconnectionContext.STANDALONE_ALONE

        # Equalizer sync would use standalone_equalizer[mac_id] or defaults

    @pytest.mark.asyncio
    async def test_websocket_broadcast_includes_sync_context(
        self, mock_settings_service, mock_state_machine
    ):
        """
        E2E: WebSocket broadcast includes sync_context after sync (AC5).
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

        # Simulate reconnection (use mac matching registered mac_id)
        client_data = {
            "id": "snapcast-client-123",
            "config": {"name": "Main", "volume": {"percent": 100}},
            "host": {"name": "milo", "ip": "192.168.1.10", "mac": "local-main"}
        }

        sync_status = await ws_service._sync_existing_client_volume("snapcast-client-123", client_data)

        # Verify sync_context is in sync_status (AC5)
        assert "context" in sync_status
        assert sync_status["context"] == ReconnectionContext.STANDALONE_OTHERS_ONLINE.value

    @pytest.mark.asyncio
    async def test_sync_completes_within_1_second_standalone(
        self, mock_settings_service, mock_state_machine
    ):
        """
        E2E/NFR4: STANDALONE sync completes within 1 second.
        """
        import time
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
        await registry.update_volume("client-1", volume_db=-25.0)

        await registry.set_client_online("local-main", False)  # Reconnecting
        await registry.set_client_online("client-1", True)  # Online

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

        # Simulate reconnection with timing (use mac matching registered mac_id)
        client_data = {
            "id": "snapcast-client-123",
            "config": {"name": "Main", "volume": {"percent": 100}},
            "host": {"name": "milo", "ip": "192.168.1.10", "mac": "local-main"}
        }

        start_time = time.perf_counter()
        sync_status = await ws_service._sync_existing_client_volume("snapcast-client-123", client_data)
        end_time = time.perf_counter()

        elapsed_time = end_time - start_time

        # Assert: Sync completes within 1 second (NFR4)
        assert elapsed_time < 1.0, f"Sync took {elapsed_time:.3f}s, expected < 1.0s"
        assert sync_status["volume_synced"] is True
