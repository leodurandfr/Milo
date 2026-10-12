"""The Spotify Jam of the signed-in account (sources/spotify/jam.py), driven
through the go-librespot and the Spotify the unit was measured against
(tests/spotify_world.py).

What is asserted is what the wire says of the Jam (`details.jam`, which the
source bar names), and what Milō asked of Spotify.
"""
import logging

import pytest

from backend.tests.golden.harness import settle
from backend.tests.spotify_world import ACCOUNT, SpotifyWorld, jam_session

LEO = ("86588fb858440c209c6dd1d31ef1ccf8", "Léo", 1791722852818)
MAX = ("6b1f0aa8c4c2e5b4d2c1c0b9a8f7e6d5", "Max", 1791722900000)
CLA = ("3c9a6c47f1d6e0b2a4b8c1d3e5f70912", "Cla", 1791722953451)
GUEST = "dxjr7oullkuqe0swrkdthtcjh"


@pytest.fixture
async def make_world(monkeypatch, tmp_path):
    worlds = []

    async def make(jam=None, settings=None, follow_jam=True):
        w = SpotifyWorld(monkeypatch, tmp_path, settings=settings, stored=ACCOUNT, follow_jam=follow_jam)
        w.daemon.jam = jam
        await w.select()
        worlds.append(w)
        return w

    yield make
    for w in worlds:
        await w.source.shutdown()


def jam_logs(caplog):
    return [r for r in caplog.records if r.name == "source.spotify.jam"]


def names(world):
    jam = world.jam()
    return None if jam is None else [m["name"] for m in jam["members"]]


async def test_the_host_comes_first_then_the_members_in_the_order_they_joined(make_world):
    """Spotify lists the members in the order of whoever asks (measured): the
    source bar reads "Jam de Léo et …" with the host first whatever it got."""
    session = jam_session(LEO, MAX, CLA)
    session["session_members"].reverse()
    world = await make_world(jam=session)

    assert names(world) == ["Léo", "Max", "Cla"]


async def test_members_joining_and_leaving_reach_the_wire_as_spotify_pushes_them(make_world):
    """Who is in the Jam comes from the dealer's pushes, never from polling."""
    world = await make_world(jam=jam_session(LEO))
    assert names(world) == ["Léo"]

    await world.jam_update(jam_session(LEO, CLA), "USER_JOINED")
    assert names(world) == ["Léo", "Cla"]

    await world.jam_update(jam_session(LEO), "USER_LEFT")
    assert names(world) == ["Léo"]
    assert world.daemon.jam_reads == 1


async def test_a_jam_that_ends_leaves_the_wire(make_world):
    world = await make_world(jam=jam_session(LEO, CLA))

    await world.jam_update(None, "SESSION_DELETED")

    assert world.jam() is None


async def test_a_jam_on_another_device_or_a_dormant_session_is_not_shown(make_world):
    """A Jam outlives its device (measured), and after a Jam ends Spotify keeps
    a dormant session with no `active`: neither plays here."""
    world = await make_world(jam=jam_session(LEO, CLA, host="1d0183bba7f631d45ffa2be4e1208f37682b5126"))
    assert world.jam() is None

    await world.jam_update(jam_session(LEO, active=None), "NEW_SESSION")
    assert world.jam() is None


async def test_the_jam_is_read_once_per_connection_and_never_polled(make_world):
    """Read every 2 s, social-connect answered 429 (measured): one read when the
    socket opens, then only what it pushes; the socket is kept with a ping."""
    world = await make_world()
    assert world.jam() is None

    await world.advance(120)

    assert world.daemon.jam_reads == 1
    assert len(world.daemon.dealer_urls) == 1
    assert '{"type":"ping"}' in world.daemon.dealer.sent


async def test_a_closed_socket_is_opened_again_and_read_again(make_world, caplog):
    """Updates pushed while the socket was closed are lost: the reopened one
    reads the Jam again. A socket that stood and reopens is no outage."""
    world = await make_world(jam=jam_session(LEO))
    await world.advance(31)     # past its first ping
    world.daemon.jam = jam_session(LEO, CLA)

    with caplog.at_level(logging.WARNING, logger="source.spotify.jam"):
        await world.dealer_drops()
        await world.advance(2.1)

    assert len(world.daemon.dealer_urls) == 2
    assert world.daemon.jam_reads == 2
    assert names(world) == ["Léo", "Cla"]
    assert not jam_logs(caplog)


async def test_an_unreachable_dealer_is_followed_again_and_reports_nothing(make_world, caplog):
    """A feed that reconnects on its own is no dead feed (CLAUDE.md): nothing
    above debug, and it follows the Jam again once the dealer answers."""
    world = await make_world(jam=jam_session(LEO))
    await world.advance(31)
    world.daemon.dealer_up = False

    with caplog.at_level(logging.DEBUG, logger="source.spotify.jam"):
        await world.dealer_drops()
        await world.advance(2 + 4 + 8 + 16 + 1)
        world.daemon.jam = jam_session(LEO, CLA)
        world.daemon.dealer_up = True
        await world.advance(32 + 1)

    assert not [r for r in jam_logs(caplog) if r.levelno > logging.DEBUG]
    assert len(world.daemon.dealer_urls) == 2
    assert names(world) == ["Léo", "Cla"]


async def test_another_account_casting_gets_its_own_feed(make_world):
    """The feed follows the account signed in: a guest's cast reads that
    account's Jam on a socket of its own."""
    world = await make_world(jam=jam_session(LEO, CLA))
    world.daemon.jam = None

    await world.cast_from(GUEST)

    assert len(world.daemon.dealer_urls) == 2
    assert world.daemon.jam_reads == 2
    assert world.jam() is None


async def test_a_source_not_asked_to_follow_the_jam_never_reaches_spotify(make_world):
    """Only dependencies.py asks for it: the golden wire and every other world
    run with no internet."""
    world = await make_world(jam=jam_session(LEO, CLA), follow_jam=False)

    await world.phone_plays()

    assert world.daemon.dealer_urls == []
    assert world.daemon.jam_reads == 0
    assert world.jam() is None


async def test_no_log_line_names_an_account_or_a_member(make_world, caplog):
    with caplog.at_level(logging.DEBUG), caplog.at_level(logging.DEBUG, logger="source.spotify.jam"):
        world = await make_world(jam=jam_session(LEO, CLA))
        await world.jam_update(jam_session(LEO, CLA, MAX), "USER_JOINED")
        await world.dealer_drops()
        await world.advance(2.1)

    logged = " ".join(r.getMessage() for r in caplog.records)
    for secret in (ACCOUNT, LEO[0], "Léo", "Cla", "Max", "access-token"):
        assert secret not in logged


async def test_a_profile_switch_follows_the_new_account_without_reporting_an_outage(make_world, caplog):
    """A switch stops go-librespot on purpose: the feed of the account it left
    ends there, and the new account's opens once the daemon names it — never
    against a stopped daemon, which read as a lost feed on the unit."""
    world = await make_world(jam=jam_session(LEO))
    await world.idle()
    await world.cast_from(GUEST)
    await world.idle()
    opened = len(world.daemon.dealer_urls)
    # systemctl start takes its time on the unit: whatever is pending runs
    # against the stopped daemon first.
    start = world.systemd.start.side_effect

    async def slow_start(*args):
        await settle()
        return await start(*args)

    world.systemd.start.side_effect = slow_start

    with caplog.at_level(logging.INFO, logger="source.spotify.jam"):
        await world.source.switch_profile(ACCOUNT)
        await world.advance(2.1)

    assert not [r for r in jam_logs(caplog) if "lost" in r.getMessage()]
    assert len(world.daemon.dealer_urls) == opened + 1
    assert world.source.account == ACCOUNT


async def test_a_refused_handshake_never_logs_the_token(make_world, caplog):
    """The dealer's URL carries the access token, and aiohttp's error text
    names the URL: what is logged is the error's type and status alone."""
    world = await make_world(jam=jam_session(LEO))
    await world.advance(31)
    world.daemon.dealer_refuses = 401

    with caplog.at_level(logging.DEBUG), caplog.at_level(logging.DEBUG, logger="source.spotify.jam"):
        await world.dealer_drops()
        await world.advance(2.1)

    assert "access-token" in str(world.daemon.last_refusal)     # what would have leaked
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "access-token" not in logged
    assert "WSServerHandshakeError 401" in logged


async def test_a_dealer_that_drops_every_socket_at_once_is_retried_with_the_backoff(make_world, caplog):
    """Each retry reads the Jam again, the read Spotify rate-limits: a socket
    closed before its first ping is an outage, never a reconnection every 2 s."""
    world = await make_world(jam=jam_session(LEO))
    world.daemon.dealer_closes_at_once = True

    with caplog.at_level(logging.INFO, logger="source.spotify.jam"):
        await world.dealer_drops()
        await world.advance(120)

    assert len(world.daemon.dealer_urls) <= 1 + 6       # 2, 4, 8, 16, 32, 60 s — not 60 tries
    assert world.daemon.jam_reads == len(world.daemon.dealer_urls)
    assert not [r for r in jam_logs(caplog) if r.levelno > logging.DEBUG]


async def test_a_frame_that_is_no_object_is_skipped_and_the_socket_kept(make_world):
    world = await make_world(jam=jam_session(LEO))

    world.daemon.dealer.queue.put_nowait([])
    world.daemon.dealer.queue.put_nowait("pong")
    await world.jam_update(jam_session(LEO, CLA), "USER_JOINED")

    assert len(world.daemon.dealer_urls) == 1
    assert names(world) == ["Léo", "Cla"]


async def test_a_socket_whose_ping_goes_unanswered_is_opened_again(make_world):
    """A half-open socket (a network path that changed under it) takes no
    error for TCP's ~15 min: an unanswered ping closes it, as go-librespot's
    own dealer client does."""
    world = await make_world(jam=jam_session(LEO))
    await world.advance(31)
    world.daemon.dealer.answers_pings = False
    world.daemon.jam = jam_session(LEO, CLA)

    await world.advance(30 + 30 + 2.1)

    assert len(world.daemon.dealer_urls) == 2
    assert names(world) == ["Léo", "Cla"]


async def test_a_member_name_that_is_no_string_never_stops_the_wire(make_world):
    """Spotify's answer is taken as it comes: a name of another type is no name,
    never a validation error in every publish after it."""
    world = await make_world(jam=jam_session(LEO))
    odd = jam_session(LEO, CLA)
    odd["session_members"][1]["display_name"] = 42

    await world.jam_update(odd, "USER_JOINED")
    await world.phone_plays()

    assert names(world) == ["Léo", None]
    assert world.session()["title"] == "Parapluie"


async def test_a_refused_first_read_keeps_the_socket_and_shows_no_stale_jam(make_world):
    """A 429 on the read is no reason to drop the socket (reopening it would
    ask the rate-limited read again); and the Jam told before the outage may
    have ended unheard, so none is shown until the next update."""
    world = await make_world(jam=jam_session(LEO, CLA))
    await world.advance(31)
    world.daemon.jam_read_refused = 429
    await world.dealer_drops()
    await world.advance(2.1)
    opened = len(world.daemon.dealer_urls)
    assert world.jam() is None

    await world.advance(120)
    await world.jam_update(jam_session(LEO, CLA), "USER_JOINED")

    assert len(world.daemon.dealer_urls) == opened
    assert names(world) == ["Léo", "Cla"]


async def test_a_sign_in_given_up_takes_the_jam_with_it(make_world):
    """The account the daemon will not sign back in is nobody's: its Jam is no
    longer the signed-in account's, and its feed ends with it."""
    world = await make_world(jam=jam_session(LEO, CLA))
    await world.phone_plays()
    assert names(world) == ["Léo", "Cla"]
    # The session ends and the sign-in that follows never completes.
    world.daemon.signin_hangs = True
    world.daemon.signed_in = False

    await world.phone_leaves()
    await world.advance(world.source.SIGNIN_TIMEOUT + 0.5)
    await world.advance(world.source.SIGNIN_TIMEOUT + 0.5)

    assert world.state()["details"]["account"] is None
    assert world.jam() is None


async def test_an_update_in_an_unknown_form_never_ends_the_jam(make_world, caplog):
    """Only SESSION_DELETED ends a Jam: an update with no session, or one in
    another encoding than the JSON measured, is skipped with a line."""
    world = await make_world(jam=jam_session(LEO, CLA))

    with caplog.at_level(logging.INFO, logger="source.spotify.jam"):
        for payload in ({"reason": "SOMETHING_NEW"}, "eyJzZXNzaW9uIjoge319"):
            world.daemon.dealer.queue.put_nowait({
                "type": "message", "uri": "social-connect/v2/session_update", "payloads": [payload],
            })
        await world.jam_update(jam_session(LEO, CLA, MAX), "USER_JOINED")

    assert names(world) == ["Léo", "Max", "Cla"]
    assert len([r for r in jam_logs(caplog) if "skipped" in r.getMessage()]) == 2
    assert len(world.daemon.dealer_urls) == 1
