# backend/tests/test_music_library_long_queue.py
"""A long Music Library queue, edited through the real mpv IPC link.

Every queue edit reads mpv's `playlist` back in one reply, one line that grows
with the queue. Measured on the unit 2026-11-22: 300 tracks edited, 500 did not
— an 87 kB line over asyncio's default 64 KiB read limit, which the reader
skipped, so the read waited out its deadline and the edit ended the session as
a lost stream (HTTP 400) while mpv played on.

The rest of the suite swaps MpvController for MpvSim, which cannot see a line's
length. Here the sim sits behind a real Unix socket (MpvSimServer) and the
source drives it through the real controller, so what is exercised is the
reading path the unit runs. The stream URLs are the real client's (token and
salt in the query), so the reply is the size mpv sends.
"""
import hashlib
import logging
from urllib.parse import parse_qs, urlsplit

import pytest

from backend.shared import mpv as mpv_module
from backend.shared import mpv_audio_source
from backend.shared.mpv import MpvController
from backend.shared.mpv_audio_source import MpvAudioSource
from backend.sources.music_library import source as library_module
from backend.sources.music_library.navidrome_client import NavidromeClient
from backend.tests.golden.harness import LiveProcessWatch
from backend.tests.golden.test_wire_music_library import FakeNavidrome
from backend.tests.mpv_sim import MpvSimServer
from backend.tests.test_mpv_sessions import LibraryRig

# asyncio.StreamReader's default limit, the one that cut the queue.
ASYNCIO_DEFAULT_LIMIT = 64 * 1024
# The reply measured to fail on the unit (500 tracks).
MEASURED_FAILING_REPLY = 87_000


class RealUrlNavidrome(FakeNavidrome):
    """Stream URLs as the real client builds them, fresh salt each time."""

    def __init__(self) -> None:
        super().__init__()
        self._client = NavidromeClient("milo", "not-a-secret")

    def stream_url(self, song_id):
        return self._client.stream_url(song_id)


def tracks(count):
    """Subsonic songs with Navidrome-shaped ids (22 characters)."""
    return [
        {
            "id": hashlib.md5(str(n).encode()).hexdigest()[:22],
            "title": f"Track {n}", "artist": "Artist", "album": "Album",
            "coverArt": "al-1", "duration": 240,
        }
        for n in range(count)
    ]


@pytest.fixture
async def rig(monkeypatch, tmp_path):
    """The library on a real state machine, its mpv the sim behind a socket."""
    rig = LibraryRig(monkeypatch)
    monkeypatch.setattr(library_module, "NavidromeClient", RealUrlNavidrome)
    server = MpvSimServer(rig.mpv, str(tmp_path / "mpv.sock"))
    await server.start()
    monkeypatch.setattr(
        mpv_audio_source, "MpvController", lambda **_: MpvController(ipc_socket_path=server.path)
    )
    # The harness's systemd names pid 4242, which the real watch would find gone.
    monkeypatch.setattr(mpv_module, "ProcessWatch", LiveProcessWatch)
    # The harness's loading watchdog passes at once, which a scenario on the
    # sim can afford because its events land inside the command; here they
    # cross a socket. Ten minutes: it never fires in a test.
    monkeypatch.setattr(MpvAudioSource, "STALL_TIMEOUT_S", 600)
    rig.server = server
    yield rig
    await rig.leave()
    await server.stop()


def queue_ids(rig):
    """The whole queue as `GET /queue` lists it (the state carries a window)."""
    return [track["id"] for track in rig.source.whole_queue.queue]


def mpv_ids(rig):
    """The songs mpv holds, in its order, read off each stream URL."""
    return [parse_qs(urlsplit(entry.url).query)["id"][0] for entry in rig.mpv.playlist]


async def edit_everything(rig):
    """Add (at the end and next), remove and move on the queue — each of
    them reads the whole playlist back."""
    extra = tracks(2003)[-3:]
    assert (await rig.command("add_to_queue", {"tracks": extra[:2], "position": "end"}))["success"]
    assert (await rig.command("add_to_queue", {"tracks": extra[2:], "position": "next"}))["success"]
    ids = queue_ids(rig)
    assert (await rig.command(
        "remove_from_queue", {"index": len(ids) - 1, "track_id": ids[-1]}
    ))["success"]
    ids = queue_ids(rig)
    assert (await rig.command(
        "move_in_queue", {"from_index": len(ids) - 1, "to_index": 2, "track_id": ids[-1]}
    ))["success"]


async def test_a_queue_of_2000_tracks_is_edited_without_losing_the_stream(rig, caplog):
    """Breaks: adding, removing or moving a track in a long queue (an artist's
    whole discography, a large playlist) stops the music with a "stream lost"
    banner, on every screen and on Milo-Mac/Milo-iOS. Each read back is one
    reply, under READ_LIMIT."""
    await rig.select()
    queue = tracks(2000)
    assert (await rig.command("play_context", {"tracks": queue, "start_index": 0}))["success"]

    with caplog.at_level(logging.WARNING, logger="backend.shared.mpv"):
        await edit_everything(rig)

    assert "read limit" not in caplog.text
    # The read this guards is as long as the one measured to fail, and more.
    assert rig.server.longest_line > 3 * max(ASYNCIO_DEFAULT_LIMIT, MEASURED_FAILING_REPLY)
    assert rig.session_ends() == []
    assert rig.state()["session"]["phase"] == "playing"
    assert len(queue_ids(rig)) == 2002
    assert queue_ids(rig) == mpv_ids(rig)


async def test_a_playlist_over_the_read_limit_is_read_entry_by_entry(rig, monkeypatch, caplog):
    """A queue whose reply is still over the limit: mpv answered, so it is not
    a lost stream. Breaks: past the limit, a live mpv reads as a dead one and
    the edit ends the session."""
    monkeypatch.setattr(mpv_module, "READ_LIMIT", 32 * 1024)
    await rig.select()
    queue = tracks(500)
    assert (await rig.command("play_context", {"tracks": queue, "start_index": 0}))["success"]

    with caplog.at_level(logging.WARNING, logger="backend.shared.mpv"):
        await edit_everything(rig)

    assert rig.server.longest_line > mpv_module.READ_LIMIT
    assert "over the 32768-byte read limit" in caplog.text
    assert rig.session_ends() == []
    assert rig.state()["session"]["phase"] == "playing"
    assert queue_ids(rig) == mpv_ids(rig)
