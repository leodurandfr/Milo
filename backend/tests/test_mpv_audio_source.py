# backend/tests/test_mpv_audio_source.py
"""
Unit tests for MpvAudioSource auto-disconnect on mpv pause.

The base class provides a single edge-tracking helper
(`_handle_pause_change`); each mpv source decides when to call it (from
its monitor tick or from explicit user commands like CD play/pause).
This file covers the helper, the `_on_auto_disconnect` override that
returns the system to NONE, and the timer self-cancel regression guard.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, Mock

from backend.core.models.audio_state import AudioSource
from backend.sources.podcast.source import PodcastSource
from backend.sources.radio.source import RadioSource


@pytest.fixture
def radio_source():
    source = RadioSource({"mpv_socket": "/tmp/test-radio-ipc.sock"})
    source.auto_disconnect_enabled = True
    source.pause_disconnect_delay = 999.0
    return source


@pytest.fixture
def podcast_source():
    source = PodcastSource({"mpv_socket": "/tmp/test-podcast-ipc.sock"})
    source.auto_disconnect_enabled = True
    source.pause_disconnect_delay = 999.0
    return source


class TestPauseChange:
    """Edge-tracking arms/cancels the auto-disconnect timer."""

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
        radio_source.auto_disconnect_enabled = False

        radio_source._handle_pause_change(True)

        assert radio_source._pause_timer is None

    def test_no_edge_no_action(self, podcast_source):
        """Same state on consecutive calls does nothing."""
        podcast_source._was_paused = False

        podcast_source._handle_pause_change(False)

        assert podcast_source._was_paused is False
        assert podcast_source._pause_timer is None


class TestAutoDisconnectAction:
    """_on_auto_disconnect transitions to NONE with a CAS guard."""

    @pytest.mark.asyncio
    async def test_transitions_to_none_with_expected_source(self, podcast_source):
        """Default mpv override hands off to state_machine.transition_to_source(NONE)."""
        podcast_source.state_machine = Mock()
        podcast_source.state_machine.transition_to_source = AsyncMock(return_value=True)

        await podcast_source._on_auto_disconnect()

        podcast_source.state_machine.transition_to_source.assert_awaited_once_with(
            AudioSource.NONE, expected_source=AudioSource.PODCAST
        )

    @pytest.mark.asyncio
    async def test_falls_back_to_stop_without_state_machine(self, radio_source):
        """When no state_machine is wired, fall back to stop()."""
        radio_source.state_machine = None
        radio_source.stop = AsyncMock(return_value=True)

        await radio_source._on_auto_disconnect()

        radio_source.stop.assert_awaited_once()


class TestReloadAutoDisconnect:
    """reload_auto_disconnect_config refreshes the delay on mpv sources."""

    @pytest.mark.asyncio
    async def test_reload_disables_when_zero(self, radio_source):
        radio_source._settings_service = Mock()
        radio_source._settings_service.get_setting = AsyncMock(return_value=0)

        result = await radio_source.reload_auto_disconnect_config()

        assert result is True
        assert radio_source.auto_disconnect_enabled is False

    @pytest.mark.asyncio
    async def test_reload_updates_delay(self, podcast_source):
        podcast_source._settings_service = Mock()
        podcast_source._settings_service.get_setting = AsyncMock(return_value=45.0)

        result = await podcast_source.reload_auto_disconnect_config()

        assert result is True
        assert podcast_source.auto_disconnect_enabled is True
        assert podcast_source.pause_disconnect_delay == 45.0


class TestSelfCancelSafety:
    """The pause timer must not cancel itself once it commits to disconnecting.

    Regression guard: _on_auto_disconnect typically calls stop() which calls
    _cancel_pause_timer(). If the running timer task were still tracked, the
    cancel would inject CancelledError mid-disconnect and abort cleanup.
    """

    @pytest.mark.asyncio
    async def test_timer_detaches_before_running_callback(self, radio_source):
        radio_source.pause_disconnect_delay = 0.01

        callback_observed_timer = []

        async def fake_disconnect():
            # By the time the callback runs, the timer ref must be detached
            # so nested _cancel_pause_timer() calls become no-ops.
            callback_observed_timer.append(radio_source._pause_timer)

        radio_source._on_auto_disconnect = fake_disconnect
        radio_source._start_pause_timer()
        # Wait for the timer to fire and the callback to record state.
        await asyncio.sleep(0.1)

        assert callback_observed_timer == [None]
