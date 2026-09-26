# backend/core/mac_link/calibration_service.py
"""Measure the Mac's ROC link and broadcast what it should be set to.

Orchestration only: the parsing lives in `sources/mac/log_patterns.py`, the
arithmetic in `calibration`. Like the Snapcast analysis it **proposes and never
applies** — the configuration reaches the panel as an event, and is written only
when the user presses Apply, through PUT /api/settings/mac-roc. Nothing here
restarts roc-recv or asks the Mac to rebuild its device, so an analysis never
interrupts what is playing.

The one term only a playing stream shows is roc-recv's queue, so the analysis
needs Mac to be the active source with exactly one Mac on one stream: roc-recv's
statistics lines carry no session, and two streams' samples could not be told
apart.
"""
import asyncio
import contextlib
import json
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, List, Optional

from backend.core.background_analysis import BackgroundAnalysis
from backend.core.mac_link.calibration import MacLinkReading, compute_mac_link_configuration
from backend.core.models.ws_events import (
    MacCalibrationFailed,
    MacCalibrationProgress,
    MacCalibrationResult,
)
from backend.core.multiroom.calibration_probe import (
    CalibrationProbeError,
    PingReading,
    first_failure_cancels,
    ping,
    sample_local_scheduling,
)
from backend.shared.journalctl import follow_unit
from backend.sources.mac.log_patterns import (
    TunerSample,
    is_session_end,
    is_session_start,
    parse_loss_ratio,
    parse_packet_ms,
    parse_session_end_cause,
    parse_source_block,
    parse_tuner,
)

logger = logging.getLogger(__name__)

ROC_UNIT = "milo-mac.service"

# One minute, pinged at 5 Hz throughout. roc-recv logs a queue sample every
# 5 s; the minute is read on top of what the running roc-recv logged just
# before — a Mac streaming for a while gives far more than the minute alone.
MEASURE_SECONDS = 60
MAC_PING_COUNT = 300
EXPECTED_DURATION_S = MEASURE_SECONDS + 2

# The last lines replayed before the live minute: about 40 a minute while a Mac
# streams, so a little under an hour — bounded, because replaying days of a
# long-running roc-recv ate the minute and loaded the unit whose scheduling is
# being measured at the same time.
REPLAY_LINES = 2000

# A session's first queue line carries nothing, so a minute of one gives eleven.
# Fewer is a Mac that arrived or left during the analysis.
MIN_SAMPLES = 10
# Ten minutes at most: an older stretch may predate a change of the Mac's network.
MAX_SAMPLES = 120
# roc-recv logs its post-FEC loss every 20 s: the last six cover two minutes,
# and a rise among them is a loss on the link as it is now.
LOSS_WINDOW = 6

# The ends that say the running configuration is too tight. `no_playback` is
# not among them: it is also how every ordinary departure ends.
UNSTABLE_ENDS = frozenset({"latency_out_of_bounds", "choppy_playback"})


class MacLinkUnavailable(RuntimeError):
    """The link could not be measured, for a reason the panel names: `reason`
    is its i18n key, `detail` the Mac it concerns when there is one."""

    def __init__(self, reason: str, detail: Optional[str] = None):
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


@dataclass
class StreamReading:
    """What roc-recv's journal said about the session being measured."""
    samples: List[TunerSample] = field(default_factory=list)
    packet_ms: Optional[float] = None
    fec_source: Optional[int] = None
    loss_ratios: Deque[float] = field(default_factory=lambda: deque(maxlen=LOSS_WINDOW))
    unstable_ends: int = 0
    # roc logs why it ends a session on the line before it says it does.
    pending_cause: Optional[str] = None

    def read(self, message: str) -> None:
        """Fold one roc-recv line in. Every session boundary starts the queue
        figures over: those before a start describe a stream that is gone, and
        between a second session's start and the first one's end both streams'
        samples arrive together, so an end drops that stretch too."""
        if is_session_start(message):
            self._restart_figures()
            self.packet_ms, self.fec_source = None, None
            return
        cause = parse_session_end_cause(message)
        if cause is not None:
            self.pending_cause = cause
            return
        if is_session_end(message):
            if self.pending_cause in UNSTABLE_ENDS:
                self.unstable_ends += 1
            self.pending_cause = None
            self._restart_figures()
            return
        sample = parse_tuner(message)
        if sample is not None:
            self.samples.append(sample)
            del self.samples[:-MAX_SAMPLES]
            return
        ratio = parse_loss_ratio(message)
        if ratio is not None:
            self.loss_ratios.append(ratio)
            return
        packet_ms = parse_packet_ms(message)
        if packet_ms is not None:
            self.packet_ms = packet_ms
            return
        fec_source = parse_source_block(message)
        if fec_source is not None:
            self.fec_source = fec_source

    def _restart_figures(self) -> None:
        self.samples = []
        self.loss_ratios.clear()

    @property
    def loss_rising(self) -> bool:
        return len(self.loss_ratios) > 1 and self.loss_ratios[-1] > self.loss_ratios[0]


async def _ping_mac(ip: str, name: str) -> PingReading:
    """The ping, a failure named after the Mac: macOS's stealth mode drops
    every probe, and that is something its owner can switch off."""
    try:
        return await ping(ip, count=MAC_PING_COUNT)
    except CalibrationProbeError as exc:
        logger.info("%s (%s) answers no ping: %s", name, ip, exc)
        raise MacLinkUnavailable("no_ping_reply", name) from exc


class MacLinkCalibrationService(BackgroundAnalysis):
    """Owns the lifecycle of one Mac link analysis at a time."""

    def __init__(self, state_machine, settings_service, systemd_manager, mac_source):
        super().__init__(logger, "mac_link_calibration", EXPECTED_DURATION_S)
        self._state_machine = state_machine
        self._settings = settings_service
        self._systemd = systemd_manager
        self._mac_source = mac_source

    async def _analyze(self) -> None:
        try:
            await self._state_machine.broadcast(
                MacCalibrationProgress(stage="measuring", expected_seconds=EXPECTED_DURATION_S)
            )
            reading = await self._measure()
            await self._state_machine.broadcast(MacCalibrationProgress(stage="computing"))
            result = compute_mac_link_configuration(reading)
            logger.info(
                "Mac link analysis proposes %s (%s)", result.config,
                "; ".join(f"{code}={params}" for code, params in result.reasons),
            )
            payload = {
                "config": result.config,
                "predicted_latency_ms": result.predicted_latency_ms,
                "measurements": result.measurements,
                "assumed": result.assumed,
            }
            self._propose(payload)
            await self._state_machine.broadcast(MacCalibrationResult(**payload))

        except MacLinkUnavailable as exc:
            logger.info("Mac link analysis stopped: %s (%s)", exc.reason, exc.detail)
            await self._state_machine.broadcast(MacCalibrationFailed(reason=exc.reason, detail=exc.detail))
        except CalibrationProbeError as exc:
            logger.error("Mac link analysis could not measure the link: %s", exc)
            await self._state_machine.broadcast(MacCalibrationFailed(reason="probe_failed", detail=None))
        except Exception:
            logger.error("Mac link analysis failed unexpectedly", exc_info=True)
            await self._state_machine.broadcast(MacCalibrationFailed(reason="probe_failed", detail=None))

    def _the_one_mac(self):
        """(ip, name) of the one Mac on one stream, or why there is not one."""
        macs = self._mac_source.streaming_macs
        if not macs:
            raise MacLinkUnavailable("no_mac")
        if len(macs) > 1:
            raise MacLinkUnavailable("several_macs", ", ".join(sorted(macs.values())))
        (ip, name), = macs.items()
        if self._mac_source.stream_count > 1:
            # A Mac reopening its stream holds two for a moment: both sessions'
            # samples would land in one reading.
            raise MacLinkUnavailable("two_streams", name)
        return ip, name

    async def _measure(self) -> MacLinkReading:
        ip, name = self._the_one_mac()

        # Read before the minute of measuring: an Apply landing meanwhile is
        # caught by the pid below rather than compared against the new values.
        stored = await self._settings.get_setting("mac")
        pid = await self._systemd.main_pid(ROC_UNIT)
        started_usec = await self._systemd.main_start_usec(ROC_UNIT)
        if pid is None or started_usec is None:
            raise MacLinkUnavailable("receiver_not_running")

        reply, sched_max_ms, stream = await first_failure_cancels(
            _ping_mac(ip, name),
            sample_local_scheduling(),
            self._read_stream(pid, started_usec),
        )

        if await self._systemd.main_pid(ROC_UNIT) != pid:
            raise MacLinkUnavailable("receiver_restarted")
        # Checked again: a second Mac, or a second stream, arriving during the
        # minute and still there sends its samples into the same reading.
        self._the_one_mac()
        if len(stream.samples) < MIN_SAMPLES:
            raise MacLinkUnavailable("too_few_samples", name)

        return MacLinkReading(
            mac_name=name,
            ping=reply,
            samples=stream.samples,
            sched_max_ms=sched_max_ms,
            stored=stored,
            observed_packet_ms=stream.packet_ms,
            observed_fec_source=stream.fec_source,
            post_fec_loss_ratio=stream.loss_ratios[-1] if stream.loss_ratios else None,
            post_fec_loss_rising=stream.loss_rising,
            unstable_ends=stream.unstable_ends,
        )

    async def _read_stream(self, pid: int, started_usec: int) -> StreamReading:
        """The last lines the running roc-recv logged, then the rest of the
        minute. Lines of an earlier roc-recv, which journald can deliver late,
        are skipped by their pid. A feed that ends before the minute is up is an
        analysis that could not read the queue, not a short session."""
        stream = StreamReading()
        lines = follow_unit(
            ROC_UNIT,
            consequence="this Mac link analysis stopped reading roc-recv",
            output="json",
            tail=REPLAY_LINES,
            since=f"@{started_usec / 1_000_000:.6f}",
            logger=logger,
        )
        try:
            async with contextlib.aclosing(lines), asyncio.timeout(MEASURE_SECONDS):
                async for entry in lines:
                    try:
                        fields = json.loads(entry)
                    except ValueError:
                        continue
                    message = fields.get("MESSAGE")
                    if isinstance(message, str) and str(fields.get("_PID")) == str(pid):
                        stream.read(message)
        except TimeoutError:
            return stream
        raise MacLinkUnavailable("journal_unreadable")


__all__: List[str] = ["MacLinkCalibrationService"]
