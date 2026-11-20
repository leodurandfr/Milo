# backend/tests/test_presence.py
"""The proof of presence (`core/system/presence.py`), across the unit it reads.

It is the only thing between the LAN and root on the routes that hand root out,
so each test here is a way someone not at the unit would get a proof: by asking
at the right moment, by asking for something else with another's gesture, by a
key that only calls itself the power button, by a reboot asked over the LAN that
reads like a power cut. The routes themselves are held by
`tests/architecture/test_presence_guard.py`.
"""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from backend.core.models.presence import Gesture, PresenceAction
from backend.core.system.presence import (
    INHIBIT_UNIT,
    PresenceBusy,
    PresenceElsewhere,
    PresenceRequired,
    PresenceUnavailable,
)
from backend.tests.presence_world import (
    BUS_BLUETOOTH,
    FakeInput,
    World,
    board_button,
    install_inputs,
)

SSH = PresenceAction.SSH_OPEN
# What the unit's own screen can wait through: a power cut takes it down too.
ON_THE_UNIT = frozenset({Gesture.ROTARY, Gesture.POWER_BUTTON})


async def _open(service, action=SSH, token=None):
    """The 428 a guarded request gets: its challenge, as published, and its token."""
    with pytest.raises(PresenceRequired) as refused:
        await service.require(action, token)
    detail = refused.value.detail
    return detail["challenge"], detail["token"]


async def _settle():
    """Let the tasks a gesture or an expiry spawned run."""
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.fixture
def unit(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch, rotary=True)


class TestTheChallenge:
    async def test_only_the_token_of_the_request_that_asked_spends_the_gesture(self, unit):
        """The 428 is the only place the token travels: a second device asking
        for the same action meanwhile must not ride on the owner's gesture."""
        service = unit.service()
        await service.initialize()
        challenge, token = await _open(service)
        assert challenge["gesture"] == "rotary" and challenge["status"] == "pending"

        assert service.on_gesture(Gesture.ROTARY) is True

        for stranger in (None, "not-the-token"):
            with pytest.raises(PresenceBusy):
                await service.require(SSH, stranger)
        await service.require(SSH, token)
        assert service.view() is None

    async def test_a_proof_is_spent_once(self, unit):
        service = unit.service()
        await service.initialize()
        first, token = await _open(service)
        service.on_gesture(Gesture.ROTARY)
        await service.require(SSH, token)

        again, _ = await _open(service, token=token)

        assert again["id"] != first["id"] and again["status"] == "pending"

    async def test_the_token_answers_its_own_challenge_while_the_gesture_is_awaited(self, unit):
        """A client that sends again before the gesture is told the same thing,
        not given a second challenge, nor refused as someone else."""
        service = unit.service()
        await service.initialize()
        first, token = await _open(service)

        again, same = await _open(service, token=token)

        assert (again["id"], same) == (first["id"], token)

    async def test_a_proof_is_bound_to_the_action_it_was_asked_for(self, unit):
        """The gesture made for closing a door must not open another one."""
        service = unit.service()
        await service.initialize()
        _, token = await _open(service, PresenceAction.PROGRAM_UPSTREAM)
        service.on_gesture(Gesture.ROTARY)

        with pytest.raises(PresenceBusy):
            await service.require(PresenceAction.PASSWORD, token)

        assert service.view().status == "confirmed", "the refusal spends nothing"

    async def test_a_gesture_this_unit_does_not_wait_for_proves_nothing(self, unit):
        service = unit.service()
        await service.initialize()
        await _open(service)

        assert service.on_gesture(Gesture.POWER_BUTTON) is False
        assert service.view().status == "pending"

    async def test_without_a_challenge_the_gesture_is_the_encoders_again(self, unit):
        service = unit.service()
        await service.initialize()

        assert service.on_gesture(Gesture.ROTARY) is False

    async def test_an_unanswered_challenge_ends_and_frees_the_guard(self, unit):
        """Left open, it would refuse every other guarded request for good."""
        service = unit.service()
        service.CHALLENGE_S = 0.05
        service.COOLDOWN_S = 0
        await service.initialize()
        first, _ = await _open(service)

        await asyncio.sleep(0.1)
        await _settle()

        assert service.view() is None
        assert unit.published()[-1] is None
        assert service.on_gesture(Gesture.ROTARY) is False, "a late gesture proves nothing"
        second, _ = await _open(service)
        assert second["id"] != first["id"]

    async def test_a_challenge_that_ends_unanswered_keeps_the_knob_unarmed_for_a_while(self, unit):
        """The knob is the owner's volume: a stranger who asks again the moment
        each challenge ends would otherwise keep it armed for good, and the
        owner's next turn would confirm a request they never made."""
        service = unit.service()
        service.CHALLENGE_S = 0.05
        service.COOLDOWN_S = 0.2
        await service.initialize()
        await _open(service)
        await asyncio.sleep(0.1)
        await _settle()

        with pytest.raises(PresenceBusy):
            await service.require(SSH, None)
        assert service.view() is None
        assert service.on_gesture(Gesture.ROTARY) is False, "the turn is the volume meanwhile"

        await asyncio.sleep(0.2)
        challenge, _ = await _open(service)
        assert challenge["status"] == "pending"

    async def test_a_cancelled_challenge_keeps_the_knob_unarmed_too(self, unit):
        """Cancel and ask again is the same loop as letting it expire."""
        service = unit.service()
        await service.initialize()
        _, token = await _open(service)
        await service.cancel(token)

        with pytest.raises(PresenceBusy):
            await service.require(SSH, None)

    async def test_once_confirmed_the_knob_is_the_volume_again(self, unit):
        """The gesture is made; swallowing the turns after it would leave the
        knob dead for as long as the request takes to come back."""
        service = unit.service()
        await service.initialize()
        await _open(service)

        assert service.on_gesture(Gesture.ROTARY) is True
        assert service.on_gesture(Gesture.ROTARY) is False
        assert service.view().status == "confirmed"

    async def test_a_proof_not_spent_in_time_ends(self, unit):
        service = unit.service()
        service.PROOF_S = 0.05
        service.COOLDOWN_S = 0
        await service.initialize()
        _, token = await _open(service)
        service.on_gesture(Gesture.ROTARY)

        await asyncio.sleep(0.1)
        await _settle()

        assert service.view() is None
        challenge, _ = await _open(service, token=token)
        assert challenge["status"] == "pending"

    async def test_the_confirmation_is_published_without_the_token(self, unit):
        """Every screen reads the event; only the asker may hold what spends it."""
        service = unit.service()
        await service.initialize()
        challenge, token = await _open(service)
        service.on_gesture(Gesture.ROTARY)
        await _settle()

        statuses = [(c.id, c.status) for c in unit.published() if c is not None]
        assert statuses == [(challenge["id"], "pending"), (challenge["id"], "confirmed")]
        for call in unit.state_machine.broadcast.await_args_list:
            assert token not in json.dumps(call.args[0].wire_data(), default=str)

    async def test_only_the_asker_cancels(self, unit):
        service = unit.service()
        await service.initialize()
        challenge, token = await _open(service)

        assert await service.cancel("not-the-token") is False
        assert await service.cancel(None) is False
        assert service.view().id == challenge["id"]
        assert await service.cancel(token) is True
        assert service.view() is None


class TestThePowerButton:
    @pytest.fixture
    def pi5(self, tmp_path, monkeypatch):
        return World(tmp_path, monkeypatch, pi5=True)

    async def test_the_button_is_read_under_the_inhibitor_and_released_after(self, pi5, monkeypatch):
        """Without the inhibitor up before the 428, the press the dialog asks
        for is logind's: the unit powers off."""
        button = board_button()
        install_inputs(monkeypatch, button)
        service = pi5.service()
        await service.initialize()

        challenge, token = await _open(service)

        assert challenge["gesture"] == "power_button"
        pi5.systemd.start.assert_awaited_once_with(INHIBIT_UNIT)
        button.press()
        await _settle()
        assert service.view().status == "confirmed"
        pi5.systemd.stop.assert_not_awaited()

        await service.require(SSH, token)

        pi5.systemd.stop.assert_awaited_once_with(INHIBIT_UNIT)
        await _settle()
        assert button.closed

    async def test_a_bluetooth_key_named_like_the_button_is_not_it(self, pi5, monkeypatch):
        """BlueZ maps a phone's AVRCP Power to KEY_POWER: a sender in radio range
        would otherwise prove presence. With no board button, the power cut."""
        impostor = FakeInput("/dev/input/event3", "pwr_button", BUS_BLUETOOTH)
        install_inputs(monkeypatch, impostor)
        service = pi5.service()
        await service.initialize()

        challenge, _ = await _open(service)

        assert challenge["gesture"] == "power_cycle"
        pi5.systemd.start.assert_not_awaited()
        assert impostor.closed
        impostor.press()
        await _settle()
        assert service.view().status == "pending"

    async def test_an_inhibitor_that_does_not_start_never_asks_for_the_button(self, pi5, monkeypatch):
        button = board_button()
        install_inputs(monkeypatch, button)
        pi5.systemd.start = AsyncMock(return_value=False)
        service = pi5.service()
        await service.initialize()

        challenge, _ = await _open(service)

        assert challenge["gesture"] == "power_cycle"
        assert button.closed

    async def test_a_fallback_to_the_plug_opens_nothing_for_the_units_own_screen(self, pi5, monkeypatch):
        """The button was the gesture the screen could wait through; the
        inhibitor's failure makes it the plug, which it cannot."""
        button = board_button()
        install_inputs(monkeypatch, button)
        pi5.systemd.start = AsyncMock(return_value=False)
        service = pi5.service()
        await service.initialize()

        with pytest.raises(PresenceElsewhere):
            await service.require(SSH, None, ON_THE_UNIT)

        assert service.view() is None
        assert button.closed
        assert not pi5.record.exists()
        await _open(service)

    async def test_the_encoder_comes_first(self, tmp_path, monkeypatch):
        install_inputs(monkeypatch, board_button())
        unit = World(tmp_path, monkeypatch, pi5=True, rotary=True)
        service = unit.service()
        await service.initialize()

        challenge, _ = await _open(service)

        assert challenge["gesture"] == "rotary"
        unit.systemd.start.assert_not_awaited()

    async def test_an_inhibitor_left_up_is_stopped_at_start(self, pi5):
        """A backend restarted mid-challenge restarts the inhibitor with it
        (`BindsTo=`): the power key would stay dead until RuntimeMaxSec."""
        pi5.systemd.unit_state.return_value = ("active", "success")

        await pi5.service().initialize()

        pi5.systemd.stop.assert_awaited_once_with(INHIBIT_UNIT)


class TestThePowerCut:
    """Neither an encoder nor a Pi 5: the plug is the gesture."""

    @pytest.fixture
    def bare(self, tmp_path, monkeypatch):
        return World(tmp_path, monkeypatch)

    async def test_a_power_cut_proves_the_challenge_for_its_token(self, bare):
        before = bare.service()
        await before.initialize()
        challenge, token = await _open(before)
        assert challenge["gesture"] == "power_cycle"
        assert bare.record.exists(), "on the card before the plug is pulled"

        bare.power_cut()
        after = bare.service()
        await after.initialize()

        assert after.view().id == challenge["id"]
        assert after.view().status == "confirmed"
        with pytest.raises(PresenceBusy):
            await after.require(SSH, "not-the-token")
        await after.require(SSH, token)

    async def test_a_reboot_proves_nothing(self, bare):
        """POST /api/system/restart is open to the LAN: whoever opened the
        challenge could otherwise reboot the unit into their own proof. The
        backend's stop clears the record, however the stop came."""
        before = bare.service()
        await before.initialize()
        await _open(before)

        bare.reboot()
        after = bare.service()
        await after.initialize()

        assert after.view() is None

    async def test_a_backend_killed_or_crashed_proves_nothing(self, bare):
        """A `milo` process can kill the backend; Restart= brings it back in
        the same boot, through the same stop hook."""
        before = bare.service()
        await before.initialize()
        await _open(before)

        bare.restart()
        after = bare.service()
        await after.initialize()

        assert after.view() is None

    async def test_a_record_the_stop_hook_missed_proves_nothing_in_its_own_boot(self, bare):
        """The boot id is the second lock: a record still there in the boot it
        was made in came from a stop whose hook failed, not from a power cut."""
        before = bare.service()
        await before.initialize()
        await _open(before)

        after = bare.service()
        await after.initialize()

        assert after.view() is None
        assert not bare.record.exists()

    async def test_a_challenge_that_ended_leaves_nothing_to_prove(self, bare):
        before = bare.service()
        await before.initialize()
        _, token = await _open(before)
        await before.cancel(token)

        bare.power_cut()
        after = bare.service()
        await after.initialize()

        assert after.view() is None

    async def test_the_record_is_consumed_at_the_start_that_reads_it(self, bare):
        """Kept, it would be proven again by every later power cut, for a
        challenge whose window is long past."""
        before = bare.service()
        await before.initialize()
        await _open(before)
        bare.power_cut()
        await bare.service().initialize()

        assert not bare.record.exists()
        bare.power_cut("boot-3")
        later = bare.service()
        await later.initialize()

        assert later.view() is None

    async def test_a_record_that_does_not_parse_proves_nothing(self, bare):
        bare.record.parent.mkdir()
        bare.record.write_text("{not json")
        bare.power_cut()

        service = bare.service()
        await service.initialize()

        assert service.view() is None
        assert not bare.record.exists()

    async def test_the_units_own_screen_opens_nothing_it_cannot_see_to_the_end(self, bare):
        """The screen goes down with the unit, and its page with the token. A
        challenge opened from it would end unspent and quiet the unit for the
        phone the owner is then sent to: the phone must be able to ask at once."""
        service = bare.service()
        await service.initialize()

        with pytest.raises(PresenceElsewhere) as refused:
            await service.require(SSH, None, ON_THE_UNIT)

        assert refused.value.detail["gesture"] == "power_cycle"
        assert service.view() is None
        assert not bare.record.exists(), "nothing for a power cut to prove"
        assert bare.published() == []
        challenge, _ = await _open(service)
        assert challenge["gesture"] == "power_cycle"

    async def test_a_client_that_waits_through_the_gesture_is_asked_for_it(self, bare):
        service = bare.service()
        await service.initialize()

        with pytest.raises(PresenceRequired):
            await service.require(SSH, None, ON_THE_UNIT | {Gesture.POWER_CYCLE})

    async def test_a_challenge_the_helper_cannot_record_is_refused(self, bare):
        """Asked to pull the plug for a record that was never written, the owner
        would come back to a refusal."""
        bare.helper_refuses = True
        service = bare.service()
        await service.initialize()

        with pytest.raises(PresenceUnavailable):
            await service.require(SSH, None)

        assert service.view() is None
