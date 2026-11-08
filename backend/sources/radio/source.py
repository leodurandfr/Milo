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
from dataclasses import dataclass, field, replace
from typing import Dict, Any, List, Optional
from urllib.parse import quote, urlparse

from pydantic import BaseModel

from backend.core import audio_source
from backend.core.audio_source import DeviceToken, Result
from backend.core.models.audio_state import NetworkRequirement
from backend.core.models.audio_wire import RadioDetails, RadioStation, RadioTrack, ResumeView
from backend.core.models.session import (
    CommandScope, EndReason, IdlePolicy, Phase, ReroutePolicy, ResumePolicy,
)
from backend.sources.radio.models import PlayStationParams
from backend.shared.artwork_resolver import ArtworkResolver
from backend.sources.radio.data import StationDataService
from backend.sources.radio.logos import StationLogos
from backend.sources.radio import radiofrance
from backend.sources.radio.playlist import is_playlist_url, resolve_stream_url
from backend.sources.radio.shazam import RecognitionQuota, ShazamRecognitionService
from backend.shared.mpv_audio_source import MpvAudioSource, MpvSession
from backend.sources.radio.browser_api import RadioBrowserAPI
from backend.shared.network import describe_network_error

# In-band metadata polling (primary now-playing source). The monitor ticks
# ~1 s; read mpv metadata every _INBAND_POLL_TICKS ticks. If in-band stays
# empty for _SHAZAM_GRACE_TICKS ticks after playback starts, Shazam kicks in
# as the fallback (metadata-less streams). On a Radio France station the
# grace also waits for Radio France's own feed to answer, and Shazam never
# starts once that feed has named a song.
_INBAND_POLL_TICKS = 4
_SHAZAM_GRACE_TICKS = 8

# Consecutive empty in-band polls (once in-band has been seen) after which a
# stale pinned title is cleared. A brief gap between tracks keeps the last
# title; sustained silence (ad/talk/dead air with an empty StreamTitle) clears
# it, so in-band can't pin a phantom title (Shazam's own expiry is
# shazam.STALE_AFTER_S). 4 polls × ~4 s ≈ 16 s of continuous empty metadata.
_INBAND_STALE_CLEAR_POLLS = 4

# The session timers of Radio France's feed: the next read, and the next
# moment the song heard can change (radiofrance.schedule).
_RF_POLL_TIMER = "radiofrance_poll"
_RF_SHOW_TIMER = "radiofrance_show"

# How long Radio France's feed may go unanswered before it is reported, once.
# A failed read is retried at the normal cadence, so this is two or three
# misses: one is a blip (one TLS timeout in ~4 170 reads, measured).
_RF_OUTAGE_S = 60.0

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
    `artwork` is the cross-source floor, and a floor field a client has to
    post-process is not a floor. The station *lists* keep resolving it
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

    The in-band title, Radio France's feed, the Shazam arbitration and the
    cover being resolved all belong to it, so ending the session is what
    clears them.
    """
    station: Dict[str, Any] = field(default_factory=dict)
    # What mpv was handed: an edit of the station while it plays changes the
    # record on screen, not the stream that is sounding.
    stream_url: str = ""
    inband_track: Optional[Dict[str, Any]] = None
    # The in-band track whose cover lookup has not answered yet (by identity).
    artwork_lookup: Optional[Dict[str, Any]] = None
    inband_seen: bool = False           # the station emits in-band → no Shazam
    inband_poll_ticks: int = 0
    empty_inband_polls: int = 0         # in-band-empty polls since the sound started
    inband_empty_streak: int = 0        # consecutive empty polls once in-band was seen
    shazam_candidate: bool = False      # the station qualifies for the Shazam fallback
    recognition_enabled: bool = True    # the per-station now-playing gate
    # Radio France's own now-playing (radiofrance.py): the station's livemeta
    # id (None: not a Radio France stream, or the gate is off), the songs it
    # announced, and the one the listener hears now.
    radiofrance_station: Optional[int] = None
    radiofrance_songs: List[radiofrance.Song] = field(default_factory=list)
    radiofrance_track: Optional[Dict[str, Any]] = None
    radiofrance_named: bool = False     # it named a song → no Shazam
    radiofrance_answered: bool = False  # it answered or failed at least once
    radiofrance_failing_since: Optional[float] = None
    radiofrance_outage_reported: bool = False
    radiofrance_malformed_reported: bool = False


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
        "prev": None,
        "next": None,
    }
    COMMAND_SCOPES = {
        "play_station": CommandScope.CONTENT,
        "stop": CommandScope.RESUME,
        "resume_playback": CommandScope.RESUME,
        # Stopped, a step moves the station a play re-tunes, and plays nothing.
        "prev": CommandScope.RESUME,
        "next": CommandScope.RESUME,
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
            state_machine=state_machine,
            on_favorites_changed=self._favorites_changed,
            on_station_changed=self._station_changed,
        )
        # The station records outlive a session, like a drive: a STOP must not
        # void an update that the resume point it leaves behind still needs.
        self._records_token = DeviceToken()

        # RadioBrowser API (initialized immediately for API access)
        self._radio_api = RadioBrowserAPI(
            station_manager=self._station_data
        )
        self._station_data.radio_api = self._radio_api

        # External station logos, served by /api/radio/favicon whatever plays.
        self._logos = StationLogos()

        self._shazam: Optional[ShazamRecognitionService] = None
        # Shazam's request budget and backoff outlive every recognition service
        # (one per start): a station change or a source switch must not reset
        # them, or zapping alone would get the unit throttled.
        self._shazam_quota = RecognitionQuota()

        # Resolves cover art for in-band tracks (which carry no artwork) from
        # their artist/title via the iTunes Search API.
        self._artwork = ArtworkResolver(self._settings_service)

        self._preroll_cache: Dict[str, int] = {}  # hostname → preroll skip seconds (for Shazam)

        # Radio France's own now-playing, read for the Radio France stations.
        self._radiofrance = radiofrance.RadioFranceClient()

    async def initialize(self) -> bool:
        """Initialize station data (call at startup for API access)."""
        await self._station_data.initialize()
        await self._logos.initialize()
        self._logger.info("Radio station data initialized")
        return await super().initialize()

    @property
    def _displayed_station(self) -> Optional[Dict[str, Any]]:
        """The station this source is about: tuned, or the one a play re-tunes."""
        if isinstance(self._session, RadioSession):
            return self._session.station
        point = self._resume_point
        return point.content if point is not None else None

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
                quota=self._shazam_quota,
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
            self._publish()
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

        if cmd in ("prev", "next"):
            return await self._handle_step(1 if cmd == "next" else -1)

        return self.error_response(f"Unhandled command: {cmd}")

    # === Command Handlers ===

    async def _handle_play_station(self, params: PlayStationParams) -> Dict[str, Any]:
        """Play a radio station: the record the user keeps first (a favorite,
        or a station they edited, favorite or not), then what the caller sent,
        then the directory."""
        station_id = params.station_id

        try:
            station = self._station_data.get_local_station(station_id)
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
        session = RadioSession(phase=Phase.LOADING, station=station, stream_url=station.get('url') or "")
        session.recognition_enabled = self._station_data.is_station_shazam_enabled(
            station.get('id')
        )
        self.open_session(session)
        self._publish()

        if is_playlist_url(session.stream_url):
            # The station replaced must not sound while the playlist is read.
            await self._mpv.stop()
            stream = await resolve_stream_url(session.stream_url)
            if stream is None:
                await self._end_playback(EndReason.LOAD_FAILED, detail="playlist gave no stream")
                return self.error_response(f"Unable to load stream: {station_name}")
            session.stream_url = stream

        # Read now, alongside the load: an answer naming a song keeps the
        # Shazam fallback from starting at all.
        self._start_radiofrance(session)

        async def load():
            if await self._mpv_ready() and await self._set_mpv_pause(False):
                return await self._mpv.loadfile(session.stream_url, mode="replace")
            return None

        entry = await self._attempt(load)
        if entry is None:
            await self._end_playback(EndReason.LOAD_FAILED, detail="mpv refused the load")
            return self.error_response(f"Unable to load stream: {station_name}")
        session.entry = entry
        session.link = self._mpv.link

        # In-band metadata (polled while sound plays) is the primary title
        # source, then Radio France's feed. Shazam is a fallback started only
        # if both stay silent past the grace period, and needs the global
        # toggle too.
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
        self._publish()
        return self.success_response("Playback stopped")

    def _can_step(self, station: Optional[Dict[str, Any]]) -> bool:
        """`station` — the one on screen: playing, or the one a play re-tunes —
        is one of at least two favorites."""
        return (
            station is not None
            and self._station_data.favorite_count >= 2
            and self._station_data.is_favorite(station.get("id"))
        )

    async def _handle_step(self, step: int) -> Dict[str, Any]:
        """Tune the previous or next favorite, in the order the favorites are
        shown and round the list — or, stopped, make it the station a play
        re-tunes, without playing it. A favorite with no record to load (not
        cached, the directory unreachable) is passed over for the one after
        it, so one dead favorite does not wall the others off."""
        playing = self._session is not None
        if not playing and self.state_machine and self.state_machine.system_state.active_source != self.source:
            # The station kept for when radio is selected again is not this
            # command's to change.
            return self.error_response("Radio is not the active source")
        here_station = self._displayed_station
        if not self._can_step(here_station):
            return self.error_response("The station on screen is not one of several favorites")
        favorites = self._station_data.favorite_ids
        here = favorites.index(here_station.get("id"))
        try:
            for distance in range(1, len(favorites)):
                target = favorites[(here + step * distance) % len(favorites)]
                station = (
                    self._station_data.get_local_station(target)
                    or await self._station_data.get_station_metadata(target)
                )
                if not station:
                    continue
                if playing:
                    return await self._tune(station)
                # A station chosen stopped never plays on its own, not even
                # from a reroute's point, which a start would restore playing.
                self._set_resume_point(replace(
                    self._resume_point, identity=target, content=station, phase=Phase.PAUSED,
                ))
                self._publish()
                return self.success_response(f"Stopped on {station.get('name', target)}", station=station)
            return self.error_response("No other favorite could be loaded")
        except Exception as e:
            self._logger.error(f"Station step error: {e}")
            if playing:
                self.broadcast_error(SourceErrorReason.PLAYBACK_FAILED)
            return self.error_response(str(e))

    def _favorites_changed(self) -> None:
        """A favorite was added or removed: whether the station on screen,
        playing or stopped, can step moved. Called from the route's task, so it posts; the net after
        the message republishes what moved."""
        self._post_result(self._favorites_moved)

    async def _favorites_moved(self) -> None:
        """Nothing to apply: `_republish_if_moved` publishes the new controls."""

    def _station_changed(self, station: Dict[str, Any]) -> None:
        """A station's record moved — edited, restored, or an added station
        deleted, each of which can delete the image it showed. The station on screen,
        playing or kept for a re-tune, is held by value: it takes the new
        record, or the state goes on naming a file the image route answers 404
        for. The stream playing stays the one tuned (`stream_url`); a re-tune
        plays the new record. Called from the route's task, so it posts."""
        async def apply() -> None:
            session = self._session
            if isinstance(session, RadioSession) and session.station.get("id") == station["id"]:
                session.station = station
            point = self._resume_point
            if point is not None and point.identity == station["id"]:
                self._set_resume_point(replace(point, content=station))

        self._post_result(apply, self._records_token)

    async def _handle_resume_playback(self) -> Dict[str, Any]:
        """Re-tune the station on screen: the live one, or the one kept."""
        station = self._displayed_station
        if not station:
            return self.error_response("No station to resume")
        if not station.get('id'):
            return self.error_response("Last station has no id, cannot resume")
        return await self._tune(station)

    # === Helpers ===

    async def _detect_preroll(self, url: str) -> int:
        """Detect pre-roll ad duration from stream ICY metadata via ffprobe.

        Some streaming servers (e.g. Infomaniak) inject pre-roll ads on each
        new HTTP connection. A verdict is cached per hostname so only the first
        play of a given host pays the probe cost; a probe that got no answer
        caches nothing and is asked again next time.
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
            finally:
                # ffprobe is holding an open HTTP connection to the station.
                # Left running — timed out, or cancelled with the session — it
                # keeps pulling the stream, and the station counts a listener
                # that is not there.
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            if process.returncode == 0 and stdout:
                tags = json.loads(stdout).get("format", {}).get("tags", {})
                if tags.get("insertionType") == "preroll":
                    duration_ms = int(tags.get("durationMilliseconds", 0))
                    if duration_ms > 0:
                        skip = (duration_ms // 1000) + 2
                        self._logger.info(f"Pre-roll ad detected ({duration_ms}ms), skipping {skip}s")
                self._preroll_cache[hostname] = skip
        except Exception as e:
            self._logger.debug(f"Preroll detection failed for {hostname}: {e}")

        return skip

    def _resolve_track(self) -> Optional[Dict[str, Any]]:
        """Current now-playing track: in-band metadata first, then Radio
        France's feed, then Shazam."""
        session = self._session
        if not isinstance(session, RadioSession):
            return None
        if session.inband_track:
            return session.inband_track
        if session.radiofrance_track:
            return session.radiofrance_track
        return self._shazam.current_track if self._shazam else None

    @staticmethod
    def _track_key(track: Optional[Dict[str, Any]]):
        """Identity of a track for change detection (None when absent)."""
        if not track:
            return None
        return (track.get("title"), track.get("artist"))

    # === The view (docs: "le fil") ===

    def _session_fields(self, session: RadioSession) -> Dict[str, Any]:
        """Two layers in one line: the recognized track when there is one, the
        station otherwise (every generic consumer reads this line — the lock
        screen, the widget, the shared player)."""
        station = session.station
        track = self._resolve_track()
        return {
            "title": track["title"] if track else station.get("name"),
            "artist": track["artist"] if track else None,
            "album": station.get("name"),
            "artwork": (
                track["artwork"] if track and track.get("artwork")
                else _resolved_favicon(station.get("favicon"))
            ),
        }

    def _resume_view(self) -> Optional[ResumeView]:
        station = self._displayed_station
        if not station:
            return None
        return ResumeView(
            title=station.get("name"), artist=None, album=station.get("name"),
            artwork=_resolved_favicon(station.get("favicon")),
            duration_ms=None, position_ms=None,
        )

    def _details(self) -> Optional[RadioDetails]:
        """The station — tuned, or the one a play would re-tune — and the song
        recognized in its stream. The song annotates a stream that is
        running, so a stopped radio carries none."""
        station = self._displayed_station
        if not station:
            return None
        track = self._resolve_track()
        return RadioDetails(
            station=RadioStation(
                id=station.get("id"), name=station.get("name"), url=station.get("url"),
                country=station.get("country"), genre=station.get("genre"),
                favicon=station.get("favicon"), bitrate=station.get("bitrate"),
                codec=station.get("codec"),
            ),
            track=RadioTrack(
                title=track.get("title"), artist=track.get("artist"), artwork=track.get("artwork"),
            ) if track else None,
            artwork_pending=self._artwork_pending(),
        )

    def _artwork_pending(self) -> bool:
        """Whether the cover of the in-band song on screen is being looked up."""
        session = self._session
        if not isinstance(session, RadioSession):
            return False
        track = session.inband_track
        return track is not None and session.artwork_lookup is track and not track.get("artwork")

    def _controls(self) -> List[str]:
        """A live stream has no pause and no track to skip: stop, or re-tune
        what was stopped — and, from a favorite playing or stopped on, step to
        the one before or after it."""
        steps = ["prev", "next"] if self._can_step(self._displayed_station) else []
        if self._session is not None:
            return ["stop", *steps]
        station = self._displayed_station
        if station and station.get("id"):
            return ["resume_playback", *steps]
        return []

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
                stream_url = session.stream_url
                if stream_url and self._station_data.is_station_shazam_enabled(station_id):
                    if session.inband_seen or session.inband_track or session.radiofrance_named:
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
            self._publish()

    async def _cleanup(self) -> None:
        """Clean up resources."""
        if self._shazam:
            await self._shazam.close()
            self._shazam = None
        await self._radiofrance.close()
        await self._detach_mpv()

    # === While sound plays ===

    async def _on_playing_tick(self, session: RadioSession) -> None:
        await self._poll_inband_metadata(session)

    async def _poll_inband_metadata(self, session: RadioSession) -> None:
        """Read mpv in-band metadata; it is the primary now-playing source.

        In-band metadata (ICY StreamTitle / HLS tags) is instant and exact when
        present, so it overrides Shazam: the first in-band title shuts any
        running Shazam loop down. When in-band stays empty past a short grace
        period, Shazam starts as the fallback (metadata-less streams; on a
        Radio France station only once its feed has answered without naming
        a song). Polled every _INBAND_POLL_TICKS ticks of sound.

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
                # Set before the publish: it already says a cover is on its way.
                session.artwork_lookup = track
                self._publish()
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
                    self._publish()
            return

        # Never seen in-band for this station → count toward the Shazam grace,
        # then start the fallback once (candidacy is consumed to avoid re-arming).
        # A Radio France station also waits for its feed's first answer: a
        # song named there is the title, and Shazam would only contradict it.
        session.empty_inband_polls += 1
        if (
            session.shazam_candidate
            and self._shazam
            and not self._shazam.is_running
            and session.empty_inband_polls * _INBAND_POLL_TICKS >= _SHAZAM_GRACE_TICKS
            and self._radiofrance_silent(session)
        ):
            stream_url = session.stream_url
            if stream_url:
                session.shazam_candidate = False
                # The preroll probe (ffprobe) runs off the actor: up to ~10 s.
                self._session_bg.spawn(
                    self._start_shazam_fallback(session, stream_url),
                    label="shazam_fallback_start",
                )

    async def _resolve_inband_artwork(self, session: RadioSession, track: Dict[str, Any]) -> None:
        """Resolve cover art for an in-band track, applied if still current.

        A miss is posted too: it lifts the announcement, and the station logo
        is then the answer."""
        try:
            artwork = await self._artwork.resolve(
                track.get("artist", ""), track.get("title", "")
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Not re-raised: the announcement must still lift.
            self._logger.error(f"Cover lookup for {track.get('title')!r} failed: {e}")
            artwork = None

        async def apply() -> None:
            if session.artwork_lookup is track:
                session.artwork_lookup = None
            if session.inband_track is not track:
                # A newer song is on screen; its own lookup is still announced.
                return
            if artwork and session.phase is Phase.PLAYING:
                track["artwork"] = artwork
            self._publish()

        self._post_result(apply, token=session)

    async def _start_shazam_fallback(self, session: RadioSession, stream_url: str) -> None:
        """Detect preroll and start Shazam, unless in-band or Radio France
        named a song meanwhile."""
        if not self._shazam:
            return
        preroll = await self._detect_preroll(stream_url)

        async def apply() -> None:
            if (
                self._shazam and session.phase is Phase.PLAYING and not session.inband_seen
                and not session.radiofrance_named
            ):
                await self._shazam.start(stream_url, preroll_skip=preroll)

        self._post_result(apply, token=session)

    # === Radio France's now-playing (radiofrance.py) ===

    def _start_radiofrance(self, session: RadioSession) -> None:
        """Read the feed of a Radio France stream at once, unless the station
        is opted out of now-playing (the per-station gate suppresses it like
        in-band; the global toggle is Shazam's alone)."""
        station = radiofrance.station_for(session.stream_url)
        if station is None or not session.recognition_enabled:
            return
        session.radiofrance_station = station
        self._read_radiofrance(session)

    def _read_radiofrance(self, session: RadioSession) -> None:
        self._session_bg.spawn(self._fetch_radiofrance(session), label="radiofrance_read")

    def _radiofrance_silent(self, session: RadioSession) -> bool:
        """Whether the Shazam fallback may start as far as the feed goes: no
        feed, or one that answered (or failed) without naming a song."""
        if session.radiofrance_station is None:
            return True
        return session.radiofrance_answered and not session.radiofrance_named

    async def _fetch_radiofrance(self, session: RadioSession) -> None:
        """One read of the feed, off the actor; applied in it."""
        try:
            payload, error = await self._radiofrance.fetch(session.radiofrance_station), None
        except asyncio.CancelledError:
            raise
        except Exception as e:
            payload, error = None, e

        async def apply() -> None:
            now = audio_source.wall_time()
            session.radiofrance_answered = True
            if error is not None:
                self._radiofrance_failed(session, error, now)
                refresh = radiofrance.DEFAULT_REFRESH_S
            else:
                refresh = await self._radiofrance_answered(session, payload, now)
            self._arm_timer(_RF_POLL_TIMER, refresh, session)
            self._show_radiofrance(session)

        self._post_result(apply, token=session)

    async def _radiofrance_answered(self, session: RadioSession, payload: Any, now: float) -> float:
        """Merge an answer into what the session knows; returns when to read
        again. The first song named stops Shazam, as the first in-band title
        does."""
        log = radiofrance.logger
        if session.radiofrance_outage_reported:
            log.info("Radio France now-playing answers again")
        session.radiofrance_failing_since = None
        session.radiofrance_outage_reported = False

        answer = radiofrance.parse(payload)
        if answer.malformed and not session.radiofrance_malformed_reported:
            session.radiofrance_malformed_reported = True
            log.warning(
                f"Radio France now-playing for station {session.radiofrance_station} "
                f"answered {answer.malformed}: read as no song"
            )
        lead = radiofrance.lead_for(session.stream_url)
        session.radiofrance_songs = radiofrance.merge(
            session.radiofrance_songs, answer.songs, now, lead,
        )
        log.debug(
            f"Radio France station {session.radiofrance_station}: {len(answer.songs)} song(s) "
            f"announced, next read in {answer.refresh_s:.0f} s"
        )
        if not session.radiofrance_named and radiofrance.names_a_song(session.radiofrance_songs, now, lead):
            session.radiofrance_named = True
            session.shazam_candidate = False
            if self._shazam and self._shazam.is_running:
                await self._shazam.stop()
        return answer.refresh_s

    def _radiofrance_failed(self, session: RadioSession, error: Exception, now: float) -> None:
        """A read that failed changes nothing: what is known keeps its
        schedule. An outage is reported once, never at error (the banner)."""
        log = radiofrance.logger
        reason = describe_network_error(error)
        if session.radiofrance_failing_since is None:
            session.radiofrance_failing_since = now
        unanswered = now - session.radiofrance_failing_since
        if not session.radiofrance_outage_reported and unanswered >= _RF_OUTAGE_S:
            session.radiofrance_outage_reported = True
            log.warning(
                f"Radio France now-playing unreachable for {unanswered:.0f} s ({reason}), titles held"
            )
        else:
            log.debug(f"Radio France now-playing not answered: {reason}")

    def _show_radiofrance(self, session: RadioSession) -> None:
        """Show the song the listener hears now, and look again when that
        can change.

        Published whatever the phase, like the stream's own title: at the
        tune the first answer usually lands before mpv's first sound, and the
        song it names as heard is the one that sound starts in (the lead is
        measured from the connect), so the card names it while loading; in a
        stall the title stays, as an in-band one does."""
        now = audio_source.wall_time()
        song, look_at = radiofrance.schedule(
            session.radiofrance_songs, now, radiofrance.lead_for(session.stream_url),
        )
        if look_at is None:
            self._disarm_timer(_RF_SHOW_TIMER)
        else:
            self._arm_timer(_RF_SHOW_TIMER, max(0.0, look_at - now), session)
        track = song.track() if song else None
        if track == session.radiofrance_track:
            return
        had_track = session.radiofrance_track is not None
        session.radiofrance_track = track
        if track:
            named = " – ".join(part for part in (track["artist"], track["title"]) if part)
            radiofrance.logger.info(f"Radio France: {named}")
        elif had_track:
            radiofrance.logger.info("Radio France: no song")
        self._publish()

    async def _on_timer(self, name: str, token: object) -> None:
        session = self._session
        if name == _RF_POLL_TIMER:
            if isinstance(session, RadioSession) and session is token:
                self._read_radiofrance(session)
        elif name == _RF_SHOW_TIMER:
            if isinstance(session, RadioSession) and session is token:
                self._show_radiofrance(session)
        else:
            await super()._on_timer(name, token)

    # === Public API ===

    @property
    def station_data(self) -> Optional[StationDataService]:
        """Get station data service."""
        return self._station_data

    @property
    def radio_api(self) -> Optional[RadioBrowserAPI]:
        """Get RadioBrowser API client."""
        return self._radio_api

    async def shutdown(self) -> None:
        await super().shutdown()
        # The base ends the mailbox without running `_cleanup`, so a radio still
        # playing at teardown would leave the recognition loop and its HTTP
        # session open.
        if self._shazam:
            await self._shazam.close()
            self._shazam = None
        await self._radiofrance.close()
        await self._logos.cleanup()

    @property
    def logos(self) -> StationLogos:
        """The cache behind /api/radio/favicon."""
        return self._logos
