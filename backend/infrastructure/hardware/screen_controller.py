# backend/infrastructure/hardware/screen_controller.py
"""
Contrôleur d'écran - Version OPTIM avec timeout_seconds = 0 pour jamais
Support multi-écrans : Waveshare 7" USB et Waveshare 8" DSI
"""
import asyncio
import logging
import os
from time import monotonic

class ScreenController:
    """Contrôleur d'écran avec timeout_seconds = 0 pour désactiver"""

    def __init__(self, state_machine, settings_service, hardware_service):
        self.state_machine = state_machine
        self.settings_service = settings_service
        self.hardware_service = hardware_service
        self.logger = logging.getLogger(__name__)

        # Détection du type d'écran
        self.screen_type = hardware_service.get_screen_type()
        self.logger.info(f"Screen type detected: {self.screen_type}")

        # Configuration depuis settings
        self.timeout_seconds = 10
        self.brightness_on = 5

        # Plages de luminosité selon le type d'écran
        if self.screen_type == "waveshare_7_usb":
            self.brightness_min = 0
            self.brightness_max = 10
        elif self.screen_type == "waveshare_8_dsi":
            self.brightness_min = 0
            self.brightness_max = 255
        else:
            # Aucun écran ou type inconnu
            self.brightness_min = 0
            self.brightness_max = 10

        # Commandes dynamiques (générées selon le type d'écran)
        self._update_screen_commands()

        # État
        self.last_activity_time = monotonic()
        self.boot_time = None  # Sera défini lors de initialize()
        self.boot_grace_period = 30  # Sera calculé comme max(30, timeout_seconds) lors de initialize()
        self.screen_on = True
        self.running = False
        self.current_plugin_state = "inactive"
    
    def _map_brightness_value(self, ui_value: int) -> int:
        """
        Convertit une valeur UI (1-10) vers la plage native de l'écran.

        Args:
            ui_value: Valeur de l'interface utilisateur (1-10)

        Returns:
            Valeur native pour l'écran (0-10 pour 7" USB, 25-255 pour 8" DSI)
        """
        if self.screen_type == "waveshare_7_usb":
            # 7" USB utilise directement 0-10
            return ui_value
        elif self.screen_type == "waveshare_8_dsi":
            # 8" DSI : mapper 1-10 vers 25-255 (éviter 0 = complètement éteint)
            # Formule : 25 + (ui_value - 1) * (255 - 25) / (10 - 1)
            return int(25 + (ui_value - 1) * (230 / 9))
        else:
            # Type inconnu : retourner la valeur telle quelle
            return ui_value

    def _update_screen_commands(self):
        """Met à jour les commandes avec la luminosité depuis settings selon le type d'écran"""
        native_brightness = self._map_brightness_value(self.brightness_on)

        if self.screen_type == "waveshare_7_usb":
            # Waveshare 7" USB : utilise le binaire milo-brightness-7
            self.screen_on_cmd = f"sudo /usr/local/bin/milo-brightness-7 -b {native_brightness}"
            self.screen_off_cmd = f"sudo /usr/local/bin/milo-brightness-7 -b 0"
        elif self.screen_type == "waveshare_8_dsi":
            # Waveshare 8" DSI : utilise sysfs (chemin direct sans wildcard pour éviter problèmes shell)
            self.screen_on_cmd = f"echo {native_brightness} | sudo tee /sys/class/backlight/*/brightness"
            self.screen_off_cmd = f"echo 0 | sudo tee /sys/class/backlight/*/brightness"
        else:
            # Aucun écran ou type inconnu : commandes vides
            self.logger.warning(f"Unknown screen type '{self.screen_type}', brightness control disabled")
            self.screen_on_cmd = ""
            self.screen_off_cmd = ""
    
    async def _load_config(self):
        """Charge la config complète depuis settings - timeout_seconds = 0 pour jamais"""
        try:
            # FORCER le reload depuis le fichier en invalidant le cache d'abord
            self.settings_service._cache = None

            # Charger directement toutes les settings depuis le fichier
            all_settings = await self.settings_service.load_settings()
            screen_config = all_settings.get('screen', {})

            self.timeout_seconds = screen_config.get('timeout_seconds', 10)
            self.brightness_on = screen_config.get('brightness_on', 5)

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
            self.logger.info(f"Reloading screen config (old timeout: {self.timeout_seconds}s)")
            await self._load_config()
            self.logger.info(f"Screen config reloaded (new timeout: {self.timeout_seconds}s)")
            self.last_activity_time = monotonic()
            return True
        except Exception as e:
            self.logger.error(f"Error reloading screen config: {e}")
            return False
    
    async def initialize(self) -> bool:
        """Initialise le contrôleur"""
        try:
            await self._load_config()

            # Calculer la période de grâce : max entre 30s et le timeout configuré
            # Cela garantit qu'on voit toujours au moins 30s de boot, même si timeout est court
            self.boot_grace_period = max(30, self.timeout_seconds if self.timeout_seconds != 0 else 30)

            await self._screen_cmd(self.screen_on_cmd)
            self.boot_time = monotonic()  # Enregistrer le temps de démarrage
            self.last_activity_time = monotonic()
            self.running = True

            self.logger.info(f"Screen controller initialized with {self.boot_grace_period}s boot grace period (timeout_seconds={self.timeout_seconds}s)")

            # Démarrer monitoring
            asyncio.create_task(self._monitor_plugin_state())
            asyncio.create_task(self._monitor_timeout())
            # asyncio.create_task(self._monitor_touch_events())  # Désactivé - détection via frontend

            return True

        except Exception as e:
            self.logger.error(f"Failed to initialize: {e}")
            return False
    
    async def _screen_cmd(self, cmd):
        """Exécute une commande écran"""
        # Ne rien faire si aucun écran n'est configuré ou commande vide
        if not cmd or self.screen_type == "none":
            return

        try:
            process = await asyncio.create_subprocess_shell(
                cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            await process.communicate()

            # Déterminer si l'écran est allumé en comparant avec screen_off_cmd
            # (si la commande est screen_off_cmd, l'écran est éteint, sinon il est allumé)
            self.screen_on = (cmd != self.screen_off_cmd)

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
                # timeout_seconds = 0 signifie jamais (désactivé)
                if self.timeout_seconds == 0:
                    await asyncio.sleep(5)
                    continue

                # Période de grâce après boot : ne pas éteindre l'écran pendant les X premières secondes
                if self.boot_time is not None:
                    time_since_boot = monotonic() - self.boot_time
                    if time_since_boot < self.boot_grace_period:
                        # Encore dans la période de grâce
                        remaining = self.boot_grace_period - time_since_boot
                        if int(time_since_boot) % 10 == 0:  # Log toutes les 10s pour éviter le spam
                            self.logger.debug(f"Boot grace period active: {remaining:.0f}s remaining")
                        await asyncio.sleep(1)
                        continue

                # Garder le timer à 0 tant que le plugin est "connected"
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
        """Interface publique pour touch externe"""
        await self._screen_cmd(self.screen_on_cmd)
        self.last_activity_time = monotonic()
    
    def cleanup(self):
        """Nettoie les ressources"""
        self.running = False