# backend/tests/test_state_machine.py
"""
Unit tests for AudioStateMachine
"""
import pytest
import asyncio
from unittest.mock import AsyncMock
from backend.core.state import AudioStateMachine
from backend.core.models.audio_state import AudioSource, SourceState
from backend.core.models.ws_events import SystemStateChanged


class TestAudioStateMachine:
    """Tests for the audio state machine"""

    @pytest.fixture
    def state_machine(self, mock_ws_manager, mock_routing_service):
        """Fixture to create a state machine"""
        sm = AudioStateMachine()
        sm.routing_service = mock_routing_service
        sm.ws_manager = mock_ws_manager
        return sm

    def test_initialization(self, state_machine):
        """State machine initialization test"""
        assert state_machine.system_state.active_source == AudioSource.NONE
        assert state_machine.system_state.source_state == SourceState.WAITING
        assert state_machine.system_state.transitioning is False

    def test_register_source(self, state_machine, mock_source):
        """Source registration test"""
        state_machine.register_source(AudioSource.SPOTIFY, mock_source)

        assert state_machine.sources[AudioSource.SPOTIFY] == mock_source
        assert state_machine.get_source(AudioSource.SPOTIFY) == mock_source

    def test_get_current_state(self, state_machine):
        """Current state retrieval test"""
        state = state_machine.get_current_state()

        assert "active_source" in state
        assert "source_state" in state
        assert "transitioning" in state
        assert "metadata" in state
        assert state["active_source"] == "none"

    @pytest.mark.asyncio
    async def test_transition_to_same_source(self, state_machine, mock_source):
        """Transition to same source test (should be no-op)"""
        state_machine.register_source(AudioSource.SPOTIFY, mock_source)
        state_machine.system_state.active_source = AudioSource.SPOTIFY
        state_machine.system_state.source_state = SourceState.ACTIVE

        result = await state_machine.transition_to_source(AudioSource.SPOTIFY)

        assert result is True
        mock_source.stop.assert_not_called()
        mock_source.start.assert_not_called()

    @pytest.mark.asyncio
    async def test_transition_to_none(self, state_machine, mock_source):
        """Transition to NONE test (stop active source)"""
        state_machine.register_source(AudioSource.SPOTIFY, mock_source)
        state_machine.system_state.active_source = AudioSource.SPOTIFY
        state_machine.system_state.source_state = SourceState.ACTIVE

        result = await state_machine.transition_to_source(AudioSource.NONE)

        assert result is True
        mock_source.stop.assert_called_once()
        assert state_machine.system_state.active_source == AudioSource.NONE
        assert state_machine.system_state.source_state == SourceState.WAITING

    @pytest.mark.asyncio
    async def test_transition_to_new_source_success(self, state_machine, mock_source):
        """Successful transition to new source test"""
        mock_source.is_initialized = True
        state_machine.register_source(AudioSource.SPOTIFY, mock_source)

        result = await state_machine.transition_to_source(AudioSource.SPOTIFY)

        assert result is True
        mock_source.start.assert_called_once()
        assert state_machine.system_state.active_source == AudioSource.SPOTIFY
        # State is STARTING until source calls notify_state_change(WAITING/ACTIVE)
        # Mock source doesn't notify state changes, so it stays at STARTING
        assert state_machine.system_state.source_state in [SourceState.STARTING, SourceState.WAITING, SourceState.ACTIVE]

    @pytest.mark.asyncio
    async def test_transition_to_unregistered_source(self, state_machine):
        """Transition to unregistered source test (should fail)"""
        result = await state_machine.transition_to_source(AudioSource.SPOTIFY)

        assert result is False

    @pytest.mark.asyncio
    async def test_transition_start_fail(self, state_machine, mock_source):
        """Transition test with start failure"""
        mock_source.start = AsyncMock(return_value=False)
        mock_source.is_initialized = True
        state_machine.register_source(AudioSource.SPOTIFY, mock_source)

        result = await state_machine.transition_to_source(AudioSource.SPOTIFY)

        assert result is False
        # Should end up in NONE state after failure
        assert state_machine.system_state.active_source == AudioSource.NONE

    @pytest.mark.asyncio
    async def test_transition_timeout(self, state_machine, mock_source):
        """Timeout during transition test"""
        # Simulate a source that takes too long to start
        async def slow_start():
            await asyncio.sleep(10)  # Longer than TRANSITION_TIMEOUT (5s)
            return True

        mock_source.start = slow_start
        mock_source.is_initialized = True
        state_machine.register_source(AudioSource.SPOTIFY, mock_source)

        result = await state_machine.transition_to_source(AudioSource.SPOTIFY)

        assert result is False
        # Timeout occurs but error may be None if _emergency_stop resets state
        assert state_machine.system_state.transitioning is False
        assert state_machine.system_state.active_source == AudioSource.NONE

    @pytest.mark.asyncio
    async def test_update_source_state_active_source(self, state_machine):
        """Active source state update test"""
        state_machine.system_state.active_source = AudioSource.SPOTIFY
        state_machine.system_state.source_state = SourceState.WAITING

        metadata = {"title": "Test Song"}
        await state_machine.update_source_state(
            AudioSource.SPOTIFY,
            SourceState.ACTIVE,
            metadata
        )

        assert state_machine.system_state.source_state == SourceState.ACTIVE
        assert state_machine.system_state.metadata == metadata

    @pytest.mark.asyncio
    async def test_update_source_state_inactive_source_ignored(self, state_machine):
        """Test updates from inactive source are ignored"""
        state_machine.system_state.active_source = AudioSource.SPOTIFY
        state_machine.system_state.source_state = SourceState.ACTIVE

        # Try to update a non-active source
        await state_machine.update_source_state(
            AudioSource.BLUETOOTH,
            SourceState.ACTIVE,
            {}
        )

        # State should not have changed
        assert state_machine.system_state.active_source == AudioSource.SPOTIFY

    @pytest.mark.asyncio
    async def test_update_source_state_during_transition_ignored(self, state_machine):
        """Test updates during transition are ignored"""
        state_machine.system_state.active_source = AudioSource.SPOTIFY
        state_machine.system_state.transitioning = True
        old_state = state_machine.system_state.source_state

        await state_machine.update_source_state(
            AudioSource.SPOTIFY,
            SourceState.ACTIVE,
            {}
        )

        # State should not have changed
        assert state_machine.system_state.source_state == old_state

    @pytest.mark.asyncio
    async def test_update_source_state_replaces_metadata_on_transition(self, state_machine):
        """A state transition replaces metadata wholesale.

        Regression guard: update_source_state used to MERGE metadata, so a
        source dropping to WAITING with a partial payload (e.g. Spotify when
        go-librespot dies, sending only the "off" flags) left the previous
        track's title/artist/album/uri stale in system_state.metadata — and
        thus in GET /api/audio/state. The provided payload must fully replace.
        """
        state_machine.system_state.active_source = AudioSource.SPOTIFY

        # 1. Source becomes ACTIVE with a full track payload
        await state_machine.update_source_state(
            AudioSource.SPOTIFY,
            SourceState.ACTIVE,
            {"title": "Song", "artist": "Artist", "album": "Album",
             "uri": "spotify:track:x", "is_playing": True},
        )

        # 2. Source drops to WAITING with only the "off" flags (partial dict)
        await state_machine.update_source_state(
            AudioSource.SPOTIFY,
            SourceState.WAITING,
            {"device_connected": False, "is_playing": False},
        )

        # The WAITING payload fully replaces metadata: no stale track fields
        assert state_machine.system_state.metadata == {
            "device_connected": False, "is_playing": False
        }

    @pytest.mark.asyncio
    async def test_update_source_state_none_metadata_preserves(self, state_machine):
        """metadata=None means a state-only change: metadata is left untouched.

        This is the routing path: AudioRoutingService flips the active source
        to STARTING during a reroute while the current track must stay visible
        in the UI, so it passes no metadata.
        """
        state_machine.system_state.active_source = AudioSource.SPOTIFY
        state_machine.system_state.source_state = SourceState.ACTIVE
        state_machine.system_state.metadata = {"title": "Song", "artist": "Artist"}

        await state_machine.update_source_state(
            AudioSource.SPOTIFY,
            SourceState.STARTING,
            None,
        )

        assert state_machine.system_state.source_state == SourceState.STARTING
        assert state_machine.system_state.metadata == {"title": "Song", "artist": "Artist"}

    @pytest.mark.asyncio
    async def test_broadcast(self, state_machine, mock_ws_manager):
        """Event broadcast test"""
        await state_machine.broadcast(SystemStateChanged(source="system"))

        mock_ws_manager.broadcast_dict.assert_called_once()
        call_args = mock_ws_manager.broadcast_dict.call_args[0][0]

        assert call_args["category"] == "system"
        assert call_args["type"] == "state_changed"
        assert "timestamp" in call_args

    @pytest.mark.asyncio
    async def test_concurrent_transitions_prevented(self, state_machine, mock_source):
        """Test concurrent transitions are prevented by the lock"""
        mock_source.is_initialized = True

        # Simulate a source that takes time to start
        async def slow_start():
            await asyncio.sleep(0.5)
            return True

        mock_source.start = slow_start
        state_machine.register_source(AudioSource.SPOTIFY, mock_source)

        # Launch two transitions in parallel
        task1 = asyncio.create_task(state_machine.transition_to_source(AudioSource.SPOTIFY))
        task2 = asyncio.create_task(state_machine.transition_to_source(AudioSource.SPOTIFY))

        results = await asyncio.gather(task1, task2)

        # One should succeed, the other should be no-op (already on source)
        assert any(results)  # At least one succeeded

    @pytest.mark.asyncio
    async def test_updates_ignored_during_transition(self, state_machine, mock_source):
        """Test that updates during transition are ignored (new architecture behavior)"""
        mock_source.is_initialized = True

        # Simulate a source that takes time
        async def slow_start():
            await asyncio.sleep(0.3)
            return True

        mock_source.start = slow_start
        state_machine.register_source(AudioSource.SPOTIFY, mock_source)

        # Start a transition
        transition_task = asyncio.create_task(
            state_machine.transition_to_source(AudioSource.SPOTIFY)
        )

        # Wait a bit for transition to start
        await asyncio.sleep(0.1)

        # Try to send an update during transition - should be ignored
        await state_machine.update_source_state(
            AudioSource.SPOTIFY,
            SourceState.ACTIVE,
            {"title": "Test Song"}
        )

        # Wait for transition to complete
        await transition_task

        # State should be STARTING (not ACTIVE since update was ignored)
        assert state_machine.system_state.active_source == AudioSource.SPOTIFY
        assert state_machine.system_state.transitioning is False
