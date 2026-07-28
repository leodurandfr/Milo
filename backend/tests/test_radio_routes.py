# backend/tests/test_radio_routes.py
"""Route-level tests for the radio source."""
from unittest.mock import Mock

import pytest

from backend.sources.radio.routes import get_custom_stations

class TestCustomStationsMerge:
    """GET /api/radio/custom must answer with what the user last saved.

    A custom station lives in both stores at once: the record written at creation
    (`manual_stations`) and the override written by every later save
    (`modified_metadata`). When the two disagree, the override is the newer of the
    two — the priority `_lookup_local` documents and `enrich_with_favorite_status`
    already applies. Merging the other way serves the pre-edit record to
    Réglages → Webradio on every page load, and the edit form then re-commits it.

    Consumer: `radioStore.fetchCustomStations()` → RadioSettings.vue.
    """

    @staticmethod
    def _station_data(modified, manual):
        data = Mock()
        data.get_modified_metadata = Mock(return_value=modified)
        data.get_manual_stations = Mock(return_value=manual)
        return data

    async def _call(self, modified, manual):
        source = Mock()
        source.station_data = self._station_data(modified, manual)
        return await get_custom_stations(source=source)

    @pytest.mark.asyncio
    async def test_edit_of_a_custom_station_wins_over_its_creation_record(self):
        result = await self._call(
            modified={"custom_1": {"name": "Renamed", "genre": "Jazz"}},
            manual={"custom_1": {"id": "custom_1", "name": "Created",
                                 "genre": "", "is_custom": True}},
        )
        assert result["custom_1"]["name"] == "Renamed"
        assert result["custom_1"]["genre"] == "Jazz"

    @pytest.mark.asyncio
    async def test_fields_only_the_creation_record_carries_survive_the_overlay(self):
        result = await self._call(
            modified={"custom_1": {"name": "Renamed"}},
            manual={"custom_1": {"id": "custom_1", "name": "Created", "is_custom": True}},
        )
        assert result["custom_1"]["id"] == "custom_1"
        assert result["custom_1"]["is_custom"] is True

    @pytest.mark.asyncio
    async def test_a_modified_favourite_has_no_creation_record_and_is_returned_alone(self):
        result = await self._call(
            modified={"api_42": {"name": "Renamed favourite"}},
            manual={"custom_1": {"id": "custom_1", "name": "Created"}},
        )
        assert result["api_42"]["name"] == "Renamed favourite"
        assert result["custom_1"]["name"] == "Created"
