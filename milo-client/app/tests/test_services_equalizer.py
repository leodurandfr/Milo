"""
Unit tests for EqualizerService.
"""
import asyncio
import copy
from pathlib import Path

import pytest
import yaml
from unittest.mock import AsyncMock, call, patch


class TestEqualizerServiceProperties:
    """Test EqualizerService property accessors."""

    def test_connected_property(self, equalizer_service):
        """Should return connection state."""
        assert equalizer_service.connected is True

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
        mock_camilla_client.set_config.assert_called()


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
        mock_camilla_client.set_mute.assert_called_with(True)


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
    async def test_connect_when_the_daemon_is_not_there(self):
        """Should answer False, and leave no half-built client behind.

        This is the cold boot: milo-client-camilladsp.service is ordered After=
        this unit, so the daemon is not listening yet and the first attempt
        always lands here. The loop owns the retry — what it must not inherit is
        a `_client` that looks connected.
        """
        refuses = AsyncMock()
        refuses.connect.side_effect = OSError("connection refused")
        with patch("services.equalizer.CamillaDspClient", return_value=refuses):
            from services.equalizer import EqualizerService
            service = EqualizerService()
            result = await service._connect_once()
            assert result is False
            assert service.connected is False
            assert service._client is None

    @pytest.mark.asyncio
    async def test_connect_once_skips_when_already_connected(self, equalizer_service):
        """Should return True immediately when already connected."""
        result = await equalizer_service._connect_once()
        assert result is True
        assert equalizer_service.connected is True

    @pytest.mark.asyncio
    async def test_probe_detects_dead_connection(self, equalizer_service, mock_camilla_client):
        """Should mark disconnected when probe fails."""
        mock_camilla_client.get_state.side_effect = IOError("Connection refused")
        await equalizer_service._probe_connection()
        assert equalizer_service.connected is False
        assert equalizer_service._client is None

    @pytest.mark.asyncio
    async def test_probe_keeps_connected_on_success(self, equalizer_service, mock_camilla_client):
        """Should stay connected when probe succeeds."""
        mock_camilla_client.get_state.return_value = "Running"
        await equalizer_service._probe_connection()
        assert equalizer_service.connected is True

    @pytest.mark.asyncio
    async def test_restore_after_reconnect_sets_volume(self, equalizer_service, mock_camilla_client):
        """Should restore cached volume and mute after reconnection."""
        equalizer_service._volume = {"main": -25.0, "mute": False}
        await equalizer_service._restore_after_reconnect()
        mock_camilla_client.set_volume.assert_called_with(-25.0)
        mock_camilla_client.set_mute.assert_called_with(False)

    @pytest.mark.asyncio
    async def test_exec_never_opens_a_connection_of_its_own(self, mock_camilla_client):
        """A command must not reach a daemon the loop has not restored yet.

        CamillaDSP comes up muted at its unit's `--gain` floor, and only the
        connection loop pushes the cached volume and mute over it. A command
        that connected here would run against the bare fader — which is exactly
        what the server's `PUT /equalizer/mute` did on 2026-09-20.
        """
        with patch("services.equalizer.CamillaDspClient", return_value=mock_camilla_client) as factory:
            from services.equalizer import EqualizerService
            service = EqualizerService()
            assert service.connected is False

            with pytest.raises(ConnectionError):
                await service._exec(lambda c: c.set_mute(False))

            factory.assert_not_called()
            mock_camilla_client.set_mute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_exec_marks_disconnected_so_the_loop_takes_over(self, mock_camilla_client):
        """A failed call hands the connection back to the loop, which restores it.

        Without this the service would keep a client the daemon has dropped and
        every later command would fail down the same dead socket.
        """
        with patch("services.equalizer.CamillaDspClient", return_value=mock_camilla_client):
            from services.equalizer import EqualizerService
            service = EqualizerService()
            service._client = mock_camilla_client
            service._connected = True

            async def fails(client):
                raise IOError("Connection lost")

            with pytest.raises(IOError):
                await service._exec(fails)

            assert service.connected is False

    @pytest.mark.asyncio
    async def test_a_push_that_lands_before_the_loop_is_deferred_not_applied_bare(
        self, mock_camilla_client
    ):
        """The 2026-09-20 sequence, replayed against a daemon the loop has not reached.

        The server pushes the level, then unmutes unconditionally
        (`websocket.py::_apply_target_volume_to_client`). Both must be refused
        while there is no restored connection, and both must survive in the
        cache so the loop replays them — volume first, unmute second. The
        ordering is the point: reversed, the fader opens before it is set.
        """
        with patch("services.equalizer.CamillaDspClient", return_value=mock_camilla_client):
            from services.equalizer import EqualizerService
            service = EqualizerService()

            assert await service.set_volume(-52.8) is False
            assert await service.set_mute(False) is False
            mock_camilla_client.set_mute.assert_not_awaited()

            # The loop gets there; it is the only path that connects.
            service._client = mock_camilla_client
            service._connected = True
            await service._restore_after_reconnect()

        assert mock_camilla_client.method_calls == [
            call.set_volume(-52.8),
            call.set_mute(False),
        ]

    @pytest.mark.asyncio
    async def test_the_level_is_restored_before_the_config_is_read(
        self, mock_camilla_client
    ):
        """Order inside the loop, not just between the loop and everything else.

        `_load_state_from_config` goes through `_exec`, which no longer
        reconnects, so a failure there clears `_connected` and skips whatever
        comes after it. With the config read first, that left the cached level
        unsent and the speaker muted at its `--gain` floor until the next pass.
        Reading the config after means a failed read costs the EQ state it was
        fetching, never the level.
        """
        from services.equalizer import EqualizerService
        service = EqualizerService()
        service._client = mock_camilla_client
        service._connected = True

        order = []
        async def restore():
            order.append("restore")
            return True
        async def load():
            order.append("load")
        service._restore_after_reconnect = restore
        service._load_state_from_config = load
        service._connect_once = AsyncMock(return_value=True)
        service._probe_connection = AsyncMock()

        async def stop_after_first_pass(_delay):
            service._running = False
        with patch("services.equalizer.asyncio.sleep", stop_after_first_pass):
            await service._connection_loop()

        assert order == ["restore", "load"]

    @pytest.mark.asyncio
    async def test_a_restore_that_fails_still_costs_a_backoff(self):
        """A connect that succeeds and a restore that then fails must not spin.

        The delay used to be slept only when `_connect_once` itself failed, so
        this path — connect ok, GetConfig error, `_exec` clears `_connected`,
        idle loop exits at once — went straight back to connecting with no wait:
        two log lines a pass, for as long as the daemon refused. `-w` holding an
        invalid config, or a timeout under CPU starvation, both reach it.
        """
        from services.equalizer import EqualizerService
        service = EqualizerService()
        service._probe_connection = AsyncMock()
        service._load_state_from_config = AsyncMock()

        # The loop is stopped by counting passes, not by the sleep it is meant
        # to take — otherwise the very defect under test (never sleeping) would
        # hang the suite instead of failing it.
        passes = []
        async def connect_once():
            passes.append(1)
            if len(passes) >= 3:
                service._running = False
            service._connected = True
            return True
        service._connect_once = connect_once

        async def restore_then_drop():
            service._connected = False
            raise ConnectionError("Not connected to CamillaDSP")
        service._restore_after_reconnect = restore_then_drop

        slept = []
        with patch("services.equalizer.asyncio.sleep", AsyncMock(side_effect=lambda d: slept.append(d))):
            await service._connection_loop()

        assert slept, "the loop retried without waiting: a refusing daemon would spin"
        assert slept == sorted(slept) and slept[-1] > slept[0], (
            f"the backoff never grew: {slept}. A delay reset on every successful "
            f"connect churns a fresh client every RECONNECT_DELAY for ever."
        )

    @pytest.mark.asyncio
    async def test_a_session_that_worked_is_retried_without_waiting(self):
        """A socket lost after a usable session must not cost a backoff.

        `_exec` no longer reconnects, so every `/equalizer/*` call answers 400
        until the loop is back. Sleeping there spends the server's sync retries
        (6 at 3 s) on a daemon that is most likely already up — measured cost of
        a lone DSP restart, before this: ~10 s of refusals instead of ~5.
        """
        from services.equalizer import EqualizerService
        service = EqualizerService()
        service._load_state_from_config = AsyncMock()
        service._restore_after_reconnect = AsyncMock(return_value=True)

        passes = []
        async def connect_once():
            passes.append(1)
            service._connected = True
            return True
        service._connect_once = connect_once

        async def drop_on_first_probe():
            service._connected = False
            if len(passes) >= 2:
                service._running = False
        service._probe_connection = drop_on_first_probe

        slept = []
        async def record(delay):
            slept.append(delay)
        with patch("services.equalizer.asyncio.sleep", record):
            await service._connection_loop()

        # The idle probe sleeps; the reconnect must not add its own on top.
        assert len(slept) == len(passes), (
            f"{len(slept)} sleeps for {len(passes)} passes: a healthy session "
            f"that dropped paid a reconnect delay it did not owe"
        )

    @pytest.mark.asyncio
    async def test_a_first_connect_restores_the_startup_floor_not_unity(
        self, mock_camilla_client
    ):
        """Before the server's first push, the loop must push the floor, not 0 dB.

        `_restore_after_reconnect` runs on the first connect too, so the cache's
        initial value is what the fader holds while the server is still
        resolving the client's level. At unity that is a speaker at full scale.
        """
        with patch("services.equalizer.CamillaDspClient", return_value=mock_camilla_client):
            from services.equalizer import EqualizerService, STARTUP_GAIN_DB
            service = EqualizerService()
            service._client = mock_camilla_client
            service._connected = True

            await service._restore_after_reconnect()

        mock_camilla_client.set_volume.assert_awaited_once_with(STARTUP_GAIN_DB)
        mock_camilla_client.set_mute.assert_awaited_once_with(True)

    @pytest.mark.asyncio
    async def test_a_reconnect_closes_the_client_it_replaces(self, equalizer_service):
        """Letting go of a client is not the same as closing its socket.

        A command the daemon *refused* clears `_connected` exactly like a dead
        socket does, but leaves the connection open and in step. The client's
        own read and ping tasks hold it alive, so overwriting `_client` does not
        collect it — it would go on pinging the local daemon every 20 s, one
        orphan per refused push from the server.
        """
        stale = equalizer_service._client
        equalizer_service._connected = False

        with patch("services.equalizer.CamillaDspClient", return_value=AsyncMock()):
            assert await equalizer_service._connect_once() is True

        stale.disconnect.assert_awaited_once()
        assert equalizer_service._client is not stale

    @pytest.mark.asyncio
    async def test_a_probe_that_finds_the_daemon_gone_closes_it_too(
        self, equalizer_service, mock_camilla_client
    ):
        """Same reason on the other path that drops a client."""
        mock_camilla_client.get_state.side_effect = IOError("Connection refused")

        await equalizer_service._probe_connection()

        assert equalizer_service._client is None
        mock_camilla_client.disconnect.assert_awaited_once()

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
        config = mock_camilla_client.get_config.return_value
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
        config = mock_camilla_client.get_config.return_value
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
        config = mock_camilla_client.get_config.return_value
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
        config = mock_camilla_client.get_config.return_value
        result = await equalizer_service.set_filter("eq_band_1", gain=2.0, filter_type="Lowshelf")
        assert result is True
        assert config["filters"]["eq_band_1"]["parameters"]["type"] == "Lowshelf"

    @pytest.mark.asyncio
    async def test_set_filter_does_not_repipe_a_bypassed_band(self, equalizer_service, mock_camilla_client):
        """Tuning a band on a bypassed client must apply the gain without restoring the band."""
        config = mock_camilla_client.get_config.return_value
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
        client = AsyncMock()
        client.get_config.side_effect = lambda: copy.deepcopy(device)
        client.set_config = AsyncMock(
            side_effect=lambda cfg: device.update(copy.deepcopy(cfg))
        )

        with patch("services.equalizer.CamillaDspClient", return_value=client):
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


class TestEqualizerWholeRecordPush:
    """The two setters only a whole-record push ever calls.

    `EqualizerClientProxyService.apply_record` is the single path by which a
    complete `EqualizerSettings` reaches a satellite — the live write, the
    reconnection sync and the pending crossover replay all go through it — and
    two of its five legs land here: `PUT /equalizer/filters` on
    `set_filters_batch`, `PUT /equalizer/mono` on `set_mono`. Measured
    2026-08-18: gutting both to constants left the whole satellite suite green,
    so a client that silently applied none of its EQ on reconnect would have
    reached the fleet with CI clean.
    """

    @pytest.mark.asyncio
    async def test_a_batch_applies_every_tuning_key(self, equalizer_service, mock_camilla_client):
        """The keys are the wire contract with apply_record, which sends id, gain,
        freq, q and filter_type for each band of the record."""
        config = mock_camilla_client.get_config.return_value

        result = await equalizer_service.set_filters_batch([
            {"id": "eq_band_1", "gain": 4.5, "freq": 250.0, "q": 1.4, "filter_type": "Lowshelf"},
            {"id": "eq_band_2", "gain": -3.0, "freq": 4000.0, "q": 0.8, "filter_type": "Peaking"},
        ])

        assert result == {"success": True, "applied": 2}
        assert config["filters"]["eq_band_1"]["parameters"] == {
            "type": "Lowshelf", "freq": 250.0, "gain": 4.5, "q": 1.4
        }
        assert config["filters"]["eq_band_2"]["parameters"] == {
            "type": "Peaking", "freq": 4000.0, "gain": -3.0, "q": 0.8
        }

    @pytest.mark.asyncio
    async def test_the_whole_batch_costs_one_save(self, equalizer_service, mock_camilla_client):
        """What the batch route exists for. A satellite writes its config to an SD
        card and re-pushes it to CamillaDSP on every save, so a per-band save on a
        ten-band record is ten rewrites and ten reloads for one user gesture."""
        await equalizer_service.set_filters_batch([
            {"id": "eq_band_1", "gain": 1.0},
            {"id": "eq_band_2", "gain": 2.0},
        ])

        assert mock_camilla_client.set_config.call_count == 1

    @pytest.mark.asyncio
    async def test_the_batch_reaches_the_disk(self, equalizer_service, mock_camilla_client):
        """set_active alone is the live DSP; a satellite that answered 200 without
        writing comes back on its old EQ at the next reboot, and only a second
        physical unit shows it."""
        await equalizer_service.set_filters_batch([{"id": "eq_band_1", "gain": 6.0}])

        persisted = yaml.safe_load(Path(equalizer_service.config_file).read_text())
        assert persisted["filters"]["eq_band_1"]["parameters"]["gain"] == 6.0

    @pytest.mark.asyncio
    async def test_a_band_the_config_does_not_define_is_not_created(self, equalizer_service, mock_camilla_client):
        """A stray definition would sit in filters/ doing nothing until the master
        toggle re-piped the bands it finds, and then be audible."""
        config = mock_camilla_client.get_config.return_value

        result = await equalizer_service.set_filters_batch([
            {"id": "eq_band_1", "gain": 1.0},
            {"id": "eq_band_99", "gain": 12.0},
        ])

        assert result["applied"] == 1, "an unknown band is not applied"
        assert "eq_band_99" not in config["filters"]

    @pytest.mark.asyncio
    async def test_a_batch_does_not_repipe_a_bypassed_client(self, equalizer_service, mock_camilla_client):
        """Bands carry tuning only, on a satellite exactly as locally — the master
        toggle owns pipeline membership. If a batch regained that power, the
        reconnection sync would un-bypass every client it re-synced."""
        config = mock_camilla_client.get_config.return_value
        await equalizer_service.set_equalizer_enabled(False)

        await equalizer_service.set_filters_batch([{"id": "eq_band_1", "gain": 5.0}])

        assert config["filters"]["eq_band_1"]["parameters"]["gain"] == 5.0
        assert "eq_band_1" not in config["pipeline"][0]["names"]
        assert equalizer_service.equalizer_enabled is False

    @pytest.mark.asyncio
    async def test_a_batch_leaves_a_band_exactly_as_a_single_push_would(
        self, equalizer_service, mock_camilla_client
    ):
        """set_filters_batch's own docstring's claim. The two paths are reached by
        different callers — a slider goes through set_filter, a reconnect through
        the batch — so a drift between them shows up as a client that tunes
        correctly under the hand and wrongly after a power cut."""
        config = mock_camilla_client.get_config.return_value

        await equalizer_service.set_filter("eq_band_1", gain=4.5, freq=250.0, q=1.4, filter_type="Lowshelf")
        after_single_push = copy.deepcopy(config["filters"]["eq_band_1"])

        await equalizer_service.set_filter("eq_band_1", gain=0.0, freq=100.0, q=1.0, filter_type="Peaking")
        await equalizer_service.set_filters_batch([
            {"id": "eq_band_1", "gain": 4.5, "freq": 250.0, "q": 1.4, "filter_type": "Lowshelf"},
        ])

        assert config["filters"]["eq_band_1"] == after_single_push


class TestEqualizerMono:
    """`PUT /equalizer/mono`, the fourth leg of apply_record.

    Mono is a Mixer step swap in the CamillaDSP pipeline, not an ALSA route: the
    satellite sums the two channels itself. A record whose mono leg does nothing
    leaves one speaker in a mono zone playing a stereo half.
    """

    @staticmethod
    def _with_mixer(mock_camilla_client, name="stereo"):
        config = mock_camilla_client.get_config.return_value
        config["pipeline"].append({"type": "Mixer", "name": name})
        return config

    @pytest.mark.asyncio
    async def test_enabling_swaps_the_pipeline_to_the_mono_mixer(self, equalizer_service, mock_camilla_client):
        config = self._with_mixer(mock_camilla_client)

        assert await equalizer_service.set_mono(True) is True

        mixer = [s for s in config["pipeline"] if s["type"] == "Mixer"]
        assert [s["name"] for s in mixer] == ["mono"]
        assert equalizer_service.mono is True

    @pytest.mark.asyncio
    async def test_disabling_swaps_it_back_to_stereo(self, equalizer_service, mock_camilla_client):
        config = self._with_mixer(mock_camilla_client, name="mono")

        assert await equalizer_service.set_mono(False) is True

        mixer = [s for s in config["pipeline"] if s["type"] == "Mixer"]
        assert [s["name"] for s in mixer] == ["stereo"]
        assert equalizer_service.mono is False

    @pytest.mark.asyncio
    async def test_the_mono_mixer_is_defined_when_the_config_lacks_it(
        self, equalizer_service, mock_camilla_client
    ):
        """The pipeline names a mixer that must exist in `mixers`, or CamillaDSP
        refuses the whole config and the satellite goes silent — so the definition
        is written before the step is pointed at it."""
        config = self._with_mixer(mock_camilla_client)
        assert "mixers" not in config

        await equalizer_service.set_mono(True)

        assert "mono" in config["mixers"]
        assert config["mixers"]["mono"]["channels"] == {"in": 2, "out": 2}

    @pytest.mark.asyncio
    async def test_mono_reaches_the_disk(self, equalizer_service, mock_camilla_client):
        """Same reason as the batch: the record has to survive a reboot."""
        self._with_mixer(mock_camilla_client)

        await equalizer_service.set_mono(True)

        persisted = yaml.safe_load(Path(equalizer_service.config_file).read_text())
        mixer = [s for s in persisted["pipeline"] if s["type"] == "Mixer"]
        assert [s["name"] for s in mixer] == ["mono"]


class TestEqualizerServiceLevelTrim:
    """The level trim is a calibration, not an effect.

    It compensates this speaker's sensitivity so every client can sit at the
    same volume, which means two things must hold on the unit: the master
    bypass must leave it alone (a bypass that unbalanced the room is a bug),
    and it must survive a reboot on the satellite's own config, since the
    server only re-pushes it when the speaker was away while it changed.
    """

    @pytest.mark.asyncio
    async def test_a_trim_is_a_gain_filter_on_both_channels(
        self, equalizer_service, mock_camilla_client
    ):
        config = mock_camilla_client.get_config.return_value

        assert await equalizer_service.set_gain(-4.5) is True

        assert config["filters"]["gain_trim"]["type"] == "Gain"
        assert config["filters"]["gain_trim"]["parameters"]["gain"] == -4.5
        step = next(s for s in config["pipeline"] if s["type"] == "Filter")
        assert "gain_trim" in step["names"]
        assert equalizer_service.gain_db == -4.5

    @pytest.mark.asyncio
    async def test_a_trim_of_zero_leaves_no_filter_behind(
        self, equalizer_service, mock_camilla_client
    ):
        """An untrimmed speaker gets the config it always had — no 0 dB stage."""
        config = mock_camilla_client.get_config.return_value
        await equalizer_service.set_gain(-4.5)

        assert await equalizer_service.set_gain(0.0) is True

        assert "gain_trim" not in config["filters"]
        step = next(s for s in config["pipeline"] if s["type"] == "Filter")
        assert "gain_trim" not in step["names"]

    @pytest.mark.asyncio
    async def test_an_unchanged_trim_does_not_reload_the_pipeline(
        self, equalizer_service, mock_camilla_client
    ):
        """The server re-pushes the trim on every admission, and applying a
        config restarts CamillaDSP's processing — so a push that changes nothing
        must reach the daemon not at all."""
        await equalizer_service.set_gain(-4.5)
        mock_camilla_client.set_config.reset_mock()

        assert await equalizer_service.set_gain(-4.5) is True

        mock_camilla_client.set_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_the_master_bypass_does_not_strip_the_trim(
        self, equalizer_service, mock_camilla_client
    ):
        """Bypassing the equalizer must not unbalance the room: the trim is not
        an effect, and it is named apart from eq_band_*/loudness_*/compressor
        precisely so the bypass cannot reach it."""
        config = mock_camilla_client.get_config.return_value
        await equalizer_service.set_gain(-4.5)

        await equalizer_service.set_equalizer_enabled(False)

        assert config["filters"]["gain_trim"]["parameters"]["gain"] == -4.5
        step = next(s for s in config["pipeline"] if s["type"] == "Filter")
        assert "gain_trim" in step["names"]
        assert "eq_band_1" not in step["names"]  # the bypass did happen

    @pytest.mark.asyncio
    async def test_the_trim_is_read_back_from_the_persisted_config(
        self, equalizer_service, mock_camilla_client
    ):
        """On (re)connect the cache is rebuilt from the config CamillaDSP
        reloaded, so the satellite reports the trim it is actually applying."""
        config = mock_camilla_client.get_config.return_value
        config["filters"]["gain_trim"] = {
            "type": "Gain",
            "parameters": {"gain": 6.0, "inverted": False, "mute": False},
        }

        await equalizer_service._load_state_from_config()

        assert equalizer_service.gain_db == 6.0

    @pytest.mark.asyncio
    async def test_the_trim_reaches_the_disk(self, equalizer_service, mock_camilla_client):
        """The server re-pushes a trim only when it changed while the speaker was
        away — a reboot recovers it from the satellite's own config or not at all."""
        await equalizer_service.set_gain(-4.5)

        persisted = yaml.safe_load(Path(equalizer_service.config_file).read_text())
        assert persisted["filters"]["gain_trim"]["parameters"]["gain"] == -4.5
