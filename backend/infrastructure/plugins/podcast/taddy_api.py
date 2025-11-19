"""
Taddy API client for podcast search and retrieval
Complete implementation with all GraphQL queries
"""
import asyncio
import aiohttp
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta


# Mapping des genres Taddy vers les IDs de genre iTunes RSS
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
    'PODCASTSERIES_SCIENCE': 1315,
    'PODCASTSERIES_SOCIETY_AND_CULTURE': 1324,
    'PODCASTSERIES_TV_AND_FILM': 1309,
}

# Mapping des langues Milo (settings.json) vers les enums Taddy
MILO_LANGUAGE_TO_TADDY = {
    'english': 'ENGLISH',
    'french': 'FRENCH',
    'spanish': 'SPANISH',
    'german': 'GERMAN',
    'italian': 'ITALIAN',
    'portuguese': 'PORTUGUESE',
    'chinese': 'CHINESE',
    'hindi': 'HINDI',
}

# Mapping des langues Milo vers codes pays iTunes RSS (pour Apple Podcasts charts)
MILO_LANGUAGE_TO_ITUNES_COUNTRY = {
    'english': 'us',      # United States
    'french': 'fr',       # France
    'spanish': 'es',      # Spain (ou 'mx' pour Mexico)
    'german': 'de',       # Germany
    'italian': 'it',      # Italy
    'portuguese': 'br',   # Brazil (ou 'pt' pour Portugal)
    'chinese': 'cn',      # China
    'hindi': 'in',        # India
}


def map_milo_language_to_taddy(milo_language: str) -> str:
    """
    Convert Milo language setting to Taddy enum

    Args:
        milo_language: Language from /var/lib/milo/settings.json (e.g., 'french')

    Returns:
        Taddy language enum (e.g., 'FRENCH')
    """
    return MILO_LANGUAGE_TO_TADDY.get(milo_language.lower(), 'ENGLISH')


def map_milo_language_to_itunes_country(milo_language: str) -> str:
    """
    Convert Milo language setting to iTunes RSS country code

    Args:
        milo_language: Language from /var/lib/milo/settings.json (e.g., 'french')

    Returns:
        iTunes country code (e.g., 'fr' for France)
    """
    return MILO_LANGUAGE_TO_ITUNES_COUNTRY.get(milo_language.lower(), 'us')


class TaddyAPI:
    """
    Async client for Taddy GraphQL API

    API Doc: https://taddy.org/developers
    Endpoint: https://api.taddy.org
    """

    BASE_URL = "https://api.taddy.org"

    def __init__(self, user_id: str, api_key: str, cache_duration_minutes: int = 60):
        self.logger = logging.getLogger(__name__)
        self.user_id = user_id
        self.api_key = api_key
        self.session: Optional[aiohttp.ClientSession] = None
        self.cache_duration = timedelta(minutes=cache_duration_minutes)

        # Caches
        self._search_cache: Dict[str, tuple[datetime, Any]] = {}
        self._series_cache: Dict[str, tuple[datetime, Any]] = {}
        self._episode_cache: Dict[str, tuple[datetime, Any]] = {}
        self._discovery_cache: Dict[str, tuple[datetime, Any]] = {}
        self._itunes_lookup_cache: Dict[str, tuple[datetime, Optional[str]]] = {}  # {podcast_name: (timestamp, uuid)}

    async def _ensure_session(self) -> None:
        """Creates aiohttp session if needed"""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                headers={
                    'Content-Type': 'application/json',
                    'X-USER-ID': self.user_id,
                    'X-API-KEY': self.api_key,
                }
            )

    async def close(self) -> None:
        """Closes aiohttp session"""
        if self.session and not self.session.closed:
            await self.session.close()
            self.session = None

    async def _make_graphql_request(self, query: str) -> Optional[Dict[str, Any]]:
        """Makes a GraphQL request to Taddy API"""
        await self._ensure_session()

        try:
            payload = {"query": query}

            self.logger.debug(f"GraphQL request: {query[:500]}...")

            async with self.session.post(
                self.BASE_URL,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 429:
                    self.logger.warning("Taddy API rate limit exceeded")
                    return None

                if resp.status != 200:
                    error_text = await resp.text()
                    self.logger.error(f"Taddy API error: HTTP {resp.status} - {error_text[:500]}")
                    return None

                data = await resp.json()

                if "errors" in data:
                    error_messages = [err.get('message', str(err)) for err in data['errors']]
                    self.logger.error(f"GraphQL errors: {error_messages}")
                    return None

                return data.get("data")

        except asyncio.TimeoutError:
            self.logger.error("Timeout calling Taddy API")
            return None
        except Exception as e:
            self.logger.error(f"Error calling Taddy API: {e}")
            return None

    def _check_cache(self, cache: Dict, key: str) -> Optional[Any]:
        """Check if cached data is still valid"""
        if key in cache:
            cached_time, cached_data = cache[key]
            if datetime.now() - cached_time < self.cache_duration:
                return cached_data
        return None

    def _set_cache(self, cache: Dict, key: str, data: Any) -> None:
        """Store data in cache"""
        cache[key] = (datetime.now(), data)

    # ========== DISCOVERY QUERIES ==========

    async def get_popular_content(
        self,
        language: str = None,
        genres: List[str] = None,
        page: int = 1,
        limit: int = 25
    ) -> Dict[str, Any]:
        """
        Get popular podcasts globally

        Args:
            language: Filter by language (e.g., "FRENCH")
            genres: Filter by genres list
            page: Page number (1-20)
            limit: Results per page (max 25)
        """
        limit = min(limit, 25)
        page = max(1, min(page, 20))

        cache_key = f"popular_{language}_{genres}_{page}_{limit}"
        cached = self._check_cache(self._discovery_cache, cache_key)
        if cached:
            return cached

        # Build filters
        filters = []
        if language:
            filters.append(f'filterByLanguage: {language}')
        if genres:
            genres_str = ', '.join(genres)
            filters.append(f'filterByGenres: [{genres_str}]')

        filters_str = '\n    '.join(filters)
        if filters_str:
            filters_str = '\n    ' + filters_str

        query = f"""
        {{
          getPopularContent(
            taddyType: PODCASTSERIES
            page: {page}
            limitPerPage: {limit}{filters_str}
          ) {{
            popularityRankId
            podcastSeries {{
              uuid
              itunesId
              name
              description(shouldStripHtmlTags: true)
              imageUrl
              totalEpisodesCount
              genres
              language
              popularityRank
              isExplicitContent
              seriesType
              isCompleted
              itunesInfo {{
                uuid
                baseArtworkUrlOf(size: 300)
                publisherName
              }}
            }}
          }}
        }}
        """

        data = await self._make_graphql_request(query)
        if not data or "getPopularContent" not in data:
            return {"results": [], "total": 0}

        popular_data = data["getPopularContent"]
        self.logger.debug(f"popular_data type: {type(popular_data)}, content: {str(popular_data)[:200]}")
        results = []

        for series in popular_data.get("podcastSeries", []):
            results.append(self._normalize_podcast_series(series))

        result = {"results": results, "total": len(results)}
        self._set_cache(self._discovery_cache, cache_key, result)
        return result

    async def get_top_charts_by_country(
        self,
        country: str,
        content_type: str = "PODCASTSERIES",
        page: int = 1,
        limit: int = 25
    ) -> Dict[str, Any]:
        """
        Get top charts by country

        Args:
            country: Country enum (e.g., "FRANCE")
            content_type: PODCASTSERIES or PODCASTEPISODE
            page: Page number (1-20)
            limit: Results per page (max 25)
        """
        limit = min(limit, 25)
        page = max(1, min(page, 20))

        cache_key = f"charts_{country}_{content_type}_{page}_{limit}"
        cached = self._check_cache(self._discovery_cache, cache_key)
        if cached:
            return cached

        if content_type == "PODCASTSERIES":
            fields = """
              podcastSeries {
                uuid
                name
                imageUrl
                popularityRank
                genres
                totalEpisodesCount
                language
                seriesType
                isCompleted
                isExplicitContent
                itunesInfo {
                    uuid
                  baseArtworkUrlOf(size: 300)
                  publisherName
                }
              }
            """
        else:
            fields = """
              podcastEpisodes {
                uuid
                name
                description(shouldStripHtmlTags: true)
                duration
                audioUrl
                imageUrl
                datePublished
                episodeType
                podcastSeries {
                  uuid
                  name
                  imageUrl
                }
              }
            """

        query = f"""
        {{
          getTopChartsByCountry(
            taddyType: {content_type}
            country: {country}
            source: APPLE_PODCASTS
            page: {page}
            limitPerPage: {limit}
          ) {{
            topChartsId
            {fields}
          }}
        }}
        """

        data = await self._make_graphql_request(query)
        if not data or "getTopChartsByCountry" not in data:
            return {"results": [], "total": 0}

        charts_data = data["getTopChartsByCountry"]
        results = []

        if content_type == "PODCASTSERIES":
            for series in charts_data.get("podcastSeries", []):
                results.append(self._normalize_podcast_series(series))
        else:
            for episode in charts_data.get("podcastEpisodes", []):
                results.append(self._normalize_episode(episode))

        result = {"results": results, "total": len(results)}
        self._set_cache(self._discovery_cache, cache_key, result)
        return result

    async def get_top_charts_by_genres(
        self,
        genres: List[str],
        country: str = None,
        content_type: str = "PODCASTSERIES",
        page: int = 1,
        limit: int = 25
    ) -> Dict[str, Any]:
        """
        Get top charts by genres

        Args:
            genres: List of genre enums
            country: Optional country filter
            content_type: PODCASTSERIES or PODCASTEPISODE
            page: Page number (1-20)
            limit: Results per page (max 25)
        """
        limit = min(limit, 25)
        page = max(1, min(page, 20))

        cache_key = f"genres_{genres}_{country}_{content_type}_{page}_{limit}"
        cached = self._check_cache(self._discovery_cache, cache_key)
        if cached:
            return cached

        genres_str = ', '.join(genres)
        # Only add country filter if provided (for PODCASTEPISODE only, per Taddy docs)
        country_filter = f'filterByCountry: {country},' if country else ''

        if content_type == "PODCASTSERIES":
            fields = """
              podcastSeries {
                uuid
                name
                imageUrl
                genres
                popularityRank
                totalEpisodesCount
                language
                itunesInfo {
                    uuid
                  baseArtworkUrlOf(size: 300)
                  publisherName
                }
              }
            """
        else:
            fields = """
              podcastEpisodes {
                uuid
                name
                duration
                audioUrl
                imageUrl
                datePublished
                podcastSeries {
                  uuid
                  name
                  imageUrl
                }
              }
            """

        query = f"""
        {{
          getTopChartsByGenres(
            taddyType: {content_type}
            genres: [{genres_str}]
            source: APPLE_PODCASTS
            {country_filter}
            page: {page}
            limitPerPage: {limit}
          ) {{
            topChartsId
            {fields}
          }}
        }}
        """

        data = await self._make_graphql_request(query)
        if not data or "getTopChartsByGenres" not in data:
            return {"results": [], "total": 0}

        genres_data = data["getTopChartsByGenres"]
        results = []

        if content_type == "PODCASTSERIES":
            for series in genres_data.get("podcastSeries", []):
                results.append(self._normalize_podcast_series(series))
        else:
            for episode in genres_data.get("podcastEpisodes", []):
                results.append(self._normalize_episode(episode))

        result = {"results": results, "total": len(results)}
        self._set_cache(self._discovery_cache, cache_key, result)
        return result

    async def get_itunes_top_podcasts_by_genre(
        self,
        genre: str,
        country_code: str = "fr",
        limit: int = 10
    ) -> Dict[str, Any]:
        """
        Get top podcasts from iTunes RSS feed for a specific genre

        Returns the EXACT Apple Podcasts top charts for the specified country and genre.
        This is the most reliable way to get the same results as shown on podcasts.apple.com

        Args:
            genre: Taddy genre enum (e.g., 'PODCASTSERIES_TECHNOLOGY')
            country_code: iTunes country code (default: 'fr' for France)
            limit: Number of results (max 200)

        Returns:
            Dict with 'results' list of podcast series with iTunes data (without UUIDs yet)
        """
        limit = min(limit, 200)

        # Get iTunes genre ID from mapping
        itunes_genre_id = GENRE_TO_ITUNES_ID.get(genre)
        if not itunes_genre_id:
            self.logger.warning(f"Unknown genre for iTunes mapping: {genre}")
            return {"results": [], "total": 0}

        cache_key = f"itunes_top_{genre}_{country_code}_{limit}"
        cached = self._check_cache(self._discovery_cache, cache_key)
        if cached:
            return cached

        # Ensure session exists
        await self._ensure_session()

        # Fetch from iTunes RSS API
        url = f"https://itunes.apple.com/{country_code}/rss/toppodcasts/genre={itunes_genre_id}/limit={limit}/json"

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

                results = []
                for entry in entries:
                    # Extract iTunes data
                    itunes_id = entry.get('id', {}).get('attributes', {}).get('im:id')
                    name = entry.get('im:name', {}).get('label', '')
                    artist = entry.get('im:artist', {}).get('label', '')

                    # Get image URL (take the largest one)
                    images = entry.get('im:image', [])
                    image_url = images[-1].get('label', '') if images else ''

                    results.append({
                        'itunes_id': itunes_id,
                        'name': name,
                        'artist': artist,
                        'publisher': artist,
                        'image_url': image_url,
                        'source': 'itunes_rss',
                        # UUID will be added during enrichment
                        'uuid': None,
                    })

                result = {"results": results, "total": len(results)}
                self._set_cache(self._discovery_cache, cache_key, result)
                return result

        except Exception as e:
            self.logger.error(f"Error fetching iTunes RSS: {e}")
            return {"results": [], "total": 0}

    async def lookup_podcast_uuid_by_itunes_id(self, itunes_id: str, podcast_name: str = None) -> Optional[str]:
        """
        Lookup Taddy UUID for a podcast using its iTunes ID

        Args:
            itunes_id: iTunes ID from iTunes RSS
            podcast_name: Optional podcast name for fallback search

        Returns:
            Taddy UUID if found, None otherwise
        """
        try:
            # Search by podcast name (simple and effective)
            if podcast_name:
                # Limit to first 8 words (Taddy API limitation)
                words = podcast_name.split()
                search_term = ' '.join(words[:8])

                result = await self.search_mixed(
                    term=search_term,
                    sort_by="EXACTNESS",
                    limit=5
                )

                podcasts = result.get('podcasts', [])

                # Strategy 1: Match by iTunes ID (most reliable)
                if itunes_id:
                    itunes_id_str = str(itunes_id)
                    for podcast in podcasts:
                        taddy_itunes_id = podcast.get('itunes_id')
                        if taddy_itunes_id and str(taddy_itunes_id) == itunes_id_str:
                            uuid = podcast.get('uuid')
                            self.logger.debug(f"Found UUID {uuid} for iTunes ID {itunes_id}")
                            return uuid

                # Strategy 2: Match by name (fallback)
                podcast_name_lower = podcast_name.lower().strip()
                for podcast in podcasts:
                    taddy_name_lower = podcast.get('name', '').lower().strip()
                    if taddy_name_lower == podcast_name_lower:
                        uuid = podcast.get('uuid')
                        self.logger.debug(f"Found UUID {uuid} for name '{podcast_name}'")
                        return uuid

            self.logger.debug(f"No UUID found for iTunes ID {itunes_id} / name '{podcast_name}'")
            return None

        except Exception as e:
            self.logger.error(f"Error looking up podcast UUID: {e}")
            return None

    # ========== SEARCH QUERIES ==========

    async def search_mixed(
        self,
        term: str,
        genres: List[str] = None,
        languages: List[str] = None,
        countries: List[str] = None,
        duration_min: int = None,
        duration_max: int = None,
        safe_mode: bool = False,
        sort_by: str = "EXACTNESS",
        page: int = 1,
        limit: int = 25
    ) -> Dict[str, Any]:
        """
        Search for both podcasts AND episodes in a single query

        Returns:
            Dict with 'podcasts' and 'episodes' lists, plus pagination info
        """
        limit = min(limit, 25)
        page = max(1, min(page, 20))

        cache_key = f"mixed_{term}_{genres}_{languages}_{countries}_{duration_min}_{duration_max}_{safe_mode}_{sort_by}_{page}_{limit}"
        cached = self._check_cache(self._search_cache, cache_key)
        if cached:
            return cached

        # Build filters
        filters = []
        if genres:
            genres_str = ', '.join(genres)
            filters.append(f'filterForGenres: [{genres_str}]')
        if languages:
            languages_str = ', '.join(languages)
            filters.append(f'filterForLanguages: [{languages_str}]')
        if countries:
            countries_str = ', '.join(countries)
            filters.append(f'filterForCountries: [{countries_str}]')
        if duration_min is not None:
            filters.append(f'filterForDurationGreaterThan: {duration_min}')
        if duration_max is not None:
            filters.append(f'filterForDurationLessThan: {duration_max}')
        if safe_mode:
            filters.append('isSafeMode: true')

        filters_str = '\n        '.join(filters)
        if filters_str:
            filters_str = '\n        ' + filters_str

        # Escape term for GraphQL
        escaped_term = term.replace('"', '\\"')

        query = f"""
        {{
          search(
            term: "{escaped_term}"
            filterForTypes: [PODCASTSERIES, PODCASTEPISODE]
            sortBy: {sort_by}
            page: {page}
            limitPerPage: {limit}{filters_str}
          ) {{
            searchId
            podcastSeries {{
              uuid
              itunesId
              name
              description(shouldStripHtmlTags: true)
              imageUrl
              totalEpisodesCount
              genres
              language
              popularityRank
              isExplicitContent
              seriesType
              isCompleted
              itunesInfo {{
                uuid
                baseArtworkUrlOf(size: 300)
                publisherName
              }}
            }}
            podcastEpisodes {{
              uuid
              name
              description(shouldStripHtmlTags: true)
              datePublished
              duration
              audioUrl
              imageUrl
              episodeType
              podcastSeries {{
                uuid
                name
                imageUrl
              }}
            }}
            responseDetails {{
              id
              type
              totalCount
              pagesCount
            }}
          }}
        }}
        """

        data = await self._make_graphql_request(query)
        if not data or "search" not in data:
            return {
                "podcasts": [],
                "episodes": [],
                "pagination": {
                    "podcasts": {"total": 0, "pages": 0},
                    "episodes": {"total": 0, "pages": 0}
                }
            }

        search_data = data["search"]

        # Normalize results
        podcasts = [self._normalize_podcast_series(p) for p in search_data.get("podcastSeries", [])]
        episodes = [self._normalize_episode(e) for e in search_data.get("podcastEpisodes", [])]

        # Parse pagination info
        pagination = {
            "podcasts": {"total": 0, "pages": 0},
            "episodes": {"total": 0, "pages": 0}
        }
        for detail in search_data.get("responseDetails", []):
            if detail.get("type") == "PODCASTSERIES":
                pagination["podcasts"] = {
                    "total": detail.get("totalCount", 0),
                    "pages": detail.get("pagesCount", 0)
                }
            elif detail.get("type") == "PODCASTEPISODE":
                pagination["episodes"] = {
                    "total": detail.get("totalCount", 0),
                    "pages": detail.get("pagesCount", 0)
                }

        result = {
            "podcasts": podcasts,
            "episodes": episodes,
            "pagination": pagination
        }

        self._set_cache(self._search_cache, cache_key, result)
        return result

    async def search(
        self,
        term: str,
        filter_type: str = "PODCASTSERIES",
        sort_by: str = "EXACTNESS",
        limit: int = 25,
        page: int = 1,
        filter_country: str = None,
        filter_genre: str = None,
        filter_language: str = None
    ) -> Dict[str, Any]:
        """
        Search for podcasts or episodes (single type)
        """
        limit = min(limit, 25)
        page = max(1, min(page, 20))

        cache_key = f"{term}_{filter_type}_{sort_by}_{limit}_{page}_{filter_country}_{filter_genre}_{filter_language}"
        cached = self._check_cache(self._search_cache, cache_key)
        if cached:
            return cached

        # Build query
        if filter_type == "PODCASTSERIES":
            result_field = "podcastSeries"
            fields = """
                uuid
                name
                description(shouldStripHtmlTags: true)
                imageUrl
                totalEpisodesCount
                genres
                language
                popularityRank
                isExplicitContent
                seriesType
                isCompleted
                itunesInfo {
                    uuid
                    baseArtworkUrlOf(size: 300)
                    publisherName
                }
            """
        else:
            result_field = "podcastEpisodes"
            fields = """
                uuid
                name
                description(shouldStripHtmlTags: true)
                datePublished
                duration
                audioUrl
                imageUrl
                episodeType
                podcastSeries {
                    uuid
                    name
                    imageUrl
                }
            """

        filters = []
        if filter_country:
            filters.append(f'filterForCountries: [{filter_country}]')
        if filter_genre:
            filters.append(f'filterForGenres: [{filter_genre}]')
        if filter_language:
            filters.append(f'filterForLanguages: [{filter_language}]')

        filters_str = '\n                '.join(filters)
        if filters_str:
            filters_str = '\n                ' + filters_str

        escaped_term = term.replace('"', '\\"')

        query = f"""
        {{
            search(
                term: "{escaped_term}"
                filterForTypes: [{filter_type}]
                sortBy: {sort_by}
                page: {page}
                limitPerPage: {limit}{filters_str}
            ) {{
                searchId
                {result_field} {{
                    {fields}
                }}
                responseDetails {{
              id
                    type
                    totalCount
                    pagesCount
                }}
            }}
        }}
        """

        data = await self._make_graphql_request(query)
        if not data or "search" not in data:
            return {"results": [], "total": 0, "pages": 0}

        search_data = data["search"]
        results = search_data.get(result_field, [])

        normalized_results = []
        for item in results:
            if filter_type == "PODCASTSERIES":
                normalized = self._normalize_podcast_series(item)
            else:
                normalized = self._normalize_episode(item)
            if normalized:
                normalized_results.append(normalized)

        # Get pagination info
        total = 0
        pages = 0
        for detail in search_data.get("responseDetails", []):
            if detail.get("type") == filter_type:
                total = detail.get("totalCount", 0)
                pages = detail.get("pagesCount", 0)

        result = {
            "results": normalized_results,
            "total": total,
            "pages": pages
        }

        self._set_cache(self._search_cache, cache_key, result)
        return result

    # ========== CONTENT QUERIES ==========

    async def get_podcast_series(
        self,
        uuid: str,
        episodes_page: int = 1,
        episodes_limit: int = 25,
        sort_order: str = "LATEST"
    ) -> Optional[Dict[str, Any]]:
        """
        Get podcast series details with episodes

        Args:
            uuid: Podcast series UUID
            episodes_page: Page number for episodes
            episodes_limit: Episodes per page (max 25)
            sort_order: LATEST, OLDEST, or SEARCH
        """
        cache_key = f"{uuid}_{episodes_page}_{sort_order}"
        if episodes_page == 1 and sort_order == "LATEST":
            cached = self._check_cache(self._series_cache, cache_key)
            if cached:
                return cached

        query = f"""
        {{
            getPodcastSeries(uuid: "{uuid}") {{
                uuid
                name
                description(shouldStripHtmlTags: true)
                imageUrl
                authorName
                totalEpisodesCount
                genres
                language
                contentType
                seriesType
                isCompleted
                isExplicitContent
                popularityRank
                websiteUrl
                rssUrl
                childrenHash
                itunesInfo {{
                uuid
                    baseArtworkUrlOf(size: 640)
                    publisherName
                    country
                    subtitle
                    summary
                }}
                episodes(
                    sortOrder: {sort_order}
                    page: {episodes_page}
                    limitPerPage: {episodes_limit}
                ) {{
                    uuid
                    name
                    description(shouldStripHtmlTags: true)
                    datePublished
                    duration
                    audioUrl
                    imageUrl
                    episodeType
                    seasonNumber
                    episodeNumber
                    isExplicitContent
                    websiteUrl
                }}
            }}
        }}
        """

        data = await self._make_graphql_request(query)
        if not data or "getPodcastSeries" not in data:
            return None

        series_data = data["getPodcastSeries"]
        if not series_data:
            return None

        normalized = self._normalize_podcast_series(series_data, include_episodes=True)

        if episodes_page == 1 and sort_order == "LATEST":
            self._set_cache(self._series_cache, cache_key, normalized)

        return normalized

    async def get_episode(self, uuid: str) -> Optional[Dict[str, Any]]:
        """Get episode details"""
        cached = self._check_cache(self._episode_cache, uuid)
        if cached:
            return cached

        query = f"""
        {{
            getPodcastEpisode(uuid: "{uuid}") {{
                uuid
                guid
                name
                description(shouldStripHtmlTags: true)
                subtitle
                audioUrl
                videoUrl
                duration
                fileLength
                fileType
                imageUrl
                datePublished
                episodeType
                seasonNumber
                episodeNumber
                isExplicitContent
                isRemoved
                websiteUrl
                podcastSeries {{
                    uuid
                    name
                    imageUrl
                }}
            }}
        }}
        """

        data = await self._make_graphql_request(query)
        if not data or "getPodcastEpisode" not in data:
            return None

        episode_data = data["getPodcastEpisode"]
        if not episode_data:
            return None

        normalized = self._normalize_episode(episode_data)
        self._set_cache(self._episode_cache, uuid, normalized)
        return normalized

    async def get_multiple_podcast_series(
        self,
        uuids: List[str]
    ) -> List[Dict[str, Any]]:
        """
        Get multiple podcast series in batch (max 25)
        """
        if not uuids:
            return []

        # Taddy limit is 25
        uuids = uuids[:25]
        uuids_str = ', '.join([f'"{u}"' for u in uuids])

        query = f"""
        {{
            getMultiplePodcastSeries(uuids: [{uuids_str}]) {{
                uuid
                name
                imageUrl
                totalEpisodesCount
                childrenHash
                genres
                language
                seriesType
                isCompleted
                itunesInfo {{
                uuid
                    baseArtworkUrlOf(size: 300)
                    publisherName
                }}
            }}
        }}
        """

        data = await self._make_graphql_request(query)
        if not data or "getMultiplePodcastSeries" not in data:
            return []

        results = []
        for series in data["getMultiplePodcastSeries"]:
            if series:
                results.append(self._normalize_podcast_series(series))

        return results

    async def get_multiple_episodes(
        self,
        uuids: List[str]
    ) -> List[Dict[str, Any]]:
        """
        Get multiple episodes in batch (max 25)
        """
        if not uuids:
            return []

        uuids = uuids[:25]
        uuids_str = ', '.join([f'"{u}"' for u in uuids])

        query = f"""
        {{
            getMultiplePodcastEpisodes(uuids: [{uuids_str}]) {{
                uuid
                name
                duration
                audioUrl
                imageUrl
                datePublished
                episodeType
                podcastSeries {{
                    uuid
                    name
                    imageUrl
                }}
            }}
        }}
        """

        data = await self._make_graphql_request(query)
        if not data or "getMultiplePodcastEpisodes" not in data:
            return []

        results = []
        for episode in data["getMultiplePodcastEpisodes"]:
            if episode:
                results.append(self._normalize_episode(episode))

        return results

    async def get_latest_episodes(
        self,
        podcast_uuids: List[str],
        page: int = 1,
        limit: int = 50
    ) -> Dict[str, Any]:
        """
        Get latest episodes from multiple podcasts (for subscriptions)

        Args:
            podcast_uuids: List of podcast UUIDs (max 1000)
            page: Page number (1-20)
            limit: Episodes per page (max 50)
        """
        if not podcast_uuids:
            return {"results": [], "total": 0}

        limit = min(limit, 50)
        page = max(1, min(page, 20))
        podcast_uuids = podcast_uuids[:1000]

        uuids_str = ', '.join([f'"{u}"' for u in podcast_uuids])

        query = f"""
        {{
            getLatestPodcastEpisodes(
                uuids: [{uuids_str}]
                page: {page}
                limitPerPage: {limit}
            ) {{
                uuid
                name
                description(shouldStripHtmlTags: true)
                datePublished
                duration
                audioUrl
                imageUrl
                episodeType
                podcastSeries {{
                    uuid
                    name
                    imageUrl
                }}
            }}
        }}
        """

        data = await self._make_graphql_request(query)
        if not data or "getLatestPodcastEpisodes" not in data:
            return {"results": [], "total": 0}

        results = []
        for episode in data["getLatestPodcastEpisodes"]:
            if episode:
                results.append(self._normalize_episode(episode))

        return {"results": results, "total": len(results)}

    # ========== UTILITY QUERIES ==========

    async def get_api_requests_remaining(self) -> int:
        """Get remaining API requests for current hour"""
        query = """
        {
            getApiRequestsRemaining
        }
        """

        data = await self._make_graphql_request(query)
        if not data or "getApiRequestsRemaining" not in data:
            return -1

        return data["getApiRequestsRemaining"]

    def clear_cache(self) -> None:
        """Clear all caches"""
        self._search_cache.clear()
        self._series_cache.clear()
        self._episode_cache.clear()
        self._discovery_cache.clear()
        self.logger.info("Cache cleared")

    def clean_expired_cache(self) -> int:
        """Remove expired entries from all caches"""
        now = datetime.now()
        removed = 0

        for cache in [self._search_cache, self._series_cache, self._episode_cache, self._discovery_cache]:
            expired_keys = [
                key for key, (cached_time, _) in cache.items()
                if now - cached_time >= self.cache_duration
            ]
            for key in expired_keys:
                del cache[key]
                removed += 1

        if removed > 0:
            self.logger.debug(f"Removed {removed} expired cache entries")

        return removed

    # ========== NORMALIZATION ==========

    def _normalize_podcast_series(
        self,
        series: Dict[str, Any],
        include_episodes: bool = False
    ) -> Dict[str, Any]:
        """Normalize podcast series from Taddy API format"""
        itunes_info = series.get('itunesInfo') or {}

        # Get best image URL
        image_url = series.get('imageUrl', '')
        if itunes_info.get('baseArtworkUrlOf'):
            image_url = itunes_info['baseArtworkUrlOf']

        normalized = {
            'uuid': series.get('uuid'),
            'itunes_id': series.get('itunesId'),  # Add iTunes ID from Taddy
            'name': series.get('name', 'Unknown Podcast'),
            'description': series.get('description', ''),
            'image_url': image_url,
            'publisher': itunes_info.get('publisherName') or series.get('authorName', 'Unknown'),
            'author': series.get('authorName', ''),
            'total_episodes': series.get('totalEpisodesCount', 0),
            'genres': series.get('genres', []),
            'language': series.get('language', ''),
            'series_type': series.get('seriesType', ''),
            'is_completed': series.get('isCompleted', False),
            'is_explicit': series.get('isExplicitContent', False),
            'popularity_rank': series.get('popularityRank'),
            'children_hash': series.get('childrenHash', ''),
            'website_url': series.get('websiteUrl', ''),
            'rss_url': series.get('rssUrl', ''),
            'content_type': series.get('contentType', 'AUDIO'),
            'country': itunes_info.get('country', ''),
            'subtitle': itunes_info.get('subtitle', ''),
            'summary': itunes_info.get('summary', ''),
        }

        if include_episodes and 'episodes' in series:
            normalized['episodes'] = [
                self._normalize_episode(ep, podcast_name=series.get('name'), podcast_image=image_url)
                for ep in series.get('episodes', [])
            ]

        return normalized

    def _normalize_episode(
        self,
        episode: Dict[str, Any],
        podcast_name: str = None,
        podcast_image: str = None
    ) -> Dict[str, Any]:
        """Normalize episode from Taddy API format"""
        podcast_series = episode.get('podcastSeries') or {}

        # Get image URL
        image_url = episode.get('imageUrl', '')
        if not image_url and podcast_series:
            image_url = podcast_series.get('imageUrl', '')
        if not image_url and podcast_image:
            image_url = podcast_image

        normalized = {
            'uuid': episode.get('uuid'),
            'guid': episode.get('guid', ''),
            'name': episode.get('name', 'Unknown Episode'),
            'description': episode.get('description', ''),
            'subtitle': episode.get('subtitle', ''),
            'date_published': episode.get('datePublished'),
            'duration': episode.get('duration', 0),
            'audio_url': episode.get('audioUrl'),
            'video_url': episode.get('videoUrl', ''),
            'image_url': image_url,
            'episode_type': episode.get('episodeType', 'FULL'),
            'season_number': episode.get('seasonNumber'),
            'episode_number': episode.get('episodeNumber'),
            'is_explicit': episode.get('isExplicitContent', False),
            'is_removed': episode.get('isRemoved', False),
            'website_url': episode.get('websiteUrl', ''),
            'file_length': episode.get('fileLength', 0),
            'file_type': episode.get('fileType', ''),
        }

        # Add podcast series info
        if podcast_series:
            normalized['podcast'] = {
                'uuid': podcast_series.get('uuid'),
                'name': podcast_series.get('name', podcast_name or 'Unknown Podcast'),
                'image_url': podcast_series.get('imageUrl', podcast_image or '')
            }
        elif podcast_name:
            normalized['podcast'] = {
                'uuid': '',
                'name': podcast_name,
                'image_url': podcast_image or ''
            }

        return normalized
