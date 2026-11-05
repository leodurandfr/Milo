"""Tidal sessions, driven through the tisoc daemon the unit was measured to run
(tests/tidal_world.py; docs: source architecture, phase 3b).

What is asserted is what the wire says, and what Milō did to the daemon, after
the sender or the daemon did something. Each gap test was seen red on the code
before phase 3b for the reason its docstring gives.
"""
import pytest

from backend.core import audio_source
from backend.core.models.ws_events import SourceErrorReason
from backend.sources.tidal.source import SOUND_TIMEOUT_S
from backend.tests.tidal_world import (
    AUDIO_FORMAT, HYPNOTIZE, KEEP_IT_THORO, SOUND_AFTER_S, STILL_DRE, TICK_S, TidalWorld, media, status,
)

DELAY = 120   # make_settings' audio.auto_stop_delay


@pytest.fixture
async def world(monkeypatch, tmp_path):
    w = TidalWorld(monkeypatch, tmp_path)
    await w.select()
    yield w
    await w.source.shutdown()


# === A session opens at its first track ===

async def test_a_session_with_no_track_yet_shows_nothing(world):
    """E14 (measured: 260 ms): `notifySessionState` opened a session
    with no title, drawn "playing" with nothing named. A session opens at its
    first displayable track."""
    await world.mac_picks_the_speaker()
    assert not world.active()


async def test_a_media_frame_that_names_nothing_opens_nothing(world):
    """E14's rule, on the frame that would open a session: one with no title
    has nothing to draw."""
    from backend.tests.tidal_world import media
    await world.mac_picks_the_speaker()
    untitled = media("x")
    untitled["mediaInfo"]["metadata"]["title"] = ""
    await world.sends(untitled, status("BUFFERING", 0, 271000))
    assert not world.active()


async def test_a_sender_that_plays_is_loading_then_playing(world):
    """The daemon's PLAYING at 0 ends a load, it does not start the sound
    (2026-11-05): the track plays from its audio format."""
    await world.mac_picks_the_speaker()
    await world.advance(0.4)
    await world.sends({"command": "notifyMediaChanged", "mediaInfo": {"metadata": {
        "title": STILL_DRE, "artists": ["Dr. Dre"], "albumTitle": "2001", "duration": 271000}}},
        status("BUFFERING", 0, 271000))
    assert world.buffering() and world.session()["title"] == STILL_DRE
    await world.sends(status("PLAYING", 0, 271000))
    assert world.buffering()
    await world.advance(SOUND_AFTER_S)
    await world.sends(AUDIO_FORMAT)
    assert world.playing() and not world.buffering()


async def test_a_repeated_media_frame_does_not_drop_the_spinner(world):
    """E70 (measured): the daemon sends each track's media twice, the second
    between two BUFFERING frames; a media frame replaced the whole record and
    dropped the loading phase, so the spinner blinked off and on as a track opened."""
    await world.mac_picks_the_speaker()
    await world.advance(0.4)
    from backend.tests.tidal_world import media
    await world.sends(media(STILL_DRE), status("PAUSED", 0, 271000), status("BUFFERING", 0, 271000))
    assert world.buffering()
    await world.advance(0.35)
    await world.sends(media(STILL_DRE, custom=True))
    assert world.buffering()


async def test_the_track_duration_is_what_the_account_may_play(world):
    """A preview: the status says 30 s where the media says the whole track."""
    await world.mac_plays(STILL_DRE)
    assert world.session()["duration_ms"] == 30066


async def test_a_skip_while_paused_stays_paused(world):
    await world.mac_plays(STILL_DRE)
    await world.mac_pauses()
    await world.mac_skips_to(KEEP_IT_THORO)
    assert world.active() and not world.playing() and not world.buffering()
    assert world.session()["title"] == KEEP_IT_THORO


async def test_a_track_running_into_the_next_keeps_playing(world):
    from backend.tests.tidal_world import media
    await world.mac_plays(STILL_DRE)
    await world.sends(media(HYPNOTIZE, custom=True))
    assert world.playing() and world.session()["title"] == HYPNOTIZE


async def test_a_new_track_carries_nothing_of_the_previous_ones_playhead(world):
    """A track that runs into the next is announced by its media alone: the
    previous track's position and preview length are not the new one's."""
    from backend.tests.tidal_world import media
    await world.mac_plays(STILL_DRE)
    await world.plays_on(3)
    await world.sends(media(HYPNOTIZE, duration=230000))
    assert world.session()["position"] is None
    assert world.session()["duration_ms"] == 230000
    await world.sends(media(HYPNOTIZE, duration=230000, custom=True), status("PLAYING", 500))
    assert world.position_ms() == 500
    assert world.session()["duration_ms"] == 30066


async def test_repeated_idle_frames_publish_once(world):
    """E18 (measured: two identical publishes per pair of IDLE frames, three
    when a sender left): an IDLE frame always forced a full-state publish."""
    await world.mac_plays(STILL_DRE)
    await world.mac_pauses()
    await world.sends(status("IDLE"))
    count = len(world.published())
    await world.sends(status("IDLE"), status("IDLE"))
    assert len(world.published()) == count


async def test_a_sender_that_leaves_ends_the_session(world):
    await world.mac_plays(STILL_DRE)
    await world.mac_leaves()
    assert not world.active()
    assert world.errors() == []


# === A pause ends, as everywhere ===

async def test_a_pause_ends_the_session_by_restarting_the_daemon(world):
    """E15 (Tidal, measured: still ACTIVE 60 s into a pause with a 30 s delay).
    No tisoc command ends a session (interrupt, stop and stopService were
    measured), and a restart of the daemon is what makes the sender let go;
    the speaker is back at once, and nothing is reported as a failure."""
    await world.mac_plays(STILL_DRE)
    await world.mac_pauses()
    await world.advance(DELAY + 1)
    assert len(world.restarts) == 1
    assert not world.active()
    assert world.errors() == []
    await world.advance(1.1)
    assert world.daemon.received.count("startService") == 2


async def test_a_resume_before_the_delay_keeps_the_session(world):
    await world.mac_plays(STILL_DRE)
    await world.mac_pauses()
    await world.advance(DELAY - 1)
    await world.mac_resumes()
    await world.advance(DELAY)
    assert world.restarts == []
    assert world.playing()


async def test_a_skip_while_paused_does_not_restart_the_delay(world):
    await world.mac_plays(STILL_DRE)
    await world.mac_pauses()
    await world.advance(DELAY - 10)
    await world.mac_skips_to(KEEP_IT_THORO)
    await world.advance(11)
    assert len(world.restarts) == 1


# === Errors and the daemon ===

async def test_a_playback_error_banner_goes_when_a_track_plays(world):
    """E16: the banner of `notifyPlaybackError` was never withdrawn, even with
    the next track playing under it."""
    await world.mac_plays(STILL_DRE)
    await world.playback_fails()
    assert world.errors() == [SourceErrorReason.PLAYBACK_FAILED]
    assert not world.playing()
    await world.mac_skips_to(KEEP_IT_THORO)
    assert world.playing()
    assert world.envelopes("source", "error_cleared")


async def test_a_daemon_killed_mid_play_ends_the_session_at_once(world):
    """E17 (measured: 6 s of "playing", no banner, and for good if the daemon
    did not come back): the session belongs to the daemon's process."""
    await world.mac_plays(STILL_DRE)
    await world.kill_daemon()
    assert not world.active()
    assert world.errors() == [SourceErrorReason.STREAM_DISCONNECTED]
    await world.advance(5)
    await world.systemd_restarts_it()
    assert not world.active()
    assert world.errors() == [SourceErrorReason.STREAM_DISCONNECTED]


async def test_a_reroute_ends_the_session_quietly(world):
    """The daemon writes to the output it started on: a multiroom toggle
    restarts it and the sender has to pick the speaker again."""
    await world.mac_plays(STILL_DRE)
    await world.reroute()
    assert not world.active()
    assert world.errors() == []
    assert world.state()["service"] == "running"


async def test_a_command_with_no_session_is_refused(world):
    result = await world.command("pause")
    assert result["success"] is False
    assert "pause" not in world.daemon.received


async def test_a_backend_restart_under_the_session_is_not_a_death(world):
    """systemd stops the daemon before the backend (BindsTo + After=): a stop
    that was asked for, not a crash."""
    from backend.tests.golden.harness import settle
    await world.mac_plays(STILL_DRE)
    await world._unit_stop()
    await settle()
    assert not world.active()
    assert world.errors() == []


async def test_a_controller_reconnect_to_the_same_daemon_keeps_the_session(world):
    """The socket can drop while the daemon lives on (a framing error): its
    handshake answer on the reconnect is not a restart — the watched process
    says whether the daemon died."""
    await world.mac_plays(STILL_DRE)
    world.daemon.reader.feed_eof()
    world.daemon.reader = None
    await world.advance(1.1)
    assert world.daemon.connections == 2
    assert world.playing() and world.session()["title"] == STILL_DRE


async def test_a_playhead_seen_before_the_track_is_kept(world):
    from backend.tests.tidal_world import media
    await world.mac_picks_the_speaker()
    await world.sends(status("PLAYING", 5000))
    await world.sends(media(STILL_DRE))
    assert world.playing()
    assert world.position_ms() == 5000 and world.session()["duration_ms"] == 30066


async def test_a_status_left_by_the_previous_session_does_not_open_the_next(world):
    """The IDLE that follows a sender's goodbye arrives with no session open;
    the next session, maybe hours later, must not open from it."""
    from backend.tests.tidal_world import media
    await world.mac_plays(STILL_DRE)
    await world.mac_leaves()
    await world.mac_picks_the_speaker()
    await world.sends(media(KEEP_IT_THORO))
    assert world.buffering() and not world.playing()


async def test_a_jump_of_the_playhead_goes_out_at_once(world):
    """A seek on the sender, or a track repeating, moves only the position: a
    client aging the anchor would never see it unless it goes out, alone."""
    await world.mac_plays(STILL_DRE)
    await world.plays_on(2)
    states = len(world.published())
    await world.sends(status("PLAYING", 20000))
    assert [p["position"]["ms"] for p in world.positions()] == [20000]
    assert len(world.published()) == states
    assert world.position_ms() == 20000


async def test_a_burst_without_a_status_does_not_move_the_playhead_back(world):
    """The playhead ages from its anchor; a burst that carried no status (a
    repeated media, setShuffle) must not re-anchor it, or pull it back to the
    last reading."""
    await world.mac_plays(STILL_DRE)
    await world.plays_on(12)
    last = world.daemon.progress
    await world.advance(0.45)
    await world.sends({"command": "setShuffle", "shuffle": False})
    await world.advance(10)
    await world.sends({"command": "notifyAudioFormatUpdated"})
    assert world.positions() == []
    assert world.position_ms() == last + 10450


async def test_a_daemon_restart_nothing_watched_is_still_reported(world):
    """With no watch on the process (its pid could not be read), the daemon's
    handshake after a restart is the only sign of its death: reported once,
    like any other."""
    world.systemd.main_pid.side_effect = lambda *_: None
    await world.mac_plays(STILL_DRE)
    await world.kill_daemon()
    await world.advance(5)
    await world.systemd_restarts_it()
    assert not world.active()
    assert world.errors() == [SourceErrorReason.STREAM_DISCONNECTED]


async def test_an_error_followed_by_play_in_one_burst_raises_no_banner(world):
    """The track the error was about was replaced before the burst ended."""
    await world.mac_plays(STILL_DRE)
    await world.sends({"command": "notifyPlaybackError", "errorCode": 5}, status("PLAYING", 1000))
    assert world.playing()
    assert world.errors() == []


async def test_the_controller_reattaches_at_once_after_the_end_request(world):
    """The restart that ends a paused session puts up a daemon that advertises
    at once; one that a phone reaches before the controller's `startService`
    rejects it and wedges — so the controller does not wait its reconnect delay."""
    await world.mac_plays(STILL_DRE)
    await world.mac_pauses()
    await world.advance(DELAY + 0.5)       # the timer fires at DELAY
    assert len(world.restarts) == 1
    assert world.daemon.connections == 2


async def test_an_audio_device_the_controller_cannot_grant_is_on_screen(world):
    """E27: the daemon asks for the audio device and decodes nothing until it
    is granted. A grant that cannot be written (the socket closed between the
    request and its answer) left Tidal active and silent with an ERROR in the
    journal only — `source.*` loggers never reach the banner."""
    world.daemon.closed_its_end = True

    await world.mac_picks_the_speaker()

    assert world.errors() == [SourceErrorReason.PLAYBACK_FAILED]


async def test_a_granted_audio_device_raises_no_banner(world):
    await world.mac_picks_the_speaker()

    assert world.errors() == []


async def test_an_ungranted_daemon_that_says_playing_keeps_its_banner(world):
    """Review of E27: a PLAYING the daemon reports after a grant that never
    reached it (its socket closed) is not sound — the banner a playing track
    clears (E16) stays until a grant goes through."""
    world.daemon.closed_its_end = True

    await world.mac_plays()

    assert world.errors() == [SourceErrorReason.PLAYBACK_FAILED]
    assert not world.envelopes("source", "error_cleared")


async def test_a_grant_that_goes_through_later_withdraws_the_banner(world):
    """Review of E27: the banner of an ungranted device outlived its cause
    when the next request was granted but no track played afterwards."""
    world.daemon.closed_its_end = True
    await world.mac_picks_the_speaker()
    assert world.errors() == [SourceErrorReason.PLAYBACK_FAILED]

    world.daemon.closed_its_end = False
    await world.sends({"command": "requestResources"})

    assert world.envelopes("source", "error_cleared")


# === A daemon that never leaves loading ===

async def buffers_for(world, seconds: float) -> None:
    """The daemon's BUFFERING, pushed every 0.5 s as it pushes every status."""
    for _ in range(int(seconds / 0.5)):
        await world.advance(0.5)
        await world.sends(status("BUFFERING"))


async def test_a_daemon_that_never_leaves_buffering_is_on_screen(world):
    """E27 as it can happen: a daemon that stops reading its controller socket
    without closing it takes the grant into the kernel buffer, where `send()`
    cannot see it, and buffers forever — the room silent, the session on
    screen, nothing in any log. Past 30 s of loading the session fails: a
    banner, the daemon restarted to make the sender let go, the controller
    attached to the new one at once. The BUFFERING repeated every 0.5 s re-arm
    nothing, or the timer would never fire."""
    await world.mac_picks_the_speaker()
    await world.sends(media(STILL_DRE), status("BUFFERING"))
    await buffers_for(world, 29)
    assert world.buffering() and world.restarts == []

    await buffers_for(world, 2)

    assert world.errors() == [SourceErrorReason.PLAYBACK_FAILED]
    assert len(world.restarts) == 1
    assert world.session_ends() == ["load_failed"]
    assert not world.active()
    assert world.daemon.received.count("startService") == 2


async def test_a_track_that_plays_within_the_loading_budget_is_not_failed(world):
    await world.mac_picks_the_speaker()
    await world.sends(media(STILL_DRE), status("BUFFERING"))
    await buffers_for(world, 29)
    world.daemon.state = "PLAYING"
    await world.sends(status("PLAYING", 0))
    await world.track_sounds()
    await world.plays_on(30)

    assert world.playing()
    assert world.restarts == []
    assert world.errors() == []


async def test_a_rebuffering_after_play_gets_a_whole_budget(world):
    """The timer follows the phase: leaving LOADING disarms it, entering it
    again starts the 30 s from zero."""
    await world.mac_picks_the_speaker()
    await world.sends(media(STILL_DRE), status("BUFFERING"))
    await buffers_for(world, 20)
    world.daemon.state = "PLAYING"
    await world.sends(status("PLAYING", 0))
    await world.track_sounds()
    await world.plays_on(5)
    await world.sends(status("BUFFERING", world.daemon.progress))
    await buffers_for(world, 20)
    assert world.restarts == []

    await buffers_for(world, 11)

    assert len(world.restarts) == 1
    assert world.session_ends() == ["stream_lost"]
    assert world.errors() == [SourceErrorReason.STREAM_DISCONNECTED]


async def test_a_session_that_opens_playing_has_been_heard(world):
    """A session whose first media follows a PLAYING status with a moving
    playhead opens playing; a later stall is a stream lost, not a load that
    never started. (A PLAYING at 0 is not sound: a silent track says it for
    as long as it is left, measured 2026-11-05.)"""
    await world.mac_picks_the_speaker()
    await world.sends(status("PLAYING", 5000), media(STILL_DRE))
    assert world.playing()
    await world.sends(status("BUFFERING", 0))

    await buffers_for(world, 31)

    assert world.session_ends() == ["stream_lost"]


# === A new track plays from its sound (measured 2026-11-05) ===

async def test_no_playing_goes_out_before_the_audio_format(world):
    """The daemon says PLAYING at 0 in the burst that ends a load, and the
    audio begins 0.6-0.8 s later with `notifyAudioFormatUpdated`. Published
    as it came, the bar ran ~1.3 s before the sound on every track."""
    await world.mac_loads(STILL_DRE)
    await world.advance(SOUND_AFTER_S - 0.01)
    assert world.buffering()
    assert "playing" not in [s["session"]["phase"] for s in world.published() if s["session"]]

    await world.sends(AUDIO_FORMAT)

    assert world.playing()


async def test_the_bar_starts_at_zero_at_the_audio_format(world):
    """Anchored at 0 where the sound begins, the bar agrees with the first
    progress the daemon sends half a second later: nothing jumps back."""
    await world.mac_loads(STILL_DRE)
    await world.advance(SOUND_AFTER_S)
    await world.sends(AUDIO_FORMAT)

    assert world.session()["position"] == {"ms": 0, "at": audio_source.wall_time(), "rate": 1.0}
    await world.advance(TICK_S)
    shown = world.position_ms()
    await world.sends(status("PLAYING", 500))
    assert shown == world.position_ms() == 500


async def test_the_bar_follows_the_daemon_through_the_track_and_its_pause(world):
    """Measured 2026-11-05 (`next` from Milō): the pause 8.88 s after the
    PLAYING at 0 reported 7856 — the bar had run ~1 s ahead from the start
    and jumped back at the pause."""
    await world.mac_plays(STILL_DRE)
    await world.plays_on(9)
    on_screen = world.position_ms()

    await world.mac_pauses()

    assert on_screen == world.daemon.progress, "the bar ran ahead of the daemon"
    assert world.position_ms() == on_screen, "the pause pulled the bar back"


async def test_a_track_whose_audio_format_never_comes_plays_on_a_moving_playhead(world):
    """The audio format is the earliest sign of sound, not the only one: a
    PLAYING whose progress moved is sound too, anchored there."""
    await world.mac_loads(STILL_DRE)
    await world.advance(SOUND_AFTER_S + TICK_S)

    await world.sends(status("PLAYING", 500))

    assert world.playing() and world.position_ms() == 500


async def test_a_session_opened_mid_track_waits_for_its_sound_where_it_loads(world):
    """A sender handing over a track it was playing loads it at its position
    (measured 2026-09-24: PAUSED at the sender's position): a PLAYING there
    has not moved, and the bar starts there with the sound."""
    await world.mac_loads(STILL_DRE, at_ms=60000)
    assert world.buffering()
    await world.advance(SOUND_AFTER_S)

    await world.sends(AUDIO_FORMAT)

    assert world.playing() and world.position_ms() == 60000


async def test_a_silent_track_fails_once_its_playing_has_gone_soundless(world):
    """About one track in three on the free tier never sounds: PLAYING at 0,
    then nothing (measured: 37 s). Shown playing, its bar ran over silence
    until someone paused it. Every track that played sounded 0.6-0.8 s after
    that PLAYING, so it is failed SOUND_TIMEOUT_S after it, not after the
    30 s a network load is given."""
    await world.mac_plays(STILL_DRE, silent=True)
    await world.plays_on(SOUND_TIMEOUT_S - 1)
    assert world.buffering() and world.restarts == [] and world.errors() == []

    await world.plays_on(2)

    assert world.session_ends() == ["load_failed"]
    assert world.errors() == [SourceErrorReason.PLAYBACK_FAILED]
    assert len(world.restarts) == 1
    assert not world.active()


async def test_a_silent_track_after_one_that_sounded_is_a_lost_stream(world):
    """The session has been heard: past the wait, the end is a stream lost."""
    await world.mac_plays(STILL_DRE)
    await world.plays_on(3)
    await world.mac_skips_to(KEEP_IT_THORO, silent=True)
    assert world.buffering()

    await world.plays_on(SOUND_TIMEOUT_S + 1)

    assert world.session_ends() == ["stream_lost"]
    assert world.errors() == [SourceErrorReason.STREAM_DISCONNECTED]


async def test_a_skip_gates_the_next_track_again(world):
    """The previous track's sound says nothing of the next one's."""
    await world.mac_plays(STILL_DRE)
    await world.plays_on(3)

    await world.mac_skips_to(KEEP_IT_THORO, silent=True)
    assert world.buffering() and world.session()["title"] == KEEP_IT_THORO

    await world.advance(SOUND_AFTER_S)
    await world.sends(AUDIO_FORMAT)
    assert world.playing() and world.position_ms() == 0


async def test_a_skip_from_a_silent_track_gives_the_next_one_its_own_wait(world):
    """A silent track left after a few seconds: the next one, loading
    normally, must not inherit what was left of the previous one's wait —
    the skip's first burst ends BUFFERING, which withdraws it."""
    await world.mac_plays(STILL_DRE, silent=True)
    await world.plays_on(SOUND_TIMEOUT_S - 2)

    await world.mac_skips_to(KEEP_IT_THORO, silent=True)
    await world.plays_on(SOUND_TIMEOUT_S - 2)
    assert world.buffering() and world.restarts == []
    await world.advance(SOUND_AFTER_S)
    await world.sends(AUDIO_FORMAT)

    assert world.playing() and world.session()["title"] == KEEP_IT_THORO
    assert world.errors() == []


async def test_a_pause_of_a_track_not_sounding_yet_shows_paused(world):
    await world.mac_plays(STILL_DRE, silent=True)
    await world.plays_on(3)

    await world.mac_pauses()

    assert world.phase() == "paused" and world.position_ms() == 0
    assert world.state()["controls"] == ["resume", "next", "prev"]


async def test_a_resume_of_a_silent_track_is_a_load_again(world):
    """A silent track's resume is BUFFERING and PLAYING at 0 in one burst,
    and still no sound (measured). Held as a resume, it was drawn playing
    over silence; it is a load, and its PLAYING waits for sound anew. The
    pause in between is the listener's and fails nothing, however long."""
    await world.mac_plays(STILL_DRE, silent=True)
    await world.plays_on(SOUND_TIMEOUT_S - 2)
    await world.mac_pauses()
    await world.advance(SOUND_TIMEOUT_S * 3)
    assert world.phase() == "paused" and world.restarts == []

    await world.mac_resumes()

    assert world.buffering()
    await world.plays_on(SOUND_TIMEOUT_S - 1)
    assert world.buffering() and world.restarts == []
    await world.plays_on(2)
    assert world.session_ends() == ["load_failed"]


# === The playhead, from the daemon's first readings ===

async def test_past_the_start_a_small_drift_is_still_not_published(world):
    """The readings are believed closely only right after a discontinuity,
    and the ones believed do not prolong that: later, the daemon's
    twice-a-second status moves nothing short of POSITION_TOLERANCE_MS, or
    every jitter would go out as a position."""
    await world.mac_plays(STILL_DRE)            # sound began TICK_S ago
    await world.advance(TICK_S)
    world.daemon.progress += 500 + 600
    await world.sends(status("PLAYING", world.daemon.progress))
    corrected = len(world.positions())
    assert corrected == 1
    await world.plays_on(audio_source.SETTLE_WINDOW_S - 1.5)
    await world.advance(0.75)                   # past the window, inside one the belief would reopen

    await world.sends(status("PLAYING", world.daemon.progress + 750 + 600))

    assert len(world.positions()) == corrected


# === A resume ===

async def test_a_resume_from_the_phone_shows_no_spinner(world):
    """Measured 2026-11-05: every resume published LOADING for 2 ms, then
    PLAYING — the daemon's BUFFERING and PLAYING are two frames read apart, and
    the first redrew the player as loading for one frame."""
    await world.mac_plays(STILL_DRE)
    await world.plays_on(3)
    await world.mac_pauses()
    since = len(world.published())

    await world.mac_resumes()

    assert world.playing()
    assert [s["session"]["phase"] for s in world.published()[since:]] == ["playing"]


async def test_a_resume_from_milo_shows_no_spinner(world):
    await world.mac_plays(STILL_DRE)
    await world.plays_on(3)
    await world.command("pause")
    since = len(world.published())

    await world.command("resume")
    await world.advance(0.01)

    assert world.playing()
    assert [s["session"]["phase"] for s in world.published()[since:]] == ["playing"]


async def test_a_resume_that_keeps_buffering_shows_the_spinner(world):
    """The held BUFFERING is a moment, not a verdict: a resume the network
    keeps waiting is drawn loading, and its loading budget runs."""
    await world.mac_plays(STILL_DRE)
    await world.plays_on(3)
    await world.mac_pauses()
    await world.sends(status("BUFFERING", world.daemon.progress))
    assert world.phase() == "paused"

    await buffers_for(world, 2)
    assert world.buffering()

    await buffers_for(world, 30)
    assert len(world.restarts) == 1
    assert world.session_ends() == ["stream_lost"]


async def test_a_track_change_during_a_held_resume_shows_the_spinner(world):
    """The hold is for the paused track only: another one named while it
    runs is a load, drawn as one."""
    await world.mac_plays(STILL_DRE)
    await world.plays_on(3)
    await world.mac_pauses()
    await world.sends(status("BUFFERING", world.daemon.progress))
    assert world.phase() == "paused"

    await world.sends(media(KEEP_IT_THORO), status("BUFFERING", 0, 224000))

    assert world.buffering() and world.session()["title"] == KEEP_IT_THORO


async def test_a_skip_whose_paused_frame_is_read_apart_shows_the_spinner(world):
    """A load passes through PAUSED → BUFFERING too, and frames milliseconds
    apart are read apart: the hold is for a track that has played, or a skip
    draws a play button for a second in the middle of its load."""
    await world.mac_plays(STILL_DRE)
    await world.plays_on(3)
    await world.sends(status("IDLE"), status("IDLE"), media(KEEP_IT_THORO), status("BUFFERING", 0, 224000))
    await world.advance(0.13)
    await world.sends(media(KEEP_IT_THORO, custom=True), status("PAUSED", 0, 224000))
    await world.advance(0.005)
    await world.sends(status("BUFFERING", 0, 224000), status("BUFFERING", 0, 224000))
    await world.advance(0.9)

    assert world.buffering()


async def test_a_start_whose_paused_frame_is_read_apart_shows_the_spinner(world):
    await world.mac_picks_the_speaker()
    await world.advance(0.4)
    await world.sends(media(STILL_DRE), status("PAUSED", 0, 271000), status("BUFFERING", 0, 271000))
    await world.advance(0.35)
    await world.sends(media(STILL_DRE, custom=True), status("PAUSED", 0, 271000))
    await world.advance(0.005)
    await world.sends(status("BUFFERING", 0, 271000), status("BUFFERING", 0, 271000))
    await world.advance(0.5)

    assert world.buffering()
