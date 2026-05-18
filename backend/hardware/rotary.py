# backend/hardware/rotary.py
"""
KY-040 Rotary Encoder Controller for Volume - dB Volume API Version

Uses event-triggered processing: detent detection immediately triggers
volume adjustment via VolumeAccumulator for minimal latency.
"""
import contextlib
import lgpio
import asyncio
import logging
from typing import Optional
from time import monotonic

from backend.hardware.playback_dispatch import PlaybackDispatcher
from backend.hardware.volume_accumulator import VolumeAccumulator

logger = logging.getLogger(__name__)


class RotaryVolumeController:
    """KY-040 rotary encoder controller - dB volume API (-80 to 0 dB)"""

    DEBOUNCE_TIME = 0.005  # 5ms debounce (KY-040 bounce is 1-3ms)
    BUTTON_DEBOUNCE_TIME = 0.02  # 20ms debounce for pushbutton

    def __init__(self, volume_service, state_machine, clk_pin=22, dt_pin=27, sw_pin=23):
        self.volume_service = volume_service
        self.CLK = clk_pin
        self.DT = dt_pin
        self.SW = sw_pin
        self.chip_handle: Optional[int] = None
        self.last_clk = 0
        self.running = False

        # Volume accumulator (shared with BT remote)
        self._volume = VolumeAccumulator(volume_service)

        # Playback dispatch (multi-click → play/pause, next, prev)
        self._dispatcher = PlaybackDispatcher(state_machine)

        # Timing
        self._last_adjustment_time = 0
        self._last_button_state = 1  # HIGH at rest (pull-up)
        self._last_button_time = 0.0

    async def initialize(self) -> bool:
        """Initialize the rotary controller."""
        try:
            logger.info("Initializing rotary controller (CLK=%d, DT=%d, SW=%d)", self.CLK, self.DT, self.SW)
            self.chip_handle = lgpio.gpiochip_open(0)

            for pin in [self.CLK, self.DT, self.SW]:
                lgpio.gpio_claim_input(self.chip_handle, pin, lgpio.SET_PULL_UP)

            self.last_clk = lgpio.gpio_read(self.chip_handle, self.CLK)
            self.running = True

            asyncio.create_task(self._monitor_loop())

            logger.info("Rotary controller initialized successfully")
            return True

        except Exception as e:
            logger.error("Failed to initialize rotary controller: %s", e)
            await self.cleanup()
            return False

    async def _monitor_loop(self):
        """GPIO polling loop — detects edges and button presses."""
        logger.info("Starting rotary monitoring loop")

        while self.running:
            try:
                self._check_rotation()
                await self._check_button()
                await asyncio.sleep(0.001)  # 1ms polling
            except Exception as e:
                logger.error("Error in monitoring loop: %s", e)
                await asyncio.sleep(1)

    def _check_rotation(self):
        """Detect rotary edge, accumulate, and trigger processor if idle."""
        clk_state = lgpio.gpio_read(self.chip_handle, self.CLK)

        if clk_state != self.last_clk:
            current_time = monotonic()

            if current_time - self._last_adjustment_time >= self.DEBOUNCE_TIME:
                dt_state = lgpio.gpio_read(self.chip_handle, self.DT)
                step = self.volume_service.volume_config.step_rotary_db

                self._volume.accumulate(step if dt_state != clk_state else -step)

                self._last_adjustment_time = current_time

            self.last_clk = clk_state

    async def _check_button(self):
        """Detect SW button press (falling edge + debounce) and dispatch to multi-click handler."""
        sw_state = lgpio.gpio_read(self.chip_handle, self.SW)
        if sw_state == 0 and self._last_button_state == 1:
            now = monotonic()
            if now - self._last_button_time >= self.BUTTON_DEBOUNCE_TIME:
                self._last_button_time = now
                await self._dispatcher.on_click()
        self._last_button_state = sw_state

    async def cleanup(self):
        """Clean up GPIO resources."""
        logger.info("Cleaning up rotary controller")
        self.running = False

        self._dispatcher.cancel()
        await self._volume.cleanup()

        if self.chip_handle is not None:
            try:
                for pin in [self.CLK, self.DT, self.SW]:
                    with contextlib.suppress(Exception):
                        lgpio.gpio_free(self.chip_handle, pin)
                lgpio.gpiochip_close(self.chip_handle)
                logger.info("GPIO resources cleaned up")
            except Exception as e:
                logger.error("Error during GPIO cleanup: %s", e)
            finally:
                self.chip_handle = None
