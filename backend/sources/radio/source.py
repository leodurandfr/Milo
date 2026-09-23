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
from backend.core.models.ws_events import SourceErrorReason
import json
import re
from dataclasses import dataclass, field
from typing import Dict, Any, Optional
from urllib.parse import quote, urlparse

from pydantic import BaseModel

from backend.core.audio_source import Result
from backend.core.models.audio_state import NetworkRequirement
from backend.core.models.session import (
    CommandScope, EndReason, IdlePolicy, Phase, ReroutePolicy, ResumePolicy,
)
from backend.core.models.source_metadata import PlaybackMetadata
from backend.sources.radio.models import PlayStationParams
from backend.shared.artwork_resolver import ArtworkResolver
from backend.sources.radio.data import StationDataService
from backend.sources.radio.shazam import ShazamRecognitionService
from backend.shared.decorators import handle_errors
from backend.shared.mpv_audio_source import MpvAudioSource, MpvSession
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



def _resolved_favicon(favicon: Optional[str]) -> Optional[str]:
    """A station logo as a URL any client can fetch directly.

    `favicon` is whatever the station directory supplied: either a path this
    unit already serves (a custom station's upload, /api/radio/images/...) or
    an arbitrary external URL, which many stations serve behind a WAF that
    refuses a bare User-Agent — hence the proxy. Resolved here because
    `album_art_url` is the cross-source floor, and a floor field a client has
    to post-process is not a floor. The station *lists* keep resolving it
    client-side (frontend/src/utils/faviconUrl.js): those are directory rows,
    not playback metadata, and they carry the raw `favicon` on purpose.
    """
    if not favicon:
        return None
    if favicon.startswith("/"):
        return favicon
    return f"/api/radio/favicon?url={quote(favicon, safe='')}"


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


@dataclass(eq=False)
class RadioSession(MpvSession):
    """One station tuned: from the tune to a named end.

    The in-band title, the Shazam arbitration and the cover being resolved
    all belong to it, so ending the session is what clears them.
    """
    station: Dict[str, Any] = field(default_factory=dict)
    inband_track: Optional[Dict[str, Any]] = None
    inband_seen: bool = False           # the station emits in-band → no Shazam
    inband_poll_ticks: int = 0
    empty_inband_polls: int = 0         # in-band-empty polls since the sound started
    inband_empty_streak: int = 0        # consecutive empty polls once in-band was seen
    shazam_candidate: bool = False      # the station qualifies for the Shazam fallback
    recognition_enabled: bool = True    # the per-station now-playing gate


class RadioSource(MpvAudioSource):
    """
    Radio audio source using MPV.

    Family C (active player): controlled from Milō's UI. A session is one
    station tuned; mpv says when its sound starts and why it ends. A live
    stream has no normal end, so any end after the first sound is a lost
    stream, and every end but that keeps the station to re-tune.
    """

    NETWORK_REQUIREMENT = NetworkRequirement.INTERNET

    IDLE_POLICY = IdlePolicy.AUTO_STOP
    REROUTE = ReroutePolicy.RESTART_AND_RESTORE
    RESUME_POLICY = ResumePolicy(
        # A live stream never ends normally (its end is STREAM_LOST), so EOF is
        # unreachable; every reachable end keeps the station.
        capture_on=frozenset(EndReason) - {EndReason.EOF},
        forget_on=frozenset({EndReason.EOF}),
    )
    SESSION_DAEMON = False

    COMMANDS = {
        "play_station": PlayStationParams,
        "stop": None,
        "resume_playback": None,
        "next": None,
        "prev": None,
    }
    COMMAND_SCOPES = {
        "play_station": CommandScope.CONTENT,
        "stop": CommandScope.RESUME,
        "resume_playback": CommandScope.RESUME,
        "next": CommandScope.RESUME,
        "prev": CommandScope.RESUME,
    }

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
        self._artwork = ArtworkResolver(self._settings_service)

        self._preroll_cache: Dict[str, int] = {}  # hostname → preroll skip seconds (for Shazam)

    @handle_errors(default=False)
    async def initialize(self) -> bool:
        """Initialize station data (call at startup for API access)."""
        await self._station_data.initialize()
        self._logger.info("Radio station data initialized")
        return await super().initialize()

    @property
    def _displayed_station(self) -> Optional[Dict[str, Any]]:
        """The station this source is about: tuned, or the one a play re-tunes."""
        if isinstance(self._session, RadioSession):
            return self._session.station
        point = self._resume_point
        return point.content if point is not None else None

    def _idle_metadata(self) -> Dict[str, Any]:
        """A stopped radio still has a station to re-tune, so publish the full
        projection (same reason the CD keeps a loaded disc visible).

        Nothing ever tuned — a disconnect before the first play — has nothing
        to resume, and falls back to the pair every player reads.
        """
        projection = self._build_playback_metadata()
        return projection if projection else super()._idle_metadata()

    def _resume_content(self, session: RadioSession):
        return session.station.get("id") or "", 0, session.station

    async def _do_start(self) -> bool:
        """Start MPV service and initialize components."""
        try:
            if not await self._start_service_and_wait():
                return False

            # initialize() self-guards on its own loaded flag
            await self._station_data.initialize()

            self._shazam = ShazamRecognitionService(
                settings_service=self._settings_service,
                on_track_changed=self._on_shazam_track_changed
            )

            if not await self._attach_mpv():
                return False
            await self._listen_to_mpv()
            await self._load_auto_stop_config()

            point = self._resume_point
            if point is not None and point.reason is EndReason.REROUTE:
                # A multiroom toggle comes back in the phase it left.
                if point.phase is not Phase.PAUSED:
                    await self._tune(point.content)
                    return True
            self._update_connection_state()
            return True

        except Exception as e:
            self._logger.error(f"Start failed: {e}")
            await self.end_session(EndReason.LOAD_FAILED)
            await self._cleanup()
            return False

    async def _do_stop(self) -> bool:
        await self.end_session(EndReason.SOURCE_SWITCH)
        await self._cleanup()
        return await self._stop_service()

    async def _do_release(self) -> bool:
        await self.end_session(EndReason.REROUTE)
        await self._cleanup()
        return await self._stop_service()

    async def _handle_command(self, cmd: str, params: Optional[BaseModel]) -> Dict[str, Any]:
        """Handle Radio-specific commands."""
        if cmd == "play_station":
            return await self._handle_play_station(params)

        if cmd == "stop":
            return await self._handle_stop_playback()

        if cmd == "resume_playback":
            return await self._handle_resume_playback()

        if cmd in ("next", "prev"):
            return await self._handle_step_favorite(1 if cmd == "next" else -1)

        return self.error_response(f"Unhandled command: {cmd}")

    # === Command Handlers ===

    async def _handle_play_station(self, params: PlayStationParams) -> Dict[str, Any]:
        """Play a radio station: favorite data first, then what the caller
        sent, then the directory."""
        station_id = params.station_id

        try:
            station = None
            if self._station_data.is_favorite(station_id):
                station = self._station_data.get_favorite_metadata_local(station_id)
            if not station and params.station:
                station = params.station
            if not station:
                station = await self._radio_api.get_station_by_id(station_id)
            if not station:
                return self.error_response(f"Station {station_id} not found")

            # Increment Radio Browser counter (fire and forget)
            self._bg.spawn(
                self._radio_api.increment_station_clicks(station_id),
                label="increment_station_clicks",
            )
            return await self._tune(station)

        except Exception as e:
            self._logger.error(f"Station playback error: {e}")
            self.broadcast_error(SourceErrorReason.PLAYBACK_FAILED)
            return self.error_response(str(e))

    async def _tune(self, station: Dict[str, Any]) -> Dict[str, Any]:
        """Open a session on `station` and hand its URL to mpv.

        mpv answering the load says nothing about the stream: its sound
        starting (playback-restart) or its end (end-file) are what tell.
        """
        station_name = station.get('name', 'Unknown')
        self._logger.info(f"Playing station: {station_name}")

        # Replaced, not stopped: the next load replaces the entry in mpv.
        await self.end_session(EndReason.USER_STOP)
        session = RadioSession(phase=Phase.LOADING, station=station)
        session.recognition_enabled = self._station_data.is_station_shazam_enabled(
            station.get('id')
        )
        self.open_session(session)
        self._update_connection_state()

        async def load():
            if await self._mpv_ready() and await self._set_mpv_pause(False):
                return await self._mpv.loadfile(station.get('url'), mode="replace")
            return None

        entry = await self._attempt(load)
        if entry is None:
            await self._end_playback(EndReason.LOAD_FAILED, detail="mpv refused the load")
            return self.error_response(f"Unable to load stream: {station_name}")
        session.entry = entry
        session.link = self._mpv.link

        # In-band metadata (polled while sound plays) is the primary title
        # source. Shazam is a fallback started only if in-band stays empty past
        # the grace period, and needs the global toggle too.
        session.shazam_candidate = bool(
            self._shazam
            and session.recognition_enabled
            and await self._shazam.is_enabled()
        )
        return self.success_response(f"Loading {station_name}", station=station)

    async def _content_finished(self, session: RadioSession) -> None:
        """A live stream has no normal end: mpv's `eof` after the first sound
        is the server going away (measured: a killed server ends in `eof`)."""
        await self._end_playback(EndReason.STREAM_LOST, detail="the stream ended")

    async def _session_ended(self, session: RadioSession, reason: EndReason) -> None:
        """Shazam listens to the session's stream: it stops with it (E44)."""
        if self._shazam:
            await self._shazam.stop()

    async def _handle_stop_playback(self) -> Dict[str, Any]:
        """Stop playback; the station stays to re-tune. Idempotent: nothing
        playing is already the end state asked for."""
        if self._session is not None:
            if self._mpv:
                await self._mpv.stop()
            await self.end_session(EndReason.USER_STOP)
        self._update_connection_state()
        return self.success_response("Playback stopped")

    async def _handle_resume_playback(self) -> Dict[str, Any]:
        """Re-tune the station on screen: the live one, or the one kept."""
        station = self._displayed_station
        if not station:
            return self.error_response("No station to resume")
        if not station.get('id'):
            return self.error_response("Last station has no id, cannot resume")
        return await self._tune(station)

    async def _handle_step_favorite(self, offset: int) -> Dict[str, Any]:
        """Play the favorite station `offset` places from the current one.

        A live stream has no track to skip, so next/prev step the favorites
        list instead — that is what the rotary, the IR remote and the iOS lock
        screen send. The list wraps. A station played from a search is not in
        it and has no neighbor, so stepping enters the list at its first
        entry (next) or its last (prev); when nothing is tuned the kept
        station stands in, so a press after `stop` resumes the walk where it
        left off.
        """
        favorites = self._station_data.favorite_ids
        if not favorites:
            return self.error_response("No favorite station to step to")

        station = self._displayed_station
        current_id = station.get('id') if station else None

        if current_id in favorites:
            index = (favorites.index(current_id) + offset) % len(favorites)
        else:
            index = 0 if offset > 0 else len(favorites) - 1

        station_id = favorites[index]
        if (
            station_id == current_id
            and self._session is not None
            and self._session.phase is Phase.PLAYING
        ):
            # Single favorite: re-tuning the station already playing would
            # only cost a re-buffer.
            return self.success_response("Already on the only favorite station")

        return await self._handle_play_station(PlayStationParams(station_id=station_id))

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
            try:
                stdout, _ = await asyncio.wait_for(process.communicate(), timeout=10)
            except asyncio.TimeoutError:
                # ffprobe is holding an open HTTP connection to the station.
                # Left running it keeps pulling the stream for the rest of the
                # session, and the station counts a listener that is not there.
                process.kill()
                await process.wait()
                raise
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
        session = self._session
        if not isinstance(session, RadioSession):
            return None
        if session.inband_track:
            return session.inband_track
        return self._shazam.current_track if self._shazam else None

    @staticmethod
    def _track_key(track: Optional[Dict[str, Any]]):
        """Identity of a track for change detection (None when absent)."""
        if not track:
            return None
        return (track.get("title"), track.get("artist"))

    def _build_playback_metadata(self) -> Dict[str, Any]:
        """The station projection — the tuned one, or the one a play would re-tune.

        The recognised track is deliberately absent when nothing is tuned: it
        annotates a stream that is running, and the identity a stopped radio
        carries is the station.
        """
        station = self._displayed_station
        if not station:
            return {}

        track = self._resolve_track()
        station_name = station.get('name')
        is_playing, is_buffering = self._flags(self._session.phase if self._session else None)

        return {
            "station_id": station.get('id'),
            "station_name": station_name,
            "station_url": station.get('url'),
            "country": station.get('country'),
            "genre": station.get('genre'),
            "favicon": station.get('favicon'),
            "bitrate": station.get('bitrate'),
            "codec": station.get('codec'),
            "is_favorite": self._station_data.is_favorite(
                station.get('id')
            ) if self._station_data else False,
            "is_playing": is_playing,
            "is_buffering": is_buffering,
            # The cross-source floor every generic consumer reads (lock screen,
            # widget, shared player). Radio has two layers and this is the
            # one-line view of them: the recognised track when there is one,
            # the station otherwise. Computing it here is what lets the clients
            # stop each re-deriving it — see core/push/payloads.py.
            "title": track["title"] if track else station_name,
            "artist": track["artist"] if track else None,
            "album": station_name,
            "album_art_url": (
                track["artwork"] if track and track.get("artwork")
                else _resolved_favicon(station.get('favicon'))
            ),
            # The two layers, kept apart for the consumers that draw them apart.
            "track_title": track["title"] if track else None,
            "track_artist": track["artist"] if track else None,
            "track_artwork": track["artwork"] if track else None
        }

    def _update_connection_state(self, extras: Optional[Dict[str, Any]] = None) -> None:
        """The source's one publish site."""
        self.emit_connection_state(*self._connection_state())

    def _connection_state(self):
        core, extras = PlaybackMetadata.split(self._build_playback_metadata())
        return self._session is not None, core, extras

    async def on_shazam_setting_changed(self, enabled: bool) -> bool:
        """React to global Shazam toggle change."""
        if not self._shazam:
            return True
        # Applied in the actor: it reads and writes the live session.
        await self._submit(Result(lambda: self._apply_shazam_setting(enabled)))
        return True

    async def _apply_shazam_setting(self, enabled: bool) -> None:
        session = self._session
        if enabled:
            # Re-arm the fallback only if the playing station is not opted out.
            # In-band metadata stays primary; if in-band has already taken over
            # (or is present), leave Shazam off — otherwise the grace logic
            # starts it once in-band stays empty.
            if isinstance(session, RadioSession) and session.phase is Phase.PLAYING:
                station_id = session.station.get('id')
                stream_url = session.station.get('url')
                if stream_url and self._station_data.is_station_shazam_enabled(station_id):
                    if session.inband_seen or session.inband_track:
                        session.shazam_candidate = False
                    else:
                        session.shazam_candidate = True
                        session.empty_inband_polls = 0
        else:
            if isinstance(session, RadioSession):
                session.shazam_candidate = False
            await self._shazam.stop()

    async def _on_shazam_track_changed(self, track) -> None:
        """Callback from ShazamRecognitionService (its own task): posted."""
        session = self._session
        if session is not None:
            self._post_result(self._republish_track, token=session)

    async def _republish_track(self) -> None:
        if self._session is not None and self._session.phase is Phase.PLAYING:
            self._update_connection_state()

    async def _cleanup(self) -> None:
        """Clean up resources."""
        if self._shazam:
            await self._shazam.stop()
            self._shazam = None
        await self._detach_mpv()

    # === While sound plays ===

    async def _on_playing_tick(self, session: RadioSession) -> None:
        await self._poll_inband_metadata(session)

    async def _poll_inband_metadata(self, session: RadioSession) -> None:
        """Read mpv in-band metadata; it is the primary now-playing source.

        In-band metadata (ICY StreamTitle / HLS tags) is instant and exact when
        present, so it overrides Shazam: the first in-band title shuts any
        running Shazam loop down. When in-band stays empty past a short grace
        period, Shazam starts as the fallback (metadata-less streams like
        Radio France). Polled every _INBAND_POLL_TICKS ticks of sound.

        Skipped entirely when the station is opted out of now-playing — the
        per-station gate must suppress in-band titles too, not just Shazam.
        """
        if not session.recognition_enabled:
            return

        session.inband_poll_ticks += 1
        if session.inband_poll_ticks < _INBAND_POLL_TICKS:
            return
        session.inband_poll_ticks = 0

        metadata = await self._mpv.get_metadata()
        track = _parse_inband_track(metadata)

        if track:
            session.empty_inband_polls = 0
            session.inband_empty_streak = 0
            if not session.inband_seen:
                session.inband_seen = True
                # In-band wins over Shazam — shut the fallback down.
                if self._shazam and self._shazam.is_running:
                    await self._shazam.stop()
            if self._track_key(track) != self._track_key(session.inband_track):
                session.inband_track = track
                self._update_connection_state()
                # In-band carries no artwork — resolve a cover off the actor
                # (iTunes Search), then patch it in if the track is still up.
                self._session_bg.spawn(
                    self._resolve_inband_artwork(session, track),
                    label="inband_artwork",
                )
            return

        # In-band empty this poll. A brief gap between tracks is normal, so keep
        # the last title for a few polls; clear it only after sustained silence
        # (ad/talk/dead air) so in-band can't leave a phantom title pinned.
        if session.inband_seen:
            if session.inband_track is not None:
                session.inband_empty_streak += 1
                if session.inband_empty_streak >= _INBAND_STALE_CLEAR_POLLS:
                    session.inband_track = None
                    session.inband_empty_streak = 0
                    self._update_connection_state()
            return

        # Never seen in-band for this station → count toward the Shazam grace,
        # then start the fallback once (candidacy is consumed to avoid re-arming).
        session.empty_inband_polls += 1
        if (
            session.shazam_candidate
            and self._shazam
            and not self._shazam.is_running
            and session.empty_inband_polls * _INBAND_POLL_TICKS >= _SHAZAM_GRACE_TICKS
        ):
            stream_url = session.station.get('url')
            if stream_url:
                session.shazam_candidate = False
                # The preroll probe (ffprobe) runs off the actor: up to ~10 s.
                self._session_bg.spawn(
                    self._start_shazam_fallback(session, stream_url),
                    label="shazam_fallback_start",
                )

    async def _resolve_inband_artwork(self, session: RadioSession, track: Dict[str, Any]) -> None:
        """Resolve cover art for an in-band track, applied if still current."""
        artwork = await self._artwork.resolve(
            track.get("artist", ""), track.get("title", "")
        )
        if not artwork:
            return

        async def apply() -> None:
            if session.inband_track is track and session.phase is Phase.PLAYING:
                track["artwork"] = artwork
                self._update_connection_state()

        self._post_result(apply, token=session)

    async def _start_shazam_fallback(self, session: RadioSession, stream_url: str) -> None:
        """Detect preroll and start Shazam, unless in-band appeared meanwhile."""
        if not self._shazam:
            return
        preroll = await self._detect_preroll(stream_url)

        async def apply() -> None:
            if self._shazam and session.phase is Phase.PLAYING and not session.inband_seen:
                await self._shazam.start(stream_url, preroll_skip=preroll)

        self._post_result(apply, token=session)

    # === Public API ===

    @property
    def station_data(self) -> Optional[StationDataService]:
        """Get station data service."""
        return self._station_data

    @property
    def radio_api(self) -> Optional[RadioBrowserAPI]:
        """Get RadioBrowser API client."""
        return self._radio_api
