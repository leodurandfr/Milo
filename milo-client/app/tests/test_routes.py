"""
Unit tests for API routes.
"""
import pytest
from unittest.mock import Mock, AsyncMock
from fastapi.testclient import TestClient
from fastapi import FastAPI

# Import route factories
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from routes import create_health_router, create_snapclient_router, create_equalizer_router
from services.equalizer import EqualizerService
from services.snapclient import SnapclientService
from services.app_update import AppUpdateService
from services.camilladsp_update import CamillaDSPUpdateService


@pytest.fixture
def mock_equalizer_service():
    """Mock EqualizerService for route tests."""
    service = Mock(spec=EqualizerService)
    service.connected = True
    service.compressor = {"enabled": False, "threshold": -20.0, "ratio": 4.0}
    service.loudness = {"enabled": False, "high_boost": 5.0, "low_boost": 8.0}
    service.crossover = {"enabled": False, "frequency": 80.0, "q": 0.707}
    service.lowpass = {"enabled": False, "frequency": 80.0, "q": 0.707}
    service.volume_state = {"main": -20.0, "mute": False}

    # Async methods
    service.get_status = AsyncMock(return_value={"available": True, "state": "running"})
    service.get_filters = AsyncMock(return_value=[])
    service.get_levels = AsyncMock(return_value={"available": True, "output_peak": [-30, -30]})
    service.set_filter = AsyncMock(return_value=True)
    service.set_volume = AsyncMock(return_value=True)
    service.set_mute = AsyncMock(return_value=True)
    service.set_compressor = AsyncMock(return_value=True)
    service.set_loudness = AsyncMock(return_value=True)
    service.set_crossover = AsyncMock(return_value=True)
    service.set_lowpass = AsyncMock(return_value=True)

    return service


@pytest.fixture
def mock_snapclient_service():
    """Mock SnapclientService for route tests."""
    service = Mock(spec=SnapclientService)
    service.update_in_progress = False

    # Async methods
    service.get_installed_version = AsyncMock(return_value="0.28.0")
    service.is_service_running = AsyncMock(return_value=True)
    service.update_snapclient = AsyncMock(return_value={"success": True})

    return service


@pytest.fixture
def mock_app_update_service():
    """Mock AppUpdateService for route tests."""
    service = Mock(spec=AppUpdateService)
    service.update_in_progress = False
    service.get_app_version = Mock(return_value="v0.2.0")
    service.get_app_payload = Mock(return_value="bbbb222")
    return service


@pytest.fixture
def mock_camilladsp_update_service():
    """Mock CamillaDSPUpdateService for route tests."""
    service = Mock(spec=CamillaDSPUpdateService)
    service.update_in_progress = False
    service.get_installed_version = AsyncMock(return_value="2.0.0")
    service.update_camilladsp = AsyncMock(return_value={"success": True})
    return service


@pytest.fixture
def app(mock_equalizer_service, mock_snapclient_service, mock_app_update_service,
        mock_camilladsp_update_service):
    """FastAPI test app with mocked services."""
    app = FastAPI()
    app.include_router(create_health_router(
        mock_equalizer_service, mock_snapclient_service,
        mock_app_update_service, mock_camilladsp_update_service))
    app.include_router(create_snapclient_router(mock_snapclient_service))
    app.include_router(create_equalizer_router(mock_equalizer_service))
    return app


@pytest.fixture
def client(app):
    """Test client for route tests."""
    return TestClient(app)


class TestHealthRoutes:
    """Test health check routes."""

    def test_health_endpoint(self, client):
        """GET /health should return healthy status."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "timestamp" in data
        assert "hostname" in data
        assert "equalizer_ready" in data

    def test_status_endpoint(self, client):
        """GET /status should return complete status."""
        response = client.get("/status")
        assert response.status_code == 200
        data = response.json()
        assert "hostname" in data
        assert "uptime" in data
        assert "snapclient" in data
        assert "update_in_progress" in data

    def test_status_carries_the_process_start(self, client):
        """The server compares app.started_at across an app push to tell a
        satellite that restarted into the new code from one that only wrote the
        version file. Drop the field and every app update reports "deployed but
        never restarted", because both sides of that comparison become None."""
        data = client.get("/status").json()

        started_at = data["app"]["started_at"]
        assert isinstance(started_at, int)
        assert started_at > 1_700_000_000, "must be an epoch, not an uptime or a counter"


class TestSnapclientRoutes:
    """Test snapclient management routes."""

    def test_update_status_endpoint(self, client):
        """GET /update/status should return update status."""
        response = client.get("/update/status")
        assert response.status_code == 200
        data = response.json()
        assert "update_in_progress" in data


class TestSnapclientUpdateTarget:
    """POST /update installs the version the server names, and only that one.

    The satellite used to ask GitHub for `releases/latest` itself. It carries no
    manifest and no token, so that answer had nothing to do with what the server
    validated — a client could land on a release the row that started it never
    named, and no error anywhere said so.
    """

    def test_the_named_version_is_the_one_installed(self, client, mock_snapclient_service):
        response = client.post("/update", json={"target_version": "0.30.0"})

        assert response.status_code == 200
        body = response.json()
        assert body["started"] is True
        assert body["target_version"] == "0.30.0"
        mock_snapclient_service.update_snapclient.assert_called_once_with("0.30.0")

    def test_a_version_below_the_installed_one_still_starts(self, client, mock_snapclient_service):
        """Ending a trial of an unvalidated release is a downgrade.

        Refusing it — or comparing "is the target newer" — is what would strand a
        satellite above the version the server runs, with nothing able to bring
        it back.
        """
        response = client.post("/update", json={"target_version": "0.27.0"})

        assert response.json()["started"] is True
        mock_snapclient_service.update_snapclient.assert_called_once_with("0.27.0")

    def test_the_version_already_installed_starts_nothing(self, client, mock_snapclient_service):
        """Restarting snapclient to install what is already there cuts the sound
        in an occupied room for no gain."""
        response = client.post("/update", json={"target_version": "0.28.0"})

        assert response.json()["started"] is False
        mock_snapclient_service.update_snapclient.assert_not_called()

    def test_a_request_naming_no_version_is_refused(self, client, mock_snapclient_service):
        """There is no version to fall back to: the resolver that used to supply
        one is gone, and installing "the latest" is the fault this closed."""
        assert client.post("/update", json={}).status_code == 422
        mock_snapclient_service.update_snapclient.assert_not_called()


class TestSnapclientConfigBounds:
    """PUT /snapclient/config carries the ALSA buffer pair the server resolved.

    The handler used to clamp both values silently, with a range of its own that
    had drifted from the server's — so a disagreement between the two halves
    played out as a satellite running a different ALSA buffer than the rest of
    the house, reported nowhere. The bound now lives on the model, and a value
    outside it is a 422 the server logs.
    """

    @pytest.mark.parametrize("payload", [
        {"buffer_time": 59, "fragments": 4},
        {"buffer_time": 301, "fragments": 4},
        {"buffer_time": 120, "fragments": 1},
        {"buffer_time": 120, "fragments": 9},
    ])
    def test_a_value_outside_the_shared_range_is_refused(self, client, payload):
        assert client.put("/snapclient/config", json=payload).status_code == 422

    def test_both_fields_are_required(self, client):
        """The server sends the pair on every push; a partial body is a bug there."""
        assert client.put("/snapclient/config", json={"buffer_time": 120}).status_code == 422

    def test_an_in_range_pair_reaches_the_env_file(self, client, tmp_path, monkeypatch):
        env = tmp_path / "env"
        env.write_text("MILO_PRINCIPAL_IP=192.168.1.10\nMILO_SNAPCLIENT_BUFFER_TIME=80\n")
        monkeypatch.setattr("routes.snapclient.ENV_FILE", env)

        async def _ok(*args, **kwargs):
            proc = Mock()
            proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.returncode = 0
            return proc

        monkeypatch.setattr("asyncio.create_subprocess_exec", _ok)

        response = client.put("/snapclient/config", json={"buffer_time": 300, "fragments": 8})

        assert response.status_code == 200
        assert response.json()["changed"] is True
        written = env.read_text()
        assert "MILO_SNAPCLIENT_BUFFER_TIME=300" in written
        assert "MILO_SNAPCLIENT_FRAGMENTS=8" in written
        assert "MILO_PRINCIPAL_IP=192.168.1.10" in written


class TestSnapclientConfigFailedRestart:
    """A push whose restart fails must neither stick nor leave the room silent.

    The handler writes the env file, then stops and starts snapclient. A start
    that fails used to leave the new values on disk with the unit stopped, so the
    server's next push of the same pair answered `changed: False` and restarted
    nothing — the room stayed silent until the satellite was rebooted.
    """

    STOP = ("sudo", "systemctl", "stop", "milo-client-snapclient.service")
    START = ("sudo", "systemctl", "start", "milo-client-snapclient.service")
    OLD_ENV = "MILO_PRINCIPAL_IP=192.168.1.10\nMILO_SNAPCLIENT_BUFFER_TIME=80\nMILO_SNAPCLIENT_FRAGMENTS=4\n"

    @pytest.fixture(autouse=True)
    def _forget_owed_start(self):
        import services.server_address_watch as watch
        watch._start_owed = False
        yield
        watch._start_owed = False

    @pytest.fixture
    def systemd(self, tmp_path, monkeypatch):
        """systemd behind sudo: records every argv, fails the next `start` on demand."""
        env = tmp_path / "env"
        env.write_text(self.OLD_ENV)
        monkeypatch.setattr("routes.snapclient.ENV_FILE", env)
        world = {"calls": [], "fail_starts": 0, "env": env}

        async def _spawn(*argv, **kwargs):
            world["calls"].append(argv)
            proc = Mock()
            proc.returncode = 0
            if argv == self.START and world["fail_starts"]:
                world["fail_starts"] -= 1
                proc.returncode = 1
            if argv[0] == "systemctl":  # the watcher's unprivileged MainPID read
                proc.communicate = AsyncMock(return_value=(b"0\n", b""))
            else:
                proc.communicate = AsyncMock(return_value=(b"", b"Job failed"))
            return proc

        monkeypatch.setattr("asyncio.create_subprocess_exec", _spawn)
        return world

    def test_snapclient_config_failed_restart_is_retried_on_same_values(self, client, systemd):
        systemd["fail_starts"] = 1
        payload = {"buffer_time": 300, "fragments": 8}

        assert client.put("/snapclient/config", json=payload).status_code == 500
        assert systemd["env"].read_text() == self.OLD_ENV, "a failed restart keeps the old values"

        systemd["calls"].clear()
        response = client.put("/snapclient/config", json=payload)

        assert response.status_code == 200
        assert response.json()["changed"] is True
        assert systemd["calls"] == [self.STOP, self.START]

    @pytest.mark.asyncio
    async def test_snapclient_config_failed_start_leaves_a_start_owed(self, client, systemd):
        """The unit the route stopped is dead, so it has no address for the watcher
        to compare: only a start the route left owed brings it back."""
        from services.server_address_watch import _reconcile

        systemd["fail_starts"] = 1
        assert client.put("/snapclient/config", json={"buffer_time": 300, "fragments": 8}).status_code == 500

        systemd["calls"].clear()
        assert await _reconcile() is True
        assert [argv for argv in systemd["calls"] if argv[0] == "sudo"] == [self.STOP, self.START]


class TestEqualizerRoutes:
    """What the equalizer routes decide, as opposed to what they pass through.

    The GET handlers here hand back whatever the service returned; a test feeding
    the service a dict and finding its keys in the response asserts the fixture,
    not the route. Eight such tests were removed 2026-08-18 — none of them could
    fail — and replaced by the smoke below plus assertions on the two things the
    routes genuinely do: unwrap a request model into the service's keyword
    arguments, and forward an absent field as None.

    Six of those eight routes then went too (2026-08-19), each a strict subset of
    `/status`, which stays; `/volume` followed (2026-10-09), its one server caller
    having none. The server reads only `/status` and `/levels` from a satellite, so
    the list below is the whole satellite read surface — a route added belongs in
    it, a route lost turns it red.
    """

    @pytest.mark.parametrize("path", [
        "/equalizer/status",
        "/equalizer/levels",
    ])
    def test_every_read_route_answers(self, client, path):
        """A floor, and only a floor: it catches a handler that raises or a router
        that no longer wires the service in. It says nothing about the payload,
        deliberately — the payloads come straight from the service, which has its
        own tests, and re-asserting them here would only pin the fixture."""
        assert client.get(path).status_code == 200

    def test_equalizer_volume_put(self, client, mock_equalizer_service):
        """PUT /equalizer/volume should update volume."""
        response = client.put("/equalizer/volume", json={"volume": -15.0})
        assert response.status_code == 200
        mock_equalizer_service.set_volume.assert_called_once_with(-15.0)

    def test_equalizer_mute_put(self, client, mock_equalizer_service):
        """PUT /equalizer/mute should update mute state."""
        response = client.put("/equalizer/mute", json={"muted": True})
        assert response.status_code == 200
        mock_equalizer_service.set_mute.assert_called_once_with(True)

    def test_the_headroom_the_server_computed_reaches_the_batch(self, client, mock_equalizer_service):
        """PUT /equalizer/filters carries the whole curve's headroom beside its bands."""
        mock_equalizer_service.set_filters_batch = AsyncMock(return_value={"success": True, "applied": 1})

        response = client.put("/equalizer/filters", json={
            "filters": [{"id": "eq_band_09", "gain": 6.0}], "headroom_db": -6.0,
        })

        assert response.status_code == 200
        mock_equalizer_service.set_filters_batch.assert_awaited_once_with(
            [{"id": "eq_band_09", "gain": 6.0}], -6.0
        )

    def test_every_compressor_field_reaches_the_service_under_its_own_name(
        self, client, mock_equalizer_service
    ):
        """Six optional fields, forwarded one by one as keyword arguments — the
        one place a crossed wire (threshold taking ratio's value) is visible at
        all. The backend contract test proves the satellite *reads* each key the
        server sends; it cannot see which argument the key ends up in, and the
        route reports success either way."""
        response = client.put("/equalizer/compressor", json={
            "enabled": True, "threshold": -15.0, "ratio": 3.0,
            "attack": 5.0, "release": 120.0, "makeup_gain": 2.0,
        })

        assert response.status_code == 200
        mock_equalizer_service.set_compressor.assert_called_once_with(
            enabled=True, threshold=-15.0, ratio=3.0,
            attack=5.0, release=120.0, makeup_gain=2.0,
        )

    def test_a_compressor_field_the_body_omits_arrives_as_none(self, client, mock_equalizer_service):
        """None is what the service reads as "leave this one alone". A default
        substituted here would reset the other five on every partial push, and the
        UI sends exactly one field when a single control moves."""
        client.put("/equalizer/compressor", json={"enabled": True})

        mock_equalizer_service.set_compressor.assert_called_once_with(
            enabled=True, threshold=None, ratio=None,
            attack=None, release=None, makeup_gain=None,
        )

    @pytest.mark.parametrize("path, setter", [
        ("/equalizer/crossover", "set_crossover"),
        ("/equalizer/lowpass", "set_lowpass"),
    ])
    def test_every_crossover_field_reaches_the_service_under_its_own_name(
        self, client, mock_equalizer_service, path, setter
    ):
        """The server's CrossoverService pushes a zone's crossover
        here. A frequency handed over as the Q (or the reverse) still answers 200
        and plays a filter nobody asked for, on a sub/sat zone's satellite only."""
        response = client.put(path, json={"enabled": True, "frequency": 120.0, "q": 0.5})

        assert response.status_code == 200
        getattr(mock_equalizer_service, setter).assert_called_once_with(
            enabled=True, frequency=120.0, q=0.5
        )

    def test_every_loudness_field_reaches_the_service_under_its_own_name(
        self, client, mock_equalizer_service
    ):
        """The server's EqualizerRouter pushes a client's loudness here. The two
        boosts are both dB and both optional, so crossing them is invisible to the
        contract test and to the response — only the curve moves."""
        response = client.put("/equalizer/loudness", json={
            "enabled": True, "high_boost": 4.0, "low_boost": 9.0, "reference_level": -25.0,
        })

        assert response.status_code == 200
        mock_equalizer_service.set_loudness.assert_called_once_with(
            enabled=True, high_boost=4.0, low_boost=9.0, reference_level=-25.0
        )
