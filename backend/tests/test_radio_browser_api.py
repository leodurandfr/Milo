# backend/tests/test_radio_browser_api.py
"""
Unit tests for RadioBrowserAPI (sources/radio/browser_api.py).

Tests cover the parts whose contract the frontend depends on:
- get_available_countries: hidebroken filter, >=20 threshold, iso_3166_1 exposure,
  drop entries missing name/ISO, no stationcount sort, cache reuse, stale-cache
  fallback on network failure.
- _normalize_station: countrycode field exposed and upper-cased.
"""
import asyncio

import aiohttp
from unittest.mock import AsyncMock, Mock, patch

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


class TestNormalizeStationSelectionSignals:
    """WI-3: `hls` and `ssl_error` propagate for quality-first stream selection."""

    def test_propagates_hls_and_ssl_error(self, api):
        raw = {
            "stationuuid": "abc",
            "name": "Test",
            "url_resolved": "http://x",
            "tags": "",
            "hls": 1,
            "ssl_error": 1,
        }
        result = api._normalize_station(raw)
        assert result["hls"] == 1
        assert result["ssl_error"] == 1

    def test_defaults_when_absent(self, api):
        raw = {"stationuuid": "abc", "name": "Test", "url_resolved": "http://x", "tags": ""}
        result = api._normalize_station(raw)
        assert result["hls"] == 0
        assert result["ssl_error"] == 0


class TestRankingKey:
    """WI-3: quality first, then metadata-likelihood, reliability, popularity."""

    def test_codec_normalizes_bitrate(self, api):
        """AAC 96k outranks MP3 128k (96*1.5=144 > 128)."""
        aac = {"codec": "AAC", "bitrate": 96}
        mp3 = {"codec": "MP3", "bitrate": 128}
        assert api._ranking_key(aac) > api._ranking_key(mp3)

    def test_quality_dominates_popularity(self, api):
        """A higher-bitrate variant wins even with a lower score."""
        hi = {"codec": "MP3", "bitrate": 256, "score": 0}
        lo = {"codec": "MP3", "bitrate": 128, "score": 9999}
        assert api._ranking_key(hi) > api._ranking_key(lo)

    def test_non_hls_preferred_at_equal_quality(self, api):
        """Metadata-likelihood tie-breaks: Icecast (hls=0) over HLS."""
        icecast = {"codec": "MP3", "bitrate": 128, "hls": 0}
        hls = {"codec": "MP3", "bitrate": 128, "hls": 1}
        assert api._ranking_key(icecast) > api._ranking_key(hls)

    def test_ssl_error_penalized_at_equal_quality(self, api):
        clean = {"codec": "MP3", "bitrate": 128, "ssl_error": 0}
        broken = {"codec": "MP3", "bitrate": 128, "ssl_error": 1}
        assert api._ranking_key(clean) > api._ranking_key(broken)


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


def _raw(name, **over):
    """A raw Radio Browser payload that passes _is_valid_station."""
    station = {
        "stationuuid": f"uuid-{name}",
        "name": name,
        "url_resolved": f"http://stream/{name}",
        "codec": "MP3",
        "lastcheckok": 1,
        "country": "France",
        "countrycode": "FR",
        "tags": "rock",
        "bitrate": 128,
        "votes": 1,
        "clickcount": 1,
    }
    station.update(over)
    return station


class TestSearchKeepsHandAddedStations:
    """A station the user typed in by hand must survive the result cap.

    `search_stations` merges the catalogue with the manually-added stations and
    then cuts the list at `limit`. Measured against the live Radio Browser on
    2026-08-24, the catalogue alone fills that cap on every broad request: the
    no-filter view returns 451 deduplicated stations, `rock` 541, `jazz` 500,
    `fm` 554 and `country=France` 515, against the 300 that
    `GET /api/radio/stations` defaults to and that the frontend never overrides.
    Merged in at the end, the hand-added station was therefore cut every time —
    the one entry no search term can bring back.
    """

    @staticmethod
    def _manager(*names):
        manager = Mock()
        manager.get_manual_stations.return_value = {
            f"custom_{n}": {"name": n, "genre": "rock", "country": "France", "url": f"http://{n}"}
            for n in names
        }
        return manager

    @pytest.mark.asyncio
    async def test_survives_a_catalogue_that_fills_the_cap(self):
        api = RadioBrowserAPI(station_manager=self._manager("Ma Radio"))
        catalogue = [_raw(f"Station {i}") for i in range(300)]

        with patch.object(api, "_request", new=AsyncMock(return_value=catalogue)):
            result = await api.search_stations(query="rock", limit=300)

        assert len(result["stations"]) == 300, "the cap must still be honoured"
        names = [s["name"] for s in result["stations"]]
        assert "Ma Radio" in names

    @pytest.mark.asyncio
    async def test_survives_when_the_catalogue_is_the_top_stations_view(self):
        """No query, no country, no genre — the screen the user lands on."""
        api = RadioBrowserAPI(station_manager=self._manager("Ma Radio"))
        catalogue = [_raw(f"Station {i}") for i in range(451)]

        with patch.object(api, "_request", new=AsyncMock(return_value=catalogue)):
            result = await api.search_stations(limit=300)

        assert len(result["stations"]) == 300
        assert "Ma Radio" in [s["name"] for s in result["stations"]]

    @pytest.mark.asyncio
    async def test_appears_once_when_there_is_room_to_spare(self):
        api = RadioBrowserAPI(station_manager=self._manager("Ma Radio"))

        with patch.object(api, "_request", new=AsyncMock(return_value=[_raw("Station 0")])):
            result = await api.search_stations(query="rock", limit=300)

        names = [s["name"] for s in result["stations"]]
        assert names.count("Ma Radio") == 1
        assert "Station 0" in names

    @pytest.mark.asyncio
    async def test_a_non_matching_hand_added_station_is_left_out(self):
        """The merge filters; it does not smuggle every custom station in."""
        api = RadioBrowserAPI(station_manager=self._manager("Ma Radio"))

        with patch.object(api, "_request", new=AsyncMock(return_value=[_raw("Station 0")])):
            result = await api.search_stations(query="nothing-matches-this", limit=300)

        assert "Ma Radio" not in [s["name"] for s in result["stations"]]

    @pytest.mark.asyncio
    async def test_total_counts_what_was_merged_not_what_was_returned(self):
        api = RadioBrowserAPI(station_manager=self._manager("Ma Radio"))
        catalogue = [_raw(f"Station {i}") for i in range(300)]

        with patch.object(api, "_request", new=AsyncMock(return_value=catalogue)):
            result = await api.search_stations(query="rock", limit=300)

        assert result["total"] == 301
        assert len(result["stations"]) == 300


class TestFaviconQualityGate:
    """The score decides what artwork a station card shows, and what it costs.

    Two thresholds read it and neither is cosmetic. `_normalize_station` blanks
    any favicon under 10, so a rejected URL is a station that falls back to the
    generated monogram. `get_stations_by_ids` treats anything under 20 as poor
    and pays a *second* Radio Browser round trip per station to look for a better
    one. A drift that lowers a whole family of URLs therefore either strips the
    catalogue of its logos or multiplies the calls the favourites list makes.
    """

    @staticmethod
    def _with_favicon(api, url):
        return api._normalize_station(_raw("Test", favicon=url))["favicon"]

    @pytest.mark.parametrize("url", [
        "https://facebook.com/pages/x/logo.png",
        "https://scontent.fbcdn.net/logo.png",
        "https://dropbox.com/s/x/logo.png",
        "https://cdn.example.com/logo.png?token=abcd",
        "https://cdn.example.com/logo.png?signature=abcd",
        "https://en.wikipedia.org/wiki/Radio_France",
        "https://cdn.example.com/cropped-favicon.png",
    ])
    def test_rejected_urls_are_blanked_by_normalize(self, api, url):
        """Each of these scores under the 10 that _normalize_station requires."""
        assert self._with_favicon(api, url) == ""

    @pytest.mark.parametrize("url", [
        "https://cdn.example.com/favicon.ico",       # exactly at the threshold
        "https://cdn.example.com/logo.png",
        "https://cdn.example.com/logo.webp",
        "https://cdn.example.com/logo.jpg",
        "https://upload.wikimedia.org/x/logo.png",
    ])
    def test_accepted_urls_survive_normalize(self, api, url):
        assert self._with_favicon(api, url) == url

    def test_a_vector_keeps_its_bonus_even_named_favicon(self, api):
        """The one format the "favicon" penalty does not apply to.

        A raster called `cropped-favicon.png` is a thumbnail and is rejected; the
        same name in SVG is scalable, so it is kept on purpose. The two must not
        drift into agreeing — that is the difference between a crisp logo and a
        monogram on the card.
        """
        svg = "https://cdn.example.com/cropped-favicon.svg"
        png = "https://cdn.example.com/cropped-favicon.png"
        assert self._with_favicon(api, svg) == svg
        assert self._with_favicon(api, png) == ""

    def test_resolution_read_from_the_url_outranks_a_plain_image(self, api):
        plain = api._get_favicon_quality("https://cdn.example.com/logo.png")
        sized = api._get_favicon_quality("https://cdn.example.com/logo-512x512.png")
        assert sized > plain

    def test_the_last_resolution_in_the_url_is_the_one_that_counts(self, api):
        """`image-400x400-resized-180x180.png` is 180 wide, not 400."""
        resized = api._get_favicon_quality("https://cdn.example.com/a-400x400-resized-180x180.png")
        small = api._get_favicon_quality("https://cdn.example.com/a-180x180.png")
        large = api._get_favicon_quality("https://cdn.example.com/a-400x400.png")
        assert resized == small
        assert resized < large

    def test_a_rectangle_scores_on_its_smaller_side(self, api):
        wide = api._get_favicon_quality("https://cdn.example.com/a-1200x200.png")
        square = api._get_favicon_quality("https://cdn.example.com/a-200x200.png")
        assert wide == square

    def test_nothing_at_all_ranks_below_the_worst_url(self, api):
        """_deduplicate_stations seeds its search with -1, so "" must lose."""
        assert api._get_favicon_quality("") < api._get_favicon_quality(
            "https://facebook.com/logo.png"
        )


class TestDeduplicateMergesBestAudioWithBestImage:
    """One station published twice must come back once, taking the best of each.

    This is the whole point of the pass: Radio Browser carries the same station
    under several entries, and the good stream and the good logo are rarely the
    same entry. Merging the wrong way round is invisible in a list — it shows up
    as a station that plays at 64 kbps, or one with no artwork while a sibling
    entry had one.
    """

    @pytest.mark.asyncio
    async def test_takes_the_url_of_the_best_stream_and_the_image_of_another(self, api):
        loud = api._normalize_station(_raw(
            "FIP", url_resolved="http://hi", bitrate=320, favicon=""))
        pretty = api._normalize_station(_raw(
            "FIP", url_resolved="http://lo", bitrate=64,
            favicon="https://cdn.example.com/fip-512x512.png"))

        merged = await api._deduplicate_stations([loud, pretty])

        assert len(merged) == 1
        assert merged[0]["url"] == "http://hi"
        assert merged[0]["favicon"] == "https://cdn.example.com/fip-512x512.png"

    @pytest.mark.asyncio
    async def test_groups_ignore_case_and_surrounding_space(self, api):
        versions = [
            api._normalize_station(_raw("FIP")),
            api._normalize_station(_raw("  fip  ")),
        ]
        assert len(await api._deduplicate_stations(versions)) == 1

    @pytest.mark.asyncio
    async def test_distinct_stations_are_all_kept(self, api):
        versions = [api._normalize_station(_raw(n)) for n in ("FIP", "TSF", "Nova")]
        merged = await api._deduplicate_stations(versions)
        assert sorted(s["name"] for s in merged) == ["FIP", "Nova", "TSF"]

    @pytest.mark.asyncio
    async def test_a_lone_station_is_returned_untouched(self, api):
        only = api._normalize_station(_raw("FIP", favicon=""))
        assert await api._deduplicate_stations([only]) == [only]

    @pytest.mark.asyncio
    async def test_an_empty_list_stays_empty(self, api):
        assert await api._deduplicate_stations([]) == []


class _Resp:
    """One scripted aiohttp response."""

    def __init__(self, status, payload=None):
        self.status = status
        self._payload = payload

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Boom:
    """A mirror that fails the way aiohttp fails: on entering the context."""

    def __init__(self, exc):
        self._exc = exc

    async def __aenter__(self):
        raise self._exc

    async def __aexit__(self, *exc):
        return False


class _Session:
    """Stands in for aiohttp.ClientSession — the outside world, one outcome per call."""

    def __init__(self, *outcomes):
        self._outcomes = list(outcomes)
        self.closed = False
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        return self._outcomes.pop(0)


def _wired(api, session, count=3):
    """Give the client a scripted session and a mirror pool of `count`.

    Faithful to ServerDiscovery on the one point _request depends on: get_server()
    keeps answering the same mirror, and only rotate() moves the cursor. _request
    calls get_server() once before the loop to prime DNS resolution, and a pool
    that advanced on every read would make that priming call skip a mirror.
    """
    api.session = session
    servers = [f"mirror{i}.example" for i in range(max(count, 1))]
    cursor = {"i": 0}

    async def _current():
        return servers[cursor["i"] % len(servers)]

    async def _next():
        cursor["i"] += 1
        return await _current()

    api._discovery = Mock()
    api._discovery.get_server = AsyncMock(side_effect=_current)
    api._discovery.rotate = AsyncMock(side_effect=_next)
    api._discovery.base_url = lambda server: f"https://{server}/json"
    api._discovery.server_count = count
    return api._discovery


class TestMirrorRotation:
    """What happens when a Radio Browser mirror misbehaves.

    Radio Browser is a federated community service: mirrors go down, time out and
    return 500s routinely, and the whole catalogue — search, countries, the
    favourites refetch — reaches the network through this one method. Its three
    outcomes are not interchangeable. Rotating recovers; returning None is a
    logical "not found" the UI renders as an empty list; raising is what makes
    the UI say the search is unavailable and keep the stale country cache. Get
    one wrong and either a transient blip looks like an empty catalogue, or a
    genuinely absent station looks like an outage.
    """

    @pytest.mark.asyncio
    async def test_a_healthy_mirror_answers_without_rotating(self, api):
        session = _Session(_Resp(200, [{"name": "FIP"}]))
        discovery = _wired(api, session)

        assert await api._request("countries") == [{"name": "FIP"}]
        discovery.rotate.assert_not_awaited()
        assert session.urls == ["https://mirror0.example/json/countries"]

    @pytest.mark.asyncio
    async def test_a_500_rotates_to_the_next_mirror_and_the_answer_stands(self, api):
        session = _Session(_Resp(503), _Resp(200, ["ok"]))
        discovery = _wired(api, session)

        assert await api._request("countries") == ["ok"]
        discovery.rotate.assert_awaited_once()
        assert session.urls == [
            "https://mirror0.example/json/countries",
            "https://mirror1.example/json/countries",
        ]

    @pytest.mark.asyncio
    async def test_a_timeout_rotates_the_same_way_a_500_does(self, api):
        session = _Session(_Boom(asyncio.TimeoutError()), _Resp(200, ["ok"]))
        discovery = _wired(api, session)

        assert await api._request("countries") == ["ok"]
        discovery.rotate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_404_is_a_verdict_and_stops_the_rotation(self, api):
        """Every mirror shares one database, so asking another cannot help."""
        session = _Session(_Resp(404), _Resp(200, ["never reached"]))
        discovery = _wired(api, session)

        assert await api._request("stations/byuuid/nope") is None
        discovery.rotate.assert_not_awaited()
        assert len(session.urls) == 1

    @pytest.mark.asyncio
    async def test_every_mirror_transient_raises_rather_than_answering_empty(self, api):
        session = _Session(*[_Boom(aiohttp.ClientError("down")) for _ in range(3)])
        _wired(api, session, count=3)

        with pytest.raises(NetworkUnavailableError):
            await api._request("countries")
        assert len(session.urls) == 3

    @pytest.mark.asyncio
    async def test_every_mirror_500_also_counts_as_unreachable(self, api):
        session = _Session(*[_Resp(500) for _ in range(3)])
        _wired(api, session, count=3)

        with pytest.raises(NetworkUnavailableError):
            await api._request("countries")

    @pytest.mark.asyncio
    async def test_the_retry_budget_follows_the_mirror_count(self, api):
        session = _Session(*[_Resp(500) for _ in range(5)])
        _wired(api, session, count=5)

        with pytest.raises(NetworkUnavailableError):
            await api._request("countries")
        assert len(session.urls) == 5

    @pytest.mark.asyncio
    async def test_an_unresolved_pool_still_gets_a_second_chance(self, api):
        """server_count is 0 until DNS answers; the floor of 2 is what retries."""
        session = _Session(_Boom(aiohttp.ClientError("down")), _Resp(200, ["ok"]))
        _wired(api, session, count=0)

        assert await api._request("countries") == ["ok"]
        assert len(session.urls) == 2

    @pytest.mark.asyncio
    async def test_an_unexpected_error_is_a_logical_failure_not_an_outage(self, api):
        """A bug on our side must not tell the UI the internet is down."""
        session = _Session(_Boom(ValueError("bad json")))
        _wired(api, session)

        assert await api._request("countries") is None
