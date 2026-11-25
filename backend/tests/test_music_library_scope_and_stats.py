# backend/tests/test_music_library_scope_and_stats.py
"""Four derivations the UI reads and nothing executed: the genre list, the
per-storage counts, the album count on a storage button, and the playhead a
reconnecting client is handed.

Each had 0 % of its body run, and each is mocked away by the test of its own
consumer — the route test replaces `source.genres_in_scope`, the storages test
replaces `libraries.stats`, and nothing calls `refresh_metadata` at all.

What breaks when they fail:

* **`genres_in_scope`** exists because Navidrome accepts ``musicFolderId`` on
  getAlbumList2/getArtists/search3/getSongsByGenre but **not** on getGenres,
  which answers with every genre in the catalog whatever it is asked. Left
  underived, the genre list offers a genre that belongs to another storage
  space and the drill-down — which *is* scoped — opens empty.
* **`NavidromeLibraryService.stats`** is the track and missing-file count on
  every storage button, and its album count where no merged one is known; it
  is an agreement between two files that nothing pins: it keys by the
  library's ``path`` and `storages_with_stats` looks it up by ``mountpoint``.
  Measured on this appliance 2026-08-25, the two agree
  (``/media/milo/nas-leo-d7992dfe``, 2403 songs / 155 albums / 16 missing).
  It is also the only honest progress figure while a scan runs: Subsonic's
  global count does not move until a scan ends.
* **`merged_album_count`** puts the grid's album count on the button, a
  multi-disc set counted once. Left to Navidrome's native count, the button
  named more albums than the grid it opens lists; read by a walk in the push,
  it delayed every storage change behind a whole-catalog read.
* **`refresh_metadata`** is the public ABC method `state.refresh_active_view`
  reaches (through `refresh_when_idle`) on every WebSocket handshake and
  GET /api/audio/state. It re-reads the live playhead so a reconnecting browser
  tab, or Milo-Mac, is handed an anchor where playback actually is rather than
  where the last reading left it.
"""
import asyncio

import pytest
from unittest.mock import AsyncMock, Mock

from backend.config.constants import MUSIC_LIBRARY_MOUNT_ROOT
from backend.shared.mpv_audio_source import MpvAudioSource
from backend.sources.music_library.disc_merge import merge_albums
from backend.sources.music_library.libraries import NavidromeLibraryService
from backend.sources.music_library.source import MusicLibrarySource
from backend.tests.golden.harness import settle
from backend.tests.test_mpv_sessions import LibraryRig

NAS = "/media/milo/nas-leo-d7992dfe"
KEY = "/media/milo/IPOD"


# =============================================================================
# genres_in_scope
# =============================================================================

@pytest.fixture
def source():
    src = MusicLibrarySource({"mpv_socket": "/tmp/test-music-library-ipc.sock"})
    src._service_manager = Mock()
    return src


def albums(*records):
    """Stand in for the scope's own album catalog, already merged and cached."""
    return AsyncMock(return_value=list(records))


class TestGenresAreDerivedFromTheScope:

    async def test_only_the_genres_of_the_scopes_albums_are_offered(self, source):
        source.get_merged_albums = albums(
            {"genres": [{"name": "Techno"}], "songCount": 10},
            {"genres": [{"name": "Jazz"}], "songCount": 4},
        )

        genres = await source.genres_in_scope([2])

        assert [g["value"] for g in genres] == ["Jazz", "Techno"]
        source.get_merged_albums.assert_awaited_once_with([2])

    async def test_an_album_with_several_genres_lands_in_each(self, source):
        source.get_merged_albums = albums(
            {"genres": [{"name": "Techno"}, {"name": "Ambient"}], "songCount": 6},
        )

        assert {g["value"] for g in await source.genres_in_scope([2])} == {
            "Techno", "Ambient",
        }

    async def test_the_single_genre_field_is_read_when_the_list_is_absent(self, source):
        """Navidrome's album payload carries `genres` on modern records and a
        lone `genre` on older ones; a release tagged only the old way would
        otherwise vanish from the list while its albums stay browsable."""
        source.get_merged_albums = albums({"genre": "Rock", "songCount": 3})

        assert [g["value"] for g in await source.genres_in_scope([2])] == ["Rock"]

    async def test_the_single_field_is_a_fallback_and_never_the_answer(self, source):
        """A record carrying both is read from the list. The single field holds
        one name, so preferring it drops every other genre of a multi-genre
        album — and the two cases have to differ for a test to tell them apart."""
        source.get_merged_albums = albums(
            {
                "genres": [{"name": "Techno"}, {"name": "Ambient"}],
                "genre": "Techno",
                "songCount": 5,
            },
        )

        assert await source.genres_in_scope([2]) == [
            {"value": "Ambient", "songCount": 5, "albumCount": 1},
            {"value": "Techno", "songCount": 5, "albumCount": 1},
        ]

    async def test_an_album_repeating_one_genre_is_counted_once(self, source):
        """A multi-disc set merged from two records can list the same genre
        twice; counting it twice inflates the album count under the name."""
        source.get_merged_albums = albums(
            {"genres": [{"name": "Jazz"}, {"name": "Jazz"}], "songCount": 8},
        )

        assert await source.genres_in_scope([2]) == [
            {"value": "Jazz", "songCount": 8, "albumCount": 1},
        ]

    async def test_counts_add_up_across_the_albums_of_one_genre(self, source):
        source.get_merged_albums = albums(
            {"genres": [{"name": "Jazz"}], "songCount": 8},
            {"genres": [{"name": "Jazz"}], "songCount": 4},
        )

        assert await source.genres_in_scope([2]) == [
            {"value": "Jazz", "songCount": 12, "albumCount": 2},
        ]

    async def test_a_blank_or_missing_genre_is_not_a_genre(self, source):
        """A tag of spaces would otherwise render as an unnamed row that opens
        on nothing."""
        source.get_merged_albums = albums(
            {"genres": [{"name": "   "}, {"name": None}, {"name": ""}], "songCount": 2},
            {"songCount": 3},
        )

        assert await source.genres_in_scope([2]) == []

    async def test_an_album_with_no_track_count_still_names_its_genre(self, source):
        """Which genres exist is the part a tap depends on; the count is
        decoration."""
        source.get_merged_albums = albums({"genres": [{"name": "Jazz"}]})

        assert await source.genres_in_scope([2]) == [
            {"value": "Jazz", "songCount": 0, "albumCount": 1},
        ]

    async def test_an_unreachable_catalog_offers_no_genres(self, source):
        source.get_merged_albums = albums()

        assert await source.genres_in_scope([2]) == []


# =============================================================================
# get_artist_index — the album count an artist row shows
# =============================================================================

class TestArtistRowsCountAsTheArtistPageLists:
    """The Artists tab and the search read an artist's ``albumCount`` from the
    scope's index; the artist page lists its albums through ``merge_albums``.
    Navidrome counts each disc of a split "… CD 1"/"CD 2" set, so left as it
    answers, the row said "3 albums" over a page that lists 2."""

    CATALOG = [
        {"id": "c1", "name": "At Carnegie Hall CD 1", "artistId": "ar-1"},
        {"id": "c2", "name": "At Carnegie Hall CD 2", "artistId": "ar-1"},
        {"id": "kb", "name": "Kind of Blue", "artistId": "ar-1"},
        {"id": "so", "name": "Solo", "artistId": "ar-2"},
    ]

    @pytest.fixture
    def navidrome(self, source):
        """Navidrome as it answers getArtists and getAlbumList2: one album per
        disc, the scope's catalog in a single page."""
        client = Mock()
        client.get_artists = AsyncMock(return_value=[{"name": "D", "artist": [
            {"id": artist_id, "albumCount": sum(a["artistId"] == artist_id for a in self.CATALOG)}
            for artist_id in ("ar-1", "ar-2")
        ]}])
        client.get_album_list = AsyncMock(return_value=list(self.CATALOG))
        source.get_navidrome_client = AsyncMock(return_value=client)
        return client

    async def test_a_merged_set_counts_once(self, source, navidrome):
        for _ in range(2):  # the second from the cache: never folded twice
            index = await source.get_artist_index([2])
            counts = {artist["id"]: artist["albumCount"] for artist in index[0]["artist"]}
            assert counts == {
                artist_id: len(merge_albums(
                    [a for a in self.CATALOG if a["artistId"] == artist_id]
                ))
                for artist_id in ("ar-1", "ar-2")
            }
        assert counts["ar-1"] == 2  # non-trivial: the raw count was 3
        navidrome.get_artists.assert_awaited_once()
        # The catalog folded is the scope's own, not another storage space's.
        scope = navidrome.get_artists.await_args.args[0]
        assert navidrome.get_album_list.await_args_list
        assert all(
            call.args[0] == scope for call in navidrome.get_album_list.await_args_list
        )


# =============================================================================
# NavidromeLibraryService.stats
# =============================================================================

class TestPerStorageCounts:

    @pytest.fixture
    def admin(self):
        client = Mock()
        client.list_libraries = AsyncMock(return_value=[])
        return client

    @pytest.fixture
    def service(self, admin):
        svc = NavidromeLibraryService(AsyncMock())
        svc._get_admin = AsyncMock(return_value=admin)
        return svc

    async def test_counts_are_keyed_by_the_path_a_mountpoint_is(self, service, admin):
        """`storages_with_stats` looks these up by mountpoint, so the key has to
        be the library's path — measured equal on this unit."""
        admin.list_libraries = AsyncMock(return_value=[
            {"id": 2, "name": "NAS-Leo", "path": NAS,
             "totalSongs": 2403, "totalAlbums": 155, "totalMissingFiles": 16},
        ])

        assert await service.stats() == {
            NAS: {"track_count": 2403, "album_count": 155, "missing_count": 16},
        }

    async def test_a_library_that_reports_no_counters_reads_as_zero(self, service, admin):
        """A library created seconds ago has not been scanned; its record omits
        the counters, and a None on a storage button is a rendering fault."""
        admin.list_libraries = AsyncMock(return_value=[
            {"id": 7, "name": "iPod", "path": KEY,
             "totalSongs": None, "totalAlbums": None, "totalMissingFiles": None},
        ])

        assert await service.stats() == {
            KEY: {"track_count": 0, "album_count": 0, "missing_count": 0},
        }

    async def test_a_library_with_no_path_is_skipped_rather_than_keyed_on_none(
        self, service, admin
    ):
        admin.list_libraries = AsyncMock(return_value=[
            {"id": 1, "name": "Default"},
            {"id": 2, "name": "NAS-Leo", "path": NAS, "totalSongs": 5},
        ])

        assert list(await service.stats()) == [NAS]

    async def test_an_unreachable_navidrome_shows_no_counts_rather_than_failing(
        self, service, admin
    ):
        """None means "could not ask" — the storage buttons render without their
        counts instead of the whole settings page erroring."""
        admin.list_libraries = AsyncMock(return_value=None)

        assert await service.stats() == {}

    async def test_no_admin_client_yet_shows_no_counts(self, service):
        service._get_admin = AsyncMock(return_value=None)

        assert await service.stats() == {}


class TestTheStorageButtonCountsAsTheGrid:
    """A storage button's album count is the album grid's, multi-disc sets merged.

    Navidrome's native ``totalAlbums`` counts every disc of a split
    "… CD 1"/"CD 2" set; the grid pages over ``merge_albums``. Measured
    2026-11-25 on a 10 069-track iPod: the button said 764, the grid listed 753.
    Navidrome is the outside world here — its native library records and
    getAlbumList2 — and the grid is read through its own route. The storages
    push is the hook the share service is handed: it is how a count that moved
    off the push reaches the screen.
    """

    LIBRARY = 6
    UUID = "AAAA-1111"
    CATALOG = [
        {"id": "b1", "name": "The Complete Sessions CD 1", "artistId": "ar-1"},
        {"id": "b2", "name": "The Complete Sessions CD 2", "artistId": "ar-1"},
        {"id": "kb", "name": "Kind of Blue", "artistId": "ar-1"},
    ]

    @pytest.fixture
    def navidrome(self, source):
        """Navidrome counting one album per disc natively, and answering the
        alphabetical walk with ``self.catalog`` — or holding a page until
        ``self.gate`` opens, or failing it while ``self.unanswered``."""
        self.catalog = list(self.CATALOG)
        self.gate = None
        self.unanswered = False

        async def page(scope, list_type, size, offset, **_):
            # Read as the request arrives: a page held at the gate answers
            # with the catalog as it was when it was asked for.
            rows = list(self.catalog[offset:offset + size]) if scope == [self.LIBRARY] else []
            if self.gate is not None:
                await self.gate.wait()
            return None if self.unanswered else rows

        client = Mock()
        client.get_album_list = AsyncMock(side_effect=page)
        source.get_navidrome_client = AsyncMock(return_value=client)
        source.broadcast_storages = AsyncMock()
        admin = Mock()
        admin.list_libraries = AsyncMock(return_value=[
            {"id": self.LIBRARY, "name": "iPod", "path": KEY,
             "totalSongs": 30, "totalAlbums": len(self.CATALOG),
             "totalMissingFiles": 0},
        ])
        shares = source.shares
        shares._libraries._get_admin = AsyncMock(return_value=admin)
        shares._libraries.library_id = Mock(side_effect={KEY: self.LIBRARY}.get)
        shares._data.list_shares = AsyncMock(return_value=[])
        shares._data.get_known_usb = AsyncMock(return_value={
            self.UUID: {"name": "iPod", "label": "IPOD", "mountpoint": KEY},
        })
        shares._storage.get_mounted_share_ids = Mock(return_value=set())
        self.plug(source)
        return client

    def plug(self, source):
        source.shares._storage.get_usb_mounts = Mock(return_value=[
            {"uuid": self.UUID, "label": "IPOD", "mountpoint": KEY},
        ])

    def unplug(self, source):
        source.shares._storage.get_usb_mounts = Mock(return_value=[])

    async def grid(self, source):
        """The alphabetical grid of this storage space, as the route serves it."""
        from backend.sources.music_library.routes import get_albums

        page = await get_albums(
            source=source, type="alphabeticalByName", size=500, offset=0,
            genre=None, from_year=None, to_year=None, library_id=self.LIBRARY,
        )
        return page["albums"]

    async def button(self, source):
        (entry,) = await source.shares.storages_with_stats()
        assert entry["library_id"] == self.LIBRARY
        return entry["album_count"]

    async def test_a_box_set_counts_once_on_the_button_as_on_the_grid(
        self, source, navidrome
    ):
        """Counted off the push, then pushed again: the screen ends on the
        grid's count without a refetch."""
        await self.button(source)
        await settle()

        source.broadcast_storages.assert_awaited_once()
        assert await self.button(source) == len(await self.grid(source))
        # Non-trivial: Navidrome's own count is one album per disc.
        assert len(await self.grid(source)) == len(self.CATALOG) - 1

    async def test_the_storage_list_never_waits_on_a_catalog_walk(self, source, navidrome):
        """Every mount and unmount drops the album cache and then pushes the
        storages, whose session end (a pulled key's) and post-mount scan wait
        for that push: a walk in it put a whole-catalog read of every other
        mounted space in front of both."""
        self.gate = asyncio.Event()

        listing = asyncio.ensure_future(source.shares.storages_with_stats())
        await settle()

        assert listing.done()
        (entry,) = listing.result()
        assert entry["album_count"] == len(self.CATALOG)  # Navidrome's, for now
        self.gate.set()
        await settle()
        source.broadcast_storages.assert_awaited_once()
        assert await self.button(source) == len(self.CATALOG) - 1

    async def test_callers_of_one_scope_share_one_walk(self, source, navidrome):
        """The library view asks for the storages and the grid together: both
        missing the cache paged the same catalog twice, against a Navidrome
        busiest during the first index."""
        self.gate = asyncio.Event()

        listing = asyncio.ensure_future(self.button(source))  # starts the count's walk
        grid = asyncio.ensure_future(self.grid(source))
        await settle()
        self.gate.set()
        await settle()

        assert listing.done() and len(grid.result()) == len(self.CATALOG) - 1
        # One page holds the catalog, so one walk is one call.
        assert navidrome.get_album_list.await_count == 1

    async def test_a_walk_begun_before_a_change_is_not_the_count_after_it(
        self, source, navidrome
    ):
        """A mount mid-walk drops the cache; the walk under way read the catalog
        from before it and must not become the count, nor the grid's cache."""
        self.gate = asyncio.Event()
        listing = asyncio.ensure_future(self.button(source))
        await settle()

        self.catalog.append({"id": "nw", "name": "New Arrival", "artistId": "ar-2"})
        source.invalidate_album_cache()  # what _sync_libraries does on a mount
        self.gate.set()
        await settle()

        assert listing.done()
        assert await self.button(source) == len(self.catalog) - 1

    async def test_a_cached_catalog_is_counted_without_walking_it_again(
        self, source, navidrome
    ):
        """Every scan poll pushes the storages: a walk per push is a walk every
        3 s, to a Navidrome busy indexing."""
        expected = len(await self.grid(source))
        walks = navidrome.get_album_list.await_count

        assert await self.button(source) == expected
        await settle()
        assert navidrome.get_album_list.await_count == walks
        source.broadcast_storages.assert_not_awaited()

    async def test_an_unplugged_key_keeps_the_count_it_last_had(self, source, navidrome):
        """Navidrome keeps an unplugged key's index, and its button its count —
        the merged one, from memory, with no walk for a space that is away."""
        expected = len(await self.grid(source))
        source.invalidate_album_cache()  # a scan, or the unplug itself
        self.unplug(source)
        walks = navidrome.get_album_list.await_count

        assert await self.button(source) == expected
        await settle()
        assert navidrome.get_album_list.await_count == walks

    async def test_a_key_never_walked_keeps_navidromes_count_while_away(
        self, source, navidrome
    ):
        """No merged count known and nothing to read it from: Navidrome's own
        figure rather than a walk of a space nobody can open."""
        self.unplug(source)

        assert await self.button(source) == len(self.CATALOG)
        await settle()
        navidrome.get_album_list.assert_not_awaited()

    async def test_a_mounted_key_whose_walk_fails_shows_navidromes_fresh_count(
        self, source, navidrome
    ):
        """A merged count from before a rescan is not the catalog's: once the
        walk that would replace it stops short, Navidrome's fresh count is the
        better answer for a space that is mounted."""
        await self.grid(source)
        source.invalidate_album_cache()  # a rescan
        self.unanswered = True

        await self.button(source)
        await settle()

        source.broadcast_storages.assert_awaited_once()
        assert await self.button(source) == len(self.CATALOG)

    async def test_a_refused_credential_drops_the_client_and_is_said_once(
        self, source, navidrome, caplog
    ):
        """The count is decoration: the storage list, which the whole library
        view is built from, must not fail for it — nor log on every push of a
        scan. The client goes, as at the routes, so the next read takes a
        rotated credential."""
        from backend.sources.music_library.navidrome_client import NavidromeAuthError

        navidrome.get_album_list.side_effect = NavidromeAuthError("rejected")
        held = Mock(close=AsyncMock())
        source._navidrome = held

        for _ in range(2):
            assert await self.button(source) == len(self.CATALOG)
            await settle()

        held.close.assert_awaited_once()
        refusals = [r for r in caplog.records if "left as Navidrome counts it" in r.getMessage()]
        assert len(refusals) == 1


class TestOnlyOurOwnLibrariesAreManaged:
    """`_converge` deletes any library whose path is no longer a mount — this is
    the predicate that decides what "ours" means."""

    @pytest.fixture
    def service(self):
        return NavidromeLibraryService(AsyncMock())

    def test_a_library_under_the_mount_root_is_ours(self, service):
        assert service._is_managed(f"{MUSIC_LIBRARY_MOUNT_ROOT}/nas-leo") is True

    def test_navidromes_own_default_library_is_left_alone(self, service):
        """Navidrome refuses to delete library 1 ("library with ID 1 cannot be
        deleted"), so treating it as ours is a delete retried for ever. Measured
        on this unit: its path is /var/lib/milo/navidrome/default-library."""
        assert service._is_managed("/var/lib/milo/navidrome/default-library") is False

    @pytest.mark.parametrize("path", [str(MUSIC_LIBRARY_MOUNT_ROOT), "/media", "/"])
    def test_a_library_covering_the_mount_root_is_named_in_the_log(
        self, service, caplog, path
    ):
        """A library at or above the mount root double-indexes every storage
        space, under a name that belongs to none of them.

        The assertion is the **warning**, and deliberately so: measured, this
        branch cannot change the answer. Its fallback,
        ``path.startswith(f"{root}/")``, is already False for the mount root and
        for every ancestor of it — a strict ancestor is shorter than
        ``root + "/"`` and so can never start with it. So the branch exists to
        tell an operator where to look (provisioning/navidrome.sh MusicFolder), and a
        test asserting only `is False` would pass with the whole branch gone."""
        with caplog.at_level("WARNING", logger="source.music_library.libraries"):
            assert service._is_managed(path) is False

        assert any(
            "double-index" in record.getMessage() for record in caplog.records
        ), [r.getMessage() for r in caplog.records]

    def test_a_path_that_merely_starts_like_the_root_is_not_ours(self, service):
        """`/media/milo-backup` is not inside `/media/milo`."""
        assert service._is_managed(f"{MUSIC_LIBRARY_MOUNT_ROOT}-backup/x") is False


# =============================================================================
# refresh_metadata — the playhead a reconnecting client is handed
# =============================================================================

class TestRefreshMetadata:
    """Driven the way the consumer drives it: `refresh_active_view` (the
    WebSocket handshake, GET /api/audio/state) on a real state machine, mpv
    simulated as measured."""

    TRACK = {"id": "s-1", "title": "Track", "duration": 240}

    @pytest.fixture
    def rig(self, monkeypatch):
        rig = LibraryRig(monkeypatch)
        rig.mpv.durations["id=s-1&"] = 240.0
        return rig

    async def _playing(self, rig):
        await rig.select()
        await rig.command("play_context", {"tracks": [self.TRACK]})
        await rig.tick()

    @staticmethod
    def _count_reads(rig):
        reads = []
        real = rig.mpv.get_property

        async def counted(name, timeout=None):
            reads.append(name)
            return await real(name, timeout)

        rig.mpv.get_property = counted
        return reads

    async def test_the_live_playhead_replaces_the_last_tick(self, rig):
        """The last reading is seconds old; a client that reconnects mid-track
        would otherwise be handed the anchor the last reading left."""
        await self._playing(rig)
        rig.mpv.playhead(97.4)
        assert rig.state()["session"]["position"]["ms"] == 0

        assert await rig.machine.refresh_active_view() is True

        live = rig.state()["session"]
        assert live["position"]["ms"] == 97_400
        assert live["duration_ms"] == 240_000

    async def test_a_pause_mpv_announced_is_what_the_handshake_hands_out(self, rig):
        """mpv is the one that knows: a pause it announced (whoever asked for
        it) is what a reconnecting client is given, not a playing track."""
        await self._playing(rig)
        await rig.mpv.set_property("pause", True)
        await settle()

        await rig.machine.refresh_active_view()

        assert rig.state()["session"]["phase"] == "paused"

    async def test_a_buffering_stream_keeps_its_loading_phase(self, rig, monkeypatch):
        """mpv reports pause=False before the stream is actually up, so trusting
        it while buffering makes a track that has not started look like it is
        playing — and the progress bar run ahead of the sound."""
        monkeypatch.setattr(MpvAudioSource, "STALL_TIMEOUT_S", 60.0)
        rig.mpv.auto_open = False
        await rig.select()
        await rig.command("play_context", {"tracks": [self.TRACK]})

        await rig.machine.refresh_active_view()

        assert rig.state()["session"]["phase"] == "loading"

    async def test_properties_mpv_cannot_answer_leave_the_last_known_values(self, rig):
        """mpv answers None between tracks; overwriting with it would show 0:00
        of 0:00 on a track that is playing."""
        await self._playing(rig)
        rig.mpv.playhead(10)
        await rig.machine.refresh_active_view()
        rig.mpv.position = None
        rig.mpv.durations.clear()
        rig.mpv.default_duration = None

        assert await rig.machine.refresh_active_view() is True

        live = rig.state()["session"]
        assert (live["position"]["ms"], live["duration_ms"]) == (10_000, 240_000)
        assert live["phase"] == "playing"

    async def test_nothing_is_read_when_nothing_plays(self, rig):
        """No session, no playhead to hand out: the handshake must not wait on
        mpv for nothing."""
        await rig.select()
        reads = self._count_reads(rig)

        assert await rig.machine.refresh_active_view() is False
        assert reads == []

    async def test_nothing_is_read_when_mpv_is_gone(self, rig):
        """The source is not started: there is no mpv to ask."""
        assert await rig.source.refresh_metadata() is False

    async def test_nothing_is_read_over_a_dead_ipc_link(self, rig):
        """A get_property on a disconnected socket is what the handshake would
        block on."""
        await self._playing(rig)
        await rig.mpv.dies()
        await settle()
        reads = self._count_reads(rig)

        assert await rig.machine.refresh_active_view() is False
        assert reads == []
