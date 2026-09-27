"""
The EQ curve's peak, the headroom that keeps it at 0 dB, and the native loudness.

What breaks when these fail: a boosting EQ clips at the maximum volume
(measured on Bureau: bands up to +6 dB at 16 kHz overlap to +6.41 dB, and with
its +6 dB trim that was +4.4 dBFS at the -8 dB limit); or a satellite and the
server build a different graph from the same record.
"""
import re
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from backend.config.constants import LOUDNESS_REFERENCE_DB
from backend.core.equalizer import CamillaDSPService, CamillaDspState
from backend.core.equalizer.client_proxy import EqualizerClientProxyService
from backend.core.equalizer.eq_response import SAMPLERATE, band_db, eq_peak_db, headroom_db
from backend.core.multiroom.models import EqFilter, EqualizerSettings, FilterType

REPO = Path(__file__).resolve().parents[2]
BUREAU = [("Peaking", freq, gain, 1.41)
          for freq, gain in ((1000, 2), (2000, 3), (4000, 4), (8000, 5), (16000, 6))]


# ============================================================================
# The response
# ============================================================================

def test_one_band_peaks_at_its_own_gain():
    assert eq_peak_db([("Peaking", 1000, 6.0, 1.41)]) == pytest.approx(6.0, abs=0.01)


def test_the_bureau_curve_peaks_where_it_was_measured():
    """The record measured on the unit: +6.41 dB near 16 kHz, so 6.5 dB of headroom."""
    peak = eq_peak_db(BUREAU)

    assert peak == pytest.approx(6.41, abs=0.05)
    assert band_db(BUREAU[-1], 16000) < peak  # the overlap adds to the largest band
    assert headroom_db(BUREAU) == -6.5


def test_overlapping_bands_peak_above_the_largest_of_them():
    """Why the curve is computed and not read off the band gains: two +3 dB
    bands an octave apart at Q 1.41 overlap well above +3."""
    assert eq_peak_db([("Peaking", 1000, 3.0, 1.41), ("Peaking", 2000, 3.0, 1.41)]) > 3.5


def test_a_flat_or_cutting_curve_needs_no_headroom():
    """Headroom is never a boost: a curve that only cuts leaves the level alone."""
    assert headroom_db([("Peaking", 1000, 0.0, 1.41)]) == 0.0
    assert headroom_db([("Peaking", 1000, -6.0, 1.41), ("Lowshelf", 100, -3.0, 0.707)]) == 0.0


def test_a_shelf_boosts_its_whole_side_of_the_spectrum():
    shelf = ("Lowshelf", 100, 6.0, 0.707)

    assert band_db(shelf, 20) == pytest.approx(6.0, abs=0.1)
    assert band_db(shelf, 5000) == pytest.approx(0.0, abs=0.1)


def test_a_notch_at_its_own_centre_does_not_break_the_computation():
    """A notch is exactly zero at its centre, which is on the grid. Its log
    raised, and every EQ write failed with it: set_filter, the record push, the
    reconnect restore. The API accepts Notch bands."""
    # The notch's skirt lowers the 4 kHz peak a little: a little less than 3 dB.
    assert -3.0 <= headroom_db([("Notch", 1000, 0.0, 1.41), ("Peaking", 4000, 3.0, 1.41)]) < -2.5


def test_a_treble_shelf_is_counted_up_to_nyquist():
    """A 48 kHz stream carries content to 24 kHz and a shelf keeps boosting it:
    inaudible, but it clips all the same. The search stopped at 20 kHz and
    gave a +15 dB shelf at 20 kHz only 7.5 dB of headroom."""
    assert headroom_db([("Highshelf", 20000, 15.0, 0.707)]) == -15.0


def test_both_dsp_configs_run_at_the_rate_the_response_is_computed_at():
    """The response near 16 kHz depends on the sample rate; the server and the
    satellite configs must both declare the one eq_response assumes."""
    configs = [
        REPO / "rootfs/var/lib/milo/camilladsp/config.yml",
        REPO / "milo-client/configs/camilladsp/config.yml",
    ]
    rates = [int(re.search(r"^\s*samplerate:\s*(\d+)", c.read_text(), re.M).group(1)) for c in configs]

    assert rates == [SAMPLERATE, SAMPLERATE]


# ============================================================================
# The server's own DSP
# ============================================================================

BANDS = [f"eq_band_{i:02d}" for i in range(10)]


@pytest.fixture
def dsp(mock_camilla_client, camilla_daemon):
    """A connected CamillaDSPService over the fake daemon, its ten bands piped."""
    service = CamillaDSPService(settings_service=Mock(get_setting=AsyncMock(return_value=None),
                                                      set_setting=AsyncMock()))
    service.set_state_machine(Mock(broadcast=AsyncMock()))
    service._client = mock_camilla_client
    service._connected = True
    service._state = CamillaDspState.RUNNING
    service._effects_enabled = True  # as AudioRoutingService loads it before any restore
    service._schedule_persist = Mock()
    camilla_daemon.load({
        "filters": {
            name: {"type": "Biquad", "parameters": {"type": "Peaking", "freq": f["freq"], "gain": 0.0, "q": 1.41}}
            for name, f in zip(BANDS, service._filters)
        },
        "pipeline": [
            {"type": "Mixer", "name": "stereo"},
            {"type": "Filter", "channels": [0], "names": list(BANDS)},
            {"type": "Filter", "channels": [1], "names": list(BANDS)},
        ],
    })
    return service


def _steps(camilla_daemon):
    return [s for s in camilla_daemon.last_pushed["pipeline"] if s["type"] == "Filter"]


async def _boost_16k(dsp, gain):
    band = dsp._filters[-1]
    await dsp.set_filter(band["id"], band["freq"], gain, 1.41, persist=False)


async def test_a_boosting_band_puts_the_headroom_first_in_each_channel(dsp, camilla_daemon):
    """The attenuation sits before anything that boosts, on both channels."""
    await _boost_16k(dsp, 6.0)

    assert camilla_daemon.last_pushed["filters"]["headroom"]["parameters"]["gain"] == -6.0
    assert [step["names"][0] for step in _steps(camilla_daemon)] == ["headroom", "headroom"]


async def test_the_headroom_follows_the_master_bypass(dsp, camilla_daemon):
    """A bypassed EQ boosts nothing, so it is attenuated by nothing."""
    await _boost_16k(dsp, 6.0)

    dsp.set_effects_enabled(False)  # AudioRoutingService sets it, then bypasses
    await dsp.bypass_effects()
    assert all("headroom" not in step["names"] for step in _steps(camilla_daemon))

    dsp.set_effects_enabled(True)
    await dsp.restore_effects()
    assert [step["names"][0] for step in _steps(camilla_daemon)] == ["headroom", "headroom"]


async def test_a_curve_brought_back_to_flat_drops_its_headroom(dsp, camilla_daemon):
    await _boost_16k(dsp, 6.0)
    await _boost_16k(dsp, 0.0)

    assert "headroom" not in camilla_daemon.last_pushed["filters"]
    assert all("headroom" not in step["names"] for step in _steps(camilla_daemon))


async def test_a_band_the_daemon_refuses_leaves_the_cache_as_it_was(dsp, camilla_daemon, mock_camilla_client):
    """The cache takes the band before the write (the headroom is computed from
    it) and must give it back: get_filters and the next persist report it."""
    mock_camilla_client.set_config.side_effect = RuntimeError("refused")

    assert await dsp.set_filter("eq_band_09", 16000, 6.0, 1.41, persist=False) is False

    assert dsp._filters[-1]["gain"] == 0.0


async def test_a_loudness_edit_during_a_bypass_stays_out_of_the_pipeline(dsp, camilla_daemon):
    """A bypassed EQ plays nothing it gates; turning loudness on then must not
    make it audible on its own. Restoring the EQ brings it in."""
    dsp.set_effects_enabled(False)  # AudioRoutingService sets it, then bypasses
    await dsp.bypass_effects()
    await dsp.set_loudness(enabled=True, persist=False)

    assert all("loudness" not in step["names"] for step in _steps(camilla_daemon))

    dsp.set_effects_enabled(True)
    await dsp.restore_effects()
    assert all("loudness" in step["names"] for step in _steps(camilla_daemon))


async def test_a_compressor_edited_during_a_bypass_stays_out_of_the_pipeline(dsp, camilla_daemon):
    """One rule for every effect the master toggle gates: an edit made during a
    bypass is kept, not played."""
    dsp.set_effects_enabled(False)
    await dsp.bypass_effects()
    await dsp.set_compressor(enabled=True, persist=False)

    assert not any(s.get("name") == "compressor" for s in camilla_daemon.last_pushed["pipeline"])
    assert "compressor" in camilla_daemon.last_pushed["processors"]


async def test_a_band_edited_while_bypassed_pipes_no_headroom(dsp, camilla_daemon):
    """Editing a band never un-bypasses, and the headroom may not either."""
    dsp.set_effects_enabled(False)
    await dsp.bypass_effects()
    await _boost_16k(dsp, 6.0)

    assert all("headroom" not in step["names"] for step in _steps(camilla_daemon))


# ============================================================================
# What a satellite is sent
# ============================================================================

async def test_a_satellite_is_sent_the_headroom_of_its_whole_curve():
    """The satellite computes nothing: the server sends the attenuation its
    record needs, with its bands, and the reference with its loudness."""
    proxy = EqualizerClientProxyService()
    proxy.request = AsyncMock(return_value={"status": "success"})
    record = EqualizerSettings(filters=[
        EqFilter(id="eq_band_09", frequency=16000, gain=6.0, q=1.41, filter_type=FilterType.PEAKING),
    ])

    await proxy.apply_record("192.168.1.60", record)

    sent = {c.args[2]: c.args[3] for c in proxy.request.await_args_list}
    assert sent["/equalizer/filters"]["headroom_db"] == -6.0
    assert sent["/equalizer/loudness"]["reference_level"] == LOUDNESS_REFERENCE_DB


async def test_a_record_with_no_bands_sends_no_headroom():
    """The headroom travels with the bands it is computed from. Sent alone, a 0
    would strip the attenuation from bands the satellite keeps boosting."""
    proxy = EqualizerClientProxyService()
    proxy.request = AsyncMock(return_value={"status": "success"})

    await proxy.apply_record("192.168.1.60", EqualizerSettings(filters=[]))

    sent = {c.args[2]: c.args[3] for c in proxy.request.await_args_list}
    assert "headroom_db" not in sent["/equalizer/filters"]
