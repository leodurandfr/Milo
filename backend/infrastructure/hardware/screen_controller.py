# backend/infrastructure/hardware/screen_controller.py
"""
Contrôleur d'écran simplifié - Utilise le script bash qui fonctionne
"""
import asyncio
import subprocess
import logging
from time import monotonic

class ScreenController:
    """Contrôleur d'écran simplifié utilisant les commandes qui fonctionnent"""
    
    def __init__(self, state_machine):
        self.state_machine = state_machine
        self.logger = logging.getLogger(__name__)
        
        # Commandes simples comme ton script
        self.SCREEN_ON_CMD = "sudo /home/oakos/RPi-USB-Brightness/64/lite/Raspi_USB_Backlight_nogui -b 5"
        self.SCREEN_OFF_CMD = "sudo /home/oakos/RPi-USB-Brightness/64/lite/Raspi_USB_Backlight_nogui -b 0"
        self.TOUCHSCREEN_DEVICE = "/dev/input/by-id/usb-WaveShare_WS170120_220211-event-if00"  # AJOUT
        
        # État simple
        self.TIMEOUT_SECONDS = 10  # 5 minutes
        self.last_activity_time = monotonic()
        self.screen_on = True
        self.running = False
        self.current_plugin_state = "inactive"
        self.touch_process = None  # AJOUT : pour gérer le processus libinput
    
    async def initialize(self) -> bool:
        """Initialise le contrôleur"""
        try:
            self.logger.info("Initializing simple screen controller")
            
            # Allumer l'écran au démarrage
            await self._turn_on_screen()
            self.last_activity_time = monotonic()
            self.running = True
            
            # Démarrer les boucles simples
            asyncio.create_task(self._monitor_plugin_state())
            asyncio.create_task(self._monitor_timeout())
            asyncio.create_task(self._monitor_touch_events())  # AJOUT : monitoring tactile
            
            self.logger.info("Simple screen controller initialized")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to initialize screen controller: {e}")
            return False
    
    async def _turn_on_screen(self):
        """Allume l'écran - Méthode simple"""
        try:
            self.logger.info(f"Turning screen ON: {self.SCREEN_ON_CMD}")
            
            # Exécution simple comme ton script
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
        """Éteint l'écran - Méthode simple"""
        try:
            self.logger.info(f"Turning screen OFF: {self.SCREEN_OFF_CMD}")
            
            # Exécution simple comme ton script  
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
        """Surveille l'état des plugins - Version simple"""
        while self.running:
            try:
                # Récupérer l'état du système
                system_state = await self.state_machine.get_current_state()
                new_plugin_state = system_state.get("plugin_state", "inactive")
                
                # Si passe à "connected", allumer l'écran et reset timer
                if (self.current_plugin_state != "connected" and 
                    new_plugin_state == "connected"):
                    self.logger.info("Plugin connected - turning screen ON")
                    await self._turn_on_screen()
                    self.last_activity_time = monotonic()
                
                self.current_plugin_state = new_plugin_state
                await asyncio.sleep(2)  # Vérifier toutes les 2 secondes
                
            except Exception as e:
                self.logger.error(f"Error monitoring plugin state: {e}")
                await asyncio.sleep(5)
    
    async def _monitor_timeout(self):
        """Surveille le timeout - Version simple comme ton script"""
        while self.running:
            try:
                current_time = monotonic()
                time_since_activity = current_time - self.last_activity_time
                
                # Conditions pour éteindre (comme ton script)
                should_turn_off = (
                    self.screen_on and
                    time_since_activity >= self.TIMEOUT_SECONDS and
                    self.current_plugin_state != "connected"
                )
                
                if should_turn_off:
                    self.logger.info("Screen timeout reached - turning OFF")
                    await self._turn_off_screen()
                
                await asyncio.sleep(10)  # Vérifier toutes les 10 secondes
                
            except Exception as e:
                self.logger.error(f"Error in timeout monitor: {e}")
                await asyncio.sleep(10)
    
    async def _monitor_touch_events(self):
        """Surveille les événements tactiles - Comme dans ton script bash"""
        self.logger.info(f"Starting touch monitoring on {self.TOUCHSCREEN_DEVICE}")
        
        while self.running:
            try:
                # Lancer libinput debug-events comme dans ton script
                self.touch_process = await asyncio.create_subprocess_exec(
                    "libinput", "debug-events", "--device", self.TOUCHSCREEN_DEVICE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                
                self.logger.info(f"Touch monitoring started on {self.TOUCHSCREEN_DEVICE}")
                
                # Lire les événements ligne par ligne
                while self.running and self.touch_process.returncode is None:
                    try:
                        line = await asyncio.wait_for(
                            self.touch_process.stdout.readline(),
                            timeout=1.0
                        )
                        
                        if line:
                            line_str = line.decode().strip()
                            # Détecter TOUCH_DOWN comme dans ton script bash
                            if "TOUCH_DOWN" in line_str:
                                self.logger.info("Touch detected!")
                                await self._turn_on_screen()
                                self.last_activity_time = monotonic()
                        else:
                            # Process terminé
                            break
                            
                    except asyncio.TimeoutError:
                        # Pas d'événement, continuer
                        continue
                    except Exception as e:
                        self.logger.error(f"Error reading touch event: {e}")
                        break
                
            except Exception as e:
                self.logger.error(f"Error in touch monitoring: {e}")
                await asyncio.sleep(5)  # Attendre avant de relancer
            
            finally:
                # Nettoyer le processus
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
        """Appelé quand un touch est détecté - Interface publique"""
        self.logger.info("Touch detected - turning screen ON")
        await self._turn_on_screen()
        self.last_activity_time = monotonic()
    
    def cleanup(self):
        """Nettoie les ressources"""
        self.logger.info("Cleaning up simple screen controller")
        self.running = False
        
        # Nettoyer le processus touch
        if self.touch_process:
            try:
                self.touch_process.terminate()
            except Exception:
                try:
                    self.touch_process.kill()
                except Exception:
                    pass
            self.touch_process = None