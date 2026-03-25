# backend/tests/test_audio_state_machine.py
"""
Unit tests for AudioStateMachine.

Tests cover:
- Source activation/deactivation (AC1, AC3)
- State transitions (AC5)
- WebSocket broadcasting
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch

from backend.core.state import AudioStateMachine
from backend.core.models.audio_state import AudioSource, SourceState


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
    source._initialized = False
    source.state = SourceState.WAITING
    source.metadata = {}
    return source


class TestAudioStateMachineBasics:
    """Test basic state machine operations."""

    @pytest.mark.asyncio
    async def test_initial_state(self, state_machine):
        """Test initial state is NONE with WAITING."""
        state = await state_machine.get_current_state()
        assert state["active_source"] == "none"
        assert state["source_state"] == "waiting"
        assert state["transitioning"] is False
        assert state["error"] is None

    def test_register_source(self, state_machine, mock_source):
        """Test source registration."""
        state_machine.register_source(AudioSource.RADIO, mock_source)

        assert state_machine.get_source(AudioSource.RADIO) is mock_source
        assert state_machine.get_source(AudioSource.SPOTIFY) is None

    def test_get_source_metadata_active(self, state_machine):
        """Test get_source_metadata returns metadata for active source."""
        state_machine.system_state.active_source = AudioSource.RADIO
        state_machine.system_state.metadata = {"title": "Test"}

        metadata = state_machine.get_source_metadata(AudioSource.RADIO)
        assert metadata == {"title": "Test"}

    def test_get_source_metadata_inactive(self, state_machine):
        """Test get_source_metadata returns empty for inactive source."""
        state_machine.system_state.active_source = AudioSource.RADIO

        metadata = state_machine.get_source_metadata(AudioSource.SPOTIFY)
        assert metadata == {}


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
        mock_radio._initialized = False

        mock_spotify = Mock()
        mock_spotify.initialize = AsyncMock(return_value=True)
        mock_spotify.start = AsyncMock(return_value=True)
        mock_spotify.stop = AsyncMock(return_value=True)
        mock_spotify._initialized = False

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


class TestMultiroomAndEqualizer:
    """Test multiroom and Equalizer state updates."""

    @pytest.mark.asyncio
    async def test_update_multiroom_state(self, state_machine):
        """Test updating multiroom state."""
        await state_machine.update_multiroom_state(True)

        assert state_machine.system_state.multiroom_enabled is True

    @pytest.mark.asyncio
    async def test_update_equalizer_effects_state(self, state_machine):
        """Test updating Equalizer effects state."""
        await state_machine.update_equalizer_effects_state(True)

        assert state_machine.system_state.equalizer_effects_enabled is True


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
    async def test_broadcast_includes_full_state_for_source(self, state_machine):
        """Test source events include full_state."""
        mock_manager = Mock()
        mock_manager.broadcast_dict = AsyncMock()
        state_machine.ws_manager = mock_manager

        await state_machine.broadcast_event("source", "state_changed", {"source": "radio"})

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
        slow_source._initialized = False

        state_machine.register_source(AudioSource.RADIO, slow_source)
        state_machine.TRANSITION_TIMEOUT = 0.1  # Very short timeout for test

        result = await state_machine.transition_to_source(AudioSource.RADIO)

        assert result is False


class TestEmergencyStop:
    """Test emergency stop functionality."""

    @pytest.mark.asyncio
    async def test_emergency_stop_on_error(self, state_machine):
        """Test emergency stop is called on transition error."""
        failing_source = Mock()
        failing_source.initialize = AsyncMock(return_value=True)
        failing_source.start = AsyncMock(side_effect=Exception("Start failed"))
        failing_source.stop = AsyncMock(return_value=True)
        failing_source._initialized = False

        state_machine.register_source(AudioSource.RADIO, failing_source)

        result = await state_machine.transition_to_source(AudioSource.RADIO)

        assert result is False
        # Emergency stop should have been called
        failing_source.stop.assert_called()
        assert state_machine.system_state.active_source == AudioSource.NONE


class TestGetCurrentState:
    """Test get_current_state method."""

    @pytest.mark.asyncio
    async def test_get_current_state_async(self, state_machine):
        """Test get_current_state returns state dict."""
        state = await state_machine.get_current_state()

        assert isinstance(state, dict)
        assert "active_source" in state
        assert "source_state" in state
        assert "transitioning" in state
