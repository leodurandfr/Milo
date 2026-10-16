"""AirPlay's outside world, as measured on the unit (docs: source architecture,
phase 3a): shairport-sync writing its metadata pipe and publishing the sender's
item on D-Bus, systemd holding the daemon.

A scenario states what a sender does once, in the order shairport-sync was
measured to write it.

The pipe — the session (5.5.1, 2026-09-23, iPhone and Mac; unchanged in 5.6):

- iPhone Music opens a Buffered stream: `conn`, `snam`, `pbeg`, `pres`, then
  `pffr` and `styp Buffered` in the same millisecond. A pause is `paus`, a
  resume `pres` then `pffr`; a skip or a seek is `paus` then `pres` 160 ms
  later. A paused phone tears its stream down after 29-184 s (`pend`, no `disc`).
- A Mac (and Spotify on the iPhone) opens a Realtime stream: `pbeg`, then
  `pffr` + `styp Realtime`; a pause says nothing on the pipe, and the Mac's
  stream goes on carrying silence. Spotify tears it down 1 s after a pause.
- Leaving while playing: `pend`, `disc` 110 ms later. A second sender taking
  over: the first one's `pend` + `disc`, then the newcomer's `conn` — or, as
  measured 2026-09-03, the goodbye a minute late.
- `DropSession`: `pend` if a stream is up, then `disc` at once.
- The daemon killed outright: no goodbye at all; systemd's Restart= brings up a
  new process, which announces nothing.

D-Bus — the item (5.6, 2026-10-16, a Mac, iPhone Music, iPhone Spotify):

- A sender connecting clears the item and the commands (`{}`, `[]`), then
  lists its commands and publishes its item: first without its picture, then
  re-sent with the file shairport-sync wrote, 8-90 ms later.
- A pause: the Mac's and Spotify's item says PlaybackRate 0 at once; iPhone
  Music's says so only with the next update, at the resume, stamped at the
  pause.
- The modes: set by Milō, the sender echoes them back; the volume: set by
  Milō, shairport-sync writes it back itself, and the sender sends nothing.

The daemon's death is observable two ways — /proc no longer carries the pid
(what a poll reads) and a pidfd turns readable (what a watch hears) — so the
same scenario holds whichever the source uses. Time is a VirtualClock the
scenario advances.
"""
import asyncio
import base64
import itertools
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from PIL import Image

from backend.core import audio_source
from backend.core.models.audio_state import AudioSource
from backend.sources.airplay import source as airplay_module
from backend.sources.airplay.metadata_reader import MetadataReader
from backend.sources.airplay.remote import NowPlaying, RemoteUpdate
from backend.sources.airplay.source import AirPlaySource
from backend.tests.golden.harness import (
    EPOCH, AsyncioProxy, VirtualClock, WireReader, make_settings, make_state_machine, settle,
    use_virtual_wall,
)
from unittest.mock import AsyncMock, Mock

Item = Tuple[str, str, Optional[bytes]]

PHONE = "2a01:e0a:1048:b5b0:30a7:b5b1:3abc:a092"
MAC = "2a01:e0a:1048:b5b0:1452:d968:54ba:8b5f"
FIRST_PID = 4242

# The commands each sender listed (CommandInformation, enabled ones only).
IPHONE_MUSIC_COMMANDS = frozenset({0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 21, 22, 24, 25, 26,
                                   122, 125, 127, 128, 129, 130, 131, 132, 134, 135, 145, 149, 25021})
MAC_COMMANDS = frozenset({0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 17, 18, 19, 21, 22, 24, 25, 26,
                          122, 125, 129, 130, 131, 132, 134, 135, 149, 25021})
SPOTIFY_COMMANDS = frozenset({0, 1, 2, 3, 4, 5, 8, 9, 10, 11, 24, 25, 26})



def png(size: int = 600, color: str = "navy") -> bytes:
    """A real cover, so the dimension decode on the way in is real too."""
    buf = BytesIO()
    Image.new("RGB", (size, size), color).save(buf, format="PNG")
    return buf.getvalue()


PNG = png()


def ssnc(code: str, payload: Optional[bytes] = None) -> Item:
    return ("ssnc", code, payload)


def _xml(item: Item) -> str:
    kind, code, payload = item
    head = (
        f"<item><type>{kind.encode().hex()}</type><code>{code.encode().hex()}</code>"
        f"<length>{len(payload) if payload else 0}</length>"
    )
    if not payload:
        return head + "</item>"
    return head + f'<data encoding="base64">{base64.b64encode(payload).decode()}</data></item>'


class FakeRemote:
    """shairport-sync's D-Bus object (remote.ShairportRemote), as measured on 5.6."""

    def __init__(self, world: "AirPlayWorld", on_update: Callable[[RemoteUpdate], None]) -> None:
        self.world = world
        self.on_update = on_update

    async def start(self) -> None:
        if self.world.remote_unreachable:
            raise OSError("no system bus")
        self.world.remote = self
        # The GetAll a start reads: what the daemon holds already.
        for update in self.world.dbus_state():
            self.on_update(update)

    async def stop(self) -> None:
        if self.world.remote is self:
            self.world.remote = None

    async def send(self, member: str) -> bool:
        self.world.requests.append(member)
        return self.world.remote_answers

    async def set_shuffle(self, shuffle: bool) -> bool:
        self.world.requests.append(("shuffle", shuffle))
        if self.world.remote_answers:
            self.world.later(RemoteUpdate("shuffle", shuffle))    # the sender's echo
        return self.world.remote_answers

    async def set_repeat(self, mode: str) -> bool:
        self.world.requests.append(("repeat", mode))
        if self.world.remote_answers:
            self.world.later(RemoteUpdate("repeat", mode))
        return self.world.remote_answers

    async def set_volume(self, airplay_db: float) -> bool:
        self.world.requests.append(("volume", airplay_db))
        self.world.later(RemoteUpdate("volume", airplay_db))      # shairport-sync writes it
        return True

    async def drop_session(self) -> bool:
        world = self.world
        world.drops.append(world.clock.now)
        if not world.drop_answers:
            return False
        # As measured: the goodbye follows the reply at once.
        asyncio.ensure_future(world._dropped())
        return True


class AirPlayWorld(WireReader):
    """The AirPlay source on a real state machine, in a world the scenario drives."""

    def __init__(self, monkeypatch, tmp_path, settings: Optional[Dict[str, Any]] = None):
        world = self
        self.clock = VirtualClock()
        use_virtual_wall(monkeypatch, self.clock)
        self._pids = itertools.count(FIRST_PID)
        self._tracks = itertools.count(1)
        self.live_pids: set = set()
        self.pid: Optional[int] = None
        self.watches: List[Tuple[int, Callable[[], None]]] = []
        self.drops: List[float] = []
        self.drop_answers = True     # the daemon answers DropSession
        self.restarts: List[float] = []
        self.restart_ok = True
        self.stream_up = False       # a stream is flowing (for DropSession's `pend`)
        self.sender: Optional[str] = None
        self.reader: Optional[MetadataReader] = None
        self.remote: Optional[FakeRemote] = None
        self.remote_unreachable = False
        self.remote_answers = True   # shairport-sync takes the requests
        self.requests: List[Any] = []
        self.covers = tmp_path / "coverart"
        self.covers.mkdir()
        # What the daemon's D-Bus object holds now.
        self.now_playing: Optional[NowPlaying] = None
        self.commands: frozenset = frozenset()
        self._held_pause: Optional[NowPlaying] = None   # iPhone Music's late report

        class PipeReader(MetadataReader):
            """The real parser; the FIFO is replaced by `send()`."""

            def __init__(self, *a: Any, **k: Any) -> None:
                super().__init__(*a, **k)
                world.reader = self

            async def _read_loop(self) -> None:
                await asyncio.Event().wait()

        class Watch:
            """A pidfd watch: fires when the world kills that pid."""

            def __init__(self, pid: int, on_exit: Callable[[], None], *a: Any, **k: Any) -> None:
                self.pid, self.on_exit = pid, on_exit
                world.watches.append((pid, on_exit))
                if pid not in world.live_pids:
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
        self.systemd = systemd       # for a scenario where systemd itself misbehaves

        async def sleep(delay: float, *a: Any, **k: Any) -> Any:
            if delay <= 0.5:                      # a unit's settle delay
                return await asyncio.sleep(0)
            return await self.clock.sleep(delay)

        monkeypatch.setattr(airplay_module, "MetadataReader", PipeReader)
        monkeypatch.setattr(airplay_module, "ShairportRemote",
                            lambda on_update: FakeRemote(world, on_update))

        async def read_artwork(path: str) -> bytes:
            # In the actor's own step: a thread would finish outside settle().
            return Path(path).read_bytes()

        monkeypatch.setattr(airplay_module, "read_artwork", read_artwork)
        monkeypatch.setattr(audio_source, "asyncio", AsyncioProxy(sleep))
        monkeypatch.setattr(audio_source, "ProcessWatch", Watch, raising=False)

        self.machine, self.recorder = make_state_machine()
        self.source = AirPlaySource(
            {"metadata_pipe": str(tmp_path / "shairport-sync-metadata")},
            state_machine=self.machine,
            settings_service=make_settings(settings),
            systemd_manager=systemd,
        )
        self.machine.register_source(AudioSource.AIRPLAY, self.source)

    # === systemd ===

    def _spawn(self) -> None:
        self.pid = next(self._pids)
        self.unit_state = ("active", "success")
        self.live_pids.add(self.pid)

    def _die(self) -> None:
        """The daemon's process ends: /proc forgets it, its pidfds turn readable."""
        pid, self.pid = self.pid, None
        if pid is None:
            return
        self.live_pids.discard(pid)
        self.stream_up = False
        self.sender = None
        self.now_playing, self.commands = None, frozenset()
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
        if not self.restart_ok:
            return False
        self._die()
        self._spawn()
        return True

    # === what happens to the daemon ===

    async def kill_daemon(self) -> None:
        """SIGKILL: no goodbye. systemd's Restart= brings a new one up 5 s later."""
        self.unit_state = ("activating", "signal")
        self._die()
        await settle()

    async def systemd_restarts_it(self) -> None:
        self._spawn()
        await settle()

    async def _dropped(self) -> None:
        items = []
        if self.stream_up:
            items.append(ssnc("pend"))
        if self.sender is not None:
            items.append(ssnc("disc", self.sender.encode()))
        await self._write(*items)

    # === the pipe ===

    async def send(self, *items: Item) -> None:
        """shairport-sync writes these items to the metadata pipe, in order."""
        await self._write(*items)
        await settle()

    async def _write(self, *items: Item) -> None:
        for kind, code, payload in items:
            if code == "pbeg":
                self.stream_up = True
            elif code == "pend":
                self.stream_up = False
            elif code == "conn":
                self.sender = payload.decode() if payload else None
            elif code == "disc" and payload and payload.decode() == self.sender:
                self.sender = None
        if items and self.reader is not None:
            await self.reader._process_buffer("".join(_xml(i) for i in items).encode())

    # === D-Bus ===

    def dbus_state(self) -> List[RemoteUpdate]:
        return [RemoteUpdate("now_playing", self.now_playing), RemoteUpdate("commands", self.commands)]

    def later(self, update: RemoteUpdate) -> None:
        """A property signal the daemon emits after answering a call."""
        if self.remote is not None:
            asyncio.get_running_loop().call_soon(self.remote.on_update, update)

    async def signal(self, *updates: RemoteUpdate) -> None:
        """shairport-sync emits these PropertiesChanged, in order."""
        for update in updates:
            if update.kind == "now_playing":
                self.now_playing = update.value
            elif update.kind == "commands":
                self.commands = update.value
            if self.remote is not None:
                self.remote.on_update(update)
        await settle()

    def cover(self, data: bytes = PNG, name: Optional[str] = None) -> str:
        """A picture file, as shairport-sync writes one (named by its md5)."""
        path = self.covers / f"cover-{name or len(list(self.covers.iterdir()))}.png"
        path.write_bytes(data)
        return str(path)

    def item(self, title: str = "Soul Officer", artist: str = "Ledeunff",
             album: str = "My Storm - EP", length_s: float = 240, elapsed_s: float = 0.0,
             rate: float = 1.0, track: Optional[int] = None, artwork_id: Optional[str] = "art-1",
             artwork_path: Optional[str] = None) -> NowPlaying:
        return NowPlaying(
            title=title, artist=artist, album=album, duration_ms=int(length_s * 1000),
            elapsed_ms=int(elapsed_s * 1000), at=EPOCH + self.clock.now, rate=rate,
            track_id=track if track is not None else next(self._tracks),
            artwork_id=artwork_id, artwork_path=artwork_path,
        )

    def moved(self, item: NowPlaying, **changes: Any) -> NowPlaying:
        """The same item re-reported now: its playhead carried to the clock."""
        position = item.position_ms(EPOCH + self.clock.now)
        fields = {**item.__dict__, "elapsed_ms": position, "at": EPOCH + self.clock.now}
        fields.update(changes)
        return NowPlaying(**fields)

    async def publishes(self, item: NowPlaying, picture: Optional[str] = None) -> None:
        """The sender's item, replaced, then re-sent with its picture 20 ms later."""
        bare = NowPlaying(**{**item.__dict__, "artwork_path": None})
        await self.signal(RemoteUpdate("now_playing", bare))
        if picture is not None:
            await self.advance(0.02)
            await self.signal(RemoteUpdate("now_playing", NowPlaying(**{**item.__dict__, "artwork_path": picture})))

    # === senders, as measured ===

    async def connects(self, ip: str = PHONE, name: str = "iPhone de Léo",
                       commands: frozenset = IPHONE_MUSIC_COMMANDS) -> None:
        await self.signal(RemoteUpdate("now_playing", None), RemoteUpdate("commands", frozenset()))
        await self.send(ssnc("conn", ip.encode()), ssnc("snam", name.encode()))
        await self.signal(RemoteUpdate("commands", commands))

    async def music_plays(self, title: str = "Soul Officer", length_s: float = 240,
                          cover: bool = True) -> None:
        """iPhone Music starts a track: the Buffered opening, then its item."""
        await self.send(ssnc("pbeg"), ssnc("pres"))
        await self.advance(0.5)
        await self.send(ssnc("prsm"), ssnc("pffr", b"1/2"), ssnc("styp", b"Buffered"))
        await self.track(title, length_s, cover)

    async def track(self, title: str, length_s: float = 240, cover: bool = True,
                    artwork_id: Optional[str] = None) -> NowPlaying:
        item = self.item(title=title, length_s=length_s, artwork_id=artwork_id or f"art-{title}")
        await self.publishes(item, self.cover(name=title) if cover else None)
        return self.now_playing

    async def pauses(self) -> None:
        """iPhone Music: `paus` at once; the item's rate 0 waits for the resume.
        The scenario then reads the state once Milō believes it (PAUS_SETTLE_S)."""
        self._held_pause = self.moved(self.now_playing, rate=0.0) if self.now_playing else None
        await self.send(ssnc("paus"))
        await self.advance(airplay_module.PAUS_SETTLE_S)

    async def resumes(self) -> None:
        await self.send(ssnc("pres"), ssnc("prsm"))
        await self.advance(0.15)
        await self.send(ssnc("pffr", b"1/2"))
        held, self._held_pause = self._held_pause, None
        if held is not None:
            await self.signal(RemoteUpdate("now_playing", held))
            await self.signal(RemoteUpdate("now_playing", NowPlaying(
                **{**held.__dict__, "rate": 1.0, "at": EPOCH + self.clock.now})))

    async def skips(self, title: str = "L.A.D.Y") -> None:
        await self.send(ssnc("paus"))
        await self.advance(0.16)
        await self.send(ssnc("pres"), ssnc("prsm"))
        await self.track(title)
        await self.send(ssnc("pffr", b"1/2"))

    async def tears_the_stream_down(self) -> None:
        await self.send(ssnc("pend"))

    async def restarts_the_stream(self) -> None:
        """A paused phone resuming after its stream was torn down: a start."""
        await self.send(ssnc("pbeg"), ssnc("pres"))
        await self.advance(0.7)
        await self.send(ssnc("prsm"), ssnc("pffr", b"1/2"), ssnc("styp", b"Buffered"))

    async def realtime_plays(self, title: Optional[str] = "Energy") -> None:
        """A Mac or Spotify: a Realtime stream, and its item unless `title` is None
        (a Mac's system audio with nothing that publishes one)."""
        await self.send(ssnc("pbeg"), ssnc("flsr", b"1"), ssnc("prsm"))
        await self.advance(2.1)
        await self.send(ssnc("pffr", b"1/2"), ssnc("styp", b"Realtime"))
        if title is not None:
            await self.track(title)

    async def realtime_pauses(self) -> None:
        """A Mac or Spotify: the item says rate 0 at once, the pipe nothing."""
        await self.signal(RemoteUpdate("now_playing", self.moved(self.now_playing, rate=0.0)))

    async def realtime_resumes(self) -> None:
        await self.signal(RemoteUpdate("now_playing", self.moved(self.now_playing, rate=1.0)))

    async def moves_its_slider(self, airplay_db: float) -> None:
        await self.signal(RemoteUpdate("volume", airplay_db))

    async def leaves(self, ip: Optional[str] = None) -> None:
        await self.send(ssnc("pend"))
        await self.advance(0.11)
        await self.send(ssnc("disc", (ip or self.sender or PHONE).encode()))

    async def says_goodbye(self, ip: str) -> None:
        await self.send(ssnc("disc", ip.encode()))

    # === Milō ===

    async def advance(self, seconds: float) -> None:
        await self.clock.advance(seconds)

    async def select(self) -> None:
        await self.machine.transition_to_source(AudioSource.AIRPLAY)
        await settle()

    async def leave(self) -> None:
        await self.machine.transition_to_source(AudioSource.NONE)
        await settle()

    async def reroute(self) -> None:
        async def apply_mode() -> None:
            return None
        await self.machine.reroute_active_source(apply_mode)
        await settle()

    async def command(self, cmd: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        result = await self.source.command(cmd, params or {})
        await settle()
        return result

    # === what the wire says ===

    def controls(self) -> List[str]:
        return self.state()["controls"]

    def details(self) -> Dict[str, Any]:
        return self.state()["details"] or {}
