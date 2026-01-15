"""
Unit tests for API routes.
"""
import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient
from fastapi import FastAPI

# Import route factories
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from routes import create_health_router, create_snapclient_router, create_dsp_router
from services.dsp import DSPService
from services.snapclient import SnapclientService


@pytest.fixture
def mock_dsp_service():
    """Mock DSPService for route tests."""
    service = Mock(spec=DSPService)
    service.connected = True
    service.available = True
    service.compressor = {"enabled": False, "threshold": -20.0, "ratio": 4.0}
    service.loudness = {"enabled": False, "high_boost": 5.0, "low_boost": 8.0}
    service.delay = {"left": 0.0, "right": 0.0}
    service.crossover = {"enabled": False, "frequency": 80.0, "q": 0.707}
    service.lowpass = {"enabled": False, "frequency": 80.0, "q": 0.707}
    service.volume_state = {"main": -20.0, "mute": False}

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
def app(mock_dsp_service, mock_snapclient_service):
    """FastAPI test app with mocked services."""
    app = FastAPI()
    app.include_router(create_health_router(mock_dsp_service, mock_snapclient_service))
    app.include_router(create_snapclient_router(mock_snapclient_service))
    app.include_router(create_dsp_router(mock_dsp_service))
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
        assert "dsp_ready" in data

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

    def test_version_endpoint(self, client):
        """GET /version should return snapclient version."""
        response = client.get("/version")
        assert response.status_code == 200
        data = response.json()
        assert data["version"] == "0.28.0"

    def test_update_status_endpoint(self, client):
        """GET /update/status should return update status."""
        response = client.get("/update/status")
        assert response.status_code == 200
        data = response.json()
        assert "update_in_progress" in data


class TestDSPRoutes:
    """Test DSP control routes."""

    def test_dsp_status_endpoint(self, client):
        """GET /dsp/status should return DSP status."""
        response = client.get("/dsp/status")
        assert response.status_code == 200
        data = response.json()
        assert "available" in data

    def test_dsp_filters_endpoint(self, client):
        """GET /dsp/filters should return filter list."""
        response = client.get("/dsp/filters")
        assert response.status_code == 200
        data = response.json()
        assert "filters" in data

    def test_dsp_volume_get(self, client):
        """GET /dsp/volume should return volume state."""
        response = client.get("/dsp/volume")
        assert response.status_code == 200
        data = response.json()
        assert "main" in data
        assert "mute" in data

    def test_dsp_volume_put(self, client, mock_dsp_service):
        """PUT /dsp/volume should update volume."""
        response = client.put("/dsp/volume", json={"volume": -15.0})
        assert response.status_code == 200
        mock_dsp_service.set_volume.assert_called_once_with(-15.0)

    def test_dsp_mute_put(self, client, mock_dsp_service):
        """PUT /dsp/mute should update mute state."""
        response = client.put("/dsp/mute", json={"muted": True})
        assert response.status_code == 200
        mock_dsp_service.set_mute.assert_called_once_with(True)

    def test_dsp_compressor_get(self, client):
        """GET /dsp/compressor should return compressor state."""
        response = client.get("/dsp/compressor")
        assert response.status_code == 200
        data = response.json()
        assert "enabled" in data
        assert "threshold" in data

    def test_dsp_compressor_put(self, client, mock_dsp_service):
        """PUT /dsp/compressor should update compressor."""
        response = client.put("/dsp/compressor", json={"enabled": True, "threshold": -15.0})
        assert response.status_code == 200
        mock_dsp_service.set_compressor.assert_called_once()

    def test_dsp_loudness_get(self, client):
        """GET /dsp/loudness should return loudness state."""
        response = client.get("/dsp/loudness")
        assert response.status_code == 200
        data = response.json()
        assert "enabled" in data

    def test_dsp_delay_get(self, client):
        """GET /dsp/delay should return delay state."""
        response = client.get("/dsp/delay")
        assert response.status_code == 200
        data = response.json()
        assert "left" in data
        assert "right" in data

    def test_dsp_crossover_get(self, client):
        """GET /dsp/crossover should return crossover state."""
        response = client.get("/dsp/crossover")
        assert response.status_code == 200
        data = response.json()
        assert "enabled" in data
        assert "frequency" in data

    def test_dsp_lowpass_get(self, client):
        """GET /dsp/lowpass should return lowpass state."""
        response = client.get("/dsp/lowpass")
        assert response.status_code == 200
        data = response.json()
        assert "enabled" in data
        assert "frequency" in data

    def test_dsp_levels_get(self, client):
        """GET /dsp/levels should return audio levels."""
        response = client.get("/dsp/levels")
        assert response.status_code == 200
        data = response.json()
        assert "available" in data
