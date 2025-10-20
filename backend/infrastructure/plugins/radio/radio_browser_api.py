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

    async def _fetch_stations_by_query(self, query: str) -> List[Dict[str, Any]]:
        """
        Récupère toutes les stations correspondant à une recherche via l'API
        Recherche globale parmi toutes les stations de tous les pays

        Args:
            query: Terme de recherche (nom de station)

        Returns:
            Liste des stations normalisées et filtrées
        """
        await self._ensure_session()

        try:
            # Utiliser l'endpoint de recherche global
            url = f"{self.BASE_URL}/stations/search"
            params = {"name": query, "limit": 10000}  # Limite haute pour obtenir tous les résultats

            async with self.session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    self.logger.warning(f"API error for query '{query}': {resp.status}")
                    return []

                stations = await resp.json()
                self.logger.debug(f"Fetched {len(stations)} stations for query '{query}'")

                # Filtrer et normaliser
                valid_stations = []
                for station in stations:
                    if self._is_valid_station(station):
                        normalized = self._normalize_station(station)
                        valid_stations.append(normalized)

                # Dédupliquer et trier par score
                deduplicated_stations = self._deduplicate_stations(valid_stations)

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
        Récupère une station par son ID via l'API

        Args:
            station_id: UUID de la station

        Returns:
            Station normalisée ou None si introuvable
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

                station = stations[0]  # L'API retourne une liste avec 1 élément

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
        Récupère les stations les plus populaires via l'API
        (basé sur les votes)

        Args:
            limit: Nombre de stations à récupérer (défaut: 500)

        Returns:
            Liste des stations normalisées et filtrées
        """
        await self._ensure_session()

        try:
            # Utiliser l'endpoint topvote pour les stations les plus votées
            url = f"{self.BASE_URL}/stations/topvote/{limit}"

            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    self.logger.warning(f"API error for top stations: {resp.status}")
                    return []

                stations = await resp.json()
                self.logger.debug(f"Fetched {len(stations)} top stations")

                # Filtrer et normaliser
                valid_stations = []
                for station in stations:
                    if self._is_valid_station(station):
                        normalized = self._normalize_station(station)
                        valid_stations.append(normalized)

                # Dédupliquer (au cas où)
                deduplicated_stations = self._deduplicate_stations(valid_stations)

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

    def _deduplicate_stations(self, stations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Déduplique une liste de stations par nom (case-insensitive)
        Garde la meilleure version basée sur score et bitrate

        Args:
            stations: Liste de stations normalisées

        Returns:
            Liste de stations dédupliquées et triées par score
        """
        stations_map = {}

        for station in stations:
            station_key = station['name'].lower().strip()

            # Garder la meilleure version si doublon
            if station_key not in stations_map:
                stations_map[station_key] = station
            else:
                existing = stations_map[station_key]

                # Toujours mettre à jour le favicon si le nouveau est de meilleure qualité
                new_favicon_quality = self._get_favicon_quality(station.get('favicon', ''))
                existing_favicon_quality = self._get_favicon_quality(existing.get('favicon', ''))

                if new_favicon_quality > existing_favicon_quality:
                    existing['favicon'] = station['favicon']

                # Remplacer la station entière si meilleure qualité globale
                if self._compare_station_quality(station, existing) > 0:
                    # Garder le meilleur favicon déjà trouvé
                    best_favicon = existing['favicon'] if existing_favicon_quality > new_favicon_quality else station['favicon']
                    station['favicon'] = best_favicon
                    stations_map[station_key] = station

        # Trier par popularité (votes + clics)
        sorted_stations = sorted(
            stations_map.values(),
            key=lambda s: s.get('score', 0),
            reverse=True
        )

        return sorted_stations

    async def search_stations(
        self,
        query: str = "",
        country: str = "",
        genre: str = "",
        limit: int = 10000
    ) -> Dict[str, Any]:
        """
        Recherche des stations avec filtres (inclut les stations personnalisées)

        Args:
            query: Terme de recherche (nom de station)
            country: Filtre pays
            genre: Filtre genre
            limit: Nombre max de résultats

        Returns:
            Dict avec stations et total: {stations: [...], total: int}
        """
        all_stations = []

        # Déterminer quelle méthode de fetch utiliser selon les filtres actifs
        # Les genres sont maintenant cherchés via l'API (paramètre tag) au lieu de filtrer localement

        if country and genre and query:
            # Tous les filtres : country + genre + query
            # Note: L'API Radio Browser ne supporte pas les 3 en même temps
            # On fait country + genre, puis on filtre localement par query
            self.logger.info(f"Fetching stations for country: {country}, genre: {genre}, query: {query}")
            all_stations = await self._fetch_stations_by_country_and_genre(country, genre)
            # Filtrage local par query
            query_lower = query.lower()
            all_stations = [
                s for s in all_stations
                if query_lower in s['name'].lower() or query_lower in s['genre'].lower()
            ]
        elif country and genre:
            # Pays + Genre
            self.logger.info(f"Fetching stations for country: {country}, genre: {genre}")
            all_stations = await self._fetch_stations_by_country_and_genre(country, genre)
        elif country and query:
            # Pays + Recherche (déjà supporté nativement par l'API)
            self.logger.info(f"Fetching stations for country: {country}, query: {query}")
            # Vérifier cache par pays
            country_lower = country.lower()
            if country_lower in self._country_cache:
                timestamp, cached_stations = self._country_cache[country_lower]
                if datetime.now() - timestamp < self.cache_duration:
                    self.logger.debug(f"Using cached stations for country: {country}")
                    all_stations = cached_stations
                else:
                    all_stations = await self._fetch_stations_by_country_name(country)
                    self._country_cache[country_lower] = (datetime.now(), all_stations)
            else:
                all_stations = await self._fetch_stations_by_country_name(country)
                self._country_cache[country_lower] = (datetime.now(), all_stations)

            # Filtrage local par query
            query_lower = query.lower()
            all_stations = [
                s for s in all_stations
                if query_lower in s['name'].lower() or query_lower in s['genre'].lower()
            ]
        elif genre and query:
            # Genre + Recherche
            self.logger.info(f"Fetching stations for genre: {genre}, query: {query}")
            all_stations = await self._fetch_stations_by_query_and_genre(query, genre)
        elif country:
            # Pays seul
            country_lower = country.lower()
            if country_lower in self._country_cache:
                timestamp, cached_stations = self._country_cache[country_lower]
                if datetime.now() - timestamp < self.cache_duration:
                    self.logger.debug(f"Using cached stations for country: {country}")
                    all_stations = cached_stations
                else:
                    all_stations = await self._fetch_stations_by_country_name(country)
                    self._country_cache[country_lower] = (datetime.now(), all_stations)
            else:
                self.logger.info(f"Fetching stations for country: {country}")
                all_stations = await self._fetch_stations_by_country_name(country)
                self._country_cache[country_lower] = (datetime.now(), all_stations)
        elif genre:
            # Genre seul (maintenant cherché via l'API au lieu de filtrer localement)
            self.logger.info(f"Fetching stations for genre: {genre}")
            all_stations = await self._fetch_stations_by_genre(genre)
        elif query:
            # Recherche seule
            self.logger.info(f"Global search for query: {query}")
            all_stations = await self._fetch_stations_by_query(query)
        else:
            # Aucun filtre : top 500 stations
            self.logger.debug("No filters, loading top 500 stations")
            all_stations = await self._fetch_top_stations(limit=500)

        # Ajouter les stations personnalisées
        if self.station_manager:
            custom_stations = self.station_manager.get_custom_stations()
            # Les stations personnalisées sont ajoutées en premier (priorité)
            all_stations = custom_stations + all_stations

        # Enrichir avec les images personnalisées
        if self.station_manager:
            all_stations = self.station_manager.enrich_with_custom_images(all_stations)

        # Total avant limitation
        total = len(all_stations)

        # Limiter résultats
        limited_results = all_stations[:limit]

        return {
            "stations": limited_results,
            "total": total
        }

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
            # Sinon, récupérer directement depuis l'API
            station = await self._fetch_station_by_id(station_id)

            # Mettre en cache si trouvée
            if station:
                self._stations_cache[station_id] = station

        # Enrichir avec les images personnalisées si la station existe
        if station and self.station_manager:
            # Appliquer l'image personnalisée si elle existe
            station = self.station_manager.enrich_with_custom_images([station])[0]

        return station

    async def get_stations_by_ids(self, station_ids: List[str]) -> List[Dict[str, Any]]:
        """
        Récupère plusieurs stations par leurs IDs en batch (inclut les stations personnalisées)
        Pour les stations avec favicons manquants/mauvais, fait une recherche par nom
        pour trouver de meilleures versions. Applique la déduplication finale.

        Args:
            station_ids: Liste des UUIDs de stations

        Returns:
            Liste des stations trouvées avec favicons améliorés
        """
        if not station_ids:
            return []

        stations = []
        stations_needing_better_favicon = []

        # Séparer les stations custom des stations normales
        custom_ids = [sid for sid in station_ids if sid.startswith("custom_")]
        regular_ids = [sid for sid in station_ids if not sid.startswith("custom_")]

        # Récupérer les stations custom
        if custom_ids and self.station_manager:
            for station_id in custom_ids:
                custom_station = self.station_manager.get_custom_station_by_id(station_id)
                if custom_station:
                    stations.append(custom_station)

        # Récupérer les stations normales
        for station_id in regular_ids:
            # Chercher dans le cache d'abord
            if self._is_cache_valid() and station_id in self._stations_cache:
                station = self._stations_cache[station_id]
            else:
                # Sinon, récupérer depuis l'API
                station = await self._fetch_station_by_id(station_id)
                # Mettre en cache si trouvée
                if station:
                    self._stations_cache[station_id] = station

            if station:
                stations.append(station)

                # Si le favicon est vide ou de mauvaise qualité, on essaiera de trouver une meilleure version
                favicon_quality = self._get_favicon_quality(station.get('favicon', ''))
                if favicon_quality < 20:  # Seuil bas = pas de favicon ou de mauvaise qualité
                    stations_needing_better_favicon.append(station)

        # Pour les stations avec favicons manquants/mauvais, chercher de meilleures versions par nom
        if stations_needing_better_favicon:
            self.logger.info(f"Searching better favicons for {len(stations_needing_better_favicon)} stations")

            additional_stations = []
            for station in stations_needing_better_favicon:
                station_name = station.get('name', '')
                if station_name:
                    # Faire une recherche par nom pour trouver d'autres versions de cette station
                    search_results = await self._fetch_stations_by_query(station_name)

                    # Garder seulement les résultats qui correspondent au même nom (case-insensitive)
                    # pour éviter d'ajouter des stations non pertinentes
                    matching_results = [
                        s for s in search_results
                        if s.get('name', '').lower().strip() == station_name.lower().strip()
                    ]

                    additional_stations.extend(matching_results)

            # Ajouter les versions alternatives trouvées
            stations.extend(additional_stations)
            self.logger.info(f"Found {len(additional_stations)} alternative versions with better favicons")

        # IMPORTANT : Appliquer la déduplication pour fusionner les versions et garder les meilleurs favicons
        # La déduplication va comparer toutes les versions de chaque station (ID + alternatives par nom)
        # et garder le meilleur favicon pour chaque station unique
        deduplicated_stations = self._deduplicate_stations(stations)

        # Enrichir avec les images personnalisées
        if deduplicated_stations and self.station_manager:
            deduplicated_stations = self.station_manager.enrich_with_custom_images(deduplicated_stations)

        return deduplicated_stations

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

                # Dédupliquer et trier par score
                deduplicated_stations = self._deduplicate_stations(valid_stations)

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
        Récupère toutes les stations d'un genre via l'API

        Args:
            genre: Genre musical (ex: "pop", "rock", "jazz")

        Returns:
            Liste des stations normalisées et filtrées
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

                # Filtrer et normaliser
                valid_stations = []
                for station in stations:
                    if self._is_valid_station(station):
                        normalized = self._normalize_station(station)
                        valid_stations.append(normalized)

                # Dédupliquer et trier par score
                deduplicated_stations = self._deduplicate_stations(valid_stations)

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
        Récupère les stations d'un pays ET d'un genre via l'API

        Args:
            country_name: Nom du pays (ex: "France", "Japan")
            genre: Genre musical (ex: "pop", "rock")

        Returns:
            Liste des stations normalisées et filtrées
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

                # Filtrer et normaliser
                valid_stations = []
                for station in stations:
                    if self._is_valid_station(station):
                        normalized = self._normalize_station(station)
                        valid_stations.append(normalized)

                # Dédupliquer et trier par score
                deduplicated_stations = self._deduplicate_stations(valid_stations)

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
        Récupère les stations correspondant à une recherche ET un genre via l'API

        Args:
            query: Terme de recherche (nom de station)
            genre: Genre musical (ex: "pop", "rock")

        Returns:
            Liste des stations normalisées et filtrées
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

                # Filtrer et normaliser
                valid_stations = []
                for station in stations:
                    if self._is_valid_station(station):
                        normalized = self._normalize_station(station)
                        valid_stations.append(normalized)

                # Dédupliquer et trier par score
                deduplicated_stations = self._deduplicate_stations(valid_stations)

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

        # Tenter 3 fois avec retry
        for attempt in range(1, 4):
            try:
                self.logger.info(f"Attempt {attempt}/3 fetching countries from Radio Browser API...")
                url = f"{self.BASE_URL}/countries"
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
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

        # Pas de cache, retourner liste vide
        self.logger.error("❌ API unreachable and no cache available, returning empty list")
        return []
