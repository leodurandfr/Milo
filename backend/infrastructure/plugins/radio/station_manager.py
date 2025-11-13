"""
Radio station manager - Favorites, broken stations and custom stations
"""
import logging
import uuid
from typing import List, Dict, Any, Optional, Set
from backend.infrastructure.plugins.radio.image_manager import ImageManager


class StationManager:
    """
    Manages favorites, broken stations and custom stations with persistence via RadioDataService

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

        # Local cache
        self._favorites: List[Dict[str, Any]] = []  # List of stations with complete metadata
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
        """Saves all data in radio_data.json"""
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
        Adds station to favorites with its complete metadata

        Args:
            station_id: Station UUID
            station: Complete dict of the station with metadata (recommended)

        Returns:
            True if addition successful
        """
        if not station_id:
            return False

        # Check if already in favorites
        if any(f.get('id') == station_id for f in self._favorites):
            self.logger.debug(f"Station {station_id} already in favorites")
            return True

        # If station provided, add it with complete metadata
        if station:
            self._favorites.append(station)
            self.logger.info(f"Added station {station.get('name')} to favorites with metadata")
        else:
            # Fallback: add only ID (metadata will be fetched on demand)
            self._favorites.append({"id": station_id})
            self.logger.warning(f"Added station {station_id} to favorites WITHOUT metadata (will need API fetch)")

        success = await self._save()

        # Broadcast event to all clients
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
            station_id: Station UUID

        Returns:
            True if removal successful
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

        # Broadcast event to all clients
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
            station_id: Station UUID

        Returns:
            True if favorite
        """
        return any(f.get('id') == station_id for f in self._favorites)

    def get_favorites(self) -> List[str]:
        """
        Gets list of favorite IDs

        Returns:
            List of favorite station IDs
        """
        return [f.get('id') for f in self._favorites if f.get('id')]

    def get_favorites_with_metadata(self) -> List[Dict[str, Any]]:
        """
        Gets favorite stations with their metadata from local cache

        Returns:
            List of stations with complete metadata
        """
        # Favorites are already stored with complete metadata
        return self._favorites.copy()

    def is_favorite(self, station_id: str) -> bool:
        """
        Checks if station is in favorites

        Args:
            station_id: Station UUID

        Returns:
            True if station is favorite
        """
        return any(f.get('id') == station_id for f in self._favorites)

    async def update_favorite_image(self, station_id: str, image_filename: str) -> bool:
        """
        Updates image of a favorite station

        Args:
            station_id: Station UUID
            image_filename: Image file name

        Returns:
            True if update successful
        """
        for favorite in self._favorites:
            if favorite.get('id') == station_id:
                # Delete old image if it exists
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
        Deletes custom image of a favorite station

        Args:
            station_id: Station UUID

        Returns:
            True if deletion successful
        """
        for favorite in self._favorites:
            if favorite.get('id') == station_id:
                # Delete image file if exists
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
            station_id: Station UUID

        Returns:
            True if marking successful
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
            station_id: Station UUID

        Returns:
            True if broken
        """
        return station_id in self._broken_stations

    async def reset_broken_stations(self) -> bool:
        """
        Resets broken stations list

        Returns:
            True if reset successful
        """
        count = len(self._broken_stations)
        self._broken_stations.clear()
        self.logger.info(f"Reset {count} broken stations")
        return await self._save()

    def filter_broken_stations(self, stations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Filters broken stations from a list

        Args:
            stations: List of stations

        Returns:
            List without broken stations
        """
        return [s for s in stations if not self.is_broken(s.get('id'))]

    def enrich_with_favorite_status(self, stations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Enriches stations with favorite status and custom images

        Args:
            stations: List of stations

        Returns:
            Enriched list with 'is_favorite' key and custom images if available
        """
        for station in stations:
            station_id = station.get('id')
            station['is_favorite'] = self.is_favorite(station_id)

            # If favorite, copy custom image data
            if station['is_favorite']:
                favorite_metadata = next((f for f in self._favorites if f.get('id') == station_id), None)
                if favorite_metadata and favorite_metadata.get('image_filename'):
                    # Override with custom image from favorite
                    station['favicon'] = favorite_metadata.get('favicon')
                    station['image_filename'] = favorite_metadata.get('image_filename')

        return stations

    def get_stats(self) -> Dict[str, int]:
        """
        Gets statistics

        Returns:
            Dict with number of favorites, broken stations and custom stations
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
            name: Station name
            url: Audio stream URL
            country: Country (default: "France")
            genre: Music genre (default: "Variety")
            image_filename: Uploaded image file name (e.g.: "abc123.jpg")
            bitrate: Bitrate in kbps (default: 128)
            codec: Audio codec (default: "MP3")

        Returns:
            Dict with success and the created station or error
        """
        if not name or not url:
            return {"success": False, "error": "name and url required"}

        try:
            # Generate unique ID with prefix "custom_"
            station_id = f"custom_{uuid.uuid4()}"

            # Build image URL (will be served by /api/radio/images/{filename})
            favicon_url = f"/api/radio/images/{image_filename}" if image_filename else ""

            # Create station
            station = {
                "id": station_id,
                "name": name.strip(),
                "url": url.strip(),
                "country": country.strip(),
                "genre": genre.strip(),
                "favicon": favicon_url,
                "image_filename": image_filename,  # Also store the file name
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

            # Save
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
        Deletes custom station and its associated image

        Args:
            station_id: Station ID to delete

        Returns:
            True if deletion successful
        """
        if not station_id or not station_id.startswith("custom_"):
            self.logger.warning(f"Invalid custom station ID: {station_id}")
            return False

        try:
            # Find station to get the image name
            station_to_remove = None
            for station in self._custom_stations:
                if station.get('id') == station_id:
                    station_to_remove = station
                    break

            if not station_to_remove:
                self.logger.warning(f"Custom station {station_id} not found")
                return False

            # Delete associated image if it exists
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

            # Save
            success = await self._save()

            if success and self.state_machine:
                await self.state_machine.broadcast_event("radio", "custom_station_removed", {
                    "station_id": station_id,
                    "custom_stations_count": len(self._custom_stations),
                    "source": "radio"
                })

            # Also remove from favorites if present
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
            List of custom stations
        """
        return self._custom_stations.copy()

    def get_custom_station_by_id(self, station_id: str) -> Optional[Dict[str, Any]]:
        """
        Gets custom station by ID

        Args:
            station_id: Station ID

        Returns:
            Station or None if not found
        """
        for station in self._custom_stations:
            if station.get('id') == station_id:
                return station.copy()
        return None

    async def update_custom_station(
        self,
        station_id: str,
        name: str,
        url: str,
        country: str = "France",
        genre: str = "Variety",
        image_filename: Optional[str] = None,
        remove_image: bool = False
    ) -> Dict[str, Any]:
        """
        Updates an existing custom station

        Args:
            station_id: Station ID to update
            name: New station name
            url: New audio stream URL
            country: New country
            genre: New music genre
            image_filename: New image file name (if changing image)
            remove_image: If True, removes the current image

        Returns:
            Dict with success and the updated station or error
        """
        if not station_id or not station_id.startswith("custom_"):
            return {"success": False, "error": "Invalid custom station ID"}

        if not name or not url:
            return {"success": False, "error": "name and url required"}

        try:
            # Find the station
            station_index = None
            old_station = None
            for i, station in enumerate(self._custom_stations):
                if station.get('id') == station_id:
                    station_index = i
                    old_station = station
                    break

            if station_index is None:
                return {"success": False, "error": "Custom station not found"}

            # Handle image changes
            old_image_filename = old_station.get('image_filename', '')
            new_image_filename = image_filename if image_filename else old_image_filename

            # If removing image, delete the old one
            if remove_image and old_image_filename:
                await self.image_manager.delete_image(old_image_filename)
                new_image_filename = ''
            elif image_filename and image_filename != old_image_filename:
                # If changing to a new image, delete the old one
                if old_image_filename:
                    await self.image_manager.delete_image(old_image_filename)

            # Build new favicon URL
            favicon_url = f"/api/radio/images/{new_image_filename}" if new_image_filename else ""

            # Update station
            updated_station = {
                "id": station_id,
                "name": name.strip(),
                "url": url.strip(),
                "country": country.strip(),
                "genre": genre.strip(),
                "favicon": favicon_url,
                "image_filename": new_image_filename,
                "bitrate": old_station.get('bitrate', 128),
                "codec": old_station.get('codec', 'MP3'),
                "is_custom": True,
                "votes": 0,
                "clickcount": 0,
                "score": 0
            }

            # Replace in list
            self._custom_stations[station_index] = updated_station
            self.logger.info(f"Updated custom station: {name} ({station_id})")

            # Save
            success = await self._save()

            if success and self.state_machine:
                await self.state_machine.broadcast_event("radio", "custom_station_updated", {
                    "station": updated_station,
                    "source": "radio"
                })

            return {
                "success": success,
                "station": updated_station
            }

        except Exception as e:
            self.logger.error(f"Error updating custom station: {e}")
            return {"success": False, "error": str(e)}

    async def create_custom_from_favorite(
        self,
        station_id: str,
        name: str,
        url: str,
        country: str = "France",
        genre: str = "Variety",
        image_filename: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Creates a new custom station from a favorite station
        This allows "editing" favorite stations by creating a custom version

        Args:
            station_id: Original favorite station ID
            name: Station name (can be modified)
            url: Audio stream URL (can be modified)
            country: Country (can be modified)
            genre: Music genre (can be modified)
            image_filename: New image file name (optional)

        Returns:
            Dict with success and the created station or error
        """
        if not name or not url:
            return {"success": False, "error": "name and url required"}

        try:
            # Create the new custom station (same as add_custom_station)
            new_station_id = f"custom_{uuid.uuid4()}"

            # Build image URL
            favicon_url = f"/api/radio/images/{image_filename}" if image_filename else ""

            # Create station
            station = {
                "id": new_station_id,
                "name": name.strip(),
                "url": url.strip(),
                "country": country.strip(),
                "genre": genre.strip(),
                "favicon": favicon_url,
                "image_filename": image_filename or "",
                "bitrate": 128,
                "codec": "MP3",
                "is_custom": True,
                "votes": 0,
                "clickcount": 0,
                "score": 0,
                "created_from": station_id  # Track the original station
            }

            # Add to list
            self._custom_stations.append(station)
            self.logger.info(f"Created custom station from favorite: {name} ({new_station_id}) from {station_id}")

            # Save
            success = await self._save()

            if success and self.state_machine:
                await self.state_machine.broadcast_event("radio", "custom_station_added", {
                    "station": station,
                    "custom_stations_count": len(self._custom_stations),
                    "source": "radio"
                })

            # Remove custom image from the original favorite if it had one
            if self.is_favorite(station_id):
                await self.remove_favorite_image(station_id)

            return {
                "success": success,
                "station": station
            }

        except Exception as e:
            self.logger.error(f"Error creating custom station from favorite: {e}")
            return {"success": False, "error": str(e)}
