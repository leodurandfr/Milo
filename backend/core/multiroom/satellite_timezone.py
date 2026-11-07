"""
The server's timezone, held by every satellite too.

A satellite has no screen and no browser, so nothing ever told it where it is:
one flashed from the image keeps the Etc/UTC it ships, and its journal, its
diagnostic and every log line it writes are an hour or more away from the
server's. The server therefore hands over the zone it runs on, read from the
system (`/etc/localtime`) and never from a browser, so it is the right one
before any browser opened the UI.

Pushed at two moments, and only these — the shape of `SatelliteWifiService`:
when a satellite is admitted (the sync that follows every arrival,
`SnapcastWebSocketService`), and when the server's own zone changes
(`PUT /api/system/timezone`). The satellite's helper leaves a zone already in
force alone, so a push that changes nothing writes nothing.

Best effort throughout: a satellite whose app predates the route answers 404
and is reported once, at info, until an update brings it the route; one that
refuses or is gone is logged and left as it was — never a reason to delay an
admission or to touch the audio, and never retried in a loop: the next
admission is the retry.
"""
import asyncio
import logging
from collections import defaultdict

import aiohttp

from backend.config.constants import CLIENT_API_PORT
from backend.core.system.localtime import current_timezone

logger = logging.getLogger(__name__)

# The satellite's helper makes two timedatectl calls within its own 10 s budget.
PUSH_TIMEOUT_S = 15


class SatelliteTimezoneService:
    def __init__(self, client_registry_service):
        self._registry = client_registry_service
        # One push at a time per satellite, the zone read inside it: an
        # admission's push that read the old zone then cannot land after the
        # push a change sent with the new one.
        self._locks = defaultdict(asyncio.Lock)
        # Satellites whose app predates the route, reported once each until one
        # takes it (the next app update brings it).
        self._lacking_route = set()
        self._unreadable_reported = False

    def _server_zone(self):
        """The zone the server runs on, or None when its /etc/localtime names none."""
        zone = current_timezone()
        if zone is None:
            if not self._unreadable_reported:
                self._unreadable_reported = True
                logger.warning(
                    "The server's timezone cannot be read (/etc/localtime is not a link into the "
                    "zoneinfo tree): the satellites keep theirs"
                )
            return None
        self._unreadable_reported = False
        return zone

    async def _send(self, client_ip: str, zone: str) -> None:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=PUSH_TIMEOUT_S)) as session:
                async with session.put(
                    f"http://{client_ip}:{CLIENT_API_PORT}/api/system/timezone",
                    json={"timezone": zone},
                ) as resp:
                    if resp.status == 200:
                        body = await resp.json()
                        self._lacking_route.discard(client_ip)
                        if body["data"]["changed"]:
                            logger.info("Satellite %s now runs on %s, the server's timezone", client_ip, zone)
                        return
                    detail = await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning("Could not hand timezone %s to satellite %s: %s", zone, client_ip, exc or type(exc).__name__)
            return

        if resp.status == 404:
            if client_ip in self._lacking_route:
                logger.debug("Satellite %s still cannot follow the server's timezone", client_ip)
            else:
                self._lacking_route.add(client_ip)
                logger.info("Satellite %s cannot follow the server's timezone yet: its app predates it", client_ip)
        else:
            logger.warning("Satellite %s refused timezone %s (HTTP %s): %s", client_ip, zone, resp.status, detail)

    async def push(self, client_ip: str) -> None:
        """Hand the server's zone to one satellite; nothing when the server's cannot be read."""
        async with self._locks[client_ip]:
            zone = self._server_zone()
            if zone is not None:
                await self._send(client_ip, zone)

    async def push_all(self) -> None:
        """Hand the server's zone to every satellite online, after a change of it."""
        satellites = [c for c in self._registry.get_online_clients() if not c.is_local]
        await asyncio.gather(*(self.push(c.ip) for c in satellites))
