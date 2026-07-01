# backend/sources/mac/log_patterns.py
"""ROC receiver (roc-recv) log-line patterns — the scraped wire contract.

roc-toolkit exposes no D-Bus/API, so Mac connection state is derived from
roc-recv's journal output. These constants pin the exact substrings we match,
so a change is a deliberate one-line edit guarded by the golden samples in
test_mac_log_patterns.py — not a silent break scattered through source.py.
"""
import re
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
