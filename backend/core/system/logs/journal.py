# backend/core/system/logs/journal.py
"""The journalctl calls the logs page makes, each bounded twice.

Bounded in entries — the read stops, and journalctl is killed, once it has
handed over what was asked — and in time, as the diagnostic's probes are
(`diagnostic/probes.py`): a journal walked on an SD card that is failing is
one of the things the page is opened to diagnose, so a read that does not end
costs its section ("not collected", with the reason), never the page.

**What is read, and at which level.** Milō's own units (`JOURNAL_UNIT_GLOB`)
at every level; the system services it depends on
(`whitelist.LOGS_PAGE_SYSTEM_UNITS`) and the kernel (`_TRANSPORT=kernel`, which
`journalctl -k` is not: `-k` implies `-b`, the current boot only) at warning
and above, always — NetworkManager and wpa_supplicant write the SSID of every
network they touch in their info lines, and every line read goes through the
Redactor. Never an unrestricted read.

**A level asked is asked of journalctl, before the cap** (`page_reads`): seven
days of a unit hold about 17 000 `milo-*` entries and some 1 200 at warning or
above, so a read filtered after the cap would keep the newest 5 000 lines of
every level and lose the older warnings. Two kinds of `milo-*` line say their
level, and each is read its own way: in the journal's PRIORITY (the backend,
whose stderr carries the syslog prefix — `core/log_handler.py` —, snapserver and
snapclient through syslog, systemd's own lines about a unit), read with `-p`;
in the text alone (logrus' `level=` of Navidrome and go-librespot, formats Milō
does not own, always at PRIORITY 6), read with `-g` over those units only, at
the priorities the first read leaves out — so the two never hold the same
entry. Those reads are made at every severity asked, warning when none is: a
`milo-*` read of every level beside them keeps its newest lines only, and the
warnings it cuts are in all the same. Each read has its own cap, and a read
that reached it says so.
"""
import asyncio
import contextlib
import logging
from asyncio import create_subprocess_exec
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.core.system.diagnostic.whitelist import JOURNAL_UNIT_GLOB, LOGS_PAGE_SYSTEM_UNITS
from backend.core.system.logs.entries import LEVEL_RANK, decode_json_lines, logrus_grep

logger = logging.getLogger(__name__)

# Long enough to walk two days of journal on an SD card (the window journald
# keeps on a unit), short enough that a wedged read costs a section.
JOURNAL_TIMEOUT_S = 8.0
# One JSON object per line; a message longer than this is a corrupt entry.
_MAX_LINE_BYTES = 1 << 20
# The fields read. journalctl adds __REALTIME_TIMESTAMP and the cursor itself.
_FIELDS = "MESSAGE,PRIORITY,_SYSTEMD_UNIT,UNIT,SYSLOG_IDENTIFIER,_PID"

MILO_UNITS = (JOURNAL_UNIT_GLOB,)
SYSTEM_UNITS = tuple(LOGS_PAGE_SYSTEM_UNITS)
# The kernel's lines, generators' and journald's own included (they write to
# /dev/kmsg before the journal is up): the one field match the page uses.
KERNEL_MATCH = "_TRANSPORT=kernel"
# The `milo-*` units whose level is in their text alone (logrus `level=`).
TEXT_LEVEL_UNITS = ("milo-navidrome.service", "milo-spotify.service")
# The floor the system services and the kernel are read at, everywhere.
SYSTEM_FLOOR = "warning"
# A level as journalctl's `-p` names it.
_PRIORITY_NAME = {"warning": "warning", "error": "err", "critical": "crit"}
# What a PRIORITY read at a level leaves out: the text read looks there only,
# so the two cover every priority once.
_BELOW = {"warning": "notice..debug", "error": "warning..debug", "critical": "err..debug"}


def at_arg(moment: datetime) -> str:
    """A moment as journalctl takes one, to the microsecond (`@<epoch>`)."""
    return f"@{moment.timestamp():.6f}"


def journal_args(
    *,
    units: Sequence[str] = MILO_UNITS,
    matches: Sequence[str] = (),
    priority: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    newest: Optional[int] = None,
    grep: Optional[str] = None,
) -> List[str]:
    """journalctl's argv: oldest first, `newest` keeping the last N only
    (newest first with `grep`, as journalctl prints a match read backward).
    `units` and `matches` are never both empty: no read is unrestricted."""
    if not units and not matches:
        raise ValueError("an unrestricted journal read")
    args = ["journalctl", "--no-pager", "-o", "json", f"--output-fields={_FIELDS}"]
    for unit in units:
        args += ["-u", unit]
    args += list(matches)
    if priority is not None:
        args += ["-p", priority]
    if since is not None:
        args += ["--since", at_arg(since)]
    if until is not None:
        args += ["--until", at_arg(until)]
    if newest is not None:
        args += ["-n", str(newest)]
    if grep is not None:
        args += ["-g", grep]
    return args


@dataclass(frozen=True)
class JournalRead:
    """One journalctl call of an answer, and what its lines are."""
    args: List[str]
    # What the page names it by when it could not be read (`not_collected`).
    name: str = "journal"
    # The kernel's lines, filed under `kernel` (entries.parse_kernel).
    kernel: bool = False
    # Every level of `milo-*`: what its cap cuts is below warning only, the
    # warnings being read whole beside it (`page_reads`).
    all_levels: bool = False


def _floor_reads(floor: str, window: Dict[str, Any]) -> List[JournalRead]:
    """Everything at `floor` and above, filtered by journalctl — by PRIORITY,
    then by text where the level is only there — so the cap holds days."""
    priority = _PRIORITY_NAME[floor]
    return [
        JournalRead(
            journal_args(units=(*MILO_UNITS, *SYSTEM_UNITS), priority=priority, **window),
            name="journal/warnings",
        ),
        JournalRead(
            journal_args(units=TEXT_LEVEL_UNITS, priority=_BELOW[floor], grep=logrus_grep(floor), **window),
            name="journal/text-level",
        ),
        JournalRead(
            journal_args(units=(), matches=(KERNEL_MATCH,), priority=priority, **window),
            name="journal/kernel", kernel=True,
        ),
    ]


def page_reads(
    *, since: datetime, until: datetime, level: Optional[str], cap: int,
) -> List[JournalRead]:
    """The reads of one page of the window, each capped at `cap`, newest kept.

    With warning or above: everything at that level (`_floor_reads`). With no
    level, or one below warning: the same reads at warning, and beside them
    every line of `milo-*` (`all_levels`: the cap bites on a noisy window, and
    says so, but only on the lines below warning). The entries the reads share
    are one entry each (`one_per_entry`).
    """
    window = {"since": since, "until": until, "newest": cap}
    if level is not None and LEVEL_RANK[level] >= LEVEL_RANK[SYSTEM_FLOOR]:
        return _floor_reads(level, window)
    return [
        JournalRead(journal_args(units=MILO_UNITS, **window), all_levels=True),
        *_floor_reads(SYSTEM_FLOOR, window),
    ]


def context_reads(*, at: datetime, n: int) -> List[Tuple[JournalRead, bool]]:
    """The reads around a moment, each `(read, before)`: `n` lines on each
    side, `milo-*` at every level, the system services and the kernel at
    warning and above, as on the page."""
    floor = _PRIORITY_NAME[SYSTEM_FLOOR]
    reads = []
    for before in (True, False):
        side = {"until": at, "newest": n} if before else {"since": at}
        reads += [
            (JournalRead(journal_args(units=MILO_UNITS, **side)), before),
            (JournalRead(
                journal_args(units=SYSTEM_UNITS, priority=floor, **side), name="journal/system",
            ), before),
            (JournalRead(
                journal_args(units=(), matches=(KERNEL_MATCH,), priority=floor, **side),
                name="journal/kernel", kernel=True,
            ), before),
        ]
    return reads


def one_per_entry(entries: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Entries of several reads, each once, oldest first: an entry is its
    cursor, which journalctl prints on every one."""
    unique: Dict[Any, Dict[str, Any]] = {}
    for entry in entries:
        key = entry.get("__CURSOR") or (
            entry.get("__REALTIME_TIMESTAMP"), entry.get("_SYSTEMD_UNIT"),
            entry.get("_PID"), str(entry.get("MESSAGE")),
        )
        unique.setdefault(key, entry)
    return sorted(unique.values(), key=lambda e: int(e.get("__REALTIME_TIMESTAMP", 0)))


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
