# backend/api/discovery.py
"""
Speaker discovery API routes — finds new Milō speakers on the network.

GET /api/discovery/wifi-speakers → list devices broadcasting a `Milō-XXXX`
setup hotspot (fresh devices waiting to be adopted as multiroom clients).

GET /api/discovery/server-wifi-creds → return this server's active WiFi
credentials for auto-fill during wifi-speaker adoption (or `available: false`
when the server is ethernet-only).
"""
import logging
from fastapi import APIRouter

from backend.api.route_helpers import api_error_handler
from backend.core.wifi.service import HOTSPOT_NAME_RE

logger = logging.getLogger(__name__)


def create_discovery_router(wifi_service):
    """Create discovery router with injected WiFi service."""
    router = APIRouter(prefix="/api/discovery", tags=["discovery"])

    @router.get("/wifi-speakers")
    async def list_wifi_speakers():
        """List Milō devices broadcasting their setup hotspot.

        Filters a fresh wifi scan to SSIDs matching `Milō-XXXX` (where XXXX is
        the last 4 hex chars of the device's wlan0 MAC). Excludes this device's
        own hotspot SSID as a safety guard.
        """
        async with api_error_handler("Discovery wifi speakers", logger):
            networks = await wifi_service.scan_networks()
            own_hotspot = wifi_service.hotspot_con_name
            hotspots = [
                {
                    "ssid": n.ssid,
                    "mac_suffix": n.ssid.split("-", 1)[1],
                    "signal": n.signal,
                }
                for n in networks
                if HOTSPOT_NAME_RE.match(n.ssid) and n.ssid != own_hotspot
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
            creds = await wifi_service.get_active_wifi_credentials()
            if creds is None:
                return {"status": "success", "data": {"available": False}}
            return {
                "status": "success",
                "data": {"available": True, **creds},
            }

    return router
