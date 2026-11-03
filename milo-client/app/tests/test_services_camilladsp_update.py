"""
Tests for the CamillaDSP binary updater on a satellite.

What breaks when these fail: a failed CamillaDSP update leaves the room silent.
The update stops the unit to replace the binary, and `Restart=on-failure` does
not undo an explicit `systemctl stop` — so a rollback that returns early (its
reinstall refused by the same wrapper, or its copy of the backup failing on a
full /tmp) used to leave CamillaDSP stopped until the satellite was rebooted,
with nothing but "update failed" on the server's screen.

The mocked boundary is systemd, the install wrapper, `tar` and GitHub; the
backup is a real file under tmp_path.
"""
import errno
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import services.camilladsp_update as camilladsp_update
from services.camilladsp_update import CamillaDSPUpdateService


class _FakeProc:
    """A finished subprocess, as create_subprocess_exec hands it back."""

    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self._output = (stdout, stderr)

    async def communicate(self):
        return self._output


class FakeMachine:
    """systemd, the install wrapper and `tar`, recording every argv.

    The satellite's sudoers policy is argument-scoped, so the exact verb and unit
    name are the contract — which is why the tests below read `calls`.
    `install_returncodes` is consumed one per wrapper call; once empty, the
    wrapper succeeds.
    """

    STOP = ("sudo", "systemctl", "stop", "milo-client-camilladsp.service")
    START = ("sudo", "systemctl", "start", "milo-client-camilladsp.service")

    def __init__(self):
        self.calls = []
        self.running = True
        self.install_returncodes = []

    async def spawn(self, *argv, **kwargs):
        self.calls.append(argv)
        if argv[1:] == ("--version",):
            return _FakeProc(stdout=b"CamillaDSP 3.0.1\n")
        if argv[0] == "tar":
            (Path(argv[4]) / "camilladsp").write_bytes(b"new binary")
            return _FakeProc()
        if argv[:2] == ("systemctl", "is-active"):
            return _FakeProc(stdout=b"active\n" if self.running else b"inactive\n")
        if argv == self.STOP:
            self.running = False
            return _FakeProc()
        if argv == self.START:
            self.running = True
            return _FakeProc()
        if argv[:2] == ("sudo", camilladsp_update.INSTALL_WRAPPER):
            code = self.install_returncodes.pop(0) if self.install_returncodes else 0
            return _FakeProc(returncode=code, stderr=b"not an aarch64 ELF")
        raise AssertionError(f"unexpected command: {argv}")


class _FakeContent:
    async def iter_chunked(self, size):
        yield b"an archive tar is faked to extract"


class _FakeResponse:
    status = 200
    content = _FakeContent()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    """GitHub, serving the release asset."""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        return _FakeResponse()


def _last_unit_verb(calls):
    """The last stop/start the updater issued: what the unit is left in."""
    verbs = [argv for argv in calls if argv in (FakeMachine.STOP, FakeMachine.START)]
    return verbs[-1] if verbs else None


@pytest.fixture
def machine(tmp_path):
    """Patches only the boundary: subprocesses, GitHub, the settling sleeps, and
    where the installed binary and its backup live."""
    world = FakeMachine()
    installed = tmp_path / "camilladsp"
    installed.write_bytes(b"old binary")

    with patch.object(camilladsp_update.asyncio, "create_subprocess_exec", world.spawn), \
         patch.object(camilladsp_update.asyncio, "sleep", AsyncMock()), \
         patch.object(camilladsp_update.aiohttp, "ClientSession", _FakeSession), \
         patch.object(camilladsp_update, "CAMILLADSP_BINARY", str(installed)), \
         patch.object(camilladsp_update, "BACKUP_DIR", tmp_path / "backups"):
        yield world


@pytest.mark.asyncio
async def test_a_failed_install_starts_camilladsp_back(machine):
    """The floor: the wrapper refuses the new binary, the backup goes back in."""
    machine.install_returncodes = [1]

    result = await CamillaDSPUpdateService().update_camilladsp("3.0.2")

    assert result["success"] is False
    assert _last_unit_verb(machine.calls) == FakeMachine.START


@pytest.mark.asyncio
async def test_a_rollback_whose_reinstall_fails_still_starts_the_unit(machine):
    """A wrapper that refuses the new binary usually refuses the backup for the
    same reason (a sudoers drift, `file` missing) — the old binary is still on
    disk, so the unit must run on it rather than stay stopped."""
    machine.install_returncodes = [1, 1]

    result = await CamillaDSPUpdateService().update_camilladsp("3.0.2")

    assert result["success"] is False
    assert _last_unit_verb(machine.calls) == FakeMachine.START


@pytest.mark.asyncio
async def test_a_rollback_whose_copy_raises_still_starts_the_unit(machine):
    """A full /tmp fails the copy the wrapper needs, after the unit is stopped."""
    machine.install_returncodes = [1]
    real_copy = shutil.copy2
    refused = []

    def _copy(src, dst, *args, **kwargs):
        if Path(dst).name.startswith("milo-rollback-"):
            refused.append(Path(dst))
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_copy(src, dst, *args, **kwargs)

    with patch.object(camilladsp_update.shutil, "copy2", _copy):
        result = await CamillaDSPUpdateService().update_camilladsp("3.0.2")

    assert result["success"] is False
    assert _last_unit_verb(machine.calls) == FakeMachine.START
    assert refused and not refused[0].exists(), "the half-made copy is not left in /tmp"
