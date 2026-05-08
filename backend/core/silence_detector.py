# backend/core/silence_detector.py
"""
SilenceDetector - audio silence detection via CamillaDSP capture levels.

Polls `CamillaDSPService.get_levels()` and fires hysteresis-protected
callbacks when the input peak stays below a dBFS threshold for a
continuous window. Used by sources that have no native pause event
(Bluetooth A2DP, ROC stream from macOS) to reuse the BaseAudioSource
auto-disconnect timer.

Why CamillaDSP levels instead of a hardware ALSA tap:
    BT/ROC audio always passes through CamillaDSP (direct mode hits it
    on the loopback; multiroom mode loops back via snapclient on the
    local zone). CamillaDSP already exposes a peak meter on the capture
    side — no new ALSA routing, services, or native dependency needed.

Limitation:
    In a multiroom setup with the local zone disabled, CamillaDSP idles
    even when remote zones play. Since BT/ROC require local hardware
    (Bluetooth controller, ROC UDP receiver), the local zone is in
    practice always active when these sources are.
"""
import asyncio
import logging
from typing import Awaitable, Callable, Optional


SilenceCallback = Callable[[], Awaitable[None]]


class SilenceDetector:
    """Polls CamillaDSP capture levels and fires silence/resume callbacks."""

    DEFAULT_THRESHOLD_DBFS = -60.0
    DEFAULT_IDLE_SECONDS = 2.0
    DEFAULT_POLL_INTERVAL = 0.5

    def __init__(
        self,
        camilladsp_service,
        threshold_dbfs: float = DEFAULT_THRESHOLD_DBFS,
        idle_seconds: float = DEFAULT_IDLE_SECONDS,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        logger: Optional[logging.Logger] = None,
    ):
        self._camilladsp = camilladsp_service
        self._threshold_dbfs = threshold_dbfs
        self._idle_seconds = idle_seconds
        self._poll_interval = poll_interval
        self._logger = logger or logging.getLogger(__name__)

        self._on_silence_started: Optional[SilenceCallback] = None
        self._on_audio_resumed: Optional[SilenceCallback] = None

        self._task: Optional[asyncio.Task] = None
        self._silent_since: Optional[float] = None
        self._silence_signaled: bool = False

    def set_callbacks(
        self,
        on_silence_started: Optional[SilenceCallback] = None,
        on_audio_resumed: Optional[SilenceCallback] = None,
    ) -> None:
        """Configure callbacks fired on edge transitions (silence/resume)."""
        self._on_silence_started = on_silence_started
        self._on_audio_resumed = on_audio_resumed

    async def start(self) -> None:
        """Start polling. Idempotent — a running detector is left untouched."""
        if self._task and not self._task.done():
            return
        self._reset_window()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Cancel the polling task and clear silence state."""
        task = self._task
        self._task = None
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._reset_window()

    def _reset_window(self) -> None:
        self._silent_since = None
        self._silence_signaled = False

    async def _loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._poll_interval)
                await self._tick()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # If the loop dies unexpectedly, drop the task ref and clear the
            # silence window so a stale armed timer doesn't fire after we've
            # lost the ability to cancel it via on_audio_resumed.
            self._logger.error(f"SilenceDetector loop error: {e}")
            self._task = None
            self._reset_window()

    async def _tick(self) -> None:
        peak = await self._read_peak()
        if peak is None:
            return

        now = asyncio.get_running_loop().time()
        if peak < self._threshold_dbfs:
            await self._handle_below_threshold(now)
        else:
            await self._handle_above_threshold()

    async def _read_peak(self) -> Optional[float]:
        """Return max input peak in dBFS, or None if unavailable."""
        try:
            levels = await self._camilladsp.get_levels()
        except Exception as e:
            self._logger.debug(f"get_levels failed: {e}")
            return None

        if not levels or not levels.get("available"):
            return None

        input_peak = levels.get("input_peak")
        if input_peak is None:
            return None

        # input_peak is a list of per-channel dBFS floats; treat the loudest
        # channel as the overall signal level.
        try:
            return max(input_peak)
        except (TypeError, ValueError):
            return None

    async def _handle_below_threshold(self, now: float) -> None:
        if self._silent_since is None:
            self._silent_since = now
            return
        if self._silence_signaled:
            return
        if (now - self._silent_since) < self._idle_seconds:
            return

        self._silence_signaled = True
        if self._on_silence_started:
            try:
                await self._on_silence_started()
            except Exception as e:
                self._logger.error(f"on_silence_started failed: {e}")

    async def _handle_above_threshold(self) -> None:
        was_signaled = self._silence_signaled
        self._silent_since = None
        self._silence_signaled = False
        if was_signaled and self._on_audio_resumed:
            try:
                await self._on_audio_resumed()
            except Exception as e:
                self._logger.error(f"on_audio_resumed failed: {e}")
