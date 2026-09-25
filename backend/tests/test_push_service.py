# backend/tests/test_push_service.py
"""When PushService pushes, to which tokens, and what it refuses to push.

The mock stands for APNs and for the token registry's storage; the service's
own decisions are never mocked. What these guard is the one failure mode that
cannot be observed from this side: Apple throttles an app that pushes too
often, and the degradation outlives the code change that caused it.
"""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.core.models.volume import VolumeConfig
from backend.core.models.volume_state import ClientVolume, VolumeState
from backend.core.models.audio_wire import PositionAnchor
from backend.core.models.ws_events import (
    AudioStateChanged,
    SourcePosition,
    VolumeChanged,
)
from backend.core.push.apns_client import ApnsResult
from backend.core.push.models import ApnsEnvironment, PushToken, PushTokenKind
from backend.core.state import AudioStateMachine
from backend.core.push.service import (
    MIN_PUSH_INTERVAL_S,
    SESSION_IDLE_GRACE_S,
    START_REPORT_GRACE_S,
    PushService,
)


def session_of(title=None, phase="playing", **fields):
    """A live session as the state carries it."""
    return {"id": "s-1", "phase": phase, "title": title, "artist": None, "album": None,
            "artwork": None, "senders": [], "duration_ms": None, "position": None,
            **fields}


def resume_of(title, **fields):
    """A resume point as the state carries it."""
    return {"title": title, "artist": None, "album": None, "artwork": None,
            "duration_ms": None, "position_ms": None, **fields}


def state_of(source, session=None, resume=None, switching=False):
    """The audio state (GET /api/audio/state), the keys this service reads."""
    return {
        "source": source, "switching": switching,
        "service": "stopped" if source == "none" else "running",
        "service_error": None, "session": session, "controls": [],
        "resume": None if session else resume, "details": None,
    }


PLAYING = state_of("spotify", session_of(
    "T", artist="A", duration_ms=200000,
    position={"ms": 1000, "at": 1700000000.0, "rate": 1.0},
))
STOPPED = state_of("none")
# A gap INSIDE the playing source: still selected, no session, and nothing it
# would resume. What a source with no resume point looks like.
READY = state_of("spotify")
# A stop that left something behind: the mpv sources publish what a play press
# would bring back, so the card can hold the truth instead of a private copy
# of the last thing it was told.
RESUMABLE = state_of("radio", resume=resume_of(
    "FIP Jazz", album="FIP Jazz", artwork="/api/radio/images/7ff7.webp",
))
# What a source change leaves behind: ANOTHER source selected, no session
# under it, nothing to resume. The card sat on the previous source's track, paused.
SWITCHED = state_of("radio")
# A switch in flight.
SWITCHING = state_of("none", switching=True)


def playing_with(**fields):
    """PLAYING with its session's fields replaced."""
    return {**PLAYING, "session": {**PLAYING["session"], **fields}}


def a_state_event():
    """A `source/state` event, as the state machine builds it."""
    return AudioStateChanged(**AudioStateMachine().state().model_dump())


def past_the_grace(service):
    """Age the idle window so the next consideration is the one that ends it."""
    service._idle_since = time.time() - SESSION_IDLE_GRACE_S - 1


def tok(kind, value, session_id=None, device_id="phone-1"):
    return PushToken(token=value, kind=kind, environment=ApnsEnvironment.SANDBOX,
                     device_id=device_id, session_id=session_id, registered_at=1.0)


@pytest.fixture
def registry():
    held = {}
    reg = MagicMock()
    reg.held = held
    reg.tokens_for = lambda kind: [t for t in held.values() if t.kind == kind]
    reg.tokens_for_session = lambda sid: sorted(
        (t for t in held.values()
         if t.kind == PushTokenKind.SESSION and t.session_id == sid),
        key=lambda t: t.registered_at, reverse=True,
    )
    reg.token_for_session = lambda sid: next(iter(reg.tokens_for_session(sid)), None)
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


def vol_state(any_volume_control=True, global_volume_db=-43.0):
    """A volume snapshot, varying only what a widget test is about.

    The limits are scaffolding: the widget signature does not read them, and
    `_devices` normalizes against `volume_config`, not against these.
    """
    return VolumeState(
        mode="multiroom",
        global_volume_db=global_volume_db,
        global_mute=False,
        limit_min_db=-78.0,
        limit_max_db=-8.0,
        any_volume_control=any_volume_control,
    )


@pytest.fixture
def with_volume(registry, apns):
    """A service that can answer the one question a widget asks of Milō.

    The bare `service` fixture deliberately has no volume service, which is
    what a host wired without one looks like; the widget signature is read
    from this side, so a test about it needs the seam present.
    """
    def build(**state):
        volume_service = MagicMock()
        volume_service.get_volume_state = AsyncMock(return_value=vol_state(**state))
        svc = PushService(token_registry=registry, apns_client=apns,
                          volume_service=volume_service)
        machine = MagicMock()
        machine.get_current_state = MagicMock(return_value=dict(PLAYING))
        svc.set_state_machine(machine)
        svc.machine = machine
        return svc
    return build


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

    def test_a_seek_marks_the_service_dirty(self, service):
        """A seek is the one move of the playhead iOS cannot compute.

        Between two pushes the lock screen extrapolates from the last anchor,
        so steady playback costs nothing. A seek made anywhere else — the
        Spotify app, Milō's screen — only reaches the wire as `source/position`,
        and without a push the lock screen went on from the old anchor until
        the next track (measured 2026-09-25). The event is cheap to honour:
        the backend sends it only on a discontinuity past 2 s, never per tick.
        """
        service.on_event(SourcePosition(
            source="spotify", session_id="s-1",
            position=PositionAnchor(ms=1000, at=1700000000.0, rate=1.0),
        ))

        assert service._dirty.is_set() is True

    @pytest.mark.parametrize("event", [
        VolumeChanged(show_bar=True, step_mobile_db=2.0, multiroom_enabled=True, state={}),
        a_state_event(),
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

    async def test_every_device_holding_the_session_gets_the_update_and_the_end(
        self, service, registry, apns
    ):
        """A `start` goes to every push-to-start token, so a phone and an iPad
        open the same session and each registers its own token for it.
        Addressing only one froze the other's card: measured 2026-09-25, the
        iPad registered last and the iPhone received nothing afterwards."""
        registry.held["pts-phone"] = tok(PushTokenKind.PUSH_TO_START, "pts-phone")
        registry.held["pts-ipad"] = tok(PushTokenKind.PUSH_TO_START, "pts-ipad",
                                        device_id="ipad-1")
        await service._publish()
        session_id = service._session_id
        registry.held["sess-phone"] = tok(PushTokenKind.SESSION, "sess-phone",
                                          session_id=session_id)
        registry.held["sess-ipad"] = tok(PushTokenKind.SESSION, "sess-ipad",
                                         session_id=session_id, device_id="ipad-1")
        apns.send.reset_mock()

        await service._publish()

        assert sent_events(apns) == ["update", "update"]
        assert {c.args[0].token for c in apns.send.await_args_list} == {
            "sess-phone", "sess-ipad"}

        service.machine.get_current_state.return_value = dict(STOPPED)
        await service._publish()
        past_the_grace(service)
        apns.send.reset_mock()

        await service._publish()

        assert sent_events(apns) == ["end", "end"]
        assert "sess-phone" not in registry.held and "sess-ipad" not in registry.held

    async def test_a_pause_does_not_end_the_session(self, service, registry, apns):
        """Ending on pause deletes the play button at the moment someone
        reaches for it. The phase rides inside the attributes instead."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        await service._publish()
        session_id = service._session_id
        registry.held["sess"] = tok(PushTokenKind.SESSION, "sess", session_id=session_id)
        service.machine.get_current_state.return_value = playing_with(phase="paused")
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
        service.machine.get_current_state.return_value = {**PLAYING, "source": "radio"}

        await service._publish()

        assert service._session_id == session_id

    async def test_leaving_the_source_shows_milos_card_then_ends(
        self, service, registry, apns
    ):
        """Leaving the source puts Milō's own card up for the grace, then ends.

        It was ended on sight from 2026-09-22, because the card it left behind
        named nothing and read as Milō still offering something to play. The
        owner chose on 2026-09-25 to show Milō's card instead: the Lock Screen
        says where the music went, and the track that was playing is gone.
        """
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        await service._publish()
        registry.held["sess"] = tok(
            PushTokenKind.SESSION, "sess", session_id=service._session_id)
        service.machine.get_current_state.return_value = dict(STOPPED)
        apns.send.reset_mock()

        await service._publish()

        assert service._session_id is not None
        assert sent_events(apns) == ["update"]
        attributes = apns.send.await_args_list[0].args[1]["aps"]["attributes"]
        assert attributes["isPlaying"] is False
        assert attributes["currentTrack"]["title"] == "Milō"
        assert attributes["currentTrack"]["artworkURL"] == "/now-playing/milo.jpg"

        past_the_grace(service)
        apns.send.reset_mock()
        await service._publish()

        assert service._session_id is None
        assert sent_events(apns) == ["end"]
        assert apns.send.await_args_list[0].args[1]["aps"]["attributes"].keys() == {"id"}

    async def test_leaving_a_quiet_source_swaps_the_card_without_restarting_the_clock(
        self, service, registry, apns
    ):
        """The grace counts from when the music stopped. A source left inside
        it still has to reach the phone as Milō's card — the first idle cycle
        is not the only one that sends — and must not buy five more minutes."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        await service._publish()
        registry.held["sess"] = tok(
            PushTokenKind.SESSION, "sess", session_id=service._session_id)
        service.machine.get_current_state.return_value = dict(READY)
        await service._publish()
        idle_since = service._idle_since
        service.machine.get_current_state.return_value = dict(STOPPED)
        apns.send.reset_mock()

        await service._publish()

        assert sent_events(apns) == ["update"]
        track = apns.send.await_args_list[0].args[1]["aps"]["attributes"]["currentTrack"]
        assert track["title"] == "Milō"
        assert service._idle_since == idle_since

    async def test_an_idle_cycle_that_changes_nothing_spends_no_push(
        self, service, registry, apns
    ):
        """Every idle cycle reaches `_publish_paused` now, and the app's report
        calls in every couple of seconds: only a different card is sent."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        await service._publish()
        registry.held["sess"] = tok(
            PushTokenKind.SESSION, "sess", session_id=service._session_id)
        service.machine.get_current_state.return_value = dict(STOPPED)
        await service._publish()
        apns.send.reset_mock()

        await service._publish()
        await service.align_session_to_playback("phone-1")

        assert sent_events(apns) == []

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

    TRANSITION = SWITCHING

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
        service.machine.get_current_state.return_value = dict(READY)
        await service._publish()
        service.machine.get_current_state.return_value = dict(PLAYING)
        apns.send.reset_mock()

        await service._publish()

        assert service._session_id == session_id
        assert sent_events(apns) == ["update"]

    async def test_a_gap_inside_the_source_keeps_its_track(
        self, service, registry, apns
    ):
        """Holding the session through the gap is what keeps the card on screen,
        but on its own it left the card claiming the source was still playing —
        nothing is published while the state reads idle.

        The held track is read off the published state, not off a private copy
        of the last attributes. That is the whole point of a source publishing
        what it would resume: the card shows the station a play press brings
        back, and the same fact stops being stored twice with two lifetimes.
        """
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        await service._publish()
        registry.held["sess"] = tok(
            PushTokenKind.SESSION, "sess", session_id=service._session_id)
        service.machine.get_current_state.return_value = dict(RESUMABLE)
        apns.send.reset_mock()

        await service._publish()

        assert sent_events(apns) == ["update"]
        attributes = apns.send.await_args_list[0].args[1]["aps"]["attributes"]
        assert attributes["isPlaying"] is False
        assert attributes["currentTrack"]["title"] == RESUMABLE["resume"]["title"]

    async def test_a_gap_under_a_source_that_says_nothing_shows_its_card(
        self, service, registry, apns
    ):
        """The other half. Only the four mpv sources publish an idle identity;
        a receiver whose sender goes, or Spotify once its Connect session is
        gone, publishes the inert pair alone. That rebuilt as a media card with
        every field null, so the previous track used to be held over instead.
        The source's own card replaces both: its name, over its dock icon."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        await service._publish()
        registry.held["sess"] = tok(
            PushTokenKind.SESSION, "sess", session_id=service._session_id)
        service.machine.get_current_state.return_value = dict(READY)
        apns.send.reset_mock()

        await service._publish()

        attributes = apns.send.await_args_list[0].args[1]["aps"]["attributes"]
        assert attributes["isPlaying"] is False
        assert attributes["currentTrack"]["title"] == "Spotify"
        assert attributes["currentTrack"]["artworkURL"] == "/now-playing/spotify.jpg"

    async def test_a_gap_under_ANOTHER_source_does_not_keep_the_track(
        self, service, registry, apns
    ):
        """Measured 2026-09-20: radio → spotify, and the Lock Screen kept the
        radio track, paused, under a source that had nothing to play, for the
        whole grace. A track belonging to a source nobody selected any more is
        a lie, not a gap."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        await service._publish()
        registry.held["sess"] = tok(
            PushTokenKind.SESSION, "sess", session_id=service._session_id)
        service.machine.get_current_state.return_value = dict(SWITCHED)
        apns.send.reset_mock()

        await service._publish()

        attributes = apns.send.await_args_list[0].args[1]["aps"]["attributes"]
        assert attributes["currentTrack"]["title"] == "Webradio"

    async def test_a_source_change_shows_the_new_source_on_the_card_it_keeps(
        self, service, registry, apns
    ):
        """The reported failure, and the half of it the grace cannot fix.
        Measured 2026-09-20: radio → spotify, and the Lock Screen kept the radio
        track, paused, offering the transport of a source with nothing to play,
        for the whole five minutes. A track playing nowhere is not this card's
        track — but ending instead costs the card until the app is relaunched,
        which is the worse half of the trade. The card names the new source."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        await service._publish()
        session_id = service._session_id
        registry.held["sess"] = tok(
            PushTokenKind.SESSION, "sess", session_id=session_id)
        service.machine.get_current_state.return_value = dict(SWITCHED)
        apns.send.reset_mock()

        await service._publish()

        assert service._session_id == session_id
        assert sent_events(apns) == ["update"]
        attributes = apns.send.await_args_list[0].args[1]["aps"]["attributes"]
        assert attributes["isPlaying"] is False
        assert attributes["currentTrack"]["title"] == "Webradio"

    async def test_the_paused_snapshot_never_says_playing(
        self, service, registry, apns
    ):
        """This push has one thing to say. A card rebuilt from what the new
        source would resume must still say paused: a playing card the music
        has left is one iOS extrapolates a position from."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        await service._publish()
        registry.held["sess"] = tok(
            PushTokenKind.SESSION, "sess", session_id=service._session_id)
        service.machine.get_current_state.return_value = {
            **SWITCHED, "resume": resume_of("X", position_ms=5000)
        }
        apns.send.reset_mock()

        await service._publish()

        attributes = apns.send.await_args_list[0].args[1]["aps"]["attributes"]
        assert attributes["isPlaying"] is False

    def test_a_finished_transition_wakes_the_loop(self, service):
        """Leaving the source must reach this loop without help.

        The end of a switch reaches the bus as one more `source/state`
        (switching false). When no event of the switch was a trigger, the card
        outlived the source it described until something unrelated — a volume
        nudge — happened to wake the loop. Measured on the appliance 2026-09-22.
        """
        service._dirty.clear()
        service.on_event(a_state_event())
        assert service._dirty.is_set()

    async def test_no_session_means_nothing_to_end(self, service, registry, apns):
        """`none` with no session open must do nothing at all.

        Ending on sight replaced a call that began `if self._session_id is
        None: return`, and dropping that guard was measured on the appliance
        2026-09-22: every publish while no source was selected ran the teardown
        over again — `Now Playing session None ended` in the journal, a
        `token_for_session(None)` lookup, and `_session_cleared_at` pushed
        forward each time, which is the window that stops a just-ended session
        from being adopted back.
        """
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        service.machine.get_current_state.return_value = dict(STOPPED)

        await service._publish()
        await service._publish()

        assert service._session_id is None
        assert sent_events(apns) == []
        assert service._session_cleared_at == 0.0

    async def test_a_source_that_goes_quiet_still_ends_after_the_grace(
        self, service, registry, apns
    ):
        """The other side of the delay, on the case that still HAS one.

        READY is a source still selected with nothing playing — the gap the
        grace exists for. It must be ridden out, then ended, or a session that
        outlived playback for good would keep Milō on a Lock Screen it no
        longer owns. `none` takes the same road, under Milō's card.
        """
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        await service._publish()
        registry.held["sess"] = tok(
            PushTokenKind.SESSION, "sess", session_id=service._session_id)
        service.machine.get_current_state.return_value = dict(READY)
        await service._publish()
        assert service._session_id is not None      # the grace is armed
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


class TestDeviceReport:
    """`align_session_to_playback` — the seam the running app reports through.

    Both ends of the lifecycle have a case the bus cannot reach, and the app
    cannot repair either one: `RemoteMediaSession` is unavailable to extensions,
    so a suspended app holds no card and Milō is the only party that can open or
    close one. Measured 2026-09-20, with the app reporting
    `rien à montrer (spotify/ready), aucune session tenue` while the Lock Screen
    kept the previous track, paused, offering the transport of a source with
    nothing to play.
    """

    TRANSITION = SWITCHING

    async def _held(self, service, registry):
        """A session this service opened and the device has reported back."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        await service._publish()
        registry.held["sess"] = tok(
            PushTokenKind.SESSION, "sess", session_id=service._session_id)
        return service._session_id

    async def test_a_report_on_an_idle_source_holds_its_card_for_the_grace(
        self, service, registry, apns
    ):
        """The reported failure. The session goes on a source change, and only
        Milō can say what the card should show then. A report used to close it
        at once, because it named nothing; it names the new source now, and a
        report holding the card for the same grace as the bus is what keeps
        its lifetime from depending on whether the app happened to be open."""
        session_id = await self._held(service, registry)
        service.machine.get_current_state.return_value = dict(SWITCHED)
        apns.send.reset_mock()

        await service.align_session_to_playback("phone-1")

        assert service._session_id == session_id
        assert sent_events(apns) == ["update"]
        track = apns.send.await_args_list[0].args[1]["aps"]["attributes"]["currentTrack"]
        assert track["title"] == "Webradio"

    async def test_a_report_with_no_source_shows_milos_card(self, service, registry, apns):
        """`source: none` is the other spelling of the same idleness —
        selecting no source, rather than selecting another one."""
        await self._held(service, registry)
        service.machine.get_current_state.return_value = dict(STOPPED)
        apns.send.reset_mock()

        await service.align_session_to_playback("phone-1")

        assert sent_events(apns) == ["update"]
        track = apns.send.await_args_list[0].args[1]["aps"]["attributes"]["currentTrack"]
        assert track["title"] == "Milō"

    async def test_a_card_is_closed_once(self, service, registry, apns):
        """The app reports every couple of seconds. A second `end` addresses a
        session that no longer exists, and spends budget saying it."""
        await self._held(service, registry)
        service.machine.get_current_state.return_value = dict(STOPPED)
        await service.align_session_to_playback("phone-1")
        past_the_grace(service)
        apns.send.reset_mock()
        await service.align_session_to_playback("phone-1")
        assert sent_events(apns) == ["end"]
        apns.send.reset_mock()

        await service.align_session_to_playback("phone-1")

        assert sent_events(apns) == []

    async def test_a_report_on_a_stopped_source_does_not_close_what_resumes(
        self, service, registry, apns
    ):
        """The assertion that keeps the two paths from drifting apart again.

        A source that stopped with something to resume is idle, not finished.
        Closing it would put the phone back to the "nothing ever played" this
        state exists to tell apart — so the card is held, showing what a play
        press would bring back, and the grace governs the ending as it does on
        the bus.
        """
        await self._held(service, registry)
        service.machine.get_current_state.return_value = dict(RESUMABLE)
        apns.send.reset_mock()

        await service.align_session_to_playback("phone-1")

        assert service._session_id is not None
        assert sent_events(apns) == ["update"]
        track = apns.send.await_args_list[0].args[1]["aps"]["attributes"]["currentTrack"]
        assert track["title"] == RESUMABLE["resume"]["title"]

        # Held, not kept: the grace still ends it.
        past_the_grace(service)
        apns.send.reset_mock()
        await service.align_session_to_playback("phone-1")

        assert service._session_id is None
        assert sent_events(apns) == ["end"]

    async def test_nothing_closes_during_a_transition(self, service, registry, apns):
        """`switching` is the beat of a source change. Closing on it would
        drop the card and raise it again at every switch — and the session that
        came back was one no app had asked to be primary."""
        session_id = await self._held(service, registry)
        service.machine.get_current_state.return_value = dict(self.TRANSITION)
        apns.send.reset_mock()

        await service.align_session_to_playback("phone-1")

        assert service._session_id == session_id
        assert sent_events(apns) == []

    async def test_music_already_playing_opens_a_card(self, service, registry, apns):
        """A session is opened on a playback EVENT, and music that is already
        playing produces none: Control Center read "stopped" until the next
        track change. The report is the only other thing that arrives."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")

        await service.align_session_to_playback("phone-1")

        assert sent_events(apns) == ["start"]
        assert service._session_id is not None

    async def test_the_card_opens_on_the_track_that_is_playing(
        self, service, registry, apns
    ):
        """A `start` with no `currentTrack` opens the card empty, which is worse
        than not opening it: the app cannot fill it, and the next update only
        arrives when something changes."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")

        await service.align_session_to_playback("phone-1")

        attributes = apns.send.await_args_list[0].args[1]["aps"]["attributes"]
        assert attributes["currentTrack"]["title"] == "T"
        assert attributes["isPlaying"] is True

    async def test_a_card_is_opened_once(self, service, registry, apns):
        """Without the guard, a route the app calls every couple of seconds
        opens a session every couple of seconds."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        await service.align_session_to_playback("phone-1")
        apns.send.reset_mock()

        await service.align_session_to_playback("phone-1")

        assert sent_events(apns) == []

    async def test_nothing_opens_during_a_transition(self, service, registry, apns):
        """A source on its way up is not a source that is playing, and the card
        opened there would carry the metadata of neither side."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        service.machine.get_current_state.return_value = {**PLAYING, "switching": True}

        await service.align_session_to_playback("phone-1")

        assert sent_events(apns) == []
        assert service._session_id is None

    async def test_a_report_that_changes_nothing_spends_no_push(
        self, service, registry, apns
    ):
        """This seam reconciles; it does not emit. Updates are the coalescer's,
        which is where the one-per-second cap lives."""
        await self._held(service, registry)
        apns.send.reset_mock()

        await service.align_session_to_playback("phone-1")

        assert sent_events(apns) == []

    async def test_a_phone_that_cannot_be_started_starts_nothing(
        self, service, registry, apns
    ):
        """The `start` goes to the push-to-start token. A phone that holds none
        would otherwise open a card on somebody else's phone by reporting."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts",
                                   device_id="phone-2")

        await service.align_session_to_playback("phone-1")

        assert sent_events(apns) == []

    async def test_a_report_and_a_bus_cycle_do_not_mint_two_sessions(
        self, service, registry, apns
    ):
        """Two seams on one loop, both deciding on `_session_id` before awaiting
        APNs. Interleaved they each read None and open a session of their own,
        and the phone then holds two: the system shows one and this service
        feeds the other, which is the failure this whole area exists to avoid."""
        async def slow_send(*_args, **_kwargs):
            await asyncio.sleep(0.05)
            return ApnsResult(ok=True, status=200)

        apns.send.side_effect = slow_send
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")

        await asyncio.gather(
            service._publish(), service.align_session_to_playback("phone-1")
        )

        assert sent_events(apns) == ["start"]
        assert len(set(sent_sessions(apns))) == 1

    async def test_one_phone_does_not_close_another_phones_card(
        self, service, registry, apns
    ):
        """A device knows its own sessions and nobody else's — the same scoping
        `drop_sessions_absent_from` has, and for the same reason: the first
        report from one phone would otherwise clear the household."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts",
                                   device_id="phone-2")
        registry.held["sess"] = tok(PushTokenKind.SESSION, "sess",
                                    session_id="APP-2", device_id="phone-2")
        service.machine.get_current_state.return_value = dict(READY)

        await service.align_session_to_playback("phone-1")

        assert service._session_id == "APP-2"
        assert sent_events(apns) == []


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
    """Radio fills the common floor like every other source.

    It did not: the four floor fields stayed empty and the track travelled
    beside them in `track_title`/`station_name`, so this layer re-derived what
    to show — and so did Milo-iOS, in its own copy. The derivation now lives in
    the source (see test_radio_source.py::TestTheCommonFloor), which is what
    lets this layer read `title`/`artist` straight and what fixed podcast, a
    source neither copy of the cascade covered.
    """

    RADIO = state_of("radio", session_of(
        "Snibor", artist="Gil Evans", album="FIP Jazz",
        artwork="/api/radio/images/7ff7.webp",
    ))

    async def test_a_station_change_reaches_the_card_and_not_the_widget(
        self, service, registry, apns
    ):
        """Through a floor radio left empty every station looked identical here,
        so a station change reached the lock screen as the previous track.

        The widget is the other half, and it is the half that was wrong in this
        file: a station is drawn nowhere in `MiloWidgetEntry`, so the push this
        test used to assert paid for no pixel. Both tokens are held, and only
        the card's is written to.
        """
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        registry.held["widget"] = tok(PushTokenKind.WIDGET, "widget")
        service.machine.get_current_state.return_value = dict(self.RADIO)
        await service._publish()
        registry.held["sess"] = tok(
            PushTokenKind.SESSION, "sess", session_id=service._session_id)
        apns.send.reset_mock()
        service.machine.get_current_state.return_value = {
            **self.RADIO, "session": {**self.RADIO["session"], "title": "Blues For Pablo"},
        }

        await service._publish()

        assert [c.args[0].token for c in apns.send.await_args_list] == ["sess"]
        card = apns.send.await_args_list[0].args[1]["aps"]["attributes"]["currentTrack"]
        assert card["title"] == "Blues For Pablo"

    async def test_a_stopped_station_still_draws_a_card(self, service, registry, apns):
        """What the whole change is for, at this layer: a source that stopped
        with something to resume reaches the lock screen as itself, paused —
        not as a session with four null fields, which on the phone is no track
        at all."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        service.machine.get_current_state.return_value = dict(self.RADIO)
        await service._publish()
        registry.held["sess"] = tok(
            PushTokenKind.SESSION, "sess", session_id=service._session_id)
        service.machine.get_current_state.return_value = dict(RESUMABLE)
        apns.send.reset_mock()

        await service._publish()

        track = apns.send.await_args_list[0].args[1]["aps"]["attributes"]["currentTrack"]
        assert track["title"] == RESUMABLE["resume"]["title"]
        assert track["artworkURL"] == RESUMABLE["resume"]["artwork"]


class TestWidgetCadence:
    """What the widget actually draws, and what a push on anything else costs.

    Measured against the published app on 2026-09-22: `MiloWidgetEntry` carries
    a `MiloWidgetData` (level, reachable, driveable, muted) and a `showVolume`
    flag. The view draws the +/- buttons, and above them either the logo — at
    full opacity iff `isConnected && canControlVolume` — or the level, and the
    level only while `showVolume` is set. Nothing else is reachable from an
    entry, so nothing else can justify a push out of a budget Apple grants per
    day.
    """

    async def test_a_level_change_pushes_no_widget(self, with_volume, registry, apns):
        """The level is drawn for 3 s after a press on the widget's own buttons
        and at no other time — `showVolume` comes from a 5 s window whose only
        writers are those two intents. So a push sent because a knob turned in
        the room re-renders the logo: it cannot show the new level, whatever it
        costs. And the press that does show it reloads the timeline itself.
        """
        service = with_volume(global_volume_db=-43.0)
        registry.held["w"] = tok(PushTokenKind.WIDGET, "w")
        await service._publish()          # first cycle: the signature is new
        service._volume_service.get_volume_state = AsyncMock(
            return_value=vol_state(global_volume_db=-12.0))
        apns.send.reset_mock()

        await service._publish()

        assert sent_types(apns) == []

    async def test_a_track_change_pushes_no_widget(self, with_volume, registry, apns):
        """A widget draws no track. It carried a source name once and Milo-iOS
        deleted it as dead on 2026-09-20 (`MiloWidgetData.sourceName`), and this
        side went on spending a push per track for a field with no reader.
        """
        service = with_volume()
        registry.held["w"] = tok(PushTokenKind.WIDGET, "w")
        await service._publish()
        service.machine.get_current_state.return_value = playing_with(title="Another")
        apns.send.reset_mock()

        await service._publish()

        assert sent_types(apns) == []

    async def test_a_source_going_ready_pushes_no_widget(self, with_volume, registry, apns):
        """The other half of the same saving: an ACTIVE/READY flip is a play
        state, and no entry field carries one. Pinned apart from the track
        because the two arrived in the signature for different reasons.
        """
        service = with_volume()
        registry.held["w"] = tok(PushTokenKind.WIDGET, "w")
        await service._publish()
        service.machine.get_current_state.return_value = dict(READY)
        apns.send.reset_mock()

        await service._publish()

        assert sent_types(apns) == []

    async def test_losing_the_last_driveable_speaker_pushes_the_widget(
        self, with_volume, registry, apns
    ):
        """The one push that buys a pixel: `any_volume_control` is the half of
        the widget's `isReady` this side can observe, and it dims the logo. A
        mode switch, the local DAC flag and the set of available clients with
        volume control all move it — and all already broadcast VolumeChanged,
        so the coalescer wakes for it without a new trigger.
        """
        service = with_volume(any_volume_control=True)
        registry.held["w"] = tok(PushTokenKind.WIDGET, "w")
        await service._publish()
        service._volume_service.get_volume_state = AsyncMock(
            return_value=vol_state(any_volume_control=False))
        apns.send.reset_mock()

        await service._publish()

        assert sent_types(apns) == ["widgets"]

    async def test_a_widget_push_apple_refused_is_retried(
        self, with_volume, registry, apns
    ):
        """The signature is recorded only once Apple has taken the push.

        It used to be stamped before the send, which was survivable while the
        signature moved on every track change — the next track retried it by
        accident. It now moves only on a rare flip, so a stamp-then-fail would
        lose that push for good and leave the logo wrong until WidgetKit's own
        refresh.
        """
        service = with_volume()
        registry.held["w"] = tok(PushTokenKind.WIDGET, "w")
        apns.send.return_value = ApnsResult(ok=False, status=0, reason="Unreachable")

        await service._publish()
        assert sent_types(apns) == ["widgets"]        # attempted, refused

        apns.send.return_value = ApnsResult(ok=True, status=200)
        apns.send.reset_mock()
        await service._publish()                       # same state, still unsent

        assert sent_types(apns) == ["widgets"]

    async def test_a_delivered_widget_push_is_not_sent_twice(
        self, with_volume, registry, apns
    ):
        """The other side of the retry: taking delivery into the condition must
        not turn the signature into a no-op that re-pushes every cycle."""
        service = with_volume()
        registry.held["w"] = tok(PushTokenKind.WIDGET, "w")

        await service._publish()
        apns.send.reset_mock()
        await service._publish()

        assert sent_types(apns) == []

    async def test_a_dead_token_does_not_leave_the_signature_unrecorded(
        self, with_volume, registry, apns
    ):
        """A 410 is not a transient failure, and the retry must not spin on it.
        `_send_all` purges the token, so the next cycle has no target left —
        which is the branch that records the signature and ends it."""
        service = with_volume()
        registry.held["w"] = tok(PushTokenKind.WIDGET, "w")
        apns.send.return_value = ApnsResult(ok=False, status=410, reason="Unregistered",
                                            dead=True, invalidated_at=123.0)

        await service._publish()
        del registry.held["w"]                         # what purge did, in this mock
        apns.send.reset_mock()
        await service._publish()

        assert sent_types(apns) == []
        assert service._widget_signature is not None

    async def test_the_first_publish_after_a_restart_pushes_the_widget(
        self, with_volume, registry, apns
    ):
        """This is what relights a logo the widget left dimmed, and it is why
        no boot hook was added for it. Milō cannot push while it is down, so
        "it is back" can only be said on the way up — and `_widget_signature`
        starting None says it, for free, on the first cycle. Remove that and
        a unit that was unreachable stays dimmed until the widget's own retry,
        which WidgetKit is free to defer.
        """
        service = with_volume()
        registry.held["w"] = tok(PushTokenKind.WIDGET, "w")

        assert service._widget_signature is None
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
            service.machine.get_current_state.return_value = playing_with(title=f"T{i}")
            service.on_event(VolumeChanged(show_bar=True, step_mobile_db=2.0,
                                           multiroom_enabled=True, state={}))

        await _settled(apns)
        await service.cleanup()

        updates = [c.args[1] for c in apns.send.await_args_list
                   if c.args[2] == "nowplaying"]
        assert len(updates) == 1
        assert updates[0]["aps"]["attributes"]["currentTrack"]["title"] == "T19"

    async def test_a_seek_reaches_the_lock_screen_with_its_new_anchor(
        self, service, registry, apns, monkeypatch
    ):
        """The seek's push carries the playhead it moved to, stamped when it
        moved: that is the anchor iOS extrapolates from next."""
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        monkeypatch.setattr("backend.core.push.service.MIN_PUSH_INTERVAL_S", 0.02)
        await service._publish()                       # open the session
        registry.held["sess"] = tok(
            PushTokenKind.SESSION, "sess", session_id=service._session_id)
        apns.send.reset_mock()
        await service.initialize()

        moved = {"ms": 90000, "at": 1700000030.0, "rate": 1.0}
        service.machine.get_current_state.return_value = playing_with(position=moved)
        service.on_event(SourcePosition(
            source="spotify", session_id="s-1", position=PositionAnchor(**moved),
        ))

        await _settled(apns)
        await service.cleanup()

        updates = [c.args[1]["aps"]["attributes"] for c in apns.send.await_args_list
                   if c.args[2] == "nowplaying"]
        assert [(u["elapsedTime"], u["timestamp"]) for u in updates] == [
            (90.0, "2023-11-14T22:13:50.000Z"),
        ]

    async def _session_open(self, service, registry, apns, monkeypatch, window):
        registry.held["pts"] = tok(PushTokenKind.PUSH_TO_START, "pts")
        monkeypatch.setattr("backend.core.push.service.MIN_PUSH_INTERVAL_S", window)
        await service._publish()                       # open the session
        registry.held["sess"] = tok(
            PushTokenKind.SESSION, "sess", session_id=service._session_id)
        apns.send.reset_mock()
        await service.initialize()

    def _seek(self, service, ms):
        moved = {"ms": ms, "at": 1700000030.0, "rate": 1.0}
        service.machine.get_current_state.return_value = playing_with(position=moved)
        service.on_event(SourcePosition(
            source="podcast", session_id="s-1", position=PositionAnchor(**moved),
        ))

    async def test_a_seek_after_a_quiet_spell_does_not_wait_out_the_window(
        self, service, registry, apns, monkeypatch
    ):
        """Sleeping the window first put the lock screen's new anchor ~1.2 s
        behind a −15/+30 press (measured from Milo-iOS), with nothing to
        coalesce it with. The window here is far longer than `_settled`
        waits: only a push sent at once is seen."""
        await self._session_open(service, registry, apns, monkeypatch, window=30.0)

        self._seek(service, 90000)
        await _settled(apns)
        await service.cleanup()

        updates = [c.args[1]["aps"]["attributes"] for c in apns.send.await_args_list
                   if c.args[2] == "nowplaying"]
        assert [u["elapsedTime"] for u in updates] == [90.0]

    async def test_a_seek_right_after_a_push_still_waits_for_the_window(
        self, service, registry, apns, monkeypatch
    ):
        """The one-per-window ceiling holds for seeks too: a burst of presses
        reaches Apple as one push now and one at the end of the window, never
        one per press."""
        await self._session_open(service, registry, apns, monkeypatch, window=30.0)

        self._seek(service, 90000)
        await _settled(apns)
        self._seek(service, 120000)
        self._seek(service, 150000)
        await asyncio.sleep(0.2)
        await service.cleanup()

        assert len(sent_types(apns)) == 1

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
            service.machine.get_current_state.return_value = playing_with(title=f"T{i}")
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


class TestTheDeviceList:
    """Which speakers the lock screen is given a slider for.

    Never exercised until now: the `service` fixture above wires no volume
    service, so every test in this file takes `_devices`' early return and the
    whole body — the filter AND the normalization — ran nowhere.

    What breaks when these fail is invisible from this side. The phone builds a
    master gesture by averaging the sliders it was handed, so a list wider than
    the one Milō averages into `global_volume_db` makes the two sides name
    different numbers for "the house volume" — and it makes the app disagree
    with itself, since awake it builds the same list from /api/volume/state and
    filters on exactly these two flags.
    """

    LIMITS = VolumeConfig(limit_min_db=-78.0, limit_max_db=-8.0)

    @pytest.fixture
    def volumes(self):
        def state(**clients):
            return VolumeState(
                mode="multiroom",
                global_volume_db=-43.0,
                global_mute=False,
                limit_min_db=self.LIMITS.limit_min_db,
                limit_max_db=self.LIMITS.limit_max_db,
                clients=clients,
            )
        return state

    @pytest.fixture
    def wired(self, registry, apns, volumes):
        """The fixture the rest of this file deliberately does without."""
        def build(**clients):
            volume_service = MagicMock()
            volume_service.get_volume_state = AsyncMock(return_value=volumes(**clients))
            volume_service.volume_config = self.LIMITS
            client_registry = MagicMock()
            client_registry.get_all_clients = MagicMock(return_value={
                mac: SimpleNamespace(name=mac.capitalize()) for mac in clients
            })
            return PushService(
                token_registry=registry, apns_client=apns,
                volume_service=volume_service, client_registry_service=client_registry,
            )
        return build

    async def test_a_speaker_that_is_playing_gets_its_level_on_the_sliders_scale(
        self, wired
    ):
        """-43 dB is the middle of -78..-8, and the phone is told 0.5 — never
        the decibel, and never a fraction of a range this unit does not use."""
        service = wired(kitchen=ClientVolume(
            volume_db=-43.0, offset_db=0.0, mute=False,
        ))

        devices = await service._devices()

        assert [(d.id, d.name, d.volume) for d in devices] == [("kitchen", "Kitchen", 0.5)]

    async def test_an_unavailable_speaker_is_not_offered_a_slider(self, wired):
        """It is out of `global_volume_db`'s average, so a handle for it moves
        the phone's idea of the house volume and not Milō's."""
        service = wired(
            kitchen=ClientVolume(volume_db=-43.0, offset_db=0.0, mute=False),
            garden=ClientVolume(volume_db=-20.0, offset_db=0.0, mute=False, available=False),
        )

        assert [d.id for d in await service._devices()] == ["kitchen"]

    async def test_a_dac_client_is_not_offered_a_slider(self, wired):
        """Same argument, other flag: an external amp owns its own level, so
        Milō neither counts it nor can move it."""
        service = wired(
            kitchen=ClientVolume(volume_db=-43.0, offset_db=0.0, mute=False),
            study=ClientVolume(volume_db=-30.0, offset_db=0.0, mute=False, volume_control=False),
        )

        assert [d.id for d in await service._devices()] == ["kitchen"]
