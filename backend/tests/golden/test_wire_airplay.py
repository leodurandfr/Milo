"""AirPlay's wire, scenario by scenario (see harness.py for the rules).

A stimulus is what shairport-sync emits: an item on its metadata pipe (the
session) or a property on its D-Bus object (the sender's item, commands and
modes), as tests/airplay_world.py models them from the 5.5.1 and 5.6
measurements — plus what systemd knows about the daemon: its main pid, and
whether that process is still alive.
"""
import struct
import zlib

import pytest

from backend.sources.airplay.remote import RemoteUpdate
from backend.tests.airplay_world import (
    IPHONE_MUSIC_COMMANDS, MAC, MAC_COMMANDS, PHONE, SPOTIFY_COMMANDS, AirPlayWorld, ssnc,
)
from backend.tests.golden.harness import Wire, check_recording
from backend.sources.airplay.source import ARTWORK_SETTLE_SECONDS

TICK_SECONDS = 10.0                  # one step of `seconds_pass`


def _png(width: int, height: int, tag: bytes) -> bytes:
    """A cover as the sender's picture file: a PNG whose header carries its size.

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


class AirPlay(AirPlayWorld):
    """The scenario world, recording its wire."""

    def __init__(self, monkeypatch, tmp_path, settings=None):
        super().__init__(monkeypatch, tmp_path, settings)
        self.wire = Wire(self.machine, self.recorder)

    async def seconds_pass(self, ticks: int = 1):
        """Ten seconds go by, `ticks` times (the old position ticker's period)."""
        for _ in range(ticks):
            await self.advance(TICK_SECONDS)

    async def plays(self, title: str, artist: str, album: str, length_s: float,
                    cover: bytes = None, artwork_id: str = None, elapsed_s: float = 0.0):
        item = self.item(title=title, artist=artist, album=album, length_s=length_s,
                         elapsed_s=elapsed_s, artwork_id=artwork_id or f"art-{title}")
        await self.publishes(item, self.cover(cover, title) if cover is not None else None)
        return self.now_playing


@pytest.fixture
def airplay(monkeypatch, tmp_path):
    return AirPlay(monkeypatch, tmp_path)


async def _music_starts_track_a(ap: AirPlay):
    await ap.connects(PHONE, "iPhone de Léo", IPHONE_MUSIC_COMMANDS)
    await ap.send(ssnc("pbeg"), ssnc("pres"))
    await ap.send(ssnc("prsm"), ssnc("pffr", b"1/2"), ssnc("styp", b"Buffered"))
    return await ap.plays("All Melody", "Nils Frahm", "All Melody", 253, COVER_A)


async def test_select_and_leave(airplay):
    await airplay.select()
    await airplay.wire.snapshot_rest()
    await airplay.leave()
    check_recording("airplay", "select_and_leave", airplay.wire)


async def test_music_plays_a_track_with_cover(airplay):
    await airplay.select()
    item = await _music_starts_track_a(airplay)
    await airplay.wire.snapshot_rest()
    await airplay.seconds_pass(2)
    await airplay.signal(RemoteUpdate("now_playing", airplay.moved(item)))   # confirms: no push
    await airplay.signal(RemoteUpdate("now_playing", airplay.moved(item, elapsed_ms=95_000)))  # a seek
    await airplay.wire.snapshot_rest()
    await airplay.leave()
    check_recording("airplay", "music_plays_a_track_with_cover", airplay.wire)


async def test_music_pauses_resumes_and_skips(airplay):
    await airplay.select()
    await _music_starts_track_a(airplay)
    await airplay.seconds_pass()
    await airplay.pauses()
    await airplay.wire.snapshot_rest()
    await airplay.seconds_pass()
    await airplay.resumes()                      # with the late pause report, then rate 1
    await airplay.seconds_pass()
    # A skip: paus, then pres 160 ms later, the new track's item and cover.
    await airplay.send(ssnc("paus"))
    await airplay.send(ssnc("pres"), ssnc("prsm"))
    await airplay.plays("Sunson", "Nils Frahm", "All Melody", 200, COVER_C)
    await airplay.send(ssnc("pffr", b"1/2"))
    await airplay.wire.snapshot_rest()
    await airplay.leave()
    check_recording("airplay", "music_pauses_resumes_and_skips", airplay.wire)


async def test_track_changes_and_the_cover_follows(airplay):
    await airplay.select()
    await _music_starts_track_a(airplay)
    # Track B names a new picture that is never written: the cover is held,
    # then goes once the settle runs out.
    await airplay.signal(RemoteUpdate("now_playing", airplay.item(
        "Sunson", "Nils Frahm", "All Melody", 200, artwork_id="art-sunson")))
    await airplay.wire.snapshot_rest()
    await airplay.advance(ARTWORK_SETTLE_SECONDS)
    await airplay.wire.snapshot_rest()
    # Track C: its item, then its picture.
    await airplay.plays("Says", "Nils Frahm", "Spaces", 498, COVER_C)
    await airplay.seconds_pass()
    await airplay.wire.snapshot_rest()
    await airplay.leave()
    check_recording("airplay", "track_changes_and_the_cover_follows", airplay.wire)


async def test_sender_stops_and_disconnects(airplay):
    await airplay.select()
    await _music_starts_track_a(airplay)
    await airplay.send(ssnc("pend"))
    await airplay.wire.snapshot_rest()
    await airplay.says_goodbye(MAC)              # a late goodbye from someone else: ignored
    await airplay.says_goodbye(PHONE)
    await airplay.wire.snapshot_rest()
    # A browser tab with only a favicon for a cover takes over afterwards.
    await airplay.connects(MAC, "Mac mini de Léo", MAC_COMMANDS)
    await airplay.realtime_plays(title=None)
    await airplay.plays("Lecture", "YouTube", "", 600, FAVICON)
    await airplay.wire.snapshot_rest()
    await airplay.leave()
    check_recording("airplay", "sender_stops_and_disconnects", airplay.wire)


async def test_a_music_pause_is_ended_by_the_daemon(monkeypatch, tmp_path):
    airplay = AirPlay(monkeypatch, tmp_path, settings={"audio.auto_stop_delay": 30})
    await airplay.select()
    await _music_starts_track_a(airplay)
    await airplay.pauses()
    await airplay.seconds_pass()
    await airplay.tears_the_stream_down()        # the phone drops its stream, connected
    await airplay.wire.snapshot_rest()
    await airplay.seconds_pass(2)                # the 30 s timeout asks for DropSession
    await airplay.wire.snapshot_rest()
    await airplay.leave()
    check_recording("airplay", "a_music_pause_is_ended_by_the_daemon", airplay.wire)


async def test_daemon_dies_under_the_session(airplay):
    await airplay.select()
    await _music_starts_track_a(airplay)
    await airplay.seconds_pass()
    await airplay.kill_daemon()
    await airplay.systemd_restarts_it()
    await airplay.wire.snapshot_rest()
    await airplay.connects(PHONE, "iPhone de Léo", IPHONE_MUSIC_COMMANDS)   # to the new daemon
    await airplay.send(ssnc("pbeg"))
    await airplay.plays("Sunson", "Nils Frahm", "All Melody", 200)
    await airplay.wire.snapshot_rest()
    await airplay.seconds_pass()
    await airplay.leave()
    check_recording("airplay", "daemon_dies_under_the_session", airplay.wire)


async def test_a_mac_plays_and_pauses(airplay):
    """Realtime: no pause on the pipe; the item's rate is the phase."""
    await airplay.select()
    await airplay.connects(MAC, "Mac mini de Léo", MAC_COMMANDS)
    await airplay.realtime_plays(title=None)
    await airplay.plays("Energy", "Jean du Voyage", "Energy", 208, COVER_C)
    await airplay.seconds_pass()
    await airplay.wire.snapshot_rest()
    await airplay.realtime_pauses()
    await airplay.seconds_pass()
    await airplay.wire.snapshot_rest()
    await airplay.realtime_resumes()
    await airplay.leaves(MAC)
    await airplay.wire.snapshot_rest()
    await airplay.leave()
    check_recording("airplay", "a_mac_plays_and_pauses", airplay.wire)


async def test_system_audio_with_no_item_is_connected(airplay):
    await airplay.select()
    await airplay.connects(MAC, "Mac mini de Léo", frozenset())
    await airplay.realtime_plays(title=None)
    await airplay.seconds_pass()
    await airplay.wire.snapshot_rest()
    await airplay.leaves(MAC)
    await airplay.wire.snapshot_rest()
    await airplay.leave()
    check_recording("airplay", "system_audio_with_no_item_is_connected", airplay.wire)


async def test_a_newcomer_before_the_late_goodbye(airplay):
    await airplay.select()
    await _music_starts_track_a(airplay)
    await airplay.connects(MAC, "Mac mini de Léo", MAC_COMMANDS)
    await airplay.wire.snapshot_rest()
    await airplay.says_goodbye(PHONE)            # a minute late, as measured 2026-09-03
    await airplay.realtime_plays(title=None)
    await airplay.wire.snapshot_rest()
    await airplay.leave()
    check_recording("airplay", "a_newcomer_before_the_late_goodbye", airplay.wire)


async def test_spotify_takes_the_modes(airplay):
    """The commands Spotify lists, and its modes as it echoes them."""
    await airplay.select()
    await airplay.connects(PHONE, "iPhone de Léo", SPOTIFY_COMMANDS)
    await airplay.realtime_plays(title=None)
    await airplay.plays("oh my love", "FKA twigs", "CAPRISONGS", 222.6, COVER_C)
    await airplay.wire.snapshot_rest()
    await airplay.command("set_shuffle", {"shuffle": True})
    await airplay.command("set_repeat", {"mode": "context"})
    await airplay.wire.snapshot_rest()
    await airplay.leave()
    check_recording("airplay", "spotify_takes_the_modes", airplay.wire)
