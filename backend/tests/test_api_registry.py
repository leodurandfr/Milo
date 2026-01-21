# backend/tests/test_api_registry.py
"""
Unit tests for ClientRegistryService API endpoints.

Tests cover:
- GET /api/registry/clients (AC1)
- GET /api/registry/clients/{mac_id} (AC2)
- PUT /api/registry/clients/{mac_id} (AC3)
- DELETE /api/registry/clients/{mac_id} (AC4)
"""
import pytest
from unittest.mock import Mock, AsyncMock
from fastapi.testclient import TestClient
from fastapi import FastAPI

from backend.api.registry import create_registry_router
from backend.core.multiroom.models import Client


@pytest.fixture
def mock_registry_service():
    """Create a mock ClientRegistryService."""
    service = Mock()

    # Default client for tests
    test_client = Client(
        mac_id="milo-client-01",
        name="Living Room",
        ip="192.168.1.100",
        online=True,
        zone_id=None,
        volume_db=-30.0,
        mute=False,
        speaker_type="bookshelf"
    )

    # Configure mock methods
    service.get_all_clients = Mock(return_value={
        "milo-client-01": test_client,
        "local": Client(
            mac_id="local",
            name="Main",
            ip="127.0.0.1",
            online=True,
            zone_id=None,
            volume_db=-25.0,
            mute=False,
            speaker_type="tower"
        )
    })

    service.get_client = Mock(side_effect=lambda mac_id: test_client if mac_id == "milo-client-01" else None)
    service.get_online_clients = Mock(return_value=[test_client])
    service.is_client_online = Mock(side_effect=lambda mac_id: mac_id == "milo-client-01")
    service.unregister_client = AsyncMock(side_effect=lambda mac_id: mac_id == "milo-client-01")
    service.update_client = AsyncMock(return_value=test_client)

    return service


@pytest.fixture
def client(mock_registry_service):
    """Create a test client with the registry router."""
    app = FastAPI()
    router = create_registry_router(mock_registry_service)
    app.include_router(router)
    return TestClient(app)


class TestGetClients:
    """Tests for GET /api/registry/clients endpoint."""

    def test_get_clients_returns_all_clients(self, client, mock_registry_service):
        """Test GET /clients returns all registered clients."""
        response = client.get("/api/registry/clients")

        assert response.status_code == 200
        data = response.json()
        assert "clients" in data
        assert len(data["clients"]) == 2

        # Verify client data
        client_macs = [c["mac_id"] for c in data["clients"]]
        assert "milo-client-01" in client_macs
        assert "local" in client_macs

    def test_get_clients_empty_registry(self, client, mock_registry_service):
        """Test GET /clients with no clients returns empty list."""
        mock_registry_service.get_all_clients.return_value = {}

        response = client.get("/api/registry/clients")

        assert response.status_code == 200
        assert response.json() == {"clients": []}


class TestGetClientById:
    """Tests for GET /api/registry/clients/{mac_id} endpoint."""

    def test_get_client_by_mac_id_success(self, client, mock_registry_service):
        """Test GET /clients/{mac_id} returns client details."""
        response = client.get("/api/registry/clients/milo-client-01")

        assert response.status_code == 200
        data = response.json()
        assert data["mac_id"] == "milo-client-01"
        assert data["name"] == "Living Room"
        assert data["online"] is True
        assert data["speaker_type"] == "bookshelf"

    def test_get_client_not_found(self, client, mock_registry_service):
        """Test GET /clients/{mac_id} returns 404 for unknown client."""
        response = client.get("/api/registry/clients/unknown-client")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestUpdateClient:
    """Tests for PUT /api/registry/clients/{mac_id} endpoint."""

    def test_update_client_name_success(self, client, mock_registry_service):
        """Test PUT /clients/{mac_id} updates client name."""
        response = client.put(
            "/api/registry/clients/milo-client-01",
            json={"name": "New Name"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "client" in data

        # Verify service was called correctly
        mock_registry_service.update_client.assert_called_once_with(
            "milo-client-01",
            name="New Name",
            speaker_type=None
        )

    def test_update_client_speaker_type_success(self, client, mock_registry_service):
        """Test PUT /clients/{mac_id} updates speaker_type."""
        response = client.put(
            "/api/registry/clients/milo-client-01",
            json={"speaker_type": "subwoofer"}
        )

        assert response.status_code == 200
        mock_registry_service.update_client.assert_called_once_with(
            "milo-client-01",
            name=None,
            speaker_type="subwoofer"
        )

    def test_update_client_both_fields(self, client, mock_registry_service):
        """Test PUT /clients/{mac_id} updates both name and speaker_type."""
        response = client.put(
            "/api/registry/clients/milo-client-01",
            json={"name": "Kitchen", "speaker_type": "satellite"}
        )

        assert response.status_code == 200
        mock_registry_service.update_client.assert_called_once_with(
            "milo-client-01",
            name="Kitchen",
            speaker_type="satellite"
        )

    def test_update_client_not_found(self, client, mock_registry_service):
        """Test PUT /clients/{mac_id} returns 404 for unknown client."""
        response = client.put(
            "/api/registry/clients/unknown-client",
            json={"name": "Test"}
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestDeleteClient:
    """Tests for DELETE /api/registry/clients/{mac_id} endpoint."""

    def test_delete_client_success(self, client, mock_registry_service):
        """Test DELETE /clients/{mac_id} removes client."""
        response = client.delete("/api/registry/clients/milo-client-01")

        assert response.status_code == 200
        assert response.json() == {"status": "success"}
        mock_registry_service.unregister_client.assert_called_once_with("milo-client-01")

    def test_delete_client_not_found(self, client, mock_registry_service):
        """Test DELETE /clients/{mac_id} returns 404 for unknown client."""
        response = client.delete("/api/registry/clients/unknown-client")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestGetOnlineClients:
    """Tests for GET /api/registry/clients/online endpoint."""

    def test_get_online_clients(self, client, mock_registry_service):
        """Test GET /clients/online returns only online clients."""
        response = client.get("/api/registry/clients/online")

        assert response.status_code == 200
        data = response.json()
        assert "clients" in data
        mock_registry_service.get_online_clients.assert_called_once()


class TestCheckClientOnline:
    """Tests for GET /api/registry/clients/{mac_id}/online endpoint."""

    def test_check_client_online_true(self, client, mock_registry_service):
        """Test GET /clients/{mac_id}/online returns true for online client."""
        response = client.get("/api/registry/clients/milo-client-01/online")

        assert response.status_code == 200
        data = response.json()
        assert data["mac_id"] == "milo-client-01"
        assert data["online"] is True

    def test_check_client_online_false(self, client, mock_registry_service):
        """Test GET /clients/{mac_id}/online returns false for offline client."""
        response = client.get("/api/registry/clients/offline-client/online")

        assert response.status_code == 200
        data = response.json()
        assert data["mac_id"] == "offline-client"
        assert data["online"] is False


class TestClientZone:
    """Tests for GET /api/registry/clients/{mac_id}/zone endpoint."""

    def test_get_client_zone_none(self, client, mock_registry_service):
        """Test GET /clients/{mac_id}/zone returns None when client not in zone."""
        mock_registry_service.get_zone_for_client = Mock(return_value=None)

        response = client.get("/api/registry/clients/milo-client-01/zone")

        assert response.status_code == 200
        data = response.json()
        assert data["mac_id"] == "milo-client-01"
        assert data["zone"] is None
