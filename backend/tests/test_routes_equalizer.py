# backend/tests/test_routes_equalizer.py
"""
Unit tests for Equalizer API routes
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import Mock, AsyncMock
from backend.presentation.api.routes.equalizer import create_equalizer_router


class TestEqualizerRoutes:
    """Tests for equalizer routes"""

    @pytest.fixture
    def mock_equalizer_service(self):
        """Equalizer service mock"""
        service = Mock()
        service.get_equalizer_status = AsyncMock(return_value={
            "available": True,
            "bands": [
                {"id": "band0", "frequency": "32Hz", "value": 50},
                {"id": "band1", "frequency": "64Hz", "value": 50},
            ]
        })
        service.get_all_bands = AsyncMock(return_value=[
            {"id": "band0", "frequency": "32Hz", "value": 50},
            {"id": "band1", "frequency": "64Hz", "value": 50},
        ])
        service.set_band_value = AsyncMock(return_value=True)
        service.reset_all_bands = AsyncMock(return_value=True)
        service.is_available = AsyncMock(return_value=True)
        return service

    @pytest.fixture
    def mock_state_machine(self):
        """State machine mock"""
        sm = Mock()
        sm.broadcast_event = AsyncMock()
        return sm

    @pytest.fixture
    def client(self, mock_equalizer_service, mock_state_machine):
        """Fixture to create a TestClient"""
        app = FastAPI()
        router = create_equalizer_router(mock_equalizer_service, mock_state_machine)
        app.include_router(router)
        client = TestClient(app)
        client._mock_equalizer = mock_equalizer_service
        client._mock_state_machine = mock_state_machine
        return client

    # ===================
    # STATUS TESTS
    # ===================

    def test_get_equalizer_status(self, client):
        """Test GET /api/equalizer/status"""
        response = client.get("/api/equalizer/status")
        assert response.status_code == 200
        assert response.json()["available"] is True
        assert "bands" in response.json()
        assert len(response.json()["bands"]) == 2

    def test_get_equalizer_status_error(self, client):
        """Test GET /api/equalizer/status with service error"""
        client._mock_equalizer.get_equalizer_status = AsyncMock(
            side_effect=Exception("Service error")
        )
        response = client.get("/api/equalizer/status")
        assert response.status_code == 200
        assert response.json()["available"] is False
        assert "error" in response.json()

    # ===================
    # BANDS TESTS
    # ===================

    def test_get_all_bands(self, client):
        """Test GET /api/equalizer/bands"""
        response = client.get("/api/equalizer/bands")
        assert response.status_code == 200
        assert "bands" in response.json()
        assert len(response.json()["bands"]) == 2

    def test_get_all_bands_error(self, client):
        """Test GET /api/equalizer/bands with service error"""
        client._mock_equalizer.get_all_bands = AsyncMock(
            side_effect=Exception("Service error")
        )
        response = client.get("/api/equalizer/bands")
        assert response.status_code == 200
        assert response.json()["bands"] == []
        assert "error" in response.json()

    # ===================
    # SET BAND VALUE TESTS
    # ===================

    def test_set_band_value_valid(self, client):
        """Test POST /api/equalizer/band/{band_id} with valid value"""
        response = client.post("/api/equalizer/band/band0", json={"value": 75})
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert response.json()["band_id"] == "band0"
        assert response.json()["value"] == 75
        client._mock_equalizer.set_band_value.assert_called_once_with("band0", 75)
        client._mock_state_machine.broadcast_event.assert_called_once()

    def test_set_band_value_min(self, client):
        """Test POST /api/equalizer/band/{band_id} with minimum value (0)"""
        response = client.post("/api/equalizer/band/band1", json={"value": 0})
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert response.json()["value"] == 0

    def test_set_band_value_max(self, client):
        """Test POST /api/equalizer/band/{band_id} with maximum value (100)"""
        response = client.post("/api/equalizer/band/band2", json={"value": 100})
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert response.json()["value"] == 100

    def test_set_band_value_out_of_range_high(self, client):
        """Test POST /api/equalizer/band/{band_id} with value > 100 - should return 422"""
        response = client.post("/api/equalizer/band/band0", json={"value": 150})
        assert response.status_code == 422

    def test_set_band_value_out_of_range_low(self, client):
        """Test POST /api/equalizer/band/{band_id} with value < 0 - should return 422"""
        response = client.post("/api/equalizer/band/band0", json={"value": -10})
        assert response.status_code == 422

    def test_set_band_value_missing_field(self, client):
        """Test POST /api/equalizer/band/{band_id} without value field - should return 422"""
        response = client.post("/api/equalizer/band/band0", json={})
        assert response.status_code == 422

    def test_set_band_value_service_failure(self, client):
        """Test POST /api/equalizer/band/{band_id} when service fails"""
        client._mock_equalizer.set_band_value = AsyncMock(return_value=False)
        response = client.post("/api/equalizer/band/band0", json={"value": 50})
        assert response.status_code == 200
        assert response.json()["status"] == "error"

    # ===================
    # RESET TESTS
    # ===================

    def test_reset_all_bands_default(self, client):
        """Test POST /api/equalizer/reset without payload (default 50%)"""
        response = client.post("/api/equalizer/reset")
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert response.json()["reset_value"] == 50
        client._mock_equalizer.reset_all_bands.assert_called_once_with(50)

    def test_reset_all_bands_custom_value(self, client):
        """Test POST /api/equalizer/reset with custom value"""
        response = client.post("/api/equalizer/reset", json={"value": 75})
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert response.json()["reset_value"] == 75
        client._mock_equalizer.reset_all_bands.assert_called_once_with(75)

    def test_reset_all_bands_min_value(self, client):
        """Test POST /api/equalizer/reset with minimum value (0)"""
        response = client.post("/api/equalizer/reset", json={"value": 0})
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert response.json()["reset_value"] == 0

    def test_reset_all_bands_max_value(self, client):
        """Test POST /api/equalizer/reset with maximum value (100)"""
        response = client.post("/api/equalizer/reset", json={"value": 100})
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert response.json()["reset_value"] == 100

    def test_reset_all_bands_out_of_range(self, client):
        """Test POST /api/equalizer/reset with out of range value - should return 422"""
        response = client.post("/api/equalizer/reset", json={"value": 150})
        assert response.status_code == 422

    def test_reset_all_bands_service_failure(self, client):
        """Test POST /api/equalizer/reset when service fails"""
        client._mock_equalizer.reset_all_bands = AsyncMock(return_value=False)
        response = client.post("/api/equalizer/reset", json={"value": 50})
        assert response.status_code == 200
        assert response.json()["status"] == "error"

    # ===================
    # AVAILABLE TESTS
    # ===================

    def test_check_equalizer_available_true(self, client):
        """Test GET /api/equalizer/available when available"""
        response = client.get("/api/equalizer/available")
        assert response.status_code == 200
        assert response.json()["available"] is True

    def test_check_equalizer_available_false(self, client):
        """Test GET /api/equalizer/available when unavailable"""
        client._mock_equalizer.is_available = AsyncMock(return_value=False)
        response = client.get("/api/equalizer/available")
        assert response.status_code == 200
        assert response.json()["available"] is False

    def test_check_equalizer_available_error(self, client):
        """Test GET /api/equalizer/available with service error"""
        client._mock_equalizer.is_available = AsyncMock(
            side_effect=Exception("Service error")
        )
        response = client.get("/api/equalizer/available")
        assert response.status_code == 200
        assert response.json()["available"] is False
        assert "error" in response.json()
