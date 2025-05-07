"""
Plugin Bluetooth gérant la configuration complète pour oakOS
"""
import asyncio
import logging
import subprocess
import time
import os
import shutil
from typing import Dict, Any, Optional, List

from backend.application.event_bus import EventBus
from backend.infrastructure.plugins.base import UnifiedAudioPlugin
from backend.domain.audio_state import PluginState
from backend.infrastructure.plugins.bluetooth.dbus_manager import BluetoothDBusManager
from backend.infrastructure.plugins.bluetooth.agent import BluetoothAgent
from backend.infrastructure.plugins.bluetooth.alsa_manager import AlsaManager


class BluetoothPlugin(UnifiedAudioPlugin):
    """Plugin Bluetooth pour oakOS - Configuration complète et auto-connexion"""
    
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
        self.bluetooth_conf_path = "/etc/bluetooth/main.conf"
        self.backup_conf_path = "/tmp/bluetooth_main.conf.bak"
    
    async def get_status(self) -> Dict[str, Any]:
        """Récupère l'état actuel du plugin"""
        try:
            # Vérifier que les services sont en cours d'exécution
            bluealsa_running = False
            try:
                result = subprocess.run(["pgrep", "-f", "blue(alsa|alsad)"], shell=True, capture_output=True)
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
                    return {"success": False, "error": "Aucun périphérique connecté"}
                
                address = self.current_device.get("address")
                if not address:
                    return {"success": False, "error": "Adresse du périphérique invalide"}
                
                success = await self.dbus_manager.disconnect_device(address)
                return {"success": success}
            
            elif command == "restart_services":
                # Redémarrer les services Bluetooth
                await self._stop_bluetooth_services()
                success = await self._start_bluetooth_services()
                return {"success": success, "message": "Services Bluetooth redémarrés"}
            
            return {"success": False, "error": f"Commande inconnue: {command}"}
        except Exception as e:
            self.logger.error(f"Erreur dans le traitement de la commande {command}: {e}")
            return {"success": False, "error": str(e)}
    
    async def get_initial_state(self) -> Dict[str, Any]:
        """Récupère l'état initial complet du plugin"""
        return await self.get_status()
    
    
    def get_process_command(self) -> List[str]:
        """Retourne la commande pour démarrer BlueALSA"""
        # Variable d'environnement pour éviter les deadlocks
        executable = "/usr/bin/bluealsa"  # On sait que c'est cette version qui est installée
        
        # Ne pas inclure sudo ici car subprocess.Popen(['sudo', ...]) ne fonctionne pas bien avec PIPE
        # Le sudo sera ajouté dans la méthode _start_bluetooth_services
        cmd = ["env", "LIBASOUND_THREAD_SAFE=0", executable, "-p", "a2dp-sink"]
        
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
            
            # Configurer le fichier de configuration Bluetooth
            if not await self._configure_bluetooth_conf():
                self.logger.warning("Impossible de configurer le fichier Bluetooth - continuera quand même")
            
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
            
            # Définir la classe de périphérique manuellement (plus fiable que via D-Bus)
            try:
                subprocess.run(["sudo", "hciconfig", "hci0", "class", "0x240414"], check=True)
                self.logger.info("Classe de périphérique définie à 0x240414 (récepteur audio)")
            except Exception as e:
                self.logger.warning(f"Échec définition classe de périphérique: {e}")
            
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
            
            # Assurer que toutes les tâches en arrière-plan sont annulées
            if hasattr(self, '_background_tasks') and self._background_tasks:
                for task in self._background_tasks:
                    if not task.done():
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                self._background_tasks = []
                
            await self.notify_state_change(PluginState.INACTIVE)
            return True
        except Exception as e:
            self.logger.error(f"Erreur d'arrêt Bluetooth: {e}")
            await self.notify_state_change(PluginState.ERROR, {
                "error": str(e),
                "error_type": "stop_error"
            })
            return False

    async def _configure_bluetooth_conf(self) -> bool:
        """Configure le fichier /etc/bluetooth/main.conf"""
        try:
            # Vérifier si le fichier existe
            if not os.path.exists(self.bluetooth_conf_path):
                self.logger.warning(f"Le fichier {self.bluetooth_conf_path} n'existe pas")
                return False
            
            # Créer une sauvegarde du fichier original
            if not os.path.exists(self.backup_conf_path):
                shutil.copy2(self.bluetooth_conf_path, self.backup_conf_path)
                self.logger.info(f"Sauvegarde de {self.bluetooth_conf_path} créée")
            
            # Lire le fichier
            with open(self.bluetooth_conf_path, 'r') as f:
                content = f.readlines()
            
            # Paramètres à configurer
            params = {
                "Class": "0x240414",
                "Name": "oakOS",
                "DiscoverableTimeout": "0",
                "PairableTimeout": "0",
                "Privacy": "off",
                "FastConnectable": "true"
            }
            
            # Rechercher et modifier les paramètres
            for param, value in params.items():
                found = False
                for i, line in enumerate(content):
                    if line.strip().startswith(f"#{param} =") or line.strip().startswith(f"{param} ="):
                        content[i] = f"{param} = {value}\n"
                        found = True
                        break
                
                if not found:
                    # Ajouter le paramètre à la fin du fichier
                    content.append(f"{param} = {value}\n")
            
            # Écrire le fichier modifié
            try:
                # Essayer d'écrire directement
                with open(self.bluetooth_conf_path, 'w') as f:
                    f.writelines(content)
            except PermissionError:
                # Si permission refusée, utiliser sudo
                temp_path = "/tmp/bluetooth_main.conf.new"
                with open(temp_path, 'w') as f:
                    f.writelines(content)
                subprocess.run(["sudo", "cp", temp_path, self.bluetooth_conf_path], check=True)
                os.remove(temp_path)
            
            self.logger.info(f"Configuration {self.bluetooth_conf_path} mise à jour")
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur configuration Bluetooth: {e}")
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
            
            # Arrêter les instances existantes
            await self._stop_bluetooth_services()
            
            # Fichiers pour la sortie de logs
            stdout_file = "/tmp/bluealsa_stdout.log"
            stderr_file = "/tmp/bluealsa_stderr.log"
            
            # Utiliser directement bluealsa-aplay pour tester les permissions
            try:
                self.logger.info("Test de l'état d'installation de BlueALSA")
                which_result = subprocess.run(["which", "bluealsa"], capture_output=True, text=True)
                self.logger.info(f"BlueALSA trouvé: {which_result.stdout.strip() if which_result.returncode == 0 else 'Non trouvé'}")
                
                # Vérifier les permissions audio de l'utilisateur actuel
                username = os.getenv("USER") or "unknown"
                groups_output = subprocess.run(["groups", username], capture_output=True, text=True)
                self.logger.info(f"Groupes de l'utilisateur {username}: {groups_output.stdout}")
            except Exception as e:
                self.logger.error(f"Erreur lors du test audio: {e}")
            
            # Méthode simplifiée: Démarrer BlueALSA via systemd si disponible
            try:
                # Vérifier si le service systemd existe
                systemd_check = subprocess.run(["systemctl", "list-unit-files", "bluealsa.service"], 
                                            capture_output=True, text=True)
                
                if "bluealsa.service" in systemd_check.stdout:
                    self.logger.info("Service systemd BlueALSA trouvé, démarrage via systemd")
                    subprocess.run(["sudo", "systemctl", "restart", "bluealsa"], check=True)
                    await asyncio.sleep(2)
                    
                    # Vérifier si le service est actif
                    status = subprocess.run(["systemctl", "is-active", "bluealsa"], 
                                        capture_output=True, text=True)
                    
                    if status.stdout.strip() == "active":
                        self.logger.info("Service BlueALSA démarré avec succès via systemd")
                        self.bluetooth_service_active = True
                        return True
                    else:
                        self.logger.error(f"Échec du démarrage du service BlueALSA via systemd: {status.stdout}")
                
                # Si systemd n'est pas disponible, démarrer manuellement
                self.logger.info("Démarrage manuel de BlueALSA")
                
                # Obtenir la commande mais sans env pour éviter les problèmes avec sudo
                # et en spécifiant directement le chemin complet vers l'exécutable
                cmd = ["/usr/bin/bluealsa", "-p", "a2dp-sink"]
                options = self.config.get("daemon_options", "--keep-alive=5 --initial-volume=80")
                if options:
                    cmd.extend(options.split())
                
                # Créer un script qui démarre BlueALSA avec LIBASOUND_THREAD_SAFE=0
                with open("/tmp/start_bluealsa.sh", "w") as f:
                    f.write("#!/bin/bash\n")
                    f.write("sudo LIBASOUND_THREAD_SAFE=0 " + " ".join(cmd) + " > " + stdout_file + " 2> " + stderr_file + " &\n")
                    f.write("echo $!\n")  # Affiche le PID du processus en arrière-plan
                
                os.chmod("/tmp/start_bluealsa.sh", 0o755)
                
                # Exécuter le script et capturer le PID
                result = subprocess.run(["/tmp/start_bluealsa.sh"], capture_output=True, text=True)
                
                # Attendre que le processus démarre
                await asyncio.sleep(2)
                
                # Vérifier si BlueALSA est en cours d'exécution
                check = subprocess.run(["pgrep", "-f", "bluealsa"], capture_output=True)
                if check.returncode != 0:
                    # Lire les logs d'erreur
                    stderr_content = ""
                    stdout_content = ""
                    try:
                        if os.path.exists(stderr_file):
                            with open(stderr_file, "r") as f:
                                stderr_content = f.read()
                        if os.path.exists(stdout_file):
                            with open(stdout_file, "r") as f:
                                stdout_content = f.read()
                    except Exception as e:
                        self.logger.error(f"Erreur lors de la lecture des logs: {e}")
                    
                    self.logger.error(f"BlueALSA n'a pas démarré. Stdout: {stdout_content}")
                    self.logger.error(f"BlueALSA n'a pas démarré. Stderr: {stderr_content}")
                    return False
                
                self.logger.info("BlueALSA démarré avec succès")
                self.bluetooth_service_active = True
                return True
                
            except Exception as e:
                self.logger.error(f"Erreur lors du démarrage de BlueALSA: {e}")
                return False
            
        except Exception as e:
            self.logger.error(f"Erreur lors du démarrage des services Bluetooth: {e}")
            return False
    
    async def _stop_bluetooth_services(self) -> bool:
        """Arrête les services Bluetooth"""
        try:
            self.logger.info("Arrêt des services Bluetooth...")
            
            # Créer un script pour arrêter proprement BlueALSA
            script_path = "/tmp/stop_bluealsa.sh"
            with open(script_path, "w") as f:
                f.write("""#!/bin/bash
    # Arrêter toutes les instances de bluealsa-aplay
    sudo pkill -f "bluealsa-aplay" || true
    sleep 0.5

    # Arrêter BlueALSA avec sudo
    sudo pkill -f "bluealsa" || true
    sleep 0.5

    # Forcer l'arrêt de toute instance restante
    for pid in $(sudo pgrep -f "bluealsa"); do
        sudo kill -9 $pid 2>/dev/null || true
    done
    """)
            os.chmod(script_path, 0o755)
            
            # Exécuter le script d'arrêt
            subprocess.run(["bash", script_path], check=False)
            
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