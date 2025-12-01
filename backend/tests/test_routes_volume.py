# backend/tests/test_routes_volume.py
"""
Unit tests for Volume API routes
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import Mock, AsyncMock
from backend.presentation.api.routes.volume import create_volume_router


class TestVolumeRoutes:
    """Tests for volume routes"""

    @pytest.fixture
    def mock_volume_service(self):
        """Volume service mock"""
        service = Mock()
        service.get_status = AsyncMock(return_value={
            "volume": 50,
            "muted": False,
            "multiroom_enabled": False
        })
        service.get_display_volume = AsyncMock(return_value=50)
        service.set_display_volume = AsyncMock(return_value=True)
        service.adjust_display_volume = AsyncMock(return_value=True)
        # Note: increase/decrease endpoints now use adjust_display_volume(±5)
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
        assert response.json()["volume"] == 50

    def test_get_volume_status_error(self, client):
        """Test GET /api/volume/status with service error"""
        client._mock_service.get_status = AsyncMock(side_effect=Exception("Service error"))
        response = client.get("/api/volume/status")
        assert response.status_code == 200  # Returns error in body, not HTTP error
        assert response.json()["status"] == "error"

    # ===================
    # SET VOLUME TESTS
    # ===================

    def test_set_volume_valid(self, client):
        """Test POST /api/volume/set with valid volume"""
        response = client.post("/api/volume/set", json={"volume": 75})
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert response.json()["volume"] == 75
        client._mock_service.set_display_volume.assert_called_once_with(75, show_bar=True)

    def test_set_volume_min(self, client):
        """Test POST /api/volume/set with minimum volume (0)"""
        response = client.post("/api/volume/set", json={"volume": 0})
        assert response.status_code == 200
        assert response.json()["status"] == "success"

    def test_set_volume_max(self, client):
        """Test POST /api/volume/set with maximum volume (100)"""
        response = client.post("/api/volume/set", json={"volume": 100})
        assert response.status_code == 200
        assert response.json()["status"] == "success"

    def test_set_volume_with_show_bar_false(self, client):
        """Test POST /api/volume/set with show_bar=false"""
        response = client.post("/api/volume/set", json={"volume": 50, "show_bar": False})
        assert response.status_code == 200
        client._mock_service.set_display_volume.assert_called_once_with(50, show_bar=False)

    def test_set_volume_too_high(self, client):
        """Test POST /api/volume/set with volume > 100 - should return 422"""
        response = client.post("/api/volume/set", json={"volume": 150})
        assert response.status_code == 422

    def test_set_volume_negative(self, client):
        """Test POST /api/volume/set with negative volume - should return 422"""
        response = client.post("/api/volume/set", json={"volume": -10})
        assert response.status_code == 422

    def test_set_volume_missing_field(self, client):
        """Test POST /api/volume/set without volume field - should return 422"""
        response = client.post("/api/volume/set", json={})
        assert response.status_code == 422

    def test_set_volume_service_failure(self, client):
        """Test POST /api/volume/set when service fails"""
        client._mock_service.set_display_volume = AsyncMock(return_value=False)
        response = client.post("/api/volume/set", json={"volume": 50})
        assert response.status_code == 500

    # ===================
    # ADJUST VOLUME TESTS
    # ===================

    def test_adjust_volume_positive(self, client):
        """Test POST /api/volume/adjust with positive delta"""
        response = client.post("/api/volume/adjust", json={"delta": 10})
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert response.json()["delta"] == 10

    def test_adjust_volume_negative(self, client):
        """Test POST /api/volume/adjust with negative delta"""
        response = client.post("/api/volume/adjust", json={"delta": -10})
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert response.json()["delta"] == -10

    def test_adjust_volume_max_delta(self, client):
        """Test POST /api/volume/adjust with max delta (100)"""
        response = client.post("/api/volume/adjust", json={"delta": 100})
        assert response.status_code == 200

    def test_adjust_volume_min_delta(self, client):
        """Test POST /api/volume/adjust with min delta (-100)"""
        response = client.post("/api/volume/adjust", json={"delta": -100})
        assert response.status_code == 200

    def test_adjust_volume_delta_too_high(self, client):
        """Test POST /api/volume/adjust with delta > 100 - should return 422"""
        response = client.post("/api/volume/adjust", json={"delta": 150})
        assert response.status_code == 422

    def test_adjust_volume_delta_too_low(self, client):
        """Test POST /api/volume/adjust with delta < -100 - should return 422"""
        response = client.post("/api/volume/adjust", json={"delta": -150})
        assert response.status_code == 422

    def test_adjust_volume_service_failure(self, client):
        """Test POST /api/volume/adjust when service fails"""
        client._mock_service.adjust_display_volume = AsyncMock(return_value=False)
        response = client.post("/api/volume/adjust", json={"delta": 10})
        assert response.status_code == 500

    # ===================
    # INCREASE/DECREASE TESTS
    # ===================

    def test_increase_volume(self, client):
        """Test POST /api/volume/increase - now uses adjust_display_volume(5)"""
        response = client.post("/api/volume/increase")
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        # Now uses adjust_display_volume(5) instead of increase_display_volume(5)
        client._mock_service.adjust_display_volume.assert_called_with(5)

    def test_increase_volume_service_failure(self, client):
        """Test POST /api/volume/increase when service fails"""
        client._mock_service.adjust_display_volume = AsyncMock(return_value=False)
        response = client.post("/api/volume/increase")
        assert response.status_code == 500

    def test_decrease_volume(self, client):
        """Test POST /api/volume/decrease - now uses adjust_display_volume(-5)"""
        # Reset mock to track new calls
        client._mock_service.adjust_display_volume = AsyncMock(return_value=True)
        response = client.post("/api/volume/decrease")
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        # Now uses adjust_display_volume(-5) instead of decrease_display_volume(5)
        client._mock_service.adjust_display_volume.assert_called_with(-5)

    def test_decrease_volume_service_failure(self, client):
        """Test POST /api/volume/decrease when service fails"""
        client._mock_service.adjust_display_volume = AsyncMock(return_value=False)
        response = client.post("/api/volume/decrease")
        assert response.status_code == 500
