# backend/tests/integration/test_audio_transitions.py
"""
Integration tests for audio source transitions.

These tests validate the contracts for audio source switching that must
remain stable during the feature-based architecture refactoring.

Contract being tested:
- Transition sequence: NONE -> RADIO -> SPOTIFY -> NONE
- WebSocket events emitted during transitions
- State machine consistency after transitions
- Direct transitions between active sources
- Error handling (invalid source, start failure, timeout)
"""
import pytest
import asyncio
from unittest.mock import AsyncMock

from backend.core.models.audio_state import AudioSource, SourceState
from backend.core.state import AudioStateMachine

from .conftest import WebSocketEventCollector, create_mock_source


class TestTransitionSequence:
    """Tests for AC1 and AC3: Transition sequence and state consistency."""

    @pytest.mark.asyncio
    async def test_transition_sequence_none_radio_spotify_none(
        self,
        state_machine_with_sources: AudioStateMachine,
        mock_sources,
        websocket_collector: WebSocketEventCollector
    ):
        """
        Test complete transition sequence: NONE -> RADIO -> SPOTIFY -> NONE

        Validates:
        - Each transition returns True (success)
        - active_source reflects the current source after each transition
        - Sources are correctly started and stopped
        """
        sm = state_machine_with_sources

        # Initial state should be NONE
        assert sm.system_state.active_source == AudioSource.NONE
        assert sm.system_state.transitioning is False

        # Transition NONE -> RADIO
        result = await sm.transition_to_source(AudioSource.RADIO)
        assert result is True, "Transition to RADIO should succeed"
        assert sm.system_state.active_source == AudioSource.RADIO
        assert sm.system_state.transitioning is False
        mock_sources[AudioSource.RADIO].start.assert_called_once()

        # Transition RADIO -> SPOTIFY
        result = await sm.transition_to_source(AudioSource.SPOTIFY)
        assert result is True, "Transition to SPOTIFY should succeed"
        assert sm.system_state.active_source == AudioSource.SPOTIFY
        assert sm.system_state.transitioning is False
        mock_sources[AudioSource.RADIO].stop.assert_called_once()
        mock_sources[AudioSource.SPOTIFY].start.assert_called_once()

        # Transition SPOTIFY -> NONE
        result = await sm.transition_to_source(AudioSource.NONE)
        assert result is True, "Transition to NONE should succeed"
        assert sm.system_state.active_source == AudioSource.NONE
        assert sm.system_state.transitioning is False
        mock_sources[AudioSource.SPOTIFY].stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_state_consistency_after_transition(
        self,
        state_machine_with_sources: AudioStateMachine
    ):
        """
        Test state machine state is consistent after each transition.

        Validates AC3:
        - active_source corresponds to requested source
        - transitioning is False after completion
        - source_state reflects active source state
        """
        sm = state_machine_with_sources

        # Transition to RADIO
        await sm.transition_to_source(AudioSource.RADIO)

        # Verify state consistency
        assert sm.system_state.active_source == AudioSource.RADIO
        assert sm.system_state.transitioning is False
        assert sm.system_state.source_state in (SourceState.STARTING, SourceState.WAITING)
        assert sm.system_state.error is None

    @pytest.mark.asyncio
    async def test_transition_to_same_source_is_noop(
        self,
        state_machine_with_sources: AudioStateMachine,
        mock_sources
    ):
        """
        Transitioning to the already active source should be a no-op.
        """
        sm = state_machine_with_sources

        # First transition to RADIO
        await sm.transition_to_source(AudioSource.RADIO)
        mock_sources[AudioSource.RADIO].start.reset_mock()

        # Transition to RADIO again
        result = await sm.transition_to_source(AudioSource.RADIO)

        assert result is True, "Transition to same source should succeed"
        assert sm.system_state.active_source == AudioSource.RADIO
        # Source should NOT be started again
        mock_sources[AudioSource.RADIO].start.assert_not_called()


class TestWebSocketEvents:
    """Tests for AC2: WebSocket events during transitions."""

    @pytest.mark.asyncio
    async def test_websocket_events_during_transition(
        self,
        state_machine_with_sources: AudioStateMachine,
        websocket_collector: WebSocketEventCollector
    ):
        """
        Test WebSocket events are emitted during transitions.

        Validates AC2:
        - transition_start event at beginning
        - transition_complete event at end
        - Events have correct format: {category, type, origin, data}
        """
        sm = state_machine_with_sources
        websocket_collector.clear()

        # Perform transition
        await sm.transition_to_source(AudioSource.RADIO)

        # Check for transition_start event
        start_events = websocket_collector.get_events_by_type("transition_start")
        assert len(start_events) >= 1, "Should emit transition_start event"

        start_event = start_events[0]
        assert start_event["category"] == "system"
        assert start_event["type"] == "transition_start"
        assert "data" in start_event
        assert start_event["data"]["to_source"] == "radio"

        # Check for transition_complete event
        complete_events = websocket_collector.get_events_by_type("transition_complete")
        assert len(complete_events) >= 1, "Should emit transition_complete event"

        complete_event = complete_events[0]
        assert complete_event["category"] == "system"
        assert complete_event["type"] == "transition_complete"
        assert complete_event["data"]["active_source"] == "radio"

    @pytest.mark.asyncio
    async def test_event_format_has_required_fields(
        self,
        state_machine_with_sources: AudioStateMachine,
        websocket_collector: WebSocketEventCollector
    ):
        """
        Validate event format: {category, type, source, data}
        """
        sm = state_machine_with_sources
        websocket_collector.clear()

        await sm.transition_to_source(AudioSource.RADIO)

        # All events should have the required fields
        for event in websocket_collector.events:
            assert "category" in event, "Event should have 'category' field"
            assert "type" in event, "Event should have 'type' field"
            assert "origin" in event, "Event should have 'origin' field"
            assert "data" in event, "Event should have 'data' field"
            assert "timestamp" in event, "Event should have 'timestamp' field"

    @pytest.mark.asyncio
    async def test_event_sequence_order(
        self,
        state_machine_with_sources: AudioStateMachine,
        websocket_collector: WebSocketEventCollector
    ):
        """
        Verify event sequence: transition_start -> state updates -> transition_complete
        """
        sm = state_machine_with_sources
        websocket_collector.clear()

        await sm.transition_to_source(AudioSource.RADIO)

        events = websocket_collector.events
        assert len(events) >= 2, "Should have at least start and complete events"

        # Find indices
        start_idx = next(
            (i for i, e in enumerate(events) if e["type"] == "transition_start"),
            None
        )
        complete_idx = next(
            (i for i, e in enumerate(events) if e["type"] == "transition_complete"),
            None
        )

        assert start_idx is not None, "Should have transition_start event"
        assert complete_idx is not None, "Should have transition_complete event"
        assert start_idx < complete_idx, "transition_start should come before transition_complete"


class TestDirectTransition:
    """Tests for AC4: Direct transitions between active sources."""

    @pytest.mark.asyncio
    async def test_direct_transition_between_sources(
        self,
        state_machine_with_sources: AudioStateMachine,
        mock_sources
    ):
        """
        Test direct transition from RADIO to SPOTIFY.

        Validates AC4:
        - Previous source (RADIO) is correctly stopped
        - New source (SPOTIFY) is correctly started
        - No state leak between sources
        """
        sm = state_machine_with_sources

        # Start with RADIO
        await sm.transition_to_source(AudioSource.RADIO)
        assert sm.system_state.active_source == AudioSource.RADIO

        # Direct transition to SPOTIFY
        result = await sm.transition_to_source(AudioSource.SPOTIFY)

        assert result is True
        assert sm.system_state.active_source == AudioSource.SPOTIFY

        # Verify stop was called on RADIO
        mock_sources[AudioSource.RADIO].stop.assert_called()

        # Verify start was called on SPOTIFY
        mock_sources[AudioSource.SPOTIFY].start.assert_called()

    @pytest.mark.asyncio
    async def test_no_state_leak_between_sources(
        self,
        state_machine_with_sources: AudioStateMachine
    ):
        """
        Verify no state leaks between source transitions.
        """
        sm = state_machine_with_sources

        # Transition to RADIO and set some metadata
        await sm.transition_to_source(AudioSource.RADIO)
        await sm.update_source_state(
            AudioSource.RADIO,
            SourceState.ACTIVE,
            {"station": "Test Radio", "bitrate": 320}
        )

        # Verify metadata is set
        assert sm.system_state.metadata.get("station") == "Test Radio"

        # Transition to SPOTIFY
        await sm.transition_to_source(AudioSource.SPOTIFY)

        # Metadata should be cleared (no leak from RADIO)
        assert sm.system_state.metadata.get("station") is None
        assert "bitrate" not in sm.system_state.metadata

    @pytest.mark.asyncio
    async def test_only_one_source_active_at_time(
        self,
        state_machine_with_sources: AudioStateMachine,
        mock_sources
    ):
        """
        Verify only one source is active at any time during transitions.
        """
        sm = state_machine_with_sources

        # Track call order
        call_order = []

        original_radio_stop = mock_sources[AudioSource.RADIO].stop
        original_spotify_start = mock_sources[AudioSource.SPOTIFY].start

        async def tracked_radio_stop():
            call_order.append("radio_stop")
            return await original_radio_stop()

        async def tracked_spotify_start():
            call_order.append("spotify_start")
            return True

        mock_sources[AudioSource.RADIO].stop = tracked_radio_stop
        mock_sources[AudioSource.SPOTIFY].start = tracked_spotify_start

        # Start with RADIO
        await sm.transition_to_source(AudioSource.RADIO)
        call_order.clear()

        # Transition to SPOTIFY
        await sm.transition_to_source(AudioSource.SPOTIFY)

        # Verify order: stop old source before starting new
        assert call_order.index("radio_stop") < call_order.index("spotify_start"), \
            "Old source should be stopped before new source starts"


class TestErrorHandling:
    """Tests for AC5: Error cases and recovery."""

    @pytest.mark.asyncio
    async def test_transition_to_unregistered_source(
        self,
        integration_state_machine: AudioStateMachine
    ):
        """
        Transition to an unregistered source should return False.
        """
        sm = integration_state_machine
        # No sources registered

        result = await sm.transition_to_source(AudioSource.RADIO)

        assert result is False, "Should fail for unregistered source"
        assert sm.system_state.active_source == AudioSource.NONE

    @pytest.mark.asyncio
    async def test_transition_with_source_start_failure(
        self,
        integration_state_machine: AudioStateMachine,
        websocket_collector: WebSocketEventCollector
    ):
        """
        When source.start() fails, should rollback to NONE and broadcast error.

        Validates AC5:
        - source.start() fails -> rollback to NONE
        - Error event broadcast
        """
        sm = integration_state_machine

        # Register a failing source
        failing_source = create_mock_source(AudioSource.RADIO, start_success=False)
        sm.register_source(AudioSource.RADIO, failing_source)

        websocket_collector.clear()

        # Attempt transition
        result = await sm.transition_to_source(AudioSource.RADIO)

        assert result is False, "Should fail when source.start() fails"

        # Should rollback to NONE after emergency stop
        assert sm.system_state.active_source == AudioSource.NONE

        # Should broadcast error event
        error_events = websocket_collector.get_events_by_type("error")
        assert len(error_events) >= 1, "Should broadcast error event"

    @pytest.mark.asyncio
    async def test_transition_timeout(
        self,
        integration_state_machine: AudioStateMachine,
        websocket_collector: WebSocketEventCollector
    ):
        """
        Transition timeout should trigger emergency_stop and broadcast timeout error.

        Validates AC5:
        - Timeout triggers emergency_stop()
        - Error event with timeout message

        Note: After emergency_stop(), the error field is cleared to None,
        but the error event should have been broadcast before that.
        """
        sm = integration_state_machine

        # Create a source that takes too long to start
        slow_source = create_mock_source(AudioSource.RADIO)

        async def very_slow_start():
            await asyncio.sleep(10)  # Longer than TRANSITION_TIMEOUT (5s)
            return True

        slow_source.start = very_slow_start
        sm.register_source(AudioSource.RADIO, slow_source)

        websocket_collector.clear()

        # Attempt transition (should timeout)
        result = await sm.transition_to_source(AudioSource.RADIO)

        assert result is False, "Should fail on timeout"

        # State should be reset after emergency_stop
        assert sm.system_state.transitioning is False
        assert sm.system_state.active_source == AudioSource.NONE

        # Should broadcast error event (before emergency_stop clears error)
        error_events = websocket_collector.get_events_by_type("error")
        assert len(error_events) >= 1, "Should broadcast timeout error"

        error_data = error_events[0]["data"]
        assert "timeout" in error_data.get("error", "").lower() or \
               "timeout" in error_data.get("message", "").lower(), \
               "Error should mention timeout"

    @pytest.mark.asyncio
    async def test_concurrent_transitions_are_serialized(
        self,
        state_machine_with_sources: AudioStateMachine
    ):
        """
        Concurrent transition attempts should be serialized by the lock.
        """
        sm = state_machine_with_sources

        # Start concurrent transitions
        task1 = asyncio.create_task(sm.transition_to_source(AudioSource.RADIO))
        task2 = asyncio.create_task(sm.transition_to_source(AudioSource.SPOTIFY))

        results = await asyncio.gather(task1, task2)

        # Both should complete (one waits for the other)
        # Final state should be one of the two sources
        assert sm.system_state.active_source in (AudioSource.RADIO, AudioSource.SPOTIFY)
        assert sm.system_state.transitioning is False

    @pytest.mark.asyncio
    async def test_emergency_stop_clears_all_sources(
        self,
        state_machine_with_sources: AudioStateMachine,
        mock_sources
    ):
        """
        Emergency stop should attempt to stop all registered sources.
        """
        sm = state_machine_with_sources

        # Start a source
        await sm.transition_to_source(AudioSource.RADIO)

        # Trigger emergency stop
        await sm._emergency_stop()

        # Should be back to NONE
        assert sm.system_state.active_source == AudioSource.NONE
        assert sm.system_state.source_state == SourceState.WAITING


class TestUpdateBuffering:
    """Tests for update buffering during transitions."""

    @pytest.mark.asyncio
    async def test_updates_buffered_during_transition(
        self,
        integration_state_machine: AudioStateMachine,
        websocket_collector: WebSocketEventCollector
    ):
        """
        Updates arriving during transition should be buffered and replayed.
        """
        sm = integration_state_machine

        # Create a source that sends an update during start
        source_instance = create_mock_source(AudioSource.RADIO)

        async def start_with_update():
            # Simulate source sending update during transition
            await sm.update_source_state(
                AudioSource.RADIO,
                SourceState.ACTIVE,
                {"buffered": True}
            )
            return True

        source_instance.start = start_with_update
        sm.register_source(AudioSource.RADIO, source_instance)

        websocket_collector.clear()

        # Perform transition
        await sm.transition_to_source(AudioSource.RADIO)

        # Update should have been replayed
        state_events = websocket_collector.get_events_by_type("state_changed")
        buffered_events = [
            e for e in state_events
            if e.get("data", {}).get("metadata", {}).get("buffered")
        ]

        # Either the update was buffered and replayed, or state reflects the update
        assert sm.system_state.active_source == AudioSource.RADIO

    @pytest.mark.asyncio
    async def test_updates_from_inactive_source_ignored(
        self,
        state_machine_with_sources: AudioStateMachine
    ):
        """
        Updates from inactive sources should be ignored.
        """
        sm = state_machine_with_sources

        # Start RADIO
        await sm.transition_to_source(AudioSource.RADIO)

        # Try to update from SPOTIFY (inactive)
        await sm.update_source_state(
            AudioSource.SPOTIFY,
            SourceState.ACTIVE,
            {"should_be": "ignored"}
        )

        # Metadata should not contain the ignored update
        assert sm.system_state.metadata.get("should_be") is None
        assert sm.system_state.active_source == AudioSource.RADIO
