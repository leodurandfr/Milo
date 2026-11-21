"""The satellite's paths to root answer its own server only (server_secret.py).

`milo-client` is root by construction: `POST /app/update` installs what it is
handed as root, and the SSH switch with the `milo` password is a login with
sudo. Before the secret, any host on the LAN could send either — `curl` from a
laptop was enough. Driven through the real `main.app`, so a route that drops
its guard, or a new route to root wired without one, fails here; the set of
guarded routes is held equal to what the server signs by
backend/tests/architecture/test_satellite_secret.py.

The outside world is the `sudo` child each route would spawn and the pin's
file, both kept under the test's control.
"""
import asyncio
import io
import logging
import stat
import tarfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import main
import server_secret
from server_secret import SECRET_FIELD, SECRET_HEADER, ServerSecretRefused, admit
from tests.conftest import ANOTHER_SERVER_SECRET, SERVER_SECRET

SATELLITE = "192.168.1.60:8001"


@pytest.fixture
def client():
    """The real app, outside `with` so the lifespan never runs, at a satellite's address."""
    return TestClient(main.app, base_url=f"http://{SATELLITE}")


@pytest.fixture
def sudo():
    """Every child process a route spawns; each exits 0 at once."""
    process = MagicMock(returncode=0)
    process.communicate = AsyncMock(return_value=(b"", b""))
    process.wait = AsyncMock(return_value=0)
    process.stderr.read = AsyncMock(return_value=b"")
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)) as spawn:
        yield spawn


def _tarball():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        grant = b"milo-client ALL=(ALL) NOPASSWD: ALL\n"
        member = tarfile.TarInfo("milo-client/rootfs/etc/sudoers.d/zz")
        member.size = len(grant)
        tar.addfile(member, io.BytesIO(grant))
    return buffer.getvalue()


def _send(client, route, secret=None):
    """One request to a route that reaches root, carrying `secret` the way
    that route takes it (None: no secret at all)."""
    method, path = route
    if path == "/app/update":
        data = {"payload": "forged", "version": "v9.9.9"}
        if secret is not None:
            data[SECRET_FIELD] = secret
        return client.post(path, files={"tarball": ("u.tar.gz", _tarball(), "application/gzip")}, data=data)
    headers = {SECRET_HEADER: secret} if secret is not None else {}
    body = {
        "/api/system/ssh": {"enabled": True},
        "/api/system/password": {"password": "correct horse"},
    }.get(path)
    return client.request(method, path, headers=headers, **({"json": body} if body else {}))


# Every route that reaches root on a satellite, as the server sends it.
TO_ROOT = [
    ("POST", "/app/update"),
    ("PUT", "/api/system/ssh"),
    ("POST", "/api/system/password"),
    ("POST", "/api/hardware/reboot"),
    ("PUT", "/api/system/server-secret"),
]
IDS = [f"{m} {p}" for m, p in TO_ROOT]


@pytest.mark.parametrize("route", TO_ROOT, ids=IDS)
def test_a_request_without_the_secret_reaches_nothing(client, sudo, server, route):
    """The curl from the Mac: a 200 here is root on the speaker for anyone on the LAN."""
    response = _send(client, route)

    assert response.status_code == 403, response.text
    sudo.assert_not_awaited()


@pytest.mark.parametrize("route", TO_ROOT, ids=IDS)
def test_another_servers_secret_reaches_nothing(client, sudo, server, route):
    """A second Milō on the LAN, or a laptop that made itself a key: a 200 here
    is root on a speaker for a server it never belonged to."""
    response = _send(client, route, ANOTHER_SERVER_SECRET)

    assert response.status_code == 403, response.text
    sudo.assert_not_awaited()


@pytest.mark.parametrize("route", TO_ROOT, ids=IDS)
def test_a_unit_that_holds_no_secret_refuses_a_bare_request(client, sudo, secret_file, route):
    """No window in which a bare request reaches root: before any server has
    reached this unit, a request that carries nothing is refused too, and
    pins nothing."""
    response = _send(client, route)

    assert response.status_code == 403, response.text
    sudo.assert_not_awaited()
    assert not secret_file.exists()


@pytest.mark.parametrize("route", [r for r in TO_ROOT if r[1] != "/app/update"],
                         ids=[i for i in IDS if "app/update" not in i])
def test_the_servers_secret_lets_its_request_through(client, sudo, server, own_password, account, route):
    """Sanity floor for the refusals above: the server itself is not locked out."""
    account(True)

    response = _send(client, route, SERVER_SECRET)

    assert response.status_code == 200, response.text


def test_the_servers_update_push_reaches_the_deploy_step(client, sudo, server, update_world):
    """The update endpoint carries every later fix: a push its own server
    cannot make could only be undone by a reflash."""
    _send(client, ("POST", "/app/update"), SERVER_SECRET)

    assert any(call.args[:1] == ("sudo",) for call in sudo.call_args_list)


def test_the_first_secret_is_pinned_and_no_other_after_it(client, sudo, secret_file):
    """Trust on first use: the speaker belongs to the first server that reaches
    it with a secret, here through the reboot that configures a fresh unit —
    which no admission reaches first, since it has no snapclient yet."""
    first = _send(client, ("POST", "/api/hardware/reboot"), SERVER_SECRET)

    assert first.status_code == 200, first.text
    assert secret_file.read_text().strip() == SERVER_SECRET
    assert stat.S_IMODE(secret_file.stat().st_mode) == 0o600, "the pin is readable by no other user"
    assert _send(client, ("POST", "/api/hardware/reboot"), ANOTHER_SERVER_SECRET).status_code == 403


def test_the_hand_over_says_when_it_pinned(client, secret_file):
    """The server logs a satellite's pin once, from `changed`: always true, it
    would announce a new pin at every admission; always false, never."""
    hand_over = ("PUT", "/api/system/server-secret")

    first = _send(client, hand_over, SERVER_SECRET).json()["data"]
    again = _send(client, hand_over, SERVER_SECRET).json()["data"]

    assert (first, again) == ({"changed": True}, {"changed": False})


def test_what_is_not_a_secret_is_never_pinned(client, sudo, secret_file):
    """The pin is trusted by every request after it: a value that is not what
    a server derives is refused, not written."""
    response = _send(client, ("PUT", "/api/system/server-secret"), "../../etc/sudoers")

    assert response.status_code == 403
    assert not secret_file.exists()


@pytest.mark.asyncio
async def test_two_first_contacts_pin_one_secret():
    """A hand-over and a reboot can reach a fresh unit at once: one pins, the
    other is compared against it, never written over it."""
    results = await asyncio.gather(
        admit(SERVER_SECRET), admit(ANOTHER_SERVER_SECRET), return_exceptions=True,
    )

    pinned = server_secret.SECRET_FILE.read_text().strip()
    winner = SERVER_SECRET if results[0] is True else ANOTHER_SERVER_SECRET
    assert pinned == winner
    assert sorted(type(r).__name__ for r in results) == ["ServerSecretRefused", "bool"]
    assert isinstance(results[1 if winner == SERVER_SECRET else 0], ServerSecretRefused)


def test_ssh_is_not_opened_on_the_factory_password(client, sudo, server):
    """The factory password is the same on every unit and published: a door
    opened on it is open to whoever reads the repository, server or not."""
    response = _send(client, ("PUT", "/api/system/ssh"), SERVER_SECRET)

    assert response.status_code == 409, response.text
    sudo.assert_not_awaited()


def test_closing_ssh_is_never_held_by_the_factory_password(client, sudo, server):
    """Refused, a door opened before the rule could never be shut from the panel."""
    response = client.put("/api/system/ssh", json={"enabled": False}, headers=server)

    assert response.status_code == 200, response.text
    assert sudo.call_args_list[0].args[1:4] == ("systemctl", "disable", "--now")


def test_reading_stays_open_to_the_lan(client, sudo, secret_file):
    """Reading and settings stay open to the LAN (owner decision 2026-11-13):
    guarded, the server's own panel could not read a speaker no admission has
    reached yet."""
    assert client.get("/api/system/ssh").status_code == 200


def test_the_secret_is_never_written_to_the_log(client, sudo, server, caplog):
    """The journal is readable over SSH and lands in diagnostics: a secret
    logged there is one any reader can replay against the speaker."""
    with caplog.at_level(logging.DEBUG):
        _send(client, ("POST", "/api/hardware/reboot"), ANOTHER_SERVER_SECRET)
        _send(client, ("PUT", "/api/system/server-secret"), SERVER_SECRET)

    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "Refused POST /api/hardware/reboot" in logged
    assert SERVER_SECRET not in logged and ANOTHER_SERVER_SECRET not in logged


def test_a_pin_that_cannot_be_read_is_refused_as_such(client, sudo, secret_file, caplog):
    """Read as "none pinned", the next secret would replace the pin; read as
    another server's, the server is told the wrong story about its speaker."""
    secret_file.mkdir()

    with caplog.at_level(logging.ERROR):
        response = _send(client, ("POST", "/api/hardware/reboot"), SERVER_SECRET)

    assert response.status_code == 403
    assert "cannot read the secret it pinned" in response.json()["detail"]
    assert any("Cannot read the pinned server secret" in r.getMessage() for r in caplog.records)
    sudo.assert_not_awaited()


def test_a_pin_that_cannot_be_written_is_a_refusal_not_a_crash(client, sudo, secret_file, monkeypatch):
    """A full or read-only /var/lib/milo-client: a bare 500 says nothing to the
    server, and the error never reaches the unit's journal as one."""
    monkeypatch.setattr(server_secret, "SECRET_FILE", secret_file.parent / "absent" / "server-secret")

    response = _send(client, ("PUT", "/api/system/server-secret"), SERVER_SECRET)

    assert response.status_code == 403
    assert "cannot write its pin" in response.json()["detail"]
