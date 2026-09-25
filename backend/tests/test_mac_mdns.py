# backend/tests/test_mac_mdns.py
"""Naming a ROC sender from its own mDNS answers (sources/mac/mdns.py).

When these fail, the Mac card reads an address, a UUID or a name DHCP rotated
('mac-mini-de-leo2') instead of "Mac mini de Léo". The packets are the ones the
owner's Mac and the Freebox answered on 2026-09-25 (tests/mac_world.py); the
few built here are marked as such.

Consumers: MacSource._name_sender → the session's `senders` → AudioSourceStatus.
"""
import struct

import pytest

from backend.sources.mac.mdns import (
    PTR, SRV, MalformedMessage, Record, build_query, is_private_hostname, listed_services,
    parse_message, sender_name,
)
from backend.tests.mac_world import FREEBOX_TYPES_ANSWER, FREEBOX_WALK_ANSWER, MINI_ANSWER

MINI_REVERSE = "173.1.168.192.in-addr.arpa"
FREEBOX_REVERSE = "254.1.168.192.in-addr.arpa"


def mini_records():
    records = parse_message(MINI_ANSWER).records
    assert len(records) == 24, "the capture no longer parses whole"
    return records


def without_ptr_on(records, *services):
    """The capture as a Mac publishing none of `services` would have answered."""
    dropped = {tuple(s.split(".")) for s in services}
    return [r for r in records if not (r.rtype == PTR and r.name in dropped)]


class TestTheWire:
    def test_a_query_is_laid_out_as_rfc_1035_says(self):
        """Header (id, flags 0, one question), the name as length-prefixed
        labels ending in the root, then type PTR and class IN — hand-written
        here from RFC 1035 §4.1, and the Mac answered exactly this layout."""
        assert build_query(0x1234, [("_rfb._tcp.local", PTR)]) == bytes.fromhex(
            "1234 0000 0001 0000 0000 0000"
            "04 5f726662 04 5f746370 05 6c6f63616c 00"
            "000c 0001"
        )

    def test_the_macs_answer_parses_whole(self):
        """Its names are compressed (pointers into earlier names), and its
        instance name is UTF-8 — 'é' is two bytes of one label."""
        message = parse_message(MINI_ANSWER)
        assert message.is_response
        by_type = {(r.name, r.rtype): r.data for r in mini_records()}
        assert by_type[(tuple(MINI_REVERSE.split(".")), PTR)] == ("Mac-mini-de-Leo", "local")
        instance = by_type[(("_companion-link", "_tcp", "local"), PTR)]
        assert instance == ("Mac mini de Léo", "_companion-link", "_tcp", "local")
        assert by_type[(instance, SRV)] == ("Mac-mini-de-Leo", "local")

    def test_a_truncated_packet_is_refused(self):
        with pytest.raises(MalformedMessage):
            parse_message(MINI_ANSWER[:700])

    def test_a_compression_loop_is_refused(self):
        """Built here: one answer whose name is a pointer to itself. Followed
        blindly it would never end."""
        loop = struct.pack(">HHHHHH", 1, 0x8400, 0, 1, 0, 0) + b"\xc0\x0c"
        with pytest.raises(MalformedMessage):
            parse_message(loop)


class TestWhatTheAnswersName:
    def test_the_mac_is_named_by_its_device_instance(self):
        assert sender_name(mini_records(), MINI_REVERSE) == "Mac mini de Léo"

    def test_the_raop_device_id_is_not_part_of_the_name(self):
        """_raop advertises '<deviceid>@<name>' — the prefix is the protocol's."""
        records = without_ptr_on(
            mini_records(), "_companion-link._tcp.local", "_airplay._tcp.local", "_rfb._tcp.local",
        )
        assert sender_name(records, MINI_REVERSE) == "Mac mini de Léo"

    def test_a_mac_answering_only_its_reverse_is_named_by_its_host(self):
        records = [r for r in mini_records() if r.name == tuple(MINI_REVERSE.split("."))]
        assert len(records) == 1
        assert sender_name(records, MINI_REVERSE) == "Mac-mini-de-Leo"

    def test_a_private_host_name_is_never_the_name(self):
        """Built here: the UUID-shaped hostname macOS rotates is worth less
        than the address the caller falls back to."""
        uuid_host = ("a8fca8ba-7a2f-4862-8934-70b031dd2eab", "local")
        records = [Record(tuple(MINI_REVERSE.split(".")), PTR, uuid_host)]
        assert sender_name(records, MINI_REVERSE) is None

    def test_a_device_service_of_another_host_is_not_this_ones_name(self):
        """Built here: an instance whose SRV names another host is someone
        else's, whatever type it is published under."""
        instance = ("Salon", "_airplay", "_tcp", "local")
        records = [
            Record(tuple(MINI_REVERSE.split(".")), PTR, ("Mac-mini-de-Leo", "local")),
            Record(("_airplay", "_tcp", "local"), PTR, instance),
            Record(instance, SRV, ("Apple-TV", "local")),
        ]
        assert sender_name(records, MINI_REVERSE) == "Mac-mini-de-Leo"

    def test_nothing_answered_names_nothing(self):
        assert sender_name([], MINI_REVERSE) is None


class TestTheServiceWalk:
    """A responder with none of the device-level services (the Freebox, as
    captured): the types it lists are asked for next, and an instance counts
    where its SRV names the host the reverse PTR did."""

    def test_the_listed_types_are_asked_for_the_device_ones_left_out(self):
        records = parse_message(FREEBOX_TYPES_ANSWER).records
        assert listed_services(records) == [
            "_smb._tcp.local", "_afpovertcp._tcp.local", "_device-info._tcp.local",
            "_adisk._tcp.local", "_fbx-api._tcp.local", "_http._tcp.local", "_https._tcp.local",
        ]

    def test_before_the_walk_answers_the_host_is_the_name(self):
        records = parse_message(FREEBOX_TYPES_ANSWER).records
        assert sender_name(records, FREEBOX_REVERSE) == "Freebox-Server"

    def test_the_walk_names_the_instance_on_the_host(self):
        records = (
            parse_message(FREEBOX_TYPES_ANSWER).records + parse_message(FREEBOX_WALK_ANSWER).records
        )
        assert sender_name(records, FREEBOX_REVERSE) == "Freebox Server"

    def test_a_lone_instance_cannot_outvote_the_devices_own(self):
        """Built here: a shared printer publishes under its own name on the
        same host; the name most services use wins."""
        host = ("Mac-mini-de-Leo", "local")
        records = [Record(tuple(MINI_REVERSE.split(".")), PTR, host)] + [
            Record((name, service, "_tcp", "local"), SRV, host)
            for name, service in (
                ("Imprimante", "_ipp"), ("Mac mini de Léo", "_smb"), ("Mac mini de Léo", "_ssh"),
            )
        ]
        assert sender_name(records, MINI_REVERSE) == "Mac mini de Léo"


class TestPrivateHostname:
    """The name that must never reach the UI."""

    def test_uuid_hostname_is_private(self):
        assert is_private_hostname("a8fca8ba-7a2f-4862-8934-70b031dd2eab")
        assert is_private_hostname("A8FCA8BA-7A2F-4862-8934-70B031DD2EAB")

    def test_real_names_are_not(self):
        for name in ("Mac-mini-de-Leo", "mac-mini-de-leo", "MacBook-Pro", "192.168.1.173", ""):
            assert not is_private_hostname(name)
