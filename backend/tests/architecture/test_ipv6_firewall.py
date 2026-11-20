"""Structural guardrail: nothing reaches a unit over IPv6 from beyond its own link.

Every daemon on a unit that listens on [::] answers the whole Internet as soon
as the router hands out a global prefix and lets inbound IPv6 through — the router's
choice, never Milō's. `/etc/nftables.conf` (identical in both trees) is RFC
6092's default deny, applied on the host: a new inbound IPv6 connection is
accepted from the link, a unique local address and the prefixes the unit's own
interfaces carry, kept in a set by the `50-milo-ipv6-link-prefixes` dispatcher
script, which NetworkManager runs at each event and a timer runs again a little
after; replies, loopback, ICMPv6 errors and neighbor discovery pass from
anywhere. IPv4 is not touched.

None of it runs in CI: nft reads the ruleset at boot, NetworkManager runs the
script, systemd runs the drop-in. So the ruleset is *evaluated* here, by a model
of the nft subset it is written in, against the listeners measured on the units;
a statement the model does not know fails the parse instead of being skipped,
and the model's own check against simulated drift is at the bottom. The
satellites do not need IPv6 to find their server (`registration.py` resolves it
over AF_INET only and `server_address_watch.py` follows an IPv4 flow), and a LAN
peer reaching either role over IPv6 comes from the link or its prefix anyway.

What is still to see on a unit: AirPlay from an iPhone on the LAN connects, and
an IPv6 connection from a phone on a mobile network is refused.

Doctrine note (as in the other guardrails here): every extractor asserts its own
output is non-trivial first, so a broken parse fails loudly instead of passing
on an empty surface.
"""
import ipaddress
import json
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pytest

from backend.tests.architecture.test_rootfs_deployment import _stage_text

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVER_TREE = REPO_ROOT / "rootfs"
CLIENT_TREE = REPO_ROOT / "milo-client" / "rootfs"

RULESET_REL = "etc/nftables.conf"
DISPATCHER_REL = "etc/NetworkManager/dispatcher.d/50-milo-ipv6-link-prefixes"
DROPIN_REL = "etc/systemd/system/nftables.service.d/milo-link-prefixes.conf"
AVAHI_DISPATCHER_REL = "etc/NetworkManager/dispatcher.d/90-milo-network"
FIREWALL_FILES = (RULESET_REL, DISPATCHER_REL, DROPIN_REL)

RULESET = SERVER_TREE / RULESET_REL
DISPATCHER = SERVER_TREE / DISPATCHER_REL
DROPIN = SERVER_TREE / DROPIN_REL

# What listens on [::] (or on every address) on a unit and would answer the
# Internet over IPv6 without the ruleset, measured with `ss -tlnu`. go-librespot's
# zeroconf port is picked at start, nothing here fixes it: 39277 is the one
# measured, and the sweep over every port below covers whichever it takes.
LISTENERS_ON_ANY_ADDRESS = {
    "sshd": ("tcp", 22),
    "rpcbind": ("tcp", 111),
    "rpcbind (udp)": ("udp", 111),
    "snapserver": ("tcp", 1704),
    "shairport-sync": ("tcp", 7000),
    "go-librespot zeroconf": ("tcp", 39277),
}

# Documentation addresses (RFC 3849) throughout. The link's prefix is one /64 of
# a delegated /56; the next /64 of the same /56 is not the link.
LINK_PREFIX = "2001:db8:12:3450::/64"
LINK_PEER = "2001:db8:12:3450:8c1:2ff:fe3a:4b5c"
INTERNET = ("2001:db8:dead:beef::1", "2001:db8:12:3451::1")
LAN_PEERS = ("fe80::8c1:2ff:fe3a:4b5c", "fd12:3456:789a:1::5", LINK_PEER)
# Where the Internet arrives: either link may carry the router's default.
INTERFACES = ("eth0", "wlan0")

# nft's ICMPv6 type names, with their IANA numbers (RFC 4443, 4861, 2710, 3810).
ICMPV6_TYPES = {
    "destination-unreachable": 1, "packet-too-big": 2, "time-exceeded": 3,
    "parameter-problem": 4, "echo-request": 128, "echo-reply": 129,
    "mld-listener-query": 130, "mld-listener-report": 131, "mld-listener-done": 132,
    "mld-listener-reduction": 132, "nd-router-solicit": 133, "nd-router-advert": 134,
    "nd-neighbor-solicit": 135, "nd-neighbor-advert": 136, "nd-redirect": 137,
    "router-renumbering": 138, "ind-neighbor-solicit": 141, "ind-neighbor-advert": 142,
    "mld2-listener-report": 143,
}
# nft's names (and numbers) for the protocols the packets above carry.
PROTOCOLS = {
    "tcp": "tcp", "6": "tcp", "udp": "udp", "17": "udp",
    "icmpv6": "icmpv6", "ipv6-icmp": "icmpv6", "58": "icmpv6",
}
# What passes from anywhere: the errors (1-4, packet-too-big among them, without
# which path-MTU discovery breaks outgoing connections) and neighbor discovery.
ICMPV6_FROM_ANYWHERE = {1, 2, 3, 4, 133, 134, 135, 136}


# --------------------------------------------------------------------------- #
# A model of the nft subset the ruleset is written in.
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Packet:
    """One inbound IPv6 packet, as the input hook sees it."""

    saddr: str
    proto: str
    dport: int | None = None
    icmpv6_type: int | None = None
    ct: str = "new"
    iif: str = "eth0"


@dataclass
class Ruleset:
    tables: list[tuple[str, str]]       # (family, name), every table defined with a body
    statements: list[str]               # every top-level statement, bodies cut off
    sets: dict[str, list[str]]          # set name -> its declaration lines
    hook: str
    policy: str
    rules: list[str]


TOKEN_RE = re.compile(r'\{[^}]*\}|"[^"]*"|[^\s{}]+')
# A comparison other than equality is not modeled: `!=` read as a value would
# invert the match.
OPERATORS = {"==", "!=", "<", ">", "<=", ">=", "!"}
# What may stand at the top level of the file; anything else (a rule added to a
# table of another family, a `define`) is refused by the parse.
TOP_LEVEL_RE = re.compile(r"(?:delete )?table \S+ \S+|flush ruleset")


def _strip_comments(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _blocks(text: str) -> list[tuple[str, str]]:
    """The statements at this nesting level: (head, body), body '' for none."""
    out, head, depth, start = [], "", 0, 0
    for index, char in enumerate(text):
        if char == "{":
            if depth == 0:
                start = index + 1
            depth += 1
        elif char == "}":
            depth -= 1
            assert depth >= 0, "unbalanced braces in the ruleset"
            if depth == 0:
                out.append((head.strip(), text[start:index]))
                head = ""
        elif depth == 0:
            if char == "\n":
                if head.strip():
                    out.append((head.strip(), ""))
                head = ""
            else:
                head += char
    assert depth == 0, "unbalanced braces in the ruleset"
    if head.strip():
        out.append((head.strip(), ""))
    return out


def parse_ruleset(text: str) -> Ruleset:
    """The tables, the one `table … { set …; chain input … }` and its rules."""
    statements = _blocks(_strip_comments(text))
    assert statements, "no statement read from the ruleset; the parse is broken"
    for head, _ in statements:
        assert TOP_LEVEL_RE.fullmatch(head), f"a top-level statement the model does not know: {head!r}"
    tables = [tuple(head.split()[1:3]) for head, body in statements if body and head.startswith("table ")]
    assert len(tables) == 1, f"one table with a body expected, read {tables}"
    body = next(body for head, body in statements if body and head.startswith("table "))

    sets, chains = {}, {}
    for head, inner in _blocks(body):
        kind, _, name = head.partition(" ")
        if kind == "set":
            sets[name] = [line.strip() for line in inner.splitlines() if line.strip()]
        elif kind == "chain":
            chains[name] = inner
        else:
            raise AssertionError(f"a table statement the model does not know: {head!r}")
    assert list(chains) == ["input"], f"one chain, `input`, is modeled; read {list(chains)}"

    lines = [line.strip() for line in chains["input"].splitlines() if line.strip()]
    header = re.fullmatch(
        r"type filter hook (\w+) priority [\w+-]+;\s*policy (\w+);", lines[0] if lines else ""
    )
    assert header, f"no `type filter hook … priority …; policy …;` header read: {lines[:1]}"
    return Ruleset(
        tables=tables,
        statements=[head for head, _ in statements],
        sets=sets,
        hook=header.group(1),
        policy=header.group(2),
        rules=lines[1:],
    )


def _values(token: str) -> list[str]:
    inner = token[1:-1] if token.startswith("{") else token
    return [value.strip().strip('"') for value in inner.split(",") if value.strip()]


def _ports(token: str) -> Callable[[int], bool]:
    spans = []
    for value in _values(token):
        low, _, high = value.partition("-")
        spans.append((int(low), int(high or low)))
    return lambda port: any(low <= port <= high for low, high in spans)


def _saddr(token: str, set_contents: dict[str, list[str]]) -> Callable[[Packet], bool]:
    if token.startswith("@"):
        name = token[1:]
        assert name in set_contents, f"a set the model has no contents for: @{name}"
        networks = [ipaddress.ip_network(p) for p in set_contents[name]]
    else:
        networks = [ipaddress.ip_network(value) for value in _values(token)]
    return lambda packet: any(_address(packet.saddr) in net for net in networks)


@lru_cache(maxsize=None)
def _address(text: str) -> ipaddress.IPv6Address:
    return ipaddress.IPv6Address(text)


def compile_rule(rule: str, set_contents: dict[str, list[str]]):
    """(matches, verdict) for one rule; a statement the model lacks fails here."""
    tokens = TOKEN_RE.findall(rule)
    matches: list[Callable[[Packet], bool]] = []
    index = 0
    while index < len(tokens):
        word = tokens[index]
        pair = " ".join(tokens[index:index + 2])
        arg = tokens[index + 2] if index + 2 < len(tokens) else ""
        assert arg not in OPERATORS and tokens[index + 1:index + 2] != ["!="], (
            f"a comparison the model does not know in {rule!r}"
        )
        if word in ("accept", "drop", "reject"):
            # `reject with …` only names the error sent back.
            assert index == len(tokens) - 1 or (word == "reject" and tokens[index + 1] == "with"), (
                f"statements after the verdict are not modeled: {rule!r}"
            )
            return matches, word
        if word == "counter":
            index += 1
            continue
        if word in ("iif", "iifname"):
            names = _values(tokens[index + 1])
            matches.append(lambda p, names=names: p.iif in names)
            index += 2
            continue
        if pair == "ct state":
            states = _values(arg)
            matches.append(lambda p, states=states: p.ct in states)
        elif pair == "icmpv6 type":
            unknown = [v for v in _values(arg) if not v.isdigit() and v not in ICMPV6_TYPES]
            assert not unknown, f"an ICMPv6 type the model does not know: {unknown} in {rule!r}"
            types = {int(v) if v.isdigit() else ICMPV6_TYPES[v] for v in _values(arg)}
            matches.append(lambda p, types=types: p.proto == "icmpv6" and p.icmpv6_type in types)
        elif pair == "ip6 saddr":
            matches.append(_saddr(arg, set_contents))
        elif pair in ("tcp dport", "udp dport", "th dport"):
            proto, inside = word, _ports(arg)
            matches.append(
                lambda p, proto=proto, inside=inside: (proto == "th" or p.proto == proto)
                and p.dport is not None and inside(p.dport)
            )
        elif pair in ("meta l4proto", "ip6 nexthdr"):
            unknown = [v for v in _values(arg) if v not in PROTOCOLS]
            assert not unknown, f"a protocol the model does not know: {unknown} in {rule!r}"
            protos = {PROTOCOLS[v] for v in _values(arg)}
            matches.append(lambda p, protos=protos: p.proto in protos)
        else:
            raise AssertionError(f"a statement the model does not know: {pair!r} in {rule!r}")
        index += 3
    raise AssertionError(f"a rule without a verdict is not modeled: {rule!r}")


def evaluator(text: str, set_contents: dict[str, list[str]]) -> Callable[[Packet], str]:
    """The verdict the input chain gives a packet: the first rule that matches, else policy."""
    ruleset = parse_ruleset(text)
    assert ruleset.hook == "input", f"the chain hooks {ruleset.hook}, not input"
    assert len(ruleset.rules) >= 5, f"only {len(ruleset.rules)} rules read; the parse is broken"
    compiled = [compile_rule(rule, set_contents) for rule in ruleset.rules]

    def verdict(packet: Packet) -> str:
        for matches, outcome in compiled:
            if all(match(packet) for match in matches):
                return outcome
        return ruleset.policy

    return verdict


def _dispatcher_names() -> tuple[str, str, str]:
    """(family, table, set) as the dispatcher script names them, read from it."""
    text = DISPATCHER.read_text(encoding="utf-8")
    names = [re.search(rf'^{key} = "([\w-]+)"$', text, re.MULTILINE) for key in ("FAMILY", "TABLE", "SET")]
    assert all(names), f"FAMILY / TABLE / SET not read from {DISPATCHER.name}"
    return tuple(match.group(1) for match in names)


def _verdict(text: str | None = None) -> Callable[[Packet], str]:
    _, _, set_name = _dispatcher_names()
    return evaluator(RULESET.read_text() if text is None else text, {set_name: [LINK_PREFIX]})


def _open_to_the_internet(verdict: Callable[[Packet], str]) -> list[str]:
    """Each listener a new connection from beyond the link reaches."""
    return [
        f"{daemon} {proto}/{port} from {source}"
        for daemon, (proto, port) in LISTENERS_ON_ANY_ADDRESS.items()
        for source in INTERNET
        for iif in INTERFACES
        if verdict(Packet(source, proto, dport=port, iif=iif)) == "accept"
    ]


def _icmpv6_from_the_internet(verdict: Callable[[Packet], str]) -> set[int]:
    return {
        icmp_type for icmp_type in range(256) for iif in INTERFACES
        if verdict(Packet(INTERNET[0], "icmpv6", icmpv6_type=icmp_type, iif=iif)) == "accept"
    }


# --------------------------------------------------------------------------- #
# The ruleset.
# --------------------------------------------------------------------------- #

def test_the_ruleset_is_one_ip6_table_replaced_whole_at_every_load():
    """IPv4 is not this file's, and a reload replaces this table and nothing else.

    Fails on a table of another family (`ip`, `inet`: IPv4 traffic), on `flush
    ruleset` — which also wipes the tables NetworkManager writes for a shared
    (hotspot) connection, at every `systemctl reload nftables` — and when the
    table is not declared then deleted before it is defined: without the delete
    a reload appends every rule a second time, and the set is never emptied.
    """
    ruleset = parse_ruleset(RULESET.read_text())
    family, table, set_name = _dispatcher_names()

    assert ruleset.tables == [(family, table)], (
        f"the ruleset defines {ruleset.tables}; the dispatcher keeps the set of {family} {table}"
    )
    assert not [s for s in ruleset.statements if s.split()[:2] == ["flush", "ruleset"]], (
        "`flush ruleset` wipes every table on the unit, NetworkManager's included"
    )
    replaced = [f"table {family} {table}", f"delete table {family} {table}"]
    assert ruleset.statements[:2] == replaced, (
        f"the table is not declared then deleted before its definition: {ruleset.statements[:3]}"
    )
    assert ruleset.policy == "drop", f"the input chain's policy is {ruleset.policy}"
    assert ruleset.sets.get(set_name) == ["type ipv6_addr", "flags interval"], (
        f"@{set_name} must hold IPv6 prefixes, as the dispatcher fills it: {ruleset.sets}"
    )


def test_no_listener_answers_a_new_connection_from_beyond_the_link():
    """sshd, rpcbind, snapserver, shairport-sync and go-librespot's zeroconf drop
    the Internet, an address of the next /64 of the same delegation included.

    Fails on a rule that opens one of them to every source, which is a login
    prompt or a control port offered to whoever scans the prefix.
    """
    reached = _open_to_the_internet(_verdict())
    assert not reached, "reachable from the Internet over IPv6:\n" + "\n".join(reached)


def test_no_port_answers_a_new_connection_from_beyond_the_link():
    """Not one TCP or UDP port: a daemon added later listens on [::] too."""
    verdict = _verdict()
    opened = [
        f"{proto}/{port} on {iif}"
        for proto in ("tcp", "udp")
        for port in range(65536)
        for iif in INTERFACES
        if verdict(Packet(INTERNET[0], proto, dport=port, iif=iif)) == "accept"
    ]
    assert not opened, f"ports open to the Internet over IPv6: {opened[:20]}"


@pytest.mark.parametrize("source", LAN_PEERS, ids=("link-local", "ula", "link-prefix"))
def test_every_listener_answers_the_lan(source):
    """A phone on the LAN reaches AirPlay, Spotify Connect, snapserver and SSH by
    whichever address it picked — link-local, unique local or one of the link's
    own global prefixes. Fails when the ruleset drops one of them: a cast that
    hangs on the address the phone tried first."""
    verdict = _verdict()
    refused = [
        f"{daemon} {proto}/{port}"
        for daemon, (proto, port) in LISTENERS_ON_ANY_ADDRESS.items()
        if verdict(Packet(source, proto, dport=port)) != "accept"
    ]
    assert not refused, f"refused from {source}: {refused}"


def test_icmpv6_from_anywhere_is_the_errors_and_neighbor_discovery():
    """Packet-too-big from any router on the path, or path-MTU discovery breaks
    every outgoing connection that crosses a smaller link; neighbor discovery,
    or the unit cannot be found on its own link. Echo and the rest stay closed
    to the Internet."""
    accepted = _icmpv6_from_the_internet(_verdict())
    assert accepted == ICMPV6_FROM_ANYWHERE, (
        f"missing: {sorted(ICMPV6_FROM_ANYWHERE - accepted)}, extra: {sorted(accepted - ICMPV6_FROM_ANYWHERE)}"
    )


def test_replies_and_loopback_pass():
    """What the unit opened itself is answered (streams, updates, NetworkManager's
    connectivity check, all over IPv6 when the router offers it), and a client of
    `localhost` reaches a daemon on [::1]: a dropped SYN on loopback is a hang,
    not a refusal to fall back from."""
    verdict = _verdict()
    for state in ("established", "related"):
        assert verdict(Packet(INTERNET[0], "tcp", dport=50000, ct=state)) == "accept", state
        assert verdict(Packet(INTERNET[0], "udp", dport=50000, ct=state)) == "accept", state
    assert verdict(Packet("::1", "tcp", dport=8000, iif="lo")) == "accept"


# --------------------------------------------------------------------------- #
# The set, the script that keeps it, and what runs that script.
# --------------------------------------------------------------------------- #

_IP_ADDR_JSON = [
    {"ifindex": 1, "ifname": "lo", "addr_info": [
        {"family": "inet6", "local": "::1", "prefixlen": 128, "scope": "host"},
    ]},
    {"ifindex": 2, "ifname": "eth0", "addr_info": [
        {"family": "inet6", "local": "2001:db8:12:3450:1e2f:3aff:fe4b:5c6d", "prefixlen": 64,
         "scope": "global", "dynamic": True, "mngtmpaddr": True},
        {"family": "inet6", "local": "2001:db8:12:3450:9a3c:41d2:77e0:1b05", "prefixlen": 64,
         "scope": "global", "temporary": True, "dynamic": True},
        {"family": "inet6", "local": "fe80::1e2f:3aff:fe4b:5c6d", "prefixlen": 64, "scope": "link"},
        {},
    ]},
    {"ifindex": 3, "ifname": "wlan0", "addr_info": [
        {"family": "inet6", "local": "2001:db8:12:3450:1e2f:3aff:fe4b:aaaa", "prefixlen": 64,
         "scope": "global", "dynamic": True},
        {"family": "inet6", "local": "fd12:3456:789a:1:1e2f:3aff:fe4b:aaaa", "prefixlen": 64,
         "scope": "global"},
    ]},
]


# What each stub prints on stderr when it is told to fail: the script captures
# none of them, so each must reach the journal as the command wrote it.
NFT_ERROR = "Error: No such file or directory"
IP_ERROR = "RTNETLINK answers: Operation not permitted"
RUN_ERROR = "Failed to start transient timer unit: Connection timed out"


@dataclass
class DispatcherRun:
    status: int
    calls: list[str]            # every `nft` / `ip` command line, in order
    batch: str                  # what was piped to `nft -f -`
    armed: list[list[str]]      # the argv of each `systemd-run`
    stderr: str


def _run_dispatcher(
    tmp_path: Path, addresses: list, *argv: str,
    loaded: bool = True, list_status: int = 0, batch_status: int = 0,
    ip_status: int = 0, run_status: int = 0,
) -> DispatcherRun:
    """Run the dispatcher with `argv` (NetworkManager passes interface and action;
    the drop-in and the timer pass nothing), `ip`, `nft` and `systemd-run` stubbed.

    `loaded`: `nft list tables` prints the firewall's table, as nft prints it.
    `list_status` / `batch_status`: what `nft list` / `nft -f -` exit with,
    printing NFT_ERROR on stderr when it is not 0. `ip_status`: what `ip` exits
    with, printing IP_ERROR on stderr and nothing on stdout when it is not 0 (a
    killed `ip`, a netlink error). `run_status`: the same for `systemd-run`,
    with RUN_ERROR, once it has recorded its argv.
    """
    family, table, _ = _dispatcher_names()
    stubs = tmp_path / "bin"
    stubs.mkdir()
    (tmp_path / "addresses.json").write_text(json.dumps(addresses))
    (tmp_path / "tables").write_text(f"table {family} {table}\n" if loaded else "")
    (stubs / "ip").write_text(
        f'#!/bin/sh\necho "ip $*" >> "{tmp_path}/calls"\n'
        f'[ {ip_status} = 0 ] || {{ echo "{IP_ERROR}" >&2; exit {ip_status}; }}\n'
        f'cat "{tmp_path}/addresses.json"\n'
    )
    (stubs / "nft").write_text(
        f'#!/bin/sh\necho "nft $*" >> "{tmp_path}/calls"\n'
        f'case "$1" in\n'
        f'  list) cat "{tmp_path}/tables"; status={list_status} ;;\n'
        f'  -f) cat >> "{tmp_path}/batch"; status={batch_status} ;;\n'
        f'esac\n'
        f'[ "$status" = 0 ] || {{ echo "{NFT_ERROR}" >&2; exit "$status"; }}\n'
    )
    (stubs / "systemd-run").write_text(
        f'#!/bin/sh\nprintf "%s\\037" "$@" >> "{tmp_path}/armed"\necho >> "{tmp_path}/armed"\n'
        f'[ {run_status} = 0 ] || {{ echo "{RUN_ERROR}" >&2; exit {run_status}; }}\n'
    )
    for stub in stubs.iterdir():
        stub.chmod(0o755)

    result = subprocess.run(
        [sys.executable, "-I", str(DISPATCHER), *argv],
        env={"PATH": f"{stubs}:/usr/bin:/bin"}, cwd=tmp_path,
        capture_output=True, text=True, timeout=30, check=False,
    )

    def read(name: str) -> str:
        return (tmp_path / name).read_text() if (tmp_path / name).exists() else ""

    calls = read("calls").splitlines()
    assert any(call.startswith("nft list") for call in calls), (
        f"the dispatcher never asked nft for its table; calls: {calls}, stderr: {result.stderr}"
    )
    armed = [line.split("\037")[:-1] for line in read("armed").splitlines()]
    return DispatcherRun(result.returncode, calls, read("batch"), armed, result.stderr)


def test_the_set_is_rebuilt_from_the_prefixes_the_interfaces_carry(tmp_path):
    """One transaction: empty the set, then add each global prefix once.

    eth0 and wlan0 on one LAN carry the same /64, which an interval set refuses
    twice; the host bits of each address go; loopback and link-local stay out.
    Fails too when the batch names a table or set the ruleset does not declare:
    the script would fail on every event, and the LAN's global addresses would
    be refused for good.
    """
    run = _run_dispatcher(tmp_path, _IP_ADDR_JSON, "eth0", "up")
    family, table, set_name = _dispatcher_names()
    ruleset = parse_ruleset(RULESET.read_text())
    assert (family, table) in ruleset.tables and set_name in ruleset.sets, (
        f"the dispatcher fills {family} {table} @{set_name}; the ruleset declares {ruleset.tables} {list(ruleset.sets)}"
    )
    assert any(f"@{set_name}" in rule for rule in ruleset.rules), f"no rule reads @{set_name}"

    assert run.status == 0, run.stderr
    assert run.batch.splitlines() == [
        f"flush set {family} {table} {set_name}",
        f"add element {family} {table} {set_name} {{ 2001:db8:12:3450::/64, fd12:3456:789a:1::/64 }}",
    ]


def test_a_prefix_the_router_took_back_leaves_the_set(tmp_path):
    """Renumbered, or the cable gone: no global address left, so the set is only
    emptied. A prefix kept past its address would let in whoever holds it next."""
    gone = [{**link, "addr_info": [a for a in link["addr_info"] if a.get("scope") != "global"]}
            for link in _IP_ADDR_JSON]
    run = _run_dispatcher(tmp_path, gone, "eth0", "down")
    family, table, set_name = _dispatcher_names()
    assert run.status == 0, run.stderr
    assert run.batch.splitlines() == [f"flush set {family} {table} {set_name}"]


@pytest.mark.parametrize("action", ["up", "dhcp6-change", "dns-change", "connectivity-change"])
def test_a_network_event_runs_the_script_once_more_later(tmp_path, action):
    """NetworkManager sends no event for a global address the router advertises
    after a link came `up` on IPv4 (SLAAC), and Avahi publishes that address for
    milo.local: with no second look, the set stays empty and every LAN peer that
    picked it hangs on a dropped SYN. So each event arms one deferred run of the
    script itself, with no argument of NetworkManager's — fails when the arming
    goes, or when the deferred run would be mistaken for an event (see below).
    Fails too without `--collect`: a deferred run that fails would then stay in
    `systemctl --failed` as a `run-u*.service`, one more at every event."""
    run = _run_dispatcher(tmp_path, _IP_ADDR_JSON, "eth0", action)
    assert run.status == 0, run.stderr
    assert len(run.armed) == 1, f"{action}: armed {run.armed}"
    argv = run.armed[0]
    start = next((i for i, arg in enumerate(argv) if not arg.startswith("-")), len(argv))
    options, command = argv[:start], argv[start:]
    assert command == [str(DISPATCHER)], (
        f"{action}: the deferred run is not this script alone, with no argument to read as an event: {argv}"
    )
    delays = [int(m.group(1)) for arg in options if (m := re.fullmatch(r"--on-active=(\d+)s", arg))]
    assert len(delays) == 1 and delays[0] > 0, f"{action}: not one deferred run: {argv}"
    assert {"--collect", "-G"} & set(options), f"{action}: a failed deferred run is never collected: {argv}"


@pytest.mark.parametrize("argv", [(), ("eth0", "down")], ids=("not-networkmanager", "link-down"))
def test_a_run_no_event_asked_for_arms_nothing(tmp_path, argv):
    """The drop-in's runs and the deferred run itself carry no argument: arming
    from them would re-run the script forever. A link going down leaves no
    address on its way, so it arms nothing either; the set is still rebuilt."""
    run = _run_dispatcher(tmp_path, _IP_ADDR_JSON, *argv)
    assert run.status == 0, run.stderr
    assert run.batch, "the set was not rebuilt"
    assert run.armed == []


def test_a_stopped_firewall_is_left_alone(tmp_path):
    """Stopping nftables.service deletes the table (the drop-in's ExecStop), and
    every NetworkManager event after that must change nothing and fail nothing:
    a deliberate stop is not a failure at each event."""
    run = _run_dispatcher(tmp_path, _IP_ADDR_JSON, "eth0", "up", loaded=False)
    assert run.status == 0, run.stderr
    assert run.batch == ""
    assert not [call for call in run.calls if call.startswith("nft -f")], run.calls
    assert run.armed == []


_FAILURES = {
    # failing command: (the stub's status keyword, its error, whether the set was already read)
    "nft list": ("list_status", NFT_ERROR, False),
    "ip": ("ip_status", IP_ERROR, False),
    "nft -f": ("batch_status", NFT_ERROR, True),
}


@pytest.mark.parametrize("failure", sorted(_FAILURES))
def test_a_command_that_fails_is_a_failure_not_a_stopped_firewall(tmp_path, failure):
    """Each failure exits non-zero with the command's own error, so nm-dispatcher
    and nftables.service log it, and arms nothing.

    A loaded table whose set was renamed or removed refuses the batch, and an nft
    that cannot list its tables knows nothing: read as "not loaded", every event
    would succeed in silence while the LAN's global addresses stay refused. An
    `ip` that fails prints nothing, which reads as "no prefix at all": the set
    would be emptied, every LAN global address refused, and the run exit 0 — so
    a failed read must leave the set as it was, unflushed.
    """
    keyword, error, read = _FAILURES[failure]
    run = _run_dispatcher(tmp_path, _IP_ADDR_JSON, "eth0", "up", **{keyword: 1})
    assert run.status != 0, f"{failure}: exited 0"
    assert error in run.stderr, f"{failure}: its error did not reach the journal: {run.stderr!r}"
    assert run.armed == []
    if not read:
        assert run.batch == "", f"{failure}: the set was changed on a failed read: {run.batch!r}"
        assert not [call for call in run.calls if call.startswith("nft -f")], run.calls


def test_a_deferred_run_systemd_refuses_is_a_failure(tmp_path):
    """The set is rebuilt either way, but with no deferred run a global address
    the router advertises later stays out of it until the next event: the exit
    status says the timer was not armed, and systemd-run's error reaches the
    journal."""
    run = _run_dispatcher(tmp_path, _IP_ADDR_JSON, "eth0", "up", run_status=1)
    assert run.batch, "the set was not rebuilt"
    assert len(run.armed) == 1, f"armed {run.armed}"
    assert run.status != 0, "a refused timer exited 0"
    assert RUN_ERROR in run.stderr, f"systemd-run's error did not reach the journal: {run.stderr!r}"


def _directives(unit_text: str) -> list[tuple[str, str]]:
    return [
        tuple(part.strip() for part in line.split("=", 1))
        for line in _strip_comments(unit_text).splitlines()
        if "=" in line
    ]


def test_nftables_fills_the_set_at_every_load_and_stops_its_own_table_only():
    """A load starts the set empty, at start and at reload alike, so both run the
    script after it; Debian's `ExecStop=nft flush ruleset` is reset, or stopping
    the firewall wipes NetworkManager's hotspot tables with it."""
    directives = _directives(DROPIN.read_text())
    assert directives, f"no directive read from {DROPIN.name}"
    script = f"/{DISPATCHER_REL}"
    for key in ("ExecStartPost", "ExecReload"):
        assert (key, f"-{script}") in directives, f"{key} does not run {script}"

    family, table, _ = _dispatcher_names()
    stops = [value for key, value in directives if key == "ExecStop"]
    assert stops == ["", f"-/usr/sbin/nft delete table {family} {table}"], (
        f"ExecStop must be reset, then delete {family} {table} alone: {stops}"
    )


def test_the_set_is_filled_before_the_avahi_dispatcher_waits():
    """NetworkManager runs dispatcher.d in name order, one script at a time, and
    the server's 90-milo-network can wait 45 s for eth0's lease: named after it,
    the set would wait behind that, refusing the LAN's global addresses."""
    assert (SERVER_TREE / AVAHI_DISPATCHER_REL).is_file(), "90-milo-network moved; re-read this"
    assert Path(DISPATCHER_REL).name < Path(AVAHI_DISPATCHER_REL).name


@pytest.mark.parametrize("rel", FIREWALL_FILES)
def test_the_satellite_tree_carries_the_same_firewall(rel):
    """A satellite updated by its own tree gets the firewall's files from it alone.

    Each one is a declared twin, so `test_twin_files_have_not_drifted` holds the
    two copies equal; this fails when a copy, or its `Twin of` header, goes.
    """
    for tree, other in ((SERVER_TREE, CLIENT_TREE), (CLIENT_TREE, SERVER_TREE)):
        path = tree / rel
        assert path.is_file(), f"{path.relative_to(REPO_ROOT)} is missing"
        twin = (other / rel).relative_to(REPO_ROOT)
        assert re.search(rf"^#\s*Twin of {re.escape(str(twin))}\s", path.read_text(), re.MULTILINE), (
            f"{path.relative_to(REPO_ROOT)} does not declare itself a twin of {twin}"
        )


def test_the_image_installs_the_firewall_and_enables_it():
    """A card flashed from the image starts with the firewall on, in both roles.

    `milo-first-boot` converts a device to a satellite without touching these
    files or the unit, which is why the image installs the server's copies once.
    Fails when the stage no longer copies one of the three files to the path the
    others name, or no longer enables nftables.service — a ruleset installed and
    never loaded.
    """
    stage = _strip_comments(_stage_text()).replace("\\\n", " ")
    assert "on_chroot" in stage, "the pi-gen stage was not read"
    for rel in FIREWALL_FILES:
        destination = re.escape(f"/{rel}")
        directory = re.escape(f"/{Path(rel).parent}/")
        assert re.search(
            rf"\bcp\s+/home/milo/milo/rootfs/{re.escape(rel)}\s+(?:{destination}|{directory})\s*$",
            stage, re.MULTILINE,
        ), f"pi-gen/stage-milo never installs rootfs/{rel} at /{rel}"
    assert re.search(r"^\s*systemctl enable nftables\.service\b", stage, re.MULTILINE), (
        "pi-gen/stage-milo never enables nftables.service"
    )


# --------------------------------------------------------------------------- #
# The model's own check against simulated drift.
# --------------------------------------------------------------------------- #

def _with_first_rule(rule: str) -> str:
    text = RULESET.read_text()
    anchor = '        iif "lo" accept\n'
    assert anchor in text, "the loopback rule moved; re-anchor the drift cases"
    return text.replace(anchor, f"        {rule}\n{anchor}", 1)


_OPENINGS = {
    "a listener's port": _with_first_rule("tcp dport 7000 accept"),
    "a port range": _with_first_rule("tcp dport { 20-25, 8000 } counter accept"),
    "every source": _with_first_rule("ip6 saddr ::/0 accept"),
    "a whole protocol": _with_first_rule("meta l4proto { tcp, udp } accept"),
    "a policy of accept": RULESET.read_text().replace("policy drop;", "policy accept;"),
    "new connections": _with_first_rule("ct state new,established accept"),
    "the WiFi link": _with_first_rule('iifname "wlan0" accept'),
}


@pytest.mark.parametrize("case", sorted(_OPENINGS))
def test_the_model_sees_a_listener_opened_to_the_internet(case):
    """Each way a rule opens a listener to every source makes the checks above red."""
    assert _open_to_the_internet(_verdict(_OPENINGS[case])), f"{case}: went unseen"


@pytest.mark.parametrize("rule", ["icmpv6 type echo-request accept", "meta l4proto ipv6-icmp accept"])
def test_the_model_sees_more_icmpv6_opened_to_the_internet(rule):
    """An extra ICMPv6 type is seen, so the exact-set check above is not vacuous."""
    accepted = _icmpv6_from_the_internet(_verdict(_with_first_rule(rule)))
    assert 128 in accepted and ICMPV6_FROM_ANYWHERE < accepted


@pytest.mark.parametrize("rule", [
    "jump allow_all",
    "tcp dport 7000 log prefix \"in\" accept",
    "ip6 saddr != 2001:db8::/32 drop",
    "tcp dport 7000",
    "meta l4proto sctp accept",
])
def test_the_model_refuses_what_it_does_not_know(rule):
    """A statement left unmodeled would be skipped and its verdict misread."""
    with pytest.raises(AssertionError):
        _verdict(_with_first_rule(rule))
