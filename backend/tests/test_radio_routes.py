# backend/tests/test_radio_routes.py
"""Route-level tests for the radio source."""
from unittest.mock import Mock, AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.sources.radio.routes import get_custom_stations, setup_radio_routes
from backend.sources.radio.source import RadioSource

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


class TestFavoritesHaveOneEntryPoint:
    """Favourites are a *service* the radio source exposes, not a playback command.

    `add_favorite`/`remove_favorite` lived in `COMMANDS` with handlers that only
    forwarded to `station_data`, while the two dedicated routes reached the same
    service through `run_source_command`. Two entry points to one operation is the
    forwarding-layer this repo forbids: `routes.py` reaches a non-playback service
    through the source *property* and calls the service's own method.

    Consumer: `radioStore.addFavorite()` / `removeFavorite()` → FavoritesView.vue.
    """

    @staticmethod
    def _client(station_data, radio_api=None):
        app = FastAPI()
        source = Mock()
        source.station_data = station_data
        source.radio_api = radio_api or Mock()
        app.include_router(setup_radio_routes(lambda: source), prefix="/api")
        return TestClient(app), source

    def test_neither_favourite_operation_is_a_command(self):
        """A second dispatch path is what lets the two drift apart."""
        assert "add_favorite" not in RadioSource.COMMANDS
        assert "remove_favorite" not in RadioSource.COMMANDS

    def test_add_reaches_station_data_without_going_through_command(self):
        data = Mock()
        data.add_favorite = AsyncMock(return_value=True)
        client, source = self._client(data)

        response = client.post("/api/radio/favorites",
                               json={"station_id": "s1", "station": {"name": "Test"}})

        assert response.status_code == 200
        assert response.json()["status"] == "success"
        data.add_favorite.assert_awaited_once_with("s1", {"name": "Test"})
        source.command.assert_not_called()

    def test_add_resolves_an_unknown_station_through_the_api(self):
        """The station body is optional — a bare id is looked up, as the command
        handler did, so favouriting from a context with no cached record works."""
        data = Mock()
        data.add_favorite = AsyncMock(return_value=True)
        api = Mock()
        api.get_station_by_id = AsyncMock(return_value={"name": "Fetched"})
        client, _ = self._client(data, api)

        response = client.post("/api/radio/favorites", json={"station_id": "s1"})

        assert response.status_code == 200
        api.get_station_by_id.assert_awaited_once_with("s1")
        data.add_favorite.assert_awaited_once_with("s1", {"name": "Fetched"})

    def test_add_of_a_station_that_resolves_to_nothing_is_a_404(self):
        data = Mock()
        data.add_favorite = AsyncMock(return_value=True)
        api = Mock()
        api.get_station_by_id = AsyncMock(return_value=None)
        client, _ = self._client(data, api)

        assert client.post("/api/radio/favorites",
                           json={"station_id": "ghost"}).status_code == 404
        # Asserting the lookup ran is what keeps this from passing on a missing
        # route, which answers 404 too.
        api.get_station_by_id.assert_awaited_once_with("ghost")
        data.add_favorite.assert_not_awaited()

    def test_a_refused_write_raises_instead_of_answering_200(self):
        """`station_data` reports a failed save as False. Passing that through as
        a body flag is the `{"success": bool}` non-envelope — it must raise."""
        data = Mock()
        data.add_favorite = AsyncMock(return_value=False)
        client, _ = self._client(data)

        # A falsy `station` sends the route to RadioBrowser first, and a Mock
        # `radio_api` raises there — the same "any other 500" the twin below
        # names. The record has to be non-empty for the refusal to be reached.
        assert client.post(
            "/api/radio/favorites",
            json={"station_id": "s1", "station": {"name": "FIP"}},
        ).status_code == 500
        data.add_favorite.assert_awaited_once_with("s1", {"name": "FIP"})

    def test_remove_reaches_station_data_without_going_through_command(self):
        data = Mock()
        data.remove_favorite = AsyncMock(return_value=True)
        client, source = self._client(data)

        response = client.delete("/api/radio/favorites/s1")

        assert response.status_code == 200
        assert response.json()["status"] == "success"
        data.remove_favorite.assert_awaited_once_with("s1")
        source.command.assert_not_called()

    def test_remove_that_fails_to_persist_raises(self):
        data = Mock()
        data.remove_favorite = AsyncMock(return_value=False)
        client, _ = self._client(data)

        assert client.delete("/api/radio/favorites/s1").status_code == 500
        # Without this the test also passes on any other 500 — a Mock source
        # blowing up inside run_source_command, for instance.
        data.remove_favorite.assert_awaited_once_with("s1")
