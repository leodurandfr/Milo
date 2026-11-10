"""The browser's catalog search: go-librespot's /search/{type} and
/search/suggestions (a build with search, search.enabled), as
tests/spotify_world.py models them, shaped by catalog.py for the search page.

What is asserted is what Milō asked of the daemon and what the browser is
given: a track it can play and whose ⋯ menu leads somewhere, cards that open
the right page, and a daemon that cannot search read as "not available", never
as a fault. All of it shows only on a unit whose daemon was built with search.
"""
import pytest
from fastapi import HTTPException

from backend.sources.spotify import routes
from backend.sources.spotify.catalog import search_card, search_image, search_results, search_track
from backend.tests.spotify_world import ACCOUNT, SpotifyWorld

COVER_640 = "https://i.scdn.co/image/ab67616d0000b2735a3a1c3a1c3a1c3a1c3a1c3a"
COVER_300 = "https://i.scdn.co/image/ab67616d00001e025a3a1c3a1c3a1c3a1c3a1c3a"
COVER_64 = "https://i.scdn.co/image/ab67616d000048515a3a1c3a1c3a1c3a1c3a1c3a"
ALBUM_IMAGES = [
    {"url": COVER_300, "width": 300, "height": 300},
    {"url": COVER_64, "width": 64, "height": 64},
    {"url": COVER_640, "width": 640, "height": 640},
]


def entity(kind, uri, name, **fields):
    """A result as go-librespot's API serialises it: every field present."""
    return {"type": kind, "uri": uri, "name": name, "images": [], "artists": [], "album": None,
            "duration_ms": 0, "owner": None, "explicit": False, "playable": None, **fields}


def page(*items):
    return {"total": len(items), "offset": 0, "limit": 10, "next_offset": None, "items": list(items)}


DA_FUNK = entity(
    "track", "spotify:track:0MyY4WcN7DIfbSmp5yej5z", "Da Funk", images=ALBUM_IMAGES,
    artists=[{"uri": "spotify:artist:4tZwfgrHOc3mvqYlEYSvVi", "name": "Daft Punk"}],
    album={"uri": "spotify:album:5uRdvUR7xCnHmUW8n64n9y", "name": "Homework"},
    duration_ms=328600, explicit=False, playable=True,
)
DAFT_PUNK = entity("artist", "spotify:artist:4tZwfgrHOc3mvqYlEYSvVi", "Daft Punk",
                   images=[{"url": "https://i.scdn.co/image/ab6761610000e5eb", "width": 640, "height": 640},
                           {"url": "https://i.scdn.co/image/ab676161000051745", "width": 320, "height": 320}])
HOMEWORK = entity("album", "spotify:album:5uRdvUR7xCnHmUW8n64n9y", "Homework", images=ALBUM_IMAGES,
                  artists=[{"uri": "spotify:artist:4tZwfgrHOc3mvqYlEYSvVi", "name": "Daft Punk"},
                           {"uri": "spotify:artist:1", "name": "Guest"}])
THIS_IS = entity("playlist", "spotify:playlist:37i9dQZF1DZ06evO1rtgpL", "This Is Daft Punk",
                 images=[{"url": "https://i.scdn.co/image/ab67706f", "width": 0, "height": 0}],
                 owner={"uri": "spotify:user:spotify", "name": "Spotify"})


# === Shaping (pure) ===

def test_a_card_is_drawn_from_the_smallest_picture_at_least_as_wide_as_a_card():
    assert search_image(ALBUM_IMAGES) == COVER_300
    assert search_image(ALBUM_IMAGES, width=None) == COVER_640
    assert search_image([{"url": COVER_64, "width": 64, "height": 64}]) == COVER_64, "too small is still a picture"
    assert search_image([{"url": "https://x/unsized", "width": 0, "height": 0}, {"url": COVER_64, "width": 64}]) \
        == "https://x/unsized", "an unsized picture counts as the widest"
    assert search_image([]) is None


def test_a_track_result_is_a_listing_row_with_its_album_and_a_thumbnail():
    """The row SpotifyTrackMenu and TrackRow read on every other page: a
    missing key there is a menu that leads nowhere or a row with no cover."""
    row = search_track(DA_FUNK)
    assert row == {
        "uri": DA_FUNK["uri"],
        "listed_uri": DA_FUNK["uri"],
        "title": "Da Funk",
        "artists": [{"name": "Daft Punk", "uri": "spotify:artist:4tZwfgrHOc3mvqYlEYSvVi"}],
        "album": {"name": "Homework", "uri": "spotify:album:5uRdvUR7xCnHmUW8n64n9y"},
        "artwork": COVER_640,
        "thumbnail": COVER_64,
        "duration_ms": 328600,
        "explicit": False,
    }


def test_each_card_says_what_it_opens_and_its_byline():
    assert search_card(DAFT_PUNK) == {
        "uri": DAFT_PUNK["uri"], "kind": "artist", "name": "Daft Punk", "subtitle": None,
        "image": "https://i.scdn.co/image/ab676161000051745",
    }
    assert search_card(HOMEWORK)["subtitle"] == "Daft Punk, Guest"
    playlist = search_card(THIS_IS)
    assert (playlist["subtitle"], playlist["owner"]) == ("Spotify", "spotify"), \
        "the page tells Spotify's own playlists apart by the owner's username"


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
    world.daemon.search_pages = {"track": page(DA_FUNK), "artist": page(DAFT_PUNK),
                                 "album": page(HOMEWORK), "playlist": page(THIS_IS)}

    answer = await routes.search(q="  daft punk ", source=world.source)

    assert sorted(world.daemon.searches) == sorted(
        (f"/search/{kind}", {"q": "daft punk", "limit": routes.SEARCH_LIMIT})
        for kind in ("track", "artist", "album", "playlist")
    ), "trimmed, and no kind the browser has no page for"
    assert answer["status"] == "success"
    assert [t["title"] for t in answer["tracks"]] == ["Da Funk"]
    assert [(c["kind"], c["name"]) for c in answer["artists"] + answer["albums"] + answer["playlists"]] == [
        ("artist", "Daft Punk"), ("album", "Homework"), ("playlist", "This Is Daft Punk"),
    ]


async def test_a_kind_spotify_failed_this_time_costs_only_its_section(world):
    """Measured live: pathfinder failed one operation (searchArtists) of a
    search, once; the page lost to it showed nothing at all."""
    world.daemon.search_pages = {"track": page(DA_FUNK), "album": page(HOMEWORK), "playlist": page(THIS_IS)}
    world.daemon.search_refused = {"artist": 502}

    answer = await routes.search(q="daft punk", source=world.source)

    assert answer["artists"] == []
    assert [t["title"] for t in answer["tracks"]] == ["Da Funk"]
    assert [a["name"] for a in answer["albums"] + answer["playlists"]] == ["Homework", "This Is Daft Punk"]


async def test_a_kind_that_did_not_come_back_in_time_costs_only_its_section(world):
    """A slow network: one kind's request times out. Read as "Spotify is not
    reachable" for the whole search, it showed "signing in" for good."""
    world.daemon.search_pages = {"track": page(DA_FUNK), "artist": page(DAFT_PUNK)}
    world.daemon.search_times_out = {"album"}

    answer = await routes.search(q="daft punk", source=world.source)

    assert answer["albums"] == []
    assert [a["name"] for a in answer["artists"]] == ["Daft Punk"]


async def test_a_search_failed_for_every_kind_is_a_failure(world):
    world.daemon.search_refused = {kind: 502 for kind in ("track", "artist", "album", "playlist")}
    with pytest.raises(HTTPException) as raised:
        await routes.search(q="daft punk", source=world.source)
    assert raised.value.status_code == 503


async def test_every_kind_timing_out_is_a_failure_not_signing_in(world):
    """Read as "Spotify is not reachable", it answered 409, which the page
    draws as "signing in" for an account that is signed in."""
    world.daemon.search_times_out = {"track", "artist", "album", "playlist"}
    with pytest.raises(HTTPException) as raised:
        await routes.search(q="daft punk", source=world.source)
    assert raised.value.status_code == 503


async def test_a_rate_limit_on_one_kind_costs_only_its_section(world):
    """The four go out at once: Spotify may limit the last one only."""
    world.daemon.search_pages = {"track": page(DA_FUNK), "album": page(HOMEWORK), "playlist": page(THIS_IS)}
    world.daemon.search_refused = {"artist": 429}

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
    """502: an outdated query hash, a refused client or token."""
    world.daemon.search_answers = 502
    with pytest.raises(HTTPException) as raised:
        await routes.search(q="daft punk", source=world.source)
    assert raised.value.status_code == 503


async def test_suggestions_are_the_completed_queries(world):
    world.daemon.suggestions = {"query": "daft p", "completions": ["daft punk", "daft punk tron"],
                                "items": [DAFT_PUNK]}

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
