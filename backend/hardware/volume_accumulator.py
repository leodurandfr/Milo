# backend/hardware/volume_accumulator.py
"""
Shared volume accumulator for hardware controllers.

Batches rapid volume deltas (from rotary encoder detents, BT remote key
repeats, etc.) into periodic adjust_volume_db() calls.  Pattern: accumulate
dB deltas -> drain + apply -> sleep 20 ms -> re-check.  If new deltas arrive
during the finally block, respawn the processor to avoid silently dropping them.
"""
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class VolumeAccumulator:
    """Drain-accumulator + batch-sleep + tail-respawn volume processor."""

    BATCH_INTERVAL = 0.02  # 20ms between volume batches

    def __init__(self, volume_service):
        self._volume_service = volume_service
        self._accumulator = 0.0
        self._processor_running = False
        self._processor_task: Optional[asyncio.Task] = None

    def accumulate(self, delta_db: float):
        """Add a dB delta and spawn the processor if idle."""
        self._accumulator += delta_db
        if not self._processor_running:
            self._processor_running = True
            self._processor_task = asyncio.create_task(self._process())

    async def _process(self):
        """Drain accumulated dB deltas into volume adjustments."""
        try:
            while self._accumulator != 0.0:
                delta = self._accumulator
                self._accumulator = 0.0
                try:
                    await self._volume_service.adjust_volume_db(delta)
                except Exception as e:
                    logger.error("Error adjusting volume: %s", e)
                await asyncio.sleep(self.BATCH_INTERVAL)
        finally:
            # Re-check: if a delta arrived between the while-check and here,
            # respawn without clearing _processor_running to prevent duplicates.
            if self._accumulator != 0.0:
                self._processor_task = asyncio.create_task(self._process())
                return
            self._processor_running = False

    async def cleanup(self):
        """Cancel pending processor and wait for it to finish."""
        if self._processor_task and not self._processor_task.done():
            self._processor_task.cancel()
            try:
                await self._processor_task
            except (asyncio.CancelledError, Exception):
                pass
        self._processor_task = None
        self._accumulator = 0.0
        self._processor_running = False
