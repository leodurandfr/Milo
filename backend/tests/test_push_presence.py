"""`DevicePresence` — whether a device holding the card is still on the LAN.

Read off the kernel's neighbour table, which the measurements of 2026-11-25
found reliable where ping was not (see the module docstring).
"""

from typing import List, Optional

import pytest

from backend.core.push.presence import DevicePresence, lan_ipv4


class _Table:
    """A neighbour table answering a scripted state per read."""

    def __init__(self, *states: Optional[str]):
        self.states = list(states)
        self.probed: List[str] = []

    async def read(self, address: str) -> Optional[str]:
        return self.states.pop(0)

    def probe(self, address: str) -> None:
        self.probed.append(address)


def _watch(table: _Table):
    changes = []
    presence = DevicePresence(
        on_change=lambda device, here: changes.append((device, here)),
        read_state=table.read, send_probe=table.probe,
    )
    presence.note("phone-1", "192.168.1.179")
    return presence, changes


async def _run(presence, times: int):
    for _ in range(times):
        await presence.check(["phone-1"])


class TestLanAddress:
    @pytest.mark.parametrize("host, expected", [
        ("192.168.1.179", "192.168.1.179"),
        ("::ffff:192.168.1.179", "192.168.1.179"),
        ("10.0.0.4", "10.0.0.4"),
        ("127.0.0.1", None),            # nginx itself
        ("100.64.0.7", None),          # Tailscale: says nothing of the Wi-Fi
        ("8.8.8.8", None),
        ("169.254.1.2", None),
        ("testclient", None),
        (None, None),
    ])
    def test_only_a_lan_address_is_watched(self, host, expected):
        assert lan_ipv4(host) == expected


class TestVerdicts:
    async def test_a_departure_is_reported_once(self):
        presence, changes = _watch(_Table("REACHABLE", "REACHABLE", "FAILED", "INCOMPLETE"))
        await _run(presence, 4)
        assert changes == [("phone-1", False)]

    async def test_a_return_is_reported_too(self):
        """Back on the LAN, its card is filled again at once rather than at the
        next track."""
        presence, changes = _watch(_Table("REACHABLE", "FAILED", "REACHABLE"))
        await _run(presence, 3)
        assert changes == [("phone-1", False), ("phone-1", True)]

    async def test_undecided_states_keep_the_verdict(self):
        """STALE, DELAY and PROBE are the kernel on its way to asking again —
        the cycle a sleeping phone went through every 20 to 45 s."""
        presence, changes = _watch(
            _Table("REACHABLE", "STALE", "DELAY", "PROBE", None, "REACHABLE"))
        await _run(presence, 6)
        assert changes == []

    async def test_the_first_verdict_is_not_a_change(self):
        """A device first seen gone left before anything here knew of it."""
        presence, changes = _watch(_Table("FAILED", "FAILED"))
        await _run(presence, 2)
        assert changes == []

    async def test_a_new_address_starts_over(self):
        presence, changes = _watch(_Table("REACHABLE", "FAILED"))
        await _run(presence, 1)
        presence.note("phone-1", "192.168.1.180")
        await _run(presence, 1)
        assert changes == []

    async def test_each_check_probes_for_the_next(self):
        table = _Table("REACHABLE", "REACHABLE")
        presence, _ = _watch(table)
        await _run(presence, 2)
        assert table.probed == ["192.168.1.179", "192.168.1.179"]

    async def test_a_device_without_a_lan_address_is_not_probed(self):
        table = _Table()
        presence = DevicePresence(on_change=lambda *a: None,
                                  read_state=table.read, send_probe=table.probe)
        presence.note("phone-1", "100.64.0.7")
        await presence.check(["phone-1"])
        assert table.probed == []
