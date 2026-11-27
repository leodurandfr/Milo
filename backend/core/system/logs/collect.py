# backend/core/system/logs/collect.py
"""The appliance's lines over a window, read one way for every reader.

Two readers ask for them: the logs page (`LogService`, which redacts, filters
and groups what this returns) and the triage the maintainer runs on the unit
itself (`.claude/skills/milo-triage/triage.py`, which reads them unredacted,
where they are). Both read the same places the same way, here: errors.log and
its rotations, the server's journal (`journal.page_reads`: the `milo-*` units,
and at warning and above the system services Milō depends on and the kernel),
and each speaker's own journal over the same window (`satellites.read_all`).
Two collections of one appliance's logs drift apart; one cannot.

Nothing here redacts: a line leaves the unit only through `LogService`, which
redacts every line before anything else touches it.

Two lines in two places are often one record: the backend writes WARNING and
above both to errors.log and to its stderr, which journald keeps. The journal's
copy is kept (it carries the unit and the microsecond); errors.log's copy is
dropped when the journal holds the same logger and first line within
`SAME_RECORD_S`. errors.log's own lines matter where the journal no longer
reaches — the rotations reach further than journald's 54 MB, and they hold
the backend's warnings from before its stderr carried their priority.
"""
import asyncio
import bisect
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from backend.core.system.logs import journal, satellites
from backend.core.system.logs.entries import (
    ERRORS_LOG_SERVICE,
    LogLine,
    journal_time,
    parse_errors_log,
    parse_journal,
    parse_kernel,
)
from backend.core.system.logs.models import NotCollected
from backend.core.system.logs.satellites import SatelliteTarget

logger = logging.getLogger(__name__)

# The two copies of one record are written within the same millisecond; the
# slack covers a journald that was busy.
SAME_RECORD_S = 2.0


@dataclass
class Collected:
    """Every line of the window, oldest first, and what of it is missing."""
    lines: List[LogLine] = field(default_factory=list)
    not_collected: List[NotCollected] = field(default_factory=list)
    # A server journal read stopped at its cap: its older lines are not in.
    truncated: bool = False
    # From when the window is whole, where a read was cut.
    journal_from: Optional[datetime] = None
    # Only the read of every level was cut: the warnings are whole.
    warnings_whole: bool = True


async def collect(
    *,
    since: datetime,
    until: datetime,
    level: Optional[str],
    cap: int,
    error_log: Path,
    speakers: Sequence[SatelliteTarget],
    cpu: Callable[[Callable[[], Any]], Awaitable[Any]] = asyncio.to_thread,
) -> Collected:
    """The lines from `since` to `until`: the server's at `level` and above
    (every level of `milo-*` when None, as `journal.page_reads` reads them),
    each speaker's at warning and above. `cpu` runs the parsing off the event
    loop — the page's runs one answer at a time."""
    reads = journal.page_reads(since=since, until=until, level=level, cap=cap)
    results, (others, not_collected) = await asyncio.gather(
        asyncio.gather(*(journal.read(r.args, max_entries=cap) for r in reads)),
        read_others(error_log=error_log, speakers=speakers, since=since, until=until,
                    errors_log_since=since),
    )
    not_collected += journal_failures(reads, results)
    cut_at = [
        min(journal_time(e) for e in entries if "__REALTIME_TIMESTAMP" in e)
        for (entries, _, stopped) in results
        if stopped and any("__REALTIME_TIMESTAMP" in e for e in entries)
    ]
    cut = [read for read, (_, _, stopped) in zip(reads, results) if stopped]

    def merge() -> List[LogLine]:
        journaled = parse_reads(reads, [entries for (entries, _, _) in results])
        return sorted(
            (line for line in (*journaled, *drop_journaled(others, journaled))
             if since <= line.at <= until),
            key=lambda line: line.at,
        )

    return Collected(
        lines=await cpu(merge),
        not_collected=not_collected,
        truncated=bool(cut),
        journal_from=max(cut_at) if cut_at else None,
        warnings_whole=all(read.all_levels for read in cut),
    )


async def read_others(
    *,
    error_log: Path,
    speakers: Sequence[SatelliteTarget],
    since: datetime,
    until: datetime,
    errors_log_since: Optional[datetime] = None,
) -> Tuple[List[LogLine], List[NotCollected]]:
    """errors.log's lines (from `errors_log_since`, every rotation when None)
    and every speaker's from `since` to `until`, and what could not be read."""
    (errors_log, errors_reason), journals = await asyncio.gather(
        read_errors_log(error_log, since=errors_log_since),
        satellites.read_all(speakers, since=since, until=until),
    )
    not_collected = [item for speaker in journals for item in speaker.not_collected]
    if errors_reason:
        not_collected.append(NotCollected(source="errors.log", reason=errors_reason))
    lines = list(errors_log)
    for speaker in journals:
        lines += speaker.lines
    return lines, not_collected


async def read_errors_log(
    base: Path, *, since: Optional[datetime] = None,
) -> Tuple[List[LogLine], Optional[str]]:
    """errors.log and its rotations (`errors.log.N`, the oldest the highest
    N), oldest line first; the reason when the file itself could not be read.
    A rotation that is absent is no fault: there is one only once the file
    filled. With `since`, a rotation last written before it holds nothing of
    the window and is not read at all."""
    oldest = since.timestamp() if since is not None else None

    def read_all() -> Tuple[List[LogLine], Optional[str]]:
        rotations = sorted(
            (p for p in base.parent.glob(f"{base.name}.*") if p.suffix[1:].isdigit()),
            key=lambda p: int(p.suffix[1:]),
            reverse=True,
        )
        lines: List[LogLine] = []
        for path in rotations:
            try:
                if oldest is not None and path.stat().st_mtime < oldest:
                    continue
                lines += parse_errors_log(path.read_text(encoding="utf-8", errors="replace"))
            except OSError as e:
                logger.warning("logs: %s could not be read: %s", path.name, e)
        try:
            text = base.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return lines, f"{base} does not exist"
        except OSError as e:
            return lines, f"{base} could not be read ({e.strerror or e})"
        return lines + parse_errors_log(text), None

    try:
        return await asyncio.to_thread(read_all)
    except OSError as e:
        return [], f"{base.parent} could not be listed ({e.strerror or e})"


def first_line(text: str) -> str:
    return text.split("\n", 1)[0]


def drop_journaled(errors_log: Sequence[LogLine], journaled: Sequence[LogLine]) -> List[LogLine]:
    """errors.log's lines the journal does not also hold."""
    seen: Dict[Tuple[str, str], List[float]] = {}
    for line in journaled:
        if line.service == ERRORS_LOG_SERVICE and line.logger:
            seen.setdefault((line.logger, first_line(line.message)), []).append(line.at.timestamp())
    for times in seen.values():
        times.sort()
    kept = []
    for line in errors_log:
        times = seen.get((line.logger or "", first_line(line.message)))
        if times:
            t = line.at.timestamp()
            i = bisect.bisect_left(times, t - SAME_RECORD_S)
            if i < len(times) and times[i] <= t + SAME_RECORD_S:
                continue
        kept.append(line)
    return kept


def parse_reads(reads: Sequence[journal.JournalRead], entries: Sequence[Sequence[Dict[str, Any]]]) -> List[LogLine]:
    """The lines of several reads, each entry once (the reads are disjoint by
    construction; the cursor makes sure of it), oldest first."""
    milo = journal.one_per_entry([e for r, got in zip(reads, entries) if not r.kernel for e in got])
    kernel = journal.one_per_entry([e for r, got in zip(reads, entries) if r.kernel for e in got])
    return sorted([*parse_journal(milo), *parse_kernel(kernel)], key=lambda line: line.at)


def journal_failures(reads: Sequence[journal.JournalRead], results) -> List[NotCollected]:
    """One entry per read that failed, under that read's name, each
    (name, reason) said once: a failed kernel read is not "the journal".
    Every read failing the same way (no journalctl) is the journal's."""
    reasons = [reason for (_, reason, _) in results]
    if reasons and None not in reasons and len(set(reasons)) == 1:
        return [NotCollected(source="journal", reason=reasons[0])]
    failures = dict.fromkeys(
        (read.name, reason) for read, reason in zip(reads, reasons) if reason is not None
    )
    return [NotCollected(source=name, reason=reason) for name, reason in failures]
