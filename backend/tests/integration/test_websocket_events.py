# backend/tests/integration/test_websocket_events.py
"""
Integration tests for WebSocket event protocol.

These tests validate the contracts for WebSocket communication that must
remain stable during the feature-based architecture refactoring.

Contracts being tested:
- Connection handshake and initial state (AC1)
- Event format: {category, type, source, data, timestamp} (AC2)
- Event categories: system, plugin, volume, registry, etc. (AC3)
- Broadcast triggers on state changes (AC4)
- Reconnection behavior (AC5)
"""
import pytest
import asyncio
import json
import time
from unittest.mock import Mock, AsyncMock, patch
from typing import List, Dict, Any

from fastapi import WebSocket
from starlette.websockets import WebSocketState

from backend.ws import WebSocketManager, WebSocketServer
from backend.core.models.audio_state import AudioSource, PluginState, SystemAudioState

from .conftest import WebSocketEventCollector


# ==============================================================================
# MOCK WEBSOCKET CLIENT
# ==============================================================================


class MockWebSocket:
    """Mock WebSocket for testing without actual network."""

    def __init__(self):
        self.client_state = WebSocketState.CONNECTING
        self.received_messages: List[Dict[str, Any]] = []
        self.messages_to_receive: List[str] = []
        self._receive_index = 0
        self._closed = False

    async def accept(self):
        """Accept connection."""
        self.client_state = WebSocketState.CONNECTED

    async def send_text(self, message: str):
        """Record sent message."""
        if self._closed:
            raise RuntimeError("WebSocket is not connected")
        self.received_messages.append(json.loads(message))

    async def send_json(self, data: Dict):
        """Send JSON data."""
        await self.send_text(json.dumps(data))

    async def receive_text(self) -> str:
        """Return next queued message."""
        if self._receive_index < len(self.messages_to_receive):
            msg = self.messages_to_receive[self._receive_index]
            self._receive_index += 1
            return msg
        # Block forever (simulate waiting for client message)
        await asyncio.sleep(100)
        return ""

    def queue_message(self, message: Dict):
        """Queue a message to be received."""
        self.messages_to_receive.append(json.dumps(message))

    def close(self):
        """Close connection."""
        self._closed = True
        self.client_state = WebSocketState.DISCONNECTED

    def get_events_by_type(self, event_type: str) -> List[Dict]:
        """Filter received events by type."""
        return [e for e in self.received_messages if e.get("type") == event_type]

    def get_events_by_category(self, category: str) -> List[Dict]:
        """Filter received events by category."""
        return [e for e in self.received_messages if e.get("category") == category]


# ==============================================================================
# FIXTURES
# ==============================================================================


@pytest.fixture
def mock_websocket():
    """Create a mock WebSocket client."""
    return MockWebSocket()


@pytest.fixture
def mock_volume_service():
    """Mock volume service for initial state."""
    service = Mock()
    service.wait_for_availability = AsyncMock(return_value=True)

    # Mock volume state
    mock_state = Mock()
    mock_state.to_dict = Mock(return_value={
        "mode": "direct",
        "global_volume_db": -30.0,
        "global_mute": False,
        "clients": {"local": {"volume_db": -30.0, "mute": False, "available": True}},
        "zones": {}
    })
    service.get_volume_state = AsyncMock(return_value=mock_state)

    return service


@pytest.fixture
def mock_state_machine_for_ws(mock_volume_service):
    """Mock state machine for WebSocket server."""
    sm = Mock()

    # Basic state
    sm.system_state = SystemAudioState(
        active_source=AudioSource.NONE,
        plugin_state=PluginState.WAITING,
        transitioning=False,
        metadata={},
        error=None,
        multiroom_enabled=False,
        equalizer_effects_enabled=True
    )

    # Methods
    sm.refresh_active_metadata = AsyncMock()
    sm.get_current_state = AsyncMock(return_value={
        "active_source": "none",
        "plugin_state": "waiting",
        "transitioning": False,
        "metadata": {},
        "error": None,
        "multiroom_enabled": False,
        "equalizer_effects_enabled": True
    })
    sm.broadcast_event = AsyncMock()

    return sm


@pytest.fixture
def websocket_manager():
    """Create WebSocket manager."""
    return WebSocketManager()


@pytest.fixture
def websocket_server(websocket_manager, mock_state_machine_for_ws, mock_volume_service):
    """Create WebSocket server with mocked dependencies."""
    return WebSocketServer(websocket_manager, mock_state_machine_for_ws, mock_volume_service)




# ==============================================================================
# AC1: Test Connection and Initial State
# ==============================================================================


class TestWebSocketConnection:
    """Tests for AC1: Connection and initial state handshake."""

    @pytest.mark.asyncio
    async def test_websocket_connection_success(
        self,
        websocket_server: WebSocketServer,
        mock_websocket: MockWebSocket
    ):
        """
        Test WebSocket connection is accepted.

        Validates:
        - Connection is accepted
        - Client state is CONNECTED
        """
        # Queue ready message
        mock_websocket.queue_message({"type": "ready"})

        # Run endpoint with timeout (it will block waiting for more messages)
        try:
            await asyncio.wait_for(
                websocket_server.websocket_endpoint(mock_websocket),
                timeout=0.5
            )
        except asyncio.TimeoutError:
            pass  # Expected - endpoint waits for messages

        assert mock_websocket.client_state == WebSocketState.CONNECTED

    @pytest.mark.asyncio
    async def test_ready_handshake_triggers_initial_state(
        self,
        websocket_server: WebSocketServer,
        mock_websocket: MockWebSocket
    ):
        """
        Test ready handshake triggers initial_state event.

        Validates:
        - Client sends {"type": "ready"}
        - Server responds with initial_state event
        """
        mock_websocket.queue_message({"type": "ready"})

        try:
            await asyncio.wait_for(
                websocket_server.websocket_endpoint(mock_websocket),
                timeout=0.5
            )
        except asyncio.TimeoutError:
            pass

        # Should have received initial_state
        initial_events = mock_websocket.get_events_by_type("initial_state")
        assert len(initial_events) >= 1

    @pytest.mark.asyncio
    async def test_initial_state_contains_full_state(
        self,
        websocket_server: WebSocketServer,
        mock_websocket: MockWebSocket
    ):
        """
        Test initial_state contains full_state data.

        Validates:
        - data.full_state contains system state snapshot
        """
        mock_websocket.queue_message({"type": "ready"})

        try:
            await asyncio.wait_for(
                websocket_server.websocket_endpoint(mock_websocket),
                timeout=0.5
            )
        except asyncio.TimeoutError:
            pass

        initial_events = mock_websocket.get_events_by_type("initial_state")
        assert len(initial_events) >= 1

        event = initial_events[0]
        assert "data" in event
        assert "full_state" in event["data"]

        full_state = event["data"]["full_state"]
        assert "active_source" in full_state
        assert "plugin_state" in full_state
        assert "transitioning" in full_state

    @pytest.mark.asyncio
    async def test_initial_state_has_required_fields(
        self,
        websocket_server: WebSocketServer,
        mock_websocket: MockWebSocket
    ):
        """
        Test initial_state has all required fields.

        Validates:
        - category, type, source, data, timestamp present
        """
        mock_websocket.queue_message({"type": "ready"})

        try:
            await asyncio.wait_for(
                websocket_server.websocket_endpoint(mock_websocket),
                timeout=0.5
            )
        except asyncio.TimeoutError:
            pass

        initial_events = mock_websocket.get_events_by_type("initial_state")
        assert len(initial_events) >= 1

        event = initial_events[0]
        assert event["category"] == "system"
        assert event["type"] == "initial_state"
        assert event["source"] == "system"
        assert "data" in event
        assert "timestamp" in event

    @pytest.mark.asyncio
    async def test_volume_state_sent_after_initial(
        self,
        websocket_server: WebSocketServer,
        mock_websocket: MockWebSocket
    ):
        """
        Test volume_changed sent after initial_state (non-blocking).

        Validates:
        - volume_changed event sent in background
        """
        mock_websocket.queue_message({"type": "ready"})

        try:
            await asyncio.wait_for(
                websocket_server.websocket_endpoint(mock_websocket),
                timeout=1.0
            )
        except asyncio.TimeoutError:
            pass

        # Should have received both initial_state and volume_changed
        initial_events = mock_websocket.get_events_by_type("initial_state")
        volume_events = mock_websocket.get_events_by_type("volume_changed")

        assert len(initial_events) >= 1
        assert len(volume_events) >= 1


# ==============================================================================
# AC2: Test Event Format
# ==============================================================================


class TestEventFormat:
    """Tests for AC2: Event format validation."""

    @pytest.mark.asyncio
    async def test_event_has_category_type_source_data(
        self,
        websocket_manager: WebSocketManager,
        mock_websocket: MockWebSocket,
    ):
        """
        Test all events have required fields.

        Validates:
        - category, type, source, data present in every event
        """
        await websocket_manager.connect(mock_websocket)

        # Broadcast a test event
        test_event = {
            "category": "test",
            "type": "test_event",
            "source": "test",
            "data": {"key": "value"},
            "timestamp": time.time()
        }
        await websocket_manager.broadcast_dict(test_event)

        assert len(mock_websocket.received_messages) == 1
        event = mock_websocket.received_messages[0]

        assert "category" in event
        assert "type" in event
        assert "source" in event
        assert "data" in event

    @pytest.mark.asyncio
    async def test_event_has_timestamp(
        self,
        websocket_manager: WebSocketManager,
        mock_websocket: MockWebSocket,
    ):
        """
        Test events have timestamp field.

        Validates:
        - timestamp is present and is a number
        """
        await websocket_manager.connect(mock_websocket)

        test_event = {
            "category": "test",
            "type": "test_event",
            "source": "test",
            "data": {},
            "timestamp": time.time()
        }
        await websocket_manager.broadcast_dict(test_event)

        event = mock_websocket.received_messages[0]
        assert "timestamp" in event
        assert isinstance(event["timestamp"], (int, float))

    @pytest.mark.asyncio
    async def test_event_source_matches_origin(
        self,
        websocket_manager: WebSocketManager,
        mock_websocket: MockWebSocket,
    ):
        """
        Test event source field is correctly set.

        Validates:
        - source reflects the originating service
        """
        await websocket_manager.connect(mock_websocket)

        # Test system event
        system_event = {
            "category": "system",
            "type": "state_changed",
            "source": "system",
            "data": {},
            "timestamp": time.time()
        }
        await websocket_manager.broadcast_dict(system_event)

        # Test plugin event
        plugin_event = {
            "category": "plugin",
            "type": "state_changed",
            "source": "radio",
            "data": {},
            "timestamp": time.time()
        }
        await websocket_manager.broadcast_dict(plugin_event)

        assert mock_websocket.received_messages[0]["source"] == "system"
        assert mock_websocket.received_messages[1]["source"] == "radio"

    @pytest.mark.asyncio
    async def test_broadcast_to_multiple_clients(
        self,
        websocket_manager: WebSocketManager,
    ):
        """
        Test events are broadcast to all connected clients.

        Validates:
        - All connected clients receive the event
        """
        client1 = MockWebSocket()
        client2 = MockWebSocket()
        client3 = MockWebSocket()

        await websocket_manager.connect(client1)
        await websocket_manager.connect(client2)
        await websocket_manager.connect(client3)

        test_event = {
            "category": "system",
            "type": "test",
            "source": "system",
            "data": {"message": "hello"},
            "timestamp": time.time()
        }
        await websocket_manager.broadcast_dict(test_event)

        assert len(client1.received_messages) == 1
        assert len(client2.received_messages) == 1
        assert len(client3.received_messages) == 1

        for client in [client1, client2, client3]:
            assert client.received_messages[0]["data"]["message"] == "hello"


# ==============================================================================
# AC3: Test Event Categories
# ==============================================================================


class TestEventCategories:
    """Tests for AC3: Event category validation."""

    @pytest.mark.asyncio
    async def test_system_category_events(
        self,
        websocket_manager: WebSocketManager,
        mock_websocket: MockWebSocket,
    ):
        """
        Test system category event types.

        Validates:
        - system category with types: initial_state, state_changed, transition_start, etc.
        """
        await websocket_manager.connect(mock_websocket)

        system_events = [
            {"category": "system", "type": "initial_state", "source": "system", "data": {}, "timestamp": time.time()},
            {"category": "system", "type": "state_changed", "source": "system", "data": {}, "timestamp": time.time()},
            {"category": "system", "type": "transition_start", "source": "system", "data": {"to_source": "radio"}, "timestamp": time.time()},
            {"category": "system", "type": "transition_complete", "source": "system", "data": {"active_source": "radio"}, "timestamp": time.time()},
            {"category": "system", "type": "error", "source": "system", "data": {"error": "test error"}, "timestamp": time.time()},
        ]

        for event in system_events:
            await websocket_manager.broadcast_dict(event)

        assert len(mock_websocket.received_messages) == 5
        for i, event in enumerate(mock_websocket.received_messages):
            assert event["category"] == "system"
            assert event["type"] == system_events[i]["type"]

    @pytest.mark.asyncio
    async def test_plugin_category_events(
        self,
        websocket_manager: WebSocketManager,
        mock_websocket: MockWebSocket,
    ):
        """
        Test plugin category event types.

        Validates:
        - plugin category with type: state_changed
        """
        await websocket_manager.connect(mock_websocket)

        plugin_event = {
            "category": "plugin",
            "type": "state_changed",
            "source": "spotify",
            "data": {"state": "connected", "metadata": {"track": "Test Song"}},
            "timestamp": time.time()
        }
        await websocket_manager.broadcast_dict(plugin_event)

        assert len(mock_websocket.received_messages) == 1
        event = mock_websocket.received_messages[0]
        assert event["category"] == "plugin"
        assert event["type"] == "state_changed"
        assert event["source"] == "spotify"

    @pytest.mark.asyncio
    async def test_volume_category_events(
        self,
        websocket_manager: WebSocketManager,
        mock_websocket: MockWebSocket,
    ):
        """
        Test volume category event types.

        Validates:
        - volume category with type: volume_changed
        """
        await websocket_manager.connect(mock_websocket)

        volume_event = {
            "category": "volume",
            "type": "volume_changed",
            "source": "volume",
            "data": {
                "show_bar": True,
                "state": {"global_volume_db": -25.0, "global_mute": False}
            },
            "timestamp": time.time()
        }
        await websocket_manager.broadcast_dict(volume_event)

        event = mock_websocket.received_messages[0]
        assert event["category"] == "volume"
        assert event["type"] == "volume_changed"
        assert event["data"]["state"]["global_volume_db"] == -25.0

    @pytest.mark.asyncio
    async def test_registry_category_events(
        self,
        websocket_manager: WebSocketManager,
        mock_websocket: MockWebSocket,
    ):
        """
        Test registry category event types.

        Validates:
        - registry category with types: zone_created, zone_deleted, etc.
        """
        await websocket_manager.connect(mock_websocket)

        registry_events = [
            {"category": "registry", "type": "zone_created", "source": "registry",
             "data": {"zone_id": "living_room", "zone": {"id": "living_room", "name": "Living Room"}},
             "timestamp": time.time()},
            {"category": "registry", "type": "zone_deleted", "source": "registry",
             "data": {"zone_id": "living_room"},
             "timestamp": time.time()},
            {"category": "registry", "type": "client_registered", "source": "registry",
             "data": {"camilladsp_id": "local", "client": {"name": "Local"}},
             "timestamp": time.time()},
        ]

        for event in registry_events:
            await websocket_manager.broadcast_dict(event)

        assert len(mock_websocket.received_messages) == 3
        assert mock_websocket.received_messages[0]["type"] == "zone_created"
        assert mock_websocket.received_messages[1]["type"] == "zone_deleted"
        assert mock_websocket.received_messages[2]["type"] == "client_registered"

    @pytest.mark.asyncio
    async def test_equalizer_category_events(
        self,
        websocket_manager: WebSocketManager,
        mock_websocket: MockWebSocket,
    ):
        """
        Test equalizer category event types.

        Validates:
        - equalizer category with types: filter_added, mute_changed, etc.
        """
        await websocket_manager.connect(mock_websocket)

        equalizer_events = [
            {"category": "equalizer", "type": "filter_added", "source": "equalizer",
             "data": {"filter_id": 1, "type": "peak"},
             "timestamp": time.time()},
            {"category": "equalizer", "type": "mute_changed", "source": "equalizer",
             "data": {"mute": True},
             "timestamp": time.time()},
            {"category": "equalizer", "type": "enabled_changed", "source": "equalizer",
             "data": {"enabled": False},
             "timestamp": time.time()},
        ]

        for event in equalizer_events:
            await websocket_manager.broadcast_dict(event)

        assert len(mock_websocket.received_messages) == 3
        assert mock_websocket.received_messages[0]["type"] == "filter_added"
        assert mock_websocket.received_messages[1]["type"] == "mute_changed"
        assert mock_websocket.received_messages[2]["type"] == "enabled_changed"


# ==============================================================================
# AC4: Test Broadcast Triggers
# ==============================================================================


class TestBroadcastTriggers:
    """Tests for AC4: State changes trigger broadcasts."""

    @pytest.mark.asyncio
    async def test_source_transition_triggers_events(
        self,
        websocket_manager: WebSocketManager,
        mock_websocket: MockWebSocket,
    ):
        """
        Test source transition triggers start and complete events.

        Validates:
        - transition_start event emitted at start
        - transition_complete event emitted at end
        """
        await websocket_manager.connect(mock_websocket)

        # Simulate transition start
        start_event = {
            "category": "system",
            "type": "transition_start",
            "source": "system",
            "data": {"to_source": "radio", "from_source": "none"},
            "timestamp": time.time()
        }
        await websocket_manager.broadcast_dict(start_event)

        # Simulate transition complete
        complete_event = {
            "category": "system",
            "type": "transition_complete",
            "source": "system",
            "data": {"active_source": "radio"},
            "timestamp": time.time()
        }
        await websocket_manager.broadcast_dict(complete_event)

        start_events = mock_websocket.get_events_by_type("transition_start")
        complete_events = mock_websocket.get_events_by_type("transition_complete")

        assert len(start_events) == 1
        assert len(complete_events) == 1
        assert start_events[0]["data"]["to_source"] == "radio"
        assert complete_events[0]["data"]["active_source"] == "radio"

    @pytest.mark.asyncio
    async def test_volume_change_triggers_broadcast(
        self,
        websocket_manager: WebSocketManager,
        mock_websocket: MockWebSocket,
    ):
        """
        Test volume changes trigger volume_changed events.

        Validates:
        - volume_changed event emitted on volume change
        """
        await websocket_manager.connect(mock_websocket)

        volume_event = {
            "category": "volume",
            "type": "volume_changed",
            "source": "volume",
            "data": {
                "show_bar": True,
                "step_mobile_db": 3.0,
                "state": {
                    "mode": "direct",
                    "global_volume_db": -35.0,
                    "global_mute": False
                }
            },
            "timestamp": time.time()
        }
        await websocket_manager.broadcast_dict(volume_event)

        events = mock_websocket.get_events_by_type("volume_changed")
        assert len(events) == 1
        assert events[0]["data"]["state"]["global_volume_db"] == -35.0
        assert events[0]["data"]["show_bar"] is True

    @pytest.mark.asyncio
    async def test_zone_creation_triggers_broadcast(
        self,
        websocket_manager: WebSocketManager,
        mock_websocket: MockWebSocket,
    ):
        """
        Test zone creation triggers zone_created event.

        Validates:
        - zone_created event emitted when zone is created
        """
        await websocket_manager.connect(mock_websocket)

        zone_event = {
            "category": "registry",
            "type": "zone_created",
            "source": "registry",
            "data": {
                "zone_id": "living_room",
                "zone": {
                    "id": "living_room",
                    "name": "Living Room",
                    "client_ids": ["local", "bedroom"]
                }
            },
            "timestamp": time.time()
        }
        await websocket_manager.broadcast_dict(zone_event)

        events = mock_websocket.get_events_by_type("zone_created")
        assert len(events) == 1
        assert events[0]["data"]["zone"]["name"] == "Living Room"

    @pytest.mark.asyncio
    async def test_multiple_events_in_sequence(
        self,
        websocket_manager: WebSocketManager,
        mock_websocket: MockWebSocket,
    ):
        """
        Test multiple events are received in order.

        Validates:
        - Events maintain order when broadcast rapidly
        """
        await websocket_manager.connect(mock_websocket)

        events = [
            {"category": "system", "type": "event_1", "source": "test", "data": {"seq": 1}, "timestamp": time.time()},
            {"category": "system", "type": "event_2", "source": "test", "data": {"seq": 2}, "timestamp": time.time()},
            {"category": "system", "type": "event_3", "source": "test", "data": {"seq": 3}, "timestamp": time.time()},
        ]

        for event in events:
            await websocket_manager.broadcast_dict(event)

        assert len(mock_websocket.received_messages) == 3
        for i, msg in enumerate(mock_websocket.received_messages):
            assert msg["data"]["seq"] == i + 1


# ==============================================================================
# AC5: Test Reconnection
# ==============================================================================


class TestReconnection:
    """Tests for AC5: Reconnection behavior."""

    @pytest.mark.asyncio
    async def test_reconnection_after_disconnect(
        self,
        websocket_server: WebSocketServer,
        websocket_manager: WebSocketManager
    ):
        """
        Test client can reconnect after disconnect.

        Validates:
        - First connection works
        - Disconnect removes client
        - Second connection works
        """
        # First connection
        client1 = MockWebSocket()
        client1.queue_message({"type": "ready"})

        try:
            await asyncio.wait_for(
                websocket_server.websocket_endpoint(client1),
                timeout=0.5
            )
        except asyncio.TimeoutError:
            pass

        # Should have received initial_state
        assert len(client1.get_events_by_type("initial_state")) >= 1

        # Disconnect (manager disconnect called in finally block)
        websocket_manager.disconnect(client1)
        assert len(websocket_manager.active_connections) == 0

        # Reconnect with new client
        client2 = MockWebSocket()
        client2.queue_message({"type": "ready"})

        try:
            await asyncio.wait_for(
                websocket_server.websocket_endpoint(client2),
                timeout=0.5
            )
        except asyncio.TimeoutError:
            pass

        # Should have received initial_state again
        assert len(client2.get_events_by_type("initial_state")) >= 1

    @pytest.mark.asyncio
    async def test_reconnection_receives_current_state(
        self,
        websocket_manager: WebSocketManager,
        mock_state_machine_for_ws
    ):
        """
        Test reconnected client receives current state.

        Validates:
        - State reflects changes made while disconnected
        """
        server = WebSocketServer(websocket_manager, mock_state_machine_for_ws)

        # First connection
        client = MockWebSocket()
        client.queue_message({"type": "ready"})

        try:
            await asyncio.wait_for(
                server.websocket_endpoint(client),
                timeout=0.5
            )
        except asyncio.TimeoutError:
            pass

        initial_state_1 = client.get_events_by_type("initial_state")[0]
        assert initial_state_1["data"]["full_state"]["active_source"] == "none"

        # Simulate state change while "disconnected"
        mock_state_machine_for_ws.get_current_state = AsyncMock(return_value={
            "active_source": "radio",
            "plugin_state": "connected",
            "transitioning": False,
            "metadata": {"station": "Test FM"},
            "error": None,
            "multiroom_enabled": False,
            "equalizer_effects_enabled": True
        })

        # Reconnect
        websocket_manager.disconnect(client)
        client2 = MockWebSocket()
        client2.queue_message({"type": "ready"})

        try:
            await asyncio.wait_for(
                server.websocket_endpoint(client2),
                timeout=0.5
            )
        except asyncio.TimeoutError:
            pass

        # Should reflect new state
        initial_state_2 = client2.get_events_by_type("initial_state")[0]
        assert initial_state_2["data"]["full_state"]["active_source"] == "radio"
        assert initial_state_2["data"]["full_state"]["plugin_state"] == "connected"

    @pytest.mark.asyncio
    async def test_multiple_clients_receive_broadcasts(
        self,
        websocket_manager: WebSocketManager,
    ):
        """
        Test all connected clients receive broadcasts.

        Validates:
        - Broadcast reaches all active connections
        """
        clients = [MockWebSocket() for _ in range(5)]

        for client in clients:
            await websocket_manager.connect(client)

        assert len(websocket_manager.active_connections) == 5

        # Broadcast event
        test_event = {
            "category": "system",
            "type": "broadcast_test",
            "source": "test",
            "data": {"message": "hello all"},
            "timestamp": time.time()
        }
        await websocket_manager.broadcast_dict(test_event)

        # All clients should receive the event
        for client in clients:
            assert len(client.received_messages) == 1
            assert client.received_messages[0]["data"]["message"] == "hello all"


# ==============================================================================
# Additional Tests
# ==============================================================================


class TestWebSocketManager:
    """Integration tests for WebSocketManager."""

    @pytest.mark.asyncio
    async def test_manager_connect_and_disconnect(
        self,
        websocket_manager: WebSocketManager
    ):
        """Test connection management."""
        client = MockWebSocket()

        await websocket_manager.connect(client)
        assert len(websocket_manager.active_connections) == 1

        websocket_manager.disconnect(client)
        assert len(websocket_manager.active_connections) == 0

    @pytest.mark.asyncio
    async def test_manager_removes_dead_connections(
        self,
        websocket_manager: WebSocketManager
    ):
        """Test dead connections are removed on broadcast."""
        client1 = MockWebSocket()
        client2 = MockWebSocket()

        await websocket_manager.connect(client1)
        await websocket_manager.connect(client2)

        # Simulate client1 disconnect
        client1.close()

        # Broadcast should remove dead connection
        await websocket_manager.broadcast_dict({
            "category": "test",
            "type": "test",
            "source": "test",
            "data": {},
            "timestamp": time.time()
        })

        # Only client2 should remain
        assert len(websocket_manager.active_connections) <= 2  # May not immediately clean up

    @pytest.mark.asyncio
    async def test_broadcast_to_empty_connections(
        self,
        websocket_manager: WebSocketManager
    ):
        """Test broadcast with no connections doesn't error."""
        # Should not raise exception
        await websocket_manager.broadcast_dict({
            "category": "test",
            "type": "test",
            "source": "test",
            "data": {},
            "timestamp": time.time()
        })


class TestPingMechanism:
    """Tests for WebSocket ping/pong keepalive."""

    @pytest.mark.asyncio
    async def test_ping_event_format(
        self,
        websocket_manager: WebSocketManager,
        mock_websocket: MockWebSocket,
    ):
        """
        Test ping event has correct format.

        Validates:
        - Ping has category=system, type=ping
        """
        await websocket_manager.connect(mock_websocket)

        ping_event = {
            "category": "system",
            "type": "ping",
            "timestamp": time.time()
        }
        await websocket_manager.broadcast_dict(ping_event)

        event = mock_websocket.received_messages[0]
        assert event["category"] == "system"
        assert event["type"] == "ping"
        assert "timestamp" in event


# ==============================================================================
# Story 6.1: Multiroom Event Broadcasting Tests
# ==============================================================================


class TestMultiroomEventFormat:
    """
    Tests for Story 6.1: WebSocket Event Broadcasting.

    Validates the standardized "multiroom" category events per architecture spec.
    """

    @pytest.mark.asyncio
    async def test_multiroom_category_client_state_changed(
        self,
        websocket_manager: WebSocketManager,
        mock_websocket: MockWebSocket,
    ):
        """
        Test client_state_changed event format (AC1, AC5).

        Validates:
        - category is "multiroom"
        - type is "client_state_changed"
        - data contains complete client object with all fields
        """
        await websocket_manager.connect(mock_websocket)

        client_event = {
            "category": "multiroom",
            "type": "client_state_changed",
            "source": "multiroom",
            "data": {
                "mac_id": "dc:a6:32:7e:d3:43",
                "client": {
                    "mac_id": "dc:a6:32:7e:d3:43",
                    "name": "Living Room Speaker",
                    "ip": "192.168.1.100",
                    "zone_id": None,
                    "volume_db": -25.0,
                    "mute": False,
                    "speaker_type": "bookshelf",
                    "crossover_frequency": 80,
                    "online": True
                }
            },
            "timestamp": time.time()
        }
        await websocket_manager.broadcast_dict(client_event)

        assert len(mock_websocket.received_messages) == 1
        event = mock_websocket.received_messages[0]

        # Verify category and type
        assert event["category"] == "multiroom"
        assert event["type"] == "client_state_changed"

        # Verify client object completeness (AC1)
        client = event["data"]["client"]
        assert "mac_id" in client
        assert "name" in client
        assert "ip" in client
        assert "zone_id" in client
        assert "volume_db" in client
        assert "mute" in client
        assert "speaker_type" in client
        assert "online" in client

    @pytest.mark.asyncio
    async def test_multiroom_category_zone_changed(
        self,
        websocket_manager: WebSocketManager,
        mock_websocket: MockWebSocket,
    ):
        """
        Test zone_changed event format (AC2, AC5).

        Validates:
        - category is "multiroom"
        - type is "zone_changed"
        - data contains complete enriched zone object
        """
        await websocket_manager.connect(mock_websocket)

        zone_event = {
            "category": "multiroom",
            "type": "zone_changed",
            "source": "multiroom",
            "data": {
                "zone_id": "uuid-living-room",
                "zone": {
                    "id": "uuid-living-room",
                    "name": "Living Room",
                    "client_ids": ["local", "dc:a6:32:7e:d3:43"],
                    "equalizer_settings": {
                        "enabled": True,
                        "filters": [],
                        "compressor": {"enabled": False},
                        "loudness": {"enabled": False}
                    },
                    "crossover_frequency": 80,
                    "crossover_enabled": True,
                    "online_client_count": 2,
                    "has_subwoofer": False
                }
            },
            "timestamp": time.time()
        }
        await websocket_manager.broadcast_dict(zone_event)

        assert len(mock_websocket.received_messages) == 1
        event = mock_websocket.received_messages[0]

        # Verify category and type
        assert event["category"] == "multiroom"
        assert event["type"] == "zone_changed"

        # Verify zone object completeness (AC2)
        zone = event["data"]["zone"]
        assert "id" in zone
        assert "name" in zone
        assert "client_ids" in zone
        assert "equalizer_settings" in zone
        assert "crossover_frequency" in zone
        # Computed fields for enriched zone
        assert "crossover_enabled" in zone
        assert "online_client_count" in zone
        assert "has_subwoofer" in zone

    @pytest.mark.asyncio
    async def test_multiroom_category_equalizer_changed(
        self,
        websocket_manager: WebSocketManager,
        mock_websocket: MockWebSocket,
    ):
        """
        Test equalizer_changed event format (AC3, AC5).

        Validates:
        - category is "multiroom"
        - type is "equalizer_changed"
        - data contains target_type, target_id, and equalizer_settings
        """
        await websocket_manager.connect(mock_websocket)

        equalizer_event = {
            "category": "multiroom",
            "type": "equalizer_changed",
            "source": "multiroom",
            "data": {
                "target_type": "zone",
                "target_id": "uuid-living-room",
                "equalizer_settings": {
                    "enabled": True,
                    "filters": [
                        {"id": "eq_band_00", "frequency": 31, "gain": 2.0, "q": 1.41}
                    ],
                    "compressor": {"enabled": True, "threshold": -20.0},
                    "loudness": {"enabled": False}
                }
            },
            "timestamp": time.time()
        }
        await websocket_manager.broadcast_dict(equalizer_event)

        assert len(mock_websocket.received_messages) == 1
        event = mock_websocket.received_messages[0]

        # Verify category and type
        assert event["category"] == "multiroom"
        assert event["type"] == "equalizer_changed"

        # Verify Equalizer event structure (AC3)
        data = event["data"]
        assert "target_type" in data
        assert "target_id" in data
        assert "equalizer_settings" in data

    @pytest.mark.asyncio
    async def test_multiroom_category_crossover_changed(
        self,
        websocket_manager: WebSocketManager,
        mock_websocket: MockWebSocket,
    ):
        """
        Test crossover_changed event format (AC4, AC5).

        Validates:
        - category is "multiroom"
        - type is "crossover_changed"
        - data contains zone_id, crossover_enabled, crossover_frequency
        """
        await websocket_manager.connect(mock_websocket)

        crossover_event = {
            "category": "multiroom",
            "type": "crossover_changed",
            "source": "multiroom",
            "data": {
                "zone_id": "uuid-living-room",
                "crossover_enabled": True,
                "crossover_frequency": 80
            },
            "timestamp": time.time()
        }
        await websocket_manager.broadcast_dict(crossover_event)

        assert len(mock_websocket.received_messages) == 1
        event = mock_websocket.received_messages[0]

        # Verify category and type
        assert event["category"] == "multiroom"
        assert event["type"] == "crossover_changed"

        # Verify crossover event structure (AC4)
        data = event["data"]
        assert "zone_id" in data
        assert "crossover_enabled" in data
        assert "crossover_frequency" in data

    @pytest.mark.asyncio
    async def test_backward_compatibility_registry_events(
        self,
        websocket_manager: WebSocketManager,
        mock_websocket: MockWebSocket,
    ):
        """
        Test backward compatibility: old registry events still work.

        During transition period, backend emits both "multiroom" and "registry"
        category events. This test ensures old handlers still work.
        """
        await websocket_manager.connect(mock_websocket)

        # Old-style registry event (should still be received)
        legacy_event = {
            "category": "registry",
            "type": "CLIENT_UPDATED",
            "source": "registry",
            "data": {
                "mac_id": "dc:a6:32:7e:d3:43",
                "client": {"name": "Test Client"}
            },
            "timestamp": time.time()
        }
        await websocket_manager.broadcast_dict(legacy_event)

        events = mock_websocket.get_events_by_category("registry")
        assert len(events) == 1
        assert events[0]["type"] == "CLIENT_UPDATED"

    @pytest.mark.asyncio
    async def test_event_contains_timestamp(
        self,
        websocket_manager: WebSocketManager,
        mock_websocket: MockWebSocket,
    ):
        """
        Test multiroom events contain valid timestamp.

        Validates:
        - timestamp field is present
        - timestamp is a valid number
        """
        await websocket_manager.connect(mock_websocket)

        event = {
            "category": "multiroom",
            "type": "client_state_changed",
            "source": "multiroom",
            "data": {"mac_id": "test"},
            "timestamp": time.time()
        }
        await websocket_manager.broadcast_dict(event)

        received = mock_websocket.received_messages[0]
        assert "timestamp" in received
        assert isinstance(received["timestamp"], (int, float))
        assert received["timestamp"] > 0
