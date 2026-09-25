# backend/sources/qobuz/source.py
"""Qobuz Connect audio source via the qobuz-proxy sidecar.

qobuz-proxy is a reverse-engineered virtual Qobuz Connect device: the Qobuz app
is the controller, qobuz-proxy renders the stream to ALSA (the milo_qobuz PCM).
Milō only displays + plays (Family B, like AirPlay) — playback is driven from
the Qobuz app, so there are no on-device controls. Now-playing metadata
(title/artist/album/artwork + position/duration) is polled from the proxy's local
HTTP API (GET /api/status); the proxy exposes no push channel and no local
control endpoints. Album art is a Qobuz CDN URL loaded directly by the kiosk —
there is no binary artwork route (unlike AirPlay).

The session belongs to the Qobuz cloud, and the source follows it through
`reconcile()` (docs: source architecture, "reconcile"), from two fields
rootfs/usr/local/bin/milo-qobuz adds to the status beside the playhead. What was
measured on the unit (2026-09-24, qobuz-proxy 1.7.2, the owner's Mac app on a
free account):

  - `renderer_active` is the cloud's SET_ACTIVE: true when the app picks Milō,
    false the instant it picks another output. The app quitting changes
    nothing — the queue plays on, the session lives in the cloud.
  - `player_state` follows each command within a millisecond: STOPPED until
    the app presses play (picking the speaker lands paused, and the player
    stays STOPPED with no track), LOADING for ~100 ms at every start and skip
    (the status reads "idle" with no track meanwhile — the blip the old timers
    absorbed), PLAYING, PAUSED. A preview running into the next track is the
    title alone, no state change. ERROR is a track that failed to load, held
    until the next command.
  - No request ends a session: releasing ownership locally leaves the app on
    Milō. A restart of the sidecar is what makes the app let go (it falls back
    to its own output), so it is the idle timeout's REQUEST_END — as for Tidal.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from backend.config.constants import MILO_DATA_DIR
from backend.core.audio_source import BaseAudioSource, Result
from backend.core.models.audio_state import NetworkRequirement
from backend.core.models.session import (
    DaemonSnapshot, EndReason, IdlePolicy, Phase, ResumePolicy, ReroutePolicy, Session,
)
from backend.sources.qobuz.account import is_connected, read_credentials
from backend.core.models.ws_events import SourceErrorReason
from backend.sources.qobuz.monitor import QobuzMonitor

# One-byte volume-policy flag the patched qobuz-proxy stream reads
# ($QOBUZPROXY_DATA_DIR/allow_app_volume): "1" → honor the Qobuz app's volume
# slider, anything else → stay at unity (CamillaDSP owns volume). Written here
# from the qobuz.allow_app_volume setting; the sidecar's data dir is D3.
QOBUZ_VOLUME_FLAG = MILO_DATA_DIR / "qobuz" / "allow_app_volume"

# qobuz-proxy local HTTP API (aiohttp, bound 0.0.0.0:8689 by milo-qobuz.service).
QOBUZ_STATUS_URL = "http://127.0.0.1:8689/api/status"
# Our speaker is matched by its ALSA output device, not the slugified id
# ("Milō" -> "mil"): qobuz-proxy hard-couples id = slugify(name).
QOBUZ_AUDIO_DEVICE = "milo_qobuz"

# player_state -> phase. STOPPED under a session is the end of the queue and
# ERROR a track that would not load: nothing plays, which the idle timeout
# reads as a pause.
_PHASES = {
    "playing": Phase.PLAYING,
    "paused": Phase.PAUSED,
    "loading": Phase.LOADING,
    "stopped": Phase.PAUSED,
    "error": Phase.PAUSED,
}
_STATE_ERROR = "error"

# The status is polled about once a second; each reading goes to the position
# axis, which publishes only a jump (a seek in the app, a new track).
TRACK_FIELDS = ("title", "artist", "album", "album_art_url")


@dataclass(eq=False)
class QobuzSession(Session):
    """The app's session on this speaker: the track on screen and its length
    (its playhead is the session's anchor)."""
    track: Dict[str, Any] = field(default_factory=dict)
    duration_ms: Optional[int] = None


class QobuzSource(BaseAudioSource):
    """Qobuz Connect source (Family B — passive player): external control, rich metadata."""

    NETWORK_REQUIREMENT = NetworkRequirement.INTERNET

    IDLE_POLICY = IdlePolicy.REQUEST_END
    # The sidecar writes to the output it was started on: a multiroom toggle
    # restarts it, and the app has to pick the speaker again.
    REROUTE = ReroutePolicy.END_SESSION
    # Milō cannot start a Connect session: nothing is kept.
    RESUME_POLICY = ResumePolicy(capture_on=frozenset(), forget_on=frozenset(EndReason))
    SESSION_DAEMON = True

    # Family B: playback is controlled from the Qobuz app; qobuz-proxy exposes no
    # local control channel — command() rejects every command as unknown.
    COMMANDS = {}
    COMMAND_SCOPES = {}

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
        # The account (docs: "le fil", §3): while the sidecar runs, what its
        # poll says (auth.authenticated, two polls in a row to call a logout);
        # otherwise the token cache the account route reads.
        self._authenticated = True
        self._unauthenticated_polls = 0
        self._account_cached = False
        # The player state last seen, session or not: an ERROR is reported once,
        # on the snapshot that enters it.
        self._last_player_state: Optional[str] = None
        self.auto_stop_enabled = True

    async def initialize(self) -> bool:
        self._account_cached = is_connected(await read_credentials())
        return await super().initialize()

    def availability(self) -> Optional[str]:
        connected = self._authenticated if self._monitor is not None else self._account_cached
        return None if connected else "no_account"

    async def account_changed(self) -> None:
        """The account was changed outside the sidecar (a logout from the
        settings screen): read the cache again, in the actor — the route's task
        never writes the source itself."""
        await self._submit(Result(self._reread_account))

    async def _reread_account(self) -> None:
        self._account_cached = is_connected(await read_credentials())
        self._availability_changed()

    async def _do_start(self) -> bool:
        """Start the qobuz-proxy service and the /api/status poll monitor."""
        try:
            # Put the volume-policy flag in place before the sidecar starts so its
            # first volume command reads the right value.
            await self._sync_volume_flag()

            if not await self._start_service_and_wait():
                return False
            await self._load_auto_stop_config()
            # The cache is what the sidecar authenticates from: the first polls
            # start from it rather than flashing "connect an account".
            self._authenticated, self._unauthenticated_polls = self._account_cached, 0

            self._monitor = QobuzMonitor(
                status_url=self._status_url,
                audio_device=self._audio_device,
                on_status=self._on_status,
            )
            await self._monitor.start()

            self._publish()
            return True

        except Exception as e:
            self._logger.error(f"Start failed: {e}")
            await self._cleanup()
            return False

    async def _do_stop(self) -> bool:
        await self.end_session(EndReason.SOURCE_SWITCH)
        await self._cleanup()
        stopped = await self._stop_service()
        # A login made while the sidecar ran is in the cache now.
        self._account_cached = is_connected(await read_credentials())
        return stopped

    async def _do_release(self) -> bool:
        await self.end_session(EndReason.REROUTE)
        await self._cleanup()
        return await self._stop_service()

    async def _request_end(self, session: Session) -> bool:
        """REQUEST_END: restart the sidecar — the only thing, measured, that
        makes the app let go. Its exit ends the session under the reason asked
        for."""
        return await self._restart_service_and_wait()

    # ------------------------------------------------------------------
    # Volume policy (allow the mobile app to control volume, or pin unity)
    # ------------------------------------------------------------------

    def _write_volume_flag(self, allowed: bool) -> None:
        """Write the one-byte flag the patched qobuz-proxy stream reads.

        "1" → honor the Qobuz app slider, "0" → stay at unity (CamillaDSP owns
        volume). Best-effort: a write failure leaves the sidecar at its safe
        default (unity), so log and carry on rather than failing the source.
        """
        try:
            QOBUZ_VOLUME_FLAG.write_text("1" if allowed else "0")
        except OSError as e:
            self._logger.warning(f"Could not write Qobuz volume-policy flag: {e}")

    async def _sync_volume_flag(self) -> None:
        """Refresh the flag from the persisted qobuz.allow_app_volume setting."""
        allowed = False
        if self._settings_service:
            qobuz = await self._settings_service.get_setting("qobuz")
            allowed = bool(qobuz["allow_app_volume"])
        self._write_volume_flag(allowed)

    async def on_allow_app_volume_changed(self, allowed: bool) -> bool:
        """Apply the 'allow app volume' toggle (settings reload_callback).

        The running stream re-reads the flag on the next app volume command, so
        unlocking is honored live. Locking must reset an already-lowered stream to
        unity immediately, which only a restart forces — bounce the sidecar when
        it is running, in the mailbox, ending the session it restarts under.
        """
        self._write_volume_flag(allowed)
        if allowed:
            return True
        return await self._submit(Result(self._restart_for_volume_lock))

    async def _restart_for_volume_lock(self) -> bool:
        """The user's own request, not a death: the app lets go of the speaker."""
        if not await self._is_service_active():
            return True
        if await self.end_session(EndReason.USER_STOP) is not None:
            self._publish()
        restarted = await self._restart_service_and_wait()
        # The poll kept running across the restart: an answer the old sidecar
        # gave meanwhile describes the session just ended and would reopen it.
        self._discard_feed()
        return restarted

    # ------------------------------------------------------------------
    # Status feed
    # ------------------------------------------------------------------

    async def _on_status(
        self, speaker: Optional[Dict[str, Any]], authenticated: bool
    ) -> None:
        """The monitor's callback: it runs on the poll task, so it posts."""
        self._post_feed((speaker, authenticated))

    async def _handle_feed(self, events) -> None:
        """Polled snapshots, applied in order, then one publish. A tick the
        monitor could not read is never delivered (QobuzMonitor._fetch_status),
        so nothing here stands for "unknown"."""
        if self._monitor is None:
            return
        failed = False
        for speaker, authenticated in events:
            failed = await self._apply(speaker, authenticated) or failed
        session = self._session
        playing = isinstance(session, QobuzSession) and session.phase is Phase.PLAYING
        if playing:
            # A track plays: the error it followed (a failed load, a sidecar
            # that died under the previous session) is over.
            self.broadcast_error_cleared()
        self._publish_changes()
        if failed and not playing:
            self.broadcast_error(SourceErrorReason.PLAYBACK_FAILED)

    async def _apply(self, speaker: Optional[Dict[str, Any]], authenticated: bool) -> bool:
        """One snapshot. True when a track has just failed to load."""
        self._apply_login(authenticated)
        if speaker is None or not speaker.get("renderer_active"):
            # The app picked another output (or the speaker is gone): the
            # session is over, under whatever reason Milō asked for if it did.
            self._last_player_state = None
            await self.reconcile(None)
            return False

        player_state = speaker.get("player_state")
        # Before the session gate: a first track that fails carries no title
        # (upstream builds now_playing for playing and paused only), and the
        # failure is still the app's command going nowhere.
        failed = player_state == _STATE_ERROR and self._last_player_state != _STATE_ERROR
        self._last_player_state = player_state
        now = speaker.get("now_playing") or {}
        session = self._session
        if not isinstance(session, QobuzSession) and not now.get("title"):
            # The app picked the speaker and has nothing loaded, or the first
            # track is still loading: nothing to name, so no session yet (E14).
            return failed

        phase = _PHASES.get(player_state, Phase.LOADING)
        session = await self.reconcile(DaemonSnapshot(None, phase))
        if not isinstance(session, QobuzSession):
            return failed
        if now.get("title"):
            self._apply_track(session, now)
        return failed

    def _apply_login(self, authenticated: bool) -> None:
        """Measured: a sidecar coming up answers `authenticated: false` for
        ~0.3 s before its credentials load, and every idle timeout restarts it.
        One poll cannot tell that window from a logout; two in a row, a second
        apart, can."""
        before = self._authenticated
        if authenticated:
            self._authenticated, self._unauthenticated_polls = True, 0
        else:
            self._unauthenticated_polls += 1
            if self._unauthenticated_polls >= 2:
                self._authenticated = False
        if self._authenticated is not before:
            self._availability_changed()

    def _apply_track(self, session: QobuzSession, now: Dict[str, Any]) -> None:
        """The track and its playhead, as the sidecar reports them.

        A new track owns none of the previous one's playhead, but keeps its
        length until its own is known: ProgressBar renders under
        `duration > 0` and replays its entrance when the bar comes back, so a
        length dropped for one tick is a visible flicker.
        """
        track = {key: now.get(key) for key in TRACK_FIELDS}
        if track != session.track:
            session.track = track
            self._clear_position()
        if now.get("duration_ms"):
            # A length resolved after its title is a state change: the bar appears.
            session.duration_ms = now["duration_ms"]
        self._observe_position(now.get("position_ms"))

    def _daemon_session(self, snapshot: DaemonSnapshot) -> Session:
        return QobuzSession(phase=snapshot.phase, sender=snapshot.sender)

    # ------------------------------------------------------------------
    # The view (docs: "le fil")
    # ------------------------------------------------------------------

    def _session_fields(self, session: QobuzSession) -> Dict[str, Any]:
        """No sender name: the proxy never reports the controlling device —
        it only knows the speaker name. No commands either: the app is the
        only remote."""
        track = session.track
        return {
            "title": track.get("title"),
            "artist": track.get("artist"),
            "album": track.get("album"),
            "artwork": track.get("album_art_url"),
            "duration_ms": session.duration_ms,
        }

    async def _cleanup(self) -> None:
        """Stop the poll monitor (unit stop is the caller's)."""
        if self._monitor:
            await self._monitor.stop()
            self._monitor = None
        # What the poll posted and nobody handled belongs to this sidecar run.
        self._discard_feed()
        self._last_player_state = None


__all__ = ["QobuzSource"]
