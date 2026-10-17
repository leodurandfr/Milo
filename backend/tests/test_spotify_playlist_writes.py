"""The account's playlists and Liked Songs, written from the browser's ⋯ menu
(go-librespot 0.10.5's library writes, as tests/spotify_world.py models them).

What is asserted is what Milō asked of the daemon — which playlist, which
entry — and what the routes answer: a removal that names the wrong copy of a
track, or a refusal that reaches the browser as a 503 instead of "not yours to
edit", shows only on a unit with a real library.
"""
import pytest
from fastapi import HTTPException

from backend.sources.spotify import routes
from backend.sources.spotify.models import AddTracksRequest
from backend.tests.spotify_world import ACCOUNT, SpotifyWorld

MINE = "spotify:playlist:1mineabcdefghijklmnopq"
EMPTY = "spotify:playlist:2emptyabcdefghijklmnop"
FOLLOWED = "spotify:playlist:3followedabcdefghijklm"
LIKED = f"spotify:user:{ACCOUNT}:collection"
SONG = "spotify:track:0yNttAVwMr39qyODHNIkrY"
OTHER = "spotify:track:4uLU6hMCjMI75M1A2tKUQC"
EPISODE = "spotify:episode:512ojhOuo1ktJprKbVcKyQ"


def playlist(uri, name, owner, length, can_edit):
    return {"uri": uri, "name": name, "description": "", "owner_username": owner, "length": length,
            "collaborative": False, "can_edit": can_edit, "folder": [], "image_url": None}


def entry(uri):
    return {"uri": uri, "track": {"uri": uri, "name": uri}}


@pytest.fixture
async def world(monkeypatch, tmp_path):
    w = SpotifyWorld(monkeypatch, tmp_path, stored=ACCOUNT)
    w.daemon.playlists = [
        playlist(MINE, "Chill appart", ACCOUNT, 3, True),
        playlist(FOLLOWED, "Spotify's", "spotify", 50, False),
        playlist(EMPTY, "À remplir", ACCOUNT, 0, True),
    ]
    w.daemon.listings[MINE] = [entry(SONG), entry(OTHER), entry(SONG)]
    await w.select()
    await w.idle()
    yield w
    await w.source.shutdown()


# === The picker ===

async def test_the_picker_lists_the_playlists_the_account_may_edit_and_what_they_hold(world):
    """An empty playlist is listed (it is the one being filled, though the
    home leaves it out), a followed one is not: go-librespot would refuse it."""
    world.daemon.liked.add(SONG)
    answer = await routes.get_track_playlists(uri=SONG, source=world.source)

    assert answer["liked_songs_uri"] == LIKED
    assert answer["liked"] is True
    assert [(p["uri"], p["contains"]) for p in answer["playlists"]] == [(MINE, True), (EMPTY, False)]


async def test_an_episode_has_no_liked_songs_row(world):
    """The collection holds tracks only: offered, the toggle would answer 400."""
    answer = await routes.get_track_playlists(uri=EPISODE, source=world.source)
    assert answer["liked"] is None


async def test_a_playlist_whose_read_failed_is_listed_without_an_answer(world):
    """One playlist Spotify did not answer for costs its own row's state,
    never the picker."""
    world.daemon.contains_refused.add(EMPTY)
    answer = await routes.get_track_playlists(uri=SONG, source=world.source)
    assert [(p["uri"], p["contains"]) for p in answer["playlists"]] == [(MINE, True), (EMPTY, None)]


async def test_a_liked_songs_read_that_failed_costs_only_its_row(world):
    world.daemon.liked_refused = True
    answer = await routes.get_track_playlists(uri=SONG, source=world.source)
    assert answer["liked"] is None
    assert [p["uri"] for p in answer["playlists"]] == [MINE, EMPTY]


# === Adding ===

async def test_adding_appends_to_the_playlist(world):
    await routes.add_tracks(uri=EMPTY, payload=AddTracksRequest(uris=[SONG]), source=world.source)
    assert world.daemon.library_writes == [
        ("/library/playlists/add_tracks", {"playlist_uri": EMPTY, "uris": [SONG]}),
    ]


async def test_adding_to_liked_songs_saves_the_track(world):
    await routes.add_tracks(uri=LIKED, payload=AddTracksRequest(uris=[SONG]), source=world.source)
    assert world.daemon.library_writes == [("/library/liked", {"uris": [SONG], "liked": True})]


async def test_an_episode_is_never_sent_to_liked_songs(world):
    with pytest.raises(HTTPException) as answer:
        await routes.add_tracks(uri=LIKED, payload=AddTracksRequest(uris=[EPISODE]), source=world.source)
    assert answer.value.status_code == 422
    assert world.daemon.library_writes == []


async def test_another_accounts_liked_songs_is_refused(world):
    """The collection a write reaches is the signed-in account's, whatever
    uri names it: a page left open across a profile switch must not write to
    the next account's."""
    with pytest.raises(HTTPException) as answer:
        await routes.add_tracks(uri="spotify:user:someoneelse:collection",
                                payload=AddTracksRequest(uris=[SONG]), source=world.source)
    assert answer.value.status_code == 422
    assert world.daemon.library_writes == []


async def test_a_playlist_not_the_accounts_to_edit_answers_403(world):
    """The browser says why; as a 503 it would read as Spotify being down."""
    with pytest.raises(HTTPException) as answer:
        await routes.add_tracks(uri=FOLLOWED, payload=AddTracksRequest(uris=[SONG]), source=world.source)
    assert answer.value.status_code == 403


# === Removing ===

async def test_removing_takes_the_entry_at_its_position(world):
    """The playlist holds the track twice: the copy removed is the row the
    menu was opened on, never the first one."""
    await routes.remove_track(uri=MINE, item=SONG, position=2, source=world.source)
    assert [e["uri"] for e in world.daemon.listings[MINE]] == [SONG, OTHER]


async def test_an_entry_moved_meanwhile_answers_409(world):
    """Edited from another device since it was listed: the browser lists it
    again rather than removing whatever now sits there."""
    with pytest.raises(HTTPException) as answer:
        await routes.remove_track(uri=MINE, item=SONG, position=1, source=world.source)
    assert answer.value.status_code == 409
    assert [e["uri"] for e in world.daemon.listings[MINE]] == [SONG, OTHER, SONG]


async def test_removing_from_liked_songs_unsaves_the_track(world):
    world.daemon.liked.add(SONG)
    await routes.remove_track(uri=LIKED, item=SONG, position=0, source=world.source)
    assert world.daemon.library_writes == [("/library/liked", {"uris": [SONG], "liked": False})]
    assert SONG not in world.daemon.liked


async def test_nobody_signed_in_answers_409(monkeypatch, tmp_path):
    """The state the browser draws as signing in, not a failure."""
    w = SpotifyWorld(monkeypatch, tmp_path)
    await w.select()
    try:
        with pytest.raises(HTTPException) as answer:
            await routes.add_tracks(uri=MINE, payload=AddTracksRequest(uris=[SONG]), source=w.source)
        assert answer.value.status_code == 409
    finally:
        await w.source.shutdown()
