"""Qobuz's outside world, as measured on the unit (docs: source architecture,
phase 3c): qobuz-proxy 1.7.2 behind rootfs/usr/local/bin/milo-qobuz, polled at
GET /api/status, and systemd holding the sidecar.

What the sidecar was measured to report (2026-09-24, the owner's Mac app on a
free account, a tracing copy of milo-qobuz):

- The app picking Milō: `renderer_active` true, the player STOPPED with no
  track (the cloud hands over a paused queue position, and nothing loads until
  play). Play: LOADING, then PLAYING ~100 ms later with the track.
- Pause / resume: PAUSED / PLAYING. A skip, playing or paused: LOADING ~100 ms,
  then PLAYING on the next track (the app plays a track skipped to while
  paused). A seek: only the playhead moves. A preview running into the next
  track: the title alone.
- During LOADING the upstream status reads "idle" and carries no now_playing;
  now_playing is built for "playing" and "paused" only.
- The app picking another output: `renderer_active` false, the player STOPPED.
  The app quitting: nothing at all, the queue plays on.
- A restart of the sidecar is what makes the app let go; SIGKILL too (systemd
  brings a new one up 5 s later, which the app has let go of).
- A sidecar coming up refuses connections for ~0.45 s, then answers
  `authenticated: false` for ~0.3 s before its credentials load: the first
  poll after a start is refused here, the worst case of that window.
"""
import asyncio
import copy
import itertools
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, Mock

import aiohttp

from backend.core import audio_source
from backend.core.models.audio_state import AudioSource
from backend.sources.qobuz import account as account_module
from backend.sources.qobuz import monitor as monitor_module
from backend.sources.qobuz import source as qobuz_module
from backend.sources.qobuz.source import QobuzSource
from backend.tests.golden.harness import (
    AsyncioProxy, VirtualClock, WireReader, make_settings, make_state_machine, settle, use_virtual_wall,
)

FIRST_PID = 96029

ON_AND_ON = {"title": "On & On", "artist": "Erykah Badu", "album": "Baduizm",
             "album_art_url": "https://static.qobuz.example/on-and-on.jpg"}
NEXT_LIFETIME = {"title": "Next Lifetime", "artist": "Erykah Badu", "album": "Baduizm",
                 "album_art_url": "https://static.qobuz.example/next-lifetime.jpg"}
TYRONE = {"title": "Tyrone", "artist": "Erykah Badu", "album": "Live",
          "album_art_url": "https://static.qobuz.example/tyrone.jpg"}

_UPSTREAM_STATUS = {"playing": "playing", "paused": "paused"}


class _Response:
    def __init__(self, status: int, payload: Dict[str, Any]) -> None:
        self.status = status
        self._payload = payload

    async def json(self) -> Dict[str, Any]:
        return copy.deepcopy(self._payload)


class _Exchange:
    def __init__(self, response: _Response) -> None:
        self._response = response

    async def __aenter__(self) -> _Response:
        return self._response

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class Sidecar:
    """qobuz-proxy's local HTTP API as milo-qobuz extends it."""

    def __init__(self) -> None:
        self.up = False
        self.refusals = 0
        self.http_status = 200
        self.authenticated = True
        self.reset()

    def reset(self) -> None:
        """A fresh process: nobody's output, nothing loaded."""
        self.renderer_active = False
        self.player_state = "stopped"
        self.track: Optional[Dict[str, Any]] = None
        self.position_ms = 0
        self.duration_ms = 0

    # -- aiohttp.ClientSession surface --------------------------------------

    def __call__(self, *a: Any, **k: Any) -> "Sidecar":
        return self

    def get(self, url: str, *a: Any, **k: Any) -> _Exchange:
        if not self.up or self.refusals:
            self.refusals = max(0, self.refusals - 1)
            raise ConnectionRefusedError("Connect call failed ('127.0.0.1', 8689)")
        status = _UPSTREAM_STATUS.get(self.player_state, "idle")
        speaker: Dict[str, Any] = {
            "id": "milo",
            "name": "Milo",
            "status": status,
            "config": {"audio_device": "milo_qobuz"},
            "now_playing": None,
            "player_state": self.player_state,
            "renderer_active": self.renderer_active,
        }
        if self.track is not None and status in ("playing", "paused"):
            speaker["now_playing"] = {
                **self.track, "quality": "MP3 320kbps",
                "position_ms": self.position_ms, "duration_ms": self.duration_ms,
            }
        payload = {"auth": {"authenticated": self.authenticated}, "speakers": [speaker]}
        return _Exchange(_Response(self.http_status, payload))

    async def close(self) -> None:
        return None


class _AiohttpProxy:
    """The monitor module's `aiohttp`, with ClientSession answered by the sidecar."""

    def __init__(self, session: Sidecar) -> None:
        self.ClientSession = session

    def __getattr__(self, name: str) -> Any:
        return getattr(aiohttp, name)




class QobuzWorld(WireReader):
    """The Qobuz source on a real state machine, in a world the scenario drives.

    The monitor polls once per virtual second: `advance(1)` is one poll.
    """

    def __init__(
        self, monkeypatch, tmp_path, settings: Optional[Dict[str, Any]] = None,
        logged_in: bool = True,
    ):
        world = self
        self.clock = VirtualClock()
        use_virtual_wall(monkeypatch, self.clock)
        self.sidecar = Sidecar()
        self._pids = itertools.count(FIRST_PID)
        self.pid: Optional[int] = None
        self.watches: List[Tuple[int, Callable[[], None]]] = []
        self.restarts: List[float] = []
        # A poll the old sidecar answers between Milō asking for the restart
        # and systemd stopping it (the restart takes ~0.5-1 s at 1 Hz polls).
        self.poll_lands_during_restart = False

        class Watch:
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
        # (ActiveState, Result) once the process is gone, measured 2026-09-24:
        # `activating`/`signal` after a crash, `inactive`/`success` after a stop.
        self.unit_state = ("inactive", "success")
        systemd.unit_state = AsyncMock(side_effect=lambda *_: self.unit_state)
        self.systemd = systemd

        async def sleep(delay: float, *a: Any, **k: Any) -> Any:
            if delay <= 0.5:
                return await asyncio.sleep(0)
            return await self.clock.sleep(delay)


        monkeypatch.setattr(monitor_module, "aiohttp", _AiohttpProxy(self.sidecar))
        monkeypatch.setattr(monitor_module, "asyncio", AsyncioProxy(sleep))
        monkeypatch.setattr(audio_source, "asyncio", AsyncioProxy(sleep))
        monkeypatch.setattr(audio_source, "ProcessWatch", Watch, raising=False)
        self.volume_flag = tmp_path / "allow_app_volume"
        # qobuz-proxy's token cache, off the appliance's own (a unit that has
        # logged in holds one; CI does not).
        self.credentials = tmp_path / "credentials.json"
        monkeypatch.setattr(account_module, "QOBUZ_CREDENTIALS_FILE", self.credentials)
        if logged_in:
            self.sidecar_logs_in()
        else:
            self.sidecar.authenticated = False
        monkeypatch.setattr(qobuz_module, "QOBUZ_VOLUME_FLAG", self.volume_flag)

        self.machine, self.recorder = make_state_machine()
        self.source = QobuzSource(
            None,
            state_machine=self.machine,
            settings_service=make_settings({"qobuz": {"allow_app_volume": False}, **(settings or {})}),
            systemd_manager=systemd,
        )
        self.machine.register_source(AudioSource.QOBUZ, self.source)

    # === systemd ===

    def _spawn(self) -> None:
        self.pid = next(self._pids)
        self.unit_state = ("active", "success")
        self.sidecar.up = True
        self.sidecar.refusals = 1
        self.sidecar.reset()

    def _die(self) -> None:
        pid, self.pid = self.pid, None
        if pid is None:
            return
        self.sidecar.up = False
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
        if self.poll_lands_during_restart:
            async with self.sidecar.get("status") as response:
                payload = await response.json()
            await self.source._on_status(payload["speakers"][0], payload["auth"]["authenticated"])
        self._die()
        self._spawn()
        return True

    async def kill_sidecar(self) -> None:
        self.unit_state = ("activating", "signal")
        self._die()
        await settle()

    async def systemd_restarts_it(self) -> None:
        self._spawn()
        await self.advance(1)

    # === the app, as measured ===

    async def app_picks_milo(self) -> None:
        """The app selects the speaker: active, nothing loaded yet."""
        self.sidecar.renderer_active = True
        self.sidecar.player_state = "stopped"
        await self.advance(1)

    async def app_plays(self, track: Dict[str, Any] = ON_AND_ON, duration_ms: int = 226000) -> None:
        """Picks the speaker (if not already) and plays: LOADING ~100 ms, then the track."""
        if not self.sidecar.renderer_active:
            await self.app_picks_milo()
        self.sidecar.player_state = "loading"
        self.sidecar.track, self.sidecar.position_ms = track, 0
        self.sidecar.duration_ms = duration_ms
        self.sidecar.player_state = "playing"
        await self.advance(1)

    async def loads(self, track: Dict[str, Any]) -> None:
        """A skip caught mid-load by a poll: LOADING, no now_playing."""
        self.sidecar.player_state = "loading"
        self.sidecar.track, self.sidecar.position_ms = track, 0
        await self.advance(1)

    async def plays_on(self, seconds: int) -> None:
        for _ in range(seconds):
            self.sidecar.position_ms += 1000
            await self.advance(1)

    async def app_pauses(self) -> None:
        self.sidecar.player_state = "paused"
        await self.advance(1)

    async def app_resumes(self) -> None:
        self.sidecar.player_state = "playing"
        await self.advance(1)

    async def app_seeks(self, position_ms: int) -> None:
        self.sidecar.position_ms = position_ms
        await self.advance(1)

    async def track_fails(self) -> None:
        self.sidecar.player_state = "error"
        await self.advance(1)

    def sidecar_logs_in(self) -> None:
        """The OAuth callback landed: the sidecar caches the token."""
        self.sidecar.authenticated = True
        self.credentials.write_text(
            '{"user_id": 1234567, "user_auth_token": "a-token", "email": "someone@example.com"}'
        )

    async def app_picks_another_output(self) -> None:
        self.sidecar.renderer_active = False
        self.sidecar.player_state = "stopped"
        await self.advance(1)

    # === Milō ===

    async def advance(self, seconds: float) -> None:
        await self.clock.advance(seconds)

    async def boot(self) -> None:
        """The backend coming up: initialize_services() initializes the source
        (it reads the token cache) long before anyone selects Qobuz."""
        await self.source.initialize()
        await settle()

    async def select(self) -> None:
        await self.machine.transition_to_source(AudioSource.QOBUZ)
        await settle()

    async def leave(self) -> None:
        await self.machine.transition_to_source(AudioSource.NONE)
        await settle()

    async def reroute(self) -> None:
        async def apply_mode() -> None:
            return None
        await self.machine.reroute_active_source(apply_mode)
        await self.advance(1)

    def availability(self) -> Optional[str]:
        return self.state()["availability"]["qobuz"]
