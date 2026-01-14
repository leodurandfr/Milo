# backend/tests/test_eventbus.py
"""
Unit tests for EventBus.

Tests cover:
- Handler registration and removal (AC1)
- Async and sync handler support (AC2)
- Event emission and propagation (AC3)
- Error isolation (AC4)
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock

from backend.core.events import EventBus, Events, get_event_bus, reset_event_bus


class TestEventBusRegistration:
    """Test handler registration (AC1)."""

    def test_on_registers_handler(self):
        """Test that on() registers a handler."""
        bus = EventBus()
        handler = Mock()

        bus.on("test.event", handler)

        assert handler in bus._handlers["test.event"]

    def test_on_creates_handler_list(self):
        """Test that on() creates handler list if not exists."""
        bus = EventBus()
        handler = Mock()

        bus.on("new.event", handler)

        assert "new.event" in bus._handlers
        assert len(bus._handlers["new.event"]) == 1

    def test_on_does_not_duplicate_handler(self):
        """Test that same handler is not registered twice."""
        bus = EventBus()
        handler = Mock()

        bus.on("test.event", handler)
        bus.on("test.event", handler)

        assert len(bus._handlers["test.event"]) == 1

    def test_off_removes_handler(self):
        """Test that off() removes a handler."""
        bus = EventBus()
        handler = Mock()

        bus.on("test.event", handler)
        bus.off("test.event", handler)

        assert handler not in bus._handlers.get("test.event", [])

    def test_off_nonexistent_event_no_error(self):
        """Test that off() on nonexistent event doesn't raise."""
        bus = EventBus()
        handler = Mock()

        # Should not raise
        bus.off("nonexistent.event", handler)

    def test_off_nonexistent_handler_no_error(self):
        """Test that off() with nonexistent handler doesn't raise."""
        bus = EventBus()
        handler1 = Mock()
        handler2 = Mock()

        bus.on("test.event", handler1)
        # Should not raise
        bus.off("test.event", handler2)

        assert handler1 in bus._handlers["test.event"]

    def test_clear_removes_all_handlers_for_event(self):
        """Test that clear(event) removes all handlers for that event."""
        bus = EventBus()
        handler1 = Mock()
        handler2 = Mock()

        bus.on("test.event", handler1)
        bus.on("test.event", handler2)
        bus.clear("test.event")

        assert "test.event" not in bus._handlers

    def test_clear_all_removes_everything(self):
        """Test that clear() removes all handlers."""
        bus = EventBus()

        bus.on("event1", Mock())
        bus.on("event2", Mock())
        bus.clear()

        assert len(bus._handlers) == 0


class TestEventBusEmission:
    """Test event emission (AC1, AC3)."""

    @pytest.mark.asyncio
    async def test_emit_calls_handler(self):
        """Test that emit() calls registered handler."""
        bus = EventBus()
        received = []

        def handler(data):
            received.append(data)

        bus.on("test.event", handler)
        await bus.emit("test.event", {"key": "value"})

        assert received == [{"key": "value"}]

    @pytest.mark.asyncio
    async def test_emit_with_none_data(self):
        """Test emit() with None data."""
        bus = EventBus()
        received = []

        def handler(data):
            received.append(data)

        bus.on("test.event", handler)
        await bus.emit("test.event")

        assert received == [None]

    @pytest.mark.asyncio
    async def test_emit_calls_multiple_handlers(self):
        """Test that emit() calls all registered handlers."""
        bus = EventBus()
        calls = []

        def handler1(data):
            calls.append(("h1", data))

        def handler2(data):
            calls.append(("h2", data))

        bus.on("test.event", handler1)
        bus.on("test.event", handler2)
        await bus.emit("test.event", "data")

        assert calls == [("h1", "data"), ("h2", "data")]

    @pytest.mark.asyncio
    async def test_emit_nonexistent_event_no_error(self):
        """Test that emit() on nonexistent event doesn't raise."""
        bus = EventBus()

        # Should not raise
        await bus.emit("nonexistent.event", {})

    @pytest.mark.asyncio
    async def test_emit_maintains_handler_order(self):
        """Test that handlers are called in registration order."""
        bus = EventBus()
        order = []

        bus.on("test.event", lambda d: order.append(1))
        bus.on("test.event", lambda d: order.append(2))
        bus.on("test.event", lambda d: order.append(3))

        await bus.emit("test.event", None)

        assert order == [1, 2, 3]


class TestAsyncSyncHandlers:
    """Test async and sync handler support (AC2)."""

    @pytest.mark.asyncio
    async def test_sync_handler_called(self):
        """Test that sync handlers are called correctly."""
        bus = EventBus()
        mock_handler = Mock()

        bus.on("test.event", mock_handler)
        await bus.emit("test.event", "data")

        mock_handler.assert_called_once_with("data")

    @pytest.mark.asyncio
    async def test_async_handler_awaited(self):
        """Test that async handlers are awaited."""
        bus = EventBus()
        received = []

        async def async_handler(data):
            await asyncio.sleep(0.01)  # Simulate async work
            received.append(data)

        bus.on("test.event", async_handler)
        await bus.emit("test.event", "async_data")

        assert received == ["async_data"]

    @pytest.mark.asyncio
    async def test_mixed_sync_async_handlers(self):
        """Test mixing sync and async handlers."""
        bus = EventBus()
        results = []

        def sync_handler(data):
            results.append(f"sync:{data}")

        async def async_handler(data):
            results.append(f"async:{data}")

        bus.on("test.event", sync_handler)
        bus.on("test.event", async_handler)
        await bus.emit("test.event", "value")

        assert results == ["sync:value", "async:value"]

    @pytest.mark.asyncio
    async def test_async_mock_handler(self):
        """Test AsyncMock handlers work correctly."""
        bus = EventBus()
        mock_handler = AsyncMock()

        bus.on("test.event", mock_handler)
        await bus.emit("test.event", {"key": "value"})

        mock_handler.assert_awaited_once_with({"key": "value"})


class TestErrorHandling:
    """Test error isolation (AC4)."""

    @pytest.mark.asyncio
    async def test_handler_error_does_not_stop_others(self):
        """Test that error in one handler doesn't stop others."""
        bus = EventBus()
        called = []

        def handler1(data):
            called.append(1)

        def handler2(data):
            raise ValueError("Test error")

        def handler3(data):
            called.append(3)

        bus.on("test.event", handler1)
        bus.on("test.event", handler2)
        bus.on("test.event", handler3)

        await bus.emit("test.event", None)

        assert called == [1, 3]

    @pytest.mark.asyncio
    async def test_async_handler_error_does_not_stop_others(self):
        """Test that error in async handler doesn't stop others."""
        bus = EventBus()
        called = []

        async def handler1(data):
            called.append(1)

        async def handler2(data):
            raise RuntimeError("Async error")

        async def handler3(data):
            called.append(3)

        bus.on("test.event", handler1)
        bus.on("test.event", handler2)
        bus.on("test.event", handler3)

        await bus.emit("test.event", None)

        assert called == [1, 3]


class TestHelperMethods:
    """Test helper methods."""

    def test_has_handlers_true(self):
        """Test has_handlers returns True when handlers exist."""
        bus = EventBus()
        bus.on("test.event", Mock())

        assert bus.has_handlers("test.event") is True

    def test_has_handlers_false(self):
        """Test has_handlers returns False when no handlers."""
        bus = EventBus()

        assert bus.has_handlers("test.event") is False

    def test_handler_count(self):
        """Test handler_count returns correct count."""
        bus = EventBus()

        bus.on("test.event", Mock())
        bus.on("test.event", Mock())

        assert bus.handler_count("test.event") == 2

    def test_handler_count_zero(self):
        """Test handler_count returns 0 for unknown event."""
        bus = EventBus()

        assert bus.handler_count("unknown.event") == 0


class TestDebugMode:
    """Test debug logging."""

    @pytest.mark.asyncio
    async def test_debug_mode_logs_events(self, caplog):
        """Test that debug mode logs events."""
        import logging

        bus = EventBus(debug=True)

        with caplog.at_level(logging.DEBUG):
            await bus.emit("test.event", {"data": "value"})

        assert "test.event" in caplog.text


class TestEventConstants:
    """Test event name constants."""

    def test_source_events_defined(self):
        """Test source event constants."""
        assert Events.SOURCE_STARTED == "source.started"
        assert Events.SOURCE_STOPPED == "source.stopped"
        assert Events.SOURCE_ERROR == "source.error"

    def test_volume_events_defined(self):
        """Test volume event constants."""
        assert Events.VOLUME_CHANGED == "volume.changed"
        assert Events.VOLUME_MUTED == "volume.muted"

    def test_registry_events_defined(self):
        """Test registry event constants."""
        assert Events.ZONE_CREATED == "registry.zone_created"
        assert Events.ZONE_DELETED == "registry.zone_deleted"


class TestSingleton:
    """Test singleton functionality."""

    def test_get_event_bus_returns_same_instance(self):
        """Test that get_event_bus returns the same instance."""
        reset_event_bus()

        bus1 = get_event_bus()
        bus2 = get_event_bus()

        assert bus1 is bus2

    def test_reset_event_bus_clears_singleton(self):
        """Test that reset_event_bus clears the singleton."""
        bus1 = get_event_bus()
        reset_event_bus()
        bus2 = get_event_bus()

        assert bus1 is not bus2
