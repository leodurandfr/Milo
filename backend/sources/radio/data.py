# backend/sources/radio/data.py
"""
Radio station data management.

This module provides:
- Persistent storage for favorites and custom stations
- Image management for station artwork
- Metadata caching from RadioBrowser API

Storage location: /var/lib/milo/radio_data.json (schema_version protocol —
see CLAUDE.md §"Persistence & schema-version protocol").
Images location: /var/lib/milo/radio_images/
"""
import asyncio
import logging
import uuid
import io
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import aiofiles
from PIL import Image

from backend.core.models.ws_events import (
    RadioFavoriteAdded,
    RadioFavoriteModified,
    RadioFavoriteRemoved,
    WsEvent,
)
from backend.shared.decorators import handle_errors
from backend.shared.persistence import load_versioned_json, save_versioned_json

REQUIRED_TOP_LEVEL_KEYS = ("favorites", "modified_metadata", "manual_stations", "favorites_cache")


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
        self.logger = logging.getLogger("source.radio.images")
        self._ensure_directory()

    @handle_errors(default=None)
    def _ensure_directory(self) -> None:
        """Create images directory if it doesn't exist."""
        self.IMAGES_DIR.mkdir(parents=True, exist_ok=True)

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

    @handle_errors(default=False)
    async def delete_image(self, filename: str) -> bool:
        """Delete an image from storage."""
        if not filename:
            return False

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

    @handle_errors(default=None)
    def get_image_path(self, filename: str) -> Optional[Path]:
        """Get full path of an image."""
        if not filename:
            return None

        file_path = self.IMAGES_DIR / filename

        if not file_path.resolve().is_relative_to(self.IMAGES_DIR.resolve()):
            return None

        if file_path.exists():
            return file_path
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

    Per-station Shazam preference (shazam_enabled) lives as a regular field
    inside modified_metadata[id] / manual_stations[id]. Default ON when the
    field is absent.
    """

    SCHEMA_VERSION: int = 1

    def __init__(self, state_machine=None):
        self.logger = logging.getLogger("source.radio.data")
        self._state_machine = state_machine
        self.image_manager = ImageManager()

        self._data_file = Path('/var/lib/milo/radio_data.json')
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
        """Load state from disk.

        Seeds defaults on fresh install. Raises SchemaVersionMismatch on version
        drift or RuntimeError on a corrupt file (missing keys / invalid JSON); the
        handler in dependencies.py logs the banner and SystemExit(1)s — favorites
        are never silently wiped.
        """
        if self._loaded:
            return

        data = await self._load_data()
        self._favorites = data['favorites']
        self._modified_metadata = data['modified_metadata']
        self._manual_stations = data['manual_stations']
        self._favorites_cache = data['favorites_cache']

        self.logger.info(
            f"Loaded {len(self._favorites)} favorites, "
            f"{len(self._manual_stations)} custom stations"
        )
        self._loaded = True

    async def _broadcast(self, event: WsEvent) -> None:
        """Broadcast a typed radio event via state machine (WebSocket)."""
        if self._state_machine:
            await self._state_machine.broadcast(event)

    async def _load_data(self) -> Dict[str, Any]:
        """Load radio_data.json, seeding defaults on fresh install.

        Raises SchemaVersionMismatch on version drift and RuntimeError / JSON
        errors on a corrupt file — a corrupt file fails loud, never a silent wipe.
        """
        async with self._file_lock:
            data = await load_versioned_json(self._data_file, self.SCHEMA_VERSION)

        if not data:
            self.logger.info("radio_data.json not found, creating new file")
            default_data = self._get_default_structure()
            await self._save_data(default_data)
            return default_data

        self._validate_required_keys(data)
        return data

    def _validate_required_keys(self, data: Dict[str, Any]) -> None:
        """Fail-loud if any expected top-level key is missing."""
        missing = [k for k in REQUIRED_TOP_LEVEL_KEYS if k not in data]
        if missing:
            raise RuntimeError(
                f"radio_data.json missing required keys: {missing} — "
                f"delete it to reset (rm {self._data_file})"
            )

    def _get_default_structure(self) -> Dict[str, Any]:
        """Default structure for a fresh install."""
        return {
            "favorites": [],
            "modified_metadata": {},
            "manual_stations": {},
            "favorites_cache": {},
        }

    @handle_errors(default=False)
    async def _save_data(self, data: Dict[str, Any]) -> bool:
        """Save radio_data.json with atomic write (schema_version stamped automatically)."""
        async with self._file_lock:
            await save_versioned_json(self._data_file, data, self.SCHEMA_VERSION)
        return True

    async def _save(self) -> bool:
        """Save all data."""
        data = {
            "favorites": self._favorites,
            "modified_metadata": self._modified_metadata,
            "manual_stations": self._manual_stations,
            "favorites_cache": self._favorites_cache
        }
        return await self._save_data(data)

    def is_station_shazam_enabled(self, station_id: str) -> bool:
        """Check if Shazam recognition is enabled for a specific station.

        Default is True. A station is OFF only if it has an explicit
        shazam_enabled=False stored in its modified_metadata or manual_stations
        entry (set via the ManageStation UI).
        """
        if not station_id:
            return True
        if station_id in self._modified_metadata:
            return self._modified_metadata[station_id].get('shazam_enabled', True)
        if station_id in self._manual_stations:
            return self._manual_stations[station_id].get('shazam_enabled', True)
        return True

    # === Favorites Management ===

    def is_favorite(self, station_id: str) -> bool:
        """Check if station is in favorites."""
        return station_id in self._favorites

    def _lookup_local(
        self, station_id: str, *, include_cache: bool = True
    ) -> Optional[Dict[str, Any]]:
        """Resolve station metadata from the local stores, `id` stamped.

        Priority: modified_metadata (user overrides) → manual_stations
        (custom_xxx) → favorites_cache. `include_cache=False` restricts the
        lookup to user-authored stores (the "custom station" view, which
        excludes the API-populated favorites cache). Returns None when absent.
        """
        stores = [self._modified_metadata, self._manual_stations]
        if include_cache:
            stores.append(self._favorites_cache)

        for store in stores:
            if station_id in store:
                metadata = store[station_id].copy()
                metadata['id'] = station_id
                return metadata

        return None

    async def get_station_metadata(self, station_id: str) -> Optional[Dict[str, Any]]:
        """
        Get station metadata with priority chain:
        1. Local data (modified → manual → cache)
        2. Fetch from API
        """
        local = self._lookup_local(station_id)
        if local:
            return local

        if self.radio_api:
            metadata = await self.radio_api.fetch_remote_station(station_id)
            if metadata:
                self._favorites_cache[station_id] = metadata
                await self._save()
                return metadata

        return None

    def get_favorite_metadata_local(self, station_id: str) -> Optional[Dict[str, Any]]:
        """Get favorite station metadata from local data only (no API)."""
        return self._lookup_local(station_id)

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
            await self._broadcast(RadioFavoriteAdded(station_id=station_id))

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
            await self._broadcast(RadioFavoriteRemoved(station_id=station_id))

        return success

    def enrich_with_favorite_status(self, stations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Enrich stations with favorite status and custom metadata."""
        for station in stations:
            station_id = station.get('id')
            station['is_favorite'] = station_id in self._favorites

            custom = self._modified_metadata.get(station_id)
            if custom is not None:
                # Overlay the override, but keep the live popularity stats
                # (score/votes/clickcount) whenever the override left them blank.
                preserved = {
                    field: station.get(field, 0)
                    for field in ('score', 'votes', 'clickcount')
                    if not custom.get(field)
                }
                station.update(custom)
                station.update(preserved)
                station['id'] = station_id
            elif station_id in self._manual_stations:
                station.update(self._manual_stations[station_id])
                station['id'] = station_id

        return stations

    # === Custom Stations ===

    def get_manual_stations(self) -> Dict[str, Dict[str, Any]]:
        """Get all manually created stations."""
        return self._manual_stations.copy()

    def _is_real_metadata_modification(self, station_id: str) -> bool:
        """True if the modified_metadata entry diverges from the original on any
        non-shazam field. A shazam-only delta is a behavior preference, not a
        metadata modification, and must not promote the station to "Modified".
        """
        if station_id not in self._modified_metadata:
            return False
        custom = self._modified_metadata[station_id]
        original = self._favorites_cache.get(station_id, {})
        real_fields = ('name', 'url', 'country', 'genre', 'codec', 'bitrate',
                       'image_filename', 'favicon')
        return any(custom.get(f) != original.get(f) for f in real_fields)

    def get_modified_metadata(self) -> Dict[str, Dict[str, Any]]:
        """Get stations with REAL metadata modifications (excludes shazam-only deltas)."""
        return {
            sid: meta.copy()
            for sid, meta in self._modified_metadata.items()
            if self._is_real_metadata_modification(sid)
        }

    def get_custom_station_by_id(self, station_id: str) -> Optional[Dict[str, Any]]:
        """Get custom station by ID (user-authored stores only, no API cache)."""
        return self._lookup_local(station_id, include_cache=False)

    async def add_custom_station(
        self,
        name: str,
        url: str,
        country: str = "",
        countrycode: str = "",
        genre: str = "",
        image_filename: str = "",
        bitrate: int = 0,
        codec: str = "",
        shazam_enabled: bool = True
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
                "countrycode": countrycode.strip().upper(),
                "genre": genre.strip(),
                "favicon": favicon_url,
                "image_filename": image_filename,
                "bitrate": bitrate,
                "codec": codec.strip(),
                "is_custom": True,
                "shazam_enabled": shazam_enabled,
                "votes": 0,
                "clickcount": 0,
                "score": 0
            }

            self._manual_stations[station_id] = station
            success = await self._save()

            return {"success": success, "station": station}

        except Exception as e:
            self.logger.error(f"Error adding custom station: {e}")
            return {"success": False, "error": str(e)}

    @handle_errors(default=False)
    async def remove_custom_station(self, station_id: str) -> bool:
        """Remove custom station.

        An edited custom station sits in both stores: the record written at
        creation and the override written by every later save. Dropping only the
        first leaves the station listed by `get_custom_stations`, un-deletable
        (this method then answers False for it) and re-creatable by opening its
        edit form.
        """
        if not station_id or not station_id.startswith("custom_"):
            return False

        created = self._manual_stations.get(station_id)
        override = self._modified_metadata.get(station_id)
        if not created and not override:
            return False

        # An edited station's image is named by the override, a never-edited
        # one's by the creation record; both stores are read so neither leaks.
        for image_filename in {
            meta.get('image_filename') for meta in (created, override) if meta
        }:
            if image_filename:
                await self.image_manager.delete_image(image_filename)

        self._manual_stations.pop(station_id, None)
        self._modified_metadata.pop(station_id, None)
        success = await self._save()

        if self.is_favorite(station_id):
            await self.remove_favorite(station_id)

        return success

    async def modify_favorite_metadata(
        self,
        station_id: str,
        name: str,
        url: str,
        country: str = "",
        countrycode: str = "",
        genre: str = "",
        codec: str = "",
        bitrate: int = 0,
        image_filename: Optional[str] = None,
        shazam_enabled: bool = True
    ) -> Dict[str, Any]:
        """Create/update custom metadata for a station."""
        if not name or not url:
            return {"success": False, "error": "name and url required"}

        try:
            existing_metadata = self._modified_metadata.get(station_id, {})
            original = self._favorites_cache.get(station_id, {})
            # The image the station shows right now: override → creation record
            # → API cache. A custom station's upload lives in the creation record
            # until its first edit, so the API cache alone is not the fallback —
            # reading it there is what dropped the image on a rename.
            current = self._lookup_local(station_id) or {}

            if image_filename is None:
                # No upload in this request: keep whatever is showing.
                favicon_url = current.get("favicon", "")
                final_image_filename = current.get("image_filename", "")
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
                "countrycode": countrycode.strip().upper(),
                "genre": genre.strip(),
                "favicon": favicon_url,
                "image_filename": final_image_filename,
                "bitrate": bitrate,
                "codec": codec.strip(),
                "shazam_enabled": shazam_enabled,
                "votes": original.get("votes", 0),
                "clickcount": original.get("clickcount", 0),
                "score": original.get("score", 0)
            }

            self._modified_metadata[station_id] = custom_metadata
            success = await self._save()

            if success:
                # The upload this save replaces is now unreachable — the override
                # is what `_lookup_local` serves, so no read can name the old file
                # again. Only this write knows it became garbage.
                stale_images = {
                    existing_metadata.get("image_filename"),
                    self._manual_stations.get(station_id, {}).get("image_filename"),
                } - {final_image_filename}
                for stale in stale_images:
                    if stale:
                        await self.image_manager.delete_image(stale)

            station_data = custom_metadata.copy()
            station_data['id'] = station_id
            station_data['is_favorite'] = station_id in self._favorites

            if success:
                await self._broadcast(RadioFavoriteModified(station=station_data))

            return {"success": success, "station": station_data}

        except Exception as e:
            self.logger.error(f"Error modifying station metadata: {e}")
            return {"success": False, "error": str(e)}

    async def restore_favorite_metadata(self, station_id: str, radio_api=None) -> Dict[str, Any]:
        """Restore original metadata by deleting custom metadata.

        Broadcasts the restored station like `modify_favorite_metadata` does: the
        stores hold the station by value, so without the event the favorites list
        keeps serving the override — its uploaded image included, still rendered
        from the browser cache after the file was deleted here.
        """
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

            await self._save()

            restored = self._lookup_local(station_id)
            if restored:
                restored['is_favorite'] = station_id in self._favorites
                await self._broadcast(RadioFavoriteModified(station=restored))
            else:
                # Nothing left to announce: the refetch failed and no original was
                # cached. The stores keep the override until the next full load.
                self.logger.warning(
                    f"Restored {station_id} with no original metadata to broadcast"
                )

            return {"success": True}

        except Exception as e:
            self.logger.error(f"Error restoring favorite metadata: {e}")
            return {"success": False, "error": str(e)}

