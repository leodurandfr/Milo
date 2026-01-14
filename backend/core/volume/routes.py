# backend/core/volume/routes.py
"""
API routes for volume management - All values in dB (-80 to 0)
"""
from fastapi import APIRouter, HTTPException
from backend.api.models import VolumeSetRequest, VolumeAdjustRequest


router = APIRouter(prefix="/api/volume", tags=["volume"])


def setup_volume_routes(volume_service):
    """
    Configure volume routes with the VolumeService instance.

    Args:
        volume_service: VolumeService instance for volume operations
    """

    @router.get("/status")
    async def get_volume_status():
        """Gets current volume status"""
        try:
            status = await volume_service.get_status()
            return {"status": "success", "data": status}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @router.get("/state")
    async def get_volume_state():
        """Get unified volume state (single source of truth)."""
        try:
            state = await volume_service.get_volume_state()
            return {"status": "success", "data": state.to_dict()}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @router.get("/")
    async def get_current_volume():
        """Gets current volume in dB"""
        try:
            volume_db = await volume_service.get_volume_db()
            return {"status": "success", "volume_db": volume_db}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/set")
    async def set_volume(request: VolumeSetRequest):
        """Sets volume in dB (-80 to 0)"""
        try:
            success = await volume_service.set_volume_db(request.volume_db, show_bar=request.show_bar)

            if success:
                volume_db = await volume_service.get_volume_db()
                return {"status": "success", "volume_db": volume_db}
            else:
                raise HTTPException(status_code=500, detail="Failed to set volume")

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/adjust")
    async def adjust_volume(request: VolumeAdjustRequest):
        """Adjusts volume by delta in dB"""
        try:
            success = await volume_service.adjust_volume_db(request.delta_db, show_bar=request.show_bar)

            if success:
                volume_db = await volume_service.get_volume_db()
                return {"status": "success", "volume_db": volume_db, "delta_db": request.delta_db}
            else:
                raise HTTPException(status_code=500, detail="Failed to adjust volume")

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/increase")
    async def increase_volume():
        """Increases volume by configured step (default 3 dB)"""
        try:
            step_db = volume_service.config.config.step_mobile_db
            success = await volume_service.adjust_volume_db(step_db)
            if success:
                volume_db = await volume_service.get_volume_db()
                return {"status": "success", "volume_db": volume_db, "delta_db": step_db}
            else:
                raise HTTPException(status_code=500, detail="Failed to increase volume")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/decrease")
    async def decrease_volume():
        """Decreases volume by configured step (default 3 dB)"""
        try:
            step_db = volume_service.config.config.step_mobile_db
            success = await volume_service.adjust_volume_db(-step_db)
            if success:
                volume_db = await volume_service.get_volume_db()
                return {"status": "success", "volume_db": volume_db, "delta_db": -step_db}
            else:
                raise HTTPException(status_code=500, detail="Failed to decrease volume")
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ============================================================================
    # ATOMIC ZONE OPERATIONS
    # ============================================================================

    @router.post("/zone/{zone_id}/delta")
    async def apply_zone_delta(zone_id: str, request: VolumeAdjustRequest):
        """
        Apply volume delta to entire zone atomically.

        This endpoint solves the race condition by:
        1. Calculating updates for ALL clients in the zone
        2. Applying them in parallel via DSPController
        3. Broadcasting complete state ONCE after all updates succeed

        Args:
            zone_id: Zone identifier (from ClientRegistryService)
            request: Delta in dB to apply to zone

        Returns:
            New zone average and status
        """
        try:
            # Apply delta atomically using new architecture
            new_average = await volume_service.apply_zone_volume_delta(zone_id, request.delta_db)

            # Get zone info for response
            volume_state = await volume_service.get_volume_state()
            zone = volume_state.zones.get(zone_id)

            if not zone:
                raise HTTPException(status_code=404, detail=f"Zone {zone_id} not found after update")

            clients_updated = len([
                cid for cid in zone.client_ids
                if cid in volume_state.clients and volume_state.clients[cid].available
            ])

            return {
                "status": "success",
                "zone_id": zone_id,
                "new_average_db": new_average,
                "delta_db": request.delta_db,
                "clients_updated": clients_updated
            }

        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error applying zone delta: {str(e)}")

    @router.get("/zone/{zone_id}")
    async def get_zone_info(zone_id: str):
        """
        Get current zone information.

        Returns zone details including average volume, clients, mute status.
        """
        try:
            volume_state = await volume_service.get_volume_state()
            zone = volume_state.zones.get(zone_id)

            if not zone:
                raise HTTPException(status_code=404, detail=f"Zone {zone_id} not found")

            return {"status": "success", "data": zone.to_dict()}

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    return router


def create_volume_router(volume_service):
    """
    Legacy function for backward compatibility.
    Creates and configures the volume router.

    Args:
        volume_service: VolumeService instance

    Returns:
        Configured router
    """
    setup_volume_routes(volume_service)
    return router
