# backend/tests/test_api_multiroom.py
"""
Unit tests for /api/multiroom/ API endpoints.

Tests cover:
- GET /api/multiroom/clients (AC1: returns all clients with online status)
- GET /api/multiroom/clients/{mac_id} (AC4: 404 for non-existent)
- PATCH /api/multiroom/clients/{mac_id} (AC2: update name, AC3: update speaker_type)
- Validation (AC4: 400 for invalid speaker_type)
- PUT alias for PATCH (backward compatibility)
"""
import pytest
from unittest.mock import Mock, AsyncMock
from fastapi.testclient import TestClient
from fastapi import FastAPI
from pydantic import ValidationError

from backend.api.multiroom import create_multiroom_router
from backend.api.models import ZoneCreate, ZoneUpdate, ZoneResponse, MAX_ZONE_NAME_LENGTH
from backend.core.multiroom.models import Client


@pytest.fixture
def mock_registry_service():
    """Create a mock ClientRegistryService."""
    service = Mock()

    # Default client for tests
    test_client = Client(
        mac_id="dc:a6:32:7e:d3:43",
        name="Living Room",
        ip="192.168.1.100",
        online=True,
        zone_id=None,
        volume_db=-30.0,
        mute=False,
        speaker_type="bookshelf"
    )

    # Secondary client
    local_client = Client(
        mac_id="local",
        name="Main",
        ip="127.0.0.1",
        online=True,
        zone_id=None,
        volume_db=-25.0,
        mute=False,
        speaker_type="tower"
    )

    # Configure mock methods
    service.get_all_clients = Mock(return_value={
        "dc:a6:32:7e:d3:43": test_client,
        "local": local_client
    })

    service.get_client = Mock(
        side_effect=lambda mac_id: test_client if mac_id == "dc:a6:32:7e:d3:43" else None
    )

    # Return updated client with new values
    def mock_update_client(mac_id, name=None, speaker_type=None):
        if mac_id != "dc:a6:32:7e:d3:43":
            return None
        updated = Client(
            mac_id=test_client.mac_id,
            name=name if name else test_client.name,
            ip=test_client.ip,
            online=test_client.online,
            zone_id=test_client.zone_id,
            volume_db=test_client.volume_db,
            mute=test_client.mute,
            speaker_type=speaker_type if speaker_type else test_client.speaker_type
        )
        return updated

    service.update_client = AsyncMock(side_effect=mock_update_client)

    return service


@pytest.fixture
def client(mock_registry_service):
    """Create a test client with the multiroom router."""
    app = FastAPI()
    router = create_multiroom_router(mock_registry_service)
    app.include_router(router)
    return TestClient(app)


class TestGetClients:
    """Tests for GET /api/multiroom/clients endpoint (AC1)."""

    def test_get_clients_returns_all_clients(self, client, mock_registry_service):
        """AC1: GET /clients returns all registered clients with online status."""
        response = client.get("/api/multiroom/clients")

        assert response.status_code == 200
        data = response.json()
        assert "clients" in data
        assert len(data["clients"]) == 2

        # Verify client data includes online status
        client_macs = {c["mac_id"]: c for c in data["clients"]}
        assert "dc:a6:32:7e:d3:43" in client_macs
        assert "local" in client_macs
        assert client_macs["dc:a6:32:7e:d3:43"]["online"] is True
        assert client_macs["local"]["online"] is True

    def test_get_clients_empty_registry(self, client, mock_registry_service):
        """GET /clients with no clients returns empty list."""
        mock_registry_service.get_all_clients.return_value = {}

        response = client.get("/api/multiroom/clients")

        assert response.status_code == 200
        assert response.json() == {"clients": []}

    def test_get_clients_response_format(self, client, mock_registry_service):
        """AC1: Response format is {"clients": [...]}."""
        response = client.get("/api/multiroom/clients")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)
        assert "clients" in data
        assert isinstance(data["clients"], list)


class TestGetClientById:
    """Tests for GET /api/multiroom/clients/{mac_id} endpoint."""

    def test_get_client_by_mac_id_success(self, client, mock_registry_service):
        """GET /clients/{mac_id} returns client with online status."""
        response = client.get("/api/multiroom/clients/dc:a6:32:7e:d3:43")

        assert response.status_code == 200
        data = response.json()
        assert data["mac_id"] == "dc:a6:32:7e:d3:43"
        assert data["name"] == "Living Room"
        assert data["online"] is True
        assert data["speaker_type"] == "bookshelf"

    def test_get_client_not_found(self, client, mock_registry_service):
        """AC4: GET /clients/{mac_id} returns 404 with meaningful message."""
        response = client.get("/api/multiroom/clients/unknown-client")

        assert response.status_code == 404
        detail = response.json()["detail"]
        assert "not found" in detail.lower()
        assert "unknown-client" in detail


class TestPatchClient:
    """Tests for PATCH /api/multiroom/clients/{mac_id} endpoint (AC2, AC3)."""

    def test_patch_client_name_success(self, client, mock_registry_service):
        """AC2: PATCH /clients/{mac_id} updates client name."""
        response = client.patch(
            "/api/multiroom/clients/dc:a6:32:7e:d3:43",
            json={"name": "Kitchen Speaker"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "client" in data
        assert data["client"]["name"] == "Kitchen Speaker"

        # Verify service was called correctly
        mock_registry_service.update_client.assert_called_once_with(
            "dc:a6:32:7e:d3:43",
            name="Kitchen Speaker",
            speaker_type=None
        )

    def test_patch_client_speaker_type_success(self, client, mock_registry_service):
        """AC3: PATCH /clients/{mac_id} updates speaker_type."""
        response = client.patch(
            "/api/multiroom/clients/dc:a6:32:7e:d3:43",
            json={"speaker_type": "subwoofer"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["client"]["speaker_type"] == "subwoofer"

        mock_registry_service.update_client.assert_called_once_with(
            "dc:a6:32:7e:d3:43",
            name=None,
            speaker_type="subwoofer"
        )

    def test_patch_client_both_fields(self, client, mock_registry_service):
        """PATCH /clients/{mac_id} updates both name and speaker_type."""
        response = client.patch(
            "/api/multiroom/clients/dc:a6:32:7e:d3:43",
            json={"name": "Bedroom", "speaker_type": "satellite"}
        )

        assert response.status_code == 200
        mock_registry_service.update_client.assert_called_once_with(
            "dc:a6:32:7e:d3:43",
            name="Bedroom",
            speaker_type="satellite"
        )

    def test_patch_client_not_found(self, client, mock_registry_service):
        """AC4: PATCH /clients/{mac_id} returns 404 for unknown client."""
        response = client.patch(
            "/api/multiroom/clients/unknown-client",
            json={"name": "Test"}
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_patch_client_invalid_speaker_type(self, client, mock_registry_service):
        """AC3/AC4: PATCH with invalid speaker_type returns 400 with allowed values."""
        response = client.patch(
            "/api/multiroom/clients/dc:a6:32:7e:d3:43",
            json={"speaker_type": "invalid_type"}
        )

        assert response.status_code == 422  # Pydantic validation error
        detail = response.json()["detail"]
        # Pydantic returns validation errors in detail array
        assert len(detail) > 0

    def test_patch_client_returns_online_status(self, client, mock_registry_service):
        """AC1: Updated client response includes runtime 'online' status."""
        response = client.patch(
            "/api/multiroom/clients/dc:a6:32:7e:d3:43",
            json={"name": "Updated"}
        )

        assert response.status_code == 200
        assert "online" in response.json()["client"]


class TestPutClientAlias:
    """Tests for PUT /api/multiroom/clients/{mac_id} endpoint (backward compatibility)."""

    def test_put_client_works_same_as_patch(self, client, mock_registry_service):
        """PUT /clients/{mac_id} behaves identically to PATCH."""
        response = client.put(
            "/api/multiroom/clients/dc:a6:32:7e:d3:43",
            json={"name": "Via PUT"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["client"]["name"] == "Via PUT"


class TestSpeakerTypeValidation:
    """Tests for speaker_type validation (AC3)."""

    @pytest.mark.parametrize("speaker_type", [
        "satellite",
        "bookshelf",
        "tower",
        "subwoofer"
    ])
    def test_valid_speaker_types(self, client, mock_registry_service, speaker_type):
        """AC3: Valid speaker_types are accepted."""
        response = client.patch(
            "/api/multiroom/clients/dc:a6:32:7e:d3:43",
            json={"speaker_type": speaker_type}
        )

        assert response.status_code == 200
        assert response.json()["client"]["speaker_type"] == speaker_type


# =============================================================================
# Zone Pydantic Model Tests (Story 2-1)
# =============================================================================


class TestZoneCreate:
    """Tests for ZoneCreate Pydantic model validation."""

    def test_valid_zone_create(self):
        """Valid ZoneCreate with name and 2 clients."""
        zone = ZoneCreate(
            name="Living Room",
            client_ids=["local", "dc:a6:32:7e:d3:43"]
        )
        assert zone.name == "Living Room"
        assert len(zone.client_ids) == 2

    def test_zone_create_name_max_length(self):
        """Name must not exceed MAX_ZONE_NAME_LENGTH (15) characters."""
        # Exactly 15 chars should work
        zone = ZoneCreate(
            name="A" * MAX_ZONE_NAME_LENGTH,
            client_ids=["c1", "c2"]
        )
        assert len(zone.name) == MAX_ZONE_NAME_LENGTH

        # 16 chars should fail
        with pytest.raises(ValidationError) as exc_info:
            ZoneCreate(
                name="A" * (MAX_ZONE_NAME_LENGTH + 1),
                client_ids=["c1", "c2"]
            )
        assert "max_length" in str(exc_info.value).lower() or "at most" in str(exc_info.value).lower()

    def test_zone_create_name_min_length(self):
        """Name must have at least 1 character."""
        with pytest.raises(ValidationError):
            ZoneCreate(name="", client_ids=["c1", "c2"])

    def test_zone_create_name_stripped(self):
        """Name is stripped of whitespace."""
        zone = ZoneCreate(
            name="  Kitchen  ",
            client_ids=["c1", "c2"]
        )
        assert zone.name == "Kitchen"

    def test_zone_create_min_two_clients(self):
        """At least 2 clients are required."""
        with pytest.raises(ValidationError) as exc_info:
            ZoneCreate(name="Test", client_ids=["single"])
        assert "2" in str(exc_info.value)

    def test_zone_create_empty_client_ids(self):
        """Empty client_ids list fails."""
        with pytest.raises(ValidationError):
            ZoneCreate(name="Test", client_ids=[])

    def test_zone_create_deduplicates_client_ids(self):
        """Duplicate client_ids are removed."""
        zone = ZoneCreate(
            name="Test",
            client_ids=["c1", "c1", "c2", "c2", "c3"]
        )
        # Should have 3 unique clients
        assert len(zone.client_ids) == 3
        assert set(zone.client_ids) == {"c1", "c2", "c3"}

    def test_zone_create_duplicate_only_fails(self):
        """If deduplication results in < 2 clients, validation fails."""
        with pytest.raises(ValidationError) as exc_info:
            ZoneCreate(
                name="Test",
                client_ids=["c1", "c1", "c1"]  # All same = 1 unique
            )
        assert "2" in str(exc_info.value)

    def test_zone_create_strips_client_ids(self):
        """Client IDs are stripped of whitespace."""
        zone = ZoneCreate(
            name="Test",
            client_ids=["  c1  ", " c2 "]
        )
        assert zone.client_ids == ["c1", "c2"]

    def test_zone_create_removes_empty_client_ids(self):
        """Empty strings in client_ids are removed."""
        zone = ZoneCreate(
            name="Test",
            client_ids=["c1", "", "  ", "c2"]
        )
        assert zone.client_ids == ["c1", "c2"]

    def test_zone_create_name_rejects_special_chars(self):
        """Name with special characters (XSS prevention) is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            ZoneCreate(
                name="<script>alert",
                client_ids=["c1", "c2"]
            )
        assert "letters" in str(exc_info.value).lower() or "name" in str(exc_info.value).lower()

    def test_zone_create_name_accepts_accented_chars(self):
        """Name with accented characters (French) is accepted."""
        zone = ZoneCreate(
            name="Séjour élégant",
            client_ids=["c1", "c2"]
        )
        assert zone.name == "Séjour élégant"

    def test_zone_create_name_accepts_hyphens(self):
        """Name with hyphens is accepted."""
        zone = ZoneCreate(
            name="Living-Room",
            client_ids=["c1", "c2"]
        )
        assert zone.name == "Living-Room"


class TestZoneUpdate:
    """Tests for ZoneUpdate Pydantic model validation."""

    def test_valid_zone_update_with_name(self):
        """Valid ZoneUpdate with name."""
        update = ZoneUpdate(name="New Name")
        assert update.name == "New Name"

    def test_zone_update_name_optional(self):
        """Name is optional (for partial updates)."""
        update = ZoneUpdate()
        assert update.name is None

    def test_zone_update_name_max_length(self):
        """Name must not exceed MAX_ZONE_NAME_LENGTH characters."""
        with pytest.raises(ValidationError):
            ZoneUpdate(name="A" * (MAX_ZONE_NAME_LENGTH + 1))

    def test_zone_update_name_min_length(self):
        """If name is provided, it must have at least 1 character."""
        with pytest.raises(ValidationError):
            ZoneUpdate(name="")

    def test_zone_update_name_stripped(self):
        """Name is stripped of whitespace."""
        update = ZoneUpdate(name="  Bedroom  ")
        assert update.name == "Bedroom"

    def test_zone_update_none_name_not_stripped(self):
        """None name stays None (not stripped)."""
        update = ZoneUpdate(name=None)
        assert update.name is None

    def test_zone_update_name_rejects_special_chars(self):
        """Name with special characters (XSS prevention) is rejected."""
        with pytest.raises(ValidationError):
            ZoneUpdate(name="<script>")

    def test_zone_update_name_accepts_accented_chars(self):
        """Name with accented characters (French) is accepted."""
        update = ZoneUpdate(name="Château")
        assert update.name == "Château"


class TestZoneResponse:
    """Tests for ZoneResponse Pydantic model."""

    def test_valid_zone_response(self):
        """Valid ZoneResponse with all fields."""
        response = ZoneResponse(
            id="550e8400-e29b-41d4-a716-446655440000",
            name="Living Room",
            client_ids=["local", "dc:a6:32:7e:d3:43"],
            dsp_settings={"filters": [], "compressor": None, "loudness": None}
        )
        assert response.id == "550e8400-e29b-41d4-a716-446655440000"
        assert response.name == "Living Room"
        assert len(response.client_ids) == 2
        assert response.dsp_settings["filters"] == []

    def test_zone_response_from_dict(self):
        """ZoneResponse can be created from dict."""
        data = {
            "id": "zone-1",
            "name": "Kitchen",
            "client_ids": ["c1", "c2"],
            "dsp_settings": {"filters": [{"freq": 1000}]}
        }
        response = ZoneResponse(**data)
        assert response.id == "zone-1"
        assert response.dsp_settings["filters"][0]["freq"] == 1000


class TestMaxZoneNameLengthConstant:
    """Tests for MAX_ZONE_NAME_LENGTH constant."""

    def test_max_zone_name_length_value(self):
        """MAX_ZONE_NAME_LENGTH is 15."""
        assert MAX_ZONE_NAME_LENGTH == 15

    def test_max_zone_name_length_matches_domain_model(self):
        """API constant matches domain model constant."""
        from backend.core.multiroom.models import MAX_ZONE_NAME_LENGTH as DOMAIN_CONSTANT
        assert MAX_ZONE_NAME_LENGTH == DOMAIN_CONSTANT


# =============================================================================
# Zone API Endpoint Tests (Story 2-2)
# =============================================================================


@pytest.fixture
def mock_zone_registry_service():
    """Create a mock ClientRegistryService with zone support."""
    from backend.core.multiroom.models import Zone, DspSettings

    service = Mock()

    # Test clients for zone operations
    client1 = Client(
        mac_id="local",
        name="Main",
        ip="127.0.0.1",
        online=True,
        zone_id=None,
        volume_db=-30.0,
        mute=False,
        speaker_type="tower"
    )
    client2 = Client(
        mac_id="dc:a6:32:7e:d3:43",
        name="Living Room",
        ip="192.168.1.100",
        online=True,
        zone_id=None,
        volume_db=-30.0,
        mute=False,
        speaker_type="bookshelf"
    )
    client3 = Client(
        mac_id="aa:bb:cc:dd:ee:ff",
        name="Subwoofer",
        ip="192.168.1.101",
        online=False,
        zone_id=None,
        volume_db=-30.0,
        mute=False,
        speaker_type="subwoofer"
    )

    # Test zone
    test_zone = Zone(
        id="zone-test-123",
        name="Living Room",
        client_ids=["local", "dc:a6:32:7e:d3:43"],
        dsp_settings=DspSettings.default()
    )

    service._clients = {
        "local": client1,
        "dc:a6:32:7e:d3:43": client2,
        "aa:bb:cc:dd:ee:ff": client3
    }

    service._zones = {
        "zone-test-123": test_zone
    }

    # Configure mock methods
    service.get_all_clients = Mock(return_value=service._clients)
    service.get_client = Mock(side_effect=lambda mac_id: service._clients.get(mac_id))
    service.get_all_zones = Mock(return_value=service._zones)
    service.get_zone = Mock(side_effect=lambda zone_id: service._zones.get(zone_id))

    def zone_to_enriched_dict(zone):
        """Mock enriched zone dict."""
        enriched = zone.to_dict()
        online_count = sum(
            1 for cid in zone.client_ids
            if cid in service._clients and service._clients[cid].online
        )
        has_sub = any(
            service._clients[cid].speaker_type == 'subwoofer'
            for cid in zone.client_ids
            if cid in service._clients
        )
        enriched['online_client_count'] = online_count
        enriched['has_subwoofer'] = has_sub
        enriched['crossover_enabled'] = has_sub and online_count > 0
        return enriched

    service.zone_to_enriched_dict = Mock(side_effect=zone_to_enriched_dict)

    async def mock_create_zone(zone_id, name, client_ids, dsp_settings=None):
        if len(client_ids) < 2:
            raise ValueError("Zone requires at least 2 clients")
        for cid in client_ids:
            if cid not in service._clients:
                raise ValueError(f"Client {cid} not found")
        zone = Zone(
            id=zone_id,
            name=name,
            client_ids=client_ids,
            dsp_settings=dsp_settings or DspSettings.default()
        )
        service._zones[zone_id] = zone
        return zone

    service.create_zone = AsyncMock(side_effect=mock_create_zone)

    async def mock_update_zone(zone_id, name=None):
        zone = service._zones.get(zone_id)
        if not zone:
            return None
        if name is not None:
            zone.name = name
        return zone

    service.update_zone = AsyncMock(side_effect=mock_update_zone)

    async def mock_delete_zone(zone_id):
        if zone_id not in service._zones:
            return False
        del service._zones[zone_id]
        return True

    service.delete_zone = AsyncMock(side_effect=mock_delete_zone)

    # Client update (needed by router)
    def mock_update_client(mac_id, name=None, speaker_type=None):
        client = service._clients.get(mac_id)
        if not client:
            return None
        if name is not None:
            client.name = name
        if speaker_type is not None:
            client.speaker_type = speaker_type
        return client

    service.update_client = AsyncMock(side_effect=mock_update_client)

    return service


@pytest.fixture
def zone_client(mock_zone_registry_service):
    """Create a test client with zone-enabled multiroom router."""
    app = FastAPI()
    router = create_multiroom_router(mock_zone_registry_service)
    app.include_router(router)
    return TestClient(app)


class TestGetZones:
    """Tests for GET /api/multiroom/zones endpoint (AC5)."""

    def test_get_zones_returns_all_zones(self, zone_client, mock_zone_registry_service):
        """AC5: GET /zones returns all zones with enriched data."""
        response = zone_client.get("/api/multiroom/zones")

        assert response.status_code == 200
        data = response.json()
        assert "zones" in data
        assert len(data["zones"]) == 1

        zone = data["zones"][0]
        assert zone["id"] == "zone-test-123"
        assert zone["name"] == "Living Room"
        assert len(zone["client_ids"]) == 2

    def test_get_zones_includes_enriched_fields(self, zone_client, mock_zone_registry_service):
        """AC5: Zone response includes computed fields (online_client_count, has_subwoofer, crossover_enabled)."""
        response = zone_client.get("/api/multiroom/zones")

        assert response.status_code == 200
        zone = response.json()["zones"][0]

        # Both clients are online (local and dc:a6:32:7e:d3:43)
        assert zone["online_client_count"] == 2
        assert zone["has_subwoofer"] is False
        assert zone["crossover_enabled"] is False

    def test_get_zones_empty_returns_empty_list(self, zone_client, mock_zone_registry_service):
        """GET /zones with no zones returns empty list."""
        mock_zone_registry_service.get_all_zones.return_value = {}

        response = zone_client.get("/api/multiroom/zones")

        assert response.status_code == 200
        assert response.json() == {"zones": []}


class TestGetZoneById:
    """Tests for GET /api/multiroom/zones/{zone_id} endpoint (AC5)."""

    def test_get_zone_by_id_success(self, zone_client, mock_zone_registry_service):
        """AC5: GET /zones/{zone_id} returns zone with enriched data."""
        response = zone_client.get("/api/multiroom/zones/zone-test-123")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "zone-test-123"
        assert data["name"] == "Living Room"
        assert "online_client_count" in data
        assert "has_subwoofer" in data
        assert "crossover_enabled" in data

    def test_get_zone_not_found(self, zone_client, mock_zone_registry_service):
        """AC5: GET /zones/{zone_id} returns 404 with meaningful message."""
        response = zone_client.get("/api/multiroom/zones/nonexistent-zone")

        assert response.status_code == 404
        detail = response.json()["detail"]
        assert "not found" in detail.lower()
        assert "nonexistent-zone" in detail


class TestCreateZone:
    """Tests for POST /api/multiroom/zones endpoint (AC2, AC3, AC5)."""

    def test_create_zone_success(self, zone_client, mock_zone_registry_service):
        """AC2/AC5: POST /zones creates zone with valid 2+ clients."""
        response = zone_client.post(
            "/api/multiroom/zones",
            json={
                "name": "Kitchen",
                "client_ids": ["local", "aa:bb:cc:dd:ee:ff"]
            }
        )

        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "success"
        assert "zone" in data
        assert data["zone"]["name"] == "Kitchen"
        assert len(data["zone"]["client_ids"]) == 2

    def test_create_zone_includes_enriched_fields(self, zone_client, mock_zone_registry_service):
        """AC2: Created zone response includes computed fields."""
        response = zone_client.post(
            "/api/multiroom/zones",
            json={
                "name": "New Zone",
                "client_ids": ["local", "dc:a6:32:7e:d3:43"]
            }
        )

        assert response.status_code == 201
        zone = response.json()["zone"]
        assert "online_client_count" in zone
        assert "has_subwoofer" in zone
        assert "crossover_enabled" in zone

    def test_create_zone_generates_uuid(self, zone_client, mock_zone_registry_service):
        """AC5: Zone creation generates UUID for zone_id."""
        response = zone_client.post(
            "/api/multiroom/zones",
            json={
                "name": "Test",
                "client_ids": ["local", "dc:a6:32:7e:d3:43"]
            }
        )

        assert response.status_code == 201
        zone_id = response.json()["zone"]["id"]
        # UUID format check (basic)
        assert len(zone_id) == 36
        assert zone_id.count("-") == 4

    def test_create_zone_less_than_2_clients_fails(self, zone_client, mock_zone_registry_service):
        """AC3: POST /zones with < 2 clients returns 400."""
        response = zone_client.post(
            "/api/multiroom/zones",
            json={
                "name": "Invalid",
                "client_ids": ["local"]
            }
        )

        assert response.status_code == 422  # Pydantic validation error
        # The validation happens at Pydantic level

    def test_create_zone_client_not_found_fails(self, zone_client, mock_zone_registry_service):
        """AC3: POST /zones with unknown client returns 400."""
        response = zone_client.post(
            "/api/multiroom/zones",
            json={
                "name": "Invalid",
                "client_ids": ["local", "nonexistent-client"]
            }
        )

        assert response.status_code == 400
        assert "not found" in response.json()["detail"].lower()

    def test_create_zone_name_max_length_enforced(self, zone_client, mock_zone_registry_service):
        """AC3: Zone name max length (15) is enforced."""
        response = zone_client.post(
            "/api/multiroom/zones",
            json={
                "name": "A" * 16,  # 16 chars, exceeds max
                "client_ids": ["local", "dc:a6:32:7e:d3:43"]
            }
        )

        assert response.status_code == 422  # Pydantic validation error


class TestUpdateZone:
    """Tests for PATCH /api/multiroom/zones/{zone_id} endpoint (AC5)."""

    def test_update_zone_name_success(self, zone_client, mock_zone_registry_service):
        """AC5: PATCH /zones/{zone_id} updates zone name."""
        response = zone_client.patch(
            "/api/multiroom/zones/zone-test-123",
            json={"name": "New Name"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["zone"]["name"] == "New Name"

    def test_update_zone_includes_enriched_fields(self, zone_client, mock_zone_registry_service):
        """AC5: Updated zone response includes computed fields."""
        response = zone_client.patch(
            "/api/multiroom/zones/zone-test-123",
            json={"name": "Updated"}
        )

        assert response.status_code == 200
        zone = response.json()["zone"]
        assert "online_client_count" in zone
        assert "has_subwoofer" in zone
        assert "crossover_enabled" in zone

    def test_update_zone_not_found(self, zone_client, mock_zone_registry_service):
        """AC5: PATCH /zones/{zone_id} returns 404 for unknown zone."""
        response = zone_client.patch(
            "/api/multiroom/zones/nonexistent-zone",
            json={"name": "Test"}
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_update_zone_empty_request(self, zone_client, mock_zone_registry_service):
        """PATCH /zones/{zone_id} with empty body still succeeds (no-op)."""
        response = zone_client.patch(
            "/api/multiroom/zones/zone-test-123",
            json={}
        )

        assert response.status_code == 200
        # Name unchanged
        assert response.json()["zone"]["name"] == "Living Room"


class TestDeleteZone:
    """Tests for DELETE /api/multiroom/zones/{zone_id} endpoint (AC4, AC5)."""

    def test_delete_zone_success(self, zone_client, mock_zone_registry_service):
        """AC4/AC5: DELETE /zones/{zone_id} deletes zone and returns success."""
        response = zone_client.delete("/api/multiroom/zones/zone-test-123")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "deleted" in data["message"].lower()
        assert "zone-test-123" in data["message"]

    def test_delete_zone_not_found(self, zone_client, mock_zone_registry_service):
        """AC5: DELETE /zones/{zone_id} returns 404 for unknown zone."""
        response = zone_client.delete("/api/multiroom/zones/nonexistent-zone")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_delete_zone_removes_from_registry(self, zone_client, mock_zone_registry_service):
        """AC4: Zone is actually removed from registry after DELETE."""
        # First delete
        response = zone_client.delete("/api/multiroom/zones/zone-test-123")
        assert response.status_code == 200

        # Verify service was called
        mock_zone_registry_service.delete_zone.assert_called_once_with("zone-test-123")


# =============================================================================
# Zone Client Membership Tests (Story 2-3)
# =============================================================================


@pytest.fixture
def mock_membership_registry_service():
    """Create a mock ClientRegistryService with zone membership support."""
    from backend.core.multiroom.models import Zone, DspSettings

    service = Mock()

    # Test clients
    client1 = Client(
        mac_id="local",
        name="Main",
        ip="127.0.0.1",
        online=True,
        zone_id="zone-test-123",
        volume_db=-30.0,
        mute=False,
        speaker_type="tower"
    )
    client2 = Client(
        mac_id="dc:a6:32:7e:d3:43",
        name="Living Room",
        ip="192.168.1.100",
        online=True,
        zone_id="zone-test-123",
        volume_db=-30.0,
        mute=False,
        speaker_type="bookshelf"
    )
    client3 = Client(
        mac_id="aa:bb:cc:dd:ee:ff",
        name="Bedroom",
        ip="192.168.1.101",
        online=True,
        zone_id=None,  # Standalone client
        volume_db=-30.0,
        mute=False,
        speaker_type="satellite"
    )

    # Test zone with 2 clients
    test_zone = Zone(
        id="zone-test-123",
        name="Living Room",
        client_ids=["local", "dc:a6:32:7e:d3:43"],
        dsp_settings=DspSettings.default()
    )

    service._clients = {
        "local": client1,
        "dc:a6:32:7e:d3:43": client2,
        "aa:bb:cc:dd:ee:ff": client3
    }

    service._zones = {
        "zone-test-123": test_zone
    }

    # Configure mock methods
    service.get_all_clients = Mock(return_value=service._clients)
    service.get_client = Mock(side_effect=lambda mac_id: service._clients.get(mac_id))
    service.get_all_zones = Mock(return_value=service._zones)
    service.get_zone = Mock(side_effect=lambda zone_id: service._zones.get(zone_id))

    def zone_to_enriched_dict(zone):
        """Mock enriched zone dict."""
        enriched = zone.to_dict()
        online_count = sum(
            1 for cid in zone.client_ids
            if cid in service._clients and service._clients[cid].online
        )
        has_sub = any(
            service._clients[cid].speaker_type == 'subwoofer'
            for cid in zone.client_ids
            if cid in service._clients
        )
        enriched['online_client_count'] = online_count
        enriched['has_subwoofer'] = has_sub
        enriched['crossover_enabled'] = has_sub and online_count > 0
        return enriched

    service.zone_to_enriched_dict = Mock(side_effect=zone_to_enriched_dict)

    async def mock_add_client_to_zone(zone_id, mac_id):
        zone = service._zones.get(zone_id)
        if not zone:
            return False
        client = service._clients.get(mac_id)
        if not client:
            return False
        if mac_id in zone.client_ids:
            return False  # Already in zone
        zone.client_ids.append(mac_id)
        client.zone_id = zone_id
        return True

    service.add_client_to_zone = AsyncMock(side_effect=mock_add_client_to_zone)

    async def mock_remove_client_from_zone(zone_id, mac_id):
        zone = service._zones.get(zone_id)
        if not zone:
            return False
        if mac_id not in zone.client_ids:
            return False
        zone.client_ids.remove(mac_id)
        client = service._clients.get(mac_id)
        if client:
            client.zone_id = None
        # If zone < 2 clients, delete it
        if len(zone.client_ids) < 2:
            del service._zones[zone_id]
        return True

    service.remove_client_from_zone = AsyncMock(side_effect=mock_remove_client_from_zone)

    # Client update (needed by router)
    def mock_update_client(mac_id, name=None, speaker_type=None):
        client = service._clients.get(mac_id)
        if not client:
            return None
        if name is not None:
            client.name = name
        if speaker_type is not None:
            client.speaker_type = speaker_type
        return client

    service.update_client = AsyncMock(side_effect=mock_update_client)

    return service


@pytest.fixture
def membership_client(mock_membership_registry_service):
    """Create a test client with zone membership-enabled multiroom router."""
    app = FastAPI()
    router = create_multiroom_router(mock_membership_registry_service)
    app.include_router(router)
    return TestClient(app)


class TestAddClientToZone:
    """Tests for POST /api/multiroom/zones/{zone_id}/clients endpoint (AC1, AC4)."""

    def test_add_client_to_zone_success(self, membership_client, mock_membership_registry_service):
        """AC1/AC4: POST /zones/{zone_id}/clients adds client to zone."""
        response = membership_client.post(
            "/api/multiroom/zones/zone-test-123/clients",
            json={"mac_id": "aa:bb:cc:dd:ee:ff"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "zone" in data
        assert "aa:bb:cc:dd:ee:ff" in data["zone"]["client_ids"]

    def test_add_client_to_zone_returns_enriched_zone(self, membership_client, mock_membership_registry_service):
        """AC4: Response includes enriched zone data with computed fields."""
        response = membership_client.post(
            "/api/multiroom/zones/zone-test-123/clients",
            json={"mac_id": "aa:bb:cc:dd:ee:ff"}
        )

        assert response.status_code == 200
        zone = response.json()["zone"]
        assert "online_client_count" in zone
        assert "has_subwoofer" in zone
        assert "crossover_enabled" in zone

    def test_add_client_zone_not_found(self, membership_client, mock_membership_registry_service):
        """AC4: POST /zones/{zone_id}/clients returns 404 for unknown zone."""
        response = membership_client.post(
            "/api/multiroom/zones/nonexistent-zone/clients",
            json={"mac_id": "aa:bb:cc:dd:ee:ff"}
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_add_client_not_found(self, membership_client, mock_membership_registry_service):
        """AC4: POST returns 400 when client mac_id doesn't exist."""
        response = membership_client.post(
            "/api/multiroom/zones/zone-test-123/clients",
            json={"mac_id": "nonexistent-client"}
        )

        assert response.status_code == 400
        assert "not found" in response.json()["detail"].lower()

    def test_add_client_already_in_zone(self, membership_client, mock_membership_registry_service):
        """AC4: POST returns 400 when client is already in the zone."""
        response = membership_client.post(
            "/api/multiroom/zones/zone-test-123/clients",
            json={"mac_id": "local"}  # Already in zone
        )

        assert response.status_code == 400
        assert "already" in response.json()["detail"].lower()

    def test_add_client_service_called_correctly(self, membership_client, mock_membership_registry_service):
        """AC3: Service method add_client_to_zone is called with correct args."""
        response = membership_client.post(
            "/api/multiroom/zones/zone-test-123/clients",
            json={"mac_id": "aa:bb:cc:dd:ee:ff"}
        )

        assert response.status_code == 200
        mock_membership_registry_service.add_client_to_zone.assert_called_once_with(
            "zone-test-123", "aa:bb:cc:dd:ee:ff"
        )


class TestRemoveClientFromZone:
    """Tests for DELETE /api/multiroom/zones/{zone_id}/clients/{mac_id} endpoint (AC2, AC4)."""

    def test_remove_client_from_zone_success(self, membership_client, mock_membership_registry_service):
        """AC2/AC4: DELETE /zones/{zone_id}/clients/{mac_id} removes client from zone."""
        # Remove one client, zone still has >= 2 clients after adding one first
        # First add the third client
        membership_client.post(
            "/api/multiroom/zones/zone-test-123/clients",
            json={"mac_id": "aa:bb:cc:dd:ee:ff"}
        )

        # Now remove one
        response = membership_client.delete(
            "/api/multiroom/zones/zone-test-123/clients/aa:bb:cc:dd:ee:ff"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"

    def test_remove_client_zone_still_exists(self, membership_client, mock_membership_registry_service):
        """AC2: Zone persists if >= 2 clients remain after removal."""
        # Add third client first
        membership_client.post(
            "/api/multiroom/zones/zone-test-123/clients",
            json={"mac_id": "aa:bb:cc:dd:ee:ff"}
        )

        # Remove one
        response = membership_client.delete(
            "/api/multiroom/zones/zone-test-123/clients/aa:bb:cc:dd:ee:ff"
        )

        assert response.status_code == 200
        data = response.json()
        assert "zone" in data
        assert data["zone"]["id"] == "zone-test-123"

    def test_remove_client_zone_deleted_when_less_than_2(self, membership_client, mock_membership_registry_service):
        """AC2: Zone is deleted when < 2 clients remain."""
        # Remove one client from 2-client zone
        response = membership_client.delete(
            "/api/multiroom/zones/zone-test-123/clients/local"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "deleted" in data["message"].lower()

    def test_remove_client_zone_not_found(self, membership_client, mock_membership_registry_service):
        """AC4: DELETE returns 404 for unknown zone."""
        response = membership_client.delete(
            "/api/multiroom/zones/nonexistent-zone/clients/local"
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_remove_client_not_in_zone(self, membership_client, mock_membership_registry_service):
        """AC4: DELETE returns 400 when client is not in the zone."""
        response = membership_client.delete(
            "/api/multiroom/zones/zone-test-123/clients/aa:bb:cc:dd:ee:ff"  # Not in zone
        )

        assert response.status_code == 400
        assert "not in zone" in response.json()["detail"].lower()

    def test_remove_client_service_called_correctly(self, membership_client, mock_membership_registry_service):
        """AC3: Service method remove_client_from_zone is called with correct args."""
        response = membership_client.delete(
            "/api/multiroom/zones/zone-test-123/clients/local"
        )

        assert response.status_code == 200
        mock_membership_registry_service.remove_client_from_zone.assert_called_once_with(
            "zone-test-123", "local"
        )

    def test_remove_client_returns_enriched_zone(self, membership_client, mock_membership_registry_service):
        """AC4: Response includes enriched zone data when zone still exists."""
        # Add third client first
        membership_client.post(
            "/api/multiroom/zones/zone-test-123/clients",
            json={"mac_id": "aa:bb:cc:dd:ee:ff"}
        )

        # Remove one
        response = membership_client.delete(
            "/api/multiroom/zones/zone-test-123/clients/aa:bb:cc:dd:ee:ff"
        )

        assert response.status_code == 200
        zone = response.json()["zone"]
        assert "online_client_count" in zone
        assert "has_subwoofer" in zone
        assert "crossover_enabled" in zone


# =============================================================================
# State Endpoint Tests (Story 1-6)
# =============================================================================


class TestGetState:
    """Tests for GET /api/multiroom/state endpoint (AC1: Story 1-6)."""

    def test_get_state_returns_clients_and_zones(self, zone_client, mock_zone_registry_service):
        """AC1: GET /state returns {clients: {...}, zones: {...}}."""
        response = zone_client.get("/api/multiroom/state")

        assert response.status_code == 200
        data = response.json()
        assert "clients" in data
        assert "zones" in data
        assert isinstance(data["clients"], dict)
        assert isinstance(data["zones"], dict)

    def test_get_state_clients_indexed_by_mac_id(self, zone_client, mock_zone_registry_service):
        """AC1: Clients are indexed by mac_id."""
        response = zone_client.get("/api/multiroom/state")

        assert response.status_code == 200
        clients = response.json()["clients"]
        assert "local" in clients
        assert "dc:a6:32:7e:d3:43" in clients
        assert clients["local"]["mac_id"] == "local"
        assert clients["dc:a6:32:7e:d3:43"]["mac_id"] == "dc:a6:32:7e:d3:43"

    def test_get_state_clients_include_online_status(self, zone_client, mock_zone_registry_service):
        """AC1: Each client includes runtime 'online' status."""
        response = zone_client.get("/api/multiroom/state")

        assert response.status_code == 200
        clients = response.json()["clients"]
        for mac_id, client_data in clients.items():
            assert "online" in client_data

    def test_get_state_zones_indexed_by_zone_id(self, zone_client, mock_zone_registry_service):
        """AC1: Zones are indexed by zone_id."""
        response = zone_client.get("/api/multiroom/state")

        assert response.status_code == 200
        zones = response.json()["zones"]
        assert "zone-test-123" in zones
        assert zones["zone-test-123"]["id"] == "zone-test-123"

    def test_get_state_zones_include_enriched_fields(self, zone_client, mock_zone_registry_service):
        """AC1: Zones include enriched computed fields."""
        response = zone_client.get("/api/multiroom/state")

        assert response.status_code == 200
        zones = response.json()["zones"]
        zone = zones["zone-test-123"]
        assert "online_client_count" in zone
        assert "has_subwoofer" in zone
        assert "crossover_enabled" in zone

    def test_get_state_empty_registry(self, client, mock_registry_service):
        """GET /state with empty registry returns empty dicts."""
        mock_registry_service.get_all_clients.return_value = {}
        mock_registry_service.get_all_zones = Mock(return_value={})

        response = client.get("/api/multiroom/state")

        assert response.status_code == 200
        data = response.json()
        assert data["clients"] == {}
        assert data["zones"] == {}

    def test_get_state_format_matches_registry_state(self, zone_client, mock_zone_registry_service):
        """AC1: Response format matches /api/registry/state for compatibility."""
        response = zone_client.get("/api/multiroom/state")

        assert response.status_code == 200
        data = response.json()
        # Same structure as /api/registry/state
        assert set(data.keys()) == {"clients", "zones"}


class TestZoneAddClientModel:
    """Tests for ZoneAddClient Pydantic model validation."""

    def test_valid_zone_add_client(self):
        """Valid ZoneAddClient with mac_id."""
        from backend.api.models import ZoneAddClient
        request = ZoneAddClient(mac_id="dc:a6:32:7e:d3:43")
        assert request.mac_id == "dc:a6:32:7e:d3:43"

    def test_zone_add_client_strips_whitespace(self):
        """mac_id is stripped of whitespace."""
        from backend.api.models import ZoneAddClient
        request = ZoneAddClient(mac_id="  local  ")
        assert request.mac_id == "local"

    def test_zone_add_client_empty_fails(self):
        """Empty mac_id fails validation."""
        from backend.api.models import ZoneAddClient
        with pytest.raises(ValidationError):
            ZoneAddClient(mac_id="")

    def test_zone_add_client_missing_fails(self):
        """Missing mac_id fails validation."""
        from backend.api.models import ZoneAddClient
        with pytest.raises(ValidationError):
            ZoneAddClient()
