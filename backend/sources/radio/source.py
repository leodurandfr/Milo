# backend/sources/radio/source.py
"""
Radio audio source using MPV.

This source handles streaming audio from internet radio stations via MPV.
It provides station management (favorites, custom stations), RadioBrowser
API integration, and playback control.

Features:
- MPV IPC for playback control
- RadioBrowser API for station search
- Local favorites and custom stations
- Stream fallback mechanism
- Station image management
"""
import asyncio
import json
import re
from typing import Dict, Any, Optional
from urllib.parse import urlparse

from pydantic import BaseModel

from backend.core.models.audio_state import NetworkRequirement, SourceState
from backend.core.models.source_metadata import PlaybackMetadata
from backend.sources.radio.models import PlayStationParams, RemoveFavoriteParams
from backend.shared.artwork_resolver import ArtworkResolver
from backend.sources.radio.data import StationDataService
from backend.sources.radio.shazam import ShazamRecognitionService
from backend.shared.decorators import handle_errors
from backend.shared.mpv import MpvController
from backend.shared.mpv_audio_source import MpvAudioSource
from backend.sources.radio.browser_api import RadioBrowserAPI

# In-band metadata polling (primary now-playing source). The monitor ticks
# ~1 s; read mpv metadata every _INBAND_POLL_TICKS ticks. If in-band stays
# empty for _SHAZAM_GRACE_TICKS ticks after playback starts, Shazam kicks in
# as the fallback (covers metadata-less streams like Radio France).
_INBAND_POLL_TICKS = 4
_SHAZAM_GRACE_TICKS = 8

# Consecutive empty in-band polls (once in-band has been seen) after which a
# stale pinned title is cleared. A brief gap between tracks keeps the last
# title; sustained silence (ad/talk/dead air with an empty StreamTitle) clears
# it — mirrors Shazam's STALE_CLEAR_ROUNDS so in-band can't pin a phantom
# title either. 4 polls × ~4 s ≈ 16 s of continuous empty metadata.
_INBAND_STALE_CLEAR_POLLS = 4

# Trailing station-promo suffix some broadcasters append to the ICY title,
# e.g. "Artist - Title - WALM Radio on walmradio.com". Stripped before parsing.
_INBAND_PROMO_RE = re.compile(r"\s*-\s*[^-]+\son\s+\S+\.\S+\s*$", re.IGNORECASE)

# Trailing source marker some stations append to every title (walmradio's
# "(Vinyl)", plus mono/stereo tags) — pure noise, dropped from the display
# title. Meaningful parentheticals ("(with …)", "(feat. …)") are kept.
_INBAND_TITLE_NOISE_RE = re.compile(
    r"\s*\((?:vinyl|mono|stereo)\)\s*$", re.IGNORECASE
)


def _parse_inband_track(metadata: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """Extract a {title, artist, artwork} track from mpv in-band metadata.

    Reads the ICY StreamTitle (mpv key `icy-title`), strips a trailing
    station-promo suffix, and best-effort splits the artist from the title.
    Two separator conventions are handled: "Artist - Title" (most ICY streams)
    and "Title by Artist" (walmradio Classic Vinyl / Adroit Jazz). Returns
    None when no usable title is present. The station name (`icy-name`) is
    deliberately NOT used as a track title — that would show the station as
    the song. `artwork` is always None here; it is resolved asynchronously
    from the artist/title (see RadioSource._resolve_inband_artwork).
    """
    raw = (metadata.get("icy-title") or metadata.get("streamtitle") or "").strip()
    if not raw:
        return None

    cleaned = _INBAND_PROMO_RE.sub("", raw).strip()
    if not cleaned:
        return None

    artist, title = "", cleaned
    if " - " in cleaned:
        left, _, right = cleaned.partition(" - ")
        if left.strip() and right.strip():
            artist, title = left.strip(), right.strip()
    elif " by " in cleaned:
        # "<Title> by <Artist>" (walmradio jazz/vinyl: titles and artists are
        # usually multi-word). " by " is ambiguous with song titles that contain
        # it literally ("Stand by Me"), so only split when at least one side is
        # multi-word — a bare "<word> by <word>" is kept whole rather than
        # mangled into a wrong artist (a wrong artist is worse than none).
        left, _, right = cleaned.partition(" by ")
        left, right = left.strip(), right.strip()
        if left and right and (len(left.split()) > 1 or len(right.split()) > 1):
            title, artist = left, right

    title = _INBAND_TITLE_NOISE_RE.sub("", title).strip()
    if not title:
        return None

    return {"title": title, "artist": artist, "artwork": None}


class RadioSource(MpvAudioSource):
    """
    Radio audio source using MPV.

    Family C (active player): controlled from Milō's UI. Extends MpvAudioSource
    (BaseAudioSource subclass) — implements playback and station commands.
    """

    NETWORK_REQUIREMENT = NetworkRequirement.INTERNET

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        state_machine=None,
        settings_service=None,
        systemd_manager=None
    ):
        super().__init__(
            source_id="radio",
            service_name="milo-radio.service",
            state_machine=state_machine,
            systemd_manager=systemd_manager,
            settings_service=settings_service,
            config=config
        )

        # Station data service (initialized immediately for API access)
        self._station_data = StationDataService(
            state_machine=state_machine
        )

        # RadioBrowser API (initialized immediately for API access)
        self._radio_api = RadioBrowserAPI(
            station_manager=self._station_data
        )
        self._station_data.radio_api = self._radio_api

        self._shazam: Optional[ShazamRecognitionService] = None

        # Resolves cover art for in-band tracks (which carry no artwork) from
        # their artist/title via the iTunes Search API.
        self._artwork = ArtworkResolver()

        # State
        self._metadata: Dict[str, Any] = {}
        self._current_station: Optional[Dict[str, Any]] = None
        self._last_station: Optional[Dict[str, Any]] = None
        self._preroll_cache: Dict[str, int] = {}  # hostname → preroll skip seconds (for Shazam)
        self._buffering_ticks: int = 0

        # In-band metadata (primary title source). Shazam is the fallback,
        # started only when in-band stays empty (see _poll_inband_metadata).
        self._inband_track: Optional[Dict[str, Any]] = None
        self._inband_seen: bool = False        # station emits in-band → suppress Shazam
        self._inband_poll_ticks: int = 0
        self._empty_inband_ticks: int = 0      # in-band-empty polls since play start
        self._inband_empty_streak: int = 0     # consecutive empty polls after in-band seen
        self._shazam_candidate: bool = False   # station qualifies for Shazam fallback
        # Per-station now-playing gate ("Reconnaissance des morceaux de cette
        # station"). When a station is opted out, NO track is shown — neither
        # in-band metadata NOR Shazam. Set per play in _handle_play_station.
        self._recognition_enabled: bool = True

    @handle_errors(default=False)
    async def initialize(self) -> bool:
        """Initialize station data (call at startup for API access)."""
        await self._station_data.initialize()
        self._logger.info("Radio station data initialized")
        return await super().initialize()

    def _reset_playback_state(self) -> None:
        super()._reset_playback_state()
        self._last_station = self._current_station or self._last_station
        self._current_station = None
        self._reset_inband_state()

    def _reset_inband_state(self) -> None:
        """Clear in-band metadata / Shazam-arbitration state between stations."""
        self._inband_track = None
        self._inband_seen = False
        self._inband_poll_ticks = 0
        self._empty_inband_ticks = 0
        self._inband_empty_streak = 0
        self._shazam_candidate = False

    async def _do_start(self) -> bool:
        """Start MPV service and initialize components."""
        try:
            # 1. Start service
            if not await self._start_service_and_wait():
                return False

            # 2. Ensure station data is initialized (initialize() self-guards
            #    on its own loaded flag, so calling it twice is a no-op)
            await self._station_data.initialize()

            # 3. Create Shazam recognition service
            self._shazam = ShazamRecognitionService(
                settings_service=self._settings_service,
                on_track_changed=self._on_shazam_track_changed
            )

            # 4. Connect to MPV IPC
            self._mpv = MpvController(ipc_socket_path=self._mpv_socket)
            if not await self._mpv.connect():
                self._logger.error("Failed to connect to MPV IPC")
                return False

            # 5. Reset state and load auto-stop config
            self._reset_playback_state()
            await self._load_auto_stop_config()

            # 6. Start monitor task
            self._start_monitor()

            # 7. Update state
            self._update_connection_state()

            return True

        except Exception as e:
            self._logger.error(f"Start failed: {e}")
            await self._cleanup()
            return False


    COMMANDS = {
        "play_station": PlayStationParams,
        "stop": None,
        "resume_playback": None,
        "add_favorite": PlayStationParams,
        "remove_favorite": RemoveFavoriteParams,
    }

    async def _handle_command(self, cmd: str, params: Optional[BaseModel]) -> Dict[str, Any]:
        """Handle Radio-specific commands."""
        if cmd == "play_station":
            return await self._handle_play_station(params)

        if cmd == "stop":
            return await self._handle_stop_playback()

        if cmd == "resume_playback":
            return await self._handle_resume_playback()

        if cmd == "add_favorite":
            return await self._handle_add_favorite(params)

        if cmd == "remove_favorite":
            return await self._handle_remove_favorite(params)

        return self.error_response(f"Unhandled command: {cmd}")

    # === Command Handlers ===

    async def _handle_play_station(self, params: PlayStationParams) -> Dict[str, Any]:
        """Play a radio station with fallback to alternative URLs."""
        station_id = params.station_id

        try:
            # Get station with fallback chain: local favorite → provided → API
            station = None
            provided_station = params.station

            # 1. Try local data for favorites
            if self._station_data.is_favorite(station_id):
                station = self._station_data.get_favorite_metadata_local(station_id)

            # 2. Fallback to provided station
            if not station and provided_station:
                station = provided_station

            # 3. Fallback to API
            if not station:
                station = await self._radio_api.get_station_by_id(station_id)

            if not station:
                return self.error_response(f"Station {station_id} not found")

            station_name = station.get('name', 'Unknown')
            primary_url = station.get('url')

            self._logger.info(f"Playing station: {station_name}")

            # Increment Radio Browser counter (fire and forget)
            self._bg.spawn(
                self._radio_api.increment_station_clicks(station_id),
                label="increment_station_clicks",
            )

            # Stop current playback before switching stations
            if self._shazam:
                await self._shazam.stop()
            if self._mpv and self._is_playing:
                await self._mpv.stop()
                self._is_playing = False

            # Update state: buffering in progress (broadcast immediately for responsive UI)
            self._reset_inband_state()
            self._current_station = station
            self._is_buffering = True
            self._buffering_ticks = 0
            self._metadata = self._build_playback_metadata()
            self._update_connection_state()

            # Try to play with fallback mechanism
            working_url = await self._try_play_with_fallback(station)

            if not working_url:
                self._is_buffering = False
                self._current_station = None
                error_msg = f"Unable to load stream: {station_name}"
                self.broadcast_error(error_msg)
                return self.error_response(error_msg)

            # Update station URL if we used an alternative
            if working_url != primary_url:
                station['url'] = working_url
                self._current_station = station
                self._metadata = self._build_playback_metadata()

            # Per-station now-playing gate: when the station is opted out via
            # ManageStation, show no track at all (neither in-band nor Shazam).
            self._recognition_enabled = self._station_data.is_station_shazam_enabled(
                station_id
            )

            # In-band metadata (polled by the monitor) is the primary title
            # source. Shazam is a fallback started only if in-band stays empty
            # past the grace period — see _poll_inband_metadata. It additionally
            # requires the global Shazam toggle (in-band needs neither).
            self._shazam_candidate = bool(
                self._shazam
                and self._recognition_enabled
                and await self._shazam.is_enabled()
            )

            return self.success_response(f"Loading {station_name}", station=station)

        except Exception as e:
            self._logger.error(f"Station playback error: {e}")
            self._is_buffering = False
            self.broadcast_error(str(e))
            return self.error_response(str(e))

    async def _try_play_with_fallback(
        self, station: Dict[str, Any], max_alternatives: int = 3
    ) -> Optional[str]:
        """Try to play a station with fallback to alternative URLs."""
        station_name = station.get('name', 'Unknown')
        primary_url = station.get('url')

        # Try primary URL
        if await self._try_single_url(primary_url):
            return primary_url

        self._logger.info(f"Primary URL failed for {station_name}, searching alternatives...")

        # Find and try alternative URLs — anchored to the same broadcaster
        # (uuid/host/country), never a different station that shares the name.
        alternatives = await self._radio_api.find_alternative_urls(
            station, exclude_url=primary_url
        )

        if not alternatives:
            return None

        for i, alt_station in enumerate(alternatives[:max_alternatives]):
            alt_url = alt_station.get('url')
            if not alt_url:
                continue

            if await self._try_single_url(alt_url):
                self._logger.info(f"Alternative URL {i+1} works for {station_name}")
                return alt_url

        return None

    async def _try_single_url(self, url: str) -> bool:
        """Try to play a single URL in mpv."""
        if not self._mpv:
            self._logger.error("MPV not connected, cannot load stream")
            return False
        success = await self._mpv.load_stream(url)
        if not success:
            self._logger.debug(f"mpv load_stream failed for: {url[:80]}")
        return success

    async def _auto_stop_action(self) -> None:
        """Stop playback in place after pause/silence timeout."""
        await self._handle_stop_playback()

    async def _handle_stop_playback(self) -> Dict[str, Any]:
        """Stop playback and reset to READY state."""
        try:
            self._is_playing = False
            self._is_buffering = False
            # Stop is an explicit user action — drop any pending pause timer
            # (mpv's pause property can stay True after `stop`).
            self._handle_pause_change(False)

            if self._shazam:
                await self._shazam.stop()

            await self._mpv.stop()

            self._last_station = self._current_station
            self._current_station = None
            self._metadata = {"is_playing": False, "is_buffering": False}
            self.set_state(SourceState.READY, self._metadata)

            return self.success_response("Playback stopped")

        except Exception as e:
            return self.error_response(str(e))

    async def _handle_resume_playback(self) -> Dict[str, Any]:
        """Resume playback of the last station."""
        if not self._last_station:
            return self.error_response("No station to resume")

        station_id = self._last_station.get('id', '')
        if not station_id:
            return self.error_response("Last station has no id, cannot resume")
        return await self._handle_play_station(
            PlayStationParams(station_id=station_id, station=self._last_station)
        )

    async def _handle_add_favorite(self, params: PlayStationParams) -> Dict[str, Any]:
        """Add station to favorites."""
        station_id = params.station_id

        station = params.station
        if not station:
            station = await self._radio_api.get_station_by_id(station_id)

        if not station:
            return self.error_response(f"Station {station_id} not found")

        success = await self._station_data.add_favorite(station_id, station)
        return (
            self.success_response("Station added to favorites")
            if success else self.error_response("Add favorite failed")
        )

    async def _handle_remove_favorite(self, params: RemoveFavoriteParams) -> Dict[str, Any]:
        """Remove station from favorites."""
        station_id = params.station_id

        success = await self._station_data.remove_favorite(station_id)
        return (
            self.success_response("Station removed from favorites")
            if success else self.error_response("Remove favorite failed")
        )

    # === Helpers ===

    async def _detect_preroll(self, url: str) -> int:
        """Detect pre-roll ad duration from stream ICY metadata via ffprobe.

        Some streaming servers (e.g. Infomaniak) inject pre-roll ads on each
        new HTTP connection. Results are cached per hostname so only the first
        play of a given host pays the probe cost.
        """
        hostname = urlparse(url).hostname or ""
        if hostname in self._preroll_cache:
            return self._preroll_cache[hostname]

        skip = 0
        try:
            process = await asyncio.create_subprocess_exec(
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_format", url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=10)
            if process.returncode == 0 and stdout:
                tags = json.loads(stdout).get("format", {}).get("tags", {})
                if tags.get("insertionType") == "preroll":
                    duration_ms = int(tags.get("durationMilliseconds", 0))
                    if duration_ms > 0:
                        skip = (duration_ms // 1000) + 2
                        self._logger.info(f"Pre-roll ad detected ({duration_ms}ms), skipping {skip}s")
        except Exception as e:
            self._logger.debug(f"Preroll detection failed for {hostname}: {e}")

        self._preroll_cache[hostname] = skip
        return skip

    def _resolve_track(self) -> Optional[Dict[str, Any]]:
        """Current now-playing track: in-band metadata is primary, Shazam fallback."""
        if self._inband_track:
            return self._inband_track
        return self._shazam.current_track if self._shazam else None

    @staticmethod
    def _track_key(track: Optional[Dict[str, Any]]):
        """Identity of a track for change detection (None when absent)."""
        if not track:
            return None
        return (track.get("title"), track.get("artist"))

    def _build_playback_metadata(self, track_override=None) -> Dict[str, Any]:
        """Build metadata dict for current station, enriched with now-playing track."""
        if not self._current_station:
            return {}

        track = track_override if track_override is not None else self._resolve_track()

        return {
            "station_id": self._current_station.get('id'),
            "station_name": self._current_station.get('name'),
            "station_url": self._current_station.get('url'),
            "country": self._current_station.get('country'),
            "genre": self._current_station.get('genre'),
            "favicon": self._current_station.get('favicon'),
            "bitrate": self._current_station.get('bitrate'),
            "codec": self._current_station.get('codec'),
            "is_favorite": self._station_data.is_favorite(
                self._current_station.get('id')
            ) if self._station_data else False,
            "is_playing": self._is_playing,
            "is_buffering": self._is_buffering,
            # Shazam track recognition data
            "track_title": track["title"] if track else None,
            "track_artist": track["artist"] if track else None,
            "track_artwork": track["artwork"] if track else None
        }

    def _update_connection_state(self) -> None:
        """Update state based on playback."""
        if self._current_station and self._is_playing:
            self.broadcast_error_cleared()
        core, extras = PlaybackMetadata.split(self._build_playback_metadata())
        self.emit_connection_state(bool(self._current_station), core, extras)

    async def on_shazam_setting_changed(self, enabled: bool) -> bool:
        """React to global Shazam toggle change."""
        if not self._shazam:
            return True

        if enabled:
            # Re-arm the fallback only if the playing station is not opted out.
            # In-band metadata stays primary; if in-band has already taken over
            # (or is present), leave Shazam off — otherwise the monitor's grace
            # logic starts it once in-band stays empty.
            if self._current_station and self._is_playing:
                station_id = self._current_station.get('id')
                stream_url = self._current_station.get('url')
                if stream_url and self._station_data.is_station_shazam_enabled(station_id):
                    if self._inband_seen or self._inband_track:
                        self._shazam_candidate = False
                    else:
                        self._shazam_candidate = True
                        self._empty_inband_ticks = 0
        else:
            # Stop recognition loop and clear track info
            self._shazam_candidate = False
            await self._shazam.stop()

        return True

    async def _on_shazam_track_changed(self, track) -> None:
        """Callback from ShazamRecognitionService when a new track is detected."""
        if self._current_station and self._is_playing:
            self._metadata = self._build_playback_metadata()
            self._update_connection_state()

    async def _cleanup(self) -> None:
        """Clean up resources."""
        self._stop_monitor()

        if self._shazam:
            await self._shazam.stop()
            self._shazam = None

        if self._mpv:
            await self._mpv.disconnect()
            self._mpv = None

        # Note: station_data and radio_api persist for API access when radio is inactive
        self._reset_playback_state()

    # === Monitor hooks ===

    async def _on_mpv_disconnect(self) -> None:
        """Handle unexpected mpv disconnect."""
        self._is_playing = False
        self._is_buffering = False
        self._current_station = None
        self._metadata = {}

    async def _on_monitor_tick(self) -> None:
        """Check playback state transitions."""
        was_playing = self._is_playing
        self._is_playing = await self._mpv.is_playing()

        # Auto-stop on mpv pause edges. Radio doesn't expose a pause
        # control in the UI, so this is mostly defensive — but it keeps
        # behavior uniform with the other mpv sources if mpv ever pauses.
        if self._current_station:
            pause_state = await self._mpv.get_property("pause")
            if pause_state is not None:
                self._handle_pause_change(bool(pause_state))

        if self._is_playing and not was_playing:
            # Started playing - buffering complete
            self._is_buffering = False
            self._metadata = self._build_playback_metadata()
            self._update_connection_state()

        elif not self._is_playing and was_playing:
            self._metadata = self._build_playback_metadata()
            self._update_connection_state()

        elif self._is_buffering and not self._is_playing:
            # Give mpv time to start loading before checking for failure.
            # idle-active is briefly True between loadfile and actual stream load.
            self._buffering_ticks += 1
            if self._buffering_ticks >= 5:
                idle = await self._mpv.get_property("idle-active")
                if idle:
                    station_name = self._current_station.get('name', 'Unknown') if self._current_station else 'Unknown'
                    self._logger.info(f"Stream load failed for {station_name} (mpv returned to idle)")
                    self._is_buffering = False
                    self._current_station = None
                    self._metadata = {}
                    self.broadcast_error(f"Unable to load stream: {station_name}")
                    self._update_connection_state()

        if self._current_station and self._is_playing:
            await self._poll_inband_metadata()

    async def _poll_inband_metadata(self) -> None:
        """Read mpv in-band metadata; it is the primary now-playing source.

        In-band metadata (ICY StreamTitle / HLS tags) is instant and exact when
        present, so it overrides Shazam: the first in-band title shuts any
        running Shazam loop down. When in-band stays empty past a short grace
        period, Shazam starts as the fallback (metadata-less streams like
        Radio France). Polled every _INBAND_POLL_TICKS monitor ticks.

        Skipped entirely when the station is opted out of now-playing — the
        per-station gate must suppress in-band titles too, not just Shazam.
        """
        if not self._recognition_enabled:
            return

        self._inband_poll_ticks += 1
        if self._inband_poll_ticks < _INBAND_POLL_TICKS:
            return
        self._inband_poll_ticks = 0

        metadata = await self._mpv.get_metadata()
        track = _parse_inband_track(metadata)

        if track:
            self._empty_inband_ticks = 0
            self._inband_empty_streak = 0
            if not self._inband_seen:
                self._inband_seen = True
                # In-band wins over Shazam — shut the fallback down.
                if self._shazam and self._shazam.is_running:
                    await self._shazam.stop()
            if self._track_key(track) != self._track_key(self._inband_track):
                self._inband_track = track
                self._metadata = self._build_playback_metadata()
                self._update_connection_state()
                # In-band carries no artwork — resolve a cover off the monitor
                # (iTunes Search), then patch it in if the track is still up.
                self._bg.spawn(
                    self._resolve_inband_artwork(track),
                    label="inband_artwork",
                )
            return

        # In-band empty this poll. A brief gap between tracks is normal, so keep
        # the last title for a few polls; clear it only after sustained silence
        # (ad/talk/dead air) so in-band can't leave a phantom title pinned.
        if self._inband_seen:
            if self._inband_track is not None:
                self._inband_empty_streak += 1
                if self._inband_empty_streak >= _INBAND_STALE_CLEAR_POLLS:
                    self._inband_track = None
                    self._inband_empty_streak = 0
                    self._metadata = self._build_playback_metadata()
                    self._update_connection_state()
            return

        # Never seen in-band for this station → count toward the Shazam grace,
        # then start the fallback once (candidacy is consumed to avoid re-arming).
        self._empty_inband_ticks += 1
        if (
            self._shazam_candidate
            and self._shazam
            and not self._shazam.is_running
            and self._empty_inband_ticks * _INBAND_POLL_TICKS >= _SHAZAM_GRACE_TICKS
        ):
            stream_url = self._current_station.get('url') if self._current_station else None
            if stream_url:
                self._shazam_candidate = False
                # Preroll probe (ffprobe) runs off the monitor to avoid stalling
                # playback-state checks for up to ~10 s.
                self._bg.spawn(
                    self._start_shazam_fallback(stream_url),
                    label="shazam_fallback_start",
                )

    async def _resolve_inband_artwork(self, track: Dict[str, Any]) -> None:
        """Resolve cover art for an in-band track and patch it in if still current.

        Runs off the monitor (spawned via `_bg`). The artwork is applied only
        when `track` is still the live in-band track — a newer title that
        arrived during the lookup must not be overwritten with a stale cover.
        """
        artwork = await self._artwork.resolve(
            track.get("artist", ""), track.get("title", "")
        )
        if not artwork:
            return
        if self._inband_track is track and self._current_station and self._is_playing:
            track["artwork"] = artwork
            self._metadata = self._build_playback_metadata()
            self._update_connection_state()

    async def _start_shazam_fallback(self, stream_url: str) -> None:
        """Detect preroll and start Shazam, unless in-band appeared meanwhile."""
        if not self._shazam:
            return
        preroll = await self._detect_preroll(stream_url)
        # Station may have changed / in-band arrived during the probe.
        if self._current_station and self._is_playing and not self._inband_seen:
            await self._shazam.start(stream_url, preroll_skip=preroll)

    # === Public API ===

    @property
    def station_data(self) -> Optional[StationDataService]:
        """Get station data service."""
        return self._station_data

    @property
    def radio_api(self) -> Optional[RadioBrowserAPI]:
        """Get RadioBrowser API client."""
        return self._radio_api

