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
is playing is never interrupted.** A restart is free in two states only, and the
address is compared in those two:

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
- **Connected, with its ALSA output closed.** snapclient closes it 5 s after
  its last chunk, which the server stops sending 5 s into a silence. This is
  how a satellite comes back to the wired address once the cable has returned:
  it re-dials the literal it holds, and when the server only restarts
  snapserver it is back in ~1 s (measured twice on 2026-11-05) — too fast to
  ever be seen disconnected — so it stayed on the WiFi address, through every
  backend restart, until someone restarted it by hand.

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
# snapclient's output, `camilladsp_input` in asound.conf: the Loopback card's
# playback device 0, subdevice 0, addressed by the card's id like the PCM is.
LOOPBACK_PLAYBACK_STATUS = "/proc/asound/Loopback/pcm0p/sub0/status"

POLL_INTERVAL = 1  # seconds between two reads of the socket table
CHECK_INTERVAL = 30  # seconds
# After a lost connection, compare this often for this long: mDNS answers the
# new address a few seconds after the loss, and snapclient reconnects in < 1 s.
LOSS_CHECK_INTERVAL = 2
LOSS_WINDOW = 60
# After a restart, wait longer before comparing again: the unit needs time to
# dial, and a check-interval loop would otherwise stop/start it on every pass
# while it is still trying.
COOLDOWN_INTERVAL = 60

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


async def _is_connected() -> bool:
    """True while snapclient holds a connection to a snapserver."""
    for path in PROC_NET_TCP:
        try:
            table = await asyncio.to_thread(Path(path).read_text)
        except OSError:
            continue  # tcp6 is absent on a kernel built without IPv6
        if _holds_snapserver_connection(table):
            return True
    return False


async def _is_playing() -> bool:
    """False only when snapclient's ALSA output reads closed.

    An unreadable status counts as playing: closed has to be seen, because a
    guess here is a room cut mid-song.
    """
    try:
        status = await asyncio.to_thread(Path(LOOPBACK_PLAYBACK_STATUS).read_text)
    except OSError:
        return True
    return status.split()[:1] != ["closed"]


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
    """Restart snapclient if its server address is stale. True if it was restarted."""
    if _start_owed:
        async with _restart_lock:
            # Read again under the lock: a restart in flight clears its own debt.
            if not _start_owed:
                return False
            logger.warning("Starting the snapclient a failed restart left stopped")
            await _stop_then_start()
            return True

    # The whole gate, read before anything that spawns or queries: no address
    # is worth cutting a room for.
    if await _is_connected() and await _is_playing():
        return False

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


class _Pacer:
    """Which reads of the socket table also run the address check.

    Counted in polls, not read off a clock: the loop's own passes are the time.
    """

    def __init__(self) -> None:
        self._connected = False
        self._loss_polls = 0  # polls left in the fast window after a loss
        self._skip = 0  # polls to let pass before the next check
        self._cooling = False

    def due(self, connected: bool) -> bool:
        if self._connected and not connected:
            self._loss_polls = LOSS_WINDOW // POLL_INTERVAL
            if not self._cooling:
                self._skip = 0
        self._connected = connected
        if self._loss_polls:
            self._loss_polls -= 1
        if self._skip:
            self._skip -= 1
            return False
        return True

    def checked(self, restarted: bool) -> None:
        self._cooling = restarted
        if restarted:
            interval = COOLDOWN_INTERVAL
        elif self._loss_polls:
            interval = LOSS_CHECK_INTERVAL
        else:
            interval = CHECK_INTERVAL
        self._skip = interval // POLL_INTERVAL - 1


async def follow_server_address() -> None:
    """Restart a snapclient playing nothing whose server has moved to a new address."""
    pacer = _Pacer()
    while True:
        try:
            if pacer.due(await _is_connected()):
                restarted = False
                try:
                    restarted = await _reconcile()
                finally:
                    pacer.checked(restarted)
        except Exception as e:
            logger.error(f"Server address check failed: {e}")
        await asyncio.sleep(POLL_INTERVAL)
