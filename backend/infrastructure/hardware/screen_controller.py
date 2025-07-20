# backend/infrastructure/hardware/screen_controller.py
"""
Contrôleur d'écran avec logique corrigée et logs détaillés du timer
"""
import asyncio
import subprocess
import logging
from time import monotonic

class ScreenController:
    """Contrôleur d'écran avec gestion correcte du timer et logs détaillés"""
    
    def __init__(self, state_machine):
        self.state_machine = state_machine
        self.logger = logging.getLogger(__name__)
        
        # Commandes écran
        self.SCREEN_ON_CMD = "sudo /home/oakos/RPi-USB-Brightness/64/lite/Raspi_USB_Backlight_nogui -b 5"
        self.SCREEN_OFF_CMD = "sudo /home/oakos/RPi-USB-Brightness/64/lite/Raspi_USB_Backlight_nogui -b 0"
        self.TOUCHSCREEN_DEVICE = "/dev/input/by-id/usb-WaveShare_WS170120_220211-event-if00"
        
        # État du timer
        self.TIMEOUT_SECONDS = 10
        self.last_activity_time = monotonic()
        self.screen_on = True
        self.running = False
        
        # État système pour détecter les changements
        self.current_plugin_state = "inactive"
        self.current_is_playing = False
        self.current_active_source = "none"
        
        # Processus touch
        self.touch_process = None
    
    async def initialize(self) -> bool:
        """Initialise le contrôleur"""
        try:
            self.logger.info("Initializing screen controller with timer logs")
            
            # Allumer l'écran au démarrage
            await self._turn_on_screen()
            self._reset_timer("initialization")
            
            self.running = True
            
            # Démarrer les boucles de monitoring
            asyncio.create_task(self._monitor_system_state())
            asyncio.create_task(self._monitor_timeout())
            asyncio.create_task(self._monitor_touch_events())
            
            self.logger.info("Screen controller initialized with timer logging")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to initialize screen controller: {e}")
            return False
    
    def _reset_timer(self, reason: str):
        """Remet le timer à zéro avec log détaillé"""
        self.last_activity_time = monotonic()
        self.logger.info(f"⏰ TIMER RESET to 0s - Reason: {reason} - plugin_state: {self.current_plugin_state} - is_playing: {self.current_is_playing} - source: {self.current_active_source}")
    
    async def _turn_on_screen(self):
        """Allume l'écran"""
        if self.screen_on:
            return
            
        try:
            self.logger.info(f"🔆 Turning screen ON: {self.SCREEN_ON_CMD}")
            
            process = await asyncio.create_subprocess_shell(
                self.SCREEN_ON_CMD,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode == 0:
                self.screen_on = True
                self.logger.info(f"✅ Screen ON success")
            else:
                self.logger.error(f"❌ Screen ON failed: {stderr.decode().strip()}")
                
        except Exception as e:
            self.logger.error(f"Error turning on screen: {e}")
    
    async def _turn_off_screen(self):
        """Éteint l'écran"""
        if not self.screen_on:
            return
            
        try:
            self.logger.info(f"🔅 Turning screen OFF: {self.SCREEN_OFF_CMD}")
            
            process = await asyncio.create_subprocess_shell(
                self.SCREEN_OFF_CMD,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await process.communicate()
            
            if process.returncode == 0:
                self.screen_on = False
                self.logger.info(f"✅ Screen OFF success")
            else:
                self.logger.error(f"❌ Screen OFF failed: {stderr.decode().strip()}")
                
        except Exception as e:
            self.logger.error(f"Error turning off screen: {e}")
    
    async def _monitor_system_state(self):
        """Surveille l'état du système et gère l'écran + timer"""
        while self.running:
            try:
                # Récupérer l'état actuel
                system_state = await self.state_machine.get_current_state()
                new_plugin_state = system_state.get("plugin_state", "inactive")
                new_active_source = system_state.get("active_source", "none")
                
                # Pour librespot, récupérer is_playing
                new_is_playing = False
                if new_active_source == "librespot":
                    metadata = system_state.get("metadata", {})
                    new_is_playing = metadata.get("is_playing", False)
                
                # Détecter les changements d'état qui nécessitent de reset le timer
                state_changed = (
                    self.current_plugin_state != new_plugin_state or
                    self.current_is_playing != new_is_playing or
                    self.current_active_source != new_active_source
                )
                
                if state_changed:
                    change_details = []
                    if self.current_plugin_state != new_plugin_state:
                        change_details.append(f"plugin_state: {self.current_plugin_state} → {new_plugin_state}")
                    if self.current_is_playing != new_is_playing:
                        change_details.append(f"is_playing: {self.current_is_playing} → {new_is_playing}")
                    if self.current_active_source != new_active_source:
                        change_details.append(f"active_source: {self.current_active_source} → {new_active_source}")
                    
                    self.logger.info(f"🔄 State change detected: {', '.join(change_details)}")
                    
                    # Mettre à jour l'état local
                    self.current_plugin_state = new_plugin_state
                    self.current_is_playing = new_is_playing
                    self.current_active_source = new_active_source
                    
                    # Gérer l'écran selon la nouvelle logique
                    await self._handle_screen_logic(state_changed=True)
                
                await asyncio.sleep(2)  # Vérifier toutes les 2 secondes
                
            except Exception as e:
                self.logger.error(f"Error monitoring system state: {e}")
                await asyncio.sleep(5)
    
    async def _handle_screen_logic(self, state_changed: bool = False):
        """Gère la logique d'allumage/extinction de l'écran"""
        should_keep_screen_on = (
            self.current_plugin_state == "connected" and (
                self.current_active_source in ["bluetooth", "roc"] or  # Bluetooth/ROC toujours ON si connecté
                (self.current_active_source == "librespot" and self.current_is_playing)  # Librespot ON si en lecture
            )
        )
        
        if should_keep_screen_on:
            if not self.screen_on:
                await self._turn_on_screen()
            if state_changed:
                self._reset_timer(f"screen_on_state: {self.current_active_source}")
        else:
            # L'écran doit s'éteindre après timeout
            if state_changed:
                self._reset_timer(f"screen_off_state: plugin={self.current_plugin_state}, playing={self.current_is_playing}")
    
    async def _monitor_timeout(self):
        """Surveille le timeout avec logs détaillés"""
        while self.running:
            try:
                current_time = monotonic()
                time_elapsed = current_time - self.last_activity_time
                
                # Conditions pour éteindre l'écran
                should_turn_off = (
                    self.screen_on and
                    time_elapsed >= self.TIMEOUT_SECONDS and
                    not self._should_keep_screen_on()
                )
                
                # Log détaillé du timer toutes les 5 secondes si on approche du timeout
                if time_elapsed >= 5 and self.screen_on:
                    time_remaining = max(0, self.TIMEOUT_SECONDS - time_elapsed)
                    self.logger.info(f"⏱️  Timer: {time_elapsed:.1f}s elapsed, {time_remaining:.1f}s remaining - Should turn off: {should_turn_off}")
                
                if should_turn_off:
                    self.logger.info(f"⏰ TIMEOUT REACHED ({time_elapsed:.1f}s) - Turning screen OFF")
                    await self._turn_off_screen()
                
                await asyncio.sleep(5)  # Log toutes les 5 secondes
                
            except Exception as e:
                self.logger.error(f"Error in timeout monitor: {e}")
                await asyncio.sleep(10)
    
    def _should_keep_screen_on(self) -> bool:
        """Détermine si l'écran doit rester allumé"""
        return (
            self.current_plugin_state == "connected" and (
                self.current_active_source in ["bluetooth", "roc"] or
                (self.current_active_source == "librespot" and self.current_is_playing)
            )
        )
    
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
                                self.logger.info("👆 Touch detected!")
                                await self._turn_on_screen()
                                self._reset_timer("touch_detected")
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
        """Interface publique pour la détection de touch"""
        self.logger.info("👆 Touch detected via API")
        await self._turn_on_screen()
        self._reset_timer("touch_api")
    
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