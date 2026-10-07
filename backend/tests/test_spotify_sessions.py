"""Spotify sessions, driven through the go-librespot the unit was measured to
run (tests/spotify_world.py; docs: source architecture, phase 3b).

What is asserted is what the wire says, and what Milō asked of the daemon,
after the phone or the daemon did something. Each gap test was seen red on
the code before phase 3b for the reason its docstring gives.
"""
import pytest

from backend.tests.golden.harness import settle

from backend.core.models.ws_events import SourceErrorReason
from backend.tests.spotify_world import ACCOUNT, LE_CHEMIN, PARAPLUIE, TROIS_NEUF_TROIS, SpotifyWorld, track

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


async def test_every_artist_of_the_track_is_published_with_its_uri(world):
    """The player draws each name of the artist line as a link to its own page
    (SpotifySource.vue): a track by two artists must name both, in the line's
    order, each with its own uri — not the first one's."""
    duet = {**track("Breathe", artists=("Télépopmusik", "Angela McCluskey")),
            "artist_uris": ["spotify:artist:tele", "spotify:artist:angela"]}
    await world.phone_plays(duet)
    assert world.session()["artist"] == "Télépopmusik, Angela McCluskey"
    assert world.state()["details"]["artists"] == [
        {"name": "Télépopmusik", "uri": "spotify:artist:tele"},
        {"name": "Angela McCluskey", "uri": "spotify:artist:angela"},
    ]


def _listed(song, known=True, uid=None, provider="context"):
    """An entry of go-librespot's GET /player/queue, its track null until
    cached (an album's tracks carry no uid, measured)."""
    return {"uri": song["uri"], "uid": uid, "provider": provider, "track": song if known else None}


async def test_the_play_order_around_the_track_is_published(world):
    """The phone's mini-bar slides the neighbour titles in under the finger
    (AudioPlayer.vue's carousel reads details.queue / queue_index): the order
    is go-librespot's window around the track, the one playing in it."""
    d = world.daemon
    d.prev_tracks = [_listed(LE_CHEMIN)]
    d.next_tracks = [_listed(TROIS_NEUF_TROIS), _listed(track("Pas encore lu"), known=False)]
    await world.phone_plays(PARAPLUIE)
    details = world.state()["details"]
    assert details["queue"] == [
        {"uri": LE_CHEMIN["uri"], "title": "Le Chemin", "artist": "Kery James"},
        {"uri": PARAPLUIE["uri"], "title": "Parapluie", "artist": "Kery James"},
        {"uri": TROIS_NEUF_TROIS["uri"], "title": "Trois Neuf Trois", "artist": "Kery James"},
        {"uri": "spotify:track:pas-encore-lu", "title": None, "artist": None},
    ]
    assert details["queue_index"] == 1


async def test_a_queue_event_alone_names_what_was_not_known(world):
    """go-librespot caches the window's metadata about a second after the
    track loads and says so with `queue` only: that read must reach the
    wire, or the next title stays blank until the track changes."""
    d = world.daemon
    d.next_tracks = [_listed(TROIS_NEUF_TROIS, known=False)]
    await world.phone_plays(PARAPLUIE)
    assert world.state()["details"]["queue"][1]["title"] is None
    d.next_tracks = [_listed(TROIS_NEUF_TROIS)]
    await world._says({"type": "queue"})
    assert world.state()["details"]["queue"][1]["title"] == "Trois Neuf Trois"


async def test_no_window_is_no_order_to_swipe_through(world):
    """A daemon that lists nothing around the track publishes no queue: a
    queue of the track alone would leave the carousel no neighbour, and the
    swipe nothing to do."""
    await world.phone_plays(PARAPLUIE)
    details = world.state()["details"]
    assert details["queue"] == [] and details["queue_index"] is None


async def test_a_daemon_without_the_queue_route_lists_no_order(world):
    """Stock go-librespot 0.10.3 (what dependencies.env pins) has no GET
    /player/queue and answers 404: the bar keeps its single cell rather
    than an order the daemon never gave."""
    d = world.daemon
    d.has_queue_route = False
    d.next_tracks = [_listed(TROIS_NEUF_TROIS)]
    await world.phone_plays(PARAPLUIE)
    details = world.state()["details"]
    assert details["queue"] == [] and details["queue_index"] is None
    assert world.playing()


async def test_the_play_order_is_read_again_when_events_reconnect(world):
    """go-librespot raises `queue` only on change, so one that went by while
    /events was down is never said again: the reconnection reads the order."""
    d = world.daemon
    await world.phone_plays(PARAPLUIE)
    d.next_tracks = [_listed(TROIS_NEUF_TROIS)]   # moved while nobody listened
    await world.events_blip()
    assert world.state()["details"]["queue"][1]["title"] == "Trois Neuf Trois"


async def test_a_queue_event_read_with_an_unreadable_status_is_not_lost(world):
    """The `queue` event is said once: when /status fails in its burst, the
    order it announced still reaches the wire with the retry's read."""
    d = world.daemon
    await world.phone_plays(PARAPLUIE)
    d.status_answers = False
    d.next_tracks = [_listed(TROIS_NEUF_TROIS)]
    await world._says({"type": "queue"})
    d.status_answers = True
    await world.advance(2.1)                  # the status retry
    assert world.state()["details"]["queue"][1]["title"] == "Trois Neuf Trois"


async def test_an_unreadable_play_order_keeps_the_last_one(world):
    """E10 for the order: a read that fails learns nothing, so the carousel
    keeps the neighbours it had rather than collapsing to one cell."""
    d = world.daemon
    d.next_tracks = [_listed(TROIS_NEUF_TROIS)]
    await world.phone_plays(PARAPLUIE)
    d.queue_answers = False
    d.next_tracks = [_listed(LE_CHEMIN)]
    await world._says({"type": "volume", "value": 40, "max": 100})
    assert world.state()["details"]["queue"][1]["title"] == "Trois Neuf Trois"
    assert world.playing()


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


async def test_a_refused_login_banner_leaves_with_the_source(world):
    """The refusal belongs to the daemon the source stops: left standing, it
    would sit over the next source, and the next Spotify start would hold its
    first refusal back as a retry of the old one."""
    refused = ('level=error msg="failed creating new session from Mac mini" error="failed '
               'authenticating with login5: TRY_AGAIN_LATER"')
    from backend.tests.golden.harness import settle

    await world.source._handle_log_line(refused)
    await settle()
    assert world.errors() == [SourceErrorReason.PROVIDER_UNAVAILABLE]

    await world.leave()

    assert world.envelopes("source", "error_cleared")


# === Another device of the account ===

@pytest.fixture
async def signed_in(monkeypatch, tmp_path):
    """A daemon signed in with stored credentials and no session here: the
    one state in which go-librespot can see another device play."""
    w = SpotifyWorld(monkeypatch, tmp_path, stored=ACCOUNT)
    await w.select()
    yield w
    await w.source.shutdown()


def remote(world):
    return world.state()["details"]["remote"]


async def test_another_device_playing_is_shown_without_a_session(signed_in):
    """What plays on the phone is drawn by the Spotify bar, never as a session:
    nothing plays here, and the widget and the Mac read `session` as Milō's."""
    await signed_in.plays_elsewhere(PARAPLUIE, at_ms=12_000)

    assert not signed_in.active()
    assert remote(signed_in)["device_name"] == "iPhone"
    assert remote(signed_in)["device_type"] == "smartphone"
    assert remote(signed_in)["title"] == "Parapluie"
    assert remote(signed_in)["position"]["ms"] == 12_000
    assert signed_in.state()["controls"] == ["take_over"]


async def test_a_remote_track_not_yet_named_is_not_shown(signed_in):
    """go-librespot names the remote track once its metadata is resolved: a
    bar with no title has nothing to draw, and nothing to take over yet."""
    await signed_in.plays_elsewhere(None)

    assert remote(signed_in) is None
    assert signed_in.state()["controls"] == []


async def test_take_over_asks_once_and_the_session_arrives_like_a_transfer(signed_in):
    """take_over is one POST /player/transfer; the session is the daemon's,
    reconciled from what it says, at the position the phone was at."""
    await signed_in.plays_elsewhere(PARAPLUIE, at_ms=81_264)

    result = await signed_in.command("take_over")
    await signed_in.transfer_lands()

    assert result["success"] is True
    assert signed_in.transfers_sent() == 1
    assert signed_in.playing()
    assert signed_in.session()["title"] == "Parapluie"
    assert signed_in.position_ms() == 81_264
    assert remote(signed_in) is None


async def test_the_bar_stays_up_while_the_session_it_takes_over_arrives(signed_in):
    """Between the transfer and its session the daemon is active with no track
    and names no remote: published as such, the bar would leave and come back.
    The remote is held until the session takes its place."""
    await signed_in.plays_elsewhere(PARAPLUIE, at_ms=81_264)
    sent = len(signed_in.published())

    await signed_in.command("take_over")
    await signed_in.transfer_lands()

    shown = [(s["session"] is not None, s["details"]["remote"] is not None) for s in signed_in.published()[sent:]]
    assert (False, False) not in shown
    assert shown[-1] == (True, False)


async def test_the_bar_moves_to_the_phone_that_takes_the_session_back(signed_in):
    """go-librespot hands a session taken by a phone back to Spotify and signs
    in again, answering 204 meanwhile: the bar goes straight to the phone, on
    what the daemon announced, rather than leaving and coming back."""
    await signed_in.phone_plays(PARAPLUIE)
    sent = len(signed_in.published())

    await signed_in.phone_takes_it_back()

    shown = [(s["session"] is not None, s["details"]["remote"] is not None) for s in signed_in.published()[sent:]]
    assert (False, False) not in shown
    assert remote(signed_in)["device_name"] == "iPhone"
    assert remote(signed_in)["position"]["ms"] == 90_000


async def test_an_announced_phone_that_never_shows_up_is_dropped(signed_in):
    """The announcement only bridges until /status names the phone: if it
    never does, the bar follows /status once the announcement has aged."""
    await signed_in.phone_plays(PARAPLUIE)
    d = signed_in.daemon
    d.session, d.track, d.signed_in = False, None, True
    await signed_in._says({"type": "inactive"}, {"type": "remote", "data": {
        "device_id": "x", "device_name": "iPhone", "device_type": "SMARTPHONE", "paused": False,
        "track": dict(PARAPLUIE)}})
    assert remote(signed_in)["device_name"] == "iPhone"

    await signed_in.advance(signed_in.source.HANDOVER_HOLD + 1)

    assert remote(signed_in) is None


async def test_an_old_announcement_does_not_come_back_on_a_later_end(signed_in):
    """Only an announcement newer than /status's last answer stands in for
    it: a phone announced long ago is not shown when Milō's own session ends."""
    await signed_in.plays_elsewhere(PARAPLUIE)
    await signed_in.command("take_over")
    await signed_in.transfer_lands()
    signed_in.daemon.signed_in = False   # the 204 of the sign-in after the end
    signed_in.daemon._ends()
    await signed_in.advance(0.1)

    assert remote(signed_in) is None


async def test_the_bar_stays_up_when_the_phone_picks_milo(signed_in):
    """The same arrival started from the phone's Spotify app: no take-over
    was asked, and the daemon is still active with no track for a moment."""
    await signed_in.plays_elsewhere(PARAPLUIE, at_ms=81_264)
    sent = len(signed_in.published())

    signed_in.daemon._transfer_starts()
    await signed_in.transfer_lands()

    shown = [(s["session"] is not None, s["details"]["remote"] is not None) for s in signed_in.published()[sent:]]
    assert (False, False) not in shown
    assert signed_in.playing()
    assert remote(signed_in) is None


async def test_a_take_over_that_never_lands_lets_the_bar_follow_status(signed_in):
    """A transfer Spotify accepted and never delivered: past the hold, the
    wire says what /status says — here, nothing plays anywhere."""
    await signed_in.plays_elsewhere(PARAPLUIE)
    await signed_in.command("take_over")
    signed_in.daemon.session = False

    await signed_in.advance(signed_in.source.TAKE_OVER_HOLD + 1)

    assert remote(signed_in) is None
    assert signed_in.state()["controls"] == []


async def test_the_phone_moving_on_during_a_take_over_is_shown(signed_in):
    """A hold keeps the bar up while /status names nothing; what /status does
    name is taken at once — here the phone skipped while Milō waited for a
    transfer that never came, and the bar shows the new track, not a frozen copy."""
    await signed_in.plays_elsewhere(PARAPLUIE)
    signed_in.daemon.session = True   # the transfer is "in flight": nothing lands
    await signed_in.command("take_over")
    signed_in.daemon.session = False

    await signed_in.plays_elsewhere(TROIS_NEUF_TROIS)

    assert remote(signed_in)["title"] == "Trois Neuf Trois"


async def test_an_arrival_that_never_loads_does_not_hold_the_bar_for_ever(signed_in):
    """A session arriving holds the remote while its track loads — for a
    bounded time: a daemon left buffering with no track shows nothing after."""
    await signed_in.plays_elsewhere(PARAPLUIE)
    signed_in.daemon._transfer_starts()
    await settle()
    assert remote(signed_in) is not None

    await signed_in.advance(signed_in.source.ARRIVAL_HOLD + 1)

    assert remote(signed_in) is None


async def test_a_refused_take_over_does_not_hold_the_bar(signed_in):
    """Spotify refused the transfer: nothing is coming, so nothing is held —
    the next read that names no phone takes the bar down at once."""
    await signed_in.plays_elsewhere(PARAPLUIE)
    signed_in.daemon.transfer_refused = True

    result = await signed_in.command("take_over")
    await signed_in.nothing_plays_elsewhere()

    assert result["success"] is False
    assert remote(signed_in) is None


async def test_what_played_elsewhere_does_not_outlive_the_daemon_run(monkeypatch, tmp_path):
    """Leaving Spotify stops go-librespot: coming back, the bar shows nothing
    of a phone until the new daemon names one — no stale take_over to press."""
    w = SpotifyWorld(monkeypatch, tmp_path, stored=ACCOUNT)
    await w.select()
    await w.plays_elsewhere(PARAPLUIE)
    await w.leave()
    w.daemon.remote = None
    sent = len(w.published())

    await w.select()

    assert all(s["details"] is None or s["details"].get("remote") is None for s in w.published()[sent:])
    assert all("take_over" not in s["controls"] for s in w.published()[sent:])
    await w.source.shutdown()


async def test_take_over_with_nothing_elsewhere_asks_nothing(signed_in):
    result = await signed_in.command("take_over")

    assert result["success"] is False
    assert signed_in.transfers_sent() == 0


async def test_a_state_request_rereading_the_same_remote_publishes_nothing(signed_in):
    """Milo-iOS polls the state: a read of the same remote playback, its
    playhead where the anchor says, must not re-stamp the anchor."""
    await signed_in.plays_elsewhere(PARAPLUIE, at_ms=12_000)
    sent = len(signed_in.published())
    await signed_in.advance(30)
    signed_in.daemon.remote["track"]["position"] = 42_000

    await signed_in.get_state()

    assert len(signed_in.published()) == sent


async def test_the_other_device_leaving_takes_the_remote_away(signed_in):
    """A phone gone to the background leaves the cluster within seconds: the
    bar goes, and with it the button."""
    await signed_in.plays_elsewhere(PARAPLUIE)
    await signed_in.nothing_plays_elsewhere()

    assert remote(signed_in) is None
    assert signed_in.state()["controls"] == []


async def test_a_paused_remote_is_still_shown(signed_in):
    await signed_in.plays_elsewhere(PARAPLUIE, at_ms=5_000, paused=True)

    assert remote(signed_in)["paused"] is True
    assert signed_in.state()["controls"] == ["take_over"]
