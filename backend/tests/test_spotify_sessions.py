"""Spotify sessions, driven through the go-librespot the unit was measured to
run (tests/spotify_world.py; docs: source architecture, phase 3b).

What is asserted is what the wire says, and what Milō asked of the daemon,
after the phone or the daemon did something. Each gap test was seen red on
the code before phase 3b for the reason its docstring gives.
"""
import pytest

from backend.core.models.ws_events import SourceErrorReason
from backend.tests.spotify_world import LE_CHEMIN, PARAPLUIE, TROIS_NEUF_TROIS, SpotifyWorld

DELAY = 120   # make_settings' audio.auto_stop_delay


@pytest.fixture
async def world(monkeypatch, tmp_path):
    w = SpotifyWorld(monkeypatch, tmp_path)
    await w.select()
    yield w
    await w.source.shutdown()


# === A session opens at its first track, and follows what the daemon says ===

async def test_a_transfer_shows_nothing_until_the_track_is_known(world):
    """E14: a session opens at its first displayable track. go-librespot says
    `active` and `will_play` before it knows the track; nothing is drawn yet."""
    d = world.daemon
    d.session, d.account, d.track, d.paused, d.buffering = True, "account", None, True, True
    await world._says({"type": "active"}, {"type": "will_play", "uri": PARAPLUIE["uri"]})
    assert not world.active()


async def test_a_transfer_lands_paused_then_plays(world):
    """Measured: a transfer loads the track paused at the phone's position and
    plays 1.6 s later. What the daemon says is what the screen shows."""
    await world.phone_transfers(PARAPLUIE, at_ms=81264)
    assert world.playing()
    assert world.session()["title"] == "Parapluie"
    assert world.position_ms() == 81264


async def test_a_playing_track_moves_on_the_wire_without_a_broadcast(world):
    """The playhead is an anchor every client ages on its own clock: a track
    that simply plays sends nothing (no tick, no drift correction), and still
    reads where it is."""
    await world.phone_transfers(PARAPLUIE, at_ms=81264)
    sent = len(world.recorder.envelopes)
    await world.advance(30)
    assert world.recorder.envelopes[sent:] == []
    assert world.position_ms() == 81264 + 30_000


async def test_a_skip_is_loading_until_the_next_track_plays(world):
    await world.phone_plays(PARAPLUIE)
    d = world.daemon
    d.track, d.buffering = None, True
    await world._says({"type": "will_play", "uri": TROIS_NEUF_TROIS["uri"]})
    assert world.buffering() and not world.playing()
    await world.advance(0.07)
    d.track, d.buffering = dict(TROIS_NEUF_TROIS), False
    await world._says({"type": "metadata"}, {"type": "playing"})
    assert world.playing() and not world.buffering()
    assert world.session()["title"] == "Trois Neuf Trois"


async def test_a_seek_moves_the_published_position(world):
    """A seek done on the phone is a discontinuity: it goes out at once, as a
    position alone (nothing else about the session moved)."""
    await world.phone_plays(PARAPLUIE)
    states = len(world.published())
    await world.phone_seeks(151280)
    assert world.position_ms() == 151280
    assert [p["position"]["ms"] for p in world.positions()] == [151280]
    assert len(world.published()) == states


async def test_a_track_that_did_not_announce_its_start_is_not_left_spinning(world):
    """E12: the spinner was an override set by `metadata` that only a later
    `playing`/`paused` cleared, so a track change with no such event behind it
    spun for the rest of the track. The daemon's own status says it plays."""
    await world.phone_plays(PARAPLUIE)
    d = world.daemon
    d.track, d.buffering, d.paused = dict(LE_CHEMIN), False, False
    await world._says({"type": "will_play"}, {"type": "metadata"})
    assert world.playing() and not world.buffering()


# === One writer for the phase: the timer and the screen agree ===

async def test_a_stale_pause_event_arms_no_auto_stop_under_a_playing_track(world):
    """E11: the event armed the auto-stop while /status, read right after,
    published "playing" — two writers. A pause the daemon no longer reports
    must not end a session that plays."""
    await world.phone_plays(PARAPLUIE)
    await world._says({"type": "paused"})     # the phone already resumed
    assert world.playing()
    await world.advance(DELAY + 1)
    assert world.stops_sent() == 0
    assert world.playing()


async def test_a_pause_asks_the_daemon_to_end_the_session_once(world):
    """REQUEST_END: after the delay Milō asks go-librespot to drop the session
    (POST /player/stop) and follows the `inactive` that comes back; no banner,
    no restart, and nothing asked twice."""
    await world.phone_plays(PARAPLUIE)
    await world.phone_pauses()
    assert world.active() and not world.playing()
    await world.advance(DELAY + 1)
    assert world.stops_sent() == 1
    assert not world.active()
    assert world.errors() == []
    assert world.restarts == []
    await world.advance(DELAY + 1)
    assert world.stops_sent() == 1


async def test_a_phone_that_leaves_leaves_no_auto_stop_behind(world):
    """E69 (measured): `stopped` follows `inactive` and armed the pause timer
    on a source with no session left, which then posted /player/stop on nothing. (A
    next session's first `paused`/`playing` replaced that timer, so it could
    not cut the next session.)"""
    await world.phone_plays(PARAPLUIE)
    await world.phone_leaves()
    assert not world.active()
    await world.advance(DELAY + 1)
    assert world.stops_sent() == 0


async def test_a_resume_withdraws_the_end_request(world):
    await world.phone_plays(PARAPLUIE)
    await world.phone_pauses()
    await world.advance(DELAY - 1)
    await world.phone_resumes()
    await world.advance(DELAY)
    assert world.stops_sent() == 0
    assert world.playing()


# === The daemon ===

async def test_a_daemon_killed_mid_play_ends_the_session_at_once(world):
    """E17 (Spotify, measured: 6 s of "playing" with no banner): the session
    belongs to go-librespot's process; its death ends it, reported once."""
    await world.phone_plays(PARAPLUIE)
    await world.kill_daemon()
    assert not world.active()
    assert world.errors() == [SourceErrorReason.STREAM_DISCONNECTED]
    await world.systemd_restarts_it()
    assert not world.active()
    assert world.errors() == [SourceErrorReason.STREAM_DISCONNECTED]


async def test_an_unreadable_status_on_reconnect_keeps_the_session(world):
    """E10: when /events came back and /status could not be read, the source
    published "no session" over music still playing. A failed read learns nothing: the
    session stays as it was, and the read is tried again."""
    await world.phone_plays(PARAPLUIE)
    world.daemon.status_answers = False
    await world.events_blip()                 # /events reconnects, /status fails
    assert world.playing()
    world.daemon.status_answers = True
    await world.advance(2.1)                  # the retry reads it
    assert world.playing()
    assert world.session()["title"] == "Parapluie"


async def test_a_state_request_with_an_unreadable_status_changes_nothing(world):
    await world.phone_plays(PARAPLUIE)
    world.daemon.status_answers = False
    state = await world.get_state()
    assert state["session"]["phase"] == "playing"
    assert state["session"]["title"] == "Parapluie"


async def test_a_settings_restart_ends_the_session_without_a_banner(world):
    """The crossfade restart is Milō's own: the session it ends is not a death."""
    await world.phone_plays(PARAPLUIE)
    assert await world.source.on_spotify_settings_changed(apply_now=True)
    await world.advance(2.1)
    assert world.restarts
    assert not world.active()
    assert world.errors() == []


# === Multiroom ===

async def test_a_reroute_keeps_the_session_and_publishes_no_pause(world):
    """E09 (measured both ways): the reroute's own pause published "active,
    paused" 7 ms after STARTING and armed the auto-stop."""
    await world.phone_plays(PARAPLUIE)
    before = len(world.published())
    await world.reroute()
    during = world.published()[before:]
    assert all(p["session"]["phase"] != "paused" for p in during if p["session"]), during
    assert world.playing()
    assert world.daemon.output.startswith("milo_spotify_")
    await world.advance(DELAY + 1)
    assert world.stops_sent() == 0


async def test_a_reroute_whose_reopen_fails_does_not_leave_the_output_on_null(world):
    """E08: when the output could not be reopened, the fallback was a start —
    a no-op on a unit already running — and go-librespot went on writing to
    `null`: every later session played into nothing."""
    await world.phone_plays(PARAPLUIE)
    world.daemon.refused_outputs = {"milo_spotify_direct", "milo_spotify_multiroom"}
    await world.reroute()
    assert world.daemon.output != "null"
    assert world.errors() == []


async def test_a_reroute_whose_output_switch_fails_reopens_the_output(world):
    """E08: snapcast refusing to move raised out of the reroute between the
    release and the reacquire, and the parked output was never reopened."""
    await world.phone_plays(PARAPLUIE)
    await world.reroute(fails=True)
    assert world.daemon.output != "null"
    assert world.playing()


async def test_two_reroutes_in_a_row_both_keep_the_session(world):
    """Each reroute parks the output and reopens it; the second must not
    find anything left over from the first (it used to read a flag)."""
    await world.phone_plays(PARAPLUIE)
    await world.reroute()
    await world.reroute()
    parks = [body for command, body in world.daemon.posted if command == "output"]
    assert [p["device"] == "null" for p in parks] == [True, False, True, False]
    assert world.playing()
    assert world.restarts == [] and world.systemd.stop.await_count == 0


async def test_a_backend_restart_under_the_session_is_not_a_death(world):
    """systemd stops go-librespot before the backend (BindsTo + After=), while
    the backend still runs: a stop that was asked for, not a crash — measured,
    the unit is `inactive` when the process is gone, `activating` after a kill."""
    from backend.tests.golden.harness import settle
    await world.phone_plays(PARAPLUIE)
    await world._unit_stop()
    await settle()
    assert not world.active()
    assert world.errors() == []


async def test_another_account_taking_over_shows_nothing_until_its_track(world):
    """E14's rule holds on a takeover too: a session of another account opens at
    its first named track — not on the untitled status that precedes it."""
    await world.phone_plays(PARAPLUIE)
    d = world.daemon
    d.account, d.track, d.paused, d.buffering = "someone-else", None, False, True
    await world._says({"type": "active"}, {"type": "will_play", "uri": LE_CHEMIN["uri"]})
    assert not (world.active() and world.session()["title"] is None)
    await world.advance(0.07)
    d.track, d.buffering = dict(LE_CHEMIN), False
    await world._says({"type": "metadata"}, {"type": "playing"})
    assert world.playing() and world.session()["title"] == "Le Chemin"


async def test_a_reroute_whose_resume_lags_publishes_no_pause(world):
    """E09 after the reacquire: go-librespot's /status lags its commands (the
    reason the release confirms its pause), so a /status read right after the
    resume could still say paused — and publish it, arming the auto-stop."""
    await world.phone_plays(PARAPLUIE)
    world.daemon.resume_lag = True
    before = len(world.published())
    await world.reroute()
    during = world.published()[before:]
    assert all(p["session"]["phase"] != "paused" for p in during if p["session"]), during
    assert world.playing()


async def test_a_daemon_that_never_answers_is_on_screen_until_its_events_connect(
    monkeypatch, tmp_path
):
    """E27: go-librespot never answering at start was an ERROR in the journal
    only (`source.*` loggers never reach the banner), so the phone found no
    Milō and the screen said nothing. It is a banner now, withdrawn as soon as
    the daemon's /events stream connects."""
    from backend.sources.spotify import source as spotify_module
    from backend.tests.test_spotify_source import deaf_daemon_clock

    monkeypatch.setattr(spotify_module, "time", deaf_daemon_clock())
    w = SpotifyWorld(monkeypatch, tmp_path)
    comes_up = w.daemon.comes_up
    w.daemon.comes_up = lambda: None        # the process runs, its API stays deaf
    await w.select()
    assert w.errors() == [SourceErrorReason.SERVICE_UNREACHABLE]

    comes_up()
    await w.advance(2.1)                    # the /events client retries every 2 s

    assert w.envelopes("source", "error_cleared")
    await w.source.shutdown()


async def test_a_daemon_that_answers_raises_no_banner(world):
    assert world.errors() == []


async def test_the_unanswered_banner_leaves_with_the_source(monkeypatch, tmp_path):
    """Review of E27: the banner a deaf daemon raised stayed over the next
    source once Spotify was left, the daemon still silent."""
    from backend.sources.spotify import source as spotify_module
    from backend.tests.test_spotify_source import deaf_daemon_clock

    monkeypatch.setattr(spotify_module, "time", deaf_daemon_clock())
    w = SpotifyWorld(monkeypatch, tmp_path)
    w.daemon.comes_up = lambda: None        # the process runs, its API stays deaf
    await w.select()
    assert w.errors() == [SourceErrorReason.SERVICE_UNREACHABLE]

    await w.leave()

    assert w.envelopes("source", "error_cleared")
    await w.source.shutdown()


async def test_the_daemon_answering_late_leaves_a_newer_banner_standing(monkeypatch, tmp_path):
    """Review of E27: /events connecting late withdrew whatever banner stood,
    a track that failed to load meanwhile included."""
    from backend.sources.spotify import source as spotify_module
    from backend.tests.test_spotify_source import deaf_daemon_clock

    monkeypatch.setattr(spotify_module, "time", deaf_daemon_clock())
    w = SpotifyWorld(monkeypatch, tmp_path)
    comes_up = w.daemon.comes_up
    w.daemon.comes_up = lambda: None        # the process runs, its API stays deaf
    await w.select()
    await w.source._handle_log_line('level=error msg="failed loading current track: no stream"')

    comes_up()
    await w.advance(2.1)                    # the /events client retries every 2 s

    assert w.errors() == [
        SourceErrorReason.SERVICE_UNREACHABLE, SourceErrorReason.TRACK_LOAD_FAILED,
    ]
    assert not w.envelopes("source", "error_cleared")
    await w.source.shutdown()
