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

from backend.core.models.audio_state import AudioSource, SourceState
from backend.core.state import AudioStateMachine


class WebSocketEventCollector:
    """
    Collects WebSocket events for test assertions.

    Mimics WebSocketManager.broadcast_dict() to capture all events
    broadcast through the state machine during tests.
    """

    def __init__(self):
        self.events: List[Dict[str, Any]] = []
        self._lock = asyncio.Lock()

    async def broadcast_dict(self, event_data: Dict[str, Any]) -> None:
        """Capture event for later inspection (same signature as WebSocketManager)."""
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


def create_mock_source(source: AudioSource, start_success: bool = True) -> Mock:
    """
    Factory function to create a mock audio source for testing.

    Args:
        source: The audio source this mock represents
        start_success: Whether source.start() should succeed

    Returns:
        Mock source implementing AudioSource interface
    """
    mock = Mock()
    mock.source = source
    mock.is_initialized = True

    # Core interface methods
    mock.initialize = AsyncMock(return_value=True)
    mock.start = AsyncMock(return_value=start_success)
    mock.stop = AsyncMock(return_value=True)
    mock.restart = AsyncMock(return_value=True)
    mock.status = AsyncMock(return_value={
        "state": "ready",
        "source_id": source.value,
        "service_running": True,
        "metadata": {}
    })
    mock.command = AsyncMock(return_value={"success": True})
    mock.state = SourceState.READY
    mock.metadata = {}

    return mock


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
def mock_sources() -> Dict[AudioSource, Mock]:
    """
    Dictionary of mock sources for all audio sources.

    Returns:
        Dict mapping AudioSource to mock source
    """
    return {
        AudioSource.SPOTIFY: create_mock_source(AudioSource.SPOTIFY),
        AudioSource.RADIO: create_mock_source(AudioSource.RADIO),
        AudioSource.PODCAST: create_mock_source(AudioSource.PODCAST),
        AudioSource.BLUETOOTH: create_mock_source(AudioSource.BLUETOOTH),
        AudioSource.MAC: create_mock_source(AudioSource.MAC),
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
    - WebSocket collector to capture broadcast events
    - Mock routing service to avoid systemd calls
    - No sources registered (register them in tests as needed)
    """
    state_machine = AudioStateMachine()
    state_machine.routing_service = mock_routing_service
    state_machine.ws_manager = websocket_collector
    return state_machine


@pytest.fixture
def state_machine_with_sources(
    integration_state_machine: AudioStateMachine,
    mock_sources: Dict[AudioSource, Mock]
) -> AudioStateMachine:
    """
    State machine with all mock sources pre-registered.

    Use this fixture when you need a state machine ready for transitions
    without manually registering sources.
    """
    for source, mock in mock_sources.items():
        integration_state_machine.register_source(source, mock)
    return integration_state_machine


@pytest.fixture
def failing_source() -> Mock:
    """
    Create a mock source that fails to start.

    Useful for testing error handling and rollback scenarios.
    """
    return create_mock_source(AudioSource.RADIO, start_success=False)


@pytest.fixture
def slow_source() -> Mock:
    """
    Create a mock source that takes a long time to start.

    Useful for testing timeout scenarios.
    """
    mock = create_mock_source(AudioSource.RADIO)

    async def slow_start():
        await asyncio.sleep(10)  # Longer than TRANSITION_TIMEOUT
        return True

    mock.start = slow_start
    return mock
