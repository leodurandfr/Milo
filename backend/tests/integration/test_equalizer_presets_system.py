# backend/tests/integration/test_equalizer_presets_system.py
"""
Integration Tests for Equalizer Presets System

Tests cover:
- Apply preset to zone/client with gains and WebSocket event
- No auto-switch when modifying filter parameter (edited state is frontend-only)
- Zone propagation to all ONLINE zone members
- Available presets list (21 builtin + Custom)
- Custom preset persistence and restoration
- Startup restoration of saved preset

These tests verify the complete preset flow:
API → CamillaDSPService → WebSocket → Frontend state update

CamillaDSP itself is mocked at the boundary the service actually talks to:
`mock_camilla_client` (backend/tests/conftest.py) is injected as `service._client`,
so `_get_config` / `_set_config` run for real and `camilla_daemon` is what the
daemon holds — seed it with `load()`, read the write back with `last_pushed`.
"""
import pytest
from unittest.mock import Mock, AsyncMock

from backend.core.equalizer import CamillaDSPService, CamillaDspState
from backend.core.equalizer.presets import (
    BUILTIN_PRESETS,
    DEFAULT_EQ_FREQS,
    get_builtin_presets,
    get_preset_by_id
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_settings_service():
    """Create mock settings service"""
    settings = Mock()
    settings.get_setting = AsyncMock(return_value=None)
    settings.set_setting = AsyncMock()
    return settings


@pytest.fixture
def mock_state_machine():
    """Create mock state machine"""
    sm = Mock()
    sm.broadcast = AsyncMock()
    return sm


@pytest.fixture
def connected_camilladsp_service(mock_settings_service, mock_state_machine, mock_camilla_client):
    """Create connected Equalizer service"""
    service = CamillaDSPService(
        settings_service=mock_settings_service
    )
    service.set_state_machine(mock_state_machine)
    service._client = mock_camilla_client
    service._connected = True
    service._state = CamillaDspState.RUNNING

    service._filters = [
        {"id": f"eq_band_{i:02d}", "freq": freq, "gain": 0, "q": 1.41, "type": "Peaking", "enabled": True}
        for i, freq in enumerate(DEFAULT_EQ_FREQS)
    ]

    return service


@pytest.fixture
def camilladsp_service_with_jazz_preset(connected_camilladsp_service):
    """Equalizer service with jazz preset active"""
    # Simulate jazz preset loaded
    jazz_gains = [4, 3, 2, 2, -2, -2, 0, 2, 3, 4]
    for i, gain in enumerate(jazz_gains):
        connected_camilladsp_service._filters[i]["gain"] = gain

    connected_camilladsp_service._active_preset = "jazz"
    connected_camilladsp_service._custom_gains = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]

    return connected_camilladsp_service


# =============================================================================
# No auto-switch when modifying filter (edited state is frontend-only)
# =============================================================================

class TestNoAutoSwitchOnPresetEdit:
    """Modifying filter while on preset does NOT auto-switch to custom"""

    @pytest.mark.asyncio
    async def test_manual_modification_does_not_switch_preset(self, camilladsp_service_with_jazz_preset, mock_settings_service, camilla_daemon):
        """Should NOT switch preset when modifying filter while on builtin preset"""
        mock_settings_service.get_setting = AsyncMock(side_effect=lambda key: {
            "equalizer.active_preset": "jazz",
            "equalizer.custom_gains": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
        }.get(key))

        camilla_daemon.load({"filters": {}, "pipeline": []})

        await camilladsp_service_with_jazz_preset.set_filter(
            filter_id="eq_band_00",
            freq=32,
            gain=6.0,
            q=1.41,
            filter_type="Peaking"
        )

        # Should NOT switch active_preset
        preset_calls = [c for c in mock_settings_service.set_setting.call_args_list
                       if c[0][0] == "equalizer.active_preset"]
        assert len(preset_calls) == 0, "Should NOT switch preset on manual filter edit"

    @pytest.mark.asyncio
    async def test_manual_modification_does_not_save_custom_gains(self, camilladsp_service_with_jazz_preset, mock_settings_service, camilla_daemon):
        """Should NOT save custom gains when modifying filter"""
        mock_settings_service.get_setting = AsyncMock(side_effect=lambda key: {
            "equalizer.active_preset": "jazz",
            "equalizer.custom_gains": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
        }.get(key))

        camilla_daemon.load({"filters": {}, "pipeline": []})

        await camilladsp_service_with_jazz_preset.set_filter(
            filter_id="eq_band_00",
            freq=32,
            gain=6.0,
            q=1.41,
            filter_type="Peaking"
        )

        # Should NOT save custom gains
        save_calls = [c for c in mock_settings_service.set_setting.call_args_list
                      if c[0][0] == "equalizer.custom_gains"]
        assert len(save_calls) == 0, "Should NOT save custom gains on manual filter edit"


# =============================================================================
# Available presets list
# =============================================================================

class TestPresetsList:
    """GET /api/equalizer/presets returns 21 builtin + Custom"""

    def test_builtin_presets_count(self):
        """Should have exactly 22 builtin presets"""
        presets = get_builtin_presets()
        assert len(presets) == 22, f"Expected 22 builtin presets, got {len(presets)}"

    def test_all_presets_have_10_gains(self):
        """Each preset should have exactly 10 gain values"""
        for preset in BUILTIN_PRESETS:
            assert len(preset["gains"]) == 10, \
                f"Preset {preset['id']} should have 10 gains, got {len(preset['gains'])}"

    def test_all_gains_within_range(self):
        """All gain values should be within -15 to +15 dB"""
        for preset in BUILTIN_PRESETS:
            for i, gain in enumerate(preset["gains"]):
                assert -15 <= gain <= 15, \
                    f"Preset {preset['id']} band {i} gain {gain} out of range"

    def test_get_preset_by_id_returns_correct_preset(self):
        """get_preset_by_id should return the correct preset"""
        jazz = get_preset_by_id("jazz")
        assert jazz is not None
        assert jazz["id"] == "jazz"
        assert jazz["gains"] == [4, 3, 2, 2, -2, -2, 0, 2, 3, 4]

    def test_get_preset_by_id_returns_none_for_unknown(self):
        """get_preset_by_id should return None for unknown preset"""
        result = get_preset_by_id("nonexistent")
        assert result is None

    def test_expected_preset_ids_exist(self):
        """Verify expected preset IDs are present"""
        preset_ids = [p["id"] for p in BUILTIN_PRESETS]
        expected_ids = [
            "acoustic", "bass_boost", "bass_reducer", "classical", "dance",
            "deep", "electronic", "hip_hop", "jazz", "latin", "loudness",
            "lounge", "piano", "pop", "rnb", "rock", "small_speakers",
            "spoken_word", "treble_boost", "treble_reducer", "vocal_boost"
        ]
        for expected_id in expected_ids:
            assert expected_id in preset_ids, f"Expected preset '{expected_id}' not found"

    @pytest.mark.asyncio
    async def test_get_presets_api_returns_all_data(self, connected_camilladsp_service):
        """get_presets() should return presets, custom_gains, and active_preset"""
        connected_camilladsp_service._active_preset = "rock"
        connected_camilladsp_service._custom_gains = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]

        # Test the service methods
        presets = connected_camilladsp_service.get_presets()
        active = await connected_camilladsp_service.get_active_preset()
        custom = await connected_camilladsp_service.get_custom_gains()

        assert len(presets) == 22
        assert active == "rock"
        assert custom == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]


# =============================================================================
# API Models Tests
# =============================================================================

class TestEqualizerPresetRequestModel:
    """Test EqualizerPresetRequest Pydantic model validation"""

    def test_valid_preset_id(self):
        """Should accept valid preset IDs"""
        from backend.api.models import EqualizerPresetRequest

        request = EqualizerPresetRequest(preset_id="jazz")
        assert request.preset_id == "jazz"

        request = EqualizerPresetRequest(preset_id="bass_boost")
        assert request.preset_id == "bass_boost"

    def test_preset_id_normalized_to_lowercase(self):
        """Should normalize preset ID to lowercase"""
        from backend.api.models import EqualizerPresetRequest

        request = EqualizerPresetRequest(preset_id="JAZZ")
        assert request.preset_id == "jazz"

    def test_preset_id_stripped(self):
        """Should strip whitespace from preset ID"""
        from backend.api.models import EqualizerPresetRequest

        request = EqualizerPresetRequest(preset_id="  jazz  ")
        assert request.preset_id == "jazz"

    def test_invalid_preset_id_rejected(self):
        """Should reject invalid preset IDs"""
        from backend.api.models import EqualizerPresetRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            EqualizerPresetRequest(preset_id="jazz!")  # Special character

        with pytest.raises(ValidationError):
            EqualizerPresetRequest(preset_id="")  # Empty string
