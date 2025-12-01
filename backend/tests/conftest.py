# backend/tests/conftest.py
"""
Configuration pytest - Fixtures partagées pour tous les tests
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock
from backend.domain.audio_state import AudioSource, PluginState


@pytest.fixture
def event_loop():
    """Fixture pour créer une boucle d'événements asyncio pour chaque test"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_websocket_handler():
    """Mock du WebSocket handler"""
    handler = Mock()
    handler.handle_event = AsyncMock()
    return handler


@pytest.fixture
def mock_routing_service():
    """Mock du routing service"""
    service = Mock()
    service.get_state = Mock()
    service.set_multiroom_enabled = AsyncMock(return_value=True)
    service.set_equalizer_enabled = AsyncMock(return_value=True)
    return service


@pytest.fixture
def mock_plugin():
    """Mock d'un plugin audio"""
    plugin = Mock()
    plugin.initialize = AsyncMock(return_value=True)
    plugin.start = AsyncMock(return_value=True)
    plugin.stop = AsyncMock(return_value=True)
    plugin.restart = AsyncMock(return_value=True)
    plugin.get_status = AsyncMock(return_value={})
    plugin._initialized = True
    return plugin


@pytest.fixture
def mock_settings_service():
    """Mock du SettingsService"""
    service = Mock()
    service.get_setting = AsyncMock(return_value=None)
    service.set_setting = AsyncMock(return_value=True)
    service.load_settings = AsyncMock(return_value={})
    service.save_settings = AsyncMock(return_value=True)
    return service


@pytest.fixture
def mock_async_lock():
    """Mock d'asyncio.Lock pour les tests"""
    lock = AsyncMock()
    lock.__aenter__ = AsyncMock(return_value=None)
    lock.__aexit__ = AsyncMock(return_value=None)
    return lock
