# backend/sources/qobuz/source.py
"""Qobuz Connect audio source via the qobuz-proxy sidecar.

qobuz-proxy is a reverse-engineered virtual Qobuz Connect device: the Qobuz app
is the controller, qobuz-proxy renders the stream to ALSA (the milo_qobuz PCM).
Milō only displays + plays (Family B, like AirPlay) — playback is driven from
the Qobuz app, so there are no on-device controls. Now-playing metadata
(title/artist/album/artwork) is polled from the proxy's local HTTP API
(GET /api/status); the proxy exposes no push channel and no local control
endpoints. Album art is a Qobuz CDN URL loaded directly by the kiosk — there is
no binary artwork route (unlike AirPlay/DLNA).
"""
from typing import Any, Dict, Optional

from backend.core.audio_source import BaseAudioSource
from backend.core.models.source_metadata import PlaybackMetadata
from backend.sources.qobuz.monitor import QobuzMonitor

# qobuz-proxy local HTTP API (aiohttp, bound 0.0.0.0:8689 by milo-qobuz.service).
QOBUZ_STATUS_URL = "http://127.0.0.1:8689/api/status"
# Our speaker is matched by its ALSA output device, not the slugified id
# ("Milō" -> "mil"): qobuz-proxy hard-couples id = slugify(name).
QOBUZ_AUDIO_DEVICE = "milo_qobuz"
# Static controller label so the source bar renders: the proxy never reports the
# controlling phone's name — it only knows the speaker name ("Milō").
QOBUZ_CLIENT_NAME = "Qobuz"

# qobuz-proxy speaker.status values that mean a session is attached.
_ACTIVE_STATUSES = {"playing", "paused"}


class QobuzSource(BaseAudioSource):
    """Qobuz Connect source (Family B — passive player): external control, rich metadata."""

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        state_machine=None,
        settings_service=None,
        systemd_manager=None,
    ):
        super().__init__(
            source_id="qobuz",
            service_name="milo-qobuz.service",
            state_machine=state_machine,
            systemd_manager=systemd_manager,
            settings_service=settings_service,
            config=config,
        )

        self._status_url = self._config.get("status_url", QOBUZ_STATUS_URL)
        self._audio_device = self._config.get("audio_device", QOBUZ_AUDIO_DEVICE)

        self._monitor: Optional[QobuzMonitor] = None

        # State
        self._metadata: Dict[str, Any] = {}
        self._is_playing = False
        self._device_connected = False

    def _reset_playback_state(self) -> None:
        super()._reset_playback_state()
        self._device_connected = False

    async def _do_start(self) -> bool:
        """Start the qobuz-proxy service and the /api/status poll monitor."""
        try:
            if not await self._start_service_and_wait():
                return False

            self._reset_playback_state()

            self._monitor = QobuzMonitor(
                status_url=self._status_url,
                audio_device=self._audio_device,
                on_status=self._on_status,
            )
            await self._monitor.start()

            self._update_connection_state()
            return True

        except Exception as e:
            self._logger.error(f"Start failed: {e}")
            await self._cleanup()
            return False

    # Family B: playback is controlled from the Qobuz app; qobuz-proxy exposes no
    # local control channel — command() rejects every command as unknown.
    COMMANDS = {}

    async def _on_status(self, speaker: Optional[Dict[str, Any]]) -> None:
        """Map a qobuz-proxy speaker snapshot into connection/playback state.

        playing/paused (with now_playing) → ACTIVE with the current track;
        idle/disconnected/absent → WAITING. Position/duration are not exposed
        over HTTP (they only flow to the Qobuz cloud), so the progress bar stays
        inert — expected for a Family B source.
        """
        status = (speaker or {}).get("status")

        if speaker is not None and status in _ACTIVE_STATUSES:
            now = speaker.get("now_playing") or {}
            self._is_playing = status == "playing"
            self._metadata = {
                "title": now.get("title"),
                "artist": now.get("artist"),
                "album": now.get("album"),
                "album_art_url": now.get("album_art_url"),
                "is_playing": self._is_playing,
                "is_buffering": False,
            }
            self._device_connected = True
        else:
            self._is_playing = False
            self._metadata = {}
            self._device_connected = False

        self._update_connection_state()

    def _update_connection_state(self) -> None:
        """Publish connection/playback state to the shared player.

        Broadcast metadata (WS source/state_changed → system_state.metadata):
        title, artist, album, album_art_url, is_playing, is_buffering (canonical
        PlaybackMetadata) + client_name="Qobuz" (extra, so the source bar shows a
        label — the proxy never reports the controlling device).
        """
        core, extras = PlaybackMetadata.split(self._metadata)
        core.is_playing = self._is_playing
        extras["client_name"] = QOBUZ_CLIENT_NAME
        self.emit_connection_state(self._device_connected, core, extras)

    async def _cleanup(self) -> None:
        """Stop the poll monitor and reset state (service stop handled by _do_stop)."""
        if self._monitor:
            await self._monitor.stop()
            self._monitor = None
        self._reset_playback_state()


__all__ = ["QobuzSource"]
