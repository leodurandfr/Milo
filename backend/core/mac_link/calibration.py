# backend/core/mac_link/calibration.py
"""Derive both halves of the Mac's ROC link from one measurement of it.

The link has two ends configured in two places: roc-recv here (target latency,
profile, frame length) and roc-vad on the Mac (packet length, FEC block,
interleaving), which Milo-Mac applies from Milō's settings. Each end's needs
depend on the other's choices, so one function decides them together.

The measurement has three terms, each read where it lives, none of them
requiring a setting to change:

* the network, by ICMP from here to the Mac: round-trip spread, loss, and
  whether losses come in runs;
* the Mac's stream, from roc-recv's own queue as it plays: how far the queue
  swings around its target and how long the newest packet goes stale — the
  CoreAudio I/O bursts the Mac sends in, plus anything the sender adds;
* this unit's scheduling, as the Snapcast analysis measures it.

The receiver's target is then a budget, like the Snapcast buffer:

    target = SAFETY x (mac_burst + network_jitter + max(interleave_hold, fec_span))

rounded up onto a grid, floored at LOWEST_TARGET_MS. Fed the owner's wired Mac
mini as measured on 2026-09-26 it proposes 30 ms, pinned by
``test_the_wired_mac_mini_measured_on_2026_09_26``.

roc-toolkit 0.4.0 facts this rests on, read from its source:

* the tuner's `jitter` field and RTCP loss are never filled — ICMP is the only
  measure of either;
* the receiver's endpoint fixes the FEC scheme, so the sender half has no
  scheme to choose;
* interleaving holds packets on the sender for up to one FEC block, and
  repairing a block's first packet needs at least one block in the queue;
* the ALSA buffer roc-recv's SoX sink opens is 8 periods of one frame, and
  roc counts it nowhere — not in the queue, not in its e2e figure.

It proposes: nothing here writes. What it cannot promise is how the result
sounds — the floor is the only value a person has listened to.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from backend.config.constants import ROC_TARGET_LATENCY_RANGE
from backend.core.multiroom.calibration import SCHED_TAIL_FACTOR, grid_up, one_way_jitter_ms
from backend.core.multiroom.calibration_probe import PingReading
from backend.sources.mac.log_patterns import TunerSample

# The lowest target the analysis proposes. At 30 ms roc-recv's queue survives
# the owner's wired Mac with twice the swing it was measured to have; below it
# roc 0.4 switches its own default to the `responsive` tuner and warns that
# `gradual` oscillates (latency_tuner.cpp:30-43), and nobody has listened to
# anything lower. Lowered only against a listening test, never a calculation.
LOWEST_TARGET_MS = 30

SAFETY_FACTOR = 2.0
TARGET_GRID_MS = 5

# The sender values heard working on this appliance (the owner's Mac mini,
# 2026-09-26). The analysis keeps them rather than inventing others: packet
# length and block size only move a network term the measurement cannot see.
VERIFIED_PACKET_LENGTH_MS = 3
VERIFIED_FEC_SOURCE = 10
VERIFIED_FEC_REPAIR = 5

# Two probes lost back to back, or more: a link that drops bursts, which FEC
# repairs only if interleaving spreads the burst across blocks.
BURST_LOSS_RUN = 2

# Below this share of probes lost, a loss is ICMP noise rather than a link that
# needs FEC to repair in time: one probe in 300 is 0.33 %, and it moved the
# proposal by 60 ms between two runs minutes apart on a clean wire.
LOSSY_PING_PCT = 1.0

# CoreAudio's usual I/O buffer, 512 frames at 44.1 kHz. It stands in for the
# Mac's burst whenever the measured one describes a sender that is not the one
# being proposed (an interleaving sender hides it behind whole blocks).
ASSUMED_MAC_BURST_MS = 512 * 1000 / 44100

# roc-recv's SoX sink asks ALSA for 8 periods of one frame and keeps it full.
# Measured 2026-09-26 on the ROC loopback: frame 2/4/6/8/12 ms gave a buffer of
# 14.7/29.3/44.2/58.8/88.2 ms at 48 kHz — 7.35 ms per millisecond of frame.
SINK_MS_PER_FRAME_MS = 7.35

# Frame lengths the analysis picks between: 4 ms when the buffer it opens covers
# this unit's scheduling tail — the Snapcast analysis's own criterion for an ALSA
# buffer, SCHED_TAIL_FACTOR times the worst wake-up — else the 6 ms roc-recv has
# always run. An extra halving here made the choice flip between two analyses
# half an hour apart (0.09 then 2.83 ms of scheduling, 2026-09-26), both far
# inside what a 29 ms buffer absorbs.
SHORT_FRAME_MS = 4
LONG_FRAME_MS = 6


@dataclass
class MacLinkReading:
    """Everything one analysis measured, plus the link Milō has stored."""
    mac_name: str
    ping: PingReading
    samples: List[TunerSample]
    sched_max_ms: float
    stored: Dict[str, object]
    # From the stream itself: what the Mac actually sends.
    observed_packet_ms: Optional[float] = None
    observed_fec_source: Optional[int] = None
    # After FEC: the latest cumulative ratio, and whether it rose during the
    # last minutes read — a loss hours ago says nothing about the link now.
    post_fec_loss_ratio: Optional[float] = None
    post_fec_loss_rising: bool = False
    # Sessions roc ended for being out of bounds or choppy, since it started.
    unstable_ends: int = 0


@dataclass
class MacLinkResult:
    """``config`` is exactly the body ``PUT /api/settings/mac-roc`` takes."""
    config: Dict[str, object]
    predicted_latency_ms: Dict[str, int]
    measurements: Dict[str, object]
    assumed: List[str] = field(default_factory=list)
    reasons: List[Tuple[str, Dict[str, object]]] = field(default_factory=list)


def sink_buffer_ms(frame_length_ms: int) -> float:
    return SINK_MS_PER_FRAME_MS * frame_length_ms


def estimated_latency_ms(config: Dict[str, object]) -> int:
    """What a link configured this way adds, from the Mac's write to this
    unit's output: one packet, the interleaver's hold, the queue, the sink."""
    packet = float(config["packet_length_ms"])
    hold = packet * int(config["fec_block_source"]) if config["packet_interleaving"] else 0.0
    return round(packet + hold + float(config["target_latency_ms"]) + sink_buffer_ms(int(config["frame_length_ms"])))


def _mac_burst(reading: MacLinkReading, assumed: List[str],
               reasons: List[Tuple[str, Dict[str, object]]]) -> float:
    """How far the Mac's stream swings the queue, when it can be told."""
    mismatch = (
        (reading.observed_packet_ms is not None
         and round(reading.observed_packet_ms) != reading.stored["packet_length_ms"])
        or (reading.observed_fec_source is not None
            and reading.observed_fec_source != reading.stored["fec_block_source"])
    )
    if mismatch:
        reasons.append(("sender_mismatch", {
            "packet_observed": round(reading.observed_packet_ms or 0, 1),
            "packet_stored": reading.stored["packet_length_ms"],
            "source_observed": reading.observed_fec_source,
            "source_stored": reading.stored["fec_block_source"],
        }))
    if mismatch or reading.stored["packet_interleaving"]:
        assumed.append("mac_burst")
        reasons.append(("burst_assumed", {"burst": round(ASSUMED_MAC_BURST_MS, 1)}))
        return ASSUMED_MAC_BURST_MS

    target = reading.samples[-1].target_ms
    burst = max(
        max(s.stale_ms for s in reading.samples),
        target - min(s.niq_ms for s in reading.samples),
        max(s.niq_ms for s in reading.samples) - target,
    )
    reasons.append(("burst_measured", {"burst": round(burst, 1), "samples": len(reading.samples)}))
    return burst


def _running_link(reading: MacLinkReading) -> Dict[str, object]:
    """The link as it runs now: what the stream shows (roc-recv's target, the
    sender's packet and block) over what is stored. Interleaving is the one
    sender setting the receiver cannot see, so the stored value stands."""
    running = dict(reading.stored)
    running["target_latency_ms"] = reading.samples[-1].target_ms
    if reading.observed_packet_ms is not None:
        running["packet_length_ms"] = round(reading.observed_packet_ms)
    if reading.observed_fec_source is not None:
        running["fec_block_source"] = reading.observed_fec_source
    return running


def compute_mac_link_configuration(reading: MacLinkReading) -> MacLinkResult:
    """Turn one measurement of the link into both halves of its configuration.

    Raises ValueError without tuner samples: the queue is the one term only a
    playing stream can show, and the caller waits for it before calling.
    """
    if not reading.samples:
        raise ValueError("the link calibration needs roc-recv's queue samples")

    assumed: List[str] = []
    reasons: List[Tuple[str, Dict[str, object]]] = []

    interleave = reading.ping.longest_loss_run >= BURST_LOSS_RUN
    block_ms = VERIFIED_PACKET_LENGTH_MS * VERIFIED_FEC_SOURCE
    reasons.append(("interleaving_on" if interleave else "interleaving_off",
                    {"run": reading.ping.longest_loss_run}))

    burst = _mac_burst(reading, assumed, reasons)
    jitter = one_way_jitter_ms(reading.ping.p50_ms, reading.ping.max_ms)
    lossy = reading.ping.loss_pct >= LOSSY_PING_PCT or reading.post_fec_loss_rising
    hold = block_ms if interleave else 0
    fec_span = block_ms if lossy else 0
    raw = burst + jitter + max(hold, fec_span)

    low, high = ROC_TARGET_LATENCY_RANGE
    target = max(grid_up(SAFETY_FACTOR * raw, TARGET_GRID_MS), LOWEST_TARGET_MS)
    target = int(max(low, min(high, target)))
    reasons.append(("target_budget", {
        "target": target, "burst": round(burst, 1), "jitter": round(jitter, 1),
        "block": max(hold, fec_span), "safety": SAFETY_FACTOR,
    }))

    short_buffer = sink_buffer_ms(SHORT_FRAME_MS)
    frame = SHORT_FRAME_MS if SCHED_TAIL_FACTOR * reading.sched_max_ms <= short_buffer else LONG_FRAME_MS
    reasons.append(("frame", {"frame": frame, "sched": round(reading.sched_max_ms, 2)}))

    if reading.unstable_ends:
        reasons.append(("unstable_current", {"ends": reading.unstable_ends}))

    config = {
        "target_latency_ms": target,
        # roc's own default at 30 ms and above (latency_tuner.cpp:30-43), which
        # LOWEST_TARGET_MS keeps every proposal at.
        "latency_profile": "gradual",
        "frame_length_ms": frame,
        "packet_length_ms": VERIFIED_PACKET_LENGTH_MS,
        "fec_block_source": VERIFIED_FEC_SOURCE,
        "fec_block_repair": VERIFIED_FEC_REPAIR,
        "packet_interleaving": interleave,
    }

    niq = [s.niq_ms for s in reading.samples]
    return MacLinkResult(
        config=config,
        predicted_latency_ms={
            "current": estimated_latency_ms(_running_link(reading)),
            "proposed": estimated_latency_ms(config),
        },
        measurements={
            "mac_name": reading.mac_name,
            "rtt_p50_ms": round(reading.ping.p50_ms, 3),
            "rtt_max_ms": round(reading.ping.max_ms, 3),
            "loss_pct": round(reading.ping.loss_pct, 2),
            "longest_loss_run": reading.ping.longest_loss_run,
            "mac_burst_ms": round(burst, 1),
            "queue_min_ms": round(min(niq), 1),
            "queue_max_ms": round(max(niq), 1),
            "samples": len(reading.samples),
            "post_fec_loss_pct": round(100 * (reading.post_fec_loss_ratio or 0), 3),
            "sched_max_ms": round(reading.sched_max_ms, 2),
            "unstable_ends": reading.unstable_ends,
        },
        assumed=assumed,
        reasons=reasons,
    )
