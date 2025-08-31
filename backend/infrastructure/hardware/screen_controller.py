# backend/infrastructure/hardware/screen_controller.py
"""
Contrôleur d'écran - Version OPTIM simplifiée
"""
import asyncio
import logging
import os
from time import monotonic

class ScreenController:
    """Contrôleur d'écran simplifié"""
    
    def __init__(self, state_machine, settings_service):
        self.state_machine = state_machine
        self.settings_service = settings_service
        self.logger = logging.getLogger(__name__)
        
        # Commandes
        backlight_binary = os.path.expanduser("~/RPi-USB-Brightness/64/lite/Raspi_USB_Backlight_nogui -b")
        self.screen_on_cmd = f"sudo {backlight_binary} 5"
        self.screen_off_cmd = f"sudo {backlight_binary} 0"
        self.touchscreen_device = "/dev/input/by-id/usb-WaveShare_WS170120_220211-event-if00"
        
        # État
        self.timeout_seconds = 10
        self.last_activity_time = monotonic()
        self.screen_on = True
        self.running = False
        self.current_plugin_state = "inactive"
        self.touch_process = None
    
    async def initialize(self) -> bool:
        """Initialise le contrôleur"""
        try:
            self._load_config()
            await self._screen_cmd(self.screen_on_cmd)
            self.last_activity_time = monotonic()
            self.running = True
            
            # Démarrer monitoring
            asyncio.create_task(self._monitor_plugin_state())
            asyncio.create_task(self._monitor_timeout())
            asyncio.create_task(self._monitor_touch_events())
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to initialize: {e}")
            return False
    
    def _load_config(self):
        """Charge la config timeout"""
        if self.settings_service:
            timeout = self.settings_service.get_setting('screen.timeout_seconds')
            if timeout is not None:
                self.timeout_seconds = max(3, min(3600, int(timeout)))
    
    async def reload_timeout_config(self) -> bool:
        """Recharge la config timeout"""
        try:
            old_timeout = self.timeout_seconds
            self._load_config()
            
            if old_timeout != self.timeout_seconds:
                self.logger.info(f"Timeout updated: {old_timeout}s → {self.timeout_seconds}s")
            
            return True
        except Exception as e:
            self.logger.error(f"Error reloading config: {e}")
            return False
    
    async def _screen_cmd(self, cmd):
        """Exécute une commande écran"""
        try:
            process = await asyncio.create_subprocess_shell(
                cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            await process.communicate()
            
            if "5" in cmd:
                self.screen_on = True
            elif "0" in cmd:
                self.screen_on = False
                
        except Exception as e:
            self.logger.error(f"Screen command failed: {e}")
    
    async def _monitor_plugin_state(self):
        """Surveille l'état des plugins"""
        while self.running:
            try:
                system_state = await self.state_machine.get_current_state()
                new_state = system_state.get("plugin_state", "inactive")
                
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
        """Surveille le timeout"""
        while self.running:
            try:
                time_since_activity = monotonic() - self.last_activity_time
                
                should_turn_off = (
                    self.screen_on and
                    time_since_activity >= self.timeout_seconds and
                    self.current_plugin_state != "connected"
                )
                
                if should_turn_off:
                    await self._screen_cmd(self.screen_off_cmd)
                
                await asyncio.sleep(1)
                
            except Exception as e:
                self.logger.error(f"Timeout monitoring error: {e}")
                await asyncio.sleep(10)
    
    async def _monitor_touch_events(self):
        """Surveille les événements tactiles"""
        while self.running:
            try:
                self.touch_process = await asyncio.create_subprocess_exec(
                    "libinput", "debug-events", "--device", self.touchscreen_device,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                
                while self.running and self.touch_process.returncode is None:
                    try:
                        line = await asyncio.wait_for(self.touch_process.stdout.readline(), timeout=1.0)
                        
                        if line and "TOUCH_DOWN" in line.decode():
                            await self._screen_cmd(self.screen_on_cmd)
                            self.last_activity_time = monotonic()
                        elif not line:
                            break
                            
                    except asyncio.TimeoutError:
                        continue
                    except Exception:
                        break
                
            except Exception as e:
                self.logger.error(f"Touch monitoring error: {e}")
                await asyncio.sleep(5)
            
            finally:
                if self.touch_process:
                    try:
                        self.touch_process.terminate()
                        await asyncio.wait_for(self.touch_process.wait(), timeout=2.0)
                    except Exception:
                        try:
                            self.touch_process.kill()
                            await self.touch_process.wait()
                        except Exception:
                            pass
                    self.touch_process = None
                
                if self.running:
                    await asyncio.sleep(5)
    
    async def on_touch_detected(self):
        """Interface publique pour touch externe"""
        await self._screen_cmd(self.screen_on_cmd)
        self.last_activity_time = monotonic()
    
    def cleanup(self):
        """Nettoie les ressources"""
        self.running = False
        if self.touch_process:
            try:
                self.touch_process.terminate()
            except Exception:
                pass