"""
Tests for SatelliteTimezoneService — the server's timezone handed to every satellite.

The outside world is the satellite's HTTP surface (an aiohttp session) and the
server's own `/etc/localtime`, relocated so `current_timezone` runs its real
readlink. If these fail: a satellite is handed a zone other than the one the
server runs on (or a browser's), a satellite whose app predates the route fills
the log at every admission, or one that refuses or is gone breaks the admission
or the settings screen that pushed to it.
"""
import asyncio
import json
import logging
from unittest.mock import Mock, patch

import aiohttp
import pytest

from backend.core.multiroom.satellite_timezone import SatelliteTimezoneService
from backend.core.system import localtime as localtime_module


class _Response:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    async def text(self):
        return str(self._payload)


class _Put:
    """One request in flight: it reaches the satellite once `gate` opens."""

    def __init__(self, satellite, url, body):
        self._satellite = satellite
        self._url = url
        self._body = body

    async def __aenter__(self):
        if self._satellite.error is not None:
            raise self._satellite.error
        gate = self._satellite.gates.pop(0) if self._satellite.gates else None
        if gate is not None:
            await gate.wait()
        self._satellite.applied.append((self._url, self._body))
        return _Response(self._satellite.status, self._satellite.payload)

    async def __aexit__(self, *exc):
        return False


class _Satellite:
    """Answers every PUT with one status and payload, and records what reached it — or fails to connect."""

    def __init__(self, status=200, payload=None, error=None):
        self.status = status
        self.payload = {"status": "success", "data": {"changed": True}} if payload is None else payload
        self.error = error
        self.applied = []
        self.gates = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def put(self, url, json=None, **kwargs):
        return _Put(self, url, json)


@pytest.fixture
def localtime(tmp_path, monkeypatch):
    """The server's `/etc/localtime`, relocated: `zone(name)` points it into the zoneinfo tree, `point_at` anywhere."""
    link = tmp_path / "localtime"
    monkeypatch.setattr(localtime_module, "LOCALTIME_LINK", str(link))

    def point_at(target):
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(target)

    def zone(name):
        point_at(f"{localtime_module.ZONEINFO_PREFIX}{name}")

    zone.point_at = point_at
    return zone


def _service(satellites=()):
    registry = Mock()
    registry.get_online_clients = Mock(return_value=list(satellites))
    return SatelliteTimezoneService(registry)


def _session(satellite):
    return patch("backend.core.multiroom.satellite_timezone.aiohttp.ClientSession", return_value=satellite)


async def test_the_satellite_is_handed_the_zone_the_server_runs_on(localtime):
    """Read off the system, not a browser: right before any browser opened the UI."""
    localtime("America/Los_Angeles")
    satellite = _Satellite()

    with _session(satellite):
        await _service().push("192.168.1.60")

    assert satellite.applied == [
        ("http://192.168.1.60:8001/api/system/timezone", {"timezone": "America/Los_Angeles"}),
    ]


async def test_a_server_whose_zone_cannot_be_read_never_addresses_the_satellite(localtime, caplog):
    """A satellite handed a guess would be wrong in a way nothing reports."""
    localtime.point_at("/opt/not-a-zone")
    satellite = _Satellite()
    service = _service()

    with _session(satellite), caplog.at_level(logging.WARNING, logger="backend.core.multiroom.satellite_timezone"):
        await service.push("192.168.1.60")
        await service.push("192.168.1.153")

    assert satellite.applied == []
    assert len(caplog.records) == 1, [r.getMessage() for r in caplog.records]


async def test_every_online_satellite_is_handed_it_and_never_the_main_unit(localtime):
    localtime("Europe/Paris")
    satellites = [Mock(ip="192.168.1.60", is_local=False), Mock(ip="127.0.0.1", is_local=True),
                  Mock(ip="192.168.1.153", is_local=False)]
    satellite = _Satellite()

    with _session(satellite):
        await _service(satellites).push_all()

    assert sorted(satellite.applied) == [
        ("http://192.168.1.153:8001/api/system/timezone", {"timezone": "Europe/Paris"}),
        ("http://192.168.1.60:8001/api/system/timezone", {"timezone": "Europe/Paris"}),
    ]


async def test_a_satellite_without_the_route_is_reported_once_and_never_as_an_error(localtime, caplog):
    """Every satellite in the field answers 404 until its next app update.

    One info line for it, then debug: the push rides on every admission, and an
    ERROR would be the red banner on every open screen. Each push is one
    request — the next admission is the retry, never a loop.
    """
    localtime("Europe/Paris")
    satellite = _Satellite(status=404, payload={"detail": "Not Found"})
    service = _service()

    with _session(satellite), caplog.at_level(logging.DEBUG, logger="backend.core.multiroom.satellite_timezone"):
        for _ in range(3):
            await service.push("192.168.1.60")

    assert len(satellite.applied) == 3
    assert [r.levelname for r in caplog.records] == ["INFO", "DEBUG", "DEBUG"]


async def test_a_satellite_updated_since_is_reported_again_if_it_loses_the_route(localtime, caplog):
    """The once-only report is per absence, not per process lifetime."""
    localtime("Europe/Paris")
    service = _service()

    with caplog.at_level(logging.INFO, logger="backend.core.multiroom.satellite_timezone"):
        for status in (404, 200, 404):
            with _session(_Satellite(status=status, payload={"detail": "Not Found"} if status == 404 else None)):
                await service.push("192.168.1.60")

    lacking = [r for r in caplog.records if "predates" in r.getMessage()]
    assert len(lacking) == 2


async def test_a_satellite_that_refuses_or_is_gone_raises_nothing(localtime, caplog):
    """The push rides on an admission: a raise there would be a failed admission over a clock."""
    localtime("Europe/Paris")
    for satellite in (
        _Satellite(status=400, payload={"detail": "ERROR: unknown timezone 'Europe/Paris'"}),
        _Satellite(status=500, payload={"detail": "Access denied"}),
        _Satellite(error=aiohttp.ClientConnectionError("unreachable")),
        _Satellite(error=asyncio.TimeoutError()),
    ):
        with _session(satellite):
            await _service().push("192.168.1.60")

    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.parametrize("payload", [
    {},
    {"status": "success", "data": None},
    ["not", "an", "object"],
    json.JSONDecodeError("Expecting value", "<html>", 0),
], ids=["empty", "no-data", "a-list", "not-json"])
async def test_a_200_that_is_not_the_satellites_answer_raises_nothing(localtime, caplog, payload):
    """Something else on port 8001, or a satellite whose answer drifted.

    The body is read past the status, so a key it lacks is a KeyError and a body
    that is not JSON a ValueError — neither is aiohttp's. Raised, the admission
    task dies at ERROR in `BackgroundTaskSet` (the red banner, at every
    admission) and the push after a change dies as an ASGI traceback.
    """
    localtime("Europe/Paris")
    satellite = _Satellite(payload=payload)

    with _session(satellite), caplog.at_level(logging.WARNING, logger="backend.core.multiroom.satellite_timezone"):
        await _service().push("192.168.1.60")

    assert [r.levelname for r in caplog.records] == ["WARNING"]
    assert "192.168.1.60" in caplog.records[0].getMessage()


async def test_a_push_that_read_the_old_zone_never_lands_after_the_new_one(localtime):
    """An admission in flight while the owner changes the zone.

    Both requests go out; if the admission's, which read the old zone, reached
    the satellite last, the unit would stay on it until its next admission.
    """
    localtime("Etc/UTC")
    satellite = _Satellite()
    in_flight = asyncio.Event()
    satellite.gates = [in_flight]
    service = _service([Mock(ip="192.168.1.60", is_local=False)])

    with _session(satellite):
        admission = asyncio.create_task(service.push("192.168.1.60"))
        await asyncio.sleep(0)
        localtime("Europe/Paris")
        change = asyncio.create_task(service.push_all())
        await asyncio.sleep(0)
        in_flight.set()
        await asyncio.gather(admission, change)

    assert [body["timezone"] for _, body in satellite.applied] == ["Etc/UTC", "Europe/Paris"]
