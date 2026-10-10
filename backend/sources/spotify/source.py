# backend/sources/spotify/source.py
"""
Spotify Connect via go-librespot.

The session belongs to go-librespot, not to Milō. It opens two ways: a phone
picks the speaker, or Milō plays a context (`play_context`) on the account the
daemon is signed in as. Either way the daemon plays, pauses and ends it, and
the source follows it through `reconcile()` (docs: source architecture,
"reconcile"). One thing decides where the session stands: go-librespot's own
GET /status, read once after every burst of /events. An event is when to look,
never what to believe — two writers (the event and /status) is how the screen
said "playing" while the auto-stop counted down a pause (E11). The play order
around the track is GET /player/queue, a second read: it is read again when the
daemon's `queue` event says it moved (raised whenever that answer changes), on
a connection, after a read that failed, and whenever the order it holds does
not surround the track /status names — it is never shown around another track.

The account. With `persist_credentials`, the first cast's account is stored in
go-librespot's state.json and signs the daemon back in, with no phone, at every
start and after every session end. Measured on 0.10.3 (2026-10-03):

- The first cast writes state.json atomically, before `active` and before
  /status names the account.
- After a session end (`inactive`, `stopped`), /status answers 204 for about
  0.25 s, then `playback_ready` and a 200 naming the account and no track. At a
  start, the sign-in takes about 0.35 s and says nothing on /events.
- One sign-in after an iPhone's session ended hung for minutes, unreproduced in
  8 tries: stored credentials that have not signed in within SIGNIN_TIMEOUT get
  the daemon restarted once.
- With nobody signed in, /player/* answers 204. Signed in, shuffle and repeat
  are taken, but a `play` with no session starts from a fresh state, shuffle
  off (2026-10-04); within a session, shuffle carries over to the next play.

Measured on go-librespot 0.10.0 (2026-09-24, an iPhone), which the phase
follows:

- /status answers 204 when nobody is signed in, 200 otherwise (a 200 with no
  track is a signed-in daemon with no session); between `will_play` and
  `metadata` it reports `buffering` and no track yet.
- A transfer loads the track paused at the phone's position and plays 1.6 s
  later; a skip is 70 ms of loading; a track's end is `not_playing` and a
  paused /status for 250-350 ms before the next one (autoplay never lets a
  context end).
- POST /player/stop ends the session (`inactive`) and the phone lets go: the
  idle timeout's REQUEST_END. A phone picking another output says the same.
- A killed daemon says nothing; its session ends when its process does (the
  base's pidfd watch).

Another device. go-librespot is told by Spotify what every device of the
account does, and its /status names the active one when it is not this one
(`remote`, raised on change by a `remote` event). Measured 2026-10-05 (an
iPhone and a Mac on the owner's account): an inactive daemon hears every play,
pause, skip, seek and track end there within ~50 ms; the device name is the
app's ("iPhone"); a phone gone to the background or offline leaves the cluster
9-14 s after its last word, and with no active device nothing is shown even
though Spotify still holds the paused session. `take_over` asks go-librespot
for the transfer (POST /player/transfer): the session arrives ~0.25 s later at
the same position, through `reconcile` like any phone's transfer.

Commands go through POST /player/<cmd>. routes.py is the browser's: the
library (library.py), its shaping (catalog.py) and the kept profiles
(profiles.py).
"""
import asyncio
from backend.core.models.ws_events import SourceErrorReason, SpotifyProfilesChanged
import contextlib
import json
import os
import re
import time
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import aiofiles
import aiohttp
from pydantic import BaseModel

from backend.core import audio_source
from backend.core.audio_source import POSITION_TOLERANCE_MS, BaseAudioSource, Result
from backend.core.models.audio_state import NetworkRequirement
from backend.core.models.audio_wire import PositionAnchor, SpotifyDetails, SpotifyRemote
from backend.core.models.session import (
    Anchor, CommandScope, DaemonSnapshot, EndReason, IdlePolicy, Phase, ResumePolicy, ReroutePolicy,
    Session,
)
from backend.core.models.commands import SetRepeatParams, SetShuffleParams, SkipParams, skip_target
from backend.sources.spotify.catalog import artists_of, history_entry, queue_entry
from backend.sources.spotify.history import SpotifyHistory
from backend.sources.spotify.library import SpotifyLibrary
from backend.sources.spotify.models import (
    NextParams, PlayContextParams, PrevParams, SeekParams,
)
from backend.sources.spotify.profiles import SpotifyProfiles
from backend.sources.spotify.websocket import LibrespotWebSocket
from backend.shared.decorators import handle_errors
from backend.shared.persistence import write_bytes_atomically
from backend.shared.journalctl import follow_unit


@dataclass(frozen=True)
class LibrespotStatus:
    """What GET /status said: whose session it is, and where it stands."""
    account: Optional[str]
    track: Optional[Dict[str, Any]]
    paused: bool
    buffering: bool
    context_uri: Optional[str] = None
    context_name: Optional[str] = None
    shuffle: bool = False
    repeat: str = "off"
    remote: Optional[Dict[str, Any]] = None


@dataclass
class RemotePlayback:
    """What the account plays on the active device when it is another one."""
    device_name: str
    device_type: str
    track_uri: Optional[str]
    track: Dict[str, Any]
    paused: bool
    anchor: Anchor


def repeat_mode(repeat_context: bool, repeat_track: bool) -> str:
    """The player's one repeat mode from go-librespot's two flags. Repeating
    the track leaves `repeat_context` on (measured), so the track flag wins."""
    if repeat_track:
        return "track"
    return "context" if repeat_context else "off"


def repeat_posts(mode: str) -> List[tuple]:
    """The two /player posts that put go-librespot in `mode`, in order: the
    flag being turned off goes first, so no intermediate state repeats what
    neither the old nor the new mode does."""
    if mode == "track":
        return [("repeat_context", {"repeat_context": True}), ("repeat_track", {"repeat_track": True})]
    return [
        ("repeat_track", {"repeat_track": False}),
        ("repeat_context", {"repeat_context": mode == "context"}),
    ]


@dataclass(frozen=True)
class PlayOrder:
    """What GET /player/queue said: the entries ({uri, uid, provider, track})
    before the current one oldest first, the ones after it nearest first, and
    the uris that name the current one (its own, and its track's if relinked)."""
    current: frozenset = frozenset()
    prev_tracks: Tuple[Dict[str, Any], ...] = ()
    next_tracks: Tuple[Dict[str, Any], ...] = ()

    def surrounds(self, uri: Optional[str]) -> bool:
        return uri is not None and uri in self.current


def play_order(data: Dict[str, Any]) -> PlayOrder:
    current = data.get("track") if isinstance(data.get("track"), dict) else {}
    track = current.get("track") if isinstance(current.get("track"), dict) else {}

    def entries(key: str) -> Tuple[Dict[str, Any], ...]:
        return tuple(item for item in data.get(key) or () if isinstance(item, dict))

    return PlayOrder(
        current=frozenset(uri for uri in (current.get("uri"), track.get("uri")) if uri),
        prev_tracks=entries("prev_tracks"),
        next_tracks=entries("next_tracks"),
    )


class _Unreadable:
    """go-librespot could not be read: nothing was learned."""


UNREADABLE = _Unreadable()

# The login5 statuses go-librespot retries: an outage or a rate limit.
LOGIN5_UNAVAILABLE = re.compile(r"login5 returned HTTP (5\d\d|429)\b")


def login_failure(line: str) -> Optional[str]:
    """Why Spotify refused a login, read off go-librespot's error chain.

    The chain carries the accesspoint's `ErrorCode` name and login5's
    `LoginError` name verbatim. A login5 error page (measured 2026-09-29: a
    503 "no healthy upstream") is Spotify down, not a refusal: go-librespot
    names its status (`login5 returned HTTP 503: …`) and retries 5xx and 429;
    another 4xx fails at once and says nothing about why. Wording is
    go-librespot 0.10.3's — re-read it on every bump.
    """
    if "PremiumAccountRequired" in line:
        return SourceErrorReason.PREMIUM_REQUIRED
    if any(m in line for m in (
        "BadCredentials", "CouldNotValidateCredentials", "login5: INVALID_CREDENTIALS",
    )):
        return SourceErrorReason.CREDENTIALS_REFUSED
    if LOGIN5_UNAVAILABLE.search(line) or any(m in line for m in (
        "failed requesting login5:", "failed reading login5 response",
        "login5: TRY_AGAIN_LATER", "TryAnotherAP",
    )):
        return SourceErrorReason.PROVIDER_UNAVAILABLE
    if any(m in line for m in ("dial tcp", "i/o timeout", "no such host")):
        return SourceErrorReason.SERVICE_UNREACHABLE  # Milō's own network, not a refusal
    return None


# Refusals of the account itself: the accesspoint retries them, and each retry
# is not an outage of the accesspoint.
ACCOUNT_REFUSALS = (SourceErrorReason.PREMIUM_REQUIRED, SourceErrorReason.CREDENTIALS_REFUSED)
LOGIN_FAILURES = ACCOUNT_REFUSALS + (
    SourceErrorReason.PROVIDER_UNAVAILABLE, SourceErrorReason.CONNECTION_REFUSED,
)


@dataclass(eq=False)
class SpotifySession(Session):
    """One Connect session: the track on screen (its playhead is the
    session's anchor) and the context it plays from."""
    track: Dict[str, Any] = field(default_factory=dict)
    uri: Optional[str] = None
    context_uri: Optional[str] = None
    context_name: Optional[str] = None
    album_uri: Optional[str] = None
    artists: List[Dict[str, Optional[str]]] = field(default_factory=list)
    # The play order around the track (queue_entry), and the track's place in it.
    queue: List[Dict[str, Any]] = field(default_factory=list)
    queue_index: Optional[int] = None


class SpotifySource(BaseAudioSource):
    """
    Spotify audio source using go-librespot.

    Family C (active player): controlled from Milō's UI and browsed there.
    Commands flow through the generic `/api/audio/control/spotify` endpoint;
    routes.py serves the browser. Extends BaseAudioSource.
    """

    NETWORK_REQUIREMENT = NetworkRequirement.INTERNET

    IDLE_POLICY = IdlePolicy.REQUEST_END
    # go-librespot reopens its output in place (POST /player/output): a
    # multiroom toggle moves the writer and keeps the session.
    REROUTE = ReroutePolicy.KEEP_SESSION
    # Milō starts a session too (play_context, on the account the daemon is
    # signed in as), but go-librespot keeps the context itself: nothing is kept.
    RESUME_POLICY = ResumePolicy(capture_on=frozenset(), forget_on=frozenset(EndReason))
    SESSION_DAEMON = True

    # The four go-librespot config keys Milō owns: crossfade_duration and
    # external_volume (from settings), credentials and metadata (constant,
    # below). Every other key in config.yml (device_name, zeroconf_backend,
    # server) is written once by provisioning/go-librespot.sh and never touched
    # here.
    #
    # Deliberately NOT owned: flac_enabled. go-librespot 0.8.0 fixed the FLAC
    # decoder (it normalised samples by 2^bps instead of 2^(bps-1), so lossless
    # played 6 dB too quiet), but the released binaries still refuse to boot
    # with the flag on — "fatal: FLAC playback requires a PlapPlay
    # implementation" (measured on the unit, 2026-08-03). It is a fatal
    # misconfiguration, not an inert flag: enabling it costs Spotify entirely.
    CROSSFADE_SETTINGS_KEY = "spotify.crossfade_duration"
    # external_volume: true leaves the samples at unity (the app's slider moves
    # nothing, CamillaDSP is the only attenuation); false lets go-librespot
    # scale them by the slider, squared. The daemon restores the slider from its
    # own state.json (last_volume), which it keeps saving while external — so
    # turning this on plays at the level the phone already shows.
    APP_VOLUME_SETTINGS_KEY = "spotify.allow_app_volume"
    # persist_credentials: the first cast's account is kept in state.json and
    # signs the daemon back in at every start and after every session end, so
    # Milō can browse and play with no phone. metadata: the cache behind
    # /context/tracks; max_tracks bounds a listing, sized on the owner's 1159
    # Liked Songs (measured 2026-10-03: 3000 cost 17 MB of RSS, `length`
    # never shows what was cut off).
    MANAGED_CONSTANT_CONFIG = {
        "credentials": {"type": "zeroconf", "zeroconf": {"persist_credentials": True}},
        "metadata": {"enabled": True, "max_tracks": 1500},
    }
    # go-librespot's own state, beside config.yml: the stored credentials.
    STATE_FILE = "state.json"
    # How long stored credentials may take to sign the daemon in before it is
    # restarted, once. Measured: 0.35 s at start, 0.4 s after a session end;
    # one sign-in after an iPhone's session ended hung for minutes, unreproduced.
    SIGNIN_TIMEOUT = 10.0

    # Neutral sink the output is parked on while a multiroom reroute reconciles
    # snapcast. ALSA's `null` discards samples as fast as they are written, so
    # playback MUST be paused before switching to it: measured on the unit
    # (2026-08-03), an unpaused switch ran a track from 1'10" to its end and
    # into the next one in about two seconds.
    RELEASE_DEVICE = "null"

    # How long to wait before re-reading /status after a failed read. Short
    # enough that a transient blip doesn't leave a visibly stale screen, long
    # enough to clear a daemon restart's unreachable window.
    STATUS_RETRY_DELAY = 2.0

    # How long the bar keeps what plays elsewhere while /status names nothing,
    # by what started the hold (measured 2026-10-05, go-librespot 0.10.3):
    # - a take-over asked here: the daemon goes active at once, names no
    #   remote, and the session opens ~0.25 s later at its first track;
    # - a session arriving, its track not loaded yet (~0.2 s of buffering);
    # - a phone taking the session over: 204 for ~0.3 s while the daemon signs
    #   in again, then sometimes ~0.15 s naming nothing, then the phone.
    # Past it the wire follows /status again.
    TAKE_OVER_HOLD = 10.0
    ARRIVAL_HOLD = 5.0
    HANDOVER_HOLD = 3.0

    # POST /player/transfer answers once Spotify accepted the transfer (~30 ms
    # measured), longer than the 3 s every other call is given on a slow link.
    TRANSFER_TIMEOUT = 10.0

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        state_machine=None,
        settings_service=None,
        systemd_manager=None
    ):
        super().__init__(
            source_id="spotify",
            service_name="milo-spotify.service",
            state_machine=state_machine,
            systemd_manager=systemd_manager,
            settings_service=settings_service,
            config=config
        )

        self._config_path = os.path.expanduser(self._config.get("config_path", ""))

        # API endpoints (loaded from config file)
        self._api_url: Optional[str] = None
        self._ws_url: Optional[str] = None

        self._http: Optional[aiohttp.ClientSession] = None
        self._ws_client: Optional[LibrespotWebSocket] = None
        # The daemon did not answer at start: a banner stands until /events connects.
        self._unanswered = False

        self.auto_stop_enabled = True

        # Multiroom reroute: whether the release went through /player/output
        # (session kept) or fell back to a full stop, and what to restore.
        self._soft_reroute = False
        self._reroute_was_playing = False

        # Log monitor for error detection
        self._log_monitor_task: Optional[asyncio.Task] = None
        self._connection_error_count = 0
        self._last_error_time = 0.0
        # The banner standing, and the last refused login: the Spotify app
        # retries a refused Connect every ~3 s for a minute, one banner for all.
        self._standing_reason: Optional[str] = None
        self._last_login_failure: Optional[tuple] = None

        # The account the daemon is signed in as (or about to be), and the
        # player state that outlives a session: what details publish.
        self._account: Optional[str] = None
        # The account whose credentials state.json holds. With
        # persist_credentials the signed-in account is always the stored one
        # (a cast writes the file before /status names it, measured), so it is
        # read from the file once per start and followed from /status after.
        self._persisted: Optional[str] = None
        self._signing_in = False
        self._signin_restarted = False
        self._shuffle = False
        self._repeat = "off"
        self._remote: Optional[RemotePlayback] = None
        # Until when the remote is kept while /status names nothing (0: no
        # hold since the last release) — see TAKE_OVER_HOLD.
        self._remote_held_until = 0.0
        # What a `remote` event announced since the last /status read: what the
        # daemon itself saw before it went to sign in again, the one record of
        # the phone while /status cannot name it. Consumed by the next read.
        self._announced_remote: Optional[Dict[str, Any]] = None
        # The play order around the track, as GET /player/queue last listed it;
        # stale once the daemon said it moved or a read of it failed, until it
        # is read again. A daemon without the route (stock 0.10.3) is not asked.
        self._order = PlayOrder()
        self._order_stale = True
        self._order_route = True
        self._order_retried = False     # the one retry after a failed read is spent
        # A track this player started, for the history: go-librespot's
        # `metadata` comes once per track it loads and carries it, and the
        # `playing` after it (a resume when it loaded paused, as a transfer
        # does) is its start. The entry is made from those two events alone —
        # /status may already name the next track — and waits here for a
        # /status read to confirm the account to list it under (a first cast,
        # or a guest's, names it there). Every start waits: two skips in one
        # burst are two plays.
        self._loaded_track: Optional[Dict[str, Any]] = None
        self._pending_starts: List[Dict[str, Any]] = []

        # The browser's two services. Profiles only where a path is given
        # (dependencies.py): a source built without one keeps no account.
        self._library = SpotifyLibrary()
        profiles_path = self._config.get("profiles_path")
        self._profiles = SpotifyProfiles(Path(profiles_path)) if profiles_path else None
        # Accounts whose credentials were already kept in this daemon run.
        self._harvested: set = set()
        history_path = self._config.get("history_path")
        self._history = SpotifyHistory(Path(history_path)) if history_path else None

    @property
    def library(self) -> SpotifyLibrary:
        return self._library

    @property
    def profiles(self) -> Optional[SpotifyProfiles]:
        return self._profiles

    @property
    def history(self) -> Optional[SpotifyHistory]:
        return self._history

    @property
    def account(self) -> Optional[str]:
        """The account go-librespot is signed in as, or about to be."""
        return self._account

    async def initialize(self) -> bool:
        """Load the profiles and the history, so a schema drift stops the boot
        with its banner."""
        if self._profiles is not None:
            await self._profiles.initialize()
        if self._history is not None:
            await self._history.initialize()
        return await super().initialize()

    async def _do_start(self) -> bool:
        """Start go-librespot service and WebSocket."""
        try:
            # 1. Load config (sets _api_url / _ws_url)
            if not await self._load_config():
                return False

            # 1b. Push Milō-owned keys into config.yml BEFORE the daemon reads
            # it — go-librespot parses its config once, at process start.
            await self._apply_managed_config()

            # 1c. Stored credentials sign the daemon in on its own, with no
            # event to say so: the account is known from them until /status
            # confirms it.
            self._persisted = self._account = await self._stored_account()
            if self._account is not None:
                self._begin_signin()

            # 2. Start the service (readiness is polled below, not slept on)
            if not await self._start_service():
                return False

            # 3. HTTP session. Bounded per-request timeout so an unresponsive
            # daemon can't block /player/stop or the startup poll. aiohttp applies
            # it to the /events upgrade request only, never to the socket after
            # it, so the long-lived stream is unaffected.
            self._http = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=3.0)
            )
            self._library.open(self._api_url)

            # 4. Wait until the daemon's API is reachable before connecting.
            # Not fatal — the WS loop reconnects on its own — but the source is
            # about to report itself started over a daemon that never answered,
            # and a phone then finds no Milō: said in the journal and on screen
            # (`source.*` loggers never reach the banner), until /events
            # connects. _wait_for_playback_ready only warns.
            if not await self._wait_for_playback_ready():
                self._logger.error(
                    "go-librespot never answered; starting anyway — playback may not work"
                )
                self.broadcast_error(SourceErrorReason.SERVICE_UNREACHABLE)
                self._unanswered = True

            # 5. /events: every (re)connection and every event is posted.
            await self._start_websocket()

            # 6. Start log monitor for error detection
            self._start_log_monitor()

            self._publish()
            return True

        except Exception as e:
            self._logger.error(f"Start failed: {e}")
            await self._cleanup()
            return False

    async def _do_stop(self) -> bool:
        """End the session, stop Spotify gracefully, then stop the service.

        POST /player/stop first to disconnect the Connect session and release
        the ALSA Loopback in-process, so the next source can grab it without
        waiting; then cleanup and `systemctl stop`. go-librespot exits gracefully
        on SIGTERM (0.8.0: 3.8ms on a live playing session, measured on the Pi
        2026-08-03), with TimeoutStopSec=5 as a backstop.

        A /player/stop failure must NOT block the service stop — log + continue.
        """
        await self.end_session(EndReason.SOURCE_SWITCH)
        result = await self._send_api_command("stop")
        if not result.get("success"):
            self._logger.warning(
                f"/player/stop failed before service stop: {result.get('error')}"
            )

        await self._cleanup()
        return await self._stop_service()

    async def _request_end(self, session: Session) -> bool:
        """REQUEST_END: POST /player/stop. The `inactive` that follows (3 ms,
        measured) ends the session through reconcile()."""
        result = await self._send_api_command("stop")
        if not result.get("success"):
            self._logger.warning(f"Asking go-librespot to end the session failed: {result.get('error')}")
        return bool(result.get("success"))

    # === Multiroom reroute ===

    async def _do_release(self) -> bool:
        """Free the ALSA device for a MILO_MODE change, keeping the session.

        go-librespot 0.8.0 reopens its output in place via POST /player/output,
        preserving the Connect session, track, position and volume — so a
        multiroom toggle no longer has to bounce the daemon and send the phone
        looking for the speaker again.

        Pause first, and wait for the daemon to confirm it: RELEASE_DEVICE does
        not rate-limit, so an unpaused switch races through the rest of the
        track. Any step that does not answer falls back to a full stop: a
        source still holding the loopback would block snapclient, which is the
        one outcome worse than a dropped session.

        The pause's own `paused` event waits in the mailbox the reroute holds,
        and is read against /status after the reacquire's resume (E09: it used
        to publish "paused" over the reroute and arm the auto-stop).
        """
        self._soft_reroute = False
        self._reroute_was_playing = False

        if not self._http or not self._api_url:
            return await self._release_by_stopping()

        # Ground truth before pausing rather than the session's phase: a stale
        # phase (a WS that dropped mid-playback) would skip the pause and hand
        # a live stream to a sink that does not rate-limit.
        status = await self._read_status()
        if status is UNREADABLE:
            self._logger.warning("Reroute: daemon unreachable, falling back to a full stop")
            return await self._release_by_stopping()

        self._reroute_was_playing = status is not None and not status.paused

        if self._reroute_was_playing and not await self._pause_and_confirm():
            self._logger.warning("Reroute: pause unconfirmed, falling back to a full stop")
            return await self._release_by_stopping()

        result = await self._send_api_command("output", {"device": self.RELEASE_DEVICE})
        if not result.get("success"):
            self._logger.warning(
                f"Reroute: releasing the output failed ({result.get('error')}), "
                "falling back to a full stop"
            )
            return await self._release_by_stopping()

        self._soft_reroute = True
        self._logger.info("Reroute: output parked, Connect session kept")
        return True

    async def _release_by_stopping(self) -> bool:
        await self.end_session(EndReason.REROUTE)
        return await self._run_stop()

    async def _do_acquire(self) -> bool:
        """Reopen the output on the device the new MILO_MODE selects.

        The device name is explicit rather than the `milo_spotify` alias: that
        alias resolves MILO_MODE from the daemon's own environment, frozen when
        it started, so with no restart it would still name the old mode — which
        is also why a reopen that fails restarts the daemon (E08): a start is a
        no-op on a unit already running, and the daemon would go on writing to
        `null`, every later session silent.

        The reroute left the source in STARTING and nothing else clears it
        without the usual start(), hence the unconditional publish.
        """
        if not self._soft_reroute:
            return await super()._do_acquire()

        self._soft_reroute = False
        device = self._output_device_for_mode()

        result = await self._send_api_command("output", {"device": device})
        if not result.get("success"):
            self._logger.warning(
                f"Reroute: reopening on {device} failed ({result.get('error')}), "
                "restarting go-librespot"
            )
            await self.end_session(EndReason.REROUTE)
            await self._do_stop()
            return await self._run_start()

        resumed = False
        if self._reroute_was_playing:
            answer = await self._send_api_command("resume")
            resumed = bool(answer.get("success"))
            if not resumed:
                self._logger.warning(f"Reroute: resume failed: {answer.get('error')}")

        # After a resume the session keeps the phase it had: /status lags the
        # command (why the release confirms its pause), and read now it could
        # still say paused. The `playing` it answers with is read in turn.
        if not resumed:
            status = await self._read_status()
            if status is not UNREADABLE:
                await self._apply_status(status)
        self._publish()
        self._logger.info(f"Reroute: output reopened on {device}")
        return True

    @staticmethod
    def _output_device_for_mode() -> str:
        """ALSA device for the current routing mode.

        RoutingEnv.regenerate() sets MILO_MODE in this process before the
        re-acquire step runs, so the backend's own environment is the live
        value. Default mirrors asound.conf's own fallback.
        """
        return f"milo_spotify_{os.environ.get('MILO_MODE', 'direct')}"

    async def _pause_and_confirm(self, timeout: float = 2.0, interval: float = 0.05) -> bool:
        """Pause playback and wait until go-librespot reports it paused.

        The command returns before the player has actually stopped pulling
        samples; only /status says when it has.
        """
        if not (await self._send_api_command("pause")).get("success"):
            return False

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status = await self._read_status()
            if status is UNREADABLE:
                return False
            if status is None or status.paused:
                return True
            await asyncio.sleep(interval)

        return False


    COMMANDS = {
        "pause": None,
        "resume": None,
        # Toggle: the hardware click dispatcher has no reliable is_playing
        # snapshot at press time, so go-librespot resolves the edge.
        "playpause": None,
        "seek": SeekParams,
        "skip": SkipParams,
        "next": NextParams,
        "prev": PrevParams,
        "play_context": PlayContextParams,
        "set_shuffle": SetShuffleParams,
        "set_repeat": SetRepeatParams,
        # Bring here what the account plays on another device.
        "take_over": None,
    }
    COMMAND_SCOPES = {
        **{name: CommandScope.SESSION for name in COMMANDS},
        "play_context": CommandScope.CONTENT,
        "take_over": CommandScope.CONTENT,
    }

    async def _handle_command(self, cmd: str, params: Optional[BaseModel]) -> Dict[str, Any]:
        """Handle Spotify-specific commands."""
        if cmd == "seek":
            duration = self._session.track.get("duration_ms") or 0
            if duration > 0 and params.position_ms > duration:
                return self.error_response(
                    f"position_ms ({params.position_ms}) exceeds duration ({duration}ms)"
                )
            return await self._seek_to(int(params.position_ms))

        if cmd == "skip":
            return await self._handle_skip(params)

        if cmd in ["pause", "resume", "playpause"]:
            return await self._send_api_command(cmd)

        if cmd == "next":
            return await self._send_api_command(cmd, {"uri": params.uri} if params.uri else {})

        if cmd == "prev":
            return await self._send_api_command(cmd, {} if params.allow_seeking else {"allow_seeking": False})

        if cmd == "play_context":
            return await self._play_context(params)

        if cmd == "take_over":
            if self._remote is None:
                return self.error_response("Nothing plays on another device")
            self._hold_remote(self.TAKE_OVER_HOLD)
            result = await self._send_api_command("transfer", timeout=self.TRANSFER_TIMEOUT)
            if not result.get("success"):
                self._release_remote_hold()
            return result

        if cmd == "set_shuffle":
            return await self._send_api_command("shuffle_context", {"shuffle_context": params.shuffle})

        if cmd == "set_repeat":
            for command, payload in repeat_posts(params.mode):
                result = await self._send_api_command(command, payload)
                if not result.get("success"):
                    return result
            return result

        return self.error_response(f"Unhandled command: {cmd}")

    async def _play_context(self, params: PlayContextParams) -> Dict[str, Any]:
        """Play a context on the account the daemon is signed in as.

        go-librespot keeps shuffle from the last context, but a play on a
        signed-in daemon with no session starts from a fresh state, shuffle off
        (measured 2026-10-04). So shuffle off is set before the play, and
        shuffle on after it — the one order that holds from both states. Where
        a shuffled play starts is `skip_to_uri`, which the browser picks at
        random from the listing on screen, as Music Library's does."""
        if self._account is None:
            return self.error_response("Spotify is not signed in")
        payload = {"uri": params.uri}
        if params.skip_to_uri:
            payload["skip_to_uri"] = params.skip_to_uri
        if not params.shuffle:
            result = await self._send_api_command("shuffle_context", {"shuffle_context": False})
            if not result.get("success"):
                return result
        result = await self._send_api_command("play", payload)
        if params.shuffle and result.get("success"):
            # The context plays either way: a shuffle that did not take shows
            # as off in the player, and is no failure of the play.
            shuffled = await self._send_api_command("shuffle_context", {"shuffle_context": True})
            if not shuffled.get("success"):
                self._logger.warning(f"Shuffle not applied after play: {shuffled.get('error')}")
        return result

    async def _seek_to(self, position_ms: int) -> Dict[str, Any]:
        """Seek, and move the anchor as soon as go-librespot took it: its own
        `seek` event is read back through /status after, within tolerance."""
        result = await self._send_api_command("seek", {"position": position_ms})
        if result.get("success"):
            self._anchor_position(position_ms)
        return result

    async def _handle_skip(self, params: SkipParams) -> Dict[str, Any]:
        """A seek by `params.seconds` from where go-librespot says the playhead
        is now (GET /status), or from this source's anchor when that read
        fails or names another track. One skip is handled at a time, so the
        next one reads where this one landed."""
        session = self._session
        position = self._position_now(session)
        status = await self._read_status()
        if (
            isinstance(status, LibrespotStatus)
            and status.track
            and status.track.get("uri") == session.uri
        ):
            position = status.track.get("position") or 0
        target = skip_target(position, params.seconds, session.track.get("duration_ms"))
        return await self._seek_to(target)

    # === Config Loading ===

    @handle_errors(default=False)
    async def _load_config(self) -> bool:
        """Load configuration from go-librespot config file."""
        if not self._config_path or not os.path.exists(self._config_path):
            self._logger.error(f"Config file not found: {self._config_path}")
            return False

        with open(self._config_path, 'r') as f:
            config = yaml.safe_load(f)

        server = config.get('server', {})
        addr = server.get('address', 'localhost')
        port = server.get('port', 3678)

        self._api_url = f"http://{addr}:{port}"
        self._ws_url = f"ws://{addr}:{port}/events"

        # Load auto-stop config from settings
        await self._load_auto_stop_config()

        self._logger.info(f"Config loaded: API={self._api_url}")
        return True

    async def _apply_managed_config(self) -> None:
        """Write the Milō-owned keys into go-librespot's config.yml.

        go-librespot parses its config once, at process start, so this runs from
        _do_start() before the unit is launched: whatever the settings page
        stored in the meantime is live on the next start, and there is no reload
        path to maintain. Only the four keys Milō owns are touched (crossfade and
        external_volume from settings, credentials and metadata constant); a
        start that would change nothing leaves the file alone, so the daemon
        never reads a file rewritten for nothing.

        Rewriting drops the baked comments from the deployed copy — their
        rationale lives in provisioning/go-librespot.sh, which is where it is read.

        Fails open: a config that cannot be patched still starts the daemon on
        whatever is on disk, rather than blocking Spotify entirely.
        """
        if not self._config_path or not os.path.exists(self._config_path):
            return

        managed = {
            "crossfade_duration": await self._get_crossfade_duration(),
            "external_volume": not await self._get_allow_app_volume(),
            **self.MANAGED_CONSTANT_CONFIG,
        }

        try:
            async with aiofiles.open(self._config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(await f.read()) or {}

            if all(config.get(key) == value for key, value in managed.items()):
                return

            config.update(managed)
            await self._write_config(config)
            self._logger.info(f"go-librespot config updated: {managed}")

        except Exception as e:
            self._logger.error(f"Failed to apply managed go-librespot config: {e}")

    async def _get_crossfade_duration(self) -> int:
        """Stored crossfade duration in ms; 0 (disabled) when unreadable.

        Never guess upwards on a missing setting — a fallback that enabled an
        audible effect nobody asked for would be indistinguishable from a bug.
        """
        if not self._settings_service:
            return 0

        value = await self._settings_service.get_setting(self.CROSSFADE_SETTINGS_KEY)
        return int(value) if value is not None else 0

    async def _get_allow_app_volume(self) -> bool:
        """Whether the Spotify app's slider may scale the samples; False
        (unity, CamillaDSP owns volume) when unreadable."""
        if not self._settings_service:
            return False

        return bool(await self._settings_service.get_setting(self.APP_VOLUME_SETTINGS_KEY))

    async def _write_config(self, config: Dict[str, Any]) -> None:
        """Replace config.yml atomically (temp file + os.replace).

        Same primitive as shared/persistence.py: the daemon must never be able
        to read a half-written config. No schema_version — this file belongs to
        go-librespot, not to Milō's versioned-persistence protocol.
        """
        path = Path(self._config_path)
        temp_file = path.with_name(f"{path.name}.{os.getpid()}.tmp")

        try:
            async with aiofiles.open(temp_file, 'w', encoding='utf-8') as f:
                await f.write(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
                await f.flush()
                os.fsync(f.fileno())

            os.replace(temp_file, path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp_file)

    async def on_spotify_settings_changed(self, apply_now: bool) -> bool:
        """Re-apply the managed config after a settings change (settings route).

        The value always reaches config.yml, so it is live at the next daemon
        start whatever happens here. `apply_now` additionally restarts the unit
        — what the settings page's "restart to apply" button asks for, and the
        only way to change crossfade or the app volume on a running daemon.

        A stopped daemon is left stopped: `systemctl restart` on an inactive
        unit STARTS it, which would raise a Spotify Connect speaker named after
        this house while another source plays, with no state-machine transition
        behind it — the source object never ran _do_start, so nothing monitors
        what that daemon then does. SpotifySettings.vue already only offers the
        button while spotify is the active source; this is the same rule where
        it belongs, since a route may not rely on a v-if.
        """
        await self._apply_managed_config()

        if not apply_now:
            return True

        return await self._submit(Result(self._restart_for_settings))

    async def _restart_for_settings(self) -> bool:
        """The restart itself, in the mailbox: it ends the session it restarts
        under (the user's own request, not a death)."""
        if not await self._is_service_active():
            self._logger.info("Spotify settings stored; go-librespot is stopped, they apply at its next start")
            return True

        if await self.end_session(EndReason.USER_STOP) is not None:
            self._publish()
        return await self._restart_service()

    # === Profiles ===

    async def switch_profile(self, username: str) -> Dict[str, Any]:
        """Sign the daemon in as a kept profile (the browser's profile screen)."""
        return await self._submit(Result(lambda: self._switch_profile(username)))

    async def forget_profile(self, username: str) -> Dict[str, Any]:
        """Drop a kept profile; the daemon stops being signed in as it."""
        return await self._submit(Result(lambda: self._forget_profile(username)))

    async def _switch_profile(self, username: str) -> Dict[str, Any]:
        credentials = self._profiles.credentials(username) if self._profiles else None
        if credentials is None:
            return self.error_response("Unknown Spotify profile")
        if username == self._account and not self._signing_in:
            return self.success_response()
        self._logger.info("Switching Spotify profile")
        await self._sign_in_as(username, credentials)
        return self.success_response()

    async def _forget_profile(self, username: str) -> Dict[str, Any]:
        if self._profiles is None or not await self._profiles.forget(username):
            return self.error_response("Unknown Spotify profile")
        self._harvested.discard(username)
        self._forget_history(username)
        self._logger.info(f"Spotify profile forgotten ({len(self._profiles)} stored)")
        await self._profiles_changed()
        if username in (self._persisted, await self._stored_account()):
            # Left in state.json, the daemon would sign back in as it, and the
            # next /status would keep it again.
            await self._sign_in_successor()
        return self.success_response()

    def _forget_history(self, account: str) -> None:
        """What a forgotten profile played goes with it: kept again, it starts
        afresh."""
        if self._history is not None and self._history.forget(account):
            self._bg.spawn(self._history.save(), label="spotify history")

    async def _sign_in_successor(self) -> None:
        """The signed-in profile is gone: the one signed in last on Milō takes
        its place, and nobody only when no profile is left — the browser then
        gives way to the card."""
        username = self._profiles.successor() if self._profiles is not None else None
        if username is None:
            await self._sign_in_as("", None)
            return
        self._logger.info("Signing in the Spotify profile signed in last")
        await self._sign_in_as(username, self._profiles.credentials(username))

    async def _sign_in_as(self, username: str, credentials: Optional[str]) -> None:
        """Hand go-librespot one account's credentials (an empty username:
        nobody, it waits for a phone). The daemon rewrites state.json itself,
        so the file is written between its stop and its start; a stopped
        daemon only gets the file, and reads it at its next start."""
        running = await self._is_service_active()
        if running:
            if await self.end_session(EndReason.USER_STOP) is not None:
                self._publish()
            await self._stop_service()
        await self._write_state_credentials(username, credentials)
        # What the daemon signs in as from now on — also when it is between two
        # runs of its own (an exit on refused credentials, then Restart=), and
        # the account it signs in as next is kept again (a forgotten one too).
        self._persisted = self._account = username or None
        self._harvested.clear()
        if self._account is None or not running:
            self._settle_signin()
        else:
            self._begin_signin()
        self._publish_changes()
        if running:
            await self._start_service()

    def _stored_credentials_refused(self, reason: str, line: str) -> None:
        """Spotify refused the stored account (a changed password, Premium
        gone): left in state.json, the daemon would exit on it at every start.
        The profile is forgotten — a new cast from the account brings it back —
        and the next one signs in. Runs on the journal task, so it posts."""
        self._report_login_failure(reason, line)
        account = self._persisted
        self._post(Result(lambda: self._drop_refused(account)))

    async def _drop_refused(self, account: Optional[str]) -> None:
        # The refused account is the one state.json held when the daemon
        # started — also when the sign-in watch had already given up on it.
        stored = await self._stored_account()
        account = account or stored
        if account is None or account not in (self._persisted, stored):
            # A switch or a first refusal already moved the daemon on.
            return
        if self._profiles is not None and await self._profiles.forget(account):
            self._harvested.discard(account)
            self._forget_history(account)
            self._logger.info(f"Refused Spotify profile forgotten ({len(self._profiles)} stored)")
            await self._profiles_changed()
        await self._sign_in_successor()

    # === /events ===

    async def _start_websocket(self) -> None:
        """Start WebSocket connection."""
        if not self._http or not self._ws_url:
            return

        self._ws_client = LibrespotWebSocket(
            ws_url=self._ws_url,
            session=self._http,
            on_event=self._on_librespot_event,
            on_connect=self._on_events_connected,
        )
        await self._ws_client.start()

    async def _on_librespot_event(self, event: Dict[str, Any]) -> None:
        """The /events callback: it runs on the client's task, so it posts.

        go-librespot wraps what an event says in `data`:
        {"type": "playing", "data": {"context_uri": …, "uri": …, "resume": false, …}}
        """
        self._post_feed(event)

    async def _on_events_connected(self) -> None:
        """Every (re)connection of /events: go-librespot emits events only on
        change, so an idle daemon after a restart would say nothing on its own."""
        self._post_feed({"type": "connected"})

    async def _handle_feed(self, events) -> None:
        """A burst of /events: one /status read, one reconcile, one publish.

        `inactive` needs no read — it is the daemon saying the session is over,
        and /status could fail right after it. Anything else sends Milō to look.
        """
        if self._http is None:
            return
        gone = False
        for event in events:
            kind = event.get("type")
            if kind == "inactive":
                gone = True
            elif kind in ("active", "connected"):
                gone = False
            if kind in ("queue", "connected"):
                # Said once: a failed /status read below must not lose it.
                self._order_stale = True
            if kind == "remote":
                self._announced_remote = event.get("data")
            if kind == "metadata":
                self._loaded_track = event.get("data") or {}
            elif kind == "playing" and self._loaded_track is not None:
                entry = history_entry(self._loaded_track, event.get("data") or {}, int(audio_source.wall_time() * 1000))
                if entry["uri"]:
                    self._pending_starts.append(entry)
                self._loaded_track = None
            if kind == "connected" and self._unanswered:
                # The daemon that did not answer at start does now.
                self.broadcast_error_cleared()
        status = None if gone else await self._read_status()
        if status is UNREADABLE:
            self._logger.warning("go-librespot status unavailable — keeping the session as it stands")
            if not self._timer_armed("status"):
                self._arm_timer("status", self.STATUS_RETRY_DELAY)
            return
        await self._apply_status(status)
        self._publish_changes()

    async def _on_timer(self, name: str, token: object) -> None:
        """`status`: the one re-read after a failed one — go-librespot emits an
        event only on change, so it will not re-announce what could not be
        read. `signin`: stored credentials that have not signed the daemon in."""
        if self._http is None:
            return
        if name == "signin":
            await self._signin_overdue()
            return
        if name == "remote_hold":
            # Nothing came in its place: show what /status says plays elsewhere.
            await self.refresh_metadata()
            return
        if name != "status":
            return
        status = await self._read_status()
        if status is UNREADABLE:
            self._logger.warning(
                "go-librespot status still unavailable after retry — "
                "leaving the session as it stands"
            )
            return
        await self._apply_status(status)
        self._publish_changes()

    async def _apply_status(self, status: Optional[LibrespotStatus]) -> None:
        """Make the session and the account match what /status said (None: a
        204, or the `inactive` that needs no read). The session first: ending
        it disarms the timers, and the sign-in watch armed below must outlive
        that end."""
        if status is None:
            # The order belonged to the session that ended.
            self._order, self._order_stale = PlayOrder(), True
            await self.reconcile(None)
        else:
            await self._apply_session(status)
        await self._follow_account(status)
        self._follow_remote(status)
        self._list_started_tracks()

    def _list_started_tracks(self) -> None:
        """List the tracks that started in the history, under the account this
        /status read named. The list is updated here; the file is written off
        the mailbox, and a write cut short is caught up by the next."""
        if not self._pending_starts or not self._account:
            return
        starts, self._pending_starts = self._pending_starts, []
        if self._history is not None:
            for entry in starts:
                self._history.add(self._account, entry)
            self._bg.spawn(self._history.save(), label="spotify history")

    async def _apply_session(self, status: LibrespotStatus) -> None:
        """Make the session match a /status that names one.

        A session opens at its first displayable track (E14): go-librespot
        reports a session before it knows the track, and a phone that picked
        the speaker without playing has nothing to draw.
        """
        titled = bool(status.track and status.track.get("name"))
        if not titled:
            session = self._session
            if session is None:
                return
            if None not in (session.sender, status.account) and session.sender != status.account:
                # Another account took the speaker and names no track yet: the
                # session on screen is over, the new one opens at its track.
                await self.reconcile(None)
                return
        session = await self.reconcile(DaemonSnapshot(status.account, self._phase_of(status)))
        if not isinstance(session, SpotifySession):
            return
        session.context_uri = status.context_uri or None
        session.context_name = status.context_name or None
        if status.track:
            content = self.transform_track_metadata(status.track)
            position = content.pop("position") or 0
            uri = status.track.get("uri")
            new_track = uri != session.uri
            session.track, session.uri = content, uri
            session.album_uri = status.track.get("album_uri") or None
            session.artists = artists_of(status.track)
            # Only with a track: a buffering read keeps the order around the
            # track still on screen. An order that does not surround this track
            # is no order for it, and with nothing around it there is no order
            # to swipe through.
            if self._order_stale or not self._order.surrounds(uri):
                await self._read_order()
            order = self._order
            if order.surrounds(uri) and (order.prev_tracks or order.next_tracks):
                session.queue = [
                    *(queue_entry(item.get("uri"), item.get("track")) for item in order.prev_tracks),
                    queue_entry(uri, status.track),
                    *(queue_entry(item.get("uri"), item.get("track")) for item in order.next_tracks),
                ]
                session.queue_index = len(order.prev_tracks)
            else:
                session.queue, session.queue_index = [], None
            if new_track:
                self._anchor_position(position)
            else:
                # A read of the same track: only a jump (a seek) moves the anchor.
                self._observe_position(position)

    def _follow_remote(self, status: Optional[LibrespotStatus]) -> None:
        """Follow what plays on another device, after the session and the
        account (ending a session disarms the source's timers, the hold's too).

        One rule keeps the bar up across a handover: the last remote stands
        while /status names nothing, until a session opens here or the hold
        started by what began the handover runs out (TAKE_OVER_HOLD). Whatever
        /status names is taken at once, held or not. A remote is shown once its
        track is named (go-librespot resolves the artists after the cluster
        names the track).
        """
        announced, self._announced_remote = self._announced_remote, None
        if self._session is not None:
            self._remote = None
            self._release_remote_hold()
            return
        arriving = bool(status and status.buffering and not (status.track and status.track.get("name")))
        named = status.remote if status is not None else None
        if named:
            self._release_remote_hold()
        elif announced:
            # A phone taking the session over: go-librespot announced it, then
            # went to sign in again, so /status cannot name it yet.
            named = announced
            self._hold_remote(self.HANDOVER_HOLD)
        elif arriving and self._remote is not None and not self._remote_held_until:
            # A session arriving here (a phone that picked Milō): what played
            # elsewhere is what is coming.
            self._hold_remote(self.ARRIVAL_HOLD)
        track = (named or {}).get("track")
        if track and track.get("name"):
            self._adopt_remote(named, track)
            return
        now = audio_source.wall_time()
        if now < self._remote_held_until:
            if not self._timer_armed("remote_hold"):
                self._arm_timer("remote_hold", self._remote_held_until - now)
            return
        self._remote = None
        if not arriving:
            # A hold that ran out during one arrival is not restarted by it.
            self._remote_held_until = 0.0

    def _adopt_remote(self, remote: Dict[str, Any], track: Dict[str, Any]) -> None:
        """Take a named remote. A read of the same playback keeps the anchor
        unless the playhead jumped, so a state request re-publishes nothing."""
        content = self.transform_track_metadata(track)
        position = content.pop("position") or 0
        paused = bool(remote.get("paused"))
        now = audio_source.wall_time()
        anchor = Anchor(ms=position, at=now, moving=not paused)
        previous = self._remote
        if (
            previous is not None
            and previous.track_uri == track.get("uri")
            and previous.paused == paused
            and abs(previous.anchor.now(now, content["duration_ms"]) - position) <= POSITION_TOLERANCE_MS
        ):
            anchor = previous.anchor
        self._remote = RemotePlayback(
            device_name=remote.get("device_name") or "",
            device_type=(remote.get("device_type") or "").lower(),
            track_uri=track.get("uri"),
            track=content,
            paused=paused,
            anchor=anchor,
        )

    def _hold_remote(self, seconds: float) -> None:
        now = audio_source.wall_time()
        self._remote_held_until = max(self._remote_held_until, now + seconds)
        self._arm_timer("remote_hold", self._remote_held_until - now)

    def _release_remote_hold(self) -> None:
        self._remote_held_until = 0.0
        self._disarm_timer("remote_hold")

    def _remote_view(self) -> Optional[SpotifyRemote]:
        remote = self._remote
        if remote is None or self._session is not None:
            return None
        return SpotifyRemote(
            device_name=remote.device_name,
            device_type=remote.device_type,
            title=remote.track["title"],
            artist=remote.track["artist"],
            album=remote.track["album"] or None,
            artwork=remote.track["artwork"] or None,
            track_uri=remote.track_uri,
            duration_ms=remote.track["duration_ms"],
            paused=remote.paused,
            position=PositionAnchor(ms=remote.anchor.ms, at=remote.anchor.at, rate=1.0),
        )

    # === The account ===

    async def _follow_account(self, status: Optional[LibrespotStatus]) -> None:
        """A /status that answers names the account. A 204 is nobody — unless
        state.json holds credentials: the daemon is then between a session end
        and its sign-in (0.25 s of 204, measured), and the account is the one
        it will sign back in as."""
        if status is not None:
            self._account = self._persisted = status.account
            self._shuffle, self._repeat = status.shuffle, status.repeat
            self._settle_signin()
            self._keep_profile(status.account)
            return
        self._shuffle, self._repeat = False, "off"
        self._account = self._persisted
        if self._account is None:
            self._settle_signin()
        elif not self._signing_in:
            self._begin_signin()

    def _begin_signin(self) -> None:
        self._signing_in = True
        self._arm_timer("signin", self.SIGNIN_TIMEOUT)

    def _settle_signin(self) -> None:
        self._signing_in = False
        self._signin_restarted = False
        self._disarm_timer("signin")

    async def _signin_overdue(self) -> None:
        """Stored credentials that have not signed the daemon in: restart it
        once — a sign-in hung for minutes once, and a restart signs in within
        a second (measured) — then give up, nobody signed in."""
        status = await self._read_status()
        if isinstance(status, LibrespotStatus):
            await self._apply_status(status)
            self._publish_changes()
            return
        if not self._signin_restarted:
            self._signin_restarted = True
            self._logger.warning(
                f"go-librespot did not sign in within {self.SIGNIN_TIMEOUT:.0f} s — restarting it"
            )
            self._arm_timer("signin", self.SIGNIN_TIMEOUT)
            self._harvested.clear()
            await self._restart_service()
            return
        self._logger.warning("go-librespot still not signed in after a restart — waiting for a phone")
        self._account = self._persisted = None
        self._settle_signin()
        self._publish_changes()

    def _keep_profile(self, account: Optional[str]) -> None:
        """Keep the signed-in account's credentials and when it signed in,
        once per daemon run. Off the mailbox: it touches only the profiles,
        never the source."""
        if self._profiles is None or not account or account in self._harvested:
            return
        self._harvested.add(account)
        self._bg.spawn(self._harvest(account), label="spotify profile")

    async def _harvest(self, account: str) -> None:
        signed_in_at = audio_source.wall_time()
        credentials = await self._read_state_credentials()
        if credentials.get("username") != account or not credentials.get("data"):
            # Measured: the file is written before /status names the account.
            self._logger.warning("Signed-in account not found in go-librespot's state — profile not kept")
            return
        if account not in self._harvested:
            # Forgotten while the file was read: kept again, it would come back.
            return
        if await self._profiles.harvest(account, credentials["data"], signed_in_at):
            self._logger.info(f"Spotify profile kept ({len(self._profiles)} stored)")
            await self._profiles_changed()
        # Read again at every sign-in (once per daemon run): a picture or a
        # name changed in the Spotify app reaches the profile screen. A failed
        # read keeps what was kept.
        identity = await self._library.fetch_profile(account)
        if identity is not None and await self._profiles.set_identity(account, identity):
            await self._profiles_changed()

    async def _profiles_changed(self) -> None:
        """The browser's copy of the profiles: nothing else tells it one came,
        changed or went."""
        if self.state_machine:
            await self.state_machine.broadcast(
                SpotifyProfilesChanged(profiles=self._profiles.list())
            )

    async def _read_state_credentials(self) -> Dict[str, Any]:
        """state.json's `credentials`, or {} when it cannot be read."""
        if not self._config_path:
            return {}
        path = Path(self._config_path).parent / self.STATE_FILE
        try:
            async with aiofiles.open(path, "r", encoding="utf-8") as f:
                return json.loads(await f.read()).get("credentials") or {}
        except (OSError, ValueError, AttributeError):
            return {}

    async def _write_state_credentials(self, username: str, data: Optional[str]) -> None:
        """Put `username`'s credentials in state.json (empty: nobody), keeping
        everything else go-librespot keeps there. Only while the daemon is
        stopped: it rewrites the file itself."""
        path = Path(self._config_path).parent / self.STATE_FILE
        try:
            async with aiofiles.open(path, "r", encoding="utf-8") as f:
                state = json.loads(await f.read())
        except FileNotFoundError:
            state = {}
        except (OSError, ValueError) as e:
            # go-librespot regenerates what is lost (a new device id, the
            # volume back to its default): phones see a new Milō once.
            self._logger.warning(f"go-librespot state.json unreadable ({type(e).__name__}), rewritten with credentials only")
            state = {}
        state["credentials"] = {"username": username, "data": data}
        await write_bytes_atomically(path, json.dumps(state).encode("utf-8"), private=True)

    async def _stored_account(self) -> Optional[str]:
        """The account whose credentials go-librespot keeps in state.json, or
        None. go-librespot replaces the file atomically (measured), so a read
        never meets half a file; a missing or unreadable one is nobody."""
        credentials = await self._read_state_credentials()
        if credentials.get("username") and credentials.get("data"):
            return credentials["username"]
        return None

    @staticmethod
    def _phase_of(status: LibrespotStatus) -> Phase:
        if status.buffering:
            return Phase.LOADING
        if status.paused or not status.track:
            return Phase.PAUSED
        return Phase.PLAYING

    def _daemon_session(self, snapshot: DaemonSnapshot) -> Session:
        return SpotifySession(phase=snapshot.phase, sender=snapshot.sender)

    # === Metadata ===

    @staticmethod
    def transform_track_metadata(track: dict) -> dict:
        """Transform a go-librespot track dict into Milo's metadata format.

        Single source of truth for the go-librespot → Milo field mapping.
        The playhead is `position`, which the caller takes out for the anchor.
        """
        return {
            "title": track.get("name"),
            "artist": ", ".join(track.get("artist_names", [])) or None,
            "album": track.get("album_name"),
            "artwork": track.get("album_cover_url"),
            "duration_ms": track.get("duration") or None,
            "position": track.get("position", 0),
        }

    # === REST API ===

    async def _wait_for_playback_ready(self, timeout: float = 10.0, interval: float = 0.25) -> bool:
        """Poll GET / until go-librespot's API is reachable, capped at `timeout`.

        Replaces the previous fixed sleep(0.5) in startup so the WS connect and
        first /status only run once the daemon is actually listening. We gate on
        API reachability (HTTP 200), not the `playback_ready` flag: at start
        the daemon is either signing in with stored credentials or, with none,
        waiting for a phone, so the flag is false either way — reachability is
        the signal the startup path actually needs. Falls back to proceeding
        after the cap so a slow/unreachable daemon can't wedge startup (the WS
        loop reconnects).
        """
        if not self._http or not self._api_url:
            return False

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with contextlib.suppress(
                aiohttp.ClientConnectorError,
                aiohttp.ClientOSError,
                asyncio.TimeoutError,
            ):
                async with self._http.get(f"{self._api_url}/") as resp:
                    if resp.status == 200:
                        ready = (await resp.json()).get("playback_ready")
                        self._logger.info(
                            f"go-librespot API ready (playback_ready={ready})"
                        )
                        return True
            await asyncio.sleep(interval)

        self._logger.warning(
            f"go-librespot API not reachable within {timeout}s; proceeding anyway"
        )
        return False

    async def _read(self, path: str, what: str) -> Union[Tuple[int, Any], _Unreadable]:
        """GET one of go-librespot's routes: its HTTP status and its JSON body
        (None unless 200), or UNREADABLE — the one failure rule (E10): a read
        that failed learned nothing, and nothing is changed on it."""
        if not self._http or not self._api_url:
            return UNREADABLE
        try:
            async with self._http.get(f"{self._api_url}{path}") as resp:
                if resp.status != 200:
                    return resp.status, None
                return 200, await resp.json()
        except (aiohttp.ClientConnectorError, aiohttp.ClientOSError, asyncio.TimeoutError):
            self._logger.debug(f"{what} read skipped: go-librespot not reachable")
            return UNREADABLE
        except Exception as e:
            self._logger.error(f"{what} read failed: {e}")
            return UNREADABLE

    async def _read_status(self) -> Union[LibrespotStatus, None, _Unreadable]:
        """GET /status: a session, None (204: no phone holds the speaker), or
        UNREADABLE."""
        answer = await self._read("/status", "Status")
        if answer is UNREADABLE:
            return UNREADABLE
        code, data = answer
        if code == 204:
            return None
        if not isinstance(data, dict):
            return UNREADABLE

        return LibrespotStatus(
            account=data.get("username"),
            track=data.get("track"),
            paused=bool(data.get("paused", True)),
            buffering=bool(data.get("buffering", False)),
            context_uri=data.get("context_uri") or None,
            context_name=data.get("context_name") or None,
            shuffle=bool(data.get("shuffle_context", False)),
            repeat=repeat_mode(bool(data.get("repeat_context")), bool(data.get("repeat_track"))),
            remote=data.get("remote"),
        )

    async def _read_order(self) -> None:
        """GET /player/queue: the play order around the track. A 204 (no
        session) lists nothing, and a 404 (stock 0.10.3) says the daemon has no
        order to give for the rest of its run. A read that fails keeps the
        order stale: the status retry reads it again once, then the daemon's
        next event does."""
        if not self._order_route:
            return
        answer = await self._read("/player/queue", "Queue")
        code, data = (None, None) if answer is UNREADABLE else answer
        if code == 404:
            self._order_route = False
            self._order, self._order_stale = PlayOrder(), False
        elif code == 204:
            self._order, self._order_stale = PlayOrder(), False
        elif code == 200 and isinstance(data, dict):
            self._order, self._order_stale = play_order(data), False
        else:
            if code is not None:
                self._logger.warning(f"go-librespot queue read answered {code} — keeping the last order")
            self._order_stale = True
            if not self._order_retried and not self._timer_armed("status"):
                self._order_retried = True
                self._arm_timer("status", self.STATUS_RETRY_DELAY)
            return
        self._order_retried = False

    async def refresh_metadata(self) -> bool:
        """A state request (GET /api/audio/state): read /status, follow it, and
        hand the state machine the current record, playhead included."""
        status = await self._read_status()
        if status is UNREADABLE:
            return False
        await self._apply_status(status)
        self._publish_changes()
        return True

    async def _send_api_command(
        self,
        command: str,
        payload: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Send command to go-librespot API. `timeout` overrides the session's
        3 s for a call go-librespot answers only once Spotify has."""
        if not self._http or not self._api_url:
            return self.error_response("Session not active")

        extra = {"timeout": aiohttp.ClientTimeout(total=timeout)} if timeout else {}
        try:
            async with self._http.post(
                f"{self._api_url}/player/{command}",
                json=payload or {},
                **extra,
            ) as resp:
                return self.success_response() if resp.status == 200 else self.error_response("Command failed")

        except Exception as e:
            return self.error_response(str(e))

    # === Log Monitor ===

    def _start_log_monitor(self) -> None:
        """Start monitoring journalctl logs for go-librespot errors."""
        if self._log_monitor_task:
            return
        self._log_monitor_task = asyncio.create_task(self._monitor_logs())

    def _stop_log_monitor(self) -> None:
        """Stop log monitoring."""
        if self._log_monitor_task:
            self._log_monitor_task.cancel()
            self._log_monitor_task = None

    async def _monitor_logs(self) -> None:
        """Monitor journalctl for go-librespot errors."""
        try:
            async for line in follow_unit(
                "milo-spotify",
                consequence="go-librespot error reporting is down until the source is restarted",
                logger=self._logger,
            ):
                # Per background-loop doctrine: a transient parse/broadcast
                # error on one line must not kill the whole monitor.
                try:
                    await self._handle_log_line(line)
                except Exception as e:
                    self._logger.error(f"Log line handling error: {e}")

        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._logger.error(f"Log monitor error: {e}")

    async def _handle_log_line(self, line: str) -> None:
        """Parse and handle a log line from go-librespot."""
        # The accesspoint is back. A refused login is not answered by it: a
        # Connect attempt authenticates the AP, then fails login5 10 ms later.
        if "authenticated AP" in line:
            self._connection_error_count = 0
            if self._standing_reason not in LOGIN_FAILURES:
                self.broadcast_error_cleared()
            return

        # Success: logged in, a sender accepted, or a track loaded
        if any(m in line for m in ("authenticated Login5", "accepted zeroconf", "loaded track")):
            self.broadcast_error_cleared()
            self._connection_error_count = 0
            self._last_login_failure = None
            return

        # login5 failing transiently: go-librespot retries for as long as the
        # login waits — at a Connect, until Spotify answers — so no session
        # line follows the outage. Each retry is its report.
        if "login5 request failed, retrying" in line:
            self._report_login_failure(
                login_failure(line) or SourceErrorReason.PROVIDER_UNAVAILABLE, line
            )
            return

        # The stored account refused, at a start or a sign-in after a session end
        if "with stored credentials" in line and login_failure(line) in ACCOUNT_REFUSALS:
            self._stored_credentials_refused(login_failure(line), line)
            return

        # A Connect attempt from the Spotify app was refused
        if "failed creating new session" in line:
            self._report_login_failure(
                login_failure(line) or SourceErrorReason.CONNECTION_REFUSED, line
            )
            return

        # Critical error: track loading failed — a login5 outage mid-session lands here
        if "failed loading current track" in line:
            reason = login_failure(line)
            if reason:
                self._report_login_failure(reason, line)
                return
            self._logger.error(self._extract_log_message(line))
            self.broadcast_error(SourceErrorReason.TRACK_LOAD_FAILED)
            return

        # Connection failures — accesspoint unreachable (running) or apresolve
        # down (boot: the daemon exits on it). Broadcast after 3 consecutive
        # failures within 60s: long enough to cover the ~5-15s systemd restart
        # cadence, short enough to stay tied to a real outage.
        if "failed connecting to accesspoint" in line or "failed getting endpoints from resolver" in line:
            if login_failure(line) in ACCOUNT_REFUSALS:
                return  # the session line that follows names the refusal
            now = time.time()
            if now - self._last_error_time < 60:
                self._connection_error_count += 1
            else:
                self._connection_error_count = 1
            self._last_error_time = now

            if self._connection_error_count >= 3:
                self._logger.error(self._extract_log_message(line))
                self.broadcast_error(SourceErrorReason.SERVICE_UNREACHABLE)
                self._connection_error_count = 0

        # Ignore normal WebSocket closures (StatusNormalClosure)
        # These are expected when stopping the service

    # Covers both retry cadences: the app's ~3 s, and login5's backoff, capped
    # at 60 s with ±50 % jitter.
    LOGIN_RETRY_WINDOW_S = 120.0

    def _report_login_failure(self, reason: str, line: str) -> None:
        """Every retry re-sends the banner; only the first is logged at error.

        Re-sent because the banner is not part of the state: a page loaded
        after the first refusal never received it (measured — the retries were
        held back and the reloaded page showed nothing). Logged at warning
        after the first, because an error line reaches the screen as a second,
        raw backend banner.
        """
        now = time.monotonic()
        previous, self._last_login_failure = self._last_login_failure, (reason, now)
        message = self._extract_log_message(line)
        if previous and previous[0] == reason and now - previous[1] < self.LOGIN_RETRY_WINDOW_S:
            self._logger.warning(message)
        else:
            self._logger.error(message)
        self.broadcast_error(reason)

    def _extract_log_message(self, line: str) -> str:
        """
        Extract the msg and error fields from a go-librespot log line.

        Log format: level=warning msg="..." error="..."
        Returns the message exactly as it appears in the logs.
        """
        msg_match = re.search(r'msg="([^"]+)"', line)
        msg = msg_match.group(1) if msg_match else ""

        # Extract error="..." (may not exist)
        error_match = re.search(r'error="([^"]+)"', line)
        error = error_match.group(1) if error_match else ""

        if error:
            return f'{msg}: {error}'
        return msg if msg else "Unknown error"

    # === Helpers ===

    async def _cleanup(self) -> None:
        """Clean up resources."""
        self._disarm_timer("status")
        self._settle_signin()
        self._account = self._persisted = None
        # What played elsewhere belongs to this daemon run: the next one reads
        # its own before the bar shows anything.
        self._remote = self._announced_remote = None
        self._order, self._order_stale, self._order_route = PlayOrder(), True, True
        self._order_retried = False
        self._loaded_track, self._pending_starts = None, []
        self._release_remote_hold()
        self._shuffle, self._repeat = False, "off"
        self._harvested.clear()
        await self._library.close()
        self._stop_log_monitor()

        if self._ws_client:
            await self._ws_client.stop()
            self._ws_client = None

        if self._http:
            await self._http.close()
            self._http = None

        # What /events posted and nobody handled belongs to this daemon run.
        self._discard_feed()
        # The daemon that did not answer, or refused a login, is gone with it:
        # so is what it put up.
        if self._unanswered or self._standing_reason in (
            *LOGIN_FAILURES, SourceErrorReason.SERVICE_UNREACHABLE,
        ):
            self.broadcast_error_cleared()
        self._last_login_failure = None

    def broadcast_error(self, reason: str) -> None:
        # The banner standing is no longer the unanswered start's: /events
        # connecting later must not withdraw this one.
        self._unanswered = False
        self._standing_reason = reason
        super().broadcast_error(reason)

    def broadcast_error_cleared(self) -> None:
        self._unanswered = False
        self._standing_reason = None
        super().broadcast_error_cleared()

    # === The view (docs: "le fil") ===

    def _session_fields(self, session: SpotifySession) -> Dict[str, Any]:
        # The account is an identity, not a name to show: `senders` stays empty.
        return dict(session.track)

    def _details(self) -> SpotifyDetails:
        session = self._session if isinstance(self._session, SpotifySession) else None
        return SpotifyDetails(
            account=self._account,
            signing_in=self._signing_in,
            context_uri=session.context_uri if session else None,
            context_name=session.context_name if session else None,
            track_uri=session.uri if session else None,
            album_uri=session.album_uri if session else None,
            artists=session.artists if session else [],
            queue=session.queue if session else [],
            queue_index=session.queue_index if session else None,
            shuffle=self._shuffle,
            repeat=self._repeat,
            remote=self._remote_view(),
        )

    def _controls(self) -> List[str]:
        session = self._session
        if session is None:
            return ["take_over"] if self._remote is not None else []
        if session.phase is Phase.LOADING:
            return ["pause", "next", "prev", "set_shuffle", "set_repeat"]
        if session.phase is Phase.PAUSED:
            return ["resume", "seek", "skip", "next", "prev", "set_shuffle", "set_repeat"]
        return ["pause", "seek", "skip", "next", "prev", "set_shuffle", "set_repeat"]
