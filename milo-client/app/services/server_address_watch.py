"""
Keep snapclient pointed at the main Milo when the server changes address, and on
the satellite's own cable when it has one.

`milo-client-snapclient-launcher` resolves the server once, at service start,
and execs snapclient with the literal IP — deliberately, so snapclient's own
reconnect loop never re-issues an mDNS lookup that can stall for seconds in the
middle of a drop. The cost is that the address is frozen for the life of the
process, and snapclient never exits on a failed connect: it retries the dead
literal forever, so `Restart=on-failure` never fires and nothing re-resolves.

Measured 2026-09-19: the server rebooted without its ethernet cable, came back
on WiFi with a new IP, and both satellites sat on `No route to host` until their
snapclient was restarted by hand. The registration loop next door had followed
the move on its own, because it re-resolves on every heartbeat.

This closes that asymmetry from the same side, in two states:

- **No connection to a snapserver.** The server lost the address snapclient
  dialled — its cable went, and it answers on WiFi now. The loss is seen by
  reading the socket table every second, which costs two procfs reads; once it
  is seen, the address is compared every 2 s for a minute, while mDNS catches
  up (avahi published the WiFi address 2.4 s after the cable went, measured
  2026-11-05). Pacing the check on the connected state instead sees a loss a
  whole interval late, because the loss always comes while connected: measured
  6 s on Bureau and 25 s on Canapé on 2026-11-05. Past that minute it falls
  back to every 30 s, since a snapserver stopped for hours (multiroom off) is a
  disconnection too.
- **Connected.** This is how a satellite comes back to the server's wired
  address once its cable has returned. snapclient re-dials the literal it
  holds, and when the server only restarts snapserver it is back in ~1 s
  (measured twice on 2026-11-05) — too fast to ever be seen disconnected — so
  it stayed on the WiFi address, through every backend restart, until someone
  restarted it by hand. The server publishes `milo.local` on one interface,
  eth0 whenever it has an IPv4 address (its NetworkManager dispatcher), so an
  answer other than the stream's own server address is the server preferring
  another link. Once mDNS has given the same other answer for `SERVER_HOLD`,
  the stream is restarted, playing or not: the owner chose Ethernet first, at
  the cost of a few seconds of silence in that room (2026-11-06). Waiting for
  the room to fall silent instead kept a room that played on the server's WiFi
  for as long as it played. The hold keeps an answer that does not last —
  avahi restarts on the other interface at each move — from cutting a room,
  and it backs off as the cable's hold does (below): a server cable that flaps
  slower than the hold would otherwise cost a return and a loss per flap, so a
  return the stream does not keep for `RETURN_SETTLE` doubles it, up to
  `SERVER_HOLD_MAX`. A return also opens the cooldown, which ends as soon as
  the restarted client has connected and lost that connection: the server's
  cable going again right after it is a loss like the first, followed in
  seconds rather than at the end of the cooldown.
  The stream's server address is read from the socket table, so this path
  spawns nothing; the lookup, every `SERVER_CHECK_INTERVAL`, is answered from
  avahi's cache, as the registration's every heartbeat is.

The lookup runs here, in the API process, and never in snapclient's reconnect
path — which is what the launcher's design protects.

The satellite's own link is followed too, under the same choice. Losing the
cable is left to snapclient, and that half is expected, not measured: no
satellite cable has been pulled yet. The stream's address goes with the cable,
snapclient's time sync should fail within 2-3 s (what it did when the *server*
lost its cable) and its re-dial should leave from the WiFi address — but only
once the kernel stops choosing eth0's routes, which it keeps marked `linkdown`
until NetworkManager removes the address unless `ignore_routes_with_linkdown` is
set — which `/etc/sysctl.d/90-milo-linkdown.conf` now does, shipped by the update
and the image. How fast the re-dial then is has still to be measured on a
real unplug.

Getting the cable back moves nothing by itself: new connections prefer eth0
(metric 100 against the WiFi's 600) but the open one keeps its source, and the
audio then arrives over wlan0 for as long as it lasts. Measured on Bureau the
same day: 3 min 38 s on its WiFi address after the replug, until a reboot. So
once eth0 has held its carrier and its address for the hold, and the kernel
would route a new connection to the server through it, a stream still leaving
from another address is restarted. The route check keeps a degraded eth0
(NetworkManager raises its metric) from being chased onto WiFi again at every
cooldown. The hold, `LINK_HOLD` at first, absorbs a cable that flaps faster
than it; a cable that flaps slower would earn a return, and a cut, at every
cooldown, so a return the cable does not keep for `RETURN_SETTLE` doubles the
hold, up to `LINK_HOLD_MAX`, and a stream that stays on the cable that long
brings it back to `LINK_HOLD`.

Only an IPv4 stream is moved, by either rule. The launcher's literal is IPv4
(the image resolves `.local` through `mdns4_minimal`); an IPv6 stream can exist
only on its hostname fallback, where snapclient resolved for itself, and the
check of a dropped stream replaces that hostname with a literal — after which
both rules apply.
"""
import asyncio
import fcntl
import ipaddress
import logging
import socket
import struct
from enum import Enum, auto
from pathlib import Path
from typing import Optional

from services.registration import local_ip_towards, resolve_milo_principal
from services.snapclient import SNAPCLIENT_SERVICE

logger = logging.getLogger(__name__)

PROC_CMDLINE = "/proc/{pid}/cmdline"
# Where the kernel lists this host's sockets. Both families are read, so the
# connection is seen whatever snapclient dialled: the launcher hands it an IPv4
# literal, but its hostname fallback lets snapclient resolve for itself, and
# that answer can be IPv6.
PROC_NET_TCP = ("/proc/net/tcp", "/proc/net/tcp6")
SNAPSERVER_PORT = 1704
WIRED_INTERFACE = "eth0"
WIRED_CARRIER = f"/sys/class/net/{WIRED_INTERFACE}/carrier"
SIOCGIFADDR = 0x8915

POLL_INTERVAL = 1  # seconds between two reads of the socket table
CHECK_INTERVAL = 30  # seconds, without a connection and past the loss window
# While connected: how often mDNS is asked, how long it must answer the same
# other address before the stream is moved there, and how far a server cable's
# own flapping can stretch that.
SERVER_CHECK_INTERVAL = 5
SERVER_HOLD = 10
SERVER_HOLD_MAX = 300
# After a lost connection, compare this often for this long: mDNS answers the
# new address a few seconds after the loss, and snapclient reconnects in < 1 s.
LOSS_CHECK_INTERVAL = 2
LOSS_WINDOW = 60
# After a restart, wait longer before comparing again: the unit needs time to
# dial, and a check-interval loop would otherwise stop/start it on every pass
# while it is still trying.
COOLDOWN_INTERVAL = 60
# How long eth0 must stay usable before a stream is moved onto it, and how far
# the cable's own flapping can stretch that.
LINK_HOLD = 10
LINK_HOLD_MAX = 300
# How long a stream must stay where a return put it — on the cable, or on the
# server address it followed — for that return to count as kept.
RETURN_SETTLE = 120

# Set between the stop and the start of a restart. A start that fails leaves the
# unit dead, and a dead unit has no address to compare — so without this the
# watcher would be unable to recover the very unit it just stopped, and the room
# would stay silent until someone rebooted it.
_start_owed = False
# The config route restarts the unit too; two interleaved stop/start pairs could
# leave it stopped with no debt recorded.
_restart_lock = asyncio.Lock()


def _server_arg(cmdline: bytes) -> Optional[str]:
    """The value of snapclient's `-h` flag, read out of a /proc cmdline blob."""
    args = [arg.decode(errors="replace") for arg in cmdline.split(b"\0") if arg]
    for i, arg in enumerate(args):
        if arg == "-h" and i + 1 < len(args):
            return args[i + 1]
    return None


def _address(hex_address: str) -> str:
    """An address as /proc/net/tcp(6) prints it: 32-bit words, each little-endian.

    An IPv4 connection listed in the tcp6 table (::ffff:a.b.c.d) reads as IPv4.
    """
    raw = bytes.fromhex(hex_address)
    address = ipaddress.ip_address(b"".join(raw[i:i + 4][::-1] for i in range(0, len(raw), 4)))
    if address.version == 6 and address.ipv4_mapped:
        return str(address.ipv4_mapped)
    return str(address)


def _snapserver_stream(proc_net_tcp: str) -> Optional[tuple[str, str]]:
    """(local, remote) address of the open snapcast socket in a /proc/net/tcp table.

    Columns are `sl local_address rem_address st …`, addresses hex `IP:PORT` and
    `st` hex; 01 is ESTABLISHED. Only snapclient talks to a snapserver from a
    satellite, so the remote port alone identifies the connection.
    """
    port = f"{SNAPSERVER_PORT:04X}"
    for line in proc_net_tcp.splitlines()[1:]:  # first line is the header
        columns = line.split()
        if len(columns) < 4:
            continue
        local, remote, state = columns[1], columns[2], columns[3]
        remote_address, _, remote_port = remote.rpartition(":")
        if state == "01" and remote_port.upper() == port:
            return _address(local.rpartition(":")[0]), _address(remote_address)
    return None


async def _stream() -> Optional[tuple[str, str]]:
    """(local, remote) address of snapclient's connection, or None when it has none."""
    for path in PROC_NET_TCP:
        try:
            table = await asyncio.to_thread(Path(path).read_text)
        except OSError:
            continue  # tcp6 is absent on a kernel built without IPv6
        stream = _snapserver_stream(table)
        if stream:
            return stream
    return None


def _wired_route(server: str) -> Optional[str]:
    """eth0's IPv4 address, when it has its carrier and a new connection to
    `server` would leave from it — else None.

    Syscalls only: a sysfs read, an ioctl, and a datagram connect that sends
    nothing. A carrier that cannot be read is an eth0 that is down or absent.
    """
    try:
        if Path(WIRED_CARRIER).read_text().strip() != "1":
            return None
    except OSError:
        return None
    ifreq = struct.pack("256s", WIRED_INTERFACE.encode())
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            wired = socket.inet_ntoa(fcntl.ioctl(s.fileno(), SIOCGIFADDR, ifreq)[20:24])
        preferred = local_ip_towards(server, SNAPSERVER_PORT)
    except OSError:
        return None  # no IPv4 on eth0 yet, or no route to the server at all
    return wired if preferred == wired else None


class _Link(Enum):
    """Where an IPv4 stream stands against eth0."""

    ON = auto()  # it leaves from eth0
    STRANDED = auto()  # a new connection would take eth0, and it leaves from elsewhere
    OFF = auto()  # a new connection would not take eth0


async def _cable_link(stream: Optional[tuple[str, str]]) -> Optional[_Link]:
    """Where the stream stands against eth0; None without a stream, or for an IPv6 one."""
    if stream is None:
        return None
    local, server = stream
    if ipaddress.ip_address(server).version != 4:
        return None
    wired = await asyncio.to_thread(_wired_route, server)
    if wired is None:
        return _Link.OFF
    return _Link.ON if local == wired else _Link.STRANDED


async def _main_pid() -> int:
    """PID of the snapclient unit, or 0 when it is not running.

    `systemctl show` needs no privilege. 0 is the answer for a satellite with no
    audio card configured, where the launcher exits 0 and the unit stays dead.
    """
    proc = await asyncio.create_subprocess_exec(
        "systemctl", "show", "--value", "-p", "MainPID", SNAPCLIENT_SERVICE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return 0
    try:
        return int(stdout.decode().strip())
    except ValueError:
        return 0


async def _running_server_address() -> Optional[str]:
    """The address snapclient was launched with, or None when it is not running."""
    pid = await _main_pid()
    if not pid:
        return None
    try:
        cmdline = await asyncio.to_thread(Path(PROC_CMDLINE.format(pid=pid)).read_bytes)
    except OSError:
        return None  # the process went away between the two reads
    return _server_arg(cmdline)


async def _stop_then_start() -> None:
    """Stop then start the unit — sudoers grants those two verbs, not `restart`."""
    global _start_owed
    for action in ("stop", "start"):
        proc = await asyncio.create_subprocess_exec(
            "sudo", "systemctl", action, SNAPCLIENT_SERVICE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"Failed to {action} snapclient: {stderr.decode().strip()}")
        _start_owed = action == "stop"


async def restart_snapclient() -> None:
    """Restart the unit; a start that fails raises and leaves the start owed, so
    the next pass of `follow_server_address` brings it back whoever stopped it."""
    async with _restart_lock:
        await _stop_then_start()


async def _reconcile() -> bool:
    """Restart a snapclient with no stream if the address it was launched with is
    stale. True if it was restarted."""
    if _start_owed:
        async with _restart_lock:
            # Read again under the lock: a restart in flight clears its own debt.
            if not _start_owed:
                return False
            logger.warning("Starting the snapclient a failed restart left stopped")
            await _stop_then_start()
            return True

    running = await _running_server_address()
    if running is None:
        return False

    # A literal MILO_PRINCIPAL_IP resolves to itself, so an operator-pinned
    # address on a LAN without mDNS can never read as a move.
    try:
        current = await asyncio.to_thread(resolve_milo_principal)
    except RuntimeError as e:
        logger.debug(f"Cannot check the server address: {e}")
        return False

    if running == current:
        return False

    logger.warning(f"Main Milo moved {running} -> {current}, restarting snapclient")
    await restart_snapclient()
    return True


async def _server_elsewhere(server: str) -> Optional[str]:
    """What mDNS answers for the main Milo, when that is not the stream's `server`.

    None for an IPv6 stream, whose server the IPv4 answer cannot be compared
    with, and when the lookup fails, which says nothing about the server.
    """
    if ipaddress.ip_address(server).version != 4:
        return None
    try:
        current = await asyncio.to_thread(resolve_milo_principal)
    except RuntimeError as e:
        logger.debug(f"Cannot check the server address: {e}")
        return None
    return current if current != server else None


class _ServerHold:
    """Whether mDNS has answered the same other address for the whole hold.

    Counted in readings, which the pacer spaces `SERVER_CHECK_INTERVAL` apart
    while connected; any other reading starts the hold again. The backoff is the
    cable's, counted in polls: a return followed within `RETURN_SETTLE` by a
    stream to another server doubles the hold, `RETURN_SETTLE` polls on the
    address it returned to restore it.
    """

    def __init__(self) -> None:
        self._hold = SERVER_HOLD
        self._address: Optional[str] = None
        self._readings = 0
        self._returned_to: Optional[str] = None
        self._on_it = 0
        self._unsettled = 0  # polls left in which another server undoes the last return

    def reset(self) -> None:
        self._address = None
        self._readings = 0

    def observe(self, server: Optional[str]) -> None:
        """One poll: the stream's server, None without a stream."""
        if server is None:
            self.reset()
        if self._unsettled:
            self._unsettled -= 1
            if server is not None and server != self._returned_to:
                self._hold = min(self._hold * 2, SERVER_HOLD_MAX)
                self._unsettled = 0
        self._on_it = self._on_it + 1 if server is not None and server == self._returned_to else 0
        if self._on_it >= RETURN_SETTLE // POLL_INTERVAL:
            self._hold = SERVER_HOLD

    def held(self, elsewhere: Optional[str]) -> bool:
        if elsewhere is None or elsewhere != self._address:
            self._readings = 0
        self._address = elsewhere
        if elsewhere is None:
            return False
        self._readings += 1
        return self._readings > self._hold // SERVER_CHECK_INTERVAL

    def returned(self, address: str) -> None:
        self.reset()
        self._returned_to = address
        self._on_it = 0
        self._unsettled = RETURN_SETTLE // POLL_INTERVAL


async def _follow_server(server: str, hold: _ServerHold) -> bool:
    """Restart a connected snapclient once the server has held another address."""
    current = await _server_elsewhere(server)
    if not hold.held(current):
        return False
    hold.returned(current)
    logger.warning(f"Main Milo moved {server} -> {current}, restarting snapclient")
    await restart_snapclient()
    return True


class _Pacer:
    """Which reads of the socket table also run an address check.

    Counted in polls, not read off a clock: the loop's own passes are the time.
    The cooldown after a restart lasts until the restarted client has connected
    and lost that connection: past that, a loss is the network's again.
    """

    def __init__(self) -> None:
        self._connected = False
        self._loss_polls = 0  # polls left in the fast window after a loss
        self._skip = 0  # polls to let pass before the next check
        self._cooling = False
        self._redialled = False  # the restarted client has connected since

    def due(self, connected: bool) -> bool:
        if self._cooling and self._redialled and self._connected and not connected:
            self._cooling = False
        if connected != self._connected and not self._cooling:
            self._skip = 0
        if self._cooling and connected:
            self._redialled = True
        if self._connected and not connected:
            self._loss_polls = LOSS_WINDOW // POLL_INTERVAL
        self._connected = connected
        if self._loss_polls:
            self._loss_polls -= 1
        if self._skip:
            self._skip -= 1
            return False
        return True

    def checked(self, restarted: bool) -> None:
        self._cooling = restarted
        self._redialled = False
        if restarted:
            interval = COOLDOWN_INTERVAL
        elif self._connected:
            interval = SERVER_CHECK_INTERVAL
        elif self._loss_polls:
            interval = LOSS_CHECK_INTERVAL
        else:
            interval = CHECK_INTERVAL
        self._skip = interval // POLL_INTERVAL - 1


class _CableHold:
    """Whether a stream stranded off the cable has been so for the whole hold.

    Counted in polls like `_Pacer`: one poll that breaks the condition starts the
    hold again. A return followed within `RETURN_SETTLE` by a poll off the cable
    doubles the hold; `RETURN_SETTLE` polls on the cable restore it. A restart for
    either reason opens the same cooldown here.
    """

    def __init__(self) -> None:
        self._hold = LINK_HOLD // POLL_INTERVAL
        self._held = 0
        self._on_cable = 0
        self._unsettled = 0  # polls left in which a loss undoes the last return
        self._cooling = 0

    def due(self, link: Optional[_Link]) -> bool:
        if self._cooling:
            self._cooling -= 1
        if self._unsettled:
            self._unsettled -= 1
            if link in (_Link.STRANDED, _Link.OFF):
                self._hold = min(self._hold * 2, LINK_HOLD_MAX // POLL_INTERVAL)
                self._unsettled = 0
        self._on_cable = self._on_cable + 1 if link is _Link.ON else 0
        if self._on_cable >= RETURN_SETTLE // POLL_INTERVAL:
            self._hold = LINK_HOLD // POLL_INTERVAL
        self._held = self._held + 1 if link is _Link.STRANDED else 0
        return self._held >= self._hold and not self._cooling

    def restarted(self, returned: bool = False) -> None:
        """A restart happened; `returned` when it was this hold's own, onto the cable."""
        self._held = 0
        self._cooling = COOLDOWN_INTERVAL // POLL_INTERVAL
        if returned:
            self._unsettled = RETURN_SETTLE // POLL_INTERVAL


async def follow_server_address() -> None:
    """Keep snapclient on the server's current address, and on eth0 when it is up."""
    pacer = _Pacer()
    server = _ServerHold()
    cable = _CableHold()
    while True:
        try:
            stream = await _stream()
            server.observe(stream[1] if stream else None)
            restarted = False
            if pacer.due(stream is not None):
                try:
                    if stream is None:
                        restarted = await _reconcile()
                    else:
                        restarted = await _follow_server(stream[1], server)
                finally:
                    pacer.checked(restarted)
            if restarted:
                cable.restarted()
            elif cable.due(await _cable_link(stream)):
                server.reset()
                logger.warning(f"Stream to {stream[1]} still leaves from {stream[0]} with "
                               f"{WIRED_INTERFACE} back, restarting snapclient")
                try:
                    await restart_snapclient()
                finally:
                    cable.restarted(returned=True)
                pacer.checked(True)
        except Exception as e:
            logger.error(f"Server address check failed: {e}")
        await asyncio.sleep(POLL_INTERVAL)
