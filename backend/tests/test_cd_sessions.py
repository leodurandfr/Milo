"""The CD on the session model: each scenario is a gap of the source audit
(E-numbers, docs: source architecture) or an owner decision, observed only
through the outside world (tests/cd_world.py — the drive, udev and mpv as
measured on the unit) and the wire a real AudioStateMachine publishes.
"""
import asyncio
import errno

import pytest

from backend.tests.cd_world import DISC_ID, OFFSETS, CdWorld, kernel_lba
from backend.tests.golden.harness import settle


@pytest.fixture
async def world(monkeypatch):
    made = []

    async def factory(settings=None, disc="audio", plugged=True):
        w = CdWorld(monkeypatch, settings=settings)
        if plugged:
            w.drive, w.media = True, disc
        made.append(w)
        await w.boot()
        return w

    yield factory
    for w in made:
        await w.source.shutdown()
    await settle()


async def _playing_track(w, track: int, seconds: float):
    """CD selected with its disc, `track` playing, `seconds` into it."""
    await w.select()
    await w.command("play_track", {"track_number": track})
    w.playhead(seconds)
    await w.advance(1.1)
    assert w.playing() and w.track() == track


# === Owner decisions (2026-09-23) ===

async def test_opening_the_source_with_a_disc_in_does_not_play(world):
    """A disc already in the slot when the source opens is loaded and parked
    paused, ready to start at once — never played."""
    w = await world()
    await w.select()

    assert w.phase() == "paused"
    assert w.disc()["id"] == DISC_ID
    assert w.track() == 1


async def test_a_disc_inserted_while_the_source_is_open_plays(world):
    w = await world(disc=None)
    await w.select()
    await w.insert()
    w.playhead(1)
    await w.advance(1.1)

    assert w.playing()
    assert w.track() == 1


async def test_a_drive_replugged_with_its_disc_in_does_not_play(world):
    """A replug is not an insertion: the disc was already in (measured: `add`,
    then media, no spinning step)."""
    w = await world(disc=None, plugged=False)
    await w.select()
    await w.plug(disc="audio")
    await w.advance(1.1)

    assert w.disc()["id"] == DISC_ID
    assert not w.playing()


# === E66: CDROMREADAUDIO counts from 0, libdiscid from the lead-in ===

async def test_a_track_is_read_from_its_first_sector(world):
    """E66: libdiscid's offsets include the 150-sector lead-in and the reader
    handed them to the ioctl as they were: every track started 2 s late and
    the last 2 s of a disc were never read."""
    w = await world()
    await w.select()
    await w.command("play_track", {"track_number": 3})

    start, end = w.reader.starts[-1]
    assert start == kernel_lba(3)
    assert end == 60150 - 150


# === E34: an eject the drive refuses ===

async def test_a_refused_eject_leaves_the_disc_as_it_was(world):
    """E34: a failed eject cleared the disc from the screen and left the drive
    marked present and read: a permanent loader, the disc never read again."""
    w = await world()
    await _playing_track(w, 2, 30)
    w.eject_fails = True
    result = await w.command("eject")
    await w.advance(3)

    assert result["success"] is False
    assert w.disc()["id"] == DISC_ID
    assert w.availability() is None
    assert (await w.command("play_track", {"track_number": 4}))["success"] is True
    assert w.track() == 4


# === E35: an eject whose disc comes back ===

async def test_an_eject_is_over_when_the_disc_is_out_even_if_it_is_pushed_back(world):
    """E35: 'ejecting' waited for a poll to see an empty drive. A disc pushed
    back in before that poll kept the screen on 'ejecting' for good. The eject's
    own no-media change ends it (measured: a slot drive announces nothing when
    the disc is pulled out)."""
    w = await world()
    await w.select()
    w.eject_fails = False
    await w.source.command("eject", None)
    # The disc is pushed straight back in, before any poll could look.
    await w.insert()
    await w.advance(3)

    assert w.availability() is None
    assert w.disc()["id"] == DISC_ID


# === E36: a disc that cannot be read ===

async def test_a_disc_that_never_becomes_readable_is_reported(world):
    """E36: a disc that spins and never reads stayed on 'loading the disc'
    for good, with no message."""
    w = await world(disc=None)
    await w.select()
    await w.insert(readable=False)
    await w.advance(60)

    assert w.availability() == "unreadable_disc"
    assert w.errors() == ["disc_unreadable"]
    # E67: a disc Milō cannot play still comes out.
    assert w.state()["controls"] == ["eject"]


async def test_a_disc_with_no_audio_track_is_reported(world):
    """A data disc reads (media, no audio track): it is unreadable as a CD."""
    w = await world(disc=None)
    await w.select()
    await w.insert(disc="data")
    await w.advance(10)

    assert w.availability() == "unreadable_disc"
    assert w.errors() == ["disc_unreadable"]


# === E37: a probe error is not a removal ===

async def test_a_drive_status_glitch_does_not_stop_the_disc(world):
    """E37: one drive-status probe answering an error counted as the disc
    being removed: playback stopped and the album started again at track 1."""
    w = await world()
    await _playing_track(w, 2, 30)
    w.probe_glitches = 1
    await w.advance(4.5)

    assert w.playing()
    assert w.track() == 2


async def test_a_read_error_mid_track_keeps_the_disc_and_the_track(world):
    """A read error is a lost stream, not a removal: banner, no session, and a
    press on play resumes the same track at the same second."""
    w = await world()
    await _playing_track(w, 2, 40)
    await w.read_error(errno.EIO)

    assert not w.active()
    assert w.errors() == ["stream_disconnected"]
    assert w.disc()["id"] == DISC_ID
    await w.command("resume")
    assert w.reader.starts[-1][0] == kernel_lba(2, 40)


# === E38: a multiroom toggle mid-play ===

async def test_a_reroute_at_track_7_comes_back_playing_there(world):
    """E38 (owner decision): a multiroom toggle brought the disc back to track
    1, 0:00, paused. It resumes playing, same track, same second."""
    w = await world()
    await _playing_track(w, 7, 25)
    await w.reroute()
    w.playhead(1)
    await w.advance(1.1)

    assert w.playing()
    assert w.track() == 7
    assert w.reader.starts[-1][0] == kernel_lba(7, 25)


async def test_a_reroute_while_paused_comes_back_paused(world):
    w = await world()
    await _playing_track(w, 3, 12)
    await w.command("pause")
    await w.reroute()

    assert w.phase() == "paused"
    assert w.track() == 3
    assert w.reader.starts[-1][0] == kernel_lba(3, 12)


# === E39: a seek with nothing loaded ===

async def test_a_seek_with_nothing_playing_moves_the_resume_point_only(world):
    """E39: a seek from READY restarted the reader, paused, holding /dev/sr0
    with no timer to release it. It moves the resume point; the next play
    starts there."""
    w = await world(settings={"audio.auto_stop_delay": 30})
    await _playing_track(w, 2, 20)
    await w.command("pause")
    await w.advance(31)                       # auto-stop: no session, drive released
    assert not w.active()
    starts = len(w.reader.starts)

    await w.command("seek", {"position_ms": 90_000})
    assert len(w.reader.starts) == starts
    assert not w.reader.running
    assert not w.active()
    assert (w.track(), w.resume_ms()) == (2, 90_000)

    await w.command("resume")
    assert w.reader.starts[-1][0] == kernel_lba(2, 90)


# === E40, E61: mpv dies mid-play ===

async def test_after_mpv_dies_next_plays_the_following_track(world):
    """E40: after an mpv crash the screen showed the disc but next and prev
    answered 'no disc'."""
    w = await world()
    await _playing_track(w, 2, 30)
    await w.mpv_dies()
    await w.advance(2)

    result = await w.command("next")
    assert result["success"] is True
    assert w.reader.starts[-1][0] == kernel_lba(3)


async def test_after_mpv_dies_the_disc_resumes_where_it_was(world):
    """E61: an mpv crash forgot the resume point (track 1, 0:00) where the
    auto-stop keeps it."""
    w = await world()
    await _playing_track(w, 2, 30)
    await w.mpv_dies()
    await w.advance(2)

    assert w.track() == 2
    await w.command("resume")
    assert w.reader.starts[-1][0] == kernel_lba(2, 30)


# === The end of the disc, a removal ===

async def test_the_end_of_the_disc_goes_back_to_track_1(world):
    w = await world()
    await _playing_track(w, 8, 35)
    await w.disc_runs_out()

    assert not w.active()
    assert w.session_ends()[-1] == "eof"
    assert (w.track(), w.resume_ms()) == (1, 0)
    assert w.errors() == []


async def test_unplugging_the_drive_mid_play_forgets_the_disc(world):
    w = await world()
    await _playing_track(w, 4, 10)
    await w.unplug()

    assert not w.active()
    assert w.availability() == "no_drive"
    assert w.disc() is None


async def test_every_track_position_maps_through_the_same_lead_in(world):
    """Auto-advance: the reader plays through a boundary; the track the
    playhead is in follows the kernel's offsets, not libdiscid's."""
    w = await world()
    await _playing_track(w, 1, 0)
    # 2 s before track 2 by the kernel's count (by libdiscid's, 2 s *into* it).
    w.playhead((OFFSETS[1] - OFFSETS[0]) / 75 - 1)
    await w.advance(1.1)
    assert w.track() == 1
    w.playhead((OFFSETS[1] - OFFSETS[0]) / 75 + 1)
    await w.advance(1.1)
    assert w.track() == 2


# === Commands ===

async def test_a_track_past_the_disc_is_refused(world):
    w = await world()
    await w.select()
    result = await w.command("play_track", {"track_number": 9})
    assert result["success"] is False
    assert not w.playing()


async def test_nothing_plays_with_no_disc_in(world):
    w = await world(disc=None)
    await w.select()
    for cmd, data in (("play_track", {"track_number": 1}), ("resume", None), ("next", None)):
        assert (await w.command(cmd, data))["success"] is False
    assert w.reader.starts == []


async def test_next_on_the_last_track_does_nothing(world):
    w = await world()
    await _playing_track(w, 8, 5)
    starts = len(w.reader.starts)
    assert (await w.command("next"))["success"] is True
    assert len(w.reader.starts) == starts
    assert w.playing() and w.track() == 8


async def test_prev_past_the_threshold_restarts_the_track(world):
    """Restart-then-previous, like Spotify: past 3 s prev restarts the track."""
    w = await world()
    await _playing_track(w, 4, 30)
    await w.command("prev")
    assert w.reader.starts[-1][0] == kernel_lba(4)


async def test_prev_in_the_first_seconds_steps_back_a_track(world):
    w = await world()
    await _playing_track(w, 4, 1)
    await w.command("prev")
    assert w.reader.starts[-1][0] == kernel_lba(3)


async def test_prev_on_track_1_stays_on_track_1(world):
    w = await world()
    await _playing_track(w, 1, 1)
    await w.command("prev")
    assert w.reader.starts[-1][0] == kernel_lba(1)


async def test_a_seek_while_playing_goes_on_playing_there(world):
    w = await world()
    await _playing_track(w, 3, 10)
    await w.command("seek", {"position_ms": 70_000})
    w.playhead(1)
    await w.advance(1.1)

    assert w.reader.starts[-1][0] == kernel_lba(3, 70)
    assert w.playing() and w.track() == 3


async def test_a_seek_while_paused_moves_the_playhead_and_stays_paused(world):
    """A seek never decides to play: the disc used to play behind a paused
    screen after one."""
    w = await world()
    await _playing_track(w, 3, 10)
    await w.command("pause")
    await w.command("seek", {"position_ms": 70_000})

    assert w.reader.starts[-1][0] == kernel_lba(3, 70)
    assert w.mpv.paused is True
    assert w.phase() == "paused"
    assert w.position_s() == 70


async def test_pause_with_nothing_playing_is_refused(world):
    w = await world(disc=None)
    await w.select()
    result = await w.command("pause")
    assert result["success"] is False


async def test_resume_while_playing_reloads_nothing(world):
    w = await world()
    await _playing_track(w, 2, 10)
    starts = len(w.reader.starts)
    assert (await w.command("resume"))["success"] is True
    assert len(w.reader.starts) == starts


async def test_a_pause_mpv_refuses_leaves_the_disc_playing(world):
    w = await world()
    await _playing_track(w, 2, 10)
    w.mpv.accept = False
    result = await w.command("pause")
    assert result["success"] is False
    assert w.playing()


async def test_a_resume_mpv_refuses_leaves_the_disc_paused(world):
    w = await world()
    await _playing_track(w, 2, 10)
    await w.command("pause")
    w.mpv.accept = False
    result = await w.command("resume")
    assert result["success"] is False
    assert w.phase() == "paused"


# === Opening and leaving the source ===

async def test_a_service_that_will_not_start_fails_the_start(world):
    w = await world()
    w.systemd.start.return_value = False
    await w.select()
    assert w.state()["service"] == "failed"
    assert w.reader.starts == []


async def test_an_empty_drive_preloads_nothing(world):
    w = await world(disc=None)
    await w.select()
    assert w.reader.starts == []
    assert not w.active()
    assert w.availability() == "no_disc"


async def test_the_preload_is_paused_before_mpv_opens_the_fifo(world):
    """mpv plays the FIFO as soon as it opens it: parked means paused first."""
    w = await world()
    await w.select()
    pause_set = w.mpv.sent.index(("set_property", "pause", True))
    load = next(i for i, c in enumerate(w.mpv.sent) if c[0] == "loadfile")
    assert pause_set < load


async def test_the_reader_is_ready_before_mpv_is_told_to_open_the_fifo(world):
    """The FIFO's writer must be waiting when mpv opens it, or both block."""
    w = await world()
    await w.select()
    assert w.log.index("reader.ready") < w.log.index("mpv.loadfile")


async def test_leaving_the_source_lets_mpv_go_before_the_reader_is_joined(world):
    """A paused mpv holds the FIFO without draining it: stopping the reader
    first waited out its 3 s join on every switch (measured)."""
    w = await world()
    await _playing_track(w, 2, 10)
    w.log.clear()
    await w.leave()
    assert w.log.index("mpv.stop") < w.log.index("reader.stop")


async def test_leaving_and_coming_back_resumes_the_track_paused(world):
    """SOURCE_SWITCH keeps the resume point: back on the CD, the track and
    second are loaded, parked — never played."""
    w = await world()
    await _playing_track(w, 3, 20)
    await w.leave()
    assert not w.reader.running
    await w.select()

    assert w.reader.starts[-1][0] == kernel_lba(3, 20)
    assert w.phase() == "paused"


async def test_the_auto_stop_releases_the_drive_and_keeps_the_point(world):
    w = await world(settings={"audio.auto_stop_delay": 30})
    await _playing_track(w, 5, 12)
    await w.command("pause")
    await w.advance(31)

    assert not w.active()
    assert w.session_ends() == ["idle_timeout"]
    assert not w.reader.running
    assert (w.track(), w.resume_ms()) == (5, 12_000)


async def test_a_reader_that_never_gets_ready_is_a_failed_load(world):
    w = await world()
    await w.select()
    w.reader.ready = False
    result = await w.command("play_track", {"track_number": 2})

    assert result["success"] is False
    assert not w.reader.running
    assert not w.active()
    assert "stream_load_failed" in w.errors()


async def test_a_load_mpv_refuses_stops_the_reader_too(world):
    w = await world()
    await w.select()
    w.mpv.accept = False
    result = await w.command("play_track", {"track_number": 2})

    assert result["success"] is False
    assert not w.reader.running


# === Naming the disc ===

async def test_a_disc_inserted_while_the_source_is_closed_is_not_looked_up(world):
    """No network for a source the user has not opened: the TOC is read, the
    lookup waits for the source."""
    w = await world(disc=None)
    await w.insert()
    assert w.lookups == 0
    await w.select()
    assert w.lookups == 1
    assert w.disc()["id"] == DISC_ID
    assert not w.playing()


async def test_an_unknown_disc_is_named_by_the_retry_once_musicbrainz_knows_it(world):
    w = await world()
    w.named = False
    await w.select()
    assert w.disc()["album"] is None

    w.named = True
    await w.advance(61)
    assert w.disc()["album"] == "Eight"


async def test_no_retry_runs_for_a_source_that_is_closed(world):
    w = await world()
    w.named = False
    await w.select()
    lookups = w.lookups
    await w.leave()
    await w.advance(200)
    assert w.lookups == lookups


async def test_a_disc_ejected_during_its_lookup_is_not_written_back(world):
    w = await world(disc=None)
    await w.select()
    w.lookup_gate = asyncio.Event()
    await w.insert()
    await w.source.command("eject", None)
    await settle()
    w.lookup_gate.set()
    await w.advance(3)

    assert w.disc() is None
    assert w.availability() == "no_disc"


async def test_a_lookup_that_fails_leaves_the_disc_playable_with_plain_titles(world):
    w = await world()
    w.lookup_raises = True
    await w.select()
    assert w.session()["title"] == "Track 1"
    assert w.disc()["tracks"][1]["title"] == "Track 2"
    assert (await w.command("play_track", {"track_number": 2}))["success"] is True


async def test_a_toc_that_fails_once_is_read_again(world):
    """The drive can still be settling when udev says the disc is readable."""
    w = await world(disc=None)
    await w.select()
    w.toc_failures = 1
    await w.insert()
    await w.advance(3)
    assert w.disc()["id"] == DISC_ID


async def test_a_toc_that_never_reads_is_reported_unreadable(world):
    w = await world(disc=None)
    await w.select()
    w.toc_failures = 99
    await w.insert()
    await w.advance(10)
    assert w.availability() == "unreadable_disc"
    assert w.errors() == ["disc_unreadable"]


# === The jacket ===

async def test_the_disc_plays_before_its_jacket_and_says_one_is_coming(world):
    w = await world(disc=None)
    w.cover_known = True
    w.cover_gate = asyncio.Event()
    await w.select()
    await w.insert()
    assert w.state()["details"]["artwork_pending"] is True
    assert w.reader.starts, "the disc waited for its jacket"

    w.cover_gate.set()
    await w.advance(0.1)
    assert w.state()["details"]["artwork_pending"] is False
    assert w.session()["artwork"] == "/api/cd/cover/world-disc-1"


async def test_a_jacket_the_archive_does_not_have_lifts_the_veil(world):
    w = await world()
    w.cover_known, w.cover_answer = True, None
    await w.select()
    assert w.state()["details"]["artwork_pending"] is False
    assert w.disc()["cover_url"] is None
    assert w.session()["artwork"] is None


async def test_a_jacket_fetch_that_raises_still_lifts_the_veil(world):
    w = await world()
    w.cover_known, w.cover_answer = True, OSError("archive unreachable")
    await w.select()
    assert w.state()["details"]["artwork_pending"] is False


async def test_a_disc_swapped_during_the_fetch_is_not_given_the_old_jacket(world):
    w = await world()
    w.cover_known = True
    w.cover_gate = asyncio.Event()
    await w.select()
    await w.command("eject")
    w.cover_known = False
    await w.insert()
    w.cover_gate.set()
    await w.advance(0.1)
    assert w.disc()["cover_url"] is None
    assert w.session()["artwork"] is None


async def test_opening_the_source_again_asks_for_a_jacket_left_unfetched(world):
    w = await world()
    w.cover_known = True
    w.cover_gate = asyncio.Event()
    await w.select()
    await w.leave()                       # the fetch is canceled with the source
    w.cover_gate.set()
    await w.select()
    await w.advance(0.1)
    assert w.cover_fetches == 2
    assert w.disc()["cover_url"] == "/api/cd/cover/world-disc-1"
    assert w.session()["artwork"] == "/api/cd/cover/world-disc-1"


# === The drive ===

async def test_an_eject_udev_never_confirms_is_settled_by_asking_the_drive(world):
    """E35's net: past 5 s with no news, the drive says the disc is out."""
    w = await world()
    await w.select()
    w.eject_silent = True
    await w.source.command("eject", None)
    await w.advance(6)
    assert w.availability() == "no_disc"
    assert w.disc() is None


async def test_a_drive_unplugged_then_replugged_empty_is_empty(world):
    w = await world()
    await w.select()
    await w.unplug()
    assert w.availability() == "no_drive"
    await w.plug()
    assert w.availability() == "no_disc"


async def test_the_live_position_answers_a_state_request(world):
    w = await world()
    await _playing_track(w, 2, 10)
    w.playhead(42.5)
    assert await w.source.refresh_when_idle() is True
    await settle()
    # Sector precision: 1/75 s.
    assert abs(w.position_ms() - 42_500) < 1000 / 75


async def test_a_host_without_udev_has_no_drive_and_runs_on(monkeypatch):
    from backend.sources.cd import source as cd_module

    w = CdWorld(monkeypatch)

    class NoUdev:
        def __init__(self, device=""):
            pass

        def start(self, on_event):
            return None

        def stop(self):
            pass

    monkeypatch.setattr(cd_module, "CdDrive", NoUdev)
    w.source._drive = NoUdev()
    assert await w.source.initialize() is True
    await w.select()
    assert w.state()["service"] == "running"
    assert not w.active()
    assert w.availability() == "no_drive"
    await w.source.shutdown()


# === The idle timeout ===

async def test_a_zero_delay_keeps_a_paused_disc_loaded(world):
    """0 in Settings means off."""
    w = await world(settings={"audio.auto_stop_delay": 0})
    await _playing_track(w, 2, 10)
    await w.command("pause")
    await w.advance(3600)
    assert w.phase() == "paused"
    assert w.reader.running


async def test_an_idle_timeout_for_a_source_left_meanwhile_stops_nothing(world):
    """The timer fired as the user switched away: the auto-stop must not end
    what the switch is already ending, nor touch the new source."""
    from backend.core.models.audio_state import AudioSource
    w = await world(settings={"audio.auto_stop_delay": 30})
    await _playing_track(w, 2, 10)
    await w.command("pause")
    w.machine.system_state.active_source = AudioSource.RADIO
    await w.advance(31)
    assert w.reader.running, "the drive was released under the switch"


async def test_a_disc_that_left_the_drive_forgets_where_it_was(world):
    """The disc's resume point is forgotten when the disc leaves, even with no
    session to end (the same rule as a storage leaving, E51): left at track 4
    by a source switch, the disc unplugged and plugged back starts at track 1."""
    w = await world()
    await _playing_track(w, 4, 10)
    await w.leave()                        # SOURCE_SWITCH keeps track 4
    await w.unplug()
    await w.plug(disc="audio")
    await w.select()

    assert w.track() == 1
    assert w.reader.starts[-1][0] == kernel_lba(1)


# === Insertion: udev's first word, and the probe (owner decision 2026-09-23) ===

async def test_a_disc_announced_only_once_readable_still_plays(world):
    """Measured on the unit with nothing polling: an inserted disc produces one
    udev event, when it is readable — no spinning step. A disc arriving in a
    drive known empty is an insertion all the same."""
    w = await world(disc=None)
    await w.select()
    w.media, w.spinning = "audio", True      # in and readable before any probe
    await w.spun_up()
    w.playhead(1)
    await w.advance(1.1)

    assert w.playing()


async def test_an_inserted_disc_shows_as_loading_within_the_probe_interval(world):
    """udev hears of it ~10 s later; the probe, CD on screen over an empty
    drive, puts "loading the album" up within 2 s."""
    w = await world(disc=None)
    await w.select()
    await w.insert(readable=False)            # 2.1 s pass, the disc still spins

    assert w.availability() == "reading_disc"
    assert w.disc() is None


async def test_no_probe_runs_for_a_closed_source_or_a_drive_holding_a_disc(world):
    w = await world()
    await _playing_track(w, 2, 10)
    asks = w.status_asks
    await w.advance(30)
    assert w.status_asks == asks, "the drive was polled with a disc in it"

    await w.command("eject")
    await w.leave()
    asks = w.status_asks
    await w.advance(30)
    assert w.status_asks == asks, "the drive was polled for a closed source"


async def test_the_probe_can_find_a_disc_but_never_lose_one(world):
    """E37 cannot come back through it: a probe answering an error on an empty
    drive changes nothing, and it does not run once a disc is in."""
    w = await world(disc=None)
    await w.select()
    w.probe_glitches = 5
    await w.advance(10)
    assert w.availability() == "no_disc"


# === Code review, 2026-09-23 ===

async def test_an_eject_the_drive_did_not_carry_out_leaves_the_disc_shown(world):
    """`eject` answered 0, the disc never left and udev said nothing: past the
    confirmation delay the drive says a disc is ready, so the disc stays —
    showing it gone left a loaded disc invisible, with nothing to bring it back
    (udev is silent while a disc sits in the drive)."""
    w = await world()
    await w.select()
    w.eject_keeps_disc = True
    await w.source.command("eject", None)
    await w.advance(6)

    assert w.availability() is None
    assert w.disc()["id"] == DISC_ID


async def test_an_eject_cut_by_a_source_switch_does_not_stay_ejecting(world):
    """The switch cuts the eject command while `eject` runs; the drive then
    refuses. The disc must not stay in "ejecting" for good (every later eject
    would answer "nothing to eject")."""
    w = await world()
    await w.select()
    w.eject_gate = asyncio.Event()
    w.eject_fails = True
    pressed = asyncio.ensure_future(w.source.command("eject", None))
    await settle()
    await w.leave()
    w.eject_gate.set()
    await asyncio.gather(pressed, return_exceptions=True)
    await w.advance(6)
    await w.select()

    assert w.availability() is None
    assert w.disc()["id"] == DISC_ID


async def test_an_unknown_disc_is_looked_up_again_until_it_is_named(world):
    """The MusicBrainz retry runs every 60 s while the source is open, not
    once: a network down for two minutes still names the disc after."""
    w = await world()
    w.named = False
    await w.select()
    await w.advance(61)                   # first retry: still unknown
    assert w.disc()["album"] is None

    w.named = True
    await w.advance(61)
    assert w.disc()["album"] == "Eight"


async def test_a_toc_read_that_hangs_does_not_hold_up_leaving_the_source(world):
    """A struggling disc read runs off the mailbox: a switch does not wait for
    libdiscid (the transition has 15 s in all)."""
    w = await world(disc=None)
    await w.select()
    w.toc_gate = asyncio.Event()
    w.media, w.spinning = "audio", True
    await w.spun_up()                     # media announced; the read hangs

    from backend.core.models.audio_state import AudioSource
    leaving = asyncio.ensure_future(w.machine.transition_to_source(AudioSource.NONE))
    await settle()
    assert leaving.done(), "the switch waited behind the disc read"
    w.toc_gate.set()
    await settle()


async def test_a_refused_eject_keeps_the_track_and_second(world):
    """E34, the other half: the drive refused, so nothing left the drive —
    play resumes where the eject interrupted it, not at track 1."""
    w = await world()
    await _playing_track(w, 7, 30)
    w.eject_fails = True
    await w.command("eject")
    await w.command("resume")

    assert w.reader.starts[-1][0] == kernel_lba(7, 30)


# === E58: the drive's state rides on the one state ===

async def test_a_drive_change_off_screen_is_one_state_carrying_it(world):
    """E58: a drive change was its own event (`system/cd_drive_status`), which
    a client could see before or after the state it belonged to. It is now
    the CD's `availability` in the one state, published whether or not CD is
    on screen — a client that shows the CD card greyed reads it there."""
    w = await world(disc=None, plugged=False)
    assert w.availability() == "no_drive"
    before = len(w.recorder.envelopes)
    await w.plug()

    sent = w.recorder.envelopes[before:]
    assert sent, "the drive change was not published"
    assert {(e["category"], e["type"]) for e in sent} == {("source", "state")}
    assert sent[-1]["data"]["source"] == "none"
    assert sent[-1]["data"]["availability"]["cd"] == "no_disc"
