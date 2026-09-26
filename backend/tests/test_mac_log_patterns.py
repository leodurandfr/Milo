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
    TunerSample,
    classify_line,
    is_session_end,
    is_session_start,
    normalize_ip,
    parse_ip_from_line,
    parse_loss_ratio,
    parse_packet_ms,
    parse_session_end_cause,
    parse_source_block,
    parse_tuner,
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
        # and the source's journal follow drops it (guards on ip).
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


# --- Link statistics: verbatim lines from this unit's journal (2026-09-26) --
# The Mac link calibration measures the link from these alone; a parser that
# stops matching must fail here, not read as a link with no samples.

TUNER = ("16:51:16.099 [279385] [dbg] roc_audio: [latency_tuner.cpp:419] latency tuner: "
         "e2e_latency=3423(71.313ms) niq_latency=2577(53.688ms) target_latency=3360(70.000ms) "
         "jitter=0(0.000ms) stale=1329(27.688ms) fe=1.000027 eff_fe=1.000027")
TUNER_FIRST_OF_SESSION = ("16:52:17.651 [283997] [dbg] roc_audio: [latency_tuner.cpp:419] latency tuner: "
                          "e2e_latency=0(0.000ms) niq_latency=0(0.000ms) target_latency=3360(70.000ms) "
                          "jitter=0(0.000ms) stale=0(0.000ms) fe=0.000000 eff_fe=0.000000")
LOSS_RATIO = ("15:06:02.959 [248771] [dbg] roc_audio: [depacketizer.cpp:329] depacketizer: "
              "ts=2535572681 loss_ratio=0.00045")
PAYLOAD_SIZE = ("16:47:35.231 [501123] [dbg] roc_fec: [reader.cpp:631] fec reader: update payload size: "
                "next_esi=0 cur_size=0 new_size=540")
SOURCE_BLOCK = ("16:47:35.231 [501123] [dbg] roc_fec: [reader.cpp:686] fec reader: update source block size: "
                "cur_sblen=0 cur_rblen=0 new_sblen=10")
SESSION_START = ("16:47:35.171 [501123] [inf] roc_pipeline: [receiver_session_group.cpp:374] session group: "
                 "creating session: src_addr=192.168.1.173:57193 dst_addr=0.0.0.0:10001")
NO_PLAYBACK = ("16:31:36.199 [499620] [dbg] roc_audio: [watchdog.cpp:179] watchdog: no_playback timeout reached: "
               "every frame was blank during timeout: max_blank_duration=4116(93.333ms) warmup_duration=3087(70.000ms)")
SESSION_END = ("16:31:36.199 [499620] [inf] roc_pipeline: [receiver_session_group.cpp:416] session group: "
               "removing session")


class TestLinkStatistics:
    def test_a_tuner_snapshot_reads_queue_stale_and_target(self):
        assert parse_tuner(TUNER) == TunerSample(niq_ms=53.688, stale_ms=27.688, target_ms=70.0)

    def test_the_empty_first_snapshot_of_a_session_is_not_a_sample(self):
        """All zeros, fe included: counted, it would read as a queue that ran dry."""
        assert parse_tuner(TUNER_FIRST_OF_SESSION) is None

    def test_a_queue_that_ran_dry_reads_negative(self):
        dry = TUNER.replace("niq_latency=2577(53.688ms)", "niq_latency=-120(-2.721ms)")
        assert parse_tuner(dry).niq_ms == -2.721

    def test_the_post_fec_loss_ratio(self):
        assert parse_loss_ratio(LOSS_RATIO) == 0.00045

    def test_the_payload_size_names_the_senders_packet_length(self):
        """540 bytes = 12 of RTP header + 132 stereo L16 samples = 3 ms at 44.1 kHz."""
        assert round(parse_packet_ms(PAYLOAD_SIZE)) == 3

    def test_the_source_block_names_the_senders_nbsrc(self):
        assert parse_source_block(SOURCE_BLOCK) == 10

    def test_session_boundaries_and_the_cause_logged_before_an_end(self):
        assert is_session_start(SESSION_START) and not is_session_end(SESSION_START)
        assert is_session_end(SESSION_END) and not is_session_start(SESSION_END)
        assert parse_session_end_cause(NO_PLAYBACK) == "no_playback"
        assert parse_session_end_cause(SESSION_END) is None

    @pytest.mark.parametrize("line", [TUNER, LOSS_RATIO, PAYLOAD_SIZE, SOURCE_BLOCK, SESSION_END])
    def test_no_statistics_line_reads_as_a_connection_event(self, line):
        """The follow that feeds MacSource sees these lines too."""
        assert classify_line(line) == (None, None, None)
