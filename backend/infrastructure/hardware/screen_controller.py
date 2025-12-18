# backend/infrastructure/hardware/screen_controller.py
"""
Screen Controller - OPTIM Version with timeout_seconds = 0 for never
Multi-screen support: Waveshare 7" USB and Waveshare 8" DSI
"""
import asyncio
import logging
import os
from time import monotonic

class ScreenController:
    """Screen controller with timeout_seconds = 0 to disable"""

    def __init__(self, state_machine, settings_service, hardware_service):
        self.state_machine = state_machine
        self.settings_service = settings_service
        self.hardware_service = hardware_service
        self.logger = logging.getLogger(__name__)

        # Screen type detection
        self.screen_type = hardware_service.get_screen_type()
        self.logger.info(f"Screen type detected: {self.screen_type}")

        # Configuration from settings
        self.timeout_seconds = 10
        self.brightness_on = 5

        # Brightness ranges based on screen type
        if self.screen_type == "waveshare_7_usb":
            self.brightness_min = 0
            self.brightness_max = 10
        elif self.screen_type == "waveshare_8_dsi":
            self.brightness_min = 0
            self.brightness_max = 255
        else:
            # No screen or unknown type
            self.brightness_min = 0
            self.brightness_max = 10

        # Dynamic commands (generated based on screen type)
        self._update_screen_commands()

        # State
        self.last_activity_time = monotonic()
        self.boot_time = None  # Will be set during initialize()
        self.boot_grace_period = 30  # Will be calculated as max(30, timeout_seconds) during initialize()
        self.screen_on = True
        self.running = False
        self.current_plugin_state = "ready"
    
    def _map_brightness_value(self, ui_value: int) -> int:
        """
        Converts a UI value (1-10) to the screen's native range.

        Args:
            ui_value: User interface value (1-10)

        Returns:
            Native value for the screen (0-10 for 7" USB, 25-255 for 8" DSI)
        """
        if self.screen_type == "waveshare_7_usb":
            # 7" USB uses 0-10 directly
            return ui_value
        elif self.screen_type == "waveshare_8_dsi":
            # 8" DSI: map 1-10 to 25-255 (avoid 0 = completely off)
            # Formula: 25 + (ui_value - 1) * (255 - 25) / (10 - 1)
            return int(25 + (ui_value - 1) * (230 / 9))
        else:
            # Unknown type: return value as-is
            return ui_value

    def _update_screen_commands(self):
        """Updates commands with brightness from settings based on screen type"""
        native_brightness = self._map_brightness_value(self.brightness_on)

        if self.screen_type == "waveshare_7_usb":
            # Waveshare 7" USB: uses milo-brightness-7 binary
            self.screen_on_cmd = f"sudo /usr/local/bin/milo-brightness-7 -b {native_brightness}"
            self.screen_off_cmd = f"sudo /usr/local/bin/milo-brightness-7 -b 0"
        elif self.screen_type == "waveshare_8_dsi":
            # Waveshare 8" DSI: uses sysfs (direct path without wildcard to avoid shell issues)
            self.screen_on_cmd = f"echo {native_brightness} | sudo tee /sys/class/backlight/*/brightness"
            self.screen_off_cmd = f"echo 0 | sudo tee /sys/class/backlight/*/brightness"
        else:
            # No screen or unknown type: empty commands
            self.logger.warning(f"Unknown screen type '{self.screen_type}', brightness control disabled")
            self.screen_on_cmd = ""
            self.screen_off_cmd = ""
    
    async def _load_config(self):
        """Loads complete config from settings - timeout_seconds = 0 for never"""
        try:
            # FORCE reload from file by invalidating cache first
            self.settings_service._cache = None

            # Load all settings directly from file
            all_settings = await self.settings_service.load_settings()
            screen_config = all_settings.get('screen', {})

            self.timeout_seconds = screen_config.get('timeout_seconds', 10)
            self.brightness_on = screen_config.get('brightness_on', 5)

            timeout_enabled = self.timeout_seconds != 0
            self.logger.info(f"Screen config loaded: timeout={self.timeout_seconds}s ({'enabled' if timeout_enabled else 'DISABLED'}), brightness={self.brightness_on}")

            # Update commands with new brightness
            self._update_screen_commands()

        except Exception as e:
            self.logger.error(f"Error loading screen config: {e}")
            # Fallback to defaults
            self.timeout_seconds = 10
            self.brightness_on = 5
            self._update_screen_commands()
    
    async def reload_timeout_config(self) -> bool:
        """Reloads timeout/brightness config"""
        try:
            self.logger.info(f"Reloading screen config (old timeout: {self.timeout_seconds}s)")
            await self._load_config()
            self.logger.info(f"Screen config reloaded (new timeout: {self.timeout_seconds}s)")
            self.last_activity_time = monotonic()
            return True
        except Exception as e:
            self.logger.error(f"Error reloading screen config: {e}")
            return False
    
    async def initialize(self) -> bool:
        """Initializes the controller"""
        try:
            await self._load_config()

            # Calculate grace period: max between 30s and configured timeout
            # This ensures we always see at least 30s of boot, even if timeout is short
            self.boot_grace_period = max(30, self.timeout_seconds if self.timeout_seconds != 0 else 30)

            await self._screen_cmd(self.screen_on_cmd)
            self.boot_time = monotonic()  # Record boot time
            self.last_activity_time = monotonic()
            self.running = True

            self.logger.info(f"Screen controller initialized with {self.boot_grace_period}s boot grace period (timeout_seconds={self.timeout_seconds}s)")

            # Start monitoring
            asyncio.create_task(self._monitor_plugin_state())
            asyncio.create_task(self._monitor_timeout())
            # asyncio.create_task(self._monitor_touch_events())  # Disabled - detection via frontend

            return True

        except Exception as e:
            self.logger.error(f"Failed to initialize: {e}")
            return False
    
    async def _screen_cmd(self, cmd):
        """Executes a screen command"""
        # Do nothing if no screen is configured or command is empty
        if not cmd or self.screen_type == "none":
            return

        try:
            process = await asyncio.create_subprocess_shell(
                cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            await process.communicate()

            # Determine if screen is on by comparing with screen_off_cmd
            # (if command is screen_off_cmd, screen is off, otherwise it's on)
            self.screen_on = (cmd != self.screen_off_cmd)

        except Exception as e:
            self.logger.error(f"Screen command failed: {e}")
    
    async def _monitor_plugin_state(self):
        """Monitors plugin state"""
        while self.running:
            try:
                system_state = await self.state_machine.get_current_state()
                new_state = system_state.get("plugin_state", "ready")
                
                if self.current_plugin_state != "connected" and new_state == "connected":
                    await self._screen_cmd(self.screen_on_cmd)
                    self.last_activity_time = monotonic()
                elif self.current_plugin_state == "connected" and new_state == "ready":
                    self.last_activity_time = monotonic()
                
                self.current_plugin_state = new_state
                await asyncio.sleep(2)
                
            except Exception as e:
                self.logger.error(f"Plugin monitoring error: {e}")
                await asyncio.sleep(5)
    
    async def _monitor_timeout(self):
        """Monitors timeout - timeout_seconds = 0 for never"""
        while self.running:
            try:
                # timeout_seconds = 0 means never (disabled)
                if self.timeout_seconds == 0:
                    await asyncio.sleep(5)
                    continue

                # Grace period after boot: don't turn off screen during the first X seconds
                if self.boot_time is not None:
                    time_since_boot = monotonic() - self.boot_time
                    if time_since_boot < self.boot_grace_period:
                        # Still in grace period
                        remaining = self.boot_grace_period - time_since_boot
                        if int(time_since_boot) % 10 == 0:  # Log every 10s to avoid spam
                            self.logger.debug(f"Boot grace period active: {remaining:.0f}s remaining")
                        await asyncio.sleep(1)
                        continue

                # Keep timer at 0 while plugin is "connected"
                if self.current_plugin_state == "connected":
                    self.last_activity_time = monotonic()

                time_since_activity = monotonic() - self.last_activity_time

                should_turn_off = (
                    self.screen_on and
                    time_since_activity >= self.timeout_seconds and
                    self.current_plugin_state != "connected"
                )

                if should_turn_off:
                    self.logger.info(f"Screen turning OFF after {time_since_activity:.1f}s (timeout: {self.timeout_seconds}s)")
                    await self._screen_cmd(self.screen_off_cmd)

                await asyncio.sleep(1)

            except Exception as e:
                self.logger.error(f"Timeout monitoring error: {e}")
                await asyncio.sleep(10)
    
    async def on_touch_detected(self):
        """Public interface for external touch"""
        await self._screen_cmd(self.screen_on_cmd)
        self.last_activity_time = monotonic()
    
    def cleanup(self):
        """Cleans up resources"""
        self.running = False