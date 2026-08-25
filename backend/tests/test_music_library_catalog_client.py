# backend/tests/test_music_library_catalog_client.py
"""The shared Navidrome client's lifecycle, and the route arms that turn on it.

`get_navidrome_client` and `invalidate_navidrome_client` had never run: every
route test replaces the source with a MagicMock whose accessor already answers a
client, so the *cycle* the pair exists for was untested from both ends.

That cycle is the source's only recovery from a rotated credential. Navidrome's
cred file is written by first-boot provisioning and re-written if the service
account is ever re-provisioned; when it moves, every Subsonic call starts coming
back as error 40 and the client holds a password that will never work again.
`_catalog_errors` catches the `NavidromeAuthError`, drops the cached client, and
answers 503 — and the *next* request rebuilds from the file on disk. Break any
link and the music library is dead until someone restarts the backend.

Also here, because they had never run either: the "Liked Songs" route, the star
fan-out that makes favouriting a collapsed multi-disc release star every disc,
and the alphabetical album page — the one browse that pages over the whole
merged catalog rather than asking Navidrome for a page.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.sources.music_library import source as source_mod
from backend.sources.music_library.disc_merge import build_merged_id
from backend.sources.music_library.navidrome_client import NavidromeAuthError
from backend.sources.music_library.routes import router, setup_music_library_routes
from backend.sources.music_library.source import MusicLibrarySource


# =============================================================================
# The shared client — built late, dropped on a rejected credential
# =============================================================================

class TestSharedClientLifecycle:

    @pytest.fixture
    def source(self):
        return MusicLibrarySource({"mpv_socket": "/tmp/test-music-library-ipc.sock"})

    async def test_the_client_is_built_from_the_cred_file_on_first_use(self, source):
        """Not in the constructor: on a fresh unit the source is created before
        first-boot provisioning has written the file."""
        built = Mock()
        with patch.object(source_mod.NavidromeClient, "from_cred_file",
                          return_value=built) as factory:
            assert await source.get_navidrome_client() is built

        factory.assert_called_once()

    async def test_the_same_client_is_shared_rather_than_rebuilt(self, source):
        """One aiohttp session and one cover memo for the whole source; a second
        instance would keep its own and miss the invalidation below."""
        with patch.object(source_mod.NavidromeClient, "from_cred_file",
                          return_value=Mock()) as factory:
            first = await source.get_navidrome_client()
            second = await source.get_navidrome_client()

        assert first is second
        factory.assert_called_once()

    async def test_the_cred_file_is_re_read_on_every_attempt_until_it_appears(
        self, source
    ):
        """Provisioning finishes on its own schedule, so the client has to appear
        without a backend restart — the routes 503 in the meantime."""
        with patch.object(source_mod.NavidromeClient, "from_cred_file",
                          side_effect=[None, None, Mock()]) as factory:
            assert await source.get_navidrome_client() is None
            assert await source.get_navidrome_client() is None
            assert await source.get_navidrome_client() is not None

        assert factory.call_count == 3

    async def test_invalidating_closes_the_session_before_dropping_it(self, source):
        """The aiohttp session is a real resource; dropping the reference without
        closing it leaks a connector for the life of the process."""
        client = Mock()
        client.close = AsyncMock()
        source._navidrome = client

        await source.invalidate_navidrome_client()

        client.close.assert_awaited_once()
        assert source._navidrome is None

    async def test_invalidating_twice_is_harmless(self, source):
        """Two requests can hit error 40 together."""
        await source.invalidate_navidrome_client()

        assert source._navidrome is None

    async def test_the_next_request_rebuilds_from_the_file_on_disk(self, source):
        """The point of the whole cycle: a rotated password is picked up without
        a restart."""
        stale, fresh = Mock(), Mock()
        stale.close = AsyncMock()
        source._navidrome = stale

        await source.invalidate_navidrome_client()
        with patch.object(source_mod.NavidromeClient, "from_cred_file",
                          return_value=fresh):
            assert await source.get_navidrome_client() is fresh


class TestAlbumCacheInvalidation:

    @pytest.fixture
    def source(self):
        return MusicLibrarySource({"mpv_socket": "/tmp/test-music-library-ipc.sock"})

    def test_a_rescan_drops_the_grid_cache_the_memo_and_the_cover_memo(self, source):
        """All three answer "what is in this storage space", and a rescan is
        exactly when that changes — an album can also gain or lose its art, which
        is what makes the client's cover memo wrong at the same moment."""
        client = Mock()
        source._navidrome = client
        source._album_cache[(2,)] = (0.0, [{"id": "al-1"}])
        source._playlist_album["pl-1"] = "al-1"

        source.invalidate_album_cache()

        assert source._album_cache == {}
        assert source._playlist_album == {}
        client.invalidate_cover_memo.assert_called_once()

    def test_it_works_before_a_client_has_ever_been_built(self, source):
        """A share can be added before Navidrome has been reached once."""
        source._album_cache[(2,)] = (0.0, [])

        source.invalidate_album_cache()

        assert source._album_cache == {}


# =============================================================================
# The routes that close the loop
# =============================================================================

@pytest.fixture
def nav_client():
    client = MagicMock()
    client.get_starred = AsyncMock(
        return_value={"song": [{"id": "s-1"}], "album": [], "artist": []}
    )
    client.star = AsyncMock(return_value=True)
    client.unstar = AsyncMock(return_value=True)
    client.get_album_list = AsyncMock(return_value=[{"id": "al-9"}])
    client.create_playlist = AsyncMock(return_value={"id": "pl-1"})
    return client


@pytest.fixture
def source(nav_client):
    src = MagicMock()
    src.get_navidrome_client = AsyncMock(return_value=nav_client)
    src.invalidate_navidrome_client = AsyncMock()
    src.browse_scope = AsyncMock(return_value=[2])
    src.mounted_album_ids = AsyncMock(return_value=set())
    src.get_merged_albums = AsyncMock(return_value=[
        {"id": f"al-{n}"} for n in range(10)
    ])
    src.shares.record_playlist_storage = AsyncMock()
    return src


@pytest.fixture
def api(source):
    app = FastAPI()
    setup_music_library_routes(lambda: source)
    app.include_router(router, prefix="/api")
    return TestClient(app)


class TestAuthRecoveryReachesTheSource:

    def test_a_rejected_credential_drops_the_cached_client_and_503s(
        self, api, source, nav_client
    ):
        """503, not 500: the condition heals itself on the next request, and the
        drop is what makes that true."""
        nav_client.get_starred = AsyncMock(side_effect=NavidromeAuthError("code 40"))

        response = api.get("/api/music-library/starred")

        assert response.status_code == 503
        source.invalidate_navidrome_client.assert_awaited_once()

    def test_any_other_failure_is_a_500_and_keeps_the_client(self, api, source, nav_client):
        """Dropping the client on an unrelated fault would throw away a good
        session on every transient error."""
        nav_client.get_starred = AsyncMock(side_effect=RuntimeError("boom"))

        assert api.get("/api/music-library/starred").status_code == 500
        source.invalidate_navidrome_client.assert_not_awaited()

    def test_a_catalog_that_is_not_provisioned_yet_is_a_503_not_a_500(
        self, api, source
    ):
        source.get_navidrome_client = AsyncMock(return_value=None)

        assert api.get("/api/music-library/starred").status_code == 503


class TestStarredSongs:

    def test_the_liked_songs_playlist_is_songs_only(self, api, nav_client):
        """Albums and artists are starrable but are not surfaced as favourites;
        the virtual playlist is a song list and the UI iterates it as one."""
        nav_client.get_starred = AsyncMock(return_value={
            "song": [{"id": "s-1"}], "album": [{"id": "al-1"}], "artist": [{"id": "ar-1"}],
        })

        assert api.get("/api/music-library/starred").json() == {"songs": [{"id": "s-1"}]}

    def test_it_is_asked_within_the_browse_scope(self, api, source, nav_client):
        """Unlike getGenres and getPlaylists, Navidrome does honour the scope
        here — so an unscoped call would list favourites from an unplugged key."""
        api.get("/api/music-library/starred", params={"library_id": 5})

        source.browse_scope.assert_awaited_once_with(5)
        nav_client.get_starred.assert_awaited_once_with([2])


class TestStarFansOutOverAMergedRelease:

    def test_favouriting_a_collapsed_release_stars_every_disc(self, api, nav_client):
        """The grid shows one row for a "… CD 1"/"CD 2" pair, so a star on that
        row has to reach both real albums — otherwise reopening the library shows
        the release half-favourited."""
        merged = build_merged_id(["al-1", "al-2"])

        response = api.post("/api/music-library/star",
                            json={"id": merged, "kind": "album"})

        assert response.status_code == 200
        assert [call.args[0] for call in nav_client.star.await_args_list] == ["al-1", "al-2"]

    def test_a_plain_id_is_starred_as_is(self, api, nav_client):
        api.post("/api/music-library/star", json={"id": "s-1", "kind": "song"})

        nav_client.star.assert_awaited_once_with("s-1", kind="song")

    def test_a_merged_id_on_a_song_is_never_expanded(self, api, nav_client):
        """The fan-out is an album rule; a song id that happens to look merged
        must reach Navidrome untouched."""
        merged = build_merged_id(["al-1", "al-2"])

        api.post("/api/music-library/star", json={"id": merged, "kind": "song"})

        nav_client.star.assert_awaited_once_with(merged, kind="song")

    def test_unfavouriting_fans_out_the_same_way(self, api, nav_client):
        merged = build_merged_id(["al-1", "al-2"])

        api.post("/api/music-library/unstar", json={"id": merged, "kind": "album"})

        assert [c.args[0] for c in nav_client.unstar.await_args_list] == ["al-1", "al-2"]

    def test_a_refused_unstar_is_reported_rather_than_claimed(self, api, nav_client):
        """502: the heart in the UI would otherwise go grey over a favourite that
        is still stored, and come back on the next load."""
        nav_client.unstar = AsyncMock(return_value=False)

        assert api.post("/api/music-library/unstar",
                        json={"id": "s-1", "kind": "song"}).status_code == 502


class TestAlphabeticalGridPagesTheWholeCatalog:

    def test_the_alphabetical_page_comes_from_the_merged_catalog(
        self, api, source, nav_client
    ):
        """The only paged browse. Navidrome pages *before* the multi-disc merge,
        so a "CD 1"/"CD 2" pair straddling a page boundary would collapse into
        one row on one page and vanish from the other."""
        response = api.get("/api/music-library/albums", params={
            "type": "alphabeticalByName", "size": 3, "offset": 6,
        })

        assert response.json() == {"albums": [{"id": "al-6"}, {"id": "al-7"}, {"id": "al-8"}]}
        nav_client.get_album_list.assert_not_awaited()
        source.get_merged_albums.assert_awaited_once_with([2])

    def test_an_alphabetical_page_filtered_by_genre_goes_back_to_navidrome(
        self, api, source, nav_client
    ):
        """The whole-catalog cache is per scope, not per genre."""
        api.get("/api/music-library/albums", params={
            "type": "alphabeticalByName", "genre": "Techno",
        })

        nav_client.get_album_list.assert_awaited_once()
        source.get_merged_albums.assert_not_awaited()

    def test_any_other_list_type_is_paged_by_navidrome(self, api, source, nav_client):
        api.get("/api/music-library/albums", params={"type": "newest"})

        nav_client.get_album_list.assert_awaited_once()
        source.get_merged_albums.assert_not_awaited()


class TestPlaylistPlacement:

    def test_a_new_playlist_is_filed_under_the_storage_space_it_was_made_in(
        self, api, source
    ):
        """An empty playlist has no track to be judged by, so this is the only
        moment its storage space can be recorded."""
        api.post("/api/music-library/playlists",
                 json={"name": "Soirée", "library_id": 2})

        source.shares.record_playlist_storage.assert_awaited_once_with("pl-1", 2)

    def test_a_playlist_made_without_a_scope_is_filed_nowhere(self, api, source):
        api.post("/api/music-library/playlists", json={"name": "Soirée"})

        source.shares.record_playlist_storage.assert_not_awaited()

    def test_a_refused_creation_is_a_502_and_files_nothing(self, api, source, nav_client):
        nav_client.create_playlist = AsyncMock(return_value=None)

        response = api.post("/api/music-library/playlists",
                            json={"name": "Soirée", "library_id": 2})

        assert response.status_code == 502
        source.shares.record_playlist_storage.assert_not_awaited()
