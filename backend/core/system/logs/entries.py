# backend/core/system/logs/entries.py
"""One log line, whatever wrote it, and the three shapes it is read from.

Two shapes hold the appliance's lines:

  * errors.log and its rotations — `[2026-11-27T09:00:00.123Z] WARNING
    backend.x - message`, UTC, a record's traceback on the lines after it
    (`core/log_handler.py::errors_log_formatter`, `backend/main.py`);
  * a journal, read as JSON so each line keeps its unit and its microsecond:
    the server's — its `milo-*` units, the system services Milō depends on,
    and the kernel's lines, filed under `kernel` (`parse_kernel`) — and each
    satellite's, which its `/diagnostic/journal` answers in the same JSON
    (`parse_satellite_reads`), filed under `<label>/<unit>`.

The level is read from the line where the line says it: Python's
`WARNING:backend.x:` and a satellite app's `… - logger - WARNING - …`, a Go
daemon's logrus `level=…`, snapcast's `[Warning]` on a stdout sink,
CamillaDSP's `WARN [`, and the status of a satellite's uvicorn access line
(a 4xx is a request the speaker refused, and says so nowhere else). Only a
line that names none is taken at its journal priority — which is where the backend's level is too since its stderr
carries the syslog prefix (`core/log_handler.py`), and where Navidrome's and
go-librespot's never is: journald files their stderr at PRIORITY 6, so the
logs page finds their warnings by their text (`logrus_grep`). A kernel line
names none of these, and is taken at its priority alone.
"""
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

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
# The satellite app's format (milo-client/app/main.py's basicConfig).
_PYTHON_ASCTIME = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} - (?P<logger>\S+) - "
    r"(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL) - (?P<message>.*)$", re.S,
)
# uvicorn's access line, which says a request's outcome by its status alone.
_ACCESS = re.compile(r'^INFO: +\S+ - "[A-Z]+ [^"]*" (?P<status>\d{3})\b')
_LOGRUS_LEVELS = ("trace", "debug", "info", "warning", "warn", "error", "fatal", "panic")
_LOGRUS = re.compile(rf"\blevel=(?P<level>{'|'.join(_LOGRUS_LEVELS)})\b")
_SNAPCAST = re.compile(r"\[(?P<level>Trace|Debug|Info|Notice|Warning|Error|Fatal)\]")
_CAMILLADSP = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+ (?P<level>TRACE|DEBUG|INFO|WARN|ERROR) \[")
_NAMED = {
    "trace": "debug", "debug": "debug", "info": "info", "notice": "info",
    "warning": "warning", "warn": "warning", "error": "error",
    "fatal": "critical", "panic": "critical", "critical": "critical",
}
# syslog priorities, for a line that names no level.
_PRIORITY = {0: "critical", 1: "critical", 2: "critical", 3: "error", 4: "warning",
             5: "info", 6: "info", 7: "debug"}


def _opens_a_record(message: str) -> bool:
    """Whether a line is a record of its own rather than the traceback of the
    record before it: a line in one of the logging formats, whatever its
    process wrote just before (a satellite app's error and the access line of
    the request it answered share a process and a second)."""
    return bool(_PYTHON.match(message) or _PYTHON_ASCTIME.match(message) or _ACCESS.match(message))


def logrus_grep(floor: str) -> str:
    """journalctl's `-g` for a logrus line at `floor` or above: the names
    `_LOGRUS` reads, those of that rank. Lowercase, so journalctl matches it
    in any case, as `_LOGRUS` does not need to: logrus writes it lowercase."""
    names = [name for name in _LOGRUS_LEVELS if LEVEL_RANK[_NAMED[name]] >= LEVEL_RANK[floor]]
    return rf"\blevel=({'|'.join(names)})\b"


def _named_level(message: str) -> Optional[str]:
    for pattern in (_LOGRUS, _SNAPCAST, _CAMILLADSP):
        found = pattern.search(message)
        if found:
            return _NAMED[found.group("level").lower()]
    return None


def classify(message: str, priority: Optional[int] = None):
    """(level, logger, message) of one line: the Python prefix is taken off
    and its logger kept; any other line keeps its text whole."""
    python = _PYTHON.match(message) or _PYTHON_ASCTIME.match(message)
    if python:
        return python.group("level").lower(), python.group("logger"), python.group("message")
    access = _ACCESS.match(message)
    if access:
        status = int(access.group("status"))
        return ("error" if status >= 500 else "warning" if status >= 400 else "info"), None, message
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
            and not _opens_a_record(message)
            and (at - previous.at).total_seconds() <= _CONTINUATION_S
        ):
            previous.message += "\n" + message
            continue
        level, logger, text = classify(message, priority)
        lines.append(LogLine(at=at, level=level, service=service, message=text,
                             logger=logger, pid=pid))
    return lines


# The service the kernel's lines are filed under, on the server as on a
# speaker (`<label>/kernel`, the satellite's heading for its kernel tail).
KERNEL_SERVICE = "kernel"


def parse_kernel(entries: Iterable[Dict[str, Any]]) -> List[LogLine]:
    """The kernel's journal entries (`_TRANSPORT=kernel`) as lines, filed under
    `kernel`, each at its priority: a kernel message names no level of its
    own. What wrote it, when that is not the kernel itself (a generator, or
    journald, writing to /dev/kmsg before the journal is up), is its logger.
    Never glued: two kernel lines in the same second are two records."""
    lines: List[LogLine] = []
    for entry in entries:
        if "__REALTIME_TIMESTAMP" not in entry:
            continue
        message = message_text(entry.get("MESSAGE")).rstrip()
        if not message:
            continue
        try:
            priority = int(entry.get("PRIORITY", 6))
        except (TypeError, ValueError):
            priority = 6
        identifier = entry.get("SYSLOG_IDENTIFIER")
        lines.append(LogLine(
            at=journal_time(entry), level=_PRIORITY.get(priority, "info"),
            service=KERNEL_SERVICE, message=message,
            logger=identifier if identifier and identifier != "kernel" else None,
        ))
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
# A satellite's journal, as its /diagnostic/journal answers it
# --------------------------------------------------------------------------- #

def parse_satellite_reads(label: str, reads: Iterable[Dict[str, Any]]) -> List[LogLine]:
    """The lines of one satellite's journal reads, each filed under
    `<label>/<unit>`, its kernel's under `<label>/kernel` — the label the
    report gives that speaker, never the room name its owner typed. The
    entries are journal JSON, as the server's own: the same parsers read them."""
    lines: List[LogLine] = []
    for read in reads:
        entries = read.get("entries") or []
        parsed = parse_kernel(entries) if read.get("kernel") else parse_journal(entries)
        for line in parsed:
            line.service = f"{label}/{line.service}"
        lines += parsed
    return sorted(lines, key=lambda line: line.at)
