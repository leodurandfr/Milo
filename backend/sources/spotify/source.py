# backend/sources/spotify/source.py
"""
Spotify Connect via go-librespot.

The session belongs to go-librespot, not to Milō: a phone picks the speaker,
plays, pauses and leaves on its own. The source follows it through
`reconcile()` (docs: source architecture, "reconcile"), and one thing decides
where the session stands: go-librespot's own GET /status, read once after
every burst of /events. An event is when to look, never what to believe — two
writers (the event and /status) is how the screen said "playing" while the
auto-stop counted down a pause (E11). Measured on go-librespot 0.10.0
(2026-09-24, an iPhone), which the phase follows:

- /status answers 204 when no phone holds the speaker, 200 otherwise; between
  `will_play` and `metadata` it reports `buffering` and no track yet.
- A transfer loads the track paused at the phone's position and plays 1.6 s
  later; a skip is 70 ms of loading; a track's end is `not_playing` and a
  paused /status for 250-350 ms before the next one (autoplay never lets a
  context end).
- POST /player/stop ends the session (`inactive`, /status 204) and the phone
  lets go: the idle timeout's REQUEST_END. A phone picking another output says
  the same.
- A killed daemon says nothing; its session ends when its process does (the
  base's pidfd watch).

Commands go through POST /player/<cmd>; no routes.py.
"""
import asyncio
from backend.core.models.ws_events import SourceErrorReason
import contextlib
import os
import re
import time
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import aiofiles
import aiohttp
from pydantic import BaseModel

from backend.core.audio_source import BaseAudioSource, Result
from backend.core.models.audio_state import NetworkRequirement
from backend.core.models.session import (
    CommandScope, DaemonSnapshot, EndReason, IdlePolicy, Phase, ResumePolicy, ReroutePolicy,
    Session,
)
from backend.core.models.commands import SkipParams, skip_target
from backend.sources.spotify.models import SeekParams, NextPrevParams
from backend.sources.spotify.websocket import LibrespotWebSocket
from backend.shared.decorators import handle_errors
from backend.shared.journalctl import follow_unit


@dataclass(frozen=True)
class LibrespotStatus:
    """What GET /status said: whose session it is, and where it stands."""
    account: Optional[str]
    track: Optional[Dict[str, Any]]
    paused: bool
    buffering: bool


class _Unreadable:
    """/status could not be read: nothing was learned."""


UNREADABLE = _Unreadable()


@dataclass(eq=False)
class SpotifySession(Session):
    """One Connect session: the track on screen (its playhead is the
    session's anchor)."""
    track: Dict[str, Any] = field(default_factory=dict)
    uri: Optional[str] = None


class SpotifySource(BaseAudioSource):
    """
    Spotify audio source using go-librespot.

    Family C (active player): controlled from Milō's UI via go-librespot
    WebSocket. No dedicated routes.py — commands flow through the generic
    `/api/audio/control/spotify` endpoint. Extends BaseAudioSource.
    """

    NETWORK_REQUIREMENT = NetworkRequirement.INTERNET

    IDLE_POLICY = IdlePolicy.REQUEST_END
    # go-librespot reopens its output in place (POST /player/output): a
    # multiroom toggle moves the writer and keeps the session.
    REROUTE = ReroutePolicy.KEEP_SESSION
    # Milō cannot start a Connect session: nothing is kept.
    RESUME_POLICY = ResumePolicy(capture_on=frozenset(), forget_on=frozenset(EndReason))
    SESSION_DAEMON = True

    # The two go-librespot config keys Milō owns: crossfade_duration and
    # external_volume. Every other key in config.yml (device_name,
    # zeroconf_backend, server) is written once by provisioning/go-librespot.sh
    # and never touched here.
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

    async def _do_start(self) -> bool:
        """Start go-librespot service and WebSocket."""
        try:
            # 1. Load config (sets _api_url / _ws_url)
            if not await self._load_config():
                return False

            # 1b. Push Milō-owned keys into config.yml BEFORE the daemon reads
            # it — go-librespot parses its config once, at process start.
            await self._apply_managed_config()

            # 2. Start the service (readiness is polled below, not slept on)
            if not await self._start_service():
                return False

            # 3. HTTP session. Bounded per-request timeout so an unresponsive
            # daemon can't block /player/stop or the startup poll. The WS
            # connect passes its own timeout, so the long-lived /events stream
            # is unaffected.
            self._http = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=3.0)
            )

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
        "next": NextPrevParams,
        "prev": NextPrevParams,
    }
    COMMAND_SCOPES = {name: CommandScope.SESSION for name in COMMANDS}

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

        if cmd in ["next", "prev"]:
            payload = {"uri": params.uri} if params.uri else {}
            return await self._send_api_command(cmd, payload)

        return self.error_response(f"Unhandled command: {cmd}")

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
        path to maintain. Only the crossfade and external_volume values are
        touched; a start that would change nothing leaves the file alone, so the
        daemon never reads a file rewritten for nothing.

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

        go-librespot sends flat events (fields at root level, no "data" wrapper):
        {"type": "seek", "position": 12345, "uri": "spotify:track:..."}
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
        """The one re-read after a failed one. go-librespot emits an event only
        on change, so it will not re-announce what could not be read."""
        if name != "status" or self._http is None:
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
        """Make the session match what /status said.

        A session opens at its first displayable track (E14): go-librespot
        reports a session before it knows the track, and a phone that picked
        the speaker without playing has nothing to draw.
        """
        if status is None:
            await self.reconcile(None)
            return
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
        if status.track:
            content = self.transform_track_metadata(status.track)
            position = content.pop("position") or 0
            uri = status.track.get("uri")
            new_track = uri != session.uri
            session.track, session.uri = content, uri
            if new_track:
                self._anchor_position(position)
            else:
                # A read of the same track: only a jump (a seek) moves the anchor.
                self._observe_position(position)

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
        API reachability (HTTP 200), not the `playback_ready` flag: in Milō's
        zeroconf setup no Connect session exists at start time (start is
        triggered by UI selection, before a phone selects the device), so the
        flag stays false until a phone connects — reachability is the signal the
        startup path actually needs. Falls back to proceeding after the cap so a
        slow/unreachable daemon can't wedge startup (the WS loop reconnects).
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

    async def _read_status(self) -> Union[LibrespotStatus, None, _Unreadable]:
        """GET /status: a session, None (204: no phone holds the speaker), or
        UNREADABLE — the one failure rule (E10): a read that failed learned
        nothing, and nothing is changed on it."""
        if not self._http or not self._api_url:
            return UNREADABLE

        try:
            async with self._http.get(f"{self._api_url}/status") as resp:
                if resp.status == 204:
                    return None
                if resp.status != 200:
                    return UNREADABLE
                data = await resp.json()
        except (aiohttp.ClientConnectorError, aiohttp.ClientOSError, asyncio.TimeoutError):
            self._logger.debug("Status read skipped: go-librespot not reachable")
            return UNREADABLE
        except Exception as e:
            self._logger.error(f"Status read failed: {e}")
            return UNREADABLE

        return LibrespotStatus(
            account=data.get("username"),
            track=data.get("track"),
            paused=bool(data.get("paused", True)),
            buffering=bool(data.get("buffering", False)),
        )

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
        payload: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Send command to go-librespot API."""
        if not self._http or not self._api_url:
            return self.error_response("Session not active")

        try:
            async with self._http.post(
                f"{self._api_url}/player/{command}",
                json=payload or {}
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
                consequence="go-librespot error reporting is down",
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
        # Success: connection established - clear any error
        if "authenticated AP" in line or "authenticated Login5" in line:
            self.broadcast_error_cleared()
            self._connection_error_count = 0
            return

        # Success: track loaded - clear any error
        if "loaded track" in line:
            self.broadcast_error_cleared()
            return

        # Critical error: track loading failed
        if "failed loading current track" in line:
            self._logger.error(self._extract_log_message(line))
            self.broadcast_error(SourceErrorReason.TRACK_LOAD_FAILED)
            return

        # Connection failures — accesspoint unreachable (running) or zeroconf /
        # apresolve down (boot). Broadcast after 3 consecutive failures within
        # 60s: long enough to cover the ~5-15s systemd restart cadence on
        # zeroconf crashes, short enough to stay tied to a real outage.
        if "failed connecting to accesspoint" in line or "failed running zeroconf" in line:
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
        self._stop_log_monitor()

        if self._ws_client:
            await self._ws_client.stop()
            self._ws_client = None

        if self._http:
            await self._http.close()
            self._http = None

        # What /events posted and nobody handled belongs to this daemon run.
        self._discard_feed()
        # The daemon that did not answer is gone with it: so is what it put up.
        if self._unanswered:
            self.broadcast_error_cleared()

    def broadcast_error(self, reason: str) -> None:
        # The banner standing is no longer the unanswered start's: /events
        # connecting later must not withdraw this one.
        self._unanswered = False
        super().broadcast_error(reason)

    def broadcast_error_cleared(self) -> None:
        self._unanswered = False
        super().broadcast_error_cleared()

    # === The view (docs: "le fil") ===

    def _session_fields(self, session: SpotifySession) -> Dict[str, Any]:
        # The account is an identity, not a name to show: `senders` stays empty.
        return dict(session.track)

    def _controls(self) -> List[str]:
        session = self._session
        if session is None:
            return []
        if session.phase is Phase.LOADING:
            return ["pause", "next", "prev"]
        if session.phase is Phase.PAUSED:
            return ["resume", "seek", "skip", "next", "prev"]
        return ["pause", "seek", "skip", "next", "prev"]
