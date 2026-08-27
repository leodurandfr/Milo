# backend/tests/test_podcast_catalogue.py
"""`podcastindex_api.py` — the parts of the catalogue client nothing entered.

`test_podcastindex_api.py` covers the normalizers and, since B-era work, the
"upstream failed" arms. What it never ran is the other half of each of those
pairs:

* **the successful iTunes Search parse.** Lines 429-437 had never executed, so
  the podcast search screen's entire data path — read as text because Apple
  serves `text/javascript`, decode, drop hits with no `collectionId`, normalize
  — was unmeasured. Every green search test asserted a failure.
* **the by-genre branch that never reaches the network.** An unmapped genre key
  short-circuits, and it is the only place a caller learns that Milō's genre
  vocabulary and Apple's ids can disagree.
* **the caches**, on both the hit side and the eviction side. A cache that never
  hits costs one upstream call per screen; a cache that never evicts grows for
  the life of the process.

The doubles are the ones already built in `test_podcastindex_api.py`, kept in
one place: a canned response usable as the async context manager the client
opens, assigned as the *session* rather than by replacing a method of the unit
(`_ensure_session` only builds a session when there is none).
"""
import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from backend.sources.podcast.podcastindex_api import (
    GENRE_TO_ITUNES_ID,
    PodcastIndexAPI,
)


@pytest.fixture
def api():
    return PodcastIndexAPI(api_key="test-key", api_secret="test-secret")


class _FakeResponse:
    def __init__(self, status, payload=None, text=""):
        self.status = status
        self._payload = payload
        self._text = text

    async def json(self):
        return self._payload

    async def text(self):
        return self._text

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
    return session


def _raises(api, exc):
    session = MagicMock()
    session.closed = False
    session.get = MagicMock(side_effect=exc)
    api.session = session
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

    async def test_a_search_hit_carries_no_feed_id_yet(self, api):
        """The contract the routes' subscribed-flag join depends on: an
        iTunes-sourced hit has `uuid=None` until `lookup_by_itunes_id` resolves
        it, so only the Apple id can match a stored subscription."""
        _answers_with(api, _FakeResponse(200, text=_itunes_search(ITUNES_HIT)))

        podcast = (await api.search_podcasts("radiolab"))["podcasts"][0]

        assert podcast["uuid"] is None
        assert podcast["itunes_id"] == "152249110"

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

    async def test_a_stale_cache_entry_is_refetched(self, api):
        """The other half of the pair: an entry past `cache_duration` must not
        be served, or the charts freeze for the life of the process."""
        session = _answers_with(api, _FakeResponse(200, text=_chart()))
        await api.get_itunes_top_podcasts(country_code="fr")
        key, (_stamp, value) = next(iter(api._discovery_cache.items()))
        api._discovery_cache[key] = (
            datetime.now() - api.cache_duration - timedelta(seconds=1), value
        )

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


class TestCacheEviction:
    def test_the_cache_stops_growing_at_its_declared_ceiling(self, api):
        """Each entry holds a whole chart or back-catalogue. Unbounded, this is
        the appliance's memory over a long uptime — and the appliance is not
        restarted between listens."""
        for i in range(api.MAX_CACHE_ENTRIES + 10):
            api._set_cache(api._discovery_cache, f"k{i}", {"results": []})

        assert len(api._discovery_cache) <= api.MAX_CACHE_ENTRIES + 1

    def test_the_oldest_entry_is_the_one_dropped(self, api):
        """Dropping the newest instead would evict what the owner is looking at
        right now and keep what they have finished with.

        The iteration order of a dict is insertion order, so `min` returning
        the first key proves nothing on its own — the stamps are set so the
        oldest sits *last* in insertion order and only the comparison can find
        it."""
        for i in range(api.MAX_CACHE_ENTRIES):
            api._set_cache(api._discovery_cache, f"k{i}", {"results": []})
        api._discovery_cache["k5"] = (
            datetime.now() - timedelta(days=1), {"results": ["oldest"]}
        )

        api._set_cache(api._discovery_cache, "fresh", {"results": []})

        assert "k5" not in api._discovery_cache
        assert "k0" in api._discovery_cache


class TestResolvingAnAppleId:
    async def test_a_known_feed_resolves_to_its_podcast_index_id(self, api):
        _answers_with(api, _FakeResponse(200, payload={
            "status": "true", "feed": {"id": 920666, "title": "Radiolab"}
        }))

        assert await api.lookup_by_itunes_id("152249110") == "920666"

    async def test_a_feed_podcast_index_does_not_index_answers_none(self, api):
        """Podcast Index answers 200 with an empty `feed` for an Apple id it
        has never crawled; the caller turns None into the 404 that stops the
        details page opening."""
        _answers_with(api, _FakeResponse(200, payload={"status": "true", "feed": []}))

        assert await api.lookup_by_itunes_id("152249110") is None

    async def test_a_feed_without_an_id_answers_none(self, api):
        """Measured note: the `feed.get("id")` half of the guard is inert for
        *this* case. `lookup_by_itunes_id` is `@handle_errors(default=None)`, so
        removing the check turns the missing key into a KeyError the decorator
        answers None to — the same answer. Only a feed carrying a falsy id
        would separate the two, and that is a shape nobody has captured from
        Podcast Index, so no test invents one (11th blind spot). What is worth
        asserting is the answer itself: a dict with no id must not become a
        feedId. Family B1-11 / B7-13."""
        _answers_with(api, _FakeResponse(200, payload={
            "status": "true", "feed": {"title": "Radiolab"}
        }))

        assert await api.lookup_by_itunes_id("152249110") is None

    async def test_an_apple_id_that_is_not_a_number_never_reaches_the_network(
        self, api
    ):
        """The id comes from a chart entry, i.e. from Apple; a non-numeric one
        means the chart parse drifted, and sending it upstream turns a local
        bug into an upstream error nobody attributes correctly."""
        session = _answers_with(api, _FakeResponse(200, payload={}))

        assert await api.lookup_by_itunes_id("not-a-number") is None
        session.get.assert_not_called()

    async def test_an_upstream_failure_answers_none(self, api):
        _answers_with(api, _FakeResponse(503, text="down"))

        assert await api.lookup_by_itunes_id("152249110") is None


class TestSeriesAndEpisodeCaching:
    SERIES_OK = {
        "status": "true",
        "feed": {"id": 920666, "title": "Radiolab", "artwork": "http://img/rl.png"},
    }
    EPISODES_OK = {
        "status": "true",
        "items": [{"id": 1, "title": "One", "enclosureUrl": "http://cdn/1.mp3"}],
    }

    async def test_a_second_open_of_the_same_series_hits_no_network(self, api):
        """Opening a podcast is two parallel upstream calls, one of which pulls
        the whole back-catalogue; paging its episode list must not repeat them."""
        session = _answers_with(
            api,
            _FakeResponse(200, payload=self.SERIES_OK),
            _FakeResponse(200, payload=self.EPISODES_OK),
        )

        first = await api.get_podcast_series("920666")
        await api.get_podcast_series("920666", episodes_page=2)

        assert first["name"] == "Radiolab"
        assert session.get.call_count == 2

    async def test_a_series_whose_feed_call_failed_is_not_cached(self, api):
        """Caching a failure would leave the podcast unopenable for the whole
        cache window, with a 404 the owner cannot clear."""
        _answers_with(api, _FakeResponse(503, text="down"))

        assert await api.get_podcast_series("920666") is None
        assert api._series_cache == {}

    async def test_a_series_answer_with_no_feed_id_is_not_a_series(self, api):
        _answers_with(
            api,
            _FakeResponse(200, payload={"status": "true", "feed": {"title": "X"}}),
            _FakeResponse(200, payload=self.EPISODES_OK),
        )

        assert await api.get_podcast_series("920666") is None

    async def test_a_cached_episode_is_handed_out_as_a_copy(self, api):
        """The route enriches the returned dict in place with
        `playback_progress`. Handing out the cached object would leave one
        listener's playhead on every later reader's copy."""
        _answers_with(api, _FakeResponse(200, payload={
            "status": "true",
            "episode": {"id": 16795089, "title": "One", "enclosureUrl": "http://c/1.mp3"},
        }))

        first = await api.get_episode("16795089")
        first["playback_progress"] = {"position": 42}
        second = await api.get_episode("16795089")

        assert "playback_progress" not in second

    async def test_an_episode_answer_with_no_id_is_not_an_episode(self, api):
        _answers_with(api, _FakeResponse(
            200, payload={"status": "true", "episode": {"title": "One"}}
        ))

        assert await api.get_episode("16795089") is None

    async def test_an_upstream_failure_leaves_the_episode_cache_empty(self, api):
        _answers_with(api, _FakeResponse(503, text="down"))

        assert await api.get_episode("16795089") is None
        assert api._episode_cache == {}
