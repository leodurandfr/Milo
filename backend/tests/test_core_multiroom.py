# backend/tests/test_core_multiroom.py
"""
Unit tests for the core.multiroom module.

Tests:
- Models (Client, Zone, EqualizerSettings, RegistryEventType)
- ClientRegistryService
- SnapcastService
- CrossoverService
- Helper functions
"""
import pytest
import asyncio
import logging
import time
import aiohttp
from unittest.mock import AsyncMock, MagicMock, patch

from backend.tests.conftest import attach_registry_broadcaster, drain_background_tasks
from backend.core.multiroom.models import (
    Client,
    Zone,
    EqFilter,
    EqualizerSettings,
    RegistryEventType,
    SPEAKER_TYPES,
    DEFAULT_SPEAKER_TYPE,
    CompressorSettings,
    LoudnessSettings,
)
from backend.config.constants import DEFAULT_VOLUME_DB
from backend.core.models.volume import VolumeConfig
from backend.core.multiroom.client_registry import ClientRegistryService
from backend.core.multiroom.identity import compute_mac_id
from backend.core.multiroom.snapcast import (
    SnapcastService,
    SnapcastRequestError,
)
from backend.core.multiroom.crossover import CrossoverService
from backend.core.multiroom.routing import DEFAULT_SNAPCLIENT_CONFIG, SNAPCLIENT_LIMITS
from backend.core.equalizer.client_proxy import is_ip_address


# =============================================================================
# Model Tests
# =============================================================================

class TestClient:
    """Tests for Client model."""

    def test_create_client_with_defaults(self):
        """Test creating a client with default values."""
        client = Client(
            mac_id="local",
            name="Main Speaker",
            ip="127.0.0.1"
        )

        assert client.mac_id == "local"
        assert client.name == "Main Speaker"
        assert client.ip == "127.0.0.1"
        assert client.online is False
        assert client.zone_id is None
        assert client.speaker_type == DEFAULT_SPEAKER_TYPE
        assert client.eq_independent is False
        assert client.delay_ms == 0

    def test_client_to_dict(self):
        """Test converting client to dictionary - completeness validation."""
        # Create client with ALL fields set to verify complete serialization
        client = Client(
            mac_id="aa:bb:cc:dd:ee:ff",
            name="Kitchen",
            ip="192.168.1.100",
            speaker_type="satellite",
            online=True,
            zone_id="zone-123",
            eq_independent=True,
            delay_ms=40
        )

        # Default: include runtime fields (for WebSocket events - )
        data = client.to_dict()

        # Verify ALL required fields are present for complete client object
        assert data["mac_id"] == "aa:bb:cc:dd:ee:ff"
        assert data["name"] == "Kitchen"
        assert data["ip"] == "192.168.1.100"
        assert data["speaker_type"] == "satellite"
        assert data["zone_id"] == "zone-123"
        assert data["eq_independent"] is True
        assert data["delay_ms"] == 40
        # Runtime fields are now included by default for complete WebSocket events
        assert data["online"] is True

        # Verify all expected fields are present (including is_local, host, volume_control)
        expected_fields = {"mac_id", "name", "ip", "host", "speaker_type", "zone_id",
                          "online", "is_local", "volume_control",
                          "eq_independent", "delay_ms"}
        assert set(data.keys()) == expected_fields

        # Explicit: the persistence shape drops every field with another
        # runtime owner, and what it writes reloads into the same client.
        data_persist = client.to_dict(include_runtime=False)
        assert set(data_persist) <= set(data)
        for runtime_owned in ("online", "is_local", "host"):
            assert runtime_owned not in data_persist
        reloaded = Client.from_dict(data_persist)
        for name in data_persist:
            assert getattr(reloaded, name) == getattr(client, name)

    def test_client_from_dict(self):
        """Test creating client from dictionary."""
        data = {
            "mac_id": "aa:bb:cc:dd:ee:ff",
            "name": "Living Room",
            "ip": "192.168.1.101",
            "speaker_type": "tower",
            "online": True,
            "zone_id": "zone1"
        }

        client = Client.from_dict(data)

        assert client.mac_id == "aa:bb:cc:dd:ee:ff"
        assert client.speaker_type == "tower"
        assert client.zone_id == "zone1"

    def test_client_from_dict_missing_required_fields(self):
        """Verify KeyError raised when mac_id missing."""
        with pytest.raises(KeyError):
            Client.from_dict({"name": "Test", "ip": "127.0.0.1"})

    def test_client_from_dict_with_unknown_speaker_type(self):
        """Test from_dict with unknown speaker_type uses default."""
        data = {
            "mac_id": "test",
            "name": "Test",
            "ip": "127.0.0.1",
            "speaker_type": "unknown_type"
        }
        client = Client.from_dict(data)
        # Model accepts any string - validation happens in API layer
        assert client.speaker_type == "unknown_type"


class TestEqualizerSettings:
    """Tests for EqualizerSettings model."""

    def test_create_default_equalizer_settings(self):
        """Test creating default Equalizer settings (without factory method)."""
        settings = EqualizerSettings()

        # New structure: enabled=True, typed sub-models
        assert settings.enabled is True
        assert settings.filters == []
        assert isinstance(settings.compressor, CompressorSettings)
        assert settings.compressor.enabled is False
        assert isinstance(settings.loudness, LoudnessSettings)
        assert settings.loudness.enabled is False

    def test_equalizer_settings_factory_default(self):
        """Test creating default Equalizer settings with factory method."""
        from backend.core.multiroom.models import EqFilter

        settings = EqualizerSettings.default()

        # Factory creates 10-band EQ with flat gains
        assert settings.enabled is True
        assert len(settings.filters) == 10
        assert all(isinstance(f, EqFilter) for f in settings.filters)
        assert all(f.gain == 0.0 for f in settings.filters)

    def test_equalizer_settings_to_dict(self):
        """Test converting Equalizer settings to dictionary."""
        from backend.core.multiroom.models import EqFilter, CompressorSettings

        settings = EqualizerSettings(
            enabled=True,
            filters=[EqFilter(id="eq_band_00", frequency=1000, gain=3.0)],
            compressor=CompressorSettings(enabled=True, threshold=-20)
        )

        data = settings.to_dict()

        assert data["enabled"] is True
        assert len(data["filters"]) == 1
        assert data["filters"][0]["frequency"] == 1000
        assert data["compressor"]["enabled"] is True
        assert data["compressor"]["threshold"] == -20
        assert data["loudness"]["enabled"] is False

    def test_equalizer_settings_from_dict(self):
        """Test creating Equalizer settings from dictionary."""
        data = {
            "enabled": True,
            "filters": [{"id": "eq_band_00", "frequency": 100, "gain": 2.0, "filter_type": "Lowshelf", "q": 1.0, "enabled": True}],
            "compressor": {"enabled": False},
            "loudness": {"enabled": True, "high_boost": 7.0}
        }

        settings = EqualizerSettings.from_dict(data)

        assert settings.enabled is True
        assert len(settings.filters) == 1
        assert settings.filters[0].frequency == 100
        assert settings.loudness.enabled is True
        assert settings.loudness.high_boost == 7.0


class TestZone:
    """Tests for Zone model."""

    def test_create_zone(self):
        """Test creating a zone."""
        zone = Zone(
            name="Living Room Zone",
            id="zone1",
            client_ids=["local", "aa:bb:cc:dd:ee:ff"]
        )

        assert zone.id == "zone1"
        assert zone.name == "Living Room Zone"
        assert len(zone.client_ids) == 2

    def test_zone_uuid_auto_generation(self):
        """Test that zone id is auto-generated as UUID when not provided."""
        import re

        zone = Zone(name="Auto ID Zone")

        # Verify UUID format (8-4-4-4-12 hex digits)
        uuid_pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        assert re.match(uuid_pattern, zone.id.lower()) is not None
        assert zone.name == "Auto ID Zone"
        assert zone.client_ids == []

    def test_zone_default_values(self):
        """Test zone default values when created with minimal parameters."""
        zone = Zone(name="Minimal Zone")

        # id should be auto-generated UUID
        assert len(zone.id) == 36  # UUID length with dashes
        # client_ids should default to empty list
        assert zone.client_ids == []
        # A zone holds no EQ of its own in the unified model
        assert not hasattr(zone, "equalizer_settings")

    def test_zone_to_dict(self):
        """Test converting zone to dictionary (no EQ — derived from members)."""
        zone = Zone(
            name="Kitchen Zone",
            id="zone2",
            client_ids=["local", "aa:bb:cc:dd:ee:ff"],
        )

        data = zone.to_dict()

        assert data["id"] == "zone2"
        assert len(data["client_ids"]) == 2
        assert "equalizer_settings" not in data

    def test_zone_from_dict(self):
        """Test creating zone from dictionary."""
        data = {
            "id": "zone3",
            "name": "Bedroom Zone",
            "client_ids": ["local", "aa:bb:cc:dd:ee:ff"],
            "equalizer_settings": {
                "filters": [],
                "compressor": None,
                "loudness": None
            }
        }

        zone = Zone.from_dict(data)

        assert zone.id == "zone3"
        assert len(zone.client_ids) == 2

    def test_zone_is_valid(self):
        """Test zone validity check (minimum 2 clients)."""
        valid_zone = Zone(name="Valid", id="z1", client_ids=["c1", "c2"])
        invalid_zone = Zone(name="Invalid", id="z2", client_ids=["c1"])

        assert valid_zone.is_valid() is True
        assert invalid_zone.is_valid() is False


class TestRegistryEventType:
    """Tests for RegistryEventType constants."""

    def test_event_types_exist(self):
        """Test that all event types are defined."""
        assert RegistryEventType.CLIENT_CONNECTED == "client_connected"
        assert RegistryEventType.CLIENT_DISCONNECTED == "client_disconnected"
        assert RegistryEventType.CLIENT_UPDATED == "client_updated"
        assert RegistryEventType.ZONE_CREATED == "zone_created"
        assert RegistryEventType.ZONE_UPDATED == "zone_updated"
        assert RegistryEventType.ZONE_DELETED == "zone_deleted"
        assert RegistryEventType.EQUALIZER_SETTINGS_CHANGED == "equalizer_settings_changed"


class TestModelConstants:
    """Tests for model constants."""

    def test_speaker_types(self):
        """Test speaker types list."""
        assert "satellite" in SPEAKER_TYPES
        assert "bookshelf" in SPEAKER_TYPES
        assert "tower" in SPEAKER_TYPES
        assert "subwoofer" in SPEAKER_TYPES

# =============================================================================
# ClientRegistryService Tests
# =============================================================================

class TestClientRegistryService:
    """Tests for ClientRegistryService."""

    @pytest.fixture
    def mock_settings_service(self):
        """Create a mock settings service."""
        service = AsyncMock()
        service.get_setting = AsyncMock(return_value=None)
        service.set_setting = AsyncMock()
        return service

    @pytest.fixture
    def registry(self, mock_settings_service):
        """Create a ClientRegistryService instance."""
        return ClientRegistryService(
            settings_service=mock_settings_service
        )

    @pytest.mark.asyncio
    async def test_initialize(self, registry):
        """Test registry initialization."""
        result = await registry.initialize()
        assert result is True
        assert registry._initialized is True

    @pytest.mark.asyncio
    async def test_register_new_client(self, registry):
        """Test registering a new client."""
        await registry.initialize()

        client = await registry.register_client(
            mac_id="local",
            name="Main",
            ip="127.0.0.1"
        )

        assert client.mac_id == "local"
        assert registry.get_client("local") is not None

    @pytest.mark.asyncio
    async def test_register_client_with_speaker_type(self, registry):
        """Test registering a client with speaker type."""
        await registry.initialize()

        client = await registry.register_client(
            mac_id="aa:bb:cc:dd:ee:ff",
            name="Kitchen",
            ip="192.168.1.100",
            speaker_type="satellite"
        )

        assert client.speaker_type == "satellite"

    @pytest.mark.asyncio
    async def test_update_existing_client(self, registry):
        """Test that registering same mac_id returns existing client."""
        await registry.initialize()

        client1 = await registry.register_client(
            mac_id="local",
            name="Main",
            ip="127.0.0.1"
        )

        # Register same mac_id - should return existing client
        client2 = await registry.register_client(
            mac_id="local",
            name="Main",
            ip="127.0.0.1"
        )

        assert client1.mac_id == client2.mac_id

    @pytest.mark.asyncio
    async def test_unregister_client(self, registry):
        """Test unregistering (deleting) a client."""
        await registry.initialize()

        await registry.register_client(
            mac_id="local",
            name="Main",
            ip="127.0.0.1"
        )

        result = await registry.unregister_client("local")
        assert result is True
        assert registry.get_client("local") is None

    @pytest.mark.asyncio
    async def test_unregister_nonexistent_client(self, registry):
        """Test unregistering a client that doesn't exist."""
        await registry.initialize()
        result = await registry.unregister_client("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_set_client_online(self, registry):
        """Test setting client online status."""
        await registry.initialize()

        await registry.register_client(
            mac_id="local",
            name="Main",
            ip="127.0.0.1"
        )

        await registry.set_client_online("local", True)
        client = registry.get_client("local")
        assert client.online is True

        await registry.set_client_online("local", False)
        client = registry.get_client("local")
        assert client.online is False

    @pytest.mark.asyncio
    async def test_update_client(self, registry):
        """Test updating client properties."""
        await registry.initialize()

        await registry.register_client(
            mac_id="local",
            name="Main",
            ip="127.0.0.1"
        )

        updated = await registry.update_client("local", name="Updated Main", speaker_type="tower")
        assert updated.name == "Updated Main"
        assert updated.speaker_type == "tower"

    @pytest.mark.asyncio
    async def test_get_online_clients(self, registry):
        """Test getting only online clients."""
        await registry.initialize()

        await registry.register_client(
            mac_id="local",
            name="Main",
            ip="127.0.0.1"
        )
        await registry.set_client_online("local", True)

        await registry.register_client(
            mac_id="aa:bb:cc:dd:ee:ff",
            name="Remote",
            ip="192.168.1.100"
        )
        # Remote client stays offline (default)

        online = registry.get_online_clients()
        assert len(online) == 1
        assert online[0].mac_id == "local"

    @pytest.mark.asyncio
    async def test_create_zone(self, registry):
        """Test creating a zone."""
        await registry.initialize()

        await registry.register_client(mac_id="local", name="Main", ip="127.0.0.1")
        await registry.register_client(mac_id="client2", name="Client 2", ip="192.168.1.100")

        zone = await registry.create_zone("zone1", "Test Zone", ["local", "client2"])

        assert zone.id == "zone1"
        assert zone.name == "Test Zone"
        assert "local" in zone.client_ids
        assert "client2" in zone.client_ids

        # Verify clients have zone_id set
        client1 = registry.get_client("local")
        client2 = registry.get_client("client2")
        assert client1.zone_id == "zone1"
        assert client2.zone_id == "zone1"

    @pytest.mark.asyncio
    async def test_create_zone_requires_minimum_clients(self, registry):
        """Test that creating a zone requires at least 2 clients."""
        await registry.initialize()

        await registry.register_client(mac_id="local", name="Main", ip="127.0.0.1")

        with pytest.raises(ValueError, match="at least 2 clients"):
            await registry.create_zone("zone1", "Test Zone", ["local"])

    @pytest.mark.asyncio
    async def test_create_zone_duplicate_id(self, registry):
        """Test creating a zone with duplicate ID."""
        await registry.initialize()

        await registry.register_client(mac_id="local", name="Main", ip="127.0.0.1")
        await registry.register_client(mac_id="client2", name="Client 2", ip="192.168.1.100")

        await registry.create_zone("zone1", "Test Zone", ["local", "client2"])

        with pytest.raises(ValueError, match="already exists"):
            await registry.create_zone("zone1", "Another Zone", ["local", "client2"])

    @pytest.mark.asyncio
    async def test_delete_zone(self, registry):
        """Test deleting a zone."""
        await registry.initialize()

        await registry.register_client(mac_id="local", name="Main", ip="127.0.0.1")
        await registry.register_client(mac_id="client2", name="Client 2", ip="192.168.1.100")

        await registry.create_zone("zone1", "Test Zone", ["local", "client2"])
        result = await registry.delete_zone("zone1")

        assert result is True
        assert registry.get_zone("zone1") is None

        # Verify clients have zone_id cleared
        client1 = registry.get_client("local")
        client2 = registry.get_client("client2")
        assert client1.zone_id is None
        assert client2.zone_id is None

    @pytest.mark.asyncio
    async def test_get_zone_for_client(self, registry):
        """Test getting the zone a client belongs to."""
        await registry.initialize()

        await registry.register_client(mac_id="local", name="Main", ip="127.0.0.1")
        await registry.register_client(mac_id="client2", name="Client 2", ip="192.168.1.100")

        await registry.create_zone("zone1", "Test Zone", ["local", "client2"])

        zone = registry.get_zone_for_client("local")
        assert zone is not None
        assert zone.id == "zone1"

    @pytest.mark.asyncio
    async def test_add_client_to_zone(self, registry):
        """Test adding a client to a zone."""
        await registry.initialize()

        await registry.register_client(mac_id="local", name="Main", ip="127.0.0.1")
        await registry.register_client(mac_id="client2", name="Client 2", ip="192.168.1.100")
        await registry.register_client(mac_id="client3", name="Client 3", ip="192.168.1.101")

        await registry.create_zone("zone1", "Test Zone", ["local", "client2"])
        result = await registry.add_client_to_zone("zone1", "client3")

        assert result is True
        zone = registry.get_zone("zone1")
        assert "client3" in zone.client_ids

        client3 = registry.get_client("client3")
        assert client3.zone_id == "zone1"

    @pytest.mark.asyncio
    async def test_remove_client_from_zone(self, registry):
        """Test removing a client from a zone."""
        await registry.initialize()

        await registry.register_client(mac_id="local", name="Main", ip="127.0.0.1")
        await registry.register_client(mac_id="client2", name="Client 2", ip="192.168.1.100")
        await registry.register_client(mac_id="client3", name="Client 3", ip="192.168.1.101")

        await registry.create_zone("zone1", "Test Zone", ["local", "client2", "client3"])
        result = await registry.remove_client_from_zone("zone1", "client3")

        assert result is True
        zone = registry.get_zone("zone1")
        assert "client3" not in zone.client_ids

        client3 = registry.get_client("client3")
        assert client3.zone_id is None

    def test_compute_mac_id_local(self):
        """Test computing mac_id for local client reads from system interface."""
        # Local client (127.0.0.1) reads MAC from eth0 or wlan0
        mac_id = compute_mac_id("milo", "127.0.0.1")
        # Should be a valid MAC address format
        assert ":" in mac_id
        assert len(mac_id) == 17  # xx:xx:xx:xx:xx:xx

    def test_compute_mac_id_uses_snapcast_client_id(self):
        """Remote clients are keyed by the id Milō assigned via --hostID.

        Snapcast's host.mac reports the interface the client connected through,
        which on a wifi-only client is wlan0 while it registers under eth0 —
        two identities for one device.
        """
        mac_id = compute_mac_id(
            "milo-client-kitchen", "192.168.1.100", host_id="aa:bb:cc:dd:ee:ff"
        )
        assert mac_id == "aa:bb:cc:dd:ee:ff"

    def test_compute_mac_id_remote_no_id_raises(self):
        """Test that a remote client announcing no id raises ValueError."""
        with pytest.raises(ValueError, match="No client id"):
            compute_mac_id("unknown-host", "192.168.1.200")

    def test_compute_mac_id_ignores_null_id(self):
        """Test that a null MAC (00:00:00:00:00:00) as id is rejected."""
        with pytest.raises(ValueError, match="No client id"):
            compute_mac_id("client", "192.168.1.200", host_id="00:00:00:00:00:00")

    @pytest.mark.asyncio
    async def test_client_equalizer_storage(self, registry):
        """Test standalone Equalizer settings storage."""
        from backend.core.multiroom.models import EqFilter

        await registry.initialize()

        await registry.register_client(mac_id="local", name="Main", ip="127.0.0.1")

        # Initially no standalone Equalizer
        assert registry.get_client_equalizer("local") is None

        # Set standalone Equalizer with typed EqFilter
        eq = EqualizerSettings(filters=[EqFilter(id="eq_band_00", frequency=1000, gain=3.0)])
        await registry.set_client_equalizer("local", eq)

        # Retrieve standalone Equalizer
        retrieved = registry.get_client_equalizer("local")
        assert retrieved is not None
        assert len(retrieved.filters) == 1
        assert retrieved.filters[0].frequency == 1000

    @pytest.mark.asyncio
    async def test_a_deferred_eq_write_costs_one_persist_for_the_whole_drag(
        self, registry, mock_settings_service
    ):
        """An EQ band drag must not rewrite settings.json 20 times a second.

        Measured on the appliance: a 3 s drag on a zone emitted 61 throttled
        requests and 61 full rewrites + fsyncs, one per remote member per
        request — 1.0 MB of block writes on the SD card for one gesture. Every
        other registry mutation is a discrete event and keeps its immediate
        write; only this streamed one defers. The flush belongs to the shutdown
        path, so the last value of a drag survives a restart.
        """
        from backend.core.multiroom.models import EqFilter

        await registry.initialize()
        await registry.register_client(mac_id="client2", name="Client 2", ip="192.168.1.100")
        mock_settings_service.set_settings.reset_mock()

        for gain in range(6):
            await registry.set_clients_equalizer(
                {"client2": EqualizerSettings(
                    filters=[EqFilter(id="eq_band_00", frequency=1000, gain=float(gain))]
                )},
                broadcast=False,
                defer_persist=True,
            )

        mock_settings_service.set_settings.assert_not_called()

        await registry.cleanup()

        mock_settings_service.set_settings.assert_awaited_once()
        persisted = mock_settings_service.set_settings.await_args[0][0]
        assert persisted["multiroom.client_equalizer"]["client2"]["filters"][0]["gain"] == 5.0

    @pytest.mark.asyncio
    async def test_a_deferred_eq_write_lands_on_its_own_without_a_shutdown(
        self, registry, mock_settings_service
    ):
        """The debounce must fire by itself, not only when something flushes it.

        It did not: `_persist_state` cancels a pending debounce so an immediate
        write supersedes it, and the debounced task reaches that line through its
        own timer — so it cancelled itself and the drag reached the disk only at
        shutdown. Invisible to the sibling test above, which flushes through
        cleanup() (a different task), and caught on the appliance by watching
        settings.json not move for three seconds after a band change.
        """
        from backend.core.multiroom.models import EqFilter

        registry.PERSIST_DEBOUNCE_S = 0
        await registry.initialize()
        await registry.register_client(mac_id="client2", name="Client 2", ip="192.168.1.100")
        mock_settings_service.set_settings.reset_mock()
        # A settings write is file I/O: it suspends, and only a suspension
        # delivers a cancellation. `assert_awaited` would not see this bug — the
        # call *is* entered — so what is asserted is that it came back.
        landed = []

        async def _yielding_write(updates):
            await asyncio.sleep(0)
            landed.append(updates)
            return True

        mock_settings_service.set_settings.side_effect = _yielding_write

        await registry.set_clients_equalizer(
            {"client2": EqualizerSettings(
                filters=[EqFilter(id="eq_band_00", frequency=1000, gain=7.0)]
            )},
            broadcast=False,
            defer_persist=True,
        )
        for _ in range(5):
            await asyncio.sleep(0)

        assert len(landed) == 1, "the debounced write never completed"
        assert landed[0]["multiroom.client_equalizer"]["client2"]["filters"][0]["gain"] == 7.0

    @pytest.mark.asyncio
    async def test_client_equalizer_broadcast_uses_wire_shape(self, registry):
        """The EQUALIZER_SETTINGS_CHANGED broadcast must carry filters in the
        frontend wire shape (freq/type), not the model's frequency/filter_type —
        the store's WS handler reads freq/type."""
        from backend.core.multiroom.models import EqFilter

        await registry.initialize()
        await registry.register_client(mac_id="client2", name="Client 2", ip="192.168.1.100")

        events = []

        async def capture(event_type, data):
            events.append((event_type, data))

        registry.subscribe(capture)

        eq = EqualizerSettings(filters=[EqFilter(id="eq_band_00", frequency=1000, gain=3.0)])
        await registry.set_client_equalizer("client2", eq)

        eq_events = [d for (t, d) in events if t == RegistryEventType.EQUALIZER_SETTINGS_CHANGED]
        assert eq_events, "expected an EQUALIZER_SETTINGS_CHANGED broadcast"
        flt = eq_events[0]["equalizer_settings"]["filters"][0]
        assert "freq" in flt and "type" in flt
        assert "frequency" not in flt and "filter_type" not in flt

    @pytest.mark.asyncio
    async def test_client_equalizer_kept_on_zone_join(self, registry):
        """A client's own EQ record is NOT cleared by the registry when it joins a
        zone — members own their record (the access layer overwrites it with the
        zone's neutral EQ in production)."""
        from backend.core.multiroom.models import EqFilter

        await registry.initialize()

        await registry.register_client(mac_id="local", name="Main", ip="127.0.0.1")
        await registry.register_client(mac_id="client2", name="Client 2", ip="192.168.1.100")

        eq = EqualizerSettings(filters=[EqFilter(id="eq_band_00", frequency=1000)])
        await registry.set_client_equalizer("client2", eq)

        await registry.create_zone("zone1", "Test Zone", ["local", "client2"])

        # The registry leaves the record in place (no implicit clear).
        assert registry.get_client_equalizer("client2") is not None

    @pytest.mark.asyncio
    async def test_client_equalizer_retained_on_leave(self, registry):
        """Leaving a zone keeps the client's own EQ record (zones hold no EQ of
        their own; the member already owns its record)."""
        from backend.core.multiroom.models import EqFilter

        await registry.initialize()

        await registry.register_client(mac_id="local", name="Main", ip="127.0.0.1")
        await registry.register_client(mac_id="client2", name="Client 2", ip="192.168.1.100")
        await registry.register_client(mac_id="client3", name="Client 3", ip="192.168.1.101")

        await registry.create_zone("zone1", "Test Zone", ["local", "client2", "client3"])

        # The access layer sets each member's record; emulate that for client3.
        eq = EqualizerSettings(filters=[EqFilter(id="eq_band_00", frequency=2000, gain=-5.0)])
        await registry.set_client_equalizer("client3", eq)

        # Remove client3 from zone — its record must survive.
        result = await registry.remove_client_from_zone("zone1", "client3")
        assert result is True

        retained = registry.get_client_equalizer("client3")
        assert retained is not None
        assert len(retained.filters) == 1
        assert retained.filters[0].frequency == 2000
        assert retained.filters[0].gain == -5.0

    @pytest.mark.asyncio
    async def test_thread_safety_concurrent_operations(self, registry):
        """Test thread safety with concurrent client operations."""
        await registry.initialize()

        # Register initial client
        await registry.register_client(
            mac_id="local",
            name="Main",
            ip="127.0.0.1"
        )

        # Run multiple concurrent operations on the same client
        async def update_name(suffix):
            await registry.update_client("local", name=f"Main-{suffix}")

        async def set_online(status):
            await registry.set_client_online("local", status)

        # Execute 10 concurrent operations
        operations = []
        for i in range(5):
            operations.append(update_name(i))
            operations.append(set_online(i % 2 == 0))

        # All operations should complete without errors (no race conditions)
        await asyncio.gather(*operations)

        # Client should still exist and be consistent
        client = registry.get_client("local")
        assert client is not None
        assert client.mac_id == "local"

    @pytest.mark.asyncio
    async def test_persistence_called_on_register(self, registry, mock_settings_service):
        """Registering a client persists it, through the registry's one write path."""
        await registry.initialize()

        await registry.register_client(
            mac_id="test-client",
            name="Test",
            ip="192.168.1.50"
        )

        written = {}
        for call in mock_settings_service.set_settings.call_args_list:
            written.update(call[0][0])
        assert "test-client" in written["multiroom.clients"]

    @pytest.mark.asyncio
    async def test_initialization_loads_clients_offline(self, mock_settings_service):
        """Test that initialization loads clients with online=False."""
        # Setup mock to return persisted client data
        mock_settings_service.get_setting = AsyncMock(side_effect=lambda key, default=None: {
            "multiroom.clients": {
                "client-1": {
                    "mac_id": "client-1",
                    "name": "Persisted Client",
                    "ip": "192.168.1.100",
                    "speaker_type": "bookshelf"
                }
            }
        }.get(key, default))

        registry = ClientRegistryService(
            settings_service=mock_settings_service
        )

        await registry.initialize()

        # Client should be loaded with online=False
        client = registry.get_client("client-1")
        assert client is not None
        assert client.name == "Persisted Client"
        assert client.online is False  # Runtime state always starts offline


class TestNoDanglingZoneReference:
    """A client must never keep a zone_id for a zone that no longer exists.

    A dangling reference is not cosmetic: MultiroomEqualizerService raises
    ValueError("client is in zone X") for a client that is actually standalone,
    so PUT /api/equalizer/target/<mac> fails for that speaker permanently — the
    reference is persisted.

    Every mutation that can drop a zone below its 2-member minimum is driven
    here; unregister_client was the one that did not detach the survivors.
    """

    @pytest.fixture
    def mock_settings_service(self):
        service = AsyncMock()
        service.get_setting = AsyncMock(return_value=None)
        service.set_setting = AsyncMock()
        return service

    @pytest.fixture
    def registry(self, mock_settings_service):
        return ClientRegistryService(settings_service=mock_settings_service)

    def _assert_no_dangling(self, registry):
        zones = registry.get_all_zones()
        for mac_id, client in registry.get_all_clients().items():
            assert client.zone_id is None or client.zone_id in zones, (
                f"client {mac_id} references zone {client.zone_id}, "
                f"which does not exist (zones: {sorted(zones)})"
            )

    async def _pair(self, registry, prefix, ip_offset):
        """Register two clients in a zone, return (zone_id, macs)."""
        macs = [f"{prefix}-1", f"{prefix}-2"]
        for i, mac in enumerate(macs):
            await registry.register_client(
                mac_id=mac, name=mac, ip=f"192.168.1.{ip_offset + i}"
            )
        zone = await registry.create_zone(f"zone-{prefix}", prefix, macs)
        return zone.id, macs

    @pytest.mark.asyncio
    async def test_unregister_last_but_one_member(self, registry, mock_settings_service):
        """Forgetting a client that leaves its zone with a single member."""
        await registry.initialize()
        zone_id, macs = await self._pair(registry, "a", 10)

        await registry.unregister_client(macs[0])

        assert registry.get_zone(zone_id) is None
        self._assert_no_dangling(registry)
        # The reference must not survive a reboot either: assert what the
        # service actually wrote, not what this test built.
        persisted = mock_settings_service.set_settings.call_args[0][0]
        for mac, data in persisted["multiroom.clients"].items():
            assert data["zone_id"] is None or data["zone_id"] in persisted["multiroom.zones"], (
                f"persisted client {mac} references missing zone {data['zone_id']}"
            )

    @pytest.mark.asyncio
    async def test_remove_last_but_one_member_from_zone(self, registry):
        """Explicitly removing a member down to one."""
        await registry.initialize()
        zone_id, macs = await self._pair(registry, "b", 20)

        await registry.remove_client_from_zone(zone_id, macs[0])

        assert registry.get_zone(zone_id) is None
        self._assert_no_dangling(registry)

    @pytest.mark.asyncio
    async def test_moving_a_member_to_another_zone(self, registry):
        """Moving a client out empties its old zone below the minimum."""
        await registry.initialize()
        old_zone_id, old_macs = await self._pair(registry, "c", 30)
        new_zone_id, _ = await self._pair(registry, "d", 40)

        await registry.add_client_to_zone(new_zone_id, old_macs[0])

        assert registry.get_zone(old_zone_id) is None
        self._assert_no_dangling(registry)

    @pytest.mark.asyncio
    async def test_delete_zone(self, registry):
        """Deleting a zone outright."""
        await registry.initialize()
        zone_id, _ = await self._pair(registry, "e", 50)

        await registry.delete_zone(zone_id)

        assert registry.get_zone(zone_id) is None
        self._assert_no_dangling(registry)

    @pytest.mark.asyncio
    async def test_standalone_client_can_still_take_an_eq_write(self, registry):
        """The consequence, end to end: the survivor is addressable as a client.

        Mirrors MultiroomEqualizerService's guard rather than importing it, so
        this stays a registry test: zone_id is what that guard reads.
        """
        await registry.initialize()
        _, macs = await self._pair(registry, "f", 60)

        await registry.unregister_client(macs[0])
        survivor = registry.get_client(macs[1])

        assert survivor.zone_id is None
        assert registry.get_zone_for_client(macs[1]) is None


# =============================================================================
# SnapcastService Tests
# =============================================================================

def _server_status_with_streams(*, chunk_ms: str, codec: str) -> dict:
    """A Server.GetStatus result in the shape the real daemon answers.

    Captured from the snapserver running on this appliance (2026-08-25): the
    result nests groups AND streams under "server", and every stream carries the
    global `[stream]` defaults in its source URI query — so the first one is
    representative. Getting this shape wrong is not cosmetic: it is what let
    `get_server_config` read a top-level "streams" that is never there.
    """
    return {"server": {"groups": [], "streams": [
        {"id": "Multiroom", "uri": {"scheme": "meta", "query": {
            "chunk_ms": chunk_ms, "codec": codec, "name": "Multiroom",
            "sampleformat": "48000:32:2"}}},
        {"id": "Spotify", "uri": {"scheme": "alsa", "query": {
            "chunk_ms": chunk_ms, "codec": codec, "name": "Spotify",
            "sampleformat": "48000:32:2"}}},
    ]}}


class TestSnapcastService:
    """Tests for SnapcastService."""

    @pytest.fixture
    def snapcast_service(self):
        """Create a SnapcastService instance."""
        return SnapcastService(systemd_manager=MagicMock())

    def test_compute_mac_id_local_via_service(self, snapcast_service):
        """Test computing mac_id for local client via ClientRegistryService."""
        # mac_id derivation moved to compute_mac_id()
        # Local client reads MAC from system interface
        mac_id = compute_mac_id("milo", "127.0.0.1")
        assert ":" in mac_id  # Returns a MAC address format
        assert len(mac_id) == 17  # xx:xx:xx:xx:xx:xx

    def test_deduplicate_by_mac_empty(self, snapcast_service):
        """Test deduplication with empty list."""
        result = snapcast_service._deduplicate_by_mac([])
        assert result == []

    def test_deduplicate_by_mac_no_duplicates(self, snapcast_service):
        """Test deduplication with no duplicates (uses mac_id as key)."""
        clients = [
            {"id": "c1", "mac_id": "aa:bb:cc:dd:ee:01", "ip": "192.168.1.1"},
            {"id": "c2", "mac_id": "aa:bb:cc:dd:ee:02", "ip": "192.168.1.2"},
        ]
        result = snapcast_service._deduplicate_by_mac(clients)
        assert len(result) == 2

    def test_deduplicate_by_mac_with_duplicates(self, snapcast_service):
        """Test deduplication with duplicate mac_id."""
        clients = [
            {"id": "c1", "mac_id": "aa:bb:cc:dd:ee:01", "ip": "192.168.1.1"},
            {"id": "c2", "mac_id": "aa:bb:cc:dd:ee:01", "ip": "192.168.1.2"},  # Same mac_id
        ]
        result = snapcast_service._deduplicate_by_mac(clients)
        assert len(result) == 1
        assert result[0]["id"] == "c1"

    def test_validate_config_valid(self, snapcast_service):
        """Test config validation with valid config."""
        config = {
            "buffer_ms": 500,
            "codec": "flac",
            "chunk_ms": 20
        }
        assert snapcast_service._validate_config(config) is True

    def test_validate_config_invalid_buffer(self, snapcast_service):
        """Test config validation with invalid buffer."""
        config = {"buffer_ms": 50}  # Too small
        assert snapcast_service._validate_config(config) is False

    def test_validate_config_invalid_codec(self, snapcast_service):
        """Test config validation with invalid codec."""
        config = {"codec": "mp3"}  # Not supported
        assert snapcast_service._validate_config(config) is False

    def test_validate_config_rejects_an_unknown_key(self, snapcast_service):
        """A body in the wrong shape must be refused, not silently ignored.

        Validating only recognised keys let the nested read shape pass, write
        nothing, return success and restart snapserver anyway — the one way
        this endpoint can lie about having applied a change.
        """
        nested = {"stream_config": {"buffer_ms": 500, "codec": "flac", "chunk_ms": 20}}
        assert snapcast_service._validate_config(nested) is False

    def test_validate_config_accepts_the_shape_the_read_returns(self, snapcast_service):
        """The other half: what GET /server-config serves must be a legal write
        body, or the unknown-key rule above would reject the app's own payload."""
        config = {
            "buffer_ms": 500,
            "chunk_ms": 20,
            "codec": "flac",
            "sampleformat": "48000:32:2",
        }
        assert snapcast_service._validate_config(config) is True

    def test_every_preset_is_a_config_the_api_would_accept(self, snapcast_service):
        """A preset the UI offers must survive the route it is sent back through.

        The presets are served as capabilities and re-sent verbatim as a write
        body, split the way the route splits it: the snapclient key leaves
        `config` and is gated by SNAPCLIENT_LIMITS, the rest is gated by
        `_validate_config`. A preset outside either bound is silently altered on
        the way in, and the settings page then never shows it as the active one.
        """
        from backend.core.multiroom.snapcast import NETWORK_PRESETS

        assert NETWORK_PRESETS, "no presets to check"
        low, high = SNAPCLIENT_LIMITS["buffer_time"]
        for preset in NETWORK_PRESETS:
            config = dict(preset["config"])
            buffer_time = config.pop("snapclient_buffer_time")
            assert low <= buffer_time <= high, f"{preset['id']}: {buffer_time} ms is outside the accepted range"
            assert snapcast_service._validate_config(config) is True, f"{preset['id']} is not a legal write body"

    @pytest.mark.asyncio
    async def test_read_and_write_of_server_config_agree_on_one_body(self, snapcast_service):
        """What GET serves must be what PUT consumes — driven, not restated.

        The two used to disagree (nested on read, flat on write) and only the
        frontend's translation hid it. Feeding the real read into the real
        validator is what keeps them from drifting apart again.
        """
        snapcast_service._request = AsyncMock(return_value=_server_status_with_streams(
            chunk_ms="20", codec="flac"))
        snapcast_service._read_snapserver_conf = AsyncMock(return_value={
            "parsed_config": {"stream": {"buffer": "700"}}
        })

        config = await snapcast_service.get_server_config()

        assert config["buffer_ms"] == 700  # non-vacuous: the read really produced values
        assert snapcast_service._validate_config(config) is True

    @pytest.mark.asyncio
    async def test_the_running_daemon_wins_over_the_file(self, snapcast_service):
        """What the settings page reports must be what snapserver plays.

        The file is written before the restart, and the restart can be refused
        (`update_server_config` then returns False and the route answers 502) —
        at which point the file holds the new preset and the daemon still runs
        the old one. Reading the daemon is the only way that shows.

        Server.GetStatus nests streams under "server", the way it nests groups;
        reading them from the top level silently found nothing and the merge
        collapsed to the file, which is what this pins. Shape measured against
        the live snapserver on this appliance, 2026-08-25.
        """
        snapcast_service._request = AsyncMock(return_value=_server_status_with_streams(
            chunk_ms="40", codec="opus"))
        snapcast_service._read_snapserver_conf = AsyncMock(return_value={
            "parsed_config": {"stream": {"buffer": "700", "chunk_ms": "20", "codec": "flac"}}
        })

        config = await snapcast_service.get_server_config()

        assert config["chunk_ms"] == 40
        assert config["codec"] == "opus"
        # buffer_ms has no daemon counterpart at all — the file is its only source.
        assert config["buffer_ms"] == 700

    @pytest.mark.asyncio
    async def test_the_file_answers_when_the_daemon_lists_no_stream(self, snapcast_service):
        """A snapserver with no stream configured must not blank the page."""
        snapcast_service._request = AsyncMock(return_value={"server": {"streams": []}})
        snapcast_service._read_snapserver_conf = AsyncMock(return_value={
            "parsed_config": {"stream": {"buffer": "700", "chunk_ms": "20", "codec": "flac"}}
        })

        config = await snapcast_service.get_server_config()

        assert config == {"buffer_ms": 700, "chunk_ms": 20, "codec": "flac",
                          "sampleformat": "48000:32:2"}

    @pytest.mark.asyncio
    async def test_is_available_connection_error(self, snapcast_service):
        """Test is_available with connection error."""
        with patch("backend.core.multiroom.snapcast.aiohttp.ClientSession") as mock_session:
            mock_session.return_value.__aenter__ = AsyncMock(side_effect=Exception("Connection refused"))
            result = await snapcast_service.is_available()
            assert result is False

    @staticmethod
    def _mock_jsonrpc_response(status=200, payload=None):
        """Build a patch target for aiohttp returning one JSON-RPC response."""
        response = MagicMock()
        response.status = status
        response.json = AsyncMock(return_value=payload if payload is not None else {})
        post_ctx = MagicMock()
        post_ctx.__aenter__ = AsyncMock(return_value=response)
        post_ctx.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.post = MagicMock(return_value=post_ctx)
        session_ctx = MagicMock()
        session_ctx.__aenter__ = AsyncMock(return_value=session)
        session_ctx.__aexit__ = AsyncMock(return_value=False)
        return patch("backend.core.multiroom.snapcast.aiohttp.ClientSession", return_value=session_ctx)

    @pytest.mark.asyncio
    async def test_request_raises_on_transport_error(self, snapcast_service):
        """A transport failure must raise, not be swallowed to {}."""
        with patch("backend.core.multiroom.snapcast.aiohttp.ClientSession") as mock_session:
            mock_session.return_value.__aenter__ = AsyncMock(side_effect=OSError("unreachable"))
            with pytest.raises(SnapcastRequestError):
                await snapcast_service._request("Server.GetStatus")

    @pytest.mark.asyncio
    async def test_request_raises_on_non_200(self, snapcast_service):
        """A non-200 HTTP status must raise."""
        with self._mock_jsonrpc_response(status=500):
            with pytest.raises(SnapcastRequestError):
                await snapcast_service._request("Server.GetStatus")

    @pytest.mark.asyncio
    async def test_request_raises_on_jsonrpc_error(self, snapcast_service):
        """A JSON-RPC error object must raise even with HTTP 200."""
        with self._mock_jsonrpc_response(payload={"error": {"code": -32601, "message": "nope"}}):
            with pytest.raises(SnapcastRequestError):
                await snapcast_service._request("Bogus.Method")

    @pytest.mark.asyncio
    async def test_request_passes_through_valid_empty_result(self, snapcast_service):
        """A valid but empty result ({}) is returned, NOT treated as failure."""
        with self._mock_jsonrpc_response(payload={"jsonrpc": "2.0", "id": 1, "result": {}}):
            result = await snapcast_service._request("Server.GetStatus")
            assert result == {}

    @pytest.mark.asyncio
    async def test_set_volume_returns_false_on_rpc_error(self, snapcast_service):
        """set_volume fails loud (returns False) when the RPC errors — the old
        bool({}) ambiguity is gone."""
        with self._mock_jsonrpc_response(status=503):
            assert await snapcast_service.set_volume("client-1", 50) is False

    @pytest.mark.asyncio
    async def test_set_volume_returns_true_on_success(self, snapcast_service):
        """set_volume returns True when the RPC applies."""
        with self._mock_jsonrpc_response(payload={"result": {"volume": {"percent": 50, "muted": False}}}):
            assert await snapcast_service.set_volume("client-1", 50) is True

    @pytest.mark.asyncio
    async def test_set_latency_emits_the_setlatency_command(self, snapcast_service):
        """set_latency issues Client.SetLatency with the client id and ms — the
        exact wire command the per-client delay rides on. Asserted against the
        JSON-RPC boundary because that command IS the contract with snapserver."""
        snapcast_service._request = AsyncMock(return_value={})
        assert await snapcast_service.set_latency("client-1", 40) is True
        snapcast_service._request.assert_awaited_once_with(
            "Client.SetLatency", {"id": "client-1", "latency": 40}
        )

    @pytest.mark.asyncio
    async def test_set_latency_clamps_negative_to_zero(self, snapcast_service):
        """A negative delay is clamped, never sent as-is."""
        snapcast_service._request = AsyncMock(return_value={})
        await snapcast_service.set_latency("client-1", -5)
        assert snapcast_service._request.await_args.args[1]["latency"] == 0

    @pytest.mark.asyncio
    async def test_set_latency_returns_false_on_rpc_error(self, snapcast_service):
        """set_latency fails loud (returns False) when the RPC errors."""
        with self._mock_jsonrpc_response(status=503):
            assert await snapcast_service.set_latency("client-1", 40) is False


# =============================================================================
# Helper Function Tests
# =============================================================================

class TestHelperFunctions:
    """Tests for helper functions."""

    def test_is_ip_address_true(self):
        """Test is_ip_address with valid IP."""
        assert is_ip_address("192.168.1.100") is True
        assert is_ip_address("10.0.0.1") is True

    def test_is_ip_address_false(self):
        """Test is_ip_address with hostname."""
        assert is_ip_address("milo-client-kitchen") is False
        assert is_ip_address("localhost") is False


# =============================================================================
# CrossoverService Tests
# =============================================================================

class TestCrossoverService:
    """Tests for CrossoverService."""

    @pytest.fixture
    def mock_settings_service(self):
        """Create a mock settings service."""
        service = AsyncMock()
        service.get_setting = AsyncMock(return_value=None)
        service.set_setting = AsyncMock()
        return service

    @pytest.fixture
    def mock_camilladsp_service(self):
        """Create a mock Equalizer service."""
        service = AsyncMock()
        service.set_crossover_filter = AsyncMock(return_value=True)
        service.set_lowpass_filter = AsyncMock(return_value=True)
        service.set_mute = AsyncMock(return_value=True)
        return service

    @pytest.fixture
    def mock_registry(self):
        """Create a mock client registry."""
        registry = MagicMock()
        registry.get_client = MagicMock(return_value=None)
        registry.get_zone = MagicMock(return_value=None)
        registry.get_zone_for_client = MagicMock(return_value=None)
        registry.is_client_online = MagicMock(return_value=True)
        registry.subscribe = MagicMock()
        return registry

    @pytest.fixture
    def crossover_service(self, mock_settings_service, mock_camilladsp_service, mock_registry):
        """Create a CrossoverService instance with mock registry for local client."""
        # Configure mock registry to return local client
        local_client = MagicMock()
        local_client.ip = "127.0.0.1"
        local_client.is_local = True
        local_client.mac_id = "aa:bb:cc:dd:ee:ff"
        mock_registry.get_client = MagicMock(side_effect=lambda x: local_client if x == "aa:bb:cc:dd:ee:ff" else None)

        service = CrossoverService(
            settings_service=mock_settings_service,
            camilladsp_service=mock_camilladsp_service
        )
        # Set registry via setter (not constructor parameter)
        service.set_registry(mock_registry)
        return service

    @pytest.mark.asyncio
    async def test_initialize(self, crossover_service):
        """Test crossover service initialization."""
        result = await crossover_service.initialize()
        assert result is True

    def test_is_client_subwoofer_default(self, crossover_service):
        """Test is_client_subwoofer with default speaker type."""
        result = crossover_service.is_client_subwoofer("unknown_client")
        assert result is False

    def test_get_client_speaker_type_default(self, crossover_service):
        """Test getting default speaker type."""
        result = crossover_service.get_client_speaker_type("unknown_client")
        assert result == DEFAULT_SPEAKER_TYPE

    def test_has_pending_settings_empty(self, crossover_service):
        """Test has_pending_settings with no pending settings."""
        assert crossover_service.has_pending_settings("aa:bb:cc:dd:ee:ff") is False

    @pytest.mark.asyncio
    async def test_queue_pending_settings(self, crossover_service):
        """Test queuing pending settings."""
        await crossover_service.queue_pending_settings("192.168.1.100", "crossover", {
            "enabled": True,
            "frequency": 80
        })

        assert crossover_service.has_pending_settings("192.168.1.100") is True

    def test_clear_pending_settings(self, crossover_service):
        """Test clearing pending settings."""
        crossover_service._pending_settings["192.168.1.100"] = {
            "crossover": {"enabled": True}
        }

        crossover_service.clear_pending_settings("192.168.1.100")
        assert crossover_service.has_pending_settings("192.168.1.100") is False

    @pytest.mark.asyncio
    async def test_set_client_crossover_local(self, crossover_service, mock_camilladsp_service):
        """Test setting crossover on local client (identified by mac_id with ip=127.0.0.1)."""
        # Use the MAC address configured in the mock registry for local client
        result = await crossover_service._set_client_filter("aa:bb:cc:dd:ee:ff", "crossover", True, 80)
        assert result is True
        mock_camilladsp_service.set_crossover_filter.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_client_lowpass_local(self, crossover_service, mock_camilladsp_service):
        """Test setting lowpass on local client (identified by mac_id with ip=127.0.0.1)."""
        # Use the MAC address configured in the mock registry for local client
        result = await crossover_service._set_client_filter("aa:bb:cc:dd:ee:ff", "lowpass", True, 80)
        assert result is True
        mock_camilladsp_service.set_lowpass_filter.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup(self, crossover_service):
        """Test cleanup clears pending settings."""
        crossover_service._pending_settings["test"] = {"data": "value"}
        await crossover_service.cleanup()
        assert len(crossover_service._pending_settings) == 0


# =============================================================================
# Integration Tests
# =============================================================================

class TestMultiroomIntegration:
    """Integration tests for multiroom module."""

    @pytest.mark.asyncio
    async def test_registry_and_crossover_integration(self):
        """Test ClientRegistryService and CrossoverService integration."""
        # Create services
        mock_settings = AsyncMock()
        mock_settings.get_setting = AsyncMock(return_value=None)
        mock_settings.set_setting = AsyncMock()

        mock_camilladsp_mock = AsyncMock()
        mock_camilladsp_mock.set_crossover_filter = AsyncMock(return_value=True)
        mock_camilladsp_mock.set_lowpass_filter = AsyncMock(return_value=True)

        registry = ClientRegistryService(settings_service=mock_settings)
        crossover = CrossoverService(settings_service=mock_settings, camilladsp_service=mock_camilladsp_mock)

        # Initialize
        await registry.initialize()
        await crossover.initialize()

        # Set registry on crossover
        crossover.set_registry(registry)

        # Register a client
        await registry.register_client(
            mac_id="local",
            name="Main",
            ip="127.0.0.1"
        )

        # Verify client is in registry
        client = registry.get_client("local")
        assert client is not None
        assert client.speaker_type == DEFAULT_SPEAKER_TYPE


# =============================================================================
# Zone Equalizer Sync Tests (-)
# =============================================================================

class TestZoneDspSync:
    """Tests for zone Equalizer settings sync on client reconnection."""

    def test_equalizer_settings_to_dict(self):
        """Test EqualizerSettings serialization."""
        from backend.core.multiroom.models import EqFilter, CompressorSettings, LoudnessSettings

        eq = EqualizerSettings(
            enabled=True,
            filters=[EqFilter(id="eq_1", frequency=1000, gain=3.0, q=1.0)],
            compressor=CompressorSettings(enabled=True, threshold=-20, ratio=4.0),
            loudness=LoudnessSettings(enabled=True, high_boost=75)
        )

        data = eq.to_dict()

        assert len(data["filters"]) == 1
        assert data["filters"][0]["frequency"] == 1000
        assert data["compressor"]["enabled"] is True
        assert data["loudness"]["high_boost"] == 75

    def test_equalizer_settings_from_dict(self):
        """Test EqualizerSettings deserialization."""
        data = {
            "enabled": True,
            "filters": [{"id": "eq_1", "frequency": 500, "gain": -2.0, "q": 1.41, "filter_type": "Peaking", "enabled": True}],
            "compressor": {"enabled": False},
            "loudness": None
        }

        eq = EqualizerSettings.from_dict(data)

        assert len(eq.filters) == 1
        assert eq.filters[0].frequency == 500
        assert eq.compressor.enabled is False
        assert eq.loudness.enabled is False  # None becomes default LoudnessSettings


# =============================================================================
# Pending Equalizer Settings Queue Tests (-)
# =============================================================================

class TestPendingEqualizerSettings:
    """Tests for the pending EQ record queue for offline clients."""

    @pytest.fixture
    def mock_proxy(self):
        proxy = MagicMock()
        proxy.try_request = AsyncMock(return_value=200)
        proxy.apply_record = AsyncMock(return_value=True)
        return proxy

    @pytest.fixture
    def crossover_service(self, mock_proxy):
        """CrossoverService whose registry knows one remote satellite.

        Remote on purpose: an EQ record is only ever queued for a satellite —
        the local client's record is equalizer.json, restored by CamillaDSPService
        itself, and the reconnection sync that produces these entries skips it.
        """
        mock_registry = MagicMock()
        satellite = MagicMock()
        satellite.ip = "192.168.1.100"
        satellite.is_local = False
        satellite.mac_id = "aa:bb:cc:dd:ee:ff"
        mock_registry.get_client = MagicMock(
            side_effect=lambda x: satellite if x == "aa:bb:cc:dd:ee:ff" else None
        )

        service = CrossoverService(
            settings_service=AsyncMock(),
            camilladsp_service=AsyncMock(),
            proxy_service=mock_proxy,
        )
        service.set_registry(mock_registry)
        return service

    @pytest.mark.asyncio
    async def test_queue_record_pending(self, crossover_service):
        from backend.core.multiroom.models import EqualizerSettings
        record = EqualizerSettings.default()

        await crossover_service.queue_pending_settings("aa:bb:cc:dd:ee:ff", "record", record)

        assert crossover_service.has_pending_settings("aa:bb:cc:dd:ee:ff")
        assert crossover_service._pending_settings["aa:bb:cc:dd:ee:ff"]["record"] is record

    @pytest.mark.asyncio
    async def test_apply_pending_record_pushes_it_whole(self, crossover_service, mock_proxy):
        from backend.core.multiroom.models import EqualizerSettings
        record = EqualizerSettings.default()
        await crossover_service.queue_pending_settings("aa:bb:cc:dd:ee:ff", "record", record)

        result = await crossover_service.apply_pending_settings("aa:bb:cc:dd:ee:ff")

        assert result is True
        mock_proxy.apply_record.assert_awaited_once_with("192.168.1.100", record)
        assert not crossover_service.has_pending_settings("aa:bb:cc:dd:ee:ff")

    @pytest.mark.asyncio
    async def test_failed_replay_is_reported(self, crossover_service, mock_proxy):
        """A replay that fails reports it, so the caller's retry loop keeps going."""
        from backend.core.multiroom.models import EqualizerSettings
        mock_proxy.apply_record.return_value = False
        await crossover_service.queue_pending_settings(
            "aa:bb:cc:dd:ee:ff", "record", EqualizerSettings.default()
        )

        assert await crossover_service.apply_pending_settings("aa:bb:cc:dd:ee:ff") is False

    @pytest.mark.asyncio
    async def test_clear_pending_after_apply(self, crossover_service):
        from backend.core.multiroom.models import EqualizerSettings
        await crossover_service.queue_pending_settings(
            "aa:bb:cc:dd:ee:ff", "record", EqualizerSettings.default()
        )
        await crossover_service.queue_pending_settings(
            "aa:bb:cc:dd:ee:ff", "crossover", {"enabled": True, "frequency": 80}
        )

        assert crossover_service.has_pending_settings("aa:bb:cc:dd:ee:ff")

        await crossover_service.apply_pending_settings("aa:bb:cc:dd:ee:ff")

        assert not crossover_service.has_pending_settings("aa:bb:cc:dd:ee:ff")


# =============================================================================
# TestStandaloneEqualizerSync -: Standalone client Equalizer settings sync
# =============================================================================


class TestStandaloneEqualizerSync:
    """Tests for standalone client behaviour.

    NOTE: standalone EQ persistence/sync moved to the registry
    standalone-equalizer store (single source of truth); those paths are covered
    by test_multiroom_equalizer_service.py (apply/get_client_equalizer) and
    test_multiroom_sync.py (reconnect sync). The old per-file sync-service tests
    were removed with EqualizerSettingsSyncService.
    """

    def test_compute_mac_id_localhost_returns_mac(self):
        """Local client (127.0.0.1) reads MAC from system interface."""
        # localhost IP reads MAC from system interface (eth0 or wlan0)
        mac_id = compute_mac_id("milo", "127.0.0.1")
        assert ":" in mac_id  # Returns MAC address format
        assert len(mac_id) == 17  # xx:xx:xx:xx:xx:xx


# =============================================================================
# TestWebSocketSyncStatus -: WebSocket events with sync status
# =============================================================================


# =============================================================================
# TestAutoCrossover -: Auto-crossover on subwoofer connect/disconnect
# =============================================================================


class TestAutoCrossover:
    """Tests for automatic crossover enable/disable based on subwoofer presence."""

    @pytest.fixture
    def mock_camilladsp(self):
        """Mock CamillaDSP service."""
        camilladsp_mock = AsyncMock()
        camilladsp_mock.set_crossover_filter = AsyncMock(return_value=True)
        camilladsp_mock.set_lowpass_filter = AsyncMock(return_value=True)
        return camilladsp_mock

    @pytest.fixture
    def crossover_service(self, mock_camilladsp):
        """Create CrossoverService with mocked dependencies."""
        from backend.core.multiroom.crossover import CrossoverService

        service = CrossoverService(camilladsp_service=mock_camilladsp)
        return service

    @pytest.fixture
    def mock_registry_with_subwoofer(self):
        """Create mock registry with zone containing speakers and subwoofer."""
        from unittest.mock import MagicMock

        registry = MagicMock()

        # Create mock clients with speaker types
        speaker1 = MagicMock()
        speaker1.speaker_type = "bookshelf"

        speaker2 = MagicMock()
        speaker2.speaker_type = "satellite"

        subwoofer = MagicMock()
        subwoofer.speaker_type = "subwoofer"

        clients = {
            "speaker-1": speaker1,
            "speaker-2": speaker2,
            "subwoofer-1": subwoofer
        }

        def get_client(cid):
            return clients.get(cid)

        # Create zone with speakers and subwoofer
        zone = MagicMock()
        zone.id = "zone-1"
        zone.client_ids = ["speaker-1", "speaker-2", "subwoofer-1"]

        registry._clients = clients
        registry.get_zone.return_value = zone
        registry.get_zone_for_client.return_value = zone
        registry.get_client.side_effect = get_client
        registry.is_client_online.side_effect = lambda cid: True
        registry.subscribe = MagicMock()
        registry._emit_event = AsyncMock()
        registry.zone_to_enriched_dict.return_value = {"id": "zone-1"}

        return registry

    @pytest.fixture
    def mock_registry_no_subwoofer(self):
        """Create mock registry with zone containing only speakers (no subwoofer)."""
        from unittest.mock import MagicMock

        registry = MagicMock()

        # Create mock clients - all speakers, no subwoofer
        speaker1 = MagicMock()
        speaker1.speaker_type = "bookshelf"

        speaker2 = MagicMock()
        speaker2.speaker_type = "satellite"

        tower = MagicMock()
        tower.speaker_type = "tower"

        clients = {
            "speaker-1": speaker1,
            "speaker-2": speaker2,
            "tower-1": tower
        }

        def get_client(cid):
            return clients.get(cid)

        # Create zone without subwoofer
        zone = MagicMock()
        zone.id = "zone-1"
        zone.client_ids = ["speaker-1", "speaker-2", "tower-1"]

        registry.get_zone.return_value = zone
        registry.get_zone_for_client.return_value = zone
        registry.get_client.side_effect = get_client
        registry.is_client_online.side_effect = lambda cid: True
        registry.subscribe = MagicMock()
        registry._emit_event = AsyncMock()
        registry.zone_to_enriched_dict.return_value = {"id": "zone-1"}

        return registry

    def test_crossover_should_apply_with_online_subwoofer(self, crossover_service, mock_registry_with_subwoofer):
        """Crossover enabled when subwoofer is online."""
        crossover_service.set_registry(mock_registry_with_subwoofer)

        # Verify subwoofer detection
        assert crossover_service.is_client_subwoofer("subwoofer-1") is True
        assert crossover_service.is_client_subwoofer("speaker-1") is False

    def test_crossover_frequency_calculation(self, crossover_service, mock_registry_with_subwoofer):
        """Frequency determined by speaker_type of zone members.

        Bookshelf (80) + satellite (120) + subwoofer: the satellite is the
        weakest, so it sets the zone's single highpass. Restating the table
        instead — which is what this test used to do — cannot fail.
        """
        registry = mock_registry_with_subwoofer
        crossover_service.set_registry(registry)
        zone = registry.get_zone("zone-1")

        assert ClientRegistryService.auto_crossover_frequency(registry, zone) == 120

    def test_no_crossover_without_subwoofer(self, crossover_service, mock_registry_no_subwoofer):
        """No crossover when zone has no subwoofer."""
        crossover_service.set_registry(mock_registry_no_subwoofer)

        # Verify no subwoofer detected
        has_sub = any(
            crossover_service.is_client_subwoofer(cid)
            for cid in ["speaker-1", "speaker-2", "tower-1"]
        )
        assert has_sub is False

    def test_multiple_subwoofers_detection(self, crossover_service):
        """Multiple subwoofers can be detected."""
        from unittest.mock import MagicMock

        registry = MagicMock()

        sub1 = MagicMock()
        sub1.speaker_type = "subwoofer"

        sub2 = MagicMock()
        sub2.speaker_type = "subwoofer"

        registry.get_client.side_effect = lambda cid: sub1 if cid == "sub-1" else sub2

        crossover_service.set_registry(registry)

        # Verify multiple subwoofers detected
        assert crossover_service.is_client_subwoofer("sub-1") is True
        assert crossover_service.is_client_subwoofer("sub-2") is True

    @pytest.mark.asyncio
    async def test_apply_zone_crossover_with_subwoofer(self, crossover_service, mock_registry_with_subwoofer):
        """Zone crossover applies highpass to speakers, lowpass to subwoofer."""
        crossover_service.set_registry(mock_registry_with_subwoofer)

        # Apply crossover
        result = await crossover_service.apply_zone_crossover("zone-1")

        # Should succeed
        assert result is True

    @pytest.mark.asyncio
    async def test_apply_zone_crossover_without_subwoofer(self, crossover_service, mock_registry_no_subwoofer):
        """Zone crossover disabled when no subwoofer present."""
        crossover_service.set_registry(mock_registry_no_subwoofer)

        # Apply crossover
        result = await crossover_service.apply_zone_crossover("zone-1")

        # Should succeed (but no filters applied)
        assert result is True

# =============================================================================
# Snapcast Client Detection Integration Tests
# =============================================================================


@pytest.mark.usefixtures("no_satellite_network")
class TestSnapcastClientDetection:
    """
    Tests for Integrate Snapcast Client Detection.

    Tests cover:
    - Client connection detection triggers registry update and WebSocket event
    - Client disconnection detection triggers registry update and WebSocket event
    - Auto-registration of new clients with default values
    - WebSocket event format compliance
    """

    @pytest.fixture
    def mock_settings_service(self):
        """Create a mock settings service."""
        service = AsyncMock()
        service.get_setting = AsyncMock(return_value=None)
        service.set_setting = AsyncMock()
        return service

    @pytest.fixture
    def registry(self, mock_settings_service):
        """Create an initialized registry."""
        reg = ClientRegistryService(
            settings_service=mock_settings_service
        )
        return reg

    @pytest.fixture
    def mock_state_machine(self):
        """Create a mock state machine with a typed broadcast."""
        sm = MagicMock()
        sm.broadcast = AsyncMock()
        return sm

    @pytest.fixture
    def mock_routing_service(self):
        """Create a mock routing service."""
        service = MagicMock()
        service.get_state = MagicMock(return_value={'multiroom_enabled': False})
        service.get_snapcast_status = AsyncMock(return_value={'multiroom_available': False})
        return service

    # === Client Connection Detection ===

    @pytest.mark.asyncio
    async def test_client_connect_registers_client(self, registry, mock_state_machine):
        """When Snapcast client connects, registry receives event and marks client online."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        await registry.initialize()
        attach_registry_broadcaster(registry, mock_state_machine)

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service.set_registry(registry)

        # Mock snapcast and volume services so volume sync succeeds
        mock_snapcast = MagicMock()
        mock_snapcast.set_volume = AsyncMock(return_value=True)
        mock_snapcast.set_latency = AsyncMock(return_value=True)
        mock_snapcast.get_clients = AsyncMock(return_value=[])
        ws_service._snapcast_service = mock_snapcast

        mock_volume_service = MagicMock()
        mock_volume_service.state_store = MagicMock()
        mock_volume_service.state_store.set_client_volume = AsyncMock()
        mock_volume_service.state_store.get_client_volume = MagicMock(return_value=None)
        mock_volume_service.state_store.get_client_mute = MagicMock(return_value=False)
        mock_volume_service.equalizer_controller = MagicMock()
        mock_volume_service.equalizer_controller.set_equalizer_volume = AsyncMock(return_value=True)
        mock_volume_service.equalizer_controller.set_equalizer_mute = AsyncMock()
        mock_volume_service.broadcast_volume_state = AsyncMock()
        mock_volume_service.volume_config = MagicMock()
        mock_volume_service.volume_config.startup_volume_db = -45.0
        ws_service._volume_service = mock_volume_service

        # Simulate Client.OnConnect params (with MAC address as required)
        params = {
            "client": {
                "id": "aa:bb:cc:dd:ee:ff",
                "config": {"name": "Kitchen Speaker", "volume": {"percent": 100, "muted": False}},
                "host": {"name": "milo-client-kitchen", "ip": "192.168.1.100", "mac": "aa:bb:cc:dd:ee:ff"}
            }
        }

        await ws_service._handle_client_connect(params)
        await drain_background_tasks()

        # Verify client was registered with MAC as identifier
        client = registry.get_client("aa:bb:cc:dd:ee:ff")
        assert client is not None
        assert client.name == "Kitchen Speaker"
        assert client.online is True

    @pytest.mark.asyncio
    async def test_server_update_path_honours_pending_config(self, registry, mock_state_machine):
        """Server.OnUpdate must apply the pending config, like Client.OnConnect does.

        Both notifications can be the first to see a new snapclient. When
        Server.OnUpdate won the race it used to register the Snapcast host name,
        so the name, speaker type and volume_control chosen in the setup wizard
        were silently dropped — and unrecoverably, since register_client
        preserves an existing non-empty name.
        """
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        await registry.initialize()
        attach_registry_broadcaster(registry, mock_state_machine)

        pending_service = MagicMock()
        pending_service.get_client = MagicMock(return_value={
            "mac_id": "aa:bb:cc:dd:ee:ff",
            "name": "Bureau",
            "speaker_type": "tower",
            "volume_control": False,
        })
        pending_service.remove_client = AsyncMock(return_value=True)

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock(),
            pending_clients_service=pending_service,
        )
        ws_service.set_registry(registry)

        snapcast_client = {
            "mac_id": "aa:bb:cc:dd:ee:ff",
            "id": "aa:bb:cc:dd:ee:ff",
            "name": "Milō Client",  # Snapcast host name — must lose to the pending name
            "ip": "192.168.1.100",
            "host": "milo-client",
        }

        await ws_service._process_new_clients([snapcast_client], known_mac_ids=set())

        client = registry.get_client("aa:bb:cc:dd:ee:ff")
        assert client.name == "Bureau"
        assert client.speaker_type == "tower"
        assert client.volume_control is False
        pending_service.remove_client.assert_awaited_once_with("aa:bb:cc:dd:ee:ff")

    @pytest.mark.asyncio
    async def test_client_connect_broadcasts_event(self, registry, mock_state_machine):
        """WebSocket event 'client_connected' is broadcast to frontend."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        await registry.initialize()
        attach_registry_broadcaster(registry, mock_state_machine)

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service.set_registry(registry)

        params = {
            "client": {
                "id": "aa:bb:cc:dd:ee:ff",
                "config": {"name": "Test Speaker", "volume": {"percent": 100, "muted": False}},
                "host": {"name": "milo-client-test", "ip": "192.168.1.101", "mac": "11:22:33:44:55:66"}
            }
        }

        await ws_service._handle_client_connect(params)
        await drain_background_tasks()

        # Verify broadcast was called with multiroom registry event
        mock_state_machine.broadcast.assert_called()
        call_args = mock_state_machine.broadcast.call_args_list
        multiroom_calls = [c for c in call_args if c.args[0].CATEGORY == "multiroom"]
        assert len(multiroom_calls) >= 1

    # === Client Disconnection Detection ===

    @pytest.mark.asyncio
    async def test_client_disconnect_marks_offline(self, registry, mock_state_machine):
        """When Snapcast client disconnects, registry marks client offline."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        await registry.initialize()
        attach_registry_broadcaster(registry, mock_state_machine)

        # First register and connect a client (using MAC as mac_id)
        await registry.register_client("aa:bb:cc:dd:ee:ff", "Test Speaker", "192.168.1.100")
        await registry.set_client_online("aa:bb:cc:dd:ee:ff", True)

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service.set_registry(registry)

        # Simulate Client.OnDisconnect params (with MAC address as required)
        params = {
            "client": {
                "id": "aa:bb:cc:dd:ee:ff",
                "config": {"name": "Test Speaker"},
                "host": {"name": "milo-client-test", "ip": "192.168.1.100", "mac": "aa:bb:cc:dd:ee:ff"}
            }
        }

        await ws_service._handle_client_disconnect(params)

        # Verify client is now offline
        client = registry.get_client("aa:bb:cc:dd:ee:ff")
        assert client is not None
        assert client.online is False

    @pytest.mark.asyncio
    async def test_client_disconnect_broadcasts_event(self, registry, mock_state_machine):
        """WebSocket event 'client_disconnected' is broadcast on disconnect."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        await registry.initialize()
        attach_registry_broadcaster(registry, mock_state_machine)

        await registry.register_client("aa:bb:cc:dd:ee:ff", "Test Speaker", "192.168.1.100")
        await registry.set_client_online("aa:bb:cc:dd:ee:ff", True)

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service.set_registry(registry)

        params = {
            "client": {
                "id": "aa:bb:cc:dd:ee:ff",
                "config": {"name": "Test Speaker"},
                "host": {"name": "milo-client-test", "ip": "192.168.1.100", "mac": "aa:bb:cc:dd:ee:ff"}
            }
        }

        # Clear previous calls
        mock_state_machine.broadcast.reset_mock()

        await ws_service._handle_client_disconnect(params)

        # Verify disconnect event broadcast via registry (multiroom category)
        mock_state_machine.broadcast.assert_called()
        call_args = mock_state_machine.broadcast.call_args_list
        multiroom_calls = [c for c in call_args if c.args[0].CATEGORY == "multiroom"]
        assert len(multiroom_calls) >= 1

    # === Auto-Registration with Default Values ===

    @pytest.mark.asyncio
    async def test_new_client_auto_registered_with_defaults(self, registry, mock_state_machine):
        """New unknown client is auto-registered with correct default values."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        await registry.initialize()
        attach_registry_broadcaster(registry, mock_state_machine)

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service.set_registry(registry)

        # Mock snapcast and volume services so volume sync succeeds
        mock_snapcast = MagicMock()
        mock_snapcast.set_volume = AsyncMock(return_value=True)
        mock_snapcast.set_latency = AsyncMock(return_value=True)
        mock_snapcast.get_clients = AsyncMock(return_value=[])
        ws_service._snapcast_service = mock_snapcast

        mock_volume_service = MagicMock()
        mock_volume_service.state_store = MagicMock()
        mock_volume_service.state_store.set_client_volume = AsyncMock()
        mock_volume_service.state_store.get_client_volume = MagicMock(return_value=None)
        mock_volume_service.state_store.get_client_mute = MagicMock(return_value=False)
        mock_volume_service.equalizer_controller = MagicMock()
        mock_volume_service.equalizer_controller.set_equalizer_volume = AsyncMock(return_value=True)
        mock_volume_service.equalizer_controller.set_equalizer_mute = AsyncMock()
        mock_volume_service.broadcast_volume_state = AsyncMock()
        mock_volume_service.volume_config = MagicMock()
        mock_volume_service.volume_config.startup_volume_db = DEFAULT_VOLUME_DB
        ws_service._volume_service = mock_volume_service

        # New client never seen before (with MAC address as required by compute_mac_id)
        params = {
            "client": {
                "id": "aa:bb:cc:dd:ee:ff",
                "config": {"name": "New Client", "volume": {"percent": 100, "muted": False}},
                "host": {"name": "milo-client-new", "ip": "192.168.1.200", "mac": "aa:bb:cc:dd:ee:ff"}
            }
        }

        await ws_service._handle_client_connect(params)
        await drain_background_tasks()

        client = registry.get_client("aa:bb:cc:dd:ee:ff")
        assert client is not None

        # Verify default values
        assert client.speaker_type == DEFAULT_SPEAKER_TYPE  # 'bookshelf'
        assert client.online is True
        assert client.zone_id is None  # standalone

    @pytest.mark.asyncio
    async def test_new_client_uses_snapcast_name(self, registry, mock_state_machine):
        """New client uses name from Snapcast (hostname or config name)."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        await registry.initialize()
        attach_registry_broadcaster(registry, mock_state_machine)

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service.set_registry(registry)

        params = {
            "client": {
                "id": "11:22:33:44:55:66",
                "config": {"name": "Living Room Speakers", "volume": {"percent": 100}},
                "host": {"name": "milo-client-living", "ip": "192.168.1.201", "mac": "11:22:33:44:55:66"}
            }
        }

        await ws_service._handle_client_connect(params)
        await drain_background_tasks()

        client = registry.get_client("11:22:33:44:55:66")
        assert client.name == "Living Room Speakers"

    # === WebSocket Event Format ===

    @pytest.mark.asyncio
    async def test_registry_event_format(self, registry, mock_state_machine):
        """Registry events follow specified format with category, type, and data."""
        await registry.initialize()
        attach_registry_broadcaster(registry, mock_state_machine)

        await registry.register_client("test-client", "Test", "192.168.1.100")

        # Get the broadcast call
        calls = mock_state_machine.broadcast.call_args_list
        assert len(calls) > 0

        # Check the typed event - registry events map to the multiroom category
        event = calls[-1].args[0]
        assert event.CATEGORY == "multiroom"
        assert event.TYPE == "client_state_changed"  # Mapped from client_connected/client_updated
        assert event.mac_id
        assert event.client is not None

    @pytest.mark.asyncio
    async def test_set_client_online_event_format(self, registry, mock_state_machine):
        """set_client_online emits event with correct format."""
        await registry.initialize()
        attach_registry_broadcaster(registry, mock_state_machine)

        await registry.register_client("test-client", "Test", "192.168.1.100")
        mock_state_machine.broadcast.reset_mock()

        await registry.set_client_online("test-client", True)

        # Verify event format - now uses multiroom category with mapped event type
        calls = mock_state_machine.broadcast.call_args_list
        assert len(calls) > 0

        event = calls[-1].args[0]
        assert event.CATEGORY == "multiroom"
        assert event.TYPE == "client_state_changed"  # Mapped from client_connected
        assert event.mac_id == "test-client"
        assert event.client is not None

    @pytest.mark.asyncio
    async def test_set_client_offline_event_format(self, registry, mock_state_machine):
        """set_client_online(False) emits client_disconnected event."""
        await registry.initialize()
        attach_registry_broadcaster(registry, mock_state_machine)

        await registry.register_client("test-client", "Test", "192.168.1.100")
        await registry.set_client_online("test-client", True)
        mock_state_machine.broadcast.reset_mock()

        await registry.set_client_online("test-client", False)

        # Verify event format - now uses multiroom category with mapped event type
        calls = mock_state_machine.broadcast.call_args_list
        assert len(calls) > 0

        event = calls[-1].args[0]
        assert event.CATEGORY == "multiroom"
        assert event.TYPE == "client_state_changed"  # Mapped from client_disconnected
        assert event.mac_id == "test-client"

    # === compute_mac_id Tests for Snapcast Integration ===

    def test_compute_mac_id_for_local_client(self):
        """Test compute_mac_id reads MAC from system interface for localhost."""
        from unittest.mock import mock_open, patch
        mock_file = mock_open(read_data="aa:bb:cc:dd:ee:ff\n")
        with patch("builtins.open", mock_file):
            mac_id = compute_mac_id("milo", "127.0.0.1")
        assert mac_id == "aa:bb:cc:dd:ee:ff"

    def test_compute_mac_id_for_milo_client(self):
        """Test compute_mac_id returns MAC address for remote clients."""
        mac_id = compute_mac_id("milo-client-kitchen", "192.168.1.100", "aa:bb:cc:dd:ee:ff")
        assert mac_id == "aa:bb:cc:dd:ee:ff"

    def test_compute_mac_id_strips_ipv6_prefix(self):
        """Test compute_mac_id handles IPv6-mapped IPv4 addresses for localhost."""
        from unittest.mock import mock_open, patch
        # Snapcast sometimes returns ::ffff:192.168.1.100 format
        # The websocket.py code strips this: .replace("::ffff:", "")
        ip = "::ffff:127.0.0.1".replace("::ffff:", "")
        mock_file = mock_open(read_data="aa:bb:cc:dd:ee:ff\n")
        with patch("builtins.open", mock_file):
            mac_id = compute_mac_id("milo", ip)
        assert mac_id == "aa:bb:cc:dd:ee:ff"

    # === Event Timing is tested via integration tests ===

    @pytest.mark.asyncio
    async def test_event_emission_is_async(self, registry, mock_state_machine):
        """Verify events are emitted asynchronously (prerequisite for <100ms timing)."""
        import asyncio

        await registry.initialize()
        attach_registry_broadcaster(registry, mock_state_machine)

        # Measure time for registration (should be fast due to async)
        start = asyncio.get_running_loop().time()
        await registry.register_client("perf-test", "Performance Test", "192.168.1.99")
        end = asyncio.get_running_loop().time()

        # Registration + event emission should be fast (< 50ms)
        assert (end - start) < 0.05

    @pytest.mark.asyncio
    async def test_registry_does_not_call_broadcast_directly(self, registry, mock_state_machine):
        """Phase 2 R2: Registry is a pure store - mutations must NOT broadcast.

        Broadcasting is owned by SnapcastWebSocketService (or another caller).
        The registry should expose no state_machine reference and never call
        state_machine.broadcast itself.
        """
        from backend.core.multiroom.models import EqualizerSettings

        await registry.initialize()

        # No state_machine wiring at all — the registry must not need it.
        assert not hasattr(registry, "set_state_machine"), \
            "Registry must not expose set_state_machine"
        assert not hasattr(registry, "_state_machine"), \
            "Registry must not store a state_machine reference"

        # Exercise every public mutation that emits a registry event.
        await registry.register_client("aa:bb:cc:dd:ee:01", "A", "192.168.1.10")
        await registry.register_client("aa:bb:cc:dd:ee:02", "B", "192.168.1.11")
        await registry.register_client("aa:bb:cc:dd:ee:03", "C", "192.168.1.12")
        await registry.set_client_online("aa:bb:cc:dd:ee:01", True)
        await registry.update_client("aa:bb:cc:dd:ee:01", name="A2")
        await registry.update_client("aa:bb:cc:dd:ee:01", speaker_type="subwoofer")
        await registry.set_client_equalizer(
            "aa:bb:cc:dd:ee:03", EqualizerSettings.default_for_zone()
        )
        await registry.create_zone(
            "z1", "Zone 1", ["aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02"]
        )
        await registry.update_zone("z1", name="Zone 1 Renamed")
        await registry.remove_client_from_zone("z1", "aa:bb:cc:dd:ee:02")
        await registry.delete_zone("z1")
        await registry.unregister_client("aa:bb:cc:dd:ee:01")

        # state_machine never touched.
        mock_state_machine.broadcast.assert_not_called()


class TestSyncStandaloneDspToClient:
    """
    Unit tests for SnapcastWebSocketService._sync_standalone_equalizer_to_client().

    In the unified per-client model this path pushes a REMOTE client's one EQ
    record (registry `client_equalizer[mac]`, the single source of truth — zone
    members hold identical records) to the satellite: filters, compressor,
    loudness, mono and the master enabled/bypass flag. The local client owns
    equalizer.json (applied to the DAC at boot by CamillaDSPService) and is never
    driven through this websocket re-sync — a local target is an explicit no-op.
    """

    @pytest.fixture
    def mock_state_machine(self):
        sm = MagicMock()
        sm.broadcast = AsyncMock()
        return sm

    @pytest.fixture
    def mock_proxy(self):
        p = MagicMock()
        p.request = AsyncMock()
        p.apply_record = AsyncMock(return_value=True)
        return p

    @pytest.fixture
    def mock_crossover(self):
        x = MagicMock()
        x.queue_pending_settings = AsyncMock()
        return x

    @pytest.fixture
    def mock_registry(self):
        """Mock registry with a remote test client and no saved EQ record."""
        registry = MagicMock()
        client = MagicMock()
        client.ip = "192.168.1.100"
        client.is_local = False
        client.mac_id = "test-client"
        registry.get_client = MagicMock(return_value=client)
        registry.get_client_equalizer = MagicMock(return_value=None)
        return registry

    def _make_ws(self, sm, registry, proxy=None, crossover=None):
        from backend.core.multiroom.websocket import SnapcastWebSocketService
        ws = SnapcastWebSocketService(state_machine=sm, routing_service=MagicMock())
        ws._registry = registry
        ws._equalizer_client_proxy_service = proxy
        ws._crossover_service = crossover
        return ws

    @pytest.mark.asyncio
    async def test_no_saved_settings_returns_true(self, mock_state_machine, mock_registry, mock_proxy, mock_crossover):
        """No persisted EQ record → nothing pushed, returns True (defaults apply)."""
        ws = self._make_ws(mock_state_machine, mock_registry, mock_proxy, mock_crossover)
        result = await ws._sync_standalone_equalizer_to_client("test-client")
        assert result is True
        mock_proxy.apply_record.assert_not_called()

    @pytest.mark.asyncio
    async def test_saved_settings_applied_via_proxy(self, mock_state_machine, mock_registry, mock_proxy, mock_crossover):
        """The saved record is pushed whole, through the one canonical push —
        the same one the live write and the pending replay use."""
        from backend.core.multiroom.models import EqualizerSettings, EqFilter
        record = EqualizerSettings(
            filters=[EqFilter(id="eq_band_00", frequency=100, gain=2.0, q=1.41)],
            mono=False, enabled=True,
        )
        mock_registry.get_client_equalizer.return_value = record
        ws = self._make_ws(mock_state_machine, mock_registry, mock_proxy, mock_crossover)
        await ws._sync_standalone_equalizer_to_client("test-client")
        mock_proxy.apply_record.assert_awaited_once_with("192.168.1.100", record)

    @pytest.mark.asyncio
    async def test_local_client_is_noop(self, mock_state_machine, mock_proxy, mock_crossover):
        """The local client owns equalizer.json (applied to the DAC at boot by
        CamillaDSPService) and is never driven through this websocket re-sync.
        Even when it carries a registry record, syncing a local target is a no-op:
        nothing is pushed to the proxy and it returns True."""
        from backend.core.multiroom.models import EqualizerSettings, EqFilter
        registry = MagicMock()
        local_client = MagicMock()
        local_client.ip = "127.0.0.1"
        local_client.is_local = True
        local_client.mac_id = "local"
        registry.get_client = MagicMock(return_value=local_client)
        registry.get_client_equalizer = MagicMock(return_value=EqualizerSettings(
            filters=[EqFilter(id="eq_band_00", frequency=100, gain=2.0, q=1.41)],
            mono=False, enabled=True, active_preset="vocal_boost",
        ))
        ws = self._make_ws(mock_state_machine, registry, mock_proxy, mock_crossover)
        result = await ws._sync_standalone_equalizer_to_client("local")
        assert result is True
        mock_proxy.apply_record.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_handles_missing_client(self, mock_state_machine, mock_proxy):
        """Missing client is handled gracefully (returns False)."""
        registry = MagicMock()
        registry.get_client = MagicMock(return_value=None)
        ws = self._make_ws(mock_state_machine, registry, mock_proxy)
        result = await ws._sync_standalone_equalizer_to_client("unknown-client")
        assert result is False

    @pytest.mark.asyncio
    async def test_failed_push_queues_the_whole_record(self, mock_state_machine, mock_registry, mock_crossover):
        """A partial push is requeued whole: replaying the record is idempotent
        and converges the client in one shot, where per-setting retries are what
        leave a satellite half-applied."""
        from backend.core.multiroom.models import EqualizerSettings, EqFilter
        record = EqualizerSettings(
            filters=[
                EqFilter(id="eq_band_00", frequency=100, gain=2.0, q=1.41),
                EqFilter(id="eq_band_01", frequency=1000, gain=-1.5, q=1.41),
            ],
        )
        mock_registry.get_client_equalizer.return_value = record
        failing_proxy = MagicMock()
        failing_proxy.apply_record = AsyncMock(return_value=False)
        ws = self._make_ws(mock_state_machine, mock_registry, failing_proxy, mock_crossover)

        result = await ws._sync_standalone_equalizer_to_client("test-client")

        assert result is False
        mock_crossover.queue_pending_settings.assert_awaited_once_with(
            "test-client", "record", record
        )


class TestReconnectRepushesEqualizer:
    """The secondary reconnect path (`_sync_reconnecting_client_volume`, used by the
    Server.OnUpdate online-status flip) must re-push the client's EQ record — not
    just volume — so a member that missed a zone-EQ change while offline recovers it
    automatically on reconnect. The local client is a no-op (is_local guard inside
    the callee). EQ re-push happens after volume is confirmed and before the client
    is shown online."""

    def _make_ws(self):
        from backend.core.multiroom.websocket import SnapcastWebSocketService
        sm = MagicMock()
        sm.broadcast = AsyncMock()
        ws = SnapcastWebSocketService(state_machine=sm, routing_service=MagicMock())
        ws._registry = MagicMock()
        ws._registry.set_client_online = AsyncMock()
        ws._volume_service = MagicMock()
        ws._volume_service.broadcast_volume_state = AsyncMock()
        ws._resolve_target_volume = MagicMock(return_value=-40.0)
        ws._apply_target_volume_to_client = AsyncMock(return_value=True)
        ws._sync_standalone_equalizer_to_client = AsyncMock(return_value=True)
        return ws

    @pytest.mark.asyncio
    async def test_reconnect_repushes_equalizer_after_volume_sync(self):
        ws = self._make_ws()
        ok = await ws._do_sync_reconnecting_client_volume(
            "milo-client-1", set_online_after=True, max_retries=0, retry_delay=0,
        )
        assert ok is True
        ws._sync_standalone_equalizer_to_client.assert_awaited_once_with("milo-client-1")
        # EQ pushed before the client is shown online (fully configured first).
        ws._registry.set_client_online.assert_awaited_once_with("milo-client-1", True)

    @pytest.mark.asyncio
    async def test_reconnect_skips_equalizer_when_volume_never_syncs(self):
        """If volume never confirms on hardware, the client stays offline and EQ is
        not pushed (avoids configuring a client we can't reach)."""
        ws = self._make_ws()
        ws._apply_target_volume_to_client = AsyncMock(return_value=False)
        ok = await ws._do_sync_reconnecting_client_volume(
            "milo-client-1", set_online_after=True, max_retries=0, retry_delay=0,
        )
        assert ok is False
        ws._sync_standalone_equalizer_to_client.assert_not_called()


# =============================================================================
# Reconnection Volume Resolution Tests
# =============================================================================

class TestResolveTargetVolume:
    """Tests for `_resolve_target_volume` — the level an admission brings a client to.

    One rule since the volume-ownership plan's phase 1: a client's level changes
    only when someone changes it, so what a reconnection applies is the client's
    own stored level, gated by `restore_last_volume`, with `startup_volume_db`
    for a client the store has never seen. The peer average this used to read
    (the zone's or the fleet's) is what made a member rejoining a zone adopt
    its neighbours' level and lose its own.
    """

    @pytest.fixture
    def mock_state_machine(self):
        """A volume store holding client-1 at -70 dB and two peers at -20/-30.

        The peers are here so the peer average (-25) is a distinct number the
        resolver could land on: the store is the only place levels live now, so
        it is also the only place a re-introduced average could read them from.
        """
        state_machine = MagicMock()

        volume_service = MagicMock()
        volume_service.volume_config = VolumeConfig(
            startup_volume_db=-40.0, restore_last_volume=True
        )
        volume_service.state_store = MagicMock()
        volume_service.state_store.get_client_volume = MagicMock(
            side_effect={"client-1": -70.0, "client-2": -20.0, "client-3": -30.0}.get
        )
        state_machine.volume_service = volume_service

        return state_machine

    @pytest.fixture
    def mock_registry(self):
        """A registry that answers anything, so a resolver consulting one shows."""
        registry = MagicMock()
        client = MagicMock()
        client.zone_id = "zone-1"
        registry.get_client = MagicMock(return_value=client)
        return registry

    def _ws(self, state_machine, registry):
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        ws_service = SnapcastWebSocketService(
            state_machine=state_machine,
            routing_service=MagicMock()
        )
        ws_service._registry = registry
        ws_service._volume_service = state_machine.volume_service
        return ws_service

    def test_a_zone_member_returns_its_own_level_not_its_peers(
        self, mock_state_machine, mock_registry
    ):
        """A member rejoining a zone keeps the level it had, whatever the room sits at."""
        ws_service = self._ws(mock_state_machine, mock_registry)

        target = ws_service._resolve_target_volume("client-1")

        assert target == -70.0
        assert target != -25.0, "its zone peers' average is not the target"

    def test_a_standalone_client_returns_its_own_level_not_the_global_average(
        self, mock_state_machine, mock_registry
    ):
        """Same rule off-zone: the global average is a reading, never a target."""
        mock_registry.get_client.return_value.zone_id = None
        ws_service = self._ws(mock_state_machine, mock_registry)

        target = ws_service._resolve_target_volume("client-1")

        assert target == -70.0
        assert target != -25.0, "the fleet's average is not the target either"

    def test_restore_last_volume_off_returns_startup_volume(
        self, mock_state_machine, mock_registry
    ):
        """The escape hatch: a fleet configured for a fixed level ignores the store.

        Without this gate the boot push would apply startup_volume_db and the
        admission the remembered value — the split this rule exists to remove.
        """
        volume_service = mock_state_machine.volume_service
        volume_service.volume_config = VolumeConfig(
            startup_volume_db=-40.0, restore_last_volume=False
        )
        ws_service = self._ws(mock_state_machine, mock_registry)

        target = ws_service._resolve_target_volume("client-1")

        assert target == -40.0
        volume_service.state_store.get_client_volume.assert_not_called()

    def test_a_client_the_store_never_saw_returns_startup_volume(
        self, mock_state_machine, mock_registry
    ):
        """First connection of a speaker Milō has no level for."""
        ws_service = self._ws(mock_state_machine, mock_registry)

        assert ws_service._resolve_target_volume("brand-new-client") == -40.0

    def test_without_volume_service_returns_the_default_constant(self, mock_registry):
        """Edge case: no volume_service at all — neither store nor config to read."""
        from backend.config.constants import DEFAULT_VOLUME_DB

        ws_service = self._ws(MagicMock(), mock_registry)
        ws_service._volume_service = None

        assert ws_service._resolve_target_volume("client-1") == DEFAULT_VOLUME_DB

    def test_resolution_does_not_need_the_registry(self, mock_state_machine):
        """The client's own record is the whole input — no peer, no zone, no registry.

        Structural: this is what makes the resolution independent of which
        clients happen to be online when the admission runs.
        """
        ws_service = self._ws(mock_state_machine, None)

        assert ws_service._resolve_target_volume("client-1") == -70.0


class TestApplyTargetVolumeToClient:
    """Tests for _apply_target_volume_to_client() method."""

    @pytest.fixture
    def mock_state_machine(self):
        """Create a mock state machine with volume_service."""
        state_machine = MagicMock()
        volume_service = AsyncMock()
        # Mock state store (used by _apply_target_volume_to_client)
        volume_service.state_store = MagicMock()
        volume_service.state_store.set_client_volume = AsyncMock()
        volume_service.state_store.get_client_volume = MagicMock(return_value=None)
        volume_service.state_store.get_client_mute = MagicMock(return_value=False)
        # Mock equalizer controller (used for hardware apply)
        volume_service.equalizer_controller = MagicMock()
        volume_service.equalizer_controller.set_equalizer_volume = AsyncMock(return_value=True)
        volume_service.equalizer_controller.set_equalizer_mute = AsyncMock()
        state_machine.volume_service = volume_service
        return state_machine

    @pytest.mark.asyncio
    async def test_apply_volume_updates_the_store_and_the_hardware(self, mock_state_machine):
        """Test that applying volume updates the state store and the hardware."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service._volume_service = mock_state_machine.volume_service

        result = await ws_service._apply_target_volume_to_client("client-1", -30.0)

        assert result is True
        mock_state_machine.volume_service.state_store.set_client_volume.assert_called_once_with(
            "client-1", -30.0
        )
        mock_state_machine.volume_service.equalizer_controller.set_equalizer_volume.assert_called_once_with(
            "client-1", -30.0, force=True
        )

    @pytest.mark.asyncio
    async def test_apply_volume_reports_a_failed_unmute(self, mock_state_machine):
        """A client whose unmute never reached CamillaDSP is not an applied client.

        CamillaDSP starts muted (-m) and the admission sync announces a client
        online only once this returns True. Discarding the unmute outcome is what
        put a satellite in the UI, online, that stayed silent for the whole
        session: the caller's retry loop never fired because nothing ever
        reported a failure.
        """
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        eq = mock_state_machine.volume_service.equalizer_controller
        eq.set_equalizer_volume = AsyncMock(return_value=True)
        eq.set_equalizer_mute = AsyncMock(return_value=False)

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service._volume_service = mock_state_machine.volume_service

        result = await ws_service._apply_target_volume_to_client("client-1", -30.0)

        assert result is False
        eq.set_equalizer_mute.assert_awaited_once_with("client-1", False, force=True)

    @pytest.mark.asyncio
    async def test_apply_volume_still_unmutes_after_a_failed_volume(self, mock_state_machine):
        """The unmute is attempted whatever the volume call returned.

        A muted client at the wrong volume is worse than an unmuted one: with the
        -m start flag, skipping the unmute on a volume failure leaves the speaker
        silent. What this pins is the ordering, not the return value — the volume
        failure alone already makes the result False.
        """
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        eq = mock_state_machine.volume_service.equalizer_controller
        eq.set_equalizer_volume = AsyncMock(return_value=False)
        eq.set_equalizer_mute = AsyncMock(return_value=True)

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service._volume_service = mock_state_machine.volume_service

        result = await ws_service._apply_target_volume_to_client("client-1", -30.0)

        assert result is False
        eq.set_equalizer_mute.assert_awaited_once_with("client-1", False, force=True)

    @pytest.mark.asyncio
    async def test_apply_volume_without_volume_service_returns_false(self):
        """Test that apply fails gracefully without volume_service."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        mock_state_machine = MagicMock()

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service._volume_service = None

        result = await ws_service._apply_target_volume_to_client("client-1", -30.0)

        assert result is False

    @pytest.mark.asyncio
    async def test_apply_volume_handles_volume_service_exception(self):
        """Test that apply returns False when state_store raises an exception."""
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        mock_state_machine = MagicMock()
        volume_service = AsyncMock()
        volume_service.state_store = MagicMock()
        volume_service.state_store.set_client_volume = AsyncMock(
            side_effect=Exception("Equalizer connection failed")
        )

        ws_service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=MagicMock()
        )
        ws_service._volume_service = volume_service

        result = await ws_service._apply_target_volume_to_client("client-1", -30.0)

        # Should return False due to exception
        assert result is False


class TestClientReconcileSweep:
    """
    Tests for SnapcastWebSocketService's periodic liveness sweep.

    Snapserver declares a client disconnected only when its socket errors, and
    it writes nothing to an idle client's socket — so a satellite that vanishes
    without a TCP FIN (power cut, Wi-Fi drop) stays `connected: true` there and
    emits no notification at all. If these fail, the registry keeps such a
    client `online` forever and the frontend offers volume, EQ and hardware
    controls for a speaker that is gone.
    """

    FRESH = "dc:a6:32:7e:d3:43"
    VANISHED = "d8:3a:dd:68:e7:e4"

    @staticmethod
    def _snap_client(mac: str, ip: str, last_seen_age: float) -> dict:
        """A snapserver client entry that still claims `connected`."""
        return {
            "id": mac,
            "connected": True,
            "config": {"name": "", "volume": {"percent": 100, "muted": False}},
            "host": {"name": "milo-client", "ip": f"::ffff:{ip}", "mac": mac},
            "lastSeen": {"sec": int(time.time() - last_seen_age), "usec": 0},
        }

    def _status(self, vanished_last_seen_age: float) -> dict:
        return {"server": {"groups": [{"id": "g1", "clients": [
            self._snap_client(self.FRESH, "192.168.1.153", 1),
            self._snap_client(self.VANISHED, "192.168.1.60", vanished_last_seen_age),
        ]}]}}

    async def _service(self, status_sequence: list):
        """A wired service whose only mock is the snapserver RPC.

        `extract_clients` is the real implementation, so the lastSeen freshness
        rule under test is production code. The sequence's last entry raises
        CancelledError, which ends the loop after a deterministic number of
        passes instead of on a wall clock.
        """
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        settings = AsyncMock()
        settings.get_setting = AsyncMock(return_value=None)
        registry = ClientRegistryService(settings_service=settings)
        await registry.initialize()
        for mac, ip in ((self.FRESH, "192.168.1.153"), (self.VANISHED, "192.168.1.60")):
            await registry.register_client(mac, f"Speaker {mac[-2:]}", ip, host="milo-client")
            await registry.set_client_online(mac, True)

        snapcast = SnapcastService(systemd_manager=MagicMock())
        snapcast.get_server_status = AsyncMock(side_effect=status_sequence)

        sm = MagicMock()
        sm.broadcast = AsyncMock()
        service = SnapcastWebSocketService(state_machine=sm, routing_service=MagicMock())
        service.set_registry(registry)
        service._snapcast_service = snapcast
        service.websocket = MagicMock(closed=False)
        service.running = True
        service.should_connect = True
        service.RECONCILE_INTERVAL_S = 0
        return service, registry

    @staticmethod
    async def _drive(service):
        """Run the sweep until the RPC mock ends it, bounded so a regression fails.

        The loop's only exit is that CancelledError, and the three guards above
        the RPC call (connected / _snapcast_service / registry) skip straight
        back to the top of the loop. With RECONCILE_INTERVAL_S = 0 that is an
        unbounded busy loop: anything that breaks one of those guards wedges the
        run at 100% CPU instead of failing it — measured, by eviscerating
        set_registry. The bound is liveness, not latency; a healthy sweep exits
        in microseconds and never approaches it.
        """
        await asyncio.wait_for(service._reconcile_loop(), timeout=5)

    @pytest.mark.asyncio
    async def test_sweep_marks_a_silently_vanished_client_offline(self):
        """A client snapserver still calls connected goes offline once it stops being seen."""
        service, registry = await self._service(
            [self._status(vanished_last_seen_age=500), asyncio.CancelledError()]
        )

        with pytest.raises(asyncio.CancelledError):
            await self._drive(service)

        assert registry.get_client(self.VANISHED).online is False
        assert registry.get_client(self.FRESH).online is True

    @pytest.mark.asyncio
    async def test_sweep_keeps_a_still_seen_client_online(self):
        """The sweep is not a timeout on its own: a client seen 1s ago stays online."""
        service, registry = await self._service(
            [self._status(vanished_last_seen_age=1), asyncio.CancelledError()]
        )

        with pytest.raises(asyncio.CancelledError):
            await self._drive(service)

        assert registry.get_client(self.VANISHED).online is True
        assert registry.get_client(self.FRESH).online is True

    @pytest.mark.asyncio
    async def test_a_parsed_client_carries_no_liveness_flag(self):
        """Absence is the only way `extract_clients` reports a departure.

        A stale client is dropped from the list, never returned carrying a false
        flag — so a consumer that reads one is reading something the parser
        cannot produce, and its offline branch can never run.
        """
        snapcast = SnapcastService(systemd_manager=MagicMock())

        parsed = snapcast.extract_clients(self._status(vanished_last_seen_age=500))

        assert [c["mac_id"] for c in parsed] == [self.FRESH]
        assert "online" not in parsed[0], (
            "a liveness flag on a list that only ever holds live clients is a "
            "constant, and the branches reading it are dead"
        )

    @pytest.mark.asyncio
    async def test_sweep_ignores_an_unreadable_server_status(self):
        """An RPC failure must not read as "every client vanished" and offline the fleet.

        get_server_status is fail-open ({} on failure), so without the guard one
        snapserver hiccup would take every client out of the UI at once.
        """
        service, registry = await self._service([{}, asyncio.CancelledError()])

        with pytest.raises(asyncio.CancelledError):
            await self._drive(service)

        assert registry.get_client(self.VANISHED).online is True
        assert registry.get_client(self.FRESH).online is True

    @pytest.mark.asyncio
    async def test_enabling_multiroom_starts_the_sweep(self):
        """The sweep is actually wired: without this the loop above is dead code.

        Nothing else in the suite notices a reconcile loop that is never spawned,
        and its absence is invisible in dev too — it only shows up as a satellite
        that stays online for hours after being unplugged.
        """
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        service = SnapcastWebSocketService(state_machine=MagicMock(), routing_service=MagicMock())
        service.running = True
        service.session = MagicMock()
        service.session.ws_connect = AsyncMock(
            side_effect=aiohttp.ClientConnectorError(MagicMock(), OSError("no snapserver"))
        )

        await service.start_connection()
        try:
            assert service.reconcile_task is not None
        finally:
            await service.stop_connection()

        assert service.reconcile_task is None

    @pytest.mark.asyncio
    async def test_disabling_multiroom_closes_the_snapserver_socket(self):
        """Turning multiroom off must actually close the control socket.

        The close was guarded on `self.websocket`, but `_connect_and_listen`'s
        finally nulls that attribute as its task unwinds — and `cancel_all()`
        drains the tasks before the guard is read, so the branch could never run
        and the TCP connection to snapserver leaked on every disable.
        """
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        websocket = MagicMock()
        websocket.closed = False
        websocket.close = AsyncMock()

        service = SnapcastWebSocketService(state_machine=MagicMock(), routing_service=MagicMock())
        service.running = True
        service.should_connect = True
        service.websocket = websocket

        async def connection_loop():
            try:
                await asyncio.sleep(3600)
            finally:
                # Exactly what _connect_and_listen does as it is cancelled.
                service.websocket = None

        service._bg.spawn(connection_loop(), label="connection_loop")
        await asyncio.sleep(0)

        await service.stop_connection()

        websocket.close.assert_awaited_once()
        assert service.websocket is None

    @pytest.mark.asyncio
    async def test_reconnect_does_not_readmit_a_client_that_is_no_longer_seen(self):
        """Re-marking a stale client online on reconnect is how the ghost survived.

        Snapserver keeps `connected: true` for a satellite that vanished without a
        TCP FIN, so the reconnection path must apply the same freshness rule as the
        sweep — otherwise a multiroom toggle puts a long-gone speaker back in the UI.
        """
        service, registry = await self._service([self._status(vanished_last_seen_age=500)])
        await registry.set_client_online(self.VANISHED, False)

        await service._initialize_existing_clients()

        assert registry.get_client(self.VANISHED).online is False
        assert registry.get_client(self.FRESH).online is True

    @pytest.mark.asyncio
    async def test_sweep_is_quiet_once_a_client_is_already_offline(self, caplog):
        """A repeating sweep must not re-announce a state it already recorded.

        Observed on a unit: the pass ran every 30s and wrote the same
        "CLIENT DISCONNECTED" line each time for as long as the satellite stayed
        unplugged. Only a transition is an event.
        """
        service, registry = await self._service([
            self._status(vanished_last_seen_age=500),
            self._status(vanished_last_seen_age=530),
            asyncio.CancelledError(),
        ])

        with caplog.at_level(logging.INFO, logger="backend.core.multiroom.websocket"):
            with pytest.raises(asyncio.CancelledError):
                await self._drive(service)

        assert registry.get_client(self.VANISHED).online is False
        announced = [r for r in caplog.records if "CLIENT DISCONNECTED" in r.getMessage()]
        assert len(announced) == 1, [r.getMessage() for r in announced]


@pytest.mark.usefixtures("no_satellite_network")
class TestAdmissionPathConvergence:
    """Every notification that can be the first to see a client must admit it the same way.

    Three of them exist — the sweep over already-connected clients at WebSocket
    connect, `Client.OnConnect`, and `Server.OnUpdate` — and which one wins is a
    race decided by whether the backend or the satellite booted first. The bugs
    this pins are all "the losing path did less": an identity the setup wizard
    had just assigned was dropped, a client was announced online before its
    volume reached the hardware (with nothing to retry it, since snapserver and
    the registry then agreed and no later transition fired), and snapserver was
    left attenuating a client the rest of Milō treats as a passthrough.
    """

    MAC = "dc:a6:32:7e:d3:43"
    IP = "192.168.1.153"

    def _status(self) -> dict:
        return {"server": {"groups": [{"id": "g1", "clients": [{
            "id": self.MAC,
            "connected": True,
            "config": {"name": "", "volume": {"percent": 100, "muted": False}},
            "host": {"name": "milo-client", "ip": f"::ffff:{self.IP}", "mac": self.MAC},
            "lastSeen": {"sec": int(time.time()), "usec": 0},
        }]}]}}

    async def _service(self, pending: dict = None):
        from backend.core.multiroom.websocket import SnapcastWebSocketService

        settings = AsyncMock()
        settings.get_setting = AsyncMock(return_value=None)
        registry = ClientRegistryService(settings_service=settings)
        await registry.initialize()

        snapcast = SnapcastService(systemd_manager=MagicMock())
        snapcast.get_server_status = AsyncMock(return_value=self._status())
        snapcast.set_volume = AsyncMock(return_value=True)
        snapcast.set_latency = AsyncMock(return_value=True)

        pending_service = MagicMock()
        pending_service.get_client = MagicMock(return_value=pending)
        pending_service.remove_client = AsyncMock()

        sm = MagicMock()
        sm.broadcast = AsyncMock()
        service = SnapcastWebSocketService(
            state_machine=sm,
            routing_service=MagicMock(),
            snapcast_service=snapcast,
            pending_clients_service=pending_service,
        )
        service.set_registry(registry)
        return service, registry, snapcast, pending_service

    @pytest.mark.asyncio
    async def test_wizard_identity_survives_a_websocket_connect(self):
        """The name, speaker type and volume_control chosen in the wizard must not be lost.

        `register_client` preserves an existing non-empty name, so a client
        admitted under its Snapcast host name cannot be repaired by a later
        notification — the user's speaker comes back as "Milō Client" for good.
        """
        service, registry, _, pending_service = await self._service(pending={
            "name": "Bureau", "speaker_type": "subwoofer", "volume_control": False,
        })
        service._sync_reconnecting_client_volume = AsyncMock(return_value=True)

        await service._initialize_existing_clients()

        client = registry.get_client(self.MAC)
        assert client.name == "Bureau"
        assert client.speaker_type == "subwoofer"
        assert client.volume_control is False
        pending_service.remove_client.assert_awaited_once_with(self.MAC)

    @pytest.mark.asyncio
    async def test_a_new_client_is_not_announced_before_its_volume_lands(self):
        """Admission goes through the retrying sync, which owns the online flag.

        Marking it online here instead is unretryable: snapserver and the
        registry then both read "online", so no transition ever fires again and
        a satellite whose API was still booting stays muted (CamillaDSP starts
        with -m) until something else disturbs it.
        """
        service, registry, _, _ = await self._service()
        service._sync_reconnecting_client_volume = AsyncMock(return_value=True)

        await service._initialize_existing_clients()
        await asyncio.sleep(0)

        assert registry.get_client(self.MAC).online is False
        service._sync_reconnecting_client_volume.assert_awaited_once_with(
            self.MAC, set_online_after=True, snapcast_id=self.MAC
        )

    @pytest.mark.asyncio
    async def test_a_known_client_is_readmitted_without_a_volume_resync(self):
        """A backend restart is not a client reconnection.

        The satellite kept playing across it, and the reconnection policy does
        not restore a client's own volume — it applies the peer average or the
        startup volume — so resyncing here would audibly reset every speaker
        each time the backend is restarted.
        """
        service, registry, _, _ = await self._service()
        await registry.register_client(self.MAC, "Bureau", self.IP, host="milo-client")
        service._sync_reconnecting_client_volume = AsyncMock(return_value=True)

        await service._initialize_existing_clients()
        await asyncio.sleep(0)

        assert registry.get_client(self.MAC).online is True
        service._sync_reconnecting_client_volume.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_reconnect_during_a_sync_still_refreshes_the_address(self):
        """The _syncing_mac_ids guard dedups the sync, not the registration.

        `_register_snapclient` is the only path that refreshes a known client's
        ip/host, and the guard used to sit before it — so a reconnect landing
        inside the sync's ~15 s retry budget skipped the refresh entirely.
        After a DHCP lease change caught by that window the speaker keeps
        playing (it dials out to snapserver) while every push Milō makes goes
        to the old address: volume, EQ and hardware all fail silently.
        """
        service, registry, _, _ = await self._service()
        await registry.register_client(self.MAC, "Bureau", self.IP, host="milo-client")
        service._sync_reconnecting_client_volume = AsyncMock(return_value=True)
        service._syncing_mac_ids.add(self.MAC)  # a sync is already in flight

        await service._handle_client_connect({"client": {
            "id": self.MAC,
            "config": {"name": "Bureau", "volume": {"percent": 100}},
            "host": {"name": "milo-client", "ip": "::ffff:192.168.1.177", "mac": self.MAC},
        }})
        await drain_background_tasks()

        assert registry.get_client(self.MAC).ip == "192.168.1.177"
        service._sync_reconnecting_client_volume.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_snapserver_is_left_a_passthrough_whenever_the_path_can_tell(self):
        """Attenuation is CamillaDSP's job on the client; snapserver stays at 100.

        Only the paths holding a Snapcast client id can restore it, and they did
        not, so the same client ended up differently attenuated depending on
        which notification announced it.
        """
        service, registry, snapcast, _ = await self._service()
        await registry.register_client(self.MAC, "Bureau", self.IP, host="milo-client")
        volume_service = MagicMock()
        volume_service.volume_config = VolumeConfig()
        volume_service.state_store.set_client_volume = AsyncMock()
        volume_service.state_store.get_client_volume = MagicMock(return_value=None)
        volume_service.state_store.get_client_mute = MagicMock(return_value=False)
        volume_service.equalizer_controller.set_equalizer_volume = AsyncMock(return_value=True)
        volume_service.equalizer_controller.set_equalizer_mute = AsyncMock(return_value=True)
        volume_service.broadcast_volume_state = AsyncMock()
        service.set_volume_service(volume_service)

        assert await service._sync_reconnecting_client_volume(
            self.MAC, max_retries=0, retry_delay=0, snapcast_id="snap-1"
        )

        snapcast.set_volume.assert_awaited_once_with("snap-1", 100)

    @pytest.mark.asyncio
    async def test_the_per_client_delay_is_repushed_on_admission(self):
        """A delay set while a client was away must reach snapserver on reconnect.

        The delay is native Snapcast latency Milō owns as its source of truth,
        and the admission path is the one place holding the Snapcast id — so it
        re-pushes the delay right beside the volume-100 passthrough, or a delay
        changed offline would silently never apply (the mirror of the buffer /
        EQ re-push bugs).
        """
        service, registry, snapcast, _ = await self._service()
        await registry.register_client(self.MAC, "Bureau", self.IP, host="milo-client")
        await registry.set_client_delay(self.MAC, 40)
        volume_service = MagicMock()
        volume_service.volume_config = VolumeConfig()
        volume_service.state_store.set_client_volume = AsyncMock()
        volume_service.state_store.get_client_volume = MagicMock(return_value=None)
        volume_service.state_store.get_client_mute = MagicMock(return_value=False)
        volume_service.equalizer_controller.set_equalizer_volume = AsyncMock(return_value=True)
        volume_service.equalizer_controller.set_equalizer_mute = AsyncMock(return_value=True)
        volume_service.broadcast_volume_state = AsyncMock()
        service.set_volume_service(volume_service)

        assert await service._sync_reconnecting_client_volume(
            self.MAC, max_retries=0, retry_delay=0, snapcast_id="snap-1"
        )

        snapcast.set_latency.assert_awaited_once_with("snap-1", 40)

    @pytest.mark.asyncio
    async def test_a_client_snapserver_lists_twice_is_admitted_once(self):
        """Admission inherits SnapcastService's dedup, and that is load-bearing.

        Reading the raw status instead processed both entries, and the second
        found the client already registered — so it took the known-client branch
        and announced a brand new speaker online without waiting for its volume,
        which is the window this path exists to close.
        """
        service, registry, _, _ = await self._service()
        status = self._status()
        status["server"]["groups"][0]["clients"].append(
            dict(status["server"]["groups"][0]["clients"][0])
        )
        service._snapcast_service.get_server_status = AsyncMock(return_value=status)
        service._sync_reconnecting_client_volume = AsyncMock(return_value=True)

        await service._initialize_existing_clients()
        await asyncio.sleep(0)

        assert registry.get_client(self.MAC).online is False
        assert service._sync_reconnecting_client_volume.await_count == 1

    def _volume_service(self, hardware_results):
        volume_service = MagicMock()
        volume_service.volume_config = VolumeConfig()
        volume_service.state_store.set_client_volume = AsyncMock()
        volume_service.state_store.get_client_volume = MagicMock(return_value=None)
        volume_service.state_store.get_client_mute = MagicMock(return_value=False)
        volume_service.equalizer_controller.set_equalizer_volume = AsyncMock(
            side_effect=list(hardware_results)
        )
        volume_service.equalizer_controller.set_equalizer_mute = AsyncMock(return_value=True)
        volume_service.broadcast_volume_state = AsyncMock()
        return volume_service

    @pytest.mark.asyncio
    async def test_client_onconnect_retries_until_the_hardware_takes_the_volume(self):
        """Client.OnConnect is the path a rebooting satellite arrives on, and it had no retry.

        Its snapclient reaches snapserver seconds before its API answers on 8001,
        so the first apply fails; the handler used to give up, leaving the client
        offline and muted until an unrelated event happened to retry it.
        """
        service, registry, _, _ = await self._service()
        service.set_volume_service(self._volume_service([False, True]))

        with patch("backend.core.multiroom.websocket.asyncio.sleep", AsyncMock()):
            await service._handle_client_connect({"client": {
                "id": self.MAC,
                "config": {"name": "Bureau", "volume": {"percent": 100}},
                "host": {"name": "milo-client", "ip": f"::ffff:{self.IP}", "mac": self.MAC},
            }})
            await drain_background_tasks()

        assert registry.get_client(self.MAC).online is True

    @pytest.mark.asyncio
    async def test_a_reconnecting_client_gets_the_current_snapclient_buffer_config(
        self, no_satellite_network
    ):
        """Buffer settings changed while a satellite was away never reached it.

        Only Client.OnConnect pushed them, so a client that came back through
        Server.OnUpdate or the reconcile sweep kept the buffer it booted with —
        which is what the setting exists to correct.
        """
        service, registry, _, _ = await self._service()
        await registry.register_client(self.MAC, "Bureau", self.IP, host="milo-client")
        service.set_volume_service(self._volume_service([True]))

        await service._sync_reconnecting_client_volume(self.MAC, max_retries=0, retry_delay=0)
        await drain_background_tasks()

        assert len(no_satellite_network) == 1
        method, url, body = no_satellite_network[0]
        assert method == "put"
        assert url.startswith(f"http://{self.IP}:")
        assert url.endswith("/snapclient/config")
        # Nothing stored here, so the pair must resolve to the one declaration
        # of the default. This push used to carry its own `80`/`4` literals —
        # a second default for a setting DEFAULT_SNAPCLIENT_CONFIG owns, which
        # a satellite would have been re-synced to on every reconnection.
        assert body == {
            "buffer_time": DEFAULT_SNAPCLIENT_CONFIG["buffer_time"],
            "fragments": DEFAULT_SNAPCLIENT_CONFIG["fragments"],
        }


# =============================================================================
# Registry persistence
# =============================================================================

class _StoringSettings:
    """A settings service that actually keeps what it is given.

    Stands in for SettingsService — the outside world on the persistence side —
    rather than for the registry's own code: `_persist_state` writes through
    `set_settings`, `_load_persisted_state` reads through `get_setting`, and
    what this makes assertable is that the two agree without either being
    restated in the test.
    """

    def __init__(self, stored: dict | None = None):
        self.stored = dict(stored or {})
        self.writes = 0

    async def get_setting(self, key):
        return self.stored.get(key)

    async def set_setting(self, key, value):
        await self.set_settings({key: value})

    async def set_settings(self, updates):
        self.writes += 1
        self.stored.update(updates)


class TestRegistryPersistenceRoundTrip:
    """What the registry holds must survive a backend restart — all three parts.

    What breaks when this fails is worse than "the data is missing": clients,
    zones and per-client EQ are persisted through a single path that always
    serialises the CURRENT in-memory state, so a section that fails to load is
    ERASED by the first mutation that follows. A zone that did not come back is
    a zone the next client registration deletes from settings.json.

    Measured 2026-08-25: only the clients branch of `_load_persisted_state` ran
    under the whole suite. The zones and client_equalizer branches were at 0 %.

    Consumers: the multiroom screen (zones, members), and every satellite —
    `_sync_standalone_equalizer_to_client` re-pushes the stored EQ record on
    admission, so an EQ that did not load is an EQ the speaker loses.
    """

    A = "aa:bb:cc:dd:ee:01"
    B = "aa:bb:cc:dd:ee:02"

    async def _populated(self, settings):
        registry = ClientRegistryService(settings_service=settings)
        await registry.initialize()
        await registry.register_client(self.A, "Salon", "192.168.1.10", host="milo-client")
        await registry.register_client(self.B, "Bureau", "192.168.1.11", host="milo-client")
        await registry.create_zone("z1", "Rez-de-chaussée", [self.A, self.B])
        await registry.set_client_equalizer(self.A, EqualizerSettings(
            enabled=False,
            filters=[EqFilter(id="eq_band_00", frequency=120, gain=-4.5, q=0.9)],
            mono=True,
        ))
        return registry

    async def _reloaded(self, settings):
        registry = ClientRegistryService(settings_service=settings)
        await registry.initialize()
        return registry

    async def test_a_zone_and_its_members_come_back(self):
        """Without this the multiroom screen loses every group on a restart."""
        settings = _StoringSettings()
        await self._populated(settings)

        reloaded = await self._reloaded(settings)

        zone = reloaded.get_zone("z1")
        assert zone is not None, "the zones branch of the load never ran"
        assert zone.name == "Rez-de-chaussée"
        assert set(zone.client_ids) == {self.A, self.B}
        assert reloaded.get_client(self.A).zone_id == "z1"

    async def test_a_client_equalizer_record_comes_back_whole(self):
        """The record is what the satellite is re-pushed on its next admission.

        Losing it does not reset the UI only: `_sync_standalone_equalizer_to_client`
        sends whatever the registry holds, so an empty record silently flattens
        the speaker's EQ, bypass flag and mono on reconnection.
        """
        settings = _StoringSettings()
        await self._populated(settings)

        eq = (await self._reloaded(settings)).get_client_equalizer(self.A)

        assert eq is not None, "the client_equalizer branch of the load never ran"
        assert eq.enabled is False
        assert eq.mono is True
        assert [(f.frequency, f.gain) for f in eq.filters] == [(120, -4.5)]

    async def test_a_corrupt_section_costs_every_section_after_it(self):
        """One unreadable entry is not one lost setting — the load is sequential.

        Clients, then zones, then client_equalizer, all under a single
        try/except. A zone that will not deserialise therefore takes the EQ
        records of the whole fleet with it, silently, and the boot still reports
        success. Combined with the write-back above, that is how a single bad
        entry ends up erasing two sections from settings.json.
        """
        settings = _StoringSettings()
        await self._populated(settings)
        settings.stored["multiroom.zones"] = {"z1": {"client_ids": "aa:bb"}}

        reloaded = await self._reloaded(settings)

        assert reloaded._initialized is True, "the boot must not be taken down by it"
        assert reloaded.get_client(self.A) is not None, "the section before it survived"
        assert reloaded.get_zone("z1") is None
        assert reloaded.get_client_equalizer(self.A) is None, "the section after it did not load"

    async def test_a_reload_followed_by_a_write_does_not_erase_the_other_sections(self):
        """The consequence that makes a silent load failure destructive.

        `_persist_state` serialises the whole current state in one write, so
        anything the load did not bring back is gone from settings.json the
        moment anything else changes — a client registration is enough.
        """
        settings = _StoringSettings()
        await self._populated(settings)
        reloaded = await self._reloaded(settings)

        await reloaded.register_client("aa:bb:cc:dd:ee:03", "Cuisine", "192.168.1.12")

        assert set(settings.stored["multiroom.zones"]) == {"z1"}
        assert self.A in settings.stored["multiroom.client_equalizer"]


class TestEqIndependentFlag:
    """A zone member that detaches its EQ must stay detached across a restart.

    `PUT /api/multiroom/clients/{mac}/eq-independent` is the only writer, and
    `api/multiroom.py` delegates the whole effect here — its own test replaces
    this method with a mock, so nothing ever ran it. Two things hang on it: the
    flag is persisted (or the detach is undone by the next boot, and
    MultiroomEqualizerService silently folds the client back into the zone
    fan-out), and CLIENT_UPDATED is emitted (or the EQ tab strip in
    `frontend/src/components/multiroom/` keeps showing it as a zone member).
    """

    MAC = "aa:bb:cc:dd:ee:01"

    async def _registry(self, settings):
        registry = ClientRegistryService(settings_service=settings)
        await registry.initialize()
        await registry.register_client(self.MAC, "Salon", "192.168.1.10")
        return registry

    async def test_the_flag_survives_a_restart(self):
        settings = _StoringSettings()
        registry = await self._registry(settings)

        assert await registry.set_client_eq_independent(self.MAC, True) is not None

        reloaded = ClientRegistryService(settings_service=settings)
        await reloaded.initialize()
        assert reloaded.get_client(self.MAC).eq_independent is True

    async def test_the_change_is_announced_to_the_frontend(self):
        settings = _StoringSettings()
        registry = await self._registry(settings)
        seen = []

        async def record(event_type, data):
            seen.append((event_type, data))

        registry.subscribe(record)
        await registry.set_client_eq_independent(self.MAC, True)

        updates = [d for t, d in seen if t == RegistryEventType.CLIENT_UPDATED]
        assert updates, "no CLIENT_UPDATED — the EQ tab strip never regroups"
        assert updates[-1]["client"]["eq_independent"] is True

    async def test_an_unknown_client_is_refused_without_a_write(self):
        """The mac comes from the URL path; an unknown one must not persist."""
        settings = _StoringSettings()
        registry = await self._registry(settings)
        writes_before = settings.writes

        assert await registry.set_client_eq_independent("no:such:client", True) is None
        assert settings.writes == writes_before


@pytest.mark.asyncio
class TestTheZoneAClientLeavesIsAnnounced:
    """Membership moves have two sides, and the registry used to announce one.

    `add_client_to_zone` and `create_zone` both take a client out of the zone it
    was in. Only the zone a departure *destroyed* was ever emitted, so a
    surviving one went stale in every subscriber at once — and silently: the
    registry's own state stayed consistent, no route failed, nothing went red.
    """

    @staticmethod
    async def _registry():
        settings = AsyncMock()
        settings.get_setting = AsyncMock(return_value=None)
        settings.set_settings = AsyncMock()
        registry = ClientRegistryService(settings_service=settings)
        await registry.initialize()
        for i in range(5):
            await registry.register_client(f"c{i}", f"Client {i}", f"192.168.1.1{i}")
        return registry

    @staticmethod
    def _zone_events(registry):
        """Every zone id the registry announced, in order."""
        seen = []

        async def spy(event_type, data):
            if event_type in (
                RegistryEventType.ZONE_CREATED,
                RegistryEventType.ZONE_UPDATED,
                RegistryEventType.ZONE_DELETED,
            ):
                seen.append((event_type, data["zone_id"]))

        registry.subscribe(spy)
        return seen

    async def test_moving_out_of_a_surviving_zone_announces_it(self):
        """The zone that lost a member changed as much as the one that gained it.

        Nothing else recomputes it: the volume store keeps the mover in the old
        zone's member list — and the ownership rule commits a zone delta to an
        absent client unconditionally — while the crossover service never
        recalculates the band split the departure changed.
        """
        registry = await self._registry()
        await registry.create_zone("z1", "Salon", ["c0", "c1", "c2"])
        await registry.create_zone("z2", "Bureau", ["c3", "c4"])
        seen = self._zone_events(registry)

        await registry.add_client_to_zone("z2", "c2")

        assert registry.get_zone("z1").client_ids == ["c0", "c1"]
        assert ("zone_updated", "z1") in seen
        # The departure lands before the arrival, so no subscriber that keys
        # clients by zone ever holds the same mac in two zones.
        assert seen.index(("zone_updated", "z1")) < seen.index(("zone_updated", "z2"))

    async def test_the_volume_store_stops_moving_a_client_that_left(self):
        """Driven through the real subscriber, because the fault is what the
        subscriber ends up holding, not what the registry returns."""
        from backend.core.models.volume import VolumeConfig
        from backend.core.volume.state import VolumeStateStore

        registry = await self._registry()
        settings = AsyncMock()
        settings.get_setting = AsyncMock(return_value=None)
        store = VolumeStateStore(settings)
        store.set_volume_config(VolumeConfig())
        store.set_registry(registry)

        await registry.create_zone("z1", "Salon", ["c0", "c1", "c2"])
        await registry.create_zone("z2", "Bureau", ["c3", "c4"])
        await registry.add_client_to_zone("z2", "c2")

        assert "c2" not in store._zones["z1"].client_ids
        assert "c2" in store._zones["z2"].client_ids

    async def test_creating_a_zone_from_a_client_that_is_in_one_moves_it(self):
        """A client in two zones at once is unrecoverable through the UI: both
        zones fan EQ and volume at it, `get_zone_for_client` can only name one,
        and the zone editor refuses to drop a zone below two members."""
        registry = await self._registry()
        await registry.create_zone("z1", "Salon", ["c0", "c1", "c2"])
        seen = self._zone_events(registry)

        await registry.create_zone("z2", "Bureau", ["c0", "c3"])

        listing_c0 = [z.id for z in registry.get_all_zones().values() if "c0" in z.client_ids]
        assert listing_c0 == ["z2"]
        assert registry.get_client("c0").zone_id == "z2"
        assert ("zone_updated", "z1") in seen

    async def test_a_move_that_empties_the_old_zone_still_dissolves_it(self):
        """The branch that already worked, kept: two members minus one is not a
        zone, and its remaining member goes standalone."""
        registry = await self._registry()
        await registry.create_zone("z1", "Salon", ["c0", "c1"])
        await registry.create_zone("z2", "Bureau", ["c2", "c3"])
        seen = self._zone_events(registry)

        await registry.add_client_to_zone("z2", "c1")

        assert registry.get_zone("z1") is None
        assert registry.get_client("c0").zone_id is None
        assert ("zone_deleted", "z1") in seen
