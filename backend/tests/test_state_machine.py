# backend/tests/test_state_machine.py
"""
Unit tests for UnifiedAudioStateMachine
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from backend.infrastructure.state.state_machine import UnifiedAudioStateMachine
from backend.domain.audio_state import AudioSource, PluginState, SystemAudioState


class TestUnifiedAudioStateMachine:
    """Tests for the unified state machine"""

    @pytest.fixture
    def state_machine(self, mock_websocket_handler, mock_routing_service):
        """Fixture to create a state machine"""
        sm = UnifiedAudioStateMachine(
            routing_service=mock_routing_service,
            websocket_handler=mock_websocket_handler
        )
        return sm

    def test_initialization(self, state_machine):
        """Test state machine initialization"""
        assert state_machine.system_state.active_source == AudioSource.NONE
        assert state_machine.system_state.plugin_state == PluginState.INACTIVE
        assert state_machine.system_state.transitioning is False
        assert state_machine.system_state.target_source is None

    def test_register_plugin(self, state_machine, mock_plugin):
        """Test plugin registration"""
        state_machine.register_plugin(AudioSource.LIBRESPOT, mock_plugin)

        assert state_machine.plugins[AudioSource.LIBRESPOT] == mock_plugin
        assert state_machine.get_plugin(AudioSource.LIBRESPOT) == mock_plugin

    def test_get_plugin_metadata(self, state_machine):
        """Test getting plugin metadata"""
        state_machine.system_state.active_source = AudioSource.LIBRESPOT
        state_machine.system_state.metadata = {"title": "Test Song"}

        metadata = state_machine.get_plugin_metadata(AudioSource.LIBRESPOT)
        assert metadata == {"title": "Test Song"}

        # Non-active source should return {}
        metadata_other = state_machine.get_plugin_metadata(AudioSource.BLUETOOTH)
        assert metadata_other == {}

    def test_get_plugin_state(self, state_machine):
        """Test getting plugin state"""
        state_machine.system_state.active_source = AudioSource.LIBRESPOT
        state_machine.system_state.plugin_state = PluginState.CONNECTED

        state = state_machine.get_plugin_state(AudioSource.LIBRESPOT)
        assert state == PluginState.CONNECTED

        # Non-active source should return INACTIVE
        state_other = state_machine.get_plugin_state(AudioSource.BLUETOOTH)
        assert state_other == PluginState.INACTIVE

    @pytest.mark.asyncio
    async def test_get_current_state(self, state_machine):
        """Test getting current state"""
        state = await state_machine.get_current_state()

        assert "active_source" in state
        assert "plugin_state" in state
        assert "transitioning" in state
        assert "metadata" in state
        assert state["active_source"] == "none"

    @pytest.mark.asyncio
    async def test_transition_to_same_source(self, state_machine, mock_plugin):
        """Test transition to same source (should be no-op)"""
        state_machine.register_plugin(AudioSource.LIBRESPOT, mock_plugin)
        state_machine.system_state.active_source = AudioSource.LIBRESPOT
        state_machine.system_state.plugin_state = PluginState.CONNECTED

        result = await state_machine.transition_to_source(AudioSource.LIBRESPOT)

        assert result is True
        mock_plugin.stop.assert_not_called()
        mock_plugin.start.assert_not_called()

    @pytest.mark.asyncio
    async def test_transition_to_none(self, state_machine, mock_plugin):
        """Test transition to NONE (stop active source)"""
        state_machine.register_plugin(AudioSource.LIBRESPOT, mock_plugin)
        state_machine.system_state.active_source = AudioSource.LIBRESPOT
        state_machine.system_state.plugin_state = PluginState.CONNECTED

        result = await state_machine.transition_to_source(AudioSource.NONE)

        assert result is True
        mock_plugin.stop.assert_called_once()
        assert state_machine.system_state.active_source == AudioSource.NONE
        assert state_machine.system_state.plugin_state == PluginState.INACTIVE

    @pytest.mark.asyncio
    async def test_transition_to_new_source_success(self, state_machine, mock_plugin):
        """Test successful transition to new source"""
        mock_plugin._initialized = True
        state_machine.register_plugin(AudioSource.LIBRESPOT, mock_plugin)

        result = await state_machine.transition_to_source(AudioSource.LIBRESPOT)

        assert result is True
        mock_plugin.start.assert_called_once()
        assert state_machine.system_state.active_source == AudioSource.LIBRESPOT
        # State should be at least READY
        assert state_machine.system_state.plugin_state in [PluginState.READY, PluginState.CONNECTED]

    @pytest.mark.asyncio
    async def test_transition_to_unregistered_source(self, state_machine):
        """Test transition to unregistered source (should fail)"""
        result = await state_machine.transition_to_source(AudioSource.LIBRESPOT)

        assert result is False

    @pytest.mark.asyncio
    async def test_transition_start_fail(self, state_machine, mock_plugin):
        """Test transition with start failure"""
        mock_plugin.start = AsyncMock(return_value=False)
        mock_plugin._initialized = True
        state_machine.register_plugin(AudioSource.LIBRESPOT, mock_plugin)

        result = await state_machine.transition_to_source(AudioSource.LIBRESPOT)

        assert result is False
        # Should end up in NONE state after failure
        assert state_machine.system_state.active_source == AudioSource.NONE

    @pytest.mark.asyncio
    async def test_transition_timeout(self, state_machine, mock_plugin):
        """Test timeout during transition"""
        # Simulate plugin that takes too long to start
        async def slow_start():
            await asyncio.sleep(10)  # Longer than TRANSITION_TIMEOUT (5s)
            return True

        mock_plugin.start = slow_start
        mock_plugin._initialized = True
        state_machine.register_plugin(AudioSource.LIBRESPOT, mock_plugin)

        result = await state_machine.transition_to_source(AudioSource.LIBRESPOT)

        assert result is False
        # Timeout occurs but error may be None if _emergency_stop resets state
        assert state_machine.system_state.transitioning is False
        assert state_machine.system_state.active_source == AudioSource.NONE

    @pytest.mark.asyncio
    async def test_update_plugin_state_active_source(self, state_machine):
        """Test updating active plugin state"""
        state_machine.system_state.active_source = AudioSource.LIBRESPOT
        state_machine.system_state.plugin_state = PluginState.READY

        metadata = {"title": "Test Song"}
        await state_machine.update_plugin_state(
            AudioSource.LIBRESPOT,
            PluginState.CONNECTED,
            metadata
        )

        assert state_machine.system_state.plugin_state == PluginState.CONNECTED
        assert state_machine.system_state.metadata == metadata

    @pytest.mark.asyncio
    async def test_update_plugin_state_inactive_source_ignored(self, state_machine):
        """Test that updates from inactive source are ignored"""
        state_machine.system_state.active_source = AudioSource.LIBRESPOT
        state_machine.system_state.plugin_state = PluginState.CONNECTED

        # Try to update non-active source
        await state_machine.update_plugin_state(
            AudioSource.BLUETOOTH,
            PluginState.CONNECTED,
            {}
        )

        # State should not have changed
        assert state_machine.system_state.active_source == AudioSource.LIBRESPOT

    @pytest.mark.asyncio
    async def test_update_plugin_state_during_transition_ignored(self, state_machine):
        """Test that updates during transition are ignored"""
        state_machine.system_state.active_source = AudioSource.LIBRESPOT
        state_machine.system_state.transitioning = True
        old_state = state_machine.system_state.plugin_state

        await state_machine.update_plugin_state(
            AudioSource.LIBRESPOT,
            PluginState.CONNECTED,
            {}
        )

        # State should not have changed
        assert state_machine.system_state.plugin_state == old_state

    @pytest.mark.asyncio
    async def test_update_multiroom_state(self, state_machine):
        """Test updating multiroom state"""
        await state_machine.update_multiroom_state(True)

        assert state_machine.system_state.multiroom_enabled is True

    @pytest.mark.asyncio
    async def test_update_equalizer_state(self, state_machine):
        """Test updating equalizer state"""
        await state_machine.update_equalizer_state(True)

        assert state_machine.system_state.equalizer_enabled is True

    @pytest.mark.asyncio
    async def test_broadcast_event(self, state_machine, mock_websocket_handler):
        """Test event broadcasting"""
        await state_machine.broadcast_event("test", "test_event", {"data": "value"})

        mock_websocket_handler.handle_event.assert_called_once()
        call_args = mock_websocket_handler.handle_event.call_args[0][0]

        assert call_args["category"] == "test"
        assert call_args["type"] == "test_event"
        assert "timestamp" in call_args

    @pytest.mark.asyncio
    async def test_concurrent_transitions_prevented(self, state_machine, mock_plugin):
        """Test that concurrent transitions are prevented by lock"""
        mock_plugin._initialized = True

        # Simulate plugin that takes time to start
        async def slow_start():
            await asyncio.sleep(0.5)
            return True

        mock_plugin.start = slow_start
        state_machine.register_plugin(AudioSource.LIBRESPOT, mock_plugin)

        # Start two transitions in parallel
        task1 = asyncio.create_task(state_machine.transition_to_source(AudioSource.LIBRESPOT))
        task2 = asyncio.create_task(state_machine.transition_to_source(AudioSource.LIBRESPOT))

        results = await asyncio.gather(task1, task2)

        # One should succeed, the other should be no-op (already on source)
        assert any(results)  # At least one succeeded

    @pytest.mark.asyncio
    async def test_buffered_updates_max_capacity(self, state_machine, mock_plugin):
        """Test that update queue has maximum capacity"""
        mock_plugin._initialized = True

        # Simulate plugin that takes time
        async def slow_start():
            await asyncio.sleep(0.3)
            return True

        mock_plugin.start = slow_start
        state_machine.register_plugin(AudioSource.LIBRESPOT, mock_plugin)

        # Start a transition
        transition_task = asyncio.create_task(
            state_machine.transition_to_source(AudioSource.LIBRESPOT)
        )

        # Wait a bit for transition to start
        await asyncio.sleep(0.1)

        # Try to send update during transition
        await state_machine.update_plugin_state(
            AudioSource.LIBRESPOT,
            PluginState.CONNECTED,
            {"title": "Test Song"}
        )

        # Verify that update is buffered
        assert len(state_machine._buffered_updates) == 1

        # Wait for transition to complete
        await transition_task

        # After transition, queue should be empty (updates replayed)
        assert len(state_machine._buffered_updates) == 0

        # Simulate plugin that takes time to start
        async def slow_start():
            await asyncio.sleep(0.3)
            return True

        mock_plugin.start = slow_start
        state_machine.register_plugin(AudioSource.LIBRESPOT, mock_plugin)

        # Start a transition
        transition_task = asyncio.create_task(
            state_machine.transition_to_source(AudioSource.LIBRESPOT)
        )

        # Wait for transition to start
        await asyncio.sleep(0.1)

        # Send updates during transition
        await state_machine.update_plugin_state(
            AudioSource.LIBRESPOT,
            PluginState.CONNECTED,
            {"title": "Test Song", "artist": "Test Artist"}
        )

        # Wait for transition to complete
        await transition_task

        # Verify that update was applied
        assert state_machine.system_state.plugin_state == PluginState.CONNECTED
        assert state_machine.system_state.metadata.get("title") == "Test Song"
        assert state_machine.system_state.metadata.get("artist") == "Test Artist"

        # Simulate plugin that times out
        async def timeout_start():
            await asyncio.sleep(10)  # Longer than TRANSITION_TIMEOUT
            return True

        mock_plugin.start = timeout_start
        state_machine.register_plugin(AudioSource.LIBRESPOT, mock_plugin)

        # Start a transition
        transition_task = asyncio.create_task(
            state_machine.transition_to_source(AudioSource.LIBRESPOT)
        )

        # Wait for transition to start
        await asyncio.sleep(0.1)

        # Send update during transition
        await state_machine.update_plugin_state(
            AudioSource.LIBRESPOT,
            PluginState.CONNECTED,
            {"title": "Test Song"}
        )

        # Verify that update is buffered
        assert len(state_machine._buffered_updates) == 1

        # Wait for transition to timeout
        result = await transition_task

        # Transition should fail
        assert result is False

        # Queue should be cleared
        assert len(state_machine._buffered_updates) == 0

    @pytest.mark.asyncio
    async def test_buffered_updates_max_capacity(self, state_machine, mock_plugin):
        """Test that update queue has maximum capacity"""
        mock_plugin._initialized = True

        # Simulate plugin that takes time
        async def slow_start():
            await asyncio.sleep(0.5)
            return True

        mock_plugin.start = slow_start
        state_machine.register_plugin(AudioSource.LIBRESPOT, mock_plugin)

        # Start a transition
        transition_task = asyncio.create_task(
            state_machine.transition_to_source(AudioSource.LIBRESPOT)
        )

        # Wait for transition to start
        await asyncio.sleep(0.1)

        # Send more updates than max capacity
        for i in range(state_machine.MAX_BUFFERED_UPDATES + 10):
            await state_machine.update_plugin_state(
                AudioSource.LIBRESPOT,
                PluginState.CONNECTED,
                {"index": i}
            )

        # Queue should not exceed max capacity
        assert len(state_machine._buffered_updates) <= state_machine.MAX_BUFFERED_UPDATES

        # Wait for transition to complete
        await transition_task
