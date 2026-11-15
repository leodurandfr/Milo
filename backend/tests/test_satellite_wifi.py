"""
Tests for SatelliteWifiService — the server's WiFi handed to every satellite.

The outside world is the satellite's HTTP surface (an aiohttp session) and
the server's NetworkManager, read through the real network service (`nmcli`). If
these fail: an Ethernet-only server sends satellites an empty network, a
satellite that refuses or is gone breaks the admission that pushed to it, or
the satellite is handed a network other than the one the server is on.
"""
import asyncio
import json
import logging
from unittest.mock import AsyncMock, Mock, call, patch

import aiohttp
import pytest

from backend.core.multiroom.satellite_wifi import KEY_READ_RETRY_S, KEY_READ_TRIES, SatelliteWifiService
from backend.core.network.service import NetworkService
from backend.shared.background import BackgroundTaskSet


class _Response:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
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


class _Nmcli:
    """`nmcli` as the server's NetworkManager answers the two reads: wlan0's
    active profile, then that profile, whose key line is each of `psk_lines` in
    turn (the last one repeating; None leaves it out) — verbatim shapes."""

    def __init__(self, *psk_lines, active="milo-Freebox-CA3555:3b7f2a9c-6d41-4e0b-9a55-1c2d3e4f5a6b:wlan0",
                 hidden="no", key_mgmt="wpa-psk"):
        self._psk_lines = list(psk_lines or ["802-11-wireless-security.psk:correct-horse"])
        self._active = active
        self._fields = (
            "802-11-wireless.ssid:Freebox-CA3555\n"
            f"802-11-wireless.hidden:{hidden}\n"
            f"802-11-wireless-security.key-mgmt:{key_mgmt}"
        )
        self.profile_reads = 0

    async def __call__(self, program, *args, **kwargs):
        assert program == "nmcli"
        if "--active" in args:
            out = self._active
        else:
            self.profile_reads += 1
            line = self._psk_lines.pop(0) if len(self._psk_lines) > 1 else self._psk_lines[0]
            out = self._fields + ("" if line is None else "\n" + line)
        proc = Mock(returncode=0)
        proc.communicate = AsyncMock(return_value=(out.encode(), b""))
        return proc


_EMPTY_KEY = "802-11-wireless-security.psk:"


def _service(satellites=()):
    settings = Mock()
    settings.get_setting = AsyncMock(return_value="FR")
    registry = Mock()
    registry.get_online_clients = Mock(return_value=list(satellites))
    return SatelliteWifiService(NetworkService(AsyncMock(), settings), registry)


def _server(nmcli=None):
    return patch("backend.core.network.service.asyncio.create_subprocess_exec", new=nmcli or _Nmcli())


def _session(satellite):
    return patch("backend.core.multiroom.satellite_wifi.aiohttp.ClientSession", return_value=satellite)


async def test_a_server_on_no_wifi_never_addresses_the_satellite():
    satellite = _Satellite()

    with _session(satellite), _server(_Nmcli(active="Wired connection 1:a1f0c0de-0000-4000-8000-000000000001:eth0")):
        await _service().push("192.168.1.60")

    assert satellite.requests == []


async def test_the_satellite_is_handed_the_servers_network_and_country():
    satellite = _Satellite()

    with _session(satellite), _server():
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
        with _session(satellite), _server():
            await _service().push("192.168.1.60")


@pytest.mark.parametrize("payload", [
    {},
    {"status": "success", "data": None},
    ["not", "an", "object"],
    json.JSONDecodeError("Expecting value", "<html>", 0),
], ids=["empty", "no-data", "a-list", "not-json"])
async def test_a_200_that_is_not_the_satellites_answer_raises_nothing(caplog, payload):
    """Something else on port 8001, or a satellite whose answer drifted.

    A missing key is a KeyError and a body that is not JSON a ValueError, neither
    of them aiohttp's: raised, the admission task dies at ERROR in
    `BackgroundTaskSet`, the red banner at every admission.
    """
    satellite = _Satellite(payload=payload)

    with _session(satellite), _server(), \
            caplog.at_level(logging.WARNING, logger="backend.core.multiroom.satellite_wifi"):
        await _service().push("192.168.1.60")

    assert [r.levelname for r in caplog.records] == ["WARNING"]
    assert "192.168.1.60" in caplog.records[0].getMessage()


async def test_every_online_satellite_is_handed_it_and_never_the_main_unit():
    """Read once for all of them: the PSK read forks nmcli on the server."""
    satellites = [Mock(ip="192.168.1.60", is_local=False), Mock(ip="127.0.0.1", is_local=True),
                  Mock(ip="192.168.1.153", is_local=False)]
    satellite = _Satellite()
    nmcli = _Nmcli()

    with _session(satellite), _server(nmcli):
        await _service(satellites=satellites).push_all()

    assert sorted(url for url, _ in satellite.requests) == [
        "http://192.168.1.153:8001/api/system/wifi",
        "http://192.168.1.60:8001/api/system/wifi",
    ]
    assert nmcli.profile_reads == 1


async def test_a_network_its_name_and_key_cannot_rebuild_is_never_handed_over():
    """The satellite would keep a profile that never associates, and the log would call it a standby link."""
    for network in (
        {"hidden": "yes"},
        {"key_mgmt": "sae"},
        {"key_mgmt": "wpa-eap"},
    ):
        satellite = _Satellite()

        with _session(satellite), _server(_Nmcli(**network)):
            await _service().push("192.168.1.60")

        assert satellite.requests == [], network


async def test_a_network_that_cannot_be_handed_over_is_reported_once(caplog):
    """Every admission pushes, and a line per admission for a network that will
    never change its kind is noise in the journal."""
    satellite = _Satellite()
    service = _service()

    with _session(satellite), _server(_Nmcli(key_mgmt="sae")), caplog.at_level(logging.INFO):
        await service.push("192.168.1.60")
        await service.push("192.168.1.153")

    lines = [r.getMessage() for r in caplog.records if "not handed to the satellites" in r.getMessage()]
    assert len(lines) == 1 and "sae" in lines[0], lines


async def test_an_empty_key_on_a_wpa_psk_profile_is_never_handed_over(caplog):
    """Measured on Bureau: a PSK read back empty (rc 0) was pushed as is, and its helper replaced the
    working wpa-psk profile by an open one — the standby link lost until the next admission."""
    for psk_line in (_EMPTY_KEY, None):
        satellite = _Satellite()
        service = _service()
        caplog.clear()

        with _session(satellite), _server(_Nmcli(psk_line)), caplog.at_level(logging.WARNING), \
                patch("asyncio.sleep", new=AsyncMock()):
            await service.push("192.168.1.60")

        assert satellite.requests == [], psk_line
        assert any(
            r.levelno == logging.WARNING and "milo-Freebox-CA3555" in r.getMessage() for r in caplog.records
        ), psk_line


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


async def test_a_key_that_reads_back_empty_once_is_read_again_and_handed_over(caplog):
    """Measured during a reboot: polkit stopped, NetworkManager answered an empty key. Given up at once,
    a satellite kept its stale standby key until the next admission or the next WiFi change."""
    satellite = _Satellite()
    nmcli = _Nmcli(_EMPTY_KEY, "802-11-wireless-security.psk:correct-horse")

    with _session(satellite), _server(nmcli), caplog.at_level(logging.WARNING), \
            patch("asyncio.sleep", new=AsyncMock()) as sleep:
        await _service().push("192.168.1.60")

    assert [body["password"] for _, body in satellite.requests] == ["correct-horse"]
    assert sleep.await_args_list == [call(KEY_READ_RETRY_S)]
    assert _warnings(caplog) == []


async def test_a_key_that_never_reads_is_reported_once_for_the_whole_push(caplog):
    """One line per push_all, whatever the number of satellites, naming the profile, and nothing
    sent: an empty key would rebuild the satellite's standby link as an open network."""
    satellites = [Mock(ip="192.168.1.60", is_local=False), Mock(ip="192.168.1.153", is_local=False)]
    satellite = _Satellite()
    nmcli = _Nmcli(_EMPTY_KEY)

    with _session(satellite), _server(nmcli), caplog.at_level(logging.WARNING), \
            patch("asyncio.sleep", new=AsyncMock()):
        await _service(satellites=satellites).push_all()

    assert satellite.requests == []
    assert nmcli.profile_reads == KEY_READ_TRIES
    warnings = _warnings(caplog)
    assert len(warnings) == 1, warnings
    assert "milo-Freebox-CA3555" in warnings[0]


async def test_a_satellite_gone_during_the_wait_is_not_addressed():
    """Picked before the wait, a satellite that rebooted meanwhile was still sent the PUT, which
    hung for the whole push timeout and logged a failure about a speaker already offline."""
    staying = Mock(ip="192.168.1.60", is_local=False)
    leaving = Mock(ip="192.168.1.153", is_local=False)
    online = [staying, leaving]
    satellite = _Satellite()
    service = _service()
    service._registry.get_online_clients = Mock(side_effect=lambda: list(online))

    async def leaves(_seconds):
        online.remove(leaving)

    with _session(satellite), _server(_Nmcli(_EMPTY_KEY, "802-11-wireless-security.psk:correct-horse")), \
            patch("asyncio.sleep", new=AsyncMock(side_effect=leaves)):
        await service.push_all()

    assert [url for url, _ in satellite.requests] == ["http://192.168.1.60:8001/api/system/wifi"]


async def test_a_backend_stop_during_the_wait_sends_nothing_and_says_nothing(caplog):
    """The teardown's cancel_all ends the push mid-wait: a PUT then would carry a key read at
    shutdown, and a WARNING would report a failure that is only the stop."""
    satellite = _Satellite()
    service = _service()
    nmcli = _Nmcli(_EMPTY_KEY, "802-11-wireless-security.psk:correct-horse")
    waiting = asyncio.Event()

    async def wait_forever(_seconds):
        waiting.set()
        await asyncio.Event().wait()

    bg = BackgroundTaskSet(logging.getLogger("test"), "test")
    with _session(satellite), _server(nmcli), caplog.at_level(logging.WARNING), \
            patch("asyncio.sleep", new=AsyncMock(side_effect=wait_forever)):
        push = bg.spawn(service.push("192.168.1.60"), label="push_wifi")
        await asyncio.wait({push, asyncio.ensure_future(waiting.wait())}, return_when=asyncio.FIRST_COMPLETED)
        assert waiting.is_set(), "the push never waited to read the key again"
        await bg.cancel_all()

    assert satellite.requests == []
    assert nmcli.profile_reads == 1
    assert _warnings(caplog) == []
