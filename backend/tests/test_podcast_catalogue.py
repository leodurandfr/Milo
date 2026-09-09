# backend/tests/test_podcast_catalogue.py
"""`podcast_catalog.py` — the client's Apple half, and its feed half.

The Apple half (charts, search, caches) is unchanged by the move off Podcast
Index and so is its coverage here. What it never ran is the other half of each
failure pair:

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

The feed half replaced Podcast Index entirely: `TestOpeningAPodcast` and
`TestFindingOneEpisode` below stand where the Podcast Index resolution and
content tests stood. The parsing itself is not retested here — that is
`test_podcast_rss_parser.py`; what these assert is what the *client* does with
it, which is caching, copying and failing.

The doubles are canned responses usable as the async context manager the client
opens, assigned as the *session* rather than by replacing a method of the unit
(`_ensure_session` only builds a session when there is none). The client holds
two sessions — its own and the resolver's — so both are pointed at the double.
"""
import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from backend.sources.podcast.podcast_catalog import (
    GENRE_TO_ITUNES_ID,
    PodcastCatalog,
    is_upstream_error,
)
from backend.sources.podcast.rss_parser import make_episode_id


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
        assert api._feed_cache == {}

    async def test_a_document_that_is_not_a_feed_is_an_outage(self, api):
        """A captcha or an error page arrives with HTTP 200. The feed exists;
        this attempt failed."""
        _answers_with(api, _lookup(), _FakeResponse(200, body=b"<html>nope</html>"))

        assert is_upstream_error(await api.get_podcast_series("152249110"))
        assert api._feed_cache == {}

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
        """A bare Podcast Index id, or junk. Sending it upstream turns a local
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
        assert api._feed_cache == {}


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
