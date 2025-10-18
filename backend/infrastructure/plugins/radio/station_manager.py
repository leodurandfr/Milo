"""
Gestionnaire de stations radio - Favoris et stations cassées
"""
import logging
from typing import List, Dict, Any, Optional, Set


class StationManager:
    """
    Gère les favoris et les stations cassées avec persistance via SettingsService

    Stockage dans milo_settings.json:
    {
        "radio": {
            "favorites": ["station_id1", "station_id2", ...],
            "broken_stations": ["station_id3", "station_id4", ...]
        }
    }
    """

    def __init__(self, settings_service=None):
        self.logger = logging.getLogger(__name__)
        self.settings_service = settings_service

        # Cache local
        self._favorites: Set[str] = set()
        self._broken_stations: Set[str] = set()
        self._loaded = False

    async def initialize(self) -> None:
        """Charge l'état depuis SettingsService"""
        if self._loaded:
            return

        try:
            if self.settings_service:
                # Charger favoris
                favorites = await self.settings_service.get_setting('radio.favorites')
                if favorites and isinstance(favorites, list):
                    self._favorites = set(favorites)
                    self.logger.info(f"Loaded {len(self._favorites)} favorite stations")

                # Charger stations cassées
                broken = await self.settings_service.get_setting('radio.broken_stations')
                if broken and isinstance(broken, list):
                    self._broken_stations = set(broken)
                    self.logger.info(f"Loaded {len(self._broken_stations)} broken stations")
            else:
                self.logger.warning("SettingsService not available, using empty state")

            self._loaded = True

        except Exception as e:
            self.logger.error(f"Error loading station manager state: {e}")
            self._favorites = set()
            self._broken_stations = set()
            self._loaded = True

    async def _save_favorites(self) -> bool:
        """Sauvegarde les favoris dans SettingsService"""
        if not self.settings_service:
            return False

        try:
            success = await self.settings_service.set_setting(
                'radio.favorites',
                list(self._favorites)
            )
            if success:
                self.logger.debug(f"Saved {len(self._favorites)} favorites")
            return success
        except Exception as e:
            self.logger.error(f"Error saving favorites: {e}")
            return False

    async def _save_broken_stations(self) -> bool:
        """Sauvegarde les stations cassées dans SettingsService"""
        if not self.settings_service:
            return False

        try:
            success = await self.settings_service.set_setting(
                'radio.broken_stations',
                list(self._broken_stations)
            )
            if success:
                self.logger.debug(f"Saved {len(self._broken_stations)} broken stations")
            return success
        except Exception as e:
            self.logger.error(f"Error saving broken stations: {e}")
            return False

    async def add_favorite(self, station_id: str) -> bool:
        """
        Ajoute une station aux favoris

        Args:
            station_id: UUID de la station

        Returns:
            True si ajout réussi
        """
        if not station_id:
            return False

        if station_id in self._favorites:
            self.logger.debug(f"Station {station_id} already in favorites")
            return True

        self._favorites.add(station_id)
        self.logger.info(f"Added station {station_id} to favorites")
        return await self._save_favorites()

    async def remove_favorite(self, station_id: str) -> bool:
        """
        Retire une station des favoris

        Args:
            station_id: UUID de la station

        Returns:
            True si retrait réussi
        """
        if not station_id:
            return False

        if station_id not in self._favorites:
            self.logger.debug(f"Station {station_id} not in favorites")
            return True

        self._favorites.discard(station_id)
        self.logger.info(f"Removed station {station_id} from favorites")
        return await self._save_favorites()

    def is_favorite(self, station_id: str) -> bool:
        """
        Vérifie si une station est dans les favoris

        Args:
            station_id: UUID de la station

        Returns:
            True si favori
        """
        return station_id in self._favorites

    def get_favorites(self) -> List[str]:
        """
        Récupère la liste des IDs de favoris

        Returns:
            Liste des IDs de stations favorites
        """
        return list(self._favorites)

    async def mark_as_broken(self, station_id: str) -> bool:
        """
        Marque une station comme cassée

        Args:
            station_id: UUID de la station

        Returns:
            True si marquage réussi
        """
        if not station_id:
            return False

        if station_id in self._broken_stations:
            return True

        self._broken_stations.add(station_id)
        self.logger.info(f"Marked station {station_id} as broken")
        return await self._save_broken_stations()

    def is_broken(self, station_id: str) -> bool:
        """
        Vérifie si une station est marquée comme cassée

        Args:
            station_id: UUID de la station

        Returns:
            True si cassée
        """
        return station_id in self._broken_stations

    async def reset_broken_stations(self) -> bool:
        """
        Réinitialise la liste des stations cassées

        Returns:
            True si reset réussi
        """
        count = len(self._broken_stations)
        self._broken_stations.clear()
        self.logger.info(f"Reset {count} broken stations")
        return await self._save_broken_stations()

    def filter_broken_stations(self, stations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Filtre les stations cassées d'une liste

        Args:
            stations: Liste de stations

        Returns:
            Liste sans les stations cassées
        """
        return [s for s in stations if not self.is_broken(s.get('id'))]

    def enrich_with_favorite_status(self, stations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Enrichit les stations avec le statut favori

        Args:
            stations: Liste de stations

        Returns:
            Liste enrichie avec clé 'is_favorite'
        """
        for station in stations:
            station['is_favorite'] = self.is_favorite(station.get('id'))
        return stations

    def get_stats(self) -> Dict[str, int]:
        """
        Récupère les statistiques

        Returns:
            Dict avec nombre de favoris et stations cassées
        """
        return {
            'favorites_count': len(self._favorites),
            'broken_stations_count': len(self._broken_stations)
        }
