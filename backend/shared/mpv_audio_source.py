# backend/shared/mpv_audio_source.py
"""
MpvAudioSource - Intermediate base class for mpv-based audio sources.

Provides shared _do_restart() and _monitor_loop() skeletons with hooks
for source-specific behavior. Used by RadioSource and PodcastSource.

Hooks:
    _before_restart_save(): Called after stopping monitor, before reset (e.g., save progress)
    _after_restart_restore(): Called after monitor restart, before state update
    _on_mpv_disconnect(): Called on unexpected mpv disconnect during playback
    _on_monitor_tick(): Called each monitor cycle when mpv is connected
"""
import asyncio
from typing import Optional

from backend.core.audio_source import BaseAudioSource
from backend.shared.decorators import handle_errors
from backend.shared.mpv import MpvController


class MpvAudioSource(BaseAudioSource):
    """
    Base class for audio sources that use mpv for playback.

    Extends BaseAudioSource with:
    - mpv controller and socket management
    - Shared _do_restart() with pre/post hooks
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

    def _reset_playback_state(self) -> None:
        super()._reset_playback_state()
        self._is_buffering = False
        self._position_ticks = 0

    def _position_sync_due(self) -> bool:
        """Return True every POSITION_SYNC_INTERVAL ticks, then reset."""
        self._position_ticks += 1
        if self._position_ticks >= self.POSITION_SYNC_INTERVAL:
            self._position_ticks = 0
            return True
        return False

    # === Restart skeleton ===

    @handle_errors(default=False)
    async def _do_restart(self) -> bool:
        """Restart service with state reset. Subclasses customize via hooks."""
        self._logger.info(f"Restarting {self.source_id} source")

        self._stop_monitor()
        await self._before_restart_save()
        self._reset_playback_state()

        if self._mpv:
            await self._mpv.disconnect()
            self._mpv = None

        if not await self._restart_service_and_wait():
            return False

        self._mpv = MpvController(ipc_socket_path=self._mpv_socket)
        if not await self._mpv.connect():
            return False

        self._start_monitor()
        await self._after_restart_restore()
        self._update_connection_state()

        return True

    async def _before_restart_save(self) -> None:
        """Hook: called after stopping monitor, before resetting state.

        Override to stop extra tasks and save state before restart.
        """

    async def _after_restart_restore(self) -> None:
        """Hook: called after monitor restart, before updating connection state.

        Override to restore state after restart.
        """

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

    async def _on_mpv_disconnect(self) -> None:
        """Hook: called on unexpected mpv disconnect during playback.

        Override to save state and clear source-specific fields.
        """

    async def _on_monitor_tick(self) -> None:
        """Hook: called each monitor cycle when mpv is connected.

        Override to check playback state, update position, etc.
        """
