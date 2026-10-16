# backend/tests/test_airplay_source.py
"""
The AirPlay source: the session it follows off the metadata pipe, the item,
cover, playhead, commands and volume it follows over shairport-sync's D-Bus
object, and the pipe reader that feeds it.

Everything here drives the source through the world shairport-sync was measured
to be (tests/airplay_world.py): the real `MetadataReader` parser, fed the items
the daemon writes, and a stand-in for its D-Bus object that emits what 5.6 was
measured to emit, behind the real `AirPlaySource` on a real state machine, on a
virtual clock. What is asserted is what reaches the wire — the state (`_shown`),
every state published, the `source/position` events, the artwork route — and
what was asked of the sender, never the source's own fields. The pure parser
and the read loop are driven through the real `MetadataReader` alone, with an
`on_event` collector.

What is being pinned first is the cover rule: the cover is the picture file of
the sender's item, held while the item re-sends itself without its path (most
updates do, for 8-90 ms) and replaced by the next item's, or removed when a new
item brings none within ARTWORK_SETTLE_SECONDS.

Danger specific to this file: `/tmp/shairport-sync-metadata` is the LIVE pipe's
path. shairport-sync is stopped whenever the AirPlay source is off, so a test
that reached `_ensure_metadata_pipe` unguarded would CREATE the service's pipe.
Every test here puts it under `tmp_path`, and `never_the_live_pipe` fails loudly
if one does not.
"""
import asyncio
import base64
import logging
import contextlib
import os
from typing import Any, Callable, List, Optional
from unittest.mock import AsyncMock, Mock

import pytest

from backend.core.models.volume import VolumeConfig
from backend.core.models.ws_events import SourceErrorReason
from backend.sources.airplay import source as airplay_module
from backend.sources.airplay.metadata_reader import MetadataReader, PipeEvent, _hex_to_str
from backend.sources.airplay.remote import RemoteUpdate
from backend.core.audio_source import POSITION_TOLERANCE_MS
from backend.sources.airplay.source import ARTWORK_SETTLE_SECONDS, VOLUME_HOLD_S
from backend.tests.airplay_world import (
    IPHONE_MUSIC_COMMANDS, MAC, MAC_COMMANDS, PHONE, SPOTIFY_COMMANDS, AirPlayWorld, png, ssnc,
)

LIVE_PIPE = "/tmp/shairport-sync-metadata"

# The old source re-published the playhead every 10 s; the spans below still
# cover several of those, so a ticker that came back would be heard.
OLD_TICK_SECONDS = 10


@pytest.fixture(autouse=True)
def never_the_live_pipe(monkeypatch):
    """Creating or opening the running service's pipe fails loudly."""
    real_mkfifo, real_open = os.mkfifo, os.open

    def mkfifo_(path, *a, **k):
        if str(path) == LIVE_PIPE:
            raise AssertionError("a test created the live shairport-sync pipe")
        return real_mkfifo(path, *a, **k)

    def open_(path, *a, **k):
        if str(path) == LIVE_PIPE:
            raise AssertionError("a test opened the live shairport-sync pipe")
        return real_open(path, *a, **k)

    monkeypatch.setattr(os, "mkfifo", mkfifo_)
    monkeypatch.setattr(os, "open", open_)


@pytest.fixture
async def world(monkeypatch, tmp_path):
    w = AirPlayWorld(monkeypatch, tmp_path)
    await w.select()
    yield w
    await w.source.shutdown()


# === What the daemon writes ===

def _item(item_type: str, code: str, payload: Optional[bytes] = None) -> str:
    """One metadata item in the wire shape the pipe carries.

    Type and code are hex-encoded ASCII, the payload base64 with its decoded
    length alongside — an item with no payload carries `<length>0</length>` and
    no data element at all.
    """
    head = (
        f"<item><type>{item_type.encode().hex()}</type>"
        f"<code>{code.encode().hex()}</code>"
        f"<length>{len(payload) if payload else 0}</length>"
    )
    if not payload:
        return head + "</item>"
    return head + f'<data encoding="base64">{base64.b64encode(payload).decode()}</data></item>'


# === Driving and reading ===

async def _on_air(world: AirPlayWorld, commands: frozenset = IPHONE_MUSIC_COMMANDS) -> None:
    """A phone connected and playing a Buffered stream, before any item."""
    await world.connects(PHONE, "iPhone de Léo", commands)
    await world.send(ssnc("pbeg"), ssnc("pres"), ssnc("pffr", b"1/2"), ssnc("styp", b"Buffered"))
    assert world.playing(), "the stream never reached PLAYING"


async def _plays(world: AirPlayWorld, title: str, cover: bool = True, **item: Any):
    """The sender's item, published as 5.6 publishes one (bare, then with its picture)."""
    published = world.item(title=title, **item)
    await world.publishes(published, world.cover(name=title) if cover else None)
    return world.now_playing


async def _after_the_hold(world: AirPlayWorld) -> None:
    """Let a withdrawal's settle run out, so what follows is the state it leaves."""
    await world.advance(ARTWORK_SETTLE_SECONDS + 1)


def _pushes(world: AirPlayWorld, since: int = 0) -> List[dict]:
    """The `source/position` events broadcast since envelope `since`."""
    return [
        e["data"] for e in world.recorder.envelopes[since:]
        if e["category"] == "source" and e["type"] == "position"
    ]


def _shown(state: dict) -> dict:
    """What a screen reads off one AirPlay state: the session's track and
    sender, and the cover's width from `details`. Every key is None (or [])
    without a session."""
    session = state["session"] or {}
    details = state["details"] or {}
    return {
        "title": session.get("title"),
        "artist": session.get("artist"),
        "album": session.get("album"),
        "artwork": session.get("artwork"),
        "artwork_width": details.get("artwork_width"),
        "senders": session.get("senders", []),
    }


def _rich(state: dict) -> bool:
    """`useRichDisplay`'s airplay arm: title AND artist AND a cover over 300 px."""
    m = _shown(state)
    return bool(m["title"]) and bool(m["artist"]) and (m["artwork_width"] or 0) > 300


class _Collector:
    """An `on_event` for the real reader: keeps what it was told, in order."""

    def __init__(self) -> None:
        self.events: List[PipeEvent] = []

    async def __call__(self, event: PipeEvent) -> None:
        self.events.append(event)

    def kinds(self) -> List[str]:
        return [e.kind for e in self.events]


class _Volume:
    """VolumeService as the source uses it: the level in dB over the real
    VolumeConfig, a state listener called after each broadcast, as the real one."""

    def __init__(self, volume_db: float = -50.0) -> None:
        self.volume_config = VolumeConfig()
        self.volume_db, self.muted = volume_db, False
        self.listeners: List[Callable[[float, bool], None]] = []
        self.sets: List[float] = []

    def add_state_listener(self, listener) -> None:
        self.listeners.append(listener)

    async def get_volume_state(self):
        return Mock(global_volume_db=self.volume_db, global_mute=self.muted)

    async def set_volume_db(self, volume_db: float, show_bar: bool = True) -> bool:
        self.sets.append(volume_db)
        self.volume_db = volume_db
        self._broadcast()
        return True

    def moved_in_milo(self, volume_db: float, muted: bool = False) -> None:
        """The knob, the slider, a remote: anything but the sender."""
        self.volume_db, self.muted = volume_db, muted
        self._broadcast()

    def _broadcast(self) -> None:
        for listener in self.listeners:
            listener(self.volume_config.normalize(self.volume_db), self.muted)


def _slider_volumes(world: AirPlayWorld) -> List[float]:
    """Every level Milō put the sender's slider at, in AirPlay dB."""
    return [r[1] for r in world.requests if isinstance(r, tuple) and r[0] == "volume"]


class TestTheItem:
    """The sender's item (`NowPlayingInformation`) is the whole current track:
    each update replaces the last."""

    async def test_the_item_is_the_track_on_screen(self, world):
        await _on_air(world)
        await _plays(world, "Steam", artist="Ledeunff", album="My Storm - EP", length_s=223.3)

        meta = _shown(world.state())
        assert (meta["title"], meta["artist"], meta["album"]) == ("Steam", "Ledeunff", "My Storm - EP")
        assert world.session()["duration_ms"] == 223_300

    async def test_a_new_item_owns_every_field(self, world):
        """No amendment rule any more: shairport-sync merges the sender's
        partial updates itself, so an item missing a field has no such field."""
        await _on_air(world)
        await _plays(world, "Steam", album="My Storm - EP")
        await _plays(world, "Plasma", album=None)

        assert _shown(world.state())["album"] is None

    async def test_the_empty_item_of_a_sender_arriving_keeps_what_is_on_screen(self, world):
        """`{}` is what every sender's connection starts with; read as an item,
        it blanked the track 0.9 s before the newcomer's own arrived."""
        await _on_air(world)
        await _plays(world, "Steam")

        await world.signal(RemoteUpdate("now_playing", None))

        assert _shown(world.state())["title"] == "Steam"

    async def test_an_item_with_no_session_opens_nothing(self, world):
        await world.publishes(world.item("Orphan"), world.cover())

        assert not world.active()

    async def test_an_item_that_arrived_before_its_session_is_shown_once_it_opens(self, world):
        """D-Bus and the pipe are two feeds: the item can land before `conn`."""
        await world.signal(RemoteUpdate("commands", IPHONE_MUSIC_COMMANDS))
        await world.publishes(world.item("Early"), world.cover())
        await world.send(ssnc("conn", PHONE.encode()))

        assert _shown(world.state())["title"] == "Early"
        assert _shown(world.state())["artwork"]


class TestTheCover:
    """The cover is the picture file of the item, held across the item's
    re-sends without it (measured 8-90 ms on most updates, 2026-10-16)."""

    async def test_a_track_and_its_cover_are_published_together(self, world):
        await _on_air(world)
        await _plays(world, "Steam")

        meta = _shown(world.state())
        assert meta["artwork"] and meta["artwork_width"] == 600
        assert _rich(world.state())

    async def test_the_item_re_sent_without_its_picture_keeps_the_cover(self, world):
        """The re-send without the path, as nearly every update does: dropping
        the cover on it would swap the player for the status card and back."""
        await _on_air(world)
        item = await _plays(world, "Steam")
        shown = _shown(world.state())["artwork"]
        published = len(world.published())

        await world.signal(RemoteUpdate("now_playing", world.moved(item, artwork_path=None)))
        await _after_the_hold(world)

        assert _shown(world.state())["artwork"] == shown
        assert all(_rich(s) for s in world.published()[published:])

    async def test_the_cover_is_held_while_the_next_one_is_in_flight(self, world):
        """A new track names a new picture before it is written: the old cover
        stays until it lands, and is replaced then."""
        await _on_air(world)
        await _plays(world, "Steam")
        first = _shown(world.state())["artwork"]
        published = len(world.published())

        await world.signal(RemoteUpdate("now_playing", world.item("Plasma", artwork_id="art-2")))
        assert _shown(world.state())["artwork"] == first
        await world.advance(1)
        await world.signal(RemoteUpdate("now_playing", world.item(
            "Plasma", artwork_id="art-2", artwork_path=world.cover(png(color="teal"), "plasma"))))

        assert _shown(world.state())["artwork"] not in (None, first)
        assert all(s["session"]["artwork"] for s in world.published()[published:])

    async def test_a_new_picture_that_never_comes_removes_the_cover(self, world):
        await _on_air(world)
        await _plays(world, "Steam")

        await world.signal(RemoteUpdate("now_playing", world.item("Plasma", artwork_id="art-2")))
        await world.advance(ARTWORK_SETTLE_SECONDS - 1)
        assert _shown(world.state())["artwork"]
        await world.advance(2)

        assert _shown(world.state())["artwork"] is None
        assert _shown(world.state())["artwork_width"] is None

    async def test_two_tracks_off_one_album_keep_their_cover(self, world):
        """The same picture written again (a Mac names the file by its md5,
        so the path is the same; an iPhone writes it anew): nothing to publish."""
        await _on_air(world)
        await _plays(world, "Steam")
        cover = _shown(world.state())["artwork"]

        await world.publishes(world.item("L.A.D.Y", artwork_id="art-2"), world.cover(name="again"))

        assert _shown(world.state())["artwork"] == cover

    async def test_a_track_that_sends_no_cover_shows_none(self, world):
        await _on_air(world)
        await _plays(world, "Energy", cover=False)

        assert _shown(world.state())["artwork"] is None
        assert world.source.get_artwork() is None

    async def test_a_picture_file_that_is_gone_is_no_cover(self, world, caplog):
        """shairport-sync deletes a track's picture once played: a path read
        too late is no cover, not an error that stops the feed."""
        await _on_air(world)
        with caplog.at_level(logging.WARNING, logger=world.source._logger.name):
            await world.publishes(world.item("Steam"), str(world.covers / "missing.png"))

        assert _shown(world.state())["title"] == "Steam"
        assert _shown(world.state())["artwork"] is None
        assert any("unreadable" in r.message for r in caplog.records)

    async def test_a_new_tracks_unreadable_picture_does_not_keep_the_old_cover(self, world):
        await _on_air(world)
        await _plays(world, "Steam")

        await world.publishes(world.item("Plasma", artwork_id="art-2"), str(world.covers / "gone.png"))
        await _after_the_hold(world)

        assert _shown(world.state())["artwork"] is None

    async def test_a_new_sender_starts_with_no_cover(self, world):
        await _on_air(world)
        await _plays(world, "Steam")

        await world.leaves(PHONE)
        await world.connects(MAC, "Mac mini de Léo", MAC_COMMANDS)

        assert _shown(world.state())["artwork"] is None
        assert world.source.get_artwork() is None


class TestThePhase:
    """The phase comes from the pipe's stream facts and the item's rate
    (see the source's docstring for the measurements)."""

    async def test_a_stream_is_loading_until_its_first_frame(self, world):
        """`pbeg` opens the stream; sound arrives at `pffr`, and `styp` says
        whether the stream will report its pauses. Published as playing before
        that, the screen and the lock screen ran ahead of silence."""
        await world.connects()
        await world.send(ssnc("pbeg"), ssnc("pres"))

        assert world.phase() == "loading"

        await world.send(ssnc("pffr", b"1/2"), ssnc("styp", b"Buffered"))

        assert world.phase() == "playing"

    async def test_a_flush_neither_pauses_nor_freezes_the_playhead(self, world):
        """E54. `pfls` (a flush) was read as a pause: it stopped the playback
        and froze the position under a sender that never paused, and armed the
        auto-stop behind it. It is not read: the session plays on, and its
        anchor keeps moving."""
        await _on_air(world)
        await _plays(world, "Says", elapsed_s=30, length_s=300)

        await world.send(ssnc("pfls", b"12345"))
        await world.advance(OLD_TICK_SECONDS)

        assert world.playing()
        assert world.position_ms() >= 30_000 + OLD_TICK_SECONDS * 1000 - 100, \
            "the playhead froze after a flush"

    async def test_play_end_is_not_a_disconnection(self, world):
        """`pend` means the stream ended; the sender is still there until `disc`.
        Treated as a disconnect, the session would end while the phone still
        has the output selected."""
        await _on_air(world)

        await world.tears_the_stream_down()

        assert world.active()
        assert _shown(world.state())["senders"] == ["iPhone de Léo"]
        assert world.phase() == "paused"

    async def test_an_item_at_rate_zero_is_a_pause_on_any_stream(self, world):
        await world.connects(MAC, "Mac mini de Léo", MAC_COMMANDS)
        await world.realtime_plays()
        assert world.phase() == "playing"

        await world.realtime_pauses()

        assert world.phase() == "paused"

    async def test_a_rate_zero_stamped_before_the_resume_is_history(self, world):
        """iPhone Music's pause report lands with its resume, stamped at the
        pause (measured: 3 s late, 11 ms before the rate-1 one)."""
        await _on_air(world)
        item = await _plays(world, "Steam")
        await world.pauses()
        await world.advance(3)
        stale = world.moved(item, rate=0.0, at=item.at + 1)

        await world.send(ssnc("pres"))
        await world.signal(RemoteUpdate("now_playing", stale))

        assert world.phase() == "playing"

    async def test_realtime_with_no_item_is_connected(self, world):
        await world.connects(MAC, "Mac mini de Léo", frozenset())
        await world.realtime_plays(title=None)

        assert world.phase() == "connected"


class TestThePlayhead:
    """The item's ElapsedTime at Timestamp, moving at PlaybackRate, is an
    anchor already: what is published is where it puts the playhead now."""

    async def test_the_item_becomes_a_position_and_a_duration_in_ms(self, world):
        await _on_air(world)
        await _plays(world, "Says", elapsed_s=30, length_s=300)

        # 20 ms on: the item's re-send with its picture, as measured.
        assert abs(world.position_ms() - 30_020) <= 5
        assert world.session()["duration_ms"] == 300_000

    async def test_an_item_stamped_earlier_is_carried_to_now(self, world):
        """The sender's report is about when it sent it."""
        await _on_air(world)
        item = world.item("Says", elapsed_s=30, length_s=300)
        await world.advance(2.5)

        await world.signal(RemoteUpdate("now_playing", item))

        assert abs(world.position_ms() - 32_500) <= 10

    async def test_the_position_moves_while_the_track_plays(self, world):
        await _on_air(world)
        await _plays(world, "Says", elapsed_s=30, length_s=300)

        await world.advance(OLD_TICK_SECONDS)

        assert abs(world.position_ms() - (30_020 + OLD_TICK_SECONDS * 1000)) <= 5

    async def test_a_report_that_only_confirms_the_playhead_is_not_broadcast(self, world):
        """Every item update carries the playhead; within the tolerance it is
        the same playhead, and a position event per update would be noise."""
        await _on_air(world)
        item = await _plays(world, "Says", elapsed_s=30, length_s=300)
        await world.advance(OLD_TICK_SECONDS)
        since = len(world.recorder.envelopes)

        drift = (POSITION_TOLERANCE_MS - 500) / 1000
        await world.signal(RemoteUpdate("now_playing", world.moved(item, elapsed_ms=int(
            (30 + OLD_TICK_SECONDS + drift) * 1000))))

        assert _pushes(world, since) == []

    async def test_a_seek_on_the_sender_is_broadcast_at_once(self, world):
        await _on_air(world)
        item = await _plays(world, "Says", elapsed_s=30, length_s=300)
        since = len(world.recorder.envelopes)

        await world.signal(RemoteUpdate("now_playing", world.moved(item, elapsed_ms=120_000)))

        assert world.position_ms() == 120_000
        assert _pushes(world, since), "a seek reached the wire only with the next state"

    async def test_a_new_track_starts_its_own_playhead(self, world):
        await _on_air(world)
        await _plays(world, "Says", elapsed_s=200, length_s=300)

        await _plays(world, "Next", elapsed_s=0.5, length_s=200)

        assert abs(world.position_ms() - 520) <= 5

    async def test_a_paused_playhead_stands_still(self, world):
        await world.connects(MAC, "Mac mini de Léo", MAC_COMMANDS)
        await world.realtime_plays()
        await world.advance(10)
        await world.realtime_pauses()
        stopped = world.position_ms()

        await world.advance(30)

        assert world.position_ms() == stopped

    async def test_a_resume_does_not_play_the_pause_into_the_playhead(self, world):
        """iPhone Music resumes with the item it had before its pause (its
        pause report comes after): carried forward, it put the playhead a whole
        pause ahead, then back, on every resume."""
        await _on_air(world)
        await _plays(world, "Says", elapsed_s=30, length_s=300)
        await world.advance(10)
        await world.pauses()
        stopped = world.position_ms()
        await world.advance(60)
        published = len(world.published())

        await world.resumes()

        positions = [s["session"]["position"]["ms"] for s in world.published()[published:]]
        assert positions and max(positions) <= stopped + 1000
        assert abs(world.position_ms() - stopped) <= 1000

    async def test_no_position_is_pushed_on_a_timer(self, world):
        """The old source re-published the playhead every 10 s."""
        await _on_air(world)
        await _plays(world, "Says", elapsed_s=30, length_s=300)
        since = len(world.recorder.envelopes)

        await world.advance(5 * OLD_TICK_SECONDS)

        assert _pushes(world, since) == []


class TestTheTransport:
    """The commands go to the sender; `controls` lists what it says it takes
    (CommandInformation), and the state follows what it then reports."""

    async def test_an_iphone_playing_lists_pause_steps_and_modes(self, world):
        await _on_air(world)
        await _plays(world, "Steam")

        assert world.controls() == ["pause", "next", "prev", "set_shuffle", "set_repeat"]

    async def test_a_paused_sender_lists_resume(self, world):
        await _on_air(world)
        await world.pauses()

        assert world.controls()[0] == "resume"

    async def test_spotify_lists_the_modes_by_their_set_commands(self, world):
        """Spotify lists 25/26 (set shuffle / set repeat), not 6/7."""
        await world.connects(PHONE, "iPhone de Léo", SPOTIFY_COMMANDS)
        await world.realtime_plays("oh my love")

        assert world.controls() == ["pause", "next", "prev", "set_shuffle", "set_repeat"]

    async def test_a_sender_that_lists_nothing_gets_no_buttons(self, world):
        await world.connects(MAC, "Mac mini de Léo", frozenset())
        await world.realtime_plays(title=None)

        assert world.controls() == []

    async def test_a_sender_that_lists_only_the_toggle_gets_both_sides(self, world):
        await _on_air(world, commands=frozenset({2}))
        assert world.controls() == ["pause"]
        await world.pauses()
        assert world.controls() == ["resume"]

    @pytest.mark.parametrize("cmd, member", [
        ("pause", "Pause"), ("resume", "Play"), ("next", "Next"), ("prev", "Previous"),
    ])
    async def test_each_transport_command_is_asked_of_the_sender(self, world, cmd, member):
        await _on_air(world)
        await _plays(world, "Steam")

        result = await world.command(cmd)

        assert result["success"] is True
        assert world.requests[-1] == member

    async def test_the_modes_are_asked_of_the_sender_and_shown_from_its_echo(self, world):
        await _on_air(world)
        await _plays(world, "Steam")

        await world.command("set_shuffle", {"shuffle": True})
        await world.command("set_repeat", {"mode": "track"})

        assert ("shuffle", True) in world.requests and ("repeat", "track") in world.requests
        assert world.details()["shuffle"] is True and world.details()["repeat"] == "track"

    async def test_the_modes_shown_are_the_senders_own(self, world):
        """A mode changed on the phone reaches Milō's buttons."""
        await _on_air(world)
        await world.signal(RemoteUpdate("shuffle", True), RemoteUpdate("repeat", "context"))

        assert world.details()["shuffle"] is True and world.details()["repeat"] == "context"

    async def test_a_toggle_only_sender_is_sent_the_toggle(self, world):
        """Pause and Play are commands 1 and 0: a sender that lists only 2 never
        said it takes them, and shairport-sync answers success either way."""
        await _on_air(world, commands=frozenset({2}))

        await world.command("pause")

        assert world.requests[-1] == "PlayPause"

    async def test_a_sender_leaving_takes_its_modes_with_it(self, world):
        """shairport-sync never writes the modes itself: the next sender's
        buttons showed the previous one's shuffle."""
        await _on_air(world)
        await world.signal(RemoteUpdate("shuffle", True), RemoteUpdate("repeat", "track"))

        await world.leaves(PHONE)
        await world.send(ssnc("conn", MAC.encode()))

        assert world.details()["shuffle"] is False and world.details()["repeat"] == "off"

    async def test_a_takeover_before_the_late_goodbye_shows_nothing_of_the_previous_sender(self, world):
        await _on_air(world)
        await _plays(world, "Steam")

        await world.send(ssnc("conn", MAC.encode()), ssnc("snam", b"Mac mini"))

        assert _shown(world.state())["title"] is None and world.controls() == []

    async def test_a_request_shairport_sync_refuses_is_a_failed_command(self, world):
        await _on_air(world)
        world.remote_answers = False

        result = await world.command("next")

        assert result["success"] is False

    async def test_a_command_with_no_session_is_refused_and_never_sent(self, world):
        result = await world.command("pause")

        assert result["success"] is False
        assert world.requests == []

    async def test_without_d_bus_the_sender_still_plays_with_no_controls(self, monkeypatch, tmp_path):
        """Fail open: D-Bus unavailable costs the item and the commands, not the audio."""
        world = AirPlayWorld(monkeypatch, tmp_path)
        world.remote_unreachable = True
        try:
            await world.select()
            await _on_air(world)
            assert world.playing() and world.controls() == []
            assert (await world.command("next"))["success"] is False
        finally:
            await world.source.shutdown()


class TestTheVolume:
    """One level on both sliders: the sender's moves Milō's, Milō's — whoever
    moved it — moves the sender's. CamillaDSP stays the only attenuation."""

    @pytest.fixture
    def volume(self, world):
        volume = _Volume(volume_db=-50.0)
        world.source.set_volume_service(volume)
        return volume

    async def test_a_sender_arriving_is_put_at_milos_level(self, world, volume):
        """The first frame: Milō's level wins, the sender's slider follows."""
        await _on_air(world)

        level = volume.volume_config.normalize(-50.0)
        assert _slider_volumes(world) == [pytest.approx(-30.0 * (1 - level))]
        assert volume.sets == []

    async def test_the_senders_own_level_on_arrival_is_ignored(self, world, volume):
        """A Mac reports a ramp to its own level as it connects (measured
        2026-10-16): taken as a move, it would set the whole house's volume
        to whatever the Mac last had."""
        await _on_air(world)
        await world.moves_its_slider(-5.0)

        assert volume.sets == []

    async def test_the_senders_slider_moves_milos_volume(self, world, volume):
        await _on_air(world)
        await world.advance(VOLUME_HOLD_S + 1)

        await world.moves_its_slider(-6.0)

        assert volume.sets == [pytest.approx(volume.volume_config.denormalize(0.8))]

    async def test_milos_move_comes_back_from_the_sender_without_an_echo(self, world, volume):
        """shairport-sync writes the level Milō set back on D-Bus; read as the
        sender's move, it would set Milō's volume to itself, and round."""
        await _on_air(world)
        await world.advance(VOLUME_HOLD_S + 1)

        volume.moved_in_milo(-30.0)
        await world.advance(0)

        assert volume.sets == []
        assert _slider_volumes(world)[-1] == pytest.approx(-30.0 * (1 - volume.volume_config.normalize(-30.0)))

    async def test_the_senders_move_is_not_sent_back_to_it(self, world, volume):
        await _on_air(world)
        await world.advance(VOLUME_HOLD_S + 1)
        sent = len(_slider_volumes(world))

        await world.moves_its_slider(-6.0)

        assert len(_slider_volumes(world)) == sent

    async def test_a_mute_in_milo_mutes_the_senders_slider(self, world, volume):
        await _on_air(world)

        volume.moved_in_milo(-50.0, muted=True)
        await world.advance(0)

        assert _slider_volumes(world)[-1] == -144.0

    async def test_milos_mute_written_back_leaves_the_volume_alone(self, world, volume):
        """shairport-sync writes the -144 Milō sent back on D-Bus: read as the
        sender's slider at 0, it put the whole house at the floor."""
        await _on_air(world)
        await world.advance(VOLUME_HOLD_S + 1)

        volume.moved_in_milo(-50.0, muted=True)
        await world.advance(0)

        assert _slider_volumes(world)[-1] == -144.0
        assert volume.sets == []

    async def test_a_knob_turn_moves_the_slider_once(self, world, volume):
        """A turn is dozens of volume states; the slider goes to the last."""
        await _on_air(world)
        sent = len(_slider_volumes(world))

        for volume_db in (-48.0, -46.0, -44.0, -42.0):
            volume.moved_in_milo(volume_db)
        await world.advance(0)

        level = volume.volume_config.normalize(-42.0)
        assert _slider_volumes(world)[sent:] == [pytest.approx(-30.0 * (1 - level))]

    async def test_no_sender_no_volume_traffic(self, world, volume):
        volume.moved_in_milo(-30.0)
        await world.advance(0)

        assert _slider_volumes(world) == []

class TestConnectionEvents:
    """`conn`/`disc` are AirPlay 2's own events, sent as soon as a client picks
    this output — before any audio flows — and when it lets go."""

    async def test_a_client_selecting_the_output_marks_it_connected(self, world):
        await world.send(ssnc("conn", PHONE.encode()))

        assert world.phase() == "connected", "selecting an output is not playing"

    async def test_a_connection_event_with_no_address_still_counts(self, world):
        await world.send(ssnc("conn"))

        assert world.active()

    async def test_a_disconnection_clears_the_session_completely(self, world):
        """Anything left behind is what the next sender inherits: the previous
        phone's track and cover on screen before it has sent its own."""
        await _on_air(world)
        await _plays(world, "Says")
        assert _shown(world.state())["title"] == "Says"
        assert world.source.get_artwork() is not None

        await world.says_goodbye(PHONE)

        assert world.session() is None
        assert world.state()["resume"] is None and world.state()["details"] is None
        assert world.source.get_artwork() is None

    async def test_a_late_disconnect_does_not_tear_down_the_sender_on_air(self, world):
        """A `disc` names a sender, and it can arrive after that sender is gone.

        Measured on the unit 2026-09-03: a phone let go at 19:12:13, the next
        sender was on air at 19:12:18, and the first one's `disc` landed at
        19:13:13 — a minute into someone else's session. Applied blind it
        emptied the live one: title, artist, cover and client name all cleared
        under playing audio. `snam` is sent once per session, so the name never
        came back and the card read a bare "AirPlay" instead of the sender for
        the rest of it.
        """
        await world.connects(PHONE, "iPhone de Léo")
        await world.says_goodbye(PHONE)
        await world.connects(MAC, "Mac mini de Léo")
        await world.send(ssnc("pbeg"), ssnc("pffr", b"1/2"), ssnc("styp", b"Buffered"))
        await _plays(world, "Says")

        await world.says_goodbye(PHONE)

        meta = _shown(world.state())
        assert meta["senders"] == ["Mac mini de Léo"]
        assert world.playing()
        assert meta["title"] == "Says"
        assert meta["artwork"]
        assert world.source.get_artwork() is not None

    async def test_a_disconnect_with_no_address_still_ends_the_session(self, world):
        """Nothing tells two senders apart then, so the teardown stands — the
        trade that keeps a sender shairport reports no address for from holding
        the source ACTIVE for ever."""
        await _on_air(world)
        await _plays(world, "Says", cover=False)

        await world.send(ssnc("disc"))

        assert world.session() is None
        assert world.state()["resume"] is None

    async def test_the_client_name_is_published_as_the_source_label(self, world):
        """`snam` is X-Apple-Client-Name; it is what the source bar shows
        instead of a bare "AirPlay"."""
        await world.send(ssnc("snam", "Mac mini de Léo".encode()))

        assert world.active()
        assert _shown(world.state())["senders"] == ["Mac mini de Léo"]



class TestTheDaemonDyingUnderTheSession:
    """A `disc` comes *from* shairport-sync, so a daemon killed outright never
    sends one — and `Restart=always` brings the unit back within seconds under
    a process that announces nothing about the session it never had.

    Measured on the unit 2026-09-22: SIGKILL at 17:20:51, restart at 17:20:57,
    audio back — and the source sat ACTIVE on "Pavilion", playing,
    and a playhead frozen at 396000/396000 while the ALSA loopback read
    `closed`. Permanently: IDLE_STATES excludes ACTIVE so the 12 h sweep never
    reclaims it, and nothing but a pause armed the auto-stop. It reached
    AudioPlayerFull and the iPhone lock screen. The session now belongs to the
    daemon's process (the base's pidfd watch).
    """

    async def test_a_dead_daemon_drops_the_session_and_says_so(self, world):
        """The track and the cover go with it: left on the wire they are what
        the lock screen keeps drawing over a daemon that no longer holds them."""
        await _on_air(world)
        await _plays(world, "Says")

        await world.kill_daemon()

        assert world.session() is None
        assert world.state()["resume"] is None and world.state()["details"] is None
        assert world.source.get_artwork() is None
        assert world.errors() == [SourceErrorReason.STREAM_DISCONNECTED]

    async def test_a_session_after_a_source_restart_is_watched_against_the_new_daemon(self, world):
        """A daemon kept across a source stop is a daemon that is dead when the
        source comes back — and its death would read as the *new* session's
        daemon having died, tearing down audible playback with a banner. And
        the other way round: the new session must be watched at all, or a kill
        of the new daemon would leave it ACTIVE for ever."""
        await _on_air(world)
        await world.leave()
        await world.select()

        await _on_air(world)
        await world.advance(3 * OLD_TICK_SECONDS)
        assert world.playing()
        assert world.errors() == []

        await world.kill_daemon()
        assert not world.active()
        assert world.errors() == [SourceErrorReason.STREAM_DISCONNECTED]

    async def test_a_session_that_never_saw_conn_is_still_watched(self, world):
        """`conn` is not guaranteed first, or at all.

        Two other messages open a session — the client name and a stream
        beginning (an item with no session is a leftover of one that ended). If only `conn` bound the session to its daemon, a session
        opened by either would never learn the daemon died.
        """
        await world.send(ssnc("pbeg"))
        await _plays(world, "Says", cover=False)
        assert world.active()

        await world.kill_daemon()

        assert not world.active()
        assert world.errors() == [SourceErrorReason.STREAM_DISCONNECTED]

    async def test_a_reconnect_takes_the_banner_down(self, world):
        """This source raises exactly one error, and it is the one above.

        Nothing else clears it: AirPlay had no `broadcast_error` at all before
        the guard, so the banner it raises had no answering event and would
        have sat over a sender that came back fine.
        """
        await _on_air(world)
        await world.kill_daemon()
        await world.systemd_restarts_it()

        await world.connects()

        types = [e["type"] for e in world.recorder.envelopes if e["category"] == "source"]
        assert "error" in types
        assert "error_cleared" in types[types.index("error"):], "the banner outlived its cause"

    async def test_a_living_daemon_is_left_alone(self, world):
        """The complement, and the one that matters most: a session on a healthy
        daemon runs through tick after tick, so a guard that fired wrongly would
        tear it down every OLD_TICK_SECONDS."""
        await _on_air(world)
        await _plays(world, "Says", cover=False, elapsed_s=30, length_s=300)

        await world.advance(5 * OLD_TICK_SECONDS)

        assert world.playing()
        assert _shown(world.state())["title"] == "Says"
        assert world.errors() == []

    async def test_a_source_that_cannot_name_its_daemon_claims_nothing(self, world):
        """A dev host injects no systemd manager, and a manager can fail to
        answer. Failing open is the rule: an unanswerable question is not
        evidence the session died."""
        world.systemd.main_pid = AsyncMock(side_effect=OSError("no systemd here"))
        await _on_air(world)

        await world.advance(5 * OLD_TICK_SECONDS)

        assert world.playing()
        assert world.errors() == []



class TestTheWireFormat:
    """The reader parses what rtsp.c writes. Its refusals matter because a
    malformed item must not take the reader down with it — the loop that feeds
    it is the only thing that ever hears of the session."""

    async def test_an_item_missing_its_code_is_dropped(self):
        heard = _Collector()
        reader = MetadataReader("/nonexistent", on_event=heard)

        await reader._process_buffer((
            "<item><type>73736e63</type><length>0</length></item>" + _item("ssnc", "pbeg")
        ).encode())

        assert heard.kinds() == ["stream_begin"], "a malformed item stopped the stream"

    async def test_a_declared_length_of_zero_means_no_payload(self):
        """A `conn` with no address (rtsp.c writes `<length>0</length>` and no
        data element) still opens a session."""
        heard = _Collector()
        reader = MetadataReader("/nonexistent", on_event=heard)

        await reader._process_buffer(_item("ssnc", "conn").encode())

        assert [(e.kind, e.value) for e in heard.events] == [("conn", None)]

    async def test_a_payload_that_is_not_base64_is_treated_as_absent(self):
        """A client name whose payload does not decode carries nothing, and the
        item after it is still heard."""
        heard = _Collector()
        reader = MetadataReader("/nonexistent", on_event=heard)

        await reader._process_buffer((
            "<item><type>73736e63</type><code>736e616d</code>"
            "<length>4</length><data encoding=\"base64\">!!!not!!!</data></item>"
            + _item("ssnc", "pbeg")
        ).encode())

        assert heard.kinds() == ["stream_begin"]

    def test_a_type_that_is_not_hex_text_is_carried_through_verbatim(self):
        """`_hex_to_str` falls back to the raw string rather than raising, so an
        item the receiver does not understand is skipped and not fatal."""
        assert _hex_to_str("73736e63") == "ssnc"
        assert _hex_to_str("zzzz") == "zzzz"
        assert _hex_to_str("ffff") == "ffff"

    async def test_a_split_item_is_completed_by_the_next_read(self):
        """The pipe hands over 64 KiB at a time, so an item is routinely cut in
        half. Dropping the remainder loses one item in every bufferful."""
        heard = _Collector()
        reader = MetadataReader("/nonexistent", on_event=heard)
        whole = _item("ssnc", "snam", "Mac mini de Léo".encode()).encode()
        cut = len(whole) // 2

        left = await reader._process_buffer(whole[:cut])
        assert left == whole[:cut], "an incomplete item was thrown away"
        assert heard.events == []

        assert await reader._process_buffer(left + whole[cut:]) == b""
        assert heard.kinds() == ["client_name"]
        assert heard.events[0].value == "Mac mini de Léo"

    async def test_the_raw_plists_and_the_old_metadata_are_not_events(self):
        """5.6 writes the sender's item raw (`copl`, and a picture inside one
        can be hundreds of kB) — it is read decoded over D-Bus instead, so the
        reader passes over it, and over the 5.5 `core`/`PICT`/`prgr` a classic
        sender may still produce."""
        heard = _Collector()
        reader = MetadataReader("/nonexistent", on_event=heard)

        await reader._process_buffer("".join([
            _item("ssnc", "copl", b"bplist00" + b"x" * 4096),
            _item("core", "minm", b"Says"),
            _item("ssnc", "PICT", b"x" * 64),
            _item("ssnc", "prgr", b"1/2/3"),
            _item("ssnc", "pbeg"),
        ]).encode())

        assert heard.kinds() == ["stream_begin"]


class TestTheReadLoop:
    """The loop that opens the pipe and keeps it open for the session.

    shairport-sync closes its write end between sessions, so reopening is the
    normal path, not the error path.
    """

    async def test_it_reopens_the_pipe_when_the_writer_lets_go(self, tmp_path):
        pipe = tmp_path / "pipe"
        os.mkfifo(pipe)
        heard = _Collector()
        reader = MetadataReader(str(pipe), on_event=heard)

        async def write_one_session():
            """A whole AirPlay session: attach, send one item, let go.

            O_NONBLOCK, retried: a blocking O_WRONLY open waits for a reader and
            would stall this very event loop — which is the one the reader runs
            on — so a mutation that stops the reopen would hang instead of
            reddening.
            """
            for _ in range(200):
                try:
                    fd = os.open(pipe, os.O_WRONLY | os.O_NONBLOCK)
                except OSError:
                    await asyncio.sleep(0.01)   # ENXIO: no reader attached yet
                    continue
                os.write(fd, _item("ssnc", "pbeg").encode())
                os.close(fd)
                return True
            return False

        await reader.start()
        try:
            for session in range(2):
                assert await write_one_session(), \
                    f"the reader never attached for session {session + 1}"
                for _ in range(200):
                    await asyncio.sleep(0.01)
                    if len(heard.events) >= session + 1:
                        break
        finally:
            await asyncio.wait_for(reader.stop(), 2.0)

        assert heard.kinds()[:2] == ["stream_begin", "stream_begin"], \
            "the reader did not come back after the writer left"

    async def test_a_pipe_that_is_not_there_yet_is_waited_for(self, tmp_path, caplog):
        """shairport-sync creates it on first start; the reader may be up first,
        and giving up here means no metadata for the whole session."""
        pipe = tmp_path / "not-yet"
        reader = MetadataReader(str(pipe), on_event=_Collector())
        # No patching of asyncio.sleep: `metadata_reader` does a plain
        # `import asyncio`, so replacing it there replaces it for this test's own
        # awaits too, and the reader never gets a turn. The retry sleep is 2 s,
        # but the log this asserts on happens before it.
        with caplog.at_level("INFO", logger="source.airplay.metadata"):
            await reader.start()
            for _ in range(200):
                await asyncio.sleep(0.01)
                if any("not found" in r.message for r in caplog.records):
                    break
            await asyncio.wait_for(reader.stop(), 2.0)

        assert any("not found" in r.message for r in caplog.records)
        assert reader._task is None

    async def test_it_can_be_stopped_while_parked_waiting_for_the_sender(self, tmp_path):
        """The loop spends the whole session parked in `read()`. A sender that
        is connected and silent never wakes it, so a stop that only lowered the
        `_running` flag would block source teardown for ever — the task has to
        be cancelled out of that wait."""
        pipe = tmp_path / "pipe"
        os.mkfifo(pipe)
        reader = MetadataReader(str(pipe), on_event=_Collector())
        await reader.start()

        # Attach a writer and send nothing: read() now parks instead of EOFing.
        fd = None
        for _ in range(200):
            try:
                fd = os.open(pipe, os.O_WRONLY | os.O_NONBLOCK)
                break
            except OSError:
                await asyncio.sleep(0.01)
        assert fd is not None, "the reader never attached"

        # NOT `asyncio.wait_for`. `stop()` awaits the loop task under
        # `contextlib.suppress(asyncio.CancelledError)`, so wait_for's own
        # timeout cancellation is ABSORBED there and the call comes back
        # looking like a success — a stop that never terminates is
        # indistinguishable from one that did. Polling a task instead asks the
        # only question that matters: did it come back at all.
        stopping = asyncio.create_task(reader.stop())
        try:
            for _ in range(100):
                await asyncio.sleep(0.01)
                if stopping.done():
                    break
            assert stopping.done(), (
                "stop() never returned: the read loop was left parked in read(), "
                "and source teardown blocks behind it"
            )
        finally:
            stopping.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stopping
            os.close(fd)

        assert reader._running is False
        assert reader._task is None

    async def test_stopping_it_twice_is_harmless(self, tmp_path):
        reader = MetadataReader(str(tmp_path / "p"), on_event=_Collector())
        await reader.start()
        await asyncio.wait_for(reader.stop(), 2.0)
        await asyncio.wait_for(reader.stop(), 2.0)
        assert reader._running is False

    async def test_stopping_one_that_never_started_is_harmless(self, tmp_path):
        reader = MetadataReader(str(tmp_path / "p"), on_event=_Collector())
        await asyncio.wait_for(reader.stop(), 2.0)


class TestTheMetadataPipe:
    """The source makes sure the pipe exists on every start. It must never be
    fatal: shairport-sync creates the pipe itself, so failing here would stop a
    source that would have worked."""

    async def test_a_missing_pipe_is_created(self, monkeypatch, tmp_path):
        world = AirPlayWorld(monkeypatch, tmp_path)
        try:
            await world.select()
            assert (tmp_path / "shairport-sync-metadata").is_fifo()
        finally:
            await world.source.shutdown()

    async def test_an_existing_pipe_is_left_alone(self, monkeypatch, tmp_path):
        pipe = tmp_path / "shairport-sync-metadata"
        os.mkfifo(pipe)
        before = pipe.stat().st_ino
        world = AirPlayWorld(monkeypatch, tmp_path)
        try:
            await world.select()
            assert pipe.stat().st_ino == before
        finally:
            await world.source.shutdown()

    async def test_a_directory_we_cannot_write_only_warns(self, monkeypatch, tmp_path, caplog):
        """/tmp is world-writable on the appliance, but a hardened unit is not,
        and shairport-sync running as its own user creates the pipe anyway."""
        world = AirPlayWorld(monkeypatch, tmp_path)
        monkeypatch.setattr(os, "mkfifo", Mock(side_effect=PermissionError(13, "denied")))
        try:
            with caplog.at_level("WARNING", logger=world.source._logger.name):
                await world.select()
            assert any("shairport-sync" in r.message for r in caplog.records)
            assert world.state()["service"] == "running" and world.session() is None
        finally:
            await world.source.shutdown()

    async def test_a_pipe_created_between_the_check_and_the_call_is_fine(self, monkeypatch, tmp_path):
        """shairport-sync may create it in that window; the race is expected."""
        world = AirPlayWorld(monkeypatch, tmp_path)
        monkeypatch.setattr(os, "mkfifo", Mock(side_effect=FileExistsError()))
        try:
            await world.select()
            assert world.state()["service"] == "running" and world.session() is None
        finally:
            await world.source.shutdown()



class TestTheArtworkRoute:
    """`GET /api/airplay/artwork` — the cover shairport-sync wrote, served to the
    player. `session.artwork` points at it with a `?v=<hash>` so the
    browser refetches exactly when the bytes change.
    """

    @staticmethod
    def _client(source):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.sources.airplay.routes import setup_airplay_routes
        app = FastAPI()
        app.include_router(setup_airplay_routes(lambda: source), prefix="/api")
        return TestClient(app)

    async def test_the_cover_is_served_with_the_type_it_arrived_as(self, world):
        await _on_air(world)
        await _plays(world, "Says")

        resp = self._client(world.source).get(_shown(world.state())["artwork"])

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"

    async def test_it_is_cached_privately_and_immutably(self, world):
        """The URL carries the content hash, so the bytes behind it never
        change; `private` keeps a shared proxy from serving one household's
        cover to another."""
        await _on_air(world)
        await _plays(world, "Says")

        cache = self._client(world.source).get(_shown(world.state())["artwork"]).headers["cache-control"]

        assert "private" in cache and "immutable" in cache

    async def test_a_sender_that_pushed_no_cover_is_a_404_and_not_an_error(self, world, caplog):
        """Plenty of senders push none. Logged at ERROR this would raise the
        WebSocket error banner on an ordinary AirPlay session."""
        await _on_air(world)
        with caplog.at_level("ERROR", logger="backend.sources.airplay.routes"):
            resp = self._client(world.source).get("/api/airplay/artwork")

        assert resp.status_code == 404
        assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []

    async def test_a_departed_senders_cover_is_no_longer_served(self, world):
        """The route answers for the session on air: the previous sender's
        cover served after its goodbye is what a page still holding the old
        URL would keep drawing."""
        await _on_air(world)
        await _plays(world, "Says")
        url = _shown(world.state())["artwork"]

        await world.leaves(PHONE)

        assert self._client(world.source).get(url).status_code == 404



class TestLifecycle:
    """Start, release and stop build and tear down the reader; a reader left
    behind splits the pipe's byte stream with its replacement."""

    @staticmethod
    def _counted(monkeypatch) -> list:
        """Every reader the source builds from here on, and whether it stopped."""
        made: list = []
        base = airplay_module.MetadataReader

        class Counted(base):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                self.stopped = False
                made.append(self)

            async def stop(self):
                self.stopped = True
                await super().stop()

        monkeypatch.setattr(airplay_module, "MetadataReader", Counted)
        return made

    async def test_a_start_reads_the_configured_pipe(self, monkeypatch, tmp_path):
        """The whole chain on a real FIFO: the reader is built on the configured
        path and its one callback reaches the wire. An un-wired callback
        silently disables every branch behind it — progress, the client name
        and the AirPlay 2 connection events all once failed that way."""
        world = AirPlayWorld(monkeypatch, tmp_path)
        monkeypatch.setattr(airplay_module, "MetadataReader", MetadataReader)
        pipe = tmp_path / "shairport-sync-metadata"
        try:
            await world.select()
            fd = None
            for _ in range(200):
                try:
                    fd = os.open(pipe, os.O_WRONLY | os.O_NONBLOCK)
                    break
                except OSError:
                    await asyncio.sleep(0.01)   # ENXIO: no reader attached yet
            assert fd is not None, "nothing reads the configured pipe"
            try:
                os.write(fd, (
                    _item("ssnc", "conn", PHONE.encode())
                    + _item("ssnc", "snam", "iPhone de Léo".encode())
                ).encode())
                for _ in range(200):
                    await asyncio.sleep(0.01)
                    if _shown(world.state())["senders"]:
                        break
            finally:
                os.close(fd)

            assert world.active()
            assert _shown(world.state())["senders"] == ["iPhone de Léo"]
        finally:
            await world.leave()
            await world.source.shutdown()

    async def test_a_service_that_will_not_start_builds_no_reader(self, monkeypatch, tmp_path):
        world = AirPlayWorld(monkeypatch, tmp_path)
        made = self._counted(monkeypatch)
        world.systemd.start = AsyncMock(return_value=False)
        try:
            await world.select()
            assert world.state()["service"] == "failed"
            assert made == []
        finally:
            await world.source.shutdown()

    async def test_a_start_that_blows_up_tears_down_rather_than_half_starting(
        self, monkeypatch, tmp_path
    ):
        """A reader that fails to start is stopped, not left holding the pipe
        under a source that reported the start failed."""
        world = AirPlayWorld(monkeypatch, tmp_path)
        made = self._counted(monkeypatch)
        refusing = airplay_module.MetadataReader

        class Refusing(refusing):
            async def start(self):
                raise OSError("the pipe went away")

        monkeypatch.setattr(airplay_module, "MetadataReader", Refusing)
        try:
            await world.select()
            assert world.state()["service"] == "failed"
            assert len(made) == 1 and made[0].stopped
        finally:
            await world.source.shutdown()

    async def test_a_reroute_replaces_the_reader_instead_of_stacking_one(self, monkeypatch, tmp_path):
        """Two readers on one pipe split the byte stream between them and every
        item is parsed by whichever got that chunk."""
        world = AirPlayWorld(monkeypatch, tmp_path)
        made = self._counted(monkeypatch)
        try:
            await world.select()
            await _on_air(world)

            await world.reroute()

            assert len(made) == 2
            assert made[0].stopped and not made[1].stopped
            await world.connects(MAC, "Mac mini de Léo")
            assert _shown(world.state())["senders"] == ["Mac mini de Léo"]
        finally:
            await world.source.shutdown()

    async def test_leaving_the_source_stops_the_reader(self, monkeypatch, tmp_path):
        """Left running, the reader holds the pipe open for a source that is
        off, and hears a sender that nothing will publish."""
        world = AirPlayWorld(monkeypatch, tmp_path)
        made = self._counted(monkeypatch)
        try:
            await world.select()
            await _on_air(world)

            await world.leave()

            assert len(made) == 1 and made[0].stopped
        finally:
            await world.source.shutdown()
