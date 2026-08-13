# backend/tests/test_audio_state_machine.py
"""
Unit tests for AudioStateMachine — the single source of truth.

Tests cover:
- Source registration, activation, deactivation and direct source-to-source switch
- The update rules: an inactive source is ignored, an update during a transition
  is dropped, a state change replaces metadata while metadata=None preserves it
- Failure settling: a clean False from start(), a raising start() and the
  timeout all leave the source selected in ERROR — plus the retry that unlocks
  and the inactivity sweep that eventually clears it
- The transition lock
- WebSocket broadcasting, and which categories carry full_state
- `network_unavailable`: the NM level crossed with the source's own requirement
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock

from backend.core.state import AudioStateMachine
from backend.core.models.audio_state import (
    AudioSource,
    ConnectivityLevel,
    NetworkRequirement,
    SourceState,
)
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
    source.state = SourceState.READY
    source.metadata = {}
    return source


class TestAudioStateMachineBasics:
    """Test basic state machine operations."""

    def test_initial_state(self, state_machine):
        """Test initial state is NONE with READY."""
        state = state_machine.get_current_state()
        assert state["active_source"] == "none"
        assert state["source_state"] == "ready"
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
        """A source with no implementation behind it settles in ERROR.

        Every AudioSource member is a key of `sources` from construction, so an
        unregistered one gets as far as _start_source and fails there — the
        same unwind as a daemon that will not come up.
        """
        result = await state_machine.transition_to_source(AudioSource.RADIO)

        assert result is False
        assert state_machine.system_state.active_source == AudioSource.RADIO
        assert state_machine.system_state.source_state == SourceState.ERROR

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
        dropping to READY with a partial payload (Spotify when go-librespot
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
            SourceState.READY,
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


class TestFailedTransition:
    """How a transition that could not complete settles, and how it is retried."""

    @pytest.mark.asyncio
    async def test_start_returning_false_settles_the_source_in_error(
        self, state_machine, mock_source
    ):
        """A clean start failure leaves the source selected, in ERROR, with the
        message kept.

        Distinct from the raising case below: _do_start returning False is the
        documented way for a source to say "the service did not come up". The
        machine keeps pointing at it on purpose — "this source is in error" is
        what happened, and dropping to "no source" is what used to throw it
        away, message included.
        """
        mock_source.start = AsyncMock(return_value=False)
        state_machine.register_source(AudioSource.SPOTIFY, mock_source)

        result = await state_machine.transition_to_source(AudioSource.SPOTIFY)

        assert result is False
        assert state_machine.system_state.active_source == AudioSource.SPOTIFY
        assert state_machine.system_state.source_state == SourceState.ERROR
        assert state_machine.system_state.error == "Failed to start spotify"
        assert state_machine.system_state.transitioning is False

    @pytest.mark.asyncio
    async def test_raising_start_stops_the_target_and_settles_in_error(
        self, state_machine
    ):
        """A start that raises is stopped, then settled the same way.

        The stop matters on its own: a start can raise after the systemd unit
        came up (mpv started, IPC connect failed), so the failed target is the
        one source the unwind must tear down.
        """
        failing_source = Mock()
        failing_source.initialize = AsyncMock(return_value=True)
        failing_source.start = AsyncMock(side_effect=Exception("Start failed"))
        failing_source.stop = AsyncMock(return_value=True)
        failing_source.is_initialized = False

        state_machine.register_source(AudioSource.RADIO, failing_source)

        result = await state_machine.transition_to_source(AudioSource.RADIO)

        assert result is False
        failing_source.stop.assert_called()
        assert state_machine.system_state.active_source == AudioSource.RADIO
        assert state_machine.system_state.source_state == SourceState.ERROR

    @pytest.mark.asyncio
    async def test_reselecting_an_errored_source_retries_it(
        self, state_machine, mock_source
    ):
        """Re-selecting the errored source restarts it, and a success clears it.

        The whole point of leaving it selected: re-selecting the *active* source
        is otherwise a no-op, and the ERROR exception in that guard is what the
        card's retry rides on. Never exercised before — nothing wrote ERROR.
        """
        mock_source.start = AsyncMock(return_value=False)
        state_machine.register_source(AudioSource.SPOTIFY, mock_source)
        await state_machine.transition_to_source(AudioSource.SPOTIFY)

        # The daemon is back: the same gesture now starts it for real.
        mock_source.start = AsyncMock(return_value=True)
        mock_source.state = SourceState.READY
        result = await state_machine.transition_to_source(AudioSource.SPOTIFY)

        assert result is True
        mock_source.start.assert_awaited_once()
        assert state_machine.system_state.active_source == AudioSource.SPOTIFY
        assert state_machine.system_state.source_state == SourceState.READY
        # The previous attempt's message must not survive its retry.
        assert state_machine.system_state.error is None

    @pytest.mark.asyncio
    async def test_errored_source_is_still_deactivated_when_idle(
        self, state_machine, mock_source
    ):
        """The 12 h inactivity sweep covers ERROR, not just READY.

        Without it a source that failed to start would stay selected for ever,
        since it produces no activity to reset the timer either.
        """
        mock_source.start = AsyncMock(return_value=False)
        state_machine.register_source(AudioSource.SPOTIFY, mock_source)
        await state_machine.transition_to_source(AudioSource.SPOTIFY)

        state_machine._last_activity_time -= state_machine.INACTIVITY_TIMEOUT + 1
        await state_machine._check_inactivity()

        assert state_machine.system_state.active_source == AudioSource.NONE

    @pytest.mark.asyncio
    async def test_timeout_inside_the_old_teardown_finishes_that_teardown(
        self, state_machine, mock_source
    ):
        """A timeout while the *old* source is stopping must still stop it.

        The unwind only ever tore down the target, so a stop cancelled mid-way
        left the previous source running: bluealsa keeps the ALSA device and
        every later start of anything fails until the unit is rebooted. The
        second stop is the recovery — the first one never returned.
        """
        stop_calls = []

        async def stop_hangs_once():
            stop_calls.append(1)
            if len(stop_calls) == 1:
                await asyncio.sleep(10)  # cancelled by the transition timeout
            return True

        old_source = Mock()
        old_source.initialize = AsyncMock(return_value=True)
        old_source.start = AsyncMock(return_value=True)
        old_source.stop = stop_hangs_once
        old_source.is_initialized = False
        old_source.state = SourceState.ACTIVE
        old_source.metadata = {}

        state_machine.register_source(AudioSource.BLUETOOTH, old_source)
        state_machine.register_source(AudioSource.SPOTIFY, mock_source)
        await state_machine.transition_to_source(AudioSource.BLUETOOTH)

        state_machine.TRANSITION_TIMEOUT = 0.1
        result = await state_machine.transition_to_source(AudioSource.SPOTIFY)

        assert result is False
        assert len(stop_calls) == 2
        mock_source.start.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_start_failure_does_not_stop_the_old_source_twice(
        self, state_machine, mock_source
    ):
        """The far commoner branch: the old teardown completed, leave it alone.

        Re-running a teardown that already ran is its own bug — Bluetooth's is
        unconditional (bluetoothctl + bluealsa, no is-running guard) — so the
        recovery above must be reachable only from the cancelled branch.
        """
        old_source = Mock()
        old_source.initialize = AsyncMock(return_value=True)
        old_source.start = AsyncMock(return_value=True)
        old_source.stop = AsyncMock(return_value=True)
        old_source.is_initialized = False
        old_source.state = SourceState.ACTIVE
        old_source.metadata = {}

        mock_source.start = AsyncMock(side_effect=Exception("Start failed"))
        state_machine.register_source(AudioSource.BLUETOOTH, old_source)
        state_machine.register_source(AudioSource.SPOTIFY, mock_source)
        await state_machine.transition_to_source(AudioSource.BLUETOOTH)

        result = await state_machine.transition_to_source(AudioSource.SPOTIFY)

        assert result is False
        old_source.stop.assert_awaited_once()


class TestGetCurrentState:
    """Test get_current_state method."""

    def test_get_current_state_returns_dict(self, state_machine):
        """Test get_current_state returns state dict."""
        state = state_machine.get_current_state()

        assert isinstance(state, dict)
        assert "active_source" in state
        assert "source_state" in state
        assert "transitioning" in state


class TestNetworkUnavailable:
    """`full_state.network_unavailable` — the two-axis rule the card renders.

    Reporting on NetworkManager's level alone is what made the old offline
    banner fire while playing a CD; reporting on the source alone cannot tell a
    LAN-only link from a dead one. Both axes, or the answer is wrong.
    """

    @staticmethod
    def _wire(state_machine, level, source, requirement):
        state_machine.connectivity_service = Mock(level=level)
        instance = Mock()
        instance.NETWORK_REQUIREMENT = requirement
        state_machine.register_source(source, instance)
        state_machine.system_state.active_source = source

    @pytest.mark.parametrize(
        "level,requirement,expected",
        [
            # A dead link blocks everything that needs any network at all.
            (ConnectivityLevel.NONE, NetworkRequirement.INTERNET, "no_network"),
            (ConnectivityLevel.NONE, NetworkRequirement.LAN, "no_network"),
            (ConnectivityLevel.NONE, NetworkRequirement.NONE, None),
            # LAN up, no route out: internet sources only. This is the row that
            # a boolean `online` could not express.
            (ConnectivityLevel.LIMITED, NetworkRequirement.INTERNET, "no_internet"),
            (ConnectivityLevel.LIMITED, NetworkRequirement.LAN, None),
            (ConnectivityLevel.LIMITED, NetworkRequirement.NONE, None),
            # A captive portal Milō has no browser to answer reads as the same.
            (ConnectivityLevel.PORTAL, NetworkRequirement.INTERNET, "no_internet"),
            (ConnectivityLevel.PORTAL, NetworkRequirement.LAN, None),
            # Fail open: never report a problem we have not observed.
            (ConnectivityLevel.FULL, NetworkRequirement.INTERNET, None),
            (ConnectivityLevel.UNKNOWN, NetworkRequirement.INTERNET, None),
        ],
    )
    def test_level_crossed_with_requirement(
        self, state_machine, level, requirement, expected
    ):
        self._wire(state_machine, level, AudioSource.SPOTIFY, requirement)
        assert state_machine.get_current_state()["network_unavailable"] == expected

    def test_no_source_selected_reports_nothing(self, state_machine):
        """AudioSource.NONE needs nothing, so a dead link is not its problem —
        this is what keeps the home screen quiet instead of raising the banner
        the old boolean raised on every `!online`."""
        state_machine.connectivity_service = Mock(level=ConnectivityLevel.NONE)
        assert state_machine.get_current_state()["network_unavailable"] is None

    def test_unwired_service_reports_nothing(self, state_machine):
        """No connectivity service (a dev host without NM) must not paint every
        source as blocked."""
        self._wire(state_machine, ConnectivityLevel.NONE, AudioSource.RADIO,
                   NetworkRequirement.INTERNET)
        state_machine.connectivity_service = None
        assert state_machine.get_current_state()["network_unavailable"] is None


class TestBlockedTransitionBanner:
    """A failed start the link already explains must not also raise a banner.

    Two notifications for one cause is what made the offline behaviour read as
    broken on the unit: the status card said "no internet" with the network
    settings one tap away, and a raw "Network is unreachable" sat on top of it.
    """

    async def _fail_to_start(self, state_machine, requirement, level):
        source = Mock()
        source.initialize = AsyncMock(return_value=True)
        source.start = AsyncMock(return_value=False)   # the daemon refuses
        source.stop = AsyncMock(return_value=True)
        source.is_initialized = True
        source.state = SourceState.ERROR
        source.metadata = {}
        source.NETWORK_REQUIREMENT = requirement
        state_machine.register_source(AudioSource.DLNA, source)
        state_machine.connectivity_service = Mock(level=level)
        state_machine.ws_manager = Mock(broadcast_dict=AsyncMock())

        events = []
        original = state_machine.broadcast

        async def capture(event):
            events.append(event)
            await original(event)

        state_machine.broadcast = capture
        await state_machine.transition_to_source(AudioSource.DLNA)
        return events

    async def test_no_banner_when_the_link_explains_it(self, state_machine):
        events = await self._fail_to_start(
            state_machine, NetworkRequirement.LAN, ConnectivityLevel.NONE
        )
        assert not any(isinstance(e, SystemErrorEvent) for e in events)
        # The state still settles in ERROR — only the banner is withheld.
        assert state_machine.system_state.source_state == SourceState.ERROR
        assert state_machine.get_current_state()["network_unavailable"] == "no_network"

    async def test_banner_still_raised_when_the_link_is_fine(self, state_machine):
        """The regression guard on the suppression: a daemon that dies with the
        network up has nothing else to tell the user, so the banner must fire."""
        events = await self._fail_to_start(
            state_machine, NetworkRequirement.LAN, ConnectivityLevel.FULL
        )
        assert any(isinstance(e, SystemErrorEvent) for e in events)
