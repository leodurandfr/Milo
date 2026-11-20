# backend/tests/test_podcast_catalogue.py
"""`podcast_catalog.py` — the client's Apple half, and its feed half.

The Apple half (charts, search, caches) did not change when the episode layer
moved to the publisher's feed, and neither did its coverage here. What it
never ran is the other half of each failure pair:

* **the successful iTunes Search parse.** Lines 429-437 had never executed, so
  the podcast search screen's entire data path — read as text because Apple
  serves `text/javascript`, decode, drop hits with no `collectionId`, normalize
  — was unmeasured. Every green search test asserted a failure.
* **the by-genre branch that never reaches the network.** An unmapped genre key
  short-circuits, and it is the only place a caller learns that Milō's genre
  vocabulary and Apple's ids can disagree.
* **the caches**, on both the hit side and the eviction side. A cache that never
  hits costs one upstream call per screen; a cache that never evicts grows for
  the life of the process. `ExpiringCache` is tested on its own, with a clock
  the test moves: an expired entry is released rather than skipped, and the
  whole is bounded in bytes, because a chart and a 1,000-episode series are
  not the same size (4.07 MB for one feed, measured).

The feed half is `TestOpeningAPodcast` and `TestFindingOneEpisode` below. The
parsing itself is not retested here — that is
`test_podcast_rss_parser.py`; what these assert is what the *client* does with
it, which is caching, copying and failing.

The doubles are canned responses usable as the async context manager the client
opens, assigned as the *session* rather than by replacing a method of the unit
(`_ensure_session` only builds a session when there is none). The client holds
two sessions — its own and the resolver's — so both are pointed at the double.
"""
import asyncio
import gc
import json
import tracemalloc
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import MagicMock

import pytest

from backend.sources.podcast.podcast_catalog import (
    GENRE_TO_ITUNES_ID,
    ExpiringCache,
    PodcastCatalog,
    estimate_size,
    is_upstream_error,
)
from backend.sources.podcast.rss_parser import make_episode_id, parse_feed


@pytest.fixture
def api():
    return PodcastCatalog()


class _FakeResponse:
    def __init__(self, status, payload=None, text="", body=b""):
        self.status = status
        self._payload = payload
        self._text = text
        self._body = body

    async def json(self):
        return self._payload

    async def text(self):
        return self._text

    async def read(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


def _answers_with(api, *responses):
    """Point the client at a canned upstream; the last response repeats."""
    queued = list(responses)
    session = MagicMock()
    session.closed = False
    session.get = MagicMock(
        side_effect=lambda *a, **kw: queued.pop(0) if len(queued) > 1 else queued[0]
    )
    api.session = session
    api.resolver.session = session
    return session


def _raises(api, exc):
    session = MagicMock()
    session.closed = False
    session.get = MagicMock(side_effect=exc)
    api.session = session
    api.resolver.session = session
    return session


ITUNES_HIT = {
    "collectionId": 152249110,
    "collectionName": "Radiolab",
    "artistName": "WNYC Studios",
    "artworkUrl100": "https://is1.mzstatic.com/image/100x100bb.jpg",
    "trackCount": 530,
}


def _itunes_search(*hits):
    return json.dumps({"resultCount": len(hits), "results": list(hits)})


class TestTheSearchThatSucceeds:
    """`_search_itunes` — the branch the search screen actually takes."""

    async def test_apples_answer_is_read_as_text_not_json(self, api):
        """Apple serves this endpoint as `text/javascript`, which is why the
        client reads `text()` and decodes by hand. The double answers text only
        — a client that switched to `resp.json()` would get None here and
        produce an empty search rather than an error."""
        _answers_with(api, _FakeResponse(
            200, payload=None, text=_itunes_search(ITUNES_HIT)
        ))

        result = await api.search_podcasts("radiolab")

        assert [p["name"] for p in result["podcasts"]] == ["Radiolab"]

    async def test_a_hit_comes_back_normalized_to_milo_keys(self, api):
        """Non-triviality first: every other assertion in this class rests on
        the search path being able to return something."""
        _answers_with(api, _FakeResponse(200, text=_itunes_search(ITUNES_HIT)))

        result = await api.search_podcasts("radiolab")

        assert [p["name"] for p in result["podcasts"]] == ["Radiolab"]
        assert "api_error" not in result

    async def test_a_search_hit_is_ready_to_open(self, api):
        """The contract the routes' subscribed-flag join depends on: a hit's
        `uuid` is its Apple id, which is also what a subscription stores. There
        is no resolution step left to fail between seeing a result and opening
        it — that step is what answered "No podcast found for iTunes ID"."""
        _answers_with(api, _FakeResponse(200, text=_itunes_search(ITUNES_HIT)))

        podcast = (await api.search_podcasts("radiolab"))["podcasts"][0]

        assert podcast["uuid"] == "152249110"
        assert podcast["itunes_id"] == "152249110"

    async def test_one_fetch_serves_every_page(self, api):
        """Apple is asked once per (country, term); the pages are sliced from
        that. Driven through the session rather than by patching the client's
        own search method, which would pin a private name instead of the
        behaviour."""
        # Ids start at 1: a collectionId of 0 is falsy and the client drops it,
        # which is the guard `test_a_hit_with_no_collection_id_is_dropped` owns.
        hits = [dict(ITUNES_HIT, collectionId=i, collectionName=f"P{i}")
                for i in range(1, 61)]
        session = _answers_with(api, _FakeResponse(200, text=_itunes_search(*hits)))

        page1 = await api.search_podcasts("radiolab", page=1, limit=25)
        page3 = await api.search_podcasts("radiolab", page=3, limit=25)

        assert session.get.call_count == 1
        assert len(page1["podcasts"]) == 25
        assert page1["podcasts"][0]["itunes_id"] == "1"
        assert len(page3["podcasts"]) == 10   # 60 hits -> 25 + 25 + 10
        assert page1["pagination"]["podcasts"] == {"total": 60, "pages": 3}

    async def test_the_thumbnail_is_upscaled_to_the_size_the_ui_renders(self, api):
        """Apple's search API answers with a 100 px thumbnail; the podcast grid
        renders it at several hundred, so unscaled it is visibly blurred."""
        _answers_with(api, _FakeResponse(200, text=_itunes_search(ITUNES_HIT)))

        podcast = (await api.search_podcasts("radiolab"))["podcasts"][0]

        assert podcast["image_url"].endswith("600x600bb.jpg")

    async def test_a_hit_with_no_collection_id_is_dropped(self, api):
        """`collectionId` is the only handle Milō has on a search hit — no id
        means the podcast cannot be opened, so a card for it is a dead card."""
        _answers_with(api, _FakeResponse(
            200, text=_itunes_search({"collectionName": "Nameless"}, ITUNES_HIT)
        ))

        result = await api.search_podcasts("radiolab")

        assert [p["name"] for p in result["podcasts"]] == ["Radiolab"]

    async def test_a_body_apple_did_not_encode_as_json_is_an_upstream_error(self, api):
        """Apple answers 200 with an HTML error page often enough to matter.
        The decode failure has to become `api_error`, or the screen says "no
        results" for a search that was never run."""
        _answers_with(api, _FakeResponse(200, text="<html>error</html>"))

        result = await api.search_podcasts("radiolab")

        assert result["api_error"] is True
        assert result["podcasts"] == []

    async def test_apple_being_unreachable_is_an_upstream_error(self, api):
        _raises(api, asyncio.TimeoutError())

        result = await api.search_podcasts("radiolab")

        assert result["api_error"] is True

    async def test_the_term_is_cached_per_country_and_not_refetched(self, api):
        """One fetch of ITUNES_SEARCH_MAX hits per (country, term), then pages
        are sliced locally — so paging through results must not re-hit Apple."""
        session = _answers_with(api, _FakeResponse(200, text=_itunes_search(ITUNES_HIT)))

        await api.search_podcasts("radiolab", page=1)
        await api.search_podcasts("radiolab", page=2)

        assert session.get.call_count == 1

    async def test_the_same_term_in_another_country_is_fetched_again(self, api):
        """Apple's catalogue differs per store, so the country belongs in the
        cache key — without it the first store's answers are served to every
        other language."""
        session = _answers_with(api, _FakeResponse(200, text=_itunes_search(ITUNES_HIT)))

        await api.search_podcasts("radiolab", country="fr")
        await api.search_podcasts("radiolab", country="us")

        assert session.get.call_count == 2

    async def test_a_failed_search_is_not_cached(self, api):
        """One 503 must not blank the search for the whole cache window."""
        _answers_with(
            api,
            _FakeResponse(503),
            _FakeResponse(200, text=_itunes_search(ITUNES_HIT)),
        )

        failed = await api.search_podcasts("radiolab")
        recovered = await api.search_podcasts("radiolab")

        assert failed["api_error"] is True
        assert [p["name"] for p in recovered["podcasts"]] == ["Radiolab"]


class TestTheStorefront:
    """`storefront()` — which Apple store every chart, search and feed lookup
    reads. A wrong one answers with a full, plausible, foreign catalogue, so
    nothing on screen says it is wrong: a Portuguese unit showed Brazil's charts,
    because the store came from the language and Portuguese is two countries."""

    async def test_the_units_country_decides_over_the_language(self, api):
        """A Brazilian unit with the UI in (European) Portuguese reads Brazil."""
        session = _answers_with(api, _FakeResponse(200, text="{}"))

        assert await api.storefront("BR", "portuguese") == "br"
        assert "/br/" in session.get.call_args.args[0]

    async def test_with_no_country_the_language_decides_and_apple_is_not_asked(self, api):
        session = _answers_with(api, _FakeResponse(200, text="{}"))

        assert await api.storefront("", "portuguese") == "pt"
        assert await api.storefront("", "klingon") == "us"
        session.get.assert_not_called()

    async def test_a_country_apple_has_no_store_in_falls_back_once_and_for_all(self, api):
        """Apple answers 400 for such a country (Iran, measured). The answer is
        kept: every chart, search and episode would otherwise ask again."""
        session = _answers_with(api, _FakeResponse(400))

        assert await api.storefront("IR", "english") == "us"
        assert await api.storefront("IR", "english") == "us"
        assert session.get.call_count == 1

    async def test_a_failed_probe_settles_nothing(self, api):
        """Offline, the country is read as it is and asked again next time: a
        network failure is not Apple saying the store does not exist."""
        import aiohttp
        session = _raises(api, aiohttp.ClientConnectionError("down"))

        assert await api.storefront("FR", "english") == "fr"
        assert await api.storefront("FR", "english") == "fr"
        assert session.get.call_count == 2


class TestTheGenreCharts:
    def test_every_genre_key_the_frontend_offers_has_an_apple_id(self):
        """Derived from the production table rather than restated: a Milō genre
        with no id short-circuits below and renders an empty screen with no
        error."""
        assert GENRE_TO_ITUNES_ID
        assert all(isinstance(v, (int, str)) and v for v in GENRE_TO_ITUNES_ID.values())

    async def test_an_unmapped_genre_never_reaches_apple(self, api):
        """The short-circuit. Building a URL with `genre=None` asks Apple for
        the whole store, which answers 200 with the wrong chart — silently."""
        session = _answers_with(api, _FakeResponse(200, text="{}"))

        result = await api.get_itunes_top_podcasts_by_genre(
            genre="PODCASTSERIES_NOT_A_GENRE", country_code="fr"
        )

        assert result == {"results": [], "total": 0}
        session.get.assert_not_called()

    async def test_a_mapped_genre_is_fetched_from_the_right_store(self, api):
        genre = next(iter(GENRE_TO_ITUNES_ID))
        session = _answers_with(api, _FakeResponse(200, text=_chart()))

        await api.get_itunes_top_podcasts_by_genre(genre=genre, country_code="fr")

        url = session.get.call_args.args[0]
        assert f"/fr/rss/toppodcasts/genre={GENRE_TO_ITUNES_ID[genre]}/" in url

    async def test_the_limit_is_capped_at_what_apple_serves(self, api):
        """Apple's RSS endpoint refuses above 200; asking for more returns an
        error page, which the screen would show as an empty chart."""
        genre = next(iter(GENRE_TO_ITUNES_ID))
        session = _answers_with(api, _FakeResponse(200, text=_chart()))

        await api.get_itunes_top_podcasts_by_genre(
            genre=genre, country_code="fr", limit=5000
        )

        assert "limit=200/" in session.get.call_args.args[0]

    async def test_two_genres_do_not_share_a_cache_entry(self, api):
        """The cache key carries the genre; without it the first genre opened
        is the chart every other genre shows."""
        keys = list(GENRE_TO_ITUNES_ID)[:2]
        session = _answers_with(api, _FakeResponse(200, text=_chart()))

        await api.get_itunes_top_podcasts_by_genre(genre=keys[0], country_code="fr")
        await api.get_itunes_top_podcasts_by_genre(genre=keys[1], country_code="fr")

        assert session.get.call_count == 2


def _chart(*names):
    """An iTunes RSS top-charts body, as `_fetch_itunes_top` parses it."""
    names = names or ("Radiolab",)
    return json.dumps({
        "feed": {
            "entry": [{
                "id": {"attributes": {"im:id": f"1522491{i:02d}"}},
                "im:name": {"label": n},
                "im:artist": {"label": "WNYC Studios"},
                "im:image": [{"label": "https://is1.mzstatic.com/170x170bb.jpg"}],
            } for i, n in enumerate(names)]
        }
    })


def _chart_with_one_bare_entry():
    """iTunes RSS answers `entry` as a bare object, not a list, when the chart
    holds exactly one item — captured from the documented quirk the client's
    own comment names."""
    return json.dumps({
        "feed": {
            "entry": {
                "id": {"attributes": {"im:id": "152249110"}},
                "im:name": {"label": "Radiolab"},
                "im:artist": {"label": "WNYC Studios"},
                "im:image": [{"label": "https://is1.mzstatic.com/170x170bb.jpg"}],
            }
        }
    })


class TestTheChartsFetch:
    async def test_a_single_entry_chart_is_not_iterated_as_a_dict(self, api):
        """The normalization the client's comment asks for. Without it the loop
        walks the dict's *keys* and every field lookup fails — a one-entry
        chart crashes the discovery screen instead of showing its one podcast."""
        _answers_with(api, _FakeResponse(200, text=_chart_with_one_bare_entry()))

        result = await api.get_itunes_top_podcasts(country_code="fr")

        assert [p["name"] for p in result["results"]] == ["Radiolab"]

    async def test_a_second_call_is_served_from_the_cache(self, api):
        """The charts are the home screen; without the hit every open is a
        round-trip to Apple."""
        session = _answers_with(api, _FakeResponse(200, text=_chart()))

        await api.get_itunes_top_podcasts(country_code="fr")
        await api.get_itunes_top_podcasts(country_code="fr")

        assert session.get.call_count == 1

    async def test_a_stale_cache_entry_is_refetched(self):
        """The other half of the pair: an entry past the cache duration must
        not be served, or the charts freeze for the life of the process. A
        catalog whose entries expire at once stands for the hours passing."""
        api = PodcastCatalog(cache_duration_minutes=0)
        session = _answers_with(api, _FakeResponse(200, text=_chart()))

        await api.get_itunes_top_podcasts(country_code="fr")
        await api.get_itunes_top_podcasts(country_code="fr")

        assert session.get.call_count == 2

    async def test_apple_being_unreachable_is_an_api_error(self, api):
        _raises(api, asyncio.TimeoutError())

        assert await api.get_itunes_top_podcasts(country_code="fr") == {
            "results": [], "total": 0, "api_error": True
        }

    async def test_a_body_apple_did_not_encode_as_json_is_an_api_error(self, api):
        _answers_with(api, _FakeResponse(200, text="<html>error</html>"))

        assert (await api.get_itunes_top_podcasts(country_code="fr"))["api_error"] is True


class _Clock:
    """A monotonic clock the test moves by hand."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class TestTheExpiringCache:
    """`ExpiringCache` — what every catalog cache is. The backend runs under
    `MemoryMax=512M`, and an OOM kill cuts the sound."""

    def test_an_entry_is_served_until_it_expires(self):
        clock = _Clock()
        cache = ExpiringCache(ttl_s=60, max_bytes=10_000, clock=clock)
        cache.put("chart", {"results": ["Radiolab"]}, size=100)

        clock.now += 59
        assert cache.get("chart") == {"results": ["Radiolab"]}
        clock.now += 1
        assert cache.get("chart") is None

    def test_an_expired_entry_is_released_when_read_not_just_skipped(self):
        """The defect: an expired series was ignored and kept, so a feed opened
        once held its megabytes for the life of the process."""
        clock = _Clock()
        cache = ExpiringCache(ttl_s=60, max_bytes=10_000, clock=clock)
        cache.put("series", {"episodes": []}, size=4_000)

        clock.now += 61
        cache.get("series")

        assert len(cache) == 0
        assert cache.size == 0

    def test_storing_anything_releases_every_expired_entry(self):
        """Most entries are never read again — a series opened once, a search
        for a term nobody types twice — so a read alone cannot be the only way
        out."""
        clock = _Clock()
        cache = ExpiringCache(ttl_s=60, max_bytes=10_000, clock=clock)
        cache.put("old-1", "a", size=1_000)
        cache.put("old-2", "b", size=1_000)
        clock.now += 30
        cache.put("recent", "c", size=1_000)
        clock.now += 31

        cache.put("new", "d", size=1_000)

        assert cache.get("recent") == "c"
        assert len(cache) == 2
        assert cache.size == 2_000

    def test_the_cache_never_holds_more_than_its_budget(self):
        cache = ExpiringCache(ttl_s=3600, max_bytes=10_000, clock=_Clock())

        for i in range(50):
            cache.put(f"k{i}", i, size=1_500)

        assert cache.size <= 10_000
        assert len(cache) == 6

    def test_the_oldest_entries_are_the_ones_dropped(self):
        """Dropping the newest instead would evict what the owner is looking at
        right now and keep what they have finished with."""
        clock = _Clock()
        cache = ExpiringCache(ttl_s=3600, max_bytes=3_000, clock=clock)
        for key in ("first", "second", "third"):
            cache.put(key, key, size=1_000)
            clock.now += 1

        cache.put("fourth", "fourth", size=1_000)

        assert cache.get("first") is None
        assert [cache.get(k) for k in ("second", "third", "fourth")] == [
            "second", "third", "fourth"
        ]

    def test_storing_a_key_again_counts_its_bytes_once(self):
        cache = ExpiringCache(ttl_s=3600, max_bytes=10_000, clock=_Clock())

        cache.put("series", "v1", size=4_000)
        cache.put("series", "v2", size=5_000)

        assert cache.size == 5_000
        assert cache.get("series") == "v2"

    def test_a_value_larger_than_the_whole_budget_is_not_kept(self):
        """Kept, it would evict everything else and still exceed the budget."""
        cache = ExpiringCache(ttl_s=3600, max_bytes=10_000, clock=_Clock())
        cache.put("small", "s", size=1_000)

        cache.put("huge", "h", size=10_001)

        assert cache.get("huge") is None
        assert cache.get("small") == "s"

    def test_a_value_is_weighed_down_to_its_strings(self):
        """The budget is only as honest as the weighing: a size that stopped at
        the outer dict would call a 1,000-episode series a few hundred bytes."""
        description = "x" * 5_000
        series = {"episodes": [{"description": description + str(i)} for i in range(20)]}

        assert estimate_size(series) > 20 * 5_000

    def test_an_object_shared_by_many_episodes_is_weighed_once(self):
        """Every episode inherits its show's image URL as the same string
        object; weighed per episode, a series would read heavier than it is."""
        shared = "https://cdn.example/" + "a" * 10_000
        one = estimate_size([{"image_url": shared}])
        many = estimate_size([{"image_url": shared} for _ in range(100)])

        assert many - one < 100 * 1_000


FEED_URL = "https://cdn.example/radiolab.xml"


def _feed_bytes(*guids, title="Radiolab", first_day=1):
    items = "".join(
        f"<item><title>Ep {g}</title><guid>{g}</guid>"
        f"<pubDate>Wed, {first_day + i:02d} Jan 2025 10:00:00 +0000</pubDate>"
        f'<enclosure url="https://cdn.example/{g}.mp3" length="1" type="audio/mpeg"/>'
        f"</item>"
        for i, g in enumerate(guids)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        f"<channel><title>{title}</title>"
        '<itunes:image href="https://cdn.example/art.jpg"/>'
        f"{items}</channel></rss>"
    ).encode("utf-8")


def _lookup(feed_url=FEED_URL, collection_id=152249110):
    body = {"resultCount": 1, "results": [
        {"collectionId": collection_id, "feedUrl": feed_url}
    ]}
    return _FakeResponse(200, text=json.dumps(body))


class TestOpeningAPodcast:
    """A chart entry opens straight onto the publisher's feed: resolve the URL
    from Apple, download once, serve every page from that."""

    async def test_a_podcast_opens_with_its_episodes(self, api):
        _answers_with(api, _lookup(), _FakeResponse(200, body=_feed_bytes("a", "b")))

        series = await api.get_podcast_series("152249110")

        assert series["name"] == "Radiolab"
        assert series["uuid"] == "152249110"
        assert series["total_episodes"] == 2
        assert series["episodes"][0]["audio_url"].endswith(".mp3")

    async def test_a_second_open_of_the_same_series_hits_no_network(self, api):
        """A feed is indivisible and publishers ignore conditional requests, so
        every miss is a full download. Paging the episode list must not repeat
        it."""
        session = _answers_with(
            api, _lookup(), _FakeResponse(200, body=_feed_bytes("a", "b", "c"))
        )

        first = await api.get_podcast_series("152249110")
        await api.get_podcast_series("152249110", episodes_page=2)

        assert first["name"] == "Radiolab"
        assert session.get.call_count == 2

    async def test_pages_are_sliced_from_the_one_download(self, api):
        _answers_with(
            api, _lookup(), _FakeResponse(200, body=_feed_bytes("a", "b", "c"))
        )

        page = await api.get_podcast_series(
            "152249110", episodes_page=2, episodes_limit=2
        )

        assert [e["guid"] for e in page["episodes"]] == ["a"]
        # The count is the whole feed, not the page: the "load more" cutoff
        # reads it.
        assert page["total_episodes"] == 3

    async def test_oldest_first_reverses_the_list(self, api):
        _answers_with(
            api, _lookup(), _FakeResponse(200, body=_feed_bytes("a", "b", "c"))
        )

        latest = await api.get_podcast_series("152249110")
        oldest = await api.get_podcast_series("152249110", sort_order="OLDEST")

        assert [e["guid"] for e in latest["episodes"]] == ["c", "b", "a"]
        assert [e["guid"] for e in oldest["episodes"]] == ["a", "b", "c"]

    async def test_a_podcast_apple_publishes_no_feed_for_is_not_a_series(self, api):
        """A subscriber-only show. The caller turns None into the 404 that
        shows the 'not available' notice."""
        _answers_with(api, _FakeResponse(200, text=json.dumps(
            {"resultCount": 1, "results": [{"collectionId": 152249110}]}
        )), _FakeResponse(404, text=""))

        assert await api.get_podcast_series("152249110") is None

    async def test_a_feed_that_failed_to_download_is_an_outage_not_an_absence(
        self, api
    ):
        """The publisher's CDN answered 503. Reported as absence, the details
        view would tell the owner the podcast is gone and pop itself; and
        caching that would keep it "gone" for the whole cache window."""
        _answers_with(api, _lookup(), _FakeResponse(503, body=b""))

        assert is_upstream_error(await api.get_podcast_series("152249110"))
        assert len(api._feed_cache) == 0

    async def test_a_document_that_is_not_a_feed_is_an_outage(self, api):
        """A captcha or an error page arrives with HTTP 200. The feed exists;
        this attempt failed."""
        _answers_with(api, _lookup(), _FakeResponse(200, body=b"<html>nope</html>"))

        assert is_upstream_error(await api.get_podcast_series("152249110"))
        assert len(api._feed_cache) == 0

    async def test_apple_being_unreachable_is_an_outage(self, api):
        """The failure one layer up: the resolver could not ask. Answering
        `None` here is what would make an offline appliance report every
        podcast as unavailable."""
        _raises(api, asyncio.TimeoutError())

        assert is_upstream_error(await api.get_podcast_series("152249110"))

    async def test_the_series_a_caller_gets_can_be_enriched_safely(self, api):
        """The route stamps `is_subscribed` on what it receives. Handing out the
        cached object would make one reader's subscription state everyone's."""
        _answers_with(api, _lookup(), _FakeResponse(200, body=_feed_bytes("a")))

        first = await api.get_podcast_series("152249110")
        first["is_subscribed"] = True
        first["episodes"][0]["playback_progress"] = {"position": 42}
        second = await api.get_podcast_series("152249110")

        assert "is_subscribed" not in second
        assert "playback_progress" not in second["episodes"][0]


class TestFindingOneEpisode:
    """`{itunes_id}:{guid hash}` addresses an episode without a directory. The
    feed is fetched whole because the audio URL lives in it."""

    async def test_an_episode_is_found_by_its_identifier(self, api):
        _answers_with(api, _lookup(), _FakeResponse(200, body=_feed_bytes("a", "b")))

        episode = await api.get_episode(make_episode_id("152249110", "b"))

        assert episode["guid"] == "b"
        assert episode["audio_url"] == "https://cdn.example/b.mp3"

    async def test_an_identifier_naming_no_episode_answers_none(self, api):
        """The guid vanished from the feed — the publisher pulled the episode.
        A stored position pointing at it must not resurrect anything."""
        _answers_with(api, _lookup(), _FakeResponse(200, body=_feed_bytes("a")))

        assert await api.get_episode(make_episode_id("152249110", "gone")) is None

    async def test_something_that_is_not_an_identifier_never_reaches_apple(self, api):
        """An id from another catalogue, or junk. Sending it upstream turns a local
        bug into an upstream error nobody attributes correctly."""
        session = _answers_with(api, _lookup())

        assert await api.get_episode("16795089") is None
        session.get.assert_not_called()

    async def test_the_episode_a_caller_gets_can_be_enriched_safely(self, api):
        """The route enriches the returned dict in place with
        `playback_progress`; the cached object must not carry it away."""
        _answers_with(api, _lookup(), _FakeResponse(200, body=_feed_bytes("a")))
        episode_id = make_episode_id("152249110", "a")

        first = await api.get_episode(episode_id)
        first["playback_progress"] = {"position": 42}
        second = await api.get_episode(episode_id)

        assert "playback_progress" not in second

    async def test_a_download_failure_is_an_outage_not_a_missing_episode(self, api):
        _answers_with(api, _lookup(), _FakeResponse(503, body=b""))

        assert is_upstream_error(
            await api.get_episode(make_episode_id("152249110", "a"))
        )
        assert len(api._feed_cache) == 0


class TestMergingSubscriptions:
    """The subscriptions screen merges every subscribed feed by date."""

    async def test_episodes_from_several_feeds_are_merged_newest_first(self, api):
        session = MagicMock()
        session.closed = False
        answers = {
            # Distinct days across both feeds, so the merged order is decided
            # by the dates and not by which feed answered first.
            "111": _FakeResponse(200, body=_feed_bytes("old", title="A", first_day=1)),
            "222": _FakeResponse(
                200, body=_feed_bytes("x", "new", title="B", first_day=2)
            ),
        }

        def _get(url, **kw):
            if "itunes.apple.com/lookup" in url:
                ids = kw.get("params", {}).get("id", "")
                return _FakeResponse(200, text=json.dumps({"results": [
                    {"collectionId": int(i), "feedUrl": f"https://cdn/{i}.xml"}
                    for i in ids.split(",")
                ]}))
            return answers["111" if "/111." in url else "222"]

        session.get = MagicMock(side_effect=_get)
        api.session = session
        api.resolver.session = session

        result = await api.get_latest_episodes(["111", "222"])

        assert [e["guid"] for e in result["results"]] == ["new", "x", "old"]

    async def test_the_merge_keeps_no_whole_series(self, api):
        """The defect: "latest episodes" read every subscription and cached
        each series whole — megabytes per feed, a hundred feeds, for the cache
        window. What it keeps is each feed's head, without the descriptions
        the episode card never shows."""
        long_feed = _many_episodes(PodcastCatalog.LATEST_EPISODES_MAX + 30, title="A")
        downloads = _serve_feeds(api, {
            "111": long_feed,
            "222": _many_episodes(3, title="B"),
        })

        result = await api.get_latest_episodes(["111", "222"])

        assert result["results"], "the merge answered nothing"
        assert downloads == ["111", "222"]
        assert len(api._feed_cache) == 0
        assert all("description" not in ep for ep in result["results"])
        # The head is the newest page of the feed and nothing older: kept whole
        # minus its descriptions, a Vergecast-sized series still weighs over a
        # megabyte, and the latest cache would hold a handful of subscriptions
        # and download the rest again at every home-screen open.
        newest = sorted(
            parse_feed(long_feed, "111", "https://cdn/111.xml")["episodes"],
            key=lambda e: e["date_published"], reverse=True,
        )[:PodcastCatalog.LATEST_EPISODES_MAX]
        kept = api._latest_cache.get("111")
        assert [e["uuid"] for e in kept] == [e["uuid"] for e in newest]
        # Opening the series afterwards finds nothing whole to serve from.
        series = await api.get_podcast_series("111")
        assert series["total_episodes"] == 80
        assert downloads == ["111", "222", "111"]

    async def test_a_second_merge_downloads_nothing(self, api):
        """The heads are what the cache window keeps: the home screen reopened
        within it costs no feed."""
        downloads = _serve_feeds(api, {"111": _many_episodes(5), "222": _many_episodes(5)})

        first = await api.get_latest_episodes(["111", "222"])
        second = await api.get_latest_episodes(["111", "222"])

        assert len(first["results"]) == 10
        assert [e["uuid"] for e in second["results"]] == [e["uuid"] for e in first["results"]]
        assert downloads == ["111", "222"]

    async def test_a_series_held_whole_serves_its_head_without_a_download(self, api):
        downloads = _serve_feeds(api, {"111": _many_episodes(5)})
        await api.get_podcast_series("111")

        result = await api.get_latest_episodes(["111"])

        assert len(result["results"]) == 5
        assert downloads == ["111"]

    async def test_the_merge_is_exact_up_to_its_limit(self, api):
        """Each feed keeps only its newest `LATEST_EPISODES_MAX`, and a page of
        that many can take no more from one feed — so the merge is the same as
        sorting every episode of every feed. One feed entirely newer than the
        other is the page that takes all of it from one feed, which is where a
        head shorter than a page would let the older feed in."""
        count = PodcastCatalog.LATEST_EPISODES_MAX + 30
        feeds = {
            "111": _many_episodes(count, title="A", offset_h=0, step_h=1),
            "222": _many_episodes(count, title="B", offset_h=count, step_h=1),
        }
        _serve_feeds(api, feeds)

        result = await api.get_latest_episodes(["111", "222"])

        every = [
            ep for itunes_id, raw in feeds.items()
            for ep in parse_feed(raw, itunes_id, f"https://cdn/{itunes_id}.xml")["episodes"]
        ]
        every.sort(key=lambda e: e["date_published"], reverse=True)
        expected = [e["uuid"] for e in every[:PodcastCatalog.LATEST_EPISODES_MAX]]
        assert [e["uuid"] for e in result["results"]] == expected

    async def test_what_the_merge_retains_stays_within_its_budget(self, api):
        """Measured rather than estimated: what forty large subscriptions leave
        behind once the merge has answered, by tracemalloc. Cached whole, as
        they were, they held about 1 MB each; the heads must fit the latest
        cache's budget and leave the feed cache empty."""
        feeds = {
            str(100 + i): _many_episodes(150, title=f"Show {i}", description_len=3_000)
            for i in range(40)
        }
        _serve_feeds(api, feeds)
        whole = estimate_size(parse_feed(feeds["100"], "100", "https://cdn/100.xml"))
        assert whole > 400_000, "the simulated feeds are too small to measure anything"

        gc.collect()
        tracemalloc.start()
        try:
            before = tracemalloc.take_snapshot()
            result = await api.get_latest_episodes(list(feeds))
            del result
            gc.collect()
            after = tracemalloc.take_snapshot()
        finally:
            tracemalloc.stop()
        retained = sum(stat.size_diff for stat in after.compare_to(before, "filename"))

        assert len(api._feed_cache) == 0
        assert retained < PodcastCatalog.LATEST_CACHE_BYTES
        assert retained < 40 * whole / 4

    async def test_the_whole_set_is_resolved_in_one_lookup(self, api):
        """Apple's lookup endpoint takes 200 ids per call. Resolving one
        subscription at a time would make a cold subscriptions screen cost one
        request per followed podcast, every two hours, forever."""
        lookups = []

        def _get(url, **kw):
            if "itunes.apple.com/lookup" in url:
                lookups.append(kw.get("params", {}).get("id", ""))
                return _FakeResponse(200, text=json.dumps({"results": [
                    {"collectionId": int(i), "feedUrl": f"https://cdn/{i}.xml"}
                    for i in kw["params"]["id"].split(",")
                ]}))
            return _FakeResponse(200, body=_feed_bytes("a"))

        session = MagicMock()
        session.closed = False
        session.get = MagicMock(side_effect=_get)
        api.session = session
        api.resolver.session = session

        await api.get_latest_episodes(["111", "222", "333"])

        assert lookups == ["111,222,333"]

    async def test_no_subscriptions_costs_no_request(self, api):
        session = _answers_with(api, _lookup())

        assert await api.get_latest_episodes([]) == {"results": [], "total": 0}
        session.get.assert_not_called()

    async def test_every_feed_failing_is_reported_as_a_catalogue_outage(self, api):
        """One dead feed among several is not worth telling the user the
        catalogue is down; all of them is."""
        _raises(api, asyncio.TimeoutError())

        result = await api.get_latest_episodes(["111", "222"])

        assert result["results"] == []
        assert result["api_error"] is True

    async def test_subscriptions_apple_publishes_no_feed_for_are_not_an_outage(
        self, api
    ):
        """Empty because those podcasts have no public feed, not because
        anything failed. Offering a retry here offers a retry that can never
        succeed."""
        _answers_with(
            api,
            _FakeResponse(200, text=json.dumps(
                {"results": [{"collectionId": 111}, {"collectionId": 222}]}
            )),
            _FakeResponse(404, text=""),
        )

        result = await api.get_latest_episodes(["111", "222"])

        assert result["results"] == []
        assert "api_error" not in result


def _many_episodes(count, title="Show", offset_h=0, step_h=24, description_len=200):
    """A feed of `count` episodes, newest first, one every `step_h` hours, each
    with a description of `description_len` characters — the part of an
    episode that weighs."""
    base = datetime(2025, 6, 1, tzinfo=timezone.utc)
    items = "".join(
        f"<item><title>{title} {i}</title><guid>{title}-{i}</guid>"
        f"<description>{'d' * description_len} {i}</description>"
        f"<pubDate>{format_datetime(base - timedelta(hours=offset_h + i * step_h))}</pubDate>"
        f'<enclosure url="https://cdn.example/{title}/{i}.mp3" length="1" type="audio/mpeg"/>'
        f"</item>"
        for i in range(count)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">'
        f"<channel><title>{title}</title>"
        '<itunes:image href="https://cdn.example/art.jpg"/>'
        f"{items}</channel></rss>"
    ).encode("utf-8")


def _serve_feeds(api, feeds):
    """Point the client at Apple's lookup and the publishers' CDN, serving
    `feeds` ({itunes id: feed bytes}); returns the ids downloaded, in order."""
    downloads = []

    def _get(url, **kw):
        if "itunes.apple.com/lookup" in url:
            ids = kw.get("params", {}).get("id", "")
            return _FakeResponse(200, text=json.dumps({"results": [
                {"collectionId": int(i), "feedUrl": f"https://cdn/{i}.xml"}
                for i in ids.split(",")
            ]}))
        itunes_id = url.rsplit("/", 1)[-1].removesuffix(".xml")
        downloads.append(itunes_id)
        return _FakeResponse(200, body=feeds[itunes_id])

    session = MagicMock()
    session.closed = False
    session.get = MagicMock(side_effect=_get)
    api.session = session
    api.resolver.session = session
    return downloads
