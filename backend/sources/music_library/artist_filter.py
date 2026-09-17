# backend/sources/music_library/artist_filter.py
"""Which rows in the catalog are a person, and which are a credit.

"Various Artists" is not an artist. It is what a compilation writes in the
album-artist field when no one performer owns the record, and the Artists tab
listed it as a browsable name with 16 albums behind it — next to the spellings
this library's own tags carry: "Various artistes", "VA" and "[Unknown Artist]".

The rule is on the name, because nothing else separates them. Measured on this
unit: "Various Artists" carries exactly the same Subsonic ``roles`` as 422 real
artists — ``albumartist, artist, maincredit`` — since the tracks are literally
tagged with it, so neither the role set nor the album count tells it apart, and
the compilation flag sits on albums rather than on the credit. Navidrome hits
the same wall and answers it the same way: one hardcoded name, one fixed id.

That id is carried here too, beside the names: it is a constant in the Navidrome
binary, so it still catches the row if a release renames or localises it, and
the names still catch it if upstream ever mints the id differently. Each half
survives the other being changed by accident.

A denylist is never complete. This is the set this library exposes, and a
placeholder in another language ("Verschiedene Interpreten", "Varios Artistas")
needs a line added here. That is the cost of a rule with no structural signal
underneath it — and it is cheaper than the obvious shortcut, a substring match
on "various", which would swallow Various Cruelties, a real band.

Nothing here hides an *album*: the Albums tab lists a record independently of
who is credited on it, so a compilation stays exactly as reachable as before.
The album page already refuses to link the name, by a structural rule of its own
that reads the tracks (AlbumView.vue::isVariousArtists).
"""
from typing import Any, Dict, List

from backend.sources.music_library.artist_images import normalize_name

# Navidrome's own placeholder, hardcoded in its binary rather than minted per
# library — which is why it can be matched exactly.
VARIOUS_ARTISTS_ID = "63sqASlAfjbGMuLP4JhnZU"

# Compared through normalize_name: accents folded, case folded, inner whitespace
# collapsed. Each entry was read off this unit's catalog, brackets included —
# "[Unknown Artist]" is the literal string iTunes writes.
_PLACEHOLDER_NAMES = frozenset({
    "various artists",
    "various artistes",
    "various interprets",
    "va",
    "unknown",
    "unknown artist",
    "[unknown artist]",
})


def is_placeholder_artist(artist: Dict[str, Any]) -> bool:
    """True when a catalog row is a credit rather than a person."""
    if artist.get("id") == VARIOUS_ARTISTS_ID:
        return True
    return normalize_name(artist.get("name") or "") in _PLACEHOLDER_NAMES


def drop_placeholder_artists(artists: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The rows worth offering to browse — every artist that is a person."""
    return [artist for artist in artists if not is_placeholder_artist(artist)]
