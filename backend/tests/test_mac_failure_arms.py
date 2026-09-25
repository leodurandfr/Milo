# backend/tests/test_mac_failure_arms.py
"""What naming a ROC sender does when the network misbehaves.

`test_mac_mdns.py` reads the answers; this file drives the exchange that gets
them (`mdns.resolve_sender_name`) against the fake responders of
tests/mac_world.py, at the UDP socket the resolver opens to <ip>:5353. The
journal follow and the start path are driven in `test_mac_sessions.py`.

Why these arms matter: this source's only job in the UI is to name the Mac
that is streaming, and `AudioSourceStatus` renders that name and nothing else,
so every arm here decides between "Mac mini de Léo" and a bare address on the
card — and the card waits on none of them longer than the bound.
"""
import asyncio
import errno

import pytest

from backend.sources.mac.mdns import resolve_sender_name
from backend.tests.golden.harness import settle
from backend.tests.mac_world import (
    FREEBOX_TYPES_ANSWER, FREEBOX_WALK_ANSWER, MINI_ANSWER, MINI_IP, FakeMdns,
)

FREEBOX_IP = "192.168.1.254"


def answering(*packets: bytes):
    """A responder that answers each query in turn with the next packet,
    under that query's id, as a legacy unicast responder does."""
    queue = list(packets)

    def respond(query: bytes):
        return [query[:2] + queue.pop(0)[2:]] if queue else []
    return respond


@pytest.fixture
def lan(monkeypatch):
    fake = FakeMdns({})
    fake.install(monkeypatch)
    yield fake
    fake.release()


async def resolving(lan: FakeMdns, ip: str, interface=None) -> asyncio.Future:
    naming = asyncio.ensure_future(resolve_sender_name(ip, interface))
    await settle()
    return naming


async def test_the_macs_captured_answer_names_it(lan):
    """Non-triviality: every refusal below is its arm, not a broken double."""
    lan.responders[MINI_IP] = answering(MINI_ANSWER)
    naming = await resolving(lan, MINI_IP)
    assert naming.done() and naming.result() == "Mac mini de Léo"
    assert lan.remotes == [(MINI_IP, 5353)]


async def test_a_responder_without_device_services_is_named_by_the_walk(lan):
    """Two queries: the first finds no device-level service but the types
    the Freebox lists, the second asks for those."""
    lan.responders[FREEBOX_IP] = answering(FREEBOX_TYPES_ANSWER, FREEBOX_WALK_ANSWER)
    naming = await resolving(lan, FREEBOX_IP)
    assert naming.done() and naming.result() == "Freebox Server"
    assert len(lan.queries) == 2


async def test_what_came_before_the_bound_still_counts(lan):
    """The walk goes unanswered: the host the first answer named is kept."""
    lan.responders[FREEBOX_IP] = answering(FREEBOX_TYPES_ANSWER)
    naming = await resolving(lan, FREEBOX_IP)
    assert not naming.done() and len(lan.queries) == 2
    lan.time_passes(1.0)
    await settle()
    assert naming.done() and naming.result() == "Freebox-Server"


async def test_an_unreadable_packet_is_skipped_for_the_answer_behind_it(lan):
    good = answering(MINI_ANSWER)
    lan.responders[MINI_IP] = lambda query: [b"\x00\x01garbage", *good(query)]
    naming = await resolving(lan, MINI_IP)
    assert naming.done() and naming.result() == "Mac mini de Léo"


async def test_an_answer_to_another_query_is_not_taken(lan):
    """A late answer to an earlier question carries that question's id."""
    lan.responders[MINI_IP] = lambda query: [bytes([query[0] ^ 0xFF, query[1]]) + MINI_ANSWER[2:]]
    naming = await resolving(lan, MINI_IP)
    assert not naming.done()
    lan.time_passes(1.0)
    await settle()
    assert naming.result() == MINI_IP


async def test_a_socket_the_network_refuses_leaves_the_address(lan, caplog):
    """No route to the sender (the link went down under it): fail open."""
    lan.refuses = OSError(errno.ENETUNREACH, "Network is unreachable")
    with caplog.at_level("WARNING", logger="source.mac.mdns"):
        assert await resolve_sender_name(MINI_IP) == MINI_IP
    assert "unreachable" in caplog.text


async def test_an_address_that_is_not_an_ip_never_reaches_the_network(lan):
    """The address comes out of a journal line matched by a regex."""
    assert await resolve_sender_name("not-an-ip") == "not-an-ip"
    assert lan.remotes == []


async def test_a_link_local_v6_sender_is_asked_on_its_interface(lan):
    """A link-local address is unroutable without its scope; the reverse PTR
    of a v6 sender lives under ip6.arpa."""
    naming = await resolving(lan, "fe80::1c2", "eth0")
    assert lan.remotes == [("fe80::1c2%eth0", 5353)]
    reverse = lan.queries[0][1][0][0]
    assert reverse.endswith(".ip6.arpa") and reverse.startswith("2.c.1.0.")
    lan.time_passes(1.0)
    await settle()
    assert naming.result() == "fe80::1c2"


async def test_a_host_without_a_responder_refusing_the_port_ends_at_once(lan):
    """Review of E73: a host with nothing on 5353 answers the query with an
    ICMP port unreachable within milliseconds, which asyncio hands to the
    socket as an error; ignored, every such sender waited the whole bound."""
    lan.closed_ports.add(MINI_IP)
    naming = await resolving(lan, MINI_IP)
    assert naming.done() and naming.result() == MINI_IP


async def test_an_exchange_that_breaks_is_logged_and_keeps_what_it_had(lan, caplog):
    """Review of E73: an exception inside the exchange was swallowed by the
    bound's cleanup — the sender shown by a lesser name with no trace. A type
    listed with an empty label (b"_x." + "_tcp.local") cannot be asked for."""
    import struct
    from backend.tests.mac_world import _encode_name, _record

    reverse = "173.1.168.192.in-addr.arpa"
    records = [
        _record(reverse, 12, _encode_name("Mac-mini-de-Leo.local")),
        _record("_services._dns-sd._udp.local", 12, b"\x03_x.\x04_tcp\x05local\x00"),
    ]

    def respond(query: bytes):
        header = query[:2] + struct.pack(">HHHHH", 0x8400, 0, len(records), 0, 0)
        return [header + b"".join(records)]
    lan.responders[MINI_IP] = respond
    with caplog.at_level("WARNING", logger="source.mac.mdns"):
        naming = await resolving(lan, MINI_IP)
    assert naming.done() and naming.result() == "Mac-mini-de-Leo"
    assert "not a DNS label" in caplog.text
