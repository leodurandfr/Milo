"""
Keep snapclient pointed at the main Milo when the server changes address.

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

This closes that asymmetry from the same side, under one rule: **a client that
is streaming is never interrupted.** A new address only matters to a client that
has to dial again, so the comparison is made on a snapclient with no connection
to a snapserver — where a restart costs nothing, because there is no audio to
cut. A connected client keeps whatever address it dialled, however stale that
address has since become, until the connection drops on its own.

The lookup runs here, in the API process, and never in snapclient's reconnect
path — which is what the launcher's design protects.
"""
import asyncio
import logging
from pathlib import Path
from typing import Optional

from services.registration import resolve_milo_principal
from services.snapclient import SNAPCLIENT_SERVICE

logger = logging.getLogger(__name__)

PROC_CMDLINE = "/proc/{pid}/cmdline"
# Where the kernel lists this host's sockets. Both families are read: the
# launcher hands snapclient an IPv4 literal, but its hostname fallback lets
# snapclient resolve for itself, and that answer can be IPv6.
PROC_NET_TCP = ("/proc/net/tcp", "/proc/net/tcp6")
SNAPSERVER_PORT = 1704

CHECK_INTERVAL = 30  # seconds
# After a restart, wait longer before comparing again: the unit needs time to
# dial, and a check-interval loop would otherwise stop/start it on every pass
# while it is still trying.
COOLDOWN_INTERVAL = 60

# Set between the stop and the start of a restart. A start that fails leaves the
# unit dead, and a dead unit has no address to compare — so without this the
# watcher would be unable to recover the very unit it just stopped, and the room
# would stay silent until someone rebooted it.
_start_owed = False


def _server_arg(cmdline: bytes) -> Optional[str]:
    """The value of snapclient's `-h` flag, read out of a /proc cmdline blob."""
    args = [arg.decode(errors="replace") for arg in cmdline.split(b"\0") if arg]
    for i, arg in enumerate(args):
        if arg == "-h" and i + 1 < len(args):
            return args[i + 1]
    return None


def _holds_snapserver_connection(proc_net_tcp: str) -> bool:
    """True when one line of a /proc/net/tcp table is an open snapcast socket.

    Columns are `sl local_address rem_address st …`, addresses hex `IP:PORT` and
    `st` hex; 01 is ESTABLISHED. Only snapclient talks to a snapserver from a
    satellite, so the remote port alone identifies the connection.
    """
    port = f"{SNAPSERVER_PORT:04X}"
    for line in proc_net_tcp.splitlines()[1:]:  # first line is the header
        columns = line.split()
        if len(columns) < 4:
            continue
        remote, state = columns[2], columns[3]
        if state == "01" and remote.rsplit(":", 1)[-1].upper() == port:
            return True
    return False


async def _is_streaming() -> bool:
    """True while snapclient holds a connection to a snapserver."""
    for path in PROC_NET_TCP:
        try:
            table = await asyncio.to_thread(Path(path).read_text)
        except OSError:
            continue  # tcp6 is absent on a kernel built without IPv6
        if _holds_snapserver_connection(table):
            return True
    return False


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


async def _restart_snapclient() -> None:
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


async def _reconcile() -> bool:
    """Restart snapclient if its server address is stale. True if it was restarted."""
    global _start_owed
    if _start_owed:
        logger.warning("Starting the snapclient a failed restart left stopped")
        await _restart_snapclient()
        return True

    running = await _running_server_address()
    if running is None:
        return False

    # The whole gate: a connected client is streaming, and no address is worth
    # cutting a room for. The stale address it holds costs nothing until the
    # connection drops, which is exactly when this check fires instead.
    if await _is_streaming():
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
    await _restart_snapclient()
    return True


async def follow_server_address() -> None:
    """Restart a disconnected snapclient whose server has moved to a new address."""
    while True:
        interval = CHECK_INTERVAL
        try:
            if await _reconcile():
                interval = COOLDOWN_INTERVAL
        except Exception as e:
            logger.error(f"Server address check failed: {e}")
        await asyncio.sleep(interval)
