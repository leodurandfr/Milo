# backend/sources/spotify/library.py
"""
go-librespot's library API: everything the browser reads that is not playback.

Open while the source runs (the daemon's API only answers then), closed by the
source's cleanup. The account is whichever the daemon is signed in as; with
nobody signed in — or for the 0.25 s between a session end and the stored
account signing back in (measured) — every call answers 204, raised here as
SpotifyUnavailable for the browser to retry.
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from backend.sources.spotify.catalog import MOSAIC_TILES, described_tracks, leading_covers, playlist_cover

logger = logging.getLogger("source.spotify.library")

# Spotify's own profile service, the one the apps read a profile from. The Web
# API's /v1/me answers the daemon's token with 429 (measured 2026-10-03).
PROFILE_URL = "https://spclient.wg.spotify.com/user-profile-view/v3/profile/{username}"
# Spotify's home, as its apps draw it, for the account whose token asks.
HOME_URL = "https://spclient.wg.spotify.com/homeview/v1/home"
PAGE = 100


class SpotifyUnavailable(Exception):
    """The daemon is not running, or nobody is signed in to it right now."""


class SpotifyLibraryError(Exception):
    """The daemon answered, and refused."""


class SpotifyLibrary:
    # One /context/tracks call answers at once, with what is cached so far:
    # how long one request waits for the listing to complete before handing
    # the browser its progress (an 800-track listing took 7.7 s, measured).
    CONTEXT_WAIT_S = 6.0
    CONTEXT_POLL_S = 0.4
    STALLED_POLLS = 3

    def __init__(self) -> None:
        self._http: Optional[aiohttp.ClientSession] = None
        self._api_url: Optional[str] = None
        # Spotify's profile service, on the internet: its own session, so its
        # timeout never shortens a call to the local daemon.
        self._internet: Optional[aiohttp.ClientSession] = None

    def open(self, api_url: str) -> None:
        self._api_url = api_url
        if self._http is None:
            self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10.0))
        if self._internet is None:
            self._internet = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5.0))

    async def close(self) -> None:
        for session in (self._http, self._internet):
            if session is not None:
                await session.close()
        self._http = self._internet = None

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        if self._http is None or self._api_url is None:
            raise SpotifyUnavailable("Spotify is not running")
        try:
            async with self._http.request(method, f"{self._api_url}{path}", **kwargs) as resp:
                if resp.status == 204:
                    raise SpotifyUnavailable("Spotify is not signed in")
                if resp.status >= 400:
                    raise SpotifyLibraryError(f"{method} {path.split('?')[0]} answered {resp.status}")
                if resp.content_type == "application/json":
                    return await resp.json()
                return None
        except (aiohttp.ClientConnectionError, asyncio.TimeoutError) as e:
            # Refused, or cut mid-request by a daemon restart (a profile switch).
            raise SpotifyUnavailable("Spotify is not reachable") from e
        except RuntimeError as e:
            # The session closed under the request: the source stopped.
            raise SpotifyUnavailable("Spotify stopped") from e

    async def playlists(self) -> List[Dict[str, Any]]:
        """The whole rootlist, in library order."""
        items: List[Dict[str, Any]] = []
        while True:
            page = await self._request("GET", "/library/playlists", params={"offset": len(items), "limit": PAGE})
            batch = page.get("items") or []
            items.extend(batch)
            if not batch or len(items) >= (page.get("total") or 0):
                return items

    async def context(self, uri: str, after: int = 0) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """A context's tracks as far as they are described, and the listing
        they came from (`ready` once its tracks are enumerated, `complete` once
        nothing more will be described). Answers as soon as more than `after`
        tracks are described, or the listing is complete — a cold Liked Songs
        takes 12 s to describe whole, its first 100 tracks one — else after
        about CONTEXT_WAIT_S with what there is. A listing whose cache stops
        short of its length (a track Spotify will not describe) is complete
        once its count stops moving: what is cached is all there will be.
        Never at 0: a listing rebuilt under load sits ready with nothing
        described for over a second (measured)."""
        polls = int(self.CONTEXT_WAIT_S / self.CONTEXT_POLL_S)
        cached, still = None, 0
        tracks: List[Dict[str, Any]] = []
        for poll in range(polls):
            listing = await self._request("GET", "/context/tracks", params={"uri": uri})
            complete = False
            if listing.get("ready"):
                if listing.get("cached") == listing.get("length"):
                    complete = True
                else:
                    still = still + 1 if listing.get("cached") == cached else 0
                    complete = still >= self.STALLED_POLLS and bool(cached)
                tracks = described_tracks(listing.get("tracks") or [], complete)
                if complete or len(tracks) > after:
                    return tracks, {**listing, "complete": complete}
            cached = listing.get("cached")
            if poll < polls - 1:
                await asyncio.sleep(self.CONTEXT_POLL_S)
        return tracks, {**listing, "complete": False}

    async def cover(self, uri: str) -> Optional[str]:
        """The picture the Spotify apps draw for a playlist that has none: as
        soon as its first four albums are described (go-librespot describes a
        listing front to back, ~1 s for the first ones of 500 tracks,
        measured), else from the whole listing once it is complete, or from
        what leads it when the wait runs out — never from albums past a gap."""
        polls = int(self.CONTEXT_WAIT_S / self.CONTEXT_POLL_S)
        covers: List[str] = []
        cached, still = None, 0
        for poll in range(polls):
            listing = await self._request("GET", "/context/tracks", params={"uri": uri})
            tracks = listing.get("tracks") or []
            covers = leading_covers(tracks)
            if len(covers) == MOSAIC_TILES:
                break
            if listing.get("ready"):
                if listing.get("cached") == listing.get("length"):
                    return playlist_cover(leading_covers(tracks, complete=True))
                still = still + 1 if listing.get("cached") == cached else 0
                if still >= self.STALLED_POLLS and cached:
                    return playlist_cover(leading_covers(tracks, complete=True))
            cached = listing.get("cached")
            if poll < polls - 1:
                await asyncio.sleep(self.CONTEXT_POLL_S)
        return playlist_cover(covers)

    async def liked(self, uris: List[str]) -> List[Dict[str, Any]]:
        answer = await self._request("GET", "/library/liked", params={"uris": ",".join(uris)})
        return answer.get("items") or []

    async def set_liked(self, uris: List[str], liked: bool) -> None:
        await self._request("POST", "/library/liked", json={"uris": uris, "liked": liked})

    async def _spotify_service(self, url: str, params: Dict[str, Any], what: str) -> Any:
        """One of Spotify's own services, with the signed-in session's token:
        only the account signed in now can be asked about."""
        internet = self._internet
        if internet is None or internet.closed:
            raise SpotifyUnavailable("Spotify stopped")
        token = (await self._request("POST", "/token") or {}).get("token")
        if not token:
            raise SpotifyUnavailable("Spotify gave no token")
        try:
            async with internet.get(url, params=params, headers={"Authorization": f"Bearer {token}"}) as resp:
                if resp.status != 200:
                    raise SpotifyLibraryError(f"Spotify's {what} service answered {resp.status}")
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            raise SpotifyLibraryError(f"Spotify's {what} service unreachable: {type(e).__name__}") from e

    async def home(self, locale: str) -> Dict[str, Any]:
        """Spotify's home for the signed-in account, its titles in `locale`
        (a BCP 47 tag; all eight of Milō's languages were measured)."""
        view = await self._spotify_service(HOME_URL, {"platform": "android", "locale": locale}, "home")
        if not isinstance(view, dict):
            raise SpotifyLibraryError("Spotify's home answered no view")
        return view

    async def fetch_profile(self, username: str) -> Optional[Dict[str, Optional[str]]]:
        """What Spotify's profile service says of the account, with the
        signed-in session's token — so only for the account signed in now — as
        the identity fields it answered (`spotify_name`, and `avatar_url` when
        the answer carries it: a field it left out is no statement about it).
        None when anything fails, or when the answer does not name the
        account, which every real one does."""
        try:
            profile = await self._spotify_service(
                PROFILE_URL.format(username=username),
                {"playlist_limit": 0, "artist_limit": 0, "episode_limit": 0},
                "profile",
            )
        except (SpotifyUnavailable, SpotifyLibraryError) as e:
            logger.warning(f"Spotify profile unavailable: {e}")
            return None
        if not isinstance(profile, dict) or not profile.get("name"):
            logger.warning("Spotify profile answer without a name, ignored")
            return None
        identity = {"spotify_name": profile["name"]}
        if "image_url" in profile:
            identity["avatar_url"] = profile["image_url"] or None
        return identity
