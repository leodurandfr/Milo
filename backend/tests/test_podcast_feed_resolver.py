# backend/tests/test_podcast_feed_resolver.py
"""
`FeedUrlResolver` — the join that replaced the Podcast Index round-trip.

Every episode Milō plays comes from the feed URL resolved here, so a resolver
that answers the wrong URL plays the wrong podcast, and one that answers None
too readily puts back the `No podcast found for iTunes ID` the rewrite removed.

The outside world is Apple over HTTP, doubled here by routing on the requested
URL rather than on call order — the two tiers must be provably ordered, and a
positional double would pass whichever way round they ran.

The tier-2 anchoring test is the one that matters most: Apple's product page
carries the feed URL of every "you might also like" tile beside the podcast's
own, so an extractor that stops anchoring on the `adamId` starts returning a
neighbouring show's feed. That failure is silent — a valid feed, a real podcast,
the wrong one.
"""
from unittest.mock import MagicMock

import aiohttp
import pytest

from backend.sources.podcast.feed_resolver import CatalogUnavailable, FeedUrlResolver


FEED_URL = "https://radiofrance-podcast.net/podcast09/podcast_4c36a463.xml"
OTHER_FEED = "https://feeds.audiomeans.fr/feed/deadbeef.xml"


class _FakeResponse:
    def __init__(self, status=200, text=""):
        self.status = status
        self._text = text

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


def _routed(resolver, *, lookup=None, page=None):
    """Answer by endpoint, so the double never depends on call order."""
    calls = {"lookup": 0, "page": 0}

    def _get(url, **_kw):
        if "itunes.apple.com/lookup" in url:
            calls["lookup"] += 1
            return lookup if lookup is not None else _FakeResponse(200, "{}")
        if "podcasts.apple.com" in url:
            calls["page"] += 1
            return page if page is not None else _FakeResponse(404, "")
        raise AssertionError(f"unexpected request to {url}")

    session = MagicMock()
    session.closed = False
    session.get = MagicMock(side_effect=_get)
    resolver.session = session
    return calls


def _lookup(*results):
    import json
    return _FakeResponse(200, json.dumps({
        "resultCount": len(results), "results": list(results)
    }))


def _page(*offers):
    """A product page blob: the podcast's own offer plus its recommendations."""
    body = ",".join(
        f'{{"adamId":"{adam}","feedUrl":"{url}","title":"whatever"}}'
        for adam, url in offers
    )
    return _FakeResponse(200, f'<script id="serialized-server-data">[{body}]</script>')


@pytest.fixture
def resolver():
    return FeedUrlResolver()


class TestTheDocumentedApiComesFirst:
    """Tier 1 is Apple's supported endpoint; tier 2 is page markup. Reversing
    them would make every resolution depend on the fragile half."""

    async def test_a_published_feed_url_never_touches_the_product_page(self, resolver):
        calls = _routed(resolver, lookup=_lookup(
            {"collectionId": 1836947328, "feedUrl": FEED_URL}
        ))

        assert await resolver.resolve("1836947328") == FEED_URL
        assert calls["lookup"] == 1
        assert calls["page"] == 0

    async def test_apples_text_javascript_body_is_still_decoded(self, resolver):
        """The lookup endpoint answers `text/javascript`, so the resolver reads
        text() and decodes by hand. A switch to resp.json() would raise on the
        content type and resolve nothing."""
        _routed(resolver, lookup=_lookup(
            {"collectionId": 1836947328, "feedUrl": FEED_URL}
        ))

        assert await resolver.resolve("1836947328") == FEED_URL


class TestTheProductPageFallback:
    """Radio France, FIP and franceinfo withhold `feedUrl` from the lookup API
    while Apple's own page still carries it — 81 of the 454 French chart
    entries, measured 2026-09-09."""

    async def test_a_withheld_feed_url_is_recovered_from_the_page(self, resolver):
        calls = _routed(
            resolver,
            lookup=_lookup({"collectionId": 1836947328, "trackName": "Big Bang"}),
            page=_page(("1836947328", FEED_URL)),
        )

        assert await resolver.resolve("1836947328") == FEED_URL
        assert calls["page"] == 1

    async def test_only_the_offer_carrying_this_adam_id_is_read(self, resolver):
        """The page lists recommended shows with their own feedUrl. Anchoring on
        the adamId is the whole safety property: drop it and this returns
        OTHER_FEED — a real feed for a different podcast, with nothing to
        signal the substitution."""
        _routed(
            resolver,
            lookup=_lookup({"collectionId": 1836947328}),
            page=_page(
                ("9999999999", OTHER_FEED),
                ("1836947328", FEED_URL),
                ("8888888888", OTHER_FEED),
            ),
        )

        assert await resolver.resolve("1836947328") == FEED_URL

    async def test_a_page_without_this_podcasts_offer_resolves_to_nothing(self, resolver):
        """A subscriber-only show has no public feed. Answering a neighbour's
        URL would play the wrong podcast; answering None shows the notice."""
        _routed(
            resolver,
            lookup=_lookup({"collectionId": 1836947328}),
            page=_page(("9999999999", OTHER_FEED)),
        )

        assert await resolver.resolve("1836947328") is None

    async def test_json_escapes_in_the_page_blob_are_decoded(self, resolver):
        """The offer sits inside a JSON document, so the captured value can
        carry escapes. Left raw, the URL would 404 at fetch time."""
        escaped = FEED_URL.replace("/", r"\/")
        _routed(
            resolver,
            lookup=_lookup({"collectionId": 1836947328}),
            page=_page(("1836947328", escaped)),
        )

        assert await resolver.resolve("1836947328") == FEED_URL

    async def test_the_page_is_requested_with_a_browser_user_agent(self, resolver):
        """Apple serves the product page to browsers; the Milō agent gets a
        page with no serialized-server-data blob at all."""
        session_calls = []
        calls = _routed(
            resolver,
            lookup=_lookup({"collectionId": 1836947328}),
            page=_page(("1836947328", FEED_URL)),
        )
        original = resolver.session.get.side_effect
        resolver.session.get.side_effect = lambda url, **kw: (
            session_calls.append((url, kw)) or original(url, **kw)
        )

        await resolver.resolve("1836947328")

        page_headers = next(
            kw.get("headers") or {} for url, kw in session_calls
            if "podcasts.apple.com" in url
        )
        assert "Mozilla" in page_headers.get("User-Agent", "")
        assert calls["page"] == 1


class TestWhatMustNotBeCached:
    """A resolved URL is cached; a failed call is not. Caching a failure as
    'no feed' would keep a podcast unopenable until the process restarts."""

    async def test_a_resolved_url_is_served_from_cache(self, resolver):
        calls = _routed(resolver, lookup=_lookup(
            {"collectionId": 1836947328, "feedUrl": FEED_URL}
        ))

        assert await resolver.resolve("1836947328") == FEED_URL
        assert await resolver.resolve("1836947328") == FEED_URL
        assert calls["lookup"] == 1

    async def test_an_upstream_failure_does_not_poison_the_next_attempt(self, resolver):
        _routed(resolver, lookup=_FakeResponse(503, ""), page=_FakeResponse(503, ""))
        with pytest.raises(CatalogUnavailable):
            await resolver.resolve("1836947328")

        _routed(resolver, lookup=_lookup(
            {"collectionId": 1836947328, "feedUrl": FEED_URL}
        ))
        assert await resolver.resolve("1836947328") == FEED_URL


class TestAnOutageIsNotAnAbsence:
    """The distinction the whole error story rests on. Apple answering "no feed
    for this id" is permanent and the screen says the podcast is unavailable;
    Apple not answering is a passing outage. Collapse them into one `None` and
    a CDN hiccup tells the owner a podcast is gone."""

    async def test_a_network_error_is_an_outage_not_an_absence(self, resolver):
        session = MagicMock()
        session.closed = False
        session.get = MagicMock(side_effect=aiohttp.ClientOSError("no route"))
        resolver.session = session

        with pytest.raises(CatalogUnavailable):
            await resolver.resolve("1836947328")

    async def test_a_server_error_is_an_outage(self, resolver):
        _routed(resolver, lookup=_FakeResponse(503, ""))

        with pytest.raises(CatalogUnavailable):
            await resolver.resolve("1836947328")

    async def test_a_body_apple_did_not_encode_as_json_is_an_outage(self, resolver):
        """A rate-limit or captcha page arrives with HTTP 200 and a body that
        is not JSON. Read as an absence it would blank the podcast."""
        _routed(resolver, lookup=_FakeResponse(200, "<html>rate limited</html>"))

        with pytest.raises(CatalogUnavailable):
            await resolver.resolve("1836947328")

    async def test_a_product_page_that_does_not_exist_is_an_absence(self, resolver):
        """404 is Apple answering: this id names nothing. That one *is* None,
        and it is what the "not available" notice reports."""
        _routed(
            resolver,
            lookup=_lookup({"collectionId": 1836947328}),
            page=_FakeResponse(404, ""),
        )

        assert await resolver.resolve("1836947328") is None


class TestIdsThatCannotAddressAPodcast:
    async def test_a_non_numeric_id_costs_no_request(self, resolver):
        calls = _routed(resolver)

        assert await resolver.resolve("not-an-id") is None
        assert calls["lookup"] == 0
        assert calls["page"] == 0

    @pytest.mark.parametrize("bad", [None, "", "  ", "12a", "-5"])
    async def test_rejected_shapes(self, resolver, bad):
        _routed(resolver)
        assert await resolver.resolve(bad) is None


class TestResolvingAWholeChart:
    """`resolve_many` backs the chart screens: one batched lookup for the whole
    Top 30, then a page fetch only for the entries Apple withheld."""

    async def test_one_lookup_covers_the_batch(self, resolver):
        calls = _routed(resolver, lookup=_lookup(
            {"collectionId": 111, "feedUrl": FEED_URL},
            {"collectionId": 222, "feedUrl": OTHER_FEED},
        ))

        assert await resolver.resolve_many(["111", "222"]) == {
            "111": FEED_URL, "222": OTHER_FEED
        }
        assert calls["lookup"] == 1
        assert calls["page"] == 0

    async def test_only_the_withheld_entries_reach_the_page(self, resolver):
        calls = _routed(
            resolver,
            lookup=_lookup(
                {"collectionId": 111, "feedUrl": FEED_URL},
                {"collectionId": 222},
            ),
            page=_page(("222", OTHER_FEED)),
        )

        assert await resolver.resolve_many(["111", "222"]) == {
            "111": FEED_URL, "222": OTHER_FEED
        }
        assert calls["page"] == 1

    async def test_an_unresolvable_entry_is_reported_not_dropped(self, resolver):
        """The chart shows every entry it was given. A key silently missing from
        the mapping is how a caller starts rendering an empty tile."""
        _routed(
            resolver,
            lookup=_lookup({"collectionId": 111, "feedUrl": FEED_URL}),
            page=_page(("999", OTHER_FEED)),
        )

        assert await resolver.resolve_many(["111", "222"]) == {
            "111": FEED_URL, "222": None
        }

    async def test_cached_entries_are_not_looked_up_again(self, resolver):
        _routed(resolver, lookup=_lookup({"collectionId": 111, "feedUrl": FEED_URL}))
        await resolver.resolve("111")

        calls = _routed(resolver, lookup=_lookup({"collectionId": 222, "feedUrl": OTHER_FEED}))
        result = await resolver.resolve_many(["111", "222"])

        assert result == {"111": FEED_URL, "222": OTHER_FEED}
        assert calls["lookup"] == 1
