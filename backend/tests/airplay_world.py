"""AirPlay's outside world, as measured on the unit (docs: source architecture,
phase 3a): shairport-sync 5.5.1 writing its metadata pipe, systemd holding the
daemon, and the daemon's D-Bus `DropSession`.

A scenario states what a sender does once, in the order shairport-sync was
measured to write it (2026-09-23, iPhone and Mac):

- iPhone Music opens a Buffered stream: `conn`, `snam`, `pbeg`, `pres`, then
  `pffr` and `styp Buffered` in the same millisecond. A pause is `paus`, a
  resume `pres` then `pffr`; a skip or a seek is `paus` then `pres` 160 ms
  later. A paused phone tears its stream down after 29-184 s (`pend`, no `disc`).
- A Mac's system audio (and Spotify on the iPhone) opens a Realtime stream:
  `pbeg`, then `pffr` + `styp Realtime`; a pause on the Mac says nothing, the
  stream goes on carrying silence. Spotify tears it down 1 s after a pause.
- Leaving while playing: `pend`, `disc` 110 ms later. A second sender taking
  over: the first one's `pend` + `disc`, then the newcomer's `conn` — or, as
  measured 2026-09-03, the goodbye a minute late.
- `DropSession`: `pend` if a stream is up, then `disc` at once.
- A track's cover (iPhone, 2026-09-25): the tags and the progress, then an
  empty picture — the sender withdrawing the cover on screen, written as a
  PICT of length 0 — and the new picture 89-126 ms later. The iPhone re-sends
  the same image, withdrawal first, up to three times inside one track.
- The daemon killed outright: no goodbye at all; systemd's Restart= brings up a
  new process, which announces nothing.

The daemon's death is observable two ways — /proc no longer carries the pid
(what a poll reads) and a pidfd turns readable (what a watch hears) — so the
same scenario holds whichever the source uses. Time is a VirtualClock the
scenario advances.
"""
import asyncio
import base64
import itertools
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.core import audio_source
from backend.core.models.audio_state import AudioSource
from backend.sources.airplay import source as airplay_module
from backend.sources.airplay.metadata_reader import MetadataReader
from backend.sources.airplay.source import AirPlaySource
from backend.tests.golden.harness import (
    AsyncioProxy, VirtualClock, WireReader, make_settings, make_state_machine, settle, use_virtual_wall,
)
from unittest.mock import AsyncMock, Mock

Item = Tuple[str, str, Optional[bytes]]

PHONE = "2a01:e0a:1048:b5b0:30a7:b5b1:3abc:a092"
MAC = "2a01:e0a:1048:b5b0:1452:d968:54ba:8b5f"
FIRST_PID = 4242


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


def bundle(rtptime: int, title: str, artist: str = "Ledeunff", album: str = "Soul") -> List[Item]:
    stamp = str(rtptime).encode()
    return [
        ssnc("mdst", stamp),
        ("core", "asar", artist.encode()),
        ("core", "minm", title.encode()),
        ("core", "asal", album.encode()),
        ssnc("mden", stamp),
    ]


def picture(rtptime: int, data: bytes) -> List[Item]:
    stamp = str(rtptime).encode()
    return [ssnc("pcst", stamp), ssnc("PICT", data), ssnc("pcen", stamp)]


def withdrawal(rtptime: int) -> List[Item]:
    """The sender's "no picture": an empty image body, which rtsp.c forwards
    as a PICT of length 0 between its pcst/pcen."""
    return picture(rtptime, b"")


def prgr(start: int, seconds_in: float, length_s: float) -> Item:
    current = start + int(44100 * seconds_in)
    end = start + int(44100 * length_s)
    return ssnc("prgr", f"{start}/{current}/{end}".encode())


PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x02X\x00\x00\x02X\x08\x02"
       b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00IEND\xaeB`\x82")




class AirPlayWorld(WireReader):
    """The AirPlay source on a real state machine, in a world the scenario drives."""

    def __init__(self, monkeypatch, tmp_path, settings: Optional[Dict[str, Any]] = None):
        world = self
        self.clock = VirtualClock()
        use_virtual_wall(monkeypatch, self.clock)
        self._pids = itertools.count(FIRST_PID)
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

        async def drop_session() -> bool:
            world.drops.append(world.clock.now)
            if not world.drop_answers:
                return False
            # As measured: the goodbye follows the reply at once.
            asyncio.ensure_future(world._dropped())
            return True

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
        monkeypatch.setattr(airplay_module, "drop_session", drop_session, raising=False)
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

    # === senders, as measured ===

    async def connects(self, ip: str = PHONE, name: str = "iPhone de Léo") -> None:
        await self.send(ssnc("conn", ip.encode()), ssnc("snam", name.encode()))

    async def music_plays(self, title: str = "Soul Officer", rtptime: int = 2534474610,
                          length_s: float = 240, cover: bool = True) -> None:
        """iPhone Music starts a track: the Buffered opening, tags, cover, progress."""
        await self.send(ssnc("pbeg"), ssnc("pres"))
        await self.advance(0.5)
        await self.send(ssnc("prsm"), ssnc("pffr", b"1/2"), ssnc("styp", b"Buffered"))
        await self.track(title, rtptime, length_s, cover)

    async def track(self, title: str, rtptime: int, length_s: float = 240, cover: bool = True) -> None:
        items = [*bundle(rtptime, title), prgr(rtptime, 0, length_s)]
        if not cover:
            await self.send(*items)
            return
        await self.send(*items, *withdrawal(rtptime))
        await self.advance(0.1)
        await self.send(*picture(rtptime, PNG))

    async def pauses(self) -> None:
        await self.send(ssnc("paus"))

    async def resumes(self) -> None:
        await self.send(ssnc("pres"), ssnc("prsm"))
        await self.advance(0.15)
        await self.send(ssnc("pffr", b"1/2"))

    async def skips(self, title: str = "L.A.D.Y", rtptime: int = 3571466929) -> None:
        await self.send(ssnc("paus"))
        await self.advance(0.16)
        await self.send(ssnc("pres"), ssnc("prsm"))
        await self.track(title, rtptime)
        await self.send(ssnc("pffr", b"1/2"))

    async def tears_the_stream_down(self) -> None:
        await self.send(ssnc("pend"))

    async def restarts_the_stream(self) -> None:
        """A paused phone resuming after its stream was torn down: a start."""
        await self.send(ssnc("pbeg"), ssnc("pres"))
        await self.advance(0.7)
        await self.send(ssnc("prsm"), ssnc("pffr", b"1/2"), ssnc("styp", b"Buffered"))

    async def system_audio_plays(self, title: str = "Energy", rtptime: int = 2115033544) -> None:
        """A Mac's system audio: a Realtime stream."""
        await self.send(ssnc("pbeg"), ssnc("flsr", b"1"), ssnc("prsm"))
        await self.advance(2.1)
        await self.send(ssnc("pffr", b"1/2"), ssnc("styp", b"Realtime"))
        await self.track(title, rtptime, cover=False)

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

    # === what the wire says ===
