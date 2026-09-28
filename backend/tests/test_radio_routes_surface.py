# backend/tests/test_radio_routes_surface.py
"""The ten radio routes that had never been entered.

Measured 2026-08-25: `sources/radio/routes.py` ran at 39,1 % of its lines.
`test_radio_routes.py` covers the two favourite routes and the custom-station
merge; everything else — search, the station image, the favicon proxy, the
custom-station writes and the metadata modify/restore pair — was at 0 %.

Two of them have a consumer outside `frontend/src/`:
`GET /api/radio/stations` is pinned by `tests/contracts/milo_mac_contract.json`
as `MiloAPIService.getRadioFavorites`, and the vendored snapshot decodes each
station into `RadioStation { id: String, name: String }` with **non-optional**
fields — one station missing either and the Mac app's whole favourites list
fails with `APIError.invalidResponse`.

The favicon proxy is covered with the logo cache behind it, in
test_radio_logos.py. Nothing here reaches the network: the conftest guard
refuses (and fails) any connect off this host.
"""
import io
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.sources.radio.routes import setup_radio_routes


@pytest.fixture
def station_data():
    data = Mock()
    data.get_favorites_with_metadata = AsyncMock(return_value=[])
    data.enrich_with_favorite_status = Mock(side_effect=lambda s: s)
    data.add_custom_station = AsyncMock(return_value={"success": True, "station": {}})
    data.remove_custom_station = AsyncMock(return_value=True)
    data.modify_favorite_metadata = AsyncMock(return_value={"success": True, "station": {}})
    data.restore_favorite_metadata = AsyncMock(return_value={"success": True})
    data.image_manager = Mock()
    data.image_manager.validate_and_save_image = AsyncMock(
        return_value=(True, "abc123.webp", None)
    )
    data.image_manager.delete_image = AsyncMock(return_value=True)
    data.image_manager.as_jpeg = AsyncMock(return_value=b"\xff\xd8\xffjpeg")
    data.image_manager.get_image_path = Mock(return_value=None)
    return data


@pytest.fixture
def radio_api():
    api = Mock()
    api.search_stations = AsyncMock(return_value={"stations": [], "total": 0})
    api.get_available_countries = AsyncMock(return_value=[])
    return api


@pytest.fixture
def client(station_data, radio_api):
    app = FastAPI()
    source = Mock()
    source.station_data = station_data
    source.radio_api = radio_api
    app.include_router(setup_radio_routes(lambda: source), prefix="/api")
    return TestClient(app)


class TestFavouritesListing:
    """`GET /api/radio/stations?favorites_only=true` — read by Milō *and* the Mac."""

    def test_the_favourites_branch_never_reaches_the_directory(
        self, client, station_data, radio_api
    ):
        """Favourites are local. Routing them through radio-browser makes the
        user's own list unavailable whenever the directory is down — which is
        exactly when `api_error` is set on the other branch."""
        station_data.get_favorites_with_metadata = AsyncMock(
            return_value=[{"id": "s1", "name": "FIP"}]
        )

        response = client.get("/api/radio/stations?favorites_only=true")

        assert response.status_code == 200
        assert response.json()["stations"] == [{"id": "s1", "name": "FIP"}]
        radio_api.search_stations.assert_not_awaited()

    def test_every_station_carries_the_two_fields_milo_mac_decodes(
        self, client, station_data
    ):
        """`RadioStation` in the vendored snapshot declares `id` and `name`
        non-optional, so one station missing either loses the *whole* list on
        the Mac — not just that row."""
        station_data.get_favorites_with_metadata = AsyncMock(return_value=[
            {"id": "s1", "name": "FIP", "url": "http://a"},
            {"id": "s2", "name": "TSF Jazz", "url": "http://b"},
        ])

        stations = client.get("/api/radio/stations?favorites_only=true").json()["stations"]

        assert len(stations) == 2
        for station in stations:
            assert isinstance(station["id"], str) and station["id"]
            assert isinstance(station["name"], str) and station["name"]

    def test_an_empty_favourites_list_is_a_total_of_zero_not_an_error(self, client):
        response = client.get("/api/radio/stations?favorites_only=true")

        assert response.status_code == 200
        assert response.json() == {"stations": [], "total": 0}

    def test_the_query_filters_on_name_or_genre(self, client, station_data):
        station_data.get_favorites_with_metadata = AsyncMock(return_value=[
            {"id": "1", "name": "FIP", "genre": "eclectic", "country": "France"},
            {"id": "2", "name": "TSF", "genre": "jazz", "country": "France"},
        ])

        stations = client.get(
            "/api/radio/stations?favorites_only=true&query=jazz"
        ).json()["stations"]

        assert [s["id"] for s in stations] == ["2"]

    def test_the_country_and_genre_filters_compose(self, client, station_data):
        station_data.get_favorites_with_metadata = AsyncMock(return_value=[
            {"id": "1", "name": "A", "genre": "jazz", "country": "France"},
            {"id": "2", "name": "B", "genre": "jazz", "country": "Belgium"},
            {"id": "3", "name": "C", "genre": "rock", "country": "France"},
        ])

        stations = client.get(
            "/api/radio/stations?favorites_only=true&country=France&genre=jazz"
        ).json()["stations"]

        assert [s["id"] for s in stations] == ["1"]

    def test_the_total_counts_the_matches_not_the_page(self, client, station_data):
        """`total` drives "N stations" under a list the `limit` truncated."""
        station_data.get_favorites_with_metadata = AsyncMock(
            return_value=[{"id": str(i), "name": f"S{i}"} for i in range(10)]
        )

        body = client.get("/api/radio/stations?favorites_only=true&limit=3").json()

        assert len(body["stations"]) == 3
        assert body["total"] == 10

    def test_favourite_status_is_stamped_before_the_list_goes_out(
        self, client, station_data
    ):
        """The star in the list comes from this call, not from the store."""
        station_data.get_favorites_with_metadata = AsyncMock(
            return_value=[{"id": "s1", "name": "FIP"}]
        )

        client.get("/api/radio/stations?favorites_only=true")

        station_data.enrich_with_favorite_status.assert_called_once()


class TestSearch:
    """The other branch of the same route: the directory."""

    def test_a_search_goes_to_the_directory_with_every_filter(
        self, client, radio_api, station_data
    ):
        radio_api.search_stations = AsyncMock(
            return_value={"stations": [{"id": "x", "name": "X"}], "total": 1}
        )

        body = client.get(
            "/api/radio/stations?query=jazz&country=France&genre=jazz&limit=25"
        ).json()

        radio_api.search_stations.assert_awaited_once_with(
            query="jazz", country="France", genre="jazz", limit=25
        )
        assert body["total"] == 1
        station_data.get_favorites_with_metadata.assert_not_awaited()

    def test_a_degraded_directory_is_flagged_rather_than_hidden(self, client, radio_api):
        """`api_error` says the *directory* did not answer, not that the unit is
        offline. The UI shows "search unavailable" over a working player."""
        radio_api.search_stations = AsyncMock(
            return_value={"stations": [], "total": 0, "api_error": True}
        )

        assert client.get("/api/radio/stations?query=x").json()["api_error"] is True

    def test_a_healthy_search_carries_no_error_flag_at_all(self, client, radio_api):
        """`response_model_exclude_none` is what keeps the key absent; present
        and false would trip a truthiness check nowhere and a `in` check here."""
        radio_api.search_stations = AsyncMock(
            return_value={"stations": [], "total": 0}
        )

        assert "api_error" not in client.get("/api/radio/stations?query=x").json()

    def test_countries_come_straight_from_the_directory(self, client, radio_api):
        radio_api.get_available_countries = AsyncMock(
            return_value=[{"name": "France", "stationcount": 12}]
        )

        body = client.get("/api/radio/countries").json()

        assert body == [{"name": "France", "stationcount": 12}]


class TestCustomStations:
    """Creation and deletion, and what happens to an image when the write fails."""

    def test_a_station_is_created_with_the_form_it_was_given(self, client, station_data):
        station_data.add_custom_station = AsyncMock(
            return_value={"success": True, "station": {"id": "custom_1", "name": "Mine"}}
        )

        response = client.post("/api/radio/custom", data={
            "name": "Mine", "url": "http://x", "country": "France",
            "countrycode": "FR", "genre": "jazz", "bitrate": "128",
            "codec": "MP3", "shazam_enabled": "false",
        })

        assert response.status_code == 200
        assert response.json()["station"]["id"] == "custom_1"
        assert station_data.add_custom_station.await_args.kwargs["shazam_enabled"] is False
        assert station_data.add_custom_station.await_args.kwargs["image_filename"] == ""

    def test_an_uploaded_image_is_validated_and_its_saved_name_is_stored(
        self, client, station_data
    ):
        response = client.post(
            "/api/radio/custom",
            data={"name": "Mine", "url": "http://x"},
            files={"image": ("logo.png", io.BytesIO(b"png-bytes"), "image/png")},
        )

        assert response.status_code == 200
        station_data.image_manager.validate_and_save_image.assert_awaited_once_with(
            file_content=b"png-bytes", filename="logo.png"
        )
        assert (station_data.add_custom_station.await_args.kwargs["image_filename"]
                == "abc123.webp")

    def test_a_rejected_image_stops_the_creation_and_names_the_reason(
        self, client, station_data
    ):
        station_data.image_manager.validate_and_save_image = AsyncMock(
            return_value=(False, None, "Image too large (7.2MB)")
        )

        response = client.post(
            "/api/radio/custom",
            data={"name": "Mine", "url": "http://x"},
            files={"image": ("logo.png", io.BytesIO(b"x"), "image/png")},
        )

        assert response.status_code == 400
        assert "Image too large (7.2MB)" in response.json()["detail"]
        station_data.add_custom_station.assert_not_awaited()

    def test_an_image_saved_for_a_creation_that_then_fails_is_deleted(
        self, client, station_data
    ):
        """The file is already on disk by the time the store refuses. Without
        this the unit accumulates orphan WebPs no screen ever lists and no
        deletion path can reach — its filename was never persisted anywhere."""
        station_data.add_custom_station = AsyncMock(
            return_value={"success": False, "error": "URL already used"}
        )

        response = client.post(
            "/api/radio/custom",
            data={"name": "Mine", "url": "http://x"},
            files={"image": ("logo.png", io.BytesIO(b"x"), "image/png")},
        )

        assert response.status_code == 400
        station_data.image_manager.delete_image.assert_awaited_once_with("abc123.webp")

    def test_nothing_is_deleted_when_a_creation_without_an_image_fails(
        self, client, station_data
    ):
        """`image_filename` is `""` on this path — deleting on a falsy name is
        how a cleanup starts reaching for other stations' files."""
        station_data.add_custom_station = AsyncMock(
            return_value={"success": False, "error": "nope"}
        )

        client.post("/api/radio/custom", data={"name": "Mine", "url": "http://x"})

        station_data.image_manager.delete_image.assert_not_awaited()

    def test_deleting_a_custom_station_reports_a_refusal(self, client, station_data):
        station_data.remove_custom_station = AsyncMock(return_value=False)

        assert client.delete("/api/radio/custom/custom_1").status_code == 400
        station_data.remove_custom_station.assert_awaited_once_with("custom_1")

    def test_deleting_a_custom_station_that_exists_succeeds(self, client, station_data):
        response = client.delete("/api/radio/custom/custom_1")

        assert response.status_code == 200
        assert response.json()["status"] == "success"


class TestFavouriteMetadata:
    """Editing a favourite, and putting it back."""

    def test_every_edited_field_reaches_the_store(self, client, station_data):
        client.post("/api/radio/favorites/modify-metadata", data={
            "station_id": "s1", "name": "Renamed", "url": "http://new",
            "country": "France", "countrycode": "FR", "genre": "jazz",
            "codec": "AAC", "bitrate": "192", "shazam_enabled": "false",
        })

        kwargs = station_data.modify_favorite_metadata.await_args.kwargs
        assert kwargs["station_id"] == "s1"
        assert kwargs["name"] == "Renamed"
        assert kwargs["url"] == "http://new"
        assert kwargs["genre"] == "jazz"
        assert kwargs["codec"] == "AAC"
        assert kwargs["bitrate"] == 192
        assert kwargs["shazam_enabled"] is False

    def test_removing_the_image_is_an_empty_name_not_a_missing_one(
        self, client, station_data
    ):
        """Three states share one argument: `None` leaves the current image
        alone, `""` clears it, a filename replaces it. Collapsing the first two
        makes every ordinary rename drop the station's logo."""
        client.post("/api/radio/favorites/modify-metadata",
                    data={"station_id": "s1", "name": "FIP", "url": "http://x",
                          "remove_image": "true"})
        assert station_data.modify_favorite_metadata.await_args.kwargs["image_filename"] == ""

        station_data.modify_favorite_metadata.reset_mock()
        client.post("/api/radio/favorites/modify-metadata",
                    data={"station_id": "s1", "name": "FIP", "url": "http://x"})
        assert station_data.modify_favorite_metadata.await_args.kwargs["image_filename"] is None

    def test_a_new_image_wins_over_a_removal_flag(self, client, station_data):
        client.post(
            "/api/radio/favorites/modify-metadata",
            data={"station_id": "s1", "name": "FIP", "url": "http://x",
                  "remove_image": "true"},
            files={"image": ("logo.png", io.BytesIO(b"x"), "image/png")},
        )

        assert (station_data.modify_favorite_metadata.await_args.kwargs["image_filename"]
                == "abc123.webp")

    def test_an_image_saved_for_an_edit_that_then_fails_is_deleted(
        self, client, station_data
    ):
        station_data.modify_favorite_metadata = AsyncMock(
            return_value={"success": False, "error": "unknown station"}
        )

        response = client.post(
            "/api/radio/favorites/modify-metadata",
            data={"station_id": "s1", "name": "FIP", "url": "http://x"},
            files={"image": ("logo.png", io.BytesIO(b"x"), "image/png")},
        )

        assert response.status_code == 400
        station_data.image_manager.delete_image.assert_awaited_once_with("abc123.webp")

    def test_a_restore_passes_the_directory_client_so_the_original_can_be_refetched(
        self, client, station_data, radio_api
    ):
        response = client.post("/api/radio/favorites/restore-metadata",
                               data={"station_id": "s1"})

        assert response.status_code == 200
        station_data.restore_favorite_metadata.assert_awaited_once_with(
            station_id="s1", radio_api=radio_api
        )

    def test_a_restore_with_nothing_to_restore_is_a_400_not_a_silent_success(
        self, client, station_data
    ):
        station_data.restore_favorite_metadata = AsyncMock(
            return_value={"success": False, "error": "No original metadata to restore"}
        )

        response = client.post("/api/radio/favorites/restore-metadata",
                               data={"station_id": "s1"})

        assert response.status_code == 400
        assert "No original metadata" in response.json()["detail"]


class TestStationImage:
    """`GET /api/radio/images/{filename}` serves a custom station's upload."""

    def test_a_missing_image_is_a_404(self, client, station_data):
        station_data.image_manager.get_image_path = Mock(return_value=None)

        assert client.get("/api/radio/images/gone.webp").status_code == 404

    def test_a_path_the_manager_refuses_is_a_404_not_a_500(self, client, station_data):
        """`get_image_path` answers None for a traversal attempt as well as for
        an absent file — the route must treat both as "not here"."""
        station_data.image_manager.get_image_path = Mock(return_value=None)

        assert client.get("/api/radio/images/..%2F..%2Fsettings.json").status_code == 404

    @pytest.mark.parametrize("suffix,media_type", [
        (".webp", "image/webp"),
        (".png", "image/png"),
        (".jpg", "image/jpeg"),
        (".jpeg", "image/jpeg"),
        (".gif", "image/gif"),
    ])
    def test_the_media_type_follows_the_file_it_serves(
        self, client, station_data, tmp_path, suffix, media_type
    ):
        """A WebP served as `application/octet-stream` is a download prompt
        where the list expects a logo.

        `Accept` is spelled out because the answer now depends on it: a caller
        that does not read WebP is handed a JPEG rendition instead of the file.
        That is the negotiation, covered in test_radio_routes.py; here we pin
        the plain case, where the caller takes the file as stored.
        """
        image = tmp_path / f"logo{suffix}"
        image.write_bytes(b"bytes")
        station_data.image_manager.get_image_path = Mock(return_value=image)

        response = client.get(f"/api/radio/images/logo{suffix}",
                              headers={"Accept": "image/webp,*/*"})

        assert response.status_code == 200
        assert response.headers["content-type"] == media_type

    def test_a_conversion_that_blows_up_still_serves_the_file(
        self, client, station_data, tmp_path
    ):
        """An image route holding a perfectly good file has no business
        answering 500 because a rendition failed."""
        image = tmp_path / "logo.webp"
        image.write_bytes(b"bytes")
        station_data.image_manager.get_image_path = Mock(return_value=image)
        station_data.image_manager.as_jpeg = AsyncMock(side_effect=OSError("boom"))

        response = client.get("/api/radio/images/logo.webp",
                              headers={"Accept": "*/*"})

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/webp"

    def test_an_unknown_suffix_falls_back_to_a_binary_type(
        self, client, station_data, tmp_path
    ):
        image = tmp_path / "logo.bmp"
        image.write_bytes(b"bytes")
        station_data.image_manager.get_image_path = Mock(return_value=image)

        response = client.get("/api/radio/images/logo.bmp")

        assert response.headers["content-type"] == "application/octet-stream"

    def test_the_upload_is_served_immutable(self, client, station_data, tmp_path):
        """The filename is a fresh uuid per save, so the content behind a given
        name never changes and the kiosk should not re-request it."""
        image = tmp_path / "logo.webp"
        image.write_bytes(b"bytes")
        station_data.image_manager.get_image_path = Mock(return_value=image)

        headers = client.get("/api/radio/images/logo.webp",
                             headers={"Accept": "image/webp,*/*"}).headers

        assert headers["cache-control"] == "public, max-age=31536000"
