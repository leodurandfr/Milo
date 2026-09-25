# backend/sources/mac/mdns.py
"""Naming a ROC sender by asking its own mDNS responder.

ROC hands over the sender's address and nothing else (its CNAME is random per
stream, measured), so the name has to be asked for — and every indirect path
was measured to answer with the wrong one. A reverse lookup through avahi is
answered by the router's DHCP zone, whose names macOS rotates (a UUID-shaped
private hostname, or 'mac-mini-de-leo2' once a Mac leases on Ethernet and
Wi-Fi in the same subnet), and avahi's cache keeps one address per name, so a
browse matched by address misses a Mac holding two.

So the sender itself is asked: a legacy unicast query (RFC 6762 §5.5, §6.7),
sent from an ephemeral port to <ip>:5353, which a Bonjour responder answers by
unicast to that port, from its own records only. Measured 2026-09-25 on the
owner's Mac mini (macOS 26, Ethernet .173 + Wi-Fi .21): one packet asking six
questions is answered by one packet in ~10 ms — the reverse PTR names the host
('Mac-mini-de-Leo.local'), and each device-level service its instance, "Mac
mini de Léo", the name the user gave the Mac and the only one that can carry
an accent. A host with no responder never answers, so the exchange is bounded.
"""
import asyncio
import ipaddress
import logging
import random
import re
import struct
from collections import Counter
from dataclasses import dataclass
from typing import Any, List, Optional, Sequence, Tuple

logger = logging.getLogger("source.mac.mdns")

MDNS_PORT = 5353
# What a sender gets to answer in. A responder answers within ~10 ms
# (measured), so this is the cost of a sender that has none.
RESOLVE_BOUND_S = 1.0

A, PTR, TXT, AAAA, SRV = 1, 12, 16, 28, 33
_CLASS_IN = 1
_HEADER = struct.Struct(">HHHHHH")
_QUESTION = struct.Struct(">HH")
_RECORD = struct.Struct(">HHIH")
_SRV_FIXED = struct.Struct(">HHH")
_FLAG_RESPONSE = 0x8000
# More pointers than a packet can hold names is a loop.
_MAX_POINTERS = 128

SERVICE_TYPES = "_services._dns-sd._udp.local"
# The services a Mac publishes once for the whole device, under the name its
# owner gave it, the most telling first. Anything else it lists is only
# trusted when its SRV target is the host the reverse lookup named.
DEVICE_SERVICES = (
    "_companion-link._tcp.local",
    "_airplay._tcp.local",
    "_raop._tcp.local",
    "_rfb._tcp.local",
)
_RAOP = "_raop._tcp.local"

# _raop advertises '<deviceid>@<instance name>'; the prefix is the protocol's,
# not part of the name.
_RAOP_PREFIX_RE = re.compile(r'^[0-9A-Fa-f]{12}@')

# The private hostname macOS rotates: a bare UUID, never a name to display.
_PRIVATE_HOSTNAME_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE,
)

Name = Tuple[str, ...]


class MalformedMessage(ValueError):
    """A packet that does not parse as a DNS message."""


@dataclass(frozen=True)
class Record:
    """One resource record. `data` is a Name for PTR and SRV (the target), the
    address text for A/AAAA, the strings for TXT, the raw bytes otherwise."""
    name: Name
    rtype: int
    data: Any


@dataclass(frozen=True)
class Message:
    qid: int
    is_response: bool
    # Answer, authority and additional sections alike: a legacy unicast
    # answer carries SRV, TXT and addresses as additionals (measured).
    records: Tuple[Record, ...]


def is_private_hostname(label: str) -> bool:
    """True for the UUID-shaped hostname macOS publishes instead of its name."""
    return bool(label) and bool(_PRIVATE_HOSTNAME_RE.match(label))


def labels(name: str) -> Name:
    return tuple(name.rstrip('.').split('.'))


def _key(name: Name) -> Name:
    """DNS compares names without regard to case."""
    return tuple(label.lower() for label in name)


# === The wire ===

def encode_name(name: str) -> bytes:
    out = bytearray()
    for label in labels(name):
        raw = label.encode()
        if not 0 < len(raw) < 64:
            raise ValueError(f"not a DNS label: {label!r}")
        out += bytes([len(raw)]) + raw
    return bytes(out + b'\0')


def build_query(qid: int, questions: Sequence[Tuple[str, int]]) -> bytes:
    """A standard query asking every (name, type) in `questions`, class IN."""
    return _HEADER.pack(qid, 0, len(questions), 0, 0, 0) + b''.join(
        encode_name(name) + _QUESTION.pack(rtype, _CLASS_IN) for name, rtype in questions
    )


def _read_name(data: bytes, offset: int) -> Tuple[Name, int]:
    """The name at `offset`, following compression pointers, and the offset
    right after it in the record that holds it."""
    out: List[str] = []
    end = None
    jumps = 0
    while True:
        if offset >= len(data):
            raise MalformedMessage("name runs past the packet")
        length = data[offset]
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(data):
                raise MalformedMessage("truncated pointer")
            jumps += 1
            if jumps > _MAX_POINTERS:
                raise MalformedMessage("compression loop")
            if end is None:
                end = offset + 2
            offset = ((length & 0x3F) << 8) | data[offset + 1]
            continue
        if length & 0xC0:
            raise MalformedMessage(f"unknown label type {length:#x}")
        offset += 1
        if length == 0:
            return tuple(out), end if end is not None else offset
        if offset + length > len(data):
            raise MalformedMessage("label runs past the packet")
        out.append(data[offset:offset + length].decode('utf-8', 'replace'))
        offset += length


def _unpack(fmt: struct.Struct, data: bytes, offset: int) -> tuple:
    if offset + fmt.size > len(data):
        raise MalformedMessage("truncated")
    return fmt.unpack_from(data, offset)


def _record_data(data: bytes, rtype: int, start: int, end: int) -> Any:
    rdata = data[start:end]
    if rtype == PTR:
        return _read_name(data, start)[0]
    if rtype == SRV:
        _unpack(_SRV_FIXED, data, start)
        return _read_name(data, start + _SRV_FIXED.size)[0]
    if rtype == A and len(rdata) == 4:
        return str(ipaddress.IPv4Address(rdata))
    if rtype == AAAA and len(rdata) == 16:
        return str(ipaddress.IPv6Address(rdata))
    if rtype == TXT:
        strings, i = [], 0
        while i < len(rdata):
            strings.append(rdata[i + 1:i + 1 + rdata[i]].decode('utf-8', 'replace'))
            i += 1 + rdata[i]
        return tuple(strings)
    return rdata


def parse_message(data: bytes) -> Message:
    """Every record of a DNS message; MalformedMessage when it does not parse."""
    qid, flags, qdcount, ancount, nscount, arcount = _unpack(_HEADER, data, 0)
    offset = _HEADER.size
    for _ in range(qdcount):
        _, offset = _read_name(data, offset)
        offset += _QUESTION.size
    records = []
    for _ in range(ancount + nscount + arcount):
        name, offset = _read_name(data, offset)
        rtype, _, _, length = _unpack(_RECORD, data, offset)
        offset += _RECORD.size
        if offset + length > len(data):
            raise MalformedMessage("record data runs past the packet")
        records.append(Record(name, rtype, _record_data(data, rtype, offset, offset + length)))
        offset += length
    return Message(qid, bool(flags & _FLAG_RESPONSE), tuple(records))


# === What the answers name ===

def _host(records: Sequence[Record], reverse: str) -> Optional[Name]:
    """The host the reverse PTR names."""
    wanted = _key(labels(reverse))
    return next((r.data for r in records if r.rtype == PTR and _key(r.name) == wanted), None)


def _srv_target(records: Sequence[Record], instance: Name) -> Optional[Name]:
    wanted = _key(instance)
    return next((r.data for r in records if r.rtype == SRV and _key(r.name) == wanted), None)


def _instances(records: Sequence[Record], service: Name) -> List[Name]:
    """The instances a PTR on `service` lists, in the order they came."""
    wanted = _key(service)
    return [
        r.data for r in records
        if r.rtype == PTR and _key(r.name) == wanted and len(r.data) > len(service)
        and _key(r.data[1:]) == wanted
    ]


def _display(instance: Name, service: str) -> str:
    name = instance[0]
    if service == _RAOP:
        name = _RAOP_PREFIX_RE.sub('', name)
    return name.strip()


def device_instance_name(records: Sequence[Record], host: Optional[Name]) -> Optional[str]:
    """The instance name of the first device-level service answered — unless
    its SRV names a host other than the one the reverse lookup did."""
    for service in DEVICE_SERVICES:
        for instance in _instances(records, labels(service)):
            target = _srv_target(records, instance)
            if host is not None and target is not None and _key(target) != _key(host):
                continue
            name = _display(instance, service)
            if name:
                return name
    return None


def listed_services(records: Sequence[Record]) -> List[str]:
    """The service types the sender lists, the device-level ones left out
    (they are asked for anyway)."""
    asked = {_key(labels(s)) for s in (*DEVICE_SERVICES, SERVICE_TYPES)}
    listing = _key(labels(SERVICE_TYPES))
    return list(dict.fromkeys(
        '.'.join(r.data) for r in records
        if r.rtype == PTR and _key(r.name) == listing and _key(r.data) not in asked
    ))


def hosted_instance_name(records: Sequence[Record], host: Optional[Name]) -> Optional[str]:
    """The instance name most services on `host` are published under: a lone
    service under its own name (a shared printer, a per-app instance) cannot
    outvote the device's own."""
    if host is None:
        return None
    names: Counter = Counter()
    for r in records:
        if r.rtype != SRV or _key(r.data) != _key(host) or len(r.name) < 4:
            continue
        name = _display(r.name, '.'.join(r.name[1:]))
        if name:
            names[name] += 1
    return names.most_common(1)[0][0] if names else None


def presentable_host(host: Optional[Name]) -> Optional[str]:
    """A hostname's first label, unless it is the private one macOS rotates."""
    label = host[0] if host else None
    return label if label and not is_private_hostname(label) else None


def sender_name(records: Sequence[Record], reverse: str) -> Optional[str]:
    """What the sender's answers name it, best first, or None."""
    host = _host(records, reverse)
    return (
        device_instance_name(records, host)
        or hosted_instance_name(records, host)
        or presentable_host(host)
    )


# === The exchange ===

class _Replies(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()

    def datagram_received(self, data: bytes, addr: Any) -> None:
        self.queue.put_nowait(data)

    def error_received(self, exc: Exception) -> None:
        # ICMP port unreachable on the connected socket: nothing listens on
        # 5353 there, and nothing will answer — no reason to wait the bound.
        self.queue.put_nowait(exc)


async def _ask(transport, replies: _Replies, questions: Sequence[Tuple[str, int]]) -> Tuple[Record, ...]:
    """Send one query and wait for the response that carries its id."""
    qid = random.randrange(1 << 16)
    transport.sendto(build_query(qid, questions))
    while True:
        reply = await replies.queue.get()
        if isinstance(reply, Exception):
            logger.debug("No mDNS responder answers there: %s", reply)
            return ()
        try:
            message = parse_message(reply)
        except MalformedMessage as e:
            logger.debug("Unreadable mDNS answer ignored: %s", e)
            continue
        if message.is_response and message.qid == qid:
            return message.records


async def _exchange(transport, replies: _Replies, reverse: str, records: List[Record]) -> None:
    """Ask for the host and the device-level services at once; failing those,
    walk the service types the sender lists. `records` gathers what came
    back, so what arrived before the bound still counts."""
    records.extend(await _ask(
        transport, replies,
        [(reverse, PTR), *((s, PTR) for s in DEVICE_SERVICES), (SERVICE_TYPES, PTR)],
    ))
    host = _host(records, reverse)
    if device_instance_name(records, host) is not None or host is None:
        return
    others = listed_services(records)
    if others:
        records.extend(await _ask(transport, replies, [(s, PTR) for s in others]))


async def resolve_sender_name(ip: str, interface: Optional[str] = None) -> str:
    """The name to show for the sender streaming from `ip`: a device-level
    instance name, else its presentable hostname, else the address itself —
    within RESOLVE_BOUND_S whatever answers."""
    try:
        address = ipaddress.ip_address(ip.split('%', 1)[0])
    except ValueError:
        logger.debug("Not an address, left as it is: %s", ip)
        return ip
    target = ip
    if address.version == 6 and address.is_link_local and '%' not in ip and interface:
        # A link-local address is unroutable without its interface.
        target = f"{ip}%{interface}"
    reverse = address.reverse_pointer

    try:
        transport, replies = await asyncio.get_running_loop().create_datagram_endpoint(
            _Replies, remote_addr=(target, MDNS_PORT),
        )
    except OSError as e:
        logger.warning("Cannot ask %s for its name: %s", ip, e)
        return ip

    records: List[Record] = []
    exchange = asyncio.ensure_future(_exchange(transport, replies, reverse, records))
    bound = asyncio.ensure_future(asyncio.sleep(RESOLVE_BOUND_S))
    try:
        await asyncio.wait({exchange, bound}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (exchange, bound):
            task.cancel()
        await asyncio.gather(exchange, bound, return_exceptions=True)
        transport.close()
    if not exchange.cancelled() and exchange.exception() is not None:
        # A fault of this code, not of the sender: said, and what came back
        # before it still names the sender.
        logger.warning("Naming %s broke off: %r", ip, exchange.exception())
    name = sender_name(records, reverse)
    if name is None:
        logger.debug("Nothing named %s: shown by its address", ip)
    return name or ip
