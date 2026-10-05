# backend/sources/spotify/routes.py
"""
FastAPI routes for the Spotify browser: the signed-in account's home and
library, the listings it opens (a track's radio among them), and the profiles
Milō keeps.

Playback is not here: `play_context`, shuffle and repeat are commands, through
`POST /api/audio/control/spotify`. These routes answer only while the source
runs (go-librespot's API does); with nobody signed in they answer 409, which
the browser reads as "signing in" or "cast from your phone", never as a fault.
"""
import asyncio
import contextlib
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from fastapi.responses import RedirectResponse

from backend.api.source_dependency import make_source_dependency
from backend.sources.spotify.catalog import artist_page, home_shelves, library_sections, liked_songs_uri
from backend.sources.spotify.library import SpotifyLibraryError, SpotifyUnavailable
from backend.sources.spotify.models import ActiveProfileRequest, RenameProfileRequest
from backend.sources.spotify.source import SpotifySource

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/spotify", tags=["spotify"])

set_source_provider, get_source = make_source_dependency("Spotify")

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

async def _spotify_home(source: SpotifySource, locale: str):
    """Spotify's home, or None when its service failed: the account's own
    playlists are still worth a page."""
    try:
        return home_shelves(await source.library.home(locale))
    except SpotifyLibraryError as exc:
        logger.warning(f"Spotify home: {exc}; only the library is shown")
        return None


# The language Spotify titles its home and an artist's page in.
Locale = Annotated[str, Query(pattern=r"^[A-Za-z]{2,3}([-_][A-Za-z0-9]{2,4})?$",
                              description="The language Spotify titles its sections in")]


@router.get("/home")
async def get_home(
    locale: Locale = "en",
    source: SpotifySource = Depends(get_source),
):
    """Spotify's home for the signed-in account — its shortcuts and its
    shelves, as its apps draw them — then the account's own playlists."""
    account = source.account
    if account is None:
        logger.warning("Spotify home: nobody is signed in")
        raise HTTPException(status_code=409, detail="Spotify is not signed in")
    async with _library_errors("Spotify home"):
        # Both read at once, and both awaited to the end: a failure of one must
        # not leave the other running with nobody to collect its own.
        items, home = await asyncio.gather(
            source.library.playlists(), _spotify_home(source, locale), return_exceptions=True,
        )
        for result in (items, home):
            if isinstance(result, BaseException):
                raise result
        shortcuts, shelves = home or ([], [])
        return {
            "status": "success",
            "account": account,
            "liked_songs_uri": liked_songs_uri(account),
            "shortcuts": shortcuts,
            "shelves": shelves,
            "playlists": library_sections(items, account),
        }


@router.get("/contexts/{uri}")
async def get_context(
    uri: str,
    after: Annotated[int, Query(ge=0, description="How many of its tracks the browser already has")] = 0,
    source: SpotifySource = Depends(get_source),
):
    """A playlist's, an album's, an artist's or Liked Songs' tracks, as
    go-librespot describes them: `tracks` are the ones past the `after` the
    browser has, and it asks again until `complete` (`cached` of `length` say
    how far the listing is). Never fewer than it has: a listing the daemon
    reads again from the start (a restart) answers nothing new."""
    async with _library_errors("Spotify listing"):
        tracks, listing = await source.library.context(uri, after)
        return {
            "status": "success",
            "uri": uri,
            "complete": listing["complete"],
            "cached": listing.get("cached") or 0,
            "length": listing.get("length") or 0,
            "tracks": tracks[after:],
        }


@router.get("/artists/{uri}")
async def get_artist(
    uri: Annotated[str, Path(pattern=r"^spotify:artist:[A-Za-z0-9]{22}$")],
    locale: Locale = "en",
    source: SpotifySource = Depends(get_source),
):
    """An artist's page as Spotify's apps draw it: the header, the discography
    (popular releases, then albums, singles and compilations, each newest
    first), This Is, the artist's radio and the rest. Its popular tracks
    are the first ten of the artist's listing (`/contexts/{uri}`), which the
    browser reads for them."""
    if source.account is None:
        logger.warning("Spotify artist: nobody is signed in")
        raise HTTPException(status_code=409, detail="Spotify is not signed in")
    async with _library_errors("Spotify artist"):
        view, releases, latest_type = await source.library.artist(uri, locale)
    return {"status": "success", "uri": uri, **artist_page(view, releases, latest_type)}


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


@router.get("/tracks/{uri}/radio")
async def get_track_radio(
    uri: Annotated[str, Path(pattern=r"^spotify:track:[A-Za-z0-9]{22}$")],
    source: SpotifySource = Depends(get_source),
):
    """The playlist Spotify makes as this track's radio, which the browser
    opens like any other. A local file has none, and is refused (422)."""
    if source.account is None:
        logger.warning("Spotify track radio: nobody is signed in")
        raise HTTPException(status_code=409, detail="Spotify is not signed in")
    async with _library_errors("Spotify track radio"):
        radio = await source.library.track_radio(uri)
    if radio is None:
        logger.debug("Spotify track radio: Spotify answered none for this track")
        raise HTTPException(status_code=404, detail="No radio for this track")
    return {"status": "success", "uri": radio}


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
