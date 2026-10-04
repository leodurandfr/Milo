# backend/sources/spotify/routes.py
"""
FastAPI routes for the Spotify browser: the signed-in account's library, its
Liked Songs, and the profiles Milō keeps.

Playback is not here: `play_context`, shuffle and repeat are commands, through
`POST /api/audio/control/spotify`. These routes answer only while the source
runs (go-librespot's API does); with nobody signed in they answer 409, which
the browser reads as "signing in" or "cast from your phone", never as a fault.
"""
import contextlib
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import RedirectResponse

from backend.api.source_dependency import make_source_dependency
from backend.sources.spotify.catalog import classify_home, liked_songs_uri, normalize_track
from backend.sources.spotify.library import SpotifyLibraryError, SpotifyUnavailable
from backend.sources.spotify.models import ActiveProfileRequest, RenameProfileRequest
from backend.sources.spotify.source import SpotifySource

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/spotify", tags=["spotify"])

set_source_provider, get_source = make_source_dependency("Spotify")

# go-librespot's /library/liked takes 1 to 50 uris per call.
MAX_LIKED_URIS = 50


def setup_spotify_routes(source_provider) -> APIRouter:
    """Configure routes with source provider."""
    set_source_provider(source_provider)
    return router


@contextlib.asynccontextmanager
async def _library_errors(context: str):
    """Nobody signed in (or the daemon down) is a state the browser shows: 409,
    logged at warning. A daemon that answered and refused is a failure: 503."""
    try:
        yield
    except HTTPException:
        raise
    except SpotifyUnavailable as exc:
        logger.warning(f"{context}: {exc}")
        raise HTTPException(status_code=409, detail=str(exc))
    except SpotifyLibraryError as exc:
        logger.error(f"{context}: {exc}")
        raise HTTPException(status_code=503, detail=str(exc))


def _require_profiles(source: SpotifySource):
    if source.profiles is None:
        logger.error("Spotify profiles requested but none are kept on this unit")
        raise HTTPException(status_code=503, detail="Spotify profiles are not configured")
    return source.profiles


# === Library ===

@router.get("/home")
async def get_home(source: SpotifySource = Depends(get_source)):
    """The signed-in account's playlists, in the home's sections."""
    account = source.account
    if account is None:
        logger.warning("Spotify home: nobody is signed in")
        raise HTTPException(status_code=409, detail="Spotify is not signed in")
    async with _library_errors("Spotify home"):
        items = await source.library.playlists()
        return {
            "status": "success",
            "account": account,
            "liked_songs_uri": liked_songs_uri(account),
            "sections": classify_home(items, account),
        }


@router.get("/contexts/{uri}")
async def get_context(uri: str, source: SpotifySource = Depends(get_source)):
    """A playlist's, an album's, an artist's or Liked Songs' tracks: the whole
    listing once go-librespot has it, its progress until then (the browser asks
    again while `ready` is false)."""
    async with _library_errors("Spotify listing"):
        listing = await source.library.context(uri)
        answer = {
            "status": "success",
            "uri": uri,
            "ready": bool(listing.get("ready")),
            "cached": listing.get("cached") or 0,
            "length": listing.get("length") or 0,
        }
        if answer["ready"]:
            tracks = (normalize_track(entry) for entry in listing.get("tracks") or [])
            answer["tracks"] = [track for track in tracks if track is not None]
        return answer


@router.get("/contexts/{uri}/cover")
async def get_context_cover(uri: str, source: SpotifySource = Depends(get_source)) -> Response:
    """The picture of a playlist that has none in the library: a redirect to
    the mosaic of its first albums, as the Spotify apps draw it. 404 for an
    empty playlist, which the browser draws as its placeholder."""
    async with _library_errors("Spotify cover"):
        cover = await source.library.cover(uri)
    if cover is None:
        logger.debug("Spotify cover: nothing to draw one from")
        return Response(status_code=404)
    return RedirectResponse(cover, status_code=302)


@router.get("/liked-tracks")
async def get_liked_tracks(
    uris: str = Query(..., description="Comma-separated track uris, 1 to 50"),
    source: SpotifySource = Depends(get_source),
):
    """Which of these tracks are in the account's Liked Songs."""
    wanted: List[str] = [uri for uri in uris.split(",") if uri]
    if not wanted or len(wanted) > MAX_LIKED_URIS:
        raise HTTPException(status_code=422, detail=f"1 to {MAX_LIKED_URIS} uris")
    async with _library_errors("Spotify liked tracks"):
        return {"status": "success", "items": await source.library.liked(wanted)}


@router.put("/liked-tracks/{uri}")
async def like_track(uri: str, source: SpotifySource = Depends(get_source)):
    async with _library_errors("Spotify like"):
        await source.library.set_liked([uri], True)
        return {"status": "success"}


@router.delete("/liked-tracks/{uri}")
async def unlike_track(uri: str, source: SpotifySource = Depends(get_source)):
    async with _library_errors("Spotify unlike"):
        await source.library.set_liked([uri], False)
        return {"status": "success"}


# === Profiles ===

@router.get("/profiles")
async def get_profiles(source: SpotifySource = Depends(get_source)):
    """The accounts Milō keeps — never their credentials."""
    profiles = _require_profiles(source)
    return {
        "status": "success",
        "profiles": [
            {**profile, "active": profile["username"] == source.account}
            for profile in profiles.list()
        ],
    }


@router.patch("/profiles/{username}")
async def rename_profile(
    username: str, payload: RenameProfileRequest, source: SpotifySource = Depends(get_source),
):
    if not await _require_profiles(source).rename(username, payload.name):
        logger.debug("Rename of an unknown Spotify profile")
        raise HTTPException(status_code=404, detail="Unknown Spotify profile")
    return {"status": "success"}


@router.delete("/profiles/{username}")
async def forget_profile(username: str, source: SpotifySource = Depends(get_source)):
    _require_profiles(source)
    result = await source.forget_profile(username)
    if not result.get("success"):
        logger.debug("Forget of an unknown Spotify profile")
        raise HTTPException(status_code=404, detail=result.get("error") or "Unknown Spotify profile")
    return {"status": "success"}


@router.put("/active-profile")
async def set_active_profile(payload: ActiveProfileRequest, source: SpotifySource = Depends(get_source)):
    """Sign go-librespot in as a kept profile; ends what plays."""
    _require_profiles(source)
    result = await source.switch_profile(payload.username)
    if not result.get("success"):
        logger.debug("Switch to an unknown Spotify profile")
        raise HTTPException(status_code=404, detail=result.get("error") or "Unknown Spotify profile")
    return {"status": "success"}
