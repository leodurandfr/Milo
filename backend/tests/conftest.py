# backend/tests/conftest.py
"""
Pytest configuration - Shared fixtures for all tests
"""
import pytest
from unittest.mock import Mock, AsyncMock
from backend.core.models.audio_state import SourceState


def attach_registry_broadcaster(registry, state_machine) -> None:
    """Forward ClientRegistryService events to state_machine.broadcast().

    Mirrors what SnapcastWebSocketService.set_registry does in production —
    the registry itself is a pure store, so tests that previously relied on
    `registry.set_state_machine(state_machine)` use this helper instead.
    """
    from backend.core.multiroom.client_registry import REGISTRY_EVENT_CLASSES

    async def _forward(event_type: str, data: dict) -> None:
        await state_machine.broadcast(REGISTRY_EVENT_CLASSES[event_type](**data))

    registry.subscribe(_forward)


def events_of(broadcast_mock, category: str, type_: str) -> list:
    """Typed events of a (category, type) pair captured by a mocked
    `state_machine.broadcast` (AsyncMock)."""
    return [
        c.args[0] for c in broadcast_mock.call_args_list
        if c.args[0].CATEGORY == category and c.args[0].TYPE == type_
    ]



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
    # Multiroom reroute hooks (default = stop()/start() on the real base class);
    # mocked as awaitables so _apply_transition can call them on this Mock.
    source.release_for_reroute = AsyncMock(return_value=True)
    source.acquire_after_reroute = AsyncMock(return_value=True)
    source.status = AsyncMock(return_value={})
    source.is_initialized = True
    source.state = SourceState.WAITING
    source.metadata = {}
    return source


@pytest.fixture
def mock_settings_service():
    """Mock of SettingsService — stateful for routing-style read-after-write tests.

    Reads via ``get_setting`` / ``get_setting_sync`` and writes via
    ``set_setting`` / ``set_setting_strict`` share an in-memory dict at
    ``service._storage``. Tests that need a starting value seed
    ``_storage`` directly (or override any of the mocks). Other tests that
    only assert call signatures continue to work unchanged — the mocks are
    still AsyncMock/Mock so ``assert_called_with`` etc. remain available.
    """
    service = Mock()
    service._storage: dict = {}

    def _get_sync(key):
        return service._storage.get(key)

    async def _get_async(key):
        return service._storage.get(key)

    async def _set_async(key, value):
        service._storage[key] = value
        return True

    async def _set_strict(key, value):
        service._storage[key] = value

    service.get_setting_sync = Mock(side_effect=_get_sync)
    service.get_setting = AsyncMock(side_effect=_get_async)
    service.set_setting = AsyncMock(side_effect=_set_async)
    service.set_setting_strict = AsyncMock(side_effect=_set_strict)
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
