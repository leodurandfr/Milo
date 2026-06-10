# backend/hardware/fan.py
"""
Runtime PWM fan controller for the Raspberry Pi cooling fan.

`dtparam=cooling_fan=on` makes the firmware instantiate a `pwm-fan` driver that
is normally steered by the kernel thermal governor using the trip points baked
into config.txt — those are compiled at boot and can't be retuned without a
reboot.

This controller takes the fan over at runtime instead: it disables the thermal
governor (`thermal_zone0/mode=disabled`), puts pwm-fan into manual mode, and
drives `pwm1` itself from a user-defined temperature→speed curve stored in
settings.json. Retuning needs no reboot. On graceful shutdown (or when the user
disables custom control) it hands the fan back to the kernel governor
(`mode=enabled`) so the config.txt curve stays the safety fallback; the SoC
hard-throttle (85 °C) and critical trip (110 °C) are independent hardware
safety nets.

Write access to the root-owned sysfs nodes is granted to the milo user by a
udev rule (rootfs/etc/udev/rules.d/99-milo-fan.rules). Off-Pi (no
/sys/devices/platform/cooling_fan) the controller is a no-op (fail-open).
"""
import asyncio
import contextlib
import glob
import logging
import os
from typing import List, Optional

import aiofiles

logger = logging.getLogger(__name__)

# Platform device created by dtparam=cooling_fan=on. Stable across boots
# (unlike the dynamic hwmonN index), so the hwmon dir is resolved underneath it.
COOLING_FAN_DEVICE = "/sys/devices/platform/cooling_fan"
THERMAL_ZONE = "/sys/class/thermal/thermal_zone0"

PWM_MAX = 255  # pwm-fan duty-cycle range is 0..255

LOOP_INTERVAL = 3.0       # seconds between temperature samples
# Only rewrite PWM when the computed target moves by more than this (in % points)
# from what was last written, to avoid jitter writes around a stable temperature.
PWM_HYSTERESIS_PCT = 2

VALID_MODES = ("auto", "manual")

# Default curve mirrors the config.txt fallback paliers (55/66/79/82 °C tiers)
# expressed as percentages, so enabling custom control changes nothing audible.
DEFAULT_CURVE: List[dict] = [
    {"temp_c": 55, "percent": 0},
    {"temp_c": 66, "percent": 22},
    {"temp_c": 79, "percent": 47},
    {"temp_c": 82, "percent": 100},
]


def _read_int(path: str) -> Optional[int]:
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError) as e:
        logger.warning("Failed to read %s: %s", path, e)
        return None


def _clamp_pct(value) -> int:
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return 0


def sanitize_curve(curve) -> List[dict]:
    """Coerce a curve payload to a sorted list of valid {temp_c, percent} points.

    Falls back to DEFAULT_CURVE when nothing usable is left. Shared with the
    settings validator so the persisted shape and the runtime shape agree.
    """
    if not isinstance(curve, list):
        return [dict(p) for p in DEFAULT_CURVE]
    points = {}
    for item in curve:
        if not isinstance(item, dict):
            continue
        try:
            temp_c = max(20, min(110, int(item["temp_c"])))
            percent = _clamp_pct(item["percent"])
        except (KeyError, TypeError, ValueError):
            continue
        points[temp_c] = percent  # dedup by temperature, last wins
    if not points:
        return [dict(p) for p in DEFAULT_CURVE]
    return [{"temp_c": t, "percent": points[t]} for t in sorted(points)]


class FanController:
    """Runtime PWM fan controller driven by a temperature→speed curve."""

    def __init__(self, state_machine, settings_service):
        self.state_machine = state_machine
        self.settings_service = settings_service

        self.available: bool = False
        self._hwmon_dir: Optional[str] = None

        # Persisted config (loaded from settings on init, updated via reload_config)
        self.enabled: bool = True           # False = fan stopped (user-disabled)
        self.mode: str = "auto"             # auto | manual (applies when enabled)
        self.manual_percent: int = 50
        self.curve: List[dict] = [dict(p) for p in DEFAULT_CURVE]

        # Live telemetry (refreshed by the monitor loop / read_status)
        self._temp_c: float = 0.0
        self._rpm: int = 0
        self._pwm_percent: int = 0

        self._monitor_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    # ========================================================================
    # LIFECYCLE
    # ========================================================================

    async def initialize(self) -> bool:
        """Detect the fan, load config, take over if custom control is enabled.

        Always returns True — fan control is opt-in; a missing device (dev box)
        or an unreadable node must never crash the backend.
        """
        if not os.path.isdir(COOLING_FAN_DEVICE):
            logger.info("cooling_fan device absent — fan controller disabled (dev/non-Pi)")
            return True
        hwmons = glob.glob(f"{COOLING_FAN_DEVICE}/hwmon/hwmon*")
        if not hwmons:
            logger.warning("cooling_fan present but no hwmon node — fan controller disabled")
            return True

        self._hwmon_dir = hwmons[0]
        # The sysfs nodes are root-owned; the udev rule (99-milo-fan.rules) grants
        # the milo user write access. We need BOTH the PWM duty AND the governor
        # toggle: without the latter the kernel governor keeps fighting our writes.
        # If either isn't writable, stay available=False so the UI hides the page,
        # the kernel governor stays in charge, and we never spam permission errors.
        if not os.access(self._pwm_path, os.W_OK) or not os.access(f"{THERMAL_ZONE}/mode", os.W_OK):
            logger.warning(
                "cooling_fan present but sysfs not writable — fan control disabled "
                "(is 99-milo-fan.rules installed?)",
            )
            return True

        self.available = True

        await self._load_config_from_settings()
        await self._apply_mode()
        if self.enabled:
            self._start_monitor()

        logger.info(
            "Fan controller ready (hwmon=%s, enabled=%s, mode=%s)",
            self._hwmon_dir, self.enabled, self.mode,
        )
        return True

    async def cleanup(self) -> None:
        """Hand the fan back to the kernel governor and stop background work."""
        await self._stop_monitor()
        if self.available:
            await self._release_to_governor()
        logger.info("Fan controller cleaned up (governor restored)")

    # ========================================================================
    # CONFIGURATION
    # ========================================================================

    async def _load_config_from_settings(self) -> None:
        cfg = await self.settings_service.get_setting("fan") or {}
        self.enabled = bool(cfg.get("enabled", True))
        mode = cfg.get("mode", "auto")
        self.mode = mode if mode in VALID_MODES else "auto"
        self.manual_percent = _clamp_pct(cfg.get("manual_percent", 50))
        self.curve = sanitize_curve(cfg.get("curve"))

    async def reload_config(self, cfg: dict) -> None:
        """Apply a validated config (called by the PUT route after persisting)."""
        async with self._lock:
            self.enabled = bool(cfg.get("enabled", self.enabled))
            mode = cfg.get("mode", self.mode)
            self.mode = mode if mode in VALID_MODES else "auto"
            self.manual_percent = _clamp_pct(cfg.get("manual_percent", self.manual_percent))
            if cfg.get("curve") is not None:
                self.curve = sanitize_curve(cfg.get("curve"))

            await self._apply_mode()
            if self.enabled:
                self._start_monitor()
            else:
                await self._stop_monitor()
        await self._broadcast_status("fan_config_changed")

    async def test_speed(self, percent: int) -> None:
        """Drive the fan to a given speed momentarily (manual preview / test).

        Does not change mode or persist. In auto mode the monitor loop will
        resume curve control on its next tick (~LOOP_INTERVAL). No-op when the
        fan is user-disabled — otherwise a test would spin a "stopped" fan with
        no loop running to bring it back to 0.
        """
        if not self.available or not self.enabled:
            return
        await self._take_control()
        await self._set_pwm_percent(percent)
        await self._broadcast_status("fan_status_changed")

    def get_status(self) -> dict:
        """Return the last-known config + telemetry (no I/O — cached values)."""
        return {
            "source": "settings",
            "available": self.available,
            "enabled": self.enabled,
            "mode": self.mode,
            "manual_percent": self.manual_percent,
            "curve": self.curve,
            "temp_c": self._temp_c,
            "rpm": self._rpm,
            "pwm_percent": self._pwm_percent,
        }

    async def read_status(self) -> dict:
        """Sample the hardware once, then return the fresh status (for GET /status)."""
        if self.available:
            await self._sample()
        return self.get_status()

    async def _broadcast_status(self, event_type: str) -> None:
        await self.state_machine.broadcast_event(
            "settings", event_type, self.get_status()
        )

    # ========================================================================
    # HARDWARE CONTROL
    # ========================================================================

    @property
    def _pwm_path(self) -> str:
        return f"{self._hwmon_dir}/pwm1"

    @property
    def _pwm_enable_path(self) -> str:
        return f"{self._hwmon_dir}/pwm1_enable"

    @property
    def _rpm_path(self) -> str:
        return f"{self._hwmon_dir}/fan1_input"

    async def _apply_mode(self) -> None:
        """Reconcile hardware ownership with the current enabled/mode state."""
        if not self.available:
            return
        if not self.enabled:
            # Disabled = fan stopped (explicit user choice). Keep the governor
            # off and hold 0%; the SoC hard-throttle (85 °C) is the safety net.
            await self._take_control()
            await self._set_pwm_percent(0)
            logger.warning("Fan disabled (0%) — relying on SoC thermal throttle")
            return

        await self._take_control()
        if self.mode == "manual":
            await self._set_pwm_percent(self.manual_percent)
        # 'auto' is driven continuously by the monitor loop.

    async def _take_control(self) -> None:
        """Stop the kernel governor and switch pwm-fan to manual so our writes stick."""
        await self._write_sysfs(f"{THERMAL_ZONE}/mode", "disabled")
        await self._write_sysfs(self._pwm_enable_path, "1")

    async def _release_to_governor(self) -> None:
        """Re-enable the kernel governor so the config.txt curve resumes.

        Re-enabling `thermal_zone0/mode` is the decisive action — the governor
        then drives pwm1 via the cooling device's cur_state (the observed boot
        state had pwm1_enable=1 with the governor active), so we leave
        pwm1_enable untouched rather than guess a driver-specific 'auto' value.
        """
        await self._write_sysfs(f"{THERMAL_ZONE}/mode", "enabled")

    async def _set_pwm_percent(self, percent: int) -> None:
        percent = _clamp_pct(percent)
        pwm = round(percent / 100 * PWM_MAX)
        if await self._write_sysfs(self._pwm_path, pwm):
            self._pwm_percent = percent

    def _curve_target_percent(self, temp_c: float) -> int:
        """Linear interpolation of the curve at temp_c (flat outside the range)."""
        curve = self.curve
        if temp_c <= curve[0]["temp_c"]:
            return curve[0]["percent"]
        if temp_c >= curve[-1]["temp_c"]:
            return curve[-1]["percent"]
        for i in range(len(curve) - 1):
            lo, hi = curve[i], curve[i + 1]
            if lo["temp_c"] <= temp_c < hi["temp_c"]:
                span = hi["temp_c"] - lo["temp_c"]
                if span <= 0:
                    return lo["percent"]
                ratio = (temp_c - lo["temp_c"]) / span
                return round(lo["percent"] + ratio * (hi["percent"] - lo["percent"]))
        return curve[-1]["percent"]

    async def _sample(self) -> None:
        # Both sysfs reads in a single executor hop.
        raw_temp, rpm = await asyncio.to_thread(
            lambda: (_read_int(f"{THERMAL_ZONE}/temp"), _read_int(self._rpm_path))
        )
        if raw_temp is not None:
            self._temp_c = round(raw_temp / 1000.0, 1)
        if rpm is not None:
            self._rpm = rpm

    async def _write_sysfs(self, path: str, value) -> bool:
        try:
            async with aiofiles.open(path, "w") as f:
                await f.write(str(value))
            return True
        except OSError as e:
            logger.warning("Failed to write %s to %s: %s", value, path, e)
            return False

    # ========================================================================
    # MONITOR LOOP
    # ========================================================================

    def _start_monitor(self) -> None:
        if self._monitor_task and not self._monitor_task.done():
            return
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("Fan monitor loop started")

    async def _stop_monitor(self) -> None:
        task = self._monitor_task
        self._monitor_task = None
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def _monitor_loop(self) -> None:
        """Sample temperature/RPM, drive PWM in auto mode, broadcast telemetry on change.

        No periodic heartbeat: the fan settings page resyncs over HTTP when it
        opens, so an unchanged status needs no WS traffic (and lets the kiosk
        renderer sleep).
        """
        last_telemetry = None
        while True:
            try:
                await self._sample()

                if self.mode == "auto":
                    target = self._curve_target_percent(self._temp_c)
                    # Compare against the ACTUAL last-written duty (self._pwm_percent),
                    # not a loop-local var — otherwise a manual/disabled excursion
                    # leaves the loop's memory stale and hysteresis suppresses the
                    # corrective write when switching back to auto.
                    crossed_zero = (target == 0) != (self._pwm_percent == 0)
                    if abs(target - self._pwm_percent) >= PWM_HYSTERESIS_PCT or crossed_zero:
                        await self._set_pwm_percent(target)

                telemetry = (self._temp_c, self._rpm, self._pwm_percent)
                if telemetry != last_telemetry:
                    await self._broadcast_status("fan_status_changed")
                    last_telemetry = telemetry
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Fan monitor loop error: %s", e)
            await asyncio.sleep(LOOP_INTERVAL)
