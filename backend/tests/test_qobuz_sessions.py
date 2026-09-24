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


@pytest.fixture
async def world(monkeypatch, tmp_path):
    w = QobuzWorld(monkeypatch, tmp_path)
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
    assert world.meta()["title"] == "On & On"
    assert world.meta()["duration"] == 226000
    assert world.meta()["account_authenticated"] is True


# === The phase is the player's ===

async def test_a_skip_caught_loading_is_loading_on_the_same_session(world):
    """The ~100 ms LOADING of a skip reads "idle" with no track upstream. The
    old source held it for three ticks and then dropped to READY — and never
    said it was loading. It is LOADING, on the track still on screen."""
    await world.app_plays(ON_AND_ON)
    for _ in range(5):
        await world.loads(NEXT_LIFETIME)
    assert world.active() and world.buffering()
    assert world.meta()["title"] == "On & On"


async def test_the_next_track_replaces_the_playhead(world):
    await world.app_plays(ON_AND_ON)
    await world.plays_on(40)
    await world.loads(NEXT_LIFETIME)
    world.sidecar.player_state = "playing"
    await world.advance(1)
    assert world.playing() and world.meta()["title"] == "Next Lifetime"
    assert world.meta()["position"] < 2000


async def test_a_preview_running_into_the_next_track_keeps_playing(world):
    """Measured: the 30 s preview's end is the title changing, no state."""
    await world.app_plays(ON_AND_ON)
    await world.plays_on(29)
    world.sidecar.track, world.sidecar.position_ms = TYRONE, 0
    await world.advance(1)
    assert world.playing() and world.meta()["title"] == "Tyrone"


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
    the bar; the frontend interpolates, so a steady playhead is a correction
    every 10 s and nothing else."""
    await world.app_plays(ON_AND_ON)
    before = len(world.published())
    await world.plays_on(20)
    assert len(world.published()) == before
    assert 1 <= len(world.positions()) <= 3


async def test_a_seek_in_the_app_goes_out_at_once(world):
    await world.app_plays(ON_AND_ON)
    await world.plays_on(12)
    sent = len(world.positions())
    await world.app_seeks(150000)
    assert len(world.positions()) == sent + 1
    assert world.positions()[-1]["position"] >= 150000


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
    assert world.positions()[-1]["position"] == 180000


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
    await world.app_plays(ON_AND_ON)
    assert await world.source.on_allow_app_volume_changed(False)
    await world.advance(1)
    assert not world.active()
    assert world.errors() == []
    assert len(world.restarts) == 1
    assert world.volume_flag.read_text() == "0"


async def test_a_poll_the_old_sidecar_answers_opens_nothing_after_a_lock(world):
    """The poll keeps running across the volume-lock restart: an answer the old
    sidecar gave in that window, handled after the session ended, reopened it
    — ACTIVE flashed between two READYs."""
    await world.app_plays(ON_AND_ON)
    world.poll_lands_during_restart = True
    before = len(world.published())
    assert await world.source.on_allow_app_volume_changed(False)
    await world.advance(3)
    assert [p["state"] for p in world.published()[before:]] == ["ready"]


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
    assert world.meta()["title"] == "Tyrone" and world.meta()["duration"] == 226000
    world.sidecar.duration_ms = 30000
    await world.advance(1)
    assert world.meta()["duration"] == 30000


async def test_an_idle_speaker_publishes_nothing_per_poll(world):
    """Idle is a steady state: the poll runs at ~1 Hz for as long as Qobuz is
    selected, and READY said everything the next tick would."""
    await world.advance(1)
    before = len(world.published())
    await world.advance(10)
    assert len(world.published()) == before


async def test_a_logout_while_idle_is_published(world):
    await world.advance(1)
    world.sidecar.authenticated = False
    await world.advance(2)
    assert world.meta()["account_authenticated"] is False


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
    assert all(p["account_authenticated"] is True for p in world.published())
