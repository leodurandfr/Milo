# backend/infrastructure/services/multiroom_volume_handler.py
"""
Multiroom volume handler - DSP volume propagation to all clients.

Volume model:
- global_volume_db: Main volume level (-60 to 0 dB)
- client_offset_db: Per-client offset (hostname -> dB), defaults to 0

Each client's actual volume = clamp(global_volume_db + client_offset_db[hostname])

Snapcast is used for client discovery only - volume is always 100% passthrough.
"""
import asyncio
import logging
import time
from typing import TYPE_CHECKING, Dict, List, Any, Optional, Callable

if TYPE_CHECKING:
    from backend.infrastructure.services.volume_converter_service import VolumeConverterService


class MultiroomVolumeHandler:
    """
    Handles volume control in multiroom mode via CamillaDSP.

    Volume model:
    - Global volume applies to all clients
    - Each client can have an offset (for zone balancing)
    - Actual volume = global + offset (clamped to limits)
    """

    SNAPCAST_CACHE_MS = 50
    CLIENT_DSP_PORT = 8001

    def __init__(
        self,
        converter: "VolumeConverterService",
        snapcast_service,
        state_machine,
        dsp_service=None,
    ):
        self.converter = converter
        self.snapcast_service = snapcast_service
        self.state_machine = state_machine
        self._dsp_service = dsp_service
        self.logger = logging.getLogger(__name__)

        # Volume state (all in dB)
        self._global_volume_db: float = -30.0
        self._client_offset_db: Dict[str, float] = {}  # hostname -> offset
        self._client_volume_db: Dict[str, float] = {}  # hostname -> actual volume

        # Snapcast cache
        self._snapcast_clients_cache: List[Dict] = []
        self._snapcast_cache_time = 0

        # Async lock for state operations to prevent race conditions
        self._state_lock = asyncio.Lock()

    # ============================================================================
    # PUBLIC API
    # ============================================================================

    def get_global_volume_db(self) -> float:
        """Get current global volume in dB."""
        return self._global_volume_db

    def set_global_volume_db(self, volume_db: float) -> None:
        """Set global volume in dB (used during initialization)."""
        self._global_volume_db = volume_db

    async def apply_delta_db(self, delta_db: float) -> bool:
        """
        Apply delta to global volume and propagate to all clients.
        Each client's offset is preserved, and each client is clamped individually.

        Args:
            delta_db: Volume change in dB (positive = louder)

        Returns:
            True if successful
        """
        async with self._state_lock:
            try:
                import aiohttp

                # Get all multiroom client hostnames
                clients = await self._get_snapcast_clients_cached()
                if not clients:
                    self.logger.warning("No clients available for DSP volume")
                    return False

                hostnames = self._extract_hostnames(clients)

                # Calculate new global (don't clamp yet - used for offset calculation)
                new_global = self._global_volume_db + delta_db

                # Check if ANY client can still move in the requested direction
                can_move = False
                for hostname in hostnames:
                    offset = self._client_offset_db.get(hostname, 0.0)
                    current_client_vol = self._client_volume_db.get(hostname, self._global_volume_db)
                    new_client_vol = self.converter.clamp_db(new_global + offset)
                    if new_client_vol != current_client_vol:
                        can_move = True
                        break

                if not can_move:
                    self.logger.debug("No client can move further in this direction")
                    return True  # Not an error, just at limit

                # Update global volume (clamp it for storage)
                self._global_volume_db = self.converter.clamp_db(new_global)

                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                    tasks = []
                    for hostname in hostnames:
                        # Calculate client volume = global + offset (clamped per-client)
                        offset = self._client_offset_db.get(hostname, 0.0)
                        client_volume = self.converter.clamp_db(new_global + offset)

                        tasks.append(self._set_client_dsp_volume(session, hostname, client_volume))
                        self._client_volume_db[hostname] = client_volume

                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    # Log failures
                    for hostname, result in zip(hostnames, results):
                        if isinstance(result, Exception):
                            self.logger.error(f"Failed to set DSP on {hostname}: {result}")
                        elif not result:
                            self.logger.warning(f"Failed to set DSP on {hostname}")

                # Recalculate offsets based on clamped global to stay in sync
                # This is necessary when global is clamped but clients continue to move
                for hostname in hostnames:
                    client_volume = self._client_volume_db.get(hostname, self._global_volume_db)
                    self._client_offset_db[hostname] = client_volume - self._global_volume_db

                return True

            except Exception as e:
                self.logger.error(f"Error applying DSP delta: {e}")
                return False

    async def set_client_volume_db(self, hostname: str, volume_db: float) -> bool:
        """
        Set volume for a specific client (from MultiroomModal slider).
        Updates the client's offset relative to global volume.

        Args:
            hostname: Client hostname or 'local'
            volume_db: Target volume in dB

        Returns:
            True if successful
        """
        async with self._state_lock:
            try:
                import aiohttp

                clamped = self.converter.clamp_db(volume_db)

                # Calculate and store the offset
                offset = clamped - self._global_volume_db
                self._client_offset_db[hostname] = offset
                self._client_volume_db[hostname] = clamped

                # Apply to client
                if hostname == 'local':
                    if self._dsp_service:
                        return await self._dsp_service.set_volume(clamped)
                    return False

                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                    result = await self._set_client_dsp_volume(session, hostname, clamped)
                    # Note: _set_client_dsp_volume already queues pending settings on failure
                    return result

            except Exception as e:
                self.logger.error(f"Error setting client volume for {hostname}: {e}")
                # Queue the volume for when client reconnects
                await self._queue_pending_volume(hostname, self.converter.clamp_db(volume_db))
                return False

    def get_client_volume_db(self, hostname: str) -> float:
        """Get cached volume for a specific client."""
        return self._client_volume_db.get(hostname, self._global_volume_db)

    def get_client_offset_db(self, hostname: str) -> float:
        """Get offset for a specific client."""
        return self._client_offset_db.get(hostname, 0.0)

    def get_average_volume_db(self) -> float:
        """Get average volume across all clients (for VolumeBar display)."""
        if not self._client_volume_db:
            return self._global_volume_db
        volumes = list(self._client_volume_db.values())
        return sum(volumes) / len(volumes)

    async def update_client_volume_db(self, client_id: str, volume_db: float) -> None:
        """Update cached client volume and recalculate offset (called from API routes)."""
        async with self._state_lock:
            self._client_volume_db[client_id] = volume_db
            # Recalculate offset relative to global volume
            self._client_offset_db[client_id] = volume_db - self._global_volume_db

    # ============================================================================
    # CLIENT SYNCHRONIZATION
    # ============================================================================

    async def sync_all_clients_from_dsp(self) -> bool:
        """
        Fetch DSP volumes from all clients and calculate their offsets.
        Called when multiroom is enabled or at backend startup.

        This ensures volume deltas preserve relative differences between clients.
        """
        async with self._state_lock:
            try:
                # Invalidate snapcast cache to get fresh client list
                self._snapcast_cache_time = 0
                clients = await self._get_snapcast_clients_cached()
                hostnames = self._extract_hostnames(clients)

                if not hostnames:
                    self.logger.info("No clients to sync DSP volumes from")
                    return True

                # Fetch all DSP volumes in parallel
                import aiohttp
                volumes = {}
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                    for hostname in hostnames:
                        volume = await self._fetch_client_dsp_volume(hostname)
                        if volume is not None:
                            volumes[hostname] = volume
                            self.logger.debug(f"Fetched DSP volume for {hostname}: {volume:.1f} dB")

                if not volumes:
                    self.logger.warning("No DSP volumes fetched from any client")
                    return False

                # Use local volume as reference, or first available if local not present
                reference_volume = volumes.get('local', next(iter(volumes.values())))
                self._global_volume_db = reference_volume

                # Calculate and store offsets for all clients
                for hostname, volume in volumes.items():
                    self._client_volume_db[hostname] = volume
                    self._client_offset_db[hostname] = volume - reference_volume

                self.logger.info(
                    f"Synced {len(volumes)} clients from DSP (global={reference_volume:.1f} dB, "
                    f"offsets: {dict((h, f'{o:.1f}') for h, o in self._client_offset_db.items())})"
                )
                return True

            except Exception as e:
                self.logger.error(f"Error syncing clients from DSP: {e}")
                return False

    async def push_volume_to_all_clients(self, volume_db: float) -> bool:
        """
        Push a specific volume to all clients.
        Called when multiroom is activated to ensure uniform volume.

        Args:
            volume_db: Volume to set on all clients (in dB)

        Returns:
            True if successful
        """
        async with self._state_lock:
            try:
                import aiohttp

                clamped = self.converter.clamp_db(volume_db)

                # Set as new global reference (all offsets will be 0)
                self._global_volume_db = clamped

                # Invalidate cache to get fresh client list
                self._snapcast_cache_time = 0
                clients = await self._get_snapcast_clients_cached()
                hostnames = self._extract_hostnames(clients)

                if not hostnames:
                    self.logger.info("No clients to push volume to")
                    return True

                self.logger.info(f"Pushing volume {clamped:.1f} dB to {len(hostnames)} clients")

                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                    tasks = []
                    for hostname in hostnames:
                        tasks.append(self._set_client_dsp_volume(session, hostname, clamped))
                        # Initialize all offsets to 0
                        self._client_volume_db[hostname] = clamped
                        self._client_offset_db[hostname] = 0.0

                    results = await asyncio.gather(*tasks, return_exceptions=True)

                    # Log failures
                    success_count = 0
                    for hostname, result in zip(hostnames, results):
                        if isinstance(result, Exception):
                            self.logger.error(f"Failed to push volume to {hostname}: {result}")
                        elif result:
                            success_count += 1
                        else:
                            self.logger.warning(f"Failed to push volume to {hostname}")

                    self.logger.info(f"Volume pushed to {success_count}/{len(hostnames)} clients")

                # Broadcast event to update frontend
                if self.state_machine:
                    await self.state_machine.broadcast_event("dsp", "client_volumes_pushed", {
                        "volume_db": clamped,
                        "hostnames": hostnames
                    })

                return True

            except Exception as e:
                self.logger.error(f"Error pushing volume to all clients: {e}")
                return False

    # ============================================================================
    # CLIENT INITIALIZATION
    # ============================================================================

    async def initialize_new_client_volume(
        self,
        client_id: str,
        startup_volume_getter: Callable[[], float],
    ) -> bool:
        """
        Initialize new client volume.
        Snapcast is set to 100% passthrough, DSP controls actual volume.

        Args:
            client_id: Snapcast client ID
            startup_volume_getter: Function to get startup volume in dB

        Returns:
            True if successful
        """
        try:
            # Set Snapcast to 100% passthrough
            await self.snapcast_service.set_volume(client_id, 100)

            # Get hostname for this client
            clients = await self._get_snapcast_clients_cached()
            hostname = None
            for client in clients:
                if client.get("id") == client_id:
                    hostname = self._get_hostname_from_client(client)
                    break

            # Initialize with average volume of existing clients
            avg_volume = self.get_average_volume_db()

            if hostname:
                self._client_volume_db[hostname] = avg_volume
                self._client_offset_db[hostname] = avg_volume - self._global_volume_db

            self.logger.info(f"Client {client_id} initialized: Snapcast=100%, DSP={avg_volume:.1f} dB (avg)")
            return True

        except Exception as e:
            self.logger.error(f"Error initializing client {client_id}: {e}")
            return False

    async def sync_existing_client_from_snapcast(self, client_id: str) -> bool:
        """
        Sync existing client - ensure Snapcast is at 100%.

        Args:
            client_id: Snapcast client ID

        Returns:
            True if successful
        """
        try:
            # Ensure Snapcast is at 100% passthrough
            await self.snapcast_service.set_volume(client_id, 100)

            # Get hostname and fetch current DSP volume
            clients = await self._get_snapcast_clients_cached()
            for client in clients:
                if client.get("id") == client_id:
                    hostname = self._get_hostname_from_client(client)
                    if hostname and hostname not in self._client_volume_db:
                        # Fetch actual DSP volume from client
                        volume = await self._fetch_client_dsp_volume(hostname)
                        if volume is not None:
                            self._client_volume_db[hostname] = volume
                            self._client_offset_db[hostname] = volume - self._global_volume_db
                    break

            return True
        except Exception as e:
            self.logger.error(f"Error syncing client {client_id}: {e}")
            return False

    async def sync_client_volume_from_external(
        self,
        client_id: str,
        volume_db: float,
        adjustment_counter: int = 0,
        schedule_broadcast_callback=None,
    ) -> None:
        """Sync client volume from external change (e.g., MultiroomModal)."""
        if adjustment_counter > 0:
            return

        # Get hostname
        clients = await self._get_snapcast_clients_cached()
        hostname = None
        for client in clients:
            if client.get("id") == client_id:
                hostname = self._get_hostname_from_client(client)
                break

        if hostname:
            old = self._client_volume_db.get(hostname)
            if old is not None and abs(old - volume_db) < 0.5:
                return

            self._client_volume_db[hostname] = volume_db
            self._client_offset_db[hostname] = volume_db - self._global_volume_db

            try:
                await self.state_machine.broadcast_event("snapcast", "client_volume_changed", {
                    "client_id": client_id,
                    "hostname": hostname,
                    "volume_db": volume_db,
                    "source": "external_sync"
                })
            except Exception as e:
                self.logger.error(f"Error broadcasting sync: {e}")

        if schedule_broadcast_callback:
            await schedule_broadcast_callback(show_bar=False)

    # ============================================================================
    # STATE MANAGEMENT
    # ============================================================================

    def invalidate_caches(self) -> None:
        """Invalidate all internal caches."""
        self._client_volume_db = {}
        self._client_offset_db = {}
        self._snapcast_clients_cache = []
        self._snapcast_cache_time = 0

    async def get_detailed_status(self) -> dict:
        """Get detailed status with all client volumes."""
        clients = await self._get_snapcast_clients_cached()
        details = []

        for client in clients:
            hostname = self._get_hostname_from_client(client)
            volume = self._client_volume_db.get(hostname, self._global_volume_db)
            offset = self._client_offset_db.get(hostname, 0.0)

            details.append({
                "id": client.get("id"),
                "name": client.get("name", "Unknown"),
                "hostname": hostname,
                "volume_db": volume,
                "offset_db": offset,
                "muted": client.get("muted", False)
            })

        return {
            "global_volume_db": self._global_volume_db,
            "client_count": len(clients),
            "clients": details
        }

    # ============================================================================
    # INTERNAL METHODS
    # ============================================================================

    def _get_hostname_from_client(self, client: Dict) -> str:
        """Extract hostname from Snapcast client."""
        host = client.get("host", "")
        ip = client.get("ip", "")

        if host == 'milo' or ip == '127.0.0.1':
            return 'local'

        # Prefer IP for reliable connectivity
        if ip and ip != '127.0.0.1':
            return ip
        elif host:
            return host.split('.')[0] if '.' in host else host

        return client.get("id", "unknown")

    def _extract_hostnames(self, clients: List[Dict]) -> List[str]:
        """Extract hostnames from all clients."""
        return [self._get_hostname_from_client(c) for c in clients]

    async def _fetch_client_dsp_volume(self, hostname: str) -> Optional[float]:
        """Fetch current DSP volume from a client."""
        try:
            if hostname == 'local':
                if self._dsp_service:
                    volume_info = await self._dsp_service.get_volume()
                    return volume_info.get("main", -30.0)
                return None

            import aiohttp
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as session:
                url = f"http://{hostname}:{self.CLIENT_DSP_PORT}/dsp/volume"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("main", -30.0)
            return None
        except Exception as e:
            self.logger.debug(f"Error fetching DSP volume from {hostname}: {e}")
            return None

    async def _set_client_dsp_volume(self, session, hostname: str, volume_db: float) -> bool:
        """Set DSP volume on a client."""
        try:
            if hostname == 'local':
                if self._dsp_service:
                    return await self._dsp_service.set_volume(volume_db)
                return False

            url = f"http://{hostname}:{self.CLIENT_DSP_PORT}/dsp/volume"
            async with session.put(url, json={"volume": volume_db}) as resp:
                return resp.status == 200
        except Exception as e:
            # Client is offline - queue the volume setting for when it reconnects
            self.logger.warning(f"Cannot reach client {hostname} for volume update: {e}")
            await self._queue_pending_volume(hostname, volume_db)
            return False

    async def _queue_pending_volume(self, hostname: str, volume_db: float) -> None:
        """Queue volume setting for an offline client via crossover_service."""
        try:
            crossover_service = getattr(self.state_machine, 'crossover_service', None)
            if crossover_service:
                await crossover_service.queue_pending_settings(hostname, "volume", {
                    "volume_db": volume_db
                })
        except Exception as e:
            self.logger.error(f"Error queuing pending volume for {hostname}: {e}")

    async def _get_snapcast_clients_cached(self) -> List[Dict[str, Any]]:
        """Get snapcast clients with short-term caching."""
        current = time.time() * 1000

        if (current - self._snapcast_cache_time) < self.SNAPCAST_CACHE_MS:
            return self._snapcast_clients_cache

        try:
            clients = await self.snapcast_service.get_clients()
            self._snapcast_clients_cache = clients
            self._snapcast_cache_time = current
            return clients
        except Exception:
            return self._snapcast_clients_cache
