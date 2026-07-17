# backend/tests/test_fan_controller.py
"""
Unit tests for the fan target mode (temperature setpoint controller).

Hardware writes are mocked — these exercise the pure control law, the config
validation surface (Pydantic + settings sanitization) and the monitor loop's
hysteresis-bypass write decision in target mode.
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
    TARGET_DEADBAND_C,
    TARGET_SNAP_OFF_DELTA_C,
    TARGET_STEP_MAX_PCT,
    TARGET_STEP_MIN_PCT,
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
    @pytest.fixture
    def controller(self):
        c = make_controller()
        c.target_temp_c = 70
        c._pwm_percent = 50
        return c

    def test_minimal_step_just_outside_deadband(self, controller):
        assert controller._target_mode_percent(70 + TARGET_DEADBAND_C + 0.1) == 50 + TARGET_STEP_MIN_PCT
        assert controller._target_mode_percent(70 - TARGET_DEADBAND_C - 0.1) == 50 - TARGET_STEP_MIN_PCT

    def test_step_scales_with_error(self, controller):
        small = controller._target_mode_percent(72.0) - 50  # 0.5 °C beyond deadband
        large = controller._target_mode_percent(76.0) - 50  # 4.5 °C beyond deadband
        assert TARGET_STEP_MIN_PCT <= small < large <= TARGET_STEP_MAX_PCT

    def test_step_caps_at_max(self, controller):
        # 9 °C over target but under the safety override: capped slew, not 100
        assert controller._target_mode_percent(79.0) == 50 + TARGET_STEP_MAX_PCT

    def test_scaled_descent_just_above_snap_boundary(self, controller):
        result = controller._target_mode_percent(70 - TARGET_SNAP_OFF_DELTA_C + 0.1)
        assert 0 < result < 50 - TARGET_STEP_MIN_PCT

    def test_snaps_to_zero_well_below_target(self, controller):
        assert controller._target_mode_percent(70 - TARGET_SNAP_OFF_DELTA_C) == 0
        assert controller._target_mode_percent(60.0) == 0

    def test_holds_inside_deadband(self, controller):
        assert controller._target_mode_percent(70.0) == 50
        assert controller._target_mode_percent(70 + TARGET_DEADBAND_C) == 50

    def test_safety_override_jumps_to_100(self, controller):
        assert controller._target_mode_percent(SAFETY_OVERRIDE_TEMP_C) == 100

    def test_clamps_at_bounds(self, controller):
        controller._pwm_percent = 99
        assert controller._target_mode_percent(75.0) == 100
        controller._pwm_percent = 1
        assert controller._target_mode_percent(68.0) == 0  # scaled step > 1, clamped at 0


class TestMonitorLoopTargetMode:
    async def run_one_tick(self, controller):
        task = asyncio.create_task(controller._monitor_loop())
        await asyncio.sleep(0.01)  # loop body runs, then parks in sleep(LOOP_INTERVAL)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_clamped_step_bypasses_write_hysteresis(self):
        # From 99% while hot, the clamped step proposes 100 (delta 1) — the
        # 2-point hysteresis must not swallow it in target mode.
        c = make_controller()
        c.mode = "target"
        c.target_temp_c = 70
        c._pwm_percent = 99
        c._sample = AsyncMock()
        c._temp_c = 75.0
        c._set_pwm_percent = AsyncMock()
        await self.run_one_tick(c)
        c._set_pwm_percent.assert_awaited_once_with(100)

    @pytest.mark.asyncio
    async def test_no_write_when_holding(self):
        c = make_controller()
        c.mode = "target"
        c.target_temp_c = 70
        c._pwm_percent = 40
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
