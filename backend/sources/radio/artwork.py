# backend/sources/radio/artwork.py
"""Cover-art resolution for radio in-band metadata.

In-band ICY metadata carries only artist/title text (no artwork), unlike Shazam
which returns an image URL. This resolver fills that gap: it looks the track up
on the iTunes Search API and returns the release's cover-art URL, so in-band
stations (walmradio, stereoscenic, …) show a cover just like Shazam-recognised
tracks do — the URL lands in the same `metadata.track_artwork` field, and Apple
Music is the same art source Shazam draws from, so the look is consistent.

iTunes was chosen over MusicBrainz/Cover Art Archive after live measurement: on
the deep-catalogue vinyl/jazz/ambient these in-band stations play, CAA indexed
almost none of the tracks while iTunes matched them correctly.

Fully async (aiohttp, like shazam.py). Results are cached per (artist, title),
misses included, and calls are serialised + lightly throttled. A plausibility
gate (returned artist AND title must overlap the query) keeps a fuzzy search
from pinning a wrong cover — a wrong cover is worse than none.
"""
import asyncio
import logging
import re
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger("source.radio.artwork")

_ITUNES_URL = "https://itunes.apple.com/search"
_HTTP_TIMEOUT = 8
_ITUNES_MIN_INTERVAL = 0.5  # polite spacing between calls (serialised by lock)
_CACHE_MAX = 500

# Fraction of query tokens that must appear in the matched field for it to count
# as the same artist / title. 0.6 tolerates extra credits ("A & B", remaster
# tags) and one missing word in a long title, while still requiring a 2-token
# field to match fully — so "Buddy Greco" ≠ "Buddy Holly" (shared first name
# only = 0.5) is rejected.
_MATCH_RATIO = 0.6

# Parenthetical annotations dropped from the *query* only (display keeps them).
_PARENS_RE = re.compile(r"\s*\([^)]*\)")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _tokens(text: str) -> List[str]:
    """Lowercase alphanumeric tokens of a string (for loose matching)."""
    return [t for t in _NON_ALNUM_RE.sub(" ", (text or "").lower()).split() if t]


class RadioArtworkResolver:
    """Resolve a cover-art URL from an in-band artist/title via iTunes Search."""

    def __init__(self) -> None:
        # (artist|title) → URL or None (misses cached to avoid re-querying).
        self._cache: "OrderedDict[str, Optional[str]]" = OrderedDict()
        self._lock = asyncio.Lock()
        self._last_call = 0.0

    @staticmethod
    def _cache_key(artist: str, title: str) -> str:
        return f"{artist.strip().lower()}|{title.strip().lower()}"

    @staticmethod
    def _clean_query_field(text: str) -> str:
        """Strip parenthetical annotations for matching (display keeps them)."""
        return _PARENS_RE.sub("", text or "").strip()

    async def resolve(self, artist: str, title: str) -> Optional[str]:
        """Return a cover-art URL for (artist, title), or None.

        Cached per (artist, title); misses are cached too so an unmatched
        title is not re-queried on every poll.
        """
        title = (title or "").strip()
        if not title:
            return None

        key = self._cache_key(artist, title)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]

        async with self._lock:
            # Another waiter may have resolved the same key while we waited.
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            url = await self._lookup(artist, title)
            self._store(key, url)
            return url

    def _store(self, key: str, url: Optional[str]) -> None:
        self._cache[key] = url
        self._cache.move_to_end(key)
        while len(self._cache) > _CACHE_MAX:
            self._cache.popitem(last=False)

    async def _lookup(self, artist: str, title: str) -> Optional[str]:
        q_title = self._clean_query_field(title)
        q_artist = self._clean_query_field(artist)
        if not q_title:
            return None

        term = f"{q_artist} {q_title}".strip()
        params = {"term": term, "entity": "song", "limit": "5"}
        try:
            timeout = aiohttp.ClientTimeout(total=_HTTP_TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                await self._throttle()
                async with session.get(_ITUNES_URL, params=params) as resp:
                    if resp.status != 200:
                        logger.info(f"iTunes search HTTP {resp.status}")
                        return None
                    # iTunes returns text/javascript; parse leniently.
                    data = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            logger.info(f"Artwork lookup failed for {artist} - {title}: {e}")
            return None

        url = self._pick_artwork(data, q_artist, q_title)
        if url:
            logger.info(f"Cover found for {artist} - {title}")
        return url

    async def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < _ITUNES_MIN_INTERVAL:
            await asyncio.sleep(_ITUNES_MIN_INTERVAL - elapsed)
        self._last_call = time.monotonic()

    @classmethod
    def _pick_artwork(
        cls, data: Dict[str, Any], q_artist: str, q_title: str
    ) -> Optional[str]:
        """First plausible result's cover URL, upscaled. None if none plausible."""
        for res in data.get("results", []):
            if cls._is_plausible(q_artist, q_title, res):
                art = res.get("artworkUrl100") or res.get("artworkUrl60")
                if art:
                    return cls._upscale(art)
        return None

    @staticmethod
    def _is_plausible(q_artist: str, q_title: str, res: Dict[str, Any]) -> bool:
        """Returned artist AND title must overlap the query (guards wrong covers).

        The title always matters; the artist is checked only when the query
        carried one (in-band "Title by Artist" / "Artist - Title" splits do).
        """
        def covered(query: str, candidate: str) -> bool:
            q = _tokens(query)
            if not q:
                return True  # nothing to match against → not a constraint
            c = set(_tokens(candidate))
            hits = sum(1 for t in q if t in c)
            return hits / len(q) >= _MATCH_RATIO

        title_ok = covered(q_title, res.get("trackName", ""))
        artist_ok = covered(q_artist, res.get("artistName", ""))
        return title_ok and artist_ok

    @staticmethod
    def _upscale(url: str) -> str:
        """Upgrade the default 100×100 iTunes thumbnail to 600×600."""
        return url.replace("100x100bb", "600x600bb").replace("100x100", "600x600")
