# backend/tests/integration/conftest.py
"""
Integration test fixtures for Milo backend.

These fixtures provide real components with mocked I/O dependencies,
allowing integration tests to validate component interactions without
requiring actual system resources (systemd, ALSA, network).
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock
from typing import Dict, List, Any

from backend.core.models.audio_state import AudioSource, PluginState
from backend.core.state import AudioStateMachine
from backend.core.events import EventBus


class WebSocketEventCollector:
    """
    Collects WebSocket events for test assertions.

    Captures all events broadcast through the state machine's websocket handler,
    allowing tests to verify event sequences and formats.
    """

    def __init__(self):
        self.events: List[Dict[str, Any]] = []
        self._lock = asyncio.Lock()

    async def handle_event(self, event_data: Dict[str, Any]) -> None:
        """Capture event for later inspection."""
        async with self._lock:
            self.events.append(event_data)

    def clear(self) -> None:
        """Clear collected events."""
        self.events.clear()

    def get_events_by_type(self, event_type: str) -> List[Dict[str, Any]]:
        """Filter events by type."""
        return [e for e in self.events if e.get("type") == event_type]

    def get_events_by_category(self, category: str) -> List[Dict[str, Any]]:
        """Filter events by category."""
        return [e for e in self.events if e.get("category") == category]


def create_mock_plugin(source: AudioSource, start_success: bool = True) -> Mock:
    """
    Factory function to create a mock plugin for testing.

    Args:
        source: The audio source this plugin represents
        start_success: Whether plugin.start() should succeed

    Returns:
        Mock plugin implementing AudioSourcePlugin interface
    """
    plugin = Mock()
    plugin.source = source
    plugin._initialized = True

    # Core interface methods
    plugin.initialize = AsyncMock(return_value=True)
    plugin.start = AsyncMock(return_value=start_success)
    plugin.stop = AsyncMock(return_value=True)
    plugin.restart = AsyncMock(return_value=True)
    plugin.get_status = AsyncMock(return_value={
        "state": "ready",
        "source_id": source.value,
        "service_running": True,
        "metadata": {}
    })
    plugin.handle_command = AsyncMock(return_value={"success": True})

    return plugin


@pytest.fixture
def websocket_collector() -> WebSocketEventCollector:
    """
    Fixture providing a WebSocket event collector.

    Use this to capture and inspect events broadcast during tests.
    """
    return WebSocketEventCollector()


@pytest.fixture
def mock_routing_service() -> Mock:
    """
    Mock routing service to avoid systemd/ALSA calls.
    """
    service = Mock()
    service.get_state = Mock(return_value={
        "multiroom_enabled": False,
        "equalizer_effects_enabled": True
    })
    service.set_multiroom_enabled = AsyncMock(return_value=True)
    service.set_equalizer_effects_enabled = AsyncMock(return_value=True)
    return service


@pytest.fixture
def mock_plugins() -> Dict[AudioSource, Mock]:
    """
    Dictionary of mock plugins for all audio sources.

    Returns:
        Dict mapping AudioSource to mock plugin
    """
    return {
        AudioSource.SPOTIFY: create_mock_plugin(AudioSource.SPOTIFY),
        AudioSource.RADIO: create_mock_plugin(AudioSource.RADIO),
        AudioSource.PODCAST: create_mock_plugin(AudioSource.PODCAST),
        AudioSource.BLUETOOTH: create_mock_plugin(AudioSource.BLUETOOTH),
        AudioSource.MAC: create_mock_plugin(AudioSource.MAC),
    }


@pytest.fixture
def integration_state_machine(
    websocket_collector: WebSocketEventCollector,
    mock_routing_service: Mock
) -> AudioStateMachine:
    """
    Create a real state machine with mock dependencies for integration testing.

    This fixture provides a fully functional state machine that can be used
    to test transitions and state management without requiring actual
    system resources.

    The state machine is configured with:
    - EventBus for decoupled communication
    - WebSocket collector to capture broadcast events
    - Mock routing service to avoid systemd calls
    - No plugins registered (register them in tests as needed)
    """
    event_bus = EventBus()
    state_machine = AudioStateMachine(event_bus=event_bus)
    state_machine.routing_service = mock_routing_service
    state_machine.websocket_handler = websocket_collector
    return state_machine


@pytest.fixture
def state_machine_with_plugins(
    integration_state_machine: AudioStateMachine,
    mock_plugins: Dict[AudioSource, Mock]
) -> AudioStateMachine:
    """
    State machine with all mock plugins pre-registered.

    Use this fixture when you need a state machine ready for transitions
    without manually registering plugins.
    """
    for source, plugin in mock_plugins.items():
        integration_state_machine.register_plugin(source, plugin)
    return integration_state_machine


@pytest.fixture
def failing_plugin() -> Mock:
    """
    Create a mock plugin that fails to start.

    Useful for testing error handling and rollback scenarios.
    """
    return create_mock_plugin(AudioSource.RADIO, start_success=False)


@pytest.fixture
def slow_plugin() -> Mock:
    """
    Create a mock plugin that takes a long time to start.

    Useful for testing timeout scenarios.
    """
    plugin = create_mock_plugin(AudioSource.RADIO)

    async def slow_start():
        await asyncio.sleep(10)  # Longer than TRANSITION_TIMEOUT
        return True

    plugin.start = slow_start
    return plugin
