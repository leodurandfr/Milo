"""The account's saved albums and followed artists on the Spotify home:
go-librespot's /library/albums and /library/artists (0.10.6), shaped by
catalog.py into the cards SpotifyHome draws as two shelves, each opening its
whole list.

The item shapes are the daemon's, measured on the owner's account
(2026-11-16): an album `{uri, name, artist_names, year, image_url}` with the
640 px cover, an artist `{uri, name, image_url}` with the 320 px photo.
"""
import logging

import pytest

from backend.sources.spotify import routes
from backend.sources.spotify.catalog import followed_artists, saved_albums
from backend.sources.spotify.library import PAGE
from backend.tests.spotify_world import ACCOUNT, SpotifyWorld

COVER_ID = "2c5eaf7cc989577b041f4fa0"
VIREE = {
    "uri": "spotify:album:4mJcxpyPjnUm023GqSRcFU", "name": "Virée Nocturne", "artist_names": ["Soless"],
    "year": 2026, "image_url": f"https://i.scdn.co/image/ab67616d0000b273{COVER_ID}",
}
BITUME = {
    "uri": "spotify:album:0FSbZ6phAPA7qhDqSSpiEH", "name": "Bitume Caviar (vol.2)",
    "artist_names": ["Limsa d'Aulnay", "ISHA"], "year": 2025, "image_url": None,
}
UNDESCRIBED_ALBUM = {"uri": "spotify:album:0000000000000000000000", "name": "", "artist_names": [],
                     "year": 0, "image_url": None}
VEGYN = {"uri": "spotify:artist:5iUnvXddCpOrbWKm7QMr6o", "name": "Vegyn",
         "image_url": "https://i.scdn.co/image/ab6761610000e5eb9e782711ee76bb0df5ad15c3"}


def artist(n):
    return {"uri": f"spotify:artist:{n:022d}", "name": f"Artist {n}", "image_url": None}


# === Shaping (pure) ===

def test_a_saved_album_is_a_card_that_opens_its_album_bylined_by_its_artists():
    """The card SpotifyCard draws and openItem routes by `kind`; the cover is
    the 300 px one a card is drawn from, never the 640 px the library names."""
    assert saved_albums([VIREE, BITUME]) == [
        {"uri": VIREE["uri"], "kind": "album", "name": "Virée Nocturne", "subtitle": "Soless",
         "image": f"https://i.scdn.co/image/ab67616d00001e02{COVER_ID}"},
        {"uri": BITUME["uri"], "kind": "album", "name": "Bitume Caviar (vol.2)",
         "subtitle": "Limsa d'Aulnay, ISHA", "image": None},
    ]


def test_a_followed_artist_is_a_card_that_opens_the_artists_page():
    assert followed_artists([VEGYN]) == [
        {"uri": VEGYN["uri"], "kind": "artist", "name": "Vegyn", "subtitle": None, "image": VEGYN["image_url"]},
    ]


def test_an_entry_the_daemon_could_not_describe_is_left_out():
    """go-librespot names an entry "" when its metadata could not be read:
    a card with no title opening a page with none."""
    assert [a["uri"] for a in saved_albums([UNDESCRIBED_ALBUM, VIREE])] == [VIREE["uri"]]
    assert followed_artists([{**VEGYN, "name": ""}]) == []


# === The home, over the daemon ===

@pytest.fixture
async def world(monkeypatch, tmp_path):
    w = SpotifyWorld(monkeypatch, tmp_path, stored=ACCOUNT)
    await w.select()
    await w.idle()
    yield w
    await w.source.shutdown()


async def test_the_home_carries_every_saved_album_and_followed_artist_in_the_daemons_order(world):
    """The shelf shows the first ones and its "Show all" page the whole list,
    both from this one answer: a list longer than a page is read to its end."""
    world.daemon.saved_albums = [VIREE, UNDESCRIBED_ALBUM, BITUME]
    world.daemon.followed_artists = [artist(n) for n in range(PAGE + 20)]

    home = await routes.get_home(locale="fr", source=world.source)

    assert [a["name"] for a in home["albums"]] == ["Virée Nocturne", "Bitume Caviar (vol.2)"]
    assert [a["name"] for a in home["artists"]] == [f"Artist {n}" for n in range(PAGE + 20)]


async def test_a_daemon_without_the_listings_still_answers_the_home(world, caplog):
    """A go-librespot before 0.10.6 answers both 404: the home loses the two
    shelves, never its playlists — a refused listing taken as the home's
    failure was a page saying the library is unavailable. And it is a version,
    not a fault: nothing at warning, which would put it in the error banner on
    every home load."""
    world.daemon.saved_albums = None
    world.daemon.followed_artists = None
    world.daemon.playlists = [{
        "uri": "spotify:playlist:1mineabcdefgh", "name": "Chill appart", "description": "",
        "owner_username": ACCOUNT, "length": 12, "image_url": None, "can_edit": False,
        "collaborative": False, "folder": [],
    }]

    home = await routes.get_home(locale="fr", source=world.source)

    assert home["status"] == "success"
    assert (home["albums"], home["artists"]) == ([], [])
    assert [p["name"] for p in home["playlists"]["mine"]] == ["Chill appart"]
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING and "Spotify home" in r.getMessage()]


async def test_a_listing_that_does_not_come_back_in_time_costs_only_its_shelf(world):
    """A long library read cold (go-librespot describes every page it lists)
    past the client's timeout: read as the daemon being gone, it took the whole
    home down to "signing in"."""
    world.daemon.saved_albums = [VIREE]
    world.daemon.followed_artists = [VEGYN]
    world.daemon.listings_time_out = {"/library/albums"}

    home = await routes.get_home(locale="fr", source=world.source)

    assert home["albums"] == []
    assert [a["name"] for a in home["artists"]] == ["Vegyn"]
