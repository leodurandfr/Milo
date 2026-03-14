# backend/tests/test_routes_volume.py
"""
Unit tests for Volume API routes - dB API Version

All volume values are in dB (-80 to 0).
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import Mock, AsyncMock
from backend.api.volume import create_volume_router
from backend.core.models.volume import VolumeConfig


class TestVolumeRoutes:
    """Tests for volume routes (dB API)"""

    @pytest.fixture
    def mock_volume_service(self):
        """Volume service mock with dB API"""
        service = Mock()

        # Volume operations (all in dB)
        service.get_volume_db = AsyncMock(return_value=-30.0)
        service.adjust_volume_db = AsyncMock(return_value=True)

        # Config for step values
        service.volume_config = VolumeConfig()

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
