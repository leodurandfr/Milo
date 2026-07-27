# backend/tests/test_radio_data.py
"""StationDataService — the two-store lifecycle of a custom station.

A custom station lives in `manual_stations` from creation, and every later save
writes an override into `modified_metadata`. Anything that treats one store as
the whole station leaves the other behind.
"""
from unittest.mock import AsyncMock

import pytest

from backend.sources.radio.data import StationDataService


@pytest.fixture
def data(tmp_path):
    svc = StationDataService()
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
