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


class TestLevelTrim:
    """The third spatial gesture: a fixed Gain stage balancing this unit against
    the satellites, so every client can sit at the same volume.

    Same two properties as the crossover and the mono swap — not gated by the
    effects toggle, survives a bypass — plus one the others do not have: the
    server never writes its config to disk, so the trim only exists in the graph
    and in the cache `_restore_after_reconnect` reads. The durable value is
    `Client.gain_db` in the multiroom registry.
    """

    async def test_a_trim_is_a_gain_filter_wired_on_both_channels(
        self, service, camilla_daemon
    ):
        camilla_daemon.load({"filters": {}, "pipeline": []})

        assert await service.set_gain(-4.5) is True

        pushed = camilla_daemon.last_pushed
        assert pushed["filters"]["gain_trim"] == {
            "type": "Gain",
            "parameters": {"gain": -4.5, "inverted": False, "mute": False},
        }
        wired = [s for s in pushed["pipeline"] if "gain_trim" in s.get("names", [])]
        assert {ch for s in wired for ch in s["channels"]} == {0, 1}

    async def test_a_trim_of_zero_leaves_no_filter_behind(self, service, camilla_daemon):
        """An untrimmed speaker runs the graph it always had — no 0 dB stage, and
        nothing left defined for the next `restore_effects` to walk over."""
        camilla_daemon.load({
            "filters": {"gain_trim": {"type": "Gain", "parameters": {"gain": -4.5}}},
            "pipeline": [{"type": "Filter", "channels": [0, 1], "names": ["gain_trim"]}],
        })

        assert await service.set_gain(0.0) is True

        pushed = camilla_daemon.last_pushed
        assert "gain_trim" not in pushed["filters"]
        names = [n for s in pushed["pipeline"] for n in s.get("names", [])]
        assert "gain_trim" not in names

    async def test_unwiring_the_trim_takes_nothing_else_with_it(
        self, service, camilla_daemon
    ):
        camilla_daemon.load({
            "filters": {"gain_trim": {"type": "Gain", "parameters": {"gain": -4.5}}},
            "pipeline": [
                {"type": "Filter", "channels": [0], "names": ["eq_band_00", "gain_trim"]},
                {"type": "Filter", "channels": [1], "names": ["gain_trim", "loudness_low"]},
            ],
        })

        await service.set_gain(0.0)

        names = [n for s in camilla_daemon.last_pushed["pipeline"] for n in s.get("names", [])]
        assert "gain_trim" not in names
        assert "eq_band_00" in names and "loudness_low" in names

    async def test_the_master_bypass_does_not_strip_the_trim(self, service, camilla_daemon):
        """Bypassing the equalizer must not unbalance the room. The trim is named
        apart from eq_band_*/loudness_*/compressor precisely so `bypass_effects`
        cannot reach it — the same reason the crossover survives one."""
        camilla_daemon.load({
            "filters": {"eq_band_00": {"type": "Biquad", "parameters": {}}},
            "processors": {},
            "pipeline": [{"type": "Filter", "channels": [0, 1], "names": ["eq_band_00"]}],
        })
        await service.set_gain(-4.5)

        assert await service.bypass_effects() is True

        pushed = camilla_daemon.last_pushed
        names = [n for s in pushed["pipeline"] for n in s.get("names", [])]
        assert "gain_trim" in names
        assert pushed["filters"]["gain_trim"]["parameters"]["gain"] == -4.5
        assert "eq_band_00" not in names, "the bypass did not happen"

    async def test_an_unchanged_trim_does_not_reload_the_pipeline(
        self, service, camilla_daemon
    ):
        """The multiroom admission re-pushes the trim on every reconnection, and
        applying a config restarts CamillaDSP's processing — so a push that
        changes nothing must not reach the daemon at all."""
        camilla_daemon.load({"filters": {}, "pipeline": []})
        await service.set_gain(-4.5)
        pushes_before = len(camilla_daemon.pushed_configs)

        assert await service.set_gain(-4.5) is True

        assert len(camilla_daemon.pushed_configs) == pushes_before

    async def test_a_disconnected_daemon_refuses_rather_than_pretending(self, service):
        """It holds no state of its own: the trim's one home is the registry
        record, and VolumeService re-derives it from there on reconnect. A True
        here would tell the route a trim landed on a daemon that is not there."""
        service._connected = False

        assert await service.set_gain(-4.5) is False

    async def test_the_trim_is_clamped_to_the_published_range(self, service, camilla_daemon):
        """The route validates too, but the reconnect restore and the mode switch
        call this directly with whatever the registry holds."""
        camilla_daemon.load({"filters": {}, "pipeline": []})

        await service.set_gain(-40.0)

        assert camilla_daemon.last_pushed["filters"]["gain_trim"]["parameters"]["gain"] == -12.0


class TestRestoreAfterReconnect:
    """What the server puts back when the daemon comes back without its graph.

    The satellite needs none of this: it writes its CamillaDSP config to disk, so
    a restart reloads the filters. The server's `_set_config` is set_active only,
    so every non-effect setting has to be re-applied from a cache here — and
    `UpdateService` stops and starts `milo-camilladsp.service` alone when the
    operator updates CamillaDSP, which is a normal gesture, not a crash.
    """

    async def test_a_reconnect_puts_the_subwoofer_split_back(self, service, camilla_daemon):
        """Without it the speaker comes back full range under a subwoofer that is
        still playing bass — audible, and logged nowhere."""
        camilla_daemon.load({"filters": {}, "pipeline": []})
        await service.set_crossover_filter(True, frequency=120.0, q=0.5)
        camilla_daemon.load({"filters": {}, "pipeline": []})  # daemon restarted: pristine graph

        await service._restore_after_reconnect()

        pushed = camilla_daemon.last_pushed
        assert pushed["filters"]["crossover_highpass"]["parameters"]["freq"] == 120.0
        assert pushed["filters"]["crossover_highpass"]["parameters"]["q"] == 0.5
        names = [n for s in pushed["pipeline"] for n in s.get("names", [])]
        assert "crossover_highpass" in names

    async def test_a_reconnect_puts_the_lowpass_back_too(self, service, camilla_daemon):
        """The other half of the split, on the subwoofer itself. Lost, it plays
        the whole range."""
        camilla_daemon.load({"filters": {}, "pipeline": []})
        await service.set_lowpass_filter(True, frequency=80.0, q=0.707)
        camilla_daemon.load({"filters": {}, "pipeline": []})

        await service._restore_after_reconnect()

        assert "crossover_lowpass" in camilla_daemon.last_pushed["filters"]

    async def test_a_reconnect_restores_nothing_that_was_switched_off(
        self, service, camilla_daemon
    ):
        """The common case — no subwoofer anywhere. Re-applying a disabled filter
        would reload the pipeline on every reconnection for nothing."""
        camilla_daemon.load({"filters": {}, "pipeline": []})
        await service.set_crossover_filter(True, frequency=120.0)
        await service.set_crossover_filter(False)
        pushes_before = len(camilla_daemon.pushed_configs)

        await service._restore_after_reconnect()

        for pushed in camilla_daemon.pushed_configs[pushes_before:]:
            assert "crossover_highpass" not in pushed["filters"]

    async def test_the_level_trim_is_not_restored_from_here(self, service, camilla_daemon):
        """It comes back through the reconnect *callback* instead — VolumeService
        reads `Client.gain_db` and pushes it (`sync_local_gain`). Keeping a copy
        here would be a second home for one value, and the one that goes stale is
        always the copy: direct mode clears the trim without touching the record.
        """
        camilla_daemon.load({"filters": {}, "pipeline": []})
        await service.set_gain(-4.5)
        camilla_daemon.load({"filters": {}, "pipeline": []})

        await service._restore_after_reconnect()

        assert "gain_trim" not in camilla_daemon.last_pushed["filters"]
