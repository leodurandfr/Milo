# backend/tests/test_push_service.py
"""When PushService pushes, to which tokens, and what it refuses to push.

The mock stands for APNs and for the token registry's storage; the service's
own decisions are never mocked. What these guard is the one failure mode that
cannot be observed from this side: Apple throttles an app that pushes too
often, and the degradation outlives the code change that caused it.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.core.models.ws_events import (
    SourcePositionUpdate,
    SourceStateChanged,
    VolumeChanged,
)
from backend.core.push.apns_client import ApnsResult
from backend.core.push.models import ApnsEnvironment, PushToken, PushTokenKind
from backend.core.push.service import MIN_PUSH_INTERVAL_S, PushService

PLAYING = {
    "active_source": "spotify", "source_state": "active",
    "metadata": {"title": "T", "artist": "A", "duration": 200000,
                 "position": 1000, "is_playing": True},
}
STOPPED = {"active_source": "none", "source_state": "inactive", "metadata": {}}


def tok(kind, value, session_id=None):
    return PushToken(token=value, kind=kind, environment=ApnsEnvironment.SANDBOX,
                     device_id="phone-1", session_id=session_id, registered_at=1.0)


@pytest.fixture
def registry():
    held = {}
    reg = MagicMock()
    reg.held = held
    reg.tokens_for = lambda kind: [t for t in held.values() if t.kind == kind]
    reg.token_for_session = lambda sid: next(
        (t for t in held.values()
         if t.kind == PushTokenKind.SESSION and t.session_id == sid), None
    )
    reg.purge = AsyncMock(return_value=True)
    return reg


@pytest.fixture
def apns():
    client = MagicMock()
    client.available = True
    client.send = AsyncMock(return_value=ApnsResult(ok=True, status=200))
    return client


@pytest.fixture
def service(registry, apns):
    svc = PushService(token_registry=registry, apns_client=apns)
    machine = MagicMock()
    machine.get_current_state = MagicMock(return_value=dict(PLAYING))
    svc.set_state_machine(machine)
    svc.machine = machine
    return svc


def sent_types(apns):
    return [c.args[2] for c in apns.send.await_args_list]


def sent_events(apns):
    return [c.args[1]["aps"].get("event") for c in apns.send.await_args_list
            if "event" in c.args[1]["aps"]]


async def _settled(apns, deadline=3.0, settle=0.1):
    """Wait until a push has landed, then a little longer for a second one.

    Polls rather than sleeping a fixed budget: the assertion that follows is
    always a COUNT, never a latency, so a slow machine makes this slower and
    never makes it wrong. A fixed sleep is the shape that goes red in CI and
    green here.
    """
    step = 0.01
    waited = 0.0
    while apns.send.await_count == 0 and waited < deadline:
        await asyncio.sleep(step)
        waited += step
    await asyncio.sleep(settle)   # room for a second push, if the code emits one


class TestTriggers:
    """Which events on the bus are worth a push."""

    def test_a_position_tick_triggers_nothing(self, service):
        """The whole throughput argument. Position streams continuously during
        playback; pushing on it would be several per second, which is how an
        app's delivery gets throttled for everything it sends."""
        service.on_event(SourcePositionUpdate(source="spotify", position=1000, duration=2000))

        assert service._dirty.is_set() is False

    @pytest.mark.parametrize("event", [
        VolumeChanged(show_bar=True, step_mobile_db=2.0, multiroom_enabled=True, state={}),
        SourceStateChanged(source="spotify", new_state="active", metadata={}),
    ])
    def test_a_state_change_marks_the_service_dirty(self, service, event):
        service.on_event(event)

        assert service._dirty.is_set() is True

    def test_on_event_never_raises_into_broadcast(self, service):
        """It runs inside AudioStateMachine.broadcast, ahead of nothing but
        after the WebSocket fan-out — an exception here would surface as a
        state change the UI received and the rest of the backend did not."""
        service.on_event(MagicMock(spec=[]))  # not a WsEvent at all


class TestSessionLifecycle:
    """A session is 'Milō is playing something', and survives more than it ends."""

    async def test_a_start_goes_to_the_push_to_start_token(self, service, registry, apns):
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")

        await service._publish()

        assert sent_events(apns) == ["start"]
        assert apns.send.await_args_list[0].args[0].token == "pts"
        assert service._session_id is not None

    async def test_an_update_goes_to_the_session_token_not_the_starter(
        self, service, registry, apns
    ):
        """They are different tokens and APNs will not tell you when you mix
        them up: an update sent to the push-to-start token is accepted,
        delivered and ignored."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        await service._publish()
        session_id = service._session_id
        registry.held["sess"] = tok(PushTokenKind.SESSION, "sess", session_id=session_id)
        apns.send.reset_mock()

        await service._publish()

        assert sent_events(apns) == ["update"]
        assert apns.send.await_args_list[0].args[0].token == "sess"

    async def test_a_pause_does_not_end_the_session(self, service, registry, apns):
        """Ending on pause deletes the play button at the moment someone
        reaches for it. `is_playing` rides inside the attributes instead."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        await service._publish()
        session_id = service._session_id
        registry.held["sess"] = tok(PushTokenKind.SESSION, "sess", session_id=session_id)
        service.machine.get_current_state.return_value = {
            **PLAYING, "metadata": {**PLAYING["metadata"], "is_playing": False}
        }
        apns.send.reset_mock()

        await service._publish()

        assert service._session_id == session_id
        assert sent_events(apns) == ["update"]

    async def test_a_source_change_keeps_one_session(self, service, registry, apns):
        """The session is the listening session, not the source. Ending and
        restarting on every source change would flash the lock screen card."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        await service._publish()
        session_id = service._session_id
        registry.held["sess"] = tok(PushTokenKind.SESSION, "sess", session_id=session_id)
        service.machine.get_current_state.return_value = {**PLAYING, "active_source": "radio"}

        await service._publish()

        assert service._session_id == session_id

    async def test_playback_stopping_ends_the_session(self, service, registry, apns):
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        await service._publish()
        registry.held["sess"] = tok(
            PushTokenKind.SESSION, "sess", session_id=service._session_id)
        service.machine.get_current_state.return_value = dict(STOPPED)
        apns.send.reset_mock()

        await service._publish()

        assert service._session_id is None
        assert sent_events(apns) == ["end"]
        assert apns.send.await_args_list[0].args[1]["aps"]["attributes"].keys() == {"id"}

    async def test_a_refused_start_does_not_claim_a_session(self, service, registry, apns):
        """Recording a session the phone never opened would send every later
        update to a token nobody registered, silently and forever."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        apns.send.return_value = ApnsResult(ok=False, status=400, reason="BadDeviceToken",
                                            dead=True)

        await service._publish()

        assert service._session_id is None


class TestWidgetCadence:
    """The widget is budgeted separately and must not ride the 1 Hz cap."""

    async def test_a_volume_change_alone_pushes_no_widget(self, service, registry, apns):
        """A widget draws the track, not the level. Spending a push on a knob
        turn burns a budget Apple grants per day, not per second."""
        registry.held["w"] = tok(PushTokenKind.WIDGET, "w")
        await service._publish()          # first cycle: the signature is new
        apns.send.reset_mock()

        await service._publish()          # same state — nothing a widget shows moved

        assert sent_types(apns) == []

    async def test_a_track_change_pushes_the_widget(self, service, registry, apns):
        registry.held["w"] = tok(PushTokenKind.WIDGET, "w")
        await service._publish()
        service.machine.get_current_state.return_value = {
            **PLAYING, "metadata": {**PLAYING["metadata"], "title": "Another"}
        }
        apns.send.reset_mock()

        await service._publish()

        assert sent_types(apns) == ["widgets"]

    async def test_the_widget_push_carries_no_state(self, service, registry, apns):
        registry.held["w"] = tok(PushTokenKind.WIDGET, "w")

        await service._publish()

        assert apns.send.await_args_list[0].args[1] == {"aps": {"content-changed": True}}


class TestCoalescing:
    """One push per window, carrying the state the window ended on."""

    async def test_a_burst_collapses_into_one_push_carrying_the_last_state(
        self, service, registry, apns, monkeypatch
    ):
        """A rotary turn emits one volume_changed per detent. Without the
        window that is one push per detent, which is exactly the abuse that
        degrades delivery for the whole app — and the one that survives is the
        state the burst ended on, not the one that opened it.

        `on_event` is synchronous, so the 20 calls below cannot be preempted by
        the loop: the collapse is decided by the code, not by how fast this
        machine ran the test.
        """
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        monkeypatch.setattr("backend.core.push.service.MIN_PUSH_INTERVAL_S", 0.02)
        await service._publish()                       # open the session
        registry.held["sess"] = tok(
            PushTokenKind.SESSION, "sess", session_id=service._session_id)
        apns.send.reset_mock()
        await service.initialize()

        for i in range(20):
            service.machine.get_current_state.return_value = {
                **PLAYING, "metadata": {**PLAYING["metadata"], "title": f"T{i}"}
            }
            service.on_event(VolumeChanged(show_bar=True, step_mobile_db=2.0,
                                           multiroom_enabled=True, state={}))

        await _settled(apns)
        await service.cleanup()

        updates = [c.args[1] for c in apns.send.await_args_list
                   if c.args[2] == "nowplaying"]
        assert len(updates) == 1
        assert updates[0]["aps"]["attributes"]["currentTrack"]["title"] == "T19"

    async def test_events_spread_over_the_window_still_produce_one_push(
        self, service, registry, apns, monkeypatch
    ):
        """The window itself, which the test above cannot see.

        That one fires its burst through synchronous `on_event` calls, so the
        loop never gets to run between them and a single push proves nothing
        about coalescing — it happens even with no window at all (verified by
        mutation). A real burst arrives from separate `broadcast()` awaits, so
        the loop CAN wake between events; here the events are spread over
        actual yields, well inside one window. Without the sleep in `_loop`,
        every one of them reaches Apple.
        """
        registry.held["w"] = tok(PushTokenKind.WIDGET, "w")
        monkeypatch.setattr("backend.core.push.service.MIN_PUSH_INTERVAL_S", 0.4)
        await service.initialize()

        for i in range(10):
            service.machine.get_current_state.return_value = {
                **PLAYING, "metadata": {**PLAYING["metadata"], "title": f"T{i}"}
            }
            service.on_event(VolumeChanged(show_bar=True, step_mobile_db=2.0,
                                           multiroom_enabled=True, state={}))
            await asyncio.sleep(0.01)      # 0.1s of burst inside a 0.4s window

        await _settled(apns, settle=0.3)
        await service.cleanup()

        assert len(sent_types(apns)) == 1

    async def test_the_loop_survives_a_failing_cycle(self, service, registry, apns, monkeypatch):
        """A background loop that dies on one bad cycle stops pushing for the
        life of the process, with one line in a journal nobody reads."""
        monkeypatch.setattr("backend.core.push.service.MIN_PUSH_INTERVAL_S", 0.02)
        service.machine.get_current_state.side_effect = RuntimeError("boom")
        await service.initialize()

        service.on_event(VolumeChanged(show_bar=True, step_mobile_db=2.0,
                                       multiroom_enabled=True, state={}))
        await asyncio.sleep(0.15)

        service.machine.get_current_state.side_effect = None
        service.machine.get_current_state.return_value = dict(PLAYING)
        registry.held["w"] = tok(PushTokenKind.WIDGET, "w")
        service.on_event(VolumeChanged(show_bar=True, step_mobile_db=2.0,
                                       multiroom_enabled=True, state={}))
        await _settled(apns)
        await service.cleanup()

        assert sent_types(apns) == ["widgets"]


class TestPurge:
    """A dead token is dropped; a live one is never collateral."""

    async def test_a_dead_token_is_purged(self, service, registry, apns):
        registry.held["w"] = tok(PushTokenKind.WIDGET, "w")
        apns.send.return_value = ApnsResult(ok=False, status=410, reason="Unregistered",
                                            dead=True, invalidated_at=123.0)

        await service._publish()

        registry.purge.assert_awaited_once_with("w", invalidated_at=123.0)

    async def test_a_transient_failure_purges_nothing(self, service, registry, apns):
        """Apple unreachable is not a verdict about the token."""
        registry.held["w"] = tok(PushTokenKind.WIDGET, "w")
        apns.send.return_value = ApnsResult(ok=False, status=0, reason="Unreachable")

        await service._publish()

        registry.purge.assert_not_awaited()

    async def test_nothing_is_sent_without_a_signing_key(self, service, registry, apns):
        """Fail open: a unit with no key runs every other feature normally."""
        registry.held["w"] = tok(PushTokenKind.WIDGET, "w")
        apns.available = False

        await service._publish()

        apns.send.assert_not_awaited()


def test_the_interval_is_a_real_ceiling():
    """Guards the constant against being tuned to zero — at which point the
    coalescer is a no-op and every burst reaches Apple whole."""
    assert MIN_PUSH_INTERVAL_S >= 1.0
