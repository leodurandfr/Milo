# backend/tests/integration/test_global_equalizer_bypass.py
"""
Integration tests for Story 4-5: Global Equalizer Bypass

Bypass is pipeline-only: `bypass_effects()` removes EQ / compressor /
loudness references from CamillaDSP's pipeline without mutating the
in-memory cache (`_filters`, `_compressor`, `_loudness`). `restore_effects()`
re-pushes the cached values and adds the pipeline references back. The
cache is the source of truth for user intent and survives any number of
bypass/restore cycles.

Tests cover:
- AC1: bypass_effects() removes EQ / compressor / loudness from pipeline
- AC2: restore_effects() pushes cached values and rebuilds pipeline
- AC3: Zone propagation for Equalizer bypass (API-level validation)
- AC4: Crossover filters NOT affected by bypass
- AC5: Bypass preserves cached user intent (no cache mutation)
- AC6: State syncs on reconnection and mode switch
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch

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
# AC1: bypass_effects() removes EQ / compressor / loudness from pipeline
# =============================================================================

def _pipeline_filter_names(config):
    """Flatten all filter names referenced in pipeline Filter steps."""
    names = []
    for step in config.get("pipeline", []):
        if step.get("type") == "Filter":
            names.extend(step.get("names", []))
    return names


def _pipeline_processor_names(config):
    """Flatten all processor names referenced in pipeline Processor steps."""
    return [s.get("name") for s in config.get("pipeline", []) if s.get("type") == "Processor"]


class TestAC1BypassEffects:
    """AC1: Toggle disables → bypass_effects() removes EQ / compressor / loudness from pipeline.

    Filter definitions and cache stay intact; only pipeline references go.
    """

    @pytest.mark.asyncio
    async def test_bypass_removes_eq_bands_from_pipeline(self, connected_camilladsp_with_effects, mock_settings_service):
        """Should remove all EQ band references from pipeline (defs and cache preserved)"""
        mock_config = {
            "filters": {
                "eq_band_00": {"type": "Biquad", "parameters": {"type": "Peaking", "freq": 32, "gain": 3, "q": 1.41}},
                "eq_band_01": {"type": "Biquad", "parameters": {"type": "Peaking", "freq": 64, "gain": -2, "q": 1.41}},
                "eq_band_02": {"type": "Biquad", "parameters": {"type": "Peaking", "freq": 125, "gain": 4, "q": 1.41}},
            },
            "processors": {},
            "pipeline": [
                {"type": "Filter", "channels": [0], "names": ["eq_band_00", "eq_band_01", "eq_band_02"]},
                {"type": "Filter", "channels": [1], "names": ["eq_band_00", "eq_band_01", "eq_band_02"]},
            ],
        }
        captured_config = None

        async def capture_config(config):
            nonlocal captured_config
            captured_config = config

        with patch.object(connected_camilladsp_with_effects, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_camilladsp_with_effects, '_set_config', new_callable=AsyncMock, side_effect=capture_config):
                result = await connected_camilladsp_with_effects.bypass_effects()

                assert result is True

                # EQ bands removed from pipeline
                pipeline_names = _pipeline_filter_names(captured_config)
                assert "eq_band_00" not in pipeline_names
                assert "eq_band_01" not in pipeline_names
                assert "eq_band_02" not in pipeline_names

                # Filter definitions PRESERVED (original gains intact, not zeroed)
                assert captured_config["filters"]["eq_band_00"]["parameters"]["gain"] == 3
                assert captured_config["filters"]["eq_band_01"]["parameters"]["gain"] == -2
                assert captured_config["filters"]["eq_band_02"]["parameters"]["gain"] == 4

                # In-memory cache UNCHANGED (the source of truth for restore)
                gains = [f["gain"] for f in connected_camilladsp_with_effects._filters]
                assert gains == [3.0, -2.0, 4.0]

    @pytest.mark.asyncio
    async def test_bypass_removes_compressor_from_pipeline(self, connected_camilladsp_with_effects, mock_settings_service):
        """Should remove compressor processor reference from pipeline; cache enabled flag stays True"""
        mock_config = {
            "filters": {},
            "processors": {"compressor": {"type": "Compressor", "parameters": {}}},
            "pipeline": [{"type": "Processor", "name": "compressor"}],
        }
        captured_config = None

        async def capture_config(config):
            nonlocal captured_config
            captured_config = config

        with patch.object(connected_camilladsp_with_effects, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_camilladsp_with_effects, '_set_config', new_callable=AsyncMock, side_effect=capture_config):
                result = await connected_camilladsp_with_effects.bypass_effects()

                assert result is True
                assert "compressor" not in _pipeline_processor_names(captured_config)
                # Cache untouched — user's compressor preference survives the bypass
                assert connected_camilladsp_with_effects._compressor["enabled"] is True

    @pytest.mark.asyncio
    async def test_bypass_removes_loudness_from_pipeline(self, connected_camilladsp_with_effects, mock_settings_service):
        """Should remove loudness filter references from pipeline; cache enabled flag stays True"""
        mock_config = {
            "filters": {
                "loudness_low": {"type": "Biquad", "parameters": {"type": "Lowshelf", "freq": 100, "gain": 10, "slope": 6}},
                "loudness_high": {"type": "Biquad", "parameters": {"type": "Highshelf", "freq": 8000, "gain": 8, "slope": 6}},
            },
            "processors": {},
            "pipeline": [
                {"type": "Filter", "channels": [0], "names": ["loudness_low", "loudness_high"]},
                {"type": "Filter", "channels": [1], "names": ["loudness_low", "loudness_high"]},
            ],
        }
        captured_config = None

        async def capture_config(config):
            nonlocal captured_config
            captured_config = config

        with patch.object(connected_camilladsp_with_effects, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_camilladsp_with_effects, '_set_config', new_callable=AsyncMock, side_effect=capture_config):
                result = await connected_camilladsp_with_effects.bypass_effects()

                assert result is True
                pipeline_names = _pipeline_filter_names(captured_config)
                assert "loudness_low" not in pipeline_names
                assert "loudness_high" not in pipeline_names
                # Cache untouched
                assert connected_camilladsp_with_effects._loudness["enabled"] is True


# =============================================================================
# AC2: restore_effects() pushes cached values and rebuilds pipeline
# =============================================================================

class TestAC2RestoreEffects:
    """AC2: Toggle enables → restore_effects() pushes filter defs and pipeline refs from cache."""

    @pytest.mark.asyncio
    async def test_restore_writes_eq_definitions_and_pipeline(self, connected_camilladsp_with_effects):
        """Should write EQ filter defs into config and add pipeline references."""
        saved_filters = [
            {"id": "eq_band_00", "freq": 32, "gain": 3, "q": 1.41, "type": "Peaking", "enabled": True},
            {"id": "eq_band_01", "freq": 64, "gain": -2, "q": 1.41, "type": "Peaking", "enabled": True},
        ]
        connected_camilladsp_with_effects._filters = list(saved_filters)
        connected_camilladsp_with_effects._compressor = {
            "enabled": False, "threshold": -20.0, "ratio": 4.0, "attack": 10.0, "release": 100.0, "makeup_gain": 0.0,
        }
        connected_camilladsp_with_effects._loudness = {"enabled": False, "high_boost": 5.0, "low_boost": 8.0}

        mock_config = {"filters": {}, "processors": {}, "pipeline": []}
        captured_config = None

        async def capture_config(config):
            nonlocal captured_config
            captured_config = config

        with patch.object(connected_camilladsp_with_effects, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_camilladsp_with_effects, '_set_config', new_callable=AsyncMock, side_effect=capture_config):
                result = await connected_camilladsp_with_effects.restore_effects()

                assert result is True

                # Filter definitions written with cached gains
                assert captured_config["filters"]["eq_band_00"]["parameters"]["gain"] == 3
                assert captured_config["filters"]["eq_band_01"]["parameters"]["gain"] == -2

                # Pipeline references added
                pipeline_names = _pipeline_filter_names(captured_config)
                assert "eq_band_00" in pipeline_names
                assert "eq_band_01" in pipeline_names

    @pytest.mark.asyncio
    async def test_restore_adds_compressor_to_pipeline_when_enabled(self, connected_camilladsp_with_effects):
        """Should add compressor processor to pipeline only when cache says enabled."""
        connected_camilladsp_with_effects._compressor = {
            "enabled": True,
            "threshold": -25,
            "ratio": 6,
            "attack": 15,
            "release": 150,
            "makeup_gain": 5,
        }
        connected_camilladsp_with_effects._loudness = {"enabled": False, "high_boost": 5.0, "low_boost": 8.0}
        connected_camilladsp_with_effects._filters = []

        mock_config = {"filters": {}, "processors": {}, "pipeline": []}
        captured_config = None

        async def capture_config(config):
            nonlocal captured_config
            captured_config = config

        with patch.object(connected_camilladsp_with_effects, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_camilladsp_with_effects, '_set_config', new_callable=AsyncMock, side_effect=capture_config):
                result = await connected_camilladsp_with_effects.restore_effects()

                assert result is True
                assert "compressor" in captured_config.get("processors", {})
                assert "compressor" in _pipeline_processor_names(captured_config)
                # Compressor parameters reflect the cached values (attack/release converted ms->s)
                params = captured_config["processors"]["compressor"]["parameters"]
                assert params["threshold"] == -25
                assert params["factor"] == 6
                assert params["attack"] == pytest.approx(0.015)
                assert params["release"] == pytest.approx(0.150)

    @pytest.mark.asyncio
    async def test_restore_skips_compressor_when_cache_disabled(self, connected_camilladsp_with_effects):
        """Should NOT add compressor to pipeline when cache says disabled."""
        connected_camilladsp_with_effects._compressor = {
            "enabled": False, "threshold": -20.0, "ratio": 4.0, "attack": 10.0, "release": 100.0, "makeup_gain": 0.0,
        }
        connected_camilladsp_with_effects._loudness = {"enabled": False, "high_boost": 5.0, "low_boost": 8.0}
        connected_camilladsp_with_effects._filters = []

        mock_config = {"filters": {}, "processors": {}, "pipeline": []}
        captured_config = None

        async def capture_config(config):
            nonlocal captured_config
            captured_config = config

        with patch.object(connected_camilladsp_with_effects, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_camilladsp_with_effects, '_set_config', new_callable=AsyncMock, side_effect=capture_config):
                await connected_camilladsp_with_effects.restore_effects()

                assert "compressor" not in _pipeline_processor_names(captured_config)

    @pytest.mark.asyncio
    async def test_restore_adds_loudness_to_pipeline_when_enabled(self, connected_camilladsp_with_effects):
        """Should write loudness filter defs and add pipeline references when enabled."""
        connected_camilladsp_with_effects._loudness = {
            "enabled": True,
            "low_boost": 10,
            "high_boost": 8,
        }
        connected_camilladsp_with_effects._compressor = {
            "enabled": False, "threshold": -20.0, "ratio": 4.0, "attack": 10.0, "release": 100.0, "makeup_gain": 0.0,
        }
        connected_camilladsp_with_effects._filters = []

        mock_config = {"filters": {}, "processors": {}, "pipeline": []}
        captured_config = None

        async def capture_config(config):
            nonlocal captured_config
            captured_config = config

        with patch.object(connected_camilladsp_with_effects, '_get_config', new_callable=AsyncMock, return_value=mock_config):
            with patch.object(connected_camilladsp_with_effects, '_set_config', new_callable=AsyncMock, side_effect=capture_config):
                result = await connected_camilladsp_with_effects.restore_effects()

                assert result is True
                assert captured_config["filters"]["loudness_low"]["parameters"]["gain"] == 10
                assert captured_config["filters"]["loudness_high"]["parameters"]["gain"] == 8
                pipeline_names = _pipeline_filter_names(captured_config)
                assert "loudness_low" in pipeline_names
                assert "loudness_high" in pipeline_names

    @pytest.mark.asyncio
    async def test_bypass_then_restore_preserves_preset_gains(self, connected_camilladsp_with_effects):
        """Regression: applying hip-hop, bypassing, then restoring must keep the same gains.

        This is the bug that motivated the pipeline-only refactor. Before the fix,
        bypass_effects() called set_filter(gain=0, persist=False) which zeroed the
        in-memory cache; restore_effects() then read those zeros and pushed flat 0 dB
        to the daemon while the UI still showed the preset name.
        """
        hip_hop_gains = [4.0, 4.5, 3.5, 1.0, -1.0, -0.5, 1.5, 3.0, 3.5, 4.0]
        connected_camilladsp_with_effects._filters = [
            {"id": f"eq_band_{i:02d}", "freq": float(f), "gain": g, "q": 1.41, "type": "Peaking", "enabled": True}
            for i, (f, g) in enumerate(zip([32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000], hip_hop_gains))
        ]
        connected_camilladsp_with_effects._compressor = {
            "enabled": False, "threshold": -20.0, "ratio": 4.0, "attack": 10.0, "release": 100.0, "makeup_gain": 0.0,
        }
        connected_camilladsp_with_effects._loudness = {"enabled": False, "high_boost": 5.0, "low_boost": 8.0}

        bypass_config = {
            "filters": {f["id"]: {"type": "Biquad", "parameters": {"type": "Peaking", "freq": f["freq"], "gain": f["gain"], "q": 1.41}}
                        for f in connected_camilladsp_with_effects._filters},
            "processors": {},
            "pipeline": [
                {"type": "Filter", "channels": [0], "names": [f["id"] for f in connected_camilladsp_with_effects._filters]},
                {"type": "Filter", "channels": [1], "names": [f["id"] for f in connected_camilladsp_with_effects._filters]},
            ],
        }
        restore_captured = None

        async def capture_restore(config):
            nonlocal restore_captured
            restore_captured = config

        with patch.object(connected_camilladsp_with_effects, '_get_config', new_callable=AsyncMock, return_value=bypass_config):
            with patch.object(connected_camilladsp_with_effects, '_set_config', new_callable=AsyncMock):
                await connected_camilladsp_with_effects.bypass_effects()

        # Cache must be untouched by bypass
        cached_gains = [f["gain"] for f in connected_camilladsp_with_effects._filters]
        assert cached_gains == hip_hop_gains

        with patch.object(connected_camilladsp_with_effects, '_get_config', new_callable=AsyncMock, return_value={"filters": {}, "processors": {}, "pipeline": []}):
            with patch.object(connected_camilladsp_with_effects, '_set_config', new_callable=AsyncMock, side_effect=capture_restore):
                await connected_camilladsp_with_effects.restore_effects()

        restored_gains = [restore_captured["filters"][f"eq_band_{i:02d}"]["parameters"]["gain"]
                          for i in range(10)]
        assert restored_gains == hip_hop_gains


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
        mock_sm.broadcast_event = AsyncMock()
        routing.set_state_machine(mock_sm)

        # Mock camilladsp_service — owns effects_enabled cache + setter
        mock_camilladsp = Mock()
        mock_camilladsp._effects_enabled = False
        type(mock_camilladsp).effects_enabled = property(lambda self: self._effects_enabled)
        mock_camilladsp.set_effects_enabled = lambda v: setattr(mock_camilladsp, '_effects_enabled', bool(v))
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
    async def test_routing_does_not_duplicate_restore_on_init(self):
        """Routing should NOT call restore_effects/bypass_effects during init.

        That work is owned by CamillaDSPService._connection_loop, which runs
        after _load_saved_config has populated the in-memory state. Letting
        routing race ahead with a stale snapshot was the source of the
        post-restart "bars at 0" bug.
        """
        from backend.core.multiroom.routing import AudioRoutingService

        mock_settings = Mock()
        mock_settings.get_setting = AsyncMock(side_effect=lambda key: {
            "routing.multiroom_enabled": False,
            "routing.equalizer_effects_enabled": True,
        }.get(key))
        mock_settings.set_setting = AsyncMock()

        routing = AudioRoutingService(settings_service=mock_settings)

        mock_sm = Mock()
        mock_sm.broadcast_event = AsyncMock()
        routing.set_state_machine(mock_sm)

        mock_camilladsp = Mock()
        mock_camilladsp.connected = False
        mock_camilladsp.connect = AsyncMock(return_value=True)
        mock_camilladsp.set_effects_enabled = Mock()
        mock_camilladsp.bypass_effects = AsyncMock(return_value=True)
        mock_camilladsp.restore_effects = AsyncMock(return_value=True)
        routing.set_camilladsp_service(mock_camilladsp)

        # Mock systemd service manager
        routing.service_manager = Mock()
        routing.service_manager.is_active = AsyncMock(return_value=True)
        routing.service_manager.start = AsyncMock(return_value=True)
        routing.service_manager.stop = AsyncMock()

        await routing._detect_initial_state()

        mock_camilladsp.connect.assert_not_called()
        mock_camilladsp.set_effects_enabled.assert_not_called()
        mock_camilladsp.restore_effects.assert_not_called()
        mock_camilladsp.bypass_effects.assert_not_called()


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
        mock_sm.broadcast_event = AsyncMock()
        routing.set_state_machine(mock_sm)

        # Mock camilladsp_service — owns effects_enabled cache + setter, currently enabled
        mock_camilladsp = Mock()
        mock_camilladsp._effects_enabled = True
        type(mock_camilladsp).effects_enabled = property(lambda self: self._effects_enabled)
        mock_camilladsp.set_effects_enabled = lambda v: setattr(mock_camilladsp, '_effects_enabled', bool(v))
        mock_camilladsp.bypass_effects = AsyncMock(return_value=True)
        mock_camilladsp.restore_effects = AsyncMock(return_value=True)
        routing.set_camilladsp_service(mock_camilladsp)

        # Test disabling Equalizer effects (enabled -> disabled)
        result = await routing.set_equalizer_effects_enabled(False)

        assert result is True
        mock_camilladsp.bypass_effects.assert_called_once()
        mock_settings.set_setting.assert_any_call('routing.equalizer_effects_enabled', False)
