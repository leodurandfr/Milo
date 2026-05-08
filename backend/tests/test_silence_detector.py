# backend/tests/test_silence_detector.py
"""
Unit tests for SilenceDetector.

Validates the hysteresis-protected silence-edge detection that drives
auto-disconnect for sources without a native pause event (Bluetooth,
Mac/ROC). The detector polls CamillaDSP capture levels — tests inject
mocked level responses to drive the state machine.
"""
import asyncio

import pytest
from unittest.mock import AsyncMock

from backend.core.silence_detector import SilenceDetector


def _level_payload(input_peak, available=True):
    """Shape mirrors CamillaDSPService.get_levels() output."""
    return {"available": available, "input_peak": input_peak, "output_peak": [-90.0]}


class _LevelsScript:
    """Drains a list of level payloads and returns the last one when exhausted.

    AsyncMock awaits the value returned by side_effect (when not a coroutine),
    so __call__ stays sync and returns the next payload directly.
    """

    def __init__(self, sequence):
        self._sequence = list(sequence)
        self._calls = 0

    def __call__(self):
        self._calls += 1
        if not self._sequence:
            return _level_payload([-90.0])
        if len(self._sequence) == 1:
            return self._sequence[0]
        return self._sequence.pop(0)


@pytest.fixture
def camilladsp():
    service = AsyncMock()
    service.get_levels = AsyncMock(return_value=_level_payload([-90.0]))
    return service


def _make_detector(camilladsp, **overrides):
    return SilenceDetector(
        camilladsp,
        threshold_dbfs=overrides.get("threshold_dbfs", -50.0),
        idle_seconds=overrides.get("idle_seconds", 0.05),
        poll_interval=overrides.get("poll_interval", 0.01),
    )


class TestStartStop:
    @pytest.mark.asyncio
    async def test_start_creates_task(self, camilladsp):
        detector = _make_detector(camilladsp)
        await detector.start()
        try:
            assert detector._task is not None
            assert not detector._task.done()
        finally:
            await detector.stop()

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self, camilladsp):
        detector = _make_detector(camilladsp)
        await detector.start()
        first_task = detector._task
        await detector.start()
        try:
            assert detector._task is first_task
        finally:
            await detector.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self, camilladsp):
        detector = _make_detector(camilladsp)
        await detector.start()
        await detector.stop()
        assert detector._task is None

    @pytest.mark.asyncio
    async def test_stop_resets_silence_state(self, camilladsp):
        detector = _make_detector(camilladsp)
        detector._silent_since = 1.0
        detector._silence_signaled = True
        await detector.stop()
        assert detector._silent_since is None
        assert detector._silence_signaled is False


class TestSilenceDetection:
    @pytest.mark.asyncio
    async def test_fires_silence_after_idle_seconds(self, camilladsp):
        on_silence = AsyncMock()
        on_resume = AsyncMock()
        camilladsp.get_levels = AsyncMock(return_value=_level_payload([-80.0, -90.0]))

        detector = _make_detector(camilladsp, idle_seconds=0.05, poll_interval=0.01)
        detector.set_callbacks(on_silence_started=on_silence, on_audio_resumed=on_resume)

        await detector.start()
        try:
            await asyncio.sleep(0.15)
            assert on_silence.await_count >= 1
            assert on_resume.await_count == 0
        finally:
            await detector.stop()

    @pytest.mark.asyncio
    async def test_fires_only_once_while_silent(self, camilladsp):
        on_silence = AsyncMock()
        camilladsp.get_levels = AsyncMock(return_value=_level_payload([-80.0]))

        detector = _make_detector(camilladsp, idle_seconds=0.02, poll_interval=0.01)
        detector.set_callbacks(on_silence_started=on_silence)

        await detector.start()
        try:
            await asyncio.sleep(0.15)
        finally:
            await detector.stop()

        assert on_silence.await_count == 1

    @pytest.mark.asyncio
    async def test_brief_dip_does_not_fire(self, camilladsp):
        on_silence = AsyncMock()
        # Loud → quiet → loud — quiet window shorter than idle_seconds
        script = _LevelsScript([
            _level_payload([-10.0]),
            _level_payload([-80.0]),
            _level_payload([-10.0]),
            _level_payload([-10.0]),
        ])
        camilladsp.get_levels = AsyncMock(side_effect=script)

        detector = _make_detector(camilladsp, idle_seconds=0.5, poll_interval=0.01)
        detector.set_callbacks(on_silence_started=on_silence)

        await detector.start()
        try:
            await asyncio.sleep(0.1)
        finally:
            await detector.stop()

        assert on_silence.await_count == 0

    @pytest.mark.asyncio
    async def test_resume_fires_after_silence(self, camilladsp):
        on_silence = AsyncMock()
        on_resume = AsyncMock()
        # Quiet long enough to trigger, then loud.
        script = _LevelsScript([
            _level_payload([-80.0]),
            _level_payload([-80.0]),
            _level_payload([-80.0]),
            _level_payload([-80.0]),
            _level_payload([-80.0]),
            _level_payload([-80.0]),
            _level_payload([-80.0]),
            _level_payload([-80.0]),
            _level_payload([-10.0]),
            _level_payload([-10.0]),
            _level_payload([-10.0]),
        ])
        camilladsp.get_levels = AsyncMock(side_effect=script)

        detector = _make_detector(camilladsp, idle_seconds=0.02, poll_interval=0.01)
        detector.set_callbacks(on_silence_started=on_silence, on_audio_resumed=on_resume)

        await detector.start()
        try:
            await asyncio.sleep(0.2)
        finally:
            await detector.stop()

        assert on_silence.await_count >= 1
        assert on_resume.await_count >= 1

    @pytest.mark.asyncio
    async def test_resume_without_signaled_silence_no_op(self, camilladsp):
        on_silence = AsyncMock()
        on_resume = AsyncMock()
        # Always loud.
        camilladsp.get_levels = AsyncMock(return_value=_level_payload([-10.0]))

        detector = _make_detector(camilladsp, idle_seconds=0.05, poll_interval=0.01)
        detector.set_callbacks(on_silence_started=on_silence, on_audio_resumed=on_resume)

        await detector.start()
        try:
            await asyncio.sleep(0.1)
        finally:
            await detector.stop()

        assert on_silence.await_count == 0
        assert on_resume.await_count == 0


class TestUnavailableLevels:
    @pytest.mark.asyncio
    async def test_camilladsp_unavailable_no_event(self, camilladsp):
        on_silence = AsyncMock()
        camilladsp.get_levels = AsyncMock(return_value=_level_payload(None, available=False))

        detector = _make_detector(camilladsp, idle_seconds=0.02, poll_interval=0.01)
        detector.set_callbacks(on_silence_started=on_silence)

        await detector.start()
        try:
            await asyncio.sleep(0.1)
        finally:
            await detector.stop()

        assert on_silence.await_count == 0

    @pytest.mark.asyncio
    async def test_missing_input_peak_no_event(self, camilladsp):
        on_silence = AsyncMock()
        camilladsp.get_levels = AsyncMock(return_value={"available": True})

        detector = _make_detector(camilladsp, idle_seconds=0.02, poll_interval=0.01)
        detector.set_callbacks(on_silence_started=on_silence)

        await detector.start()
        try:
            await asyncio.sleep(0.1)
        finally:
            await detector.stop()

        assert on_silence.await_count == 0

    @pytest.mark.asyncio
    async def test_get_levels_exception_no_event(self, camilladsp):
        on_silence = AsyncMock()
        camilladsp.get_levels = AsyncMock(side_effect=RuntimeError("boom"))

        detector = _make_detector(camilladsp, idle_seconds=0.02, poll_interval=0.01)
        detector.set_callbacks(on_silence_started=on_silence)

        await detector.start()
        try:
            await asyncio.sleep(0.1)
        finally:
            await detector.stop()

        assert on_silence.await_count == 0


class TestLoopRecovery:
    @pytest.mark.asyncio
    async def test_unhandled_exception_resets_state(self, camilladsp):
        """If _tick raises, the loop must drop its task ref and clear the
        silence window so a stale timer doesn't fire after the detector dies."""
        on_silence = AsyncMock()

        detector = _make_detector(camilladsp, idle_seconds=0.02, poll_interval=0.01)
        detector.set_callbacks(on_silence_started=on_silence)
        # Simulate corruption: pre-arm window, then crash the tick.
        detector._tick = AsyncMock(side_effect=RuntimeError("unexpected"))

        await detector.start()
        await asyncio.sleep(0.05)

        assert detector._task is None or detector._task.done()
        assert detector._silent_since is None
        assert detector._silence_signaled is False


class TestThresholdMath:
    @pytest.mark.asyncio
    async def test_uses_loudest_channel(self, camilladsp):
        on_silence = AsyncMock()
        # Left silent, right loud → loudest is loud → no silence.
        camilladsp.get_levels = AsyncMock(return_value=_level_payload([-90.0, -10.0]))

        detector = _make_detector(camilladsp, idle_seconds=0.02, poll_interval=0.01)
        detector.set_callbacks(on_silence_started=on_silence)

        await detector.start()
        try:
            await asyncio.sleep(0.1)
        finally:
            await detector.stop()

        assert on_silence.await_count == 0
