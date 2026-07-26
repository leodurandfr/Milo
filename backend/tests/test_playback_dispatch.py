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
    async def test_cancel_clears_pending_clicks(self):
        source = MagicMock()
        source.command = AsyncMock()
        sm = _make_state_machine(AudioSource.SPOTIFY, source)

        dispatcher = PlaybackDispatcher(sm)
        await dispatcher.on_click()
        dispatcher.cancel()
        await asyncio.sleep(MULTI_CLICK_WINDOW + 0.1)

        source.command.assert_not_called()
