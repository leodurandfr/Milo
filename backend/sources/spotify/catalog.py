# backend/sources/spotify/catalog.py
"""
What go-librespot's library answers, shaped for the browser. Pure functions.

The home sections never read a playlist's name: go-librespot names Spotify's
own playlists in the language of the session that signed it in ("Leaves Radio"
one day, "Radio Leaves" the next, measured 2026-10-03). They read what does
not move with the language — the owner, the cover's image path, and the
playlist id's prefix.
"""
import re
from typing import Any, Dict, List, Optional

SPOTIFY_OWNER = "spotify"
# Spotify's editorial playlists ("Songs to Test Speakers With"): everyone gets
# the same one, so they sit with the followed ones, not "made for you".
EDITORIAL_PREFIX = "37i9dQZF1D"
# Spotify draws a radio's cover itself, under this path, whatever its name.
RADIO_IMAGE = "/img/radio/"
SHORTCUTS = 7     # the 8th tile is Liked Songs, added by the browser

# An album cover on i.scdn.co carries its size in the id: b273 (640 px),
# 1e02 (300 px), 4851 (64 px). The three are served for every cover (measured).
_ALBUM_COVER = re.compile(r"^(https://i\.scdn\.co/image/ab67616d0000)(b273|1e02|4851)(\w+)$")
THUMBNAIL_SIZE = "4851"


def thumbnail_url(url: Optional[str]) -> Optional[str]:
    """The 64 px version of an album cover, for a track row; any other image as
    it is."""
    if not url:
        return None
    match = _ALBUM_COVER.match(url)
    if match is None:
        return url
    return f"{match.group(1)}{THUMBNAIL_SIZE}{match.group(3)}"


def liked_songs_uri(account: str) -> str:
    return f"spotify:user:{account}:collection"


def normalize_playlist(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "uri": item["uri"],
        "name": item.get("name") or None,
        "description": item.get("description") or None,
        "owner": item.get("owner_username") or None,
        "image": item.get("image_url") or None,
        "editable": bool(item.get("can_edit") or item.get("collaborative")),
    }


def normalize_track(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One /context/tracks entry; None while its metadata is not cached yet."""
    track = entry.get("track")
    if not track:
        return None
    names = track.get("artist_names") or []
    uris = track.get("artist_uris") or []
    artwork = track.get("album_cover_url") or None
    return {
        "uri": track.get("uri") or entry.get("uri"),
        "title": track.get("name"),
        "artists": [
            {"name": name, "uri": uris[i] if i < len(uris) and uris[i] else None}
            for i, name in enumerate(names)
        ],
        "album": {"name": track.get("album_name") or None, "uri": track.get("album_uri") or None},
        "artwork": artwork,
        "thumbnail": thumbnail_url(artwork),
        "duration_ms": track.get("duration") or None,
        "track_number": track.get("track_number") or None,
        "disc_number": track.get("disc_number") or None,
        "release_date": track.get("release_date") or None,
    }


def _section_of(item: Dict[str, Any], account: Optional[str]) -> str:
    if item["owner"] == SPOTIFY_OWNER:
        if RADIO_IMAGE in (item["image"] or ""):
            return "radios"
        if item["uri"].rsplit(":", 1)[-1].startswith(EDITORIAL_PREFIX):
            return "followed"
        return "made_for_you"
    if item["owner"] == account or item["editable"]:
        return "mine"
    return "followed"


def classify_home(items: List[Dict[str, Any]], account: Optional[str]) -> Dict[str, List[Dict[str, Any]]]:
    """The home sections, each in library order. `items` are raw
    /library/playlists entries; every one lands in exactly one section, and the
    first few in the shortcuts too, as the Spotify app's home does."""
    playlists = [normalize_playlist(item) for item in items]
    sections: Dict[str, List[Dict[str, Any]]] = {
        "shortcuts": playlists[:SHORTCUTS],
        "made_for_you": [], "radios": [], "mine": [], "followed": [],
    }
    for playlist in playlists:
        sections[_section_of(playlist, account)].append(playlist)
    return sections
