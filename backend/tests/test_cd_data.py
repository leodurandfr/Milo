"""
Tests for CdDataService's cover-art cache.

What breaks when these fail: a jacket interrupted mid-write is served
forever. `_download_cover` returns early on `os.path.exists(cover_path)` and
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

    assert await service._download_cover("disc-1", "release-mbid") is True

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

    assert await service._download_cover("disc-2", "release-mbid") is False
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
# Disc detection, MusicBrainz lookup, and the on-disk cache.
#
# `data.py` is what tells the appliance a disc is in the tray, and it does it
# with raw descriptors on /dev/sr0. This host IS the appliance and there is a
# disc in that tray, so `never_the_real_drive` below makes the real primitives
# RAISE for the whole module; every test installs the drive it wants to see.
#
# `discid.read()` in particular spins the disc up. It is never called.
# =============================================================================
import fcntl
import json
from unittest.mock import AsyncMock, Mock, patch

from backend.config.constants import CD_DEVICE
from backend.sources.cd.data import (
    CDROM_DRIVE_STATUS,
    CDS_DISC_OK,
    CDS_DRIVE_NOT_READY,
)
from backend.sources.cd.models import DiscInfo

DRIVE_FD = 771


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


class FakeDriveNode:
    """`/dev/sr0` as an fd and a status code, plus what was done to it."""

    def __init__(self, status=CDS_DISC_OK, *, open_error=None, ioctl_error=None):
        self.status = status
        self.opened = []
        self.closed = []
        self.ioctls = []
        self._open_error = open_error
        self._ioctl_error = ioctl_error

    def install(self, monkeypatch, *, present=True):
        real_open, real_close = os.open, os.close
        real_ioctl, real_exists = fcntl.ioctl, os.path.exists

        def open_(path, flags, *a):
            if str(path) != CD_DEVICE:
                return real_open(path, flags, *a)
            self.opened.append(flags)
            if self._open_error:
                raise self._open_error
            return DRIVE_FD

        def close_(fd):
            if fd != DRIVE_FD:
                return real_close(fd)
            self.closed.append(fd)

        def ioctl_(fd, request, *a):
            if request != CDROM_DRIVE_STATUS:
                return real_ioctl(fd, request, *a)
            assert fd == DRIVE_FD
            self.ioctls.append(request)
            if self._ioctl_error:
                raise self._ioctl_error
            return self.status

        monkeypatch.setattr(os, "open", open_)
        monkeypatch.setattr(os, "close", close_)
        monkeypatch.setattr(fcntl, "ioctl", ioctl_)
        monkeypatch.setattr(
            os.path, "exists",
            lambda p: present if str(p) == CD_DEVICE else real_exists(p))
        return self


class TestDiscDetection:
    """`probe_drive_and_disc` is the whole of disc presence: the watcher calls it
    every poll for the life of the unit, and the two-phase NOT_READY/DISC_OK
    answer is what puts the loading-album indicator on screen before the TOC is
    readable."""

    def test_a_ready_disc_is_reported_through_the_drive_status_ioctl(self, service, monkeypatch):
        drive = FakeDriveNode(CDS_DISC_OK).install(monkeypatch)
        assert service.probe_drive_and_disc() == (True, CDS_DISC_OK)
        assert drive.ioctls == [CDROM_DRIVE_STATUS]

    def test_a_spinning_up_disc_is_distinguished_from_a_ready_one(self, service, monkeypatch):
        FakeDriveNode(CDS_DRIVE_NOT_READY).install(monkeypatch)
        assert service.probe_drive_and_disc() == (True, CDS_DRIVE_NOT_READY)

    def test_no_drive_answers_without_touching_the_device(self, service, monkeypatch):
        """`-1` is the sentinel for "no drive", and it must not come from a
        failed open: the watcher tells the two apart to broadcast a drive
        disconnect exactly once."""
        drive = FakeDriveNode().install(monkeypatch, present=False)
        assert service.probe_drive_and_disc() == (False, -1)
        assert drive.opened == [], "the device was opened although it is not there"

    def test_the_device_is_opened_without_blocking(self, service, monkeypatch):
        """O_NONBLOCK is what makes this safe to call from a poll: a plain open
        on a CD device waits for the disc to spin up, and the watcher runs in an
        executor thread the event loop is waiting on."""
        drive = FakeDriveNode().install(monkeypatch)
        service.check_disc_status()
        assert drive.opened and drive.opened[0] & os.O_NONBLOCK

    def test_the_descriptor_is_released_even_when_the_ioctl_fails(self, service, monkeypatch):
        """Leaked once per poll, the drive is held open for ever: the tray stops
        opening and `eject` reports the device busy."""
        drive = FakeDriveNode(
            ioctl_error=OSError(5, "Input/output error")).install(monkeypatch)
        assert service.check_disc_status() == -1
        assert drive.closed == [DRIVE_FD]

    def test_a_drive_that_will_not_open_answers_minus_one(self, service, monkeypatch):
        drive = FakeDriveNode(
            open_error=OSError(16, "Device or resource busy")).install(monkeypatch)
        assert service.check_disc_status() == -1
        assert drive.closed == [], "a descriptor that was never obtained was closed"


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

    async def test_a_cached_disc_is_answered_without_reaching_the_network(self, service, monkeypatch):
        calls = self._catalogue(monkeypatch, {"disc": {"release-list": []}})
        service._cache["d1"] = {
            "album": "Spaces", "artist": "Nils Frahm", "year": "2013",
            "has_cover": True,
            "tracks": [{"number": 1, "title": "One", "duration": 200},
                       {"number": 2, "title": "Two", "duration": 150}],
        }
        info = await service.lookup_metadata("d1", "toc", self.TOC)

        assert calls == [], "a cached disc still queried MusicBrainz"
        assert isinstance(info, DiscInfo)
        assert (info.album, info.artist, info.total_duration) == ("Spaces", "Nils Frahm", 350)
        assert info.cover_url == "/api/cd/cover/d1"

    async def test_a_cached_disc_with_no_cover_offers_no_cover_url(self, service):
        """The URL is what the player requests; offered for a disc whose jacket
        was never fetched, every render pays a 404."""
        service._cache["d1"] = {"album": "A", "artist": "B", "has_cover": False,
                                "tracks": []}
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

        assert info.cover_url == "/api/cd/cover/d1"
        assert [t.title for t in info.tracks] == ["An Aborted Beginning", "Says"]
        assert [t.duration for t in info.tracks] == [200, 150], \
            "the catalogue's durations displaced the disc's own"
        assert (tmp_path / "d1.jpg").read_bytes() == PAYLOAD

        on_disk = json.loads((tmp_path / "cd_data.json").read_text())
        assert on_disk["discs"]["d1"]["album"] == "Spaces"
        assert on_disk["discs"]["d1"]["has_cover"] is True
        assert "cached_at" in on_disk["discs"]["d1"]

    async def test_a_disc_whose_jacket_is_missing_is_still_cached(self, service, tmp_path, monkeypatch):
        """has_cover=False is a real answer, not a failure: caching it is what
        stops every insertion re-asking the Cover Art Archive."""
        service._data_file = str(tmp_path / "cd_data.json")
        self._catalogue(monkeypatch,
                        {"disc": {"release-list": [TestTheMusicBrainzQuery.RELEASE]}},
                        jacket=None)
        info = await service.lookup_metadata("d1", "toc", self.TOC)

        assert info.cover_url is None
        assert service._cache["d1"]["has_cover"] is False
        assert service._cache["d1"]["album"] == "Spaces"

    async def test_a_release_with_no_mbid_does_not_ask_for_a_jacket(self, service, tmp_path, monkeypatch):
        service._data_file = str(tmp_path / "cd_data.json")
        release = dict(TestTheMusicBrainzQuery.RELEASE, id="")
        self._catalogue(monkeypatch, {"disc": {"release-list": [release]}})
        await service.lookup_metadata("d1", "toc", self.TOC)

        assert list(tmp_path.glob("*.jpg")) == [], \
            "the archive was asked for a jacket with no release to name"
        assert service._cache["d1"]["has_cover"] is False


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

    async def test_a_cache_written_by_a_previous_boot_comes_back(self, service, tmp_path):
        entry = {"album": "Spaces", "artist": "Nils Frahm", "has_cover": True,
                 "tracks": [{"number": 1, "title": "One", "duration": 200}]}
        (tmp_path / "cd_data.json").write_text(json.dumps({"discs": {"d1": entry}}))
        service._data_file = str(tmp_path / "cd_data.json")

        await service._load_data()
        assert service._cache == {"d1": entry}

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
        service._cache = {"d1": {"album": "Ólafur", "artist": "Arnalds",
                                 "tracks": [], "has_cover": False}}
        assert await service._save_data() is True

        reader = CdDataService()
        reader._data_file = service._data_file
        await reader._load_data()
        assert reader._cache == service._cache, "non-ASCII did not survive the write"

    async def test_initialize_creates_the_covers_directory(self, service, tmp_path):
        """`_download_cover` writes straight into it; missing, every jacket
        fails with ENOENT and every disc shows the placeholder."""
        covers = tmp_path / "cd_covers"
        service._covers_dir = str(covers)
        service._data_file = str(tmp_path / "cd_data.json")

        await service.initialize()
        assert covers.is_dir()
        assert service._loaded is True
