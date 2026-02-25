# backend/features/airplay/source.py
"""
AirPlay 2 audio source using shairport-sync.

Handles AirPlay streaming from Apple devices via shairport-sync.
Metadata (title, artist, album, artwork) flows through the metadata pipe
and is broadcast to the frontend via WebSocket, just like other sources.
"""
import asyncio
import base64
import logging
import os
from typing import Dict, Any, Optional

from backend.core.audio_source import BaseAudioSource, SourceState
from backend.core.events import EventBus
from backend.features.airplay.metadata_reader import MetadataReader

# Sample rate for RTP frame to millisecond conversion
AIRPLAY_SAMPLE_RATE = 44100


class AirPlaySource(BaseAudioSource):

    def __init__(
        self,
        event_bus: EventBus,
        config: Optional[Dict[str, Any]] = None,
        state_machine=None,
        settings_service=None,
        systemd_manager=None
    ):
        super().__init__(
            source_id="airplay",
            service_name="milo-airplay.service",
            event_bus=event_bus,
            state_machine=state_machine,
            systemd_manager=systemd_manager
        )

        config = config or {}
        self._metadata_pipe = config.get("metadata_pipe", "/tmp/shairport-sync-metadata")
        self._settings_service = settings_service

        # Metadata reader
        self._metadata_reader: Optional[MetadataReader] = None

        # State
        self._metadata: Dict[str, Any] = {}
        self._is_playing = False
        self._device_connected = False
        self._client_name: Optional[str] = None

        # Progress tracking (RTP frames)
        self._progress_start = 0
        self._progress_current = 0
        self._progress_end = 0

        # Auto-disconnect
        self.auto_disconnect_enabled = True
        self.pause_disconnect_delay = 10.0
        self._pause_timer: Optional[asyncio.Task] = None

    async def _do_start(self) -> bool:
        """Start shairport-sync service and metadata reader."""
        try:
            if not await self._start_service():
                return False

            await asyncio.sleep(0.5)

            self._device_connected = False
            self._is_playing = False
            self._metadata = {}
            self._client_name = None
            self._cancel_pause_timer()

            await self._ensure_metadata_pipe()

            self._metadata_reader = MetadataReader(
                pipe_path=self._metadata_pipe,
                on_metadata=self._on_metadata_update,
                on_play_state=self._on_play_state,
                on_artwork=self._on_artwork,
                on_progress=self._on_progress,
                on_client_name=self._on_client_name,
            )
            await self._metadata_reader.start()

            self._update_connection_state()
            return True

        except Exception as e:
            self._logger.error(f"Start failed: {e}")
            await self._cleanup()
            return False

    async def _do_stop(self) -> bool:
        """Stop metadata reader and service."""
        try:
            await self._cleanup()
            return await self._stop_service()
        except Exception as e:
            self._logger.error(f"Stop failed: {e}")
            return False

    async def _do_restart(self) -> bool:
        """Restart service with state reset."""
        try:
            self._logger.info("Restarting AirPlay source")

            self._cancel_pause_timer()
            self._device_connected = False
            self._is_playing = False
            self._metadata = {}
            self._client_name = None

            if self._metadata_reader:
                await self._metadata_reader.stop()
                self._metadata_reader = None

            if not await self._restart_service():
                return False

            await asyncio.sleep(0.5)

            await self._ensure_metadata_pipe()
            self._metadata_reader = MetadataReader(
                pipe_path=self._metadata_pipe,
                on_metadata=self._on_metadata_update,
                on_play_state=self._on_play_state,
                on_artwork=self._on_artwork,
                on_progress=self._on_progress,
                on_client_name=self._on_client_name,
            )
            await self._metadata_reader.start()

            self._update_connection_state()
            return True

        except Exception as e:
            self._logger.error(f"Restart failed: {e}")
            return False

    async def _get_status(self) -> Dict[str, Any]:
        """Get AirPlay-specific status."""
        return {
            "device_connected": self._device_connected,
            "is_playing": self._is_playing,
            "metadata": self._metadata,
        }

    async def _handle_command(self, cmd: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle AirPlay-specific commands."""
        if cmd == "restart_service":
            success = await self._do_restart()
            return (
                self.success_response("Service restarted")
                if success else self.error_response("Restart failed")
            )

        # AirPlay 2 does not support remote playback control
        # (shairport-sync AIRPLAY2.md: "Remote control facilities are not implemented")
        return self.error_response(f"Unknown command: {cmd}")

    # === Metadata Callbacks ===

    async def _on_metadata_update(self, metadata: Dict[str, Any]) -> None:
        """Handle track metadata from pipe (title, artist, album)."""
        self._metadata.update({
            "title": metadata.get("title", self._metadata.get("title", "")),
            "artist": metadata.get("artist", self._metadata.get("artist", "")),
            "album": metadata.get("album", self._metadata.get("album", "")),
            "is_playing": self._is_playing,
        })
        # artwork_url is set separately by _on_artwork when PICT data arrives

        self._update_progress_metadata()
        self._device_connected = True
        self._update_connection_state()

    async def _on_play_state(self, state: str) -> None:
        """Handle play state change from pipe reader."""
        if state == "play":
            self._is_playing = True
            self._device_connected = True
            self._cancel_pause_timer()
        elif state == "pause":
            self._is_playing = False
            self._start_pause_timer()
        elif state == "stop":
            self._is_playing = False
            self._device_connected = False
            self._metadata = {}
            self._client_name = None
            self._cancel_pause_timer()

        self._metadata["is_playing"] = self._is_playing
        self._update_connection_state()

    async def _on_artwork(self, data: bytes) -> None:
        """Handle artwork from pipe: encode as base64 data URI in metadata."""
        try:
            b64 = base64.b64encode(data).decode("ascii")
            self._metadata["album_art_url"] = f"data:image/jpeg;base64,{b64}"
            self._update_connection_state()
        except Exception as e:
            self._logger.error(f"Failed to encode artwork: {e}")

    async def _on_client_name(self, name: str) -> None:
        """Handle client name from pipe (X-Apple-Client-Name)."""
        self._client_name = name
        self._device_connected = True
        self._update_connection_state()

    async def _on_progress(self, start: int, current: int, end: int) -> None:
        """Handle progress update from pipe reader (RTP frames at 44100Hz)."""
        self._progress_start = start
        self._progress_current = current
        self._progress_end = end
        self._update_progress_metadata()
        self._update_connection_state()

    # === Helpers ===

    def _update_progress_metadata(self) -> None:
        """Convert RTP frame positions to milliseconds and update metadata."""
        if self._progress_end > self._progress_start:
            duration_frames = self._progress_end - self._progress_start
            position_frames = self._progress_current - self._progress_start

            self._metadata["duration"] = int(duration_frames / AIRPLAY_SAMPLE_RATE * 1000)
            self._metadata["position"] = max(
                0, int(position_frames / AIRPLAY_SAMPLE_RATE * 1000)
            )

    async def _ensure_metadata_pipe(self) -> None:
        """Ensure metadata pipe exists."""
        if not os.path.exists(self._metadata_pipe):
            try:
                os.mkfifo(self._metadata_pipe)
            except FileExistsError:
                pass
            except PermissionError:
                self._logger.warning(
                    f"Cannot create metadata pipe {self._metadata_pipe} "
                    "(will be created by shairport-sync)"
                )

    def _update_connection_state(self) -> None:
        """Update state based on device connection."""
        if self._device_connected:
            self.set_state(SourceState.CONNECTED, {
                **self._metadata,
                "device_connected": True,
                "is_playing": self._is_playing,
                "client_name": self._client_name,
            })
        else:
            self.set_state(SourceState.READY, {
                "device_connected": False,
                "is_playing": False,
            })

    async def _cleanup(self) -> None:
        """Clean up resources."""
        self._cancel_pause_timer()

        if self._metadata_reader:
            await self._metadata_reader.stop()
            self._metadata_reader = None

        self._device_connected = False
        self._is_playing = False
        self._metadata = {}
        self._client_name = None

    # === Auto-Disconnect Timer ===

    def _cancel_pause_timer(self) -> None:
        """Cancel auto-disconnect timer."""
        if self._pause_timer:
            self._pause_timer.cancel()
            self._pause_timer = None

    def _start_pause_timer(self) -> None:
        """Start auto-disconnect timer after pause."""
        if not self.auto_disconnect_enabled:
            return

        self._cancel_pause_timer()

        async def disconnect_after_delay():
            try:
                await asyncio.sleep(self.pause_disconnect_delay)
                self._logger.info(
                    f"Auto-disconnecting after {self.pause_disconnect_delay}s pause"
                )
                self._device_connected = False
                self._is_playing = False
                self._metadata = {}
                self._update_connection_state()
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self._logger.error(f"Auto-disconnect failed: {e}")

        self._pause_timer = asyncio.create_task(disconnect_after_delay())

    # === Public API ===

    @property
    def is_playing(self) -> bool:
        return self._is_playing

    @property
    def device_connected(self) -> bool:
        return self._device_connected

    @property
    def metadata(self) -> Dict[str, Any]:
        return dict(self._metadata)
