"""
Tests for VolumeAccumulator, the batching layer the rotary encoder, the IR
remote and the BT remote all push detents through.

What breaks when these fail: a volume change applied after the controller was
torn down (an untracked task nothing can cancel), or a detent silently dropped
during a normal drain.
"""
import asyncio

import pytest

from backend.hardware.volume_accumulator import VolumeAccumulator


class _RecordingVolumeService:
    """Stands in for VolumeService — the boundary the accumulator drives."""

    def __init__(self, delay: float = 0.0):
        self.calls = []
        self._delay = delay

    async def adjust_volume_db(self, delta):
        if self._delay:
            await asyncio.sleep(self._delay)
        self.calls.append(delta)


async def test_rapid_deltas_are_batched_and_all_applied():
    """Every dB fed in must come back out, batched but never lost."""
    service = _RecordingVolumeService()
    acc = VolumeAccumulator(service)

    for _ in range(10):
        acc.accumulate(2.0)
    await asyncio.sleep(0.15)

    assert service.calls, "the processor never ran"
    assert sum(service.calls) == pytest.approx(20.0)
    assert len(service.calls) < 10, "deltas were not batched at all"


async def test_cleanup_applies_nothing_further():
    """
    cleanup() must leave no task behind.

    The processor used to respawn itself from its finally block, which
    swallowed the CancelledError and produced a task cleanup() had already
    stopped tracking (_processor_task = None) — a volume change landing on the
    hardware after teardown, with nothing able to cancel it.
    """
    service = _RecordingVolumeService(delay=0.05)
    acc = VolumeAccumulator(service)

    acc.accumulate(3.0)
    await asyncio.sleep(0.01)  # first adjust_volume_db is in flight
    acc.accumulate(3.0)        # queued behind it, so the drain is not empty

    await acc.cleanup()
    applied_at_cleanup = len(service.calls)

    await asyncio.sleep(0.15)
    assert len(service.calls) == applied_at_cleanup
    assert acc._processor_task is None
    assert not acc._processor_running


async def test_a_delta_after_cleanup_spawns_nothing():
    """A late detent from a controller being torn down must not restart the loop."""
    service = _RecordingVolumeService()
    acc = VolumeAccumulator(service)
    await acc.cleanup()

    acc.accumulate(5.0)
    await asyncio.sleep(0.1)

    assert service.calls == []
    assert acc._processor_task is None


async def test_a_refused_adjustment_does_not_strand_the_detents_behind_it():
    """One refused push must not end the drain task.

    `accumulate` only spawns a processor when none is running, so a detent
    that arrives *while* a push is in flight has no other way in. If the
    refusal kills the task, that delta sits in the accumulator until the next
    detent — the knob skips, and nothing is logged above the one error.
    """
    service = _RecordingVolumeService()
    failures = [RuntimeError("CamillaDSP is down")]
    acc = VolumeAccumulator(service)

    async def flaky(delta_db):
        service.calls.append(delta_db)
        if failures:
            acc.accumulate(3.0)  # a detent turned while the push was in flight
            raise failures.pop()

    service.adjust_volume_db = flaky

    acc.accumulate(2.0)
    await asyncio.sleep(0.15)

    assert service.calls == [2.0, 3.0], "the detent behind the refusal was applied"
    assert acc._processor_running is False
    await acc.cleanup()
