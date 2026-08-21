# backend/sources/dlna/metadata_reader.py
"""UPnP control-point bridge to the local gmediarender (DLNA renderer).

Milō is the DMR; gmediarender does the UPnP device work + GStreamer/ALSA output
but emits no metadata on stdout (unlike shairport's pipe). This bridge acts as a
UPnP control point *toward the local gmediarender*: it builds a DmrDevice from the
fixed description URL, subscribes (GENA) to the AVTransport/RenderingControl
LastChange events, and polls GetPositionInfo for progress. It exposes the same
callback shape as AirPlay's MetadataReader (on_metadata / on_play_state /
on_artwork / on_progress / on_connection) so DlnaSource mirrors AirPlaySource,
plus on_media_origin — the URL the content is served from, which is the only
thing UPnP gives the renderer that identifies anything upstream of it.

async-upnp-client (the library Home Assistant uses) does the GENA plumbing.
"""
import asyncio
import contextlib
import logging
from typing import Any, Callable, Dict, Optional, Tuple

from async_upnp_client.aiohttp import AiohttpNotifyServer, AiohttpRequester
from async_upnp_client.client_factory import UpnpFactory
from async_upnp_client.profiles.dlna import DmrDevice
from async_upnp_client.utils import get_local_ip

from backend.shared.background import BackgroundTaskSet

logger = logging.getLogger("source.dlna.metadata")

# UPnP AVTransport CurrentTransportState -> our normalized play state.
_PLAY_STATE = {
    "PLAYING": "play",
    "PAUSED_PLAYBACK": "pause",
    "PAUSED_RECORDING": "pause",
    "STOPPED": "stop",
    "NO_MEDIA_PRESENT": "stop",
}


def _to_ms(value: Any) -> Optional[int]:
    """Convert an async-upnp-client position/duration to int milliseconds.

    async-upnp-client returns these as int/float SECONDS (0.47 — verified against
    the live gmediarender); a timedelta is tolerated too, for robustness across
    versions."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value * 1000)
    try:
        return int(value.total_seconds() * 1000)
    except AttributeError:
        return None


class DlnaBridge:
    """Control-point bridge to the local gmediarender renderer."""

    def __init__(
        self,
        description_url: str,
        on_metadata: Callable[[Dict[str, Any]], Any],
        on_play_state: Callable[[str], Any],
        on_artwork: Callable[[str], Any],
        on_media_origin: Callable[[str], Any],
        on_progress: Callable[[int, int], Any],
        on_connection: Callable[[str], Any],
        poll_interval: float = 10.0,
        retry_delay: float = 3.0,
    ):
        self._description_url = description_url
        self._on_metadata = on_metadata
        self._on_play_state = on_play_state
        self._on_artwork = on_artwork
        self._on_media_origin = on_media_origin
        self._on_progress = on_progress
        self._on_connection = on_connection
        self._poll_interval = poll_interval
        self._retry_delay = retry_delay

        self._running = False
        self._bg = BackgroundTaskSet(logger, "source.dlna.bridge")
        self._server: Optional[AiohttpNotifyServer] = None
        self._dmr: Optional[DmrDevice] = None

        # Last-seen values — GENA re-sends full state, so only emit on change.
        self._last_state: Optional[str] = None
        self._last_meta: Optional[Tuple[str, str, str]] = None
        self._last_art: Optional[str] = None
        self._last_origin: Optional[str] = None

    async def start(self) -> None:
        """Spawn the supervise loop (connect + subscribe + poll, with retry)."""
        self._running = True
        self._bg.spawn(self._run(), label="supervise")
        logger.info("DlnaBridge started (%s)", self._description_url)

    async def stop(self) -> None:
        """Stop polling, unsubscribe, and drain tasks."""
        self._running = False
        await self._teardown()
        await self._bg.cancel_all()
        logger.info("DlnaBridge stopped")

    async def _run(self) -> None:
        """Supervise loop: (re)connect + subscribe, then poll until failure.

        The subscription auto-resubscribes while alive; this outer loop only
        re-establishes it when the renderer goes away (e.g. gmediarender
        restart). Loop body is wrapped per the background-loop doctrine.
        """
        while self._running:
            try:
                await self._connect_and_subscribe()
                await self._on_connection("connected")
                while self._running:
                    await asyncio.sleep(self._poll_interval)
                    await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("DlnaBridge loop error: %s", e)
                with contextlib.suppress(Exception):
                    await self._on_connection("disconnected")
                await self._teardown()
                await asyncio.sleep(self._retry_delay)

    async def _connect_and_subscribe(self) -> None:
        requester = AiohttpRequester()
        factory = UpnpFactory(requester, non_strict=True)
        device = await factory.async_create_device(self._description_url)

        # gmediarender POSTs GENA callbacks back to us; bind the notify server to
        # the LAN IP that routes to it (gmediarender does not listen on loopback).
        local_ip = get_local_ip(self._description_url)
        self._server = AiohttpNotifyServer(requester, source=(local_ip, 0))
        await self._server.async_start_server()

        self._dmr = DmrDevice(device, event_handler=self._server.event_handler)
        self._dmr.on_event = self._on_dmr_event
        await self._dmr.async_subscribe_services(auto_resubscribe=True)
        logger.info("DlnaBridge subscribed to %s", self._description_url)
        self._dispatch_state()

    def _on_dmr_event(self, service, state_variables) -> None:
        """GENA LastChange callback (async-upnp-client invokes this synchronously
        from the event loop while handling the NOTIFY request)."""
        self._dispatch_state()

    def _dispatch_state(self) -> None:
        """Read current DmrDevice properties and emit only the changed fields."""
        dmr = self._dmr
        if dmr is None:
            return

        state = dmr.transport_state
        if state and state != self._last_state:
            self._last_state = state
            mapped = _PLAY_STATE.get(state)
            if mapped:
                self._bg.spawn(self._on_play_state(mapped), label="play_state")

        title = dmr.media_title or ""
        artist = dmr.media_artist or dmr.media_album_artist or ""
        album = dmr.media_album_name or ""
        meta = (title, artist, album)
        if any(meta) and meta != self._last_meta:
            self._last_meta = meta
            # The source drops the cover with the track it belonged to, so the
            # new track's art has to be dispatched again even when the renderer
            # reports the very same URL — two tracks off one album do.
            self._last_art = None
            self._bg.spawn(
                self._on_metadata({"title": title, "artist": artist, "album": album}),
                label="metadata",
            )

        art = dmr.media_image_url
        if art and art != self._last_art:
            self._last_art = art
            self._bg.spawn(self._on_artwork(art), label="artwork")

        # Where the audio came from. CurrentTrackURI is the content itself and
        # is the right answer; the art URL is the standby for a renderer that
        # publishes no track URI, since DIDL-Lite art is served by that same
        # media server. Only the host is used downstream — the consumer resolves
        # it to a name and caches it, so re-emitting per track costs nothing.
        origin = dmr.current_track_uri or art
        if origin and origin != self._last_origin:
            self._last_origin = origin
            self._bg.spawn(self._on_media_origin(origin), label="media_origin")

    def forget_last_seen(self) -> None:
        """Drop the change-detection memory, so the next event re-emits it all.

        For a consumer that cleared its own copy while the renderer kept
        publishing the same track — the auto-stop reset. Without this the
        bridge has nothing new to report and the player never comes back.
        """
        self._last_state = None
        self._last_meta = None
        self._last_art = None
        self._last_origin = None

    async def _poll_once(self) -> None:
        dmr = self._dmr
        if dmr is None:
            return
        await dmr.async_update()
        position = _to_ms(dmr.media_position)
        duration = _to_ms(dmr.media_duration)
        if position is not None and duration is not None:
            await self._on_progress(position, duration)

    async def _teardown(self) -> None:
        if self._dmr is not None:
            with contextlib.suppress(Exception):
                await self._dmr.async_unsubscribe_services()
            self._dmr = None
        if self._server is not None:
            with contextlib.suppress(Exception):
                await self._server.async_stop_server()
            self._server = None
        self.forget_last_seen()
