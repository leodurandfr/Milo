# backend/tests/test_request_origin_gate.py
"""The gate that makes CORS a request check instead of only a response check.

Every test here is a way the appliance is legitimately reached, or a way it is
attacked. The two halves pull against each other: a gate that only admits the four
CORS origins locks out the Pi kiosk, an address-typed URL and an Avahi-renamed unit,
while one that admits anything named is a `POST /api/programs/milo/update` away from
rebooting the unit from a page on the open web.

Consumers: the Vue frontend (served by nginx, by the kiosk over loopback, and by the
Vite dev server), Milo-Mac (no `Origin` at all), and curl on the unit itself.
"""
import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

from backend.api.middleware import ALLOWED_ORIGINS, RequestOriginGate


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
    pytest.param("100.117.193.57", "http://100.117.193.57", id="tailscale-from-off-lan"),
    pytest.param("127.0.0.1:8000", "http://localhost:5173", id="vite-dev-proxy"),
    pytest.param("milo.local:5173", "http://milo.local:5173", id="vite-dev-server-on-the-unit"),
])
def test_a_real_way_of_reaching_the_unit_can_still_write(client, host, origin):
    """A 403 here is the whole UI dead for whoever reaches the unit that way."""
    assert client.post("/api/thing", headers=headers(host, origin)).status_code == 200


def test_a_client_that_sends_no_origin_is_served(client):
    """Milo-Mac builds a bare URLRequest; a missing Origin must not read as foreign."""
    assert client.post("/api/thing", headers=headers("192.168.1.50")).status_code == 200


def test_the_websocket_upgrade_is_not_gated(client):
    """The gate is HTTP-scope only — gating it would cut Milo-Mac's live feed."""
    with client.websocket_connect("/ws") as ws:
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
