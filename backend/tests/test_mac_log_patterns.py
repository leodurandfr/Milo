# backend/tests/test_mac_log_patterns.py
"""Golden-sample guard for the ROC (roc-recv) log-line contract.

roc-toolkit exposes no D-Bus/API, so Mac connection state is scraped from
roc-recv's journal. These tests pin `classify_line` against verbatim sample
lines: an accidental edit to a marker constant fails loudly here, and any
refresh to match new upstream output forces a matching constant change in the
same commit (same discipline as milo_mac_contract).
"""
import pytest

from backend.sources.mac.log_patterns import (
    classify_line,
    parse_ip_from_line,
    normalize_ip,
)


# --- Golden verbatim roc-recv journal lines -------------------------------
# Captured from roc-recv output; keep these as-is when refreshing upstream.

CONNECT_SESSION_IPV4 = "session group: creating session address=192.168.1.100:10003"
CONNECT_ROUTE_IPV4 = "session router: creating route: address=192.168.1.100:10003"
CONNECT_ROUTE_IPV6 = "session router: creating route: address=[2001:db8::1]:10003"
# Scoped IPv6 uses a numeric interface index (what IP_PORT_RE's char class
# supports); an interface-name scope like %eth0 is intentionally NOT matched.
CONNECT_ROUTE_IPV6_LINKLOCAL = "session router: creating route: address=[fe80::1%1]:10003"

DISCONNECT_REMOVING_ROUTE = "session router: removing route: address=192.168.1.100:10003"
DISCONNECT_REMOVING_ADDRESS = "session router: removing address=192.168.1.100:10003"

NOISE_TRACE = "[trc] pipeline: refresh deadline=0"
NOISE_UNRELATED = "some random log line without an address"


class TestClassifyConnect:
    def test_session_create_ipv4(self):
        assert classify_line(CONNECT_SESSION_IPV4) == ("connect", "192.168.1.100", 10003)

    def test_route_create_ipv4(self):
        assert classify_line(CONNECT_ROUTE_IPV4) == ("connect", "192.168.1.100", 10003)

    def test_route_create_ipv6(self):
        assert classify_line(CONNECT_ROUTE_IPV6) == ("connect", "2001:db8::1", 10003)

    def test_route_create_ipv6_linklocal_preserves_scope(self):
        event, ip, port = classify_line(CONNECT_ROUTE_IPV6_LINKLOCAL)
        assert event == "connect"
        assert ip == "fe80::1%1"
        assert port == 10003


class TestClassifyDisconnect:
    def test_removing_route(self):
        assert classify_line(DISCONNECT_REMOVING_ROUTE) == ("disconnect", "192.168.1.100", 10003)

    def test_removing_address(self):
        assert classify_line(DISCONNECT_REMOVING_ADDRESS) == ("disconnect", "192.168.1.100", 10003)


class TestClassifyNonEvents:
    @pytest.mark.parametrize("line", [NOISE_TRACE, NOISE_UNRELATED, ""])
    def test_no_event(self, line):
        assert classify_line(line) == (None, None, None)

    def test_connect_marker_without_address_yields_no_ip(self):
        # Marker present but no parseable address -> event fires with ip=None,
        # and _process_log_line drops it (guards on ip).
        event, ip, port = classify_line("session group: creating session")
        assert event == "connect"
        assert ip is None and port is None


class TestParseHelpers:
    def test_parse_no_match(self):
        assert parse_ip_from_line("nothing here") == (None, None)

    def test_normalize_ip(self):
        assert normalize_ip("[192.168.1.1]") == "192.168.1.1"
        assert normalize_ip("192.168.1.1") == "192.168.1.1"
        assert normalize_ip(None) is None
