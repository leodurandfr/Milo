"""Is a device that holds the Now Playing card still on the LAN?

The card's extension on the phone empties the card when Milō is out of reach
(Milo-iOS `MiloRemoteSession.miloReachable`), but only while it runs, and iOS
does not wake it for a network change: it sleeps between pushes. Measured
2026-11-25: Wi-Fi off at 15:19:00.49, the extension still asleep, the card full
with its buttons until a press woke it at 15:19:20.42; and a second time, Wi-Fi
off at 15:54:49, the card full until the next track's push at 15:56:12. A push
is what wakes it, and only Milō sends pushes, so Milō has to notice the phone
leaving.

**ARP, not ICMP.** Locked and on battery, the phone left ping unanswered for up
to 50 s at a time (15:38:59 to 15:39:49, 15:46:03 to 15:46:54): a sleep looks
like a departure. Its Wi-Fi chip answers ARP on its own while it sleeps: six
locked minutes without a single failed resolution (16:00 to 16:06), and Wi-Fi
off at 16:06:15.9 read FAILED at 16:06:21. Sending a raw ARP request needs
CAP_NET_RAW, which this process does not have; any datagram does it
instead, since the kernel resolves the address to send it, and its neighbour
table says how that went. The datagram goes to the discard port and nobody
reads it.

What it costs to learn: a resolution the kernel still holds as REACHABLE is
not asked again until that state lapses (15 to 45 s, then 5 s of DELAY and 3 s
of probes), so a departure reads in 5 s to about a minute. Undecided states
(STALE, DELAY, PROBE) keep the last verdict.
"""

import asyncio
import ipaddress
import logging
import socket
from typing import Awaitable, Callable, Dict, Iterable, Optional

logger = logging.getLogger(__name__)

POLL_S = 2.0
DISCARD_PORT = 9

GONE = frozenset({"FAILED", "INCOMPLETE"})
HERE = frozenset({"REACHABLE", "PERMANENT", "NOARP"})

# Carrier-grade NAT addresses (RFC 6598, 100.64.0.0/10), what a VPN overlay
# hands out, are not on this LAN: the phone reaching Milō through one says
# nothing about its Wi-Fi.
_NOT_LAN = (ipaddress.ip_network("100.64.0.0/10"),)


def lan_ipv4(host: Optional[str]) -> Optional[str]:
    """The address, if it is a private IPv4 address on a LAN; else None."""
    if not host:
        return None
    try:
        address = ipaddress.ip_address(host.removeprefix("::ffff:"))
    except ValueError:
        return None
    if address.version != 4 or address.is_loopback or address.is_link_local:
        return None
    if not address.is_private or any(address in net for net in _NOT_LAN):
        return None
    return str(address)


async def _run(*argv: str) -> str:
    process = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    out, _ = await process.communicate()
    return out.decode(errors="replace")


async def neighbor_state(address: str) -> Optional[str]:
    """The kernel's neighbour state for `address` on the interface that routes
    to it: REACHABLE, STALE, FAILED... None when it holds no entry.

    Read on that interface only: the Pi can hold the same address on eth0 and
    on wlan0, and the entry nobody sends through stays STALE forever.
    """
    route = (await _run("ip", "-4", "route", "get", address)).split()
    device = route[route.index("dev") + 1] if "dev" in route else None
    argv = ["ip", "-4", "neigh", "show", "to", address]
    if device:
        argv += ["dev", device]
    words = (await _run(*argv)).split()
    return words[-1] if words else None


def nudge(address: str) -> None:
    """Make the kernel resolve `address`: one datagram to the discard port."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setblocking(False)
            sock.sendto(b"\0", (address, DISCARD_PORT))
    except OSError:
        # EHOSTUNREACH once the entry has failed: the kernel answers with
        # the verdict we are about to read anyway.
        pass


class DevicePresence:
    """Follows, per device, whether its last known LAN address still answers.

    `on_change(device_id, here)` is called on a change of verdict only, never on
    the first one: a device first seen gone was gone before anything here knew
    of it, and has nothing on its card to take back.
    """

    def __init__(
        self,
        on_change: Callable[[str, bool], None],
        read_state: Callable[[str], Awaitable[Optional[str]]] = neighbor_state,
        send_probe: Callable[[str], None] = nudge,
    ):
        self._on_change = on_change
        self._read_state = read_state
        self._send_probe = send_probe
        self._addresses: Dict[str, str] = {}
        self._here: Dict[str, bool] = {}

    def note(self, device_id: str, host: Optional[str]) -> None:
        """The address a device's request came from. A new one starts over."""
        address = lan_ipv4(host)
        if address is None or self._addresses.get(device_id) == address:
            return
        self._addresses[device_id] = address
        self._here.pop(device_id, None)

    async def check(self, device_ids: Iterable[str]) -> None:
        """Read what the last probe of each device gave, then probe again."""
        for device_id in device_ids:
            address = self._addresses.get(device_id)
            if address is None:
                continue
            state = await self._read_state(address)
            here = True if state in HERE else False if state in GONE else None
            if here is not None:
                before = self._here.get(device_id)
                self._here[device_id] = here
                if before is not None and before != here:
                    self._on_change(device_id, here)
            self._send_probe(address)
