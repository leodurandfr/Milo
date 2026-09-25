# backend/tests/test_audio_state_machine.py
"""
Unit tests for AudioStateMachine — the single source of truth.

Tests cover:
- Source registration, activation, deactivation and direct source-to-source switch
- The update rules: an inactive source is ignored, a publish during a transition
  is dropped, a view replaces the previous one wholesale
- Failure settling: a clean False from start(), a raising start() and the
  timeout all leave the source selected with `service: failed` — plus the
  retry that unlocks and the inactivity sweep that eventually clears it
- The transition lock, and the multiroom switch held across a reroute
- What goes on the wire: `source/state` only when the state moved,
  `source/position` when only the playhead did
- `availability`: the NM level crossed with each source's own requirement,
  then the source's own reason
"""
import logging

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock

from backend.core.state import AudioStateMachine
from backend.tests.conftest import free_mailbox
from backend.core.models.audio_state import (
    AudioSource,
    ConnectivityLevel,
    NetworkRequirement,
)
from backend.core.models.audio_wire import (
    PositionAnchor, ServiceError, SessionView, SourceView,
)
from backend.core.models.ws_events import SourceError, VolumeChanged


def make_source(**overrides):
    """A registered source as the state machine sees it: lifecycle calls that
    succeed, no session, available, needing no network."""
    source = Mock()
    source.initialize = AsyncMock(return_value=True)
    source.start = AsyncMock(return_value=True)
    source.stop = AsyncMock(return_value=True)
    source.is_initialized = False
    source.view = SourceView()
    source.availability = Mock(return_value=None)
    source.NETWORK_REQUIREMENT = NetworkRequirement.NONE
    source.hold_mailbox = free_mailbox()
    for name, value in overrides.items():
        setattr(source, name, value)
    return source


def session_view(title="Song", *, session_id="s1", phase="playing", position=None, **fields):
    """A source's view with a live session."""
    return SourceView(session=SessionView(
        id=session_id, phase=phase, title=title, artist=fields.get("artist"),
        album=fields.get("album"), artwork=None, senders=[],
        duration_ms=fields.get("duration_ms"), position=position,
    ))


def recorded(state_machine):
    """Record what the machine broadcasts, as envelopes."""
    state_machine.ws_manager = Mock(broadcast_dict=AsyncMock())
    return state_machine.ws_manager.broadcast_dict


def envelopes(broadcast_dict, category=None, type_=None):
    return [
        c.args[0] for c in broadcast_dict.call_args_list
        if category is None or (c.args[0]["category"], c.args[0]["type"]) == (category, type_)
    ]


@pytest.fixture
def state_machine():
    """Create AudioStateMachine."""
    return AudioStateMachine()


@pytest.fixture
def mock_source():
    """Create a mock audio source."""
    return make_source()


class TestAudioStateMachineBasics:
    """Test basic state machine operations."""

    def test_initial_state(self, state_machine):
        """No source selected: nothing runs, nothing switches, nothing failed."""
        state = state_machine.get_current_state()
        assert state["source"] == "none"
        assert state["service"] == "stopped"
        assert state["switching"] is False
        assert state["service_error"] is None
        assert state["session"] is None
        assert state["controls"] == []

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
    async def test_transition_to_unregistered_source(self, state_machine, mock_source):
        """A source with no implementation behind it is refused before anything stops.

        Every AudioSource member is a key of `sources` from construction, so the
        key is never the question — the value is. Refusing here is what keeps the
        source that is currently playing when a service failed to be created.
        """
        state_machine.register_source(AudioSource.SPOTIFY, mock_source)
        await state_machine.transition_to_source(AudioSource.SPOTIFY)
        mock_source.reset_mock()

        result = await state_machine.transition_to_source(AudioSource.RADIO)

        assert result is False
        mock_source.stop.assert_not_called()
        assert state_machine.system_state.active_source == AudioSource.SPOTIFY
        assert state_machine.get_current_state()["service"] == "running"

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
        mock_radio = make_source()
        mock_spotify = make_source()

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


class TestSourceViewUpdate:
    """What a source's publish does to the state."""

    @pytest.mark.asyncio
    async def test_update_source_view(self, state_machine, mock_source):
        """The selected source's view becomes the state's."""
        state_machine.register_source(AudioSource.RADIO, mock_source)
        await state_machine.transition_to_source(AudioSource.RADIO)

        await state_machine.update_source_view(AudioSource.RADIO, session_view("Test Station"))

        session = state_machine.get_current_state()["session"]
        assert (session["phase"], session["title"]) == ("playing", "Test Station")

    @pytest.mark.asyncio
    async def test_update_from_an_inactive_source_is_ignored(self, state_machine, mock_source):
        """A source that is not selected cannot put its session on the card."""
        state_machine.register_source(AudioSource.RADIO, mock_source)
        await state_machine.transition_to_source(AudioSource.RADIO)

        await state_machine.update_source_view(AudioSource.SPOTIFY, session_view("Elsewhere"))

        assert state_machine.get_current_state()["session"] is None

    @pytest.mark.asyncio
    async def test_update_during_transition_is_dropped(self, state_machine):
        """Publishes arriving while `transitioning` is set are dropped, not buffered.

        There is no replay queue: the post-start resync re-reads source.view
        instead. A publish that slipped through here would let a source's
        pre-transition view overwrite the one being transitioned to.
        """
        state_machine.system_state.active_source = AudioSource.SPOTIFY
        state_machine.system_state.transitioning = True
        before = state_machine.system_state.view

        await state_machine.update_source_view(AudioSource.SPOTIFY, session_view())

        assert state_machine.system_state.view == before

    @pytest.mark.asyncio
    async def test_a_view_replaces_the_previous_one_wholesale(self, state_machine):
        """A publish replaces the view, it does not merge it.

        Regression guard: the state used to MERGE, so a source dropping to a
        narrower payload (Spotify when go-librespot dies) left the previous
        track's title/artist/album stale in GET /api/audio/state.
        """
        state_machine.system_state.active_source = AudioSource.SPOTIFY

        await state_machine.update_source_view(
            AudioSource.SPOTIFY, session_view("Song", artist="Artist", album="Album"),
        )
        await state_machine.update_source_view(
            AudioSource.SPOTIFY, session_view(None, phase="connected"),
        )

        session = state_machine.get_current_state()["session"]
        assert (session["title"], session["artist"], session["album"]) == (None, None, None)
        assert session["phase"] == "connected"


class TestWebSocketBroadcasting:
    """Test WebSocket broadcasting via ws_manager."""

    @pytest.mark.asyncio
    async def test_broadcast_with_ws_manager(self, state_machine):
        """Test broadcast calls ws_manager.broadcast_dict."""
        sent = recorded(state_machine)

        await state_machine.broadcast(SourceError(source="radio", reason="stream_load_failed"))

        sent.assert_called_once()
        call_args = sent.call_args[0][0]
        assert (call_args["category"], call_args["type"]) == ("source", "error")
        assert call_args["data"]["reason"] == "stream_load_failed"

    @pytest.mark.asyncio
    async def test_broadcast_without_ws_manager(self, state_machine):
        """Test broadcast works without ws_manager."""
        # Should not raise
        await state_machine.broadcast(SourceError(source="radio", reason="stream_load_failed"))

    @pytest.mark.asyncio
    async def test_a_volume_event_carries_no_state(self, state_machine):
        """The state travels once, as `source/state`; no other event carries it."""
        sent = recorded(state_machine)

        await state_machine.broadcast(VolumeChanged(
            show_bar=True, step_mobile_db=3.0, multiroom_enabled=False, state={}
        ))

        assert "full_state" not in sent.call_args[0][0]["data"]

    @pytest.mark.asyncio
    async def test_the_state_composes_the_flags_of_their_owning_services(self, state_machine):
        """multiroom_enabled and equalizer_effects_enabled are read from the
        routing and CamillaDSP services, not kept here."""
        sent = recorded(state_machine)
        state_machine.routing_service = Mock(multiroom_enabled=True)
        state_machine.camilladsp_service = Mock(effects_enabled=False)

        await state_machine.publish_state()

        data = envelopes(sent, "source", "state")[0]["data"]
        assert data["multiroom_enabled"] is True
        assert data["equalizer_effects_enabled"] is False

    @pytest.mark.asyncio
    async def test_transition_broadcasts_to_websocket(self, state_machine, mock_source):
        """A switch is announced as it starts and as it ends."""
        sent = recorded(state_machine)
        state_machine.register_source(AudioSource.RADIO, mock_source)

        await state_machine.transition_to_source(AudioSource.RADIO)

        states = [e["data"] for e in envelopes(sent, "source", "state")]
        assert [(s["switching"], s["service"]) for s in states] == [
            (True, "starting"), (False, "running"),
        ]


class TestPublishState:
    """What `publish_state()` puts on the wire: the state when anything but the
    playhead moved, the position alone when only the playhead did, and
    nothing when nothing did.

    What breaks when this fails: a state per tick floods every client (and
    wakes the push loop on each), or a seek never reaches a client that is
    extrapolating from the old anchor.
    """

    ANCHOR = PositionAnchor(ms=1000, at=1700000000.0, rate=1.0)

    async def _playing(self, state_machine, position):
        state_machine.system_state.active_source = AudioSource.PODCAST
        await state_machine.update_source_view(
            AudioSource.PODCAST, session_view("Episode", position=position),
        )

    async def test_the_state_event_is_the_state(self, state_machine):
        sent = recorded(state_machine)
        state_machine.system_state.active_source = AudioSource.RADIO

        await state_machine.publish_state()

        (envelope,) = envelopes(sent)
        assert (envelope["category"], envelope["type"], envelope["origin"]) == (
            "source", "state", "radio",
        )
        assert envelope["data"] == state_machine.get_current_state()

    async def test_an_unchanged_state_is_not_sent_again(self, state_machine):
        sent = recorded(state_machine)

        await state_machine.publish_state()
        await state_machine.publish_state()

        assert len(envelopes(sent)) == 1

    async def test_a_moved_anchor_alone_is_a_position_event(self, state_machine):
        sent = recorded(state_machine)
        await self._playing(state_machine, self.ANCHOR)
        sent.reset_mock()

        await self._playing(state_machine, PositionAnchor(ms=60000, at=1700000005.0, rate=1.0))

        (envelope,) = envelopes(sent)
        assert (envelope["category"], envelope["type"]) == ("source", "position")
        assert envelope["data"] == {
            "source": "podcast", "session_id": "s1",
            "position": {"ms": 60000, "at": 1700000005.0, "rate": 1.0},
        }

    async def test_a_state_change_carries_the_anchor_with_it(self, state_machine):
        """A pause moves the phase and re-stamps the anchor: one `source/state`
        carrying both, never a state and then a position."""
        sent = recorded(state_machine)
        await self._playing(state_machine, self.ANCHOR)
        sent.reset_mock()

        state_machine.system_state.active_source = AudioSource.PODCAST
        paused_anchor = PositionAnchor(ms=4000, at=1700000003.0, rate=1.0)
        await state_machine.update_source_view(
            AudioSource.PODCAST,
            session_view("Episode", phase="paused", position=paused_anchor),
        )

        (envelope,) = envelopes(sent)
        assert (envelope["category"], envelope["type"]) == ("source", "state")
        assert envelope["data"]["session"]["position"]["ms"] == 4000

        # ...and the anchor it carried is not sent again on its own.
        await state_machine.publish_state()
        assert len(envelopes(sent)) == 1


class TestTransitionTimeout:
    """Test transition timeout handling."""

    @pytest.mark.asyncio
    async def test_transition_timeout(self, state_machine):
        """A start that outlives the budget fails, releases the target, and says so.

        Only the bool used to be checked here, and the arm is worth more than
        that. `connect()` now spends the whole of its 6.0s instead of stopping a
        probe's width short, so a slow mpv reaches this timeout about a second
        sooner than it used to — on the boot of 2026-09-01 the transition
        finished 1.15s inside TRANSITION_TIMEOUT, and that is the margin being
        spent. What the arm must not do is differ from the clean-failure path in
        what it leaves running: the target's systemd unit and its ALSA device
        stay held by a source the machine has already given up on, and the
        cancellation cannot unwind that itself — `_do_start`'s own `except
        Exception` does not catch a CancelledError.

        The reason is the second half: both arms settle identically, so
        `service_error.reason` is the only thing in the state or the journal
        that says which one fired.
        """
        slow_source = make_source()

        async def slow_start():
            await asyncio.sleep(10)  # Longer than TRANSITION_TIMEOUT
            return True

        slow_source.start = slow_start

        state_machine.register_source(AudioSource.RADIO, slow_source)
        state_machine.TRANSITION_TIMEOUT = 0.1  # Very short timeout for test

        result = await state_machine.transition_to_source(AudioSource.RADIO)

        assert result is False
        slow_source.stop.assert_awaited_once()
        state = state_machine.get_current_state()
        assert state["service"] == "failed"
        assert state["service_error"]["reason"] == "start_timeout"


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
    async def test_start_returning_false_settles_the_source_failed(
        self, state_machine, mock_source
    ):
        """A clean start failure leaves the source selected, failed, with the
        message kept.

        Distinct from the raising case below: _do_start returning False is the
        documented way for a source to say "the service did not come up". The
        machine keeps pointing at it on purpose — "this source failed" is what
        happened, and dropping to "no source" is what used to throw it away,
        message included.
        """
        mock_source.start = AsyncMock(return_value=False)
        state_machine.register_source(AudioSource.SPOTIFY, mock_source)

        result = await state_machine.transition_to_source(AudioSource.SPOTIFY)

        assert result is False
        state = state_machine.get_current_state()
        assert (state["source"], state["service"], state["switching"]) == (
            "spotify", "failed", False,
        )
        assert state["service_error"] == {
            "reason": "start_failed", "message": "Failed to start spotify",
        }
        assert state["controls"] == []

    @pytest.mark.asyncio
    async def test_raising_start_stops_the_target_and_settles_failed(
        self, state_machine
    ):
        """A start that raises is stopped, then settled the same way.

        The stop matters on its own: a start can raise after the systemd unit
        came up (mpv started, IPC connect failed), so the failed target is the
        one source the unwind must tear down.
        """
        failing_source = make_source(start=AsyncMock(side_effect=Exception("Start failed")))

        state_machine.register_source(AudioSource.RADIO, failing_source)

        result = await state_machine.transition_to_source(AudioSource.RADIO)

        assert result is False
        failing_source.stop.assert_called()
        state = state_machine.get_current_state()
        assert (state["source"], state["service"]) == ("radio", "failed")

    @pytest.mark.asyncio
    async def test_reselecting_a_failed_source_retries_it(
        self, state_machine, mock_source
    ):
        """Re-selecting the failed source restarts it, and a success clears it.

        The whole point of leaving it selected: re-selecting the *active* source
        is otherwise a no-op, and the failure exception in that guard is what
        the card's retry rides on.
        """
        mock_source.start = AsyncMock(return_value=False)
        state_machine.register_source(AudioSource.SPOTIFY, mock_source)
        await state_machine.transition_to_source(AudioSource.SPOTIFY)

        # The daemon is back: the same gesture now starts it for real.
        mock_source.start = AsyncMock(return_value=True)
        result = await state_machine.transition_to_source(AudioSource.SPOTIFY)

        assert result is True
        mock_source.start.assert_awaited_once()
        state = state_machine.get_current_state()
        assert (state["source"], state["service"]) == ("spotify", "running")
        # The previous attempt's message must not survive its retry.
        assert state["service_error"] is None

    @pytest.mark.asyncio
    async def test_a_failed_source_is_still_deactivated_when_idle(
        self, state_machine, mock_source
    ):
        """The 12 h inactivity sweep covers a failed source, not just an idle one.

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
    async def test_a_teardown_cut_by_the_timeout_is_finished_not_re_issued(
        self, state_machine, mock_source
    ):
        """The budget cuts the transition's *wait* for the old stop, never the stop.

        A stop cancelled mid-way left the previous source running (bluealsa
        kept the ALSA device and every later start failed until reboot), so the
        unwind used to re-issue it — after probing the unit, because the cut
        stop had usually landed anyway, and a blind second call on a dead unit
        timed out and read as a refusal. Now the stop is never cancelled: the
        failed transition waits for that one call to end, and there is nothing
        to re-issue or probe.
        """
        released = asyncio.Event()
        stop_calls = []

        async def slow_stop():
            stop_calls.append("begin")
            await released.wait()
            stop_calls.append("end")
            return True

        old_source = make_source(stop=slow_stop)

        state_machine.register_source(AudioSource.BLUETOOTH, old_source)
        state_machine.register_source(AudioSource.SPOTIFY, mock_source)
        await state_machine.transition_to_source(AudioSource.BLUETOOTH)

        state_machine.TRANSITION_TIMEOUT = 0.05
        asyncio.get_running_loop().call_later(0.2, released.set)
        result = await state_machine.transition_to_source(AudioSource.SPOTIFY)

        assert result is False
        assert stop_calls == ["begin", "end"]
        mock_source.start.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_retry_never_starts_over_a_teardown_still_running(
        self, state_machine, mock_source
    ):
        """The press that follows a timed-out switch waits for the old source.

        The old stop outlived the budget and the user presses the new source
        again at once. Starting it while the old unit still holds the device is
        the failure the cut-teardown machinery existed for — now the failed
        transition holds the lock until that stop is over, so the retry is
        ordered behind it by construction.
        """
        released = asyncio.Event()
        order = []

        async def slow_stop():
            await released.wait()
            order.append("old stopped")
            return True

        async def start_new():
            order.append("new started")
            return True

        old_source = make_source(stop=slow_stop)
        mock_source.start = start_new

        state_machine.register_source(AudioSource.BLUETOOTH, old_source)
        state_machine.register_source(AudioSource.SPOTIFY, mock_source)
        await state_machine.transition_to_source(AudioSource.BLUETOOTH)

        state_machine.TRANSITION_TIMEOUT = 0.05
        first = asyncio.ensure_future(state_machine.transition_to_source(AudioSource.SPOTIFY))
        await asyncio.sleep(0.1)                     # past the budget, stop still held
        retry = asyncio.ensure_future(state_machine.transition_to_source(AudioSource.SPOTIFY))
        await asyncio.sleep(0.05)
        released.set()
        await first
        await retry

        assert order[0] == "old stopped"
        assert "new started" in order

    @pytest.mark.asyncio
    async def test_a_start_failure_does_not_stop_the_old_source_twice(
        self, state_machine, mock_source
    ):
        """The far commoner branch: the old teardown completed, leave it alone.

        Re-running a teardown that already ran is its own bug — Bluetooth's is
        unconditional (bluetoothctl + bluealsa, no is-running guard) — so the
        recovery above must be reachable only from the cancelled branch.
        """
        old_source = make_source(stop=AsyncMock(return_value=True))

        mock_source.start = AsyncMock(side_effect=Exception("Start failed"))
        state_machine.register_source(AudioSource.BLUETOOTH, old_source)
        state_machine.register_source(AudioSource.SPOTIFY, mock_source)
        await state_machine.transition_to_source(AudioSource.BLUETOOTH)

        result = await state_machine.transition_to_source(AudioSource.SPOTIFY)

        assert result is False
        old_source.stop.assert_awaited_once()


class TestAFailureIsTheLastWordOfAFailedStart:
    """`service: failed`, once settled, is what every client is shown until a
    start succeeds."""

    @pytest.mark.asyncio
    async def test_the_failure_lands_with_the_switch_over(self, state_machine, mock_source):
        """The state that says `failed` is the one the card draws.

        A "starting" once lingered with the switch already over — a spinner
        under the failure, Dock re-enabled — for as long as the failed target
        took to stop, before the failure landed (E03). And the settle after
        the target stopped repeats nothing already on the wire.
        """
        sent = recorded(state_machine)
        mock_source.start = AsyncMock(return_value=False)
        state_machine.register_source(AudioSource.SPOTIFY, mock_source)

        await state_machine.transition_to_source(AudioSource.SPOTIFY)

        states = [e["data"] for e in envelopes(sent, "source", "state")]
        assert not [s for s in states if s["service"] == "starting" and not s["switching"]]
        failed = [s for s in states if s["service"] == "failed"]
        assert len(failed) == 1
        assert failed[0]["switching"] is False
        assert states[-1] == failed[0]

    @pytest.mark.asyncio
    async def test_a_source_publishing_after_a_failed_start_does_not_hide_the_failure(
        self, state_machine, mock_source
    ):
        """The CD's disc watcher runs whether or not its start succeeded, and its
        next publish replaced the failure — the card lost "Retry" and
        re-selecting the source became a no-op (E07)."""
        mock_source.start = AsyncMock(return_value=False)
        state_machine.register_source(AudioSource.CD, mock_source)
        await state_machine.transition_to_source(AudioSource.CD)

        await state_machine.update_source_view(AudioSource.CD, session_view("Track 1"))

        state = state_machine.get_current_state()
        assert state["service"] == "failed"
        assert state["service_error"]["message"] == "Failed to start cd"
        assert state["session"] is None

        mock_source.start = AsyncMock(return_value=True)
        assert await state_machine.transition_to_source(AudioSource.CD) is True
        mock_source.start.assert_awaited_once()
        assert state_machine.get_current_state()["service"] == "running"


class TestASourceThatWillNotStop:
    """`stop()` reporting False is journal-only, and the transition proceeds.

    The next source is then started over one still holding the ALSA device, and
    the silence that follows had no trace at banner level. The transition is not
    refused — `_start_source` fails on its own when the device is genuinely held,
    and refusing here would make the source unselectable — so the log IS the fix.
    """

    async def test_a_refused_stop_is_reported_at_error(self, state_machine, caplog):
        """The unit answered that it is still up: this is the real refusal."""
        stubborn = Mock()
        stubborn.stop = AsyncMock(return_value=False)
        stubborn.probe_service_active = AsyncMock(return_value=True)
        state_machine.sources[AudioSource.SPOTIFY] = stubborn

        with caplog.at_level(logging.ERROR):
            await state_machine._stop_source(AudioSource.SPOTIFY)

        assert "would not stop" in caplog.text
        assert AudioSource.SPOTIFY.value in caplog.text

    async def test_a_stop_that_landed_late_is_not_called_a_refusal(
        self, state_machine, caplog
    ):
        """`stop()` False with the unit down is a budget that ran out, not a
        refusal — and the banner used to carry the accusation anyway.

        This is the shape measured on the unit: the transition guard cancelled
        a stop that systemd completed a second later, and the log named the
        source as the culprit.
        """
        late = Mock()
        late.stop = AsyncMock(return_value=False)
        late.probe_service_active = AsyncMock(return_value=False)
        state_machine.sources[AudioSource.SPOTIFY] = late

        with caplog.at_level(logging.ERROR):
            await state_machine._stop_source(AudioSource.SPOTIFY)

        assert caplog.text == ""

    async def test_an_unreadable_unit_is_reported_as_unconfirmed(
        self, state_machine, caplog
    ):
        """Neither silence nor an accusation: the device may still be held."""
        opaque = Mock()
        opaque.stop = AsyncMock(return_value=False)
        opaque.probe_service_active = AsyncMock(return_value=None)
        state_machine.sources[AudioSource.SPOTIFY] = opaque

        with caplog.at_level(logging.WARNING):
            await state_machine._stop_source(AudioSource.SPOTIFY)

        assert "unconfirmed" in caplog.text
        assert "would not stop" not in caplog.text

    async def test_a_clean_stop_says_nothing(self, state_machine, mock_source, caplog):
        state_machine.sources[AudioSource.SPOTIFY] = mock_source

        with caplog.at_level(logging.ERROR):
            await state_machine._stop_source(AudioSource.SPOTIFY)

        assert caplog.text == ""

    async def test_the_transition_still_proceeds_over_it(self, state_machine, mock_source):
        """Refusing would turn an unclean stop into a source the user cannot
        select — worse than the failure it guards."""
        stubborn = make_source(stop=AsyncMock(return_value=False))
        state_machine.sources[AudioSource.SPOTIFY] = stubborn
        state_machine.sources[AudioSource.BLUETOOTH] = mock_source
        state_machine.system_state.active_source = AudioSource.SPOTIFY

        assert await state_machine.transition_to_source(AudioSource.BLUETOOTH) is True
        mock_source.start.assert_awaited_once()


class TestGetCurrentState:
    """Test get_current_state method."""

    def test_every_key_is_always_present(self, state_machine):
        """An absent value is null, never a missing key: the store's strict
        schema and Milo-Mac's decoder both refuse a state with a key missing."""
        state = state_machine.get_current_state()

        assert set(state) == {
            "source", "switching", "service", "service_error", "availability",
            "session", "controls", "resume", "details", "multiroom_enabled",
            "equalizer_effects_enabled",
        }
        assert set(state["availability"]) == {
            s.value for s in AudioSource if s is not AudioSource.NONE
        }


class TestAvailability:
    """`availability` — the two-axis rule the cards render, then the source's
    own reason.

    Reporting on NetworkManager's level alone is what made the old offline
    banner fire while playing a CD; reporting on the source alone cannot tell a
    LAN-only link from a dead one. Both axes, or the answer is wrong.
    """

    @staticmethod
    def _wire(state_machine, level, source, requirement, own=None):
        state_machine.connectivity_service = Mock(level=level)
        instance = make_source(NETWORK_REQUIREMENT=requirement,
                               availability=Mock(return_value=own))
        state_machine.register_source(source, instance)
        return instance

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
        assert state_machine.get_current_state()["availability"]["spotify"] == expected

    def test_every_source_is_answered_whichever_is_selected(self, state_machine):
        """The home screen's cards read one entry each: a source nobody
        selected is still greyed by a dead link, and one the link does not
        concern is not."""
        self._wire(state_machine, ConnectivityLevel.NONE, AudioSource.RADIO,
                   NetworkRequirement.INTERNET)
        self._wire(state_machine, ConnectivityLevel.NONE, AudioSource.CD,
                   NetworkRequirement.NONE)

        availability = state_machine.get_current_state()["availability"]

        assert state_machine.get_current_state()["source"] == "none"
        assert (availability["radio"], availability["cd"]) == ("no_network", None)

    def test_the_link_comes_before_the_sources_own_reason(self, state_machine):
        """Two reasons, one card: the link is the one the user can act on first."""
        instance = self._wire(state_machine, ConnectivityLevel.FULL, AudioSource.QOBUZ,
                              NetworkRequirement.INTERNET, own="no_account")
        assert state_machine.get_current_state()["availability"]["qobuz"] == "no_account"

        state_machine.connectivity_service.level = ConnectivityLevel.LIMITED
        assert state_machine.get_current_state()["availability"]["qobuz"] == "no_internet"
        assert instance.availability.call_count == 1   # the link answered first

    def test_a_source_that_cannot_answer_is_available(self, state_machine, caplog):
        """Fail open, and say so: a raising `availability()` must not take the
        state route (GET /api/audio/state, every broadcast) down with it."""
        instance = self._wire(state_machine, ConnectivityLevel.FULL, AudioSource.CD,
                              NetworkRequirement.NONE)
        instance.availability = Mock(side_effect=RuntimeError("drive gone"))

        with caplog.at_level(logging.ERROR):
            assert state_machine.get_current_state()["availability"]["cd"] is None
        assert "drive gone" in caplog.text

    def test_unwired_service_reports_nothing(self, state_machine):
        """No connectivity service (a dev host without NM) must not paint every
        source as blocked."""
        self._wire(state_machine, ConnectivityLevel.NONE, AudioSource.RADIO,
                   NetworkRequirement.INTERNET)
        state_machine.connectivity_service = None
        assert state_machine.get_current_state()["availability"]["radio"] is None


class TestAFailedStartRaisesNoBanner:
    """A failed start is the state's to say, never a banner as well.

    Two notifications for one cause is what made the offline behaviour read as
    broken on the unit: the status card said "no internet" with the network
    settings one tap away, and a raw "Network is unreachable" sat on top of it.
    The card now shows `service: failed` with its retry, and the log line is a
    WARNING, which the banner's log handler does not forward.
    """

    async def _fail_to_start(self, state_machine, requirement, level, caplog):
        source = make_source(
            start=AsyncMock(return_value=False),   # the daemon refuses
            is_initialized=True, NETWORK_REQUIREMENT=requirement,
        )
        state_machine.register_source(AudioSource.AIRPLAY, source)
        state_machine.connectivity_service = Mock(level=level)
        sent = recorded(state_machine)
        with caplog.at_level(logging.WARNING, logger="backend.core.state"):
            await state_machine.transition_to_source(AudioSource.AIRPLAY)
        return sent

    @pytest.mark.parametrize("level", [ConnectivityLevel.NONE, ConnectivityLevel.FULL])
    async def test_only_the_state_says_it(self, state_machine, caplog, level):
        sent = await self._fail_to_start(
            state_machine, NetworkRequirement.LAN, level, caplog
        )

        assert {(e["category"], e["type"]) for e in envelopes(sent)} == {("source", "state")}
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert "Transition failed" in caplog.text
        assert state_machine.get_current_state()["service"] == "failed"

    async def test_the_link_that_explains_it_is_on_the_state(self, state_machine, caplog):
        await self._fail_to_start(
            state_machine, NetworkRequirement.LAN, ConnectivityLevel.NONE, caplog
        )

        state = state_machine.get_current_state()
        assert state["availability"]["airplay"] == "no_network"
        assert "link is no_network" in caplog.text


class TestRefreshActiveView:
    """The re-read behind GET /api/audio/state and the WS handshake.

    What breaks when this fails: a client connecting mid-track is handed the
    view the state machine last stored instead of the one the source holds,
    so the now-playing card — and Milo-Mac, which reads the same payload —
    draws a stale anchor or an empty track until the source next publishes.
    """

    STORED = session_view("Stored")

    def _register(self, state_machine, source):
        state_machine.register_source(AudioSource.SPOTIFY, source)
        state_machine.system_state.active_source = AudioSource.SPOTIFY
        state_machine.system_state.view = self.STORED

    async def test_the_sources_view_replaces_the_stored_one(self, state_machine):
        source = make_source(view=session_view("Fresh", artist="Artist"),
                             refresh_when_idle=AsyncMock(return_value=True))
        self._register(state_machine, source)

        assert await state_machine.refresh_active_view() is True
        session = state_machine.get_current_state()["session"]
        assert (session["title"], session["artist"]) == ("Fresh", "Artist")

    async def test_a_source_with_nothing_to_report_leaves_the_view_alone(self, state_machine):
        """Only a hook that says it re-read may overwrite the view: a False
        verdict means the source could not read, and copying its view anyway
        would publish whatever half-state the failed read left behind."""
        source = make_source(view=session_view("Half-read"),
                             refresh_when_idle=AsyncMock(return_value=False))
        self._register(state_machine, source)

        assert await state_machine.refresh_active_view() is False
        assert state_machine.system_state.view == self.STORED

    async def test_a_hook_that_raises_does_not_take_the_state_route_down(self, state_machine):
        """This runs on the WS handshake and on every GET /api/audio/state, so a
        source whose daemon just died must cost a stale view, not a 500."""
        source = make_source(refresh_when_idle=AsyncMock(side_effect=RuntimeError("daemon gone")))
        self._register(state_machine, source)

        assert await state_machine.refresh_active_view() is False
        assert state_machine.system_state.view == self.STORED

    async def test_a_source_switched_away_from_during_the_read_is_not_written_back(
        self, state_machine, mock_source
    ):
        """The hook awaits the source's daemon (Spotify reads go-librespot's
        /status over HTTP), and a transition can complete inside that await.
        Writing the outgoing source's view then puts its track on the card of
        the source that replaced it, until that one next publishes."""
        answered = asyncio.Event()

        async def _read_the_daemon():
            await answered.wait()
            return True

        source = make_source(view=session_view("Outgoing"),
                             refresh_when_idle=AsyncMock(side_effect=_read_the_daemon))
        self._register(state_machine, source)
        mock_source.view = session_view("Radio")
        state_machine.register_source(AudioSource.RADIO, mock_source)

        refresh = asyncio.create_task(state_machine.refresh_active_view())
        await asyncio.sleep(0)
        assert await state_machine.transition_to_source(AudioSource.RADIO)
        answered.set()
        await refresh

        assert state_machine.system_state.active_source == AudioSource.RADIO
        assert state_machine.get_current_state()["session"]["title"] == "Radio"

    async def test_a_read_that_lands_inside_a_transition_is_not_written(self, state_machine):
        """Retrying a failed source keeps it selected while it restarts. The
        transition blanks the view and resyncs it from the source once the
        start is done; a refresh landing in between would publish the view of
        the attempt that failed, over a card that says it is starting."""
        answered = asyncio.Event()
        started = asyncio.Event()

        async def _read_the_daemon():
            await answered.wait()
            return True

        async def _start():
            await started.wait()
            return True

        source = make_source(view=session_view("Failed attempt"),
                             refresh_when_idle=AsyncMock(side_effect=_read_the_daemon),
                             start=AsyncMock(side_effect=_start))
        self._register(state_machine, source)
        state_machine.system_state.view = SourceView()
        state_machine.system_state.service_error = ServiceError(
            reason="start_failed", message="Failed to start spotify")

        refresh = asyncio.create_task(state_machine.refresh_active_view())
        await asyncio.sleep(0)
        retry = asyncio.create_task(state_machine.transition_to_source(AudioSource.SPOTIFY))
        while not state_machine.system_state.transitioning:
            await asyncio.sleep(0)
        answered.set()
        await refresh

        assert state_machine.system_state.view == SourceView()
        started.set()
        assert await retry


class TestRerouteActiveSource:
    """The lock and the order the multiroom reroute holds while it moves a source.

    `AudioRoutingService._apply_transition` hands its output switch to
    `reroute_active_source()`, which carries the active source across it under
    the transition lock. What breaks when this fails: a user tapping a source
    while a reroute is in flight has both paths stopping and starting the same
    units — or the card never shows what the source published across it.
    """

    async def test_it_locks_out_a_concurrent_transition(self, state_machine, mock_source):
        state_machine.register_source(AudioSource.RADIO, mock_source)
        state_machine.ALSA_RELEASE_SETTLE_S = 0
        switching = asyncio.Event()
        release = asyncio.Event()

        async def switch_output():
            switching.set()
            await release.wait()

        reroute = asyncio.create_task(state_machine.reroute_active_source(switch_output))
        await switching.wait()
        transition = asyncio.create_task(state_machine.transition_to_source(AudioSource.RADIO))
        await asyncio.sleep(0.01)  # let the task run until it blocks
        mock_source.start.assert_not_awaited()

        release.set()
        await reroute
        assert await transition is True
        mock_source.start.assert_awaited_once()

    async def test_a_failed_output_switch_still_reacquires_the_source(
        self, state_machine, mock_source
    ):
        """E08: a switch that raised (snapcast refusing to move) left the source
        released — Spotify's output parked on `null`, any other source stopped —
        although the mode had not changed. The source is taken back under the
        mode in force, and the failure still reaches the caller."""
        mock_source.release_for_reroute = AsyncMock(return_value=True)
        mock_source.acquire_after_reroute = AsyncMock(return_value=True)
        state_machine.register_source(AudioSource.RADIO, mock_source)
        state_machine.system_state.active_source = AudioSource.RADIO
        state_machine.ALSA_RELEASE_SETTLE_S = 0

        async def switch_output():
            raise RuntimeError("Failed to start snapcast services")

        with pytest.raises(RuntimeError):
            await state_machine.reroute_active_source(switch_output)

        mock_source.acquire_after_reroute.assert_awaited_once()

    async def test_what_the_source_publishes_meanwhile_reaches_the_ui_live(
        self, state_machine, mock_source
    ):
        """The reroute deliberately does NOT set `transitioning`.

        `update_source_view()` drops — never buffers — every publish arriving
        while that flag is set, and the source's own publishes across the
        reroute must go out: the toggle's `switching` (the caller's
        `multiroom_switch()`) already says the service is starting, with the
        track kept on the card, and the reacquire's publish lands on it.
        """
        sent = recorded(state_machine)
        state_machine.register_source(AudioSource.RADIO, mock_source)
        state_machine.system_state.active_source = AudioSource.RADIO
        state_machine.system_state.view = session_view("Song")
        state_machine.ALSA_RELEASE_SETTLE_S = 0

        async def reacquire():
            await state_machine.update_source_view(
                AudioSource.RADIO, session_view("Song", session_id="s2"))
            return True

        mock_source.release_for_reroute = AsyncMock(return_value=True)
        mock_source.acquire_after_reroute = AsyncMock(side_effect=reacquire)

        async def switch_output():
            pass

        async with state_machine.multiroom_switch():
            await state_machine.reroute_active_source(switch_output)
            assert state_machine.system_state.transitioning is False

        states = [e["data"] for e in envelopes(sent, "source", "state")]
        assert [(s["switching"], s["service"], s["session"]["id"]) for s in states] == [
            (True, "starting", "s1"),       # the toggle starts: the track stays
            (True, "starting", "s2"),       # the reacquire's publish, live
            (False, "running", "s2"),       # the toggle ends
        ]
        mock_source.release_for_reroute.assert_awaited_once()
        mock_source.acquire_after_reroute.assert_awaited_once()


class TestReloadAutoStopForAllSources:
    """The fan-out behind a write to `audio.auto_stop_delay`.

    What breaks when this fails: one source refusing the new delay leaves every
    source after it on the old one, so the setting applies to part of the
    appliance only — and the settings route reports success either way.
    """

    async def test_a_refusing_source_does_not_deny_the_others(self, state_machine):
        sources = {}
        for name, effect in (
            (AudioSource.RADIO, None),
            (AudioSource.PODCAST, RuntimeError("mpv socket gone")),
            (AudioSource.SPOTIFY, None),
        ):
            source = Mock(reload_auto_stop_config=AsyncMock(side_effect=effect))
            state_machine.register_source(name, source)
            sources[name] = source

        assert await state_machine.reload_auto_stop_for_all_sources() is False

        not_reloaded = [
            name.value for name, source in sources.items()
            if not source.reload_auto_stop_config.await_count
        ]
        assert not not_reloaded, f"sources left on the old delay: {not_reloaded}"


class TestInactivityMonitorTask:
    """The 12 h sweep as a running task, not just its one-shot check.

    `_check_inactivity` was already driven directly; the loop around it was not.
    That loop is the appliance's only way back to `source=NONE` without a user
    gesture, it is spawned exactly once (by the lifespan), and nothing restarts
    it — so if its body can die, the appliance never idles again for the life of
    the process.
    """

    class _StopTheMonitor(BaseException):
        """Ends a bounded double.

        Derived from `BaseException` because the loop body catches `Exception`
        and logs it, which would turn a runaway double into a silent busy loop
        instead of a failure — the shape that took this machine down in B8a.
        """

    @pytest.mark.asyncio
    async def test_the_monitor_is_started_once_and_is_idempotent(self, state_machine):
        """`main.py` calls it at boot; a second call must not spawn a second
        sweep, or two tasks race the same CAS transition and the second one
        deactivates a source the user selected in between."""
        state_machine.start_inactivity_monitor()
        first = state_machine._inactivity_monitor_task
        state_machine.start_inactivity_monitor()

        try:
            assert state_machine._inactivity_monitor_task is first
            assert first is not None
        finally:
            state_machine.cleanup()

    @pytest.mark.asyncio
    async def test_the_monitor_checks_once_per_tick(self, state_machine, monkeypatch):
        checks = {"n": 0}

        async def _check():
            checks["n"] += 1
            if checks["n"] >= 3:
                raise self._StopTheMonitor()

        async def _sleep(_delay):
            return None

        monkeypatch.setattr("backend.core.state.asyncio.sleep", _sleep)
        state_machine._check_inactivity = _check

        with pytest.raises(self._StopTheMonitor):
            await state_machine._monitor_inactivity()

        assert checks["n"] == 3

    @pytest.mark.asyncio
    async def test_a_check_that_raises_does_not_end_the_sweep(
        self, state_machine, monkeypatch, caplog
    ):
        """A source whose `stop()` blew up makes `transition_to_source` raise.

        Unguarded, that one failure ends the sweep for the life of the process
        and the appliance never returns to NONE again — no error, no banner, and
        the screen stays awake on a source nobody is listening to.
        """
        checks = {"n": 0}

        async def _check():
            checks["n"] += 1
            if checks["n"] >= 3:
                raise self._StopTheMonitor()
            raise RuntimeError("source would not stop")

        async def _sleep(_delay):
            return None

        monkeypatch.setattr("backend.core.state.asyncio.sleep", _sleep)
        state_machine._check_inactivity = _check

        with caplog.at_level(logging.ERROR):
            with pytest.raises(self._StopTheMonitor):
                await state_machine._monitor_inactivity()

        assert checks["n"] == 3
        assert "Inactivity check failed" in caplog.text

    @pytest.mark.asyncio
    async def test_a_cancelled_monitor_ends_quietly(self, state_machine, monkeypatch):
        """`cleanup()` cancels it during shutdown. Left to propagate, the
        CancelledError surfaces as a task exception on every single shutdown.
        """
        async def _sleep(_delay):
            raise asyncio.CancelledError

        monkeypatch.setattr("backend.core.state.asyncio.sleep", _sleep)

        await state_machine._monitor_inactivity()

    @pytest.mark.asyncio
    async def test_cleanup_cancels_the_sweep_and_forgets_it(self, state_machine):
        """Forgetting the reference is what lets a restarted lifespan start a
        fresh one; kept, `start_inactivity_monitor` sees a non-None task and
        never spawns again."""
        state_machine.start_inactivity_monitor()
        task = state_machine._inactivity_monitor_task

        state_machine.cleanup()
        await asyncio.sleep(0)

        assert task.cancelled() or task.done()
        assert state_machine._inactivity_monitor_task is None

    @pytest.mark.asyncio
    async def test_cleanup_without_a_monitor_is_a_no_op(self, state_machine):
        """Shutdown runs whether or not startup got as far as the monitor."""
        state_machine.cleanup()

        assert state_machine._inactivity_monitor_task is None


class TestShutdownSources:
    """The teardown entry that ends every source's mailbox."""

    async def test_every_registered_source_is_shut_down(self, state_machine):
        radio, spotify = Mock(), Mock()
        radio.shutdown = AsyncMock()
        spotify.shutdown = AsyncMock()
        state_machine.register_source(AudioSource.RADIO, radio)
        state_machine.register_source(AudioSource.SPOTIFY, spotify)

        await state_machine.shutdown_sources()

        radio.shutdown.assert_awaited_once()
        spotify.shutdown.assert_awaited_once()


class TestRerouteOfAFailedSource:
    async def test_a_reacquire_that_succeeds_clears_the_failure(self, state_machine, mock_source):
        """A failure is sticky against the source's own publishes, so the
        reroute's successful reacquire is what must lift it — otherwise the
        source plays (the reacquire is a full start) under a card that still
        says "Retry"."""
        mock_source.start = AsyncMock(return_value=False)
        mock_source.release_for_reroute = AsyncMock(return_value=True)
        mock_source.acquire_after_reroute = AsyncMock(return_value=True)
        state_machine.register_source(AudioSource.SPOTIFY, mock_source)
        state_machine.ALSA_RELEASE_SETTLE_S = 0
        await state_machine.transition_to_source(AudioSource.SPOTIFY)
        assert state_machine.get_current_state()["service"] == "failed"

        mock_source.view = session_view("Back")

        async def switch_output():
            pass

        await state_machine.reroute_active_source(switch_output)

        state = state_machine.get_current_state()
        assert (state["service"], state["service_error"]) == ("running", None)
        assert state["session"]["title"] == "Back"


class TestATeardownThatNeverEnds:
    async def test_the_lock_is_given_back_after_a_second_budget(
        self, state_machine, mock_source, caplog
    ):
        """Waiting for a cut teardown keeps the next start off the device; waiting
        for ever would freeze every source press, IR key and multiroom toggle
        behind the transition lock. One more budget, then an ERROR that names it."""
        async def stuck_stop():
            await asyncio.Event().wait()

        old_source = make_source(stop=stuck_stop)
        state_machine.register_source(AudioSource.BLUETOOTH, old_source)
        state_machine.register_source(AudioSource.SPOTIFY, mock_source)
        await state_machine.transition_to_source(AudioSource.BLUETOOTH)

        state_machine.TRANSITION_TIMEOUT = 0.05
        state_machine.TEARDOWN_GRACE = 0.05
        with caplog.at_level(logging.ERROR):
            async with asyncio.timeout(5):          # a hang guard, not a budget
                assert await state_machine.transition_to_source(AudioSource.SPOTIFY) is False
        assert "bluetooth" in caplog.text and "still stopping" in caplog.text
        async with asyncio.timeout(5):
            assert await state_machine.transition_to_source(AudioSource.NONE) is True
