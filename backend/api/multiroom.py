# backend/api/multiroom.py
"""
API routes for multiroom client and zone management.

Provides endpoints under /api/multiroom/ prefix for multiroom operations.
This router delegates to ClientRegistryService for all operations.

Added in Story 2.2 to provide:
- /api/multiroom/clients/ prefix for client endpoints (PATCH updates)
- /api/multiroom/zones/ prefix for zone CRUD operations

This is the canonical API for zone management. The /api/registry/zones
endpoints are deprecated in favor of these endpoints.

Features:
- PATCH method for partial updates
- Pydantic validation with meaningful error messages
- UUID auto-generation for zone creation
- Enriched zone responses with computed fields
"""
import logging
import uuid
from typing import Optional, Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from backend.core.multiroom.models import SPEAKER_TYPES
from backend.api.models import ZoneCreate, ZoneUpdate, ZoneAddClient

logger = logging.getLogger(__name__)


# === REQUEST/RESPONSE MODELS ===

class ClientUpdateRequest(BaseModel):
    """Request to update client properties (name and/or speaker_type)."""
    name: Optional[str] = None
    speaker_type: Optional[Literal['satellite', 'bookshelf', 'tower', 'subwoofer']] = None

    @field_validator('speaker_type')
    @classmethod
    def validate_speaker_type(cls, v):
        """Validate speaker_type against allowed values."""
        if v is not None and v not in SPEAKER_TYPES:
            raise ValueError(
                f"Invalid speaker_type '{v}'. "
                f"Must be one of: {', '.join(SPEAKER_TYPES)}"
            )
        return v


def create_multiroom_router(registry_service):
    """
    Creates multiroom router with dependency injection.

    Provides /api/multiroom/clients/ endpoints per architecture specification.
    Delegates all operations to ClientRegistryService.
    """
    router = APIRouter(prefix="/api/multiroom", tags=["multiroom"])

    def _client_with_online(client):
        """Helper to include runtime 'online' status in client dict."""
        data = client.to_dict()
        data["online"] = client.online
        return data

    # === STATE ENDPOINT ===

    @router.get("/state")
    async def get_state():
        """
        Get complete registry state (all clients and zones).

        Used for initial frontend sync. This is the canonical endpoint
        for fetching the full multiroom state. Replaces /api/registry/state.

        Returns:
            {
                "clients": {mac_id: {...}, ...} with runtime 'online' status,
                "zones": {zone_id: {...}, ...} with enriched computed fields
            }
        """
        try:
            # Get clients indexed by mac_id
            clients = registry_service.get_all_clients()
            clients_dict = {
                mac_id: _client_with_online(client)
                for mac_id, client in clients.items()
            }

            # Get zones indexed by zone_id with enriched fields
            zones = registry_service.get_all_zones()
            zones_dict = {
                zone_id: registry_service.zone_to_enriched_dict(zone)
                for zone_id, zone in zones.items()
            }

            return {"clients": clients_dict, "zones": zones_dict}
        except Exception as e:
            logger.error(f"Error getting registry state: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # === CLIENT ENDPOINTS ===

    @router.get("/clients")
    async def get_clients():
        """
        Get all registered clients.

        Returns:
            {"clients": [...]} with each client including runtime 'online' status
        """
        try:
            clients = registry_service.get_all_clients()
            return {
                "clients": [_client_with_online(c) for c in clients.values()]
            }
        except Exception as e:
            logger.error(f"Error getting clients: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/clients/{mac_id}")
    async def get_client(mac_id: str):
        """
        Get a specific client by mac_id.

        Returns:
            Client data with runtime 'online' status

        Raises:
            404: Client not found
        """
        try:
            client = registry_service.get_client(mac_id)
            if not client:
                raise HTTPException(
                    status_code=404,
                    detail=f"Client with mac_id '{mac_id}' not found"
                )
            return _client_with_online(client)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting client {mac_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.patch("/clients/{mac_id}")
    async def update_client(mac_id: str, request: ClientUpdateRequest):
        """
        Update client properties (name and/or speaker_type).

        Supports partial updates - only provided fields are updated.
        Broadcasts 'client_updated' WebSocket event on success.

        Args:
            mac_id: Client MAC address
            request: Partial update with optional name and speaker_type

        Returns:
            {"status": "success", "client": {...}} with updated client data

        Raises:
            404: Client not found
            400: Invalid speaker_type (validation handled by Pydantic)
        """
        try:
            client = registry_service.get_client(mac_id)
            if not client:
                raise HTTPException(
                    status_code=404,
                    detail=f"Client with mac_id '{mac_id}' not found"
                )

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

    @router.put("/clients/{mac_id}")
    async def update_client_put(mac_id: str, request: ClientUpdateRequest):
        """
        Update client properties (PUT alias for PATCH).

        Provides backward compatibility with PUT method.
        Behaves identically to PATCH endpoint.
        """
        return await update_client(mac_id, request)

    @router.delete("/clients/{mac_id}")
    async def delete_client(mac_id: str):
        """
        Permanently delete a client from the registry.

        Removes the client from any zone it belongs to and clears all
        persisted configuration. Use this for offline clients that are
        no longer needed.

        Args:
            mac_id: Client MAC address

        Returns:
            {"status": "success", "message": "Client deleted"}

        Raises:
            404: Client not found
        """
        try:
            success = await registry_service.unregister_client(mac_id)
            if not success:
                raise HTTPException(
                    status_code=404,
                    detail=f"Client with mac_id '{mac_id}' not found"
                )
            return {
                "status": "success",
                "message": f"Client '{mac_id}' deleted"
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error deleting client {mac_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # === ZONE ENDPOINTS ===

    @router.get("/zones")
    async def get_zones():
        """
        Get all zones with enriched data.

        Returns:
            {"zones": [...]} with each zone including computed fields:
            - online_client_count: Number of currently online clients
            - has_subwoofer: Whether zone has a subwoofer client
            - crossover_enabled: Whether crossover is active
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
        """
        Get a specific zone by ID with enriched data.

        Returns:
            Zone data with computed fields (online_client_count, has_subwoofer, crossover_enabled)

        Raises:
            404: Zone not found
        """
        try:
            zone = registry_service.get_zone(zone_id)
            if not zone:
                raise HTTPException(
                    status_code=404,
                    detail=f"Zone '{zone_id}' not found"
                )
            return registry_service.zone_to_enriched_dict(zone)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting zone {zone_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/zones", status_code=201)
    async def create_zone(request: ZoneCreate):
        """
        Create a new zone with specified clients.

        Requires at least 2 valid client mac_ids.
        Broadcasts 'zone_created' WebSocket event on success.
        Clients are assigned to the zone and receive shared DSP settings.

        Args:
            request: Zone creation request with name and client_ids

        Returns:
            {"status": "success", "zone": {...}} with zone data including computed fields

        Raises:
            400: Less than 2 clients, client not found, or validation error
        """
        try:
            zone_id = str(uuid.uuid4())

            zone = await registry_service.create_zone(
                zone_id=zone_id,
                name=request.name,
                client_ids=request.client_ids
            )
            return {
                "status": "success",
                "zone": registry_service.zone_to_enriched_dict(zone)
            }
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"Error creating zone: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.patch("/zones/{zone_id}")
    async def update_zone(zone_id: str, request: ZoneUpdate):
        """
        Update zone properties (currently only name).

        Supports partial updates - only provided fields are updated.
        Broadcasts 'zone_updated' WebSocket event on success.

        Args:
            zone_id: The zone's unique identifier
            request: Partial update with optional name

        Returns:
            {"status": "success", "zone": {...}} with updated zone data

        Raises:
            404: Zone not found
            400: Validation error (e.g., name too long)
        """
        try:
            zone = await registry_service.update_zone(zone_id, name=request.name)
            if not zone:
                raise HTTPException(
                    status_code=404,
                    detail=f"Zone '{zone_id}' not found"
                )
            return {
                "status": "success",
                "zone": registry_service.zone_to_enriched_dict(zone)
            }
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"Error updating zone {zone_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete("/zones/{zone_id}")
    async def delete_zone(zone_id: str):
        """
        Delete a zone.

        When deleted:
        - All member clients have their zone_id set to None
        - Clients retain zone DSP settings as their standalone DSP (FR14)
        - 'zone_deleted' WebSocket event is broadcast

        Args:
            zone_id: The zone's unique identifier

        Returns:
            {"status": "success", "message": "Zone deleted"}

        Raises:
            404: Zone not found
        """
        try:
            success = await registry_service.delete_zone(zone_id)
            if not success:
                raise HTTPException(
                    status_code=404,
                    detail=f"Zone '{zone_id}' not found"
                )
            return {
                "status": "success",
                "message": f"Zone '{zone_id}' deleted"
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error deleting zone {zone_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # === ZONE MEMBERSHIP ENDPOINTS ===

    @router.post("/zones/{zone_id}/clients", status_code=200)
    async def add_client_to_zone(zone_id: str, request: ZoneAddClient):
        """
        Add a client to a zone. Client's DSP is replaced by zone's (FR15).

        The client will adopt the zone's shared DSP settings.
        If the client was in another zone, it is removed from that zone first.
        Broadcasts 'zone_updated' WebSocket event on success.

        Args:
            zone_id: The zone's unique identifier
            request: Request body with mac_id of client to add

        Returns:
            {"status": "success", "zone": {...}} with updated zone data

        Raises:
            404: Zone not found
            400: Client not found or failed to add
        """
        try:
            zone = registry_service.get_zone(zone_id)
            if not zone:
                raise HTTPException(
                    status_code=404,
                    detail=f"Zone '{zone_id}' not found"
                )

            client = registry_service.get_client(request.mac_id)
            if not client:
                raise HTTPException(
                    status_code=400,
                    detail=f"Client '{request.mac_id}' not found"
                )

            success = await registry_service.add_client_to_zone(zone_id, request.mac_id)
            if not success:
                raise HTTPException(
                    status_code=400,
                    detail=f"Client '{request.mac_id}' is already in zone '{zone_id}'"
                )

            zone = registry_service.get_zone(zone_id)
            return {
                "status": "success",
                "zone": registry_service.zone_to_enriched_dict(zone)
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error adding client {request.mac_id} to zone {zone_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete("/zones/{zone_id}/clients/{mac_id}")
    async def remove_client_from_zone(zone_id: str, mac_id: str):
        """
        Remove a client from a zone. Client keeps zone DSP as standalone (FR14).

        The client retains the zone's DSP settings as its standalone settings.
        If the zone has less than 2 clients after removal, the zone is deleted.
        Broadcasts 'zone_updated' or 'zone_deleted' WebSocket event.

        Args:
            zone_id: The zone's unique identifier
            mac_id: MAC address of client to remove

        Returns:
            {"status": "success", "zone": {...}} if zone still exists
            {"status": "success", "message": "Zone deleted"} if zone was deleted

        Raises:
            404: Zone not found
            400: Client not found in zone
        """
        try:
            zone = registry_service.get_zone(zone_id)
            if not zone:
                raise HTTPException(
                    status_code=404,
                    detail=f"Zone '{zone_id}' not found"
                )

            if mac_id not in zone.client_ids:
                raise HTTPException(
                    status_code=400,
                    detail=f"Client '{mac_id}' is not in zone '{zone_id}'"
                )

            success = await registry_service.remove_client_from_zone(zone_id, mac_id)
            if not success:
                raise HTTPException(
                    status_code=400,
                    detail=f"Failed to remove client '{mac_id}' from zone"
                )

            # Zone may have been deleted if < 2 clients remain
            zone = registry_service.get_zone(zone_id)
            if zone:
                return {
                    "status": "success",
                    "zone": registry_service.zone_to_enriched_dict(zone)
                }
            else:
                return {
                    "status": "success",
                    "message": f"Client removed, zone '{zone_id}' deleted (< 2 clients)"
                }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error removing client {mac_id} from zone {zone_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    return router
