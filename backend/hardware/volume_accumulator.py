"""
Shared volume accumulator for hardware controllers.

Batches rapid volume deltas (from rotary encoder detents, BT remote key
repeats, etc.) into periodic adjust_volume_db() calls.  Pattern: accumulate
dB deltas -> drain + apply -> sleep 20 ms -> re-check.

The drain loop is the only owner of the processor task: it re-tests the
accumulator itself rather than respawning from a finally block, so a
cancelled processor stays cancelled.
"""
import asyncio
import contextlib
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class VolumeAccumulator:
    """Drain-accumulator + batch-sleep volume processor."""

    BATCH_INTERVAL = 0.02  # 20ms between volume batches

    def __init__(self, volume_service):
        self._volume_service = volume_service
        self._accumulator = 0.0
        self._processor_running = False
        self._processor_task: Optional[asyncio.Task] = None
        self._stopping = False

    def accumulate(self, delta_db: float):
        """Add a dB delta and spawn the processor if idle."""
        if self._stopping:
            return
        self._accumulator += delta_db
        if not self._processor_running:
            self._processor_running = True
            self._processor_task = asyncio.create_task(self._process())

    async def _process(self):
        """Drain accumulated dB deltas into volume adjustments."""
        try:
            while self._accumulator != 0.0 and not self._stopping:
                delta = self._accumulator
                self._accumulator = 0.0
                try:
                    await self._volume_service.adjust_volume_db(delta)
                except Exception as e:
                    logger.error("Error adjusting volume: %s", e)
                await asyncio.sleep(self.BATCH_INTERVAL)
        finally:
            # No respawn here: a delta cannot arrive between the while-check
            # and this line (accumulate() is synchronous), so the only way to
            # reach it with a non-empty accumulator is cancellation.
            self._processor_running = False

    async def cleanup(self):
        """Cancel pending processor and wait for it to finish."""
        self._stopping = True
        if self._processor_task and not self._processor_task.done():
            self._processor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._processor_task
        self._processor_task = None
        self._accumulator = 0.0
        self._processor_running = False
