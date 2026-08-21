# backend/tests/test_fan_controller.py
"""
Unit tests for the runtime PWM fan controller.

Hardware writes are mocked and the two sysfs roots are redirected at a tmp tree
— the machine this suite runs on IS a Pi whose `thermal_zone0/mode` is
writable, so a test that reached the real node would disable the kernel thermal
governor on a live appliance.

What is exercised: the pure proportional control law, the config validation
surface (Pydantic + settings sanitization), the monitor loop's write decision
(hysteresis around a stable target, rails always written), the governor
handover, and the four boundary methods the rest of the appliance calls into —
`initialize`, `read_status`, `test_speed`, `cleanup`.
"""
import asyncio
import contextlib
import logging

import pytest
from unittest.mock import AsyncMock, MagicMock
from pydantic import ValidationError

from backend.api.models import FanConfigRequest
from backend.core.settings import SettingsService
from backend.hardware import fan
from backend.hardware.fan import (
    SAFETY_OVERRIDE_TEMP_C,
    TARGET_FULL_ABOVE_C,
    TARGET_OFF_BELOW_C,
    TARGET_TEMP_DEFAULT_C,
    TARGET_TEMP_MAX_C,
    TARGET_TEMP_MIN_C,
    FanController,
    clamp_target_temp,
)


def make_controller() -> FanController:
    state_machine = MagicMock()
    state_machine.broadcast = AsyncMock()
    return FanController(state_machine, MagicMock())


VALID_PAYLOAD = {
    "enabled": True,
    "manual_percent": 50,
    "target_temp_c": 70,
    "curve": [{"temp_c": 55, "percent": 0}, {"temp_c": 82, "percent": 100}],
}


class TestFanConfigRequest:
    def test_accepts_target_mode(self):
        cfg = FanConfigRequest(mode="target", **VALID_PAYLOAD)
        assert cfg.mode == "target"
        assert cfg.target_temp_c == 70

    @pytest.mark.parametrize("temp", [TARGET_TEMP_MIN_C - 1, TARGET_TEMP_MAX_C + 1])
    def test_rejects_out_of_range_target_temp(self, temp):
        with pytest.raises(ValidationError):
            FanConfigRequest(mode="target", **{**VALID_PAYLOAD, "target_temp_c": temp})


class TestClampTargetTemp:
    def test_clamps_to_range(self):
        assert clamp_target_temp(TARGET_TEMP_MIN_C - 15) == TARGET_TEMP_MIN_C
        assert clamp_target_temp(TARGET_TEMP_MAX_C + 19) == TARGET_TEMP_MAX_C
        assert clamp_target_temp(70) == 70

    def test_falls_back_to_default_on_garbage(self):
        assert clamp_target_temp(None) == TARGET_TEMP_DEFAULT_C
        assert clamp_target_temp("hot") == TARGET_TEMP_DEFAULT_C


class TestTargetModePercent:
    # target 70 → band bottom (0 %) at 67 °C, band top (100 %) at 79 °C.
    @pytest.fixture
    def controller(self):
        c = make_controller()
        c.target_temp_c = 70
        c._pwm_percent = 50
        return c

    def test_off_at_and_below_band_bottom(self, controller):
        assert controller._target_mode_percent(70 - TARGET_OFF_BELOW_C) == 0
        assert controller._target_mode_percent(60.0) == 0

    def test_full_at_and_above_band_top(self, controller):
        assert controller._target_mode_percent(70 + TARGET_FULL_ABOVE_C) == 100
        assert controller._target_mode_percent(80.0) == 100  # still under the safety override

    def test_proportional_at_setpoint(self, controller):
        # setpoint sits 3 °C into a 12 °C band → 25 %
        assert controller._target_mode_percent(70.0) == 25

    def test_small_overshoot_yields_small_speed(self, controller):
        # 1 °C over target must be a gentle speed, not a ramp to 100 %
        assert controller._target_mode_percent(71.0) == 33

    def test_monotonic_across_band(self, controller):
        duties = [controller._target_mode_percent(t) for t in range(67, 80)]
        assert duties == sorted(duties)
        assert duties[0] == 0 and duties[-1] == 100

    def test_stateless_ignores_current_duty(self, controller):
        # No integrator: the duty depends only on temperature, never on the
        # last-written value — so it can never wind up to 100 % near the target.
        results = []
        for pwm in (0, 25, 50, 99, 100):
            controller._pwm_percent = pwm
            results.append(controller._target_mode_percent(70.0))
        assert results == [25, 25, 25, 25, 25]

    def test_safety_override_jumps_to_100(self, controller):
        assert controller._target_mode_percent(SAFETY_OVERRIDE_TEMP_C) == 100


class TestMonitorLoopTargetMode:
    async def run_one_tick(self, controller):
        task = asyncio.create_task(controller._monitor_loop())
        await asyncio.sleep(0.01)  # loop body runs, then parks in sleep(LOOP_INTERVAL)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_safety_rail_100_bypasses_hysteresis(self):
        # From 99 %, the safety override proposes the 100 % rail (delta 1) — the
        # 2-point hysteresis must not swallow it while the SoC is hot.
        c = make_controller()
        c.mode = "target"
        c.target_temp_c = 70
        c._pwm_percent = 99
        c._sample = AsyncMock()
        c._temp_c = SAFETY_OVERRIDE_TEMP_C
        c._set_pwm_percent = AsyncMock()
        await self.run_one_tick(c)
        c._set_pwm_percent.assert_awaited_once_with(100)

    @pytest.mark.asyncio
    async def test_off_rail_bypasses_hysteresis(self):
        # Below the band the target is the 0 % rail (delta 1 from 1 %) — a clean
        # stop must be written, not held at a barely-spinning 1 %.
        c = make_controller()
        c.mode = "target"
        c.target_temp_c = 70
        c._pwm_percent = 1
        c._sample = AsyncMock()
        c._temp_c = 60.0
        c._set_pwm_percent = AsyncMock()
        await self.run_one_tick(c)
        c._set_pwm_percent.assert_awaited_once_with(0)

    @pytest.mark.asyncio
    async def test_no_write_when_holding(self):
        # target 70 → 25 % at 70 °C; already there, within hysteresis → no write.
        c = make_controller()
        c.mode = "target"
        c.target_temp_c = 70
        c._pwm_percent = 25
        c._sample = AsyncMock()
        c._temp_c = 70.0
        c._set_pwm_percent = AsyncMock()
        await self.run_one_tick(c)
        c._set_pwm_percent.assert_not_awaited()


class TestDisableRacesTheMonitorLoop:
    """reload_config must stop the loop before it touches the hardware.

    The tick takes no lock — `_lock` appears in reload_config and
    _load_config_from_settings and nowhere else — so a tick already awaiting
    when the fan is disabled used to resume and re-assert the curve setpoint
    over the 0 % _apply_mode had just written. reload_config then killed the
    loop, so nothing corrected it: the fan spins on, the UI says disabled.
    """

    DISABLE = {
        "enabled": False,
        "mode": "auto",
        "manual_percent": 50,
        "target_temp_c": 70,
        "curve": [{"temp_c": 55, "percent": 0}, {"temp_c": 82, "percent": 100}],
    }

    @pytest.mark.asyncio
    async def test_an_in_flight_tick_cannot_restart_a_disabled_fan(self):
        c = make_controller()
        c.available = True
        c.enabled = True
        c.mode = "auto"
        c.curve = [{"temp_c": 55, "percent": 0}, {"temp_c": 82, "percent": 100}]
        c._temp_c = 82.0  # top of the curve → the tick would ask for 100 %
        c._take_control = AsyncMock()

        gate = asyncio.Event()

        async def gated_sample():
            await gate.wait()

        writes = []

        async def record_write(percent):
            writes.append(percent)
            if percent == 0:
                # The disable write has landed. Release the parked tick here:
                # this is the interleaving that leaves the fan running, because
                # its write arrives after the 0 % and before the loop is killed.
                gate.set()
                await asyncio.sleep(0.01)

        c._sample = gated_sample
        c._set_pwm_percent = record_write

        c._start_monitor()
        await asyncio.sleep(0)  # the tick reaches _sample and parks

        await c.reload_config(self.DISABLE)

        assert writes == [0], f"a duty was re-asserted on a disabled fan: {writes}"
        assert c._monitor_task is None

    @pytest.mark.asyncio
    async def test_a_tick_writes_nothing_while_disabled(self):
        """Second half of the fix: the tick reads self.enabled itself."""
        c = make_controller()
        c.enabled = False
        c.mode = "auto"
        c._pwm_percent = 0  # the curve asks for 100 here, so a write is due
        c._temp_c = 82.0
        c._sample = AsyncMock()
        c._set_pwm_percent = AsyncMock()

        await TestMonitorLoopTargetMode().run_one_tick(c)

        c._set_pwm_percent.assert_not_awaited()


class TestGovernorHandover:
    """Taking control from the kernel governor is the precondition for everything.

    With `thermal_zone0/mode` still `enabled`, the governor keeps driving pwm1
    and every duty we write is overwritten — the configured curve simply does
    not apply. `_write_sysfs` only warns, so the failure that makes the whole
    controller decorative was the quietest thing in the file.
    """

    async def test_a_governor_that_will_not_stop_is_reported_at_error(self, caplog):
        c = make_controller()
        c._write_sysfs = AsyncMock(return_value=False)

        with caplog.at_level(logging.ERROR):
            await c._take_control()

        assert "governor still active" in caplog.text
        assert "not switched to manual" in caplog.text

    async def test_a_clean_handover_says_nothing(self, caplog):
        c = make_controller()
        c._write_sysfs = AsyncMock(return_value=True)

        with caplog.at_level(logging.ERROR):
            await c._take_control()

        assert caplog.text == ""

    async def test_a_governor_that_will_not_resume_is_reported_at_error(self, caplog):
        """The fan is then stuck on whatever duty we last wrote, with nothing
        left watching the temperature."""
        c = make_controller()
        c._write_sysfs = AsyncMock(return_value=False)

        with caplog.at_level(logging.ERROR):
            await c._release_to_governor()

        assert "governor not restored" in caplog.text


class TestSettingsSanitization:
    @pytest.fixture
    def service(self):
        return SettingsService()

    def test_clamps_target_temp(self, service):
        result = service._validate_and_merge({'fan': {'mode': 'target', 'target_temp_c': 95}})
        assert result['fan']['mode'] == 'target'
        assert result['fan']['target_temp_c'] == TARGET_TEMP_MAX_C

    def test_falls_back_to_default_on_garbage(self, service):
        result = service._validate_and_merge({'fan': {'target_temp_c': 'hot'}})
        assert result['fan']['target_temp_c'] == TARGET_TEMP_DEFAULT_C


class TestConfigLoadIsAProjection:
    """_load_config_from_settings adopts the settings layer's resolved values.

    It re-validates nothing: SettingsService already clamps every fan key, and a
    second set of bounds in the controller is free to disagree with the first.
    These drive the REAL validator into the REAL controller, so a bound moved on
    either side breaks here rather than on a unit.
    """

    @pytest.fixture
    def service(self):
        svc = SettingsService()
        # Out of range on every axis: if the controller re-clamped with its own
        # literals, or fell back to one, its values would leave the validator's.
        svc._cache = svc._validate_and_merge({'fan': {
            'enabled': 'truthy-string',
            'mode': 'target',
            'manual_percent': 300,
            'target_temp_c': 999,
            'curve': [{'temp_c': 70, 'percent': 10}, {'temp_c': 60, 'percent': 500}],
        }})
        return svc

    @pytest.mark.asyncio
    async def test_adopts_exactly_what_the_validator_resolved(self, service):
        controller = FanController(MagicMock(), service)
        await controller._load_config_from_settings()

        resolved = service._cache['fan']
        assert controller.enabled == resolved['enabled']
        assert controller.mode == resolved['mode']
        assert controller.manual_percent == resolved['manual_percent']
        assert controller.target_temp_c == resolved['target_temp_c']
        assert controller.curve == resolved['curve']

    @pytest.mark.asyncio
    async def test_curve_is_copied_not_aliased(self, service):
        """get_setting hands out the live cache object.

        Without a copy the running controller and the settings cache share one
        list of dicts, so a settings write would silently re-point the thermal
        curve the monitor loop is reading.
        """
        controller = FanController(MagicMock(), service)
        await controller._load_config_from_settings()

        controller.curve[0]['percent'] = 99
        controller.curve.append({'temp_c': 90, 'percent': 100})

        assert service._cache['fan']['curve'][0]['percent'] != 99
        assert len(service._cache['fan']['curve']) != len(controller.curve)


class TestInitializeDetection:
    """What `initialize()` decides at boot: whether Milō owns the fan at all.

    `available` gates the settings page, the monitor loop and every sysfs
    write. Wrong in one direction, the kernel governor keeps overwriting our
    duty and the configured curve silently does not apply; wrong in the other,
    a box with no fan spams permission errors. It must return True either way —
    fan control is opt-in and a dev host has no cooling_fan node.

    Both sysfs roots are redirected at a tmp tree on purpose: the machine this
    suite runs on IS a Pi whose `thermal_zone0/mode` is writable, and a test
    that reached the real one would disable the kernel thermal governor on a
    live appliance.
    """

    @pytest.fixture
    def sysfs(self, tmp_path, monkeypatch):
        device = tmp_path / "cooling_fan"
        hwmon = device / "hwmon" / "hwmon3"
        hwmon.mkdir(parents=True)
        zone = tmp_path / "thermal_zone0"
        zone.mkdir()
        monkeypatch.setattr(fan, "COOLING_FAN_DEVICE", str(device))
        monkeypatch.setattr(fan, "THERMAL_ZONE", str(zone))
        return hwmon, zone

    def _controller(self, cfg):
        controller = make_controller()
        controller.settings_service.get_setting = AsyncMock(return_value=cfg)
        controller._write_sysfs = AsyncMock(return_value=True)
        return controller

    CONFIG = {
        "enabled": True,
        "mode": "manual",
        "manual_percent": 40,
        "target_temp_c": 70,
        "curve": [{"temp_c": 55, "percent": 0}, {"temp_c": 82, "percent": 100}],
    }

    async def test_a_writable_fan_is_detected_configured_and_taken_over(self, sysfs):
        """The whole boot chain in one: hwmon resolved under the stable platform
        device, config adopted from settings, hardware taken from the governor,
        the mode's duty asserted and the monitor loop running."""
        hwmon, zone = sysfs
        (hwmon / "pwm1").write_text("0")
        (zone / "mode").write_text("enabled")
        controller = self._controller(self.CONFIG)

        assert await controller.initialize() is True

        assert controller.available is True
        assert controller.mode == "manual"
        assert controller.manual_percent == 40
        assert controller._pwm_percent == 40
        assert controller._monitor_task is not None
        await controller._stop_monitor()

    async def test_a_fan_whose_governor_toggle_is_not_writable_is_left_to_the_kernel(
        self, sysfs
    ):
        """Owning pwm1 without owning `thermal_zone0/mode` is worse than owning
        nothing: the governor keeps driving the fan and overwrites every duty we
        write, so the curve the user configured quietly does nothing. Both nodes
        or neither — and the udev rule (99-milo-fan.rules) is what grants them.
        """
        hwmon, _zone = sysfs
        (hwmon / "pwm1").write_text("0")  # writable — but no thermal_zone0/mode
        controller = self._controller(self.CONFIG)

        assert await controller.initialize() is True

        assert controller.available is False
        assert controller._monitor_task is None
        controller._write_sysfs.assert_not_awaited()


class TestReadStatus:
    """`GET /api/settings/fan/status` samples the hardware before answering.

    What breaks when this fails: the fan page reports the temperature and RPM
    of whenever the monitor loop last ticked — up to LOOP_INTERVAL stale, and
    arbitrarily stale when the fan is disabled and no loop runs at all.

    The sysfs files ARE the outside world here, so they are the only thing
    stood in for: `_sample` and `_read_int` run for real.
    """

    async def test_the_answer_carries_what_the_sample_just_read(self, tmp_path, monkeypatch):
        zone = tmp_path / "thermal_zone0"
        zone.mkdir()
        (zone / "temp").write_text("61400")  # sysfs reports millidegrees
        hwmon = tmp_path / "hwmon3"
        hwmon.mkdir()
        (hwmon / "fan1_input").write_text("2900")
        monkeypatch.setattr(fan, "THERMAL_ZONE", str(zone))

        controller = make_controller()
        controller.available = True
        controller._hwmon_dir = str(hwmon)

        status = await controller.read_status()

        assert status["temp_c"] == 61.4
        assert status["rpm"] == 2900

    async def test_a_box_with_no_fan_is_not_sampled(self):
        """Off-Pi the sysfs nodes do not exist; reading them would log a warning
        pair on every poll of a page that has nothing to show anyway."""
        controller = make_controller()
        controller.available = False
        controller._sample = AsyncMock()

        status = await controller.read_status()

        controller._sample.assert_not_awaited()
        assert status["available"] is False


class TestTestSpeed:
    """The speed preview (`POST /api/settings/fan/test`).

    What breaks when this fails: the preview does nothing — or spins a fan the
    user switched off, with no monitor loop running to bring it back to 0 %.
    """

    def _controller(self, monkeypatch, *, enabled):
        monkeypatch.setattr(fan, "THERMAL_ZONE", "/sys/stand-in/thermal_zone0")
        controller = make_controller()
        controller.available = True
        controller.enabled = enabled
        controller._hwmon_dir = "/sys/stand-in/hwmon3"
        controller._write_sysfs = AsyncMock(return_value=True)
        return controller

    async def test_control_is_taken_before_the_duty_is_written(self, monkeypatch):
        """Order is the point. A duty written while the governor still drives
        pwm1 is overwritten before it is ever heard."""
        controller = self._controller(monkeypatch, enabled=True)

        await controller.test_speed(80)

        paths = [call.args[0] for call in controller._write_sysfs.await_args_list]
        assert paths.index(f"{controller._hwmon_dir}/pwm1") > paths.index(
            f"{fan.THERMAL_ZONE}/mode"
        )
        assert paths.index(f"{controller._hwmon_dir}/pwm1") > paths.index(
            f"{controller._hwmon_dir}/pwm1_enable"
        )
        assert controller._pwm_percent == 80
        controller.state_machine.broadcast.assert_awaited_once()

    async def test_a_fan_the_user_switched_off_is_not_spun(self, monkeypatch):
        """Nothing would bring it back down: `_apply_mode` holds a disabled fan
        at 0 % and then stops the loop, so a preview duty written here stays on
        the fan until the next config write."""
        controller = self._controller(monkeypatch, enabled=False)

        await controller.test_speed(80)

        controller._write_sysfs.assert_not_awaited()
        controller.state_machine.broadcast.assert_not_awaited()


class TestCleanupHandsTheFanBack:
    """Shutdown returns the fan to the kernel governor.

    What breaks when this fails: the backend exits with `thermal_zone0`
    disabled and pwm-fan still in manual on whatever duty we last wrote — so
    nothing watches the SoC temperature, and the config.txt curve that is meant
    to be the safety fallback never resumes. Only the SoC hard-throttle is
    left. `architecture/test_service_wiring.py` checks that main.py *calls*
    this cleanup; nothing checked what it does.
    """

    async def test_the_governor_is_re_enabled_and_the_loop_is_gone(self, monkeypatch):
        monkeypatch.setattr(fan, "THERMAL_ZONE", "/sys/stand-in/thermal_zone0")
        controller = make_controller()
        controller.available = True
        controller.enabled = False  # so a tick in flight writes no duty of its own
        controller._hwmon_dir = "/sys/stand-in/hwmon3"
        controller._write_sysfs = AsyncMock(return_value=True)
        controller._sample = AsyncMock()
        controller._start_monitor()

        await controller.cleanup()

        assert controller._monitor_task is None
        writes = [call.args for call in controller._write_sysfs.await_args_list]
        assert (f"{fan.THERMAL_ZONE}/mode", "enabled") in writes

    async def test_a_box_with_no_fan_writes_nothing(self):
        """`available` False means we never took the fan over, so there is no
        ownership to hand back — and off-Pi the node does not exist."""
        controller = make_controller()
        controller.available = False
        controller._write_sysfs = AsyncMock(return_value=True)

        await controller.cleanup()

        controller._write_sysfs.assert_not_awaited()
