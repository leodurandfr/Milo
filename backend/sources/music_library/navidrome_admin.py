# backend/sources/music_library/navidrome_admin.py
"""Navidrome native-API client — the *library* administration surface.

The Subsonic API (:mod:`navidrome_client`) can read a per-library catalog
(``musicFolderId``) but cannot create or delete a library: that verb only exists
on Navidrome's own REST API, the one its web UI drives. This module is that
second window onto the same sidecar — JWT auth via ``/auth/login`` with the
first-boot-provisioned admin account, then CRUD on ``/api/library``.

Why it exists at all: Milō gives each mounted storage space (USB key, SMB/NFS
share) its own Navidrome library, because that is the only handle a browse call
can be scoped by. A single library over /media/milo says nothing about which
mount a track came from — ``media_file`` rows carry a ``library_id`` and a path
relative to the library root, never a mount name. :mod:`libraries.py` owns that
mapping; this client only speaks HTTP.

Fail-open, like the rest of the storage layer: a Navidrome still starting up, an
expired token or a rejected write logs and returns None/False. A mount must
complete whatever the catalog engine is doing.
"""
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp

from backend.config.constants import NAVIDROME_CRED_FILE, NAVIDROME_URL
from backend.shared.network import describe_network_error, is_network_error
from backend.sources.music_library.navidrome_client import load_navidrome_credentials

# Navidrome reads its session JWT from this header (not a plain Authorization:),
# and returns a refreshed token in the same header on every authenticated call.
AUTH_HEADER = "x-nd-authorization"

# One call's ceiling. Library writes trigger scanner bookkeeping, so they are
# slower than a read, but none of them is a long operation.
_TIMEOUT_S = 20


class NavidromeAdminClient:
    """Async client for Navidrome's native admin REST API (localhost only).

    Holds one aiohttp session and one JWT, re-obtained transparently when
    Navidrome rejects it (restart, session timeout). The caller owns the
    session's lifecycle via :meth:`close`.
    """

    def __init__(
        self,
        username: str,
        password: str,
        base_url: str = NAVIDROME_URL,
    ) -> None:
        self.logger = logging.getLogger("source.music_library.navidrome_admin")
        self._username = username
        self._password = password
        self._base_url = base_url.rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None
        self._token: Optional[str] = None
        # Our own user id, returned by the login response — needed to grant the
        # service account access to a library it just created.
        self._user_id: Optional[str] = None

    @classmethod
    def from_cred_file(
        cls,
        cred_file: Path = NAVIDROME_CRED_FILE,
        base_url: str = NAVIDROME_URL,
    ) -> Optional["NavidromeAdminClient"]:
        """Build a client from the provisioned cred file, or None if unavailable."""
        creds = load_navidrome_credentials(cred_file)
        if not creds:
            return None
        return cls(creds["username"], creds["password"], base_url=base_url)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None
        self._token = None

    @property
    def user_id(self) -> Optional[str]:
        """Navidrome's id for the service account (None until first login)."""
        return self._user_id

    # =========================================================================
    # AUTH
    # =========================================================================

    async def _ensure_session(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers={"User-Agent": "Milo/1.0"})

    async def _login(self) -> bool:
        """Exchange the service account's password for a session JWT."""
        await self._ensure_session()
        try:
            async with self._session.post(
                f"{self._base_url}/auth/login",
                json={"username": self._username, "password": self._password},
                timeout=aiohttp.ClientTimeout(total=_TIMEOUT_S),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    self.logger.error(
                        "Navidrome admin login failed (HTTP %s): %s",
                        resp.status, body[:200],
                    )
                    return False
                payload = await resp.json()
        except Exception as exc:
            if is_network_error(exc):
                self.logger.info(
                    "Navidrome did not answer yet for admin login: %s",
                    describe_network_error(exc),
                )
            else:
                self.logger.error("Navidrome admin login error: %s", exc)
            return False

        self._token = payload.get("token")
        self._user_id = payload.get("id")
        if not self._token:
            self.logger.error("Navidrome admin login returned no token")
            return False
        return True

    # =========================================================================
    # REQUESTS
    # =========================================================================

    async def _request(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Optional[Any]:
        """Call an authenticated native-API endpoint, logging in on demand.

        Returns the decoded JSON body (``{}`` for an empty 200/204), or None on
        any failure. A 401 costs one retry with a fresh token — Navidrome
        invalidates sessions on restart, which is routine here since the backend
        outlives its sidecar.
        """
        if self._token is None and not await self._login():
            return None

        for attempt in (1, 2):
            await self._ensure_session()
            try:
                async with self._session.request(
                    method,
                    f"{self._base_url}{path}",
                    json=payload,
                    params=params,
                    headers={AUTH_HEADER: f"Bearer {self._token}"},
                    timeout=aiohttp.ClientTimeout(total=_TIMEOUT_S),
                ) as resp:
                    if resp.status == 401 and attempt == 1:
                        self._token = None
                        if not await self._login():
                            return None
                        continue
                    if resp.status >= 400:
                        body = await resp.text()
                        self.logger.error(
                            "Navidrome admin %s %s failed (HTTP %s): %s",
                            method, path, resp.status, body[:300],
                        )
                        return None
                    # Navidrome refreshes the session token on every call.
                    refreshed = resp.headers.get(AUTH_HEADER)
                    if refreshed:
                        self._token = refreshed
                    text = await resp.text()
                    if not text:
                        return {}
                    return await resp.json(content_type=None)
            except Exception as exc:
                if is_network_error(exc):
                    self.logger.info(
                        "Navidrome did not answer for admin %s %s: %s",
                        method, path, describe_network_error(exc),
                    )
                else:
                    self.logger.error(
                        "Navidrome admin %s %s error: %s", method, path, exc
                    )
                return None
        return None

    # =========================================================================
    # LIBRARIES
    # =========================================================================

    async def list_libraries(self) -> Optional[List[Dict[str, Any]]]:
        """Every configured library (``{id, name, path, …}``), or None on failure.

        None and ``[]`` mean different things to the reconciler — "could not ask"
        must never be read as "there are none", which would delete every library.
        """
        result = await self._request(
            "GET", "/api/library", params={"_start": 0, "_end": 100, "_sort": "name"}
        )
        if result is None:
            return None
        return result if isinstance(result, list) else []

    async def create_library(self, name: str, path: str) -> Optional[Dict[str, Any]]:
        """Create a library rooted at ``path``. Returns the created record."""
        result = await self._request(
            "POST",
            "/api/library",
            payload={"name": name, "path": path, "defaultNewUsers": True},
        )
        if result is None:
            self.logger.error("Could not create Navidrome library %r at %s", name, path)
            return None
        self.logger.info("Created Navidrome library %r at %s", name, path)
        return result

    async def rename_library(self, library_id: int, name: str, path: str) -> bool:
        """Rename a library, keeping it where it is.

        ``path`` is not optional: this PUT replaces the record, and Navidrome
        rejects one without a path (``400 {"errors":{"path":"required"}}``), so
        a name-only payload is a rename that silently never happens. Moving a
        mount is not a rename — that is a new library.
        """
        result = await self._request(
            "PUT", f"/api/library/{library_id}", payload={"name": name, "path": path}
        )
        return result is not None

    async def delete_library(self, library_id: int) -> bool:
        """Delete a library and, with it, every track Navidrome indexed under it."""
        result = await self._request("DELETE", f"/api/library/{library_id}")
        return result is not None

    # =========================================================================
    # USER ACCESS
    # =========================================================================

    async def grant_all_libraries(self, library_ids: List[int]) -> bool:
        """Give the service account access to exactly ``library_ids``.

        A library nobody can see is invisible to the Subsonic API too, so every
        browse call would come back empty. Navidrome stores this as an explicit
        user↔library set once one has ever been assigned, so it has to be
        rewritten after each create/delete rather than left to the
        ``defaultNewUsers`` flag (which only seeds *new* users).

        **Read, merge, write — never write the one field.** ``PUT /api/user/{id}``
        replaces the whole record: sending only ``libraryIds`` blanks the rest,
        and a user whose ``userName`` is empty cannot be authenticated at all,
        by the Subsonic API either. That is a locked-out appliance, so the GET
        below is not an optimisation to skip.
        """
        if self._user_id is None and not await self._login():
            return False
        user = await self._request("GET", f"/api/user/{self._user_id}")
        if not user:
            self.logger.error("Could not read the Navidrome service account")
            return False
        # Everything the record carries goes back except the credential fields:
        # echoing an empty password from a GET that does not return one would
        # set it, and the whole appliance authenticates with that password.
        record = {
            key: value for key, value in user.items()
            if key not in ("password", "currentPassword", "newPassword")
        }
        result = await self._request(
            "PUT",
            f"/api/user/{self._user_id}",
            payload={**record, "libraryIds": library_ids},
        )
        return result is not None
