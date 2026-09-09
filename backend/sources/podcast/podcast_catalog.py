"""
Milō's podcast catalogue: Apple for discovery, the publisher's feed for content.

Apple's iTunes RSS charts give the exact Apple Podcasts top lists per country
and genre, and the iTunes Search API backs term search — Podcast Index's own
/search/byterm cannot reach titles with glued punctuation ("Underscore_" is
unreachable from "underscore"). Both hand over an `itunes_id` and nothing else.

Everything past discovery — a podcast's details, its episode list, one episode's
audio URL — is read from the RSS feed its publisher serves, resolved by
`feed_resolver` and parsed by `rss_parser`. Podcast Index used to sit in that
position and is gone: it built its own `itunesId` index by matching the feed URL
Apple publishes, so for the publishers who withhold that URL (Radio France, FIP,
franceinfo) it had no mapping at all and answered "no feeds match this itunes
id" — 77 of the 454 podcasts in the French charts, measured 2026-09-09. Asking
Apple directly and reading the feed removes the middleman and the failure with
it. It is also what every comparable client does: AntennaPod, podgrab, Poddr,
kima-hub and Anytime all use a directory for discovery only.

`uuid` is therefore the `itunes_id` for a series, and `{itunes_id}:{guid hash}`
for an episode — see `rss_parser`. Nothing is resolved lazily on open any more.

Failures are one fact here, internally and outward. `_search_itunes` and
`_fetch_itunes_top` return the `_upstream_error` sentinel whenever a call did
not usefully answer — a network failure, a non-200 status, or an envelope that
carries none of what was asked — and the discovery methods turn that into the
outward `api_error` key the UI reads. One answer, because the user's screen has
one thing to say either way: the catalogue is down. Splitting them is what once
let an HTTP 503 read as "no results".

`api_error` is deliberately not a claim about the link — that one is reported at
the source level by full_state.network_unavailable.
"""
import asyncio
import contextlib
import json
import aiohttp
import logging
from math import ceil
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from urllib.parse import urlencode

from backend.shared.network import describe_network_error, is_network_error
from backend.sources.podcast.feed_resolver import CatalogUnavailable, FeedUrlResolver
from backend.sources.podcast.rss_parser import parse_feed, split_episode_id


# Map Milō genre keys (frontend genre grid/filters) to iTunes RSS genre IDs
GENRE_TO_ITUNES_ID = {
    'PODCASTSERIES_NEWS': 1489,
    'PODCASTSERIES_COMEDY': 1303,
    'PODCASTSERIES_TRUE_CRIME': 1488,
    'PODCASTSERIES_TECHNOLOGY': 1318,
    'PODCASTSERIES_SPORTS': 1545,
    'PODCASTSERIES_EDUCATION': 1304,
    'PODCASTSERIES_BUSINESS': 1321,
    'PODCASTSERIES_HEALTH_AND_FITNESS': 1512,
    'PODCASTSERIES_ARTS': 1301,
    'PODCASTSERIES_KIDS_AND_FAMILY': 1305,
    'PODCASTSERIES_MUSIC': 1310,
    'PODCASTSERIES_RELIGION_AND_SPIRITUALITY': 1314,
    'PODCASTSERIES_SCIENCE': 1533,
    'PODCASTSERIES_SOCIETY_AND_CULTURE': 1324,
    'PODCASTSERIES_TV_AND_FILM': 1309,
}

# Map Milō languages to iTunes RSS country codes (for Apple Podcasts charts)
MILO_LANGUAGE_TO_ITUNES_COUNTRY = {
    'english': 'us',      # United States
    'french': 'fr',       # France
    'spanish': 'es',      # Spain (or 'mx' for Mexico)
    'german': 'de',       # Germany
    'italian': 'it',      # Italy
    'portuguese': 'br',   # Brazil (or 'pt' for Portugal)
    'chinese': 'cn',      # China
    'hindi': 'in',        # India
}


def map_milo_language_to_itunes_country(milo_language: str) -> str:
    """
    Convert Milo language setting to iTunes RSS country code

    Args:
        milo_language: Language from /var/lib/milo/settings.json (e.g., 'french')

    Returns:
        iTunes country code (e.g., 'fr' for France)
    """
    return MILO_LANGUAGE_TO_ITUNES_COUNTRY.get(milo_language.lower(), 'us')


# The one thing a failed upstream call returns. One value rather than two,
# because the callers below all treat "no usable answer" the same way and the
# single place that used to tell them apart — the outward `api_error` flag —
# is exactly where the distinction was wrong: an HTTP 503 from the catalogue
# left the UI saying "no results" instead of "catalogue unavailable".
UPSTREAM_ERROR_KEY = "_upstream_error"


def _upstream_error() -> Dict[str, Any]:
    """A fresh sentinel per call — never a shared dict, callers enrich theirs."""
    return {UPSTREAM_ERROR_KEY: True}


def _failed(data: Optional[Dict[str, Any]]) -> bool:
    """Whether an upstream call produced nothing usable."""
    return not data or bool(data.get(UPSTREAM_ERROR_KEY))


def is_upstream_error(data: Optional[Dict[str, Any]]) -> bool:
    """Whether an answer is a *transient* upstream failure.

    The distinction routes depend on: a podcast Apple publishes no feed for is
    absent for good and the screen says so, while a publisher CDN answering 503
    is a passing outage. Conflating them is what turns an outage into a
    permanent "not available", which is the same bug — in the other direction —
    that made an HTTP 503 read as "no results" before `_upstream_error` existed.
    """
    return bool(data) and bool(data.get(UPSTREAM_ERROR_KEY))


class PodcastCatalog:
    """
    Async catalogue client: Apple for discovery, RSS feeds for content.

    A series `uuid` is its `itunes_id`; an episode `uuid` is
    `{itunes_id}:{guid hash}`. Neither needs resolving through a third party,
    so opening a chart entry costs no extra round-trip.

    Feeds are cached whole (`_feed_cache`) rather than per page: an RSS feed is
    indivisible, and the same bytes serve the series view, the episode list and
    the audio URL playback needs. Publishers do not honour conditional requests
    — Radio France sends no ETag, and audiomeans and audion answer 200 with the
    full body on If-None-Match — so a cache miss is always a full download.
    """

    MAX_CACHE_ENTRIES = 200
    ITUNES_SEARCH_MAX = 100    # iTunes Search hits fetched per term, sliced client-side
    LATEST_EPISODES_MAX_FEEDS = 100  # subscriptions merged into "latest episodes"
    LATEST_EPISODES_CONCURRENCY = 8   # simultaneous feed downloads in that fan-out

    def __init__(self, cache_duration_minutes: int = 120):
        self.logger = logging.getLogger("source.podcast.podcast_catalog")
        self.session: Optional[aiohttp.ClientSession] = None
        self.cache_duration = timedelta(minutes=cache_duration_minutes)
        self.resolver = FeedUrlResolver(cache_duration_minutes)

        # Caches
        self._search_cache: Dict[str, tuple[datetime, Any]] = {}
        self._feed_cache: Dict[str, tuple[datetime, Any]] = {}
        self._discovery_cache: Dict[str, tuple[datetime, Any]] = {}

    async def _ensure_session(self) -> None:
        """Create the aiohttp session if needed, and lend it to the resolver.

        One session, one connection pool: the resolver talks to the same two
        Apple hosts this client already holds keep-alive connections to, and a
        second pool would open its own for no gain.
        """
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                headers={'User-Agent': 'Milo/1.0'}
            )
        self.resolver.session = self.session

    def _check_cache(self, cache: Dict, key: str) -> Optional[Any]:
        """Check if cached data is still valid"""
        if key in cache:
            cached_time, cached_data = cache[key]
            if datetime.now() - cached_time < self.cache_duration:
                return cached_data
        return None

    def _set_cache(self, cache: Dict, key: str, data: Any) -> None:
        """Store data in cache, evicting oldest entries if over limit."""
        cache[key] = (datetime.now(), data)
        if len(cache) > self.MAX_CACHE_ENTRIES:
            oldest_key = min(cache, key=lambda k: cache[k][0])
            del cache[oldest_key]

    # ========== DISCOVERY (iTunes RSS — exact Apple Podcasts charts) ==========

    async def get_itunes_top_podcasts(
        self,
        country_code: str,
        limit: int = 25
    ) -> Dict[str, Any]:
        """
        Get the overall Apple Podcasts top charts for a country (no genre).

        Returns:
            Dict with 'results' list of podcasts, each carrying its `uuid`
            (the Apple id) ready to open.
        """
        limit = min(limit, 200)
        cache_key = f"itunes_top_all_{country_code}_{limit}"
        url = f"https://itunes.apple.com/{country_code}/rss/toppodcasts/limit={limit}/json"
        return await self._fetch_itunes_top(cache_key, url)

    async def get_itunes_top_podcasts_by_genre(
        self,
        genre: str,
        country_code: str,
        limit: int = 10
    ) -> Dict[str, Any]:
        """
        Get top podcasts from iTunes RSS feed for a specific genre

        Returns the EXACT Apple Podcasts top charts for the specified country and genre.
        This is the most reliable way to get the same results as shown on podcasts.apple.com

        Args:
            genre: Milō genre key (e.g., 'PODCASTSERIES_TECHNOLOGY')
            country_code: iTunes country code (e.g., 'fr' for France)
            limit: Number of results (max 200)

        Returns:
            Dict with 'results' list of podcast series, each carrying its `uuid`
        """
        limit = min(limit, 200)

        itunes_genre_id = GENRE_TO_ITUNES_ID.get(genre)
        if not itunes_genre_id:
            self.logger.info(f"Unknown genre for iTunes mapping: {genre}")
            return {"results": [], "total": 0}

        cache_key = f"itunes_top_{genre}_{country_code}_{limit}"
        url = (
            f"https://itunes.apple.com/{country_code}/rss/toppodcasts/"
            f"genre={itunes_genre_id}/limit={limit}/json"
        )
        return await self._fetch_itunes_top(cache_key, url)

    async def _fetch_itunes_top(self, cache_key: str, url: str) -> Dict[str, Any]:
        """Fetch + parse an iTunes RSS top-podcasts feed (shared by the
        genre and no-genre variants)."""
        cached = self._check_cache(self._discovery_cache, cache_key)
        if cached:
            return cached

        await self._ensure_session()

        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    self.logger.error(f"iTunes RSS error: HTTP {resp.status}")
                    return {"results": [], "total": 0, "api_error": True}

                # iTunes returns text/javascript instead of application/json
                data = json.loads(await resp.text())
                entries = data.get('feed', {}).get('entry', [])
                # iTunes RSS returns `entry` as a bare object (not a list) when
                # the chart holds exactly one entry — normalize to a list so the
                # loop below doesn't iterate dict keys and crash.
                if isinstance(entries, dict):
                    entries = [entries]

                results = []
                for entry in entries:
                    # Extract iTunes data
                    itunes_id = entry.get('id', {}).get('attributes', {}).get('im:id')
                    name = entry.get('im:name', {}).get('label', '')
                    artist = entry.get('im:artist', {}).get('label', '')

                    # Get image URL (take the largest one, upscale via Apple URL)
                    images = entry.get('im:image', [])
                    image_url = images[-1].get('label', '') if images else ''
                    if '170x170bb' in image_url:
                        image_url = image_url.replace('170x170bb', '600x600bb')

                    results.append({
                        'itunes_id': itunes_id,
                        # The Apple id IS the series uuid: opening a chart entry
                        # needs no resolution step, and none can fail.
                        'uuid': itunes_id,
                        'name': name,
                        'artist': artist,
                        'publisher': artist,
                        'image_url': image_url,
                        'source': 'itunes_rss',
                    })

                result = {"results": results, "total": len(results)}
                self._set_cache(self._discovery_cache, cache_key, result)
                return result

        except Exception as e:
            if is_network_error(e):
                self.logger.error(f"Network error fetching iTunes top podcasts: {e}")
            else:
                self.logger.error(f"Error fetching iTunes top podcasts: {e}")
            return {"results": [], "total": 0, "api_error": True}

    # ========== SEARCH ==========

    async def search_podcasts(
        self,
        term: str,
        page: int = 1,
        limit: int = 25,
        country: str = "us",
    ) -> Dict[str, Any]:
        """
        Search for podcasts by term (feeds-only — there is no cross-podcast
        episode search).

        Backed by the Apple iTunes Search API, whose index reaches titles with
        glued punctuation that a tokenized directory cannot ("Underscore_" is
        unreachable from "underscore"). Fetches ITUNES_SEARCH_MAX hits once per
        (country, term) (cached), then slices pages client-side.
        """
        limit = max(1, min(limit, 25))
        page = max(1, page)

        cache_key = f"search_{country}_{term}"
        data = self._check_cache(self._search_cache, cache_key)
        if data is None:
            data = await self._search_itunes(term, country)
            if _failed(data):
                return {
                    "podcasts": [],
                    "pagination": {"podcasts": {"total": 0, "pages": 0}},
                    "api_error": True,
                }
            self._set_cache(self._search_cache, cache_key, data)

        results = data.get("results") or []
        start = (page - 1) * limit
        podcasts = results[start:start + limit]

        return {
            "podcasts": podcasts,
            "pagination": {
                "podcasts": {"total": len(results), "pages": ceil(len(results) / limit)}
            },
        }

    async def _search_itunes(
        self, term: str, country: str
    ) -> Dict[str, Any]:
        """
        Query the Apple iTunes Search API for podcasts matching `term`.

        Returns {"results": [...normalized...]} on success (possibly empty), or
        the `_upstream_error` sentinel on any failure — Apple answering 503 and
        Apple being unreachable both mean the same to the search screen.
        """
        await self._ensure_session()
        url = "https://itunes.apple.com/search?" + urlencode({
            "term": term,
            "media": "podcast",
            "entity": "podcast",
            "limit": self.ITUNES_SEARCH_MAX,
            "country": country,
        })
        try:
            async with self.session.get(
                url, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    self.logger.error(f"iTunes Search error: HTTP {resp.status}")
                    return _upstream_error()

                # iTunes returns text/javascript instead of application/json
                data = json.loads(await resp.text())
                results = [
                    self._normalize_itunes_search(r)
                    for r in (data.get("results") or [])
                    if r.get("collectionId")
                ]
                return {"results": results}

        except Exception as e:
            if is_network_error(e):
                self.logger.error(f"Network error searching iTunes: {e}")
            else:
                self.logger.error(f"Error searching iTunes: {e}")
            return _upstream_error()

    def _normalize_itunes_search(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize an iTunes Search API podcast result to Milō keys.

        Same shape as the iTunes-RSS charts entries, so search results and
        charts render and open through one path.
        """
        image_url = result.get("artworkUrl600") or result.get("artworkUrl100") or ""
        if "100x100bb" in image_url:
            image_url = image_url.replace("100x100bb", "600x600bb")
        artist = result.get("artistName") or ""
        itunes_id = str(result.get("collectionId"))
        return {
            "itunes_id": itunes_id,
            "uuid": itunes_id,
            "name": result.get("collectionName") or "Unknown Podcast",
            "artist": artist,
            "publisher": artist,
            "image_url": image_url,
            "total_episodes": result.get("trackCount") or 0,
            "source": "itunes_search",
        }

    # ========== CONTENT (the publisher's own feed) ==========

    async def get_podcast_series(
        self,
        itunes_id: str,
        episodes_page: int = 1,
        episodes_limit: int = 25,
        sort_order: str = "LATEST",
        country: str = "us",
    ) -> Optional[Dict[str, Any]]:
        """Podcast details plus one page of its episodes, from its RSS feed.

        None means Apple publishes no feed for this id — a subscriber-only
        show, or an id that names nothing. That is an answer, not a failure:
        the caller tells the user the podcast is unavailable.
        """
        series = await self._feed(itunes_id, country)
        if _failed(series):
            # None (no feed published) or the sentinel (could not be read):
            # both travel out untouched so the route can answer differently.
            return series

        episodes = series["episodes"]
        if sort_order == "OLDEST":
            episodes = list(reversed(episodes))

        start = (episodes_page - 1) * episodes_limit
        page = dict(series)
        page["episodes"] = [
            dict(ep) for ep in episodes[start:start + episodes_limit]
        ]
        # The whole feed is held, so this is the real count — what "load more"
        # needs, and what a publisher's own episode-count metadata is not.
        page["total_episodes"] = len(episodes)
        return page

    async def get_episode(
        self, episode_id: str, country: str = "us"
    ) -> Optional[Dict[str, Any]]:
        """One episode, addressed by `{itunes_id}:{guid hash}`.

        The feed is fetched whole because the audio URL lives in it; finding
        the episode inside costs the walk and nothing more.
        """
        itunes_id, digest = split_episode_id(episode_id)
        if not itunes_id:
            self.logger.debug("Not an episode identifier: %r", episode_id)
            return None

        series = await self._feed(itunes_id, country)
        if _failed(series):
            return series

        episode = next(
            (ep for ep in series["episodes"] if ep["uuid"] == episode_id), None
        )
        # A copy, never the cached object: the route enriches what it gets in
        # place with `playback_progress`, and handing out the cached episode
        # would leave one listener's playhead on every later reader's copy.
        return dict(episode) if episode else None

    async def get_latest_episodes(
        self,
        itunes_ids: List[str],
        page: int = 1,
        limit: int = 50,
        country: str = "us",
    ) -> Dict[str, Any]:
        """Newest episodes across every subscription, merged and paginated.

        One fetch per subscribed feed, in parallel. There is no way to ask for
        only the newest few: an RSS feed is indivisible, and publishers do not
        answer conditional requests, so each cache miss downloads whole feeds.
        The 120-minute cache is what keeps that off the critical path.
        """
        if not itunes_ids:
            return {"results": [], "total": 0}

        limit = min(limit, 50)
        page = max(1, min(page, 20))
        itunes_ids = itunes_ids[:self.LATEST_EPISODES_MAX_FEEDS]

        # Resolve the whole set in one batch first: Apple's lookup endpoint
        # takes 200 comma-separated ids per call, so a cold subscriptions
        # screen costs one request here instead of one per subscription. The
        # resolver caches what it answers, so the fan-out below finds them.
        await self._ensure_session()
        with contextlib.suppress(CatalogUnavailable):
            # Best effort: a failure here is re-met per feed below, which is
            # where it becomes the sentinel the outage message reads.
            await self.resolver.resolve_many(itunes_ids, country)

        # Bounded fan-out: a feed is downloaded whole, so 100 subscriptions
        # unbounded is 100 simultaneous multi-megabyte transfers competing for
        # one appliance's link and SD card.
        gate = asyncio.Semaphore(self.LATEST_EPISODES_CONCURRENCY)

        async def _one(itunes_id: str) -> Optional[Dict[str, Any]]:
            async with gate:
                return await self._feed(itunes_id, country)

        feeds = await asyncio.gather(*(_one(i) for i in itunes_ids))

        episodes: List[Dict[str, Any]] = []
        for series in feeds:
            if not _failed(series):
                episodes.extend(dict(ep) for ep in series["episodes"])

        # Only when nothing came back at all, and only for a *transient*
        # failure: several subscriptions that Apple simply publishes no feed
        # for is not a catalogue outage, and must not offer a retry that can
        # never succeed.
        if not episodes and any(is_upstream_error(series) for series in feeds):
            return {"results": [], "total": 0, "api_error": True}

        episodes.sort(key=lambda e: e.get("date_published") or 0, reverse=True)
        start = (page - 1) * limit
        page_episodes = episodes[start:start + limit]
        return {"results": page_episodes, "total": len(page_episodes)}

    # ========== FEED ACCESS ==========

    async def _feed(
        self, itunes_id: str, country: str
    ) -> Optional[Dict[str, Any]]:
        """Resolve, fetch and parse one feed. Cached whole, by iTunes id.

        Three outcomes, and callers must keep them apart:
        the series; `None` when Apple publishes no feed for this id, which is
        permanent and is what the "not available" notice reports; and the
        `_upstream_error` sentinel when the feed exists but could not be read
        this time — a CDN 503, a timeout, a body that is not a feed. Returning
        `None` for that last case is what would turn a passing outage into a
        podcast the owner believes is gone.
        """
        cached = self._check_cache(self._feed_cache, str(itunes_id))
        if cached:
            return cached

        await self._ensure_session()
        try:
            feed_url = await self.resolver.resolve(itunes_id, country)
        except CatalogUnavailable as exc:
            self.logger.info("Could not resolve feed for %s: %s", itunes_id, exc)
            return _upstream_error()
        if not feed_url:
            return None

        raw = await self._fetch_feed(feed_url)
        if raw is None:
            return _upstream_error()

        # ElementTree is synchronous and a large feed is not cheap — 59 ms for a
        # 1.6 MB / 244-episode feed on this hardware. Off the loop, or a cold
        # subscriptions screen stalls every other client for a second.
        series = await asyncio.to_thread(
            parse_feed, raw, str(itunes_id), feed_url
        )
        if series is None:
            self.logger.error("Feed at %s did not parse as a podcast", feed_url)
            return _upstream_error()

        self._set_cache(self._feed_cache, str(itunes_id), series)
        return series

    async def _fetch_feed(self, feed_url: str) -> Optional[bytes]:
        """Download a feed. Bytes, not text: the XML declaration owns the
        encoding, and decoding before the parser sees it loses that."""
        await self._ensure_session()
        try:
            async with self.session.get(
                feed_url, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status != 200:
                    self.logger.error(
                        "Feed %s answered HTTP %s", feed_url, resp.status
                    )
                    return None
                return await resp.read()
        except Exception as exc:
            if is_network_error(exc):
                self.logger.info(
                    "Network error fetching feed %s: %s",
                    feed_url, describe_network_error(exc),
                )
            else:
                self.logger.error("Error fetching feed %s: %s", feed_url, exc)
            return None
