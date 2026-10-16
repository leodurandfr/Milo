"""AirPlay sessions, driven through the world shairport-sync was measured to be
(tests/airplay_world.py; docs: source architecture, phase 3a).

What is asserted is what the wire says — the state a screen, the lock screen and
Milo-iOS read — after a sender or the daemon did something. Each gap test was
seen red on the code before phase 3a for the reason its docstring gives.
"""
import pytest

from backend.core.models.ws_events import SourceErrorReason
from backend.sources.airplay import source as airplay_source
from backend.sources.airplay.remote import RemoteUpdate
from backend.tests.airplay_world import MAC, MAC_COMMANDS, PHONE, SPOTIFY_COMMANDS, AirPlayWorld, ssnc

DELAY = 120   # make_settings' audio.auto_stop_delay


@pytest.fixture
async def world(monkeypatch, tmp_path):
    w = AirPlayWorld(monkeypatch, tmp_path)
    await w.select()
    yield w
    await w.source.shutdown()


async def _phone_plays(world: AirPlayWorld) -> None:
    await world.connects(PHONE, "iPhone de Léo")
    await world.music_plays()
    assert world.playing()


# === A sender's pause (Buffered: iPhone Music) ===

async def test_a_music_pause_is_published(world):
    """E55: shairport announces a Buffered pause (`paus`); the screen and
    the lock screen read is_playing, which stayed true."""
    await _phone_plays(world)
    await world.pauses()
    assert world.active() and not world.playing()
    await world.resumes()
    assert world.playing()


async def test_a_paused_session_is_ended_by_asking_the_sender_to_leave(world):
    """E15/E55: a pause now times out, and the end is asked of the daemon
    (DropSession) rather than done by restarting it under the sender. The
    phone then falls back to its own speaker, as measured."""
    await _phone_plays(world)
    await world.pauses()
    await world.advance(DELAY + 1)
    assert world.drops, "DropSession was never asked"
    assert world.restarts == []
    assert not world.active()
    assert world.errors() == []


async def test_a_pause_whose_stream_was_torn_down_still_times_out(world):
    """A paused phone drops its stream after 29-184 s and stays connected:
    still a pause, still ended by the idle timeout."""
    await _phone_plays(world)
    await world.pauses()
    await world.advance(30)
    await world.tears_the_stream_down()
    assert world.active() and not world.playing()
    await world.advance(DELAY)
    assert world.drops and not world.active()


async def test_resuming_after_the_stream_was_torn_down_plays(world):
    await _phone_plays(world)
    await world.pauses()
    await world.tears_the_stream_down()
    await world.restarts_the_stream()
    assert world.playing()
    await world.advance(DELAY + 1)
    assert world.drops == [] and world.playing()


async def test_a_skip_is_not_left_as_a_pause(world):
    """A skip or a seek is `paus` then `pres` 160 ms later: playing after it,
    and no idle timer left behind."""
    await _phone_plays(world)
    await world.skips()
    assert world.playing()
    assert world.session()["title"] == "L.A.D.Y"
    await world.advance(DELAY + 1)
    assert world.drops == [] and world.restarts == [] and world.playing()


async def test_a_flush_is_not_a_pause(world):
    """E54: `pfls` (a flush) was read as a pause: it armed the auto-stop, which
    restarted shairport-sync under a sender that never paused."""
    await _phone_plays(world)
    await world.send(ssnc("pfls", b"12345"))
    assert world.playing()
    await world.advance(DELAY + 1)
    assert world.restarts == [] and world.drops == [] and world.playing()


# === iPhone Music's late pause report (5.6) ===

async def test_the_pause_reported_at_the_resume_does_not_pause_again(world):
    """Measured 2026-10-16: iPhone Music's item says PlaybackRate 0 only with
    its next update — the resume's, 20 ms before the rate-1 one, stamped at the
    pause. Read as it arrived, it would publish a PAUSED blip after every
    resume."""
    await _phone_plays(world)
    await world.pauses()
    assert world.phase() == "paused"
    await world.advance(5)
    published = len(world.published())
    await world.resumes()
    assert world.playing()
    assert [s["session"]["phase"] for s in world.published()[published:]].count("paused") == 0


# === Realtime (a Mac; Spotify on the iPhone) ===

async def test_a_realtime_sender_with_an_item_is_playing(world):
    """A Realtime stream says nothing of a pause on the pipe; since 5.6 its
    item does (PlaybackRate), so a Mac playing Music or Spotify is PLAYING."""
    await world.connects(MAC, "Mac mini de Léo", MAC_COMMANDS)
    await world.realtime_plays()
    assert world.phase() == "playing"
    await world.advance(3 * DELAY)
    assert world.playing()
    assert world.drops == [] and world.restarts == []


async def test_system_audio_with_no_item_never_times_out(world):
    """Owner decision: a Realtime stream with nothing that publishes an item
    (a Mac's system audio) says nothing of a pause, so the session is
    CONNECTED and no idle timeout applies to it."""
    await world.connects(MAC, "Mac mini de Léo", frozenset())
    await world.realtime_plays(title=None)
    assert world.phase() == "connected"
    await world.advance(3 * DELAY)
    assert world.active()
    assert world.drops == [] and world.restarts == []


async def test_a_mac_pause_is_published_and_times_out(world):
    """The Mac's stream goes on carrying silence through a pause; its item
    says rate 0 at once, which is the only sign of it."""
    await world.connects(MAC, "Mac mini de Léo", MAC_COMMANDS)
    await world.realtime_plays()
    await world.realtime_pauses()
    assert world.phase() == "paused"
    await world.realtime_resumes()
    assert world.playing()
    await world.realtime_pauses()
    await world.advance(DELAY + 1)
    assert world.drops and not world.active()


async def test_a_sender_that_stops_sending_times_out(world):
    """Spotify on the iPhone says rate 0, then tears its Realtime stream down
    1 s after a pause and stays connected: a pause, ended by asking the daemon."""
    await world.connects(PHONE, "iPhone de Léo", SPOTIFY_COMMANDS)
    await world.realtime_plays("oh my love")
    await world.realtime_pauses()
    await world.advance(1)
    await world.tears_the_stream_down()
    assert world.active() and world.phase() == "paused"
    await world.advance(DELAY + 1)
    assert world.drops and not world.active()


# === One session per sender ===

async def test_the_next_sender_inherits_nothing(world):
    """E19: the next session published the previous track's duration and
    position (the lock screen drew the phone's bar under the Mac)."""
    await _phone_plays(world)
    await world.advance(30)
    await world.leaves(PHONE)
    assert not world.active()
    await world.connects(MAC, "Mac mini de Léo")
    await world.send(ssnc("pbeg"))
    session = world.session()
    assert session is not None
    assert session["duration_ms"] is None and session["position"] is None
    assert session["title"] is None and session["artwork"] is None
    assert world.state()["details"]["artwork_width"] is None


async def test_a_newcomer_replaces_a_sender_whose_goodbye_is_late(world):
    """E20: a second sender's `conn` while the first one's `disc` is still to
    come (measured a minute late, 2026-09-03) — the newcomer showed the first
    sender's title and cover until it sent tags of its own."""
    await _phone_plays(world)
    await world.connects(MAC, "Mac mini de Léo")
    session = world.session()
    assert session["senders"] == ["Mac mini de Léo"]
    assert session["title"] is None and session["artwork"] is None
    await world.says_goodbye(PHONE)          # the late goodbye
    assert world.active() and world.session()["senders"] == ["Mac mini de Léo"]


async def test_a_takeover_hands_the_output_to_the_newcomer(world):
    """As measured: the first sender's pend + disc, then the newcomer's conn."""
    await _phone_plays(world)
    await world.send(ssnc("pend"), ssnc("disc", PHONE.encode()))
    await world.connects(MAC, "Mac mini de Léo")
    await world.realtime_plays()
    assert world.active() and world.session()["senders"] == ["Mac mini de Léo"]
    assert world.session()["title"] == "Energy"


# === The daemon ===

async def test_a_killed_daemon_ends_the_session_at_once(world):
    """A daemon killed outright says nothing. Its death ends the session when
    it happens — not at the next 10 s check."""
    await _phone_plays(world)
    await world.kill_daemon()
    assert not world.active()
    assert world.errors() == [SourceErrorReason.STREAM_DISCONNECTED]


async def test_a_session_on_the_restarted_daemon_is_not_cut(world):
    """E21: the pid was read once per session and kept, so a sender that
    reconnected to the restarted daemon within the check interval was watched
    against the dead one — and cut, with a banner, while it played."""
    await _phone_plays(world)
    await world.kill_daemon()
    await world.systemd_restarts_it()
    await _phone_plays(world)
    await world.advance(11)
    assert world.playing()
    assert world.session()["title"] == "Soul Officer"


async def test_a_daemon_that_does_not_answer_is_restarted(world):
    """DropSession failing leaves one lever, the restart the auto-stop used to
    do. The end is still an idle timeout: no banner."""
    world.drop_answers = False
    await _phone_plays(world)
    await world.pauses()
    await world.advance(DELAY + 1)
    assert world.drops and world.restarts
    assert not world.active()
    assert world.errors() == []


async def test_a_failed_restart_still_hears_the_sender(world):
    """E22: a restart that failed left the last ACTIVE on screen with the pipe
    reader already torn down, so nothing the sender did afterwards was heard.
    The daemon still holds the session: it stays, and a resume is seen."""
    world.drop_answers = False
    world.restart_ok = False
    await _phone_plays(world)
    await world.pauses()
    await world.tears_the_stream_down()
    await world.advance(DELAY + 1)
    assert world.restarts
    assert world.active() and not world.playing()
    await world.restarts_the_stream()
    assert world.playing()


# === Milō's own ends ===

async def test_a_source_switch_ends_the_session(world):
    await _phone_plays(world)
    await world.leave()
    assert world.state()["source"] == "none"
    assert world.session_ends() == ["source_switch"]
    await world.select()
    assert not world.active()
    assert world.state()["resume"] is None


async def test_a_reroute_ends_the_session(world):
    """AirPlay cannot be moved to the other output under a sender: the daemon
    restarts, and the sender has to reconnect (REROUTE = END_SESSION)."""
    await _phone_plays(world)
    await world.reroute()
    assert world.state()["source"] == "airplay"
    assert not world.active()
    assert world.session_ends() == ["reroute"]
    assert world.errors() == []


# === Found by the code review (2026-09-24) ===

async def test_a_new_stream_after_a_pause_is_not_paused(world):
    """A phone paused in Music, its stream torn down, then Spotify on the same
    phone: a Realtime stream opens with no `pres`. It played — and stayed
    PAUSED, so the idle timeout dropped a sender that was playing."""
    await _phone_plays(world)
    await world.pauses()
    await world.tears_the_stream_down()
    await world.realtime_plays("Spotify track")
    assert world.playing()
    await world.advance(DELAY + 1)
    assert world.drops == [] and world.active()


async def test_announcements_left_over_from_a_stopped_source_are_not_replayed(world):
    """What the feeds posted just before a source switch belongs to that run:
    replayed at the next start, it opened a session with no sender that the
    next sender's `conn` adopted — with the previous sender's title (E19)."""
    await _phone_plays(world)
    world.remote.on_update(RemoteUpdate("now_playing", world.item("Left Over")))  # posted, not handled
    await world.leave()
    await world.select()
    await world.send(ssnc("conn", MAC.encode()), ssnc("snam", "Mac mini de Léo".encode()))
    assert world.session()["title"] is None


async def test_leftovers_after_a_goodbye_open_nothing(world):
    """An item arriving after the `disc` is about the session that ended: it
    opens nothing."""
    await _phone_plays(world)
    await world.leaves(PHONE)
    await world.publishes(world.item("After The Goodbye"), world.cover())
    assert not world.active()


async def test_the_next_sender_never_shows_the_departed_ones_item(world):
    """shairport-sync clears the item only when the next sender connects, and
    that signal can trail the newcomer's `conn`: the departed sender's title
    would sit under the newcomer's name meanwhile."""
    await _phone_plays(world)
    await world.leaves(PHONE)
    await world.send(ssnc("conn", MAC.encode()), ssnc("snam", b"Mac mini"))
    assert world.session()["senders"] == ["Mac mini"]
    assert world.session()["title"] is None and world.controls() == []


# === A track change is not a pause (measured 2026-10-16) ===

def _phases_since(world: AirPlayWorld, since: int):
    return [s["session"]["phase"] for s in world.published()[since:] if s["session"]]


async def test_milos_next_shows_no_pause_while_the_phone_skips(world):
    """Next from Milō on iPhone Music: `paus`, then `pres` 90-250 ms later. The
    main button drew play for that dip on every press."""
    await _phone_plays(world)
    since = len(world.published())

    assert (await world.command("next"))["success"]
    await world.send(ssnc("paus"))
    await world.advance(0.12)
    await world.send(ssnc("pres"))
    await world.track("L.A.D.Y")

    assert "paused" not in _phases_since(world, since)
    assert world.playing() and world.controls()[0] == "pause"


async def test_a_phone_loading_the_next_track_is_loading_not_paused(world):
    """Measured: a next left iPhone Music at `paus` for seconds while it
    loaded the track — the screen said paused, and offered play."""
    await _phone_plays(world)
    since = len(world.published())

    await world.command("next")
    await world.send(ssnc("paus"))
    await world.advance(3)
    assert world.phase() == "loading" and world.controls()[0] == "pause"
    await world.send(ssnc("pres"))
    await world.track("Steam")

    assert "paused" not in _phases_since(world, since)
    assert world.playing()


async def test_a_skip_that_never_resumes_becomes_a_pause(world):
    await _phone_plays(world)
    await world.command("next")
    await world.send(ssnc("paus"))

    await world.advance(airplay_source.SKIP_LOADING_S + 1)

    assert world.phase() == "paused" and world.controls()[0] == "resume"


async def test_the_phones_own_skip_shows_no_pause(world):
    """A skip pressed on the phone is the same paus/pres pair, 160 ms apart."""
    await _phone_plays(world)
    since = len(world.published())

    await world.skips()

    assert "paused" not in _phases_since(world, since)


async def test_a_pause_on_the_phone_shows_once_held_where_it_stopped(world):
    await _phone_plays(world)
    await world.advance(20)
    since = len(world.published())
    playhead = world.position_ms()

    await world.send(ssnc("paus"))
    assert world.playing(), "a paus shown at once flickers on every skip"
    await world.advance(airplay_source.PAUS_SETTLE_S)

    assert world.phase() == "paused"
    assert abs(world.position_ms() - playhead) <= 50, "the playhead ran on through the hold"
    assert _phases_since(world, since).count("paused") == 1


async def test_a_pause_from_milo_shows_at_once(world):
    await _phone_plays(world)

    await world.command("pause")
    await world.send(ssnc("paus"))

    assert world.phase() == "paused" and world.controls()[0] == "resume"


async def test_a_mac_loading_the_next_track_is_not_paused(world):
    """A Mac's next: the new item at rate 0 about a second before rate 1."""
    await world.connects(MAC, "Mac mini de Léo", MAC_COMMANDS)
    await world.realtime_plays()
    since = len(world.published())

    await world.command("next")
    item = world.item("My Storm", rate=0.0)
    await world.publishes(item, world.cover(name="storm"))
    assert world.phase() == "loading"
    await world.advance(1.1)
    await world.signal(RemoteUpdate("now_playing", world.moved(world.now_playing, rate=1.0)))

    assert "paused" not in _phases_since(world, since)
    assert world.playing() and world.session()["title"] == "My Storm"


async def test_a_paused_phone_does_not_play_on_in_the_playhead(world):
    """The item a phone had before its pause says rate 1: read after the
    pause (at the stream's teardown, say), it carried the playhead forward
    through the whole pause."""
    await _phone_plays(world)
    await world.advance(5)
    await world.pauses()
    stopped = world.position_ms()

    await world.advance(30)
    await world.tears_the_stream_down()

    assert abs(world.position_ms() - stopped) <= 50


async def test_prev_restarting_the_track_brings_the_playhead_back(world):
    """Measured 2026-10-16: prev 20 s into a track restarts it, and the item
    saying 0:00 at rate 1 is stamped 2 ms before Milō heard the `pres` — read
    as stale, the bar ran on from where the track had been."""
    await _phone_plays(world)
    await world.advance(20)
    item = world.now_playing

    await world.command("prev")
    await world.send(ssnc("paus"))
    await world.advance(0.17)
    stamp = world.moved(item, elapsed_ms=0, rate=1.0)
    await world.send(ssnc("pres"))
    await world.signal(RemoteUpdate("now_playing", world.moved(item, elapsed_ms=0, rate=1.0,
                                                               at=stamp.at - 0.002)))

    assert world.position_ms() < 1000


async def test_a_seek_on_the_phone_moves_the_bar(world):
    """A seek on iPhone Music (measured 2026-10-16) is `paus`, `pres` 176 ms
    later, then the item at the new playhead stamped ~2 ms before Milō heard
    the `pres` — the same stamp as a restart by prev, and it was dropped the
    same way."""
    await _phone_plays(world)
    await world.advance(20)
    item = world.now_playing

    await world.send(ssnc("paus"))
    await world.advance(0.176)
    stamp = world.moved(item).at - 0.002
    await world.send(ssnc("pres"))
    await world.signal(RemoteUpdate("now_playing", world.moved(item, elapsed_ms=150_000, at=stamp)))

    assert abs(world.position_ms() - 150_000) < 1000
