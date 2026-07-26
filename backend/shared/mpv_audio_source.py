# backend/shared/mpv_audio_source.py
"""
MpvAudioSource - Intermediate base class for mpv-based audio sources.

Provides a shared _monitor_loop() skeleton with hooks for source-specific
behavior. Used by RadioSource, PodcastSource, CdSource, MusicLibrarySource.

Hooks:
    _on_mpv_disconnect(): Called on unexpected mpv disconnect during playback
    _on_monitor_tick(): Called each monitor cycle when mpv is connected
"""
import asyncio
from typing import Optional

from backend.core.audio_source import BaseAudioSource
from backend.shared.mpv import MpvController


class MpvAudioSource(BaseAudioSource):
    """
    Base class for audio sources that use mpv for playback.

    Extends BaseAudioSource with:
    - mpv controller and socket management
    - Shared _monitor_loop() with disconnect/tick hooks
    - Common _is_buffering state
    """

    # Interval (in ticks) between position-only broadcasts.
    # Frontend interpolates locally, so this is just drift correction.
    POSITION_SYNC_INTERVAL = 30

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._mpv_socket = self._config.get(
            "mpv_socket", f"/run/milo/{self.source_id}-ipc.sock"
        )
        self._mpv: Optional[MpvController] = None
        self._is_buffering = False
        self._position_ticks: int = 0

        # Auto-stop on mpv pause (effective enable controlled by
        # the global delay: 0 means disabled, see _load_auto_stop_config).
        self.auto_stop_enabled = True
        # Last observed pause state, used by _handle_pause_change to debounce
        # repeated same-state calls from a subclass's monitor tick.
        self._was_paused: bool = False

    def _reset_playback_state(self) -> None:
        super()._reset_playback_state()
        self._is_buffering = False
        self._position_ticks = 0
        self._was_paused = False
        self._cancel_pause_timer()

    def _position_sync_due(self) -> bool:
        """Return True every POSITION_SYNC_INTERVAL ticks, then reset."""
        self._position_ticks += 1
        if self._position_ticks >= self.POSITION_SYNC_INTERVAL:
            self._position_ticks = 0
            return True
        return False

    # === Monitor skeleton ===

    async def _monitor_loop(self) -> None:
        """Monitor mpv connection and playback state."""
        try:
            while True:
                await asyncio.sleep(1.0)

                if not self._mpv or not self._mpv.is_connected:
                    if self._is_playing or self._is_buffering:
                        self._logger.error("mpv disconnected unexpectedly during playback")
                        await self._on_mpv_disconnect()
                        self.broadcast_error("Audio stream disconnected")
                    continue

                await self._on_monitor_tick()

        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._logger.error(f"Monitor error: {e}")

    # === Auto-stop on pause ===

    def _handle_pause_change(self, is_paused: bool) -> None:
        """Arm or cancel the auto-stop timer on a pause-state edge.

        Called by subclasses with whatever pause signal they already track:
        an mpv property they polled in their tick (radio, podcast) or an
        explicit user command (CD play/pause/resume/stop). Edge-tracking via
        `_was_paused` keeps repeated same-state calls cheap.
        """
        if not self.auto_stop_enabled:
            return
        if is_paused == self._was_paused:
            return
        self._was_paused = is_paused
        if is_paused:
            self._start_pause_timer()
        else:
            self._cancel_pause_timer()

    async def _on_auto_stop(self) -> None:
        """Stop playback in-source after pause timeout.

        Keeps active_source unchanged (source_state → WAITING). The
        AudioPlayer hides while the user's source-tab stays open; the 12h
        INACTIVITY_TIMEOUT in AudioStateMachine handles the final source
        close.

        Uses a CAS guard so a user who switched away between the timer
        firing and the method body running doesn't trigger a stop on the
        freshly-activated source.

        Subclasses implement `_auto_stop_action()` to perform their
        source-specific stop sequence (clear current item, stop mpv,
        broadcast WAITING).
        """
        if (
            self.state_machine
            and self.state_machine.system_state.active_source != self.source
        ):
            return
        await self._auto_stop_action()

    async def _auto_stop_action(self) -> None:
        """Source-specific in-source stop. Override in subclass."""
        raise NotImplementedError

    async def _on_mpv_disconnect(self) -> None:
        """Hook: called on unexpected mpv disconnect during playback.

        Override to save state and clear source-specific fields.
        """

    async def _on_monitor_tick(self) -> None:
        """Hook: called each monitor cycle when mpv is connected.

        Override to check playback state, update position, etc.
        """
