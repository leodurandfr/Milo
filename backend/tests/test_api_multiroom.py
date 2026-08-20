# backend/tests/test_api_multiroom.py
"""
Unit tests for /api/multiroom/ API endpoints.

Tests cover:
- GET /api/multiroom/clients/{mac_id} — 404 for a non-existent client
- PATCH /api/multiroom/clients/{mac_id} — update name, update speaker_type
- Validation — 400 for an invalid speaker_type
"""
import dataclasses
import logging
import pytest
import aiohttp
from unittest.mock import Mock, AsyncMock, patch
from fastapi.testclient import TestClient
from fastapi import FastAPI
from pydantic import ValidationError

from backend.api.multiroom import create_multiroom_router
from backend.api.models import ZoneCreate, ZoneUpdate, MAX_ZONE_NAME_LENGTH
from backend.core.multiroom.models import Client, Zone, EqualizerSettings


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

    known_clients = {"dc:a6:32:7e:d3:43": test_client, "local": local_client}

    service.get_client = Mock(side_effect=known_clients.get)

    # Return updated client with new values
    def mock_update_client(mac_id, name=None, speaker_type=None, volume_control=None):
        base = known_clients.get(mac_id)
        if base is None:
            return None
        return dataclasses.replace(
            base,
            name=name if name else base.name,
            speaker_type=speaker_type if speaker_type else base.speaker_type,
            volume_control=base.volume_control if volume_control is None else volume_control
        )

    service.update_client = AsyncMock(side_effect=mock_update_client)

    return service


@pytest.fixture
def client(mock_registry_service):
    """Create a test client with the multiroom router."""
    app = FastAPI()
    router = create_multiroom_router(mock_registry_service)
    app.include_router(router)
    return TestClient(app)


class TestPatchClient:
    """Tests for PATCH /api/multiroom/clients/{mac_id} endpoint."""

    def test_patch_client_name_success(self, client, mock_registry_service):
        """PATCH /clients/{mac_id} updates client name."""
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
            speaker_type=None,
            volume_control=None
        )

    def test_patch_client_speaker_type_success(self, client, mock_registry_service):
        """PATCH /clients/{mac_id} updates speaker_type."""
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
            speaker_type="subwoofer",
            volume_control=None
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
            speaker_type="satellite",
            volume_control=None
        )

    def test_patch_client_not_found(self, client, mock_registry_service):
        """PATCH /clients/{mac_id} returns 404 for unknown client."""
        response = client.patch(
            "/api/multiroom/clients/unknown-client",
            json={"name": "Test"}
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_patch_client_invalid_speaker_type(self, client, mock_registry_service):
        """PATCH with invalid speaker_type returns 400 with allowed values."""
        response = client.patch(
            "/api/multiroom/clients/dc:a6:32:7e:d3:43",
            json={"speaker_type": "invalid_type"}
        )

        assert response.status_code == 422  # Pydantic validation error
        detail = response.json()["detail"]
        # Pydantic returns validation errors in detail array
        assert len(detail) > 0

    def test_patch_client_returns_online_status(self, client, mock_registry_service):
        """Updated client response includes runtime 'online' status."""
        response = client.patch(
            "/api/multiroom/clients/dc:a6:32:7e:d3:43",
            json={"name": "Updated"}
        )

        assert response.status_code == 200
        assert "online" in response.json()["client"]


class TestSpeakerTypeValidation:
    """Tests for speaker_type validation."""

    @pytest.mark.parametrize("speaker_type", [
        "satellite",
        "bookshelf",
        "tower",
        "subwoofer"
    ])
    def test_valid_speaker_types(self, client, mock_registry_service, speaker_type):
        """Valid speaker_types are accepted."""
        response = client.patch(
            "/api/multiroom/clients/dc:a6:32:7e:d3:43",
            json={"speaker_type": speaker_type}
        )

        assert response.status_code == 200
        assert response.json()["client"]["speaker_type"] == speaker_type


# =============================================================================
# Client tuning endpoints (EQ independence + delay)
# =============================================================================

@pytest.fixture
def tuning_client():
    """Router wired with a registry, EQ service and snapcast for the tuning routes.

    Two online zone members ("mac-1" the subject, "mac-2" a shared donor) so the
    reattach path has a non-independent online member to re-adopt the EQ from.
    """
    clients = {
        "mac-1": Client(mac_id="mac-1", name="Kitchen", ip="192.168.1.50", online=True, zone_id="z1"),
        "mac-2": Client(mac_id="mac-2", name="Living", ip="192.168.1.51", online=True, zone_id="z1"),
        "mac-off": Client(mac_id="mac-off", name="Garage", ip="192.168.1.52", online=False, zone_id=None),
    }
    registry = Mock()
    registry.get_client = Mock(side_effect=clients.get)
    registry.get_zone = Mock(return_value=Zone(id="z1", name="Pair", client_ids=["mac-1", "mac-2"]))

    def _set_eq_ind(mac_id, enabled):
        clients[mac_id] = dataclasses.replace(clients[mac_id], eq_independent=enabled)
        return clients[mac_id]

    def _set_delay(mac_id, delay_ms):
        clients[mac_id] = dataclasses.replace(clients[mac_id], delay_ms=delay_ms)
        return clients[mac_id]

    registry.set_client_eq_independent = AsyncMock(side_effect=_set_eq_ind)
    registry.set_client_delay = AsyncMock(side_effect=_set_delay)

    eq = Mock()
    eq.get_client_eq = AsyncMock(return_value=EqualizerSettings.default())
    eq.set_client_eq = AsyncMock(return_value=True)

    snapcast = Mock()
    snapcast.set_latency = AsyncMock(return_value=True)

    app = FastAPI()
    app.include_router(create_multiroom_router(registry, eq, None, None, snapcast))
    return TestClient(app), registry, eq, snapcast


class TestEqIndependentEndpoint:
    """PUT /api/multiroom/clients/{mac_id}/eq-independent."""

    def test_detach_sets_the_flag_only(self, tuning_client):
        """enabled=true copies nothing — the member already holds the zone EQ."""
        client, registry, eq, _ = tuning_client
        resp = client.put("/api/multiroom/clients/mac-1/eq-independent", json={"enabled": True})

        assert resp.status_code == 200
        assert resp.json()["client"]["eq_independent"] is True
        registry.set_client_eq_independent.assert_awaited_once_with("mac-1", True)
        eq.get_client_eq.assert_not_awaited()  # no adoption on detach
        eq.set_client_eq.assert_not_awaited()

    def test_reattach_readopts_the_zone_eq_from_a_shared_member(self, tuning_client):
        """enabled=false re-adopts the current zone EQ from a shared online member."""
        client, registry, eq, _ = tuning_client
        resp = client.put("/api/multiroom/clients/mac-1/eq-independent", json={"enabled": False})

        assert resp.status_code == 200
        registry.set_client_eq_independent.assert_awaited_once_with("mac-1", False)
        eq.get_client_eq.assert_awaited_once_with("mac-2")  # the shared donor
        eq.set_client_eq.assert_awaited_once()
        assert eq.set_client_eq.await_args.args[0] == "mac-1"

    def test_unknown_client_404(self, tuning_client):
        client, _, _, _ = tuning_client
        resp = client.put("/api/multiroom/clients/nope/eq-independent", json={"enabled": True})
        assert resp.status_code == 404

    def test_a_refused_readoption_is_warned(self, tuning_client, caplog):
        """`set_client_eq` answers False when the satellite refused the push —
        it never raises, so the route's `except Exception` could not see it and
        the member kept its own curve while the UI showed it back on the zone's.

        Warning, not error: the record is persisted either way and the client's
        next reconnection sync re-pushes it.
        """
        client, _, eq, _ = tuning_client
        eq.set_client_eq = AsyncMock(return_value=False)

        with caplog.at_level(logging.WARNING):
            resp = client.put(
                "/api/multiroom/clients/mac-1/eq-independent", json={"enabled": False}
            )

        assert resp.status_code == 200
        assert "Failed to re-adopt zone equalizer for mac-1" in caplog.text

    def test_an_accepted_readoption_says_nothing(self, tuning_client, caplog):
        client, _, _, _ = tuning_client
        with caplog.at_level(logging.WARNING):
            client.put("/api/multiroom/clients/mac-1/eq-independent", json={"enabled": False})

        assert "Failed to re-adopt" not in caplog.text


class TestClientDelayEndpoint:
    """PATCH /api/multiroom/clients/{mac_id}/delay."""

    def test_delay_persists_and_applies_to_snapcast(self, tuning_client):
        client, registry, _, snapcast = tuning_client
        resp = client.patch("/api/multiroom/clients/mac-1/delay", json={"delay_ms": 40})

        assert resp.status_code == 200
        assert resp.json()["client"]["delay_ms"] == 40
        registry.set_client_delay.assert_awaited_once_with("mac-1", 40)
        snapcast.set_latency.assert_awaited_once_with("mac-1", 40)

    def test_delay_offline_client_skips_snapcast(self, tuning_client):
        """An offline client's delay persists but is not pushed — it re-applies on
        reconnection via the admission path."""
        client, registry, _, snapcast = tuning_client
        resp = client.patch("/api/multiroom/clients/mac-off/delay", json={"delay_ms": 20})

        assert resp.status_code == 200
        registry.set_client_delay.assert_awaited_once_with("mac-off", 20)
        snapcast.set_latency.assert_not_awaited()

    def test_delay_out_of_range_422(self, tuning_client):
        client, _, _, _ = tuning_client
        resp = client.patch("/api/multiroom/clients/mac-1/delay", json={"delay_ms": 500})
        assert resp.status_code == 422

    def test_delay_unknown_client_404(self, tuning_client):
        client, _, _, _ = tuning_client
        resp = client.patch("/api/multiroom/clients/nope/delay", json={"delay_ms": 10})
        assert resp.status_code == 404


# =============================================================================
# Zone Pydantic Model Tests
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

    def test_zone_create_name_accepts_any_chars(self):
        """Name accepts any characters (accents, special chars, emojis)."""
        zone = ZoneCreate(
            name="Milō 2.1 🎵",
            client_ids=["c1", "c2"]
        )
        assert zone.name == "Milō 2.1 🎵"

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

    def test_zone_update_name_accepts_any_chars(self):
        """Name accepts any characters (accents, special chars, emojis)."""
        update = ZoneUpdate(name="Milō 2.1 🎵")
        assert update.name == "Milō 2.1 🎵"


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
# Zone API Endpoint Tests
# =============================================================================


@pytest.fixture
def mock_zone_registry_service():
    """Create a mock ClientRegistryService with zone support."""
    from backend.core.multiroom.models import Zone

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

    # Test zone (a zone holds no EQ of its own in the unified model)
    test_zone = Zone(
        id="zone-test-123",
        name="Living Room",
        client_ids=["local", "dc:a6:32:7e:d3:43"],
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

    async def mock_create_zone(zone_id, name, client_ids):
        if len(client_ids) < 2:
            raise ValueError("Zone requires at least 2 clients")
        for cid in client_ids:
            if cid not in service._clients:
                raise ValueError(f"Client {cid} not found")
        zone = Zone(
            id=zone_id,
            name=name,
            client_ids=client_ids,
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
    def mock_update_client(mac_id, name=None, speaker_type=None, volume_control=None):
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


class TestCreateZone:
    """Tests for POST /api/multiroom/zones endpoint."""

    def test_create_zone_success(self, zone_client, mock_zone_registry_service):
        """POST /zones creates zone with valid 2+ clients."""
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
        """Created zone response includes computed fields."""
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
        """Zone creation generates UUID for zone_id."""
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
        """POST /zones with < 2 clients returns 400."""
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
        """POST /zones with unknown client returns 400."""
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
        """Zone name max length (15) is enforced."""
        response = zone_client.post(
            "/api/multiroom/zones",
            json={
                "name": "A" * 16,  # 16 chars, exceeds max
                "client_ids": ["local", "dc:a6:32:7e:d3:43"]
            }
        )

        assert response.status_code == 422  # Pydantic validation error


class TestUpdateZone:
    """Tests for PATCH /api/multiroom/zones/{zone_id} endpoint."""

    def test_update_zone_name_success(self, zone_client, mock_zone_registry_service):
        """PATCH /zones/{zone_id} updates zone name."""
        response = zone_client.patch(
            "/api/multiroom/zones/zone-test-123",
            json={"name": "New Name"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["zone"]["name"] == "New Name"

    def test_update_zone_includes_enriched_fields(self, zone_client, mock_zone_registry_service):
        """Updated zone response includes computed fields."""
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
        """PATCH /zones/{zone_id} returns 404 for unknown zone."""
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
    """Tests for DELETE /api/multiroom/zones/{zone_id} endpoint."""

    def test_delete_zone_success(self, zone_client, mock_zone_registry_service):
        """DELETE /zones/{zone_id} deletes zone and returns success."""
        response = zone_client.delete("/api/multiroom/zones/zone-test-123")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "deleted" in data["message"].lower()
        assert "zone-test-123" in data["message"]

    def test_delete_zone_not_found(self, zone_client, mock_zone_registry_service):
        """DELETE /zones/{zone_id} returns 404 for unknown zone."""
        response = zone_client.delete("/api/multiroom/zones/nonexistent-zone")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_delete_zone_removes_from_registry(self, zone_client, mock_zone_registry_service):
        """Zone is actually removed from registry after DELETE."""
        # First delete
        response = zone_client.delete("/api/multiroom/zones/zone-test-123")
        assert response.status_code == 200

        # Verify service was called
        mock_zone_registry_service.delete_zone.assert_called_once_with("zone-test-123")


# =============================================================================
# Zone Client Membership Tests
# =============================================================================


@pytest.fixture
def mock_membership_registry_service():
    """Create a mock ClientRegistryService with zone membership support."""
    from backend.core.multiroom.models import Zone

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

    # Test zone with 2 clients (a zone holds no EQ of its own)
    test_zone = Zone(
        id="zone-test-123",
        name="Living Room",
        client_ids=["local", "dc:a6:32:7e:d3:43"],
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
    def mock_update_client(mac_id, name=None, speaker_type=None, volume_control=None):
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
    """Tests for POST /api/multiroom/zones/{zone_id}/clients endpoint."""

    def test_add_client_to_zone_success(self, membership_client, mock_membership_registry_service):
        """POST /zones/{zone_id}/clients adds client to zone."""
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
        """Response includes enriched zone data with computed fields."""
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
        """POST /zones/{zone_id}/clients returns 404 for unknown zone."""
        response = membership_client.post(
            "/api/multiroom/zones/nonexistent-zone/clients",
            json={"mac_id": "aa:bb:cc:dd:ee:ff"}
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_add_client_not_found(self, membership_client, mock_membership_registry_service):
        """POST returns 400 when client mac_id doesn't exist."""
        response = membership_client.post(
            "/api/multiroom/zones/zone-test-123/clients",
            json={"mac_id": "nonexistent-client"}
        )

        assert response.status_code == 400
        assert "not found" in response.json()["detail"].lower()

    def test_add_client_already_in_zone(self, membership_client, mock_membership_registry_service):
        """POST returns 400 when client is already in the zone."""
        response = membership_client.post(
            "/api/multiroom/zones/zone-test-123/clients",
            json={"mac_id": "local"}  # Already in zone
        )

        assert response.status_code == 400
        assert "already" in response.json()["detail"].lower()

    def test_add_client_service_called_correctly(self, membership_client, mock_membership_registry_service):
        """Service method add_client_to_zone is called with correct args."""
        response = membership_client.post(
            "/api/multiroom/zones/zone-test-123/clients",
            json={"mac_id": "aa:bb:cc:dd:ee:ff"}
        )

        assert response.status_code == 200
        mock_membership_registry_service.add_client_to_zone.assert_called_once_with(
            "zone-test-123", "aa:bb:cc:dd:ee:ff"
        )


@pytest.fixture
def eq_donor_client():
    """A zone whose FIRST member detached its EQ, a second member still sharing
    the zone's record, and a standalone client about to join."""
    clients = {
        "mac-indep": Client(mac_id="mac-indep", name="Kitchen", ip="192.168.1.50",
                            online=True, zone_id="z1", eq_independent=True),
        "mac-shared": Client(mac_id="mac-shared", name="Living", ip="192.168.1.51",
                             online=True, zone_id="z1"),
        "mac-new": Client(mac_id="mac-new", name="Bedroom", ip="192.168.1.52",
                          online=True, zone_id=None),
    }
    zone = Zone(id="z1", name="Pair", client_ids=["mac-indep", "mac-shared"])

    registry = Mock()
    registry.get_client = Mock(side_effect=clients.get)
    registry.get_zone = Mock(side_effect=lambda zone_id: zone if zone_id == "z1" else None)
    registry.zone_to_enriched_dict = Mock(side_effect=lambda z: z.to_dict())

    async def _add(zone_id, mac_id):
        zone.client_ids.append(mac_id)
        return True

    registry.add_client_to_zone = AsyncMock(side_effect=_add)

    eq = Mock()
    eq.get_client_eq = AsyncMock(return_value=EqualizerSettings.default())
    eq.set_client_eq = AsyncMock(return_value=True)

    app = FastAPI()
    app.include_router(create_multiroom_router(registry, eq))
    return TestClient(app), eq


class TestNewZoneMemberEqDonor:
    """POST /zones/{id}/clients picks the record the new member adopts."""

    def test_the_donor_is_never_a_member_that_detached_its_eq(self, eq_donor_client):
        """A detached member has retuned its own curve and left the zone's shared
        record — adopting it would put two audibly different timbres in one zone.
        The re-attach donor (PUT /clients/{mac}/eq-independent) already filters
        it out; this path took the first member it found.
        """
        client, eq = eq_donor_client

        resp = client.post("/api/multiroom/zones/z1/clients", json={"mac_id": "mac-new"})

        assert resp.status_code == 200
        eq.get_client_eq.assert_awaited_once_with("mac-shared")
        assert eq.set_client_eq.await_args.args[0] == "mac-new"

    def test_a_refused_adoption_is_warned(self, eq_donor_client, caplog):
        """Same dropped verdict as the re-attach path: False never enters the
        route's `except`, so a new member silently kept whatever curve it had."""
        client, eq = eq_donor_client
        eq.set_client_eq = AsyncMock(return_value=False)

        with caplog.at_level(logging.WARNING):
            resp = client.post("/api/multiroom/zones/z1/clients", json={"mac_id": "mac-new"})

        assert resp.status_code == 200
        assert "Failed to apply zone equalizer to new member mac-new" in caplog.text


@pytest.fixture
def pending_client():
    """The configure route wired with a pending entry and a stubbed reboot push."""
    pending = Mock()
    pending.get_client = Mock(return_value={"ip": "192.168.1.60", "mac_id": "aa:bb"})
    pending.update_client = AsyncMock(return_value={"mac_id": "aa:bb"})
    pending.mark_configuring = AsyncMock(return_value=True)

    app = FastAPI()
    app.include_router(create_multiroom_router(Mock(), None, pending))
    return TestClient(app), pending


class TestConfigurePendingClient:
    """POST /api/multiroom/pending-clients/{mac}/configure.

    `mark_configuring` shields the entry from the heartbeat sweep across a reboot
    that outlasts STALE_TIMEOUT. It answers False when the sweep already expired
    the entry — and that entry holds the only copy of the name and speaker type
    the user just typed, so a client comes back unnamed with nobody told why.
    """

    def test_a_vanished_entry_is_reported_at_error(self, pending_client, caplog):
        client, pending = pending_client
        pending.mark_configuring = AsyncMock(return_value=False)

        with patch("backend.api.multiroom._send_audio_config_and_reboot", AsyncMock()) as reboot, \
                caplog.at_level(logging.ERROR):
            resp = client.post(
                "/api/multiroom/pending-clients/aa:bb/configure",
                json={"name": "Bureau", "speaker_type": "bookshelf", "audio_id": "hifiberry_amp2"},
            )

        assert resp.status_code == 200
        assert "will come back unnamed" in caplog.text
        # The reboot is what the caller asked for and is addressed by IP, so it
        # still goes ahead — only the name is lost.
        reboot.assert_awaited_once()

    def test_a_shielded_entry_says_nothing(self, pending_client, caplog):
        client, _ = pending_client

        with patch("backend.api.multiroom._send_audio_config_and_reboot", AsyncMock()), \
                caplog.at_level(logging.ERROR):
            resp = client.post(
                "/api/multiroom/pending-clients/aa:bb/configure",
                json={"name": "Bureau", "speaker_type": "bookshelf", "audio_id": "hifiberry_amp2"},
            )

        assert resp.status_code == 200
        assert caplog.text == ""


class TestRemoveClientFromZone:
    """Tests for DELETE /api/multiroom/zones/{zone_id}/clients/{mac_id} endpoint."""

    def test_remove_client_from_zone_success(self, membership_client, mock_membership_registry_service):
        """DELETE /zones/{zone_id}/clients/{mac_id} removes client from zone."""
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
        """Zone persists if >= 2 clients remain after removal."""
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
        """Zone is deleted when < 2 clients remain."""
        # Remove one client from 2-client zone
        response = membership_client.delete(
            "/api/multiroom/zones/zone-test-123/clients/local"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert "deleted" in data["message"].lower()

    def test_remove_client_zone_not_found(self, membership_client, mock_membership_registry_service):
        """DELETE returns 404 for unknown zone."""
        response = membership_client.delete(
            "/api/multiroom/zones/nonexistent-zone/clients/local"
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_remove_client_not_in_zone(self, membership_client, mock_membership_registry_service):
        """DELETE returns 400 when client is not in the zone."""
        response = membership_client.delete(
            "/api/multiroom/zones/zone-test-123/clients/aa:bb:cc:dd:ee:ff"  # Not in zone
        )

        assert response.status_code == 400
        assert "not in zone" in response.json()["detail"].lower()

    def test_remove_client_service_called_correctly(self, membership_client, mock_membership_registry_service):
        """Service method remove_client_from_zone is called with correct args."""
        response = membership_client.delete(
            "/api/multiroom/zones/zone-test-123/clients/local"
        )

        assert response.status_code == 200
        mock_membership_registry_service.remove_client_from_zone.assert_called_once_with(
            "zone-test-123", "local"
        )

    def test_remove_client_returns_enriched_zone(self, membership_client, mock_membership_registry_service):
        """Response includes enriched zone data when zone still exists."""
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
# State Endpoint Tests
# =============================================================================


class TestGetState:
    """Tests for GET /api/multiroom/state endpoint (:)."""

    def test_get_state_returns_clients_and_zones(self, zone_client, mock_zone_registry_service):
        """GET /state returns {clients: {...}, zones: {...}}."""
        response = zone_client.get("/api/multiroom/state")

        assert response.status_code == 200
        data = response.json()
        assert "clients" in data
        assert "zones" in data
        assert isinstance(data["clients"], dict)
        assert isinstance(data["zones"], dict)

    def test_get_state_clients_indexed_by_mac_id(self, zone_client, mock_zone_registry_service):
        """Clients are indexed by mac_id."""
        response = zone_client.get("/api/multiroom/state")

        assert response.status_code == 200
        clients = response.json()["clients"]
        assert "local" in clients
        assert "dc:a6:32:7e:d3:43" in clients
        assert clients["local"]["mac_id"] == "local"
        assert clients["dc:a6:32:7e:d3:43"]["mac_id"] == "dc:a6:32:7e:d3:43"

    def test_get_state_clients_include_online_status(self, zone_client, mock_zone_registry_service):
        """Each client includes runtime 'online' status."""
        response = zone_client.get("/api/multiroom/state")

        assert response.status_code == 200
        clients = response.json()["clients"]
        for mac_id, client_data in clients.items():
            assert "online" in client_data

    def test_get_state_zones_indexed_by_zone_id(self, zone_client, mock_zone_registry_service):
        """Zones are indexed by zone_id."""
        response = zone_client.get("/api/multiroom/state")

        assert response.status_code == 200
        zones = response.json()["zones"]
        assert "zone-test-123" in zones
        assert zones["zone-test-123"]["id"] == "zone-test-123"

    def test_get_state_zones_include_enriched_fields(self, zone_client, mock_zone_registry_service):
        """Zones include enriched computed fields."""
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
        """Response format matches /api/registry/state for compatibility."""
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


class TestUnreachableSatellite:
    """
    Tests for what a route does when a registered satellite does not answer on
    CLIENT_API_PORT.

    Snapserver only notices a client once its socket errors, so the registry can
    still claim `online` at the moment a route tries to reach one that is gone.
    If these fail, either the client keeps being shown as online and controllable,
    or the failure is logged at ERROR — which WebSocketLogHandler turns into a
    backend-error banner in the UI for a speaker that is merely unplugged.
    """

    class _RefusingSession:
        """An aiohttp session whose every request is refused, like a dead host."""

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def get(self, *args, **kwargs):
            raise aiohttp.ClientConnectorError(Mock(), OSError("connection refused"))

        def put(self, *args, **kwargs):
            raise aiohttp.ClientConnectorError(Mock(), OSError("connection refused"))

    def test_hardware_read_marks_the_client_offline(self, client, mock_registry_service, caplog):
        """An unreachable satellite is recorded offline, so the UI stops offering controls."""
        mock_registry_service.set_client_online = AsyncMock()

        with patch("backend.api.multiroom.aiohttp.ClientSession", return_value=self._RefusingSession()):
            with caplog.at_level(logging.DEBUG, logger="backend.api.multiroom"):
                response = client.get("/api/multiroom/clients/dc:a6:32:7e:d3:43/hardware")

        assert response.status_code == 502
        mock_registry_service.set_client_online.assert_awaited_once_with("dc:a6:32:7e:d3:43", False)

    def test_hardware_read_does_not_log_an_error(self, client, mock_registry_service, caplog):
        """An absent satellite is an expected state — logging it at ERROR raises a UI banner."""
        mock_registry_service.set_client_online = AsyncMock()

        with patch("backend.api.multiroom.aiohttp.ClientSession", return_value=self._RefusingSession()):
            with caplog.at_level(logging.DEBUG, logger="backend.api.multiroom"):
                client.get("/api/multiroom/clients/dc:a6:32:7e:d3:43/hardware")

        assert [r.message for r in caplog.records if r.levelno >= logging.ERROR] == []
        assert any(r.levelno == logging.WARNING for r in caplog.records)


class TestVolumeControlPush:
    """
    Tests for PATCH /clients/{mac_id} carrying `volume_control` on a REMOTE client.

    The satellite owns that flag: it lives in the satellite's own hardware.json,
    and its registration heartbeat re-sends that value every 15 s. Writing only
    the registry is therefore undone a few seconds later, with no error and no
    log — the failure mode the milo-client contract test cannot see, because the
    route is served and every key is read; what is wrong is only *who* was told.

    If these fail: either the satellite stops being written first (the flag
    silently reverts), or a satellite that refuses the change is recorded as if
    it had accepted it.
    """

    class _FakeResponse:
        def __init__(self, status, payload):
            self.status = status
            self._payload = payload

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def json(self):
            return self._payload

        async def text(self):
            return str(self._payload)

    class _RecordingSatellite:
        """A satellite that answers its hardware read and records what is written."""

        def __init__(self, audio=None, put_status=200):
            self.audio = {"id": "hifiberry-dacplus", "overlay": "hifiberry-dacplus"} if audio is None else audio
            self.put_status = put_status
            self.gets = []
            self.puts = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def get(self, url, **kwargs):
            self.gets.append(url)
            return TestVolumeControlPush._FakeResponse(200, {"audio": self.audio})

        def put(self, url, json=None, **kwargs):
            self.puts.append((url, json))
            return TestVolumeControlPush._FakeResponse(self.put_status, {})

    @staticmethod
    def _patch_session(satellite):
        return patch("backend.api.multiroom.aiohttp.ClientSession", return_value=satellite)

    def test_the_satellite_is_told_on_its_own_port(self, client, mock_registry_service):
        """The flag reaches the satellite's hardware config, carrying the card it reported."""
        satellite = self._RecordingSatellite()

        with self._patch_session(satellite):
            response = client.patch(
                "/api/multiroom/clients/dc:a6:32:7e:d3:43",
                json={"volume_control": False}
            )

        assert response.status_code == 200
        assert len(satellite.puts) == 1
        url, body = satellite.puts[0]
        assert url == "http://192.168.1.100:8001/api/hardware/audio"
        assert body["volume_control"] is False
        # The card is read back from the satellite, not invented here: writing
        # hardware.json with a wrong audio_id would reconfigure its output.
        assert body["audio_id"] == satellite.audio["id"]
        assert body["overlay"] == satellite.audio["overlay"]
        mock_registry_service.update_client.assert_awaited_once()

    def test_a_refusing_satellite_leaves_the_registry_untouched(self, client, mock_registry_service):
        """A rejected push must surface as an error, not become a local write the heartbeat undoes."""
        satellite = self._RecordingSatellite(put_status=500)

        with self._patch_session(satellite):
            response = client.patch(
                "/api/multiroom/clients/dc:a6:32:7e:d3:43",
                json={"volume_control": False}
            )

        assert response.status_code == 502
        mock_registry_service.update_client.assert_not_awaited()

    def test_an_unreachable_satellite_is_marked_offline(self, client, mock_registry_service):
        """Same path as every other satellite call: a dead host stops being shown as controllable."""
        mock_registry_service.set_client_online = AsyncMock()

        with patch("backend.api.multiroom.aiohttp.ClientSession",
                   return_value=TestUnreachableSatellite._RefusingSession()):
            response = client.patch(
                "/api/multiroom/clients/dc:a6:32:7e:d3:43",
                json={"volume_control": False}
            )

        assert response.status_code == 502
        mock_registry_service.set_client_online.assert_awaited_once_with("dc:a6:32:7e:d3:43", False)
        mock_registry_service.update_client.assert_not_awaited()

    def test_an_unchanged_value_does_not_reach_the_satellite(self, client, mock_registry_service):
        """Renaming a speaker must not cost a round trip to it, nor fail when it is asleep."""
        satellite = self._RecordingSatellite()

        with self._patch_session(satellite):
            response = client.patch(
                "/api/multiroom/clients/dc:a6:32:7e:d3:43",
                json={"volume_control": True, "name": "Kitchen"}
            )

        assert response.status_code == 200
        assert satellite.gets == []
        assert satellite.puts == []
        mock_registry_service.update_client.assert_awaited_once()

    def test_a_satellite_with_no_audio_card_is_refused(self, client, mock_registry_service):
        """Nothing to manage the volume of yet — writing `none` back would erase its config."""
        satellite = self._RecordingSatellite(audio={"id": "none", "overlay": ""})

        with self._patch_session(satellite):
            response = client.patch(
                "/api/multiroom/clients/dc:a6:32:7e:d3:43",
                json={"volume_control": False}
            )

        assert response.status_code == 400
        assert satellite.puts == []
        mock_registry_service.update_client.assert_not_awaited()

    def test_the_local_client_is_not_pushed_to(self, client, mock_registry_service):
        """The server runs no milo-client: a push to 127.0.0.1:8001 would 502 on its own flag."""
        satellite = self._RecordingSatellite()

        with self._patch_session(satellite):
            response = client.patch("/api/multiroom/clients/local", json={"volume_control": False})

        assert response.status_code == 200
        assert satellite.gets == []
        assert satellite.puts == []
        mock_registry_service.update_client.assert_awaited_once()


class TestRebootAfterAudioChange:
    """A satellite that refuses the reboot has not changed its audio card.

    The overlay is written to hardware.json and applied by the reboot; without
    the reboot the unit keeps playing through the old card. This was documented
    inside the tree before it was fixed — test_rootfs_deployment.py names it as
    the reason a two-unit fleet hid a missing rootfs helper: every
    script-installed satellite answered 500 here, and the pairing wizard
    reported success anyway because a non-200 was only a warning.
    """

    class _RebootingSatellite:
        """Answers the audio write, and the reboot with whatever it is given."""

        def __init__(self, reboot_status=200):
            self.reboot_status = reboot_status
            self.puts = []
            self.posts = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def put(self, url, json=None, **kwargs):
            self.puts.append((url, json))
            return TestVolumeControlPush._FakeResponse(200, {})

        def post(self, url, **kwargs):
            self.posts.append(url)
            return TestVolumeControlPush._FakeResponse(self.reboot_status, "no such helper")

    @staticmethod
    def _patch_session(satellite):
        return patch("backend.api.multiroom.aiohttp.ClientSession", return_value=satellite)

    def _configure(self, client, satellite):
        with self._patch_session(satellite):
            return client.put(
                "/api/multiroom/clients/dc:a6:32:7e:d3:43/audio",
                json={"audio_id": "hifiberry_amp2"},
            )

    def test_a_refused_reboot_is_a_failed_configuration(self, client, mock_registry_service):
        satellite = self._RebootingSatellite(reboot_status=500)

        response = self._configure(client, satellite)

        assert response.status_code == 502
        assert satellite.posts == ["http://192.168.1.100:8001/api/hardware/reboot"]
        assert mock_registry_service.update_client.await_count == 0, \
            "the registry must not record a card the satellite never booted into"

    def test_an_accepted_reboot_is_a_successful_configuration(self, client, mock_registry_service):
        """Sanity floor: the assertion above must be about the reboot's status."""
        satellite = self._RebootingSatellite(reboot_status=200)

        response = self._configure(client, satellite)

        assert response.status_code == 200
        mock_registry_service.update_client.assert_awaited_once()
