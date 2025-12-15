# backend/tests/test_routes_volume.py
"""
Unit tests for Volume API routes - dB API Version

All volume values are in dB (-80 to 0).
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import Mock, AsyncMock
from backend.presentation.api.routes.volume import create_volume_router


class TestVolumeRoutes:
    """Tests for volume routes (dB API)"""

    @pytest.fixture
    def mock_volume_service(self):
        """Volume service mock with dB API"""
        service = Mock()

        # Status endpoint
        service.get_status = AsyncMock(return_value={
            "volume_db": -30.0,
            "muted": False,
            "multiroom_enabled": False
        })

        # Volume operations (all in dB)
        service.get_volume_db = AsyncMock(return_value=-30.0)
        service.set_volume_db = AsyncMock(return_value=True)
        service.adjust_volume_db = AsyncMock(return_value=True)

        # Config for step values
        mock_config = Mock()
        mock_config.config = Mock()
        mock_config.config.step_mobile_db = 3.0
        service.config = mock_config

        return service

    @pytest.fixture
    def client(self, mock_volume_service):
        """Fixture to create a TestClient"""
        app = FastAPI()
        router = create_volume_router(mock_volume_service)
        app.include_router(router)
        client = TestClient(app)
        client._mock_service = mock_volume_service
        return client

    # ===================
    # GET TESTS
    # ===================

    def test_get_volume_status(self, client):
        """Test GET /api/volume/status"""
        response = client.get("/api/volume/status")
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert "data" in response.json()

    def test_get_current_volume(self, client):
        """Test GET /api/volume/"""
        response = client.get("/api/volume/")
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert response.json()["volume_db"] == -30.0

    def test_get_volume_status_error(self, client):
        """Test GET /api/volume/status with service error"""
        client._mock_service.get_status = AsyncMock(side_effect=Exception("Service error"))
        response = client.get("/api/volume/status")
        assert response.status_code == 200  # Returns error in body, not HTTP error
        assert response.json()["status"] == "error"

    # ===================
    # SET VOLUME TESTS (dB: -80 to 0)
    # ===================

    def test_set_volume_valid(self, client):
        """Test POST /api/volume/set with valid volume in dB"""
        response = client.post("/api/volume/set", json={"volume_db": -30.0})
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert response.json()["volume_db"] == -30.0
        client._mock_service.set_volume_db.assert_called_once_with(-30.0, show_bar=True)

    def test_set_volume_min(self, client):
        """Test POST /api/volume/set with minimum volume (-80 dB)"""
        response = client.post("/api/volume/set", json={"volume_db": -80.0})
        assert response.status_code == 200
        assert response.json()["status"] == "success"

    def test_set_volume_max(self, client):
        """Test POST /api/volume/set with maximum volume (0 dB)"""
        response = client.post("/api/volume/set", json={"volume_db": 0.0})
        assert response.status_code == 200
        assert response.json()["status"] == "success"

    def test_set_volume_with_show_bar_false(self, client):
        """Test POST /api/volume/set with show_bar=false"""
        response = client.post("/api/volume/set", json={"volume_db": -30.0, "show_bar": False})
        assert response.status_code == 200
        client._mock_service.set_volume_db.assert_called_once_with(-30.0, show_bar=False)

    def test_set_volume_too_high(self, client):
        """Test POST /api/volume/set with volume > 0 dB - should return 422"""
        response = client.post("/api/volume/set", json={"volume_db": 10.0})
        assert response.status_code == 422

    def test_set_volume_too_low(self, client):
        """Test POST /api/volume/set with volume < -80 dB - should return 422"""
        response = client.post("/api/volume/set", json={"volume_db": -100.0})
        assert response.status_code == 422

    def test_set_volume_missing_field(self, client):
        """Test POST /api/volume/set without volume_db field - should return 422"""
        response = client.post("/api/volume/set", json={})
        assert response.status_code == 422

    def test_set_volume_service_failure(self, client):
        """Test POST /api/volume/set when service fails"""
        client._mock_service.set_volume_db = AsyncMock(return_value=False)
        response = client.post("/api/volume/set", json={"volume_db": -30.0})
        assert response.status_code == 500

    # ===================
    # ADJUST VOLUME TESTS (delta in dB: -60 to 60)
    # ===================

    def test_adjust_volume_positive(self, client):
        """Test POST /api/volume/adjust with positive delta in dB"""
        response = client.post("/api/volume/adjust", json={"delta_db": 3.0})
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert response.json()["delta_db"] == 3.0

    def test_adjust_volume_negative(self, client):
        """Test POST /api/volume/adjust with negative delta in dB"""
        response = client.post("/api/volume/adjust", json={"delta_db": -3.0})
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert response.json()["delta_db"] == -3.0

    def test_adjust_volume_max_delta(self, client):
        """Test POST /api/volume/adjust with max delta (60 dB)"""
        response = client.post("/api/volume/adjust", json={"delta_db": 60.0})
        assert response.status_code == 200

    def test_adjust_volume_min_delta(self, client):
        """Test POST /api/volume/adjust with min delta (-60 dB)"""
        response = client.post("/api/volume/adjust", json={"delta_db": -60.0})
        assert response.status_code == 200

    def test_adjust_volume_delta_too_high(self, client):
        """Test POST /api/volume/adjust with delta > 60 dB - should return 422"""
        response = client.post("/api/volume/adjust", json={"delta_db": 100.0})
        assert response.status_code == 422

    def test_adjust_volume_delta_too_low(self, client):
        """Test POST /api/volume/adjust with delta < -60 dB - should return 422"""
        response = client.post("/api/volume/adjust", json={"delta_db": -100.0})
        assert response.status_code == 422

    def test_adjust_volume_service_failure(self, client):
        """Test POST /api/volume/adjust when service fails"""
        client._mock_service.adjust_volume_db = AsyncMock(return_value=False)
        response = client.post("/api/volume/adjust", json={"delta_db": 3.0})
        assert response.status_code == 500

    # ===================
    # INCREASE/DECREASE TESTS (uses step_mobile_db from config)
    # ===================

    def test_increase_volume(self, client):
        """Test POST /api/volume/increase - uses step_mobile_db (3.0 dB)"""
        response = client.post("/api/volume/increase")
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        # Uses adjust_volume_db with step_mobile_db (3.0 dB)
        client._mock_service.adjust_volume_db.assert_called_with(3.0)

    def test_increase_volume_service_failure(self, client):
        """Test POST /api/volume/increase when service fails"""
        client._mock_service.adjust_volume_db = AsyncMock(return_value=False)
        response = client.post("/api/volume/increase")
        assert response.status_code == 500

    def test_decrease_volume(self, client):
        """Test POST /api/volume/decrease - uses -step_mobile_db (-3.0 dB)"""
        # Reset mock to track new calls
        client._mock_service.adjust_volume_db = AsyncMock(return_value=True)
        response = client.post("/api/volume/decrease")
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        # Uses adjust_volume_db with -step_mobile_db (-3.0 dB)
        client._mock_service.adjust_volume_db.assert_called_with(-3.0)

    def test_decrease_volume_service_failure(self, client):
        """Test POST /api/volume/decrease when service fails"""
        client._mock_service.adjust_volume_db = AsyncMock(return_value=False)
        response = client.post("/api/volume/decrease")
        assert response.status_code == 500
