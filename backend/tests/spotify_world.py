"""Spotify's outside world, as measured on the unit (docs: source architecture,
phase 3b): go-librespot 0.10.0 — its HTTP API and its /events WebSocket — and
systemd holding it.

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
from backend.sources.spotify import source as spotify_module
from backend.sources.spotify import websocket as websocket_module
from backend.sources.spotify.source import SpotifySource
from backend.tests.golden.harness import (
    AsyncioProxy, VirtualClock, make_settings, make_state_machine, settle,
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

    async def json(self) -> Any:
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
        self.session = False            # a phone holds the speaker (else /status is 204)
        self.account: Optional[str] = None
        self.track: Optional[Dict[str, Any]] = None
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
        self.connections = 0

    # -- aiohttp.ClientSession surface --------------------------------------

    def __call__(self, *a: Any, **k: Any) -> "Librespot":
        return self

    def get(self, url: str, *a: Any, **k: Any) -> _Exchange:
        if not self.up:
            return _Exchange(error=_refused())
        if url.endswith("/status"):
            if not self.status_answers:
                return _Exchange(_Response(503))
            if not self.session:
                return _Exchange(_Response(204))
            return _Exchange(_Response(200, {
                "username": self.account, "stopped": False, "paused": self.paused,
                "buffering": self.buffering, "track": copy.deepcopy(self.track),
            }))
        return _Exchange(_Response(200, {"playback_ready": self.session}))

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
        if not self.session:
            return _Exchange(_Response(400))
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

    def ws_connect(self, url: str, *a: Any, **k: Any) -> _Exchange:
        if not self.up:
            return _Exchange(error=_refused())
        self.connections += 1
        self.socket = _EventsSocket()
        return _Exchange(self.socket)

    async def close(self) -> None:
        return None

    # -- the daemon's side ----------------------------------------------------

    def says(self, *events: Dict[str, Any]) -> None:
        if self.socket is not None:
            for event in events:
                self.socket.queue.put_nowait(event)

    def _later(self, event: Dict[str, Any]) -> None:
        """The event a command produces, pushed after the command's answer."""
        asyncio.get_running_loop().call_soon(self.says, event)

    def _resumed(self) -> None:
        self.paused = False
        self.says({"type": "playing"})

    def _ends(self) -> None:
        self.session, self.track, self.paused, self.buffering = False, None, True, False
        self._later({"type": "inactive"})
        self._later({"type": "stopped"})

    def dies(self) -> None:
        self.up = False
        self.session, self.track = False, None
        if self.socket is not None:
            self.socket.queue.put_nowait(_CLOSED)
            self.socket = None

    def comes_up(self) -> None:
        self.up, self.session, self.track, self.paused, self.buffering = True, False, None, True, False
        self.output = "milo_spotify"


class _AiohttpProxy:
    def __init__(self, session: Librespot) -> None:
        self.ClientSession = session

    def __getattr__(self, name: str) -> Any:
        return getattr(aiohttp, name)


class _LoopView:
    def __init__(self, clock: VirtualClock) -> None:
        self._clock = clock

    def time(self) -> float:
        return self._clock.now

    def __getattr__(self, name: str) -> Any:
        return getattr(asyncio.get_running_loop(), name)


def _silent_journal(unit: str, **_: Any):
    async def stream():
        await asyncio.Event().wait()
        yield ""  # pragma: no cover — unreachable, makes this a generator

    return stream()


class SpotifyWorld:
    """The Spotify source on a real state machine, in a world the scenario drives."""

    def __init__(self, monkeypatch, tmp_path, settings: Optional[Dict[str, Any]] = None):
        world = self
        self.clock = VirtualClock()
        self.daemon = Librespot()
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

        class SourceAsyncio(AsyncioProxy):
            def get_running_loop(self) -> _LoopView:
                return _LoopView(world.clock)

        monkeypatch.setattr(spotify_module, "aiohttp", _AiohttpProxy(self.daemon))
        monkeypatch.setattr(spotify_module, "follow_unit", _silent_journal)
        monkeypatch.setattr(spotify_module, "asyncio", SourceAsyncio(sleep))
        monkeypatch.setattr(websocket_module, "asyncio", AsyncioProxy(sleep))
        monkeypatch.setattr(audio_source, "asyncio", AsyncioProxy(sleep))
        monkeypatch.setattr(audio_source, "ProcessWatch", Watch, raising=False)

        config = tmp_path / "config.yml"
        config.write_text("server:\n  address: localhost\n  port: 3678\ncrossfade_duration: 0\n")
        self.machine, self.recorder = make_state_machine()
        self.source = SpotifySource(
            {"config_path": str(config)},
            state_machine=self.machine,
            settings_service=make_settings(settings),
            systemd_manager=systemd,
        )
        self.machine.register_source(AudioSource.SPOTIFY, self.source)

    # === systemd ===

    def _spawn(self) -> None:
        self.pid = next(self._pids)
        self.unit_state = ("active", "success")
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

    async def phone_plays(self, song: Dict[str, Any] = PARAPLUIE) -> None:
        """A phone starts a track here from the top."""
        d = self.daemon
        d.session, d.account, d.track, d.paused, d.buffering = True, ACCOUNT, None, False, True
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

    async def phone_skips_to(self, song: Dict[str, Any]) -> None:
        d = self.daemon
        d.track, d.buffering, d.paused = None, True, False
        await self._says({"type": "will_play", "uri": song["uri"]})
        await self.advance(0.07)
        d.track, d.buffering = copy.deepcopy(song), False
        await self._says({"type": "metadata", "uri": song["uri"]}, {"type": "playing"})

    async def phone_seeks(self, position_ms: int) -> None:
        self.daemon.track["position"] = position_ms
        await self._says({"type": "seek", "position": position_ms,
                          "duration": self.daemon.track["duration"]})

    async def track_runs_out_into(self, song: Dict[str, Any]) -> None:
        d = self.daemon
        d.paused, d.track["position"] = True, 0
        await self._says({"type": "not_playing"})
        await self.advance(0.3)
        await self.phone_skips_to(song)

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
        await self.machine.refresh_active_metadata()
        await settle()
        return self.state()

    # === what the wire says ===

    def state(self) -> Dict[str, Any]:
        return self.machine.get_current_state()

    def meta(self) -> Dict[str, Any]:
        return self.state()["metadata"] or {}

    def active(self) -> bool:
        return self.state()["source_state"] == "active"

    def playing(self) -> bool:
        return self.active() and bool(self.meta().get("is_playing"))

    def buffering(self) -> bool:
        return self.active() and bool(self.meta().get("is_buffering"))

    def errors(self) -> List[str]:
        return [
            e["data"]["reason"] for e in self.recorder.envelopes
            if e["category"] == "source" and e["type"] == "error"
        ]

    def published(self) -> List[Dict[str, Any]]:
        out = []
        for e in self.recorder.envelopes:
            full = (e.get("data") or {}).get("full_state")
            if e["category"] == "source" and e["type"] == "state_changed" and full:
                out.append({"state": full["source_state"], **(full.get("metadata") or {})})
        return out

    def stops_sent(self) -> int:
        return sum(1 for command, _ in self.daemon.posted if command == "stop")
