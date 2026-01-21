# backend/presentation/api/routes/registry.py
"""
API routes for ClientRegistryService - centralized client/zone management.

DEPRECATED: This router is superseded by /api/multiroom/ which provides
better validation, consistent response format, and is the canonical API.

Migration guide:
- GET /api/registry/state → GET /api/multiroom/state
- GET /api/registry/clients → GET /api/multiroom/clients
- PUT /api/registry/clients/{mac_id} → PATCH /api/multiroom/clients/{mac_id}
- All zone operations → /api/multiroom/zones/*

These endpoints are kept for backward compatibility but should not be
used for new frontend code.
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


class ClientUpdateRequest(BaseModel):
    """Request to update client properties (name and/or speaker_type)."""
    name: Optional[str] = None
    speaker_type: Optional[str] = None


def create_registry_router(registry_service):
    """Creates registry router with dependency injection."""
    router = APIRouter(prefix="/api/registry", tags=["registry"])

    # === STATE ENDPOINTS ===

    @router.get("/state")
    async def get_registry_state():
        """
        Get complete registry state (all clients and zones).
        Used for initial frontend sync.

        DEPRECATED: Use GET /api/multiroom/state instead.
        This endpoint is kept for backward compatibility.
        """
        try:
            state = registry_service.get_state_dict()
            # Add online status to each client (runtime field not in to_dict())
            clients = registry_service.get_all_clients()
            for mac_id, client_data in state["clients"].items():
                if mac_id in clients:
                    client_data["online"] = clients[mac_id].online
            return state
        except Exception as e:
            logger.error(f"Error getting registry state: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # === CLIENT ENDPOINTS ===

    def _client_with_online(client):
        """Helper to include runtime 'online' status in client dict."""
        data = client.to_dict()
        data["online"] = client.online
        return data

    @router.get("/clients")
    async def get_clients():
        """Get all registered clients.

        DEPRECATED: Use GET /api/multiroom/clients instead.
        """
        try:
            clients = registry_service.get_all_clients()
            return {
                "clients": [_client_with_online(c) for c in clients.values()]
            }
        except Exception as e:
            logger.error(f"Error getting clients: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/clients/online")
    async def get_online_clients():
        """Get only online (connected) clients."""
        try:
            clients = registry_service.get_online_clients()
            return {
                "clients": [_client_with_online(c) for c in clients]
            }
        except Exception as e:
            logger.error(f"Error getting online clients: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/clients/{mac_id}")
    async def get_client(mac_id: str):
        """Get a specific client by mac_id.

        DEPRECATED: Use GET /api/multiroom/clients/{mac_id} instead.
        """
        try:
            client = registry_service.get_client(mac_id)
            if not client:
                raise HTTPException(status_code=404, detail=f"Client {mac_id} not found")
            return _client_with_online(client)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting client {mac_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete("/clients/{mac_id}")
    async def delete_client(mac_id: str):
        """
        Permanently delete a client from the registry.
        Removes client from all zones and clears persisted configuration.
        Use this for offline clients that are no longer needed.
        """
        try:
            success = await registry_service.unregister_client(mac_id)
            if not success:
                raise HTTPException(status_code=404, detail=f"Client {mac_id} not found")
            return {"status": "success"}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error deleting client {mac_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/clients/{mac_id}/online")
    async def check_client_online(mac_id: str):
        """Check if a specific client is online."""
        try:
            online = registry_service.is_client_online(mac_id)
            return {"mac_id": mac_id, "online": online}
        except Exception as e:
            logger.error(f"Error checking client online status: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.put("/clients/{mac_id}")
    async def update_client(mac_id: str, request: ClientUpdateRequest):
        """Update client properties (name and/or speaker_type).

        DEPRECATED: Use PATCH /api/multiroom/clients/{mac_id} instead.
        """
        try:
            client = registry_service.get_client(mac_id)
            if not client:
                raise HTTPException(status_code=404, detail=f"Client {mac_id} not found")

            updated_client = await registry_service.update_client(
                mac_id,
                name=request.name,
                speaker_type=request.speaker_type
            )

            return {"status": "success", "client": _client_with_online(updated_client)}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating client {mac_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.put("/clients/{mac_id}/type")
    async def update_client_type(mac_id: str, request: ClientTypeRequest):
        """Update client speaker type (legacy endpoint, prefer PUT /clients/{mac_id})."""
        try:
            client = registry_service.get_client(mac_id)
            if not client:
                raise HTTPException(status_code=404, detail=f"Client {mac_id} not found")

            await registry_service.update_speaker_type(
                mac_id,
                request.speaker_type,
                request.crossover_frequency
            )

            return {"status": "success", "mac_id": mac_id}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating client type: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # === ZONE ENDPOINTS ===
    # DEPRECATED: These zone endpoints are superseded by /api/multiroom/zones
    # which provides better validation and consistent response format.
    # These endpoints are kept for backward compatibility but should not be
    # used for new frontend code. Use /api/multiroom/zones instead.

    @router.get("/zones")
    async def get_zones():
        """Get all zones.

        DEPRECATED: Use GET /api/multiroom/zones instead.
        """
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
        """Get a specific zone by ID.

        DEPRECATED: Use GET /api/multiroom/zones/{zone_id} instead.
        """
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
        """Create a new zone.

        DEPRECATED: Use POST /api/multiroom/zones instead (auto-generates UUID).
        """
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
        """Update zone properties.

        DEPRECATED: Use PATCH /api/multiroom/zones/{zone_id} instead.
        """
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
        """Delete a zone.

        DEPRECATED: Use DELETE /api/multiroom/zones/{zone_id} instead.
        """
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

    @router.post("/zones/{zone_id}/clients/{mac_id}")
    async def add_client_to_zone(zone_id: str, mac_id: str):
        """Add a client to a zone."""
        try:
            success = await registry_service.add_client_to_zone(zone_id, mac_id)
            if not success:
                raise HTTPException(
                    status_code=400,
                    detail=f"Could not add client {mac_id} to zone {zone_id}"
                )
            return {"status": "success"}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error adding client to zone: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete("/zones/{zone_id}/clients/{mac_id}")
    async def remove_client_from_zone(zone_id: str, mac_id: str):
        """Remove a client from a zone."""
        try:
            success = await registry_service.remove_client_from_zone(zone_id, mac_id)
            if not success:
                raise HTTPException(
                    status_code=404,
                    detail=f"Client {mac_id} not found in zone {zone_id}"
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
                "clients": [_client_with_online(c) for c in clients]
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting zone clients: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/zones/{zone_id}/clients/online")
    async def get_zone_online_clients(zone_id: str):
        """Get only online clients in a zone."""
        try:
            zone = registry_service.get_zone(zone_id)
            if not zone:
                raise HTTPException(status_code=404, detail=f"Zone {zone_id} not found")

            clients = registry_service.get_online_zone_clients(zone_id)
            return {
                "zone_id": zone_id,
                "clients": [_client_with_online(c) for c in clients],
                "has_subwoofer": registry_service.has_online_subwoofer(zone_id)
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting online zone clients: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # === CLIENT ZONE LOOKUP ===

    @router.get("/clients/{mac_id}/zone")
    async def get_client_zone(mac_id: str):
        """Get the zone a client belongs to."""
        try:
            zone = registry_service.get_zone_for_client(mac_id)
            if zone:
                return {"mac_id": mac_id, "zone": zone.to_dict()}
            return {"mac_id": mac_id, "zone": None}
        except Exception as e:
            logger.error(f"Error getting client zone: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    return router
