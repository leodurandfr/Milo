# backend/core/system/logs/journal.py
"""The one journalctl call the logs page makes, bounded twice.

Bounded in entries — the read stops, and journalctl is killed, once it has
handed over what was asked — and in time, as the diagnostic's probes are
(`diagnostic/probes.py`): a journal walked on an SD card that is failing is
one of the things the page is opened to diagnose, so a read that does not end
costs its section ("not collected", with the reason), never the page.

Restricted to Milō's own units (`JOURNAL_UNIT_GLOB`), like every journal read
of the diagnostic: NetworkManager and wpa_supplicant log the SSID of every
network they touch, and an unrestricted read would answer for them too.
"""
import asyncio
import contextlib
import logging
from asyncio import create_subprocess_exec
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.core.system.diagnostic.whitelist import JOURNAL_UNIT_GLOB
from backend.core.system.logs.entries import decode_json_lines

logger = logging.getLogger(__name__)

# Long enough to walk two days of journal on an SD card (the window journald
# keeps on a unit), short enough that a wedged read costs a section.
JOURNAL_TIMEOUT_S = 8.0
# One JSON object per line; a message longer than this is a corrupt entry.
_MAX_LINE_BYTES = 1 << 20
# The fields read. journalctl adds __REALTIME_TIMESTAMP and the cursor itself.
_FIELDS = "MESSAGE,PRIORITY,_SYSTEMD_UNIT,UNIT,SYSLOG_IDENTIFIER,_PID"


def at_arg(moment: datetime) -> str:
    """A moment as journalctl takes one, to the microsecond (`@<epoch>`)."""
    return f"@{moment.timestamp():.6f}"


def journal_args(
    *,
    units: Sequence[str] = (JOURNAL_UNIT_GLOB,),
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    newest: Optional[int] = None,
    grep: Optional[str] = None,
) -> List[str]:
    """journalctl's argv: oldest first, `newest` keeping the last N only."""
    args = ["journalctl", "--no-pager", "-o", "json", f"--output-fields={_FIELDS}"]
    for unit in units:
        args += ["-u", unit]
    if since is not None:
        args += ["--since", at_arg(since)]
    if until is not None:
        args += ["--until", at_arg(until)]
    if newest is not None:
        args += ["-n", str(newest)]
    if grep is not None:
        args += ["-g", grep]
    return args


async def read(
    args: Sequence[str], *, max_entries: int, timeout: float = JOURNAL_TIMEOUT_S
) -> Tuple[List[Dict[str, Any]], Optional[str], bool]:
    """(entries, why the read failed or None, whether it stopped at `max_entries`).

    Entries are kept in the order journalctl printed them. A read cut by the
    timeout keeps what it had, and says so.
    """
    try:
        proc = await create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=_MAX_LINE_BYTES,
        )
    except (OSError, ValueError) as e:
        return [], f"journalctl could not start ({e})", False

    raw: List[bytes] = []
    stopped = False

    async def pump() -> None:
        nonlocal stopped
        while True:
            line = await proc.stdout.readline()
            if not line:
                return
            raw.append(line)
            if len(raw) >= max_entries:
                stopped = True
                return

    reason = None
    # Read to its end: the one way out that leaves journalctl to exit on its
    # own. Any other — the cap, the timeout, an unreadable entry, the read
    # canceled (a shutdown) — kills it, or the wait below would wait on a
    # journalctl blocked writing to a pipe nobody reads any more.
    read_to_end = False
    try:
        await asyncio.wait_for(pump(), timeout)
        read_to_end = not stopped
    except asyncio.TimeoutError:
        reason = f"journalctl did not answer within {timeout:g} s"
    except (ValueError, asyncio.LimitOverrunError) as e:
        reason = f"journalctl printed an unreadable entry ({e})"
    finally:
        if proc.returncode is None and not read_to_end:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
        with contextlib.suppress(ProcessLookupError):
            await proc.wait()

    # A grep that matched nothing exits 1 with nothing printed: an empty
    # answer, not a failure.
    if (
        reason is None and not stopped and proc.returncode
        and not (proc.returncode == 1 and not raw)
    ):
        reason = f"journalctl exited with status {proc.returncode}"
    return decode_json_lines(raw), reason, stopped
