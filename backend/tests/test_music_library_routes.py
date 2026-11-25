# backend/tests/test_music_library_routes.py
"""The playlist list and a genre's albums, against a Navidrome that speaks HTTP.

The other route tests replace the source with a MagicMock, which is what lets
them drive one route arm at a time — and what hides the two things these tests
are about, since both live in how the source and the route read Navidrome
together: what ``GET /playlists`` does when a read fails, how many reads it
costs, and how a genre longer than one Navidrome page is answered. So here the
source and the Subsonic client are the real ones, and the outside world is a
fake Navidrome at its HTTP surface (``FakeNavidrome``: getAlbumList2,
getPlaylists, getPlaylist), counting what it was asked. The storage list is the
one other boundary stood in for: it is read from files the share service owns.

What breaks when these fail:

* **the Playlists tab answers 503** while Navidrome is busy with a first index,
  or because one playlist among forty did not answer — a tab that listed fine
  before its rows were recounted;
* **a playlist vanishes** from one load of the list because Navidrome answered
  its read with an error, as if it had been deleted;
* **every load reads every playlist**, twice on the first: forty playlists and
  three adds from the picker were 123 requests to a Navidrome on a Pi;
* **a genre's Albums tab stops at 500** beside a Songs tab that lists the whole
  genre, and a box set across Navidrome's page boundary is two rows.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.sources.music_library import navidrome_client as client_module
from backend.sources.music_library.disc_merge import is_merged_id
from backend.sources.music_library.navidrome_client import NavidromeClient
from backend.sources.music_library.routes import router, setup_music_library_routes
from backend.sources.music_library.source import _PLAYLIST_READS, MusicLibrarySource

# The one mounted storage space, Navidrome library 2.
LIBRARY = 2


class _Reply:
    """One HTTP answer of the fake Navidrome, as aiohttp's response context."""

    def __init__(self, navidrome, status, body):
        self._navidrome = navidrome
        self.status = status
        self._body = body

    async def __aenter__(self):
        # Hold the request open for a few turns of the loop, so requests sent
        # together are in flight together and the fake can count them.
        self._navidrome.in_flight += 1
        self._navidrome.most_in_flight = max(
            self._navidrome.most_in_flight, self._navidrome.in_flight
        )
        for _ in range(3):
            await asyncio.sleep(0)
        self._navidrome.in_flight -= 1
        return self

    async def __aexit__(self, *exc):
        return False

    async def text(self):
        return str(self._body)

    async def json(self, content_type=None):
        return self._body


class FakeNavidrome:
    """Navidrome's Subsonic surface for these routes, as the client's session.

    ``albums`` is the alphabetical catalog of the one library, ``genre_albums``
    a genre's albums in Navidrome's order, ``playlists`` id → (changed stamp,
    entries). ``unanswered`` holds what answers HTTP 503 — ``("getPlaylist",
    id)`` or ``("getAlbumList2", type, offset)`` — and ``refused`` the
    playlists whose read answers an API error.
    """

    def __init__(self):
        self.closed = False
        self.albums = []
        self.genre_albums = []
        self.playlists = {}
        self.unanswered = set()
        self.refused = set()
        self.reads = {}  # playlist id -> getPlaylist requests
        self.in_flight = 0
        self.most_in_flight = 0

    def get(self, url, params=(), **_):
        endpoint = url.rsplit("/", 1)[-1]
        query = {}
        for key, value in params:
            query.setdefault(key, value)
        return getattr(self, f"_{endpoint}")(query)

    async def close(self):
        self.closed = True

    def _ok(self, payload):
        return _Reply(self, 200, {"subsonic-response": {"status": "ok", **payload}})

    def _getAlbumList2(self, query):
        kind, offset, size = query["type"], int(query["offset"]), int(query["size"])
        if ("getAlbumList2", kind, offset) in self.unanswered:
            return _Reply(self, 503, "busy")
        listed = self.genre_albums if kind == "byGenre" else self.albums
        return self._ok({"albumList2": {"album": listed[offset:offset + size]}})

    def _getPlaylists(self, query):
        return self._ok({"playlists": {"playlist": [
            {
                "id": playlist_id,
                "name": playlist_id,
                "changed": changed,
                "songCount": len(entries),
                "duration": sum(entry["duration"] for entry in entries),
            }
            for playlist_id, (changed, entries) in self.playlists.items()
        ]}})

    def _getPlaylist(self, query):
        playlist_id = query["id"]
        self.reads[playlist_id] = self.reads.get(playlist_id, 0) + 1
        if ("getPlaylist", playlist_id) in self.unanswered:
            return _Reply(self, 503, "busy")
        if playlist_id in self.refused:
            return _Reply(self, 200, {"subsonic-response": {
                "status": "failed", "error": {"code": 0, "message": "database is locked"},
            }})
        changed, entries = self.playlists[playlist_id]
        return self._ok({"playlist": {
            "id": playlist_id,
            "changed": changed,
            "songCount": len(entries),
            "duration": sum(entry["duration"] for entry in entries),
            "entry": entries,
        }})


def _entries(*albums, duration=100):
    """A playlist's entries, one song per album id given."""
    return [
        {"id": f"s-{index}-{album}", "albumId": album, "duration": duration}
        for index, album in enumerate(albums)
    ]


@pytest.fixture(autouse=True)
def never_dial_the_live_navidrome(monkeypatch):
    """A session the fake did not replace is an error, not a call to the unit's
    own Navidrome on loopback."""
    def _refuse(*args, **kwargs):
        raise AssertionError("the client built a real aiohttp session")

    monkeypatch.setattr(client_module.aiohttp, "ClientSession", _refuse)


@pytest.fixture
def navidrome():
    fake = FakeNavidrome()
    fake.albums = [{"id": "al-home", "name": "Home"}]
    return fake


@pytest.fixture
def source(navidrome):
    src = MusicLibrarySource(config={}, state_machine=MagicMock())
    src._shares = MagicMock()
    src._shares.storages = AsyncMock(return_value=[
        {"kind": "usb", "id": "U-1", "library_id": LIBRARY, "mounted": True},
    ])
    src._shares.playlist_storages = AsyncMock(return_value={})
    client = NavidromeClient("milo-svc", "secret")
    client._session = navidrome
    src._navidrome = client
    return src


@pytest.fixture
def api(source):
    app = FastAPI()
    setup_music_library_routes(lambda: source)
    app.include_router(router, prefix="/api")
    with TestClient(app) as test_client:
        yield test_client


def _rows(api):
    response = api.get("/api/music-library/playlists")
    assert response.status_code == 200, response.text
    return {row["id"]: row for row in response.json()["playlists"]}


class TestPlaylistListSurvivesWhatItCannotRead:

    def test_a_row_counts_what_its_page_lists(self, api, source, navidrome):
        """The recount the list does at all: Navidrome's count takes in a key
        that is away, so the row said 2 tracks over a page listing 1."""
        navidrome.playlists = {"pl-1": ("t1", _entries("al-home", "al-unplugged"))}

        row = _rows(api)["pl-1"]
        page = api.get("/api/music-library/playlist/pl-1").json()["playlist"]

        assert (row["songCount"], row["duration"]) == (page["songCount"], page["duration"])
        assert row["songCount"] == 1  # non-trivial: Navidrome said 2

    def test_an_album_inventory_cut_short_leaves_navidromes_counts(
        self, api, source, navidrome
    ):
        """A first index keeps Navidrome too busy to answer an album page in
        time. The list placed its playlist from the albums it did read, and the
        strict inventory the recount needs stopped short: that was a 503 for
        the whole tab, which listed fine before the recount existed."""
        navidrome.albums = [{"id": f"al-{n:03d}", "name": f"{n:03d} Songs"} for n in range(501)]
        navidrome.unanswered = {("getAlbumList2", "alphabeticalByName", 500)}
        navidrome.playlists = {"pl-1": ("t1", _entries("al-000", "al-001", "al-002"))}

        row = _rows(api)["pl-1"]

        assert (row["songCount"], row["duration"]) == (3, 300)

    @pytest.mark.parametrize("recorded", [True, False], ids=["created", "imported"])
    @pytest.mark.parametrize("failure", ["refused", "unanswered"])
    def test_a_playlist_whose_read_failed_stays_listed_at_navidromes_count(
        self, api, source, navidrome, failure, recorded
    ):
        """An error Navidrome answered (a locked database, a 500) used to file
        the playlist as deleted for that load; one it did not answer failed the
        whole list. Either way the others are still recounted. An imported
        ``.m3u`` — no storage recorded — is first read by its placement, which is
        the only reader on a first load and after every scan: the same holds
        there, and its failed read is not asked again by the count."""
        source._shares.playlist_storages = AsyncMock(
            return_value={"pl-bad": "U-1", "pl-ok": "U-1"} if recorded else {}
        )
        navidrome.playlists = {
            "pl-bad": ("t1", _entries("al-home", "al-home")),
            "pl-ok": ("t1", _entries("al-home", "al-unplugged")),
        }
        if failure == "refused":
            navidrome.refused = {"pl-bad"}
        else:
            navidrome.unanswered = {("getPlaylist", "pl-bad")}

        rows = _rows(api)

        assert rows["pl-bad"]["songCount"] == 2
        assert rows["pl-ok"]["songCount"] == 1
        assert navidrome.reads["pl-bad"] == 1


class TestPlaylistListReadsEachPlaylistOncePerChange:

    def test_two_loads_with_no_change_read_each_playlist_once(
        self, api, source, navidrome
    ):
        """The tab is reloaded after every playlist edit, at the end of every
        scan and, during one, as the album count moves. It read every playlist
        on each load — and twice on the first, once to place it and once to
        count it. A playlist whose ``changed`` moved is read again, once."""
        source._shares.playlist_storages = AsyncMock(return_value={"pl-made": "U-1"})
        navidrome.playlists = {
            "pl-made": ("t1", _entries("al-home")),
            "pl-m3u-1": ("t1", _entries("al-home", "al-home")),
            "pl-m3u-2": ("t1", _entries("al-home", "al-unplugged")),
        }

        _rows(api)
        _rows(api)

        assert navidrome.reads == {"pl-made": 1, "pl-m3u-1": 1, "pl-m3u-2": 1}

        navidrome.playlists["pl-m3u-1"] = ("t2", _entries("al-home"))
        rows = _rows(api)

        assert navidrome.reads == {"pl-made": 1, "pl-m3u-1": 2, "pl-m3u-2": 1}
        assert rows["pl-m3u-1"]["songCount"] == 1

    def test_reads_are_sent_a_few_at_a_time(self, api, source, navidrome):
        """Forty imported playlists were forty reads sent at once to a Navidrome
        that may be indexing."""
        source._shares.playlist_storages = AsyncMock(
            return_value={f"pl-{n}": "U-1" for n in range(20)}
        )
        navidrome.playlists = {f"pl-{n}": ("t1", _entries("al-home")) for n in range(20)}

        rows = _rows(api)

        assert len(rows) == 20
        assert sum(navidrome.reads.values()) == 20
        assert 1 < navidrome.most_in_flight <= _PLAYLIST_READS < 20


class TestGenreAlbumsAreTheWholeGenre:

    @staticmethod
    def _genre(navidrome):
        """500 + 120 albums, the two discs of a box set across the boundary."""
        albums = [{"id": f"al-{n:03d}", "name": f"{n:03d} Songs"} for n in range(620)]
        albums[499] = {"id": "box-1", "name": "Box (CD 1)", "artistId": "ar-1"}
        albums[500] = {"id": "box-2", "name": "Box (CD 2)", "artistId": "ar-1"}
        navidrome.genre_albums = albums

    def test_every_page_is_read_and_merged_once(self, api, navidrome):
        """One page was the tab, cut at 500 with nothing said; merged page by
        page, the box set would be two rows."""
        self._genre(navidrome)

        response = api.get("/api/music-library/albums", params={
            "type": "byGenre", "genre": "Rock",
        })

        albums = response.json()["albums"]
        assert len(albums) == 619
        assert [album["id"] for album in albums if is_merged_id(album["id"])] != []

    def test_a_page_navidrome_did_not_answer_is_a_503_not_a_shorter_genre(
        self, api, navidrome
    ):
        self._genre(navidrome)
        navidrome.unanswered = {("getAlbumList2", "byGenre", 500)}

        response = api.get("/api/music-library/albums", params={
            "type": "byGenre", "genre": "Rock",
        })

        assert response.status_code == 503
