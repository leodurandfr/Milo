"""POST /api/hardware/reboot: run the apply script, and nothing else.

The role lock (`setup_completed` in `/var/lib/milo/settings.json`) belongs to
milo-first-boot, which writes it when it converts a card into a satellite. The
route used to write it again after the script, into a directory a satellite
may not have: Canapé has no `/var/lib/milo`, so every reboot from the server's
hardware panel logged an ERROR there (measured 2026-11-01 and 2026-11-03).

Consumer: the server's satellite hardware panel, which calls this route after
`PUT /api/hardware/audio`.
"""
import logging
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes import create_hardware_router
from routes.hardware import APPLY_HARDWARE_SCRIPT

_writes: list[str] | None = None


def _refuse_writes(event, args):
    """Record every file opened for writing while armed, and stop the open, so a
    write the route attempts never reaches a real path — on a dev host
    `/var/lib/milo/settings.json` is the server's own."""
    if _writes is None or event != "open":
        return
    path, mode, flags = args
    if (mode and any(c in mode for c in "wax+")) or (flags and flags & 0o3):
        _writes.append(str(path))
        raise PermissionError(f"write refused by the test: {path}")


sys.addaudithook(_refuse_writes)


@pytest.fixture
def armed():
    global _writes
    _writes = []
    try:
        yield _writes
    finally:
        _writes = None


@pytest.fixture
def apply_script_reboots():
    """The apply script still running after the route's 2 s wait: the reboot is underway."""
    process = MagicMock(returncode=None)
    process.wait = AsyncMock(side_effect=TimeoutError)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)) as exec_:
        yield exec_


def test_a_reboot_writes_no_file_and_logs_no_error(armed, apply_script_reboots, caplog):
    app = FastAPI()
    app.include_router(create_hardware_router())

    with caplog.at_level(logging.INFO):
        response = TestClient(app).post("/api/hardware/reboot")

    assert response.status_code == 200
    assert apply_script_reboots.call_args.args[:2] == ("sudo", APPLY_HARDWARE_SCRIPT)
    assert armed == []
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR] == []
