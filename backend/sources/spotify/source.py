# backend/sources/spotify/source.py
"""
Spotify audio source using go-librespot.

This source handles streaming audio from Spotify Connect via go-librespot.
It provides real-time metadata updates through WebSocket connection and
supports playback control via REST API.

Features:
- WebSocket for real-time events (playing, paused, metadata, etc.)
- REST API for playback commands (play, pause, seek, etc.)
- Auto-stop timer after pause (configurable)
- Metadata tracking with album art and position
"""
import asyncio
import contextlib
import os
import re
import time
import yaml
from pathlib import Path
from typing import Dict, Any, Optional

import aiofiles
import aiohttp
from pydantic import BaseModel

from backend.core.audio_source import BaseAudioSource
from backend.core.models.source_metadata import PlaybackMetadata
from backend.sources.spotify.models import SeekParams, NextPrevParams
from backend.sources.spotify.websocket import LibrespotWebSocket
from backend.shared.decorators import handle_errors
from backend.shared.journalctl import follow_unit


class SpotifySource(BaseAudioSource):
    """
    Spotify audio source using go-librespot.

    Family C (active player): controlled from Milō's UI via go-librespot
    WebSocket. No dedicated routes.py — commands flow through the generic
    `/api/audio/control/spotify` endpoint. Extends BaseAudioSource.
    """

    # The one go-librespot config key Milō owns. Every other key in config.yml
    # (device_name, zeroconf_backend, server, external_volume) is written once
    # by install/go-librespot.sh and never touched here.
    #
    # Deliberately NOT owned: flac_enabled. go-librespot 0.8.0 fixed the FLAC
    # decoder (it normalised samples by 2^bps instead of 2^(bps-1), so lossless
    # played 6 dB too quiet), but the released binaries still refuse to boot
    # with the flag on — "fatal: FLAC playback requires a PlapPlay
    # implementation" (measured on the unit, 2026-08-03). It is a fatal
    # misconfiguration, not an inert flag: enabling it costs Spotify entirely.
    CROSSFADE_SETTINGS_KEY = "spotify.crossfade_duration"

    # Neutral sink the output is parked on while a multiroom reroute reconciles
    # snapcast. ALSA's `null` discards samples as fast as they are written, so
    # playback MUST be paused before switching to it: measured on the unit
    # (2026-08-03), an unpaused switch ran a track from 1'10" to its end and
    # into the next one in about two seconds.
    RELEASE_DEVICE = "null"

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

        self._session: Optional[aiohttp.ClientSession] = None

        self._ws_client: Optional[LibrespotWebSocket] = None

        # State
        self._metadata: Dict[str, Any] = {}
        self._is_playing = False
        self._device_connected = False
        self._ws_connected = False

        # Auto-stop (uses BaseAudioSource timer infrastructure)
        self.auto_stop_enabled = True
        self.auto_stop_delay = 10.0

        # Multiroom reroute: whether the release went through /player/output
        # (session kept) or fell back to a full stop, and what to restore.
        self._soft_reroute = False
        self._reroute_was_playing = False

        # Log monitor for error detection
        self._log_monitor_task: Optional[asyncio.Task] = None
        self._connection_error_count = 0
        self._last_error_time = 0.0

    def _reset_playback_state(self) -> None:
        super()._reset_playback_state()
        self._device_connected = False

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

            # 3. Reset state
            self._reset_playback_state()
            self._cancel_pause_timer()

            # 4. Create HTTP session. Bounded per-request timeout so an
            # unresponsive daemon can't block /player/stop or the startup poll.
            # The WS connect passes its own timeout, so the long-lived /events
            # stream is unaffected.
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=3.0)
            )

            # 5. Wait until the daemon's API is reachable before connecting
            await self._wait_for_playback_ready()

            # 6. Start WebSocket
            await self._start_websocket()

            # 7. Start log monitor for error detection
            self._start_log_monitor()

            # 8. Update state
            self._update_connection_state()

            return True

        except Exception as e:
            self._logger.error(f"Start failed: {e}")
            await self._cleanup()
            return False

    async def _do_stop(self) -> bool:
        """Stop Spotify gracefully, then stop the service.

        POST /player/stop first to disconnect the Connect session and release
        the ALSA Loopback in-process, so the next source can grab it without
        waiting; then cleanup and `systemctl stop`. go-librespot exits gracefully
        on SIGTERM (0.8.0: 3.8ms on a live playing session, measured on the Pi
        2026-08-03), with TimeoutStopSec=5 as a backstop.

        A /player/stop failure must NOT block the service stop — log + continue.
        """
        result = await self._send_api_command("stop")
        if not result.get("success"):
            self._logger.warning(
                f"/player/stop failed before service stop: {result.get('error')}"
            )

        await self._cleanup()
        return await self._stop_service()

    async def _on_auto_stop(self) -> None:
        """Auto-stop after the pause delay (Spotify stays the selected source).

        End the idle Connect session via POST /player/stop instead of bouncing
        the process: the daemon stays alive and advertised for an instant
        reconnect, while the resulting `inactive` WS event drives Spotify back
        to WAITING — behaviorally equal to the old post-restart state, minus the
        SIGTERM bounce.
        """
        result = await self._send_api_command("stop")
        if not result.get("success"):
            self._logger.warning(f"Auto-stop /player/stop failed: {result.get('error')}")

    # === Multiroom reroute ===

    async def release_for_reroute(self) -> bool:
        """Free the ALSA device for a MILO_MODE change, keeping the session.

        go-librespot 0.8.0 reopens its output in place via POST /player/output,
        preserving the Connect session, track, position and volume — so a
        multiroom toggle no longer has to bounce the daemon and send the phone
        looking for the speaker again.

        Pause first, and wait for the daemon to confirm it: RELEASE_DEVICE does
        not rate-limit, so an unpaused switch races through the rest of the
        track. Any step that does not answer falls back to the base stop(): a
        source still holding the loopback would block snapclient, which is the
        one outcome worse than a dropped session.
        """
        self._soft_reroute = False
        self._reroute_was_playing = False

        if not self._session or not self._api_url:
            return await super().release_for_reroute()

        # Ground truth before pausing rather than the cached flag: a stale
        # _is_playing=False (WS dropped mid-playback) would skip the pause and
        # hand a live stream to a sink that does not rate-limit.
        if not await self.refresh_metadata():
            self._logger.warning("Reroute: daemon unreachable, falling back to a full stop")
            return await super().release_for_reroute()

        self._reroute_was_playing = self._is_playing

        if self._reroute_was_playing and not await self._pause_and_confirm():
            self._logger.warning("Reroute: pause unconfirmed, falling back to a full stop")
            return await super().release_for_reroute()

        result = await self._send_api_command("output", {"device": self.RELEASE_DEVICE})
        if not result.get("success"):
            self._logger.warning(
                f"Reroute: releasing the output failed ({result.get('error')}), "
                "falling back to a full stop"
            )
            return await super().release_for_reroute()

        self._soft_reroute = True
        self._logger.info("Reroute: output parked, Connect session kept")
        return True

    async def acquire_after_reroute(self) -> bool:
        """Reopen the output on the device the new MILO_MODE selects.

        The device name is explicit rather than the `milo_spotify` alias: that
        alias resolves MILO_MODE from the daemon's own environment, frozen when
        it started, so with no restart it would still name the old mode.

        _apply_transition left the source in STARTING and nothing else clears it
        without the usual start(), hence the final state emission here.
        """
        if not self._soft_reroute:
            return await super().acquire_after_reroute()

        self._soft_reroute = False
        device = self._output_device_for_mode()

        result = await self._send_api_command("output", {"device": device})
        if not result.get("success"):
            self._logger.warning(
                f"Reroute: reopening on {device} failed ({result.get('error')}), "
                "restarting the source"
            )
            return await super().acquire_after_reroute()

        if self._reroute_was_playing:
            resumed = await self._send_api_command("resume")
            if not resumed.get("success"):
                self._logger.warning(f"Reroute: resume failed: {resumed.get('error')}")

        await self.refresh_metadata()
        self._update_connection_state()
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
            if not await self.refresh_metadata():
                return False
            if not self._is_playing:
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
        "next": NextPrevParams,
        "prev": NextPrevParams,
    }

    async def _handle_command(self, cmd: str, params: Optional[BaseModel]) -> Dict[str, Any]:
        """Handle Spotify-specific commands."""
        if cmd == "seek":
            duration = self._metadata.get("duration", 0)
            if duration > 0 and params.position_ms > duration:
                return self.error_response(
                    f"position_ms ({params.position_ms}) exceeds duration ({duration}ms)"
                )
            return await self._send_api_command("seek", {"position": int(params.position_ms)})

        if cmd in ["pause", "resume", "playpause"]:
            return await self._send_api_command(cmd)

        if cmd in ["next", "prev"]:
            payload = {"uri": params.uri} if params.uri else {}
            return await self._send_api_command(cmd, payload)

        return self.error_response(f"Unhandled command: {cmd}")

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
        path to maintain. Only the crossfade value is touched; a start that
        would change nothing leaves the file alone, so the daemon never reads a
        file rewritten for nothing.

        Rewriting drops the installer's comments from the deployed copy — their
        rationale lives in install/go-librespot.sh, which is where it is read.

        Fails open: a config that cannot be patched still starts the daemon on
        whatever is on disk, rather than blocking Spotify entirely.
        """
        if not self._config_path or not os.path.exists(self._config_path):
            return

        managed = {"crossfade_duration": await self._get_crossfade_duration()}

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
        only way to change crossfade on a running daemon. The restart is
        absorbed by the existing WS retry loop + _reconcile_on_connect.
        """
        await self._apply_managed_config()

        if not apply_now:
            return True

        return await self._restart_service()

    # === WebSocket ===

    async def _start_websocket(self) -> None:
        """Start WebSocket connection."""
        if not self._session or not self._ws_url:
            return

        self._ws_client = LibrespotWebSocket(
            ws_url=self._ws_url,
            session=self._session,
            on_event=self._handle_ws_event,
            on_connect=self._reconcile_on_connect
        )
        await self._ws_client.start()

    async def _handle_ws_event(self, event: Dict[str, Any]) -> None:
        """Handle WebSocket event from go-librespot.

        go-librespot sends flat events (fields at root level, no "data" wrapper):
        {"type": "seek", "position": 12345, "uri": "spotify:track:..."}
        """
        event_type = event.get("type")

        self._ws_connected = True

        if event_type == "active":
            await self._on_device_active()

        elif event_type == "inactive":
            await self._on_device_inactive()

        elif event_type == "playing":
            await self._on_playback_state(True)

        elif event_type == "paused":
            await self._on_playback_state(False)

        elif event_type == "metadata":
            await self._on_metadata_update()

        elif event_type == "seek":
            await self._on_seek()

        elif event_type == "stopped":
            await self._on_stopped()

        elif event_type == "not_playing":
            await self._on_not_playing()

    async def _on_device_active(self) -> None:
        """Handle device active event."""
        self._device_connected = True
        await self.refresh_metadata()
        self._update_connection_state()

    async def _on_device_inactive(self) -> None:
        """Handle device inactive event."""
        self._cancel_pause_timer()
        self._device_connected = False
        self._is_playing = False
        self._metadata = {}
        self._update_connection_state()

    async def _on_playback_state(self, is_playing: bool) -> None:
        """Handle playback state change."""
        self._is_playing = is_playing
        self._device_connected = True

        if is_playing:
            self._cancel_pause_timer()
        else:
            self._start_pause_timer()

        await self.refresh_metadata()
        self._metadata["is_playing"] = is_playing
        self._metadata["is_buffering"] = False
        self._update_connection_state()

    async def _on_metadata_update(self) -> None:
        """Handle metadata update event.

        Set is_buffering=true so the frontend shows a spinner while the new
        track loads.  Cleared when the 'playing' event arrives.
        """
        await self.refresh_metadata()
        self._metadata["is_buffering"] = True
        self._update_connection_state()

    async def _on_seek(self) -> None:
        """Handle seek event."""
        await self.refresh_metadata()
        self._update_connection_state()

    async def _on_stopped(self) -> None:
        """Handle stopped event - context ended, nothing more to play."""
        self._logger.info("Playback stopped - context ended")
        self._is_playing = False
        self._metadata["is_buffering"] = False
        self._start_pause_timer()
        self._update_connection_state()

    async def _on_not_playing(self) -> None:
        """Handle not_playing event - track finished naturally."""
        self._logger.debug("Track finished playing")
        self._is_playing = False
        self._metadata["is_buffering"] = False
        self._start_pause_timer()
        self._update_connection_state()

    async def _reconcile_on_connect(self) -> None:
        """Reconcile state with go-librespot on every WS (re)connection.

        go-librespot emits events only on change, so after an un-commanded WS
        drop (daemon crash + systemd restart, transient blip) the daemon can be
        back idle with no session while Milō still shows the last track. Pull
        ground truth from GET /status: a live session refreshes metadata (also
        heals any events missed during the gap); an idle daemon — or an
        unreachable one (API not yet up after a restart, so state is unknown) —
        resets the source to WAITING rather than re-affirming a stale track. The
        WS loop retries every 2s, so a too-early reconcile self-corrects. The
        normal source-switch / auto-stop paths already manage state — this only
        catches the un-commanded case.
        """
        refreshed = await self.refresh_metadata()
        if not refreshed or not self._device_connected:
            # No session, or state unknown: drop any stale pause timer (so a
            # leftover auto-stop can't later fire /player/stop on a fresh
            # session) and clear the ghost metadata before re-broadcasting.
            self._cancel_pause_timer()
            self._device_connected = False
            self._metadata = {}
        self._update_connection_state()

    # === Metadata ===

    @staticmethod
    def transform_track_metadata(track: dict) -> dict:
        """Transform a go-librespot track dict into Milo's metadata format.

        Single source of truth for the go-librespot → Milo field mapping.
        Does NOT include 'is_playing' — callers add that based on their own context.
        """
        return {
            "title": track.get("name"),
            "artist": ", ".join(track.get("artist_names", [])) or None,
            "album": track.get("album_name"),
            "album_art_url": track.get("album_cover_url"),
            "duration": track.get("duration", 0),
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
        if not self._session or not self._api_url:
            return False

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with contextlib.suppress(
                aiohttp.ClientConnectorError,
                aiohttp.ClientOSError,
                asyncio.TimeoutError,
            ):
                async with self._session.get(f"{self._api_url}/") as resp:
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

    async def refresh_metadata(self) -> bool:
        """Refresh metadata from go-librespot API."""
        if not self._session or not self._api_url:
            return False

        try:
            async with self._session.get(f"{self._api_url}/status") as resp:
                if resp.status != 200:
                    return False

                data = await resp.json()

                self._device_connected = bool(data.get("track"))
                self._is_playing = not data.get("paused", True)

                if data.get("track"):
                    self._metadata = self.transform_track_metadata(data["track"])
                    self._metadata["is_playing"] = self._is_playing
                else:
                    self._metadata = {}

                return True

        except (aiohttp.ClientConnectorError, aiohttp.ClientOSError):
            self._logger.debug("Metadata refresh skipped: go-librespot not reachable")
            return False
        except Exception as e:
            self._logger.error(f"Metadata refresh failed: {e}")
            return False

    async def _send_api_command(
        self,
        command: str,
        payload: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Send command to go-librespot API."""
        if not self._session or not self._api_url:
            return self.error_response("Session not active")

        try:
            async with self._session.post(
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
            async for line in follow_unit("milo-spotify", logger=self._logger):
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

        # Critical error: track loading failed - show raw log message
        if "failed loading current track" in line:
            self.broadcast_error(self._extract_log_message(line))
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
                self.broadcast_error(self._extract_log_message(line))
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
        self._cancel_pause_timer()
        self._stop_log_monitor()

        if self._ws_client:
            await self._ws_client.stop()
            self._ws_client = None

        if self._session:
            await self._session.close()
            self._session = None

        self._ws_connected = False
        self._reset_playback_state()

    def _update_connection_state(self) -> None:
        """Update state based on device connection."""
        core, extras = PlaybackMetadata.split(self._metadata)
        core.is_playing = self._is_playing
        self.emit_connection_state(self._device_connected, core, extras)
