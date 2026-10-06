# backend/sources/spotify/profiles.py
"""
The Spotify accounts Milō keeps: one per account that cast to it once.

go-librespot stores a single account (state.json `credentials`) and replaces it
with whoever casts next. Milō keeps every one it saw here, so the browser can
sign the daemon back in as any of them. The credentials are go-librespot's own
stored blob, verbatim: reusable from any device, so the file is 0600 and never
leaves the backend — no route, no log line and no diagnostic collector reads
it. A user may forget a profile, and one Spotify refuses is forgotten; a new
cast brings it back. When the signed-in profile goes, the one signed in last
on Milō takes its place (`successor`).
"""
import asyncio
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.shared.persistence import load_versioned_json, save_versioned_json


class SpotifyProfiles:
    SCHEMA_VERSION = 2

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
                "name": profile["spotify_name"] or username,
                "spotify_name": profile["spotify_name"],
                "avatar_url": profile["avatar_url"],
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

    def successor(self) -> Optional[str]:
        """The profile to sign in when the signed-in one goes: the one signed
        in last; None with none left."""
        if not self._profiles:
            return None
        return max(self._profiles, key=lambda username: self._profiles[username]["signed_in_at"])

    async def harvest(self, username: str, credentials: str, at: float) -> bool:
        """Keep `credentials` for `username`, signed in at `at`; True when the
        account is new. A known account's blob is refreshed (Spotify rotates
        it)."""
        async with self._lock:
            profile = self._profiles.get(username)
            new = profile is None
            if new:
                profile = self._profiles[username] = {
                    "spotify_name": None, "avatar_url": None, "added_at": time.time(),
                }
            profile.update(credentials=credentials, signed_in_at=at)
            await self._save()
            return new

    async def set_identity(self, username: str, identity: Dict[str, Optional[str]]) -> None:
        """Take what Spotify answered (`spotify_name`, `avatar_url`):
        a field it left out keeps what was kept."""
        async with self._lock:
            profile = self._profiles.get(username)
            if profile is None:
                return
            if all(profile[key] == value for key, value in identity.items()):
                return
            profile.update(identity)
            await self._save()

    async def forget(self, username: str) -> bool:
        async with self._lock:
            if self._profiles.pop(username, None) is None:
                return False
            await self._save()
            return True

    async def _save(self) -> None:
        await save_versioned_json(
            self._file, {"profiles": self._profiles}, self.SCHEMA_VERSION, private=True,
        )
