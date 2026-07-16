"""
Radio Browser API client with caching to limit calls
"""
import asyncio
import aiohttp
import logging
import re
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse
from datetime import datetime, timedelta
from backend.sources.radio.genres import extract_valid_genre
from backend.sources.radio.server_discovery import ServerDiscovery
from backend.shared.decorators import handle_errors
from backend.shared.network import NetworkUnavailableError


class RadioBrowserAPI:
    """
    Async client for Radio Browser API

    API Doc: https://api.radio-browser.info/
    Uses ServerDiscovery for explicit mirror selection + rotation
    (per the official Radio Browser docs).
    """

    def __init__(self, station_manager=None):
        self.logger = logging.getLogger("source.radio.browser_api")
        self.session: Optional[aiohttp.ClientSession] = None
        self.station_manager = station_manager
        self._discovery = ServerDiscovery()

        # Cache for the list of available countries (valid 24h)
        self._countries_cache: List[Dict[str, Any]] = []
        self._countries_cache_timestamp: Optional[datetime] = None
        self._countries_cache_duration = timedelta(hours=24)

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

    async def _request(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: float = 15,
    ) -> Optional[Any]:
        """GET {server}/json/{path} with mirror rotation on failure.

        Returns parsed JSON on success, None on a 4xx logical failure, or raises
        NetworkUnavailableError if every mirror failed with transient errors.
        """
        await self._ensure_session()

        # Trigger DNS resolution on first use so the retry budget below reflects
        # the actual mirror count (otherwise it would always be the stale 0).
        await self._discovery.get_server()
        # Try every known mirror once; min 2 covers the fallback-only case where
        # DNS failed and we want a chance for transient recovery on the retry.
        attempts = max(2, self._discovery.server_count)
        endpoint = path.lstrip("/")
        last_error: Optional[Exception] = None

        for _ in range(attempts):
            server = await self._discovery.get_server()
            url = f"{self._discovery.base_url(server)}/{endpoint}"

            try:
                async with self.session.get(
                    url,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    if 200 <= resp.status < 300:
                        return await resp.json()
                    if 500 <= resp.status < 600:
                        # A single flaky mirror is normal for this federated
                        # community service; rotating to the next one is the
                        # designed recovery, not a fault worth a WARNING.
                        self.logger.info(
                            f"Mirror {server} returned HTTP {resp.status} for /{endpoint}; rotating"
                        )
                        await self._discovery.rotate()
                        continue
                    # 4xx: every federated mirror shares the same DB, so rotating
                    # won't help. Treat as a logical not-found / bad-request.
                    self.logger.info(
                        f"Mirror {server} returned HTTP {resp.status} for /{endpoint}"
                    )
                    return None
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_error = e
                # Transient mirror failure (often a bare timeout, whose str() is
                # empty — hence the type-name fallback). Rotating is the designed
                # recovery; only an all-mirrors failure below is a real WARNING.
                self.logger.info(
                    f"Mirror {server} failed for /{endpoint}: {str(e) or type(e).__name__}; rotating"
                )
                await self._discovery.rotate()
                continue
            except Exception as e:
                self.logger.error(
                    f"Unexpected error calling mirror {server} for /{endpoint}: {e}"
                )
                return None

        raise NetworkUnavailableError(
            f"All Radio Browser mirrors failed for /{endpoint}: {last_error}"
        )

    async def _fetch_stations_by_query(
        self, query: str, *, deduplicate: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Gets all stations matching a search query via the API
        Global search among all stations from all countries

        Args:
            query: Search term (station name)
            deduplicate: Merge same-name variants into one entry (default). Set
                False for alternative-URL resolution, which needs the raw
                same-name variants to rank them by broadcaster affinity — dedup
                would collapse different broadcasters into one and defeat WI-4.

        Returns:
            List of normalized and filtered stations
            (empty list on network failure — preserves the pre-rotation contract
            for callers like get_stations_by_ids' favicon-fallback loop)
        """
        try:
            # Exact-name lookup (favicon/alternative resolution): a station rarely
            # has more than a handful of URL variants, so a broad limit was waste.
            # Order server-side by bitrate so the strongest variants survive the
            # cap even when a name is very common (final ranking is client-side).
            stations = await self._request(
                "stations/search",
                params={
                    "name": query,
                    "limit": 100,
                    "order": "bitrate",
                    "reverse": "true",
                    "hidebroken": "true",
                },
                timeout=15,
            )
        except NetworkUnavailableError as e:
            self.logger.info(f"Network unavailable for query '{query}': {e}")
            return []

        if not stations:
            return []

        self.logger.debug(f"Fetched {len(stations)} stations for query '{query}'")

        valid_stations = [
            self._normalize_station(station)
            for station in stations
            if self._is_valid_station(station)
        ]

        if not deduplicate:
            return valid_stations

        deduplicated_stations = await self._deduplicate_stations(valid_stations)

        self.logger.info(
            f"Deduplicated {len(stations)} → {len(deduplicated_stations)} stations for query '{query}'"
        )

        return deduplicated_stations

    async def fetch_remote_station(self, station_id: str) -> Optional[Dict[str, Any]]:
        """
        Gets station by ID via the API

        Args:
            station_id: Station UUID

        Returns:
            Normalized station, or None if not found or all mirrors unreachable
        """
        try:
            stations = await self._request(
                f"stations/byuuid/{station_id}",
                timeout=10,
            )
        except NetworkUnavailableError as e:
            self.logger.info(f"Network unavailable for station {station_id}: {e}")
            return None

        if not stations:
            self.logger.debug(f"Station {station_id} not found")
            return None

        station = stations[0]  # The API returns a list with 1 element

        if not self._is_valid_station(station):
            self.logger.debug(f"Station {station_id} is not valid")
            return None

        normalized = self._normalize_station(station)
        self.logger.debug(f"Fetched station {station_id}: {normalized['name']}")
        return normalized

    async def _fetch_top_stations(self, limit: int = 500) -> List[Dict[str, Any]]:
        """
        Gets most popular stations via the API
        (based on click count)

        Args:
            limit: Number of stations to fetch (default: 500)

        Returns:
            List of normalized and filtered stations

        Raises:
            NetworkUnavailableError: If all mirrors are unreachable
        """
        stations = await self._request(
            f"stations/topclick/{limit}",
            timeout=15,
        )
        if not stations:
            return []

        self.logger.debug(f"Fetched {len(stations)} top stations")

        valid_stations = [
            self._normalize_station(station)
            for station in stations
            if self._is_valid_station(station)
        ]

        deduplicated_stations = await self._deduplicate_stations(valid_stations)

        self.logger.info(f"Returning {len(deduplicated_stations)} top stations")

        return deduplicated_stations

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

    @handle_errors(default=[])
    async def find_alternative_urls(
        self, station: Dict[str, Any], exclude_url: str = ""
    ) -> List[Dict[str, Any]]:
        """
        Finds alternative URLs for the *same* station when its primary URL fails.

        Two genuinely different stations can share a name (e.g. a French and a
        Brazilian "Radio X"), so matching by name alone risks silently switching
        the listener to a different broadcaster. To prevent that (WI-4) we:
          - never accept an alternative from a *different* country than the origin,
          - rank survivors by affinity to the origin — same `stationuuid`, then
            same URL host, then same country — before audio quality.

        Args:
            station: The origin station (needs `name`; uses `id`, `countrycode`,
                     `url` to anchor the match to the same broadcaster)
            exclude_url: URL to exclude from results (the failing primary URL)

        Returns:
            List of alternative stations, most-trustworthy-and-highest-quality first
        """
        station_name = (station.get('name') or '').strip()
        if not station_name:
            return []

        origin_uuid = station.get('id')
        origin_cc = station.get('countrycode') or ''
        origin_host = urlparse(exclude_url or station.get('url') or '').hostname

        # Search by exact name. Keep the raw same-name variants (no dedup) so the
        # affinity ranking below can distinguish broadcasters (WI-4) — dedup-by-name
        # would merge them into a single entry and make the ranking a no-op.
        search_results = await self._fetch_stations_by_query(station_name, deduplicate=False)

        # Keep exact-name matches with a usable, non-excluded URL.
        alternatives = [
            s for s in search_results
            if s.get('name', '').lower().strip() == station_name.lower()
            and s.get('url') and s.get('url') != exclude_url
        ]

        # Anti "other station": drop any variant from a different country. A blank
        # country on either side is treated as compatible (federated data is spotty).
        if origin_cc:
            alternatives = [
                s for s in alternatives
                if not s.get('countrycode') or s.get('countrycode') == origin_cc
            ]

        def _affinity(s: Dict[str, Any]) -> int:
            if origin_uuid and s.get('id') == origin_uuid:
                return 3  # same radio-browser entry (url_resolved drifted)
            if origin_host and urlparse(s.get('url') or '').hostname == origin_host:
                return 2  # same streaming host → same broadcaster
            if origin_cc and s.get('countrycode') == origin_cc:
                return 1  # same country name-match
            return 0

        # Affinity dominates (trust), then quality-first ranking within a tier.
        alternatives.sort(key=lambda s: (_affinity(s), self._ranking_key(s)), reverse=True)

        self.logger.debug(
            f"Found {len(alternatives)} alternative URLs for '{station_name}' "
            f"(cc={origin_cc or '?'}, host={origin_host or '?'}, "
            f"excluded: {exclude_url[:50] if exclude_url else 'none'})"
        )

        return alternatives

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

        # `or 0` (not `.get(k, 0)`): radio-browser can send an explicit null for a
        # numeric field, and `.get` only defaults a *missing* key — a null would
        # otherwise reach the arithmetic in `_effective_bitrate` / `score`.
        votes = station.get('votes') or 0
        clickcount = station.get('clickcount') or 0
        return {
            'id': station.get('stationuuid'),
            'name': station.get('name'),
            'url': station.get('url_resolved'),
            'country': station.get('country', 'Unknown'),
            'countrycode': (station.get('countrycode') or '').upper(),
            'genre': extract_valid_genre(station.get('tags', '')),
            'favicon': favicon,
            'bitrate': station.get('bitrate') or 0,
            'codec': station.get('codec') or 'Unknown',
            # WI-3 stream-selection signals: `hls` proxies metadata-likelihood
            # (Icecast/`hls=0` more often carries ICY StreamTitle than HLS),
            # `ssl_error` is a reliability penalty. Both feed _ranking_key.
            'hls': station.get('hls') or 0,
            'ssl_error': station.get('ssl_error') or 0,
            'votes': votes,
            'clickcount': clickcount,
            'score': votes + clickcount
        }

    # Lossy codecs that sound clearly better than MP3 at the same bitrate; used
    # to normalize bitrate across codecs so selection is quality-first (locked
    # design decision — never trade real audio quality for metadata).
    _EFFICIENT_CODECS = frozenset({'AAC', 'AAC+', 'OPUS'})

    def _effective_bitrate(self, station: Dict[str, Any]) -> float:
        """Bitrate normalized by codec efficiency (AAC/Opus ≈ 1.5× MP3)."""
        codec = (station.get('codec') or '').upper()
        factor = 1.5 if codec in self._EFFICIENT_CODECS else 1.0
        return station.get('bitrate', 0) * factor

    def _ranking_key(self, station: Dict[str, Any]) -> tuple:
        """Quality-first ordering key for a station variant (sort reverse=True).

        Priority (locked design): (1) audio quality = codec-normalized bitrate,
        (2) metadata-likelihood = prefer non-HLS (Icecast carries StreamTitle
        more often), (3) reliability = no SSL error, then popularity score.
        """
        return (
            self._effective_bitrate(station),
            0 if station.get('hls') else 1,
            0 if station.get('ssl_error') else 1,
            station.get('score', 0),
        )

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
            List of deduplicated stations (preserves original order)
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

                # 1. Find version with best audio stream (quality-first, then
                #    metadata-likelihood, reliability, popularity — see _ranking_key)
                best_audio = max(versions, key=self._ranking_key)

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
                        f"Merged {len(versions)} versions of '{versions[0]['name']}' "
                        f"(score={best_audio.get('score', 0)}, bitrate={best_audio.get('bitrate', 0)}, "
                        f"favicon_quality={best_favicon_quality})"
                    )

        self.logger.debug(f"Deduplication: {len(stations)} → {len(deduplicated)} stations")

        return deduplicated

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

        Raises:
            NetworkUnavailableError: If all mirrors are unreachable
        """
        self.logger.debug(f"API call [{description}]: {params}")

        stations = await self._request(
            "stations/search",
            params=params,
            timeout=15,
        )
        if not stations:
            return []

        self.logger.debug(f"Fetched {len(stations)} raw stations [{description}]")

        valid_stations = [
            self._normalize_station(station)
            for station in stations
            if self._is_valid_station(station)
        ]

        deduplicated_stations = await self._deduplicate_stations(valid_stations)

        self.logger.info(
            f"[{description}] {len(stations)} raw → "
            f"{len(valid_stations)} valid → "
            f"{len(deduplicated_stations)} deduplicated"
        )

        return deduplicated_stations

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
        filters_desc = []
        if query:
            filters_desc.append(f"query='{query}'")
        if country:
            filters_desc.append(f"country='{country}'")
        if genre:
            filters_desc.append(f"genre='{genre}'")

        search_desc = ", ".join(filters_desc) if filters_desc else "no filters (top stations)"
        self.logger.info(f"Search: {search_desc}")

        # Special case: no filters → top stations
        try:
            if not query and not country and not genre:
                self.logger.debug("No filters, loading top 500 stations")
                all_stations = await self._fetch_top_stations(limit=500)
            else:
                search_params = self._build_search_params(query, country, genre)

                all_stations = await self._fetch_with_search_params(search_params, search_desc)
        except NetworkUnavailableError:
            self.logger.info("Network unavailable for station search")
            return {"stations": [], "total": 0, "network_error": True}

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

            if filtered_custom:
                all_stations = all_stations + filtered_custom
                self.logger.info(f"Added {len(filtered_custom)} manually-added custom station(s)")

        # Total before limit
        total = len(all_stations)

        limited_results = all_stations[:limit]

        self.logger.info(f"Final: {total} stations (returning {len(limited_results)})")

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
        if station_id.startswith("custom_") and self.station_manager:
            custom_station = self.station_manager.get_custom_station_by_id(station_id)
            if custom_station:
                return custom_station

        station = await self.fetch_remote_station(station_id)

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

        if custom_ids and self.station_manager:
            for station_id in custom_ids:
                custom_station = self.station_manager.get_custom_station_by_id(station_id)
                if custom_station:
                    stations.append(custom_station)

        for station_id in regular_ids:
            station = await self.fetch_remote_station(station_id)

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

    @handle_errors(default=False, level='debug')
    async def increment_station_clicks(self, station_id: str) -> bool:
        """
        Increments click counter for a station

        The Radio Browser API uses this counter for ranking.

        Args:
            station_id: Station UUID

        Returns:
            True if successful
        """
        result = await self._request(f"url/{station_id}", timeout=5)
        success = isinstance(result, dict) and result.get("ok") is True
        if success:
            self.logger.debug(f"Incremented click count for station {station_id}")
        return success

    async def get_available_countries(self) -> List[Dict[str, Any]]:
        """
        Gets list of all available countries from Radio Browser API
        With 24h cache + stale-cache fallback when all mirrors are unreachable

        Uses `hidebroken=true` to match the station counts displayed on
        radio-browser.info (broken/offline stations excluded).

        Returns:
            List of countries with ISO 3166-1 alpha-2 code, name and station count.
            Format: [{"name": "France", "iso_3166_1": "FR", "stationcount": 2345}, ...]
            Frontend translates and sorts via Intl.DisplayNames using iso_3166_1.
        """
        # Check cache first
        if self._countries_cache and self._countries_cache_timestamp:
            cache_age = datetime.now() - self._countries_cache_timestamp
            if cache_age < self._countries_cache_duration:
                self.logger.debug(f"Using cached countries ({len(self._countries_cache)} countries, age: {cache_age})")
                return self._countries_cache

        try:
            countries = await self._request("countries", params={"hidebroken": "true"}, timeout=10)
        except NetworkUnavailableError as e:
            # All mirrors failed — fall back to stale cache if we have one
            if self._countries_cache:
                cache_age = datetime.now() - self._countries_cache_timestamp if self._countries_cache_timestamp else None
                self.logger.info(
                    f"API unreachable ({e}), using stale cache "
                    f"({len(self._countries_cache)} countries, age: {cache_age})"
                )
                return self._countries_cache
            self.logger.error(f"API unreachable ({e}) and no cache available, returning empty list")
            return []

        if not countries:
            return self._countries_cache or []

        # Keep countries with at least 20 valid stations (matches the threshold
        # surfaced on radio-browser.info). Drop entries without an ISO code:
        # the frontend relies on it for Intl.DisplayNames translation.
        filtered_countries = [
            {
                "name": c.get("name", ""),
                "iso_3166_1": c.get("iso_3166_1", "").upper(),
                "stationcount": c.get("stationcount", 0),
            }
            for c in countries
            if c.get("stationcount", 0) >= 20
            and c.get("name")
            and c.get("iso_3166_1")
        ]

        # No stationcount sort: the frontend sorts alphabetically on the
        # translated name (locale-aware), which can only happen client-side.
        self._countries_cache = filtered_countries
        self._countries_cache_timestamp = datetime.now()

        self.logger.info(f"Fetched and cached {len(filtered_countries)} countries from Radio Browser API")
        return filtered_countries
