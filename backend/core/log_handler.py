"""Custom logging handler that broadcasts backend errors to the frontend via
WebSocket, the format errors.log is written in, and the one stderr is."""
import asyncio
import contextlib
import logging
import os
import time
from time import monotonic

from backend.core.models.ws_events import SystemBackendError
from backend.shared.background import BackgroundTaskSet

logger = logging.getLogger(__name__)


def errors_log_formatter() -> logging.Formatter:
    """errors.log's line: `[2026-11-27T09:00:00.123Z] WARNING backend.x - message`.

    UTC, and marked so (`Z`). Local time read back by another process names
    no moment: glibc reads /etc/localtime once per process, so every line
    written before a time zone change (the setup wizard's, then a reboot)
    read an hour off, and the hour that repeats each autumn names two moments.
    Read back by `core/system/logs/entries.py::parse_errors_log`.
    """
    formatter = logging.Formatter("[%(asctime)s] %(levelname)s %(name)s - %(message)s")
    formatter.converter = time.gmtime
    formatter.default_time_format = "%Y-%m-%dT%H:%M:%S"
    formatter.default_msec_format = "%s.%03dZ"
    return formatter


# A record's level as syslog numbers it, which journald reads off a `<N>` that
# opens a line of a unit's stdout or stderr (`SyslogLevelPrefix=`, on by
# default) and takes off the line.
_SYSLOG_PRIORITY = (
    (logging.CRITICAL, 2), (logging.ERROR, 3), (logging.WARNING, 4), (logging.INFO, 6),
)


def syslog_priority(levelno: int) -> int:
    for floor, priority in _SYSLOG_PRIORITY:
        if levelno >= floor:
            return priority
    return 7


class JournalPriorityFormatter(logging.Formatter):
    """Python's line, every line of it opened by the record's syslog priority.

    journald files a stderr line at PRIORITY 6 whatever it says, so the
    backend's warnings could be found only by reading the text of every line
    (`journalctl -p warning` saw none of them). With the prefix, the PRIORITY
    is the record's level, and the logs page asks journalctl for a level
    rather than reading a week of lines to filter them itself. Every line, a
    traceback's included: a line with no prefix is filed at 6, and a read at
    warning would keep the record and drop its traceback. The prefix is taken
    off, so MESSAGE is the line Python wrote (`WARNING:backend.x:…`).
    """

    def format(self, record: logging.LogRecord) -> str:
        prefix = f"<{syslog_priority(record.levelno)}>"
        return "\n".join(prefix + line for line in super().format(record).split("\n"))


def stderr_is_the_journal() -> bool:
    """Whether stderr is the journal stream systemd connected, by the
    `JOURNAL_STREAM` (`<device>:<inode>`) it sets: a run in a terminal, or with
    stderr redirected, inherits nothing or another file, and prints no `<4>`."""
    stream = os.environ.get("JOURNAL_STREAM", "")
    device, _, inode = stream.partition(":")
    try:
        expected = (int(device), int(inode))
        stat = os.fstat(2)
    except (ValueError, OSError):
        return False
    return (stat.st_dev, stat.st_ino) == expected


def stderr_formatter() -> logging.Formatter:
    """stderr's line: Python's default (`WARNING:backend.x:message`, which
    `core/system/logs/entries.py::classify` reads), carrying its priority
    when stderr is the journal."""
    if stderr_is_the_journal():
        return JournalPriorityFormatter(logging.BASIC_FORMAT)
    return logging.Formatter(logging.BASIC_FORMAT)


class WebSocketLogHandler(logging.Handler):
    """
    Logging handler that forwards ERROR logs to WebSocket clients.

    Schedules async broadcasts from the synchronous logging.Handler.emit()
    method via BackgroundTaskSet.
    """

    # Minimum interval between broadcasts (seconds) to avoid flooding
    MIN_BROADCAST_INTERVAL = 1.0

    def __init__(self, level=logging.ERROR):
        super().__init__(level)
        self._state_machine = None
        self._loop = None
        self._last_broadcast_time = 0
        self._bg = BackgroundTaskSet(logger, "log_handler")

    def set_state_machine(self, state_machine):
        """Set state machine reference (called after service initialization)."""
        self._state_machine = state_machine
        # The loop an error logged from another thread is handed to.
        with contextlib.suppress(RuntimeError):
            self._loop = asyncio.get_running_loop()

    def emit(self, record):
        if not self._state_machine:
            return

        # Rate-limit: skip if too recent (lock for thread-safety — emit() can be
        # called from multiple threads, e.g. uvicorn's thread pool)
        now = monotonic()
        with self.lock:
            if now - self._last_broadcast_time < self.MIN_BROADCAST_INTERVAL:
                return
            self._last_broadcast_time = now

        # Formatted now: the arguments may change before the broadcast runs.
        message = record.getMessage()
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None and (self._loop is None or running is self._loop):
            self._spawn_broadcast(message)
            return
        # Off the backend's loop (a to_thread worker, a GPIO callback, a library
        # thread running its own loop): hand the broadcast to the backend's.
        if self._loop is not None:
            with contextlib.suppress(RuntimeError):  # loop already closed
                self._loop.call_soon_threadsafe(self._spawn_broadcast, message)

    def _spawn_broadcast(self, message):
        self._bg.spawn(self._broadcast(message), label="broadcast_log")

    async def _broadcast(self, message):
        # Never let broadcasting errors propagate — otherwise a logged exception
        # would re-enter the WebSocket layer and loop. Intentionally silent.
        with contextlib.suppress(Exception):
            await self._state_machine.broadcast(
                SystemBackendError(message=message)
            )
