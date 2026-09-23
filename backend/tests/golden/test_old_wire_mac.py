"""Mac's old wire, scenario by scenario (see harness.py for the rules).

The outside world is two programs: roc-recv, whose journal is the only thing
that says a sender connected or left, and Avahi, which names the sender from
its IP. Both are faked as the processes the source spawns — `journalctl`
(the startup read and the follow) and `avahi-resolve`/`avahi-browse` — so a
stimulus is a line roc-recv logs, and a name is what the LAN advertises.
"""
import asyncio
from typing import Any, List, Optional, Tuple

import pytest

from backend.core import audio_source
from backend.core.models.audio_state import AudioSource
from backend.shared import journalctl as journalctl_module
from backend.sources.mac import source as mac_module
from backend.sources.mac.source import MacSource
from backend.tests.golden.harness import (
    AsyncioProxy, Wire, check_recording, instant_short_sleep, make_settings,
    make_state_machine, make_systemd, settle,
)

# Two Macs on the LAN. The mini advertises Bonjour services under its
# instance name; the Air advertises nothing, so only its hostname is left.
MINI_IP = "192.168.1.21"
AIR_IP = "192.168.1.34"
AVAHI_REVERSE = {
    MINI_IP: "a8fca8ba-7a2f-4862-8934-70b031dd2eab.home",
    AIR_IP: "MacBook-Air-de-Camille.home",
}
AVAHI_FORWARD = {
    "a8fca8ba-7a2f-4862-8934-70b031dd2eab.local": ("Mac-mini-de-Leo.local", MINI_IP),
}
AVAHI_BROWSE = "\n".join([
    r"+;eth0;IPv4;Mac\032mini\032de\032L\195\169o;_companion-link._tcp;local",
    rf"=;eth0;IPv4;Mac\032mini\032de\032L\195\169o;_companion-link._tcp;local;"
    rf"Mac-mini-de-Leo.local;{MINI_IP};49153;",
    rf"=;eth0;IPv4;Mac\032mini\032de\032L\195\169o;_smb._tcp;local;"
    rf"Mac-mini-de-Leo.local;{MINI_IP};445;",
    r"=;eth0;IPv4;NAS\032Leo;_smb._tcp;local;NAS-Leo.local;192.168.1.30;445;",
])


def roc_connect(ip: str) -> List[str]:
    """What roc-recv logs when a sender starts streaming: a session, then a route."""
    return [
        f"session group: creating session address={ip}:10003",
        f"session router: creating route: address={ip}:10003",
    ]


def roc_disconnect(ip: str) -> List[str]:
    return [f"session router: removing route: address={ip}:10003"]


class _Pipe:
    def __init__(self) -> None:
        self.lines: asyncio.Queue = asyncio.Queue()

    async def readline(self) -> bytes:
        return await self.lines.get()


class _FollowProcess:
    """`journalctl -u milo-mac.service -f`: prints what roc-recv logs from now on."""

    def __init__(self) -> None:
        self.stdout = _Pipe()
        self.returncode: Optional[int] = None

    def print(self, line: str) -> None:
        self.stdout.lines.put_nowait(f"{line}\n".encode())

    def terminate(self) -> None:
        self.returncode = -15
        self.stdout.lines.put_nowait(b"")

    def kill(self) -> None:
        self.terminate()

    async def wait(self) -> int:
        return self.returncode


class _OneShotProcess:
    """A command that prints its answer and exits."""

    def __init__(self, output: str, returncode: int = 0) -> None:
        self._output = output.encode()
        self.returncode = returncode

    async def communicate(self) -> Tuple[bytes, bytes]:
        return self._output, b""

    def kill(self) -> None:
        pass

    async def wait(self) -> int:
        return self.returncode


class FakeLan:
    """roc-recv's journal and the LAN's mDNS, as the spawned processes see them."""

    def __init__(self) -> None:
        self.history: List[str] = []      # journal lines logged before select
        self.follow: Optional[_FollowProcess] = None

    async def exec(self, *argv: str, **_: Any):
        program = argv[0]
        if program == "journalctl":
            if "-f" in argv:
                self.follow = _FollowProcess()
                return self.follow
            return _OneShotProcess("".join(f"{line}\n" for line in self.history))
        if program == "avahi-resolve":
            if "-a" in argv:
                ip = argv[-1]
                return self._answer(f"{ip}\t{AVAHI_REVERSE[ip]}" if ip in AVAHI_REVERSE else None)
            name = argv[-1]
            found = AVAHI_FORWARD.get(name)
            return self._answer(f"{found[0]}\t{found[1]}" if found else None)
        if program == "avahi-browse":
            return self._answer(AVAHI_BROWSE)
        raise AssertionError(f"unexpected process {argv}")

    @staticmethod
    def _answer(output: Optional[str]) -> _OneShotProcess:
        return _OneShotProcess(output, 0) if output is not None else _OneShotProcess("", 1)


class Mac:
    """Adapter: how each outside-world stimulus reaches MacSource today."""

    def __init__(self, monkeypatch) -> None:
        self.lan = FakeLan()
        fake = AsyncioProxy(instant_short_sleep)
        fake.create_subprocess_exec = self.lan.exec
        monkeypatch.setattr(mac_module, "asyncio", fake)
        monkeypatch.setattr(journalctl_module, "asyncio", fake)
        monkeypatch.setattr(audio_source, "asyncio", AsyncioProxy(instant_short_sleep))
        self.machine, recorder = make_state_machine()
        self.wire = Wire(self.machine, recorder)
        self.source = MacSource(
            {},
            state_machine=self.machine,
            settings_service=make_settings(),
            systemd_manager=make_systemd(),
        )
        self.machine.register_source(AudioSource.MAC, self.source)

    async def select(self) -> None:
        await self.machine.transition_to_source(AudioSource.MAC)
        await settle()

    async def deselect(self) -> None:
        await self.machine.transition_to_source(AudioSource.NONE)
        await settle()

    def logged_before_select(self, lines: List[str]) -> None:
        self.lan.history.extend(lines)

    async def roc_logs(self, lines: List[str]) -> None:
        for line in lines:
            self.lan.follow.print(line)
        await settle()


@pytest.fixture
def mac(monkeypatch):
    return Mac(monkeypatch)


async def test_select_and_leave(mac):
    await mac.select()
    await mac.wire.snapshot_rest()
    await mac.deselect()
    check_recording("mac", "select_and_leave", mac.wire)


async def test_one_mac_connects_by_its_bonjour_name(mac):
    await mac.select()
    await mac.roc_logs(roc_connect(MINI_IP))
    await mac.wire.snapshot_rest()
    await mac.deselect()
    check_recording("mac", "one_mac_connects_by_its_bonjour_name", mac.wire)


async def test_two_macs_then_one_leaves_then_the_other(mac):
    await mac.select()
    await mac.roc_logs(roc_connect(MINI_IP))
    await mac.roc_logs(roc_connect(AIR_IP))       # no Bonjour: its hostname
    await mac.wire.snapshot_rest()
    await mac.roc_logs(roc_disconnect(MINI_IP))
    await mac.wire.snapshot_rest()
    await mac.roc_logs(roc_disconnect(AIR_IP))
    await mac.wire.snapshot_rest()
    await mac.deselect()
    check_recording("mac", "two_macs_then_one_leaves_then_the_other", mac.wire)


async def test_already_streaming_at_select(mac):
    # A sender that connected before the source was selected is only in the
    # journal's history; a noise line and a trace line sit around it.
    mac.logged_before_select([
        "roc-recv: starting receiver",
        *roc_connect(MINI_IP),
        "[trc] pipeline: refresh deadline=0",
    ])
    await mac.select()
    await mac.wire.snapshot_rest()
    await mac.roc_logs(roc_connect(AIR_IP))
    await mac.roc_logs(roc_disconnect(AIR_IP) + roc_disconnect(MINI_IP))
    await mac.wire.snapshot_rest()
    await mac.deselect()
    check_recording("mac", "already_streaming_at_select", mac.wire)
