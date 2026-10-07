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
import unicodedata
import uuid
import io
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

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


WEBP_QUALITY = 80
JPEG_QUALITY = 88


def has_alpha(image: Image.Image) -> bool:
    """True when the image can carry transparency — what decides how it encodes."""
    return image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info)


def encode_webp(image: Image.Image) -> bytes:
    """WebP, keeping transparency when the image has any."""
    image = image.convert("RGBA" if has_alpha(image) else "RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="WEBP", quality=WEBP_QUALITY)
    return buffer.getvalue()


def flatten(image: Image.Image, backdrop: Tuple[int, int, int] = (255, 255, 255)) -> Image.Image:
    """The image as opaque RGB, any transparency filled with `backdrop`."""
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        flat = Image.new("RGB", image.size, backdrop)
        flat.paste(image, mask=image.split()[-1])
        return flat
    return image.convert("RGB")


def encode_jpeg(image: Image.Image) -> bytes:
    """JPEG, for the callers that cannot draw WebP (iOS).

    An uploaded station image is often transparent, and JPEG has no alpha:
    flattening onto white keeps the artwork readable, where the default black
    turns a dark logo into a square.
    """
    image = flatten(image)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY)
    return buffer.getvalue()


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

            # PIL is synchronous; the event loop also answers the WebSocket.
            webp_content, error = await asyncio.to_thread(self._to_stored_webp, file_content)
            if error:
                return False, None, error

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

    def _to_stored_webp(self, file_content: bytes) -> Tuple[Optional[bytes], Optional[str]]:
        """Validate an upload and re-encode it as the stored WebP. Blocking.

        Returns (webp, None) or (None, the reason shown to the user).
        """
        try:
            image = Image.open(io.BytesIO(file_content))
            image.verify()
            image = Image.open(io.BytesIO(file_content))

            if image.format not in self.ALLOWED_FORMATS:
                return None, f"Unsupported image format: {image.format}"

            width, height = image.size
            if width < 50 or height < 50:
                return None, f"Image too small ({width}x{height}). Minimum: 50x50px"

        except Exception as e:
            self.logger.warning(f"Image validation failed: {e}")
            return None, "Invalid or corrupted file"

        try:
            image.thumbnail(self.MAX_DIMENSIONS, Image.Resampling.LANCZOS)
            return encode_webp(image), None
        except Exception as e:
            self.logger.error(f"Image processing failed: {e}")
            return None, "Error processing image"

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
            # The JPEG rendition is derived from this file and nothing else will
            # ever come looking for it. Leaving it behind would put one orphan
            # per deleted station in the directory, which nothing sweeps.
            file_path.with_suffix(self.JPEG_SUFFIX).unlink(missing_ok=True)
            self.logger.info(f"Image deleted: {filename}")
            return True
        return False

    # Where JPEG renditions live. Beside the originals rather than in a separate
    # tree, so `delete` and the stale-image sweep keep finding them by prefix.
    JPEG_SUFFIX = ".jpg"

    async def as_jpeg(self, filename: str) -> Optional[bytes]:
        """The same image as JPEG, converted once and kept.

        Exists because WebP is the right storage format and the wrong wire
        format for one of the callers: iOS draws nothing from WebP bytes, and
        says nothing about it. See the negotiation in `get_station_image`.

        The rendition is written next to the original and reused. Converting on
        every request would put a PIL decode on the path the Lock Screen waits
        on — and that path is served to an app extension the system may
        terminate while it waits.
        """
        source_path = self.get_image_path(filename)
        if source_path is None:
            return None

        cached = source_path.with_suffix(self.JPEG_SUFFIX)
        if cached.exists():
            try:
                async with aiofiles.open(cached, "rb") as f:
                    return await f.read()
            except OSError as e:
                # A truncated rendition would be served forever otherwise.
                self.logger.warning(f"Unreadable JPEG rendition {cached.name}: {e}")

        def _convert() -> Optional[bytes]:
            try:
                with Image.open(source_path) as image:
                    return encode_jpeg(image)
            except Exception as e:
                self.logger.error(f"JPEG conversion failed for {filename}: {e}")
                return None

        # PIL is synchronous and this runs on the event loop that also answers
        # the WebSocket the UI depends on.
        content = await asyncio.to_thread(_convert)
        if content is None:
            return None

        try:
            # `.tmp` then rename: a reader must never meet a half-written file,
            # and this one is cached forever once it exists.
            staging = cached.with_suffix(".jpg.tmp")
            async with aiofiles.open(staging, "wb") as f:
                await f.write(content)
            staging.replace(cached)
        except OSError as e:
            self.logger.warning(f"Could not keep JPEG rendition {cached.name}: {e}")

        return content

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

    Three invariants keep the stores disjoint, so no reader reconciles two
    records for one station:
    - `modified_metadata` holds edits of *directory* stations that are
      favorites. Un-favoriting one drops its edit, its cached original and its
      upload: re-adding it later shows the directory's station, not the edit.
    - `manual_stations` holds the stations the user added (`custom_…`), each
      one record, edits written into it. An added station is always a
      favorite; it leaves the favorites only by being deleted.
    - `favorites_cache` holds the directory original of a favorite.

    Per-station Shazam preference (shazam_enabled) lives as a regular field
    inside modified_metadata[id] / manual_stations[id]. Default ON when the
    field is absent.
    """

    SCHEMA_VERSION: int = 1

    def __init__(self, state_machine=None, on_favorites_changed: Optional[Callable[[], None]] = None):
        self.logger = logging.getLogger("source.radio.data")
        self._state_machine = state_machine
        # The source's: whether the playing station can step to another
        # favorite depends on the list.
        self._on_favorites_changed = on_favorites_changed
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

    @staticmethod
    def is_custom_station(station_id: str) -> bool:
        """True for a station the user added, as opposed to a directory one."""
        return station_id.startswith("custom_")

    def is_favorite(self, station_id: str) -> bool:
        """Check if station is in favorites."""
        return station_id in self._favorites

    @property
    def favorite_count(self) -> int:
        return len(self._favorites)

    def _favorites_moved(self) -> None:
        """Tell the source the list changed, once it is saved."""
        if self._on_favorites_changed:
            self._on_favorites_changed()

    @property
    def favorite_ids(self) -> List[str]:
        """Favorite station ids, in the one order every consumer shows them.

        Sorted here rather than stored sorted: `_favorites` keeps the order the
        stations were added in. The favorites endpoint serves this order to
        every client — the grid, Milo-Mac's menu, Milo-iOS — so none of them
        sorts on its own and they cannot disagree.
        """
        return sorted(self._favorites, key=self._display_sort_key)

    def _display_sort_key(self, station_id: str) -> Tuple[str, str]:
        """Sort key reproducing the grid's `name.localeCompare(name)`.

        Accents are folded and case ignored, which is what ICU's collation does
        at its primary level; the raw name breaks ties so the order is total.
        Measured against Node's `localeCompare` on a real 22-station list: same
        order, position for position.

        A station whose metadata is not local yet sorts under the empty string,
        so its place is still decided here rather than left to each client.
        """
        name = (self._lookup_local(station_id) or {}).get('name', '')
        folded = unicodedata.normalize('NFKD', name)
        folded = ''.join(c for c in folded if not unicodedata.combining(c))
        return (folded.casefold(), name)

    def _lookup_local(
        self, station_id: str, *, include_cache: bool = True
    ) -> Optional[Dict[str, Any]]:
        """Resolve station metadata from the local stores, `id` stamped.

        Priority: modified_metadata (a directory station's edit) →
        manual_stations (custom_xxx) → favorites_cache. The first two never
        share an id, so the order only puts an edit over its cached original.
        `include_cache=False` restricts the lookup to user-authored stores (the
        "custom station" view, which excludes the API-populated favorites
        cache). Returns None when absent.
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
        """Get favorite stations with complete metadata, in display order."""
        result = []
        for station_id in self.favorite_ids:
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

        if station and not self.is_custom_station(station_id):
            cached_station = station.copy()
            cached_station.pop('id', None)
            self._favorites_cache[station_id] = cached_station

        success = await self._save()

        if success:
            self._favorites_moved()
            await self._broadcast(RadioFavoriteAdded(station_id=station_id))

        return success

    async def remove_favorite(self, station_id: str) -> bool:
        """Remove a directory station from favorites, and everything the user
        made of it.

        The edit, the cached original and the uploaded image go with the
        favorite: kept, they came back on a re-add, and in between the search
        list and Réglages still showed an edit of a station nobody kept. An
        added station is refused — it is a favorite for as long as it exists,
        and `remove_custom_station` is how it leaves.
        """
        if not station_id or station_id not in self._favorites:
            return True
        if self.is_custom_station(station_id):
            self.logger.error(f"Refused to un-favorite added station {station_id}")
            return False

        position = self._favorites.index(station_id)
        self._favorites.remove(station_id)
        override = self._modified_metadata.pop(station_id, None)
        cached = self._favorites_cache.pop(station_id, None)

        success = await self._save()

        if not success:
            # Left dropped in memory, the next save would write the removal the
            # UI was told failed — and orphan the upload nothing names any more.
            self._favorites.insert(position, station_id)
            if override is not None:
                self._modified_metadata[station_id] = override
            if cached is not None:
                self._favorites_cache[station_id] = cached
        else:
            if override and override.get('image_filename'):
                await self.image_manager.delete_image(override['image_filename'])
            self._favorites_moved()
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
        """Get all manually created stations, each record copied."""
        return {sid: station.copy() for sid, station in self._manual_stations.items()}

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
        # Strip before guarding, not after: the route takes a raw Form string, so
        # a whitespace-only name passed `not name` and was then emptied by the
        # .strip() below — a station with no name and no URL, reported as created.
        name, url = name.strip(), url.strip()
        if not name or not url:
            return {"success": False, "error": "name and url required"}

        try:
            station_id = f"custom_{uuid.uuid4()}"
            favicon_url = f"/api/radio/images/{image_filename}" if image_filename else ""

            station = {
                "id": station_id,
                "name": name,
                "url": url,
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
            self._favorites.append(station_id)
            success = await self._save()

            if success:
                self._favorites_moved()
                await self._broadcast(RadioFavoriteAdded(station_id=station_id))

            return {"success": success, "station": station}

        except Exception as e:
            self.logger.error(f"Error adding custom station: {e}")
            return {"success": False, "error": str(e)}

    @handle_errors(default=False)
    async def remove_custom_station(self, station_id: str) -> bool:
        """Remove an added station — its record and its favorite, in one write."""
        if not station_id or not self.is_custom_station(station_id):
            return False

        station = self._manual_stations.pop(station_id, None)
        if station is None:
            return False
        position = self._favorites.index(station_id) if station_id in self._favorites else None
        if position is not None:
            self._favorites.remove(station_id)

        success = await self._save()

        if not success:
            # Same reason as remove_favorite: a failed delete must not be
            # written by the next unrelated save.
            self._manual_stations[station_id] = station
            if position is not None:
                self._favorites.insert(position, station_id)
        else:
            if station.get('image_filename'):
                await self.image_manager.delete_image(station['image_filename'])
            if position is not None:
                self._favorites_moved()
                await self._broadcast(RadioFavoriteRemoved(station_id=station_id))

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
        """Write the user's edit of a station into its one record.

        An added station's edit goes into its own record in `manual_stations`
        — it has no original to restore, and two records for one station is
        what every reader then had to reconcile. A directory station's goes
        into `modified_metadata`, over the cached original, and only while it
        is a favorite: an edit of one that is not (an auto-save landing after
        the heart was released) would be an override nothing lists or purges.
        """
        # Same order as add_custom_station, and the same reason: here it renamed
        # an existing favourite to nothing rather than creating a blank one.
        name, url = name.strip(), url.strip()
        if not name or not url:
            return {"success": False, "error": "name and url required"}

        custom = self.is_custom_station(station_id)
        if custom:
            record = self._manual_stations.get(station_id)
            if record is None:
                return {"success": False, "error": "Unknown station"}
        elif station_id not in self._favorites:
            return {"success": False, "error": "Only a favorite station can be edited"}
        else:
            record = self._modified_metadata.get(station_id)

        try:
            original = self._favorites_cache.get(station_id, {})
            # The image the station shows right now: its record, or for a
            # directory station never edited, the directory's logo.
            current = record or original
            previous_image = current.get("image_filename", "")

            if image_filename is None:
                # No upload in this request: keep whatever is showing.
                favicon_url = current.get("favicon", "")
                final_image_filename = previous_image
            elif image_filename == "":
                favicon_url = ""
                final_image_filename = ""
            else:
                favicon_url = f"/api/radio/images/{image_filename}"
                final_image_filename = image_filename

            edited = {
                "name": name,
                "url": url,
                "country": country.strip(),
                "countrycode": countrycode.strip().upper(),
                "genre": genre.strip(),
                "favicon": favicon_url,
                "image_filename": final_image_filename,
                "bitrate": bitrate,
                "codec": codec.strip(),
                "shazam_enabled": shazam_enabled,
            }

            if custom:
                station = {**record, **edited}
                self._manual_stations[station_id] = station
            else:
                station = {
                    **edited,
                    "votes": original.get("votes", 0),
                    "clickcount": original.get("clickcount", 0),
                    "score": original.get("score", 0),
                }
                self._modified_metadata[station_id] = station
            success = await self._save()

            # The upload this save replaces is now unreachable — no read can
            # name the old file again. Only this write knows it became garbage.
            if success and previous_image and previous_image != final_image_filename:
                await self.image_manager.delete_image(previous_image)

            station_data = station.copy()
            station_data['id'] = station_id
            station_data['is_favorite'] = station_id in self._favorites

            if success:
                await self._broadcast(RadioFavoriteModified(station=station_data))

            return {"success": success, "station": station_data}

        except Exception as e:
            self.logger.error(f"Error modifying station metadata: {e}")
            return {"success": False, "error": str(e)}

    async def restore_favorite_metadata(self, station_id: str, radio_api=None) -> Dict[str, Any]:
        """Restore a directory station's original metadata by dropping its edit.

        An added station never has an edit here (its edits live in its own
        record), so it is answered "no modified metadata".

        The refetch runs *before* the override is dropped, and the drop is
        refused when nothing would be left to restore. The override is the
        user's own work — a name, a genre, an uploaded image — and
        `_lookup_local` has nothing else to offer once it is gone: deleting
        first turned an unreachable directory into a silent loss of that work,
        reported as `{"success": True}` and a `logger.warning` the user never
        sees.

        Broadcasts the restored station like `modify_favorite_metadata` does: the
        stores hold the station by value, so without the event the favorites list
        keeps serving the override — its uploaded image included, still rendered
        from the browser cache after the file was deleted here.
        """
        try:
            if station_id not in self._modified_metadata:
                return {"success": False, "error": "Station has no modified metadata"}

            # Refetched by its own id — never matched by name, which could
            # bring back another station's stream under this one's id.
            if radio_api:
                station = await radio_api.fetch_remote_station(station_id)
                if station:
                    cached = station.copy()
                    cached.pop('id', None)
                    self._favorites_cache[station_id] = cached

            if station_id not in self._favorites_cache:
                self.logger.error(
                    f"Cannot restore {station_id}: no original metadata is known"
                )
                return {"success": False, "error": "No original metadata to restore"}

            old_image = self._modified_metadata[station_id].get('image_filename')
            if old_image:
                await self.image_manager.delete_image(old_image)

            del self._modified_metadata[station_id]

            await self._save()

            restored = self._lookup_local(station_id)
            restored['is_favorite'] = station_id in self._favorites
            await self._broadcast(RadioFavoriteModified(station=restored))

            return {"success": True}

        except Exception as e:
            self.logger.error(f"Error restoring favorite metadata: {e}")
            return {"success": False, "error": str(e)}

