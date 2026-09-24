# backend/sources/tidal/source.py
"""Tidal Connect audio source via the tisoc controller socket.

Family C (active player), same shape as Spotify: the phone's Tidal app picks
Milō as a speaker and hands over a queue, the daemon renders it to ALSA, and
Milō both displays the track and drives transport. `controller_socket.py`
replaces Spotify's `websocket.py` as the event feed — a Unix socket instead of
an HTTP WebSocket.

The session belongs to the daemon, and the source follows it through
`reconcile()` (docs: source architecture, "reconcile"). What was measured on
the unit (2026-09-24, a Mac's Tidal app, strace on the controller socket):

  - A session opens with `notifySessionState` 1 then 2 and names no track
    until its first `notifyMediaChanged`, 0.25-0.6 s later: the Milō session
    opens at that first track (E14). Every track's media comes twice, the
    second between two BUFFERING frames.
  - The phase is the player state the daemon pushes about twice a second:
    PLAYING, PAUSED, BUFFERING (loading) and IDLE, which is a skip's first
    millisecond — or nothing left to play, a pause as far as the idle timeout
    is concerned. A sender leaving is `releaseResources` + `notifySessionState 0`.
  - There is no end-of-session command: `interrupt` only notifies, `stop`
    pauses at 0, `stopService` withdraws nothing. A restart of the daemon is
    what makes the sender let go (the speaker is back at once), so it is the
    idle timeout's REQUEST_END.
  - There is no status query and no seek: `tidal::media::MediaPlayer::seekTo`
    exists inside the daemon but the controller protocol exposes no command for
    it, so the progress bar is read-only (`:seekable="false"` on the player)
    and `seek` is deliberately absent from COMMANDS.

Album art is a Tidal CDN URL loaded directly by the kiosk — no binary artwork
route, same as Qobuz.
"""
import asyncio
from backend.core.models.ws_events import SourceErrorReason
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from pydantic import BaseModel

from backend.core.audio_source import BaseAudioSource
from backend.core.models.audio_state import NetworkRequirement
from backend.core.models.session import (
    CommandScope, DaemonSnapshot, EndReason, IdlePolicy, Phase, ResumePolicy, ReroutePolicy,
    Session,
)
from backend.core.models.source_metadata import PlaybackMetadata
from backend.sources.tidal.controller_socket import TidalControllerSocket

# Where milo-tidal.service is told to put its controller socket
# (--controller-unix-socket-path). Not the SDK's /tmp default: /run/milo is the
# same RuntimeDirectory the mpv sources already share, so the socket is not
# world-writable.
TIDAL_CONTROLLER_SOCKET = "/run/milo/tidal-controller.sock"

# playerState -> phase. IDLE is a skip's first millisecond or the end of the
# queue: nothing plays, which the idle timeout reads as a pause.
_PHASES = {
    "PLAYING": Phase.PLAYING,
    "PAUSED": Phase.PAUSED,
    "BUFFERING": Phase.LOADING,
    "IDLE": Phase.PAUSED,
}
_STATE_IDLE = "IDLE"

# The daemon pushes a player status about twice a second. The frontend
# interpolates the playhead locally, so a moved position alone is worth only a
# periodic drift correction — at the same cadence AirPlay ages its own. What
# interpolation cannot guess (play/pause, buffering, a session appearing, a
# track ending) still goes out immediately, through the full state path.
POSITION_BROADCAST_INTERVAL = 10.0

# A playhead further than this from the aged one is a jump, not drift.
POSITION_JUMP_TOLERANCE_MS = 2000


@dataclass(eq=False)
class TidalSession(Session):
    """One sender's session: the track on screen and the daemon's player state.

    `duration_ms` is the status one — what the account may play (a 30 s
    preview) — and wins over the media's once known. `position_at` is the loop
    time `position_ms` was read at while the track plays.
    """
    media: Dict[str, Any] = field(default_factory=dict)
    media_id: Optional[str] = None
    player_state: Optional[str] = None
    position_ms: Optional[int] = None
    duration_ms: Optional[int] = None
    position_at: Optional[float] = None


class TidalSource(BaseAudioSource):
    """Tidal Connect source (Family C — active player): UI control, rich metadata."""

    NETWORK_REQUIREMENT = NetworkRequirement.INTERNET

    IDLE_POLICY = IdlePolicy.REQUEST_END
    # The daemon writes to the output it was started on: a multiroom toggle
    # restarts it, and the sender has to pick the speaker again.
    REROUTE = ReroutePolicy.END_SESSION
    # Milō cannot start a Connect session: nothing is kept.
    RESUME_POLICY = ResumePolicy(capture_on=frozenset(), forget_on=frozenset(EndReason))
    SESSION_DAEMON = True

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
        # The last player status seen before any track was named (E14): the
        # session it describes opens with the first media.
        self._early_status: Optional[Dict[str, Any]] = None
        self._last_progress_broadcast: Optional[float] = None
        self.auto_stop_enabled = True

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
            await self._load_auto_stop_config()

            self._controller = TidalControllerSocket(
                socket_path=self._socket_path,
                on_event=self._on_controller_event,
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
        await self.end_session(EndReason.SOURCE_SWITCH)
        return await self._withdraw()

    async def _do_release(self) -> bool:
        await self.end_session(EndReason.REROUTE)
        return await self._withdraw()

    async def _withdraw(self) -> bool:
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

    async def _request_end(self, session: Session) -> bool:
        """REQUEST_END: restart the daemon — the only thing, measured, that
        makes a sender let go. Its exit ends the session under the reason asked
        for. The controller reattaches now rather than after its reconnect
        delay: the new daemon advertises at once, and a phone it accepts before
        `startService` wedges it (see _do_start)."""
        if not await self._restart_service_and_wait():
            return False
        if self._controller is not None:
            await self._controller.start()
        return True

    COMMANDS = {
        "pause": None,
        "resume": None,
        "next": None,
        "prev": None,
    }
    COMMAND_SCOPES = {name: CommandScope.SESSION for name in COMMANDS}

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

    async def _on_controller_event(self, message: Dict[str, Any]) -> None:
        """The controller's callback: it runs on the socket's task, so it posts."""
        self._post_feed(message)

    async def _handle_feed(self, events) -> None:
        """A burst of tisoc frames: applied in order, then one reconcile and one
        publish — a skip's IDLE, media and BUFFERING land in the same
        millisecond, and the screen sees where they end up."""
        if self._controller is None:
            return
        failed = False
        for message in events:
            failed = await self._apply(message) or failed
        playing = False
        session = self._session
        if isinstance(session, TidalSession):
            await self.reconcile(DaemonSnapshot(None, self._phase_of(session)))
            if self._session is session:
                self._sync_clock(session)
                playing = session.phase is Phase.PLAYING
                if playing:
                    # A track plays: the playback error it followed is over (E16).
                    self.broadcast_error_cleared()
        if not self._publish_changes():
            self._broadcast_progress()
        if failed and not playing:
            # An error the same burst already played past is not shown.
            self.broadcast_error(SourceErrorReason.PLAYBACK_FAILED)

    async def _apply(self, message: Dict[str, Any]) -> bool:
        """One frame, applied to the session it is about. True for a playback
        error, reported once the burst is published."""
        command = message.get("command")

        if command == "notifyServiceStateChanged":
            # Only ever sent in answer to the controller's `startService`, which
            # is only ever sent on connect. A reconnect to the same daemon (the
            # socket dropped on a framing error) keeps its session; whether the
            # daemon died is the watched process's to say. With no watch (its
            # pid could not be read) this answer is the only sign a restarted
            # daemon gives, and it remembers no session.
            self._early_status = None
            session = self._session
            if self._daemon_watch is None and session is not None:
                await self._daemon_gone(session)

        elif command == "notifySessionState":
            # 1 (opening) and 2 (established) come before any track is named;
            # 0 is the sender leaving. A frame whose `state` cannot be read is
            # logged and ignored rather than taking the destructive branch.
            state = message.get("state")
            if state == 0:
                self._early_status = None
                await self.reconcile(None)
            elif state == 1:
                # A session opening: what the previous one left (the IDLE after
                # its goodbye) is not about this one.
                self._early_status = None
            elif not isinstance(state, int):
                self._logger.warning(f"notifySessionState without a readable state: {message}")

        elif command == "releaseResources":
            # The daemon handing the audio device back: the session is over.
            self._early_status = None
            await self.reconcile(None)

        elif command == "notifyMediaChanged":
            await self._apply_media(message.get("mediaInfo") or {})

        elif command == "notifyPlayerStatusChanged":
            self._apply_player_status(message)

        elif command == "notifyPlaybackError":
            self._logger.error(f"Tidal playback error (code {message.get('errorCode')})")
            # The track did not start. Whether a status frame follows is not
            # known, so the transport is settled here: nothing plays, and the
            # session stays — the phone can pick another track.
            session = self._session
            if isinstance(session, TidalSession):
                session.player_state = "PAUSED"
                session.position_ms = None
            return True

        else:
            # setShuffle/setRepeatMode/notifyRequestResult/requestResources/
            # notifyAudioFormatUpdated — answered by the transport or not
            # modeled by Milō (measured: nothing else is sent in a session).
            self._logger.debug(f"Unhandled tisoc frame: {command}")
        return False

    async def _apply_media(self, media_info: Dict[str, Any]) -> None:
        """Name the track on screen; the first one opens the session (E14).

        `duration` is milliseconds here as in the player status (a 3:57 track
        arrives as 237000), but the two do not always mean the same span: this
        one is the track, the status one is what the account is entitled to
        play. The media frame changes the track and nothing else — it used to
        replace the whole record and drop the spinner the status frames set.
        """
        metadata = media_info.get("metadata") or {}
        if not metadata.get("title"):
            return
        early = None
        session = self._session
        if not isinstance(session, TidalSession):
            early = self._early_status
            self._early_status = None
            phase = _PHASES.get((early or {}).get("playerState"), Phase.LOADING)
            session = await self.reconcile(DaemonSnapshot(None, phase))
            if not isinstance(session, TidalSession):
                return
        # Every track's media comes twice (measured); a different track owns
        # none of the previous one's playhead or entitlement.
        identity = media_info.get("mediaId") or media_info.get("itemId") or metadata.get("title")
        if identity != session.media_id:
            session.media_id, session.position_ms, session.duration_ms = identity, None, None
            session.position_at = None
            # Its first playhead goes out at once, not up to 10 s later.
            self._last_progress_broadcast = None
        if early:
            self._apply_player_status(early)
        artists = metadata.get("artists") or []
        session.media = {
            "title": metadata.get("title"),
            "artist": ", ".join(artists) or None,
            "album": metadata.get("albumTitle"),
            "album_art_url": self._largest_image(metadata.get("images") or {}),
            "duration": metadata.get("duration"),
        }

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

    def _apply_player_status(self, status: Dict[str, Any]) -> None:
        """A `notifyPlayerStatusChanged` frame: the player state and the playhead."""
        session = self._session
        if not isinstance(session, TidalSession):
            self._early_status = status
            return
        player_state = status.get("playerState")
        session.player_state = player_state
        if player_state == _STATE_IDLE:
            # No track loaded: no position inside one.
            session.position_ms = None
            return
        predicted = self._position_of(session)
        session.position_ms = status.get("progress")
        session.position_at = self._now()
        session.duration_ms = status.get("duration") or session.duration_ms
        if (
            predicted is not None and session.position_ms is not None
            and abs(session.position_ms - predicted) > POSITION_JUMP_TOLERANCE_MS
        ):
            # A seek on the sender, a track repeating: interpolation cannot
            # guess it, so it goes out now rather than at the next correction.
            self._last_progress_broadcast = None

    @staticmethod
    def _phase_of(session: TidalSession) -> Phase:
        return _PHASES.get(session.player_state, Phase.LOADING)

    def _daemon_session(self, snapshot: DaemonSnapshot) -> Session:
        return TidalSession(phase=snapshot.phase, sender=snapshot.sender)

    # === Position ===

    def _now(self) -> float:
        return asyncio.get_running_loop().time()

    def _position_of(self, session: TidalSession) -> Optional[int]:
        if session.position_ms is None or session.position_at is None:
            return session.position_ms
        position = session.position_ms + int((self._now() - session.position_at) * 1000)
        return min(position, session.duration_ms) if session.duration_ms else position

    def _sync_clock(self, session: TidalSession) -> None:
        """The playhead ages from the last status frame while the track plays
        and freezes otherwise; a burst that carried no status leaves the
        anchor where that frame put it."""
        if session.phase is Phase.PLAYING:
            if session.position_at is None:
                session.position_at = self._now()
        else:
            session.position_ms = self._position_of(session)
            session.position_at = None

    def _duration_of(self, session: TidalSession) -> Optional[int]:
        return session.duration_ms or session.media.get("duration")

    def _broadcast_progress(self) -> None:
        """Drift-correct the playhead, at most every POSITION_BROADCAST_INTERVAL."""
        session = self._session
        if not isinstance(session, TidalSession) or session.phase is not Phase.PLAYING:
            return
        position, duration = self._position_of(session), self._duration_of(session)
        if position is None or not duration:
            return

        now = self._now()
        last = self._last_progress_broadcast
        if last is not None and now - last < POSITION_BROADCAST_INTERVAL:
            return

        self._last_progress_broadcast = now
        self.broadcast_position_update(position, duration)

    # === Publication ===

    def _update_connection_state(self) -> None:
        """Publish connection/playback state to the shared player.

        Broadcast metadata (WS source/state_changed → system_state.metadata):
        title, artist, album, album_art_url, position, duration, is_playing,
        is_buffering — all canonical PlaybackMetadata, no extras. Unlike Qobuz
        there is no client_name: with transport controls on screen the player
        draws the transport, not a source bar.
        """
        self.emit_connection_state(*self._connection_state())

    def _connection_state(self):
        session = self._session
        if not isinstance(session, TidalSession):
            return False, None, None
        core = PlaybackMetadata(
            **session.media,
            is_playing=session.phase is Phase.PLAYING,
            is_buffering=session.phase is Phase.LOADING,
        )
        core.duration = self._duration_of(session)
        core.position = self._position_of(session)
        return True, core, None

    async def _cleanup(self) -> None:
        """Drop the controller socket (unit stop is _withdraw's)."""
        if self._controller:
            await self._controller.stop()
            self._controller = None
        # What the socket posted and nobody handled belongs to this daemon run.
        self._discard_feed()
        self._early_status = None


__all__ = ["TidalSource"]
