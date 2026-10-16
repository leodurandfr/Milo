"""
Tests for SatelliteWifiService — the server's WiFi handed to every satellite.

The outside world is the satellite's HTTP surface (an aiohttp session) and
NetworkManager's view of the server (the network service's public reads). If
these fail: an Ethernet-only server sends satellites an empty network, a
satellite that refuses or is gone breaks the admission that pushed to it, or
the satellite is handed a network other than the one the server is on.
"""
import asyncio
from unittest.mock import AsyncMock, Mock, patch

import aiohttp

from backend.core.multiroom.satellite_wifi import SatelliteWifiService


class _Response:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return str(self._payload)


class _Satellite:
    """Answers every PUT with one status and payload, and records it — or fails to connect."""

    def __init__(self, status=200, payload=None, error=None):
        self.status = status
        self.payload = {"status": "success", "data": {"changed": True}} if payload is None else payload
        self.error = error
        self.requests = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def put(self, url, json=None, **kwargs):
        if self.error is not None:
            raise self.error
        self.requests.append((url, json))
        return _Response(self.status, self.payload)


_FREEBOX = {"ssid": "Freebox-CA3555", "password": "correct-horse", "hidden": False, "key_mgmt": "wpa-psk"}


def _service(profile=_FREEBOX, satellites=()):
    network = Mock()
    network.get_active_wifi_profile = AsyncMock(return_value=profile)
    network.get_country = AsyncMock(return_value="FR")
    registry = Mock()
    registry.get_online_clients = Mock(return_value=list(satellites))
    return SatelliteWifiService(network, registry)


def _session(satellite):
    return patch("backend.core.multiroom.satellite_wifi.aiohttp.ClientSession", return_value=satellite)


async def test_a_server_on_no_wifi_never_addresses_the_satellite():
    satellite = _Satellite()

    with _session(satellite):
        await _service(profile=None).push("192.168.1.60")

    assert satellite.requests == []


async def test_the_satellite_is_handed_the_servers_network_and_country():
    satellite = _Satellite()

    with _session(satellite):
        await _service().push("192.168.1.60")

    assert satellite.requests == [(
        "http://192.168.1.60:8001/api/system/wifi",
        {"ssid": "Freebox-CA3555", "password": "correct-horse", "country": "FR"},
    )]


async def test_a_satellite_that_refuses_or_is_gone_raises_nothing():
    """The push rides on an admission: a raise there would be a failed admission over a standby link."""
    for satellite in (
        _Satellite(status=500, payload={"detail": "ERROR: this unit has no wlan0"}),
        _Satellite(status=404, payload={"detail": "Not Found"}),
        _Satellite(error=aiohttp.ClientConnectionError("unreachable")),
        _Satellite(error=asyncio.TimeoutError()),
    ):
        with _session(satellite):
            await _service().push("192.168.1.60")


async def test_every_online_satellite_is_handed_it_and_never_the_main_unit():
    """Read once for all of them: the PSK read forks nmcli on the server."""
    satellites = [Mock(ip="192.168.1.60", is_local=False), Mock(ip="127.0.0.1", is_local=True),
                  Mock(ip="192.168.1.153", is_local=False)]
    satellite = _Satellite()
    service = _service(satellites=satellites)

    with _session(satellite):
        await service.push_all()

    assert sorted(url for url, _ in satellite.requests) == [
        "http://192.168.1.153:8001/api/system/wifi",
        "http://192.168.1.60:8001/api/system/wifi",
    ]
    assert service._network.get_active_wifi_profile.await_count == 1


async def test_a_network_its_name_and_key_cannot_rebuild_is_never_handed_over():
    """The satellite would keep a profile that never associates, and the log would call it a standby link."""
    for profile in (
        {**_FREEBOX, "hidden": True},
        {**_FREEBOX, "key_mgmt": "sae"},
        {**_FREEBOX, "key_mgmt": "wpa-eap", "password": ""},
    ):
        satellite = _Satellite()

        with _session(satellite):
            await _service(profile=profile).push("192.168.1.60")

        assert satellite.requests == [], profile
