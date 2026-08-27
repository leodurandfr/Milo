"""The EQ view's level meters: a 10 Hz broadcast with no unsubscribe call.

`LevelsMonitor` replaced the modal's HTTP polling. Every open EQ view POSTs a
keepalive; while one is fresh the monitor samples at 10 Hz and broadcasts a
single shared `equalizer`/`levels` event. Nothing ever calls "stop" — closing the
modal, killing the tab and losing the network all look identical from here, so
the expiring deadline IS the shutdown, and the payload comparison IS the
flood control.

The whole file was at 36.7 %: not one method body had run. What that leaves
unmeasured is a task that either never starts or never stops, on the appliance
whose multiroom sync is already the first thing CPU starvation breaks.
"""
import asyncio
import logging
from unittest.mock import AsyncMock, Mock

import pytest

from backend.core.equalizer.levels_monitor import LevelsMonitor, SILENT_PEAK


class StopTheMonitor(BaseException):
    """Ends a bounded double.

    Derived from `BaseException` because `_run`'s loop body catches `Exception`
    and logs it, which would turn a runaway double into a silent busy loop
    instead of a failure.
    """


@pytest.fixture
def state_machine():
    sm = Mock()
    sm.broadcast = AsyncMock()
    return sm


@pytest.fixture
def router():
    return Mock()


@pytest.fixture
def camilladsp():
    svc = Mock()
    svc.get_levels = AsyncMock(return_value={"available": True, "output_peak": [-20.0, -21.0]})
    return svc


@pytest.fixture
def monitor(state_machine, router, camilladsp, monkeypatch):
    """A monitor whose two production delays are collapsed.

    SAMPLE_INTERVAL is 0.1 s and KEEPALIVE_TTL is 15 s. Both are reduced rather
    than removed: a TTL of exactly zero would make the loop exit before its first
    sample, which is not a state the appliance can be in, and would hide the
    dedup and the error arm behind a body that never runs. Neither value is ever
    asserted on — what is asserted is that the deadline governs the loop at all.
    """
    monkeypatch.setattr(LevelsMonitor, "SAMPLE_INTERVAL", 0)
    monkeypatch.setattr(LevelsMonitor, "KEEPALIVE_TTL", 0.05)
    return LevelsMonitor(state_machine, router, camilladsp)


def events(state_machine):
    return [call.args[0] for call in state_machine.broadcast.await_args_list]


class TestKeepalive:
    """Arming, re-arming, and the one task shared by every viewer."""

    async def test_the_first_keepalive_starts_the_sampling_task(self, monitor):
        """Nothing else starts it — the POST is the only entry point.

        Without the spawn the meters sit at zero for as long as the modal is
        open and no error appears anywhere.
        """
        monitor.keepalive([])
        try:
            assert monitor._task is not None
        finally:
            await monitor.cleanup()

    async def test_a_second_viewer_does_not_start_a_second_task(self, monitor):
        """Two open EQ views share one sampler and one broadcast.

        A task per viewer multiplies both the DSP round-trips and the WebSocket
        traffic by the number of tabs, at 10 Hz each.
        """
        monitor.keepalive([])
        first = monitor._task
        monitor.keepalive([])
        try:
            assert monitor._task is first
        finally:
            await monitor.cleanup()

    async def test_a_keepalive_after_the_task_finished_starts_a_fresh_one(self, monitor):
        """Re-opening the modal must resurrect the meters.

        The task ends by itself when the deadline passes; a check on `_task is
        None` alone would find a finished task and never restart, so the view
        would come back dead until the backend restarted.
        """
        monitor.keepalive([])
        first = monitor._task
        await asyncio.wait_for(first, timeout=2)

        monitor.keepalive([])
        try:
            assert monitor._task is not first
            assert not monitor._task.done()
        finally:
            await monitor.cleanup()

    async def test_the_last_keepalive_decides_which_clients_are_read(self, monitor):
        """Two viewers can be looking at two different zones.

        Last-one-wins is the documented rule; accumulating instead would average
        a satellite into a meter the viewer thinks is local.
        """
        monitor.keepalive(["aa:bb"])
        try:
            monitor.keepalive(["cc:dd", "ee:ff"])
            assert monitor._client_ids == ["cc:dd", "ee:ff"]
        finally:
            await monitor.cleanup()

    async def test_the_deadline_is_pushed_out_by_each_keepalive(self, monitor):
        """The TTL runs from the LAST POST — the modal re-arms well inside it.

        A keepalive that re-armed the client list without moving the deadline
        would let the sampler die under a modal that is still open, and the
        meters would freeze with no way back short of closing and reopening.
        """
        loop_now = asyncio.get_running_loop().time()
        monitor._deadline = loop_now - 100

        monitor.keepalive([])
        try:
            assert monitor._deadline >= loop_now + monitor.KEEPALIVE_TTL
        finally:
            await monitor.cleanup()


class TestRunLoop:
    """The sampler itself: what it emits, what it refuses to re-emit, and when it stops."""

    async def test_the_loop_stops_on_its_own_once_the_keepalive_expires(self, monitor):
        """There is no unsubscribe call. This is the entire shutdown path.

        A loop that outlived its deadline would sample the DSP at 10 Hz forever
        after the last tab closed — on this appliance, sustained CPU is what
        desynchronises the snapcast clients.

        Counted rather than timed: waiting `n` seconds for the task to end
        cannot tell a deadline that is respected from one that is merely a
        second late, and a wall-clock bound wide enough to be stable is also
        wide enough to miss the defect. Zero samples past the deadline is the
        statement; the ceiling only stops a runaway from spinning the machine.
        """
        samples = {"n": 0}

        async def _sample():
            samples["n"] += 1
            if samples["n"] > 50:
                raise StopTheMonitor("the expired deadline did not end the loop")
            return {"available": True, "output_peak": [-80.0, -80.0]}

        monitor._sample = _sample
        monitor._deadline = asyncio.get_running_loop().time()

        await asyncio.wait_for(monitor._run(), timeout=5)

        assert samples["n"] == 0

    async def test_a_changed_reading_is_broadcast(self, monitor, state_machine, camilladsp):
        monitor._deadline = asyncio.get_running_loop().time() + 3600
        camilladsp.get_levels = AsyncMock(side_effect=[
            {"available": True, "output_peak": [-20.0, -21.0]},
            {"available": True, "output_peak": [-10.0, -11.0]},
            StopTheMonitor(),
        ])

        with pytest.raises(StopTheMonitor):
            await monitor._run()

        emitted = events(state_machine)
        assert [e.output_peak for e in emitted] == [[-20.0, -21.0], [-10.0, -11.0]]
        assert emitted[0].CATEGORY == "equalizer"
        assert emitted[0].TYPE == "levels"

    async def test_an_unchanged_reading_is_not_broadcast_again(
        self, monitor, state_machine, camilladsp
    ):
        """This comparison is the flood control, and silence is the common case.

        A paused stream reads the same silent floor ten times a second; without
        the dedup that is ten WebSocket frames per second, per connected client,
        for as long as the modal stays open on a track nobody is playing.
        """
        monitor._deadline = asyncio.get_running_loop().time() + 3600
        camilladsp.get_levels = AsyncMock(side_effect=[
            {"available": True, "output_peak": [-80.0, -80.0]},
            {"available": True, "output_peak": [-80.0, -80.0]},
            {"available": True, "output_peak": [-80.0, -80.0]},
            StopTheMonitor(),
        ])

        with pytest.raises(StopTheMonitor):
            await monitor._run()

        assert len(events(state_machine)) == 1

    async def test_a_sampling_error_does_not_kill_the_sampler(
        self, monitor, state_machine, camilladsp, caplog
    ):
        """A DSP hiccup must cost one frame, not the meters.

        The task is spawned once per modal opening; if the body dies the meters
        freeze at their last value and look plausible — worse than going silent.
        """
        monitor._deadline = asyncio.get_running_loop().time() + 3600
        camilladsp.get_levels = AsyncMock(side_effect=[
            RuntimeError("daemon busy"),
            {"available": True, "output_peak": [-30.0, -30.0]},
            StopTheMonitor(),
        ])

        with caplog.at_level(logging.ERROR):
            with pytest.raises(StopTheMonitor):
                await monitor._run()

        assert "Levels monitor sampling error" in caplog.text
        assert [e.output_peak for e in events(state_machine)] == [[-30.0, -30.0]]


class TestSampleLocal:
    """No client ids means the local DAC — the direct-mode meter."""

    async def test_the_local_reading_comes_straight_from_camilladsp(self, monitor, camilladsp):
        camilladsp.get_levels = AsyncMock(return_value={
            "available": True, "output_peak": [-12.0, -13.0]
        })

        assert await monitor._sample() == {"available": True, "output_peak": [-12.0, -13.0]}

    async def test_an_unavailable_dsp_reads_as_the_silent_floor(self, monitor, camilladsp):
        """The meters must fall to the floor, not hold their last position.

        A frozen meter over a disconnected daemon reads as "signal present" and
        is the exact opposite of the truth.
        """
        camilladsp.get_levels = AsyncMock(return_value={"available": False})

        assert await monitor._sample() == {
            "available": False, "output_peak": list(SILENT_PEAK)
        }

    async def test_a_reading_with_no_peak_field_falls_back_to_the_floor(
        self, monitor, camilladsp
    ):
        """`get_levels` is `@handle_errors(default={"available": False})`, but a
        partial answer from the daemon is a shape it cannot filter."""
        camilladsp.get_levels = AsyncMock(return_value={"available": True})

        assert await monitor._sample() == {
            "available": True, "output_peak": list(SILENT_PEAK)
        }


class TestSampleClients:
    """Client ids mean the average across the selected satellites."""

    async def test_two_clients_are_averaged_channel_by_channel(self, monitor, router):
        """Per channel, not across them: averaging L into R would collapse a
        stereo meter into two identical bars and hide a dead channel."""
        router.get_levels = AsyncMock(side_effect=[
            {"available": True, "output_peak": [-20.0, -40.0]},
            {"available": True, "output_peak": [-30.0, -50.0]},
        ])
        monitor._client_ids = ["aa:bb", "cc:dd"]

        assert await monitor._sample() == {
            "available": True, "output_peak": [-25.0, -45.0]
        }

    async def test_an_offline_satellite_is_dropped_from_the_average(self, monitor, router):
        """One unreachable client must not drag the whole meter to the floor.

        A zone of four speakers with one asleep is the normal state of this
        installation; counting the missing one would report the room 6 dB quieter
        than it is.
        """
        router.get_levels = AsyncMock(side_effect=[
            {"available": True, "output_peak": [-20.0, -20.0]},
            Exception("satellite unreachable"),
        ])
        monitor._client_ids = ["aa:bb", "cc:dd"]

        assert await monitor._sample() == {
            "available": True, "output_peak": [-20.0, -20.0]
        }

    async def test_a_client_answering_a_single_channel_is_dropped(self, monitor, router):
        """The average indexes `[0]` and `[1]`; a one-element list would raise
        inside the sampler and cost the frame for every other client too."""
        router.get_levels = AsyncMock(side_effect=[
            {"available": True, "output_peak": [-20.0]},
            {"available": True, "output_peak": [-30.0, -30.0]},
        ])
        monitor._client_ids = ["aa:bb", "cc:dd"]

        assert await monitor._sample() == {
            "available": True, "output_peak": [-30.0, -30.0]
        }

    async def test_a_zone_with_nothing_reachable_reads_as_the_floor(self, monitor, router):
        router.get_levels = AsyncMock(side_effect=[Exception("gone"), Exception("gone")])
        monitor._client_ids = ["aa:bb", "cc:dd"]

        assert await monitor._sample() == {
            "available": False, "output_peak": list(SILENT_PEAK)
        }

    async def test_an_unreachable_client_is_logged_at_debug_only(self, monitor, router, caplog):
        """An asleep satellite is expected, not a fault: at warning it would
        reach the `WebSocketLogHandler` banner ten times a second."""
        router.get_levels = AsyncMock(side_effect=Exception("satellite unreachable"))

        with caplog.at_level(logging.DEBUG):
            assert await monitor._client_levels("aa:bb") is None

        assert "Levels unavailable for aa:bb" in caplog.text
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


class TestCleanup:
    """Teardown — `main.py` lists this monitor in the shutdown table."""

    async def test_cleanup_cancels_a_running_sampler(self, monitor):
        monitor.keepalive([])
        monitor._deadline = asyncio.get_running_loop().time() + 3600
        task = monitor._task

        await monitor.cleanup()

        assert task.done()
        assert monitor._task is None

    async def test_cleanup_is_safe_when_nothing_was_ever_started(self, monitor):
        """The modal may never have been opened; shutdown runs regardless."""
        await monitor.cleanup()

        assert monitor._task is None
