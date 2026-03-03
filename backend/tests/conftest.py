# backend/tests/conftest.py
"""
Pytest configuration - Shared fixtures for all tests
"""
import pytest
from unittest.mock import Mock, AsyncMock
from backend.core.models.audio_state import AudioSource, PluginState



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
def mock_plugin():
    """Mock of an audio plugin"""
    plugin = Mock()
    plugin.initialize = AsyncMock(return_value=True)
    plugin.start = AsyncMock(return_value=True)
    plugin.stop = AsyncMock(return_value=True)
    plugin.restart = AsyncMock(return_value=True)
    plugin.status = AsyncMock(return_value={})
    plugin._initialized = True
    return plugin


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
