"""Tidal sessions, driven through the tisoc daemon the unit was measured to run
(tests/tidal_world.py; docs: source architecture, phase 3b).

What is asserted is what the wire says, and what Milō did to the daemon, after
the sender or the daemon did something. Each gap test was seen red on the code
before phase 3b for the reason its docstring gives.
"""
import pytest

from backend.core.models.ws_events import SourceErrorReason
from backend.tests.tidal_world import HYPNOTIZE, KEEP_IT_THORO, STILL_DRE, TidalWorld, status

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
    await world.mac_picks_the_speaker()
    await world.advance(0.4)
    await world.sends({"command": "notifyMediaChanged", "mediaInfo": {"metadata": {
        "title": STILL_DRE, "artists": ["Dr. Dre"], "albumTitle": "2001", "duration": 271000}}},
        status("BUFFERING", 0, 271000))
    assert world.buffering() and world.session()["title"] == STILL_DRE
    await world.advance(0.8)
    await world.sends(status("PLAYING", 0))
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
    is granted. A grant that cannot be written (the daemon stopped reading its
    socket) left Tidal active and silent with an ERROR in the journal only —
    `source.*` loggers never reach the banner."""
    world.daemon.stops_reading = True

    await world.mac_picks_the_speaker()

    assert world.errors() == [SourceErrorReason.PLAYBACK_FAILED]


async def test_a_granted_audio_device_raises_no_banner(world):
    await world.mac_picks_the_speaker()

    assert world.errors() == []


async def test_an_ungranted_daemon_that_says_playing_keeps_its_banner(world):
    """Review of E27: a PLAYING the daemon reports after a grant it never got
    is not sound — the banner a playing track clears (E16) stays until a grant
    goes through."""
    world.daemon.stops_reading = True

    await world.mac_plays()

    assert world.errors() == [SourceErrorReason.PLAYBACK_FAILED]
    assert not world.envelopes("source", "error_cleared")


async def test_a_grant_that_goes_through_later_withdraws_the_banner(world):
    """Review of E27: the banner of an ungranted device outlived its cause
    when the next request was granted but no track played afterwards."""
    world.daemon.stops_reading = True
    await world.mac_picks_the_speaker()
    assert world.errors() == [SourceErrorReason.PLAYBACK_FAILED]

    world.daemon.stops_reading = False
    await world.sends({"command": "requestResources"})

    assert world.envelopes("source", "error_cleared")
