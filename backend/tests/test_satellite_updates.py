# backend/tests/test_satellite_updates.py
"""
Tests for the server half of the satellite update path: what gets shipped, what
counts as a finished update, and when the UI is allowed to offer one.

Every assertion here is about a machine CI can never reach — a second physical
Pi — so the mocks stand for the satellite's HTTP surface and for git, and the
assertion is always what the service concluded from what the satellite said.
"""
import tarfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.programs import create_programs_router
from backend.core.updates.satellite import SatelliteUpdateService

SERVER_VERSION = "v0.1.0-1673-gdeadbee"


def _mock_proc(stdout: bytes, returncode: int = 0):
    proc = Mock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    return proc


class _FakeResponse:
    """One aiohttp response: an async context manager with a status and a body."""

    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._payload


class _FakeSatellite:
    """A satellite answering the endpoints the update path polls.

    `/status` reports the versions it currently runs; `/<component>/update/status`
    reports an update that has already finished, which is the exact situation the
    waiters must not mistake for a successful one.
    """

    def __init__(self, status_payload, post_payload=None):
        self.status_payload = status_payload
        self.post_payload = post_payload
        self.posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        if url.endswith("/update/status"):
            return _FakeResponse(200, {"update_in_progress": False})
        return _FakeResponse(200, self.status_payload)

    def post(self, url, **kwargs):
        self.posts.append(url)
        return _FakeResponse(200, self.post_payload)


def _patch_satellite(satellite):
    """Every ClientSession the service opens answers as this one satellite."""
    return patch("backend.core.updates.satellite.aiohttp.ClientSession", return_value=satellite)


@pytest.fixture
def satellite_service():
    registry = Mock()
    registry.get_all_clients = Mock(return_value={
        "dc:a6:32:7e:d3:43": Mock(
            is_local=False, ip="192.168.1.153", online=True,
            host="milo-client", name="Canapé",
        )
    })
    return SatelliteUpdateService(snapcast_service=Mock(), client_registry_service=registry)


class TestClientTarball:
    """What a satellite update actually ships.

    The satellite builds its own venv at install time and symlinks it into the
    repo dir; a venv inside the tarball is bytes it extracts and throws away.
    """

    @pytest.fixture
    def client_tree(self, tmp_path):
        tree = tmp_path / "milo-client"
        (tree / "app" / "routes").mkdir(parents=True)
        (tree / "app" / "main.py").write_text("app\n")
        (tree / "app" / "routes" / "health.py").write_text("health\n")
        (tree / "app" / "__pycache__").mkdir()
        (tree / "app" / "__pycache__" / "main.cpython-313.pyc").write_bytes(b"\x00")
        (tree / "app" / "tests").mkdir()
        (tree / "app" / "tests" / "test_routes.py").write_text("tests\n")
        (tree / "system").mkdir()
        (tree / "system" / "milo-client.service").write_text("unit\n")
        (tree / "venv" / "lib" / "python3.13" / "site-packages").mkdir(parents=True)
        (tree / "venv" / "bin").mkdir()
        (tree / "venv" / "bin" / "pip3").write_text("pip\n")
        (tree / "venv" / "lib" / "python3.13" / "site-packages" / "aiohttp.py").write_text("dep\n")
        return tree

    async def _members(self, service, client_tree):
        with patch("backend.core.updates.satellite.MILO_CLIENT_DIR", client_tree), \
             patch("backend.core.updates.satellite.asyncio.create_subprocess_exec",
                   AsyncMock(return_value=_mock_proc(SERVER_VERSION.encode() + b"\n"))):
            tarball_path, version = await service._create_client_tarball()

        assert version == SERVER_VERSION
        try:
            with tarfile.open(tarball_path, "r:gz") as tar:
                return [m.name for m in tar.getmembers()]
        finally:
            Path(tarball_path).unlink()

    async def test_the_app_the_satellite_runs_is_shipped(self, satellite_service, client_tree):
        """Sanity floor: if the tarball were empty every exclusion below would pass."""
        members = await self._members(satellite_service, client_tree)

        assert "milo-client/app/main.py" in members
        assert "milo-client/app/routes/health.py" in members
        assert "milo-client/system/milo-client.service" in members

    async def test_the_venv_is_not_shipped(self, satellite_service, client_tree):
        """A shipped venv is ~67 MB of an ~67 MB transfer, extracted then discarded."""
        members = await self._members(satellite_service, client_tree)

        assert not [m for m in members if "venv" in Path(m).parts]

    async def test_pycache_and_tests_are_not_shipped(self, satellite_service, client_tree):
        """The other exclusions must survive the venv addition."""
        members = await self._members(satellite_service, client_tree)

        assert not [m for m in members if "__pycache__" in Path(m).parts]
        assert not [m for m in members if "tests" in Path(m).parts]


class TestSnapclientUpdateOutcome:
    """`update_in_progress` going false says the attempt ended, not that it worked.

    Reporting success there tells the UI a satellite was updated when it still
    runs the old binary — the fleet then looks current and nobody retries.
    """

    async def test_a_version_that_did_not_move_is_a_failed_update(self, satellite_service):
        satellite = _FakeSatellite(
            status_payload={"snapclient": {"version": "0.27.0", "running": True}},
            post_payload={"success": True, "target_version": "0.28.0"},
        )

        with _patch_satellite(satellite), patch("asyncio.sleep", new_callable=AsyncMock):
            result = await satellite_service.update_satellite("dc:a6:32:7e:d3:43")

        assert satellite.posts == ["http://192.168.1.153:8001/update"]
        assert result["success"] is False
        assert "0.27.0" in result["error"] and "0.28.0" in result["error"]

    async def test_the_version_the_satellite_targeted_is_the_one_required(self, satellite_service):
        """The target comes from the satellite's own POST answer — the server never
        knows which snapclient release GitHub offered it."""
        satellite = _FakeSatellite(
            status_payload={"snapclient": {"version": "0.28.0", "running": True}},
            post_payload={"success": True, "target_version": "0.28.0"},
        )

        with _patch_satellite(satellite), patch("asyncio.sleep", new_callable=AsyncMock):
            result = await satellite_service.update_satellite("dc:a6:32:7e:d3:43")

        assert result["success"] is True
        assert result["new_version"] == "0.28.0"


class TestCamillaDspUpdateOutcome:
    """Same gate on the DSP binary: a satellite left on the old CamillaDSP is a
    speaker whose EQ pipeline silently differs from every other."""

    async def test_a_version_that_did_not_move_is_a_failed_update(self, satellite_service):
        satellite = _FakeSatellite(
            status_payload={"camilladsp": {"version": "3.0.0"}, "snapclient": {"version": "0.28.0"}},
            post_payload={"success": True, "target_version": "3.1.0"},
        )

        with _patch_satellite(satellite), patch("asyncio.sleep", new_callable=AsyncMock):
            result = await satellite_service.update_satellite_camilladsp("dc:a6:32:7e:d3:43")

        assert result["success"] is False
        assert "3.0.0" in result["error"] and "3.1.0" in result["error"]

    async def test_a_version_that_moved_is_a_successful_update(self, satellite_service):
        satellite = _FakeSatellite(
            status_payload={"camilladsp": {"version": "3.1.0"}, "snapclient": {"version": "0.28.0"}},
            post_payload={"success": True, "target_version": "3.1.0"},
        )

        with _patch_satellite(satellite), patch("asyncio.sleep", new_callable=AsyncMock):
            result = await satellite_service.update_satellite_camilladsp("dc:a6:32:7e:d3:43")

        assert result["success"] is True
        assert result["new_version"] == "3.1.0"


class TestAppUpdateAvailableFlag:
    """`app_update_available` is the only thing that puts the update button in
    UpdateManager.vue. Both sides report the same repo's `git describe`, so the
    server and the satellite are the same version only when the strings match —
    comparing base tags made the flag permanently false and froze the fleet at
    whatever commit it was installed with.
    """

    @pytest.fixture
    def satellites(self):
        return [{
            "mac_id": "dc:a6:32:7e:d3:43",
            "hostname": "milo-client",
            "display_name": "Canapé",
            "ip": "192.168.1.153",
            "snapclient_version": "0.28.0",
            "app_version": SERVER_VERSION,
            "camilladsp_version": "3.0.0",
            "online": True,
            "uptime": 1000,
            "snapclient_running": True,
        }]

    @pytest.fixture
    def client(self, satellites):
        def _build(server_version):
            update_service = Mock()
            update_service.get_latest_github_version = AsyncMock(
                return_value={"status": "success", "version": "0.28.0"}
            )
            installed = {"status": "installed", "versions": {}}
            if server_version is not None:
                installed["raw_version"] = server_version
            update_service.get_installed_version = AsyncMock(return_value=installed)

            satellite_service = Mock()
            satellite_service.discover_satellites = AsyncMock(return_value=satellites)

            app = FastAPI()
            app.include_router(create_programs_router(update_service, satellite_service, Mock()))
            return TestClient(app)

        return _build

    def _flag(self, client, server_version):
        response = client(server_version).get("/api/programs/satellites")
        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 1, "the fixture satellite must survive the route"
        return body["satellites"][0]["app_update_available"]

    def test_the_same_describe_on_both_sides_offers_nothing(self, client):
        assert self._flag(client, SERVER_VERSION) is False

    def test_a_satellite_behind_the_server_is_offered_the_update(self, client, satellites):
        """Same base tag, ~100 commits apart — the case the whole fleet was in."""
        satellites[0]["app_version"] = "v0.1.0-1573-g0ae3872d"

        assert self._flag(client, SERVER_VERSION) is True

    def test_a_satellite_that_never_updated_is_offered_the_update(self, client, satellites):
        """No version file yet: it runs whatever it was installed with."""
        satellites[0]["app_version"] = None

        assert self._flag(client, SERVER_VERSION) is True

    def test_a_server_that_cannot_describe_itself_offers_nothing(self, client, satellites):
        """`git describe` failing must not light up every satellite at once."""
        satellites[0]["app_version"] = "v0.1.0-1573-g0ae3872d"

        assert self._flag(client, None) is False
