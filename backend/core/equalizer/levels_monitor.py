# backend/core/equalizer/levels_monitor.py
"""
On-demand audio level sampling pushed over WebSocket.

Replaces the EQ modal's 10 Hz HTTP polling: every open EQ view POSTs a
keepalive to /api/equalizer/levels/monitor; while at least one keepalive is
fresh, this monitor samples output peaks at SAMPLE_INTERVAL and broadcasts a
single `equalizer`/`levels` event shared by all viewers. The sampling task
stops on its own once the last keepalive expires (modal closed, tab killed,
network gone) — there is no unsubscribe call.
"""
import asyncio
import contextlib
import logging
from typing import Any, Dict, List, Optional

from backend.core.models.ws_events import EqualizerLevels

logger = logging.getLogger(__name__)

SILENT_PEAK = [-80.0, -80.0]


class LevelsMonitor:
    """Samples CamillaDSP output levels while a UI keepalive is fresh."""

    SAMPLE_INTERVAL = 0.1   # 10 Hz — fast enough that the UI's CSS transition reads as continuous motion
    KEEPALIVE_TTL = 15.0    # sampling survives this long past the last keepalive

    def __init__(self, state_machine, equalizer_router, camilladsp_service):
        self.state_machine = state_machine
        self.equalizer_router = equalizer_router
        self.camilladsp_service = camilladsp_service
        self._client_ids: List[str] = []
        self._deadline: float = 0.0
        self._task: Optional[asyncio.Task] = None

    def keepalive(self, client_ids: List[str]) -> None:
        """Arm (or re-arm) the monitor for KEEPALIVE_TTL seconds.

        client_ids selects the clients to aggregate (last keepalive wins);
        empty means the local DAC.
        """
        self._client_ids = list(client_ids)
        self._deadline = asyncio.get_running_loop().time() + self.KEEPALIVE_TTL
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
            logger.debug("Levels monitor started (clients=%s)", self._client_ids or "local")

    async def cleanup(self) -> None:
        task = self._task
        self._task = None
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        last_payload = None
        while loop.time() < self._deadline:
            try:
                payload = await self._sample()
                if payload != last_payload:
                    await self.state_machine.broadcast(EqualizerLevels(**payload))
                    last_payload = payload
            except Exception as e:
                logger.error("Levels monitor sampling error: %s", e)
            await asyncio.sleep(self.SAMPLE_INTERVAL)
        logger.debug("Levels monitor stopped (keepalive expired)")

    async def _sample(self) -> Dict[str, Any]:
        """One reading: local DAC, or the AVERAGE across the requested clients."""
        if not self._client_ids:
            levels = await self.camilladsp_service.get_levels()
            if not levels.get("available"):
                return {"available": False, "output_peak": list(SILENT_PEAK)}
            return {"available": True, "output_peak": levels.get("output_peak", list(SILENT_PEAK))}

        results = await asyncio.gather(
            *(self._client_levels(cid) for cid in self._client_ids)
        )
        peaks = [
            r["output_peak"] for r in results
            if r and r.get("available") and len(r.get("output_peak") or []) >= 2
        ]
        if not peaks:
            return {"available": False, "output_peak": list(SILENT_PEAK)}
        return {
            "available": True,
            "output_peak": [
                sum(p[0] for p in peaks) / len(peaks),
                sum(p[1] for p in peaks) / len(peaks),
            ],
        }

    async def _client_levels(self, client_id: str) -> Optional[Dict[str, Any]]:
        # Best-effort: an offline satellite must not break the aggregate.
        try:
            return await self.equalizer_router.get_levels(client_id)
        except Exception as e:
            logger.debug("Levels unavailable for %s: %s", client_id, e)
            return None
