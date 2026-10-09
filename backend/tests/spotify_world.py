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

Another device of the account (go-librespot with `remote`, measured on the
owner's iPhone 2026-10-05): /status names the active device when it is not this
one, with its track once resolved and the playhead read when /status was; each
change raises `remote`. POST /player/transfer answers 200 at once, and the
session arrives as a phone's transfer does, at the same position.

The play order (the fork's GET /player/queue, measured 2026-10-07 on the
owner's iPhone): `provider` is "context", "queue" (the user's queue, listed
first in next_tracks and kept across a new context) or "autoplay" (the station
that replaces a context once it has run out, never listed after it); a
playlist's entries carry a uid, an album's none, the user's queue numbers its
own ("q1", "q2", the same track queued twice included).

Time is a VirtualClock the scenario advances; the daemon's death is heard
through the same pidfd watch the source opens (patched here, as for AirPlay).
"""
import asyncio
import copy
import itertools
import json
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple

import aiohttp
from unittest.mock import AsyncMock, Mock

from backend.core import audio_source
from backend.core.models.audio_state import AudioSource
from backend.sources.spotify import library as library_module
from backend.sources.spotify import source as spotify_module
from backend.sources.spotify import websocket as websocket_module
from backend.sources.spotify.source import SpotifySource
from backend.tests.golden.harness import (
    AsyncioProxy, VirtualClock, WireReader, make_settings, make_state_machine, settle, use_virtual_wall,
)

ACCOUNT = "p6puyc6we1egphk4nt2l5vaxy"
FIRST_PID = 46508


def track(name: str, duration: int = 182920, artists=("Kery James",), album: str = "Réel") -> Dict[str, Any]:
    slug = name.lower().replace(" ", "-")
    return {
        "uri": f"spotify:track:{slug}",
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


class _Exchange:
    def __init__(self, response: Any = None, error: Optional[BaseException] = None) -> None:
        self._response, self._error = response, error

    async def __aenter__(self) -> Any:
        if self._error is not None:
            raise self._error
        return self._response

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _EventsSocket:
    """One /events connection: what the daemon pushes, in order, until it closes."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()

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

    def __init__(self) -> None:
        self.up = False
        self.session = False            # a session is live (else /status is 204, or the signed-in 200)
        self.account: Optional[str] = None
        self.stored: Optional[str] = None   # the account whose credentials state.json holds
        self.signed_in = False          # signed in with no session (stored credentials)
        self.signin_hangs = False       # a sign-in that never completes (seen once, unreproduced)
        self.context: Optional[Tuple[str, str]] = None   # (uri, name) of what plays
        self.shuffle = False
        self.repeat_context = False
        self.repeat_track = False
        self.track: Optional[Dict[str, Any]] = None
        # The play order GET /player/queue lists around the track, as
        # {uri, uid, provider, track} entries (track None until its metadata
        # is cached). Any change of that answer raises `queue` ahead of the
        # events it comes with, as go-librespot compares the answer itself.
        self.prev_tracks: List[Dict[str, Any]] = []
        self.next_tracks: List[Dict[str, Any]] = []
        self.has_queue_route = True     # False: a go-librespot without it (stock 0.10.3), 404
        self.queue_answers = True       # False: GET /player/queue answers 503
        self.queue_reads = 0
        self.queue_body: Any = None     # not None: GET /player/queue answers 200 with this instead
        # True: a change of the order raises no `queue` of its own (the test
        # says it later), as when the event lands in a burst after the track's.
        self.queue_event_late = False
        self.queue_listed: Optional[Dict[str, Any]] = None
        # What the account plays on another device, as /status's `remote`.
        self.remote: Optional[Dict[str, Any]] = None
        self.taken_over: Optional[Dict[str, Any]] = None
        self.transfer_pending = False
        self.transfer_refused = False   # Spotify refused the transfer (a 500 from go-librespot)
        self.paused = True
        self.buffering = False
        self.status_answers = True      # False: /status answers 503 (learned nothing)
        self.refused_outputs: set = set()   # devices POST /player/output answers 500 for
        self.output = "milo_spotify"    # the ALSA device it writes to
        # A resume reaches /status only after its answer (the lag the reroute's
        # pause is confirmed against): /status still says paused meanwhile.
        self.resume_lag = False
        self.posted: List[Tuple[str, Dict[str, Any]]] = []
        self.socket: Optional[_EventsSocket] = None
        # The library, as the signed-in account sees it.
        self.playlists: List[Dict[str, Any]] = []
        self.listings: Dict[str, List[Dict[str, Any]]] = {}   # uri -> tracks
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
        self.token_reads = 0
        self.closed = False

    # -- aiohttp.ClientSession surface --------------------------------------

    def __call__(self, *a: Any, **k: Any) -> "Librespot":
        return self

    def get(self, url: str, *a: Any, **k: Any) -> _Exchange:
        if "homeview" in url:
            self.home_locales.append(k["params"]["locale"])
            answer = self.home_answer
            return _Exchange(_Response(503) if answer is None else _Response(200, answer))
        if "artistview" in url:
            self.artist_asked.append((url, k["params"]["locale"]))
            answer = self.releases_answer if url.endswith("/releases") else self.artist_answer
            return _Exchange(_Response(503) if answer is None else _Response(200, answer))
        if "/metadata/4/artist/" in url:
            answer = self.artist_metadata.get(url.rsplit("/", 1)[1])
            return _Exchange(_Response(503) if answer is None else _Response(200, answer))
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
            if not self.status_answers:
                return _Exchange(_Response(503))
            if not self.session and not self.signed_in:
                return _Exchange(_Response(204))
            if not self.session:
                return _Exchange(_Response(200, {
                    "username": self.stored, "stopped": False, "paused": False,
                    "buffering": False, "track": None, **self._player_flags(),
                    "remote": copy.deepcopy(self.remote),
                }))
            context_uri, context_name = self.context or (None, None)
            return _Exchange(_Response(200, {
                "username": self.account, "stopped": False, "paused": self.paused,
                "buffering": self.buffering, "track": copy.deepcopy(self.track),
                "context_uri": context_uri, "context_name": context_name,
                **self._player_flags(), "remote": None,
            }))
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
            current = {"uri": self.track["uri"], "uid": None, "provider": "context",
                       "track": copy.deepcopy(self.track)}
        return {"prev_tracks": copy.deepcopy(self.prev_tracks), "track": current,
                "next_tracks": copy.deepcopy(self.next_tracks)}

    def post(self, url: str, json: Optional[Dict[str, Any]] = None, **k: Any) -> _Exchange:
        if not self.up:
            return _Exchange(error=_refused())
        command = url.rsplit("/player/", 1)[1]
        body = json or {}
        self.posted.append((command, body))
        if command == "output":
            if body["device"] in self.refused_outputs:
                return _Exchange(_Response(500))
            self.output = body["device"]
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
        if command == "transfer":
            if self.transfer_refused:
                return _Exchange(_Response(500))
            if self.session:
                return _Exchange(_Response(200))
            self.transfer_pending = True
            asyncio.get_running_loop().call_soon(self._transfer_starts)
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
                self._later({"type": "playing"})
        elif command == "seek" and self.track:
            self.track["position"] = body["position"]
            self._later({"type": "seek", "position": body["position"]})
        elif command == "stop":
            self._ends()
        return _Exchange(_Response(200))

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
        return _Exchange(_Response(404))

    def ws_connect(self, url: str, *a: Any, **k: Any) -> _Exchange:
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
        self.session, self.account = True, self.stored
        self.context = (body["uri"], "Chill appart")
        self.track, self.paused, self.buffering = song, False, False
        self._later({"type": "will_play", "uri": uri})
        self._later({"type": "metadata", "uri": uri})
        self._later({"type": "playing"})

    # -- the daemon's side ----------------------------------------------------

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

    def _transfer_starts(self) -> None:
        """The transfer arrives: the daemon is active at once, with no track
        yet, and /status names no remote any more (it is the active device)."""
        self.taken_over, self.remote = self.remote, None
        self.session, self.account = True, self.stored
        self.track, self.paused, self.buffering = None, True, True
        self.says({"type": "active"}, {"type": "will_play", "uri": self.taken_over["track"]["uri"]})

    def transfer_lands(self) -> None:
        """~0.25 s later: the track it brought, where it stood, playing."""
        self.transfer_pending = False
        self.track, self.paused, self.buffering = copy.deepcopy(self.taken_over["track"]), False, False
        self.says({"type": "metadata", "uri": self.track["uri"]}, {"type": "playing"})

    def _resumed(self) -> None:
        self.paused = False
        self.says({"type": "playing"})

    def _ends(self) -> None:
        self.session, self.track, self.paused, self.buffering = False, None, True, False
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
        self.session, self.track = False, None
        if self.socket is not None:
            self.socket.queue.put_nowait(_CLOSED)
            self.socket = None

    def comes_up(self) -> None:
        self.up, self.session, self.track, self.paused, self.buffering = True, False, None, True, False
        self.output = "milo_spotify"
        self.context = None
        self.signed_in = self.stored is not None and not self.signin_hangs


class _AiohttpProxy:
    def __init__(self, session: Librespot) -> None:
        self.ClientSession = session

    def __getattr__(self, name: str) -> Any:
        return getattr(aiohttp, name)


def _silent_journal(unit: str, **_: Any):
    async def stream():
        await asyncio.Event().wait()
        yield ""  # pragma: no cover — unreachable, makes this a generator

    return stream()


class SpotifyWorld(WireReader):
    """The Spotify source on a real state machine, in a world the scenario drives."""

    def __init__(
        self, monkeypatch, tmp_path, settings: Optional[Dict[str, Any]] = None,
        stored: Optional[str] = None,
    ):
        """`stored`: the account whose credentials go-librespot keeps in
        state.json (a phone cast to it once, with persist_credentials)."""
        world = self
        self.clock = VirtualClock()
        use_virtual_wall(monkeypatch, self.clock)
        self.daemon = Librespot()
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
        systemd.is_active = AsyncMock(side_effect=lambda *_: self.pid is not None)
        systemd.probe_active = AsyncMock(side_effect=lambda *_: self.pid is not None)
        systemd.main_pid = AsyncMock(side_effect=lambda *_: self.pid)
        # What systemd says of the unit once its process is gone (measured
        # 2026-09-24): (ActiveState, Result) is `activating`/`signal` after a
        # crash (auto-restart), `inactive`/`success` after a stop.
        self.unit_state = ("inactive", "success")
        systemd.unit_state = AsyncMock(side_effect=lambda *_: self.unit_state)
        self.systemd = systemd

        async def sleep(delay: float, *a: Any, **k: Any) -> Any:
            if delay <= 0.5:          # a poll interval, a settle delay
                return await asyncio.sleep(0)
            return await self.clock.sleep(delay)

        monkeypatch.setattr(spotify_module, "aiohttp", _AiohttpProxy(self.daemon))
        monkeypatch.setattr(library_module, "aiohttp", _AiohttpProxy(self.daemon))
        monkeypatch.setattr(library_module, "asyncio", AsyncioProxy(sleep))
        monkeypatch.setattr(spotify_module, "follow_unit", _silent_journal)
        monkeypatch.setattr(spotify_module, "asyncio", AsyncioProxy(sleep))
        monkeypatch.setattr(websocket_module, "asyncio", AsyncioProxy(sleep))
        monkeypatch.setattr(audio_source, "asyncio", AsyncioProxy(sleep))
        monkeypatch.setattr(audio_source, "ProcessWatch", Watch, raising=False)

        config = tmp_path / "config.yml"
        config.write_text(
            "server:\n  address: localhost\n  port: 3678\ncrossfade_duration: 0\nexternal_volume: true\n"
        )
        self.profiles_file = tmp_path / "spotify" / "profiles.json"
        self.machine, self.recorder = make_state_machine()
        self.source = SpotifySource(
            {"config_path": str(config), "profiles_path": str(self.profiles_file)},
            state_machine=self.machine,
            settings_service=make_settings(settings),
            systemd_manager=systemd,
        )
        self.machine.register_source(AudioSource.SPOTIFY, self.source)
        # Spotify's profile service (the internet): what it answers per account.
        self.identities = self.daemon.profile_answers

    # === systemd ===

    def _spawn(self) -> None:
        self.pid = next(self._pids)
        self.unit_state = ("active", "success")
        self.daemon.stored = self.stored_account()   # go-librespot reads state.json at start
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
        self._die()
        return True

    async def _unit_restart(self, *_: Any) -> bool:
        self.restarts.append(self.clock.now)
        self._die()
        self._spawn()
        return True

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
        self._spawn()
        await self.advance(2.1)

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
        await self._says({"type": "active"}, {"type": "will_play", "uri": song["uri"]})
        d.track, d.buffering = {**copy.deepcopy(song), "position": at_ms}, False
        await self._says({"type": "metadata", "uri": song["uri"]}, {"type": "paused"})
        await self.advance(1.6)
        d.paused = False
        await self._says({"type": "playing", "resume": True})

    async def phone_plays(self, song: Dict[str, Any] = PARAPLUIE, account: str = ACCOUNT) -> None:
        """A phone starts a track here from the top."""
        d = self.daemon
        d.session, d.account, d.track, d.paused, d.buffering = True, account, None, False, True
        await self._says({"type": "active"}, {"type": "will_play", "uri": song["uri"]})
        await self.advance(0.07)
        d.track, d.buffering = copy.deepcopy(song), False
        await self._says({"type": "metadata", "uri": song["uri"]}, {"type": "playing"})

    async def phone_pauses(self) -> None:
        self.daemon.paused = True
        await self._says({"type": "paused"})

    async def phone_resumes(self) -> None:
        self.daemon.paused = False
        await self._says({"type": "playing", "resume": True})

    async def phone_seeks(self, position_ms: int) -> None:
        self.daemon.track["position"] = position_ms
        await self._says({"type": "seek", "position": position_ms,
                          "duration": self.daemon.track["duration"]})

    async def plays_elsewhere(self, song: Optional[Dict[str, Any]] = PARAPLUIE, *, at_ms: int = 0,
                              paused: bool = False, device: str = "iPhone") -> None:
        """Another device of the account plays `song` (None: its track is not
        resolved yet); go-librespot says `remote`."""
        self.daemon.remote = {
            "device_id": "1d0183bba7f631d45ffa2be4e1208f37682b5126", "device_name": device,
            "device_type": "SMARTPHONE", "paused": paused,
            "track": {**copy.deepcopy(song), "position": at_ms} if song else None,
        }
        await self._says({"type": "remote", "data": copy.deepcopy(self.daemon.remote)})

    async def transfer_lands(self) -> None:
        await self.advance(0.25)
        self.daemon.transfer_lands()
        await settle()

    async def nothing_plays_elsewhere(self) -> None:
        """The other device left the cluster (gone to the background, offline)."""
        self.daemon.remote = None
        await self._says({"type": "remote", "data": None})

    async def phone_takes_it_back(self, at_ms: int = 90_000) -> None:
        """The phone takes the session over from Milō, as measured (2026-10-05):
        go-librespot drops it (`inactive`), announces the phone (`remote`),
        hands the session back and signs in again — 204 for ~0.3 s — then
        names nothing for ~0.15 s (Spotify's cluster not back yet), and only
        then does /status name the phone."""
        d = self.daemon
        remote = {
            "device_id": "1d0183bba7f631d45ffa2be4e1208f37682b5126", "device_name": "iPhone",
            "device_type": "SMARTPHONE", "paused": False,
            "track": {**copy.deepcopy(d.track), "position": at_ms},
        }
        d.session, d.track, d.paused, d.buffering, d.signed_in = False, None, True, False, False
        await self._says({"type": "inactive"}, {"type": "stopped"}, {"type": "remote", "data": remote})
        await self.advance(0.3)
        d.signed_in = True
        await self._says({"type": "playback_ready"})
        await self.advance(0.15)
        d.remote = remote
        await self._says({"type": "remote", "data": remote})

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
        return sum(1 for command, _ in self.daemon.posted if command == "transfer")

    def stops_sent(self) -> int:
        return sum(1 for command, _ in self.daemon.posted if command == "stop")
