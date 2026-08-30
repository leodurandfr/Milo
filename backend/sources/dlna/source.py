# backend/sources/dlna/source.py
"""DLNA / UPnP Media Renderer (DMR) audio source using gmediarender.

Milō appears as a DLNA renderer that any control point (BubbleUPnP, a NAS,
Audirvana…) can push audio to. gmediarender does the UPnP device work + the
GStreamer/ALSA output; a local control-point bridge (metadata_reader.DlnaBridge)
subscribes via GENA to the renderer's AVTransport/RenderingControl and feeds
title/artist/album/artwork/state/position here. Playback is driven by the
external sender (Family B, like AirPlay): no commands from Milō's UI. Artwork is
referenced by URL in DIDL-Lite; we fetch it, decode its dimensions, cache it in
memory, and serve it via a dedicated HTTP endpoint.
"""
import asyncio
import errno
import hashlib
from typing import Any, Dict, Optional, Tuple

import aiohttp
from async_upnp_client.utils import get_local_ip

from backend.core.audio_source import BaseAudioSource
from backend.core.models.audio_state import NetworkRequirement
from backend.core.models.source_metadata import PlaybackMetadata
from backend.shared.artwork import decode_artwork_dimensions
from backend.shared.artwork_resolver import RESOLVED_ARTWORK_PX, ArtworkResolver
from backend.shared.decorators import handle_errors
from backend.sources.dlna.metadata_reader import DlnaBridge
from backend.sources.dlna.server_resolver import MediaServerResolver, host_of

# What makes a track a different track here: the triple _on_metadata_update
# compares to decide the cover is stale, and the one _on_artwork re-checks
# across its fetch.
TRACK_IDENTITY_KEYS = ("title", "artist", "album")

# A polled position further than this from where the frontend's own
# interpolation would have put it is a real jump — a track change, a seek on
# the controller — rather than routine confirmation. Generous because the
# renderer reports RelTime at one-second granularity and the poll is spaced by
# seconds, so a couple of hundred milliseconds of slack is noise, not movement.
POSITION_JUMP_TOLERANCE_MS = 2000


class DlnaSource(BaseAudioSource):
    NETWORK_REQUIREMENT = NetworkRequirement.LAN

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        state_machine=None,
        settings_service=None,
        systemd_manager=None
    ):
        super().__init__(
            source_id="dlna",
            service_name="milo-dlna.service",
            state_machine=state_machine,
            systemd_manager=systemd_manager,
            settings_service=settings_service,
            config=config
        )

        # gmediarender is launched with a fixed port/UUID by milo-dlna.service.
        # host defaults to the LAN IP that routes out (gmediarender does not bind
        # loopback); overridable via config for tests.
        self._port = self._config.get("port", 49494)
        self._host_override = self._config.get("host")
        self._description_url: Optional[str] = None

        self._bridge: Optional[DlnaBridge] = None
        # Resolved once per media-server host and cached for the process, so a
        # stop/start cycle does not pay for the discovery again.
        self._server_resolver = MediaServerResolver()

        # State
        self._metadata: Dict[str, Any] = {}
        self._is_playing = False
        self._device_connected = False
        self._server_name: Optional[str] = None
        self._server_host: Optional[str] = None

        # One cover, whichever supplied it: the sender's — fetched and served
        # from /api/dlna/artwork — or one looked up from the track text, for
        # the many control points that publish no albumArtURI at all. Held
        # apart from _metadata, which is the last publish handed back: a cover
        # left in it round-trips and captions the track after.
        #
        # _cover_answered is what makes holding it safe with no timer. The
        # cover stays while the current track's artwork question is open, and
        # the moment it is settled — one arrived, or there is none — the answer
        # decides. Both replacements cost an HTTP round trip (a fetch of up to
        # 10 s, an iTunes lookup), and a state published with no cover across
        # either is read by the frontend's untrusted-sender gate as "this
        # sender has none": the whole player is swapped for the status card and
        # back, which is what a track change looked like.
        self._artwork = ArtworkResolver()
        self._cover_url: Optional[str] = None
        self._cover_width: int = 0
        self._cover_key: Tuple[str, ...] = ()
        self._cover_answered: Tuple[str, ...] = ()

        self._artwork_data: Optional[bytes] = None
        self._artwork_mime: Optional[str] = None
        self._artwork_hash: Optional[str] = None
        self._artwork_width: int = 0

        self._last_progress_broadcast: float = 0.0
        self._last_poll_at: Optional[float] = None

        # Auto-stop (uses BaseAudioSource timer infrastructure)
        self.auto_stop_enabled = True
        self.auto_stop_delay = 10.0

    def _reset_playback_state(self) -> None:
        super()._reset_playback_state()
        self._device_connected = False
        # An idle renderer is serving nobody, so it names nobody: the label goes
        # back to the static one. The bridge re-emits the origin URL on resume
        # (forget_last_seen), and the resolver answers that from cache.
        self._server_name = None
        self._server_host = None
        self._cover_answered = ()
        self._clear_artwork()

    async def _do_start(self) -> bool:
        """Start gmediarender and the UPnP control-point bridge."""
        try:
            if not await self._start_service_and_wait(settle=1.5):
                return False

            self._reset_playback_state()
            self._cancel_pause_timer()
            await self._load_auto_stop_config()

            host = self._host_override or get_local_ip()
            self._description_url = f"http://{host}:{self._port}/description.xml"

            self._bridge = DlnaBridge(
                description_url=self._description_url,
                on_metadata=self._on_metadata_update,
                on_play_state=self._on_play_state,
                on_artwork=self._on_artwork,
                on_media_origin=self._on_media_origin,
                on_progress=self._on_progress,
                on_connection=self._on_connection,
            )
            await self._bridge.start()

            self._update_connection_state()
            return True

        except Exception as e:
            # ENETUNREACH is the link saying there is nothing to advertise on,
            # not the renderer failing: it is the expected outcome of starting
            # DLNA with the network down, and the status card already says so.
            # ERROR here would forward it to the system-error banner on top.
            expected = isinstance(e, OSError) and e.errno == errno.ENETUNREACH
            (self._logger.warning if expected else self._logger.error)(f"Start failed: {e}")
            await self._cleanup()
            return False

    # DLNA renderers are passive: playback is controlled by the external sender,
    # so no commands are registered — command() rejects every command as unknown.

    COMMANDS = {}

    # === Metadata Callbacks (fed by DlnaBridge) ===

    async def _on_metadata_update(self, metadata: Dict[str, Any]) -> None:
        """Handle track metadata from GENA DIDL-Lite (title, artist, album).

        Nothing belonging to the previous track is dropped here, and both
        omissions are deliberate — each field is what a piece of the UI is
        gated on, so publishing none of it removes that piece rather than
        correcting it. The cover gates the rich player (see the publish). The
        playhead gates ProgressBar, on `duration > 0`, and that bar carries a
        mount animation: dropping the playhead was tried and made it vanish and
        spring back on every track change, while the title beside it changed
        without a flicker.

        Neither is left wrong for long, which is the other half of the trade:
        the bridge polls the playhead on this same change, and a jump is
        broadcast the moment it lands instead of waiting out the rate limit.
        """
        track = {
            key: metadata.get(key, self._metadata.get(key, ""))
            for key in TRACK_IDENTITY_KEYS
        }
        self._metadata.update({**track, "is_playing": self._is_playing})
        self._device_connected = True
        self._update_connection_state()

    async def _on_play_state(self, state: str) -> None:
        """Handle transport-state change from the bridge.

        'stop' only starts the idle timer once a controller has actually been
        using us (device_connected): at subscribe time an idle renderer reports
        STOPPED and must stay quietly READY, not arm an auto-stop restart.
        """
        if state == "play":
            self._is_playing = True
            self._device_connected = True
            self._cancel_pause_timer()
        elif state == "pause":
            self._is_playing = False
            self._start_pause_timer()
        elif state == "stop":
            self._is_playing = False
            if self._device_connected:
                self._start_pause_timer()

        self._metadata["is_playing"] = self._is_playing
        self._update_connection_state()

    async def _on_auto_stop(self) -> None:
        """Idle timeout: return to READY without bouncing gmediarender.

        Unlike AirPlay (which restarts shairport to release the AirPlay session),
        gmediarender holds no session — after STOPPED it is immediately ready for
        the next push, so we just reset to READY and keep the renderer and its
        GENA subscription alive.
        """
        self._reset_playback_state()
        # The bridge only forwards what changed, and the renderer goes on
        # publishing the same track: without this, the resume that follows
        # re-emits nothing and the player stays a status card for the rest of it.
        if self._bridge:
            self._bridge.forget_last_seen()
        self._update_connection_state()

    def _track_key(self) -> Tuple[str, ...]:
        """The currently published track identity."""
        return tuple(self._metadata.get(key, "") for key in TRACK_IDENTITY_KEYS)

    @handle_errors(default=None)
    async def _on_artwork(self, url: Optional[str]) -> None:
        """Settle this track's artwork question: fetch the sender's, or look one up.

        `None` is the bridge saying the renderer publishes no art for the new
        track — the signal that lets this stay deterministic instead of waiting
        out a timer, since _dispatch_state reads the track identity and the art
        URL off one DmrDevice snapshot and already knows.

        The fetch runs up to 10 s and each callback is its own task, so the next
        track can land mid-flight; the identity is captured before the await and
        re-checked after, because that newer track owns the answer now.
        """
        if url is None:
            self._look_up_cover()
            return

        track = self._track_key()
        data = await self._fetch_artwork(url)
        if self._track_key() != track:
            self._logger.debug("Discarding artwork: the track moved on during the fetch")
            return
        if not data:
            # Announced and undeliverable — a 404, a host gone. The text is the
            # only thing left, and it is what the lookup runs on.
            self._look_up_cover()
            return

        new_hash = hashlib.md5(data).hexdigest()[:12]
        if new_hash != self._artwork_hash:
            # Detect format from magic bytes (shairport aside, senders vary).
            if data[:8] == b'\x89PNG\r\n\x1a\n':
                self._artwork_mime = "image/png"
            else:
                self._artwork_mime = "image/jpeg"
            width, height = decode_artwork_dimensions(data, self._logger, "DLNA")
            self._artwork_data = data
            self._artwork_hash = new_hash
            self._artwork_width = width
            self._logger.info(f"DLNA artwork {width}x{height} ({self._artwork_mime})")

        # Re-stamped even when the image is unchanged: two tracks off one album
        # send the identical bytes, and the picture that changed nothing still
        # moved which track the cover belongs to.
        self._cover_url = f"/api/dlna/artwork?v={self._artwork_hash}"
        self._cover_width = self._artwork_width
        self._cover_key = track
        self._cover_answered = track
        self._update_connection_state()

    def _look_up_cover(self) -> None:
        """The sender has nothing for this track: ask the track text instead.

        Deliberately does not drop the cover in hand first — the question is
        still open until the lookup answers, and blanking it meanwhile is the
        flicker this whole arrangement exists to avoid.
        """
        self._bg.spawn(self._resolve_artwork(self._track_key()), label="artwork_resolve")

    async def _resolve_artwork(self, track: Tuple[str, ...]) -> None:
        """Look a cover up from the track text and settle the question.

        A newer track that landed during the lookup owns the answer, so this one
        says nothing at all. Otherwise the question closes either way: with a
        cover, or with the held one released.
        """
        title, artist, album = track
        url = await self._artwork.resolve(artist, title, album)
        if track != self._track_key() or self._cover_answered == track:
            # A newer track owns the answer, or the sender delivered one of its
            # own while this was in flight — a later GENA event, a slow server.
            # Either way this lookup is moot, and applying it would displace a
            # real cover with one guessed from text.
            return

        if url:
            self._cover_url = url
            self._cover_width = RESOLVED_ARTWORK_PX
            self._cover_key = track
        else:
            # Nothing is coming. Release the held cover *and* the bytes behind
            # it, so /api/dlna/artwork stops serving a track left the screen.
            self._clear_artwork()
        self._cover_answered = track
        self._update_connection_state()

    @handle_errors(default=None)
    async def _on_media_origin(self, url: str) -> None:
        """Label the source bar with the media server serving this track.

        The bridge hands over the URL the content came from; only its host
        matters, and the same host serves track after track, so the work is
        skipped entirely once it is known. The resolution behind it is an SSDP
        sweep costing seconds — it runs here, in a bridge-spawned task, and the
        player shows its own source label meanwhile. Failure is silent by design:
        no name simply means that label stays.
        """
        host = host_of(url)
        if not host or host == self._server_host:
            return
        self._server_host = host

        # The previous server's name is wrong for this host and the sweep that
        # replaces it takes seconds: drop it now rather than caption the new
        # track with the old server for the length of a discovery.
        if self._server_name:
            self._server_name = None
            self._update_connection_state()

        name = await self._server_resolver.resolve(host)
        if host != self._server_host:
            self._logger.debug("Discarding server name: the track moved on during the sweep")
            return
        if name:
            self._server_name = name
            self._logger.info(f"DLNA media server: {name} ({host})")
            self._update_connection_state()

    async def _fetch_artwork(self, url: str) -> Optional[bytes]:
        """Fetch artwork bytes from the DMS URL (best-effort)."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status != 200:
                        self._logger.warning(f"Artwork fetch {url} -> HTTP {resp.status}")
                        return None
                    return await resp.read()
        except Exception as e:
            self._logger.warning(f"Artwork fetch failed ({url}): {e}")
            return None

    async def _on_progress(self, position: int, duration: int) -> None:
        """Handle polled position (ms).

        A snapshot that merely confirms the frontend's own interpolation
        (useSourceProgress) is rate-limited to 30 s. A jump cannot be
        interpolated — a new track, or a seek on the controller — so it goes
        out at once. Without that, a track change waited on the poll AND on the
        rate limit: 12.7 s of the previous track's playhead, measured on the
        unit.
        """
        if not duration or duration <= 0:
            return

        now = asyncio.get_running_loop().time()
        predicted = self._metadata.get("position")
        if predicted is not None and self._last_poll_at is not None:
            predicted += (now - self._last_poll_at) * 1000

        self._metadata["position"] = position
        self._metadata["duration"] = duration
        self._last_poll_at = now

        jumped = predicted is None or abs(position - predicted) > POSITION_JUMP_TOLERANCE_MS
        if jumped or now - self._last_progress_broadcast >= 30.0:
            self._last_progress_broadcast = now
            self.broadcast_position_update(position, duration)

    async def _on_connection(self, state: str) -> None:
        """Handle bridge connect/disconnect.

        'connected' only means the renderer is reachable and subscribed — the
        baseline READY state, not an active push. 'disconnected' means the
        renderer went away (e.g. restart): reset to READY.
        """
        if state == "connected":
            self._logger.info("DLNA renderer bridge connected")
        elif state == "disconnected":
            self._logger.info("DLNA renderer bridge disconnected")
            self._cancel_pause_timer()
            self._device_connected = False
            self._is_playing = False
            self._metadata = {}
            self._clear_artwork()
            self._update_connection_state()

    # === Helpers ===

    def _update_connection_state(self) -> None:
        """Publish connection/playback state to the shared player.

        Broadcast metadata shape (WS source/state_changed → system_state.metadata):
        title, artist, album, album_art_url, album_art_width, position, duration,
        is_playing (canonical PlaybackMetadata) + client_name (extra, the source
        bar's label): the media server's friendlyName, present only once resolved.
        The control point is never identified by the renderer, so what is named
        here is where the audio came from, not who asked for it.
        """
        core, extras = PlaybackMetadata.split(self._metadata)
        core.is_playing = self._is_playing
        # The cover is merged in here and never stored: _metadata is the last
        # publish handed back, so one left in it round-trips and captions the
        # track after. Shown while it is this track's, or while this track's
        # artwork question is still open — that second clause is the hold, and
        # what spares a timer. Its width travels with it because the frontend
        # gate reads album_art_width and judges a cover of unstated size
        # exactly as if there were none.
        core.album_art_url = None
        extras.pop("album_art_width", None)
        if self._cover_url and (
            self._cover_key == self._track_key()
            or self._cover_answered != self._track_key()
        ):
            core.album_art_url = self._cover_url
            extras["album_art_width"] = self._cover_width
        # The media server's friendlyName, or None until the sweep names it —
        # UPnP never identifies the *control point* to the renderer, so with no
        # server resolved there is nobody to name and the player falls back to
        # the source's own label, which belongs to the frontend's i18n table and
        # not here. Assigned unconditionally: emit_connection_state drops a None
        # extra, and _metadata is the record it hands back, so this is also what
        # clears the previous server's name out of it when the host changes.
        extras["client_name"] = self._server_name
        self.emit_connection_state(self._device_connected, core, extras)

    async def _cleanup(self) -> None:
        """Clean up resources."""
        self._cancel_pause_timer()

        if self._bridge:
            await self._bridge.stop()
            self._bridge = None

        self._reset_playback_state()

    def _clear_artwork(self) -> None:
        """Drop the cover, its bytes, and the question it was the answer to."""
        self._artwork_data = None
        self._artwork_mime = None
        self._artwork_hash = None
        self._artwork_width = 0
        self._cover_url = None
        self._cover_width = 0
        self._cover_key = ()
        # _metadata is not touched: the publish decides the cover fields from
        # scratch every time, so clearing them here would be a second mechanism
        # for one rule — and the one that is not exercised is the one that rots.

    # === Public API ===

    def get_artwork(self) -> Optional[Tuple[bytes, str]]:
        """Return current artwork as (data, mime_type), or None."""
        if self._artwork_data and self._artwork_mime:
            return self._artwork_data, self._artwork_mime
        return None
