# backend/tests/test_music_library_storage_routes.py
"""The storage-space and share HTTP surface, and the USB name store under it.

Every route in this file ran at 0 %: `/storages`, the two `/usb-devices/{uuid}`
verbs, and the four `/shares` ones. So did the two store mutators they end on
(`set_usb_name`, `forget_usb`).

What breaks when these fail, in order of stake:

* **`DELETE /usb-devices/{uuid}` refuses a key that is plugged in (409).**
  Forgetting is what retires a key's Navidrome library and frees the catalog
  rows it holds — the counterpart to keeping an unplugged key's index for ever.
  Done while the key is mounted it is a no-op that reports success: the very
  next reconcile sees the mount again and puts the library straight back, so
  the row the user pressed comes back on its own with nothing said.
* **The 404s.** `rename_usb`, `update` and `remove` each answer False/None for
  an id that is not there, and a route that read that as success would tell the
  settings screen a share was deleted while it stays mounted.
* **`set_usb_name` refuses to invent an entry.** A name is filed under the
  filesystem UUID so it survives a replug; naming a UUID that was never mounted
  would put a key with no label and no mountpoint into the known set, and
  `storages()` builds a row per known key.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.sources.music_library.data import MusicLibraryDataService
from backend.sources.music_library.routes import router, setup_music_library_routes

USB_UUID = "1234-ABCD"


@pytest.fixture
def shares():
    """The source's share service — the outside world of these routes."""
    service = MagicMock()
    service.storages_with_stats = AsyncMock(return_value=[
        {"id": USB_UUID, "kind": "usb", "name": "iPod", "mounted": True,
         "library_id": 7, "track_count": 12},
    ])
    service.scan_state = MagicMock(return_value={"scanning": False})
    service.rename_usb = AsyncMock(return_value=True)
    service.usb_is_mounted = AsyncMock(return_value=False)
    service.forget_usb = AsyncMock(return_value=True)
    service.list = AsyncMock(return_value=[{"id": "nas-leo", "type": "cifs"}])
    service.add = AsyncMock(return_value={"id": "nas-leo", "type": "cifs"})
    service.update = AsyncMock(return_value={"id": "nas-leo", "type": "cifs"})
    service.remove = AsyncMock(return_value=True)
    return service


@pytest.fixture
def source(shares):
    src = MagicMock()
    src.shares = shares
    src.get_navidrome_client = AsyncMock(return_value=None)
    return src


@pytest.fixture
def api(source):
    app = FastAPI()
    setup_music_library_routes(lambda: source)
    app.include_router(router, prefix="/api")
    return TestClient(app)


SHARE_BODY = {
    "type": "cifs", "host": "nas.local", "path": "Music", "name": "NAS-Leo",
}


# =============================================================================
# Storage spaces
# =============================================================================

class TestStoragesListing:

    def test_the_filter_is_served_with_its_counts_and_the_scan_flag(self, api, shares):
        """One shape for both readers: this is the initial load, and every later
        change arrives as `source/storages_changed` carrying the same thing."""
        response = api.get("/api/music-library/storages")

        assert response.status_code == 200
        assert response.json() == {
            "storages": shares.storages_with_stats.return_value,
            "scanning": False,
        }

    def test_a_scan_in_flight_is_reported(self, api, shares):
        shares.scan_state = MagicMock(return_value={"scanning": True, "count": 40})

        assert api.get("/api/music-library/storages").json()["scanning"] is True

    def test_a_share_service_that_raises_is_a_500_not_a_traceback(self, api, shares):
        shares.storages_with_stats = AsyncMock(side_effect=RuntimeError("boom"))

        assert api.get("/api/music-library/storages").status_code == 500


# =============================================================================
# USB keys
# =============================================================================

class TestRenameUsbDevice:

    def test_the_name_reaches_the_store_under_its_uuid(self, api, shares):
        response = api.put(
            f"/api/music-library/usb-devices/{USB_UUID}", json={"name": "iPod de Claire"},
        )

        assert response.json() == {"status": "success"}
        shares.rename_usb.assert_awaited_once_with(USB_UUID, "iPod de Claire")

    def test_a_uuid_that_was_never_mounted_is_a_404(self, api, shares):
        """Not a success: the settings row would keep showing the old name with
        no reason given."""
        shares.rename_usb = AsyncMock(return_value=False)

        response = api.put(
            f"/api/music-library/usb-devices/{USB_UUID}", json={"name": "Nope"},
        )

        assert response.status_code == 404

    def test_an_empty_name_is_accepted_and_means_restore_the_label(self, api, shares):
        api.put(f"/api/music-library/usb-devices/{USB_UUID}", json={"name": ""})

        shares.rename_usb.assert_awaited_once_with(USB_UUID, "")

    def test_a_control_character_never_reaches_the_store(self, api, shares):
        response = api.put(
            f"/api/music-library/usb-devices/{USB_UUID}", json={"name": "iPod\x07"},
        )

        assert response.status_code == 422
        shares.rename_usb.assert_not_awaited()


class TestForgetUsbDevice:

    def test_an_unplugged_key_is_forgotten(self, api, shares):
        response = api.delete(f"/api/music-library/usb-devices/{USB_UUID}")

        assert response.json() == {"status": "success"}
        shares.forget_usb.assert_awaited_once_with(USB_UUID)

    def test_a_key_still_plugged_in_is_refused_before_anything_is_dropped(
        self, api, shares
    ):
        """409, and nothing removed: the next reconcile would see the mount and
        re-create the library, so the only readable outcome is to unplug first.
        Asserted as *not awaited* — a 409 raised after the drop would leave the
        store already changed."""
        shares.usb_is_mounted = AsyncMock(return_value=True)

        response = api.delete(f"/api/music-library/usb-devices/{USB_UUID}")

        assert response.status_code == 409
        shares.forget_usb.assert_not_awaited()

    def test_an_unknown_key_is_a_404(self, api, shares):
        shares.forget_usb = AsyncMock(return_value=False)

        assert api.delete(f"/api/music-library/usb-devices/{USB_UUID}").status_code == 404


# =============================================================================
# Network shares
# =============================================================================

class TestShareRoutes:

    def test_the_configured_shares_are_listed(self, api, shares):
        response = api.get("/api/music-library/shares")

        assert response.json() == {"shares": shares.list.return_value}

    def test_a_new_share_comes_back_with_the_id_it_was_given(self, api, shares):
        response = api.post("/api/music-library/shares", json=SHARE_BODY)

        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert response.json()["share"] == shares.add.return_value

    def test_a_share_body_that_fails_its_guards_never_reaches_the_service(
        self, api, shares
    ):
        response = api.post(
            "/api/music-library/shares", json={**SHARE_BODY, "host": "nas local"},
        )

        assert response.status_code == 422
        shares.add.assert_not_awaited()

    def test_an_edit_replaces_the_config_of_the_named_share(self, api, shares):
        response = api.put("/api/music-library/shares/nas-leo", json=SHARE_BODY)

        assert response.status_code == 200
        assert shares.update.await_args.args[0] == "nas-leo"

    def test_editing_a_share_that_is_gone_is_a_404(self, api, shares):
        """Two tabs on the settings screen is enough to reach this."""
        shares.update = AsyncMock(return_value=None)

        assert api.put("/api/music-library/shares/gone", json=SHARE_BODY).status_code == 404

    def test_a_deleted_share_is_unmounted_and_forgotten(self, api, shares):
        response = api.delete("/api/music-library/shares/nas-leo")

        assert response.json() == {"status": "success"}
        shares.remove.assert_awaited_once_with("nas-leo")

    def test_deleting_a_share_that_is_gone_is_a_404(self, api, shares):
        shares.remove = AsyncMock(return_value=False)

        assert api.delete("/api/music-library/shares/gone").status_code == 404


# =============================================================================
# The store the two USB routes end on
# =============================================================================

class TestUsbNameStore:

    @pytest.fixture
    def store(self, tmp_path):
        service = MusicLibraryDataService()
        service._data_file = tmp_path / "music_library_data.json"
        return service

    async def _known(self, store):
        await store.remember_usb(USB_UUID, "IPOD", "/media/milo/IPOD")

    async def test_a_name_is_kept_against_the_filesystem_uuid(self, store):
        await self._known(store)

        assert await store.set_usb_name(USB_UUID, "iPod de Claire") is True
        assert (await store.get_known_usb())[USB_UUID]["name"] == "iPod de Claire"

    async def test_the_name_survives_the_key_being_mounted_again(self, store):
        """That it comes back with the key is the whole point of filing it under
        the UUID: `remember_usb` runs on every mount and must not wipe it."""
        await self._known(store)
        await store.set_usb_name(USB_UUID, "iPod de Claire")

        await store.remember_usb(USB_UUID, "IPOD", "/media/milo/IPOD_1")

        entry = (await store.get_known_usb())[USB_UUID]
        assert entry["name"] == "iPod de Claire"
        assert entry["mountpoint"] == "/media/milo/IPOD_1"

    async def test_an_emptied_name_falls_back_to_the_disk_label(self, store):
        """Stored as None rather than "": the display reads `name or label`, and
        an empty string would render as a nameless row."""
        await self._known(store)
        await store.set_usb_name(USB_UUID, "iPod de Claire")

        assert await store.set_usb_name(USB_UUID, "") is True
        assert (await store.get_known_usb())[USB_UUID]["name"] is None

    async def test_naming_a_key_that_was_never_mounted_invents_nothing(self, store):
        """`storages()` builds a row per known key, so an entry with no label and
        no mountpoint is a storage space that cannot be browsed or explained."""
        assert await store.set_usb_name("never-seen", "Ghost") is False
        assert await store.get_known_usb() == {}

    async def test_forgetting_a_key_drops_it_from_the_known_set(self, store):
        await self._known(store)

        assert await store.forget_usb(USB_UUID) is True
        assert await store.get_known_usb() == {}

    async def test_forgetting_a_key_that_is_not_there_changes_nothing(self, store):
        await self._known(store)

        assert await store.forget_usb("never-seen") is False
        assert set(await store.get_known_usb()) == {USB_UUID}

    async def test_forgetting_one_key_leaves_the_others_alone(self, store):
        await self._known(store)
        await store.remember_usb("EEEE-FFFF", "OTHER", "/media/milo/OTHER")

        await store.forget_usb(USB_UUID)

        assert set(await store.get_known_usb()) == {"EEEE-FFFF"}
