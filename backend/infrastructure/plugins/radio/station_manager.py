"""
Gestionnaire de stations radio - Favoris, stations cassées et stations personnalisées
"""
import logging
import uuid
from typing import List, Dict, Any, Optional, Set


class StationManager:
    """
    Gère les favoris, stations cassées et stations personnalisées avec persistance via SettingsService

    Stockage dans milo_settings.json:
    {
        "radio": {
            "favorites": ["station_id1", "station_id2", ...],
            "broken_stations": ["station_id3", "station_id4", ...],
            "custom_stations": [
                {
                    "id": "custom_uuid",
                    "name": "RTL",
                    "url": "http://streaming.radio.rtl.fr/rtl-1-44-128",
                    "country": "France",
                    "genre": "Variety",
                    "favicon": "",
                    "bitrate": 128,
                    "codec": "MP3"
                }
            ]
        }
    }
    """

    def __init__(self, settings_service=None, state_machine=None):
        self.logger = logging.getLogger(__name__)
        self.settings_service = settings_service
        self.state_machine = state_machine

        # Cache local
        self._favorites: Set[str] = set()
        self._broken_stations: Set[str] = set()
        self._custom_stations: List[Dict[str, Any]] = []
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

                # Charger stations personnalisées
                custom = await self.settings_service.get_setting('radio.custom_stations')
                if custom and isinstance(custom, list):
                    self._custom_stations = custom
                    self.logger.info(f"Loaded {len(self._custom_stations)} custom stations")
            else:
                self.logger.warning("SettingsService not available, using empty state")

            self._loaded = True

        except Exception as e:
            self.logger.error(f"Error loading station manager state: {e}")
            self._favorites = set()
            self._broken_stations = set()
            self._custom_stations = []
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

        success = await self._save_favorites()

        # Broadcast l'événement à tous les clients
        if success and self.state_machine:
            await self.state_machine.broadcast_event("radio", "favorite_added", {
                "station_id": station_id,
                "favorites": list(self._favorites),
                "source": "radio"
            })

        return success

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

        success = await self._save_favorites()

        # Broadcast l'événement à tous les clients
        if success and self.state_machine:
            await self.state_machine.broadcast_event("radio", "favorite_removed", {
                "station_id": station_id,
                "favorites": list(self._favorites),
                "source": "radio"
            })

        return success

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
            Dict avec nombre de favoris, stations cassées et stations personnalisées
        """
        return {
            'favorites_count': len(self._favorites),
            'broken_stations_count': len(self._broken_stations),
            'custom_stations_count': len(self._custom_stations)
        }

    # === Gestion des stations personnalisées ===

    async def _save_custom_stations(self) -> bool:
        """Sauvegarde les stations personnalisées dans SettingsService"""
        if not self.settings_service:
            return False

        try:
            success = await self.settings_service.set_setting(
                'radio.custom_stations',
                self._custom_stations
            )
            if success:
                self.logger.debug(f"Saved {len(self._custom_stations)} custom stations")
            return success
        except Exception as e:
            self.logger.error(f"Error saving custom stations: {e}")
            return False

    async def add_custom_station(
        self,
        name: str,
        url: str,
        country: str = "France",
        genre: str = "Variety",
        favicon: str = "",
        bitrate: int = 128,
        codec: str = "MP3"
    ) -> Dict[str, Any]:
        """
        Ajoute une station personnalisée

        Args:
            name: Nom de la station
            url: URL du flux audio
            country: Pays (défaut: "France")
            genre: Genre musical (défaut: "Variety")
            favicon: URL du favicon (optionnel)
            bitrate: Bitrate en kbps (défaut: 128)
            codec: Codec audio (défaut: "MP3")

        Returns:
            Dict avec success et la station créée ou erreur
        """
        if not name or not url:
            return {"success": False, "error": "name et url requis"}

        try:
            # Générer un ID unique avec préfixe "custom_"
            station_id = f"custom_{uuid.uuid4()}"

            # Créer la station
            station = {
                "id": station_id,
                "name": name.strip(),
                "url": url.strip(),
                "country": country.strip(),
                "genre": genre.strip(),
                "favicon": favicon.strip(),
                "bitrate": bitrate,
                "codec": codec.strip(),
                "is_custom": True,
                "votes": 0,
                "clickcount": 0,
                "score": 0
            }

            # Ajouter à la liste
            self._custom_stations.append(station)
            self.logger.info(f"Added custom station: {name} ({station_id})")

            # Sauvegarder
            success = await self._save_custom_stations()

            if success and self.state_machine:
                await self.state_machine.broadcast_event("radio", "custom_station_added", {
                    "station": station,
                    "custom_stations_count": len(self._custom_stations),
                    "source": "radio"
                })

            return {
                "success": success,
                "station": station
            }

        except Exception as e:
            self.logger.error(f"Error adding custom station: {e}")
            return {"success": False, "error": str(e)}

    async def remove_custom_station(self, station_id: str) -> bool:
        """
        Supprime une station personnalisée

        Args:
            station_id: ID de la station à supprimer

        Returns:
            True si suppression réussie
        """
        if not station_id or not station_id.startswith("custom_"):
            self.logger.warning(f"Invalid custom station ID: {station_id}")
            return False

        try:
            # Trouver et supprimer la station
            original_count = len(self._custom_stations)
            self._custom_stations = [
                s for s in self._custom_stations
                if s.get('id') != station_id
            ]

            if len(self._custom_stations) == original_count:
                self.logger.warning(f"Custom station {station_id} not found")
                return False

            self.logger.info(f"Removed custom station {station_id}")

            # Sauvegarder
            success = await self._save_custom_stations()

            if success and self.state_machine:
                await self.state_machine.broadcast_event("radio", "custom_station_removed", {
                    "station_id": station_id,
                    "custom_stations_count": len(self._custom_stations),
                    "source": "radio"
                })

            # Retirer aussi des favoris si présent
            if station_id in self._favorites:
                await self.remove_favorite(station_id)

            return success

        except Exception as e:
            self.logger.error(f"Error removing custom station: {e}")
            return False

    def get_custom_stations(self) -> List[Dict[str, Any]]:
        """
        Récupère toutes les stations personnalisées

        Returns:
            Liste des stations personnalisées
        """
        return self._custom_stations.copy()

    def get_custom_station_by_id(self, station_id: str) -> Optional[Dict[str, Any]]:
        """
        Récupère une station personnalisée par son ID

        Args:
            station_id: ID de la station

        Returns:
            Station ou None si introuvable
        """
        for station in self._custom_stations:
            if station.get('id') == station_id:
                return station.copy()
        return None
