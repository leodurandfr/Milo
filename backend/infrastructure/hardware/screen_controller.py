# backend/infrastructure/hardware/screen_controller.py
"""
Contrôleur d'écran - Version OPTIM avec timeout_seconds = 0 pour jamais
"""
import asyncio
import logging
import os
from time import monotonic

class ScreenController:
    """Contrôleur d'écran avec timeout_seconds = 0 pour désactiver"""
    
    def __init__(self, state_machine, settings_service):
        self.state_machine = state_machine
        self.settings_service = settings_service
        self.logger = logging.getLogger(__name__)
        
        # Configuration depuis settings
        self.timeout_seconds = 10
        self.brightness_on = 5
        
        # Commandes dynamiques
        self._update_screen_commands()
        self.touchscreen_device = "/dev/input/by-id/usb-WaveShare_WS170120_220211-event-if00"
        
        # État
        self.last_activity_time = monotonic()
        self.screen_on = True
        self.running = False
        self.current_plugin_state = "inactive"
        self.touch_process = None
    
    def _update_screen_commands(self):
        """Met à jour les commandes avec la luminosité depuis settings"""
        backlight_binary = os.path.expanduser("~/RPi-USB-Brightness/64/lite/Raspi_USB_Backlight_nogui -b")
        self.screen_on_cmd = f"sudo {backlight_binary} {self.brightness_on}"
        self.screen_off_cmd = f"sudo {backlight_binary} 0"
    
    async def _load_config(self):
        """Charge la config complète depuis settings - timeout_seconds = 0 pour jamais"""
        try:
            # Utiliser l'API async pour garantir le reload depuis le fichier
            screen_config = await self.settings_service.get_setting('screen') or {}
            self.timeout_seconds = screen_config.get('timeout_seconds', 10)
            self.brightness_on = screen_config.get('brightness_on', 5)

            # MODIFIÉ : timeout_seconds = 0 signifie jamais (désactivé)
            timeout_enabled = self.timeout_seconds != 0

            self.logger.info(f"Screen config loaded: timeout={self.timeout_seconds}s ({'enabled' if timeout_enabled else 'DISABLED'}), brightness={self.brightness_on}")

            # Mettre à jour les commandes avec la nouvelle luminosité
            self._update_screen_commands()

        except Exception as e:
            self.logger.error(f"Error loading screen config: {e}")
            # Fallback sur defaults
            self.timeout_seconds = 10
            self.brightness_on = 5
            self._update_screen_commands()
    
    async def reload_timeout_config(self) -> bool:
        """Recharge la config timeout/brightness"""
        try:
            await self._load_config()
            self.last_activity_time = monotonic()
            return True
        except Exception as e:
            self.logger.error(f"Error reloading screen config: {e}")
            return False
    
    async def initialize(self) -> bool:
        """Initialise le contrôleur"""
        try:
            await self._load_config()
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
    
    async def _screen_cmd(self, cmd):
        """Exécute une commande écran"""
        try:
            process = await asyncio.create_subprocess_shell(
                cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            await process.communicate()
            
            self.screen_on = str(self.brightness_on) in cmd
                
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
        """Surveille le timeout - timeout_seconds = 0 pour jamais"""
        while self.running:
            try:
                # MODIFIÉ : timeout_seconds = 0 signifie jamais (désactivé)
                if self.timeout_seconds == 0:
                    await asyncio.sleep(5)
                    continue

                # NOUVEAU : Garder le timer à 0 tant que le plugin est "connected"
                if self.current_plugin_state == "connected":
                    self.last_activity_time = monotonic()

                time_since_activity = monotonic() - self.last_activity_time
                time_until_off = max(0, self.timeout_seconds - time_since_activity)

                # DEBUG : Broadcaster l'état pour le debug (toutes les secondes)
                if hasattr(self.state_machine, 'websocket_handler') and self.state_machine.websocket_handler:
                    try:
                        await self.state_machine.websocket_handler.handle_event({
                            "category": "screen",
                            "type": "debug_update",
                            "source": "screen",
                            "data": {
                                "time_since_activity": round(time_since_activity, 1),
                                "timeout_seconds": self.timeout_seconds,
                                "screen_on": self.screen_on,
                                "current_plugin_state": self.current_plugin_state,
                                "will_turn_off_in": round(time_until_off, 1)
                            },
                            "timestamp": monotonic()
                        })
                    except Exception as broadcast_error:
                        self.logger.debug(f"Debug broadcast error: {broadcast_error}")

                should_turn_off = (
                    self.screen_on and
                    time_since_activity >= self.timeout_seconds and
                    self.current_plugin_state != "connected"
                )

                if should_turn_off:
                    self.logger.info(f"🌙 Screen turning OFF - time_since_activity: {time_since_activity:.1f}s, plugin_state: {self.current_plugin_state}")
                    await self._screen_cmd(self.screen_off_cmd)

                await asyncio.sleep(1)

            except Exception as e:
                self.logger.error(f"Timeout monitoring error: {e}")
                await asyncio.sleep(10)
    
    async def _monitor_touch_events(self):
        """Surveille les événements tactiles avec luminosité configurée"""
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