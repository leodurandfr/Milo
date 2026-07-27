"""
API routes for volume management - All values in dB (-80 to 0)
"""
import logging
from typing import Optional, TYPE_CHECKING
from fastapi import APIRouter, HTTPException

from backend.api.route_helpers import api_error_handler
from backend.api.models import (
    VolumeAdjustRequest,
    ClientVolumeRequest,
    ClientMuteRequest,
    VolumeControlRequest,
)
from backend.api.responses import (
    ClientMuteSetResponse,
    ClientVolumeSetResponse,
    VolumeAdjustResponse,
    VolumeControlResponse,
    VolumeStateEnvelope,
    ZoneVolumeDeltaResponse,
)

if TYPE_CHECKING:
    from backend.core.multiroom.client_registry import ClientRegistryService
    from backend.core.volume.service import VolumeService

logger = logging.getLogger(__name__)


def create_volume_router(
    volume_service: "VolumeService",
    client_registry_service: Optional["ClientRegistryService"] = None
):
    """Creates volume router with dependency injection"""
    router = APIRouter(prefix="/api/volume", tags=["volume"])

    @router.get("/state", response_model=VolumeStateEnvelope, response_model_exclude_none=True)
    async def get_volume_state():
        """Get unified volume state (single source of truth)."""
        try:
            state = await volume_service.get_volume_state()
            return {"status": "success", "data": state.to_dict()}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @router.post("/adjust", response_model=VolumeAdjustResponse)
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

    @router.patch("/zone/{zone_id}", response_model=ZoneVolumeDeltaResponse)
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
            if client_registry_service:
                zone = client_registry_service.get_zone(zone_id)
                if not zone:
                    raise HTTPException(status_code=404, detail=f"Zone {zone_id} not found")

            try:
                new_average = await volume_service.apply_zone_volume_delta(zone_id, request.delta_db)
            except ValueError as e:
                raise HTTPException(status_code=404, detail=str(e))

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

    # ============================================================================
    # CLIENT VOLUME OPERATIONS
    # ============================================================================

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

    # ============================================================================
    # MAC ADDRESS BASED CLIENT ENDPOINTS
    # ============================================================================

    @router.patch("/client/mac/{mac_url}", response_model=ClientVolumeSetResponse)
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
                logger.info(f"Setting volume for offline client {mac_id}: will be applied on reconnection")

            await volume_service.update_client_volume_db(mac_id, request.volume_db)

            return {
                "status": "success",
                "mac_id": mac_id,
                "volume_db": request.volume_db
            }

    @router.patch("/client/mac/{mac_url}/mute", response_model=ClientMuteSetResponse)
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

    @router.patch("/volume-control", response_model=VolumeControlResponse)
    async def set_volume_control(request: VolumeControlRequest):
        """Toggle local device volume_control (DAC mode) at runtime."""
        async with api_error_handler("Error setting volume control", logger):
            await volume_service.set_local_volume_control(request.volume_control)
            return {"status": "success", "volume_control": request.volume_control}

    return router
