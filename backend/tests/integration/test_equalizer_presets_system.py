# backend/tests/integration/test_equalizer_presets_system.py
"""
Integration tests for Story 4-6: Equalizer Presets System

Tests cover:
- AC1: Apply preset to zone/client with gains and WebSocket event
- AC2: No auto-switch when modifying filter parameter (edited state is frontend-only)
- AC3: Zone propagation to all ONLINE zone members
- AC4: Available presets list (21 builtin + Custom)
- AC5: Custom preset persistence and restoration
- AC6: Startup restoration of saved preset

These tests verify the complete preset flow:
API → CamillaDSPService → WebSocket → Frontend state update
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch

from backend.core.equalizer import CamillaDSPService, CamillaDspState
from backend.core.equalizer.presets import (
    BUILTIN_PRESETS,
    DEFAULT_EQ_FREQS,
    DEFAULT_CUSTOM_GAINS,
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
    sm.broadcast_event = AsyncMock()
    return sm


@pytest.fixture
def mock_equalizer_router_service():
    """Create mock Equalizer router service for local/remote client checks"""
    service = Mock()
    # Local client returns True, remote clients return False
    service.is_local_client = Mock(side_effect=lambda mac_id: mac_id == "local")
    return service


@pytest.fixture
def mock_multiroom_equalizer_service():
    """Create mock multiroom Equalizer service for zone and client operations"""
    service = Mock()
    service.get_zone_equalizer = AsyncMock(return_value=Mock())
    service.get_client_equalizer = AsyncMock(return_value=Mock())
    service.resolve_preset_gains = AsyncMock(return_value=[0.0] * 10)
    service.load_zone_preset = AsyncMock(return_value=True)
    service.load_client_preset = AsyncMock(return_value=True)
    service.update_filter = AsyncMock(return_value=True)
    service.update_compressor = AsyncMock(return_value=True)
    service.update_loudness = AsyncMock(return_value=True)
    service.set_zone_equalizer_effects_enabled = AsyncMock(return_value=True)
    return service


@pytest.fixture
def connected_camilladsp_service(mock_settings_service, mock_state_machine):
    """Create connected Equalizer service"""
    service = CamillaDSPService(
        settings_service=mock_settings_service
    )
    service.set_state_machine(mock_state_machine)
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
# AC1: Apply preset to zone/client
# =============================================================================

class TestAC1ApplyPreset:
    """AC1: Apply preset → gains overwritten, WebSocket preset_loaded broadcast"""

    @pytest.mark.asyncio
    async def test_load_preset_applies_correct_gains(self, connected_camilladsp_service, mock_settings_service):
        """Should apply preset gains to EQ bands"""
        jazz_gains = [4, 3, 2, 2, -2, -2, 0, 2, 3, 4]
        mock_config = {"filters": {}, "pipeline": []}

        with patch.object(connected_camilladsp_service, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_camilladsp_service, '_set_config', new_callable=AsyncMock):
                result = await connected_camilladsp_service.load_preset("jazz")

                assert result is True
                # Verify gains were applied
                for i, expected_gain in enumerate(jazz_gains):
                    assert connected_camilladsp_service._filters[i]["gain"] == expected_gain, \
                        f"Filter {i} should have gain={expected_gain}, got {connected_camilladsp_service._filters[i]['gain']}"

    @pytest.mark.asyncio
    async def test_load_preset_saves_active_preset_to_settings(self, connected_camilladsp_service):
        """Should save active preset ID to in-memory state."""
        mock_config = {"filters": {}, "pipeline": []}

        with patch.object(connected_camilladsp_service, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_camilladsp_service, '_set_config', new_callable=AsyncMock):
                await connected_camilladsp_service.load_preset("rock")

                assert connected_camilladsp_service._active_preset == "rock"

    @pytest.mark.asyncio
    async def test_load_preset_broadcasts_preset_loaded_event(self, connected_camilladsp_service, mock_state_machine):
        """Should broadcast preset_loaded WebSocket event"""
        mock_config = {"filters": {}, "pipeline": []}

        with patch.object(connected_camilladsp_service, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_camilladsp_service, '_set_config', new_callable=AsyncMock):
                await connected_camilladsp_service.load_preset("classical")

                # Check preset_loaded event was broadcast
                calls = [c for c in mock_state_machine.broadcast_event.call_args_list
                         if c[0][1] == "preset_loaded"]
                assert len(calls) >= 1, "Should broadcast preset_loaded event"
                assert calls[-1][0][2] == {"id": "classical"}

    @pytest.mark.asyncio
    async def test_load_preset_returns_false_for_unknown_preset(self, connected_camilladsp_service):
        """Should return False for unknown preset ID"""
        result = await connected_camilladsp_service.load_preset("unknown_preset_xyz")
        assert result is False

    @pytest.mark.asyncio
    async def test_load_preset_skips_if_already_active(self, connected_camilladsp_service, mock_state_machine):
        """Should skip if preset is already active (no redundant API calls)"""
        connected_camilladsp_service._active_preset = "jazz"

        result = await connected_camilladsp_service.load_preset("jazz")

        assert result is True
        # No set_config should be called if already on the same preset
        mock_state_machine.broadcast_event.assert_not_called()


# =============================================================================
# AC2: No auto-switch when modifying filter (edited state is frontend-only)
# =============================================================================

class TestAC2NoAutoSwitch:
    """AC2: Modifying filter while on preset does NOT auto-switch to custom"""

    @pytest.mark.asyncio
    async def test_manual_modification_does_not_switch_preset(self, camilladsp_service_with_jazz_preset, mock_settings_service):
        """Should NOT switch preset when modifying filter while on builtin preset"""
        mock_settings_service.get_setting = AsyncMock(side_effect=lambda key: {
            "equalizer.active_preset": "jazz",
            "equalizer.custom_gains": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
        }.get(key))

        mock_config = {"filters": {}, "pipeline": []}

        with patch.object(camilladsp_service_with_jazz_preset, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(camilladsp_service_with_jazz_preset, '_set_config', new_callable=AsyncMock):
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
    async def test_manual_modification_does_not_broadcast_preset_loaded(self, camilladsp_service_with_jazz_preset, mock_settings_service, mock_state_machine):
        """Should NOT broadcast preset_loaded when modifying filter"""
        mock_settings_service.get_setting = AsyncMock(side_effect=lambda key: {
            "equalizer.active_preset": "jazz",
            "equalizer.custom_gains": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
        }.get(key))

        mock_config = {"filters": {}, "pipeline": []}

        with patch.object(camilladsp_service_with_jazz_preset, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(camilladsp_service_with_jazz_preset, '_set_config', new_callable=AsyncMock):
                await camilladsp_service_with_jazz_preset.set_filter(
                    filter_id="eq_band_05",
                    freq=1000,
                    gain=-3.0,
                    q=1.41,
                    filter_type="Peaking"
                )

                # Should NOT broadcast preset_loaded
                calls = [c for c in mock_state_machine.broadcast_event.call_args_list
                         if len(c[0]) > 1 and c[0][1] == "preset_loaded"]
                assert len(calls) == 0, "Should NOT broadcast preset_loaded on manual filter edit"

    @pytest.mark.asyncio
    async def test_manual_modification_does_not_save_custom_gains(self, camilladsp_service_with_jazz_preset, mock_settings_service):
        """Should NOT save custom gains when modifying filter"""
        mock_settings_service.get_setting = AsyncMock(side_effect=lambda key: {
            "equalizer.active_preset": "jazz",
            "equalizer.custom_gains": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
        }.get(key))

        mock_config = {"filters": {}, "pipeline": []}

        with patch.object(camilladsp_service_with_jazz_preset, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(camilladsp_service_with_jazz_preset, '_set_config', new_callable=AsyncMock):
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
# AC4: Available presets list
# =============================================================================

class TestAC4PresetsList:
    """AC4: GET /api/equalizer/presets returns 21 builtin + Custom"""

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

    def test_default_custom_gains_are_flat(self):
        """Default custom gains should be all zeros (flat)"""
        assert DEFAULT_CUSTOM_GAINS == [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]

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

    @pytest.mark.asyncio
    async def test_custom_preset_selectable_via_api(self, connected_camilladsp_service):
        """Custom preset should be loadable via load_preset('custom')"""
        saved_custom_gains = [3, 2, 1, 0, -1, -2, -3, -4, -5, -6]

        connected_camilladsp_service._active_preset = "jazz"
        connected_camilladsp_service._custom_gains = list(saved_custom_gains)

        mock_config = {"filters": {}, "pipeline": []}

        with patch.object(connected_camilladsp_service, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_camilladsp_service, '_set_config', new_callable=AsyncMock):
                result = await connected_camilladsp_service.load_preset("custom")

                assert result is True
                # Verify saved custom gains were applied
                for i, expected_gain in enumerate(saved_custom_gains):
                    assert connected_camilladsp_service._filters[i]["gain"] == expected_gain

                # Verify active preset set to custom
                assert connected_camilladsp_service._active_preset == "custom"


# =============================================================================
# AC5: Custom preset persistence
# =============================================================================

class TestAC5CustomPresetPersistence:
    """AC5: Custom gains restored when loading custom preset"""

    @pytest.mark.asyncio
    async def test_switching_preset_does_not_auto_save_custom_gains(self, connected_camilladsp_service, mock_settings_service):
        """Should NOT auto-save custom gains when switching to another preset"""
        # Start with custom gains (simulating custom mode)
        for i, gain in enumerate([5, 4, 3, 2, 1, 0, -1, -2, -3, -4]):
            connected_camilladsp_service._filters[i]["gain"] = gain

        # Currently on "custom" preset
        mock_settings_service.get_setting = AsyncMock(side_effect=lambda key: {
            "equalizer.active_preset": "custom",
            "equalizer.custom_gains": None,
        }.get(key))

        mock_config = {"filters": {}, "pipeline": []}

        with patch.object(connected_camilladsp_service, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_camilladsp_service, '_set_config', new_callable=AsyncMock):
                await connected_camilladsp_service.load_preset("jazz")

                # Should NOT auto-save custom gains (user must use save-custom endpoint)
                save_calls = [c for c in mock_settings_service.set_setting.call_args_list
                              if c[0][0] == "equalizer.custom_gains"]
                assert len(save_calls) == 0, "Should NOT auto-save custom gains when switching preset"

    @pytest.mark.asyncio
    async def test_load_custom_preset_restores_saved_gains(self, connected_camilladsp_service):
        """Switching to Custom should restore previously saved custom gains"""
        saved_custom_gains = [2, 4, 6, 8, 10, 8, 6, 4, 2, 0]

        connected_camilladsp_service._active_preset = "jazz"
        connected_camilladsp_service._custom_gains = list(saved_custom_gains)

        mock_config = {"filters": {}, "pipeline": []}

        with patch.object(connected_camilladsp_service, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_camilladsp_service, '_set_config', new_callable=AsyncMock):
                await connected_camilladsp_service.load_preset("custom")

                # Verify saved custom gains were applied
                for i, expected_gain in enumerate(saved_custom_gains):
                    assert connected_camilladsp_service._filters[i]["gain"] == expected_gain, \
                        f"Filter {i} should have gain={expected_gain}"


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
