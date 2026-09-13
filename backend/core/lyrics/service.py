# backend/core/lyrics/service.py
"""Lyrics resolution via LRCLIB.

Transverse feature: keyed off the now-playing (artist, title, duration) of
whichever source is active, independent of the source itself. Fetches from
LRCLIB (lrclib.net — no API key, returns both synced LRC and plain lyrics),
caches results under /var/lib/milo/lyrics/, and returns a normalized shape the
frontend Lyrics app renders directly.

Fully async (aiohttp, per-lookup session like shared/artwork_resolver.py).

Three answers, and the whole point of this module is keeping them apart:

- **Lyrics found** — cached with no expiry. A track's lyrics do not change.
- **LRCLIB answered, no match** — cached for `_NEGATIVE_TTL` only. LRCLIB is
  crowd-sourced and *grows*: a track released this week gets its lyrics
  uploaded next month. Cached forever, the answer for every new release is
  frozen at the moment it first played, which is exactly when it is most
  likely to be missing.
- **LRCLIB did not answer** — `LyricsUnavailable`, cached nowhere, so the next
  open retries (the route maps it to a 200 + status=error, which the frontend
  also skips caching). *Any* status other than 200 or the `/get` 404 is this
  case: lrclib.net answers `503 ServerOverloaded` to a few percent of calls,
  and reading that as "this track has no lyrics" is what wrote permanent
  negatives to disk. A 5xx is retried once before it counts as an outage at all
  (see `_request`), which is what makes this answer rare rather than weekly.

The **album is deliberately not part of the query or the cache key**. LRCLIB
filters `album_name` by exact string match with no tolerance, against free text
its contributors typed — measured: its record for Moussa's "Laguna" stores the
album as `La nuit je r^ve` (0x5e, an ASCII caret, a mojibake upload), so the
correct string 404s forever and every lookup for it fell through to the fuzzy
`/search`. Duration is the discriminator instead, and LRCLIB matches it within
±2 s, which is the tolerance free text does not have.

The disk cache is a disposable derived cache — no schema_version / fail-loud
protocol. A file not carrying the expected keys reads as a miss, so a shape
change costs one refetch per track rather than a migration.
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
from typing import Any, Dict, List, Optional, Tuple

import aiofiles
import aiohttp

logger = logging.getLogger("core.lyrics")

_LRCLIB_BASE = "https://lrclib.net/api"
_HTTP_TIMEOUT = 8
_MIN_INTERVAL = 0.3  # polite spacing between LRCLIB calls (serialised by lock)
# How long a 5xx waits before its one retry. Long enough to clear the overload
# that caused it, short enough to stay inside the lookup the reader is waiting on.
_RETRY_DELAY = 0.8
_MEM_CACHE_MAX = 256
# How long a "this track has no lyrics" answer is trusted. A week is one query
# per lyricless track per week — nothing against a cache that otherwise answers
# from memory — for a catalogue that gains entries daily.
_NEGATIVE_TTL = 7 * 24 * 3600
# LRCLIB asks callers to identify themselves with a User-Agent.
_USER_AGENT = "Milo/1.0 (https://github.com/leodurandfr/milo)"

# What a cached record must carry to be served. Anything else on disk is a miss.
_CACHE_FIELDS = ("found", "synced", "plain", "checked_at")

# Parenthetical annotations + trailing "- …" suffixes dropped when building the
# match key and the search-fallback query (e.g. "(feat. X)", "- Remastered 2011").
_PARENS_RE = re.compile(r"\s*[\(\[][^)\]]*[\)\]]")
_SUFFIX_RE = re.compile(r"\s*-\s.*$")
# Featuring credits, which tags carry and LRCLIB's own records mostly do not:
# "Dr. Dre Feat. Mary J. Blige" → "Dr. Dre". Measured on that track, LRCLIB
# stores the artist as "Dr. Dre feat. Mary J. Blige & Rell", so the tagged string
# 404s on /get and drags every featuring track onto the fuzzy path — where it
# loses a second time, the search ranking a 304 s variant above the 330 s one the
# file actually is. On "Dr. Dre" both endpoints answer on the first call. `&` is
# deliberately left alone: "Earth, Wind & Fire" and "Eminem & Dr. Dre" are names,
# not credits, and LRCLIB stores them whole. Two bounds keep the strip honest:
# the lookbehind, because a credit always FOLLOWS something ("Ft. Worth Blues"
# and "Featuring the Sound" are whole names, and without it they cleaned to the
# empty string); and the dot required on bare "ft", because "ft" is also feet
# ("10 Ft Tall" -> "10"). A Fort abbreviated "Ft." is the known residual — rarer
# in a music library than the credit, and it costs a fuzzy search, not an error.
_FEAT_RE = re.compile(
    r"(?<=\S)\s+(?:\b(?:feat|featuring)\b\.?|\bft\.)\s+.*$", re.IGNORECASE
)
# What a credit introduced by " - " leaves behind once it is gone, in the one
# path that does not also run _SUFFIX_RE: "Forever - feat. Drake" -> "Forever -".
_DANGLING_SEP_RE = re.compile(r"\s*-\s*$")
# One LRC line = one or more [mm:ss.xx] stamps followed by the lyric text.
_LRC_LINE_RE = re.compile(r"((?:\[\d{1,2}:\d{2}(?:[.:]\d{1,3})?\])+)(.*)")
_LRC_TS_RE = re.compile(r"\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]")


class LyricsUnavailable(Exception):
    """LRCLIB could not be reached, or refused — distinct from "no lyrics".

    Raised so neither the disk cache nor the frontend's per-session cache stores
    the outage as a real negative; the next lookup retries.
    """


def _empty() -> Dict[str, Any]:
    return {"found": False, "synced": None, "plain": None}


def _payload(record: Dict[str, Any]) -> Dict[str, Any]:
    """The shape the route returns — the cache's own bookkeeping left behind."""
    return {"found": record["found"], "synced": record["synced"], "plain": record["plain"]}


def _is_fresh(record: Dict[str, Any]) -> bool:
    """Whether a cached record is still worth serving.

    Positives never expire; negatives do. See the module docstring.
    """
    if record["found"]:
        return True
    return (time.time() - record["checked_at"]) < _NEGATIVE_TTL


def _is_well_formed(record: Any) -> bool:
    """Whether a cached record can be served at all — types, not just presence.

    `_is_fresh` does arithmetic on `checked_at` and runs outside the read's
    try/except, so a string there would raise a TypeError out of the service and
    500 that one track's lyrics until somebody deleted the file by hand. That is
    the exact failure a disposable cache exists to not have.
    """
    return (
        isinstance(record, dict)
        and all(f in record for f in _CACHE_FIELDS)
        and isinstance(record["found"], bool)
        and isinstance(record["checked_at"], (int, float))
    )


def _clean(text: str) -> str:
    """Strip parentheticals, trailing suffixes and featuring credits for matching
    (display keeps them).

    Order matters: the suffix rule runs first, so "Forever - feat. Drake" loses
    the whole tail rather than leaving the dangling "Forever -" the credit rule
    alone would (it strips from the word, and `- ` needs something after it).

    Never returns the empty string. "(Untitled)" and "- Remastered" are titles
    made entirely of what this strips, and an empty query is a guaranteed miss
    cached as a week-long negative — the raw text at least has a chance.
    """
    original = (text or "").strip()
    text = _PARENS_RE.sub("", original)
    text = _SUFFIX_RE.sub("", text)
    text = _FEAT_RE.sub("", text)
    return text.strip() or original


def _strip_credit(text: str) -> str:
    """Drop a featuring credit and nothing else — what the exact match needs.

    `_clean` is the matching normalizer: it also drops parentheticals and
    trailing suffixes, which is right for the fuzzy search and the cache key and
    wrong here. LRCLIB's /get compares the title as an exact string, and
    "(Don't Fear) The Reaper", "Hurt (Live)" and "Lolo (Intro)" are how it
    stores them — cleaning those away turns a match into a guaranteed 404 and
    hands the track to the duration-blind search. The credit is the one piece
    both sides agree is noise: LRCLIB files it under the lead artist.
    """
    return _DANGLING_SEP_RE.sub("", _FEAT_RE.sub("", text or "")).strip()


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
        # sha1(artist|title|duration_s) → {found, synced, plain, checked_at}.
        # Misses cached too, but only until they expire.
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

        key = self._cache_key(artist, title, duration_ms)

        cached = self._from_mem(key)
        if cached is None:
            cached = await self._read_disk(key)
            if cached is not None:
                self._store_mem(key, cached)
        if cached is not None:
            return _payload(cached)

        # Serialise + throttle network calls; a peer may have populated the mem
        # cache (via _store_mem on write) while we waited on the lock.
        async with self._lock:
            cached = self._from_mem(key)
            if cached is not None:
                return _payload(cached)
            # A LyricsUnavailable from here propagates untouched: nothing is
            # stored, so the next open asks again.
            record = {**await self._lookup(artist, title, duration_ms), "checked_at": time.time()}
            self._store_mem(key, record)
            await self._write_disk(key, record)
            return _payload(record)

    async def _lookup(
        self, artist: str, title: str, duration_ms: Optional[int]
    ) -> Dict[str, Any]:
        """Resolve a track via LRCLIB: exact match first, fuzzy search on a miss.

        A "miss" is three things, not one: LRCLIB has no such track (404), it
        refused to answer (5xx, after `_request` spent the retry), or it answered
        with a record carrying no lyrics at all. The third is the one that reads
        like success and is not — see below.

        Returns the normalized result — found may be False, which is an answer
        and is cached. Raises LyricsUnavailable only if LRCLIB answered neither
        call, which is not an answer and is cached nowhere.
        """
        # The credit only, never the full `_clean` — see `_strip_credit`. Measured:
        # "Dr. Dre Feat. Mary J. Blige" 404s here where "Dr. Dre" answers, because
        # LRCLIB files the track under the lead artist. Duration stays the
        # discriminator the stripped credit pretended to be: /get matches it ±2 s.
        params = {"artist_name": _strip_credit(artist), "track_name": _strip_credit(title)}
        if duration_ms:
            params["duration"] = str(round(duration_ms / 1000))
        try:
            timeout = aiohttp.ClientTimeout(total=_HTTP_TIMEOUT)
            headers = {"User-Agent": _USER_AGENT}
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                try:
                    record = await self._get(session, params)
                except LyricsUnavailable as e:
                    # A refusal on the exact match is not fatal. The two are
                    # separate endpoints and a burst hits them independently —
                    # measured on this unit: /get answering 503 while /search
                    # answered 200 for the same track, in the same window. So a
                    # refusal here takes the same road as a 404 and lets the
                    # search decide; only both refusing means LRCLIB did not
                    # answer, and only then is nothing cached.
                    logger.info("LRCLIB exact match unavailable (%s) — searching", e)
                    record = None
                # Resolved here rather than at the return, because whether the
                # exact record actually carries lyrics is what decides if the
                # search still gets its turn.
                #
                # An exact record with nothing in it is not an answer. LRCLIB's
                # copy for a given (artist, title, duration) can be a stub, and
                # it can be flagged `instrumental` on a track that plainly is not
                # — measured: its exact record for Dr. Dre's "The Message" is
                # `instrumental: true` with both lyric fields null, while the
                # search has the synced lyrics for the same song. Treating that
                # as a result is a "no lyrics found" on a track LRCLIB has; so a
                # stub takes the same road as the 404 and the refusal.
                resolved = _from_record(record)
                if not resolved["found"]:
                    resolved = _from_record(await self._search(session, artist, title))
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning("Lyrics lookup failed for %s - %s: %s", artist, title, e)
            raise LyricsUnavailable(f"{artist} - {title}: {e}") from e
        return resolved

    async def _request(
        self, session: aiohttp.ClientSession, path: str, params: Dict[str, str]
    ) -> Tuple[int, Any]:
        """One LRCLIB call, retried once on a 5xx. Returns (status, body or None).

        lrclib.net does not fail in sustained outages — it refuses a few percent
        of calls at random with 503 ServerOverloaded. Measured on this unit: one
        call in twenty, and the immediate retry succeeded. A lookup spends two
        calls, so that was ~10 % of tracks reaching the reader as "no lyrics
        found" for a track LRCLIB had all along — 40 % of every empty Lyrics
        screen over a week. One retry takes it to ~0.25 %; a second would buy a
        thousandth of that and delay a real outage by another second. So: one.

        A 429 is deliberately NOT retried. That one is LRCLIB asking for less
        traffic, and answering it with a second call is the wrong reply; it
        propagates as the outage it is, cached nowhere, retried on the next open.

        The politeness spacing lives here rather than in `_lookup` so that it
        counts the retry too — it is an LRCLIB call like any other.
        """
        status = 0
        for attempt in (1, 2):
            if attempt == 2:
                await asyncio.sleep(_RETRY_DELAY)
            await self._throttle()
            async with session.get(f"{_LRCLIB_BASE}/{path}", params=params) as resp:
                if resp.status == 200:
                    return resp.status, await resp.json(content_type=None)
                status = resp.status
            if status < 500:
                return status, None
            logger.info("LRCLIB /%s answered HTTP %s — retrying once", path, status)
        return status, None

    async def _get(
        self, session: aiohttp.ClientSession, params: Dict[str, str]
    ) -> Optional[Dict[str, Any]]:
        """The exact match. None means LRCLIB has no such track — a 404 is the
        one non-200 status that is an answer rather than a failure."""
        status, body = await self._request(session, "get", params)
        if status == 404:
            return None
        if status != 200:
            raise LyricsUnavailable(f"LRCLIB /get answered HTTP {status}")
        return body

    async def _search(
        self, session: aiohttp.ClientSession, artist: str, title: str
    ) -> Optional[Dict[str, Any]]:
        """The fuzzy fallback. An empty list is a genuine "no lyrics"; anything
        that is not a list of results is LRCLIB failing to answer."""
        params = {"track_name": _clean(title), "artist_name": _clean(artist)}
        status, results = await self._request(session, "search", params)
        if status != 200:
            raise LyricsUnavailable(f"LRCLIB /search answered HTTP {status}")
        if not isinstance(results, list):
            raise LyricsUnavailable("LRCLIB /search answered a non-list body")
        # Prefer a synced hit; else the first with any lyrics.
        for r in results:
            if r.get("syncedLyrics"):
                return r
        for r in results:
            if r.get("plainLyrics"):
                return r
        return None

    @staticmethod
    def _cache_key(artist: str, title: str, duration_ms: Optional[int]) -> str:
        """Everything that varies the LRCLIB query, and nothing else.

        Duration is in the key for the same reason it is in the query: `_clean`
        strips "(Live at Wembley)" and "- Remastered 2011" from the title before
        matching, so on artist + title alone a live take and the studio one
        collapse onto one entry — and the first lookup's LRC, timed for the
        other recording, is then served for both with the cache answering before
        the duration ever reaches the network. Rounded to the second, exactly as
        sent, so the key and the query cannot disagree.
        """
        seconds = round(duration_ms / 1000) if duration_ms else ""
        norm = f"{_clean(artist).lower()}|{_clean(title).lower()}|{seconds}"
        return hashlib.sha1(norm.encode("utf-8")).hexdigest()

    def _cache_file(self, key: str) -> Path:
        return self.CACHE_DIR / f"{key}.json"

    def _from_mem(self, key: str) -> Optional[Dict[str, Any]]:
        """A live memory entry, or None — absent or expired."""
        record = self._mem.get(key)
        if record is None:
            return None
        if not _is_fresh(record):
            del self._mem[key]
            return None
        self._mem.move_to_end(key)
        return record

    def _store_mem(self, key: str, result: Dict[str, Any]) -> None:
        self._mem[key] = result
        self._mem.move_to_end(key)
        while len(self._mem) > _MEM_CACHE_MAX:
            self._mem.popitem(last=False)

    async def _read_disk(self, key: str) -> Optional[Dict[str, Any]]:
        """A live disk entry, or None — absent, unreadable, stale-shaped or
        expired. All four are a miss: the cache is disposable, so refetching is
        always the right answer and no migration is ever owed."""
        path = self._cache_file(key)
        try:
            if not path.is_file():
                return None
            async with aiofiles.open(path, "r", encoding="utf-8") as f:
                record = json.loads(await f.read())
        except (OSError, ValueError) as e:
            logger.warning("Lyrics cache read failed (%s): %s", key, e)
            return None
        if not _is_well_formed(record) or not _is_fresh(record):
            return None
        return record

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
