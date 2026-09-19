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

This closes that asymmetry from the same side: compare the address snapclient is
*running* with against the address the server answers on *now*, and restart the
unit when they diverge. The lookup happens here, in the API process, and never
in snapclient's reconnect path — which is what the launcher's design protects.
"""
import asyncio
import logging
from pathlib import Path
from typing import Optional

from services.registration import resolve_milo_principal

logger = logging.getLogger(__name__)

SNAPCLIENT_UNIT = "milo-client-snapclient.service"
PROC_CMDLINE = "/proc/{pid}/cmdline"

CHECK_INTERVAL = 30  # seconds
# After a restart, wait longer before comparing again: if snapclient failed to
# come back on the new address, a check-interval loop would stop/start it every
# 30s forever.
COOLDOWN_INTERVAL = 60


def _server_arg(cmdline: bytes) -> Optional[str]:
    """The value of snapclient's `-h` flag, read out of a /proc cmdline blob."""
    args = [arg.decode(errors="replace") for arg in cmdline.split(b"\0") if arg]
    for i, arg in enumerate(args):
        if arg == "-h" and i + 1 < len(args):
            return args[i + 1]
    return None


async def _main_pid() -> int:
    """PID of the snapclient unit, or 0 when it is not running.

    `systemctl show` needs no privilege. 0 is the answer for a satellite with no
    audio card configured, where the launcher exits 0 and the unit stays dead.
    """
    proc = await asyncio.create_subprocess_exec(
        "systemctl", "show", "--value", "-p", "MainPID", SNAPCLIENT_UNIT,
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
    for action in ("stop", "start"):
        proc = await asyncio.create_subprocess_exec(
            "sudo", "systemctl", action, SNAPCLIENT_UNIT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"Failed to {action} snapclient: {stderr.decode().strip()}")


async def _reconcile() -> bool:
    """Restart snapclient if its server address is stale. True if it was restarted."""
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
    await _restart_snapclient()
    return True


async def follow_server_address() -> None:
    """Restart snapclient whenever the main Milo answers on a new address."""
    while True:
        interval = CHECK_INTERVAL
        try:
            if await _reconcile():
                interval = COOLDOWN_INTERVAL
        except Exception as e:
            logger.error(f"Server address check failed: {e}")
        await asyncio.sleep(interval)
