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

    Storage in radio_data.json (three-tier architecture):
    {
        "favorites": ["station_id1", "station_id2", ...],
        "modified_metadata": {
            "station_id1": {
                "name": "RTL (Custom Name)",
                "url": "http://...",
                "genre": "Custom Genre",
                "favicon": "/api/radio/images/abc123.jpg",
                "image_filename": "abc123.jpg"
            }
        },
        "manual_stations": {
            "custom_uuid": {
                "name": "Ma Radio",
                "url": "http://...",
                "country": "France",
                "genre": "Variety",
                "favicon": "/api/radio/images/def456.jpg",
                "image_filename": "def456.jpg",
                "bitrate": 128,
                "codec": "MP3",
                "is_custom": true
            }
        },
        "favorites_cache": {
            "station_id1": {
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
        },
        "broken_stations": ["station_id3", "station_id4", ...]
    }
    """

    def __init__(self, radio_data_service=None, state_machine=None):
        self.logger = logging.getLogger(__name__)
        self.radio_data_service = radio_data_service
        self.state_machine = state_machine
        self.image_manager = ImageManager()

        # Local cache - Three-tier architecture with separated concerns
        self._favorites: List[str] = []  # List of station IDs
        self._modified_metadata: Dict[str, Dict[str, Any]] = {}  # RadioBrowser UUID → custom metadata overrides
        self._manual_stations: Dict[str, Dict[str, Any]] = {}  # custom_xxx → manually created stations
        self._favorites_cache: Dict[str, Dict[str, Any]] = {}  # ID → API metadata cache
        self._broken_stations: Set[str] = set()
        self._loaded = False
        self.radio_api = None  # Will be set by RadioPlugin after initialization

    async def initialize(self) -> None:
        """Loads state from RadioDataService"""
        if self._loaded:
            return

        try:
            if self.radio_data_service:
                # Load all data at once
                data = await self.radio_data_service.load_data()

                self._favorites = data.get('favorites', [])
                self._modified_metadata = data.get('modified_metadata', {})
                self._manual_stations = data.get('manual_stations', {})
                self._favorites_cache = data.get('favorites_cache', {})
                self._broken_stations = set(data.get('broken_stations', []))

                self.logger.info(
                    f"Loaded {len(self._favorites)} favorites, "
                    f"{len(self._broken_stations)} broken, "
                    f"{len(self._modified_metadata)} modified metadata, "
                    f"{len(self._manual_stations)} manual stations"
                )
            else:
                self.logger.warning("RadioDataService not available, using empty state")

            self._loaded = True

        except Exception as e:
            self.logger.error(f"Error loading station manager state: {e}")
            self._favorites = []
            self._modified_metadata = {}
            self._manual_stations = {}
            self._favorites_cache = {}
            self._broken_stations = set()
            self._loaded = True

    async def _save(self) -> bool:
        """Saves all data in radio_data.json"""
        if not self.radio_data_service:
            return False

        try:
            data = {
                "favorites": self._favorites,
                "modified_metadata": self._modified_metadata,
                "manual_stations": self._manual_stations,
                "favorites_cache": self._favorites_cache,
                "broken_stations": list(self._broken_stations)
            }
            return await self.radio_data_service.save_data(data)
        except Exception as e:
            self.logger.error(f"Error saving radio data: {e}")
            return False

    async def get_station_metadata(self, station_id: str) -> Optional[Dict[str, Any]]:
        """
        Gets station metadata with priority chain:
        1. Modified metadata (user overrides for RadioBrowser stations)
        2. Manual stations (user-created stations)
        3. Favorites cache (API cache)
        4. Fetch from API + cache

        Args:
            station_id: Station ID

        Returns:
            Station metadata dict or None
        """
        # Priority 1: Modified metadata (overrides for existing RadioBrowser stations)
        if station_id in self._modified_metadata:
            metadata = self._modified_metadata[station_id].copy()
            metadata['id'] = station_id
            return metadata

        # Priority 2: Manual stations (user-created stations with custom_xxx IDs)
        if station_id in self._manual_stations:
            metadata = self._manual_stations[station_id].copy()
            metadata['id'] = station_id
            return metadata

        # Priority 3: Cache
        if station_id in self._favorites_cache:
            metadata = self._favorites_cache[station_id].copy()
            metadata['id'] = station_id
            return metadata

        # Priority 4: Fetch from API (RadioBrowser stations)
        # If we reach here and it's a RadioBrowser UUID, fetch from API
        if self.radio_api:
            metadata = await self.radio_api._fetch_station_by_id(station_id)
            if metadata:
                # Cache it
                self._favorites_cache[station_id] = metadata
                await self._save()
                return metadata

        return None

    async def add_favorite(self, station_id: str, station: Optional[Dict[str, Any]] = None) -> bool:
        """
        Adds station to favorites and caches its metadata

        Args:
            station_id: Station UUID
            station: Complete dict of the station with metadata (will be cached)

        Returns:
            True if addition successful
        """
        if not station_id:
            return False

        # Check if already in favorites
        if station_id in self._favorites:
            self.logger.debug(f"Station {station_id} already in favorites")
            return True

        # Add ID to favorites list
        self._favorites.append(station_id)

        # Cache metadata if provided (and not already in modified_metadata or manual_stations)
        if station and station_id not in self._modified_metadata and station_id not in self._manual_stations:
            # Remove id from station dict to avoid duplication
            cached_station = station.copy()
            if 'id' in cached_station:
                del cached_station['id']
            self._favorites_cache[station_id] = cached_station
            self.logger.info(f"Added station {station.get('name')} to favorites with cached metadata")
        else:
            self.logger.info(f"Added station {station_id} to favorites")

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
        Removes station from favorites (but keeps custom metadata and cache)

        Args:
            station_id: Station UUID

        Returns:
            True if removal successful
        """
        if not station_id:
            return False

        if station_id not in self._favorites:
            self.logger.debug(f"Station {station_id} not in favorites")
            return True

        # Remove from favorites list only (keep custom_stations and favorites_cache)
        self._favorites.remove(station_id)

        self.logger.info(f"Removed station {station_id} from favorites (custom metadata preserved)")

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
        return station_id in self._favorites

    def get_favorites(self) -> List[str]:
        """
        Gets list of favorite IDs

        Returns:
            List of favorite station IDs
        """
        return self._favorites.copy()

    async def get_favorites_with_metadata(self) -> List[Dict[str, Any]]:
        """
        Gets favorite stations with their complete metadata

        Returns:
            List of stations with complete metadata (custom > cache > API)
        """
        result = []
        for station_id in self._favorites:
            metadata = await self.get_station_metadata(station_id)
            if metadata:
                metadata['is_favorite'] = True
                result.append(metadata)
        return result

    def is_favorite(self, station_id: str) -> bool:
        """
        Checks if station is in favorites

        Args:
            station_id: Station UUID

        Returns:
            True if station is favorite
        """
        return station_id in self._favorites

    async def update_favorite_image(self, station_id: str, image_filename: str) -> bool:
        """
        Updates image of a favorite station

        Args:
            station_id: Station UUID
            image_filename: Image file name

        Returns:
            True if update successful
        """
        if station_id not in self._favorites:
            return False

        # Get current metadata (from custom, cache, or API)
        current_metadata = await self.get_station_metadata(station_id)
        if not current_metadata:
            return False

        # Delete old image if it exists in modified metadata
        if station_id in self._modified_metadata:
            old_image = self._modified_metadata[station_id].get('image_filename')
            if old_image:
                await self.image_manager.delete_image(old_image)

        # Update/create modified metadata with new image
        self._modified_metadata[station_id] = {
            **current_metadata,
            'image_filename': image_filename,
            'favicon': f"/api/radio/images/{image_filename}"
        }
        # Remove 'id' and 'is_favorite' keys as they're not stored in the dict value
        self._modified_metadata[station_id].pop('id', None)
        self._modified_metadata[station_id].pop('is_favorite', None)

        self.logger.info(f"Updated image for favorite station {station_id}")
        return await self._save()

    async def remove_favorite_image(self, station_id: str) -> bool:
        """
        Deletes custom image of a favorite station

        Args:
            station_id: Station UUID

        Returns:
            True if deletion successful
        """
        if station_id not in self._favorites:
            return False

        # Delete image file if exists in modified metadata
        if station_id in self._modified_metadata:
            old_image = self._modified_metadata[station_id].get('image_filename')
            if old_image:
                await self.image_manager.delete_image(old_image)

            # Get current metadata and update with empty image
            current_metadata = await self.get_station_metadata(station_id)
            if current_metadata:
                self._modified_metadata[station_id] = {
                    **current_metadata,
                    'image_filename': "",
                    'favicon': ""
                }
                # Remove 'id' and 'is_favorite' keys as they're not stored in the dict value
                self._modified_metadata[station_id].pop('id', None)
                self._modified_metadata[station_id].pop('is_favorite', None)

        self.logger.info(f"Removed image for favorite station {station_id}")
        return await self._save()

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
        Enriches stations with favorite status and merges custom metadata if exists

        Args:
            stations: List of stations

        Returns:
            Enriched list with 'is_favorite' key and custom metadata merged
        """
        for station in stations:
            station_id = station.get('id')

            # Add favorite status
            station['is_favorite'] = station_id in self._favorites

            # Merge modified metadata if exists (overrides for RadioBrowser stations)
            if station_id in self._modified_metadata:
                custom = self._modified_metadata[station_id]

                # Preserve original score/votes from API if custom doesn't have them
                preserved_score = station.get('score', 0)
                preserved_votes = station.get('votes', 0)
                preserved_clickcount = station.get('clickcount', 0)

                # Merge modified metadata
                station.update(custom)
                station['id'] = station_id  # Preserve ID

                # Restore score/votes if custom doesn't have them
                if not custom.get('score'):
                    station['score'] = preserved_score
                if not custom.get('votes'):
                    station['votes'] = preserved_votes
                if not custom.get('clickcount'):
                    station['clickcount'] = preserved_clickcount

            # Merge manual station metadata if exists (for custom_xxx IDs)
            elif station_id in self._manual_stations:
                custom = self._manual_stations[station_id]
                station.update(custom)
                station['id'] = station_id  # Preserve ID

        return stations

    def get_stats(self) -> Dict[str, int]:
        """
        Gets statistics

        Returns:
            Dict with number of favorites, broken stations, modified metadata and manual stations
        """
        return {
            'favorites_count': len(self._favorites),
            'broken_stations_count': len(self._broken_stations),
            'modified_metadata_count': len(self._modified_metadata),
            'manual_stations_count': len(self._manual_stations)
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

            # Add to dict (manual stations)
            self._manual_stations[station_id] = station
            self.logger.info(f"Added manual station: {name} ({station_id}) with image: {image_filename}")

            # Save
            success = await self._save()

            if success and self.state_machine:
                await self.state_machine.broadcast_event("radio", "custom_station_added", {
                    "station": station,
                    "custom_stations_count": len(self._manual_stations),
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
            # Get station from dict (manual stations only)
            station_to_remove = self._manual_stations.get(station_id)

            if not station_to_remove:
                self.logger.warning(f"Manual station {station_id} not found")
                return False

            # Delete associated image if it exists
            image_filename = station_to_remove.get('image_filename')
            if image_filename:
                await self.image_manager.delete_image(image_filename)
                self.logger.info(f"Deleted image {image_filename} for station {station_id}")

            # Remove station from dict
            del self._manual_stations[station_id]

            self.logger.info(f"Removed manual station {station_id}")

            # Save
            success = await self._save()

            if success and self.state_machine:
                await self.state_machine.broadcast_event("radio", "custom_station_removed", {
                    "station_id": station_id,
                    "custom_stations_count": len(self._manual_stations),
                    "source": "radio"
                })

            # Also remove from favorites if present
            if self.is_favorite(station_id):
                await self.remove_favorite(station_id)

            return success

        except Exception as e:
            self.logger.error(f"Error removing custom station: {e}")
            return False

    def get_modified_metadata(self) -> Dict[str, Dict[str, Any]]:
        """
        Gets all modified metadata (overrides for RadioBrowser stations)

        Returns:
            Dict of station_id → modified metadata
        """
        return self._modified_metadata.copy()

    def get_manual_stations(self) -> Dict[str, Dict[str, Any]]:
        """
        Gets all manually created stations (custom_xxx IDs)

        Returns:
            Dict of station_id → manual station metadata
        """
        return self._manual_stations.copy()

    def get_custom_station_by_id(self, station_id: str) -> Optional[Dict[str, Any]]:
        """
        Gets custom station by ID (works for both modified and manual stations)

        Args:
            station_id: Station ID

        Returns:
            Station or None if not found
        """
        # Check modified metadata first
        if station_id in self._modified_metadata:
            station = self._modified_metadata[station_id].copy()
            station['id'] = station_id
            return station

        # Then check manual stations
        if station_id in self._manual_stations:
            station = self._manual_stations[station_id].copy()
            station['id'] = station_id
            return station

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
            # Get the station from dict (manual stations only)
            old_station = self._manual_stations.get(station_id)

            if not old_station:
                return {"success": False, "error": "Manual station not found"}

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
                "score": 0,
                "source_station_id": old_station.get('source_station_id', '')  # Preserve original station tracking
            }

            # Update in dict (manual stations)
            self._manual_stations[station_id] = updated_station
            self.logger.info(f"Updated manual station: {name} ({station_id})")

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
                "source_station_id": station_id  # Track the original station
            }

            # Add to dict (manual stations)
            self._manual_stations[new_station_id] = station
            self.logger.info(f"Created manual station from favorite: {name} ({new_station_id}) from {station_id}")

            # Save
            success = await self._save()

            if success and self.state_machine:
                await self.state_machine.broadcast_event("radio", "custom_station_added", {
                    "station": station,
                    "custom_stations_count": len(self._manual_stations),
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

    async def modify_favorite_metadata(
        self,
        station_id: str,
        name: str,
        url: str,
        country: str = "France",
        genre: str = "Variety",
        image_filename: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Creates/updates custom metadata for a station (works for both favorites and non-favorites)

        Args:
            station_id: Station ID
            name: New station name
            url: New audio stream URL
            country: New country
            genre: New music genre
            image_filename: New image file name (optional)

        Returns:
            Dict with success and the modified station or error
        """
        if not name or not url:
            return {"success": False, "error": "name and url required"}

        try:
            # Build image URL
            favicon_url = f"/api/radio/images/{image_filename}" if image_filename else ""

            # Get original metadata for score/votes (if exists)
            original = self._favorites_cache.get(station_id, {})

            # Create custom metadata entry
            custom_metadata = {
                "name": name.strip(),
                "url": url.strip(),
                "country": country.strip(),
                "genre": genre.strip(),
                "favicon": favicon_url,
                "image_filename": image_filename or "",
                "bitrate": original.get("bitrate", 128),
                "codec": original.get("codec", "MP3"),
                "votes": original.get("votes", 0),
                "clickcount": original.get("clickcount", 0),
                "score": original.get("score", 0)
            }

            # Save to modified_metadata (overrides for RadioBrowser stations)
            self._modified_metadata[station_id] = custom_metadata
            self.logger.info(f"Modified station metadata: {name} ({station_id})")

            # Save
            success = await self._save()

            # Build response with full station data
            station_data = custom_metadata.copy()
            station_data['id'] = station_id
            station_data['is_favorite'] = station_id in self._favorites

            if success and self.state_machine:
                await self.state_machine.broadcast_event("radio", "favorite_modified", {
                    "station": station_data,
                    "source": "radio"
                })

            return {
                "success": success,
                "station": station_data
            }

        except Exception as e:
            self.logger.error(f"Error modifying station metadata: {e}")
            return {"success": False, "error": str(e)}

    async def restore_favorite_metadata(self, station_id: str, radio_api=None) -> Dict[str, Any]:
        """
        Restores original metadata by deleting custom metadata

        Args:
            station_id: Station ID
            radio_api: RadioBrowserAPI instance to refresh cache (optional)

        Returns:
            Dict with success and error if any
        """
        try:
            # Check if station has modified metadata
            if station_id not in self._modified_metadata:
                return {"success": False, "error": "Station has no modified metadata"}

            # Delete custom image if exists
            old_image = self._modified_metadata[station_id].get('image_filename')
            if old_image:
                await self.image_manager.delete_image(old_image)

            # Remove modified metadata
            del self._modified_metadata[station_id]
            self.logger.info(f"Restored station metadata to original: {station_id}")

            # Optionally refresh cache from API
            if radio_api:
                original_station = await radio_api._fetch_station_by_id(station_id)
                if original_station:
                    # Update cache with fresh data from API
                    cached = original_station.copy()
                    if 'id' in cached:
                        del cached['id']
                    self._favorites_cache[station_id] = cached
                    self.logger.info(f"Refreshed cache from API: {original_station['name']}")

            # Save
            success = await self._save()

            if success and self.state_machine:
                await self.state_machine.broadcast_event("radio", "favorite_restored", {
                    "station_id": station_id,
                    "source": "radio"
                })

            return {"success": True}

        except Exception as e:
            self.logger.error(f"Error restoring favorite metadata: {e}")
            return {"success": False, "error": str(e)}
