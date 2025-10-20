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

    def __init__(self, cache_duration_minutes: int = 60, station_manager=None):
        self.logger = logging.getLogger(__name__)
        self.session: Optional[aiohttp.ClientSession] = None
        self.cache_duration = timedelta(minutes=cache_duration_minutes)
        self.station_manager = station_manager

        # Cache avec timestamp
        self._stations_cache: Dict[str, Any] = {}
        self._cache_timestamp: Optional[datetime] = None

        # Cache par pays pour recherches dynamiques (country_name -> (timestamp, stations))
        self._country_cache: Dict[str, tuple[datetime, List[Dict[str, Any]]]] = {}

        # Cache pour la liste des pays disponibles (valide 24h)
        self._countries_cache: List[Dict[str, Any]] = []
        self._countries_cache_timestamp: Optional[datetime] = None
        self._countries_cache_duration = timedelta(hours=24)

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

                # Toujours mettre à jour le favicon si le nouveau est de meilleure qualité
                new_favicon_quality = self._get_favicon_quality(normalized.get('favicon', ''))
                existing_favicon_quality = self._get_favicon_quality(existing.get('favicon', ''))

                if new_favicon_quality > existing_favicon_quality:
                    existing['favicon'] = normalized['favicon']

                # Remplacer la station entière si meilleure qualité globale
                if self._compare_station_quality(normalized, existing) > 0:
                    # Garder le meilleur favicon déjà trouvé
                    best_favicon = existing['favicon'] if existing_favicon_quality > new_favicon_quality else normalized['favicon']
                    normalized['favicon'] = best_favicon
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

    def _get_favicon_quality(self, url: str) -> int:
        """
        Évalue la qualité d'un favicon pour prioriser les meilleures sources

        Args:
            url: URL du favicon

        Returns:
            Score de qualité (plus élevé = meilleur)
        """
        if not url:
            return -1

        url_lower = url.lower()

        # Rejeter les URLs qui causent des problèmes CORS ou sont temporaires
        problematic_domains = [
            'facebook.com', 'fbcdn.net', 'dropbox.com',
            'googledrive.com', 'onedrive.com', 'sharepoint.com',
            'syncusercontent.com'
        ]

        if any(domain in url_lower for domain in problematic_domains):
            return 0  # Très mauvaise qualité

        # Rejeter les URLs avec tokens/timestamps (souvent temporaires)
        if any(param in url_lower for param in ['?timestamp=', '?token=', '?signature=']):
            return 0

        # Rejeter les pages Wikipedia (pas des images directes)
        if 'wikipedia.org/wiki/' in url_lower or '#/media/' in url_lower:
            return 5  # Très mauvaise qualité (page web, pas image)

        # favicon.ico = faible qualité
        if 'favicon.ico' in url_lower:
            return 10

        # Préférer les images directes de sources fiables
        quality = 50

        # Bonus pour Wikimedia (images directes, pas Wikipedia pages)
        if 'upload.wikimedia.org' in url_lower:
            quality += 100

        # Bonus pour les formats d'image
        if '.svg' in url_lower:
            quality += 30
        elif '.png' in url_lower:
            quality += 20
        elif '.webp' in url_lower:
            quality += 20
        elif '.jpg' in url_lower or '.jpeg' in url_lower:
            quality += 15

        return quality

    def _normalize_station(self, station: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalise une station depuis le format API vers format Milo

        Args:
            station: Station au format Radio Browser API

        Returns:
            Station normalisée
        """
        # Nettoyer le favicon (éviter les URLs problématiques)
        favicon = station.get('favicon', '')
        if favicon:
            # Filtrer les favicons de mauvaise qualité
            if self._get_favicon_quality(favicon) < 10:
                favicon = ''
            # Note: Pas de conversion HTTP→HTTPS, le proxy backend gérera les redirections

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
        limit: int = 10000
    ) -> List[Dict[str, Any]]:
        """
        Recherche des stations avec filtres (inclut les stations personnalisées)

        Args:
            query: Terme de recherche (nom de station)
            country: Filtre pays
            genre: Filtre genre
            limit: Nombre max de résultats

        Returns:
            Liste des stations matchant les critères
        """
        all_stations = []

        # Si un filtre country est spécifié, chercher spécifiquement ce pays
        if country:
            # Vérifier le cache par pays
            country_lower = country.lower()
            if country_lower in self._country_cache:
                timestamp, cached_stations = self._country_cache[country_lower]
                # Vérifier si le cache est encore valide
                if datetime.now() - timestamp < self.cache_duration:
                    self.logger.debug(f"Using cached stations for country: {country}")
                    all_stations = cached_stations
                else:
                    # Cache expiré, recharger
                    all_stations = await self._fetch_stations_by_country_name(country)
                    self._country_cache[country_lower] = (datetime.now(), all_stations)
            else:
                # Pas en cache, faire la requête
                self.logger.info(f"Fetching stations for country: {country}")
                all_stations = await self._fetch_stations_by_country_name(country)
                self._country_cache[country_lower] = (datetime.now(), all_stations)
        else:
            # Pas de filtre pays, charger les pays par défaut
            all_stations = await self.load_all_stations()

        # Ajouter les stations personnalisées
        if self.station_manager:
            custom_stations = self.station_manager.get_custom_stations()
            # Les stations personnalisées sont ajoutées en premier (priorité)
            all_stations = custom_stations + all_stations

        # Filtrer localement par query
        results = all_stations
        if query:
            query_lower = query.lower()
            results = [
                s for s in results
                if query_lower in s['name'].lower() or query_lower in s['genre'].lower()
            ]

        # Filtrer par genre
        if genre:
            genre_lower = genre.lower()
            results = [s for s in results if genre_lower in s['genre'].lower()]

        # Enrichir avec les images personnalisées
        if self.station_manager:
            results = self.station_manager.enrich_with_custom_images(results)

        # Limiter résultats
        return results[:limit]

    async def get_station_by_id(self, station_id: str) -> Optional[Dict[str, Any]]:
        """
        Récupère une station par son ID (inclut les stations personnalisées)

        Args:
            station_id: UUID de la station

        Returns:
            Station ou None si introuvable
        """
        # Vérifier d'abord si c'est une station personnalisée
        if station_id.startswith("custom_") and self.station_manager:
            custom_station = self.station_manager.get_custom_station_by_id(station_id)
            if custom_station:
                return custom_station

        # Chercher dans le cache d'abord
        if self._is_cache_valid() and station_id in self._stations_cache:
            station = self._stations_cache[station_id]
        else:
            # Sinon, recharger toutes les stations
            await self.load_all_stations()
            station = self._stations_cache.get(station_id)

        # Enrichir avec les images personnalisées si la station existe
        if station and self.station_manager:
            # Appliquer l'image personnalisée si elle existe
            station = self.station_manager.enrich_with_custom_images([station])[0]

        return station

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

    async def _fetch_stations_by_country_name(self, country_name: str) -> List[Dict[str, Any]]:
        """
        Récupère toutes les stations d'un pays via l'API (par nom de pays)

        Args:
            country_name: Nom du pays (ex: "France", "Japan")

        Returns:
            Liste des stations normalisées et filtrées
        """
        await self._ensure_session()

        try:
            # Utiliser l'endpoint de recherche avec filtre country
            url = f"{self.BASE_URL}/stations/search"
            params = {"country": country_name, "limit": 10000}  # Limite haute pour obtenir toutes les stations

            async with self.session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    self.logger.warning(f"API error for country {country_name}: {resp.status}")
                    return []

                stations = await resp.json()
                self.logger.debug(f"Fetched {len(stations)} stations from {country_name}")

                # Filtrer et normaliser
                valid_stations = []
                for station in stations:
                    if self._is_valid_station(station):
                        normalized = self._normalize_station(station)
                        valid_stations.append(normalized)

                # Trier par score
                valid_stations.sort(key=lambda s: s.get('score', 0), reverse=True)

                return valid_stations

        except asyncio.TimeoutError:
            self.logger.error(f"Timeout fetching stations for {country_name}")
            return []
        except Exception as e:
            self.logger.error(f"Error fetching stations for {country_name}: {e}")
            return []

    async def get_available_countries(self) -> List[Dict[str, Any]]:
        """
        Récupère la liste de tous les pays disponibles depuis Radio Browser API
        Avec cache 24h + retry logic + fallback

        Returns:
            Liste des pays avec nom et nombre de stations
            Format: [{"name": "France", "stationcount": 2345}, ...]
        """
        # Vérifier le cache d'abord
        if self._countries_cache and self._countries_cache_timestamp:
            cache_age = datetime.now() - self._countries_cache_timestamp
            if cache_age < self._countries_cache_duration:
                self.logger.debug(f"Using cached countries ({len(self._countries_cache)} countries, age: {cache_age})")
                return self._countries_cache

        # Cache expiré ou absent, essayer de fetch
        await self._ensure_session()

        # Tenter 3 fois avec retry (timeout plus long pour le premier appel)
        for attempt in range(1, 4):
            try:
                # Premier appel: timeout 20s (API peut être lente au démarrage)
                # Appels suivants: timeout 10s
                timeout_seconds = 20 if attempt == 1 else 10
                self.logger.info(f"Attempt {attempt}/3 fetching countries from Radio Browser API (timeout: {timeout_seconds}s)...")
                url = f"{self.BASE_URL}/countries"
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=timeout_seconds)) as resp:
                    if resp.status != 200:
                        self.logger.warning(f"API error fetching countries (attempt {attempt}): HTTP {resp.status}")
                        if attempt < 3:
                            await asyncio.sleep(2)  # Attendre 2s avant retry
                            continue
                        # Dernière tentative échouée, utiliser fallback
                        break

                    countries = await resp.json()
                    # Filtrer les pays avec au moins 80 stations
                    filtered_countries = [
                        {"name": c.get("name", ""), "stationcount": c.get("stationcount", 0)}
                        for c in countries
                        if c.get("stationcount", 0) >= 80 and c.get("name")
                    ]

                    # Trier par nombre de stations (décroissant)
                    sorted_countries = sorted(
                        filtered_countries,
                        key=lambda c: c["stationcount"],
                        reverse=True
                    )

                    # Mettre en cache
                    self._countries_cache = sorted_countries
                    self._countries_cache_timestamp = datetime.now()

                    self.logger.info(f"✅ Fetched and cached {len(sorted_countries)} countries from Radio Browser API")
                    return sorted_countries

            except asyncio.TimeoutError:
                self.logger.warning(f"Timeout fetching countries list (attempt {attempt}/3)")
                if attempt < 3:
                    await asyncio.sleep(2)  # Attendre 2s avant retry
                    continue
            except Exception as e:
                self.logger.warning(f"Error fetching countries (attempt {attempt}/3): {e}")
                if attempt < 3:
                    await asyncio.sleep(2)  # Attendre 2s avant retry
                    continue

        # Toutes les tentatives ont échoué
        # Utiliser le cache périmé s'il existe
        if self._countries_cache:
            cache_age = datetime.now() - self._countries_cache_timestamp if self._countries_cache_timestamp else None
            self.logger.warning(f"⚠️ API unreachable, using stale cache ({len(self._countries_cache)} countries, age: {cache_age})")
            return self._countries_cache

        # Pas de cache, retourner fallback par défaut
        fallback_countries = [
            {"name": "United States", "stationcount": 0},
            {"name": "Germany", "stationcount": 0},
            {"name": "France", "stationcount": 0},
            {"name": "United Kingdom", "stationcount": 0},
            {"name": "Spain", "stationcount": 0},
            {"name": "Italy", "stationcount": 0}
        ]
        self.logger.warning(f"⚠️ API unreachable and no cache, returning fallback ({len(fallback_countries)} countries)")
        return fallback_countries
