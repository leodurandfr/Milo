"""Unit tests for the Music Library network-share config (MusicLibraryDataService).

Covers the versioned-JSON CRUD that backs /api/music-library/shares: default
seeding, add/list/get/update/remove, id generation, the immutability of id/
created_at across updates, and the fail-loud schema-version protocol. The file is
redirected to a tmp path so nothing touches /var/lib/milo.
"""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from backend.shared.persistence import SchemaVersionMismatch, save_versioned_json
from backend.sources.music_library import data as library_data
from backend.sources.music_library.data import MusicLibraryDataService


@pytest.fixture
def service(tmp_path):
    svc = MusicLibraryDataService()
    svc._data_file = tmp_path / "music_library_data.json"
    return svc


# === seeding / schema =============================================================

async def test_initialize_seeds_defaults(service):
    await service.initialize()
    assert service._data_file.exists()
    data = json.loads(service._data_file.read_text())
    assert data["shares"] == []
    assert data["schema_version"] == MusicLibraryDataService.SCHEMA_VERSION


async def test_load_data_on_fresh_install_returns_defaults(service):
    # No file on disk yet — load returns the default shape without writing.
    data = await service.load_data()
    assert data == {"shares": [], "known_usb": {}, "playlist_storages": {}}
    assert not service._data_file.exists()


async def test_schema_mismatch_raises(service):
    await save_versioned_json(service._data_file, {"shares": []}, version=999)
    with pytest.raises(SchemaVersionMismatch):
        await service.initialize()


async def test_missing_required_key_raises(service):
    # A file at the right version but missing "shares" must fail loud, not heal.
    await save_versioned_json(
        service._data_file, {"something_else": 1}, MusicLibraryDataService.SCHEMA_VERSION
    )
    with pytest.raises(RuntimeError, match="missing required keys"):
        await service.initialize()


# === CRUD =========================================================================

async def test_add_share_returns_share_with_generated_id(service):
    await service.initialize()
    share = await service.add_share(
        share_type="cifs", host="192.168.1.10", path="Music", name="NAS", has_credentials=True
    )
    assert share["type"] == "cifs"
    assert share["host"] == "192.168.1.10"
    assert share["path"] == "Music"
    assert share["name"] == "NAS"
    assert share["has_credentials"] is True
    assert share["id"].startswith("nas-")
    assert isinstance(share["created_at"], int)
    # Persisted.
    assert (await service.list_shares())[0]["id"] == share["id"]


async def test_add_share_stores_username_and_domain(service):
    # username/domain are non-secret metadata (shown on the edit screen); only
    # the password stays out of the store.
    await service.initialize()
    share = await service.add_share(
        share_type="cifs", host="h", path="Music", name="NAS",
        has_credentials=True, username="Leo", domain="WORKGROUP",
    )
    assert share["username"] == "Leo"
    assert share["domain"] == "WORKGROUP"
    assert (await service.list_shares())[0]["username"] == "Leo"


async def test_add_share_generates_unique_ids(service):
    await service.initialize()
    a = await service.add_share(share_type="nfs", host="h", path="/a", name="Same", has_credentials=False)
    b = await service.add_share(share_type="nfs", host="h", path="/b", name="Same", has_credentials=False)
    assert a["id"] != b["id"]
    assert len(await service.list_shares()) == 2


async def test_get_share(service):
    await service.initialize()
    share = await service.add_share(share_type="nfs", host="h", path="/x", name="X", has_credentials=False)
    assert (await service.get_share(share["id"]))["path"] == "/x"
    assert await service.get_share("nope") is None


async def test_update_share_merges_and_keeps_id(service):
    await service.initialize()
    share = await service.add_share(share_type="cifs", host="h1", path="A", name="N", has_credentials=False)
    original_created = share["created_at"]

    updated = await service.update_share(
        share["id"],
        {"host": "h2", "path": "B", "has_credentials": True, "id": "hacked", "created_at": 0},
    )
    assert updated["host"] == "h2"
    assert updated["path"] == "B"
    assert updated["has_credentials"] is True
    # id and created_at are immutable even if the caller tries to change them.
    assert updated["id"] == share["id"]
    assert updated["created_at"] == original_created


async def test_update_unknown_share_returns_none(service):
    await service.initialize()
    assert await service.update_share("missing", {"host": "x"}) is None


async def test_remove_share(service):
    await service.initialize()
    share = await service.add_share(share_type="nfs", host="h", path="/x", name="X", has_credentials=False)
    removed = await service.remove_share(share["id"])
    assert removed["id"] == share["id"]
    assert await service.list_shares() == []


async def test_remove_unknown_share_returns_none(service):
    await service.initialize()
    assert await service.remove_share("missing") is None


# === id slug ======================================================================

@pytest.mark.parametrize("name,prefix", [
    ("My NAS Music!", "my-nas-music-"),
    ("   ", "share-"),          # all-whitespace slug -> fallback
    ("!!!", "share-"),          # no alnum -> fallback
    ("Café", "caf-"),           # non-ascii stripped
])
def test_generate_id_slug(name, prefix):
    svc = MusicLibraryDataService()
    generated = svc._generate_id(name, set())
    assert generated.startswith(prefix)


# === read-modify-write atomicity ==================================================

async def test_concurrent_mutations_both_survive(service):
    """A share added while a USB key is being remembered must not vanish.

    `load_data` and `save_data` take `_file_lock` separately, so the second
    mutator's load lands in the window between the first's load and its save:
    it starts from a stale dict and its save writes the first one's whole
    update back out. This file is what a boot remount replays, so a lost write
    is a share or a key that silently stops coming back — and the udev USB
    path genuinely runs alongside the shares API.
    """
    await service.initialize()

    await asyncio.gather(
        service.add_share("cifs", "nas.local", "/music", "NAS", has_credentials=False),
        service.remember_usb("UUID-1", "MUSIC", "/media/milo/music"),
    )

    data = await service.load_data()
    assert [s["name"] for s in data["shares"]] == ["NAS"]
    assert "UUID-1" in data["known_usb"]


async def test_concurrent_usb_writes_both_survive(service):
    """Several keys plugged at once — no entry may be dropped."""
    await service.initialize()

    await asyncio.gather(*[
        service.remember_usb(f"UUID-{i}", f"KEY{i}", f"/media/milo/key{i}")
        for i in range(6)
    ])

    assert sorted(await service.get_known_usb()) == [f"UUID-{i}" for i in range(6)]


async def test_unchanged_mutation_does_not_rewrite(service, monkeypatch):
    """Forgetting an unknown playlist must not write.

    `_mutate` writes only when its callback reports a change. The content would
    be identical either way, so the file cannot show this — only the write can:
    a mutator that found nothing to do still re-stamps the file, and on this
    appliance every write is an fsync to the SD card. It is also what the
    pre-`_mutate` code did, and keeping it is what makes this a pure refactor
    of the locking.
    """
    await service.initialize()
    await service.set_playlist_storage("pl-1", "UUID-1")

    saves = AsyncMock()
    monkeypatch.setattr(library_data, "save_versioned_json", saves)

    await service.forget_playlist("absent-playlist")

    saves.assert_not_awaited()
