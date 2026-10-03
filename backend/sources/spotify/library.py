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
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger("source.spotify.library")

# Spotify's own profile service, the one the apps read a profile from. The Web
# API's /v1/me answers the daemon's token with 429 (measured 2026-10-03).
PROFILE_URL = "https://spclient.wg.spotify.com/user-profile-view/v3/profile/{username}"
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

    def open(self, api_url: str) -> None:
        self._api_url = api_url
        if self._http is None:
            self._http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10.0))

    async def close(self) -> None:
        http, self._http = self._http, None
        if http is not None:
            await http.close()

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

    async def context(self, uri: str) -> Dict[str, Any]:
        """A context's listing, waiting about CONTEXT_WAIT_S for it to be
        complete: `ready` with every track, or the progress so far. A listing
        whose cache stops short of its length (a track Spotify will not
        describe) is complete once its count stops moving: what is cached is
        all there will be."""
        polls = int(self.CONTEXT_WAIT_S / self.CONTEXT_POLL_S)
        cached, still = None, 0
        for poll in range(polls):
            listing = await self._request("GET", "/context/tracks", params={"uri": uri})
            if listing.get("ready"):
                if listing.get("cached") == listing.get("length"):
                    return listing
                still = still + 1 if listing.get("cached") == cached else 0
                if still >= self.STALLED_POLLS:
                    return listing
            cached = listing.get("cached")
            if poll < polls - 1:
                await asyncio.sleep(self.CONTEXT_POLL_S)
        return {**listing, "ready": False}

    async def liked(self, uris: List[str]) -> List[Dict[str, Any]]:
        answer = await self._request("GET", "/library/liked", params={"uris": ",".join(uris)})
        return answer.get("items") or []

    async def set_liked(self, uris: List[str], liked: bool) -> None:
        await self._request("POST", "/library/liked", json={"uris": uris, "liked": liked})

    async def fetch_profile(self, username: str) -> Optional[Dict[str, Optional[str]]]:
        """The account's display name, picture and color from Spotify, with
        the signed-in session's token — so only for the account signed in now.
        None when anything fails: the profile then shows its username."""
        try:
            token = (await self._request("POST", "/token") or {}).get("token")
            if not token:
                return None
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5.0)) as http:
                async with http.get(
                    PROFILE_URL.format(username=username),
                    params={"playlist_limit": 0, "artist_limit": 0, "episode_limit": 0},
                    headers={"Authorization": f"Bearer {token}"},
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"Spotify profile service answered {resp.status}")
                        return None
                    profile = await resp.json(content_type=None)
        except (SpotifyUnavailable, SpotifyLibraryError, aiohttp.ClientError,
                asyncio.TimeoutError, ValueError) as e:
            logger.warning(f"Spotify profile unavailable: {type(e).__name__}")
            return None
        return {
            "name": profile.get("name") or None,
            "image_url": profile.get("image_url") or None,
            "color": _color(profile.get("color")),
        }


def _color(value: Any) -> Optional[str]:
    """The profile's color, an RGB integer, as #rrggbb."""
    if isinstance(value, int) and 0 <= value <= 0xFFFFFF:
        return f"#{value:06x}"
    return None
