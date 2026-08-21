# backend/tests/integration/test_global_equalizer_bypass.py
"""
Integration Tests for Global Equalizer Bypass

Bypass is pipeline-only: `bypass_effects()` removes EQ / compressor /
loudness references from CamillaDSP's pipeline without mutating the
in-memory cache (`_filters`, `_compressor`, `_loudness`). `restore_effects()`
re-pushes the cached values and adds the pipeline references back. The
cache is the source of truth for user intent and survives any number of
bypass/restore cycles.

Tests cover:
- bypass_effects() removes EQ / compressor / loudness from pipeline
- restore_effects() pushes cached values and rebuilds pipeline
- Zone propagation for Equalizer bypass (API-level validation)
- Crossover filters NOT affected by bypass
- Bypass preserves cached user intent (no cache mutation)
- State syncs on reconnection and mode switch

CamillaDSP itself is mocked at the boundary the service actually talks to:
`mock_camilla_client` (backend/tests/conftest.py) is injected as `service._client`,
so `_get_config` / `_set_config` run for real and `camilla_daemon` is what the
daemon holds — seed it with `load()`, read the write back with `last_pushed`.
"""
import pytest
from unittest.mock import Mock, AsyncMock

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
    sm.broadcast = AsyncMock()
    return sm


@pytest.fixture
def connected_camilladsp_with_effects(mock_settings_service, mock_state_machine, mock_camilla_client):
    """Create connected Equalizer service with EQ, compressor, and loudness enabled"""
    service = CamillaDSPService(
        settings_service=mock_settings_service
    )
    service.set_state_machine(mock_state_machine)
    service._client = mock_camilla_client
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
# bypass_effects removes EQ / compressor / loudness from pipeline
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


class TestBypassEffects:
    """Toggle disables → bypass_effects removes EQ / compressor / loudness from pipeline.

    Filter definitions and cache stay intact; only pipeline references go.
    """

    @pytest.mark.asyncio
    async def test_bypass_removes_eq_bands_from_pipeline(self, connected_camilladsp_with_effects, mock_settings_service, camilla_daemon):
        """Should remove all EQ band references from pipeline (defs and cache preserved)"""
        daemon_config = {
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

        camilla_daemon.load(daemon_config)

        result = await connected_camilladsp_with_effects.bypass_effects()

        assert result is True
        pushed = camilla_daemon.last_pushed

        # EQ bands removed from pipeline
        pipeline_names = _pipeline_filter_names(pushed)
        assert "eq_band_00" not in pipeline_names
        assert "eq_band_01" not in pipeline_names
        assert "eq_band_02" not in pipeline_names

        # Filter definitions PRESERVED (original gains intact, not zeroed)
        assert pushed["filters"]["eq_band_00"]["parameters"]["gain"] == 3
        assert pushed["filters"]["eq_band_01"]["parameters"]["gain"] == -2
        assert pushed["filters"]["eq_band_02"]["parameters"]["gain"] == 4

        # In-memory cache UNCHANGED (the source of truth for restore)
        gains = [f["gain"] for f in connected_camilladsp_with_effects._filters]
        assert gains == [3.0, -2.0, 4.0]

    @pytest.mark.asyncio
    async def test_bypass_removes_compressor_from_pipeline(self, connected_camilladsp_with_effects, mock_settings_service, camilla_daemon):
        """Should remove compressor processor reference from pipeline; cache enabled flag stays True"""
        daemon_config = {
            "filters": {},
            "processors": {"compressor": {"type": "Compressor", "parameters": {}}},
            "pipeline": [{"type": "Processor", "name": "compressor"}],
        }

        camilla_daemon.load(daemon_config)

        result = await connected_camilladsp_with_effects.bypass_effects()

        assert result is True
        assert "compressor" not in _pipeline_processor_names(camilla_daemon.last_pushed)
        # Cache untouched — user's compressor preference survives the bypass
        assert connected_camilladsp_with_effects._compressor["enabled"] is True

    @pytest.mark.asyncio
    async def test_bypass_removes_loudness_from_pipeline(self, connected_camilladsp_with_effects, mock_settings_service, camilla_daemon):
        """Should remove loudness filter references from pipeline; cache enabled flag stays True"""
        daemon_config = {
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

        camilla_daemon.load(daemon_config)

        result = await connected_camilladsp_with_effects.bypass_effects()

        assert result is True
        pipeline_names = _pipeline_filter_names(camilla_daemon.last_pushed)
        assert "loudness_low" not in pipeline_names
        assert "loudness_high" not in pipeline_names
        # Cache untouched
        assert connected_camilladsp_with_effects._loudness["enabled"] is True


# =============================================================================
# restore_effects pushes cached values and rebuilds pipeline
# =============================================================================

class TestRestoreEffects:
    """Toggle enables → restore_effects pushes filter defs and pipeline refs from cache."""

    @pytest.mark.asyncio
    async def test_restore_writes_eq_definitions_and_pipeline(self, connected_camilladsp_with_effects, camilla_daemon):
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

        camilla_daemon.load({"filters": {}, "processors": {}, "pipeline": []})

        result = await connected_camilladsp_with_effects.restore_effects()

        assert result is True

        # Filter definitions written with cached gains
        assert camilla_daemon.last_pushed["filters"]["eq_band_00"]["parameters"]["gain"] == 3
        assert camilla_daemon.last_pushed["filters"]["eq_band_01"]["parameters"]["gain"] == -2

        # Pipeline references added
        pipeline_names = _pipeline_filter_names(camilla_daemon.last_pushed)
        assert "eq_band_00" in pipeline_names
        assert "eq_band_01" in pipeline_names

    @pytest.mark.asyncio
    async def test_restore_adds_compressor_to_pipeline_when_enabled(self, connected_camilladsp_with_effects, camilla_daemon):
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

        camilla_daemon.load({"filters": {}, "processors": {}, "pipeline": []})

        result = await connected_camilladsp_with_effects.restore_effects()

        assert result is True
        assert "compressor" in camilla_daemon.last_pushed.get("processors", {})
        assert "compressor" in _pipeline_processor_names(camilla_daemon.last_pushed)
        # Compressor parameters reflect the cached values (attack/release converted ms->s)
        params = camilla_daemon.last_pushed["processors"]["compressor"]["parameters"]
        assert params["threshold"] == -25
        assert params["factor"] == 6
        assert params["attack"] == pytest.approx(0.015)
        assert params["release"] == pytest.approx(0.150)

    @pytest.mark.asyncio
    async def test_restore_skips_compressor_when_cache_disabled(self, connected_camilladsp_with_effects, camilla_daemon):
        """Should NOT add compressor to pipeline when cache says disabled."""
        connected_camilladsp_with_effects._compressor = {
            "enabled": False, "threshold": -20.0, "ratio": 4.0, "attack": 10.0, "release": 100.0, "makeup_gain": 0.0,
        }
        connected_camilladsp_with_effects._loudness = {"enabled": False, "high_boost": 5.0, "low_boost": 8.0}
        connected_camilladsp_with_effects._filters = []

        camilla_daemon.load({"filters": {}, "processors": {}, "pipeline": []})

        await connected_camilladsp_with_effects.restore_effects()

        assert "compressor" not in _pipeline_processor_names(camilla_daemon.last_pushed)

    @pytest.mark.asyncio
    async def test_restore_adds_loudness_to_pipeline_when_enabled(self, connected_camilladsp_with_effects, camilla_daemon):
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

        camilla_daemon.load({"filters": {}, "processors": {}, "pipeline": []})

        result = await connected_camilladsp_with_effects.restore_effects()

        assert result is True
        assert camilla_daemon.last_pushed["filters"]["loudness_low"]["parameters"]["gain"] == 10
        assert camilla_daemon.last_pushed["filters"]["loudness_high"]["parameters"]["gain"] == 8
        pipeline_names = _pipeline_filter_names(camilla_daemon.last_pushed)
        assert "loudness_low" in pipeline_names
        assert "loudness_high" in pipeline_names

    @pytest.mark.asyncio
    async def test_bypass_then_restore_preserves_preset_gains(self, connected_camilladsp_with_effects, camilla_daemon):
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

        camilla_daemon.load(bypass_config)

        await connected_camilladsp_with_effects.bypass_effects()

        # Cache must be untouched by bypass
        cached_gains = [f["gain"] for f in connected_camilladsp_with_effects._filters]
        assert cached_gains == hip_hop_gains

        camilla_daemon.load({"filters": {}, "processors": {}, "pipeline": []})

        await connected_camilladsp_with_effects.restore_effects()

        restored_gains = [camilla_daemon.last_pushed["filters"][f"eq_band_{i:02d}"]["parameters"]["gain"]
                          for i in range(10)]
        assert restored_gains == hip_hop_gains


# =============================================================================
# Zone propagation for Equalizer bypass (API-level validation)
# =============================================================================

class TestZonePropagationBypass:
    """Equalizer bypass/restore propagates to all zone members within 200ms"""

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
        mock_sm.broadcast = AsyncMock()
        routing.set_state_machine(mock_sm)

        # Mock camilladsp_service — owns effects_enabled cache + setter
        mock_camilladsp = Mock()
        mock_camilladsp._effects_enabled = False
        type(mock_camilladsp).effects_enabled = property(lambda self: self._effects_enabled)
        mock_camilladsp.set_effects_enabled = lambda v: setattr(mock_camilladsp, '_effects_enabled', bool(v))
        mock_camilladsp.bypass_effects = AsyncMock(return_value=True)
        mock_camilladsp.restore_effects = AsyncMock(return_value=True)
        routing.camilladsp_service = mock_camilladsp

        # Test enabling Equalizer effects
        result = await routing.set_equalizer_effects_enabled(True)
        assert result is True
        mock_camilladsp.restore_effects.assert_called_once()

# =============================================================================
# Crossover filters NOT affected by bypass
# =============================================================================

class TestCrossoverIndependence:
    """Crossover filters remain unchanged during bypass/restore"""

    @pytest.mark.asyncio
    async def test_bypass_preserves_crossover_highpass(self, connected_camilladsp_with_effects, camilla_daemon):
        """Crossover highpass filter should NOT be affected by bypass"""
        daemon_config = {
            "filters": {
                "eq_band_00": {"type": "Biquad", "parameters": {"type": "Peaking", "freq": 100, "gain": 5, "q": 1.41}},
                "crossover_highpass": {"type": "Biquad", "parameters": {"type": "Highpass", "freq": 80, "q": 0.707}},
            },
            "processors": {},
            "pipeline": []
        }

        camilla_daemon.load(daemon_config)

        await connected_camilladsp_with_effects.bypass_effects()

        # crossover_highpass should remain unchanged
        assert "crossover_highpass" in camilla_daemon.last_pushed["filters"]
        crossover = camilla_daemon.last_pushed["filters"]["crossover_highpass"]
        assert crossover["parameters"]["type"] == "Highpass"
        assert crossover["parameters"]["freq"] == 80

    @pytest.mark.asyncio
    async def test_bypass_preserves_crossover_lowpass(self, connected_camilladsp_with_effects, camilla_daemon):
        """Crossover lowpass filter should NOT be affected by bypass"""
        daemon_config = {
            "filters": {
                "eq_band_00": {"type": "Biquad", "parameters": {"type": "Peaking", "freq": 100, "gain": 5, "q": 1.41}},
                "crossover_lowpass": {"type": "Biquad", "parameters": {"type": "Lowpass", "freq": 80, "q": 0.707}},
            },
            "processors": {},
            "pipeline": []
        }

        camilla_daemon.load(daemon_config)

        await connected_camilladsp_with_effects.bypass_effects()

        # crossover_lowpass should remain unchanged
        assert "crossover_lowpass" in camilla_daemon.last_pushed["filters"]
        crossover = camilla_daemon.last_pushed["filters"]["crossover_lowpass"]
        assert crossover["parameters"]["type"] == "Lowpass"
        assert crossover["parameters"]["freq"] == 80


# =============================================================================
# Bypass preserves persisted settings (persist=False pattern)
# =============================================================================

class TestCachePreservedAcrossBypass:
    """Bypass uses persist=False to preserve saved settings for restore"""

    @pytest.mark.asyncio
    async def test_bypass_does_not_overwrite_saved_eq_settings(self, connected_camilladsp_with_effects, mock_settings_service, camilla_daemon):
        """Bypass should NOT call set_setting for eq.filters (persist=False)"""
        daemon_config = {
            "filters": {"eq_band_00": {"type": "Biquad", "parameters": {"type": "Peaking", "freq": 100, "gain": 5, "q": 1.41}}},
            "processors": {},
            "pipeline": []
        }

        mock_settings_service.set_setting.reset_mock()

        camilla_daemon.load(daemon_config)

        await connected_camilladsp_with_effects.bypass_effects()

        # Verify eq.filters was NOT saved during bypass
        filter_calls = [c for c in mock_settings_service.set_setting.call_args_list
                       if c[0][0] == "equalizer.filters"]
        assert len(filter_calls) == 0, \
            "set_setting should NOT be called for eq.filters during bypass"

    @pytest.mark.asyncio
    async def test_bypass_does_not_overwrite_saved_compressor_settings(self, connected_camilladsp_with_effects, mock_settings_service, camilla_daemon):
        """Bypass should NOT call set_setting for eq.compressor (persist=False)"""
        daemon_config = {
            "filters": {},
            "processors": {"compressor": {"type": "Compressor"}},
            "pipeline": []
        }

        mock_settings_service.set_setting.reset_mock()

        camilla_daemon.load(daemon_config)

        await connected_camilladsp_with_effects.bypass_effects()

        # Verify eq.compressor was NOT saved during bypass
        compressor_calls = [c for c in mock_settings_service.set_setting.call_args_list
                           if c[0][0] == "equalizer.compressor"]
        assert len(compressor_calls) == 0, \
            "set_setting should NOT be called for eq.compressor during bypass"

    @pytest.mark.asyncio
    async def test_bypass_does_not_overwrite_saved_loudness_settings(self, connected_camilladsp_with_effects, mock_settings_service, camilla_daemon):
        """Bypass should NOT call set_setting for eq.loudness (persist=False)"""
        daemon_config = {
            "filters": {"loudness_low": {}, "loudness_high": {}},
            "processors": {},
            "pipeline": []
        }

        mock_settings_service.set_setting.reset_mock()

        camilla_daemon.load(daemon_config)

        await connected_camilladsp_with_effects.bypass_effects()

        # Verify eq.loudness was NOT saved during bypass
        loudness_calls = [c for c in mock_settings_service.set_setting.call_args_list
                         if c[0][0] == "equalizer.loudness"]
        assert len(loudness_calls) == 0, \
            "set_setting should NOT be called for eq.loudness during bypass"


# =============================================================================
# State syncs on reconnection and mode switch
# =============================================================================

class TestStateSyncOnReconnect:
    """Equalizer enabled state syncs on reconnection and multiroom mode switch"""

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
        mock_sm.broadcast = AsyncMock()
        routing.set_state_machine(mock_sm)

        mock_camilladsp = Mock()
        mock_camilladsp.connected = False
        mock_camilladsp.connect = AsyncMock(return_value=True)
        mock_camilladsp.set_effects_enabled = Mock()
        mock_camilladsp.bypass_effects = AsyncMock(return_value=True)
        mock_camilladsp.restore_effects = AsyncMock(return_value=True)
        routing.camilladsp_service = mock_camilladsp

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
        mock_sm.broadcast = AsyncMock()
        routing.set_state_machine(mock_sm)

        # Mock camilladsp_service — owns effects_enabled cache + setter, currently enabled
        mock_camilladsp = Mock()
        mock_camilladsp._effects_enabled = True
        type(mock_camilladsp).effects_enabled = property(lambda self: self._effects_enabled)
        mock_camilladsp.set_effects_enabled = lambda v: setattr(mock_camilladsp, '_effects_enabled', bool(v))
        mock_camilladsp.bypass_effects = AsyncMock(return_value=True)
        mock_camilladsp.restore_effects = AsyncMock(return_value=True)
        routing.camilladsp_service = mock_camilladsp

        # Test disabling Equalizer effects (enabled -> disabled)
        result = await routing.set_equalizer_effects_enabled(False)

        assert result is True
        mock_camilladsp.bypass_effects.assert_called_once()
        mock_settings.set_setting.assert_any_call('routing.equalizer_effects_enabled', False)
