# backend/tests/test_base_audio_source.py
"""
Unit tests for BaseAudioSource.

Tests cover:
- BaseAudioSource inheritance
- Status format
- BaseAudioSource lifecycle
"""
import asyncio
import logging

import pytest
from unittest.mock import Mock, AsyncMock
from pydantic import BaseModel, Field

from backend.core.audio_source import BaseAudioSource
from backend.core.models.audio_state import AudioSource, SourceState
from backend.core.state import AudioStateMachine


class _ValueParams(BaseModel):
    value: int = Field(ge=0)


class ConcreteAudioSource(BaseAudioSource):
    """Concrete implementation for testing."""

    COMMANDS = {"test_command": None, "validated_command": _ValueParams}

    def __init__(self, start_success=True, stop_success=True):
        super().__init__(
            source_id="test",
            service_name="milo-test",
        )
        self._start_success = start_success
        self._stop_success = stop_success
        self.start_called = False
        self.stop_called = False

    async def _do_start(self) -> bool:
        self.start_called = True
        if self._start_success:
            self.set_state(SourceState.ACTIVE, {"connected": True})
        return self._start_success

    async def _do_stop(self) -> bool:
        self.stop_called = True
        return self._stop_success

    async def _handle_command(self, cmd, params):
        if cmd == "test_command":
            return self.success_response("Command executed")
        if cmd == "validated_command":
            return self.success_response("Validated", value=params.value)
        return self.error_response(f"Unhandled command: {cmd}")


class SilentStartSource(ConcreteAudioSource):
    """A source whose `_do_start()` succeeds without announcing a state.

    Not a contrived case: `_do_start` is only *expected* to publish READY or
    ACTIVE, nothing enforces it, and a source that hands back True after
    launching its unit has done its job. `ConcreteAudioSource` always publishes
    ACTIVE, which is why the default below was reached by no test.
    """

    async def _do_start(self) -> bool:
        self.start_called = True
        return True



class TestBaseAudioSourceLifecycle:
    """Test BaseAudioSource lifecycle methods."""

    @pytest.mark.asyncio
    async def test_start_success(self):
        """Test successful start."""
        source = ConcreteAudioSource()

        result = await source.start()

        assert result is True
        assert source.start_called
        assert source.state == SourceState.ACTIVE


    @pytest.mark.asyncio
    async def test_a_start_that_announced_no_state_lands_on_ready(self):
        """`start()` owes every source a resting state, and READY is it.

        What breaks when this fails: a source whose `_do_start()` succeeded
        without publishing a state stays in STARTING for good — `start()`
        answered True, the unit is running, and the source never becomes
        selectable. This is the state contract `BaseAudioSource` holds for all
        twelve sources, and `audio_source.py:186` is the whole of it.

        The complement is already pinned by `test_start_success`: a `_do_start`
        that published ACTIVE keeps it, which is why the default is guarded by
        `== STARTING` rather than applied unconditionally.
        """
        source = SilentStartSource()

        result = await source.start()

        assert result is True
        assert source.state == SourceState.READY

    @pytest.mark.asyncio
    async def test_start_failure(self):
        """Test failed start."""
        source = ConcreteAudioSource(start_success=False)

        result = await source.start()

        assert result is False
        assert source.state == SourceState.ERROR

    @pytest.mark.asyncio
    async def test_stop_success(self):
        """Test successful stop."""
        source = ConcreteAudioSource()
        await source.start()

        result = await source.stop()

        assert result is True
        assert source.stop_called
        assert source.state == SourceState.READY

    @pytest.mark.asyncio
    async def test_stop_failure(self):
        """Test failed stop."""
        source = ConcreteAudioSource(stop_success=False)
        await source.start()

        result = await source.stop()

        assert result is False

    @pytest.mark.asyncio
    async def test_do_restart_success(self):
        """Test the default _do_restart() (stop + start) behind _on_auto_stop."""
        source = ConcreteAudioSource()
        await source.start()
        source.start_called = False
        source.stop_called = False

        result = await source._do_restart()

        assert result is True
        assert source.stop_called
        assert source.start_called

    @pytest.mark.asyncio
    async def test_do_restart_failure_on_stop(self):
        """Test _do_restart() fails if stop fails."""
        source = ConcreteAudioSource(stop_success=False)
        await source.start()

        result = await source._do_restart()

        assert result is False


class TestBaseAudioSourceCommand:
    """Test BaseAudioSource command method."""

    @pytest.mark.asyncio
    async def test_known_command(self):
        """Test handling known command."""
        source = ConcreteAudioSource()

        result = await source.command("test_command", {})

        assert result["success"] is True
        assert "message" in result

    @pytest.mark.asyncio
    async def test_unknown_command(self):
        """Unknown command is rejected centrally by command()."""
        source = ConcreteAudioSource()

        result = await source.command("unknown", {})

        assert result["success"] is False
        assert "Unknown command" in result["error"]

    @pytest.mark.asyncio
    async def test_none_data_treated_as_empty(self):
        """data=None (explicit {"data": null} on the wire) is coerced to {}."""
        source = ConcreteAudioSource()

        result = await source.command("test_command", None)

        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_valid_params_reach_handler(self):
        """Validated params are passed to the handler as a typed model."""
        source = ConcreteAudioSource()

        result = await source.command("validated_command", {"value": 5})

        assert result["success"] is True
        assert result["value"] == 5

    @pytest.mark.asyncio
    async def test_invalid_params_rejected(self):
        """Out-of-range params fail validation and never reach the handler."""
        source = ConcreteAudioSource()

        result = await source.command("validated_command", {"value": -1})

        assert result["success"] is False
        assert "Invalid parameters" in result["error"]

    @pytest.mark.asyncio
    async def test_missing_required_param_rejected(self):
        """Missing required field fails validation."""
        source = ConcreteAudioSource()

        result = await source.command("validated_command", {})

        assert result["success"] is False
        assert "error" in result


class TestBaseAudioSourceHelpers:
    """Test BaseAudioSource helper methods."""

    def test_success_response(self):
        """Test success_response helper."""
        source = ConcreteAudioSource()

        response = source.success_response("Test message", extra="data")

        assert response["success"] is True
        assert response["message"] == "Test message"
        assert response["extra"] == "data"

    def test_success_response_no_message(self):
        """Test success_response without message."""
        source = ConcreteAudioSource()

        response = source.success_response()

        assert response["success"] is True
        assert "message" not in response

    def test_error_response(self):
        """Test error_response helper."""
        source = ConcreteAudioSource()

        response = source.error_response("Test error", code=500)

        assert response["success"] is False
        assert response["error"] == "Test error"
        assert response["code"] == 500

    def test_set_state(self):
        """Test set_state helper."""
        source = ConcreteAudioSource()

        source.set_state(SourceState.ACTIVE, {"key": "value"})

        assert source.state == SourceState.ACTIVE
        assert source.metadata["key"] == "value"


class TestSetStateMetadataSemantics:
    """set_state() must leave the source's metadata copy identical to the one
    the state machine stores.

    The two are maintained independently, and two paths copy the source's copy
    back into the machine — the post-start resync and refresh_active_metadata()
    on every WS handshake. So any divergence (a merge here against the
    machine's replace) resurrects fields the machine had already dropped, at a
    moment no source chose.
    """

    @staticmethod
    async def _publish(source, state, metadata):
        """Run set_state and drain the update it spawns at the machine."""
        spawned = []
        source._bg.spawn = Mock(
            side_effect=lambda coro, **kw: spawned.append(asyncio.ensure_future(coro))
        )
        source.set_state(state, metadata)
        await asyncio.gather(*spawned)

    @pytest.fixture
    def wired(self):
        """A real state machine with the source registered and active."""
        state_machine = AudioStateMachine()
        source = ConcreteAudioSource()
        source.source_id = AudioSource.RADIO.value
        source.state_machine = state_machine
        state_machine.register_source(AudioSource.RADIO, source)
        state_machine.system_state.active_source = AudioSource.RADIO
        return source, state_machine

    @pytest.mark.asyncio
    async def test_successive_states_agree(self, wired):
        """A second, narrower payload must not leave the earlier track behind
        on the source while the machine has already dropped it."""
        source, state_machine = wired

        await self._publish(
            source, SourceState.ACTIVE,
            {"title": "Track", "artist": "Artist", "is_playing": True},
        )
        await self._publish(
            source, SourceState.READY, {"is_playing": False},
        )

        assert source.metadata == state_machine.system_state.metadata

    @pytest.mark.asyncio
    async def test_state_only_change_agrees(self, wired):
        """metadata=None is a state-only change on both sides (the multiroom
        reroute flips to STARTING this way to keep the track on screen)."""
        source, state_machine = wired

        await self._publish(
            source, SourceState.ACTIVE, {"title": "Track", "is_playing": True},
        )
        published = dict(state_machine.system_state.metadata)
        await self._publish(source, SourceState.STARTING, None)

        assert source.metadata == state_machine.system_state.metadata == published


class TestErrorMechanismsStaySeparate:
    """A failed *operation* is a banner; a source that is *down* is a state.

    Both used to ride on `source/state_changed` with new_state "error", which
    made a station that would not tune indistinguishable on the wire from a
    dead daemon — and left the real state unreachable, since the injected
    full_state carried the previous one anyway.
    """

    @pytest.fixture
    def wired(self):
        """A real state machine with the source registered and active."""
        state_machine = AudioStateMachine()
        state_machine.ws_manager = Mock()
        state_machine.ws_manager.broadcast_dict = AsyncMock()
        source = ConcreteAudioSource()
        source.source_id = AudioSource.RADIO.value
        source.state_machine = state_machine
        state_machine.register_source(AudioSource.RADIO, source)
        state_machine.system_state.active_source = AudioSource.RADIO
        return source, state_machine

    @pytest.mark.asyncio
    async def test_broadcast_error_sends_the_banner_and_no_state(self, wired):
        """broadcast_error() emits source/error and touches no state."""
        source, state_machine = wired
        spawned = []
        source._bg.spawn = Mock(
            side_effect=lambda coro, **kw: spawned.append(asyncio.ensure_future(coro))
        )

        source.broadcast_error("Unable to load stream: FIP")
        await asyncio.gather(*spawned)

        envelope = state_machine.ws_manager.broadcast_dict.call_args[0][0]
        assert (envelope["category"], envelope["type"]) == ("source", "error")
        assert envelope["data"]["message"] == "Unable to load stream: FIP"

        # The source is still perfectly usable — its browser, its commands.
        assert source.state != SourceState.ERROR
        assert state_machine.system_state.source_state != SourceState.ERROR
        assert state_machine.system_state.error is None

    @pytest.mark.asyncio
    async def test_broadcast_error_cleared_dismisses_the_banner(self, wired):
        """broadcast_error_cleared() emits source/error_cleared after a banner,
        and stays silent when no banner is up.

        The count of envelopes is asserted before their content: the
        `not self._error_active` guard makes a method that does nothing
        indistinguishable from one that cleared correctly, so a no-op passes
        any assertion written on the last envelope alone.
        """
        source, state_machine = wired
        spawned = []
        source._bg.spawn = Mock(
            side_effect=lambda coro, **kw: spawned.append(asyncio.ensure_future(coro))
        )

        # Nothing was broadcast, so clearing must produce no wire noise at all.
        source.broadcast_error_cleared()
        assert spawned == []

        source.broadcast_error("Unable to load stream: FIP")
        source.broadcast_error_cleared()
        await asyncio.gather(*spawned)

        envelopes = [
            call.args[0]
            for call in state_machine.ws_manager.broadcast_dict.call_args_list
        ]
        assert len(envelopes) == 2
        assert (envelopes[1]["category"], envelopes[1]["type"]) == (
            "source", "error_cleared",
        )
        assert envelopes[1]["data"]["source"] == AudioSource.RADIO.value


class TestPositionUpdateReachesBothSinks:
    """broadcast_position_update() has two effects, and losing either one is
    invisible from the other.

    The wire event is the frontend's drift correction; the write into
    system_state.metadata is what a WebSocket connecting mid-track receives in
    its initial_state. A method that only broadcast would hand every freshly
    opened tab the position the track started from.
    """

    @pytest.fixture
    def wired(self):
        """A real state machine with the source registered and active."""
        state_machine = AudioStateMachine()
        state_machine.ws_manager = Mock()
        state_machine.ws_manager.broadcast_dict = AsyncMock()
        source = ConcreteAudioSource()
        source.source_id = AudioSource.RADIO.value
        source.state_machine = state_machine
        state_machine.register_source(AudioSource.RADIO, source)
        state_machine.system_state.active_source = AudioSource.RADIO
        return source, state_machine

    @pytest.mark.asyncio
    async def test_position_update_syncs_state_then_broadcasts(self, wired):
        """Both sinks carry position and duration, not just the wire."""
        source, state_machine = wired
        spawned = []
        source._bg.spawn = Mock(
            side_effect=lambda coro, **kw: spawned.append(asyncio.ensure_future(coro))
        )

        source.broadcast_position_update(42000, 180000)
        await asyncio.gather(*spawned)

        assert state_machine.system_state.metadata["position"] == 42000
        assert state_machine.system_state.metadata["duration"] == 180000

        envelope = state_machine.ws_manager.broadcast_dict.call_args.args[0]
        assert (envelope["category"], envelope["type"]) == (
            "source", "position_update",
        )
        assert envelope["data"]["position"] == 42000
        assert envelope["data"]["duration"] == 180000


class TestSourceStateValues:
    """Test SourceState enum values."""

    def test_state_values(self):
        """Test state values match expected strings."""
        assert SourceState.STARTING.value == "starting"
        assert SourceState.READY.value == "ready"
        assert SourceState.ACTIVE.value == "active"
        assert SourceState.ERROR.value == "error"


class TestBaseAudioSourceServiceManager:
    """Test BaseAudioSource systemd service management."""

    @pytest.mark.asyncio
    async def test_start_service(self):
        """Test _start_service helper."""
        source = ConcreteAudioSource()
        source._service_manager = Mock()
        source._service_manager.start = AsyncMock(return_value=True)

        result = await source._start_service()

        assert result is True
        source._service_manager.start.assert_called_once_with("milo-test")

    @pytest.mark.asyncio
    async def test_stop_service(self):
        """Test _stop_service helper."""
        source = ConcreteAudioSource()
        source._service_manager = Mock()
        source._service_manager.stop = AsyncMock(return_value=True)

        result = await source._stop_service()

        assert result is True
        source._service_manager.stop.assert_called_once_with("milo-test")

    @pytest.mark.asyncio
    async def test_is_service_active(self):
        """Test _is_service_active helper."""
        source = ConcreteAudioSource()
        source._service_manager = Mock()
        source._service_manager.is_active = AsyncMock(return_value=True)

        result = await source._is_service_active()

        assert result is True


class TestBaseAudioSourceProperties:
    """Test BaseAudioSource properties."""

    def test_state_property(self):
        """Test state property."""
        source = ConcreteAudioSource()

        assert source.state == SourceState.READY

        source._state = SourceState.ACTIVE
        assert source.state == SourceState.ACTIVE

    def test_metadata_property_returns_copy(self):
        """Test metadata property returns a copy."""
        source = ConcreteAudioSource()
        source._metadata = {"key": "value"}

        metadata = source.metadata
        metadata["new_key"] = "new_value"

        # Original should not be modified
        assert "new_key" not in source._metadata


class TestBaseAudioSourceInheritance:
    """Test that BaseAudioSource subclasses are properly typed."""

    def test_concrete_source_is_base_audio_source(self):
        """Test ConcreteAudioSource inherits from BaseAudioSource."""
        source = ConcreteAudioSource()
        assert isinstance(source, BaseAudioSource)

    def test_base_source_has_required_attributes(self):
        """Test BaseAudioSource has required attributes."""
        source = ConcreteAudioSource()

        assert hasattr(source, 'source_id')
        assert hasattr(source, 'service_name')
        assert source.source_id == "test"
        assert source.service_name == "milo-test"


class TestServiceHelperFailureArms:
    """The five systemd wrappers every source inherits, when systemd says no.

    Each one is `try: await manager.X(name) except: log + False`, and every
    `except` arm was at zero — as was the "no unit name" early return that the
    metadata-only sources (Bluetooth, DLNA) take on every call.

    What the arms buy: `SystemdServiceManager` already answers False for a
    non-zero systemctl, so an exception here is a *broken* privileged path — a
    sudoers rule that stopped matching, a unit renamed on one side only. Swallowed
    into the caller's `_do_start`, the source reports ERROR with no explanation in
    the journal; re-raised, it escapes into `start()`'s generic handler and every
    source failure reads the same.
    """

    def _source_with(self, service_name="milo-test"):
        source = ConcreteAudioSource()
        source.service_name = service_name
        source._service_manager = Mock()
        return source

    @pytest.mark.parametrize("helper,manager_method", [
        ("_start_service", "start"),
        ("_stop_service", "stop"),
        ("_restart_service", "restart"),
        ("_is_service_active", "is_active"),
    ])
    @pytest.mark.asyncio
    async def test_a_raising_manager_answers_false_and_names_the_unit(
        self, helper, manager_method, caplog
    ):
        source = self._source_with()
        setattr(source._service_manager, manager_method,
                AsyncMock(side_effect=OSError("sudo: a password is required")))

        with caplog.at_level(logging.ERROR):
            assert await getattr(source, helper)() is False

        assert "milo-test" in caplog.text
        assert "a password is required" in caplog.text

    @pytest.mark.parametrize("helper,manager_method", [
        ("_start_service", "start"),
        ("_stop_service", "stop"),
        ("_restart_service", "restart"),
        ("_is_service_active", "is_active"),
    ])
    @pytest.mark.asyncio
    async def test_a_source_with_no_unit_succeeds_without_calling_systemd(
        self, helper, manager_method
    ):
        """Bluetooth and DLNA own no systemd unit; their `service_name` is None.

        Answered False, every one of their starts and stops would fail. Passed to
        systemd, `systemctl start None` is a spawn with a nonsense argument on a
        path the sudoers policy scopes by unit name.
        """
        source = self._source_with(service_name=None)
        setattr(source._service_manager, manager_method, AsyncMock())

        assert await getattr(source, helper)() is True

        getattr(source._service_manager, manager_method).assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_explicit_unit_name_overrides_the_source_own(self):
        """Spotify stops go-librespot's unit, not its own name.

        A helper that ignored the argument would act on the caller's unit — the
        wrong one — and report success.
        """
        source = self._source_with()
        source._service_manager.stop = AsyncMock(return_value=True)

        assert await source._stop_service("milo-go-librespot") is True

        source._service_manager.stop.assert_awaited_once_with("milo-go-librespot")

    @pytest.mark.asyncio
    async def test_a_restart_that_failed_is_not_waited_out(self):
        """`_restart_service_and_wait` settles only after a restart that worked.

        Sleeping anyway adds half a second to every failed source switch, and —
        worse — the caller reads True from the settle instead of False from the
        restart.
        """
        source = self._source_with()
        source._service_manager.restart = AsyncMock(return_value=False)
        slept = []

        async def _sleep(delay):
            slept.append(delay)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("backend.core.audio_source.asyncio.sleep", _sleep)
            assert await source._restart_service_and_wait() is False

        assert slept == []

    @pytest.mark.asyncio
    async def test_a_successful_restart_settles_before_answering(self):
        """The settle is what makes the state machine's post-start resync read a
        unit that has actually come up rather than one still forking."""
        source = self._source_with()
        source._service_manager.restart = AsyncMock(return_value=True)
        slept = []

        async def _sleep(delay):
            slept.append(delay)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("backend.core.audio_source.asyncio.sleep", _sleep)
            assert await source._restart_service_and_wait(settle=0.25) is True

        assert slept == [0.25]


class TestStartFailureArm:
    """`start()` when `_do_start` raises rather than answers.

    `AudioStateMachine.transition_to_source` reads the boolean and the state; a
    raise that reached it instead would abort the transition itself, leaving
    `transitioning` set and every later update dropped rather than buffered.
    """

    @pytest.mark.asyncio
    async def test_a_raising_start_is_contained_and_lands_in_error(self, caplog):
        source = ConcreteAudioSource()

        async def _boom():
            raise RuntimeError("loopback device busy")

        source._do_start = _boom

        with caplog.at_level(logging.ERROR):
            assert await source.start() is False

        assert source.state == SourceState.ERROR
        assert "loopback device busy" in caplog.text

    @pytest.mark.asyncio
    async def test_the_recorded_reason_has_no_reader(self):
        """A constat, asserted so it stays true: `_error` is write-only.

        `start()` stores the exception text (and "Start failed"), `stop()` clears
        it, and nothing in either application reads it back — there is no
        property, no serialiser, no consumer. What `GET /api/audio/state` shows
        as `error` is `SystemAudioState.error`, which the state machine sets from
        the transition or from `metadata["error"]`, and the UI banner is the
        separate `broadcast_error` / `_error_active` mechanism that
        `TestErrorMechanismsStaySeparate` above pins.

        Left as is rather than removed: four inert assignments in the ABC every
        source inherits, and touching that file means the audio-path checklist
        for a change that provably alters nothing. This test is the record, and
        it turns red the day someone gives the field a reader — which is the
        moment to decide what it should mean.
        """
        source = ConcreteAudioSource()

        async def _boom():
            raise RuntimeError("loopback device busy")

        source._do_start = _boom
        await source.start()

        assert source._error == "loopback device busy"
        assert not hasattr(type(source), "error"), \
            "the write-only field grew a reader — see this test's docstring"


class TestCommandFailureArm:
    """`command()` when the handler raises."""

    @pytest.mark.asyncio
    async def test_a_raising_handler_answers_an_error_envelope(self, caplog):
        """`run_source_command` turns the envelope into the HTTP response.

        A raise escaping here becomes a 500 on a transport button, where the
        contract is `{"success": False, "error": ...}` and the UI shows the
        reason next to the control the user pressed.
        """
        source = ConcreteAudioSource()

        async def _boom(cmd, params):
            raise RuntimeError("mpv socket gone")

        source._handle_command = _boom

        with caplog.at_level(logging.ERROR):
            result = await source.command("test_command", None)

        assert result["success"] is False
        assert "mpv socket gone" in result["error"]
        assert "Error handling command test_command" in caplog.text


class TestInitializeContract:
    """`BaseAudioSource.initialize()` — the default every source inherits.

    Entered by the boot but held by nothing (it came out ESCAPED when gutted):
    `dependencies.py::init_async` awaits it for four sources and only logs the
    outcome, so a default that answered False would be reported as a failed
    init on every boot, and one that skipped the flag would leave
    `is_initialized` False forever.
    """

    @pytest.mark.asyncio
    async def test_the_default_initialize_succeeds_and_records_that_it_ran(self):
        source = ConcreteAudioSource()
        assert source.is_initialized is False

        assert await source.initialize() is True

        assert source.is_initialized is True

    @pytest.mark.asyncio
    async def test_initialize_is_idempotent(self):
        """`init_async` runs once, but a source may also be initialized by its
        own routes on first access; the second call must not report failure."""
        source = ConcreteAudioSource()
        await source.initialize()

        assert await source.initialize() is True
        assert source.is_initialized is True
