# backend/tests/test_volume_api.py
"""
Unit tests for client volume API endpoints.

Tests cover:
- PATCH /api/volume/client/{client_id} - Set client volume
- PATCH /api/volume/client/{client_id}/mute - Set client mute
- GET /api/volume/client/{client_id} - Get client volume state
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.api.models import ClientVolumeRequest, ClientMuteRequest
from backend.api.volume import create_volume_router
from backend.config.constants import DEFAULT_VOLUME_DB


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
# Fixtures
# =============================================================================

@pytest.fixture
def mock_volume_service():
    """Create a mock VolumeService for testing."""
    service = MagicMock()
    service.update_client_volume_db = AsyncMock(return_value=None)
    service.set_client_mute = AsyncMock(return_value=None)
    service.get_client_volume = AsyncMock(return_value={
        "main": -30.0,
        "mute": False
    })
    service.config = MagicMock()
    service.config.config = MagicMock()
    service.config.config.limit_min_db = -80.0
    service.config.config.limit_max_db = 0.0
    return service


@pytest.fixture
def mock_client_registry():
    """Create a mock ClientRegistryService for testing."""
    registry = MagicMock()
    registry.get_client_by_dsp_id = MagicMock(return_value={
        "mac_id": "dca6327ed343",
        "hostname": "milo-client-01",
        "dsp_id": "local",
        "status": "ONLINE"
    })
    return registry


@pytest.fixture
def test_client(mock_volume_service, mock_client_registry):
    """Create a FastAPI test client with mocked services."""
    app = FastAPI()
    router = create_volume_router(mock_volume_service, mock_client_registry)
    app.include_router(router)
    return TestClient(app)


# =============================================================================
# PATCH /api/volume/client/{client_id} Tests
# =============================================================================

class TestSetClientVolumeEndpoint:
    """Tests for PATCH /api/volume/client/{client_id} endpoint."""

    def test_set_volume_online_client(self, test_client, mock_volume_service, mock_client_registry):
        """Test setting volume for an online client."""
        response = test_client.patch(
            "/api/volume/client/local",
            json={"volume_db": -30.0}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["client_id"] == "local"
        assert data["volume_db"] == -30.0

        mock_volume_service.update_client_volume_db.assert_called_once_with("local", -30.0)

    def test_set_volume_offline_client(self, test_client, mock_volume_service, mock_client_registry):
        """Test setting volume for an offline client (should still persist)."""
        mock_client_registry.get_client_by_dsp_id.return_value = {
            "mac_id": "dca6327ed343",
            "hostname": "milo-client-01",
            "dsp_id": "milo-client-01",
            "status": "OFFLINE"
        }

        response = test_client.patch(
            "/api/volume/client/milo-client-01",
            json={"volume_db": -45.0}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        mock_volume_service.update_client_volume_db.assert_called_once_with("milo-client-01", -45.0)

    def test_set_volume_invalid_client_returns_404(self, test_client, mock_client_registry):
        """Test setting volume for non-existent client returns 404."""
        mock_client_registry.get_client_by_dsp_id.return_value = None

        response = test_client.patch(
            "/api/volume/client/unknown-client",
            json={"volume_db": -30.0}
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_set_volume_out_of_range_returns_400(self, test_client, mock_volume_service, mock_client_registry):
        """Test setting volume outside configured limits returns 400."""
        # Set stricter limits
        mock_volume_service.config.config.limit_min_db = -60.0
        mock_volume_service.config.config.limit_max_db = -10.0

        response = test_client.patch(
            "/api/volume/client/local",
            json={"volume_db": -70.0}  # Below min of -60
        )

        assert response.status_code == 400
        assert "out of configured range" in response.json()["detail"]

    def test_set_volume_pydantic_validation_too_low(self, test_client):
        """Test Pydantic validation rejects volume below -80."""
        response = test_client.patch(
            "/api/volume/client/local",
            json={"volume_db": -85.0}
        )

        assert response.status_code == 422  # Pydantic validation error

    def test_set_volume_pydantic_validation_too_high(self, test_client):
        """Test Pydantic validation rejects volume above 0."""
        response = test_client.patch(
            "/api/volume/client/local",
            json={"volume_db": 5.0}
        )

        assert response.status_code == 422  # Pydantic validation error


# =============================================================================
# PATCH /api/volume/client/{client_id}/mute Tests
# =============================================================================

class TestSetClientMuteEndpoint:
    """Tests for PATCH /api/volume/client/{client_id}/mute endpoint."""

    def test_mute_client(self, test_client, mock_volume_service, mock_client_registry):
        """Test muting a client."""
        response = test_client.patch(
            "/api/volume/client/local/mute",
            json={"mute": True}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["client_id"] == "local"
        assert data["mute"] is True

        mock_volume_service.set_client_mute.assert_called_once_with("local", True)

    def test_unmute_client(self, test_client, mock_volume_service, mock_client_registry):
        """Test unmuting a client."""
        response = test_client.patch(
            "/api/volume/client/local/mute",
            json={"mute": False}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["mute"] is False

        mock_volume_service.set_client_mute.assert_called_once_with("local", False)

    def test_mute_invalid_client_returns_404(self, test_client, mock_client_registry):
        """Test muting non-existent client returns 404."""
        mock_client_registry.get_client_by_dsp_id.return_value = None

        response = test_client.patch(
            "/api/volume/client/unknown-client/mute",
            json={"mute": True}
        )

        assert response.status_code == 404


# =============================================================================
# GET /api/volume/client/{client_id} Tests
# =============================================================================

class TestGetClientVolumeEndpoint:
    """Tests for GET /api/volume/client/{client_id} endpoint."""

    def test_get_volume_returns_state(self, test_client, mock_volume_service, mock_client_registry):
        """Test getting client volume returns volume and mute state."""
        response = test_client.get("/api/volume/client/local")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["client_id"] == "local"
        assert data["volume_db"] == -30.0
        assert data["mute"] is False
        assert data["online"] is True

        mock_volume_service.get_client_volume.assert_called_once_with("local")

    def test_get_volume_offline_client(self, test_client, mock_volume_service, mock_client_registry):
        """Test getting volume for offline client shows online=False."""
        mock_client_registry.get_client_by_dsp_id.return_value = {
            "mac_id": "dca6327ed343",
            "hostname": "milo-client-01",
            "dsp_id": "milo-client-01",
            "status": "OFFLINE"
        }

        response = test_client.get("/api/volume/client/milo-client-01")

        assert response.status_code == 200
        data = response.json()
        assert data["online"] is False

    def test_get_volume_invalid_client_returns_404(self, test_client, mock_client_registry):
        """Test getting volume for non-existent client returns 404."""
        mock_client_registry.get_client_by_dsp_id.return_value = None

        response = test_client.get("/api/volume/client/unknown-client")

        assert response.status_code == 404

    def test_get_volume_unknown_client_returns_defaults(self, test_client, mock_volume_service, mock_client_registry):
        """Test getting volume for unknown client returns defaults from VolumeService."""
        mock_volume_service.get_client_volume.return_value = {
            "main": DEFAULT_VOLUME_DB,
            "mute": False
        }

        response = test_client.get("/api/volume/client/local")

        assert response.status_code == 200
        data = response.json()
        assert data["volume_db"] == DEFAULT_VOLUME_DB
        assert data["mute"] is False


# =============================================================================
# Edge Cases and Error Handling
# =============================================================================

class TestVolumeApiErrorHandling:
    """Tests for error handling in volume API."""

    def test_service_error_returns_500(self, test_client, mock_volume_service, mock_client_registry):
        """Test that service errors return 500."""
        mock_volume_service.update_client_volume_db.side_effect = Exception("DSP connection failed")

        response = test_client.patch(
            "/api/volume/client/local",
            json={"volume_db": -30.0}
        )

        assert response.status_code == 500
        assert "DSP connection failed" in response.json()["detail"]

    def test_get_volume_service_error(self, test_client, mock_volume_service, mock_client_registry):
        """Test that get volume service errors return 500."""
        mock_volume_service.get_client_volume.side_effect = Exception("State store error")

        response = test_client.get("/api/volume/client/local")

        assert response.status_code == 500
        assert "State store error" in response.json()["detail"]

    def test_mute_service_error(self, test_client, mock_volume_service, mock_client_registry):
        """Test that mute service errors return 500."""
        mock_volume_service.set_client_mute.side_effect = Exception("Mute failed")

        response = test_client.patch(
            "/api/volume/client/local/mute",
            json={"mute": True}
        )

        assert response.status_code == 500
        assert "Mute failed" in response.json()["detail"]


# =============================================================================
# Fallback Mode (No Registry)
# =============================================================================

class TestVolumeApiWithoutRegistry:
    """Tests for volume API when client_registry_service is None."""

    @pytest.fixture
    def test_client_no_registry(self, mock_volume_service):
        """Create a test client without registry service."""
        app = FastAPI()
        router = create_volume_router(mock_volume_service, client_registry_service=None)
        app.include_router(router)
        return TestClient(app)

    def test_set_volume_without_registry(self, test_client_no_registry, mock_volume_service):
        """Test setting volume works without registry (fallback mode)."""
        response = test_client_no_registry.patch(
            "/api/volume/client/any-client",
            json={"volume_db": -30.0}
        )

        assert response.status_code == 200
        mock_volume_service.update_client_volume_db.assert_called_once_with("any-client", -30.0)

    def test_get_volume_without_registry(self, test_client_no_registry, mock_volume_service):
        """Test getting volume works without registry (fallback mode)."""
        response = test_client_no_registry.get("/api/volume/client/any-client")

        assert response.status_code == 200
        data = response.json()
        # Without registry, online defaults to True
        assert data["online"] is True


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
        service.config.config = MagicMock()
        service.config.config.limit_min_db = -80.0
        service.config.config.limit_max_db = 0.0
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
        mock_volume_service.config.config.limit_min_db = -60.0
        mock_volume_service.config.config.limit_max_db = -10.0

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


class TestVolumeSettings:
    """Tests for volume settings endpoints (AC4, AC5)."""

    @pytest.fixture
    def mock_volume_service(self):
        """Create a mock VolumeService for settings tests."""
        service = MagicMock()
        service.config = MagicMock()
        service.config.config = MagicMock()
        service.config.config.startup_volume_db = -60.0
        service.config.config.restore_last_volume = False
        service.reload_startup_config = AsyncMock(return_value=True)
        return service

    @pytest.fixture
    def mock_settings_service(self):
        """Create a mock SettingsService."""
        service = MagicMock()
        service.get_setting = AsyncMock(return_value=-60.0)
        service.set_setting = AsyncMock(return_value=True)
        return service

    @pytest.fixture
    def test_client(self, mock_volume_service, mock_settings_service):
        """Create a FastAPI test client with mocked services."""
        app = FastAPI()
        router = create_volume_router(
            mock_volume_service,
            client_registry_service=None,
            settings_service=mock_settings_service
        )
        app.include_router(router)
        return TestClient(app)

    def test_get_volume_settings(self, test_client, mock_volume_service):
        """AC4: Test GET volume settings returns startup_volume_db and restore_last_volume."""
        response = test_client.get("/api/volume/settings")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["startup_volume_db"] == -60.0
        assert data["restore_last_volume"] is False

    def test_patch_volume_settings_startup(self, test_client, mock_volume_service, mock_settings_service):
        """AC5: Test PATCH volume settings updates startup_volume_db."""
        response = test_client.patch(
            "/api/volume/settings",
            json={"startup_volume_db": -30.0}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"

        mock_settings_service.set_setting.assert_called()
        mock_volume_service.reload_startup_config.assert_called_once()

    def test_patch_volume_settings_restore(self, test_client, mock_volume_service, mock_settings_service):
        """Test PATCH volume settings updates restore_last_volume."""
        response = test_client.patch(
            "/api/volume/settings",
            json={"restore_last_volume": True}
        )

        assert response.status_code == 200
        mock_settings_service.set_setting.assert_called()

    def test_patch_volume_settings_both(self, test_client, mock_volume_service, mock_settings_service):
        """Test PATCH volume settings updates both fields."""
        response = test_client.patch(
            "/api/volume/settings",
            json={"startup_volume_db": -45.0, "restore_last_volume": True}
        )

        assert response.status_code == 200
        # Should call set_setting twice (once for each field)
        assert mock_settings_service.set_setting.call_count == 2
