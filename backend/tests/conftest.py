# backend/tests/conftest.py
"""
Pytest configuration - Shared fixtures for all tests
"""
import pytest
from unittest.mock import Mock, AsyncMock
from backend.core.models.audio_state import SourceState


def attach_registry_broadcaster(registry, state_machine) -> None:
    """Forward ClientRegistryService events to state_machine.broadcast_event.

    Mirrors what SnapcastWebSocketService.set_registry does in production —
    the registry itself is a pure store, so tests that previously relied on
    `registry.set_state_machine(state_machine)` use this helper instead.
    """
    from backend.core.multiroom.client_registry import REGISTRY_EVENT_TYPE_MAP

    async def _forward(event_type: str, data: dict) -> None:
        mapped_type = REGISTRY_EVENT_TYPE_MAP.get(event_type, event_type.lower())
        await state_machine.broadcast_event("multiroom", mapped_type, data)

    registry.subscribe(_forward)



@pytest.fixture
def mock_ws_manager():
    """Mock of WebSocketManager"""
    manager = Mock()
    manager.broadcast_dict = AsyncMock()
    return manager


@pytest.fixture
def mock_routing_service():
    """Mock of routing service"""
    service = Mock()
    service.get_state = Mock()
    service.set_multiroom_enabled = AsyncMock(return_value=True)
    service.set_equalizer_effects_enabled = AsyncMock(return_value=True)
    return service


@pytest.fixture
def mock_source():
    """Mock of an audio source"""
    source = Mock()
    source.initialize = AsyncMock(return_value=True)
    source.start = AsyncMock(return_value=True)
    source.stop = AsyncMock(return_value=True)
    source.restart = AsyncMock(return_value=True)
    source.status = AsyncMock(return_value={})
    source._initialized = True
    source.state = SourceState.WAITING
    source.metadata = {}
    return source


@pytest.fixture
def mock_settings_service():
    """Mock of SettingsService"""
    service = Mock()
    service.get_setting = AsyncMock(return_value=None)
    service.set_setting = AsyncMock(return_value=True)
    service.load_settings = AsyncMock(return_value={})
    service.save_settings = AsyncMock(return_value=True)
    return service


@pytest.fixture
def mock_async_lock():
    """Mock of asyncio.Lock for tests"""
    lock = AsyncMock()
    lock.__aenter__ = AsyncMock(return_value=None)
    lock.__aexit__ = AsyncMock(return_value=None)
    return lock
