"""The browser's catalog search: go-librespot's /search/{tracks,artists,albums,
playlists} and /search/suggestions (a build with search, search.enabled), as
tests/spotify_world.py models them, shaped by catalog.py for the search page.

What is asserted is what Milō asked of the daemon and what the browser is
given: a track it can play and whose ⋯ menu leads somewhere, cards that open
the right page, and a daemon that cannot search read as "not available", never
as a fault. All of it shows only on a unit whose daemon was built with search.
"""
import pytest
from fastapi import HTTPException

from backend.sources.spotify import routes
from backend.sources.spotify.catalog import search_playlist, search_results, search_track
from backend.tests.spotify_world import ACCOUNT, SpotifyWorld

COVER_640 = "https://i.scdn.co/image/ab67616d0000b2735a3a1c3a1c3a1c3a1c3a1c3a"
COVER_300 = "https://i.scdn.co/image/ab67616d00001e025a3a1c3a1c3a1c3a1c3a1c3a"
COVER_64 = "https://i.scdn.co/image/ab67616d000048515a3a1c3a1c3a1c3a1c3a1c3a"
PORTRAIT = "https://i.scdn.co/image/ab6761610000e5eb1a2b3c4d5e6f"


def page(*items):
    """One page as go-librespot's /search/{kind}s answers it."""
    return {"total": len(items), "offset": 0, "limit": 10, "items": list(items)}


# The shapes of go-librespot's api-spec.yml: searchTrack, libraryArtist,
# libraryAlbum and searchPlaylist, every required field present.
DA_FUNK = {
    "uri": "spotify:track:0MyY4WcN7DIfbSmp5yej5z", "name": "Da Funk",
    "artist_names": ["Daft Punk"], "artist_uris": ["spotify:artist:4tZwfgrHOc3mvqYlEYSvVi"],
    "album_name": "Homework", "album_uri": "spotify:album:5uRdvUR7xCnHmUW8n64n9y",
    "album_cover_url": COVER_640, "duration": 328600,
}
DAFT_PUNK = {"uri": "spotify:artist:4tZwfgrHOc3mvqYlEYSvVi", "name": "Daft Punk", "image_url": PORTRAIT}
HOMEWORK = {"uri": "spotify:album:5uRdvUR7xCnHmUW8n64n9y", "name": "Homework",
            "artist_names": ["Daft Punk", "Guest"], "year": 1997, "image_url": COVER_640}
THIS_IS = {"uri": "spotify:playlist:37i9dQZF1DZ06evO1rtgpL", "name": "This Is Daft Punk", "description": "",
           "owner_username": "spotify", "image_url": "https://i.scdn.co/image/ab67706f"}


# === Shaping (pure) ===

def test_a_track_result_is_a_listing_row_with_its_album_and_a_thumbnail():
    """The row SpotifyTrackMenu and TrackRow read on every other page: a
    missing key there is a menu that leads nowhere or a row with no cover."""
    assert search_track(DA_FUNK) == {
        "uri": DA_FUNK["uri"],
        "title": "Da Funk",
        "artists": [{"name": "Daft Punk", "uri": "spotify:artist:4tZwfgrHOc3mvqYlEYSvVi"}],
        "album": {"name": "Homework", "uri": "spotify:album:5uRdvUR7xCnHmUW8n64n9y"},
        "artwork": COVER_640,
        "thumbnail": COVER_64,
        "duration_ms": 328600,
    }


def test_each_card_says_what_it_opens_and_its_byline():
    shaped = search_results("daft punk", {
        "track": page(), "artist": page(DAFT_PUNK), "album": page(HOMEWORK), "playlist": page(THIS_IS),
    })
    assert shaped["artists"] == [{"uri": DAFT_PUNK["uri"], "kind": "artist", "name": "Daft Punk",
                                  "subtitle": None, "image": PORTRAIT}]
    assert [(a["kind"], a["subtitle"], a["image"]) for a in shaped["albums"]] == [
        ("album", "Daft Punk, Guest", COVER_300)], "the card's 300 px cover, not the 640 px one"
    playlist = shaped["playlists"][0]
    assert (playlist["kind"], playlist["subtitle"], playlist["owner"]) == ("playlist", "Spotify", "spotify"), \
        "the page tells Spotify's own playlists apart by the owner's username"


def test_a_playlist_someone_else_owns_has_no_byline():
    """go-librespot names a playlist's owner by username only, which reads as
    an id for most accounts: no byline, as for a library playlist."""
    card = search_playlist({**THIS_IS, "owner_username": "31l2k4j5h6g7f8d9s0a", "image_url": None})
    assert (card["subtitle"], card["owner"], card["image"]) == (None, "31l2k4j5h6g7f8d9s0a", None)


def test_a_result_spotify_repeats_in_a_page_is_listed_once():
    shaped = search_results("daft punk", {
        "track": page(DA_FUNK, DA_FUNK), "artist": page(DAFT_PUNK), "album": page(), "playlist": page(THIS_IS),
    })
    assert [t["uri"] for t in shaped["tracks"]] == [DA_FUNK["uri"]]
    assert [a["uri"] for a in shaped["artists"]] == [DAFT_PUNK["uri"]]
    assert shaped["albums"] == []
    assert shaped["query"] == "daft punk"


# === The routes, over the daemon ===

@pytest.fixture
async def world(monkeypatch, tmp_path):
    w = SpotifyWorld(monkeypatch, tmp_path, stored=ACCOUNT)
    await w.select()
    await w.idle()
    yield w
    await w.source.shutdown()


async def test_one_search_asks_each_kind_the_browser_opens_once(world):
    world.daemon.search_pages = {"tracks": page(DA_FUNK), "artists": page(DAFT_PUNK),
                                 "albums": page(HOMEWORK), "playlists": page(THIS_IS)}

    answer = await routes.search(q="  daft punk ", source=world.source)

    assert sorted(world.daemon.searches) == sorted(
        (f"/search/{kind}", {"q": "daft punk", "limit": routes.SEARCH_LIMIT})
        for kind in ("tracks", "artists", "albums", "playlists")
    ), "trimmed, and no kind the browser has no page for"
    assert answer["status"] == "success"
    assert answer["incomplete"] == []
    assert [t["title"] for t in answer["tracks"]] == ["Da Funk"]
    assert [(c["kind"], c["name"]) for c in answer["artists"] + answer["albums"] + answer["playlists"]] == [
        ("artist", "Daft Punk"), ("album", "Homework"), ("playlist", "This Is Daft Punk"),
    ]


async def test_a_kind_spotify_failed_this_time_costs_only_its_section(world):
    """Measured live: pathfinder failed one operation (searchArtists) of a
    search, once; the page lost to it showed nothing at all."""
    world.daemon.search_pages = {"tracks": page(DA_FUNK), "albums": page(HOMEWORK), "playlists": page(THIS_IS)}
    world.daemon.search_refused = {"artists": 500}

    answer = await routes.search(q="daft punk", source=world.source)

    assert answer["artists"] == []
    assert answer["incomplete"] == ["artist"], "the browser may ask the same search again"
    assert [t["title"] for t in answer["tracks"]] == ["Da Funk"]
    assert [a["name"] for a in answer["albums"] + answer["playlists"]] == ["Homework", "This Is Daft Punk"]


async def test_a_kind_that_did_not_come_back_in_time_costs_only_its_section(world):
    """A slow network: one kind's request times out. Read as "Spotify is not
    reachable" for the whole search, it showed "signing in" for good."""
    world.daemon.search_pages = {"tracks": page(DA_FUNK), "artists": page(DAFT_PUNK)}
    world.daemon.search_times_out = {"albums"}

    answer = await routes.search(q="daft punk", source=world.source)

    assert answer["albums"] == []
    assert [a["name"] for a in answer["artists"]] == ["Daft Punk"]


async def test_a_search_failed_for_every_kind_is_a_failure(world):
    world.daemon.search_refused = {kind: 500 for kind in ("tracks", "artists", "albums", "playlists")}
    with pytest.raises(HTTPException) as raised:
        await routes.search(q="daft punk", source=world.source)
    assert raised.value.status_code == 503


async def test_every_kind_unreachable_is_the_daemons_state(world):
    """The daemon restarting under the search (a profile switch), or every
    request lost: the page draws it as signing in, with its retry, as every
    other Spotify page does."""
    world.daemon.search_times_out = {"tracks", "artists", "albums", "playlists"}
    with pytest.raises(HTTPException) as raised:
        await routes.search(q="daft punk", source=world.source)
    assert raised.value.status_code == 409


async def test_an_answer_that_does_not_read_costs_only_its_section(world):
    world.daemon.search_pages = {"tracks": page(DA_FUNK)}
    world.daemon.search_garbled = {"albums"}

    answer = await routes.search(q="daft punk", source=world.source)

    assert [t["title"] for t in answer["tracks"]] == ["Da Funk"]
    assert answer["incomplete"] == ["album"]


async def test_a_rate_limit_on_one_kind_costs_only_its_section(world):
    """The four go out at once: Spotify may limit the last one only."""
    world.daemon.search_pages = {"tracks": page(DA_FUNK), "albums": page(HOMEWORK), "playlists": page(THIS_IS)}
    world.daemon.search_refused = {"artists": 429}

    answer = await routes.search(q="daft punk", source=world.source)

    assert answer["artists"] == []
    assert [t["title"] for t in answer["tracks"]] == ["Da Funk"]


async def test_a_daemon_that_cannot_search_answers_not_available(world):
    """A go-librespot without search (an upstream build, or search off)
    answers 404: the browser says search is not available, not that Spotify
    failed."""
    world.daemon.search_answers = 404
    with pytest.raises(HTTPException) as raised:
        await routes.search(q="daft punk", source=world.source)
    assert raised.value.status_code == 501

    with pytest.raises(HTTPException) as raised:
        await routes.search_suggestions(q="daft", source=world.source)
    assert raised.value.status_code == 501


async def test_a_rate_limit_reaches_the_browser_as_one(world):
    world.daemon.search_answers = 429
    with pytest.raises(HTTPException) as raised:
        await routes.search(q="daft punk", source=world.source)
    assert raised.value.status_code == 429


async def test_spotify_refusing_the_search_is_a_failure(world):
    """500: an outdated query hash, a refused client or token."""
    world.daemon.search_answers = 500
    with pytest.raises(HTTPException) as raised:
        await routes.search(q="daft punk", source=world.source)
    assert raised.value.status_code == 503


async def test_suggestions_are_the_completed_queries(world):
    world.daemon.suggestions = {**world.daemon.suggestions, "queries": ["daft punk", "daft punk tron"],
                                "artists": [DAFT_PUNK]}

    answer = await routes.search_suggestions(q="daft p ", source=world.source)

    assert world.daemon.searches == [("/search/suggestions", {"q": "daft p", "limit": routes.SUGGESTIONS_LIMIT})]
    assert answer == {"status": "success", "query": "daft p", "completions": ["daft punk", "daft punk tron"]}


async def test_nobody_signed_in_is_the_signing_in_state(monkeypatch, tmp_path):
    """No account stored, nobody cast: 409, which the browser draws as
    "signing in", and no search is sent."""
    w = SpotifyWorld(monkeypatch, tmp_path)
    await w.select()
    await w.idle()
    try:
        assert w.source.account is None
        with pytest.raises(HTTPException) as raised:
            await routes.search(q="daft punk", source=w.source)
        assert raised.value.status_code == 409
        assert w.daemon.searches == []
    finally:
        await w.source.shutdown()
