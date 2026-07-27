# backend/api/discovery.py
"""
Speaker discovery API routes — finds new Milō speakers on the network.

GET /api/discovery/wifi-speakers → list devices broadcasting the `Milō` setup
hotspot (fresh devices waiting to be adopted as multiroom clients). Because
the SSID is shared across devices, the scanner only ever returns 0 or 1
adoptable hotspot (NetworkManager deduplicates by SSID).

GET /api/discovery/server-wifi-creds → return this server's active WiFi
credentials for auto-fill during wifi-speaker adoption (or `available: false`
when the server is ethernet-only).

POST /api/discovery/adopt-speaker → orchestrate the wifi adoption of a fresh
speaker: temporarily join its setup hotspot, push audio + target wifi config,
then restore the server's original network connection.
"""
import logging
from typing import Literal, TYPE_CHECKING

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from backend.api.route_helpers import api_error_handler
from backend.core.multiroom.models import SPEAKER_TYPES
from backend.core.multiroom.wifi_adoption import AdoptionError
from backend.core.network.service import HOTSPOT_NAME

if TYPE_CHECKING:
    from backend.core.multiroom.wifi_adoption import WifiAdoptionService
    from backend.core.network.service import NetworkService


logger = logging.getLogger(__name__)


class AdoptSpeakerRequest(BaseModel):
    """Payload to adopt a wifi-only speaker exposing the 'Milō' hotspot."""
    ssid: str = Field(..., min_length=1, description="Hotspot SSID of the speaker (always 'Milō')")
    audio_id: str = Field(..., min_length=1, description="Audio card registry ID")
    speaker_name: str = Field(..., min_length=1, max_length=64, description="Display name for the speaker")
    speaker_type: Literal['satellite', 'bookshelf', 'tower', 'subwoofer'] = Field(..., description="Speaker physical type")
    wifi_ssid: str = Field(..., min_length=1, description="Target home wifi the speaker must join")
    wifi_password: str = Field(default="", description="Target wifi password (empty for open networks)")

    @field_validator('speaker_type')
    @classmethod
    def _validate_speaker_type(cls, v):
        if v not in SPEAKER_TYPES:
            raise ValueError(f"Invalid speaker_type '{v}'. Must be one of: {', '.join(SPEAKER_TYPES)}")
        return v


# Map AdoptionError.code → HTTP status. 4xx = caller/state issue, 502 = the
# server is fine but an upstream step (speaker hotspot, push, gateway) failed.
# Anything not listed falls through to 500 (genuinely internal).
_ADOPTION_CLIENT_ERROR_CODES = {
    "invalid_ssid": 400,
    "invalid_target_wifi": 400,
    "already_configured": 409,
    "server_in_hotspot_mode": 409,
    "push_rejected": 502,
    "push_failed": 502,
    "no_gateway": 502,
    "hotspot_connect_failed": 502,
}


def create_discovery_router(network_service: "NetworkService", wifi_adoption_service: "WifiAdoptionService"):
    """Create discovery router with injected services."""
    router = APIRouter(prefix="/api/discovery", tags=["discovery"])

    @router.get("/wifi-speakers")
    async def list_wifi_speakers():
        """List Milō devices broadcasting their setup hotspot.

        Filters a fresh wifi scan to the `Milō` SSID. Returns an empty list
        while this device is itself broadcasting the setup hotspot (a fresh
        server cannot adopt anything). The scan dedupes by SSID, so the
        result contains at most one adoptable hotspot.
        """
        async with api_error_handler("Discovery wifi speakers", logger):
            if network_service.hotspot_active:
                return {"status": "success", "data": {"hotspots": []}}
            networks = await network_service.scan_networks()
            hotspots = [
                {"ssid": n.ssid, "signal": n.signal}
                for n in networks
                if n.ssid == HOTSPOT_NAME
            ]
            return {"status": "success", "data": {"hotspots": hotspots}}

    @router.get("/server-wifi-creds")
    async def get_server_wifi_creds():
        """Return this server's active WiFi credentials for adoption auto-fill.

        When adopting a wifi-only speaker, the UI pre-fills the speaker's
        target WiFi with the server's home network so the user typically only
        confirms instead of retyping the password. When the server is
        ethernet-only (or otherwise has no active WiFi client connection),
        responds with ``available: false`` so the UI falls back to manual
        credentials entry.
        """
        async with api_error_handler("Discovery server wifi creds", logger):
            creds = await network_service.get_active_wifi_credentials()
            if creds is None:
                return {"status": "success", "data": {"available": False}}
            return {
                "status": "success",
                "data": {"available": True, **creds},
            }

    @router.post("/adopt-speaker")
    async def adopt_speaker(payload: AdoptSpeakerRequest):
        """Orchestrate wifi adoption of a fresh speaker.

        The server temporarily switches its wifi to the speaker's hotspot,
        pushes the audio + target wifi config, then reconnects to its original
        wifi. Wifi-only servers lose LAN connectivity for ~30 s during this
        flow; the UI is expected to handle the brief outage. Ethernet-equipped
        servers keep their LAN through the whole adoption.
        """
        try:
            data = await wifi_adoption_service.adopt_speaker(
                ssid=payload.ssid,
                audio_id=payload.audio_id,
                speaker_name=payload.speaker_name,
                speaker_type=payload.speaker_type,
                wifi_ssid=payload.wifi_ssid,
                wifi_password=payload.wifi_password,
            )
            return {"status": "success", "data": data}
        except AdoptionError as e:
            status_code = _ADOPTION_CLIENT_ERROR_CODES.get(e.code, 500)
            logger.error(
                "Adopt speaker '%s' failed (code=%s): %s",
                payload.ssid, e.code, e.detail,
            )
            raise HTTPException(
                status_code=status_code,
                detail={"code": e.code, "message": e.detail or e.code},
            )
        except Exception as e:
            logger.error("Adopt speaker '%s' unexpected error: %s", payload.ssid, e)
            raise HTTPException(
                status_code=500,
                detail={"code": "internal_error", "message": str(e)},
            )

    return router
