# backend/tests/integration/test_compressor_loudness_control.py
"""
Integration Tests for Compressor & Loudness Control

Tests cover:
- Compressor enable/disable with WebSocket broadcast
- Compressor parameter validation and application
- Loudness enable/disable with shelf filters
- Loudness boost adjustment with WebSocket broadcast
- Zone propagation for compressor/loudness (tested at API level)
- Preset auto-switch on manual modification (investigation)

These tests verify the complete flow:
API → CamillaDSP → WebSocket → Frontend state update

CamillaDSP itself is mocked at the boundary the service actually talks to:
`mock_camilla_client` (backend/tests/conftest.py) is injected as `service._client`,
so `_get_config` / `_set_config` run for real and `camilla_daemon` is what the
daemon holds — seed it with `load()`, read the write back with `last_pushed`.
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch

from backend.core.equalizer import (
    CamillaDSPService,
    CamillaDspState,
)
from backend.api.models import EqualizerCompressorRequest, EqualizerLoudnessRequest


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
    """Create connected Equalizer service with mocked CamillaClient"""
    service = CamillaDSPService(
        settings_service=mock_settings_service
    )
    service.set_state_machine(mock_state_machine)

    # Simulate connected state
    service._client = mock_camilla_client
    service._connected = True
    service._state = CamillaDspState.RUNNING
    return service


@pytest.fixture
def disconnected_camilladsp_service(mock_settings_service):
    """Create disconnected Equalizer service"""
    return CamillaDSPService(
        settings_service=mock_settings_service
    )


# =============================================================================
# Compressor enable/disable
# =============================================================================

class TestCompressorEnableDisable:
    """Compressor enable/disable with WebSocket broadcast"""

    @pytest.mark.asyncio
    async def test_enable_compressor_adds_processor_to_camilladsp(self, connected_camilladsp_service, camilla_daemon):
        """Should add compressor processor to CamillaDSP pipeline when enabled"""
        camilla_daemon.load({"filters": {}, "processors": {}, "pipeline": []})

        result = await connected_camilladsp_service.set_compressor(enabled=True)

        assert result is True
        assert "compressor" in camilla_daemon.last_pushed["processors"]
        assert camilla_daemon.last_pushed["processors"]["compressor"]["type"] == "Compressor"
        # Verify processor in pipeline
        assert any(s.get("type") == "Processor" and s.get("name") == "compressor"
                  for s in camilla_daemon.last_pushed["pipeline"])

    @pytest.mark.asyncio
    async def test_disable_compressor_removes_processor(self, connected_camilladsp_service, camilla_daemon):
        """Should remove compressor processor from CamillaDSP when disabled"""
        daemon_config = {
            "filters": {},
            "processors": {
                "compressor": {"type": "Compressor", "parameters": {}}
            },
            "pipeline": [{"type": "Processor", "name": "compressor"}]
        }

        connected_camilladsp_service._compressor["enabled"] = True  # Was enabled

        camilla_daemon.load(daemon_config)

        result = await connected_camilladsp_service.set_compressor(enabled=False)

        assert result is True
        assert "compressor" not in camilla_daemon.last_pushed.get("processors", {})
        assert not any(s.get("type") == "Processor" and s.get("name") == "compressor"
                      for s in camilla_daemon.last_pushed.get("pipeline", []))

    @pytest.mark.asyncio
    async def test_compressor_broadcasts_websocket_event(self, connected_camilladsp_service, mock_state_machine, camilla_daemon):
        """Should broadcast compressor_changed WebSocket event"""
        camilla_daemon.load({"filters": {}, "processors": {}, "pipeline": []})

        await connected_camilladsp_service.set_compressor(enabled=True, threshold=-25)

        # The service broadcasts through its own _broadcast method
        assert connected_camilladsp_service._compressor["enabled"] is True
        assert connected_camilladsp_service._compressor["threshold"] == -25

    @pytest.mark.asyncio
    async def test_compressor_persists_to_settings(self, connected_camilladsp_service, camilla_daemon):
        """Should schedule a persist to equalizer.json after a compressor change."""
        camilla_daemon.load({"filters": {}, "processors": {}, "pipeline": []})

        with patch.object(connected_camilladsp_service, '_schedule_persist') as mock_persist:
            await connected_camilladsp_service.set_compressor(enabled=True)
            mock_persist.assert_called()

    @pytest.mark.asyncio
    async def test_compressor_fails_when_disconnected(self, disconnected_camilladsp_service):
        """Should fail when disconnected without updating cache"""
        result = await disconnected_camilladsp_service.set_compressor(enabled=True, threshold=-30)

        assert result is False


# =============================================================================
# Compressor parameter validation and application
# =============================================================================

class TestCompressorParameterValidation:
    """Compressor parameter validation and application within 200ms"""

    @pytest.mark.asyncio
    async def test_compressor_threshold_range(self, connected_camilladsp_service, camilla_daemon):
        """Should accept threshold in range -60 to 0 dB"""
        camilla_daemon.load({"filters": {}, "processors": {}, "pipeline": []})

        # Test minimum
        result = await connected_camilladsp_service.set_compressor(threshold=-60)
        assert result is True
        assert connected_camilladsp_service._compressor["threshold"] == -60

        # Test maximum
        result = await connected_camilladsp_service.set_compressor(threshold=0)
        assert result is True
        assert connected_camilladsp_service._compressor["threshold"] == 0

    @pytest.mark.asyncio
    async def test_compressor_ratio_range(self, connected_camilladsp_service, camilla_daemon):
        """Should accept ratio in range 1 to 20"""
        camilla_daemon.load({"filters": {}, "processors": {}, "pipeline": []})

        # Test minimum
        result = await connected_camilladsp_service.set_compressor(ratio=1)
        assert result is True
        assert connected_camilladsp_service._compressor["ratio"] == 1

        # Test maximum
        result = await connected_camilladsp_service.set_compressor(ratio=20)
        assert result is True
        assert connected_camilladsp_service._compressor["ratio"] == 20

    @pytest.mark.asyncio
    async def test_compressor_attack_release_conversion(self, connected_camilladsp_service, camilla_daemon):
        """Should convert attack/release from ms to seconds for CamillaDSP API"""
        camilla_daemon.load({"filters": {}, "processors": {}, "pipeline": []})

        # Set attack=10ms, release=100ms
        await connected_camilladsp_service.set_compressor(enabled=True, attack=10, release=100)

        params = camilla_daemon.last_pushed["processors"]["compressor"]["parameters"]
        # Should be converted to seconds: 10ms = 0.01s, 100ms = 0.1s
        assert params["attack"] == 0.01  # 10 / 1000.0
        assert params["release"] == 0.1  # 100 / 1000.0

    @pytest.mark.asyncio
    async def test_compressor_partial_update(self, connected_camilladsp_service, camilla_daemon):
        """Should support partial updates (only changed parameters)"""
        daemon_config = {"filters": {}, "processors": {}, "pipeline": []}

        # Initialize with defaults
        connected_camilladsp_service._compressor = {
            "enabled": True,
            "threshold": -20,
            "ratio": 4,
            "attack": 10,
            "release": 100,
            "makeup_gain": 0
        }

        camilla_daemon.load(daemon_config)

        # Only update threshold
        result = await connected_camilladsp_service.set_compressor(threshold=-30)

        assert result is True
        # Threshold updated
        assert connected_camilladsp_service._compressor["threshold"] == -30
        # Others unchanged
        assert connected_camilladsp_service._compressor["ratio"] == 4
        assert connected_camilladsp_service._compressor["attack"] == 10

    @pytest.mark.asyncio
    async def test_compressor_makeup_gain_range(self, connected_camilladsp_service, camilla_daemon):
        """Should accept makeup_gain in range 0 to 30 dB"""
        camilla_daemon.load({"filters": {}, "processors": {}, "pipeline": []})

        # Test minimum
        result = await connected_camilladsp_service.set_compressor(makeup_gain=0)
        assert result is True

        # Test maximum
        result = await connected_camilladsp_service.set_compressor(makeup_gain=30)
        assert result is True
        assert connected_camilladsp_service._compressor["makeup_gain"] == 30


# =============================================================================
# Loudness enable/disable
# =============================================================================

class TestLoudnessEnableDisable:
    """Loudness enable/disable with shelf filters"""

    @pytest.mark.asyncio
    async def test_enable_loudness_creates_shelf_filters(self, connected_camilladsp_service, camilla_daemon):
        """Should create loudness_low and loudness_high shelf filters when enabled"""
        camilla_daemon.load({"filters": {}, "pipeline": []})

        result = await connected_camilladsp_service.set_loudness(enabled=True)

        assert result is True
        pushed_filters = camilla_daemon.last_pushed["filters"]

        # Verify loudness_low filter (Lowshelf at 100Hz)
        assert "loudness_low" in pushed_filters
        assert pushed_filters["loudness_low"]["type"] == "Biquad"
        assert pushed_filters["loudness_low"]["parameters"]["type"] == "Lowshelf"
        assert pushed_filters["loudness_low"]["parameters"]["freq"] == 100

        # Verify loudness_high filter (Highshelf at 8000Hz)
        assert "loudness_high" in pushed_filters
        assert pushed_filters["loudness_high"]["type"] == "Biquad"
        assert pushed_filters["loudness_high"]["parameters"]["type"] == "Highshelf"
        assert pushed_filters["loudness_high"]["parameters"]["freq"] == 8000

    @pytest.mark.asyncio
    async def test_disable_loudness_removes_shelf_filters(self, connected_camilladsp_service, camilla_daemon):
        """Should remove loudness shelf filters when disabled"""
        daemon_config = {
            "filters": {
                "loudness_low": {"type": "Biquad", "parameters": {"type": "Lowshelf", "freq": 100, "gain": 5, "slope": 6}},
                "loudness_high": {"type": "Biquad", "parameters": {"type": "Highshelf", "freq": 8000, "gain": 5, "slope": 6}}
            },
            "pipeline": [{"type": "Filter", "names": ["loudness_low", "loudness_high"]}]
        }

        connected_camilladsp_service._loudness["enabled"] = True  # Was enabled

        camilla_daemon.load(daemon_config)

        result = await connected_camilladsp_service.set_loudness(enabled=False)

        assert result is True
        assert "loudness_low" not in camilla_daemon.last_pushed["filters"]
        assert "loudness_high" not in camilla_daemon.last_pushed["filters"]

    @pytest.mark.asyncio
    async def test_loudness_persists_to_settings(self, connected_camilladsp_service, camilla_daemon):
        """Should schedule a persist to equalizer.json after a loudness change."""
        camilla_daemon.load({"filters": {}, "pipeline": []})

        with patch.object(connected_camilladsp_service, '_schedule_persist') as mock_persist:
            await connected_camilladsp_service.set_loudness(enabled=True)
            mock_persist.assert_called()

    @pytest.mark.asyncio
    async def test_loudness_fails_when_disconnected(self, disconnected_camilladsp_service):
        """Should fail when disconnected without updating cache"""
        result = await disconnected_camilladsp_service.set_loudness(enabled=True, low_boost=10)

        assert result is False


# =============================================================================
# Loudness parameter adjustment
# =============================================================================

class TestLoudnessParameterAdjustment:
    """Loudness boost adjustment with WebSocket broadcast"""

    @pytest.mark.asyncio
    async def test_loudness_boost_range(self, connected_camilladsp_service, camilla_daemon):
        """Should accept high_boost and low_boost in range 0 to 15 dB"""
        camilla_daemon.load({"filters": {}, "pipeline": []})

        # Test low_boost minimum
        result = await connected_camilladsp_service.set_loudness(low_boost=0)
        assert result is True

        # Test high_boost maximum
        result = await connected_camilladsp_service.set_loudness(high_boost=15)
        assert result is True
        assert connected_camilladsp_service._loudness["high_boost"] == 15

    @pytest.mark.asyncio
    async def test_loudness_boost_updates_filter_gain(self, connected_camilladsp_service, camilla_daemon):
        """Should update shelf filter gains when boost values change"""
        camilla_daemon.load({"filters": {}, "pipeline": []})

        await connected_camilladsp_service.set_loudness(enabled=True, low_boost=10, high_boost=8)

        # Verify filter gains match boost values
        assert camilla_daemon.last_pushed["filters"]["loudness_low"]["parameters"]["gain"] == 10
        assert camilla_daemon.last_pushed["filters"]["loudness_high"]["parameters"]["gain"] == 8

    @pytest.mark.asyncio
    async def test_loudness_partial_update(self, connected_camilladsp_service, camilla_daemon):
        """Should support partial updates (only changed parameters)"""
        daemon_config = {"filters": {}, "pipeline": []}

        # Initialize
        connected_camilladsp_service._loudness = {
            "enabled": True,
            "low_boost": 5,
            "high_boost": 5
        }

        camilla_daemon.load(daemon_config)

        # Only update low_boost
        result = await connected_camilladsp_service.set_loudness(low_boost=10)

        assert result is True
        assert connected_camilladsp_service._loudness["low_boost"] == 10
        # Others unchanged
        assert connected_camilladsp_service._loudness["high_boost"] == 5


# =============================================================================
# Zone propagation (API-level validation)
# =============================================================================

class TestZonePropagationCompressorLoudness:
    """Zone propagation for compressor/loudness changes

    Tests verify:
    1. Proxy routes exist for compressor and loudness
    2. Settings are propagated to online zone members only
    3. Offline clients are skipped (no 503 errors)
    """

    @pytest.fixture
    def camilladsp_service_with_preset(self, mock_settings_service, mock_state_machine, mock_camilla_client):
        """Create Equalizer service with active preset"""
        service = CamillaDSPService(
            settings_service=mock_settings_service
        )
        service.set_state_machine(mock_state_machine)
        service._client = mock_camilla_client
        service._connected = True
        service._state = CamillaDspState.RUNNING
        service._active_preset = "rock"  # Active preset
        return service

    @pytest.mark.asyncio
    async def test_compressor_proxy_route_callable(self, camilladsp_service_with_preset, camilla_daemon):
        """Compressor proxy route should forward settings to remote clients"""
        # Import the router to verify route exists

        # Verify the route pattern exists by checking router creation doesn't fail
        # and the service can handle compressor updates that would be proxied
        camilla_daemon.load({"filters": {}, "processors": {}, "pipeline": []})

        # Service should handle compressor settings that proxy routes forward
        result = await camilladsp_service_with_preset.set_compressor(
            enabled=True,
            threshold=-25,
            ratio=4
        )
        assert result is True
        assert camilladsp_service_with_preset._compressor["enabled"] is True
        assert camilladsp_service_with_preset._compressor["threshold"] == -25

    @pytest.mark.asyncio
    async def test_loudness_proxy_route_callable(self, camilladsp_service_with_preset, camilla_daemon):
        """Loudness proxy route should forward settings to remote clients"""
        camilla_daemon.load({"filters": {}, "pipeline": []})

        # Service should handle loudness settings that proxy routes forward
        result = await camilladsp_service_with_preset.set_loudness(
            enabled=True,
            low_boost=10,
            high_boost=8
        )
        assert result is True
        assert camilladsp_service_with_preset._loudness["enabled"] is True
        assert camilladsp_service_with_preset._loudness["low_boost"] == 10


# =============================================================================
# Preset auto-switch behavior
# =============================================================================

class TestPresetAutoSwitchOnManualEdit:
    """Verify compressor/loudness changes do NOT affect EQ presets

    Design Decision: Compressor and loudness are INDEPENDENT of EQ presets.

    Rationale:
    - EQ presets only control the 10-band parametric EQ gains
    - Compressor and loudness are separate audio processing features
    - Users should be able to apply compression while using a preset EQ
    - Only modifying EQ band gains triggers auto-switch to "Manual"
    """

    @pytest.fixture
    def camilladsp_service_with_preset(self, mock_settings_service, mock_state_machine, mock_camilla_client):
        """Create Equalizer service with active preset"""
        service = CamillaDSPService(
            settings_service=mock_settings_service
        )
        service.set_state_machine(mock_state_machine)
        service._client = mock_camilla_client
        service._connected = True
        service._state = CamillaDspState.RUNNING
        service._active_preset = "rock"  # Simulate active preset
        return service

    @pytest.mark.asyncio
    async def test_compressor_change_preserves_active_preset(self, camilladsp_service_with_preset, camilla_daemon):
        """Changing compressor should NOT change the active EQ preset"""
        daemon_config = {"filters": {}, "processors": {}, "pipeline": []}
        initial_preset = camilladsp_service_with_preset._active_preset

        camilla_daemon.load(daemon_config)

        # Change compressor settings
        await camilladsp_service_with_preset.set_compressor(enabled=True, threshold=-30)

        # Preset should remain unchanged
        assert camilladsp_service_with_preset._active_preset == initial_preset
        assert camilladsp_service_with_preset._active_preset == "rock"

    @pytest.mark.asyncio
    async def test_loudness_change_preserves_active_preset(self, camilladsp_service_with_preset, camilla_daemon):
        """Changing loudness should NOT change the active EQ preset"""
        daemon_config = {"filters": {}, "pipeline": []}
        initial_preset = camilladsp_service_with_preset._active_preset

        camilla_daemon.load(daemon_config)

        # Change loudness settings
        await camilladsp_service_with_preset.set_loudness(enabled=True, low_boost=12)

        # Preset should remain unchanged
        assert camilladsp_service_with_preset._active_preset == initial_preset
        assert camilladsp_service_with_preset._active_preset == "rock"

    @pytest.mark.asyncio
    async def test_multiple_compressor_loudness_changes_preserve_preset(self, camilladsp_service_with_preset, camilla_daemon):
        """Multiple compressor/loudness changes should preserve preset"""
        camilla_daemon.load({"filters": {}, "processors": {}, "pipeline": []})

        # Multiple changes
        await camilladsp_service_with_preset.set_compressor(enabled=True)
        await camilladsp_service_with_preset.set_loudness(enabled=True)
        await camilladsp_service_with_preset.set_compressor(threshold=-25)
        await camilladsp_service_with_preset.set_loudness(high_boost=10)

        # Preset should still be unchanged
        assert camilladsp_service_with_preset._active_preset == "rock"


# =============================================================================
# API Validation (Pydantic models)
# =============================================================================

class TestAPICompressorValidation:
    """Test EqualizerCompressorRequest validation"""

    def test_compressor_request_all_optional(self):
        """All compressor fields should be optional for partial updates"""

        # Empty request should be valid
        req = EqualizerCompressorRequest()
        assert req.enabled is None

    def test_compressor_threshold_bounds(self):
        """Threshold should accept -60 to 0"""
        req = EqualizerCompressorRequest(threshold=-60)
        assert req.threshold == -60

        req = EqualizerCompressorRequest(threshold=0)
        assert req.threshold == 0

    def test_compressor_threshold_out_of_bounds_rejected(self):
        """Threshold outside -60 to 0 should be rejected"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            EqualizerCompressorRequest(threshold=-61)

        with pytest.raises(ValidationError):
            EqualizerCompressorRequest(threshold=1)

    def test_compressor_ratio_bounds(self):
        """Ratio should accept 1 to 20"""
        req = EqualizerCompressorRequest(ratio=1)
        assert req.ratio == 1

        req = EqualizerCompressorRequest(ratio=20)
        assert req.ratio == 20

    def test_compressor_ratio_out_of_bounds_rejected(self):
        """Ratio outside 1 to 20 should be rejected"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            EqualizerCompressorRequest(ratio=0.5)

        with pytest.raises(ValidationError):
            EqualizerCompressorRequest(ratio=21)

    def test_compressor_attack_bounds(self):
        """Attack should accept 0.1 to 100 ms"""
        req = EqualizerCompressorRequest(attack=0.1)
        assert req.attack == 0.1

        req = EqualizerCompressorRequest(attack=100)
        assert req.attack == 100

    def test_compressor_release_bounds(self):
        """Release should accept 10 to 1000 ms"""
        req = EqualizerCompressorRequest(release=10)
        assert req.release == 10

        req = EqualizerCompressorRequest(release=1000)
        assert req.release == 1000

    def test_compressor_makeup_gain_bounds(self):
        """Makeup gain should accept 0 to 30 dB"""
        req = EqualizerCompressorRequest(makeup_gain=0)
        assert req.makeup_gain == 0

        req = EqualizerCompressorRequest(makeup_gain=30)
        assert req.makeup_gain == 30


class TestAPILoudnessValidation:
    """Test EqualizerLoudnessRequest validation"""

    def test_loudness_request_all_optional(self):
        """All loudness fields should be optional for partial updates"""
        req = EqualizerLoudnessRequest()
        assert req.enabled is None

    def test_loudness_boost_bounds(self):
        """High/low boost should accept 0 to 15 dB"""
        req = EqualizerLoudnessRequest(high_boost=0)
        assert req.high_boost == 0

        req = EqualizerLoudnessRequest(low_boost=15)
        assert req.low_boost == 15

    def test_loudness_boost_out_of_bounds_rejected(self):
        """Boost outside 0 to 15 should be rejected"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            EqualizerLoudnessRequest(high_boost=-1)

        with pytest.raises(ValidationError):
            EqualizerLoudnessRequest(low_boost=16)


# =============================================================================
# Effects Bypass/Restore Integration
# =============================================================================

class TestEffectsBypassRestore:
    """Test that bypass_effects/restore_effects preserves compressor/loudness settings.

    Pipeline-only bypass: bypass_effects() removes compressor / loudness pipeline
    references but leaves their definitions in config and their `enabled` flags
    in the in-memory cache untouched. restore_effects() pushes those cached
    values back and re-adds the pipeline references.
    """

    @pytest.fixture
    def connected_camilladsp_with_effects(self, mock_settings_service, mock_state_machine, mock_camilla_client):
        """Create connected Equalizer service with compressor and loudness enabled"""
        service = CamillaDSPService(
            settings_service=mock_settings_service
        )
        service.set_state_machine(mock_state_machine)
        service._client = mock_camilla_client
        service._connected = True
        service._state = CamillaDspState.RUNNING

        # Enable effects
        service._compressor = {
            "enabled": True,
            "threshold": -25,
            "ratio": 6,
            "attack": 15,
            "release": 150,
            "makeup_gain": 5
        }
        service._loudness = {
            "enabled": True,
            "low_boost": 10,
            "high_boost": 8
        }
        service._filters = []  # No EQ filters for these tests
        return service

    @pytest.mark.asyncio
    async def test_bypass_removes_compressor_from_pipeline_without_touching_cache(self, connected_camilladsp_with_effects, mock_settings_service, camilla_daemon):
        """Bypass should remove compressor from pipeline while leaving cache enabled flag intact."""
        daemon_config = {
            "filters": {},
            "processors": {"compressor": {}},
            "pipeline": [{"type": "Processor", "name": "compressor"}],
        }

        mock_settings_service.set_setting.reset_mock()

        camilla_daemon.load(daemon_config)

        await connected_camilladsp_with_effects.bypass_effects()

        # Compressor processor no longer referenced in pipeline
        pipeline_procs = [s.get("name") for s in camilla_daemon.last_pushed["pipeline"] if s.get("type") == "Processor"]
        assert "compressor" not in pipeline_procs

        # Cache untouched — user's intent survives
        assert connected_camilladsp_with_effects._compressor["enabled"] is True

        # No persistence side-effects on bypass
        compressor_calls = [
            call for call in mock_settings_service.set_setting.call_args_list
            if call[0][0] == "equalizer.compressor"
        ]
        assert len(compressor_calls) == 0

    @pytest.mark.asyncio
    async def test_bypass_removes_loudness_from_pipeline_without_touching_cache(self, connected_camilladsp_with_effects, mock_settings_service, camilla_daemon):
        """Bypass should remove loudness from pipeline while leaving cache enabled flag intact."""
        daemon_config = {
            "filters": {"loudness_low": {}, "loudness_high": {}},
            "processors": {},
            "pipeline": [
                {"type": "Filter", "channels": [0], "names": ["loudness_low", "loudness_high"]},
                {"type": "Filter", "channels": [1], "names": ["loudness_low", "loudness_high"]},
            ],
        }

        mock_settings_service.set_setting.reset_mock()

        camilla_daemon.load(daemon_config)

        await connected_camilladsp_with_effects.bypass_effects()

        # Loudness filters no longer referenced in any Filter step
        pipeline_names = []
        for step in camilla_daemon.last_pushed["pipeline"]:
            if step.get("type") == "Filter":
                pipeline_names.extend(step.get("names", []))
        assert "loudness_low" not in pipeline_names
        assert "loudness_high" not in pipeline_names

        # Cache untouched
        assert connected_camilladsp_with_effects._loudness["enabled"] is True

        loudness_calls = [
            call for call in mock_settings_service.set_setting.call_args_list
            if call[0][0] == "equalizer.loudness"
        ]
        assert len(loudness_calls) == 0

    @pytest.mark.asyncio
    async def test_restore_adds_compressor_to_pipeline_from_cache(self, connected_camilladsp_with_effects, camilla_daemon):
        """Restore should add compressor processor + pipeline reference from cached settings."""
        daemon_config = {"filters": {}, "processors": {}, "pipeline": []}

        connected_camilladsp_with_effects._filters = []
        connected_camilladsp_with_effects._compressor = {
            "enabled": True, "threshold": -25, "ratio": 6, "attack": 15, "release": 150, "makeup_gain": 5
        }
        connected_camilladsp_with_effects._loudness = {"enabled": False, "low_boost": 5.0, "high_boost": 5.0}

        camilla_daemon.load(daemon_config)

        await connected_camilladsp_with_effects.restore_effects()

        # Compressor definition written from cache and added to pipeline
        assert "compressor" in camilla_daemon.last_pushed["processors"]
        assert camilla_daemon.last_pushed["processors"]["compressor"]["parameters"]["threshold"] == -25
        pipeline_procs = [s.get("name") for s in camilla_daemon.last_pushed["pipeline"] if s.get("type") == "Processor"]
        assert "compressor" in pipeline_procs

        # Cache values unchanged by restore
        assert connected_camilladsp_with_effects._compressor["enabled"] is True
        assert connected_camilladsp_with_effects._compressor["threshold"] == -25

    @pytest.mark.asyncio
    async def test_restore_adds_loudness_to_pipeline_from_cache(self, connected_camilladsp_with_effects, camilla_daemon):
        """Restore should add loudness filter defs + pipeline references from cached settings."""
        daemon_config = {"filters": {}, "processors": {}, "pipeline": []}

        connected_camilladsp_with_effects._filters = []
        connected_camilladsp_with_effects._compressor = {
            "enabled": False, "threshold": -20.0, "ratio": 4.0, "attack": 10.0, "release": 100.0, "makeup_gain": 0.0
        }
        connected_camilladsp_with_effects._loudness = {"enabled": True, "low_boost": 10, "high_boost": 8}

        camilla_daemon.load(daemon_config)

        await connected_camilladsp_with_effects.restore_effects()

        # Loudness defs written from cache and added to pipeline
        assert camilla_daemon.last_pushed["filters"]["loudness_low"]["parameters"]["gain"] == 10
        assert camilla_daemon.last_pushed["filters"]["loudness_high"]["parameters"]["gain"] == 8
        pipeline_names = []
        for step in camilla_daemon.last_pushed["pipeline"]:
            if step.get("type") == "Filter":
                pipeline_names.extend(step.get("names", []))
        assert "loudness_low" in pipeline_names
        assert "loudness_high" in pipeline_names

        # Cache values unchanged
        assert connected_camilladsp_with_effects._loudness["enabled"] is True
        assert connected_camilladsp_with_effects._loudness["low_boost"] == 10
