# backend/tests/test_mac_link_calibration.py
"""The Mac link model: one measurement in, both halves of the ROC link out.

What breaks when these fail: the Mac panel's "Set automatically" offers a
configuration the receiver cannot hold (too low a target), one the sender does
not need (interleaving on a clean link, which costs a whole FEC block of
latency), or one the PUT route refuses outright.
"""
import pytest

from backend.api.models import MacRocConfigRequest
from backend.core.multiroom.calibration_probe import PingReading
from backend.core.mac_link.calibration import (
    LOWEST_TARGET_MS,
    MacLinkReading,
    compute_mac_link_configuration,
)
from backend.sources.mac.log_patterns import TunerSample

# The link as it was stored when the owner's Mac mini was measured.
STORED = {
    "target_latency_ms": 70, "latency_profile": "gradual", "frame_length_ms": 6,
    "packet_length_ms": 3, "fec_block_source": 10, "fec_block_repair": 5,
    "packet_interleaving": False,
}

CLEAN_WIRE = PingReading(p50_ms=0.29, max_ms=0.37, loss_pct=0.0, longest_loss_run=0)


def samples(niq_values, stale_values, target=70.0):
    return [TunerSample(niq_ms=n, stale_ms=s, target_ms=target) for n, s in zip(niq_values, stale_values)]


# 2026-09-26, interleaving off, 70 ms target: the queue ran 56.9 to 83.2 ms and
# the newest packet never went staler than 7.3 ms.
MAC_MINI = samples(
    [69.1, 56.9, 81.9, 83.2, 64.8, 70.4, 61.4, 75.5, 68.7, 67.4, 72.0, 66.3],
    [2.6, 6.9, 4.8, 3.0, 7.3, 1.2, 0.4, 5.5, 2.9, 4.1, 3.3, 6.1],
)


def reading(**overrides):
    base = dict(mac_name="Mac mini de Léo", ping=CLEAN_WIRE, samples=MAC_MINI,
                sched_max_ms=1.42, stored=dict(STORED),
                observed_packet_ms=2.99, observed_fec_source=10, post_fec_loss_ratio=0.0)
    base.update(overrides)
    return MacLinkReading(**base)


def test_the_wired_mac_mini_measured_on_2026_09_26():
    """The one measured point: a swing of 13 ms and a clean wire ask for 30 ms,
    no interleaving, and the short frame this unit's scheduling allows. The
    estimate drops from 117 to 62 ms — 40 of it the queue, 15 the ALSA buffer
    roc-recv opens and roc never counts."""
    result = compute_mac_link_configuration(reading())

    assert result.config == {
        "target_latency_ms": 30, "latency_profile": "gradual", "frame_length_ms": 4,
        "packet_length_ms": 3, "fec_block_source": 10, "fec_block_repair": 5,
        "packet_interleaving": False,
    }
    assert result.predicted_latency_ms == {"current": 117, "proposed": 62}
    assert result.assumed == []


def test_losses_in_runs_turn_interleaving_on_and_budget_its_hold():
    """Interleaving holds packets for up to one FEC block on the Mac, and the
    queue has to cover it: turning it on without raising the target is a
    receiver that starves on every block."""
    bursty = PingReading(p50_ms=3.0, max_ms=9.0, loss_pct=2.0, longest_loss_run=3)
    clean = compute_mac_link_configuration(reading())
    result = compute_mac_link_configuration(reading(ping=bursty))

    assert result.config["packet_interleaving"] is True
    assert result.config["target_latency_ms"] >= clean.config["target_latency_ms"] + 2 * 30


def test_scattered_losses_keep_interleaving_off_but_budget_the_fec_block():
    """One probe lost at a time is what FEC repairs without help — but only if
    the queue holds a whole block when the repair packets land."""
    scattered = PingReading(p50_ms=0.29, max_ms=0.37, loss_pct=1.0, longest_loss_run=1)
    result = compute_mac_link_configuration(reading(ping=scattered))

    assert result.config["packet_interleaving"] is False
    assert result.config["target_latency_ms"] >= 2 * 30


def test_an_interleaving_sender_hides_its_burst_so_it_is_assumed_and_declared():
    """With interleaving on, the queue swings by whole blocks and the Mac's own
    I/O burst cannot be read from it. The value stood in for is said to be one."""
    result = compute_mac_link_configuration(
        reading(stored={**STORED, "packet_interleaving": True})
    )
    assert result.assumed == ["mac_burst"]


def test_a_sender_that_does_not_run_the_stored_link_is_named_not_measured():
    """A Milo-Mac that has not applied Milō's sender half (an older build) sends
    what the stream shows, not what is stored: its swing describes a sender the
    proposal will not configure."""
    result = compute_mac_link_configuration(reading(observed_packet_ms=5.0))

    codes = [code for code, _ in result.reasons]
    assert "sender_mismatch" in codes
    assert result.assumed == ["mac_burst"]


def test_a_perfect_link_still_stops_at_the_floor():
    """The measured swing on a flawless link would propose less than anyone has
    heard play cleanly; the floor is the listened-to value, not the model's."""
    calm = samples([69.5, 70.2, 70.4, 69.8], [0.5, 0.8, 0.3, 0.6])
    result = compute_mac_link_configuration(reading(samples=calm))
    assert result.config["target_latency_ms"] == LOWEST_TARGET_MS


def test_the_frame_follows_the_scheduling_tail_without_flipping_on_ordinary_noise():
    """The short frame cuts the ALSA buffer roc-recv keeps full from 44 to 29 ms.
    This unit measured 0.09 and 2.83 ms of scheduling half an hour apart: both
    are ordinary and must give the same frame. A unit whose threads wake 5 ms
    late needs the longer buffer."""
    frame = lambda sched: compute_mac_link_configuration(reading(sched_max_ms=sched)).config["frame_length_ms"]
    assert frame(0.09) == frame(2.83) == 4
    assert frame(5.0) == 6


@pytest.mark.parametrize("ping", [
    CLEAN_WIRE,
    PingReading(p50_ms=4.0, max_ms=180.0, loss_pct=8.0, longest_loss_run=6),
    PingReading(p50_ms=90.0, max_ms=900.0, loss_pct=30.0, longest_loss_run=20),
], ids=["wired", "poor_wifi", "hopeless"])
def test_every_proposal_is_a_body_the_put_route_accepts(ping):
    """The panel applies the proposal through PUT /api/settings/mac-roc as-is;
    a value outside that model's bounds would come back as a 422 on Apply."""
    MacRocConfigRequest(**compute_mac_link_configuration(reading(ping=ping)).config)


def test_every_decision_carries_a_reason_code_and_no_prose():
    result = compute_mac_link_configuration(reading())
    assert result.reasons
    for code, params in result.reasons:
        assert code.replace("_", "").isalnum() and code == code.lower()
        assert isinstance(params, dict)


def test_a_reading_without_queue_samples_is_refused():
    with pytest.raises(ValueError):
        compute_mac_link_configuration(reading(samples=[]))


def test_one_lost_probe_in_three_hundred_does_not_move_the_proposal():
    """0.33 % is ICMP noise, not a link that needs FEC to repair in time: it
    moved the proposal by 60 ms between two runs minutes apart on a clean wire."""
    one_lost = PingReading(p50_ms=0.29, max_ms=0.37, loss_pct=100 / 300, longest_loss_run=1)
    assert (compute_mac_link_configuration(reading(ping=one_lost)).config
            == compute_mac_link_configuration(reading()).config)


def test_the_current_estimate_describes_the_sender_the_stream_shows():
    """A Mac that has not applied Milō's sender half (an older Milo-Mac, 5 ms
    packets) sends what the stream shows; 'now' is that link, not the stored one."""
    older_app = compute_mac_link_configuration(reading(observed_packet_ms=5.0, observed_fec_source=18))
    as_stored = compute_mac_link_configuration(reading())
    assert older_app.predicted_latency_ms["current"] == as_stored.predicted_latency_ms["current"] + 2
