# backend/tests/test_radio_browser_api.py
"""
Unit tests for RadioBrowserAPI (sources/radio/browser_api.py).

Tests cover the parts whose contract the frontend depends on:
- get_available_countries: hidebroken filter, >=20 threshold, iso_3166_1 exposure,
  drop entries missing name/ISO, no stationcount sort, cache reuse, stale-cache
  fallback on network failure.
- _normalize_station: countrycode field exposed and upper-cased.
"""
from unittest.mock import AsyncMock, patch

import pytest

from backend.shared.network import NetworkUnavailableError
from backend.sources.radio.browser_api import RadioBrowserAPI
from backend.sources.radio.genres import extract_valid_genre


SAMPLE_COUNTRIES = [
    {"name": "France", "iso_3166_1": "FR", "stationcount": 2477},
    {"name": "Nepal", "iso_3166_1": "NP", "stationcount": 29},
    {"name": "Andorra", "iso_3166_1": "AD", "stationcount": 8},  # below threshold
    {"name": "Antarctica", "iso_3166_1": "AQ", "stationcount": 10},  # below threshold
    {"name": "", "iso_3166_1": "ZZ", "stationcount": 100},  # missing name
    {"name": "Nowhere", "iso_3166_1": "", "stationcount": 100},  # missing ISO
    {"name": "Germany", "iso_3166_1": "de", "stationcount": 5792},  # lowercase ISO
]


@pytest.fixture
def api():
    return RadioBrowserAPI()


class TestGetAvailableCountries:
    @pytest.mark.asyncio
    async def test_passes_hidebroken_param(self, api):
        """Must request hidebroken=true to match radio-browser.info counts."""
        with patch.object(api, "_request", new=AsyncMock(return_value=[])) as mock:
            await api.get_available_countries()
            mock.assert_awaited_once_with(
                "countries", params={"hidebroken": "true"}, timeout=10
            )

    @pytest.mark.asyncio
    async def test_filters_threshold_and_missing_fields(self, api):
        """Drop <20 stations, missing name, or missing ISO."""
        with patch.object(api, "_request", new=AsyncMock(return_value=SAMPLE_COUNTRIES)):
            result = await api.get_available_countries()

        names = {c["name"] for c in result}
        assert names == {"France", "Nepal", "Germany"}

    @pytest.mark.asyncio
    async def test_iso_is_uppercased(self, api):
        """ISO 3166-1 alpha-2 codes are normalized to uppercase for Intl.DisplayNames."""
        with patch.object(api, "_request", new=AsyncMock(return_value=SAMPLE_COUNTRIES)):
            result = await api.get_available_countries()

        germany = next(c for c in result if c["name"] == "Germany")
        assert germany["iso_3166_1"] == "DE"

    @pytest.mark.asyncio
    async def test_shape_matches_frontend_contract(self, api):
        """Each entry must expose name, iso_3166_1, stationcount."""
        with patch.object(api, "_request", new=AsyncMock(return_value=SAMPLE_COUNTRIES)):
            result = await api.get_available_countries()

        for c in result:
            assert set(c.keys()) == {"name", "iso_3166_1", "stationcount"}

    @pytest.mark.asyncio
    async def test_no_stationcount_sort(self, api):
        """Order is left to the frontend (locale-aware alphabetical sort)."""
        # Pass entries in non-sorted order; result preserves filtered API order.
        with patch.object(api, "_request", new=AsyncMock(return_value=SAMPLE_COUNTRIES)):
            result = await api.get_available_countries()

        # France (2477) appears before Nepal (29) because that's the API order.
        # A stationcount-desc sort would have placed Germany (5792) first.
        assert [c["name"] for c in result] == ["France", "Nepal", "Germany"]

    @pytest.mark.asyncio
    async def test_cache_hit_skips_request(self, api):
        """Within TTL, a second call must not hit the network."""
        with patch.object(api, "_request", new=AsyncMock(return_value=SAMPLE_COUNTRIES)) as mock:
            await api.get_available_countries()
            await api.get_available_countries()
            assert mock.await_count == 1

    @pytest.mark.asyncio
    async def test_network_failure_returns_stale_cache(self, api):
        """When all mirrors fail, fall back to the cached list."""
        from datetime import datetime, timedelta

        with patch.object(api, "_request", new=AsyncMock(return_value=SAMPLE_COUNTRIES)):
            cached = await api.get_available_countries()

        # Expire the freshness window to force a refetch on the next call.
        api._countries_cache_timestamp = datetime.now() - timedelta(days=2)
        with patch.object(
            api,
            "_request",
            new=AsyncMock(side_effect=NetworkUnavailableError("all mirrors down")),
        ):
            result = await api.get_available_countries()

        assert result == cached

    @pytest.mark.asyncio
    async def test_network_failure_without_cache_returns_empty(self, api):
        """No cache + all mirrors down → empty list, never raise."""
        with patch.object(
            api,
            "_request",
            new=AsyncMock(side_effect=NetworkUnavailableError("all mirrors down")),
        ):
            result = await api.get_available_countries()

        assert result == []


class TestNormalizeStation:
    def test_exposes_countrycode_uppercase(self, api):
        raw = {
            "stationuuid": "abc",
            "name": "Test",
            "url_resolved": "http://x",
            "country": "France",
            "countrycode": "fr",
            "tags": "rock",
            "bitrate": 128,
            "codec": "MP3",
            "votes": 10,
            "clickcount": 5,
        }
        result = api._normalize_station(raw)
        assert result["countrycode"] == "FR"

    def test_missing_countrycode_yields_empty_string(self, api):
        raw = {
            "stationuuid": "abc",
            "name": "Test",
            "url_resolved": "http://x",
            "country": "Unknown",
            "tags": "",
        }
        result = api._normalize_station(raw)
        assert result["countrycode"] == ""


class TestExtractValidGenre:
    def test_urban_is_skipped(self):
        """'urban' was removed from VALID_GENRES — must skip past it."""
        assert extract_valid_genre("urban,rock,pop") == "rock"

    def test_hiphop_form_is_skipped(self):
        """Bare 'hiphop' was removed — only the hyphenated 'hip-hop' is valid."""
        assert extract_valid_genre("hiphop,jazz") == "jazz"
        assert extract_valid_genre("hip-hop,jazz") == "hip-hop"

    def test_rnb_form_is_skipped(self):
        """Bare 'rnb' was removed — only 'r&b' is valid."""
        assert extract_valid_genre("rnb,soul") == "soul"
        assert extract_valid_genre("r&b,soul") == "r&b"

    def test_world_music_is_skipped(self):
        """'world music' was removed (zero stations have this exact tag)."""
        assert extract_valid_genre("world music,reggae") == "reggae"
