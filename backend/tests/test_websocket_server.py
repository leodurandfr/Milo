# backend/tests/test_websocket_server.py
"""
Unit tests for the WebSocket server and manager
"""
import pytest
import asyncio
import json
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from backend.presentation.websockets.server import WebSocketServer
from backend.presentation.websockets.manager import WebSocketManager


class TestWebSocketManager:
    """Tests for the WebSocket manager"""

    @pytest.fixture
    def manager(self):
        """Fixture to create a WebSocket manager"""
        return WebSocketManager()

    @pytest.fixture
    def mock_websocket(self):
        """WebSocket mock"""
        ws = AsyncMock()
        ws.accept = AsyncMock()
        ws.send_text = AsyncMock()
        ws.receive_text = AsyncMock(return_value='{"type": "ready"}')
        return ws

    # ===================
    # CONNECTION TESTS
    # ===================

    @pytest.mark.asyncio
    async def test_connect(self, manager, mock_websocket):
        """Test WebSocket connection"""
        await manager.connect(mock_websocket)

        assert mock_websocket in manager.active_connections
        assert len(manager.active_connections) == 1
        mock_websocket.accept.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_multiple(self, manager):
        """Test multiple connections"""
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        ws3 = AsyncMock()

        await manager.connect(ws1)
        await manager.connect(ws2)
        await manager.connect(ws3)

        assert len(manager.active_connections) == 3

    def test_disconnect(self, manager, mock_websocket):
        """Test disconnection"""
        manager.active_connections.add(mock_websocket)

        manager.disconnect(mock_websocket)

        assert mock_websocket not in manager.active_connections
        assert len(manager.active_connections) == 0

    def test_disconnect_not_connected(self, manager, mock_websocket):
        """Test disconnection of a non-connected client"""
        # Should not raise an exception
        manager.disconnect(mock_websocket)

        assert len(manager.active_connections) == 0

    # ===================
    # BROADCAST TESTS
    # ===================

    @pytest.mark.asyncio
    async def test_broadcast_dict(self, manager, mock_websocket):
        """Test broadcast to one client"""
        manager.active_connections.add(mock_websocket)
        event = {
            "category": "system",
            "type": "test",
            "data": {"message": "hello"}
        }

        await manager.broadcast_dict(event)

        mock_websocket.send_text.assert_called_once()
        sent_data = json.loads(mock_websocket.send_text.call_args[0][0])
        assert sent_data["category"] == "system"
        assert sent_data["type"] == "test"

    @pytest.mark.asyncio
    async def test_broadcast_dict_multiple_clients(self, manager):
        """Test broadcast to multiple clients"""
        ws1 = AsyncMock()
        ws2 = AsyncMock()
        ws3 = AsyncMock()

        manager.active_connections.add(ws1)
        manager.active_connections.add(ws2)
        manager.active_connections.add(ws3)

        event = {"category": "test", "type": "broadcast"}

        await manager.broadcast_dict(event)

        ws1.send_text.assert_called_once()
        ws2.send_text.assert_called_once()
        ws3.send_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_broadcast_dict_no_connections(self, manager):
        """Test broadcast with no connections"""
        event = {"category": "test", "type": "broadcast"}

        # Should not raise an exception
        await manager.broadcast_dict(event)

    @pytest.mark.asyncio
    async def test_broadcast_dict_removes_dead_connections(self, manager):
        """Test that dead connections are removed"""
        good_ws = AsyncMock()
        bad_ws = AsyncMock()
        bad_ws.send_text = AsyncMock(side_effect=Exception("Connection lost"))

        manager.active_connections.add(good_ws)
        manager.active_connections.add(bad_ws)

        event = {"category": "test", "type": "broadcast"}

        await manager.broadcast_dict(event)

        # The good websocket should receive the message
        good_ws.send_text.assert_called_once()
        # The bad websocket should be removed
        assert bad_ws not in manager.active_connections
        assert len(manager.active_connections) == 1


class TestWebSocketServer:
    """Tests for the WebSocket server"""

    @pytest.fixture
    def mock_manager(self):
        """WebSocket manager mock"""
        manager = Mock(spec=WebSocketManager)
        manager.connect = AsyncMock()
        manager.disconnect = Mock()
        manager.broadcast_dict = AsyncMock()
        return manager

    @pytest.fixture
    def mock_state_machine(self):
        """State machine mock"""
        sm = Mock()
        sm.get_current_state = AsyncMock(return_value={
            "active_source": "none",
            "plugin_state": "inactive",
            "metadata": {},
            "multiroom": {"enabled": False},
            "equalizer": {"enabled": False}
        })
        return sm

    @pytest.fixture
    def server(self, mock_manager, mock_state_machine):
        """Fixture to create a WebSocket server"""
        return WebSocketServer(mock_manager, mock_state_machine)

    @pytest.fixture
    def mock_websocket(self):
        """WebSocket mock"""
        ws = AsyncMock()
        ws.accept = AsyncMock()
        ws.send_text = AsyncMock()
        # First message: ready, then blocks on the next
        ws.receive_text = AsyncMock(side_effect=['{"type": "ready"}', asyncio.CancelledError()])
        return ws

    # ===================
    # ENDPOINT TESTS
    # ===================

    @pytest.mark.asyncio
    async def test_websocket_endpoint_connect_and_ready(self, server, mock_websocket):
        """Test connection and ready handshake"""
        # Simulate the flow: ready then disconnection
        mock_websocket.receive_text = AsyncMock(side_effect=[
            '{"type": "ready"}',
            asyncio.CancelledError()
        ])

        try:
            await server.websocket_endpoint(mock_websocket)
        except asyncio.CancelledError:
            pass

        # Verify that the manager was called to connect
        server.manager.connect.assert_called_once_with(mock_websocket)

        # Verify that the initial state was sent
        assert mock_websocket.send_text.called
        sent_data = json.loads(mock_websocket.send_text.call_args_list[0][0][0])
        assert sent_data["category"] == "system"
        assert sent_data["type"] == "initial_state"
        assert "full_state" in sent_data["data"]

    @pytest.mark.asyncio
    async def test_websocket_endpoint_disconnect(self, server, mock_websocket):
        """Test clean disconnection"""
        from fastapi import WebSocketDisconnect

        # Simulate ready then disconnect
        mock_websocket.receive_text = AsyncMock(side_effect=[
            '{"type": "ready"}',
            WebSocketDisconnect()
        ])

        await server.websocket_endpoint(mock_websocket)

        # Verify that disconnect was called
        server.manager.disconnect.assert_called_once_with(mock_websocket)

    @pytest.mark.asyncio
    async def test_websocket_endpoint_sends_initial_state(self, server, mock_state_machine, mock_websocket):
        """Test that initial state is properly sent"""
        from fastapi import WebSocketDisconnect

        mock_state_machine.get_current_state = AsyncMock(return_value={
            "active_source": "spotify",
            "plugin_state": "connected",
            "metadata": {"title": "Test Song"},
            "multiroom": {"enabled": True}
        })

        mock_websocket.receive_text = AsyncMock(side_effect=[
            '{"type": "ready"}',
            WebSocketDisconnect()
        ])

        await server.websocket_endpoint(mock_websocket)

        # Verify that the initial state contains the correct data
        sent_data = json.loads(mock_websocket.send_text.call_args_list[0][0][0])
        assert sent_data["data"]["full_state"]["active_source"] == "spotify"
        assert sent_data["data"]["full_state"]["plugin_state"] == "connected"

    # ===================
    # PING TESTS
    # ===================

    @pytest.mark.asyncio
    async def test_send_ping(self, server, mock_websocket):
        """Test ping sending"""
        # Create a modified version with a short interval
        server.PING_INTERVAL = 0.1

        # Launch the ping in a task with timeout
        ping_task = asyncio.create_task(server._send_ping(mock_websocket))

        # Wait a bit for the ping to be sent
        await asyncio.sleep(0.2)

        # Cancel the task
        ping_task.cancel()
        try:
            await ping_task
        except asyncio.CancelledError:
            pass

        # Verify that at least one ping was sent
        assert mock_websocket.send_text.called
        sent_data = json.loads(mock_websocket.send_text.call_args[0][0])
        assert sent_data["category"] == "system"
        assert sent_data["type"] == "ping"
        assert "timestamp" in sent_data

    @pytest.mark.asyncio
    async def test_send_ping_stops_on_error(self, server):
        """Test that ping stops on error"""
        bad_ws = AsyncMock()
        bad_ws.send_text = AsyncMock(side_effect=Exception("Connection lost"))

        server.PING_INTERVAL = 0.1

        # The task should terminate after the error
        ping_task = asyncio.create_task(server._send_ping(bad_ws))

        # Wait for the task to terminate (after the error)
        try:
            await asyncio.wait_for(ping_task, timeout=1.0)
        except asyncio.TimeoutError:
            ping_task.cancel()

        # The ping was attempted
        bad_ws.send_text.assert_called()


class TestWebSocketIntegration:
    """WebSocket integration tests"""

    @pytest.mark.asyncio
    async def test_full_flow(self):
        """Test full flow: connection, ready, state, disconnection"""
        from fastapi import WebSocketDisconnect

        # Create real objects
        manager = WebSocketManager()
        state_machine = Mock()
        state_machine.get_current_state = AsyncMock(return_value={
            "active_source": "bluetooth",
            "plugin_state": "connected",
            "metadata": {"device_name": "iPhone"},
            "multiroom": {"enabled": False},
            "equalizer": {"enabled": True}
        })

        server = WebSocketServer(manager, state_machine)

        # WebSocket mock
        ws = AsyncMock()
        sent_messages = []

        async def capture_send(msg):
            sent_messages.append(json.loads(msg))

        ws.send_text = capture_send
        ws.receive_text = AsyncMock(side_effect=[
            '{"type": "ready"}',
            WebSocketDisconnect()
        ])

        await server.websocket_endpoint(ws)

        # Verifications
        assert len(sent_messages) >= 1
        initial_state = sent_messages[0]
        assert initial_state["type"] == "initial_state"
        assert initial_state["data"]["full_state"]["active_source"] == "bluetooth"

        # Verify that the connection was cleaned up
        assert ws not in manager.active_connections
