# backend/tests/test_music_library_storages.py
"""Tests for the storage-space layer of the Music Library source.

Two behaviours nothing else covers, both of which fail *silently* — the screen
just shows the wrong music:

- ``NetworkShareService.storages`` is what the library filter is built from, so
  a duplicate display name or a missing library id makes two storage spaces
  indistinguishable (or unbrowsable).
- ``MusicLibrarySource.playlists_in_storage`` decides which playlists belong to
  the storage space being browsed. Navidrome keeps playlists catalog-wide and
  ignores ``musicFolderId`` on getPlaylists, so this is the only thing standing
  between the user and a NAS playlist listed under a USB key.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.sources.music_library.disc_merge import build_merged_id
from backend.sources.music_library.source import MusicLibrarySource


# === storages() =============================================================

def _service(shares, usb, library_ids, known=None):
    """A NetworkShareService with its three collaborators stubbed out.

    The mocks stand for the outside world the service reads: the config file,
    the mount table, and Navidrome's library list. ``usb`` is what is plugged in
    right now; ``known`` is what has ever been plugged in (defaulting to the
    same), which is what the service persists so a key keeps its library while
    it is away.
    """
    from backend.sources.music_library.shares import NetworkShareService

    service = NetworkShareService(
        AsyncMock(return_value=None), lambda: None, AsyncMock()
    )
    if known is None:
        known = {
            volume["uuid"]: {
                "name": None,
                "label": volume["label"],
                "mountpoint": volume["mountpoint"],
            }
            for volume in usb
        }
    service._data.list_shares = AsyncMock(return_value=shares)
    service._data.get_known_usb = AsyncMock(return_value=known)
    service._storage.get_mounted_share_ids = MagicMock(
        return_value={s["id"] for s in shares}
    )
    service._storage.get_usb_mounts = MagicMock(return_value=usb)
    service._libraries.library_id = MagicMock(side_effect=library_ids.get)
    return service


async def test_storages_lists_shares_and_keys_with_their_library():
    service = _service(
        shares=[{"id": "nas-1", "name": "NAS-Leo"}],
        usb=[{"uuid": "U-1", "label": "MUSIC", "mountpoint": "/media/milo/MUSIC"}],
        library_ids={"/media/milo/nas-1": 2, "/media/milo/MUSIC": 3},
    )

    entries = await service.storages()

    assert [(e["kind"], e["name"], e["library_id"]) for e in entries] == [
        ("share", "NAS-Leo", 2),
        ("usb", "MUSIC", 3),
    ]


async def test_storages_disambiguates_two_keys_with_the_same_label():
    # milo-mount already made the mountpoints unique; the *names* are what the
    # filter renders, and two buttons reading "MUSIC" name nothing.
    service = _service(
        shares=[],
        usb=[
            {"uuid": "U-1", "label": "MUSIC", "mountpoint": "/media/milo/MUSIC"},
            {"uuid": "U-2", "label": "MUSIC", "mountpoint": "/media/milo/MUSIC-1a2b3c4d"},
        ],
        library_ids={"/media/milo/MUSIC": 2, "/media/milo/MUSIC-1a2b3c4d": 3},
    )

    names = [entry["name"] for entry in await service.storages()]

    assert len(set(names)) == 2, names
    assert names[0] == "MUSIC"


async def test_storage_without_a_library_is_listed_but_not_browsable():
    # Navidrome has not accepted the library yet (still starting up): the entry
    # must still appear — the settings screen shows it — with a null id the
    # frontend leaves out of the filter.
    service = _service(
        shares=[{"id": "nas-1", "name": "NAS-Leo"}],
        usb=[],
        library_ids={},
    )

    assert (await service.storages())[0]["library_id"] is None


async def test_unplugged_key_keeps_its_entry_and_its_library():
    # What makes a replug cost a quick scan instead of re-indexing 10 000 tracks:
    # the key stays in the set with its library id, so the reconcile that runs on
    # the unplug reads it as "should exist" and leaves the library alone. Drop
    # the entry here and the library goes with it, silently — the next plug-in
    # just takes 18 minutes again.
    service = _service(
        shares=[],
        usb=[],
        library_ids={"/media/milo/MUSIC": 3},
        known={"U-1": {"name": "iPod", "label": "MUSIC",
                       "mountpoint": "/media/milo/MUSIC"}},
    )

    entries = await service.storages()

    assert [(e["id"], e["mounted"], e["library_id"]) for e in entries] == [
        ("U-1", False, 3)
    ]


async def test_offline_names_covers_an_unplugged_key():
    # offline_names() gates the full scan, and a full scan purges everything
    # Navidrome cannot see (PurgeMissing="full"). Leaving the unplugged key out
    # would let a refresh throw away the very index the entry above preserves.
    service = _service(
        shares=[],
        usb=[],
        library_ids={},
        known={"U-1": {"name": "iPod de Léo", "label": "MUSIC",
                       "mountpoint": "/media/milo/MUSIC"}},
    )

    assert await service.offline_names() == ["iPod de Léo"]


# === library reconcile ======================================================

def _reconciler(admin):
    from backend.sources.music_library.libraries import NavidromeLibraryService

    service = NavidromeLibraryService()
    service._get_admin = AsyncMock(return_value=admin)
    return service


async def test_reconcile_fails_when_the_library_cannot_be_granted():
    # The library is created but the service account never gets access to it, so
    # every Subsonic call scoped to it answers empty: a storage button that
    # browses nothing. Reporting success here would leave that in place, because
    # only a failed reconcile schedules the retry that repairs it.
    admin = MagicMock()
    admin.list_libraries = AsyncMock(
        return_value=[{"id": 3, "name": "MUSIC", "path": "/media/milo/MUSIC"}]
    )
    admin.create_library = AsyncMock(return_value={"id": 3})
    admin.grant_all_libraries = AsyncMock(return_value=False)
    service = _reconciler(admin)
    service._desired = {"/media/milo/MUSIC": "MUSIC"}

    assert await service._converge() is False
    admin.grant_all_libraries.assert_awaited_once()


async def test_reconcile_leaves_a_library_outside_the_mount_root_alone():
    # Navidrome refuses to delete the library it made from MusicFolder, so
    # trying is a guaranteed 500 on every single reconcile.
    admin = MagicMock()
    admin.list_libraries = AsyncMock(return_value=[
        {"id": 1, "name": "Default", "path": "/var/lib/milo/navidrome/default-library"},
        {"id": 3, "name": "MUSIC", "path": "/media/milo/MUSIC"},
    ])
    admin.delete_library = AsyncMock(return_value=True)
    admin.grant_all_libraries = AsyncMock(return_value=True)
    service = _reconciler(admin)
    service._desired = {"/media/milo/MUSIC": "MUSIC"}

    assert await service._converge() is True
    admin.delete_library.assert_not_called()


# === playlists_in_storage() =================================================

@pytest.fixture
def source():
    """A source whose two live storage spaces are the USB key (library 3) it is
    browsing and a NAS share (library 2)."""
    src = MusicLibrarySource(config={}, state_machine=MagicMock())
    src._shares = MagicMock()
    src._shares.storages = AsyncMock(return_value=[
        {"kind": "share", "id": "nas-1", "library_id": 2},
        {"kind": "usb", "id": "U-1", "library_id": 3},
    ])
    return src


def _with_catalog(src, album_ids, playlist_albums=None):
    """Pin the storage space's album catalog and each playlist's first album."""
    src.get_merged_albums = AsyncMock(
        return_value=[{"id": album_id} for album_id in album_ids]
    )
    entries = playlist_albums or {}
    client = MagicMock()
    client.get_playlist = AsyncMock(
        side_effect=lambda pid: {"entry": [{"albumId": entries[pid]}]} if entries.get(pid) else {"entry": []}
    )
    src.get_navidrome_client = AsyncMock(return_value=client)
    return client


async def test_recorded_playlist_belongs_only_to_its_own_storage(source):
    source._shares.playlist_storages = AsyncMock(
        return_value={"pl-usb": "U-1", "pl-nas": "nas-1"}
    )
    _with_catalog(source, album_ids=["al-1"])
    playlists = [{"id": "pl-usb", "songCount": 3}, {"id": "pl-nas", "songCount": 3}]

    kept = await source.playlists_in_storage(playlists, library_id=3)

    assert [p["id"] for p in kept] == ["pl-usb"]


async def test_unknown_playlist_is_placed_by_its_first_track(source):
    # A .m3u Navidrome imported from the key: no record, so its content decides.
    source._shares.playlist_storages = AsyncMock(return_value={})
    _with_catalog(
        source,
        album_ids=["al-here"],
        playlist_albums={"pl-here": "al-here", "pl-elsewhere": "al-other"},
    )
    playlists = [{"id": "pl-here", "songCount": 5}, {"id": "pl-elsewhere", "songCount": 5}]

    kept = await source.playlists_in_storage(playlists, library_id=3)

    assert [p["id"] for p in kept] == ["pl-here"]


async def test_track_of_a_merged_multi_disc_album_still_places_its_playlist(source):
    # The catalog collapses "… CD 1"/"CD 2" into one synthetic id, but a track
    # points at the member album — so the members have to be matched.
    source._shares.playlist_storages = AsyncMock(return_value={})
    _with_catalog(
        source,
        album_ids=[build_merged_id(["al-disc1", "al-disc2"])],
        playlist_albums={"pl-1": "al-disc2"},
    )

    kept = await source.playlists_in_storage([{"id": "pl-1", "songCount": 2}], library_id=3)

    assert [p["id"] for p in kept] == ["pl-1"]


async def test_playlist_of_a_removed_storage_falls_back_to_its_content(source):
    # The share it was created in was deleted, so its record points at a storage
    # space that no longer exists. Honouring it would match nothing anywhere and
    # the playlist would vanish from Milō while still existing in Navidrome.
    source._shares.playlist_storages = AsyncMock(return_value={"pl-1": "nas-gone"})
    _with_catalog(source, album_ids=["al-here"], playlist_albums={"pl-1": "al-here"})

    kept = await source.playlists_in_storage([{"id": "pl-1", "songCount": 4}], library_id=3)

    assert [p["id"] for p in kept] == ["pl-1"]


async def test_unrecorded_empty_playlist_is_never_hidden(source):
    # Nothing places it, and hiding it in every storage space would make it
    # unreachable — a playlist the user cannot open is worse than one listed twice.
    source._shares.playlist_storages = AsyncMock(return_value={})
    _with_catalog(source, album_ids=["al-1"])

    kept = await source.playlists_in_storage([{"id": "pl-empty", "songCount": 0}], library_id=3)

    assert [p["id"] for p in kept] == ["pl-empty"]
