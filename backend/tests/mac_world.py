"""The Mac source's outside world, as measured on the unit (docs: source
architecture, phase 3d): roc-recv 0.4.0 under milo-mac.service, its journal,
systemd holding it, and each Mac's own mDNS responder naming it.

What roc-recv was measured to do (2026-09-24, the owner's Mac with roc-vad):

- A Mac picking Milō as its output: `session group: creating session:
  src_addr=<ip>:<port>` — the one line carrying the sender's address.
- The Mac picking another output (or sleeping, or losing the network): the
  packets stop and roc-recv's watchdog ends the session ~0.3 s later —
  `removing session`, then `removing route: … address=<ip>:<port>`.
- roc-recv stopped (a source switch, a backend restart) writes nothing about
  its sessions: they end with the process, unannounced.
- roc-recv killed: systemd's `Restart=always` brings a new process 5 s later,
  under a new invocation, and a Mac still streaming reattaches to it at once
  (same address, the connect line within the process's first milliseconds).
- A Mac's Bonjour responder answers a legacy unicast query sent to its
  <ip>:5353 (2026-09-25, macOS 26): one response per query, echoing its id and
  its questions, answering each question it holds records for — the reverse
  PTR with its host, a service type with its instance, whose SRV target and
  address ride along as additionals — and nothing at all when it holds none.
  A host with no responder never answers.

The journal is a list of (microseconds, pid, line), and `journalctl -o json`
prints each entry with its `_PID`, as journald does. `journalctl -f` honors `-n`
and `--since=@<seconds>`, a bound on the time journald *received* a line
(a stdout stream carries no source timestamp, measured) — so a line an exiting
roc-recv wrote can land after the next one started (`received_late`).
"""
import json
import asyncio
import ipaddress
import itertools
import struct
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from unittest.mock import AsyncMock, Mock

from backend.core import audio_source
from backend.core.models.audio_state import AudioSource
from backend.shared import journalctl as journalctl_module
from backend.sources.mac import mdns as mdns_module
from backend.sources.mac import source as mac_module
from backend.sources.mac.source import MacSource
from backend.tests.golden.harness import (
    AsyncioProxy, WireReader, make_settings, make_state_machine, settle,
)

UNIT = "milo-mac.service"
FIRST_PID = 111424

MINI_IP = "192.168.1.173"   # Ethernet; the same Mac is on Wi-Fi at .21
AIR_IP = "192.168.1.34"     # publishes no service: its host name is all there is
SILENT_IP = "192.168.1.120"  # no responder at all
MINI_NAME = "Mac mini de Léo"
AIR_NAME = "MacBook-Air-de-Camille"


PORT = 53721


def connect_lines(ip: str, port: int = PORT) -> List[str]:
    return [
        '[dbg] roc_pipeline: [receiver_session_router.cpp:350] session router: '
        'creating route: ssrc=2158310017 cname="3c66" address=<none> has_session=0',
        f"[inf] roc_pipeline: [receiver_session_group.cpp:374] session group: "
        f"creating session: src_addr={ip}:{port} dst_addr=0.0.0.0:10001",
        f"[dbg] roc_rtcp: [reporter.cpp:1451] rtcp reporter: creating address: remote_addr={ip}:{port}",
    ]


def leave_lines(ip: str, port: int = PORT) -> List[str]:
    return [
        "[dbg] roc_audio: [watchdog.cpp:179] watchdog: no_playback timeout reached",
        "[inf] roc_pipeline: [receiver_session_group.cpp:416] session group: removing session",
        f"[dbg] roc_pipeline: [receiver_session_router.cpp:425] session router: removing route: "
        f'n_ssrcs=1 cname="3c66" address={ip}:{port} has_session=1',
    ]


# === Answers captured off the LAN (2026-09-25) ===
# Verbatim but for privacy: every TXT string overwritten with 'x' and each
# global IPv6 address moved into 2001:db8::/32, byte for byte, so every length
# and compression pointer is the responder's own.

# The owner's Mac mini (macOS 26) at .173, asked in one query for the reverse
# PTR of 192.168.1.173, the four device-level services and the service types.
MINI_ANSWER = bytes.fromhex(
    "db7a84000006000c0000000c033137330131033136380331393207696e2d616464720461"
    "72706100000c00010f5f636f6d70616e696f6e2d6c696e6b045f746370056c6f63616c00"
    "000c0001085f616972706c6179c03c000c0001055f72616f70c03c000c0001045f726662"
    "c03c000c0001095f7365727669636573075f646e732d7364045f756470c041000c0001c0"
    "72000c00010000000a0007045f737368c03cc072000c00010000000a000c095f73667470"
    "2d737368c03cc072000c00010000000a0002c04cc04c000c00010000000a0013104d6163"
    "206d696e69206465204cc3a96fc04cc072000c00010000000a0002c05bc05b000c000100"
    "00000a00201d324546364231323234303532404d6163206d696e69206465204cc3a96fc0"
    "5bc072000c00010000000a0002c02cc02c000c00010000000a0013104d6163206d696e69"
    "206465204cc3a96fc02cc00c000c00010000000a00120f4d61632d6d696e692d64652d4c"
    "656fc041c072000c00010000000a0002c067c067000c00010000000a0013104d6163206d"
    "696e69206465204cc3a96fc067c072000c00010000000a000a075f617371756963c084c0"
    "d4002100010000000a0008000000001b58c15ac0d4001000010000000a015f0578787878"
    "781a78787878787878787878787878787878787878787878787878781478787878787878"
    "787878787878787878787878781e78787878787878787878787878787878787878787878"
    "78787878787878780b787878787878787878787828787878787878787878787878787878"
    "787878787878787878787878787878787878787878787878780578787878780678787878"
    "78780e787878787878787878787878787804787878780d78787878787878787878787878"
    "277878787878787878787878787878787878787878787878787878787878787878787878"
    "787878782878787878787878787878787878787878787878787878787878787878787878"
    "787878787878787878437878787878787878787878787878787878787878787878787878"
    "787878787878787878787878787878787878787878787878787878787878787878787878"
    "78787878781078787878787878787878787878787878104d6163206d696e69206465204c"
    "c3a96f0c5f6465766963652d696e666fc03c001000010000000a00230e78787878787878"
    "787878787878780a78787878787878787878087878787878787878c10100210001000000"
    "0a0008000000001b58c15ac101001000010000000a00b80a787878787878787878780778"
    "787878787878087878787878787878187878787878787878787878787878787878787878"
    "787878780878787878787878780878787878787878780b78787878787878787878784378"
    "787878787878787878787878787878787878787878787878787878787878787878787878"
    "787878787878787878787878787878787878787878787878787878787878067878787878"
    "780878787878787878780b78787878787878787878780478787878c13b00210001000000"
    "0a000800000000c2aac15ac13b001000010000000a007f07787878787878781178787878"
    "787878787878787878787878780c78787878787878787878787811787878787878787878"
    "78787878787878780a787878787878787878781178787878787878787878787878787878"
    "781178787878787878787878787878787878781678787878787878787878787878787878"
    "787878787878c186002100010000000a000800000000170cc15ac186001000010000000a"
    "000100c15a001c00010000000a0010fe800000000000001490b7fc9300750ac15a000100"
    "010000000a0004c0a801adc15a001c00010000000a001020010db80000000004f331bcc6"
    "96c567"
)

# The Freebox at .254, a responder publishing none of the device-level
# services: the same first query, then the PTR of each type it listed.
FREEBOX_TYPES_ANSWER = bytes.fromhex(
    "8c1f84000006000800000000033235340131033136380331393207696e2d616464720461"
    "72706100000c00010f5f636f6d70616e696f6e2d6c696e6b045f746370056c6f63616c00"
    "000c0001085f616972706c6179c03c000c0001055f72616f70c03c000c0001045f726662"
    "c03c000c0001095f7365727669636573075f646e732d7364045f756470c041000c0001c0"
    "72000c00010000000a0007045f736d62c03cc072000c00010000000a000e0b5f6166706f"
    "766572746370c03cc072000c00010000000a000f0c5f6465766963652d696e666fc03cc0"
    "72000c00010000000a0009065f616469736bc03cc072000c00010000000a000b085f6662"
    "782d617069c03cc072000c00010000000a0008055f68747470c03cc072000c0001000000"
    "0a0009065f6874747073c03cc00c000c00010000000a00110e46726565626f782d536572"
    "766572c041"
)

FREEBOX_WALK_ANSWER = bytes.fromhex(
    "187284000007000f00000000045f736d62045f746370056c6f63616c00000c00010b5f61"
    "66706f766572746370c011000c00010c5f6465766963652d696e666fc011000c0001065f"
    "616469736bc011000c0001085f6662782d617069c011000c0001055f68747470c011000c"
    "0001065f6874747073c011000c0001c06e000c00010000000a00110e46726565626f7820"
    "536572766572c06ec087001000010000000a000100c087002100010000000a0017000000"
    "0001bb0e46726565626f782d536572766572c016c0b7000100010000000a0004c0a801fe"
    "c062000c00010000000a00110e46726565626f7820536572766572c062c0e40010000100"
    "00000a000100c0e4002100010000000a0008000000000050c0b7c053000c00010000000a"
    "00110e46726565626f7820536572766572c053c122002100010000000a00080000000000"
    "50c0b7c046000c00010000000a00110e46726565626f7820536572766572c046c1530010"
    "00010000000a002625787878787878787878787878787878787878787878787878787878"
    "78787878787878787878c153002100010000000a0008000000000009c0b7c033000c0001"
    "0000000a00110e46726565626f7820536572766572c033c1b6001000010000000a000e0d"
    "78787878787878787878787878c1b6002100010000000a0008000000000000c0b7"
)


# === The LAN's mDNS responders, at the UDP boundary ===

def _encode_name(name: str) -> bytes:
    out = b""
    for label in name.rstrip(".").split("."):
        raw = label.encode()
        out += bytes([len(raw)]) + raw
    return out + b"\0"


def _read_questions(query: bytes) -> List[Tuple[str, int]]:
    """A query's questions — plain names, a querier compresses nothing."""
    count = struct.unpack_from(">H", query, 4)[0]
    offset, questions = 12, []
    for _ in range(count):
        labels = []
        while query[offset]:
            length = query[offset]
            labels.append(query[offset + 1:offset + 1 + length].decode())
            offset += 1 + length
        qtype = struct.unpack_from(">H", query, offset + 1)[0]
        offset += 5
        questions.append((".".join(labels), qtype))
    return questions


def _record(name: str, rtype: int, rdata: bytes) -> bytes:
    return _encode_name(name) + struct.pack(">HHIH", rtype, 1, 10, len(rdata)) + rdata


class MacResponder:
    """A Mac's mDNSResponder, as measured: its host, and the instance each
    service type it publishes goes by (`services`: type -> instance label)."""

    def __init__(self, ip: str, host: str, services: Dict[str, str]) -> None:
        self.ip, self.host, self.services = ip, host, services

    def __call__(self, query: bytes) -> List[bytes]:
        questions = _read_questions(query)
        answers, extras = [], []
        for name, qtype in questions:
            if qtype != 12:
                continue
            if name == ipaddress.ip_address(self.ip).reverse_pointer:
                answers.append(_record(name, 12, _encode_name(self.host)))
            elif name == "_services._dns-sd._udp.local":
                answers += [_record(name, 12, _encode_name(t)) for t in self.services]
            elif name in self.services:
                instance = f"{self.services[name]}.{name}"
                answers.append(_record(name, 12, _encode_name(instance)))
                srv = struct.pack(">HHH", 0, 0, 7000) + _encode_name(self.host)
                extras += [_record(instance, 33, srv), _record(instance, 16, b"\x00")]
        if not answers:
            return []
        extras.append(_record(self.host, 1, ipaddress.IPv4Address(self.ip).packed))
        header = query[:2] + struct.pack(">HHHHH", 0x8400, len(questions), len(answers), 0, len(extras))
        return [header + query[12:] + b"".join(answers + extras)]


RESPONDERS: Dict[str, Callable[[bytes], List[bytes]]] = {
    MINI_IP: MacResponder(MINI_IP, "Mac-mini-de-Leo.local", {
        "_companion-link._tcp.local": MINI_NAME,
        "_raop._tcp.local": f"2EF6B1224052@{MINI_NAME}",
        "_smb._tcp.local": MINI_NAME,
    }),
    AIR_IP: MacResponder(AIR_IP, f"{AIR_NAME}.local", {}),
}


class _Transport:
    def __init__(self, lan: "FakeMdns", ip: str, protocol: Any) -> None:
        self.lan, self.ip, self.protocol = lan, ip, protocol
        self.closed = False

    def sendto(self, data: bytes, addr: Any = None) -> None:
        self.lan.queries.append((self.ip, _read_questions(data)))
        if self.ip in self.lan.closed_ports:
            # Nothing listens on 5353 there: the host answers ICMP port
            # unreachable, which asyncio hands to the connected socket.
            self.protocol.error_received(ConnectionRefusedError(111, "Connection refused"))
            return
        responder = self.lan.responders.get(self.ip)
        if responder is not None:
            self.lan.answer(self, responder(data))

    def close(self) -> None:
        self.closed = True


class FakeMdns:
    """What the resolver reaches through `create_datagram_endpoint` — a socket
    connected to <ip>:5353 — and the clock its bound runs on, which moves
    only when a scenario says time passes."""

    def __init__(self, responders: Dict[str, Callable[[bytes], List[bytes]]]) -> None:
        self.responders = dict(responders)
        self.queries: List[Tuple[str, List[Tuple[str, int]]]] = []
        self.remotes: List[Any] = []
        self.gates: Dict[str, asyncio.Event] = {}
        self.refuses: Optional[OSError] = None
        self.closed_ports: set = set()      # hosts up with no mDNS responder
        self._now = 0.0
        self._sleepers: List[Tuple[float, asyncio.Future]] = []
        self._pending: List[asyncio.Task] = []

    def install(self, monkeypatch) -> None:
        proxy = AsyncioProxy(self._sleep)
        proxy.get_running_loop = lambda: self
        monkeypatch.setattr(mdns_module, "asyncio", proxy)

    async def create_datagram_endpoint(self, factory, remote_addr, **_: Any):
        self.remotes.append(remote_addr)
        if self.refuses is not None:
            raise self.refuses
        protocol = factory()
        transport = _Transport(self, remote_addr[0].split("%", 1)[0], protocol)
        protocol.connection_made(transport)
        return transport, protocol

    def answer(self, transport: _Transport, packets: Sequence[bytes]) -> None:
        async def deliver() -> None:
            gate = self.gates.get(transport.ip)
            if gate is not None:
                await gate.wait()
            for packet in packets:
                if not transport.closed:
                    transport.protocol.datagram_received(packet, (transport.ip, 5353))
        self._pending.append(asyncio.ensure_future(deliver()))

    def asked(self) -> List[str]:
        return [ip for ip, _ in self.queries]

    async def _sleep(self, delay: float, *a: Any, **k: Any) -> None:
        wake = asyncio.get_running_loop().create_future()
        self._sleepers.append((self._now + delay, wake))
        await wake

    def time_passes(self, seconds: float) -> None:
        self._now += seconds
        for at, wake in list(self._sleepers):
            if at <= self._now and not wake.done():
                wake.set_result(None)

    def release(self) -> None:
        for gate in self.gates.values():
            gate.set()


class _Pipe:
    def __init__(self) -> None:
        self.lines: asyncio.Queue = asyncio.Queue()

    async def readline(self) -> bytes:
        return await self.lines.get()


class _Follow:
    """`journalctl -u milo-mac.service -f`: what it replays, then what comes."""

    def __init__(self, since_usec: Optional[int], output: Optional[str]) -> None:
        self.stdout = _Pipe()
        self.returncode: Optional[int] = None
        self.since_usec = since_usec
        self.output = output

    def print(self, pid: int, line: str) -> None:
        if self.output == "json":
            line = json.dumps({"MESSAGE": line, "_PID": str(pid), "SYSLOG_IDENTIFIER": "milo-mac"})
        self.stdout.lines.put_nowait(f"{line}\n".encode())

    def terminate(self) -> None:
        self.returncode = -15
        self.stdout.lines.put_nowait(b"")

    def kill(self) -> None:
        self.terminate()

    async def wait(self) -> int:
        return self.returncode


class _OneShot:
    def __init__(self, output: str, returncode: int = 0) -> None:
        self._output = output.encode()
        self.returncode = returncode

    async def communicate(self) -> Tuple[bytes, bytes]:
        return self._output, b""

    def kill(self) -> None:
        pass

    async def wait(self) -> int:
        return self.returncode


def _option(argv: Tuple[str, ...], flag: str) -> Optional[str]:
    for i, arg in enumerate(argv):
        if arg == flag and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith(flag + "="):
            return arg.split("=", 1)[1]
    return None


class MacWorld(WireReader):
    """The Mac source on a real state machine, in a world the scenario drives."""

    def __init__(self, monkeypatch) -> None:
        world = self
        self._usec = itertools.count(1_790_253_774_000_000, 1000)
        self._pids = itertools.count(FIRST_PID)
        self.pid: Optional[int] = None
        self.started_usec: Optional[int] = None
        self.journal: List[Tuple[int, int, str]] = []
        self.follows: List[_Follow] = []
        self.streaming: Dict[str, int] = {}       # Macs with Milō as their output: ip -> port
        self.watches: List[Tuple[int, Callable[[], None]]] = []
        self.mdns = FakeMdns(RESPONDERS)
        self.unit_state = ("inactive", "success")
        self.journal_refuses = False
        # The pidfd fallback: /proc checked every 10 s, so a death is heard late.
        self.watch_is_late = False

        class Watch:
            def __init__(self, pid: int, on_exit: Callable[[], None], *a: Any, **k: Any) -> None:
                self.on_exit = on_exit
                if world.watch_is_late:
                    return
                world.watches.append((pid, on_exit))
                if pid != world.pid:
                    asyncio.get_running_loop().call_soon(on_exit)

            def close(self) -> None:
                world.watches[:] = [w for w in world.watches if w[1] is not self.on_exit]

        systemd = Mock()
        systemd.start = AsyncMock(side_effect=self._unit_start)
        systemd.stop = AsyncMock(side_effect=self._unit_stop)
        systemd.restart = AsyncMock(side_effect=self._unit_restart)
        systemd.is_active = AsyncMock(side_effect=lambda *_: self.pid is not None)
        systemd.probe_active = AsyncMock(side_effect=lambda *_: self.pid is not None)
        self.pid_unreadable = False
        systemd.main_pid = AsyncMock(side_effect=lambda *_: None if self.pid_unreadable else self.pid)
        systemd.unit_state = AsyncMock(side_effect=lambda *_: self.unit_state)
        systemd.main_start_usec = AsyncMock(side_effect=lambda *_: self.started_usec)
        self.systemd = systemd

        proxy = AsyncioProxy(self._sleep)
        proxy.create_subprocess_exec = self._exec
        monkeypatch.setattr(mac_module, "asyncio", proxy)
        monkeypatch.setattr(journalctl_module, "asyncio", proxy)
        monkeypatch.setattr(audio_source, "asyncio", AsyncioProxy(self._sleep))
        monkeypatch.setattr(audio_source, "ProcessWatch", Watch, raising=False)
        self.mdns.install(monkeypatch)

        self.machine, self.recorder = make_state_machine()
        self.source = MacSource(
            {}, state_machine=self.machine, settings_service=make_settings(),
            systemd_manager=systemd,
        )
        self.machine.register_source(AudioSource.MAC, self.source)

    @staticmethod
    async def _sleep(delay: float, *a: Any, **k: Any) -> Any:
        # A unit given a second to settle passes at once; nothing here waits longer.
        return await asyncio.sleep(0)

    # === roc-recv and its journal ===

    def _log(self, lines: List[str], pid: Optional[int] = None) -> None:
        pid = self.pid if pid is None else pid
        for line in lines:
            usec = next(self._usec)
            self.journal.append((usec, pid, line))
            for follow in self.follows:
                if follow.returncode is None:
                    follow.print(pid, line)

    def _spawn(self) -> None:
        self.pid = next(self._pids)
        self.started_usec = next(self._usec)
        self.unit_state = ("active", "success")
        # Measured: a Mac still streaming reattaches to a new process at once.
        for ip, port in self.streaming.items():
            self._log(connect_lines(ip, port))

    def _die(self) -> None:
        pid, self.pid = self.pid, None
        self.started_usec = None
        for watched, on_exit in list(self.watches):
            if watched == pid:
                on_exit()

    async def _unit_start(self, *_: Any) -> bool:
        if self.pid is None:
            self._spawn()
        return True

    async def _unit_stop(self, *_: Any) -> bool:
        self.unit_state = ("inactive", "success")
        self._die()
        return True

    async def _unit_restart(self, *_: Any) -> bool:
        self._die()
        self._spawn()
        return True

    async def _exec(self, *argv: str, **_: Any):
        program = argv[0]
        if program == "journalctl":
            return self._journalctl(argv)
        raise AssertionError(f"unexpected process {argv}")

    def _journalctl(self, argv: Tuple[str, ...]):
        assert _option(argv, "-u") == UNIT, argv
        if self.journal_refuses:
            raise OSError("journalctl: cannot exec")
        since = _option(argv, "--since")
        since_usec = None
        if since is not None and since.startswith("@"):
            since_usec = round(float(since[1:]) * 1_000_000)
        entries = [
            (pid, line) for usec, pid, line in self.journal
            if since_usec is None or usec >= since_usec
        ]
        tail = _option(argv, "-n")
        if "-f" not in argv:
            # A one-shot read: what the journal holds, printed and done.
            entries = entries[-int(tail):] if tail is not None else entries
            return _OneShot("".join(f"{line}\n" for _, line in entries))
        follow = _Follow(since_usec, _option(argv, "-o"))
        replay = entries if tail == "all" else entries[-int(tail):] if tail and int(tail) else []
        for pid, line in replay:
            follow.print(pid, line)
        self.follows.append(follow)
        return follow

    # === the Macs, as measured ===

    async def mac_streams(self, ip: str = MINI_IP, port: int = PORT) -> None:
        """The Mac picks Milō as its output (from `port`: a new sender socket)."""
        self.streaming[ip] = port
        if self.pid is not None:
            self._log(connect_lines(ip, port))
        await settle()

    async def mac_leaves(self, ip: str = MINI_IP, port: Optional[int] = None) -> None:
        """The Mac picks another output: roc-recv's watchdog ends its session."""
        port = self.streaming.pop(ip) if port is None else port
        if self.pid is not None:
            self._log(leave_lines(ip, port))
        await settle()

    def stops_streaming(self, ip: str = MINI_IP) -> None:
        """The Mac picks another output while no roc-recv hears it."""
        del self.streaming[ip]

    def received_late(self, pid: int, lines: List[str]) -> None:
        """Lines an exited roc-recv wrote, received by journald only now."""
        self._log(lines, pid=pid)

    def name_is_slow(self, ip: str) -> asyncio.Event:
        """The Mac at `ip` holds its answer until the returned event is set."""
        gate = asyncio.Event()
        self.mdns.gates[ip] = gate
        return gate

    async def time_passes(self, seconds: float) -> None:
        self.mdns.time_passes(seconds)
        await settle()

    async def kill_roc_recv(self) -> None:
        self.unit_state = ("activating", "signal")
        self._die()
        await settle()

    async def systemd_restarts_it(self) -> None:
        self._spawn()
        await settle()

    # === Milō ===

    async def select(self) -> None:
        await self.machine.transition_to_source(AudioSource.MAC)
        await settle()

    async def leave(self) -> None:
        await self.machine.transition_to_source(AudioSource.NONE)
        await settle()

    async def reroute(self) -> None:
        async def apply_mode() -> None:
            return None
        await self.machine.reroute_active_source(apply_mode)
        await settle()

    # === what the wire says ===

    def names(self) -> List[str]:
        session = self.session()
        return session["senders"] if session else []

    def cleared(self) -> int:
        return len(self.envelopes("source", "error_cleared"))
