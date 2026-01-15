# backend/presentation/api/routes/registry.py
"""
API routes for ClientRegistryService - centralized client/zone management.
"""
import logging
from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# === REQUEST/RESPONSE MODELS ===

class ZoneCreateRequest(BaseModel):
    """Request to create a new zone."""
    id: str
    name: str
    client_ids: List[str] = []


class ZoneUpdateRequest(BaseModel):
    """Request to update zone properties."""
    name: Optional[str] = None
    crossover_frequency: Optional[int] = None
    crossover_enabled: Optional[bool] = None


class ZoneClientsRequest(BaseModel):
    """Request to set zone clients."""
    client_ids: List[str]


class ClientTypeRequest(BaseModel):
    """Request to update client speaker type."""
    speaker_type: str
    crossover_frequency: Optional[int] = None


def create_registry_router(registry_service):
    """Creates registry router with dependency injection."""
    router = APIRouter(prefix="/api/registry", tags=["registry"])

    # === STATE ENDPOINTS ===

    @router.get("/state")
    async def get_registry_state():
        """
        Get complete registry state (all clients and zones).
        Used for initial frontend sync.
        """
        try:
            return registry_service.get_state_dict()
        except Exception as e:
            logger.error(f"Error getting registry state: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # === CLIENT ENDPOINTS ===

    @router.get("/clients")
    async def get_clients():
        """Get all registered clients."""
        try:
            clients = registry_service.get_all_clients()
            return {
                "clients": [c.to_dict() for c in clients.values()]
            }
        except Exception as e:
            logger.error(f"Error getting clients: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/clients/available")
    async def get_available_clients():
        """Get only available (connected) clients."""
        try:
            clients = registry_service.get_available_clients()
            return {
                "clients": [c.to_dict() for c in clients]
            }
        except Exception as e:
            logger.error(f"Error getting available clients: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/clients/{dsp_id}")
    async def get_client(dsp_id: str):
        """Get a specific client by dsp_id."""
        try:
            client = registry_service.get_client(dsp_id)
            if not client:
                raise HTTPException(status_code=404, detail=f"Client {dsp_id} not found")
            return client.to_dict()
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting client {dsp_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete("/clients/{dsp_id}")
    async def delete_client(dsp_id: str):
        """
        Permanently delete a client from the registry.
        Removes client from all zones and clears persisted configuration.
        Use this for offline clients that are no longer needed.
        """
        try:
            success = await registry_service.unregister_client(dsp_id)
            if not success:
                raise HTTPException(status_code=404, detail=f"Client {dsp_id} not found")
            return {"status": "success"}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error deleting client {dsp_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/clients/{dsp_id}/available")
    async def check_client_available(dsp_id: str):
        """Check if a specific client is available."""
        try:
            available = registry_service.is_client_available(dsp_id)
            return {"dsp_id": dsp_id, "available": available}
        except Exception as e:
            logger.error(f"Error checking client availability: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.put("/clients/{dsp_id}/type")
    async def update_client_type(dsp_id: str, request: ClientTypeRequest):
        """Update client speaker type."""
        try:
            client = registry_service.get_client(dsp_id)
            if not client:
                raise HTTPException(status_code=404, detail=f"Client {dsp_id} not found")

            await registry_service.update_speaker_type(
                dsp_id,
                request.speaker_type,
                request.crossover_frequency
            )

            return {"status": "success", "dsp_id": dsp_id}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating client type: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # === ZONE ENDPOINTS ===

    @router.get("/zones")
    async def get_zones():
        """Get all zones."""
        try:
            zones = registry_service.get_all_zones()
            return {
                "zones": [registry_service.zone_to_enriched_dict(z) for z in zones.values()]
            }
        except Exception as e:
            logger.error(f"Error getting zones: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/zones/{zone_id}")
    async def get_zone(zone_id: str):
        """Get a specific zone by ID."""
        try:
            zone = registry_service.get_zone(zone_id)
            if not zone:
                raise HTTPException(status_code=404, detail=f"Zone {zone_id} not found")
            return registry_service.zone_to_enriched_dict(zone)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting zone {zone_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/zones")
    async def create_zone(request: ZoneCreateRequest):
        """Create a new zone."""
        try:
            zone = await registry_service.create_zone(
                zone_id=request.id,
                name=request.name,
                client_ids=request.client_ids
            )
            return {"status": "success", "zone": registry_service.zone_to_enriched_dict(zone)}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"Error creating zone: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.put("/zones/{zone_id}")
    async def update_zone(zone_id: str, request: ZoneUpdateRequest):
        """Update zone properties."""
        try:
            updates = request.model_dump(exclude_none=True)
            zone = await registry_service.update_zone(zone_id, **updates)

            if not zone:
                raise HTTPException(status_code=404, detail=f"Zone {zone_id} not found")

            return {"status": "success", "zone": registry_service.zone_to_enriched_dict(zone)}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating zone {zone_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete("/zones/{zone_id}")
    async def delete_zone(zone_id: str):
        """Delete a zone."""
        try:
            success = await registry_service.delete_zone(zone_id)
            if not success:
                raise HTTPException(status_code=404, detail=f"Zone {zone_id} not found")
            return {"status": "success"}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error deleting zone {zone_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.put("/zones/{zone_id}/clients")
    async def set_zone_clients(zone_id: str, request: ZoneClientsRequest):
        """Set the complete client list for a zone."""
        try:
            zone = await registry_service.set_zone_clients(zone_id, request.client_ids)
            if not zone:
                raise HTTPException(status_code=404, detail=f"Zone {zone_id} not found")
            return {"status": "success", "zone": zone.to_dict()}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error setting zone clients: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/zones/{zone_id}/clients/{dsp_id}")
    async def add_client_to_zone(zone_id: str, dsp_id: str):
        """Add a client to a zone."""
        try:
            success = await registry_service.add_client_to_zone(zone_id, dsp_id)
            if not success:
                raise HTTPException(
                    status_code=400,
                    detail=f"Could not add client {dsp_id} to zone {zone_id}"
                )
            return {"status": "success"}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error adding client to zone: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete("/zones/{zone_id}/clients/{dsp_id}")
    async def remove_client_from_zone(zone_id: str, dsp_id: str):
        """Remove a client from a zone."""
        try:
            success = await registry_service.remove_client_from_zone(zone_id, dsp_id)
            if not success:
                raise HTTPException(
                    status_code=404,
                    detail=f"Client {dsp_id} not found in zone {zone_id}"
                )
            return {"status": "success"}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error removing client from zone: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/zones/{zone_id}/clients")
    async def get_zone_clients(zone_id: str):
        """Get all clients in a zone."""
        try:
            zone = registry_service.get_zone(zone_id)
            if not zone:
                raise HTTPException(status_code=404, detail=f"Zone {zone_id} not found")

            clients = registry_service.get_zone_clients(zone_id)
            return {
                "zone_id": zone_id,
                "clients": [c.to_dict() for c in clients]
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting zone clients: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/zones/{zone_id}/clients/available")
    async def get_zone_available_clients(zone_id: str):
        """Get only available clients in a zone."""
        try:
            zone = registry_service.get_zone(zone_id)
            if not zone:
                raise HTTPException(status_code=404, detail=f"Zone {zone_id} not found")

            clients = registry_service.get_available_zone_clients(zone_id)
            return {
                "zone_id": zone_id,
                "clients": [c.to_dict() for c in clients],
                "has_subwoofer": registry_service.has_available_subwoofer(zone_id)
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting available zone clients: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # === CLIENT ZONE LOOKUP ===

    @router.get("/clients/{dsp_id}/zone")
    async def get_client_zone(dsp_id: str):
        """Get the zone a client belongs to."""
        try:
            zone = registry_service.get_zone_for_client(dsp_id)
            if zone:
                return {"dsp_id": dsp_id, "zone": zone.to_dict()}
            return {"dsp_id": dsp_id, "zone": None}
        except Exception as e:
            logger.error(f"Error getting client zone: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    return router
