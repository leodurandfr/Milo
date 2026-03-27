# backend/hardware/rotary.py
"""
KY-040 Rotary Encoder Controller for Volume - dB Volume API Version

Uses event-triggered processing: detent detection immediately triggers
volume adjustment via an accumulator + on-demand processor task,
mirroring the BT remote pattern for minimal latency.
"""
import lgpio
import asyncio
import logging
from typing import Optional
from time import monotonic

from backend.hardware.playback_dispatch import PlaybackDispatcher


class RotaryVolumeController:
    """KY-040 rotary encoder controller - dB volume API (-80 to 0 dB)"""

    DEBOUNCE_TIME = 0.005  # 5ms debounce (KY-040 bounce is 1-3ms)
    BATCH_INTERVAL = 0.02  # 20ms between volume batches during sustained rotation

    def __init__(self, volume_service, state_machine, clk_pin=22, dt_pin=27, sw_pin=23):
        self.volume_service = volume_service
        self.CLK = clk_pin
        self.DT = dt_pin
        self.SW = sw_pin
        self.chip_handle: Optional[int] = None
        self.last_clk = 0
        self.running = False
        self.logger = logging.getLogger(__name__)

        # Accumulator + on-demand processor (same pattern as BT remote)
        self._rotation_accumulator = 0
        self._processor_running = False
        self._processor_task: Optional[asyncio.Task] = None

        # Playback dispatch (multi-click → play/pause, next, prev)
        self._dispatcher = PlaybackDispatcher(state_machine)

        # Timing
        self._last_adjustment_time = 0
        self._last_button_press = 0

    async def initialize(self) -> bool:
        """Initialize the rotary controller."""
        try:
            self.logger.info(f"Initializing rotary controller (CLK={self.CLK}, DT={self.DT}, SW={self.SW})")
            self.chip_handle = lgpio.gpiochip_open(0)

            for pin in [self.CLK, self.DT, self.SW]:
                lgpio.gpio_claim_input(self.chip_handle, pin, lgpio.SET_PULL_UP)

            self.last_clk = lgpio.gpio_read(self.chip_handle, self.CLK)
            self.running = True

            asyncio.create_task(self._monitor_loop())

            self.logger.info("Rotary controller initialized successfully")
            return True

        except Exception as e:
            self.logger.error(f"Failed to initialize rotary controller: {e}")
            self.cleanup()
            return False

    async def _monitor_loop(self):
        """GPIO polling loop — detects edges and button presses."""
        self.logger.info("Starting rotary monitoring loop")

        while self.running:
            try:
                self._check_rotation()
                await self._check_button()
                await asyncio.sleep(0.001)  # 1ms polling
            except Exception as e:
                self.logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(1)

    def _check_rotation(self):
        """Detect rotary edge, accumulate, and trigger processor if idle."""
        clk_state = lgpio.gpio_read(self.chip_handle, self.CLK)

        if clk_state != self.last_clk:
            current_time = monotonic()

            if current_time - self._last_adjustment_time >= self.DEBOUNCE_TIME:
                dt_state = lgpio.gpio_read(self.chip_handle, self.DT)

                if dt_state != clk_state:
                    self._rotation_accumulator += 1
                else:
                    self._rotation_accumulator -= 1

                self._last_adjustment_time = current_time

                # Trigger processing immediately if no processor is running
                if not self._processor_running:
                    self._processor_task = asyncio.create_task(self._process_volume())

            self.last_clk = clk_state

    async def _process_volume(self):
        """Drain accumulated rotations into volume adjustments."""
        self._processor_running = True
        try:
            while self._rotation_accumulator != 0:
                volume_step = self.volume_service.volume_config.step_rotary_db
                volume_delta = self._rotation_accumulator * volume_step
                self._rotation_accumulator = 0

                try:
                    await self.volume_service.adjust_volume_db(volume_delta)
                except Exception as e:
                    self.logger.error(f"Error adjusting volume: {e}")

                await asyncio.sleep(self.BATCH_INTERVAL)
        finally:
            # Re-check: if an edge arrived between the while-check and here,
            # spawn a new processor to avoid silently dropping it.
            if self._rotation_accumulator != 0:
                self._processor_running = False
                self._processor_task = asyncio.create_task(self._process_volume())
                return
            self._processor_running = False

    async def _check_button(self):
        """Detect SW button press and dispatch to multi-click handler."""
        if lgpio.gpio_read(self.chip_handle, self.SW) == 0:
            current_time = monotonic()

            if current_time - self._last_button_press >= 0.05:
                self._last_button_press = current_time
                await self._dispatcher.on_click()

    def cleanup(self):
        """Clean up GPIO resources."""
        self.logger.info("Cleaning up rotary controller")
        self.running = False

        self._dispatcher.cancel()

        if self._processor_task and not self._processor_task.done():
            self._processor_task.cancel()
        self._processor_task = None
        self._rotation_accumulator = 0
        self._processor_running = False

        if self.chip_handle is not None:
            try:
                for pin in [self.CLK, self.DT, self.SW]:
                    try:
                        lgpio.gpio_free(self.chip_handle, pin)
                    except Exception:
                        pass
                lgpio.gpiochip_close(self.chip_handle)
                self.logger.info("GPIO resources cleaned up")
            except Exception as e:
                self.logger.error(f"Error during GPIO cleanup: {e}")
            finally:
                self.chip_handle = None
