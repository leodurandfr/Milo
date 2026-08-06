# backend/core/lyrics/service.py
"""Lyrics resolution via LRCLIB.

Transverse feature: keyed off the now-playing (artist, title, album, duration)
of whichever source is active, independent of the source itself. Fetches from
LRCLIB (lrclib.net — no API key, returns both synced LRC and plain lyrics),
caches results on disk under /var/lib/milo/lyrics/ (negatives included, so a
track with no lyrics is not re-queried), and returns a normalized shape the
frontend Lyrics app renders directly.

Fully async (aiohttp, per-lookup session like shared/artwork_resolver.py). A genuine
no-match returns found=False and is cached as a negative, so it is not
re-queried. An unreachable LRCLIB is different: it raises LyricsUnavailable and
caches nothing, so a brief outage isn't frozen into "no lyrics" for the track
(the route maps it to a 200 + status=error, which the frontend also skips
caching). The disk cache is a disposable derived cache — no schema_version /
fail-loud protocol; wipe the directory if the shape ever changes.
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiofiles
import aiohttp

logger = logging.getLogger("core.lyrics")

_LRCLIB_BASE = "https://lrclib.net/api"
_HTTP_TIMEOUT = 8
_MIN_INTERVAL = 0.3  # polite spacing between LRCLIB calls (serialised by lock)
_MEM_CACHE_MAX = 256
# LRCLIB asks callers to identify themselves with a User-Agent.
_USER_AGENT = "Milo/1.0 (https://github.com/leodurandfr/milo)"

# Parenthetical annotations + trailing "- …" suffixes dropped when building the
# match key and the search-fallback query (e.g. "(feat. X)", "- Remastered 2011").
_PARENS_RE = re.compile(r"\s*[\(\[][^)\]]*[\)\]]")
_SUFFIX_RE = re.compile(r"\s*-\s.*$")
# One LRC line = one or more [mm:ss.xx] stamps followed by the lyric text.
_LRC_LINE_RE = re.compile(r"((?:\[\d{1,2}:\d{2}(?:[.:]\d{1,3})?\])+)(.*)")
_LRC_TS_RE = re.compile(r"\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]")


class LyricsUnavailable(Exception):
    """LRCLIB could not be reached — distinct from a genuine "no lyrics" match.

    Raised so neither the disk cache nor the frontend's per-session cache stores
    the outage as a real negative; the next lookup retries.
    """


def _empty() -> Dict[str, Any]:
    return {"found": False, "synced": None, "plain": None}


def _clean(text: str) -> str:
    """Strip parentheticals + trailing suffixes for matching (display keeps them)."""
    text = _PARENS_RE.sub("", text or "")
    text = _SUFFIX_RE.sub("", text)
    return text.strip()


def _parse_lrc(lrc: str) -> Optional[List[Dict[str, Any]]]:
    """Parse an LRC string into sorted [{t: ms, line: str}]; None if empty."""
    lines: List[Dict[str, Any]] = []
    for raw in lrc.splitlines():
        m = _LRC_LINE_RE.match(raw.strip())
        if not m:
            continue
        stamps, text = m.group(1), m.group(2).strip()
        for ts in _LRC_TS_RE.finditer(stamps):
            mm, ss, frac = ts.group(1), ts.group(2), ts.group(3) or "0"
            # Fraction is centiseconds (2 digits) or milliseconds (3) — pad to ms.
            frac_ms = int(frac.ljust(3, "0")[:3])
            t = (int(mm) * 60 + int(ss)) * 1000 + frac_ms
            lines.append({"t": t, "line": text})
    lines.sort(key=lambda x: x["t"])
    return lines or None


def _from_record(record: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Normalize an LRCLIB record into {found, synced, plain}."""
    if not record or record.get("instrumental"):
        return _empty()
    synced_raw = record.get("syncedLyrics")
    plain_raw = record.get("plainLyrics")
    synced = _parse_lrc(synced_raw) if synced_raw else None
    plain = plain_raw or ("\n".join(ln["line"] for ln in synced) if synced else None)
    if not synced and not plain:
        return _empty()
    return {"found": True, "synced": synced, "plain": plain}


class LyricsService:
    """Resolve synced/plain lyrics for a track via LRCLIB, cached to disk."""

    CACHE_DIR = Path("/var/lib/milo/lyrics")

    def __init__(self) -> None:
        # sha1(artist|title|album) → {found, synced, plain}. Misses cached too.
        self._mem: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._lock = asyncio.Lock()
        self._last_call = 0.0
        try:
            self.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("Could not create lyrics cache dir: %s", e)

    async def get_lyrics(
        self,
        artist: str,
        title: str,
        album: Optional[str] = None,
        duration_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Return {found, synced, plain} for a track.

        Raises LyricsUnavailable if LRCLIB could not be reached; a genuine
        no-match is a normal found=False result.
        """
        artist = (artist or "").strip()
        title = (title or "").strip()
        if not artist or not title:
            return _empty()

        key = self._cache_key(artist, title, album)

        if key in self._mem:
            self._mem.move_to_end(key)
            return self._mem[key]

        cached = await self._read_disk(key)
        if cached is not None:
            self._store_mem(key, cached)
            return cached

        # Serialise + throttle network calls; a peer may have populated the mem
        # cache (via _store_mem on write) while we waited on the lock.
        async with self._lock:
            if key in self._mem:
                self._mem.move_to_end(key)
                return self._mem[key]
            result = await self._lookup(artist, title, album, duration_ms)
            if result is None:
                # Transient LRCLIB failure — cache nothing and let the caller
                # tell it apart from a genuine "no match" (LRCLIB answered),
                # which IS cached as a negative.
                raise LyricsUnavailable(f"{artist} - {title}")
            self._store_mem(key, result)
            await self._write_disk(key, result)
            return result

    async def _lookup(
        self, artist: str, title: str, album: Optional[str], duration_ms: Optional[int]
    ) -> Optional[Dict[str, Any]]:
        """Resolve a track via LRCLIB. Returns the normalized result on success
        (found may be False for a genuine no-match), or None on a network error
        (signals the caller not to cache the failure)."""
        params = {"artist_name": artist, "track_name": title}
        if album:
            params["album_name"] = album
        if duration_ms:
            params["duration"] = str(round(duration_ms / 1000))
        try:
            timeout = aiohttp.ClientTimeout(total=_HTTP_TIMEOUT)
            headers = {"User-Agent": _USER_AGENT}
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                await self._throttle()
                record = await self._get(session, params)
                if record is None:
                    # Exact match missed (wrong/absent duration, tag noise) — retry
                    # with a fuzzy search on the normalized artist/title.
                    await self._throttle()
                    record = await self._search(session, artist, title)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning("Lyrics lookup failed for %s - %s: %s", artist, title, e)
            return None
        return _from_record(record)

    async def _get(
        self, session: aiohttp.ClientSession, params: Dict[str, str]
    ) -> Optional[Dict[str, Any]]:
        async with session.get(f"{_LRCLIB_BASE}/get", params=params) as resp:
            if resp.status == 404:
                return None
            if resp.status != 200:
                logger.info("LRCLIB /get HTTP %s", resp.status)
                return None
            return await resp.json(content_type=None)

    async def _search(
        self, session: aiohttp.ClientSession, artist: str, title: str
    ) -> Optional[Dict[str, Any]]:
        params = {"track_name": _clean(title), "artist_name": _clean(artist)}
        async with session.get(f"{_LRCLIB_BASE}/search", params=params) as resp:
            if resp.status != 200:
                return None
            results = await resp.json(content_type=None)
        if not isinstance(results, list):
            return None
        # Prefer a synced hit; else the first with any lyrics.
        for r in results:
            if r.get("syncedLyrics"):
                return r
        for r in results:
            if r.get("plainLyrics"):
                return r
        return None

    @staticmethod
    def _cache_key(artist: str, title: str, album: Optional[str]) -> str:
        norm = f"{_clean(artist).lower()}|{_clean(title).lower()}|{(album or '').strip().lower()}"
        return hashlib.sha1(norm.encode("utf-8")).hexdigest()

    def _cache_file(self, key: str) -> Path:
        return self.CACHE_DIR / f"{key}.json"

    def _store_mem(self, key: str, result: Dict[str, Any]) -> None:
        self._mem[key] = result
        self._mem.move_to_end(key)
        while len(self._mem) > _MEM_CACHE_MAX:
            self._mem.popitem(last=False)

    async def _read_disk(self, key: str) -> Optional[Dict[str, Any]]:
        path = self._cache_file(key)
        try:
            if not path.is_file():
                return None
            async with aiofiles.open(path, "r", encoding="utf-8") as f:
                return json.loads(await f.read())
        except (OSError, ValueError) as e:
            logger.warning("Lyrics cache read failed (%s): %s", key, e)
            return None

    async def _write_disk(self, key: str, result: Dict[str, Any]) -> None:
        path = self._cache_file(key)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        try:
            async with aiofiles.open(tmp, "w", encoding="utf-8") as f:
                await f.write(json.dumps(result, ensure_ascii=False))
            os.replace(tmp, path)
        except OSError as e:
            logger.warning("Lyrics cache write failed (%s): %s", key, e)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    async def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        self._last_call = time.monotonic()
