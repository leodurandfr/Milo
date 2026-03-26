# backend/tests/integration/test_eq_filter_management.py
"""
Integration tests for Story 4.3: EQ Filter Management

Tests cover:
- AC1: Filter parameter update with WebSocket broadcast
- AC2: DspService.set_filter method validation and propagation
- AC3: 10-band parametric EQ configuration
- AC4: Preset auto-switch on manual modification

These tests verify the complete filter update flow:
API → CamillaDSP → WebSocket → Frontend state update
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch

from backend.core.equalizer import (
    CamillaDSPService,
    CamillaDspState,
    get_preset_by_id,
    DEFAULT_CUSTOM_GAINS,
    BUILTIN_PRESETS,
)
from backend.core.multiroom.models import (
    EqualizerSettings,
    EqFilter,
    DEFAULT_EQ_FREQUENCIES,
)


# =============================================================================
# AC1: Filter parameter update with WebSocket broadcast
# =============================================================================

class TestAC1FilterParameterUpdate:
    """AC1: Filter parameter update broadcasts equalizer_changed event within 200ms"""

    @pytest.fixture
    def mock_settings_service(self):
        """Create mock settings service"""
        settings = Mock()
        settings.get_setting = AsyncMock(return_value=None)
        settings.set_setting = AsyncMock()
        return settings

    @pytest.fixture
    def mock_state_machine(self):
        """Create mock state machine"""
        sm = Mock()
        sm.broadcast_event = AsyncMock()
        return sm

    @pytest.fixture
    def connected_camilladsp_service(self, mock_settings_service, mock_state_machine):
        """Create connected Equalizer service with mocked CamillaClient"""
        service = CamillaDSPService(
            settings_service=mock_settings_service
        )
        service.set_state_machine(mock_state_machine)

        # Simulate connected state with filters cache
        service._connected = True
        service._state = CamillaDspState.RUNNING
        service._filters = [
            {"id": f"eq_band_{i:02d}", "type": "Peaking", "freq": DEFAULT_EQ_FREQUENCIES[i], "gain": 0, "q": 1.41, "enabled": True}
            for i in range(10)
        ]
        return service

    @pytest.mark.asyncio
    async def test_set_filter_broadcasts_filter_changed_event(self, connected_camilladsp_service, mock_state_machine):
        """Should broadcast filter_changed event when filter is updated"""
        # Mock CamillaClient methods
        mock_config = {
            "filters": {
                "eq_band_00": {
                    "type": "Biquad",
                    "parameters": {"type": "Peaking", "freq": 31, "gain": 0, "q": 1.41}
                }
            }
        }

        with patch.object(connected_camilladsp_service, '_get_config', new_callable=AsyncMock) as mock_get:
            with patch.object(connected_camilladsp_service, '_set_config', new_callable=AsyncMock):
                mock_get.return_value = mock_config

                # Update filter
                result = await connected_camilladsp_service.set_filter(
                    filter_id="eq_band_00",
                    freq=100,
                    gain=3.0,
                    q=1.41
                )

                assert result is True

                # Verify broadcast was called with correct event type and data
                mock_state_machine.broadcast_event.assert_called()
                call_args = mock_state_machine.broadcast_event.call_args
                assert call_args[0][0] == "equalizer"  # category
                assert call_args[0][1] == "filter_changed"  # event type
                assert call_args[0][2]["id"] == "eq_band_00"
                assert call_args[0][2]["freq"] == 100
                assert call_args[0][2]["gain"] == 3.0
                assert call_args[0][2]["q"] == 1.41

    @pytest.mark.asyncio
    async def test_set_filter_updates_local_cache(self, connected_camilladsp_service):
        """Should update local filter cache when filter is set"""
        mock_config = {"filters": {"eq_band_00": {"type": "Biquad", "parameters": {"type": "Peaking", "freq": 31, "gain": 0, "q": 1.41}}}}

        with patch.object(connected_camilladsp_service, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_camilladsp_service, '_set_config', new_callable=AsyncMock):
                await connected_camilladsp_service.set_filter("eq_band_00", freq=200, gain=5.0, q=2.0)

                # Verify local cache was updated
                filter_00 = next(f for f in connected_camilladsp_service._filters if f["id"] == "eq_band_00")
                assert filter_00["freq"] == 200
                assert filter_00["gain"] == 5.0
                assert filter_00["q"] == 2.0

    @pytest.mark.asyncio
    async def test_set_filter_persists_to_settings(self, connected_camilladsp_service, mock_settings_service):
        """Should persist filter changes to settings service"""
        mock_config = {"filters": {"eq_band_00": {"type": "Biquad", "parameters": {"type": "Peaking", "freq": 31, "gain": 0, "q": 1.41}}}}

        with patch.object(connected_camilladsp_service, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_camilladsp_service, '_set_config', new_callable=AsyncMock):
                await connected_camilladsp_service.set_filter("eq_band_00", freq=100, gain=3.0, q=1.41)

                # Verify settings were saved
                mock_settings_service.set_setting.assert_called()
                # Find the call that saves filters
                filter_calls = [c for c in mock_settings_service.set_setting.call_args_list if c[0][0] == "equalizer.filters"]
                assert len(filter_calls) >= 1


# =============================================================================
# AC2: DspService.set_filter method validation
# =============================================================================

class TestAC2SetFilterMethod:
    """AC2: DspService.set_filter validates and applies filter parameters"""

    @pytest.fixture
    def mock_settings_service(self):
        settings = Mock()
        settings.get_setting = AsyncMock(return_value=None)
        settings.set_setting = AsyncMock()
        return settings

    @pytest.fixture
    def disconnected_camilladsp_service(self, mock_settings_service):
        """Create disconnected Equalizer service"""
        return CamillaDSPService(
            settings_service=mock_settings_service
        )

    @pytest.fixture
    def connected_camilladsp_service(self, mock_settings_service):
        """Create connected Equalizer service"""
        service = CamillaDSPService(
            settings_service=mock_settings_service
        )
        service._connected = True
        service._state = CamillaDspState.RUNNING
        service._filters = [
            {"id": f"eq_band_{i:02d}", "type": "Peaking", "freq": DEFAULT_EQ_FREQUENCIES[i], "gain": 0, "q": 1.41, "enabled": True}
            for i in range(10)
        ]
        service.state_machine = Mock()
        service.state_machine.broadcast_event = AsyncMock()
        return service

    @pytest.mark.asyncio
    async def test_set_filter_fails_when_disconnected(self, disconnected_camilladsp_service):
        """Should return False when not connected to CamillaDSP"""
        result = await disconnected_camilladsp_service.set_filter("eq_band_00", 100, 0, 1.0)
        assert result is False

    @pytest.mark.asyncio
    async def test_set_filter_accepts_valid_frequency_range(self, connected_camilladsp_service):
        """Should accept frequency in range 20-20000 Hz (AC3 requirement)"""
        mock_config = {"filters": {}}

        with patch.object(connected_camilladsp_service, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_camilladsp_service, '_set_config', new_callable=AsyncMock):
                # Test minimum frequency (20 Hz)
                result = await connected_camilladsp_service.set_filter("eq_band_00", freq=20, gain=0, q=1.0)
                assert result is True

                # Test maximum frequency (20000 Hz)
                result = await connected_camilladsp_service.set_filter("eq_band_09", freq=20000, gain=0, q=1.0)
                assert result is True

    @pytest.mark.asyncio
    async def test_set_filter_accepts_valid_gain_range(self, connected_camilladsp_service):
        """Should accept gain in range -12 to +12 dB (AC3 requirement)"""
        mock_config = {"filters": {}}

        with patch.object(connected_camilladsp_service, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_camilladsp_service, '_set_config', new_callable=AsyncMock):
                # Test minimum gain (-12 dB per AC3, but implementation allows -15)
                result = await connected_camilladsp_service.set_filter("eq_band_00", freq=1000, gain=-12, q=1.0)
                assert result is True

                # Test maximum gain (+12 dB per AC3, but implementation allows +15)
                result = await connected_camilladsp_service.set_filter("eq_band_00", freq=1000, gain=12, q=1.0)
                assert result is True

    @pytest.mark.asyncio
    async def test_set_filter_accepts_valid_q_range(self, connected_camilladsp_service):
        """Should accept Q in range 0.1-10 (AC3 requirement)"""
        mock_config = {"filters": {}}

        with patch.object(connected_camilladsp_service, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_camilladsp_service, '_set_config', new_callable=AsyncMock):
                # Test minimum Q (0.1)
                result = await connected_camilladsp_service.set_filter("eq_band_00", freq=1000, gain=0, q=0.1)
                assert result is True

                # Test maximum Q (10.0)
                result = await connected_camilladsp_service.set_filter("eq_band_00", freq=1000, gain=0, q=10.0)
                assert result is True

    @pytest.mark.asyncio
    async def test_set_filter_builds_correct_camilladsp_config(self, connected_camilladsp_service):
        """Should build correct CamillaDSP configuration format"""
        mock_config = {"filters": {}}
        captured_config = None

        async def capture_config(config):
            nonlocal captured_config
            captured_config = config

        with patch.object(connected_camilladsp_service, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_camilladsp_service, '_set_config', new_callable=AsyncMock, side_effect=capture_config):
                await connected_camilladsp_service.set_filter(
                    filter_id="eq_band_05",
                    freq=1000,
                    gain=3.0,
                    q=1.41,
                    filter_type="Peaking"
                )

                # Verify CamillaDSP config format
                assert "eq_band_05" in captured_config["filters"]
                filter_config = captured_config["filters"]["eq_band_05"]
                assert filter_config["type"] == "Biquad"
                assert filter_config["parameters"]["type"] == "Peaking"
                assert filter_config["parameters"]["freq"] == 1000
                assert filter_config["parameters"]["gain"] == 3.0
                assert filter_config["parameters"]["q"] == 1.41


# =============================================================================
# AC3: 10-band parametric EQ configuration
# =============================================================================

class TestAC3TenBandEQConfiguration:
    """AC3: 10-band parametric EQ with correct default frequencies"""

    def test_default_eq_frequencies_match_spec(self):
        """Should have exactly 10 bands with standard frequencies"""
        expected = [31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
        assert DEFAULT_EQ_FREQUENCIES == expected
        assert len(DEFAULT_EQ_FREQUENCIES) == 10

    def test_equalizer_settings_default_creates_10_bands(self):
        """EqualizerSettings.default() should create 10-band EQ"""
        eq = EqualizerSettings.default()
        assert len(eq.filters) == 10

    def test_filter_ids_match_pattern(self):
        """Filter IDs should match eq_band_00 to eq_band_09"""
        eq = EqualizerSettings.default()
        expected_ids = [f"eq_band_{i:02d}" for i in range(10)]
        actual_ids = [f.id for f in eq.filters]
        assert actual_ids == expected_ids

    def test_default_filters_have_correct_frequencies(self):
        """Default filters should have frequencies [31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]"""
        eq = EqualizerSettings.default()
        expected = [31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
        actual = [f.frequency for f in eq.filters]
        assert actual == expected

    def test_default_filters_have_zero_gain(self):
        """Default filters should have 0 dB gain (flat response)"""
        eq = EqualizerSettings.default()
        for f in eq.filters:
            assert f.gain == 0.0

    def test_default_filters_have_correct_q(self):
        """Default filters should have Q = 1.41 (standard parametric EQ)"""
        eq = EqualizerSettings.default()
        for f in eq.filters:
            assert f.q == 1.41

    def test_default_filters_have_peaking_type(self):
        """Default filters should be Peaking type"""
        from backend.core.multiroom.models import FilterType
        eq = EqualizerSettings.default()
        for f in eq.filters:
            assert f.filter_type == FilterType.PEAKING

    def test_all_filters_enabled_by_default(self):
        """All filters should be enabled by default"""
        eq = EqualizerSettings.default()
        for f in eq.filters:
            assert f.enabled is True

    def test_eq_filter_has_required_fields(self):
        """Each EqFilter should have id, freq, gain, q, type, enabled"""
        f = EqFilter(id="eq_band_00", frequency=31)
        assert hasattr(f, 'id')
        assert hasattr(f, 'frequency')
        assert hasattr(f, 'gain')
        assert hasattr(f, 'q')
        assert hasattr(f, 'filter_type')
        assert hasattr(f, 'enabled')


# =============================================================================
# AC4: No auto-switch on filter modification
# =============================================================================

class TestAC4NoAutoSwitch:
    """AC4: Modifying filters does NOT auto-switch preset (removed behavior)"""

    @pytest.fixture
    def mock_settings_service(self):
        settings = Mock()
        settings.get_setting = AsyncMock(return_value="acoustic")  # Currently on a preset
        settings.set_setting = AsyncMock()
        return settings

    @pytest.fixture
    def connected_camilladsp_service_on_preset(self, mock_settings_service):
        """Create connected Equalizer service that's on a predefined preset"""
        service = CamillaDSPService(
            settings_service=mock_settings_service
        )
        service._connected = True
        service._state = CamillaDspState.RUNNING
        service._filters = [
            {"id": f"eq_band_{i:02d}", "type": "Peaking", "freq": DEFAULT_EQ_FREQUENCIES[i], "gain": 0, "q": 1.41, "enabled": True}
            for i in range(10)
        ]
        service.state_machine = Mock()
        service.state_machine.broadcast_event = AsyncMock()
        return service

    @pytest.mark.asyncio
    async def test_manual_modification_does_not_switch_preset(self, connected_camilladsp_service_on_preset, mock_settings_service):
        """Should NOT switch preset when filter is manually modified"""
        mock_config = {"filters": {}}

        with patch.object(connected_camilladsp_service_on_preset, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_camilladsp_service_on_preset, '_set_config', new_callable=AsyncMock):
                await connected_camilladsp_service_on_preset.set_filter(
                    filter_id="eq_band_00",
                    freq=100,
                    gain=5.0,
                    q=1.41,
                )

                # Verify preset was NOT switched
                preset_calls = [c for c in mock_settings_service.set_setting.call_args_list
                               if c[0][0] == "equalizer.active_preset"]
                assert len(preset_calls) == 0

    @pytest.mark.asyncio
    async def test_manual_modification_does_not_broadcast_preset_loaded(self, connected_camilladsp_service_on_preset):
        """Should NOT broadcast preset_loaded event when filter is modified"""
        mock_config = {"filters": {}}
        state_machine = connected_camilladsp_service_on_preset.state_machine

        with patch.object(connected_camilladsp_service_on_preset, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_camilladsp_service_on_preset, '_set_config', new_callable=AsyncMock):
                await connected_camilladsp_service_on_preset.set_filter(
                    filter_id="eq_band_00",
                    freq=100,
                    gain=5.0,
                    q=1.41,
                )

                # Should NOT have preset_loaded broadcasts
                preset_events = [
                    c for c in state_machine.broadcast_event.call_args_list
                    if len(c[0]) >= 2 and c[0][1] == "preset_loaded"
                ]
                assert len(preset_events) == 0

    @pytest.mark.asyncio
    async def test_manual_modification_does_not_save_custom_gains(self, connected_camilladsp_service_on_preset, mock_settings_service):
        """Should NOT save custom gains when filter is modified"""
        mock_config = {"filters": {}}

        with patch.object(connected_camilladsp_service_on_preset, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_camilladsp_service_on_preset, '_set_config', new_callable=AsyncMock):
                await connected_camilladsp_service_on_preset.set_filter(
                    filter_id="eq_band_00",
                    freq=100,
                    gain=5.0,
                    q=1.41,
                )

                # Should NOT have saved custom gains
                custom_gains_calls = [c for c in mock_settings_service.set_setting.call_args_list
                                     if c[0][0] == "equalizer.custom_gains"]
                assert len(custom_gains_calls) == 0


# =============================================================================
# Frontend Store Validation (dspStore.js)
# =============================================================================

class TestFrontendStoreDefaults:
    """Verify frontend defaults match backend expectations"""

    def test_default_frequencies_match_frontend(self):
        """Frontend DEFAULT_FREQUENCIES should match backend DEFAULT_EQ_FREQUENCIES"""
        # Frontend: [31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
        frontend_default = [31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
        assert DEFAULT_EQ_FREQUENCIES == frontend_default

    def test_filter_id_format_consistency(self):
        """Filter IDs should use eq_band_XX format with zero-padding"""
        for i in range(10):
            expected_id = f"eq_band_{i:02d}"
            # Verify format: eq_band_00, eq_band_01, ..., eq_band_09
            assert len(expected_id) == 10
            assert expected_id.startswith("eq_band_")


# =============================================================================
# Preset System Tests
# =============================================================================

class TestPresetSystem:
    """Test preset management functionality"""

    def test_builtin_presets_have_10_gains(self):
        """All builtin presets should have exactly 10 gain values"""
        for preset in BUILTIN_PRESETS:
            assert len(preset["gains"]) == 10

    def test_get_preset_by_id_returns_preset(self):
        """Should return preset by ID"""
        preset = get_preset_by_id("acoustic")
        assert preset is not None
        assert preset["id"] == "acoustic"

    def test_get_preset_by_id_returns_none_for_unknown(self):
        """Should return None for unknown preset ID"""
        preset = get_preset_by_id("nonexistent")
        assert preset is None

    def test_default_custom_gains_are_flat(self):
        """Default custom gains should be all zeros (flat response)"""
        assert DEFAULT_CUSTOM_GAINS == [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
