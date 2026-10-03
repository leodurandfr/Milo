"""Spotify with a stored account: Milō browses and plays with no phone.

Driven through go-librespot 0.10.3 as it was measured with
`persist_credentials` (tests/spotify_world.py, 2026-10-03). What is asserted is
what the wire says (`details.spotify`, the session) and what Milō sent the
daemon — the browser's play button is the only way a session starts without a
phone, so a play sent twice, or sent to nobody, is a bug only a unit shows.
"""
import pytest

from backend.sources.spotify.source import repeat_mode, repeat_posts
from backend.tests.spotify_world import ACCOUNT, SpotifyWorld

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


@pytest.mark.parametrize("skip_to", [TRACK, None])
async def test_a_shuffled_play_turns_shuffle_on_after_the_play(stored, skip_to):
    """A play on a signed-in daemon with no session starts from a fresh state,
    shuffle off (measured): set before the play, shuffle was lost. So it is
    turned on once the session exists, and the play starts where the browser
    said (its random pick) or at the first track."""
    data = {"uri": PLAYLIST, "shuffle": True}
    if skip_to:
        data["skip_to_uri"] = skip_to

    result = await stored.command("play_context", data)

    assert result["success"] is True
    expected_play = {"uri": PLAYLIST, **({"skip_to_uri": skip_to} if skip_to else {})}
    assert stored.daemon.posted == [
        ("play", expected_play),
        ("shuffle_context", {"shuffle_context": True}),
    ]
    assert details(stored)["shuffle"] is True


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
