# backend/features/radio/data.py
"""
Radio station data management.

This module provides:
- Persistent storage for favorites and custom stations
- Image management for station artwork
- Metadata caching from RadioBrowser API

Storage location: /var/lib/milo/radio_data.json
Images location: /var/lib/milo/radio_images/
"""
import asyncio
import json
import logging
import os
import uuid
import io
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import aiofiles
from PIL import Image


class ImageManager:
    """
    Manages storage, validation, and cleanup of radio station images.

    Features:
    - Image validation (format, size, dimensions)
    - Automatic WebP conversion for optimization
    - Secure file path handling
    """

    IMAGES_DIR = Path("/var/lib/milo/radio_images")
    ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF"}
    ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    MAX_FILE_SIZE_MB = 5
    MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
    MAX_DIMENSIONS = (1024, 1024)
    WEBP_QUALITY = 80

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self._ensure_directory()

    def _ensure_directory(self) -> None:
        """Create images directory if it doesn't exist."""
        try:
            self.IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.logger.error(f"Error creating images directory: {e}")

    async def validate_and_save_image(
        self,
        file_content: bytes,
        filename: str
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Validate and save an image.

        Args:
            file_content: Binary file content
            filename: Original file name

        Returns:
            Tuple (success, saved_filename, error_message)
        """
        try:
            # Verify file size
            file_size = len(file_content)
            if file_size > self.MAX_FILE_SIZE_BYTES:
                return False, None, f"Image too large ({file_size / 1024 / 1024:.1f}MB)"
            if file_size == 0:
                return False, None, "Empty file"

            # Verify file extension
            original_ext = Path(filename).suffix.lower()
            if original_ext not in self.ALLOWED_EXTENSIONS:
                return False, None, f"Unsupported format. Accepted: {', '.join(self.ALLOWED_EXTENSIONS)}"

            # Open and validate image with PIL
            try:
                image = Image.open(io.BytesIO(file_content))
                image.verify()
                image = Image.open(io.BytesIO(file_content))

                if image.format not in self.ALLOWED_FORMATS:
                    return False, None, f"Unsupported image format: {image.format}"

                width, height = image.size
                if width < 50 or height < 50:
                    return False, None, f"Image too small ({width}x{height}). Minimum: 50x50px"

            except Exception as e:
                self.logger.warning(f"Image validation failed: {e}")
                return False, None, "Invalid or corrupted file"

            # Process image: resize if needed and convert to WebP
            try:
                if width > self.MAX_DIMENSIONS[0] or height > self.MAX_DIMENSIONS[1]:
                    image.thumbnail(self.MAX_DIMENSIONS, Image.Resampling.LANCZOS)

                output_buffer = io.BytesIO()
                if image.mode in ('RGBA', 'LA') or (image.mode == 'P' and 'transparency' in image.info):
                    image = image.convert('RGBA')
                    image.save(output_buffer, format='WEBP', quality=self.WEBP_QUALITY, lossless=False)
                else:
                    image = image.convert('RGB')
                    image.save(output_buffer, format='WEBP', quality=self.WEBP_QUALITY)

                webp_content = output_buffer.getvalue()

            except Exception as e:
                self.logger.error(f"Image processing failed: {e}")
                return False, None, "Error processing image"

            # Generate unique file name
            unique_id = uuid.uuid4().hex[:12]
            saved_filename = f"{unique_id}.webp"
            file_path = self.IMAGES_DIR / saved_filename

            # Save the WebP file
            async with aiofiles.open(file_path, 'wb') as f:
                await f.write(webp_content)

            self.logger.info(f"Image saved: {saved_filename}")
            return True, saved_filename, None

        except Exception as e:
            self.logger.error(f"Error saving image: {e}")
            return False, None, f"Error saving file: {str(e)}"

    async def delete_image(self, filename: str) -> bool:
        """Delete an image from storage."""
        if not filename:
            return False

        try:
            file_path = self.IMAGES_DIR / filename

            # Security check
            if not file_path.resolve().is_relative_to(self.IMAGES_DIR.resolve()):
                self.logger.warning(f"Attempted path traversal: {filename}")
                return False

            if file_path.exists():
                file_path.unlink()
                self.logger.info(f"Image deleted: {filename}")
                return True
            return False

        except Exception as e:
            self.logger.error(f"Error deleting image {filename}: {e}")
            return False

    def get_image_path(self, filename: str) -> Optional[Path]:
        """Get full path of an image."""
        if not filename:
            return None

        try:
            file_path = self.IMAGES_DIR / filename

            if not file_path.resolve().is_relative_to(self.IMAGES_DIR.resolve()):
                return None

            if file_path.exists():
                return file_path
            return None

        except Exception as e:
            self.logger.error(f"Error getting image path {filename}: {e}")
            return None


class StationDataService:
    """
    Manages radio station data with persistence.

    Data structure in radio_data.json:
    {
        "favorites": ["station_id1", ...],
        "modified_metadata": {"station_id": {...}},
        "manual_stations": {"custom_xxx": {...}},
        "favorites_cache": {"station_id": {...}}
    }
    """

    def __init__(self, event_bus=None, state_machine=None):
        self.logger = logging.getLogger(__name__)
        self._event_bus = event_bus
        self._state_machine = state_machine
        self.image_manager = ImageManager()

        self._data_file = '/var/lib/milo/radio_data.json'
        self._file_lock = asyncio.Lock()

        # Local cache
        self._favorites: List[str] = []
        self._modified_metadata: Dict[str, Dict[str, Any]] = {}
        self._manual_stations: Dict[str, Dict[str, Any]] = {}
        self._favorites_cache: Dict[str, Dict[str, Any]] = {}
        self._loaded = False

        # External API reference (set after initialization)
        self.radio_api = None

    async def initialize(self) -> None:
        """Load state from disk."""
        if self._loaded:
            return

        try:
            data = await self._load_data()
            self._favorites = data.get('favorites', [])
            self._modified_metadata = data.get('modified_metadata', {})
            self._manual_stations = data.get('manual_stations', {})
            self._favorites_cache = data.get('favorites_cache', {})

            self.logger.info(
                f"Loaded {len(self._favorites)} favorites, "
                f"{len(self._manual_stations)} custom stations"
            )
            self._loaded = True

        except Exception as e:
            self.logger.error(f"Error loading station data: {e}")
            self._loaded = True

    async def _broadcast_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Broadcast radio event via state machine and EventBus."""
        if self._state_machine:
            await self._state_machine.broadcast_event("radio", event_type, data)
        if self._event_bus:
            from backend.core.events import Events
            event_name = getattr(Events, f"RADIO_{event_type.upper()}", None)
            if event_name:
                await self._event_bus.emit(event_name, data)

    async def _load_data(self) -> Dict[str, Any]:
        """Load radio_data.json."""
        try:
            if os.path.exists(self._data_file):
                async with self._file_lock:
                    async with aiofiles.open(self._data_file, 'r', encoding='utf-8') as f:
                        data = json.loads(await f.read())
                        if 'favorites_cache' not in data:
                            data['favorites_cache'] = {}
                        return data
            else:
                self.logger.info("radio_data.json not found, creating new file")
                default_data = {
                    "favorites": [],
                    "modified_metadata": {},
                    "manual_stations": {},
                    "favorites_cache": {}
                }
                await self._save_data(default_data)
                return default_data

        except json.JSONDecodeError as e:
            self.logger.error(f"JSON error in radio_data.json: {e}")
            return {"favorites": [], "modified_metadata": {}, "manual_stations": {}, "favorites_cache": {}}
        except Exception as e:
            self.logger.error(f"Error loading radio_data.json: {e}")
            return {"favorites": [], "modified_metadata": {}, "manual_stations": {}, "favorites_cache": {}}

    async def _save_data(self, data: Dict[str, Any]) -> bool:
        """Save radio_data.json with atomic write."""
        try:
            async with self._file_lock:
                temp_file = self._data_file + '.tmp'

                async with aiofiles.open(temp_file, 'w', encoding='utf-8') as f:
                    await f.write(json.dumps(data, ensure_ascii=False, indent=2))
                    await f.write('\n')
                    await f.flush()
                    os.fsync(f.fileno())

                os.replace(temp_file, self._data_file)

            return True

        except Exception as e:
            self.logger.error(f"Error saving radio_data.json: {e}")
            return False

    async def _save(self) -> bool:
        """Save all data."""
        data = {
            "favorites": self._favorites,
            "modified_metadata": self._modified_metadata,
            "manual_stations": self._manual_stations,
            "favorites_cache": self._favorites_cache
        }
        return await self._save_data(data)

    # === Favorites Management ===

    def is_favorite(self, station_id: str) -> bool:
        """Check if station is in favorites."""
        return station_id in self._favorites

    def get_favorites(self) -> List[str]:
        """Get list of favorite station IDs."""
        return self._favorites.copy()

    async def get_station_metadata(self, station_id: str) -> Optional[Dict[str, Any]]:
        """
        Get station metadata with priority chain:
        1. Modified metadata (user overrides)
        2. Manual stations (custom_xxx)
        3. Favorites cache
        4. Fetch from API
        """
        if station_id in self._modified_metadata:
            metadata = self._modified_metadata[station_id].copy()
            metadata['id'] = station_id
            return metadata

        if station_id in self._manual_stations:
            metadata = self._manual_stations[station_id].copy()
            metadata['id'] = station_id
            return metadata

        if station_id in self._favorites_cache:
            metadata = self._favorites_cache[station_id].copy()
            metadata['id'] = station_id
            return metadata

        if self.radio_api:
            metadata = await self.radio_api._fetch_station_by_id(station_id)
            if metadata:
                self._favorites_cache[station_id] = metadata
                await self._save()
                return metadata

        return None

    def get_favorite_metadata_local(self, station_id: str) -> Optional[Dict[str, Any]]:
        """Get favorite station metadata from local data only (no API)."""
        if station_id in self._modified_metadata:
            metadata = self._modified_metadata[station_id].copy()
            metadata['id'] = station_id
            return metadata

        if station_id in self._manual_stations:
            metadata = self._manual_stations[station_id].copy()
            metadata['id'] = station_id
            return metadata

        if station_id in self._favorites_cache:
            metadata = self._favorites_cache[station_id].copy()
            metadata['id'] = station_id
            return metadata

        return None

    async def get_favorites_with_metadata(self) -> List[Dict[str, Any]]:
        """Get favorite stations with complete metadata."""
        result = []
        for station_id in self._favorites:
            metadata = await self.get_station_metadata(station_id)
            if metadata:
                metadata['is_favorite'] = True
                result.append(metadata)
        return result

    async def add_favorite(self, station_id: str, station: Optional[Dict[str, Any]] = None) -> bool:
        """Add station to favorites."""
        if not station_id:
            return False

        if station_id in self._favorites:
            return True

        self._favorites.append(station_id)

        if station and station_id not in self._modified_metadata and station_id not in self._manual_stations:
            cached_station = station.copy()
            cached_station.pop('id', None)
            self._favorites_cache[station_id] = cached_station

        success = await self._save()

        if success:
            await self._broadcast_event("favorite_added", {
                "station_id": station_id,
                "favorites_count": len(self._favorites),
                "source": "radio"
            })

        return success

    async def remove_favorite(self, station_id: str) -> bool:
        """Remove station from favorites.

        Modified metadata (custom images, name overrides) and favorites cache
        are preserved so they are restored if the station is re-added.
        """
        if not station_id or station_id not in self._favorites:
            return True

        self._favorites.remove(station_id)

        success = await self._save()

        if success:
            await self._broadcast_event("favorite_removed", {
                "station_id": station_id,
                "favorites_count": len(self._favorites),
                "source": "radio"
            })

        return success

    def enrich_with_favorite_status(self, stations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Enrich stations with favorite status and custom metadata."""
        for station in stations:
            station_id = station.get('id')
            station['is_favorite'] = station_id in self._favorites

            if station_id in self._modified_metadata:
                custom = self._modified_metadata[station_id]
                preserved_score = station.get('score', 0)
                preserved_votes = station.get('votes', 0)
                preserved_clickcount = station.get('clickcount', 0)
                station.update(custom)
                station['id'] = station_id
                if not custom.get('score'):
                    station['score'] = preserved_score
                if not custom.get('votes'):
                    station['votes'] = preserved_votes
                if not custom.get('clickcount'):
                    station['clickcount'] = preserved_clickcount
            elif station_id in self._manual_stations:
                custom = self._manual_stations[station_id]
                station.update(custom)
                station['id'] = station_id

        return stations

    # === Custom Stations ===

    def get_manual_stations(self) -> Dict[str, Dict[str, Any]]:
        """Get all manually created stations."""
        return self._manual_stations.copy()

    def get_modified_metadata(self) -> Dict[str, Dict[str, Any]]:
        """Get all modified metadata."""
        return self._modified_metadata.copy()

    def get_custom_station_by_id(self, station_id: str) -> Optional[Dict[str, Any]]:
        """Get custom station by ID."""
        if station_id in self._modified_metadata:
            station = self._modified_metadata[station_id].copy()
            station['id'] = station_id
            return station

        if station_id in self._manual_stations:
            station = self._manual_stations[station_id].copy()
            station['id'] = station_id
            return station

        return None

    async def add_custom_station(
        self,
        name: str,
        url: str,
        country: str = "",
        genre: str = "",
        image_filename: str = "",
        bitrate: int = 0,
        codec: str = ""
    ) -> Dict[str, Any]:
        """Add custom station."""
        if not name or not url:
            return {"success": False, "error": "name and url required"}

        try:
            station_id = f"custom_{uuid.uuid4()}"
            favicon_url = f"/api/radio/images/{image_filename}" if image_filename else ""

            station = {
                "id": station_id,
                "name": name.strip(),
                "url": url.strip(),
                "country": country.strip(),
                "genre": genre.strip(),
                "favicon": favicon_url,
                "image_filename": image_filename,
                "bitrate": bitrate,
                "codec": codec.strip(),
                "is_custom": True,
                "votes": 0,
                "clickcount": 0,
                "score": 0
            }

            self._manual_stations[station_id] = station
            success = await self._save()

            if success and self._event_bus:
                from backend.core.events import Events
                await self._event_bus.emit(Events.RADIO_CUSTOM_STATION_ADDED, {
                    "station": station,
                    "custom_stations_count": len(self._manual_stations),
                    "source": "radio"
                })

            return {"success": success, "station": station}

        except Exception as e:
            self.logger.error(f"Error adding custom station: {e}")
            return {"success": False, "error": str(e)}

    async def remove_custom_station(self, station_id: str) -> bool:
        """Remove custom station."""
        if not station_id or not station_id.startswith("custom_"):
            return False

        try:
            station_to_remove = self._manual_stations.get(station_id)
            if not station_to_remove:
                return False

            image_filename = station_to_remove.get('image_filename')
            if image_filename:
                await self.image_manager.delete_image(image_filename)

            del self._manual_stations[station_id]
            success = await self._save()

            if success and self._event_bus:
                from backend.core.events import Events
                await self._event_bus.emit(Events.RADIO_CUSTOM_STATION_REMOVED, {
                    "station_id": station_id,
                    "custom_stations_count": len(self._manual_stations),
                    "source": "radio"
                })

            if self.is_favorite(station_id):
                await self.remove_favorite(station_id)

            return success

        except Exception as e:
            self.logger.error(f"Error removing custom station: {e}")
            return False

    async def update_custom_station(
        self,
        station_id: str,
        name: str,
        url: str,
        country: str = "",
        genre: str = "",
        image_filename: Optional[str] = None,
        remove_image: bool = False
    ) -> Dict[str, Any]:
        """Update an existing custom station."""
        if not station_id or not station_id.startswith("custom_"):
            return {"success": False, "error": "Invalid custom station ID"}

        if not name or not url:
            return {"success": False, "error": "name and url required"}

        try:
            old_station = self._manual_stations.get(station_id)
            if not old_station:
                return {"success": False, "error": "Manual station not found"}

            old_image_filename = old_station.get('image_filename', '')
            new_image_filename = image_filename if image_filename else old_image_filename

            if remove_image and old_image_filename:
                await self.image_manager.delete_image(old_image_filename)
                new_image_filename = ''
            elif image_filename and image_filename != old_image_filename:
                if old_image_filename:
                    await self.image_manager.delete_image(old_image_filename)

            favicon_url = f"/api/radio/images/{new_image_filename}" if new_image_filename else ""

            updated_station = {
                "id": station_id,
                "name": name.strip(),
                "url": url.strip(),
                "country": country.strip(),
                "genre": genre.strip(),
                "favicon": favicon_url,
                "image_filename": new_image_filename,
                "bitrate": old_station.get('bitrate', 0),
                "codec": old_station.get('codec', ''),
                "is_custom": True,
                "votes": 0,
                "clickcount": 0,
                "score": 0,
                "source_station_id": old_station.get('source_station_id', '')
            }

            self._manual_stations[station_id] = updated_station
            success = await self._save()

            if success and self._event_bus:
                from backend.core.events import Events
                await self._event_bus.emit(Events.RADIO_CUSTOM_STATION_UPDATED, {
                    "station": updated_station,
                    "source": "radio"
                })

            return {"success": success, "station": updated_station}

        except Exception as e:
            self.logger.error(f"Error updating custom station: {e}")
            return {"success": False, "error": str(e)}

    async def update_favorite_image(self, station_id: str, image_filename: str) -> bool:
        """Update image of a favorite station."""
        if station_id not in self._favorites:
            return False

        current_metadata = await self.get_station_metadata(station_id)
        if not current_metadata:
            return False

        if station_id in self._modified_metadata:
            old_image = self._modified_metadata[station_id].get('image_filename')
            if old_image:
                await self.image_manager.delete_image(old_image)

        self._modified_metadata[station_id] = {
            **current_metadata,
            'image_filename': image_filename,
            'favicon': f"/api/radio/images/{image_filename}"
        }
        self._modified_metadata[station_id].pop('id', None)
        self._modified_metadata[station_id].pop('is_favorite', None)

        return await self._save()

    async def remove_favorite_image(self, station_id: str) -> bool:
        """Remove custom image of a favorite station."""
        if station_id not in self._favorites:
            return False

        if station_id in self._modified_metadata:
            old_image = self._modified_metadata[station_id].get('image_filename')
            if old_image:
                await self.image_manager.delete_image(old_image)

            current_metadata = await self.get_station_metadata(station_id)
            if current_metadata:
                self._modified_metadata[station_id] = {
                    **current_metadata,
                    'image_filename': "",
                    'favicon': ""
                }
                self._modified_metadata[station_id].pop('id', None)
                self._modified_metadata[station_id].pop('is_favorite', None)

        return await self._save()

    async def modify_favorite_metadata(
        self,
        station_id: str,
        name: str,
        url: str,
        country: str = "",
        genre: str = "",
        codec: str = "",
        bitrate: int = 0,
        image_filename: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create/update custom metadata for a station."""
        if not name or not url:
            return {"success": False, "error": "name and url required"}

        try:
            existing_metadata = self._modified_metadata.get(station_id, {})
            original = self._favorites_cache.get(station_id, {})

            if image_filename is None:
                if existing_metadata.get("favicon"):
                    favicon_url = existing_metadata.get("favicon", "")
                    final_image_filename = existing_metadata.get("image_filename", "")
                else:
                    favicon_url = original.get("favicon", "")
                    final_image_filename = ""
            elif image_filename == "":
                favicon_url = ""
                final_image_filename = ""
            else:
                favicon_url = f"/api/radio/images/{image_filename}"
                final_image_filename = image_filename

            custom_metadata = {
                "name": name.strip(),
                "url": url.strip(),
                "country": country.strip(),
                "genre": genre.strip(),
                "favicon": favicon_url,
                "image_filename": final_image_filename,
                "bitrate": bitrate,
                "codec": codec.strip(),
                "votes": original.get("votes", 0),
                "clickcount": original.get("clickcount", 0),
                "score": original.get("score", 0)
            }

            self._modified_metadata[station_id] = custom_metadata
            success = await self._save()

            station_data = custom_metadata.copy()
            station_data['id'] = station_id
            station_data['is_favorite'] = station_id in self._favorites

            if success:
                await self._broadcast_event("favorite_modified", {
                    "station": station_data
                })

            return {"success": success, "station": station_data}

        except Exception as e:
            self.logger.error(f"Error modifying station metadata: {e}")
            return {"success": False, "error": str(e)}

    async def restore_favorite_metadata(self, station_id: str, radio_api=None) -> Dict[str, Any]:
        """Restore original metadata by deleting custom metadata."""
        try:
            if station_id not in self._modified_metadata:
                return {"success": False, "error": "Station has no modified metadata"}

            old_image = self._modified_metadata[station_id].get('image_filename')
            if old_image:
                await self.image_manager.delete_image(old_image)

            del self._modified_metadata[station_id]

            if radio_api:
                stations = await radio_api.get_stations_by_ids([station_id])
                if stations:
                    cached = stations[0].copy()
                    if 'id' in cached:
                        del cached['id']
                    self._favorites_cache[station_id] = cached

            success = await self._save()

            if success and self._event_bus:
                from backend.core.events import Events
                await self._event_bus.emit(Events.RADIO_FAVORITE_RESTORED, {
                    "station_id": station_id,
                    "source": "radio"
                })

            return {"success": True}

        except Exception as e:
            self.logger.error(f"Error restoring favorite metadata: {e}")
            return {"success": False, "error": str(e)}

    def get_stats(self) -> Dict[str, int]:
        """Get statistics."""
        return {
            'favorites_count': len(self._favorites),
            'modified_metadata_count': len(self._modified_metadata),
            'manual_stations_count': len(self._manual_stations)
        }
