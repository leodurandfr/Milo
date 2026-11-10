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
from typing import Annotated, Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from fastapi.responses import RedirectResponse

from backend.api.source_dependency import make_source_dependency
from backend.sources.spotify.catalog import (
    SEARCH_TYPES, artist_page, artist_shelf_seeds, head_artist_shelves, home_shelves, is_mine,
    library_sections, liked_songs_uri, normalize_playlist, search_results,
)
from backend.sources.spotify.library import SpotifyLibraryError, SpotifyNoSession, SpotifyUnavailable
from backend.sources.spotify.models import ITEM_URI, ActiveProfileRequest, AddTracksRequest
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


@contextlib.asynccontextmanager
async def _write_errors(context: str):
    """A write refused for a reason the browser says keeps its status: 403
    (not the account's to edit), 404 (the playlist is gone, or no longer
    holds the item), 409 (it changed meanwhile: list it again). Any other
    refusal is a failure, as for a read."""
    async with _library_errors(context):
        try:
            yield
        except SpotifyLibraryError as exc:
            if exc.status not in (403, 404, 409):
                raise
            logger.warning(f"{context}: {exc}")
            raise HTTPException(status_code=exc.status, detail=str(exc))


@contextlib.asynccontextmanager
async def _search_errors(context: str):
    """A search the daemon cannot run (404: a go-librespot built without
    search, or with it off) answers 501, which the browser reads as "search is
    not available"; Spotify rate-limiting it answers 429. Any other refusal is
    a failure, as for a read."""
    async with _library_errors(context):
        try:
            yield
        except SpotifyLibraryError as exc:
            if exc.status == 404:
                logger.warning(f"{context}: go-librespot does not search ({exc})")
                raise HTTPException(status_code=501, detail="This go-librespot does not search")
            if exc.status == 429:
                logger.warning(f"{context}: Spotify rate-limited the search")
                raise HTTPException(status_code=429, detail="Spotify rate-limited the search")
            raise


def _signed_in(source: SpotifySource, context: str) -> str:
    """The account signed in, or 409: the state the browser draws as "signing in"."""
    if source.account is None:
        logger.warning(f"{context}: nobody is signed in")
        raise HTTPException(status_code=409, detail="Spotify is not signed in")
    return source.account


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
        shortcuts, shelves = home_shelves(await source.library.home(locale))
        seeds = artist_shelf_seeds(shelves)
        return shortcuts, head_artist_shelves(shelves, seeds, await source.library.artist_summaries(seeds.values()))
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


# How many results of each kind the search page lists, and how many completed
# queries it offers.
SEARCH_LIMIT = 10
SUGGESTIONS_LIMIT = 5
# What is searched: 1 to 200 characters, not all blank (go-librespot's bounds).
SearchQuery = Annotated[str, Query(min_length=1, max_length=200, pattern=r"\S",
                                   description="What to search for")]


@router.get("/search")
async def search(q: SearchQuery, source: SpotifySource = Depends(get_source)):
    """Spotify's catalog searched for `q`: the first tracks, artists, albums
    and playlists, each kind asked at once. A kind that failed this time (a
    refusal, a rate limit on that one request, a request that did not come
    back, an answer that did not read) is left empty and named in
    `incomplete`, the others shown: Spotify fails one operation in well over
    a thousand (measured), so the page is not lost to it. No session and a
    daemon that cannot search hold for every kind and are answered as such.
    Every kind failing the same way is answered as that way — a rate limit,
    or the daemon unreachable (a restart: a profile switch), which the page
    draws as signing in, with a retry; any other mix is a failure."""
    _signed_in(source, "Spotify search")
    query = q.strip()
    async with _search_errors("Spotify search"):
        # All awaited to the end: a failure of one must not leave the others
        # running with nobody to collect them.
        pages = await asyncio.gather(
            *(source.library.search(kind, query, SEARCH_LIMIT) for kind in SEARCH_TYPES),
            return_exceptions=True,
        )
        failures = [page for page in pages if isinstance(page, BaseException)]
        for failure in failures:
            # What holds for every kind is answered as such; a cancellation
            # is not a failure of the search at all.
            no_search = isinstance(failure, SpotifyLibraryError) and failure.status == 404
            if no_search or isinstance(failure, SpotifyNoSession) or not isinstance(failure, Exception):
                raise failure
        if len(failures) == len(pages):
            rate_limited = all(isinstance(f, SpotifyLibraryError) and f.status == 429 for f in failures)
            if rate_limited or all(isinstance(f, SpotifyUnavailable) for f in failures):
                raise failures[0]
            logger.error(f"Spotify search: every kind failed ({failures[0]!r})")
            raise HTTPException(status_code=503, detail="Spotify failed the search")
        found = {}
        incomplete = []
        for kind, page in zip(SEARCH_TYPES, pages):
            if isinstance(page, BaseException):
                logger.warning(f"Spotify search: the {kind}s are left out this time ({page!r})")
                incomplete.append(kind)
                page = {}
            found[kind] = page
        return {"status": "success", **search_results(query, found), "incomplete": incomplete}


@router.get("/search/suggestions")
async def search_suggestions(q: SearchQuery, source: SpotifySource = Depends(get_source)):
    """The queries Spotify completes `q` into, as it is typed."""
    _signed_in(source, "Spotify search suggestions")
    async with _search_errors("Spotify search suggestions"):
        answer = await source.library.suggestions(q.strip(), SUGGESTIONS_LIMIT)
        return {"status": "success", "query": answer.get("query") or q.strip(),
                "completions": answer.get("completions") or []}


@router.get("/history")
async def get_history(source: SpotifySource = Depends(get_source)):
    """The tracks Milō played for the signed-in account, newest first, each
    with the context it played in: the queue page's recently played tab. Kept
    by Milō, as each Spotify app keeps its own player's, so it answers while
    nothing plays."""
    history, account = source.history, source.account
    return {"status": "success", "tracks": history.tracks(account) if history and account else []}


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
    if source.account is None:
        logger.warning("Spotify listing: nobody is signed in")
        raise HTTPException(status_code=409, detail="Spotify is not signed in")
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
        view, releases, latest_type, listeners = await source.library.artist(uri, locale)
    return {"status": "success", "uri": uri, **artist_page(view, releases, latest_type, listeners)}


@router.get("/contexts/{uri}/cover")
async def get_context_cover(uri: str, source: SpotifySource = Depends(get_source)) -> Response:
    """The picture of a playlist that has none in the library: a redirect to
    the mosaic of its first albums, as the Spotify apps draw it. 404 for an
    empty playlist, which the browser draws as its placeholder."""
    if source.account is None:
        logger.warning("Spotify cover: nobody is signed in")
        raise HTTPException(status_code=409, detail="Spotify is not signed in")
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


# === The account's playlists, written ===

# A playlist the browser writes to: one of the account's, or its Liked Songs.
WRITABLE_URI = r"^spotify:(playlist:[A-Za-z0-9]{22}|user:[^:]+:collection)$"
# How many playlists the picker asks about at once: go-librespot reads each
# one whole from Spotify on every call.
CONTAINS_AT_ONCE = 6


@router.get("/tracks/{uri}/playlists")
async def get_track_playlists(
    uri: Annotated[str, Path(pattern=ITEM_URI)],
    source: SpotifySource = Depends(get_source),
):
    """The "Add to playlist" picker: the playlists the account may edit, in
    library order (an empty one included: it is the one being filled), each
    saying whether it holds the item already, read fresh. `liked` is whether
    the item is in Liked Songs, null for an episode (the collection holds
    tracks only) or where the read failed; a playlist's `contains` is null
    where its read failed. Each opening reads every editable playlist whole
    from Spotify (23 on the owner's account, the longest 503 tracks)."""
    account = _signed_in(source, "Spotify playlist picker")
    async with _library_errors("Spotify playlist picker"):
        playlists = [
            playlist for playlist in map(normalize_playlist, await source.library.playlists())
            if is_mine(playlist, account)
        ]
        gate = asyncio.Semaphore(CONTAINS_AT_ONCE)

        async def holds(playlist_uri: str):
            async with gate:
                return await source.library.playlist_contains(playlist_uri, uri)

        answers = await asyncio.gather(*(holds(p["uri"]) for p in playlists), return_exceptions=True)
        for answer in answers:
            if isinstance(answer, SpotifyUnavailable):
                raise answer
            if isinstance(answer, SpotifyLibraryError):
                logger.warning(f"Spotify playlist picker: {answer}")
            elif isinstance(answer, BaseException):
                raise answer
        liked = None
        if uri.startswith("spotify:track:"):
            try:
                liked = await source.library.liked(uri)
            except SpotifyLibraryError as exc:
                # The row it costs, never the picker: the playlists are read.
                logger.warning(f"Spotify playlist picker: {exc}")
    return {
        "status": "success",
        "liked_songs_uri": liked_songs_uri(account),
        "liked": liked,
        "playlists": [
            {"uri": p["uri"], "name": p["name"], "image": p["image"],
             "contains": answer if isinstance(answer, bool) else None}
            for p, answer in zip(playlists, answers)
        ],
    }


def _liked_songs(uri: str, account: str, items, context: str) -> bool:
    """Whether the write is to Liked Songs, which holds tracks only (422)."""
    if uri != liked_songs_uri(account):
        if not uri.startswith("spotify:playlist:"):
            logger.error(f"{context}: another account's Liked Songs ({uri})")
            raise HTTPException(status_code=422, detail="Not this account's Liked Songs")
        return False
    if any(not item.startswith("spotify:track:") for item in items):
        logger.error(f"{context}: Liked Songs holds tracks only")
        raise HTTPException(status_code=422, detail="Liked Songs holds tracks only")
    return True


@router.post("/contexts/{uri}/tracks")
async def add_tracks(
    uri: Annotated[str, Path(pattern=WRITABLE_URI)],
    payload: AddTracksRequest,
    source: SpotifySource = Depends(get_source),
):
    """Add items to the end of a playlist the account may edit, or to Liked
    Songs. A playlist may hold an item twice: the picker says when it does."""
    context = "Spotify add to playlist"
    account = _signed_in(source, context)
    liked = _liked_songs(uri, account, payload.uris, context)
    async with _write_errors(context):
        if liked:
            await source.library.set_liked(payload.uris, True)
        else:
            await source.library.add_to_playlist(uri, payload.uris)
    return {"status": "success"}


@router.delete("/contexts/{uri}/tracks/{item}")
async def remove_track(
    uri: Annotated[str, Path(pattern=WRITABLE_URI)],
    item: Annotated[str, Path(pattern=ITEM_URI)],
    position: Annotated[Optional[int], Query(
        ge=0, description="The entry's place in the listing; without it, the first copy"
    )] = None,
    source: SpotifySource = Depends(get_source),
):
    """Remove one entry from a playlist the account may edit — the one at
    `position` as the listing gave it, which must still be `item` (409: list
    it again) — or the item from Liked Songs."""
    context = "Spotify remove from playlist"
    account = _signed_in(source, context)
    liked = _liked_songs(uri, account, [item], context)
    async with _write_errors(context):
        if liked:
            await source.library.set_liked([item], False)
        else:
            await source.library.remove_from_playlist(uri, item, position)
    return {"status": "success"}


# === Profiles ===

@router.get("/profiles")
async def get_profiles(source: SpotifySource = Depends(get_source)):
    """The accounts Milō keeps — never their credentials."""
    profiles = _require_profiles(source)
    return {"status": "success", "profiles": profiles.list()}


def _profile_changed(result: Dict[str, Any], context: str) -> Dict[str, str]:
    """A profile no longer kept is the browser's stale list (404); anything
    else is a change that did not happen (503)."""
    if result.get("success"):
        return {"status": "success"}
    if result.get("unknown"):
        logger.debug(f"{context}: unknown profile")
        raise HTTPException(status_code=404, detail=result["error"])
    logger.error(f"{context} failed: {result.get('error')}")
    raise HTTPException(status_code=503, detail=result.get("error"))


@router.delete("/profiles/{username}")
async def forget_profile(username: str, source: SpotifySource = Depends(get_source)):
    _require_profiles(source)
    return _profile_changed(await source.forget_profile(username), "Spotify profile forget")


@router.put("/active-profile")
async def set_active_profile(payload: ActiveProfileRequest, source: SpotifySource = Depends(get_source)):
    """Sign go-librespot in as a kept profile; ends what plays."""
    _require_profiles(source)
    return _profile_changed(await source.switch_profile(payload.username), "Spotify profile switch")
