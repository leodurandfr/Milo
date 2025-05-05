"""
Plugin Bluetooth adapté au contrôle centralisé des processus
"""
import asyncio
import logging
import subprocess
import time
import os
from typing import Dict, Any, Optional, List

from backend.application.event_bus import EventBus
from backend.infrastructure.plugins.base import UnifiedAudioPlugin
from backend.domain.audio_state import PluginState
from backend.infrastructure.plugins.bluetooth.dbus_manager import BluetoothDBusManager
from backend.infrastructure.plugins.bluetooth.agent import BluetoothAgent
from backend.infrastructure.plugins.bluetooth.alsa_manager import AlsaManager


class BluetoothPlugin(UnifiedAudioPlugin):
    """Plugin Bluetooth pour oakOS - Réception audio uniquement via a2dp-sink"""
    
    def __init__(self, event_bus: EventBus, config: Dict[str, Any]):
        super().__init__(event_bus, "bluetooth")
        self.config = config
        self.dbus_manager = None
        self.agent = None
        self.alsa_manager = None
        self.current_device = None
        self._initialized = False
        self.playback_process = None
        self.bluetooth_service_active = False
    
    def get_process_command(self) -> List[str]:
        """Retourne la commande pour démarrer BlueALSA"""
        # Variable d'environnement pour éviter les deadlocks
        cmd = ["env", "LIBASOUND_THREAD_SAFE=0", "/usr/bin/bluealsa", "-p", "a2dp-sink"]
        
        # Options additionnelles
        options = self.config.get("daemon_options", "--keep-alive=5 --initial-volume=80")
        if options:
            cmd.extend(options.split())
            
        return cmd
    
    async def initialize(self) -> bool:
        """Initialise le plugin Bluetooth"""
        if self._initialized:
            return True
            
        try:
            self.logger.info("Initialisation du plugin Bluetooth")
            
            # Vérifier si les services sont déjà actifs et les arrêter si nécessaire
            await self._stop_bluetooth_services()
            
            # Initialiser le gestionnaire D-Bus
            self.dbus_manager = BluetoothDBusManager()
            if not await self.dbus_manager.initialize():
                raise Exception("Échec de l'initialisation du gestionnaire D-Bus")
            
            # Initialiser le gestionnaire ALSA
            self.alsa_manager = AlsaManager()
            
            # Créer l'agent Bluetooth
            self.agent = BluetoothAgent(self.dbus_manager.system_bus)
            
            # Enregistrer le callback pour les événements de périphérique
            self.dbus_manager.register_device_callback(self._handle_device_event)
            
            self._initialized = True
            return True
        except Exception as e:
            self.logger.error(f"Erreur d'initialisation Bluetooth: {e}")
            await self.notify_state_change(PluginState.ERROR, {
                "error": str(e),
                "error_type": "initialization_error"
            })
            return False
    
    async def start(self) -> bool:
        """Démarre le plugin - Gère le démarrage du service BlueALSA"""
        try:
            self.logger.info("Démarrage du plugin Bluetooth")
            
            # Démarrer les services Bluetooth si nécessaire
            if not await self._start_bluetooth_services():
                self.logger.error("Échec du démarrage des services Bluetooth")
                await self.notify_state_change(PluginState.ERROR, {
                    "error": "Échec du démarrage des services Bluetooth",
                    "error_type": "service_start_error"
                })
                return False
            
            # Configurer l'adaptateur Bluetooth
            success = await self.dbus_manager.configure_adapter(
                name="oakOS",
                discoverable=True,
                discoverable_timeout=0,
                pairable=True,
                pairable_timeout=0
            )
            
            if not success:
                self.logger.error("Échec de la configuration de l'adaptateur Bluetooth")
                await self.notify_state_change(PluginState.ERROR, {
                    "error": "Échec de la configuration de l'adaptateur Bluetooth",
                    "error_type": "adapter_configuration_error"
                })
                return False
            
            # Enregistrer l'agent Bluetooth
            if not await self.agent.register():
                self.logger.error("Échec de l'enregistrement de l'agent Bluetooth")
                await self.notify_state_change(PluginState.ERROR, {
                    "error": "Échec de l'enregistrement de l'agent Bluetooth",
                    "error_type": "agent_registration_error"
                })
                return False
                
            # Vérifier les périphériques déjà connectés
            connected_devices = await self.dbus_manager.get_connected_devices()
            if connected_devices:
                device = connected_devices[0]
                self.current_device = device
                self.logger.info(f"Périphérique Bluetooth déjà connecté: {device.get('name', 'Unknown')}")
                
                await self.notify_state_change(PluginState.CONNECTED, {
                    "device_connected": True,
                    "device_name": device.get("name", "Unknown"),
                    "device_address": device.get("address"),
                })
                
                # Démarrer la lecture audio automatiquement
                await self._start_audio_playback(device.get("address"))
            else:
                self.logger.info("Aucun périphérique Bluetooth connecté, en attente...")
                await self.notify_state_change(PluginState.READY)
            
            return True
        except Exception as e:
            self.logger.error(f"Erreur de démarrage Bluetooth: {e}")
            await self.notify_state_change(PluginState.ERROR, {
                "error": str(e),
                "error_type": "start_error"
            })
            return False
    
    async def stop(self) -> bool:
        """Arrête le plugin - y compris les services Bluetooth"""
        try:
            self.logger.info("Arrêt du plugin Bluetooth")
            
            # Arrêter la lecture audio
            if self.playback_process:
                try:
                    self.playback_process.terminate()
                    await asyncio.sleep(0.5)
                    if self.playback_process.poll() is None:
                        self.playback_process.kill()
                except Exception as e:
                    self.logger.error(f"Erreur lors de l'arrêt du processus de lecture: {e}")
                self.playback_process = None
            
            # Désenregistrer l'agent
            if self.agent:
                try:
                    await self.agent.unregister()
                except Exception as e:
                    self.logger.error(f"Erreur lors du désenregistrement de l'agent: {e}")
            
            # Arrêter les services Bluetooth
            await self._stop_bluetooth_services()
            
            # Réinitialiser l'état
            self.current_device = None
            
            await self.notify_state_change(PluginState.INACTIVE)
            return True
        except Exception as e:
            self.logger.error(f"Erreur d'arrêt Bluetooth: {e}")
            await self.notify_state_change(PluginState.ERROR, {
                "error": str(e),
                "error_type": "stop_error"
            })
            return False
    
    async def _start_bluetooth_services(self) -> bool:
        """Démarre les services Bluetooth nécessaires"""
        if self.bluetooth_service_active:
            return True
            
        try:
            self.logger.info("Démarrage des services Bluetooth...")
            
            # Démarrer le service Bluetooth (si nécessaire)
            try:
                subprocess.run(["sudo", "systemctl", "start", "bluetooth"], check=True)
                await asyncio.sleep(1)  # Attendre que le service démarre
            except subprocess.CalledProcessError as e:
                self.logger.error(f"Erreur lors du démarrage du service bluetooth: {e}")
                return False
            
            # Démarrer le service BlueALSA
            # Vérifier d'abord si le service est déjà en cours d'exécution
            try:
                result = subprocess.run(["pgrep", "-f", "bluealsa"], capture_output=True)
                if result.returncode == 0:
                    self.logger.info("Service BlueALSA déjà en cours d'exécution, arrêt...")
                    for pid in result.stdout.decode().strip().split():
                        try:
                            subprocess.run(["sudo", "kill", pid], check=True)
                        except Exception as e:
                            self.logger.warning(f"Erreur lors de l'arrêt du processus BlueALSA {pid}: {e}")
                    await asyncio.sleep(1)  # Attendre que les processus soient arrêtés
            except Exception as e:
                self.logger.warning(f"Erreur lors de la vérification des processus BlueALSA: {e}")
            
            # Démarrer BlueALSA
            cmd = self.get_process_command()
            self.logger.info(f"Démarrage de BlueALSA avec la commande: {' '.join(cmd)}")
            
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            await asyncio.sleep(2)  # Attendre que le service démarre
            
            if process.poll() is not None:
                # Le processus s'est arrêté
                stderr = process.stderr.read().decode() if process.stderr else "Pas de sortie stderr"
                self.logger.error(f"BlueALSA s'est arrêté immédiatement. Erreur: {stderr}")
                return False
            
            # Démarrer également bluealsa-aplay (s'il y a déjà un périphérique connecté)
            self.logger.info("Services Bluetooth démarrés avec succès")
            self.bluetooth_service_active = True
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur lors du démarrage des services Bluetooth: {e}")
            return False
    
    async def _stop_bluetooth_services(self) -> bool:
        """Arrête les services Bluetooth via systemd"""
        try:
            self.logger.info("Arrêt des services Bluetooth...")
            
            # Utiliser systemctl pour arrêter les services
            try:
                # Vérifier si un service bluealsa existe dans systemd
                result = subprocess.run(["systemctl", "list-units", "--all", "*bluealsa*"], 
                                    capture_output=True, text=True)
                
                if "bluealsa" in result.stdout:
                    # Arrêter le service via systemd
                    self.logger.info("Arrêt du service BlueALSA via systemd...")
                    subprocess.run(["sudo", "systemctl", "stop", "bluealsa"], check=False)
                else:
                    self.logger.info("Aucun service systemd BlueALSA trouvé, vérification des processus...")
                    # Créer un fichier temporaire avec un script pour arrêter les processus
                    script_path = "/tmp/stop_bluealsa.sh"
                    with open(script_path, "w") as f:
                        f.write("""#!/bin/bash
    pkill -f "bluealsa-aplay" || true
    pkill -f "bluealsa " || true  # Espace important pour différencier de bluealsa-aplay
    """)
                    os.chmod(script_path, 0o755)
                    subprocess.run(["sudo", "bash", script_path], check=False)
                    
            except Exception as e:
                self.logger.warning(f"Erreur lors de l'arrêt des services via systemd: {e}")
            
            # Attendre que les processus soient terminés
            await asyncio.sleep(1)
            
            # Vérifier que les services sont bien arrêtés
            result = subprocess.run(["pgrep", "-f", "bluealsa"], capture_output=True)
            if result.returncode == 0:
                self.logger.warning("Les services BlueALSA sont toujours en cours d'exécution")
            else:
                self.logger.info("Services Bluetooth arrêtés avec succès")
                
            self.bluetooth_service_active = False
            return True
                
        except Exception as e:
            self.logger.error(f"Erreur lors de l'arrêt des services Bluetooth: {e}")
            return False
    
    async def _handle_device_event(self, event_type: str, device: Dict[str, Any]) -> None:
        """Gère les événements de périphérique Bluetooth"""
        try:
            address = device.get("address")
            name = device.get("name", "Unknown Device")
            
            if event_type == "connected":
                self.logger.info(f"Périphérique Bluetooth connecté: {name} ({address})")
                
                # Vérifier si le périphérique prend en charge A2DP
                if not device.get("a2dp_sink_support", False):
                    self.logger.warning(f"Le périphérique {name} ne prend pas en charge A2DP Sink")
                    return
                
                # Mettre à jour le périphérique actuel
                self.current_device = device
                
                await self.notify_state_change(PluginState.CONNECTED, {
                    "device_connected": True,
                    "device_name": name,
                    "device_address": address,
                })
                
                # Démarrer la lecture audio
                await self._start_audio_playback(address)
                
            elif event_type == "disconnected" and self.current_device and self.current_device.get("address") == address:
                self.logger.info(f"Périphérique Bluetooth déconnecté: {name}")
                
                # Arrêter la lecture audio
                if self.playback_process:
                    try:
                        self.playback_process.terminate()
                    except Exception:
                        pass
                    self.playback_process = None
                
                # Réinitialiser le périphérique actuel
                self.current_device = None
                
                await self.notify_state_change(PluginState.READY, {
                    "device_connected": False
                })
        except Exception as e:
            self.logger.error(f"Erreur lors du traitement de l'événement périphérique: {e}")
    
    async def _start_audio_playback(self, device_address: str) -> None:
        """Démarre la lecture audio depuis le périphérique Bluetooth"""
        try:
            # Arrêter la lecture existante si présente
            if self.playback_process:
                try:
                    self.playback_process.terminate()
                    await asyncio.sleep(0.5)
                except Exception:
                    pass
                self.playback_process = None
                
            # Utiliser bluealsa-aplay pour lire l'audio
            self.logger.info(f"Démarrage de la lecture audio pour {device_address}")
            
            # Vérifier si bluealsa-aplay est disponible
            if not os.path.exists("/usr/bin/bluealsa-aplay"):
                self.logger.error("bluealsa-aplay n'est pas disponible")
                return
                
            self.playback_process = subprocess.Popen([
                "/usr/bin/bluealsa-aplay",
                "--profile-a2dp",
                device_address
            ])
            
            # Vérifier que le processus a bien démarré
            await asyncio.sleep(0.5)
            if self.playback_process.poll() is not None:
                stderr = self.playback_process.stderr.read().decode() if self.playback_process.stderr else "Pas de sortie stderr"
                self.logger.error(f"Erreur: bluealsa-aplay s'est arrêté immédiatement. Erreur: {stderr}")
            else:
                self.logger.info("Lecture audio démarrée avec succès")
                
        except Exception as e:
            self.logger.error(f"Erreur lors du démarrage de la lecture audio: {e}")
    
    async def get_status(self) -> Dict[str, Any]:
        """Récupère l'état actuel du plugin"""
        try:
            # Vérifier que les services sont en cours d'exécution
            bluealsa_running = False
            try:
                result = subprocess.run(["pgrep", "-f", "bluealsa "], capture_output=True)  # Espace important
                bluealsa_running = result.returncode == 0
            except Exception:
                pass
                
            # Vérifier que le processus de lecture est toujours en cours
            playback_running = False
            if self.playback_process:
                playback_running = self.playback_process.poll() is None
            
            return {
                "device_connected": self.current_device is not None,
                "device_name": self.current_device.get("name", "Unknown") if self.current_device else None,
                "device_address": self.current_device.get("address") if self.current_device else None,
                "playback_running": playback_running,
                "services_running": bluealsa_running,
                "a2dp_sink_support": self.current_device.get("a2dp_sink_support", False) if self.current_device else False
            }
        except Exception as e:
            self.logger.error(f"Erreur lors de la récupération du statut: {e}")
            return {
                "device_connected": False,
                "services_running": False,
                "error": str(e)
            }
    
    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite les commandes spécifiques à cette source"""
        try:
            if command == "disconnect":
                if not self.current_device:
                    return {"success": False, "error": "No device connected"}
                
                address = self.current_device.get("address")
                if not address:
                    return {"success": False, "error": "Invalid device address"}
                
                success = await self.dbus_manager.disconnect_device(address)
                return {"success": success}
            
            elif command == "restart_services":
                # Redémarrer les services Bluetooth
                await self._stop_bluetooth_services()
                success = await self._start_bluetooth_services()
                return {"success": success, "message": "Services Bluetooth redémarrés"}
            
            return {"success": False, "error": f"Unknown command: {command}"}
        except Exception as e:
            self.logger.error(f"Error handling command {command}: {e}")
            return {"success": False, "error": str(e)}
    
    async def get_initial_state(self) -> Dict[str, Any]:
        """Récupère l'état initial complet du plugin"""
        return await self.get_status()