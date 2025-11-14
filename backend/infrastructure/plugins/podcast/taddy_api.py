"""
Taddy API client for podcast search and retrieval
"""
import asyncio
import aiohttp
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta


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

        # Cache for search results (query -> (timestamp, results))
        self._search_cache: Dict[str, tuple[datetime, Dict[str, Any]]] = {}

        # Cache for podcast series details (uuid -> (timestamp, series))
        self._series_cache: Dict[str, tuple[datetime, Dict[str, Any]]] = {}

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
        """
        Makes a GraphQL request to Taddy API

        Args:
            query: GraphQL query string

        Returns:
            Response data or None if error
        """
        await self._ensure_session()

        try:
            payload = {"query": query}

            async with self.session.post(
                self.BASE_URL,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status == 429:
                    self.logger.warning("Taddy API rate limit exceeded (100 requests/hour)")
                    return None

                if resp.status != 200:
                    self.logger.warning(f"Taddy API error: HTTP {resp.status}")
                    return None

                data = await resp.json()

                # Check for GraphQL errors
                if "errors" in data:
                    self.logger.error(f"GraphQL errors: {data['errors']}")
                    return None

                return data.get("data")

        except asyncio.TimeoutError:
            self.logger.error("Timeout calling Taddy API")
            return None
        except Exception as e:
            self.logger.error(f"Error calling Taddy API: {e}")
            return None

    async def search(
        self,
        term: str,
        filter_type: str = "PODCASTSERIES",
        sort_by: str = "POPULARITY",
        limit: int = 50
    ) -> Dict[str, Any]:
        """
        Search for podcasts or episodes

        Args:
            term: Search term
            filter_type: PODCASTSERIES or PODCASTEPISODE
            sort_by: POPULARITY or EXACTNESS
            limit: Max results (default 50)

        Returns:
            Dict with podcasts/episodes and total count
        """
        # Check cache
        cache_key = f"{term}_{filter_type}_{sort_by}_{limit}"
        if cache_key in self._search_cache:
            cached_time, cached_data = self._search_cache[cache_key]
            if datetime.now() - cached_time < self.cache_duration:
                self.logger.debug(f"Using cached search results for '{term}'")
                return cached_data

        self.logger.info(f"🔍 Searching Taddy: term='{term}', type={filter_type}")

        # Build GraphQL query
        if filter_type == "PODCASTSERIES":
            result_field = "podcastSeries"
            fields = """
                uuid
                name
                description
                imageUrl
                itunesInfo {
                    uuid
                    publisherName
                    baseArtworkUrlOf(size: 640)
                }
            """
        else:  # PODCASTEPISODE
            result_field = "podcastEpisodes"
            fields = """
                uuid
                name
                description
                datePublished
                duration
                audioUrl
                imageUrl
                podcastSeries {
                    uuid
                    name
                    imageUrl
                }
            """

        query = f"""
        {{
            search(
                term: "{term}"
                filterForTypes: {filter_type}
                sortBy: {sort_by}
                limitPerType: {limit}
            ) {{
                searchId
                {result_field} {{
                    {fields}
                }}
            }}
        }}
        """

        data = await self._make_graphql_request(query)

        if not data or "search" not in data:
            return {"results": [], "total": 0}

        search_data = data["search"]
        results = search_data.get(result_field, [])

        # Normalize results
        normalized_results = []
        for item in results:
            if filter_type == "PODCASTSERIES":
                normalized = self._normalize_podcast_series(item)
            else:
                normalized = self._normalize_episode(item)

            if normalized:
                normalized_results.append(normalized)

        result = {
            "results": normalized_results,
            "total": len(normalized_results)
        }

        # Cache results
        self._search_cache[cache_key] = (datetime.now(), result)

        self.logger.info(f"Found {len(normalized_results)} {filter_type} for '{term}'")

        return result

    async def get_podcast_series(
        self,
        uuid: str,
        episodes_page: int = 1,
        episodes_limit: int = 20
    ) -> Optional[Dict[str, Any]]:
        """
        Get podcast series details with episodes

        Args:
            uuid: Podcast series UUID
            episodes_page: Page number for episodes (default 1)
            episodes_limit: Episodes per page (default 20)

        Returns:
            Podcast series with episodes or None if not found
        """
        # Check cache (only for page 1)
        if episodes_page == 1:
            cache_key = f"{uuid}_page1"
            if cache_key in self._series_cache:
                cached_time, cached_data = self._series_cache[cache_key]
                if datetime.now() - cached_time < self.cache_duration:
                    self.logger.debug(f"Using cached series data for {uuid}")
                    return cached_data

        self.logger.info(f"📻 Fetching podcast series: {uuid} (page {episodes_page})")

        query = f"""
        {{
            getPodcastSeries(uuid: "{uuid}") {{
                uuid
                name
                description
                imageUrl
                totalEpisodesCount
                itunesInfo {{
                    uuid
                    publisherName
                    baseArtworkUrlOf(size: 640)
                }}
                episodes(page: {episodes_page}, limitPerPage: {episodes_limit}) {{
                    uuid
                    name
                    description
                    datePublished
                    duration
                    audioUrl
                    imageUrl
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

        # Normalize series
        normalized = self._normalize_podcast_series(series_data, include_episodes=True)

        # Cache (only page 1)
        if episodes_page == 1:
            cache_key = f"{uuid}_page1"
            self._series_cache[cache_key] = (datetime.now(), normalized)

        return normalized

    async def get_episode(self, uuid: str) -> Optional[Dict[str, Any]]:
        """
        Get episode details

        Args:
            uuid: Episode UUID

        Returns:
            Episode details or None if not found
        """
        self.logger.info(f"🎧 Fetching episode: {uuid}")

        query = f"""
        {{
            getPodcastEpisode(uuid: "{uuid}") {{
                uuid
                name
                description
                datePublished
                duration
                audioUrl
                imageUrl
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

        return self._normalize_episode(episode_data)

    def _normalize_podcast_series(
        self,
        series: Dict[str, Any],
        include_episodes: bool = False
    ) -> Dict[str, Any]:
        """
        Normalize podcast series from Taddy API format to Milo format

        Args:
            series: Series data from Taddy API
            include_episodes: Whether to include episodes list

        Returns:
            Normalized podcast series
        """
        itunes_info = series.get('itunesInfo', {})

        # Get best image URL (prefer iTunes artwork)
        image_url = series.get('imageUrl', '')
        if itunes_info and itunes_info.get('baseArtworkUrlOf'):
            image_url = itunes_info.get('baseArtworkUrlOf') or image_url

        normalized = {
            'uuid': series.get('uuid'),
            'name': series.get('name', 'Unknown Podcast'),
            'description': series.get('description', ''),
            'image_url': image_url,
            'publisher': itunes_info.get('publisherName', 'Unknown'),
            'total_episodes': series.get('totalEpisodesCount', 0)
        }

        if include_episodes and 'episodes' in series:
            episodes = series.get('episodes', [])
            normalized['episodes'] = [
                self._normalize_episode(ep) for ep in episodes
            ]

        return normalized

    def _normalize_episode(self, episode: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize episode from Taddy API format to Milo format

        Args:
            episode: Episode data from Taddy API

        Returns:
            Normalized episode
        """
        podcast_series = episode.get('podcastSeries', {})

        # Get image URL (prefer episode image, fallback to podcast image)
        image_url = episode.get('imageUrl', '')
        if not image_url and podcast_series:
            image_url = podcast_series.get('imageUrl', '')

        normalized = {
            'uuid': episode.get('uuid'),
            'name': episode.get('name', 'Unknown Episode'),
            'description': episode.get('description', ''),
            'date_published': episode.get('datePublished'),
            'duration': episode.get('duration', 0),
            'audio_url': episode.get('audioUrl'),
            'image_url': image_url,
        }

        # Add podcast series info if available
        if podcast_series:
            normalized['podcast'] = {
                'uuid': podcast_series.get('uuid'),
                'name': podcast_series.get('name', 'Unknown Podcast'),
                'image_url': podcast_series.get('imageUrl', '')
            }

        return normalized
