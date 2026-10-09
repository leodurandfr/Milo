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
# The shelves whose covers carry each card's name, written in the picture:
# their cards show no name under the cover, and each says what line it writes
# instead. A mix keeps Spotify's subtitle (the artists it plays). A station
# names its artists from the card's own list of them, which is in no language,
# where its subtitle is a sentence in the account's ("Avec …" under an English
# home). Best of artists and Soundtrack your day write nothing: their subtitle
# describes what the cover already names. Same ids in every language (measured
# 2026-10-09).
_SUBTITLE, _ARTISTS = "subtitle", "artists"
NAMED_COVER_SECTIONS: Dict[str, Optional[str]] = {
    "spotify:section:0JQ5DAnM3wGh0gz1MXnu89": _SUBTITLE,  # Your top mixes
    "spotify:section:0JQ5DAnM3wGh0gz1MXnu3R": _ARTISTS,   # Recommended Stations
    "spotify:section:0JQ5DAnM3wGh0gz1MXnu3n": None,       # Best of artists
    "spotify:section:0JQ5DAUnp4wcj0bCb3wh8h": None,       # Soundtrack your day
}
# The artist a shelf is about ("For fans of FKA twigs", "More like Prince
# Waly"): the home names it only inside the shelf's title and draws no picture
# of it, where the apps head the shelf with the artist's photo. The shelf's
# station or This Is card carries the artist's id in its cover's address
# (measured 2026-10-09 on every such shelf of the owner's home).
_SEED_ARTIST_COVER = re.compile(r"/img/(?:radio/artist|thisisv3)/([0-9A-Za-z]{22})/")
_BASE62 = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
# Liked Songs, as the home names it: the signed-in user is "@".
_HOME_LIKED_SONGS = re.compile(r"^spotify:user:[^:]+:collection$")

# An album cover on i.scdn.co carries its size in the id: b273 (640 px),
# 1e02 (300 px), 4851 (64 px). The three are served for every cover (measured).
_ALBUM_COVER = re.compile(r"^(https://i\.scdn\.co/image/ab67616d0000)(b273|1e02|4851)(\w+)$")
THUMBNAIL_SIZE = "4851"
MOSAIC_URL = "https://mosaic.scdn.co/300/{}"
MOSAIC_TILES = 4


def spotify_gid(base62_id: str) -> str:
    """A Spotify id as the hex gid its metadata service is addressed by."""
    number = 0
    for char in base62_id:
        number = number * 62 + _BASE62.index(char)
    return f"{number:032x}"


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


def artists_of(track: Dict[str, Any]) -> List[Dict[str, Optional[str]]]:
    """A go-librespot track's artists, each name paired with its uri: the two
    lists come in the same order, and an artist given no uri (an episode's
    show) has none rather than its neighbour's."""
    names = track.get("artist_names") or []
    uris = track.get("artist_uris") or []
    return [
        {"name": name, "uri": uris[i] if i < len(uris) and uris[i] else None}
        for i, name in enumerate(names)
    ]


def queue_entry(uri: Optional[str], track: Optional[Dict[str, Any]]) -> Dict[str, Optional[str]]:
    """One track of the play order: its uri, and its title and artist line
    once go-librespot has its metadata (a window entry's track is null
    until then)."""
    track = track or {}
    return {
        "uri": uri,
        "title": track.get("name") or None,
        "artist": ", ".join(track.get("artist_names") or []) or None,
    }


def normalize_track(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One /context/tracks entry; None while its metadata is not cached yet."""
    track = entry.get("track")
    if not track:
        return None
    artwork = track.get("album_cover_url") or None
    return {
        "uri": track.get("uri") or entry.get("uri"),
        "title": track.get("name"),
        "artists": artists_of(track),
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


def _card_line(card: Dict[str, Any], section: str) -> Optional[str]:
    line = NAMED_COVER_SECTIONS.get(section, _SUBTITLE)
    if line == _ARTISTS:
        return (card.get("metadata") or {}).get("label")
    return (card.get("text") or {}).get("subtitle") if line == _SUBTITLE else None


def _home_card(card: Dict[str, Any], section: str) -> Optional[Dict[str, Any]]:
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
        "subtitle": _card_line(card, section) or None,
        "image": image or None,
        "name_in_cover": section in NAMED_COVER_SECTIONS,
    }


def artist_portrait(metadata: Dict[str, Any]) -> Optional[str]:
    """The small (160 px) photo in an artist's metadata, else its first."""
    images = (metadata.get("portrait_group") or {}).get("image") or []
    small = next((image for image in images if image.get("size") == "SMALL"), images[0] if images else None)
    return f"https://i.scdn.co/image/{small['file_id']}" if small and small.get("file_id") else None


def artist_shelf_seeds(shelves: List[Dict[str, Any]]) -> Dict[str, str]:
    """For each shelf that may be about an artist, the id of the artist whose
    station or This Is it holds first. A named-cover shelf is a row of them
    and about nobody (Recommended Stations, Best of artists)."""
    seeds: Dict[str, str] = {}
    for shelf in shelves:
        if shelf["id"] in NAMED_COVER_SECTIONS:
            continue
        for card in shelf["items"]:
            match = _SEED_ARTIST_COVER.search(card.get("image") or "")
            if match:
                seeds[shelf["id"]] = match.group(1)
                break
    return seeds


def head_artist_shelves(
    shelves: List[Dict[str, Any]], seeds: Dict[str, str], artists: Dict[str, Dict[str, Optional[str]]],
) -> List[Dict[str, Any]]:
    """Each shelf about an artist gets its `artist` ({name, image}) and the
    `overline` its title says around the name ("Pour les fans de"). Only when
    the name opens or closes the title as a whole word: a shelf whose station
    is someone else's keeps its plain title, and so does a language that puts
    the name mid-sentence (Chinese "与 X 相似的更多艺人")."""
    for shelf in shelves:
        artist = artists.get(seeds.get(shelf["id"], ""))
        title, name = shelf["title"] or "", (artist or {}).get("name") or ""
        if not name or len(title) <= len(name):
            continue
        if title.startswith(name) and title[len(name)].isspace():
            overline = title[len(name):]
        elif title.endswith(name) and title[-len(name) - 1].isspace():
            overline = title[:-len(name)]
        else:
            continue
        shelf["artist"] = {"name": name, "image": artist.get("image")}
        shelf["overline"] = overline.strip()
    return shelves


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
        card = _home_card(entry, section)
        if card is not None and section in shelves:
            shelves[section]["items"].append(card)
    shortcuts = shelves.pop(SHORTCUTS_SECTION, None)
    return (shortcuts["items"] if shortcuts else []), [shelf for shelf in shelves.values() if shelf["items"]]


# The artist page (spclient artistview, measured 2026-10-05 on four artists):
# a header, then sections in the order Spotify's apps draw them. The section
# ids are the same for every artist and in every language.
ARTIST_TOP_TRACKS_SECTION = "artist-entity-view-top-tracks-combined"
ARTIST_POPULAR_RELEASES_SECTION = "artist-entity-view-releases"
DISCOGRAPHY_SECTION = "discography"
# The discography's lists, by what each release in it is. Its latest release
# is pulled out of its own list into a section of its own (measured on every
# artist that had one), which says nothing of what it is.
LATEST_RELEASE_SECTION = "artist-entity-view-latest-release"
_RELEASE_GROUPS = {
    "artist-entity-view-albums-source": ("albums", "album"),
    "artist-entity-view-artist-singles-source": ("singles", "single"),
    "artist-entity-view-compilations-source": ("compilations", "compilation"),
}
# What Spotify calls an album rather than a single or an EP (which it files
# with the singles): seven tracks or more, or thirty minutes or more.
ALBUM_MIN_TRACKS = 7
ALBUM_MIN_MS = 30 * 60 * 1000
# A card's target: Spotify still names its own playlists the old way
# (spotify:user:spotify:playlist:…) on this page, and links its discography and
# concerts with uris no listing opens (…:releases, spotify:concert:…).
_CARD_TARGET = re.compile(r"^spotify:(?:user:[^:]+:)?(playlist|album|artist):([A-Za-z0-9]{22})$")
# An artist photo on i.scdn.co, 320 px as the page names it; 640 px for the header.
_ARTIST_IMAGE = re.compile(r"^(https://i\.scdn\.co/image/ab676161000)0(?:5174|e5eb|f178)(\w+)$")


def _artist_photo(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    match = _ARTIST_IMAGE.match(url)
    return f"{match.group(1)}0e5eb{match.group(2)}" if match else url


def _view_card(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """An artist page row or card, shaped as a home card; None for anything no
    listing opens (a concert, the merch, a "see all" link)."""
    click = (entry.get("events") or {}).get("click") or {}
    if click.get("name") != "navigate":
        return None
    match = _CARD_TARGET.match((click.get("data") or {}).get("uri") or "")
    if match is None:
        return None
    kind, item_id = match.groups()
    text = entry.get("text") or {}
    image = ((entry.get("images") or {}).get("main") or {}).get("uri")
    return {
        "uri": f"spotify:{kind}:{item_id}",
        "kind": _CARD_KINDS[kind],
        "name": text.get("title") or None,
        "subtitle": text.get("subtitle") or None,
        "image": image or None,
    }


def _is_section_header(entry: Dict[str, Any]) -> bool:
    # The discography's headers are filed as rows (glue2:sectionHeader).
    component = entry.get("component") or {}
    return component.get("category") == "header" or "sectionHead" in (component.get("id") or "")


def _view_sections(view: Dict[str, Any]) -> List[Dict[str, Any]]:
    """An artistview body as its sections, in order: a header and the rows
    after it, or a carousel and its cards. Sections are kept even when empty."""
    sections: List[Dict[str, Any]] = []
    for entry in view.get("body") or []:
        title = (entry.get("text") or {}).get("title") or None
        if _is_section_header(entry):
            sections.append({"id": entry.get("id"), "title": title, "items": []})
        elif (entry.get("component") or {}).get("category") == "carousel":
            cards = [_view_card(child) for child in entry.get("children") or []]
            sections.append({"id": entry.get("id"), "title": title, "items": [c for c in cards if c]})
        elif sections:
            card = _view_card(entry)
            if card is not None:
                sections[-1]["items"].append(card)
    return sections


def latest_release_uri(releases: Optional[Dict[str, Any]]) -> Optional[str]:
    """The release the discography sets apart as the latest, if it does."""
    for section in _view_sections(releases or {}):
        if section["id"] == LATEST_RELEASE_SECTION and section["items"]:
            return section["items"][0]["uri"]
    return None


def release_type(length: int, durations_ms: Optional[List[int]]) -> Optional[str]:
    """What Spotify files a release as, from its tracks: "album", or "single"
    (an EP included). None while it cannot tell: under seven tracks, only the
    whole running time decides, so every track must be known."""
    if length >= ALBUM_MIN_TRACKS:
        return "album"
    if durations_ms is None or not length or len(durations_ms) < length:
        return None
    return "album" if sum(durations_ms) >= ALBUM_MIN_MS else "single"


def _release(card: Dict[str, Any], kind: Optional[str], latest: Optional[str]) -> Dict[str, Any]:
    # The row's subtitle is the release's year.
    return {
        "uri": card["uri"], "kind": card["kind"], "name": card["name"], "image": card["image"],
        "year": card["subtitle"], "release_type": kind, "latest": card["uri"] == latest,
    }


def _discography(popular: Dict[str, Any], releases: Optional[Dict[str, Any]],
                 latest_type: Optional[str]) -> Dict[str, Any]:
    """The artist's releases as Spotify's desktop app files them under its
    discography: the popular ones, then one list per kind, each newest first
    as Spotify orders it. The latest release goes back at the head of its own
    list once `latest_type` says which (Spotify leaves it out of it), else at
    the head of the popular ones, and every release is marked with its kind
    for the card to say."""
    groups: List[Dict[str, Any]] = []
    kinds: Dict[str, str] = {}
    latest_card = None
    for section in _view_sections(releases or {}):
        if section["id"] == LATEST_RELEASE_SECTION:
            latest_card = section["items"][0] if section["items"] else None
            continue
        group_id, kind = _RELEASE_GROUPS.get(section["id"], (section["id"], None))
        groups.append({"id": group_id, "title": section["title"], "kind": kind, "items": section["items"]})
        for card in section["items"]:
            if kind:
                kinds.setdefault(card["uri"], kind)
    latest = latest_card["uri"] if latest_card else None
    if latest and latest_type:
        kinds[latest] = latest_type
    shaped = [{
        "id": "popular", "title": popular["title"],
        "items": [_release(card, kinds.get(card["uri"]), latest) for card in popular["items"]],
    }]
    for group in groups:
        cards = group["items"]
        if latest_card and latest_type and group["kind"] == latest_type:
            cards = [latest_card, *cards]
        shaped.append({
            "id": group["id"], "title": group["title"],
            "items": [_release(card, group["kind"] or kinds.get(card["uri"]), latest) for card in cards],
        })
    # Spotify took it out of its own list: placed in none (its kind unknown,
    # or no list of that kind), it leads the popular ones rather than vanish.
    if latest_card and not any(r["uri"] == latest for group in shaped for r in group["items"]):
        shaped[0]["items"].insert(0, _release(latest_card, latest_type, latest))
    return {"id": DISCOGRAPHY_SECTION, "title": None,
            "groups": [group for group in shaped if group["items"]]}


def artist_page(view: Dict[str, Any], releases: Optional[Dict[str, Any]],
                latest_type: Optional[str] = None) -> Dict[str, Any]:
    """The artist page as Spotify's apps draw it, minus its popular tracks —
    the browser lists those from the artist's own listing, whose first ten they
    are (measured) — and with its four popular releases turned, in place, into
    the discography: those four, then the albums, the singles and EPs and the
    compilations (see _discography). Without the discography (`releases`
    None) the four popular releases are all of it. A section with nothing a
    listing opens (concerts, merch) is left out."""
    header = view.get("header") or {}
    text = header.get("text") or {}
    sections: List[Dict[str, Any]] = []
    popular_title = None
    for section in _view_sections(view):
        if section["id"] == ARTIST_TOP_TRACKS_SECTION:
            popular_title = section["title"]
        elif section["id"] == ARTIST_POPULAR_RELEASES_SECTION:
            discography = _discography(section, releases, latest_type)
            if discography["groups"]:
                sections.append(discography)
        elif section["items"]:
            sections.append(section)
    return {
        "name": text.get("title") or view.get("title") or None,
        "image": _artist_photo(((header.get("images") or {}).get("main") or {}).get("uri")),
        "listeners": text.get("accessory") or None,
        "popular_title": popular_title,
        "sections": sections,
    }
