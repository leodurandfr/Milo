"""`GET /status` — what a satellite says it runs.

The server reads `app.payload` off this route to decide whether a satellite
needs a push (the inventory, the fleet push after a Milō update, the waiter of
a single push). An update writes the identity files before it schedules its own
restart, so a route that read them per request would let a process whose
restart was refused announce the code it never ran: the server then calls the
unit current and never pushes it again.
"""
import asyncio
import importlib
import os
import sys
import tarfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

import routes.health as health  # noqa: E402
import services.app_update as app_update_module  # noqa: E402
from services.app_update import AppUpdateService  # noqa: E402
from services.camilladsp_update import CamillaDSPUpdateService  # noqa: E402
from services.equalizer import EqualizerService  # noqa: E402
from services.snapclient import SnapclientService  # noqa: E402


def _status_client(app_update_service: AppUpdateService) -> TestClient:
    snapclient = Mock(spec=SnapclientService)
    snapclient.update_in_progress = False
    snapclient.get_installed_version = AsyncMock(return_value="0.35.0")
    snapclient.is_service_running = AsyncMock(return_value=True)
    camilladsp = Mock(spec=CamillaDSPUpdateService)
    camilladsp.update_in_progress = False
    camilladsp.get_installed_version = AsyncMock(return_value="4.1.3")

    app = FastAPI()
    app.include_router(health.create_health_router(
        Mock(spec=EqualizerService), snapclient, app_update_service, camilladsp
    ))
    return TestClient(app)


def _tarball(tmp_path) -> str:
    staged = tmp_path / "tarball-src" / "milo-client" / "app"
    staged.mkdir(parents=True)
    (staged / "main.py").write_text("new main\n")
    tarball = tmp_path / "update.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(str(staged.parent), arcname="milo-client")
    return str(tarball)


@pytest.fixture
def repo_dir(tmp_path):
    live = tmp_path / "repo"
    (live / "app").mkdir(parents=True)
    (live / "app" / "main.py").write_text("live main\n")
    cwd = Path.cwd()
    with patch.object(app_update_module, "REPO_DIR", live):
        yield live
    # _sync_tree re-anchors the cwd on the tree it swaps.
    os.chdir(cwd)


def _refusing_restart(spawned):
    """Stands in for sudo and pip — the outside world: pip installs, and
    systemd refuses the restart the way a masked unit does."""
    async def _spawn(*argv, **kwargs):
        spawned.append(argv)
        proc = AsyncMock()
        refused = "restart" in argv
        proc.returncode = 1 if refused else 0
        stderr = b"Failed to restart milo-client.service: Unit milo-client.service is masked." if refused else b""
        proc.communicate = AsyncMock(return_value=(b"", stderr))
        return proc
    return _spawn


@pytest.mark.asyncio
async def test_status_reports_the_identity_the_process_started_with(
    tmp_path, repo_dir, identity_at_startup
):
    """The update has run and its restart was refused: /status must still name
    the code that answers, so the server keeps offering this unit the push."""
    version, payload = identity_at_startup
    service = AppUpdateService()
    spawned, scheduled = [], []
    loop = asyncio.get_running_loop()

    # The restart is scheduled two seconds after the answer; it is caught here
    # and run at once, so no timer outlives the patched subprocess.
    with patch.object(app_update_module.asyncio, "create_subprocess_exec", _refusing_restart(spawned)), \
            patch.object(loop, "call_later", lambda delay, callback: scheduled.append(callback)):
        result = await service.deploy_update(_tarball(tmp_path), "NEWPAYLOAD", "v0.2.0")
        assert result["success"] is True
        assert len(scheduled) == 1
        await scheduled[0]()

    assert any("restart" in argv for argv in spawned), "the restart must have been attempted"
    assert payload.read_text() == "NEWPAYLOAD", "the deploy must have written the next identity"

    app = _status_client(service).get("/status").json()["app"]

    assert app["version"] == "v0.1.0"
    assert app["payload"] == "OLDPAYLOAD"


def test_the_next_process_reports_what_the_update_wrote(identity_at_startup):
    """The other half: once the restart lands, the new process reads the files
    the update left, or every push would time out on an unchanged payload."""
    version, payload = identity_at_startup
    version.write_text("v0.2.0")
    payload.write_text("NEWPAYLOAD")
    importlib.reload(health)

    app = _status_client(AppUpdateService()).get("/status").json()["app"]

    assert app["version"] == "v0.2.0"
    assert app["payload"] == "NEWPAYLOAD"
