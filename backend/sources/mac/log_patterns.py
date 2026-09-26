# backend/sources/mac/log_patterns.py
"""ROC receiver (roc-recv) log-line patterns — the scraped wire contract.

roc-toolkit exposes no D-Bus/API, so Mac connection state is derived from
roc-recv's journal output. These constants pin the exact substrings we match,
so a change is a deliberate one-line edit guarded by the golden samples in
test_mac_log_patterns.py — not a silent break scattered through source.py.
"""
import re
from dataclasses import dataclass
from typing import Optional, Tuple

# roc-recv session lifecycle markers (substring match against a journal line).
ROC_DISCONNECT_MARKERS = ("removing route", "removing address")
ROC_SESSION_CREATE_MARKER = "session group: creating session"
ROC_ROUTE_CREATE_MARKERS = ("creating", "route", "address=")  # all must be present

# IPv4/IPv6 (+optional %scope) address=/src_addr= extraction from a ROC log line.
IP_PORT_RE = re.compile(
    r'(?:address|src_addr)=\[(?P<ip6>[0-9A-Fa-f:.%]+)\]:(?P<port>\d+)'
    r'|'
    r'(?:address|src_addr)=(?P<ip4>\d{1,3}(?:\.\d{1,3}){3}):(?P<port4>\d+)'
)


def parse_ip_from_line(line: str) -> Tuple[Optional[str], Optional[int]]:
    """Extract (ip, port) from a ROC log line, or (None, None)."""
    m = IP_PORT_RE.search(line)
    if not m:
        return None, None
    if m.group('ip6'):
        return m.group('ip6'), int(m.group('port'))
    if m.group('ip4'):
        return m.group('ip4'), int(m.group('port4'))
    return None, None


def normalize_ip(ip: Optional[str]) -> Optional[str]:
    """Clean brackets and preserve %scope for IPv6."""
    if not ip:
        return None
    return ip.strip('[]')


def classify_line(line: str) -> Tuple[Optional[str], Optional[str], Optional[int]]:
    """Classify a ROC log line into a connection event.

    Returns (event, ip, port) where event is:
      - "disconnect" for route/address removal,
      - "connect" for a new session or route,
      - None for anything else (ip/port also None).
    ip is normalized (brackets stripped, %scope preserved).
    """
    if any(marker in line for marker in ROC_DISCONNECT_MARKERS):
        ip, port = parse_ip_from_line(line)
        return "disconnect", normalize_ip(ip), port

    if ROC_SESSION_CREATE_MARKER in line:
        ip, port = parse_ip_from_line(line)
        return "connect", normalize_ip(ip), port

    if all(marker in line for marker in ROC_ROUTE_CREATE_MARKERS):
        ip, port = parse_ip_from_line(line)
        return "connect", normalize_ip(ip), port

    return None, None, None


# --- Link statistics, read by the Mac link calibration ----------------------
# roc-recv prints these at Debug level, which is why milo-mac.service runs it
# with -vv. Every figure below is roc-toolkit 0.4.0's own; the `jitter` field of
# the tuner line is not parsed because 0.4.0 never fills it (always 0).

ROC_SESSION_END_MARKER = "session group: removing session"

# The tuner's first line of a session reports all zeros, fe included: nothing
# has been measured yet. A negative niq is real — the queue ran dry.
TUNER_RE = re.compile(
    r'latency tuner: e2e_latency=-?\d+\(-?[\d.]+ms\) '
    r'niq_latency=-?\d+\((?P<niq>-?[\d.]+)ms\) '
    r'target_latency=\d+\((?P<target>[\d.]+)ms\) .*?'
    r'stale=-?\d+\((?P<stale>-?[\d.]+)ms\) fe=(?P<fe>[\d.]+)'
)

# Cumulative since the session started, in samples, and counted after FEC:
# what is still missing once repair has done what it could.
LOSS_RATIO_RE = re.compile(r'depacketizer: ts=\d+ loss_ratio=(?P<ratio>[\d.]+)')

PAYLOAD_SIZE_RE = re.compile(r'fec reader: update payload size: .*new_size=(?P<size>\d+)')
SOURCE_BLOCK_RE = re.compile(r'fec reader: update source block size: .*new_sblen=(?P<sblen>\d+)')

# roc-vad sends L16 stereo at 44.1 kHz behind a 12-byte RTP header, so a
# payload of N bytes carries (N - 12) / 4 samples.
RTP_HEADER_BYTES = 12
L16_STEREO_FRAME_BYTES = 4
ROC_PAYLOAD_RATE_HZ = 44100

# The Debug line roc logs just before `removing session`, which carries no
# reason of its own. `no_playback` is also how an ordinary departure ends.
SESSION_END_CAUSES = (
    ("latency tuner: latency out of bounds", "latency_out_of_bounds"),
    ("watchdog: choppy_playback timeout reached", "choppy_playback"),
    ("watchdog: no_playback timeout reached", "no_playback"),
)


@dataclass(frozen=True)
class TunerSample:
    """One 5-second snapshot of the receiver's queue."""
    niq_ms: float
    stale_ms: float
    target_ms: float


def parse_tuner(line: str) -> Optional[TunerSample]:
    """A latency-tuner snapshot, or None (other lines, and the empty first one)."""
    m = TUNER_RE.search(line)
    if not m or float(m.group('fe')) == 0.0:
        return None
    return TunerSample(float(m.group('niq')), float(m.group('stale')), float(m.group('target')))


def parse_loss_ratio(line: str) -> Optional[float]:
    m = LOSS_RATIO_RE.search(line)
    return float(m.group('ratio')) if m else None


def parse_packet_ms(line: str) -> Optional[float]:
    """The sender's packet length, from the first payload of a session."""
    m = PAYLOAD_SIZE_RE.search(line)
    if not m:
        return None
    samples = (int(m.group('size')) - RTP_HEADER_BYTES) / L16_STEREO_FRAME_BYTES
    return samples * 1000 / ROC_PAYLOAD_RATE_HZ


def parse_source_block(line: str) -> Optional[int]:
    """The sender's FEC source packets per block (nbsrc)."""
    m = SOURCE_BLOCK_RE.search(line)
    return int(m.group('sblen')) if m else None


def is_session_start(line: str) -> bool:
    return ROC_SESSION_CREATE_MARKER in line


def is_session_end(line: str) -> bool:
    return ROC_SESSION_END_MARKER in line


def parse_session_end_cause(line: str) -> Optional[str]:
    """Why roc is about to remove a session, when this line is the reason."""
    for marker, cause in SESSION_END_CAUSES:
        if marker in line:
            return cause
    return None
