"""
Plugin Bluetooth pour la gestion audio A2DP sur oakOS
"""
import asyncio
import logging
import subprocess
import os
import time
import json
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
        self.bluealsa_process = None
        self.bluetooth_conf_path = "/etc/bluetooth/main.conf"
        self.connection_retries = 3
        self.auto_agent_script = "/tmp/autobt_agent.py"
    
    async def get_status(self) -> Dict[str, Any]:
        """Récupère l'état actuel du plugin"""
        # Vérifier que BlueALSA est en cours d'exécution
        bluealsa_running = subprocess.run(
            ["systemctl", "is-active", "bluealsa.service"], 
            capture_output=True, text=True
        ).stdout.strip() == "active"
        
        # Vérifier que le processus de lecture est actif
        playback_running = self.playback_process and self.playback_process.poll() is None
        
        # Vérifier que le service bluetooth est actif
        bluetooth_running = subprocess.run(
            ["systemctl", "is-active", "bluetooth"], 
            capture_output=True, text=True
        ).stdout.strip() == "active"
        
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
            if command == "debug_info":
                # Collecte d'informations de diagnostic complètes
                debug_info = {}
                # Statut des services
                debug_info["bluealsa_status"] = subprocess.run(
                    ["systemctl", "status", "bluealsa.service"], 
                    capture_output=True, text=True
                ).stdout
                
                debug_info["bluetooth_status"] = subprocess.run(
                    ["systemctl", "status", "bluetooth.service"], 
                    capture_output=True, text=True
                ).stdout
                
                # Informations sur les périphériques
                debug_info["bluetoothctl_devices"] = subprocess.run(
                    ["bluetoothctl", "devices"], 
                    capture_output=True, text=True
                ).stdout
                
                # Périphériques connectés
                debug_info["connected_devices"] = subprocess.run(
                    ["bluetoothctl", "devices", "Connected"], 
                    capture_output=True, text=True
                ).stdout
                
                # Périphériques BlueALSA
                debug_info["bluealsa_devices"] = subprocess.run(
                    ["bluealsa-aplay", "-L"], 
                    capture_output=True, text=True
                ).stdout
                
                debug_info["bluealsa_list"] = subprocess.run(
                    ["bluealsa-aplay", "-l"], 
                    capture_output=True, text=True
                ).stdout
                
                # Info de l'adaptateur
                debug_info["adapter_info"] = subprocess.run(
                    ["hciconfig", "hci0"], 
                    capture_output=True, text=True
                ).stdout
                
                # Vérifier les processus
                debug_info["processes"] = subprocess.run(
                    ["ps", "aux", "|", "grep", "blue"], 
                    shell=True, capture_output=True, text=True
                ).stdout
                
                # Vérifier dbus
                debug_info["dbus_names"] = subprocess.run(
                    ["dbus-send", "--system", "--dest=org.freedesktop.DBus", 
                     "--print-reply", "/org/freedesktop/DBus", 
                     "org.freedesktop.DBus.ListNames"], 
                    capture_output=True, text=True
                ).stdout
                
                # Information sur le périphérique actuel
                if self.current_device and self.current_device.get("address"):
                    debug_info["current_device_info"] = subprocess.run(
                        ["bluetoothctl", "info", self.current_device.get("address")], 
                        capture_output=True, text=True
                    ).stdout
                
                return {
                    "success": True,
                    "debug_info": debug_info
                }
            
            elif command == "disconnect":
                if not self.current_device:
                    return {"success": False, "error": "Aucun périphérique connecté"}
                
                address = self.current_device.get("address")
                if not address:
                    return {"success": False, "error": "Adresse invalide"}
                
                subprocess.run(["bluetoothctl", "disconnect", address], check=False)
                success = await self.dbus_manager.disconnect_device(address)
                return {"success": success}
            
            elif command == "restart_services":
                await self.stop()
                success = await self.start()
                return {"success": success, "message": "Services Bluetooth redémarrés"}
            
            elif command == "restart_audio":
                if not self.current_device or not self.current_device.get("address"):
                    return {"success": False, "error": "Aucun périphérique connecté"}
                
                # Arrêter puis redémarrer la lecture audio
                if self.playback_process:
                    self.playback_process.terminate()
                    self.playback_process = None
                
                await asyncio.sleep(1)
                await self._start_audio_playback(self.current_device.get("address"))
                return {"success": True, "message": "Lecture audio redémarrée"}
            
            elif command == "force_reconnect":
                if not self.current_device or not self.current_device.get("address"):
                    return {"success": False, "error": "Aucun périphérique connecté"}
                
                address = self.current_device.get("address")
                self.logger.info(f"Tentative de reconnexion forcée pour {address}")
                
                # Déconnexion puis reconnexion
                subprocess.run(["bluetoothctl", "disconnect", address], check=False)
                await asyncio.sleep(2)
                subprocess.run(["bluetoothctl", "connect", address], check=False)
                await asyncio.sleep(3)
                
                # Redémarrer l'audio
                await self._start_audio_playback(address)
                return {"success": True, "message": "Reconnexion forcée effectuée"}
            
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
            
            # 2. Créer le script d'agent automatique
            await self._create_auto_agent_script()
            
            # 3. S'assurer que bluetooth est COMPLÈTEMENT arrêté au démarrage
            subprocess.run(["sudo", "systemctl", "stop", "bluetooth"], check=False)
            await asyncio.sleep(1)
            
            # 3.1 Désactiver également le démarrage automatique
            subprocess.run(["sudo", "systemctl", "disable", "bluetooth"], check=False)
            
            # 3.2 Vérifier que l'adaptateur est désactivé physiquement
            try:
                subprocess.run(["sudo", "hciconfig", "hci0", "down"], check=False)
            except:
                pass
            
            # 4. Initialiser les gestionnaires D-Bus avec prudence (sans activer bluetooth)
            self.dbus_manager = BluetoothDBusManager()
            await self.dbus_manager.initialize()
            
            # 5. Initialiser ALSA et agent (sans les démarrer)
            self.alsa_manager = AlsaManager()
            self.agent = BluetoothAgent(self.dbus_manager.system_bus)
            
            # 6. Callbacks pour les événements
            self.dbus_manager.register_device_callback(self._handle_device_event)
            
            # 7. Vérifier encore une fois que bluetooth est bien arrêté
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
    
    async def _create_auto_agent_script(self) -> None:
        """Crée un script d'agent Bluetooth automatique qui accepte toutes les demandes"""
        script_content = """#!/usr/bin/env python3
import sys
import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib

class AutoAgent(dbus.service.Object):
    AGENT_PATH = "/oakos/bluetooth/agent"
    AGENT_INTERFACE = "org.bluez.Agent1"
    
    def __init__(self, bus, path=AGENT_PATH):
        super(AutoAgent, self).__init__(bus, path)
        
    @dbus.service.method(AGENT_INTERFACE, in_signature="os", out_signature="")
    def AuthorizeService(self, device, uuid):
        print(f"AutoAgent: Authorizing service {uuid} for device {device}")
        return
        
    @dbus.service.method(AGENT_INTERFACE, in_signature="o", out_signature="s")
    def RequestPinCode(self, device):
        print(f"AutoAgent: PIN code request for {device}")
        return "0000"
        
    @dbus.service.method(AGENT_INTERFACE, in_signature="o", out_signature="u")
    def RequestPasskey(self, device):
        print(f"AutoAgent: Passkey request for {device}")
        return dbus.UInt32(0)
        
    @dbus.service.method(AGENT_INTERFACE, in_signature="ouq", out_signature="")
    def DisplayPasskey(self, device, passkey, entered):
        print(f"AutoAgent: Display passkey {passkey} for device {device}")
        
    @dbus.service.method(AGENT_INTERFACE, in_signature="os", out_signature="")
    def DisplayPinCode(self, device, pincode):
        print(f"AutoAgent: Display PIN code {pincode} for device {device}")
        
    @dbus.service.method(AGENT_INTERFACE, in_signature="ou", out_signature="")
    def RequestConfirmation(self, device, passkey):
        print(f"AutoAgent: Confirming passkey {passkey} for device {device}")
        return
        
    @dbus.service.method(AGENT_INTERFACE, in_signature="o", out_signature="")
    def RequestAuthorization(self, device):
        print(f"AutoAgent: Authorizing device {device}")
        return
        
    @dbus.service.method(AGENT_INTERFACE, in_signature="", out_signature="")
    def Cancel(self):
        print("AutoAgent: Request canceled")

def main():
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    
    # Create and register agent
    agent = AutoAgent(bus)
    
    # Get the manager object
    manager = dbus.Interface(
        bus.get_object("org.bluez", "/org/bluez"),
        "org.bluez.AgentManager1"
    )
    
    try:
        manager.UnregisterAgent(AutoAgent.AGENT_PATH)
    except:
        pass
    
    # Register agent with "NoInputNoOutput" capability
    manager.RegisterAgent(AutoAgent.AGENT_PATH, "NoInputNoOutput")
    print("Agent registered with capability NoInputNoOutput")
    
    # Set as default agent
    manager.RequestDefaultAgent(AutoAgent.AGENT_PATH)
    print("Agent set as default")
    
    # Run the main loop
    mainloop = GLib.MainLoop()
    
    try:
        mainloop.run()
    except KeyboardInterrupt:
        mainloop.quit()
        manager.UnregisterAgent(AutoAgent.AGENT_PATH)
        print("Agent unregistered")

if __name__ == "__main__":
    main()
"""
        
        with open(self.auto_agent_script, "w") as f:
            f.write(script_content)
        
        os.chmod(self.auto_agent_script, 0o755)
        self.logger.info(f"Script d'agent automatique créé: {self.auto_agent_script}")
    
    async def _configure_bluetooth_conf(self) -> None:
        """Configure le fichier de configuration Bluetooth pour tous les appareils"""
        config_content = """
# Configuration Bluetooth pour oakOS
[General]
Name = oakOS
# Configuration pour un haut-parleur audio A2DP
Class = 0x240414
DiscoverableTimeout = 0
PairableTimeout = 0
AlwaysPairable = true
Privacy = off
FastConnectable = true
# Pour faciliter l'appairage sans code pin
JustWorksRepairing = always
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
            
            # 1. Réactiver et démarrer le service bluetooth
            subprocess.run(["sudo", "systemctl", "enable", "bluetooth"], check=False)
            subprocess.run(["sudo", "systemctl", "start", "bluetooth"], check=True)
            await asyncio.sleep(2)  # Attendre le démarrage du service
            
            # 2. Activer et configurer l'adaptateur
            await self._activate_adapter()
            
            # 3. S'assurer que bluealsa.service est actif avec la bonne configuration
            await self._ensure_bluealsa_active()
            
            # 4. Démarrer l'agent automatique qui accepte toutes les demandes
            await self._start_auto_agent()
            
            # 5. Notifier l'état
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
    
    async def _start_auto_agent(self) -> None:
        """Démarre l'agent automatique qui accepte toutes les demandes"""
        try:
            # Arrêter tout agent existant
            subprocess.run(["pkill", "-f", self.auto_agent_script], check=False)
            
            # Démarrer l'agent automatique
            self.logger.info("Démarrage de l'agent automatique...")
            
            with open("/tmp/agent_log.txt", "w") as log:
                subprocess.Popen(
                    ["python3", self.auto_agent_script],
                    stdout=log,
                    stderr=log,
                    start_new_session=True
                )
            
            # Attendre que l'agent soit prêt
            await asyncio.sleep(2)
            
            # Vérifier que l'agent est en cours d'exécution
            check = subprocess.run(["pgrep", "-f", self.auto_agent_script], capture_output=True)
            if check.returncode != 0:
                self.logger.warning("L'agent automatique n'a pas démarré correctement")
                with open("/tmp/agent_log.txt", "r") as f:
                    log = f.read()
                    self.logger.warning(f"Log de l'agent: {log}")
                
                # Fallback: utiliser l'agent standard
                self.logger.info("Utilisation de l'agent standard comme fallback")
                if self.agent:
                    await self.agent.register()
                    self.logger.info("Agent Bluetooth standard enregistré")
            else:
                self.logger.info("Agent automatique démarré avec succès")
        
        except Exception as e:
            self.logger.error(f"Erreur démarrage agent: {e}")
            # Fallback: utiliser l'agent standard
            if self.agent:
                await self.agent.register()
                self.logger.info("Agent Bluetooth standard enregistré en fallback")
        
    async def _activate_adapter(self) -> None:
        """Active et configure l'adaptateur Bluetooth pour tous types d'appareils"""
        try:
            # Désactiver puis réactiver l'adaptateur pour un état propre
            subprocess.run(["sudo", "hciconfig", "hci0", "down"], check=False)
            await asyncio.sleep(1)
            subprocess.run(["sudo", "hciconfig", "hci0", "up"], check=False)
            await asyncio.sleep(1)
            
            # Configurer comme haut-parleur audio (classe 0x240414)
            subprocess.run(["sudo", "hciconfig", "hci0", "class", "0x240414"], check=True)
            subprocess.run(["sudo", "hciconfig", "hci0", "name", "oakOS"], check=True)
            await asyncio.sleep(1)
            
            # Activer les fonctionnalités de découverte et d'appairage
            subprocess.run(["sudo", "bluetoothctl", "discoverable", "on"], check=False)
            subprocess.run(["sudo", "bluetoothctl", "pairable", "on"], check=False)
            subprocess.run(["sudo", "bluetoothctl", "discoverable-timeout", "0"], check=False)
            
            # Configuration supplémentaire pour faciliter la connexion automatique
            subprocess.run(["sudo", "btmgmt", "connectable", "on"], check=False)
            subprocess.run(["sudo", "btmgmt", "advertising", "on"], check=False)
            
            # Vérification des paramètres
            adapter_info = subprocess.run(
                ["hciconfig", "hci0"], 
                capture_output=True, text=True
            ).stdout
            
            # Vérifier que l'adaptateur est bien configuré comme audio
            if "Class: 0x240414" in adapter_info:
                self.logger.info(f"Adaptateur correctement configuré comme périphérique audio")
            else:
                self.logger.warning(f"La classe de l'adaptateur n'est pas correcte: {adapter_info}")
                # Seconde tentative avec autre méthode
                subprocess.run(["sudo", "hciconfig", "hci0", "reset"], check=False)
                await asyncio.sleep(1)
                subprocess.run(["sudo", "hciconfig", "hci0", "class", "0x240414"], check=True)
            
            # Vérifier la configuration finale
            result = subprocess.run(
                ["sudo", "hciconfig", "hci0"], 
                capture_output=True, text=True
            )
            self.logger.info(f"Configuration finale de l'adaptateur: {result.stdout.strip()}")
            
        except Exception as e:
            self.logger.warning(f"Erreur activation adaptateur: {e}")
    
    async def _ensure_bluealsa_active(self) -> bool:
        """S'assure que le service bluealsa est actif et correctement configuré"""
        try:
            # Vérifier si le service est masqué, si oui le démasquer
            mask_check = subprocess.run(
                ["systemctl", "is-enabled", "bluealsa.service"], 
                capture_output=True, text=True
            )
            if "masked" in mask_check.stdout:
                self.logger.info("Démasquage du service bluealsa...")
                subprocess.run(["sudo", "systemctl", "unmask", "bluealsa.service"], check=False)
                await asyncio.sleep(1)
            
            # Vérifier les options du service
            service_content = subprocess.run(
                ["systemctl", "cat", "bluealsa.service"], 
                capture_output=True, text=True
            ).stdout
            
            # Si le service est configuré pour a2dp-source, le reconfigurer uniquement pour a2dp-sink
            if "-p a2dp-source" in service_content and "-p a2dp-sink" in service_content:
                self.logger.info("BlueALSA configuré pour les deux modes a2dp-source et a2dp-sink")
                self.logger.info("Arrêt du service BlueALSA standard pour une configuration personnalisée")
                
                # Arrêter le service standard
                subprocess.run(["sudo", "systemctl", "stop", "bluealsa.service"], check=False)
                subprocess.run(["sudo", "pkill", "-f", "bluealsa"], check=False)
                await asyncio.sleep(1)
                
                # Démarrer BlueALSA manuellement en mode sink uniquement
                self.logger.info("Démarrage de BlueALSA en mode sink uniquement")
                cmd = [
                    "sudo", 
                    "env", 
                    "LIBASOUND_THREAD_SAFE=0", 
                    "/usr/bin/bluealsa", 
                    "-p", "a2dp-sink", 
                    "--keep-alive=10", 
                    "--initial-volume=80"
                ]
                
                with open("/tmp/bluealsa_stdout.log", "w") as stdout, \
                    open("/tmp/bluealsa_stderr.log", "w") as stderr:
                    self.bluealsa_process = subprocess.Popen(
                        cmd,
                        stdout=stdout, 
                        stderr=stderr
                    )
                
                self.logger.info(f"BlueALSA démarré manuellement: {' '.join(cmd)}")
                await asyncio.sleep(2)
                
                # Vérifier que le processus est en cours d'exécution
                if self.bluealsa_process.poll() is not None:
                    self.logger.error("Erreur: BlueALSA s'est arrêté immédiatement")
                    with open("/tmp/bluealsa_stderr.log", "r") as f:
                        stderr = f.read()
                        self.logger.error(f"Erreur BlueALSA: {stderr}")
                    return False
                
                self.logger.info("BlueALSA personnalisé démarré avec succès")
                
            else:
                # Utiliser le service standard mais s'assurer qu'il est actif
                status = subprocess.run(
                    ["systemctl", "is-active", "bluealsa.service"], 
                    capture_output=True, text=True
                )
                
                if status.stdout.strip() != "active":
                    self.logger.info("Démarrage du service bluealsa...")
                    subprocess.run(["sudo", "systemctl", "enable", "bluealsa.service"], check=False)
                    subprocess.run(["sudo", "systemctl", "restart", "bluealsa.service"], check=False)
                    await asyncio.sleep(2)
                
                # Vérifier à nouveau
                status = subprocess.run(
                    ["systemctl", "is-active", "bluealsa.service"], 
                    capture_output=True, text=True
                )
                if status.stdout.strip() != "active":
                    self.logger.error("Impossible de démarrer bluealsa.service")
                    return False
            
            # Vérifier le service D-Bus BlueALSA
            dbus_check = subprocess.run(
                ["dbus-send", "--system", "--print-reply", "--dest=org.freedesktop.DBus", 
                "/org/freedesktop/DBus", "org.freedesktop.DBus.ListNames"],
                capture_output=True, text=True
            )
            
            if "org.bluealsa" in dbus_check.stdout:
                self.logger.info("Service D-Bus BlueALSA détecté")
                return True
            else:
                self.logger.error("Service D-Bus BlueALSA non détecté malgré le démarrage du service")
                return False
                
        except Exception as e:
            self.logger.error(f"Erreur vérification bluealsa: {e}")
            return False
    
    async def stop(self) -> bool:
        """Arrête le service Bluetooth mais laisse BlueALSA actif"""
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
            
            # 2. Arrêter l'agent automatique
            subprocess.run(["pkill", "-f", self.auto_agent_script], check=False)
            
            # 3. Désenregistrer l'agent standard si présent
            if hasattr(self, 'agent') and self.agent:
                try:
                    await self.agent.unregister()
                    self.logger.info("Agent Bluetooth désenregistré")
                except Exception as e:
                    self.logger.warning(f"Erreur désenregistrement agent: {e}")
            
            # 4. Arrêter les processus de lecture potentiels
            subprocess.run(["sudo", "pkill", "-f", "bluealsa-aplay"], check=False)
            
            # 5. Arrêter BlueALSA personnalisé si actif
            if self.bluealsa_process and self.bluealsa_process.poll() is None:
                try:
                    self.bluealsa_process.terminate()
                    await asyncio.sleep(0.5)
                    if self.bluealsa_process.poll() is None:
                        self.bluealsa_process.kill()
                    self.bluealsa_process = None
                    self.logger.info("BlueALSA personnalisé arrêté")
                except Exception as e:
                    self.logger.warning(f"Erreur arrêt BlueALSA personnalisé: {e}")
            
            # 6. Désactiver la découvrabilité et l'adaptateur
            try:
                subprocess.run(["sudo", "bluetoothctl", "discoverable", "off"], check=False)
                subprocess.run(["sudo", "hciconfig", "hci0", "down"], check=False)
                self.logger.info("Adaptateur désactivé")
            except:
                pass
            
            # 7. Arrêter complètement le service Bluetooth et désactiver son démarrage auto
            subprocess.run(["sudo", "systemctl", "stop", "bluetooth"], check=False)
            subprocess.run(["sudo", "systemctl", "disable", "bluetooth"], check=False)
            self.logger.info("Service Bluetooth arrêté et désactivé")
            
            # 8. Notifier l'état
            await self.notify_state_change(PluginState.INACTIVE)
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur arrêt Bluetooth: {e}")
            return False
    
    async def _handle_device_event(self, event_type: str, device: Dict[str, Any]) -> None:
        """Gère les connexions/déconnexions pour tous types de périphériques Bluetooth"""
        try:
            address = device.get("address")
            name = device.get("name", "Appareil inconnu")
            
            self.logger.info(f"Événement périphérique: {event_type} pour {name} ({address})")
            
            if event_type == "connected":
                self.logger.info(f"Périphérique connecté: {name} ({address})")
                
                # Considérer le périphérique comme A2DP par défaut
                self.current_device = device
                self.current_device["a2dp_sink_support"] = True
                
                # Enregistrer l'appareil connecté pour diagnostic
                with open("/tmp/last_connected_bt_device.json", "w") as f:
                    json.dump(device, f, indent=2)
                
                # Trust automatiquement l'appareil
                subprocess.run(["bluetoothctl", "trust", address], check=False)
                
                # Notifier le changement d'état
                await self.notify_state_change(PluginState.CONNECTED, {
                    "device_connected": True,
                    "device_name": name,
                    "device_address": address,
                })
                
                # Attendre pour stabilisation de la connexion
                self.logger.info(f"Attente pour stabilisation de la connexion A2DP...")
                await asyncio.sleep(3)
                
                # Vérifier si le profil A2DP est disponible
                device_info = subprocess.run(
                    ["bluetoothctl", "info", address], 
                    capture_output=True, text=True
                ).stdout
                
                # Démarrer la lecture audio
                self.logger.info(f"Démarrage lecture audio pour {address}...")
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
        """Démarre la lecture audio via bluealsa-aplay pour tout type d'appareil"""
        try:
            # Arrêter toute lecture en cours
            if self.playback_process:
                try:
                    self.playback_process.terminate()
                    await asyncio.sleep(0.5)
                except Exception:
                    pass
                self.playback_process = None
            
            # Vérifier que l'appareil est bien connecté
            connected_devices = subprocess.run(
                ["bluetoothctl", "devices", "Connected"], 
                capture_output=True, text=True
            ).stdout
            
            if device_address not in connected_devices:
                self.logger.warning(f"Appareil {device_address} non connecté, tentative de reconnexion...")
                subprocess.run(["bluetoothctl", "connect", device_address], check=False)
                await asyncio.sleep(3)
            
            # Attendre que BlueALSA détecte l'appareil
            self.logger.info("Attente que BlueALSA détecte l'appareil...")
            for attempt in range(5):
                # Lister les périphériques BlueALSA
                list_devices = subprocess.run(
                    ["bluealsa-aplay", "-L"], 
                    capture_output=True, text=True
                )
                self.logger.info(f"Tentative {attempt+1}/5 - Périphériques BlueALSA:\n{list_devices.stdout}")
                
                if device_address.lower() in list_devices.stdout.lower():
                    self.logger.info(f"Appareil {device_address} détecté par BlueALSA!")
                    break
                
                # Attendre et réessayer
                await asyncio.sleep(2)
            
            # Démarrer la lecture avec logs détaillés
            log_file = "/tmp/bluealsa_aplay.log"
            with open(log_file, "w") as f:
                f.write(f"=== Tentative de lecture le {time.strftime('%Y-%m-%d à %H:%M:%S')} ===\n")
                f.write(f"Appareil: {device_address}\n\n")
            
            # Première tentative avec profil A2DP explicite
            cmd = [
                "/usr/bin/bluealsa-aplay",
                "--verbose",
                "--profile-a2dp",
                device_address
            ]
            
            self.logger.info(f"Démarrage lecture: {' '.join(cmd)}")
            with open(log_file, "a") as log:
                log.write(f"Commande: {' '.join(cmd)}\n")
                self.playback_process = subprocess.Popen(
                    cmd, 
                    stdout=log, 
                    stderr=log
                )
            
            # Vérifier si le processus a démarré
            await asyncio.sleep(2)
            
            if self.playback_process.poll() is not None:
                self.logger.warning(f"Échec première tentative. Tentative sans option de profil...")
                
                # Deuxième tentative sans option de profil
                cmd = [
                    "/usr/bin/bluealsa-aplay",
                    "--verbose",
                    device_address
                ]
                
                with open(log_file, "a") as log:
                    log.write(f"\nDeuxième tentative: {' '.join(cmd)}\n")
                    self.playback_process = subprocess.Popen(
                        cmd, 
                        stdout=log, 
                        stderr=log
                    )
                
                # Vérifier si le processus a démarré
                await asyncio.sleep(2)
                
                if self.playback_process.poll() is not None:
                    self.logger.warning(f"Échec deuxième tentative. Tentative avec buffer étendu...")
                    
                    # Troisième tentative avec buffer étendu
                    cmd = [
                        "/usr/bin/bluealsa-aplay",
                        "--verbose",
                        "--pcm-buffer-time=2000000",
                        "--pcm-period-time=100000",
                        device_address
                    ]
                    
                    with open(log_file, "a") as log:
                        log.write(f"\nTroisième tentative: {' '.join(cmd)}\n")
                        self.playback_process = subprocess.Popen(
                            cmd, 
                            stdout=log, 
                            stderr=log
                        )
                    
                    # Vérifier le résultat final
                    await asyncio.sleep(2)
                    if self.playback_process.poll() is not None:
                        self.logger.error(f"Échec de toutes les tentatives de lecture pour {device_address}")
                        with open(log_file, "r") as log:
                            log_content = log.read()
                            self.logger.error(f"Log des tentatives:\n{log_content}")
                    else:
                        self.logger.info(f"Troisième tentative réussie pour {device_address}")
                else:
                    self.logger.info(f"Deuxième tentative réussie pour {device_address}")
            else:
                self.logger.info(f"Première tentative réussie pour {device_address}")
            
        except Exception as e:
            self.logger.error(f"Erreur lors du démarrage de la lecture audio: {e}")
            import traceback
            self.logger.error(traceback.format_exc())