# backend/sources/music_library/data.py
"""Network-share configuration for the Music Library source (Phase 2).

Persists the list of SMB/NFS shares the user has added, so they can be re-mounted
under /media/milo at every boot. Versioned JSON at
``/var/lib/milo/music_library_data.json`` (schema_version protocol — see CLAUDE.md
§"Persistence & schema-version protocol").

This file holds **non-secret metadata only** — id, type, host, path, display
name, and a ``has_credentials`` flag. Share passwords never touch this file (nor
any WS/API payload): they live in a root-only cred file written by ``milo-mount``
(see :mod:`backend.sources.music_library.storage`). USB keys are not persisted —
they are auto-detected live by the StorageManager; only network shares, which
have no hotplug event to rediscover them, are recorded here.
"""
import asyncio
import logging
import re
import secrets
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.config.constants import MUSIC_LIBRARY_DATA_FILE
from backend.shared.persistence import load_versioned_json, save_versioned_json

REQUIRED_TOP_LEVEL_KEYS = ("shares",)

# Share types we can mount. Mirrors milo-mount's --network dispatch.
SHARE_TYPES = frozenset({"cifs", "nfs"})


class MusicLibraryDataService:
    """Persistence for the configured network shares (create/read/update/delete).

    Pure storage — no mounting. The source orchestrates mount/unmount around
    these writes (config first, then the privileged milo-mount call), so the
    on-disk list is the source of truth a boot remount replays.
    """

    SCHEMA_VERSION: int = 1

    def __init__(self) -> None:
        self._logger = logging.getLogger("source.music_library.data")
        self._data_file: Path = MUSIC_LIBRARY_DATA_FILE
        self._file_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Pre-load the shares file so a schema mismatch surfaces at boot.

        Seeds defaults on fresh install. Raises SchemaVersionMismatch on version
        drift or RuntimeError on missing required keys; the handler in
        dependencies.py::init_async logs the banner and SystemExit(1)s.
        """
        async with self._file_lock:
            data = await load_versioned_json(self._data_file, self.SCHEMA_VERSION)

        if not data:
            await self.save_data(self._get_default_structure())
            return

        self._validate_required_keys(data)

    async def load_data(self) -> Dict[str, Any]:
        """Load the shares file. Trusts shape (validated at boot in initialize())."""
        async with self._file_lock:
            data = await load_versioned_json(self._data_file, self.SCHEMA_VERSION)

        if not data:
            return self._get_default_structure()

        self._validate_required_keys(data)
        return data

    def _validate_required_keys(self, data: Dict[str, Any]) -> None:
        """Fail-loud if any expected top-level key is missing."""
        missing = [k for k in REQUIRED_TOP_LEVEL_KEYS if k not in data]
        if missing:
            raise RuntimeError(
                f"music_library_data.json missing required keys: {missing} — "
                f"delete it to reset (rm {self._data_file})"
            )

    def _get_default_structure(self) -> Dict[str, Any]:
        return {"shares": []}

    async def save_data(self, data: Dict[str, Any]) -> bool:
        """Save the shares file with atomic write (schema_version stamped auto)."""
        async with self._file_lock:
            await save_versioned_json(self._data_file, data, self.SCHEMA_VERSION)
        return True

    # ========== SHARES ==========

    async def list_shares(self) -> List[Dict[str, Any]]:
        """All configured shares (non-secret metadata; safe to return over API)."""
        data = await self.load_data()
        return data.get("shares", [])

    async def get_share(self, share_id: str) -> Optional[Dict[str, Any]]:
        """A single share by id, or None."""
        shares = await self.list_shares()
        return next((s for s in shares if s.get("id") == share_id), None)

    async def add_share(
        self,
        share_type: str,
        host: str,
        path: str,
        name: str,
        has_credentials: bool,
    ) -> Dict[str, Any]:
        """Append a new share and return it (with its generated id).

        The id doubles as the /media/milo/<id> mountpoint and the <id>.cred
        filename, so it is a URL/path-safe slug of the display name plus a short
        random suffix (unique, stable, human-readable).
        """
        data = await self.load_data()
        existing_ids = {s.get("id") for s in data["shares"]}
        share = {
            "id": self._generate_id(name, existing_ids),
            "type": share_type,
            "host": host,
            "path": path,
            "name": name,
            "has_credentials": has_credentials,
            "created_at": int(time.time()),
        }
        data["shares"].append(share)
        await self.save_data(data)
        return share

    async def update_share(
        self, share_id: str, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Merge ``updates`` into an existing share (id/created_at immutable).

        Returns the updated share, or None if no share has that id.
        """
        data = await self.load_data()
        share = next((s for s in data["shares"] if s.get("id") == share_id), None)
        if share is None:
            return None
        for key, value in updates.items():
            if key in ("id", "created_at"):
                continue
            share[key] = value
        await self.save_data(data)
        return share

    async def remove_share(self, share_id: str) -> Optional[Dict[str, Any]]:
        """Drop a share from the config; return the removed entry or None."""
        data = await self.load_data()
        removed = next((s for s in data["shares"] if s.get("id") == share_id), None)
        if removed is None:
            return None
        data["shares"] = [s for s in data["shares"] if s.get("id") != share_id]
        await self.save_data(data)
        return removed

    @staticmethod
    def _generate_id(name: str, existing_ids: set) -> str:
        """Path-safe unique id: ``<slug>-<8 hex>`` (retries on the astronomically
        unlikely suffix collision)."""
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32] or "share"
        while True:
            candidate = f"{slug}-{secrets.token_hex(4)}"
            if candidate not in existing_ids:
                return candidate
