"""Custom logging handler that broadcasts backend errors to the frontend via
WebSocket, and the format errors.log is written in."""
import asyncio
import contextlib
import logging
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
