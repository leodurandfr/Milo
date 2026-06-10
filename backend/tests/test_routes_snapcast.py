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
        # Mock volume_service with async broadcast_volume_state for mute tests
        sm.volume_service = Mock()
        sm.volume_service.broadcast_volume_state = AsyncMock()
        return sm

    @pytest.fixture
    def client(self, mock_routing_service, mock_snapcast_service, mock_state_machine):
        """Fixture to create a TestClient"""
        app = FastAPI()
        router = create_snapcast_router(
            mock_routing_service,
            mock_snapcast_service,
            mock_state_machine,
        )
        app.include_router(router)
        client = TestClient(app)
        client._mock_routing = mock_routing_service
        client._mock_snapcast = mock_snapcast_service
        client._mock_state_machine = mock_state_machine
        return client

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
