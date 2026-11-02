# backend/tests/test_request_origin_gate.py
"""The gate that makes CORS a request check instead of only a response check.

Every test here is a way the appliance is legitimately reached, or a way it is
attacked. The two halves pull against each other: a gate that only admits the four
CORS origins locks out the Pi kiosk, an address-typed URL, an Avahi-renamed unit and
the name the router's DNS gives the unit, while one that admits anything named is a
`POST /api/programs/milo/update` away from rebooting the unit from a page on the open
web.

Consumers: the Vue frontend (served by nginx, by the kiosk over loopback, and by the
Vite dev server), Milo-Mac (no `Origin` at all), and curl on the unit itself. The
last section is the tracker beside the gate, read by the network service's
home-network retry while the recovery access point is up.
"""
import contextlib

import pytest
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.testclient import TestClient

from backend.api.middleware import (
    ALLOWED_ORIGINS, RecoveryClientTracker, RequestOriginGate, client_address,
)


@pytest.fixture
def client():
    app = FastAPI()

    @app.get("/api/thing")
    async def read():
        return {"status": "success"}

    @app.post("/api/thing")
    async def write():
        return {"status": "success"}

    @app.websocket("/ws")
    async def feed(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text("state")

    app.add_middleware(RequestOriginGate)
    return TestClient(app)


def headers(host, origin=None):
    h = {"Host": host}
    if origin:
        h["Origin"] = origin
    return h


# --------------------------------------------------------------------------- #
# The ways the appliance is legitimately reached. Each of these is a lockout if
# the gate is narrowed to the CORS allowlist.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("host,origin", [
    pytest.param("milo.local", "http://milo.local", id="mdns-name"),
    pytest.param("localhost", "http://localhost", id="pi-kiosk-over-loopback"),
    pytest.param("192.168.1.50", "http://192.168.1.50", id="address-typed-in-the-bar"),
    pytest.param("milo-2.local", "http://milo-2.local", id="avahi-renamed-unit"),
    pytest.param("127.0.0.1:8000", "http://localhost:5173", id="vite-dev-proxy"),
    pytest.param("milo.local:5173", "http://milo.local:5173", id="vite-dev-server-on-the-unit"),
])
def test_a_real_way_of_reaching_the_unit_can_still_write(client, host, origin):
    """A 403 here is the whole UI dead for whoever reaches the unit that way."""
    assert client.post("/api/thing", headers=headers(host, origin)).status_code == 200


def test_a_client_that_sends_no_origin_is_served(client):
    """Milo-Mac builds a bare URLRequest; a missing Origin must not read as foreign."""
    assert client.post("/api/thing", headers=headers("192.168.1.50")).status_code == 200


@pytest.mark.parametrize("host,origin", [
    pytest.param("milo.local", None, id="native-client-no-origin"),
    pytest.param("localhost", "http://localhost", id="pi-kiosk-over-loopback"),
    pytest.param("192.168.1.50", "http://192.168.1.50", id="address-typed-in-the-bar"),
    pytest.param("milo.local:8000", "http://localhost:5173", id="vite-dev-server"),
])
def test_a_real_way_of_reaching_the_unit_can_open_the_socket(client, host, origin):
    """A refused upgrade is a screen that never shows the state, or Milo-Mac's live feed cut."""
    with client.websocket_connect("/ws", headers=headers(host, origin)) as ws:
        assert ws.receive_text() == "state"


@pytest.mark.parametrize("host", [
    pytest.param("milo.lan", id="openwrt"),
    pytest.param("milo.home", id="isp-router"),
    pytest.param("milo.home.arpa", id="pfsense-rfc-8375"),
    pytest.param("milo.internal", id="icann-private-tld"),
    pytest.param("milo.localdomain", id="dnsmasq-default"),
])
def test_the_router_dns_name_of_the_unit_is_served(client, host):
    """Often the only name that resolves on a device without mDNS (Windows, Android,
    a VLAN that filters multicast): nginx serves the page under it, so a 403 here is
    an interface that loads and then never shows a state nor takes a command."""
    origin = f"http://{host}"
    assert client.get("/api/thing", headers=headers(host)).status_code == 200
    assert client.post("/api/thing", headers=headers(host, origin)).status_code == 200
    with client.websocket_connect("/ws", headers=headers(host, origin)) as ws:
        assert ws.receive_text() == "state"


# --------------------------------------------------------------------------- #
# The attacks.
# --------------------------------------------------------------------------- #

def test_a_foreign_origin_cannot_write(client):
    """The bodyless cross-site POST that reboots the unit mid-listening."""
    response = client.post("/api/thing", headers=headers("milo.local", "http://evil.example"))
    assert response.status_code == 403
    assert response.json()["status"] == "error"


def test_a_foreign_origin_may_still_read(client):
    """CORS already stops the page reading the reply; a GET changes nothing."""
    assert client.get("/api/thing", headers=headers("milo.local", "http://evil.example")).status_code == 200


@pytest.mark.parametrize("method", ["get", "post"])
def test_a_rebound_name_is_refused_whatever_the_method(client, method):
    """DNS rebinding makes the call same-origin, so only the Host header still tells."""
    call = getattr(client, method)
    assert call("/api/thing", headers=headers("evil.example")).status_code == 403


def test_a_public_address_is_not_an_appliance_address(client):
    """Else the attacker just serves the page from their own IP instead of a name."""
    response = client.post("/api/thing", headers=headers("milo.local", "http://93.184.216.34"))
    assert response.status_code == 403


def test_an_opaque_origin_cannot_write(client):
    """A sandboxed iframe sends `Origin: null`; that is not a missing Origin."""
    assert client.post("/api/thing", headers=headers("milo.local", "null")).status_code == 403


@pytest.mark.parametrize("host,origin", [
    pytest.param("evil.example", None, id="rebound-name"),
    pytest.param("milo.local", "https://evil.example", id="page-from-the-internet"),
])
def test_a_foreign_page_cannot_read_the_socket(client, host, origin):
    """CORS does not cover a WebSocket: an upgrade let through is the whole state read."""
    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect("/ws", headers=headers(host, origin)):
            pass
    assert refused.value.code == 1008


@pytest.mark.parametrize("host", [
    pytest.param("milo.example.com", id="public-domain"),
    pytest.param("milo.fritz.box", id="delegated-box-tld"),
    pytest.param("milo.attacker.box", id="attacker-under-box"),
    pytest.param("milo.home.evil.example", id="reserved-suffix-as-a-label"),
    pytest.param("milo.home.arpa.evil.example", id="compound-suffix-as-labels"),
])
def test_the_unit_name_under_a_foreign_domain_is_still_refused(client, host):
    """Under a delegated domain whoever holds it decides what the name resolves to,
    so `milo.<theirs>` is a rebinding open again: the router-name suffixes must stay
    a closed list of names nobody outside the LAN can make resolve."""
    origin = f"http://{host}"
    assert client.get("/api/thing", headers=headers(host)).status_code == 403
    assert client.post("/api/thing", headers=headers(host, origin)).status_code == 403
    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect("/ws", headers=headers(host, origin)):
            pass
    assert refused.value.code == 1008


def test_a_host_that_names_nothing_is_refused(client):
    """A request with no Host predates HTTP/1.1 and is not one of ours."""
    assert client.post("/api/thing", headers={"Host": ""}).status_code == 403


# --------------------------------------------------------------------------- #
# Wiring. A gate nobody installed is the failure mode with no symptom.
# --------------------------------------------------------------------------- #

def test_the_gate_is_installed_on_the_app():
    from backend.main import app

    assert RequestOriginGate in [m.cls for m in app.user_middleware]


def test_cors_and_the_gate_read_one_allowlist():
    """Two copies of the origins is how a reader keeps reading while its writes 403."""
    from fastapi.middleware.cors import CORSMiddleware

    from backend.main import app

    cors = next(m for m in app.user_middleware if m.cls is CORSMiddleware)
    assert cors.kwargs["allow_origins"] is ALLOWED_ORIGINS


# --------------------------------------------------------------------------- #
# Who is on the recovery access point. Read by the station retry, which must
# not take the AP from under someone repairing the unit over it.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("peer,real_ip,expected", [
    pytest.param(("127.0.0.1", 41000), "10.42.0.5", "10.42.0.5", id="nginx-names-the-client"),
    pytest.param(("127.0.0.1", 41000), None, "127.0.0.1", id="loopback-with-no-proxy"),
    pytest.param(("192.168.1.20", 41000), "10.42.0.5", "192.168.1.20", id="header-sent-straight-to-8000"),
    pytest.param(None, None, None, id="no-peer-at-all"),
])
def test_the_client_address_believes_nginx_alone(peer, real_ip, expected):
    """uvicorn listens on 0.0.0.0, so anyone on the LAN can reach :8000 with any
    header; only nginx, on loopback, is entitled to name the client."""
    headers = [(b"x-real-ip", real_ip.encode())] if real_ip else []
    assert client_address({"type": "http", "client": peer, "headers": headers}) == expected


class RecordingNetwork:
    """Stands in for what the tracker reads of NetworkService: the AP flag and the hook."""

    def __init__(self):
        self.hotspot_active = True
        self.open = 0
        self.served = 0

    @contextlib.contextmanager
    def serving_recovery_client(self):
        self.open += 1
        try:
            yield
        finally:
            self.open -= 1
            self.served += 1


@pytest.fixture
def network():
    return RecordingNetwork()


@pytest.fixture
def tracked(network):
    app = FastAPI()

    @app.get("/api/thing")
    async def read():
        return {"open": network.open}

    @app.websocket("/ws")
    async def feed(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text(str(network.open))
        with contextlib.suppress(WebSocketDisconnect):
            await websocket.receive_text()

    app.add_middleware(RecoveryClientTracker, network_service=network)
    return app


@pytest.mark.parametrize("peer,real_ip,on_the_ap", [
    pytest.param("127.0.0.1", "10.42.0.5", True, id="phone-on-the-ap-through-nginx"),
    pytest.param("127.0.0.1", "192.168.1.20", False, id="lan-client-through-nginx"),
    pytest.param("127.0.0.1", None, False, id="kiosk-over-loopback"),
    pytest.param("192.168.1.20", "10.42.0.5", False, id="forged-header-straight-to-8000"),
])
def test_a_request_counts_as_the_access_points_while_it_runs_and_only_from_it(
    tracked, network, peer, real_ip, on_the_ap,
):
    """A request the tracker misses lets a retry drop the page using the AP; one
    it counts from elsewhere holds a WiFi-only unit off its home network."""
    client = TestClient(tracked, client=(peer, 41000))
    response = client.get("/api/thing", headers={"X-Real-IP": real_ip} if real_ip else {})

    assert response.json()["open"] == (1 if on_the_ap else 0)
    assert (network.open, network.served) == (0, 1 if on_the_ap else 0)


def test_nothing_counts_while_no_access_point_is_up(tracked, network):
    """A home LAN that happens to be 10.42.0.0/24 is not a recovery AP."""
    network.hotspot_active = False
    client = TestClient(tracked, client=("127.0.0.1", 41000))
    response = client.get("/api/thing", headers={"X-Real-IP": "10.42.0.5"})

    assert (response.json()["open"], network.served) == (0, 0)


def test_a_socket_from_the_access_point_counts_for_as_long_as_it_is_open(tracked, network):
    """The setup page holds `/ws` open for minutes without a single HTTP request."""
    client = TestClient(tracked, client=("127.0.0.1", 41000))
    with client.websocket_connect("/ws", headers={"X-Real-IP": "10.42.0.5"}) as ws:
        assert ws.receive_text() == "1"
        assert network.open == 1

    assert network.open == 0


def test_the_tracker_is_handed_the_network_service_inside_the_gate():
    """Installed without the service there is nothing to tell; outside the gate a
    request it refuses would count as someone using the AP."""
    from backend import main

    stack = [m.cls for m in main.app.user_middleware]
    tracker = next(m for m in main.app.user_middleware if m.cls is RecoveryClientTracker)
    assert tracker.kwargs["network_service"] is main.network_service
    assert stack.index(RequestOriginGate) < stack.index(RecoveryClientTracker)
