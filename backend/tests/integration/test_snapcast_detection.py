# backend/tests/integration/test_snapcast_detection.py
"""
Integration tests for Story 1-3: Integrate Snapcast Client Detection.

Tests the end-to-end flow:
- Simulated Snapcast event -> ClientRegistryService update -> WebSocket broadcast

AC Coverage:
- AC1: Client connection detection
- AC2: Client disconnection detection
- AC5: Event timing (NFR2 < 100ms)
"""
import pytest
import time
from unittest.mock import AsyncMock, MagicMock

from backend.tests.conftest import attach_registry_broadcaster
from backend.core.multiroom.client_registry import ClientRegistryService
from backend.core.multiroom.websocket import SnapcastWebSocketService
from backend.core.multiroom.models import (
    DEFAULT_SPEAKER_TYPE,
)
from backend.config.constants import DEFAULT_VOLUME_DB


class TestSnapcastDetectionIntegration:
    """
    Integration tests for Snapcast client detection.

    Tests the full event flow from Snapcast notification to frontend WebSocket.
    """

    @pytest.fixture
    def mock_settings_service(self):
        """Create a mock settings service."""
        service = AsyncMock()
        service.get_setting = AsyncMock(return_value=None)
        service.set_setting = AsyncMock()
        return service

    @pytest.fixture
    async def registry(self, mock_settings_service):
        """Create and initialize a ClientRegistryService."""
        reg = ClientRegistryService(
            settings_service=mock_settings_service
        )
        await reg.initialize()
        return reg

    @pytest.fixture
    def mock_state_machine(self, registry):
        """Create a mock state machine that tracks broadcasts."""
        sm = MagicMock()
        sm.broadcasts = []

        async def track_broadcast(event):
            sm.broadcasts.append({
                "category": event.CATEGORY,
                "type": event.TYPE,
                "data": event.wire_data(),
                "timestamp": time.time()
            })

        sm.broadcast = track_broadcast
        return sm

    @pytest.fixture
    def mock_routing_service(self):
        """Create a mock routing service."""
        service = MagicMock()
        service.get_state = MagicMock(return_value={'multiroom_enabled': False})
        service.get_snapcast_status = AsyncMock(return_value={'multiroom_available': False})
        return service

    @pytest.fixture
    def ws_service(self, mock_state_machine, mock_routing_service, registry):
        """Create a SnapcastWebSocketService with mocked volume/snapcast services."""
        service = SnapcastWebSocketService(
            state_machine=mock_state_machine,
            routing_service=mock_routing_service
        )
        service.set_registry(registry)

        # Mock snapcast service so volume sync succeeds
        mock_snapcast = MagicMock()
        mock_snapcast.set_volume = AsyncMock(return_value=True)
        mock_snapcast.get_clients = AsyncMock(return_value=[])
        service._snapcast_service = mock_snapcast

        # Mock volume service so _apply_target_volume_to_client succeeds
        mock_volume_service = MagicMock()
        mock_volume_service.state_store = MagicMock()
        mock_volume_service.state_store.set_client_volume = AsyncMock()
        mock_volume_service.state_store.get_client_mute = MagicMock(return_value=False)
        mock_volume_service.equalizer_controller = MagicMock()
        mock_volume_service.equalizer_controller.set_equalizer_volume = AsyncMock(return_value=True)
        mock_volume_service.equalizer_controller.set_equalizer_mute = AsyncMock()
        mock_volume_service.broadcast_volume_state = AsyncMock()
        mock_volume_service.volume_config = MagicMock()
        mock_volume_service.volume_config.startup_volume_db = DEFAULT_VOLUME_DB
        service._volume_service = mock_volume_service

        return service

    # === End-to-End Flow Tests ===

    @pytest.mark.asyncio
    async def test_end_to_end_client_connect_flow(
        self, registry, mock_state_machine, ws_service
    ):
        """
        Test full flow: Snapcast Client.OnConnect -> registry -> WebSocket broadcast.

        AC1: Client connection detection triggers registry update and event broadcast.
        """
        attach_registry_broadcaster(registry, mock_state_machine)

        mac_addr = "aa:bb:cc:dd:ee:01"

        # Simulate Snapcast Client.OnConnect notification
        snapcast_notification = {
            "client": {
                "id": "snapcast-client-abc",
                "config": {
                    "name": "Kitchen Speakers",
                    "volume": {"percent": 100, "muted": False}
                },
                "host": {
                    "name": "milo-client-kitchen",
                    "ip": "192.168.1.150",
                    "mac": mac_addr
                }
            }
        }

        # Process the notification
        await ws_service._handle_client_connect(snapcast_notification)

        # Verify 1: Client is registered in registry (by mac_id)
        client = registry.get_client(mac_addr)
        assert client is not None, "Client should be registered in registry"
        assert client.name == "Kitchen Speakers"
        assert client.ip == "192.168.1.150"

        # Verify 2: Client is marked online
        assert client.online is True, "Client should be marked online"

        # Verify 3: WebSocket broadcasts were made
        assert len(mock_state_machine.broadcasts) >= 1, "Should have broadcast events"

        # Verify 4: Registry event was broadcast (category="multiroom" via ClientRegistryService._emit_event)
        registry_events = [
            b for b in mock_state_machine.broadcasts
            if b["category"] == "multiroom"
        ]
        assert len(registry_events) >= 1, "Should have multiroom registry event"

    @pytest.mark.asyncio
    async def test_end_to_end_client_disconnect_flow(
        self, registry, mock_state_machine, ws_service
    ):
        """
        Test full flow: Snapcast Client.OnDisconnect -> registry -> WebSocket broadcast.

        AC2: Client disconnection detection triggers offline status and event broadcast.
        """
        attach_registry_broadcaster(registry, mock_state_machine)

        mac_addr = "aa:bb:cc:dd:ee:02"

        # First connect a client
        await registry.register_client(
            mac_id=mac_addr,
            name="Living Room",
            ip="192.168.1.151"
        )
        await registry.set_client_online(mac_addr, True)

        # Clear broadcasts
        mock_state_machine.broadcasts.clear()

        # Simulate Snapcast Client.OnDisconnect notification
        snapcast_notification = {
            "client": {
                "id": "snapcast-client-xyz",
                "config": {"name": "Living Room"},
                "host": {
                    "name": "milo-client-living",
                    "ip": "192.168.1.151",
                    "mac": mac_addr
                }
            }
        }

        # Process the notification
        await ws_service._handle_client_disconnect(snapcast_notification)

        # Verify 1: Client is now offline
        client = registry.get_client(mac_addr)
        assert client is not None, "Client should still exist"
        assert client.online is False, "Client should be offline"

        # Verify 2: Disconnect events were broadcast
        assert len(mock_state_machine.broadcasts) >= 1, "Should have broadcast events"

        # Verify 3: Registry disconnect event (category="multiroom", type="client_state_changed")
        registry_events = [
            b for b in mock_state_machine.broadcasts
            if b["category"] == "multiroom" and b["type"] == "client_state_changed"
        ]
        assert len(registry_events) >= 1, "Should have registry disconnect event"

    @pytest.mark.asyncio
    async def test_new_client_auto_registration_flow(
        self, registry, mock_state_machine, ws_service
    ):
        """
        Test auto-registration of completely new client with default values.

        AC3: New unknown client is auto-registered with specified defaults.
        """
        attach_registry_broadcaster(registry, mock_state_machine)

        mac_addr = "aa:bb:cc:dd:ee:03"

        # Verify client doesn't exist
        assert registry.get_client(mac_addr) is None

        # Simulate new client connecting
        snapcast_notification = {
            "client": {
                "id": "brand-new-client",
                "config": {
                    "name": "Brand New Speaker",
                    "volume": {"percent": 100, "muted": False}
                },
                "host": {
                    "name": "milo-client-new",
                    "ip": "192.168.1.200",
                    "mac": mac_addr
                }
            }
        }

        await ws_service._handle_client_connect(snapcast_notification)

        # Verify client was created with correct defaults
        client = registry.get_client(mac_addr)
        assert client is not None

        # AC3 specified defaults
        assert client.name == "Brand New Speaker"
        assert client.speaker_type == DEFAULT_SPEAKER_TYPE  # 'bookshelf'
        assert client.volume_db == DEFAULT_VOLUME_DB  # -45.0
        assert client.online is True
        assert client.zone_id is None  # standalone

    # === Event Timing Tests (AC5 / NFR2) ===

    @pytest.mark.asyncio
    async def test_event_timing_under_100ms(
        self, registry, mock_state_machine, ws_service
    ):
        """
        Test that WebSocket events reach frontend within 100ms (NFR2).

        AC5: Event timing requirement.

        Note: This test verifies the processing time, not network latency.
        """
        attach_registry_broadcaster(registry, mock_state_machine)

        mac_addr = "aa:bb:cc:dd:ee:04"

        snapcast_notification = {
            "client": {
                "id": "timing-test-client",
                "config": {"name": "Timing Test", "volume": {"percent": 100}},
                "host": {"name": "milo-client-timing", "ip": "192.168.1.250", "mac": mac_addr}
            }
        }

        # Measure total processing time
        start_time = time.time()
        await ws_service._handle_client_connect(snapcast_notification)
        end_time = time.time()

        processing_time_ms = (end_time - start_time) * 1000

        # Verify processing completes within 100ms
        assert processing_time_ms < 100, (
            f"Event processing took {processing_time_ms:.2f}ms, "
            "should be under 100ms for NFR2 compliance"
        )

    @pytest.mark.asyncio
    async def test_disconnect_event_timing_under_100ms(
        self, registry, mock_state_machine, ws_service
    ):
        """
        Test disconnect event timing within 100ms.
        """
        attach_registry_broadcaster(registry, mock_state_machine)

        mac_addr = "aa:bb:cc:dd:ee:05"

        # Setup: register client first
        await registry.register_client(mac_addr, "Test", "192.168.1.251")
        await registry.set_client_online(mac_addr, True)

        snapcast_notification = {
            "client": {
                "id": "timing-disconnect-id",
                "config": {"name": "Test"},
                "host": {"name": "timing-disconnect", "ip": "192.168.1.251", "mac": mac_addr}
            }
        }

        # Measure disconnect processing time
        start_time = time.time()
        await ws_service._handle_client_disconnect(snapcast_notification)
        end_time = time.time()

        processing_time_ms = (end_time - start_time) * 1000

        assert processing_time_ms < 100, (
            f"Disconnect processing took {processing_time_ms:.2f}ms, "
            "should be under 100ms for NFR2 compliance"
        )

    # === Multiple Client Scenarios ===

    @pytest.mark.asyncio
    async def test_multiple_clients_connect_sequentially(
        self, registry, mock_state_machine, ws_service
    ):
        """Test multiple clients connecting in sequence."""
        attach_registry_broadcaster(registry, mock_state_machine)

        clients_data = [
            ("milo-client-1", "Speaker 1", "192.168.1.101", "aa:bb:cc:dd:01:01"),
            ("milo-client-2", "Speaker 2", "192.168.1.102", "aa:bb:cc:dd:01:02"),
            ("milo-client-3", "Speaker 3", "192.168.1.103", "aa:bb:cc:dd:01:03"),
        ]

        for hostname, name, ip, mac in clients_data:
            notification = {
                "client": {
                    "id": f"id-{hostname}",
                    "config": {"name": name, "volume": {"percent": 100}},
                    "host": {"name": hostname, "ip": ip, "mac": mac}
                }
            }
            await ws_service._handle_client_connect(notification)

        # Verify all clients registered (by mac_id)
        for hostname, name, _, mac in clients_data:
            client = registry.get_client(mac)
            assert client is not None, f"Client {hostname} should be registered"
            assert client.name == name
            assert client.online is True

        # Verify total client count
        all_clients = registry.get_all_clients()
        assert len(all_clients) == 3

    @pytest.mark.asyncio
    async def test_rapid_connect_disconnect_cycle(
        self, registry, mock_state_machine, ws_service
    ):
        """Test rapid connect/disconnect cycles don't cause issues."""
        attach_registry_broadcaster(registry, mock_state_machine)

        hostname = "milo-client-rapid"
        ip = "192.168.1.199"
        mac_addr = "aa:bb:cc:dd:ee:06"

        connect_notification = {
            "client": {
                "id": "rapid-id",
                "config": {"name": "Rapid Test", "volume": {"percent": 100}},
                "host": {"name": hostname, "ip": ip, "mac": mac_addr}
            }
        }

        disconnect_notification = {
            "client": {
                "id": "rapid-id",
                "config": {"name": "Rapid Test"},
                "host": {"name": hostname, "ip": ip, "mac": mac_addr}
            }
        }

        # Rapid connect/disconnect cycles
        for _ in range(5):
            await ws_service._handle_client_connect(connect_notification)
            client = registry.get_client(mac_addr)
            assert client.online is True

            await ws_service._handle_client_disconnect(disconnect_notification)
            client = registry.get_client(mac_addr)
            assert client.online is False

        # Final state check
        client = registry.get_client(mac_addr)
        assert client is not None
        assert client.online is False

    # === Local Client Tests ===

    @pytest.mark.asyncio
    async def test_local_client_detection(
        self, registry, mock_state_machine, ws_service
    ):
        """Test local client (127.0.0.1) is handled correctly."""
        attach_registry_broadcaster(registry, mock_state_machine)

        # Read the actual local MAC that compute_mac_id will return
        local_mac = ClientRegistryService.compute_mac_id("milo", "127.0.0.1", "")

        notification = {
            "client": {
                "id": "local-client-id",
                "config": {"name": "Local Speaker", "volume": {"percent": 100}},
                "host": {"name": "milo", "ip": "127.0.0.1"}
            }
        }

        await ws_service._handle_client_connect(notification)

        # Local client should get the system's actual MAC as mac_id
        client = registry.get_client(local_mac)
        assert client is not None
        assert client.mac_id == local_mac
        assert client.online is True

    # === Event Format Verification ===

    @pytest.mark.asyncio
    async def test_broadcast_event_format_compliance(
        self, registry, mock_state_machine, ws_service
    ):
        """
        Verify broadcast events match AC4 format specification.

        The registry broadcasts with category="multiroom" and maps
        CLIENT_CONNECTED/CLIENT_UPDATED to type="client_state_changed".

        Expected format:
        {
            "category": "multiroom",
            "type": "client_state_changed",
            "data": {
                "mac_id": "...",
                "client": { /* full client state */ }
            }
        }
        """
        attach_registry_broadcaster(registry, mock_state_machine)

        mac_addr = "aa:bb:cc:dd:ee:07"

        notification = {
            "client": {
                "id": "format-test",
                "config": {"name": "Format Test", "volume": {"percent": 100}},
                "host": {"name": "milo-client-format", "ip": "192.168.1.180", "mac": mac_addr}
            }
        }

        await ws_service._handle_client_connect(notification)

        # Find registry event (category="multiroom", type="client_state_changed")
        registry_events = [
            b for b in mock_state_machine.broadcasts
            if b["category"] == "multiroom" and b["type"] == "client_state_changed"
        ]

        assert len(registry_events) >= 1

        event = registry_events[-1]

        # Verify structure
        assert "category" in event
        assert "type" in event
        assert "data" in event

        assert event["category"] == "multiroom"
        assert event["type"] == "client_state_changed"

        data = event["data"]
        assert "mac_id" in data
        assert "client" in data

        # Verify client dict has expected fields
        client_dict = data["client"]
        assert "mac_id" in client_dict
        assert "name" in client_dict
        assert "speaker_type" in client_dict
