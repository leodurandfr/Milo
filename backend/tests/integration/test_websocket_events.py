# backend/tests/integration/test_websocket_events.py
"""
Integration tests for WebSocket event protocol.

These tests validate the contracts for WebSocket communication that must
remain stable during the feature-based architecture refactoring.

Contracts being tested:
- Connection handshake and initial state
- Event format: {category, type, origin, data, timestamp}
- Event categories: system, source, volume, registry, etc.
- Broadcast triggers on state changes
- Reconnection behavior
"""
import pytest
import asyncio
import json
import time
from unittest.mock import Mock, AsyncMock
from typing import List, Dict, Any

from fastapi.websockets import WebSocketState

from backend.ws import WebSocketManager, WebSocketServer
from backend.core.models.audio_state import AudioSource, SourceState, SystemAudioState



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
    # Handshake payload includes the mobile step (must be JSON-serializable)
    service.volume_config.step_mobile_db = 3.0

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
        source_state=SourceState.WAITING,
        transitioning=False,
        metadata={},
        error=None,
    )

    # Methods
    sm.refresh_active_metadata = AsyncMock()
    sm.get_current_state = Mock(return_value={
        "active_source": "none",
        "source_state": "waiting",
        "transitioning": False,
        "metadata": {},
        "error": None,
    })
    sm.broadcast = AsyncMock()

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
# Test Connection and Initial State
# ==============================================================================


class TestWebSocketConnection:
    """Tests for Connection and initial state handshake."""

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
        assert "source_state" in full_state
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
        - category, type, origin, data, timestamp present
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
        assert event["origin"] == "system"
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

        # Handshake must carry step_mobile_db so the mobile +/- step is correct
        # from the first frame (else the frontend keeps its stale default).
        assert volume_events[0]["data"]["step_mobile_db"] == 3.0


# ==============================================================================
# Test Event Format
# ==============================================================================


class TestEventFormat:
    """Tests for Event format validation."""

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
            "origin": "system",
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
# Test Event Categories
# ==============================================================================


# ==============================================================================
# Test Broadcast Triggers
# ==============================================================================


class TestBroadcastTriggers:
    """Tests for State changes trigger broadcasts."""

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
            {"category": "system", "type": "event_1", "origin": "test", "data": {"seq": 1}, "timestamp": time.time()},
            {"category": "system", "type": "event_2", "origin": "test", "data": {"seq": 2}, "timestamp": time.time()},
            {"category": "system", "type": "event_3", "origin": "test", "data": {"seq": 3}, "timestamp": time.time()},
        ]

        for event in events:
            await websocket_manager.broadcast_dict(event)

        assert len(mock_websocket.received_messages) == 3
        for i, msg in enumerate(mock_websocket.received_messages):
            assert msg["data"]["seq"] == i + 1


# ==============================================================================
# Test Reconnection
# ==============================================================================


class TestReconnection:
    """Tests for Reconnection behavior."""

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
        mock_state_machine_for_ws.get_current_state = Mock(return_value={
            "active_source": "radio",
            "source_state": "connected",
            "transitioning": False,
            "metadata": {"station": "Test FM"},
            "error": None,
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
        assert initial_state_2["data"]["full_state"]["source_state"] == "connected"

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
            "origin": "test",
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
        """A client that died since it connected is dropped by the next broadcast.

        Complements test_websocket_server.py, which injects the dead socket into
        the set directly: here both clients arrive through the real connect()
        path, so a registration that kept its own reference would survive there
        and be caught here. Reaping is synchronous inside broadcast_dict — the
        set is exact after it returns, not eventually.
        """
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
            "origin": "test",
            "data": {},
            "timestamp": time.time()
        })

        assert websocket_manager.active_connections == {client2}

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
            "origin": "test",
            "data": {},
            "timestamp": time.time()
        })


# ==============================================================================
# Multiroom Event Broadcasting Tests
# ==============================================================================


