# backend/tests/test_music_library_scope_and_stats.py
"""Three derivations the UI reads and nothing executed: the genre list, the
per-storage counts, and the playhead a reconnecting client is handed.

Each had 0 % of its body run, and each is mocked away by the test of its own
consumer — the route test replaces `source.genres_in_scope`, the storages test
replaces `libraries.stats`, and nothing calls `refresh_metadata` at all.

What breaks when they fail:

* **`genres_in_scope`** exists because Navidrome accepts ``musicFolderId`` on
  getAlbumList2/getArtists/search3/getSongsByGenre but **not** on getGenres,
  which answers with every genre in the catalog whatever it is asked. Left
  underived, the genre list offers a genre that belongs to another storage
  space and the drill-down — which *is* scoped — opens empty.
* **`NavidromeLibraryService.stats`** is the count on every storage button, and
  it is an agreement between two files that nothing pins: it keys by the
  library's ``path`` and `storages_with_stats` looks it up by ``mountpoint``.
  Measured on this appliance 2026-08-25, the two agree
  (``/media/milo/nas-leo-d7992dfe``, 2403 songs / 155 albums / 16 missing).
  It is also the only honest progress figure while a scan runs: Subsonic's
  global count does not move until a scan ends.
* **`refresh_metadata`** is the public ABC method `state.refresh_active_view`
  reaches (through `refresh_when_idle`) on every WebSocket handshake and
  GET /api/audio/state. It re-reads the live playhead so a reconnecting browser
  tab, or Milo-Mac, is handed an anchor where playback actually is rather than
  where the last reading left it.
"""
import pytest
from unittest.mock import AsyncMock, Mock

from backend.config.constants import MUSIC_LIBRARY_MOUNT_ROOT
from backend.shared.mpv_audio_source import MpvAudioSource
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
        svc = NavidromeLibraryService()
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


class TestOnlyOurOwnLibrariesAreManaged:
    """`_converge` deletes any library whose path is no longer a mount — this is
    the predicate that decides what "ours" means."""

    @pytest.fixture
    def service(self):
        return NavidromeLibraryService()

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
