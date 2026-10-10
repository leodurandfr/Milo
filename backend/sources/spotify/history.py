# backend/sources/spotify/history.py
"""
The tracks milo-spotify played: the queue page's "Recently played" tab.

The Spotify apps keep this list on each device, for that device's own player
(measured 2026-10-10 on the Mac app 1.3.4: its context_player_state_restore
holds PlayHistoryV1, every track its player started, the one a transfer hands
it included, 500 at most, replays kept). No Spotify service lists it for a
Connect device, so Milō keeps its own the same way: a track is listed when it
starts playing here, newest first, per account, duplicates kept.
"""
import asyncio
from pathlib import Path
from typing import Any, Dict, List

from backend.shared.persistence import load_versioned_json, save_versioned_json


class SpotifyHistory:
    SCHEMA_VERSION = 1
    # What the Spotify apps keep.
    LIMIT = 500

    def __init__(self, file: Path) -> None:
        self._file = file
        self._lock = asyncio.Lock()
        self._accounts: Dict[str, List[Dict[str, Any]]] = {}

    async def initialize(self) -> None:
        """Load the file; a schema drift raises SchemaVersionMismatch at boot."""
        data = await load_versioned_json(self._file, self.SCHEMA_VERSION)
        self._accounts = data["accounts"] if data else {}

    def tracks(self, account: str) -> List[Dict[str, Any]]:
        """The account's history, newest first."""
        return list(self._accounts.get(account, []))

    def add(self, account: str, entry: Dict[str, Any]) -> None:
        """List a track that started playing, ahead of the others. The list
        moves at once; `save` writes it."""
        self._accounts[account] = [entry, *self._accounts.get(account, [])][:self.LIMIT]

    def forget(self, account: str) -> bool:
        """Drop what a forgotten profile played; whether there was anything."""
        return self._accounts.pop(account, None) is not None

    async def save(self) -> None:
        """Write the lists as they stand. Private like profiles.json, beside
        it: what an account listens to. The whole file goes at every start,
        so an entry holds only what the tab draws and the context to play it
        in again."""
        async with self._lock:
            # Taken here, on the loop: the write serializes on a worker thread
            # while the lists may move again.
            accounts = {account: list(tracks) for account, tracks in self._accounts.items()}
            await save_versioned_json(self._file, {"accounts": accounts}, self.SCHEMA_VERSION, private=True)
