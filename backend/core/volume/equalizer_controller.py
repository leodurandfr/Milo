# backend/core/volume/equalizer_controller.py
"""
EqualizerController - Hardware Abstraction for Volume Control

This service handles all interactions with audio hardware (CamillaDSP).
It abstracts local vs remote clients and provides parallel updates with error handling.

Key features:
- Routes volume commands to local CamillaDSP or remote clients
- Parallel updates with asyncio.gather()
- Timeout and error handling
- Retry logic for transient failures
- Wait for client readiness before sending commands
"""

import asyncio
import logging
import time
from typing import Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from backend.core.multiroom.client_registry import ClientRegistryService


class EqualizerController:
    """
    Hardware abstraction layer for CamillaDSP volume control.

    Receives mac_id from callers, looks up IP from registry, and routes to proxy.
    """

    DEFAULT_TIMEOUT = 5.0  # seconds
    RETRY_ATTEMPTS = 2
    RETRY_DELAY = 0.5  # seconds

    def __init__(self, camilladsp_service, client_proxy_service, client_registry=None):
        """
        Initialize EqualizerController.

        Args:
            camilladsp_service: Service for controlling local CamillaDSP
            client_proxy_service: Service for controlling remote clients
            client_registry: Registry for looking up client IPs
        """
        self.logger = logging.getLogger(__name__)
        self._camilladsp_service = camilladsp_service
        self._proxy_service = client_proxy_service
        self._registry = client_registry
        self._timeout = self.DEFAULT_TIMEOUT

    def set_registry(self, registry):
        """Set the client registry (for dependency injection after init)."""
        self._registry = registry

    def _has_registry(self) -> bool:
        """Check if registry is available for client lookups."""
        return self._registry is not None

    # ========== Client Readiness ==========

    async def wait_for_client_ready(self, mac_id: str, max_wait: float = 10.0, interval: float = 0.5) -> bool:
        """Wait for a client's equalizer to become available."""
        if not self._has_registry():
            return False
        if self._registry.is_local_client(mac_id):
            if self._camilladsp_service and hasattr(self._camilladsp_service, 'wait_for_connection'):
                return await self._camilladsp_service.wait_for_connection(timeout=max_wait)
            return True

        client_ip = self._registry.get_client_ip(mac_id)
        if not client_ip:
            return False

        start_time = time.time()
        while (time.time() - start_time) < max_wait:
            try:
                if await self._proxy_service.check_available(client_ip):
                    return True
            except Exception:
                pass
            await asyncio.sleep(interval)
        return False

    # ========== Single Client Operations ==========

    async def set_equalizer_volume(self, mac_id: str, volume_db: float, retry: int = 0) -> bool:
        """
        Set volume for a single client (local or remote).

        Args:
            mac_id: Client identifier (mac_id from registry)
            volume_db: Target volume in dB
            retry: Current retry attempt (internal)

        Returns:
            True if successful, False otherwise
        """
        try:
            if not self._has_registry():
                return False
            if self._registry.is_local_client(mac_id):
                return await self._set_local_volume(volume_db)

            # Remote client: get IP from registry
            client_ip = self._registry.get_client_ip(mac_id)
            if not client_ip:
                return False
            return await self._set_remote_volume(client_ip, volume_db)

        except asyncio.TimeoutError:
            if retry < self.RETRY_ATTEMPTS:
                await asyncio.sleep(self.RETRY_DELAY)
                return await self.set_equalizer_volume(mac_id, volume_db, retry + 1)
            self.logger.error(f"Timeout setting volume for {mac_id}")
            return False
        except Exception as e:
            self.logger.error(f"Error setting volume for {mac_id}: {e}")
            return False

    async def _set_local_volume(self, volume_db: float) -> bool:
        """
        Set local CamillaDSP volume.

        Args:
            volume_db: Target volume in dB

        Returns:
            True if successful
        """
        try:
            result = await asyncio.wait_for(
                self._camilladsp_service.set_volume(volume_db),
                timeout=self._timeout
            )

            if result:
                self.logger.debug(f"Local CamillaDSP volume set to {volume_db:.1f}dB")
                return True
            else:
                self.logger.warning(f"Local CamillaDSP volume update failed")
                return False

        except Exception as e:
            self.logger.error(f"Error setting local equalizer volume: {e}")
            raise

    async def _set_remote_volume(self, hostname: str, volume_db: float) -> bool:
        """
        Set remote client volume via proxy.

        Args:
            hostname: Remote client hostname
            volume_db: Target volume in dB

        Returns:
            True if successful
        """
        try:
            result = await asyncio.wait_for(
                self._proxy_service.request(
                    hostname,
                    "PUT",
                    "/equalizer/volume",
                    {"volume": volume_db}
                ),
                timeout=self._timeout
            )

            if result and result.get("status") == "success":
                self.logger.debug(f"Remote CamillaDSP ({hostname}) volume set to {volume_db:.1f}dB")
                return True
            else:
                self.logger.warning(f"Remote CamillaDSP ({hostname}) volume update failed: {result}")
                return False

        except Exception as e:
            self.logger.error(f"Error setting remote equalizer volume ({hostname}): {e}")
            raise

    async def set_equalizer_mute(self, mac_id: str, mute: bool) -> bool:
        """Set mute state for a client's equalizer."""
        try:
            if not self._has_registry():
                return False
            if self._registry.is_local_client(mac_id):
                result = await asyncio.wait_for(self._camilladsp_service.set_mute(mute), timeout=self._timeout)
                return bool(result)

            client_ip = self._registry.get_client_ip(mac_id)
            if not client_ip:
                return False

            result = await asyncio.wait_for(
                self._proxy_service.request(client_ip, "PUT", "/equalizer/mute", {"muted": mute}),
                timeout=self._timeout
            )
            return result and result.get("status") == "success"
        except Exception:
            return False

    # ========== Parallel Zone Operations ==========

    async def apply_volumes_parallel(self, updates: Dict[str, float]) -> Dict[str, bool]:
        """
        Apply volume updates to multiple clients in parallel.

        Args:
            updates: Dict mapping mac_id -> volume_db

        Returns:
            Dict mapping mac_id -> success (True/False)
        """
        if not updates:
            return {}

        self.logger.info(f"Applying parallel volume updates to {len(updates)} clients")

        if not self._has_registry():
            return {k: False for k in updates}

        # Build IP map for remote clients and check availability
        available_map = {}
        for mac_id in updates.keys():
            if self._registry.is_local_client(mac_id):
                available_map[mac_id] = True
            else:
                client_ip = self._registry.get_client_ip(mac_id)
                if client_ip:
                    try:
                        available_map[mac_id] = await self._proxy_service.check_available(client_ip)
                    except Exception:
                        available_map[mac_id] = False
                else:
                    available_map[mac_id] = False

        # Filter to available clients
        available_updates = {k: v for k, v in updates.items() if available_map.get(k)}
        success_map = {k: False for k in updates if not available_map.get(k)}

        if not available_updates:
            return success_map

        # Apply volumes in parallel
        tasks = {mac_id: asyncio.create_task(self.set_equalizer_volume(mac_id, vol))
                 for mac_id, vol in available_updates.items()}
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        for mac_id, result in zip(tasks.keys(), results):
            success_map[mac_id] = result if not isinstance(result, Exception) else False

        return success_map

    # ========== Synchronization ==========

    async def read_current_volume(self, mac_id: str) -> Optional[float]:
        """Read current volume from hardware."""
        try:
            if not self._has_registry():
                return None
            if self._registry.is_local_client(mac_id):
                vol = await asyncio.wait_for(self._camilladsp_service.get_volume(), timeout=self._timeout)
                return vol.get("main") if vol else None

            client_ip = self._registry.get_client_ip(mac_id)
            if not client_ip:
                return None

            result = await asyncio.wait_for(
                self._proxy_service.request(client_ip, "GET", "/equalizer/volume", None),
                timeout=self._timeout
            )
            return result.get("volume_db") if result else None
        except Exception:
            return None

    async def sync_all_from_hardware(self, hostnames: list) -> Dict[str, Optional[float]]:
        """
        Read current volumes from all specified clients.

        Useful for synchronizing state after reconnection.

        Args:
            hostnames: List of client hostnames

        Returns:
            Dict mapping hostname -> volume_db (or None if failed)
        """
        if not hostnames:
            return {}

        self.logger.info(f"Syncing volumes from {len(hostnames)} clients")

        # Create tasks for all reads
        tasks = {
            hostname: asyncio.create_task(self.read_current_volume(hostname))
            for hostname in hostnames
        }

        # Wait for all tasks
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        # Map results back to hostnames
        volume_map = {}
        for hostname, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                self.logger.error(f"Exception reading {hostname}: {result}")
                volume_map[hostname] = None
            else:
                volume_map[hostname] = result

        # Log summary
        successes = sum(1 for vol in volume_map.values() if vol is not None)
        self.logger.info(f"Sync complete: {successes}/{len(hostnames)} succeeded")

        return volume_map

    # ========== Configuration ==========

    def set_timeout(self, timeout: float) -> None:
        """
        Set timeout for equalizer operations.

        Args:
            timeout: Timeout in seconds
        """
        self._timeout = timeout
        self.logger.debug(f"Equalizer timeout set to {timeout}s")
