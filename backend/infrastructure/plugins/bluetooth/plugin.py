"""
Plugin Bluetooth pour la gestion audio A2DP sur oakOS
"""
import asyncio
import logging
import subprocess
import os
from typing import Dict, Any, List

from backend.application.event_bus import EventBus
from backend.infrastructure.plugins.base import UnifiedAudioPlugin
from backend.domain.audio_state import PluginState
from backend.infrastructure.plugins.bluetooth.dbus_manager import BluetoothDBusManager
from backend.infrastructure.plugins.bluetooth.agent import BluetoothAgent
from backend.infrastructure.plugins.bluetooth.alsa_manager import AlsaManager


class BluetoothPlugin(UnifiedAudioPlugin):
    """Plugin Bluetooth pour la réception audio via A2DP"""
    
    def __init__(self, event_bus: EventBus, config: Dict[str, Any]):
        super().__init__(event_bus, "bluetooth")
        self.config = config
        self.dbus_manager = None
        self.agent = None
        self.alsa_manager = None
        self.current_device = None
        self._initialized = False
        self.playback_process = None
        self.bluetooth_conf_path = "/etc/bluetooth/main.conf"
    
    async def get_status(self) -> Dict[str, Any]:
        """Récupère l'état actuel du plugin"""
        # Vérifier que BlueALSA est en cours d'exécution
        bluealsa_running = subprocess.run(["pgrep", "-f", "blue(alsa|alsad)"], 
                                         shell=True, capture_output=True).returncode == 0
        
        # Vérifier que le processus de lecture est actif
        playback_running = self.playback_process and self.playback_process.poll() is None
        
        # Vérifier que le service bluetooth est actif
        bluetooth_running = subprocess.run(["systemctl", "is-active", "bluetooth"], 
                                          capture_output=True, text=True).stdout.strip() == "active"
        
        return {
            "device_connected": self.current_device is not None,
            "device_name": self.current_device.get("name", "Unknown") if self.current_device else None,
            "device_address": self.current_device.get("address") if self.current_device else None,
            "playback_running": playback_running,
            "services_running": bluealsa_running and bluetooth_running
        }

    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite les commandes spécifiques au plugin Bluetooth"""
        try:
            if command == "disconnect":
                if not self.current_device:
                    return {"success": False, "error": "Aucun périphérique connecté"}
                
                address = self.current_device.get("address")
                if not address:
                    return {"success": False, "error": "Adresse invalide"}
                
                success = await self.dbus_manager.disconnect_device(address)
                return {"success": success}
            
            elif command == "restart_services":
                await self.stop()
                success = await self.start()
                return {"success": success, "message": "Services Bluetooth redémarrés"}
            
            elif command == "restart_audio":
                # Commande spécifique pour redémarrer uniquement la partie audio
                if not self.current_device or not self.current_device.get("address"):
                    return {"success": False, "error": "Aucun périphérique connecté"}
                
                # Arrêter puis redémarrer la lecture audio
                if self.playback_process:
                    self.playback_process.terminate()
                    self.playback_process = None
                
                await asyncio.sleep(1)
                await self._start_audio_playback(self.current_device.get("address"))
                return {"success": True, "message": "Lecture audio redémarrée"}
            
            return {"success": False, "error": f"Commande inconnue: {command}"}
        except Exception as e:
            self.logger.error(f"Erreur commande {command}: {e}")
            return {"success": False, "error": str(e)}
    
    async def get_initial_state(self) -> Dict[str, Any]:
        """État initial du plugin"""
        return await self.get_status()

    def manages_own_process(self) -> bool:
        """Le plugin gère son propre processus"""
        return True

    def get_process_command(self) -> List[str]:
        """Commande fictive (non utilisée car manages_own_process = True)"""
        return ["tail", "-f", "/dev/null"]
    
    async def initialize(self) -> bool:
        """
        Initialise les objets et configure le fichier de configuration
        mais ne démarre pas les services.
        """
        if self._initialized:
            return True
            
        try:
            self.logger.info("Initialisation du plugin Bluetooth")
            
            # 1. Configurer le fichier de configuration
            await self._configure_bluetooth_conf()
            
            # 2. S'assurer que bluetooth est COMPLÈTEMENT arrêté au démarrage
            subprocess.run(["sudo", "systemctl", "stop", "bluetooth"], check=False)
            await asyncio.sleep(1)
            
            # 2.1 Désactiver également le démarrage automatique
            subprocess.run(["sudo", "systemctl", "disable", "bluetooth"], check=False)
            
            # 2.2 Vérifier que l'adaptateur est désactivé physiquement
            try:
                subprocess.run(["sudo", "hciconfig", "hci0", "down"], check=False)
            except:
                pass
            
            # 3. Initialiser les gestionnaires D-Bus avec prudence (sans activer bluetooth)
            self.dbus_manager = BluetoothDBusManager()
            await self.dbus_manager.initialize()
            
            # 4. Initialiser ALSA et agent (sans les démarrer)
            self.alsa_manager = AlsaManager()
            self.agent = BluetoothAgent(self.dbus_manager.system_bus)
            
            # 5. Callbacks pour les événements
            self.dbus_manager.register_device_callback(self._handle_device_event)
            
            # 6. Vérifier encore une fois que bluetooth est bien arrêté
            subprocess.run(["sudo", "systemctl", "stop", "bluetooth"], check=False)
            
            self._initialized = True
            return True
        except Exception as e:
            self.logger.error(f"Erreur initialisation: {e}")
            await self.notify_state_change(PluginState.ERROR, {
                "error": str(e),
                "error_type": "initialization_error"
            })
            return False
    
    async def _configure_bluetooth_conf(self) -> None:
        """Configure le fichier de configuration Bluetooth une fois pour toutes"""
        config_content = """
# Configuration Bluetooth pour oakOS
[General]
Name = oakOS
Class = 0x240414
DiscoverableTimeout = 0
PairableTimeout = 0
AlwaysPairable = true
Privacy = off
FastConnectable = true
"""

        try:
            # Écrire dans un fichier temporaire
            temp_path = "/tmp/bluetooth_main.conf.new"
            with open(temp_path, "w") as f:
                f.write(config_content)
            
            # Copier avec sudo
            subprocess.run(["sudo", "cp", temp_path, self.bluetooth_conf_path], check=True)
            self.logger.info("Configuration Bluetooth enregistrée")
        except Exception as e:
            self.logger.warning(f"Erreur configuration Bluetooth: {e}")
    
    async def start(self) -> bool:
        """Démarre les services Bluetooth et active l'adaptateur"""
        try:
            self.logger.info("Démarrage du plugin Bluetooth")
            
            # 1. Réactiver temporairement le démarrage automatique
            subprocess.run(["sudo", "systemctl", "enable", "bluetooth"], check=False)
            
            # 2. Démarrer le service bluetooth
            subprocess.run(["sudo", "systemctl", "start", "bluetooth"], check=True)
            await asyncio.sleep(2)  # Attendre le démarrage du service
            
            # 3. S'assurer qu'aucune instance de BlueALSA n'est en cours
            subprocess.run(["sudo", "pkill", "-f", "blue(alsa|alsad)"], shell=True, check=False)
            await asyncio.sleep(1)
            
            # 4. Activer et configurer l'adaptateur
            await self._activate_adapter()
            
            # 5. Démarrer BlueALSA
            if not await self._start_bluealsa():
                self.logger.error("Impossible de démarrer BlueALSA")
                return False
            
            # 6. Enregistrer l'agent Bluetooth
            if self.agent:
                await self.agent.register()
                self.logger.info("Agent Bluetooth enregistré")
            
            # 7. Notifier l'état
            await self.notify_state_change(PluginState.READY, {
                "status": "Prêt à accepter les connexions"
            })
            
            return True
        except Exception as e:
            self.logger.error(f"Erreur démarrage Bluetooth: {e}")
            await self.notify_state_change(PluginState.ERROR, {
                "error": str(e),
                "error_type": "start_error"
            })
            return False
        
    async def _activate_adapter(self) -> None:
        """Active et configure l'adaptateur Bluetooth"""
        try:
            # Script pour activer l'adaptateur avec les bons paramètres
            with open("/tmp/activate_bt.sh", "w") as f:
                f.write("""#!/bin/bash
# Activer l'adaptateur et appliquer les paramètres
sudo hciconfig hci0 up
sleep 1
sudo hciconfig hci0 class 0x240414
sudo hciconfig hci0 name "oakOS"
sleep 1
sudo bluetoothctl discoverable on
sudo bluetoothctl pairable on
sudo bluetoothctl discoverable-timeout 0
""")
            
            os.chmod("/tmp/activate_bt.sh", 0o755)
            subprocess.run(["/tmp/activate_bt.sh"], check=True)
            
            # Vérifier la configuration 
            result = subprocess.run(["sudo", "hciconfig", "hci0", "class"], 
                                   capture_output=True, text=True)
            self.logger.info(f"Adaptateur activé: {result.stdout.strip()}")
        except Exception as e:
            self.logger.warning(f"Erreur activation adaptateur: {e}")
    
    async def _start_bluealsa(self) -> bool:
        """Démarre le service BlueALSA"""
        # Vérifier d'abord si le service systemd existe
        systemd_check = subprocess.run(["systemctl", "list-unit-files", "bluealsa.service"],
                                    capture_output=True, text=True)
        
        if "bluealsa.service" in systemd_check.stdout:
            # Si on utilise systemd, il faut modifier le service pour n'avoir que a2dp-sink
            try:
                # Arrêter le service existant
                subprocess.run(["sudo", "systemctl", "stop", "bluealsa"], check=False)
                
                # Démarrer manuellement bluealsa avec les bons paramètres
                bluealsa_path = None
                for path in ["/usr/bin/bluealsa", "/usr/bin/bluealsad"]:
                    if os.path.exists(path):
                        bluealsa_path = path
                        break
                
                if not bluealsa_path:
                    raise Exception("Binaire BlueALSA introuvable")
                
                # Démarrer BlueALSA avec uniquement a2dp-sink
                cmd = ["sudo", "env", "LIBASOUND_THREAD_SAFE=0", bluealsa_path, "-p", "a2dp-sink"]
                options = self.config.get("daemon_options", "--keep-alive=5 --initial-volume=80")
                if options:
                    cmd.extend(options.split())
                
                # Sortie vers des fichiers de log
                with open("/tmp/bluealsa_stdout.log", "w") as stdout, \
                    open("/tmp/bluealsa_stderr.log", "w") as stderr:
                    subprocess.Popen(cmd, stdout=stdout, stderr=stderr)
                
                self.logger.info(f"BlueALSA démarré manuellement: {' '.join(cmd)}")
                await asyncio.sleep(2)
                
                return True
            except Exception as e:
                self.logger.error(f"Erreur démarrage BlueALSA: {e}")
                return False
        
        # Si systemd n'est pas disponible, démarrer manuellement
        try:
            # Chercher le binaire
            bluealsa_path = None
            for path in ["/usr/bin/bluealsa", "/usr/bin/bluealsad"]:
                if os.path.exists(path):
                    bluealsa_path = path
                    break
            
            if not bluealsa_path:
                raise Exception("Binaire BlueALSA introuvable")
            
            # Démarrer BlueALSA manuellement avec uniquement a2dp-sink
            cmd = ["sudo", "env", "LIBASOUND_THREAD_SAFE=0", bluealsa_path, "-p", "a2dp-sink"]
            options = self.config.get("daemon_options", "--keep-alive=5 --initial-volume=80")
            if options:
                cmd.extend(options.split())
            
            # Sortie vers des fichiers de log
            with open("/tmp/bluealsa_stdout.log", "w") as stdout, \
                open("/tmp/bluealsa_stderr.log", "w") as stderr:
                subprocess.Popen(cmd, stdout=stdout, stderr=stderr)
            
            self.logger.info(f"BlueALSA démarré: {' '.join(cmd)}")
            await asyncio.sleep(2)
            
            # Vérifier via D-Bus
            dbus_check = subprocess.run(
                ["dbus-send", "--system", "--dest=org.freedesktop.DBus", 
                "--print-reply", "/org/freedesktop/DBus", 
                "org.freedesktop.DBus.ListNames"],
                capture_output=True, text=True
            )
            
            return "org.bluealsa" in dbus_check.stdout
            
        except Exception as e:
            self.logger.error(f"Erreur démarrage BlueALSA: {e}")
            return False
    
    async def stop(self) -> bool:
        """Arrête complètement les services Bluetooth"""
        try:
            self.logger.info("Arrêt du plugin Bluetooth")
            
            # 1. Arrêter la lecture audio
            if self.playback_process:
                try:
                    self.playback_process.terminate()
                    await asyncio.sleep(0.2)
                    if self.playback_process.poll() is None:
                        self.playback_process.kill()
                except Exception:
                    pass
                self.playback_process = None
            
            # 2. Désenregistrer l'agent
            if hasattr(self, 'agent') and self.agent:
                try:
                    await self.agent.unregister()
                    self.logger.info("Agent Bluetooth désenregistré")
                except Exception as e:
                    self.logger.warning(f"Erreur désenregistrement agent: {e}")
            
            # 3. Arrêter BlueALSA
            subprocess.run(["sudo", "pkill", "-f", "bluealsa-aplay"], check=False)
            subprocess.run(["sudo", "pkill", "-f", "blue(alsa|alsad)"], shell=True, check=False)
            await asyncio.sleep(1)
            
            # 4. Désactiver la découvrabilité et l'adaptateur
            try:
                subprocess.run(["sudo", "bluetoothctl", "discoverable", "off"], check=False)
                subprocess.run(["sudo", "hciconfig", "hci0", "down"], check=False)
                self.logger.info("Adaptateur désactivé")
            except:
                pass
            
            # 5. Arrêter complètement le service Bluetooth et désactiver son démarrage auto
            subprocess.run(["sudo", "systemctl", "stop", "bluetooth"], check=False)
            subprocess.run(["sudo", "systemctl", "disable", "bluetooth"], check=False)
            self.logger.info("Service Bluetooth arrêté et désactivé")
            
            # 6. Notifier l'état
            await self.notify_state_change(PluginState.INACTIVE)
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur arrêt Bluetooth: {e}")
            return False
    
    async def _handle_device_event(self, event_type: str, device: Dict[str, Any]) -> None:
        """Gère les connexions/déconnexions de périphériques"""
        try:
            address = device.get("address")
            name = device.get("name", "Appareil inconnu")
            
            self.logger.info(f"Événement périphérique: {event_type} pour {name} ({address})")
            
            if event_type == "connected":
                self.logger.info(f"Périphérique connecté: {name} ({address})")
                
                # Toujours considérer le périphérique comme compatible A2DP
                # La vérification via UUIDs n'est pas toujours fiable
                self.current_device = device
                self.current_device["a2dp_sink_support"] = True
                
                # Notifier le changement d'état
                await self.notify_state_change(PluginState.CONNECTED, {
                    "device_connected": True,
                    "device_name": name,
                    "device_address": address,
                })
                
                # Attendre un peu pour s'assurer que la connexion est stable
                # Attente plus longue pour les appareils iOS
                self.logger.info(f"Attente pour stabilisation de la connexion A2DP...")
                await asyncio.sleep(3)
                
                # Démarrer la lecture audio
                self.logger.info(f"Démarrage de la lecture audio pour {address}...")
                await self._start_audio_playback(address)
                
            elif event_type == "disconnected" and self.current_device and self.current_device.get("address") == address:
                self.logger.info(f"Périphérique déconnecté: {name}")
                
                # Arrêter la lecture
                if self.playback_process:
                    try:
                        self.playback_process.terminate()
                    except Exception:
                        pass
                    self.playback_process = None
                
                # Réinitialiser l'état
                self.current_device = None
                
                # Notification
                await self.notify_state_change(PluginState.READY, {
                    "device_connected": False
                })
        except Exception as e:
            self.logger.error(f"Erreur traitement événement: {e}")
    
    async def _start_audio_playback(self, device_address: str) -> None:
        """Démarre la lecture audio via bluealsa-aplay"""
        try:
            # Arrêter toute lecture en cours
            if self.playback_process:
                try:
                    self.playback_process.terminate()
                    await asyncio.sleep(0.5)
                except Exception:
                    pass
                self.playback_process = None
                
            # Vérifier disponibilité de bluealsa-aplay
            if not os.path.exists("/usr/bin/bluealsa-aplay"):
                self.logger.error("bluealsa-aplay non disponible - installation requise")
                return
            
            # Vérifier présence de l'adaptateur et du daemon BlueALSA
            check_prereqs = subprocess.run(["hciconfig", "hci0"], capture_output=True, text=True)
            if "UP RUNNING" not in check_prereqs.stdout:
                self.logger.error("L'adaptateur Bluetooth n'est pas actif - activation forcée...")
                subprocess.run(["sudo", "hciconfig", "hci0", "up"], check=False)
                await asyncio.sleep(1)
            
            # Vérifier les périphériques disponibles
            self.logger.info("Vérification des périphériques BlueALSA disponibles...")
            check_devices = subprocess.run(["bluealsa-aplay", "-L"], 
                                        capture_output=True, text=True)
            self.logger.info(f"Périphériques disponibles: {check_devices.stdout}")
            
            # Vérifier si notre périphérique est dans la liste
            if device_address not in check_devices.stdout:
                self.logger.warning(f"Périphérique {device_address} non trouvé dans la liste BlueALSA. Attente supplémentaire...")
                await asyncio.sleep(3)
                
                # Vérifier à nouveau
                check_devices = subprocess.run(["bluealsa-aplay", "-L"], 
                                            capture_output=True, text=True)
                self.logger.info(f"Périphériques disponibles après attente: {check_devices.stdout}")
                
                if device_address not in check_devices.stdout:
                    self.logger.error(f"Périphérique {device_address} introuvable dans BlueALSA même après attente")
                    # Continuer malgré tout, en cas de problème d'affichage du périphérique
            
            # Démarrer la nouvelle lecture avec options standard
            self.logger.info(f"Démarrage lecture pour {device_address}")
            
            # Tenter d'abord les options standard pour iOS
            self.playback_process = subprocess.Popen([
                "/usr/bin/bluealsa-aplay",
                "--profile-a2dp",
                device_address
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            # Vérifier le démarrage
            await asyncio.sleep(1)
            if self.playback_process.poll() is not None:
                stderr = self.playback_process.stderr.read().decode()
                stdout = self.playback_process.stdout.read().decode()
                self.logger.error(f"bluealsa-aplay a échoué: {stderr}")
                self.logger.info(f"Sortie standard: {stdout}")
                
                # Tentative de récupération avec options minimales
                self.logger.info("Tentative de récupération avec options minimales...")
                self.playback_process = subprocess.Popen([
                    "/usr/bin/bluealsa-aplay",
                    device_address
                ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                
                # Vérifier à nouveau
                await asyncio.sleep(1)
                if self.playback_process.poll() is not None:
                    stderr = self.playback_process.stderr.read().decode()
                    self.logger.error(f"Deuxième tentative échouée: {stderr}")
                    
                    # Dernière tentative avec toutes les options spéciales
                    self.logger.info("Dernière tentative avec options spéciales...")
                    self.playback_process = subprocess.Popen([
                        "/usr/bin/bluealsa-aplay",
                        "--pcm-buffer-time=2000000",  # Buffer très grand (2s)
                        "--pcm-period-time=100000",   # Période plus grande
                        "--profile-a2dp",
                        device_address
                    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            else:
                self.logger.info("Lecture audio démarrée avec succès")
                
        except Exception as e:
            self.logger.error(f"Erreur démarrage lecture: {e}")