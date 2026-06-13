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

logger = logging.getLogger(__name__)

PENDING_CLIENTS_FILE = MILO_DATA_DIR / "pending_clients.json"

# Clients that haven't sent a heartbeat within this window are considered offline
STALE_TIMEOUT = 45  # seconds
CLEANUP_INTERVAL = 15  # seconds


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

        # Remove stale entries left over from a previous run
        await self._purge_stale()

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
        """
        async with self._lock:
            existing = self._clients.get(mac_id)

            if existing:
                # Update connection info, preserve user-set fields
                existing["ip"] = ip
                existing["hardware_configured"] = hardware_configured
                existing["audio_id"] = audio_id
                existing["volume_control"] = volume_control
                existing["registered_at"] = time.time()
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

        await self._broadcast("pending_client_changed", {
            "action": "registered",
            "client": client_snapshot,
        })

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
        """Partial update of a pending client. Returns None if not found."""
        async with self._lock:
            client = self._clients.get(mac_id)
            if not client:
                return None

            if name is not None:
                client["name"] = name
            if speaker_type is not None:
                client["speaker_type"] = speaker_type
            if audio_id is not None:
                client["audio_id"] = audio_id
            if volume_control is not None:
                client["volume_control"] = volume_control

            client_snapshot = dict(client)
            await self._persist()

        await self._broadcast("pending_client_changed", {
            "action": "updated",
            "client": client_snapshot,
        })
        return client_snapshot

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

        await self._broadcast("pending_client_changed", {
            "action": "removed",
            "mac_id": mac_id,
        })

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
                if now - client.get("registered_at", 0) > STALE_TIMEOUT:
                    stale_ids.append(mac_id)

            if not stale_ids:
                return

            for mac_id in stale_ids:
                del self._clients[mac_id]
            await self._persist()

        for mac_id in stale_ids:
            logger.info(f"Pending client expired (no heartbeat): {mac_id}")
            await self._broadcast("pending_client_changed", {
                "action": "removed",
                "mac_id": mac_id,
            })

    # === BROADCASTING ===

    async def _broadcast(self, event_type: str, data: dict) -> None:
        """Broadcast event via state machine."""
        if self._state_machine:
            try:
                await self._state_machine.broadcast_event(
                    category="multiroom",
                    event_type=event_type,
                    data=data,
                )
            except Exception as e:
                logger.error(f"Failed to broadcast pending client event: {e}")
