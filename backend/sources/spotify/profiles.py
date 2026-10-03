# backend/sources/spotify/profiles.py
"""
The Spotify accounts Milō keeps: one per account that cast to it once.

go-librespot stores a single account (state.json `credentials`) and replaces it
with whoever casts next. Milō keeps every one it saw here, so the browser can
sign the daemon back in as any of them. The credentials are go-librespot's own
stored blob, verbatim: reusable from any device, so the file is 0600 and never
leaves the backend — no route, no log line and no diagnostic collector reads
it. A user may rename a profile or forget it; a new cast brings it back.
"""
import asyncio
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.shared.persistence import load_versioned_json, save_versioned_json


class SpotifyProfiles:
    SCHEMA_VERSION = 1

    def __init__(self, file: Path) -> None:
        self._file = file
        self._lock = asyncio.Lock()
        self._profiles: Dict[str, Dict[str, Any]] = {}

    async def initialize(self) -> None:
        """Load the file; a schema drift raises SchemaVersionMismatch at boot."""
        data = await load_versioned_json(self._file, self.SCHEMA_VERSION)
        self._profiles = data["profiles"] if data else {}

    def list(self) -> List[Dict[str, Any]]:
        """Every profile in the order they were first seen — never the credentials."""
        ordered = sorted(self._profiles.items(), key=lambda item: item[1]["added_at"])
        return [
            {
                "username": username,
                "name": profile["name"] or profile["spotify_name"] or username,
                "spotify_name": profile["spotify_name"],
                "avatar_url": profile["avatar_url"],
                "color": profile["color"],
                "stale": profile["stale"],
            }
            for username, profile in ordered
        ]

    def __contains__(self, username: str) -> bool:
        return username in self._profiles

    def __len__(self) -> int:
        return len(self._profiles)

    def credentials(self, username: str) -> Optional[str]:
        profile = self._profiles.get(username)
        return profile["credentials"] if profile else None

    def needs_identity(self, username: str) -> bool:
        profile = self._profiles.get(username)
        return profile is not None and profile["spotify_name"] is None

    async def harvest(self, username: str, credentials: str) -> bool:
        """Keep `credentials` for `username`; True when the account is new. A
        known account's blob is refreshed (Spotify rotates it) and its stale
        mark cleared: it just signed in."""
        async with self._lock:
            profile = self._profiles.get(username)
            if profile is not None and profile["credentials"] == credentials and not profile["stale"]:
                return False
            new = profile is None
            if new:
                profile = self._profiles[username] = {
                    "name": None, "spotify_name": None, "avatar_url": None, "color": None,
                    "added_at": time.time(),
                }
            profile.update(credentials=credentials, stale=False)
            await self._save()
            return new

    async def set_identity(
        self, username: str, spotify_name: Optional[str], avatar_url: Optional[str], color: Optional[str],
    ) -> None:
        async with self._lock:
            profile = self._profiles.get(username)
            if profile is None:
                return
            profile.update(spotify_name=spotify_name, avatar_url=avatar_url, color=color)
            await self._save()

    async def rename(self, username: str, name: Optional[str]) -> bool:
        """`name` None goes back to the Spotify name."""
        async with self._lock:
            profile = self._profiles.get(username)
            if profile is None:
                return False
            profile["name"] = name
            await self._save()
            return True

    async def forget(self, username: str) -> bool:
        async with self._lock:
            if self._profiles.pop(username, None) is None:
                return False
            await self._save()
            return True

    async def mark_stale(self, username: str) -> None:
        """Spotify refused the stored credentials (a changed password): only a
        new cast from that account can bring it back."""
        async with self._lock:
            profile = self._profiles.get(username)
            if profile is None or profile["stale"]:
                return
            profile["stale"] = True
            await self._save()

    async def _save(self) -> None:
        await save_versioned_json(
            self._file, {"profiles": self._profiles}, self.SCHEMA_VERSION, private=True,
        )
