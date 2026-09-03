# backend/shared/artwork_resolver.py
"""Cover-art resolution from text metadata, via the iTunes API.

For the sources whose feed carries artist/title text but no image: radio's
in-band ICY metadata, DLNA controllers that publish no albumArtURI, and
Bluetooth AVRCP — whose 1.6 cover-art feature rides an OBEX BIP channel BlueZ
gives no client for, and which senders do not offer anyway (measured on an
iPhone: the `Track` dict carries Title, TrackNumber, NumberOfTracks, Duration,
Album and Artist, and the device advertises no BIP service at all). The resolved
URL lands in the same field a source with real artwork would fill, so the player
looks the same either way.

iTunes was chosen over MusicBrainz/Cover Art Archive after live measurement: on
the deep-catalogue vinyl/jazz/ambient the in-band stations play, CAA indexed
almost none of the tracks while iTunes matched them correctly. It is also the
same art source Shazam draws from, which keeps radio's two paths consistent.
There is one provider and there should stay one — a second catalogue was very
nearly added here to work around a query of ours, see below.

**The artist is the index, not the search box.** `/search` is a fuzzy full-text
ranker and it does not reliably surface an exact artist+album pair: measured on
the unit, `Jeune Mort NO COLORS` answers Dadju, Sopico and So La Lune, while
`/lookup` on that artist's own id lists `NO COLORS` outright. Same for
`Memoria`, same for `Le réveil`. So this module asks `/search` exactly one
question — *which artist is this?* — and everything after that is `/lookup`,
which is exact. Measured over 13 tracks including the awkward ones: 12 resolved
against 11 for the term search, and every track the term search found the
catalogue found too, so nothing was traded for it.

Two consequences worth knowing. It costs **fewer** requests, not more: the
artist id and that artist's catalogue are cached per artist, so an album played
track by track pays two or three requests once instead of one or two per track.
And the plausibility gate below now runs *inside one artist's catalogue*, where
the artist is already certain — so it only has to judge the name, which is what
lets `Wild Wild West` match `Wild Wild West (feat. Dru Hill & Kool Moe Dee)`.

Fully async (aiohttp). Results are cached per query, misses included, and calls
are serialised + lightly throttled. A lookup that could not be *performed* is
not a miss and is never cached — see `_LookupUnavailable`.
"""
import asyncio
import logging
import re
import time
import unicodedata
from collections import OrderedDict
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)

_ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
_ITUNES_LOOKUP_URL = "https://itunes.apple.com/lookup"
_HTTP_TIMEOUT = 8
_ITUNES_MIN_INTERVAL = 0.5  # polite spacing between calls (serialised by lock)
_CACHE_MAX = 500
# Artists remembered, with one catalogue each. Bounded for the same reason
# _CACHE_MAX is: this resolver serves a radio station that never stops, and a
# unit that never reboots. Each entry is a list of (name, artwork URL) pairs
# rather than the result dicts iTunes sent, which is the whole of what is read
# from them -- a full lookup is ~200 dicts of thirty-odd fields.
_ARTISTS_MAX = 50
# How much of an artist's catalogue is read in one lookup. iTunes caps this at
# 200 and there is no way past it: `offset` is accepted and ignored — measured,
# offsets 0/200/400/600 answer the identical 200 rows, 0 new. So a prolific
# artist loses their deep cuts, and the album step — tried first whenever an
# album is known — is what covers it.
#
# **Which leaves radio's in-band feed uncovered, and that is a deliberate
# trade, not an oversight.** In-band carries no album, so the ceiling bites
# there hardest: measured over eight deep-catalogue jazz tracks off the station
# this was tested on, the catalogue resolves 5, and adding the old term search
# back as a last resort would resolve 7 (`Art Farmer — Prelude In "A" Minor`
# and `Duke Ellington — Rockin' In Rhythm`, both artists past 200 tracks).
#
# It was offered and declined by the owner, on the ground the numbers do not
# capture: the term search cannot verify the artist against a catalogue row, so
# it can only be guarded by matching the artist *text* two ways round — and a
# cover that is confidently wrong is worse than a slot that is honestly empty.
# One mechanism, exact, is the choice. Do not re-add the fallback without
# re-opening that decision; the measurement above is the whole of what a fresh
# one would find.
_CATALOGUE_LIMIT = "200"

# The size _upscale asks iTunes for, and therefore the width of every URL this
# module returns. Declared rather than inlined because a consumer has to be able
# to say how wide a resolved cover is: DLNA publishes it as album_art_width, and
# the frontend's untrusted-sender gate judges a cover of unstated size as if it
# had none -- which would make a resolved cover invisible.
RESOLVED_ARTWORK_PX = 600

# Fraction of query tokens that must appear in the matched field for it to count
# as the same name. 0.6 tolerates a featuring credit the catalogue keeps in the
# title and one missing word in a long one, while still requiring a 2-token
# field to match fully — so "Buddy Greco" ≠ "Buddy Holly" (shared first name
# only = 0.5) is rejected.
_MATCH_RATIO = 0.6

# Parenthetical annotations dropped from the *query* only (display keeps them).
_PARENS_RE = re.compile(r"\s*\([^)]*\)")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

# The two halves of an artist's catalogue, in the order they are tried when an
# album is known. An album has one cover; a track can appear on a dozen
# compilations with a dozen different ones, so the album is both more accurate
# and more stable across the tracks of one record. Sources that know no album
# (radio's in-band ICY) go straight to the songs. Each pair is the `entity` the
# lookup asks for and the field a hit is judged on — iTunes names the matched
# field differently for a record and for a track.
_ALBUM_ENTITY = ("album", "collectionName")
_SONG_ENTITY = ("song", "trackName")


def _fold(text: str) -> str:
    """Strip accents, so a sender that drops them and a catalogue that keeps
    them are the same word.

    `_NON_ALNUM_RE` is not accent-aware, and it does not merely fail to match:
    it *cuts*. Measured — `réveil` became `r` + `veil`, `Björk` became `bj` +
    `rk`, `Édith Piaf` became `dith` + `piaf` — so a ratio meant to be scored
    on words was scored on debris. AVRCP senders make the asymmetry routine:
    one was measured here publishing `Roce` where the catalogue has `Rocé`,
    which tokenised to `roce` against `roc` and could never match.
    """
    return "".join(
        c for c in unicodedata.normalize("NFKD", text or "")
        if not unicodedata.combining(c)
    )


def _tokens(text: str) -> List[str]:
    """Lowercase alphanumeric tokens of a string (for loose matching)."""
    return [t for t in _NON_ALNUM_RE.sub(" ", _fold(text).lower()).split() if t]


def _same_name(a: str, b: str) -> bool:
    """Whether two names are the same name, accents and punctuation aside."""
    return bool(_tokens(a)) and _tokens(a) == _tokens(b)


def _artist_candidates(artist: str) -> List[str]:
    """The names to look an artist up under, best first.

    The **whole** string first, because a comma is not always a credit
    separator: `Tyler, The Creator` is one artist, and splitting on the comma
    finds a different one — measured, it resolves an id whose catalogue holds
    none of the tracks asked for. Only if the whole string names nobody is the
    lead credit tried, which is what a joint publication like `Jeune Mort, ISHA`
    needs.
    """
    whole = (artist or "").strip()
    lead = whole.split(",")[0].strip()
    return [whole] if lead == whole else [whole, lead]


def _entries(data: Dict[str, Any], result_key: str) -> List[tuple]:
    """(name, artwork URL) for each record in a result set.

    The whole of what is read from an iTunes row, kept instead of the row: a
    catalogue is up to 200 dicts of thirty-odd fields and it is held for the
    life of the process. The artist row a `/lookup` answers with first is
    dropped here — it names no record in either matched field, and left in it
    makes the catalogue's own length lie about how much came back.
    """
    return [
        (res.get(result_key) or "",
         res.get("artworkUrl100") or res.get("artworkUrl60") or "")
        for res in data.get("results", [])
        if res.get("wrapperType") != "artist"
    ]


class _LookupUnavailable(Exception):
    """iTunes could not be asked — which is not "it has nothing for this track".

    The two used to be one return value, and the difference is the whole life of
    a cover: a miss is remembered for the life of the process (deliberately, so
    an unmatched title is not re-queried on every poll), so a blip recorded as a
    miss leaves that track coverless until the backend restarts.
    """


class ArtworkResolver:
    """Resolve a cover-art URL from artist/title (and album when known)."""

    def __init__(self, settings_service=None) -> None:
        # (artist|title|album) → URL or None (misses cached to avoid re-querying;
        # a lookup that could not run is not a miss, and is not cached).
        self._cache: "OrderedDict[str, Optional[str]]" = OrderedDict()
        # An artist is asked about once: their id, then each half of their
        # catalogue. Playing a record track by track is the normal case here,
        # and it is what makes this cheaper than a query per track.
        self._artist_ids: "OrderedDict[str, Optional[int]]" = OrderedDict()
        self._catalogues: "OrderedDict[tuple, List[tuple]]" = OrderedDict()
        self._lock = asyncio.Lock()
        self._last_call = 0.0
        self._settings_service = settings_service

    async def _storefront(self) -> Optional[str]:
        """The iTunes store to search, or None for the API's own default (US).

        iTunes indexes per store, and a record distributed in one country is
        simply absent from another's — the API says so with `resultCount: 0`,
        which is indistinguishable from "no such artist". Measured on the unit:
        `Koma — Un parmi des millions` answers 0 on the default store and 1 on
        FR, deterministically, at any limit, and it survives every other fix in
        this module. Which is the failure mode this appliance meets most, since
        the catalogue this resolver was chosen for is exactly the deep,
        locally-distributed kind.

        The appliance already declares where it is — `wifi.country`, validated
        as two uppercase letters, i.e. the ISO 3166-1 alpha-2 code this
        parameter takes — so there is nothing to ask and nothing new to store.
        Unset it is the empty string the validator guarantees, and with no
        settings service (tests) it is None; the request carries the parameter
        only when there is one, so both leave the API its own default.
        """
        if not self._settings_service:
            return None
        return await self._settings_service.get_setting("wifi.country")

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
            try:
                url = await self._lookup(artist, title, album)
            except _LookupUnavailable:
                # Nothing was learned about this track, so nothing is recorded
                # about it: the next track change asks again.
                return None
            self._remember(self._cache, key, url, _CACHE_MAX)
            return url

    @staticmethod
    def _remember(store: "OrderedDict", key: Any, value: Any, cap: int) -> None:
        """Put a value in a bounded LRU, evicting the least recently used."""
        store[key] = value
        store.move_to_end(key)
        while len(store) > cap:
            store.popitem(last=False)

    async def _lookup(self, artist: str, title: str, album: str) -> Optional[str]:
        """Find the artist, then look for the record in their own catalogue.

        Unless there is no artist to find. Half of radio's in-band feed announces
        a bare title — `_parse_inband_track` splits an artist off only on
        `Artist - Title` or an unambiguous `Title by Artist`, and leaves it empty
        otherwise, deliberately, because a wrong artist is worse than none. With
        nothing to index by there is no catalogue to read, and the term search is
        the only tool left: it is not a fallback for the path below, it is the
        answer to a different question, and it is what that feed has always used.
        """
        q_title = self._clean_query_field(title)
        if not (artist or "").strip():
            return await self._by_title(q_title) if q_title else None

        artist_id = await self._artist_id(artist)
        if artist_id is None:
            return None

        if album:
            url = await self._from_catalogue(
                artist_id, _ALBUM_ENTITY, self._clean_query_field(album)
            )
            if url:
                return url

        if not q_title:
            return None
        return await self._from_catalogue(artist_id, _SONG_ENTITY, q_title)

    async def _by_title(self, q_title: str) -> Optional[str]:
        """The artist-less path: one term search, judged on the track name."""
        data = await self._request(
            _ITUNES_SEARCH_URL, {"term": q_title, "media": "music", "limit": "5"}
        )
        url = self._pick_artwork(_entries(data, "trackName"), q_title)
        if url:
            logger.info(f"Cover found for {q_title!r} (title alone)")
        return url

    async def _artist_id(self, artist: str) -> Optional[int]:
        """The one fuzzy question this module asks, and the only `/search` call.

        A name is accepted only when it comes back *identical* (accents and
        punctuation folded). Nothing looser: everything downstream trusts the
        artist completely, so a near-miss here would hand a whole catalogue to
        the wrong person — which is the failure `music_library/artist_images.py`
        was written to undo for artist photos.

        Cached per artist string, misses included: an artist iTunes does not
        carry must not be re-asked on every track of their record.
        """
        key = (artist or "").strip().lower()
        if key in self._artist_ids:
            self._artist_ids.move_to_end(key)
            return self._artist_ids[key]

        found: Optional[int] = None
        for candidate in _artist_candidates(artist):
            if not candidate:
                continue
            data = await self._request(
                _ITUNES_SEARCH_URL,
                {"term": candidate, "entity": "musicArtist", "limit": "5"},
            )
            for res in data.get("results", []):
                if _same_name(candidate, res.get("artistName", "")):
                    found = res.get("artistId")
                    break
            if found is not None:
                logger.info(f"Artist {candidate!r} is iTunes {found}")
                break

        self._remember(self._artist_ids, key, found, _ARTISTS_MAX)
        return found

    async def _from_catalogue(
        self, artist_id: int, entity: tuple, name: str
    ) -> Optional[str]:
        """Look one half of an artist's catalogue up and judge the names in it."""
        entity_name, result_key = entity
        cache_key = (artist_id, entity_name)
        if cache_key not in self._catalogues:
            data = await self._request(
                _ITUNES_LOOKUP_URL,
                {
                    "id": str(artist_id),
                    "entity": entity_name,
                    "limit": _CATALOGUE_LIMIT,
                },
            )
            self._remember(
                self._catalogues, cache_key, _entries(data, result_key), _ARTISTS_MAX
            )
        else:
            self._catalogues.move_to_end(cache_key)

        url = self._pick_artwork(self._catalogues[cache_key], name)
        if url:
            logger.info(f"Cover found for {name!r} ({entity_name})")
        return url

    async def _request(self, url: str, params: Dict[str, str]) -> Dict[str, Any]:
        """One iTunes call, throttled and bounded.

        Raises `_LookupUnavailable` when the question could not be put at all.
        That is not an answer about the track and the caller must not record one.
        """
        params = dict(params)
        try:
            # Inside the guard: everything this method cannot answer with is
            # reported the one way, so a settings read that ever raises does not
            # escape as something the caller has no arm for.
            storefront = await self._storefront()
            if storefront:
                params["country"] = storefront
            timeout = aiohttp.ClientTimeout(total=_HTTP_TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                await self._throttle()
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        logger.info(f"iTunes {url} -> HTTP {resp.status}")
                        raise _LookupUnavailable(url)
                    # iTunes returns text/javascript; parse leniently.
                    data = await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            logger.info(f"Artwork lookup failed ({params}): {e}")
            raise _LookupUnavailable(url) from e

        if not isinstance(data, dict):
            # Not iTunes answering: a captive portal, a proxy. Reported as
            # unavailable rather than as an empty catalogue, for the same reason
            # a 500 is.
            logger.info(f"iTunes answered {data!r}, not a result set")
            raise _LookupUnavailable(url)
        return data

    async def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call
        if elapsed < _ITUNES_MIN_INTERVAL:
            await asyncio.sleep(_ITUNES_MIN_INTERVAL - elapsed)
        self._last_call = time.monotonic()

    @classmethod
    def _pick_artwork(cls, entries: List[tuple], q_name: str) -> Optional[str]:
        """First plausible entry's cover URL, upscaled. None if none is.

        An exact name wins over a merely plausible one, whichever comes first in
        the catalogue: a record's own title and its deluxe reissue both pass the
        gate, and the plain one is the answer.
        """
        plausible = None
        for candidate, art in entries:
            if not art or not cls._is_plausible(q_name, candidate):
                continue
            if _same_name(q_name, candidate):
                return cls._upscale(art)
            if plausible is None:
                plausible = cls._upscale(art)
        return plausible

    @staticmethod
    def _is_plausible(q_name: str, candidate: str) -> bool:
        """Whether a catalogue entry is the record that was asked for.

        Only the name is judged, because the artist is no longer in question:
        this runs over one artist's own catalogue, reached through an id whose
        name matched exactly. That is what lets the rule be this loose without
        risking a wrong cover — the whole of the old two-sided artist test, and
        the credit-list asymmetry it existed to absorb, is answered upstream by
        `_artist_id` instead.

        Loose in one direction only, query into candidate: a catalogue routinely
        keeps a featuring credit in the title the sender leaves out
        (`Wild Wild West` against `Wild Wild West (feat. Dru Hill & Kool Moe
        Dee)`, `Un parmi des millions` against `… (feat. Rocé & Kohndo)`), and
        never the reverse.
        """
        q = _tokens(q_name)
        if not q:
            return False
        c = set(_tokens(candidate))
        return sum(1 for t in q if t in c) / len(q) >= _MATCH_RATIO

    @staticmethod
    def _upscale(url: str) -> str:
        """Upgrade the default 100×100 iTunes thumbnail to RESOLVED_ARTWORK_PX."""
        big = f"{RESOLVED_ARTWORK_PX}x{RESOLVED_ARTWORK_PX}"
        return url.replace("100x100bb", f"{big}bb").replace("100x100", big)
