# backend/sources/airplay/source.py
"""
AirPlay 2 audio source using shairport-sync.

Handles AirPlay streaming from Apple devices via shairport-sync.
Metadata (title, artist, album) flows through the metadata pipe
and is broadcast to the frontend via WebSocket. Artwork is stored
in memory and served via a dedicated HTTP endpoint.

A sender-side pause is invisible here, and no amount of looking changes that.
Measured on shairport-sync 5.2.1 with a macOS sender (2026-08-07), every channel
the receiver has: `pfls`/`pend` never fire — only tearing the output down sends
`pend`, and `disc` with it; `core/caps` stays 0x01 throughout; D-Bus
`RemoteControl.PlayerState` still reads "Playing" 96 s into a pause (it only
moves for a pause *we* command, and `RemoteControl.Available` is false, so there
is no back-channel to ask); and `FramePosition` keeps advancing at 44.1 kHz
because shairport goes on writing silence — the same shape as ROC on the Mac
source, which is why the pause path there is closed too.

The one thing that stops is the position the sender reports, so pause is
*inferrable* from two `prgr` snapshots standing still — but only 5-15 s late, and
sometimes not at all, because the sender may simply stop reporting. That
inference was built and then dropped at the owner's call: the progress bar it
existed to freeze is not drawn for AirPlay at all (AirPlaySource.vue), the sender
draws its own. Consequence to know before wondering: `_start_pause_timer()` is
reachable only from `pfls`/`pend`, so a paused session holds the source until the
sender disconnects — IDLE_STATES excludes ACTIVE, so the 12 h sweep never gets it.
"""
import asyncio
import hashlib
import os
from typing import Dict, Any, Optional, Tuple

from backend.core.audio_source import BaseAudioSource
from backend.core.models.audio_state import NetworkRequirement
from backend.core.models.source_metadata import PlaybackMetadata
from backend.sources.airplay.metadata_reader import MetadataReader
from backend.shared.artwork import decode_artwork_dimensions
from backend.shared.decorators import handle_errors

# Sample rate for RTP frame to millisecond conversion
AIRPLAY_SAMPLE_RATE = 44100

# How often the aged position is pushed out while a track plays. shairport-sync
# emits `prgr` every 5-15 s, not continuously, so the frame snapshot has
# to be aged here: left alone, system_state.metadata["position"] stays frozen at
# whatever the track started on, and every client that connects mid-track (a page
# refresh, a second browser) seeds its progress bar from that stale value and
# restarts from it. Live clients interpolate locally, so this interval only bounds
# how stale a *new* connection's initial_state can be.
POSITION_TICK_SECONDS = 10.0

# A snapshot further than this from the interpolated position is a real jump
# (track change, seek) rather than routine confirmation.
POSITION_JUMP_TOLERANCE_MS = 2000

# How long the cover in hand may outlive its pairing while the next one is
# still in flight.
#
# The tags and the picture are two SET_PARAMETER requests in no guaranteed
# order, so a track change leaves one of the two unpaired whichever way round
# it arrives. This bounds the half that can be stale -- the picture stamped
# *before* the tags on screen (see _artwork_is_current, which is what decides
# that a picture stamped after them is simply this track's).
# Publishing that gap drops album_art_url, and the frontend's untrusted-sender
# gate (UNTRUSTED_SENDER_MIN_ARTWORK_PX) reads a missing album_art_width as
# "this sender pushes no real cover": AudioPlayerFull is swapped for the status
# card and back, which is visible as the player animating itself out and in.
#
# Holding the cover across the gap keeps what the pairing is for -- a coverless
# track must not wear the previous one's for its whole duration -- and bounds it
# explicitly instead of deciding on an instant. Two delays were measured
# against a macOS sender: ~30 ms when the sender merely re-sends its bundle
# under a fresh rtptime, which every transport action makes it do, and 5.4 s on
# a genuine track change where it had to produce the new cover. The bound has
# to cover the second, so what it sizes is not the gap but how long a cover may
# be wrong -- and at a track change inside one album the held cover IS the new
# track's, so only an album boundary onto a coverless track shows a stale one,
# with the title and artist beside it already correct throughout.
ARTWORK_SETTLE_SECONDS = 8.0


class AirPlaySource(BaseAudioSource):
    """AirPlay 2 source (Family B — passive player): external control, rich metadata."""

    NETWORK_REQUIREMENT = NetworkRequirement.LAN

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        state_machine=None,
        settings_service=None,
        systemd_manager=None
    ):
        super().__init__(
            source_id="airplay",
            service_name="milo-airplay.service",
            state_machine=state_machine,
            systemd_manager=systemd_manager,
            settings_service=settings_service,
            config=config
        )

        self._metadata_pipe = self._config.get("metadata_pipe", "/tmp/shairport-sync-metadata")

        self._metadata_reader: Optional[MetadataReader] = None

        # State
        self._metadata: Dict[str, Any] = {}
        self._is_playing = False
        self._device_connected = False
        self._client_name: Optional[str] = None

        # Artwork served via dedicated endpoint, and held apart from _metadata
        # so it is merged in at publish time rather than written through it.
        # _artwork_id is the rtptime the held cover was stamped with and
        # _track_id the one on screen: a picture and its track arrive in
        # separate messages in no guaranteed order (see MetadataReader), so the
        # pairing is the id, never the arrival.
        self._artwork_data: Optional[bytes] = None
        self._artwork_mime: Optional[str] = None
        self._artwork_hash: Optional[str] = None
        self._artwork_url: Optional[str] = None
        self._artwork_width: int = 0
        self._artwork_id: Optional[str] = None
        self._artwork_settle_task: Optional[asyncio.Task] = None
        self._track_id: Optional[str] = None

        # Progress tracking: `prgr` gives a position snapshot in RTP frames; the
        # elapsed time since it arrived is what makes the position current.
        # _position_at is None while paused/stopped, which freezes the ageing.
        self._position_ms = 0
        self._duration_ms = 0
        self._position_at: Optional[float] = None
        self._position_task: Optional[asyncio.Task] = None

        # Auto-stop (uses BaseAudioSource timer infrastructure)
        self.auto_stop_enabled = True
        self.auto_stop_delay = 10.0

    def _reset_playback_state(self) -> None:
        super()._reset_playback_state()
        self._device_connected = False
        self._client_name = None
        self._cancel_position_ticker()
        self._position_ms = 0
        self._duration_ms = 0
        self._position_at = None
        self._track_id = None
        self._clear_artwork()

    async def _do_start(self) -> bool:
        """Start shairport-sync service and metadata reader."""
        try:
            if not await self._start_service_and_wait():
                return False

            self._reset_playback_state()
            self._cancel_pause_timer()

            # Load auto-stop config from settings
            await self._load_auto_stop_config()

            await self._ensure_metadata_pipe()

            self._metadata_reader = MetadataReader(
                pipe_path=self._metadata_pipe,
                on_metadata=self._on_metadata_update,
                on_play_state=self._on_play_state,
                on_artwork=self._on_artwork,
                on_progress=self._on_progress,
                on_client_name=self._on_client_name,
                on_connection=self._on_connection,
            )
            await self._metadata_reader.start()
            self._start_position_ticker()

            self._update_connection_state()
            return True

        except Exception as e:
            self._logger.error(f"Start failed: {e}")
            await self._cleanup()
            return False

    @handle_errors(default=False)
    async def _do_restart(self) -> bool:
        """Restart service with state reset."""
        self._logger.info("Restarting AirPlay source")

        self._cancel_pause_timer()
        self._reset_playback_state()

        if self._metadata_reader:
            await self._metadata_reader.stop()
            self._metadata_reader = None

        if not await self._restart_service_and_wait():
            return False

        await self._ensure_metadata_pipe()
        self._metadata_reader = MetadataReader(
            pipe_path=self._metadata_pipe,
            on_metadata=self._on_metadata_update,
            on_play_state=self._on_play_state,
            on_artwork=self._on_artwork,
            on_progress=self._on_progress,
            on_client_name=self._on_client_name,
            on_connection=self._on_connection,
        )
        await self._metadata_reader.start()
        self._start_position_ticker()

        self._update_connection_state()
        return True

    # AirPlay 2 does not support remote playback control
    # (shairport-sync AIRPLAY2.md: "Remote control facilities are not implemented"),
    # so no commands are registered — command() rejects every command as unknown.

    COMMANDS = {}

    # === Metadata Callbacks ===

    async def _on_metadata_update(
        self, metadata: Dict[str, Any], track_id: Optional[str]
    ) -> None:
        """Handle track metadata from pipe (title, artist, album).

        Recording which track is on screen is all that is needed to move the
        cover with it: the publish pairs the two by rtptime. A track whose
        sender pushes no PICT of its own — plenty do not — would otherwise wear
        the previous one's cover for its whole duration.

        The tags are paired by the same stamp, and for the same reason. A
        bundle carries only the DAAP tags the sender put in it, so one arriving
        without an `asar` used to leave the previous track's artist standing —
        published under the new title, for the whole of it. A bundle under a
        *new* rtptime is a different track and owns all three fields, absences
        included; one under the stamp already on screen is an amendment to it
        and merges. A sender that sends no RTP-Info stamps nothing, so every
        bundle reads as an amendment and keeps what it had — the same trade
        the cover makes there, and for the same want of anything to pair on.
        """
        amendment = track_id is None or track_id == self._track_id
        self._track_id = track_id
        # Before the publish below, so it already sees the hold rather than
        # emitting one coverless state and correcting it a task-turn later.
        self._sync_artwork_hold()
        self._metadata.update({
            key: (
                metadata.get(key, self._metadata.get(key, ""))
                if amendment else metadata.get(key, "")
            )
            for key in ("title", "artist", "album")
        })
        self._metadata["is_playing"] = self._is_playing

        self._update_progress_metadata()
        self._device_connected = True
        self._update_connection_state()

    async def _on_play_state(self, state: str) -> None:
        """Handle play state change from pipe reader.

        Note: pend (stop) only means the playback stream ended, NOT that the
        device disconnected.  The device remains connected until we receive a
        'disc' event via _on_connection.  We start the auto-stop timer
        on both pause and stop so the UI resets to READY after a timeout.
        """
        if state == "play":
            self._is_playing = True
            self._device_connected = True
            # Resume ageing from wherever the frozen snapshot left off.
            if self._position_at is None and self._duration_ms > 0:
                self._position_at = asyncio.get_running_loop().time()
            self._cancel_pause_timer()
        elif state == "pause":
            self._freeze_position()
            self._is_playing = False
            self._start_pause_timer()
        elif state == "stop":
            self._freeze_position()
            self._is_playing = False
            # Device may still be connected — don't reset _device_connected.
            # Start auto-stop timer as session idle timeout.
            self._start_pause_timer()

        self._metadata["is_playing"] = self._is_playing
        self._update_progress_metadata()
        self._update_connection_state()

    @handle_errors(default=None)
    async def _on_artwork(self, data: bytes, track_id: Optional[str]) -> None:
        """Handle artwork from pipe: store in memory and serve via endpoint.

        Also decodes pixel dimensions so the frontend can gate the rich
        player on artwork quality: browser audio (no MediaSession cover) ends
        up as a small favicon / app-icon, whereas real senders (Apple Music,
        Spotify desktop) push a high-resolution cover. Dimensions are
        broadcast as album_art_width/height; the display policy lives on the
        frontend (AudioSourceView.hasRichDisplay).

        The rtptime is recorded before the dedupe: two tracks off one album send
        the identical image, and the picture that changed nothing still moved
        which track the cover belongs to.
        """
        paired_with, self._artwork_id = self._artwork_id, track_id
        new_hash = hashlib.md5(data).hexdigest()[:12]
        if new_hash == self._artwork_hash:
            if track_id != paired_with:
                self._sync_artwork_hold()
                self._update_connection_state()
            return

        # Detect image format from magic bytes (shairport-sync sends JPEG or PNG)
        if data[:8] == b'\x89PNG\r\n\x1a\n':
            self._artwork_mime = "image/png"
        else:
            self._artwork_mime = "image/jpeg"

        width, height = decode_artwork_dimensions(data, self._logger, "AirPlay")

        self._artwork_data = data
        self._artwork_hash = new_hash
        self._artwork_url = f"/api/airplay/artwork?v={new_hash}"
        self._artwork_width = width
        self._logger.info(f"AirPlay artwork {width}x{height} ({self._artwork_mime})")
        self._sync_artwork_hold()
        self._update_connection_state()

    async def _on_client_name(self, name: str) -> None:
        """Handle client name from pipe (X-Apple-Client-Name)."""
        self._client_name = name
        self._device_connected = True
        self._update_connection_state()

    async def _on_connection(self, state: str, client_ip: Optional[str] = None) -> None:
        """Handle AirPlay 2 connection/disconnection events.

        'conn' is sent as soon as a client selects this AirPlay output,
        before any audio flows.  'disc' is sent when the client disconnects.
        """
        if state == "connected":
            self._logger.info(f"AirPlay client connected (IP: {client_ip})")
            self._device_connected = True
            self._cancel_pause_timer()
            self._update_connection_state()
        elif state == "disconnected":
            self._logger.info(f"AirPlay client disconnected (IP: {client_ip})")
            self._cancel_pause_timer()
            self._device_connected = False
            self._is_playing = False
            self._metadata = {}
            self._client_name = None
            self._clear_artwork()
            self._update_connection_state()

    async def _on_progress(self, start: int, current: int, end: int) -> None:
        """Handle progress update from pipe reader (RTP frames at 44100Hz).

        Takes a fresh snapshot (a new track, a seek) and publishes it at once —
        it can be an arbitrarily large jump, which local interpolation on the
        clients cannot guess.
        """
        if end <= start:
            return

        predicted = self._current_position_ms()
        self._duration_ms = int((end - start) / AIRPLAY_SAMPLE_RATE * 1000)
        self._position_ms = max(0, int((current - start) / AIRPLAY_SAMPLE_RATE * 1000))
        self._position_at = asyncio.get_running_loop().time() if self._is_playing else None
        self._update_progress_metadata()

        # Only a jump (new track, seek) is worth an immediate broadcast — the
        # clients' local interpolation cannot guess it. A snapshot that merely
        # confirms the interpolation is left to the ticker, so a sender that
        # emits `prgr` often can't flood every connected client.
        if abs(self._position_ms - predicted) > POSITION_JUMP_TOLERANCE_MS:
            self.broadcast_position_update(self._position_ms, self._duration_ms)

    # === Helpers ===

    def _freeze_position(self) -> None:
        """Stop ageing the position (pause/stop), keeping where it got to."""
        self._position_ms = self._current_position_ms()
        self._position_at = None

    def _current_position_ms(self) -> int:
        """Snapshot position aged by the time elapsed since it was taken."""
        if self._position_at is None:
            return self._position_ms
        elapsed = (asyncio.get_running_loop().time() - self._position_at) * 1000
        return min(self._position_ms + int(elapsed), self._duration_ms)

    def _update_progress_metadata(self) -> None:
        """Write the current position/duration into the metadata dict."""
        if self._duration_ms <= 0:
            return
        self._metadata["duration"] = self._duration_ms
        self._metadata["position"] = self._current_position_ms()

    def _start_position_ticker(self) -> None:
        """Keep the broadcast position (and thus system_state.metadata) aged."""
        self._cancel_position_ticker()

        async def tick():
            while True:
                await asyncio.sleep(POSITION_TICK_SECONDS)
                if not self._is_playing or self._duration_ms <= 0:
                    continue
                self._update_progress_metadata()
                self.broadcast_position_update(
                    self._metadata["position"], self._duration_ms
                )

        self._position_task = asyncio.create_task(tick())

    def _cancel_position_ticker(self) -> None:
        if self._position_task:
            self._position_task.cancel()
            self._position_task = None

    async def _ensure_metadata_pipe(self) -> None:
        """Ensure metadata pipe exists."""
        if not os.path.exists(self._metadata_pipe):
            try:
                os.mkfifo(self._metadata_pipe)
            except FileExistsError:
                pass
            except PermissionError:
                self._logger.warning(
                    f"Cannot create metadata pipe {self._metadata_pipe} "
                    "(will be created by shairport-sync)"
                )

    def _update_connection_state(self) -> None:
        """Update state based on device connection."""
        core, extras = PlaybackMetadata.split(self._metadata)
        core.is_playing = self._is_playing
        # The cover is published for the track it was stamped for, and the
        # width comes with it — both dropped first, because _metadata is the
        # last publish handed back and either would otherwise round-trip
        # through it and outlive the pairing that put it there. A pending
        # settle keeps them for the few ms a newly-stamped track's own picture
        # may still be in flight (ARTWORK_SETTLE_SECONDS).
        core.album_art_url = None
        extras.pop("album_art_width", None)
        if self._artwork_url and (
            self._artwork_is_current() or self._artwork_settle_task
        ):
            core.album_art_url = self._artwork_url
            extras["album_art_width"] = self._artwork_width
        extras["client_name"] = self._client_name
        self.emit_connection_state(self._device_connected, core, extras)

    async def _cleanup(self) -> None:
        """Clean up resources."""
        self._cancel_pause_timer()

        if self._metadata_reader:
            await self._metadata_reader.stop()
            self._metadata_reader = None

        self._reset_playback_state()

    def _artwork_is_current(self) -> bool:
        """Whether the cover in hand belongs to the tags on screen.

        Not equality, and the difference is a sender's. Measured on an iPhone
        (2026-09-03): iOS re-sends its bundle several times inside one track
        under an rtptime that keeps advancing -- +1056 frames, 24 ms, three
        AirPlay packets -- so the stamp is the sender's playback position when
        it wrote the request, not the per-track identity rtsp.c describes.
        Equality then holds only when a bundle happens to land on the picture's
        stamp, and when the picture is the one stamped *later* no further
        bundle comes to meet it: the hold expired mid-track and dropped a cover
        that was this very track's, taking AudioPlayerFull off the screen for
        as long as 11 s, four times in 95 publishes.

        What the stamps do carry is order. A picture stamped at or after the
        tags on screen cannot belong to a track already past, so it is the
        cover to show. Only a picture stamped *before* them may be the previous
        track's -- that is the one ARTWORK_SETTLE_SECONDS bounds, and it is the
        order (tags first) the bound was written for.
        """
        if self._artwork_id == self._track_id:
            return True
        if self._artwork_id is None or self._track_id is None:
            return False
        try:
            # RTP timestamps are 32-bit and wrap, so "after" is the serial
            # comparison, not the integer one.
            delta = (int(self._artwork_id) - int(self._track_id)) % (1 << 32)
        except ValueError:
            return False
        return 0 < delta < (1 << 31)

    def _sync_artwork_hold(self) -> None:
        """Arm or release the hold on the cover in hand.

        The tags and the picture are two SET_PARAMETER requests in no
        guaranteed order, so either can be the one still in flight — and the
        gap is the same gap. Tags first leaves the new stamp with no picture
        yet; picture first leaves a picture stamped for a track the tags have
        not announced, and the publish, judging on equality alone, dropped the
        cover from a state still carrying the *previous* track's title. The
        frontend reads a missing album_art_width as "this sender pushes no real
        cover" and swapped the player for the status card and back, which is
        the same flicker from the other side.

        Which of the two is in flight is what `_artwork_is_current` reads off
        the stamps' order, and only the picture that may genuinely be the
        previous track's — the one stamped before the tags — is held on a
        deadline. Arming the deadline for the other one is what emptied the
        cover mid-track on an iPhone.
        """
        if self._artwork_url and not self._artwork_is_current():
            self._start_artwork_settle()
        else:
            self._cancel_artwork_settle()

    def _cancel_artwork_settle(self) -> None:
        """Stop holding the previous cover: the pairing resolved, or is moot."""
        if self._artwork_settle_task:
            self._artwork_settle_task.cancel()
            self._artwork_settle_task = None

    def _start_artwork_settle(self) -> None:
        """Hold the cover in hand while this track's own may still arrive."""
        self._cancel_artwork_settle()

        async def drop_after_delay():
            try:
                await asyncio.sleep(ARTWORK_SETTLE_SECONDS)
            except asyncio.CancelledError:
                return
            # Detached before the publish so it sees no hold and drops the
            # cover -- the coverless state, once, instead of on every event.
            self._artwork_settle_task = None
            self._update_connection_state()

        self._artwork_settle_task = asyncio.create_task(drop_after_delay())

    def _clear_artwork(self) -> None:
        """Clear stored artwork data."""
        self._cancel_artwork_settle()
        self._artwork_data = None
        self._artwork_mime = None
        self._artwork_hash = None
        self._artwork_url = None
        self._artwork_width = 0
        self._artwork_id = None

    # === Public API ===

    def get_artwork(self) -> Optional[Tuple[bytes, str]]:
        """Return current artwork as (data, mime_type), or None."""
        if self._artwork_data and self._artwork_mime:
            return self._artwork_data, self._artwork_mime
        return None
