"""Tests for the calibration probe route.

The server sizes one buffer for the whole house from what these fields say, so
a field this route gets wrong is a buffer the whole house gets wrong. The two
that matter most are the interface -- which must be the one reaching the
*caller*, not merely one that is up -- and the route's refusal to fail.
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

from routes import create_probe_router  # noqa: E402
from routes import probe as probe_module  # noqa: E402

# Captured before the autouse fixture can patch it: one test exercises the
# real sampler, and the fixture would otherwise hand it the stub.
_REAL_SAMPLER = probe_module._sample_scheduling


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(create_probe_router())
    return TestClient(app)


@pytest.fixture(autouse=True)
def fast_scheduling():
    """The real sample sleeps for most of a second, which no test should pay."""
    with patch.object(
        probe_module, "_sample_scheduling",
        return_value={"p50_ms": 0.05, "p99_ms": 0.1, "max_ms": 0.4, "samples": 400},
    ):
        yield


def test_the_interface_reported_is_the_one_that_reaches_the_caller():
    """A satellite with both interfaces up sits on one subnet, and the link its
    audio crosses is decided by the route table, not by which interfaces exist.
    Reporting `wlan0` because a radio is associated would hand the server the
    wrong link for a unit whose traffic leaves over the cable."""
    with patch.object(probe_module, "_run", new=AsyncMock(
            return_value="1.2.3.4 via 192.168.1.254 dev eth0 src 192.168.1.153 uid 0")):
        app = FastAPI()
        app.include_router(create_probe_router())
        body = TestClient(app).get("/probe").json()

    assert body["link"]["interface"] == "eth0"
    assert body["link"]["kind"] == "ethernet"


def test_an_unresolvable_route_is_named_rather_than_raised(client):
    """`unavailable` is the contract /diagnostic already follows: a probe that
    cannot answer costs its own field, never the whole payload. A 500 here would
    cost the server the entire analysis over one unreadable value."""
    with patch.object(probe_module, "_run", new=AsyncMock(return_value=None)):
        response = client.get("/probe")

    assert response.status_code == 200
    body = response.json()
    assert body["link"]["interface"] is None
    assert "link.interface" in body["unavailable"]


def test_a_wireless_interface_reports_its_negotiated_rate(client):
    """Wi-Fi has no `speed` in sysfs, so it has to come from NetworkManager.

    Leaving it unknown is not free: the server then assumes a plain 802.11n
    link for every wireless satellite, and every analysis on a wireless fleet
    ends by warning that values had to be guessed -- on a fleet where nothing
    was actually wrong.
    """
    with patch.object(probe_module, "_run", new=AsyncMock(
            return_value="1.2.3.4 dev wlan0 src 192.168.1.34")), \
         patch.object(probe_module.Path, "exists", return_value=True), \
         patch.object(probe_module, "_wireless_link", new=AsyncMock(
             return_value={"rate_mbps": 270.0, "signal_percent": 61.0})):
        body = client.get("/probe").json()

    assert body["link"]["kind"] == "wifi"
    assert body["link"]["signal_percent"] == 61.0
    assert body["link"]["speed_mbps"] == 270.0
    assert body["unavailable"] == []


def test_the_signal_is_read_from_the_access_point_nmcli_marks_in_use():
    """`ACTIVE` is localized -- a unit in French answers `oui`. Keying on it
    would report no signal on a perfectly associated radio, and only on units
    whose locale someone changed. The access point is the one IN-USE marks
    `*`, which nmcli never translates -- not the first listed, a neighbor at
    87 here.

    The rate is not on that row: it is the device's CAPABILITIES.SPEED, read
    by the same `device show`, so one command answers both.
    """
    listing = ("CAPABILITIES.SPEED:270 Mb/s\n"
               "AP[1].IN-USE: \nAP[1].ACTIVE:non\nAP[1].SIGNAL:87\n"
               "AP[2].IN-USE:*\nAP[2].ACTIVE:oui\nAP[2].SIGNAL:61\n")

    with patch.object(probe_module, "_run", new=AsyncMock(return_value=listing)):
        link = asyncio.run(probe_module._wireless_link("wlan0"))

    assert link == {"rate_mbps": 270.0, "signal_percent": 61.0}


def test_scheduling_is_reported_as_a_tail_not_an_average(client):
    """The client's ALSA buffer is sized from the worst wake-up, not the typical
    one: an average hides exactly the outlier that empties a buffer."""
    body = client.get("/probe").json()

    assert set(body["scheduling"]) == {"p50_ms", "p99_ms", "max_ms", "samples"}
    assert body["scheduling"]["max_ms"] >= body["scheduling"]["p99_ms"]


def test_the_real_sampler_measures_overshoot_and_not_elapsed_time():
    """A sampler returning the interval itself instead of the overshoot would
    report a full 2 ms of scheduling latency on a perfectly idle machine, and
    push every installation onto a larger ALSA buffer than it needs.

    Runs the real function, with the sample count cut so the test costs
    milliseconds: patching it away here would leave the arithmetic untested,
    which is the only part of this route that can be silently wrong.
    """
    with patch.object(probe_module, "SCHED_SAMPLES", 20):
        result = _REAL_SAMPLER()

    assert result["samples"] == 20
    assert 0.0 <= result["p50_ms"] < probe_module.SCHED_INTERVAL_S * 1000
    assert result["max_ms"] >= result["p50_ms"]


class _Unit:
    """The processes /probe spawns, answered as nmcli 1.52 answered for the
    server's own wlan0 on 2026-11-06: the AP list's RATE is the access point's
    maximum (270), CAPABILITIES.SPEED the rate this link negotiated (NM's
    Device.Wireless.Bitrate, 433). `nmcli device wifi` rescans whenever its last
    scan is over 30 s old, unless told `--rescan no`."""

    def __init__(self, iface="wlan0"):
        self.iface = iface
        self.scans = 0

    async def __call__(self, *argv, **kwargs):
        if argv[:4] == ("ip", "-o", "route", "get"):
            return _Process(f"{argv[4]} dev {self.iface} src 192.168.1.60 uid 1001 \\    cache \n")
        assert argv[0] == "nmcli", argv
        if "wifi" in argv:
            if "--rescan" not in argv or argv[argv.index("--rescan") + 1] != "no":
                self.scans += 1
            return _Process(" :130 Mbit/s:87\n*:270 Mbit/s:61\n")
        if argv[-3:] == ("device", "show", self.iface):
            return _Process(
                "CAPABILITIES.CARRIER-DETECT:no\nCAPABILITIES.SPEED:433 Mb/s\n"
                "AP[1].IN-USE: \nAP[1].SIGNAL:87\nAP[1].RATE:130 Mbit/s\n"
                "AP[2].IN-USE:*\nAP[2].SIGNAL:61\nAP[2].RATE:270 Mbit/s\n"
            )
        return _Process("", returncode=10)


class _Process:
    def __init__(self, stdout, returncode=0):
        self._stdout = stdout.encode()
        self.returncode = returncode

    async def communicate(self):
        return self._stdout, None


def test_a_wireless_link_reports_the_rate_it_negotiated_without_scanning(client):
    """The server sizes PCM against this rate. The AP list's RATE is what the
    access point could do at best, not what this link runs at, and listing it
    can start a scan -- which disturbs the very link being measured."""
    unit = _Unit("wlan0")
    with patch.object(probe_module.asyncio, "create_subprocess_exec", new=unit), \
         patch.object(probe_module.Path, "exists", return_value=True):
        body = client.get("/probe").json()

    assert body["link"]["interface"] == "wlan0"
    assert (body["link"]["speed_mbps"], body["link"]["signal_percent"]) == (433.0, 61.0)
    assert unit.scans == 0
    assert body["unavailable"] == []


def test_a_cable_with_no_carrier_reports_no_rate(client):
    """sysfs answers -1 for an interface with no carrier: a link of -1 Mbit/s
    is no reading, and the server must assume one rather than divide by it."""
    unit = _Unit("eth0")
    sysfs = {"/sys/class/net/eth0/speed": "-1\n"}
    with patch.object(probe_module.asyncio, "create_subprocess_exec", new=unit), \
         patch.object(probe_module.Path, "exists", return_value=False), \
         patch.object(probe_module.Path, "read_text", autospec=True,
                      side_effect=lambda path, **kw: sysfs[str(path)]):
        body = client.get("/probe").json()

    assert body["link"]["kind"] == "ethernet"
    assert body["link"]["speed_mbps"] is None
    assert "link.speed_mbps" in body["unavailable"]
