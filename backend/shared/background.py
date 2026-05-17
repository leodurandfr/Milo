# backend/shared/background.py
"""
Tracked fire-and-forget tasks with exception logging and cleanup.

Use BackgroundTaskSet to schedule coroutines that should not block the caller
but whose failures must still surface in logs. See CLAUDE.md §"Background tasks".
"""
import asyncio
import logging
from typing import Coroutine


class BackgroundTaskSet:
    """Owns a set of fire-and-forget tasks, logs uncaught exceptions, drains on cleanup."""

    def __init__(self, logger: logging.Logger, owner_label: str) -> None:
        self._tasks: set[asyncio.Task] = set()
        self._logger = logger
        self._owner = owner_label

    def spawn(self, coro: Coroutine, *, label: str) -> asyncio.Task | None:
        """
        Schedule coro as a tracked background task.

        Returns the task, or None if no event loop is running (coroutine is closed
        to avoid 'coroutine was never awaited' warnings). CancelledError is treated
        as a legitimate cancellation and never logged; any other exception is
        logged at ERROR level with exc_info.
        """
        try:
            task = asyncio.get_running_loop().create_task(
                coro, name=f"{self._owner}.{label}"
            )
        except RuntimeError:
            coro.close()
            self._logger.debug(
                "BG task '%s.%s' skipped: no running event loop",
                self._owner,
                label,
            )
            return None

        self._tasks.add(task)
        task.add_done_callback(self._make_done_callback(label))
        return task

    def _make_done_callback(self, label: str):
        def _cb(task: asyncio.Task) -> None:
            self._tasks.discard(task)
            if task.cancelled():
                return
            exc = task.exception()
            if exc is not None:
                self._logger.error(
                    "BG task '%s.%s' failed: %s",
                    self._owner,
                    label,
                    exc,
                    exc_info=exc,
                )
        return _cb

    async def cancel_all(self) -> None:
        """Cancel and drain every in-flight task. Idempotent."""
        if not self._tasks:
            return
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
