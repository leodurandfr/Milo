# backend/sources/tidal/source.py
"""Tidal Connect audio source via the tisoc controller socket.

Family C (active player), same shape as Spotify: the phone's Tidal app picks
Milō as a speaker and hands over a queue, the daemon renders it to ALSA, and
Milō both displays the track and drives transport. `controller_socket.py`
replaces Spotify's `websocket.py` as the event feed — a Unix socket instead of
an HTTP WebSocket, everything above it is the same.

Two protocol facts shape this file:

  - There is no status query. The daemon pushes and never answers a "what are
    you playing" question, so `refresh_metadata` stays the base no-op and the
    only truth is the last event received. A controller reconnect therefore
    resets the session rather than re-affirming a track that may be gone.
  - There is no seek. `tidal::media::MediaPlayer::seekTo` exists inside the
    daemon but the controller protocol exposes no command for it, so the
    progress bar is read-only (`:seekable="false"` on the player) and `seek`
    is deliberately absent from COMMANDS.

Album art is a Tidal CDN URL loaded directly by the kiosk — no binary artwork
route, same as Qobuz.
"""
import asyncio
from typing import Any, Dict, Optional

from pydantic import BaseModel

from backend.core.audio_source import BaseAudioSource
from backend.core.models.audio_state import NetworkRequirement
from backend.core.models.source_metadata import PlaybackMetadata
from backend.sources.tidal.controller_socket import TidalControllerSocket

# Where milo-tidal.service is told to put its controller socket
# (--controller-unix-socket-path). Not the SDK's /tmp default: /run/milo is the
# same RuntimeDirectory the mpv sources already share, so the socket is not
# world-writable.
TIDAL_CONTROLLER_SOCKET = "/run/milo/tidal-controller.sock"

# playerState values the daemon reports. IDLE means no track is loaded; the
# other three all mean one is, which is what separates "playing" from "a track
# exists but is paused" — both are ACTIVE for Milō, a paused track still has a
# session to draw.
_STATE_PLAYING = "PLAYING"
_STATE_BUFFERING = "BUFFERING"
_STATE_IDLE = "IDLE"

# The daemon pushes a player status about twice a second. The frontend
# interpolates the playhead locally, so a moved position alone is worth only a
# periodic drift correction — at the same cadence AirPlay ages its own. What
# interpolation cannot guess (play/pause, buffering, a session appearing, a
# track ending) still goes out immediately, through the full state path.
POSITION_BROADCAST_INTERVAL = 10.0



class TidalSource(BaseAudioSource):
    """Tidal Connect source (Family C — active player): UI control, rich metadata."""

    NETWORK_REQUIREMENT = NetworkRequirement.INTERNET

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        state_machine=None,
        settings_service=None,
        systemd_manager=None,
    ):
        super().__init__(
            source_id="tidal",
            service_name="milo-tidal.service",
            state_machine=state_machine,
            systemd_manager=systemd_manager,
            settings_service=settings_service,
            config=config,
        )

        self._socket_path = self._config.get("socket_path", TIDAL_CONTROLLER_SOCKET)
        self._controller: Optional[TidalControllerSocket] = None

        self._metadata: Dict[str, Any] = {}
        self._is_playing = False
        self._device_connected = False
        self._last_progress_broadcast = 0.0

    def _reset_playback_state(self) -> None:
        super()._reset_playback_state()
        self._device_connected = False

    async def _do_start(self) -> bool:
        """Start the daemon, then attach the controller before any phone can.

        Ordering is load-bearing rather than tidy: the daemon advertises itself
        over mDNS as soon as it is up, and a phone session arriving before the
        controller has sent `startService` is rejected AND wedges the daemon's
        SessionManager until it restarts. Attaching inside the start sequence
        keeps that window to the service's own settle time.
        """
        try:
            if not await self._start_service_and_wait():
                return False

            self._reset_playback_state()

            self._controller = TidalControllerSocket(
                socket_path=self._socket_path,
                on_event=self._handle_event,
                logger=self._logger,
            )
            await self._controller.start()

            if not await self._controller.wait_ready():
                self._logger.error(
                    "Tidal Connect daemon never became ready — it would reject "
                    "every phone session"
                )
                await self._cleanup()
                return False

            self._update_connection_state()
            return True

        except Exception as e:
            self._logger.error(f"Start failed: {e}")
            await self._cleanup()
            return False

    async def _do_stop(self) -> bool:
        """Tell the daemon to withdraw the speaker, then stop the unit.

        Best-effort: `stopService` lets the phone see the speaker disappear
        instead of timing out on it, but a daemon that will not answer must not
        block the source switch that is waiting on this.
        """
        try:
            if self._controller and self._controller.connected:
                await self._controller.send("stopService")
        except Exception as e:
            # The notification is a courtesy to the phone; the unit stop below
            # is the actual contract. Nothing the controller does may skip it.
            self._logger.error(f"stopService was not delivered: {e}")

        await self._cleanup()
        return await self._stop_service()

    COMMANDS = {
        "pause": None,
        "resume": None,
        "next": None,
        "prev": None,
    }

    # Milō command -> tisoc command. The two spellings differ on purpose: Milō's
    # vocabulary is canonical across sources (`resume`, `prev`), the SDK's is its
    # own (`play`, `previous`), and mapping here is what keeps the difference
    # from leaking into the API. Dispatch is this table rather than an if-chain,
    # so COMMANDS and COMMAND_MAP must name the same commands — a registered
    # command missing here would KeyError on the first press.
    COMMAND_MAP = {
        "pause": "pause",
        "resume": "play",
        "next": "next",
        "prev": "previous",
    }

    async def _handle_command(self, cmd: str, params: Optional[BaseModel]) -> Dict[str, Any]:
        """Forward a validated command to the daemon under its own spelling."""
        if not self._controller:
            return self.error_response("Tidal controller is not connected")

        sent = await self._controller.send(self.COMMAND_MAP[cmd])
        return self.success_response() if sent else self.error_response(f"'{cmd}' was not delivered")

    # === Event feed ===

    async def _handle_event(self, message: Dict[str, Any]) -> None:
        """Map one tisoc frame onto connection/playback state."""
        command = message.get("command")

        if command == "notifyServiceStateChanged":
            # Only ever sent in answer to the controller's `startService`, which
            # is only ever sent on connect — so this *is* the connect signal.
            # Nothing can be known about a session at that point (the protocol
            # has no status query), so start from no session rather than
            # re-affirming whatever was on screen before the socket dropped.
            self._reset_playback_state()

        elif command == "notifySessionState":
            # 1 (opening) and 2 (established) observed on a live session; 0 is
            # the terminal value the ordering implies, and `releaseResources`
            # below is what the end of a session is actually read from. So only
            # an explicit 0 clears the screen: a frame whose `state` cannot be
            # read is logged and ignored rather than taking the destructive
            # branch — the one arm never seen on the wire.
            state = message.get("state")
            if state == 0:
                self._reset_playback_state()
            elif isinstance(state, int):
                self._device_connected = True
            else:
                self._logger.warning(f"notifySessionState without a readable state: {message}")
                return

        elif command == "releaseResources":
            # The daemon handing the audio device back: the session is over.
            self._reset_playback_state()

        elif command == "notifyMediaChanged":
            self._apply_media(message.get("mediaInfo") or {})

        elif command == "notifyPlayerStatusChanged":
            if not self._apply_player_status(message):
                self._broadcast_progress()
                return

        elif command == "notifyPlaybackError":
            code = message.get("errorCode")
            self._logger.error(f"Tidal playback error (code {code})")
            self.broadcast_error(f"Tidal playback error (code {code})")
            return

        else:
            # setShuffle/setRepeatMode/notifyRequestResult/requestResources —
            # either already answered by the transport or not modelled by Milō.
            return

        self._update_connection_state()

    def _apply_media(self, media_info: Dict[str, Any]) -> None:
        """Replace the current track from a `notifyMediaChanged` payload."""
        metadata = media_info.get("metadata") or {}
        artists = metadata.get("artists") or []

        # `duration` is milliseconds here as in the player status (a 3:57 track
        # arrives as 237000), but the two do not always mean the same span:
        # this one is the track, the status one is what the account is entitled
        # to play. The status frame following a media change therefore wins, and
        # is also the only writer of is_buffering — a media frame that arrived
        # without one behind it would otherwise leave a spinner on a playing
        # track forever.
        self._metadata = {
            "title": metadata.get("title"),
            "artist": ", ".join(artists) or None,
            "album": metadata.get("albumTitle"),
            "album_art_url": self._largest_image(metadata.get("images") or {}),
            "duration": metadata.get("duration"),
        }
        self._device_connected = True

    @staticmethod
    def _largest_image(images: Dict[str, Any]) -> Optional[str]:
        """URL of the widest cover the daemon offers (low/medium/high, 320-1280px).

        Picked by reported width rather than by key so a payload that ships only
        some of the three still yields the best available one.
        """
        candidates = [img for img in images.values() if isinstance(img, dict) and img.get("url")]
        if not candidates:
            return None
        return max(candidates, key=lambda img: img.get("width") or 0)["url"]

    def _apply_player_status(self, status: Dict[str, Any]) -> bool:
        """Apply a `notifyPlayerStatusChanged` payload (state + progress).

        True when the frame changed something the frontend cannot interpolate
        and a full broadcast is owed; False when only the playhead moved.
        """
        player_state = status.get("playerState")
        before = (self._is_playing, self._metadata.get("is_buffering"), self._device_connected)

        self._is_playing = player_state == _STATE_PLAYING
        self._metadata["is_buffering"] = player_state == _STATE_BUFFERING

        # IDLE means no track is loaded. Keep the session — the phone is still
        # attached — but stop claiming a position inside a track that ended.
        if player_state == _STATE_IDLE:
            self._metadata.pop("position", None)
        else:
            self._device_connected = True
            self._metadata["position"] = status.get("progress")
            self._metadata["duration"] = status.get("duration")

        after = (self._is_playing, self._metadata["is_buffering"], self._device_connected)
        return player_state == _STATE_IDLE or before != after

    def _broadcast_progress(self) -> None:
        """Drift-correct the playhead, at most every POSITION_BROADCAST_INTERVAL."""
        position = self._metadata.get("position")
        duration = self._metadata.get("duration")
        if position is None or not duration:
            return

        now = asyncio.get_running_loop().time()
        if now - self._last_progress_broadcast < POSITION_BROADCAST_INTERVAL:
            return

        self._last_progress_broadcast = now
        self.broadcast_position_update(position, duration)

    def _update_connection_state(self) -> None:
        """Publish connection/playback state to the shared player.

        Broadcast metadata (WS source/state_changed → system_state.metadata):
        title, artist, album, album_art_url, position, duration, is_playing,
        is_buffering — all canonical PlaybackMetadata, no extras. Unlike Qobuz
        there is no client_name: with transport controls on screen the player
        draws the transport, not a source bar.
        """
        core, _ = PlaybackMetadata.split(self._metadata)
        core.is_playing = self._is_playing
        self.emit_connection_state(self._device_connected, core)

    async def _cleanup(self) -> None:
        """Drop the controller socket and reset state (unit stop is _do_stop's)."""
        if self._controller:
            await self._controller.stop()
            self._controller = None
        self._reset_playback_state()


__all__ = ["TidalSource"]
