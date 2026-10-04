# backend/sources/spotify/catalog.py
"""
What go-librespot's library answers, shaped for the browser. Pure functions.

The home sections never read a playlist's name: go-librespot names Spotify's
own playlists in the language of the session that signed it in ("Leaves Radio"
one day, "Radio Leaves" the next, measured 2026-10-03). They read what does
not move with the language — the owner, the cover's image path, and the
playlist id's prefix.

A playlist nobody gave a picture has no image in the library at all: the
Spotify apps draw it as a mosaic of its first four albums, which mosaic.scdn.co
serves from the four cover ids (measured 2026-10-04), so Milō draws the same.
"""
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote

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
MOSAIC_URL = "https://mosaic.scdn.co/300/{}"
MOSAIC_TILES = 4


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


def leading_covers(entries: List[Dict[str, Any]], complete: bool = False) -> List[str]:
    """The first distinct album covers of a listing, in its order, up to the
    mosaic's four. While the listing is read, stops at the first entry not
    described yet: a cover further down would take a place that is not its
    own. Once it is `complete`, such an entry is a track Spotify no longer
    describes (old playlists hold several, measured) and is skipped."""
    covers: List[str] = []
    for entry in entries:
        track = entry.get("track")
        if not track:
            if complete:
                continue
            break
        cover = track.get("album_cover_url")
        if cover and _ALBUM_COVER.match(cover) and cover not in covers:
            covers.append(cover)
            if len(covers) == MOSAIC_TILES:
                break
    return covers


def playlist_cover(covers: List[str]) -> Optional[str]:
    """Four albums make a mosaic; fewer, the first one's cover, as Spotify does."""
    if len(covers) >= MOSAIC_TILES:
        return MOSAIC_URL.format("".join(cover.rsplit("/", 1)[1] for cover in covers[:MOSAIC_TILES]))
    return covers[0] if covers else None


def normalize_playlist(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "uri": item["uri"],
        "name": item.get("name") or None,
        "description": item.get("description") or None,
        "owner": item.get("owner_username") or None,
        "image": item.get("image_url") or f"/api/spotify/contexts/{quote(item['uri'], safe='')}/cover",
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


def _has_tracks(item: Dict[str, Any]) -> bool:
    """Whether a raw /library/playlists entry holds anything to play. Its
    `length` is the truth for a playlist a person keeps, but not for one Spotify
    generates: a live mix lists 0 and holds 50 (measured 2026-10-04), so such a
    playlist is empty only once it has lost its name and its picture too — an
    expired mix, whose listing never completes."""
    if item.get("length"):
        return True
    if item.get("owner_username") == SPOTIFY_OWNER:
        return bool(item.get("name") or item.get("image_url"))
    return False


def classify_home(items: List[Dict[str, Any]], account: Optional[str]) -> Dict[str, List[Dict[str, Any]]]:
    """The home sections, each in library order. `items` are raw
    /library/playlists entries; every one holding tracks lands in exactly one
    section, and the first few in the shortcuts too, as the Spotify app's home
    does. An empty playlist is left out: there is nothing to play in it."""
    playlists = [normalize_playlist(item) for item in items if _has_tracks(item)]
    sections: Dict[str, List[Dict[str, Any]]] = {
        "shortcuts": playlists[:SHORTCUTS],
        "made_for_you": [], "radios": [], "mine": [], "followed": [],
    }
    for playlist in playlists:
        sections[_section_of(playlist, account)].append(playlist)
    return sections
