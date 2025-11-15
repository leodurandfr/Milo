"""
Radio Browser API client with caching to limit calls
"""
import asyncio
import aiohttp
import logging
import re
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
from backend.infrastructure.plugins.radio.genres import extract_valid_genre


class RadioBrowserAPI:
    """
    Async client for Radio Browser API

    API Doc: https://api.radio-browser.info/
    Uses all.api.radio-browser.info for automatic load balancing
    """

    BASE_URL = "https://all.api.radio-browser.info/json"

    def __init__(self, cache_duration_minutes: int = 60, station_manager=None):
        self.logger = logging.getLogger(__name__)
        self.session: Optional[aiohttp.ClientSession] = None
        self.cache_duration = timedelta(minutes=cache_duration_minutes)
        self.station_manager = station_manager

        # Cache by country for dynamic searches (country_name -> (timestamp, stations))
        self._country_cache: Dict[str, tuple[datetime, List[Dict[str, Any]]]] = {}

        # Cache for the list of available countries (valid 24h)
        self._countries_cache: List[Dict[str, Any]] = []
        self._countries_cache_timestamp: Optional[datetime] = None
        self._countries_cache_duration = timedelta(hours=24)

        # Cache for favicon quality evaluations (url -> (quality_score, file_size, timestamp))
        self._favicon_quality_cache: Dict[str, tuple[int, int, datetime]] = {}

    async def _ensure_session(self) -> None:
        """Creates aiohttp session if needed"""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                headers={
                    'User-Agent': 'Milo/1.0',  # Radio Browser API requires a User-Agent
                }
            )

    async def close(self) -> None:
        """Closes aiohttp session"""
        if self.session and not self.session.closed:
            await self.session.close()
            self.session = None

    async def _fetch_stations_by_country(self, country_code: str) -> List[Dict[str, Any]]:
        """
        Gets all stations from a country via the API

        Args:
            country_code: ISO country code (e.g.: "FR", "GB")

        Returns:
            List of stations
        """
        await self._ensure_session()

        try:
            url = f"{self.BASE_URL}/stations/bycountrycodeexact/{country_code}"
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    self.logger.warning(f"API error for {country_code}: {resp.status}")
                    return []

                stations = await resp.json()
                self.logger.debug(f"Fetched {len(stations)} stations from {country_code}")
                return stations

        except asyncio.TimeoutError:
            self.logger.error(f"Timeout fetching stations for {country_code}")
            return []
        except Exception as e:
            self.logger.error(f"Error fetching stations for {country_code}: {e}")
            return []

    async def _fetch_stations_by_query(self, query: str) -> List[Dict[str, Any]]:
        """
        Gets all stations matching a search query via the API
        Global search among all stations from all countries

        Args:
            query: Search term (station name)

        Returns:
            List of normalized and filtered stations
        """
        await self._ensure_session()

        try:
            # Use the global search endpoint
            url = f"{self.BASE_URL}/stations/search"
            params = {"name": query, "limit": 10000}  # High limit to get all results

            async with self.session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    self.logger.warning(f"API error for query '{query}': {resp.status}")
                    return []

                stations = await resp.json()
                self.logger.debug(f"Fetched {len(stations)} stations for query '{query}'")

                # Filter and normalize
                valid_stations = []
                for station in stations:
                    if self._is_valid_station(station):
                        normalized = self._normalize_station(station)
                        valid_stations.append(normalized)

                # Deduplicate and sort par score
                deduplicated_stations = await self._deduplicate_stations(valid_stations)

                self.logger.info(f"Deduplicated {len(stations)} → {len(deduplicated_stations)} stations for query '{query}'")

                return deduplicated_stations

        except asyncio.TimeoutError:
            self.logger.error(f"Timeout fetching stations for query '{query}'")
            return []
        except Exception as e:
            self.logger.error(f"Error fetching stations for query '{query}': {e}")
            return []

    async def _fetch_station_by_id(self, station_id: str) -> Optional[Dict[str, Any]]:
        """
        Gets station by ID via the API

        Args:
            station_id: Station UUID

        Returns:
            Normalized station or None if not found
        """
        await self._ensure_session()

        try:
            url = f"{self.BASE_URL}/stations/byuuid/{station_id}"
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    self.logger.warning(f"API error for station {station_id}: {resp.status}")
                    return None

                stations = await resp.json()
                if not stations or len(stations) == 0:
                    self.logger.debug(f"Station {station_id} not found")
                    return None

                station = stations[0]  # The API returns a list with 1 element

                if self._is_valid_station(station):
                    normalized = self._normalize_station(station)
                    self.logger.debug(f"Fetched station {station_id}: {normalized['name']}")
                    return normalized
                else:
                    self.logger.debug(f"Station {station_id} is not valid")
                    return None

        except asyncio.TimeoutError:
            self.logger.error(f"Timeout fetching station {station_id}")
            return None
        except Exception as e:
            self.logger.error(f"Error fetching station {station_id}: {e}")
            return None

    async def _fetch_top_stations(self, limit: int = 500) -> List[Dict[str, Any]]:
        """
        Gets most popular stations via the API
        (based on votes)

        Args:
            limit: Number of stations to fetch (default: 500)

        Returns:
            List of normalized and filtered stations
        """
        await self._ensure_session()

        try:
            # Use the topvote endpoint for the most voted stations
            url = f"{self.BASE_URL}/stations/topvote/{limit}"

            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    self.logger.warning(f"API error for top stations: {resp.status}")
                    return []

                stations = await resp.json()
                self.logger.debug(f"Fetched {len(stations)} top stations")

                # Filter and normalize
                valid_stations = []
                for station in stations:
                    if self._is_valid_station(station):
                        normalized = self._normalize_station(station)
                        valid_stations.append(normalized)

                # Deduplicate (just in case)
                deduplicated_stations = await self._deduplicate_stations(valid_stations)

                self.logger.info(f"Returning {len(deduplicated_stations)} top stations")

                return deduplicated_stations

        except asyncio.TimeoutError:
            self.logger.error("Timeout fetching top stations")
            return []
        except Exception as e:
            self.logger.error(f"Error fetching top stations: {e}")
            return []

    def _is_valid_station(self, station: Dict[str, Any]) -> bool:
        """
        Checks if station is valid

        Args:
            station: Station dict from API

        Returns:
            True if station is valid
        """
        return (
            station.get('url_resolved') and
            station.get('codec') != 'UNKNOWN' and
            station.get('lastcheckok') == 1 and
            station.get('name')
        )

    def _get_favicon_quality(self, url: str) -> int:
        """
        Evaluates the quality of a favicon to prioritize the best sources

        Args:
            url: Favicon URL

        Returns:
            Quality score (higher = better)
        """
        if not url:
            return -1

        url_lower = url.lower()

        # Reject URLs that cause CORS problems or are temporary
        problematic_domains = [
            'facebook.com', 'fbcdn.net', 'dropbox.com',
            'googledrive.com', 'onedrive.com', 'sharepoint.com',
            'syncusercontent.com'
        ]

        if any(domain in url_lower for domain in problematic_domains):
            return 0  # Very poor quality

        # Reject URLs with tokens/timestamps (often temporary)
        if any(param in url_lower for param in ['?timestamp=', '?token=', '?signature=']):
            return 0

        # Reject Wikipedia pages (not direct images)
        if 'wikipedia.org/wiki/' in url_lower or '#/media/' in url_lower:
            return 5  # Very poor quality (web page, not image)

        # favicon.ico = low quality
        if 'favicon.ico' in url_lower:
            return 10

        # Prefer direct images from reliable sources
        quality = 50

        # Bonus for Wikimedia (direct images, not Wikipedia pages)
        if 'upload.wikimedia.org' in url_lower:
            quality += 100

        # Detect if the name contains "favicon" (e.g.: cropped-favicon.png)
        # Penalize these images as they are generally of lower quality than "official" images
        contains_favicon = 'favicon' in url_lower and 'favicon.ico' not in url_lower

        # Bonus for image formats (reduced if name contains "favicon")
        if '.svg' in url_lower:
            quality += 30 if not contains_favicon else 30
        elif '.png' in url_lower:
            quality += 20 if not contains_favicon else -50
        elif '.webp' in url_lower:
            quality += 20 if not contains_favicon else -50
        elif '.jpg' in url_lower or '.jpeg' in url_lower:
            quality += 15 if not contains_favicon else -50

        # Bonus for resolution detected in URL (e.g.: 1260x1260, 180x180)
        # Search for all occurrences of widthxheight pattern
        resolution_matches = re.findall(r'(\d+)x(\d+)', url_lower)
        if resolution_matches:
            # Take the LAST occurrence (e.g.: image-400x400-resized-180x180.png → 180x180)
            width, height = map(int, resolution_matches[-1])
            # Bonus = minimum dimension (works for squares and rectangles)
            resolution_bonus = min(width, height)
            quality += resolution_bonus

        return quality

    async def _evaluate_favicon_with_head(self, favicon_url: str) -> tuple[int, int]:
        """
        Evaluates the quality of a favicon via HTTP HEAD request (lightweight, without downloading the image)

        Checks:
        - Availability (status 200)
        - Valid MIME type (image/*)
        - File size (Content-Length)

        Args:
            favicon_url: Favicon URL to evaluate

        Returns:
            (quality_score, file_size_bytes)
            - quality_score = -1 if error/404/not an image
            - quality_score = file_size + bonus according to Content-Type
            - file_size = size in bytes (0 if not available)
        """
        if not favicon_url:
            return (-1, 0)

        # Check cache first
        if favicon_url in self._favicon_quality_cache:
            cached_score, cached_size, cached_time = self._favicon_quality_cache[favicon_url]
            # Cache valid for the duration of station cache
            if datetime.now() - cached_time < self.cache_duration:
                return (cached_score, cached_size)

        # First check URL quality (fast filter)
        url_quality = self._get_favicon_quality(favicon_url)
        if url_quality < 10:
            # Problematic URL, don't make request
            self._favicon_quality_cache[favicon_url] = (-1, 0, datetime.now())
            return (-1, 0)

        await self._ensure_session()

        try:
            # HEAD request only (no download)
            async with self.session.head(
                favicon_url,
                timeout=aiohttp.ClientTimeout(total=3),
                allow_redirects=True
            ) as resp:
                # Check status code
                if resp.status != 200:
                    self.logger.debug(f"Favicon HEAD failed (HTTP {resp.status}): {favicon_url}")
                    self._favicon_quality_cache[favicon_url] = (-1, 0, datetime.now())
                    return (-1, 0)

                # Check Content-Type
                content_type = resp.headers.get('Content-Type', '').lower()
                if not content_type.startswith('image/'):
                    self.logger.debug(f"Favicon not an image (Content-Type: {content_type}): {favicon_url}")
                    self._favicon_quality_cache[favicon_url] = (-1, 0, datetime.now())
                    return (-1, 0)

                # Get the size
                file_size = 0
                content_length = resp.headers.get('Content-Length')
                if content_length:
                    try:
                        file_size = int(content_length)
                    except ValueError:
                        file_size = 0

                # Calculate quality score based on size + MIME type
                quality_score = file_size

                # Bonus according to MIME type (high to surpass ICO even without Content-Length)
                if 'svg' in content_type:
                    quality_score += 100000  # SVG = vector, excellent quality
                elif 'png' in content_type or 'webp' in content_type:
                    quality_score += 50000  # PNG/WEBP = good quality (priority over ICO)
                elif 'jpeg' in content_type or 'jpg' in content_type:
                    quality_score += 20000   # JPEG = medium quality
                # else: image/x-icon or other = no bonus (file_size only)

                # Cache
                self._favicon_quality_cache[favicon_url] = (quality_score, file_size, datetime.now())

                self.logger.debug(
                    f"✅ Favicon evaluated: {favicon_url[:50]}... "
                    f"(score={quality_score}, size={file_size}, type={content_type})"
                )

                return (quality_score, file_size)

        except asyncio.TimeoutError:
            self.logger.debug(f"Favicon HEAD timeout: {favicon_url}")
            self._favicon_quality_cache[favicon_url] = (-1, 0, datetime.now())
            return (-1, 0)
        except Exception as e:
            self.logger.debug(f"Favicon HEAD error for {favicon_url}: {e}")
            self._favicon_quality_cache[favicon_url] = (-1, 0, datetime.now())
            return (-1, 0)

    def _normalize_station(self, station: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalizes a station from API format to Milo format

        Args:
            station: Station in Radio Browser API format

        Returns:
            Normalized station
        """
        # Clean the favicon (avoid problematic URLs)
        favicon = station.get('favicon', '')
        if favicon:
            # Filter low quality favicons
            if self._get_favicon_quality(favicon) < 10:
                favicon = ''
            # Note: No HTTP→HTTPS conversion, the backend proxy will handle redirects

        return {
            'id': station.get('stationuuid'),
            'name': station.get('name'),
            'url': station.get('url_resolved'),
            'country': station.get('country', 'Unknown'),
            'genre': extract_valid_genre(station.get('tags', '')),
            'favicon': favicon,
            'bitrate': station.get('bitrate', 0),
            'codec': station.get('codec', 'Unknown'),
            'votes': station.get('votes', 0),
            'clickcount': station.get('clickcount', 0),
            'score': station.get('votes', 0) + station.get('clickcount', 0)
        }

    def _compare_station_quality(self, station1: Dict[str, Any], station2: Dict[str, Any]) -> int:
        """
        Compares quality of two stations for deduplication

        Args:
            station1: First station
            station2: Second station

        Returns:
            > 0 if station1 better, < 0 if station2 better, 0 if equal
        """
        # Compare popularity (score = votes + clicks)
        score1 = station1.get('score', 0)
        score2 = station2.get('score', 0)

        if score1 > score2 * 1.2:  # 20% significant difference
            return 1
        elif score2 > score1 * 1.2:
            return -1

        # If similar scores, compare bitrate
        bitrate1 = station1.get('bitrate', 0)
        bitrate2 = station2.get('bitrate', 0)

        return bitrate1 - bitrate2

    async def _deduplicate_stations(self, stations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Deduplicates station list by name (case-insensitive)
        For each group of duplicates, merges the best audio URL with the best image

        Optimized strategy (WITHOUT blocking HTTP HEAD requests):
        1. Group all versions of the same station by name
        2. Choose the version with the best audio stream (highest score + bitrate)
        3. Choose the best favicon based on URL quality only (no HEAD request)
        4. Merge both to create the optimal station

        Args:
            stations: List of normalized stations

        Returns:
            List of deduplicated stations sorted by score
        """
        if not stations:
            return []

        # Group all versions of each station by name
        stations_by_name = {}

        for station in stations:
            station_key = station['name'].lower().strip()

            if station_key not in stations_by_name:
                stations_by_name[station_key] = []

            stations_by_name[station_key].append(station)

        # For each group of duplicates, create a merged station
        deduplicated = []

        for station_name, versions in stations_by_name.items():
            if len(versions) == 1:
                # No duplicates, keep as is
                deduplicated.append(versions[0])
            else:
                # Multiple versions: merge best audio + best image

                # 1. Find version with best audio stream (score + bitrate)
                best_audio = max(
                    versions,
                    key=lambda s: (s.get('score', 0), s.get('bitrate', 0))
                )

                # 2. Find best favicon based on URL quality only (fast)
                best_favicon = ""
                best_favicon_quality = -1

                for version in versions:
                    favicon = version.get('favicon', '')
                    # Always evaluate quality, even if empty (returns -1)
                    url_quality = self._get_favicon_quality(favicon)
                    if url_quality > best_favicon_quality:
                        best_favicon_quality = url_quality
                        best_favicon = favicon

                # 3. Create merged station (best audio + best image)
                merged_station = best_audio.copy()
                merged_station['favicon'] = best_favicon

                deduplicated.append(merged_station)

                # Concise log for debug (only if duplicates merged)
                if len(versions) > 1:
                    self.logger.debug(
                        f"🔀 Merged {len(versions)} versions of '{versions[0]['name']}' "
                        f"(score={best_audio.get('score', 0)}, bitrate={best_audio.get('bitrate', 0)}, "
                        f"favicon_quality={best_favicon_quality})"
                    )

        # Sort by popularity (votes + clicks)
        sorted_stations = sorted(
            deduplicated,
            key=lambda s: s.get('score', 0),
            reverse=True
        )

        self.logger.debug(f"Deduplication: {len(stations)} → {len(sorted_stations)} stations")

        return sorted_stations

    def _build_search_params(
        self,
        query: str = "",
        country: str = "",
        genre: str = "",
        order: str = "votes",
        limit: int = 10000
    ) -> Dict[str, Any]:
        """
        Intelligently builds search parameters for the RadioBrowser API

        Args:
            query: Search term
            country: Country filter
            genre: Genre filter (tag)
            order: Sorting (votes, clickcount, name, etc.)
            limit: Max number of results

        Returns:
            Dict of parameters for the API
        """
        params = {
            "limit": limit,
            "order": order,
            "reverse": "true",  # Descending sort (best first)
            "hidebroken": "true"  # Hide non-functional stations
        }

        # Add active filters
        if query:
            # Use ONLY name for query (substring matching by default)
            # Do NOT put in tag also → avoids overly restrictive AND logic
            params["name"] = query

        if country:
            params["country"] = country

        if genre:
            # Tag = music genre
            params["tag"] = genre

        return params

    async def _fetch_with_search_params(
        self,
        params: Dict[str, Any],
        description: str = "search"
    ) -> List[Dict[str, Any]]:
        """
        Unified API call with search parameters

        Args:
            params: Search parameters built by _build_search_params()
            description: Description for logs

        Returns:
            List of normalized and deduplicated stations
        """
        await self._ensure_session()

        try:
            url = f"{self.BASE_URL}/stations/search"

            self.logger.debug(f"API call [{description}]: {params}")

            async with self.session.get(
                url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    self.logger.warning(f"API error [{description}]: HTTP {resp.status}")
                    return []

                stations = await resp.json()
                self.logger.debug(f"Fetched {len(stations)} raw stations [{description}]")

                # Filter and normalize
                valid_stations = []
                for station in stations:
                    if self._is_valid_station(station):
                        normalized = self._normalize_station(station)
                        valid_stations.append(normalized)

                # Deduplicate and sort
                deduplicated_stations = await self._deduplicate_stations(valid_stations)

                # Debug: Log favicons after deduplication
                for station in deduplicated_stations:
                    if 'meuh' in station.get('name', '').lower():
                        self.logger.debug(
                            f"🔍 After deduplication: {station.get('name')} → "
                            f"favicon={'✅' if station.get('favicon') else '❌'} "
                            f"({station.get('favicon')[:50] if station.get('favicon') else 'empty'})"
                        )

                self.logger.info(
                    f"[{description}] {len(stations)} raw → "
                    f"{len(valid_stations)} valid → "
                    f"{len(deduplicated_stations)} deduplicated"
                )

                return deduplicated_stations

        except asyncio.TimeoutError:
            self.logger.error(f"Timeout during [{description}]")
            return []
        except Exception as e:
            self.logger.error(f"Error during [{description}]: {e}")
            return []

    async def search_stations(
        self,
        query: str = "",
        country: str = "",
        genre: str = "",
        limit: int = 10000
    ) -> Dict[str, Any]:
        """
        Unified station search with filters (includes custom stations)

        Strategy:
        1. Build optimal search parameters
        2. Make unified API call
        3. If < 10 results, attempt progressive fallback
        4. Add custom stations
        5. Enrich with custom images

        Args:
            query: Search term (station name)
            country: Country filter
            genre: Genre filter
            limit: Max number of results

        Returns:
            Dict with stations and total: {stations: [...], total: int}
        """
        # Log the search
        filters_desc = []
        if query:
            filters_desc.append(f"query='{query}'")
        if country:
            filters_desc.append(f"country='{country}'")
        if genre:
            filters_desc.append(f"genre='{genre}'")

        search_desc = ", ".join(filters_desc) if filters_desc else "no filters (top stations)"
        self.logger.info(f"🔍 Search: {search_desc}")

        # Special case: no filters → top stations
        if not query and not country and not genre:
            self.logger.debug("No filters, loading top 500 stations")
            all_stations = await self._fetch_top_stations(limit=500)
        else:
            # Build search parameters
            search_params = self._build_search_params(query, country, genre)

            # Unified API call
            all_stations = await self._fetch_with_search_params(search_params, search_desc)

        # Add manually created stations (not modified favorites)
        # Modified favorites are already enriched in the normal API flow via station_manager
        if self.station_manager:
            manual_stations_dict = self.station_manager.get_manual_stations()

            # Apply same filters as RadioBrowserAPI stations
            filtered_custom = []
            # Iterate over manual stations (custom_xxx IDs)
            for station_id, station in manual_stations_dict.items():
                # Add ID to station metadata for consistency
                station = {**station, 'id': station_id}
                matches = True

                # Check query match (in name or genre)
                if query:
                    query_lower = query.lower()
                    name_match = query_lower in station.get('name', '').lower()
                    genre_match = query_lower in station.get('genre', '').lower()
                    if not (name_match or genre_match):
                        matches = False

                # Check country match
                if country and matches:
                    if country.lower() not in station.get('country', '').lower():
                        matches = False

                # Check genre match
                if genre and matches:
                    if genre.lower() not in station.get('genre', '').lower():
                        matches = False

                if matches:
                    filtered_custom.append(station)

            # Merge and sort by score (custom stations have score=0, so they appear last)
            all_stations = all_stations + filtered_custom
            all_stations = sorted(all_stations, key=lambda s: s.get('score', 0), reverse=True)

            if filtered_custom:
                self.logger.info(f"✅ Added {len(filtered_custom)} manually-added custom station(s)")

        # Total before limit
        total = len(all_stations)

        # Limit results
        limited_results = all_stations[:limit]

        self.logger.info(f"📊 Final: {total} stations (returning {len(limited_results)})")

        return {
            "stations": limited_results,
            "total": total
        }

    async def get_station_by_id(self, station_id: str) -> Optional[Dict[str, Any]]:
        """
        Gets station by ID (includes custom stations)

        Args:
            station_id: Station UUID

        Returns:
            Station or None if not found
        """
        # First check if custom station
        if station_id.startswith("custom_") and self.station_manager:
            custom_station = self.station_manager.get_custom_station_by_id(station_id)
            if custom_station:
                return custom_station

        # Get directly from API
        station = await self._fetch_station_by_id(station_id)

        return station

    async def get_stations_by_ids(self, station_ids: List[str]) -> List[Dict[str, Any]]:
        """
        Gets multiple stations by IDs in batch (includes custom stations)
        For stations with missing/poor favicons, searches by name
        to find better versions. Applies final deduplication.

        Args:
            station_ids: List of station UUIDs

        Returns:
            List of found stations with improved favicons
        """
        if not station_ids:
            return []

        stations = []
        stations_needing_better_favicon = []

        # Separate custom stations from regular stations
        custom_ids = [sid for sid in station_ids if sid.startswith("custom_")]
        regular_ids = [sid for sid in station_ids if not sid.startswith("custom_")]

        # Get custom stations
        if custom_ids and self.station_manager:
            for station_id in custom_ids:
                custom_station = self.station_manager.get_custom_station_by_id(station_id)
                if custom_station:
                    stations.append(custom_station)

        # Get regular stations
        for station_id in regular_ids:
            # Fetch from API
            station = await self._fetch_station_by_id(station_id)

            if station:
                stations.append(station)

                # If the favicon is empty or of poor quality, we'll try to find a better version
                favicon_quality = self._get_favicon_quality(station.get('favicon', ''))
                if favicon_quality < 20:  # Low threshold = no favicon or poor quality
                    stations_needing_better_favicon.append(station)

        # For stations with missing/poor favicons, search for better versions by name
        if stations_needing_better_favicon:
            self.logger.info(f"Searching better favicons for {len(stations_needing_better_favicon)} stations")

            additional_stations = []
            for station in stations_needing_better_favicon:
                station_name = station.get('name', '')
                if station_name:
                    # Search by name to find other versions of this station
                    search_results = await self._fetch_stations_by_query(station_name)

                    # Keep only results that match the same name (case-insensitive)
                    # to avoid adding irrelevant stations
                    matching_results = [
                        s for s in search_results
                        if s.get('name', '').lower().strip() == station_name.lower().strip()
                    ]

                    additional_stations.extend(matching_results)

            # Add found alternative versions
            stations.extend(additional_stations)
            self.logger.info(f"Found {len(additional_stations)} alternative versions with better favicons")

        # IMPORTANT: Apply deduplication to merge versions and keep the best favicons
        # Deduplication will compare all versions of each station (ID + alternatives by name)
        # and keep the best favicon for each unique station
        deduplicated_stations = await self._deduplicate_stations(stations)

        return deduplicated_stations

    async def increment_station_clicks(self, station_id: str) -> bool:
        """
        Increments click counter for a station

        The Radio Browser API uses this counter for ranking.

        Args:
            station_id: Station UUID

        Returns:
            True if successful
        """
        await self._ensure_session()

        try:
            url = f"{self.BASE_URL}/url/{station_id}"
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                success = resp.status == 200
                if success:
                    self.logger.debug(f"Incremented click count for station {station_id}")
                return success

        except Exception as e:
            self.logger.warning(f"Failed to increment clicks for {station_id}: {e}")
            return False

    async def _fetch_stations_by_country_name(self, country_name: str) -> List[Dict[str, Any]]:
        """
        Gets all stations from a country via the API (by country name)

        Args:
            country_name: Country name (e.g.: "France", "Japan")

        Returns:
            List of normalized and filtered stations
        """
        await self._ensure_session()

        try:
            # Use the search endpoint with country filter
            url = f"{self.BASE_URL}/stations/search"
            params = {"country": country_name, "limit": 10000}  # High limit to get all stations

            async with self.session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    self.logger.warning(f"API error for country {country_name}: {resp.status}")
                    return []

                stations = await resp.json()
                self.logger.debug(f"Fetched {len(stations)} stations from {country_name}")

                # Filter and normalize
                valid_stations = []
                for station in stations:
                    if self._is_valid_station(station):
                        normalized = self._normalize_station(station)
                        valid_stations.append(normalized)

                # Deduplicate and sort par score
                deduplicated_stations = await self._deduplicate_stations(valid_stations)

                self.logger.info(f"Deduplicated {len(stations)} → {len(deduplicated_stations)} stations for {country_name}")

                return deduplicated_stations

        except asyncio.TimeoutError:
            self.logger.error(f"Timeout fetching stations for {country_name}")
            return []
        except Exception as e:
            self.logger.error(f"Error fetching stations for {country_name}: {e}")
            return []

    async def _fetch_stations_by_genre(self, genre: str) -> List[Dict[str, Any]]:
        """
        Gets all stations from a genre via the API

        Args:
            genre: Music genre (e.g.: "pop", "rock", "jazz")

        Returns:
            List of normalized and filtered stations
        """
        await self._ensure_session()

        try:
            url = f"{self.BASE_URL}/stations/search"
            params = {"tag": genre, "limit": 10000}

            async with self.session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    self.logger.warning(f"API error for genre {genre}: {resp.status}")
                    return []

                stations = await resp.json()
                self.logger.debug(f"Fetched {len(stations)} stations for genre {genre}")

                # Filter and normalize
                valid_stations = []
                for station in stations:
                    if self._is_valid_station(station):
                        normalized = self._normalize_station(station)
                        valid_stations.append(normalized)

                # Deduplicate and sort by score
                deduplicated_stations = await self._deduplicate_stations(valid_stations)

                self.logger.info(f"Deduplicated {len(stations)} → {len(deduplicated_stations)} stations for genre {genre}")

                return deduplicated_stations

        except asyncio.TimeoutError:
            self.logger.error(f"Timeout fetching stations for genre {genre}")
            return []
        except Exception as e:
            self.logger.error(f"Error fetching stations for genre {genre}: {e}")
            return []

    async def _fetch_stations_by_country_and_genre(self, country_name: str, genre: str) -> List[Dict[str, Any]]:
        """
        Gets stations from a country AND genre via API

        Args:
            country_name: Country name (e.g. "France", "Japan")
            genre: Music genre (e.g. "pop", "rock")

        Returns:
            List of normalized and filtered stations
        """
        await self._ensure_session()

        try:
            url = f"{self.BASE_URL}/stations/search"
            params = {"country": country_name, "tag": genre, "limit": 10000}

            async with self.session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    self.logger.warning(f"API error for {country_name} + {genre}: {resp.status}")
                    return []

                stations = await resp.json()
                self.logger.debug(f"Fetched {len(stations)} stations from {country_name} with genre {genre}")

                # Filter and normalize
                valid_stations = []
                for station in stations:
                    if self._is_valid_station(station):
                        normalized = self._normalize_station(station)
                        valid_stations.append(normalized)

                # Deduplicate and sort by score
                deduplicated_stations = await self._deduplicate_stations(valid_stations)

                self.logger.info(f"Deduplicated {len(stations)} → {len(deduplicated_stations)} stations for {country_name} + {genre}")

                return deduplicated_stations

        except asyncio.TimeoutError:
            self.logger.error(f"Timeout fetching stations for {country_name} + {genre}")
            return []
        except Exception as e:
            self.logger.error(f"Error fetching stations for {country_name} + {genre}: {e}")
            return []

    async def _fetch_stations_by_query_and_genre(self, query: str, genre: str) -> List[Dict[str, Any]]:
        """
        Gets stations matching a search query AND genre via API

        Args:
            query: Search term (station name)
            genre: Music genre (e.g. "pop", "rock")

        Returns:
            List of normalized and filtered stations
        """
        await self._ensure_session()

        try:
            url = f"{self.BASE_URL}/stations/search"
            params = {"name": query, "tag": genre, "limit": 10000}

            async with self.session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    self.logger.warning(f"API error for query '{query}' + genre {genre}: {resp.status}")
                    return []

                stations = await resp.json()
                self.logger.debug(f"Fetched {len(stations)} stations for query '{query}' with genre {genre}")

                # Filter and normalize
                valid_stations = []
                for station in stations:
                    if self._is_valid_station(station):
                        normalized = self._normalize_station(station)
                        valid_stations.append(normalized)

                # Deduplicate and sort by score
                deduplicated_stations = await self._deduplicate_stations(valid_stations)

                self.logger.info(f"Deduplicated {len(stations)} → {len(deduplicated_stations)} stations for query '{query}' + genre {genre}")

                return deduplicated_stations

        except asyncio.TimeoutError:
            self.logger.error(f"Timeout fetching stations for query '{query}' + genre {genre}")
            return []
        except Exception as e:
            self.logger.error(f"Error fetching stations for query '{query}' + genre {genre}: {e}")
            return []

    async def get_available_countries(self) -> List[Dict[str, Any]]:
        """
        Gets list of all available countries from Radio Browser API
        With 24h cache + retry logic + fallback

        Returns:
            List of countries with name and station count
            Format: [{"name": "France", "stationcount": 2345}, ...]
        """
        # Check cache first
        if self._countries_cache and self._countries_cache_timestamp:
            cache_age = datetime.now() - self._countries_cache_timestamp
            if cache_age < self._countries_cache_duration:
                self.logger.debug(f"Using cached countries ({len(self._countries_cache)} countries, age: {cache_age})")
                return self._countries_cache

        # Cache expired or absent, try to fetch
        await self._ensure_session()

        # Try 3 times with retry
        for attempt in range(1, 4):
            try:
                self.logger.info(f"Attempt {attempt}/3 fetching countries from Radio Browser API...")
                url = f"{self.BASE_URL}/countries"
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        self.logger.warning(f"API error fetching countries (attempt {attempt}): HTTP {resp.status}")
                        if attempt < 3:
                            await asyncio.sleep(2)  # Wait 2s before retry
                            continue
                        # Last attempt failed, use fallback
                        break

                    countries = await resp.json()
                    # Filter countries with at least 80 stations
                    filtered_countries = [
                        {"name": c.get("name", ""), "stationcount": c.get("stationcount", 0)}
                        for c in countries
                        if c.get("stationcount", 0) >= 80 and c.get("name")
                    ]

                    # Sort by station count (descending)
                    sorted_countries = sorted(
                        filtered_countries,
                        key=lambda c: c["stationcount"],
                        reverse=True
                    )

                    # Cache
                    self._countries_cache = sorted_countries
                    self._countries_cache_timestamp = datetime.now()

                    self.logger.info(f"✅ Fetched and cached {len(sorted_countries)} countries from Radio Browser API")
                    return sorted_countries

            except asyncio.TimeoutError:
                self.logger.warning(f"Timeout fetching countries list (attempt {attempt}/3)")
                if attempt < 3:
                    await asyncio.sleep(2)  # Wait 2s before retry
                    continue
            except Exception as e:
                self.logger.warning(f"Error fetching countries (attempt {attempt}/3): {e}")
                if attempt < 3:
                    await asyncio.sleep(2)  # Wait 2s before retry
                    continue

        # All attempts failed
        # Use stale cache if it exists
        if self._countries_cache:
            cache_age = datetime.now() - self._countries_cache_timestamp if self._countries_cache_timestamp else None
            self.logger.warning(f"⚠️ API unreachable, using stale cache ({len(self._countries_cache)} countries, age: {cache_age})")
            return self._countries_cache

        # No cache, return empty list
        self.logger.error("❌ API unreachable and no cache available, returning empty list")
        return []
