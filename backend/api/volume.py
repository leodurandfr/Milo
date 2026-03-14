"""
API routes for volume management - All values in dB (-80 to 0)
"""
import logging
from fastapi import APIRouter, HTTPException

from backend.api.route_helpers import api_error_handler
from backend.api.models import (
    VolumeAdjustRequest,
    ClientVolumeRequest,
    ClientMuteRequest,
    VolumeSettingsPatchRequest,
)

logger = logging.getLogger(__name__)


def create_volume_router(volume_service, client_registry_service=None, settings_service=None):
    """Creates volume router with dependency injection"""
    router = APIRouter(prefix="/api/volume", tags=["volume"])

    @router.get("/state")
    async def get_volume_state():
        """Get unified volume state (single source of truth)."""
        try:
            state = await volume_service.get_volume_state()
            return {"status": "success", "data": state.to_dict()}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @router.post("/adjust")
    async def adjust_volume(request: VolumeAdjustRequest):
        """Adjusts volume by delta in dB"""
        async with api_error_handler("Failed to adjust volume"):
            success = await volume_service.adjust_volume_db(request.delta_db, show_bar=request.show_bar)

            if success:
                volume_db = await volume_service.get_volume_db()
                return {"status": "success", "volume_db": volume_db, "delta_db": request.delta_db}
            else:
                raise HTTPException(status_code=500, detail="Failed to adjust volume")

    # ============================================================================
    # MAC ADDRESS UTILITIES
    # ============================================================================

    def _mac_from_url(mac_url: str) -> str:
        """
        Convert MAC from URL format (no colons) to internal format (with colons).

        Example: dca6327ed343 -> dc:a6:32:7e:d3:43

        Args:
            mac_url: MAC address without colons (12 hex characters)

        Returns:
            MAC address with colons

        Raises:
            HTTPException: 400 if invalid format
        """
        if len(mac_url) != 12:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid MAC address format: {mac_url}. Expected 12 hex characters."
            )
        try:
            # Validate it's actually hex
            int(mac_url, 16)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid MAC address format: {mac_url}. Must be hexadecimal."
            )
        # Insert colons every 2 characters
        return ':'.join(mac_url[i:i+2] for i in range(0, 12, 2))

    def _validate_mac_exists(mac_id: str) -> dict:
        """
        Validate MAC address exists in registry.

        Args:
            mac_id: MAC address with colons

        Returns:
            Client object if found

        Raises:
            HTTPException: 404 if not found
        """
        if client_registry_service is None:
            raise HTTPException(
                status_code=500,
                detail="Client registry service not available"
            )
        client = client_registry_service.get_client(mac_id)
        if not client:
            raise HTTPException(
                status_code=404,
                detail=f"Client with MAC {mac_id} not found"
            )
        return client

    # ============================================================================
    # NEW ATOMIC ZONE OPERATIONS (Refactored architecture)
    # ============================================================================

    @router.patch("/zone/{zone_id}")
    async def apply_zone_delta_patch(zone_id: str, request: VolumeAdjustRequest):
        """
        Apply volume delta to entire zone atomically (PATCH method per architecture).

        This endpoint solves the race condition by:
        1. Calculating updates for ALL clients in the zone
        2. Applying them in parallel via EqualizerController
        3. Broadcasting complete state ONCE after all updates succeed

        Args:
            zone_id: Zone identifier (UUID)
            request: Delta in dB to apply to zone

        Returns:
            New zone average, list of affected clients, and offline clients
        """
        async with api_error_handler("Error applying zone delta"):
            # Validate zone exists
            if client_registry_service:
                zone = client_registry_service.get_zone(zone_id)
                if not zone:
                    raise HTTPException(status_code=404, detail=f"Zone {zone_id} not found")

            # Apply delta atomically using new architecture
            try:
                new_average = await volume_service.apply_zone_volume_delta(zone_id, request.delta_db)
            except ValueError as e:
                raise HTTPException(status_code=404, detail=str(e))

            # Get applied/offline client lists
            applied_to = []
            offline_clients = []

            if client_registry_service:
                all_clients = client_registry_service.get_zone_clients(zone_id)
                for client in all_clients:
                    if client.online:
                        applied_to.append(client.mac_id)
                    else:
                        offline_clients.append(client.mac_id)

            return {
                "status": "success",
                "zone_id": zone_id,
                "new_average_db": new_average,
                "delta_db": request.delta_db,
                "applied_to": applied_to,
                "offline_clients": offline_clients
            }

    @router.get("/zone/{zone_id}")
    async def get_zone_info(zone_id: str):
        """
        Get current zone information.

        Returns zone details including average volume, clients, mute status.
        """
        async with api_error_handler("Error getting zone info"):
            volume_state = await volume_service.get_volume_state()
            zone = volume_state.zones.get(zone_id)

            if not zone:
                raise HTTPException(status_code=404, detail=f"Zone {zone_id} not found")

            return {"status": "success", "data": zone.to_dict()}

    # ============================================================================
    # CLIENT VOLUME OPERATIONS
    # ============================================================================

    def _validate_client_exists(client_id: str) -> dict:
        """
        Validate that a client exists in the registry.

        Args:
            client_id: Client equalizer ID (mac_id, e.g., "local" or "dc:a6:32:7e:d3:43")

        Returns:
            Client data dict if found

        Raises:
            HTTPException: 404 if client not found
        """
        if client_registry_service is None:
            # No registry service - allow all client IDs (fallback mode)
            return {"camilladsp_id": client_id}

        client = client_registry_service.get_client_by_camilladsp_id(client_id)
        if not client:
            raise HTTPException(
                status_code=404,
                detail=f"Client '{client_id}' not found in registry"
            )
        return client

    def _validate_volume_limits(volume_db: float) -> float:
        """
        Validate volume against configured limits.

        Args:
            volume_db: Volume in dB

        Returns:
            Clamped volume if valid

        Raises:
            HTTPException: 400 if volume is out of range
        """
        min_db = volume_service.volume_config.limit_min_db
        max_db = volume_service.volume_config.limit_max_db

        if volume_db < min_db or volume_db > max_db:
            raise HTTPException(
                status_code=400,
                detail=f"Volume {volume_db} dB is out of configured range [{min_db}, {max_db}] dB"
            )
        return volume_db

    @router.patch("/client/{client_id}")
    async def set_client_volume(client_id: str, request: ClientVolumeRequest):
        """
        Set volume for a specific client.

        Args:
            client_id: Client equalizer ID (mac_id, e.g., "local" or "dc:a6:32:7e:d3:43")
            request: Volume in dB (-80 to 0)

        Returns:
            Success status with new volume

        Notes:
            - For online clients: Volume is applied immediately to CamillaDSP
            - For offline clients: Volume is persisted and applied when client reconnects
            - A WebSocket event `volume_changed` is broadcast after the update
        """
        async with api_error_handler("Error setting client volume", logger):
            client = _validate_client_exists(client_id)
            _validate_volume_limits(request.volume_db)

            if client.get("status") == "OFFLINE":
                logger.warning(f"Setting volume for offline client {client_id}: will be applied on reconnection")

            await volume_service.update_client_volume_db(client_id, request.volume_db)

            return {
                "status": "success",
                "client_id": client_id,
                "volume_db": request.volume_db
            }

    @router.patch("/client/{client_id}/mute")
    async def set_client_mute(client_id: str, request: ClientMuteRequest):
        """
        Set mute state for a specific client.

        Args:
            client_id: Client equalizer ID (mac_id, e.g., "local" or "dc:a6:32:7e:d3:43")
            request: Mute state (true/false)

        Returns:
            Success status with new mute state

        Notes:
            - For online clients: Mute is applied immediately to CamillaDSP
            - For offline clients: Mute is persisted and applied when client reconnects
            - A WebSocket event `volume_changed` is broadcast after the update
        """
        async with api_error_handler("Error setting client mute", logger):
            _validate_client_exists(client_id)

            await volume_service.set_client_mute(client_id, request.mute)

            return {
                "status": "success",
                "client_id": client_id,
                "mute": request.mute
            }

    @router.get("/client/{client_id}")
    async def get_client_volume(client_id: str):
        """
        Get volume state for a specific client.

        Args:
            client_id: Client equalizer ID (mac_id, e.g., "local" or "dc:a6:32:7e:d3:43")

        Returns:
            Client volume state including volume_db, mute, and online status
        """
        async with api_error_handler("Error getting client volume", logger):
            client = _validate_client_exists(client_id)

            volume_data = await volume_service.get_client_volume(client_id)

            online = client.get("status") == "ONLINE" if client_registry_service else True

            return {
                "status": "success",
                "client_id": client_id,
                "volume_db": volume_data.get("main", -60.0),
                "mute": volume_data.get("mute", False),
                "online": online
            }

    # ============================================================================
    # MAC ADDRESS BASED CLIENT ENDPOINTS (Story 3.4 - AC1, AC3)
    # ============================================================================

    @router.patch("/client/mac/{mac_url}")
    async def set_client_volume_by_mac(mac_url: str, request: ClientVolumeRequest):
        """
        Set volume for a specific client using MAC address.

        Args:
            mac_url: MAC address without colons (e.g., "dca6327ed343")
            request: Volume in dB (-80 to 0)

        Returns:
            Success status with MAC (with colons) and new volume

        Notes:
            - MAC format in URL: no colons (dca6327ed343)
            - MAC format in response: with colons (dc:a6:32:7e:d3:43)
            - A WebSocket event `volume_changed` is broadcast after the update
        """
        async with api_error_handler("Error setting client volume by MAC", logger):
            mac_id = _mac_from_url(mac_url)
            client = _validate_mac_exists(mac_id)
            _validate_volume_limits(request.volume_db)

            if hasattr(client, 'online') and not client.online:
                logger.warning(f"Setting volume for offline client {mac_id}: will be applied on reconnection")

            await volume_service.update_client_volume_db(mac_id, request.volume_db)

            return {
                "status": "success",
                "mac_id": mac_id,
                "volume_db": request.volume_db
            }

    @router.patch("/client/mac/{mac_url}/mute")
    async def set_client_mute_by_mac(mac_url: str, request: ClientMuteRequest):
        """
        Set mute state for a specific client using MAC address.

        Args:
            mac_url: MAC address without colons (e.g., "dca6327ed343")
            request: Mute state (true/false)

        Returns:
            Success status with MAC (with colons) and new mute state

        Notes:
            - MAC format in URL: no colons (dca6327ed343)
            - MAC format in response: with colons (dc:a6:32:7e:d3:43)
            - A WebSocket event is broadcast after the update
        """
        async with api_error_handler("Error setting client mute by MAC", logger):
            mac_id = _mac_from_url(mac_url)
            _validate_mac_exists(mac_id)

            await volume_service.set_client_mute(mac_id, request.mute)

            return {
                "status": "success",
                "mac_id": mac_id,
                "mute": request.mute
            }

    # ============================================================================
    # VOLUME SETTINGS ENDPOINTS (Story 3.4 - AC4, AC5)
    # ============================================================================

    @router.get("/settings")
    async def get_volume_settings():
        """
        Get volume startup settings.

        Returns:
            Current startup_volume_db and restore_last_volume values
        """
        async with api_error_handler("Error getting volume settings", logger):
            return {
                "status": "success",
                "startup_volume_db": volume_service.volume_config.startup_volume_db,
                "restore_last_volume": volume_service.volume_config.restore_last_volume
            }

    @router.patch("/settings")
    async def update_volume_settings(request: VolumeSettingsPatchRequest):
        """
        Update volume startup settings.

        Args:
            request: Partial update with startup_volume_db and/or restore_last_volume

        Returns:
            Success status with updated values

        Notes:
            - Settings are persisted via SettingsService
            - VolumeService config is reloaded after change
        """
        async with api_error_handler("Error updating volume settings", logger):
            if settings_service is None:
                raise HTTPException(
                    status_code=500,
                    detail="Settings service not available"
                )

            if request.startup_volume_db is not None:
                await settings_service.set_setting('volume.startup_volume_db', request.startup_volume_db)

            if request.restore_last_volume is not None:
                await settings_service.set_setting('volume.restore_last_volume', request.restore_last_volume)

            await volume_service.reload_startup_config()

            return {
                "status": "success",
                "startup_volume_db": volume_service.volume_config.startup_volume_db,
                "restore_last_volume": volume_service.volume_config.restore_last_volume
            }

    return router
