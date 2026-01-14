# backend/core/volume/dsp_controller.py
"""
DSPController - Hardware Abstraction for Volume Control

This service handles all interactions with audio hardware (CamillaDSP).
It abstracts local vs remote clients and provides parallel updates with error handling.

Key features:
- Routes volume commands to local DSP or remote clients
- Parallel updates with asyncio.gather()
- Timeout and error handling
- Retry logic for transient failures
- Wait for client readiness before sending commands
"""

import asyncio
import logging
import time
from typing import Dict, Optional


class DSPController:
    """
    Hardware abstraction layer for CamillaDSP volume control.

    Responsibilities:
    - Route volume commands to correct destination (local or remote)
    - Execute parallel volume updates for zones
    - Handle errors gracefully with retry logic
    - Provide timeout protection
    """

    DEFAULT_TIMEOUT = 5.0  # seconds
    RETRY_ATTEMPTS = 2
    RETRY_DELAY = 0.5  # seconds

    def __init__(self, camilladsp_service, client_proxy_service):
        """
        Initialize DSPController.

        Args:
            camilladsp_service: Service for controlling local CamillaDSP
            client_proxy_service: Service for controlling remote clients
        """
        self.logger = logging.getLogger(self.__class__.__name__)
        self._dsp_service = camilladsp_service
        self._proxy_service = client_proxy_service
        self._timeout = self.DEFAULT_TIMEOUT

        self.logger.info("DSPController initialized")

    # ========== Client Readiness ==========

    async def wait_for_client_ready(self, hostname: str, max_wait: float = 10.0, interval: float = 0.5) -> bool:
        """
        Wait for a client's DSP to become available.

        For local client: waits for CamillaDSP daemon connection.
        For remote clients: polls health endpoint until dsp_ready is true.

        Args:
            hostname: Client hostname ('local' or 'milo-client-XX')
            max_wait: Maximum wait time in seconds
            interval: Check interval in seconds

        Returns:
            True if client became ready, False if timeout
        """
        if hostname == "local":
            # Wait for local CamillaDSP daemon to be connected
            if self._dsp_service and hasattr(self._dsp_service, 'wait_for_connection'):
                ready = await self._dsp_service.wait_for_connection(timeout=max_wait)
                if ready:
                    self.logger.info(f"[{time.time():.3f}] WAIT_READY: Local CamillaDSP ready")
                else:
                    self.logger.warning(f"[{time.time():.3f}] WAIT_READY: Local CamillaDSP not ready after {max_wait}s")
                return ready
            # Fallback: if no service or method, assume ready
            return True

        start_time = time.time()
        attempts = 0

        while (time.time() - start_time) < max_wait:
            attempts += 1
            try:
                if await self._proxy_service.check_available(hostname):
                    elapsed = time.time() - start_time
                    self.logger.info(f"[{time.time():.3f}] WAIT_READY: {hostname} ready after {attempts} attempts ({elapsed:.1f}s)")
                    return True
            except Exception as e:
                self.logger.debug(f"Health check failed for {hostname}: {e}")

            await asyncio.sleep(interval)

        self.logger.warning(f"[{time.time():.3f}] WAIT_READY: {hostname} not ready after {max_wait}s ({attempts} attempts)")
        return False

    # ========== Single Client Operations ==========

    async def set_dsp_volume(self, hostname: str, volume_db: float, retry: int = 0) -> bool:
        """
        Set volume for a single client (local or remote).

        Args:
            hostname: Client hostname ('local' or 'milo-client-XX')
            volume_db: Target volume in dB
            retry: Current retry attempt (internal)

        Returns:
            True if successful, False otherwise
        """
        try:
            if hostname == "local":
                # Local CamillaDSP
                return await self._set_local_volume(volume_db)
            else:
                # Remote client via proxy
                return await self._set_remote_volume(hostname, volume_db)

        except asyncio.TimeoutError:
            self.logger.warning(f"Timeout setting volume for {hostname} (attempt {retry + 1}/{self.RETRY_ATTEMPTS})")

            if retry < self.RETRY_ATTEMPTS:
                await asyncio.sleep(self.RETRY_DELAY)
                return await self.set_dsp_volume(hostname, volume_db, retry + 1)

            return False

        except Exception as e:
            self.logger.error(f"Error setting volume for {hostname}: {e}", exc_info=True)
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
                self._dsp_service.set_volume(volume_db),
                timeout=self._timeout
            )

            if result:
                self.logger.debug(f"Local DSP volume set to {volume_db:.1f}dB")
                return True
            else:
                self.logger.warning(f"Local DSP volume update failed")
                return False

        except Exception as e:
            self.logger.error(f"Error setting local DSP volume: {e}")
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
                    "/dsp/volume",
                    {"volume": volume_db}
                ),
                timeout=self._timeout
            )

            if result and result.get("status") == "success":
                self.logger.debug(f"Remote DSP ({hostname}) volume set to {volume_db:.1f}dB")
                return True
            else:
                self.logger.warning(f"Remote DSP ({hostname}) volume update failed: {result}")
                return False

        except Exception as e:
            self.logger.error(f"Error setting remote DSP volume ({hostname}): {e}")
            raise

    async def set_dsp_mute(self, hostname: str, mute: bool) -> bool:
        """
        Set mute state for a client's DSP.

        Args:
            hostname: Client hostname ('local' or 'milo-client-XX')
            mute: True to mute, False to unmute

        Returns:
            True if successful, False otherwise
        """
        try:
            if hostname == "local":
                result = await asyncio.wait_for(
                    self._dsp_service.set_mute(mute),
                    timeout=self._timeout
                )
                if result:
                    self.logger.debug(f"Local DSP mute set to {mute}")
                    return True
                return False
            else:
                result = await asyncio.wait_for(
                    self._proxy_service.request(
                        hostname,
                        "PUT",
                        "/dsp/mute",
                        {"muted": mute}
                    ),
                    timeout=self._timeout
                )
                if result and result.get("status") == "success":
                    self.logger.debug(f"Remote DSP ({hostname}) mute set to {mute}")
                    return True
                return False

        except asyncio.TimeoutError:
            self.logger.warning(f"Timeout setting mute for {hostname}")
            return False
        except Exception as e:
            self.logger.error(f"Error setting mute for {hostname}: {e}")
            return False

    # ========== Parallel Zone Operations ==========

    async def apply_volumes_parallel(self, updates: Dict[str, float]) -> Dict[str, bool]:
        """
        Apply volume updates to multiple clients in parallel.

        This is the core method for atomic zone updates.

        Args:
            updates: Dict mapping hostname -> volume_db

        Returns:
            Dict mapping hostname -> success (True/False)

        Example:
            updates = {
                "local": -25.0,
                "milo-client-01": -27.0,
                "milo-client-02": -23.0
            }
            results = await controller.apply_volumes_parallel(updates)
            # results = {"local": True, "milo-client-01": True, "milo-client-02": False}
        """
        if not updates:
            self.logger.debug("No volume updates to apply")
            return {}

        self.logger.info(f"Applying parallel volume updates to {len(updates)} clients")

        # First, check availability of remote clients in parallel (fast fail for unreachable clients)
        remote_clients = [h for h in updates.keys() if h != "local"]
        available_map = {"local": True}  # Local is always available

        if remote_clients:
            availability_tasks = {
                hostname: asyncio.create_task(self._proxy_service.check_available(hostname))
                for hostname in remote_clients
            }
            availability_results = await asyncio.gather(*availability_tasks.values(), return_exceptions=True)
            for hostname, available in zip(availability_tasks.keys(), availability_results):
                if isinstance(available, Exception):
                    self.logger.warning(f"Availability check failed for {hostname}: {available}")
                    available_map[hostname] = False
                else:
                    available_map[hostname] = available

        # Filter updates to only available clients
        available_updates = {h: v for h, v in updates.items() if available_map.get(h, False)}
        unavailable_clients = [h for h in updates.keys() if not available_map.get(h, False)]

        if unavailable_clients:
            self.logger.warning(f"Skipping unavailable clients: {unavailable_clients}")

        # Initialize success_map with failures for unavailable clients
        success_map = {h: False for h in unavailable_clients}

        if not available_updates:
            self.logger.warning("No available clients to update")
            return success_map

        # Create tasks for available updates only
        tasks = {
            hostname: asyncio.create_task(self.set_dsp_volume(hostname, volume))
            for hostname, volume in available_updates.items()
        }

        # Wait for all tasks to complete (with exception handling)
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        # Map results back to hostnames
        for hostname, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                self.logger.error(f"Exception updating {hostname}: {result}")
                success_map[hostname] = False
            else:
                success_map[hostname] = result

        # Log summary
        successes = sum(1 for success in success_map.values() if success)
        self.logger.info(f"Parallel update complete: {successes}/{len(updates)} succeeded")

        return success_map

    # ========== Synchronization ==========

    async def read_current_volume(self, hostname: str) -> Optional[float]:
        """
        Read current volume from hardware.

        Args:
            hostname: Client hostname

        Returns:
            Current volume in dB, or None if failed
        """
        try:
            if hostname == "local":
                # Read from local CamillaDSP
                volume = await asyncio.wait_for(
                    self._dsp_service.get_volume(),
                    timeout=self._timeout
                )
                return volume

            else:
                # Read from remote client
                result = await asyncio.wait_for(
                    self._proxy_service.request(
                        hostname,
                        "GET",
                        "/dsp/volume",
                        None
                    ),
                    timeout=self._timeout
                )

                if result and "volume_db" in result:
                    return result["volume_db"]

                return None

        except asyncio.TimeoutError:
            self.logger.warning(f"Timeout reading volume from {hostname}")
            return None

        except Exception as e:
            self.logger.error(f"Error reading volume from {hostname}: {e}")
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
        Set timeout for DSP operations.

        Args:
            timeout: Timeout in seconds
        """
        self._timeout = timeout
        self.logger.debug(f"DSP timeout set to {timeout}s")
