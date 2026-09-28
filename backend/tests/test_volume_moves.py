"""
How a level moves: the block a zone or the house moves as, the door every level
leaves through, and the per-speaker sender behind that door.

What breaks when these fail:
- a zone pushed to a limit loses its rooms' distances for good (measured on
  the unit: three rooms stored at -78 / -77.95 / -78);
- two gestures in flight together lose one of them (ten +2 dB steps landed as
  one while the store was written only after the hardware answered);
- a satellite ends at an older level than the one Milō shows, because two
  commands overtook each other or a retry re-sent a stale one;
- a speaker plays a level outside the operator's limits.

The outside world is the router (local CamillaDSP or a satellite's HTTP API);
every assertion is on what reached it, or on what the store holds. No clock is
measured: speakers that must be slow are held on an event and released.
"""
import asyncio
import logging
from unittest.mock import AsyncMock, Mock

import pytest

from backend.core.models.volume import VolumeConfig
from backend.core.volume import EqualizerController, VolumeService, VolumeStateStore
from backend.tests.volume_world import Registry
from backend.core.volume.state import StoredLevel

LIMITS = VolumeConfig(limit_min_db=-78.0, limit_max_db=-8.0, startup_volume_db=-60.0,
                      restore_last_volume=False)
LOCAL = "local-mac"


@pytest.fixture(autouse=True)
def _last_volume_in_tmp(tmp_path, monkeypatch):
    """The store persists for real, into the test's own directory."""
    monkeypatch.setattr(VolumeStateStore, "STORAGE_PATH", tmp_path / "last_volume.json")


class Router:
    """The outside world behind the door: records what each speaker receives.

    Commands to a speaker in `hold` wait until `release` is set; `answer` maps a
    speaker to the router result it gets back.
    """

    def __init__(self):
        self.received = []
        self.muted = []
        self.hold = set()
        self.release = asyncio.Event()
        self.answer = {}
        self.in_flight = {}
        self.peak_in_flight = 0

    async def set_volume(self, mac_id, volume_db, force=False):
        self.in_flight[mac_id] = self.in_flight.get(mac_id, 0) + 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight[mac_id])
        try:
            if mac_id in self.hold:
                await self.release.wait()
            self.received.append((mac_id, volume_db))
            return self.answer.get(mac_id, {"status": "success"})
        finally:
            self.in_flight[mac_id] -= 1

    async def set_mute(self, mac_id, muted, force=False):
        self.muted.append((mac_id, muted))
        return {"status": "success"}

    def last(self, mac_id):
        values = [volume for mac, volume in self.received if mac == mac_id]
        return values[-1] if values else None


async def settle():
    """Let the per-speaker senders drain: they run as their own tasks."""
    for _ in range(20):
        await asyncio.sleep(0)


def make_service(levels, online, multiroom=True, router=None, local=LOCAL, **config):
    """A VolumeService over `levels` ({mac: dB}), `online` reachable, the door
    wired to `router`."""
    settings = Mock()
    settings.invalidate_cache = Mock()
    settings.get_setting = AsyncMock(return_value=None)
    settings.set_setting = AsyncMock()
    registry = Registry(online=online, known=levels)
    service = VolumeService(
        state_machine=Mock(broadcast=AsyncMock()),
        snapcast_service=Mock(),
        settings_service=settings,
        camilladsp_service=Mock(
            is_volume_control_available=Mock(return_value=True),
            wait_for_connection=AsyncMock(return_value=True),
        ),
    )
    service._volume_config = VolumeConfig(**{**vars(LIMITS), **config})
    service._state_store.set_volume_config(service._volume_config)
    service._routing_service = Mock(get_state=Mock(return_value={"multiroom_enabled": multiroom}))
    service._client_registry = registry
    service._state_store.set_registry(registry)
    service._equalizer_controller.set_registry(registry)
    service._equalizer_controller._router = router or Router()
    service._state_store._local_mac_id = local
    service._state_store._clients = {
        mac: StoredLevel(volume_db=level, mute=False)
        for mac, level in levels.items()
    }
    return service


def zone(service, members):
    service._client_registry.zone("z", members, name="Z")


def level(service, mac):
    return service._state_store.get_client_volume(mac)


# ============================================================================
# What the screens are sent
# ============================================================================

async def test_the_multiroom_state_every_screen_and_phone_reads():
    """The snapshot Milo-Mac (`MultiroomVolume`), the web UI and the push
    service decode: levels as stored, availability, the zone average over its
    reachable members, a zone muted when every reachable member is."""
    service = make_service({LOCAL: -40.0, "sat": -50.0, "away": -60.0}, online=[LOCAL, "sat"])
    zone(service, ["sat", "away"])
    await service._state_store.set_client_mute("sat", True)

    state = (await service.get_volume_state()).to_dict()

    assert state == {
        "mode": "multiroom", "global_volume_db": -45.0, "global_volume": 0.4714,
        "global_mute": False, "limit_min_db": -78.0, "limit_max_db": -8.0,
        "volume_control": True, "any_volume_control": True,
        "clients": {
            LOCAL: {"volume_db": -40.0, "volume": 0.5429, "mute": False, "available": True,
                    "volume_control": True},
            "sat": {"volume_db": -50.0, "volume": 0.4, "mute": True, "available": True,
                    "volume_control": True},
            "away": {"volume_db": -60.0, "volume": 0.2571, "mute": False, "available": False,
                     "volume_control": True},
        },
        "zones": {"z": {"id": "z", "name": "Z", "client_ids": ["sat", "away"],
                        "average_volume_db": -50.0, "all_muted": True}},
    }


async def test_the_direct_state_is_the_local_speaker_alone():
    """In direct mode the global level is the local speaker's own, whatever
    the satellites the store still holds are at — and it is the one speaker
    published available, though the registry holds it offline (no snapclient
    runs). Published unavailable, the lock screen, which draws a slider for the
    available speakers only, had nothing to draw. The registry still holding a
    satellite online from before the switch counts for nothing either: the
    zone's figures are the local speaker's alone."""
    service = make_service({LOCAL: -40.0, "sat": -50.0}, online=["sat"], multiroom=False)
    zone(service, [LOCAL, "sat"])

    state = (await service.get_volume_state()).to_dict()

    assert state == {
        "mode": "direct", "global_volume_db": -40.0, "global_volume": 0.5429,
        "global_mute": False, "limit_min_db": -78.0, "limit_max_db": -8.0,
        "volume_control": True, "any_volume_control": True,
        "clients": {
            LOCAL: {"volume_db": -40.0, "volume": 0.5429, "mute": False, "available": True,
                    "volume_control": True},
            "sat": {"volume_db": -50.0, "volume": 0.4, "mute": False, "available": False,
                    "volume_control": True},
        },
        "zones": {"z": {"id": "z", "name": "Z", "client_ids": [LOCAL, "sat"],
                        "average_volume_db": -40.0, "all_muted": False}},
    }


async def test_leaving_multiroom_unmutes_the_local_speaker_in_the_store_too():
    """The way to direct unmutes the local speaker, the only one left playing.
    Done on the hardware alone, the store kept the mute: every screen showed it
    muted while it played, and the next CamillaDSP reconnect or boot — both
    apply the stored mute — silenced it."""
    router = Router()
    service = make_service({LOCAL: -40.0, "sat": -50.0}, online=[LOCAL, "sat"],
                           multiroom=False, router=router)
    await service._state_store.set_client_mute(LOCAL, True)

    await service.update_volume_mode(False)

    assert service._state_store.get_client_mute(LOCAL) is False
    assert router.muted == [(LOCAL, False)]
    assert (await service.get_volume_state()).global_mute is False


# ============================================================================
# The registry changes what the screens are sent
# ============================================================================

async def _published_by_the_registry(change, started=True):
    """A VolumeService over the real ClientRegistryService, as dependencies.py
    wires it — with a second subscriber after it that yields, as the snapcast
    WS re-emit does, and a broadcast that yields too; `change(registry)` moves
    the registry. Answers the volume states broadcast afterwards."""
    from backend.core.models.ws_events import VolumeChanged
    from backend.core.multiroom.client_registry import ClientRegistryService

    settings = AsyncMock()
    settings.get_setting = AsyncMock(return_value=None)
    registry = ClientRegistryService(settings_service=settings)
    await registry.initialize()
    service = make_service({LOCAL: -40.0, "sat": -60.0}, online=[])
    service.attach_registry(registry)

    async def re_emit(event_type, data):
        await asyncio.sleep(0)

    registry.subscribe(re_emit)
    published = []

    async def broadcast(event):
        await asyncio.sleep(0)
        if isinstance(event, VolumeChanged):
            published.append(event.state)

    service.state_machine.broadcast = broadcast
    service._started = started
    for mac, ip in ((LOCAL, "127.0.0.1"), ("sat", "192.168.1.60")):
        await registry.register_client(mac, mac, ip)
        await registry.set_client_online(mac, True)
    await settle()
    published.clear()

    await change(registry)
    await settle()
    return published


async def test_a_client_that_drops_is_published_gone_from_the_average():
    """Nothing published a client going offline: every screen kept it
    available and kept an average it no longer counts in, until the next move."""
    states = await _published_by_the_registry(
        lambda registry: registry.set_client_online("sat", False))

    assert len(states) == 1
    assert states[0]["clients"]["sat"]["available"] is False
    assert states[0]["global_volume_db"] == -40.0


async def test_a_client_handed_to_its_amp_is_published():
    """`volume_control` feeds `any_volume_control`, which the Dock and the iOS
    widget read; changing it published nothing."""
    states = await _published_by_the_registry(
        lambda registry: registry.update_client("sat", volume_control=False))

    assert len(states) == 1
    assert states[0]["clients"]["sat"]["volume_control"] is False


async def test_a_new_zone_is_published_with_its_figures():
    """A zone created had no average on any screen until a volume moved."""
    states = await _published_by_the_registry(
        lambda registry: registry.create_zone("z", "Salon", [LOCAL, "sat"]))

    assert states and states[-1]["zones"]["z"]["average_volume_db"] == -50.0


async def test_an_edit_the_volume_state_does_not_hold_publishes_nothing():
    """A trim or a delay is a client property the volume state does not carry:
    a slider dragged across it sent a full volume state per step to every
    screen, and woke the push coalescer each time, for nothing that moved."""
    async def edit(registry):
        await registry.set_client_gain("sat", 3.0)
        await registry.set_client_delay("sat", 40)

    assert await _published_by_the_registry(edit) == []


async def test_a_state_already_sent_is_not_sent_again():
    """The admission publishes the state its registry event already did."""
    service = make_service({LOCAL: -40.0}, online=[LOCAL])

    await service.broadcast_volume_state(show_bar=False)
    await service.broadcast_volume_state(show_bar=False)
    await service.broadcast_volume_state(show_bar=True)  # the bar is always shown

    assert service.state_machine.broadcast.await_count == 2


async def test_nothing_is_published_before_the_service_has_started():
    """The registry's boot sweep can come first; a state published then would
    carry defaults — `any_volume_control` among them, which the widget draws."""
    states = await _published_by_the_registry(
        lambda registry: registry.set_client_online("sat", False), started=False)

    assert states == []


# ============================================================================
# A zone's mute
# ============================================================================

async def test_a_zone_mute_is_one_request_stored_for_every_member_and_broadcast_once():
    """The web UI muted a zone with one PATCH per member, each broadcast, the
    offline members included. One call now: every member stores the mute (the
    away one takes it at its admission), only the reachable ones are sent it —
    one whose amp owns its level included, since a mute is not a level — and
    the screens hear of it once."""
    router = Router()
    service = make_service({LOCAL: -40.0, "amp": -40.0, "away": -40.0}, online=[LOCAL, "amp"],
                           router=router)
    service._client_registry.dac.add("amp")
    zone(service, [LOCAL, "amp", "away"])

    assert await service.set_zone_mute("z", True) == []
    await settle()

    assert all(service._state_store.get_client_mute(mac) for mac in (LOCAL, "amp", "away"))
    assert sorted(router.muted) == [("amp", True), (LOCAL, True)]
    assert service.state_machine.broadcast.await_count == 1


async def test_a_zone_of_dac_speakers_reads_muted_once_muted():
    """A zone mute mutes a member whose amp owns its level, so `all_muted`
    counts it. Counted out, a zone of such speakers read unmuted for ever, and
    the zone toggle, which sends the opposite of what it reads, could only mute."""
    service = make_service({"amp1": -40.0, "amp2": -40.0}, online=["amp1", "amp2"])
    service._client_registry.dac |= {"amp1", "amp2"}
    zone(service, ["amp1", "amp2"])

    await service.set_zone_mute("z", True)

    assert (await service.get_volume_state()).zones["z"].all_muted is True


# ============================================================================
# No step is lost
# ============================================================================

async def test_ten_steps_in_flight_together_all_land():
    """Ten +2 dB presses in flight at once move the house by 20 dB.

    Measured before the fix, with the real VolumeService in memory: +2, because
    every press read the levels before any had written them — the store was
    written only once the hardware answered. The satellite here answers only
    after all ten are in. Fails if a move reads a level another has not written.
    """
    router = Router()
    router.hold.add("sat")
    service = make_service({LOCAL: -60.0, "sat": -60.0}, online=[LOCAL, "sat"], router=router)

    presses = asyncio.gather(*[service.adjust_volume_db(2.0) for _ in range(10)])
    await settle()
    router.release.set()
    results = await presses
    await settle()

    assert all(results)
    assert level(service, LOCAL) == -40.0
    assert level(service, "sat") == -40.0
    assert router.last("sat") == -40.0


# ============================================================================
# The per-speaker sender
# ============================================================================

@pytest.fixture
def controller():
    ctrl = EqualizerController(equalizer_router=Router(), clamp=LIMITS.clamp)
    ctrl._timeout = 0.05
    return ctrl


async def test_a_burst_reaches_a_speaker_as_two_commands_ending_on_its_last_value(controller):
    """While one command is in flight, the next ones wait and only the latest leaves.

    Consumer: the rotary and the dock, whose steps outrun a satellite's round
    trip. Fails if the speaker is sent every intermediate value, or not the last.
    """
    router = controller._router
    router.hold.add("sat")
    futures = [controller.submit_volume("sat", -50.0)]
    await settle()  # the first command is in flight
    futures += [controller.submit_volume("sat", value) for value in (-49.0, -48.0, -47.0, -46.0)]
    await settle()
    router.release.set()

    assert await asyncio.gather(*futures) == [True] * 5
    assert router.received == [("sat", -50.0), ("sat", -46.0)]


async def test_commands_to_one_speaker_never_overlap_and_keep_their_order(controller):
    """A speaker never has two commands in flight, so none can overtake another.

    Two pooled connections could deliver an older level after a newer one, and
    the satellite would end where the store does not say. Fails if two commands
    to one speaker are ever in flight together, or if it ends anywhere but on
    the last value submitted.
    """
    router = controller._router
    router.hold.add("sat")
    submitted = []
    for burst in range(4):
        for step in range(3):
            submitted.append(-70.0 + burst * 3 + step)
            controller.submit_volume("sat", submitted[-1])
        await settle()
        router.release.set()
        await settle()
        router.release.clear()
    router.release.set()
    await settle()

    assert router.peak_in_flight == 1
    received = [volume for _, volume in router.received]
    assert received == sorted(received), "a later value arrived before an earlier one"
    assert received[-1] == submitted[-1]


async def test_a_retry_carries_the_value_that_is_current_when_it_leaves(controller, monkeypatch):
    """A command that timed out is retried with the latest value, never the stale one.

    The previous retry re-sent what the first attempt carried: after a slow
    spell, a satellite could be left at a level from before the last gesture.
    """
    monkeypatch.setattr(EqualizerController, "RETRY_DELAY", 0)
    router = controller._router
    router.hold.add("sat")
    first = controller.submit_volume("sat", -50.0)
    await settle()  # the first attempt is in flight, and will time out
    controller.submit_volume("sat", -40.0)
    router.hold.clear()

    await first

    assert router.received[0] == ("sat", -40.0)
    assert router.last("sat") == -40.0


async def test_a_sender_cancelled_before_it_starts_answers_and_frees_its_speaker(controller):
    """Shutdown can cancel a sender before its first step.

    Its cleanup never runs then, and the channel kept a dead task: the caller
    waited forever, and every later command to that speaker was never sent.
    """
    waiting = controller.submit_volume("sat", -50.0)
    await controller.cleanup()

    assert await waiting is False
    assert await controller.submit_volume("sat", -40.0) is True
    assert controller._router.last("sat") == -40.0


async def test_a_slow_satellite_holds_back_neither_the_local_speaker_nor_the_move():
    """A move waits for the local speaker only; the satellites converge on their own.

    The rotary's accumulator waits for each move before sending the next batch,
    so a move that waited for the slowest satellite slowed the local speaker
    down with it. The satellite here never answers during the move.
    """
    router = Router()
    router.hold.add("sat")
    service = make_service({LOCAL: -60.0, "sat": -60.0}, online=[LOCAL, "sat"], router=router)

    assert await service.adjust_volume_db(3.0) is True
    assert router.received == [(LOCAL, -57.0)]

    router.release.set()
    await settle()
    assert router.last("sat") == -57.0


async def test_a_speaker_that_refuses_keeps_the_level_asked_and_is_reported_once(caplog):
    """A refusal is logged once per spell, and the level asked for is kept.

    A satellite answers a refusal after caching the value, and applies it when
    its CamillaDSP comes back — so the level asked for is the one it will play.
    A turn of the knob against a refusing speaker must not log every step.
    """
    router = Router()
    router.answer["sat"] = {"status": "error", "message": "CamillaDSP not connected"}
    service = make_service({LOCAL: -60.0, "sat": -60.0}, online=[LOCAL, "sat"], router=router)

    with caplog.at_level(logging.ERROR):
        for _ in range(3):
            await service.adjust_volume_db(1.0)
            await settle()

    assert level(service, "sat") == -57.0
    assert len([r for r in caplog.records if "not applied to sat" in r.getMessage()]) == 1


async def test_a_new_session_with_a_speaker_reports_its_first_refusal_again(controller, caplog):
    """A spell of refusals ends at the next admission, not only at a success.

    A satellite that refused once, went away, and came back hours later with a
    new fault would otherwise refuse in silence for as long as the process ran.
    """
    controller._router.answer["sat"] = {"status": "error", "message": "refused"}

    with caplog.at_level(logging.ERROR):
        await controller.submit_volume("sat", -50.0)
        await controller.submit_volume("sat", -49.0)
        await controller.submit_volume("sat", -48.0, force=True)  # its admission

    assert len([r for r in caplog.records if "not applied to sat" in r.getMessage()]) == 2


async def test_a_move_the_local_speaker_refused_is_still_shown():
    """The levels were written and the satellites sent theirs; every screen must see it.

    The broadcast used to follow success only, so a local refusal left the web
    UI, Milo-Mac and Milo-iOS on the old levels of rooms that had moved.
    """
    router = Router()
    router.answer[LOCAL] = {"status": "error", "message": "refused"}
    service = make_service({LOCAL: -60.0, "sat": -60.0}, online=[LOCAL, "sat"], router=router)

    assert await service.set_volume_db(-50.0) is False

    service.state_machine.broadcast.assert_awaited()
    event = service.state_machine.broadcast.await_args_list[-1].args[0]
    assert event.state["clients"]["sat"]["volume_db"] == -50.0


async def test_a_move_landing_during_the_boot_push_is_not_overwritten():
    """The boot push stores before it sends, like every move.

    It used to write the store after the hardware answered, with the level it
    had read before: a rotary step landing in between was erased from the store
    while the speaker kept playing it.
    """
    router = Router()
    router.hold.add("sat")
    service = make_service({LOCAL: -60.0, "sat": -60.0}, online=[LOCAL, "sat"], router=router,
                           restore_last_volume=True)
    service._equalizer_controller.set_equalizer_mute = AsyncMock(return_value=True)
    service._equalizer_controller.set_equalizer_gain = AsyncMock(return_value=True)

    push = asyncio.ensure_future(service.push_volume_to_all_clients())
    await settle()
    await service.adjust_volume_db(2.0)
    router.release.set()
    await push
    await settle()

    assert level(service, "sat") == -58.0
    assert router.last("sat") == -58.0


# ============================================================================
# The block: a group stops when one of its rooms meets a limit
# ============================================================================

@pytest.mark.parametrize("delta, loudest, quietest, expected", [
    (10.0, -12.0, -40.0, 4.0),     # up: stops when the loudest reaches the maximum
    (-30.0, -40.0, -60.0, -18.0),  # down: stops when the quietest reaches the minimum
    (-5.0, -70.0, -80.0, 0.0),     # a room already past the floor never turns a step down into a step up
    (5.0, -40.0, -60.0, 5.0),      # inside the range: the delta passes whole
], ids=["up", "down", "never-reversed", "inside"])
def test_bound_block_delta(delta, loudest, quietest, expected):
    """The part of a move a block can make: the first room to meet a limit stops it."""
    assert LIMITS.bound_block_delta(delta, loudest, quietest) == expected


async def test_a_zone_pulled_to_the_floor_keeps_its_distances_and_gets_them_back():
    """A zone pulled down stops when its quietest room reaches the floor, and comes back exactly.

    Measured on the unit before the fix: the three rooms were stored at
    -78 / -77.95 / -78, because each room was clamped while the block kept
    moving, and the distances between the rooms were gone for good.
    """
    router = Router()
    service = make_service({"a": -40.0, "b": -50.0, "c": -60.0}, online=["a", "b", "c"],
                           router=router, local=None)
    zone(service, ["a", "b", "c"])

    await service.apply_zone_volume_delta("z", -30.0)
    await settle()
    assert [level(service, m) for m in "abc"] == [-58.0, -68.0, -78.0]
    assert [router.last(m) for m in "abc"] == [-58.0, -68.0, -78.0]

    await service.apply_zone_volume_delta("z", 18.0)
    assert [level(service, m) for m in "abc"] == [-40.0, -50.0, -60.0]


async def test_the_house_going_up_stops_when_its_loudest_room_reaches_the_maximum():
    """Going up, the whole house stops when its loudest reachable room hits the ceiling."""
    service = make_service({LOCAL: -12.0, "sat": -40.0}, online=[LOCAL, "sat"])

    await service.adjust_volume_db(10.0)

    assert level(service, LOCAL) == -8.0
    assert level(service, "sat") == -36.0


async def test_the_house_going_down_stops_when_its_quietest_room_reaches_the_minimum():
    """Going down, the whole house stops when its quietest reachable room hits the floor.

    The knob then stops turning the house down: the rooms keep their balance,
    and the quiet one is not squeezed against the floor while the others go on.
    """
    service = make_service({LOCAL: -70.0, "sat": -76.0}, online=[LOCAL, "sat"])

    await service.adjust_volume_db(-10.0)

    assert level(service, LOCAL) == -72.0
    assert level(service, "sat") == -78.0


async def test_a_house_with_no_speaker_reachable_still_moves_as_a_block():
    """With every speaker away, the block is bounded by all its rooms.

    Unbounded, a step taken while nothing was online moved each stored level by
    the whole delta and clamped them one by one, and the rooms collapsed onto a
    limit — the very loss the block exists to prevent.
    """
    router = Router()
    service = make_service({"a": -40.0, "b": -70.0}, online=[], router=router, local=None)

    await service.adjust_volume_db(-10.0)
    await settle()

    assert level(service, "a") == -48.0
    assert level(service, "b") == -78.0
    assert router.received == []


async def test_a_room_that_is_away_moves_with_the_house_but_does_not_hold_it_back():
    """An absent room moves by the same delta, clamped to the limits, and bounds nothing.

    Letting it bound the block would let a speaker unplugged at -10 freeze the
    whole house at +2 dB with nothing on screen to say why. It is stored only.
    """
    router = Router()
    service = make_service({LOCAL: -12.0, "sat": -30.0, "away": -10.0}, online=[LOCAL, "sat"],
                           router=router)

    await service.adjust_volume_db(10.0)
    await settle()

    # Bounded by the loudest *reachable* room (-12 -> +4): the other reachable
    # one keeps its 18 dB below it; clamping each room instead would give -20,
    # and letting the absent one bound the block -28.
    assert level(service, LOCAL) == -8.0
    assert level(service, "sat") == -26.0
    assert level(service, "away") == -8.0
    assert router.last("away") is None


# ============================================================================
# The door: whatever reaches a speaker is inside the limits
# ============================================================================

async def _startup(service):
    await service._apply_startup_volume()


async def _boot_push(service):
    service._equalizer_controller.set_equalizer_mute = AsyncMock(return_value=True)
    service._equalizer_controller.set_equalizer_gain = AsyncMock(return_value=True)
    await service.push_volume_to_all_clients()


async def _admission(service):
    await service.equalizer_controller.set_equalizer_volume(
        LOCAL, service.volume_config.startup_volume_db, force=True
    )


@pytest.mark.parametrize("path", [_startup, _boot_push, _admission],
                         ids=["startup", "boot-push", "admission"])
async def test_a_startup_level_below_the_floor_is_played_at_the_floor(path):
    """startup_volume_db is checked against the limits when it is saved, not after.

    Raise the minimum above it, and every path that applies it — the local boot,
    the boot push, a client's admission — would send a level under the floor.
    The door clamps it, for every caller at once (the boot push also stores it
    first, and the store clamps too). Fails if any path sends it raw.
    """
    router = Router()
    service = make_service({LOCAL: -60.0}, online=[LOCAL], router=router, startup_volume_db=-90.0)

    await path(service)
    await settle()

    assert router.last(LOCAL) == -78.0


async def test_a_level_saved_under_older_limits_is_brought_into_them_at_load():
    """last_volume.json may hold a level the limits no longer allow (changed while
    the unit was off): the store brings it to the nearest limit as it loads it."""
    store = VolumeStateStore()
    store.set_volume_config(VolumeConfig(limit_min_db=-90.0, limit_max_db=-8.0))
    store._clients["room"] = StoredLevel(volume_db=-90.0)
    await store._persist_state_async()

    restored = VolumeStateStore()
    restored.set_volume_config(LIMITS)
    await restored.initialize()

    assert restored.get_client_volume("room") == -78.0
