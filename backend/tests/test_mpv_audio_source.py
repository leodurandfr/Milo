# backend/tests/test_mpv_audio_source.py
"""
Unit tests for MpvAudioSource's shared plumbing.

Two paths live in the base class. The session-driven one (Radio, Podcast,
Music Library follow mpv's events, and the idle timer follows the session's
PAUSED phase) is covered by tests/test_mpv_sessions.py and the golden
scenarios. What is left here is the attach every mpv source shares, and the
polled path the CD still runs until it migrates: the monitor loop and its
disconnect fallback, the pause-edge helper (`_handle_pause_change`) and the
`_on_auto_stop` dispatch to `_auto_stop_action` that keeps `active_source`
intact. Those are driven through `PolledSource`, a minimal subclass shaped the
way the CD uses them, and observed through what its hooks did.
"""
import asyncio
import logging

import pytest
from unittest.mock import AsyncMock, Mock, patch

from backend.core import audio_source
from backend.core.models.audio_state import AudioSource, SourceState
from backend.shared.mpv import MpvController
from backend.shared.mpv_audio_source import MpvAudioSource
from backend.sources.podcast.source import PodcastSource
from backend.sources.radio.source import RadioSource
from backend.tests.golden.harness import AsyncioProxy, VirtualClock


class PolledSource(MpvAudioSource):
    """An mpv source on the polled path, as the CD is: it reports pause edges
    itself and implements `_auto_stop_action`, which only records the stop."""

    def __init__(self, **kwargs):
        super().__init__(source_id="cd", service_name="milo-probe.service", **kwargs)
        self.auto_stops = 0

    async def _do_start(self) -> bool:
        return True

    async def _auto_stop_action(self) -> None:
        self.auto_stops += 1


@pytest.fixture
def radio_source():
    source = RadioSource({"mpv_socket": "/tmp/test-radio-ipc.sock"})
    source.auto_stop_enabled = True
    source.auto_stop_delay = 999.0
    return source


@pytest.fixture
def podcast_source():
    source = PodcastSource({"mpv_socket": "/tmp/test-podcast-ipc.sock"})
    source.auto_stop_enabled = True
    source.auto_stop_delay = 999.0
    return source


@pytest.fixture
def polled():
    return PolledSource()


@pytest.fixture
def clock(monkeypatch):
    """The pause timer's clock (it sleeps in core/audio_source.py)."""
    clock = VirtualClock()
    monkeypatch.setattr(audio_source, "asyncio", AsyncioProxy(clock.sleep))
    return clock


@pytest.fixture
async def timed(clock):
    """A polled source whose auto-stop delay comes from settings, as on a unit."""
    settings = Mock()
    settings.get_setting = AsyncMock(
        side_effect=lambda key, *a, **k: 30 if key == "audio.auto_stop_delay" else None
    )
    source = PolledSource(settings_service=settings)
    await source.reload_auto_stop_config()
    yield source
    await source.shutdown()


async def _run_monitor(source, passes: int = 1, before_pass=None) -> None:
    """Run N iterations of the shared monitor loop, no wall clock.

    The loop's 1 s sleep is replaced by a counter that lets `passes` iterations
    through and cancels the next, so the real loop body runs against the source's
    state instead of being re-implemented here. `before_pass(i)` runs just before
    iteration i (0-based) — that is where the outside world changes under the
    loop, e.g. mpv dying between two passes.
    """
    n = {"i": 0}

    async def _sleep(_delay):
        if n["i"] >= passes:
            raise asyncio.CancelledError
        if before_pass:
            before_pass(n["i"])
        n["i"] += 1

    with patch("backend.shared.mpv_audio_source.asyncio.sleep", _sleep):
        await source._monitor_loop()


def _live_mpv() -> Mock:
    mpv = Mock()
    mpv.is_connected = True
    return mpv


class TestAttach:
    """`_attach_mpv` is the four sources' only way onto the IPC socket."""

    @pytest.mark.asyncio
    async def test_a_failed_start_writes_one_line_and_not_two(
        self, podcast_source, caplog
    ):
        """The source used to add a line of its own to a failure already logged.

        connect() reports what it waited for and how long; "Failed to connect to
        MPV IPC" next to it says only that it happened, in the second of the two
        spellings the four sources had drifted into. Whoever reads a boot's
        errors.log gets one record per failure, or the count means nothing.
        """
        podcast_source._service_manager = Mock(start=AsyncMock(return_value=True))

        with patch.object(MpvController, "connect", AsyncMock(return_value=False)), \
             caplog.at_level(logging.DEBUG):
            assert await podcast_source._do_start() is False

        from_the_source = [r for r in caplog.records if r.name.startswith("source.")]
        assert from_the_source == []

    @pytest.mark.asyncio
    async def test_the_attach_answers_what_the_link_answered(self, radio_source):
        """No interpretation between connect()'s verdict and the caller's.

        The source is left holding the controller either way: a failed start is
        stopped by the state machine, and the CD pre-start path reuses the same
        object when the real start follows.
        """
        with patch.object(MpvController, "connect", AsyncMock(return_value=True)):
            assert await radio_source._attach_mpv() is True
        assert radio_source._mpv.ipc_socket_path == radio_source._mpv_socket


class TestMpvDisconnect:
    """mpv dying mid-playback must publish READY, and publish it first.

    The disconnect hooks clear the source's own fields and nothing else, while
    the SourceError that follows carries full_state: with no publish, the client
    gets "Audio stream disconnected" alongside a state saying the station is
    still playing, and IDLE_STATES keeps the 12 h sweep from ever repairing it.
    """

    @staticmethod
    def _arm(source, order: list[str]) -> None:
        source.state_machine = Mock()
        source.state_machine.update_source_state = AsyncMock(
            side_effect=lambda *a, **kw: order.append("publish")
        )
        source._bg = Mock()
        source._bg.spawn = Mock(
            side_effect=lambda coro, **kw: (order.append("error"), coro.close())
        )
        source._state = SourceState.ACTIVE
        source._mpv = _live_mpv()

    @pytest.mark.asyncio
    async def test_disconnect_publishes_ready_before_the_error_banner(self, polled):
        order: list[str] = []
        self._arm(polled, order)
        polled._is_playing = True
        polled._on_monitor_tick = AsyncMock()

        # pass 0: link up. pass 1: mpv gone.
        await _run_monitor(
            polled, passes=2,
            before_pass=lambda i: setattr(polled, "_mpv", None) if i == 1 else None,
        )

        assert order == ["publish", "error"]
        published = polled.state_machine.update_source_state.await_args.args
        assert published[:2] == (AudioSource.CD, SourceState.READY)
        assert published[2]["is_playing"] is False
        # The source's own copy must agree — routing reads it back on failure.
        assert polled.state is SourceState.READY

    @pytest.mark.asyncio
    async def test_publishes_even_though_the_tick_already_cleared_is_playing(self, polled):
        """The tick sees the dying mpv one pass before is_connected flips.

        A tick that reads the playing flag off mpv (Radio's did, `_is_playing =
        await self._mpv.is_playing()`) gets False from a dead socket; gating the
        fallback on that flag meant the station card stayed ACTIVE for good. Observed on the unit 2026-08-07 with
        `systemctl stop milo-radio`: state ACTIVE, station_name still set, 18 s
        later unchanged.
        """
        order: list[str] = []
        self._arm(polled, order)
        polled._is_playing = True

        async def tick_against_dead_mpv():
            polled._is_playing = False  # what is_playing() answers now

        polled._on_monitor_tick = tick_against_dead_mpv

        await _run_monitor(
            polled, passes=2,
            before_pass=lambda i: setattr(polled, "_mpv", None) if i == 1 else None,
        )

        assert order == ["publish", "error"]
        assert polled.state is SourceState.READY

    @pytest.mark.asyncio
    async def test_publishes_when_the_link_drops_without_the_controller_being_nulled(
        self, polled
    ):
        """The branch production actually takes — green here by design.

        Every other test in this class simulates the death with `_mpv = None`,
        which no crash path ever produces: only _cleanup() nulls the controller,
        and a crash reaches the loop through disconnect() alone. So the half of
        the condition that carries every real mpv death had no coverage at all,
        which is exactly the half the link-ownership change makes load-bearing.
        """
        order: list[str] = []
        self._arm(polled, order)
        polled._is_playing = True
        polled._on_monitor_tick = AsyncMock()

        def drop_the_link(i):
            if i == 1:
                polled._mpv.is_connected = False

        await _run_monitor(polled, passes=2, before_pass=drop_the_link)

        assert order == ["publish", "error"]
        assert polled.state is SourceState.READY

    @pytest.mark.asyncio
    async def test_no_publish_while_idle(self, polled):
        """A disconnected mpv on a source that never went ACTIVE is normal idle."""
        polled.state_machine = Mock()
        polled.state_machine.update_source_state = AsyncMock()
        polled._mpv = None
        polled._is_playing = False

        await _run_monitor(polled, passes=2)

        polled.state_machine.update_source_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_publish_when_the_source_swaps_mpv_itself(self, polled):
        """CD tears mpv down on every seek with _is_playing still set.

        That window is indistinguishable from a crash by the loop alone, so
        _mpv_swap_in_progress() is what tells them apart; without it a seek would
        emit "Audio stream disconnected" and drop the disc to READY.
        """
        order: list[str] = []
        self._arm(polled, order)
        polled._is_playing = True
        polled._on_monitor_tick = AsyncMock()
        polled._mpv_swap_in_progress = lambda: True

        await _run_monitor(
            polled, passes=2,
            before_pass=lambda i: setattr(polled, "_mpv", None) if i == 1 else None,
        )

        assert order == []
        polled.state_machine.update_source_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_publishes_once_not_every_second(self, polled):
        """The link stays down; the banner must not repeat every tick."""
        order: list[str] = []
        self._arm(polled, order)
        polled._is_playing = True
        polled._on_monitor_tick = AsyncMock()

        await _run_monitor(
            polled, passes=5,
            before_pass=lambda i: setattr(polled, "_mpv", None) if i == 1 else None,
        )

        assert order == ["publish", "error"]

    @pytest.mark.asyncio
    async def test_cd_reports_its_restart_window(self):
        """The CD override is what the shared loop reads — pin the wiring."""
        from backend.sources.cd.source import CdSource

        cd = CdSource({"mpv_socket": "/tmp/test-cd-ipc.sock"})
        assert cd._mpv_swap_in_progress() is False
        cd._restarting = True
        assert cd._mpv_swap_in_progress() is True


class TestMonitorSurvivesABadPass:
    """One raising pass must cost one pass, not the rest of the session.

    With the guard around the whole loop instead of the pass, a single
    exception out of a source's tick retired the monitor for good: no
    auto-advance, no position sync, and — worse — no disconnect banner and no
    idle publish when mpv died afterwards, since that detection lives in the
    same loop.
    """

    @pytest.mark.asyncio
    async def test_a_raising_tick_does_not_end_the_monitor(self, polled):
        polled._mpv = _live_mpv()
        ticks: list[int] = []

        async def tick():
            ticks.append(len(ticks))
            if len(ticks) == 1:
                raise RuntimeError("transient mpv read")

        polled._on_monitor_tick = tick

        await _run_monitor(polled, passes=3)

        assert ticks == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_a_cancel_landing_inside_the_tick_still_ends_the_monitor(
        self, polled
    ):
        """The per-pass guard must stay `except Exception`, never BaseException.

        `_stop_monitor()` cancels the task wherever it happens to be, and that
        is usually the 1 s sleep but can be any await inside the tick. Widened
        to BaseException the loop would swallow its own teardown and keep
        polling an mpv the source has already dropped.
        """
        polled._mpv = _live_mpv()
        ticks: list[int] = []

        async def tick():
            ticks.append(len(ticks))
            raise asyncio.CancelledError

        polled._on_monitor_tick = tick

        await asyncio.wait_for(_run_monitor(polled, passes=5), timeout=5)

        assert ticks == [0]


class TestPauseChange:
    """The CD reports pause edges itself; an edge arms or disarms the auto-stop."""

    async def test_a_pause_edge_auto_stops_after_the_delay(self, timed, clock):
        """The disc paused and left alone releases the drive, so the screen can
        sleep; without the edge arming the timer the CD plays paused forever."""
        timed._handle_pause_change(True)
        await clock.advance(timed.auto_stop_delay)

        assert timed.auto_stops == 1

    async def test_a_resume_edge_before_the_delay_keeps_playing(self, timed, clock):
        """A resume must disarm it, or the music stops mid-track one delay
        after the last pause."""
        timed._handle_pause_change(True)
        await clock.advance(timed.auto_stop_delay / 2)
        timed._handle_pause_change(False)
        await clock.advance(timed.auto_stop_delay)

        assert timed.auto_stops == 0

    async def test_no_auto_stop_when_the_delay_is_zero(self, clock):
        """0 in Settings means off: a paused disc stays loaded."""
        settings = Mock()
        settings.get_setting = AsyncMock(return_value=0)
        source = PolledSource(settings_service=settings)
        await source.reload_auto_stop_config()
        try:
            source._handle_pause_change(True)
            await clock.advance(3600)

            assert source.auto_stops == 0
        finally:
            await source.shutdown()

    async def test_a_repeated_pause_does_not_push_the_deadline(self, timed, clock):
        """The CD reports a pause from several paths (the command, the preload);
        a same-state report is not an edge, so it must not re-arm the timer and
        restart the delay — the stop comes one delay after the first pause."""
        timed._handle_pause_change(True)
        await clock.advance(timed.auto_stop_delay / 2)
        timed._handle_pause_change(True)
        await clock.advance(timed.auto_stop_delay / 2)

        assert timed.auto_stops == 1


class TestAutoStopAction:
    """_on_auto_stop dispatches to per-source _auto_stop_action with a CAS guard.

    Regression guard: the prior behavior called transition_to_source(NONE),
    which kicked the user back to the home screen instead of stopping
    in-source. The new behavior keeps active_source intact.
    """

    @staticmethod
    def _wire(source, active: AudioSource) -> Mock:
        source.state_machine = Mock()
        source.state_machine.system_state = Mock(active_source=active)
        source.state_machine.transition_to_source = AsyncMock(return_value=True)
        source.state_machine.update_source_state = AsyncMock()
        return source.state_machine

    async def test_the_expiry_stops_in_source(self, timed, clock):
        """When the source is still active, the source's own stop runs and the
        selection is left alone."""
        machine = self._wire(timed, AudioSource.CD)

        timed._handle_pause_change(True)
        await clock.advance(timed.auto_stop_delay)

        assert timed.auto_stops == 1
        # Critical: must NOT call transition_to_source — that was the bug.
        machine.transition_to_source.assert_not_called()

    async def test_cas_guard_aborts_when_source_switched_away(self, polled):
        """If the user switched to another source mid-timer, do nothing."""
        self._wire(polled, AudioSource.RADIO)

        await polled._on_auto_stop()

        assert polled.auto_stops == 0


class TestReloadAutoStop:
    """reload_auto_stop_config refreshes the delay on mpv sources."""

    @pytest.mark.asyncio
    async def test_reload_disables_when_zero(self, radio_source):
        radio_source._settings_service = Mock()
        radio_source._settings_service.get_setting = AsyncMock(return_value=0)

        result = await radio_source.reload_auto_stop_config()

        assert result is True
        assert radio_source.auto_stop_enabled is False

    @pytest.mark.asyncio
    async def test_reload_updates_delay(self, podcast_source):
        podcast_source._settings_service = Mock()
        podcast_source._settings_service.get_setting = AsyncMock(return_value=45.0)

        result = await podcast_source.reload_auto_stop_config()

        assert result is True
        assert podcast_source.auto_stop_enabled is True
        assert podcast_source.auto_stop_delay == 45.0
