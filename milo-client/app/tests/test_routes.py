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
    service.available = True
    service.compressor = {"enabled": False, "threshold": -20.0, "ratio": 4.0}
    service.loudness = {"enabled": False, "high_boost": 5.0, "low_boost": 8.0}
    service.delay = {"left": 0.0, "right": 0.0}
    service.crossover = {"enabled": False, "frequency": 80.0, "q": 0.707}
    service.lowpass = {"enabled": False, "frequency": 80.0, "q": 0.707}
    service.volume_state = {"main": -20.0, "mute": False}
    service.equalizer_enabled = True

    # Async methods
    service.get_status = AsyncMock(return_value={"available": True, "state": "running"})
    service.get_filters = AsyncMock(return_value=[])
    service.get_volume = AsyncMock(return_value={"main": -20.0, "mute": False})
    service.get_levels = AsyncMock(return_value={"available": True, "input_peak": [-30, -30]})
    service.set_filter = AsyncMock(return_value=True)
    service.set_volume = AsyncMock(return_value=True)
    service.set_mute = AsyncMock(return_value=True)
    service.set_compressor = AsyncMock(return_value=True)
    service.set_loudness = AsyncMock(return_value=True)
    service.set_delay = AsyncMock(return_value=True)
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
    service.get_latest_github_version = AsyncMock(return_value="0.29.0")
    service.is_service_running = AsyncMock(return_value=True)
    service.update_snapclient = AsyncMock(return_value={"success": True})

    return service


@pytest.fixture
def mock_app_update_service():
    """Mock AppUpdateService for route tests."""
    service = Mock(spec=AppUpdateService)
    service.update_in_progress = False
    service.get_app_version = Mock(return_value="1.0.0")
    return service


@pytest.fixture
def mock_camilladsp_update_service():
    """Mock CamillaDSPUpdateService for route tests."""
    service = Mock(spec=CamillaDSPUpdateService)
    service.update_in_progress = False
    service.get_installed_version = AsyncMock(return_value="2.0.0")
    service.get_latest_github_version = AsyncMock(return_value="2.0.1")
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


class TestSnapclientRoutes:
    """Test snapclient management routes."""

    def test_update_status_endpoint(self, client):
        """GET /update/status should return update status."""
        response = client.get("/update/status")
        assert response.status_code == 200
        data = response.json()
        assert "update_in_progress" in data


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

        monkeypatch.setattr("routes.snapclient.asyncio.create_subprocess_exec", _ok)

        response = client.put("/snapclient/config", json={"buffer_time": 300, "fragments": 8})

        assert response.status_code == 200
        assert response.json()["changed"] is True
        written = env.read_text()
        assert "MILO_SNAPCLIENT_BUFFER_TIME=300" in written
        assert "MILO_SNAPCLIENT_FRAGMENTS=8" in written
        assert "MILO_PRINCIPAL_IP=192.168.1.10" in written


class TestEqualizerRoutes:
    """Test Equalizer control routes."""

    def test_equalizer_enabled_get(self, client):
        """GET /equalizer/enabled should return enabled state."""
        response = client.get("/equalizer/enabled")
        assert response.status_code == 200
        assert response.json() == {"enabled": True}

    def test_equalizer_status_endpoint(self, client):
        """GET /equalizer/status should return Equalizer status."""
        response = client.get("/equalizer/status")
        assert response.status_code == 200
        data = response.json()
        assert "available" in data

    def test_equalizer_filters_endpoint(self, client):
        """GET /equalizer/filters should return filter list."""
        response = client.get("/equalizer/filters")
        assert response.status_code == 200
        data = response.json()
        assert "filters" in data

    def test_equalizer_volume_get(self, client):
        """GET /equalizer/volume should return volume state."""
        response = client.get("/equalizer/volume")
        assert response.status_code == 200
        data = response.json()
        assert "main" in data
        assert "mute" in data

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

    def test_equalizer_compressor_get(self, client):
        """GET /equalizer/compressor should return compressor state."""
        response = client.get("/equalizer/compressor")
        assert response.status_code == 200
        data = response.json()
        assert "enabled" in data
        assert "threshold" in data

    def test_equalizer_compressor_put(self, client, mock_equalizer_service):
        """PUT /equalizer/compressor should update compressor."""
        response = client.put("/equalizer/compressor", json={"enabled": True, "threshold": -15.0})
        assert response.status_code == 200
        mock_equalizer_service.set_compressor.assert_called_once()

    def test_equalizer_loudness_get(self, client):
        """GET /equalizer/loudness should return loudness state."""
        response = client.get("/equalizer/loudness")
        assert response.status_code == 200
        data = response.json()
        assert "enabled" in data

    def test_equalizer_delay_get(self, client):
        """GET /equalizer/delay should return delay state."""
        response = client.get("/equalizer/delay")
        assert response.status_code == 200
        data = response.json()
        assert "left" in data
        assert "right" in data

    def test_equalizer_levels_get(self, client):
        """GET /equalizer/levels should return audio levels."""
        response = client.get("/equalizer/levels")
        assert response.status_code == 200
        data = response.json()
        assert "available" in data
