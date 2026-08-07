# backend/tests/test_mpv_audio_source.py
"""
Unit tests for MpvAudioSource auto-stop on mpv pause.

The base class provides a single edge-tracking helper
(`_handle_pause_change`); each mpv source decides when to call it (from
its monitor tick or from explicit user commands like CD play/pause).
This file covers the helper, the `_on_auto_stop` dispatch that keeps
`active_source` intact (regression guard against the prior
`transition_to_source(NONE)` behavior), and the timer self-cancel
regression guard.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, Mock, patch

from backend.core.models.audio_state import AudioSource, SourceState
from backend.sources.podcast.source import PodcastSource
from backend.sources.radio.source import RadioSource


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
    async def test_disconnect_publishes_ready_before_the_error_banner(self, radio_source):
        order: list[str] = []
        self._arm(radio_source, order)
        radio_source._is_playing = True
        radio_source._on_monitor_tick = AsyncMock()

        # pass 0: link up. pass 1: mpv gone.
        await _run_monitor(
            radio_source, passes=2,
            before_pass=lambda i: setattr(radio_source, "_mpv", None) if i == 1 else None,
        )

        assert order == ["publish", "error"]
        published = radio_source.state_machine.update_source_state.await_args.args
        assert published[:2] == (AudioSource.RADIO, SourceState.READY)
        assert published[2]["is_playing"] is False
        # The source's own copy must agree — routing reads it back on failure.
        assert radio_source.state is SourceState.READY

    @pytest.mark.asyncio
    async def test_publishes_even_though_the_tick_already_cleared_is_playing(self, radio_source):
        """The tick sees the dying mpv one pass before is_connected flips.

        Radio's tick assigns `_is_playing = await self._mpv.is_playing()`, which a
        dead socket answers False; gating the fallback on that flag meant the
        station card stayed ACTIVE for good. Observed on the unit 2026-08-07 with
        `systemctl stop milo-radio`: state ACTIVE, station_name still set, 18 s
        later unchanged.
        """
        order: list[str] = []
        self._arm(radio_source, order)
        radio_source._is_playing = True

        async def tick_against_dead_mpv():
            radio_source._is_playing = False  # what is_playing() answers now

        radio_source._on_monitor_tick = tick_against_dead_mpv

        await _run_monitor(
            radio_source, passes=2,
            before_pass=lambda i: setattr(radio_source, "_mpv", None) if i == 1 else None,
        )

        assert order == ["publish", "error"]
        assert radio_source.state is SourceState.READY

    @pytest.mark.asyncio
    async def test_publishes_when_the_link_drops_without_the_controller_being_nulled(
        self, radio_source
    ):
        """The branch production actually takes — green here by design.

        Every other test in this class simulates the death with `_mpv = None`,
        which no crash path ever produces: only _cleanup() nulls the controller,
        and a crash reaches the loop through disconnect() alone. So the half of
        the condition that carries every real mpv death had no coverage at all,
        which is exactly the half the link-ownership change makes load-bearing.
        """
        order: list[str] = []
        self._arm(radio_source, order)
        radio_source._is_playing = True
        radio_source._on_monitor_tick = AsyncMock()

        def drop_the_link(i):
            if i == 1:
                radio_source._mpv.is_connected = False

        await _run_monitor(radio_source, passes=2, before_pass=drop_the_link)

        assert order == ["publish", "error"]
        assert radio_source.state is SourceState.READY

    @pytest.mark.asyncio
    async def test_no_publish_while_idle(self, radio_source):
        """A disconnected mpv on a source that never went ACTIVE is normal idle."""
        radio_source.state_machine = Mock()
        radio_source.state_machine.update_source_state = AsyncMock()
        radio_source._mpv = None
        radio_source._is_playing = False

        await _run_monitor(radio_source, passes=2)

        radio_source.state_machine.update_source_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_publish_when_the_source_swaps_mpv_itself(self, radio_source):
        """CD tears mpv down on every seek with _is_playing still set.

        That window is indistinguishable from a crash by the loop alone, so
        _mpv_swap_in_progress() is what tells them apart; without it a seek would
        emit "Audio stream disconnected" and drop the disc to READY.
        """
        order: list[str] = []
        self._arm(radio_source, order)
        radio_source._is_playing = True
        radio_source._on_monitor_tick = AsyncMock()
        radio_source._mpv_swap_in_progress = lambda: True

        await _run_monitor(
            radio_source, passes=2,
            before_pass=lambda i: setattr(radio_source, "_mpv", None) if i == 1 else None,
        )

        assert order == []
        radio_source.state_machine.update_source_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_publishes_once_not_every_second(self, radio_source):
        """The link stays down; the banner must not repeat every tick."""
        order: list[str] = []
        self._arm(radio_source, order)
        radio_source._is_playing = True
        radio_source._on_monitor_tick = AsyncMock()

        await _run_monitor(
            radio_source, passes=5,
            before_pass=lambda i: setattr(radio_source, "_mpv", None) if i == 1 else None,
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


class TestPauseChange:
    """Edge-tracking arms/cancels the auto-stop timer."""

    @pytest.mark.asyncio
    async def test_arms_timer_on_pause_edge(self, radio_source):
        radio_source._handle_pause_change(True)

        assert radio_source._was_paused is True
        assert radio_source._pause_timer is not None
        assert not radio_source._pause_timer.done()
        radio_source._cancel_pause_timer()

    @pytest.mark.asyncio
    async def test_cancels_timer_on_resume_edge(self, radio_source):
        radio_source._was_paused = True
        radio_source._pause_timer = asyncio.create_task(asyncio.sleep(999))

        radio_source._handle_pause_change(False)

        assert radio_source._was_paused is False
        assert radio_source._pause_timer is None

    def test_no_op_when_disabled(self, radio_source):
        radio_source.auto_stop_enabled = False

        radio_source._handle_pause_change(True)

        assert radio_source._pause_timer is None

    def test_no_edge_no_action(self, podcast_source):
        """Same state on consecutive calls does nothing."""
        podcast_source._was_paused = False

        podcast_source._handle_pause_change(False)

        assert podcast_source._was_paused is False
        assert podcast_source._pause_timer is None


class TestAutoStopAction:
    """_on_auto_stop dispatches to per-source _auto_stop_action with a CAS guard.

    Regression guard: the prior behavior called transition_to_source(NONE),
    which kicked the user back to the home screen instead of stopping
    in-source. The new behavior keeps active_source intact.
    """

    @pytest.mark.asyncio
    async def test_dispatches_to_auto_stop_action_in_source(self, podcast_source):
        """When the source is still active, delegate to _auto_stop_action."""
        podcast_source.state_machine = Mock()
        podcast_source.state_machine.system_state = Mock()
        podcast_source.state_machine.system_state.active_source = AudioSource.PODCAST
        podcast_source.state_machine.transition_to_source = AsyncMock(return_value=True)
        podcast_source._auto_stop_action = AsyncMock(return_value=None)

        await podcast_source._on_auto_stop()

        podcast_source._auto_stop_action.assert_awaited_once()
        # Critical: must NOT call transition_to_source — that was the bug.
        podcast_source.state_machine.transition_to_source.assert_not_called()

    @pytest.mark.asyncio
    async def test_cas_guard_aborts_when_source_switched_away(self, podcast_source):
        """If the user switched to another source mid-timer, do nothing."""
        podcast_source.state_machine = Mock()
        podcast_source.state_machine.system_state = Mock()
        podcast_source.state_machine.system_state.active_source = AudioSource.RADIO
        podcast_source._auto_stop_action = AsyncMock(return_value=None)

        await podcast_source._on_auto_stop()

        podcast_source._auto_stop_action.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatches_without_state_machine(self, radio_source):
        """When no state_machine is wired (test scaffold), still call action."""
        radio_source.state_machine = None
        radio_source._auto_stop_action = AsyncMock(return_value=None)

        await radio_source._on_auto_stop()

        radio_source._auto_stop_action.assert_awaited_once()


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


class TestSelfCancelSafety:
    """The pause timer must not cancel itself once it commits to stopping.

    Regression guard: _on_auto_stop typically calls stop() which calls
    _cancel_pause_timer(). If the running timer task were still tracked, the
    cancel would inject CancelledError mid-stop and abort cleanup.
    """

    @pytest.mark.asyncio
    async def test_timer_detaches_before_running_callback(self, radio_source):
        radio_source.auto_stop_delay = 0.01

        callback_observed_timer = []

        async def fake_stop():
            # By the time the callback runs, the timer ref must be detached
            # so nested _cancel_pause_timer() calls become no-ops.
            callback_observed_timer.append(radio_source._pause_timer)

        radio_source._on_auto_stop = fake_stop
        radio_source._start_pause_timer()
        # Wait for the timer to fire and the callback to record state.
        await asyncio.sleep(0.1)

        assert callback_observed_timer == [None]
