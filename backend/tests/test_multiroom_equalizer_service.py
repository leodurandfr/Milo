# backend/tests/test_multiroom_equalizer_service.py
"""
Unit tests for MultiroomEqualizerService.

Tests cover:
- Zone Equalizer propagation (AC2)
- Standalone client Equalizer management (AC3)
- CamillaDSP failure handling (AC4)
- Target-agnostic Equalizer methods
- Partial update methods
- Event broadcasting
"""
import pytest
from unittest.mock import Mock, AsyncMock

from backend.core.equalizer import MultiroomEqualizerService
from backend.core.multiroom.models import (
    EqualizerSettings,
    EqFilter,
    CompressorSettings,
    LoudnessSettings,
    FilterType,
    Client,
    Zone,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_registry():
    """Create mock ClientRegistryService"""
    registry = Mock()
    registry.get_zone = Mock(return_value=None)
    registry.get_client = Mock(return_value=None)
    registry.get_standalone_equalizer = Mock(return_value=None)
    registry.get_online_zone_clients = Mock(return_value=[])
    registry.set_standalone_equalizer = AsyncMock()
    registry.set_zone_equalizer = AsyncMock(return_value=True)
    registry.is_local_client = Mock(side_effect=lambda mac_id: mac_id == "local")
    registry.get_client_ip = Mock(side_effect=lambda mac_id: None if mac_id == "local" else "192.168.1.100")
    return registry


@pytest.fixture
def mock_camilladsp_service():
    """Create mock CamillaDSPService"""
    camilladsp_mock = Mock()
    camilladsp_mock.connected = True
    camilladsp_mock.set_filter = AsyncMock(return_value=True)
    camilladsp_mock.set_compressor = AsyncMock(return_value=True)
    camilladsp_mock.set_loudness = AsyncMock(return_value=True)
    camilladsp_mock.settings_service = None  # Prevent Mock auto-creation for await
    return camilladsp_mock


@pytest.fixture
def mock_state_machine():
    """Create mock state machine"""
    sm = Mock()
    sm.broadcast_event = AsyncMock()
    return sm


@pytest.fixture
def multiroom_equalizer_service(mock_registry, mock_camilladsp_service, mock_state_machine):
    """Create MultiroomEqualizerService with mocks"""
    service = MultiroomEqualizerService(
        client_registry_service=mock_registry,
        camilladsp_service=mock_camilladsp_service,
    )
    service.set_state_machine(mock_state_machine)
    return service


@pytest.fixture
def sample_equalizer_settings():
    """Create sample EqualizerSettings"""
    return EqualizerSettings(
        enabled=True,
        filters=[
            EqFilter(
                id="eq_band_00",
                frequency=100,
                gain=3.0,
                q=1.41,
                filter_type=FilterType.PEAKING,
                enabled=True,
            ),
            EqFilter(
                id="eq_band_01",
                frequency=1000,
                gain=-2.0,
                q=1.0,
                filter_type=FilterType.PEAKING,
                enabled=True,
            ),
        ],
        compressor=CompressorSettings(
            enabled=True,
            threshold=-20.0,
            ratio=4.0,
            attack=10.0,
            release=100.0,
            makeup_gain=2.0,
        ),
        loudness=LoudnessSettings(
            enabled=True,
            high_boost=5.0,
            low_boost=8.0,
        ),
    )


@pytest.fixture
def sample_zone():
    """Create sample Zone"""
    return Zone(
        id="zone-123",
        name="Living Room",
        client_ids=["local", "milo-client-1"],
        equalizer_settings=EqualizerSettings.default(),
    )


@pytest.fixture
def sample_client():
    """Create sample standalone Client"""
    return Client(
        mac_id="local",
        name="Main Speaker",
        ip="127.0.0.1",
        online=True,
        zone_id=None,
    )


@pytest.fixture
def sample_zone_client():
    """Create sample Client that is in a zone"""
    return Client(
        mac_id="milo-client-1",
        name="Kitchen Speaker",
        ip="192.168.1.100",
        online=True,
        zone_id="zone-123",
    )


# =============================================================================
# Initialization Tests
# =============================================================================

class TestMultiroomEqualizerServiceInit:
    """Test service initialization"""

    def test_create_service(self, mock_registry, mock_camilladsp_service):
        """Should create service with dependencies"""
        service = MultiroomEqualizerService(
            client_registry_service=mock_registry,
            camilladsp_service=mock_camilladsp_service,
        )
        assert service._registry == mock_registry
        assert service._camilladsp_service == mock_camilladsp_service

    def test_create_service_no_deps(self):
        """Should create service without dependencies (lazy injection)"""
        service = MultiroomEqualizerService()
        assert service._registry is None
        assert service._camilladsp_service is None

    def test_set_registry(self, multiroom_equalizer_service, mock_registry):
        """Should set registry via setter"""
        new_registry = Mock()
        multiroom_equalizer_service.set_registry(new_registry)
        assert multiroom_equalizer_service._registry == new_registry

    def test_set_camilladsp_service(self, multiroom_equalizer_service, mock_camilladsp_service):
        """Should set Equalizer service via setter"""
        new_camilladsp_mock = Mock()
        multiroom_equalizer_service.set_camilladsp_service(new_camilladsp_mock)
        assert multiroom_equalizer_service._camilladsp_service == new_camilladsp_mock

    def test_set_state_machine(self, multiroom_equalizer_service, mock_state_machine):
        """Should set state machine via setter"""
        new_sm = Mock()
        multiroom_equalizer_service.set_state_machine(new_sm)
        assert multiroom_equalizer_service._state_machine == new_sm


# =============================================================================
# Zone Equalizer Tests (AC2, AC5)
# =============================================================================

class TestZoneEqualizerMethods:
    """Test zone Equalizer methods"""

    @pytest.mark.asyncio
    async def test_apply_zone_equalizer_success(
        self, multiroom_equalizer_service, mock_registry, mock_camilladsp_service,
        mock_state_machine, sample_zone, sample_equalizer_settings
    ):
        """Should apply Equalizer settings to zone and all online clients"""
        # Setup
        mock_registry.get_zone.return_value = sample_zone
        online_client = Client(
            mac_id="local",
            name="Main",
            ip="127.0.0.1",
            online=True,
            zone_id="zone-123",
        )
        mock_registry.get_online_zone_clients.return_value = [online_client]
        mock_registry.get_client.return_value = online_client  # For _is_local_client()

        # Execute
        result = await multiroom_equalizer_service.apply_zone_equalizer("zone-123", sample_equalizer_settings)

        # Verify
        assert result is True
        mock_registry.set_zone_equalizer.assert_called_once_with("zone-123", sample_equalizer_settings)
        mock_state_machine.broadcast_event.assert_called_once()

        # Verify Equalizer was applied to local client
        assert mock_camilladsp_service.set_filter.call_count == 2  # 2 filters
        mock_camilladsp_service.set_compressor.assert_called_once()
        mock_camilladsp_service.set_loudness.assert_called_once()

    @pytest.mark.asyncio
    async def test_apply_zone_equalizer_zone_not_found(
        self, multiroom_equalizer_service, mock_registry, sample_equalizer_settings
    ):
        """Should raise ValueError when zone not found"""
        mock_registry.get_zone.return_value = None

        with pytest.raises(ValueError, match="Zone not found"):
            await multiroom_equalizer_service.apply_zone_equalizer("nonexistent", sample_equalizer_settings)

    @pytest.mark.asyncio
    async def test_apply_zone_equalizer_no_registry(self, sample_equalizer_settings):
        """Should return False when registry not available"""
        service = MultiroomEqualizerService()
        result = await service.apply_zone_equalizer("zone-123", sample_equalizer_settings)
        assert result is False

    @pytest.mark.asyncio
    async def test_apply_zone_equalizer_skips_offline_clients(
        self, multiroom_equalizer_service, mock_registry, mock_camilladsp_service,
        sample_zone, sample_equalizer_settings
    ):
        """Should only apply to ONLINE clients"""
        mock_registry.get_zone.return_value = sample_zone
        # No online clients
        mock_registry.get_online_zone_clients.return_value = []

        result = await multiroom_equalizer_service.apply_zone_equalizer("zone-123", sample_equalizer_settings)

        assert result is True
        # Equalizer methods should not be called (no online clients)
        mock_camilladsp_service.set_filter.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_zone_equalizer_found(
        self, multiroom_equalizer_service, mock_registry, sample_zone
    ):
        """Should return zone Equalizer settings"""
        mock_registry.get_zone.return_value = sample_zone

        result = await multiroom_equalizer_service.get_zone_equalizer("zone-123")

        assert result == sample_zone.equalizer_settings

    @pytest.mark.asyncio
    async def test_get_zone_equalizer_not_found(
        self, multiroom_equalizer_service, mock_registry
    ):
        """Should return None when zone not found"""
        mock_registry.get_zone.return_value = None

        result = await multiroom_equalizer_service.get_zone_equalizer("nonexistent")

        assert result is None


# =============================================================================
# Standalone Client Equalizer Tests (AC3)
# =============================================================================

class TestStandaloneClientEqualizerMethods:
    """Test standalone client Equalizer methods"""

    @pytest.mark.asyncio
    async def test_apply_client_equalizer_success(
        self, multiroom_equalizer_service, mock_registry, mock_camilladsp_service,
        mock_state_machine, sample_client, sample_equalizer_settings
    ):
        """Should apply Equalizer settings to standalone client"""
        mock_registry.get_client.return_value = sample_client

        result = await multiroom_equalizer_service.apply_client_equalizer("local", sample_equalizer_settings)

        assert result is True
        mock_registry.set_standalone_equalizer.assert_called_once_with("local", sample_equalizer_settings)
        mock_state_machine.broadcast_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_apply_client_equalizer_client_not_found(
        self, multiroom_equalizer_service, mock_registry, sample_equalizer_settings
    ):
        """Should raise ValueError when client not found"""
        mock_registry.get_client.return_value = None

        with pytest.raises(ValueError, match="Client not found"):
            await multiroom_equalizer_service.apply_client_equalizer("nonexistent", sample_equalizer_settings)

    @pytest.mark.asyncio
    async def test_apply_client_equalizer_client_in_zone(
        self, multiroom_equalizer_service, mock_registry, sample_zone_client, sample_equalizer_settings
    ):
        """Should raise ValueError when client is in a zone"""
        mock_registry.get_client.return_value = sample_zone_client

        with pytest.raises(ValueError, match="is in zone"):
            await multiroom_equalizer_service.apply_client_equalizer("milo-client-1", sample_equalizer_settings)

    @pytest.mark.asyncio
    async def test_get_client_equalizer_found(
        self, multiroom_equalizer_service, mock_registry, sample_equalizer_settings
    ):
        """Should return standalone client Equalizer settings"""
        mock_registry.get_standalone_equalizer.return_value = sample_equalizer_settings

        result = await multiroom_equalizer_service.get_client_equalizer("local")

        assert result == sample_equalizer_settings

    @pytest.mark.asyncio
    async def test_get_client_equalizer_not_found(
        self, multiroom_equalizer_service, mock_registry
    ):
        """Should return None when standalone Equalizer not found"""
        mock_registry.get_standalone_equalizer.return_value = None

        result = await multiroom_equalizer_service.get_client_equalizer("unknown")

        assert result is None


# =============================================================================
# Target-Agnostic Equalizer Tests
# =============================================================================

class TestTargetAgnosticEqualizerMethods:
    """Test target-agnostic Equalizer methods"""

    @pytest.mark.asyncio
    async def test_apply_equalizer_to_zone(
        self, multiroom_equalizer_service, mock_registry, sample_zone, sample_equalizer_settings
    ):
        """Should route to apply_zone_equalizer for zone target"""
        mock_registry.get_zone.return_value = sample_zone
        mock_registry.get_online_zone_clients.return_value = []

        result = await multiroom_equalizer_service.apply_equalizer("zone", "zone-123", sample_equalizer_settings)

        assert result is True
        mock_registry.get_zone.assert_called_with("zone-123")

    @pytest.mark.asyncio
    async def test_apply_equalizer_to_client(
        self, multiroom_equalizer_service, mock_registry, sample_client, sample_equalizer_settings
    ):
        """Should route to apply_client_equalizer for client target"""
        mock_registry.get_client.return_value = sample_client

        result = await multiroom_equalizer_service.apply_equalizer("client", "local", sample_equalizer_settings)

        assert result is True
        mock_registry.set_standalone_equalizer.assert_called_once()

    @pytest.mark.asyncio
    async def test_apply_equalizer_invalid_target_type(
        self, multiroom_equalizer_service, sample_equalizer_settings
    ):
        """Should raise ValueError for invalid target_type"""
        with pytest.raises(ValueError, match="Invalid target_type"):
            await multiroom_equalizer_service.apply_equalizer("invalid", "id", sample_equalizer_settings)

    @pytest.mark.asyncio
    async def test_get_equalizer_zone(
        self, multiroom_equalizer_service, mock_registry, sample_zone
    ):
        """Should route to get_zone_equalizer for zone target"""
        mock_registry.get_zone.return_value = sample_zone

        result = await multiroom_equalizer_service.get_equalizer("zone", "zone-123")

        assert result == sample_zone.equalizer_settings

    @pytest.mark.asyncio
    async def test_get_equalizer_client(
        self, multiroom_equalizer_service, mock_registry, sample_equalizer_settings
    ):
        """Should route to get_client_equalizer for client target"""
        mock_registry.get_standalone_equalizer.return_value = sample_equalizer_settings

        result = await multiroom_equalizer_service.get_equalizer("client", "local")

        assert result == sample_equalizer_settings

    @pytest.mark.asyncio
    async def test_get_equalizer_invalid_target_type(self, multiroom_equalizer_service):
        """Should raise ValueError for invalid target_type"""
        with pytest.raises(ValueError, match="Invalid target_type"):
            await multiroom_equalizer_service.get_equalizer("invalid", "id")


# =============================================================================
# CamillaDSP Application Tests (AC4)
# =============================================================================

class TestCamillaDspApplication:
    """Test CamillaDSP application with error handling"""

    @pytest.mark.asyncio
    async def test_apply_to_camilladsp_success(
        self, multiroom_equalizer_service, mock_registry, mock_camilladsp_service, sample_equalizer_settings
    ):
        """Should apply all settings to CamillaDSP"""
        # Set up registry to recognize "local" as local client
        local_client = Client(mac_id="local", name="Main", ip="127.0.0.1", online=True, zone_id=None)
        mock_registry.get_client.return_value = local_client

        result = await multiroom_equalizer_service._apply_to_camilladsp("local", sample_equalizer_settings)

        assert result is True
        # Verify all filters applied
        assert mock_camilladsp_service.set_filter.call_count == 2
        mock_camilladsp_service.set_compressor.assert_called_once()
        mock_camilladsp_service.set_loudness.assert_called_once()

    @pytest.mark.asyncio
    async def test_apply_to_camilladsp_disconnected(
        self, multiroom_equalizer_service, mock_registry, mock_camilladsp_service, sample_equalizer_settings
    ):
        """Should return False when CamillaDSP disconnected (AC4)"""
        mock_camilladsp_service.connected = False
        local_client = Client(mac_id="local", name="Main", ip="127.0.0.1", online=True, zone_id=None)
        mock_registry.get_client.return_value = local_client

        result = await multiroom_equalizer_service._apply_to_camilladsp("local", sample_equalizer_settings)

        assert result is False
        mock_camilladsp_service.set_filter.assert_not_called()

    @pytest.mark.asyncio
    async def test_apply_to_camilladsp_no_service(self, sample_equalizer_settings):
        """Should return False when Equalizer service not available"""
        service = MultiroomEqualizerService()
        # Set up registry to recognize "local" as local, but no Equalizer service
        mock_reg = Mock()
        local_client = Client(mac_id="local", name="Main", ip="127.0.0.1", online=True, zone_id=None)
        mock_reg.get_client.return_value = local_client
        service.set_registry(mock_reg)

        result = await service._apply_to_camilladsp("local", sample_equalizer_settings)

        assert result is False

    @pytest.mark.asyncio
    async def test_apply_to_camilladsp_remote_client_skipped(
        self, multiroom_equalizer_service, mock_camilladsp_service, sample_equalizer_settings
    ):
        """Should skip remote clients (not local)"""
        result = await multiroom_equalizer_service._apply_to_camilladsp("milo-client-1", sample_equalizer_settings)

        # Remote clients are skipped (return True, but no Equalizer calls)
        assert result is True
        mock_camilladsp_service.set_filter.assert_not_called()

    @pytest.mark.asyncio
    async def test_apply_to_camilladsp_filter_failure(
        self, multiroom_equalizer_service, mock_camilladsp_service, sample_equalizer_settings
    ):
        """Should continue on filter failure (AC4: no exception raised)"""
        mock_camilladsp_service.set_filter.return_value = False

        # Should not raise, just log warning
        result = await multiroom_equalizer_service._apply_to_camilladsp("local", sample_equalizer_settings)

        # Still returns True (settings saved, partial application)
        assert result is True

    @pytest.mark.asyncio
    async def test_apply_to_camilladsp_exception(
        self, multiroom_equalizer_service, mock_registry, mock_camilladsp_service, sample_equalizer_settings
    ):
        """Should handle exceptions gracefully (AC4)"""
        mock_camilladsp_service.set_filter.side_effect = Exception("Connection lost")
        local_client = Client(mac_id="local", name="Main", ip="127.0.0.1", online=True, zone_id=None)
        mock_registry.get_client.return_value = local_client

        result = await multiroom_equalizer_service._apply_to_camilladsp("local", sample_equalizer_settings)

        # Should return False but not raise
        assert result is False


# =============================================================================
# Partial Update Methods Tests
# =============================================================================

class TestPartialUpdateMethods:
    """Test partial Equalizer update methods"""

    @pytest.mark.asyncio
    async def test_update_filter(
        self, multiroom_equalizer_service, mock_registry, sample_zone
    ):
        """Should update single filter preserving others"""
        # Setup zone with default Equalizer
        sample_zone.equalizer_settings = EqualizerSettings.default()
        mock_registry.get_zone.return_value = sample_zone
        mock_registry.get_online_zone_clients.return_value = []

        # Update first filter gain
        result = await multiroom_equalizer_service.update_filter(
            "zone", "zone-123", "eq_band_00", gain=5.0
        )

        assert result is True
        # Check filter was updated
        updated_filter = sample_zone.equalizer_settings.filters[0]
        assert updated_filter.gain == 5.0

    @pytest.mark.asyncio
    async def test_update_filter_type(
        self, multiroom_equalizer_service, mock_registry, sample_zone
    ):
        """Should update filter type preserving other settings"""
        sample_zone.equalizer_settings = EqualizerSettings.default()
        mock_registry.get_zone.return_value = sample_zone
        mock_registry.get_online_zone_clients.return_value = []

        # Default filter type is PEAKING, change to LOWSHELF
        result = await multiroom_equalizer_service.update_filter(
            "zone", "zone-123", "eq_band_00", filter_type="Lowshelf"
        )

        assert result is True
        updated_filter = sample_zone.equalizer_settings.filters[0]
        assert updated_filter.filter_type == FilterType.LOWSHELF
        # Other values preserved
        assert updated_filter.gain == 0.0
        assert updated_filter.q == 1.41

    @pytest.mark.asyncio
    async def test_update_filter_not_found(
        self, multiroom_equalizer_service, mock_registry, sample_zone
    ):
        """Should raise ValueError when filter not found"""
        sample_zone.equalizer_settings = EqualizerSettings.default()
        mock_registry.get_zone.return_value = sample_zone

        with pytest.raises(ValueError, match="Filter not found"):
            await multiroom_equalizer_service.update_filter(
                "zone", "zone-123", "nonexistent", gain=5.0
            )

    @pytest.mark.asyncio
    async def test_update_compressor(
        self, multiroom_equalizer_service, mock_registry, sample_zone
    ):
        """Should update compressor preserving other settings"""
        sample_zone.equalizer_settings = EqualizerSettings.default()
        mock_registry.get_zone.return_value = sample_zone
        mock_registry.get_online_zone_clients.return_value = []

        result = await multiroom_equalizer_service.update_compressor(
            "zone", "zone-123", enabled=True, threshold=-30.0
        )

        assert result is True
        comp = sample_zone.equalizer_settings.compressor
        assert comp.enabled is True
        assert comp.threshold == -30.0
        # Other values preserved
        assert comp.ratio == 4.0

    @pytest.mark.asyncio
    async def test_update_loudness(
        self, multiroom_equalizer_service, mock_registry, sample_zone
    ):
        """Should update loudness preserving other settings"""
        sample_zone.equalizer_settings = EqualizerSettings.default()
        mock_registry.get_zone.return_value = sample_zone
        mock_registry.get_online_zone_clients.return_value = []

        result = await multiroom_equalizer_service.update_loudness(
            "zone", "zone-123", enabled=True, low_boost=10.0
        )

        assert result is True
        loud = sample_zone.equalizer_settings.loudness
        assert loud.enabled is True
        assert loud.low_boost == 10.0
        # Other values preserved
        assert loud.high_boost == 5.0

    @pytest.mark.asyncio
    async def test_update_equalizer_enabled(
        self, multiroom_equalizer_service, mock_registry, sample_zone
    ):
        """Should update global Equalizer enabled state"""
        sample_zone.equalizer_settings = EqualizerSettings.default()
        mock_registry.get_zone.return_value = sample_zone
        mock_registry.get_online_zone_clients.return_value = []

        result = await multiroom_equalizer_service.update_equalizer_enabled(
            "zone", "zone-123", enabled=False
        )

        assert result is True
        assert sample_zone.equalizer_settings.enabled is False


# =============================================================================
# Event Broadcasting Tests
# =============================================================================

class TestEventBroadcasting:
    """Test WebSocket event broadcasting"""

    @pytest.mark.asyncio
    async def test_broadcast_zone_equalizer_event(
        self, multiroom_equalizer_service, mock_registry, mock_state_machine,
        sample_zone, sample_equalizer_settings
    ):
        """Should broadcast equalizer_changed event for zone"""
        mock_registry.get_zone.return_value = sample_zone
        mock_registry.get_online_zone_clients.return_value = []

        await multiroom_equalizer_service.apply_zone_equalizer("zone-123", sample_equalizer_settings)

        mock_state_machine.broadcast_event.assert_called_once_with(
            "multiroom",
            "equalizer_changed",
            {
                "target_type": "zone",
                "target_id": "zone-123",
                "equalizer_settings": sample_equalizer_settings.to_dict(),
            },
        )

    @pytest.mark.asyncio
    async def test_broadcast_client_equalizer_event(
        self, multiroom_equalizer_service, mock_registry, mock_state_machine,
        sample_client, sample_equalizer_settings
    ):
        """Should broadcast equalizer_changed event for client"""
        mock_registry.get_client.return_value = sample_client

        await multiroom_equalizer_service.apply_client_equalizer("local", sample_equalizer_settings)

        mock_state_machine.broadcast_event.assert_called_once_with(
            "multiroom",
            "equalizer_changed",
            {
                "target_type": "client",
                "target_id": "local",
                "equalizer_settings": sample_equalizer_settings.to_dict(),
            },
        )

    @pytest.mark.asyncio
    async def test_no_broadcast_without_state_machine(
        self, mock_registry, sample_zone, sample_equalizer_settings
    ):
        """Should handle missing state machine gracefully"""
        service = MultiroomEqualizerService(client_registry_service=mock_registry)
        mock_registry.get_zone.return_value = sample_zone
        mock_registry.get_online_zone_clients.return_value = []

        # Should not raise
        result = await service.apply_zone_equalizer("zone-123", sample_equalizer_settings)
        assert result is True
