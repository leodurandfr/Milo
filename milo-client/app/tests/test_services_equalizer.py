"""
Unit tests for EqualizerService.
"""
import asyncio
import copy
from pathlib import Path

import pytest
from unittest.mock import MagicMock, Mock, patch


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

    @pytest.mark.asyncio
    async def test_connect_once_skips_when_already_connected(self, equalizer_service):
        """Should return True immediately when already connected."""
        result = await equalizer_service._connect_once()
        assert result is True
        assert equalizer_service.connected is True

    @pytest.mark.asyncio
    async def test_probe_detects_dead_connection(self, equalizer_service, mock_camilla_client):
        """Should mark disconnected when probe fails."""
        mock_camilla_client.general.state.side_effect = IOError("Connection refused")
        await equalizer_service._probe_connection()
        assert equalizer_service.connected is False
        assert equalizer_service._client is None

    @pytest.mark.asyncio
    async def test_probe_keeps_connected_on_success(self, equalizer_service, mock_camilla_client):
        """Should stay connected when probe succeeds."""
        mock_camilla_client.general.state.return_value = "Running"
        await equalizer_service._probe_connection()
        assert equalizer_service.connected is True

    @pytest.mark.asyncio
    async def test_restore_after_reconnect_sets_volume(self, equalizer_service, mock_camilla_client):
        """Should restore cached volume and mute after reconnection."""
        equalizer_service._volume = {"main": -25.0, "mute": False}
        await equalizer_service._restore_after_reconnect()
        mock_camilla_client.volume.set_main_volume.assert_called_with(-25.0)
        mock_camilla_client.volume.set_main_mute.assert_called_with(False)

    @pytest.mark.asyncio
    async def test_exec_reconnects_on_failure(self, mock_camilla_client):
        """Should reconnect and retry when first attempt fails."""
        with patch("services.equalizer.CAMILLADSP_AVAILABLE", True), \
             patch("services.equalizer.CamillaClient", return_value=mock_camilla_client):
            from services.equalizer import EqualizerService
            service = EqualizerService()
            service._client = mock_camilla_client
            service._connected = True

            call_count = 0
            def flaky_call():
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise IOError("Connection lost")
                return "ok"

            result = await service._exec(flaky_call)
            assert result == "ok"
            assert call_count == 2

    @pytest.mark.asyncio
    async def test_stop_connection_loop_cleans_up(self, equalizer_service):
        """Should cancel the background task on stop."""
        equalizer_service.start_connection_loop()
        assert equalizer_service._reconnect_task is not None
        await equalizer_service.stop_connection_loop()
        assert equalizer_service._running is False


class TestEqualizerServiceStatusPayload:
    """get_status must expose mono and master enabled so the backend/frontend
    can render per-target state (the local /status already returns both)."""

    @pytest.mark.asyncio
    async def test_status_includes_mono_and_enabled(self, equalizer_service):
        """Should report mono and equalizer_enabled in the status payload."""
        equalizer_service._mono = True
        equalizer_service._equalizer_enabled = False
        status = await equalizer_service.get_status()
        assert status["mono"] is True
        assert status["equalizer_enabled"] is False


class TestEqualizerServiceMasterBypass:
    """Master EQ enable/disable = pipeline-only bypass mirroring the backend's
    bypass_effects/restore_effects: EQ bands + compressor + loudness leave the
    pipeline but their definitions stay, so restore re-pushes exact values."""

    @pytest.mark.asyncio
    async def test_disable_removes_eq_bands_from_pipeline_keeps_defs(self, equalizer_service, mock_camilla_client):
        """Disabling must strip eq_band_* from the pipeline (not just compressor/loudness)."""
        config = mock_camilla_client.config.active.return_value
        result = await equalizer_service.set_equalizer_enabled(False)
        assert result is True
        assert equalizer_service.equalizer_enabled is False
        names = config["pipeline"][0]["names"]
        assert "eq_band_1" not in names
        assert "eq_band_2" not in names
        # Definitions are preserved for restore
        assert "eq_band_1" in config["filters"]
        assert "eq_band_2" in config["filters"]

    @pytest.mark.asyncio
    async def test_enable_readds_eq_bands_to_pipeline(self, equalizer_service, mock_camilla_client):
        """Re-enabling must re-add the EQ bands to the pipeline."""
        config = mock_camilla_client.config.active.return_value
        await equalizer_service.set_equalizer_enabled(False)
        result = await equalizer_service.set_equalizer_enabled(True)
        assert result is True
        assert equalizer_service.equalizer_enabled is True
        names = config["pipeline"][0]["names"]
        assert "eq_band_1" in names
        assert "eq_band_2" in names

    @pytest.mark.asyncio
    async def test_load_state_derives_enabled_false_when_bands_not_piped(self, equalizer_service, mock_camilla_client):
        """On (re)connect, enabled state must be derived from the persisted config:
        bands defined but absent from the pipeline => bypassed => enabled False."""
        config = mock_camilla_client.config.active.return_value
        config["pipeline"] = [{"type": "Filter", "channels": [0, 1], "names": []}]
        equalizer_service._equalizer_enabled = True
        await equalizer_service._load_state_from_config()
        assert equalizer_service.equalizer_enabled is False


class TestEqualizerServiceFilterTuning:
    """set_filter applies tuning only — filter_type included, pipeline membership excluded.

    A band's presence in the pipeline is owned by set_equalizer_enabled(), on a
    satellite exactly as on the server. If set_filter ever regains that power,
    tuning a band would silently un-bypass a bypassed client.
    """

    @pytest.mark.asyncio
    async def test_set_filter_applies_filter_type(self, equalizer_service, mock_camilla_client):
        """filter_type should set the Biquad band type."""
        config = mock_camilla_client.config.active.return_value
        result = await equalizer_service.set_filter("eq_band_1", gain=2.0, filter_type="Lowshelf")
        assert result is True
        assert config["filters"]["eq_band_1"]["parameters"]["type"] == "Lowshelf"

    @pytest.mark.asyncio
    async def test_set_filter_does_not_repipe_a_bypassed_band(self, equalizer_service, mock_camilla_client):
        """Tuning a band on a bypassed client must apply the gain without restoring the band."""
        config = mock_camilla_client.config.active.return_value
        await equalizer_service.set_equalizer_enabled(False)
        assert "eq_band_1" not in config["pipeline"][0]["names"]

        result = await equalizer_service.set_filter("eq_band_1", gain=4.0)

        assert result is True
        assert config["filters"]["eq_band_1"]["parameters"]["gain"] == 4.0
        assert "eq_band_1" not in config["pipeline"][0]["names"]
        assert equalizer_service.equalizer_enabled is False


class TestEqualizerServiceConfigPersistence:
    """The DSP config is the satellite's only durable state.

    Three ways it used to be lost, none of them visible from the server: a write
    that never landed but answered success, a truncated file after a power cut,
    and one of two concurrent mutations dropped.
    """

    @pytest.mark.asyncio
    async def test_a_persist_failure_is_not_reported_as_success(self, equalizer_service, tmp_path):
        """A setter that could not write must answer False, so the route raises.

        The server has no other way to learn the push did nothing: the satellite
        would come back on its old EQ at the next reboot.
        """
        equalizer_service.config_file = str(tmp_path / "absent-dir" / "config.yml")

        assert await equalizer_service.set_filter("eq_band_1", gain=3.0) is False

    @pytest.mark.asyncio
    async def test_the_live_config_survives_a_write_that_dies(self, equalizer_service, tmp_path):
        """New bytes must reach the live path only through the rename.

        CamillaDSP re-reads this file on the recovery path, so a truncated one is
        a room that stays silent.
        """
        await equalizer_service.set_filter("eq_band_1", gain=1.0)
        persisted = Path(equalizer_service.config_file).read_text()
        assert "eq_band_1" in persisted

        with patch("services.equalizer.os.replace", side_effect=OSError("power cut")):
            result = await equalizer_service.set_filter("eq_band_1", gain=9.0)

        assert result is False
        assert Path(equalizer_service.config_file).read_text() == persisted

    @pytest.mark.asyncio
    async def test_concurrent_setters_do_not_lose_a_mutation(self, tmp_path):
        """Two setters running at once must both reach the config.

        Reachable in production: the server pushes a whole record from a
        background task (the reconnection sync) while a targeted write from the
        UI lands at the same moment. Each setter reads the config, mutates its
        own corner and writes the whole document back, so without the lock the
        slower one overwrites the other with a document read before it existed.

        The client here hands out a snapshot per call, as CamillaDSP does over
        the WebSocket — the shared-dict fixture would hide the interleaving.
        """
        device = {
            "filters": {
                "eq_band_1": {
                    "type": "Biquad",
                    "parameters": {"type": "Peaking", "freq": 100, "gain": 0.0, "q": 1.0},
                },
            },
            "processors": {},
            "pipeline": [{"type": "Filter", "channels": [0, 1], "names": ["eq_band_1"]}],
        }
        client = MagicMock()
        client.config.active.side_effect = lambda: copy.deepcopy(device)
        client.config.set_active = Mock(
            side_effect=lambda cfg: device.update(copy.deepcopy(cfg))
        )

        with patch("services.equalizer.CAMILLADSP_AVAILABLE", True), \
             patch("services.equalizer.CamillaClient", return_value=client):
            from services.equalizer import EqualizerService
            service = EqualizerService(config_file=str(tmp_path / "config.yml"))
            service._client = client
            service._connected = True

            applied = await asyncio.gather(
                service.set_compressor(enabled=True, threshold=-18.0),
                service.set_filter("eq_band_1", gain=6.0),
            )
            await service.stop_connection_loop()

        assert device["filters"]["eq_band_1"]["parameters"]["gain"] == 6.0
        assert "compressor" in device["processors"]
        assert applied == [True, True]
