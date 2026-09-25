"""The per-source actor: one message at a time, lifecycle first, stale mail dropped.

Every source runs its lifecycle, its commands, its pause timer and its state
reads through one mailbox drained by one task (core/audio_source.py). What
these tests pin is the ordering the rest of the appliance relies on — a
handler's check before an await is still true after it — observed through the
outside world only: a systemd double that tracks what the unit really is, and
the responses callers get back.
"""
import asyncio
from typing import List
from unittest.mock import AsyncMock

import pytest

from backend.core.audio_source import BaseAudioSource


class Unit:
    """systemd for one unit: every call is logged, a call can be held open."""

    def __init__(self) -> None:
        self.active = False
        self.calls: List[str] = []
        self.hold: dict[str, asyncio.Event] = {}

    async def _run(self, verb: str, active_after: bool) -> bool:
        self.calls.append(verb)
        if verb in self.hold:
            await self.hold[verb].wait()
        self.active = active_after
        self.calls.append(f"{verb} done")
        return True

    async def start(self, name):
        return await self._run("start", True)

    async def stop(self, name):
        return await self._run("stop", False)

    async def restart(self, name):
        return await self._run("restart", True)


class Probe(BaseAudioSource):
    """A source whose handlers do their work against the Unit double."""

    COMMANDS = {"play": None, "slow": None, "pause": None}

    def __init__(self, unit: Unit) -> None:
        super().__init__(source_id="radio", service_name="milo-probe.service",
                         systemd_manager=unit)
        self.auto_stop_enabled = True
        self.auto_stop_delay = 0.0
        self.log: List[str] = []
        self.gate = asyncio.Event()
        self.reads = 0

    async def _do_start(self) -> bool:
        return await self._start_service()

    async def _handle_command(self, cmd, params):
        self.log.append(f"{cmd} begin")
        if cmd == "slow":
            await self.gate.wait()
        if cmd == "play":
            self._cancel_pause_timer()
        if cmd == "pause":
            self._start_pause_timer()
        self.log.append(f"{cmd} end")
        return self.success_response(cmd)

    async def refresh_metadata(self) -> bool:
        self.reads += 1
        return True


async def _until(predicate) -> None:
    for _ in range(1000):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition never reached")


@pytest.fixture
async def probe():
    source = Probe(Unit())
    yield source
    await source.shutdown()


async def test_a_command_posted_during_a_handler_runs_after_it(probe):
    first = asyncio.ensure_future(probe.command("slow", None))
    await _until(lambda: "slow begin" in probe.log)
    second = asyncio.ensure_future(probe.command("play", None))
    for _ in range(20):
        await asyncio.sleep(0)
    assert "play begin" not in probe.log

    probe.gate.set()
    assert (await first)["success"] and (await second)["success"]
    assert probe.log == ["slow begin", "slow end", "play begin", "play end"]


async def test_a_command_waits_for_the_start_in_flight():
    """What a press during a source switch meets: a started source, not half of one."""
    unit = Unit()
    unit.hold["start"] = asyncio.Event()
    source = Probe(unit)
    starting = asyncio.ensure_future(source.start())
    await _until(lambda: unit.calls == ["start"])
    pressed = asyncio.ensure_future(source.command("play", None))
    for _ in range(20):
        await asyncio.sleep(0)
    assert source.log == []

    unit.hold["start"].set()
    assert await starting
    await pressed
    assert source.log == ["play begin", "play end"]
    await source.shutdown()


async def test_stop_preempts_the_command_in_flight(probe):
    """Stop beats ordered: a load still waiting on the network does not get to
    finish over a source that was told to stop."""
    pressed = asyncio.ensure_future(probe.command("slow", None))
    await _until(lambda: "slow begin" in probe.log)

    assert await probe.stop()
    async with asyncio.timeout(5):              # a hang guard, not a budget
        response = await pressed
    assert not response["success"]
    assert "interrupted" in response["error"]
    probe.gate.set()
    for _ in range(20):
        await asyncio.sleep(0)
    assert "slow end" not in probe.log


async def test_a_stop_whose_caller_gave_up_still_finishes():
    """The transition's 15 s budget cuts the *wait*, never the teardown: a
    half-stopped unit is what kept the ALSA device held for every later start."""
    unit = Unit()
    unit.hold["stop"] = asyncio.Event()
    source = Probe(unit)
    await source.start()

    with pytest.raises(asyncio.TimeoutError):
        async with asyncio.timeout(0.01):
            await source.stop()
    assert unit.calls[-1] == "stop"

    unit.hold["stop"].set()
    await _until(lambda: unit.calls[-1] == "stop done")
    assert unit.calls.count("stop") == 1
    assert not unit.active
    await source.shutdown()


async def test_an_auto_stop_cut_by_a_stop_does_not_bring_the_unit_back():
    """The pause timer restarts the source (stop + start); a transition that
    stops it meanwhile must be the last word. Before, the timer's restart went
    on after the transition's stop and left the daemon running next to the
    next source."""
    unit = Unit()
    source = Probe(unit)
    await source.start()
    unit.hold["stop"] = asyncio.Event()

    await source.command("pause", None)             # arms a 0 s timer
    await _until(lambda: unit.calls.count("stop") == 1)   # the restart's stop is held

    switching_away = asyncio.ensure_future(source.stop())
    for _ in range(20):
        await asyncio.sleep(0)
    asked_to_stop_at = len(unit.calls)
    unit.hold["stop"].set()
    assert await switching_away
    for _ in range(50):
        await asyncio.sleep(0)

    # Order of issue is the fact: systemd merges the two stops into one job, and
    # a start issued after them runs the daemon again whatever the double says.
    assert "start" not in unit.calls[asked_to_stop_at:]
    await source.shutdown()


async def test_a_timer_that_expired_behind_a_play_is_dropped(probe):
    """The play that disarms the timer runs first; the expiry queued behind it
    belongs to a pause that no longer exists."""
    await probe.start()
    blocking = asyncio.ensure_future(probe.command("slow", None))
    await _until(lambda: "slow begin" in probe.log)
    play = asyncio.ensure_future(probe.command("play", None))
    for _ in range(20):
        await asyncio.sleep(0)                       # play is queued
    probe._start_pause_timer()                      # the pause that was in effect
    for _ in range(20):
        await asyncio.sleep(0)                       # 0 s: expires now, behind play
    probe.gate.set()
    await blocking
    await play
    for _ in range(50):
        await asyncio.sleep(0)

    assert probe._service_manager.calls == ["start", "start done"]
    await probe.shutdown()


async def test_a_state_read_never_waits_for_a_handler(probe):
    """GET /api/audio/state and the WS handshake read through here, and Milo-iOS
    gives up after 3 s while a podcast resume can hold its handler for 10."""
    pressed = asyncio.ensure_future(probe.command("slow", None))
    await _until(lambda: "slow begin" in probe.log)

    assert await probe.refresh_when_idle() is False
    assert probe.reads == 0

    probe.gate.set()
    await pressed
    assert await probe.refresh_when_idle() is True
    assert probe.reads == 1


async def test_shutdown_ends_the_mailbox(probe):
    await probe.command("play", None)
    await probe.shutdown()
    response = await probe.command("play", None)
    assert not response["success"]


async def test_a_failed_start_answers_false_and_leaves_the_mailbox_alive():
    """The failure is the state machine's to show (`service: failed`); the
    source must answer it and still take the retry's messages."""
    unit = Unit()
    unit.start = AsyncMock(side_effect=RuntimeError("unit refused"))
    source = Probe(unit)
    assert await source.start() is False
    assert (await source.command("play", None))["success"]
    await source.shutdown()


class Flaky(Probe):
    """A source whose projection can blow up, the way a model_validate can."""

    def __init__(self, unit):
        super().__init__(unit)
        self.broken = False

    def _controls(self):
        if self.broken:
            raise ValueError("unexpected AVRCP payload")
        # Moves with every command, so the net has something to republish.
        return [f"after-{len(self.log)}"]


async def test_a_projection_that_raises_does_not_kill_the_mailbox():
    """The net runs after every command; a source whose projection raises must
    cost that republish, not every later command, stop and start."""
    source = Flaky(Unit())
    await source.command("play", None)
    source._publish()
    source.broken = True

    async with asyncio.timeout(5):              # a hang guard, not a budget
        first = await source.command("play", None)
        source.broken = False
        second = await source.command("play", None)

    assert first["success"] and second["success"]
    await source.shutdown()


async def test_shutdown_answers_the_message_in_flight(probe):
    pressed = asyncio.ensure_future(probe.command("slow", None))
    await _until(lambda: "slow begin" in probe.log)

    await probe.shutdown()
    async with asyncio.timeout(5):
        response = await pressed
    assert not response["success"]


async def test_a_start_after_shutdown_is_refused_at_once():
    source = Probe(Unit())
    await source.shutdown()
    async with asyncio.timeout(5):
        assert await source.start() is False


async def test_a_stop_voids_the_commands_queued_before_it(probe):
    """A command posted before the stop targets the session the stop ends."""
    first = asyncio.ensure_future(probe.command("slow", None))
    await _until(lambda: "slow begin" in probe.log)
    queued = asyncio.ensure_future(probe.command("play", None))
    for _ in range(20):
        await asyncio.sleep(0)

    assert await probe.stop()
    assert not (await queued)["success"]
    assert "play begin" not in probe.log
    await first


async def test_a_held_mailbox_queues_what_arrives_meanwhile(probe):
    """The multiroom reroute holds the source across RELEASE, the output switch
    and ACQUIRE: a command or a stop arriving in between waits for the whole of
    it — a resume must not land on an output parked on `null`, and a stop must
    not be undone by the reacquire."""
    await probe.start()
    order = []
    async with probe.hold_mailbox():
        await probe.release_for_reroute()
        pressed = asyncio.ensure_future(probe.command("play", None))
        stopping = asyncio.ensure_future(probe.stop())
        for _ in range(20):
            await asyncio.sleep(0)
        order.append("still held")
        assert probe.log == []
        await probe.acquire_after_reroute()
    await stopping
    await pressed
    assert probe._service_manager.calls[-2:] == ["stop", "stop done"]
    assert not probe._service_manager.active


# === Work on the hardware outlives a stop (DeviceToken) ===

async def test_a_stop_neither_voids_nor_cuts_the_hardware_s_news(probe):
    """A disc read cut by a source switch left the drive "reading" with
    nothing left to finish it: a STOP answers the session's mail, not the
    device's. Queued before the stop, the news still runs; running when it
    arrives, it runs to its end."""
    from backend.core.audio_source import DeviceToken
    token = DeviceToken()
    reading = asyncio.Event()
    done: List[str] = []

    async def read_the_disc():
        done.append("read begin")
        await reading.wait()
        done.append("read end")

    async def news():
        done.append("news")

    probe._post_result(read_the_disc, token=token)
    await _until(lambda: done == ["read begin"])
    probe._post_result(news, token=token)
    stopping = asyncio.ensure_future(probe.stop())
    for _ in range(20):
        await asyncio.sleep(0)
    reading.set()
    assert await stopping
    await _until(lambda: "news" in done)
    assert done == ["read begin", "read end", "news"]


async def test_a_stop_still_voids_the_session_s_results(probe):
    """The other half: a result for a session is dropped by the stop that
    ends it (the rule the device exemption must not widen)."""
    ran: List[str] = []

    async def late():
        ran.append("late")

    blocking = asyncio.ensure_future(probe.command("slow", None))
    await _until(lambda: "slow begin" in probe.log)
    probe._post_result(late)
    assert await probe.stop()
    probe.gate.set()
    await asyncio.gather(blocking, return_exceptions=True)
    for _ in range(20):
        await asyncio.sleep(0)
    assert ran == []


async def test_a_device_timer_survives_the_end_of_a_session(probe):
    """The disc's watchdog belongs to the drive: a session ending (a switch,
    an eject) disarms the session's timers, not the drive's."""
    from backend.core.audio_source import DeviceToken
    from backend.core.models.session import EndReason, Phase, Session

    probe._arm_timer("reading", 999, DeviceToken())
    probe._arm_timer("stall", 999, probe.open_session(Session(phase=Phase.LOADING)))
    await probe.end_session(EndReason.USER_STOP)

    assert probe._timer_armed("reading")
    assert not probe._timer_armed("stall")
