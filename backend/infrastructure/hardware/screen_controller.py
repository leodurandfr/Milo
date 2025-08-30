# backend/infrastructure/hardware/screen_controller.py
"""
Contrôleur d'écran avec SettingsService et rechargement à chaud du timeout
"""
import asyncio
import subprocess
import logging
import os
from time import monotonic

class ScreenController:
    """Contrôleur d'écran avec configuration depuis SettingsService"""
    
    def __init__(self, state_machine, settings_service):
        self.state_machine = state_machine
        self.settings_service = settings_service
        self.logger = logging.getLogger(__name__)
        
        # Commandes écran
        backlight_binary = os.path.expanduser("~/RPi-USB-Brightness/64/lite/Raspi_USB_Backlight_nogui -b")
        self.SCREEN_ON_CMD = f"sudo {backlight_binary} 5"
        self.SCREEN_OFF_CMD = f"sudo {backlight_binary} 0"
        self.TOUCHSCREEN_DEVICE = "/dev/input/by-id/usb-WaveShare_WS170120_220211-event-if00"
        
        # État
        self.timeout_seconds = 10  # Valeur par défaut, sera mise à jour depuis settings
        self.last_activity_time = monotonic()
        self.screen_on = True
        self.running = False
        self.current_plugin_state = "inactive"
        self.touch_process = None
    
    async def initialize(self) -> bool:
        """Initialise le contrôleur avec lecture config depuis SettingsService"""
        try:
            self.logger.info("Initializing screen controller with SettingsService")
            
            # Charger la configuration timeout depuis SettingsService
            await self._load_timeout_config()
            
            # Allumer l'écran au démarrage
            await self._turn_on_screen()
            self.last_activity_time = monotonic()
            self.running = True
            
            # Démarrer les boucles de monitoring
            asyncio.create_task(self._monitor_plugin_state())
            asyncio.create_task(self._monitor_timeout())
            asyncio.create_task(self._monitor_touch_events())
            
            self.logger.info(f"Screen controller initialized with timeout: {self.timeout_seconds}s")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to initialize screen controller: {e}")
            return False
    
    async def _load_timeout_config(self):
        """Charge la configuration timeout depuis SettingsService avec logs détaillés"""
        self.logger.info(f"[LOAD_CONFIG] SettingsService available: {self.settings_service is not None}")
        
        if self.settings_service:
            try:
                screen_timeout = self.settings_service.get_setting('screen.timeout_seconds')
                self.logger.info(f"[LOAD_CONFIG] Raw value from settings: {screen_timeout}")
                
                if screen_timeout is not None:
                    old_timeout = self.timeout_seconds
                    self.timeout_seconds = max(3, min(3600, int(screen_timeout)))
                    self.logger.info(f"[LOAD_CONFIG] Timeout changed: {old_timeout}s → {self.timeout_seconds}s")
                else:
                    self.logger.warning("[LOAD_CONFIG] Screen timeout not found in settings, using default")
            except Exception as e:
                self.logger.error(f"[LOAD_CONFIG] Error loading screen timeout settings: {e}")
        else:
            self.logger.error("[LOAD_CONFIG] SettingsService is None - injection failed")
    
    async def reload_timeout_config(self) -> bool:
        """Recharge la configuration timeout à chaud avec logs détaillés"""
        try:
            old_timeout = self.timeout_seconds
            
            self.logger.info(f"[RELOAD] Current timeout: {old_timeout}s")
            self.logger.info(f"[RELOAD] SettingsService available: {self.settings_service is not None}")
            
            await self._load_timeout_config()
            
            if old_timeout != self.timeout_seconds:
                self.logger.info(f"[RELOAD] Screen timeout updated: {old_timeout}s → {self.timeout_seconds}s")
            else:
                self.logger.warning(f"[RELOAD] Screen timeout unchanged: {self.timeout_seconds}s")
            
            return True
        except Exception as e:
            self.logger.error(f"[RELOAD] Error reloading screen timeout config: {e}")
            return False
    
    async def _turn_on_screen(self):
        """Allume l'écran"""
        try:
            self.logger.info(f"Turning screen ON: {self.SCREEN_ON_CMD}")
            
            process = await asyncio.create_subprocess_shell(
                self.SCREEN_ON_CMD,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode == 0:
                self.screen_on = True
                self.logger.info(f"Screen ON success: {stdout.decode().strip()}")
            else:
                self.logger.error(f"Screen ON failed: {stderr.decode().strip()}")
                
        except Exception as e:
            self.logger.error(f"Error turning on screen: {e}")
    
    async def _turn_off_screen(self):
        """Éteint l'écran"""
        try:
            self.logger.info(f"Turning screen OFF: {self.SCREEN_OFF_CMD}")
            
            process = await asyncio.create_subprocess_shell(
                self.SCREEN_OFF_CMD,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode == 0:
                self.screen_on = False
                self.logger.info(f"Screen OFF success: {stdout.decode().strip()}")
            else:
                self.logger.error(f"Screen OFF failed: {stderr.decode().strip()}")
                
        except Exception as e:
            self.logger.error(f"Error turning off screen: {e}")
    
    async def _monitor_plugin_state(self):
        """Surveille l'état des plugins"""
        while self.running:
            try:
                # Récupérer l'état du système
                system_state = await self.state_machine.get_current_state()
                new_plugin_state = system_state.get("plugin_state", "inactive")
                
                # Si plugin passe en "connected"
                if self.current_plugin_state != "connected" and new_plugin_state == "connected":
                    self.logger.info("Plugin connected - turning screen ON")
                    await self._turn_on_screen()
                    self.last_activity_time = monotonic()

                # Si plugin passe de "connected" à "ready"
                elif self.current_plugin_state == "connected" and new_plugin_state == "ready":
                    self.logger.info("Plugin became ready - resetting activity timer")
                    self.last_activity_time = monotonic()
                
                self.current_plugin_state = new_plugin_state
                await asyncio.sleep(2)
                
            except Exception as e:
                self.logger.error(f"Error monitoring plugin state: {e}")
                await asyncio.sleep(5)
    
    async def _monitor_timeout(self):
        """Surveille le timeout avec valeur configurable"""
        while self.running:
            try:
                current_time = monotonic()
                time_since_activity = current_time - self.last_activity_time

                self.logger.debug(
                    f"[TimeoutMonitor] screen_on={self.screen_on}, plugin_state={self.current_plugin_state}, "
                    f"time_since_activity={time_since_activity:.1f}s / timeout={self.timeout_seconds}s"
                )
                
                # Conditions pour éteindre (utilise self.timeout_seconds configurable)
                should_turn_off = (
                    self.screen_on and
                    time_since_activity >= self.timeout_seconds and
                    self.current_plugin_state != "connected"
                )
                
                if should_turn_off:
                    self.logger.info(f"Screen timeout reached ({self.timeout_seconds}s) - turning OFF")
                    await self._turn_off_screen()
                
                await asyncio.sleep(1)
                
            except Exception as e:
                self.logger.error(f"Error in timeout monitor: {e}")
                await asyncio.sleep(10)
    
    async def _monitor_touch_events(self):
        """Surveille les événements tactiles"""
        self.logger.info(f"Starting touch monitoring on {self.TOUCHSCREEN_DEVICE}")
        
        while self.running:
            try:
                self.touch_process = await asyncio.create_subprocess_exec(
                    "libinput", "debug-events", "--device", self.TOUCHSCREEN_DEVICE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                
                self.logger.info(f"Touch monitoring started on {self.TOUCHSCREEN_DEVICE}")
                
                while self.running and self.touch_process.returncode is None:
                    try:
                        line = await asyncio.wait_for(
                            self.touch_process.stdout.readline(),
                            timeout=1.0
                        )
                        
                        if line:
                            line_str = line.decode().strip()
                            if "TOUCH_DOWN" in line_str:
                                self.logger.info("Touch detected!")
                                await self._turn_on_screen()
                                self.last_activity_time = monotonic()
                        else:
                            break
                            
                    except asyncio.TimeoutError:
                        continue
                    except Exception as e:
                        self.logger.error(f"Error reading touch event: {e}")
                        break
                
            except Exception as e:
                self.logger.error(f"Error in touch monitoring: {e}")
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
                    self.logger.warning("Touch monitoring crashed, restarting in 5 seconds...")
                    await asyncio.sleep(5)
    
    async def on_touch_detected(self):
        """Interface publique pour les événements touch externes"""
        self.logger.info("Touch detected - turning screen ON")
        await self._turn_on_screen()
        self.last_activity_time = monotonic()
    
    def cleanup(self):
        """Nettoie les ressources"""
        self.logger.info("Cleaning up screen controller")
        self.running = False
        
        if self.touch_process:
            try:
                self.touch_process.terminate()
            except Exception:
                try:
                    self.touch_process.kill()
                except Exception:
                    pass
            self.touch_process = None