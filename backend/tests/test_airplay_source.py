# backend/tests/test_airplay_source.py
"""
The AirPlay source's metadata pipe, the cover it pairs, the playhead it
anchors, and the reader that feeds it.

Everything here drives the source through the world shairport-sync was measured
to be (tests/airplay_world.py): the real `MetadataReader` parser, fed the items
the daemon writes, behind the real `AirPlaySource` on a real state machine, on a
virtual clock. What is asserted is what reaches the wire — the state (`_shown`),
every state published, the `source/position` events, the artwork route — never
the source's own fields. The pure parser and the read loop are driven through the real
`MetadataReader` alone, with an `on_event` collector.

What is being pinned first is the pairing rule. shairport-sync sends a track's
tags and its cover in two separate SET_PARAMETER requests and stamps both with
the same rtptime — "if they refer to the same item, they have the same rtptime"
(rtsp.c) — precisely because neither one follows the other reliably. Take the
order as the pairing and one of two things breaks: a cover that arrives first
is thrown away, or a track that sends none at all wears the previous track's
for its whole duration.

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
from io import BytesIO
from typing import List, Optional
from unittest.mock import AsyncMock, Mock

import pytest
from PIL import Image

from backend.core.models.ws_events import SourceErrorReason
from backend.sources.airplay import source as airplay_module
from backend.sources.airplay.metadata_reader import MetadataReader, PipeEvent, _hex_to_str
from backend.core.audio_source import POSITION_TOLERANCE_MS
from backend.sources.airplay.source import ARTWORK_SETTLE_SECONDS
from backend.tests.airplay_world import MAC, PHONE, AirPlayWorld, Item, ssnc

LIVE_PIPE = "/tmp/shairport-sync-metadata"

# Two rtptimes, as the sender sends them (the pipe carries them as ASCII decimal).
RTP_A = 3222108659
RTP_B = 3222285731
# The same track, three AirPlay packets later. Measured on an iPhone
# (2026-09-03): iOS re-stamps every re-sent bundle and its picture with the
# playback position, which advances by 1056 frames (24 ms) inside one track.
RTP_A_LATER = RTP_A + 1056
# The same track, the other way round: a Mac stamped the picture 1408 frames
# (32 ms) *before* its own track's tags. Measured 2026-09-03.
RTP_A_EARLIER = RTP_A - 1408

SAMPLE_RATE = 44100

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


# === What the sender writes ===

def _cover(color: str, size: int = 600) -> bytes:
    """A real cover, so the dimension decode on the way in is real too."""
    buf = BytesIO()
    Image.new("RGB", (size, size), color).save(buf, format="PNG")
    return buf.getvalue()


def _stamp(rtptime: Optional[int]) -> Optional[bytes]:
    """An rtptime as mdst/pcst carry it; None is the empty item shairport-sync
    writes when the sender gave it no RTP-Info."""
    return str(rtptime).encode() if rtptime is not None else None


def _bundle(rtptime: Optional[int], title: str, artist: Optional[str] = "Nils Frahm") -> List[Item]:
    """A track's tags, bracketed by mdst/mden as rtsp.c brackets them.

    `artist=None` is a bundle the sender sent no `asar` in — not an empty one:
    a DAAP tag a sender omits produces no item at all, which is the whole
    difference between "this track has no artist" and "unchanged".
    """
    tags: List[Item] = [("core", "minm", title.encode())]
    if artist is not None:
        tags.append(("core", "asar", artist.encode()))
    return [ssnc("mdst", _stamp(rtptime)), *tags, ssnc("mden", _stamp(rtptime))]


def _picture(rtptime: Optional[int], data: Optional[bytes]) -> List[Item]:
    """A cover, bracketed by pcst/pcen as rtsp.c brackets it."""
    return [ssnc("pcst", _stamp(rtptime)), ssnc("PICT", data), ssnc("pcen", _stamp(rtptime))]


def _progress(start_s: float, current_s: float, end_s: float) -> Item:
    """`prgr` as rtsp.c writes it: three RTP frame counts separated by slashes."""
    frames = [int(s * SAMPLE_RATE) for s in (start_s, current_s, end_s)]
    return ssnc("prgr", "/".join(map(str, frames)).encode())


def _item(item_type: str, code: str, payload: Optional[bytes] = None) -> str:
    """One metadata item in the wire shape the pipe carries.

    Type and code are hex-encoded ASCII, the payload base64 with its decoded
    length alongside — an item with no payload carries `<length>0</length>` and
    no data element at all, which is how shairport-sync reports an rtptime it
    did not get from the sender.
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

async def _on_air(world: AirPlayWorld) -> None:
    """A phone connected and playing a Buffered stream, before any tags."""
    await world.connects(PHONE, "iPhone de Léo")
    await world.send(ssnc("pbeg"), ssnc("pres"), ssnc("pffr", b"1/2"), ssnc("styp", b"Buffered"))
    assert world.playing(), "the stream never reached PLAYING"


async def _after_the_hold(world: AirPlayWorld) -> None:
    """Let the artwork hold run out, so what follows is the state it leaves."""
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
        "title": session["title"],
        "artist": session["artist"],
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


class TestCoverPairing:
    """Which track the cover on screen belongs to."""

    async def test_a_track_and_its_cover_are_published_together(self, world):
        """The non-triviality check the rest of this class rests on: a stream
        that produced no cover at all would satisfy every 'has no cover'
        assertion below."""
        await _on_air(world)

        await world.send(*_bundle(RTP_A, "Says"), *_picture(RTP_A, _cover("navy")))

        meta = _shown(world.state())
        assert meta["title"] == "Says"
        assert meta["artwork"].startswith("/api/airplay/artwork?v=")
        assert meta["artwork_width"] == 600
        assert world.source.get_artwork() is not None

    async def test_a_cover_that_arrives_before_its_track_is_kept(self, world):
        """The order is the sender's to choose — two SET_PARAMETER requests,
        nothing sequencing them. Dropping the cover on the bundle that follows
        would delete the one that was right. Asserted after the hold, so the
        cover is on screen because it is paired, not because it is held."""
        await _on_air(world)

        await world.send(*_picture(RTP_A, _cover("navy")))
        await world.send(*_bundle(RTP_A, "Says"))
        await _after_the_hold(world)

        assert _shown(world.state())["title"] == "Says"
        assert _shown(world.state())["artwork"] is not None

    async def test_a_track_that_sends_no_cover_shows_none(self, world):
        """The defect this pairing exists for. Plenty of senders push a picture
        for one track and nothing for the next; the cover left behind is what
        the full-screen player draws for the whole of it.

        The drop is deferred by ARTWORK_SETTLE_SECONDS, not instant — see
        `test_the_cover_is_held_while_the_next_one_is_still_in_flight` for what
        that window is for. What this pins is that the window *ends*: a hold
        that never expired would be the whole-track-stale-cover bug again."""
        await _on_air(world)
        await world.send(*_bundle(RTP_A, "Says"), *_picture(RTP_A, _cover("navy")))

        await world.send(*_bundle(RTP_B, "Toilet Brush"))
        await _after_the_hold(world)

        meta = _shown(world.state())
        assert meta["title"] == "Toilet Brush"
        assert meta["artwork"] is None
        assert meta["artwork_width"] is None

    async def test_a_cover_stamped_for_the_previous_track_is_not_adopted(self, world):
        """The stamp is the whole rule: a picture in hand is not this track's
        merely because it is the most recent one — once the hold has expired."""
        await _on_air(world)
        await world.send(*_picture(RTP_A, _cover("navy")))

        await world.send(*_bundle(RTP_B, "Toilet Brush"))
        await _after_the_hold(world)

        assert _shown(world.state())["title"] == "Toilet Brush"
        assert _shown(world.state())["artwork"] is None

    async def test_a_cover_stamped_just_after_its_own_tags_is_not_dropped(self, world):
        """The stamp is a position, not an identity, and iOS proves it.

        An iPhone re-sends its bundle several times inside one track, each under
        a fresh rtptime, and stamps the picture with one of them — so the
        picture routinely carries a stamp a few packets *after* the last bundle
        received, and no later bundle ever comes to meet it. Judged by equality
        that pairing never completes: the hold expired mid-track and dropped a
        cover that was this very track's, which the untrusted-sender gate reads
        as "no real cover" and takes AudioPlayerFull off the screen. Measured
        live at 11 s on the screen, four times in 95 publishes.

        Asserted after the hold has run out, because before it the pending
        settle shows the cover for the wrong reason.
        """
        await _on_air(world)
        await world.send(*_bundle(RTP_A, "Says"), *_picture(RTP_A_LATER, _cover("navy")))

        await _after_the_hold(world)

        meta = _shown(world.state())
        assert meta["title"] == "Says"
        assert meta["artwork"], meta
        assert meta["artwork_width"] == 600

    async def test_a_cover_stamped_just_before_its_own_tags_is_not_dropped(self, world):
        """The drift runs both ways, and the second direction is a Mac's.

        Measured on the unit: a macOS sender opened a session stamping the
        picture 1408 frames — 32 ms — *ahead* of the tags that followed it, and
        nothing came after to close the gap. Judged by order alone that reads
        as "the previous track's cover", which is what the deadline is for, so
        the hold expired on the playing track's own sleeve and the player left
        the screen. What tells the two apart is distance: a real track change
        measured no nearer than 535 ms.
        """
        await _on_air(world)
        await world.send(*_picture(RTP_A_EARLIER, _cover("navy")))
        await world.send(*_bundle(RTP_A, "Says"))

        await _after_the_hold(world)

        meta = _shown(world.state())
        assert meta["title"] == "Says"
        assert meta["artwork"], meta
        assert meta["artwork_width"] == 600

    async def test_a_drifting_sender_never_takes_the_player_off_the_screen(self, world):
        """The same shape over a run, judged the way the screen judges it.

        Every publish is replayed through `useRichDisplay`'s airplay arm — title
        AND artist AND a cover over 300 px — and none of them may take it from
        true back to false. That is the whole defect class: a display field
        emptied while its replacement is in flight does not correct the piece of
        UI it feeds, it removes it. Over every published state, not the last:
        the last one was always right, which is how this survived the fix that
        named it.
        """
        await _on_air(world)
        await world.send(*_bundle(RTP_A, "Says"), *_picture(RTP_A, _cover("navy")))
        before = len(world.published())
        assert _rich(world.published()[-1]), "the run starts from nothing to lose"

        # The sender re-sends the same track under a fresh stamp, and its
        # picture lands a few packets ahead of it — the measured iOS order.
        await world.send(*_bundle(RTP_A_LATER, "Says"))
        await world.send(*_picture(RTP_A_LATER + 1056, _cover("navy")))
        await _after_the_hold(world)

        # Only what changed is published, so the run's states are the starting
        # one plus every change; ending rich is what makes "no unmount" count.
        published = world.published()[before - 1:]
        assert _rich(world.state()), "the run ended without the player it started with"
        unmounts = [(a, b) for a, b in zip(published, published[1:]) if _rich(a) and not _rich(b)]
        assert not unmounts, unmounts

    async def test_two_tracks_off_one_album_keep_their_cover(self, world):
        """The same image byte for byte, so the source's dedupe short-circuits —
        but the picture that changed nothing still moved which track the cover
        belongs to. Recorded after the dedupe, the second track would drop to
        its glyph on an album that has a cover: asserted after the hold, which
        would otherwise carry it for 8 s and hide exactly that."""
        await _on_air(world)
        sleeve = _cover("navy")
        await world.send(*_bundle(RTP_A, "Says"), *_picture(RTP_A, sleeve))
        first = _shown(world.state())["artwork"]

        await world.send(*_bundle(RTP_B, "Says (Live)"))
        await world.send(*_picture(RTP_B, sleeve))
        await _after_the_hold(world)

        assert _shown(world.state())["title"] == "Says (Live)"
        assert _shown(world.state())["artwork"] == first

    async def test_the_cover_is_held_while_the_next_one_is_still_in_flight(self, world):
        """A track change must not blank the cover for the millisecond before
        its own arrives.

        The tags and the picture are two SET_PARAMETER requests in no
        guaranteed order, so the tags-first order leaves the new stamp
        unpaired. Publishing that gap sends a state with no `artwork`,
        and `useRichDisplay`'s untrusted-sender gate reads a missing
        `artwork_width` as "no real cover from this sender": the frontend
        swaps AudioPlayerFull for the AudioSourceStatus card and back within
        ~30 ms, which is visible as the player animating itself out and in.
        Measured on a macOS sender, on every track change *and* every transport
        action, since the sender re-sends its bundle under a fresh rtptime.

        The window is what is asserted here; that it expires is asserted by
        `test_a_track_that_sends_no_cover_shows_none`.
        """
        await _on_air(world)
        await world.send(*_bundle(RTP_A, "Says"), *_picture(RTP_A, _cover("navy")))
        held = _shown(world.state())["artwork"]

        await world.send(*_bundle(RTP_B, "Toilet Brush"))

        meta = _shown(world.state())
        assert meta["title"] == "Toilet Brush"
        assert meta["artwork"] == held
        assert meta["artwork_width"] == 600

        # And the track's own picture, when it lands, takes the hold's place.
        await world.send(*_picture(RTP_B, _cover("crimson", size=450)))

        assert _shown(world.state())["artwork"] != held
        assert _shown(world.state())["artwork_width"] == 450

    async def test_a_cover_arriving_first_does_not_blank_the_one_on_screen(self, world):
        """The mirror of the test above, and the same flicker.

        The order is the sender's, so the picture can be the one that arrives
        first — and then the new stamp is on the cover while the title on
        screen is still the previous track's. A publish judging on the stamps
        alone found them unequal and dropped `artwork` from that state,
        which `useRichDisplay`'s untrusted-sender gate reads as "this sender
        pushes no real cover": AudioPlayerFull swapped for the AudioSourceStatus
        card and back, the player animating itself out and in.

        Asserted over every published state, not the last one: the last one was
        always right, which is why the tags-first fix left this half standing.
        The two requests are sent apart, so the state between them is published.
        """
        await _on_air(world)
        await world.send(*_bundle(RTP_A, "Says"), *_picture(RTP_A, _cover("navy")))
        before = len(world.published())

        await world.send(*_picture(RTP_B, _cover("crimson", size=450)))
        await world.send(*_bundle(RTP_B, "Toilet Brush"))

        published = world.published()[before:]
        assert published, "the track change published nothing to judge"
        assert all(_shown(m)["artwork_width"] for m in published), published
        assert _shown(world.state())["title"] == "Toilet Brush"
        assert _shown(world.state())["artwork_width"] == 450

    async def test_a_cover_arriving_first_off_one_album_does_not_blank_it_either(self, world):
        """Same order, through the dedupe: the identical image re-sent under a
        new stamp takes the early return, which published its own coverless
        state on the way past."""
        await _on_air(world)
        sleeve = _cover("navy")
        await world.send(*_bundle(RTP_A, "Says"), *_picture(RTP_A, sleeve))
        before = len(world.published())

        await world.send(*_picture(RTP_B, sleeve))
        await world.send(*_bundle(RTP_B, "Says (Live)"))

        published = world.published()[before:]
        assert published, "the track change published nothing to judge"
        assert all(_shown(m)["artwork_width"] for m in published), published
        assert _shown(world.state())["title"] == "Says (Live)"

    async def test_a_bundle_under_a_new_stamp_owns_every_tag(self, world):
        """Nothing belonging to the previous track is published as this one's.

        A bundle carries only the DAAP tags the sender put in it, so a track
        sent without an `asar` used to be published wearing the previous
        track's artist — under the right title, for the whole of it. The stamp
        settles it, the same stamp the cover is paired by: a new one is a
        different track and owns its absences too. The status card is then the
        right screen, and it is the one the gate already picks for a sender
        that publishes a bare title.
        """
        await _on_air(world)
        await world.send(*_bundle(RTP_A, "Says", artist="Nils Frahm"))

        await world.send(*_bundle(RTP_B, "Untitled recording", artist=None))

        assert _shown(world.state())["title"] == "Untitled recording"
        assert not _shown(world.state())["artist"]

    async def test_a_bundle_under_the_stamp_on_screen_amends_it(self, world):
        """The other half of the same rule, and what stops it stripping a track
        that is already right: a bundle re-sent under the stamp on screen is an
        amendment to that track, not a new one, so the tags it does not carry
        stay as they were."""
        await _on_air(world)
        await world.send(*_bundle(RTP_A, "Says", artist="Nils Frahm"))

        await world.send(*_bundle(RTP_A, "Says", artist=None))

        assert _shown(world.state())["artist"] == "Nils Frahm"

    async def test_a_sender_without_rtp_info_keeps_what_it_had(self, world):
        """shairport-sync tolerates a sender that sends no RTP-Info and sends
        mdst/pcst empty, which leaves nothing to pair on. Hiding every cover
        there would be worse than carrying one: documented, not worked around.
        Asserted after the hold, so it is kept and not merely held."""
        await _on_air(world)
        await world.send(*_bundle(None, "Says"), *_picture(None, _cover("navy")))

        await world.send(*_bundle(None, "Toilet Brush"))
        await _after_the_hold(world)

        assert _shown(world.state())["title"] == "Toilet Brush"
        assert _shown(world.state())["artwork"] is not None


class TestSessionControl:
    """What moves the phase: a Buffered stream's first frame, its `paus`/`pres`,
    and the stream ending. The session model's own scenarios — pauses timing
    out, skips, Realtime streams — live in test_airplay_sessions.py."""

    async def test_a_stream_is_loading_until_its_first_frame(self, world):
        """`pbeg` opens the stream; sound arrives at `pffr`, and `styp` says
        whether the stream will report its pauses. Published as playing before
        that, the screensaver and the lock screen ran ahead of silence."""
        await world.connects()
        await world.send(ssnc("pbeg"), ssnc("pres"))

        assert world.phase() == "loading"

        await world.send(ssnc("pffr", b"1/2"), ssnc("styp", b"Buffered"))

        assert world.phase() == "playing"

    async def test_a_flush_neither_pauses_nor_freezes_the_playhead(self, world):
        """E54, inverted from the test this file used to carry. `pfls` (a flush)
        was read as a pause: it stopped the playback and froze the position under
        a sender that never paused, and armed the auto-stop behind it. It never
        appeared in the 2026-09-23 measurements and is no longer read: the
        session plays on, and its anchor keeps moving."""
        await _on_air(world)
        await world.send(_progress(0, 30, 300))

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
        await world.send(*_bundle(RTP_A, "Says"), *_picture(RTP_A, _cover("navy")))
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
        await world.send(*_bundle(RTP_A, "Says"), *_picture(RTP_A, _cover("navy")))

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
        await world.send(*_bundle(RTP_A, "Says"))

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
        await world.send(*_bundle(RTP_A, "Says"), *_picture(RTP_A, _cover("navy")))

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
        beginning (tags or a cover with no session are leftovers of one that
        ended). If only `conn` bound the session to its daemon, a session
        opened by either would never learn the daemon died.
        """
        await world.send(ssnc("pbeg"), *_bundle(RTP_A, "Says"))
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
        await world.send(*_bundle(RTP_A, "Says"), _progress(0, 30, 300))

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


class TestProgress:
    """`prgr` carries three RTP frame counts. It arrives every 5-15 s, not
    continuously, so what is published is an anchor: where the playhead was,
    and when."""

    async def test_a_snapshot_becomes_a_position_and_a_duration_in_ms(self, world):
        await _on_air(world)
        await world.send(_progress(0, 30, 300))

        assert world.session()["duration_ms"] == 300_000
        assert abs(world.position_ms() - 30_000) < 50

    async def test_a_track_that_does_not_start_at_zero_is_measured_from_its_start(self, world):
        """`start` is the track's own first frame, not the session's: a stream
        running for an hour has huge frame counts, and reading `current` as an
        absolute would show the position as the session's age."""
        await _on_air(world)
        await world.send(_progress(3600, 3610, 3900))

        assert world.session()["duration_ms"] == 300_000
        assert abs(world.position_ms() - 10_000) < 50

    async def test_a_snapshot_that_makes_no_sense_is_ignored(self, world):
        """`end <= start` is a zero-length track; taken at face value it makes
        the duration 0 and every position clamp to it."""
        await _on_air(world)
        await world.send(_progress(0, 30, 300))

        await world.send(_progress(500, 500, 500))

        assert world.session()["duration_ms"] == 300_000

    async def test_a_jump_is_broadcast_at_once(self, world):
        """A track change or a seek is an arbitrarily large move that the
        clients' local interpolation cannot guess."""
        await _on_air(world)
        await world.send(_progress(0, 0, 300))
        marker = len(world.recorder.envelopes)

        await world.send(_progress(0, 200, 300))

        pushed = _pushes(world, marker)
        assert len(pushed) == 1
        assert pushed[0]["position"]["ms"] > 199_000
        assert world.position_ms() > 199_000

    async def test_a_snapshot_that_only_confirms_the_interpolation_is_not_broadcast(self, world):
        """A sender that emits `prgr` often would otherwise flood every
        connected client with values they had already worked out."""
        await _on_air(world)
        await world.send(_progress(0, 100, 300))
        marker = len(world.recorder.envelopes)

        within = (POSITION_TOLERANCE_MS / 1000) / 2
        await world.send(_progress(0, 100 + within, 300))

        assert _pushes(world, marker) == []
        assert world.recorder.envelopes[marker:] == []

    async def test_the_position_moves_while_the_track_plays(self, world):
        """`prgr` is 5-15 s apart, so between two of them the position has to be
        derived from the clock or the bar stops: the anchor a playing session
        publishes moves at rate 1."""
        await _on_air(world)
        await world.send(_progress(0, 30, 300))
        taken = world.position_ms()

        await world.advance(OLD_TICK_SECONDS)

        assert world.session()["position"]["rate"] == 1.0
        assert world.position_ms() - taken >= OLD_TICK_SECONDS * 1000 - 100

    async def test_a_paused_position_keeps_what_it_had_aged_to(self, world):
        """The clock is moved on between the snapshot and the pause on purpose:
        with the two at the same instant, a freeze that forgot to bank the aged
        value would look identical to one that banked it."""
        await _on_air(world)
        await world.send(_progress(0, 30, 300))
        await world.advance(5)                   # five seconds of playing

        await world.pauses()

        frozen = world.position_ms()
        assert frozen >= 34_900, f"the pause discarded the five seconds that had played: {frozen} ms"
        marker = len(world.recorder.envelopes)
        await world.advance(30)
        await world.send(ssnc("snam", "iPhone de Léo".encode()))   # any publish
        assert world.position_ms() == frozen
        assert _pushes(world, marker) == []

    async def test_playing_again_resumes_ageing_from_the_frozen_point(self, world):
        await _on_air(world)
        await world.send(_progress(0, 30, 300))
        await world.pauses()
        frozen = world.position_ms()
        await world.advance(30)

        await world.resumes()

        resumed = world.position_ms()
        assert frozen <= resumed < frozen + 1000, "resuming restarted or skipped the track"
        await world.advance(OLD_TICK_SECONDS)
        assert world.position_ms() > resumed + 5_000, "the playhead stayed frozen"

    async def test_a_snapshot_taken_while_paused_does_not_start_ageing(self, world):
        await _on_air(world)
        await world.pauses()
        await world.send(_progress(0, 30, 300))
        taken = world.position_ms()

        await world.advance(30)
        await world.send(ssnc("snam", "iPhone de Léo".encode()))   # any publish

        assert world.position_ms() == taken == 30_000

    async def test_a_malformed_progress_payload_is_ignored(self, world):
        await _on_air(world)
        await world.send(_progress(0, 30, 300))

        await world.send(ssnc("prgr", b"not/a/number"))

        assert world.session()["duration_ms"] == 300_000
        assert world.playing()

    async def test_a_progress_payload_of_the_wrong_shape_is_ignored(self, world):
        await _on_air(world)
        await world.send(_progress(0, 30, 300))

        await world.send(ssnc("prgr", b"12345"))

        assert world.session()["duration_ms"] == 300_000
        assert world.playing()


class TestNoPositionTicker:
    """The old source re-published the aged playhead every 10 s so that a new
    connection's initial state was never more than 10 s stale. The anchor
    replaces it: any client computes the playhead from the state it is handed,
    so a steady playhead puts nothing on the wire."""

    async def test_a_new_client_reads_the_playhead_where_it_is(self, world):
        await _on_air(world)
        await world.send(_progress(0, 30, 300))
        marker = len(world.recorder.envelopes)

        await world.advance(2 * OLD_TICK_SECONDS)

        assert world.recorder.envelopes[marker:] == [], "a steady playhead was broadcast"
        assert abs(world.position_ms() - (30_000 + 2 * OLD_TICK_SECONDS * 1000)) < 50

    async def test_a_track_of_unknown_length_has_no_playhead(self, world):
        """A sender that never sends `prgr` leaves the duration unknown;
        publishing a playhead would seed every client's bar with a zero-length
        track."""
        await _on_air(world)

        await world.advance(6 * OLD_TICK_SECONDS)

        assert world.session()["position"] is None
        assert world.session()["duration_ms"] is None
        assert world.positions() == []

    async def test_a_stopped_source_pushes_no_position(self, world):
        """A playhead broadcast for a source that is no longer selected moves
        a bar nobody should see."""
        await _on_air(world)
        await world.send(_progress(0, 30, 300))
        await world.leave()
        marker = len(world.recorder.envelopes)

        await world.advance(6 * OLD_TICK_SECONDS)

        assert _pushes(world, marker) == []


class TestTheWireFormat:
    """The reader parses what rtsp.c writes. Its refusals matter because a
    malformed item must not take the reader down with it — the loop that feeds
    it is the only thing that ever hears from the sender."""

    async def test_an_item_missing_its_code_is_dropped(self):
        heard = _Collector()
        reader = MetadataReader("/nonexistent", on_event=heard)

        await reader._process_buffer((
            "<item><type>73736e63</type><length>0</length></item>" + _item("ssnc", "pbeg")
        ).encode())

        assert heard.kinds() == ["stream_begin"], "a malformed item stopped the stream"

    async def test_a_declared_length_of_zero_means_no_payload(self):
        """rtsp.c writes `<length>0</length>` and no data element when the
        sender gave it no rtptime — that is how an un-stamped bundle arrives."""
        heard = _Collector()
        reader = MetadataReader("/nonexistent", on_event=heard)

        await reader._process_buffer("".join(_item(*i) for i in _bundle(None, "Says")).encode())

        assert heard.kinds() == ["tags"]
        assert heard.events[0].value["title"] == "Says"
        assert heard.events[0].rtptime is None

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

    async def test_the_tags_a_sender_can_send_all_land(self, world):
        await _on_air(world)

        await world.send(
            ssnc("mdst", _stamp(RTP_A)),
            ("core", "minm", b"Says"),
            ("core", "asar", b"Nils Frahm"),
            ("core", "asal", b"Spaces"),
            ("core", "asgn", b"Modern Classical"),
            ssnc("mden", _stamp(RTP_A)),
        )

        meta = _shown(world.state())
        assert meta["title"] == "Says"
        assert meta["artist"] == "Nils Frahm"
        assert meta["album"] == "Spaces"

    async def test_a_bundle_that_gathered_nothing_is_not_published_as_a_track(self, world):
        """An empty mdst/mden pair is routine. What it must not do is re-stamp
        the track on screen: published as a bundle, it would carry a new rtptime,
        and the cover that belongs to the track still playing would be dropped
        for the rest of it once the hold ran out.
        """
        await _on_air(world)
        await world.send(*_bundle(RTP_A, "Says"), *_picture(RTP_A, _cover("navy")))
        assert _shown(world.state())["artwork"], "no cover to lose"

        await world.send(ssnc("mdst", _stamp(RTP_B)), ssnc("mden", _stamp(RTP_B)))
        await _after_the_hold(world)

        assert _shown(world.state())["title"] == "Says"
        assert _shown(world.state())["artwork"], \
            "the cover was unpaired by a bundle that carried no track"

    async def test_a_picture_with_no_bytes_is_not_published_as_a_cover(self, world):
        await _on_air(world)

        await world.send(*_bundle(RTP_A, "Says"), *_picture(RTP_A, None))

        assert world.source.get_artwork() is None
        assert _shown(world.state())["artwork"] is None


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


class TestArtworkHandoff:
    async def test_the_cover_is_served_with_the_type_it_arrived_as(self, world):
        await _on_air(world)
        await world.send(*_bundle(RTP_A, "Says"), *_picture(RTP_A, _cover("navy")))

        data, mime = world.source.get_artwork()

        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        assert mime == "image/png"

    async def test_a_jpeg_cover_is_labelled_jpeg(self, world):
        await _on_air(world)
        jpeg = b"\xff\xd8\xff\xe0" + b"J" * 2048

        await world.send(*_bundle(RTP_A, "Says"), *_picture(RTP_A, jpeg))

        assert world.source.get_artwork()[1] == "image/jpeg"

    async def test_no_cover_yet_is_no_answer_at_all(self, world):
        await _on_air(world)
        assert world.source.get_artwork() is None


class TestTheArtworkRoute:
    """`GET /api/airplay/artwork` — the cover the pipe delivered, served to the
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
        await world.send(*_bundle(RTP_A, "Says"), *_picture(RTP_A, _cover("navy")))

        resp = self._client(world.source).get(_shown(world.state())["artwork"])

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"

    async def test_it_is_cached_privately_and_immutably(self, world):
        """The URL carries the content hash, so the bytes behind it never
        change; `private` keeps a shared proxy from serving one household's
        cover to another."""
        await _on_air(world)
        await world.send(*_bundle(RTP_A, "Says"), *_picture(RTP_A, _cover("navy")))

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
        await world.send(*_bundle(RTP_A, "Says"), *_picture(RTP_A, _cover("navy")))
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
