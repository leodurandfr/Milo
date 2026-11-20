# backend/core/multiroom/pending_clients.py
"""
PendingClientsService — Storage for client devices that have registered
via API but are not yet visible in Snapcast.

Clients register at boot time with their MAC, IP, and hardware status.
They stay in pending storage until:
1. User configures them (name, speaker_type, audio_id)
2. Client reboots with audio configured
3. Snapclient connects → SnapcastWebSocketService transfers pending
   data to ClientRegistryService → entry removed from pending
4. Heartbeat expires → client stops sending registration POSTs;
   background task removes the entry after STALE_TIMEOUT seconds

Persistence: /var/lib/milo/pending_clients.json
"""
import asyncio
import contextlib
import json
import logging
import os
import time
from typing import Any, Dict, Optional

import aiofiles

from backend.config.constants import MILO_DATA_DIR
from backend.core.models.ws_events import MultiroomPendingClientChanged

logger = logging.getLogger(__name__)

PENDING_CLIENTS_FILE = MILO_DATA_DIR / "pending_clients.json"

# Clients that haven't sent a heartbeat within this window are considered offline
STALE_TIMEOUT = 45  # seconds
CLEANUP_INTERVAL = 15  # seconds

# A configure+reboot cycle (hardware apply, config.txt rewrite, full boot) routinely
# outlasts STALE_TIMEOUT, and this entry holds the only copy of the name and speaker
# type the user just chose — purging it mid-reboot silently discards them, and the
# client comes back registered under its Snapcast host name instead. Protect the
# entry for one reboot's worth of time; it is removed for real when the snapclient
# connects and the registry takes over.
CONFIGURING_GRACE = 300  # seconds


class PendingClientsService:
    """
    Manages pending client registrations before they appear in Snapcast.

    Thread-safe via asyncio.Lock. All mutations persist to disk
    and broadcast a WebSocket event. A background task removes
    clients that stop sending heartbeats (powered off / disconnected).
    """

    def __init__(self, state_machine=None):
        self._clients: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._state_machine = state_machine
        self._cleanup_task: Optional[asyncio.Task] = None

    async def initialize(self) -> bool:
        """Load persisted pending clients from disk and start cleanup task."""
        try:
            if os.path.exists(PENDING_CLIENTS_FILE):
                async with aiofiles.open(PENDING_CLIENTS_FILE, "r") as f:
                    content = await f.read()
                self._clients = json.loads(content) if content.strip() else {}
                logger.info(f"Loaded {len(self._clients)} pending client(s)")
            else:
                self._clients = {}
                logger.info("No pending clients file, starting fresh")
        except Exception as e:
            logger.error(f"Failed to load pending clients: {e}")
            self._clients = {}

        # The file does not carry the last heartbeat (`register_client` keeps it
        # in memory), so a loaded entry's timestamp says nothing about whether
        # its satellite is still alive. Each one gets a full STALE_TIMEOUT from
        # boot: a live satellite heartbeats within it and keeps its entry — a
        # purge here would drop it, and its next heartbeat would come back as a
        # brand-new speaker that wakes the screen — while a dead one expires at
        # the sweep after.
        now = time.time()
        for client in self._clients.values():
            client["registered_at"] = now

        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        return True

    async def shutdown(self) -> None:
        """Cancel the background cleanup task."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task

    # === CRUD ===

    async def register_client(
        self,
        mac_id: str,
        ip: str,
        hardware_configured: bool,
        audio_id: str,
        volume_control: bool = True,
    ) -> Dict[str, Any]:
        """
        Register or update a pending client.

        Called when a client POSTs to /api/multiroom/register-client.
        Upserts by MAC address — preserves user-set name/speaker_type on re-registration.

        A satellite re-registers every 15 s. A heartbeat that changes nothing
        but the timestamp only refreshes it in memory: no write of the file, no
        event, no info line. The timestamp is not worth persisting: `initialize`
        restarts every loaded entry's clock rather than reading it, so a live
        satellite keeps its entry across a backend restart and a dead one
        expires STALE_TIMEOUT after it.
        """
        async with self._lock:
            existing = self._clients.get(mac_id)

            if existing:
                connection = {
                    "ip": ip,
                    "hardware_configured": hardware_configured,
                    "audio_id": audio_id,
                    "volume_control": volume_control,
                }
                existing["registered_at"] = time.time()
                # The configure grace ends the moment the client reports the card
                # it was rebooted for: the cycle is over, normal staleness resumes.
                ends_grace = (hardware_configured and not existing.get("hardware_configured")
                              and "configuring_since" in existing)
                if not ends_grace and all(existing.get(k) == v for k, v in connection.items()):
                    logger.debug(f"Pending client heartbeat: {mac_id}")
                    return dict(existing)
                if ends_grace:
                    existing.pop("configuring_since")
                # Update connection info, preserve user-set fields
                existing.update(connection)
                client = existing
            else:
                client = {
                    "mac_id": mac_id,
                    "ip": ip,
                    "hardware_configured": hardware_configured,
                    "audio_id": audio_id,
                    "volume_control": volume_control,
                    "name": None,
                    "speaker_type": "bookshelf",
                    "registered_at": time.time(),
                }
                self._clients[mac_id] = client

            client_snapshot = dict(client)
            await self._persist()

        await self._broadcast(MultiroomPendingClientChanged(
            action="registered", client=client_snapshot,
        ))

        logger.info(f"Pending client {'updated' if existing else 'registered'}: {mac_id} (ip={ip}, audio={audio_id})")
        return client_snapshot

    async def update_client(
        self,
        mac_id: str,
        name: Optional[str] = None,
        speaker_type: Optional[str] = None,
        audio_id: Optional[str] = None,
        volume_control: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        """Partial update of a pending client. Returns None if not found.

        An update that changes nothing — an adopted satellite's heartbeat
        carries its identity every 15 s — is neither written nor broadcast.
        """
        async with self._lock:
            client = self._clients.get(mac_id)
            if not client:
                return None

            fields = {
                "name": name,
                "speaker_type": speaker_type,
                "audio_id": audio_id,
                "volume_control": volume_control,
            }
            changes = {k: v for k, v in fields.items() if v is not None and client.get(k) != v}
            if not changes:
                return dict(client)
            client.update(changes)

            client_snapshot = dict(client)
            await self._persist()

        await self._broadcast(MultiroomPendingClientChanged(
            action="updated", client=client_snapshot,
        ))
        return client_snapshot

    async def mark_configuring(self, mac_id: str) -> bool:
        """Shield a client from heartbeat expiry while it applies config + reboots.

        Server-side bookkeeping only — no broadcast: the UI tracks its own
        'rebooting' state, and nothing in the payload changes for consumers.
        """
        async with self._lock:
            client = self._clients.get(mac_id)
            if not client:
                return False
            client["configuring_since"] = time.time()
            await self._persist()
        return True

    def get_client(self, mac_id: str) -> Optional[Dict[str, Any]]:
        """Get a single pending client by MAC. Returns None if not found."""
        client = self._clients.get(mac_id)
        return dict(client) if client else None

    def get_all_clients(self) -> Dict[str, Dict[str, Any]]:
        """Get all pending clients (returns copies to prevent external mutation)."""
        return {mac_id: dict(client) for mac_id, client in self._clients.items()}

    async def remove_client(self, mac_id: str) -> bool:
        """Remove a client from pending storage. Returns True if removed."""
        async with self._lock:
            if mac_id not in self._clients:
                return False
            del self._clients[mac_id]
            await self._persist()

        await self._broadcast(MultiroomPendingClientChanged(
            action="removed", mac_id=mac_id,
        ))

        logger.info(f"Pending client removed: {mac_id}")
        return True

    # === PERSISTENCE ===

    async def _persist(self) -> None:
        """Atomic write pending clients to disk."""
        try:
            tmp_path = str(PENDING_CLIENTS_FILE) + ".tmp"
            content = json.dumps(self._clients, indent=2)

            async with aiofiles.open(tmp_path, "w") as f:
                await f.write(content)

            os.replace(tmp_path, str(PENDING_CLIENTS_FILE))
        except Exception as e:
            logger.error(f"Failed to persist pending clients: {e}")

    # === HEARTBEAT CLEANUP ===

    async def _cleanup_loop(self) -> None:
        """Periodically remove clients that stopped sending heartbeats."""
        while True:
            await asyncio.sleep(CLEANUP_INTERVAL)
            await self._purge_stale()

    async def _purge_stale(self) -> None:
        """Remove clients whose last heartbeat is older than STALE_TIMEOUT seconds."""
        now = time.time()
        stale_ids = []

        async with self._lock:
            for mac_id, client in self._clients.items():
                configuring_since = client.get("configuring_since")
                if configuring_since and now - configuring_since < CONFIGURING_GRACE:
                    continue
                if now - client.get("registered_at", 0) > STALE_TIMEOUT:
                    stale_ids.append(mac_id)

            if not stale_ids:
                return

            for mac_id in stale_ids:
                del self._clients[mac_id]
            await self._persist()

        for mac_id in stale_ids:
            logger.info(f"Pending client expired (no heartbeat): {mac_id}")
            await self._broadcast(MultiroomPendingClientChanged(
                action="removed", mac_id=mac_id,
            ))

    # === BROADCASTING ===

    async def _broadcast(self, event: MultiroomPendingClientChanged) -> None:
        """Broadcast event via state machine."""
        if self._state_machine:
            try:
                await self._state_machine.broadcast(event)
            except Exception as e:
                logger.error(f"Failed to broadcast pending client event: {e}")
