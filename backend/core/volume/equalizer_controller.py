# backend/core/volume/equalizer_controller.py
"""
EqualizerController - Hardware Abstraction for Volume Control

Delegates local/remote routing to EqualizerRouter and provides:
- Parallel volume updates with asyncio.gather()
- Timeout and retry logic for transient failures
- Wait for client readiness before sending commands
"""

import asyncio
import logging
import time
from typing import Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from backend.core.multiroom.client_registry import ClientRegistryService
    from backend.core.multiroom.equalizer_router import EqualizerRouter


class EqualizerController:
    """
    Hardware abstraction layer for CamillaDSP volume control.

    Delegates local/remote routing to EqualizerRouter. Adds retry logic,
    parallel updates, and client readiness polling on top.
    """

    DEFAULT_TIMEOUT = 5.0  # seconds
    RETRY_ATTEMPTS = 2
    RETRY_DELAY = 0.5  # seconds

    def __init__(self, camilladsp_service, client_proxy_service, equalizer_router=None, client_registry=None):
        """
        Initialize EqualizerController.

        Args:
            camilladsp_service: Service for local CamillaDSP (used for wait_for_connection)
            client_proxy_service: Service for checking remote client availability
            equalizer_router: EqualizerRouter for local/remote volume routing
            client_registry: Registry for looking up client IPs and locality
        """
        self.logger = logging.getLogger(__name__)
        self._camilladsp_service = camilladsp_service
        self._proxy_service = client_proxy_service
        self._router: Optional["EqualizerRouter"] = equalizer_router
        self._registry: Optional["ClientRegistryService"] = client_registry
        self._timeout = self.DEFAULT_TIMEOUT

    def set_registry(self, registry):
        """Set the client registry (for dependency injection after init)."""
        self._registry = registry

    def set_router(self, router: "EqualizerRouter"):
        """Set the equalizer router (for dependency injection after init)."""
        self._router = router

    def _has_registry(self) -> bool:
        """Check if registry is available for client lookups."""
        return self._registry is not None

    @staticmethod
    def _is_success(result: dict) -> bool:
        """Interpret EqualizerRouter result dict as boolean."""
        if not result:
            return False
        status = result.get("status", "")
        return status in ("success", "skipped")

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
        Set volume for a single client via EqualizerRouter.

        Args:
            mac_id: Client identifier (mac_id from registry)
            volume_db: Target volume in dB
            retry: Current retry attempt (internal)

        Returns:
            True if successful, False otherwise
        """
        try:
            if not self._router:
                return False
            result = await asyncio.wait_for(
                self._router.set_volume(mac_id, volume_db),
                timeout=self._timeout
            )
            return self._is_success(result)

        except asyncio.TimeoutError:
            if retry < self.RETRY_ATTEMPTS:
                await asyncio.sleep(self.RETRY_DELAY)
                return await self.set_equalizer_volume(mac_id, volume_db, retry + 1)
            self.logger.error(f"Timeout setting volume for {mac_id}")
            return False
        except Exception as e:
            self.logger.error(f"Error setting volume for {mac_id}: {e}")
            return False

    async def set_equalizer_mute(self, mac_id: str, mute: bool) -> bool:
        """Set mute state for a client's equalizer via EqualizerRouter."""
        try:
            if not self._router:
                return False
            result = await asyncio.wait_for(
                self._router.set_mute(mac_id, mute),
                timeout=self._timeout
            )
            return self._is_success(result)
        except Exception:
            return False

    async def read_current_volume(self, mac_id: str) -> Optional[float]:
        """Read current volume from hardware via EqualizerRouter."""
        try:
            if not self._router:
                return None
            result = await asyncio.wait_for(
                self._router.get_volume(mac_id),
                timeout=self._timeout
            )
            if result:
                return result.get("main") if result.get("main") is not None else result.get("volume_db")
            return None
        except Exception:
            return None

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

    async def sync_all_from_hardware(self, hostnames: list) -> Dict[str, Optional[float]]:
        """
        Read current volumes from all specified clients.

        Args:
            hostnames: List of client hostnames

        Returns:
            Dict mapping hostname -> volume_db (or None if failed)
        """
        if not hostnames:
            return {}

        self.logger.info(f"Syncing volumes from {len(hostnames)} clients")

        tasks = {
            hostname: asyncio.create_task(self.read_current_volume(hostname))
            for hostname in hostnames
        }

        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        volume_map = {}
        for hostname, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                self.logger.error(f"Exception reading {hostname}: {result}")
                volume_map[hostname] = None
            else:
                volume_map[hostname] = result

        successes = sum(1 for vol in volume_map.values() if vol is not None)
        self.logger.info(f"Sync complete: {successes}/{len(hostnames)} succeeded")

        return volume_map

    # ========== Configuration ==========

    def set_timeout(self, timeout: float) -> None:
        """Set timeout for equalizer operations."""
        self._timeout = timeout
        self.logger.debug(f"Equalizer timeout set to {timeout}s")
