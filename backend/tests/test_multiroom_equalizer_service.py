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
    registry.get_client_equalizer = Mock(return_value=None)
    registry.get_online_zone_clients = Mock(return_value=[])
    registry.set_client_equalizer = AsyncMock()
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
    camilladsp_mock.set_mono = AsyncMock(return_value=True)
    camilladsp_mock.set_active_preset = AsyncMock(return_value=None)
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

        # Verify Equalizer was applied to local client
        assert mock_camilladsp_service.set_filter.call_count == 2  # 2 filters
        mock_camilladsp_service.set_compressor.assert_called_once()
        mock_camilladsp_service.set_loudness.assert_called_once()
        mock_camilladsp_service.set_mono.assert_called_once()

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
        mock_registry.set_client_equalizer.assert_called_once_with("local", sample_equalizer_settings)

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
        mock_registry.get_client_equalizer.return_value = sample_equalizer_settings

        result = await multiroom_equalizer_service.get_client_equalizer("local")

        assert result == sample_equalizer_settings

    @pytest.mark.asyncio
    async def test_get_client_equalizer_not_found(
        self, multiroom_equalizer_service, mock_registry
    ):
        """Should return None when standalone Equalizer not found"""
        mock_registry.get_client_equalizer.return_value = None

        result = await multiroom_equalizer_service.get_client_equalizer("unknown")

        assert result is None


# =============================================================================
# Standalone Client Preset Tests (preset NAME persistence)
# =============================================================================

class TestStandaloneClientPresetPersistence:
    """Regression tests for the 'remote client preset name lost' bug.

    A standalone remote client that has never had its EQ saved has no entry in
    the registry's standalone-equalizer store. The preset-name write paths
    (load_client_preset / save_custom_preset) must create the entry on demand
    and persist active_preset — exactly like the local path does — instead of
    raising (which the route turned into a 404, dropping the preset name while
    the filter gains survived via the separate gains-write path).
    """

    @pytest.fixture
    def fresh_standalone_client(self):
        """A registered, online, standalone remote client with NO saved EQ entry."""
        return Client(
            mac_id="dc:a6:32:aa:bb:cc",
            name="Bedroom",
            ip="192.168.1.100",
            online=True,
            zone_id=None,
        )

    @pytest.mark.asyncio
    async def test_load_client_preset_creates_entry_for_fresh_standalone_client(
        self, multiroom_equalizer_service, mock_registry, fresh_standalone_client
    ):
        """Picking a preset on a fresh remote client must persist the preset NAME
        (no pre-existing standalone-equalizer entry required)."""
        mock_registry.get_client_equalizer.return_value = None  # never saved
        mock_registry.get_client.return_value = fresh_standalone_client

        result = await multiroom_equalizer_service.load_client_preset(
            "dc:a6:32:aa:bb:cc", "bass_boost"
        )

        assert result is True
        mock_registry.set_client_equalizer.assert_called_once()
        persisted = mock_registry.set_client_equalizer.call_args.args[1]
        # The preset NAME is the thing that used to be lost — assert it persists.
        assert persisted.active_preset == "bass_boost"
        # And the gains were built from the chosen preset.
        assert [round(f.gain) for f in persisted.filters] == [6, 5, 4, 2, 0, 0, 0, 0, 0, 0]

    @pytest.mark.asyncio
    async def test_load_client_preset_raises_for_unknown_client(
        self, multiroom_equalizer_service, mock_registry
    ):
        """A genuinely unknown client still raises (route → 404), unchanged."""
        mock_registry.get_client_equalizer.return_value = None
        mock_registry.get_client.return_value = None

        with pytest.raises(ValueError, match="Client not found"):
            await multiroom_equalizer_service.load_client_preset("nope", "bass_boost")

    @pytest.mark.asyncio
    async def test_load_client_preset_raises_for_zoned_client(
        self, multiroom_equalizer_service, mock_registry, sample_zone_client
    ):
        """A client that is in a zone still raises (must use the zone path)."""
        mock_registry.get_client_equalizer.return_value = None
        mock_registry.get_client.return_value = sample_zone_client

        with pytest.raises(ValueError, match="is in a zone"):
            await multiroom_equalizer_service.load_client_preset("milo-client-1", "bass_boost")

    @pytest.mark.asyncio
    async def test_save_custom_preset_creates_entry_for_fresh_standalone_client(
        self, multiroom_equalizer_service, mock_registry, fresh_standalone_client
    ):
        """Saving a custom preset on a fresh remote client must persist
        active_preset='custom' instead of raising."""
        mock_registry.get_client_equalizer.return_value = None
        mock_registry.get_client.return_value = fresh_standalone_client

        await multiroom_equalizer_service.save_custom_preset("client", "dc:a6:32:aa:bb:cc")

        mock_registry.set_client_equalizer.assert_called_once()
        persisted = mock_registry.set_client_equalizer.call_args.args[1]
        assert persisted.active_preset == "custom"

    @pytest.mark.asyncio
    async def test_save_custom_preset_raises_for_missing_zone(
        self, multiroom_equalizer_service, mock_registry
    ):
        """A missing zone still raises — zones are not auto-created on demand."""
        mock_registry.get_zone.return_value = None

        with pytest.raises(ValueError, match="not found"):
            await multiroom_equalizer_service.save_custom_preset("zone", "nonexistent")


# =============================================================================
# Local Active-Preset Name Sync Tests (zone-delete "wrong name / right gains" bug)
# =============================================================================

class TestLocalActivePresetSync:
    """Regression tests for the 'local client shows the wrong preset NAME after a
    zone is deleted' bug.

    The local client's gains live in the CamillaDSP filter cache (get_filters)
    while its preset NAME lives in a separate store, CamillaDSPService._active_preset
    (read by GET /api/equalizer/presets). The multiroom apply path pushed the gains
    but never the name, so after a zone was deleted the local client kept showing the
    previous preset's NAME against the zone's GAINS. _apply_to_local must keep the
    local name in sync with the gains it applies, without persisting to equalizer.json
    (multiroom uses the registry as the source of truth).
    """

    @pytest.mark.asyncio
    async def test_apply_to_local_syncs_active_preset_name(
        self, multiroom_equalizer_service, mock_camilladsp_service, sample_equalizer_settings
    ):
        """Applying settings to the local client syncs the active preset NAME onto
        the local CamillaDSP (persist=False — the registry is the source of truth)."""
        sample_equalizer_settings.active_preset = "vocal_boost"

        result = await multiroom_equalizer_service._apply_to_local(sample_equalizer_settings)

        assert result is True
        mock_camilladsp_service.set_active_preset.assert_called_once_with(
            "vocal_boost", persist=False
        )

    @pytest.mark.asyncio
    async def test_apply_zone_equalizer_syncs_local_preset_name(
        self, multiroom_equalizer_service, mock_registry, mock_camilladsp_service,
        sample_zone, sample_equalizer_settings
    ):
        """The bug's entry point: applying a zone preset to a zone containing the
        local client syncs the local CamillaDSP preset NAME, so it stays correct
        after the zone is later deleted (deletion leaves the local DSP untouched)."""
        sample_equalizer_settings.active_preset = "vocal_boost"
        mock_registry.get_zone.return_value = sample_zone
        local_online = Client(
            mac_id="local", name="Main", ip="127.0.0.1", online=True, zone_id="zone-123"
        )
        mock_registry.get_online_zone_clients.return_value = [local_online]
        mock_registry.get_client.return_value = local_online

        await multiroom_equalizer_service.apply_zone_equalizer("zone-123", sample_equalizer_settings)

        mock_camilladsp_service.set_active_preset.assert_called_once_with(
            "vocal_boost", persist=False
        )

    @pytest.mark.asyncio
    async def test_save_custom_preset_zone_with_local_member_syncs_local_name(
        self, multiroom_equalizer_service, mock_registry, mock_camilladsp_service, sample_zone
    ):
        """Saving a zone's gains as 'custom' must sync the local member's CamillaDSP
        preset NAME to 'custom' (gains are unchanged — only the name store needs it),
        so it shows 'custom' after a target switch or a later zone deletion."""
        sample_zone.equalizer_settings = EqualizerSettings.default()
        mock_registry.get_zone.return_value = sample_zone  # client_ids include "local"

        await multiroom_equalizer_service.save_custom_preset("zone", "zone-123")

        mock_camilladsp_service.set_active_preset.assert_called_once_with(
            "custom", persist=False
        )

    @pytest.mark.asyncio
    async def test_save_custom_preset_zone_without_local_member_skips_local(
        self, multiroom_equalizer_service, mock_registry, mock_camilladsp_service
    ):
        """A zone with no local member must NOT touch the local CamillaDSP name."""
        remote_only_zone = Zone(
            id="zone-r",
            name="Remote Pair",
            client_ids=["milo-client-1", "milo-client-2"],
            equalizer_settings=EqualizerSettings.default(),
        )
        mock_registry.get_zone.return_value = remote_only_zone

        await multiroom_equalizer_service.save_custom_preset("zone", "zone-r")

        mock_camilladsp_service.set_active_preset.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_custom_preset_remote_client_skips_local(
        self, multiroom_equalizer_service, mock_registry, mock_camilladsp_service
    ):
        """Saving a custom preset for a REMOTE standalone client must not touch the
        local CamillaDSP name (the remote name lives in the registry)."""
        remote_client = Client(
            mac_id="milo-client-1", name="Bedroom", ip="192.168.1.100",
            online=True, zone_id=None,
        )
        mock_registry.get_client_equalizer.return_value = None
        mock_registry.get_client.return_value = remote_client

        await multiroom_equalizer_service.save_custom_preset("client", "milo-client-1")

        mock_camilladsp_service.set_active_preset.assert_not_called()


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
        mock_registry.set_client_equalizer.assert_called_once()

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
        mock_registry.get_client_equalizer.return_value = sample_equalizer_settings

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
        mock_camilladsp_service.set_mono.assert_called_once()

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
        """apply_zone_equalizer no longer broadcasts directly — broadcasting
        is handled by the registry's set_zone_equalizer and partial update methods."""
        mock_registry.get_zone.return_value = sample_zone
        mock_registry.get_online_zone_clients.return_value = []

        await multiroom_equalizer_service.apply_zone_equalizer("zone-123", sample_equalizer_settings)

        mock_state_machine.broadcast_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_broadcast_client_equalizer_event(
        self, multiroom_equalizer_service, mock_registry, mock_state_machine,
        sample_client, sample_equalizer_settings
    ):
        """apply_client_equalizer no longer broadcasts directly — broadcasting
        is handled by the registry's set_client_equalizer and partial update methods."""
        mock_registry.get_client.return_value = sample_client

        await multiroom_equalizer_service.apply_client_equalizer("local", sample_equalizer_settings)

        mock_state_machine.broadcast_event.assert_not_called()

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
