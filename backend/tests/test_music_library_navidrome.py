"""Unit tests for the Music Library Navidrome client + REST routes (P1-5).

Two layers, both off the real Navidrome daemon:
- NavidromeClient: the browse/search/genres/playlists/star/coverArt surface,
  tested by mocking `_make_request` (the JSON boundary) with canned Subsonic
  payloads and asserting the extraction + param mapping. get_cover_art bypasses
  `_make_request`, so its aiohttp session is stubbed directly.
- Routes: the /api/music-library/* envelopes, 404/503/400 handling, the cover
  proxy and the resilient scan-status endpoint, via a FastAPI TestClient over a
  fake source whose get_navidrome_client returns a mock client.

Payloads mirror the Subsonic 1.16.1 shapes Navidrome returns (subsonic-response
already unwrapped by _make_request, so the fixtures start at the inner objects).
"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.sources.music_library.navidrome_client import NavidromeClient
from backend.sources.music_library.routes import (
    router,
    setup_music_library_routes,
)


# =============================================================================
# NavidromeClient — browse / search / playlists / star
# =============================================================================

@pytest.fixture
def client():
    return NavidromeClient("milo-svc", "secret")


class TestNavidromeBrowse:
    async def test_get_artists_returns_index_buckets(self, client):
        client._make_request = AsyncMock(return_value={
            "artists": {
                "ignoredArticles": "The",
                "index": [
                    {"name": "D", "artist": [{"id": "ar-1", "name": "Daft Punk"}]},
                ],
            }
        })
        index = await client.get_artists()
        assert index == [
            {"name": "D", "artist": [{"id": "ar-1", "name": "Daft Punk"}]}
        ]

    async def test_get_artist_unwraps_artist(self, client):
        client._make_request = AsyncMock(return_value={
            "artist": {"id": "ar-1", "name": "Daft Punk", "album": [{"id": "al-1"}]}
        })
        artist = await client.get_artist("ar-1")
        assert artist["name"] == "Daft Punk"
        client._make_request.assert_awaited_once_with("getArtist", {"id": "ar-1"})

    async def test_get_album_unwraps_album(self, client):
        client._make_request = AsyncMock(return_value={
            "album": {"id": "al-1", "name": "Discovery", "song": [{"id": "s-1"}]}
        })
        album = await client.get_album("al-1")
        assert album["name"] == "Discovery"
        assert album["song"][0]["id"] == "s-1"

    async def test_get_album_missing_returns_none(self, client):
        client._make_request = AsyncMock(return_value={})
        assert await client.get_album("nope") is None

    async def test_get_album_list_maps_params_and_extracts(self, client):
        client._make_request = AsyncMock(return_value={
            "albumList2": {"album": [{"id": "al-1"}, {"id": "al-2"}]}
        })
        albums = await client.get_album_list(
            list_type="byGenre", size=10, offset=20, genre="Techno"
        )
        assert [a["id"] for a in albums] == ["al-1", "al-2"]
        _, params = client._make_request.await_args.args
        assert params["type"] == "byGenre"
        assert params["size"] == 10
        assert params["offset"] == 20
        assert params["genre"] == "Techno"

    async def test_get_genres_extracts_list(self, client):
        client._make_request = AsyncMock(return_value={
            "genres": {"genre": [{"value": "Techno", "songCount": 42}]}
        })
        genres = await client.get_genres()
        assert genres[0]["value"] == "Techno"

    async def test_get_songs_by_genre_extracts_list(self, client):
        client._make_request = AsyncMock(return_value={
            "songsByGenre": {"song": [{"id": "s-1"}, {"id": "s-2"}]}
        })
        songs = await client.get_songs_by_genre("Techno", count=50, offset=0)
        assert [s["id"] for s in songs] == ["s-1", "s-2"]
        _, params = client._make_request.await_args.args
        assert params == {"genre": "Techno", "count": 50, "offset": 0}

    async def test_get_playlists_extracts_list(self, client):
        client._make_request = AsyncMock(return_value={
            "playlists": {"playlist": [{"id": "pl-1", "name": "Chill"}]}
        })
        assert (await client.get_playlists())[0]["name"] == "Chill"

    async def test_get_playlist_unwraps(self, client):
        client._make_request = AsyncMock(return_value={
            "playlist": {"id": "pl-1", "entry": [{"id": "s-1"}]}
        })
        pl = await client.get_playlist("pl-1")
        assert pl["entry"][0]["id"] == "s-1"

    async def test_search3_shapes_result(self, client):
        client._make_request = AsyncMock(return_value={
            "searchResult3": {
                "artist": [{"id": "ar-1"}],
                "album": [{"id": "al-1"}],
                "song": [{"id": "s-1"}],
            }
        })
        result = await client.search3("daft")
        assert result == {
            "artist": [{"id": "ar-1"}],
            "album": [{"id": "al-1"}],
            "song": [{"id": "s-1"}],
        }

    @pytest.mark.parametrize("payload", [None, {"_network_error": True}])
    async def test_list_methods_degrade_to_empty(self, client, payload):
        """A None (API error) or network-error sentinel yields empty, never raises."""
        client._make_request = AsyncMock(return_value=payload)
        assert await client.get_artists() == []
        assert await client.get_genres() == []
        assert await client.get_playlists() == []
        assert await client.get_album_list() == []
        assert await client.get_songs_by_genre("Techno") == []
        assert await client.get_artist("ar-1") is None
        assert await client.get_album("al-1") is None


class TestNavidromeStar:
    @pytest.mark.parametrize("kind,expected_param", [
        ("song", "id"),
        ("album", "albumId"),
        ("artist", "artistId"),
    ])
    async def test_star_maps_kind_to_param(self, client, kind, expected_param):
        client._make_request = AsyncMock(return_value={"status": "ok"})
        assert await client.star("x-1", kind=kind) is True
        endpoint, params = client._make_request.await_args.args
        assert endpoint == "star"
        assert params == {expected_param: "x-1"}

    async def test_unstar_hits_unstar_endpoint(self, client):
        client._make_request = AsyncMock(return_value={"status": "ok"})
        assert await client.unstar("s-1") is True
        assert client._make_request.await_args.args[0] == "unstar"

    async def test_star_network_error_is_false(self, client):
        client._make_request = AsyncMock(return_value={"_network_error": True})
        assert await client.star("s-1") is False


class TestNavidromePlaylistWrites:
    async def test_create_playlist_maps_name_and_songs(self, client):
        client._make_request = AsyncMock(return_value={
            "playlist": {"id": "pl-9", "name": "Road Trip"}
        })
        pl = await client.create_playlist("Road Trip", song_ids=["s-1", "s-2"])
        assert pl == {"id": "pl-9", "name": "Road Trip"}
        endpoint, params = client._make_request.await_args.args
        assert endpoint == "createPlaylist"
        assert params == {"name": "Road Trip", "songId": ["s-1", "s-2"]}

    async def test_create_playlist_without_songs(self, client):
        client._make_request = AsyncMock(return_value={"playlist": {"id": "pl-9"}})
        await client.create_playlist("Empty")
        _, params = client._make_request.await_args.args
        assert params == {"name": "Empty", "songId": None}

    @pytest.mark.parametrize("payload", [None, {"_network_error": True}])
    async def test_create_playlist_error_is_none(self, client, payload):
        client._make_request = AsyncMock(return_value=payload)
        assert await client.create_playlist("Road Trip") is None

    async def test_update_playlist_rename(self, client):
        client._make_request = AsyncMock(return_value={"status": "ok"})
        assert await client.update_playlist("pl-1", name="Renamed") is True
        endpoint, params = client._make_request.await_args.args
        assert endpoint == "updatePlaylist"
        assert params == {"playlistId": "pl-1", "name": "Renamed"}

    async def test_update_playlist_append_tracks(self, client):
        client._make_request = AsyncMock(return_value={"status": "ok"})
        assert await client.update_playlist("pl-1", song_ids_to_add=["s-3"]) is True
        _, params = client._make_request.await_args.args
        assert params == {"playlistId": "pl-1", "songIdToAdd": ["s-3"]}

    async def test_set_playlist_tracks_replaces_via_create(self, client):
        client._make_request = AsyncMock(return_value={"status": "ok"})
        assert await client.set_playlist_tracks("pl-1", ["s-2", "s-1"]) is True
        endpoint, params = client._make_request.await_args.args
        # Subsonic has no reorder verb: createPlaylist w/ playlistId rewrites order.
        assert endpoint == "createPlaylist"
        assert params == {"playlistId": "pl-1", "songId": ["s-2", "s-1"]}

    async def test_set_playlist_tracks_empty_clears(self, client):
        client._make_request = AsyncMock(return_value={"status": "ok"})
        assert await client.set_playlist_tracks("pl-1", []) is True
        _, params = client._make_request.await_args.args
        assert params == {"playlistId": "pl-1", "songId": []}

    async def test_delete_playlist_maps_id(self, client):
        client._make_request = AsyncMock(return_value={"status": "ok"})
        assert await client.delete_playlist("pl-1") is True
        endpoint, params = client._make_request.await_args.args
        assert endpoint == "deletePlaylist"
        assert params == {"id": "pl-1"}

    async def test_write_network_error_is_false(self, client):
        client._make_request = AsyncMock(return_value={"_network_error": True})
        assert await client.update_playlist("pl-1", name="x") is False
        assert await client.set_playlist_tracks("pl-1", ["s-1"]) is False
        assert await client.delete_playlist("pl-1") is False


class TestEncodeQuery:
    """The dict → aiohttp list-of-pairs encoder that backs multi-valued params."""

    def test_scalars_are_stringified_pairs(self):
        from backend.sources.music_library.navidrome_client import _encode_query
        assert _encode_query({"a": "x", "n": 5}) == [("a", "x"), ("n", "5")]

    def test_list_value_expands_to_repeated_keys(self):
        from backend.sources.music_library.navidrome_client import _encode_query
        pairs = _encode_query({"songId": ["s-1", "s-2", "s-3"]})
        assert pairs == [("songId", "s-1"), ("songId", "s-2"), ("songId", "s-3")]

    def test_none_values_and_none_items_dropped(self):
        from backend.sources.music_library.navidrome_client import _encode_query
        assert _encode_query({"a": None, "songId": ["s-1", None]}) == [("songId", "s-1")]

    def test_empty_list_contributes_no_pairs(self):
        from backend.sources.music_library.navidrome_client import _encode_query
        assert _encode_query({"playlistId": "pl-1", "songId": []}) == [("playlistId", "pl-1")]


class TestNavidromeCoverArt:
    def _stub_session(self, client, *, status=200, content_type="image/jpeg", body=b"IMG"):
        class _Resp:
            def __init__(self, resp_status, resp_ct, resp_body):
                self.status = resp_status
                self.headers = {"Content-Type": resp_ct}
                self._body = resp_body

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def read(self):
                return self._body

        def _get(url, **kwargs):
            params = kwargs.get("params") or []
            cover_id = (
                params.get("id")
                if isinstance(params, dict)
                else next((v for k, v in params if k == "id"), None)
            )
            # get_cover_art also probes an empty id to learn Navidrome's generic
            # placeholder signature — serve that a body distinct from the real
            # cover so the signature never collides with actual artwork.
            if not cover_id:
                return _Resp(200, "image/png", b"PLACEHOLDER")
            return _Resp(status, content_type, body)

        client._ensure_session = AsyncMock()
        client._session = MagicMock()
        client._session.get = MagicMock(side_effect=_get)

    async def test_cover_art_returns_bytes_and_type(self, client):
        self._stub_session(client, content_type="image/png", body=b"PNGDATA")
        result = await client.get_cover_art("al-1", size=300)
        assert result == (b"PNGDATA", "image/png")

    async def test_cover_art_json_error_body_is_none(self, client):
        # Subsonic replies with a JSON error body (not an image) on a bad id.
        self._stub_session(client, content_type="application/json", body=b"{}")
        assert await client.get_cover_art("bad") is None

    async def test_cover_art_http_error_is_none(self, client):
        self._stub_session(client, status=404)
        assert await client.get_cover_art("al-1") is None

    def _stub_placeholder_session(self, client, full_ph, thumb_ph, real):
        """Model Navidrome's placeholder behaviour: the empty-id reference and any
        art-less entity return ``full_ph`` at full size, but a *resized* art-less
        cover returns ``thumb_ph`` (different bytes) — the trap the size-aware
        check must catch. A real cover returns ``real`` at any size."""
        class _Resp:
            def __init__(self, ct, body):
                self.status, self.headers, self._body = 200, {"Content-Type": ct}, body

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def read(self):
                return self._body

        def _get(url, **kwargs):
            params = kwargs.get("params") or []
            d = params if isinstance(params, dict) else {k: v for k, v in params}
            cid, size = d.get("id"), d.get("size")
            if not cid or cid == "pl-empty":  # empty-id ref + art-less playlist
                return _Resp("image/webp", full_ph) if size is None else _Resp("image/jpeg", thumb_ph)
            return _Resp("image/jpeg", real)

        client._ensure_session = AsyncMock()
        client._session = MagicMock()
        client._session.get = MagicMock(side_effect=_get)

    async def test_resized_placeholder_suppressed_at_thumb_size(self, client):
        # The regression: an art-less playlist's blue-vinyl default, resized for a
        # thumbnail, doesn't match the full-size signature — must still be a miss.
        self._stub_placeholder_session(
            client, full_ph=b"FULL-PLACEHOLDER", thumb_ph=b"tiny-ph", real=b"REALART"
        )
        assert await client.get_cover_art("pl-empty", size=300) is None
        # A real cover at the same size still comes through.
        assert await client.get_cover_art("real", size=300) == (b"REALART", "image/jpeg")
        # Full-size placeholder is suppressed too.
        assert await client.get_cover_art("pl-empty") is None


# =============================================================================
# Routes — /api/music-library/*
# =============================================================================

@pytest.fixture
def nav_client():
    """A mock NavidromeClient with every consumed method as an AsyncMock."""
    c = MagicMock()
    c.get_artists = AsyncMock(return_value=[{"name": "D", "artist": []}])
    c.get_artist = AsyncMock(return_value={"id": "ar-1", "name": "Daft Punk"})
    c.get_album = AsyncMock(return_value={"id": "al-1", "name": "Discovery"})
    c.get_album_list = AsyncMock(return_value=[{"id": "al-1"}])
    c.get_genres = AsyncMock(return_value=[{"value": "Techno"}])
    c.get_songs_by_genre = AsyncMock(return_value=[{"id": "s-1"}])
    c.search3 = AsyncMock(return_value={"artist": [{"id": "ar-1"}], "album": [], "song": []})
    c.get_playlists = AsyncMock(return_value=[{"id": "pl-1"}])
    c.get_playlist = AsyncMock(return_value={"id": "pl-1", "entry": []})
    c.create_playlist = AsyncMock(return_value={"id": "pl-9", "name": "Road Trip"})
    c.update_playlist = AsyncMock(return_value=True)
    c.set_playlist_tracks = AsyncMock(return_value=True)
    c.delete_playlist = AsyncMock(return_value=True)
    c.get_cover_art = AsyncMock(return_value=(b"IMG", "image/jpeg"))
    c.star = AsyncMock(return_value=True)
    c.unstar = AsyncMock(return_value=True)
    c.get_scan_status = AsyncMock(return_value={"scanning": False, "count": 100})
    c.start_scan = AsyncMock(return_value=True)
    return c


@pytest.fixture
def source(nav_client):
    src = MagicMock()
    src.get_navidrome_client = AsyncMock(return_value=nav_client)
    src.invalidate_navidrome_client = AsyncMock()
    return src


@pytest.fixture
def api(source):
    app = FastAPI()
    setup_music_library_routes(lambda: source)
    app.include_router(router, prefix="/api")
    return TestClient(app)


class TestBrowseRoutes:
    def test_artists_envelope(self, api, nav_client):
        r = api.get("/api/music-library/artists")
        assert r.status_code == 200
        assert r.json() == {"index": [{"name": "D", "artist": []}]}

    def test_artist_wraps(self, api):
        r = api.get("/api/music-library/artist/ar-1")
        assert r.status_code == 200
        assert r.json()["artist"]["name"] == "Daft Punk"

    def test_artist_404_when_missing(self, api, nav_client):
        nav_client.get_artist = AsyncMock(return_value=None)
        assert api.get("/api/music-library/artist/nope").status_code == 404

    def test_album_404_when_missing(self, api, nav_client):
        nav_client.get_album = AsyncMock(return_value=None)
        assert api.get("/api/music-library/album/nope").status_code == 404

    def test_albums_passes_params(self, api, nav_client):
        r = api.get("/api/music-library/albums", params={
            "type": "byGenre", "genre": "Techno", "size": 10, "offset": 5
        })
        assert r.status_code == 200
        assert r.json() == {"albums": [{"id": "al-1"}]}
        nav_client.get_album_list.assert_awaited_once()
        kwargs = nav_client.get_album_list.await_args.kwargs
        assert kwargs["list_type"] == "byGenre"
        assert kwargs["genre"] == "Techno"
        assert kwargs["size"] == 10
        assert kwargs["offset"] == 5

    def test_albums_rejects_invalid_type(self, api, nav_client):
        r = api.get("/api/music-library/albums", params={"type": "bogus"})
        assert r.status_code == 400
        nav_client.get_album_list.assert_not_called()

    def test_genres_envelope(self, api):
        assert api.get("/api/music-library/genres").json() == {"genres": [{"value": "Techno"}]}

    def test_genre_songs_requires_genre(self, api):
        # `genre` is a required query param — omitting it is a 422 from FastAPI.
        assert api.get("/api/music-library/genre-songs").status_code == 422

    def test_genre_songs_envelope(self, api):
        r = api.get("/api/music-library/genre-songs", params={"genre": "Techno"})
        assert r.json() == {"songs": [{"id": "s-1"}]}


class TestSearchRoute:
    def test_search_shapes_envelope(self, api):
        r = api.get("/api/music-library/search", params={"query": "daft"})
        assert r.status_code == 200
        assert r.json() == {"artists": [{"id": "ar-1"}], "albums": [], "songs": []}

    def test_empty_query_short_circuits(self, api, nav_client):
        r = api.get("/api/music-library/search", params={"query": "  "})
        assert r.json() == {"artists": [], "albums": [], "songs": []}
        nav_client.search3.assert_not_called()


class TestPlaylistRoutes:
    def test_playlists_envelope(self, api):
        assert api.get("/api/music-library/playlists").json() == {"playlists": [{"id": "pl-1"}]}

    def test_playlist_404_when_missing(self, api, nav_client):
        nav_client.get_playlist = AsyncMock(return_value=None)
        assert api.get("/api/music-library/playlist/nope").status_code == 404


class TestPlaylistWriteRoutes:
    def test_create_returns_playlist(self, api, nav_client):
        r = api.post("/api/music-library/playlists", json={"name": "Road Trip"})
        assert r.status_code == 200
        assert r.json() == {"status": "success", "playlist": {"id": "pl-9", "name": "Road Trip"}}
        nav_client.create_playlist.assert_awaited_once_with("Road Trip", song_ids=None)

    def test_create_with_seed_songs(self, api, nav_client):
        api.post("/api/music-library/playlists", json={"name": "Mix", "song_ids": ["s-1", "s-2"]})
        nav_client.create_playlist.assert_awaited_once_with("Mix", song_ids=["s-1", "s-2"])

    def test_create_rejects_empty_name(self, api):
        assert api.post("/api/music-library/playlists", json={"name": ""}).status_code == 422

    def test_create_502_when_navidrome_rejects(self, api, nav_client):
        nav_client.create_playlist = AsyncMock(return_value=None)
        assert api.post("/api/music-library/playlists", json={"name": "x"}).status_code == 502

    def test_update_rename(self, api, nav_client):
        r = api.put("/api/music-library/playlist/pl-1", json={"name": "Renamed"})
        assert r.status_code == 200
        nav_client.update_playlist.assert_awaited_once_with("pl-1", name="Renamed")
        nav_client.set_playlist_tracks.assert_not_called()

    def test_update_append_tracks(self, api, nav_client):
        api.put("/api/music-library/playlist/pl-1", json={"song_ids_to_add": ["s-3"]})
        nav_client.update_playlist.assert_awaited_once_with("pl-1", song_ids_to_add=["s-3"])

    def test_update_replace_order(self, api, nav_client):
        api.put("/api/music-library/playlist/pl-1", json={"track_ids": ["s-2", "s-1"]})
        nav_client.set_playlist_tracks.assert_awaited_once_with("pl-1", ["s-2", "s-1"])
        nav_client.update_playlist.assert_not_called()

    def test_update_replace_empty_clears(self, api, nav_client):
        # track_ids=[] is a valid single op (distinct from None) — clears the playlist.
        r = api.put("/api/music-library/playlist/pl-1", json={"track_ids": []})
        assert r.status_code == 200
        nav_client.set_playlist_tracks.assert_awaited_once_with("pl-1", [])

    def test_update_rejects_no_operation(self, api):
        assert api.put("/api/music-library/playlist/pl-1", json={}).status_code == 422

    def test_update_rejects_multiple_operations(self, api):
        r = api.put("/api/music-library/playlist/pl-1", json={"name": "x", "track_ids": ["s-1"]})
        assert r.status_code == 422

    def test_update_502_when_navidrome_rejects(self, api, nav_client):
        nav_client.update_playlist = AsyncMock(return_value=False)
        assert api.put("/api/music-library/playlist/pl-1", json={"name": "x"}).status_code == 502

    def test_delete_success(self, api, nav_client):
        r = api.delete("/api/music-library/playlist/pl-1")
        assert r.status_code == 200
        assert r.json() == {"status": "success"}
        nav_client.delete_playlist.assert_awaited_once_with("pl-1")

    def test_delete_502_when_navidrome_rejects(self, api, nav_client):
        nav_client.delete_playlist = AsyncMock(return_value=False)
        assert api.delete("/api/music-library/playlist/pl-1").status_code == 502


class TestCoverRoute:
    def test_cover_returns_bytes_with_cache_header(self, api):
        r = api.get("/api/music-library/cover/al-1")
        assert r.status_code == 200
        assert r.content == b"IMG"
        assert r.headers["content-type"] == "image/jpeg"
        assert "max-age=31536000" in r.headers["cache-control"]

    def test_cover_404_when_unavailable(self, api, nav_client):
        nav_client.get_cover_art = AsyncMock(return_value=None)
        assert api.get("/api/music-library/cover/al-1").status_code == 404


class TestStarRoutes:
    def test_star_passes_id_and_kind(self, api, nav_client):
        r = api.post("/api/music-library/star", json={"id": "al-1", "kind": "album"})
        assert r.status_code == 200
        assert r.json() == {"status": "success"}
        nav_client.star.assert_awaited_once_with("al-1", kind="album")

    def test_star_defaults_to_song_kind(self, api, nav_client):
        api.post("/api/music-library/star", json={"id": "s-1"})
        nav_client.star.assert_awaited_once_with("s-1", kind="song")

    def test_star_rejects_empty_id(self, api):
        assert api.post("/api/music-library/star", json={"id": ""}).status_code == 422

    def test_star_502_when_navidrome_rejects(self, api, nav_client):
        nav_client.star = AsyncMock(return_value=False)
        assert api.post("/api/music-library/star", json={"id": "s-1"}).status_code == 502

    def test_unstar(self, api, nav_client):
        r = api.post("/api/music-library/unstar", json={"id": "s-1"})
        assert r.status_code == 200
        nav_client.unstar.assert_awaited_once_with("s-1", kind="song")


class TestClientUnavailable:
    def test_browse_503_when_client_missing(self, api, source):
        source.get_navidrome_client = AsyncMock(return_value=None)
        assert api.get("/api/music-library/artists").status_code == 503

    def test_scan_status_resilient_when_client_missing(self, api, source):
        source.get_navidrome_client = AsyncMock(return_value=None)
        r = api.get("/api/music-library/scan-status")
        assert r.status_code == 200
        assert r.json() == {"scan_status": None}

    def test_scan_status_passthrough(self, api):
        r = api.get("/api/music-library/scan-status")
        assert r.status_code == 200
        assert r.json() == {"scan_status": {"scanning": False, "count": 100}}


class TestScanRoute:
    def test_scan_triggers_start_scan(self, api, nav_client):
        r = api.post("/api/music-library/scan")
        assert r.status_code == 200
        assert r.json() == {"status": "success"}
        nav_client.start_scan.assert_awaited_once()

    def test_scan_503_when_client_missing(self, api, source):
        source.get_navidrome_client = AsyncMock(return_value=None)
        assert api.post("/api/music-library/scan").status_code == 503


class TestAuthRecovery:
    def test_auth_error_invalidates_client_and_503(self, api, source, nav_client):
        from backend.sources.music_library.navidrome_client import NavidromeAuthError
        nav_client.get_artists = AsyncMock(side_effect=NavidromeAuthError("bad creds"))
        r = api.get("/api/music-library/artists")
        assert r.status_code == 503
        source.invalidate_navidrome_client.assert_awaited_once()
