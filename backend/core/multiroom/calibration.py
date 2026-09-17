# backend/core/multiroom/calibration.py
"""Derive a Snapcast server configuration from measurements of this house.

A canned configuration is somebody's guess about a house they have not seen;
this module computes the four parameters from what this fleet actually
measures. It is pure:
it takes one reading per client and returns a configuration plus the reasoning
behind it, so the UI can show *why* a value was chosen and a test can assert the
decision without a network.

The model is a budget. A chunk has to survive, in order: assembly at the server
(``chunk_ms``), the encoder's frame, transit and its jitter, and the client's own
ALSA buffer -- and the server must promise a playback instant covering all of it:

    buffer_ms = SAFETY x (chunk_ms + encoder + snapclient_buffer_time
                          + jitter_tail + SYNC_RESIDUAL_MS)

rounded up onto a coarse grid. Fed the readings of a wired gigabit fleet it
lands on 180 ms, the configuration confirmed by ear on 2026-09-17 -- the one
external check this model has, pinned by
``test_wired_gigabit_fleet_reproduces_the_configuration_confirmed_by_ear``.

Two rules are what make a *computed* value safe to offer, where the alternative
was to hand the user a canned guess and hope:

1. Every output is clamped to the range its own validator enforces
   (``SnapcastService._validate_config``, ``SNAPCLIENT_LIMITS``), so the result
   can never be a payload the write path would reject.
2. ``buffer_ms`` can never fall below the structural terms plus
   ``FLOOR_MARGIN_MS``. The measured network term is tiny on a healthy LAN --
   around 3 ms against a 180 ms answer -- so without a floor a clean measurement
   would propose a buffer no amount of network quality can make work.

Reasons are emitted as ``(code, params)`` pairs, never as prose: the strings the
user reads are i18n keys resolved in the frontend.

What the caller still owes the user: this is a prediction. Nothing here observes
audio, so nothing here can promise the result plays cleanly.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Tuple

# Restating a bound that lives elsewhere is what this module must not do, so
# every range is imported from whichever module enforces it. One that moves
# there moves here.
from backend.core.multiroom.routing import SNAPCLIENT_LIMITS
from backend.core.multiroom.snapcast import BUFFER_MS_RANGE, CHUNK_MS_RANGE

# The lowest buffer anyone has confirmed by listening: 180 ms, measured on
# 2026-09-17 across three wired speakers, playing for 5 min 40 s with no
# underrun, no xrun and no client disconnect.
#
# Declared here and nowhere else. It was briefly read out of NETWORK_PRESETS,
# which shares the value today -- and that was the wrong shape: the analysis
# must derive every parameter from what it measured, so editing or dropping a
# canned preset has to leave its floor exactly where it is. Two facts, one
# number, independent lifetimes.
#
# It exists because the model's constants are fitted to two measured points in
# one house. On a topology it has never seen -- a mesh repeater, a powerline
# bridge, five speakers on one access point -- it can be wrong, and wrong low is
# the direction that costs audio rather than latency. Raise it only against a
# new listening test, never against a calculation.
VERIFIED_FLOOR_MS = 180

# Uncompressed stereo at the pipeline's own format, 48000 x 32 bits x 2 channels.
PCM_BITRATE_MBPS = 3.072

# Encoder frame latency in milliseconds. PCM has none; the others carry the frame
# their decoder needs before it can emit a sample.
ENCODER_LATENCY_MS = {"pcm": 0, "flac": 26, "opus": 20, "ogg": 26}

# Residual left by snapclient's clock sync once its resampler has converged.
# Measured on a satellite over 75 s of playback: reported sync error stayed
# within +/-1 ms.
SYNC_RESIDUAL_MS = 1

# A 60-second sample sees the network's typical behaviour and misses its rare
# spikes, so the observed spread is widened before the budget trusts it.
JITTER_TAIL_FACTOR = 4.0

# Snapcast carries audio over TCP, so a dropped packet is not a dropped chunk —
# it is a chunk that arrives one retransmission late. Linux clamps the
# retransmission timeout at 200 ms whatever the round trip, so on a lossy link
# that timeout, not the jitter, is what the buffer has to cover: a Wi-Fi client
# measured at 10.4% loss has a 5 ms round trip and a 1.6 ms jitter spread, and a
# budget reading only those two proposes 280 ms for a link that needs ~700.
# Below LOSS_FULL_PENALTY_PCT the penalty scales, so one lost probe in three
# hundred costs a few milliseconds rather than the whole timeout.
TCP_RTO_MIN_MS = 200
LOSS_FULL_PENALTY_PCT = 2.0

SAFETY_FACTOR = 2.0
FLOOR_MARGIN_MS = 60
BUFFER_GRID_MS = 20

# A link never carries its nominal rate. Ethernet is switched and full duplex, so
# half is already pessimistic; Wi-Fi is a shared half-duplex medium whose
# negotiated PHY rate overstates real throughput by roughly three.
LINK_USABLE_FRACTION = {"ethernet": 0.5, "wifi": 0.3}

# Above this one-way jitter, or with any measured loss, the network is judged
# unclean. Two decisions read that same judgement: the tighter chunk size, and
# PCM over FLAC.
CLEAN_NETWORK_JITTER_MS = 5.0

# The client's ALSA buffer drains while its playback thread is not scheduled, so
# it is sized from that thread's worst wake-up rather than from anything about
# the network -- network jitter is what `buffer_ms` absorbs, one stage upstream.
#
# Continuous on purpose. This was a table of steps (2 ms -> 60, 10 ms -> 120),
# and the server measured 1.42 ms: a hair under a threshold whose crossing
# doubled the buffer and added 120 ms of latency in one jump. A cliff that close
# to a measured value makes the proposal depend on what the machine happened to
# be doing, which is the same irreproducibility the sampler itself had.
#
# The factor extrapolates a tail the way JITTER_TAIL_FACTOR does: the reading is
# the worst of 400 samples, and the buffer has to survive an evening. At 8x it
# reproduces the hand-calibrated ladder -- 14 ms of scheduling asks for 120,
# 25 ms for 200 -- while anything healthy sits on the floor.
SCHED_TAIL_FACTOR = 8.0
BUFFER_TIME_GRID_MS = 20


@dataclass
class ClientReading:
    """One client's measurements, in the shape the probes return them.

    ``rtt_*`` are round trips in milliseconds; the model halves their spread to
    get a one-way term. ``sched_max_ms`` is the worst overshoot of a
    fixed-interval sleep on that client, standing in for how late its audio
    thread can be woken.
    """

    mac_id: str
    name: str
    link: Literal["ethernet", "wifi"]
    link_speed_mbps: float
    rtt_p50_ms: float
    rtt_max_ms: float
    loss_pct: float
    sched_max_ms: float
    signal_percent: Optional[float] = None
    is_local: bool = False


@dataclass
class CalibrationResult:
    """The computed configuration, together with what produced it.

    ``config`` is exactly the body ``PUT /api/routing/snapcast/server-config``
    takes. ``reasons`` carries one ``(code, params)`` per decision: a computed
    number the user cannot interrogate is worse than a preset with a name.
    """

    config: Dict[str, object]
    predicted_latency_ms: int
    limiting_client: Optional[str]
    reasons: List[Tuple[str, Dict[str, object]]] = field(default_factory=list)
    budget_ms: Dict[str, float] = field(default_factory=dict)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _one_way_jitter_ms(reading: ClientReading) -> float:
    """This client's round-trip spread, halved and widened.

    Halved because a chunk travels one way. Widened by ``JITTER_TAIL_FACTOR``
    because the sample lasts a minute and the buffer has to cover an evening.
    """
    spread = max(0.0, reading.rtt_max_ms - reading.rtt_p50_ms)
    return spread / 2.0 * JITTER_TAIL_FACTOR


def _is_clean(worst_jitter_ms: float, worst_loss_pct: float) -> bool:
    return worst_jitter_ms <= CLEAN_NETWORK_JITTER_MS and worst_loss_pct == 0.0


def _pick_codec(
    readings: List[ClientReading],
    quality: str,
    worst_jitter_ms: float,
    worst_loss_pct: float,
) -> Tuple[str, Tuple[str, Dict]]:
    """Choose the codec and say why.

    Lossless takes PCM only when the weakest link is both *wide* enough for every
    client's uncompressed stream and *clean*. Width alone is not enough: PCM puts
    twice FLAC's bytes on the wire, and on a link that already drops packets the
    extra traffic buys retransmissions. A single speaker on a 72 Mbit/s Wi-Fi has
    ample room for 3 Mbit/s and still wants the smaller stream.

    It falls back to FLAC, never to a lossy codec: "lossless" is the user's
    instruction, not a hint.
    """
    if quality == "economical":
        return "opus", ("codec_economical", {})

    remote = [r for r in readings if not r.is_local]
    needed = PCM_BITRATE_MBPS * max(1, len(remote))
    weakest = min(remote, key=lambda r: r.link_speed_mbps * LINK_USABLE_FRACTION[r.link])
    usable = weakest.link_speed_mbps * LINK_USABLE_FRACTION[weakest.link]
    params = {"needed": round(needed, 1), "usable": round(usable), "client": weakest.name}

    if needed > usable:
        return "flac", ("codec_flac_too_narrow", params)
    if not _is_clean(worst_jitter_ms, worst_loss_pct):
        # One string for both triggers, naming both numbers. It used to say the
        # link "drops packets ({loss} %)" whatever the cause, so a link failing
        # on jitter alone was explained by a loss of 0 % printed next to a table
        # showing no loss at all.
        return "flac", ("codec_flac_unstable", {
            "jitter": round(worst_jitter_ms, 1), "loss": round(worst_loss_pct, 2)
        })
    return "pcm", ("codec_pcm_fits", params)


def _pick_chunk_ms(worst_jitter_ms: float, worst_loss_pct: float) -> Tuple[int, Tuple[str, Dict]]:
    if _is_clean(worst_jitter_ms, worst_loss_pct):
        return 20, ("chunk_tight", {})
    return 40, (
        "chunk_relaxed",
        {"jitter": round(worst_jitter_ms, 1), "loss": round(worst_loss_pct, 2)},
    )


def _pick_buffer_time_ms(worst_sched_ms: float) -> Tuple[int, Tuple[str, Dict]]:
    low, high = SNAPCLIENT_LIMITS["buffer_time"]
    needed = SCHED_TAIL_FACTOR * worst_sched_ms
    rounded = BUFFER_TIME_GRID_MS * -(-needed // BUFFER_TIME_GRID_MS)
    value = int(_clamp(rounded, low, high))
    return value, ("client_alsa_buffer", {"value": value, "sched": round(worst_sched_ms, 2)})


def compute_configuration(
    readings: List[ClientReading],
    quality: Literal["lossless", "economical"] = "lossless",
) -> CalibrationResult:
    """Turn one reading per client into the configuration to propose.

    Raises ValueError when no remote client was measured: the buffer exists to
    absorb a network and with only the local speaker there is none, so the caller
    answers that case without running an analysis at all.
    """
    remote = [r for r in readings if not r.is_local]
    if not remote:
        raise ValueError("calibration needs at least one remote client")

    worst_jitter = max(_one_way_jitter_ms(r) for r in remote)
    worst_loss = max(r.loss_pct for r in remote)
    worst_sched = max(r.sched_max_ms for r in readings)
    limiting = max(remote, key=_one_way_jitter_ms).name

    codec, codec_why = _pick_codec(readings, quality, worst_jitter, worst_loss)
    chunk_ms, chunk_why = _pick_chunk_ms(worst_jitter, worst_loss)
    buffer_time, buffer_time_why = _pick_buffer_time_ms(worst_sched)

    encoder_ms = ENCODER_LATENCY_MS[codec]
    retransmit_ms = TCP_RTO_MIN_MS * min(1.0, worst_loss / LOSS_FULL_PENALTY_PCT)
    structural = chunk_ms + encoder_ms + buffer_time
    raw = structural + worst_jitter + retransmit_ms + SYNC_RESIDUAL_MS

    buffer_ms = SAFETY_FACTOR * raw
    buffer_ms = BUFFER_GRID_MS * -(-buffer_ms // BUFFER_GRID_MS)
    buffer_ms = max(buffer_ms, structural + FLOOR_MARGIN_MS, VERIFIED_FLOOR_MS)
    buffer_ms = int(_clamp(buffer_ms, *BUFFER_MS_RANGE))

    return CalibrationResult(
        config={
            "buffer_ms": buffer_ms,
            "codec": codec,
            "chunk_ms": int(_clamp(chunk_ms, *CHUNK_MS_RANGE)),
            "snapclient_buffer_time": buffer_time,
        },
        predicted_latency_ms=buffer_ms,
        limiting_client=limiting,
        reasons=[
            codec_why,
            chunk_why,
            buffer_time_why,
            ("buffer_budget", {"buffer": buffer_ms, "structural": structural,
                               "jitter": round(worst_jitter, 1),
                               "retransmit": round(retransmit_ms),
                               "safety": SAFETY_FACTOR}),
        ],
        budget_ms={
            "chunk": float(chunk_ms),
            "encoder": float(encoder_ms),
            "client_alsa": float(buffer_time),
            "jitter_tail": round(worst_jitter, 2),
            "retransmit": round(retransmit_ms, 2),
            "sync_residual": float(SYNC_RESIDUAL_MS),
        },
    )
