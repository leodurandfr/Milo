# backend/tests/test_playback_dispatch.py
"""
Unit tests for PlaybackDispatcher.

Covers:
- The public rename of dispatch_play_pause / dispatch_track (used directly by
  the IR remote controller, in addition to the multi-click path used by
  rotary + BT remote).
- The multi-click resolver still routes to the renamed methods.
- Source-aware command dispatch (no-op when the active source doesn't
  support the requested action).
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.hardware.playback_dispatch import PlaybackDispatcher, MULTI_CLICK_WINDOW
from backend.core.models.audio_state import AudioSource


def _make_state_machine(active: AudioSource, source_instance):
    sm = MagicMock()
    sm.system_state = MagicMock()
    sm.system_state.active_source = active
    sm.get_source = MagicMock(return_value=source_instance)
    return sm


class TestPublicDispatchMethods:
    """The IR controller calls dispatch_play_pause / dispatch_track directly."""

    @pytest.mark.asyncio
    async def test_dispatch_play_pause_spotify(self):
        source = MagicMock()
        source.command = AsyncMock()
        sm = _make_state_machine(AudioSource.SPOTIFY, source)

        dispatcher = PlaybackDispatcher(sm)
        await dispatcher.dispatch_play_pause()

        source.command.assert_awaited_once_with("playpause", {})

    @pytest.mark.asyncio
    async def test_dispatch_play_pause_radio_when_playing(self):
        source = MagicMock()
        source.is_playing = True
        source.command = AsyncMock()
        sm = _make_state_machine(AudioSource.RADIO, source)

        dispatcher = PlaybackDispatcher(sm)
        await dispatcher.dispatch_play_pause()

        source.command.assert_awaited_once_with("stop", {})

    @pytest.mark.asyncio
    async def test_dispatch_play_pause_radio_when_paused(self):
        source = MagicMock()
        source.is_playing = False
        source.command = AsyncMock()
        sm = _make_state_machine(AudioSource.RADIO, source)

        dispatcher = PlaybackDispatcher(sm)
        await dispatcher.dispatch_play_pause()

        source.command.assert_awaited_once_with("resume_playback", {})

    @pytest.mark.asyncio
    async def test_dispatch_play_pause_music_library_when_playing(self):
        source = MagicMock()
        source.is_playing = True
        source.command = AsyncMock()
        sm = _make_state_machine(AudioSource.MUSIC_LIBRARY, source)

        dispatcher = PlaybackDispatcher(sm)
        await dispatcher.dispatch_play_pause()

        source.command.assert_awaited_once_with("pause", {})

    @pytest.mark.asyncio
    async def test_dispatch_play_pause_music_library_when_paused(self):
        source = MagicMock()
        source.is_playing = False
        source.command = AsyncMock()
        sm = _make_state_machine(AudioSource.MUSIC_LIBRARY, source)

        dispatcher = PlaybackDispatcher(sm)
        await dispatcher.dispatch_play_pause()

        source.command.assert_awaited_once_with("resume", {})

    @pytest.mark.asyncio
    async def test_dispatch_track_next_music_library(self):
        source = MagicMock()
        source.command = AsyncMock()
        sm = _make_state_machine(AudioSource.MUSIC_LIBRARY, source)

        dispatcher = PlaybackDispatcher(sm)
        await dispatcher.dispatch_track("next")

        source.command.assert_awaited_once_with("next", {})

    @pytest.mark.asyncio
    async def test_dispatch_play_pause_unsupported_source_is_noop(self):
        # AirPlay does not support play/pause via backend
        source = MagicMock()
        source.command = AsyncMock()
        sm = _make_state_machine(AudioSource.AIRPLAY, source)

        dispatcher = PlaybackDispatcher(sm)
        await dispatcher.dispatch_play_pause()

        source.command.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_track_next_spotify(self):
        source = MagicMock()
        source.command = AsyncMock()
        sm = _make_state_machine(AudioSource.SPOTIFY, source)

        dispatcher = PlaybackDispatcher(sm)
        await dispatcher.dispatch_track("next")

        source.command.assert_awaited_once_with("next", {})

    @pytest.mark.asyncio
    async def test_dispatch_track_unsupported_source_is_noop(self):
        # Radio doesn't support next/prev
        source = MagicMock()
        source.command = AsyncMock()
        sm = _make_state_machine(AudioSource.RADIO, source)

        dispatcher = PlaybackDispatcher(sm)
        await dispatcher.dispatch_track("next")

        source.command.assert_not_called()

class TestMultiClickRouting:
    """The on_click → _resolve_clicks path must call the renamed public methods."""

    @pytest.mark.asyncio
    async def test_single_click_routes_to_dispatch_play_pause(self):
        source = MagicMock()
        source.command = AsyncMock()
        sm = _make_state_machine(AudioSource.SPOTIFY, source)

        dispatcher = PlaybackDispatcher(sm)
        await dispatcher.on_click()
        await asyncio.sleep(MULTI_CLICK_WINDOW + 0.1)

        source.command.assert_awaited_once_with("playpause", {})

    @pytest.mark.asyncio
    async def test_double_click_routes_to_dispatch_track_next(self):
        source = MagicMock()
        source.command = AsyncMock()
        sm = _make_state_machine(AudioSource.SPOTIFY, source)

        dispatcher = PlaybackDispatcher(sm)
        await dispatcher.on_click()
        await dispatcher.on_click()
        await asyncio.sleep(MULTI_CLICK_WINDOW + 0.1)

        source.command.assert_awaited_once_with("next", {})

    @pytest.mark.asyncio
    async def test_triple_click_routes_to_dispatch_track_prev(self):
        source = MagicMock()
        source.command = AsyncMock()
        sm = _make_state_machine(AudioSource.SPOTIFY, source)

        dispatcher = PlaybackDispatcher(sm)
        await dispatcher.on_click()
        await dispatcher.on_click()
        await dispatcher.on_click()
        await asyncio.sleep(MULTI_CLICK_WINDOW + 0.1)

        source.command.assert_awaited_once_with("prev", {})

    @pytest.mark.asyncio
    async def test_cleanup_clears_pending_clicks(self):
        source = MagicMock()
        source.command = AsyncMock()
        sm = _make_state_machine(AudioSource.SPOTIFY, source)

        dispatcher = PlaybackDispatcher(sm)
        await dispatcher.on_click()
        await dispatcher.cleanup()
        await asyncio.sleep(MULTI_CLICK_WINDOW + 0.1)

        source.command.assert_not_called()


class TestCleanupDrains:
    """A resolver already spawned must not outlive the controller's teardown.

    Cancelling the TimerHandle only stops a window that has not expired yet.
    Once it has, the resolve task is in `_bg` and holds a reference to the
    source: without the drain it dispatches a command after the hardware
    controller released its devices, and its failure is logged against a
    controller nobody owns any more.
    """

    @pytest.mark.asyncio
    async def test_cleanup_cancels_an_in_flight_resolve(self):
        blocked = asyncio.Event()
        never = asyncio.Event()
        cancelled = False

        async def slow_command(*_args):
            nonlocal cancelled
            blocked.set()
            try:
                await never.wait()
            except asyncio.CancelledError:
                cancelled = True
                raise

        source = MagicMock()
        source.command = AsyncMock(side_effect=slow_command)
        sm = _make_state_machine(AudioSource.SPOTIFY, source)

        dispatcher = PlaybackDispatcher(sm)
        await dispatcher.on_click()
        # The window has expired: the resolver is spawned and inside the command.
        await asyncio.wait_for(blocked.wait(), timeout=MULTI_CLICK_WINDOW + 1)

        await asyncio.wait_for(dispatcher.cleanup(), timeout=1)

        assert cancelled, "cleanup() left the resolve task running"


class TestASourceThatIsNotThere:
    """The three arms at 0 %: a registered source with no live instance, and a
    command that raises.

    `get_source` answers None while a source is mid-restart. Both dispatch
    methods run inside a hardware monitor task — the IR runtime loop and the BT
    per-device loop — so an escaping exception ends the task and the remote
    goes dead until it reconnects, with one line in the journal.
    """

    @pytest.mark.asyncio
    async def test_play_pause_on_a_source_with_no_instance_is_a_no_op(self, caplog):
        """Without the guard the None is subscripted and the AttributeError
        lands in the `except` below — same silence to the user, but an ERROR in
        the journal on every press during a source restart."""
        dispatcher = PlaybackDispatcher(
            _make_state_machine(AudioSource.SPOTIFY, None)
        )

        await dispatcher.dispatch_play_pause()

        assert caplog.text == ""

    @pytest.mark.asyncio
    async def test_track_nav_on_a_source_with_no_instance_is_a_no_op(self, caplog):
        dispatcher = PlaybackDispatcher(
            _make_state_machine(AudioSource.SPOTIFY, None)
        )

        await dispatcher.dispatch_track("next")

        assert caplog.text == ""

    @pytest.mark.asyncio
    async def test_a_command_that_raises_does_not_escape_play_pause(self, caplog):
        source = MagicMock()
        source.command = AsyncMock(side_effect=RuntimeError("ipc socket gone"))
        dispatcher = PlaybackDispatcher(
            _make_state_machine(AudioSource.SPOTIFY, source)
        )

        await dispatcher.dispatch_play_pause()

        assert "Error dispatching play/pause" in caplog.text

    @pytest.mark.asyncio
    async def test_a_command_that_raises_does_not_escape_track_nav(self, caplog):
        source = MagicMock()
        source.command = AsyncMock(side_effect=RuntimeError("ipc socket gone"))
        dispatcher = PlaybackDispatcher(
            _make_state_machine(AudioSource.SPOTIFY, source)
        )

        await dispatcher.dispatch_track("next")

        assert "Error dispatching next" in caplog.text

    @pytest.mark.asyncio
    async def test_a_resolver_that_raises_does_not_kill_the_click_window(self, caplog):
        """`_resolve_clicks` runs from a timer into the BackgroundTaskSet; an
        exception there would surface as a task-set error and, worse, leave
        `_click_count` at whatever it was — the next single press would resolve
        as a double."""
        sm = _make_state_machine(AudioSource.SPOTIFY, MagicMock())
        sm.get_source = MagicMock(side_effect=RuntimeError("state machine gone"))
        dispatcher = PlaybackDispatcher(sm)
        dispatcher._click_count = 1

        await dispatcher._resolve_clicks()

        assert dispatcher._click_count == 0
        assert "Error resolving clicks" in caplog.text
