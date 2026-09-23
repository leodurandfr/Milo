"""AirPlay sessions, driven through the world shairport-sync was measured to be
(tests/airplay_world.py; docs: source architecture, phase 3a).

What is asserted is what the wire says — the state a screen, the lock screen and
Milo-iOS read — after a sender or the daemon did something. Each gap test was
seen red on the code before phase 3a for the reason its docstring gives.
"""
import pytest

from backend.core.models.ws_events import SourceErrorReason
from backend.tests.airplay_world import MAC, PHONE, PNG, AirPlayWorld, bundle, picture, ssnc

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
    """E55: shairport announces a Buffered pause (`paus`); the screen, the
    lock screen and the screensaver read is_playing, which stayed true."""
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
    assert world.meta().get("title") == "L.A.D.Y"
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


# === Realtime (a Mac's system audio; Spotify on the iPhone) ===

async def test_system_audio_never_times_out(world):
    """Owner decision: a Realtime stream says nothing of a pause, so the
    session is CONNECTED and no idle timeout applies to it."""
    await world.connects(MAC, "Mac mini de Léo")
    await world.system_audio_plays()
    await world.advance(3 * DELAY)
    assert world.active()
    assert world.drops == [] and world.restarts == []


async def test_a_sender_that_stops_sending_times_out(world):
    """Spotify on the iPhone tears its Realtime stream down 1 s after a pause
    and stays connected: a pause, ended by asking the daemon."""
    await world.connects(PHONE, "iPhone de Léo")
    await world.system_audio_plays()
    await world.tears_the_stream_down()
    assert world.active() and not world.playing()
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
    meta = world.meta()
    assert world.active()
    assert "duration" not in meta and "position" not in meta
    assert "title" not in meta and "album_art_url" not in meta


async def test_a_newcomer_replaces_a_sender_whose_goodbye_is_late(world):
    """E20: a second sender's `conn` while the first one's `disc` is still to
    come (measured a minute late, 2026-09-03) — the newcomer showed the first
    sender's title and cover until it sent tags of its own."""
    await _phone_plays(world)
    await world.connects(MAC, "Mac mini de Léo")
    meta = world.meta()
    assert meta.get("client_name") == "Mac mini de Léo"
    assert "title" not in meta and "album_art_url" not in meta
    await world.says_goodbye(PHONE)          # the late goodbye
    assert world.active() and world.meta().get("client_name") == "Mac mini de Léo"


async def test_a_takeover_hands_the_output_to_the_newcomer(world):
    """As measured: the first sender's pend + disc, then the newcomer's conn."""
    await _phone_plays(world)
    await world.send(ssnc("pend"), ssnc("disc", PHONE.encode()))
    await world.connects(MAC, "Mac mini de Léo")
    await world.system_audio_plays()
    assert world.active() and world.meta().get("client_name") == "Mac mini de Léo"
    assert world.meta().get("title") == "Energy"


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
    assert world.meta().get("title") == "Soul Officer"


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
    assert world.state()["active_source"] == "none"
    await world.select()
    assert not world.active()
    assert "title" not in world.meta()


async def test_a_reroute_ends_the_session(world):
    """AirPlay cannot be moved to the other output under a sender: the daemon
    restarts, and the sender has to reconnect (REROUTE = END_SESSION)."""
    await _phone_plays(world)
    await world.reroute()
    assert world.state()["active_source"] == "airplay"
    assert not world.active()
    assert world.errors() == []


# === Found by the code review (2026-09-24) ===

async def test_a_new_stream_after_a_pause_is_not_paused(world):
    """A phone paused in Music, its stream torn down, then Spotify on the same
    phone: a Realtime stream opens with no `pres`. It played — and stayed
    PAUSED, so the idle timeout dropped a sender that was playing."""
    await _phone_plays(world)
    await world.pauses()
    await world.tears_the_stream_down()
    await world.system_audio_plays("Spotify track")
    assert world.playing()
    await world.advance(DELAY + 1)
    assert world.drops == [] and world.active()


async def test_announcements_left_over_from_a_stopped_source_are_not_replayed(world):
    """What the reader posted just before a source switch belongs to that run:
    replayed at the next start, it opened a session with no sender that the
    next sender's `conn` adopted — with the previous sender's title (E19)."""
    await _phone_plays(world)
    await world._write(*bundle(1234, "Left Over"))     # posted, not handled yet
    await world.leave()
    await world.select()
    await world.connects(MAC, "Mac mini de Léo")
    assert world.meta().get("title") is None
    assert world.meta().get("client_name") == "Mac mini de Léo"


async def test_leftovers_after_a_goodbye_open_nothing(world):
    """Tags or a cover arriving after the `disc` are about the session that
    ended: they opened a sender-less CONNECTED session nothing ever closed."""
    await _phone_plays(world)
    await world.leaves(PHONE)
    await world.send(*bundle(99, "After The Goodbye"), *picture(99, PNG))
    assert not world.active()
