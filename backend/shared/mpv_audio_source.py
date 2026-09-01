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
from typing import Any, Dict, Optional

from backend.core.audio_source import BaseAudioSource
from backend.core.models.audio_state import SourceState
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

    async def _attach_mpv(self) -> bool:
        """Build this source's mpv controller and open its IPC link.

        The one attach for the four mpv sources, across five call sites (CD does
        it twice: once for playback, once for its pre-start warm-up). It replaces
        four copies of the same three lines, whose failure report had drifted
        into two spellings of the same sentence at two different levels — and
        which said nothing `connect()` had not already said.

        Silent on failure on purpose: connect() logs what it waited for and how
        long, and `start()` turns this False into the source's single report. A
        line here would be the second one in the journal for one event.

        The caller starts the unit itself, and the gap it leaves before calling
        this is free — mpv publishes its socket some time after exec (0.269s on
        this unit warm, 1.081s with its 51 MB of libraries evicted, past 7s when
        something else is reading the same card), so work done in between is
        time not spent waiting. Radio loads its station data there.
        """
        self._mpv = MpvController(ipc_socket_path=self._mpv_socket)
        return await self._mpv.connect()

    def mpv_refused(self, action: str) -> Dict[str, Any]:
        """Error response for a transport command mpv did not take.

        MpvController answers False (and logs at debug) whenever the IPC link is
        down, so the bool is the only channel this failure has. Return this
        *before* flipping any playback flag: the flip is what makes the UI draw
        a play button over audio that is still running.
        """
        self._logger.error(f"mpv did not take '{action}' (IPC link down?)")
        return self.error_response(f"mpv did not take: {action}")

    # === Monitor skeleton ===

    async def _monitor_loop(self) -> None:
        """Monitor mpv connection and playback state."""
        mpv_was_up = False
        try:
            while True:
                await asyncio.sleep(1.0)

                # Per-pass, not around the loop: one raising hook used to end
                # the monitor for the rest of the session — no disconnect
                # banner, no idle publish, nothing polling again. CancelledError
                # is a BaseException, so it still reaches the exit below.
                try:
                    if not self._mpv or not self._mpv.is_connected:
                        # Latched here rather than read off _is_playing: the tick
                        # below queries the dying mpv one pass earlier and clears
                        # that flag itself (radio does `_is_playing = await
                        # is_playing()`), so by the time is_connected flips, the
                        # flag the fallback used to gate on is already False and
                        # the source stays ACTIVE with a frozen card forever.
                        dropped, mpv_was_up = mpv_was_up, False
                        if (
                            dropped
                            and self._state == SourceState.ACTIVE
                            and not self._mpv_swap_in_progress()
                        ):
                            self._logger.error("mpv disconnected unexpectedly during playback")
                            await self._on_mpv_disconnect()
                            # Before the banner: SourceError carries full_state,
                            # and the hook above only cleared the source's own
                            # fields.
                            await self._publish_idle()
                            self.broadcast_error("Audio stream disconnected")
                        continue

                    mpv_was_up = True
                    await self._on_monitor_tick()
                except Exception as e:
                    self._logger.error(f"Monitor error: {e}")

        except asyncio.CancelledError:
            pass

    def _mpv_swap_in_progress(self) -> bool:
        """True while the source is deliberately tearing mpv down to start another.

        Such a window looks exactly like a crash from the loop — mpv gone with
        the source still ACTIVE — so a source that swaps mpv under itself (CD,
        on every seek and prev-restart) must say so.
        """
        return False

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

        Keeps active_source unchanged (source_state → READY). The
        AudioPlayer hides while the user's source-tab stays open; the 12h
        INACTIVITY_TIMEOUT in AudioStateMachine handles the final source
        close.

        Uses a CAS guard so a user who switched away between the timer
        firing and the method body running doesn't trigger a stop on the
        freshly-activated source.

        Subclasses implement `_auto_stop_action()` to perform their
        source-specific stop sequence (clear current item, stop mpv,
        broadcast READY).
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
