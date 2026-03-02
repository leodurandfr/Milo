# backend/tests/test_audio_state_machine.py
"""
Unit tests for AudioStateMachine.

Tests cover:
- Source activation/deactivation (AC1, AC3)
- EventBus emission (AC2)
- State transitions (AC5)
- WebSocket broadcasting
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch

from backend.core.state import AudioStateMachine
from backend.core.events import EventBus, Events
from backend.core.models.audio_state import AudioSource, PluginState


@pytest.fixture
def event_bus():
    """Create a fresh EventBus for each test."""
    return EventBus(debug=True)


@pytest.fixture
def state_machine(event_bus):
    """Create AudioStateMachine with EventBus."""
    return AudioStateMachine(event_bus)


@pytest.fixture
def mock_plugin():
    """Create a mock plugin."""
    plugin = Mock()
    plugin.initialize = AsyncMock(return_value=True)
    plugin.start = AsyncMock(return_value=True)
    plugin.stop = AsyncMock(return_value=True)
    plugin._initialized = False
    return plugin


class TestAudioStateMachineBasics:
    """Test basic state machine operations."""

    def test_initial_state(self, state_machine):
        """Test initial state is NONE with READY."""
        state = state_machine.get_state()
        assert state["active_source"] == "none"
        assert state["plugin_state"] == "ready"
        assert state["transitioning"] is False
        assert state["error"] is None

    def test_register_plugin(self, state_machine, mock_plugin):
        """Test plugin registration."""
        state_machine.register_plugin(AudioSource.RADIO, mock_plugin)

        assert state_machine.get_plugin(AudioSource.RADIO) is mock_plugin
        assert state_machine.get_plugin(AudioSource.SPOTIFY) is None

    def test_get_plugin_metadata_active(self, state_machine):
        """Test get_plugin_metadata returns metadata for active source."""
        state_machine.system_state.active_source = AudioSource.RADIO
        state_machine.system_state.metadata = {"title": "Test"}

        metadata = state_machine.get_plugin_metadata(AudioSource.RADIO)
        assert metadata == {"title": "Test"}

    def test_get_plugin_metadata_inactive(self, state_machine):
        """Test get_plugin_metadata returns empty for inactive source."""
        state_machine.system_state.active_source = AudioSource.RADIO

        metadata = state_machine.get_plugin_metadata(AudioSource.SPOTIFY)
        assert metadata == {}


class TestSourceActivation:
    """Test source activation and deactivation."""

    @pytest.mark.asyncio
    async def test_activate_source(self, state_machine, mock_plugin):
        """Test activating a source."""
        state_machine.register_plugin(AudioSource.RADIO, mock_plugin)

        result = await state_machine.activate_source(AudioSource.RADIO)

        assert result is True
        assert state_machine.system_state.active_source == AudioSource.RADIO
        mock_plugin.initialize.assert_called_once()
        mock_plugin.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_activate_unregistered_source(self, state_machine):
        """Test activating an unregistered source fails."""
        result = await state_machine.activate_source(AudioSource.RADIO)

        assert result is False
        assert state_machine.system_state.active_source == AudioSource.NONE

    @pytest.mark.asyncio
    async def test_deactivate_source(self, state_machine, mock_plugin):
        """Test deactivating a source."""
        state_machine.register_plugin(AudioSource.RADIO, mock_plugin)
        await state_machine.activate_source(AudioSource.RADIO)

        result = await state_machine.deactivate_source()

        assert result is True
        assert state_machine.system_state.active_source == AudioSource.NONE
        mock_plugin.stop.assert_called()

    @pytest.mark.asyncio
    async def test_already_active_source(self, state_machine, mock_plugin):
        """Test activating already active source returns True."""
        state_machine.register_plugin(AudioSource.RADIO, mock_plugin)
        await state_machine.activate_source(AudioSource.RADIO)
        mock_plugin.reset_mock()

        result = await state_machine.activate_source(AudioSource.RADIO)

        assert result is True
        # Should not call start again
        mock_plugin.start.assert_not_called()


class TestDirectTransition:
    """Test direct transition between two sources."""

    @pytest.mark.asyncio
    async def test_direct_transition(self, state_machine):
        """Test switching directly from one source to another."""
        mock_radio = Mock()
        mock_radio.initialize = AsyncMock(return_value=True)
        mock_radio.start = AsyncMock(return_value=True)
        mock_radio.stop = AsyncMock(return_value=True)
        mock_radio._initialized = False

        mock_spotify = Mock()
        mock_spotify.initialize = AsyncMock(return_value=True)
        mock_spotify.start = AsyncMock(return_value=True)
        mock_spotify.stop = AsyncMock(return_value=True)
        mock_spotify._initialized = False

        state_machine.register_plugin(AudioSource.RADIO, mock_radio)
        state_machine.register_plugin(AudioSource.SPOTIFY, mock_spotify)

        # Start with radio
        await state_machine.activate_source(AudioSource.RADIO)
        assert state_machine.system_state.active_source == AudioSource.RADIO

        # Switch to spotify
        await state_machine.activate_source(AudioSource.SPOTIFY)

        assert state_machine.system_state.active_source == AudioSource.SPOTIFY
        mock_radio.stop.assert_called()
        mock_spotify.start.assert_called()


class TestEventBusEmission:
    """Test EventBus event emission."""

    @pytest.mark.asyncio
    async def test_source_started_event(self, state_machine, event_bus, mock_plugin):
        """Test SOURCE_STARTED event is emitted."""
        received = []

        async def handler(data):
            received.append(data)

        event_bus.on(Events.SOURCE_STARTED, handler)
        state_machine.register_plugin(AudioSource.RADIO, mock_plugin)

        await state_machine.activate_source(AudioSource.RADIO)

        assert len(received) == 1
        assert received[0]["source"] == "radio"
        assert received[0]["old_source"] == "none"

    @pytest.mark.asyncio
    async def test_source_stopped_event(self, state_machine, event_bus, mock_plugin):
        """Test SOURCE_STOPPED event is emitted on deactivation."""
        received = []

        async def handler(data):
            received.append(data)

        event_bus.on(Events.SOURCE_STOPPED, handler)
        state_machine.register_plugin(AudioSource.RADIO, mock_plugin)

        await state_machine.activate_source(AudioSource.RADIO)
        await state_machine.deactivate_source()

        assert len(received) == 1
        assert received[0]["source"] == "radio"

    @pytest.mark.asyncio
    async def test_transition_start_event(self, state_machine, event_bus, mock_plugin):
        """Test TRANSITION_START event is emitted."""
        received = []

        async def handler(data):
            received.append(data)

        event_bus.on(Events.TRANSITION_START, handler)
        state_machine.register_plugin(AudioSource.RADIO, mock_plugin)

        await state_machine.activate_source(AudioSource.RADIO)

        assert len(received) == 1
        assert received[0]["from_source"] == "none"
        assert received[0]["to_source"] == "radio"

    @pytest.mark.asyncio
    async def test_source_state_changed_event(self, state_machine, event_bus, mock_plugin):
        """Test SOURCE_STATE_CHANGED event is emitted."""
        received = []

        async def handler(data):
            received.append(data)

        event_bus.on(Events.SOURCE_STATE_CHANGED, handler)
        state_machine.register_plugin(AudioSource.RADIO, mock_plugin)

        # Activate source first
        await state_machine.activate_source(AudioSource.RADIO)

        # Update plugin state
        await state_machine.update_plugin_state(
            AudioSource.RADIO,
            PluginState.CONNECTED,
            {"title": "Test Station"}
        )

        assert len(received) == 1
        assert received[0]["source"] == "radio"
        assert received[0]["new_state"] == "connected"


class TestPluginStateUpdate:
    """Test plugin state updates."""

    @pytest.mark.asyncio
    async def test_update_plugin_state(self, state_machine, mock_plugin):
        """Test updating plugin state."""
        state_machine.register_plugin(AudioSource.RADIO, mock_plugin)
        await state_machine.activate_source(AudioSource.RADIO)

        await state_machine.update_plugin_state(
            AudioSource.RADIO,
            PluginState.CONNECTED,
            {"title": "Test Station"}
        )

        assert state_machine.system_state.plugin_state == PluginState.CONNECTED
        assert state_machine.system_state.metadata["title"] == "Test Station"

    @pytest.mark.asyncio
    async def test_update_plugin_state_inactive_source(self, state_machine, mock_plugin):
        """Test updating inactive source state is ignored."""
        state_machine.register_plugin(AudioSource.RADIO, mock_plugin)
        await state_machine.activate_source(AudioSource.RADIO)

        # Try to update spotify while radio is active
        await state_machine.update_plugin_state(
            AudioSource.SPOTIFY,
            PluginState.CONNECTED,
            {}
        )

        # Should still be starting (from activation), not connected
        assert state_machine.system_state.plugin_state != PluginState.CONNECTED

    @pytest.mark.asyncio
    async def test_update_plugin_state_error(self, state_machine, mock_plugin):
        """Test updating plugin state to ERROR."""
        state_machine.register_plugin(AudioSource.RADIO, mock_plugin)
        await state_machine.activate_source(AudioSource.RADIO)

        await state_machine.update_plugin_state(
            AudioSource.RADIO,
            PluginState.ERROR,
            {"error": "Connection failed"}
        )

        assert state_machine.system_state.plugin_state == PluginState.ERROR
        assert state_machine.system_state.error == "Connection failed"


class TestMultiroomAndEqualizer:
    """Test multiroom and Equalizer state updates."""

    @pytest.mark.asyncio
    async def test_update_multiroom_state(self, state_machine, event_bus):
        """Test updating multiroom state."""
        received = []

        async def handler(data):
            received.append(data)

        event_bus.on(Events.ROUTING_MODE_CHANGED, handler)

        await state_machine.update_multiroom_state(True)

        assert state_machine.system_state.multiroom_enabled is True
        assert len(received) == 1
        assert received[0]["multiroom_enabled"] is True

    @pytest.mark.asyncio
    async def test_update_equalizer_effects_state(self, state_machine, event_bus):
        """Test updating Equalizer effects state."""
        received = []

        async def handler(data):
            received.append(data)

        event_bus.on(Events.EQUALIZER_CONFIG_CHANGED, handler)

        await state_machine.update_equalizer_effects_state(True)

        assert state_machine.system_state.equalizer_effects_enabled is True
        assert len(received) == 1
        assert received[0]["equalizer_effects_enabled"] is True


class TestWebSocketBroadcasting:
    """Test WebSocket broadcasting via ws_manager."""

    @pytest.mark.asyncio
    async def test_broadcast_event_with_ws_manager(self, state_machine):
        """Test broadcast_event calls ws_manager.broadcast_dict."""
        mock_manager = Mock()
        mock_manager.broadcast_dict = AsyncMock()
        state_machine.ws_manager = mock_manager

        await state_machine.broadcast_event("test", "event", {"key": "value"})

        mock_manager.broadcast_dict.assert_called_once()
        call_args = mock_manager.broadcast_dict.call_args[0][0]
        assert call_args["category"] == "test"
        assert call_args["type"] == "event"
        assert call_args["data"]["key"] == "value"

    @pytest.mark.asyncio
    async def test_broadcast_event_without_ws_manager(self, state_machine):
        """Test broadcast_event works without ws_manager."""
        # Should not raise
        await state_machine.broadcast_event("test", "event", {"key": "value"})

    @pytest.mark.asyncio
    async def test_broadcast_includes_full_state_for_plugin(self, state_machine):
        """Test plugin events include full_state."""
        mock_manager = Mock()
        mock_manager.broadcast_dict = AsyncMock()
        state_machine.ws_manager = mock_manager

        await state_machine.broadcast_event("plugin", "state_changed", {"source": "radio"})

        call_args = mock_manager.broadcast_dict.call_args[0][0]
        assert "full_state" in call_args["data"]

    @pytest.mark.asyncio
    async def test_broadcast_excludes_full_state_for_volume(self, state_machine):
        """Test volume events do not include full_state."""
        mock_manager = Mock()
        mock_manager.broadcast_dict = AsyncMock()
        state_machine.ws_manager = mock_manager

        await state_machine.broadcast_event("volume", "volume_changed", {"source": "volume"})

        call_args = mock_manager.broadcast_dict.call_args[0][0]
        assert "full_state" not in call_args["data"]

    @pytest.mark.asyncio
    async def test_transition_broadcasts_to_websocket(self, state_machine, mock_plugin):
        """Test transitions broadcast to ws_manager."""
        mock_manager = Mock()
        mock_manager.broadcast_dict = AsyncMock()
        state_machine.ws_manager = mock_manager
        state_machine.register_plugin(AudioSource.RADIO, mock_plugin)

        await state_machine.activate_source(AudioSource.RADIO)

        # Should have called broadcast_dict for transition_start and transition_complete
        assert mock_manager.broadcast_dict.call_count >= 2


class TestTransitionTimeout:
    """Test transition timeout handling."""

    @pytest.mark.asyncio
    async def test_transition_timeout(self, state_machine, event_bus):
        """Test transition timeout emits error."""
        slow_plugin = Mock()
        slow_plugin.initialize = AsyncMock(return_value=True)

        async def slow_start():
            await asyncio.sleep(10)  # Longer than TRANSITION_TIMEOUT
            return True

        slow_plugin.start = slow_start
        slow_plugin.stop = AsyncMock(return_value=True)
        slow_plugin._initialized = False

        state_machine.register_plugin(AudioSource.RADIO, slow_plugin)
        state_machine.TRANSITION_TIMEOUT = 0.1  # Very short timeout for test

        received_errors = []

        async def handler(data):
            received_errors.append(data)

        event_bus.on(Events.SOURCE_ERROR, handler)

        result = await state_machine.activate_source(AudioSource.RADIO)

        assert result is False
        assert len(received_errors) == 1
        assert "timeout" in received_errors[0]["error"].lower()


class TestEmergencyStop:
    """Test emergency stop functionality."""

    @pytest.mark.asyncio
    async def test_emergency_stop_on_error(self, state_machine):
        """Test emergency stop is called on transition error."""
        failing_plugin = Mock()
        failing_plugin.initialize = AsyncMock(return_value=True)
        failing_plugin.start = AsyncMock(side_effect=Exception("Start failed"))
        failing_plugin.stop = AsyncMock(return_value=True)
        failing_plugin._initialized = False

        state_machine.register_plugin(AudioSource.RADIO, failing_plugin)

        result = await state_machine.activate_source(AudioSource.RADIO)

        assert result is False
        # Emergency stop should have been called
        failing_plugin.stop.assert_called()
        assert state_machine.system_state.active_source == AudioSource.NONE


class TestGetCurrentState:
    """Test get_current_state method."""

    @pytest.mark.asyncio
    async def test_get_current_state_async(self, state_machine):
        """Test get_current_state returns state dict."""
        state = await state_machine.get_current_state()

        assert isinstance(state, dict)
        assert "active_source" in state
        assert "plugin_state" in state
        assert "transitioning" in state
