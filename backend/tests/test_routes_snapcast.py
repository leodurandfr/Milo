# backend/tests/test_routes_snapcast.py
"""
Unit tests for Snapcast API routes
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import Mock, AsyncMock
from backend.core.multiroom.routes import create_snapcast_router


class TestSnapcastRoutes:
    """Tests for Snapcast routes"""

    @pytest.fixture
    def mock_routing_service(self):
        """Routing service mock"""
        service = Mock()
        service.get_state = Mock(return_value={'multiroom_enabled': True})
        return service

    @pytest.fixture
    def mock_snapcast_service(self):
        """Snapcast service mock"""
        service = Mock()
        service.is_available = AsyncMock(return_value=True)
        service.get_clients = AsyncMock(return_value=[
            {"id": "client1", "name": "Client 1", "volume": 50, "muted": False, "host": "milo", "ip": "127.0.0.1", "camilladsp_id": "local"},
            {"id": "client2", "name": "Client 2", "volume": 75, "muted": True, "host": "remote", "ip": "192.168.1.100", "camilladsp_id": "192.168.1.100"}
        ])
        service.get_detailed_clients = AsyncMock(return_value=[
            {"id": "client1", "name": "Client 1", "volume": 50, "muted": False, "host": "milo"},
        ])
        service.get_server_config = AsyncMock(return_value={"version": "0.27.0"})
        service.set_volume = AsyncMock(return_value=True)
        service.set_mute = AsyncMock(return_value=True)
        service.set_client_name = AsyncMock(return_value=True)
        service.update_server_config = AsyncMock(return_value=True)
        return service

    @pytest.fixture
    def mock_state_machine(self):
        """State machine mock"""
        sm = Mock()
        sm.broadcast_event = AsyncMock()
        # Mock volume_service with async _broadcast_volume_state for mute tests
        sm.volume_service = Mock()
        sm.volume_service._broadcast_volume_state = AsyncMock()
        return sm

    @pytest.fixture
    def mock_camilladsp_service(self):
        """Equalizer service mock for local mute control"""
        service = Mock()
        service.set_mute = AsyncMock(return_value=True)
        return service

    @pytest.fixture
    def client(self, mock_routing_service, mock_snapcast_service, mock_state_machine, mock_camilladsp_service):
        """Fixture to create a TestClient"""
        app = FastAPI()
        router = create_snapcast_router(
            mock_routing_service,
            mock_snapcast_service,
            mock_state_machine,
            camilladsp_service=mock_camilladsp_service
        )
        app.include_router(router)
        client = TestClient(app)
        client._mock_routing = mock_routing_service
        client._mock_snapcast = mock_snapcast_service
        client._mock_state_machine = mock_state_machine
        client._mock_camilladsp_service = mock_camilladsp_service
        return client

    # ===================
    # STATUS TESTS
    # ===================

    def test_get_snapcast_status(self, client):
        """Test GET /api/routing/snapcast/status"""
        response = client.get("/api/routing/snapcast/status")
        assert response.status_code == 200
        assert "available" in response.json()
        assert response.json()["available"] is True
        assert response.json()["client_count"] == 2

    def test_get_snapcast_status_unavailable(self, client):
        """Test GET /api/routing/snapcast/status when unavailable"""
        client._mock_snapcast.is_available = AsyncMock(return_value=False)
        response = client.get("/api/routing/snapcast/status")
        assert response.status_code == 200
        assert response.json()["available"] is False

    # ===================
    # CLIENTS TESTS
    # ===================

    def test_get_snapcast_clients(self, client):
        """Test GET /api/routing/snapcast/clients"""
        response = client.get("/api/routing/snapcast/clients")
        assert response.status_code == 200
        assert "clients" in response.json()
        assert len(response.json()["clients"]) == 2

    def test_get_snapcast_clients_multiroom_disabled(self, client):
        """Test GET /api/routing/snapcast/clients when multiroom disabled"""
        client._mock_routing.get_state = Mock(return_value={'multiroom_enabled': False})
        response = client.get("/api/routing/snapcast/clients")
        assert response.status_code == 200
        assert response.json()["clients"] == []
        assert "message" in response.json()

    # Note: Volume and mute endpoints were moved to /api/volume/ routes
    # and are tested in test_volume_api.py

    # ===================
    # NAME TESTS
    # ===================

    def test_set_client_name_valid(self, client):
        """Test POST /api/routing/snapcast/client/{id}/name with valid name"""
        response = client.post(
            "/api/routing/snapcast/client/client1/name",
            json={"name": "Salon"}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        client._mock_snapcast.set_client_name.assert_called_once_with("client1", "Salon")

    def test_set_client_name_empty(self, client):
        """Test POST /api/routing/snapcast/client/{id}/name with empty name - should return 422"""
        response = client.post(
            "/api/routing/snapcast/client/client1/name",
            json={"name": ""}
        )
        assert response.status_code == 422

    def test_set_client_name_too_long(self, client):
        """Test POST /api/routing/snapcast/client/{id}/name with name too long"""
        long_name = "x" * 101
        response = client.post(
            "/api/routing/snapcast/client/client1/name",
            json={"name": long_name}
        )
        assert response.status_code == 422

    # ===================
    # MONITORING TESTS
    # ===================

    def test_get_snapcast_monitoring(self, client):
        """Test GET /api/routing/snapcast/monitoring"""
        response = client.get("/api/routing/snapcast/monitoring")
        assert response.status_code == 200
        assert response.json()["available"] is True
        assert "clients" in response.json()
        assert "server_config" in response.json()

    def test_get_snapcast_monitoring_multiroom_disabled(self, client):
        """Test GET /api/routing/snapcast/monitoring when multiroom disabled"""
        client._mock_routing.get_state = Mock(return_value={'multiroom_enabled': False})
        response = client.get("/api/routing/snapcast/monitoring")
        assert response.status_code == 200
        assert response.json()["available"] is False

    # ===================
    # SERVER CONFIG TESTS
    # ===================

    def test_get_server_config(self, client):
        """Test GET /api/routing/snapcast/server-config"""
        response = client.get("/api/routing/snapcast/server-config")
        assert response.status_code == 200
        assert "config" in response.json()

    def test_get_server_config_unavailable(self, client):
        """Test GET /api/routing/snapcast/server-config when unavailable"""
        client._mock_snapcast.is_available = AsyncMock(return_value=False)
        response = client.get("/api/routing/snapcast/server-config")
        assert response.status_code == 200
        assert response.json()["config"] is None
        assert "error" in response.json()

    def test_update_server_config(self, client):
        """Test POST /api/routing/snapcast/server/config"""
        response = client.post(
            "/api/routing/snapcast/server/config",
            json={"config": {"buffer": 1000}}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "success"
