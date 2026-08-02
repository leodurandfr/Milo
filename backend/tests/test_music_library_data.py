"""Unit tests for the Music Library network-share config (MusicLibraryDataService).

Covers the versioned-JSON CRUD that backs /api/music-library/shares: default
seeding, add/list/get/update/remove, id generation, the immutability of id/
created_at across updates, and the fail-loud schema-version protocol. The file is
redirected to a tmp path so nothing touches /var/lib/milo.
"""
import json

import pytest

from backend.shared.persistence import SchemaVersionMismatch, save_versioned_json
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
