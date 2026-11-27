# backend/core/system/logs/entries.py
"""One log line, whatever wrote it, and the three shapes it is read from.

Three places hold the appliance's lines, each in its own shape:

  * errors.log and its rotations — `[2026-11-27T09:00:00.123Z] WARNING
    backend.x - message`, UTC, a record's traceback on the lines after it
    (`core/log_handler.py::errors_log_formatter`, `backend/main.py`);
  * the journal of the server's `milo-*` units, read as JSON so each line keeps
    its unit and its microsecond;
  * a satellite's own journal tails, inside the diagnostic block it answers
    (`milo-client/app/routes/diagnostic.py`), `short-iso` under one
    `--- <unit> ---` heading per unit.

The level is read from the line where the line says it. Milō's Python services
reach journald through stderr, which files every line at PRIORITY=6 whatever
the record's level, so the level lives in the text (`WARNING:backend.x:`); a
Go daemon writes logrus' `level=…`, snapcast `[Warning]`. Only a line that
names none is taken at its journal priority.
"""
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from backend.core.system.diagnostic.satellite import JOURNAL_HEADING, UNIT_HEADING

LEVELS = ("debug", "info", "warning", "error", "critical")
LEVEL_RANK = {level: rank for rank, level in enumerate(LEVELS)}

# The service errors.log's lines are filed under: the process that writes the
# file. Its browser errors (logger `frontend`) are relayed by that process too.
ERRORS_LOG_SERVICE = "milo-backend.service"


@dataclass
class LogLine:
    """One line, as the page shows it. `at` is timezone-aware, in UTC."""

    at: datetime
    level: str
    service: str
    message: str
    logger: Optional[str] = None
    # Which process wrote it, for gluing a traceback to its record; never shown.
    pid: Optional[str] = None


# --------------------------------------------------------------------------- #
# The level a line names
# --------------------------------------------------------------------------- #

# Python's default `logging` format, as basicConfig writes it to stderr.
_PYTHON = re.compile(r"^(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL):(?P<logger>[^:\s]+):(?P<message>.*)$", re.S)
_LOGRUS = re.compile(r"\blevel=(?P<level>trace|debug|info|warning|warn|error|fatal|panic)\b")
_SNAPCAST = re.compile(r"\[(?P<level>Trace|Debug|Info|Notice|Warning|Error|Fatal)\]")
_NAMED = {
    "trace": "debug", "debug": "debug", "info": "info", "notice": "info",
    "warning": "warning", "warn": "warning", "error": "error",
    "fatal": "critical", "panic": "critical", "critical": "critical",
}
# syslog priorities, for a line that names no level.
_PRIORITY = {0: "critical", 1: "critical", 2: "critical", 3: "error", 4: "warning",
             5: "info", 6: "info", 7: "debug"}


def _named_level(message: str) -> Optional[str]:
    for pattern in (_LOGRUS, _SNAPCAST):
        found = pattern.search(message)
        if found:
            return _NAMED[found.group("level").lower()]
    return None


def classify(message: str, priority: Optional[int] = None):
    """(level, logger, message) of one line: the Python prefix is taken off
    and its logger kept; any other line keeps its text whole."""
    python = _PYTHON.match(message)
    if python:
        return python.group("level").lower(), python.group("logger"), python.group("message")
    level = _named_level(message) or _PRIORITY.get(priority if priority is not None else 6, "info")
    return level, None, message


# --------------------------------------------------------------------------- #
# errors.log
# --------------------------------------------------------------------------- #

_ERRORS_LOG = re.compile(
    r"^\[(?P<at>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3})Z\] "
    r"(?P<level>[A-Z]+) (?P<logger>\S+) - (?P<message>.*)$"
)


def parse_errors_log(text: str) -> List[LogLine]:
    """Every record of one errors.log file, oldest first.

    A line that does not open a record continues the one above it (a
    traceback, a multi-line message) and is kept on it; one before the first
    record is no record's and is dropped. Its time is UTC, as the formatter
    wrote it.
    """
    lines: List[LogLine] = []
    for raw in text.splitlines():
        record = _ERRORS_LOG.match(raw)
        if record:
            at = datetime.strptime(record.group("at"), "%Y-%m-%dT%H:%M:%S.%f")
            level = record.group("level").lower()
            lines.append(LogLine(
                at=at.replace(tzinfo=timezone.utc),
                level=level if level in LEVEL_RANK else "warning",
                service=ERRORS_LOG_SERVICE,
                logger=record.group("logger"),
                message=record.group("message"),
            ))
        elif lines and raw.strip():
            lines[-1].message += "\n" + raw.rstrip()
    return lines


# --------------------------------------------------------------------------- #
# The journal, as `journalctl -o json`
# --------------------------------------------------------------------------- #

# A traceback line carries no level prefix of its own: it is glued to the
# Python record before it when the same process wrote it this close after.
_CONTINUATION_S = 1.0


def message_text(value: Any) -> str:
    # journald hands a MESSAGE that is not valid UTF-8 as a list of bytes.
    if isinstance(value, list):
        return bytes(value).decode("utf-8", errors="replace")
    return value if isinstance(value, str) else ""


def journal_time(entry: Dict[str, Any]) -> datetime:
    micros = int(entry["__REALTIME_TIMESTAMP"])
    return datetime.fromtimestamp(micros // 1_000_000, timezone.utc).replace(
        microsecond=micros % 1_000_000
    )


def _service(entry: Dict[str, Any]) -> str:
    # systemd's own lines about a unit (started, failed, killed) are filed
    # under PID 1 and name the unit in UNIT.
    unit = entry.get("_SYSTEMD_UNIT")
    if not unit or not unit.startswith("milo-"):
        unit = entry.get("UNIT") or unit or entry.get("SYSLOG_IDENTIFIER") or "journal"
    return unit


def parse_journal(entries: Iterable[Dict[str, Any]]) -> List[LogLine]:
    """Journal entries (decoded JSON objects, oldest first) as lines."""
    lines: List[LogLine] = []
    for entry in entries:
        if "__REALTIME_TIMESTAMP" not in entry:
            continue
        message = message_text(entry.get("MESSAGE")).rstrip()
        if not message:
            continue
        at = journal_time(entry)
        service = _service(entry)
        pid = entry.get("_PID")
        try:
            priority = int(entry.get("PRIORITY", 6))
        except (TypeError, ValueError):
            priority = 6
        previous = lines[-1] if lines else None
        if (
            previous is not None and previous.logger is not None
            and previous.service == service and previous.pid == pid
            and not _PYTHON.match(message)
            and (at - previous.at).total_seconds() <= _CONTINUATION_S
        ):
            previous.message += "\n" + message
            continue
        level, logger, text = classify(message, priority)
        lines.append(LogLine(at=at, level=level, service=service, message=text,
                             logger=logger, pid=pid))
    return lines


def decode_json_lines(raw: Iterable[bytes]) -> List[Dict[str, Any]]:
    entries = []
    for line in raw:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


# --------------------------------------------------------------------------- #
# A satellite's journal, inside its diagnostic block
# --------------------------------------------------------------------------- #

_SHORT_ISO = re.compile(
    r"^(?P<at>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2}))"
    r" \S+ (?P<ident>[^\s:]+): (?P<message>.*)$"
)


def parse_satellite_block(label: str, text: str) -> List[LogLine]:
    """The journal lines of one satellite's block, each filed under
    `<label>/<unit>` — the label the report gives that speaker, never the
    room name its owner typed."""
    lines: List[LogLine] = []
    if JOURNAL_HEADING not in text.splitlines():
        return lines
    block = text.splitlines()
    start = len(block) - 1 - block[::-1].index(JOURNAL_HEADING)
    unit = None
    for raw in block[start + 1:]:
        heading = UNIT_HEADING.match(raw)
        if heading:
            unit = heading.group("unit")
            continue
        if unit is None or not raw.strip():
            continue
        entry = _SHORT_ISO.match(raw)
        if entry is None:
            if lines:
                lines[-1].message += "\n" + raw.strip()
            continue
        level, logger, message = classify(entry.group("message"))
        lines.append(LogLine(
            at=datetime.fromisoformat(entry.group("at")).astimezone(timezone.utc),
            level=level, service=f"{label}/{unit}", message=message, logger=logger,
        ))
    return lines
