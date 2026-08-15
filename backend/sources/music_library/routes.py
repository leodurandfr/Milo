# backend/sources/music_library/routes.py
"""FastAPI routes for the Music Library source (Family C).

REST surface for the indexed catalog served by the Navidrome sidecar:
- Browse   — artists (A–Z index), a single artist/album, album lists, genres.
- Search   — fuzzy search3 across artists/albums/songs.
- Genres   — the genre list plus songs-by-genre (a play context).
- Playlists — list, a single playlist with its entries, and create/rename/
             add-tracks/reorder/remove/delete (Subsonic create/update/delete).
- Cover    — a localhost-only proxy for Navidrome getCoverArt bytes, so the
             frontend never talks to Navidrome (or sees its credentials) directly.
- Favorites — star/unstar a song/album/artist.
- Scan     — trigger a quick or full rescan on demand.
- Shares   — CRUD for SMB/NFS network shares: add/edit/remove a share,
             which persists its non-secret config, (re)mounts it read-only under
             /media/milo through milo-mount, and rescans. Credentials are write-
             only — the password is handed to milo-mount and never read back.
- Storages — the storage spaces music can come from (shares + known USB keys)
             with the library id that scopes a browse call to one of them, their
             track/album counts, and a live ``mounted`` flag; plus renaming and
             forgetting a USB key.

Every browse route takes an optional ``library_id``: it is the Navidrome library
of one storage space (see libraries.py). Omitting it browses **every storage
space that is mounted**, not the whole catalog — an unplugged key keeps its index
on purpose, and Navidrome serves its files as HTTP 200 with a JSON error body, so
nothing downstream can tell the difference at play time. The scope is resolved
once per request by ``source.browse_scope`` and the three descent routes, which
Subsonic gives no scope at all, post-filter against ``source.mounted_album_ids``.

There is **no scan-status route**: the scan flag rides the ``/storages`` payload
and the ``source/storages_changed`` push, so one backend watcher observes
Navidrome for the whole appliance instead of every browser polling for itself.
A second endpoint reporting Navidrome's own global counter is what showed a
frozen "2419 tracks indexed…" for the 18 minutes it took to index an iPod.

Playback (play_context/transport) is NOT here — it goes through the generic
`/api/audio/control/{source}` path and lands in source.py (P1-6). All catalog
reads go through the source's shared NavidromeClient; a missing cred file (daemon
not provisioned yet) surfaces as 503 on browse routes.
"""
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from backend.api.route_helpers import api_error_handler
from backend.api.source_dependency import make_source_dependency
from backend.sources.music_library.browse import browse_share
from backend.sources.music_library.disc_merge import (
    expand_merged_album,
    is_merged_id,
    merge_albums,
    parse_merged_id,
)
from backend.sources.music_library.discovery import discover_servers
from backend.sources.music_library.models import (
    CreatePlaylistRequest,
    ShareBrowseRequest,
    ShareRequest,
    StarRequest,
    UpdatePlaylistRequest,
    UsbNameRequest,
)
from backend.sources.music_library.navidrome_client import (
    ALBUM_LIST_TYPES,
    NavidromeAuthError,
    NavidromeClient,
)
from backend.sources.music_library.source import MusicLibrarySource

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/music-library",
    tags=["music-library"],
    responses={404: {"description": "Not found"}},
)

set_source_provider, get_source = make_source_dependency("Music Library")

# Every browse route shares this one: the Navidrome library id of a storage
# space, from GET /storages. Omitted = every storage space that is mounted.
LIBRARY_ID_DESC = "Scope to one storage space (Navidrome library id)"


def _keep_playable(item: Dict[str, Any], key: str, album_ids: set) -> None:
    """Drop an album's or playlist's entries that no storage space can serve.

    In place, and the two header fields that describe those entries are retallied
    with them: left at their catalog values, ``songCount`` and ``duration`` would
    announce tracks the page no longer lists (AlbumView reads both, PlaylistView
    the duration). Every entry carries its own ``albumId``, so a merged multi-disc
    set spanning a mounted and an absent space keeps exactly the discs that play.
    """
    kept = [entry for entry in item.get(key) or [] if entry.get("albumId") in album_ids]
    item[key] = kept
    item["songCount"] = len(kept)
    item["duration"] = sum(int(entry.get("duration") or 0) for entry in kept)


def setup_music_library_routes(source_provider) -> APIRouter:
    """Configure routes with source provider."""
    set_source_provider(source_provider)
    return router


async def _require_client(source: MusicLibrarySource) -> NavidromeClient:
    """Return the source's Navidrome client, or 503 if it isn't ready yet.

    None means the cred file is still absent (Navidrome hasn't finished first-boot
    provisioning) — a transient, self-healing condition, hence 503 not 500.
    """
    client = await source.get_navidrome_client()
    if client is None:
        logger.error("Navidrome client unavailable (cred file missing)")
        raise HTTPException(status_code=503, detail="Music library catalog not ready")
    return client


@asynccontextmanager
async def _catalog_errors(context: str, source: MusicLibrarySource):
    """Error wrapper for catalog routes (api_error_handler + auth recovery).

    HTTPException passes through (404/503). NavidromeAuthError drops the cached
    client so the next request rebuilds it from a possibly-rotated cred file, and
    reports 503 (transient) rather than 500. Anything else is logged and 500.
    """
    try:
        yield
    except HTTPException:
        raise
    except NavidromeAuthError as exc:
        await source.invalidate_navidrome_client()
        logger.error(f"{context}: {exc}")
        raise HTTPException(status_code=503, detail="Music library catalog auth failed")
    except Exception as exc:
        logger.error(f"{context}: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# === Browse ===

@router.get("/artists")
async def get_artists(
    source: MusicLibrarySource = Depends(get_source),
    library_id: Optional[int] = Query(None, description=LIBRARY_ID_DESC),
) -> Dict[str, Any]:
    """All artists as A–Z index buckets (Subsonic getArtists)."""
    async with _catalog_errors("Error listing artists", source):
        client = await _require_client(source)
        return {"index": await client.get_artists(await source.browse_scope(library_id))}


@router.get("/artist/{artist_id}")
async def get_artist(
    artist_id: str,
    source: MusicLibrarySource = Depends(get_source),
) -> Dict[str, Any]:
    """A single artist with its albums (Subsonic getArtist).

    The album list is collapsed for multi-disc sets (see disc_merge) — the artist
    page shows a split "… CD 1/CD 2" release as one album — and holds only the
    albums a mounted storage space can serve: getArtist answers across every
    library, which is how a correctly-scoped artist list still led to 2902
    unplayable tracks (62 of 108 artists, measured).

    An artist left with nothing renders an empty page rather than a 404: the id
    is legitimately held by a tab opened before the key was pulled, and a 404
    there reads as "this artist never existed".
    """
    async with _catalog_errors("Error getting artist", source):
        client = await _require_client(source)
        artist = await client.get_artist(artist_id)
        if artist is None:
            logger.error("Artist not found: %s", artist_id)
            raise HTTPException(status_code=404, detail="Artist not found")
        album_ids = await source.mounted_album_ids()
        artist["album"] = merge_albums(
            [album for album in artist.get("album") or [] if album.get("id") in album_ids]
        )
        return {"artist": artist}


@router.get("/album/{album_id}")
async def get_album(
    album_id: str,
    source: MusicLibrarySource = Depends(get_source),
) -> Dict[str, Any]:
    """A single album with its ordered songs (Subsonic getAlbum).

    A synthetic ``mdisc:`` id (a merged multi-disc release) is expanded into one
    album with the members' tracks concatenated and disc-tagged; a plain id is a
    straight getAlbum. Songs a mounted storage space cannot serve are dropped,
    for the same reason as on the artist page.
    """
    async with _catalog_errors("Error getting album", source):
        client = await _require_client(source)
        if is_merged_id(album_id):
            album = await expand_merged_album(client.get_album, album_id)
        else:
            album = await client.get_album(album_id)
        if album is None:
            logger.error("Album not found: %s", album_id)
            raise HTTPException(status_code=404, detail="Album not found")
        _keep_playable(album, "song", await source.mounted_album_ids())
        return {"album": album}


@router.get("/albums")
async def get_albums(
    source: MusicLibrarySource = Depends(get_source),
    type: str = Query("newest", description="getAlbumList2 type"),
    size: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    genre: Optional[str] = Query(None, description="Required for type=byGenre"),
    from_year: Optional[int] = Query(None, description="Required for type=byYear"),
    to_year: Optional[int] = Query(None, description="Required for type=byYear"),
    library_id: Optional[int] = Query(None, description=LIBRARY_ID_DESC),
) -> Dict[str, Any]:
    """A page of albums (Subsonic getAlbumList2). See ALBUM_LIST_TYPES.

    Multi-disc sets are collapsed (see disc_merge). For the alphabetical grid
    (the only paged browse) the merge runs over the *whole* catalog — cached on
    the source — so a disc pair is never split across a page boundary; other
    list types merge within the returned page.
    """
    async with _catalog_errors("Error listing albums", source):
        if type not in ALBUM_LIST_TYPES:
            logger.error("Invalid album list type: %s", type)
            raise HTTPException(status_code=400, detail=f"Invalid album list type: {type}")
        client = await _require_client(source)
        scope = await source.browse_scope(library_id)
        if type == "alphabeticalByName" and genre is None:
            merged = await source.get_merged_albums(scope)
            return {"albums": merged[offset:offset + size]}
        albums = await client.get_album_list(
            scope,
            list_type=type,
            size=size,
            offset=offset,
            genre=genre,
            from_year=from_year,
            to_year=to_year,
        )
        if albums is None:
            logger.error("Navidrome did not answer the %s album list", type)
            raise HTTPException(
                status_code=503, detail="Music library catalog not ready"
            )
        return {"albums": merge_albums(albums)}


# === Search ===

@router.get("/search")
async def search(
    source: MusicLibrarySource = Depends(get_source),
    query: str = Query("", description="Search term"),
    song_count: int = Query(20, ge=0, le=100),
    album_count: int = Query(20, ge=0, le=100),
    artist_count: int = Query(20, ge=0, le=100),
    library_id: Optional[int] = Query(None, description=LIBRARY_ID_DESC),
) -> Dict[str, Any]:
    """Fuzzy search across artists/albums/songs (Subsonic search3)."""
    async with _catalog_errors("Error searching library", source):
        if not query.strip():
            return {"artists": [], "albums": [], "songs": []}
        client = await _require_client(source)
        result = await client.search3(
            query,
            await source.browse_scope(library_id),
            song_count=song_count,
            album_count=album_count,
            artist_count=artist_count,
        )
        return {
            "artists": result["artist"],
            "albums": merge_albums(result["album"]),
            "songs": result["song"],
        }


# === Genres ===

@router.get("/genres")
async def get_genres(
    source: MusicLibrarySource = Depends(get_source),
    library_id: Optional[int] = Query(None, description=LIBRARY_ID_DESC),
) -> Dict[str, Any]:
    """All genres with song/album counts, in the getGenres shape.

    getGenres itself is never called: it ignores musicFolderId and answers with
    the whole catalog, so every genre list here — scoped or default — is derived
    from the scope's own album catalog (see source.genres_in_scope). The client
    is still required, so a catalog that isn't ready says 503 instead of
    answering "no genres".
    """
    async with _catalog_errors("Error listing genres", source):
        await _require_client(source)
        scope = await source.browse_scope(library_id)
        return {"genres": await source.genres_in_scope(scope)}


@router.get("/genre-songs")
async def get_genre_songs(
    source: MusicLibrarySource = Depends(get_source),
    genre: str = Query(..., min_length=1, description="Genre name"),
    count: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    library_id: Optional[int] = Query(None, description=LIBRARY_ID_DESC),
) -> Dict[str, Any]:
    """Songs tagged with a genre (Subsonic getSongsByGenre) — a play context.

    Genre is a query param (not a path segment) because genre names carry
    slashes and spaces (e.g. "Folk/Rock").
    """
    async with _catalog_errors("Error getting genre songs", source):
        client = await _require_client(source)
        songs = await client.get_songs_by_genre(
            genre, await source.browse_scope(library_id), count=count, offset=offset
        )
        return {"songs": songs}


# === Playlists ===

@router.get("/playlists")
async def get_playlists(
    source: MusicLibrarySource = Depends(get_source),
    library_id: Optional[int] = Query(None, description=LIBRARY_ID_DESC),
) -> Dict[str, Any]:
    """All playlists, without their entries (Subsonic getPlaylists).

    Navidrome keeps playlists catalog-wide and ignores musicFolderId here, so
    membership is decided by the source (see source.playlists_in_scope), not by a
    Subsonic param — for the default scope exactly as for a named storage space.
    """
    async with _catalog_errors("Error listing playlists", source):
        client = await _require_client(source)
        playlists = await client.get_playlists()
        scope = await source.browse_scope(library_id)
        return {"playlists": await source.playlists_in_scope(playlists, scope)}


@router.get("/playlist/{playlist_id}")
async def get_playlist(
    playlist_id: str,
    source: MusicLibrarySource = Depends(get_source),
) -> Dict[str, Any]:
    """A single playlist with its ordered entries (Subsonic getPlaylist).

    A playlist can mix storage spaces, so the entries no mounted space can serve
    are dropped in silence — the alternative is a queue that skips tracks with
    nothing said about why.
    """
    async with _catalog_errors("Error getting playlist", source):
        client = await _require_client(source)
        playlist = await client.get_playlist(playlist_id)
        if playlist is None:
            logger.error("Playlist not found: %s", playlist_id)
            raise HTTPException(status_code=404, detail="Playlist not found")
        _keep_playable(playlist, "entry", await source.mounted_album_ids())
        return {"playlist": playlist}


@router.post("/playlists")
async def create_playlist(
    request: CreatePlaylistRequest,
    source: MusicLibrarySource = Depends(get_source),
) -> Dict[str, Any]:
    """Create a playlist (Subsonic createPlaylist), optionally seeded with songs.

    Returns the created playlist (with its generated id) so the caller can open it.
    ``library_id`` ties it to the storage space it was created in — the only way
    an empty playlist can be placed, since it has no track to be judged by.
    """
    async with _catalog_errors("Error creating playlist", source):
        client = await _require_client(source)
        playlist = await client.create_playlist(request.name, song_ids=request.song_ids)
        if playlist is None:
            logger.error("Navidrome rejected playlist creation: %s", request.name)
            raise HTTPException(status_code=502, detail="Navidrome rejected playlist creation")
        if request.library_id is not None and playlist.get("id"):
            await source.shares.record_playlist_storage(
                playlist["id"], request.library_id
            )
        return {"status": "success", "playlist": playlist}


@router.put("/playlist/{playlist_id}")
async def update_playlist(
    playlist_id: str,
    request: UpdatePlaylistRequest,
    source: MusicLibrarySource = Depends(get_source),
) -> Dict[str, Any]:
    """Edit a playlist (Subsonic update/create): rename, append tracks, or replace
    the whole ordered list (reorder/remove). The request carries exactly one of
    those operations (enforced by the model)."""
    async with _catalog_errors("Error updating playlist", source):
        client = await _require_client(source)
        if request.track_ids is not None:
            ok = await client.set_playlist_tracks(playlist_id, request.track_ids)
        elif request.song_ids_to_add is not None:
            ok = await client.update_playlist(
                playlist_id, song_ids_to_add=request.song_ids_to_add
            )
        else:
            ok = await client.update_playlist(playlist_id, name=request.name)
        if not ok:
            logger.error("Navidrome rejected playlist update: %s", playlist_id)
            raise HTTPException(status_code=502, detail="Navidrome rejected playlist update")
        # Its first track may have moved to another storage space, which is what
        # an unrecorded playlist is placed by.
        source.forget_playlist_placement(playlist_id)
        return {"status": "success"}


@router.delete("/playlist/{playlist_id}")
async def delete_playlist(
    playlist_id: str,
    source: MusicLibrarySource = Depends(get_source),
) -> Dict[str, Any]:
    """Delete a playlist (Subsonic deletePlaylist)."""
    async with _catalog_errors("Error deleting playlist", source):
        client = await _require_client(source)
        if not await client.delete_playlist(playlist_id):
            logger.error("Navidrome rejected playlist deletion: %s", playlist_id)
            raise HTTPException(status_code=502, detail="Navidrome rejected playlist deletion")
        await source.shares.forget_playlist(playlist_id)
        source.forget_playlist_placement(playlist_id)
        return {"status": "success"}


# === Cover art proxy ===

@router.get("/cover/{cover_id}")
async def get_cover(
    cover_id: str,
    source: MusicLibrarySource = Depends(get_source),
    size: Optional[int] = Query(None, ge=1, le=2000, description="Square px"),
) -> Response:
    """Proxy Navidrome getCoverArt bytes so the frontend stays on /api/*.

    Cached hard (1 year) — Navidrome cover ids are stable per album, and the
    frontend cache-busts by requesting a new id when art changes.
    """
    async with _catalog_errors("Error getting cover art", source):
        client = await _require_client(source)
        result = await client.get_cover_art(cover_id, size=size)
        if result is None:
            # Expected, not an error: the album has no art (never had any, or its
            # folder image was removed) — the frontend shows its own placeholder.
            # Kept below ERROR so it never reaches the WebSocketLogHandler banner.
            logger.debug("Cover art not available: %s", cover_id)
            raise HTTPException(status_code=404, detail="Cover art not available")
        data, content_type = result
        return Response(
            content=data,
            media_type=content_type,
            headers={"Cache-Control": "public, max-age=31536000"},
        )


# === Favorites (star) ===

def _star_targets(request: StarRequest) -> list:
    """Real Subsonic ids for a star toggle — fanning a merged album (``mdisc:``)
    out to its member album ids so favouriting the collapsed release stars every
    disc. Any other id is starred as-is."""
    if request.kind == "album" and is_merged_id(request.id):
        return parse_merged_id(request.id)
    return [request.id]


@router.post("/star")
async def star(
    request: StarRequest,
    source: MusicLibrarySource = Depends(get_source),
) -> Dict[str, Any]:
    """Star a song/album/artist (Subsonic star)."""
    async with _catalog_errors("Error starring item", source):
        client = await _require_client(source)
        for item_id in _star_targets(request):
            if not await client.star(item_id, kind=request.kind):
                logger.error("Navidrome rejected star for %s (%s)", item_id, request.kind)
                raise HTTPException(status_code=502, detail="Navidrome rejected star")
        return {"status": "success"}


@router.post("/unstar")
async def unstar(
    request: StarRequest,
    source: MusicLibrarySource = Depends(get_source),
) -> Dict[str, Any]:
    """Remove a star (Subsonic unstar)."""
    async with _catalog_errors("Error unstarring item", source):
        client = await _require_client(source)
        for item_id in _star_targets(request):
            if not await client.unstar(item_id, kind=request.kind):
                logger.error("Navidrome rejected unstar for %s (%s)", item_id, request.kind)
                raise HTTPException(status_code=502, detail="Navidrome rejected unstar")
        return {"status": "success"}


@router.get("/starred")
async def get_starred(
    source: MusicLibrarySource = Depends(get_source),
    library_id: Optional[int] = Query(None, description=LIBRARY_ID_DESC),
) -> Dict[str, Any]:
    """Starred songs (Subsonic getStarred2) — backs the virtual "Liked Songs"
    playlist. Songs only; albums/artists aren't surfaced as favourites."""
    async with _catalog_errors("Error listing starred songs", source):
        client = await _require_client(source)
        starred = await client.get_starred(await source.browse_scope(library_id))
        return {"songs": starred["song"]}


# === Scan status ===

@router.post("/scan")
async def trigger_scan(
    source: MusicLibrarySource = Depends(get_source),
) -> Dict[str, Any]:
    """Kick a Navidrome library rescan on demand ("I added music, refresh now").

    A quick (mtime-based) scan: it picks up new/changed files but does NOT purge
    disappeared ones — purging only ever happens on the explicit full scan below,
    so a refresh stays a fast, non-destructive "index what's new". Needed because
    inotify does not report changes made on the far side of a CIFS/NFS mount, so
    files added directly on a NAS aren't picked up by the watcher — only by a
    scan. 503 until provisioned.

    Cheap on an already-indexed catalog: measured at 401 ms across 12 488 tracks
    on two storage spaces. Only *new* files cost real time (the first pass over a
    10 000-track iPod took 18 minutes), which is why plugging a key in triggers
    an ordinary global scan rather than trying to scope one — Navidrome exposes
    no per-library scan, and there would be nothing to save.
    """
    async with _catalog_errors("Error starting scan", source):
        client = await _require_client(source)
        if not await client.start_scan():
            logger.error("Navidrome refused the scan request")
            raise HTTPException(status_code=502, detail="Navidrome refused the scan")
        # The catalog is about to change — drop the merged-album cache so the next
        # grid load reflects new/removed music without waiting for its TTL.
        source.invalidate_album_cache()
        # Only now: note_scan_started's own precondition is that a scan really
        # is running, and it pushes `scanning: true` to every client.
        await source.shares.note_scan_started()
        return {"status": "success"}


@router.post("/scan/full")
async def trigger_full_scan(
    source: MusicLibrarySource = Depends(get_source),
) -> Dict[str, Any]:
    """Full rescan that also purges vanished tracks — the settings "remove deleted
    music" action (the quick /scan never purges; see its docstring).

    A full scan drops every track Navidrome can't see (Scanner.PurgeMissing="full"),
    so a storage space that is away would have its still-valid tracks purged. When
    any is unmounted we skip the scan and return ``{"status": "blocked",
    offline_shares}`` rather than a 5xx (an asleep NAS is a normal precondition to
    surface). Unplugged USB keys count too, and that is the point: a key keeps its
    library and its index across an unplug, so a full scan run while it is away is
    exactly what would throw that index out. 503 until ready.
    """
    async with _catalog_errors("Error starting full scan", source):
        offline = await source.shares.offline_names()
        if offline:
            logger.info("Full scan skipped; storage offline: %s", ", ".join(offline))
            return {"status": "blocked", "offline_shares": offline}
        client = await _require_client(source)
        if not await client.start_scan(full=True):
            logger.error("Navidrome refused the full scan request")
            raise HTTPException(status_code=502, detail="Navidrome refused the scan")
        source.invalidate_album_cache()
        await source.shares.note_scan_started()
        return {"status": "success"}


# === Network shares (SMB/NFS) ===

@router.get("/storages")
async def list_storages(
    source: MusicLibrarySource = Depends(get_source),
) -> Dict[str, Any]:
    """The storage spaces music can come from, with their library ids and counts.

    What the library view's storage filter is built from: one entry per
    configured share and per known USB key, each carrying the ``library_id`` a
    browse call is scoped by (null while Navidrome hasn't accepted it yet), a
    live ``mounted`` flag, and its track/album counts.

    The initial load only — every later change arrives as the
    ``source/storages_changed`` WS event, which carries this exact shape.
    """
    async with api_error_handler("Error listing storage spaces", logger):
        return {
            "storages": await source.shares.storages_with_stats(),
            "scanning": bool(source.shares.scan_state().get("scanning")),
        }


@router.put("/usb-devices/{uuid}")
async def rename_usb_device(
    uuid: str,
    request: UsbNameRequest,
    source: MusicLibrarySource = Depends(get_source),
) -> Dict[str, Any]:
    """Name a known USB key (filed under its filesystem UUID).

    The name follows the key across replugs and is what the settings row and the
    storage filter show; an empty name restores the filesystem label.
    """
    async with api_error_handler("Error renaming USB device", logger):
        if not await source.shares.rename_usb(uuid, request.name):
            logger.error("USB device not found: %s", uuid)
            raise HTTPException(status_code=404, detail="USB device not found")
        return {"status": "success"}


@router.delete("/usb-devices/{uuid}")
async def forget_usb_device(
    uuid: str,
    source: MusicLibrarySource = Depends(get_source),
) -> Dict[str, Any]:
    """Forget an unplugged USB key, retiring its Navidrome library and index.

    The counterpart to keeping a key's index forever: a key that will not come
    back would otherwise hold its catalog rows for good. 409 while it is plugged
    in — the mount would put it straight back on the next reconcile, so the only
    readable outcome is to unplug it first.
    """
    async with api_error_handler("Error forgetting USB device", logger):
        if await source.shares.usb_is_mounted(uuid):
            logger.error("USB device still plugged in: %s", uuid)
            raise HTTPException(
                status_code=409, detail="Unplug the key before forgetting it"
            )
        if not await source.shares.forget_usb(uuid):
            logger.error("USB device not found: %s", uuid)
            raise HTTPException(status_code=404, detail="USB device not found")
        return {"status": "success"}


@router.get("/shares/discover")
async def discover_shares() -> Dict[str, Any]:
    """SMB/NFS servers found on the LAN via mDNS, to prefill the add-share form.

    Resilient (always HTTP 200): discovery is a convenience over manual entry, so
    an unavailable Avahi, a timeout, or a parse miss yields an empty list rather
    than an error. Needs no source — it never touches Navidrome or credentials.
    """
    return {"servers": await discover_servers()}


@router.post("/shares/browse")
async def browse_share_route(request: ShareBrowseRequest) -> Dict[str, Any]:
    """Walk a server one level (SMB shares/folders, NFS exports) for the wizard.

    Unprivileged and mount-free (smbclient / showmount). Always HTTP 200 with a
    typed ``status`` (ok / auth_required / unreachable / error) the wizard branches
    on — a wrong password or an offline NAS is normal wizard flow, not a 5xx.
    Credentials are used transiently for the smbclient call, never persisted here.
    """
    result = await browse_share(
        share_type=request.type,
        host=request.host,
        path=request.path,
        credentials={
            "username": request.username,
            "password": request.password,
            "domain": request.domain,
        } if request.password or request.username else None,
    )
    return result


@router.get("/shares")
async def list_shares(
    source: MusicLibrarySource = Depends(get_source),
) -> Dict[str, Any]:
    """All configured network shares (non-secret metadata; never credentials)."""
    async with api_error_handler("Error listing shares", logger):
        return {"shares": await source.shares.list()}


@router.post("/shares")
async def create_share(
    request: ShareRequest,
    source: MusicLibrarySource = Depends(get_source),
) -> Dict[str, Any]:
    """Add a share, mount it read-only under /media/milo, and rescan.

    Returns the created share (with its generated id) minus any credentials.
    """
    async with api_error_handler("Error creating share", logger):
        return {"status": "success", "share": await source.shares.add(request)}


@router.put("/shares/{share_id}")
async def update_share(
    share_id: str,
    request: ShareRequest,
    source: MusicLibrarySource = Depends(get_source),
) -> Dict[str, Any]:
    """Replace a share's config, remount, and rescan (404 if unknown).

    Idempotent: a request that omits the password keeps the existing cred file.
    """
    async with api_error_handler("Error updating share", logger):
        share = await source.shares.update(share_id, request)
        if share is None:
            logger.error("Share not found: %s", share_id)
            raise HTTPException(status_code=404, detail="Share not found")
        return {"status": "success", "share": share}


@router.delete("/shares/{share_id}")
async def delete_share(
    share_id: str,
    source: MusicLibrarySource = Depends(get_source),
) -> Dict[str, Any]:
    """Unmount + remove a share and forget its credentials (404 if unknown)."""
    async with api_error_handler("Error deleting share", logger):
        if not await source.shares.remove(share_id):
            logger.error("Share not found: %s", share_id)
            raise HTTPException(status_code=404, detail="Share not found")
        return {"status": "success"}
