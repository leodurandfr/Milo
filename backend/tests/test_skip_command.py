"""`skip`: the playhead moved by a signed number of seconds, by the source.

A client used to compute "position + 30" from the last anchor it had been
sent, and Milō republished that anchor only after the seek — so two quick
presses aimed at the same second, and an anchor arriving mid-burst described
an older seek (measured from Milo-iOS, 2026-09-25: 52 presses, 52 commands, all
200, the playhead short of where the presses added up to). A skip is applied
by the source from where its own playhead is, one command at a time, so a
burst adds up there; every source that takes `seek` takes `skip`, and lists it
wherever it lists `seek` (frontend useSourceProgress.skip, the Milo-iOS lock
screen's −15/+30).

Driven through the outside world each source was measured against: MpvSim
(podcast, music library), cd_world (the drive), spotify_world (go-librespot).
"""
import pytest

from backend.core.models.commands import skip_target
from backend.shared.mpv_audio_source import MpvAudioSource
from backend.tests.cd_world import kernel_lba
from backend.tests.golden.harness import settle
from backend.tests.golden.test_wire_music_library import LENGTHS
from backend.tests.golden.test_wire_podcast import EPISODE_A
from backend.tests.spotify_world import PARAPLUIE, SpotifyWorld
from backend.tests.test_cd_sessions import _playing_track, world as cd_world  # noqa: F401
from backend.tests.test_mpv_sessions import LibraryRig, PodcastRig


def test_the_target_is_bounded_by_zero_and_the_duration():
    assert skip_target(10_000, -15, 200_000) == 0
    assert skip_target(190_000, 30, 200_000) == 200_000
    assert skip_target(60_000, 30, None) == 90_000
    assert skip_target(None, 30, 200_000) == 30_000


# === Podcast: a relative seek in mpv ===

@pytest.fixture
def podcast(monkeypatch):
    return PodcastRig(monkeypatch)


async def _podcast_at(rig, seconds: float) -> None:
    await rig.select()
    await rig.play(EPISODE_A)
    rig.mpv.playhead(seconds)
    await rig.tick()
    assert rig.position_ms() == seconds * 1000


async def test_podcast_skips_in_a_burst_add_up(podcast):
    """Three presses before any anchor could reach a client land where they add
    up, in mpv and on the wire — each with its own `source/position`, and the
    progress file on the last one. If this fails, the second +30 of a burst is
    lost again."""
    await _podcast_at(podcast, 300)

    for seconds in (30, 30, -15):
        result = await podcast.command("skip", {"seconds": seconds})
        assert result["success"] is True

    assert [c for c in podcast.mpv.sent if c[0] == "seek_by"] == [
        ("seek_by", 30), ("seek_by", 30), ("seek_by", -15),
    ]
    assert podcast.mpv.position == 345
    assert podcast.position_ms() == 345_000
    assert [p["position"]["ms"] for p in podcast.positions()][-3:] == [330_000, 360_000, 345_000]
    assert podcast.data.progress[EPISODE_A["uuid"]]["position"] == 345


async def test_podcast_skip_anchors_where_mpv_landed(podcast):
    """The anchor may stand up to 2 s from mpv without being moved (the
    tolerance); a skip lands on mpv's own second, not on the anchor's."""
    await _podcast_at(podcast, 300)
    podcast.mpv.playhead(301.5)

    await podcast.command("skip", {"seconds": 30})

    assert podcast.position_ms() == 331_500
    assert podcast.data.progress[EPISODE_A["uuid"]]["position"] == 331


async def test_podcast_skip_back_stops_at_zero(podcast):
    await _podcast_at(podcast, 10)

    await podcast.command("skip", {"seconds": -15})

    assert podcast.mpv.position == 0
    assert podcast.position_ms() == 0


async def test_podcast_skip_past_the_end_finishes_the_episode(podcast):
    """mpv ends the file on a relative seek past its end (measured), exactly as
    a seek to the duration does: the episode is listened, not left 30 s short."""
    await _podcast_at(podcast, 1790)

    await podcast.command("skip", {"seconds": 30})

    assert podcast.session_ends() == ["eof"]
    assert podcast.data.completed == [EPISODE_A["uuid"]]


async def test_podcast_skip_while_paused_stays_paused(podcast):
    await _podcast_at(podcast, 300)
    await podcast.command("pause")

    await podcast.command("skip", {"seconds": 30})

    assert podcast.phase() == "paused"
    assert podcast.position_ms() == 330_000


async def test_podcast_skip_refused_moves_nothing(podcast):
    await _podcast_at(podcast, 300)
    podcast.mpv.accept = False
    before = len(podcast.recorder.envelopes)

    result = await podcast.command("skip", {"seconds": 30})

    assert result["success"] is False
    assert podcast.position_ms() == 300_000
    assert len(podcast.recorder.envelopes) == before


async def test_podcast_lists_skip_wherever_it_lists_seek(podcast, monkeypatch):
    """The frontend draws −15/+30 iff `skip` is listed, and the iOS lock screen
    enables them the same way."""
    monkeypatch.setattr(MpvAudioSource, "STALL_TIMEOUT_S", 60.0, raising=False)
    await podcast.select()
    podcast.mpv.auto_open = False
    await podcast.play(EPISODE_A)
    seen = [podcast.state()["controls"]]
    await podcast.mpv.opens()
    await settle()
    seen.append(podcast.state()["controls"])
    await podcast.command("pause")
    seen.append(podcast.state()["controls"])

    assert [("seek" in c, "skip" in c) for c in seen] == [
        (False, False), (True, True), (True, True),
    ]


# === Music Library: a relative seek in mpv, across the queue ===

@pytest.fixture
def library(monkeypatch):
    rig = LibraryRig(monkeypatch)
    rig.mpv.durations = dict(LENGTHS)
    return rig


async def test_library_skips_in_a_burst_add_up(library):
    await library.select()
    await library.play_album()
    library.mpv.playhead(100)
    await library.tick()

    await library.command("skip", {"seconds": 30})
    await library.command("skip", {"seconds": 30})

    assert library.mpv.position == 160
    assert library.position_ms() == 160_000
    assert "skip" in library.state()["controls"]


async def test_library_skip_past_the_end_of_a_track_plays_the_next(library):
    await library.select()
    await library.play_album()
    library.mpv.playhead(550)
    await library.tick()

    await library.command("skip", {"seconds": 30})

    assert library.track() == "tr-2"
    assert library.playing()


# === CD: a reload at the target, from the drive's playhead ===

async def test_cd_skips_in_a_burst_add_up(cd_world):  # noqa: F811
    """Every seek on a disc is a reload, and a skip handled while the one
    before it still reloads starts from that one's target, not from where the
    track was."""
    w = await cd_world()
    await _playing_track(w, 3, 10)

    await w.command("skip", {"seconds": 30})
    await w.command("skip", {"seconds": 30})

    assert w.reader.starts[-1][0] == kernel_lba(3, 70)
    assert int(w.position_s()) == 70
    assert "skip" in w.state()["controls"]


async def test_cd_skip_stops_at_the_start_of_the_track(cd_world):  # noqa: F811
    w = await cd_world()
    await _playing_track(w, 3, 10)

    await w.command("skip", {"seconds": -15})

    assert w.reader.starts[-1][0] == kernel_lba(3, 0)
    assert w.track() == 3


async def test_cd_skip_with_nothing_loaded_moves_the_resume_point(cd_world):  # noqa: F811
    """Like a seek (E39): no reload, the drive stays released."""
    w = await cd_world(settings={"audio.auto_stop_delay": 30})
    await _playing_track(w, 2, 20)
    await w.command("pause")
    await w.advance(31)
    assert not w.active()
    starts = len(w.reader.starts)

    await w.command("skip", {"seconds": 30})

    assert len(w.reader.starts) == starts
    assert (w.track(), w.resume_ms()) == (2, 50_000)


# === Spotify: an absolute seek from go-librespot's own position ===

@pytest.fixture
async def spotify(monkeypatch, tmp_path):
    w = SpotifyWorld(monkeypatch, tmp_path)
    await w.select()
    yield w
    await w.source.shutdown()


def _seeks(world):
    return [body["position"] for command, body in world.daemon.posted if command == "seek"]


async def test_spotify_skip_starts_from_where_the_daemon_is(spotify):
    """The anchor is within 2 s of the daemon, by design; /status is exact."""
    await spotify.phone_transfers(PARAPLUIE, at_ms=81_264)
    spotify.daemon.track["position"] = 82_900

    await spotify.command("skip", {"seconds": 30})

    assert _seeks(spotify) == [112_900]
    assert spotify.position_ms() == 112_900


async def test_spotify_skips_in_a_burst_add_up(spotify):
    await spotify.phone_transfers(PARAPLUIE, at_ms=81_264)

    for seconds in (30, 30, -15):
        await spotify.command("skip", {"seconds": seconds})

    assert _seeks(spotify) == [111_264, 141_264, 126_264]
    assert spotify.position_ms() == 126_264
    assert "skip" in spotify.state()["controls"]


async def test_spotify_skip_stops_at_the_end_of_the_track(spotify):
    await spotify.phone_transfers(PARAPLUIE, at_ms=170_000)

    await spotify.command("skip", {"seconds": 30})

    assert _seeks(spotify) == [PARAPLUIE["duration"]]


async def test_spotify_skip_with_status_unreadable_starts_from_the_anchor(spotify):
    await spotify.phone_transfers(PARAPLUIE, at_ms=81_264)
    spotify.daemon.status_answers = False

    await spotify.command("skip", {"seconds": 30})

    assert _seeks(spotify) == [111_264]
