# backend/tests/test_multiroom_dsp_service.py
"""
Unit tests for MultiroomDspService.

Tests cover:
- Zone DSP propagation (AC2)
- Standalone client DSP management (AC3)
- CamillaDSP failure handling (AC4)
- Target-agnostic DSP methods
- Partial update methods
- Event broadcasting
"""
import pytest
from unittest.mock import Mock, AsyncMock, MagicMock

from backend.core.dsp import MultiroomDspService
from backend.core.multiroom.models import (
    DspSettings,
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
    registry.get_standalone_dsp = Mock(return_value=None)
    registry.get_online_zone_clients = Mock(return_value=[])
    registry.set_standalone_dsp = AsyncMock()
    registry.set_zone_dsp = AsyncMock(return_value=True)
    return registry


@pytest.fixture
def mock_dsp_service():
    """Create mock CamillaDSPService"""
    dsp = Mock()
    dsp.connected = True
    dsp.set_filter = AsyncMock(return_value=True)
    dsp.set_compressor = AsyncMock(return_value=True)
    dsp.set_loudness = AsyncMock(return_value=True)
    dsp.settings_service = None  # Prevent Mock auto-creation for await
    return dsp


@pytest.fixture
def mock_state_machine():
    """Create mock state machine"""
    sm = Mock()
    sm.broadcast_event = AsyncMock()
    return sm


@pytest.fixture
def multiroom_dsp_service(mock_registry, mock_dsp_service, mock_state_machine):
    """Create MultiroomDspService with mocks"""
    service = MultiroomDspService(
        client_registry_service=mock_registry,
        camilladsp_service=mock_dsp_service,
    )
    service.set_state_machine(mock_state_machine)
    return service


@pytest.fixture
def sample_dsp_settings():
    """Create sample DspSettings"""
    return DspSettings(
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
        dsp_settings=DspSettings.default(),
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

class TestMultiroomDspServiceInit:
    """Test service initialization"""

    def test_create_service(self, mock_registry, mock_dsp_service):
        """Should create service with dependencies"""
        service = MultiroomDspService(
            client_registry_service=mock_registry,
            camilladsp_service=mock_dsp_service,
        )
        assert service._registry == mock_registry
        assert service._dsp_service == mock_dsp_service

    def test_create_service_no_deps(self):
        """Should create service without dependencies (lazy injection)"""
        service = MultiroomDspService()
        assert service._registry is None
        assert service._dsp_service is None

    def test_set_registry(self, multiroom_dsp_service, mock_registry):
        """Should set registry via setter"""
        new_registry = Mock()
        multiroom_dsp_service.set_registry(new_registry)
        assert multiroom_dsp_service._registry == new_registry

    def test_set_dsp_service(self, multiroom_dsp_service, mock_dsp_service):
        """Should set DSP service via setter"""
        new_dsp = Mock()
        multiroom_dsp_service.set_dsp_service(new_dsp)
        assert multiroom_dsp_service._dsp_service == new_dsp

    def test_set_state_machine(self, multiroom_dsp_service, mock_state_machine):
        """Should set state machine via setter"""
        new_sm = Mock()
        multiroom_dsp_service.set_state_machine(new_sm)
        assert multiroom_dsp_service._state_machine == new_sm


# =============================================================================
# Zone DSP Tests (AC2, AC5)
# =============================================================================

class TestZoneDspMethods:
    """Test zone DSP methods"""

    @pytest.mark.asyncio
    async def test_apply_zone_dsp_success(
        self, multiroom_dsp_service, mock_registry, mock_dsp_service,
        mock_state_machine, sample_zone, sample_dsp_settings
    ):
        """Should apply DSP settings to zone and all online clients"""
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
        result = await multiroom_dsp_service.apply_zone_dsp("zone-123", sample_dsp_settings)

        # Verify
        assert result is True
        mock_registry.set_zone_dsp.assert_called_once_with("zone-123", sample_dsp_settings)
        mock_state_machine.broadcast_event.assert_called_once()

        # Verify DSP was applied to local client
        assert mock_dsp_service.set_filter.call_count == 2  # 2 filters
        mock_dsp_service.set_compressor.assert_called_once()
        mock_dsp_service.set_loudness.assert_called_once()

    @pytest.mark.asyncio
    async def test_apply_zone_dsp_zone_not_found(
        self, multiroom_dsp_service, mock_registry, sample_dsp_settings
    ):
        """Should raise ValueError when zone not found"""
        mock_registry.get_zone.return_value = None

        with pytest.raises(ValueError, match="Zone not found"):
            await multiroom_dsp_service.apply_zone_dsp("nonexistent", sample_dsp_settings)

    @pytest.mark.asyncio
    async def test_apply_zone_dsp_no_registry(self, sample_dsp_settings):
        """Should return False when registry not available"""
        service = MultiroomDspService()
        result = await service.apply_zone_dsp("zone-123", sample_dsp_settings)
        assert result is False

    @pytest.mark.asyncio
    async def test_apply_zone_dsp_skips_offline_clients(
        self, multiroom_dsp_service, mock_registry, mock_dsp_service,
        sample_zone, sample_dsp_settings
    ):
        """Should only apply to ONLINE clients"""
        mock_registry.get_zone.return_value = sample_zone
        # No online clients
        mock_registry.get_online_zone_clients.return_value = []

        result = await multiroom_dsp_service.apply_zone_dsp("zone-123", sample_dsp_settings)

        assert result is True
        # DSP methods should not be called (no online clients)
        mock_dsp_service.set_filter.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_zone_dsp_found(
        self, multiroom_dsp_service, mock_registry, sample_zone
    ):
        """Should return zone DSP settings"""
        mock_registry.get_zone.return_value = sample_zone

        result = await multiroom_dsp_service.get_zone_dsp("zone-123")

        assert result == sample_zone.dsp_settings

    @pytest.mark.asyncio
    async def test_get_zone_dsp_not_found(
        self, multiroom_dsp_service, mock_registry
    ):
        """Should return None when zone not found"""
        mock_registry.get_zone.return_value = None

        result = await multiroom_dsp_service.get_zone_dsp("nonexistent")

        assert result is None


# =============================================================================
# Standalone Client DSP Tests (AC3)
# =============================================================================

class TestStandaloneClientDspMethods:
    """Test standalone client DSP methods"""

    @pytest.mark.asyncio
    async def test_apply_client_dsp_success(
        self, multiroom_dsp_service, mock_registry, mock_dsp_service,
        mock_state_machine, sample_client, sample_dsp_settings
    ):
        """Should apply DSP settings to standalone client"""
        mock_registry.get_client.return_value = sample_client

        result = await multiroom_dsp_service.apply_client_dsp("local", sample_dsp_settings)

        assert result is True
        mock_registry.set_standalone_dsp.assert_called_once_with("local", sample_dsp_settings)
        mock_state_machine.broadcast_event.assert_called_once()

    @pytest.mark.asyncio
    async def test_apply_client_dsp_client_not_found(
        self, multiroom_dsp_service, mock_registry, sample_dsp_settings
    ):
        """Should raise ValueError when client not found"""
        mock_registry.get_client.return_value = None

        with pytest.raises(ValueError, match="Client not found"):
            await multiroom_dsp_service.apply_client_dsp("nonexistent", sample_dsp_settings)

    @pytest.mark.asyncio
    async def test_apply_client_dsp_client_in_zone(
        self, multiroom_dsp_service, mock_registry, sample_zone_client, sample_dsp_settings
    ):
        """Should raise ValueError when client is in a zone"""
        mock_registry.get_client.return_value = sample_zone_client

        with pytest.raises(ValueError, match="is in zone"):
            await multiroom_dsp_service.apply_client_dsp("milo-client-1", sample_dsp_settings)

    @pytest.mark.asyncio
    async def test_get_client_dsp_found(
        self, multiroom_dsp_service, mock_registry, sample_dsp_settings
    ):
        """Should return standalone client DSP settings"""
        mock_registry.get_standalone_dsp.return_value = sample_dsp_settings

        result = await multiroom_dsp_service.get_client_dsp("local")

        assert result == sample_dsp_settings

    @pytest.mark.asyncio
    async def test_get_client_dsp_not_found(
        self, multiroom_dsp_service, mock_registry
    ):
        """Should return None when standalone DSP not found"""
        mock_registry.get_standalone_dsp.return_value = None

        result = await multiroom_dsp_service.get_client_dsp("unknown")

        assert result is None


# =============================================================================
# Target-Agnostic DSP Tests
# =============================================================================

class TestTargetAgnosticDspMethods:
    """Test target-agnostic DSP methods"""

    @pytest.mark.asyncio
    async def test_apply_dsp_to_zone(
        self, multiroom_dsp_service, mock_registry, sample_zone, sample_dsp_settings
    ):
        """Should route to apply_zone_dsp for zone target"""
        mock_registry.get_zone.return_value = sample_zone
        mock_registry.get_online_zone_clients.return_value = []

        result = await multiroom_dsp_service.apply_dsp("zone", "zone-123", sample_dsp_settings)

        assert result is True
        mock_registry.get_zone.assert_called_with("zone-123")

    @pytest.mark.asyncio
    async def test_apply_dsp_to_client(
        self, multiroom_dsp_service, mock_registry, sample_client, sample_dsp_settings
    ):
        """Should route to apply_client_dsp for client target"""
        mock_registry.get_client.return_value = sample_client

        result = await multiroom_dsp_service.apply_dsp("client", "local", sample_dsp_settings)

        assert result is True
        mock_registry.set_standalone_dsp.assert_called_once()

    @pytest.mark.asyncio
    async def test_apply_dsp_invalid_target_type(
        self, multiroom_dsp_service, sample_dsp_settings
    ):
        """Should raise ValueError for invalid target_type"""
        with pytest.raises(ValueError, match="Invalid target_type"):
            await multiroom_dsp_service.apply_dsp("invalid", "id", sample_dsp_settings)

    @pytest.mark.asyncio
    async def test_get_dsp_zone(
        self, multiroom_dsp_service, mock_registry, sample_zone
    ):
        """Should route to get_zone_dsp for zone target"""
        mock_registry.get_zone.return_value = sample_zone

        result = await multiroom_dsp_service.get_dsp("zone", "zone-123")

        assert result == sample_zone.dsp_settings

    @pytest.mark.asyncio
    async def test_get_dsp_client(
        self, multiroom_dsp_service, mock_registry, sample_dsp_settings
    ):
        """Should route to get_client_dsp for client target"""
        mock_registry.get_standalone_dsp.return_value = sample_dsp_settings

        result = await multiroom_dsp_service.get_dsp("client", "local")

        assert result == sample_dsp_settings

    @pytest.mark.asyncio
    async def test_get_dsp_invalid_target_type(self, multiroom_dsp_service):
        """Should raise ValueError for invalid target_type"""
        with pytest.raises(ValueError, match="Invalid target_type"):
            await multiroom_dsp_service.get_dsp("invalid", "id")


# =============================================================================
# CamillaDSP Application Tests (AC4)
# =============================================================================

class TestCamillaDspApplication:
    """Test CamillaDSP application with error handling"""

    @pytest.mark.asyncio
    async def test_apply_to_camilladsp_success(
        self, multiroom_dsp_service, mock_registry, mock_dsp_service, sample_dsp_settings
    ):
        """Should apply all settings to CamillaDSP"""
        # Set up registry to recognize "local" as local client
        local_client = Client(mac_id="local", name="Main", ip="127.0.0.1", online=True, zone_id=None)
        mock_registry.get_client.return_value = local_client

        result = await multiroom_dsp_service._apply_to_camilladsp("local", sample_dsp_settings)

        assert result is True
        # Verify all filters applied
        assert mock_dsp_service.set_filter.call_count == 2
        mock_dsp_service.set_compressor.assert_called_once()
        mock_dsp_service.set_loudness.assert_called_once()

    @pytest.mark.asyncio
    async def test_apply_to_camilladsp_disconnected(
        self, multiroom_dsp_service, mock_registry, mock_dsp_service, sample_dsp_settings
    ):
        """Should return False when CamillaDSP disconnected (AC4)"""
        mock_dsp_service.connected = False
        local_client = Client(mac_id="local", name="Main", ip="127.0.0.1", online=True, zone_id=None)
        mock_registry.get_client.return_value = local_client

        result = await multiroom_dsp_service._apply_to_camilladsp("local", sample_dsp_settings)

        assert result is False
        mock_dsp_service.set_filter.assert_not_called()

    @pytest.mark.asyncio
    async def test_apply_to_camilladsp_no_service(self, sample_dsp_settings):
        """Should return False when DSP service not available"""
        service = MultiroomDspService()
        # Set up registry to recognize "local" as local, but no DSP service
        mock_reg = Mock()
        local_client = Client(mac_id="local", name="Main", ip="127.0.0.1", online=True, zone_id=None)
        mock_reg.get_client.return_value = local_client
        service.set_registry(mock_reg)

        result = await service._apply_to_camilladsp("local", sample_dsp_settings)

        assert result is False

    @pytest.mark.asyncio
    async def test_apply_to_camilladsp_remote_client_skipped(
        self, multiroom_dsp_service, mock_dsp_service, sample_dsp_settings
    ):
        """Should skip remote clients (not local)"""
        result = await multiroom_dsp_service._apply_to_camilladsp("milo-client-1", sample_dsp_settings)

        # Remote clients are skipped (return True, but no DSP calls)
        assert result is True
        mock_dsp_service.set_filter.assert_not_called()

    @pytest.mark.asyncio
    async def test_apply_to_camilladsp_filter_failure(
        self, multiroom_dsp_service, mock_dsp_service, sample_dsp_settings
    ):
        """Should continue on filter failure (AC4: no exception raised)"""
        mock_dsp_service.set_filter.return_value = False

        # Should not raise, just log warning
        result = await multiroom_dsp_service._apply_to_camilladsp("local", sample_dsp_settings)

        # Still returns True (settings saved, partial application)
        assert result is True

    @pytest.mark.asyncio
    async def test_apply_to_camilladsp_exception(
        self, multiroom_dsp_service, mock_registry, mock_dsp_service, sample_dsp_settings
    ):
        """Should handle exceptions gracefully (AC4)"""
        mock_dsp_service.set_filter.side_effect = Exception("Connection lost")
        local_client = Client(mac_id="local", name="Main", ip="127.0.0.1", online=True, zone_id=None)
        mock_registry.get_client.return_value = local_client

        result = await multiroom_dsp_service._apply_to_camilladsp("local", sample_dsp_settings)

        # Should return False but not raise
        assert result is False


# =============================================================================
# Partial Update Methods Tests
# =============================================================================

class TestPartialUpdateMethods:
    """Test partial DSP update methods"""

    @pytest.mark.asyncio
    async def test_update_filter(
        self, multiroom_dsp_service, mock_registry, sample_zone
    ):
        """Should update single filter preserving others"""
        # Setup zone with default DSP
        sample_zone.dsp_settings = DspSettings.default()
        mock_registry.get_zone.return_value = sample_zone
        mock_registry.get_online_zone_clients.return_value = []

        # Update first filter gain
        result = await multiroom_dsp_service.update_filter(
            "zone", "zone-123", "eq_band_00", gain=5.0
        )

        assert result is True
        # Check filter was updated
        updated_filter = sample_zone.dsp_settings.filters[0]
        assert updated_filter.gain == 5.0

    @pytest.mark.asyncio
    async def test_update_filter_type(
        self, multiroom_dsp_service, mock_registry, sample_zone
    ):
        """Should update filter type preserving other settings"""
        sample_zone.dsp_settings = DspSettings.default()
        mock_registry.get_zone.return_value = sample_zone
        mock_registry.get_online_zone_clients.return_value = []

        # Default filter type is PEAKING, change to LOWSHELF
        result = await multiroom_dsp_service.update_filter(
            "zone", "zone-123", "eq_band_00", filter_type="Lowshelf"
        )

        assert result is True
        updated_filter = sample_zone.dsp_settings.filters[0]
        assert updated_filter.filter_type == FilterType.LOWSHELF
        # Other values preserved
        assert updated_filter.gain == 0.0
        assert updated_filter.q == 1.41

    @pytest.mark.asyncio
    async def test_update_filter_not_found(
        self, multiroom_dsp_service, mock_registry, sample_zone
    ):
        """Should raise ValueError when filter not found"""
        sample_zone.dsp_settings = DspSettings.default()
        mock_registry.get_zone.return_value = sample_zone

        with pytest.raises(ValueError, match="Filter not found"):
            await multiroom_dsp_service.update_filter(
                "zone", "zone-123", "nonexistent", gain=5.0
            )

    @pytest.mark.asyncio
    async def test_update_compressor(
        self, multiroom_dsp_service, mock_registry, sample_zone
    ):
        """Should update compressor preserving other settings"""
        sample_zone.dsp_settings = DspSettings.default()
        mock_registry.get_zone.return_value = sample_zone
        mock_registry.get_online_zone_clients.return_value = []

        result = await multiroom_dsp_service.update_compressor(
            "zone", "zone-123", enabled=True, threshold=-30.0
        )

        assert result is True
        comp = sample_zone.dsp_settings.compressor
        assert comp.enabled is True
        assert comp.threshold == -30.0
        # Other values preserved
        assert comp.ratio == 4.0

    @pytest.mark.asyncio
    async def test_update_loudness(
        self, multiroom_dsp_service, mock_registry, sample_zone
    ):
        """Should update loudness preserving other settings"""
        sample_zone.dsp_settings = DspSettings.default()
        mock_registry.get_zone.return_value = sample_zone
        mock_registry.get_online_zone_clients.return_value = []

        result = await multiroom_dsp_service.update_loudness(
            "zone", "zone-123", enabled=True, low_boost=10.0
        )

        assert result is True
        loud = sample_zone.dsp_settings.loudness
        assert loud.enabled is True
        assert loud.low_boost == 10.0
        # Other values preserved
        assert loud.high_boost == 5.0

    @pytest.mark.asyncio
    async def test_update_dsp_enabled(
        self, multiroom_dsp_service, mock_registry, sample_zone
    ):
        """Should update global DSP enabled state"""
        sample_zone.dsp_settings = DspSettings.default()
        mock_registry.get_zone.return_value = sample_zone
        mock_registry.get_online_zone_clients.return_value = []

        result = await multiroom_dsp_service.update_dsp_enabled(
            "zone", "zone-123", enabled=False
        )

        assert result is True
        assert sample_zone.dsp_settings.enabled is False


# =============================================================================
# Event Broadcasting Tests
# =============================================================================

class TestEventBroadcasting:
    """Test WebSocket event broadcasting"""

    @pytest.mark.asyncio
    async def test_broadcast_zone_dsp_event(
        self, multiroom_dsp_service, mock_registry, mock_state_machine,
        sample_zone, sample_dsp_settings
    ):
        """Should broadcast dsp_changed event for zone"""
        mock_registry.get_zone.return_value = sample_zone
        mock_registry.get_online_zone_clients.return_value = []

        await multiroom_dsp_service.apply_zone_dsp("zone-123", sample_dsp_settings)

        mock_state_machine.broadcast_event.assert_called_once_with(
            "multiroom",
            "dsp_changed",
            {
                "target_type": "zone",
                "target_id": "zone-123",
                "dsp_settings": sample_dsp_settings.to_dict(),
            },
        )

    @pytest.mark.asyncio
    async def test_broadcast_client_dsp_event(
        self, multiroom_dsp_service, mock_registry, mock_state_machine,
        sample_client, sample_dsp_settings
    ):
        """Should broadcast dsp_changed event for client"""
        mock_registry.get_client.return_value = sample_client

        await multiroom_dsp_service.apply_client_dsp("local", sample_dsp_settings)

        mock_state_machine.broadcast_event.assert_called_once_with(
            "multiroom",
            "dsp_changed",
            {
                "target_type": "client",
                "target_id": "local",
                "dsp_settings": sample_dsp_settings.to_dict(),
            },
        )

    @pytest.mark.asyncio
    async def test_no_broadcast_without_state_machine(
        self, mock_registry, sample_zone, sample_dsp_settings
    ):
        """Should handle missing state machine gracefully"""
        service = MultiroomDspService(client_registry_service=mock_registry)
        mock_registry.get_zone.return_value = sample_zone
        mock_registry.get_online_zone_clients.return_value = []

        # Should not raise
        result = await service.apply_zone_dsp("zone-123", sample_dsp_settings)
        assert result is True
