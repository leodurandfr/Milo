# backend/infrastructure/hardware/rotary_volume_controller.py
"""
KY-040 Rotary Encoder Controller for Volume - dB Volume API Version
"""
import lgpio
import asyncio
import logging
from typing import Optional, Callable, Awaitable
from time import monotonic

class RotaryVolumeController:
    """KY-040 rotary encoder controller - dB volume API (-80 to 0 dB)"""
    
    def __init__(self, volume_service, clk_pin=22, dt_pin=27, sw_pin=23):
        self.volume_service = volume_service
        self.CLK = clk_pin
        self.DT = dt_pin
        self.SW = sw_pin
        self.chip_handle: Optional[int] = None
        self.last_clk = 0
        self.running = False
        self.logger = logging.getLogger(__name__)
        
        # Rotary configuration for dB volume
        self.DEBOUNCE_TIME = 0.05  # 50ms debounce
        self.rotation_accumulator = 0
        self.is_processing = False
        self.PROCESS_INTERVAL = 0.05  # 50ms between processing
        self.MIN_UPDATE_INTERVAL = 0.05  # 50ms minimum between updates
        
        # Timing
        self._last_adjustment_time = 0
        self._last_volume_update = 0
        self._last_button_press = 0
    
    async def initialize(self) -> bool:
        """Initializes the rotary controller"""
        try:
            self.logger.info(f"Initializing rotary controller with dB volume API (CLK={self.CLK}, DT={self.DT}, SW={self.SW})")
            self.chip_handle = lgpio.gpiochip_open(0)
            
            # Pin configuration
            for pin in [self.CLK, self.DT, self.SW]:
                lgpio.gpio_claim_input(self.chip_handle, pin, lgpio.SET_PULL_UP)
            
            self.last_clk = lgpio.gpio_read(self.chip_handle, self.CLK)
            self.running = True
            
            # Start monitoring loops
            asyncio.create_task(self._monitor_loop())
            asyncio.create_task(self._process_rotations_loop())
            
            self.logger.info("Rotary controller initialized successfully with dB volume API")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to initialize rotary controller: {e}")
            self.cleanup()
            return False
    
    async def _monitor_loop(self):
        """Main monitoring loop"""
        self.logger.info("Starting rotary monitoring loop")
        
        while self.running:
            try:
                await self._check_rotation()
                await self._check_button()
                await asyncio.sleep(0.001)  # 1ms polling
                
            except Exception as e:
                self.logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(1)
    
    async def _process_rotations_loop(self):
        """Loop for processing accumulated rotations"""
        last_process_time = monotonic()
        
        while self.running:
            try:
                current_time = monotonic()
                
                # Conditions for processing rotations
                should_process = (
                    current_time - last_process_time >= self.PROCESS_INTERVAL and
                    self.rotation_accumulator != 0 and
                    not self.is_processing and
                    current_time - self._last_volume_update >= self.MIN_UPDATE_INTERVAL
                )
                
                if should_process:
                    self.is_processing = True
                    
                    # Get dynamic step from config service (in dB)
                    volume_step = self.volume_service.config.config.step_rotary_db

                    # Calculate volume change in dB
                    volume_delta = self.rotation_accumulator * volume_step
                    self.rotation_accumulator = 0
                    last_process_time = current_time

                    # Apply change via volume service
                    try:
                        result = await self.volume_service.adjust_volume_db(volume_delta)
                        self._last_volume_update = current_time
                        self.logger.debug(f"Applied volume delta: {volume_delta} dB (via rotary, step={volume_step})")
                    except Exception as e:
                        self.logger.error(f"Error adjusting volume: {e}")
                    
                    self.is_processing = False
                
                await asyncio.sleep(0.01)  # 10ms loop
                
            except Exception as e:
                self.logger.error(f"Error in process rotations loop: {e}")
                self.is_processing = False
                await asyncio.sleep(0.1)
    
    async def _check_rotation(self):
        """Detects and accumulates rotations"""
        clk_state = lgpio.gpio_read(self.chip_handle, self.CLK)
        
        if clk_state != self.last_clk:
            current_time = monotonic()
            
            if current_time - self._last_adjustment_time >= self.DEBOUNCE_TIME:
                dt_state = lgpio.gpio_read(self.chip_handle, self.DT)
                
                if dt_state != clk_state:
                    # Clockwise rotation (volume +)
                    self.rotation_accumulator += 1
                    self.logger.debug(f"Rotation clockwise → (+1), accumulator={self.rotation_accumulator}")
                else:
                    # Counter-clockwise rotation (volume -)
                    self.rotation_accumulator -= 1
                    self.logger.debug(f"Rotation counter-clockwise ← (-1), accumulator={self.rotation_accumulator}")
                
                self._last_adjustment_time = current_time
            
            self.last_clk = clk_state
    
    async def _check_button(self):
        """Detects SW button press"""
        if lgpio.gpio_read(self.chip_handle, self.SW) == 0:
            current_time = monotonic()
            
            if current_time - self._last_button_press >= self.DEBOUNCE_TIME:
                self.logger.debug("Button pressed - could implement mute/unmute")
                # Free action - could be mute/unmute via VolumeService
                self._last_button_press = current_time
                await asyncio.sleep(0.2)  # Avoid bouncing
    
    def cleanup(self):
        """Cleans up GPIO resources"""
        self.logger.info("Cleaning up rotary controller")
        self.running = False
        
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