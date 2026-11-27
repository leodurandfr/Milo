# backend/core/system/logs/service.py
"""The appliance's logs, read when the logs page asks and never otherwise.

Nothing here runs in the background and nothing is kept between two reads:
each answer collects the window (`collect.py`, which the maintainer's triage
reads too: errors.log and its rotations, the server's journal — its `milo-*`
units, and at warning and above the system services Milō depends on and the
kernel — and each online speaker's own journal over the same window, at
warning and above), then groups what it read (`grouping.py`); and the Pi's
throttling state, asked of the firmware (`throttling.py`). A place that could
not be read is a `not_collected` entry with its reason — never a silence,
which would read as "nothing happened there".

**Every line that leaves passes through the diagnostic's Redactor** (built by
`DiagnosticService.build_redactor`, from the same reads a report makes: the
WiFi networks NetworkManager holds, the share names, the speakers' and zones'
names, the Bluetooth devices, what is playing), then through
`strip_public_ipv4`. The API is not authenticated and the page is meant to
stay publishable, so a line is redacted before it is filtered, grouped or
cut: a text search for a network name finds nothing, rather than telling who
asks that the network exists. The Redactor is told the service of each line,
because a journal message does not name its unit: a line of NetworkManager or
wpa_supplicant is about a network, one of bluetoothd about Bluetooth, whatever
its text says.

**What costs CPU runs off the event loop** — parsing up to 8 MB of errors.log,
5000 journal entries, the Redactor's passes over every line, the grouping —
in a thread (`_in_thread`), one answer at a time: measured, 50 000 lines
through the Redactor took 2.4 s on the dev VM, several times that on a Pi,
and on the loop that is the WebSocket, mpv's events and the rotary's volume
frozen for as long. The Redactor is built per answer, so one thread uses it.
"""
import asyncio
import logging
import re
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from backend.config.constants import ERROR_LOG_FILE
from backend.core.system.diagnostic.whitelist import strip_public_ipv4
from backend.core.system.logs import grouping, journal, throttling
from backend.core.system.logs.collect import (
    collect,
    drop_journaled,
    first_line,
    journal_failures,
    parse_reads,
    read_others,
)
from backend.core.system.logs.entries import (
    LEVEL_RANK,
    LogLine,
    journal_time,
    message_text,
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

# A speaker's lines around an event, for its context: its journal has no
# line count to ask by, so a window, wide enough for the 20 lines on each side
# of a busy moment.
_CONTEXT_SPEAKER_WINDOW = timedelta(minutes=10)

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
        key = (line.at, line.service, line.pid, first_line(line.message))
        kept = unique.get(key)
        if kept is None or len(line.message) > len(kept.message):
            unique[key] = line
    return sorted(unique.values(), key=lambda line: line.at)


class LogService:
    """Reads, groups and redacts. Holds no state and spawns no task."""

    # Each journal read's cap, newest kept: two days of a quiet unit's
    # `milo-*` lines at every level, a few hours of a noisy one; seven days
    # and more at warning and above, which journalctl filters before it — a
    # 15- or 30-day retention (`system.log_retention_days`) is not measured
    # against it yet. A window a read cuts is said so (`truncated`).
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
        *,
        settings_service,
    ):
        self._diagnostic = diagnostic_service
        self._version = version_service
        self._state_machine = state_machine
        self._error_log = error_log
        # Read for the journal's retention, which the page offers periods up to.
        self._settings = settings_service
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
        `q` a case-insensitive text matched against the redacted line; and
        the Pi's throttling state as it is now, and how long the journal is
        kept (`retention_days`)."""
        retention_days = await self._settings.get_setting("system.log_retention_days")
        until = datetime.now(timezone.utc)
        start = until - since
        collected, sanitize, throttled = await asyncio.gather(
            collect(
                since=start, until=until, level=level, cap=self.JOURNAL_MAX_ENTRIES,
                error_log=self._error_log, speakers=self._diagnostic.satellite_targets(),
                cpu=self._in_thread,
            ),
            self._sanitizer(),
            throttling.read(),
        )
        not_collected = list(collected.not_collected)
        if isinstance(throttled, str):
            not_collected.append(NotCollected(source="throttling", reason=throttled))
            throttled = None

        def answer() -> LogsPage:
            lines = self._redacted(collected.lines, sanitize)
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
                truncated=collected.truncated,
                journal_from=collected.journal_from,
                warnings_whole=collected.warnings_whole,
                throttling=throttled,
                not_collected=self._redacted_reasons(not_collected, sanitize),
                retention_days=retention_days,
            )

        return await self._in_thread(answer)

    async def context(self, *, at: datetime, service: Optional[str] = None) -> LogContext:
        """The `CONTEXT_LINES` lines on each side of the event at `at` (of
        `service`, when given), every service together — `milo-*` at every
        level, the system services and the kernel at warning and above, as on
        the page; the event's own text, when a line is exactly at `at`,
        redacted with them; the release the unit runs; and the audio source at
        that moment (`_source_state_at`)."""
        at = at.astimezone(timezone.utc)
        n = self.CONTEXT_LINES + 1
        reads = journal.context_reads(at=at, n=n)
        (
            sanitize,
            results,
            (others, not_collected),
            build,
            source_state,
        ) = await asyncio.gather(
            self._sanitizer(),
            asyncio.gather(*(journal.read(r.args, max_entries=n) for r, _ in reads)),
            read_others(
                error_log=self._error_log, speakers=self._diagnostic.satellite_targets(),
                since=at - _CONTEXT_SPEAKER_WINDOW, until=at + _CONTEXT_SPEAKER_WINDOW,
            ),
            self._running_build(),
            self._source_state_at(at),
        )
        not_collected += journal_failures([r for r, _ in reads], results)

        def answer() -> LogContext:
            journaled = _one_per_record(parse_reads(
                [r for r, _ in reads], [entries for (entries, _, _) in results],
            ))
            # Where a journal read was cut, a line from elsewhere past the cut
            # would sit next to lines that read never had between them: the
            # window keeps what every cut read covers.
            low = high = None
            for (read, before), (entries, _, full) in zip(reads, results):
                times = [journal_time(e) for e in entries if "__REALTIME_TIMESTAMP" in e]
                if not (full and times):
                    continue
                if before:
                    low = max(low, min(times)) if low else min(times)
                else:
                    high = min(high, max(times)) if high else max(times)
            merged = sorted(
                (
                    line for line in (*journaled, *drop_journaled(others, journaled))
                    if (low is None or line.at >= low) and (high is None or line.at <= high)
                ),
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

    async def _sanitizer(self) -> Callable[..., str]:
        """The Redactor of this answer, then the public IPv4 rule: a text and,
        for a line, the service it is filed under.

        Never a fallback: a Redactor that could not be built fails the read,
        because a line sent without it would carry what it exists to remove.
        """
        redactor = await self._diagnostic.build_redactor()
        return lambda text, unit=None: strip_public_ipv4(redactor(text, unit=unit))

    def _redacted(self, lines: Sequence[LogLine], sanitize: Callable[..., str]) -> List[LogLine]:
        """Each line redacted, oldest first: the Redactor labels a value
        found by its shape in the order it meets it."""
        out = []
        for line in sorted(lines, key=lambda line: line.at):
            message = sanitize(line.message, line.service)
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
            raw = first_line(message_text(entry.get("MESSAGE")))
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
