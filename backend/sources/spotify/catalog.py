# backend/sources/spotify/catalog.py
"""
What go-librespot's library and Spotify's home answer, shaped for the
browser. Pure functions.

The home is Spotify's own, the one its apps draw (spclient homeview, measured
2026-10-04): its shelves in its order, titled in the language asked for, each
card a playlist, an album, an artist or Liked Songs. The account's own
playlists follow it, read from the library — the home lists only a few of
them, and the apps keep the rest under their library tab, which Milō has not.

A playlist nobody gave a picture has no image in the library at all: the
Spotify apps draw it as a mosaic of its first four albums, which mosaic.scdn.co
serves from the four cover ids (measured 2026-10-04), so Milō draws the same.
"""
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

SPOTIFY_OWNER = "spotify"
# The home's first shelf, drawn as the tiles above the others. Its id is the
# same in every language and for every account (measured).
SHORTCUTS_SECTION = "spotify:section:0JQ5DAIiKWzVFULQfUm85Y"
# What a home card opens, by its uri's kind. A show is left out: its episodes
# are not something go-librespot lists.
_CARD_KINDS = {"playlist": "playlist", "album": "album", "artist": "artist"}
# Liked Songs, as the home names it: the signed-in user is "@".
_HOME_LIKED_SONGS = re.compile(r"^spotify:user:[^:]+:collection$")

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


def described_tracks(entries: List[Dict[str, Any]], complete: bool) -> List[Dict[str, Any]]:
    """A listing's tracks as far as go-librespot has described them. It
    describes a listing front to back, 100 tracks a second (daemon/
    track_meta_cache.go, 0.10.3), so while it reads, the first entry not
    described yet ends what can be shown: a row slotted in above the rows on
    screen would push them down. Once `complete`, such an entry is a track
    Spotify no longer describes, and is left out."""
    tracks: List[Dict[str, Any]] = []
    for entry in entries:
        track = normalize_track(entry)
        if track is None:
            if complete:
                continue
            break
        tracks.append(track)
    return tracks


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


def library_sections(items: List[Dict[str, Any]], account: Optional[str]) -> Dict[str, List[Dict[str, Any]]]:
    """The account's playlists, in library order: the ones it owns or may edit,
    and every other it saved (Spotify's included). `items` are raw
    /library/playlists entries; an empty playlist is left out, there is nothing
    to play in it."""
    sections: Dict[str, List[Dict[str, Any]]] = {"mine": [], "followed": []}
    for item in items:
        if not _has_tracks(item):
            continue
        playlist = normalize_playlist(item)
        mine = playlist["owner"] == account or playlist["editable"]
        sections["mine" if mine else "followed"].append(playlist)
    return sections


def _home_card(card: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    uri = (card.get("target") or {}).get("uri") or ""
    if _HOME_LIKED_SONGS.match(uri):
        kind = "liked"
    else:
        kind = _CARD_KINDS.get(uri.split(":")[1] if uri.count(":") >= 2 else "")
    if kind is None:
        return None
    text = card.get("text") or {}
    image = ((card.get("images") or {}).get("main") or {}).get("uri")
    return {
        "uri": uri,
        "kind": kind,
        "name": text.get("title") or None,
        "subtitle": text.get("subtitle") or None,
        "image": image or None,
    }


def home_shelves(view: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Spotify's home as the shortcuts and the shelves after them, each in its
    order. The view is flat — a header, then its cards — and every card names
    its shelf. A card the browser cannot open is left out, and a shelf left
    with nothing (the account's podcasts) with it."""
    shelves: Dict[str, Dict[str, Any]] = {}
    for entry in view.get("body") or []:
        section = (entry.get("metadata") or {}).get("sectionId")
        if not section:
            continue
        if (entry.get("component") or {}).get("category") == "header":
            shelves[section] = {"id": section, "title": (entry.get("text") or {}).get("title") or None, "items": []}
            continue
        card = _home_card(entry)
        if card is not None and section in shelves:
            shelves[section]["items"].append(card)
    shortcuts = shelves.pop(SHORTCUTS_SECTION, None)
    return (shortcuts["items"] if shortcuts else []), [shelf for shelf in shelves.values() if shelf["items"]]
