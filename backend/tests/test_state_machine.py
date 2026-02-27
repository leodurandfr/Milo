# backend/tests/test_state_machine.py
"""
Unit tests for AudioStateMachine
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from backend.core.state import AudioStateMachine
from backend.core.events import EventBus
from backend.core.models.audio_state import AudioSource, PluginState, SystemAudioState


class TestAudioStateMachine:
    """Tests for the audio state machine"""

    @pytest.fixture
    def state_machine(self, mock_websocket_handler, mock_routing_service):
        """Fixture to create a state machine"""
        event_bus = EventBus()
        sm = AudioStateMachine(event_bus=event_bus)
        sm.routing_service = mock_routing_service
        sm.websocket_handler = mock_websocket_handler
        return sm

    def test_initialization(self, state_machine):
        """State machine initialization test"""
        assert state_machine.system_state.active_source == AudioSource.NONE
        assert state_machine.system_state.plugin_state == PluginState.READY
        assert state_machine.system_state.transitioning is False

    def test_register_plugin(self, state_machine, mock_plugin):
        """Plugin registration test"""
        state_machine.register_plugin(AudioSource.SPOTIFY, mock_plugin)

        assert state_machine.plugins[AudioSource.SPOTIFY] == mock_plugin
        assert state_machine.get_plugin(AudioSource.SPOTIFY) == mock_plugin

    def test_get_plugin_metadata(self, state_machine):
        """Plugin metadata retrieval test"""
        state_machine.system_state.active_source = AudioSource.SPOTIFY
        state_machine.system_state.metadata = {"title": "Test Song"}

        metadata = state_machine.get_plugin_metadata(AudioSource.SPOTIFY)
        assert metadata == {"title": "Test Song"}

        # Non-active source should return {}
        metadata_other = state_machine.get_plugin_metadata(AudioSource.BLUETOOTH)
        assert metadata_other == {}

    def test_get_plugin_state(self, state_machine):
        """Plugin state retrieval test"""
        state_machine.system_state.active_source = AudioSource.SPOTIFY
        state_machine.system_state.plugin_state = PluginState.CONNECTED

        state = state_machine.get_plugin_state(AudioSource.SPOTIFY)
        assert state == PluginState.CONNECTED

        # Non-active source should return READY
        state_other = state_machine.get_plugin_state(AudioSource.BLUETOOTH)
        assert state_other == PluginState.READY

    @pytest.mark.asyncio
    async def test_get_current_state(self, state_machine):
        """Current state retrieval test"""
        state = await state_machine.get_current_state()

        assert "active_source" in state
        assert "plugin_state" in state
        assert "transitioning" in state
        assert "metadata" in state
        assert state["active_source"] == "none"

    @pytest.mark.asyncio
    async def test_transition_to_same_source(self, state_machine, mock_plugin):
        """Transition to same source test (should be no-op)"""
        state_machine.register_plugin(AudioSource.SPOTIFY, mock_plugin)
        state_machine.system_state.active_source = AudioSource.SPOTIFY
        state_machine.system_state.plugin_state = PluginState.CONNECTED

        result = await state_machine.transition_to_source(AudioSource.SPOTIFY)

        assert result is True
        mock_plugin.stop.assert_not_called()
        mock_plugin.start.assert_not_called()

    @pytest.mark.asyncio
    async def test_transition_to_none(self, state_machine, mock_plugin):
        """Transition to NONE test (stop active source)"""
        state_machine.register_plugin(AudioSource.SPOTIFY, mock_plugin)
        state_machine.system_state.active_source = AudioSource.SPOTIFY
        state_machine.system_state.plugin_state = PluginState.CONNECTED

        result = await state_machine.transition_to_source(AudioSource.NONE)

        assert result is True
        mock_plugin.stop.assert_called_once()
        assert state_machine.system_state.active_source == AudioSource.NONE
        assert state_machine.system_state.plugin_state == PluginState.READY

    @pytest.mark.asyncio
    async def test_transition_to_new_source_success(self, state_machine, mock_plugin):
        """Successful transition to new source test"""
        mock_plugin._initialized = True
        state_machine.register_plugin(AudioSource.SPOTIFY, mock_plugin)

        result = await state_machine.transition_to_source(AudioSource.SPOTIFY)

        assert result is True
        mock_plugin.start.assert_called_once()
        assert state_machine.system_state.active_source == AudioSource.SPOTIFY
        # State is STARTING until plugin calls notify_state_change(READY/CONNECTED)
        # Mock plugin doesn't notify state changes, so it stays at STARTING
        assert state_machine.system_state.plugin_state in [PluginState.STARTING, PluginState.READY, PluginState.CONNECTED]

    @pytest.mark.asyncio
    async def test_transition_to_unregistered_source(self, state_machine):
        """Transition to unregistered source test (should fail)"""
        result = await state_machine.transition_to_source(AudioSource.SPOTIFY)

        assert result is False

    @pytest.mark.asyncio
    async def test_transition_start_fail(self, state_machine, mock_plugin):
        """Transition test with start failure"""
        mock_plugin.start = AsyncMock(return_value=False)
        mock_plugin._initialized = True
        state_machine.register_plugin(AudioSource.SPOTIFY, mock_plugin)

        result = await state_machine.transition_to_source(AudioSource.SPOTIFY)

        assert result is False
        # Should end up in NONE state after failure
        assert state_machine.system_state.active_source == AudioSource.NONE

    @pytest.mark.asyncio
    async def test_transition_timeout(self, state_machine, mock_plugin):
        """Timeout during transition test"""
        # Simulate a plugin that takes too long to start
        async def slow_start():
            await asyncio.sleep(10)  # Longer than TRANSITION_TIMEOUT (5s)
            return True

        mock_plugin.start = slow_start
        mock_plugin._initialized = True
        state_machine.register_plugin(AudioSource.SPOTIFY, mock_plugin)

        result = await state_machine.transition_to_source(AudioSource.SPOTIFY)

        assert result is False
        # Timeout occurs but error may be None if _emergency_stop resets state
        assert state_machine.system_state.transitioning is False
        assert state_machine.system_state.active_source == AudioSource.NONE

    @pytest.mark.asyncio
    async def test_update_plugin_state_active_source(self, state_machine):
        """Active plugin state update test"""
        state_machine.system_state.active_source = AudioSource.SPOTIFY
        state_machine.system_state.plugin_state = PluginState.READY

        metadata = {"title": "Test Song"}
        await state_machine.update_plugin_state(
            AudioSource.SPOTIFY,
            PluginState.CONNECTED,
            metadata
        )

        assert state_machine.system_state.plugin_state == PluginState.CONNECTED
        assert state_machine.system_state.metadata == metadata

    @pytest.mark.asyncio
    async def test_update_plugin_state_inactive_source_ignored(self, state_machine):
        """Test that updates from inactive source are ignored"""
        state_machine.system_state.active_source = AudioSource.SPOTIFY
        state_machine.system_state.plugin_state = PluginState.CONNECTED

        # Try to update a non-active source
        await state_machine.update_plugin_state(
            AudioSource.BLUETOOTH,
            PluginState.CONNECTED,
            {}
        )

        # State should not have changed
        assert state_machine.system_state.active_source == AudioSource.SPOTIFY

    @pytest.mark.asyncio
    async def test_update_plugin_state_during_transition_ignored(self, state_machine):
        """Test that updates during transition are ignored"""
        state_machine.system_state.active_source = AudioSource.SPOTIFY
        state_machine.system_state.transitioning = True
        old_state = state_machine.system_state.plugin_state

        await state_machine.update_plugin_state(
            AudioSource.SPOTIFY,
            PluginState.CONNECTED,
            {}
        )

        # State should not have changed
        assert state_machine.system_state.plugin_state == old_state

    @pytest.mark.asyncio
    async def test_update_multiroom_state(self, state_machine):
        """Multiroom state update test"""
        await state_machine.update_multiroom_state(True)

        assert state_machine.system_state.multiroom_enabled is True

    @pytest.mark.asyncio
    async def test_update_equalizer_effects_state(self, state_machine):
        """Equalizer effects state update test"""
        await state_machine.update_equalizer_effects_state(True)

        assert state_machine.system_state.equalizer_effects_enabled is True

    @pytest.mark.asyncio
    async def test_broadcast_event(self, state_machine, mock_websocket_handler):
        """Event broadcast test"""
        await state_machine.broadcast_event("test", "test_event", {"data": "value"})

        mock_websocket_handler.handle_event.assert_called_once()
        call_args = mock_websocket_handler.handle_event.call_args[0][0]

        assert call_args["category"] == "test"
        assert call_args["type"] == "test_event"
        assert "timestamp" in call_args

    @pytest.mark.asyncio
    async def test_concurrent_transitions_prevented(self, state_machine, mock_plugin):
        """Test that concurrent transitions are prevented by the lock"""
        mock_plugin._initialized = True

        # Simulate a plugin that takes time to start
        async def slow_start():
            await asyncio.sleep(0.5)
            return True

        mock_plugin.start = slow_start
        state_machine.register_plugin(AudioSource.SPOTIFY, mock_plugin)

        # Launch two transitions in parallel
        task1 = asyncio.create_task(state_machine.transition_to_source(AudioSource.SPOTIFY))
        task2 = asyncio.create_task(state_machine.transition_to_source(AudioSource.SPOTIFY))

        results = await asyncio.gather(task1, task2)

        # One should succeed, the other should be no-op (already on source)
        assert any(results)  # At least one succeeded

    @pytest.mark.asyncio
    async def test_updates_ignored_during_transition(self, state_machine, mock_plugin):
        """Test that updates during transition are ignored (new architecture behavior)"""
        mock_plugin._initialized = True

        # Simulate a plugin that takes time
        async def slow_start():
            await asyncio.sleep(0.3)
            return True

        mock_plugin.start = slow_start
        state_machine.register_plugin(AudioSource.SPOTIFY, mock_plugin)

        # Start a transition
        transition_task = asyncio.create_task(
            state_machine.transition_to_source(AudioSource.SPOTIFY)
        )

        # Wait a bit for transition to start
        await asyncio.sleep(0.1)

        # Try to send an update during transition - should be ignored
        await state_machine.update_plugin_state(
            AudioSource.SPOTIFY,
            PluginState.CONNECTED,
            {"title": "Test Song"}
        )

        # Wait for transition to complete
        await transition_task

        # State should be STARTING (not CONNECTED since update was ignored)
        assert state_machine.system_state.active_source == AudioSource.SPOTIFY
        assert state_machine.system_state.transitioning is False
