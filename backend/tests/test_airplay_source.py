# backend/tests/test_airplay_source.py
"""
Unit tests for the AirPlay source's metadata pipe and the cover it pairs.

Everything here drives the real `MetadataReader` wired to the real
`AirPlaySource`, feeding the byte stream shairport-sync writes to the pipe. The
outside world is the sender, and it is represented by exactly that stream.

What is being pinned is the pairing rule. shairport-sync sends a track's tags
and its cover in two separate SET_PARAMETER requests and stamps both with the
same rtptime — "if they refer to the same item, they have the same rtptime"
(rtsp.c) — precisely because neither one follows the other reliably. Take the
order as the pairing and one of two things breaks: a cover that arrives first is
thrown away, or a track that sends none at all wears the previous track's for
its whole duration.
"""
import base64
from io import BytesIO
from typing import Optional

import pytest
from PIL import Image

from backend.sources.airplay.metadata_reader import MetadataReader
from backend.sources.airplay import source as airplay_source
from backend.sources.airplay.source import AirPlaySource

# Two rtptimes, as the sender sends them: an ASCII decimal string.
RTP_A = "3222108659"
RTP_B = "3222285731"
# The same track, three AirPlay packets later. Measured on an iPhone
# (2026-09-03): iOS re-stamps every re-sent bundle and its picture with the
# playback position, which advances by 1056 frames (24 ms) inside one track.
RTP_A_LATER = str(int(RTP_A) + 1056)


def _cover(color: str, size: int = 600) -> bytes:
    """A real cover, so the dimension decode on the way in is real too."""
    buf = BytesIO()
    Image.new("RGB", (size, size), color).save(buf, format="PNG")
    return buf.getvalue()


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


def _bundle(
    rtptime: Optional[str], title: str, artist: Optional[str] = "Nils Frahm"
) -> str:
    """A track's tags, bracketed by mdst/mden as rtsp.c brackets them.

    `artist=None` is a bundle the sender sent no `asar` in — not an empty one:
    a DAAP tag a sender omits produces no item at all, which is the whole
    difference between "this track has no artist" and "unchanged".
    """
    stamp = rtptime.encode() if rtptime else None
    tags = [_item("core", "minm", title.encode())]
    if artist is not None:
        tags.append(_item("core", "asar", artist.encode()))
    return "".join([
        _item("ssnc", "mdst", stamp),
        *tags,
        _item("ssnc", "mden", stamp),
    ])


def _picture(rtptime: Optional[str], data: bytes) -> str:
    """A cover, bracketed by pcst/pcen as rtsp.c brackets it."""
    stamp = rtptime.encode() if rtptime else None
    return "".join([
        _item("ssnc", "pcst", stamp),
        _item("ssnc", "PICT", data),
        _item("ssnc", "pcen", stamp),
    ])


@pytest.fixture
def airplay(monkeypatch):
    """The real source behind the real reader, fed by the caller.

    The artwork hold is shortened from its production 8 s. What the two "no
    cover" tests pin is that the window *ends*, not how long it is, and
    sleeping the real bound would put 16 s of wall clock in the suite for a
    tuning constant.
    """
    monkeypatch.setattr(airplay_source, "ARTWORK_SETTLE_SECONDS", 0.2)
    source = AirPlaySource()
    reader = MetadataReader(
        pipe_path="/nonexistent",
        on_metadata=source._on_metadata_update,
        on_play_state=source._on_play_state,
        on_artwork=source._on_artwork,
    )

    async def feed(*chunks: str) -> None:
        await reader._process_buffer("".join(chunks).encode())

    return source, feed


def _every_publish(source) -> list:
    """Record every state the source publishes from here on, in order.

    `source.metadata` is only the last one, and a flicker is by definition a
    state that was published and then corrected — so the assertions about one
    have to be able to see the states in between. The real `set_state` still
    runs; this only watches what goes through it.
    """
    published: list = []
    real = source.set_state

    def watch(state, metadata=None):
        published.append(dict(metadata or {}))
        real(state, metadata)

    source.set_state = watch
    return published


async def _after_the_hold() -> None:
    """Wait out the artwork hold, so what follows is the state it leaves behind.

    Read off the module rather than imported, so it follows the shortened value
    the fixture installs. `asyncio` is the module's single import of it, made
    further down with the second block; a second one here would be an F811
    redefinition.
    """
    await asyncio.sleep(airplay_source.ARTWORK_SETTLE_SECONDS + 0.05)


class TestCoverPairing:
    """Which track the cover on screen belongs to."""

    async def test_a_track_and_its_cover_are_published_together(self, airplay):
        """The non-triviality check the rest of this class rests on: a stream
        that produced no cover at all would satisfy every 'has no cover'
        assertion below."""
        source, feed = airplay

        await feed(_bundle(RTP_A, "Says"), _picture(RTP_A, _cover("navy")))

        assert source.metadata["title"] == "Says"
        assert source.metadata["album_art_url"].startswith("/api/airplay/artwork?v=")
        assert source.metadata["album_art_width"] == 600
        assert source.get_artwork() is not None

    async def test_a_cover_that_arrives_before_its_track_is_kept(self, airplay):
        """The order is the sender's to choose — two SET_PARAMETER requests,
        nothing sequencing them. Dropping the cover on the bundle that follows
        would delete the one that was right."""
        source, feed = airplay

        await feed(_picture(RTP_A, _cover("navy")), _bundle(RTP_A, "Says"))

        assert source.metadata["title"] == "Says"
        assert "album_art_url" in source.metadata

    async def test_a_track_that_sends_no_cover_shows_none(self, airplay):
        """The defect this pairing exists for. Plenty of senders push a picture
        for one track and nothing for the next; the cover left behind is what
        the full-screen player draws for the whole of it.

        The drop is deferred by ARTWORK_SETTLE_SECONDS, not instant — see
        `test_the_cover_is_held_while_the_next_one_is_still_in_flight` for what
        that window is for. What this pins is that the window *ends*: a hold
        that never expired would be the whole-track-stale-cover bug again."""
        source, feed = airplay
        await feed(_bundle(RTP_A, "Says"), _picture(RTP_A, _cover("navy")))

        await feed(_bundle(RTP_B, "Toilet Brush"))
        await _after_the_hold()

        assert source.metadata["title"] == "Toilet Brush"
        assert "album_art_url" not in source.metadata
        assert "album_art_width" not in source.metadata

    async def test_a_cover_stamped_for_the_previous_track_is_not_adopted(self, airplay):
        """The stamp is the whole rule: a picture in hand is not this track's
        merely because it is the most recent one — once the hold has expired."""
        source, feed = airplay
        await feed(_picture(RTP_A, _cover("navy")))

        await feed(_bundle(RTP_B, "Toilet Brush"))
        await _after_the_hold()

        assert "album_art_url" not in source.metadata

    async def test_a_cover_stamped_just_after_its_own_tags_is_not_dropped(self, airplay):
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
        source, feed = airplay
        await feed(_bundle(RTP_A, "Says"), _picture(RTP_A_LATER, _cover("navy")))

        await _after_the_hold()

        assert source.metadata["title"] == "Says"
        assert source.metadata["album_art_url"], source.metadata
        assert source.metadata["album_art_width"] == 600

    async def test_a_drifting_sender_never_takes_the_player_off_the_screen(self, airplay):
        """The same shape over a run, judged the way the screen judges it.

        Every publish is replayed through `useRichDisplay`'s airplay arm — title
        AND artist AND a cover over 300 px — and none of them may take it from
        true back to false. That is the whole defect class: a display field
        emptied while its replacement is in flight does not correct the piece of
        UI it feeds, it removes it. Over every published state, not the last:
        the last one was always right, which is how this survived the fix that
        named it.
        """
        source, feed = airplay
        await feed(_bundle(RTP_A, "Says"), _picture(RTP_A, _cover("navy")))
        published = _every_publish(source)

        # The sender re-sends the same track under a fresh stamp, and its
        # picture lands a few packets ahead of it — the measured iOS order.
        await feed(_bundle(RTP_A_LATER, "Says"))
        await feed(_picture(str(int(RTP_A_LATER) + 1056), _cover("navy")))
        await _after_the_hold()

        def rich(m):
            return bool(m.get("title")) and bool(m.get("artist")) and (
                m.get("album_art_width") or 0) > 300

        assert published, "the run published nothing to judge"
        unmounts = [
            (before, after)
            for before, after in zip(published, published[1:])
            if rich(before) and not rich(after)
        ]
        assert not unmounts, unmounts

    async def test_two_tracks_off_one_album_keep_their_cover(self, airplay):
        """The same image byte for byte, so the md5 dedupe in `_on_artwork`
        short-circuits — but the picture that changed nothing still moved which
        track the cover belongs to. Recorded after the dedupe, the second track
        would drop to its glyph on an album that has a cover."""
        source, feed = airplay
        sleeve = _cover("navy")
        await feed(_bundle(RTP_A, "Says"), _picture(RTP_A, sleeve))
        first = source.metadata["album_art_url"]

        await feed(_bundle(RTP_B, "Says (Live)"), _picture(RTP_B, sleeve))

        assert source.metadata["title"] == "Says (Live)"
        assert source.metadata["album_art_url"] == first

    async def test_the_cover_is_held_while_the_next_one_is_still_in_flight(self, airplay):
        """A track change must not blank the cover for the millisecond before
        its own arrives.

        The tags and the picture are two SET_PARAMETER requests in no
        guaranteed order, so the tags-first order leaves the new stamp
        unpaired. Publishing that gap sends a state with no `album_art_url`,
        and `useRichDisplay`'s untrusted-sender gate reads a missing
        `album_art_width` as "no real cover from this sender": the frontend
        swaps AudioPlayerFull for the AudioSourceStatus card and back within
        ~30 ms, which is visible as the player animating itself out and in.
        Measured on a macOS sender, on every track change *and* every transport
        action, since the sender re-sends its bundle under a fresh rtptime.

        The window is what is asserted here; that it expires is asserted by
        `test_a_track_that_sends_no_cover_shows_none`.
        """
        source, feed = airplay
        await feed(_bundle(RTP_A, "Says"), _picture(RTP_A, _cover("navy")))
        held = source.metadata["album_art_url"]

        await feed(_bundle(RTP_B, "Toilet Brush"))

        assert source.metadata["title"] == "Toilet Brush"
        assert source.metadata["album_art_url"] == held
        assert source.metadata["album_art_width"] == 600

        # And the track's own picture, when it lands, takes the hold's place.
        await feed(_picture(RTP_B, _cover("crimson", size=450)))

        assert source.metadata["album_art_url"] != held
        assert source.metadata["album_art_width"] == 450

    async def test_a_cover_arriving_first_does_not_blank_the_one_on_screen(self, airplay):
        """The mirror of the test above, and the same flicker.

        The order is the sender's, so the picture can be the one that arrives
        first — and then the new stamp is on the cover while the title on
        screen is still the previous track's. A publish judging on the stamps
        alone found them unequal and dropped `album_art_url` from that state,
        which `useRichDisplay`'s untrusted-sender gate reads as "this sender
        pushes no real cover": AudioPlayerFull swapped for the AudioSourceStatus
        card and back, the player animating itself out and in.

        Asserted over every published state, not the last one: the last one was
        always right, which is why the tags-first fix left this half standing.
        """
        source, feed = airplay
        await feed(_bundle(RTP_A, "Says"), _picture(RTP_A, _cover("navy")))
        published = _every_publish(source)

        await feed(
            _picture(RTP_B, _cover("crimson", size=450)),
            _bundle(RTP_B, "Toilet Brush"),
        )

        assert published, "the track change published nothing to judge"
        assert all(m.get("album_art_width") for m in published), published
        assert source.metadata["title"] == "Toilet Brush"
        assert source.metadata["album_art_width"] == 450

    async def test_a_cover_arriving_first_off_one_album_does_not_blank_it_either(
        self, airplay
    ):
        """Same order, through the md5 dedupe: the identical image re-sent under
        a new stamp takes the early return, which published its own coverless
        state on the way past."""
        source, feed = airplay
        sleeve = _cover("navy")
        await feed(_bundle(RTP_A, "Says"), _picture(RTP_A, sleeve))
        published = _every_publish(source)

        await feed(_picture(RTP_B, sleeve), _bundle(RTP_B, "Says (Live)"))

        assert published, "the track change published nothing to judge"
        assert all(m.get("album_art_width") for m in published), published
        assert source.metadata["title"] == "Says (Live)"

    async def test_a_bundle_under_a_new_stamp_owns_every_tag(self, airplay):
        """Nothing belonging to the previous track is published as this one's.

        A bundle carries only the DAAP tags the sender put in it, so a track
        sent without an `asar` used to be published wearing the previous
        track's artist — under the right title, for the whole of it. The stamp
        settles it, the same stamp the cover is paired by: a new one is a
        different track and owns its absences too. The status card is then the
        right screen, and it is the one the gate already picks for a sender
        that publishes a bare title.
        """
        source, feed = airplay
        await feed(_bundle(RTP_A, "Says", artist="Nils Frahm"))

        await feed(_bundle(RTP_B, "Untitled recording", artist=None))

        assert source.metadata["title"] == "Untitled recording"
        assert not source.metadata.get("artist")

    async def test_a_bundle_under_the_stamp_on_screen_amends_it(self, airplay):
        """The other half of the same rule, and what stops it stripping a track
        that is already right: a bundle re-sent under the stamp on screen is an
        amendment to that track, not a new one, so the tags it does not carry
        stay as they were."""
        source, feed = airplay
        await feed(_bundle(RTP_A, "Says", artist="Nils Frahm"))

        await feed(_bundle(RTP_A, "Says", artist=None))

        assert source.metadata["artist"] == "Nils Frahm"

    async def test_a_sender_without_rtp_info_keeps_what_it_had(self, airplay):
        """shairport-sync tolerates a sender that sends no RTP-Info and sends
        mdst/pcst empty, which leaves nothing to pair on. Hiding every cover
        there would be worse than carrying one: documented, not worked around."""
        source, feed = airplay
        await feed(_bundle(None, "Says"), _picture(None, _cover("navy")))

        await feed(_bundle(None, "Toilet Brush"))

        assert source.metadata["title"] == "Toilet Brush"
        assert "album_art_url" in source.metadata


# =============================================================================
# The rest of the pipe: session control, progress, connection — and the loop
# that reads them.
#
# Same principle as the cover pairing above: the outside world is shairport-sync
# writing to a FIFO, and it is represented by exactly that byte stream. Nothing
# here mocks a callback the source owns.
#
# Danger specific to this file: `/tmp/shairport-sync-metadata` is the LIVE
# pipe's path. shairport-sync is stopped whenever the AirPlay source is off, so
# a test that reached `_ensure_metadata_pipe` unguarded would CREATE the
# service's pipe. Every test here puts it under `tmp_path`.
# =============================================================================
import asyncio
import contextlib
import os
import time
from unittest.mock import AsyncMock, Mock, patch

from backend.sources.airplay.metadata_reader import MetadataReader as _Reader
from backend.sources.airplay.source import (
    AIRPLAY_SAMPLE_RATE,
    POSITION_JUMP_TOLERANCE_MS,
)

LIVE_PIPE = "/tmp/shairport-sync-metadata"


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
def wired(tmp_path):
    """The real source behind the real reader, with every callback connected.

    The fixture above wires three; the session, progress and connection arms are
    only reachable with all seven, which is why none of them had ever run.
    """
    source = AirPlaySource(config={"metadata_pipe": str(tmp_path / "pipe")})
    source._bg = Mock()
    source._bg.spawn = Mock(side_effect=lambda coro, **kw: coro.close())
    reader = _Reader(
        pipe_path=str(tmp_path / "pipe"),
        on_metadata=source._on_metadata_update,
        on_play_state=source._on_play_state,
        on_artwork=source._on_artwork,
        on_progress=source._on_progress,
        on_client_name=source._on_client_name,
        on_connection=source._on_connection,
    )

    async def feed(*chunks: str) -> None:
        await reader._process_buffer("".join(chunks).encode())

    return source, feed


def _frames(seconds: float) -> int:
    return int(seconds * AIRPLAY_SAMPLE_RATE)


def _progress(start_s: float, current_s: float, end_s: float) -> str:
    """`prgr` as rtsp.c writes it: three RTP frame counts separated by slashes."""
    payload = f"{_frames(start_s)}/{_frames(current_s)}/{_frames(end_s)}"
    return _item("ssnc", "prgr", payload.encode())


class TestSessionControl:
    """`pbeg`/`prsm`/`pfls`/`pend` are the only thing that moves is_playing.

    Read the module docstring before adding to this: a sender-side pause is
    invisible on every channel the receiver has, so `pfls` and `pend` in
    practice only arrive when the output is torn down.
    """

    async def test_play_begin_marks_the_session_playing_and_connected(self, wired):
        source, feed = wired
        await feed(_item("ssnc", "pbeg"))
        assert source._is_playing is True
        assert source._device_connected is True

    async def test_resume_is_the_same_as_begin(self, wired):
        source, feed = wired
        await feed(_item("ssnc", "pbeg"), _item("ssnc", "pfls"), _item("ssnc", "prsm"))
        assert source._is_playing is True

    async def test_flush_pauses_without_disconnecting_the_device(self, wired):
        source, feed = wired
        await feed(_item("ssnc", "pbeg"), _item("ssnc", "pfls"))
        assert source._is_playing is False
        assert source._device_connected is True

    async def test_play_end_is_not_a_disconnection(self, wired):
        """`pend` means the stream ended; the sender is still there until `disc`.
        Treated as a disconnect, the source would drop to READY while the phone
        still has it selected."""
        source, feed = wired
        await feed(_item("ssnc", "pbeg"), _item("ssnc", "pend"))
        assert source._is_playing is False
        assert source._device_connected is True

    async def test_the_published_metadata_carries_the_play_state(self, wired):
        source, feed = wired
        await feed(_bundle(RTP_A, "Says"), _item("ssnc", "pbeg"))
        assert source._metadata["is_playing"] is True
        await feed(_item("ssnc", "pfls"))
        assert source._metadata["is_playing"] is False

    async def test_the_idle_timer_is_armed_on_pause_and_on_stop(self, wired):
        """It is the only thing that returns the source to READY: a sender that
        stops without disconnecting would otherwise hold ACTIVE for ever, and
        IDLE_STATES excludes ACTIVE so the 12 h sweep never gets it."""
        source, feed = wired
        source._start_pause_timer = Mock()
        source._cancel_pause_timer = Mock()

        await feed(_item("ssnc", "pfls"))
        await feed(_item("ssnc", "pend"))
        assert source._start_pause_timer.call_count == 2

    async def test_playing_again_cancels_the_idle_timer(self, wired):
        source, feed = wired
        source._cancel_pause_timer = Mock()
        await feed(_item("ssnc", "pbeg"))
        source._cancel_pause_timer.assert_called_once()


class TestConnectionEvents:
    """`conn`/`disc` are AirPlay 2's own events, sent as soon as a client picks
    this output — before any audio flows — and when it lets go."""

    async def test_a_client_selecting_the_output_marks_it_connected(self, wired):
        source, feed = wired
        await feed(_item("ssnc", "conn", b"192.168.1.42"))
        assert source._device_connected is True
        assert source._is_playing is False, "selecting an output is not playing"

    async def test_a_connection_event_with_no_address_still_counts(self, wired):
        source, feed = wired
        await feed(_item("ssnc", "conn"))
        assert source._device_connected is True

    async def test_a_disconnection_clears_the_session_completely(self, wired):
        """Anything left behind is what the next sender inherits: the previous
        phone's track and cover on screen before it has sent its own."""
        source, feed = wired
        await feed(_item("ssnc", "conn", b"192.168.1.42"),
                   _bundle(RTP_A, "Says"),
                   _picture(RTP_A, _cover("navy")),
                   _item("ssnc", "pbeg"))
        assert source._metadata.get("title") == "Says"
        assert source.get_artwork() is not None

        await feed(_item("ssnc", "disc", b"192.168.1.42"))

        assert source._device_connected is False
        assert source._is_playing is False
        # `_metadata` is emptied and then the publish writes the two transport
        # flags back into it, so the check is that no *track* survives.
        assert not {"title", "artist", "album", "album_art_url"} & set(source._metadata)
        assert source.get_artwork() is None
        assert source._client_name is None

    async def test_a_late_disconnect_does_not_tear_down_the_sender_on_air(self, wired):
        """A `disc` names a sender, and it can arrive after that sender is gone.

        Measured on the unit 2026-09-03: a phone let go at 19:12:13, the next
        sender was on air at 19:12:18, and the first one's `disc` landed at
        19:13:13 — a minute into someone else's session. Applied blind it
        emptied the live one: title, artist, cover and client name all cleared
        under playing audio. `snam` is sent once per session, so the name never
        came back and the card read a bare "AirPlay" instead of the sender for
        the rest of it.
        """
        source, feed = wired
        await feed(_item("ssnc", "conn", b"192.168.1.42"),
                   _item("ssnc", "snam", "iPhone de Léo".encode()),
                   _item("ssnc", "disc", b"192.168.1.42"))
        await feed(_item("ssnc", "conn", b"192.168.1.77"),
                   _item("ssnc", "snam", "Mac mini de Léo".encode()),
                   _bundle(RTP_A, "Says"),
                   _picture(RTP_A, _cover("navy")),
                   _item("ssnc", "pbeg"))

        await feed(_item("ssnc", "disc", b"192.168.1.42"))

        assert source._client_name == "Mac mini de Léo"
        assert source._device_connected is True
        assert source._is_playing is True
        assert source._metadata.get("title") == "Says"
        assert source.get_artwork() is not None

    async def test_a_disconnect_with_no_address_still_ends_the_session(self, wired):
        """Nothing tells two senders apart then, so the teardown stands — the
        trade that keeps a sender shairport reports no address for from holding
        the source ACTIVE for ever."""
        source, feed = wired
        await feed(_item("ssnc", "conn", b"192.168.1.42"),
                   _bundle(RTP_A, "Says"),
                   _item("ssnc", "pbeg"))

        await feed(_item("ssnc", "disc"))

        assert source._device_connected is False
        assert not {"title", "artist"} & set(source._metadata)

    async def test_the_client_name_is_published_as_the_source_label(self, wired):
        """`snam` is X-Apple-Client-Name; it is what the source bar shows
        instead of a bare "AirPlay"."""
        source, feed = wired
        await feed(_item("ssnc", "snam", "Mac mini de Léo".encode()))
        assert source._client_name == "Mac mini de Léo"
        assert source._device_connected is True


class TestProgress:
    """`prgr` carries three RTP frame counts. It arrives every 5-15 s, not
    continuously, so what is stored is a snapshot plus the time it was taken."""

    async def test_a_snapshot_becomes_a_position_and_a_duration_in_ms(self, wired):
        source, feed = wired
        await feed(_item("ssnc", "pbeg"), _progress(0, 30, 300))
        assert source._duration_ms == 300_000
        assert abs(source._position_ms - 30_000) < 50

    async def test_a_track_that_does_not_start_at_zero_is_measured_from_its_start(self, wired):
        """`start` is the track's own first frame, not the session's: a stream
        running for an hour has huge frame counts, and reading `current` as an
        absolute would show the position as the session's age."""
        source, feed = wired
        await feed(_item("ssnc", "pbeg"), _progress(3600, 3610, 3900))
        assert source._duration_ms == 300_000
        assert abs(source._position_ms - 10_000) < 50

    async def test_a_snapshot_that_makes_no_sense_is_ignored(self, wired):
        """`end <= start` is a zero-length track; taken at face value it makes
        the duration 0 and every position clamp to it."""
        source, feed = wired
        await feed(_item("ssnc", "pbeg"), _progress(0, 30, 300))
        await feed(_progress(500, 500, 500))
        assert source._duration_ms == 300_000

    async def test_a_jump_is_broadcast_at_once(self, wired):
        """A track change or a seek is an arbitrarily large move that the
        clients' local interpolation cannot guess."""
        source, feed = wired
        source.broadcast_position_update = Mock()
        await feed(_item("ssnc", "pbeg"), _progress(0, 0, 300))
        source.broadcast_position_update.reset_mock()

        await feed(_progress(0, 200, 300))
        source.broadcast_position_update.assert_called_once()
        assert source.broadcast_position_update.call_args[0][0] > 199_000

    async def test_a_snapshot_that_only_confirms_the_interpolation_is_not_broadcast(self, wired):
        """A sender that emits `prgr` often would otherwise flood every
        connected client with values they had already worked out."""
        source, feed = wired
        await feed(_item("ssnc", "pbeg"), _progress(0, 100, 300))
        source.broadcast_position_update = Mock()

        within = (POSITION_JUMP_TOLERANCE_MS / 1000) / 2
        await feed(_progress(0, 100 + within, 300))
        source.broadcast_position_update.assert_not_called()

    async def test_the_position_ages_while_the_track_plays(self, wired):
        """`prgr` is 5-15 s apart, so between two of them the position has to be
        derived from the clock or the bar stops."""
        source, feed = wired
        await feed(_item("ssnc", "pbeg"), _progress(0, 30, 300))
        taken = source._current_position_ms()

        source._position_at -= 5.0            # five seconds of wall clock
        assert source._current_position_ms() - taken >= 4900

    async def test_the_aged_position_never_runs_past_the_track(self, wired):
        source, feed = wired
        await feed(_item("ssnc", "pbeg"), _progress(0, 30, 300))
        source._position_at -= 10_000.0
        assert source._current_position_ms() == source._duration_ms

    async def test_a_paused_position_keeps_what_it_had_aged_to(self, wired):
        """The clock is moved on between the snapshot and the pause on purpose:
        with the two at the same instant, a freeze that forgot to bank the aged
        value would look identical to one that banked it."""
        source, feed = wired
        await feed(_item("ssnc", "pbeg"), _progress(0, 30, 300))
        source._position_at -= 5.0            # five seconds of playing

        await feed(_item("ssnc", "pfls"))

        assert source._position_at is None
        frozen = source._current_position_ms()
        assert frozen >= 34_900, \
            f"the pause discarded the five seconds that had played: {frozen} ms"
        time.sleep(0.05)
        assert source._current_position_ms() == frozen

    async def test_playing_again_resumes_ageing_from_the_frozen_point(self, wired):
        source, feed = wired
        await feed(_item("ssnc", "pbeg"), _progress(0, 30, 300),
                   _item("ssnc", "pfls"))
        frozen = source._position_ms
        await feed(_item("ssnc", "prsm"))

        assert source._position_at is not None
        assert source._position_ms == frozen, "resuming restarted the track"

    async def test_a_snapshot_taken_while_paused_does_not_start_ageing(self, wired):
        source, feed = wired
        await feed(_item("ssnc", "pfls"), _progress(0, 30, 300))
        assert source._position_at is None

    async def test_a_malformed_progress_payload_is_ignored(self, wired):
        source, feed = wired
        await feed(_item("ssnc", "pbeg"), _progress(0, 30, 300))
        await feed(_item("ssnc", "prgr", b"not/a/number"))
        assert source._duration_ms == 300_000

    async def test_a_progress_payload_of_the_wrong_shape_is_ignored(self, wired):
        source, feed = wired
        await feed(_item("ssnc", "pbeg"), _progress(0, 30, 300))
        await feed(_item("ssnc", "prgr", b"12345"))
        assert source._duration_ms == 300_000


class TestThePositionTicker:
    """Live clients interpolate locally; this only bounds how stale a *new*
    connection's initial_state can be — a page refresh mid-track seeds its bar
    from `system_state.metadata["position"]`."""

    async def test_it_pushes_the_aged_position_while_a_track_plays(self, wired):
        source, feed = wired
        await feed(_item("ssnc", "pbeg"), _progress(0, 30, 300))
        source.broadcast_position_update = Mock()

        with patch("backend.sources.airplay.source.POSITION_TICK_SECONDS", 0.01):
            source._start_position_ticker()
            for _ in range(200):
                await asyncio.sleep(0.01)
                if source.broadcast_position_update.call_count >= 2:
                    break
            source._cancel_position_ticker()

        assert source.broadcast_position_update.call_count >= 2
        position, duration = source.broadcast_position_update.call_args[0]
        assert duration == 300_000
        assert position >= 30_000

    async def test_it_stays_quiet_while_nothing_is_playing(self, wired):
        source, feed = wired
        await feed(_item("ssnc", "pbeg"), _progress(0, 30, 300), _item("ssnc", "pfls"))
        source.broadcast_position_update = Mock()

        with patch("backend.sources.airplay.source.POSITION_TICK_SECONDS", 0.01):
            source._start_position_ticker()
            await asyncio.sleep(0.1)
            source._cancel_position_ticker()

        source.broadcast_position_update.assert_not_called()

    async def test_it_stays_quiet_for_a_track_of_unknown_length(self, wired):
        """A sender that never sends `prgr` leaves duration at 0; broadcasting
        that would seed every new client's bar with a zero-length track."""
        source, feed = wired
        await feed(_item("ssnc", "pbeg"))
        source.broadcast_position_update = Mock()

        with patch("backend.sources.airplay.source.POSITION_TICK_SECONDS", 0.01):
            source._start_position_ticker()
            await asyncio.sleep(0.1)
            source._cancel_position_ticker()

        source.broadcast_position_update.assert_not_called()

    async def test_starting_it_twice_cancels_the_first(self, wired):
        """`_do_restart` starts it again. Two live tickers double every position
        broadcast, and only the second is ever cancelled — the first outlives
        the source and goes on publishing into a stopped session."""
        source, _feed = wired
        source._start_position_ticker()
        first = source._position_task
        source._start_position_ticker()
        assert source._position_task is not first

        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(first, 2.0)
        assert first.cancelled(), "the previous ticker was left running"
        source._cancel_position_ticker()

    async def test_cancelling_it_when_none_runs_is_harmless(self, wired):
        source, _feed = wired
        source._cancel_position_ticker()
        assert source._position_task is None


class TestTheWireFormat:
    """`_parse_item` reads what rtsp.c writes. Its refusals matter because a
    malformed item must not take the reader down with it — the loop that feeds
    it is the only thing that ever hears from the sender."""

    async def test_an_item_missing_its_code_is_dropped(self, wired):
        source, feed = wired
        await feed("<item><type>73736e63</type><length>0</length></item>",
                   _item("ssnc", "pbeg"))
        assert source._is_playing is True, "a malformed item stopped the stream"

    async def test_a_declared_length_of_zero_means_no_payload(self, wired):
        """rtsp.c writes `<length>0</length>` and no data element when the
        sender gave it no rtptime — that is how an un-stamped bundle arrives."""
        source, feed = wired
        await feed(_bundle(None, "Says"))
        assert source._metadata.get("title") == "Says"
        assert source._track_id is None

    async def test_a_payload_that_is_not_base64_is_treated_as_absent(self, wired):
        source, feed = wired
        await feed("<item><type>73736e63</type><code>736e616d</code>"
                   "<length>4</length><data encoding=\"base64\">!!!not!!!</data></item>",
                   _item("ssnc", "pbeg"))
        assert source._is_playing is True

    async def test_a_type_that_is_not_hex_text_is_carried_through_verbatim(self):
        """`_hex_to_str` falls back to the raw string rather than raising, so an
        item the receiver does not understand is skipped and not fatal."""
        from backend.sources.airplay.metadata_reader import _hex_to_str
        assert _hex_to_str("73736e63") == "ssnc"
        assert _hex_to_str("zzzz") == "zzzz"
        assert _hex_to_str("ffff") == "ffff"

    async def test_a_split_item_is_completed_by_the_next_read(self, wired, tmp_path):
        """The pipe hands over 64 KiB at a time, so an item is routinely cut in
        half. Dropping the remainder loses one tag in every bufferful."""
        reader = _Reader(str(tmp_path / "p"), on_metadata=AsyncMock(),
                         on_play_state=AsyncMock(), on_artwork=AsyncMock())
        whole = _item("core", "minm", b"Says").encode()
        cut = len(whole) // 2

        left = await reader._process_buffer(whole[:cut])
        assert left == whole[:cut], "an incomplete item was thrown away"
        assert await reader._process_buffer(left + whole[cut:]) == b""
        assert reader._pending_metadata["title"] == "Says"

    async def test_the_tags_a_sender_can_send_all_land(self, wired):
        source, feed = wired
        await feed(
            _item("ssnc", "mdst", RTP_A.encode()),
            _item("core", "minm", b"Says"),
            _item("core", "asar", b"Nils Frahm"),
            _item("core", "asal", b"Spaces"),
            _item("core", "asgn", b"Modern Classical"),
            _item("ssnc", "mden", RTP_A.encode()),
        )
        assert source._metadata["title"] == "Says"
        assert source._metadata["artist"] == "Nils Frahm"
        assert source._metadata["album"] == "Spaces"

    async def test_a_bundle_that_gathered_nothing_is_not_published_as_a_track(self, wired):
        """An empty mdst/mden pair is routine. The title survives it either way
        — `_on_metadata_update` falls back to what is already there — so what
        actually separates the two is the PAIRING: published, the empty bundle
        stamps the session with a new rtptime, and the cover that belongs to the
        track still on screen is dropped for the rest of it.
        """
        source, feed = wired
        await feed(_bundle(RTP_A, "Says"), _picture(RTP_A, _cover("navy")))
        assert source._metadata.get("album_art_url"), "no cover to lose"

        await feed(_item("ssnc", "mdst", RTP_B.encode()),
                   _item("ssnc", "mden", RTP_B.encode()))

        assert source._metadata["title"] == "Says"
        assert source._track_id == RTP_A, "an empty bundle re-stamped the track"
        assert source._metadata.get("album_art_url"), \
            "the cover was unpaired by a bundle that carried no track"

    async def test_a_picture_with_no_bytes_is_not_published_as_a_cover(self, wired):
        source, feed = wired
        await feed(_bundle(RTP_A, "Says"), _item("ssnc", "pcst", RTP_A.encode()),
                   _item("ssnc", "PICT"))
        assert source.get_artwork() is None


class TestTheReadLoop:
    """The loop that opens the pipe and keeps it open for the session.

    shairport-sync closes its write end between sessions, so reopening is the
    normal path, not the error path.
    """

    async def test_it_reopens_the_pipe_when_the_writer_lets_go(self, tmp_path):
        pipe = tmp_path / "pipe"
        os.mkfifo(pipe)
        seen = []
        reader = _Reader(str(pipe), on_metadata=AsyncMock(),
                         on_play_state=AsyncMock(side_effect=lambda s: seen.append(s)),
                         on_artwork=AsyncMock())
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
                    if len(seen) >= session + 1:
                        break
        finally:
            await asyncio.wait_for(reader.stop(), 2.0)

        assert len(seen) >= 2, "the reader did not come back after the writer left"

    async def test_a_pipe_that_is_not_there_yet_is_waited_for(self, tmp_path, caplog):
        """shairport-sync creates it on first start; the reader may be up first,
        and giving up here means no metadata for the whole session."""
        pipe = tmp_path / "not-yet"
        reader = _Reader(str(pipe), on_metadata=AsyncMock(),
                         on_play_state=AsyncMock(), on_artwork=AsyncMock())
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
        reader = _Reader(str(pipe), on_metadata=AsyncMock(),
                         on_play_state=AsyncMock(), on_artwork=AsyncMock())
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
        reader = _Reader(str(tmp_path / "p"), on_metadata=AsyncMock(),
                         on_play_state=AsyncMock(), on_artwork=AsyncMock())
        await reader.start()
        await asyncio.wait_for(reader.stop(), 2.0)
        await asyncio.wait_for(reader.stop(), 2.0)
        assert reader._running is False

    async def test_stopping_one_that_never_started_is_harmless(self, tmp_path):
        reader = _Reader(str(tmp_path / "p"), on_metadata=AsyncMock(),
                         on_play_state=AsyncMock(), on_artwork=AsyncMock())
        await asyncio.wait_for(reader.stop(), 2.0)


class TestTheMetadataPipe:
    """`_ensure_metadata_pipe` runs on every start. It must never be fatal:
    shairport-sync creates the pipe itself, so failing here would stop a source
    that would have worked."""

    async def test_a_missing_pipe_is_created(self, tmp_path):
        source = AirPlaySource(config={"metadata_pipe": str(tmp_path / "pipe")})
        await source._ensure_metadata_pipe()
        assert (tmp_path / "pipe").is_fifo()

    async def test_an_existing_pipe_is_left_alone(self, tmp_path):
        pipe = tmp_path / "pipe"
        os.mkfifo(pipe)
        before = pipe.stat().st_ino
        source = AirPlaySource(config={"metadata_pipe": str(pipe)})
        await source._ensure_metadata_pipe()
        assert pipe.stat().st_ino == before

    async def test_a_directory_we_cannot_write_only_warns(self, tmp_path, caplog, monkeypatch):
        """/tmp is world-writable on the appliance, but a hardened unit is not,
        and shairport-sync running as its own user creates the pipe anyway."""
        source = AirPlaySource(config={"metadata_pipe": str(tmp_path / "pipe")})
        monkeypatch.setattr(os, "mkfifo", Mock(side_effect=PermissionError(13, "denied")))

        with caplog.at_level("WARNING", logger=source._logger.name):
            await source._ensure_metadata_pipe()
        assert any("shairport-sync" in r.message for r in caplog.records)

    async def test_a_pipe_created_between_the_check_and_the_call_is_fine(self, tmp_path, monkeypatch):
        """shairport-sync may create it in that window; the race is expected."""
        source = AirPlaySource(config={"metadata_pipe": str(tmp_path / "pipe")})
        monkeypatch.setattr(os, "mkfifo", Mock(side_effect=FileExistsError()))
        await source._ensure_metadata_pipe()      # must not raise


class TestArtworkHandoff:
    async def test_the_cover_is_served_with_the_type_it_arrived_as(self, wired):
        source, feed = wired
        await feed(_bundle(RTP_A, "Says"), _picture(RTP_A, _cover("navy")))
        data, mime = source.get_artwork()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        assert mime == "image/png"

    async def test_a_jpeg_cover_is_labelled_jpeg(self, wired):
        source, feed = wired
        jpeg = b"\xff\xd8\xff\xe0" + b"J" * 2048
        await feed(_bundle(RTP_A, "Says"), _picture(RTP_A, jpeg))
        assert source.get_artwork()[1] == "image/jpeg"

    async def test_no_cover_yet_is_no_answer_at_all(self, wired):
        source, _feed = wired
        assert source.get_artwork() is None

    async def test_the_same_image_arriving_for_a_new_track_republishes_it(self, wired):
        """Two tracks off one album send identical bytes. The dedupe skips the
        decode, but the pairing moved — without the republish the cover belongs
        to a track that is no longer on screen."""
        source, feed = wired
        cover = _cover("navy")
        await feed(_bundle(RTP_A, "One"), _picture(RTP_A, cover))
        publishes = []
        source._update_connection_state = Mock(
            side_effect=lambda: publishes.append(source._artwork_id))

        await feed(_bundle(RTP_B, "Two"), _picture(RTP_B, cover))
        assert RTP_B in publishes, "the cover stayed stamped for the previous track"


class TestTheArtworkRoute:
    """`GET /api/airplay/artwork` — the cover the pipe delivered, served to the
    player. `metadata.album_art_url` points at it with a `?v=<hash>` so the
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

    async def test_the_cover_is_served_with_the_type_it_arrived_as(self, wired):
        source, feed = wired
        await feed(_bundle(RTP_A, "Says"), _picture(RTP_A, _cover("navy")))

        resp = self._client(source).get("/api/airplay/artwork")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        assert resp.content[:8] == b"\x89PNG\r\n\x1a\n"

    async def test_it_is_cached_privately_and_immutably(self, wired):
        """The URL carries the content hash, so the bytes behind it never
        change; `private` keeps a shared proxy from serving one household's
        cover to another."""
        source, feed = wired
        await feed(_bundle(RTP_A, "Says"), _picture(RTP_A, _cover("navy")))

        cache = self._client(source).get("/api/airplay/artwork").headers["cache-control"]
        assert "private" in cache and "immutable" in cache

    def test_a_sender_that_pushed_no_cover_is_a_404_and_not_an_error(self, caplog):
        """Plenty of senders push none. Logged at ERROR this would raise the
        WebSocket error banner on an ordinary AirPlay session."""
        source = AirPlaySource()
        with caplog.at_level("ERROR", logger="backend.sources.airplay.routes"):
            resp = self._client(source).get("/api/airplay/artwork")

        assert resp.status_code == 404
        assert caplog.records == []


class TestLifecycle:
    """`_do_start` / `_do_restart` — both build the reader and start the ticker,
    and `_do_restart` is what releases an AirPlay session so the sender lets go.
    """

    @staticmethod
    def _source(tmp_path):
        source = AirPlaySource(config={"metadata_pipe": str(tmp_path / "pipe")})
        source._bg = Mock()
        source._bg.spawn = Mock(side_effect=lambda coro, **kw: coro.close())
        source._start_service_and_wait = AsyncMock(return_value=True)
        source._restart_service_and_wait = AsyncMock(return_value=True)
        source._load_auto_stop_config = AsyncMock()
        source._update_connection_state = Mock()
        return source

    async def test_a_start_brings_up_the_reader_on_the_configured_pipe(self, tmp_path):
        source = self._source(tmp_path)
        try:
            assert await asyncio.wait_for(source._do_start(), 2.0) is True
            assert source._metadata_reader is not None
            assert source._metadata_reader._pipe_path == str(tmp_path / "pipe")
            assert (tmp_path / "pipe").is_fifo()
            assert source._position_task is not None
        finally:
            await asyncio.wait_for(source._cleanup(), 2.0)

    async def test_every_callback_is_wired_or_the_arm_behind_it_is_dead(self, tmp_path):
        """Four of the seven are optional in the reader's signature, and an
        un-wired one silently disables its whole branch — progress, the client
        name, and the AirPlay 2 connection events all failed that way."""
        source = self._source(tmp_path)
        try:
            await asyncio.wait_for(source._do_start(), 2.0)
            r = source._metadata_reader
            assert r._on_metadata == source._on_metadata_update
            assert r._on_play_state == source._on_play_state
            assert r._on_artwork == source._on_artwork
            assert r._on_progress == source._on_progress
            assert r._on_client_name == source._on_client_name
            assert r._on_connection == source._on_connection
        finally:
            await asyncio.wait_for(source._cleanup(), 2.0)

    async def test_a_service_that_will_not_start_builds_no_reader(self, tmp_path):
        source = self._source(tmp_path)
        source._start_service_and_wait = AsyncMock(return_value=False)
        assert await asyncio.wait_for(source._do_start(), 2.0) is False
        assert source._metadata_reader is None

    async def test_a_start_that_blows_up_tears_down_rather_than_half_starting(self, tmp_path):
        source = self._source(tmp_path)
        source._load_auto_stop_config = AsyncMock(side_effect=RuntimeError("settings gone"))
        source._cleanup = AsyncMock()

        assert await asyncio.wait_for(source._do_start(), 2.0) is False
        source._cleanup.assert_awaited_once()

    async def test_a_restart_replaces_the_reader_instead_of_stacking_one(self, tmp_path):
        """Two readers on one pipe split the byte stream between them and every
        item is parsed by whichever got that chunk."""
        source = self._source(tmp_path)
        try:
            await asyncio.wait_for(source._do_start(), 2.0)
            first = source._metadata_reader

            assert await asyncio.wait_for(source._do_restart(), 2.0) is True
            assert source._metadata_reader is not first
            assert first._running is False
        finally:
            await asyncio.wait_for(source._cleanup(), 2.0)

    async def test_a_restart_clears_the_previous_session(self, tmp_path):
        source = self._source(tmp_path)
        source._metadata = {"title": "Says"}
        source._device_connected = True
        source._artwork_data = b"x"
        try:
            await asyncio.wait_for(source._do_restart(), 2.0)
            assert source._device_connected is False
            assert source.get_artwork() is None
        finally:
            await asyncio.wait_for(source._cleanup(), 2.0)

    async def test_a_service_that_will_not_restart_gives_up_before_the_reader(self, tmp_path):
        source = self._source(tmp_path)
        source._restart_service_and_wait = AsyncMock(return_value=False)
        assert await asyncio.wait_for(source._do_restart(), 2.0) is False
        assert source._metadata_reader is None

    async def test_cleanup_stops_the_reader_and_the_ticker(self, tmp_path):
        """Left running, the ticker goes on broadcasting a position for a source
        that is no longer active, and the reader holds the pipe open."""
        source = self._source(tmp_path)
        await asyncio.wait_for(source._do_start(), 2.0)
        reader, ticker = source._metadata_reader, source._position_task

        await asyncio.wait_for(source._cleanup(), 2.0)

        assert source._metadata_reader is None
        assert reader._running is False
        # cancel() only requests it; the task observes it on its next turn.
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(ticker, 2.0)
        assert ticker.cancelled()
        assert source._position_task is None
