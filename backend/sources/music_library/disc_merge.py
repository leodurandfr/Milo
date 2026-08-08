# backend/sources/music_library/disc_merge.py
"""Collapse a multi-disc release that Navidrome split into several albums.

Navidrome groups tracks into an album by the ``album`` tag (PID config
``musicbrainz_albumid|album`` — see install/navidrome.sh). A correctly-tagged
multi-disc set (one shared ALBUM tag + per-track DISCNUMBER) already collapses
into a single album, and the frontend renders the discs. The problem case is a
rip whose *disc marker is baked into the album title* with no DISCNUMBER — e.g.
``At Carnegie Hall CD 1`` / ``At Carnegie Hall CD 2`` — which Navidrome sees as
two distinct titles, hence two albums.

Detection runs on the tag values Navidrome exposes (title + album-artist + year);
the real folder layout is invisible through the API (``song.path`` is synthesized
from tags, not the filesystem), so there is no folder signal to lean on. Two
tiers, deliberately asymmetric in how much they trust the title:

- **marker** — the title ends in an explicit disc word: ``CD``/``Disc``/
  ``Disque``/``Disk`` + a number. Safe: the word *is* the disc signal, so a
  shared base title + same album-artist is enough to merge.
- **number** — the title ends in a bare number (``Rhapsody 1`` / ``Rhapsody 2``).
  Ambiguous (soundtracks, yearly comps, series all end in a number), so it merges
  only under strict corroboration: same album-artist *id*, same year, and a
  complete ``1..N`` run with no gaps or duplicates.

The merge is presentation-only — no files or tags are touched. A merged album
carries a synthetic id (:func:`build_merged_id`) that encodes its member album
ids, so :func:`expand_merged_album` can rebuild the combined tracklist on demand
without any server-side state. Member songs keep their real ids, so playback and
cover art are unaffected.
"""
import re
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

# Synthetic album-id prefix + member separator. Subsonic ids are base62/UUID and
# never contain "~", so joining on it round-trips cleanly. The whole id is one
# URL path segment (no slash), so it rides /api/music-library/album/{id} as-is.
MERGED_ID_PREFIX = "mdisc:"
_MEMBER_SEP = "~"

# The disc word for the marker tier (English + the French forms a NAS commonly
# carries). Word-boundary anchored so it never fires mid-word.
_DISC_WORD = r"(?:cd|disc|disque|disk)"
# A base that is *only* a disc word ("CD 1" → base "CD") is degenerate — the title
# carries no real album name — so it's left intact rather than collapsing unrelated
# discs into a "CD"-named blob.
_LONE_DISC_WORDS = frozenset({"cd", "disc", "disque", "disk"})

# Marker tier: "<base> [(/[] CD|Disc|… N )]". Optional bracket/separator run, the
# disc word, an optional 0-pad, 1–3 digits, an optional closing bracket.
_MARKER_RE = re.compile(
    rf"^(?P<base>.+?)[\s\-–—_·,]*[\(\[\{{]?\s*{_DISC_WORD}\s*[.:#]?\s*"
    rf"0*(?P<n>\d{{1,3}})\s*[\)\]\}}]?\s*$",
    re.IGNORECASE,
)
# Number tier: "<base> <sep> N" — a bare trailing number after a real separator
# (so "Album2" without a break does NOT match; a genuine disc is set off from the
# title). 1–3 digits only, so 4-digit years ("Blade Runner 2049") never match.
_NUMBER_RE = re.compile(
    r"^(?P<base>.+?)[\s\-–—_·,]+[\(\[\{]?\s*0*(?P<n>\d{1,3})\s*[\)\]\}]?\s*$"
)

# Trailing separators/brackets left dangling on a base after the suffix is peeled
# ("At Carnegie Hall (" → "At Carnegie Hall").
_TRAILING_JUNK_RE = re.compile(r"[\s\-–—_·,(\[\{]+$")

# A bare trailing number after one of these words is an *enumeration*, not a disc:
# a work/volume/part number, not "disc N". They flip a number-tier match back to
# "no suffix" so e.g. "Symphony No. 1"/"No. 2" or "Hits Vol. 1"/"Vol. 2" are never
# collapsed (the marker tier, which requires the word CD/Disc, is unaffected).
_NUMBER_TIER_STOPWORDS = frozenset({
    "no", "n°", "nº", "vol", "volume", "part", "pt", "act",
    "chapter", "chap", "episode", "ep", "movement", "mvt", "book",
})
_LAST_WORD_RE = re.compile(r"([^\s]+)\s*$")


class DiscSuffix(NamedTuple):
    """Parsed trailing disc marker. ``disc`` is None when the title has none."""

    base: str          # title with the disc suffix peeled off (original case)
    disc: Optional[int]
    tier: Optional[str]  # "marker" | "number" | None


def parse_disc_suffix(title: str) -> DiscSuffix:
    """Split a trailing disc marker off an album title.

    Tries the explicit-marker pattern first (safe), then the bare-number pattern.
    Returns ``DiscSuffix(title, None, None)`` when neither matches, and never
    returns an empty base (a title that is *only* a disc marker, e.g. "CD 1", is
    left intact so it can't collapse unrelated albums into one empty-named blob).
    """
    raw = (title or "").strip()
    for tier, pattern in (("marker", _MARKER_RE), ("number", _NUMBER_RE)):
        match = pattern.match(raw)
        if not match:
            continue
        base = _TRAILING_JUNK_RE.sub("", match.group("base")).strip()
        if not base or base.casefold() in _LONE_DISC_WORDS:
            return DiscSuffix(raw, None, None)
        if tier == "number" and _ends_with_stopword(base):
            continue  # "Vol 2" / "No. 5" — an enumeration, not a disc
        return DiscSuffix(base, int(match.group("n")), tier)
    return DiscSuffix(raw, None, None)


def _ends_with_stopword(base: str) -> bool:
    """True when ``base``'s last word is a number-tier enumeration stopword."""
    last = _LAST_WORD_RE.search(base)
    if not last:
        return False
    return last.group(1).strip(".").casefold() in _NUMBER_TIER_STOPWORDS


# === Synthetic id codec ===

def build_merged_id(member_ids: List[str]) -> str:
    """Synthetic album id encoding its member ids in disc order."""
    return MERGED_ID_PREFIX + _MEMBER_SEP.join(member_ids)


def is_merged_id(album_id: str) -> bool:
    """True if ``album_id`` names a synthetic merged album."""
    return album_id.startswith(MERGED_ID_PREFIX)


def parse_merged_id(album_id: str) -> List[str]:
    """Member album ids from a synthetic id (empty list if malformed)."""
    if not is_merged_id(album_id):
        return []
    body = album_id[len(MERGED_ID_PREFIX):]
    return [part for part in body.split(_MEMBER_SEP) if part]


# === Grouping ===

class _Member(NamedTuple):
    index: int                 # position in the input list (order preservation)
    album: Dict[str, Any]      # the raw Subsonic album dict
    base: str
    disc: int
    tier: str


def _artist_key(album: Dict[str, Any]) -> str:
    """Grouping key for an album's artist — id when present, else the name."""
    return album.get("artistId") or f"name:{(album.get('artist') or '').casefold()}"


def _mergeable(members: List[_Member]) -> bool:
    """Whether a same-(artist, base) group is a real multi-disc set.

    Marker-tier groups only need distinct disc numbers. Number-tier (or mixed)
    groups must additionally form a gapless ``1..N`` run and agree on album-artist
    id and year — the corroboration that keeps ``Rhapsody 1``/``2`` in but two
    unrelated ``… 1``/``… 2`` releases out.
    """
    discs = [m.disc for m in members]
    if len(set(discs)) != len(discs):  # duplicate disc numbers → not a clean set
        return False
    if all(m.tier == "marker" for m in members):
        return True
    if sorted(discs) != list(range(1, len(discs) + 1)):
        return False
    years = {m.album.get("year") for m in members}
    if len(years) != 1 or None in years:
        return False
    artist_ids = {m.album.get("artistId") for m in members}
    if len(artist_ids) != 1 or None in artist_ids:
        return False
    return True


def _sum_int(members: List[_Member], field: str) -> int:
    return sum(int(m.album.get(field) or 0) for m in members)


def _merge_genres(members: List[_Member]) -> List[Dict[str, Any]]:
    """Every disc's genres, de-duplicated, in first-seen order.

    A union rather than disc 1's list: the per-scope genre list is derived from
    this catalog (see source.genres_in_scope), and a genre carried only by
    disc 2 would otherwise vanish from it while its songs stay findable.
    """
    seen: Dict[str, Dict[str, Any]] = {}
    for member in members:
        for genre in member.album.get("genres") or []:
            name = genre.get("name")
            if name and name not in seen:
                seen[name] = genre
    return list(seen.values())


def _build_merged_album(members: List[_Member]) -> Dict[str, Any]:
    """One synthetic album-list entry from disc-ordered members.

    Inherits disc 1's identity fields (artist/art/year/genre); sums the track
    count and duration; keeps ``starred`` only when *every* disc is starred (the
    UI star reflects the whole set). ``_merged``/``_discCount`` are advisory hints
    the frontend may ignore.
    """
    ordered = sorted(members, key=lambda m: m.disc)
    disc1 = ordered[0].album
    merged = {
        "id": build_merged_id([m.album["id"] for m in ordered]),
        "name": ordered[0].base,
        "artist": disc1.get("artist"),
        "artistId": disc1.get("artistId"),
        "artists": disc1.get("artists"),
        "coverArt": disc1.get("coverArt") or disc1.get("id"),
        "songCount": _sum_int(ordered, "songCount"),
        "duration": _sum_int(ordered, "duration"),
        "year": disc1.get("year"),
        "genre": disc1.get("genre"),
        "genres": _merge_genres(ordered),
        "_merged": True,
        "_discCount": len(ordered),
    }
    if all(m.album.get("starred") for m in ordered):
        merged["starred"] = disc1.get("starred")
    return merged


def merge_albums(albums: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse multi-disc siblings in an album list, preserving order.

    Albums with no disc suffix, and suffixed albums that don't form a mergeable
    set, pass through untouched. A merged album takes the slot of its first
    member; the other members are dropped.
    """
    parsed = [(a, parse_disc_suffix(a.get("name", ""))) for a in albums]

    groups: Dict[Tuple[str, str], List[_Member]] = {}
    for index, (album, suffix) in enumerate(parsed):
        if suffix.disc is None or not album.get("id"):
            continue
        key = (_artist_key(album), suffix.base.casefold())
        groups.setdefault(key, []).append(
            _Member(index, album, suffix.base, suffix.disc, suffix.tier)
        )

    merged_at: Dict[int, Dict[str, Any]] = {}
    dropped: set = set()
    for members in groups.values():
        if len(members) < 2 or not _mergeable(members):
            continue
        first = min(m.index for m in members)
        merged_at[first] = _build_merged_album(members)
        dropped.update(m.index for m in members)
        dropped.discard(first)

    result: List[Dict[str, Any]] = []
    for index, (album, _) in enumerate(parsed):
        if index in merged_at:
            result.append(merged_at[index])
        elif index not in dropped:
            result.append(album)
    return result


async def expand_merged_album(
    get_album, synthetic_id: str
) -> Optional[Dict[str, Any]]:
    """Rebuild a merged album's detail (concatenated, disc-tagged tracklist).

    ``get_album`` is an async ``id -> album|None`` (the NavidromeClient method).
    Each member's songs are appended in disc order and stamped with a
    ``discNumber`` (from the title marker, else the member's ordinal) unless they
    already carry one — so the frontend's existing disc-separator rendering works.
    Falls back to whatever members resolve; returns None if none do.
    """
    members = []
    for member_id in parse_merged_id(synthetic_id):
        album = await get_album(member_id)
        if album and album.get("id"):
            suffix = parse_disc_suffix(album.get("name", ""))
            members.append((album, suffix))
    if not members:
        return None

    # Disc order: by parsed marker when available, else input order.
    members.sort(key=lambda pair: pair[1].disc if pair[1].disc is not None else 1_000)

    songs: List[Dict[str, Any]] = []
    for ordinal, (album, suffix) in enumerate(members, start=1):
        disc_no = suffix.disc if suffix.disc is not None else ordinal
        for song in album.get("song", []) or []:
            song = dict(song)
            if not song.get("discNumber"):
                song["discNumber"] = disc_no
            songs.append(song)

    ordered_members = [
        _Member(i, album, suffix.base, suffix.disc or (i + 1), suffix.tier or "number")
        for i, (album, suffix) in enumerate(members)
    ]
    merged = _build_merged_album(ordered_members)
    merged["song"] = songs
    merged["songCount"] = len(songs)
    merged["duration"] = sum(int(a.get("duration") or 0) for a, _ in members)
    return merged
