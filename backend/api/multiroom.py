# backend/api/multiroom.py
"""
API routes for multiroom client and zone management.

Provides endpoints under /api/multiroom/ prefix for multiroom operations:
- /api/multiroom/clients/ prefix for client endpoints (PATCH updates)
- /api/multiroom/zones/ prefix for zone CRUD operations
- the satellite-facing routes (register-client, pending clients, remote
  access), several of which reach a satellite with aiohttp directly

Membership and client state go through ClientRegistryService, EQ through
MultiroomEqualizerService.

Features:
- PATCH method for partial updates
- Pydantic validation with meaningful error messages
- UUID auto-generation for zone creation
- Enriched zone responses with computed fields
"""
import asyncio
import contextlib
import logging
import uuid

import aiohttp
from typing import Optional, TYPE_CHECKING
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from backend.api.route_helpers import CONTRACTED, api_error_handler
from backend.api.models import (
    ZoneCreate, ZoneUpdate, ZoneAddClient, ClientUpdateRequest,
    ClientEqIndependentRequest, ClientDelayRequest, ClientGainRequest,
    RegisterClientRequest, ConfigurePendingClientRequest,
    ConfigureClientAudioRequest, SshRequest, DevicePasswordRequest,
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
from backend.config.constants import CLIENT_API_PORT, get_client_display_name
from backend.core.equalizer.client_proxy import CommandNotApplied
from backend.core.network.addresses import server_addresses
from backend.core.multiroom.models import EqualizerSettings
from backend.core.multiroom.satellite_http import Holding, SatelliteSecretUnavailable
from backend.hardware.registry import missing_card_label

if TYPE_CHECKING:
    from backend.core.equalizer.multiroom_service import MultiroomEqualizerService
    from backend.core.multiroom.satellite_http import SatelliteSecret
    from backend.core.multiroom.client_registry import ClientRegistryService
    from backend.core.multiroom.crossover import CrossoverService
    from backend.core.multiroom.equalizer_router import EqualizerRouter
    from backend.core.multiroom.pending_clients import PendingClientsService
    from backend.core.multiroom.snapcast import SnapcastService
    from backend.core.volume import VolumeService


logger = logging.getLogger(__name__)


async def _mark_unreachable(registry_service, mac_id: str, client_ip: str, exc: Exception) -> None:
    """Record a satellite that just failed to answer on CLIENT_API_PORT as offline.

    Snapserver is the normal authority on liveness, but it only notices a client
    once its socket errors — a satellite that dropped off without a TCP FIN stays
    `connected` there for as long as nothing is written to it, so the registry can
    still claim online at the moment a route tries to reach it. A refused HTTP
    request is direct proof to the contrary: flipping the flag here makes the
    frontend render the client as offline immediately instead of waiting for the
    reconcile sweep, which takes up to SnapcastWebSocketService.RECONCILE_INTERVAL_S
    + SnapcastService.LAST_SEEN_FRESHNESS_S to notice.

    Logged at warning, not error: an unplugged speaker is an expected state, and
    WebSocketLogHandler (level=ERROR) would turn it into a backend-error banner.
    Called for a connection that failed only, never for a slow answer
    (`_reaching_satellite`): a speaker that is playing and shown offline loses
    its controls until the sweep puts it back.
    """
    logger.warning(f"Client {mac_id} unreachable at {client_ip}, marking offline: {exc}")
    if registry_service:
        await registry_service.set_client_online(mac_id, False)


def _require_satellite(client) -> None:
    """Refuse a satellite-only route aimed at the main unit.

    These routes mean nothing for the local client: its milo-client API (the
    server runs none — `milo-client/app` is installed on satellites only), its
    audio card + reboot, its SSH switch and password (the server's own live
    under /api/system), and its removal. Unguarded, each one reached
    127.0.0.1:CLIENT_API_PORT, failed, and called `_mark_unreachable` on the way
    out — which flips the MAIN unit to offline, and that is exactly what puts a
    "Système hors ligne · Supprimer" screen on its own settings page. The delete
    then erases the name, speaker type, delay and level trim of a client that
    re-registers itself seconds later with defaults: a silent reset, no audio
    fault, nothing in the journal.
    """
    if client.is_local:
        raise HTTPException(
            status_code=400,
            detail="This endpoint addresses a multiroom satellite, not the main unit",
        )


async def _satellite_reply(resp: aiohttp.ClientResponse, mac_id: str) -> dict:
    """The satellite's JSON answer, or its refusal relayed as a 502 that says why.

    A 404 is a satellite whose app predates the route: it is reached by an app
    update from the server, and saying so is the one useful thing to tell. A
    409 is the satellite refusing from its own state — SSH opened on its
    factory password, no `milo` account — which is the owner's to act on, not
    a failure: relayed as a 409 with its reason, logged at warning.
    """
    # Parsed here, not left to the caller: aiohttp's ContentTypeError is a
    # ClientError, which the relay reads as "unreachable" — a satellite that
    # answered something other than JSON would be marked offline.
    try:
        body = await resp.json()
    except (aiohttp.ContentTypeError, ValueError):
        body = None
    if resp.status == 200 and isinstance(body, dict):
        return body
    if resp.status == 409:
        detail = (body or {}).get("detail") or "HTTP 409"
        logger.warning(f"Satellite {mac_id} refused the request from its state: {detail}")
        raise HTTPException(status_code=409, detail=detail)
    if resp.status == 200:
        detail = "it answered something that is not JSON"
    elif resp.status == 404:
        detail = "this satellite's app does not offer it yet; update the satellite first"
    else:
        detail = (body or {}).get("detail") or f"HTTP {resp.status}"
    logger.error(f"Satellite {mac_id} refused the request (HTTP {resp.status}): {detail}")
    raise HTTPException(status_code=502, detail=f"Satellite refused: {detail}")


async def _signed(
    satellite_secret: Optional["SatelliteSecret"], registry_service, mac_id: str, client_ip: str,
) -> dict:
    """The headers a satellite's path to root requires of this server (core/multiroom/satellite_http.py).

    503 when this server cannot make them — a router built without the secret
    refuses those routes rather than sending them bare, which the satellite
    would refuse anyway. 409 when the satellite refuses them, asked of the
    satellite itself by the hand-over (which pins the secret on one that holds
    none and only compares it otherwise): a satellite that pinned another
    server — this one reflashed, or a host that reached it first — is found
    out here, not at the last request. Asked before anything is sent, so a
    refused request has changed nothing.
    """
    if satellite_secret is None:
        logger.error(f"Refused a path to root on satellite {mac_id}: no satellite secret on this server")
        raise HTTPException(status_code=503, detail="This server holds no satellite secret")
    try:
        headers = await satellite_secret.headers(mac_id)
    except SatelliteSecretUnavailable as e:
        logger.error(f"Refused a path to root on satellite {mac_id}: {e}")
        raise HTTPException(status_code=503, detail=str(e)) from None
    async with _reaching_satellite(registry_service, mac_id, client_ip):
        holding = await satellite_secret.probe(mac_id, client_ip)
    if holding is Holding.REFUSED:
        logger.warning(f"Refused a path to root on satellite {mac_id}: it refuses this server's secret")
        raise HTTPException(
            status_code=409,
            detail="This speaker refuses this server's secret: it answers another server, or "
                   "cannot read its own pin. Its update, remote access and reboot stay refused "
                   "to this server until it is unplugged and plugged back in",
        )
    return headers


@contextlib.asynccontextmanager
async def _reaching_satellite(registry_service, mac_id: str, client_ip: str):
    """One exchange with a satellite, its two ways of failing told apart.

    No connection is a satellite that is gone: recorded offline, 502. No answer
    in time is a satellite that is there but slow — a `systemctl` on a loaded Pi —
    and may have applied the change, so it stays online and the caller hears 504.
    Every relay in this file goes through it, so the two failures read the same
    whichever route met them.
    """
    try:
        yield
    except asyncio.TimeoutError:
        logger.warning(f"Client {mac_id} at {client_ip} did not answer in time")
        raise HTTPException(status_code=504, detail=f"Client at {client_ip} did not answer in time")
    except aiohttp.ClientError as e:
        await _mark_unreachable(registry_service, mac_id, client_ip, e)
        raise HTTPException(status_code=502, detail=f"Cannot reach client at {client_ip}")


async def _push_volume_control(
    mac_id: str, client_ip: str, volume_control: bool, registry_service=None,
) -> None:
    """Write ``volume_control`` into a satellite's own hardware.json.

    The satellite owns this flag: its registration heartbeat re-sends
    hardware.json's value every 15s and ``POST /register-client`` writes it back
    over the registry. Updating the registry alone is therefore undone a few
    seconds later, silently. No reboot follows — nothing on the satellite reads
    the flag at runtime, it only declares who drives the volume.
    """
    timeout = aiohttp.ClientTimeout(total=5)
    async with _reaching_satellite(registry_service, mac_id, client_ip):
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"http://{client_ip}:{CLIENT_API_PORT}/api/hardware") as resp:
                audio = (await _satellite_reply(resp, mac_id)).get("audio", {})

            audio_id = audio.get("id", "none")
            if audio_id == "none":
                raise HTTPException(
                    status_code=400,
                    detail=f"Client '{mac_id}' has no audio card configured",
                )

            async with session.put(
                f"http://{client_ip}:{CLIENT_API_PORT}/api/hardware/audio",
                json={
                    "audio_id": audio_id,
                    "overlay": audio.get("overlay", ""),
                    "volume_control": volume_control,
                },
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"Client {mac_id} rejected volume_control: {resp.status} {body}")
                    raise HTTPException(
                        status_code=502,
                        detail=f"Client rejected volume management change: {resp.status}",
                    )


async def _send_audio_config_and_reboot(
    mac_id: str, client_ip: str, audio_id: str, overlay: str,
    volume_control: bool = True, registry_service=None, satellite_secret=None,
    before_write=None,
):
    """Send audio config to a milo-client and reboot it. Raises HTTPException on failure.

    ``registry_service`` is passed only for an already-registered client, so an
    unreachable satellite is also marked offline (see _mark_unreachable). A
    pending client has no registry entry yet.

    The reboot carries the server's secret (``satellite_secret``), made and
    accepted by the satellite (`_signed`) before ``before_write`` runs and the
    card is written: a card left on disk for a reboot that is then refused
    would change the speaker's output at its next power cut. A pending client
    pins that secret here, since nothing has admitted it yet.
    """
    reboot_headers = await _signed(satellite_secret, registry_service, mac_id, client_ip)
    if before_write is not None:
        await before_write()
    timeout = aiohttp.ClientTimeout(total=10)
    async with _reaching_satellite(registry_service, mac_id, client_ip):
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

            # Step 2: Reboot. A non-200 is fatal like step 1's: the satellite
            # answered and refused, so the overlay written just above never
            # takes effect and the card does not change. Warning here is how a
            # fleet where every script-installed satellite answered 500 — its
            # apply-hardware helper missing from its own rootfs tree — still
            # reported a successful pairing, for as long as one flashed unit
            # worked. A dropped connection stays a warning: that is what a
            # satellite that *is* rebooting looks like.
            try:
                async with session.post(
                    f"http://{client_ip}:{CLIENT_API_PORT}/api/hardware/reboot", headers=reboot_headers,
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error(f"Client {mac_id} refused to reboot: {resp.status} {body}")
                        raise HTTPException(
                            status_code=502,
                            detail=f"Client accepted the audio configuration but refused to reboot: {resp.status}",
                        )
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(f"Reboot request to {mac_id} failed (may already be rebooting): {e}")


def create_multiroom_router(
    registry_service: "ClientRegistryService",
    multiroom_equalizer_service: Optional["MultiroomEqualizerService"] = None,
    pending_clients_service: Optional["PendingClientsService"] = None,
    crossover_service: Optional["CrossoverService"] = None,
    snapcast_service: Optional["SnapcastService"] = None,
    equalizer_router: Optional["EqualizerRouter"] = None,
    volume_service: Optional["VolumeService"] = None,
    satellite_secret: Optional["SatelliteSecret"] = None,
):
    """
    Creates multiroom router with dependency injection.

    Provides /api/multiroom/clients/ endpoints per architecture specification.
    Delegates all operations to ClientRegistryService. ``satellite_secret`` is
    what a satellite's paths to root (its reboot, SSH switch and password)
    require of this server.
    """
    router = APIRouter(prefix="/api/multiroom", tags=["multiroom"])

    def _client_with_online(client):
        """Helper to include runtime 'online' status in client dict."""
        data = client.to_dict()
        data["online"] = client.online
        return data

    async def _drop_trim_before_dac(mac_id: str, client) -> None:
        """Clear a client's level trim, in its DSP and then in its record, before it turns DAC.

        A trim is attenuation too, so leaving it would break the one promise a
        DAC client gets — that Milō attenuates nothing on that path — and once
        the flag says DAC nothing reaches it again: the router skips a DAC
        client's trim, PATCH /gain refuses one, and a satellite reloads it from
        its own config at every boot. A DSP that did not take the 0 dB therefore
        keeps the flag where it is. `force`: a client the registry holds offline
        can still answer, and a skip here is a trim left in the DSP. An
        unreachable satellite stays the 503 every satellite route answers; any
        other failure is the speaker refusing, a 502. A skip for an external
        amp is this unit's own DSP already in DAC mode, whose trim went with its
        flag (VolumeService._clear_local_gain): nothing is left to clear.
        """
        if not client.volume_control or client.gain_db == 0.0:
            return
        logger.info(f"Clearing the level trim of {mac_id}: its amp now owns the level")
        if equalizer_router:
            try:
                await equalizer_router.set_gain(mac_id, 0.0, force=True)
            except CommandNotApplied as e:
                if e.status_code == 503:
                    raise
                logger.error(f"Client {mac_id} kept its level trim, so it stays volume-managed: {e.detail}")
                raise HTTPException(
                    status_code=502,
                    detail="The speaker did not drop its level trim; its volume management was not changed",
                )
        await registry_service.set_client_gain(mac_id, 0.0)

    async def _send_level_for_flag(mac_id: str, managed: bool) -> None:
        """Send a satellite whose volume_control just flipped what its fader now owes.

        Called after the registry write, since the router reads the flag there
        when the level leaves and picks between unity (DAC) and the stored level
        (managed). Nothing else would: no level move targets a DAC client, so a
        speaker handed to its amp kept the stored attenuation — the owner turns
        the amp up over a quiet room — until its next admission sent unity, +40 dB
        at once; and one handed back kept unity until its next level change. The
        flag has moved either way, so a refusal is reported, not answered as an
        error — at error only for an admitted speaker: one not yet admitted (a
        heartbeat can arrive ahead of its snapclient and its CamillaDSP) refuses
        for that reason alone, and its admission, which reads the flag just
        written, sends what it owes.
        """
        if not volume_service:
            return
        if await volume_service.reapply_client_volume(mac_id):
            return
        client = registry_service.get_client(mac_id)
        name = (client.name if client else None) or mac_id
        level = volume_service.state_store.get_client_volume(mac_id)
        if not (client and client.online):
            logger.warning(
                f"{name} is not admitted yet and did not take what its new volume management "
                f"owes its fader; its admission sends it"
            )
            return
        if managed:
            logger.error(
                f"{name} is volume-managed again but did not take its stored level ({level} dB) "
                f"or mute; its next level change or admission sends them again"
            )
        else:
            logger.error(
                f"{name} is handed to its amp but its fader did not leave {level} dB for unity; "
                f"its next admission sends unity"
            )

    # === STATE ENDPOINT ===

    @router.get("/state", response_model=MultiroomStateResponse, openapi_extra=CONTRACTED)
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
        Update client properties (name, speaker_type and/or volume_control).

        Supports partial updates - only provided fields are updated.
        Broadcasts 'client_updated' WebSocket event on success.

        Args:
            mac_id: Client MAC address
            request: Partial update with optional name, speaker_type, volume_control

        Returns:
            {"status": "success", "client": {...}} with updated client data

        Raises:
            404: Client not found
            400: Invalid speaker_type (validation handled by Pydantic),
                 or volume_control on a client with no audio card
            502: Satellite unreachable or refused the volume_control change,
                 or kept the level trim a DAC flip drops first
            503: Satellite unreachable when its level trim was to be dropped
        """
        async with api_error_handler(f"Error updating client {mac_id}", logger):
            client = registry_service.get_client(mac_id)
            if not client:
                raise HTTPException(
                    status_code=404,
                    detail=f"Client with mac_id '{mac_id}' not found"
                )

            if request.volume_control is False:
                await _drop_trim_before_dac(mac_id, client)
            # Read before the registry write, which moves the record's flag.
            flag_flips = (
                request.volume_control is not None
                and not client.is_local
                and request.volume_control != client.volume_control
            )

            # A remote client's volume_control lives on the satellite; push it
            # there first, or its heartbeat reverts the registry (see
            # _push_volume_control). A failure must surface, not be written
            # locally and undone seconds later.
            if flag_flips:
                await _push_volume_control(
                    mac_id, client.ip, request.volume_control,
                    registry_service=registry_service,
                )

            updated_client = await registry_service.update_client(
                mac_id,
                name=request.name,
                speaker_type=request.speaker_type,
                volume_control=request.volume_control
            )

            if flag_flips:
                await _send_level_for_flag(mac_id, request.volume_control)

            return {"status": "success", "client": _client_with_online(updated_client)}

    @router.put("/clients/{mac_id}/eq-independent", response_model=ClientMutationResponse)
    async def set_client_eq_independent(mac_id: str, request: ClientEqIndependentRequest):
        """Detach (or reattach) a zone member's EQ from its zone.

        The member stays in the zone for synchronized playback either way.

        - enabled=True (detach): flag only. The member already holds a
          byte-identical copy of the zone's EQ, so there is nothing to copy and
          no audible jump; later zone-EQ edits simply skip it.
        - enabled=False (reattach): clear the flag, then re-adopt the zone's
          current EQ — the one the zone's screen shows, `get_zone_eq` — and
          push it (`adopt_zone_eq`, which keeps the member's own `enabled`), so
          the member falls back in step with the zone.

        Returns:
            {"status": "success", "client": {...}}

        Raises:
            404: Client not found
            502: the flag is saved, but the member did not take the zone's EQ
        """
        async with api_error_handler(f"Error setting eq_independent for client {mac_id}", logger):
            client = registry_service.get_client(mac_id)
            if not client:
                raise HTTPException(
                    status_code=404,
                    detail=f"Client with mac_id '{mac_id}' not found"
                )

            # On reattachment, read the zone's EQ BEFORE clearing the flag: the
            # member is still independent, so the zone's record is its sharing
            # members' alone — the EQ to re-adopt.
            zone_eq = None
            if not request.enabled and client.zone_id and multiroom_equalizer_service:
                zone_eq = await multiroom_equalizer_service.get_zone_eq(client.zone_id)

            updated_client = await registry_service.set_client_eq_independent(mac_id, request.enabled)

            if zone_eq is not None:
                try:
                    adopted = await multiroom_equalizer_service.adopt_zone_eq(mac_id, zone_eq)
                    refusal = None if adopted else "the client refused the push"
                except Exception as e:
                    refusal = str(e)
                if refusal:
                    logger.error(f"Failed to re-adopt zone equalizer for {mac_id}: {refusal}")
                    raise HTTPException(
                        status_code=502, detail="saved, the speaker did not take its EQ"
                    )

            return {"status": "success", "client": _client_with_online(updated_client)}

    @router.patch("/clients/{mac_id}/delay", response_model=ClientMutationResponse)
    async def set_client_delay(mac_id: str, request: ClientDelayRequest):
        """Set a client's playback delay (native Snapcast latency, in ms).

        Milō owns the value as source of truth: it is applied to snapserver
        first when the client is online, and re-pushed on reconnection either
        way. Addresses the exact client — a zone member and a standalone alike,
        never a zone redirect.

        Returns:
            {"status": "success", "client": {...}}

        Raises:
            404: Client not found
            502: snapserver did not confirm the delay
        """
        async with api_error_handler(f"Error setting delay for client {mac_id}", logger):
            client = registry_service.get_client(mac_id)
            if not client:
                raise HTTPException(
                    status_code=404,
                    detail=f"Client with mac_id '{mac_id}' not found"
                )

            # Hardware first, as for the trim: set_latency answers a refusal
            # (HTTP error, timeout, JSON-RPC error) with False, and a record
            # written before it would broadcast a delay the room never took. The
            # snapcast client id is the mac_id by construction (see
            # compute_mac_id); an offline client has nothing to refuse and
            # recovers the value via the admission re-push.
            if snapcast_service and client.online:
                if not await snapcast_service.set_latency(mac_id, request.delay_ms):
                    logger.error(f"snapserver refused the {request.delay_ms} ms delay for {mac_id}")
                    raise HTTPException(
                        status_code=502,
                        detail="snapserver did not confirm the delay"
                    )

            updated_client = await registry_service.set_client_delay(mac_id, request.delay_ms)
            # The user's action, said here: snapserver's own trace of the
            # request is debug, since every admission sends it again.
            logger.info(f"Delay of {mac_id} set to {request.delay_ms} ms")

            return {"status": "success", "client": _client_with_online(updated_client)}

    @router.patch("/clients/{mac_id}/gain", response_model=ClientMutationResponse)
    async def set_client_gain(mac_id: str, request: ClientGainRequest):
        """Set a client's level trim (a fixed CamillaDSP Gain stage, in dB).

        Compensates a speaker that plays louder or quieter than the others, so
        every client can sit at the same volume. It is not a volume: the fader
        keeps its own range, which is what stops a shared level change from
        collapsing the balance when one client reaches a limit.

        Addresses the exact client — the main unit and a satellite alike, through
        the one router that already knows which is which.

        Returns:
            {"status": "success", "client": {...}}

        Raises:
            400: The client is a DAC whose external amp owns its level
            404: Client not found
            502: The DSP refused the trim
            503: Satellite unreachable
        """
        async with api_error_handler(f"Error setting gain for client {mac_id}", logger):
            client = registry_service.get_client(mac_id)
            if not client:
                raise HTTPException(
                    status_code=404,
                    detail=f"Client with mac_id '{mac_id}' not found"
                )
            if not client.volume_control:
                # Same promise EqualizerRouter.set_gain keeps for a DAC client:
                # its external amp owns the level and Milō attenuates nothing on
                # that path. A digital trim would be attenuation by another name.
                # (`set_volume` no longer skips — it writes unity explicitly, so
                # that no unmute can land on a fader nobody set.)
                raise HTTPException(
                    status_code=400,
                    detail="This speaker's external amplifier owns its level"
                )

            # Hardware first: a refusal must not leave the registry claiming a
            # trim the DSP never took — the admission sync would then replay that
            # record as if it were the truth. The router raises it, and
            # api_error_handler answers its status (502 for this unit's DSP, the
            # satellite's own otherwise). An offline client is the exception:
            # the router skips it, there is nothing to refuse, and that same sync
            # is what delivers the value when it comes back.
            if equalizer_router:
                await equalizer_router.set_gain(mac_id, request.gain_db)

            updated_client = await registry_service.set_client_gain(mac_id, request.gain_db)

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
            client = registry_service.get_client(mac_id)
            if client:
                _require_satellite(client)

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
            _require_satellite(client)

            async with _reaching_satellite(registry_service, mac_id, client.ip):
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                    async with session.get(f"http://{client.ip}:{CLIENT_API_PORT}/api/hardware") as resp:
                        return await _satellite_reply(resp, mac_id)

    @router.put("/clients/{mac_id}/audio", response_model=MultiroomMessageResponse)
    async def configure_client_audio(mac_id: str, request: ConfigureClientAudioRequest):
        """Change audio card on a registered milo-client and reboot it."""
        async with api_error_handler(f"Error configuring audio for client {mac_id}", logger):
            client = registry_service.get_client(mac_id)
            if not client:
                raise HTTPException(status_code=404, detail=f"Client '{mac_id}' not found")
            _require_satellite(client)

            from backend.hardware.registry import audio_config
            audio = audio_config(request.audio_id, request.volume_control)
            volume_control = audio["volume_control"]

            # The trim goes once the satellite has taken the secret its reboot
            # carries: a refused reboot must leave the speaker as it found it.
            await _send_audio_config_and_reboot(
                mac_id, client.ip, request.audio_id, audio["overlay"], volume_control,
                registry_service=registry_service, satellite_secret=satellite_secret,
                before_write=None if volume_control else lambda: _drop_trim_before_dac(mac_id, client),
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

    # The satellite's remote access: the same SSH switch and `milo` password the
    # server offers for itself under /api/system, relayed because a satellite has
    # no UI of its own and its image ships with SSH off. Each URL stays literal at
    # its call: the milo-client contract test reads them there. The budgets cover
    # the satellite's own: two parallel 5 s reads, a 15 s write followed by them.
    #
    # Both writes reach root on the satellite, so they carry the server's secret
    # (the satellite answers no one else), handed over first (`_signed`): a
    # satellite that pinned another server is refused before anything is sent.
    # What else the satellite refuses — no `milo` account, SSH opened on the
    # factory password — is its own to say, relayed by `_satellite_reply`.

    def _satellite(mac_id: str):
        client = registry_service.get_client(mac_id)
        if not client:
            raise HTTPException(status_code=404, detail=f"Client '{mac_id}' not found")
        _require_satellite(client)
        return client

    async def _read_client_ssh(mac_id: str, client) -> dict:
        async with _reaching_satellite(registry_service, mac_id, client.ip):
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(f"http://{client.ip}:{CLIENT_API_PORT}/api/system/ssh") as resp:
                    return await _satellite_reply(resp, mac_id)

    @router.get("/clients/{mac_id}/ssh")
    async def get_client_ssh(mac_id: str):
        """SSH state of a satellite, the account it logs into, and whether that account still has the factory password."""
        async with api_error_handler(f"Error reading SSH state of client {mac_id}", logger):
            return await _read_client_ssh(mac_id, _satellite(mac_id))

    @router.put("/clients/{mac_id}/ssh")
    async def set_client_ssh(mac_id: str, request: SshRequest):
        """Open or close SSH on a satellite.

        The satellite refuses to open it while its factory password is in
        place. Closing is never refused.
        """
        async with api_error_handler(f"Error setting SSH on client {mac_id}", logger):
            client = _satellite(mac_id)
            headers = await _signed(satellite_secret, registry_service, mac_id, client.ip)
            async with _reaching_satellite(registry_service, mac_id, client.ip):
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25)) as session:
                    async with session.put(
                        f"http://{client.ip}:{CLIENT_API_PORT}/api/system/ssh",
                        json={"enabled": request.enabled}, headers=headers,
                    ) as resp:
                        reply = await _satellite_reply(resp, mac_id)
            logger.info(f"SSH {'enabled' if request.enabled else 'disabled'} on client {mac_id}")
            return reply

    @router.post("/clients/{mac_id}/password")
    async def set_client_password(mac_id: str, request: DevicePasswordRequest):
        """Set the `milo` account password of a satellite (SSH login + sudo)."""
        async with api_error_handler(f"Error setting the password of client {mac_id}", logger):
            client = _satellite(mac_id)
            headers = await _signed(satellite_secret, registry_service, mac_id, client.ip)
            async with _reaching_satellite(registry_service, mac_id, client.ip):
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
                    async with session.post(
                        f"http://{client.ip}:{CLIENT_API_PORT}/api/system/password",
                        json={"password": request.password}, headers=headers,
                    ) as resp:
                        reply = await _satellite_reply(resp, mac_id)
            logger.info(f"Password updated on client {mac_id}")
            return reply

    @router.get("/clients/{mac_id}/network")
    async def get_client_network(mac_id: str):
        """The links a satellite is on: Ethernet and WiFi, each null when down."""
        async with api_error_handler(f"Error reading the network links of client {mac_id}", logger):
            client = _satellite(mac_id)
            async with _reaching_satellite(registry_service, mac_id, client.ip):
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
                    async with session.get(f"http://{client.ip}:{CLIENT_API_PORT}/api/system/network") as resp:
                        # An app that predates the route is expected until its next
                        # update, and the panel only hides the two rows: no error.
                        if resp.status == 404:
                            logger.debug(f"Client {mac_id} does not report its network links yet")
                            raise HTTPException(status_code=404, detail="This satellite does not report its network links yet")
                        return await _satellite_reply(resp, mac_id)

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
            # Best-effort on purpose: the zone exists either way, and a member
            # that was not reached recovers the record on its reconnection sync.
            # Only the log tells the operator, so it must not stay silent.
            if multiroom_equalizer_service:
                try:
                    if not await multiroom_equalizer_service.apply_zone_equalizer(
                        zone_id, EqualizerSettings.default_for_zone()
                    ):
                        logger.warning(
                            f"Zone {zone_id} created, but its initial equalizer did not "
                            "reach every member"
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
        - Clients keep their EQ records, which were the zone's (a zone holds none)
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
            502: the client is in the zone, but did not take the zone's EQ
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

            # Read the zone's EQ before the add — the one the zone's screen
            # shows, by the same rule as the re-attach path above — so the new
            # member adopts it, not whichever member happens to be listed first.
            zone_eq = (
                await multiroom_equalizer_service.get_zone_eq(zone_id)
                if multiroom_equalizer_service else None
            )

            success = await registry_service.add_client_to_zone(zone_id, request.mac_id)
            if not success:
                raise HTTPException(
                    status_code=400,
                    detail=f"Client '{request.mac_id}' is already in zone '{zone_id}'"
                )

            # New member adopts the zone's current EQ, its own `enabled` kept.
            if zone_eq is not None:
                try:
                    applied = await multiroom_equalizer_service.adopt_zone_eq(
                        request.mac_id, zone_eq
                    )
                    refusal = None if applied else "the client refused the push"
                except Exception as e:
                    refusal = str(e)
                if refusal:
                    logger.error(
                        f"Failed to apply zone equalizer to new member "
                        f"{request.mac_id}: {refusal}"
                    )
                    raise HTTPException(
                        status_code=502, detail="saved, the speaker did not take its EQ"
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
    async def register_client(
        request: RegisterClientRequest, raw_request: Request, background_tasks: BackgroundTasks,
    ):
        """
        Register a milo-client as a pending speaker.

        Called by milo-client at boot, then re-posted every 15s as a heartbeat.
        - hardware_configured=false → store as pending (user must configure via wizard).
          If the mac_id already exists in the registry (reinstall scenario),
          the old entry is removed so the client appears fresh.
        - hardware_configured=true, unknown → stage the identity it carries
          (wifi-adopted, heartbeat ahead of snapclient).
        - hardware_configured=true, known → sync volume_control (and send the
          fader what the new flag owes it), and apply the identity it carries
          if the record still holds the fallback name (wifi-adopted, snapclient
          admitted ahead of the heartbeat).

        After the response, the satellite is handed this server's secret
        when its heartbeat calls for it, so Multiroom knows whether it answers
        another server before anyone tries to use it.
        """
        def hand_over_if_due(asked: bool) -> None:
            """Due whenever the satellite says it would follow a new server —
            the heartbeat of a speaker just plugged back in, which then comes
            to answer this server, admitted or not — and otherwise only while
            `asked` is false. The answer is recorded by the secret itself
            (`SatelliteSecret.probe`), so asking again changes nothing."""
            if satellite_secret is None or (asked and not request.accepts_new_server):
                return
            background_tasks.add_task(satellite_secret.hand_over, request.mac_id, request.ip)

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

            addresses = await asyncio.to_thread(server_addresses)

            # Hardware already configured → snapclient will handle reconnection
            # Still sync volume_control from client's audio card to keep registry accurate
            if request.hardware_configured:
                existing = registry_service.get_client(request.mac_id)
                if existing:
                    if existing.volume_control != request.volume_control:
                        try:
                            if not request.volume_control:
                                await _drop_trim_before_dac(request.mac_id, existing)
                        except (HTTPException, CommandNotApplied) as e:
                            # The flag waits, and the satellite's next heartbeat
                            # retries the clear.
                            logger.warning(
                                f"Client {request.mac_id} reports an external amp, kept volume-managed "
                                f"until its level trim is cleared: {e.detail}"
                            )
                        else:
                            await registry_service.update_client(request.mac_id, volume_control=request.volume_control)
                            logger.info(f"Client {request.mac_id} volume_control updated to {request.volume_control}")
                            # An admission that ran on the old flag sent its
                            # fader unity or the stored level, the wrong one
                            # now — a satellite adopted over WiFi is admitted
                            # ~5 s before this first heartbeat. After the
                            # response: the satellite waits 10 s for it.
                            background_tasks.add_task(
                                _send_level_for_flag, request.mac_id, request.volume_control
                            )
                    await registry_service.set_client_audio_card_missing(
                        request.mac_id, missing_card_label(request.audio_id, request.alsa_cards)
                    )
                    # A wifi-adopted satellite's snapclient connects ~5 s before
                    # this first heartbeat, so OnConnect admitted it under the
                    # fallback name. Its identity lands once: applying the name
                    # leaves the fallback, and a later rename is never reverted.
                    if (request.name and request.name != existing.name
                            and existing.name == get_client_display_name(existing.host)):
                        await registry_service.update_client(
                            request.mac_id, name=request.name, speaker_type=request.speaker_type
                        )
                        logger.info(
                            f"Client {request.mac_id} adopted identity applied: "
                            f"{request.name} ({request.speaker_type or existing.speaker_type})"
                        )
                    logger.debug(f"Client {request.mac_id} heartbeat, hardware configured, skipping pending")
                    hand_over_if_due(asked=True)
                    return {"status": "success", "message": "Hardware configured, snapclient will reconnect",
                            "server_addresses": addresses}

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
                # A configured speaker this process has not asked yet: one
                # that kept running through this server's reflash, announced
                # before its admission — or for good with multiroom off,
                # where no admission comes.
                hand_over_if_due(asked="other_server" in client)
                return {"status": "success", "client": client, "server_addresses": addresses}

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
            # A speaker waiting to be configured is asked only when it would
            # follow: its first contact is the reboot that configures it, a
            # gesture someone makes, and that reboot's own hand-over (`_signed`)
            # finds one that answers another server.
            hand_over_if_due(asked=True)
            return {"status": "success", "client": client, "server_addresses": addresses}

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
            from backend.hardware.registry import audio_config
            audio = audio_config(request.audio_id, request.volume_control)
            volume_control = audio["volume_control"]

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
            # False means the sweep already expired it between the lookup above
            # and here. The reboot still goes ahead — it is addressed by IP and
            # is what the caller asked for — but the name is now lost, so this
            # has to be said rather than swallowed under a "configured" answer.
            if not await pending_clients_service.mark_configuring(mac_id):
                logger.error(
                    f"Pending entry for {mac_id} vanished before it could be shielded — "
                    f"the client will come back unnamed"
                )

            # 4. Send config + reboot to client
            await _send_audio_config_and_reboot(
                mac_id, client_ip, request.audio_id, audio["overlay"], volume_control,
                satellite_secret=satellite_secret,
            )
            logger.info(f"Pending client {mac_id} configured with audio={request.audio_id}, rebooting")
            return {"status": "success", "message": f"Client {mac_id} configured and rebooting"}

    return router
