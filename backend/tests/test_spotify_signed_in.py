"""Spotify with a stored account: Milō browses and plays with no phone.

Driven through go-librespot 0.10.3 as it was measured with
`persist_credentials` (tests/spotify_world.py, 2026-10-03). What is asserted is
what the wire says (`details.spotify`, the session) and what Milō sent the
daemon — the browser's play button is the only way a session starts without a
phone, so a play sent twice, or sent to nobody, is a bug only a unit shows.
"""
import json

import pytest

from backend.sources.spotify.source import repeat_mode, repeat_posts
from backend.tests.spotify_world import ACCOUNT, SpotifyWorld, track

DELAY = 120   # make_settings' audio.auto_stop_delay
PLAYLIST = "spotify:playlist:3G1Qd5iTuEjDBCgpqciOpv"
TRACK = "spotify:track:emsamo"


@pytest.fixture
async def stored(monkeypatch, tmp_path):
    w = SpotifyWorld(monkeypatch, tmp_path, stored=ACCOUNT)
    await w.select()
    yield w
    await w.source.shutdown()


@pytest.fixture
async def nobody(monkeypatch, tmp_path):
    w = SpotifyWorld(monkeypatch, tmp_path)
    await w.select()
    yield w
    await w.source.shutdown()


def details(world):
    return world.state()["details"]


async def test_a_stored_account_signs_in_with_no_phone(stored):
    """The daemon signs in on its own and says nothing on /events (measured):
    the account must still reach the wire, or the browser shows "cast from
    your phone" to a unit that could play right away."""
    assert details(stored)["account"] == ACCOUNT
    assert details(stored)["signing_in"] is False
    assert stored.session() is None
    assert stored.state()["controls"] == []


async def test_play_context_starts_one_session_on_the_signed_in_account(stored):
    """One press, one play — shuffle turned off first, because go-librespot
    keeps the last context's shuffle within a session."""
    result = await stored.command("play_context", {"uri": PLAYLIST, "skip_to_uri": TRACK})

    assert result["success"] is True
    assert stored.daemon.posted == [
        ("shuffle_context", {"shuffle_context": False}),
        ("play", {"uri": PLAYLIST, "skip_to_uri": TRACK}),
    ]
    assert stored.playing()
    assert details(stored)["context_uri"] == PLAYLIST
    assert details(stored)["track_uri"] == TRACK
    assert details(stored)["shuffle"] is False


async def test_play_context_with_nobody_signed_in_sends_nothing(nobody):
    """A daemon nobody is signed in to answers every command with a 204: the
    press must be refused here, with nothing posted, rather than read as sent."""
    result = await nobody.command("play_context", {"uri": PLAYLIST})

    assert result["success"] is False
    assert nobody.daemon.posted == []
    assert nobody.session() is None


async def test_set_repeat_reaches_the_daemon_and_the_wire(stored):
    """The player's one repeat button drives go-librespot's two flags; the
    mode the wire then shows is the one the daemon announced."""
    await stored.command("play_context", {"uri": PLAYLIST})
    stored.daemon.posted.clear()

    await stored.command("set_repeat", {"mode": "track"})

    assert stored.daemon.posted == repeat_posts("track")
    assert details(stored)["repeat"] == "track"


async def test_shuffle_and_repeat_need_a_session(stored):
    """No session, nothing to shuffle: refused once by the base, never sent."""
    for command, data in (("set_shuffle", {"shuffle": True}), ("set_repeat", {"mode": "context"})):
        result = await stored.command(command, data)
        assert result["success"] is False
    assert stored.daemon.posted == []


@pytest.mark.parametrize("start", [(False, False), (True, False), (True, True), (False, True)])
@pytest.mark.parametrize("mode", ["off", "context", "track"])
def test_repeat_posts_land_on_the_mode_they_name(start, mode):
    """Whatever flags the daemon holds, the posts for a mode leave it in that
    mode — read back the way /status is read."""
    flags = {"repeat_context": start[0], "repeat_track": start[1]}
    for command, body in repeat_posts(mode):
        flags[command] = body[command]
    assert repeat_mode(flags["repeat_context"], flags["repeat_track"]) == mode


async def test_an_idle_end_on_a_session_milo_started_keeps_the_account(stored):
    """After the idle stop the daemon is nobody's for 0.25 s, then signs back
    in (measured). The account must never read as null in between: the
    browser would flash "cast from your phone" and drop its listings."""
    await stored.command("play_context", {"uri": PLAYLIST})
    await stored.command("pause")
    sent = len(stored.published())

    await stored.advance(DELAY + 1)
    await stored.advance(DELAY)

    assert stored.stops_sent() == 1
    assert stored.session() is None
    accounts = [state["details"]["account"] for state in stored.published()[sent:]]
    assert accounts and all(account == ACCOUNT for account in accounts)
    assert details(stored)["signing_in"] is False


async def test_a_signin_that_hangs_restarts_the_daemon_once(monkeypatch, tmp_path):
    """Stored credentials that never sign in (seen once, unreproduced): one
    restart, then the account goes null so the browser stops waiting."""
    w = SpotifyWorld(monkeypatch, tmp_path, stored=ACCOUNT)
    w.daemon.signin_hangs = True
    try:
        await w.select()
        assert details(w)["signing_in"] is True

        await w.advance(w.source.SIGNIN_TIMEOUT + 0.5)
        assert len(w.restarts) == 1
        assert details(w)["account"] == ACCOUNT

        await w.advance(w.source.SIGNIN_TIMEOUT + 0.5)
        assert len(w.restarts) == 1
        assert details(w)["account"] is None
        assert details(w)["signing_in"] is False

        # Given up for good: a later read of the same 204 (a client asking for
        # the state) must not bring the account back into "signing in".
        await w.get_state()
        assert details(w)["account"] is None
        assert details(w)["signing_in"] is False
        await w.advance(w.source.SIGNIN_TIMEOUT + 0.5)
        assert details(w)["account"] is None
        assert details(w)["signing_in"] is False
        assert len(w.restarts) == 1
    finally:
        await w.source.shutdown()


async def test_an_unreadable_status_after_the_restart_keeps_the_stored_account(monkeypatch, tmp_path):
    """A /status that never answers (a daemon stuck reaching Spotify, a loaded
    unit) learned nothing (E10): after the one restart the stored account
    stays named, so the browser keeps its listings instead of "cast from your
    phone", and the daemon is not restarted again."""
    w = SpotifyWorld(monkeypatch, tmp_path, stored=ACCOUNT)
    w.daemon.status_answers = False
    try:
        await w.select()

        await w.advance(w.source.SIGNIN_TIMEOUT + 0.5)
        assert len(w.restarts) == 1

        await w.advance(w.source.SIGNIN_TIMEOUT + 0.5)
        assert details(w)["account"] == ACCOUNT
        assert details(w)["signing_in"] is False
        assert len(w.restarts) == 1

        await w.advance(60)
        assert details(w)["account"] == ACCOUNT
        assert len(w.restarts) == 1
    finally:
        await w.source.shutdown()


async def test_a_late_silent_signin_is_read_without_a_client(monkeypatch, tmp_path):
    """The stored sign-in says nothing on /events (measured): once /status
    answers again after the watch gave up, Milō reads it itself — no client
    GET is there to do it — and stops reading once it has."""
    w = SpotifyWorld(monkeypatch, tmp_path, stored=ACCOUNT)
    w.daemon.status_answers = False
    try:
        await w.select()
        await w.advance(w.source.SIGNIN_TIMEOUT + 0.5)
        await w.advance(w.source.SIGNIN_TIMEOUT + 0.5)
        # A re-read that finds /status still silent reads it again later.
        await w.advance(w.source.SIGNIN_REREAD_S + 0.5)

        # Only a read of /status can carry the daemon's shuffle to the wire.
        w.daemon.shuffle = True
        w.daemon.status_answers = True
        await w.advance(w.source.SIGNIN_REREAD_S + 0.5)
        assert details(w)["shuffle"] is True
        assert details(w)["account"] == ACCOUNT
        assert details(w)["signing_in"] is False

        w.daemon.shuffle = False
        await w.advance(w.source.SIGNIN_REREAD_S * 3)
        assert details(w)["shuffle"] is True
        assert len(w.restarts) == 1
    finally:
        await w.source.shutdown()


# === The resume point: what a session left, played again with no phone ===

@pytest.fixture
async def resuming(monkeypatch, tmp_path):
    """A stored account, with CLOCK_MONOTONIC on the scenario's clock (the
    resume window is timed on it)."""
    from backend.core import audio_source
    w = SpotifyWorld(
        monkeypatch, tmp_path, stored=ACCOUNT, settings={"audio.resume_window": 300},
    )
    monkeypatch.setattr(audio_source, "monotonic", lambda: w.clock.now)
    await w.select()
    yield w
    await w.source.shutdown()


async def _left_after(world, seconds: float):
    """A context played `seconds` into its track, then another source selected
    (go-librespot stopped with it), then Spotify selected back."""
    await world.command("play_context", {"uri": PLAYLIST, "skip_to_uri": TRACK})
    await world.advance(seconds)
    await world.leave()
    await world.select()


async def test_back_on_spotify_the_track_left_is_offered_paused(resuming):
    """go-librespot keeps nothing across its exit: without a resume point the
    bar came back empty after any other source was played."""
    await _left_after(resuming, 30)

    state = resuming.state()
    assert resuming.session() is None
    assert state["resume"]["title"]
    assert state["resume"]["position_ms"] > 0
    assert state["controls"] == ["resume", "seek", "prev", "set_shuffle", "set_repeat"]
    assert details(resuming)["context_uri"] == PLAYLIST
    assert details(resuming)["track_uri"] == TRACK


async def test_resume_plays_the_context_from_its_track_at_its_second(resuming):
    """One press, one play — the context, the track it lists, the second it
    was left at: go-librespot loads it paused and seeks before it plays."""
    await _left_after(resuming, 30)
    left_at = resuming.state()["resume"]["position_ms"]
    resuming.daemon.posted.clear()

    result = await resuming.command("resume")

    assert result["success"] is True
    assert resuming.daemon.posted == [
        ("play", {"uri": PLAYLIST, "position": left_at, "skip_to_uri": TRACK}),
    ]
    assert resuming.playing()


async def test_the_hardware_toggle_resumes_it_too(resuming):
    """The knob and the remotes send `playpause`: with no session it is the
    same press as the bar's play button, not a refusal."""
    await _left_after(resuming, 30)
    resuming.daemon.posted.clear()

    await resuming.command("playpause")

    assert [command for command, _ in resuming.daemon.posted] == ["play"]


BEFORE = "spotify:track:before"
AFTER = "spotify:track:after"
QUEUED = "spotify:track:queued"


def _listed(uri, provider="context"):
    """An entry of go-librespot's GET /player/queue."""
    return {"uri": uri, "uid": None, "provider": provider, "track": {**track("x"), "uri": uri}}


async def _left_between_neighbors(world, seconds: float):
    """As _left_after, the play order listing a context track on either side
    and, next, the user's queue ahead of the context's own."""
    world.daemon.prev_tracks = [_listed(BEFORE)]
    world.daemon.next_tracks = [_listed(QUEUED, provider="queue"), _listed(AFTER)]
    await _left_after(world, seconds)


async def test_the_track_left_keeps_the_players_transport(resuming):
    """The full player drew a lone play button over the track left
    (playerControls draws a button iff its command is listed): the row it
    had while playing comes back with it, but the relative skip, which would
    replace the steps."""
    await _left_between_neighbors(resuming, 30)

    assert resuming.state()["controls"] == [
        "resume", "seek", "prev", "next", "set_shuffle", "set_repeat",
    ]


async def _left_with_modes(world):
    await world.command("play_context", {"uri": PLAYLIST, "skip_to_uri": TRACK})
    await world.command("set_shuffle", {"shuffle": True})
    await world.command("set_repeat", {"mode": "context"})
    await world.advance(30)
    await world.leave()
    await world.select()


async def test_the_track_left_shows_the_modes_it_played_in(resuming):
    """go-librespot comes back with both modes off: the toggles show what
    the session had, which a resume puts back."""
    await _left_with_modes(resuming)

    assert details(resuming)["shuffle"] is True
    assert details(resuming)["repeat"] == "context"


async def test_a_mode_set_over_the_track_left_sends_nothing(resuming):
    """No daemon session to send it to: the point keeps it."""
    await _left_after(resuming, 30)
    resuming.daemon.posted.clear()

    await resuming.command("set_shuffle", {"shuffle": True})
    await resuming.command("set_repeat", {"mode": "track"})

    assert resuming.daemon.posted == []
    assert details(resuming)["shuffle"] is True
    assert details(resuming)["repeat"] == "track"


async def test_resume_puts_the_modes_back_once_the_track_has_loaded(resuming):
    """A mode sent with the play lands on a context not loaded yet and is
    lost (go-librespot answers the play first): it follows the session's
    first loaded state, as the player's own toggle would."""
    await _left_with_modes(resuming)
    resuming.daemon.posted.clear()
    published = len(resuming.published())

    await resuming.command("resume")

    # The toggles never blink off between the point and the session.
    assert all(
        (state["details"]["shuffle"], state["details"]["repeat"]) == (True, "context")
        for state in resuming.published()[published:]
    )
    assert [command for command, _ in resuming.daemon.posted] == [
        "play", "shuffle_context", "repeat_track", "repeat_context",
    ]
    assert resuming.daemon.shuffle is True
    assert repeat_mode(resuming.daemon.repeat_context, resuming.daemon.repeat_track) == "context"
    assert details(resuming)["shuffle"] is True
    assert details(resuming)["repeat"] == "context"


def _plays_slowly(world, monkeypatch) -> list:
    """go-librespot answers the play; its track loads when the test says
    (_load_starts, _load_ends)."""
    plays: list = []
    monkeypatch.setattr(world.daemon, "_plays", plays.append)
    return plays


async def _load_starts(world, body):
    d = world.daemon
    song = {**track("Loaded"), "uri": body.get("skip_to_uri") or f"{body['uri']}:first"}
    # A play with no session starts from a fresh state (measured 2026-10-04).
    d.shuffle = d.repeat_context = d.repeat_track = False
    d.session, d.account, d.context = True, d.stored, (body["uri"], "Chill appart")
    d.track, d.buffering, d.loading, d.paused = None, True, song, False
    await world._says({"type": "active"}, {"type": "will_play", "uri": song["uri"]})
    await world.advance(0.07)


async def _load_ends(world):
    d = world.daemon
    d.track, d.buffering, d.loading = d.loading, False, None
    await world._says(d.metadata(), d.playing())


async def test_a_mode_set_while_the_track_loads_is_kept(resuming, monkeypatch):
    """The point's toggles stay up while the resumed track loads (no session
    opens before its track is named, E14): a press there is the newer
    choice, and is what the loaded session gets."""
    await _left_with_modes(resuming)
    plays = _plays_slowly(resuming, monkeypatch)
    await resuming.command("resume")
    await _load_starts(resuming, plays[0])
    assert resuming.session() is None

    await resuming.command("set_repeat", {"mode": "off"})
    resuming.daemon.posted.clear()
    await _load_ends(resuming)

    assert resuming.playing()
    assert [command for command, _ in resuming.daemon.posted] == ["shuffle_context"]
    assert details(resuming)["repeat"] == "off"


async def test_modes_turned_off_are_not_put_back_by_an_older_play(resuming, monkeypatch):
    """A first resume that never loaded owed shuffle; the second, made after
    shuffle was turned off, owes nothing."""
    await _left_with_modes(resuming)
    plays = _plays_slowly(resuming, monkeypatch)
    await resuming.command("resume")
    await resuming.command("set_shuffle", {"shuffle": False})
    await resuming.command("set_repeat", {"mode": "off"})
    await resuming.command("resume")
    resuming.daemon.posted.clear()

    await _load_starts(resuming, plays[-1])
    await _load_ends(resuming)

    assert resuming.playing()
    assert not any(c in ("shuffle_context", "repeat_context", "repeat_track") for c, _ in resuming.daemon.posted)


async def test_modes_owed_by_a_play_that_never_loaded_expire(resuming, monkeypatch):
    """The same context cast from a phone long after is the phone's."""
    await _left_with_modes(resuming)
    plays = _plays_slowly(resuming, monkeypatch)
    await resuming.command("resume")
    await resuming.advance(resuming.source.MODES_RESTORE_HOLD + 1)
    resuming.daemon.posted.clear()

    await _load_starts(resuming, plays[0])
    await _load_ends(resuming)

    assert resuming.playing()
    assert not any(c in ("shuffle_context", "repeat_context", "repeat_track") for c, _ in resuming.daemon.posted)


async def test_modes_are_put_back_on_the_resumed_context_only(resuming, monkeypatch):
    """A phone that cast something else before the resumed play loaded keeps
    its own modes."""
    await _left_with_modes(resuming)
    d = resuming.daemon
    monkeypatch.setattr(d, "_plays", lambda body: None)   # answered, never loads
    await resuming.command("resume")
    d.context = None
    d.posted.clear()

    await resuming.phone_plays()

    assert resuming.playing()
    assert not any(c in ("shuffle_context", "repeat_context", "repeat_track") for c, _ in d.posted)


async def test_a_seek_moves_the_track_left_and_plays_nothing(resuming):
    """As an episode's: the bar is dragged with nothing playing, and play
    starts from where it was dropped."""
    await _left_after(resuming, 30)
    resuming.daemon.posted.clear()

    result = await resuming.command("seek", {"position_ms": 90_000})

    assert result["success"] is True
    assert resuming.daemon.posted == []
    assert resuming.state()["resume"]["position_ms"] == 90_000
    await resuming.command("resume")
    assert resuming.daemon.posted == [
        ("play", {"uri": PLAYLIST, "position": 90_000, "skip_to_uri": TRACK}),
    ]


async def test_a_seek_past_the_track_left_is_refused(resuming):
    await _left_after(resuming, 30)
    left_at = resuming.state()["resume"]["position_ms"]

    result = await resuming.command("seek", {"position_ms": 10_000_000})

    assert result["success"] is False
    assert resuming.state()["resume"]["position_ms"] == left_at


async def test_next_plays_the_contexts_following_track(resuming):
    """The user's queue does not outlive the daemon, and skip_to_uri finds
    only what the context lists: next is the context's own next track."""
    await _left_between_neighbors(resuming, 30)
    resuming.daemon.posted.clear()

    await resuming.command("next")

    assert resuming.daemon.posted == [
        ("play", {"uri": PLAYLIST, "position": 0, "skip_to_uri": AFTER}),
    ]
    assert resuming.playing()


async def test_with_no_track_after_next_is_not_offered_and_refused(resuming):
    await _left_after(resuming, 30)
    resuming.daemon.posted.clear()

    result = await resuming.command("next")

    assert "next" not in resuming.state()["controls"]
    assert result["success"] is False
    assert resuming.daemon.posted == []


async def test_next_past_the_last_track_wraps_under_repeat(resuming):
    """go-librespot lists nothing past a context's last track, repeat or not:
    under repeat, next is the context from its first track."""
    resuming.daemon.prev_tracks = [_listed(BEFORE)]
    await _left_after(resuming, 30)
    assert "next" not in resuming.state()["controls"]
    await resuming.command("set_repeat", {"mode": "context"})
    resuming.daemon.posted.clear()

    assert "next" in resuming.state()["controls"]
    await resuming.command("next")

    assert resuming.daemon.posted[0] == ("play", {"uri": PLAYLIST, "position": 0})


async def test_a_next_naming_another_track_than_the_next_is_refused(resuming):
    """skip_to_uri finds only what the context lists, and the user's queue
    did not outlive the daemon: go-librespot would start the context from its
    first track instead."""
    await _left_between_neighbors(resuming, 30)
    resuming.daemon.posted.clear()

    result = await resuming.command("next", {"uri": QUEUED})

    assert result["success"] is False
    assert resuming.daemon.posted == []


async def test_with_an_unread_order_next_never_wraps(resuming):
    """No play order read is no last track: under repeat, next would restart
    the context from its first track instead of moving on."""
    resuming.daemon.queue_answers = False
    await _left_after(resuming, 30)
    await resuming.command("set_repeat", {"mode": "context"})

    assert "next" not in resuming.state()["controls"]


async def test_a_prev_that_may_not_rewind_goes_back_a_track_or_is_refused(resuming):
    """The mini-bar's swipe (allow_seeking false) already slid the previous
    title in: it never restarts the track left."""
    await _left_after(resuming, 30)
    resuming.daemon.posted.clear()

    result = await resuming.command("prev", {"allow_seeking": False})

    assert result["success"] is False
    assert resuming.daemon.posted == []


async def test_a_press_while_a_cast_arrives_leaves_it_alone(resuming):
    """A phone's transfer opens no session before its track is named (E14):
    a play over the point would replace what the phone just sent."""
    await _left_between_neighbors(resuming, 30)
    d = resuming.daemon
    d.session, d.account, d.track, d.loading, d.buffering, d.paused = True, ACCOUNT, None, None, True, False
    await resuming._says({"type": "active"})
    d.posted.clear()

    await resuming.command("next")
    await resuming.command("resume")
    assert d.posted == []

    # Still loading past the hold: the load is stuck, and a press plays.
    await resuming.advance(resuming.source.ARRIVAL_HOLD + 1)
    await resuming.command("resume")
    assert [command for command, _ in d.posted] == ["play"]


async def test_prev_rewinds_a_track_left_past_its_start(resuming):
    await _left_between_neighbors(resuming, 30)
    resuming.daemon.posted.clear()

    await resuming.command("prev")

    assert resuming.daemon.posted == [
        ("play", {"uri": PLAYLIST, "position": 0, "skip_to_uri": TRACK}),
    ]


async def test_prev_near_the_start_plays_the_track_before(resuming):
    await _left_between_neighbors(resuming, 30)
    await resuming.command("seek", {"position_ms": 1_000})
    resuming.daemon.posted.clear()

    await resuming.command("prev")

    assert resuming.daemon.posted == [
        ("play", {"uri": PLAYLIST, "position": 0, "skip_to_uri": BEFORE}),
    ]


async def test_another_accounts_track_is_not_offered(resuming):
    """A friend cast, then the daemon signs back in as the stored account:
    the friend's playlist may not open on it, so nothing is offered."""
    await resuming.cast_from("friend-account")
    await resuming.advance(10)
    resuming.daemon.stored = ACCOUNT
    resuming.state_file.write_text(json.dumps({
        "device_id": "0123456789abcdef0123456789abcdef01234567", "event_manager": None,
        "credentials": {"username": ACCOUNT, "data": "c3RvcmVkLWJsb2I="}, "last_volume": 42,
    }))
    await resuming.leave()
    await resuming.select()

    assert details(resuming)["account"] == ACCOUNT
    assert resuming.state()["resume"] is None
    assert resuming.state()["controls"] == []


async def test_what_plays_on_another_device_comes_before_the_track_left(resuming):
    """The account playing elsewhere is newer than what Milō left: the bar
    offers to take it over, not to resume."""
    await _left_after(resuming, 30)

    await resuming.plays_elsewhere()

    assert resuming.state()["resume"] is None
    assert resuming.state()["controls"] == ["take_over"]


async def test_past_the_window_nothing_is_offered(resuming):
    await resuming.command("play_context", {"uri": PLAYLIST, "skip_to_uri": TRACK})
    await resuming.leave()
    await resuming.advance(301)
    await resuming.select()

    assert resuming.state()["resume"] is None
    assert resuming.state()["controls"] == []
