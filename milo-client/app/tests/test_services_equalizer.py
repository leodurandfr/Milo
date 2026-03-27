"""
Unit tests for EqualizerService.
"""
import pytest
from unittest.mock import patch


class TestEqualizerServiceProperties:
    """Test EqualizerService property accessors."""

    def test_connected_property(self, equalizer_service):
        """Should return connection state."""
        assert equalizer_service.connected is True

    def test_available_property(self, equalizer_service):
        """Should return True when CamillaDSP library is available."""
        assert equalizer_service.available is True

    def test_compressor_property(self, equalizer_service):
        """Should return compressor state dict."""
        compressor = equalizer_service.compressor
        assert isinstance(compressor, dict)
        assert "enabled" in compressor
        assert "threshold" in compressor
        assert "ratio" in compressor

    def test_loudness_property(self, equalizer_service):
        """Should return loudness state dict."""
        loudness = equalizer_service.loudness
        assert isinstance(loudness, dict)
        assert "enabled" in loudness
        assert "high_boost" in loudness
        assert "low_boost" in loudness

    def test_delay_property(self, equalizer_service):
        """Should return delay state dict."""
        delay = equalizer_service.delay
        assert isinstance(delay, dict)
        assert "left" in delay
        assert "right" in delay

    def test_crossover_property(self, equalizer_service):
        """Should return crossover state dict."""
        crossover = equalizer_service.crossover
        assert isinstance(crossover, dict)
        assert "enabled" in crossover
        assert "frequency" in crossover
        assert "q" in crossover


class TestEqualizerServiceFilters:
    """Test EqualizerService filter operations."""

    @pytest.mark.asyncio
    async def test_get_filters_returns_list(self, equalizer_service):
        """Should return list of EQ filters."""
        filters = await equalizer_service.get_filters()
        assert isinstance(filters, list)

    @pytest.mark.asyncio
    async def test_get_filters_parses_eq_bands(self, equalizer_service):
        """Should parse eq_band_ filters from config."""
        filters = await equalizer_service.get_filters()
        assert len(filters) == 2
        assert filters[0]["id"] == "eq_band_1"
        assert filters[1]["id"] == "eq_band_2"

    @pytest.mark.asyncio
    async def test_set_filter_updates_gain(self, equalizer_service, mock_camilla_client):
        """Should update filter gain in config."""
        result = await equalizer_service.set_filter("eq_band_1", gain=3.0)
        assert result is True
        mock_camilla_client.config.set_active.assert_called()


class TestEqualizerServiceVolume:
    """Test EqualizerService volume operations."""

    @pytest.mark.asyncio
    async def test_get_volume_returns_dict(self, equalizer_service):
        """Should return volume state dict."""
        volume = await equalizer_service.get_volume()
        assert isinstance(volume, dict)
        assert "main" in volume
        assert "mute" in volume

    @pytest.mark.asyncio
    async def test_set_volume_clamps_value(self, equalizer_service):
        """Should clamp volume between -80 and 0 dB."""
        await equalizer_service.set_volume(-100)
        assert equalizer_service.volume_state["main"] == -80

        await equalizer_service.set_volume(10)
        assert equalizer_service.volume_state["main"] == 0

    @pytest.mark.asyncio
    async def test_set_mute_updates_state(self, equalizer_service, mock_camilla_client):
        """Should update mute state."""
        result = await equalizer_service.set_mute(True)
        assert result is True
        assert equalizer_service.volume_state["mute"] is True
        mock_camilla_client.volume.set_main_mute.assert_called_with(True)


class TestEqualizerServiceCompressor:
    """Test EqualizerService compressor operations."""

    @pytest.mark.asyncio
    async def test_set_compressor_enabled(self, equalizer_service, mock_camilla_client):
        """Should enable compressor and add to pipeline."""
        result = await equalizer_service.set_compressor(enabled=True, threshold=-15.0)
        assert result is True
        assert equalizer_service.compressor["enabled"] is True
        assert equalizer_service.compressor["threshold"] == -15.0

    @pytest.mark.asyncio
    async def test_set_compressor_disabled(self, equalizer_service, mock_camilla_client):
        """Should disable compressor and remove from pipeline."""
        equalizer_service._compressor["enabled"] = True
        result = await equalizer_service.set_compressor(enabled=False)
        assert result is True
        assert equalizer_service.compressor["enabled"] is False


class TestEqualizerServiceLoudness:
    """Test EqualizerService loudness operations."""

    @pytest.mark.asyncio
    async def test_set_loudness_enabled(self, equalizer_service, mock_camilla_client):
        """Should enable loudness and add filters to pipeline."""
        result = await equalizer_service.set_loudness(enabled=True, low_boost=10.0, high_boost=6.0)
        assert result is True
        assert equalizer_service.loudness["enabled"] is True
        assert equalizer_service.loudness["low_boost"] == 10.0
        assert equalizer_service.loudness["high_boost"] == 6.0


class TestEqualizerServiceDelay:
    """Test EqualizerService delay operations."""

    @pytest.mark.asyncio
    async def test_set_delay_clamps_values(self, equalizer_service):
        """Should clamp delay between 0 and 50 ms."""
        await equalizer_service.set_delay(left=-5.0, right=100.0)
        assert equalizer_service.delay["left"] == 0.0
        assert equalizer_service.delay["right"] == 50.0

    @pytest.mark.asyncio
    async def test_set_delay_updates_config(self, equalizer_service, mock_camilla_client):
        """Should update delay in CamillaDSP config."""
        result = await equalizer_service.set_delay(left=10.0, right=5.0)
        assert result is True
        assert equalizer_service.delay["left"] == 10.0
        assert equalizer_service.delay["right"] == 5.0


class TestEqualizerServiceCrossover:
    """Test EqualizerService crossover operations."""

    @pytest.mark.asyncio
    async def test_set_crossover_enabled(self, equalizer_service, mock_camilla_client):
        """Should enable crossover highpass filter."""
        result = await equalizer_service.set_crossover(enabled=True, frequency=100.0, q=0.707)
        assert result is True
        assert equalizer_service.crossover["enabled"] is True
        assert equalizer_service.crossover["frequency"] == 100.0

    @pytest.mark.asyncio
    async def test_set_lowpass_enabled(self, equalizer_service, mock_camilla_client):
        """Should enable lowpass filter for subwoofer."""
        result = await equalizer_service.set_lowpass(enabled=True, frequency=80.0, q=0.707)
        assert result is True
        assert equalizer_service.lowpass["enabled"] is True
        assert equalizer_service.lowpass["frequency"] == 80.0


class TestEqualizerServiceLevels:
    """Test EqualizerService audio level operations."""

    @pytest.mark.asyncio
    async def test_get_levels_returns_peaks(self, equalizer_service):
        """Should return input and output peak levels."""
        levels = await equalizer_service.get_levels()
        assert levels["available"] is True
        assert "input_peak" in levels
        assert "output_peak" in levels


class TestEqualizerServiceConnection:
    """Test EqualizerService connection handling."""

    @pytest.mark.asyncio
    async def test_connect_when_unavailable(self):
        """Should return False when CamillaDSP library unavailable."""
        with patch("services.equalizer.CAMILLADSP_AVAILABLE", False):
            from services.equalizer import EqualizerService
            service = EqualizerService()
            result = await service.connect()
            assert result is False
            assert service.connected is False
