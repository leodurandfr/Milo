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
from backend.core.push.service import (
    MIN_PUSH_INTERVAL_S,
    SESSION_IDLE_GRACE_S,
    START_REPORT_GRACE_S,
    PushService,
)

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
    reg.lost = set()
    reg.was_lost_to_reboot = lambda sid: sid in reg.lost

    def _newest_session_token():
        """Mirrors the real rule: a session belonging to a device that no longer
        holds a push-to-start token is an orphan, not a session."""
        live = {t.device_id for t in held.values()
                if t.kind == PushTokenKind.PUSH_TO_START}
        return max(
            (t for t in held.values()
             if t.kind == PushTokenKind.SESSION and t.device_id in live),
            key=lambda t: t.registered_at, default=None
        )

    reg.newest_session_token = _newest_session_token
    async def _unregister(token):
        return held.pop(token, None) is not None

    reg.unregister = _unregister
    reg.purge = AsyncMock(return_value=True)
    reg.mark_pushed = AsyncMock()
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


def sent_sessions(apns):
    """The session id each push addressed, read from the attributes it carried.

    A renewal is only a retry if it names the session already held; a different
    id would be the rival session this whole area exists to avoid.
    """
    return [c.args[1]["aps"]["attributes"].get("id")
            for c in apns.send.await_args_list
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

        await service._publish()          # arms the delay, and says "paused"
        service._idle_since -= SESSION_IDLE_GRACE_S + 1
        await service._publish()

        assert service._session_id is None
        # The paused snapshot rides ahead of the ending — see `_publish_paused`.
        assert sent_events(apns) == ["update", "end"]
        assert apns.send.await_args_list[1].args[1]["aps"]["attributes"].keys() == {"id"}

    async def test_a_refused_start_does_not_claim_a_session(self, service, registry, apns):
        """Recording a session the phone never opened would send every later
        update to a token nobody registered, silently and forever."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        apns.send.return_value = ApnsResult(ok=False, status=400, reason="BadDeviceToken",
                                            dead=True)

        await service._publish()

        assert service._session_id is None


class TestSessionAdoption:
    """A session can be opened from either end, and only one of them is shown.

    Measured on the appliance 2026-09-19: fifteen session tokens registered
    over an afternoon, every one of them for a session the app had opened, and
    not one for an id this service had minted — so `token_for_session` answered
    None on every cycle, `_update_session` returned in silence, and the lock
    screen kept whatever it was showing when the app went to the background.
    """

    async def test_a_session_the_app_opened_is_adopted(self, service, registry, apns):
        """The app opens one while it runs. Milō has to push updates to THAT
        one — the id it would mint itself addresses nothing."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        registry.held["sess"] = tok(PushTokenKind.SESSION, "sess", session_id="APP-1")

        await service._publish()

        assert service._session_id == "APP-1"
        assert sent_events(apns) == ["update"]
        assert apns.send.await_args_list[0].args[0].token == "sess"

    async def test_a_newer_registration_wins(self, service, registry, apns):
        """The app restarts and opens a second session; the first one's token
        stays in the registry until a push to it returns a 410. The freshest
        registration is the only statement about what exists now."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        registry.held["old"] = tok(PushTokenKind.SESSION, "old", session_id="APP-1")
        await service._publish()
        fresh = tok(PushTokenKind.SESSION, "new", session_id="APP-2")
        fresh.registered_at = 2.0
        registry.held["new"] = fresh

        await service._publish()

        assert service._session_id == "APP-2"

    async def test_a_leftover_token_does_not_steal_a_fresh_start(
        self, service, registry, apns
    ):
        """A start takes a second round trip to be reported back. Adopting in
        that window would hand every new session to the previous one's
        leftover token, which is the one case where this rule inverts."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        await service._publish()
        minted = service._session_id
        registry.held["stale"] = tok(PushTokenKind.SESSION, "stale", session_id="GONE")

        await service._publish()

        assert service._session_id == minted

    async def test_a_leftover_token_is_adopted_once_the_grace_expires(
        self, service, registry, apns
    ):
        """The other side of the same window: a start nobody ever reported is
        not a session, and holding it forever is how this got stuck."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        await service._publish()
        service._session_started_at -= START_REPORT_GRACE_S + 1
        registry.held["sess"] = tok(PushTokenKind.SESSION, "sess", session_id="OTHER")

        await service._publish()

        assert service._session_id == "OTHER"


class TestSourceTransitions:
    """Changing source on the appliance must not take the card off the screen."""

    TRANSITION = {"active_source": "none", "source_state": "inactive",
                  "transitioning": True, "metadata": {}}

    async def test_a_source_change_does_not_end_the_session(
        self, service, registry, apns
    ):
        """Between `Stopping radio` and `music_library started` the machine
        reads as nothing playing. Ending there emptied the lock screen on every
        source change, and it came back only when the app was relaunched."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        await service._publish()
        session_id = service._session_id
        registry.held["sess"] = tok(PushTokenKind.SESSION, "sess", session_id=session_id)
        service.machine.get_current_state.return_value = dict(self.TRANSITION)
        apns.send.reset_mock()

        await service._publish()

        assert service._session_id == session_id
        assert sent_events(apns) == []

    async def test_a_gap_between_stations_is_not_an_ending(
        self, service, registry, apns
    ):
        """Changing station stops one stream before starting the next, and the
        machine reads idle in between. Ending there took the card off the Lock
        Screen on every station change, and what replaced it was a session the
        app had never asked to be primary."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        await service._publish()
        session_id = service._session_id
        registry.held["sess"] = tok(PushTokenKind.SESSION, "sess", session_id=session_id)
        service.machine.get_current_state.return_value = dict(STOPPED)
        await service._publish()
        service.machine.get_current_state.return_value = dict(PLAYING)
        apns.send.reset_mock()

        await service._publish()

        assert service._session_id == session_id
        assert sent_events(apns) == ["update"]

    async def test_the_gap_is_published_as_paused(self, service, registry, apns):
        """Holding the session through the gap is what keeps the card on screen,
        but on its own it left the card claiming the previous source was still
        playing — nothing is published while the state reads idle."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        await service._publish()
        registry.held["sess"] = tok(
            PushTokenKind.SESSION, "sess", session_id=service._session_id)
        service.machine.get_current_state.return_value = dict(STOPPED)
        apns.send.reset_mock()

        await service._publish()

        assert sent_events(apns) == ["update"]
        attributes = apns.send.await_args_list[0].args[1]["aps"]["attributes"]
        assert attributes["isPlaying"] is False
        assert attributes["currentTrack"]["title"] == "T"

    async def test_a_real_stop_still_ends_it(self, service, registry, apns):
        """The other side of the delay: a session that outlived playback for
        good would keep Milō on a Lock Screen it no longer owns."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        await service._publish()
        registry.held["sess"] = tok(
            PushTokenKind.SESSION, "sess", session_id=service._session_id)
        service.machine.get_current_state.return_value = dict(STOPPED)
        await service._publish()
        service._idle_since -= SESSION_IDLE_GRACE_S + 1
        apns.send.reset_mock()

        await service._publish()

        assert service._session_id is None
        assert sent_events(apns) == ["end"]

    async def test_an_ended_session_is_not_adopted_back(self, service, registry, apns):
        """`_end_session` clears the id, and the token it could be adopted from
        outlives it. Following it put every later update on a session this
        service had itself just killed, and `_start_session` was never reached
        again."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        await service._publish()
        registry.held["sess"] = tok(
            PushTokenKind.SESSION, "sess", session_id=service._session_id)
        ended = service._session_id
        service.machine.get_current_state.return_value = dict(STOPPED)
        await service._publish()
        service._idle_since -= SESSION_IDLE_GRACE_S + 1
        await service._publish()
        service.machine.get_current_state.return_value = dict(PLAYING)
        apns.send.reset_mock()

        await service._publish()

        assert service._session_id != ended
        assert sent_events(apns) == ["start"]

    async def test_ending_drops_the_token_that_only_addressed_it(
        self, service, registry, apns
    ):
        """It is the one token whose death this side witnesses. Fifteen of them
        had piled up in the registry by the afternoon of 2026-09-19."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        await service._publish()
        registry.held["sess"] = tok(
            PushTokenKind.SESSION, "sess", session_id=service._session_id)
        service.machine.get_current_state.return_value = dict(STOPPED)

        await service._publish()
        service._idle_since -= SESSION_IDLE_GRACE_S + 1
        await service._publish()

        assert "sess" not in registry.held


class TestDeviceReboot:
    """A restart destroys every session on the phone and tells nobody."""

    async def test_a_session_the_phone_lost_to_a_reboot_is_let_go(
        self, service, registry, apns
    ):
        """Holding it meant updating a session that no longer existed, forever,
        while APNs answered 200 to every push. Measured 2026-09-19: phone
        restarted with music playing, not one `start` sent afterwards."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        await service._publish()
        registry.held["sess"] = tok(
            PushTokenKind.SESSION, "sess", session_id=service._session_id)
        lost = service._session_id
        del registry.held["sess"]
        registry.lost.add(lost)
        apns.send.reset_mock()

        await service._publish()          # notices, lets go
        await service._publish()          # starts a real one

        assert service._session_id not in (None, lost)
        assert sent_events(apns) == ["start"]

    async def test_a_token_that_is_merely_late_keeps_its_session(
        self, service, registry, apns
    ):
        """The other silence, and it wants the opposite. The registration comes
        from an app extension whose only route to Milō is an mDNS lookup that
        fails now and then — `token 8a3983dc rotation → unavailable`, 18:13:49.
        Giving up on it opened a rival session two seconds later that the phone
        showed and nobody fed, and the commands went to the one with nothing
        behind it."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        await service._publish()
        started = service._session_id
        service._session_started_at -= START_REPORT_GRACE_S + 1
        apns.send.reset_mock()

        await service._publish()

        assert service._session_id == started

    async def test_the_wait_knocks_again_instead_of_lasting_forever(
        self, service, registry, apns
    ):
        """Waiting in silence could never end: the extension is woken by
        `update`, Milō sends none without a token, and only the woken extension
        can supply one. Measured 2026-09-19 — `token de session : Milō
        injoignable` at 18:53:00, then 98 seconds of nothing, until the app was
        brought to the foreground by hand at 18:54:38.

        `start` is the one message that reaches the phone without a session
        token, so it is what breaks the deadlock. Carrying the SAME id keeps it
        a retry rather than a second session."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        await service._publish()
        started = service._session_id
        service._session_started_at -= START_REPORT_GRACE_S + 1
        apns.send.reset_mock()

        await service._publish()

        assert sent_events(apns) == ["start"]
        assert service._session_id == started
        assert sent_sessions(apns) == [started]

    async def test_the_knocking_is_spaced_out(self, service, registry, apns):
        """Each renewal wakes an app extension and spends APNs budget, and the
        token it fishes for needs a moment to come back. Sending one per cycle
        would be a push per second for as long as the token stayed away."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        await service._publish()
        service._session_started_at -= START_REPORT_GRACE_S + 1
        apns.send.reset_mock()

        await service._publish()      # knocks
        await service._publish()      # too soon to knock again
        await service._publish()

        assert sent_events(apns) == ["start"]

    async def test_a_session_nobody_reports_is_let_go(
        self, service, registry, apns
    ):
        """The ghost: the session died without the phone restarting, so its
        token outlived it. APNs answers 200, the phone discards the payload, and
        no `start` is ever sent because a token exists. Measured 2026-09-19 —
        `Could not find the specified now playing client` on the phone while
        this side logged `session 2eb3b71b adopted (was None)`.

        Once the app reports what it really holds, the stale token goes, and the
        renewal path above is what brings the card back."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        await service._publish()
        started = service._session_id
        registry.held["sess"] = tok(
            PushTokenKind.SESSION, "sess", session_id=started)
        apns.send.reset_mock()

        # The device says it holds no session at all — the report that retires
        # a ghost. Silence would say nothing; this says something.
        del registry.held["sess"]
        service._session_started_at -= START_REPORT_GRACE_S + 1

        await service._publish()

        assert sent_events(apns) == ["start"]


class TestRadioMetadata:
    """Radio leaves the common floor empty and carries the track beside it."""

    RADIO = {
        "active_source": "radio", "source_state": "active",
        "metadata": {"is_playing": True, "station_name": "FIP Jazz",
                     "track_title": "Snibor", "track_artist": "Gil Evans",
                     "favicon": "/api/radio/images/7ff7.webp"},
    }

    async def test_a_station_reaches_the_lock_screen(self, service, registry, apns):
        """Reading `title` alone sent a push whose every field was null, which
        on the phone is a session with no track at all."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        service.machine.get_current_state.return_value = dict(self.RADIO)

        await service._publish()

        track = apns.send.await_args_list[0].args[1]["aps"]["attributes"]["currentTrack"]
        assert track["title"] == "Snibor"
        assert track["artist"] == "Gil Evans"
        assert track["album"] == "FIP Jazz"
        assert track["artworkURL"] == "/api/radio/images/7ff7.webp"

    async def test_a_station_change_spends_a_widget_push(self, service, registry, apns):
        """Through the floor alone every station looked identical, so the
        widget signature never moved and the change cost no push — and the
        widget kept the old station until its own timeline came round."""
        registry.held["widget"] = tok(PushTokenKind.WIDGET, "widget")
        service.machine.get_current_state.return_value = dict(self.RADIO)
        await service._publish()
        apns.send.reset_mock()
        service.machine.get_current_state.return_value = {
            **self.RADIO,
            "metadata": {**self.RADIO["metadata"], "track_title": "Blues For Pablo"},
        }

        await service._publish()

        assert [c.args[0].token for c in apns.send.await_args_list] == ["widget"]


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

    async def test_a_delivered_push_is_stamped_on_the_token(self, service, registry, apns):
        """`last_push_at` is the registry's only observable: there is no read
        route, so an operator diagnosing "the widget never updates" has nothing
        but this file. It sat at null through three real 200s from the unit on
        2026-09-19 because nothing called mark_pushed — the field existed and
        answered no question."""
        registry.held["w"] = tok(PushTokenKind.WIDGET, "w")

        await service._publish()

        registry.mark_pushed.assert_awaited_once_with(["w"])

    async def test_a_refused_push_is_not_stamped(self, service, registry, apns):
        """Stamping a send Apple refused would make the file say the opposite
        of what happened — the token would read as recently used at the exact
        moment it stopped working."""
        registry.held["w"] = tok(PushTokenKind.WIDGET, "w")
        apns.send.return_value = ApnsResult(ok=False, status=0, reason="Unreachable")

        await service._publish()

        registry.mark_pushed.assert_not_awaited()

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
