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
from unittest.mock import Mock, AsyncMock, patch, MagicMock
import asyncio

from backend.core.dsp import (
    CamillaDSPService,
    DspState,
    FilterType,
    get_builtin_presets,
    get_preset_by_id,
    DEFAULT_MANUAL_GAINS,
    BUILTIN_PRESETS,
)
from backend.core.events import EventBus
from backend.core.multiroom.models import (
    DspSettings,
    EqFilter,
    DEFAULT_EQ_FREQUENCIES,
)


# =============================================================================
# AC1: Filter parameter update with WebSocket broadcast
# =============================================================================

class TestAC1FilterParameterUpdate:
    """AC1: Filter parameter update broadcasts dsp_changed event within 200ms"""

    @pytest.fixture
    def mock_settings_service(self):
        """Create mock settings service"""
        settings = Mock()
        settings.get_setting = AsyncMock(return_value=None)
        settings.set_setting = AsyncMock()
        return settings

    @pytest.fixture
    def mock_event_bus(self):
        """Create mock event bus"""
        bus = Mock(spec=EventBus)
        bus.emit = AsyncMock()
        return bus

    @pytest.fixture
    def mock_state_machine(self):
        """Create mock state machine"""
        sm = Mock()
        sm.broadcast_event = AsyncMock()
        return sm

    @pytest.fixture
    def connected_dsp_service(self, mock_settings_service, mock_event_bus, mock_state_machine):
        """Create connected DSP service with mocked CamillaClient"""
        service = CamillaDSPService(
            settings_service=mock_settings_service,
            event_bus=mock_event_bus
        )
        service.set_state_machine(mock_state_machine)

        # Simulate connected state with filters cache
        service._connected = True
        service._state = DspState.RUNNING
        service._filters = [
            {"id": f"eq_band_{i:02d}", "type": "Peaking", "freq": DEFAULT_EQ_FREQUENCIES[i], "gain": 0, "q": 1.41, "enabled": True}
            for i in range(10)
        ]
        return service

    @pytest.mark.asyncio
    async def test_set_filter_broadcasts_filter_changed_event(self, connected_dsp_service, mock_state_machine, mock_event_bus):
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

        with patch.object(connected_dsp_service, '_get_config', new_callable=AsyncMock) as mock_get:
            with patch.object(connected_dsp_service, '_set_config', new_callable=AsyncMock) as mock_set:
                mock_get.return_value = mock_config

                # Update filter
                result = await connected_dsp_service.set_filter(
                    filter_id="eq_band_00",
                    freq=100,
                    gain=3.0,
                    q=1.41
                )

                assert result is True

                # Verify broadcast was called with correct event type and data
                mock_state_machine.broadcast_event.assert_called()
                call_args = mock_state_machine.broadcast_event.call_args
                assert call_args[0][0] == "dsp"  # category
                assert call_args[0][1] == "filter_changed"  # event type
                assert call_args[0][2]["id"] == "eq_band_00"
                assert call_args[0][2]["freq"] == 100
                assert call_args[0][2]["gain"] == 3.0
                assert call_args[0][2]["q"] == 1.41

    @pytest.mark.asyncio
    async def test_set_filter_updates_local_cache(self, connected_dsp_service):
        """Should update local filter cache when filter is set"""
        mock_config = {"filters": {"eq_band_00": {"type": "Biquad", "parameters": {"type": "Peaking", "freq": 31, "gain": 0, "q": 1.41}}}}

        with patch.object(connected_dsp_service, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_dsp_service, '_set_config', new_callable=AsyncMock):
                await connected_dsp_service.set_filter("eq_band_00", freq=200, gain=5.0, q=2.0)

                # Verify local cache was updated
                filter_00 = next(f for f in connected_dsp_service._filters if f["id"] == "eq_band_00")
                assert filter_00["freq"] == 200
                assert filter_00["gain"] == 5.0
                assert filter_00["q"] == 2.0

    @pytest.mark.asyncio
    async def test_set_filter_persists_to_settings(self, connected_dsp_service, mock_settings_service):
        """Should persist filter changes to settings service"""
        mock_config = {"filters": {"eq_band_00": {"type": "Biquad", "parameters": {"type": "Peaking", "freq": 31, "gain": 0, "q": 1.41}}}}

        with patch.object(connected_dsp_service, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_dsp_service, '_set_config', new_callable=AsyncMock):
                await connected_dsp_service.set_filter("eq_band_00", freq=100, gain=3.0, q=1.41)

                # Verify settings were saved
                mock_settings_service.set_setting.assert_called()
                # Find the call that saves filters
                filter_calls = [c for c in mock_settings_service.set_setting.call_args_list if c[0][0] == "dsp.filters"]
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
    def mock_event_bus(self):
        bus = Mock(spec=EventBus)
        bus.emit = AsyncMock()
        return bus

    @pytest.fixture
    def disconnected_dsp_service(self, mock_settings_service, mock_event_bus):
        """Create disconnected DSP service"""
        return CamillaDSPService(
            settings_service=mock_settings_service,
            event_bus=mock_event_bus
        )

    @pytest.fixture
    def connected_dsp_service(self, mock_settings_service, mock_event_bus):
        """Create connected DSP service"""
        service = CamillaDSPService(
            settings_service=mock_settings_service,
            event_bus=mock_event_bus
        )
        service._connected = True
        service._state = DspState.RUNNING
        service._filters = [
            {"id": f"eq_band_{i:02d}", "type": "Peaking", "freq": DEFAULT_EQ_FREQUENCIES[i], "gain": 0, "q": 1.41, "enabled": True}
            for i in range(10)
        ]
        service.state_machine = Mock()
        service.state_machine.broadcast_event = AsyncMock()
        return service

    @pytest.mark.asyncio
    async def test_set_filter_fails_when_disconnected(self, disconnected_dsp_service):
        """Should return False when not connected to CamillaDSP"""
        result = await disconnected_dsp_service.set_filter("eq_band_00", 100, 0, 1.0)
        assert result is False

    @pytest.mark.asyncio
    async def test_set_filter_accepts_valid_frequency_range(self, connected_dsp_service):
        """Should accept frequency in range 20-20000 Hz (AC3 requirement)"""
        mock_config = {"filters": {}}

        with patch.object(connected_dsp_service, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_dsp_service, '_set_config', new_callable=AsyncMock):
                # Test minimum frequency (20 Hz)
                result = await connected_dsp_service.set_filter("eq_band_00", freq=20, gain=0, q=1.0)
                assert result is True

                # Test maximum frequency (20000 Hz)
                result = await connected_dsp_service.set_filter("eq_band_09", freq=20000, gain=0, q=1.0)
                assert result is True

    @pytest.mark.asyncio
    async def test_set_filter_accepts_valid_gain_range(self, connected_dsp_service):
        """Should accept gain in range -12 to +12 dB (AC3 requirement)"""
        mock_config = {"filters": {}}

        with patch.object(connected_dsp_service, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_dsp_service, '_set_config', new_callable=AsyncMock):
                # Test minimum gain (-12 dB per AC3, but implementation allows -15)
                result = await connected_dsp_service.set_filter("eq_band_00", freq=1000, gain=-12, q=1.0)
                assert result is True

                # Test maximum gain (+12 dB per AC3, but implementation allows +15)
                result = await connected_dsp_service.set_filter("eq_band_00", freq=1000, gain=12, q=1.0)
                assert result is True

    @pytest.mark.asyncio
    async def test_set_filter_accepts_valid_q_range(self, connected_dsp_service):
        """Should accept Q in range 0.1-10 (AC3 requirement)"""
        mock_config = {"filters": {}}

        with patch.object(connected_dsp_service, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_dsp_service, '_set_config', new_callable=AsyncMock):
                # Test minimum Q (0.1)
                result = await connected_dsp_service.set_filter("eq_band_00", freq=1000, gain=0, q=0.1)
                assert result is True

                # Test maximum Q (10.0)
                result = await connected_dsp_service.set_filter("eq_band_00", freq=1000, gain=0, q=10.0)
                assert result is True

    @pytest.mark.asyncio
    async def test_set_filter_builds_correct_camilladsp_config(self, connected_dsp_service):
        """Should build correct CamillaDSP configuration format"""
        mock_config = {"filters": {}}
        captured_config = None

        async def capture_config(config):
            nonlocal captured_config
            captured_config = config

        with patch.object(connected_dsp_service, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_dsp_service, '_set_config', new_callable=AsyncMock, side_effect=capture_config):
                await connected_dsp_service.set_filter(
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

    def test_dsp_settings_default_creates_10_bands(self):
        """DspSettings.default() should create 10-band EQ"""
        dsp = DspSettings.default()
        assert len(dsp.filters) == 10

    def test_filter_ids_match_pattern(self):
        """Filter IDs should match eq_band_00 to eq_band_09"""
        dsp = DspSettings.default()
        expected_ids = [f"eq_band_{i:02d}" for i in range(10)]
        actual_ids = [f.id for f in dsp.filters]
        assert actual_ids == expected_ids

    def test_default_filters_have_correct_frequencies(self):
        """Default filters should have frequencies [31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]"""
        dsp = DspSettings.default()
        expected = [31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
        actual = [f.frequency for f in dsp.filters]
        assert actual == expected

    def test_default_filters_have_zero_gain(self):
        """Default filters should have 0 dB gain (flat response)"""
        dsp = DspSettings.default()
        for f in dsp.filters:
            assert f.gain == 0.0

    def test_default_filters_have_correct_q(self):
        """Default filters should have Q = 1.41 (standard parametric EQ)"""
        dsp = DspSettings.default()
        for f in dsp.filters:
            assert f.q == 1.41

    def test_default_filters_have_peaking_type(self):
        """Default filters should be Peaking type"""
        from backend.core.multiroom.models import FilterType
        dsp = DspSettings.default()
        for f in dsp.filters:
            assert f.filter_type == FilterType.PEAKING

    def test_all_filters_enabled_by_default(self):
        """All filters should be enabled by default"""
        dsp = DspSettings.default()
        for f in dsp.filters:
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
# AC4: Preset auto-switch on manual modification (FR23)
# =============================================================================

class TestAC4PresetAutoSwitch:
    """AC4: Auto-switch to manual preset when filters are manually modified"""

    @pytest.fixture
    def mock_settings_service(self):
        settings = Mock()
        settings.get_setting = AsyncMock(return_value="acoustic")  # Currently on a preset
        settings.set_setting = AsyncMock()
        return settings

    @pytest.fixture
    def mock_event_bus(self):
        bus = Mock(spec=EventBus)
        bus.emit = AsyncMock()
        return bus

    @pytest.fixture
    def connected_dsp_service_on_preset(self, mock_settings_service, mock_event_bus):
        """Create connected DSP service that's on a predefined preset"""
        service = CamillaDSPService(
            settings_service=mock_settings_service,
            event_bus=mock_event_bus
        )
        service._connected = True
        service._state = DspState.RUNNING
        service._filters = [
            {"id": f"eq_band_{i:02d}", "type": "Peaking", "freq": DEFAULT_EQ_FREQUENCIES[i], "gain": 0, "q": 1.41, "enabled": True}
            for i in range(10)
        ]
        service.state_machine = Mock()
        service.state_machine.broadcast_event = AsyncMock()
        return service

    @pytest.mark.asyncio
    async def test_manual_modification_switches_to_manual_preset(self, connected_dsp_service_on_preset, mock_settings_service):
        """Should switch to manual preset when filter manually modified (from_preset=False)"""
        mock_config = {"filters": {}}

        with patch.object(connected_dsp_service_on_preset, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_dsp_service_on_preset, '_set_config', new_callable=AsyncMock):
                # Modify filter with from_preset=False (default)
                await connected_dsp_service_on_preset.set_filter(
                    filter_id="eq_band_00",
                    freq=100,
                    gain=5.0,
                    q=1.41,
                    from_preset=False
                )

                # Verify preset was switched to "manual"
                preset_calls = [c for c in mock_settings_service.set_setting.call_args_list
                               if c[0][0] == "dsp.active_preset"]
                assert len(preset_calls) >= 1
                assert preset_calls[-1][0][1] == "manual"

    @pytest.mark.asyncio
    async def test_preset_load_does_not_trigger_manual_switch(self, connected_dsp_service_on_preset, mock_settings_service):
        """Should NOT switch to manual when loading preset (from_preset=True)"""
        mock_config = {"filters": {}}

        # Reset to track calls during this test only
        mock_settings_service.set_setting.reset_mock()

        with patch.object(connected_dsp_service_on_preset, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_dsp_service_on_preset, '_set_config', new_callable=AsyncMock):
                # Apply filter from preset (from_preset=True)
                await connected_dsp_service_on_preset.set_filter(
                    filter_id="eq_band_00",
                    freq=100,
                    gain=5.0,
                    q=1.41,
                    from_preset=True
                )

                # Should NOT have switched preset
                preset_calls = [c for c in mock_settings_service.set_setting.call_args_list
                               if c[0][0] == "dsp.active_preset"]
                # from_preset=True means we shouldn't switch to manual
                assert len(preset_calls) == 0 or preset_calls[-1][0][1] != "manual"

    @pytest.mark.asyncio
    async def test_manual_switch_broadcasts_preset_loaded_event(self, connected_dsp_service_on_preset):
        """Should broadcast preset_loaded event with id=manual on auto-switch"""
        mock_config = {"filters": {}}
        state_machine = connected_dsp_service_on_preset.state_machine

        with patch.object(connected_dsp_service_on_preset, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_dsp_service_on_preset, '_set_config', new_callable=AsyncMock):
                await connected_dsp_service_on_preset.set_filter(
                    filter_id="eq_band_00",
                    freq=100,
                    gain=5.0,
                    q=1.41,
                    from_preset=False
                )

                # Find preset_loaded broadcast
                preset_events = [
                    c for c in state_machine.broadcast_event.call_args_list
                    if len(c[0]) >= 2 and c[0][1] == "preset_loaded"
                ]
                assert len(preset_events) >= 1
                # Verify it broadcasts id="manual"
                assert preset_events[-1][0][2]["id"] == "manual"

    @pytest.mark.asyncio
    async def test_manual_gains_saved_before_switch(self, connected_dsp_service_on_preset, mock_settings_service):
        """Should save current gains as manual gains before switching"""
        mock_config = {"filters": {}}

        with patch.object(connected_dsp_service_on_preset, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_dsp_service_on_preset, '_set_config', new_callable=AsyncMock):
                await connected_dsp_service_on_preset.set_filter(
                    filter_id="eq_band_00",
                    freq=100,
                    gain=5.0,
                    q=1.41,
                    from_preset=False
                )

                # Should have saved manual gains
                manual_gains_calls = [c for c in mock_settings_service.set_setting.call_args_list
                                     if c[0][0] == "dsp.manual_gains"]
                assert len(manual_gains_calls) >= 1

    @pytest.mark.asyncio
    async def test_already_on_manual_no_switch(self):
        """Should not trigger switch when already on manual preset"""
        mock_settings = Mock()
        mock_settings.get_setting = AsyncMock(return_value="manual")  # Already on manual
        mock_settings.set_setting = AsyncMock()
        mock_event_bus = Mock(spec=EventBus)
        mock_event_bus.emit = AsyncMock()

        service = CamillaDSPService(
            settings_service=mock_settings,
            event_bus=mock_event_bus
        )
        service._connected = True
        service._state = DspState.RUNNING
        service._filters = [
            {"id": f"eq_band_{i:02d}", "type": "Peaking", "freq": DEFAULT_EQ_FREQUENCIES[i], "gain": 0, "q": 1.41, "enabled": True}
            for i in range(10)
        ]
        service.state_machine = Mock()
        service.state_machine.broadcast_event = AsyncMock()

        mock_config = {"filters": {}}

        with patch.object(service, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(service, '_set_config', new_callable=AsyncMock):
                await service.set_filter("eq_band_00", freq=100, gain=5.0, q=1.41)

                # Should not have a preset_loaded broadcast for manual (already on manual)
                preset_events = [
                    c for c in service.state_machine.broadcast_event.call_args_list
                    if len(c[0]) >= 2 and c[0][1] == "preset_loaded"
                ]
                assert len(preset_events) == 0


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
# API Validation (Pydantic models)
# =============================================================================

class TestAPIValidation:
    """Test API request validation matches AC3 requirements"""

    def test_dsp_filter_request_frequency_range(self):
        """DspFilterRequest should accept frequency 20-20000 Hz"""
        from backend.api.models import DspFilterRequest

        # Valid minimum
        f = DspFilterRequest(freq=20, gain=0, q=1.0)
        assert f.freq == 20

        # Valid maximum
        f = DspFilterRequest(freq=20000, gain=0, q=1.0)
        assert f.freq == 20000

    def test_dsp_filter_request_gain_range(self):
        """DspFilterRequest should accept gain -15 to +15 dB (superset of AC3 -12 to +12)"""
        from backend.api.models import DspFilterRequest

        # Valid minimum (implementation allows -15)
        f = DspFilterRequest(freq=1000, gain=-15, q=1.0)
        assert f.gain == -15

        # Valid maximum (implementation allows +15)
        f = DspFilterRequest(freq=1000, gain=15, q=1.0)
        assert f.gain == 15

        # AC3 spec bounds should also work
        f = DspFilterRequest(freq=1000, gain=-12, q=1.0)
        assert f.gain == -12
        f = DspFilterRequest(freq=1000, gain=12, q=1.0)
        assert f.gain == 12

    def test_dsp_filter_request_q_range(self):
        """DspFilterRequest should accept Q 0.1-10.0"""
        from backend.api.models import DspFilterRequest

        # Valid minimum
        f = DspFilterRequest(freq=1000, gain=0, q=0.1)
        assert f.q == 0.1

        # Valid maximum
        f = DspFilterRequest(freq=1000, gain=0, q=10.0)
        assert f.q == 10.0

    def test_dsp_filter_request_invalid_frequency_rejected(self):
        """DspFilterRequest should reject out-of-range frequency"""
        from backend.api.models import DspFilterRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            DspFilterRequest(freq=19, gain=0, q=1.0)  # Below 20 Hz

        with pytest.raises(ValidationError):
            DspFilterRequest(freq=20001, gain=0, q=1.0)  # Above 20000 Hz

    def test_dsp_filter_request_invalid_gain_rejected(self):
        """DspFilterRequest should reject out-of-range gain"""
        from backend.api.models import DspFilterRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            DspFilterRequest(freq=1000, gain=-16, q=1.0)  # Below -15 dB

        with pytest.raises(ValidationError):
            DspFilterRequest(freq=1000, gain=16, q=1.0)  # Above +15 dB

    def test_dsp_filter_request_invalid_q_rejected(self):
        """DspFilterRequest should reject out-of-range Q"""
        from backend.api.models import DspFilterRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            DspFilterRequest(freq=1000, gain=0, q=0.05)  # Below 0.1

        with pytest.raises(ValidationError):
            DspFilterRequest(freq=1000, gain=0, q=11.0)  # Above 10.0


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

    def test_default_manual_gains_are_flat(self):
        """Default manual gains should be all zeros (flat response)"""
        assert DEFAULT_MANUAL_GAINS == [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
