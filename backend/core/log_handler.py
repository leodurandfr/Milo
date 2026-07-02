"""Custom logging handler that broadcasts backend errors to the frontend via WebSocket."""
import contextlib
import logging
import time

from backend.shared.background import BackgroundTaskSet

logger = logging.getLogger(__name__)


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
        self._last_broadcast_time = 0
        self._bg = BackgroundTaskSet(logger, "log_handler")

    def set_state_machine(self, state_machine):
        """Set state machine reference (called after service initialization)."""
        self._state_machine = state_machine

    def emit(self, record):
        if not self._state_machine:
            return

        # Rate-limit: skip if too recent (lock for thread-safety — emit() can be
        # called from multiple threads, e.g. uvicorn's thread pool)
        now = time.monotonic()
        with self.lock:
            if now - self._last_broadcast_time < self.MIN_BROADCAST_INTERVAL:
                return
            self._last_broadcast_time = now

        self._bg.spawn(self._broadcast(record), label="broadcast_log")

    async def _broadcast(self, record):
        # Never let broadcasting errors propagate — otherwise a logged exception
        # would re-enter the WebSocket layer and loop. Intentionally silent.
        with contextlib.suppress(Exception):
            await self._state_machine.broadcast_event("system", "backend_error", {
                "message": record.getMessage(),
            })
