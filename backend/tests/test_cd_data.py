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
