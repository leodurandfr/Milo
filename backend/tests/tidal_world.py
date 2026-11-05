"""Tidal's outside world, as measured on the unit (docs: source architecture,
phase 3b): tidal_connect_application's `tisoc` controller socket and systemd
holding the daemon.

What the daemon was measured to send (2026-09-24, the owner's Mac, strace on
the controller socket; the load and the resume re-read 2026-11-05, the owner's
free tier, raw frames by strace):

- A controller (re)connecting: `startService` is answered by
  `notifyServiceStateChanged` — a daemon that restarted says nothing else.
- A sender picking the speaker: `notifySessionState 1`, `requestResources`,
  `notifyRequestResult`, `notifySessionState 2` in 5 ms; 0.25-0.6 s later
  `setRepeatMode`, `setShuffle`, the media, PAUSED at the sender's position,
  BUFFERING; the media again 0.37 s later, PAUSED, BUFFERING ×2 and PLAYING at
  that position, all in one burst. Nothing names a track before the media.
- That PLAYING is not sound. A track that sounds: `notifyAudioFormatUpdated`
  0.6-0.8 s later (for every new track, the format unchanged or not), the
  audio beginning within 10 ms of it; the first status with a progress
  (503-507, the preview's 30066 as duration) ~0.5 s after that, then one
  every ~0.5 s. A track that never sounds (about one in three on the free
  tier, no decode in the daemon's journal, digital silence downstream):
  nothing at all after its PLAYING at 0, for as long as it is left (37 s
  seen); a pause is PAUSED at 0, a resume BUFFERING then PLAYING at 0 in one
  burst.
- Pause: PAUSED. Resume of a track that sounded: BUFFERING, then 7 ms later
  BUFFERING again and PLAYING, the second pair carrying where the daemon
  stopped (504 ms past the pause's report, measured: the pause repeated the
  last tick's progress 480 ms after it; the world pauses on a tick, where the
  two agree). No audio format on a resume.
- A skip while playing: IDLE ×2, the next media, BUFFERING; 0.12 s later the
  media again, PAUSED, BUFFERING ×2, PLAYING at 0; then the audio format and
  the progress as for a start. While paused (2026-09-24): IDLE ×2, media,
  PAUSED, media, PAUSED. A track running into the next without a gap
  (2026-09-24): the media alone.
- The sender leaving: `releaseResources`, `notifySessionState 0`, IDLE,
  `notifyRequestResult 1002`.
- No controller command ends a session: `interrupt` only notifies, `stop`
  pauses at 0, `stopService` withdraws nothing. A restart of the daemon is what
  makes the sender let go (a SIGTERM restart takes 28 ms).
- SIGKILL: the socket closes; systemd brings a new process 5 s later.
"""
import asyncio
import itertools
import json
import struct
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, Mock

from backend.core import audio_source
from backend.core.models.audio_state import AudioSource
from backend.sources.tidal import controller_socket as controller_module
from backend.sources.tidal.source import TidalSource
from backend.tests.golden.harness import (
    AsyncioProxy, VirtualClock, WireReader, make_settings, make_state_machine, settle, use_virtual_wall,
)

FIRST_PID = 47039
PREVIEW_MS = 30066
# A resume's BUFFERING, then its PLAYING this much later (measured).
RESUME_GAP_S = 0.007
# A new track's PLAYING at 0, then its audio format this much later (measured
# 0.6-0.8 s), then its first progress (measured ~0.5 s, 503-507 ms).
SOUND_AFTER_S = 0.74
TICK_S = 0.5
_START, _END = b"\xff\x02", b"\xff\x03"


def media(title: str, duration: int = 271000, artists=("Dr. Dre", "Snoop Dogg"),
          album: str = "2001", custom: bool = False) -> Dict[str, Any]:
    slug = title.lower().replace(" ", "-")
    info: Dict[str, Any] = {
        "itemId": slug, "mediaId": slug, "mediaType": 0,
        "metadata": {
            "title": title, "artists": list(artists), "albumTitle": album, "duration": duration,
            "images": {
                "low": {"url": f"https://resources.tidal.example/{slug}/320.jpg", "width": 320},
                "high": {"url": f"https://resources.tidal.example/{slug}/1280.jpg", "width": 1280},
            },
        },
    }
    if custom:
        info["customData"] = "{}"
    return {"command": "notifyMediaChanged", "mediaInfo": info}


def status(state: str, progress: int = 0, duration: int = PREVIEW_MS) -> Dict[str, Any]:
    if state == "IDLE":
        progress, duration = 0, 0
    return {"command": "notifyPlayerStatusChanged", "playerState": state,
            "progress": progress, "duration": duration}


# What the daemon sends as a track's audio begins (the free tier's preview).
AUDIO_FORMAT = {
    "command": "notifyAudioFormatUpdated", "audioBitPerSample": 16, "audioBitrate": 705600,
    "audioChannels": 2, "audioCodec": 0, "audioSamplingRate": 22050, "mediaformat": 0,
    "streamType": 0,
}

STILL_DRE = "Still D.R.E."
KEEP_IT_THORO = "Keep It Thoro"
HYPNOTIZE = "Hypnotize"


class _Writer:
    """The controller's end: every frame it writes reaches the daemon."""

    def __init__(self, daemon: "Tisoc") -> None:
        self._daemon, self._closing = daemon, False

    def write(self, data: bytes) -> None:
        (length,) = struct.unpack(">H", data[2:4])
        self._daemon.heard(json.loads(data[4:4 + length]))

    async def drain(self) -> None:
        # A daemon whose end of the socket is gone: the kernel answers the
        # write with EPIPE. A daemon that merely stops reading is not modeled
        # here, because a Unix socket would not tell the controller either:
        # the write lands in the kernel buffer and drain() returns.
        if self._daemon.closed_its_end:
            raise BrokenPipeError(32, "Broken pipe")

    def is_closing(self) -> bool:
        return self._closing

    def close(self) -> None:
        self._closing = True

    async def wait_closed(self) -> None:
        return None


class Tisoc:
    """The daemon's controller socket, and what it answers to a command."""

    def __init__(self, later: Callable[..., None]) -> None:
        self.later = later
        self.up = False
        self.received: List[str] = []
        self.reader: Optional[asyncio.StreamReader] = None
        self.connections = 0
        self.state = "IDLE"
        self.progress = 0
        # The track loaded now produces audio: a silent one sends nothing past
        # its PLAYING at 0.
        self.sounding = False
        self.closed_its_end = False

    async def open_unix_connection(self, path: str, *a: Any, **k: Any):
        if not self.up:
            raise FileNotFoundError(path)
        self.connections += 1
        self.reader = asyncio.StreamReader()
        return self.reader, _Writer(self)

    def heard(self, message: Dict[str, Any]) -> None:
        command = message["command"]
        self.received.append(command)
        if command == "startService":
            self.push({"command": "notifyServiceStateChanged", "serviceState": 1})
        elif command == "stopService":
            self.push({"command": "notifyServiceStateChanged", "serviceState": 0})
        elif command == "pause":
            self.state = "PAUSED"
            self.push(status("PAUSED", self.progress))
        elif command == "play":
            self.state = "PLAYING"
            if not self.sounding:
                self.push(status("BUFFERING", self.progress), status("PLAYING", self.progress))
                return
            self.push(status("BUFFERING", self.progress))
            self.later(RESUME_GAP_S, status("BUFFERING", self.progress), status("PLAYING", self.progress))

    def push(self, *messages: Dict[str, Any]) -> None:
        if self.reader is None:
            return
        for message in messages:
            payload = json.dumps(message).encode()
            self.reader.feed_data(_START + struct.pack(">H", len(payload)) + payload + _END)

    def dies(self) -> None:
        self.up = False
        if self.reader is not None:
            self.reader.feed_eof()
            self.reader = None


class TidalWorld(WireReader):
    """The Tidal source on a real state machine, in a world the scenario drives."""

    def __init__(self, monkeypatch, tmp_path, settings: Optional[Dict[str, Any]] = None):
        world = self
        self.clock = VirtualClock()
        use_virtual_wall(monkeypatch, self.clock)
        self.daemon = Tisoc(self._push_later)
        self._pids = itertools.count(FIRST_PID)
        self.pid: Optional[int] = None
        self.watches: List[Tuple[int, Callable[[], None]]] = []
        self.restarts: List[float] = []

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
        systemd.main_pid = AsyncMock(side_effect=lambda *_: self.pid or 0)
        # What systemd says of the unit once its process is gone (measured
        # 2026-09-24): (ActiveState, Result) is `activating`/`signal` after a
        # crash (auto-restart), `inactive`/`success` after a stop.
        self.unit_state = ("inactive", "success")
        systemd.unit_state = AsyncMock(side_effect=lambda *_: self.unit_state)
        self.systemd = systemd

        async def sleep(delay: float, *a: Any, **k: Any) -> Any:
            if delay <= 0.5:
                return await asyncio.sleep(0)
            return await self.clock.sleep(delay)

        class ControllerAsyncio(AsyncioProxy):
            open_unix_connection = staticmethod(self.daemon.open_unix_connection)

        monkeypatch.setattr(controller_module, "asyncio", ControllerAsyncio(sleep))
        monkeypatch.setattr(audio_source, "asyncio", AsyncioProxy(sleep))
        monkeypatch.setattr(audio_source, "ProcessWatch", Watch, raising=False)

        self.machine, self.recorder = make_state_machine()
        self.source = TidalSource(
            {"socket_path": str(tmp_path / "tisoc.sock")},
            state_machine=self.machine,
            settings_service=make_settings(settings),
            systemd_manager=systemd,
        )
        self.machine.register_source(AudioSource.TIDAL, self.source)

    def _push_later(self, delay: float, *messages: Dict[str, Any]) -> None:
        """The daemon pushes `messages` `delay` seconds of the scenario's clock
        from now: a frame Milō reads apart from the one before it."""
        async def push() -> None:
            await self.clock.sleep(delay)
            self.daemon.push(*messages)
        asyncio.get_running_loop().create_task(push())

    # === systemd ===

    def _spawn(self) -> None:
        self.pid = next(self._pids)
        self.unit_state = ("active", "success")
        self.daemon.up = True
        self.daemon.state, self.daemon.progress, self.daemon.sounding = "IDLE", 0, False

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
        self.unit_state = ("activating", "signal")
        self._die()
        await settle()

    async def systemd_restarts_it(self) -> None:
        self._spawn()
        await self.advance(1.1)      # the controller's reconnect delay

    # === the daemon, as measured ===

    async def sends(self, *messages: Dict[str, Any]) -> None:
        self.daemon.push(*messages)
        await settle()

    async def mac_picks_the_speaker(self) -> None:
        """The session opens: nothing names a track yet."""
        await self.sends(
            {"command": "notifySessionState", "state": 1,
             "appInfo": {"appId": "tidal", "appName": "tidal"}},
            {"command": "requestResources"},
            {"command": "notifyRequestResult", "requestId": 0, "resultCode": 0},
            {"command": "notifySessionState", "state": 2,
             "appInfo": {"appId": "tidal", "appName": "tidal"}},
        )

    async def mac_loads(self, title: str = STILL_DRE, at_ms: int = 0) -> None:
        """A sender picks the speaker and its track loads, ending on the
        daemon's PLAYING — which no sound has followed yet."""
        await self.mac_picks_the_speaker()
        await self.advance(0.35)
        await self.sends(
            {"command": "setRepeatMode", "repeatMode": "OFF"},
            {"command": "setShuffle", "shuffle": False},
            media(title), status("PAUSED", at_ms, 271000), status("BUFFERING", at_ms, 271000),
        )
        await self.advance(0.37)
        self.daemon.state, self.daemon.progress, self.daemon.sounding = "PLAYING", at_ms, False
        await self.sends(
            media(title, custom=True), status("PAUSED", at_ms, 271000),
            status("BUFFERING", at_ms, 271000), status("BUFFERING", at_ms, 271000),
            status("PLAYING", at_ms, 271000),
        )

    async def track_sounds(self) -> None:
        """The loaded track's audio begins: its audio format SOUND_AFTER_S
        after the PLAYING, its first progress a tick later."""
        await self.advance(SOUND_AFTER_S)
        self.daemon.sounding = True
        await self.sends(AUDIO_FORMAT)
        await self.plays_on(TICK_S)

    async def mac_plays(self, title: str = STILL_DRE, at_ms: int = 0, silent: bool = False) -> None:
        """A sender picks the speaker and its track plays — or, `silent`,
        never sounds."""
        await self.mac_loads(title, at_ms)
        if not silent:
            await self.track_sounds()

    async def plays_on(self, seconds: float) -> None:
        """Time passes: a track that sounds sends a PLAYING frame every tick,
        its progress moving with the clock; a silent one sends nothing."""
        for _ in range(int(seconds / TICK_S)):
            await self.advance(TICK_S)
            if self.daemon.sounding:
                self.daemon.progress += int(TICK_S * 1000)
                await self.sends(status("PLAYING", self.daemon.progress))

    async def mac_pauses(self) -> None:
        self.daemon.state = "PAUSED"
        await self.sends(status("PAUSED", self.daemon.progress))

    async def mac_resumes(self, buffering_s: float = RESUME_GAP_S) -> None:
        """A track that sounded: BUFFERING, then BUFFERING and PLAYING
        `buffering_s` later (7 ms, measured). A silent one: BUFFERING and
        PLAYING in one burst."""
        self.daemon.state = "PLAYING"
        p = self.daemon.progress
        if not self.daemon.sounding:
            await self.sends(status("BUFFERING", p), status("PLAYING", p))
            return
        await self.sends(status("BUFFERING", p))
        await self.advance(buffering_s)
        await self.sends(status("BUFFERING", p), status("PLAYING", p))

    async def mac_skips_to(self, title: str, silent: bool = False) -> None:
        paused = self.daemon.state == "PAUSED"
        self.daemon.progress, self.daemon.sounding = 0, False
        if paused:
            await self.sends(status("IDLE"), status("IDLE"), media(title), status("PAUSED", 0, 224000))
            await self.advance(0.13)
            await self.sends(media(title, custom=True), status("PAUSED", 0, 224000))
            return
        await self.sends(status("IDLE"), status("IDLE"), media(title), status("BUFFERING", 0, 224000))
        await self.advance(0.12)
        await self.sends(media(title, custom=True), status("PAUSED", 0, 224000),
                         status("BUFFERING", 0, 224000), status("BUFFERING", 0, 224000),
                         status("PLAYING", 0, 224000))
        if not silent:
            await self.track_sounds()

    async def mac_leaves(self) -> None:
        self.daemon.state = "IDLE"
        await self.sends(
            {"command": "releaseResources"}, {"command": "notifySessionState", "state": 0},
            status("IDLE"), {"command": "notifyRequestResult", "requestId": 0, "resultCode": 1002},
        )

    async def playback_fails(self, code: int = 5) -> None:
        await self.sends({"command": "notifyPlaybackError", "errorCode": code})

    # === Milō ===

    async def advance(self, seconds: float) -> None:
        await self.clock.advance(seconds)

    async def select(self) -> None:
        await self.machine.transition_to_source(AudioSource.TIDAL)
        await settle()

    async def leave(self) -> None:
        await self.machine.transition_to_source(AudioSource.NONE)
        await settle()

    async def command(self, cmd: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        result = await self.source.command(cmd, data)
        await settle()
        return result

    async def reroute(self) -> None:
        async def apply_mode() -> None:
            return None
        await self.machine.reroute_active_source(apply_mode)
        await self.advance(1.1)
