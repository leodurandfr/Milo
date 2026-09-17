# backend/tests/test_calibration.py
"""Guard the computed Snapcast configuration.

The analysis replaces "pick one of three hand-calibrated presets" with "compute
four values", so what used to be guaranteed by construction -- that the offered
configuration is one a human measured and the write path accepts -- now has to
be guaranteed by these tests.
"""
from pathlib import Path

import pytest

from backend.core.multiroom.calibration import (
    ClientReading,
    compute_configuration,
    ENCODER_LATENCY_MS,
    FLOOR_MARGIN_MS,
    TCP_RTO_MIN_MS,
    VERIFIED_FLOOR_MS,
)
from backend.core.multiroom.routing import SNAPCLIENT_LIMITS
from backend.core.multiroom.snapcast import (
    BUFFER_MS_RANGE,
    CHUNK_MS_RANGE,
    SUPPORTED_CODECS,
)

# The model's one external check, and it no longer has a preset to lean on.
#
# `responsive` used to carry these four values and this file asserted against
# it, which quietly made a canned UI choice the reference for a computed one.
# It is now stated here as what it actually is: a configuration confirmed by
# listening -- 2026-09-17, three wired speakers, 5 min 40 s of playback with no
# underrun, no xrun and no client disconnect. Change it only against a new
# listening test.
MEASURED_REFERENCE = {
    "buffer_ms": 180, "codec": "pcm", "chunk_ms": 20, "snapclient_buffer_time": 60,
}


def _wired_fleet():
    """The owner's three speakers, as measured on 2026-09-17 under playback.

    300 ICMP probes at 5 Hz per satellite and 4000 fixed-interval sleeps on each
    of them, taken while a radio stream fed all three. These are the numbers the
    model has to turn into a configuration.
    """
    return [
        ClientReading(mac_id="2c:cf:67:b9:46:6f", name="Milō", link="ethernet",
                      link_speed_mbps=1000, rtt_p50_ms=0.0, rtt_max_ms=0.0,
                      loss_pct=0.0, sched_max_ms=0.30, is_local=True),
        ClientReading(mac_id="dc:a6:32:7e:d3:43", name="Canapé", link="ethernet",
                      link_speed_mbps=1000, rtt_p50_ms=0.334, rtt_max_ms=1.440,
                      loss_pct=0.0, sched_max_ms=0.422),
        ClientReading(mac_id="d8:3a:dd:68:e7:e4", name="Bureau", link="ethernet",
                      link_speed_mbps=1000, rtt_p50_ms=0.219, rtt_max_ms=0.786,
                      loss_pct=0.0, sched_max_ms=0.825),
    ]


def _weak_wifi_fleet():
    return [
        ClientReading(mac_id="aa:aa:aa:aa:aa:aa", name="Salon", link="wifi",
                      link_speed_mbps=72, rtt_p50_ms=8.0, rtt_max_ms=140.0,
                      loss_pct=1.4, sched_max_ms=14.0),
    ]


def test_wired_gigabit_fleet_reproduces_the_configuration_confirmed_by_ear():
    """The model's one external check.

    MEASURED_REFERENCE was arrived at by listening; the budget model was derived
    independently from where a chunk spends its time. Fed the readings of the
    very fleet that reference was confirmed on, the two must agree -- if they
    ever diverge, one of them has drifted, and the computed value is the one
    with no human behind it.
    """
    result = compute_configuration(_wired_fleet(), quality="lossless")

    assert result.config == MEASURED_REFERENCE


def test_a_fleet_with_no_remote_client_is_refused():
    """A local-only system has no network in the path, so there is nothing to
    size a buffer against. The caller must answer that case outright instead of
    being handed a number computed from no measurement."""
    local_only = [r for r in _wired_fleet() if r.is_local]

    with pytest.raises(ValueError):
        compute_configuration(local_only)


@pytest.mark.parametrize("quality", ["lossless", "economical"])
@pytest.mark.parametrize(
    "fleet_name", ["wired", "weak_wifi", "perfect", "hostile"]
)
def test_every_computed_value_is_one_the_write_path_accepts(fleet_name, quality):
    """No reading may produce a payload `_validate_config` would reject.

    This is the rail that replaces preset selection. A computed configuration
    outside these ranges reaches the user as a proposal and then fails on apply,
    which is the one failure mode a preset could not have.
    """
    fleets = {
        "wired": _wired_fleet(),
        "weak_wifi": _weak_wifi_fleet(),
        "perfect": [ClientReading("m", "Perfect", "ethernet", 1000, 0.0, 0.0, 0.0, 0.0)],
        "hostile": [ClientReading("m", "Hostile", "wifi", 1.0, 400.0, 4000.0, 40.0, 900.0)],
    }
    config = compute_configuration(fleets[fleet_name], quality=quality).config

    assert BUFFER_MS_RANGE[0] <= config["buffer_ms"] <= BUFFER_MS_RANGE[1]
    assert CHUNK_MS_RANGE[0] <= config["chunk_ms"] <= CHUNK_MS_RANGE[1]
    assert config["codec"] in SUPPORTED_CODECS
    low, high = SNAPCLIENT_LIMITS["buffer_time"]
    assert low <= config["snapclient_buffer_time"] <= high


def test_a_flawless_network_still_clears_the_structural_floor():
    """Zero jitter must not collapse the buffer onto the network term.

    The measured network contribution is ~3 ms against a 180 ms answer, so a
    model that trusted it alone would propose a buffer that cannot work whatever
    the network does -- the chunk, the encoder frame and the client's ALSA
    buffer are spent before a packet is late.
    """
    flawless = [ClientReading("m", "Flawless", "ethernet", 1000, 0.0, 0.0, 0.0, 0.0)]

    result = compute_configuration(flawless)
    c = result.config
    structural = c["chunk_ms"] + ENCODER_LATENCY_MS[c["codec"]] + c["snapclient_buffer_time"]

    assert c["buffer_ms"] >= structural + FLOOR_MARGIN_MS


def test_the_worst_client_sets_the_buffer_for_the_whole_house():
    """`buffer_ms` is a single server-wide value, so degrading one speaker has to
    raise it for everyone. A model that averaged, or that read only the client
    being looked at, would leave the weakest one dropping out."""
    fleet = _wired_fleet()
    before = compute_configuration(fleet).config["buffer_ms"]

    fleet[2].rtt_max_ms = 90.0
    fleet[2].sched_max_ms = 11.0
    after = compute_configuration(fleet)

    assert after.config["buffer_ms"] > before
    assert after.limiting_client == "Bureau"


def test_economical_quality_leaves_the_lossless_codecs():
    """The quality axis is an input, not something the measurement overrides:
    asking for the economical codec on a gigabit link must still answer Opus,
    or the setting does nothing on exactly the fleets that can afford it."""
    result = compute_configuration(_wired_fleet(), quality="economical")

    assert result.config["codec"] == "opus"


def test_lossless_never_falls_back_to_a_lossy_codec():
    """A link too thin for PCM must answer FLAC. Falling to Opus would silently
    discard the one thing the user asked for by name."""
    result = compute_configuration(_weak_wifi_fleet(), quality="lossless")

    assert result.config["codec"] == "flac"


def test_every_decision_carries_a_reason_code_and_no_prose():
    """The UI resolves these through i18n, so a reason must be a key plus its
    parameters. A sentence composed here would reach the user untranslated and
    in whichever language the backend happened to be written in."""
    result = compute_configuration(_wired_fleet())

    assert len(result.reasons) == 4
    for code, params in result.reasons:
        assert code and code.replace("_", "").isalnum()
        assert isinstance(params, dict)


def _canape_over_wifi(loss_pct=10.4):
    """Canapé's own Wi-Fi interface, measured on 2026-09-17.

    250 probes sent from the satellite with the egress interface forced to
    wlan0, because both interfaces sit on one subnet and the reply to a probe
    addressed to the Wi-Fi IP leaves over eth0 (metric 100 against 600).
    """
    return [
        ClientReading(mac_id="dc:a6:32:7e:d3:43", name="Canapé", link="wifi",
                      link_speed_mbps=72, rtt_p50_ms=4.855, rtt_max_ms=6.447,
                      loss_pct=loss_pct, sched_max_ms=0.422),
    ]


def test_a_lossy_link_budgets_for_a_retransmission():
    """Loss has to enter the budget as time, not only as a quality flag.

    Snapcast runs over TCP, so a dropped packet is a chunk that arrives one
    retransmission late, and Linux never retransmits sooner than its minimum
    timeout. Judged on jitter alone the measured Wi-Fi link looks calm — a 1.6 ms
    spread — and a budget that ignored its 10.4% loss proposed 280 ms for a link
    that needs the better part of a second.
    """
    lossy = compute_configuration(_canape_over_wifi()).config["buffer_ms"]
    clean = compute_configuration(_canape_over_wifi(loss_pct=0.0)).config["buffer_ms"]

    assert lossy - clean >= TCP_RTO_MIN_MS


def test_the_same_speaker_costs_more_over_its_wifi_than_its_cable():
    """Canapé is reachable on both interfaces at once, so this compares one
    machine against itself: same CPU, same DSP, only the link differs. The
    analysis is worth building only if it separates these two."""
    wired = compute_configuration(_wired_fleet()).config
    wifi = compute_configuration(_canape_over_wifi()).config

    assert wifi["buffer_ms"] > 3 * wired["buffer_ms"]
    assert wifi["codec"] == "flac" and wired["codec"] == "pcm"


def test_nothing_below_the_value_a_human_confirmed_is_ever_offered():
    """The model's constants are fitted to two measured points in one house.

    On a topology it has never seen it can be wrong, and wrong low is the
    direction that costs audio rather than latency. A network measuring as
    flawless must therefore still not drag the proposal under the smallest value
    anyone confirmed by listening.
    """
    flawless = [ClientReading("m", "Flawless", "ethernet", 1000, 0.0, 0.0, 0.0, 0.0)]

    buffer_ms = compute_configuration(flawless).config["buffer_ms"]

    assert buffer_ms >= VERIFIED_FLOOR_MS


def test_the_analysis_does_not_read_the_canned_presets():
    """The computed configuration must come from measurements alone.

    The safety floor briefly derived from `NETWORK_PRESETS`, which shares its
    value today -- so editing or deleting a preset silently moved the floor of
    an analysis that had measured nothing different. The two are independent
    facts and must stay textually independent, or the coupling comes back the
    next time someone needs a number that happens to match.
    """
    source = (Path(__file__).resolve().parents[1]
              / "core" / "multiroom" / "calibration.py").read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )

    assert "NETWORK_PRESETS" not in code


@pytest.mark.parametrize("sched_ms", [0.0, 1.41, 1.42, 1.43, 1.9, 2.0, 2.1, 2.5])
def test_the_client_buffer_has_no_cliff_next_to_a_measured_value(sched_ms):
    """A threshold beside a real reading makes the proposal irreproducible.

    The server measures ~1.42 ms of scheduling tail. When this mapping was a
    step table with a 2 ms boundary, the same house answered 60 ms or 120 ms
    depending on what the backend happened to be doing that second -- and the
    120 ms one carried an extra 120 ms of latency all the way to the speakers.
    Crossing that region must now change nothing.
    """
    fleet = _wired_fleet()
    fleet[1].sched_max_ms = sched_ms

    config = compute_configuration(fleet).config

    assert config["snapclient_buffer_time"] == SNAPCLIENT_LIMITS["buffer_time"][0]
    assert config["buffer_ms"] == MEASURED_REFERENCE["buffer_ms"]


def test_a_client_that_wakes_late_is_given_a_larger_alsa_buffer():
    """The floor must not swallow the signal: a machine whose playback thread is
    genuinely late needs a buffer that survives it, or it underruns locally
    whatever the network does."""
    fleet = _wired_fleet()
    fleet[1].sched_max_ms = 25.0

    config = compute_configuration(fleet).config

    assert config["snapclient_buffer_time"] > SNAPCLIENT_LIMITS["buffer_time"][0]
    assert config["snapclient_buffer_time"] <= SNAPCLIENT_LIMITS["buffer_time"][1]
