"""
Radio station manager - Favorites, broken stations and custom stations
"""
import logging
import uuid
from typing import List, Dict, Any, Optional, Set
from backend.infrastructure.plugins.radio.image_manager import ImageManager


class StationManager:
    """
    Manages favorites, stations cassées et stations personnalisées avec persistance via RadioDataService

    Storage in radio_data.json:
    {
        "favorites": [
            {
                "id": "station_id1",
                "name": "RTL",
                "url": "http://...",
                "country": "France",
                "genre": "Variety",
                "favicon": "http://...",
                "bitrate": 128,
                "codec": "MP3",
                "votes": 100,
                "clickcount": 200,
                "score": 300
            }
        ],
        "broken_stations": ["station_id3", "station_id4", ...],
        "custom_stations": [
            {
                "id": "custom_uuid",
                "name": "Ma Radio",
                "url": "http://...",
                "country": "France",
                "genre": "Variety",
                "favicon": "/api/radio/images/abc123.jpg",
                "image_filename": "abc123.jpg",
                "bitrate": 128,
                "codec": "MP3",
                "is_custom": true
            }
        ]
    }
    """

    def __init__(self, radio_data_service=None, state_machine=None):
        self.logger = logging.getLogger(__name__)
        self.radio_data_service = radio_data_service
        self.state_machine = state_machine
        self.image_manager = ImageManager()

        # Cache local
        self._favorites: List[Dict[str, Any]] = []  # Liste de stations avec métadonnées complètes
        self._broken_stations: Set[str] = set()
        self._custom_stations: List[Dict[str, Any]] = []
        self._loaded = False

    async def initialize(self) -> None:
        """Loads state from RadioDataService"""
        if self._loaded:
            return

        try:
            if self.radio_data_service:
                # Load all data at once
                data = await self.radio_data_service.load_data()

                self._favorites = data.get('favorites', [])
                self._broken_stations = set(data.get('broken_stations', []))
                self._custom_stations = data.get('custom_stations', [])

                self.logger.info(
                    f"Loaded {len(self._favorites)} favorites, "
                    f"{len(self._broken_stations)} broken, "
                    f"{len(self._custom_stations)} custom stations"
                )
            else:
                self.logger.warning("RadioDataService not available, using empty state")

            self._loaded = True

        except Exception as e:
            self.logger.error(f"Error loading station manager state: {e}")
            self._favorites = []
            self._broken_stations = set()
            self._custom_stations = []
            self._loaded = True

    async def _save(self) -> bool:
        """Saves all data dans radio_data.json"""
        if not self.radio_data_service:
            return False

        try:
            data = {
                "favorites": self._favorites,
                "broken_stations": list(self._broken_stations),
                "custom_stations": self._custom_stations
            }
            return await self.radio_data_service.save_data(data)
        except Exception as e:
            self.logger.error(f"Error saving radio data: {e}")
            return False

    async def add_favorite(self, station_id: str, station: Optional[Dict[str, Any]] = None) -> bool:
        """
        Adds station to favorites avec ses métadonnées complètes

        Args:
            station_id: UUID de la station
            station: Dict complet de la station avec métadonnées (recommandé)

        Returns:
            True si ajout réussi
        """
        if not station_id:
            return False

        # Check if already in favorites
        if any(f.get('id') == station_id for f in self._favorites):
            self.logger.debug(f"Station {station_id} already in favorites")
            return True

        # If station provided, l'ajouter avec métadonnées complètes
        if station:
            self._favorites.append(station)
            self.logger.info(f"Added station {station.get('name')} to favorites with metadata")
        else:
            # Fallback: add only ID (métadonnées seront fetchées à la demande)
            self._favorites.append({"id": station_id})
            self.logger.warning(f"Added station {station_id} to favorites WITHOUT metadata (will need API fetch)")

        success = await self._save()

        # Broadcast event à tous les clients
        if success and self.state_machine:
            await self.state_machine.broadcast_event("radio", "favorite_added", {
                "station_id": station_id,
                "favorites_count": len(self._favorites),
                "source": "radio"
            })

        return success

    async def remove_favorite(self, station_id: str) -> bool:
        """
        Removes station from favorites

        Args:
            station_id: UUID de la station

        Returns:
            True si retrait réussi
        """
        if not station_id:
            return False

        original_count = len(self._favorites)
        self._favorites = [f for f in self._favorites if f.get('id') != station_id]

        if len(self._favorites) == original_count:
            self.logger.debug(f"Station {station_id} not in favorites")
            return True

        self.logger.info(f"Removed station {station_id} from favorites")

        success = await self._save()

        # Broadcast event à tous les clients
        if success and self.state_machine:
            await self.state_machine.broadcast_event("radio", "favorite_removed", {
                "station_id": station_id,
                "favorites_count": len(self._favorites),
                "source": "radio"
            })

        return success

    def is_favorite(self, station_id: str) -> bool:
        """
        Checks if station is in favorites

        Args:
            station_id: UUID de la station

        Returns:
            True si favori
        """
        return any(f.get('id') == station_id for f in self._favorites)

    def get_favorites(self) -> List[str]:
        """
        Gets list of favorite IDs

        Returns:
            Liste des IDs de stations favorites
        """
        return [f.get('id') for f in self._favorites if f.get('id')]

    def get_favorites_with_metadata(self) -> List[Dict[str, Any]]:
        """
        Gets favorite stations avec leurs métadonnées depuis le cache local

        Returns:
            Liste des stations avec métadonnées complètes
        """
        # Favorites are already stored avec métadonnées complètes
        return self._favorites.copy()

    def is_favorite(self, station_id: str) -> bool:
        """
        Checks if station is in favorites

        Args:
            station_id: UUID de la station

        Returns:
            True si la station est favorite
        """
        return any(f.get('id') == station_id for f in self._favorites)

    async def update_favorite_image(self, station_id: str, image_filename: str) -> bool:
        """
        Updates image d'une station favorite

        Args:
            station_id: UUID de la station
            image_filename: Nom du fichier image

        Returns:
            True si mise à jour réussie
        """
        for favorite in self._favorites:
            if favorite.get('id') == station_id:
                # Delete old image si elle existe
                old_image = favorite.get('image_filename')
                if old_image:
                    await self.image_manager.delete_image(old_image)

                # Update with new image
                favorite['image_filename'] = image_filename
                favorite['favicon'] = f"/api/radio/images/{image_filename}"

                self.logger.info(f"Updated image for favorite station {station_id}")
                return await self._save()

        return False

    async def remove_favorite_image(self, station_id: str) -> bool:
        """
        Deletes custom image d'une station favorite

        Args:
            station_id: UUID de la station

        Returns:
            True si suppression réussie
        """
        for favorite in self._favorites:
            if favorite.get('id') == station_id:
                # Delete image file si existe
                old_image = favorite.get('image_filename')
                if old_image:
                    await self.image_manager.delete_image(old_image)

                # Reset image fields
                favorite['image_filename'] = ""
                favorite['favicon'] = ""

                self.logger.info(f"Removed image for favorite station {station_id}")
                return await self._save()

        return False

    async def mark_as_broken(self, station_id: str) -> bool:
        """
        Marks station as broken

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
        return await self._save()

    def is_broken(self, station_id: str) -> bool:
        """
        Checks if station is marked as broken

        Args:
            station_id: UUID de la station

        Returns:
            True si cassée
        """
        return station_id in self._broken_stations

    async def reset_broken_stations(self) -> bool:
        """
        Resets broken stations list

        Returns:
            True si reset réussi
        """
        count = len(self._broken_stations)
        self._broken_stations.clear()
        self.logger.info(f"Reset {count} broken stations")
        return await self._save()

    def filter_broken_stations(self, stations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Filters broken stations d'une liste

        Args:
            stations: Liste de stations

        Returns:
            Liste sans les stations cassées
        """
        return [s for s in stations if not self.is_broken(s.get('id'))]

    def enrich_with_favorite_status(self, stations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Enriches stations with favorite status et les images personnalisées

        Args:
            stations: Liste de stations

        Returns:
            Liste enrichie avec clé 'is_favorite' et images personnalisées si disponibles
        """
        for station in stations:
            station_id = station.get('id')
            station['is_favorite'] = self.is_favorite(station_id)

            # If favorite, copier les données d'image personnalisée
            if station['is_favorite']:
                favorite_metadata = next((f for f in self._favorites if f.get('id') == station_id), None)
                if favorite_metadata and favorite_metadata.get('image_filename'):
                    # Override with custom image du favori
                    station['favicon'] = favorite_metadata.get('favicon')
                    station['image_filename'] = favorite_metadata.get('image_filename')

        return stations

    def get_stats(self) -> Dict[str, int]:
        """
        Gets statistics

        Returns:
            Dict avec nombre de favoris, stations cassées et stations personnalisées
        """
        return {
            'favorites_count': len(self._favorites),
            'broken_stations_count': len(self._broken_stations),
            'custom_stations_count': len(self._custom_stations)
        }

    # === Custom stations management ===

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
        Adds custom station

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
            # Generate unique ID avec préfixe "custom_"
            station_id = f"custom_{uuid.uuid4()}"

            # Build image URL (sera servie par /api/radio/images/{filename})
            favicon_url = f"/api/radio/images/{image_filename}" if image_filename else ""

            # Create station
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

            # Add to list
            self._custom_stations.append(station)
            self.logger.info(f"Added custom station: {name} ({station_id}) with image: {image_filename}")

            # Sauvegarder
            success = await self._save()

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
        Deletes custom station et son image associée

        Args:
            station_id: ID de la station à supprimer

        Returns:
            True si suppression réussie
        """
        if not station_id or not station_id.startswith("custom_"):
            self.logger.warning(f"Invalid custom station ID: {station_id}")
            return False

        try:
            # Find station to get le nom de l'image
            station_to_remove = None
            for station in self._custom_stations:
                if station.get('id') == station_id:
                    station_to_remove = station
                    break

            if not station_to_remove:
                self.logger.warning(f"Custom station {station_id} not found")
                return False

            # Delete associated image si elle existe
            image_filename = station_to_remove.get('image_filename')
            if image_filename:
                await self.image_manager.delete_image(image_filename)
                self.logger.info(f"Deleted image {image_filename} for station {station_id}")

            # Remove station from list
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
            success = await self._save()

            if success and self.state_machine:
                await self.state_machine.broadcast_event("radio", "custom_station_removed", {
                    "station_id": station_id,
                    "custom_stations_count": len(self._custom_stations),
                    "source": "radio"
                })

            # Also remove from favorites si présent
            if self.is_favorite(station_id):
                await self.remove_favorite(station_id)

            return success

        except Exception as e:
            self.logger.error(f"Error removing custom station: {e}")
            return False

    def get_custom_stations(self) -> List[Dict[str, Any]]:
        """
        Gets all custom stations

        Returns:
            Liste des stations personnalisées
        """
        return self._custom_stations.copy()

    def get_custom_station_by_id(self, station_id: str) -> Optional[Dict[str, Any]]:
        """
        Gets custom station by ID

        Args:
            station_id: ID de la station

        Returns:
            Station ou None si introuvable
        """
        for station in self._custom_stations:
            if station.get('id') == station_id:
                return station.copy()
        return None
