# backend/sources/qobuz/source.py
"""Qobuz Connect audio source via the qobuz-proxy sidecar.

qobuz-proxy is a reverse-engineered virtual Qobuz Connect device: the Qobuz app
is the controller, qobuz-proxy renders the stream to ALSA (the milo_qobuz PCM).
Milō only displays + plays (Family B, like AirPlay) — playback is driven from
the Qobuz app, so there are no on-device controls. Now-playing metadata
(title/artist/album/artwork + position/duration) is polled from the proxy's local
HTTP API (GET /api/status); the proxy exposes no push channel and no local
control endpoints. Progress is there because install/qobuz_proxy_patches.py adds
position_ms/duration_ms to the vendored now_playing payload — upstream reports
them only to the Qobuz cloud. Album art is a Qobuz CDN URL loaded directly by the
kiosk — there is no binary artwork route (unlike AirPlay/DLNA).
"""
from typing import Any, Dict, Optional

from backend.config.constants import MILO_DATA_DIR
from backend.core.audio_source import BaseAudioSource
from backend.core.models.audio_state import NetworkRequirement
from backend.core.models.source_metadata import PlaybackMetadata
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

# qobuz-proxy speaker.status values that mean a session is attached.
_ACTIVE_STATUSES = {"playing", "paused"}

# A track change makes qobuz-proxy briefly report idle / an empty now_playing for
# a poll tick or two. Hold the last ACTIVE state for this many ticks (~poll_interval
# each) before committing to READY, so the UI doesn't flash the "ready to stream"
# fallback between tracks. A real stop persists past the window; a track change
# does not.
_IDLE_GRACE_TICKS = 3

# Mirror of the above for the opposite blip: an active status whose now_playing
# is still empty at the start of a session. Hold rather than publish ACTIVE with
# no track — nothing renders, so the card would show the idle fallback over
# playing audio. Bounded, not indefinite: a proxy that never delivers a track
# must not wedge the source in READY forever.
_TRACKLESS_GRACE_TICKS = 3


class QobuzSource(BaseAudioSource):
    """Qobuz Connect source (Family B — passive player): external control, rich metadata."""

    NETWORK_REQUIREMENT = NetworkRequirement.INTERNET

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
        self._idle_ticks = 0
        self._trackless_ticks = 0
        # qobuz-proxy account login state (from /api/status auth.authenticated).
        # Optimistic default so the idle card doesn't flash the "connect account"
        # CTA before the first poll confirms there is no account.
        self._authenticated = True

    def _reset_playback_state(self) -> None:
        super()._reset_playback_state()
        self._device_connected = False
        self._idle_ticks = 0
        self._trackless_ticks = 0

    async def _do_start(self) -> bool:
        """Start the qobuz-proxy service and the /api/status poll monitor."""
        try:
            # Put the volume-policy flag in place before the sidecar starts so its
            # first volume command reads the right value.
            await self._sync_volume_flag()

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
        it is running.
        """
        self._write_volume_flag(allowed)
        if not allowed and await self._is_service_active():
            return await self._restart_service_and_wait()
        return True

    async def _on_status(
        self, speaker: Optional[Dict[str, Any]], authenticated: Optional[bool]
    ) -> None:
        """Map a qobuz-proxy speaker snapshot into connection/playback state.

        playing/paused (with now_playing) → ACTIVE with the current track;
        idle/disconnected/absent → READY. Both directions pass through a short
        grace window, for the same reason in mirror: qobuz-proxy blips to idle
        between tracks (don't flash the "ready to stream" fallback) and starts a
        session before it has a track to report (don't publish an ACTIVE with
        nothing to draw). Position/duration ride the same poll (our patched
        now_playing carries them, see
        install/qobuz_proxy_patches.py) and the frontend interpolates between
        ticks; seeking stays with the Qobuz app — Family B has no local control.

        `authenticated` is the proxy's login state (None = unknown, keep last);
        it rides the broadcast metadata so the idle card can offer a "connect
        account" CTA when no Qobuz account is logged in.
        """
        was_authenticated = self._authenticated
        if authenticated is not None:
            self._authenticated = authenticated

        status = (speaker or {}).get("status")

        if speaker is not None and status in _ACTIVE_STATUSES:
            now = speaker.get("now_playing") or {}
            self._is_playing = status == "playing"
            # Overwrite metadata only when the proxy actually reports a track.
            # During a track change now_playing is briefly empty — keep the last
            # track's metadata rather than blanking title/artist (which would gate
            # out the rich player and flash the fallback).
            if now.get("title") or now.get("artist"):
                self._metadata = {
                    "title": now.get("title"),
                    "artist": now.get("artist"),
                    "album": now.get("album"),
                    "album_art_url": now.get("album_art_url"),
                    "is_playing": self._is_playing,
                    "is_buffering": False,
                }
            elif self._metadata:
                self._metadata["is_playing"] = self._is_playing
            # Progress is absent from the between-tracks blip payload — keep the
            # last pair rather than snapping the bar back to zero.
            if self._metadata and "duration_ms" in now:
                self._metadata["position"] = now["position_ms"]
                self._metadata["duration"] = now["duration_ms"]

            # Nothing to render: an active status whose now_playing has not
            # produced a title (the session's first ticks). Committing ACTIVE
            # here publishes a session the card can only draw as its idle
            # fallback, over audio that is playing — so hold, exactly as the
            # idle branch below holds the opposite blip.
            if not self._metadata.get("title"):
                if self._trackless_ticks < _TRACKLESS_GRACE_TICKS:
                    self._trackless_ticks += 1
                    return
                self._logger.warning(
                    "qobuz-proxy reports '%s' with no track after %d ticks — "
                    "publishing the session without one",
                    status, _TRACKLESS_GRACE_TICKS,
                )
            else:
                self._trackless_ticks = 0

            self._device_connected = True
            self._idle_ticks = 0
        else:
            # idle/disconnected/absent. A real stop persists; a track-change blip
            # lasts a tick or two — hold the previous ACTIVE state for the grace
            # window, then commit to READY.
            if self._device_connected and self._idle_ticks < _IDLE_GRACE_TICKS:
                self._idle_ticks += 1
                return
            # Idle is a steady state, not a progress feed: the READY published
            # when the session ended (or by _do_start) already says everything
            # the next tick would, and this poll runs at ~1 Hz for as long as the
            # source is selected. Only the login state still moves while idle, so
            # that is the one thing worth a second broadcast.
            was_connected = self._device_connected
            self._is_playing = False
            self._metadata = {}
            self._device_connected = False
            self._trackless_ticks = 0
            if not was_connected and self._authenticated == was_authenticated:
                return

        self._update_connection_state()

    def _update_connection_state(self) -> None:
        """Publish connection/playback state to the shared player.

        Broadcast metadata (WS source/state_changed → system_state.metadata):
        title, artist, album, album_art_url, position, duration, is_playing,
        is_buffering (canonical PlaybackMetadata) + account_authenticated (login
        state; drives the idle card's "connect account" CTA when no Qobuz account
        is logged in). No client_name: the proxy never reports the controlling
        device — it only knows the speaker name — so the source bar falls back to
        the source's own label rather than to one hardcoded here.

        The ~1 Hz poll doubles as the progress feed: every tick re-emits the full
        state, so there is no separate broadcast_position_update path here.
        """
        core, extras = PlaybackMetadata.split(self._metadata)
        core.is_playing = self._is_playing
        extras["account_authenticated"] = self._authenticated
        self.emit_connection_state(self._device_connected, core, extras)

    async def _cleanup(self) -> None:
        """Stop the poll monitor and reset state (service stop handled by _do_stop)."""
        if self._monitor:
            await self._monitor.stop()
            self._monitor = None
        self._reset_playback_state()


__all__ = ["QobuzSource"]
