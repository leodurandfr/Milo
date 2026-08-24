# backend/tests/test_radio_data.py
"""StationDataService — the two-store lifecycle of an edited station.

A custom station lives in `manual_stations` from creation, a favourite's
original in `favorites_cache`, and every later save writes an override into
`modified_metadata`. Anything that treats one store as the whole station leaves
the other behind — a stale record, an orphaned upload, or a dropped image.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.sources.radio.data import StationDataService


@pytest.fixture
def data(tmp_path):
    svc = StationDataService(state_machine=MagicMock(broadcast=AsyncMock()))
    svc._data_file = tmp_path / "radio_data.json"
    svc.image_manager.delete_image = AsyncMock(return_value=True)
    return svc


async def _create_then_edit(data, *, image="", new_image=None):
    created = await data.add_custom_station(
        name="Created", url="http://example.invalid/s", image_filename=image,
    )
    station_id = created["station"]["id"]
    await data.modify_favorite_metadata(
        station_id, name="Renamed", url="http://example.invalid/s",
        image_filename=new_image,
    )
    return station_id


class TestRemoveCustomStation:
    """Deleting an *edited* custom station must leave nothing behind.

    When it left the override, `GET /api/radio/custom` kept serving the station:
    Réglages → Webradio showed a card for a station that no longer existed, its
    edit form re-created it, and a second delete answered 400 for good.
    """

    @pytest.mark.asyncio
    async def test_delete_removes_the_override_too(self, data):
        station_id = await _create_then_edit(data)

        assert await data.remove_custom_station(station_id) is True

        assert station_id not in data.get_manual_stations()
        assert station_id not in data.get_modified_metadata()

    @pytest.mark.asyncio
    async def test_a_station_deleted_once_does_not_come_back(self, data):
        station_id = await _create_then_edit(data)
        await data.remove_custom_station(station_id)

        # Second delete: nothing left to remove, so it reports so.
        assert await data.remove_custom_station(station_id) is False

    @pytest.mark.asyncio
    async def test_both_images_are_deleted_when_a_save_uploaded_a_new_one(self, data):
        station_id = await _create_then_edit(data, image="first.webp", new_image="second.webp")

        await data.remove_custom_station(station_id)

        deleted = {call.args[0] for call in data.image_manager.delete_image.call_args_list}
        assert deleted == {"first.webp", "second.webp"}

    @pytest.mark.asyncio
    async def test_an_unedited_custom_station_still_deletes(self, data):
        created = await data.add_custom_station(name="Solo", url="http://example.invalid/x")
        station_id = created["station"]["id"]

        assert await data.remove_custom_station(station_id) is True
        assert station_id not in data.get_manual_stations()

    @pytest.mark.asyncio
    async def test_a_station_that_exists_only_as_an_override_can_be_deleted(self, data):
        # The state the old delete left behind, and the one a unit carries today:
        # an override for a custom id whose creation record is already gone.
        await data.modify_favorite_metadata(
            "custom_ghost", name="Ghost", url="http://example.invalid/g",
        )

        assert await data.remove_custom_station("custom_ghost") is True
        assert "custom_ghost" not in data.get_modified_metadata()

    @pytest.mark.asyncio
    async def test_an_unknown_station_is_refused(self, data):
        assert await data.remove_custom_station("custom_nope") is False
        assert await data.remove_custom_station("api_42") is False
class TestModifyStationImage:
    """A save owns the upload it replaces — and only the one it replaces.

    Both directions were wrong at once: a re-upload left the previous .webp in
    /var/lib/milo/radio_images forever (nothing can name it again once the
    override points elsewhere), and a save carrying no upload read the image
    from the API cache — where a custom station has none — so renaming one
    silently dropped the image it was showing.
    """

    @pytest.mark.asyncio
    async def test_a_new_upload_deletes_the_file_it_replaces(self, data):
        station_id = await _create_then_edit(
            data, image="first.webp", new_image="second.webp"
        )

        data.image_manager.delete_image.assert_awaited_once_with("first.webp")
        assert data.get_favorite_metadata_local(station_id)["image_filename"] == "second.webp"

    @pytest.mark.asyncio
    async def test_a_save_without_an_upload_keeps_the_current_image(self, data):
        station_id = await _create_then_edit(data, image="only.webp", new_image=None)

        station = data.get_favorite_metadata_local(station_id)
        assert station["image_filename"] == "only.webp"
        assert station["favicon"] == "/api/radio/images/only.webp"
        data.image_manager.delete_image.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_removing_the_image_deletes_the_file(self, data):
        station_id = await _create_then_edit(data, image="only.webp", new_image="")

        data.image_manager.delete_image.assert_awaited_once_with("only.webp")
        assert data.get_favorite_metadata_local(station_id)["favicon"] == ""


class TestRestoreFavoriteMetadata:
    """Restoring must announce the station it restored.

    The stores hold stations by value, so without the event the favorites list
    kept serving the override — uploaded image included, still rendered from the
    browser cache after this method deleted the file.
    """

    @pytest.mark.asyncio
    async def test_the_restored_station_is_broadcast(self, data):
        station_id = "api-42"
        data._favorites.append(station_id)
        data._favorites_cache[station_id] = {"name": "Origin", "favicon": "http://origin/logo.png"}
        await data.modify_favorite_metadata(
            station_id, name="Renamed", url="http://example.invalid/s",
            image_filename="upload.webp",
        )
        data._state_machine.broadcast.reset_mock()

        assert (await data.restore_favorite_metadata(station_id))["success"] is True

        data.image_manager.delete_image.assert_awaited_once_with("upload.webp")
        event = data._state_machine.broadcast.await_args.args[0]
        assert event.TYPE == "favorite_modified"
        assert event.station["favicon"] == "http://origin/logo.png"
        assert event.station["id"] == station_id
        assert event.station["is_favorite"] is True


class TestBlankNameIsRefused:
    """The guard ran before the strip, so whitespace was not "empty" yet.

    Found while probing Phase 4 on the unit: `POST /api/radio/custom/add` with a
    whitespace-only name and url answered `{"status": "success"}` and created a
    station with neither — a card with no title that plays nothing, and no way to
    tell it apart from a real one except by opening it.
    """

    @pytest.mark.parametrize("name,url", [
        ("   ", "http://example.invalid/s"),
        ("Real name", "  "),
        ("\t", "\n"),
    ])
    async def test_whitespace_cannot_create_a_station(self, data, name, url):
        result = await data.add_custom_station(name=name, url=url)

        assert result["success"] is False
        assert data._manual_stations == {}, "a blank station was stored"

    async def test_whitespace_cannot_blank_an_existing_favourite(self, data):
        created = await data.add_custom_station(
            name="Created", url="http://example.invalid/s",
        )
        station_id = created["station"]["id"]

        result = await data.modify_favorite_metadata(
            station_id, name="   ", url="http://example.invalid/s",
        )

        assert result["success"] is False
        assert station_id not in data._modified_metadata
        assert data._manual_stations[station_id]["name"] == "Created"

    async def test_a_padded_name_is_still_accepted_trimmed(self, data):
        """The strip itself must survive the reorder — a name typed with a
        trailing space is ordinary input, not an error."""
        result = await data.add_custom_station(
            name="  France Inter  ", url="  http://example.invalid/s  ",
        )

        assert result["success"] is True
        assert result["station"]["name"] == "France Inter"
        assert result["station"]["url"] == "http://example.invalid/s"


class TestModifiedStationsList:
    """`GET /api/radio/custom` lists what the user actually edited.

    Turning Shazam off writes a full override, original values included: listing
    every override would file the station under "Modified" in Réglages → Webradio
    for a preference the user set elsewhere, and offer to restore metadata that
    was never changed. The predicate that separates the two ran in no test.

    Consumer: `radioStore.fetchCustomStations()` → RadioSettings.vue.
    """

    ORIGINAL = {"name": "France Inter", "url": "http://example.invalid/fi",
                "country": "France", "genre": "News", "codec": "MP3",
                "bitrate": 128, "image_filename": "", "favicon": "http://origin/logo.png"}

    async def _save_as_is(self, data, **overrides):
        data._favorites_cache["api-1"] = dict(self.ORIGINAL)
        fields = {k: self.ORIGINAL[k] for k in
                  ("name", "url", "country", "genre", "codec", "bitrate")}
        await data.modify_favorite_metadata("api-1", **fields, **overrides)

    async def test_a_shazam_only_change_is_not_a_metadata_change(self, data):
        await self._save_as_is(data, shazam_enabled=False)

        assert data.is_station_shazam_enabled("api-1") is False, "the preference was not stored"
        assert "api-1" not in data.get_modified_metadata()

    async def test_a_renamed_station_is_listed(self, data):
        await self._save_as_is(data)
        await data.modify_favorite_metadata(
            "api-1", name="Renamed", url=self.ORIGINAL["url"],
        )

        assert data.get_modified_metadata()["api-1"]["name"] == "Renamed"

    async def test_a_custom_id_is_never_resolved_from_the_api_cache(self, data):
        # `get_custom_station_by_id` answers the "is this one of ours" question
        # for browser_api; the API cache holds records for stations that are not.
        data._favorites_cache["custom_1"] = {"name": "Cached, not authored here"}

        assert data.get_custom_station_by_id("custom_1") is None
        assert data.get_favorite_metadata_local("custom_1")["name"] == "Cached, not authored here"

    async def test_the_two_station_stores_answer_with_copies(self, data):
        created = await data.add_custom_station(
            name="Created", url="http://example.invalid/s",
        )
        station_id = created["station"]["id"]
        await data.modify_favorite_metadata(
            station_id, name="Renamed", url="http://example.invalid/s",
        )

        data.get_manual_stations()[station_id]["name"] = "mutated"
        data.get_modified_metadata()[station_id]["name"] = "mutated"

        assert data._manual_stations[station_id]["name"] == "Created"
        assert data._modified_metadata[station_id]["name"] == "Renamed"


