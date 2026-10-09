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
from backend.sources.spotify.catalog import spotify_gid
from backend.sources.spotify.profiles import SpotifyProfiles
from backend.tests.spotify_world import ACCOUNT, SpotifyWorld

GUEST = "31guestaccountxxxxxxxxxxxxx"
THIRD = "31thirdaccountxxxxxxxxxxxxx"
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

    # A read that fails is no answer: the picture kept stays, nothing raised.
    world.identities[ACCOUNT] = None
    caplog.set_level(logging.WARNING)
    await world.leave()
    await world.select()
    await world.idle()
    assert kept(world)[ACCOUNT]["avatar_url"] == "https://i.scdn.co/image/new"
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
    named by its username until Spotify describes it."""
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


def profile_pushes(world):
    return [e["data"]["profiles"] for e in world.recorder.envelopes
            if (e["category"], e["type"]) == ("source", "profiles_changed")]


async def test_a_new_cast_tells_the_browser_its_profile_and_then_its_picture(world):
    """The audio state names a new account before its profile is kept, and a
    picture comes later still: a browser that read the list at the account
    change never shows the newcomer's avatar unless both are pushed."""
    world.identities[GUEST] = {"name": "Cla", "image_url": "https://i.scdn.co/image/cla"}
    sent = len(profile_pushes(world))
    await world.cast_from(GUEST)
    await world.idle()

    kept_then, described = profile_pushes(world)[sent:]
    assert [(p["username"], p["avatar_url"]) for p in kept_then] == [
        (ACCOUNT, "https://i.scdn.co/image/leo"), (GUEST, None)]
    assert [(p["username"], p["name"], p["avatar_url"]) for p in described] == [
        (ACCOUNT, "Léo", "https://i.scdn.co/image/leo"), (GUEST, "Cla", "https://i.scdn.co/image/cla")]

    # Read again at the next sign-in, an unchanged identity pushes nothing.
    sent = len(profile_pushes(world))
    await world.leave()
    await world.select()
    await world.idle()
    assert profile_pushes(world)[sent:] == []


async def test_a_forgotten_profile_leaves_the_browser_s_list(world):
    await world.cast_from(GUEST)
    await world.idle()
    await routes.forget_profile(GUEST, source=world.source)
    await world.idle()

    assert [p["username"] for p in profile_pushes(world)[-1]] == [ACCOUNT]


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


async def test_forgetting_the_only_profile_leaves_the_daemon_to_nobody(world):
    """Left in state.json, the forgotten account would sign back in and be
    kept again by the next /status; with no profile left, nobody signs in and
    the browser gives way to the card."""
    await world.source.forget_profile(ACCOUNT)
    await world.advance(2.1)

    assert ACCOUNT not in kept(world)
    assert world.stored_state()["credentials"] == {"username": "", "data": None}
    assert world.state()["details"]["account"] is None
    assert world.daemon.stored is None


async def test_forgetting_the_active_profile_signs_in_the_one_signed_in_last(world):
    """With profiles left, the card would hide them: the one signed in last
    takes the forgotten one's place — the owner here, signed in after the
    guest was added, not the guest, added after the owner."""
    await world.cast_from(GUEST)
    await world.idle()
    await world.advance(60)
    await world.cast_from(THIRD)
    await world.idle()
    await world.advance(60)
    await world.source.switch_profile(ACCOUNT)
    await world.advance(2.1)
    await world.idle()
    await world.advance(60)
    await world.source.switch_profile(THIRD)
    await world.advance(2.1)
    await world.idle()

    await world.source.forget_profile(THIRD)
    await world.advance(2.1)
    await world.idle()

    assert set(kept(world)) == {ACCOUNT, GUEST}
    assert world.stored_account() == ACCOUNT
    assert world.state()["details"]["account"] == ACCOUNT


async def test_forgetting_another_profile_restarts_nothing(world):
    await world.cast_from(GUEST)
    await world.idle()
    world.systemd.stop.reset_mock()

    await world.source.forget_profile(ACCOUNT)

    assert set(kept(world)) == {GUEST}
    world.systemd.stop.assert_not_awaited()
    assert world.state()["details"]["account"] == GUEST


REFUSED = ('level=fatal msg="daemon exited with error" error="failed authenticating accesspoint '
           'with stored credentials: accesspoint login failed: BadCredentials <nil>"')


async def test_refused_stored_credentials_forget_the_only_profile_and_free_the_daemon(world):
    """A changed password: go-librespot exits on stored credentials it cannot
    use, at every start. The profile goes, and with none left the daemon is
    handed nobody — the card, ready for a cast."""
    await world.source._handle_log_line(REFUSED)
    await world.advance(2.1)
    await world.idle()

    assert kept(world) == {}
    assert world.stored_state()["credentials"]["username"] == ""
    assert world.state()["details"]["account"] is None
    assert world.errors() == ["credentials_refused"]


async def test_refused_stored_credentials_hand_the_daemon_to_the_profile_left(world):
    """The guest's password changed: the owner, still kept, signs in rather
    than the card hiding them."""
    await world.cast_from(GUEST)
    await world.idle()

    await world.source._handle_log_line(REFUSED)
    await world.advance(2.1)
    await world.idle()

    assert set(kept(world)) == {ACCOUNT}
    assert world.stored_account() == ACCOUNT
    assert world.state()["details"]["account"] == ACCOUNT
    assert world.errors() == ["credentials_refused"]


async def test_a_refusal_heard_twice_moves_the_daemon_once(world):
    """go-librespot can log the refusal again before Milō has moved it on (a
    Restart= in between): the second one must not take the next profile out
    or restart the daemon that just signed in."""
    await world.cast_from(GUEST)
    await world.idle()
    world.systemd.stop.reset_mock()

    await world.source._handle_log_line(REFUSED)
    await world.source._handle_log_line(REFUSED)
    await world.advance(2.1)
    await world.idle()

    assert set(kept(world)) == {ACCOUNT}
    assert world.stored_account() == ACCOUNT
    assert world.systemd.stop.await_count == 1


async def test_a_spotify_outage_at_sign_in_forgets_nothing(world):
    """Spotify down is not the account's fault: the profile stays, signed in
    again at the next start."""
    await world.source._handle_log_line(
        'level=warning msg="login5 request failed, retrying" '
        'error="login5 returned HTTP 503: no healthy upstream"'
    )
    await world.advance(2.1)
    await world.idle()

    assert set(kept(world)) == {ACCOUNT}
    assert world.stored_account() == ACCOUNT


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
    await world.source._handle_log_line(REFUSED)
    await world.idle()
    await world.systemd_restarts_it()

    assert world.state()["details"]["account"] is None
    assert world.state()["details"]["signing_in"] is False


# === Persistence ===

async def test_profiles_round_trip_and_fail_loud_on_a_schema_drift(tmp_path):
    file = tmp_path / "profiles.json"
    profiles = SpotifyProfiles(file)
    await profiles.initialize()
    await profiles.harvest(ACCOUNT, "blob", 1.0)
    await profiles.set_identity(ACCOUNT, {"spotify_name": "Léo", "avatar_url": None})

    reloaded = SpotifyProfiles(file)
    await reloaded.initialize()
    assert reloaded.list()[0]["name"] == "Léo"
    assert reloaded.credentials(ACCOUNT) == "blob"

    file.write_text(json.dumps({"schema_version": 1, "profiles": {}}))
    with pytest.raises(SchemaVersionMismatch):
        await SpotifyProfiles(file).initialize()


async def test_the_successor_is_the_profile_signed_in_last(tmp_path):
    """Not the one added last: a profile signed in again moves ahead."""
    profiles = SpotifyProfiles(tmp_path / "profiles.json")
    await profiles.initialize()
    await profiles.harvest(ACCOUNT, "blob", 1.0)
    await profiles.harvest(GUEST, "blob", 2.0)
    await profiles.harvest(ACCOUNT, "blob", 3.0)

    assert profiles.successor() == ACCOUNT
    await profiles.forget(ACCOUNT)
    assert profiles.successor() == GUEST
    await profiles.forget(GUEST)
    assert profiles.successor() is None


# === Routes ===

HOME_SHELF = "spotify:section:0JQ5DAUnp4wcj0bCb3wh3S"
DAILY_MIX = {"component": {"id": "glue2:card", "category": "card"}, "text": {"title": "Daily Mix 1"},
             "target": {"uri": "spotify:playlist:37i9dQZF1E35HBJ2wuuMhp"}, "metadata": {"sectionId": HOME_SHELF}}


async def test_the_home_route_answers_spotifys_home_then_the_library(world):
    """Spotify's shelves, titled in the language asked for, then the
    account's own playlists."""
    world.daemon.home_answer = {"body": [
        {"component": {"id": "glue:sectionHeader", "category": "header"}, "text": {"title": "Conçu pour Léo"},
         "metadata": {"sectionId": HOME_SHELF}},
        DAILY_MIX,
    ]}
    world.daemon.playlists = [
        {"uri": "spotify:playlist:1mine", "name": "Chill appart", "description": "",
         "owner_username": ACCOUNT, "length": 50, "collaborative": False, "can_edit": True,
         "folder": [], "image_url": "https://i.scdn.co/image/x"},
    ]
    answer = await routes.get_home(locale="fr-FR", source=world.source)

    assert world.daemon.home_locales == ["fr-FR"]
    assert answer["liked_songs_uri"] == f"spotify:user:{ACCOUNT}:collection"
    assert [(s["title"], [c["name"] for c in s["items"]]) for s in answer["shelves"]] == [
        ("Conçu pour Léo", ["Daily Mix 1"]),
    ]
    assert [p["name"] for p in answer["playlists"]["mine"]] == ["Chill appart"]


def fans_of(section, title, artist_id):
    return [
        {"component": {"id": "glue:sectionHeader", "category": "header"}, "text": {"title": title},
         "metadata": {"sectionId": section}},
        {"component": {"id": "glue2:card", "category": "card"}, "text": {"title": "This Is"},
         "images": {"main": {"uri": f"https://pickasso.spotifycdn.com/image/x/dt/v1/img/thisisv3/{artist_id}/fr"}},
         "target": {"uri": "spotify:playlist:37i9dQZF1DZ06evO3LyCc0"}, "metadata": {"sectionId": section}},
    ]


async def test_shelves_about_artists_are_headed_with_one_token_and_a_failed_artist_costs_only_its_heading(
    world, caplog,
):
    """Two shelves about two artists: both are asked at once under one token
    (the daemon is on the Pi), and Spotify answering 503 for one leaves that
    shelf a plain title, never the home an error."""
    fka, mairo = "6nB0iY1cjSY1KyhYyuIIKH", "2bd5Yx9Q4oGSCWzEuIpZ6p"
    world.daemon.artist_metadata = {spotify_gid(fka): {"name": "FKA twigs", "portrait_group": {"image": [
        {"file_id": "ab6761610000f1783f6b8973be0344896d8680ab", "size": "SMALL"}]}}}
    world.daemon.home_answer = {"body": [
        *fans_of("spotify:section:a", "Pour les fans de FKA twigs", fka),
        *fans_of("spotify:section:b", "Pour les fans de Mairo", mairo),
    ]}
    caplog.set_level(logging.WARNING)
    before = world.daemon.token_reads
    answer = await routes.get_home(locale="fr", source=world.source)

    assert [(s["overline"], s["artist"]["name"]) if "artist" in s else s["title"] for s in answer["shelves"]] == [
        ("Pour les fans de", "FKA twigs"), "Pour les fans de Mairo",
    ]
    assert world.daemon.token_reads - before == 2  # the home's, then one for both artists
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


TRACK_URI = "spotify:track:0yNttAVwMr39qyODHNIkrY"


async def test_a_track_radio_is_the_playlist_spotify_makes_for_that_track(world):
    """The browser opens the uri answered as a playlist page: it must be the
    one Spotify made from this track, not a guess."""
    answer = await routes.get_track_radio(uri=TRACK_URI, source=world.source)

    assert answer == {"status": "success", "uri": "spotify:playlist:37i9dQZF1E8UJ1xRHXd2z2"}
    assert [url.rsplit("/", 1)[1] for url in world.daemon.radio_asked] == [TRACK_URI]


async def test_a_track_spotify_makes_no_radio_for_answers_404(world):
    world.daemon.radio_answer = {"total": 0, "mediaItems": []}
    with pytest.raises(HTTPException) as answer:
        await routes.get_track_radio(uri=TRACK_URI, source=world.source)
    assert answer.value.status_code == 404


async def test_a_radio_answer_of_another_shape_is_no_radio_not_a_crash(world):
    """An unhandled exception would answer 500; the browser leaves out an entry
    for a 404 and shows nothing broken."""
    for shape in ({"mediaItems": {"uri": "spotify:playlist:x"}}, {"mediaItems": ["spotify:playlist:x"]}, []):
        world.daemon.radio_answer = shape
        with pytest.raises(HTTPException) as answer:
            await routes.get_track_radio(uri=TRACK_URI, source=world.source)
        assert answer.value.status_code == 404


async def test_a_radio_service_that_fails_answers_503(world):
    world.daemon.radio_answer = None
    with pytest.raises(HTTPException) as answer:
        await routes.get_track_radio(uri=TRACK_URI, source=world.source)
    assert answer.value.status_code == 503


ARTIST_URI = "spotify:artist:6nB0iY1cjSY1KyhYyuIIKH"
LATEST = "spotify:album:2TZNyzwG89VKnROkBaK7w3"


def artist_section(section, title, uri, name):
    return [
        {"id": section, "component": {"id": "glue2:sectionHeader", "category": "row"}, "text": {"title": title}},
        {"id": f"{section}_row0", "component": {"id": "freetier:largerRow", "category": "row"},
         "text": {"title": name, "subtitle": "2026"}, "events": {"click": {"name": "navigate", "data": {"uri": uri}}}},
    ]


ARTIST_PAGE = {"header": {"text": {"title": "FKA twigs"}}, "body": [
    *artist_section("artist-entity-view-releases", "Sorties populaires", LATEST, "On Your Mind"),
]}
DISCOGRAPHY = {"body": [
    *artist_section("artist-entity-view-latest-release", "Dernière sortie", LATEST, "On Your Mind"),
    *artist_section("artist-entity-view-albums-source", "Albums", "spotify:album:25PQxi9SR1OODB5XG6m48J", "LP1"),
    *artist_section("artist-entity-view-artist-singles-source", "Singles",
                    "spotify:album:44keDDETPLFK48WCykPKit", "EP1"),
]}


def release(tracks, minutes):
    return [{"uri": f"spotify:track:{n}", "track": {"uri": f"spotify:track:{n}", "name": str(n),
                                                     "duration": minutes * 60_000}} for n in range(tracks)]


def groups_of(answer):
    section = next(s for s in answer["sections"] if "groups" in s)
    return {g["id"]: [(r["name"], r["release_type"]) for r in g["items"]] for g in section["groups"]}


async def test_the_artist_route_reads_the_page_and_its_discography_in_the_language_asked_for(world):
    world.daemon.artist_answer = ARTIST_PAGE
    world.daemon.releases_answer = DISCOGRAPHY
    world.daemon.listings[LATEST] = release(1, 4)
    answer = await routes.get_artist(uri=ARTIST_URI, locale="fr-FR", source=world.source)

    assert sorted(world.daemon.artist_asked) == [
        ("https://spclient.wg.spotify.com/artistview/v1/artist/6nB0iY1cjSY1KyhYyuIIKH", "fr-FR"),
        ("https://spclient.wg.spotify.com/artistview/v1/artist/6nB0iY1cjSY1KyhYyuIIKH/releases", "fr-FR"),
    ]
    assert answer["name"] == "FKA twigs"


async def test_a_latest_release_is_filed_by_its_tracks_as_spotify_files_it(world):
    """Spotify leaves its latest release out of its own list: read from the
    daemon, nine tracks are an album, one track a single."""
    world.daemon.artist_answer = ARTIST_PAGE
    world.daemon.releases_answer = DISCOGRAPHY

    world.daemon.listings[LATEST] = release(9, 4)
    album = groups_of(await routes.get_artist(uri=ARTIST_URI, locale="fr", source=world.source))
    world.daemon.listings[LATEST] = release(1, 4)
    single = groups_of(await routes.get_artist(uri=ARTIST_URI, locale="fr", source=world.source))

    assert album["albums"] == [("On Your Mind", "album"), ("LP1", "album")]
    assert album["popular"] == [("On Your Mind", "album")]
    assert single["singles"] == [("On Your Mind", "single"), ("EP1", "single")]


async def test_a_latest_release_not_read_in_time_leaves_its_kind_unsaid_and_the_page_whole(world, caplog):
    world.daemon.artist_answer = ARTIST_PAGE
    world.daemon.releases_answer = DISCOGRAPHY
    world.daemon.listings[LATEST] = release(1, 4)
    world.daemon.listing_calls_until_ready = 1000
    caplog.set_level(logging.WARNING)
    groups = groups_of(await routes.get_artist(uri=ARTIST_URI, locale="fr", source=world.source))

    assert groups["popular"] == [("On Your Mind", None)]
    assert groups["albums"] == [("LP1", "album")] and groups["singles"] == [("EP1", "single")]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_a_discography_spotify_will_not_serve_leaves_the_artist_page_its_popular_releases(world, caplog):
    world.daemon.artist_answer = ARTIST_PAGE
    world.daemon.releases_answer = None
    caplog.set_level(logging.WARNING)
    groups = groups_of(await routes.get_artist(uri=ARTIST_URI, locale="fr-FR", source=world.source))

    assert groups == {"popular": [("On Your Mind", None)]}
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_an_artist_page_spotify_will_not_serve_answers_503(world):
    world.daemon.artist_answer = None
    with pytest.raises(HTTPException) as answer:
        await routes.get_artist(uri=ARTIST_URI, locale="fr-FR", source=world.source)
    assert answer.value.status_code == 503


async def test_a_home_spotify_will_not_serve_still_answers_the_library(world, caplog):
    """Spotify's home is a service on the internet, not the daemon: its
    failure costs the shelves, never the account's own playlists."""
    world.daemon.home_answer = None
    world.daemon.playlists = [
        {"uri": "spotify:playlist:1mine", "name": "Chill appart", "description": "",
         "owner_username": ACCOUNT, "length": 50, "collaborative": False, "can_edit": True,
         "folder": [], "image_url": "https://i.scdn.co/image/x"},
    ]
    caplog.set_level(logging.WARNING)
    answer = await routes.get_home(source=world.source)

    assert answer["shelves"] == [] and answer["shortcuts"] == []
    assert [p["name"] for p in answer["playlists"]["mine"]] == ["Chill appart"]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_a_listing_still_loading_answers_its_progress(world):
    """The browser asks again while `ready` is false: the route must never
    hold a request past its wait, nor hand over a partial list as complete."""
    world.daemon.listings[PLAYLIST] = [
        {"uri": "spotify:track:a", "track": {"uri": "spotify:track:a", "name": "A", "artist_names": ["X"],
                                             "artist_uris": ["spotify:artist:x"], "duration": 1000}},
    ]
    world.daemon.listing_calls_until_ready = 1000
    pending = await routes.get_context(PLAYLIST, source=world.source)
    assert pending["complete"] is False and pending["tracks"] == []

    world.daemon.listing_calls_until_ready = 0
    ready = await routes.get_context(PLAYLIST, source=world.source)
    assert ready["complete"] is True and [t["title"] for t in ready["tracks"]] == ["A"]


def track(name):
    return {"uri": f"spotify:track:{name}",
            "track": {"uri": f"spotify:track:{name}", "name": name, "artist_names": [], "artist_uris": []}}


async def test_a_long_listing_is_handed_over_as_it_is_described(world):
    """A cold Liked Songs takes go-librespot 12 s to describe whole and one
    for its first 100 tracks: waiting for all of them is what kept the page
    empty. Each answer carries the tracks described past what the browser
    has, in order, until the listing is complete."""
    world.daemon.listings[PLAYLIST] = [track(n) for n in "abcde"]
    world.daemon.described_per_call = 2

    first = await routes.get_context(PLAYLIST, after=0, source=world.source)
    second = await routes.get_context(PLAYLIST, after=2, source=world.source)
    last = await routes.get_context(PLAYLIST, after=4, source=world.source)

    assert [([t["title"] for t in a["tracks"]], a["complete"]) for a in (first, second, last)] == [
        (["a", "b"], False), (["c", "d"], False), (["e"], True),
    ]
    assert world.daemon.listing_calls[PLAYLIST] == 3


async def test_a_listing_read_again_from_the_start_takes_no_rows_back(world, monkeypatch):
    """go-librespot restarted reads a half-loaded Liked Songs from nothing:
    the browser still holds 3 rows and must keep them, not be told to start
    over with fewer."""
    from backend.sources.spotify.library import SpotifyLibrary

    monkeypatch.setattr(SpotifyLibrary, "CONTEXT_WAIT_S", 0.1)
    monkeypatch.setattr(SpotifyLibrary, "CONTEXT_POLL_S", 0.01)
    world.daemon.listings[PLAYLIST] = [track(n) for n in "abcde"]
    world.daemon.described_per_call = 0

    answer = await routes.get_context(PLAYLIST, after=3, source=world.source)

    assert answer["tracks"] == [] and answer["complete"] is False


async def test_a_track_not_described_yet_holds_back_the_ones_after_it(world):
    """A track the cache already holds further down (the one playing) must
    not show above the gap: the rows on screen would move when it fills."""
    world.daemon.listings[PLAYLIST] = [track("a"), {"uri": "spotify:track:gap", "track": None}, track("c")]
    world.daemon.listing_calls_until_ready = 1
    world.daemon.described_per_call = None

    answer = await routes.get_context(PLAYLIST, after=0, source=world.source)

    assert [t["title"] for t in answer["tracks"]] == ["a"]


async def test_a_listing_whose_cache_stops_short_is_shown_with_what_it_has(world):
    """A track Spotify will not describe keeps `cached` below `length` for good:
    the listing must still be handed over once the count stops moving, not
    asked for forever."""
    world.daemon.listings[PLAYLIST] = [
        {"uri": "spotify:track:a", "track": {"uri": "spotify:track:a", "name": "A", "artist_names": ["X"],
                                             "artist_uris": [], "duration": 1000}},
        {"uri": "spotify:track:gone", "track": None},
    ]
    first = await routes.get_context(PLAYLIST, source=world.source)
    rest = await routes.get_context(PLAYLIST, after=len(first["tracks"]), source=world.source)

    assert [t["title"] for t in first["tracks"]] == ["A"]
    assert rest["complete"] is True and rest["tracks"] == []


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
    assert listing["complete"] is False
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


async def test_a_page_asked_while_the_session_is_rebuilt_waits_for_it(world, monkeypatch):
    """Playback transferred to another device makes go-librespot sign in
    again (~1 s, measured), answering 204 meanwhile — /token included: answered
    409 at once, the artist page opened at that moment showed no popular
    tracks."""
    import asyncio

    artist = "spotify:artist:3b5bg1k6N9u31OtzSfK2dP"
    world.daemon.listings[artist] = [track(n) for n in "ab"]
    world.daemon.session = world.daemon.signed_in = False
    request, refused = world.daemon.request, []

    def rebuilding(method, url, **kwargs):
        if not world.daemon.signed_in:
            refused.append(url)
            world.daemon.signed_in = len(refused) >= 4
        return request(method, url, **kwargs)

    monkeypatch.setattr(world.daemon, "request", rebuilding)

    listing, page = await asyncio.gather(
        routes.get_context(artist, source=world.source),
        routes.get_artist(artist, source=world.source),
    )

    assert [t["title"] for t in listing["tracks"]] == ["a", "b"] and listing["complete"] is True
    assert page["status"] == "success" and world.daemon.token_reads >= 1
    assert any("/token" in url for url in refused) and any("/context/tracks" in url for url in refused)


async def test_a_session_that_never_comes_back_answers_409(world):
    """Waiting for a session must end: the browser reads 'signing in'."""
    world.daemon.listings[PLAYLIST] = [track("a")]
    world.daemon.session = world.daemon.signed_in = False

    with pytest.raises(HTTPException) as answer:
        await routes.get_context(PLAYLIST, source=world.source)
    assert answer.value.status_code == 409


async def test_with_nobody_signed_in_a_listing_is_refused_without_waiting_on_the_daemon(monkeypatch, tmp_path):
    """The daemon's 204 is waited out as a session being rebuilt: asked with
    nobody signed in, every listing and cover would hang that long first."""
    signed_out = SpotifyWorld(monkeypatch, tmp_path)
    await signed_out.select()
    await signed_out.idle()
    asked = []
    monkeypatch.setattr(signed_out.daemon, "request", lambda *a, **k: asked.append(a))
    try:
        assert signed_out.source.account is None
        for route in (routes.get_context, routes.get_context_cover):
            with pytest.raises(HTTPException) as answer:
                await route(PLAYLIST, source=signed_out.source)
            assert answer.value.status_code == 409
        assert asked == []
    finally:
        await signed_out.source.shutdown()


async def test_the_library_answers_409_when_spotify_is_not_running(world):
    await world.leave()
    with pytest.raises(HTTPException) as answer:
        await routes.get_context(PLAYLIST, source=world.source)
    assert answer.value.status_code == 409


async def test_no_route_or_push_answers_a_credential(world):
    """The blob is reusable from any device: it must never leave the backend."""
    answer = await routes.get_profiles(source=world.source)
    assert "c3RvcmVkLWJsb2I=" not in json.dumps(answer)
    assert answer["profiles"][0]["username"] == ACCOUNT
    assert profile_pushes(world)
    assert "c3RvcmVkLWJsb2I=" not in json.dumps(profile_pushes(world))


def _recording(calls, name, real, world):
    async def record(*args, **kwargs):
        calls.append((name, world.stored_account()))
        return await real(*args, **kwargs)
    return record


# Keep os imported for the mode checks some environments need.
_ = os
