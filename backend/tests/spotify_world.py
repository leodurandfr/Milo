"""Spotify's outside world, as measured on the unit (docs: source architecture,
phase 3b): go-librespot 0.10.0 — its HTTP API and its /events WebSocket — and
systemd holding it; the stored account as go-librespot 0.10.3 keeps it.

What go-librespot was measured to say (2026-09-24, the owner's iPhone):

- A phone transferring playback: `active`, `will_play`, then `metadata` and
  `paused` together (the track loads paused, at the phone's position), and
  `playing` 1.6 s later. Between `will_play` and `metadata`, /status answers a
  session with no track, `paused` and `buffering`.
- Pause / resume: `paused` / `playing`. A skip: `will_play`, then `metadata` +
  `playing` 70 ms later (/status: buffering, no track, in between). A seek:
  one `seek` carrying the position. A skip while paused plays (the phone
  decides). A track that ends: `not_playing`, /status paused at 0, the next
  `will_play` 250-350 ms later — Spotify's autoplay never lets a context end.
- The phone picking another output, or POST /player/stop: `inactive` and
  `stopped`, 3 ms apart; /status then answers **204**: no session.
- A command sent to the API is answered by the event a phone's press sends
  (the reroute's pause produced `paused`, its resume `playing`).
- SIGKILL: /events closes at once, nothing else; systemd's Restart= brings a
  new process 5 s later, with no session.

With stored credentials (0.10.3, `persist_credentials`; measured 2026-10-03):

- At a start the daemon signs in on its own and says nothing on /events;
  /status answers 200 naming the account, with no track.
- After a session end (`inactive`, `stopped`) /status answers 204 for about
  0.25 s, then `playback_ready` and the same 200.
- POST /player/play on a signed-in daemon opens a session: `will_play`, then
  `metadata` and `playing` (no `active`). Shuffle and repeat are answered on a
  signed-in daemon and announced by an event carrying `value`.
- With nobody signed in, every /player command answers 204.

Another device of the account, as go-librespot master 9085364 (#428) is
written — read in its code, NOT measured on the unit yet: GET /connect/devices
lists the Connect cluster (`active_device_id`, the `devices` sorted by id with
the one `is_self`, and `now_playing`, the cluster's playback whoever is
active: its position extrapolated to the answer while it plays and capped at
its duration, `artist_name` "", no cover). It answers 404 until the session's
first cluster — which comes in the answer to the daemon's first push, with no
event — and 204 with no session. Every ClusterUpdate raises `cluster`
carrying the same answer, and nothing else does: a transfer here leaves the
previous cluster listed until the answer to its push lands (taken here at
`will_play`). A ClusterUpdate naming another device active over a session
here is said in its own frame; the daemon then stops the player, pushes, asks
to sign out and only then says `inactive` (and the fork's `queue` after the
`cluster`, never ahead of it). POST /connect/transfer {device_id} answers 400
with no id before anything else, then 204 with no session, 404 for a device
the cluster lacks, 502 when Spotify refuses, 200 — and for the device already
active, the whole transfer again (`active`, `will_play`, `metadata`) — and asks
Spotify to "restore": the session lands paused if it was paused there. Until the track starts loading
(`will_play`) a resume is answered 200 and lost; during the load it is applied
as the track lands; after it, the track plays from where it landed. From the
2026-10-05 measurements (the owner's iPhone, the fork's route): the session
arrives ~0.25 s after the transfer, at the same position. Spotify's metadata
service (metadata/4/track/{gid}) answers in the shape of
spotify_track_metadata.json, measured 2026-11-09.

The play order (the fork's GET /player/queue, measured 2026-10-07 on the
owner's iPhone): `provider` is "context", "queue" (the user's queue, listed
first in next_tracks and kept across a new context) or "autoplay" (the station
that replaces a context once it has run out, never listed after it); a
playlist's entries carry a uid, an album's none, the user's queue numbers its
own ("q1", "q2", the same track queued twice included). The current entry
carries the playing position; while a track loads it is the one loading, once
its metadata is cached, described at its load's start and standing still there
— 0 for a skip, the transferred position for a transfer (go-librespot #421,
read in its player_queue.go, not measured): /status names no track until it
has streamed.

A track Spotify relinks to a market alternative (measured 2026-11-09, a Liked
Songs entry): the entry keeps the uri its context lists, at the top level of
GET /player/queue's current entry, while the track's own uri — nested in it,
and the one /status names — is the alternative's once the stream has loaded.
Before the first stream go-librespot describes the entry by the listed uri; it
then keeps the alternative's metadata under both, so a later load describes it
by the alternative's from the start.

Time is a VirtualClock the scenario advances; the daemon's death is heard
through the same pidfd watch the source opens (patched here, as for AirPlay).

The journal (`journalctl -o json`, read 2026-11-04 on the unit): every line
go-librespot writes carries its run's `_SYSTEMD_INVOCATION_ID` (32 hex
digits), and journald may hand a line on seconds after a newer run started
(14 s of lag measured on this unit). systemd's InvocationID is the running
run's, and empty once the unit is inactive (measured). After a crash, on the
way to Restart=, the world keeps the dead run's — an assumption, not a
measurement.
"""
import asyncio
import copy
import itertools
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple

import aiohttp
import yaml
import yarl
from multidict import CIMultiDict, CIMultiDictProxy
from unittest.mock import AsyncMock, Mock

from backend.core import audio_source
from backend.core.models.audio_state import AudioSource
from backend.sources.spotify import jam as jam_module
from backend.sources.spotify import library as library_module
from backend.sources.spotify import source as spotify_module
from backend.sources.spotify import websocket as websocket_module
from backend.sources.spotify.catalog import spotify_gid
from backend.sources.spotify.source import SpotifySource
from backend.tests.golden.harness import (
    AsyncioProxy, VirtualClock, WireReader, make_settings, make_state_machine, settle, use_virtual_wall,
)

ACCOUNT = "p6puyc6we1egphk4nt2l5vaxy"
# go-librespot's device id on the unit, as /status names it.
DEVICE_ID = "84929e0ca31026278abdcf27127aa71d1d5c166b"
# The account's other devices, by the name their app gives them.
OTHER_DEVICES = {
    "iPhone": "1d0183bba7f631d45ffa2be4e1208f37682b5126",
    "Mac mini": "c2f1a4a5ea4a3d0b8b6d4d1fd2a81e37ab2c9f10",
}
# What metadata/4/track answers (measured, see the module docstring).
TRACK_METADATA = json.loads((Path(__file__).parent / "spotify_track_metadata.json").read_text())


def jam_session(*members: Tuple[str, str, int], active: Optional[bool] = True,
                host: str = DEVICE_ID) -> Dict[str, Any]:
    """A Jam as social-connect answers it (measured 2026-10-11): `members` as
    (id, display_name, joined_timestamp), the first one the owner, in the
    order the asker is given them; `active` None: the dormant session Spotify
    keeps after a Jam ends."""
    session: Dict[str, Any] = {
        "session_id": "56cc449ae52a4d4a1940bfc2211b21a4",
        "session_owner_id": members[0][0] if members else "",
        "session_members": [
            {"id": member_id, "username": f"user-{member_id}", "display_name": name,
             "joined_timestamp": str(joined), "is_listening": True}
            for member_id, name, joined in members
        ],
        "hostActiveDeviceId": host,
    }
    if active is not None:
        session["active"] = active
    return session
FIRST_PID = 46508


def track(name: str, duration: int = 182920, artists=("Kery James",), album: str = "Réel") -> Dict[str, Any]:
    slug = name.lower().replace(" ", "-")
    return {
        # A Spotify id is base62: Milō reads it as the hex gid of its metadata.
        "uri": f"spotify:track:{slug.replace('-', '')}",
        "name": name,
        "artist_names": list(artists),
        "album_name": album,
        "album_cover_url": f"https://i.scdn.co/image/{slug}",
        "duration": duration,
        "position": 0,
    }


PARAPLUIE = track("Parapluie")
TROIS_NEUF_TROIS = track("Trois Neuf Trois", 199533)
LE_CHEMIN = track("Le Chemin", 222022)


def gid_of(song: Dict[str, Any]) -> str:
    return spotify_gid(song["uri"].rsplit(":", 1)[1])


def cover_named_for(song: Dict[str, Any]) -> str:
    """The 640 px cover Spotify's metadata service names for `song`."""
    return f"https://i.scdn.co/image/ab67616d0000b273{gid_of(song)[:24]}"


def track_metadata_answer(song: Dict[str, Any]) -> Dict[str, Any]:
    """What metadata/4/track answers for `song`: the fixture's answer, its
    names and covers made the song's."""
    answer = copy.deepcopy(TRACK_METADATA)
    gid = gid_of(song)
    answer["gid"], answer["name"], answer["duration"] = gid, song["name"], song["duration"]
    answer["artist"] = [{"gid": f"{i:032x}", "name": name} for i, name in enumerate(song["artist_names"])]
    answer["album"]["name"] = song["album_name"]
    for image in answer["album"]["cover_group"]["image"]:
        image["file_id"] = image["file_id"][:16] + gid[:24]
    return answer


_CLOSED = object()


class _Response:
    def __init__(self, status: int, payload: Any = None) -> None:
        self.status = status
        self._payload = payload
        self.content_type = "application/json" if payload is not None else ""

    async def json(self, **_: Any) -> Any:
        if self.status == 204:
            raise aiohttp.ContentTypeError(Mock(), (), message="204 has no body")
        return copy.deepcopy(self._payload)


class _Protobuf(_Response):
    """Spotify's metadata service, asked without `Accept: application/json`:
    its protobuf answer, which no JSON decoder reads."""

    def __init__(self) -> None:
        super().__init__(200, b"\x0a\x10")
        self.content_type = "application/protobuf"

    async def json(self, **_: Any) -> Any:
        return json.loads(b"\x0a\x10")


class _Exchange:
    def __init__(self, response: Any = None, error: Optional[BaseException] = None,
                 delay: float = 0.0, clock: Optional["VirtualClock"] = None) -> None:
        self._response, self._error = response, error
        self._delay, self._clock = delay, clock

    async def __aenter__(self) -> Any:
        if self._delay:
            await self._clock.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return self._response

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _EventsSocket:
    """One /events connection (or one dealer socket): what is pushed, in
    order, until it closes; and what the client sent on it."""

    def __init__(self, answers_pings: bool = False) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()
        self.sent: List[str] = []
        # The dealer answers each ping with a pong (measured; go-librespot's
        # client relies on it).
        self.answers_pings = answers_pings

    async def send_str(self, data: str) -> None:
        self.sent.append(data)
        if self.answers_pings and json.loads(data).get("type") == "ping":
            self.queue.put_nowait({"type": "pong"})

    async def close(self) -> None:
        self.queue.put_nowait(_CLOSED)

    def __aiter__(self) -> "_EventsSocket":
        return self

    async def __anext__(self) -> Any:
        item = await self.queue.get()
        if item is _CLOSED:
            raise StopAsyncIteration
        return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(item))

    def exception(self) -> None:
        return None


def _refused() -> aiohttp.ClientOSError:
    return aiohttp.ClientOSError(111, "Connect call failed ('127.0.0.1', 3678)")


class Librespot:
    """go-librespot as the source sees it over HTTP and /events."""

    def __init__(self, clock: VirtualClock) -> None:
        self.clock = clock
        self.up = False
        self.session = False            # a session is live (else /status is 204, or the signed-in 200)
        self.account: Optional[str] = None
        self.stored: Optional[str] = None   # the account whose credentials state.json holds
        self.crossfade: Optional[int] = None   # config.yml's crossfade_duration, read at start
        self.signed_in = False          # signed in with no session (stored credentials)
        self.signin_hangs = False       # a sign-in that never completes (seen once, unreproduced)
        self.context: Optional[Tuple[str, str]] = None   # (uri, name) of what plays
        self.shuffle = False
        self.repeat_context = False
        self.repeat_track = False
        self.track: Optional[Dict[str, Any]] = None
        # The track a load points at while /status names none, its `position`
        # the load's start (0 for a skip, the transferred position for a
        # transfer): the fork's GET /player/queue lists it as the current
        # entry, described there, standing still, once its metadata is
        # cached; `loading_known` False: not cached, a null track.
        self.loading: Optional[Dict[str, Any]] = None
        self.loading_known = True
        # The uri the context lists for the current entry, when Spotify
        # relinked it to the alternative `track` / `loading` names (cleared by
        # every play the world starts itself).
        self.listed: Optional[str] = None
        # The play order GET /player/queue lists around the track, as
        # {uri, uid, provider, track} entries (track None until its metadata
        # is cached). Any change of that answer raises `queue` ahead of the
        # events it comes with, as go-librespot compares the answer itself.
        self.prev_tracks: List[Dict[str, Any]] = []
        self.next_tracks: List[Dict[str, Any]] = []
        self.has_queue_route = True     # False: a go-librespot without it (stock 0.10.3), 404
        self.cluster_names_track = False   # True: the cluster names the title and album elsewhere
        self.queue_answers = True       # False: GET /player/queue answers 503
        self.queue_reads = 0
        self.queue_body: Any = None     # not None: GET /player/queue answers 200 with this instead
        # True: a change of the order raises no `queue` of its own (the test
        # says it later), as when the event lands in a burst after the track's.
        self.queue_event_late = False
        self.queue_listed: Optional[Dict[str, Any]] = None
        # The account's Connect cluster: the other devices ({id: {name, type}}),
        # the active one when it is not this one ("" with nobody active), and
        # the last playback reported from elsewhere ({song, paused, position_ms,
        # at}), which the cluster keeps after its device has left.
        self.others: Dict[str, Dict[str, str]] = {}
        self.active_device_id = ""
        self.playback_elsewhere: Optional[Dict[str, Any]] = None
        self.cluster_ready = True       # False: no cluster yet this session, a 404
        self.devices_answers = True     # False: GET /connect/devices answers 503
        self.devices_reads = 0
        self.status_reads = 0
        # A transfer here: the playback it brings, its stage ("resolving" the
        # context until `will_play`, then "loading"), a resume asked during
        # the load, and the resumes lost before it.
        self.taken_over: Optional[Dict[str, Any]] = None
        self.transfer_pending = False
        self.stage: Optional[str] = None
        self.resume_at_load = False
        self.resumes_lost = 0
        self.transfer_refused = False   # Spotify refused the transfer (a 502 from go-librespot)
        self.transfer_never_lands = False   # Spotify accepted it (200) and never delivered it
        self.self_transfers = 0         # transfers asked to the device already active
        self.transfer_start_delay = 0.0  # how long after its 200 a transfer here starts
        # A ClusterUpdate named another device active, and the session here is
        # not stopped yet: the cluster is that device's.
        self.taken_back = False
        # Spotify's track metadata (spclient metadata/4/track), per hex gid:
        # filled for every song played elsewhere but the uris in `unnamed`
        # (an unknown gid answers 503); how long it takes to answer, and the
        # gids asked.
        self.track_metadata: Dict[str, Dict[str, Any]] = {}
        self.unnamed: set = set()
        self.refused: set = set()          # uris the service answers 404 for

        self.naming_delay = 0.0
        self.track_metadata_asked: List[str] = []
        self.paused = True
        self.buffering = False
        # The app's slider, in steps (0.10.5: kept across sessions, set with no
        # session too). Each change, the phone's or a POST's, re-arms one
        # 100 ms timer whose event carries the position at its expiry; with
        # no player (neither a session nor signed in) a POST raises none.
        self.volume = 42
        self.volume_steps = 100
        self.volume_without_session = True   # False: 0.10.4 and before, a 204
        self.status_answers = True      # False: /status answers 503 (learned nothing)
        self.refused_outputs: set = set()   # devices POST /player/output answers 500 for
        self.output = "milo_spotify"    # the ALSA device it writes to
        # A resume reaches /status only after its answer (the lag the reroute's
        # pause is confirmed against): /status still says paused meanwhile.
        self.resume_lag = False
        self.posted: List[Tuple[str, Dict[str, Any]]] = []
        self._volume_report: Optional[asyncio.Task] = None
        self.socket: Optional[_EventsSocket] = None
        # The library, as the signed-in account sees it.
        self.playlists: List[Dict[str, Any]] = []
        self.listings: Dict[str, List[Dict[str, Any]]] = {}   # uri -> tracks
        self.liked: set = set()                                # Liked Songs' track uris
        # Every write to the library (0.10.5's routes), as (path, body).
        self.library_writes: List[Tuple[str, Dict[str, Any]]] = []
        self.contains_refused: set = set()                     # playlists /contains answers 503 for
        self.liked_refused = False                             # GET /library/liked answers 503
        self.listing_calls_until_ready = 1   # /context/tracks answers ready on this call
        # go-librespot describes a listing front to back while it is read
        # (batches of 100 a second): None describes it whole at once, n
        # describes n more tracks on each call after it is ready.
        self.described_per_call: Optional[int] = None
        self.listing_calls: Dict[str, int] = {}
        # Spotify's profile service (spclient user-profile-view): the answer
        # per account, as measured ({name, image_url, ...}); None
        # answers 503.
        self.profile_answers: Dict[str, Optional[Dict[str, Any]]] = {}
        # Spotify's home (spclient homeview) for the signed-in account; None
        # answers 503.
        self.home_answer: Optional[Dict[str, Any]] = {"body": []}
        self.home_locales: List[str] = []
        # An artist's page and its discography (spclient artistview); None
        # answers 503. The urls asked are kept, with their locale.
        self.artist_answer: Optional[Dict[str, Any]] = {"body": []}
        self.releases_answer: Optional[Dict[str, Any]] = {"body": []}
        self.artist_asked: List[Tuple[str, str]] = []
        # Spotify's track radio (spclient inspiredby-mix), as measured: one
        # playlist; None answers 503. The urls asked are kept.
        self.radio_answer: Optional[Dict[str, Any]] = {
            "total": 1, "mediaItems": [{"uri": "spotify:playlist:37i9dQZF1E8UJ1xRHXd2z2"}],
        }
        self.radio_asked: List[str] = []
        # Spotify's artist metadata (spclient metadata/4), as measured: per
        # hex gid, {name, portrait_group}; an unknown gid answers 503.
        self.artist_metadata: Dict[str, Dict[str, Any]] = {}
        # go-librespot's own device id, as /status names it.
        self.device_id = DEVICE_ID
        # Spotify's social-connect (the account's Jam), on the internet: what
        # sessions/current answers (None: 404, no Jam), how often it was read,
        # and the dealer sockets opened.
        self.jam: Optional[Dict[str, Any]] = None
        self.jam_reads = 0
        # sessions/current answers this status instead (a 429), when set.
        self.jam_read_refused: Optional[int] = None
        self.dealer: Optional[_EventsSocket] = None
        self.dealer_urls: List[str] = []
        self.dealer_up = True
        # The dealer refuses the upgrade with this status (an expired token:
        # 401), the way aiohttp raises it, its text naming the URL.
        self.dealer_refuses: Optional[int] = None
        self.last_refusal: Optional[BaseException] = None
        # The dealer closes every socket as soon as it is open.
        self.dealer_closes_at_once = False
        # The dealer answers pings (False: a half-open socket, nothing comes back).
        self.dealer_answers_pings = True
        self.token_reads = 0
        self.closed = False
        # Catalog search (a go-librespot built with it, search.enabled): one
        # /search/{type} page per kind, as the daemon answers it, and the
        # suggestions. `search_answers` set to a status answers every search
        # with it: 404 for a daemon without search, 429 when Spotify
        # rate-limits it. Every search asked is kept, (path, params).
        self.search_pages: Dict[str, Dict[str, Any]] = {}
        self.suggestions: Dict[str, Any] = {"query": "", "completions": [], "items": []}
        self.search_answers: Optional[int] = None
        # A kind refused alone, with its status: Spotify failing that one
        # operation this time (go-librespot answers 502).
        self.search_refused: Dict[str, int] = {}
        # Kinds whose request never comes back in time (a slow network).
        self.search_times_out: set = set()
        self.searches: List[Tuple[str, Dict[str, Any]]] = []

    # -- aiohttp.ClientSession surface --------------------------------------

    def __call__(self, *a: Any, **k: Any) -> "Librespot":
        return self

    def get(self, url: str, *a: Any, **k: Any) -> _Exchange:
        if "apresolve" in url:
            return _Exchange(_Response(200, {"dealer": ["dealer.test:443"]}))
        if "social-connect/v2/sessions/current" in url:
            self.jam_reads += 1
            if self.jam_read_refused is not None:
                return _Exchange(_Response(self.jam_read_refused))
            return _Exchange(_Response(404) if self.jam is None else _Response(200, copy.deepcopy(self.jam)))
        if "homeview" in url:
            self.home_locales.append(k["params"]["locale"])
            answer = self.home_answer
            return _Exchange(_Response(503) if answer is None else _Response(200, answer))
        if "artistview" in url:
            self.artist_asked.append((url, k["params"]["locale"]))
            answer = self.releases_answer if url.endswith("/releases") else self.artist_answer
            return _Exchange(_Response(503) if answer is None else _Response(200, answer))
        if "/metadata/4/" in url and (k.get("headers") or {}).get("Accept") != "application/json":
            return _Exchange(_Protobuf())
        if "/metadata/4/artist/" in url:
            answer = self.artist_metadata.get(url.rsplit("/", 1)[1])
            return _Exchange(_Response(503) if answer is None else _Response(200, answer))
        if "/metadata/4/track/" in url:
            gid = url.rsplit("/", 1)[1]
            self.track_metadata_asked.append(gid)
            if any(gid_of({"uri": uri}) == gid for uri in self.refused):
                return _Exchange(_Response(404))
            answer = self.track_metadata.get(gid)
            return _Exchange(_Response(503) if answer is None else _Response(200, answer),
                             delay=self.naming_delay, clock=self.clock)
        if "inspiredby-mix" in url:
            self.radio_asked.append(url)
            answer = self.radio_answer
            return _Exchange(_Response(503) if answer is None else _Response(200, answer))
        if "user-profile-view" in url:
            answer = self.profile_answers.get(url.rsplit("/", 1)[1])
            return _Exchange(_Response(503) if answer is None else _Response(200, answer))
        if not self.up:
            return _Exchange(error=_refused())
        if url.endswith("/status"):
            self.status_reads += 1
            if not self.status_answers:
                return _Exchange(_Response(503))
            if not self.session and not self.signed_in:
                return _Exchange(_Response(204))
            if not self.session:
                return _Exchange(_Response(200, {
                    "username": self.stored, "stopped": False, "paused": False,
                    "buffering": False, "track": None, **self._player_flags(),
                    "device_id": self.device_id,
                    "volume": self.volume, "volume_steps": self.volume_steps,
                }))
            context_uri, context_name = self.context or (None, None)
            return _Exchange(_Response(200, {
                "username": self.account, "stopped": False, "paused": self.paused,
                "buffering": self.buffering, "track": copy.deepcopy(self.track),
                "context_uri": context_uri, "context_name": context_name,
                **self._player_flags(), "device_id": self.device_id,
                "volume": self.volume, "volume_steps": self.volume_steps,
            }))
        if url.endswith("/connect/devices"):
            self.devices_reads += 1
            if not self.devices_answers:
                return _Exchange(_Response(503))
            if not self.session and not self.signed_in:
                return _Exchange(_Response(204))
            if not self.cluster_ready:
                return _Exchange(_Response(404))
            return _Exchange(_Response(200, self.cluster()))
        if url.endswith("/player/queue"):
            self.queue_reads += 1
            if not self.has_queue_route:
                return _Exchange(_Response(404))
            if not self.queue_answers:
                return _Exchange(_Response(503))
            if self.queue_body is not None:
                return _Exchange(_Response(200, self.queue_body))
            if not self.session and not self.signed_in:
                return _Exchange(_Response(204))
            return _Exchange(_Response(200, self._queue()))
        return _Exchange(_Response(200, {"playback_ready": self.session}))

    def _queue(self) -> Dict[str, Any]:
        """What GET /player/queue answers now."""
        current = None
        if self.session and self.track:
            current = {"uri": self.listed or self.track["uri"], "uid": None, "provider": "context",
                       "track": copy.deepcopy(self.track)}
        elif self.session and self.loading:
            described = copy.deepcopy(self.loading) if self.loading_known else None
            current = {"uri": self.listed or self.loading["uri"], "uid": None, "provider": "context",
                       "track": described}
        return {"prev_tracks": copy.deepcopy(self.prev_tracks), "track": current,
                "next_tracks": copy.deepcopy(self.next_tracks)}

    def post(self, url: str, json: Optional[Dict[str, Any]] = None, **k: Any) -> _Exchange:
        if not self.up:
            return _Exchange(error=_refused())
        body = json or {}
        if "/connect/" in url:
            command = "connect/" + url.rsplit("/connect/", 1)[1]
            self.posted.append((command, body))
            return _Exchange(self._connect(command, body))
        command = url.rsplit("/player/", 1)[1]
        self.posted.append((command, body))
        if command == "resume" and self.stage == "resolving":
            # No stream loaded yet: go-librespot's play fails, and the route
            # answers 200 all the same.
            self.resumes_lost += 1
            return _Exchange(_Response(200))
        if command == "resume" and self.stage == "loading":
            self.resume_at_load = True
            return _Exchange(_Response(200))
        if command == "output":
            if body["device"] in self.refused_outputs:
                return _Exchange(_Response(500))
            self.output = body["device"]
            return _Exchange(_Response(200))
        if command == "volume":
            if not self.session and not self.volume_without_session:
                return _Exchange(_Response(204))
            self.volume = max(0, min(self.volume_steps, body["volume"]))
            if self.session or self.signed_in:
                self.volume_changed()
            return _Exchange(_Response(200))
        if not self.session and not self.signed_in:
            return _Exchange(_Response(204))
        if command in ("shuffle_context", "repeat_context", "repeat_track"):
            setattr(self, {"shuffle_context": "shuffle"}.get(command, command), body[command])
            self._later({"type": command, "value": body[command]})
            return _Exchange(_Response(200))
        if command == "play":
            self._plays(body)
            return _Exchange(_Response(200))
        if not self.session:
            return _Exchange(_Response(204))
        if command == "pause" or (command == "playpause" and not self.paused):
            self.paused = True
            self._later({"type": "paused"})
        elif command in ("resume", "playpause"):
            if self.resume_lag:
                asyncio.get_running_loop().call_soon(self._resumed)
            else:
                self.paused = False
                self._later(self.playing(resume=True))
        elif command == "seek" and self.track:
            self.track["position"] = body["position"]
            self._later({"type": "seek", "position": body["position"]})
        elif command == "stop":
            self._ends()
        return _Exchange(_Response(200))

    def _connect(self, command: str, body: Dict[str, Any]) -> _Response:
        """POST /connect/transfer, as master's handler and handleConnectControl answer it."""
        if command != "connect/transfer":
            return _Response(404)
        target = body.get("device_id") or ""
        if not target:
            return _Response(400)
        if not self.session and not self.signed_in:
            return _Response(204)
        if not self.cluster_ready or (target != DEVICE_ID and target not in self.others):
            return _Response(404)
        if self.transfer_refused:
            return _Response(502)
        if target != DEVICE_ID or self.transfer_never_lands:
            return _Response(200)
        if self.session:
            # Spotify sends this device's own session back to it: it loads again.
            self.self_transfers += 1
            self._later({"type": "active"})
            if self.track:
                self._later({"type": "will_play", "uri": self.track["uri"]})
                self._later(self.metadata())
            return _Response(200)
        self.transfer_pending = True
        if self.transfer_start_delay:
            asyncio.get_running_loop().create_task(self._transfer_starts_later())
        else:
            asyncio.get_running_loop().call_soon(self._transfer_starts)
        return _Response(200)

    async def _transfer_starts_later(self) -> None:
        await self.clock.sleep(self.transfer_start_delay)
        self._transfer_starts()

    def request(self, method: str, url: str, params: Optional[Dict[str, Any]] = None,
                json: Optional[Dict[str, Any]] = None, **k: Any) -> _Exchange:
        """The library API (library.py), on the same daemon."""
        if not self.up:
            return _Exchange(error=_refused())
        if not self.session and not self.signed_in:
            return _Exchange(_Response(204))
        path = url.split(":3678", 1)[1]
        params = params or {}
        if path == "/library/playlists":
            offset, limit = int(params.get("offset", 0)), int(params.get("limit", 50))
            return _Exchange(_Response(200, {
                "items": self.playlists[offset:offset + limit], "total": len(self.playlists),
                "offset": offset, "limit": limit,
            }))
        if path == "/context/tracks":
            uri = params["uri"]
            if uri not in self.listings:
                return _Exchange(_Response(400))
            calls = self.listing_calls[uri] = self.listing_calls.get(uri, 0) + 1
            tracks = self.listings[uri]
            ready = calls >= self.listing_calls_until_ready
            if ready and self.described_per_call is not None:
                shown = (calls - self.listing_calls_until_ready + 1) * self.described_per_call
                tracks = [t if i < shown else {"uri": t["uri"], "track": None} for i, t in enumerate(tracks)]
            return _Exchange(_Response(200, {
                "uri": uri, "ready": ready, "length": len(tracks),
                "cached": sum(1 for t in tracks if t.get("track")) if ready else 0,
                "tracks": tracks if ready else [],
            }))
        if path == "/token":
            self.token_reads += 1
            return _Exchange(_Response(200, {"token": "access-token"}))
        if path.startswith("/library/"):
            return _Exchange(self._library(method, path, params, json or {}))
        if path.startswith("/search/"):
            self.searches.append((path, dict(params)))
            if self.search_answers is not None:
                return _Exchange(_Response(self.search_answers))
            if path == "/search/suggestions":
                return _Exchange(_Response(200, self.suggestions))
            if path[len("/search/"):] in self.search_times_out:
                return _Exchange(error=asyncio.TimeoutError())
            if path[len("/search/"):] in self.search_refused:
                return _Exchange(_Response(self.search_refused[path[len("/search/"):]]))
            empty = {"total": 0, "offset": 0, "limit": params.get("limit", 20), "next_offset": None, "items": []}
            return _Exchange(_Response(200, self.search_pages.get(path[len("/search/"):], empty)))
        return _Exchange(_Response(404))

    def _library(self, method: str, path: str, params: Dict[str, Any], body: Dict[str, Any]) -> _Response:
        """go-librespot 0.10.5's library writes and the reads that go with
        them: 403 for a playlist the account may not edit, 404 for one that
        does not exist (or that no longer holds the item to remove), 409 for
        an entry no longer at the position named."""
        if path == "/library/liked":
            if method == "GET" and self.liked_refused:
                return _Response(503)
            if method == "GET":
                return _Response(200, {"items": [
                    {"uri": u, "liked": u in self.liked} for u in params["uris"].split(",")
                ]})
            self.library_writes.append((path, body))
            for uri in body["uris"]:
                (self.liked.add if body["liked"] else self.liked.discard)(uri)
            return _Response(200)
        playlist_uri = params.get("playlist_uri") or body.get("playlist_uri")
        playlist = next((p for p in self.playlists if p["uri"] == playlist_uri), None)
        if playlist is None:
            return _Response(404)
        entries = self.listings.setdefault(playlist_uri, [])
        if path == "/library/playlists/contains":
            if playlist_uri in self.contains_refused:
                return _Response(503)
            held = {e["uri"] for e in entries}
            return _Response(200, {"items": [
                {"uri": u, "contained": u in held} for u in params["uris"].split(",")
            ]})
        if not playlist.get("can_edit"):
            return _Response(403)
        self.library_writes.append((path, body))
        if path == "/library/playlists/add_tracks":
            entries.extend({"uri": u, "track": {"uri": u, "name": u}} for u in body["uris"])
            return _Response(200)
        if path == "/library/playlists/remove_track":
            position = body.get("position")
            if position is None:
                position = next((i for i, e in enumerate(entries) if e["uri"] == body["uri"]), None)
                if position is None:
                    return _Response(404)
            elif position >= len(entries) or entries[position]["uri"] != body["uri"]:
                return _Response(409)
            del entries[position]
            return _Response(200)
        return _Response(404)

    def ws_connect(self, url: str, *a: Any, **k: Any) -> _Exchange:
        if url.startswith("wss://"):
            if not self.dealer_up:
                return _Exchange(error=aiohttp.ClientConnectionError("dealer unreachable"))
            if self.dealer_refuses is not None:
                info = aiohttp.RequestInfo(yarl.URL(url), "GET", CIMultiDictProxy(CIMultiDict()), yarl.URL(url))
                self.last_refusal = aiohttp.WSServerHandshakeError(
                    info, (), status=self.dealer_refuses, message="Invalid response status",
                )
                return _Exchange(error=self.last_refusal)
            self.dealer_urls.append(url)
            self.dealer = _EventsSocket(answers_pings=self.dealer_answers_pings)
            if self.dealer_closes_at_once:
                self.dealer.queue.put_nowait(_CLOSED)
            return _Exchange(self.dealer)
        if not self.up:
            return _Exchange(error=_refused())
        self.socket = _EventsSocket()
        return _Exchange(self.socket)

    async def close(self) -> None:
        return None

    def _player_flags(self) -> Dict[str, Any]:
        return {
            "shuffle_context": self.shuffle,
            "repeat_context": self.repeat_context,
            "repeat_track": self.repeat_track,
        }

    def _plays(self, body: Dict[str, Any]) -> None:
        """POST /player/play: a session opens on the signed-in account, at
        `skip_to_uri` or the context's first track."""
        uri = body.get("skip_to_uri") or f"{body['uri']}:first"
        song = track(uri.rsplit(":", 1)[-1].replace("-", " ").title())
        song["uri"] = uri
        if not self.session:
            # A play with no session starts from a fresh state (measured).
            self.shuffle = False
        self.session, self.account, self.listed = True, self.stored, None
        self.context = (body["uri"], "Chill appart")
        self.track, self.paused, self.buffering = song, False, False
        self._later({"type": "will_play", "uri": uri})
        self._later(self.metadata())
        self._later(self.playing())

    # -- the daemon's side ----------------------------------------------------

    def metadata(self) -> Dict[str, Any]:
        """`metadata`: once per track the daemon loads, carrying it."""
        return {"type": "metadata", "data": copy.deepcopy(self.track)}

    def playing(self, resume: bool = False) -> Dict[str, Any]:
        """`playing`: a resume, or the start of the track loaded (a resume too
        when it loaded paused, as a transfer does)."""
        context_uri, _ = self.context or ("", None)
        return {"type": "playing", "data": {
            "context_uri": context_uri, "uri": self.listed or self.track["uri"], "resume": resume,
            "play_origin": "go-librespot",
        }}

    def volume_event(self) -> Dict[str, Any]:
        return {"type": "volume", "data": {"value": self.volume, "max": self.volume_steps}}

    def volume_changed(self) -> None:
        if self._volume_report is not None:
            self._volume_report.cancel()
        self._volume_report = asyncio.get_running_loop().create_task(self._report_volume())

    async def _report_volume(self) -> None:
        await self.clock.sleep(0.1)
        self.says(self.volume_event())

    def says(self, *events: Dict[str, Any]) -> None:
        if self.socket is not None:
            queue = self._queue()
            if self.has_queue_route and not self.queue_event_late and queue != self.queue_listed:
                self.queue_listed = queue
                events = ({"type": "queue"}, *events)
            for event in events:
                self.socket.queue.put_nowait(event)

    def _later(self, event: Dict[str, Any]) -> None:
        """The event a command produces, pushed after the command's answer."""
        asyncio.get_running_loop().call_soon(self.says, event)

    # -- the Connect cluster (master's connect_output.go) ---------------------

    def cluster(self) -> Dict[str, Any]:
        """What GET /connect/devices answers, and a `cluster` event carries."""
        active = DEVICE_ID if self._cluster_is_mine() else self.active_device_id
        devices = [{"id": DEVICE_ID, "name": "Milō", "type": "speaker"},
                   *({"id": device_id, **device} for device_id, device in self.others.items())]
        answer: Dict[str, Any] = {"active_device_id": active, "devices": sorted((
            {**device, "volume": 42000, "is_self": device["id"] == DEVICE_ID,
             "is_active": device["id"] == active, "is_offline": False, "can_transfer": True}
            for device in devices
        ), key=lambda device: device["id"])}
        playing = self._now_playing()
        if playing is not None:
            answer["now_playing"] = playing
        return answer

    def _cluster_is_mine(self) -> bool:
        """The cluster names this device active: a session here, past the
        answer to its first push (a transfer: `will_play`), and not taken."""
        return self.session and self.stage != "resolving" and not self.taken_back

    def cluster_event(self) -> Dict[str, Any]:
        return {"type": "cluster", "data": self.cluster()}

    def _now_playing(self) -> Optional[Dict[str, Any]]:
        """The cluster's playback: this daemon's own while a session plays
        here, else the last one reported elsewhere, whoever is active."""
        if self._cluster_is_mine() and (self.track or self.loading):
            song = self.track or self.loading
            paused, position = self.paused, song.get("position", 0)
        elif self.playback_elsewhere is not None:
            song, paused = self.playback_elsewhere["song"], self.playback_elsewhere["paused"]
            position = self.elsewhere_position()
        else:
            return None
        # The iPhone's cluster names neither the title nor the album, only the
        # uri (measured 2026-11-09); `cluster_names_track` is a sender that does.
        named = self.cluster_names_track or self._cluster_is_mine()
        return {"context_uri": "", "track_uri": song["uri"], "track_name": song["name"] if named else "",
                "artist_name": "", "album_name": song["album_name"] if named else "", "paused": paused,
                "playing": not paused, "position": position, "duration": song["duration"]}

    def elsewhere_position(self) -> int:
        """The playhead elsewhere now: extrapolated while it plays, capped at
        the track's end."""
        playback = self.playback_elsewhere
        position = playback["position_ms"]
        if not playback["paused"]:
            position += int((self.clock.now - playback["at"]) * 1000)
        return min(position, playback["song"]["duration"])

    def _transfer_starts(self) -> None:
        """A transfer here (asked here, or the phone picking Milō): the daemon
        is active at once, with no track yet, and resolves the context before
        it loads the track (`will_play`), 0.1 s later."""
        playback = self.playback_elsewhere
        self.taken_over = {**copy.deepcopy(playback), "position_ms": self.elsewhere_position()}
        self.session, self.account = True, self.stored
        self.track, self.paused, self.buffering = None, True, True
        self.loading = {**copy.deepcopy(self.taken_over["song"]), "position": self.taken_over["position_ms"]}
        self.stage = "resolving"
        self.says({"type": "active"})
        asyncio.get_running_loop().create_task(self._load_starts())

    async def _load_starts(self) -> None:
        await self.clock.sleep(0.1)
        if self.stage == "resolving":
            self.stage = "loading"
            self.active_device_id = ""      # the push answered: this device is active
            self.says({"type": "will_play", "uri": self.loading["uri"]})

    def track_loads(self) -> None:
        """The track a transfer brought is named, still buffering."""
        taken = self.taken_over
        self.track = {**copy.deepcopy(taken["song"]), "position": taken["position_ms"]}
        self.buffering, self.loading = True, None
        self.says(self.metadata())

    def transfer_lands(self) -> None:
        """~0.25 s later: the track it brought, where it stood — paused if it
        was paused there, unless a resume came during the load."""
        taken = self.taken_over
        self.transfer_pending = False
        paused = taken["paused"] and not self.resume_at_load
        self.track = {**copy.deepcopy(taken["song"]), "position": taken["position_ms"]}
        self.paused, self.buffering, self.loading = paused, False, None
        self.stage, self.resume_at_load = None, False
        self.says(self.metadata(), {"type": "paused"} if paused else self.playing())

    def _resumed(self) -> None:
        self.paused = False
        self.says(self.playing(resume=True))

    def _ends(self) -> None:
        self.session, self.track, self.paused, self.buffering = False, None, True, False
        self.loading, self.stage = None, None
        self.context = None
        self._later({"type": "inactive"})
        self._later({"type": "stopped"})
        if self.stored is not None and not self.signin_hangs:
            asyncio.get_running_loop().call_soon(self._signs_back_in)

    def _signs_back_in(self) -> None:
        self.signed_in = True
        self.says({"type": "playback_ready"})

    def dies(self) -> None:
        self.up = False
        self.session, self.track, self.loading = False, None, None
        if self._volume_report is not None:
            self._volume_report.cancel()
        if self.socket is not None:
            self.socket.queue.put_nowait(_CLOSED)
            self.socket = None

    def comes_up(self) -> None:
        self.up, self.session, self.track, self.paused, self.buffering = True, False, None, True, False
        self.output = "milo_spotify"
        self.context = None
        self.stage = None
        # The answer to the first push of the run, before /events connects.
        self.cluster_ready = True
        self.signed_in = self.stored is not None and not self.signin_hangs


class _AiohttpProxy:
    def __init__(self, session: Librespot) -> None:
        self.ClientSession = session

    def __getattr__(self, name: str) -> Any:
        return getattr(aiohttp, name)


class SpotifyWorld(WireReader):
    """The Spotify source on a real state machine, in a world the scenario drives."""

    def __init__(
        self, monkeypatch, tmp_path, settings: Optional[Dict[str, Any]] = None,
        stored: Optional[str] = None, follow_jam: bool = False,
    ):
        """`stored`: the account whose credentials go-librespot keeps in
        state.json (a phone cast to it once, with persist_credentials).
        `follow_jam`: the source follows the account's Jam (dependencies.py
        asks it of the real one)."""
        world = self
        self.clock = VirtualClock()
        use_virtual_wall(monkeypatch, self.clock)
        self.daemon = Librespot(self.clock)
        self.state_file = tmp_path / "state.json"
        if stored is not None:
            self.daemon.stored = stored
            self.state_file.write_text(json.dumps({
                "device_id": "0123456789abcdef0123456789abcdef01234567",
                "event_manager": None,
                "credentials": {"username": stored, "data": "c3RvcmVkLWJsb2I="},
                "last_volume": 42,
            }))
        self._pids = itertools.count(FIRST_PID)
        self.pid: Optional[int] = None
        self._invocations = itertools.count(0x2C7E0F58)
        self.runs: List[str] = []                 # every run's invocation, in order
        self.invocation: Optional[str] = None     # what systemd answers for the unit
        self.journal: "asyncio.Queue[str]" = asyncio.Queue()
        self.watches: List[Tuple[int, Callable[[], None]]] = []
        self.restarts: List[float] = []

        class Watch:
            """A pidfd watch: fires when the world kills that pid."""

            def __init__(self, pid: int, on_exit: Callable[[], None], *a: Any, **k: Any) -> None:
                self.on_exit = on_exit
                world.watches.append((pid, on_exit))
                if pid != world.pid:
                    asyncio.get_running_loop().call_soon(on_exit)

            def close(self) -> None:
                world.watches[:] = [w for w in world.watches if w[1] is not self.on_exit]

        systemd = Mock()
        systemd.start = AsyncMock(side_effect=self._unit_start)
        systemd.stop = AsyncMock(side_effect=self._unit_stop)
        systemd.restart = AsyncMock(side_effect=self._unit_restart)
        systemd.probe_active = AsyncMock(side_effect=lambda *_: self.pid is not None)

        async def is_active(unit: str) -> bool:
            # As SystemdServiceManager answers it: an unreadable probe is not up.
            return await systemd.probe_active(unit) is True

        systemd.is_active = AsyncMock(side_effect=is_active)
        systemd.main_pid = AsyncMock(side_effect=lambda *_: self.pid or 0)
        # What systemd says of the unit once its process is gone (measured
        # 2026-09-24): (ActiveState, Result) is `activating`/`signal` after a
        # crash (auto-restart), `inactive`/`success` after a stop.
        self.unit_state = ("inactive", "success")
        systemd.unit_state = AsyncMock(side_effect=lambda *_: self.unit_state)
        # NRestarts counts what Restart= did and is flushed by a restart that
        # was asked for (measured 2026-11-08, systemd 257).
        self.n_restarts = 0
        systemd.main_pid_and_restarts = AsyncMock(
            side_effect=lambda *_: (self.pid or 0, self.n_restarts)
        )
        systemd.invocation_id = AsyncMock(side_effect=lambda *_: self.invocation)
        self.systemd = systemd

        async def sleep(delay: float, *a: Any, **k: Any) -> Any:
            if delay <= 0.5:          # a poll interval, a settle delay
                return await asyncio.sleep(0)
            return await self.clock.sleep(delay)

        monkeypatch.setattr(spotify_module, "aiohttp", _AiohttpProxy(self.daemon))
        monkeypatch.setattr(library_module, "aiohttp", _AiohttpProxy(self.daemon))
        monkeypatch.setattr(library_module, "asyncio", AsyncioProxy(sleep))
        monkeypatch.setattr(spotify_module, "follow_unit", self._follow_journal)
        async def wait_for(awaitable: Any, timeout: float) -> Any:
            """asyncio.wait_for on the scenario's clock."""
            task = asyncio.ensure_future(awaitable)
            timer = asyncio.ensure_future(self.clock.sleep(timeout))
            try:
                await asyncio.wait((task, timer), return_when=asyncio.FIRST_COMPLETED)
            except asyncio.CancelledError:
                task.cancel()
                raise
            finally:
                timer.cancel()
            if task.done():
                return task.result()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise asyncio.TimeoutError()

        spotify_asyncio = AsyncioProxy(sleep)
        spotify_asyncio.wait_for = wait_for
        monkeypatch.setattr(spotify_module, "asyncio", spotify_asyncio)
        monkeypatch.setattr(websocket_module, "asyncio", AsyncioProxy(sleep))
        monkeypatch.setattr(jam_module, "asyncio", AsyncioProxy(sleep))
        monkeypatch.setattr(audio_source, "asyncio", AsyncioProxy(sleep))
        monkeypatch.setattr(audio_source, "ProcessWatch", Watch, raising=False)

        config = self.config_file = tmp_path / "config.yml"
        config.write_text(
            "server:\n  address: localhost\n  port: 3678\ncrossfade_duration: 0\nexternal_volume: true\n"
        )
        self.profiles_file = tmp_path / "spotify" / "profiles.json"
        self.history_file = tmp_path / "spotify" / "history.json"
        self.machine, self.recorder = make_state_machine()
        self.settings = make_settings(settings)
        self.source = SpotifySource(
            {"config_path": str(config), "profiles_path": str(self.profiles_file),
             "history_path": str(self.history_file), "follow_jam": follow_jam},
            state_machine=self.machine,
            settings_service=self.settings,
            systemd_manager=systemd,
        )
        self.machine.register_source(AudioSource.SPOTIFY, self.source)
        # Spotify's profile service (the internet): what it answers per account.
        self.identities = self.daemon.profile_answers

    # === systemd ===

    def _spawn(self) -> None:
        self.pid = next(self._pids)
        self.invocation = f"{next(self._invocations):032x}"
        self.runs.append(self.invocation)
        self.unit_state = ("active", "success")
        self.daemon.stored = self.stored_account()   # go-librespot reads state.json at start
        self.daemon.crossfade = yaml.safe_load(self.config_file.read_text()).get("crossfade_duration")   # and config.yml
        self.daemon.comes_up()

    def _die(self) -> None:
        pid, self.pid = self.pid, None
        if pid is None:
            return
        self.daemon.dies()
        for watched, on_exit in list(self.watches):
            if watched == pid:
                on_exit()

    async def _unit_start(self, *_: Any) -> bool:
        if self.pid is None:
            self._spawn()
        return True

    async def _unit_stop(self, *_: Any) -> bool:
        self.unit_state = ("inactive", "success")
        self.invocation = None
        self._die()
        return True

    async def _unit_restart(self, *_: Any) -> bool:
        self.restarts.append(self.clock.now)
        self.n_restarts = 0
        self._die()
        self._spawn()
        return True

    async def restarted_from_outside(self) -> None:
        """`systemctl restart` from a shell or an update: the unit is up again
        under a new pid before the source hears the old one exit (measured
        2026-11-08: `active`/`success` 9 ms after the pidfd fired)."""
        self.n_restarts = 0
        self._die()
        self._spawn()
        await settle()

    async def crash_heard_late(self) -> None:
        """SIGKILL, and the exit is read only once Restart= has brought the
        successor up: `active`/`success` too, but counted in NRestarts."""
        self.n_restarts += 1
        self._die()
        self._spawn()
        await settle()

    async def exits_by_itself(self) -> None:
        """A clean exit 0 under Restart=always, heard at once: `activating`
        (auto-restart), Result `success`, MainPID 0, count 0 (measured)."""
        self.unit_state = ("activating", "success")
        self._die()
        await settle()

    async def crash_heard_before_systemd(self) -> None:
        """SIGKILL, and the exit is read before systemd has reaped it: the unit
        still says `active`/`success` and names the dead pid as its MainPID."""
        dead = self.pid
        self.daemon.dies()
        for watched, on_exit in list(self.watches):
            if watched == dead:
                on_exit()
        await settle()
        self.pid = None

    async def kill_daemon(self) -> None:
        """SIGKILL: /events closes, nothing else is said."""
        self.unit_state = ("activating", "signal")
        self._die()
        await settle()

    async def events_blip(self) -> None:
        """/events closes while the daemon lives on; the client reconnects 2 s later."""
        socket, self.daemon.socket = self.daemon.socket, None
        if socket is not None:
            socket.queue.put_nowait(_CLOSED)
        await self.advance(2.1)

    async def systemd_restarts_it(self) -> None:
        """Restart= brings a new process up; the /events client reconnects."""
        self.n_restarts += 1
        self._spawn()
        await self.advance(2.1)

    # === the journal, as `journalctl -f -o json` hands it on ===

    def _follow_journal(self, unit: str, **_: Any):
        async def stream():
            while True:
                yield await self.journal.get()

        return stream()

    async def journal_says(self, message: str, invocation: Optional[str] = None) -> None:
        """go-librespot logs `message`, from the run `invocation` (the latest
        one by default) — an older run's line is one journald hands on late."""
        self.journal.put_nowait(json.dumps({
            "MESSAGE": message, "_SYSTEMD_INVOCATION_ID": invocation or self.runs[-1],
        }))
        await settle()

    async def idle(self) -> None:
        """Let the source finish what it started: the message in hand and the
        work off the mailbox (keeping a profile, writing state.json) read and
        write files on a worker thread, which `settle` does not wait for. Each
        wait is bounded, so a handler parked on a virtual timer is left there."""
        source = self.source
        for _ in range(100):
            await settle()
            pending = [
                task for task in (source._actor_handler, *source._bg._tasks)
                if task is not None and not task.done()
            ]
            if not pending and not source._actor_inbox and not source._actor_urgent:
                return
            if pending:
                await asyncio.wait(pending, timeout=0.05)

    # === state.json, as go-librespot keeps it ===

    def stored_state(self) -> Dict[str, Any]:
        return json.loads(self.state_file.read_text()) if self.state_file.exists() else {}

    def stored_account(self) -> Optional[str]:
        credentials = self.stored_state().get("credentials") or {}
        return credentials.get("username") if credentials.get("data") else None

    async def cast_from(self, account: str, song: Dict[str, Any] = None) -> None:
        """A phone signed in to `account` casts: go-librespot keeps its
        credentials (written before /status names it, measured), then plays."""
        state = self.stored_state() or {"device_id": "0123456789abcdef0123456789abcdef01234567",
                                         "event_manager": None, "last_volume": 42}
        state["credentials"] = {"username": account, "data": f"blob-of-{account}"}
        self.state_file.write_text(json.dumps(state))
        self.daemon.stored = account
        await self.phone_plays(song or PARAPLUIE, account=account)

    # === what a phone does, as measured ===

    async def _says(self, *events: Dict[str, Any]) -> None:
        self.daemon.says(*events)
        await settle()

    async def phone_transfers(self, song: Dict[str, Any] = PARAPLUIE, at_ms: int = 81264) -> None:
        """A phone moves its playback here: it loads paused, then plays 1.6 s later."""
        d = self.daemon
        d.session, d.account, d.track, d.paused, d.buffering = True, ACCOUNT, None, True, True
        d.loading, d.listed = {**copy.deepcopy(song), "position": at_ms}, None
        await self._says({"type": "active"}, {"type": "will_play", "uri": song["uri"]})
        d.track, d.buffering, d.loading = {**copy.deepcopy(song), "position": at_ms}, False, None
        await self._says(d.metadata(), {"type": "paused"})
        await self.advance(1.6)
        d.paused = False
        await self._says(d.playing(resume=True))

    async def phone_plays(self, song: Dict[str, Any] = PARAPLUIE, account: str = ACCOUNT) -> None:
        """A phone starts a track here from the top."""
        d = self.daemon
        d.session, d.account, d.track, d.paused, d.buffering = True, account, None, False, True
        d.loading, d.listed = song, None
        await self._says({"type": "active"}, {"type": "will_play", "uri": song["uri"]})
        await self.advance(0.07)
        d.track, d.buffering, d.loading = copy.deepcopy(song), False, None
        await self._says(d.metadata(), d.playing())

    async def phone_pauses(self) -> None:
        self.daemon.paused = True
        await self._says({"type": "paused"})

    async def phone_resumes(self) -> None:
        self.daemon.paused = False
        await self._says(self.daemon.playing(resume=True))

    async def phone_moves_volume(self, value: int) -> None:
        """The Spotify app's slider, moved on the phone (in steps)."""
        self.daemon.volume = value
        self.daemon.volume_changed()
        await settle()

    async def phone_seeks(self, position_ms: int) -> None:
        self.daemon.track["position"] = position_ms
        await self._says({"type": "seek", "position": position_ms,
                          "duration": self.daemon.track["duration"]})

    def _reports(self, song: Dict[str, Any], at_ms: int, paused: bool) -> None:
        """The active device reports its playback to the cluster; Spotify's
        metadata service knows the track, unless the test said otherwise."""
        d = self.daemon
        d.playback_elsewhere = {"song": copy.deepcopy(song), "paused": paused,
                                "position_ms": at_ms, "at": self.clock.now}
        if song["uri"] not in d.unnamed:
            d.track_metadata[gid_of(song)] = track_metadata_answer(song)

    async def plays_elsewhere(self, song: Dict[str, Any] = PARAPLUIE, *, at_ms: int = 0,
                              paused: bool = False, device: str = "iPhone", kind: str = "smartphone",
                              announced: bool = True) -> None:
        """Another device of the account plays `song`, and becomes the active
        one; go-librespot says `cluster` (`announced` False: the cluster is
        already so before /events connects)."""
        d = self.daemon
        device_id = OTHER_DEVICES[device]
        d.others[device_id] = {"name": device, "type": kind}
        d.active_device_id = device_id
        self._reports(song, at_ms, paused)
        if announced:
            await self._says(d.cluster_event())

    async def next_track_elsewhere(self, song: Dict[str, Any], *, at_ms: int = 0) -> None:
        """The same device moves on to `song`."""
        self._reports(song, at_ms, False)
        await self._says(self.daemon.cluster_event())

    async def pauses_elsewhere(self) -> None:
        d = self.daemon
        self._reports(d.playback_elsewhere["song"], d.elsewhere_position(), True)
        await self._says(d.cluster_event())

    async def transfer_lands(self, *, buffers_for: float = 0.0) -> None:
        """`buffers_for`: the track is named that long before it is ready."""
        await self.advance(0.25)
        if buffers_for:
            self.daemon.track_loads()
            await settle()
            await self.advance(buffers_for)
        self.daemon.transfer_lands()
        await settle()

    async def nothing_plays_elsewhere(self) -> None:
        """The other device left the cluster (gone to the background,
        offline); the cluster keeps the playback it last reported."""
        d = self.daemon
        d.others.pop(d.active_device_id, None)
        d.active_device_id = ""
        await self._says(d.cluster_event())

    async def nobody_active_stale(self) -> None:
        """Nobody is active any more, though the device is still listed and
        the cluster still holds its last state, playing (pinned at the end)."""
        self.daemon.active_device_id = ""
        await self._says(self.daemon.cluster_event())

    async def phone_takes_it_back(self, at_ms: int = 90_000, *, song: Optional[Dict[str, Any]] = None,
                                  back_after: float = 0.3, names_phone: bool = True,
                                  first_push: bool = True) -> None:
        """The phone takes the session over from Milō (playing `song`, else
        the track here), as go-librespot's code says it goes (the 204 window
        measured 2026-10-05): the cluster naming the phone active in a frame of
        its own, then — once the player stopped — `inactive`; the daemon hands
        the session back and signs in again — 204 for `back_after` s, then 404
        until its first push answers with the cluster, with no event: only
        `playback_ready` (`first_push` False: not yet, first_push_answers says
        it). `names_phone` False: by then nobody is active."""
        d = self.daemon
        song = {**copy.deepcopy(song or d.track), "position": 0}
        d.others[OTHER_DEVICES["iPhone"]] = {"name": "iPhone", "type": "smartphone"}
        d.active_device_id = OTHER_DEVICES["iPhone"]
        self._reports(song, at_ms, False)
        d.taken_back = True
        await self._says(d.cluster_event())
        d.session, d.track, d.paused, d.buffering, d.signed_in = False, None, True, False, False
        d.taken_back, d.cluster_ready = False, False
        await self._says({"type": "inactive"}, {"type": "stopped"})
        await self.advance(back_after)
        if not names_phone:
            d.active_device_id = ""
        d.signed_in = True
        if first_push:
            await self.first_push_answers()

    async def first_push_answers(self) -> None:
        """The daemon signed in again: the answer to its first push brings the
        cluster, and only `playback_ready` says so."""
        self.daemon.cluster_ready = True
        await self._says({"type": "playback_ready"})

    # === Spotify's Jam (social-connect, on the internet) ===

    async def jam_update(self, session: Optional[Dict[str, Any]], reason: str) -> None:
        """Spotify pushes a Jam update on the dealer socket (and its read
        answers the same from now on); None with SESSION_DELETED."""
        self.daemon.jam = session
        frame = {"type": "message", "uri": "social-connect/v2/session_update",
                 "headers": {"Content-Type": "application/json"},
                 "payloads": [{"session": session or {}, "reason": reason, "update_session_members": []}]}
        self.daemon.dealer.queue.put_nowait(frame)
        await settle()

    async def dealer_drops(self) -> None:
        """The dealer closes the socket."""
        socket, self.daemon.dealer = self.daemon.dealer, None
        socket.queue.put_nowait(_CLOSED)
        await settle()

    def jam(self) -> Optional[Dict[str, Any]]:
        return self.state()["details"]["jam"]

    async def phone_leaves(self) -> None:
        """The phone picks another output: the daemon drops the session."""
        d = self.daemon
        d.session, d.track, d.paused, d.buffering = False, None, True, False
        await self._says({"type": "inactive"}, {"type": "stopped"})

    # === Milō ===

    async def advance(self, seconds: float) -> None:
        await self.clock.advance(seconds)

    async def select(self) -> None:
        await self.machine.transition_to_source(AudioSource.SPOTIFY)
        await settle()

    async def leave(self) -> None:
        await self.machine.transition_to_source(AudioSource.NONE)
        await settle()

    async def command(self, cmd: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        result = await self.source.command(cmd, data)
        await settle()
        return result

    async def reroute(self, fails: bool = False) -> None:
        """A multiroom toggle. `fails`: snapcast refused to move (apply_mode raises)."""
        async def apply_mode() -> None:
            if fails:
                raise RuntimeError("Failed to start snapcast services")
        try:
            await self.machine.reroute_active_source(apply_mode)
        except RuntimeError:
            pass
        await settle()

    async def get_state(self) -> Dict[str, Any]:
        """GET /api/audio/state, as the route does it."""
        await self.machine.refresh_active_view()
        await settle()
        return self.state()

    # === what the wire says ===

    def transfers_sent(self) -> int:
        return sum(1 for command, _ in self.daemon.posted if command == "connect/transfer")

    def resumes_sent(self) -> int:
        return sum(1 for command, _ in self.daemon.posted if command == "resume")

    def stops_sent(self) -> int:
        return sum(1 for command, _ in self.daemon.posted if command == "stop")
