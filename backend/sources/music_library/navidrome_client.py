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

The surface covered: ping, scan trigger/status, the bit-perfect stream URL,
the browse calls /api/music-library/* consumes (getArtists/getArtist/getAlbum/
getAlbumList2/getSongsByGenre, getPlaylists/getPlaylist, search3), star/unstar,
getCoverArt bytes for the cover proxy, and the playlist write verbs
(createPlaylist/updatePlaylist/deletePlaylist).

**Every catalog read is scoped.** Each takes ``music_folder_ids`` — the Navidrome
libraries, i.e. the storage spaces, it may read — as a required argument sent as
a repeated ``musicFolderId``; there is no unscoped call to make. An empty list
therefore means "no library to read" and answers empty, never the whole catalog:
to Subsonic an absent ``musicFolderId`` is *everything*, so the one place that
distinction can be lost is here.
"""
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
        # Signature of Navidrome's built-in "no cover" placeholder, fetched once
        # at runtime (see _ensure_placeholder_signature). None until fetched or
        # if the fetch failed (fail-open: we then never suppress any cover).
        self._placeholder_len: Optional[int] = None
        self._placeholder_sha: Optional[str] = None
        self._placeholder_checked: bool = False
        # Navidrome resizes its "no cover" placeholder too, so a thumbnail request
        # returns bytes that differ from the full-size signature above (this is why
        # an art-less playlist's generic cover slips through at thumb size). We
        # learn the resized placeholder's signature per requested size the first
        # time we confirm one against the authoritative full-size art. size → (len,
        # sha256).
        self._thumb_placeholder_sigs: Dict[int, Tuple[int, str]] = {}

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

    # === Catalog browse ===

    async def search3(
        self,
        query: str,
        music_folder_ids: List[int],
        song_count: int = 20,
        album_count: int = 20,
        artist_count: int = 20,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Fuzzy search across artists/albums/songs (Subsonic ``search3``)."""
        if not music_folder_ids:
            return {"artist": [], "album": [], "song": []}
        response = await self._make_request(
            "search3",
            {
                "query": query,
                "songCount": song_count,
                "albumCount": album_count,
                "artistCount": artist_count,
                "musicFolderId": music_folder_ids,
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

    async def get_artists(self, music_folder_ids: List[int]) -> List[Dict[str, Any]]:
        """All artists as A–Z index buckets (Subsonic ``getArtists``).

        Returns the ``artists.index`` list — ``[{"name": "A", "artist": [...]},
        ...]`` — preserving the alphabetical grouping the Artists view renders.
        """
        if not music_folder_ids:
            return []
        response = await self._make_request(
            "getArtists", {"musicFolderId": music_folder_ids}
        )
        if not response or response.get("_network_error"):
            return []
        return response.get("artists", {}).get("index", []) or []

    async def get_artist(self, artist_id: str) -> Optional[Dict[str, Any]]:
        """A single artist with its albums (Subsonic ``getArtist``).

        Takes no scope, because Subsonic offers none here: the answer spans every
        library the account can read, which is why the route post-filters its
        album list against the storage spaces that are mounted (see routes.py).
        """
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
        music_folder_ids: List[int],
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
        with ``size``/``offset``, and the page spans the storage spaces named by
        ``music_folder_ids`` — the counts add up (155 + 43 = 198, measured).
        """
        if not music_folder_ids:
            return []
        response = await self._make_request(
            "getAlbumList2",
            {
                "type": list_type,
                "size": size,
                "offset": offset,
                "genre": genre,
                "musicFolderId": music_folder_ids,
                "fromYear": from_year,
                "toYear": to_year,
            },
        )
        if not response or response.get("_network_error"):
            return []
        return response.get("albumList2", {}).get("album", []) or []

    async def get_songs_by_genre(
        self,
        genre: str,
        music_folder_ids: List[int],
        count: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Songs tagged with ``genre`` (Subsonic ``getSongsByGenre``)."""
        if not music_folder_ids:
            return []
        response = await self._make_request(
            "getSongsByGenre",
            {
                "genre": genre,
                "count": count,
                "offset": offset,
                "musicFolderId": music_folder_ids,
            },
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

    async def get_starred(
        self, music_folder_ids: List[int]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """All starred items (Subsonic ``getStarred2``, id3 shape).

        Returns the ``starred2`` envelope normalised to
        ``{"song": [...], "album": [...], "artist": [...]}`` — the read side of
        :meth:`star`/:meth:`unstar`, so favourites can actually be enumerated
        (the browse payloads only carry a per-item ``starred`` flag). Navidrome
        does honour the scope here, unlike on getGenres/getPlaylists."""
        if not music_folder_ids:
            return {"song": [], "album": [], "artist": []}
        response = await self._make_request(
            "getStarred2", {"musicFolderId": music_folder_ids}
        )
        if not response or response.get("_network_error"):
            return {"song": [], "album": [], "artist": []}
        starred = response.get("starred2", {}) or {}
        return {
            "song": starred.get("song", []) or [],
            "album": starred.get("album", []) or [],
            "artist": starred.get("artist", []) or [],
        }

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

    async def _ensure_placeholder_signature(self) -> None:
        """Fetch (once) the byte-signature of Navidrome's built-in "no cover"
        placeholder so get_cover_art can tell it apart from real artwork.

        Navidrome serves an identical generic image (HTTP 200, an actual image
        body) for any album that has no embedded/folder art — and, conveniently,
        for an *empty* id too. We fetch that empty-id reference at runtime rather
        than hardcoding a hash, so the check tracks whatever placeholder the
        running Navidrome version ships. Fail-open: on any error the signature
        stays unset and no cover is ever suppressed.
        """
        if self._placeholder_checked:
            return
        self._placeholder_checked = True
        await self._ensure_session()
        try:
            async with self._session.get(
                f"{self._base_url}/rest/getCoverArt",
                params=_encode_query({**self._auth_params(), "id": ""}),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return
                if resp.headers.get("Content-Type", "").startswith(
                    ("application/json", "text/")
                ):
                    return
                data = await resp.read()
            self._placeholder_len = len(data)
            self._placeholder_sha = hashlib.sha256(data).hexdigest()
            self.logger.info(
                "Navidrome cover placeholder signature: %d bytes sha256=%s…",
                self._placeholder_len,
                self._placeholder_sha[:12],
            )
        except Exception as exc:
            self.logger.info(f"Could not fetch Navidrome cover placeholder ref: {exc}")

    def _is_placeholder(self, data: bytes) -> bool:
        """True when ``data`` is byte-identical to Navidrome's generic placeholder
        (length pre-check short-circuits for real covers of any other size)."""
        return (
            self._placeholder_sha is not None
            and len(data) == self._placeholder_len
            and hashlib.sha256(data).hexdigest() == self._placeholder_sha
        )

    async def _fetch_cover_bytes(
        self, cover_id: str, size: Optional[int]
    ) -> Optional[Tuple[bytes, str]]:
        """Raw getCoverArt fetch → ``(bytes, content_type)`` or None on a miss.

        A miss is a non-200, a non-image body (Subsonic's XML/JSON error for an
        unknown/stale id), or a connectivity failure — all reported as None so the
        caller 404s. Placeholder detection happens in :meth:`get_cover_art`.
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
                    # Unusual (Navidrome itself erroring) but still a "no cover"
                    # outcome the frontend handles — warning, not error, so it
                    # never surfaces as a system-error banner.
                    self.logger.warning(
                        f"Navidrome getCoverArt HTTP {resp.status} for {cover_id}"
                    )
                    return None
                content_type = resp.headers.get("Content-Type", "image/jpeg")
                if not content_type.startswith("image/"):
                    self.logger.info(
                        f"Navidrome has no cover for {cover_id} ({content_type})"
                    )
                    return None
                data = await resp.read()
        except Exception as exc:
            if is_network_error(exc):
                self.logger.info(f"Navidrome not reachable for cover art: {exc}")
            else:
                self.logger.error(f"Navidrome getCoverArt error for {cover_id}: {exc}")
            return None
        return data, content_type

    async def get_cover_art(
        self, cover_id: str, size: Optional[int] = None
    ) -> Optional[Tuple[bytes, str]]:
        """Fetch cover-art bytes + content-type for the /api/music-library/cover
        proxy (the frontend never reaches Navidrome directly).

        Returns ``(data, content_type)`` on success, None on miss. A miss is
        either a Subsonic error body (unknown id) or Navidrome's generic "no cover"
        placeholder — both reported as None so the route 404s and the frontend
        shows Milō's own placeholder instead of a foreign asset.

        The placeholder check is size-aware: Navidrome resizes its placeholder for
        thumbnail requests, so those bytes don't match the full-size signature.
        For a sized request whose placeholder signature we haven't learned yet, we
        confirm against the authoritative full-size art before treating it as a
        miss (and cache the resized signature so later thumbnails are a cheap
        compare) — this is what stops an art-less playlist's blue-vinyl default
        from leaking through at thumb size.
        """
        result = await self._fetch_cover_bytes(cover_id, size)
        if result is None:
            return None
        data, content_type = result

        await self._ensure_placeholder_signature()
        # Full-size signature: matches size=None requests and the cases where
        # Navidrome ignored `size` and returned the full placeholder.
        if self._is_placeholder(data):
            self.logger.debug(
                "Cover %s is Navidrome's placeholder — reporting as missing", cover_id
            )
            return None
        if size is None:
            return data, content_type

        sig = self._thumb_placeholder_sigs.get(size)
        digest = hashlib.sha256(data).hexdigest()
        if sig is not None:
            if sig == (len(data), digest):
                self.logger.debug(
                    "Cover %s is the resized placeholder (size=%s) — missing",
                    cover_id,
                    size,
                )
                return None
            return data, content_type

        # First sized request whose placeholder signature is unknown: confirm
        # against the full-size art. Only a genuine placeholder learns the
        # signature, so a real cover can never poison it.
        full = await self._fetch_cover_bytes(cover_id, None)
        if full is not None and self._is_placeholder(full[0]):
            self._thumb_placeholder_sigs[size] = (len(data), digest)
            self.logger.debug(
                "Learned resized placeholder for size=%s from %s", size, cover_id
            )
            return None
        return data, content_type
