# backend/tests/test_radio_data.py
"""StationDataService — the two-store lifecycle of an edited station.

A custom station lives in `manual_stations` from creation, a favourite's
original in `favorites_cache`, and every later save writes an override into
`modified_metadata`. Anything that treats one store as the whole station leaves
the other behind — a stale record, an orphaned upload, or a dropped image.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.sources.radio.data import ImageManager, StationDataService


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

    @pytest.mark.asyncio
    async def test_deleting_a_station_that_was_favourited_drops_the_favourite(self, data):
        # The stores are emptied here, so a favourite id left behind resolves to
        # nothing: a station the favourites list carries forever and no screen
        # can show or remove.
        created = await data.add_custom_station(name="Solo", url="http://example.invalid/x")
        station_id = created["station"]["id"]
        await data.add_favorite(station_id)

        await data.remove_custom_station(station_id)

        assert data.is_favorite(station_id) is False
        assert await data.get_favorites_with_metadata() == []


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


class TestEnrichWithFavoriteStatus:
    """Every station list the UI draws passes through this overlay.

    The favourite flag was the only half the suite watched; the overlay itself —
    the user's edits applied over the API record, and the live popularity stats
    kept whenever the edit left them blank — ran in no test at all. Without the
    preserved stats an edited station shows 0 votes and 0 clicks in the search
    list, because every override carries those three keys at zero.

    Consumer: `RadioBrowserAPI.search_stations` → `radioStore` → RadioSearch.vue.
    """

    @staticmethod
    def _api_result(station_id):
        return {"id": station_id, "name": "API name", "genre": "Pop",
                "score": 9, "votes": 100, "clickcount": 50}

    async def test_an_edit_is_overlaid_on_the_api_record(self, data):
        await data.modify_favorite_metadata(
            "api-1", name="Renamed", url="http://example.invalid/s", genre="Jazz",
        )

        enriched = data.enrich_with_favorite_status([self._api_result("api-1")])[0]

        assert enriched["name"] == "Renamed"
        assert enriched["genre"] == "Jazz"

    async def test_live_popularity_survives_an_edit_that_left_it_blank(self, data):
        # An override written with no cached original carries score/votes/
        # clickcount at zero — overlaying it flat is what blanked the stats.
        await data.modify_favorite_metadata(
            "api-1", name="Renamed", url="http://example.invalid/s",
        )

        enriched = data.enrich_with_favorite_status([self._api_result("api-1")])[0]

        assert enriched["name"] == "Renamed", "the override was not applied at all"
        assert (enriched["score"], enriched["votes"], enriched["clickcount"]) == (9, 100, 50)

    async def test_stats_the_edit_does_carry_win_over_the_api(self, data):
        data._favorites_cache["api-1"] = {"votes": 42}
        await data.modify_favorite_metadata(
            "api-1", name="Renamed", url="http://example.invalid/s",
        )

        enriched = data.enrich_with_favorite_status([self._api_result("api-1")])[0]

        assert enriched["votes"] == 42

    async def test_a_custom_station_is_overlaid_from_its_creation_record(self, data):
        created = await data.add_custom_station(
            name="Created", url="http://example.invalid/s", genre="Jazz",
        )
        station_id = created["station"]["id"]

        enriched = data.enrich_with_favorite_status(
            [{"id": station_id, "name": "stale", "genre": "Pop"}]
        )[0]

        assert enriched["name"] == "Created"
        assert enriched["is_custom"] is True
        assert enriched["id"] == station_id


class TestFavorites:
    """Adding and removing a favourite — the durable user data of this source.

    Both write `/var/lib/milo/radio_data.json` and both announce themselves over
    WS; nothing in the suite entered either. The two promises that are not
    obvious from the call site: the cached original is stored without its `id`
    (the id is the key, and a stamped copy is what `_lookup_local` would serve
    back as metadata), and removing a favourite keeps the override and the cached
    original so re-adding restores the user's edits.

    Consumers: `radioStore` via WS `radio/favorite_added` + `favorite_removed`.
    """

    async def test_adding_caches_the_original_without_its_id(self, data):
        added = await data.add_favorite("api-1", {"id": "api-1", "name": "Origin"})

        assert added is True
        assert data.is_favorite("api-1") is True
        assert data._favorites_cache["api-1"] == {"name": "Origin"}
        event = data._state_machine.broadcast.await_args.args[0]
        assert (event.TYPE, event.station_id) == ("favorite_added", "api-1")

    async def test_adding_a_second_time_announces_nothing(self, data):
        await data.add_favorite("api-1", {"id": "api-1", "name": "Origin"})
        data._state_machine.broadcast.reset_mock()

        assert await data.add_favorite("api-1", {"id": "api-1", "name": "Origin"}) is True

        data._state_machine.broadcast.assert_not_awaited()
        assert data._favorites == ["api-1"]

    async def test_an_edited_station_keeps_its_override_as_the_original(self, data):
        await data.modify_favorite_metadata(
            "api-1", name="Renamed", url="http://example.invalid/s",
        )

        await data.add_favorite("api-1", {"id": "api-1", "name": "API name"})

        assert "api-1" not in data._favorites_cache, "the API record overwrote the edit"

    async def test_removing_keeps_what_a_re_add_restores(self, data):
        await data.add_favorite("api-1", {"id": "api-1", "name": "Origin"})
        await data.modify_favorite_metadata(
            "api-1", name="Renamed", url="http://example.invalid/s",
        )
        data._state_machine.broadcast.reset_mock()

        assert await data.remove_favorite("api-1") is True

        assert data.is_favorite("api-1") is False
        assert data.get_favorite_metadata_local("api-1")["name"] == "Renamed"
        assert data._favorites_cache["api-1"]["name"] == "Origin"
        event = data._state_machine.broadcast.await_args.args[0]
        assert (event.TYPE, event.station_id) == ("favorite_removed", "api-1")

    async def test_removing_a_station_that_is_not_a_favourite_announces_nothing(self, data):
        assert await data.remove_favorite("api-1") is True

        data._state_machine.broadcast.assert_not_awaited()

    async def test_the_favourites_list_stamps_the_flag_and_drops_what_it_cannot_resolve(self, data):
        await data.add_favorite("api-1", {"id": "api-1", "name": "Origin"})
        data._favorites.append("api-ghost")

        favorites = await data.get_favorites_with_metadata()

        assert [s["name"] for s in favorites] == ["Origin"]
        assert favorites[0]["is_favorite"] is True
        assert favorites[0]["id"] == "api-1"


class TestShazamOptOut:
    """The per-station Shazam switch of Réglages → ManageStation.

    Read on every play and on every global-toggle flip; a station opted out must
    show no recognised track at all. Default is ON, so a lookup that silently
    stopped finding the stored preference would re-enable recognition on every
    station that turned it off, with nothing in any log to say so.

    Consumer: `RadioSource._handle_play_station` → `_recognition_enabled`.
    """

    @pytest.mark.parametrize("store,stored,expected", [
        ("_modified_metadata", {"shazam_enabled": False}, False),
        ("_manual_stations", {"shazam_enabled": False}, False),
        ("_modified_metadata", {"shazam_enabled": True}, True),
        ("_modified_metadata", {"name": "No preference stored"}, True),
    ])
    def test_the_stored_preference_decides(self, data, store, stored, expected):
        getattr(data, store)["s-1"] = stored

        assert data.is_station_shazam_enabled("s-1") is expected

    def test_an_unknown_station_recognises_by_default(self, data):
        assert data.is_station_shazam_enabled("s-1") is True
        assert data.is_station_shazam_enabled("") is True

    def test_an_edit_overrides_the_creation_record(self, data):
        data._manual_stations["custom_1"] = {"shazam_enabled": True}
        data._modified_metadata["custom_1"] = {"shazam_enabled": False}

        assert data.is_station_shazam_enabled("custom_1") is False


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


class TestImageStore:
    """`GET /api/radio/images/{filename}` is the one radio route that turns a
    free string from the LAN into a filesystem path.

    Its whole defence is the `is_relative_to` check inside the store, and no test
    entered it. `delete_image` takes the same treatment because the name it is
    given comes from a stored record, which a bad save can put anything into.
    """

    @pytest.fixture
    def images(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ImageManager, "IMAGES_DIR", tmp_path / "radio_images")
        return ImageManager()

    def test_a_stored_image_resolves(self, images):
        (images.IMAGES_DIR / "cover.webp").write_bytes(b"webp")

        assert images.get_image_path("cover.webp") == images.IMAGES_DIR / "cover.webp"

    def test_a_name_with_no_file_behind_it_resolves_to_nothing(self, images):
        assert images.get_image_path("absent.webp") is None
        assert images.get_image_path("") is None

    @pytest.mark.parametrize("escape", ["../outside.webp", "sub/../../outside.webp",
                                        "/etc/passwd"])
    def test_a_name_that_leaves_the_store_is_refused(self, images, tmp_path, escape):
        (tmp_path / "outside.webp").write_bytes(b"webp")
        (images.IMAGES_DIR / "sub").mkdir()
        # Each escape must reach a file that exists, or an unguarded store would
        # answer None anyway and the case would pass without the guard.
        assert (images.IMAGES_DIR / escape).exists()

        assert images.get_image_path(escape) is None

    async def test_delete_refuses_a_name_that_leaves_the_store(self, images, tmp_path):
        victim = tmp_path / "outside.webp"
        victim.write_bytes(b"webp")

        assert await images.delete_image("../outside.webp") is False
        assert victim.exists(), "a stored name reached a file outside the image store"

    async def test_delete_removes_a_stored_image(self, images):
        stored = images.IMAGES_DIR / "cover.webp"
        stored.write_bytes(b"webp")

        assert await images.delete_image("cover.webp") is True
        assert not stored.exists()
        assert await images.delete_image("cover.webp") is False
