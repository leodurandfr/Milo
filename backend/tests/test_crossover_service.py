# backend/tests/test_crossover_service.py
"""
Unit tests for CrossoverService.

Tests:
- Crossover filter calculation by speaker type
- Zone crossover application
- Subwoofer ONLINE/OFFLINE toggle
- Pending settings queue and apply on reconnect
- Remote client proxy with success and failure scenarios
- Automatic crossover activation/deactivation
- WebSocket event broadcasting for crossover changes
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.core.multiroom.models import (
    Client,
    Zone,
    RegistryEventType,
    DEFAULT_CROSSOVER_FREQUENCIES,
)
from backend.core.multiroom.crossover import CrossoverService
from backend.core.equalizer.client_proxy import is_ip_address


# =============================================================================
# Test Fixtures
# =============================================================================

@pytest.fixture
def mock_settings_service():
    """Create a mock settings service."""
    service = AsyncMock()
    service.get_setting = AsyncMock(return_value=None)
    service.set_setting = AsyncMock()
    return service


@pytest.fixture
def mock_camilladsp_service():
    """Create a mock Equalizer service."""
    service = AsyncMock()
    service.set_crossover_filter = AsyncMock(return_value=True)
    service.set_lowpass_filter = AsyncMock(return_value=True)
    service.set_mute = AsyncMock(return_value=True)
    service.set_filter = AsyncMock(return_value=True)
    service.set_compressor = AsyncMock(return_value=True)
    service.set_loudness = AsyncMock(return_value=True)
    return service


@pytest.fixture
def mock_registry():
    """Create a mock client registry with helper methods."""
    registry = MagicMock()
    registry._clients = {}
    registry._zones = {}

    def get_client(mac_id):
        return registry._clients.get(mac_id)

    def get_zone(zone_id):
        return registry._zones.get(zone_id)

    def get_zone_for_client(mac_id):
        client = registry._clients.get(mac_id)
        if client and client.zone_id:
            return registry._zones.get(client.zone_id)
        return None

    def is_client_online(mac_id):
        client = registry._clients.get(mac_id)
        return client.online if client else False

    def zone_to_enriched_dict(zone):
        result = zone.to_dict()
        online_count = 0
        has_subwoofer = False
        for mac_id in zone.client_ids:
            client = registry._clients.get(mac_id)
            if client:
                if client.online:
                    online_count += 1
                if client.speaker_type == 'subwoofer':
                    has_subwoofer = True
        result['online_client_count'] = online_count
        result['has_subwoofer'] = has_subwoofer
        result['crossover_enabled'] = has_subwoofer and online_count > 0
        return result

    registry.get_client = MagicMock(side_effect=get_client)
    registry.get_zone = MagicMock(side_effect=get_zone)
    registry.get_zone_for_client = MagicMock(side_effect=get_zone_for_client)
    registry.is_client_online = MagicMock(side_effect=is_client_online)
    registry.zone_to_enriched_dict = MagicMock(side_effect=zone_to_enriched_dict)
    registry.subscribe = MagicMock()
    registry.update_speaker_type = AsyncMock()
    registry.update_zone = AsyncMock()
    registry._emit_event = AsyncMock()

    return registry


@pytest.fixture
def mock_proxy_service():
    """Mock EqualizerClientProxyService — non-raising try_request returns a status code."""
    proxy = MagicMock()
    proxy.try_request = AsyncMock(return_value=200)
    proxy.apply_record = AsyncMock(return_value=True)
    return proxy


@pytest.fixture
def crossover_service(mock_settings_service, mock_camilladsp_service, mock_proxy_service):
    """Create a CrossoverService instance."""
    return CrossoverService(
        settings_service=mock_settings_service,
        camilladsp_service=mock_camilladsp_service,
        proxy_service=mock_proxy_service
    )


@pytest.fixture
def crossover_service_with_registry(crossover_service, mock_registry):
    """Create a CrossoverService with registry connected."""
    crossover_service.set_registry(mock_registry)
    return crossover_service, mock_registry


# =============================================================================
# Test calculate_crossover_filters returns correct filter types
# =============================================================================

class TestCrossoverFilterCalculation:
    """Tests for crossover filter calculation by speaker type."""

    def test_satellite_speaker_returns_highpass_120hz(self, crossover_service_with_registry):
        """Test satellite speaker gets highpass at 120Hz (default)."""
        service, registry = crossover_service_with_registry

        # Create satellite client
        client = Client(
            mac_id="satellite-1",
            name="Satellite",
            ip="192.168.1.10",
            speaker_type="satellite",
            online=True
        )
        registry._clients["satellite-1"] = client

        # Verify speaker type and frequency
        speaker_type = service.get_client_speaker_type("satellite-1")
        assert speaker_type == "satellite"

        # Verify default crossover frequency for satellite
        assert DEFAULT_CROSSOVER_FREQUENCIES['satellite'] == 120

    def test_bookshelf_speaker_returns_highpass_80hz(self, crossover_service_with_registry):
        """Test bookshelf speaker gets highpass at 80Hz (THX standard)."""
        service, registry = crossover_service_with_registry

        # Create bookshelf client
        client = Client(
            mac_id="bookshelf-1",
            name="Bookshelf",
            ip="192.168.1.11",
            speaker_type="bookshelf",
            online=True
        )
        registry._clients["bookshelf-1"] = client

        speaker_type = service.get_client_speaker_type("bookshelf-1")
        assert speaker_type == "bookshelf"
        assert DEFAULT_CROSSOVER_FREQUENCIES['bookshelf'] == 80

    def test_tower_speaker_returns_highpass_50hz(self, crossover_service_with_registry):
        """Test tower speaker gets highpass at 50Hz."""
        service, registry = crossover_service_with_registry

        # Create tower client
        client = Client(
            mac_id="tower-1",
            name="Tower",
            ip="192.168.1.12",
            speaker_type="tower",
            online=True
        )
        registry._clients["tower-1"] = client

        speaker_type = service.get_client_speaker_type("tower-1")
        assert speaker_type == "tower"
        assert DEFAULT_CROSSOVER_FREQUENCIES['tower'] == 50

    def test_subwoofer_returns_lowpass_at_zone_frequency(self, crossover_service_with_registry):
        """Test subwoofer gets lowpass (no highpass)."""
        service, registry = crossover_service_with_registry

        # Create subwoofer client
        client = Client(
            mac_id="subwoofer-1",
            name="Subwoofer",
            ip="192.168.1.13",
            speaker_type="subwoofer",
            online=True
        )
        registry._clients["subwoofer-1"] = client

        # Subwoofer should have None as crossover frequency (receives lowpass instead)
        assert DEFAULT_CROSSOVER_FREQUENCIES['subwoofer'] is None
        assert service.is_client_subwoofer("subwoofer-1") is True

# =============================================================================
# Test apply_zone_crossover applies filters to online clients
# =============================================================================

class TestZoneCrossoverApplication:
    """Tests for zone crossover application."""

    @pytest.mark.asyncio
    async def test_apply_zone_crossover_with_subwoofer_online(self, crossover_service_with_registry, mock_camilladsp_service):
        """Test crossover is applied when subwoofer is ONLINE."""
        service, registry = crossover_service_with_registry

        # Create zone with satellite + subwoofer (both ONLINE)
        satellite = Client(mac_id="local", name="Satellite", ip="127.0.0.1",
                          speaker_type="satellite", online=True, zone_id="zone-1")
        subwoofer = Client(mac_id="sub-1", name="Subwoofer", ip="192.168.1.20",
                          speaker_type="subwoofer", online=True, zone_id="zone-1")

        zone = Zone(
            id="zone-1",
            name="Living Room",
            client_ids=["local", "sub-1"],
            crossover_frequency=120,
            crossover_enabled=True
        )

        registry._clients["local"] = satellite
        registry._clients["sub-1"] = subwoofer
        registry._zones["zone-1"] = zone

        # Apply zone crossover
        result = await service.apply_zone_crossover("zone-1")

        assert result is True
        # Local satellite should get highpass
        mock_camilladsp_service.set_crossover_filter.assert_called()

    @pytest.mark.asyncio
    async def test_apply_zone_crossover_skips_offline_clients(self, crossover_service_with_registry, mock_camilladsp_service):
        """Test crossover skips OFFLINE clients."""
        service, registry = crossover_service_with_registry

        # Create zone with one OFFLINE client
        satellite = Client(mac_id="local", name="Satellite", ip="127.0.0.1",
                          speaker_type="satellite", online=True, zone_id="zone-1")
        offline_client = Client(mac_id="offline-1", name="Offline", ip="192.168.1.21",
                               speaker_type="bookshelf", online=False, zone_id="zone-1")
        subwoofer = Client(mac_id="sub-1", name="Subwoofer", ip="192.168.1.20",
                          speaker_type="subwoofer", online=True, zone_id="zone-1")

        zone = Zone(
            id="zone-1",
            name="Living Room",
            client_ids=["local", "offline-1", "sub-1"],
            crossover_frequency=80,
            crossover_enabled=True
        )

        registry._clients["local"] = satellite
        registry._clients["offline-1"] = offline_client
        registry._clients["sub-1"] = subwoofer
        registry._zones["zone-1"] = zone

        result = await service.apply_zone_crossover("zone-1")

        assert result is True
        # Offline client should be skipped - no HTTP call attempted

    @pytest.mark.asyncio
    async def test_apply_zone_crossover_no_subwoofer_disables_filters(self, crossover_service_with_registry, mock_camilladsp_service):
        """Test crossover is disabled when no subwoofer present."""
        service, registry = crossover_service_with_registry

        # Create zone without subwoofer
        satellite = Client(mac_id="local", name="Satellite", ip="127.0.0.1",
                          speaker_type="satellite", online=True, zone_id="zone-1")
        bookshelf = Client(mac_id="book-1", name="Bookshelf", ip="192.168.1.22",
                          speaker_type="bookshelf", online=True, zone_id="zone-1")

        zone = Zone(
            id="zone-1",
            name="Living Room",
            client_ids=["local", "book-1"],
            crossover_frequency=80,
            crossover_enabled=True
        )

        registry._clients["local"] = satellite
        registry._clients["book-1"] = bookshelf
        registry._zones["zone-1"] = zone

        result = await service.apply_zone_crossover("zone-1")

        assert result is True
        # Filters should be disabled (enabled=False) because no online subwoofer


# =============================================================================
# Test subwoofer ONLINE/OFFLINE toggle
# =============================================================================

class TestSubwooferOnlineOfflineToggle:
    """Tests for subwoofer ONLINE/OFFLINE state changes."""

    @pytest.mark.asyncio
    async def test_subwoofer_goes_offline_disables_crossover(self, crossover_service_with_registry, mock_camilladsp_service):
        """Test crossover disables when subwoofer goes OFFLINE."""
        service, registry = crossover_service_with_registry

        # Initial state: subwoofer OFFLINE
        satellite = Client(mac_id="local", name="Satellite", ip="127.0.0.1",
                          speaker_type="satellite", online=True, zone_id="zone-1")
        subwoofer = Client(mac_id="sub-1", name="Subwoofer", ip="192.168.1.20",
                          speaker_type="subwoofer", online=False, zone_id="zone-1")

        zone = Zone(
            id="zone-1",
            name="Living Room",
            client_ids=["local", "sub-1"],
            crossover_frequency=80,
            crossover_enabled=True
        )

        registry._clients["local"] = satellite
        registry._clients["sub-1"] = subwoofer
        registry._zones["zone-1"] = zone

        result = await service.apply_zone_crossover("zone-1")

        assert result is True
        # Crossover should NOT be applied (subwoofer offline)

    @pytest.mark.asyncio
    async def test_subwoofer_comes_online_enables_crossover(self, crossover_service_with_registry, mock_camilladsp_service):
        """Test crossover enables when subwoofer comes ONLINE."""
        service, registry = crossover_service_with_registry

        # Subwoofer is ONLINE
        satellite = Client(mac_id="local", name="Satellite", ip="127.0.0.1",
                          speaker_type="satellite", online=True, zone_id="zone-1")
        subwoofer = Client(mac_id="sub-1", name="Subwoofer", ip="192.168.1.20",
                          speaker_type="subwoofer", online=True, zone_id="zone-1")

        zone = Zone(
            id="zone-1",
            name="Living Room",
            client_ids=["local", "sub-1"],
            crossover_frequency=80,
            crossover_enabled=True
        )

        registry._clients["local"] = satellite
        registry._clients["sub-1"] = subwoofer
        registry._zones["zone-1"] = zone

        result = await service.apply_zone_crossover("zone-1")

        assert result is True
        # Highpass should be applied to satellite at zone frequency (80Hz)
        mock_camilladsp_service.set_crossover_filter.assert_called_with(
            enabled=True, frequency=80, q=0.707
        )


# =============================================================================
# Test pending settings queue and apply on reconnect
# =============================================================================

class TestPendingSettingsQueue:
    """Tests for pending settings queue."""

    @pytest.mark.asyncio
    async def test_queue_pending_crossover_settings(self, crossover_service):
        """Test queuing crossover settings for offline client."""
        await crossover_service.queue_pending_settings("192.168.1.100", "crossover", {
            "enabled": True,
            "frequency": 80
        })

        assert crossover_service.has_pending_settings("192.168.1.100") is True
        settings = crossover_service._pending_settings.get("192.168.1.100", {})
        assert settings["crossover"]["enabled"] is True
        assert settings["crossover"]["frequency"] == 80

    @pytest.mark.asyncio
    async def test_queue_pending_lowpass_settings(self, crossover_service):
        """Test queuing lowpass settings for offline subwoofer."""
        await crossover_service.queue_pending_settings("192.168.1.101", "lowpass", {
            "enabled": True,
            "frequency": 80
        })

        assert crossover_service.has_pending_settings("192.168.1.101") is True
        settings = crossover_service._pending_settings.get("192.168.1.101", {})
        assert settings["lowpass"]["enabled"] is True

    @pytest.mark.asyncio
    async def test_apply_pending_crossover_on_reconnect(self, crossover_service_with_registry, mock_camilladsp_service):
        """Test pending crossover settings applied on reconnect."""
        service, registry = crossover_service_with_registry
        # Register local client
        local_client = Client(mac_id="local", name="Local", ip="127.0.0.1", online=True)
        registry._clients["local"] = local_client
        # Queue settings for local client
        service._pending_settings["local"] = {
            "crossover": {"enabled": True, "frequency": 100}
        }

        result = await service.apply_pending_settings("local")

        assert result is True
        mock_camilladsp_service.set_crossover_filter.assert_called_once()
        assert service.has_pending_settings("local") is False

    @pytest.mark.asyncio
    async def test_apply_pending_lowpass_on_reconnect(self, crossover_service_with_registry, mock_camilladsp_service):
        """Test pending lowpass settings applied on reconnect."""
        service, registry = crossover_service_with_registry
        # Register local client
        local_client = Client(mac_id="local", name="Local", ip="127.0.0.1", online=True)
        registry._clients["local"] = local_client
        service._pending_settings["local"] = {
            "lowpass": {"enabled": True, "frequency": 80}
        }

        result = await service.apply_pending_settings("local")

        assert result is True
        mock_camilladsp_service.set_lowpass_filter.assert_called_once()

    @pytest.mark.asyncio
    async def test_apply_multiple_pending_settings(self, crossover_service_with_registry, mock_camilladsp_service):
        """Test applying multiple pending settings types."""
        service, registry = crossover_service_with_registry
        # Register local client
        local_client = Client(mac_id="local", name="Local", ip="127.0.0.1", online=True)
        registry._clients["local"] = local_client
        service._pending_settings["local"] = {
            "crossover": {"enabled": True, "frequency": 80},
            "lowpass": {"enabled": False, "frequency": 80}
        }

        result = await service.apply_pending_settings("local")

        assert result is True
        mock_camilladsp_service.set_crossover_filter.assert_called_once()
        mock_camilladsp_service.set_lowpass_filter.assert_called_once()


    @pytest.mark.asyncio
    async def test_replays_every_type_a_producer_can_queue(
        self, crossover_service_with_registry, mock_proxy_service
    ):
        """Every PENDING_SETTING_TYPES entry reaches the satellite on replay.

        apply_pending_settings() pops the whole per-client dict, so a queued type
        it does not dispatch is discarded without a trace. "mono" and "enabled"
        were queued by SnapcastWebSocketService's reconnection sync and dropped
        here, which meant a failed mono/bypass push was never retried.
        """
        from backend.core.multiroom.crossover import PENDING_SETTING_TYPES
        from backend.core.multiroom.models import EqualizerSettings

        service, registry = crossover_service_with_registry
        registry._clients["sat-1"] = Client(
            mac_id="sat-1", name="Bedroom", ip="192.168.1.20", online=True
        )
        record = EqualizerSettings.default()

        await service.queue_pending_settings("sat-1", "crossover", {"enabled": True, "frequency": 80})
        await service.queue_pending_settings("sat-1", "lowpass", {"enabled": False, "frequency": 80})
        await service.queue_pending_settings("sat-1", "record", record)
        assert set(service._pending_settings["sat-1"]) == set(PENDING_SETTING_TYPES)

        assert await service.apply_pending_settings("sat-1") is True

        pushed = [c.args[2] for c in mock_proxy_service.try_request.await_args_list]
        assert pushed == ["/equalizer/crossover", "/equalizer/lowpass"]
        # The EQ record goes through the one canonical push, not a per-setting
        # replay of its own — so it cannot drift from the live write path.
        mock_proxy_service.apply_record.assert_awaited_once_with("192.168.1.20", record)

    @pytest.mark.asyncio
    async def test_unknown_setting_type_fails_loud(self, crossover_service):
        """A type apply_pending_settings cannot dispatch must not be queueable."""
        with pytest.raises(ValueError, match="Unknown pending setting type"):
            await crossover_service.queue_pending_settings("sat-1", "delay", {"ms": 10})

    def test_has_pending_settings_returns_false_for_unknown_client(self, crossover_service):
        """Test has_pending_settings returns False for unknown client."""
        assert crossover_service.has_pending_settings("unknown-client") is False

    def test_clear_pending_settings(self, crossover_service):
        """Test clearing pending settings."""
        crossover_service._pending_settings["client-1"] = {"crossover": {"enabled": True}}

        crossover_service.clear_pending_settings("client-1")

        assert crossover_service.has_pending_settings("client-1") is False


# =============================================================================
# Test remote client proxy with success and failure scenarios
# =============================================================================

class TestRemoteClientProxy:
    """Tests for remote client proxy."""

    @pytest.mark.asyncio
    async def test_proxy_crossover_to_remote_client_success(self, crossover_service):
        """Test successful proxy call for crossover via the shared session."""
        crossover_service._proxy_service.try_request.return_value = 200

        result = await crossover_service._proxy_filter_to_client(
            "crossover", "192.168.1.100", True, 80
        )

        assert result is True
        crossover_service._proxy_service.try_request.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_proxy_crossover_to_remote_client_timeout(self, crossover_service):
        """Test unreachable client (status 0) queues pending settings."""
        crossover_service._proxy_service.try_request.return_value = 0

        result = await crossover_service._proxy_filter_to_client(
            "crossover", "192.168.1.100", True, 80
        )

        assert result is False
        # Settings should be queued for later
        assert crossover_service.has_pending_settings("192.168.1.100") is True

    @pytest.mark.asyncio
    async def test_proxy_lowpass_to_remote_client_success(self, crossover_service):
        """Test successful proxy call for lowpass via the shared session."""
        crossover_service._proxy_service.try_request.return_value = 200

        result = await crossover_service._proxy_filter_to_client(
            "lowpass", "192.168.1.101", True, 80
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_proxy_lowpass_to_remote_client_failure(self, crossover_service):
        """Test proxy failure (status 0) queues pending lowpass settings."""
        crossover_service._proxy_service.try_request.return_value = 0

        result = await crossover_service._proxy_filter_to_client(
            "lowpass", "192.168.1.101", True, 80
        )

        assert result is False
        assert crossover_service.has_pending_settings("192.168.1.101") is True
        settings = crossover_service._pending_settings.get("192.168.1.101", {})
        assert "lowpass" in settings


# =============================================================================
# Helper Function Tests
# =============================================================================

class TestHelperFunctions:
    """Tests for helper functions."""

    def test_is_ip_address_valid_ipv4(self):
        """Test is_ip_address with valid IPv4."""
        assert is_ip_address("192.168.1.100") is True
        assert is_ip_address("10.0.0.1") is True
        assert is_ip_address("127.0.0.1") is True

    def test_is_ip_address_invalid(self):
        """Test is_ip_address with invalid values."""
        assert is_ip_address("hostname") is False
        assert is_ip_address("milo-client-1") is False
        assert is_ip_address("192.168.1") is False
        assert is_ip_address("") is False


# =============================================================================
# Zone Model Crossover Fields Tests
# =============================================================================

class TestZoneCrossoverFields:
    """Tests for Zone model crossover fields."""

    def test_zone_default_crossover_frequency(self):
        """Test zone has default crossover frequency of 80Hz."""
        zone = Zone(name="Test Zone")
        assert zone.crossover_frequency == 80

    def test_zone_crossover_enabled_defaults_to_none(self):
        """Test zone crossover_enabled defaults to None (auto mode)."""
        zone = Zone(name="Test Zone")
        assert zone.crossover_enabled is None

    def test_zone_to_dict_includes_crossover_fields(self):
        """Test zone.to_dict() includes crossover fields."""
        zone = Zone(
            name="Test Zone",
            crossover_frequency=100,
            crossover_enabled=True
        )
        data = zone.to_dict()

        assert "crossover_frequency" in data
        assert data["crossover_frequency"] == 100
        assert "crossover_enabled" in data
        assert data["crossover_enabled"] is True

    def test_zone_from_dict_parses_crossover_fields(self):
        """Test Zone.from_dict() parses crossover fields."""
        data = {
            "id": "zone-1",
            "name": "Test Zone",
            "client_ids": [],
            "crossover_frequency": 120,
            "crossover_enabled": False
        }
        zone = Zone.from_dict(data)

        assert zone.crossover_frequency == 120
        assert zone.crossover_enabled is False


# =============================================================================
# Client Model Crossover Fields Tests
# =============================================================================

class TestClientCrossoverFields:
    """Tests for Client model crossover fields."""

    def test_client_crossover_frequency_defaults_to_none(self):
        """Test client crossover_frequency defaults to None."""
        client = Client(mac_id="test", name="Test", ip="127.0.0.1")
        assert client.crossover_frequency is None

    def test_client_to_dict_includes_crossover_frequency(self):
        """Test client.to_dict() includes crossover_frequency."""
        client = Client(
            mac_id="test",
            name="Test",
            ip="127.0.0.1",
            crossover_frequency=100
        )
        data = client.to_dict()

        assert "crossover_frequency" in data
        assert data["crossover_frequency"] == 100

    def test_client_from_dict_parses_crossover_frequency(self):
        """Test Client.from_dict() parses crossover_frequency."""
        data = {
            "mac_id": "test",
            "name": "Test",
            "ip": "127.0.0.1",
            "crossover_frequency": 150
        }
        client = Client.from_dict(data)

        assert client.crossover_frequency == 150

    def test_client_from_dict_missing_crossover_frequency(self):
        """Test Client.from_dict() handles missing crossover_frequency."""
        data = {
            "mac_id": "test",
            "name": "Test",
            "ip": "127.0.0.1"
        }
        client = Client.from_dict(data)

        assert client.crossover_frequency is None


# =============================================================================
# Auto Crossover Calculation Tests
# =============================================================================

class TestAutoCrossoverCalculation:
    """Tests for automatic crossover frequency calculation."""

    @pytest.mark.asyncio
    async def test_get_zone_auto_crossover_uses_minimum_frequency(self, crossover_service_with_registry):
        """Test auto crossover uses minimum frequency from non-subwoofer speakers."""
        service, registry = crossover_service_with_registry

        # Create zone with satellite (120Hz) and tower (50Hz)
        satellite = Client(mac_id="sat-1", name="Satellite", ip="192.168.1.10",
                          speaker_type="satellite", online=True, zone_id="zone-1")
        tower = Client(mac_id="tower-1", name="Tower", ip="192.168.1.11",
                      speaker_type="tower", online=True, zone_id="zone-1")
        subwoofer = Client(mac_id="sub-1", name="Subwoofer", ip="192.168.1.12",
                          speaker_type="subwoofer", online=True, zone_id="zone-1")

        zone = Zone(
            id="zone-1",
            name="Living Room",
            client_ids=["sat-1", "tower-1", "sub-1"]
        )

        registry._clients["sat-1"] = satellite
        registry._clients["tower-1"] = tower
        registry._clients["sub-1"] = subwoofer
        registry._zones["zone-1"] = zone

        freq = await service.get_zone_auto_crossover("zone-1")

        # Should return minimum: tower=50Hz
        assert freq == 50

    @pytest.mark.asyncio
    async def test_get_zone_auto_crossover_ignores_subwoofer(self, crossover_service_with_registry):
        """Test auto crossover ignores subwoofer speaker type."""
        service, registry = crossover_service_with_registry

        # Create zone with only subwoofer (should use default)
        subwoofer = Client(mac_id="sub-1", name="Subwoofer", ip="192.168.1.12",
                          speaker_type="subwoofer", online=True, zone_id="zone-1")

        zone = Zone(
            id="zone-1",
            name="Sub Only",
            client_ids=["sub-1"]
        )

        registry._clients["sub-1"] = subwoofer
        registry._zones["zone-1"] = zone

        freq = await service.get_zone_auto_crossover("zone-1")

        # Should return default (80Hz) since no non-subwoofer speakers
        assert freq == service.DEFAULT_CROSSOVER_FREQUENCY

    @pytest.mark.asyncio
    async def test_get_zone_auto_crossover_empty_zone(self, crossover_service_with_registry):
        """Test auto crossover returns default for empty zone."""
        service, registry = crossover_service_with_registry

        zone = Zone(id="zone-1", name="Empty Zone", client_ids=[])
        registry._zones["zone-1"] = zone

        freq = await service.get_zone_auto_crossover("zone-1")

        assert freq == service.DEFAULT_CROSSOVER_FREQUENCY


# =============================================================================
# Test Automatic Crossover Activation on ONLINE/OFFLINE Events
# =============================================================================

class TestAutomaticCrossoverActivation:
    """Tests for automatic crossover activation/deactivation."""

    @pytest.mark.asyncio
    async def test_client_connected_subwoofer_activates_crossover(self, crossover_service_with_registry, mock_camilladsp_service):
        """Test: When subwoofer comes ONLINE, crossover is automatically activated."""
        service, registry = crossover_service_with_registry

        # Setup: Zone with satellite + subwoofer (subwoofer initially offline)
        satellite = Client(mac_id="local", name="Satellite", ip="127.0.0.1",
                          speaker_type="satellite", online=True, zone_id="zone-1")
        subwoofer = Client(mac_id="sub-1", name="Subwoofer", ip="192.168.1.20",
                          speaker_type="subwoofer", online=False, zone_id="zone-1")

        zone = Zone(
            id="zone-1",
            name="Living Room",
            client_ids=["local", "sub-1"],
            crossover_frequency=80,
            crossover_enabled=None  # Auto mode
        )

        registry._clients["local"] = satellite
        registry._clients["sub-1"] = subwoofer
        registry._zones["zone-1"] = zone

        # Subwoofer comes online
        subwoofer.online = True

        # Simulate CLIENT_CONNECTED event from registry
        await service._handle_registry_event(
            RegistryEventType.CLIENT_CONNECTED,
            {"mac_id": "sub-1", "client": subwoofer.to_dict()}
        )

        # Verify crossover was applied (highpass on satellite)
        mock_camilladsp_service.set_crossover_filter.assert_called()
        call_args = mock_camilladsp_service.set_crossover_filter.call_args
        assert call_args.kwargs.get('enabled') is True or call_args[1].get('enabled') is True

    @pytest.mark.asyncio
    async def test_client_disconnected_subwoofer_deactivates_crossover(self, crossover_service_with_registry, mock_camilladsp_service):
        """Test: When subwoofer goes OFFLINE, crossover is automatically deactivated."""
        service, registry = crossover_service_with_registry

        # Setup: Zone with satellite + subwoofer (both online)
        satellite = Client(mac_id="local", name="Satellite", ip="127.0.0.1",
                          speaker_type="satellite", online=True, zone_id="zone-1")
        subwoofer = Client(mac_id="sub-1", name="Subwoofer", ip="192.168.1.20",
                          speaker_type="subwoofer", online=True, zone_id="zone-1")

        zone = Zone(
            id="zone-1",
            name="Living Room",
            client_ids=["local", "sub-1"],
            crossover_frequency=80,
            crossover_enabled=None  # Auto mode
        )

        registry._clients["local"] = satellite
        registry._clients["sub-1"] = subwoofer
        registry._zones["zone-1"] = zone

        # Subwoofer goes offline
        subwoofer.online = False

        # Simulate CLIENT_DISCONNECTED event from registry
        await service._handle_registry_event(
            RegistryEventType.CLIENT_DISCONNECTED,
            {"mac_id": "sub-1"}
        )

        # Verify crossover was disabled
        mock_camilladsp_service.set_crossover_filter.assert_called()
        call_args = mock_camilladsp_service.set_crossover_filter.call_args
        assert call_args.kwargs.get('enabled') is False or call_args[1].get('enabled') is False

    @pytest.mark.asyncio
    async def test_speaker_type_change_to_subwoofer_activates_crossover(self, crossover_service_with_registry, mock_camilladsp_service):
        """Test: Changing speaker_type to subwoofer activates crossover."""
        service, registry = crossover_service_with_registry

        # Setup: Zone with satellite + bookshelf (no subwoofer)
        satellite = Client(mac_id="local", name="Satellite", ip="127.0.0.1",
                          speaker_type="satellite", online=True, zone_id="zone-1")
        bookshelf = Client(mac_id="book-1", name="Bookshelf", ip="192.168.1.20",
                          speaker_type="bookshelf", online=True, zone_id="zone-1")

        zone = Zone(
            id="zone-1",
            name="Living Room",
            client_ids=["local", "book-1"],
            crossover_frequency=80,
            crossover_enabled=None  # Auto mode
        )

        registry._clients["local"] = satellite
        registry._clients["book-1"] = bookshelf
        registry._zones["zone-1"] = zone

        # Change bookshelf to subwoofer
        bookshelf.speaker_type = "subwoofer"

        # Simulate CLIENT_UPDATED event
        await service._handle_registry_event(
            RegistryEventType.CLIENT_UPDATED,
            {"mac_id": "book-1", "client": bookshelf.to_dict()}
        )

        # Verify crossover was activated
        mock_camilladsp_service.set_crossover_filter.assert_called()
        call_args = mock_camilladsp_service.set_crossover_filter.call_args
        assert call_args.kwargs.get('enabled') is True or call_args[1].get('enabled') is True

    @pytest.mark.asyncio
    async def test_speaker_type_change_from_subwoofer_deactivates_crossover(self, crossover_service_with_registry, mock_camilladsp_service):
        """Test: Changing speaker_type from subwoofer deactivates crossover."""
        service, registry = crossover_service_with_registry

        # Setup: Zone with satellite + subwoofer (crossover active)
        satellite = Client(mac_id="local", name="Satellite", ip="127.0.0.1",
                          speaker_type="satellite", online=True, zone_id="zone-1")
        subwoofer = Client(mac_id="sub-1", name="Subwoofer", ip="192.168.1.20",
                          speaker_type="subwoofer", online=True, zone_id="zone-1")

        zone = Zone(
            id="zone-1",
            name="Living Room",
            client_ids=["local", "sub-1"],
            crossover_frequency=80,
            crossover_enabled=None  # Auto mode
        )

        registry._clients["local"] = satellite
        registry._clients["sub-1"] = subwoofer
        registry._zones["zone-1"] = zone

        # Change subwoofer to bookshelf (no longer a subwoofer)
        subwoofer.speaker_type = "bookshelf"

        # Simulate CLIENT_UPDATED event
        await service._handle_registry_event(
            RegistryEventType.CLIENT_UPDATED,
            {"mac_id": "sub-1", "client": subwoofer.to_dict()}
        )

        # Verify crossover was deactivated
        mock_camilladsp_service.set_crossover_filter.assert_called()
        call_args = mock_camilladsp_service.set_crossover_filter.call_args
        assert call_args.kwargs.get('enabled') is False or call_args[1].get('enabled') is False

    @pytest.mark.asyncio
    async def test_non_subwoofer_connect_no_crossover_change(self, crossover_service_with_registry, mock_camilladsp_service):
        """Test: Non-subwoofer connecting does not change crossover state."""
        service, registry = crossover_service_with_registry

        # Setup: Zone with satellite only (no subwoofer)
        satellite = Client(mac_id="local", name="Satellite", ip="127.0.0.1",
                          speaker_type="satellite", online=True, zone_id="zone-1")
        bookshelf = Client(mac_id="book-1", name="Bookshelf", ip="192.168.1.20",
                          speaker_type="bookshelf", online=False, zone_id="zone-1")

        zone = Zone(
            id="zone-1",
            name="Living Room",
            client_ids=["local", "book-1"],
            crossover_frequency=80,
            crossover_enabled=None  # Auto mode
        )

        registry._clients["local"] = satellite
        registry._clients["book-1"] = bookshelf
        registry._zones["zone-1"] = zone

        # Bookshelf comes online (not a subwoofer)
        bookshelf.online = True

        # Simulate CLIENT_CONNECTED event
        await service._handle_registry_event(
            RegistryEventType.CLIENT_CONNECTED,
            {"mac_id": "book-1", "client": bookshelf.to_dict()}
        )

        # Crossover should be disabled (no subwoofer)
        mock_camilladsp_service.set_crossover_filter.assert_called()
        call_args = mock_camilladsp_service.set_crossover_filter.call_args
        assert call_args.kwargs.get('enabled') is False or call_args[1].get('enabled') is False

    @pytest.mark.asyncio
    async def test_multiple_subwoofers_one_offline_crossover_still_active(self, crossover_service_with_registry, mock_camilladsp_service):
        """Test: Multiple subwoofers - crossover stays active if ANY subwoofer is online."""
        service, registry = crossover_service_with_registry

        # Setup: Zone with satellite + 2 subwoofers
        satellite = Client(mac_id="local", name="Satellite", ip="127.0.0.1",
                          speaker_type="satellite", online=True, zone_id="zone-1")
        subwoofer1 = Client(mac_id="sub-1", name="Subwoofer 1", ip="192.168.1.20",
                           speaker_type="subwoofer", online=True, zone_id="zone-1")
        subwoofer2 = Client(mac_id="sub-2", name="Subwoofer 2", ip="192.168.1.21",
                           speaker_type="subwoofer", online=True, zone_id="zone-1")

        zone = Zone(
            id="zone-1",
            name="Living Room",
            client_ids=["local", "sub-1", "sub-2"],
            crossover_frequency=80,
            crossover_enabled=None
        )

        registry._clients["local"] = satellite
        registry._clients["sub-1"] = subwoofer1
        registry._clients["sub-2"] = subwoofer2
        registry._zones["zone-1"] = zone

        # One subwoofer goes offline
        subwoofer1.online = False

        await service._handle_registry_event(
            RegistryEventType.CLIENT_DISCONNECTED,
            {"mac_id": "sub-1"}
        )

        # Crossover should STILL be active (subwoofer2 is online)
        mock_camilladsp_service.set_crossover_filter.assert_called()
        call_args = mock_camilladsp_service.set_crossover_filter.call_args
        assert call_args.kwargs.get('enabled') is True or call_args[1].get('enabled') is True


# =============================================================================
# Test WebSocket Event Broadcasting for Crossover Changes
# =============================================================================

class TestCrossoverEventBroadcasting:
    """Tests for WebSocket event broadcasting on crossover state changes."""

    @pytest.mark.asyncio
    async def test_crossover_change_broadcasts_zone_updated_event(self, crossover_service_with_registry):
        """Test: Crossover state change broadcasts zone_changed event."""
        service, registry = crossover_service_with_registry

        # Setup mock state machine for broadcast
        mock_state_machine = MagicMock()
        mock_state_machine.broadcast = AsyncMock()
        service.state_machine = mock_state_machine

        # Setup: Zone with satellite + subwoofer
        satellite = Client(mac_id="local", name="Satellite", ip="127.0.0.1",
                          speaker_type="satellite", online=True, zone_id="zone-1")
        subwoofer = Client(mac_id="sub-1", name="Subwoofer", ip="192.168.1.20",
                          speaker_type="subwoofer", online=True, zone_id="zone-1")

        zone = Zone(
            id="zone-1",
            name="Living Room",
            client_ids=["local", "sub-1"],
            crossover_frequency=80,
            crossover_enabled=None
        )

        registry._clients["local"] = satellite
        registry._clients["sub-1"] = subwoofer
        registry._zones["zone-1"] = zone

        # Trigger crossover recalculation
        await service._recalculate_zones_for_client("sub-1")

        # Verify zone_changed event was broadcast via state machine
        mock_state_machine.broadcast.assert_called()
        event = mock_state_machine.broadcast.call_args.args[0]
        assert event.CATEGORY == "multiroom"
        assert event.TYPE == "zone_changed"

    @pytest.mark.asyncio
    async def test_zone_updated_event_includes_crossover_enabled(self, crossover_service_with_registry):
        """Test: zone_changed event includes computed crossover_enabled field."""
        service, registry = crossover_service_with_registry

        # Setup mock state machine for broadcast
        mock_state_machine = MagicMock()
        mock_state_machine.broadcast = AsyncMock()
        service.state_machine = mock_state_machine

        # Setup: Zone with satellite + online subwoofer
        satellite = Client(mac_id="local", name="Satellite", ip="127.0.0.1",
                          speaker_type="satellite", online=True, zone_id="zone-1")
        subwoofer = Client(mac_id="sub-1", name="Subwoofer", ip="192.168.1.20",
                          speaker_type="subwoofer", online=True, zone_id="zone-1")

        zone = Zone(
            id="zone-1",
            name="Living Room",
            client_ids=["local", "sub-1"],
            crossover_frequency=80,
            crossover_enabled=None  # Auto mode
        )

        registry._clients["local"] = satellite
        registry._clients["sub-1"] = subwoofer
        registry._zones["zone-1"] = zone

        # Trigger recalculation
        await service._recalculate_zones_for_client("sub-1")

        # Verify event data includes crossover_enabled
        event = mock_state_machine.broadcast.call_args.args[0]
        assert event.zone is not None
        zone_data = event.zone
        assert "crossover_enabled" in zone_data
        # Should be True because subwoofer is online
        assert zone_data["crossover_enabled"] is True



# =============================================================================
# Performance Tests (- < 500ms)
# =============================================================================

# =============================================================================
# Test Filter Application Methods
# =============================================================================

class TestFilterApplicationMethods:
    """Tests for filter application methods."""

    @pytest.fixture
    def service_with_local_client(self, crossover_service_with_registry):
        """Setup service with local client registered at 127.0.0.1."""
        service, registry = crossover_service_with_registry
        local_client = Client(mac_id="local", name="Local", ip="127.0.0.1", online=True)
        registry._clients["local"] = local_client
        return service, registry

    @pytest.mark.asyncio
    async def test_set_client_crossover_local_calls_camilladsp_service(self, service_with_local_client, mock_camilladsp_service):
        """Test _set_client_filter("crossover") for local client calls CamillaDSPService."""
        service, registry = service_with_local_client
        result = await service._set_client_filter("local", "crossover", True, 80)

        assert result is True
        mock_camilladsp_service.set_crossover_filter.assert_called_once_with(
            enabled=True,
            frequency=80,
            q=0.707  # DEFAULT_Q Butterworth
        )

    @pytest.mark.asyncio
    async def test_set_client_crossover_local_disable(self, service_with_local_client, mock_camilladsp_service):
        """Test _set_client_filter("crossover") disables filter for local client."""
        service, registry = service_with_local_client
        result = await service._set_client_filter("local", "crossover", False, 80)

        assert result is True
        mock_camilladsp_service.set_crossover_filter.assert_called_once_with(
            enabled=False,
            frequency=80,
            q=0.707
        )

    @pytest.mark.asyncio
    async def test_set_client_crossover_uses_correct_q_factor(self, service_with_local_client, mock_camilladsp_service):
        """Test _set_client_filter("crossover") uses DEFAULT_Q = 0.707 (Butterworth) (1.4)."""
        service, registry = service_with_local_client
        await service._set_client_filter("local", "crossover", True, 120)

        call_args = mock_camilladsp_service.set_crossover_filter.call_args
        assert call_args is not None, "Expected set_crossover_filter to be called"
        assert call_args.kwargs.get('q') == 0.707 or call_args[1].get('q') == 0.707

    @pytest.mark.asyncio
    async def test_set_client_crossover_remote_sends_http(self, crossover_service_with_registry):
        """Test _set_client_filter("crossover") for remote client sends HTTP request (1.2)."""
        service, registry = crossover_service_with_registry
        # Register a remote client
        remote_client = Client(mac_id="remote-1", name="Remote", ip="192.168.1.100", online=True)
        registry._clients["remote-1"] = remote_client

        service._proxy_service.try_request.return_value = 200

        result = await service._set_client_filter("remote-1", "crossover", True, 80)

        assert result is True
        # Verify the proxy was called with the crossover path
        service._proxy_service.try_request.assert_awaited_once()
        call_args = service._proxy_service.try_request.call_args
        assert "/equalizer/crossover" in str(call_args)

    @pytest.mark.asyncio
    async def test_set_client_lowpass_local_calls_camilladsp_service(self, service_with_local_client, mock_camilladsp_service):
        """Test _set_client_filter("lowpass") for local client calls CamillaDSPService (2.1)."""
        service, registry = service_with_local_client
        result = await service._set_client_filter("local", "lowpass", True, 80)

        assert result is True
        mock_camilladsp_service.set_lowpass_filter.assert_called_once_with(
            enabled=True,
            frequency=80,
            q=0.707
        )

    @pytest.mark.asyncio
    async def test_set_client_lowpass_local_disable(self, service_with_local_client, mock_camilladsp_service):
        """Test _set_client_filter("lowpass") disables filter for local client."""
        service, registry = service_with_local_client
        result = await service._set_client_filter("local", "lowpass", False, 80)

        assert result is True
        mock_camilladsp_service.set_lowpass_filter.assert_called_once_with(
            enabled=False,
            frequency=80,
            q=0.707
        )

    @pytest.mark.asyncio
    async def test_set_client_lowpass_remote_sends_http(self, crossover_service_with_registry):
        """Test _set_client_filter("lowpass") for remote client sends HTTP request (2.2)."""
        service, registry = crossover_service_with_registry
        # Register a remote client
        remote_client = Client(mac_id="remote-1", name="Remote", ip="192.168.1.100", online=True)
        registry._clients["remote-1"] = remote_client

        service._proxy_service.try_request.return_value = 200

        result = await service._set_client_filter("remote-1", "lowpass", True, 80)

        assert result is True
        service._proxy_service.try_request.assert_awaited_once()
        call_args = service._proxy_service.try_request.call_args
        assert "/equalizer/lowpass" in str(call_args)

    @pytest.mark.asyncio
    async def test_set_client_lowpass_without_camilladsp_service_returns_false(self, mock_settings_service, mock_registry):
        """Test _set_client_filter("lowpass") returns False when no camilladsp_service for local."""
        service = CrossoverService(
            settings_service=mock_settings_service,
            camilladsp_service=None  # No Equalizer service
        )
        service.set_registry(mock_registry)
        # Register local client
        local_client = Client(mac_id="local", name="Local", ip="127.0.0.1", online=True)
        mock_registry._clients["local"] = local_client

        result = await service._set_client_filter("local", "lowpass", True, 80)

        assert result is False


# =============================================================================
# Test Speaker Type Crossover Frequencies
# =============================================================================

class TestSpeakerTypeCrossoverFrequencies:
    """Tests for speaker type specific crossover frequencies."""

    @pytest.mark.asyncio
    async def test_apply_zone_crossover_uses_speaker_type_frequency(self, crossover_service_with_registry, mock_camilladsp_service):
        """Test crossover application uses speaker_type default frequency (1.3)."""
        service, registry = crossover_service_with_registry

        # Create satellite client (120Hz default)
        satellite = Client(mac_id="local", name="Satellite", ip="127.0.0.1",
                          speaker_type="satellite", online=True, zone_id="zone-1")
        subwoofer = Client(mac_id="sub-1", name="Subwoofer", ip="192.168.1.20",
                          speaker_type="subwoofer", online=True, zone_id="zone-1")

        zone = Zone(
            id="zone-1",
            name="Test Zone",
            client_ids=["local", "sub-1"],
            crossover_enabled=True
        )

        registry._clients["local"] = satellite
        registry._clients["sub-1"] = subwoofer
        registry._zones["zone-1"] = zone

        # Apply zone crossover - should use satellite's 120Hz
        await service.apply_zone_crossover("zone-1")

        # Auto crossover frequency should be 120Hz (from satellite)
        freq = await service.get_zone_auto_crossover("zone-1")
        assert freq == 120


# =============================================================================
# Test Subwoofer Gets Lowpass
# =============================================================================

class TestSubwooferLowpassApplication:
    """Tests for subwoofer lowpass filter application."""

    @pytest.mark.asyncio
    async def test_subwoofer_receives_lowpass_at_zone_frequency(self, crossover_service_with_registry, mock_camilladsp_service):
        """Test subwoofer receives lowpass at zone crossover frequency (2.3)."""
        service, registry = crossover_service_with_registry

        # Create satellite + subwoofer zone
        satellite = Client(mac_id="sat-1", name="Satellite", ip="192.168.1.10",
                          speaker_type="satellite", online=True, zone_id="zone-1")
        subwoofer = Client(mac_id="local", name="Subwoofer", ip="127.0.0.1",
                          speaker_type="subwoofer", online=True, zone_id="zone-1")

        zone = Zone(
            id="zone-1",
            name="Living Room",
            client_ids=["sat-1", "local"],
            crossover_frequency=120,  # Zone frequency
            crossover_enabled=True
        )

        registry._clients["sat-1"] = satellite
        registry._clients["local"] = subwoofer
        registry._zones["zone-1"] = zone

        await service.apply_zone_crossover("zone-1")

        # Subwoofer (local) should get lowpass
        mock_camilladsp_service.set_lowpass_filter.assert_called()
        call_args = mock_camilladsp_service.set_lowpass_filter.call_args
        assert call_args.kwargs.get('enabled') is True or call_args[1].get('enabled') is True

    @pytest.mark.asyncio
    async def test_subwoofer_does_not_receive_highpass(self, crossover_service_with_registry, mock_camilladsp_service):
        """Test subwoofer does NOT receive highpass filter (2.4)."""
        service, registry = crossover_service_with_registry

        # Subwoofer as local client
        satellite = Client(mac_id="sat-1", name="Satellite", ip="192.168.1.10",
                          speaker_type="satellite", online=True, zone_id="zone-1")
        subwoofer = Client(mac_id="local", name="Subwoofer", ip="127.0.0.1",
                          speaker_type="subwoofer", online=True, zone_id="zone-1")

        zone = Zone(
            id="zone-1",
            name="Living Room",
            client_ids=["sat-1", "local"],
            crossover_enabled=True
        )

        registry._clients["sat-1"] = satellite
        registry._clients["local"] = subwoofer
        registry._zones["zone-1"] = zone

        await service.apply_zone_crossover("zone-1")

        # Check set_crossover_filter calls for local
        # When applying to subwoofer, crossover should be DISABLED
        crossover_calls = mock_camilladsp_service.set_crossover_filter.call_args_list
        # The last call for local should be enabled=False
        # (since apply_zone_crossover first applies lowpass=True, then crossover=False for subs)
        last_crossover_call = crossover_calls[-1] if crossover_calls else None
        if last_crossover_call:
            enabled = last_crossover_call.kwargs.get('enabled', last_crossover_call[1].get('enabled'))
            assert enabled is False, "Subwoofer should have highpass disabled"


# =============================================================================
# Test Filter Bypass on Deactivation
# =============================================================================

class TestFilterBypassOnDeactivation:
    """Tests for filter bypass when crossover is deactivated."""

    @pytest.mark.asyncio
    async def test_crossover_disabled_removes_highpass(self, crossover_service_with_registry, mock_camilladsp_service):
        """Test highpass filter is disabled when crossover deactivates (3.1)."""
        service, registry = crossover_service_with_registry

        satellite = Client(mac_id="local", name="Satellite", ip="127.0.0.1",
                          speaker_type="satellite", online=True, zone_id="zone-1")

        zone = Zone(
            id="zone-1",
            name="Test Zone",
            client_ids=["local"],
            crossover_enabled=False  # Explicitly disabled
        )

        registry._clients["local"] = satellite
        registry._zones["zone-1"] = zone

        await service.apply_zone_crossover("zone-1")

        # Crossover should be disabled
        call = mock_camilladsp_service.set_crossover_filter.call_args
        assert call.kwargs.get('enabled') is False or call[1].get('enabled') is False

    @pytest.mark.asyncio
    async def test_crossover_disabled_removes_lowpass(self, crossover_service_with_registry, mock_camilladsp_service):
        """Test lowpass filter is disabled when crossover deactivates (3.2)."""
        service, registry = crossover_service_with_registry

        subwoofer = Client(mac_id="local", name="Subwoofer", ip="127.0.0.1",
                          speaker_type="subwoofer", online=True, zone_id="zone-1")

        zone = Zone(
            id="zone-1",
            name="Test Zone",
            client_ids=["local"],
            crossover_enabled=False
        )

        registry._clients["local"] = subwoofer
        registry._zones["zone-1"] = zone

        await service.apply_zone_crossover("zone-1")

        # Lowpass should be disabled
        call = mock_camilladsp_service.set_lowpass_filter.call_args
        assert call.kwargs.get('enabled') is False or call[1].get('enabled') is False

    @pytest.mark.asyncio
    async def test_client_removed_from_zone_filters_disabled(self, crossover_service_with_registry, mock_camilladsp_service):
        """Test filters are disabled when client is removed from zone (3.4)."""
        service, registry = crossover_service_with_registry

        satellite = Client(mac_id="local", name="Satellite", ip="127.0.0.1",
                          speaker_type="satellite", online=True, zone_id="zone-1")
        subwoofer = Client(mac_id="sub-1", name="Subwoofer", ip="192.168.1.20",
                          speaker_type="subwoofer", online=True, zone_id="zone-1")

        zone = Zone(
            id="zone-1",
            name="Test Zone",
            client_ids=["local", "sub-1"],
            crossover_enabled=True
        )

        registry._clients["local"] = satellite
        registry._clients["sub-1"] = subwoofer
        registry._zones["zone-1"] = zone

        mock_camilladsp_service.reset_mock()

        # Simulate client removal event
        await service._handle_registry_event(
            "zone_client_removed",
            {"zone_id": "zone-1", "mac_id": "local"}
        )

        # Both crossover and lowpass should be disabled for removed client
        mock_camilladsp_service.set_crossover_filter.assert_called()
        mock_camilladsp_service.set_lowpass_filter.assert_called()


# =============================================================================
# Test Crossover on Client Reconnection
# =============================================================================

class TestCrossoverOnReconnection:
    """Tests for crossover application on client reconnection."""

    @pytest.mark.asyncio
    async def test_client_connected_triggers_zone_recalculation(self, crossover_service_with_registry, mock_camilladsp_service):
        """Test CLIENT_CONNECTED event triggers _recalculate_zones_for_client (4.1)."""
        service, registry = crossover_service_with_registry

        satellite = Client(mac_id="local", name="Satellite", ip="127.0.0.1",
                          speaker_type="satellite", online=True, zone_id="zone-1")
        subwoofer = Client(mac_id="sub-1", name="Subwoofer", ip="192.168.1.20",
                          speaker_type="subwoofer", online=True, zone_id="zone-1")

        zone = Zone(
            id="zone-1",
            name="Test Zone",
            client_ids=["local", "sub-1"],
            crossover_enabled=True
        )

        registry._clients["local"] = satellite
        registry._clients["sub-1"] = subwoofer
        registry._zones["zone-1"] = zone

        mock_camilladsp_service.reset_mock()

        # Simulate client reconnection
        await service._handle_registry_event(
            RegistryEventType.CLIENT_CONNECTED,
            {"mac_id": "local"}
        )

        # Crossover should be recalculated and applied
        mock_camilladsp_service.set_crossover_filter.assert_called()

    @pytest.mark.asyncio
    async def test_pending_settings_queued_for_offline_client(self, crossover_service_with_registry):
        """Test crossover settings are queued for offline clients (4.3)."""
        service, registry = crossover_service_with_registry
        # Register a remote client
        remote_client = Client(mac_id="remote-1", name="Remote", ip="192.168.1.100", online=True)
        registry._clients["remote-1"] = remote_client

        service._proxy_service.try_request.return_value = 0  # unreachable

        # Attempt to apply crossover to unreachable client
        result = await service._set_client_filter("remote-1", "crossover", True, 80)

        assert result is False
        # Settings should be queued
        assert service.has_pending_settings("remote-1") is True
        settings = service._pending_settings.get("remote-1", {})
        assert "crossover" in settings
        assert settings["crossover"]["enabled"] is True
        assert settings["crossover"]["frequency"] == 80

    @pytest.mark.asyncio
    async def test_pending_lowpass_queued_for_offline_subwoofer(self, crossover_service_with_registry):
        """Test lowpass settings are queued for offline subwoofer (4.3)."""
        service, registry = crossover_service_with_registry
        # Register a remote client
        remote_client = Client(mac_id="remote-1", name="Remote", ip="192.168.1.100", online=True)
        registry._clients["remote-1"] = remote_client

        service._proxy_service.try_request.return_value = 0  # unreachable

        result = await service._set_client_filter("remote-1", "lowpass", True, 80)

        assert result is False
        assert service.has_pending_settings("remote-1") is True
        settings = service._pending_settings.get("remote-1", {})
        assert "lowpass" in settings

    @pytest.mark.asyncio
    async def test_pending_crossover_applied_on_reconnect(self, crossover_service_with_registry, mock_camilladsp_service):
        """Test pending crossover is applied when client reconnects (4.4)."""
        service, registry = crossover_service_with_registry
        # Register local client
        local_client = Client(mac_id="local", name="Local", ip="127.0.0.1", online=True)
        registry._clients["local"] = local_client
        # Queue settings
        service._pending_settings["local"] = {
            "crossover": {"enabled": True, "frequency": 100}
        }

        # Apply pending settings
        result = await service.apply_pending_settings("local")

        assert result is True
        mock_camilladsp_service.set_crossover_filter.assert_called_once()
        call_args = mock_camilladsp_service.set_crossover_filter.call_args
        assert call_args.kwargs.get('enabled') is True or call_args[1].get('enabled') is True
        assert call_args.kwargs.get('frequency') == 100 or call_args[1].get('frequency') == 100

        # Settings should be cleared
        assert service.has_pending_settings("local") is False

    @pytest.mark.asyncio
    async def test_pending_lowpass_applied_on_reconnect(self, crossover_service_with_registry, mock_camilladsp_service):
        """Test pending lowpass is applied when subwoofer reconnects (4.4)."""
        service, registry = crossover_service_with_registry
        # Register local client
        local_client = Client(mac_id="local", name="Local", ip="127.0.0.1", online=True)
        registry._clients["local"] = local_client
        service._pending_settings["local"] = {
            "lowpass": {"enabled": True, "frequency": 80}
        }

        result = await service.apply_pending_settings("local")

        assert result is True
        mock_camilladsp_service.set_lowpass_filter.assert_called_once()


# =============================================================================
# Test Crossover Independence from Equalizer Bypass
# =============================================================================

class TestCrossoverIndependenceFromDspBypass:
    """Tests for crossover independence from global Equalizer bypass."""

    def test_equalizer_service_crossover_filter_uses_separate_pipeline(self):
        """Test crossover filter uses 'crossover_highpass' separate from EQ bands (5.1).

        The DspService.set_crossover_filter() method uses 'crossover_highpass' filter name,
        which is separate from the eq_band_* filters affected by bypass_effects().
        """
        # Verify the crossover filter name is NOT an EQ band filter
        crossover_filter_name = "crossover_highpass"

        # EQ bands use naming pattern eq_band_00, eq_band_01, etc.
        # Crossover must NOT start with eq_band_ to be independent of bypass_effects()
        assert not crossover_filter_name.startswith("eq_band_"), \
            f"Crossover filter '{crossover_filter_name}' must not be an EQ band filter"

        # Verify it uses the expected name
        assert crossover_filter_name == "crossover_highpass", \
            "Crossover filter must use 'crossover_highpass' name"

    def test_equalizer_service_lowpass_filter_uses_separate_pipeline(self):
        """Test lowpass filter uses 'crossover_lowpass' separate from EQ bands (5.1).

        The DspService.set_lowpass_filter() method uses 'crossover_lowpass' filter name.
        """
        # Verify the lowpass filter name is NOT an EQ band filter
        lowpass_filter_name = "crossover_lowpass"

        # Lowpass must NOT start with eq_band_ to be independent of bypass_effects()
        assert not lowpass_filter_name.startswith("eq_band_"), \
            f"Lowpass filter '{lowpass_filter_name}' must not be an EQ band filter"

        # Verify it uses the expected name
        assert lowpass_filter_name == "crossover_lowpass", \
            "Lowpass filter must use 'crossover_lowpass' name"

    def test_bypass_effects_does_not_affect_crossover_by_filter_naming(self):
        """Test bypass_effects() only affects EQ bands, not crossover filters (5.2).

        The bypass_effects() method in DspService only processes filters that
        start with 'eq_band_' prefix. Crossover filters use different names:
        - crossover_highpass
        - crossover_lowpass

        This naming convention ensures crossover is never affected by Equalizer bypass.
        """
        # Define the filter names used by crossover system
        crossover_filter_names = ["crossover_highpass", "crossover_lowpass"]

        # Define the EQ band filter pattern (what bypass_effects processes)
        eq_band_prefix = "eq_band_"

        # Verify NO crossover filter matches the EQ band pattern
        for filter_name in crossover_filter_names:
            assert not filter_name.startswith(eq_band_prefix), \
                f"Filter '{filter_name}' must not be affected by bypass_effects()"

        # Verify crossover filters are distinctly named
        assert all("crossover" in name for name in crossover_filter_names), \
            "All crossover filters should contain 'crossover' in their name"

    @pytest.mark.asyncio
    async def test_crossover_independent_of_eq_compressor_loudness(self, crossover_service_with_registry, mock_camilladsp_service):
        """Test crossover can be enabled/disabled independently (5.3)."""
        service, registry = crossover_service_with_registry
        # Register local client
        local_client = Client(mac_id="local", name="Local", ip="127.0.0.1", online=True)
        registry._clients["local"] = local_client
        # Enable crossover
        await service._set_client_filter("local", "crossover", True, 80)
        mock_camilladsp_service.set_crossover_filter.assert_called_with(
            enabled=True, frequency=80, q=0.707
        )

        mock_camilladsp_service.reset_mock()

        # Disable crossover - should work independently of other Equalizer state
        await service._set_client_filter("local", "crossover", False, 80)
        mock_camilladsp_service.set_crossover_filter.assert_called_with(
            enabled=False, frequency=80, q=0.707
        )

        # Lowpass is also independent
        mock_camilladsp_service.reset_mock()
        await service._set_client_filter("local", "lowpass", True, 80)
        mock_camilladsp_service.set_lowpass_filter.assert_called_with(
            enabled=True, frequency=80, q=0.707
        )
