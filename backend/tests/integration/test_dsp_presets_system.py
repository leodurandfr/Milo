# backend/tests/integration/test_dsp_presets_system.py
"""
Integration tests for Story 4-6: DSP Presets System

Tests cover:
- AC1: Apply preset to zone/client with gains and WebSocket event
- AC2: Auto-switch to "Manual" when modifying filter parameter
- AC3: Zone propagation to all ONLINE zone members
- AC4: Available presets list (21 builtin + Manual)
- AC5: Manual preset persistence and restoration
- AC6: Startup restoration of saved preset

These tests verify the complete preset flow:
API → CamillaDSPService → WebSocket → Frontend state update
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch

from backend.core.dsp import CamillaDSPService, DspState
from backend.core.dsp.presets import (
    BUILTIN_PRESETS,
    DEFAULT_MANUAL_GAINS,
    get_builtin_presets,
    get_preset_by_id
)
from backend.core.events import EventBus


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
def mock_event_bus():
    """Create mock event bus"""
    bus = Mock(spec=EventBus)
    bus.emit = AsyncMock()
    return bus


@pytest.fixture
def mock_state_machine():
    """Create mock state machine"""
    sm = Mock()
    sm.broadcast_event = AsyncMock()
    return sm


@pytest.fixture
def mock_dsp_router_service():
    """Create mock DSP router service for local/remote client checks"""
    service = Mock()
    # Local client returns True, remote clients return False
    service.is_local_client = Mock(side_effect=lambda mac_id: mac_id == "local")
    return service


@pytest.fixture
def mock_multiroom_dsp_service():
    """Create mock multiroom DSP service for zone and client operations"""
    service = Mock()
    service.load_zone_preset = AsyncMock(return_value=True)
    service.load_client_preset = AsyncMock(return_value=True)
    service.update_filter = AsyncMock(return_value=True)
    service.update_compressor = AsyncMock(return_value=True)
    service.update_loudness = AsyncMock(return_value=True)
    service.set_zone_dsp_effects_enabled = AsyncMock(return_value=True)
    return service


@pytest.fixture
def connected_dsp_service(mock_settings_service, mock_event_bus, mock_state_machine):
    """Create connected DSP service"""
    service = CamillaDSPService(
        settings_service=mock_settings_service,
        event_bus=mock_event_bus
    )
    service.set_state_machine(mock_state_machine)
    service._connected = True
    service._state = DspState.RUNNING

    # Set up 10-band EQ filters with default frequencies
    DEFAULT_EQ_FREQS = [31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
    service._filters = [
        {"id": f"eq_band_{i:02d}", "freq": freq, "gain": 0, "q": 1.41, "type": "Peaking", "enabled": True}
        for i, freq in enumerate(DEFAULT_EQ_FREQS)
    ]

    return service


@pytest.fixture
def dsp_service_with_jazz_preset(connected_dsp_service, mock_settings_service):
    """DSP service with jazz preset active"""
    # Simulate jazz preset loaded
    jazz_gains = [4, 3, 2, 2, -2, -2, 0, 2, 3, 4]
    for i, gain in enumerate(jazz_gains):
        connected_dsp_service._filters[i]["gain"] = gain

    mock_settings_service.get_setting = AsyncMock(side_effect=lambda key: {
        "dsp.active_preset": "jazz",
        "dsp.manual_gains": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    }.get(key))

    return connected_dsp_service


# =============================================================================
# AC1: Apply preset to zone/client
# =============================================================================

class TestAC1ApplyPreset:
    """AC1: Apply preset → gains overwritten, WebSocket preset_loaded broadcast"""

    @pytest.mark.asyncio
    async def test_load_preset_applies_correct_gains(self, connected_dsp_service, mock_settings_service):
        """Should apply preset gains to EQ bands"""
        jazz_gains = [4, 3, 2, 2, -2, -2, 0, 2, 3, 4]
        mock_config = {"filters": {}, "pipeline": []}

        with patch.object(connected_dsp_service, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_dsp_service, '_set_config', new_callable=AsyncMock):
                result = await connected_dsp_service.load_preset("jazz")

                assert result is True
                # Verify gains were applied
                for i, expected_gain in enumerate(jazz_gains):
                    assert connected_dsp_service._filters[i]["gain"] == expected_gain, \
                        f"Filter {i} should have gain={expected_gain}, got {connected_dsp_service._filters[i]['gain']}"

    @pytest.mark.asyncio
    async def test_load_preset_saves_active_preset_to_settings(self, connected_dsp_service, mock_settings_service):
        """Should save active preset ID to settings"""
        mock_config = {"filters": {}, "pipeline": []}

        with patch.object(connected_dsp_service, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_dsp_service, '_set_config', new_callable=AsyncMock):
                await connected_dsp_service.load_preset("rock")

                # Verify dsp.active_preset was saved
                mock_settings_service.set_setting.assert_any_call("dsp.active_preset", "rock")

    @pytest.mark.asyncio
    async def test_load_preset_broadcasts_preset_loaded_event(self, connected_dsp_service, mock_state_machine):
        """Should broadcast preset_loaded WebSocket event"""
        mock_config = {"filters": {}, "pipeline": []}

        with patch.object(connected_dsp_service, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_dsp_service, '_set_config', new_callable=AsyncMock):
                await connected_dsp_service.load_preset("classical")

                # Check preset_loaded event was broadcast
                calls = [c for c in mock_state_machine.broadcast_event.call_args_list
                         if c[0][1] == "preset_loaded"]
                assert len(calls) >= 1, "Should broadcast preset_loaded event"
                assert calls[-1][0][2] == {"id": "classical"}

    @pytest.mark.asyncio
    async def test_load_preset_returns_false_for_unknown_preset(self, connected_dsp_service):
        """Should return False for unknown preset ID"""
        result = await connected_dsp_service.load_preset("unknown_preset_xyz")
        assert result is False

    @pytest.mark.asyncio
    async def test_load_preset_skips_if_already_active(self, connected_dsp_service, mock_settings_service, mock_state_machine):
        """Should skip if preset is already active (no redundant API calls)"""
        mock_settings_service.get_setting = AsyncMock(return_value="jazz")

        result = await connected_dsp_service.load_preset("jazz")

        assert result is True
        # No set_config should be called if already on the same preset
        mock_state_machine.broadcast_event.assert_not_called()


# =============================================================================
# AC2: Auto-switch to Manual when modifying filter
# =============================================================================

class TestAC2AutoSwitchToManual:
    """AC2: Modifying filter while on preset → auto-switch to Manual"""

    @pytest.mark.asyncio
    async def test_set_filter_switches_to_manual_when_on_preset(self, dsp_service_with_jazz_preset, mock_settings_service):
        """Should switch to manual when modifying filter while on builtin preset"""
        mock_settings_service.get_setting = AsyncMock(side_effect=lambda key: {
            "dsp.active_preset": "jazz",
            "dsp.manual_gains": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
        }.get(key))

        mock_config = {"filters": {}, "pipeline": []}

        with patch.object(dsp_service_with_jazz_preset, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(dsp_service_with_jazz_preset, '_set_config', new_callable=AsyncMock):
                # Modify a filter (not from preset = from_preset=False is default)
                await dsp_service_with_jazz_preset.set_filter(
                    filter_id="eq_band_00",
                    freq=32,
                    gain=6.0,  # Different from jazz preset gain
                    q=1.41,
                    filter_type="Peaking"
                )

                # Should save manual gains and switch to manual
                save_calls = [c for c in mock_settings_service.set_setting.call_args_list
                              if c[0][0] == "dsp.manual_gains"]
                assert len(save_calls) >= 1, "Should save manual gains"

                preset_calls = [c for c in mock_settings_service.set_setting.call_args_list
                               if c[0][0] == "dsp.active_preset" and c[0][1] == "manual"]
                assert len(preset_calls) >= 1, "Should switch to manual preset"

    @pytest.mark.asyncio
    async def test_set_filter_with_from_preset_does_not_switch(self, dsp_service_with_jazz_preset, mock_settings_service):
        """Should NOT switch to manual when from_preset=True (preset loading)"""
        mock_settings_service.get_setting = AsyncMock(return_value="jazz")

        mock_config = {"filters": {}, "pipeline": []}

        with patch.object(dsp_service_with_jazz_preset, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(dsp_service_with_jazz_preset, '_set_config', new_callable=AsyncMock):
                # Modify a filter WITH from_preset=True
                await dsp_service_with_jazz_preset.set_filter(
                    filter_id="eq_band_00",
                    freq=32,
                    gain=6.0,
                    q=1.41,
                    filter_type="Peaking",
                    from_preset=True  # This prevents auto-switch
                )

                # Should NOT switch to manual
                preset_calls = [c for c in mock_settings_service.set_setting.call_args_list
                               if c[0][0] == "dsp.active_preset" and c[0][1] == "manual"]
                assert len(preset_calls) == 0, "Should NOT switch to manual when from_preset=True"

    @pytest.mark.asyncio
    async def test_auto_switch_broadcasts_preset_loaded_manual_event(self, dsp_service_with_jazz_preset, mock_settings_service, mock_state_machine):
        """Should broadcast preset_loaded with id=manual when auto-switching"""
        mock_settings_service.get_setting = AsyncMock(side_effect=lambda key: {
            "dsp.active_preset": "jazz",
            "dsp.manual_gains": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
        }.get(key))

        mock_config = {"filters": {}, "pipeline": []}

        with patch.object(dsp_service_with_jazz_preset, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(dsp_service_with_jazz_preset, '_set_config', new_callable=AsyncMock):
                await dsp_service_with_jazz_preset.set_filter(
                    filter_id="eq_band_05",
                    freq=1000,
                    gain=-3.0,
                    q=1.41,
                    filter_type="Peaking"
                )

                # Check preset_loaded event with "manual"
                calls = [c for c in mock_state_machine.broadcast_event.call_args_list
                         if c[0][1] == "preset_loaded"]
                manual_calls = [c for c in calls if c[0][2].get("id") == "manual"]
                assert len(manual_calls) >= 1, "Should broadcast preset_loaded with id=manual"


# =============================================================================
# AC3: Zone propagation
# =============================================================================

class TestAC3ZonePropagation:
    """AC3: Zone preset propagation to all ONLINE members within 200ms (NFR3)"""

    @pytest.mark.asyncio
    async def test_zone_preset_endpoint_exists(self):
        """Verify POST /api/dsp/zone/{zone_id}/preset route exists"""
        from backend.api.dsp import create_dsp_router

        mock_dsp = Mock()
        mock_sm = Mock()
        mock_registry = Mock()
        mock_dsp_router = Mock()
        mock_dsp_router.is_local_client = Mock(side_effect=lambda mac_id: mac_id == "local")

        router = create_dsp_router(
            dsp_service=mock_dsp,
            state_machine=mock_sm,
            client_registry_service=mock_registry,
            dsp_router_service=mock_dsp_router
        )

        routes = [r.path for r in router.routes]
        assert "/api/dsp/zone/{zone_id}/preset" in routes, \
            "Zone preset route should exist"

    @pytest.mark.asyncio
    async def test_client_preset_endpoint_exists(self):
        """Verify POST /api/dsp/client/{mac_id}/preset route exists"""
        from backend.api.dsp import create_dsp_router

        mock_dsp = Mock()
        mock_sm = Mock()
        mock_registry = Mock()
        mock_dsp_router = Mock()
        mock_dsp_router.is_local_client = Mock(side_effect=lambda mac_id: mac_id == "local")

        router = create_dsp_router(
            dsp_service=mock_dsp,
            state_machine=mock_sm,
            client_registry_service=mock_registry,
            dsp_router_service=mock_dsp_router
        )

        routes = [r.path for r in router.routes]
        assert "/api/dsp/client/{mac_id}/preset" in routes, \
            "Client preset route should exist"

    @pytest.mark.asyncio
    async def test_zone_preset_calls_multiroom_dsp_service(self, connected_dsp_service, mock_state_machine, mock_dsp_router_service, mock_multiroom_dsp_service):
        """Zone preset should delegate to multiroom_dsp_service.load_zone_preset()"""
        from backend.api.dsp import create_dsp_router
        from backend.api.models import DspPresetRequest

        router = create_dsp_router(
            dsp_service=connected_dsp_service,
            state_machine=mock_state_machine,
            dsp_router_service=mock_dsp_router_service,
            multiroom_dsp_service=mock_multiroom_dsp_service
        )

        # Get the endpoint function
        for route in router.routes:
            if route.path == "/api/dsp/zone/{zone_id}/preset":
                endpoint = route.endpoint
                break

        # Call endpoint
        payload = DspPresetRequest(preset_id="jazz")
        result = await endpoint("zone_test", payload)

        # Verify multiroom_dsp_service was called
        mock_multiroom_dsp_service.load_zone_preset.assert_called_once_with("zone_test", "jazz")
        assert result["status"] == "success"
        assert result["preset_id"] == "jazz"
        assert result["zone_id"] == "zone_test"

    @pytest.mark.asyncio
    async def test_zone_preset_returns_404_for_unknown_zone(self, connected_dsp_service, mock_state_machine, mock_dsp_router_service, mock_multiroom_dsp_service):
        """Zone preset should return 404 for unknown zone ID"""
        from backend.api.dsp import create_dsp_router
        from backend.api.models import DspPresetRequest
        from fastapi import HTTPException

        # Configure multiroom_dsp_service to raise ValueError for unknown zone
        mock_multiroom_dsp_service.load_zone_preset = AsyncMock(
            side_effect=ValueError("Zone not found: nonexistent_zone")
        )

        router = create_dsp_router(
            dsp_service=connected_dsp_service,
            state_machine=mock_state_machine,
            dsp_router_service=mock_dsp_router_service,
            multiroom_dsp_service=mock_multiroom_dsp_service
        )

        for route in router.routes:
            if route.path == "/api/dsp/zone/{zone_id}/preset":
                endpoint = route.endpoint
                break

        payload = DspPresetRequest(preset_id="jazz")

        with pytest.raises(HTTPException) as exc_info:
            await endpoint("nonexistent_zone", payload)

        assert exc_info.value.status_code == 404
        assert "not found" in str(exc_info.value.detail).lower()


class TestAC3ClientPresetEndpoint:
    """AC3: Client preset endpoint tests (POST /api/dsp/client/{mac_id}/preset)

    These tests verify the endpoint delegates to multiroom_dsp_service.load_client_preset()
    """

    @pytest.mark.asyncio
    async def test_client_preset_calls_multiroom_dsp_service(self, connected_dsp_service, mock_state_machine, mock_dsp_router_service, mock_multiroom_dsp_service):
        """Client preset should delegate to multiroom_dsp_service.load_client_preset()"""
        from backend.api.dsp import create_dsp_router
        from backend.api.models import DspPresetRequest

        router = create_dsp_router(
            dsp_service=connected_dsp_service,
            state_machine=mock_state_machine,
            dsp_router_service=mock_dsp_router_service,
            multiroom_dsp_service=mock_multiroom_dsp_service
        )

        for route in router.routes:
            if route.path == "/api/dsp/client/{mac_id}/preset":
                endpoint = route.endpoint
                break

        payload = DspPresetRequest(preset_id="pop")
        result = await endpoint("local", payload)

        # Verify multiroom_dsp_service was called
        mock_multiroom_dsp_service.load_client_preset.assert_called_once_with("local", "pop")
        assert result["status"] == "success"
        assert result["client_id"] == "local"
        assert result["preset_id"] == "pop"

    @pytest.mark.asyncio
    async def test_client_preset_returns_404_for_unknown_client(self, connected_dsp_service, mock_state_machine, mock_dsp_router_service, mock_multiroom_dsp_service):
        """Client preset for unknown client should return 404"""
        from backend.api.dsp import create_dsp_router
        from backend.api.models import DspPresetRequest
        from fastapi import HTTPException

        # Configure multiroom_dsp_service to raise ValueError for unknown client
        mock_multiroom_dsp_service.load_client_preset = AsyncMock(
            side_effect=ValueError("Client not found: unknown:mac:address")
        )

        router = create_dsp_router(
            dsp_service=connected_dsp_service,
            state_machine=mock_state_machine,
            dsp_router_service=mock_dsp_router_service,
            multiroom_dsp_service=mock_multiroom_dsp_service
        )

        for route in router.routes:
            if route.path == "/api/dsp/client/{mac_id}/preset":
                endpoint = route.endpoint
                break

        payload = DspPresetRequest(preset_id="jazz")

        with pytest.raises(HTTPException) as exc_info:
            await endpoint("unknown:mac:address", payload)

        assert exc_info.value.status_code == 404
        assert "not found" in str(exc_info.value.detail).lower()

    @pytest.mark.asyncio
    async def test_client_preset_returns_404_for_zone_client(self, connected_dsp_service, mock_state_machine, mock_dsp_router_service, mock_multiroom_dsp_service):
        """Client preset for a client in a zone should return 404 with guidance"""
        from backend.api.dsp import create_dsp_router
        from backend.api.models import DspPresetRequest
        from fastapi import HTTPException

        # Configure multiroom_dsp_service to raise ValueError for zone client
        mock_multiroom_dsp_service.load_client_preset = AsyncMock(
            side_effect=ValueError("Client aa:bb:cc:dd:ee:ff is in a zone. Use load_zone_preset() instead.")
        )

        router = create_dsp_router(
            dsp_service=connected_dsp_service,
            state_machine=mock_state_machine,
            dsp_router_service=mock_dsp_router_service,
            multiroom_dsp_service=mock_multiroom_dsp_service
        )

        for route in router.routes:
            if route.path == "/api/dsp/client/{mac_id}/preset":
                endpoint = route.endpoint
                break

        payload = DspPresetRequest(preset_id="jazz")

        with pytest.raises(HTTPException) as exc_info:
            await endpoint("aa:bb:cc:dd:ee:ff", payload)

        assert exc_info.value.status_code == 404
        assert "zone" in str(exc_info.value.detail).lower()


# =============================================================================
# AC4: Available presets list
# =============================================================================

class TestAC4PresetsList:
    """AC4: GET /api/dsp/presets returns 21 builtin + Manual"""

    def test_builtin_presets_count(self):
        """Should have exactly 21 builtin presets"""
        presets = get_builtin_presets()
        assert len(presets) == 21, f"Expected 21 builtin presets, got {len(presets)}"

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

    def test_default_manual_gains_are_flat(self):
        """Default manual gains should be all zeros (flat)"""
        assert DEFAULT_MANUAL_GAINS == [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]

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
    async def test_get_presets_api_returns_all_data(self, connected_dsp_service, mock_settings_service):
        """get_presets() should return presets, manual_gains, and active_preset"""
        mock_settings_service.get_setting = AsyncMock(side_effect=lambda key: {
            "dsp.active_preset": "rock",
            "dsp.manual_gains": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
        }.get(key))

        # Test the service methods
        presets = connected_dsp_service.get_presets()
        active = await connected_dsp_service.get_active_preset()
        manual = await connected_dsp_service.get_manual_gains()

        assert len(presets) == 21
        assert active == "rock"
        assert manual == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]

    @pytest.mark.asyncio
    async def test_manual_preset_selectable_via_api(self, connected_dsp_service, mock_settings_service):
        """Manual preset should be loadable via load_preset('manual')"""
        saved_manual_gains = [3, 2, 1, 0, -1, -2, -3, -4, -5, -6]

        mock_settings_service.get_setting = AsyncMock(side_effect=lambda key: {
            "dsp.active_preset": "jazz",  # Currently on jazz
            "dsp.manual_gains": saved_manual_gains,
        }.get(key))

        mock_config = {"filters": {}, "pipeline": []}

        with patch.object(connected_dsp_service, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_dsp_service, '_set_config', new_callable=AsyncMock):
                result = await connected_dsp_service.load_preset("manual")

                assert result is True
                # Verify saved manual gains were applied
                for i, expected_gain in enumerate(saved_manual_gains):
                    assert connected_dsp_service._filters[i]["gain"] == expected_gain

                # Verify active preset set to manual
                mock_settings_service.set_setting.assert_any_call("dsp.active_preset", "manual")


# =============================================================================
# AC5: Manual preset persistence
# =============================================================================

class TestAC5ManualPresetPersistence:
    """AC5: Manual gains saved before switching, restored on return to Manual"""

    @pytest.mark.asyncio
    async def test_save_manual_gains_before_switching_preset(self, connected_dsp_service, mock_settings_service):
        """Should save current gains as manual before switching to builtin preset"""
        # Start with custom gains (simulating manual mode)
        for i, gain in enumerate([5, 4, 3, 2, 1, 0, -1, -2, -3, -4]):
            connected_dsp_service._filters[i]["gain"] = gain

        # Currently on "manual" preset
        mock_settings_service.get_setting = AsyncMock(side_effect=lambda key: {
            "dsp.active_preset": "manual",
            "dsp.manual_gains": None,
        }.get(key))

        mock_config = {"filters": {}, "pipeline": []}

        with patch.object(connected_dsp_service, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_dsp_service, '_set_config', new_callable=AsyncMock):
                await connected_dsp_service.load_preset("jazz")

                # Verify manual gains were saved before switching
                save_calls = [c for c in mock_settings_service.set_setting.call_args_list
                              if c[0][0] == "dsp.manual_gains"]
                assert len(save_calls) >= 1, "Should save manual gains before switching"
                # Verify the saved gains match what was in the filters
                saved_gains = save_calls[0][0][1]
                assert saved_gains == [5, 4, 3, 2, 1, 0, -1, -2, -3, -4]

    @pytest.mark.asyncio
    async def test_load_manual_preset_restores_saved_gains(self, connected_dsp_service, mock_settings_service):
        """Switching to Manual should restore previously saved manual gains"""
        saved_manual_gains = [2, 4, 6, 8, 10, 8, 6, 4, 2, 0]

        mock_settings_service.get_setting = AsyncMock(side_effect=lambda key: {
            "dsp.active_preset": "jazz",  # Currently on jazz
            "dsp.manual_gains": saved_manual_gains,
        }.get(key))

        mock_config = {"filters": {}, "pipeline": []}

        with patch.object(connected_dsp_service, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_dsp_service, '_set_config', new_callable=AsyncMock):
                await connected_dsp_service.load_preset("manual")

                # Verify saved manual gains were applied
                for i, expected_gain in enumerate(saved_manual_gains):
                    assert connected_dsp_service._filters[i]["gain"] == expected_gain, \
                        f"Filter {i} should have gain={expected_gain}"


# =============================================================================
# AC6: Startup restoration
# =============================================================================

class TestAC6StartupRestoration:
    """AC6: Saved preset is applied automatically on backend restart"""

    @pytest.mark.asyncio
    async def test_apply_saved_preset_on_initialization(self, mock_settings_service, mock_event_bus, mock_state_machine):
        """Should apply saved preset during initialization"""
        mock_settings_service.get_setting = AsyncMock(side_effect=lambda key: {
            "dsp.active_preset": "rock",
            "dsp.manual_gains": [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
            "dsp.filters": None,
            "dsp.compressor": None,
            "dsp.loudness": None,
        }.get(key))

        service = CamillaDSPService(
            settings_service=mock_settings_service,
            event_bus=mock_event_bus
        )
        service.set_state_machine(mock_state_machine)
        service._connected = True
        service._state = DspState.RUNNING

        # Initialize default filters
        DEFAULT_EQ_FREQS = [31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
        service._filters = [
            {"id": f"eq_band_{i:02d}", "freq": freq, "gain": 0, "q": 1.41, "type": "Peaking", "enabled": True}
            for i, freq in enumerate(DEFAULT_EQ_FREQS)
        ]

        mock_config = {"filters": {}, "pipeline": []}

        with patch.object(service, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(service, '_set_config', new_callable=AsyncMock):
                # Simulate what happens during initialize()
                await service._apply_saved_preset()

                # Verify rock preset gains were applied
                rock_gains = [5, 4, 3, 2, 0, -1, 1, 3, 4, 5]
                for i, expected_gain in enumerate(rock_gains):
                    assert service._filters[i]["gain"] == expected_gain, \
                        f"Filter {i} should have rock preset gain={expected_gain}"

    @pytest.mark.asyncio
    async def test_apply_saved_preset_does_nothing_if_no_preset_saved(self, mock_settings_service, mock_event_bus):
        """Should do nothing if no preset is saved in settings"""
        mock_settings_service.get_setting = AsyncMock(return_value=None)

        service = CamillaDSPService(
            settings_service=mock_settings_service,
            event_bus=mock_event_bus
        )
        service._connected = True

        # Initialize with zero gains
        service._filters = [
            {"id": f"eq_band_{i:02d}", "freq": 100, "gain": 0, "q": 1.41, "type": "Peaking", "enabled": True}
            for i in range(10)
        ]

        await service._apply_saved_preset()

        # Gains should remain at 0 (no preset applied)
        for f in service._filters:
            assert f["gain"] == 0, "Gains should remain unchanged when no preset saved"


# =============================================================================
# API Models Tests
# =============================================================================

class TestDspPresetRequestModel:
    """Test DspPresetRequest Pydantic model validation"""

    def test_valid_preset_id(self):
        """Should accept valid preset IDs"""
        from backend.api.models import DspPresetRequest

        request = DspPresetRequest(preset_id="jazz")
        assert request.preset_id == "jazz"

        request = DspPresetRequest(preset_id="bass_boost")
        assert request.preset_id == "bass_boost"

    def test_preset_id_normalized_to_lowercase(self):
        """Should normalize preset ID to lowercase"""
        from backend.api.models import DspPresetRequest

        request = DspPresetRequest(preset_id="JAZZ")
        assert request.preset_id == "jazz"

    def test_preset_id_stripped(self):
        """Should strip whitespace from preset ID"""
        from backend.api.models import DspPresetRequest

        request = DspPresetRequest(preset_id="  jazz  ")
        assert request.preset_id == "jazz"

    def test_invalid_preset_id_rejected(self):
        """Should reject invalid preset IDs"""
        from backend.api.models import DspPresetRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            DspPresetRequest(preset_id="jazz!")  # Special character

        with pytest.raises(ValidationError):
            DspPresetRequest(preset_id="")  # Empty string
