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

Phase status (docs/plans/music-library.md): P0-3 landed the auth layer plus the
handful of calls the end-to-end smoke test exercises (ping, scan trigger/status,
get_random_songs / search3, the bit-perfect stream URL). P1-5 adds the full
browse surface consumed by /api/music-library/* — getArtists/getArtist/getAlbum/
getAlbumList2/getGenres/getSongsByGenre, getPlaylists/getPlaylist, star/unstar,
and getCoverArt bytes for the cover proxy. Playback wiring in source.py is P1-6.
P3-11 adds the playlist write verbs (createPlaylist/updatePlaylist/deletePlaylist).
"""
import asyncio
import hashlib
import logging
import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import aiohttp

from backend.config.constants import NAVIDROME_CRED_FILE, NAVIDROME_URL
from backend.shared.network import is_network_error

# Subsonic protocol version we negotiate. Navidrome implements 1.16.1; token
# auth (s/t params) requires >= 1.13.0.
SUBSONIC_API_VERSION = "1.16.1"
# Client identifier sent as `c=` — surfaces in Navidrome's "players" list.
SUBSONIC_CLIENT_NAME = "milo"

# Valid `type` values for getAlbumList2. Routes validate against this before
# calling so an unknown type is a 400, not a silent empty list from Navidrome.
# `byGenre` needs `genre`; `byYear` needs `fromYear`/`toYear`.
ALBUM_LIST_TYPES = frozenset(
    {
        "random",
        "newest",
        "highest",
        "frequent",
        "recent",
        "alphabeticalByName",
        "alphabeticalByArtist",
        "starred",
        "byYear",
        "byGenre",
    }
)


class NavidromeAuthError(Exception):
    """Raised when Navidrome rejects our credentials (Subsonic error 40/41)."""


def _encode_query(query: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Flatten a Subsonic param dict to aiohttp's list-of-pairs form.

    None values are dropped; a list/tuple value expands to a repeated key
    (``songId=a&songId=b``) — Subsonic's convention for the multi-valued params
    used by createPlaylist (``songId``) and updatePlaylist (``songIdToAdd``).
    """
    pairs: List[Tuple[str, str]] = []
    for key, value in query.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            pairs.extend((key, str(item)) for item in value if item is not None)
        else:
            pairs.append((key, str(value)))
    return pairs


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
                params=_encode_query(query),
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

    # === Catalog browse ===

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

    async def get_artists(self) -> List[Dict[str, Any]]:
        """All artists as A–Z index buckets (Subsonic ``getArtists``).

        Returns the ``artists.index`` list — ``[{"name": "A", "artist": [...]},
        ...]`` — preserving the alphabetical grouping the Artists view renders.
        """
        response = await self._make_request("getArtists")
        if not response or response.get("_network_error"):
            return []
        return response.get("artists", {}).get("index", []) or []

    async def get_artist(self, artist_id: str) -> Optional[Dict[str, Any]]:
        """A single artist with its albums (Subsonic ``getArtist``)."""
        response = await self._make_request("getArtist", {"id": artist_id})
        if not response or response.get("_network_error"):
            return None
        return response.get("artist")

    async def get_album(self, album_id: str) -> Optional[Dict[str, Any]]:
        """A single album with its ordered songs (Subsonic ``getAlbum``)."""
        response = await self._make_request("getAlbum", {"id": album_id})
        if not response or response.get("_network_error"):
            return None
        return response.get("album")

    async def get_album_list(
        self,
        list_type: str = "alphabeticalByName",
        size: int = 50,
        offset: int = 0,
        genre: Optional[str] = None,
        from_year: Optional[int] = None,
        to_year: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """A page of albums (Subsonic ``getAlbumList2``).

        ``list_type`` is one of :data:`ALBUM_LIST_TYPES`. ``genre`` is required
        for ``byGenre``; ``from_year``/``to_year`` for ``byYear``. Callers page
        with ``size``/``offset``.
        """
        response = await self._make_request(
            "getAlbumList2",
            {
                "type": list_type,
                "size": size,
                "offset": offset,
                "genre": genre,
                "fromYear": from_year,
                "toYear": to_year,
            },
        )
        if not response or response.get("_network_error"):
            return []
        return response.get("albumList2", {}).get("album", []) or []

    async def get_genres(self) -> List[Dict[str, Any]]:
        """All genres with song/album counts (Subsonic ``getGenres``)."""
        response = await self._make_request("getGenres")
        if not response or response.get("_network_error"):
            return []
        return response.get("genres", {}).get("genre", []) or []

    async def get_songs_by_genre(
        self, genre: str, count: int = 100, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Songs tagged with ``genre`` (Subsonic ``getSongsByGenre``)."""
        response = await self._make_request(
            "getSongsByGenre",
            {"genre": genre, "count": count, "offset": offset},
        )
        if not response or response.get("_network_error"):
            return []
        return response.get("songsByGenre", {}).get("song", []) or []

    # === Playlists ===

    async def get_playlists(self) -> List[Dict[str, Any]]:
        """All playlists, without their entries (Subsonic ``getPlaylists``)."""
        response = await self._make_request("getPlaylists")
        if not response or response.get("_network_error"):
            return []
        return response.get("playlists", {}).get("playlist", []) or []

    async def get_playlist(self, playlist_id: str) -> Optional[Dict[str, Any]]:
        """A single playlist with its ordered entries (Subsonic ``getPlaylist``)."""
        response = await self._make_request("getPlaylist", {"id": playlist_id})
        if not response or response.get("_network_error"):
            return None
        return response.get("playlist")

    async def create_playlist(
        self, name: str, song_ids: Optional[List[str]] = None
    ) -> Optional[Dict[str, Any]]:
        """Create a playlist (Subsonic ``createPlaylist``), optionally seeded with
        an ordered set of songs. Navidrome echoes the created playlist back, which
        is returned (with its generated id); None on error.
        """
        response = await self._make_request(
            "createPlaylist", {"name": name, "songId": song_ids}
        )
        if not response or response.get("_network_error"):
            return None
        return response.get("playlist")

    async def update_playlist(
        self,
        playlist_id: str,
        name: Optional[str] = None,
        song_ids_to_add: Optional[List[str]] = None,
    ) -> bool:
        """Rename a playlist and/or append tracks (Subsonic ``updatePlaylist``).

        This is the surgical edit path — a rename or an append that doesn't need
        the current track list. Reordering/removal go through
        :meth:`set_playlist_tracks` (which rewrites the whole ordered list).
        """
        params: Dict[str, Any] = {"playlistId": playlist_id}
        if name is not None:
            params["name"] = name
        if song_ids_to_add:
            params["songIdToAdd"] = song_ids_to_add
        response = await self._make_request("updatePlaylist", params)
        return bool(response) and not response.get("_network_error")

    async def set_playlist_tracks(
        self, playlist_id: str, song_ids: List[str]
    ) -> bool:
        """Replace a playlist's entire ordered track list.

        Subsonic has no reorder verb, so ``createPlaylist`` with an existing
        ``playlistId`` is the canonical way to rewrite the order (Navidrome
        replaces the tracks and keeps the name). Used for both reordering and
        removing tracks — the caller passes the full new order (``[]`` clears it).
        """
        response = await self._make_request(
            "createPlaylist", {"playlistId": playlist_id, "songId": song_ids}
        )
        return bool(response) and not response.get("_network_error")

    async def delete_playlist(self, playlist_id: str) -> bool:
        """Delete a playlist (Subsonic ``deletePlaylist``)."""
        response = await self._make_request("deletePlaylist", {"id": playlist_id})
        return bool(response) and not response.get("_network_error")

    # === Favorites (star) ===

    async def star(self, item_id: str, kind: str = "song") -> bool:
        """Star a song/album/artist (Subsonic ``star``). ``kind`` picks the id
        param Subsonic expects (``id``/``albumId``/``artistId``)."""
        return await self._set_star("star", item_id, kind)

    async def unstar(self, item_id: str, kind: str = "song") -> bool:
        """Remove a star set by :meth:`star` (Subsonic ``unstar``)."""
        return await self._set_star("unstar", item_id, kind)

    async def _set_star(self, endpoint: str, item_id: str, kind: str) -> bool:
        param = {"song": "id", "album": "albumId", "artist": "artistId"}.get(
            kind, "id"
        )
        response = await self._make_request(endpoint, {param: item_id})
        return bool(response) and not response.get("_network_error")

    # === Playback / media URLs ===

    def stream_url(self, song_id: str) -> str:
        """Authenticated bit-perfect stream URL for ``song_id``.

        ``format=raw`` makes Navidrome serve the original bytes with no
        transcode (FLAC/hi-res pass untouched to CamillaDSP). mpv loads this URL
        directly, so the auth params are embedded in the query string.
        """
        return self._build_url("stream", {"id": song_id, "format": "raw"})

    def cover_art_url(self, cover_id: str, size: Optional[int] = None) -> str:
        """Authenticated getCoverArt URL (proxied behind /api/music-library)."""
        return self._build_url("getCoverArt", {"id": cover_id, "size": size})

    async def get_cover_art(
        self, cover_id: str, size: Optional[int] = None
    ) -> Optional[Tuple[bytes, str]]:
        """Fetch cover-art bytes + content-type for the /api/music-library/cover
        proxy (the frontend never reaches Navidrome directly).

        Returns ``(data, content_type)`` on success, None on error. Subsonic
        replies with a JSON error body instead of an image when the id is
        unknown, so a JSON/text content-type is treated as a miss.
        """
        await self._ensure_session()
        query = {**self._auth_params(), "id": cover_id, "size": size}
        try:
            async with self._session.get(
                f"{self._base_url}/rest/getCoverArt",
                params={k: v for k, v in query.items() if v is not None},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    self.logger.error(
                        f"Navidrome getCoverArt HTTP {resp.status} for {cover_id}"
                    )
                    return None
                content_type = resp.headers.get("Content-Type", "image/jpeg")
                if content_type.startswith(("application/json", "text/")):
                    self.logger.error(
                        f"Navidrome getCoverArt error for {cover_id}: {content_type}"
                    )
                    return None
                return await resp.read(), content_type
        except Exception as exc:
            if is_network_error(exc):
                self.logger.info(f"Navidrome not reachable for cover art: {exc}")
            else:
                self.logger.error(f"Navidrome getCoverArt error for {cover_id}: {exc}")
            return None
