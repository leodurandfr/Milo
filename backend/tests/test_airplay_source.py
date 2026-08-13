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
from backend.sources.airplay.source import AirPlaySource

# Two rtptimes, as the sender sends them: an ASCII decimal string.
RTP_A = "3222108659"
RTP_B = "3222285731"


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


def _bundle(rtptime: Optional[str], title: str, artist: str = "Nils Frahm") -> str:
    """A track's tags, bracketed by mdst/mden as rtsp.c brackets them."""
    stamp = rtptime.encode() if rtptime else None
    return "".join([
        _item("ssnc", "mdst", stamp),
        _item("core", "minm", title.encode()),
        _item("core", "asar", artist.encode()),
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
def airplay():
    """The real source behind the real reader, fed by the caller."""
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
        the full-screen player draws for the whole of it."""
        source, feed = airplay
        await feed(_bundle(RTP_A, "Says"), _picture(RTP_A, _cover("navy")))

        await feed(_bundle(RTP_B, "Toilet Brush"))

        assert source.metadata["title"] == "Toilet Brush"
        assert "album_art_url" not in source.metadata
        assert "album_art_width" not in source.metadata

    async def test_a_cover_stamped_for_the_previous_track_is_not_adopted(self, airplay):
        """The stamp is the whole rule: a picture in hand is not this track's
        merely because it is the most recent one."""
        source, feed = airplay
        await feed(_picture(RTP_A, _cover("navy")))

        await feed(_bundle(RTP_B, "Toilet Brush"))

        assert "album_art_url" not in source.metadata

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

    async def test_a_sender_without_rtp_info_keeps_what_it_had(self, airplay):
        """shairport-sync tolerates a sender that sends no RTP-Info and sends
        mdst/pcst empty, which leaves nothing to pair on. Hiding every cover
        there would be worse than carrying one: documented, not worked around."""
        source, feed = airplay
        await feed(_bundle(None, "Says"), _picture(None, _cover("navy")))

        await feed(_bundle(None, "Toilet Brush"))

        assert source.metadata["title"] == "Toilet Brush"
        assert "album_art_url" in source.metadata
