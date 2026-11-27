# backend/core/system/logs/service.py
"""The appliance's logs, read when the logs page asks and never otherwise.

Nothing here runs in the background and nothing is kept between two reads:
each answer reads errors.log and its rotations, the journal of the server's
`milo-*` units and each online speaker's own journal tails, then groups what
it read (`grouping.py`). A place that could not be read is a `not_collected`
entry with its reason — never a silence, which would read as "nothing
happened there".

**Every line that leaves passes through the diagnostic's Redactor** (built by
`DiagnosticService.build_redactor`, from the same reads a report makes: the
WiFi networks NetworkManager holds, the share names, the speakers' and zones'
names, the Bluetooth devices, what is playing), then through
`strip_public_ipv4`. The API is not authenticated and the page is meant to
stay publishable, so a line is redacted before it is filtered, grouped or
cut: a text search for a network name finds nothing, rather than telling who
asks that the network exists.

Two lines in two places are often one record: the backend writes WARNING and
above both to errors.log and to its stderr, which journald keeps. The journal's
copy is kept (it carries the unit and the microsecond); errors.log's copy is
dropped when the journal holds the same logger and first line within
`_SAME_RECORD_S`. errors.log's own lines matter where the journal no longer
reaches — journald keeps about two days on a unit, the rotations far more.

**What costs CPU runs off the event loop** — parsing up to 8 MB of errors.log,
5000 journal entries, the Redactor's passes over every line, the grouping —
in a thread (`_in_thread`), one answer at a time: measured, 50 000 lines
through the Redactor took 2.4 s on the dev VM, several times that on a Pi,
and on the loop that is the WebSocket, mpv's events and the rotary's volume
frozen for as long. The Redactor is built per answer, so one thread uses it.
"""
import asyncio
import bisect
import logging
import re
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import aiohttp

from backend.config.constants import ERROR_LOG_FILE
from backend.core.system.diagnostic.whitelist import strip_public_ipv4
from backend.core.system.logs import grouping, journal
from backend.core.system.logs.entries import (
    ERRORS_LOG_SERVICE,
    LEVEL_RANK,
    LogLine,
    journal_time,
    message_text,
    parse_errors_log,
    parse_journal,
    parse_satellite_block,
)
from backend.core.system.logs.models import (
    LogContext,
    LogGroupOut,
    LogLineOut,
    LogsPage,
    NotCollected,
    RunningBuild,
    SourceStateAt,
)

logger = logging.getLogger(__name__)

# The two copies of one record are written within the same millisecond; the
# slack covers a journald that was busy.
_SAME_RECORD_S = 2.0

# `Transition completed: <source>[ (unavailable: <why>)]` and `Transition
# failed: <source>: <why>`, core/state.py — a failed transition leaves its
# target selected, its service failed —, and systemd's own line for a start of
# the backend.
_TRANSITION = re.compile(
    r"^INFO:backend\.core\.state:Transition completed: (?P<source>\w+)"
    r"(?: \(unavailable: (?P<why>[^)]*)\))?"
)
_TRANSITION_FAILED = re.compile(r"^WARNING:backend\.core\.state:Transition failed: (?P<source>\w+): ")
_BACKEND_STARTED = re.compile(r"^Started milo-backend\.service")
_SOURCE_STATE_GREP = (
    r"^INFO:backend\.core\.state:Transition completed: "
    r"|^WARNING:backend\.core\.state:Transition failed: "
    r"|^Started milo-backend\.service"
)


def _first_line(text: str) -> str:
    return text.split("\n", 1)[0]


def _one_per_record(lines: Sequence[LogLine]) -> List[LogLine]:
    """One line per journal record, oldest first, when two reads both hold it.

    A record is its moment, its service, its process and its first line; the
    longer copy is kept. Two reads cut at the same moment glue a record's
    traceback differently: the one that ends at it (`--until`) holds its first
    line alone, the next one stamped microseconds later and out of the read,
    while the one that starts at it holds the record whole.
    """
    unique: Dict[Tuple[datetime, str, Optional[str], str], LogLine] = {}
    for line in lines:
        key = (line.at, line.service, line.pid, _first_line(line.message))
        kept = unique.get(key)
        if kept is None or len(line.message) > len(kept.message):
            unique[key] = line
    return sorted(unique.values(), key=lambda line: line.at)


def _drop_journaled(errors_log: Sequence[LogLine], journaled: Sequence[LogLine]) -> List[LogLine]:
    """errors.log's lines the journal does not also hold."""
    seen: Dict[Tuple[str, str], List[float]] = {}
    for line in journaled:
        if line.service == ERRORS_LOG_SERVICE and line.logger:
            seen.setdefault((line.logger, _first_line(line.message)), []).append(line.at.timestamp())
    for times in seen.values():
        times.sort()
    kept = []
    for line in errors_log:
        times = seen.get((line.logger or "", _first_line(line.message)))
        if times:
            t = line.at.timestamp()
            i = bisect.bisect_left(times, t - _SAME_RECORD_S)
            if i < len(times) and times[i] <= t + _SAME_RECORD_S:
                continue
        kept.append(line)
    return kept


class LogService:
    """Reads, groups and redacts. Holds no state and spawns no task."""

    # The journal read's cap, newest kept: two days of a quiet unit, a few
    # hours of a noisy one. A window it cuts is said so (`truncated`).
    JOURNAL_MAX_ENTRIES = 5000
    # Lines of context on each side of an event, every service together.
    CONTEXT_LINES = 20
    # After redaction, never before: a value cut in half escapes its rule.
    MAX_MESSAGE_CHARS = 4000

    def __init__(
        self,
        diagnostic_service,
        version_service=None,
        state_machine=None,
        error_log: Path = ERROR_LOG_FILE,
    ):
        self._diagnostic = diagnostic_service
        self._version = version_service
        self._state_machine = state_machine
        self._error_log = error_log
        # One answer's CPU work at a time (_in_thread).
        self._cpu = asyncio.Lock()

    # ----------------------------------------------------------------- public

    async def read(
        self,
        *,
        since: timedelta,
        level: Optional[str] = None,
        service: Optional[str] = None,
        q: Optional[str] = None,
        limit: int = 200,
    ) -> LogsPage:
        """The groups of the window `since` back from now that pass the
        filters: `level` a minimum severity, `service` one service exactly,
        `q` a case-insensitive text matched against the redacted line."""
        until = datetime.now(timezone.utc)
        start = until - since
        sanitize, (entries, others, not_collected, truncated) = await asyncio.gather(
            self._sanitizer(),
            self._collect(
                journal.journal_args(since=start, until=until, newest=self.JOURNAL_MAX_ENTRIES),
                self.JOURNAL_MAX_ENTRIES,
                errors_log_since=start,
            ),
        )

        def answer() -> LogsPage:
            journaled = parse_journal(entries)
            journal_from = min((line.at for line in journaled), default=None) if truncated else None
            lines = self._redacted(
                [
                    line for line in (*journaled, *_drop_journaled(others, journaled))
                    if start <= line.at <= until
                ],
                sanitize,
            )
            minimum = LEVEL_RANK[level] if level else 0
            needle = q.casefold() if q else None
            matched = [
                line for line in lines
                if LEVEL_RANK.get(line.level, 0) >= minimum
                and (service is None or line.service == service)
                and (needle is None or needle in (
                    f"{line.service}\n{line.logger or ''}\n{line.message}".casefold()
                ))
            ]
            groups = grouping.group(matched)
            return LogsPage(
                since=start,
                until=until,
                groups=[LogGroupOut(**asdict(g)) for g in groups[:limit]],
                total_groups=len(groups),
                services=sorted({line.service for line in lines}),
                lines_read=len(lines),
                truncated=truncated,
                journal_from=journal_from,
                not_collected=self._redacted_reasons(not_collected, sanitize),
            )

        return await self._in_thread(answer)

    async def context(self, *, at: datetime, service: Optional[str] = None) -> LogContext:
        """The `CONTEXT_LINES` lines on each side of the event at `at` (of
        `service`, when given), every service together; the event's own text,
        when a line is exactly at `at`, redacted with them; the release the
        unit runs; and the audio source at that moment (`_source_state_at`)."""
        at = at.astimezone(timezone.utc)
        n = self.CONTEXT_LINES + 1
        (
            sanitize,
            (before, before_reason, before_full),
            (after, after_reason, after_full),
            (others, not_collected),
            build,
            source_state,
        ) = await asyncio.gather(
            self._sanitizer(),
            journal.read(journal.journal_args(until=at, newest=n), max_entries=n),
            journal.read(journal.journal_args(since=at), max_entries=n),
            self._collect_others(),
            self._running_build(),
            self._source_state_at(at),
        )
        for reason in {before_reason, after_reason} - {None}:
            not_collected.append(NotCollected(source="journal", reason=reason))

        def answer() -> LogContext:
            journaled = _one_per_record(parse_journal(before) + parse_journal(after))
            # Where the journal read was cut, a line from elsewhere past the
            # cut would sit next to journal lines it never had between them.
            low = journaled[0].at if before_full and journaled else None
            high = journaled[-1].at if after_full and journaled else None
            merged = sorted(
                [
                    *journaled,
                    *(
                        line for line in _drop_journaled(others, journaled)
                        if (low is None or line.at >= low) and (high is None or line.at <= high)
                    ),
                ],
                key=lambda line: line.at,
            )

            index, exact = self._locate(merged, at, service)
            if index is None:
                window, event_index = merged[-self.CONTEXT_LINES:], None
            else:
                first = max(index - self.CONTEXT_LINES, 0)
                window = merged[first:index + self.CONTEXT_LINES + 1]
                event_index = index - first
            lines = self._redacted(window, sanitize)
            return LogContext(
                at=at,
                event_index=event_index,
                # One Redactor for the event and its neighbors: a value it
                # labels by its shape is the same label in both.
                sample=lines[event_index].message if exact else None,
                lines=[self._out(line) for line in lines],
                build=build,
                source_state=source_state,
                not_collected=self._redacted_reasons(not_collected, sanitize),
            )

        return await self._in_thread(answer)

    # ---------------------------------------------------------------- private

    async def _in_thread(self, work: Callable[[], Any]) -> Any:
        """One answer's parsing, redaction and grouping, off the event loop —
        one at a time, so reads asked in a burst (a query being typed) queue
        rather than stack threads that each take the GIL from the loop."""
        async with self._cpu:
            return await asyncio.to_thread(work)

    async def _sanitizer(self) -> Callable[[str], str]:
        """The Redactor of this answer, then the public IPv4 rule.

        Never a fallback: a Redactor that could not be built fails the read,
        because a line sent without it would carry what it exists to remove.
        """
        redactor = await self._diagnostic.build_redactor()
        return lambda text: strip_public_ipv4(redactor(text))

    def _redacted(self, lines: Sequence[LogLine], sanitize: Callable[[str], str]) -> List[LogLine]:
        """Each line redacted, oldest first: the Redactor labels a value
        found by its shape in the order it meets it."""
        out = []
        for line in sorted(lines, key=lambda line: line.at):
            message = sanitize(line.message)
            if len(message) > self.MAX_MESSAGE_CHARS:
                message = message[:self.MAX_MESSAGE_CHARS - 1] + "…"
            out.append(replace(line, message=message, pid=None))
        return out

    @staticmethod
    def _redacted_reasons(items: Sequence[NotCollected], sanitize) -> List[NotCollected]:
        return [NotCollected(source=item.source, reason=sanitize(item.reason)) for item in items]

    @staticmethod
    def _out(line: LogLine) -> LogLineOut:
        return LogLineOut(
            at=line.at, level=line.level, service=line.service,
            logger=line.logger, message=line.message,
        )

    @staticmethod
    def _locate(
        lines: Sequence[LogLine], at: datetime, service: Optional[str],
    ) -> Tuple[Optional[int], bool]:
        """The event's index, and whether it is the event's own line: the line
        at `at` (of `service`) when there is one, else the line nearest to it
        in time; None when there is no line."""
        if not lines:
            return None, False
        for i, line in enumerate(lines):
            if line.at == at and (service is None or line.service == service):
                return i, True
        candidates = [
            i for i, line in enumerate(lines) if service is None or line.service == service
        ] or list(range(len(lines)))
        return min(candidates, key=lambda i: abs((lines[i].at - at).total_seconds())), False

    async def _collect(
        self, journal_argv: List[str], max_entries: int, *, errors_log_since: datetime,
    ) -> Tuple[List[Dict[str, Any]], List[LogLine], List[NotCollected], bool]:
        """The journal's entries as read, the other places' lines, what could
        not be read, and whether the journal read was cut at its cap."""
        (entries, reason, truncated), (others, not_collected) = await asyncio.gather(
            journal.read(journal_argv, max_entries=max_entries),
            self._collect_others(errors_log_since=errors_log_since),
        )
        if reason is not None:
            not_collected.append(NotCollected(source="journal", reason=reason))
        return entries, others, not_collected, truncated

    async def _collect_others(
        self, *, errors_log_since: Optional[datetime] = None,
    ) -> Tuple[List[LogLine], List[NotCollected]]:
        """errors.log's lines and every speaker's, and what could not be read."""
        (errors_log, errors_reason), satellites = await asyncio.gather(
            self._read_errors_log(since=errors_log_since), self._satellite_blocks(),
        )
        not_collected = []
        if isinstance(satellites, str):
            not_collected.append(NotCollected(source="speakers", reason=satellites))
            satellites = []
        if errors_reason:
            not_collected.append(NotCollected(source="errors.log", reason=errors_reason))
        lines = list(errors_log)
        for result in satellites:
            label = result.get("label", "?")
            if not result.get("reachable"):
                not_collected.append(NotCollected(
                    source=f"speaker {label}", reason=result.get("reason") or "no answer",
                ))
                continue
            lines += parse_satellite_block(label, result.get("text") or "")
        return lines, not_collected

    async def _satellite_blocks(self):
        """Each speaker's block, or why none could be asked."""
        try:
            return await self._diagnostic.satellite_blocks()
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            # Best effort, like the report's SATELLITES section: the page
            # answers without them, and says so. Each speaker's own failure
            # is its result already; this is the session that could not open.
            logger.warning("logs could not ask the speakers: %s", e)
            return f"{type(e).__name__}: {e}"

    async def _read_errors_log(
        self, *, since: Optional[datetime] = None,
    ) -> Tuple[List[LogLine], Optional[str]]:
        """errors.log and its rotations (`errors.log.N`, the oldest the
        highest N), oldest line first; the reason when the file itself could
        not be read. A rotation that is absent is no fault: there is one only
        once the file filled. With `since`, a rotation last written before it
        holds nothing of the window and is not read at all."""
        base = self._error_log
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

    async def _running_build(self) -> RunningBuild:
        if self._version is None:
            return RunningBuild()
        return RunningBuild(**await self._version.running_build())

    async def _source_state_at(self, at: datetime) -> SourceStateAt:
        """The audio source at `at`, and how that is known (SourceStateAt).

        The best honest answer, in this order: the last transition the
        backend logged before `at` — completed, or failed, which left its
        target selected with its service failed; nothing selected, when the
        backend started after that transition and before `at` (a backend
        starts with no source); and only when the journal holds neither, the
        state as it is *now*, labeled `current` — never passed off as the past
        one.
        """
        entries, reason, _ = await journal.read(
            journal.journal_args(
                units=("milo-backend.service",), until=at, newest=1, grep=_SOURCE_STATE_GREP,
            ),
            max_entries=1,
        )
        for entry in entries:
            if "__REALTIME_TIMESTAMP" not in entry:
                continue
            raw = _first_line(message_text(entry.get("MESSAGE")))
            moment = journal_time(entry)
            transition = _TRANSITION.match(raw)
            if transition:
                why = transition.group("why")
                return SourceStateAt(
                    basis="logged_transition",
                    source=transition.group("source"),
                    at=moment,
                    detail=(
                        "the last transition logged before this moment"
                        + (f", the source unavailable: {why}" if why else "")
                    ),
                )
            failed = _TRANSITION_FAILED.match(raw)
            if failed:
                return SourceStateAt(
                    basis="failed_transition",
                    source=failed.group("source"),
                    at=moment,
                    detail=(
                        "the last transition logged before this moment failed: "
                        "its source stayed selected, its service failed"
                    ),
                )
            if _BACKEND_STARTED.match(raw):
                return SourceStateAt(
                    basis="backend_started",
                    at=moment,
                    detail="the backend started before this moment and logged no transition since",
                )
        return self._current_state(
            "no transition is logged before this moment"
            + (f" ({reason})" if reason else "")
        )

    def _current_state(self, why: str) -> SourceStateAt:
        if self._state_machine is None:
            return SourceStateAt(basis="current", detail=f"{why}; the current state is unknown")
        state = self._state_machine.get_current_state()
        session = state.get("session") or {}
        phase = session.get("phase")
        return SourceStateAt(
            basis="current",
            source=state.get("source"),
            detail=(
                f"{why}; this is the state now, not then: service {state.get('service')}"
                + (f", session {phase}" if phase else ", no session")
            ),
        )
