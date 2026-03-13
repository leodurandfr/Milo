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

Persistence: /var/lib/milo/pending_clients.json
"""
import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, Optional

import aiofiles

from backend.config.constants import MILO_DATA_DIR

logger = logging.getLogger(__name__)

PENDING_CLIENTS_FILE = MILO_DATA_DIR / "pending_clients.json"


class PendingClientsService:
    """
    Manages pending client registrations before they appear in Snapcast.

    Thread-safe via asyncio.Lock. All mutations persist to disk
    and broadcast a WebSocket event.
    """

    def __init__(self):
        self._clients: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._state_machine = None

    def set_state_machine(self, state_machine) -> None:
        """Set state machine for event broadcasting."""
        self._state_machine = state_machine

    async def initialize(self) -> bool:
        """Load persisted pending clients from disk."""
        try:
            if os.path.exists(PENDING_CLIENTS_FILE):
                async with aiofiles.open(PENDING_CLIENTS_FILE, "r") as f:
                    content = await f.read()
                self._clients = json.loads(content) if content.strip() else {}
                logger.info(f"Loaded {len(self._clients)} pending client(s)")
            else:
                self._clients = {}
                logger.info("No pending clients file, starting fresh")
            return True
        except Exception as e:
            logger.error(f"Failed to load pending clients: {e}")
            self._clients = {}
            return True

    # === CRUD ===

    async def register_client(
        self,
        mac_id: str,
        ip: str,
        hardware_configured: bool,
        audio_id: str,
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
                existing["registered_at"] = time.time()
                client = existing
            else:
                client = {
                    "mac_id": mac_id,
                    "ip": ip,
                    "hardware_configured": hardware_configured,
                    "audio_id": audio_id,
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
