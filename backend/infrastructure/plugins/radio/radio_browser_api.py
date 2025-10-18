"""
Client API Radio Browser avec cache pour limiter les appels
"""
import asyncio
import aiohttp
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta


class RadioBrowserAPI:
    """
    Client async pour l'API Radio Browser

    API Doc: https://api.radio-browser.info/
    Utilise all.api.radio-browser.info pour répartition de charge automatique
    """

    BASE_URL = "https://all.api.radio-browser.info/json"

    # Codes pays pour filtrage
    COUNTRY_CODES = {
        "France": "FR",
        "United Kingdom": "GB",
        "United States": "US",
        "Germany": "DE",
        "Spain": "ES",
        "Italy": "IT"
    }

    def __init__(self, cache_duration_minutes: int = 60):
        self.logger = logging.getLogger(__name__)
        self.session: Optional[aiohttp.ClientSession] = None
        self.cache_duration = timedelta(minutes=cache_duration_minutes)

        # Cache avec timestamp
        self._stations_cache: Dict[str, Any] = {}
        self._cache_timestamp: Optional[datetime] = None

    async def _ensure_session(self) -> None:
        """Crée la session aiohttp si nécessaire"""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                headers={
                    'User-Agent': 'Milo/1.0',  # API Radio Browser demande un User-Agent
                }
            )

    async def close(self) -> None:
        """Ferme la session aiohttp"""
        if self.session and not self.session.closed:
            await self.session.close()
            self.session = None

    def _is_cache_valid(self) -> bool:
        """Vérifie si le cache est encore valide"""
        if not self._cache_timestamp or not self._stations_cache:
            return False
        return datetime.now() - self._cache_timestamp < self.cache_duration

    async def _fetch_stations_by_country(self, country_code: str) -> List[Dict[str, Any]]:
        """
        Récupère toutes les stations d'un pays via l'API

        Args:
            country_code: Code pays ISO (ex: "FR", "GB")

        Returns:
            Liste des stations
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

    async def load_all_stations(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Charge toutes les stations des pays supportés avec cache

        Args:
            force_refresh: Force le rechargement même si cache valide

        Returns:
            Liste de toutes les stations filtrées et dédupliquées
        """
        # Utiliser le cache si valide
        if not force_refresh and self._is_cache_valid():
            self.logger.debug("Returning cached stations")
            return list(self._stations_cache.values())

        self.logger.info("Loading stations from Radio Browser API...")

        # Fetch toutes les stations en parallèle
        tasks = [
            self._fetch_stations_by_country(code)
            for code in self.COUNTRY_CODES.values()
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Fusionner et dédupliquer
        all_stations = []
        for result in results:
            if isinstance(result, list):
                all_stations.extend(result)

        # Filtrer et normaliser
        stations_map = {}
        for station in all_stations:
            # Filtrer les stations invalides
            if not self._is_valid_station(station):
                continue

            # Normaliser et dédupliquer par nom
            normalized = self._normalize_station(station)
            station_key = normalized['name'].lower().strip()

            # Garder la meilleure version si doublon
            if station_key not in stations_map:
                stations_map[station_key] = normalized
            else:
                existing = stations_map[station_key]
                if self._compare_station_quality(normalized, existing) > 0:
                    stations_map[station_key] = normalized

        # Trier par popularité (votes + clics)
        sorted_stations = sorted(
            stations_map.values(),
            key=lambda s: s.get('score', 0),
            reverse=True
        )

        # Mettre en cache
        self._stations_cache = {s['id']: s for s in sorted_stations}
        self._cache_timestamp = datetime.now()

        self.logger.info(f"Loaded and cached {len(sorted_stations)} unique stations")
        return sorted_stations

    def _is_valid_station(self, station: Dict[str, Any]) -> bool:
        """
        Vérifie si une station est valide

        Args:
            station: Dict station depuis API

        Returns:
            True si station valide
        """
        return (
            station.get('url_resolved') and
            station.get('codec') != 'UNKNOWN' and
            station.get('lastcheckok') == 1 and
            station.get('name')
        )

    def _normalize_station(self, station: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalise une station depuis le format API vers format Milo

        Args:
            station: Station au format Radio Browser API

        Returns:
            Station normalisée
        """
        # Nettoyer le favicon (éviter CORS issues)
        favicon = station.get('favicon', '')
        if favicon:
            # Filtrer les domaines problématiques
            blocked_domains = ['facebook.com', 'fbcdn.net', 'dropbox.com', 'googledrive.com']
            if any(domain in favicon.lower() for domain in blocked_domains):
                favicon = ''

        return {
            'id': station.get('stationuuid'),
            'name': station.get('name'),
            'url': station.get('url_resolved'),
            'country': station.get('country', 'Unknown'),
            'genre': (station.get('tags', 'Variety').split(',')[0].strip() if station.get('tags') else 'Variety'),
            'favicon': favicon,
            'bitrate': station.get('bitrate', 0),
            'codec': station.get('codec', 'Unknown'),
            'votes': station.get('votes', 0),
            'clickcount': station.get('clickcount', 0),
            'score': station.get('votes', 0) + station.get('clickcount', 0)
        }

    def _compare_station_quality(self, station1: Dict[str, Any], station2: Dict[str, Any]) -> int:
        """
        Compare la qualité de deux stations pour dédupliquer

        Args:
            station1: Première station
            station2: Deuxième station

        Returns:
            > 0 si station1 meilleure, < 0 si station2 meilleure, 0 si égales
        """
        # Comparer popularité (score = votes + clics)
        score1 = station1.get('score', 0)
        score2 = station2.get('score', 0)

        if score1 > score2 * 1.2:  # 20% de différence significative
            return 1
        elif score2 > score1 * 1.2:
            return -1

        # Si scores similaires, comparer bitrate
        bitrate1 = station1.get('bitrate', 0)
        bitrate2 = station2.get('bitrate', 0)

        return bitrate1 - bitrate2

    async def search_stations(
        self,
        query: str = "",
        country: str = "",
        genre: str = "",
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Recherche des stations avec filtres

        Args:
            query: Terme de recherche (nom de station)
            country: Filtre pays
            genre: Filtre genre
            limit: Nombre max de résultats

        Returns:
            Liste des stations matchant les critères
        """
        # Charger toutes les stations (depuis cache si possible)
        all_stations = await self.load_all_stations()

        # Filtrer localement
        results = all_stations

        if query:
            query_lower = query.lower()
            results = [
                s for s in results
                if query_lower in s['name'].lower() or query_lower in s['genre'].lower()
            ]

        if country:
            country_lower = country.lower()
            results = [s for s in results if country_lower in s['country'].lower()]

        if genre:
            genre_lower = genre.lower()
            results = [s for s in results if genre_lower in s['genre'].lower()]

        # Limiter résultats
        return results[:limit]

    async def get_station_by_id(self, station_id: str) -> Optional[Dict[str, Any]]:
        """
        Récupère une station par son ID

        Args:
            station_id: UUID de la station

        Returns:
            Station ou None si introuvable
        """
        # Chercher dans le cache d'abord
        if self._is_cache_valid() and station_id in self._stations_cache:
            return self._stations_cache[station_id]

        # Sinon, recharger toutes les stations
        await self.load_all_stations()

        return self._stations_cache.get(station_id)

    async def increment_station_clicks(self, station_id: str) -> bool:
        """
        Incrémente le compteur de clicks pour une station

        L'API Radio Browser utilise ce compteur pour le ranking.

        Args:
            station_id: UUID de la station

        Returns:
            True si succès
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
