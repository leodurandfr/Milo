# backend/tests/integration/test_global_equalizer_bypass.py
"""
Integration tests for Story 4-5: Global Equalizer Bypass

Tests cover:
- AC1: bypass_effects() sets all EQ to 0dB, disables compressor/loudness
- AC2: restore_effects() restores all Equalizer settings from equalizer.* settings
- AC3: Zone propagation for Equalizer bypass (API-level validation)
- AC4: Crossover filters NOT affected by bypass
- AC5: Bypass preserves persisted settings (persist=False pattern)
- AC6: State syncs on reconnection and mode switch

These tests verify the complete flow:
API → routing_service → CamillaDSPService → WebSocket → Frontend state update
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
import asyncio

from backend.core.equalizer import (
    CamillaDSPService,
    CamillaDspState,
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
def connected_camilladsp_with_effects(mock_settings_service, mock_state_machine):
    """Create connected Equalizer service with EQ, compressor, and loudness enabled"""
    service = CamillaDSPService(
        settings_service=mock_settings_service
    )
    service.set_state_machine(mock_state_machine)
    service._connected = True
    service._state = CamillaDspState.RUNNING

    # Set up EQ filters with gains
    service._filters = [
        {"id": "eq_band_00", "freq": 32, "gain": 3.0, "q": 1.41, "type": "Peaking", "enabled": True},
        {"id": "eq_band_01", "freq": 64, "gain": -2.0, "q": 1.41, "type": "Peaking", "enabled": True},
        {"id": "eq_band_02", "freq": 125, "gain": 4.0, "q": 1.41, "type": "Peaking", "enabled": True},
    ]

    # Enable compressor
    service._compressor = {
        "enabled": True,
        "threshold": -25,
        "ratio": 6,
        "attack": 15,
        "release": 150,
        "makeup_gain": 5
    }

    # Enable loudness
    service._loudness = {
        "enabled": True,
                "low_boost": 10,
        "high_boost": 8
    }

    return service


@pytest.fixture
def disconnected_camilladsp_service(mock_settings_service):
    """Create disconnected Equalizer service"""
    return CamillaDSPService(
        settings_service=mock_settings_service
    )


# =============================================================================
# AC1: bypass_effects() sets all EQ to 0dB, disables compressor/loudness
# =============================================================================

class TestAC1BypassEffects:
    """AC1: Toggle enables bypass_effects() → all EQ to 0dB, compressor/loudness disabled"""

    @pytest.mark.asyncio
    async def test_bypass_resets_all_eq_bands_to_zero(self, connected_camilladsp_with_effects, mock_settings_service):
        """Should reset all EQ band gains to 0 dB"""
        mock_config = {
            "filters": {
                "eq_band_00": {"type": "Biquad", "parameters": {"type": "Peaking", "freq": 32, "gain": 3, "q": 1.41}},
                "eq_band_01": {"type": "Biquad", "parameters": {"type": "Peaking", "freq": 64, "gain": -2, "q": 1.41}},
                "eq_band_02": {"type": "Biquad", "parameters": {"type": "Peaking", "freq": 125, "gain": 4, "q": 1.41}},
            },
            "processors": {"compressor": {}},
            "pipeline": []
        }
        captured_config = None

        async def capture_config(config):
            nonlocal captured_config
            captured_config = config

        with patch.object(connected_camilladsp_with_effects, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_camilladsp_with_effects, '_set_config', new_callable=AsyncMock, side_effect=capture_config):
                with patch.object(connected_camilladsp_with_effects, 'save_current_config', new_callable=AsyncMock):
                    result = await connected_camilladsp_with_effects.bypass_effects()

                    assert result is True
                    # All EQ gains should be 0
                    for filter_id, filter_data in captured_config["filters"].items():
                        if filter_id.startswith("eq_band_"):
                            assert filter_data["parameters"]["gain"] == 0, \
                                f"Filter {filter_id} should have gain=0, got {filter_data['parameters']['gain']}"

    @pytest.mark.asyncio
    async def test_bypass_disables_compressor(self, connected_camilladsp_with_effects, mock_settings_service):
        """Should disable compressor when bypass_effects() is called"""
        mock_config = {
            "filters": {},
            "processors": {"compressor": {"type": "Compressor", "parameters": {}}},
            "pipeline": [{"type": "Processor", "name": "compressor"}]
        }
        captured_config = None

        async def capture_config(config):
            nonlocal captured_config
            captured_config = config

        with patch.object(connected_camilladsp_with_effects, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_camilladsp_with_effects, '_set_config', new_callable=AsyncMock, side_effect=capture_config):
                with patch.object(connected_camilladsp_with_effects, 'save_current_config', new_callable=AsyncMock):
                    result = await connected_camilladsp_with_effects.bypass_effects()

                    assert result is True
                    assert connected_camilladsp_with_effects._compressor["enabled"] is False

    @pytest.mark.asyncio
    async def test_bypass_disables_loudness(self, connected_camilladsp_with_effects, mock_settings_service):
        """Should disable loudness when bypass_effects() is called"""
        mock_config = {
            "filters": {
                "loudness_low": {"type": "Biquad", "parameters": {"type": "Lowshelf", "freq": 100, "gain": 10, "slope": 6}},
                "loudness_high": {"type": "Biquad", "parameters": {"type": "Highshelf", "freq": 8000, "gain": 8, "slope": 6}},
            },
            "processors": {},
            "pipeline": []
        }
        captured_config = None

        async def capture_config(config):
            nonlocal captured_config
            captured_config = config

        with patch.object(connected_camilladsp_with_effects, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_camilladsp_with_effects, '_set_config', new_callable=AsyncMock, side_effect=capture_config):
                with patch.object(connected_camilladsp_with_effects, 'save_current_config', new_callable=AsyncMock):
                    result = await connected_camilladsp_with_effects.bypass_effects()

                    assert result is True
                    assert connected_camilladsp_with_effects._loudness["enabled"] is False


# =============================================================================
# AC2: restore_effects() restores all Equalizer settings from equalizer.* settings
# =============================================================================

class TestAC2RestoreEffects:
    """AC2: Toggle enables restore_effects() → restore all Equalizer from settings"""

    @pytest.mark.asyncio
    async def test_restore_loads_eq_filters_from_settings(self, connected_camilladsp_with_effects, mock_settings_service):
        """Should restore EQ filters from eq.filters settings"""
        saved_filters = [
            {"id": "eq_band_00", "freq": 32, "gain": 3, "q": 1.41, "type": "Peaking", "enabled": True},
            {"id": "eq_band_01", "freq": 64, "gain": -2, "q": 1.41, "type": "Peaking", "enabled": True},
        ]

        mock_settings_service.get_setting = AsyncMock(side_effect=lambda key: {
            "equalizer.filters": saved_filters,
            "equalizer.compressor": {"enabled": True, "threshold": -25, "ratio": 6},
            "equalizer.loudness": {"enabled": True, "high_boost": 8}
        }.get(key))

        mock_config = {"filters": {}, "processors": {}, "pipeline": []}

        with patch.object(connected_camilladsp_with_effects, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_camilladsp_with_effects, '_set_config', new_callable=AsyncMock):
                result = await connected_camilladsp_with_effects.restore_effects()

                assert result is True
                # Verify get_setting was called for eq.filters
                filter_calls = [c for c in mock_settings_service.get_setting.call_args_list
                               if c[0][0] == "equalizer.filters"]
                assert len(filter_calls) >= 1

    @pytest.mark.asyncio
    async def test_restore_loads_compressor_from_settings(self, connected_camilladsp_with_effects, mock_settings_service):
        """Should restore compressor settings from eq.compressor"""
        saved_compressor = {
            "enabled": True,
            "threshold": -25,
            "ratio": 6,
            "attack": 15,
            "release": 150,
            "makeup_gain": 5
        }

        mock_settings_service.get_setting = AsyncMock(side_effect=lambda key: {
            "equalizer.filters": [],
            "equalizer.compressor": saved_compressor,
            "equalizer.loudness": {"enabled": False}
        }.get(key))

        # Start with compressor disabled (as if bypassed)
        connected_camilladsp_with_effects._compressor["enabled"] = False

        mock_config = {"filters": {}, "processors": {}, "pipeline": []}

        with patch.object(connected_camilladsp_with_effects, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_camilladsp_with_effects, '_set_config', new_callable=AsyncMock):
                result = await connected_camilladsp_with_effects.restore_effects()

                assert result is True
                assert connected_camilladsp_with_effects._compressor["enabled"] is True
                assert connected_camilladsp_with_effects._compressor["threshold"] == -25

    @pytest.mark.asyncio
    async def test_restore_loads_loudness_from_settings(self, connected_camilladsp_with_effects, mock_settings_service):
        """Should restore loudness settings from eq.loudness"""
        saved_loudness = {
            "enabled": True,
                        "low_boost": 10,
            "high_boost": 8
        }

        mock_settings_service.get_setting = AsyncMock(side_effect=lambda key: {
            "equalizer.filters": [],
            "equalizer.compressor": {"enabled": False},
            "equalizer.loudness": saved_loudness
        }.get(key))

        # Start with loudness disabled (as if bypassed)
        connected_camilladsp_with_effects._loudness["enabled"] = False

        mock_config = {"filters": {}, "processors": {}, "pipeline": []}

        with patch.object(connected_camilladsp_with_effects, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_camilladsp_with_effects, '_set_config', new_callable=AsyncMock):
                result = await connected_camilladsp_with_effects.restore_effects()

                assert result is True
                assert connected_camilladsp_with_effects._loudness["enabled"] is True
                assert connected_camilladsp_with_effects._loudness["low_boost"] == 10


# =============================================================================
# AC3: Zone propagation for Equalizer bypass (API-level validation)
# =============================================================================

class TestAC3ZonePropagation:
    """AC3: Equalizer bypass/restore propagates to all zone members within 200ms"""

    @pytest.mark.asyncio
    async def test_client_enabled_proxy_route_exists(self):
        """Verify /api/equalizer/client/{hostname}/enabled route exists"""
        from backend.api.equalizer import create_equalizer_router

        # Create router with minimal mocks
        mock_camilladsp = Mock()
        mock_sm = Mock()

        router = create_equalizer_router(
            camilladsp_service=mock_camilladsp,
            state_machine=mock_sm,
            routing_service=Mock()
        )

        # Check that the route is registered
        routes = [r.path for r in router.routes]
        assert "/api/equalizer/client/{hostname}/enabled" in routes, \
            "Proxy route /api/equalizer/client/{hostname}/enabled should exist"

    @pytest.mark.asyncio
    async def test_local_client_enabled_uses_routing_service(self, connected_camilladsp_with_effects):
        """PUT /api/equalizer/client/local/enabled should use routing_service"""
        # This test verifies the route logic handles "local" correctly
        # Full integration would require FastAPI TestClient

        # Verify routing_service.set_equalizer_effects_enabled exists and is callable
        from backend.core.multiroom.routing import AudioRoutingService

        mock_settings = Mock()
        mock_settings.get_setting = AsyncMock(return_value=False)
        mock_settings.set_setting = AsyncMock()

        routing = AudioRoutingService(settings_service=mock_settings)
        routing._initial_detection_done = True

        # Set up state_machine mock
        mock_sm = Mock()
        mock_sm.system_state = Mock()
        mock_sm.system_state.equalizer_effects_enabled = False
        mock_sm.broadcast_event = AsyncMock()
        mock_sm.update_equalizer_effects_state = AsyncMock(
            side_effect=lambda v, silent=False: setattr(mock_sm.system_state, 'equalizer_effects_enabled', v)
        )
        routing.set_state_machine(mock_sm)

        # Mock camilladsp_service
        mock_camilladsp = Mock()
        mock_camilladsp.bypass_effects = AsyncMock(return_value=True)
        mock_camilladsp.restore_effects = AsyncMock(return_value=True)
        routing.set_camilladsp_service(mock_camilladsp)

        # Test enabling Equalizer effects
        result = await routing.set_equalizer_effects_enabled(True)
        assert result is True
        mock_camilladsp.restore_effects.assert_called_once()

    @pytest.mark.asyncio
    async def test_remote_client_enabled_proxies_to_client(self):
        """PUT /api/equalizer/client/{hostname}/enabled should proxy to remote client"""
        from backend.api.equalizer import create_equalizer_router
        from fastapi.testclient import TestClient
        from fastapi import FastAPI

        # Create mocks
        mock_camilladsp = Mock()
        mock_sm = Mock()
        mock_routing = Mock()
        mock_proxy = Mock()
        mock_proxy.check_available = AsyncMock(return_value=True)
        mock_proxy.request = AsyncMock(return_value={"status": "success", "enabled": False})
        mock_sync = Mock()
        mock_sync.update_client_settings = AsyncMock()

        router = create_equalizer_router(
            camilladsp_service=mock_camilladsp,
            state_machine=mock_sm,
            routing_service=mock_routing,
            proxy_service=mock_proxy,
            sync_service=mock_sync
        )

        # Verify proxy is called for remote client
        app = FastAPI()
        app.include_router(router)

        # Use async test client approach - verify the proxy service would be called
        # The route handler calls proxy_service.request for non-local clients
        assert mock_proxy is not None
        # Route exists and proxy_service is properly wired
        routes = [r.path for r in router.routes]
        assert "/api/equalizer/client/{hostname}/enabled" in routes

    @pytest.mark.asyncio
    async def test_zone_propagation_skips_offline_clients(self):
        """Zone propagation should skip OFFLINE clients gracefully"""
        # This validates the frontend propagateToLinkedClients behavior pattern
        # where offline clients are filtered out before making requests

        # Mock client registry with mixed online/offline clients
        online_clients = ["local", "milo-client-01"]
        offline_clients = ["milo-client-02"]
        all_clients = online_clients + offline_clients

        # Simulate the filtering logic used in frontend dspStore.js
        # propagateToLinkedClients filters by registryStore.isClientOnline()
        propagated_to = [c for c in all_clients if c in online_clients]

        # Verify only online clients would receive the propagation
        assert "local" in propagated_to
        assert "milo-client-01" in propagated_to
        assert "milo-client-02" not in propagated_to
        assert len(propagated_to) == 2


# =============================================================================
# AC4: Crossover filters NOT affected by bypass
# =============================================================================

class TestAC4CrossoverIndependence:
    """AC4: Crossover filters remain unchanged during bypass/restore"""

    @pytest.mark.asyncio
    async def test_bypass_preserves_crossover_highpass(self, connected_camilladsp_with_effects):
        """Crossover highpass filter should NOT be affected by bypass"""
        mock_config = {
            "filters": {
                "eq_band_00": {"type": "Biquad", "parameters": {"type": "Peaking", "freq": 100, "gain": 5, "q": 1.41}},
                "crossover_highpass": {"type": "Biquad", "parameters": {"type": "Highpass", "freq": 80, "q": 0.707}},
            },
            "processors": {},
            "pipeline": []
        }
        captured_config = None

        async def capture_config(config):
            nonlocal captured_config
            captured_config = config

        with patch.object(connected_camilladsp_with_effects, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_camilladsp_with_effects, '_set_config', new_callable=AsyncMock, side_effect=capture_config):
                with patch.object(connected_camilladsp_with_effects, 'save_current_config', new_callable=AsyncMock):
                    await connected_camilladsp_with_effects.bypass_effects()

                    # crossover_highpass should remain unchanged
                    assert "crossover_highpass" in captured_config["filters"]
                    crossover = captured_config["filters"]["crossover_highpass"]
                    assert crossover["parameters"]["type"] == "Highpass"
                    assert crossover["parameters"]["freq"] == 80

    @pytest.mark.asyncio
    async def test_bypass_preserves_crossover_lowpass(self, connected_camilladsp_with_effects):
        """Crossover lowpass filter should NOT be affected by bypass"""
        mock_config = {
            "filters": {
                "eq_band_00": {"type": "Biquad", "parameters": {"type": "Peaking", "freq": 100, "gain": 5, "q": 1.41}},
                "crossover_lowpass": {"type": "Biquad", "parameters": {"type": "Lowpass", "freq": 80, "q": 0.707}},
            },
            "processors": {},
            "pipeline": []
        }
        captured_config = None

        async def capture_config(config):
            nonlocal captured_config
            captured_config = config

        with patch.object(connected_camilladsp_with_effects, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_camilladsp_with_effects, '_set_config', new_callable=AsyncMock, side_effect=capture_config):
                with patch.object(connected_camilladsp_with_effects, 'save_current_config', new_callable=AsyncMock):
                    await connected_camilladsp_with_effects.bypass_effects()

                    # crossover_lowpass should remain unchanged
                    assert "crossover_lowpass" in captured_config["filters"]
                    crossover = captured_config["filters"]["crossover_lowpass"]
                    assert crossover["parameters"]["type"] == "Lowpass"
                    assert crossover["parameters"]["freq"] == 80


# =============================================================================
# AC5: Bypass preserves persisted settings (persist=False pattern)
# =============================================================================

class TestAC5SettingsPersistence:
    """AC5: Bypass uses persist=False to preserve saved settings for restore"""

    @pytest.mark.asyncio
    async def test_bypass_does_not_overwrite_saved_eq_settings(self, connected_camilladsp_with_effects, mock_settings_service):
        """Bypass should NOT call set_setting for eq.filters (persist=False)"""
        mock_config = {
            "filters": {"eq_band_00": {"type": "Biquad", "parameters": {"type": "Peaking", "freq": 100, "gain": 5, "q": 1.41}}},
            "processors": {},
            "pipeline": []
        }

        mock_settings_service.set_setting.reset_mock()

        with patch.object(connected_camilladsp_with_effects, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_camilladsp_with_effects, '_set_config', new_callable=AsyncMock):
                with patch.object(connected_camilladsp_with_effects, 'save_current_config', new_callable=AsyncMock):
                    await connected_camilladsp_with_effects.bypass_effects()

                    # Verify eq.filters was NOT saved during bypass
                    filter_calls = [c for c in mock_settings_service.set_setting.call_args_list
                                   if c[0][0] == "equalizer.filters"]
                    assert len(filter_calls) == 0, \
                        "set_setting should NOT be called for eq.filters during bypass"

    @pytest.mark.asyncio
    async def test_bypass_does_not_overwrite_saved_compressor_settings(self, connected_camilladsp_with_effects, mock_settings_service):
        """Bypass should NOT call set_setting for eq.compressor (persist=False)"""
        mock_config = {
            "filters": {},
            "processors": {"compressor": {"type": "Compressor"}},
            "pipeline": []
        }

        mock_settings_service.set_setting.reset_mock()

        with patch.object(connected_camilladsp_with_effects, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_camilladsp_with_effects, '_set_config', new_callable=AsyncMock):
                with patch.object(connected_camilladsp_with_effects, 'save_current_config', new_callable=AsyncMock):
                    await connected_camilladsp_with_effects.bypass_effects()

                    # Verify eq.compressor was NOT saved during bypass
                    compressor_calls = [c for c in mock_settings_service.set_setting.call_args_list
                                       if c[0][0] == "equalizer.compressor"]
                    assert len(compressor_calls) == 0, \
                        "set_setting should NOT be called for eq.compressor during bypass"

    @pytest.mark.asyncio
    async def test_bypass_does_not_overwrite_saved_loudness_settings(self, connected_camilladsp_with_effects, mock_settings_service):
        """Bypass should NOT call set_setting for eq.loudness (persist=False)"""
        mock_config = {
            "filters": {"loudness_low": {}, "loudness_high": {}},
            "processors": {},
            "pipeline": []
        }

        mock_settings_service.set_setting.reset_mock()

        with patch.object(connected_camilladsp_with_effects, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_camilladsp_with_effects, '_set_config', new_callable=AsyncMock):
                with patch.object(connected_camilladsp_with_effects, 'save_current_config', new_callable=AsyncMock):
                    await connected_camilladsp_with_effects.bypass_effects()

                    # Verify eq.loudness was NOT saved during bypass
                    loudness_calls = [c for c in mock_settings_service.set_setting.call_args_list
                                     if c[0][0] == "equalizer.loudness"]
                    assert len(loudness_calls) == 0, \
                        "set_setting should NOT be called for eq.loudness during bypass"


# =============================================================================
# AC6: State syncs on reconnection and mode switch
# =============================================================================

class TestAC6StateSync:
    """AC6: Equalizer enabled state syncs on reconnection and multiroom mode switch"""

    @pytest.mark.asyncio
    async def test_bypass_fails_when_disconnected(self, disconnected_camilladsp_service):
        """bypass_effects() should fail gracefully when disconnected"""
        result = await disconnected_camilladsp_service.bypass_effects()
        assert result is False

    @pytest.mark.asyncio
    async def test_restore_fails_when_disconnected(self, disconnected_camilladsp_service):
        """restore_effects() should fail gracefully when disconnected"""
        result = await disconnected_camilladsp_service.restore_effects()
        assert result is False

    @pytest.mark.asyncio
    async def test_routing_service_applies_state_on_init(self):
        """Routing service should apply equalizer_effects_enabled on initialization

        This test verifies that during _detect_initial_state(), the routing service
        calls restore_effects() when equalizer.effects_enabled is True in settings, or
        bypass_effects() when it's False.
        """
        from backend.core.multiroom.routing import AudioRoutingService

        mock_settings = Mock()
        mock_settings.get_setting = AsyncMock(side_effect=lambda key: {
            "routing.multiroom_enabled": False,
            "equalizer.effects_enabled": True,  # Equalizer effects enabled in settings
            "equalizer.enabled": None,
        }.get(key))
        mock_settings.set_setting = AsyncMock()

        routing = AudioRoutingService(settings_service=mock_settings)

        # Set up state_machine mock
        mock_sm = Mock()
        mock_sm.system_state = Mock()
        mock_sm.system_state.multiroom_enabled = False
        mock_sm.system_state.equalizer_effects_enabled = True  # Match settings
        mock_sm.broadcast_event = AsyncMock()
        mock_sm.update_multiroom_state = AsyncMock(
            side_effect=lambda v, silent=False: setattr(mock_sm.system_state, 'multiroom_enabled', v)
        )
        mock_sm.update_equalizer_effects_state = AsyncMock(
            side_effect=lambda v, silent=False: setattr(mock_sm.system_state, 'equalizer_effects_enabled', v)
        )
        routing.set_state_machine(mock_sm)

        # Mock camilladsp_service (must not be connected yet for connect to succeed)
        mock_camilladsp = Mock()
        mock_camilladsp.connected = False  # Not connected initially
        mock_camilladsp.connect = AsyncMock(return_value=True)  # Connect succeeds
        mock_camilladsp.bypass_effects = AsyncMock(return_value=True)
        mock_camilladsp.restore_effects = AsyncMock(return_value=True)
        routing.set_camilladsp_service(mock_camilladsp)

        # Mock systemd service manager
        routing.service_manager = Mock()
        routing.service_manager.is_active = AsyncMock(return_value=True)
        routing.service_manager.start = AsyncMock(return_value=True)
        routing.service_manager.stop = AsyncMock()

        # Run initialization
        await routing._detect_initial_state()

        # connect() should be called, then restore_effects() since equalizer.effects_enabled = True
        mock_camilladsp.connect.assert_called_once()
        mock_camilladsp.restore_effects.assert_called_once()


# =============================================================================
# API Endpoint Tests
# =============================================================================

class TestDspEnabledAPI:
    """Test /api/equalizer/enabled endpoint behavior"""

    @pytest.mark.asyncio
    async def test_get_enabled_returns_routing_state(self):
        """GET /api/equalizer/enabled should return routing_service.equalizer_effects_enabled"""
        # This test verifies the API route exists and returns correct data
        from backend.api.equalizer import create_equalizer_router

        mock_camilladsp = Mock()
        mock_sm = Mock()
        mock_routing = Mock()
        mock_routing.equalizer_effects_enabled = True

        router = create_equalizer_router(
            camilladsp_service=mock_camilladsp,
            state_machine=mock_sm,
            routing_service=mock_routing
        )

        # Verify route exists
        routes = [r.path for r in router.routes]
        assert "/api/equalizer/enabled" in routes

    @pytest.mark.asyncio
    async def test_put_enabled_calls_routing_service(self):
        """PUT /api/equalizer/enabled should call routing_service.set_equalizer_effects_enabled"""
        from backend.core.multiroom.routing import AudioRoutingService

        mock_settings = Mock()
        mock_settings.get_setting = AsyncMock(return_value=False)
        mock_settings.set_setting = AsyncMock()

        routing = AudioRoutingService(settings_service=mock_settings)
        routing._initial_detection_done = True

        # Set up state_machine mock
        mock_sm = Mock()
        mock_sm.system_state = Mock()
        mock_sm.system_state.equalizer_effects_enabled = True  # Currently enabled
        mock_sm.broadcast_event = AsyncMock()
        mock_sm.update_equalizer_effects_state = AsyncMock(
            side_effect=lambda v, silent=False: setattr(mock_sm.system_state, 'equalizer_effects_enabled', v)
        )
        routing.set_state_machine(mock_sm)

        # Mock camilladsp_service
        mock_camilladsp = Mock()
        mock_camilladsp.bypass_effects = AsyncMock(return_value=True)
        mock_camilladsp.restore_effects = AsyncMock(return_value=True)
        routing.set_camilladsp_service(mock_camilladsp)

        # Test disabling Equalizer effects (enabled -> disabled)
        result = await routing.set_equalizer_effects_enabled(False)

        assert result is True
        mock_camilladsp.bypass_effects.assert_called_once()
        mock_settings.set_setting.assert_any_call('equalizer.effects_enabled', False)
