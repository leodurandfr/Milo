"""
Radio Browser mirror discovery and rotation.

The Radio Browser project recommends that clients resolve
`all.api.radio-browser.info` to its full A-record list, shuffle it, and rotate
through entries on transient failure rather than relying on the DNS round-robin
alone.

References:
- https://api.radio-browser.info/
- https://docs.radio-browser.info/
"""
import asyncio
import logging
import random
import socket
from datetime import datetime, timedelta
from typing import List, Optional


class ServerDiscovery:
    """Resolves and rotates through Radio Browser mirrors.

    Strategy:
    1. Resolve `all.api.radio-browser.info` to a list of A records.
    2. Reverse-resolve each IP to a friendly hostname (best-effort).
    3. Shuffle the list to distribute load across volunteer mirrors.
    4. On request failure, advance to the next mirror via `rotate()`.
    5. Re-resolve every TTL so the pool stays fresh.
    """

    DISCOVERY_HOST = "all.api.radio-browser.info"
    FALLBACK_SERVER = "all.api.radio-browser.info"
    TTL = timedelta(hours=1)

    def __init__(self) -> None:
        self._logger = logging.getLogger(__name__)
        self._servers: List[str] = []
        self._cursor: int = 0
        self._resolved_at: Optional[datetime] = None
        self._lock = asyncio.Lock()

    def base_url(self, server: str) -> str:
        """Build the JSON API base URL for a given mirror hostname."""
        return f"https://{server}/json"

    @property
    def server_count(self) -> int:
        """Number of mirrors currently in the rotation pool (0 before resolution)."""
        return len(self._servers)

    async def get_server(self) -> str:
        """Return the current preferred mirror, resolving on first call or TTL expiry."""
        async with self._lock:
            if not self._servers or self._is_stale():
                await self._refresh()
            if not self._servers:
                return self.FALLBACK_SERVER
            return self._servers[self._cursor]

    async def rotate(self) -> str:
        """Advance to the next mirror; force a re-resolve after a full cycle."""
        async with self._lock:
            if not self._servers:
                await self._refresh()
                if not self._servers:
                    return self.FALLBACK_SERVER
                return self._servers[self._cursor]

            self._cursor += 1
            if self._cursor >= len(self._servers):
                # Full cycle since last resolution — refresh and start over.
                await self._refresh()
                self._cursor = 0
            if not self._servers:
                return self.FALLBACK_SERVER
            return self._servers[self._cursor]

    def _is_stale(self) -> bool:
        if self._resolved_at is None:
            return True
        return datetime.now() - self._resolved_at > self.TTL

    async def _refresh(self) -> None:
        """Resolve A records for DISCOVERY_HOST and reverse-lookup friendly names."""
        try:
            _, _, ips = await asyncio.to_thread(
                socket.gethostbyname_ex, self.DISCOVERY_HOST
            )
        except (socket.gaierror, OSError) as e:
            self._logger.info(
                f"DNS resolution of {self.DISCOVERY_HOST} failed: {e}; "
                f"falling back to {self.FALLBACK_SERVER}"
            )
            # Leave _resolved_at unchanged so the next call retries immediately
            # once the network recovers (instead of being stuck on the fallback
            # for a full TTL window).
            self._servers = []
            self._cursor = 0
            return

        names: List[str] = []
        for ip in ips:
            try:
                host, _, _ = await asyncio.to_thread(socket.gethostbyaddr, ip)
                names.append(host)
            except (socket.herror, socket.gaierror, OSError):
                names.append(ip)

        # Dedupe while preserving order, then shuffle.
        seen = set()
        unique_names = []
        for name in names:
            if name not in seen:
                seen.add(name)
                unique_names.append(name)
        random.shuffle(unique_names)

        self._servers = unique_names
        self._cursor = 0
        self._resolved_at = datetime.now()
        self._logger.info(
            f"Resolved {len(unique_names)} Radio Browser mirrors: "
            f"{', '.join(unique_names)}"
        )
