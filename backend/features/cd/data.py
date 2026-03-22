# backend/features/cd/data.py
"""
CD data service for disc detection, metadata lookup, and caching.

Responsibilities:
- Detect USB CD drive presence via /dev/sr0
- Detect disc insertion via ioctl CDROM_DRIVE_STATUS
- Read disc TOC via python-discid (libdiscid)
- Lookup metadata from MusicBrainz (with fuzzy TOC fallback)
- Download cover art from MusicBrainz Cover Art Archive
- Cache disc metadata locally in /var/lib/milo/cd_data.json
- Provide offline fallback with generic track names

Data is persisted to /var/lib/milo/cd_data.json
Cover art is stored in /var/lib/milo/cd_covers/
"""
import asyncio
import fcntl
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import aiofiles

from backend.config.constants import CD_COVERS_DIR, CD_DATA_FILE, CD_DEVICE
from backend.features.cd.models import DiscInfo, TrackInfo
from backend.shared.decorators import handle_errors

logger = logging.getLogger(__name__)

# ioctl constants for CD drive status
CDROM_DRIVE_STATUS = 0x5326
CDS_DISC_OK = 4


class CdDataService:
    """
    Service for CD disc detection, metadata lookup, and local caching.

    Uses an in-memory cache backed by cd_data.json for persistence.
    Blocking I/O (discid, musicbrainzngs) is offloaded via asyncio.to_thread().
    """

    def __init__(self):
        self._logger = logging.getLogger(__name__)
        self._data_file = str(CD_DATA_FILE)
        self._covers_dir = str(CD_COVERS_DIR)
        self._file_lock = asyncio.Lock()
        self._cache: Dict[str, Any] = {}
        self._loaded = False

        # Configure MusicBrainz user agent (module-level, idempotent)
        try:
            import musicbrainzngs
            musicbrainzngs.set_useragent("Milo", "1.0", "https://music.milo.audio")
        except ImportError:
            self._logger.warning("musicbrainzngs not installed, metadata lookup disabled")

    async def initialize(self) -> None:
        """Load cached data from disk and ensure directories exist."""
        os.makedirs(self._covers_dir, exist_ok=True)
        await self._load_data()
        self._loaded = True

    # =========================================================================
    # DISC DETECTION
    # =========================================================================

    def check_drive_present(self) -> bool:
        """Check if a CD drive is connected at /dev/sr0."""
        return os.path.exists(CD_DEVICE)

    def check_disc_present(self) -> bool:
        """Check if a disc is inserted via ioctl CDROM_DRIVE_STATUS."""
        try:
            fd = os.open(CD_DEVICE, os.O_RDONLY | os.O_NONBLOCK)
            try:
                status = fcntl.ioctl(fd, CDROM_DRIVE_STATUS)
                return status == CDS_DISC_OK
            finally:
                os.close(fd)
        except OSError:
            return False

    # =========================================================================
    # DISC TOC READING
    # =========================================================================

    async def read_disc(self) -> Optional[Tuple[str, str, List[Dict[str, Any]]]]:
        """
        Read the disc TOC via libdiscid.

        Returns:
            (disc_id, toc_string, tracks) where tracks is a list of
            {"number": int, "duration": int} dicts, or None if read fails.
        """
        try:
            result = await asyncio.to_thread(self._read_disc_sync)
            return result
        except Exception as e:
            self._logger.error(f"Failed to read disc: {e}")
            return None

    def _read_disc_sync(self) -> Optional[Tuple[str, str, List[Dict[str, Any]]]]:
        """Synchronous disc read (runs in thread)."""
        try:
            import discid
            disc = discid.read(CD_DEVICE)
        except Exception as e:
            self._logger.error(f"discid.read() failed: {e}")
            return None

        tracks = []
        for i, track in enumerate(disc.tracks):
            duration = track.seconds
            tracks.append({
                "number": i + 1,
                "duration": duration,
            })

        return disc.id, disc.toc_string, tracks

    # =========================================================================
    # MUSICBRAINZ METADATA LOOKUP
    # =========================================================================

    async def lookup_metadata(
        self, disc_id: str, toc_string: str, tracks: List[Dict[str, Any]]
    ) -> DiscInfo:
        """
        Lookup disc metadata from cache or MusicBrainz.

        Falls back to generic track names if MusicBrainz is unavailable
        or the disc is unknown.
        """
        # Check cache first
        cached = self._cache.get(disc_id)
        if cached:
            self._logger.info(f"Cache hit for disc {disc_id}")
            return self._disc_info_from_cache(disc_id, cached)

        # Try MusicBrainz lookup (in thread to avoid blocking)
        try:
            result = await asyncio.to_thread(
                self._lookup_musicbrainz_sync, disc_id, toc_string
            )
        except Exception as e:
            self._logger.warning(f"MusicBrainz lookup failed: {e}")
            result = None

        if result:
            album, artist, year, release_mbid, mb_tracks = result

            # Merge MusicBrainz track titles with TOC durations
            merged_tracks = self._merge_tracks(mb_tracks, tracks)

            # Download cover art (fire and forget on failure)
            has_cover = False
            if release_mbid:
                has_cover = await self._download_cover(disc_id, release_mbid)

            # Cache the result
            cache_entry = {
                "album": album,
                "artist": artist,
                "year": year,
                "has_cover": has_cover,
                "tracks": [{"number": t["number"], "title": t["title"], "duration": t["duration"]} for t in merged_tracks],
                "cached_at": int(time.time()),
            }
            self._cache[disc_id] = cache_entry
            await self._save_data()

            total_duration = sum(t["duration"] for t in merged_tracks)
            cover_url = f"/api/cd/cover/{disc_id}" if has_cover else None

            return DiscInfo(
                disc_id=disc_id,
                album=album,
                artist=artist,
                year=year,
                cover_url=cover_url,
                track_count=len(merged_tracks),
                total_duration=total_duration,
                tracks=[TrackInfo(**t) for t in merged_tracks],
            )

        # Fallback: generic track names with TOC durations
        return self._build_fallback_disc_info(disc_id, tracks)

    def _lookup_musicbrainz_sync(
        self, disc_id: str, toc_string: str
    ) -> Optional[Tuple[str, str, str, str, List[Dict[str, Any]]]]:
        """
        Synchronous MusicBrainz lookup (runs in thread).

        Returns:
            (album, artist, year, release_mbid, tracks) or None
        """
        try:
            import musicbrainzngs
        except ImportError:
            return None

        # Try exact disc ID match first
        try:
            result = musicbrainzngs.get_releases_by_discid(
                disc_id, includes=["artists", "recordings"]
            )
            release = self._extract_release(result)
            if release:
                return self._parse_release(release)
        except musicbrainzngs.ResponseError:
            self._logger.debug(f"No exact match for disc ID {disc_id}")

        # Fallback: fuzzy match by TOC
        try:
            result = musicbrainzngs.get_releases_by_discid(
                disc_id, toc=toc_string, includes=["artists", "recordings"]
            )
            release = self._extract_release(result)
            if release:
                return self._parse_release(release)
        except musicbrainzngs.ResponseError:
            self._logger.debug(f"No fuzzy match for disc TOC")

        return None

    def _extract_release(self, result: Dict) -> Optional[Dict]:
        """Extract the best release from MusicBrainz response."""
        if "disc" in result and "release-list" in result["disc"]:
            releases = result["disc"]["release-list"]
            if releases:
                return releases[0]

        if "release-list" in result:
            releases = result["release-list"]
            if releases:
                return releases[0]

        return None

    def _parse_release(
        self, release: Dict
    ) -> Tuple[str, str, str, str, List[Dict[str, Any]]]:
        """Parse a MusicBrainz release into structured data."""
        album = release.get("title", "Unknown Album")
        release_mbid = release.get("id", "")

        # Artist
        artist_credit = release.get("artist-credit", [])
        if artist_credit:
            artist = artist_credit[0].get("artist", {}).get("name", "Unknown Artist")
        else:
            artist = "Unknown Artist"

        # Year
        date = release.get("date", "")
        year = date[:4] if date else ""

        # Tracks from medium list
        tracks = []
        medium_list = release.get("medium-list", [])
        if medium_list:
            track_list = medium_list[0].get("track-list", [])
            for i, track in enumerate(track_list):
                recording = track.get("recording", {})
                title = recording.get("title", f"Track {i + 1}")
                # MusicBrainz duration is in milliseconds
                length_ms = recording.get("length")
                duration = int(int(length_ms) / 1000) if length_ms else 0
                tracks.append({
                    "number": i + 1,
                    "title": title,
                    "duration": duration,
                })

        return album, artist, year, release_mbid, tracks

    def _merge_tracks(
        self, mb_tracks: List[Dict], toc_tracks: List[Dict]
    ) -> List[Dict[str, Any]]:
        """Merge MusicBrainz titles with TOC durations (TOC durations are authoritative)."""
        merged = []
        for i, toc_track in enumerate(toc_tracks):
            title = f"Track {i + 1}"
            if i < len(mb_tracks):
                title = mb_tracks[i].get("title", title)
            merged.append({
                "number": toc_track["number"],
                "title": title,
                "duration": toc_track["duration"],
            })
        return merged

    def _build_fallback_disc_info(
        self, disc_id: str, tracks: List[Dict[str, Any]]
    ) -> DiscInfo:
        """Build DiscInfo with generic track names when MusicBrainz is unavailable."""
        fallback_tracks = [
            TrackInfo(number=t["number"], title=f"Track {t['number']}", duration=t["duration"])
            for t in tracks
        ]
        total_duration = sum(t["duration"] for t in tracks)

        return DiscInfo(
            disc_id=disc_id,
            track_count=len(fallback_tracks),
            total_duration=total_duration,
            tracks=fallback_tracks,
        )

    # =========================================================================
    # COVER ART
    # =========================================================================

    async def _download_cover(self, disc_id: str, release_mbid: str) -> bool:
        """Download cover art from MusicBrainz Cover Art Archive."""
        cover_path = os.path.join(self._covers_dir, f"{disc_id}.jpg")
        if os.path.exists(cover_path):
            return True

        try:
            image_data = await asyncio.to_thread(
                self._download_cover_sync, release_mbid
            )
            if image_data:
                async with aiofiles.open(cover_path, "wb") as f:
                    await f.write(image_data)
                self._logger.info(f"Cover art saved for disc {disc_id}")
                return True
        except Exception as e:
            self._logger.warning(f"Cover art download failed: {e}")

        return False

    def _download_cover_sync(self, release_mbid: str) -> Optional[bytes]:
        """Synchronous cover art download (runs in thread)."""
        try:
            import musicbrainzngs
            return musicbrainzngs.get_image_front(release_mbid, size="500")
        except Exception:
            return None

    def get_cover_path(self, disc_id: str) -> Optional[str]:
        """Get the file path for a disc's cover art, or None if not available."""
        cover_path = os.path.join(self._covers_dir, f"{disc_id}.jpg")
        if os.path.exists(cover_path):
            return cover_path
        return None

    # =========================================================================
    # CACHE HELPERS
    # =========================================================================

    def _disc_info_from_cache(self, disc_id: str, entry: Dict) -> DiscInfo:
        """Build DiscInfo from a cache entry."""
        tracks = [TrackInfo(**t) for t in entry.get("tracks", [])]
        total_duration = sum(t.duration for t in tracks)
        has_cover = entry.get("has_cover", False)
        cover_url = f"/api/cd/cover/{disc_id}" if has_cover else None

        return DiscInfo(
            disc_id=disc_id,
            album=entry.get("album"),
            artist=entry.get("artist"),
            year=entry.get("year"),
            cover_url=cover_url,
            track_count=len(tracks),
            total_duration=total_duration,
            tracks=tracks,
        )

    # =========================================================================
    # DATA PERSISTENCE
    # =========================================================================

    async def _load_data(self) -> None:
        """Load cached disc data from disk."""
        try:
            if os.path.exists(self._data_file):
                async with self._file_lock:
                    async with aiofiles.open(self._data_file, "r", encoding="utf-8") as f:
                        data = json.loads(await f.read())
                        self._cache = data.get("discs", {})
                self._logger.info(f"Loaded {len(self._cache)} cached disc(s)")
            else:
                self._cache = {}
        except Exception as e:
            self._logger.error(f"Error loading cd_data.json: {e}")
            self._cache = {}

    @handle_errors(default=False)
    async def _save_data(self) -> bool:
        """Save disc cache to disk with atomic write."""
        async with self._file_lock:
            temp_file = self._data_file + ".tmp"

            async with aiofiles.open(temp_file, "w", encoding="utf-8") as f:
                await f.write(json.dumps({"discs": self._cache}, ensure_ascii=False, indent=2))
                await f.write("\n")
                await f.flush()
                fd = f.fileno()
                await asyncio.to_thread(os.fsync, fd)

            os.replace(temp_file, self._data_file)

        return True
