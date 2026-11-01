"""The satellite's request gate, driven through the real app stack.

Two halves that pull against each other. Without the gate, a page open in any
browser on the LAN posts a tarball to `/app/update` (a multipart form is a CORS
simple request: no preflight) and the deploy wrapper installs its `rootfs/` as
root, sudoers included; or it reboots the speaker with a bodyless POST. A gate
one notch too strict locks the server out instead: volume, EQ and the update
push itself all arrive on this port, and the update endpoint is the one that
would have carried the fix.

Consumer: the server, over aiohttp, which addresses a satellite by its IP (or
`<hostname>.local`) and sends no `Origin`. No browser calls this port.
"""
import io
import tarfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import main
from routes.hardware import APPLY_HARDWARE_SCRIPT
from services.app_update import DEPLOY_SCRIPT

SATELLITE = "192.168.1.60:8001"
FOREIGN_ORIGIN = "https://evil.example"


@pytest.fixture
def client():
    """The real `main.app`, outside `with` so the lifespan (CamillaDSP loop,
    registration with the server) never runs. The base URL is a satellite's
    address: TestClient's default Host, `testserver`, is not one."""
    return TestClient(main.app, base_url=f"http://{SATELLITE}")


@pytest.fixture
def sudo():
    """Every privileged step on a satellite is a `sudo` child process.

    The stand-in exits 1, so a run without the gate stops at the first
    privileged step instead of going on to the files a real satellite writes
    next — `/var/lib/milo/settings.json` among them, which on a dev host is the
    server's own.
    """
    process = MagicMock(returncode=1)
    process.communicate = AsyncMock(return_value=(b"", b"refused by the test"))
    process.wait = AsyncMock(return_value=1)
    process.stderr.read = AsyncMock(return_value=b"refused by the test")
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)) as exec_:
        yield exec_


def _sudo_ran(exec_, script):
    return any(call.args[:2] == ("sudo", script) for call in exec_.call_args_list)


def _update_form():
    """A `POST /app/update` form whose `rootfs/` grants the app account
    everything: what the attack ships, in the shape of the server's own push."""
    grant = b"milo-client ALL=(ALL) NOPASSWD: ALL\n"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        member = tarfile.TarInfo("milo-client/rootfs/etc/sudoers.d/zz")
        member.size = len(grant)
        tar.addfile(member, io.BytesIO(grant))
    return {
        "files": {"tarball": ("update.tar.gz", buffer.getvalue(), "application/gzip")},
        "data": {"payload": "forged", "version": "v9.9.9"},
    }


def test_a_cross_site_app_update_is_refused_before_the_handler(client, sudo):
    """Root on the speaker, kept across every later push: the forged payload
    equals the server's own, so nothing ever overwrites it."""
    response = client.post("/app/update", headers={"Origin": FOREIGN_ORIGIN}, **_update_form())

    assert response.status_code == 403
    assert not _sudo_ran(sudo, DEPLOY_SCRIPT)


def test_a_bodyless_reboot_from_a_page_is_refused(client, sudo):
    """The room goes silent for a reboot nobody asked for."""
    response = client.post("/api/hardware/reboot", headers={"Origin": FOREIGN_ORIGIN})

    assert response.status_code == 403
    assert not _sudo_ran(sudo, APPLY_HARDWARE_SCRIPT)


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("origin", [
    pytest.param(FOREIGN_ORIGIN, id="internet-page"),
    pytest.param("http://192.168.1.10", id="lan-page"),
    pytest.param("http://milo-client.local:8001", id="own-name"),
    pytest.param("null", id="sandboxed-frame"),
])
def test_any_origin_on_a_write_is_refused(client, method, origin):
    """No browser legitimately writes here, so any Origin at all is a page.

    Stricter than the server, whose kiosk and phones are browsers: the route
    does not exist, and a 404 or a 405 means the request got past the gate.
    """
    response = client.request(method, "/definitely-not-a-route", headers={"Origin": origin})

    assert response.status_code == 403


@pytest.mark.parametrize("host", [
    pytest.param("rebind.example", id="rebound-name"),
    pytest.param("rebind.example:8001", id="rebound-name-with-port"),
    pytest.param("milo-client.local.rebind.example", id="own-name-as-a-prefix"),
    pytest.param("8.8.8.8", id="public-address"),
    pytest.param("100.64.0.1", id="carrier-grade-nat"),
    pytest.param("[fe80::1", id="malformed-ipv6"),
])
def test_a_rebound_host_is_refused(client, host):
    """Under DNS rebinding the page is same-origin to its browser and sends no
    Origin at all; the Host header still names what the page was served from."""
    assert client.get("/health", headers={"Host": host}).status_code == 403


def test_a_forwarded_header_does_not_vouch_for_a_request(client):
    """Nothing proxies this port, so an `X-Forwarded-*` header is the client's
    own claim — and the client is the page being refused."""
    vouched = {"X-Forwarded-Host": "milo-client.local"}

    assert client.get("/health", headers={"Host": "rebind.example", **vouched}).status_code == 403
    assert client.post(
        "/definitely-not-a-route", headers={"Origin": FOREIGN_ORIGIN, **vouched},
    ).status_code == 403


@pytest.mark.parametrize("host", [
    pytest.param(SATELLITE, id="address-the-server-dials"),
    pytest.param("192.168.1.153", id="address-without-port"),
    pytest.param("milo-client.local", id="mdns-name"),
    pytest.param("milo-client-2.local:8001", id="avahi-renamed-unit"),
    pytest.param("[fe80::1]:8001", id="ipv6-link-local"),
    pytest.param("localhost:8001", id="sat-status-on-the-unit"),
    pytest.param("127.0.0.1:8001", id="loopback-address"),
])
def test_the_servers_own_call_passes(client, host):
    """Every name the server can dial a satellite by, and `curl localhost` on
    the unit itself. A 403 here is a speaker the server can no longer steer."""
    assert client.get("/health", headers={"Host": host}).status_code == 200
    assert client.post("/definitely-not-a-route", headers={"Host": host}).status_code == 404


def test_the_servers_update_push_still_reaches_the_deploy_step(client, sudo):
    """The update endpoint carries every later fix, the gate's included: a push
    the gate refused could only be undone over SSH, unit by unit."""
    client.post("/app/update", **_update_form())

    assert _sudo_ran(sudo, DEPLOY_SCRIPT)
