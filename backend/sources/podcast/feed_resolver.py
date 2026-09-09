"""
Resolve an Apple podcast id to the RSS feed URL its publisher serves.

Apple's charts and search hand over an `itunes_id` and nothing else, while every
episode Milō plays comes from the publisher's own feed. This module is the join
between the two, and it is the whole reason the Podcast Index round-trip could
be dropped: Podcast Index built its own `itunesId` mapping by matching the very
`feedUrl` resolved here, so asking Apple directly removes a middleman that was
answering "no feeds match this itunes id" for 77 of the 454 podcasts in the
French charts.

Two tiers, official first:

1. ``/lookup?id=`` — Apple's documented API. Serves 373/454 in France, and
   essentially everything outside it.
2. The product page on podcasts.apple.com, read at the `adamId` that names the
   podcast. Serves the remaining 81.

Tier 2 exists because Radio France (plus FIP and franceinfo) withhold `feedUrl`
from the lookup API while Apple's own page still carries it. It is the fragile
half — page markup, not an API — so it is deliberately second and it matches on
identity: the `feedUrl` it returns is the one sitting next to *this* podcast's
`adamId`, never the nearest URL on the page. Should Apple change the page, this
tier stops answering and coverage falls back to tier 1, which is the behaviour
that shipped before — a degradation, never a wrong feed.

Coverage measured 2026-09-09: 454/454 across the French charts, 664/664 across
all eight Milō locales. Podcasts sold by subscription have no public feed and
are meant to resolve to nothing.
"""
import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import aiohttp

from backend.shared.network import describe_network_error, is_network_error


class CatalogUnavailable(Exception):
    """Apple did not answer — as opposed to answering that there is no feed.

    The two must not collapse into one `None`. A podcast Apple publishes no
    feed for is absent for good and the screen says so; Apple being unreachable
    is a passing outage, and reporting it as absence tells the owner a podcast
    is gone when the appliance simply could not ask.
    """


# Apple serves the product page to browsers only; the lookup API does not care.
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)


class FeedUrlResolver:
    """Async `itunes_id` → RSS feed URL resolver, with an in-memory cache.

    A resolved feed URL is cached for the same duration as the catalogue data
    that led to it: a publisher moving hosts is rare, and the miss costs one
    Apple round-trip.
    """

    LOOKUP_URL = "https://itunes.apple.com/lookup"
    PAGE_URL = "https://podcasts.apple.com/{country}/podcast/id{itunes_id}"
    MAX_CACHE_ENTRIES = 500

    # The page embeds the podcast's own offer as
    # {"adamId":"<id>","feedUrl":"<url>", …}. Anchoring on the id is what makes
    # this an identity match rather than a guess: a page also carries the feeds
    # of every "you might also like" tile.
    _PAGE_FEED_URL = (
        r'"adamId"\s*:\s*"{itunes_id}"\s*,\s*"feedUrl"\s*:\s*"([^"]+)"'
    )

    def __init__(self, cache_duration_minutes: int = 120):
        self.logger = logging.getLogger("source.podcast.feed_resolver")
        self.session: Optional[aiohttp.ClientSession] = None
        self.cache_duration = timedelta(minutes=cache_duration_minutes)
        self._cache: Dict[str, tuple[datetime, str]] = {}

    async def _ensure_session(self) -> None:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(headers={"User-Agent": "Milo/1.0"})

    # ========== CACHE ==========

    def _cached(self, itunes_id: str) -> Optional[str]:
        entry = self._cache.get(itunes_id)
        if entry and datetime.now() - entry[0] < self.cache_duration:
            return entry[1]
        return None

    def _store(self, itunes_id: str, feed_url: str) -> None:
        self._cache[itunes_id] = (datetime.now(), feed_url)
        if len(self._cache) > self.MAX_CACHE_ENTRIES:
            oldest = min(self._cache, key=lambda k: self._cache[k][0])
            del self._cache[oldest]

    # ========== RESOLUTION ==========

    async def resolve(self, itunes_id: str, country: str = "us") -> Optional[str]:
        """Return the podcast's RSS feed URL, or None when Apple publishes none.

        None is an expected answer — a subscriber-only show has no public feed —
        so callers report it to the user, they do not treat it as a failure.
        Apple not answering at all raises `CatalogUnavailable` instead, which is
        the whole point of the distinction.
        """
        itunes_id = self._normalize_id(itunes_id)
        if not itunes_id:
            return None

        cached = self._cached(itunes_id)
        if cached:
            return cached

        feed_url = await self._from_lookup_api(itunes_id)
        if not feed_url:
            feed_url = await self._from_product_page(itunes_id, country)

        if feed_url:
            self._store(itunes_id, feed_url)
        else:
            # Expected for subscriber-only shows: not a failure, so it must not
            # reach the WebSocketLogHandler banner.
            self.logger.debug("Apple publishes no feed URL for iTunes id %s", itunes_id)
        return feed_url

    async def resolve_many(
        self, itunes_ids: list[str], country: str = "us"
    ) -> Dict[str, Optional[str]]:
        """Resolve several ids concurrently, keyed by the id passed in.

        The lookup API takes up to 200 comma-separated ids per call, so the
        common case costs one request for the whole batch and one product-page
        fetch per podcast Apple withheld.
        """
        normalized = [self._normalize_id(i) for i in itunes_ids]
        wanted = [i for i in normalized if i]
        if not wanted:
            return {i: None for i in itunes_ids}

        resolved: Dict[str, Optional[str]] = {
            i: self._cached(i) for i in wanted
        }
        missing = [i for i in wanted if not resolved[i]]

        for chunk in (missing[i:i + 200] for i in range(0, len(missing), 200)):
            resolved.update(await self._from_lookup_api_batch(chunk))

        still_missing = [i for i in wanted if not resolved.get(i)]
        if still_missing:
            pages = await asyncio.gather(
                *(self._from_product_page(i, country) for i in still_missing)
            )
            resolved.update(dict(zip(still_missing, pages)))

        # Only what this call resolved: re-storing a cache hit would refresh
        # its timestamp, and a set touched more often than the TTL would never
        # expire — a publisher moving hosts would then need a restart to be
        # picked up.
        for i in missing:
            if resolved.get(i):
                self._store(i, resolved[i])

        return {
            original: resolved.get(self._normalize_id(original))
            for original in itunes_ids
        }

    # ========== TIER 1: the documented lookup API ==========

    async def _from_lookup_api(self, itunes_id: str) -> Optional[str]:
        return (await self._from_lookup_api_batch([itunes_id])).get(itunes_id)

    async def _from_lookup_api_batch(
        self, itunes_ids: list[str]
    ) -> Dict[str, Optional[str]]:
        """Read `feedUrl` for a batch of ids. Empty dict when the call failed.

        A failed call and a podcast Apple has no feed for are different news:
        the first must not be cached as "no feed", so it answers with nothing
        rather than with None per id.
        """
        payload = await self._get_json(
            self.LOOKUP_URL,
            params={"id": ",".join(itunes_ids), "entity": "podcast"},
        )
        if payload is None:
            return {}

        out: Dict[str, Optional[str]] = {}
        for result in payload.get("results") or []:
            collection_id = result.get("collectionId")
            feed_url = result.get("feedUrl")
            if collection_id and feed_url:
                out[str(collection_id)] = feed_url
        return out

    # ========== TIER 2: the product page ==========

    async def _from_product_page(
        self, itunes_id: str, country: str
    ) -> Optional[str]:
        """Read `feedUrl` off the product page, anchored on the podcast's adamId."""
        html = await self._get_text(
            self.PAGE_URL.format(country=country, itunes_id=itunes_id),
            headers={
                "User-Agent": _BROWSER_USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        if not html:
            return None

        match = re.search(
            self._PAGE_FEED_URL.format(itunes_id=re.escape(itunes_id)), html
        )
        if not match:
            return None

        # The blob is JSON, so the captured value still carries JSON escapes.
        try:
            return json.loads(f'"{match.group(1)}"')
        except ValueError:
            return match.group(1)

    # ========== HTTP ==========

    async def _get_json(
        self, url: str, params: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """GET a JSON document. None on any failure.

        The lookup API answers `text/javascript`, so the body is decoded by
        hand rather than through `resp.json()`, which enforces the content type.
        """
        text = await self._get_text(url, params=params)
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError as exc:
            self.logger.error("Apple lookup returned malformed JSON: %s", exc)
            raise CatalogUnavailable("malformed JSON from Apple") from exc

    async def _get_text(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Optional[str]:
        """GET a text document.

        None means Apple answered and the resource is not there (a 404 on a
        product page: the id names nothing). Anything that means Apple did not
        answer — a network failure, a 5xx, a rate limit — raises
        `CatalogUnavailable`, so the caller never reads an outage as an absence.
        """
        await self._ensure_session()
        try:
            async with self.session.get(
                url,
                params=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 404:
                    return None
                if resp.status != 200:
                    self.logger.warning(
                        "Apple answered HTTP %s for %s", resp.status, url
                    )
                    raise CatalogUnavailable(f"HTTP {resp.status} from Apple")
                return await resp.text()
        except CatalogUnavailable:
            raise
        except Exception as exc:
            if is_network_error(exc):
                self.logger.info(
                    "Network error resolving feed URL: %s", describe_network_error(exc)
                )
            else:
                self.logger.error("Unexpected error resolving feed URL: %s", exc)
            raise CatalogUnavailable(str(exc) or type(exc).__name__) from exc

    # ========== HELPERS ==========

    @staticmethod
    def _normalize_id(itunes_id: Any) -> Optional[str]:
        """Apple ids are digits. Anything else cannot address a podcast."""
        text = str(itunes_id).strip() if itunes_id is not None else ""
        return text if text.isdigit() else None
