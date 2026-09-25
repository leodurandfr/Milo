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

from backend.core.models.audio_state import AudioSource
from backend.core.models.audio_wire import SessionView, SourceView
from backend.core.state import AudioStateMachine

from .conftest import WebSocketEventCollector, create_mock_source


def _playing(title: str, session_id: str = "s1") -> SourceView:
    """A source's view with a live session playing `title`."""
    return SourceView(session=SessionView(
        id=session_id, phase="playing", title=title, artist=None, album=None,
        artwork=None, senders=[], duration_ms=None, position=None,
    ))


def _states(collector: WebSocketEventCollector):
    """Every `source/state` payload broadcast, in order."""
    return [e["data"] for e in collector.events
            if e["category"] == "source" and e["type"] == "state"]


class TestTransitionSequence:
    """Tests for and: Transition sequence and state consistency."""

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

        Validates: - active_source corresponds to requested source
        - transitioning is False after completion
        - the service runs, with no session and no error
        """
        sm = state_machine_with_sources

        # Transition to RADIO
        await sm.transition_to_source(AudioSource.RADIO)

        # Verify state consistency
        state = sm.get_current_state()
        assert state["source"] == "radio"
        assert state["switching"] is False
        assert state["service"] == "running"
        assert state["service_error"] is None
        assert state["session"] is None

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
    """Tests for WebSocket events during transitions."""

    @pytest.mark.asyncio
    async def test_websocket_events_during_transition(
        self,
        state_machine_with_sources: AudioStateMachine,
        websocket_collector: WebSocketEventCollector
    ):
        """
        Test WebSocket events are emitted during transitions.

        Validates: - a `source/state` saying the switch started (starting)
        - one saying it is over (running)
        - their origin is the selected source
        """
        sm = state_machine_with_sources
        websocket_collector.clear()

        # Perform transition
        await sm.transition_to_source(AudioSource.RADIO)

        events = websocket_collector.get_events_by_type("state")
        assert [e["origin"] for e in events] == ["radio", "radio"]
        start, complete = (e["data"] for e in events)
        assert (start["source"], start["switching"], start["service"]) == ("radio", True, "starting")
        assert (complete["source"], complete["switching"], complete["service"]) == (
            "radio", False, "running",
        )

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
        Verify the sequence: switching starts, then ends — never the reverse.
        """
        sm = state_machine_with_sources
        websocket_collector.clear()

        await sm.transition_to_source(AudioSource.RADIO)

        switching = [s["switching"] for s in _states(websocket_collector)]
        assert switching[0] is True, "the first state says the switch started"
        assert switching[-1] is False, "the last state says it is over"
        assert switching == sorted(switching, reverse=True), "switching never comes back"


class TestDirectTransition:
    """Tests for Direct transitions between active sources."""

    @pytest.mark.asyncio
    async def test_direct_transition_between_sources(
        self,
        state_machine_with_sources: AudioStateMachine,
        mock_sources
    ):
        """
        Test direct transition from RADIO to SPOTIFY.

        Validates: - Previous source (RADIO) is correctly stopped
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

        # Transition to RADIO and publish a session
        await sm.transition_to_source(AudioSource.RADIO)
        await sm.update_source_view(AudioSource.RADIO, _playing("Test Radio"))

        # Verify the session is on the state
        assert sm.get_current_state()["session"]["title"] == "Test Radio"

        # Transition to SPOTIFY
        await sm.transition_to_source(AudioSource.SPOTIFY)

        # The session must be gone (no leak from RADIO)
        assert sm.get_current_state()["session"] is None

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
        mock_sources[AudioSource.SPOTIFY].start

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
    """Tests for Error cases and recovery."""

    @pytest.mark.asyncio
    async def test_transition_to_unregistered_source(
        self,
        integration_state_machine: AudioStateMachine
    ):
        """
        Transition to an unregistered source is refused, and changes nothing.

        The refusal happens before the state machine commits to the target, so
        neither the selection nor the state moves — a source that failed to be
        created cannot drag the appliance into ERROR by being asked for.
        """
        sm = integration_state_machine
        # No sources registered

        result = await sm.transition_to_source(AudioSource.RADIO)

        assert result is False, "Should fail for unregistered source"
        state = sm.get_current_state()
        assert (state["source"], state["service"]) == ("none", "stopped")

    @pytest.mark.asyncio
    async def test_transition_with_source_start_failure(
        self,
        integration_state_machine: AudioStateMachine,
        websocket_collector: WebSocketEventCollector
    ):
        """
        When source.start fails, the source settles failed and the state that
        says so is broadcast.

        Validates: - source.start fails -> the source stays selected, failed
        - the broadcast state carries the failure
        """
        sm = integration_state_machine

        # Register a failing source
        failing_source = create_mock_source(AudioSource.RADIO, start_success=False)
        sm.register_source(AudioSource.RADIO, failing_source)

        websocket_collector.clear()

        # Attempt transition
        result = await sm.transition_to_source(AudioSource.RADIO)

        assert result is False, "Should fail when source.start() fails"

        # The source stays selected so the failure is visible — and retryable
        state = sm.get_current_state()
        assert (state["source"], state["service"]) == ("radio", "failed")
        assert state["service_error"]["reason"] == "start_failed"

        # The failure reached the wire
        last = _states(websocket_collector)[-1]
        assert (last["service"], last["service_error"]["reason"]) == ("failed", "start_failed")

    @pytest.mark.asyncio
    async def test_transition_timeout(
        self,
        integration_state_machine: AudioStateMachine,
        websocket_collector: WebSocketEventCollector
    ):
        """
        A transition timeout settles the source failed, for `start_timeout`.

        Note: the timeout settles exactly like a failed start — same handler,
        one reason apart — so the source stays selected, failed, and the
        broadcast state says why.
        """
        sm = integration_state_machine
        # Shortened so the test measures the guard, not the wall clock: the
        # production value is sized for a real stop+start and would make this
        # sleep half a minute long.
        sm.TRANSITION_TIMEOUT = 0.1

        # Create a source that takes too long to start
        slow_source = create_mock_source(AudioSource.RADIO)

        async def very_slow_start():
            await asyncio.sleep(sm.TRANSITION_TIMEOUT * 10)
            return True

        slow_source.start = very_slow_start
        sm.register_source(AudioSource.RADIO, slow_source)

        websocket_collector.clear()

        # Attempt transition (should timeout)
        result = await sm.transition_to_source(AudioSource.RADIO)

        assert result is False, "Should fail on timeout"

        # Settled, not reset: the transition is over and the source failed
        state = sm.get_current_state()
        assert state["switching"] is False
        assert (state["source"], state["service"]) == ("radio", "failed")

        failed = [s for s in _states(websocket_collector) if s["service"] == "failed"]
        assert failed, "the timeout reached the wire"
        assert failed[0]["service_error"]["reason"] == "start_timeout"

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

        await asyncio.gather(task1, task2)

        # Both should complete (one waits for the other)
        # Final state should be one of the two sources
        assert sm.system_state.active_source in (AudioSource.RADIO, AudioSource.SPOTIFY)
        assert sm.system_state.transitioning is False

    @pytest.mark.asyncio
    async def test_failed_transition_stops_only_the_target(
        self,
        state_machine_with_sources: AudioStateMachine,
        mock_sources
    ):
        """A failed start stops ONLY the source that failed.

        Regression guard: the unwind used to stop every registered source, which
        ran Bluetooth's unconditional teardown (bluetoothctl + bluealsa/
        bluetooth.service) on a source the transition never touched. The
        previous source is stopped by the transition itself, before the start
        attempt; the target still needs stopping because a start can fail after
        its systemd unit came up.
        """
        sm = state_machine_with_sources
        target = mock_sources[AudioSource.SPOTIFY]
        uninvolved = mock_sources[AudioSource.BLUETOOTH]

        await sm.transition_to_source(AudioSource.RADIO)
        previous = mock_sources[AudioSource.RADIO]
        previous.stop.reset_mock()

        target.start = AsyncMock(return_value=False)
        result = await sm.transition_to_source(AudioSource.SPOTIFY)

        assert result is False
        state = sm.get_current_state()
        assert (state["source"], state["service"]) == ("spotify", "failed")

        target.stop.assert_awaited()
        uninvolved.stop.assert_not_awaited()
        # The previous source is stopped once by the transition, not again by the reset.
        previous.stop.assert_awaited_once()


class TestUpdateSourceViewGuards:
    """Tests for update_source_view's drop guards: updates emitted during a
    transition are dropped (no buffer/replay) and recovered by the post-start
    resync; updates from an inactive source are ignored."""

    @pytest.mark.asyncio
    async def test_in_transition_update_dropped_then_resynced(
        self,
        integration_state_machine: AudioStateMachine,
        websocket_collector: WebSocketEventCollector
    ):
        """
        A publish from a source's start() (while transitioning=True) is
        dropped — never broadcast, no replay queue — but the source's final
        view is recovered by the post-start resync in transition_to_source().
        """
        sm = integration_state_machine
        source_instance = create_mock_source(AudioSource.RADIO)

        async def start_with_update():
            # Published while transitioning=True → dropped, never broadcast.
            await sm.update_source_view(AudioSource.RADIO, _playing("In flight", "s0"))
            # What _do_start actually achieved — recovered by the resync.
            source_instance.view = _playing("Resynced")
            return True

        source_instance.start = start_with_update
        sm.register_source(AudioSource.RADIO, source_instance)
        websocket_collector.clear()

        await sm.transition_to_source(AudioSource.RADIO)

        # The in-transition publish was dropped: no state carried it.
        assert not any(
            (s["session"] or {}).get("title") == "In flight"
            for s in _states(websocket_collector)
        ), "in-transition publish must not be broadcast (no buffer/replay)"

        # ...but the source's post-start view was resynced into the state.
        state = sm.get_current_state()
        assert state["source"] == "radio"
        assert state["switching"] is False
        assert state["session"]["title"] == "Resynced"

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

        # Try to publish from SPOTIFY (inactive)
        await sm.update_source_view(AudioSource.SPOTIFY, _playing("Ignored"))

        # The state must not carry the ignored publish
        state = sm.get_current_state()
        assert state["session"] is None
        assert state["source"] == "radio"
