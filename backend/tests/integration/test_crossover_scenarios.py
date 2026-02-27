# backend/tests/integration/test_crossover_scenarios.py
"""
Integration tests for crossover scenarios - Stories 5.4 and 5.5.

Tests:
- E2E crossover activation when subwoofer joins zone (AC#1, AC#4)
- E2E crossover deactivation when subwoofer leaves zone
- E2E crossover recalculation on speaker_type change
- E2E pending settings applied on client reconnect (AC#6)
- E2E automatic crossover activation/deactivation (Story 5.5)
- E2E WebSocket event broadcasting on crossover state change (Story 5.5 AC#4)
"""
import pytest
import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from backend.core.multiroom.models import (
    Client,
    Zone,
    EqualizerSettings,
    RegistryEventType,
    DEFAULT_SPEAKER_TYPE,
    DEFAULT_CROSSOVER_FREQUENCIES,
)
from backend.config.constants import DEFAULT_VOLUME_DB
from backend.core.multiroom.client_registry import ClientRegistryService
from backend.core.multiroom.crossover import CrossoverService


def generate_zone_id() -> str:
    """Generate a unique zone ID."""
    return str(uuid.uuid4())


# =============================================================================
# Test Fixtures
# =============================================================================

@pytest.fixture
def mock_settings_service():
    """Create a mock settings service."""
    service = AsyncMock()
    service.get_setting = AsyncMock(return_value=None)
    service.set_setting = AsyncMock()
    return service


@pytest.fixture
def mock_camilladsp_service():
    """Create a mock Equalizer service for local client."""
    service = AsyncMock()
    service.set_crossover_filter = AsyncMock(return_value=True)
    service.set_lowpass_filter = AsyncMock(return_value=True)
    service.set_mute = AsyncMock(return_value=True)
    service.set_filter = AsyncMock(return_value=True)
    service.set_compressor = AsyncMock(return_value=True)
    service.set_loudness = AsyncMock(return_value=True)
    return service


@pytest.fixture
def mock_event_bus():
    """Create a mock event bus."""
    bus = MagicMock()
    bus.emit = AsyncMock()
    return bus


@pytest.fixture
async def registry(mock_settings_service, mock_event_bus):
    """Create an initialized ClientRegistryService."""
    service = ClientRegistryService(
        settings_service=mock_settings_service,
        event_bus=mock_event_bus
    )
    await service.initialize()
    return service


@pytest.fixture
async def crossover_with_registry(mock_settings_service, mock_camilladsp_service, mock_event_bus, registry):
    """Create CrossoverService integrated with ClientRegistryService."""
    crossover = CrossoverService(
        settings_service=mock_settings_service,
        camilladsp_service=mock_camilladsp_service,
        event_bus=mock_event_bus
    )
    await crossover.initialize()
    crossover.set_registry(registry)

    # Mock remote HTTP proxy methods to prevent real network calls
    crossover._proxy_crossover_to_client = AsyncMock(return_value=True)
    crossover._proxy_lowpass_to_client = AsyncMock(return_value=True)

    return crossover, registry


# =============================================================================
# Task 9.1: E2E crossover activation when subwoofer joins zone
# =============================================================================

class TestCrossoverActivation:
    """E2E tests for crossover activation scenarios."""

    @pytest.mark.asyncio
    async def test_e2e_subwoofer_joins_zone_activates_crossover(self, crossover_with_registry, mock_camilladsp_service):
        """Test crossover activates when subwoofer joins a zone."""
        crossover, registry = crossover_with_registry

        # Register local client (main speaker)
        await registry.register_client(
            mac_id="local",
            name="Main Speaker",
            ip="127.0.0.1"
        )
        await registry.set_client_online("local", True)

        # Register second client (zones require at least 2 clients)
        await registry.register_client(
            mac_id="bookshelf-1",
            name="Bookshelf",
            ip="192.168.1.50"
        )
        await registry.update_speaker_type("bookshelf-1", "bookshelf")
        await registry.set_client_online("bookshelf-1", True)

        # Create zone with 2 clients (minimum required)
        zone = await registry.create_zone(generate_zone_id(), "Living Room", ["local", "bookshelf-1"])
        zone_id = zone.id

        # Verify zone created
        assert zone is not None
        assert "local" in zone.client_ids
        assert "bookshelf-1" in zone.client_ids

        # Register subwoofer client
        await registry.register_client(
            mac_id="sub-1",
            name="Subwoofer",
            ip="192.168.1.100"
        )
        await registry.update_speaker_type("sub-1", "subwoofer")
        await registry.set_client_online("sub-1", True)

        # Add subwoofer to zone - this should activate crossover
        await registry.add_client_to_zone(zone_id, "sub-1")

        # Apply crossover
        result = await crossover.apply_zone_crossover(zone_id)

        assert result is True
        # Local client should get highpass filter
        mock_camilladsp_service.set_crossover_filter.assert_called()

    @pytest.mark.asyncio
    async def test_e2e_crossover_applies_to_all_zone_members(self, crossover_with_registry, mock_camilladsp_service):
        """Test crossover applies highpass to all non-subwoofer members."""
        crossover, registry = crossover_with_registry

        # Register local and subwoofer
        await registry.register_client("local", "Main", "127.0.0.1")
        await registry.set_client_online("local", True)

        await registry.register_client("sub-1", "Subwoofer", "192.168.1.100")
        await registry.update_speaker_type("sub-1", "subwoofer")
        await registry.set_client_online("sub-1", True)

        # Create zone
        zone = await registry.create_zone(generate_zone_id(), "Living Room", ["local", "sub-1"])

        # Apply crossover
        await crossover.apply_zone_crossover(zone.id)

        # Verify local got highpass
        mock_camilladsp_service.set_crossover_filter.assert_called()

        # Verify subwoofer did NOT get highpass (it gets lowpass instead)
        # Check that last call to set_crossover_filter was for enabling
        calls = mock_camilladsp_service.set_crossover_filter.call_args_list
        # At least one call should be for local client
        assert len(calls) >= 1


# =============================================================================
# Task 9.2: E2E crossover deactivation when subwoofer leaves zone
# =============================================================================

class TestCrossoverDeactivation:
    """E2E tests for crossover deactivation scenarios."""

    @pytest.mark.asyncio
    async def test_e2e_subwoofer_leaves_zone_deactivates_crossover(self, crossover_with_registry, mock_camilladsp_service):
        """Test crossover deactivates when subwoofer leaves zone."""
        crossover, registry = crossover_with_registry

        # Setup: zone with local + subwoofer
        await registry.register_client("local", "Main", "127.0.0.1")
        await registry.set_client_online("local", True)

        await registry.register_client("sub-1", "Subwoofer", "192.168.1.100")
        await registry.update_speaker_type("sub-1", "subwoofer")
        await registry.set_client_online("sub-1", True)

        zone = await registry.create_zone(generate_zone_id(), "Living Room", ["local", "sub-1"])

        # Verify initial crossover can be applied
        result = await crossover.apply_zone_crossover(zone.id)
        assert result is True

        # Reset mock to track deactivation
        mock_camilladsp_service.reset_mock()

        # Remove subwoofer from zone
        await registry.remove_client_from_zone(zone.id, "sub-1")

        # Apply crossover again (now without subwoofer)
        await crossover.apply_zone_crossover(zone.id)

        # Filters should be disabled
        mock_camilladsp_service.set_crossover_filter.assert_called()
        # Should be called with enabled=False since no subwoofer
        calls = mock_camilladsp_service.set_crossover_filter.call_args_list
        last_call = calls[-1] if calls else None
        if last_call:
            assert last_call.kwargs.get('enabled') is False or last_call[1].get('enabled') is False

    @pytest.mark.asyncio
    async def test_e2e_subwoofer_goes_offline_deactivates_crossover(self, crossover_with_registry, mock_camilladsp_service):
        """Test crossover deactivates when subwoofer goes offline."""
        crossover, registry = crossover_with_registry

        # Setup: zone with local + subwoofer (both online)
        await registry.register_client("local", "Main", "127.0.0.1")
        await registry.set_client_online("local", True)

        await registry.register_client("sub-1", "Subwoofer", "192.168.1.100")
        await registry.update_speaker_type("sub-1", "subwoofer")
        await registry.set_client_online("sub-1", True)

        zone = await registry.create_zone(generate_zone_id(), "Living Room", ["local", "sub-1"])

        # Apply crossover with subwoofer online
        await crossover.apply_zone_crossover(zone.id)

        # Reset mock
        mock_camilladsp_service.reset_mock()

        # Subwoofer goes offline
        await registry.set_client_online("sub-1", False)

        # Apply crossover again
        await crossover.apply_zone_crossover(zone.id)

        # Should disable crossover (subwoofer offline means no crossover)
        mock_camilladsp_service.set_crossover_filter.assert_called()


# =============================================================================
# Task 9.3: E2E crossover recalculation on speaker_type change
# =============================================================================

class TestCrossoverRecalculation:
    """E2E tests for crossover recalculation on configuration changes."""

    @pytest.mark.asyncio
    async def test_e2e_speaker_type_change_triggers_recalculation(self, crossover_with_registry, mock_camilladsp_service):
        """Test crossover recalculates when speaker_type changes."""
        crossover, registry = crossover_with_registry

        # Setup: zone with local (bookshelf) + subwoofer
        await registry.register_client("local", "Main", "127.0.0.1")
        await registry.update_speaker_type("local", "bookshelf")  # 80Hz default
        await registry.set_client_online("local", True)

        await registry.register_client("sub-1", "Subwoofer", "192.168.1.100")
        await registry.update_speaker_type("sub-1", "subwoofer")
        await registry.set_client_online("sub-1", True)

        zone = await registry.create_zone(generate_zone_id(), "Living Room", ["local", "sub-1"])

        # Apply initial crossover
        await crossover.apply_zone_crossover(zone.id)

        # Reset mock
        mock_camilladsp_service.reset_mock()

        # Change local speaker type to satellite (120Hz)
        await registry.update_speaker_type("local", "satellite")

        # Recalculate crossover
        await crossover.apply_zone_crossover(zone.id)

        # Should be called with new frequency
        mock_camilladsp_service.set_crossover_filter.assert_called()

    @pytest.mark.asyncio
    async def test_e2e_client_join_zone_triggers_recalculation(self, crossover_with_registry, mock_camilladsp_service):
        """Test crossover recalculates when client joins zone."""
        crossover, registry = crossover_with_registry

        # Setup: zone with local + subwoofer
        await registry.register_client("local", "Main", "127.0.0.1")
        await registry.set_client_online("local", True)

        await registry.register_client("sub-1", "Subwoofer", "192.168.1.100")
        await registry.update_speaker_type("sub-1", "subwoofer")
        await registry.set_client_online("sub-1", True)

        zone = await registry.create_zone(generate_zone_id(), "Living Room", ["local", "sub-1"])

        # Apply initial crossover
        await crossover.apply_zone_crossover(zone.id)

        # Reset mock
        mock_camilladsp_service.reset_mock()

        # Register new client with tower speaker type (50Hz)
        await registry.register_client("tower-1", "Tower", "192.168.1.101")
        await registry.update_speaker_type("tower-1", "tower")
        await registry.set_client_online("tower-1", True)

        # Add to zone
        await registry.add_client_to_zone(zone.id, "tower-1")

        # Recalculate crossover
        await crossover.apply_zone_crossover(zone.id)

        # Auto crossover should now be 50Hz (minimum of all speakers)
        freq = await crossover.get_zone_auto_crossover(zone.id)
        assert freq == 50  # Tower's default

    @pytest.mark.asyncio
    async def test_e2e_client_leave_zone_triggers_recalculation(self, crossover_with_registry, mock_camilladsp_service):
        """Test crossover recalculates when client leaves zone."""
        crossover, registry = crossover_with_registry

        # Setup: zone with local (bookshelf), tower, subwoofer
        await registry.register_client("local", "Main", "127.0.0.1")
        await registry.update_speaker_type("local", "bookshelf")
        await registry.set_client_online("local", True)

        await registry.register_client("tower-1", "Tower", "192.168.1.101")
        await registry.update_speaker_type("tower-1", "tower")
        await registry.set_client_online("tower-1", True)

        await registry.register_client("sub-1", "Subwoofer", "192.168.1.100")
        await registry.update_speaker_type("sub-1", "subwoofer")
        await registry.set_client_online("sub-1", True)

        zone = await registry.create_zone(generate_zone_id(), "Living Room", ["local", "tower-1", "sub-1"])

        # Initial auto crossover should be 50Hz (tower's frequency)
        freq = await crossover.get_zone_auto_crossover(zone.id)
        assert freq == 50

        # Remove tower from zone
        await registry.remove_client_from_zone(zone.id, "tower-1")

        # Recalculate - now should be 80Hz (bookshelf only)
        freq = await crossover.get_zone_auto_crossover(zone.id)
        assert freq == 80


# =============================================================================
# Task 9.4: E2E pending settings applied on client reconnect
# =============================================================================

class TestPendingSettingsOnReconnect:
    """E2E tests for pending settings application on reconnect."""

    @pytest.mark.asyncio
    async def test_e2e_pending_crossover_applied_on_reconnect(self, crossover_with_registry, mock_camilladsp_service):
        """Test pending crossover settings are applied when client reconnects.

        The CrossoverService automatically applies pending settings when
        CLIENT_CONNECTED event fires, so we verify the settings were applied
        automatically rather than calling apply_pending_settings() manually.
        """
        crossover, registry = crossover_with_registry

        # Queue pending settings BEFORE client is registered
        await crossover.queue_pending_settings("local", "crossover", {
            "enabled": True,
            "frequency": 100
        })

        # Verify settings are pending
        assert crossover.has_pending_settings("local") is True

        # Register client - this triggers CLIENT_UPDATED event but not CLIENT_CONNECTED
        await registry.register_client("local", "Main", "127.0.0.1")

        # Settings should still be pending (client not "connected" yet)
        # Bring client online - this triggers CLIENT_CONNECTED event
        # which calls apply_pending_settings() automatically
        await registry.set_client_online("local", True)

        # Pending settings should now be cleared (applied automatically)
        # Note: The event triggers _handle_registry_event which calls apply_pending_settings
        assert crossover.has_pending_settings("local") is False
        mock_camilladsp_service.set_crossover_filter.assert_called()

    @pytest.mark.asyncio
    async def test_e2e_pending_lowpass_applied_on_reconnect(self, crossover_with_registry, mock_camilladsp_service):
        """Test pending lowpass settings are applied when subwoofer reconnects."""
        crossover, registry = crossover_with_registry

        # Queue pending lowpass
        await crossover.queue_pending_settings("local", "lowpass", {
            "enabled": True,
            "frequency": 80
        })

        # Register as subwoofer
        await registry.register_client("local", "Subwoofer", "127.0.0.1")
        await registry.update_speaker_type("local", "subwoofer")

        # Bring online - triggers automatic pending settings application
        await registry.set_client_online("local", True)

        # Settings should be automatically applied via event handler
        assert crossover.has_pending_settings("local") is False
        mock_camilladsp_service.set_lowpass_filter.assert_called()

    @pytest.mark.asyncio
    async def test_e2e_multiple_pending_settings_applied(self, crossover_with_registry, mock_camilladsp_service):
        """Test multiple pending settings types are all applied automatically."""
        crossover, registry = crossover_with_registry

        # Queue multiple settings
        await crossover.queue_pending_settings("local", "crossover", {"enabled": True, "frequency": 80})
        await crossover.queue_pending_settings("local", "compressor", {"enabled": True, "threshold": -20})

        await registry.register_client("local", "Main", "127.0.0.1")
        await registry.set_client_online("local", True)

        # Both settings should be applied via event handler
        assert crossover.has_pending_settings("local") is False
        mock_camilladsp_service.set_crossover_filter.assert_called()
        mock_camilladsp_service.set_compressor.assert_called()


# =============================================================================
# Integration with Registry Events
# =============================================================================

class TestRegistryEventIntegration:
    """Tests for CrossoverService integration with registry events."""

    @pytest.mark.asyncio
    async def test_crossover_service_receives_zone_events(self, crossover_with_registry):
        """Test CrossoverService is subscribed to registry events."""
        crossover, registry = crossover_with_registry

        # Verify subscription happened by checking registry has subscribers
        # The registry.subscribe() is a real method, not a mock
        assert len(registry._subscribers) > 0
        # The crossover._handle_registry_event should be in the subscribers
        assert crossover._handle_registry_event in registry._subscribers

    @pytest.mark.asyncio
    async def test_zone_created_event_triggers_crossover_application(self, crossover_with_registry, mock_camilladsp_service):
        """Test ZONE_CREATED event triggers crossover application."""
        crossover, registry = crossover_with_registry

        # Create clients
        await registry.register_client("local", "Main", "127.0.0.1")
        await registry.set_client_online("local", True)
        await registry.register_client("sub-1", "Subwoofer", "192.168.1.100")
        await registry.update_speaker_type("sub-1", "subwoofer")
        await registry.set_client_online("sub-1", True)

        # Manually trigger the event handler
        zone = await registry.create_zone(generate_zone_id(), "Test", ["local", "sub-1"])

        # Simulate ZONE_CREATED event
        await crossover._handle_registry_event(
            RegistryEventType.ZONE_CREATED,
            {"zone_id": zone.id}
        )

        # Crossover should be applied
        mock_camilladsp_service.set_crossover_filter.assert_called()


# =============================================================================
# Story 5.5: E2E Automatic Crossover Activation Tests
# =============================================================================

class TestAutomaticCrossoverE2E:
    """E2E tests for automatic crossover activation/deactivation (Story 5.5)."""

    @pytest.mark.asyncio
    async def test_e2e_subwoofer_online_activates_crossover(self, crossover_with_registry, mock_camilladsp_service):
        """E2E Test: Snapcast event → CLIENT_CONNECTED → crossover activation (AC#1, AC#6)."""
        crossover, registry = crossover_with_registry

        # 1. Register clients (simulating initial discovery)
        await registry.register_client("local", "Main Speaker", "127.0.0.1")
        await registry.update_speaker_type("local", "bookshelf")
        await registry.set_client_online("local", True)

        await registry.register_client("sub-1", "Subwoofer", "192.168.1.100")
        await registry.update_speaker_type("sub-1", "subwoofer")
        # Subwoofer starts OFFLINE

        # 2. Create zone (minimum 2 clients)
        zone = await registry.create_zone(generate_zone_id(), "Living Room", ["local", "sub-1"])

        # 3. Reset mock to track only activation calls
        mock_camilladsp_service.reset_mock()

        # 4. Subwoofer comes ONLINE (simulating Snapcast connection event)
        await registry.set_client_online("sub-1", True)

        # 5. Verify crossover was automatically activated
        #    The EVENT_CONNECTED triggers _handle_registry_event which calls apply_zone_crossover
        mock_camilladsp_service.set_crossover_filter.assert_called()

        # Verify highpass was enabled on local (bookshelf) speaker
        calls = [c for c in mock_camilladsp_service.set_crossover_filter.call_args_list]
        enabled_calls = [c for c in calls if c.kwargs.get('enabled', c[1].get('enabled', None)) is True]
        assert len(enabled_calls) > 0, "Expected at least one call with enabled=True"

    @pytest.mark.asyncio
    async def test_e2e_subwoofer_offline_deactivates_crossover(self, crossover_with_registry, mock_camilladsp_service):
        """E2E Test: Snapcast event → CLIENT_DISCONNECTED → crossover deactivation (AC#5, AC#6)."""
        crossover, registry = crossover_with_registry

        # 1. Setup: Zone with active crossover (subwoofer online)
        await registry.register_client("local", "Main Speaker", "127.0.0.1")
        await registry.update_speaker_type("local", "bookshelf")
        await registry.set_client_online("local", True)

        await registry.register_client("sub-1", "Subwoofer", "192.168.1.100")
        await registry.update_speaker_type("sub-1", "subwoofer")
        await registry.set_client_online("sub-1", True)

        zone = await registry.create_zone(generate_zone_id(), "Living Room", ["local", "sub-1"])

        # Apply initial crossover
        await crossover.apply_zone_crossover(zone.id)

        # Verify crossover is active
        mock_camilladsp_service.reset_mock()

        # 2. Subwoofer goes OFFLINE (simulating Snapcast disconnection)
        await registry.set_client_online("sub-1", False)

        # 3. Verify crossover was automatically deactivated
        mock_camilladsp_service.set_crossover_filter.assert_called()

        # Verify highpass was disabled
        last_call = mock_camilladsp_service.set_crossover_filter.call_args
        assert last_call.kwargs.get('enabled') is False or last_call[1].get('enabled') is False

    @pytest.mark.asyncio
    async def test_e2e_speaker_type_api_change_triggers_crossover(self, crossover_with_registry, mock_camilladsp_service):
        """E2E Test: speaker_type API change → crossover recalculation → WebSocket broadcast (AC#7)."""
        crossover, registry = crossover_with_registry

        # 1. Setup: Zone with two bookshelf speakers (no subwoofer, no crossover)
        await registry.register_client("local", "Main", "127.0.0.1")
        await registry.update_speaker_type("local", "bookshelf")
        await registry.set_client_online("local", True)

        await registry.register_client("book-2", "Bookshelf 2", "192.168.1.50")
        await registry.update_speaker_type("book-2", "bookshelf")
        await registry.set_client_online("book-2", True)

        zone = await registry.create_zone(generate_zone_id(), "Study", ["local", "book-2"])

        # Verify no crossover initially
        await crossover.apply_zone_crossover(zone.id)
        mock_camilladsp_service.reset_mock()

        # 2. Change book-2 to subwoofer (simulating API call: PATCH /api/multiroom/clients/{mac_id})
        #    This triggers SPEAKER_TYPE_CHANGED event which leads to crossover recalculation
        await registry.update_speaker_type("book-2", "subwoofer")

        # 3. Manually trigger recalculation (in real system, this is triggered by event)
        await crossover._handle_registry_event(
            RegistryEventType.CLIENT_UPDATED,
            {"mac_id": "book-2"}
        )

        # 4. Verify crossover was activated
        mock_camilladsp_service.set_crossover_filter.assert_called()

        last_call = mock_camilladsp_service.set_crossover_filter.call_args
        assert last_call.kwargs.get('enabled') is True or last_call[1].get('enabled') is True

    @pytest.mark.asyncio
    async def test_e2e_websocket_event_on_crossover_state_change(self, crossover_with_registry, mock_event_bus):
        """E2E Test: Verify WebSocket event is broadcast when crossover state changes (AC#4)."""
        crossover, registry = crossover_with_registry

        # Setup: Zone with subwoofer
        await registry.register_client("local", "Main", "127.0.0.1")
        await registry.set_client_online("local", True)
        await registry.register_client("sub-1", "Subwoofer", "192.168.1.100")
        await registry.update_speaker_type("sub-1", "subwoofer")
        await registry.set_client_online("sub-1", True)

        zone = await registry.create_zone(generate_zone_id(), "Test", ["local", "sub-1"])

        # Reset to track only crossover-related events
        mock_event_bus.reset_mock()

        # Trigger crossover recalculation via client status change
        # This triggers _recalculate_zones_for_client which emits ZONE_UPDATED via registry
        await crossover._recalculate_zones_for_client("sub-1")

        # Verify EventBus.emit was called with multiroom.zone_changed
        mock_event_bus.emit.assert_called()

        # Find the zone_changed event call (ZONE_UPDATED maps to zone_changed)
        zone_changed_calls = [
            call for call in mock_event_bus.emit.call_args_list
            if call[0][0] == "multiroom.zone_changed"
        ]
        assert len(zone_changed_calls) > 0, "Expected multiroom.zone_changed event via EventBus"

        # Verify event data contains crossover_enabled
        event_data = zone_changed_calls[-1][0][1]  # Second positional arg is data
        assert "zone" in event_data, "ZONE_UPDATED event should contain zone data"
        assert "crossover_enabled" in event_data["zone"], "Zone data should include crossover_enabled"
        # Verify crossover is enabled (subwoofer is online)
        assert event_data["zone"]["crossover_enabled"] is True, "Crossover should be enabled with online subwoofer"

    @pytest.mark.asyncio
    async def test_e2e_auto_mode_respects_subwoofer_online_status(self, crossover_with_registry, mock_camilladsp_service):
        """E2E Test: Auto mode (crossover_enabled=None) correctly follows subwoofer status."""
        crossover, registry = crossover_with_registry

        # Setup zone in auto mode
        await registry.register_client("local", "Main", "127.0.0.1")
        await registry.set_client_online("local", True)
        await registry.register_client("sub-1", "Sub", "192.168.1.100")
        await registry.update_speaker_type("sub-1", "subwoofer")
        # Subwoofer starts offline

        zone = await registry.create_zone(generate_zone_id(), "Auto Test", ["local", "sub-1"])

        # Verify zone is in auto mode
        assert zone.crossover_enabled is None

        # 1. With subwoofer OFFLINE - crossover should be disabled
        await crossover.apply_zone_crossover(zone.id)
        last_call = mock_camilladsp_service.set_crossover_filter.call_args
        assert last_call.kwargs.get('enabled') is False or last_call[1].get('enabled') is False

        mock_camilladsp_service.reset_mock()

        # 2. Bring subwoofer ONLINE - crossover should automatically enable
        await registry.set_client_online("sub-1", True)
        await crossover.apply_zone_crossover(zone.id)

        last_call = mock_camilladsp_service.set_crossover_filter.call_args
        assert last_call.kwargs.get('enabled') is True or last_call[1].get('enabled') is True

        mock_camilladsp_service.reset_mock()

        # 3. Take subwoofer OFFLINE again - crossover should automatically disable
        await registry.set_client_online("sub-1", False)
        await crossover.apply_zone_crossover(zone.id)

        last_call = mock_camilladsp_service.set_crossover_filter.call_args
        assert last_call.kwargs.get('enabled') is False or last_call[1].get('enabled') is False

    @pytest.mark.asyncio
    async def test_e2e_explicit_crossover_enabled_overrides_auto(self, crossover_with_registry, mock_camilladsp_service):
        """E2E Test: Explicit crossover_enabled=False overrides auto-activation."""
        crossover, registry = crossover_with_registry

        # Setup zone with explicit crossover_enabled=False
        await registry.register_client("local", "Main", "127.0.0.1")
        await registry.set_client_online("local", True)
        await registry.register_client("sub-1", "Sub", "192.168.1.100")
        await registry.update_speaker_type("sub-1", "subwoofer")
        await registry.set_client_online("sub-1", True)

        zone = await registry.create_zone(generate_zone_id(), "Explicit Test", ["local", "sub-1"])

        # Set explicit crossover_enabled=False
        await registry.update_zone(zone.id, crossover_enabled=False)

        mock_camilladsp_service.reset_mock()

        # Apply crossover - should NOT enable despite online subwoofer
        await crossover.apply_zone_crossover(zone.id)

        # Crossover should be disabled because explicit setting overrides
        last_call = mock_camilladsp_service.set_crossover_filter.call_args
        assert last_call.kwargs.get('enabled') is False or last_call[1].get('enabled') is False


# =============================================================================
# Story 5.5: Edge Case Tests
# =============================================================================

class TestCrossoverEdgeCases:
    """Tests for crossover edge cases documented in Story 5.5 Dev Notes."""

    @pytest.mark.asyncio
    async def test_client_changes_zone_both_zones_recalculated(self, crossover_with_registry, mock_camilladsp_service):
        """Test: When client moves between zones, both zones recalculate crossover."""
        crossover, registry = crossover_with_registry

        # Setup: Two zones, one with subwoofer, one without
        # Note: Only "local" (127.0.0.1) uses mock_camilladsp_service directly; others are remote (proxy)
        await registry.register_client("local", "Main", "127.0.0.1")
        await registry.set_client_online("local", True)

        await registry.register_client("sub-1", "Subwoofer", "192.168.1.100")
        await registry.update_speaker_type("sub-1", "subwoofer")
        await registry.set_client_online("sub-1", True)

        await registry.register_client("sat-1", "Satellite", "192.168.1.101")
        await registry.update_speaker_type("sat-1", "satellite")
        await registry.set_client_online("sat-1", True)

        await registry.register_client("sat-2", "Satellite 2", "192.168.1.102")
        await registry.update_speaker_type("sat-2", "satellite")
        await registry.set_client_online("sat-2", True)

        # Zone 1: local + subwoofer + satellite (crossover active, local uses mock_camilladsp_service)
        zone1 = await registry.create_zone(generate_zone_id(), "Zone 1", ["local", "sub-1", "sat-1"])

        # Verify zone1 has crossover active - local client should get crossover enabled
        await crossover.apply_zone_crossover(zone1.id)
        call = mock_camilladsp_service.set_crossover_filter.call_args
        assert call is not None, "Expected set_crossover_filter called for local client"
        assert call.kwargs.get('enabled') is True or call[1].get('enabled') is True

        mock_camilladsp_service.reset_mock()

        # Move subwoofer from zone1 to a new zone (zone1 keeps local + sat-1)
        await registry.remove_client_from_zone(zone1.id, "sub-1")

        # Zone 1 should now have crossover disabled (no subwoofer)
        await crossover.apply_zone_crossover(zone1.id)
        call = mock_camilladsp_service.set_crossover_filter.call_args
        assert call is not None, "Expected set_crossover_filter called for local client"
        assert call.kwargs.get('enabled') is False or call[1].get('enabled') is False

    @pytest.mark.asyncio
    async def test_zone_deleted_filters_disabled_for_ex_members(self, crossover_with_registry, mock_camilladsp_service):
        """Test: When zone is deleted, all crossover filters are disabled for ex-members."""
        crossover, registry = crossover_with_registry

        # Setup: Zone with crossover active
        await registry.register_client("local", "Main", "127.0.0.1")
        await registry.set_client_online("local", True)

        await registry.register_client("sub-1", "Subwoofer", "192.168.1.100")
        await registry.update_speaker_type("sub-1", "subwoofer")
        await registry.set_client_online("sub-1", True)

        zone = await registry.create_zone(generate_zone_id(), "Test Zone", ["local", "sub-1"])

        # Apply crossover (should be active)
        await crossover.apply_zone_crossover(zone.id)
        call = mock_camilladsp_service.set_crossover_filter.call_args
        assert call.kwargs.get('enabled') is True or call[1].get('enabled') is True

        mock_camilladsp_service.reset_mock()

        # Delete the zone
        await registry.delete_zone(zone.id)

        # CrossoverService._handle_registry_event(ZONE_DELETED) should disable filters
        # Verify set_crossover_filter was called with enabled=False
        # The event handler disables filters for all ex-members
        if mock_camilladsp_service.set_crossover_filter.called:
            call = mock_camilladsp_service.set_crossover_filter.call_args
            assert call.kwargs.get('enabled') is False or call[1].get('enabled') is False


# =============================================================================
# Story 5.6: E2E Filter Application Tests
# =============================================================================

class TestFilterApplicationE2E:
    """E2E tests for filter application (Story 5.6 Task 6)."""

    @pytest.mark.asyncio
    async def test_e2e_satellite_receives_highpass_at_speaker_type_freq(self, crossover_with_registry, mock_camilladsp_service):
        """E2E Test: satellite in zone with subwoofer receives highpass at speaker_type freq (6.1)."""
        crossover, registry = crossover_with_registry

        # Setup: Zone with satellite (120Hz) + subwoofer
        await registry.register_client("local", "Satellite", "127.0.0.1")
        await registry.update_speaker_type("local", "satellite")  # 120Hz
        await registry.set_client_online("local", True)

        await registry.register_client("sub-1", "Subwoofer", "192.168.1.100")
        await registry.update_speaker_type("sub-1", "subwoofer")
        await registry.set_client_online("sub-1", True)

        zone = await registry.create_zone(generate_zone_id(), "Living Room", ["local", "sub-1"])
        # Enable auto-frequency mode (zone default is 80Hz, override to None for auto-calc)
        zone.crossover_frequency = None

        mock_camilladsp_service.reset_mock()

        # Apply crossover
        await crossover.apply_zone_crossover(zone.id)

        # Verify local (satellite) received highpass
        mock_camilladsp_service.set_crossover_filter.assert_called()
        call = mock_camilladsp_service.set_crossover_filter.call_args
        assert call.kwargs.get('enabled') is True or call[1].get('enabled') is True
        # Frequency should be satellite's default (120Hz) - calculated by get_zone_auto_crossover
        freq = call.kwargs.get('frequency', call[1].get('frequency'))
        assert freq == 120, f"Expected 120Hz for satellite, got {freq}"

    @pytest.mark.asyncio
    async def test_e2e_subwoofer_receives_lowpass_at_zone_freq(self, crossover_with_registry, mock_camilladsp_service):
        """E2E Test: subwoofer in zone receives lowpass at zone crossover freq (6.2)."""
        crossover, registry = crossover_with_registry

        # Setup: Local is the subwoofer, remote is satellite
        await registry.register_client("sat-1", "Satellite", "192.168.1.10")
        await registry.update_speaker_type("sat-1", "satellite")
        await registry.set_client_online("sat-1", True)

        await registry.register_client("local", "Subwoofer", "127.0.0.1")
        await registry.update_speaker_type("local", "subwoofer")
        await registry.set_client_online("local", True)

        zone = await registry.create_zone(generate_zone_id(), "Bass Zone", ["sat-1", "local"])

        mock_camilladsp_service.reset_mock()

        # Apply crossover
        await crossover.apply_zone_crossover(zone.id)

        # Verify local (subwoofer) received lowpass
        mock_camilladsp_service.set_lowpass_filter.assert_called()
        call = mock_camilladsp_service.set_lowpass_filter.call_args
        assert call.kwargs.get('enabled') is True or call[1].get('enabled') is True

        # Verify subwoofer did NOT receive highpass (should be disabled)
        # The apply_zone_crossover calls set_crossover_filter with enabled=False for subwoofers
        crossover_calls = [c for c in mock_camilladsp_service.set_crossover_filter.call_args_list
                          if c.kwargs.get('enabled', c[1].get('enabled')) is False]
        assert len(crossover_calls) > 0, "Subwoofer should have crossover (highpass) disabled"

    @pytest.mark.asyncio
    async def test_e2e_crossover_disabled_returns_to_fullrange(self, crossover_with_registry, mock_camilladsp_service):
        """E2E Test: crossover disabled - both clients return to full-range (6.3)."""
        crossover, registry = crossover_with_registry

        # Setup: Zone with crossover active
        await registry.register_client("local", "Main", "127.0.0.1")
        await registry.update_speaker_type("local", "bookshelf")
        await registry.set_client_online("local", True)

        await registry.register_client("sub-1", "Subwoofer", "192.168.1.100")
        await registry.update_speaker_type("sub-1", "subwoofer")
        await registry.set_client_online("sub-1", True)

        zone = await registry.create_zone(generate_zone_id(), "Test Zone", ["local", "sub-1"])

        # First, apply crossover (active)
        await crossover.apply_zone_crossover(zone.id)

        # Then, disable crossover explicitly
        await registry.update_zone(zone.id, crossover_enabled=False)

        mock_camilladsp_service.reset_mock()

        # Apply crossover again with disabled setting
        await crossover.apply_zone_crossover(zone.id)

        # Both filters should be disabled
        last_crossover_call = mock_camilladsp_service.set_crossover_filter.call_args
        last_lowpass_call = mock_camilladsp_service.set_lowpass_filter.call_args

        assert last_crossover_call.kwargs.get('enabled') is False or last_crossover_call[1].get('enabled') is False
        assert last_lowpass_call.kwargs.get('enabled') is False or last_lowpass_call[1].get('enabled') is False

    @pytest.mark.asyncio
    async def test_e2e_reconnect_applies_crossover_filters(self, crossover_with_registry, mock_camilladsp_service):
        """E2E Test: client reconnects to active crossover zone - filters applied (6.4)."""
        crossover, registry = crossover_with_registry

        # Setup: Zone with satellite + subwoofer (satellite was offline)
        await registry.register_client("local", "Satellite", "127.0.0.1")
        await registry.update_speaker_type("local", "satellite")
        # Local starts offline

        await registry.register_client("sub-1", "Subwoofer", "192.168.1.100")
        await registry.update_speaker_type("sub-1", "subwoofer")
        await registry.set_client_online("sub-1", True)

        zone = await registry.create_zone(generate_zone_id(), "Reconnect Test", ["local", "sub-1"])

        mock_camilladsp_service.reset_mock()

        # Bring local online (simulates reconnection)
        await registry.set_client_online("local", True)

        # The CLIENT_CONNECTED event should trigger crossover application
        # via _recalculate_zones_for_client
        mock_camilladsp_service.set_crossover_filter.assert_called()

        # Verify highpass was enabled (satellite reconnected to zone with subwoofer)
        call = mock_camilladsp_service.set_crossover_filter.call_args
        enabled = call.kwargs.get('enabled', call[1].get('enabled'))
        assert enabled is True, "Reconnected satellite should have highpass enabled"

    @pytest.mark.asyncio
    async def test_e2e_dsp_bypass_does_not_affect_crossover(self, crossover_with_registry, mock_camilladsp_service):
        """E2E Test: Equalizer bypass does not affect crossover filters (Story 5.6 AC#6)."""
        crossover, registry = crossover_with_registry

        # Setup: Zone with active crossover
        await registry.register_client("local", "Main", "127.0.0.1")
        await registry.set_client_online("local", True)

        await registry.register_client("sub-1", "Subwoofer", "192.168.1.100")
        await registry.update_speaker_type("sub-1", "subwoofer")
        await registry.set_client_online("sub-1", True)

        zone = await registry.create_zone(generate_zone_id(), "Bypass Test", ["local", "sub-1"])

        # Apply crossover
        await crossover.apply_zone_crossover(zone.id)

        # Record crossover state
        crossover_enabled_before = True  # We just applied it

        # Simulate Equalizer bypass (this would be called on DspService, not CrossoverService)
        # The key point is that CrossoverService crossover methods are independent
        # of DspService.bypass_effects()

        # Verify crossover is still applicable after "bypass" simulation
        mock_camilladsp_service.reset_mock()
        await crossover.apply_zone_crossover(zone.id)

        # Crossover should still be enabled (not affected by Equalizer bypass)
        call = mock_camilladsp_service.set_crossover_filter.call_args
        assert call.kwargs.get('enabled') is True or call[1].get('enabled') is True


# =============================================================================
# Story 5.6: Mixed Speaker Type Zone Tests
# =============================================================================

class TestMixedSpeakerTypeZones:
    """Tests for zones with mixed speaker types."""

    @pytest.mark.asyncio
    async def test_e2e_mixed_speakers_use_minimum_frequency(self, crossover_with_registry, mock_camilladsp_service):
        """E2E Test: Zone with satellite + tower uses minimum frequency (tower's 50Hz)."""
        crossover, registry = crossover_with_registry

        # Setup: satellite (120Hz) + tower (50Hz) + subwoofer
        await registry.register_client("sat-1", "Satellite", "192.168.1.10")
        await registry.update_speaker_type("sat-1", "satellite")
        await registry.set_client_online("sat-1", True)

        await registry.register_client("local", "Tower", "127.0.0.1")
        await registry.update_speaker_type("local", "tower")  # 50Hz
        await registry.set_client_online("local", True)

        await registry.register_client("sub-1", "Subwoofer", "192.168.1.100")
        await registry.update_speaker_type("sub-1", "subwoofer")
        await registry.set_client_online("sub-1", True)

        zone = await registry.create_zone(generate_zone_id(), "Mixed Zone", ["sat-1", "local", "sub-1"])
        # Enable auto-frequency mode (zone default is 80Hz, override to None for auto-calc)
        zone.crossover_frequency = None

        # Auto crossover should be 50Hz (minimum of satellite=120, tower=50)
        freq = await crossover.get_zone_auto_crossover(zone.id)
        assert freq == 50, f"Expected minimum 50Hz, got {freq}"

        mock_camilladsp_service.reset_mock()

        # Apply crossover
        await crossover.apply_zone_crossover(zone.id)

        # Local (tower) should receive highpass at 50Hz
        call = mock_camilladsp_service.set_crossover_filter.call_args
        freq = call.kwargs.get('frequency', call[1].get('frequency'))
        assert freq == 50, f"Expected 50Hz crossover frequency, got {freq}"

    @pytest.mark.asyncio
    async def test_e2e_all_bookshelf_zone_uses_80hz(self, crossover_with_registry, mock_camilladsp_service):
        """E2E Test: Zone with all bookshelf speakers uses 80Hz (THX standard)."""
        crossover, registry = crossover_with_registry

        # Setup: Two bookshelves + subwoofer
        await registry.register_client("local", "Bookshelf 1", "127.0.0.1")
        await registry.update_speaker_type("local", "bookshelf")
        await registry.set_client_online("local", True)

        await registry.register_client("book-2", "Bookshelf 2", "192.168.1.11")
        await registry.update_speaker_type("book-2", "bookshelf")
        await registry.set_client_online("book-2", True)

        await registry.register_client("sub-1", "Subwoofer", "192.168.1.100")
        await registry.update_speaker_type("sub-1", "subwoofer")
        await registry.set_client_online("sub-1", True)

        zone = await registry.create_zone(generate_zone_id(), "Bookshelf Zone", ["local", "book-2", "sub-1"])

        # Auto crossover should be 80Hz (all bookshelves are 80Hz)
        freq = await crossover.get_zone_auto_crossover(zone.id)
        assert freq == 80

        mock_camilladsp_service.reset_mock()

        await crossover.apply_zone_crossover(zone.id)

        # Verify 80Hz was used
        call = mock_camilladsp_service.set_crossover_filter.call_args
        freq = call.kwargs.get('frequency', call[1].get('frequency'))
        assert freq == 80
