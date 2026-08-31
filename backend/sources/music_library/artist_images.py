# backend/sources/music_library/artist_images.py
"""Artist photos for the Music Library, resolved by Milō rather than Navidrome.

Navidrome's own online tier is deliberately switched off for artist art
(``ArtistArtPriority`` in install/navidrome.sh no longer lists ``external``),
because the picture it chose was routinely the wrong person. Its agent searches
Deezer by name and keeps the FIRST result whose name matches — and Deezer's
search is not ordered by popularity, so the first "Amy Winehouse" is a duplicate
profile with 741 fans and no photo while the real one, 3.8 million fans, sits
second. Measured over this unit's 108 artists: 25 of them got someone else's
face, and Deezer's own generic silhouette counted as a photo for a dozen more.

The fix is the field Navidrome ignores. Deezer returns ``nb_fan`` on every hit,
so the rule here is: keep the hits whose name matches exactly (accent- and
case-insensitively), drop the ones with no real photo, and take the most
followed of what remains. Same 108 artists: 105 resolved, all 25 corrected, and
the 3 left over are "Various Artists" and two compilation credits — not artists,
and Milō's own placeholder is the right answer for them.

Deliberately strict on the name: no fuzzy fallback. A missing photo is a
placeholder, a wrong photo is a lie, and the whole reason this module exists is
that the loose rule shipped upstream produces the second one.

Navidrome still answers first for artist art that is *local* (an ``artist.*``
file the user put beside their music). This is only the online tier, reached
when Navidrome has nothing — so a user who ships their own art keeps it.
"""
import asyncio
import hashlib
import logging
import os
import re
import time
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

import aiofiles
import aiohttp

from backend.config.constants import ARTIST_IMAGES_DIR
from backend.shared.network import is_network_error

logger = logging.getLogger("source.music_library.artist_images")

_DEEZER_SEARCH_URL = "https://api.deezer.com/search/artist"
_HTTP_TIMEOUT = 10
# Deezer answers 50 requests per 5 seconds per IP, and a breach is HTTP 200 with
# an `error` body — see _search. Calls are serialised and spaced well under that
# ceiling: 108 artists resolved back to back at this interval, zero refusals.
_MIN_INTERVAL = 0.15
# How many hits to consider. The right artist is never far down when the name
# matches exactly; 25 is Deezer's own page size.
_SEARCH_LIMIT = 25

# Deezer serves a generic grey silhouette for a profile that has no picture, and
# the CDN slug it serves it under is the MD5 of the empty string. Recognising it
# by URL costs nothing and is what keeps that silhouette — a real, resizable
# image no byte-level rule can tell from a photo — out of the library.
_NO_PHOTO_SLUG = "d41d8cd98f00b204e9800998ecf8427e"

# Which of Deezer's four fixed sizes is cached and served. Artists render at
# ~160 px today (MediaRow thumbnails in the A–Z list and search), so 500 px is
# already generous and leaves room for an artist header without a second cache
# tier; the whole picture is ~40 kB.
_PICTURE_FIELD = "picture_big"

# Navidrome cover ids for an artist are `ar-<artist id>_<n>`, where the suffix
# changes when its art does. Only the prefix is contractual here — the id is
# handed straight back to Subsonic getArtist, which is what turns it into a name.
_ARTIST_COVER_PREFIX = "ar-"

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """The form two artist names are compared in: accents folded away, case
    folded, inner whitespace collapsed.

    Accents matter more than they look: Deezer lists the same artist as "ROCé"
    and "Rocé", and a byte comparison would call them different people.
    """
    decomposed = unicodedata.normalize("NFKD", name or "")
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _WHITESPACE_RE.sub(" ", stripped).strip().casefold()


def has_photo(artist: Dict[str, Any]) -> bool:
    """True when a Deezer hit carries a real picture rather than the silhouette."""
    picture = artist.get(_PICTURE_FIELD) or ""
    return bool(picture) and _NO_PHOTO_SLUG not in picture


def pick_artist(name: str, hits: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The most followed Deezer artist whose name is exactly ``name`` and who has
    a photo, or None.

    The two filters are what make the answer trustworthy and the ranking is what
    makes it the right person; none of the three is optional. Dropping the exact
    match gives "Adèle & Robin" for Adele, dropping the photo test gives a grey
    silhouette, and dropping the ranking is upstream's bug.
    """
    target = normalize_name(name)
    if not target:
        return None
    candidates = [
        hit
        for hit in hits
        if normalize_name(hit.get("name", "")) == target and has_photo(hit)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda hit: hit.get("nb_fan") or 0)


class ArtistImageService:
    """Resolve, cache and serve the photo of an artist by name.

    Owned by the Music Library source and reached as ``source.artist_images``;
    the cover route calls it when Navidrome has no art for an ``ar-`` id.
    """

    def __init__(self, get_client) -> None:
        # Async accessor for the shared Navidrome client (built lazily by the
        # source) — used only to turn a cover id into an artist name.
        self._get_client = get_client
        # Serialises Deezer calls and spaces them; see _MIN_INTERVAL.
        self._lock = asyncio.Lock()
        self._last_call = 0.0
        # Navidrome artist id → name. Stable for the life of the id, which
        # changes when the artist is renamed, so this never goes stale.
        self._names: Dict[str, str] = {}
        # Normalised names Deezer has no usable photo for. Cached so a library
        # of compilation credits does not re-ask on every render; dropped on a
        # rescan (invalidate), the one moment the artist list itself changes.
        # A *transient* failure — offline, quota — is deliberately never
        # recorded here, or one bad minute would freeze the gap until a rescan.
        self._missing: set = set()

    def invalidate(self) -> None:
        """Give the artists with no photo another chance (called on a rescan)."""
        self._missing.clear()

    async def get_cover(self, cover_id: str) -> Optional[Tuple[bytes, str]]:
        """Photo bytes + content type for a Navidrome *artist* cover id, or None.

        None for any other kind of id, so the cover route can call this on every
        miss without deciding what kind of item it was looking at.
        """
        if not cover_id.startswith(_ARTIST_COVER_PREFIX):
            return None
        artist_id = cover_id[len(_ARTIST_COVER_PREFIX):].rsplit("_", 1)[0]
        if not artist_id:
            return None
        name = await self._artist_name(artist_id)
        if not name:
            return None
        return await self.get_image(name)

    async def get_image(self, name: str) -> Optional[Tuple[bytes, str]]:
        """Photo bytes + content type for an artist name, or None."""
        key = normalize_name(name)
        if not key:
            return None

        path = self._cache_path(key)
        cached = await self._read_cache(path)
        if cached:
            return cached, "image/jpeg"
        if key in self._missing:
            return None

        hits = await self._search(name)
        if hits is None:
            return None  # transient: not remembered as a miss
        artist = pick_artist(name, hits)
        if artist is None:
            logger.info("No Deezer artist matching %r with a photo", name)
            self._missing.add(key)
            return None

        data = await self._download(artist[_PICTURE_FIELD])
        if not data:
            # Empty is treated as a transient failure, not a photo: caching zero
            # bytes would make every later request a hit on nothing.
            return None
        logger.info(
            "Artist photo for %r: Deezer #%s (%s fans)",
            name,
            artist.get("id"),
            artist.get("nb_fan"),
        )
        await self._write_cache(path, data)
        return data, "image/jpeg"

    # =========================================================================
    # INTERNALS
    # =========================================================================

    async def _artist_name(self, artist_id: str) -> Optional[str]:
        """The artist's name, from Navidrome, memoised per id."""
        if artist_id in self._names:
            return self._names[artist_id]
        client = await self._get_client()
        if client is None:
            return None
        artist = await client.get_artist(artist_id)
        name = (artist or {}).get("name")
        if not name:
            return None
        self._names[artist_id] = name
        return name

    async def _search(self, name: str) -> Optional[List[Dict[str, Any]]]:
        """Deezer hits for ``name``, or None when the answer cannot be trusted.

        The None case is the one that matters. A quota breach is served as HTTP
        200 with an ``error`` body and, if the caller only reads ``data``, as an
        EMPTY result set — indistinguishable from "this artist does not exist"
        unless the error is looked for. Reporting it as None is what stops one
        burst of throttling from being remembered as a permanent miss.
        """
        async with self._lock:
            await self._space_out()
            try:
                timeout = aiohttp.ClientTimeout(total=_HTTP_TIMEOUT)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(
                        _DEEZER_SEARCH_URL,
                        params={"q": name, "limit": _SEARCH_LIMIT},
                    ) as resp:
                        if resp.status != 200:
                            logger.warning(
                                "Deezer artist search HTTP %s for %r", resp.status, name
                            )
                            return None
                        payload = await resp.json(content_type=None)
            except Exception as exc:
                if is_network_error(exc):
                    logger.info("Deezer not reachable for artist %r: %s", name, exc)
                else:
                    logger.warning("Deezer artist search failed for %r: %s", name, exc)
                return None

        if "error" in payload:
            logger.warning("Deezer refused the search for %r: %s", name, payload["error"])
            return None
        return payload.get("data") or []

    async def _download(self, url: str) -> Optional[bytes]:
        """Picture bytes from Deezer's CDN, or None on any failure."""
        try:
            timeout = aiohttp.ClientTimeout(total=_HTTP_TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        logger.warning("Deezer picture HTTP %s for %s", resp.status, url)
                        return None
                    return await resp.read()
        except Exception as exc:
            if is_network_error(exc):
                logger.info("Deezer CDN not reachable: %s", exc)
            else:
                logger.warning("Deezer picture download failed: %s", exc)
            return None

    async def _space_out(self) -> None:
        """Hold the caller until _MIN_INTERVAL has passed since the last search."""
        wait = self._last_call + _MIN_INTERVAL - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_call = time.monotonic()

    @staticmethod
    def _cache_path(key: str) -> str:
        """On-disk name for a normalised artist name. Hashed rather than spelled
        out because an artist name is not a filename — "AC/DC" alone rules that
        out, and it is the same trap that makes Navidrome's own ArtistImageFolder
        unable to represent those artists at all."""
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return os.path.join(str(ARTIST_IMAGES_DIR), f"{digest}.jpg")

    @staticmethod
    async def _read_cache(path: str) -> Optional[bytes]:
        try:
            async with aiofiles.open(path, "rb") as f:
                return await f.read()
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.warning("Could not read cached artist photo %s: %s", path, exc)
            return None

    @staticmethod
    async def _write_cache(path: str, data: bytes) -> None:
        """Store the photo, atomically — a picture truncated by a power cut would
        be served forever, since a cache hit is decided by the file existing."""
        try:
            os.makedirs(str(ARTIST_IMAGES_DIR), exist_ok=True)
            temp_path = f"{path}.tmp"
            async with aiofiles.open(temp_path, "wb") as f:
                await f.write(data)
                await f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, path)
        except OSError as exc:
            logger.warning("Could not cache artist photo %s: %s", path, exc)
