"""
Gestionnaire de stations radio - Favoris, stations cassées et stations personnalisées
"""
import logging
import uuid
from typing import List, Dict, Any, Optional, Set
from backend.infrastructure.plugins.radio.image_manager import ImageManager


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
        self.image_manager = ImageManager()

        # Cache local
        self._favorites: Set[str] = set()
        self._broken_stations: Set[str] = set()
        self._custom_stations: List[Dict[str, Any]] = []
        self._station_images: Dict[str, Dict[str, Any]] = {}  # station_id -> {name, image_filename}
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

                # Charger les images modifiées (station_id -> {name, image_filename})
                station_images = await self.settings_service.get_setting('radio.station_images')
                if station_images and isinstance(station_images, dict):
                    self._station_images = station_images
                    self.logger.info(f"Loaded {len(self._station_images)} stations with custom images")
            else:
                self.logger.warning("SettingsService not available, using empty state")

            self._loaded = True

        except Exception as e:
            self.logger.error(f"Error loading station manager state: {e}")
            self._favorites = set()
            self._broken_stations = set()
            self._custom_stations = []
            self._station_images = {}
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

    def enrich_with_custom_images(self, stations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Enrichit les stations avec les images personnalisées si elles existent

        Args:
            stations: Liste de stations

        Returns:
            Liste enrichie avec images personnalisées appliquées
        """
        for station in stations:
            station_id = station.get('id')
            if station_id and station_id in self._station_images:
                image_info = self._station_images[station_id]
                station['favicon'] = image_info.get('favicon', '')
                station['image_filename'] = image_info.get('image_filename', '')
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
        image_filename: str = "",
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
            image_filename: Nom du fichier image uploadé (ex: "abc123.jpg")
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

            # Construire l'URL de l'image (sera servie par /api/radio/images/{filename})
            favicon_url = f"/api/radio/images/{image_filename}" if image_filename else ""

            # Créer la station
            station = {
                "id": station_id,
                "name": name.strip(),
                "url": url.strip(),
                "country": country.strip(),
                "genre": genre.strip(),
                "favicon": favicon_url,
                "image_filename": image_filename,  # Stocker aussi le nom de fichier
                "bitrate": bitrate,
                "codec": codec.strip(),
                "is_custom": True,
                "votes": 0,
                "clickcount": 0,
                "score": 0
            }

            # Ajouter à la liste
            self._custom_stations.append(station)
            self.logger.info(f"Added custom station: {name} ({station_id}) with image: {image_filename}")

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
        Supprime une station personnalisée et son image associée

        Args:
            station_id: ID de la station à supprimer

        Returns:
            True si suppression réussie
        """
        if not station_id or not station_id.startswith("custom_"):
            self.logger.warning(f"Invalid custom station ID: {station_id}")
            return False

        try:
            # Trouver la station pour récupérer le nom de l'image
            station_to_remove = None
            for station in self._custom_stations:
                if station.get('id') == station_id:
                    station_to_remove = station
                    break

            if not station_to_remove:
                self.logger.warning(f"Custom station {station_id} not found")
                return False

            # Supprimer l'image associée si elle existe
            image_filename = station_to_remove.get('image_filename')
            if image_filename:
                await self.image_manager.delete_image(image_filename)
                self.logger.info(f"Deleted image {image_filename} for station {station_id}")

            # Retirer la station de la liste
            original_count = len(self._custom_stations)
            self._custom_stations = [
                s for s in self._custom_stations
                if s.get('id') != station_id
            ]

            if len(self._custom_stations) == original_count:
                self.logger.warning(f"Custom station {station_id} not found in list")
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

    # === Gestion des images de stations ===

    async def _save_station_images(self) -> bool:
        """Sauvegarde les mappings station_id -> image dans SettingsService"""
        if not self.settings_service:
            return False

        try:
            success = await self.settings_service.set_setting(
                'radio.station_images',
                self._station_images
            )
            if success:
                self.logger.debug(f"Saved {len(self._station_images)} station images")
            return success
        except Exception as e:
            self.logger.error(f"Error saving station images: {e}")
            return False

    async def add_station_image(
        self,
        station_id: str,
        station_name: str,
        image_filename: str,
        country: str = "",
        genre: str = ""
    ) -> bool:
        """
        Enregistre qu'une station a une image modifiée

        Args:
            station_id: ID de la station
            station_name: Nom de la station
            image_filename: Nom du fichier image
            country: Pays de la station (optionnel)
            genre: Genre de la station (optionnel)

        Returns:
            True si enregistrement réussi
        """
        if not station_id or not image_filename:
            return False

        self._station_images[station_id] = {
            "name": station_name,
            "image_filename": image_filename,
            "country": country,
            "genre": genre,
            "favicon": f"/api/radio/images/{image_filename}"
        }

        self.logger.info(f"Added image {image_filename} for station {station_name} ({station_id})")
        return await self._save_station_images()

    async def remove_station_image(self, station_id: str) -> bool:
        """
        Retire l'image modifiée d'une station

        Args:
            station_id: ID de la station

        Returns:
            True si retrait réussi
        """
        if not station_id or station_id not in self._station_images:
            return False

        image_info = self._station_images.pop(station_id)
        self.logger.info(f"Removed image for station {image_info.get('name')} ({station_id})")
        return await self._save_station_images()

    def get_stations_with_images(self) -> List[Dict[str, Any]]:
        """
        Récupère toutes les stations avec des images modifiées

        Returns:
            Liste des stations avec leurs infos et image_filename
        """
        stations = []
        for station_id, info in self._station_images.items():
            station = {
                "id": station_id,
                "name": info.get("name", ""),
                "country": info.get("country", ""),
                "genre": info.get("genre", ""),
                "favicon": info.get("favicon", ""),
                "image_filename": info.get("image_filename", ""),
                "is_custom": False  # Ces stations sont des stations existantes avec image modifiée
            }
            stations.append(station)
        return stations
