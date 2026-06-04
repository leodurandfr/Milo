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

    # ========== Single Client Operations ==========

    async def set_equalizer_volume(self, mac_id: str, volume_db: float, retry: int = 0, force: bool = False) -> bool:
        """
        Set volume for a single client via EqualizerRouter.

        Args:
            mac_id: Client identifier (mac_id from registry)
            volume_db: Target volume in dB
            retry: Current retry attempt (internal)
            force: Bypass online check in router (for reconnection sync)

        Returns:
            True if successful, False otherwise
        """
        try:
            if not self._router:
                self.logger.warning(f"Cannot set volume for {mac_id}: router not configured")
                return False
            result = await asyncio.wait_for(
                self._router.set_volume(mac_id, volume_db, force=force),
                timeout=self._timeout
            )
            return self._is_success(result)

        except asyncio.TimeoutError:
            if retry < self.RETRY_ATTEMPTS:
                await asyncio.sleep(self.RETRY_DELAY)
                return await self.set_equalizer_volume(mac_id, volume_db, retry + 1, force=force)
            self.logger.error(f"Timeout setting volume for {mac_id}")
            return False
        except Exception as e:
            self.logger.warning(f"Failed to set volume for {mac_id}: {e}")
            return False

    async def set_equalizer_mute(self, mac_id: str, mute: bool, force: bool = False) -> bool:
        """Set mute state for a client's equalizer via EqualizerRouter."""
        try:
            if not self._router:
                self.logger.warning(f"Cannot set mute for {mac_id}: router not configured")
                return False
            result = await asyncio.wait_for(
                self._router.set_mute(mac_id, mute, force=force),
                timeout=self._timeout
            )
            return self._is_success(result)
        except Exception as e:
            self.logger.warning(f"Failed to set mute for {mac_id}: {e}")
            return False

    # ========== Parallel Zone Operations ==========

    async def apply_volumes_parallel(self, updates: Dict[str, float]) -> Dict[str, bool]:
        """
        Apply volume updates to multiple clients in parallel.

        Routes through EqualizerRouter, which short-circuits offline clients
        (online flag driven by Snapcast WS) and dispatches to the local
        CamillaDSP or the proxy service. No pre-flight health check is
        performed: an extra round-trip would only add latency and a second
        failure mode on top of the actual set_volume call.

        Args:
            updates: Dict mapping mac_id -> volume_db

        Returns:
            Dict mapping mac_id -> success (True/False)
        """
        if not updates:
            return {}

        self.logger.info(f"Applying parallel volume updates to {len(updates)} clients")

        if not self._has_registry():
            self.logger.warning("Cannot apply parallel volumes: client registry not available")
            return {k: False for k in updates}

        tasks = {mac_id: asyncio.create_task(self.set_equalizer_volume(mac_id, vol))
                 for mac_id, vol in updates.items()}
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        return {
            mac_id: result if not isinstance(result, Exception) else False
            for mac_id, result in zip(tasks.keys(), results)
        }

