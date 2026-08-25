# backend/tests/test_radio_lookups.py
"""Resolving a station the user already named, and the stores behind it.

Measured 2026-08-25: `browser_api.py` ran at 72,3 % and four of its public
methods were never entered — `fetch_remote_station`, `get_station_by_id`,
`get_stations_by_ids` and `increment_station_clicks`. Between them they are how
a favourite becomes a row in the list and how a tap becomes a stream URL, so the
directory's *quality* filters were deciding the fate of stations the user had
already chosen, with nothing watching.

Also covers `StationDataService.restore_favorite_metadata` and
`ImageManager.validate_and_save_image` (44 lines, 0 %), whose only tests were
two assertions restating the class's own constants.

`ImageManager.IMAGES_DIR` is repointed at `tmp_path` everywhere: it defaults to
`/var/lib/milo/radio_images` and the write goes through `aiofiles`.
"""
import io
from unittest.mock import AsyncMock, Mock, patch

import pytest
from PIL import Image

from backend.shared.network import NetworkUnavailableError
from backend.sources.radio.browser_api import RadioBrowserAPI
from backend.sources.radio.data import ImageManager, StationDataService


def _remote(**overrides):
    """A station as radio-browser.info actually returns one.

    Captured from the directory's `stations/byuuid` payload: the fields
    `_is_valid_station` and `_normalize_station` read, with the health flags set
    to "checked and up".
    """
    station = {
        "stationuuid": "s1",
        "name": "FIP",
        "url_resolved": "http://stream/fip",
        "codec": "MP3",
        "bitrate": 128,
        "lastcheckok": 1,
        "country": "France",
        "countrycode": "fr",
        "tags": "eclectic",
        "favicon": "https://cdn.example.com/fip-512x512.png",
        "votes": 10,
        "clickcount": 20,
        "hls": 0,
        "ssl_error": 0,
    }
    station.update(overrides)
    return station


@pytest.fixture
def api():
    return RadioBrowserAPI()


class TestAnExplicitLookupIsNotASearch:
    """`fetch_remote_station` resolves a station the caller already named.

    It sits under `get_station_by_id` (a tap on a station), under
    `get_stations_by_ids` (the favourites list) and under
    `get_station_metadata` (a favourite with no cached record). Applying the
    directory's health filters here does not improve a list — it deletes a
    station the user chose.
    """

    @pytest.mark.asyncio
    async def test_a_station_the_directory_last_saw_offline_is_still_resolved(
        self, api
    ):
        """`lastcheckok` is measured from radio-browser's own infrastructure, not
        from this LAN: a geo-restricted stream, or one that refuses their
        checker's user agent, is marked down while playing fine here. Dropping
        it turned a working station into `Station <id> not found` and took it out
        of the favourites list on the way."""
        with patch.object(api, "_request",
                          new=AsyncMock(return_value=[_remote(lastcheckok=0)])):
            station = await api.fetch_remote_station("s1")

        assert station is not None
        assert station["url"] == "http://stream/fip"

    @pytest.mark.asyncio
    async def test_a_station_with_an_unknown_codec_is_still_resolved(self, api):
        with patch.object(api, "_request",
                          new=AsyncMock(return_value=[_remote(codec="UNKNOWN")])):
            assert await api.fetch_remote_station("s1") is not None

    @pytest.mark.asyncio
    async def test_a_station_with_no_stream_url_is_refused(self, api):
        """There is nothing to play and nothing to store — this is the one the
        caller cannot work around."""
        with patch.object(api, "_request",
                          new=AsyncMock(return_value=[_remote(url_resolved="")])):
            assert await api.fetch_remote_station("s1") is None

    @pytest.mark.asyncio
    async def test_a_station_with_no_name_is_refused(self, api):
        """Milo-Mac decodes `name` non-optionally; one nameless station in the
        favourites payload loses the whole list on the Mac."""
        with patch.object(api, "_request",
                          new=AsyncMock(return_value=[_remote(name="")])):
            assert await api.fetch_remote_station("s1") is None

    @pytest.mark.asyncio
    async def test_a_search_still_drops_what_the_directory_marked_broken(self, api):
        """The other half of the same decision: a list the user did *not* ask
        for by name is where the health flags belong."""
        with patch.object(api, "_request", new=AsyncMock(return_value=[
            _remote(stationuuid="ok", name="Good"),
            _remote(stationuuid="dead", name="Dead", lastcheckok=0),
            _remote(stationuuid="mystery", name="Mystery", codec="UNKNOWN"),
        ])):
            stations = await api._fetch_stations_by_query("x")

        assert [s["name"] for s in stations] == ["Good"]

    @pytest.mark.asyncio
    async def test_an_unknown_id_is_none_not_an_error(self, api):
        with patch.object(api, "_request", new=AsyncMock(return_value=[])):
            assert await api.fetch_remote_station("ghost") is None

    @pytest.mark.asyncio
    async def test_an_unreachable_directory_is_none_not_a_raise(self, api):
        """The caller is a playback path: raising here would surface a directory
        outage as a failed *play* on a favourite whose URL is already known."""
        with patch.object(api, "_request",
                          new=AsyncMock(side_effect=NetworkUnavailableError("all down"))):
            assert await api.fetch_remote_station("s1") is None

    @pytest.mark.asyncio
    async def test_the_answer_is_normalised_before_it_leaves(self, api):
        """Callers store this verbatim (`favorites_cache`), so the directory's
        field names must not leak into the store."""
        with patch.object(api, "_request", new=AsyncMock(return_value=[_remote()])):
            station = await api.fetch_remote_station("s1")

        assert station["id"] == "s1"
        assert station["url"] == "http://stream/fip"
        assert station["countrycode"] == "FR"
        assert "stationuuid" not in station
        assert "url_resolved" not in station


class TestCustomStationsResolveLocally:
    """A hand-added station has no directory entry to find.

    The twin of the T17 finding, on the read side: `custom_*` ids exist only in
    `manual_stations`, so a lookup that skips the local store asks
    radio-browser for a uuid it has never heard of and answers "not found" for a
    station sitting in the user's own list.
    """

    @pytest.mark.asyncio
    async def test_a_custom_station_is_answered_from_the_local_store(self, api):
        manager = Mock()
        manager.get_custom_station_by_id = Mock(
            return_value={"id": "custom_1", "name": "Mine", "url": "http://mine"}
        )
        api.station_manager = manager

        with patch.object(api, "_request", new=AsyncMock()) as request:
            station = await api.get_station_by_id("custom_1")

        assert station["name"] == "Mine"
        request.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_directory_station_is_not_looked_for_locally(self, api):
        manager = Mock()
        api.station_manager = manager

        with patch.object(api, "_request", new=AsyncMock(return_value=[_remote()])):
            assert (await api.get_station_by_id("s1"))["id"] == "s1"

        manager.get_custom_station_by_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_custom_id_the_store_lost_falls_through_rather_than_hanging(
        self, api
    ):
        api.station_manager = Mock()
        api.station_manager.get_custom_station_by_id = Mock(return_value=None)

        with patch.object(api, "_request", new=AsyncMock(return_value=[])) as request:
            assert await api.get_station_by_id("custom_gone") is None

        request.assert_awaited_once()


class TestBatchResolution:
    """`get_stations_by_ids` builds the favourites list."""

    @pytest.mark.asyncio
    async def test_an_empty_request_asks_the_directory_nothing(self, api):
        with patch.object(api, "_request", new=AsyncMock()) as request:
            assert await api.get_stations_by_ids([]) == []
        request.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_custom_and_directory_ids_are_both_returned(self, api):
        api.station_manager = Mock()
        api.station_manager.get_custom_station_by_id = Mock(
            return_value={"id": "custom_1", "name": "Mine",
                          "url": "http://mine", "favicon": "https://x/512x512.png"}
        )

        with patch.object(api, "fetch_remote_station",
                          new=AsyncMock(return_value={
                              "id": "s1", "name": "FIP", "url": "http://stream/fip",
                              "favicon": "https://cdn/fip-512x512.png"})):
            stations = await api.get_stations_by_ids(["custom_1", "s1"])

        assert {s["name"] for s in stations} == {"Mine", "FIP"}

    @pytest.mark.asyncio
    async def test_a_station_the_directory_cannot_resolve_is_skipped_not_fatal(
        self, api
    ):
        """One dead id must not empty the user's whole favourites list."""
        async def _fetch(station_id):
            return None if station_id == "gone" else {
                "id": station_id, "name": "FIP", "url": "http://x",
                "favicon": "https://cdn/fip-512x512.png"}

        with patch.object(api, "fetch_remote_station", new=AsyncMock(side_effect=_fetch)):
            stations = await api.get_stations_by_ids(["gone", "s1"])

        assert [s["id"] for s in stations] == ["s1"]

    @pytest.mark.asyncio
    async def test_a_station_with_a_poor_favicon_is_searched_by_name(self, api):
        """The list is a grid of logos: a favourite with no usable icon falls
        back to a generated monogram, so the by-name search is what puts the
        real logo back."""
        with patch.object(api, "fetch_remote_station", new=AsyncMock(return_value={
            "id": "s1", "name": "FIP", "url": "http://x", "favicon": ""
        })), patch.object(api, "_fetch_stations_by_query",
                          new=AsyncMock(return_value=[])) as by_name:
            await api.get_stations_by_ids(["s1"])

        by_name.assert_awaited_once_with("FIP")

    @pytest.mark.asyncio
    async def test_a_station_with_a_good_favicon_costs_no_extra_lookup(self, api):
        with patch.object(api, "fetch_remote_station", new=AsyncMock(return_value={
            "id": "s1", "name": "FIP", "url": "http://x",
            "favicon": "https://cdn.example.com/fip-512x512.png"
        })), patch.object(api, "_fetch_stations_by_query",
                          new=AsyncMock(return_value=[])) as by_name:
            await api.get_stations_by_ids(["s1"])

        by_name.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_only_same_named_results_are_admitted_as_alternatives(self, api):
        """The by-name search is a broad match. Admitting everything it returns
        puts strangers' stations into the user's favourites list."""
        with patch.object(api, "fetch_remote_station", new=AsyncMock(return_value={
            "id": "s1", "name": "FIP", "url": "http://x", "favicon": ""
        })), patch.object(api, "_fetch_stations_by_query", new=AsyncMock(return_value=[
            {"id": "alt", "name": "fip ", "url": "http://alt",
             "favicon": "https://cdn/fip-512x512.png", "bitrate": 128},
            {"id": "other", "name": "FIP Rock", "url": "http://rock",
             "favicon": "https://cdn/rock-512x512.png", "bitrate": 128},
        ])):
            stations = await api.get_stations_by_ids(["s1"])

        assert "FIP Rock" not in {s["name"] for s in stations}

    @pytest.mark.asyncio
    async def test_the_alternatives_are_merged_away_not_appended(self, api):
        """Deduplication is how the alternative's logo reaches the original
        entry. Without it the list shows the same station twice."""
        with patch.object(api, "fetch_remote_station", new=AsyncMock(return_value={
            "id": "s1", "name": "FIP", "url": "http://x", "favicon": "", "bitrate": 128
        })), patch.object(api, "_fetch_stations_by_query", new=AsyncMock(return_value=[
            {"id": "alt", "name": "FIP", "url": "http://alt",
             "favicon": "https://cdn/fip-512x512.png", "bitrate": 128},
        ])):
            stations = await api.get_stations_by_ids(["s1"])

        assert len(stations) == 1
        assert stations[0]["favicon"] == "https://cdn/fip-512x512.png"


class TestClickCounter:
    """radio-browser ranks on clicks; Milō reports each play."""

    @pytest.mark.asyncio
    async def test_a_play_is_reported_to_the_directory(self, api):
        with patch.object(api, "_request",
                          new=AsyncMock(return_value={"ok": True})) as request:
            assert await api.increment_station_clicks("s1") is True

        request.assert_awaited_once_with("url/s1", timeout=5)

    @pytest.mark.asyncio
    async def test_a_refusal_is_reported_as_such(self, api):
        with patch.object(api, "_request",
                          new=AsyncMock(return_value={"ok": False})):
            assert await api.increment_station_clicks("s1") is False

    @pytest.mark.asyncio
    async def test_an_outage_never_reaches_the_caller(self, api):
        """It is spawned fire-and-forget off the play path — an exception here
        would surface in the background-task log on every play made offline."""
        with patch.object(api, "_request",
                          new=AsyncMock(side_effect=NetworkUnavailableError("down"))):
            assert await api.increment_station_clicks("s1") is False


@pytest.fixture
def images(tmp_path, monkeypatch):
    """An ImageManager writing under `tmp_path`.

    `IMAGES_DIR` is a class attribute pointing at `/var/lib/milo/radio_images`,
    and the save goes through `aiofiles.open(..., 'wb')` — a direct write with
    no rename to catch it.
    """
    monkeypatch.setattr(ImageManager, "IMAGES_DIR", tmp_path / "radio_images")
    return ImageManager()


def _png(size=(200, 200), mode="RGB"):
    buffer = io.BytesIO()
    Image.new(mode, size, "red").save(buffer, format="PNG")
    return buffer.getvalue()


def _png_with_a_hole(size=(200, 200)):
    """An RGBA logo whose corner is genuinely transparent.

    A fully opaque RGBA image is not enough: PIL's WebP writer drops an alpha
    channel that carries no information, so such a fixture would assert PIL's
    optimisation rather than Milō's branch.
    """
    image = Image.new("RGBA", size, (255, 0, 0, 255))
    for x in range(size[0] // 2):
        for y in range(size[1] // 2):
            image.putpixel((x, y), (0, 0, 0, 0))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class TestImageUpload:
    """`validate_and_save_image` accepts a file a user picked on their phone.

    It is the only place in the backend that writes a caller-supplied binary to
    disk, and the filename it answers is what ends up persisted in
    `radio_data.json` — so anything it lets through is permanent.
    """

    @pytest.mark.asyncio
    async def test_a_valid_image_is_stored_as_webp_and_the_name_is_returned(
        self, images
    ):
        ok, filename, error = await images.validate_and_save_image(_png(), "logo.png")

        assert (ok, error) == (True, None)
        assert filename.endswith(".webp")
        saved = images.IMAGES_DIR / filename
        assert saved.exists()
        with Image.open(saved) as stored:
            assert stored.format == "WEBP"

    @pytest.mark.asyncio
    async def test_each_upload_gets_its_own_name(self, images):
        """The name is persisted per station; a deterministic one would make a
        second upload overwrite the first station's logo."""
        _, first, _ = await images.validate_and_save_image(_png(), "a.png")
        _, second, _ = await images.validate_and_save_image(_png(), "b.png")

        assert first != second

    @pytest.mark.asyncio
    async def test_an_oversized_image_is_refused_before_it_is_decoded(self, images):
        """The size gate is what stops a decode of arbitrary attacker-chosen
        bytes; PIL is the library that would otherwise open them."""
        oversized = b"\x00" * (images.MAX_FILE_SIZE_BYTES + 1)

        ok, filename, error = await images.validate_and_save_image(oversized, "big.png")

        assert (ok, filename) == (False, None)
        assert "too large" in error
        assert list(images.IMAGES_DIR.iterdir()) == []

    @pytest.mark.asyncio
    async def test_an_empty_file_is_refused(self, images):
        ok, _, error = await images.validate_and_save_image(b"", "empty.png")
        assert (ok, error) == (False, "Empty file")

    @pytest.mark.asyncio
    async def test_an_extension_outside_the_allow_list_is_refused(self, images):
        """The stored name keeps no trace of the original, but the check is what
        stops the decode — the byte content is never the reason to trust a file."""
        ok, _, error = await images.validate_and_save_image(_png(), "payload.svg")

        assert ok is False
        assert "Unsupported format" in error

    @pytest.mark.asyncio
    async def test_bytes_that_are_not_an_image_are_refused_without_raising(
        self, images
    ):
        ok, _, error = await images.validate_and_save_image(b"not an image", "x.png")

        assert (ok, error) == (False, "Invalid or corrupted file")

    @pytest.mark.asyncio
    async def test_a_favicon_sized_image_is_refused(self, images):
        """Below 50×50 the logo is a smudge at the size the grid renders it."""
        ok, _, error = await images.validate_and_save_image(_png(size=(32, 32)), "s.png")

        assert ok is False
        assert "too small" in error

    @pytest.mark.asyncio
    async def test_a_large_image_is_scaled_down_rather_than_refused(self, images):
        """A phone photo is 4000 px wide. Storing it whole is megabytes per
        station on a Pi's SD card, re-decoded on every list render."""
        ok, filename, _ = await images.validate_and_save_image(
            _png(size=(3000, 2000)), "photo.png"
        )

        assert ok is True
        with Image.open(images.IMAGES_DIR / filename) as stored:
            assert max(stored.size) <= max(images.MAX_DIMENSIONS)
            # Scaled, not cropped.
            assert stored.size[0] / stored.size[1] == pytest.approx(3000 / 2000, abs=0.01)

    @pytest.mark.asyncio
    async def test_transparency_survives_the_conversion(self, images):
        """A logo on a transparent background turns into a black box if the
        alpha channel is dropped by the RGB path."""
        ok, filename, _ = await images.validate_and_save_image(
            _png_with_a_hole(), "logo.png"
        )

        assert ok is True
        with Image.open(images.IMAGES_DIR / filename) as stored:
            assert stored.convert("RGBA").getpixel((10, 10))[3] == 0

    @pytest.mark.asyncio
    async def test_a_disk_that_refuses_the_write_is_reported_not_raised(self, images):
        """The route turns a False into a 400 the user can read; an exception
        escaping here is the red banner instead."""
        images.IMAGES_DIR = images.IMAGES_DIR / "does" / "not" / "exist"

        ok, filename, error = await images.validate_and_save_image(_png(), "logo.png")

        assert (ok, filename) == (False, None)
        assert error.startswith("Error saving file")


class TestImageDeletion:
    """`delete_image` is reached from the route's own rollback."""

    @pytest.mark.asyncio
    async def test_an_uploaded_image_is_removed(self, images):
        _, filename, _ = await images.validate_and_save_image(_png(), "logo.png")

        assert await images.delete_image(filename) is True
        assert not (images.IMAGES_DIR / filename).exists()

    @pytest.mark.asyncio
    async def test_a_name_that_escapes_the_directory_is_refused(self, images, tmp_path):
        """The name reaching here comes from `modified_metadata`, which is
        persisted JSON — the one store a corrupted file could put a path into."""
        outsider = tmp_path / "settings.json"
        outsider.write_text("{}")

        assert await images.delete_image("../settings.json") is False
        assert outsider.exists()

    @pytest.mark.asyncio
    async def test_an_absent_file_is_false_not_an_error(self, images):
        assert await images.delete_image("gone.webp") is False

    @pytest.mark.asyncio
    async def test_an_empty_name_deletes_nothing(self, images):
        assert await images.delete_image("") is False


@pytest.fixture
async def store(tmp_path, monkeypatch):
    """A loaded StationDataService on `tmp_path`, with one modified favourite."""
    monkeypatch.setattr(ImageManager, "IMAGES_DIR", tmp_path / "radio_images")
    service = StationDataService()
    # `_data_file` — not a class constant: the default is
    # /var/lib/milo/radio_data.json and this suite runs ON the appliance.
    service._data_file = tmp_path / "radio_data.json"
    service._broadcast = AsyncMock()
    await service.initialize()
    service._favorites = ["s1"]
    service._modified_metadata = {"s1": {"name": "My name", "url": "http://mine"}}
    return service


class TestRestoringAFavourite:
    """"Restore original metadata" throws away work the user typed.

    The override is the only copy of a rename, a genre or an uploaded logo, and
    `_lookup_local` has nothing else to offer once it is gone. Dropping it before
    knowing whether an original can be produced turned an unreachable directory
    into a silent loss, reported as success.
    """

    @pytest.mark.asyncio
    async def test_a_refetched_original_replaces_the_override(self, store):
        radio_api = Mock()
        radio_api.get_stations_by_ids = AsyncMock(return_value=[
            {"id": "s1", "name": "FIP", "url": "http://stream/fip"}
        ])

        result = await store.restore_favorite_metadata("s1", radio_api=radio_api)

        assert result["success"] is True
        assert "s1" not in store.get_modified_metadata()
        assert store.get_favorite_metadata_local("s1")["name"] == "FIP"

    @pytest.mark.asyncio
    async def test_a_cached_original_is_enough_without_the_directory(self, store):
        """A favourite added through the UI already carries its record."""
        store._favorites_cache = {"s1": {"name": "FIP", "url": "http://stream/fip"}}
        radio_api = Mock()
        radio_api.get_stations_by_ids = AsyncMock(return_value=[])

        result = await store.restore_favorite_metadata("s1", radio_api=radio_api)

        assert result["success"] is True
        assert store.get_favorite_metadata_local("s1")["name"] == "FIP"

    @pytest.mark.asyncio
    async def test_nothing_to_restore_keeps_the_users_edit(self, store):
        radio_api = Mock()
        radio_api.get_stations_by_ids = AsyncMock(return_value=[])

        result = await store.restore_favorite_metadata("s1", radio_api=radio_api)

        assert result["success"] is False
        assert result["error"] == "No original metadata to restore"
        assert store.get_modified_metadata()["s1"]["name"] == "My name"

    @pytest.mark.asyncio
    async def test_a_refused_restore_keeps_the_uploaded_logo_on_disk(self, store):
        """The image is deleted as part of the restore. Deleting it and then
        refusing leaves the override pointing at a file that is gone."""
        _, filename, _ = await store.image_manager.validate_and_save_image(
            _png(), "logo.png"
        )
        store._modified_metadata["s1"]["image_filename"] = filename
        radio_api = Mock()
        radio_api.get_stations_by_ids = AsyncMock(return_value=[])

        await store.restore_favorite_metadata("s1", radio_api=radio_api)

        assert (store.image_manager.IMAGES_DIR / filename).exists()

    @pytest.mark.asyncio
    async def test_a_successful_restore_removes_the_uploaded_logo(self, store):
        _, filename, _ = await store.image_manager.validate_and_save_image(
            _png(), "logo.png"
        )
        store._modified_metadata["s1"]["image_filename"] = filename
        radio_api = Mock()
        radio_api.get_stations_by_ids = AsyncMock(return_value=[
            {"id": "s1", "name": "FIP", "url": "http://stream/fip"}
        ])

        await store.restore_favorite_metadata("s1", radio_api=radio_api)

        assert not (store.image_manager.IMAGES_DIR / filename).exists()

    @pytest.mark.asyncio
    async def test_a_directory_outage_never_deletes_anything(self, store):
        radio_api = Mock()
        radio_api.get_stations_by_ids = AsyncMock(
            side_effect=NetworkUnavailableError("all mirrors down")
        )

        result = await store.restore_favorite_metadata("s1", radio_api=radio_api)

        assert result["success"] is False
        assert store.get_modified_metadata()["s1"]["name"] == "My name"

    @pytest.mark.asyncio
    async def test_a_station_that_was_never_edited_is_refused(self, store):
        result = await store.restore_favorite_metadata("never-touched")

        assert result["success"] is False
        assert result["error"] == "Station has no modified metadata"

    @pytest.mark.asyncio
    async def test_the_restored_station_is_announced(self, store):
        """The stores hold stations by value: without the event the favourites
        list keeps serving the override, its deleted image included."""
        radio_api = Mock()
        radio_api.get_stations_by_ids = AsyncMock(return_value=[
            {"id": "s1", "name": "FIP", "url": "http://stream/fip"}
        ])

        await store.restore_favorite_metadata("s1", radio_api=radio_api)

        announced = [c.args[0] for c in store._broadcast.await_args_list]
        assert any(getattr(e, "station", {}).get("name") == "FIP" for e in announced)

    @pytest.mark.asyncio
    async def test_the_refetched_record_is_stored_without_its_own_id(self, store):
        """`favorites_cache` is keyed by id; a second `id` inside the value is
        what `_lookup_local` then stamps over."""
        radio_api = Mock()
        radio_api.get_stations_by_ids = AsyncMock(return_value=[
            {"id": "s1", "name": "FIP", "url": "http://stream/fip"}
        ])

        await store.restore_favorite_metadata("s1", radio_api=radio_api)

        assert "id" not in store._favorites_cache["s1"]


class TestFavouriteMetadataLookup:
    """`get_station_metadata` — local first, directory second, cached after."""

    @pytest.mark.asyncio
    async def test_a_local_record_is_answered_without_the_directory(self, store):
        radio_api = Mock()
        radio_api.fetch_remote_station = AsyncMock()
        store.radio_api = radio_api

        assert (await store.get_station_metadata("s1"))["name"] == "My name"
        radio_api.fetch_remote_station.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_unknown_station_is_fetched_and_then_cached(self, store):
        """Without the cache write, every favourites listing re-resolves the
        same station against a federated service that rate-limits."""
        radio_api = Mock()
        radio_api.fetch_remote_station = AsyncMock(
            return_value={"id": "s9", "name": "TSF", "url": "http://tsf"}
        )
        store.radio_api = radio_api

        assert (await store.get_station_metadata("s9"))["name"] == "TSF"
        assert store._favorites_cache["s9"]["name"] == "TSF"

        radio_api.fetch_remote_station.reset_mock()
        assert (await store.get_station_metadata("s9"))["name"] == "TSF"
        radio_api.fetch_remote_station.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_station_nobody_can_resolve_is_none(self, store):
        radio_api = Mock()
        radio_api.fetch_remote_station = AsyncMock(return_value=None)
        store.radio_api = radio_api

        assert await store.get_station_metadata("ghost") is None
        assert "ghost" not in store._favorites_cache
