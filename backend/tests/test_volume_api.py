# backend/tests/test_volume_api.py
"""
Unit tests for client volume API endpoints.

Tests cover:
- PATCH /api/volume/client/mac/{mac_url} - Set client volume by MAC
- PATCH /api/volume/client/mac/{mac_url}/mute - Set client mute by MAC
- PATCH /api/volume/zone/{zone_id} - Apply zone volume delta
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.api.models import ClientVolumeRequest, ClientMuteRequest
from backend.api.volume import create_volume_router


# =============================================================================
# Pydantic Model Tests
# =============================================================================

class TestClientVolumeRequest:
    """Tests for ClientVolumeRequest Pydantic model."""

    def test_valid_volume(self):
        """Test valid volume_db values."""
        request = ClientVolumeRequest(volume_db=-30.0)
        assert request.volume_db == -30.0

    def test_min_volume(self):
        """Test minimum volume_db value."""
        request = ClientVolumeRequest(volume_db=-80.0)
        assert request.volume_db == -80.0

    def test_max_volume(self):
        """Test maximum volume_db value."""
        request = ClientVolumeRequest(volume_db=0.0)
        assert request.volume_db == 0.0

    def test_volume_too_low(self):
        """Test volume_db below -80 is rejected."""
        with pytest.raises(ValueError):
            ClientVolumeRequest(volume_db=-81.0)

    def test_volume_too_high(self):
        """Test volume_db above 0 is rejected."""
        with pytest.raises(ValueError):
            ClientVolumeRequest(volume_db=1.0)


class TestClientMuteRequest:
    """Tests for ClientMuteRequest Pydantic model."""

    def test_mute_true(self):
        """Test mute=True."""
        request = ClientMuteRequest(mute=True)
        assert request.mute is True

    def test_mute_false(self):
        """Test mute=False."""
        request = ClientMuteRequest(mute=False)
        assert request.mute is False


# =============================================================================
# MAC Address Endpoint Tests (Story 3.4)
# =============================================================================

class TestMacAddressClientVolume:
    """Tests for MAC address based client volume endpoints (AC1, AC3)."""

    @pytest.fixture
    def mock_volume_service(self):
        """Create a mock VolumeService for MAC tests."""
        service = MagicMock()
        service.update_client_volume_db = AsyncMock(return_value=None)
        service.set_client_mute = AsyncMock(return_value=None)
        service.get_client_volume = AsyncMock(return_value={
            "main": -25.0,
            "mute": False
        })
        service.config = MagicMock()
        service.volume_config = MagicMock()
        service.volume_config.limit_min_db = -80.0
        service.volume_config.limit_max_db = 0.0
        return service

    @pytest.fixture
    def mock_client_registry(self):
        """Create a mock ClientRegistryService with MAC lookup."""
        registry = MagicMock()
        # get_client returns Client object with mac_id
        registry.get_client = MagicMock(return_value=MagicMock(
            mac_id="dc:a6:32:7e:d3:43",
            name="Living Room",
            online=True,
            volume_db=-25.0,
            mute=False
        ))
        return registry

    @pytest.fixture
    def test_client(self, mock_volume_service, mock_client_registry):
        """Create a FastAPI test client with mocked services."""
        app = FastAPI()
        router = create_volume_router(mock_volume_service, mock_client_registry)
        app.include_router(router)
        return TestClient(app)

    def test_set_volume_with_mac_address(self, test_client, mock_volume_service, mock_client_registry):
        """AC1: Test setting volume using MAC address in URL (no colons)."""
        response = test_client.patch(
            "/api/volume/client/mac/dca6327ed343",
            json={"volume_db": -25.0}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["mac_id"] == "dc:a6:32:7e:d3:43"  # Response uses colons
        assert data["volume_db"] == -25.0

        # Verify registry was queried with colon format
        mock_client_registry.get_client.assert_called_with("dc:a6:32:7e:d3:43")
        # Verify VolumeService was called with MAC (colon format)
        mock_volume_service.update_client_volume_db.assert_called_once_with("dc:a6:32:7e:d3:43", -25.0)

    def test_set_volume_invalid_mac_format(self, test_client):
        """Test invalid MAC address format (wrong length) returns 400."""
        response = test_client.patch(
            "/api/volume/client/mac/invalid",
            json={"volume_db": -25.0}
        )

        assert response.status_code == 400
        assert "Invalid MAC address" in response.json()["detail"]

    def test_set_volume_invalid_mac_non_hex(self, test_client):
        """Test MAC address with non-hexadecimal characters returns 400."""
        response = test_client.patch(
            "/api/volume/client/mac/ghijklmnopqr",  # 12 chars but not hex
            json={"volume_db": -25.0}
        )

        assert response.status_code == 400
        assert "hexadecimal" in response.json()["detail"].lower()

    def test_set_volume_mac_not_found(self, test_client, mock_client_registry):
        """Test MAC address not in registry returns 404."""
        mock_client_registry.get_client.return_value = None

        response = test_client.patch(
            "/api/volume/client/mac/dca6327ed343",
            json={"volume_db": -25.0}
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_set_volume_mac_out_of_range(self, test_client, mock_volume_service, mock_client_registry):
        """Test volume outside configured limits returns 400."""
        mock_volume_service.volume_config.limit_min_db = -60.0
        mock_volume_service.volume_config.limit_max_db = -10.0

        response = test_client.patch(
            "/api/volume/client/mac/dca6327ed343",
            json={"volume_db": -70.0}
        )

        assert response.status_code == 400
        assert "out of configured range" in response.json()["detail"]

    def test_mute_with_mac_address(self, test_client, mock_volume_service, mock_client_registry):
        """AC3: Test muting client using MAC address."""
        response = test_client.patch(
            "/api/volume/client/mac/dca6327ed343/mute",
            json={"mute": True}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["mac_id"] == "dc:a6:32:7e:d3:43"
        assert data["mute"] is True

        mock_volume_service.set_client_mute.assert_called_once_with("dc:a6:32:7e:d3:43", True)

    def test_unmute_with_mac_address(self, test_client, mock_volume_service, mock_client_registry):
        """Test unmuting client using MAC address."""
        response = test_client.patch(
            "/api/volume/client/mac/dca6327ed343/mute",
            json={"mute": False}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["mute"] is False


class TestZoneVolumeDelta:
    """Tests for zone volume delta endpoint (AC2)."""

    @pytest.fixture
    def mock_volume_service(self):
        """Create a mock VolumeService for zone tests."""
        service = MagicMock()
        service.apply_zone_volume_delta = AsyncMock(return_value=-35.0)  # Returns new average
        service.get_volume_state = AsyncMock()
        return service

    @pytest.fixture
    def mock_client_registry(self):
        """Create a mock ClientRegistryService for zone tests."""
        registry = MagicMock()
        registry.get_zone = MagicMock(return_value=MagicMock(
            id="zone-uuid-123",
            name="Living Room",
            client_ids=["dc:a6:32:7e:d3:43", "aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66"]
        ))
        registry.get_online_zone_clients = MagicMock(return_value=[
            MagicMock(mac_id="dc:a6:32:7e:d3:43", online=True, volume_db=-40.0),
            MagicMock(mac_id="aa:bb:cc:dd:ee:ff", online=True, volume_db=-40.0)
        ])
        registry.get_zone_clients = MagicMock(return_value=[
            MagicMock(mac_id="dc:a6:32:7e:d3:43", online=True, volume_db=-40.0),
            MagicMock(mac_id="aa:bb:cc:dd:ee:ff", online=True, volume_db=-40.0),
            MagicMock(mac_id="11:22:33:44:55:66", online=False, volume_db=-40.0)
        ])
        return registry

    @pytest.fixture
    def test_client(self, mock_volume_service, mock_client_registry):
        """Create a FastAPI test client with mocked services."""
        app = FastAPI()
        router = create_volume_router(mock_volume_service, mock_client_registry)
        app.include_router(router)
        return TestClient(app)

    def test_apply_zone_delta(self, test_client, mock_volume_service, mock_client_registry):
        """AC2: Test applying delta to zone returns affected and offline clients."""
        response = test_client.patch(
            "/api/volume/zone/zone-uuid-123",
            json={"delta_db": 5.0}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["zone_id"] == "zone-uuid-123"
        assert data["delta_db"] == 5.0
        assert data["new_average_db"] == -35.0
        assert "applied_to" in data
        assert "offline_clients" in data
        # 2 online clients should be affected
        assert len(data["applied_to"]) == 2
        # 1 offline client
        assert len(data["offline_clients"]) == 1
        assert "11:22:33:44:55:66" in data["offline_clients"]

        mock_volume_service.apply_zone_volume_delta.assert_called_once_with("zone-uuid-123", 5.0)

    def test_apply_zone_delta_zone_not_found(self, test_client, mock_client_registry, mock_volume_service):
        """Test zone delta with invalid zone ID returns 404."""
        mock_client_registry.get_zone.return_value = None
        mock_volume_service.apply_zone_volume_delta.side_effect = ValueError("Zone not found")

        response = test_client.patch(
            "/api/volume/zone/invalid-zone",
            json={"delta_db": 5.0}
        )

        assert response.status_code == 404


# =============================================================================
# /api/volume/adjust route — deferred-success vs genuine-failure mapping
# =============================================================================

class TestVolumeAdjustRoute:
    """The adjust route maps service success→200 and service failure→500.

    After the cold-boot fix, adjust_volume_db returns True on the deferred path
    (CamillaDSP reconnecting) and only False on a genuine failure (e.g. local MAC
    unresolved, or a connected set_volume command failure)."""

    @pytest.fixture
    def mock_volume_service(self):
        service = MagicMock()
        service.adjust_volume_db = AsyncMock(return_value=True)
        service.get_volume_db = AsyncMock(return_value=-40.0)
        return service

    @pytest.fixture
    def test_client(self, mock_volume_service):
        app = FastAPI()
        app.include_router(create_volume_router(mock_volume_service))
        return TestClient(app)

    def test_adjust_success_returns_200(self, test_client, mock_volume_service):
        response = test_client.post("/api/volume/adjust", json={"delta_db": 2.0})
        assert response.status_code == 200
        assert response.json()["status"] == "success"
        mock_volume_service.adjust_volume_db.assert_awaited_once()

    def test_adjust_genuine_failure_returns_500(self, test_client, mock_volume_service):
        """A genuine failure (service returns False) surfaces as HTTP 500, not a
        silent no-op — this is the case the deferred path deliberately preserves."""
        mock_volume_service.adjust_volume_db.return_value = False
        response = test_client.post("/api/volume/adjust", json={"delta_db": 2.0})
        assert response.status_code == 500
