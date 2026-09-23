"""AirPlay's old wire, scenario by scenario (see harness.py for the rules).

A stimulus is what shairport-sync writes to its metadata pipe — one
(type, code, payload) item each, in the vocabulary of metadata_reader.py — plus
the two things systemd knows about the daemon: its main pid, and whether that
process is still alive.
"""
import asyncio
import base64
import struct
import zlib
from typing import Optional, Tuple

import pytest

from backend.core import audio_source
from backend.core.models.audio_state import AudioSource
from backend.sources.airplay import source as airplay_module
from backend.sources.airplay.metadata_reader import MetadataReader
from backend.sources.airplay.source import AirPlaySource
from backend.tests.golden.harness import (
    AsyncioProxy, VirtualClock, Wire, check_recording, make_settings,
    make_state_machine, make_systemd, settle,
)

Item = Tuple[str, str, Optional[bytes]]

PHONE = "192.168.1.20"
LAPTOP = "192.168.1.31"
DAEMON_PID = 4242
RESTARTED_PID = 4343


def _png(width: int, height: int, tag: bytes) -> bytes:
    """A cover as the sender pushes it: a PNG whose header carries its size.

    Built by hand rather than encoded by Pillow, so the bytes — and the md5 the
    artwork URL carries onto the wire — cannot move with a library upgrade.
    """
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"tEXt", b"Comment\x00" + tag)
        + chunk(b"IEND", b"")
    )


COVER_A = _png(1000, 1000, b"all melody")
COVER_C = _png(600, 600, b"says")
FAVICON = _png(64, 64, b"browser")

RTP_A = 3222108659
RTP_B = RTP_A + 44100 * 240          # the next track, four minutes on
RTP_C = RTP_B + 44100 * 200
RTP_LAPTOP = 17_000_000              # another sender, another RTP clock
TRACK_START = 1_000_000              # prgr frames: where the track began


# === What shairport-sync writes (one metadata item each) ===

def ssnc(code: str, payload: Optional[bytes] = None) -> Item:
    return ("ssnc", code, payload)


def conn(ip: str) -> Item:
    return ssnc("conn", ip.encode())


def disc(ip: str) -> Item:
    return ssnc("disc", ip.encode())


def snam(name: str) -> Item:
    return ssnc("snam", name.encode())


def bundle(rtptime: int, title: str, artist: str, album: str) -> Tuple[Item, ...]:
    stamp = str(rtptime).encode()
    return (
        ssnc("mdst", stamp),
        ("core", "minm", title.encode()),
        ("core", "asar", artist.encode()),
        ("core", "asal", album.encode()),
        ssnc("mden", stamp),
    )


def picture(rtptime: int, data: bytes) -> Tuple[Item, ...]:
    stamp = str(rtptime).encode()
    return (ssnc("pcst", stamp), ssnc("PICT", data), ssnc("pcen", stamp))


def prgr(seconds_in: int, length_s: int) -> Item:
    current = TRACK_START + 44100 * seconds_in
    end = TRACK_START + 44100 * length_s
    return ssnc("prgr", f"{TRACK_START}/{current}/{end}".encode())


def _xml(item: Item) -> str:
    item_type, code, payload = item
    head = (
        f"<item><type>{item_type.encode().hex()}</type>"
        f"<code>{code.encode().hex()}</code>"
        f"<length>{len(payload) if payload else 0}</length>"
    )
    if not payload:
        return head + "</item>"
    return head + f'<data encoding="base64">{base64.b64encode(payload).decode()}</data></item>'


# === The adapter ===

class _LoopView:
    """The running loop, its `time()` answered from the scenario's clock: the
    source ages the `prgr` snapshot by loop time, and a real clock would put
    wall-clock jitter into every published position."""

    def __init__(self, clock: VirtualClock) -> None:
        self._clock = clock

    def time(self) -> float:
        return self._clock.now

    def __getattr__(self, name: str):
        return getattr(asyncio.get_running_loop(), name)


class AirPlay:
    """Adapter: how each outside-world stimulus reaches AirPlaySource today.

    Phase 3a: the source's timers (the position ticker, the cover hold, the
    idle timeout) are the base's named timers on one virtual clock; the
    daemon's death reaches it through a pidfd watch; the idle timeout asks the
    daemon for the end over D-Bus (DropSession), which answers with a `disc`.
    """

    def __init__(self, monkeypatch, tmp_path, settings=None):
        self.clock = VirtualClock()
        self.live_pids = {DAEMON_PID}
        self.daemon_pid = DAEMON_PID
        self.watches: list = []
        self.sender: Optional[str] = None
        self.reader: Optional[MetadataReader] = None
        adapter = self

        class PipeReader(MetadataReader):
            """The real parser; the FIFO transport is replaced by `pipe()`.

            The read loop is parked instead of opening the pipe: bytes arriving
            through a real FIFO land on an I/O callback whose timing `settle()`
            cannot see, while handing the same bytes to the parser is exact.
            """

            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                adapter.reader = self

            async def _read_loop(self) -> None:
                await asyncio.Event().wait()

        class Watch:
            """The pidfd watch: fires when the adapter kills that pid."""

            def __init__(self, pid, on_exit):
                self.on_exit = on_exit
                adapter.watches.append((pid, on_exit))

            def close(self):
                adapter.watches[:] = [w for w in adapter.watches if w[1] is not self.on_exit]

        async def drop_session() -> bool:
            # As measured on shairport-sync 5.5.1: the goodbye follows at once.
            if adapter.sender is not None:
                asyncio.ensure_future(adapter._write(disc(adapter.sender)))
            return True

        class SourceAsyncio(AsyncioProxy):
            def get_running_loop(self):
                return _LoopView(adapter.clock)

        async def sleep(delay: float, *a, **k):
            if delay <= 1.0:                     # a unit's settle delay
                return await asyncio.sleep(0)
            return await adapter.clock.sleep(delay)

        monkeypatch.setattr(airplay_module, "MetadataReader", PipeReader)
        monkeypatch.setattr(airplay_module, "asyncio", SourceAsyncio(sleep))
        monkeypatch.setattr(airplay_module, "drop_session", drop_session)
        monkeypatch.setattr(audio_source, "asyncio", AsyncioProxy(sleep))
        monkeypatch.setattr(audio_source, "ProcessWatch", Watch)
        systemd = make_systemd()
        systemd.main_pid.side_effect = lambda *_: self.daemon_pid
        self.machine, recorder = make_state_machine()
        self.wire = Wire(self.machine, recorder)
        self.source = AirPlaySource(
            {"metadata_pipe": str(tmp_path / "shairport-sync-metadata")},
            state_machine=self.machine,
            settings_service=make_settings(settings),
            systemd_manager=systemd,
        )
        self.machine.register_source(AudioSource.AIRPLAY, self.source)

    async def select(self):
        await self.machine.transition_to_source(AudioSource.AIRPLAY)
        await settle()

    async def deselect(self):
        await self.machine.transition_to_source(AudioSource.NONE)
        await settle()

    async def _write(self, *items: Item):
        for _, code, payload in items:
            if code == "conn":
                self.sender = payload.decode()
            elif code == "disc" and payload and payload.decode() == self.sender:
                self.sender = None
        await self.reader._process_buffer("".join(_xml(i) for i in items).encode())

    async def pipe(self, *items: Item):
        """shairport-sync writes these items to the metadata pipe, in order."""
        await self._write(*items)
        await settle()

    async def seconds_pass(self, ticks: int = 1):
        """Ten seconds go by, `ticks` times: the position ticker runs once each."""
        for _ in range(ticks):
            await self.clock.advance(airplay_module.POSITION_TICK_SECONDS)

    async def cover_hold_runs_out(self):
        await self.clock.advance(airplay_module.ARTWORK_SETTLE_SECONDS)

    def daemon_killed_and_restarted(self):
        """shairport-sync is killed outright; systemd's Restart= brings up a new one."""
        dead = self.daemon_pid
        self.live_pids.discard(dead)
        self.daemon_pid = RESTARTED_PID
        self.live_pids.add(RESTARTED_PID)
        self.sender = None
        for pid, on_exit in list(self.watches):
            if pid == dead:
                on_exit()


@pytest.fixture
def airplay(monkeypatch, tmp_path):
    return AirPlay(monkeypatch, tmp_path)


async def _phone_starts_track_a(ap: AirPlay):
    await ap.pipe(conn(PHONE))
    await ap.pipe(snam("iPhone de Léo"))
    await ap.pipe(ssnc("pbeg"))
    await ap.pipe(*bundle(RTP_A, "All Melody", "Nils Frahm", "All Melody"))
    await ap.pipe(*picture(RTP_A + 1056, COVER_A))
    await ap.pipe(prgr(0, 253))


async def test_select_and_leave(airplay):
    await airplay.select()
    await airplay.wire.snapshot_rest()
    await airplay.deselect()
    check_recording("airplay", "select_and_leave", airplay.wire)


async def test_sender_plays_a_track_with_cover(airplay):
    await airplay.select()
    await _phone_starts_track_a(airplay)
    await airplay.wire.snapshot_rest()
    await airplay.seconds_pass(2)
    await airplay.pipe(prgr(20, 253))            # confirms the interpolation: no push
    await airplay.pipe(prgr(95, 253))            # a seek on the phone: pushed at once
    await airplay.wire.snapshot_rest()
    await airplay.deselect()
    check_recording("airplay", "sender_plays_a_track_with_cover", airplay.wire)


async def test_sender_pauses_and_resumes(airplay):
    await airplay.select()
    await _phone_starts_track_a(airplay)
    await airplay.seconds_pass()
    await airplay.pipe(ssnc("pfls"))
    await airplay.wire.snapshot_rest()
    await airplay.seconds_pass()                 # paused: the ticker stays quiet
    await airplay.pipe(ssnc("prsm"))
    await airplay.seconds_pass()
    await airplay.wire.snapshot_rest()
    await airplay.deselect()
    check_recording("airplay", "sender_pauses_and_resumes", airplay.wire)


async def test_track_changes_and_the_cover_follows(airplay):
    await airplay.select()
    await _phone_starts_track_a(airplay)
    # Track B: its tags arrive, its picture never does — the held cover
    # outlives the pairing only until the hold runs out.
    await airplay.pipe(*bundle(RTP_B, "Sunson", "Nils Frahm", "All Melody"))
    await airplay.pipe(prgr(0, 200))
    await airplay.wire.snapshot_rest()
    await airplay.cover_hold_runs_out()
    await airplay.wire.snapshot_rest()
    # Track C: the picture lands first, then its own tags.
    await airplay.pipe(*picture(RTP_C - 1408, COVER_C))
    await airplay.pipe(*bundle(RTP_C, "Says", "Nils Frahm", "Spaces"))
    await airplay.pipe(prgr(0, 498))
    await airplay.seconds_pass()
    # The same track re-sent without an album tag amends rather than replaces.
    await airplay.pipe(ssnc("mdst", str(RTP_C).encode()),
                       ("core", "minm", b"Says (Live)"),
                       ssnc("mden", str(RTP_C).encode()))
    await airplay.wire.snapshot_rest()
    await airplay.deselect()
    check_recording("airplay", "track_changes_and_the_cover_follows", airplay.wire)


async def test_sender_stops_and_disconnects(airplay):
    await airplay.select()
    await _phone_starts_track_a(airplay)
    await airplay.pipe(ssnc("pend"))
    await airplay.wire.snapshot_rest()
    await airplay.pipe(disc(LAPTOP))             # a late goodbye from someone else: ignored
    await airplay.pipe(disc(PHONE))
    await airplay.wire.snapshot_rest()
    # A browser tab with only a favicon for a cover takes over afterwards.
    await airplay.pipe(conn(LAPTOP))
    await airplay.pipe(ssnc("pbeg"))
    await airplay.pipe(*bundle(RTP_LAPTOP, "Lecture", "YouTube", ""))
    await airplay.pipe(*picture(RTP_LAPTOP, FAVICON))
    await airplay.wire.snapshot_rest()
    await airplay.deselect()
    check_recording("airplay", "sender_stops_and_disconnects", airplay.wire)


async def test_pause_auto_stops(monkeypatch, tmp_path):
    airplay = AirPlay(monkeypatch, tmp_path, settings={"audio.auto_stop_delay": 1})
    await airplay.select()
    await _phone_starts_track_a(airplay)
    await airplay.pipe(ssnc("pfls"))             # arms the 1 s timer, which restarts the daemon
    await airplay.wire.snapshot_rest()
    await airplay.seconds_pass()                 # the restarted source's ticker runs idle
    await airplay.deselect()
    check_recording("airplay", "pause_auto_stops", airplay.wire)


async def test_daemon_dies_under_the_session(airplay):
    await airplay.select()
    await _phone_starts_track_a(airplay)
    await airplay.seconds_pass()
    airplay.daemon_killed_and_restarted()
    await airplay.seconds_pass()                 # the ticker finds the pid gone
    await airplay.wire.snapshot_rest()
    await airplay.pipe(conn(PHONE))              # the sender reconnects to the new daemon
    await airplay.pipe(ssnc("pbeg"))
    await airplay.pipe(*bundle(RTP_B, "Sunson", "Nils Frahm", "All Melody"))
    await airplay.wire.snapshot_rest()
    await airplay.seconds_pass()
    await airplay.deselect()
    check_recording("airplay", "daemon_dies_under_the_session", airplay.wire)


# === Phase 3a: the sequences shairport-sync 5.5.1 was measured to write ===
#
# The scenarios above predate the measurement: their stream opens with `pbeg`
# alone, while every real one follows it with its first frame (`pffr`) and its
# type (`styp`) 0.5-2 s later. They are kept as they were recorded; these use
# what an iPhone (Music: Buffered) and a Mac (system audio: Realtime) sent.

async def _music_starts_track_a(ap: AirPlay):
    await ap.pipe(conn(PHONE), snam("iPhone de Léo"))
    await ap.pipe(ssnc("pbeg"), ssnc("pres"))
    await ap.pipe(ssnc("prsm"), ssnc("pffr", b"1/2"), ssnc("styp", b"Buffered"))
    await ap.pipe(*bundle(RTP_A, "All Melody", "Nils Frahm", "All Melody"))
    await ap.pipe(*picture(RTP_A + 1056, COVER_A))
    await ap.pipe(prgr(0, 253))


async def test_music_pauses_resumes_and_skips(airplay):
    await airplay.select()
    await _music_starts_track_a(airplay)
    await airplay.seconds_pass()
    await airplay.pipe(ssnc("paus"))
    await airplay.wire.snapshot_rest()
    await airplay.pipe(ssnc("pres"), ssnc("prsm"))
    await airplay.pipe(ssnc("pffr", b"1/2"))
    await airplay.seconds_pass()
    # A skip: paus, then pres 160 ms later, the new track's tags and cover.
    await airplay.pipe(ssnc("paus"))
    await airplay.pipe(ssnc("pres"), ssnc("prsm"))
    await airplay.pipe(*bundle(RTP_B, "Sunson", "Nils Frahm", "All Melody"),
                       *picture(RTP_B, COVER_C), prgr(0, 200), ssnc("pffr", b"1/2"))
    await airplay.wire.snapshot_rest()
    await airplay.deselect()
    check_recording("airplay", "music_pauses_resumes_and_skips", airplay.wire)


async def test_a_music_pause_is_ended_by_the_daemon(monkeypatch, tmp_path):
    airplay = AirPlay(monkeypatch, tmp_path, settings={"audio.auto_stop_delay": 30})
    await airplay.select()
    await _music_starts_track_a(airplay)
    await airplay.pipe(ssnc("paus"))
    await airplay.seconds_pass()
    await airplay.pipe(ssnc("pend"))             # the phone tears its stream down, connected
    await airplay.wire.snapshot_rest()
    await airplay.seconds_pass(2)                # the 30 s timeout asks for DropSession
    await airplay.wire.snapshot_rest()
    await airplay.deselect()
    check_recording("airplay", "a_music_pause_is_ended_by_the_daemon", airplay.wire)


async def test_system_audio_is_connected(airplay):
    await airplay.select()
    await airplay.pipe(conn(LAPTOP), snam("Mac mini de Léo"))
    await airplay.pipe(ssnc("pbeg"), ssnc("flsr", b"1"), ssnc("prsm"))
    await airplay.pipe(ssnc("pffr", b"1/2"), ssnc("styp", b"Realtime"))
    await airplay.pipe(*bundle(RTP_LAPTOP, "Energy", "Jean du Voyage", ""), prgr(0, 208))
    await airplay.seconds_pass()
    await airplay.wire.snapshot_rest()
    await airplay.pipe(ssnc("pend"))
    await airplay.pipe(disc(LAPTOP))
    await airplay.wire.snapshot_rest()
    await airplay.deselect()
    check_recording("airplay", "system_audio_is_connected", airplay.wire)


async def test_a_newcomer_before_the_late_goodbye(airplay):
    await airplay.select()
    await _music_starts_track_a(airplay)
    await airplay.pipe(conn(LAPTOP), snam("Mac mini de Léo"))
    await airplay.wire.snapshot_rest()
    await airplay.pipe(disc(PHONE))              # a minute late, as measured 2026-09-03
    await airplay.pipe(ssnc("pbeg"), ssnc("prsm"))
    await airplay.pipe(ssnc("pffr", b"1/2"), ssnc("styp", b"Realtime"))
    await airplay.wire.snapshot_rest()
    await airplay.deselect()
    check_recording("airplay", "a_newcomer_before_the_late_goodbye", airplay.wire)
