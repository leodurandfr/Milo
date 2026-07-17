"""
Podcast Index API client for podcast search and retrieval.

API doc: https://podcastindex-org.github.io/docs-api/
Endpoint: https://api.podcastindex.org/api/1.0

Auth is a SHA-1 of api_key + api_secret + unix_time, recomputed for every
request (3-minute validity window). The key pair is app-level (embedded in
backend/config/constants.py) — free and unlimited, no per-user credentials.

Apple Podcasts top charts (exact ordering) come from the keyless iTunes RSS
API, and term search from the keyless iTunes Search API — both live here too.
Podcast Index's own /search/byterm can't match titles with glued punctuation
(e.g. "Underscore_" tokenizes whole and is unreachable from "underscore"), so
search is backed by Apple's better-tokenized index and resolved to a Podcast
Index feedId lazily on open (same path the charts already use).
"""
import asyncio
import hashlib
import html
import re
import time
import aiohttp
import logging
from math import ceil
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from urllib.parse import urlencode

from backend.shared.decorators import handle_errors
from backend.shared.network import is_network_error


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


class PodcastIndexAPI:
    """
    Async client for the Podcast Index REST API.

    IDs are Podcast Index feedId (podcast) / episode id (episode), exposed as
    opaque strings under the normalized `uuid` key. Podcast Index has no
    server-side pagination (`max` + `since` cursor only), so list endpoints
    fetch a large `max` once, cache it, and paginate client-side.
    """

    BASE_URL = "https://api.podcastindex.org/api/1.0"
    MAX_CACHE_ENTRIES = 200
    ITUNES_SEARCH_MAX = 100    # iTunes Search hits fetched per term, sliced client-side
    EPISODES_FETCH_MAX = 1000  # PI hard max for /episodes/byfeedid

    def __init__(self, api_key: str, api_secret: str, cache_duration_minutes: int = 120):
        self.logger = logging.getLogger("source.podcast.podcastindex_api")
        self.api_key = api_key
        self.api_secret = api_secret
        self.session: Optional[aiohttp.ClientSession] = None
        self.cache_duration = timedelta(minutes=cache_duration_minutes)

        # Caches
        self._search_cache: Dict[str, tuple[datetime, Any]] = {}
        self._series_cache: Dict[str, tuple[datetime, Any]] = {}
        self._episode_cache: Dict[str, tuple[datetime, Any]] = {}
        self._discovery_cache: Dict[str, tuple[datetime, Any]] = {}

    async def _ensure_session(self) -> None:
        """Creates aiohttp session if needed"""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                headers={'User-Agent': 'Milo/1.0'}
            )

    async def close(self) -> None:
        """Closes aiohttp session"""
        if self.session and not self.session.closed:
            await self.session.close()
            self.session = None

    def _auth_headers(self) -> Dict[str, str]:
        """Per-request auth headers: SHA-1(key + secret + epoch), 3-min window."""
        now = str(int(time.time()))
        auth = hashlib.sha1(
            f"{self.api_key}{self.api_secret}{now}".encode("utf-8")
        ).hexdigest()
        return {
            "X-Auth-Key": self.api_key,
            "X-Auth-Date": now,
            "Authorization": auth,
        }

    async def _make_request(
        self, path: str, params: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """GET a Podcast Index endpoint. Returns the JSON envelope, the
        `{"_network_error": True}` sentinel, or None on API errors."""
        await self._ensure_session()

        try:
            async with self.session.get(
                f"{self.BASE_URL}{path}",
                params=params,
                headers=self._auth_headers(),
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    self.logger.error(
                        f"Podcast Index error: HTTP {resp.status} on {path} - {error_text[:300]}"
                    )
                    return None

                data = await resp.json()

                # Envelope status is the string "true"/"false"
                if str(data.get("status")).lower() == "false":
                    self.logger.error(
                        f"Podcast Index error on {path}: {data.get('description', 'unknown')}"
                    )
                    return None

                return data

        except Exception as e:
            if is_network_error(e):
                self.logger.info(f"Podcast Index network error: {e}")
                return {"_network_error": True}
            self.logger.error(f"Podcast Index unexpected error: {e}")
            return None

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
            Dict with 'results' list of podcasts with iTunes data (uuid=None
            until resolved via lookup_by_itunes_id)
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
            Dict with 'results' list of podcast series with iTunes data (without UUIDs yet)
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
                    return {"results": [], "total": 0}

                # iTunes returns text/javascript instead of application/json
                text = await resp.text()
                import json as json_module
                data = json_module.loads(text)
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
                        'name': name,
                        'artist': artist,
                        'publisher': artist,
                        'image_url': image_url,
                        'source': 'itunes_rss',
                        # UUID (Podcast Index feedId) is resolved on demand
                        # via lookup_by_itunes_id when the podcast is opened
                        'uuid': None,
                    })

                result = {"results": results, "total": len(results)}
                self._set_cache(self._discovery_cache, cache_key, result)
                return result

        except Exception as e:
            if is_network_error(e):
                self.logger.error(f"Network error fetching iTunes top podcasts: {e}")
                return {"results": [], "total": 0, "network_error": True}
            self.logger.error(f"Error fetching iTunes top podcasts: {e}")
            return {"results": [], "total": 0}

    @handle_errors(default=None)
    async def lookup_by_itunes_id(self, itunes_id: str) -> Optional[str]:
        """
        Resolve a Podcast Index feedId for an iTunes top-charts entry.

        Podcast Index indexes feeds by iTunes ID natively, so the direct
        lookup is authoritative — no name/author fallback needed. Returns
        None when the podcast isn't in the index (caller shows "not
        available").
        """
        try:
            itunes_id_int = int(itunes_id)
        except (TypeError, ValueError):
            self.logger.warning(f"Invalid iTunes ID for lookup: {itunes_id!r}")
            return None

        data = await self._make_request("/podcasts/byitunesid", {"id": itunes_id_int})
        if not data or data.get("_network_error"):
            return None

        feed = data.get("feed")
        if isinstance(feed, dict) and feed.get("id"):
            self.logger.debug(f"Resolved iTunes ID {itunes_id} -> feedId {feed['id']}")
            return str(feed["id"])

        self.logger.debug(f"No Podcast Index feed for iTunes ID {itunes_id}")
        return None

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

        Backed by the Apple iTunes Search API rather than Podcast Index's own
        /search/byterm, whose index can't reach titles with glued punctuation
        (e.g. "Underscore_" from "underscore"). Each hit carries only an
        itunes_id; the Podcast Index feedId is resolved lazily via
        lookup_by_itunes_id when the podcast is opened — the same path the
        iTunes-RSS charts use. Fetches ITUNES_SEARCH_MAX hits once per
        (country, term) (cached), then slices pages client-side.
        """
        limit = max(1, min(limit, 25))
        page = max(1, page)

        cache_key = f"search_{country}_{term}"
        data = self._check_cache(self._search_cache, cache_key)
        if data is None:
            data = await self._search_itunes(term, country)
            if data and data.get("_network_error"):
                return {
                    "podcasts": [],
                    "pagination": {"podcasts": {"total": 0, "pages": 0}},
                    "network_error": True,
                }
            if not data:
                return {"podcasts": [], "pagination": {"podcasts": {"total": 0, "pages": 0}}}
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
    ) -> Optional[Dict[str, Any]]:
        """
        Query the Apple iTunes Search API for podcasts matching `term`.

        Returns {"results": [...normalized...]} on success (possibly empty),
        {"_network_error": True} on a network failure, or None on any other
        error — mirroring the sentinels search_podcasts already handles.
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
                    return None

                # iTunes returns text/javascript instead of application/json
                text = await resp.text()
                import json as json_module
                data = json_module.loads(text)
                results = [
                    self._normalize_itunes_search(r)
                    for r in (data.get("results") or [])
                    if r.get("collectionId")
                ]
                return {"results": results}

        except Exception as e:
            if is_network_error(e):
                self.logger.error(f"Network error searching iTunes: {e}")
                return {"_network_error": True}
            self.logger.error(f"Error searching iTunes: {e}")
            return None

    def _normalize_itunes_search(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize an iTunes Search API podcast result to Milō keys.

        Same shape as the iTunes-RSS charts entries (uuid=None until resolved
        from itunes_id on open) so search results and charts render and open
        through one path.
        """
        image_url = result.get("artworkUrl600") or result.get("artworkUrl100") or ""
        if "100x100bb" in image_url:
            image_url = image_url.replace("100x100bb", "600x600bb")
        artist = result.get("artistName") or ""
        return {
            "itunes_id": str(result.get("collectionId")),
            "uuid": None,
            "name": result.get("collectionName") or "Unknown Podcast",
            "artist": artist,
            "publisher": artist,
            "image_url": image_url,
            "total_episodes": result.get("trackCount") or 0,
            "source": "itunes_search",
        }

    # ========== CONTENT ==========

    async def get_podcast_series(
        self,
        feed_id: str,
        episodes_page: int = 1,
        episodes_limit: int = 25,
        sort_order: str = "LATEST"
    ) -> Optional[Dict[str, Any]]:
        """
        Get podcast series details with a page of episodes.

        Two parallel calls: /podcasts/byfeedid + /episodes/byfeedid (full
        back-catalogue, cached), then client-side sort/pagination.

        Args:
            feed_id: Podcast Index feedId (stringified)
            episodes_page: Page number for episodes
            episodes_limit: Episodes per page (max 25)
            sort_order: LATEST (PI native order) or OLDEST (client reverse)
        """
        cached = self._check_cache(self._series_cache, str(feed_id))
        if cached:
            feed_data, episodes_data = cached
        else:
            feed_data, episodes_data = await asyncio.gather(
                self._make_request("/podcasts/byfeedid", {"id": feed_id}),
                self._make_request(
                    "/episodes/byfeedid", {"id": feed_id, "max": self.EPISODES_FETCH_MAX}
                ),
            )
            if (
                not feed_data or feed_data.get("_network_error")
                or not episodes_data or episodes_data.get("_network_error")
            ):
                return None
            self._set_cache(self._series_cache, str(feed_id), (feed_data, episodes_data))

        feed = feed_data.get("feed")
        if not isinstance(feed, dict) or not feed.get("id"):
            return None

        normalized = self._normalize_podcast_series(feed)

        items = episodes_data.get("items") or []
        if sort_order == "OLDEST":
            # PI returns newest-first; reversing yields true oldest-first only
            # when we hold the whole catalogue (len(items) < EPISODES_FETCH_MAX).
            # For larger feeds this reverses the newest EPISODES_FETCH_MAX only —
            # true oldest needs `since`-cursor paging (deferred, same cap as below).
            items = list(reversed(items))

        start = (episodes_page - 1) * episodes_limit
        normalized["episodes"] = [
            self._normalize_episode(
                ep,
                podcast_name=normalized["name"],
                podcast_image=normalized["image_url"],
                podcast_uuid=normalized["uuid"],
            )
            for ep in items[start:start + episodes_limit]
        ]
        # total_episodes drives the frontend "load more" cutoff, so it must be
        # exactly what we can serve: the fetched count. Below EPISODES_FETCH_MAX
        # we hold the whole catalogue (feed episodeCount metadata can lag OR
        # lead, so it's unreliable); at the cap we deliberately expose only the
        # fetched slice so the cutoff terminates instead of chasing episodes
        # past the cap. Full >EPISODES_FETCH_MAX coverage needs cursor paging.
        normalized["total_episodes"] = len(items)

        return normalized

    async def get_episode(self, episode_id: str) -> Optional[Dict[str, Any]]:
        """Get episode details (by Podcast Index episode id)."""
        cached = self._check_cache(self._episode_cache, str(episode_id))
        if cached:
            # Shallow copy: callers (routes) enrich the returned dict in place
            # with playback_progress — that must not mutate the cached object.
            return dict(cached)

        data = await self._make_request("/episodes/byid", {"id": episode_id})
        if not data or data.get("_network_error"):
            return None

        episode = data.get("episode")
        if not isinstance(episode, dict) or not episode.get("id"):
            return None

        normalized = self._normalize_episode(episode)
        self._set_cache(self._episode_cache, str(episode_id), normalized)
        return dict(normalized)

    async def get_latest_episodes(
        self,
        feed_ids: List[str],
        page: int = 1,
        limit: int = 50,
        feed_meta: Optional[Dict[str, Dict[str, str]]] = None
    ) -> Dict[str, Any]:
        """
        Get latest episodes from multiple podcasts (for subscriptions).

        No batch endpoint on Podcast Index: N parallel /episodes/byfeedid
        calls (free + unlimited), merged and sorted by publish date.

        Args:
            feed_ids: List of Podcast Index feedIds (stringified)
            page: Page number (1-20)
            limit: Episodes per merged page (max 50)
            feed_meta: Optional {feed_id: {name, image_url}} fallbacks for the
                episode's podcast block (/episodes/byfeedid items may omit
                feedTitle)
        """
        if not feed_ids:
            return {"results": [], "total": 0}

        limit = min(limit, 50)
        page = max(1, min(page, 20))
        feed_ids = feed_ids[:1000]
        # Each feed could theoretically fill every merged page on its own
        per_feed = min(page * limit, 100)

        responses = await asyncio.gather(
            *(
                self._make_request("/episodes/byfeedid", {"id": fid, "max": per_feed})
                for fid in feed_ids
            )
        )

        episodes = []
        network_error = False
        for fid, data in zip(feed_ids, responses):
            if not data:
                continue
            if data.get("_network_error"):
                network_error = True
                continue
            meta = (feed_meta or {}).get(str(fid), {})
            for item in data.get("items") or []:
                episodes.append(
                    self._normalize_episode(
                        item,
                        podcast_name=meta.get("name"),
                        podcast_image=meta.get("image_url"),
                        podcast_uuid=str(fid),
                    )
                )

        if network_error and not episodes:
            return {"results": [], "total": 0, "network_error": True}

        episodes.sort(key=lambda e: e.get("date_published") or 0, reverse=True)
        start = (page - 1) * limit
        page_episodes = episodes[start:start + limit]
        return {"results": page_episodes, "total": len(page_episodes)}

    def clear_cache(self) -> None:
        """Clear all caches"""
        self._search_cache.clear()
        self._series_cache.clear()
        self._episode_cache.clear()
        self._discovery_cache.clear()
        self.logger.info("Cache cleared")

    # ========== NORMALIZATION ==========

    @staticmethod
    def _strip_html(text: Optional[str]) -> str:
        """Strip HTML tags/entities from PI descriptions — the frontend
        renders them as plain text."""
        if not text:
            return ''
        return html.unescape(re.sub(r'<[^>]+>', '', text)).strip()

    def _normalize_podcast_series(self, feed: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize a podcast feed from Podcast Index format to Milō keys."""
        return {
            'uuid': str(feed.get('id')),
            'itunes_id': feed.get('itunesId'),
            'name': feed.get('title') or 'Unknown Podcast',
            'description': self._strip_html(feed.get('description')),
            'image_url': feed.get('artwork') or feed.get('image') or '',
            'publisher': feed.get('author') or feed.get('ownerName') or '',
            'author': feed.get('author') or '',
            'total_episodes': feed.get('episodeCount') or 0,
            'genres': list((feed.get('categories') or {}).values()),
            'language': feed.get('language') or '',
            'is_explicit': bool(feed.get('explicit', False)),
            # "New episodes" token: any feed update bumps lastUpdateTime
            'children_hash': str(
                feed.get('lastUpdateTime') or feed.get('newestItemPubdate') or ''
            ),
            'website_url': feed.get('link') or '',
            'rss_url': feed.get('url') or '',
        }

    def _coerce_duration_seconds(self, episode: Dict[str, Any]) -> int:
        """Return episode duration in seconds, coercing milliseconds when needed.

        Podcast Index documents duration as Int seconds (nullable). A small
        subset of upstream RSS feeds publish the value in milliseconds and it
        gets forwarded unchanged. Any duration above 24h is therefore treated
        as ms and divided by 1000; a warning is logged so the offending feed
        can be identified.
        """
        raw = episode.get('duration', 0)
        if not isinstance(raw, (int, float)) or raw <= 0:
            return 0
        if raw > 86_400:
            self.logger.warning(
                "Episode %s reports duration=%s > 24h, treating as milliseconds",
                episode.get('id'), raw,
            )
            return int(raw / 1000)
        return int(raw)

    def _normalize_episode(
        self,
        episode: Dict[str, Any],
        podcast_name: str = None,
        podcast_image: str = None,
        podcast_uuid: str = None
    ) -> Dict[str, Any]:
        """Normalize an episode from Podcast Index format to Milō keys.

        The podcast_* fallbacks cover /episodes/byfeedid items, which omit
        feedTitle (only /episodes/byid guarantees it).
        """
        image_url = (
            episode.get('image') or episode.get('feedImage') or podcast_image or ''
        )

        normalized = {
            'uuid': str(episode.get('id')),
            'guid': episode.get('guid') or '',
            'name': episode.get('title') or 'Unknown Episode',
            'description': self._strip_html(episode.get('description')),
            'date_published': episode.get('datePublished'),
            'duration': self._coerce_duration_seconds(episode),
            'audio_url': episode.get('enclosureUrl'),
            'image_url': image_url,
            'episode_type': episode.get('episodeType') or 'full',
            'season_number': episode.get('season'),
            'episode_number': episode.get('episode'),
            # PI episode explicit is 0/1 (feed explicit is a boolean)
            'is_explicit': bool(episode.get('explicit', 0)),
            'website_url': episode.get('link') or '',
            'file_length': episode.get('enclosureLength') or 0,
            'file_type': episode.get('enclosureType') or '',
        }

        feed_id = episode.get('feedId')
        if feed_id or podcast_uuid or podcast_name:
            normalized['podcast'] = {
                'uuid': str(feed_id) if feed_id else (podcast_uuid or ''),
                'name': episode.get('feedTitle') or podcast_name or 'Unknown Podcast',
                'image_url': episode.get('feedImage') or podcast_image or '',
            }

        return normalized
