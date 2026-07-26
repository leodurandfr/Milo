# backend/tests/test_audio_state_machine.py
"""
Unit tests for AudioStateMachine — the single source of truth.

Tests cover:
- Source registration, activation, deactivation and direct source-to-source switch
- The update rules: an inactive source is ignored, an update during a transition
  is dropped, a state change replaces metadata while metadata=None preserves it
- Failure unwind: a clean False from start(), a raising start(), and the timeout
- The transition lock
- WebSocket broadcasting, and which categories carry full_state
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock

from backend.core.state import AudioStateMachine
from backend.core.models.audio_state import AudioSource, SourceState
from backend.core.models.ws_events import SourceStateChanged, SystemErrorEvent, VolumeChanged


@pytest.fixture
def state_machine():
    """Create AudioStateMachine."""
    return AudioStateMachine()


@pytest.fixture
def mock_source():
    """Create a mock audio source."""
    source = Mock()
    source.initialize = AsyncMock(return_value=True)
    source.start = AsyncMock(return_value=True)
    source.stop = AsyncMock(return_value=True)
    source.is_initialized = False
    source.state = SourceState.WAITING
    source.metadata = {}
    return source


class TestAudioStateMachineBasics:
    """Test basic state machine operations."""

    def test_initial_state(self, state_machine):
        """Test initial state is NONE with WAITING."""
        state = state_machine.get_current_state()
        assert state["active_source"] == "none"
        assert state["source_state"] == "waiting"
        assert state["transitioning"] is False
        assert state["error"] is None

    def test_register_source(self, state_machine, mock_source):
        """Test source registration."""
        state_machine.register_source(AudioSource.RADIO, mock_source)

        assert state_machine.get_source(AudioSource.RADIO) is mock_source
        assert state_machine.get_source(AudioSource.SPOTIFY) is None


class TestSourceActivation:
    """Test source activation and deactivation."""

    @pytest.mark.asyncio
    async def test_transition_to_source(self, state_machine, mock_source):
        """Test transitioning to a source."""
        state_machine.register_source(AudioSource.RADIO, mock_source)

        result = await state_machine.transition_to_source(AudioSource.RADIO)

        assert result is True
        assert state_machine.system_state.active_source == AudioSource.RADIO
        mock_source.initialize.assert_called_once()
        mock_source.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_transition_to_unregistered_source(self, state_machine):
        """Test transitioning to an unregistered source fails."""
        result = await state_machine.transition_to_source(AudioSource.RADIO)

        assert result is False
        assert state_machine.system_state.active_source == AudioSource.NONE

    @pytest.mark.asyncio
    async def test_transition_to_none(self, state_machine, mock_source):
        """Test transitioning to NONE stops the active source."""
        state_machine.register_source(AudioSource.RADIO, mock_source)
        await state_machine.transition_to_source(AudioSource.RADIO)

        result = await state_machine.transition_to_source(AudioSource.NONE)

        assert result is True
        assert state_machine.system_state.active_source == AudioSource.NONE
        mock_source.stop.assert_called()

    @pytest.mark.asyncio
    async def test_already_active_source(self, state_machine, mock_source):
        """Test activating already active source returns True."""
        state_machine.register_source(AudioSource.RADIO, mock_source)
        await state_machine.transition_to_source(AudioSource.RADIO)
        mock_source.reset_mock()

        result = await state_machine.transition_to_source(AudioSource.RADIO)

        assert result is True
        # Should not call start again
        mock_source.start.assert_not_called()


class TestDirectTransition:
    """Test direct transition between two sources."""

    @pytest.mark.asyncio
    async def test_direct_transition(self, state_machine):
        """Test switching directly from one source to another."""
        mock_radio = Mock()
        mock_radio.initialize = AsyncMock(return_value=True)
        mock_radio.start = AsyncMock(return_value=True)
        mock_radio.stop = AsyncMock(return_value=True)
        mock_radio.is_initialized = False

        mock_spotify = Mock()
        mock_spotify.initialize = AsyncMock(return_value=True)
        mock_spotify.start = AsyncMock(return_value=True)
        mock_spotify.stop = AsyncMock(return_value=True)
        mock_spotify.is_initialized = False

        state_machine.register_source(AudioSource.RADIO, mock_radio)
        state_machine.register_source(AudioSource.SPOTIFY, mock_spotify)

        # Start with radio
        await state_machine.transition_to_source(AudioSource.RADIO)
        assert state_machine.system_state.active_source == AudioSource.RADIO

        # Switch to spotify
        await state_machine.transition_to_source(AudioSource.SPOTIFY)

        assert state_machine.system_state.active_source == AudioSource.SPOTIFY
        mock_radio.stop.assert_called()
        mock_spotify.start.assert_called()


class TestSourceStateUpdate:
    """Test source state updates."""

    @pytest.mark.asyncio
    async def test_update_source_state(self, state_machine, mock_source):
        """Test updating source state."""
        state_machine.register_source(AudioSource.RADIO, mock_source)
        await state_machine.transition_to_source(AudioSource.RADIO)

        await state_machine.update_source_state(
            AudioSource.RADIO,
            SourceState.ACTIVE,
            {"title": "Test Station"}
        )

        assert state_machine.system_state.source_state == SourceState.ACTIVE
        assert state_machine.system_state.metadata["title"] == "Test Station"

    @pytest.mark.asyncio
    async def test_update_source_state_inactive_source(self, state_machine, mock_source):
        """Test updating inactive source state is ignored."""
        state_machine.register_source(AudioSource.RADIO, mock_source)
        await state_machine.transition_to_source(AudioSource.RADIO)

        # Try to update spotify while radio is active
        await state_machine.update_source_state(
            AudioSource.SPOTIFY,
            SourceState.ACTIVE,
            {}
        )

        # Should still be starting (from activation), not connected
        assert state_machine.system_state.source_state != SourceState.ACTIVE

    @pytest.mark.asyncio
    async def test_update_source_state_error(self, state_machine, mock_source):
        """Test updating source state to ERROR."""
        state_machine.register_source(AudioSource.RADIO, mock_source)
        await state_machine.transition_to_source(AudioSource.RADIO)

        await state_machine.update_source_state(
            AudioSource.RADIO,
            SourceState.ERROR,
            {"error": "Connection failed"}
        )

        assert state_machine.system_state.source_state == SourceState.ERROR
        assert state_machine.system_state.error == "Connection failed"

    @pytest.mark.asyncio
    async def test_update_during_transition_is_dropped(self, state_machine):
        """Updates arriving while `transitioning` is set are dropped, not buffered.

        There is no replay queue: the post-start resync re-reads source.state
        instead. An update that slipped through here would let a source's
        pre-transition state overwrite the one being transitioned to.
        """
        state_machine.system_state.active_source = AudioSource.SPOTIFY
        state_machine.system_state.transitioning = True
        before = state_machine.system_state.source_state

        await state_machine.update_source_state(
            AudioSource.SPOTIFY, SourceState.ACTIVE, {}
        )

        assert state_machine.system_state.source_state == before

    @pytest.mark.asyncio
    async def test_state_change_replaces_metadata_wholesale(self, state_machine):
        """A state transition replaces metadata, it does not merge it.

        Regression guard: update_source_state used to MERGE, so a source
        dropping to WAITING with a partial payload (Spotify when go-librespot
        dies, sending only the "off" flags) left the previous track's
        title/artist/album/uri stale in system_state.metadata — and so in
        GET /api/audio/state.
        """
        state_machine.system_state.active_source = AudioSource.SPOTIFY

        await state_machine.update_source_state(
            AudioSource.SPOTIFY,
            SourceState.ACTIVE,
            {"title": "Song", "artist": "Artist", "album": "Album",
             "uri": "spotify:track:x", "is_playing": True},
        )
        await state_machine.update_source_state(
            AudioSource.SPOTIFY,
            SourceState.WAITING,
            {"device_connected": False, "is_playing": False},
        )

        assert state_machine.system_state.metadata == {
            "device_connected": False, "is_playing": False
        }

    @pytest.mark.asyncio
    async def test_none_metadata_leaves_the_previous_payload(self, state_machine):
        """metadata=None is a state-only change: metadata is left untouched.

        This is the routing path — AudioRoutingService flips the active source
        to STARTING during a reroute while the current track must stay visible
        in the UI, so it passes no metadata.
        """
        state_machine.system_state.active_source = AudioSource.SPOTIFY
        state_machine.system_state.source_state = SourceState.ACTIVE
        state_machine.system_state.metadata = {"title": "Song", "artist": "Artist"}

        await state_machine.update_source_state(
            AudioSource.SPOTIFY, SourceState.STARTING, None
        )

        assert state_machine.system_state.source_state == SourceState.STARTING
        assert state_machine.system_state.metadata == {"title": "Song", "artist": "Artist"}


class TestWebSocketBroadcasting:
    """Test WebSocket broadcasting via ws_manager."""

    @pytest.mark.asyncio
    async def test_broadcast_with_ws_manager(self, state_machine):
        """Test broadcast calls ws_manager.broadcast_dict."""
        mock_manager = Mock()
        mock_manager.broadcast_dict = AsyncMock()
        state_machine.ws_manager = mock_manager

        await state_machine.broadcast(SystemErrorEvent(source="radio", error="boom", message="Boom"))

        mock_manager.broadcast_dict.assert_called_once()
        call_args = mock_manager.broadcast_dict.call_args[0][0]
        assert call_args["category"] == "system"
        assert call_args["type"] == "error"
        assert call_args["data"]["message"] == "Boom"

    @pytest.mark.asyncio
    async def test_broadcast_without_ws_manager(self, state_machine):
        """Test broadcast works without ws_manager."""
        # Should not raise
        await state_machine.broadcast(SystemErrorEvent(source="radio", error="boom", message="Boom"))

    @pytest.mark.asyncio
    async def test_broadcast_includes_full_state_for_source(self, state_machine):
        """Test source events include full_state."""
        mock_manager = Mock()
        mock_manager.broadcast_dict = AsyncMock()
        state_machine.ws_manager = mock_manager

        await state_machine.broadcast(SourceStateChanged(source="radio", new_state="active"))

        call_args = mock_manager.broadcast_dict.call_args[0][0]
        assert "full_state" in call_args["data"]

    @pytest.mark.asyncio
    async def test_broadcast_excludes_full_state_for_volume(self, state_machine):
        """Test volume events do not include full_state."""
        mock_manager = Mock()
        mock_manager.broadcast_dict = AsyncMock()
        state_machine.ws_manager = mock_manager

        await state_machine.broadcast(VolumeChanged(
            show_bar=True, step_mobile_db=3.0, multiroom_enabled=False, state={}
        ))

        call_args = mock_manager.broadcast_dict.call_args[0][0]
        assert "full_state" not in call_args["data"]

    @pytest.mark.asyncio
    async def test_broadcast_full_state_aggregates_flags_from_services(self, state_machine):
        """Source/system events must merge multiroom_enabled and
        equalizer_effects_enabled into full_state from their owning services."""
        mock_manager = Mock()
        mock_manager.broadcast_dict = AsyncMock()
        state_machine.ws_manager = mock_manager

        # Wire stand-in services exposing the two flag properties
        routing = Mock()
        routing.multiroom_enabled = True
        equalizer = Mock()
        equalizer.effects_enabled = False
        state_machine.routing_service = routing
        state_machine.camilladsp_service = equalizer

        await state_machine.broadcast(SourceStateChanged(source="radio", new_state="active"))

        full_state = mock_manager.broadcast_dict.call_args[0][0]["data"]["full_state"]
        assert full_state["multiroom_enabled"] is True
        assert full_state["equalizer_effects_enabled"] is False

    @pytest.mark.asyncio
    async def test_transition_broadcasts_to_websocket(self, state_machine, mock_source):
        """Test transitions broadcast to ws_manager."""
        mock_manager = Mock()
        mock_manager.broadcast_dict = AsyncMock()
        state_machine.ws_manager = mock_manager
        state_machine.register_source(AudioSource.RADIO, mock_source)

        await state_machine.transition_to_source(AudioSource.RADIO)

        # Should have called broadcast_dict for transition_start and transition_complete
        assert mock_manager.broadcast_dict.call_count >= 2


class TestTransitionTimeout:
    """Test transition timeout handling."""

    @pytest.mark.asyncio
    async def test_transition_timeout(self, state_machine):
        """Test transition timeout results in failure."""
        slow_source = Mock()
        slow_source.initialize = AsyncMock(return_value=True)

        async def slow_start():
            await asyncio.sleep(10)  # Longer than TRANSITION_TIMEOUT
            return True

        slow_source.start = slow_start
        slow_source.stop = AsyncMock(return_value=True)
        slow_source.is_initialized = False

        state_machine.register_source(AudioSource.RADIO, slow_source)
        state_machine.TRANSITION_TIMEOUT = 0.1  # Very short timeout for test

        result = await state_machine.transition_to_source(AudioSource.RADIO)

        assert result is False


class TestConcurrency:
    """Test the transition lock."""

    @pytest.mark.asyncio
    async def test_concurrent_transitions_are_serialized(self, state_machine, mock_source):
        """Two transitions fired at once must not interleave.

        _transition_lock is what keeps two sources from being started against
        the same ALSA device; without it the second caller reads a half-applied
        state and both end up believing they own the output.
        """
        async def slow_start():
            await asyncio.sleep(0.05)
            return True

        mock_source.start = slow_start
        state_machine.register_source(AudioSource.SPOTIFY, mock_source)

        results = await asyncio.gather(
            state_machine.transition_to_source(AudioSource.SPOTIFY),
            state_machine.transition_to_source(AudioSource.SPOTIFY),
        )

        assert all(results)
        assert state_machine.system_state.active_source == AudioSource.SPOTIFY
        assert state_machine.system_state.transitioning is False


class TestEmergencyStop:
    """Test emergency stop functionality."""

    @pytest.mark.asyncio
    async def test_start_returning_false_leaves_no_active_source(
        self, state_machine, mock_source
    ):
        """A source that reports a clean start failure still unwinds to NONE.

        Distinct from the raising case below: _do_start returning False is the
        documented way for a source to say "the service did not come up", and
        it must not leave the machine pointing at a source that is not playing.
        """
        mock_source.start = AsyncMock(return_value=False)
        state_machine.register_source(AudioSource.SPOTIFY, mock_source)

        result = await state_machine.transition_to_source(AudioSource.SPOTIFY)

        assert result is False
        assert state_machine.system_state.active_source == AudioSource.NONE

    @pytest.mark.asyncio
    async def test_emergency_stop_on_error(self, state_machine):
        """Test emergency stop is called on transition error."""
        failing_source = Mock()
        failing_source.initialize = AsyncMock(return_value=True)
        failing_source.start = AsyncMock(side_effect=Exception("Start failed"))
        failing_source.stop = AsyncMock(return_value=True)
        failing_source.is_initialized = False

        state_machine.register_source(AudioSource.RADIO, failing_source)

        result = await state_machine.transition_to_source(AudioSource.RADIO)

        assert result is False
        # Emergency stop should have been called
        failing_source.stop.assert_called()
        assert state_machine.system_state.active_source == AudioSource.NONE


class TestGetCurrentState:
    """Test get_current_state method."""

    def test_get_current_state_returns_dict(self, state_machine):
        """Test get_current_state returns state dict."""
        state = state_machine.get_current_state()

        assert isinstance(state, dict)
        assert "active_source" in state
        assert "source_state" in state
        assert "transitioning" in state
