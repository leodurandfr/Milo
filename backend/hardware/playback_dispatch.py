# backend/hardware/playback_dispatch.py
"""
Centralized playback dispatch for hardware controllers.

Provides multi-click detection (1=play/pause, 2=next, 3=prev) and
source-aware command dispatch. Used by both the GPIO rotary encoder
and the Bluetooth HID remote controller.
"""
import asyncio
import logging
from typing import Optional

from backend.core.models.audio_state import AudioSource
from backend.shared.background import BackgroundTaskSet

logger = logging.getLogger(__name__)

MULTI_CLICK_WINDOW = 0.4  # 400ms window for multi-click grouping

# Sources that support play/pause toggle
_PLAY_PAUSE_SOURCES = {
    AudioSource.SPOTIFY,
    AudioSource.RADIO,
    AudioSource.PODCAST,
    AudioSource.CD,
}

# Sources that support next/prev track navigation
_TRACK_NAV_SOURCES = {
    AudioSource.SPOTIFY,
    AudioSource.CD,
}


class PlaybackDispatcher:
    """Multi-click detection and playback command dispatch.

    Each hardware controller (GPIO rotary, BT remote) creates its own
    instance. Call ``on_click()`` on every button press — the dispatcher
    accumulates clicks within a 400ms window, then resolves:
        1 click  → play/pause
        2 clicks → next track
        3+ clicks → previous track
    """

    def __init__(self, state_machine):
        self._state_machine = state_machine
        self._click_count = 0
        self._click_timer: Optional[asyncio.TimerHandle] = None
        self._bg = BackgroundTaskSet(logger, "playback_dispatch")

    async def on_click(self):
        """Register a click and (re)start the multi-click window timer."""
        self._click_count += 1

        if self._click_timer:
            self._click_timer.cancel()

        loop = asyncio.get_running_loop()
        self._click_timer = loop.call_later(
            MULTI_CLICK_WINDOW,
            lambda: self._bg.spawn(self._resolve_clicks(), label="resolve_clicks"),
        )

    def cancel(self):
        """Cancel any pending multi-click timer."""
        if self._click_timer:
            self._click_timer.cancel()
            self._click_timer = None
        self._click_count = 0

    async def _resolve_clicks(self):
        """Resolve accumulated clicks after the multi-click window expires."""
        try:
            count = self._click_count
            self._click_count = 0
            self._click_timer = None

            if count == 1:
                await self.dispatch_play_pause()
            elif count == 2:
                await self.dispatch_track("next")
            elif count >= 3:
                await self.dispatch_track("prev")
        except Exception as e:
            logger.error("Error resolving clicks: %s", e)

    async def dispatch_play_pause(self):
        """Dispatch play/pause to the active audio source."""
        active_source = self._state_machine.system_state.active_source
        if active_source not in _PLAY_PAUSE_SOURCES:
            return

        source_instance = self._state_machine.get_source(active_source)
        if not source_instance:
            return

        try:
            if active_source == AudioSource.SPOTIFY:
                await source_instance.command("playpause", {})
            elif active_source == AudioSource.RADIO:
                if source_instance._is_playing:
                    await source_instance.command("stop_playback", {})
                else:
                    await source_instance.command("resume_playback", {})
            elif active_source in (AudioSource.PODCAST, AudioSource.CD):
                if source_instance._is_playing:
                    await source_instance.command("pause", {})
                else:
                    await source_instance.command("resume", {})
        except Exception as e:
            logger.error("Error dispatching play/pause to %s: %s", active_source.value, e)

    async def dispatch_track(self, direction: str):
        """Dispatch next/prev track command to the active source."""
        active_source = self._state_machine.system_state.active_source
        if active_source not in _TRACK_NAV_SOURCES:
            return

        source_instance = self._state_machine.get_source(active_source)
        if not source_instance:
            return

        try:
            await source_instance.command(direction, {})
        except Exception as e:
            logger.error("Error dispatching %s to %s: %s", direction, active_source.value, e)
