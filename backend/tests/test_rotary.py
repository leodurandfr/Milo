# backend/tests/test_rotary.py
"""The KY-040 volume knob — the appliance's most-used physical control.

This module had no test file at all: every method body was at 0 %, including
the quadrature decode that decides *which way* the volume goes. Nothing in the
suite could tell a knob turned clockwise from one turned counter-clockwise.

`lgpio` IS installed on this host and the running backend holds CLK/DT/SW, so
the autouse fixture below makes the real chip refuse every call: a test that
misses the stand-in fails loudly instead of fighting the physical knob.

What breaks when these fail: turning the knob moves the volume the wrong way,
or not at all, and a press stops reaching PlaybackDispatcher (1 click =
play/pause, 2 = next, 3 = prev). None of it leaves a trace in the journal —
`docs/manual/verification-checklist.md` and the nginx log are the only proof on
a unit, which is exactly why the decode has to be pinned here.
"""
import asyncio
import types
from unittest.mock import AsyncMock

import pytest

from backend.hardware import rotary as rotary_module
from backend.hardware.rotary import RotaryVolumeController

CLK, DT, SW = 22, 27, 23
STEP_DB = 2.0
HANDLE = 7  # arbitrary; only its round-trip from open to close is asserted


@pytest.fixture(autouse=True)
def never_the_real_gpio(monkeypatch):
    """The real lgpio refuses everything for the duration of this module."""
    try:
        import lgpio
    except ImportError:  # pragma: no cover - lgpio is present on the appliance
        return

    def refuse(*_args, **_kwargs):
        raise AssertionError("a test reached the appliance's real GPIO chip")

    for name in ("gpiochip_open", "gpio_claim_input", "gpio_read",
                 "gpio_free", "gpiochip_close"):
        monkeypatch.setattr(lgpio, name, refuse)


class FakeLgpio:
    """The kernel's GPIO chip: a pin-level table plus a log of what was asked."""

    SET_PULL_UP = 0x20  # opaque to the controller, which only forwards it

    def __init__(self, levels):
        self.levels = dict(levels)
        self.opened = None
        self.claimed = []
        self.reads = []
        self.freed = []
        self.closed = []
        self.open_error = None
        self.claim_error = None
        self.close_error = None
        self.free_error = None
        self.read_error = None

    def gpiochip_open(self, chip):
        if self.open_error:
            raise self.open_error
        self.opened = chip
        return HANDLE

    def gpio_claim_input(self, handle, pin, flags):
        if self.claim_error and pin == self.claim_error[0]:
            raise self.claim_error[1]
        self.claimed.append((handle, pin, flags))

    def gpio_read(self, handle, pin):
        if self.read_error:
            error, self.read_error = self.read_error, None
            raise error
        self.reads.append(pin)
        return self.levels[pin]

    def gpio_free(self, handle, pin):
        if self.free_error:
            raise self.free_error
        self.freed.append((handle, pin))

    def gpiochip_close(self, handle):
        if self.close_error:
            raise self.close_error
        self.closed.append(handle)


class Clock:
    """Monotonic time the test owns — the debounce windows are 5 ms and 20 ms."""

    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def gpio(monkeypatch):
    fake = FakeLgpio({CLK: 0, DT: 0, SW: 1})
    # `raising=False`: requirements.txt installs lgpio on aarch64 only, so off the
    # appliance `rotary` never binds the name and a plain setattr is an
    # AttributeError -- every test here errored on CI while passing on the unit.
    monkeypatch.setattr(rotary_module, "lgpio", fake, raising=False)
    monkeypatch.setattr(rotary_module, "LGPIO_AVAILABLE", True)
    return fake


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(rotary_module, "monotonic", c)
    return c


@pytest.fixture
def rot(gpio, clock, monkeypatch):
    """A controller whose two collaborators are recorded rather than driven.

    `VolumeAccumulator` and `PlaybackDispatcher` are separate units with their
    own test files; the encoder's whole contract is the *sign and size* of the
    delta it hands over, and the fact a press produces exactly one click.
    """
    volume_service = types.SimpleNamespace(
        volume_config=types.SimpleNamespace(step_rotary_db=STEP_DB)
    )
    controller = RotaryVolumeController(
        volume_service, state_machine=types.SimpleNamespace(),
        clk_pin=CLK, dt_pin=DT, sw_pin=SW,
    )
    deltas = []
    monkeypatch.setattr(controller._volume, "accumulate", deltas.append)
    monkeypatch.setattr(controller._volume, "cleanup", AsyncMock())
    clicks = AsyncMock()
    monkeypatch.setattr(controller._dispatcher, "on_click", clicks)
    monkeypatch.setattr(controller._dispatcher, "cleanup", AsyncMock())
    return types.SimpleNamespace(
        controller=controller, gpio=gpio, clock=clock,
        deltas=deltas, clicks=clicks, volume_service=volume_service,
    )


def _edge(rot, clk, dt):
    """Move the encoder to one (CLK, DT) position and let the controller read it."""
    rot.gpio.levels[CLK] = clk
    rot.gpio.levels[DT] = dt
    return rot.controller._check_rotation()


# ------------------------------------------------------------------ direction
class TestWhichWayTheKnobTurns:
    """The quadrature decode: DT read *against* CLK, not against a fixed level.

    A KY-040 emits one CLK edge per half-detent and the direction is carried by
    whether DT differs from CLK at that instant — which is why the same test
    holds on the rising and the falling edge. Invert it and every turn moves
    the volume the wrong way.
    """

    def test_a_clockwise_edge_asks_for_a_step_up(self, rot):
        rot.controller.last_clk = 0
        assert _edge(rot, clk=1, dt=0) is True
        assert rot.deltas == [STEP_DB]

    def test_a_counter_clockwise_edge_asks_for_a_step_down(self, rot):
        rot.controller.last_clk = 0
        assert _edge(rot, clk=1, dt=1) is True
        assert rot.deltas == [-STEP_DB]

    def test_both_edges_of_one_clockwise_detent_move_the_same_way(self, rot):
        """0,0 → 1,0 → 1,1 → 0,1 → 0,0: two CLK edges, both must count up."""
        rot.controller.last_clk = 0
        for clk, dt in ((1, 0), (1, 1), (0, 1), (0, 0)):
            rot.clock.now += 1.0
            _edge(rot, clk, dt)
        assert rot.deltas == [STEP_DB, STEP_DB]

    def test_both_edges_of_one_counter_clockwise_detent_move_the_same_way(self, rot):
        """0,0 → 0,1 → 1,1 → 1,0 → 0,0: the mirror sequence, both down."""
        rot.controller.last_clk = 0
        for clk, dt in ((0, 1), (1, 1), (1, 0), (0, 0)):
            rot.clock.now += 1.0
            _edge(rot, clk, dt)
        assert rot.deltas == [-STEP_DB, -STEP_DB]

    def test_a_still_knob_asks_for_nothing_and_never_reads_dt(self, rot):
        rot.controller.last_clk = 1
        rot.gpio.levels[CLK] = 1
        rot.gpio.reads.clear()

        assert rot.controller._check_rotation() is False

        assert rot.deltas == []
        assert DT not in rot.gpio.reads, "DT is only meaningful on a CLK edge"


class TestTheStepSizeIsTheSettingNotAConstant:
    def test_a_new_rotary_step_takes_effect_without_a_restart(self, rot):
        """`PUT /api/settings/rotary-steps` writes the config the encoder reads.

        The value is looked up on every edge; caching it at construction would
        make the slider in Réglages do nothing until milo-backend restarts.
        """
        rot.controller.last_clk = 0
        _edge(rot, clk=1, dt=0)

        rot.volume_service.volume_config.step_rotary_db = 5.0
        rot.clock.now += 1.0
        _edge(rot, clk=0, dt=1)

        assert rot.deltas == [2.0, 5.0]


class TestContactBounce:
    """5 ms of KY-040 bounce must not become five volume steps."""

    def test_a_second_edge_inside_the_window_is_activity_but_not_a_step(self, rot):
        rot.controller.last_clk = 0
        _edge(rot, clk=1, dt=0)
        assert rot.deltas == [STEP_DB]

        rot.clock.now += RotaryVolumeController.DEBOUNCE_TIME / 2
        assert _edge(rot, clk=0, dt=1) is True, "a bounce still counts as activity"
        assert rot.deltas == [STEP_DB], "but it must not move the volume again"

    def test_an_edge_past_the_window_is_a_step_again(self, rot):
        rot.controller.last_clk = 0
        _edge(rot, clk=1, dt=0)

        rot.clock.now += RotaryVolumeController.DEBOUNCE_TIME * 2
        _edge(rot, clk=0, dt=1)

        assert rot.deltas == [STEP_DB, STEP_DB]


# --------------------------------------------------------------------- button
class TestThePushButton:
    """SW is pulled up, so a press is a falling edge and a release a rising one."""

    async def test_a_press_dispatches_exactly_one_click(self, rot):
        rot.gpio.levels[SW] = 0
        assert await rot.controller._check_button() is True
        rot.clicks.assert_awaited_once_with()

    async def test_a_release_dispatches_nothing(self, rot):
        rot.gpio.levels[SW] = 0
        await rot.controller._check_button()
        rot.clicks.reset_mock()

        rot.clock.now += 1.0
        rot.gpio.levels[SW] = 1
        assert await rot.controller._check_button() is True

        rot.clicks.assert_not_awaited()

    async def test_holding_the_button_does_not_repeat(self, rot):
        rot.gpio.levels[SW] = 0
        await rot.controller._check_button()
        rot.clock.now += 1.0

        assert await rot.controller._check_button() is False, "no state change"
        assert rot.clicks.await_count == 1

    async def test_a_bounce_inside_the_button_window_is_not_a_second_click(self, rot):
        """Press, bounce back up, press again 1 ms later — one click, not two."""
        rot.gpio.levels[SW] = 0
        await rot.controller._check_button()

        rot.clock.now += 0.001
        rot.gpio.levels[SW] = 1
        await rot.controller._check_button()
        rot.clock.now += 0.001
        rot.gpio.levels[SW] = 0
        await rot.controller._check_button()

        assert rot.clicks.await_count == 1

    async def test_a_deliberate_second_press_past_the_window_is_a_second_click(self, rot):
        rot.gpio.levels[SW] = 0
        await rot.controller._check_button()

        rot.clock.now += RotaryVolumeController.BUTTON_DEBOUNCE_TIME * 2
        rot.gpio.levels[SW] = 1
        await rot.controller._check_button()
        rot.clock.now += RotaryVolumeController.BUTTON_DEBOUNCE_TIME * 2
        rot.gpio.levels[SW] = 0
        await rot.controller._check_button()

        assert rot.clicks.await_count == 2


# ------------------------------------------------------------------- lifecycle
class TestInitialize:
    async def test_the_three_pins_are_claimed_as_pulled_up_inputs(self, rot):
        """A floating input reads noise, so the knob would free-run on its own."""
        rot.gpio.levels[CLK] = 1

        assert await rot.controller.initialize() is True

        assert rot.gpio.opened == 0, "the encoder is on gpiochip0"
        assert rot.gpio.claimed == [
            (HANDLE, CLK, FakeLgpio.SET_PULL_UP),
            (HANDLE, DT, FakeLgpio.SET_PULL_UP),
            (HANDLE, SW, FakeLgpio.SET_PULL_UP),
        ]
        assert rot.controller.last_clk == 1, "the first edge is measured against this"
        assert rot.controller.running is True
        await rot.controller.cleanup()

    async def test_cleanup_cancels_the_loop_rather_than_waiting_it_out(self, rot):
        """Clearing `running` alone makes teardown wait out the poll interval —
        up to a second on the error path — with the loop still reading GPIO
        lines cleanup is about to free."""
        await rot.controller.initialize()
        task = rot.controller._monitor_task
        assert task is not None and not task.done()
        await asyncio.sleep(0)  # let the loop reach its first await

        await rot.controller.cleanup()

        assert task.cancelled()
        assert rot.controller._monitor_task is None

    async def test_a_chip_that_will_not_open_is_a_failure_not_a_crash(self, rot):
        rot.gpio.open_error = OSError("GPIO busy")

        assert await rot.controller.initialize() is False

        assert rot.controller.running is False
        assert rot.controller.chip_handle is None

    async def test_a_pin_another_process_holds_gives_the_chip_back(self, rot):
        """Half-claimed lines would stay claimed for the life of the process."""
        rot.gpio.claim_error = (SW, OSError("GPIO busy"))

        assert await rot.controller.initialize() is False

        assert rot.gpio.closed == [HANDLE]
        assert rot.controller.chip_handle is None

    async def test_a_host_without_lgpio_is_not_a_failure_and_touches_no_gpio(
        self, rot, monkeypatch
    ):
        """Fail-open: a dev box has no encoder and must still boot the backend."""
        monkeypatch.setattr(rotary_module, "LGPIO_AVAILABLE", False)

        assert await rot.controller.initialize() is True

        assert rot.gpio.opened is None
        assert rot.controller.running is False


class TestCleanup:
    async def test_every_pin_is_freed_and_the_chip_closed(self, rot):
        await rot.controller.initialize()

        await rot.controller.cleanup()

        assert rot.gpio.freed == [(HANDLE, CLK), (HANDLE, DT), (HANDLE, SW)]
        assert rot.gpio.closed == [HANDLE]
        assert rot.controller.chip_handle is None
        assert rot.controller.running is False

    async def test_both_collaborators_are_drained_before_the_pins_go(self, rot):
        """The dispatcher holds a 400 ms multi-click timer and the accumulator a
        drain task: left alive, they dispatch a play/pause after the controller
        has already released the hardware."""
        await rot.controller.initialize()

        await rot.controller.cleanup()

        rot.controller._dispatcher.cleanup.assert_awaited_once_with()
        rot.controller._volume.cleanup.assert_awaited_once_with()

    async def test_a_pin_that_will_not_free_still_lets_the_chip_close(self, rot):
        await rot.controller.initialize()
        rot.gpio.free_error = OSError("already released")

        await rot.controller.cleanup()

        assert rot.gpio.closed == [HANDLE]

    async def test_a_chip_that_will_not_close_still_drops_the_handle(self, rot, caplog):
        """Keeping a dead handle would make the next initialize() read through it."""
        await rot.controller.initialize()
        rot.gpio.close_error = OSError("chip gone")

        await rot.controller.cleanup()

        assert rot.controller.chip_handle is None
        assert "Error during GPIO cleanup" in caplog.text

    async def test_cleanup_before_initialize_touches_no_gpio(self, rot):
        await rot.controller.cleanup()
        assert rot.gpio.freed == []
        assert rot.gpio.closed == []


# ------------------------------------------------------------------ the loop
class TestTheAdaptivePollingLoop:
    """1 ms while the knob is being used, ~16 ms at rest.

    The window is what keeps a Pi core free between turns without losing the
    first edge of the next one — a loop stuck on the idle interval feels laggy,
    one stuck on the active interval polls a core at 1 kHz forever.
    """

    async def _run(self, rot, monkeypatch, ticks, advance, between=None):
        """Drive `ticks` passes of the loop, advancing the clock between each."""
        delays = []

        async def fake_sleep(delay):
            delays.append(delay)
            rot.clock.now += advance
            if between:
                between(len(delays))
            if len(delays) >= ticks:
                rot.controller.running = False

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)
        rot.controller.running = True
        await rot.controller._monitor_loop()
        return delays

    async def test_a_moving_knob_keeps_the_fast_interval(self, rot, monkeypatch):
        """Every tick sees an edge, so the window never closes."""
        rot.controller.last_clk = 0
        rot.gpio.levels[CLK] = 1

        def flip(_tick):
            rot.gpio.levels[CLK] ^= 1

        delays = await self._run(
            rot, monkeypatch, ticks=3,
            advance=RotaryVolumeController.ACTIVE_WINDOW_S + 0.5, between=flip,
        )
        assert delays == [RotaryVolumeController.ACTIVE_POLL_S] * 3

    async def test_a_knob_left_alone_falls_back_to_the_idle_interval(
        self, rot, monkeypatch
    ):
        rot.controller.last_clk = 0
        rot.gpio.levels[CLK] = 0  # no edge, ever

        delays = await self._run(
            rot, monkeypatch, ticks=2,
            advance=RotaryVolumeController.ACTIVE_WINDOW_S + 0.5,
        )
        assert delays == [
            RotaryVolumeController.ACTIVE_POLL_S,  # the window is still open
            RotaryVolumeController.IDLE_POLL_S,
        ]

    async def test_a_press_reopens_the_fast_window(self, rot, monkeypatch):
        """Activity is the button too, not just rotation — otherwise a press
        made after a rest period is answered 16 ms late instead of 1 ms."""
        rot.controller.last_clk = 0
        rot.gpio.levels[CLK] = 0

        def press_on_the_third_tick(tick):
            if tick == 2:
                rot.gpio.levels[SW] = 0

        delays = await self._run(
            rot, monkeypatch, ticks=3,
            advance=RotaryVolumeController.ACTIVE_WINDOW_S + 0.5,
            between=press_on_the_third_tick,
        )
        assert delays == [
            RotaryVolumeController.ACTIVE_POLL_S,
            RotaryVolumeController.IDLE_POLL_S,
            RotaryVolumeController.ACTIVE_POLL_S,
        ]

    async def test_a_failing_read_does_not_kill_the_loop(self, rot, monkeypatch, caplog):
        """One bad read must not leave the knob dead until the next reboot."""
        rot.gpio.read_error = OSError("transient")

        delays = await self._run(rot, monkeypatch, ticks=2, advance=0.0)

        assert len(delays) == 2, "the loop kept going after the failing tick"
        assert delays[0] == 1, "a failing tick backs off before retrying"
        assert "Error in monitoring loop" in caplog.text
