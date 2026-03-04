"""Custom logging handler that broadcasts backend errors to the frontend via WebSocket."""
import asyncio
import logging
import time


class WebSocketLogHandler(logging.Handler):
    """
    Logging handler that forwards ERROR/WARNING logs to WebSocket clients.

    Uses asyncio.get_running_loop().create_task() to schedule async broadcasts
    from the synchronous logging.Handler.emit() method.
    """

    # Minimum interval between broadcasts (seconds) to avoid flooding
    MIN_BROADCAST_INTERVAL = 1.0

    def __init__(self, level=logging.WARNING):
        super().__init__(level)
        self._state_machine = None
        self._last_broadcast_time = 0

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

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._broadcast(record))
        except RuntimeError:
            pass  # No running event loop (startup/shutdown)

    async def _broadcast(self, record):
        try:
            await self._state_machine.broadcast_event("system", "backend_error", {
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            })
        except Exception:
            pass  # Never let broadcasting errors propagate
