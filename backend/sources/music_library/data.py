# backend/sources/music_library/data.py
"""Network-share configuration for the Music Library source.

Persists the list of SMB/NFS shares the user has added, so they can be re-mounted
under /media/milo at every boot. Versioned JSON at
``/var/lib/milo/music_library_data.json`` (schema_version protocol — see CLAUDE.md
§"Persistence & schema-version protocol").

This file holds **non-secret metadata only** — id, type, host, path, display
name, and a ``has_credentials`` flag. Share passwords never touch this file (nor
any WS/API payload): they live in a root-only cred file written by ``milo-mount``
(see :mod:`backend.sources.music_library.storage`).

A USB key is remembered under ``known_usb``, keyed by filesystem UUID — the only
identity a key keeps across a replug (its mountpoint follows the filesystem
label, and gains a disambiguating suffix when two keys share one). The entry
holds the name the user gave it plus the mountpoint it was last mounted at.

The mountpoint is persisted because it is what keeps a key's *index* alive while
it is unplugged: a Navidrome library is identified by its path, so a key whose
mountpoint the backend has forgotten across a restart loses its library on the
next reconcile — and with it the 18 minutes it took to index 10 000 tracks.
Remembering it is what makes a replug cost a quick scan (~0.4 s measured) instead
of a full re-index.
"""
import asyncio
import logging
import re
import secrets
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.config.constants import MUSIC_LIBRARY_DATA_FILE
from backend.shared.persistence import load_versioned_json, save_versioned_json

REQUIRED_TOP_LEVEL_KEYS = ("shares", "known_usb", "playlist_storages")

# Share types we can mount. Mirrors milo-mount's --network dispatch.
SHARE_TYPES = frozenset({"cifs", "nfs"})


class MusicLibraryDataService:
    """Persistence for the configured network shares and the USB names.

    Pure storage — no mounting. The source orchestrates mount/unmount around
    these writes (config first, then the privileged milo-mount call), so the
    on-disk list is the source of truth a boot remount replays.
    """

    SCHEMA_VERSION: int = 3

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
            return await self._load_locked()

    async def _load_locked(self) -> Dict[str, Any]:
        """Read + validate. The caller must already hold ``_file_lock``."""
        data = await load_versioned_json(self._data_file, self.SCHEMA_VERSION)

        if not data:
            return self._get_default_structure()

        self._validate_required_keys(data)
        return data

    async def _mutate(self, apply: Callable[[Dict[str, Any]], Tuple[bool, Any]]) -> Any:
        """Read → mutate → write under a single hold of ``_file_lock``.

        ``apply`` receives the loaded dict, edits it in place and returns
        ``(changed, result)``; the file is rewritten only when ``changed``, and
        ``result`` is what this returns. It is deliberately synchronous: an
        await inside it would reopen the window this closes.

        Taking the lock once is the whole point. ``load_data`` and ``save_data``
        each take it separately, so two concurrent mutators interleave as
        load/load/save/save and the second write drops the first one's entire
        update — a USB key remembered while a share was being added simply
        disappeared, and this file is what a boot remount replays. ``asyncio.Lock``
        is not reentrant, which is why this goes through the unlocked internals
        instead of reusing those two.
        """
        async with self._file_lock:
            data = await self._load_locked()
            changed, result = apply(data)
            if changed:
                await save_versioned_json(self._data_file, data, self.SCHEMA_VERSION)
        return result

    def _validate_required_keys(self, data: Dict[str, Any]) -> None:
        """Fail-loud if any expected top-level key is missing."""
        missing = [k for k in REQUIRED_TOP_LEVEL_KEYS if k not in data]
        if missing:
            raise RuntimeError(
                f"music_library_data.json missing required keys: {missing} — "
                f"delete it to reset (rm {self._data_file})"
            )

    def _get_default_structure(self) -> Dict[str, Any]:
        return {"shares": [], "known_usb": {}, "playlist_storages": {}}

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
        username: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Append a new share and return it (with its generated id).

        The id doubles as the /media/milo/<id> mountpoint and the <id>.cred
        filename, so it is a URL/path-safe slug of the display name plus a short
        random suffix (unique, stable, human-readable).

        ``username``/``domain`` are stored as non-secret metadata so the edit
        screen can show them (an account name is an identifier, not a secret);
        only the password stays write-only in the root-only cred file.
        """
        def apply(data: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
            existing_ids = {s.get("id") for s in data["shares"]}
            share = {
                "id": self._generate_id(name, existing_ids),
                "type": share_type,
                "host": host,
                "path": path,
                "name": name,
                "has_credentials": has_credentials,
                "username": username,
                "domain": domain,
                "created_at": int(time.time()),
            }
            data["shares"].append(share)
            return True, share

        return await self._mutate(apply)

    async def update_share(
        self, share_id: str, updates: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Merge ``updates`` into an existing share (id/created_at immutable).

        Returns the updated share, or None if no share has that id.
        """
        def apply(data: Dict[str, Any]) -> Tuple[bool, Optional[Dict[str, Any]]]:
            share = next((s for s in data["shares"] if s.get("id") == share_id), None)
            if share is None:
                return False, None
            for key, value in updates.items():
                if key in ("id", "created_at"):
                    continue
                share[key] = value
            return True, share

        return await self._mutate(apply)

    async def remove_share(self, share_id: str) -> Optional[Dict[str, Any]]:
        """Drop a share from the config; return the removed entry or None."""
        def apply(data: Dict[str, Any]) -> Tuple[bool, Optional[Dict[str, Any]]]:
            removed = next((s for s in data["shares"] if s.get("id") == share_id), None)
            if removed is None:
                return False, None
            data["shares"] = [s for s in data["shares"] if s.get("id") != share_id]
            return True, removed

        return await self._mutate(apply)

    # ========== KNOWN USB KEYS ==========

    async def get_known_usb(self) -> Dict[str, Dict[str, Any]]:
        """Every USB key ever mounted, keyed by filesystem UUID.

        Each entry is ``{name, label, mountpoint, last_seen}``: ``name`` is the
        user-given one (None when never renamed — the display falls back to
        ``label``, the sanitized filesystem label milo-mount derived the
        mountpoint from).
        """
        data = await self.load_data()
        return data["known_usb"]

    async def remember_usb(self, uuid: str, label: str, mountpoint: str) -> None:
        """Record a key that has just been mounted, preserving its given name.

        Called on every mount, so ``mountpoint`` tracks the one milo-mount
        actually chose — it can differ from the last session's when a second key
        claimed the same label first and this one took the disambiguating suffix.
        """
        def apply(data: Dict[str, Any]) -> Tuple[bool, None]:
            entry = data["known_usb"].get(uuid) or {"name": None}
            data["known_usb"][uuid] = {
                "name": entry.get("name"),
                "label": label,
                "mountpoint": mountpoint,
                "last_seen": int(time.time()),
            }
            return True, None

        await self._mutate(apply)

    async def set_usb_name(self, uuid: str, name: str) -> bool:
        """Name a known USB key, or restore its disk label when ``name`` is empty.

        False when the UUID was never mounted — there is nothing to name, and
        inventing an entry would put a key with no label or mountpoint in the
        known set. The name outlives an unplug: that it comes back with the key
        is the whole point of filing it under the filesystem UUID.
        """
        def apply(data: Dict[str, Any]) -> Tuple[bool, bool]:
            entry = data["known_usb"].get(uuid)
            if entry is None:
                return False, False
            entry["name"] = name or None
            return True, True

        return await self._mutate(apply)

    async def forget_usb(self, uuid: str) -> bool:
        """Drop a key from the known set; False when it was not there.

        Retires its Navidrome library on the next reconcile, which is what
        actually frees the index — so this is the only way a key that will never
        be plugged in again stops costing catalog rows.
        """
        def apply(data: Dict[str, Any]) -> Tuple[bool, bool]:
            if data["known_usb"].pop(uuid, None) is None:
                return False, False
            return True, True

        return await self._mutate(apply)

    # ========== PLAYLIST ↔ STORAGE SPACE ==========

    async def get_playlist_storages(self) -> Dict[str, str]:
        """Which storage space each Milō-created playlist belongs to.

        Keyed by playlist id, valued by *storage* id (a USB key's filesystem
        UUID, a share's id) rather than a Navidrome library id — a library is
        recreated with a new id every time a key comes back, and the playlist
        must survive that.
        """
        data = await self.load_data()
        return data["playlist_storages"]

    async def set_playlist_storage(self, playlist_id: str, storage_id: str) -> None:
        """Record the storage space a playlist was created in."""
        def apply(data: Dict[str, Any]) -> Tuple[bool, None]:
            data["playlist_storages"][playlist_id] = storage_id
            return True, None

        await self._mutate(apply)

    async def forget_playlist(self, playlist_id: str) -> None:
        """Drop a deleted playlist's association."""
        def apply(data: Dict[str, Any]) -> Tuple[bool, None]:
            return data["playlist_storages"].pop(playlist_id, None) is not None, None

        await self._mutate(apply)

    @staticmethod
    def _generate_id(name: str, existing_ids: set) -> str:
        """Path-safe unique id: ``<slug>-<8 hex>`` (retries on the astronomically
        unlikely suffix collision)."""
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32] or "share"
        while True:
            candidate = f"{slug}-{secrets.token_hex(4)}"
            if candidate not in existing_ids:
                return candidate
