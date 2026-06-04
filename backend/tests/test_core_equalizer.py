# backend/tests/test_core_equalizer.py
"""
Unit tests for core/equalizer module.

Tests cover:
- CamillaDSPService
- EqualizerClientProxyService
- Presets
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock

from backend.core.equalizer import (
    CamillaDSPService,
    CamillaDspState,
    EqualizerClientProxyService,
    get_builtin_presets,
    get_preset_by_id,
    DEFAULT_CUSTOM_GAINS,
    BUILTIN_PRESETS,
    is_ip_address,
)


# =============================================================================
# Presets Tests
# =============================================================================

class TestPresets:
    """Test preset functions"""

    def test_get_builtin_presets_returns_list(self):
        """Should return list of presets"""
        presets = get_builtin_presets()
        assert isinstance(presets, list)
        assert len(presets) > 0

    def test_builtin_presets_have_required_keys(self):
        """Each preset should have id and gains"""
        for preset in BUILTIN_PRESETS:
            assert "id" in preset
            assert "gains" in preset
            assert len(preset["gains"]) == 10

    def test_get_preset_by_id_found(self):
        """Should find preset by ID"""
        preset = get_preset_by_id("acoustic")
        assert preset is not None
        assert preset["id"] == "acoustic"

    def test_get_preset_by_id_not_found(self):
        """Should return None for unknown preset"""
        preset = get_preset_by_id("nonexistent")
        assert preset is None

    def test_default_custom_gains_flat(self):
        """Default custom gains should be flat (all zeros)"""
        assert DEFAULT_CUSTOM_GAINS == [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]

    def test_preset_gains_within_range(self):
        """All preset gains should be within -15 to +15 dB"""
        for preset in BUILTIN_PRESETS:
            for gain in preset["gains"]:
                assert -15 <= gain <= 15, f"Preset {preset['id']} has out-of-range gain: {gain}"


# =============================================================================
# is_ip_address Tests
# =============================================================================

class TestIsIpAddress:
    """Test IP address detection"""

    def test_ipv4_address(self):
        """Should detect IPv4 addresses"""
        assert is_ip_address("192.168.1.1") is True
        assert is_ip_address("10.0.0.1") is True
        assert is_ip_address("127.0.0.1") is True

    def test_ipv6_address(self):
        """Should detect IPv6 addresses"""
        assert is_ip_address("::1") is True
        assert is_ip_address("2001:db8::1") is True

    def test_hostname_not_ip(self):
        """Should return False for hostnames"""
        assert is_ip_address("milo") is False
        assert is_ip_address("milo-client-1") is False
        assert is_ip_address("localhost") is False


# =============================================================================
# EqualizerClientProxyService Tests
# =============================================================================

class TestEqualizerClientProxyService:
    """Test Equalizer client proxy service"""

    @pytest.fixture
    def proxy_service(self):
        """Create proxy service instance"""
        return EqualizerClientProxyService()

    def test_get_host_with_ip(self, proxy_service):
        """Should return IP address as-is"""
        assert proxy_service._get_host("192.168.1.100") == "192.168.1.100"

    def test_get_host_with_hostname(self, proxy_service):
        """Should add .local suffix to hostname"""
        assert proxy_service._get_host("milo-client-1") == "milo-client-1.local"

    def test_set_routing_service(self, proxy_service):
        """Should set routing service"""
        mock_routing = Mock()
        proxy_service.set_routing_service(mock_routing)
        assert proxy_service.routing_service == mock_routing

    @pytest.mark.asyncio
    async def test_check_available_success(self, proxy_service):
        """Should return True when client is available"""
        with patch("aiohttp.ClientSession") as mock_session:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"equalizer_ready": True})

            mock_context = AsyncMock()
            mock_context.__aenter__.return_value = mock_response

            mock_session_instance = MagicMock()
            mock_session_instance.get.return_value = mock_context
            mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
            mock_session_instance.__aexit__ = AsyncMock()

            mock_session.return_value = mock_session_instance

            result = await proxy_service.check_available("192.168.1.100")
            assert result is True

    @pytest.mark.asyncio
    async def test_check_available_not_ready(self, proxy_service):
        """Should return False when Equalizer not ready"""
        with patch("aiohttp.ClientSession") as mock_session:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"equalizer_ready": False})

            mock_context = AsyncMock()
            mock_context.__aenter__.return_value = mock_response

            mock_session_instance = MagicMock()
            mock_session_instance.get.return_value = mock_context
            mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
            mock_session_instance.__aexit__ = AsyncMock()

            mock_session.return_value = mock_session_instance

            result = await proxy_service.check_available("192.168.1.100")
            assert result is False

    @pytest.mark.asyncio
    async def test_check_available_connection_error(self, proxy_service):
        """Should return False on connection error"""
        with patch("backend.core.equalizer.client_proxy.aiohttp.ClientSession") as mock_session:
            mock_session.side_effect = Exception("Connection refused")

            result = await proxy_service.check_available("192.168.1.100")
            assert result is False


# =============================================================================
# CamillaDSPService Tests
# =============================================================================

class TestCamillaDSPService:
    """Test CamillaDSP service"""

    @pytest.fixture
    def mock_settings_service(self):
        """Create mock settings service"""
        settings = Mock()
        settings.get_setting = AsyncMock(return_value=None)
        settings.set_setting = AsyncMock()
        return settings

    @pytest.fixture
    def camilladsp_service(self, mock_settings_service):
        """Create Equalizer service instance"""
        return CamillaDSPService(
            settings_service=mock_settings_service
        )

    def test_initial_state_disconnected(self, camilladsp_service):
        """Should start in disconnected state"""
        assert camilladsp_service.state == CamillaDspState.DISCONNECTED
        assert camilladsp_service.connected is False

    def test_default_host_port(self, camilladsp_service):
        """Should use default host and port"""
        assert camilladsp_service.host == "127.0.0.1"
        assert camilladsp_service.port == 1234

    def test_custom_host_port(self, mock_settings_service):
        """Should accept custom host and port"""
        service = CamillaDSPService(
            settings_service=mock_settings_service,
            host="192.168.1.100",
            port=5678
        )
        assert service.host == "192.168.1.100"
        assert service.port == 5678

    def test_set_state_machine(self, camilladsp_service):
        """Should set state machine reference"""
        mock_state_machine = Mock()
        camilladsp_service.set_state_machine(mock_state_machine)
        assert camilladsp_service.state_machine == mock_state_machine

    def test_is_volume_control_available_disconnected(self, camilladsp_service):
        """Should return False when disconnected"""
        assert camilladsp_service.is_volume_control_available() is False

    def test_initial_compressor_settings(self, camilladsp_service):
        """Should have default compressor settings"""
        assert camilladsp_service._compressor["enabled"] is False
        assert camilladsp_service._compressor["threshold"] == -20.0
        assert camilladsp_service._compressor["ratio"] == 4.0

    def test_initial_loudness_settings(self, camilladsp_service):
        """Should have default loudness settings"""
        assert camilladsp_service._loudness["enabled"] is False
        assert camilladsp_service._loudness["high_boost"] == 5.0
        assert camilladsp_service._loudness["low_boost"] == 8.0

    def test_initial_volume_settings(self, camilladsp_service):
        """Should have default volume settings"""
        assert camilladsp_service._volume["main"] == 0.0
        assert camilladsp_service._volume["mute"] is False

    @pytest.mark.asyncio
    async def test_get_compressor(self, camilladsp_service):
        """Should return compressor settings copy"""
        compressor = await camilladsp_service.get_compressor()
        assert compressor == camilladsp_service._compressor
        # Modify returned value should not affect internal state
        compressor["enabled"] = True
        assert camilladsp_service._compressor["enabled"] is False

    @pytest.mark.asyncio
    async def test_get_loudness(self, camilladsp_service):
        """Should return loudness settings copy"""
        loudness = await camilladsp_service.get_loudness()
        assert loudness == camilladsp_service._loudness

    @pytest.mark.asyncio
    async def test_get_volume_disconnected(self, camilladsp_service):
        """Should return cached volume when disconnected"""
        volume = await camilladsp_service.get_volume()
        assert volume == {"main": 0.0, "mute": False}

    @pytest.mark.asyncio
    async def test_get_filters_disconnected(self, camilladsp_service):
        """Should return the in-memory default bands when disconnected."""
        filters = await camilladsp_service.get_filters()
        # 10 default flat bands are initialized in __init__ so the API never
        # returns an empty list during the post-restart sync window.
        assert len(filters) == 10
        assert all(f["gain"] == 0.0 for f in filters)
        assert [f["id"] for f in filters] == [f"eq_band_{i:02d}" for i in range(10)]

    @pytest.mark.asyncio
    async def test_set_filter_disconnected(self, camilladsp_service):
        """Should fail when disconnected"""
        result = await camilladsp_service.set_filter("eq_band_00", 100, 0, 1.0)
        assert result is False

    @pytest.mark.asyncio
    async def test_set_volume_disconnected(self, camilladsp_service):
        """Should fail when disconnected"""
        result = await camilladsp_service.set_volume(-20)
        assert result is False

    @pytest.mark.asyncio
    async def test_set_mute_disconnected(self, camilladsp_service):
        """Should fail when disconnected"""
        result = await camilladsp_service.set_mute(True)
        assert result is False

    @pytest.mark.asyncio
    async def test_set_compressor_disconnected(self, camilladsp_service):
        """Should fail when disconnected without updating cache"""
        result = await camilladsp_service.set_compressor(enabled=True, threshold=-30)
        assert result is False

    @pytest.mark.asyncio
    async def test_set_loudness_disconnected(self, camilladsp_service):
        """Should fail when disconnected without updating cache"""
        result = await camilladsp_service.set_loudness(enabled=True, low_boost=10.0)
        assert result is False

    @pytest.mark.asyncio
    async def test_get_status_disconnected(self, camilladsp_service):
        """Should return disconnected status"""
        status = await camilladsp_service.get_status()
        assert status["available"] is False
        assert status["state"] == CamillaDspState.DISCONNECTED.value

    @pytest.mark.asyncio
    async def test_get_levels_disconnected(self, camilladsp_service):
        """Should return unavailable when disconnected"""
        levels = await camilladsp_service.get_levels()
        assert levels["available"] is False

    @pytest.mark.asyncio
    async def test_set_crossover_filter_disconnected(self, camilladsp_service):
        """Should fail when disconnected"""
        result = await camilladsp_service.set_crossover_filter(enabled=True)
        assert result is False

    @pytest.mark.asyncio
    async def test_set_lowpass_filter_disconnected(self, camilladsp_service):
        """Should fail when disconnected"""
        result = await camilladsp_service.set_lowpass_filter(enabled=True)
        assert result is False

    def test_get_presets(self, camilladsp_service):
        """Should return builtin presets"""
        presets = camilladsp_service.get_presets()
        assert len(presets) == len(BUILTIN_PRESETS)

    @pytest.mark.asyncio
    async def test_get_active_preset_none(self, camilladsp_service):
        """Should return None when no preset active"""
        preset = await camilladsp_service.get_active_preset()
        assert preset is None

    @pytest.mark.asyncio
    async def test_get_custom_gains_default(self, camilladsp_service):
        """Should return default gains when none saved"""
        gains = await camilladsp_service.get_custom_gains()
        assert gains == DEFAULT_CUSTOM_GAINS

    @pytest.mark.asyncio
    async def test_load_preset_skips_when_already_active(self, camilladsp_service, mock_settings_service):
        """Should skip loading when already on the same preset"""
        # Setup: preset is already active
        mock_settings_service.get_setting = AsyncMock(return_value="acoustic")

        # Should return True without applying gains
        result = await camilladsp_service.load_preset("acoustic")
        assert result is True
        # set_setting should NOT be called (no change needed)
        mock_settings_service.set_setting.assert_not_called()

    @pytest.mark.asyncio
    async def test_bypass_effects_disconnected(self, camilladsp_service):
        """Should fail when disconnected"""
        result = await camilladsp_service.bypass_effects()
        assert result is False

    @pytest.mark.asyncio
    async def test_restore_effects_disconnected(self, camilladsp_service):
        """Should fail when disconnected"""
        result = await camilladsp_service.restore_effects()
        assert result is False

    @pytest.mark.asyncio
    async def test_wait_for_connection_timeout(self, camilladsp_service):
        """Should timeout when not connected"""
        result = await camilladsp_service.wait_for_connection(timeout=0.1)
        assert result is False


# =============================================================================
# Equalizer State Tests
# =============================================================================

class TestCamillaDspState:
    """Test Equalizer state enum"""

    def test_equalizer_states_exist(self):
        """Should have all expected states"""
        assert CamillaDspState.DISCONNECTED.value == "disconnected"
        assert CamillaDspState.INACTIVE.value == "inactive"
        assert CamillaDspState.RUNNING.value == "running"
        assert CamillaDspState.PAUSED.value == "paused"
