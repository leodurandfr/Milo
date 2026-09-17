# backend/core/multiroom/calibration_service.py
"""Run the Snapcast analysis and broadcast what it found.

Orchestration only: the measuring lives in `calibration_probe`, the arithmetic
in `calibration`. This module owns the three things neither of those should --
that exactly one analysis runs at a time, that its progress reaches the UI, and
that its verdict survives a tab being backgrounded.

**It proposes, it does not apply.** The configuration travels to the user as an
event and is written only when they press apply, through the existing
``PUT /api/routing/snapcast/server-config``. Applying here would make an
analysis a silent reconfiguration of every speaker in the house, and would put a
second writer on a resource that already has exactly one.

The run is a background task rather than a held-open HTTP request because it
takes tens of seconds today and will take minutes once it also verifies by
playing. A progress bar the user can watch is the difference between a feature
and a frozen screen.
"""
import logging
from typing import Any, Dict, List, Optional

from backend.core.models.ws_events import (
    RoutingCalibrationFailed,
    RoutingCalibrationProgress,
    RoutingCalibrationResult,
)
from backend.core.multiroom.calibration import compute_configuration
from backend.core.multiroom.calibration_probe import (
    CalibrationProbeError,
    EXPECTED_DURATION_S,
    NoRemoteClientError,
)
import time
from backend.shared.background import BackgroundTaskSet

logger = logging.getLogger(__name__)


class CalibrationService:
    """Owns the lifecycle of one Snapcast analysis at a time."""

    def __init__(self, state_machine, probe_service):
        self._state_machine = state_machine
        self._probe = probe_service
        self._bg = BackgroundTaskSet(logger, "calibration")
        self._running = False
        self._last_result: Optional[Dict[str, Any]] = None
        self._started_at: float = 0.0

    @property
    def running(self) -> bool:
        return self._running

    @property
    def last_result(self) -> Optional[Dict[str, Any]]:
        """The most recent proposal, for the refetch after a backgrounded tab.

        Progress and result arrive as WS deltas and deltas are never replayed,
        so a client that reconnects mid-run would otherwise show an idle screen
        while an analysis was still going.
        """
        return self._last_result

    @property
    def progress(self) -> Dict[str, float]:
        """What the UI needs to draw the bar after a refetch.

        Elapsed rather than a start timestamp: the browser would have to trust
        its own clock against this one to turn a timestamp into a position, and
        the two are only as close as whoever set them.
        """
        elapsed = (time.monotonic() - self._started_at) if self._running else 0.0
        return {"expected_seconds": EXPECTED_DURATION_S, "elapsed_seconds": round(elapsed, 1)}

    def start(self, quality: str = "lossless") -> bool:
        """Begin an analysis. False when one is already running."""
        if self._running:
            return False
        # Dropped now, not when the new one lands. Held through the run, a
        # refetch mid-analysis answered `running: true` beside the *previous*
        # proposal, and the panel staged that stale configuration -- spending
        # the one staging the fresh result was waiting for.
        self._last_result = None
        self._running = True
        self._started_at = time.monotonic()
        self._bg.spawn(self._run(quality), label="run")
        return True

    async def cleanup(self) -> None:
        await self._bg.cancel_all()

    async def _run(self, quality: str) -> None:
        try:
            await self._state_machine.broadcast(
                RoutingCalibrationProgress(
                    stage="probing", expected_seconds=EXPECTED_DURATION_S
                )
            )
            readings, assumed = await self._probe.measure()

            await self._state_machine.broadcast(
                RoutingCalibrationProgress(
                    stage="computing",
                    clients_total=len(readings),
                    clients_done=len(readings),
                )
            )
            result = compute_configuration(readings, quality=quality)

            # Logged rather than shipped: the reasoning is what explains a
            # computed number when one looks wrong, and the panel that would
            # have displayed it already shows the result on its own sliders.
            logger.info(
                "Calibration proposes %s (%s)", result.config,
                "; ".join(f"{code}={params}" for code, params in result.reasons),
            )

            payload = {
                "config": result.config,
                "predicted_latency_ms": result.predicted_latency_ms,
                "limiting_client": result.limiting_client,
                "measurements": [_reading_payload(r) for r in readings],
                "assumed": assumed,
            }
            self._last_result = payload
            await self._state_machine.broadcast(RoutingCalibrationResult(**payload))

        except (NoRemoteClientError, ValueError) as exc:
            logger.info("Calibration has nothing to measure: %s", exc)
            self._last_result = None
            await self._state_machine.broadcast(
                RoutingCalibrationFailed(reason="no_remote_client", detail=None)
            )
        except CalibrationProbeError as exc:
            logger.error("Calibration could not measure the fleet: %s", exc)
            self._last_result = None
            await self._state_machine.broadcast(
                RoutingCalibrationFailed(reason="probe_failed", detail=str(exc))
            )
        except Exception:
            logger.error("Calibration failed unexpectedly", exc_info=True)
            self._last_result = None
            await self._state_machine.broadcast(
                RoutingCalibrationFailed(reason="probe_failed", detail=None)
            )
        finally:
            self._running = False


def _reading_payload(reading) -> Dict[str, Any]:
    """One client's measurements, for the UI's per-speaker breakdown.

    The proposal is a single number for the whole house, so without this the
    user can see *what* was decided but never *which speaker decided it*.
    """
    return {
        "mac_id": reading.mac_id,
        "name": reading.name,
        "link": reading.link,
        "link_speed_mbps": reading.link_speed_mbps,
        "signal_percent": reading.signal_percent,
        "rtt_p50_ms": round(reading.rtt_p50_ms, 3),
        "rtt_max_ms": round(reading.rtt_max_ms, 3),
        "loss_pct": round(reading.loss_pct, 2),
        "sched_max_ms": round(reading.sched_max_ms, 3),
        "is_local": reading.is_local,
    }


__all__: List[str] = ["CalibrationService"]
