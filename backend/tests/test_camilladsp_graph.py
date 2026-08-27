"""What the crossover and the mono switch actually write into the DSP graph.

Two pipeline gestures with no coverage at all. Both are *spatial* rather than
tonal, which is why they are here and not with the EQ bands: neither is gated by
the effects master toggle, and both survive a bypass.

* `_set_passband_filter` is the whole subwoofer story — `PUT /api/equalizer/
  target/zone:<id>/crossover` and its lowpass twin. A highpass left in the
  pipeline after the user turned it off is a speaker with no bass, permanently;
  a lowpass left in is a speaker playing nothing *but* bass. The removal branch
  is the one that has to be right, and it deletes from two places (the filter
  definition and the pipeline step) — leaving either behind is a different
  broken room.
* `set_mono` swaps the pipeline's Mixer step. Restored independently of bypass
  after a reconnect, so it is also part of the recovery path.

These run against `CamillaDaemonDouble`, so `_get_config` / `_set_config` execute
for real and the assertions are on the graph that was pushed.
"""
import logging
from unittest.mock import AsyncMock, Mock

import pytest

from backend.core.equalizer.service import CamillaDSPService


@pytest.fixture
def service(mock_camilla_client, tmp_path, monkeypatch):
    """Connected, talking to the daemon double, persisting into `tmp_path`.

    STORAGE_PATH is redirected because this checkout is the appliance: a
    crossover write schedules a debounced rewrite of the operator's real
    equalizer.json.
    """
    monkeypatch.setattr(CamillaDSPService, "STORAGE_PATH", tmp_path / "equalizer.json")
    settings = Mock()
    settings.get_setting = AsyncMock(return_value=None)
    svc = CamillaDSPService(settings_service=settings)
    svc._client = mock_camilla_client
    svc._connected = True
    return svc


class TestCrossover:
    """Highpass and lowpass: the two halves of a subwoofer split."""

    async def test_enabling_the_highpass_defines_it_and_wires_it_in(
        self, service, camilla_daemon
    ):
        """Both halves are needed. A definition with no pipeline reference is
        inert; a pipeline reference with no definition makes CamillaDSP reject
        the whole config and keep the previous graph — silently, from here.
        """
        camilla_daemon.load({"filters": {}, "pipeline": []})

        assert await service.set_crossover_filter(True, frequency=120.0, q=0.5) is True

        pushed = camilla_daemon.last_pushed
        assert pushed["filters"]["crossover_highpass"] == {
            "type": "Biquad",
            "parameters": {"type": "Highpass", "freq": 120.0, "q": 0.5},
        }
        wired = [s for s in pushed["pipeline"] if "crossover_highpass" in s.get("names", [])]
        assert {ch for s in wired for ch in s["channels"]} == {0, 1}

    async def test_the_lowpass_is_the_same_gesture_with_the_other_type(
        self, service, camilla_daemon
    ):
        """One helper serves both; the type string is the only thing that
        distinguishes "speakers without bass" from "subwoofer only"."""
        camilla_daemon.load({"filters": {}, "pipeline": []})

        assert await service.set_lowpass_filter(True, frequency=80.0, q=0.707) is True

        params = camilla_daemon.last_pushed["filters"]["crossover_lowpass"]["parameters"]
        assert params == {"type": "Lowpass", "freq": 80.0, "q": 0.707}

    async def test_disabling_the_crossover_deletes_the_filter_definition(
        self, service, camilla_daemon
    ):
        """Left defined but unreferenced it is harmless today and a trap later:
        the next `restore_effects` walks the filter map to rebuild the pipeline.
        """
        camilla_daemon.load({
            "filters": {"crossover_highpass": {"type": "Biquad", "parameters": {}}},
            "pipeline": [{"type": "Filter", "channels": [0], "names": ["crossover_highpass"]}],
        })

        assert await service.set_crossover_filter(False) is True

        assert "crossover_highpass" not in camilla_daemon.last_pushed["filters"]

    async def test_disabling_the_crossover_unwires_it_from_the_pipeline(
        self, service, camilla_daemon
    ):
        """The audible half. A highpass still referenced after the user switched
        it off leaves that room with no bass and no control that brings it back.
        """
        camilla_daemon.load({
            "filters": {"crossover_highpass": {"type": "Biquad", "parameters": {}}},
            "pipeline": [
                {"type": "Filter", "channels": [0], "names": ["eq_band_00", "crossover_highpass"]},
                {"type": "Filter", "channels": [1], "names": ["crossover_highpass"]},
            ],
        })

        await service.set_crossover_filter(False)

        names = [n for s in camilla_daemon.last_pushed["pipeline"] for n in s.get("names", [])]
        assert "crossover_highpass" not in names
        assert "eq_band_00" in names, "unwiring the crossover took a band with it"

    async def test_disabling_a_crossover_that_was_never_there_is_not_an_error(
        self, service, camilla_daemon
    ):
        """The UI sends the off state on every zone save, subwoofer or not."""
        camilla_daemon.load({"filters": {}, "pipeline": []})

        assert await service.set_crossover_filter(False) is True

    async def test_the_two_crossovers_do_not_disturb_each_other(
        self, service, camilla_daemon
    ):
        """A sub split is both filters at once, on two different clients.

        They share one helper and one pipeline; turning one off must not unwire
        the other, or one half of the split goes full range.
        """
        camilla_daemon.load({"filters": {}, "pipeline": []})
        await service.set_crossover_filter(True, frequency=80.0)
        await service.set_lowpass_filter(True, frequency=80.0)

        await service.set_crossover_filter(False)

        pushed = camilla_daemon.last_pushed
        names = [n for s in pushed["pipeline"] for n in s.get("names", [])]
        assert "crossover_lowpass" in names
        assert "crossover_highpass" not in names
        assert "crossover_lowpass" in pushed["filters"]

    async def test_a_disconnected_daemon_refuses_rather_than_raising(self, service):
        """`@handle_errors(default=False)` wraps it, but the guard answers first.

        The route reads the boolean: raising would turn a crossover save into an
        HTTP 500 while the daemon is merely restarting.
        """
        service._connected = False

        assert await service.set_crossover_filter(True, frequency=80.0) is False

    async def test_a_daemon_that_rejects_the_graph_answers_false(
        self, service, mock_camilla_client
    ):
        """CamillaDSP validates the config it is handed and can refuse it.

        Answered True, the UI would show a crossover the daemon never applied.
        """
        mock_camilla_client.config.set_active.side_effect = ValueError("invalid pipeline")

        assert await service.set_crossover_filter(True, frequency=80.0) is False


class TestMono:
    """The Mixer swap — stereo passthrough versus a −6 dB L+R sum."""

    async def test_enabling_mono_points_the_mixer_step_at_the_mono_mixer(
        self, service, camilla_daemon
    ):
        camilla_daemon.load({
            "mixers": {"stereo": {}, "mono": {}},
            "filters": {},
            "pipeline": [{"type": "Mixer", "name": "stereo"}],
        })

        assert await service.set_mono(enabled=True) is True

        assert camilla_daemon.last_pushed["pipeline"][0]["name"] == "mono"
        assert service._mono is True

    async def test_disabling_mono_points_it_back_at_stereo(self, service, camilla_daemon):
        camilla_daemon.load({
            "mixers": {"stereo": {}, "mono": {}},
            "filters": {},
            "pipeline": [{"type": "Mixer", "name": "mono"}],
        })
        service._mono = True

        assert await service.set_mono(enabled=False) is True

        assert camilla_daemon.last_pushed["pipeline"][0]["name"] == "stereo"

    async def test_a_config_without_a_mono_mixer_gets_one_defined(
        self, service, camilla_daemon
    ):
        """The definition carries −6 dB per source: summing two channels at unity
        clips. A mixer synthesised at the wrong gain is audible distortion, not a
        missing feature.
        """
        camilla_daemon.load({
            "mixers": {"stereo": {}},
            "filters": {},
            "pipeline": [{"type": "Mixer", "name": "stereo"}],
        })

        await service.set_mono(enabled=True)

        mono = camilla_daemon.last_pushed["mixers"]["mono"]
        assert mono["channels"] == {"in": 2, "out": 2}
        assert [src["gain"] for m in mono["mapping"] for src in m["sources"]] == [-6] * 4
        assert {src["channel"] for m in mono["mapping"] for src in m["sources"]} == {0, 1}

    async def test_only_the_first_mixer_step_is_retargeted(self, service, camilla_daemon):
        """The pipeline can hold more than one Mixer; the first is the channel
        stage. Rewriting them all would repoint a downstream mixer the DSP
        config author put there on purpose."""
        camilla_daemon.load({
            "mixers": {"stereo": {}, "mono": {}, "downstream": {}},
            "filters": {},
            "pipeline": [
                {"type": "Mixer", "name": "stereo"},
                {"type": "Filter", "channels": [0], "names": []},
                {"type": "Mixer", "name": "downstream"},
            ],
        })

        await service.set_mono(enabled=True)

        pushed = camilla_daemon.last_pushed["pipeline"]
        assert pushed[0]["name"] == "mono"
        assert pushed[2]["name"] == "downstream"

    async def test_a_disconnected_daemon_refuses_mono_and_says_why(self, service, caplog):
        """`_restore_after_reconnect` reads this boolean to decide whether the
        daemon took its mono back; a silent True there would hide the failure."""
        service._connected = False

        with caplog.at_level(logging.WARNING):
            assert await service.set_mono(enabled=True) is False

        assert "Cannot set mono: not connected" in caplog.text

    async def test_a_batched_mono_write_does_not_persist(self, service, camilla_daemon, tmp_path):
        """`persist=False` is what a zone update and the reconnect restore pass.

        Persisting there would write equalizer.json once per member of the zone,
        and the reconnect would rewrite the file it had just read.
        """
        camilla_daemon.load({
            "mixers": {"stereo": {}, "mono": {}},
            "filters": {},
            "pipeline": [{"type": "Mixer", "name": "stereo"}],
        })

        await service.set_mono(enabled=True, persist=False)

        assert not (tmp_path / "equalizer.json").exists()
