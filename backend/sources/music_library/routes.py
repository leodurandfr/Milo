# backend/sources/music_library/routes.py
"""FastAPI routes for the Music Library source (Family C).

REST surface for the indexed catalog served by the Navidrome sidecar:
- Browse   — artists (A–Z index), a single artist/album, album lists, genres.
- Search   — fuzzy search3 across artists/albums/songs.
- Genres   — the genre list plus songs-by-genre (a play context).
- Playlists — list + a single playlist with its entries (read-only in P1-5).
- Cover    — a localhost-only proxy for Navidrome getCoverArt bytes, so the
             frontend never talks to Navidrome (or sees its credentials) directly.
- Favorites — star/unstar a song/album/artist.
- Scan     — the current scan status (polled while a fresh library indexes).
- Shares   — CRUD for SMB/NFS network shares (Phase 2): add/edit/remove a share,
             which persists its non-secret config, (re)mounts it read-only under
             /media/milo through milo-mount, and rescans. Credentials are write-
             only — the password is handed to milo-mount and never read back.

Playback (play_context/transport) is NOT here — it goes through the generic
`/api/audio/control/{source}` path and lands in source.py (P1-6). All catalog
reads go through the source's shared NavidromeClient; a missing cred file (daemon
not provisioned yet) surfaces as 503 on browse routes and a null status on the
polled scan-status route.
"""
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from backend.api.route_helpers import api_error_handler
from backend.api.source_dependency import make_source_dependency
from backend.sources.music_library.models import ShareRequest, StarRequest
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
) -> Dict[str, Any]:
    """All artists as A–Z index buckets (Subsonic getArtists)."""
    async with _catalog_errors("Error listing artists", source):
        client = await _require_client(source)
        return {"index": await client.get_artists()}


@router.get("/artist/{artist_id}")
async def get_artist(
    artist_id: str,
    source: MusicLibrarySource = Depends(get_source),
) -> Dict[str, Any]:
    """A single artist with its albums (Subsonic getArtist)."""
    async with _catalog_errors("Error getting artist", source):
        client = await _require_client(source)
        artist = await client.get_artist(artist_id)
        if artist is None:
            logger.error("Artist not found: %s", artist_id)
            raise HTTPException(status_code=404, detail="Artist not found")
        return {"artist": artist}


@router.get("/album/{album_id}")
async def get_album(
    album_id: str,
    source: MusicLibrarySource = Depends(get_source),
) -> Dict[str, Any]:
    """A single album with its ordered songs (Subsonic getAlbum)."""
    async with _catalog_errors("Error getting album", source):
        client = await _require_client(source)
        album = await client.get_album(album_id)
        if album is None:
            logger.error("Album not found: %s", album_id)
            raise HTTPException(status_code=404, detail="Album not found")
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
) -> Dict[str, Any]:
    """A page of albums (Subsonic getAlbumList2). See ALBUM_LIST_TYPES."""
    async with _catalog_errors("Error listing albums", source):
        if type not in ALBUM_LIST_TYPES:
            logger.error("Invalid album list type: %s", type)
            raise HTTPException(status_code=400, detail=f"Invalid album list type: {type}")
        client = await _require_client(source)
        albums = await client.get_album_list(
            list_type=type,
            size=size,
            offset=offset,
            genre=genre,
            from_year=from_year,
            to_year=to_year,
        )
        return {"albums": albums}


# === Search ===

@router.get("/search")
async def search(
    source: MusicLibrarySource = Depends(get_source),
    query: str = Query("", description="Search term"),
    song_count: int = Query(20, ge=0, le=100),
    album_count: int = Query(20, ge=0, le=100),
    artist_count: int = Query(20, ge=0, le=100),
) -> Dict[str, Any]:
    """Fuzzy search across artists/albums/songs (Subsonic search3)."""
    async with _catalog_errors("Error searching library", source):
        if not query.strip():
            return {"artists": [], "albums": [], "songs": []}
        client = await _require_client(source)
        result = await client.search3(
            query,
            song_count=song_count,
            album_count=album_count,
            artist_count=artist_count,
        )
        return {
            "artists": result["artist"],
            "albums": result["album"],
            "songs": result["song"],
        }


# === Genres ===

@router.get("/genres")
async def get_genres(
    source: MusicLibrarySource = Depends(get_source),
) -> Dict[str, Any]:
    """All genres with song/album counts (Subsonic getGenres)."""
    async with _catalog_errors("Error listing genres", source):
        client = await _require_client(source)
        return {"genres": await client.get_genres()}


@router.get("/genre-songs")
async def get_genre_songs(
    source: MusicLibrarySource = Depends(get_source),
    genre: str = Query(..., min_length=1, description="Genre name"),
    count: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    """Songs tagged with a genre (Subsonic getSongsByGenre) — a play context.

    Genre is a query param (not a path segment) because genre names carry
    slashes and spaces (e.g. "Folk/Rock").
    """
    async with _catalog_errors("Error getting genre songs", source):
        client = await _require_client(source)
        songs = await client.get_songs_by_genre(genre, count=count, offset=offset)
        return {"songs": songs}


# === Playlists ===

@router.get("/playlists")
async def get_playlists(
    source: MusicLibrarySource = Depends(get_source),
) -> Dict[str, Any]:
    """All playlists, without their entries (Subsonic getPlaylists)."""
    async with _catalog_errors("Error listing playlists", source):
        client = await _require_client(source)
        return {"playlists": await client.get_playlists()}


@router.get("/playlist/{playlist_id}")
async def get_playlist(
    playlist_id: str,
    source: MusicLibrarySource = Depends(get_source),
) -> Dict[str, Any]:
    """A single playlist with its ordered entries (Subsonic getPlaylist)."""
    async with _catalog_errors("Error getting playlist", source):
        client = await _require_client(source)
        playlist = await client.get_playlist(playlist_id)
        if playlist is None:
            logger.error("Playlist not found: %s", playlist_id)
            raise HTTPException(status_code=404, detail="Playlist not found")
        return {"playlist": playlist}


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
            logger.error("Cover art not available: %s", cover_id)
            raise HTTPException(status_code=404, detail="Cover art not available")
        data, content_type = result
        return Response(
            content=data,
            media_type=content_type,
            headers={"Cache-Control": "public, max-age=31536000"},
        )


# === Favorites (star) ===

@router.post("/star")
async def star(
    request: StarRequest,
    source: MusicLibrarySource = Depends(get_source),
) -> Dict[str, Any]:
    """Star a song/album/artist (Subsonic star)."""
    async with _catalog_errors("Error starring item", source):
        client = await _require_client(source)
        if not await client.star(request.id, kind=request.kind):
            logger.error("Navidrome rejected star for %s (%s)", request.id, request.kind)
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
        if not await client.unstar(request.id, kind=request.kind):
            logger.error("Navidrome rejected unstar for %s (%s)", request.id, request.kind)
            raise HTTPException(status_code=502, detail="Navidrome rejected unstar")
        return {"status": "success"}


# === Scan status ===

@router.get("/scan-status")
async def get_scan_status(
    source: MusicLibrarySource = Depends(get_source),
) -> Dict[str, Any]:
    """Current library scan status (polled while a fresh library indexes).

    Resilient (always HTTP 200): a not-yet-provisioned or still-starting daemon
    yields ``scan_status: null`` rather than an error, since the frontend polls
    this and an unbuilt catalog is a normal transient state, not a failure.
    """
    try:
        client = await source.get_navidrome_client()
        status = await client.get_scan_status() if client else None
    except NavidromeAuthError as exc:
        await source.invalidate_navidrome_client()
        logger.error("Scan status auth failed: %s", exc)
        status = None
    except Exception as exc:
        logger.error("Error getting scan status: %s", exc)
        status = None
    return {"scan_status": status}


# === Network shares (SMB/NFS) ===

@router.get("/shares")
async def list_shares(
    source: MusicLibrarySource = Depends(get_source),
) -> Dict[str, Any]:
    """All configured network shares (non-secret metadata; never credentials)."""
    async with api_error_handler("Error listing shares", logger):
        return {"shares": await source.list_shares()}


@router.post("/shares")
async def create_share(
    request: ShareRequest,
    source: MusicLibrarySource = Depends(get_source),
) -> Dict[str, Any]:
    """Add a share, mount it read-only under /media/milo, and rescan.

    Returns the created share (with its generated id) minus any credentials.
    """
    async with api_error_handler("Error creating share", logger):
        return {"status": "success", "share": await source.add_share(request)}


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
        share = await source.update_share(share_id, request)
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
        if not await source.remove_share(share_id):
            logger.error("Share not found: %s", share_id)
            raise HTTPException(status_code=404, detail="Share not found")
        return {"status": "success"}
