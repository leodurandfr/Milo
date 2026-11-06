"""Qobuz sessions, driven through the sidecar the unit was measured to run
(tests/qobuz_world.py; docs: source architecture, phase 3c).

What is asserted is what the wire says, and what Milō did to systemd, after
the app or the sidecar did something. Each gap test was seen red on the code
before phase 3c for the reason its docstring gives.
"""
import pytest

from backend.core.models.ws_events import SourceErrorReason
from backend.tests.qobuz_world import NEXT_LIFETIME, ON_AND_ON, TYRONE, QobuzWorld

DELAY = 120   # make_settings' audio.auto_stop_delay

# The owner's bound for a pause or a resume made in the app to reach the
# screen, and for the bar's step when it does (measured at 1 Hz: 0.5-1.0 s).
SCREEN_WITHIN_S = 0.25


@pytest.fixture
async def world(monkeypatch, tmp_path):
    w = QobuzWorld(monkeypatch, tmp_path)
    await w.boot()
    await w.select()
    yield w
    await w.source.shutdown()


# === A session opens at its first track ===

async def test_the_app_picking_the_speaker_shows_nothing_yet(world):
    """Measured: the app lands paused with nothing loaded — no track to draw."""
    await world.app_picks_milo()
    await world.advance(5)
    assert not world.active()


async def test_an_active_status_with_no_title_never_opens_a_session(world):
    """E14: the old source held an active status with no track for three
    ticks, then published the session anyway — a player with nothing on it
    over audio. A session opens at its first displayable track, however long
    that takes."""
    await world.app_picks_milo()
    world.sidecar.player_state = "playing"
    world.sidecar.track, world.sidecar.duration_ms = {**ON_AND_ON, "title": ""}, 226000
    await world.advance(5)
    assert not world.active()


async def test_a_track_that_plays_is_published_playing(world):
    await world.app_plays(ON_AND_ON)
    assert world.playing()
    assert world.session()["title"] == "On & On"
    assert world.session()["duration_ms"] == 226000
    assert world.availability() is None


# === The phase is the player's ===

async def test_a_skip_caught_loading_is_loading_on_the_same_session(world):
    """The ~100 ms LOADING of a skip reads "idle" with no track upstream. The
    old source held it for three ticks and then dropped to READY — and never
    said it was loading. It is LOADING, on the track still on screen."""
    await world.app_plays(ON_AND_ON)
    for _ in range(5):
        await world.loads(NEXT_LIFETIME)
    assert world.active() and world.buffering()
    assert world.session()["title"] == "On & On"


async def test_the_next_track_replaces_the_playhead(world):
    await world.app_plays(ON_AND_ON)
    await world.plays_on(40)
    await world.loads(NEXT_LIFETIME)
    world.sidecar.player_state = "playing"
    await world.advance(1)
    assert world.playing() and world.session()["title"] == "Next Lifetime"
    assert world.position_ms() < 2000


async def test_a_preview_running_into_the_next_track_keeps_playing(world):
    """Measured: the 30 s preview's end is the title changing, no state."""
    await world.app_plays(ON_AND_ON)
    await world.plays_on(29)
    world.sidecar.track, world.sidecar.position_ms = TYRONE, 0
    await world.advance(1)
    assert world.playing() and world.session()["title"] == "Tyrone"


async def test_a_pause_is_paused(world):
    await world.app_plays(ON_AND_ON)
    await world.app_pauses()
    assert world.active() and not world.playing() and not world.buffering()


async def test_a_first_track_that_fails_is_reported_too(world):
    """Upstream builds now_playing for playing and paused only: an ERROR on the
    session's first track carries no title, so no session opens — and the
    failure the app caused went unreported."""
    await world.app_picks_milo()
    world.sidecar.player_state = "error"
    await world.advance(3)
    assert not world.active()
    assert world.errors() == [SourceErrorReason.PLAYBACK_FAILED]


async def test_a_track_that_fails_to_load_is_reported_once(world):
    """ERROR (the stream URL refused, the start raised) is held until the next
    command: nothing plays, the banner says so once, the session stays for the
    app to pick another track."""
    await world.app_plays(ON_AND_ON)
    await world.track_fails()
    await world.advance(3)
    assert world.active() and not world.playing()
    assert world.errors() == [SourceErrorReason.PLAYBACK_FAILED]


# === The playhead ===

async def test_a_moving_playhead_is_not_a_full_state_every_second(world):
    """The old source re-published the whole state at every 1 Hz poll to move
    the bar; every client moves it from the anchor, so a playhead that keeps
    pace with it puts nothing on the wire at all."""
    await world.app_plays(ON_AND_ON)
    before, sent = len(world.published()), len(world.positions())
    await world.plays_on(20)
    assert len(world.published()) == before
    assert len(world.positions()) == sent
    assert world.position_ms() == world.sidecar.position_ms


async def test_four_readings_a_second_put_nothing_on_the_wire(world):
    """The poll runs at 4 Hz so a pause reaches the screen in a quarter of a
    second; the readings in between must still cost nothing — through the
    settling window a resume opens too, where a reading 250 ms off the anchor
    would move it (core/audio_source.py, SETTLE_TOLERANCE_MS)."""
    await world.app_plays(ON_AND_ON)
    await world.app_pauses()
    await world.app_resumes()
    before, sent, polls = len(world.published()), len(world.positions()), world.sidecar.polls
    await world.plays_on(20)
    assert world.sidecar.polls - polls >= 20 / SCREEN_WITHIN_S
    assert len(world.published()) == before
    assert len(world.positions()) == sent
    assert world.position_ms() == world.sidecar.position_ms


async def test_a_pause_reaches_the_screen_within_a_quarter_second(world):
    """Measured at 1 Hz: every pause was published 0.5-1.0 s after the sound
    stopped, the bar running on meanwhile and then jumping back 0.76-0.92 s.
    The worst case is a pause pressed just after a poll."""
    await world.app_plays(ON_AND_ON)
    await world.plays_on(10.01)
    playing = world.session()["position"]
    world.sidecar.player_state = "paused"
    stopped_at = world.sidecar.position_ms
    await world.advance(SCREEN_WITHIN_S)
    assert world.phase() == "paused"
    paused = world.session()["position"]
    assert paused["ms"] == stopped_at
    ran_on = playing["ms"] + (paused["at"] - playing["at"]) * 1000 - paused["ms"]
    assert 0 <= ran_on <= SCREEN_WITHIN_S * 1000


async def test_a_resume_reaches_the_screen_within_a_quarter_second(world):
    """Measured at 1 Hz: every resume jumped the bar ~0.3 s forward, the music
    having played that long before the poll saw it."""
    await world.app_plays(ON_AND_ON)
    await world.plays_on(10)
    await world.app_pauses()
    await world.advance(5.01)
    paused = world.session()["position"]
    world.sidecar.player_state = "playing"
    await world.advance(SCREEN_WITHIN_S)
    assert world.playing()
    jumped = world.session()["position"]["ms"] - paused["ms"]
    assert 0 <= jumped <= SCREEN_WITHIN_S * 1000


async def test_a_seek_in_the_app_goes_out_at_once(world):
    await world.app_plays(ON_AND_ON)
    await world.plays_on(12)
    sent = len(world.positions())
    await world.app_seeks(150000)
    assert len(world.positions()) == sent + 1
    assert world.positions()[-1]["position"]["ms"] >= 150000


async def test_a_seek_while_paused_goes_out(world):
    """The playhead is frozen in PAUSED and the position axis is not part of
    the published state: a seek made in the app while paused stayed off the
    screen until play."""
    await world.app_plays(ON_AND_ON)
    await world.plays_on(12)
    await world.app_pauses()
    sent = len(world.positions())
    await world.app_seeks(180000)
    assert len(world.positions()) == sent + 1
    assert world.positions()[-1]["position"]["ms"] == 180000


async def test_a_new_track_is_not_followed_by_a_repeated_playhead(world):
    """The full state published for a new track carries its playhead; a
    position update a second later saying the same thing is noise."""
    await world.app_plays(ON_AND_ON)
    await world.plays_on(12)
    world.sidecar.track, world.sidecar.position_ms = TYRONE, 0
    await world.advance(1)
    sent = len(world.positions())
    world.sidecar.position_ms = 1000
    await world.advance(1)
    assert len(world.positions()) == sent


# === Ends ===

async def test_a_pause_asks_the_sidecar_to_end_the_session(world):
    """E15: a paused Qobuz session stayed ACTIVE for good. No request ends one
    (measured: releasing ownership leaves the app on Milō), a restart does:
    after the delay the sidecar is restarted once, the session ends with no
    banner, and the app has let go."""
    await world.app_plays(ON_AND_ON)
    await world.app_pauses()
    await world.advance(DELAY - 2)
    assert world.active() and world.restarts == []
    await world.advance(3)
    assert not world.active()
    assert len(world.restarts) == 1
    assert world.errors() == []
    await world.advance(DELAY + 1)
    assert len(world.restarts) == 1


async def test_a_restart_milo_asked_for_logs_no_outage(world, caplog):
    """The sidecar refuses connections for ~0.45 s after it starts (measured),
    and every idle timeout now restarts it: a warning per auto-stop would be a
    fault reported for Milō's own request."""
    await world.app_plays(ON_AND_ON)
    await world.app_pauses()
    with caplog.at_level("WARNING", logger="source.qobuz.monitor"):
        await world.advance(DELAY + 3)
    assert len(world.restarts) == 1 and not world.active()
    assert [r for r in caplog.records if r.name == "source.qobuz.monitor"] == []


async def test_a_sidecar_that_dies_is_one_warning(world, caplog):
    await world.app_plays(ON_AND_ON)
    with caplog.at_level("WARNING", logger="source.qobuz.monitor"):
        await world.kill_sidecar()
        await world.advance(5)
        await world.systemd_restarts_it()
        await world.advance(3)
    assert [r.levelname for r in caplog.records if r.name == "source.qobuz.monitor"] == ["WARNING"]


async def test_a_resume_withdraws_the_end_request(world):
    await world.app_plays(ON_AND_ON)
    await world.app_pauses()
    await world.advance(DELAY / 2)
    await world.app_resumes()
    await world.advance(DELAY)
    assert world.playing() and world.restarts == []


async def test_a_resume_the_poll_has_not_shown_yet_is_not_cut(world):
    """The pause timer decided on the last poll, up to an interval old: a play
    pressed in the app within that interval was cut by the restart, the music
    that had just come back stopping in the room."""
    await world.app_plays(ON_AND_ON)
    await world.app_pauses()
    await world.advance(DELAY - 1)
    world.sidecar.player_state = "playing"
    await world.advance(1.5)
    assert world.restarts == []
    assert world.playing()
    assert world.session_ends() == []


async def test_an_idle_end_the_old_sidecar_answers_after_stays_an_idle_end(world):
    """A play the old sidecar reported while the restart was under way, handled
    after it, withdrew a request already carried out: the exit Milō ordered read
    as a death, with its banner."""
    await world.app_plays(ON_AND_ON)
    await world.app_pauses()
    world.poll_lands_during_restart = True
    world.app_resumes_during_restart = True
    await world.advance(DELAY + 3)
    assert len(world.restarts) == 1
    assert world.session_ends() == ["idle_timeout"]
    assert world.errors() == []


async def test_a_poll_the_old_sidecar_answers_opens_nothing_after_an_idle_end(world):
    """An answer the old sidecar gave before it exited, handled after the end,
    reopened the session it described — a paused phantom between two idle
    states, and a second session_ended."""
    await world.app_plays(ON_AND_ON)
    await world.app_pauses()
    world.poll_lands_during_restart = True
    world.old_answer_lands_after_exit = True
    await world.advance(DELAY - 1)
    before = len(world.published())
    await world.advance(4)
    after = world.published()[before:]
    assert len(world.restarts) == 1
    assert after and all(p["session"] is None for p in after)
    assert world.session_ends() == ["idle_timeout"]


async def test_the_app_picking_another_output_ends_the_session_at_once(world):
    """SET_ACTIVE(false) is the app leaving, not a between-tracks blip: the old
    source held ACTIVE three more ticks on a speaker nobody was using."""
    await world.app_plays(ON_AND_ON)
    await world.app_picks_another_output()
    assert not world.active()
    assert world.errors() == []


async def test_a_sidecar_killed_mid_play_ends_the_session_at_once(world):
    """E17 (measured on the old code: 9 s of "playing" on a dead sidecar, then
    READY with no message): the session is bound to the process."""
    await world.app_plays(ON_AND_ON)
    await world.kill_sidecar()
    assert not world.active()
    assert world.errors() == [SourceErrorReason.STREAM_DISCONNECTED]


async def test_the_banner_goes_when_a_track_plays_again(world):
    """The banner outlived its cause, over a speaker the app had picked again
    and that played fine."""
    await world.app_plays(ON_AND_ON)
    await world.kill_sidecar()
    await world.systemd_restarts_it()
    assert world.envelopes("source", "error_cleared") == []
    await world.app_plays(NEXT_LIFETIME)
    assert world.playing()
    assert len(world.envelopes("source", "error_cleared")) >= 1


async def test_a_backend_restart_under_the_session_is_not_a_death(world):
    """systemd stops the sidecar first (BindsTo): a stop on purpose, no banner."""
    await world.app_plays(ON_AND_ON)
    await world.systemd.stop("milo-qobuz.service")
    await world.advance(1)
    assert not world.active()
    assert world.errors() == []


async def test_a_reroute_ends_the_session_without_a_banner(world):
    await world.app_plays(ON_AND_ON)
    await world.reroute()
    assert not world.active()
    assert world.errors() == []
    assert world.sidecar.up


async def test_locking_the_app_volume_ends_the_session_it_restarts_under(world):
    """A restart the user asked for, not a death: no banner, one restart."""
    world.volume_flag.write_text("1")
    await world.app_plays(ON_AND_ON)
    assert await world.source.on_allow_app_volume_changed(False)
    await world.advance(1)
    assert not world.active()
    assert world.errors() == []
    assert len(world.restarts) == 1
    assert world.volume_flag.read_text() == "0"


async def test_a_volume_lock_under_an_unreadable_probe_is_refused(world):
    """A probe that timed out cannot say whether the sidecar runs. Read as
    stopped, the lock answered "applied" while the app kept the slider of the
    session it joined under; the retry once the probe answers must still
    restart, so the refusal leaves the flag where it was."""
    world.volume_flag.write_text("1")
    await world.app_plays(ON_AND_ON)
    readable = world.systemd.probe_active.side_effect
    world.systemd.probe_active.side_effect = None
    world.systemd.probe_active.return_value = None

    assert await world.source.on_allow_app_volume_changed(False) is False
    await world.advance(1)
    assert world.restarts == []
    # The sidecar reads the flag on every volume command: the lock holds the
    # stream at unity already, and the retry still restarts.
    assert world.volume_flag.read_text() == "0"

    world.systemd.probe_active.side_effect = readable
    assert await world.source.on_allow_app_volume_changed(False) is True
    await world.advance(1)
    assert len(world.restarts) == 1
    assert world.volume_flag.read_text() == "0"


async def test_a_volume_lock_whose_restart_failed_is_retried(world):
    """A restart that did not happen leaves the app on the policy it joined
    under, so the retry must restart even though the flag already holds the
    lock. Put back to "1" instead, the flag let the stream follow the app's
    slider — the sidecar reads it on every volume command — while the settings
    screen showed the lock."""
    world.volume_flag.write_text("1")
    await world.app_plays(ON_AND_ON)
    restart = world.systemd.restart.side_effect
    world.systemd.restart.side_effect = None
    world.systemd.restart.return_value = False

    assert await world.source.on_allow_app_volume_changed(False) is False
    await world.advance(1)
    assert world.volume_flag.read_text() == "0"

    world.systemd.restart.side_effect = restart
    assert await world.source.on_allow_app_volume_changed(False) is True
    await world.advance(1)
    assert len(world.restarts) == 1
    assert world.volume_flag.read_text() == "0"


async def test_a_poll_the_old_sidecar_answers_opens_nothing_after_a_lock(world):
    """The poll keeps running across the volume-lock restart: an answer the old
    sidecar gave in that window, handled after the session ended, reopened it
    — a session flashed between two idle states."""
    world.volume_flag.write_text("1")
    await world.app_plays(ON_AND_ON)
    world.poll_lands_during_restart = True
    before = len(world.published())
    assert await world.source.on_allow_app_volume_changed(False)
    await world.advance(3)
    after = world.published()[before:]
    assert after and all(p["session"] is None for p in after)
    assert world.session_ends() == ["user_stop"]


async def test_an_unanswered_poll_changes_nothing(world):
    """A tick the sidecar answers with 5xx is not a status: the session stays."""
    await world.app_plays(ON_AND_ON)
    world.sidecar.http_status = 503
    await world.advance(5)
    assert world.playing()


async def test_a_new_track_keeps_the_length_until_its_own_is_known(world):
    """ProgressBar renders under `duration > 0` and replays its entrance when
    it comes back: a length dropped for one tick is a visible flicker."""
    await world.app_plays(ON_AND_ON)
    world.sidecar.track, world.sidecar.position_ms = TYRONE, 0
    world.sidecar.duration_ms = 0
    await world.advance(1)
    assert world.session()["title"] == "Tyrone" and world.session()["duration_ms"] == 226000
    world.sidecar.duration_ms = 30000
    await world.advance(1)
    assert world.session()["duration_ms"] == 30000


async def test_an_idle_speaker_publishes_nothing_per_poll(world):
    """Idle is a steady state: the poll runs at 4 Hz for as long as Qobuz is
    selected, and READY said everything the next tick would."""
    await world.advance(1)
    before = len(world.published())
    await world.advance(10)
    assert len(world.published()) == before


async def test_a_logout_while_idle_is_published(world):
    await world.advance(1)
    world.sidecar.authenticated = False
    await world.advance(2)
    assert world.availability() == "no_account"
    assert world.published()[-1]["availability"]["qobuz"] == "no_account"


async def test_a_login_made_in_the_sidecar_outlives_it(monkeypatch, tmp_path):
    """While the sidecar is down, the account is the token cache: a login
    completed while Qobuz played must still read as connected once another
    source is selected, or the source list offers "connect an account" to
    someone who just did."""
    world = QobuzWorld(monkeypatch, tmp_path, logged_in=False)
    await world.select()
    await world.advance(3)
    assert world.availability() == "no_account"
    world.sidecar_logs_in()
    await world.advance(1)
    assert world.availability() is None
    await world.leave()
    assert world.availability() is None
    await world.source.shutdown()


async def test_a_login_made_in_the_sidecar_outlives_a_reroute(monkeypatch, tmp_path):
    """A multiroom toggle stops the sidecar and starts it again from the token
    cache: a login made while Qobuz played must be in it by then, or every
    toggle draws "connect an account" until the first authenticated poll."""
    world = QobuzWorld(monkeypatch, tmp_path, logged_in=False)
    await world.boot()
    await world.select()
    await world.advance(3)
    assert world.availability() == "no_account"
    world.sidecar_logs_in()
    await world.advance(1)
    assert world.availability() is None
    before = len(world.published())
    for _ in range(2):
        await world.reroute()
    after = world.published()[before:]
    assert after and all(p["availability"]["qobuz"] is None for p in after)
    assert world.availability() is None
    assert world.sidecar.up
    await world.source.shutdown()


async def test_a_logout_from_the_settings_screen_is_published(world):
    """The settings screen clears the cache while the sidecar is down, then
    tells the source (api/qobuz_account.py): the state must say so at once,
    not at the next selection."""
    await world.leave()
    assert world.availability() is None
    world.credentials.write_text("{}")
    await world.source.account_changed()
    await world.advance(1)
    assert world.availability() == "no_account"
    assert world.published()[-1]["availability"]["qobuz"] == "no_account"


async def test_a_starting_sidecar_does_not_flash_the_login_prompt(world):
    """Measured: a sidecar coming up answers `authenticated: false` for ~0.3 s
    before its credentials load — and every idle timeout now restarts it. One
    poll landing there drew the "connect your account" card for a second at
    someone who is logged in."""
    for _ in range(2):                      # two idle timeouts, two restarts
        await world.app_plays(ON_AND_ON)
        await world.app_pauses()
        await world.advance(DELAY + 1)
        world.sidecar.authenticated = False     # the poll lands in the window
        await world.advance(1)
        world.sidecar.authenticated = True
        await world.advance(3)
    assert len(world.restarts) == 2
    assert all(p["availability"]["qobuz"] is None for p in world.published())
