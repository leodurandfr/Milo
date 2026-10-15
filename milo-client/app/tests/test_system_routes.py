"""
Tests for the satellite's remote access routes (routes/system.py).

The outside world here is the process the route spawns — `sudo systemctl` and
`sudo milo-set-password` — so that is what is replaced: the assertions are on
what the route handed it and on what it answered when it failed. The argv itself
is pinned against the sudoers grants by test_privileged_exec_contract.py.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import system
from routes.system import create_system_router


class _Process:
    """A finished child process: its exit code and what it wrote to stderr."""

    def __init__(self, returncode=0, stderr=b""):
        self.returncode = returncode
        self._stderr = stderr
        self.stdin_received = None
        self.killed = False

    async def communicate(self, data=None):
        self.stdin_received = data
        return b"", self._stderr

    async def wait(self):
        return self.returncode

    def kill(self):
        self.killed = True


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(create_system_router())
    return TestClient(app)


def _spawning(process):
    spawn = AsyncMock(return_value=process)
    return spawn, patch("routes.system.asyncio.create_subprocess_exec", spawn)


@pytest.fixture
def account(monkeypatch):
    """Whether the unit has the `milo` account — the passwd database is the outside world here."""
    def present(exists):
        def getpwnam(name):
            if exists and name == "milo":
                return object()
            raise KeyError(name)
        monkeypatch.setattr(system.pwd, "getpwnam", getpwnam)
    return present


class TestPassword:
    def test_a_unit_without_the_account_is_refused_before_any_helper(self, client, account):
        """Canapé was provisioned before the image and has no `milo`: say so, run nothing."""
        account(False)
        spawn, patched = _spawning(_Process())

        with patched:
            response = client.post("/api/system/password", json={"password": "correct-horse"})

        assert response.status_code == 409
        spawn.assert_not_awaited()

    def test_the_password_travels_on_stdin_and_never_in_argv(self, client, account):
        """/proc/<pid>/cmdline is world-readable: an argv would publish the password."""
        account(True)
        process = _Process()
        spawn, patched = _spawning(process)

        with patched:
            response = client.post("/api/system/password", json={"password": "correct-horse"})

        assert response.status_code == 200
        argv = spawn.await_args.args
        assert all("correct-horse" not in str(word) for word in argv)
        assert process.stdin_received == b"correct-horse"

    def test_a_failed_helper_is_not_a_success(self, client, account):
        account(True)
        spawn, patched = _spawning(_Process(returncode=1, stderr=b"chpasswd: PAM failure"))

        with patched:
            response = client.post("/api/system/password", json={"password": "correct-horse"})

        assert response.status_code == 500
        assert "chpasswd: PAM failure" in response.json()["detail"]


class TestSsh:
    def test_a_systemctl_that_failed_is_not_a_success(self, client):
        """The switch must not read as open when the unit never started."""
        spawn, patched = _spawning(_Process(returncode=1, stderr=b"Unit ssh.service not found."))

        with patched:
            response = client.put("/api/system/ssh", json={"enabled": True})

        assert response.status_code == 500
        assert "ssh.service not found" in response.json()["detail"]

    def test_closing_disables_rather_than_only_stopping(self, client):
        """A stop alone would reopen the door at the next boot."""
        spawn, patched = _spawning(_Process())

        with patched:
            client.put("/api/system/ssh", json={"enabled": False})

        assert spawn.await_args_list[0].args[1:4] == ("systemctl", "disable", "--now")

    def test_a_systemctl_that_hangs_is_killed_and_reported(self, client):
        """A D-Bus stall must not leave a child behind nor answer a bare 500."""
        process = _Process()

        async def never(data=None):
            await asyncio.sleep(3600)
        process.communicate = never
        spawn, patched = _spawning(process)

        with patched, patch.object(system, "SYSTEMCTL_READ_TIMEOUT_S", 0.01):
            response = client.get("/api/system/ssh")

        assert response.status_code == 500
        assert "did not answer" in response.json()["detail"]
        assert process.killed

    def test_the_factory_password_is_reported_until_the_helper_ran(self, client, tmp_path, monkeypatch):
        marker = tmp_path / "password-changed"
        monkeypatch.setattr(system, "PASSWORD_CHANGED_MARKER", str(marker))
        spawn, patched = _spawning(_Process())

        with patched:
            before = client.get("/api/system/ssh").json()["data"]["password_is_default"]
            marker.touch()
            after = client.get("/api/system/ssh").json()["data"]["password_is_default"]

        assert (before, after) == (True, False)
