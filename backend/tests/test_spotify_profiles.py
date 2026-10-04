"""The Spotify profiles Milō keeps, and the browser's library routes.

Driven through go-librespot 0.10.3 as measured (tests/spotify_world.py): a
cast writes the account's credentials into state.json before /status names it,
and the daemon reads that file at every start. What is asserted is what Milō
wrote (profiles.json, state.json), what it asked of systemd and the daemon,
and what the routes answer — a profile the browser can pick but that signs in
as nobody, or a credential that leaks into an answer, shows only on a unit.
"""
import json
import logging
import os

import pytest
from fastapi import HTTPException

from backend.shared.persistence import SchemaVersionMismatch
from backend.sources.spotify import routes
from backend.sources.spotify.profiles import SpotifyProfiles
from backend.tests.spotify_world import ACCOUNT, SpotifyWorld

GUEST = "31guestaccountxxxxxxxxxxxxx"
PLAYLIST = "spotify:playlist:3G1Qd5iTuEjDBCgpqciOpv"


@pytest.fixture
async def world(monkeypatch, tmp_path):
    w = SpotifyWorld(monkeypatch, tmp_path, stored=ACCOUNT)
    w.identities[ACCOUNT] = {"name": "Léo", "image_url": "https://i.scdn.co/image/leo",
                             "has_spotify_image": True}
    await w.select()
    await w.idle()
    yield w
    await w.source.shutdown()


def kept(world):
    return json.loads(world.profiles_file.read_text())["profiles"]


# === Keeping ===

async def test_the_signed_in_account_is_kept_with_its_spotify_identity(world):
    """The stored account becomes a profile the first time /status names it,
    with the name and picture Spotify's profile service gives."""
    profile = kept(world)[ACCOUNT]
    assert profile["credentials"] == "c3RvcmVkLWJsb2I="
    assert profile["spotify_name"] == "Léo"
    assert profile["avatar_url"] == "https://i.scdn.co/image/leo"
    assert world.profiles_file.stat().st_mode & 0o777 == 0o600


async def test_a_picture_changed_in_spotify_reaches_the_profile_at_the_next_sign_in(world, caplog):
    """Read once, a profile kept its first picture for good; it is read again
    at every sign-in, and a read that fails keeps what was kept."""
    world.identities[ACCOUNT] = {"name": "Léo", "image_url": "https://i.scdn.co/image/new"}
    await world.leave()
    await world.select()
    await world.idle()
    assert kept(world)[ACCOUNT]["avatar_url"] == "https://i.scdn.co/image/new"

    # A read that fails is no answer: nothing is rewritten, nothing raised.
    world.identities[ACCOUNT] = None
    before = world.profiles_file.stat().st_mtime_ns
    caplog.set_level(logging.WARNING)
    await world.leave()
    await world.select()
    await world.idle()
    assert world.profiles_file.stat().st_mtime_ns == before
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_an_answer_that_leaves_a_field_out_keeps_what_was_kept(world):
    """Read at every sign-in, an answer naming the account but carrying no
    picture must not wipe the one kept; one that names nothing is no answer."""
    world.identities[ACCOUNT] = {"name": "Léo B."}
    await world.leave()
    await world.select()
    await world.idle()
    assert kept(world)[ACCOUNT]["spotify_name"] == "Léo B."
    assert kept(world)[ACCOUNT]["avatar_url"] == "https://i.scdn.co/image/leo"

    world.identities[ACCOUNT] = {"image_url": None}
    await world.leave()
    await world.select()
    await world.idle()
    assert kept(world)[ACCOUNT]["spotify_name"] == "Léo B."
    assert kept(world)[ACCOUNT]["avatar_url"] == "https://i.scdn.co/image/leo"


async def test_a_guest_who_casts_is_kept_beside_the_owner(world):
    """The guest replaces the owner in go-librespot's single slot; Milō keeps
    both, the owner's blob untouched."""
    await world.cast_from(GUEST)
    await world.idle()

    profiles = kept(world)
    assert set(profiles) == {ACCOUNT, GUEST}
    assert profiles[GUEST]["credentials"] == f"blob-of-{GUEST}"
    assert profiles[ACCOUNT]["credentials"] == "c3RvcmVkLWJsb2I="
    assert world.state()["details"]["account"] == GUEST


async def test_a_profile_spotify_will_not_describe_shows_its_username(world):
    """The profile service failing must not cost the profile: it is kept, and
    named by its username until someone renames it."""
    await world.cast_from(GUEST)
    await world.idle()

    listed = {p["username"]: p for p in world.source.profiles.list()}
    assert listed[GUEST]["name"] == GUEST
    assert listed[GUEST]["avatar_url"] is None


async def test_no_account_name_reaches_the_logs(world, caplog):
    """The backend journal goes into the diagnostic export: an account id or a
    display name there identifies a person."""
    caplog.set_level(logging.INFO)
    await world.cast_from(GUEST)
    await world.idle()
    await world.source.switch_profile(ACCOUNT)
    await world.idle()

    text = "\n".join(record.getMessage() for record in caplog.records)
    assert GUEST not in text and ACCOUNT not in text and "Léo" not in text


# === Switching and forgetting ===

async def test_switching_profile_ends_the_session_and_signs_in_as_the_other(world):
    """The file is written between the daemon's stop and its start — it
    rewrites state.json itself — and only `credentials` changes in it."""
    await world.cast_from(GUEST)
    await world.idle()
    before = world.stored_state()
    calls = []
    world.systemd.stop.side_effect = _recording(calls, "stop", world._unit_stop, world)
    world.systemd.start.side_effect = _recording(calls, "start", world._unit_start, world)

    result = await world.source.switch_profile(ACCOUNT)
    await world.advance(2.1)

    assert result["success"] is True
    assert world.session_ends() == ["user_stop"]
    assert calls == [("stop", GUEST), ("start", ACCOUNT)]
    after = world.stored_state()
    assert after["credentials"]["username"] == ACCOUNT
    assert {k: v for k, v in after.items() if k != "credentials"} == \
        {k: v for k, v in before.items() if k != "credentials"}
    assert world.state()["details"]["account"] == ACCOUNT
    assert world.state()["details"]["signing_in"] is False


async def test_switching_while_spotify_is_stopped_only_writes_the_file(world):
    """A stopped daemon is never started for a profile pick: it reads the file
    at its next start."""
    await world.cast_from(GUEST)
    await world.idle()
    await world.leave()
    world.systemd.start.reset_mock()

    await world.source.switch_profile(ACCOUNT)

    assert world.stored_account() == ACCOUNT
    world.systemd.start.assert_not_awaited()


async def test_forgetting_the_active_profile_leaves_the_daemon_to_nobody(world):
    """Left in state.json, the forgotten account would sign back in and be
    kept again by the next /status."""
    await world.source.forget_profile(ACCOUNT)
    await world.advance(2.1)

    assert ACCOUNT not in kept(world)
    assert world.stored_state()["credentials"] == {"username": "", "data": None}
    assert world.state()["details"]["account"] is None
    assert world.daemon.stored is None


async def test_forgetting_another_profile_restarts_nothing(world):
    await world.cast_from(GUEST)
    await world.idle()
    world.systemd.stop.reset_mock()

    await world.source.forget_profile(ACCOUNT)

    assert set(kept(world)) == {GUEST}
    world.systemd.stop.assert_not_awaited()
    assert world.state()["details"]["account"] == GUEST


async def test_refused_stored_credentials_mark_the_profile_and_free_the_daemon(world):
    """A changed password: go-librespot exits on stored credentials it cannot
    use, at every start. The profile says so, and the daemon is handed nobody."""
    await world.source._handle_log_line(
        'level=fatal msg="daemon exited with error" error="failed authenticating accesspoint '
        'with stored credentials: accesspoint login failed: BadCredentials <nil>"'
    )
    await world.advance(2.1)
    await world.idle()

    assert kept(world)[ACCOUNT]["stale"] is True
    assert world.stored_state()["credentials"]["username"] == ""
    assert world.errors() == ["credentials_refused"]


async def test_a_forgotten_profile_comes_back_with_its_next_cast(world):
    """The forget screen promises it: a new cast from the account keeps it
    again — here one forgotten while another account was signed in, so no
    restart came between the forget and the cast."""
    await world.cast_from(GUEST)
    await world.idle()
    await world.source.forget_profile(ACCOUNT)
    assert ACCOUNT not in kept(world)

    await world.cast_from(ACCOUNT)
    await world.idle()

    assert ACCOUNT in kept(world)


async def test_refused_credentials_while_the_daemon_restarts_itself_sign_in_nobody(world):
    """go-librespot exits on credentials it cannot use and Restart= brings it
    back: the account must not be announced as signing in meanwhile."""
    await world.kill_daemon()
    await world.source._handle_log_line(
        'level=fatal msg="daemon exited with error" error="failed authenticating accesspoint '
        'with stored credentials: accesspoint login failed: BadCredentials <nil>"'
    )
    await world.idle()
    await world.systemd_restarts_it()

    assert world.state()["details"]["account"] is None
    assert world.state()["details"]["signing_in"] is False


# === Persistence ===

async def test_profiles_round_trip_and_fail_loud_on_a_schema_drift(tmp_path):
    file = tmp_path / "profiles.json"
    profiles = SpotifyProfiles(file)
    await profiles.initialize()
    await profiles.harvest(ACCOUNT, "blob")
    await profiles.rename(ACCOUNT, "Salon")

    reloaded = SpotifyProfiles(file)
    await reloaded.initialize()
    assert reloaded.list()[0]["name"] == "Salon"
    assert reloaded.credentials(ACCOUNT) == "blob"

    file.write_text(json.dumps({"schema_version": 0, "profiles": {}}))
    with pytest.raises(SchemaVersionMismatch):
        await SpotifyProfiles(file).initialize()


# === Routes ===

async def test_the_home_route_answers_the_signed_in_library(world):
    world.daemon.playlists = [
        {"uri": "spotify:playlist:37i9dQZF1E8PE5vUgEe9T7", "name": "Radio Leaves", "description": "",
         "owner_username": "spotify", "length": 50, "collaborative": False, "can_edit": False,
         "folder": [], "image_url": "https://pickasso.spotifycdn.com/image/x/dt/v1/img/radio/track/y/en"},
    ]
    answer = await routes.get_home(source=world.source)
    assert answer["liked_songs_uri"] == f"spotify:user:{ACCOUNT}:collection"
    assert [p["name"] for p in answer["sections"]["radios"]] == ["Radio Leaves"]


async def test_a_listing_still_loading_answers_its_progress(world):
    """The browser asks again while `ready` is false: the route must never
    hold a request past its wait, nor hand over a partial list as complete."""
    world.daemon.listings[PLAYLIST] = [
        {"uri": "spotify:track:a", "track": {"uri": "spotify:track:a", "name": "A", "artist_names": ["X"],
                                             "artist_uris": ["spotify:artist:x"], "duration": 1000}},
    ]
    world.daemon.listing_calls_until_ready = 1000
    pending = await routes.get_context(PLAYLIST, source=world.source)
    assert pending["ready"] is False and "tracks" not in pending

    world.daemon.listing_calls_until_ready = 0
    ready = await routes.get_context(PLAYLIST, source=world.source)
    assert ready["ready"] is True and [t["title"] for t in ready["tracks"]] == ["A"]


async def test_a_listing_whose_cache_stops_short_is_shown_with_what_it_has(world):
    """A track Spotify will not describe keeps `cached` below `length` for good:
    the listing must still be handed over once the count stops moving, not
    asked for forever."""
    world.daemon.listings[PLAYLIST] = [
        {"uri": "spotify:track:a", "track": {"uri": "spotify:track:a", "name": "A", "artist_names": ["X"],
                                             "artist_uris": [], "duration": 1000}},
        {"uri": "spotify:track:gone", "track": None},
    ]
    ready = await routes.get_context(PLAYLIST, source=world.source)

    assert ready["ready"] is True
    assert [t["title"] for t in ready["tracks"]] == ["A"]


def described(cover_id):
    return {"uri": f"spotify:track:{cover_id}",
            "track": {"uri": f"spotify:track:{cover_id}", "name": cover_id, "artist_names": [], "artist_uris": [],
                      "album_cover_url": f"https://i.scdn.co/image/ab67616d0000b273{cover_id}"}}


async def test_a_cover_redirects_to_the_mosaic_without_waiting_for_the_whole_listing(world):
    """A home draws one cover per playlist: four described albums are enough,
    and asking again for the rest of a long listing would hold the browser's
    connections for nothing."""
    world.daemon.listings[PLAYLIST] = [described(c) for c in ("a1", "b2", "c3", "d4")] + [
        {"uri": "spotify:track:later", "track": None},
    ]
    answer = await routes.get_context_cover(PLAYLIST, source=world.source)

    assert answer.status_code == 302
    assert answer.headers["location"].startswith("https://mosaic.scdn.co/300/ab67616d0000b273a1")
    assert world.daemon.listing_calls[PLAYLIST] == 1


async def test_a_playlist_opening_on_a_track_spotify_no_longer_describes_still_has_its_cover(world):
    """Old playlists keep tracks that stay undescribed for good: once the
    listing stops moving, the albums around them make the cover."""
    world.daemon.listings[PLAYLIST] = [{"uri": "spotify:track:gone", "track": None}, described("a1")]
    answer = await routes.get_context_cover(PLAYLIST, source=world.source)

    assert answer.status_code == 302
    assert answer.headers["location"] == "https://i.scdn.co/image/ab67616d0000b273a1"


async def test_a_listing_with_nothing_described_yet_is_never_taken_as_complete(world, monkeypatch):
    """go-librespot rebuilds an evicted listing ready but with nothing
    described, and under load stays so for over a second: taken as complete,
    the playlist page drew an empty playlist and the cover a placeholder."""
    from backend.sources.spotify.library import SpotifyLibrary

    monkeypatch.setattr(SpotifyLibrary, "CONTEXT_WAIT_S", 0.1)
    monkeypatch.setattr(SpotifyLibrary, "CONTEXT_POLL_S", 0.01)
    world.daemon.listings[PLAYLIST] = [{"uri": "spotify:track:pending", "track": None}]

    listing = await routes.get_context(PLAYLIST, source=world.source)
    assert listing["ready"] is False
    await routes.get_context_cover(PLAYLIST, source=world.source)
    assert world.daemon.listing_calls[PLAYLIST] > 2 * (SpotifyLibrary.STALLED_POLLS + 1)


async def test_a_cover_the_wait_ran_out_on_is_not_drawn_from_albums_past_a_gap(world, monkeypatch):
    """Under load the first tracks can still be undescribed when the wait
    ends: the albums cached after them are not the playlist's first ones."""
    from backend.sources.spotify.library import SpotifyLibrary

    monkeypatch.setattr(SpotifyLibrary, "CONTEXT_WAIT_S", 0.1)
    monkeypatch.setattr(SpotifyLibrary, "CONTEXT_POLL_S", 0.01)
    monkeypatch.setattr(SpotifyLibrary, "STALLED_POLLS", 1000)
    world.daemon.listings[PLAYLIST] = [{"uri": "spotify:track:pending", "track": None}, described("a1")]

    answer = await routes.get_context_cover(PLAYLIST, source=world.source)
    assert answer.status_code == 404


async def test_an_empty_playlist_has_no_cover(world):
    world.daemon.listings[PLAYLIST] = []
    answer = await routes.get_context_cover(PLAYLIST, source=world.source)
    assert answer.status_code == 404


async def test_a_request_cut_by_a_daemon_restart_answers_409(world, monkeypatch):
    """A profile switch stops go-librespot under an in-flight listing: the
    browser reads 'signing in', never a 500."""
    import aiohttp

    def cut(*args, **kwargs):
        raise aiohttp.ServerDisconnectedError()

    monkeypatch.setattr(world.daemon, "request", cut)
    with pytest.raises(HTTPException) as answer:
        await routes.get_context(PLAYLIST, source=world.source)
    assert answer.value.status_code == 409


async def test_the_library_answers_409_when_spotify_is_not_running(world):
    await world.leave()
    with pytest.raises(HTTPException) as answer:
        await routes.get_context(PLAYLIST, source=world.source)
    assert answer.value.status_code == 409


async def test_liked_tracks_takes_one_to_fifty_uris(world):
    with pytest.raises(HTTPException) as answer:
        await routes.get_liked_tracks(uris=",".join(f"spotify:track:{i}" for i in range(51)), source=world.source)
    assert answer.value.status_code == 422


async def test_no_route_answers_a_credential(world):
    """The blob is reusable from any device: it must never leave the backend."""
    answer = await routes.get_profiles(source=world.source)
    assert "c3RvcmVkLWJsb2I=" not in json.dumps(answer)
    assert answer["profiles"][0]["active"] is True


def _recording(calls, name, real, world):
    async def record(*args, **kwargs):
        calls.append((name, world.stored_account()))
        return await real(*args, **kwargs)
    return record


# Keep os imported for the mode checks some environments need.
_ = os
