"""
The server's WiFi, kept by every satellite as a standby link.

A satellite adopted over Ethernet has no screen to type a WiFi password on, and
nothing to fall back on when its cable or its switch goes. So the server hands
over the network it is on itself — SSID, PSK and country — and the satellite
keeps it as an autoconnecting profile beside its Ethernet. Nothing to switch on:
whenever the server is on a WiFi network, every satellite holds it too.

Pushed at two moments, and only these: when a satellite is admitted (the sync
that follows every arrival, `SnapcastWebSocketService`), and when the server's
own WiFi comes up or changes network (`NetworkService`). The satellite's helper
leaves a matching profile alone, so a push that changes nothing costs a few
nmcli reads and never touches a live link.

Best effort throughout: a satellite whose app predates the route, or that
refuses, is logged and left as it was — never a reason to delay an admission or
to touch the audio.
"""
import asyncio
import logging

import aiohttp

from backend.config.constants import CLIENT_API_PORT

logger = logging.getLogger(__name__)

# The satellite's helper runs a handful of nmcli calls (and the country, once),
# within its own 20 s budget.
PUSH_TIMEOUT_S = 25


class SatelliteWifiService:
    def __init__(self, network_service, client_registry_service):
        self._network = network_service
        self._registry = client_registry_service
        # The network last reported as one a satellite cannot be handed, so the
        # line is written once per network rather than at every admission.
        self._unsupported_reported = None

    async def _server_network(self):
        """What a satellite needs to join the server's WiFi, or None when there is nothing to hand over.

        None too for a network an SSID and a PSK cannot rebuild — hidden, WPA3
        alone, 802.1X, WEP: the satellite would keep a profile that never
        associates, and the standby link would exist only in the log.
        """
        profile = await self._network.get_active_wifi_profile()
        if profile is None:
            return None
        if profile["hidden"] or profile["key_mgmt"] not in ("", "wpa-psk"):
            if self._unsupported_reported != profile["ssid"]:
                self._unsupported_reported = profile["ssid"]
                logger.info(
                    "WiFi '%s' is not handed to the satellites: a %s network cannot be rebuilt "
                    "from its name and key, so they keep Ethernet only",
                    profile["ssid"], "hidden" if profile["hidden"] else profile["key_mgmt"],
                )
            return None
        return {
            "ssid": profile["ssid"],
            "password": profile["password"],
            "country": await self._network.get_country(),
        }

    async def _send(self, client_ip: str, network: dict) -> None:
        ssid = network["ssid"]
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=PUSH_TIMEOUT_S)) as session:
                async with session.put(
                    f"http://{client_ip}:{CLIENT_API_PORT}/api/system/wifi",
                    json={"ssid": ssid, "password": network["password"], "country": network["country"]},
                ) as resp:
                    if resp.status == 200:
                        body = await resp.json()
                        if body["data"]["changed"]:
                            logger.info("Satellite %s now keeps WiFi '%s' as a standby link", client_ip, ssid)
                        return
                    detail = await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning("Could not hand WiFi '%s' to satellite %s: %s", ssid, client_ip, exc or type(exc).__name__)
            return

        if resp.status == 404:
            # Reached by the next app update from the server; nothing else to do.
            logger.info("Satellite %s cannot keep a WiFi link yet: its app predates it", client_ip)
        else:
            logger.warning("Satellite %s refused WiFi '%s' (HTTP %s): %s", client_ip, ssid, resp.status, detail)

    async def push(self, client_ip: str) -> None:
        """Hand the server's WiFi to one satellite; nothing when the server is on none."""
        network = await self._server_network()
        if network is not None:
            await self._send(client_ip, network)

    async def push_all(self) -> None:
        """Hand the server's WiFi to every satellite online now, read once for all of them."""
        satellites = [c for c in self._registry.get_online_clients() if not c.is_local]
        if not satellites:
            return
        network = await self._server_network()
        if network is not None:
            await asyncio.gather(*(self._send(c.ip, network) for c in satellites))
