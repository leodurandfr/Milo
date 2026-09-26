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
from typing import Any, Dict, List

from backend.core.background_analysis import BackgroundAnalysis
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

logger = logging.getLogger(__name__)


class CalibrationService(BackgroundAnalysis):
    """Owns the lifecycle of one Snapcast analysis at a time."""

    def __init__(self, state_machine, probe_service):
        super().__init__(logger, "calibration", EXPECTED_DURATION_S)
        self._state_machine = state_machine
        self._probe = probe_service

    def start(self, quality: str = "lossless") -> bool:
        """Begin an analysis. False when one is already running."""
        return super().start(quality)

    async def _analyze(self, quality: str) -> None:
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
            self._propose(payload)
            await self._state_machine.broadcast(RoutingCalibrationResult(**payload))

        except (NoRemoteClientError, ValueError) as exc:
            logger.info("Calibration has nothing to measure: %s", exc)
            await self._state_machine.broadcast(
                RoutingCalibrationFailed(reason="no_remote_client", detail=None)
            )
        except CalibrationProbeError as exc:
            logger.error("Calibration could not measure the fleet: %s", exc)
            await self._state_machine.broadcast(
                RoutingCalibrationFailed(reason="probe_failed", detail=str(exc))
            )
        except Exception:
            logger.error("Calibration failed unexpectedly", exc_info=True)
            await self._state_machine.broadcast(
                RoutingCalibrationFailed(reason="probe_failed", detail=None)
            )


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
