"""
WiFi management API routes.
"""
import logging
from fastapi import APIRouter

from backend.api.route_helpers import api_error_handler
from backend.core.wifi.models import WifiConnectRequest, WifiRadioRequest

logger = logging.getLogger(__name__)


def create_wifi_router(wifi_service):
    router = APIRouter(prefix="/api/wifi", tags=["wifi"])

    @router.get("/networks")
    async def scan_networks():
        """Scan for available WiFi networks."""
        async with api_error_handler("WiFi scan", logger):
            networks = await wifi_service.scan_networks()
            return {"status": "success", "data": [n.model_dump() for n in networks]}

    @router.get("/status")
    async def get_network_status():
        """Get current network status (ethernet + WiFi)."""
        async with api_error_handler("Network status", logger):
            status = await wifi_service.get_network_status()
            return {"status": "success", "data": status.model_dump()}

    @router.post("/connect")
    async def connect_to_network(request: WifiConnectRequest):
        """Connect to a WiFi network."""
        async with api_error_handler("WiFi connect", logger):
            status = await wifi_service.connect(request.ssid, request.password)
            return {"status": "success", "data": status.model_dump()}

    @router.delete("/saved/{ssid}")
    async def forget_network(ssid: str):
        """Forget a saved WiFi network."""
        async with api_error_handler("WiFi forget", logger):
            await wifi_service.forget_network(ssid)
            return {"status": "success"}

    @router.get("/saved")
    async def get_saved_networks():
        """List saved WiFi networks."""
        async with api_error_handler("WiFi saved networks", logger):
            networks = await wifi_service.get_saved_networks()
            return {"status": "success", "data": [n.model_dump() for n in networks]}

    @router.put("/radio")
    async def set_wifi_radio(request: WifiRadioRequest):
        """Enable or disable WiFi radio."""
        async with api_error_handler("WiFi radio", logger):
            await wifi_service.set_wifi_enabled(request.enabled)
            status = await wifi_service.get_network_status()
            return {"status": "success", "data": status.model_dump()}

    @router.get("/hotspot/status")
    async def get_hotspot_status():
        """Return whether the setup hotspot is currently active."""
        return {"status": "success", "data": {"active": wifi_service.hotspot_active}}

    return router
