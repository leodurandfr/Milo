# backend/tests/test_fan_controller.py
"""
Unit tests for the fan target mode (temperature setpoint controller).

Hardware writes are mocked — these exercise the pure proportional control law,
the config validation surface (Pydantic + settings sanitization) and the monitor
loop's write decision (hysteresis around a stable target, rails always written).
"""
import asyncio
import contextlib

import pytest
from unittest.mock import AsyncMock, MagicMock
from pydantic import ValidationError

from backend.api.models import FanConfigRequest
from backend.core.settings import SettingsService
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
