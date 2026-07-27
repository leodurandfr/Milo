"""
Network management API routes (Ethernet + WiFi).

Routes:
  GET /api/network/status         — combined Ethernet + WiFi status
  WiFi-specific endpoints live under /api/network/wifi/...
"""
import logging
from typing import TYPE_CHECKING
from fastapi import APIRouter

from backend.api.route_helpers import api_error_handler
from backend.api.responses import (
    NetworkStatusEnvelope,
    StatusResponse,
    WifiCountryEnvelope,
    WifiNetworksEnvelope,
    WifiSaveEnvelope,
    WifiSavedEnvelope,
)
from backend.core.network.models import WifiConnectRequest, WifiRadioRequest, WifiCountryRequest

if TYPE_CHECKING:
    from backend.core.network.service import NetworkService


logger = logging.getLogger(__name__)


def create_network_router(network_service: "NetworkService"):
    router = APIRouter(prefix="/api/network", tags=["network"])

    @router.get("/status", response_model=NetworkStatusEnvelope)
    async def get_network_status():
        """Get current network status (Ethernet + WiFi)."""
        async with api_error_handler("Network status", logger):
            status = await network_service.get_network_status()
            return {"status": "success", "data": status.model_dump()}

    @router.get("/wifi/networks", response_model=WifiNetworksEnvelope)
    async def scan_networks():
        """Scan for available WiFi networks."""
        async with api_error_handler("WiFi scan", logger):
            networks = await network_service.scan_networks()
            return {"status": "success", "data": [n.model_dump() for n in networks]}

    @router.post("/wifi/connect", response_model=NetworkStatusEnvelope)
    async def connect_to_network(request: WifiConnectRequest):
        """Connect to a WiFi network."""
        async with api_error_handler("WiFi connect", logger):
            status = await network_service.connect(request.ssid, request.password)
            return {"status": "success", "data": status.model_dump()}

    @router.post("/wifi/save", response_model=WifiSaveEnvelope)
    async def save_network(request: WifiConnectRequest):
        """Save WiFi credentials without connecting (for hotspot setup mode)."""
        async with api_error_handler("WiFi save", logger):
            await network_service.save_network(request.ssid, request.password)
            return {"status": "success", "data": {"ssid": request.ssid}}

    @router.delete("/wifi/saved/{ssid}", response_model=StatusResponse)
    async def forget_network(ssid: str):
        """Forget a saved WiFi network."""
        async with api_error_handler("WiFi forget", logger):
            await network_service.forget_network(ssid)
            return {"status": "success"}

    @router.get("/wifi/saved", response_model=WifiSavedEnvelope)
    async def get_saved_networks():
        """List saved WiFi networks."""
        async with api_error_handler("WiFi saved networks", logger):
            networks = await network_service.get_saved_networks()
            return {"status": "success", "data": [n.model_dump() for n in networks]}

    @router.put("/wifi/radio", response_model=NetworkStatusEnvelope)
    async def set_wifi_radio(request: WifiRadioRequest):
        """Enable or disable WiFi radio."""
        async with api_error_handler("WiFi radio", logger):
            await network_service.set_wifi_enabled(request.enabled)
            status = await network_service.get_network_status()
            return {"status": "success", "data": status.model_dump()}

    @router.get("/wifi/country", response_model=WifiCountryEnvelope)
    async def get_wifi_country():
        """Get the configured WiFi regulatory domain country code."""
        async with api_error_handler("WiFi country get", logger):
            code = await network_service.get_country()
            return {"status": "success", "data": {"country_code": code}}

    @router.put("/wifi/country", response_model=WifiCountryEnvelope)
    async def set_wifi_country(request: WifiCountryRequest):
        """Set the WiFi regulatory domain. Reboot required for full effect."""
        async with api_error_handler("WiFi country set", logger):
            await network_service.set_country(request.country_code)
            return {"status": "success", "data": {"country_code": request.country_code}}

    return router
