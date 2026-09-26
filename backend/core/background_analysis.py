# backend/core/background_analysis.py
"""One background analysis at a time: its run, its progress, its last result.

Shared by the Snapcast analysis (`core/multiroom/calibration_service.py`) and the
Mac link analysis (`core/mac_link/calibration_service.py`), which differ only in
what they measure and which events they broadcast. What they share is what the
routes and the panels rely on:

* exactly one run at a time — a second start is refused, never queued;
* progress as elapsed seconds against an expected duration, never a start
  timestamp: the browser would have to trust its own clock against this one to
  turn a timestamp into a position;
* the last proposal kept for the refetch a backgrounded tab makes, since the WS
  deltas that carried it are never replayed — and dropped when a run starts, so
  a refetch mid-run never pairs `running: true` with the previous proposal;
* a `cleanup()` the lifespan teardown awaits.

Neither analysis writes anything: each proposes, and the panel applies through
the resource's own PUT, its one writer.
"""
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from backend.shared.background import BackgroundTaskSet


class BackgroundAnalysis(ABC):
    """Subclasses implement `_analyze` and call `_propose` with the payload to
    keep, before broadcasting it, so a refetch the result prompts finds it."""

    def __init__(self, logger: logging.Logger, label: str, expected_seconds: float):
        self._bg = BackgroundTaskSet(logger, label)
        self._expected_seconds = expected_seconds
        self._running = False
        self._last_result: Optional[Dict[str, Any]] = None
        self._started_at = 0.0

    @property
    def running(self) -> bool:
        return self._running

    @property
    def last_result(self) -> Optional[Dict[str, Any]]:
        """The most recent proposal, for the refetch after a backgrounded tab."""
        return self._last_result

    @property
    def progress(self) -> Dict[str, float]:
        """What a panel needs to draw the bar after a refetch."""
        elapsed = (time.monotonic() - self._started_at) if self._running else 0.0
        return {"expected_seconds": self._expected_seconds, "elapsed_seconds": round(elapsed, 1)}

    def start(self, *args: Any) -> bool:
        """Begin a run. False when one is already running."""
        if self._running:
            return False
        self._last_result = None
        self._running = True
        self._started_at = time.monotonic()
        self._bg.spawn(self._run(*args), label="run")
        return True

    def forget(self) -> None:
        """Drop the last proposal: whoever asked for it left it unapplied.

        A run in progress is not touched. One client walking away is not a
        reason to stop measuring for the others.
        """
        self._last_result = None

    async def cleanup(self) -> None:
        await self._bg.cancel_all()

    def _propose(self, payload: Dict[str, Any]) -> None:
        self._last_result = payload

    async def _run(self, *args: Any) -> None:
        try:
            await self._analyze(*args)
        finally:
            self._running = False

    @abstractmethod
    async def _analyze(self, *args: Any) -> None:
        """Measure, broadcast progress, then either `_propose` and broadcast the
        verdict, or broadcast why there is none."""
