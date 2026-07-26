# backend/api/multiroom.py
"""
API routes for multiroom client and zone management.

Provides endpoints under /api/multiroom/ prefix for multiroom operations.
This router delegates to ClientRegistryService for all operations:
- /api/multiroom/clients/ prefix for client endpoints (PATCH updates)
- /api/multiroom/zones/ prefix for zone CRUD operations

Features:
- PATCH method for partial updates
- Pydantic validation with meaningful error messages
- UUID auto-generation for zone creation
- Enriched zone responses with computed fields
"""
import asyncio
import logging
import uuid

import aiohttp
from fastapi import APIRouter, HTTPException, Request

from backend.api.route_helpers import api_error_handler
from backend.api.models import (
    ZoneCreate, ZoneUpdate, ZoneAddClient, ClientUpdateRequest,
    RegisterClientRequest, ConfigurePendingClientRequest,
    ConfigureClientAudioRequest,
)
from backend.api.responses import (
    ClientMutationResponse,
    MultiroomMessageResponse,
    MultiroomPendingClientsResponse,
    MultiroomStateResponse,
    RegisterClientResponse,
    ZoneMutationResponse,
    ZoneOrMessageResponse,
)
from backend.config.constants import CLIENT_API_PORT
from backend.core.multiroom.models import EqualizerSettings

logger = logging.getLogger(__name__)


async def _mark_unreachable(registry_service, mac_id: str, client_ip: str, exc: Exception) -> None:
    """Record a satellite that just failed to answer on CLIENT_API_PORT as offline.

    Snapserver is the normal authority on liveness, but it only notices a client
    once its socket errors — a satellite that dropped off without a TCP FIN stays
    `connected` there for as long as nothing is written to it, so the registry can
    still claim online at the moment a route tries to reach it. A refused HTTP
    request is direct proof to the contrary: flipping the flag here makes the
    frontend render the client as offline immediately instead of waiting for the
    reconcile sweep (SnapcastWebSocketService._reconcile_loop, up to 90s).

    Logged at warning, not error: an unplugged speaker is an expected state, and
    WebSocketLogHandler (level=ERROR) would turn it into a backend-error banner.
    A false negative costs nothing — the same sweep puts the client back online
    once its volume applies again.
    """
    logger.warning(f"Client {mac_id} unreachable at {client_ip}, marking offline: {exc}")
    if registry_service:
        await registry_service.set_client_online(mac_id, False)


async def _send_audio_config_and_reboot(
    mac_id: str, client_ip: str, audio_id: str, overlay: str,
    volume_control: bool = True, registry_service=None,
):
    """Send audio config to a milo-client and reboot it. Raises HTTPException on failure.

    ``registry_service`` is passed only for an already-registered client, so an
    unreachable satellite can be marked offline instead of surfacing as an error
    (see _mark_unreachable). A pending client has no registry entry yet.
    """
    timeout = aiohttp.ClientTimeout(total=10)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # Step 1: Write audio config
            async with session.put(
                f"http://{client_ip}:{CLIENT_API_PORT}/api/hardware/audio",
                json={"audio_id": audio_id, "overlay": overlay, "volume_control": volume_control},
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"Client {mac_id} rejected audio config: {resp.status} {body}")
                    raise HTTPException(
                        status_code=502,
                        detail=f"Client rejected audio configuration: {resp.status}",
                    )

            # Step 2: Reboot
            try:
                async with session.post(f"http://{client_ip}:{CLIENT_API_PORT}/api/hardware/reboot") as resp:
                    if resp.status != 200:
                        logger.warning(f"Client {mac_id} reboot returned {resp.status}")
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(f"Reboot request to {mac_id} failed (may already be rebooting): {e}")
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        await _mark_unreachable(registry_service, mac_id, client_ip, e)
        raise HTTPException(status_code=502, detail=f"Cannot reach client at {client_ip}")


def create_multiroom_router(registry_service, multiroom_equalizer_service=None, pending_clients_service=None, crossover_service=None):
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

    @router.get("/state", response_model=MultiroomStateResponse)
    async def get_state():
        """
        Get complete registry state (all clients and zones).

        Used for initial frontend sync. This is the canonical endpoint
        for fetching the full multiroom state.

        Returns:
            {
                "clients": {mac_id: {...}, ...} with runtime 'online' status,
                "zones": {zone_id: {...}, ...} with enriched computed fields
            }
        """
        async with api_error_handler("Error getting registry state", logger):
            clients = registry_service.get_all_clients()
            clients_dict = {
                mac_id: _client_with_online(client)
                for mac_id, client in clients.items()
            }

            zones = registry_service.get_all_zones()
            zones_dict = {
                zone_id: registry_service.zone_to_enriched_dict(zone)
                for zone_id, zone in zones.items()
            }

            return {"clients": clients_dict, "zones": zones_dict}

    # === CLIENT ENDPOINTS ===

    @router.patch("/clients/{mac_id}", response_model=ClientMutationResponse)
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
        async with api_error_handler(f"Error updating client {mac_id}", logger):
            client = registry_service.get_client(mac_id)
            if not client:
                raise HTTPException(
                    status_code=404,
                    detail=f"Client with mac_id '{mac_id}' not found"
                )

            updated_client = await registry_service.update_client(
                mac_id,
                name=request.name,
                speaker_type=request.speaker_type,
                volume_control=request.volume_control
            )

            return {"status": "success", "client": _client_with_online(updated_client)}

    @router.delete("/clients/{mac_id}", response_model=MultiroomMessageResponse)
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
        async with api_error_handler(f"Error deleting client {mac_id}", logger):
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

    @router.get("/clients/{mac_id}/hardware")
    async def get_client_hardware(mac_id: str):
        """Get hardware configuration from a registered milo-client."""
        async with api_error_handler(f"Error getting hardware for client {mac_id}", logger):
            client = registry_service.get_client(mac_id)
            if not client:
                raise HTTPException(status_code=404, detail=f"Client '{mac_id}' not found")

            timeout = aiohttp.ClientTimeout(total=5)
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(f"http://{client.ip}:{CLIENT_API_PORT}/api/hardware") as resp:
                        if resp.status != 200:
                            raise HTTPException(status_code=502, detail="Client returned an error")
                        return await resp.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                await _mark_unreachable(registry_service, mac_id, client.ip, e)
                raise HTTPException(status_code=502, detail=f"Cannot reach client at {client.ip}")

    @router.put("/clients/{mac_id}/audio", response_model=MultiroomMessageResponse)
    async def configure_client_audio(mac_id: str, request: ConfigureClientAudioRequest):
        """Change audio card on a registered milo-client and reboot it."""
        async with api_error_handler(f"Error configuring audio for client {mac_id}", logger):
            client = registry_service.get_client(mac_id)
            if not client:
                raise HTTPException(status_code=404, detail=f"Client '{mac_id}' not found")

            from backend.hardware.registry import AUDIO_CARDS, is_dac_card
            card_info = AUDIO_CARDS.get(request.audio_id, {})
            overlay = card_info.get("overlay", "")
            # Use explicit value from request, or auto-detect from card category
            volume_control = request.volume_control if request.volume_control is not None else not is_dac_card(request.audio_id)

            await _send_audio_config_and_reboot(
                mac_id, client.ip, request.audio_id, overlay, volume_control,
                registry_service=registry_service,
            )

            # Clear stale pending crossover/EQ settings after hardware confirmed —
            # the new audio card means a fresh CamillaDSP config, so old
            # pending filters would fail (HTTP 400) and are no longer relevant.
            if crossover_service:
                crossover_service.clear_pending_settings(mac_id)

            # Update volume_control in registry after hardware confirmed the new config
            await registry_service.update_client(mac_id, volume_control=volume_control)
            logger.info(f"Client {mac_id} audio changed to {request.audio_id} (volume_control={volume_control}), rebooting")
            return {"status": "success", "message": f"Client {mac_id} audio changed, rebooting"}

    # === ZONE ENDPOINTS ===

    @router.post("/zones", status_code=201, response_model=ZoneMutationResponse)
    async def create_zone(request: ZoneCreate):
        """
        Create a new zone with specified clients.

        Requires at least 2 valid client mac_ids.
        Broadcasts 'zone_created' WebSocket event on success.
        Clients are assigned to the zone and receive shared equalizer settings.

        Args:
            request: Zone creation request with name and client_ids

        Returns:
            {"status": "success", "zone": {...}} with zone data including computed fields

        Raises:
            400: Less than 2 clients, client not found, or validation error
        """
        async with api_error_handler("Error creating zone", logger):
            zone_id = str(uuid.uuid4())

            try:
                zone = await registry_service.create_zone(
                    zone_id=zone_id,
                    name=request.name,
                    client_ids=request.client_ids
                )
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))

            # Reset every member to a neutral zone EQ. In the unified per-client
            # model a zone holds no EQ of its own; creating one applies a neutral
            # record (mono on) to each member via the access layer.
            if multiroom_equalizer_service:
                try:
                    await multiroom_equalizer_service.apply_zone_equalizer(
                        zone_id, EqualizerSettings.default_for_zone()
                    )
                except Exception as e:
                    logger.warning(f"Failed to apply initial zone equalizer: {e}")

            return {
                "status": "success",
                "zone": registry_service.zone_to_enriched_dict(zone)
            }

    @router.patch("/zones/{zone_id}", response_model=ZoneMutationResponse)
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
        async with api_error_handler(f"Error updating zone {zone_id}", logger):
            try:
                zone = await registry_service.update_zone(zone_id, name=request.name)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            if not zone:
                raise HTTPException(
                    status_code=404,
                    detail=f"Zone '{zone_id}' not found"
                )
            return {
                "status": "success",
                "zone": registry_service.zone_to_enriched_dict(zone)
            }

    @router.delete("/zones/{zone_id}", response_model=MultiroomMessageResponse)
    async def delete_zone(zone_id: str):
        """
        Delete a zone.

        When deleted:
        - All member clients have their zone_id set to None
        - Clients retain zone equalizer settings as their standalone equalizer
        - 'zone_deleted' WebSocket event is broadcast

        Args:
            zone_id: The zone's unique identifier

        Returns:
            {"status": "success", "message": "Zone deleted"}

        Raises:
            404: Zone not found
        """
        async with api_error_handler(f"Error deleting zone {zone_id}", logger):
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

    # === ZONE MEMBERSHIP ENDPOINTS ===

    @router.post("/zones/{zone_id}/clients", status_code=200, response_model=ZoneMutationResponse)
    async def add_client_to_zone(zone_id: str, request: ZoneAddClient):
        """
        Add a client to a zone. Client's equalizer is replaced by the zone's.

        The client will adopt the zone's shared equalizer settings.
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
        async with api_error_handler(f"Error adding client {request.mac_id} to zone {zone_id}", logger):
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

            # Capture an existing member's EQ before the add, so the new member
            # can adopt the zone's current EQ (members hold identical records).
            existing_member = next(
                (m for m in zone.client_ids if m != request.mac_id), None
            )

            success = await registry_service.add_client_to_zone(zone_id, request.mac_id)
            if not success:
                raise HTTPException(
                    status_code=400,
                    detail=f"Client '{request.mac_id}' is already in zone '{zone_id}'"
                )

            # New member adopts the zone's current EQ (unified per-client model).
            if multiroom_equalizer_service and existing_member:
                try:
                    zone_eq = await multiroom_equalizer_service.get_client_eq(existing_member)
                    await multiroom_equalizer_service.set_client_eq(request.mac_id, zone_eq)
                except Exception as e:
                    logger.warning(
                        f"Failed to apply zone equalizer to new member {request.mac_id}: {e}"
                    )

            zone = registry_service.get_zone(zone_id)
            return {
                "status": "success",
                "zone": registry_service.zone_to_enriched_dict(zone)
            }

    @router.delete("/zones/{zone_id}/clients/{mac_id}", response_model=ZoneOrMessageResponse, response_model_exclude_none=True)
    async def remove_client_from_zone(zone_id: str, mac_id: str):
        """
        Remove a client from a zone. Client keeps the zone equalizer as standalone.

        The client retains the zone's equalizer settings as its standalone settings.
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
        async with api_error_handler(f"Error removing client {mac_id} from zone {zone_id}", logger):
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

    # === PENDING CLIENT ENDPOINTS ===

    @router.post("/register-client", response_model=RegisterClientResponse, response_model_exclude_none=True)
    async def register_client(request: RegisterClientRequest, raw_request: Request):
        """
        Register a milo-client as a pending speaker.

        Called by milo-client at boot, then re-posted every 15s as a heartbeat.
        - hardware_configured=false → store as pending (user must configure via wizard).
          If the mac_id already exists in the registry (reinstall scenario),
          the old entry is removed so the client appears fresh.
        - hardware_configured=true → no action needed (snapclient will reconnect).
        """
        async with api_error_handler("Error registering client", logger):
            # Validate that the declared IP matches the actual request origin
            # Strip IPv4-mapped IPv6 prefix (::ffff:) for dual-stack compatibility
            caller_ip = raw_request.client.host
            normalized_caller = caller_ip.removeprefix("::ffff:")
            if normalized_caller != request.ip:
                logger.warning(f"IP mismatch: body declares {request.ip} but request came from {caller_ip}")
                raise HTTPException(
                    status_code=403,
                    detail=f"Declared IP {request.ip} does not match request origin {normalized_caller}",
                )

            # Hardware already configured → snapclient will handle reconnection
            # Still sync volume_control from client's audio card to keep registry accurate
            if request.hardware_configured:
                existing = registry_service.get_client(request.mac_id)
                if existing:
                    if existing.volume_control != request.volume_control:
                        await registry_service.update_client(request.mac_id, volume_control=request.volume_control)
                        logger.info(f"Client {request.mac_id} volume_control updated to {request.volume_control}")
                    logger.debug(f"Client {request.mac_id} heartbeat, hardware configured, skipping pending")
                    return {"status": "success", "message": "Hardware configured, snapclient will reconnect"}

                # Not in registry yet (e.g. wifi-adopted client on first boot).
                # Stage identity in pending storage so the snapclient transfer
                # logic picks up name/speaker_type when it connects.
                client = await pending_clients_service.register_client(
                    mac_id=request.mac_id,
                    ip=request.ip,
                    hardware_configured=request.hardware_configured,
                    audio_id=request.audio_id,
                    volume_control=request.volume_control,
                )
                if request.name or request.speaker_type:
                    client = await pending_clients_service.update_client(
                        request.mac_id,
                        name=request.name,
                        speaker_type=request.speaker_type,
                    ) or client
                return {"status": "success", "client": client}

            # Reinstall detection: if mac_id exists in registry, remove stale entry
            existing = registry_service.get_client(request.mac_id)
            if existing:
                logger.info(f"Reinstall detected for {request.mac_id}, removing stale registry entry")
                await registry_service.unregister_client(request.mac_id)

            client = await pending_clients_service.register_client(
                mac_id=request.mac_id,
                ip=request.ip,
                hardware_configured=request.hardware_configured,
                audio_id=request.audio_id,
                volume_control=request.volume_control,
            )
            if request.name or request.speaker_type:
                client = await pending_clients_service.update_client(
                    request.mac_id,
                    name=request.name,
                    speaker_type=request.speaker_type,
                ) or client
            return {"status": "success", "client": client}

    @router.get("/pending-clients", response_model=MultiroomPendingClientsResponse)
    async def get_pending_clients():
        """Get all pending (not yet configured) clients."""
        async with api_error_handler("Error getting pending clients", logger):
            return {"clients": pending_clients_service.get_all_clients()}

    @router.post("/pending-clients/{mac_id}/configure", response_model=MultiroomMessageResponse)
    async def configure_pending_client(mac_id: str, request: ConfigurePendingClientRequest):
        """
        Configure a pending client's audio card and reboot it.

        Orchestrates the full flow:
        1. Updates pending storage with name/speaker_type/audio_id
        2. Sends PUT /api/hardware/audio to the client
        3. Sends POST /api/hardware/reboot to the client
        """
        async with api_error_handler(f"Error configuring pending client {mac_id}", logger):
            client = pending_clients_service.get_client(mac_id)
            if not client:
                raise HTTPException(status_code=404, detail=f"Pending client '{mac_id}' not found")

            client_ip = client["ip"]

            # 1. Resolve overlay and volume_control from audio card
            from backend.hardware.registry import AUDIO_CARDS, is_dac_card
            card_info = AUDIO_CARDS.get(request.audio_id, {})
            overlay = card_info.get("overlay", "")
            # Use explicit value from request, or auto-detect from card category
            volume_control = request.volume_control if request.volume_control is not None else not is_dac_card(request.audio_id)

            # 2. Update pending storage (including volume_control)
            await pending_clients_service.update_client(
                mac_id,
                name=request.name,
                speaker_type=request.speaker_type,
                audio_id=request.audio_id,
                volume_control=volume_control,
            )

            # 3. Shield the entry from heartbeat expiry — it holds the only copy
            # of the name/speaker_type until the snapclient reconnects, and the
            # reboot it is about to take is longer than STALE_TIMEOUT.
            await pending_clients_service.mark_configuring(mac_id)

            # 4. Send config + reboot to client
            await _send_audio_config_and_reboot(mac_id, client_ip, request.audio_id, overlay, volume_control)
            logger.info(f"Pending client {mac_id} configured with audio={request.audio_id}, rebooting")
            return {"status": "success", "message": f"Client {mac_id} configured and rebooting"}

    return router
