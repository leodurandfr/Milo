"""
Tests for CdDataService's cover-art cache.

What breaks when these fail: a jacket interrupted mid-write is served
forever. `fetch_cover` returns early on `os.path.exists(cover_path)` and
`get_cover_path` only tests existence, so nothing ever re-downloads a partial
file — the operator's only escape hatch is `rm -rf /var/lib/milo/cd_covers/`.
"""
import os
from pathlib import Path

import musicbrainzngs
import pytest

from backend.sources.cd.data import CdDataService

PAYLOAD = b"\xff\xd8\xff\xe0" + b"J" * 4096


@pytest.fixture
def service(tmp_path, monkeypatch):
    svc = CdDataService()
    svc._covers_dir = str(tmp_path)
    monkeypatch.setattr(
        musicbrainzngs, "get_image_front", lambda mbid, size="500": PAYLOAD
    )
    return svc


def _known(service, disc_id, *, release_mbid="release-mbid", release_group_mbid=""):
    """A cache entry as lookup_metadata writes it for a disc MusicBrainz knows."""
    service._cache[disc_id] = {
        "album": "A", "artist": "B", "year": "2000",
        "release_mbid": release_mbid, "release_group_mbid": release_group_mbid,
        "cover_missing": False, "tracks": [],
    }


async def test_a_cover_becomes_visible_only_through_a_rename(service, tmp_path, monkeypatch):
    """
    The destination must be created by os.replace from a fully written
    sibling, never by writing into it directly — the same shape _save_data
    already uses for cd_data.json.
    """
    renames = []
    real_replace = os.replace

    def spy(src, dst):
        renames.append((str(src), str(dst), os.path.exists(dst), Path(src).read_bytes()))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    _known(service, "disc-1")

    assert await service.fetch_cover("disc-1") == "/api/cd/cover/disc-1"

    assert len(renames) == 1, "the cover was written into its final path directly"
    src, dst, dst_existed_before, staged = renames[0]
    assert src.endswith(".tmp")
    assert dst == str(tmp_path / "disc-1.jpg")
    assert not dst_existed_before
    assert staged == PAYLOAD, "the rename published a partially written file"

    assert Path(dst).read_bytes() == PAYLOAD
    assert list(tmp_path.glob("*.tmp")) == []


async def test_an_interrupted_write_leaves_no_servable_cover(service, tmp_path, monkeypatch):
    """
    Kill the publish step and the cache must come out empty, so the next
    lookup re-downloads instead of serving a truncated jacket.
    """
    def die(src, dst):
        raise OSError("No space left on device")

    monkeypatch.setattr(os, "replace", die)
    _known(service, "disc-2")

    with pytest.raises(OSError):
        await service.fetch_cover("disc-2")
    assert service.get_cover_path("disc-2") is None


class TestCoverPathLookup:
    """`get_cover_path` — the on-disk cover for a disc, or nothing.

    Green in the Lot A eviscration sweep. Consumer: `sources/cd/routes.py`,
    whose only job is cover art; a None is an expected 404 there, not a
    failure. Neutralised it answers None for every disc, so a disc whose cover
    was fetched and cached shows the placeholder for ever -- and no test moved.
    """

    def test_a_cached_cover_is_found_by_its_disc_id(self, service, tmp_path):
        (tmp_path / "disc-42.jpg").write_bytes(b"\xff\xd8\xff")
        assert service.get_cover_path("disc-42") == str(tmp_path / "disc-42.jpg")

    def test_a_disc_with_no_cached_cover_answers_nothing(self, service):
        assert service.get_cover_path("disc-with-no-cover") is None

    def test_the_lookup_is_per_disc_and_not_a_directory_check(self, service, tmp_path):
        (tmp_path / "disc-42.jpg").write_bytes(b"\xff\xd8\xff")
        assert service.get_cover_path("disc-43") is None


# =============================================================================
# The TOC, the MusicBrainz lookup, and the on-disk cache.
#
# This host IS the appliance and there is a disc in its drive, so
# `never_the_real_drive` below makes the real primitives on /dev/sr0 RAISE for
# the whole module (the drive itself is tests/test_cd_drive.py's).
#
# `discid.read()` in particular spins the disc up. It is never called.
# =============================================================================
import asyncio
import fcntl
import json
from unittest.mock import Mock, patch

from backend.config.constants import CD_DEVICE
from backend.sources.cd.drive import CDROM_DRIVE_STATUS
from backend.sources.cd.models import DiscInfo

@pytest.fixture(autouse=True)
def never_the_real_drive(monkeypatch):
    """/dev/sr0 is off limits for this module; so is spinning the disc."""
    real_open, real_ioctl = os.open, fcntl.ioctl

    def open_(path, *a, **k):
        if str(path) == CD_DEVICE:
            raise AssertionError("a test opened the appliance's real CD drive")
        return real_open(path, *a, **k)

    def ioctl_(fd, request, *a, **k):
        if request == CDROM_DRIVE_STATUS:
            raise AssertionError("a test issued a real CDROM_DRIVE_STATUS")
        return real_ioctl(fd, request, *a, **k)

    monkeypatch.setattr(os, "open", open_)
    monkeypatch.setattr(fcntl, "ioctl", ioctl_)


class TestReadingTheToc:
    """The TOC is where every sector offset comes from: `_sector_offsets` drives
    seek, next/prev and the LBA the reader is started at."""

    def test_the_toc_becomes_one_based_tracks_with_their_start_sectors(self, service):
        disc = Mock(id="xyz", toc_string="1 3 190000 150 20000 40000", length=190000)
        disc.tracks = [Mock(seconds=200, offset=150),
                       Mock(seconds=150, offset=20000),
                       Mock(seconds=180, offset=40000)]
        with patch("discid.read", return_value=disc) as read:
            got = service._read_disc_sync()

        read.assert_called_once_with(CD_DEVICE)
        disc_id, toc, tracks, end_lba = got
        assert (disc_id, toc, end_lba) == ("xyz", "1 3 190000 150 20000 40000", 190000)
        assert tracks == [
            {"number": 1, "duration": 200, "offset": 150},
            {"number": 2, "duration": 150, "offset": 20000},
            {"number": 3, "duration": 180, "offset": 40000},
        ]

    async def test_an_unreadable_disc_answers_nothing_rather_than_raising(self, service):
        """A disc the drive cannot read must leave the watcher's loop running —
        it retries, and TOC_READ_ATTEMPTS bounds it."""
        with patch("discid.read", side_effect=Exception("cannot read TOC")):
            assert await service.read_disc() is None


class TestTheMusicBrainzQuery:
    """Two queries, in this order: the disc ID (exact), then the TOC (fuzzy).

    The fallback is the point. A pressing whose disc ID nobody has submitted is
    the common case for anything not mainstream, and the TOC query is what finds
    it — the tracks' offsets match even when the disc ID does not.
    """

    def _mb(self, monkeypatch, *, exact, fuzzy):
        import musicbrainzngs
        calls = []

        def get_releases_by_discid(disc_id, includes=None, toc=None):
            calls.append({"disc_id": disc_id, "toc": toc, "includes": includes})
            answer = fuzzy if toc else exact
            if isinstance(answer, Exception):
                raise answer
            return answer

        monkeypatch.setattr(musicbrainzngs, "get_releases_by_discid",
                            get_releases_by_discid)
        return calls

    RELEASE = {
        "id": "rel-1",
        "title": "Spaces",
        "release-group": {"id": "rg-1"},
        "artist-credit": [{"artist": {"name": "Nils Frahm"}}],
        "date": "2013-11-15",
        "medium-list": [{"track-list": [
            {"recording": {"title": "An Aborted Beginning", "length": "83000"}},
            {"recording": {"title": "Says", "length": "521000"}},
        ]}],
    }

    def test_an_exact_disc_id_match_never_pays_for_the_fuzzy_query(self, service, monkeypatch):
        calls = self._mb(monkeypatch,
                         exact={"disc": {"release-list": [self.RELEASE]}},
                         fuzzy=None)
        album, artist, year, rel, rg, tracks = service._lookup_musicbrainz_sync("d1", "toc")

        assert [c["toc"] for c in calls] == [None], "the TOC query ran despite an exact hit"
        assert (album, artist, year, rel, rg) == ("Spaces", "Nils Frahm", "2013", "rel-1", "rg-1")
        assert [t["title"] for t in tracks] == ["An Aborted Beginning", "Says"]

    def test_an_unknown_disc_id_falls_back_to_the_toc(self, service, monkeypatch):
        import musicbrainzngs
        calls = self._mb(
            monkeypatch,
            exact=musicbrainzngs.ResponseError("404"),
            fuzzy={"disc": {"release-list": [self.RELEASE]}},
        )
        result = service._lookup_musicbrainz_sync("d1", "1 2 190000 150 20000")

        assert [c["toc"] for c in calls] == [None, "1 2 190000 150 20000"]
        assert result[0] == "Spaces"

    def test_a_disc_neither_query_knows_answers_nothing(self, service, monkeypatch):
        import musicbrainzngs
        self._mb(monkeypatch,
                 exact=musicbrainzngs.ResponseError("404"),
                 fuzzy=musicbrainzngs.ResponseError("404"))
        assert service._lookup_musicbrainz_sync("d1", "toc") is None

    def test_an_answer_carrying_no_release_falls_through_to_the_toc(self, service, monkeypatch):
        """A 200 with an empty release-list is not an error, and taking it for
        one would leave the fuzzy query unrun for every such disc."""
        calls = self._mb(monkeypatch,
                         exact={"disc": {"release-list": []}},
                         fuzzy={"release-list": [self.RELEASE]})
        result = service._lookup_musicbrainz_sync("d1", "toc")

        assert len(calls) == 2
        assert result[0] == "Spaces"


class TestParsingARelease:
    """What MusicBrainz answers is the outside world, and every field here is
    optional in its schema — a release with no artist-credit, no date or no
    medium is a routine answer, not a corrupt one."""

    def test_the_fields_the_player_shows_are_carried_across(self, service):
        album, artist, year, rel, rg, tracks = service._parse_release(
            TestTheMusicBrainzQuery.RELEASE)
        assert (album, artist, year, rel, rg) == \
            ("Spaces", "Nils Frahm", "2013", "rel-1", "rg-1")

    def test_a_duration_arrives_in_milliseconds_and_is_shown_in_seconds(self, service):
        _a, _ar, _y, _r, _rg, tracks = service._parse_release(
            TestTheMusicBrainzQuery.RELEASE)
        # 83000 ms and 521000 ms — a track shown as 83 minutes is the tell.
        assert [t["duration"] for t in tracks] == [83, 521]

    def test_a_release_with_nothing_filled_in_still_parses(self, service):
        album, artist, year, rel, rg, tracks = service._parse_release({})
        assert (album, artist, year, rel, rg) == \
            ("Unknown Album", "Unknown Artist", "", "", "")
        assert tracks == []

    def test_a_recording_with_no_title_is_numbered(self, service):
        _a, _ar, _y, _r, _rg, tracks = service._parse_release(
            {"medium-list": [{"track-list": [{"recording": {}}, {"recording": {}}]}]})
        assert [t["title"] for t in tracks] == ["Track 1", "Track 2"]
        assert [t["duration"] for t in tracks] == [0, 0]

    def test_a_release_under_disc_is_preferred_to_a_bare_release_list(self, service):
        """The disc-keyed list is the one that matched *this* disc; the bare
        list is what the fuzzy TOC query answers."""
        assert service._extract_release({
            "disc": {"release-list": [{"id": "matched"}]},
            "release-list": [{"id": "fuzzy"}],
        }) == {"id": "matched"}

    def test_an_empty_disc_list_does_not_shadow_the_bare_list(self, service):
        assert service._extract_release({
            "disc": {"release-list": []},
            "release-list": [{"id": "fuzzy"}],
        }) == {"id": "fuzzy"}

    def test_a_response_with_no_release_at_all_answers_nothing(self, service):
        assert service._extract_release({"disc": {}}) is None


class TestMergingTitlesOntoTheToc:
    """The TOC is the disc in the tray; MusicBrainz is a guess about it. Track
    count and durations therefore come from the TOC, titles from MusicBrainz."""

    TOC = [{"number": 1, "duration": 200, "offset": 150},
           {"number": 2, "duration": 150, "offset": 20000},
           {"number": 3, "duration": 180, "offset": 40000}]

    def test_toc_durations_win_over_the_catalogue(self, service):
        mb = [{"title": "One", "duration": 999},
              {"title": "Two", "duration": 999},
              {"title": "Three", "duration": 999}]
        merged = service._merge_tracks(mb, self.TOC)
        assert [t["duration"] for t in merged] == [200, 150, 180]
        assert [t["title"] for t in merged] == ["One", "Two", "Three"]

    def test_a_catalogue_entry_shorter_than_the_disc_numbers_the_rest(self, service):
        """A release whose medium is not this disc — a single where the disc is
        the album — must not drop the tracks it does not know about."""
        merged = service._merge_tracks([{"title": "One"}], self.TOC)
        assert [t["title"] for t in merged] == ["One", "Track 2", "Track 3"]
        assert len(merged) == len(self.TOC)

    def test_a_catalogue_entry_longer_than_the_disc_is_cut_to_the_disc(self, service):
        mb = [{"title": f"T{i}"} for i in range(10)]
        assert len(service._merge_tracks(mb, self.TOC)) == 3


class TestLookupMetadata:
    """The one entry point the source calls. Its contract is that it always
    returns a DiscInfo — a disc nobody has heard of still has to play.

    Driven through `musicbrainzngs`, which is the outside world here; the
    service's own lookup and download run for real.
    """

    TOC = [{"number": 1, "duration": 200, "offset": 150},
           {"number": 2, "duration": 150, "offset": 20000}]

    @staticmethod
    def _catalogue(monkeypatch, answer, *, jacket=PAYLOAD):
        """MusicBrainz, as it answers: `answer` may be a release dict or a
        ResponseError, and `jacket` None means the archive has no image."""
        import musicbrainzngs
        calls = []

        def get_releases_by_discid(disc_id, includes=None, toc=None):
            calls.append(toc)
            if isinstance(answer, Exception):
                raise answer
            return answer

        def get_image_front(mbid, size="500"):
            if jacket is None:
                raise musicbrainzngs.ResponseError("404")
            return jacket

        monkeypatch.setattr(musicbrainzngs, "get_releases_by_discid", get_releases_by_discid)
        monkeypatch.setattr(musicbrainzngs, "get_image_front", get_image_front)
        monkeypatch.setattr(musicbrainzngs, "get_release_group_image_front",
                            Mock(side_effect=musicbrainzngs.ResponseError("404")))
        return calls

    async def test_a_cached_disc_is_answered_without_reaching_the_network(
            self, service, tmp_path, monkeypatch):
        calls = self._catalogue(monkeypatch, {"disc": {"release-list": []}})
        service._cache["d1"] = {
            "album": "Spaces", "artist": "Nils Frahm", "year": "2013",
            "release_mbid": "rel-1", "release_group_mbid": "rg-1", "cover_missing": False,
            "tracks": [{"number": 1, "title": "One", "duration": 200},
                       {"number": 2, "title": "Two", "duration": 150}],
        }
        (tmp_path / "d1.jpg").write_bytes(PAYLOAD)
        info = await service.lookup_metadata("d1", "toc", self.TOC)

        assert calls == [], "a cached disc still queried MusicBrainz"
        assert isinstance(info, DiscInfo)
        assert (info.album, info.artist, info.total_duration) == ("Spaces", "Nils Frahm", 350)
        assert info.cover_url == "/api/cd/cover/d1"

    async def test_a_cached_disc_with_no_cover_offers_no_cover_url(self, service):
        """The URL is what the player requests; offered for a disc whose jacket
        was never fetched, every render pays a 404."""
        _known(service, "d1")
        info = await service.lookup_metadata("d1", "toc", [])
        assert info.cover_url is None

    async def test_an_unknown_disc_still_comes_back_playable(self, service, monkeypatch):
        """MusicBrainz reachable but the disc unknown: the fallback carries the
        TOC's own track count and durations, so the disc plays with generic
        names instead of not appearing at all."""
        import musicbrainzngs
        self._catalogue(monkeypatch, musicbrainzngs.ResponseError("404"))
        info = await service.lookup_metadata("d1", "toc", self.TOC)

        assert info.album is None and info.artist is None
        assert [t.title for t in info.tracks] == ["Track 1", "Track 2"]
        assert info.total_duration == 350
        assert info.cover_url is None
        assert "d1" not in service._cache, "a fallback was cached as if it were known"

    async def test_a_catalogue_that_raises_falls_back_instead_of_propagating(self, service, monkeypatch):
        """Not a ResponseError but the socket itself — MusicBrainz down, or no
        internet. `read_disc` already succeeded, so the disc must still play."""
        self._catalogue(monkeypatch, OSError("Network is unreachable"))
        info = await service.lookup_metadata("d1", "toc", self.TOC)
        assert info.track_count == 2 and info.album is None

    async def test_a_found_disc_is_cached_and_persisted_for_the_next_insertion(
            self, service, tmp_path, monkeypatch):
        service._data_file = str(tmp_path / "cd_data.json")
        self._catalogue(monkeypatch,
                        {"disc": {"release-list": [TestTheMusicBrainzQuery.RELEASE]}})
        info = await service.lookup_metadata("d1", "toc", self.TOC)

        assert [t.title for t in info.tracks] == ["An Aborted Beginning", "Says"]
        assert [t.duration for t in info.tracks] == [200, 150], \
            "the catalogue's durations displaced the disc's own"

        on_disk = json.loads((tmp_path / "cd_data.json").read_text())
        entry = on_disk["discs"]["d1"]
        assert entry["album"] == "Spaces"
        assert (entry["release_mbid"], entry["release_group_mbid"]) == ("rel-1", "rg-1"), \
            "a cached disc kept nothing to ask the archive for its jacket later"
        assert "cached_at" in entry

    async def test_the_lookup_never_waits_for_the_archive(self, service, tmp_path, monkeypatch):
        """The caller publishes the disc only once this returns. Measured on
        the unit: an archive answering 500 then looping on its redirect held a
        disc MusicBrainz had named in 0.2 s — and its auto-play — for 97 s."""
        service._data_file = str(tmp_path / "cd_data.json")
        self._catalogue(monkeypatch,
                        {"disc": {"release-list": [TestTheMusicBrainzQuery.RELEASE]}})
        archive = Mock(side_effect=AssertionError("the lookup reached the archive"))
        monkeypatch.setattr(musicbrainzngs, "get_image_front", archive)
        monkeypatch.setattr(musicbrainzngs, "get_release_group_image_front", archive)

        info = await service.lookup_metadata("d1", "toc", self.TOC)

        archive.assert_not_called()
        assert info.album == "Spaces"
        assert info.cover_url is None
        assert list(tmp_path.glob("*.jpg")) == []

    async def test_a_fetched_jacket_is_offered_to_the_player(self, service, tmp_path, monkeypatch):
        service._data_file = str(tmp_path / "cd_data.json")
        self._catalogue(monkeypatch,
                        {"disc": {"release-list": [TestTheMusicBrainzQuery.RELEASE]}})
        await service.lookup_metadata("d1", "toc", self.TOC)

        assert await service.fetch_cover("d1") == "/api/cd/cover/d1"
        assert (tmp_path / "d1.jpg").read_bytes() == PAYLOAD
        info = await service.lookup_metadata("d1", "toc", self.TOC)
        assert info.cover_url == "/api/cd/cover/d1", "the next insertion forgot the jacket"

    async def test_an_unreachable_archive_is_not_remembered_as_no_jacket(
            self, service, tmp_path, monkeypatch):
        """Measured on the unit: one outage cached `has_cover: False` for a
        disc the archive does illustrate, and no later insertion asked again.
        Only the archive's own "none" may be remembered."""
        service._data_file = str(tmp_path / "cd_data.json")
        self._catalogue(monkeypatch,
                        {"disc": {"release-list": [TestTheMusicBrainzQuery.RELEASE]}})
        await service.lookup_metadata("d1", "toc", self.TOC)
        monkeypatch.setattr(musicbrainzngs, "get_image_front",
                            Mock(side_effect=musicbrainzngs.NetworkError("retried 8 times")))

        assert await service.fetch_cover("d1") is None
        assert service._cache["d1"]["cover_missing"] is False

        monkeypatch.setattr(musicbrainzngs, "get_image_front", lambda mbid, size="500": PAYLOAD)
        assert await service.fetch_cover("d1") == "/api/cd/cover/d1"

    async def test_a_disc_whose_jacket_is_missing_is_not_asked_again(
            self, service, tmp_path, monkeypatch):
        """No jacket on the pressing nor the album is a real answer, not a
        failure: remembering it is what stops every insertion re-asking."""
        service._data_file = str(tmp_path / "cd_data.json")
        self._catalogue(monkeypatch,
                        {"disc": {"release-list": [TestTheMusicBrainzQuery.RELEASE]}},
                        jacket=None)
        info = await service.lookup_metadata("d1", "toc", self.TOC)
        assert await service.fetch_cover("d1") is None
        assert info.album == "Spaces"

        archive = Mock(side_effect=AssertionError("asked again for a known absence"))
        monkeypatch.setattr(musicbrainzngs, "get_image_front", archive)
        assert await service.fetch_cover("d1") is None
        archive.assert_not_called()
        on_disk = json.loads((tmp_path / "cd_data.json").read_text())
        assert on_disk["discs"]["d1"]["cover_missing"] is True

    async def test_a_release_with_no_mbid_does_not_ask_for_a_jacket(self, service, tmp_path, monkeypatch):
        service._data_file = str(tmp_path / "cd_data.json")
        release = dict(TestTheMusicBrainzQuery.RELEASE, id="")
        self._catalogue(monkeypatch, {"disc": {"release-list": [release]}})
        await service.lookup_metadata("d1", "toc", self.TOC)
        archive = Mock(side_effect=AssertionError("asked with no release to name"))
        monkeypatch.setattr(musicbrainzngs, "get_image_front", archive)

        assert await service.fetch_cover("d1") is None
        archive.assert_not_called()
        assert list(tmp_path.glob("*.jpg")) == []

    async def test_a_stalled_archive_is_given_up_on_not_awaited_forever(
            self, service, tmp_path, monkeypatch):
        """musicbrainzngs sets no socket timeout: a connection that stops
        answering never returns, and the source announces the jacket — the
        player's veil — until this does."""
        import threading
        import backend.sources.cd.data as data_module
        monkeypatch.setattr(data_module, "COVER_FETCH_TIMEOUT_S", 0.05)
        release = threading.Event()
        monkeypatch.setattr(musicbrainzngs, "get_image_front",
                            lambda mbid, size="500": release.wait(5) and PAYLOAD)
        _known(service, "d1")
        try:
            assert await asyncio.wait_for(service.fetch_cover("d1"), 2.0) is None
            assert service._cache["d1"]["cover_missing"] is False, \
                "a stall was remembered as no jacket"
        finally:
            release.set()

    async def test_a_download_outlives_its_caller_and_serves_the_next(
            self, service, tmp_path, monkeypatch):
        """A source closed and reopened during an outage: the first caller is
        cancelled, its thread is not. The next caller must wait on that thread,
        not start another — each one parks in the default executor, which the
        whole backend shares."""
        import threading
        release = threading.Event()
        calls = []

        def slow(mbid, size="500"):
            calls.append(mbid)
            release.wait(5)
            return PAYLOAD

        monkeypatch.setattr(musicbrainzngs, "get_image_front", slow)
        _known(service, "d1")

        first = asyncio.ensure_future(service.fetch_cover("d1"))
        await asyncio.sleep(0.05)
        first.cancel()
        second = asyncio.ensure_future(service.fetch_cover("d1"))
        await asyncio.sleep(0.05)
        release.set()

        assert await asyncio.wait_for(second, 2.0) == "/api/cd/cover/d1"
        assert calls == ["release-mbid"], "the reopen started a second download"

    async def test_a_missing_musicbrainzngs_is_not_remembered_as_no_jacket(
            self, service, monkeypatch):
        """A broken venv answers nothing about the archive; recorded as an
        absence, every disc loaded meanwhile stays coverless once repaired."""
        import sys
        monkeypatch.setitem(sys.modules, "musicbrainzngs", None)
        _known(service, "d1")

        assert await service.fetch_cover("d1") is None
        assert service._cache["d1"]["cover_missing"] is False

    async def test_only_a_named_release_without_a_known_absence_is_fetchable(self, service):
        """The source announces a pending cover on this answer alone."""
        assert service.cover_fetchable("never-looked-up") is False
        _known(service, "d1")
        assert service.cover_fetchable("d1") is True
        _known(service, "d2", release_mbid="")
        assert service.cover_fetchable("d2") is False
        _known(service, "d3")
        service._cache["d3"]["cover_missing"] = True
        assert service.cover_fetchable("d3") is False

    async def test_an_unknown_disc_has_no_jacket_to_ask_for(self, service, monkeypatch):
        """The fallback DiscInfo is never cached, so there is no release to
        name; the fetch must answer without the network."""
        archive = Mock(side_effect=AssertionError("asked for a disc nobody named"))
        monkeypatch.setattr(musicbrainzngs, "get_image_front", archive)
        assert await service.fetch_cover("never-looked-up") is None
        archive.assert_not_called()


class TestCoverArtFallback:
    """Release first, release group second. A CD pressing often carries no
    jacket of its own while the album does — dropping the second query is the
    difference between a cover and a placeholder for a good share of discs."""

    def test_the_pressing_is_asked_before_the_album(self, service, monkeypatch):
        import musicbrainzngs
        calls = []
        monkeypatch.setattr(musicbrainzngs, "get_image_front",
                            lambda mbid, size="500": calls.append(("release", mbid)) or PAYLOAD)
        monkeypatch.setattr(musicbrainzngs, "get_release_group_image_front",
                            lambda mbid, size="500": calls.append(("group", mbid)) or PAYLOAD)

        assert service._download_cover_sync("rel-1", "rg-1") == PAYLOAD
        assert calls == [("release", "rel-1")]

    def test_a_pressing_with_no_jacket_falls_back_to_the_album(self, service, monkeypatch):
        import musicbrainzngs
        monkeypatch.setattr(musicbrainzngs, "get_image_front",
                            Mock(side_effect=musicbrainzngs.ResponseError("404")))
        monkeypatch.setattr(musicbrainzngs, "get_release_group_image_front",
                            lambda mbid, size="500": PAYLOAD)
        assert service._download_cover_sync("rel-1", "rg-1") == PAYLOAD

    def test_with_no_release_group_there_is_nothing_left_to_try(self, service, monkeypatch):
        import musicbrainzngs
        monkeypatch.setattr(musicbrainzngs, "get_image_front",
                            Mock(side_effect=musicbrainzngs.ResponseError("404")))
        group = Mock()
        monkeypatch.setattr(musicbrainzngs, "get_release_group_image_front", group)
        assert service._download_cover_sync("rel-1", "") is None
        group.assert_not_called()

    def test_neither_the_pressing_nor_the_album_has_one(self, service, monkeypatch):
        import musicbrainzngs
        monkeypatch.setattr(musicbrainzngs, "get_image_front",
                            Mock(side_effect=musicbrainzngs.ResponseError("404")))
        monkeypatch.setattr(musicbrainzngs, "get_release_group_image_front",
                            Mock(side_effect=musicbrainzngs.ResponseError("404")))
        assert service._download_cover_sync("rel-1", "rg-1") is None


class TestTheDiscCacheOnDisk:
    """`cd_data.json` carries no schema_version on purpose: it is a disposable
    derived cache (CLAUDE.md, persistence). So it must degrade to empty rather
    than fail loud — the opposite rule from a versioned store."""

    ENTRY = {"album": "Spaces", "artist": "Nils Frahm", "year": "2013",
             "release_mbid": "rel-1", "release_group_mbid": "rg-1", "cover_missing": False,
             "tracks": [{"number": 1, "title": "One", "duration": 200}]}

    async def test_a_cache_written_by_a_previous_boot_comes_back(self, service, tmp_path):
        (tmp_path / "cd_data.json").write_text(json.dumps({"discs": {"d1": self.ENTRY}}))
        service._data_file = str(tmp_path / "cd_data.json")

        await service._load_data()
        assert service._cache == {"d1": self.ENTRY}

    async def test_an_entry_in_an_older_shape_is_dropped_not_fatal(self, service, tmp_path, caplog):
        """Written before the MBIDs were cached, an entry has `has_cover` and
        nothing to fetch a jacket with; kept, it raised KeyError when the disc
        was loaded, and the CD source failed to open. Dropped, the disc is
        looked up again — the worst case this cache is allowed."""
        old = {"album": "Greatest Hits", "artist": "Queen", "year": "1991",
               "has_cover": False, "tracks": []}
        (tmp_path / "cd_data.json").write_text(
            json.dumps({"discs": {"old": old, "d1": self.ENTRY}}))
        service._data_file = str(tmp_path / "cd_data.json")

        await service._load_data()

        assert service._cache == {"d1": self.ENTRY}
        assert "Dropped 1 cached disc(s)" in caplog.text
        assert service.cover_fetchable("old") is False

    async def test_a_first_boot_with_no_file_starts_empty(self, service, tmp_path):
        service._data_file = str(tmp_path / "never-written.json")
        service._cache = {"stale": {}}
        await service._load_data()
        assert service._cache == {}

    async def test_a_truncated_cache_is_discarded_not_fatal(self, service, tmp_path, caplog):
        """A cache killed mid-write must not stop the CD source from starting:
        the worst case is one MusicBrainz lookup per disc, and the alternative
        is a source that never comes up."""
        (tmp_path / "cd_data.json").write_text('{"discs": {"d1": ')
        service._data_file = str(tmp_path / "cd_data.json")
        service._cache = {"stale": {}}

        with caplog.at_level("ERROR", logger="source.cd.data"):
            await service._load_data()

        assert service._cache == {}
        assert any("cd_data.json" in r.message for r in caplog.records)

    async def test_the_cache_becomes_visible_only_through_a_rename(self, service, tmp_path, monkeypatch):
        """Same shape as the cover art above: a cache truncated mid-write would
        be loaded as corrupt on the next boot and every disc re-looked-up."""
        service._data_file = str(tmp_path / "cd_data.json")
        service._cache = {"d1": {"album": "Spaces"}}
        renames = []
        real_replace = os.replace

        def spy(src, dst):
            renames.append((str(src), str(dst), Path(src).read_text()))
            real_replace(src, dst)

        monkeypatch.setattr(os, "replace", spy)
        assert await service._save_data() is True

        assert len(renames) == 1
        src, dst, staged = renames[0]
        assert src.endswith(".tmp") and dst == service._data_file
        assert json.loads(staged)["discs"]["d1"]["album"] == "Spaces"
        assert list(tmp_path.glob("*.tmp")) == []

    async def test_a_cache_that_cannot_be_written_reports_failure(self, service, tmp_path, monkeypatch):
        service._data_file = str(tmp_path / "cd_data.json")
        monkeypatch.setattr(os, "replace",
                            Mock(side_effect=OSError("No space left on device")))
        assert await service._save_data() is False

    async def test_a_saved_cache_survives_a_round_trip(self, service, tmp_path):
        service._data_file = str(tmp_path / "cd_data.json")
        service._cache = {"d1": dict(self.ENTRY, album="Ólafur", artist="Arnalds")}
        assert await service._save_data() is True

        reader = CdDataService()
        reader._data_file = service._data_file
        await reader._load_data()
        assert reader._cache == service._cache, "non-ASCII did not survive the write"

    async def test_initialize_creates_the_covers_directory(self, service, tmp_path):
        """`fetch_cover` writes straight into it; missing, every jacket
        fails with ENOENT and every disc shows the placeholder."""
        covers = tmp_path / "cd_covers"
        service._covers_dir = str(covers)
        service._data_file = str(tmp_path / "cd_data.json")

        await service.initialize()
        assert covers.is_dir()
