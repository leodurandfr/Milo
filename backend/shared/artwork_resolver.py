# backend/shared/artwork_resolver.py
"""Cover-art resolution from text metadata, via the iTunes Search API.

For the sources whose feed carries artist/title text but no image: radio's
in-band ICY metadata, and Bluetooth AVRCP (whose 1.6 cover-art feature rides an
OBEX channel BlueZ gives no client for). The resolved URL lands in the same
field a source with real artwork would fill, so the player looks the same
either way.

iTunes was chosen over MusicBrainz/Cover Art Archive after live measurement: on
the deep-catalogue vinyl/jazz/ambient the in-band stations play, CAA indexed
almost none of the tracks while iTunes matched them correctly. It is also the
same art source Shazam draws from, which keeps radio's two paths consistent.

Fully async (aiohttp). Results are cached per query, misses included, and calls
are serialised + lightly throttled. A plausibility gate (the returned artist
AND name must overlap the query) keeps a fuzzy search from pinning a wrong
cover — a wrong cover is worse than none.
"""
import asyncio
import logging
import re
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)

_ITUNES_URL = "https://itunes.apple.com/search"
_HTTP_TIMEOUT = 8
_ITUNES_MIN_INTERVAL = 0.5  # polite spacing between calls (serialised by lock)
_CACHE_MAX = 500

# The size _upscale asks iTunes for, and therefore the width of every URL this
# module returns. Declared rather than inlined because a consumer has to be able
# to say how wide a resolved cover is: DLNA publishes it as album_art_width, and
# the frontend's untrusted-sender gate judges a cover of unstated size as if it
# had none -- which would make a resolved cover invisible.
RESOLVED_ARTWORK_PX = 600

# Fraction of query tokens that must appear in the matched field for it to count
# as the same artist / name. 0.6 tolerates extra credits ("A & B", remaster
# tags) and one missing word in a long title, while still requiring a 2-token
# field to match fully — so "Buddy Greco" ≠ "Buddy Holly" (shared first name
# only = 0.5) is rejected.
_MATCH_RATIO = 0.6

# Parenthetical annotations dropped from the *query* only (display keeps them).
_PARENS_RE = re.compile(r"\s*\([^)]*\)")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

# The two searches, in the order they are tried when an album is known. iTunes
# names the matched field differently per entity, hence the pairing.
_ALBUM_ENTITY = ("album", "collectionName")
_SONG_ENTITY = ("song", "trackName")


def _tokens(text: str) -> List[str]:
    """Lowercase alphanumeric tokens of a string (for loose matching)."""
    return [t for t in _NON_ALNUM_RE.sub(" ", (text or "").lower()).split() if t]


class ArtworkResolver:
    """Resolve a cover-art URL from artist/title (and album when known)."""

    def __init__(self) -> None:
        # (artist|title|album) → URL or None (misses cached to avoid re-querying).
        self._cache: "OrderedDict[str, Optional[str]]" = OrderedDict()
        self._lock = asyncio.Lock()
        self._last_call = 0.0

    @staticmethod
    def _cache_key(artist: str, title: str, album: str) -> str:
        return "|".join((part or "").strip().lower() for part in (artist, title, album))

    @staticmethod
    def _clean_query_field(text: str) -> str:
        """Strip parenthetical annotations for matching (display keeps them)."""
        return _PARENS_RE.sub("", text or "").strip()

    async def resolve(
        self, artist: str, title: str, album: str = ""
    ) -> Optional[str]:
        """Return a cover-art URL for the track, or None.

        Cached per query; misses are cached too so an unmatched title is not
        re-queried on every poll.
        """
        title = (title or "").strip()
        album = (album or "").strip()
        if not title and not album:
            return None

        key = self._cache_key(artist, title, album)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]

        async with self._lock:
            # Another waiter may have resolved the same key while we waited.
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            url = await self._lookup(artist, title, album)
            self._store(key, url)
            return url

    def _store(self, key: str, url: Optional[str]) -> None:
        self._cache[key] = url
        self._cache.move_to_end(key)
        while len(self._cache) > _CACHE_MAX:
            self._cache.popitem(last=False)

    async def _lookup(self, artist: str, title: str, album: str) -> Optional[str]:
        """Album search first when an album is known, then the track search.

        An album has one cover; a track can appear on a dozen compilations with
        a dozen different ones, so the album query is both more accurate and
        more stable across the tracks of one record. Sources that know no album
        (radio's in-band ICY) skip straight to the track search, which is what
        they did before this resolver was shared.
        """
        q_artist = self._clean_query_field(artist)

        if album:
            url = await self._search(_ALBUM_ENTITY, q_artist, self._clean_query_field(album))
            if url:
                return url

        q_title = self._clean_query_field(title)
        if not q_title:
            return None
        return await self._search(_SONG_ENTITY, q_artist, q_title)

    async def _search(
        self, entity: tuple, q_artist: str, q_name: str
    ) -> Optional[str]:
        """One iTunes query for one entity kind; None when nothing is plausible."""
        entity_name, result_key = entity
        term = f"{q_artist} {q_name}".strip()
        params = {"term": term, "entity": entity_name, "limit": "5"}
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
            logger.info(f"Artwork lookup failed for {term}: {e}")
            return None

        url = self._pick_artwork(data, q_artist, q_name, result_key)
        if url:
            logger.info(f"Cover found for {term} ({entity_name})")
        return url

    async def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < _ITUNES_MIN_INTERVAL:
            await asyncio.sleep(_ITUNES_MIN_INTERVAL - elapsed)
        self._last_call = time.monotonic()

    @classmethod
    def _pick_artwork(
        cls, data: Dict[str, Any], q_artist: str, q_name: str, result_key: str
    ) -> Optional[str]:
        """First plausible result's cover URL, upscaled. None if none plausible."""
        for res in data.get("results", []):
            if cls._is_plausible(q_artist, q_name, res, result_key):
                art = res.get("artworkUrl100") or res.get("artworkUrl60")
                if art:
                    return cls._upscale(art)
        return None

    @staticmethod
    def _is_plausible(
        q_artist: str, q_name: str, res: Dict[str, Any], result_key: str
    ) -> bool:
        """Returned artist AND name must overlap the query (guards wrong covers).

        The name always matters; the artist is checked only when the query
        carried one (in-band "Title by Artist" / "Artist - Title" splits do,
        and AVRCP always does).
        """
        def covered(query: str, candidate: str) -> bool:
            q = _tokens(query)
            if not q:
                return True  # nothing to match against → not a constraint
            c = set(_tokens(candidate))
            hits = sum(1 for t in q if t in c)
            return hits / len(q) >= _MATCH_RATIO

        name = res.get(result_key, "")
        name_ok = covered(q_name, name)
        # The artist is matched against the title too, because a featured
        # credit sits on whichever side of the pair the source chose. AVRCP
        # hands the whole credit list as one artist string while iTunes keeps
        # the feature in the title: "Ice Cube, Das EFX" against artistName
        # "Ice Cube" scores 0.50 and rejected a cover whose trackName was
        # "Check Yo Self (feat. Das EFX) [Remix]" -- an exact match. The ratio
        # is over the *query*'s tokens, so a query richer than the catalogue
        # is penalised unless the tokens are allowed to be found where the
        # catalogue actually put them. It costs no strictness: the title must
        # still match on its own, so "Buddy Greco" stays 0.50 against
        # "Buddy Holly" singing "Oh Boy".
        artist_ok = covered(q_artist, f"{res.get('artistName', '')} {name}")
        return name_ok and artist_ok

    @staticmethod
    def _upscale(url: str) -> str:
        """Upgrade the default 100×100 iTunes thumbnail to RESOLVED_ARTWORK_PX."""
        big = f"{RESOLVED_ARTWORK_PX}x{RESOLVED_ARTWORK_PX}"
        return url.replace("100x100bb", f"{big}bb").replace("100x100", big)
