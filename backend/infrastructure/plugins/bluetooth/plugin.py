"""
Plugin Bluetooth optimisé avec approche systemd pour oakOS
"""
import asyncio
import logging
import subprocess
from typing import Dict, Any, List

from backend.application.event_bus import EventBus
from backend.infrastructure.plugins.base import UnifiedAudioPlugin
from backend.domain.audio_state import PluginState


class BluetoothPlugin(UnifiedAudioPlugin):
    """Plugin Bluetooth pour la réception audio via A2DP"""
    
    def __init__(self, event_bus: EventBus, config: Dict[str, Any]):
        super().__init__(event_bus, "bluetooth")
        self.config = config
        self.current_device = None
        self._initialized = False
        self.playback_process = None
    
    async def initialize(self) -> bool:
        """Initialisation simple"""
        self.logger.info("Initialisation du plugin Bluetooth")
        self._initialized = True
        return True
    
    async def start(self) -> bool:
        """Démarre bluetooth et configure l'adaptateur"""
        try:
            self.logger.info("Démarrage du plugin Bluetooth")
            
            # 1. S'assurer que bluealsa est démarré
            bluealsa_status = subprocess.run(
                ["systemctl", "is-active", "bluealsa"],
                capture_output=True, text=True
            ).stdout.strip()
            
            if bluealsa_status != "active":
                subprocess.run(["sudo", "systemctl", "restart", "bluealsa"], check=False)
                self.logger.info("Service bluealsa redémarré")
            
            # 2. Démarrer bluetooth
            subprocess.run(["sudo", "systemctl", "start", "bluetooth"], check=True)
            await asyncio.sleep(1)
            
            # 3. Configurer l'adaptateur bluetooth spécifiquement pour A2DP Sink
            subprocess.run(["sudo", "hciconfig", "hci0", "class", "0x240404"], check=False)
            subprocess.run(["sudo", "hciconfig", "hci0", "name", "oakOS"], check=False)
            subprocess.run(["sudo", "bluetoothctl", "discoverable", "on"], check=False)
            subprocess.run(["sudo", "bluetoothctl", "pairable", "on"], check=False)
            subprocess.run(["sudo", "bluetoothctl", "agent", "on"], check=False)
            subprocess.run(["sudo", "bluetoothctl", "default-agent"], check=False)
            
            # 4. Vérifier les appareils déjà connectés
            await asyncio.sleep(2)
            await self._check_connected_devices()
            
            # 5. Notifier l'état prêt
            await self.notify_state_change(PluginState.READY)
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur démarrage Bluetooth: {e}")
            await self.notify_state_change(PluginState.ERROR, {"error": str(e)})
            return False
    
    async def _check_connected_devices(self) -> None:
        """Vérifie si un périphérique est déjà connecté"""
        try:
            result = subprocess.run(
                ["bluetoothctl", "devices", "Connected"], 
                capture_output=True, text=True
            )
            
            for line in result.stdout.strip().split('\n'):
                if line:
                    parts = line.split(' ', 2)
                    if len(parts) >= 2:
                        address = parts[1]
                        name = parts[2] if len(parts) > 2 else "Appareil inconnu"
                        
                        # Vérifier si le périphérique est disponible dans BlueALSA
                        check = subprocess.run(
                            ["bluealsa-aplay", "-L"], 
                            capture_output=True, text=True
                        )
                        
                        if address.lower() in check.stdout.lower():
                            # Enregistrer les infos du périphérique
                            self.current_device = {
                                "address": address,
                                "name": name
                            }
                            
                            # Démarrer la lecture audio
                            await self._start_audio_playback(address)
                            
                            # Notifier le changement d'état
                            await self.notify_state_change(PluginState.CONNECTED, {
                                "device_connected": True,
                                "device_name": name,
                                "device_address": address
                            })
                            
                            # Un seul périphérique à la fois
                            break
        except Exception as e:
            self.logger.error(f"Erreur vérification périphériques: {e}")
    
    async def _start_audio_playback(self, device_address: str) -> None:
        """Démarrage simplifié et robuste de la lecture audio"""
        try:
            # Arrêter d'abord tout processus bluealsa-aplay existant
            subprocess.run(["sudo", "pkill", "-f", "bluealsa-aplay"], check=False)
            await asyncio.sleep(1)
            
            # Démarrer bluealsa-aplay directement avec sudo
            self.logger.info(f"Démarrage de bluealsa-aplay pour {device_address}")
            
            # sudo bluealsa-aplay ADRESSE_MAC
            self.playback_process = subprocess.Popen(
                ["sudo", "bluealsa-aplay", device_address],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            # Vérifier si le processus a démarré
            await asyncio.sleep(2)
            if self.playback_process.poll() is not None:
                self.logger.error("Erreur: bluealsa-aplay s'est arrêté immédiatement")
                
                # Lire l'erreur
                stderr = self.playback_process.stderr.read()
                self.logger.error(f"Erreur bluealsa-aplay: {stderr.decode('utf-8')}")
                return
            
            self.logger.info("Lecture audio démarrée avec succès")
            
        except Exception as e:
            self.logger.error(f"Erreur démarrage lecture: {e}")
    
    async def stop(self) -> bool:
        """Arrête uniquement bluetooth (pas bluealsa)"""
        try:
            self.logger.info("Arrêt du plugin Bluetooth")
            
            # 1. Arrêter la lecture audio
            if self.playback_process and self.playback_process.poll() is None:
                self.playback_process.terminate()
                self.playback_process = None
            
            # 2. Arrêter tous les processus bluealsa-aplay
            subprocess.run(["sudo", "pkill", "-f", "bluealsa-aplay"], check=False)
            
            # 3. Désactiver l'adaptateur bluetooth
            subprocess.run(["sudo", "hciconfig", "hci0", "down"], check=False)
            
            # 4. Arrêter bluetooth (pas bluealsa)
            subprocess.run(["sudo", "systemctl", "stop", "bluetooth"], check=False)
            
            # 5. Réinitialiser l'état et notifier
            self.current_device = None
            await self.notify_state_change(PluginState.INACTIVE)
            
            return True
        except Exception as e:
            self.logger.error(f"Erreur arrêt Bluetooth: {e}")
            return False
    
    async def get_status(self) -> Dict[str, Any]:
        """État actuel du plugin"""
        # Vérifier si bluetooth est en cours d'exécution
        bt_running = subprocess.run(
            ["systemctl", "is-active", "bluetooth"], 
            capture_output=True, text=True
        ).stdout.strip() == "active"
        
        # Vérifier si bluealsa est en cours d'exécution
        bluealsa_running = subprocess.run(
            ["systemctl", "is-active", "bluealsa"], 
            capture_output=True, text=True
        ).stdout.strip() == "active"
        
        # Vérifier si bluealsa-aplay est en cours d'exécution
        playback_running = False
        if self.playback_process and self.playback_process.poll() is None:
            playback_running = True
        else:
            check = subprocess.run(
                ["pgrep", "-f", "bluealsa-aplay"],
                capture_output=True
            )
            playback_running = check.returncode == 0
        
        # Vérifier si l'audio est actif
        audio_active = False
        check = subprocess.run(
            ["sudo", "fuser", "-v", "/dev/snd/*"],
            capture_output=True, text=True
        )
        audio_active = "bluealsa-aplay" in check.stdout
        
        return {
            "device_connected": self.current_device is not None,
            "device_name": self.current_device.get("name") if self.current_device else None,
            "device_address": self.current_device.get("address") if self.current_device else None,
            "bluetooth_running": bt_running,
            "bluealsa_running": bluealsa_running,
            "playback_running": playback_running,
            "audio_active": audio_active
        }
    
    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Commandes spécifiques au plugin Bluetooth"""
        if command == "disconnect":
            if not self.current_device:
                return {"success": False, "error": "Aucun périphérique connecté"}
            
            address = self.current_device.get("address")
            subprocess.run(["bluetoothctl", "disconnect", address], check=False)
            
            # Arrêter la lecture
            if self.playback_process and self.playback_process.poll() is None:
                self.playback_process.terminate()
                self.playback_process = None
            
            # Réinitialiser l'état
            self.current_device = None
            await self.notify_state_change(PluginState.READY, {"device_connected": False})
            
            return {"success": True}
            
        elif command == "restart_audio":
            if not self.current_device:
                return {"success": False, "error": "Aucun périphérique connecté"}
            
            address = self.current_device.get("address")
            await self._start_audio_playback(address)
            return {"success": True}
            
        elif command == "start_direct_audio":
            # Commande d'urgence pour démarrer la lecture directement
            if not self.current_device:
                return {"success": False, "error": "Aucun périphérique connecté"}
            
            address = self.current_device.get("address")
            
            # Arrêter d'abord tout processus existant
            subprocess.run(["sudo", "pkill", "-f", "bluealsa-aplay"], check=False)
            await asyncio.sleep(1)
            
            # Lancer en mode direct
            cmd = ["sudo", "bluealsa-aplay", address]
            
            # Exécution non bloquante
            subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            
            return {"success": True, "message": "Démarrage direct lancé"}
            
        return {"success": False, "error": f"Commande inconnue: {command}"}
    
    async def _handle_device_event(self, event_type: str, device: Dict[str, Any]) -> None:
        """Gère les événements de connexion/déconnexion des périphériques"""
        address = device.get("address")
        name = device.get("name", "Appareil inconnu")
        
        if event_type == "connected":
            self.logger.info(f"Périphérique connecté: {name} ({address})")
            
            # Attendre que BlueALSA détecte le périphérique
            await asyncio.sleep(2)
            
            # Vérifier si le périphérique est disponible dans BlueALSA
            check = subprocess.run(
                ["bluealsa-aplay", "-L"], 
                capture_output=True, text=True
            )
            
            if address.lower() in check.stdout.lower():
                self.current_device = {
                    "address": address,
                    "name": name
                }
                
                # Notifier le changement d'état
                await self.notify_state_change(PluginState.CONNECTED, {
                    "device_connected": True,
                    "device_name": name,
                    "device_address": address
                })
                
                # Démarrer la lecture audio
                await self._start_audio_playback(address)
            
        elif event_type == "disconnected" and self.current_device and self.current_device.get("address") == address:
            self.logger.info(f"Périphérique déconnecté: {name}")
            
            # Arrêter la lecture audio
            if self.playback_process and self.playback_process.poll() is None:
                self.playback_process.terminate()
                self.playback_process = None
            
            # Réinitialiser l'état
            self.current_device = None
            
            # Notifier le changement d'état
            await self.notify_state_change(PluginState.READY, {
                "device_connected": False
            })
    
    def manages_own_process(self) -> bool:
        """Le plugin gère ses propres processus"""
        return True
    
    def get_process_command(self) -> List[str]:
        """Non utilisé (manages_own_process = True)"""
        return ["true"]
    
    async def get_initial_state(self) -> Dict[str, Any]:
        """État initial du plugin"""
        return await self.get_status()