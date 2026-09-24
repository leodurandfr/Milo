"""The Mac source's outside world, as measured on the unit (docs: source
architecture, phase 3d): roc-recv 0.4.0 under milo-mac.service, its journal,
systemd holding it, and Avahi naming the senders.

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
- `avahi-resolve` answers an address nobody names over mDNS after 5 s; the
  owner's Mac cost 6.8 s from its connect line to its name.

The journal is a list of (microseconds, pid, line), and `journalctl -o json`
prints each entry with its `_PID`, as journald does. `journalctl -f` honors `-n`
and `--since=@<seconds>`, a bound on the time journald *received* a line
(a stdout stream carries no source timestamp, measured) — so a line an exiting
roc-recv wrote can land after the next one started (`received_late`).
"""
import json
import asyncio
import itertools
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, Mock

from backend.core import audio_source
from backend.core.models.audio_state import AudioSource
from backend.shared import journalctl as journalctl_module
from backend.sources.mac import source as mac_module
from backend.sources.mac.source import MacSource
from backend.tests.golden.harness import (
    AsyncioProxy, make_settings, make_state_machine, settle,
)

UNIT = "milo-mac.service"
FIRST_PID = 111424

MINI_IP = "192.168.1.173"   # streams ROC from here, advertises Bonjour on .21
AIR_IP = "192.168.1.34"     # advertises nothing: its router name is all there is
MINI_NAME = "Mac mini de Léo"
AIR_NAME = "MacBook-Air-de-Camille"

AVAHI_REVERSE = {
    MINI_IP: "a8fca8ba-7a2f-4862-8934-70b031dd2eab.home",
    AIR_IP: "MacBook-Air-de-Camille.home",
}
AVAHI_FORWARD = {
    "a8fca8ba-7a2f-4862-8934-70b031dd2eab.local": ("Mac-mini-de-Leo.local", "192.168.1.21"),
}
AVAHI_BROWSE = "\n".join([
    r"=;eth0;IPv4;Mac\032mini\032de\032L\195\169o;_companion-link._tcp;local;"
    r"Mac-mini-de-Leo.local;192.168.1.21;49153;",
    r"=;eth0;IPv4;Mac\032mini\032de\032L\195\169o;_smb._tcp;local;"
    r"Mac-mini-de-Leo.local;192.168.1.21;445;",
])


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
    def __init__(self, output: str, returncode: int = 0, gate: Optional[asyncio.Event] = None) -> None:
        self._output = output.encode()
        self.returncode = returncode
        self._gate = gate

    async def communicate(self) -> Tuple[bytes, bytes]:
        if self._gate is not None:
            await self._gate.wait()
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


class MacWorld:
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
        self.avahi_gates: Dict[str, asyncio.Event] = {}
        self.avahi_calls: List[Tuple[str, ...]] = []
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
        if program in ("avahi-resolve", "avahi-browse"):
            return self._avahi(argv)
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

    def _avahi(self, argv: Tuple[str, ...]):
        self.avahi_calls.append(argv)
        if argv[0] == "avahi-browse":
            return _OneShot(AVAHI_BROWSE)
        if "-a" in argv:
            ip = argv[-1]
            gate = self.avahi_gates.get(ip)
            name = AVAHI_REVERSE.get(ip)
            return _OneShot(f"{ip}\t{name}" if name else "", 0 if name else 1, gate)
        found = AVAHI_FORWARD.get(argv[-1])
        return _OneShot(f"{found[0]}\t{found[1]}" if found else "", 0 if found else 1)

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
        """Avahi takes its time to name `ip` (6.8 s for the owner's Mac): the
        reverse lookup answers when the returned event is set."""
        gate = asyncio.Event()
        self.avahi_gates[ip] = gate
        return gate

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

    def state(self) -> Dict[str, Any]:
        return self.machine.get_current_state()

    def active(self) -> bool:
        return self.state()["source_state"] == "active"

    def names(self) -> List[str]:
        return (self.state()["metadata"] or {}).get("client_names", [])

    def envelopes(self, category: str, kind: str) -> List[Dict[str, Any]]:
        return [e for e in self.recorder.envelopes if e["category"] == category and e["type"] == kind]

    def errors(self) -> List[str]:
        return [e["data"]["reason"] for e in self.envelopes("source", "error")]

    def cleared(self) -> int:
        return len(self.envelopes("source", "error_cleared"))

    def published(self) -> List[Dict[str, Any]]:
        out = []
        for e in self.envelopes("source", "state_changed"):
            full = (e.get("data") or {}).get("full_state")
            if full:
                out.append({"state": full["source_state"], **(full.get("metadata") or {})})
        return out
