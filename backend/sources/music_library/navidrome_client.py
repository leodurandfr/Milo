# backend/sources/music_library/navidrome_client.py
"""Async Subsonic API client for the Navidrome catalog engine.

Navidrome (milo-navidrome.service) indexes the mount root and exposes a
localhost Subsonic API. This client is the music_library source's window onto
that catalog — the analog of PodcastIndexAPI for the Podcast source, but the
"server" is our own localhost sidecar instead of a public REST service.

Auth is Subsonic token auth (`t = md5(password + salt)`, fresh salt per call),
using the single service account provisioned on first boot by
milo-navidrome-provision and stored in a milo-owned 0600 cred file
(NAVIDROME_CRED_FILE). Credentials never touch settings.json or WS payloads.

Phase status (docs/plans/music-library.md): P0-3 lands the auth layer plus the
handful of calls the end-to-end smoke test exercises — ping, scan trigger/status,
a way to pull a playable song id (get_random_songs / search3), and the
bit-perfect stream URL builder. The full browse surface (getArtists/getAlbum/
getGenres/getPlaylists/star/getCoverArt paging) is layered on in P1-5; playback
wiring in source.py in P1-6.
"""
import asyncio
import hashlib
import logging
import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import aiohttp

from backend.config.constants import NAVIDROME_CRED_FILE, NAVIDROME_URL
from backend.shared.network import is_network_error

# Subsonic protocol version we negotiate. Navidrome implements 1.16.1; token
# auth (s/t params) requires >= 1.13.0.
SUBSONIC_API_VERSION = "1.16.1"
# Client identifier sent as `c=` — surfaces in Navidrome's "players" list.
SUBSONIC_CLIENT_NAME = "milo"


class NavidromeAuthError(Exception):
    """Raised when Navidrome rejects our credentials (Subsonic error 40/41)."""


def load_navidrome_credentials(
    cred_file: Path = NAVIDROME_CRED_FILE,
) -> Optional[Dict[str, str]]:
    """Read the first-boot-provisioned `key=value` cred file.

    Returns ``{"username": ..., "password": ...}`` or None when the file is
    absent/unreadable/incomplete (fail open — the daemon may not have finished
    provisioning yet; the caller logs and retries).
    """
    try:
        creds: Dict[str, str] = {}
        for line in cred_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            creds[key.strip()] = value.strip()
        if creds.get("username") and creds.get("password"):
            return {"username": creds["username"], "password": creds["password"]}
        return None
    except OSError:
        return None


class NavidromeClient:
    """Async client for Navidrome's Subsonic REST API (localhost only).

    IDs are opaque Subsonic ids (songs/albums/artists/playlists/coverArt). One
    long-lived aiohttp session; the caller owns its lifecycle via ``close()``.
    """

    def __init__(
        self,
        username: str,
        password: str,
        base_url: str = NAVIDROME_URL,
    ):
        self.logger = logging.getLogger("source.music_library.navidrome")
        self._username = username
        self._password = password
        self._base_url = base_url.rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None

    @classmethod
    def from_cred_file(
        cls,
        cred_file: Path = NAVIDROME_CRED_FILE,
        base_url: str = NAVIDROME_URL,
    ) -> Optional["NavidromeClient"]:
        """Build a client from the provisioned cred file, or None if unavailable."""
        creds = load_navidrome_credentials(cred_file)
        if not creds:
            return None
        return cls(creds["username"], creds["password"], base_url=base_url)

    async def _ensure_session(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers={"User-Agent": "Milo/1.0"})

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    # === Auth ===

    def _auth_params(self) -> Dict[str, str]:
        """Subsonic token-auth params with a fresh per-call salt."""
        salt = secrets.token_hex(8)
        token = hashlib.md5(f"{self._password}{salt}".encode("utf-8")).hexdigest()
        return {
            "u": self._username,
            "t": token,
            "s": salt,
            "v": SUBSONIC_API_VERSION,
            "c": SUBSONIC_CLIENT_NAME,
        }

    def _build_url(self, endpoint: str, params: Dict[str, Any]) -> str:
        """Full authenticated `/rest/{endpoint}` URL (used for binary endpoints
        like stream/getCoverArt that a client such as mpv fetches directly)."""
        query = {**self._auth_params(), **{k: v for k, v in params.items() if v is not None}}
        return f"{self._base_url}/rest/{endpoint}?{urlencode(query)}"

    async def _make_request(
        self, endpoint: str, params: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """GET a Subsonic JSON endpoint and unwrap the ``subsonic-response``.

        Returns the response object on ``status == "ok"``; the
        ``{"_network_error": True}`` sentinel on transient connectivity failures
        (Navidrome still starting up); None on HTTP/API errors. Raises
        NavidromeAuthError on credential rejection so the caller can re-read the
        cred file rather than silently degrade.
        """
        await self._ensure_session()
        query = {**self._auth_params(), "f": "json", **(params or {})}

        try:
            async with self._session.get(
                f"{self._base_url}/rest/{endpoint}",
                params={k: v for k, v in query.items() if v is not None},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    self.logger.error(
                        f"Navidrome HTTP {resp.status} on {endpoint}: {body[:300]}"
                    )
                    return None

                payload = await resp.json()
                response = payload.get("subsonic-response", {})

                if response.get("status") == "ok":
                    return response

                error = response.get("error", {})
                code = error.get("code")
                message = error.get("message", "unknown")
                # 40 = wrong credentials, 41 = token auth not supported for user.
                if code in (40, 41):
                    raise NavidromeAuthError(f"Navidrome auth rejected: {message}")
                self.logger.error(
                    f"Navidrome API error on {endpoint}: code={code} {message}"
                )
                return None

        except NavidromeAuthError:
            raise
        except Exception as exc:
            if is_network_error(exc):
                self.logger.info(f"Navidrome not reachable yet: {exc}")
                return {"_network_error": True}
            self.logger.error(f"Navidrome unexpected error on {endpoint}: {exc}")
            return None

    # === Health / scan ===

    async def ping(self) -> bool:
        """True if Navidrome is up and our credentials authenticate."""
        response = await self._make_request("ping")
        return bool(response) and not response.get("_network_error")

    async def start_scan(self, full: bool = False) -> bool:
        """Trigger a library scan (quick by default; ``full`` re-reads all tags)."""
        response = await self._make_request(
            "startScan", {"fullScan": "true" if full else "false"}
        )
        return bool(response) and not response.get("_network_error")

    async def get_scan_status(self) -> Optional[Dict[str, Any]]:
        """Return ``{scanning, count, folderCount}`` or None on error.

        ``count`` is the number of tracks indexed so far — surfaced over WS as
        scan progress in later phases.
        """
        response = await self._make_request("getScanStatus")
        if not response or response.get("_network_error"):
            return None
        return response.get("scanStatus")

    async def wait_until_indexed(
        self, min_songs: int = 1, timeout: float = 60.0, poll_interval: float = 1.0
    ) -> bool:
        """Poll getScanStatus until the library holds >= ``min_songs`` and the
        scan is idle, or ``timeout`` elapses. Returns True once satisfied.

        Used after seeding/mounting to gate playback on a ready catalog.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            status = await self.get_scan_status()
            if status is not None:
                count = int(status.get("count", 0) or 0)
                scanning = bool(status.get("scanning", False))
                if count >= min_songs and not scanning:
                    return True
            await asyncio.sleep(poll_interval)
        return False

    # === Catalog (minimal P0-3 surface; full browse in P1-5) ===

    async def get_random_songs(
        self, size: int = 10, genre: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Return up to ``size`` random songs (raw Subsonic song dicts)."""
        response = await self._make_request(
            "getRandomSongs", {"size": size, "genre": genre}
        )
        if not response or response.get("_network_error"):
            return []
        return response.get("randomSongs", {}).get("song", []) or []

    async def search3(
        self,
        query: str,
        song_count: int = 20,
        album_count: int = 20,
        artist_count: int = 20,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Fuzzy search across artists/albums/songs (Subsonic ``search3``)."""
        response = await self._make_request(
            "search3",
            {
                "query": query,
                "songCount": song_count,
                "albumCount": album_count,
                "artistCount": artist_count,
            },
        )
        if not response or response.get("_network_error"):
            return {"artist": [], "album": [], "song": []}
        result = response.get("searchResult3", {})
        return {
            "artist": result.get("artist", []) or [],
            "album": result.get("album", []) or [],
            "song": result.get("song", []) or [],
        }

    # === Playback / media URLs ===

    def stream_url(self, song_id: str) -> str:
        """Authenticated bit-perfect stream URL for ``song_id``.

        ``format=raw`` makes Navidrome serve the original bytes with no
        transcode (FLAC/hi-res pass untouched to CamillaDSP). mpv loads this URL
        directly, so the auth params are embedded in the query string.
        """
        return self._build_url("stream", {"id": song_id, "format": "raw"})

    def cover_art_url(self, cover_id: str, size: Optional[int] = None) -> str:
        """Authenticated getCoverArt URL (proxied behind /api/music-library in P1-5)."""
        return self._build_url("getCoverArt", {"id": cover_id, "size": size})
