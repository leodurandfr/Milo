"""
Plugin Bluetooth minimaliste pour oakOS - Version avec systemd
"""
import asyncio
import logging
import subprocess
import threading
from typing import Dict, Any, List

import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib

from backend.application.event_bus import EventBus
from backend.infrastructure.plugins.base import UnifiedAudioPlugin
from backend.domain.audio_state import PluginState
from backend.infrastructure.plugins.bluetooth.agent import BluetoothAgent


class BluetoothPlugin(UnifiedAudioPlugin):
    """Plugin Bluetooth pour la réception audio via A2DP"""
    
    def __init__(self, event_bus: EventBus, config: Dict[str, Any]):
        super().__init__(event_bus, "bluetooth")
        self.config = config
        self.current_device = None
        self._initialized = False
        
        # Agent Bluetooth et D-Bus
        self.agent = None
        self.agent_thread = None
        self.mainloop = None
        self.system_bus = None
        self.signal_match = None
        self.bluealsa_signal_match = None
        
        # Configuration
        self.stop_bluetooth = config.get("stop_bluetooth_on_exit", True)
    
    async def initialize(self) -> bool:
        """Initialisation simple"""
        self.logger.info("Initialisation du plugin Bluetooth")
        self._initialized = True
        return True
    
    def _run_agent_thread(self):
        """Exécute l'agent Bluetooth dans un thread séparé"""
        try:
            # Configuration D-Bus mainloop
            dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
            self.system_bus = dbus.SystemBus()
            
            # Créer et enregistrer l'agent (avec un chemin unique)
            self.agent = BluetoothAgent(self.system_bus)
            self.mainloop = GLib.MainLoop()
            
            # Configurer les événements D-Bus pour la détection d'appareils et de PCMs
            self._setup_device_signals()
            self._setup_bluealsa_signals()
            
            # Enregistrer l'agent
            if self.agent.register_sync():
                self.logger.info(f"Agent Bluetooth enregistré et prêt avec le chemin {self.agent.agent_path}")
            else:
                self.logger.warning("Échec de l'enregistrement de l'agent Bluetooth")
                    
            # Exécuter le mainloop
            self.mainloop.run()
            
        except Exception as e:
            self.logger.error(f"Erreur thread agent: {e}")
    
    def _setup_device_signals(self):
        """Configuration des événements D-Bus pour détecter les connexions Bluetooth"""
        try:
            # S'abonner aux changements de propriétés des périphériques
            self.signal_match = self.system_bus.add_signal_receiver(
                self._device_property_changed,
                dbus_interface="org.freedesktop.DBus.Properties",
                signal_name="PropertiesChanged",
                arg0="org.bluez.Device1",
                path_keyword="path"
            )
            self.logger.info("Événements D-Bus BlueZ configurés avec succès")
        except Exception as e:
            self.logger.error(f"Erreur configuration événements D-Bus BlueZ: {e}")
    
    def _setup_bluealsa_signals(self):
        """Configuration des événements D-Bus pour détecter les PCMs BlueALSA"""
        try:
            # S'abonner aux événements InterfacesAdded pour détecter les nouveaux PCMs
            self.bluealsa_signal_match = self.system_bus.add_signal_receiver(
                self._bluealsa_interfaces_added,
                dbus_interface="org.freedesktop.DBus.ObjectManager", 
                signal_name="InterfacesAdded"
            )
            
            # Vérifier immédiatement les PCMs disponibles
            self._check_existing_pcms()
            
            self.logger.info("Événements D-Bus BlueALSA configurés avec succès")
        except Exception as e:
            self.logger.error(f"Erreur configuration événements D-Bus BlueALSA: {e}")
    
    def _check_existing_pcms(self):
        """Vérifie les PCMs BlueALSA existants au démarrage"""
        try:
            # Interroger BlueALSA pour obtenir tous les objets gérés
            bluealsa_obj = self.system_bus.get_object("org.bluealsa", "/")
            object_manager = dbus.Interface(bluealsa_obj, "org.freedesktop.DBus.ObjectManager")
            
            # Récupérer tous les objets gérés
            managed_objects = object_manager.GetManagedObjects()
            
            # Rechercher les PCMs
            for path, interfaces in managed_objects.items():
                if "org.bluealsa.PCM1" in interfaces:
                    # Traiter ce PCM comme s'il venait d'être ajouté
                    self._process_pcm(path, interfaces["org.bluealsa.PCM1"])
                    
        except dbus.exceptions.DBusException as e:
            # Si BlueALSA n'est pas encore démarré, c'est normal
            self.logger.debug(f"BlueALSA n'est pas encore disponible: {e}")
        except Exception as e:
            self.logger.error(f"Erreur vérification PCMs existants: {e}")
    
    def _bluealsa_interfaces_added(self, object_path, interfaces):
        """Callback lorsqu'une nouvelle interface est ajoutée à BlueALSA"""
        try:
            # Vérifier si c'est un PCM
            if "org.bluealsa.PCM1" in interfaces:
                self.logger.info(f"Nouveau PCM détecté: {object_path}")
                self._process_pcm(object_path, interfaces["org.bluealsa.PCM1"])
        except Exception as e:
            self.logger.error(f"Erreur traitement du nouveau PCM: {e}")
    
    def _process_pcm(self, path, properties):
        """Traite un PCM BlueALSA"""
        try:
            # Extraire l'adresse du périphérique
            device_path = str(properties.get("Device", ""))
            
            if not device_path:
                self.logger.warning(f"PCM sans chemin de périphérique: {path}")
                return
                
            # Vérifier si c'est un PCM de type source (audio provenant du périphérique Bluetooth)
            transport = str(properties.get("Transport", ""))
            mode = str(properties.get("Mode", ""))
            codec = str(properties.get("Codec", ""))
            
            self.logger.info(f"PCM trouvé: {path}, Transport: {transport}, Mode: {mode}, Codec: {codec}")
            
            # A2DP-sink signifie que c'est un périphérique qui envoie de l'audio à notre système
            if transport.startswith("A2DP") and mode == "source":
                # Extraire l'adresse Bluetooth
                device_parts = device_path.split('/')
                if len(device_parts) > 0:
                    last_part = device_parts[-1]
                    
                    # Format typique: dev_XX_XX_XX_XX_XX_XX
                    if last_part.startswith("dev_"):
                        address = last_part[4:].replace("_", ":")
                        
                        # Obtenir le nom du périphérique
                        name = self._get_device_name(device_path)
                        
                        # Si nous n'avons pas d'appareil actif, connecter celui-ci
                        if self.current_device is None:
                            # Créer une fonction pour exécuter la connexion asynchrone
                            def connect_device():
                                loop = asyncio.new_event_loop()
                                asyncio.set_event_loop(loop)
                                try:
                                    loop.run_until_complete(self._connect_device(address, name))
                                finally:
                                    loop.close()
                            
                            threading.Thread(target=connect_device, daemon=True).start()
        except Exception as e:
            self.logger.error(f"Erreur traitement PCM: {e}")
    
    def _get_device_name(self, device_path):
        """Obtient le nom d'un périphérique Bluetooth à partir de son chemin"""
        try:
            device_obj = self.system_bus.get_object("org.bluez", device_path)
            props = dbus.Interface(device_obj, "org.freedesktop.DBus.Properties")
            return str(props.Get("org.bluez.Device1", "Name"))
        except:
            return "Appareil inconnu"
    
    def _device_property_changed(self, interface, changed, invalidated, path):
        """Callback pour les changements de propriétés D-Bus (connexion/déconnexion)"""
        if "Connected" not in changed:
            return
            
        try:
            is_connected = changed["Connected"]
            
            # Obtenir les informations du périphérique
            device_obj = self.system_bus.get_object("org.bluez", path)
            props = dbus.Interface(device_obj, "org.freedesktop.DBus.Properties")
            
            address = str(props.Get("org.bluez.Device1", "Address"))
            try:
                name = str(props.Get("org.bluez.Device1", "Name"))
            except:
                name = "Appareil inconnu"
            
            if is_connected:
                # Appareil connecté - BlueALSA nous notifiera quand le PCM sera prêt
                self.logger.info(f"Appareil connecté: {name} ({address})")
                
                # Pas besoin de démarrer une surveillance ici, nous utilisons l'API D-Bus
            else:
                # Appareil déconnecté
                self.logger.info(f"Appareil déconnecté: {name} ({address})")
                
                # Gérer la déconnexion seulement si c'est l'appareil actuel
                if self.current_device and self.current_device.get("address") == address:
                    def handle_disconnect():
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        try:
                            loop.run_until_complete(self._handle_device_disconnected(address, name))
                        finally:
                            loop.close()
                    
                    threading.Thread(target=handle_disconnect, daemon=True).start()
                
        except Exception as e:
            self.logger.error(f"Erreur traitement événement: {e}")
    
    async def _connect_device(self, address: str, name: str) -> bool:
        """Connecte un appareil et démarre la lecture audio"""
        try:
            self.logger.info(f"Connexion de l'appareil {name} ({address})")
            
            # Enregistrer l'appareil
            self.current_device = {
                "address": address,
                "name": name
            }
            
            # Démarrer la lecture audio
            await self._start_audio_playback(address)
            
            # Notifier le changement d'état
            await self.notify_state_change(
                PluginState.CONNECTED, 
                {
                    "device_connected": True,
                    "device_name": name,
                    "device_address": address
                }
            )
            
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur connexion appareil: {e}")
            self.current_device = None
            return False
    
    async def _handle_device_disconnected(self, address: str, name: str) -> None:
        """Gère la déconnexion d'un appareil"""
        try:
            self.logger.info(f"Déconnexion de l'appareil {name} ({address})")
            
            # Arrêter la lecture audio
            await self._stop_audio_playback()
            
            # Réinitialiser l'état
            self.current_device = None
            
            # Notifier le changement d'état
            await self.notify_state_change(
                PluginState.READY, 
                {"device_connected": False}
            )
            
        except Exception as e:
            self.logger.error(f"Erreur déconnexion appareil: {e}")
    
    async def _stop_audio_playback(self):
        """Arrête la lecture audio en cours en utilisant systemd"""
        try:
            # Utiliser systemctl pour arrêter le service bluealsa-aplay
            if self.current_device:
                address = self.current_device.get("address")
                if address:
                    # Format spécial pour systemd (remplacer : par _)
                    systemd_address = address.replace(":", "_")
                    cmd = ["sudo", "systemctl", "stop", f"bluealsa-aplay@{address}.service"]
                    self.logger.info(f"Arrêt du service: {cmd}")
                    subprocess.run(cmd, check=True)
                    self.logger.info(f"Service bluealsa-aplay arrêté pour {address}")
        except Exception as e:
            self.logger.error(f"Erreur arrêt audio: {e}")
    
    async def _start_bluetooth_agent(self):
        """Démarre l'agent Bluetooth et les événements D-Bus"""
        try:
            # Arrêter tout agent précédent
            if self.agent_thread and self.agent_thread.is_alive():
                if self.mainloop and hasattr(self.mainloop, 'is_running') and self.mainloop.is_running():
                    GLib.idle_add(self.mainloop.quit)
                self.agent_thread.join(1)
            
            # Démarrer le thread de l'agent
            self.agent_thread = threading.Thread(target=self._run_agent_thread, daemon=True)
            self.agent_thread.start()
            
            # Attendre que le thread démarre
            await asyncio.sleep(1)
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur démarrage agent: {e}")
            return False
    
    async def _start_audio_playback(self, device_address: str) -> None:
        """Démarre la lecture audio pour un appareil en utilisant systemd"""
        try:
            # Arrêter toute lecture existante
            await self._stop_audio_playback()
            
            # Démarrer bluealsa-aplay via systemd
            self.logger.info(f"Démarrage du service bluealsa-aplay pour {device_address}")
            cmd = ["sudo", "systemctl", "start", f"bluealsa-aplay@{device_address}.service"]
            subprocess.run(cmd, check=True)
            self.logger.info(f"Service bluealsa-aplay démarré pour {device_address}")
            
            # Vérifier que le service a démarré correctement
            await asyncio.sleep(1)
            cmd = ["systemctl", "is-active", f"bluealsa-aplay@{device_address}.service"]
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.stdout.strip() != "active":
                self.logger.error(f"Le service bluealsa-aplay n'a pas démarré correctement: {result.stdout.strip()}")
                return
            
            self.logger.info("Lecture audio démarrée avec succès")
            
        except Exception as e:
            self.logger.error(f"Erreur démarrage lecture: {e}")
    
    async def start(self) -> bool:
        """Démarre les services nécessaires via systemd"""
        try:
            self.logger.info("Démarrage du plugin Bluetooth")
            
            # 1. Démarrer et configurer Bluetooth
            bt_status = subprocess.run(
                ["systemctl", "is-active", "bluetooth"], 
                capture_output=True, text=True
            ).stdout.strip()
            
            if bt_status != "active":
                self.logger.info("Démarrage du service bluetooth")
                subprocess.run(["sudo", "systemctl", "start", "bluetooth"], check=True)
                await asyncio.sleep(2)
            
            # Configurer l'adaptateur bluetooth
            subprocess.run(["sudo", "hciconfig", "hci0", "class", "0x240404"], check=False)
            subprocess.run(["sudo", "hciconfig", "hci0", "name", "oakOS"], check=False)
            subprocess.run(["sudo", "bluetoothctl", "discoverable", "on"], check=False)
            subprocess.run(["sudo", "bluetoothctl", "pairable", "on"], check=False)
            subprocess.run(["sudo", "bluetoothctl", "discoverable-timeout", "0"], check=False)
            
            # 2. Démarrer BlueALSA via systemd
            self.logger.info("Démarrage du service BlueALSA via systemd")
            subprocess.run(["sudo", "systemctl", "start", "bluealsa.service"], check=True)
            
            # Vérifier que le service a démarré correctement
            await asyncio.sleep(2)
            bluealsa_status = subprocess.run(
                ["systemctl", "is-active", "bluealsa.service"],
                capture_output=True, text=True
            ).stdout.strip()
            
            if bluealsa_status != "active":
                raise RuntimeError("Le service BlueALSA n'a pas pu démarrer")
            
            # 3. Démarrer l'agent Bluetooth
            await self._start_bluetooth_agent()
            
            # 4. Notifier l'état prêt
            await self.notify_state_change(PluginState.READY)
            
            self.logger.info("Plugin Bluetooth démarré avec succès")
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur démarrage Bluetooth: {e}")
            await self.notify_state_change(PluginState.ERROR, {"error": str(e)})
            return False
    
    async def stop(self) -> bool:
        """Arrête tous les services via systemd"""
        try:
            self.logger.info("Arrêt du plugin Bluetooth")
            
            # 1. Arrêter l'audio
            await self._stop_audio_playback()
            
            # 2. Supprimer les événements D-Bus
            if self.signal_match:
                try:
                    self.signal_match.remove()
                    self.signal_match = None
                except Exception as e:
                    self.logger.warning(f"Erreur suppression signal D-Bus: {e}")
                    
            if self.bluealsa_signal_match:
                try:
                    self.bluealsa_signal_match.remove()
                    self.bluealsa_signal_match = None
                except Exception as e:
                    self.logger.warning(f"Erreur suppression signal BlueALSA: {e}")
            
            # 3. Arrêter le service bluealsa via systemd
            self.logger.info("Arrêt du service BlueALSA")
            subprocess.run(["sudo", "systemctl", "stop", "bluealsa.service"], check=False)
            
            # 4. Arrêter l'agent Bluetooth
            if self.agent:
                try:
                    agent_path = self.agent.agent_path if hasattr(self.agent, 'agent_path') else None
                    self.logger.info(f"Arrêt de l'agent Bluetooth {agent_path}")
                    self.agent.unregister_sync()
                    
                    # Arrêter le mainloop
                    if self.mainloop and hasattr(self.mainloop, 'is_running') and self.mainloop.is_running():
                        GLib.idle_add(self.mainloop.quit)
                        
                    # Attendre que le thread se termine
                    if self.agent_thread and self.agent_thread.is_alive():
                        self.agent_thread.join(3)  # Donner plus de temps
                        
                    # Libérer explicitement les références
                    self.agent = None
                    self.mainloop = None
                except Exception as e:
                    self.logger.error(f"Erreur arrêt agent: {e}")
            
            # 5. Désactiver la découvrabilité et l'appairabilité Bluetooth
            subprocess.run(["sudo", "bluetoothctl", "discoverable", "off"], check=False)
            subprocess.run(["sudo", "bluetoothctl", "pairable", "off"], check=False)
            
            # 6. Arrêter le service Bluetooth si configuré
            if self.stop_bluetooth:
                self.logger.info("Arrêt du service Bluetooth")
                subprocess.run(["sudo", "systemctl", "stop", "bluetooth"], check=False)
            
            # 7. Réinitialiser l'état
            self.current_device = None
            await self.notify_state_change(PluginState.INACTIVE)
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur arrêt Bluetooth: {e}")
            return False
    
    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite les commandes pour le plugin"""
        if command == "disconnect":
            if not self.current_device:
                return {"success": False, "error": "Aucun périphérique connecté"}
            
            address = self.current_device.get("address")
            subprocess.run(["bluetoothctl", "disconnect", address], check=False)
            return {"success": True}
            
        elif command == "restart_audio":
            if not self.current_device:
                return {"success": False, "error": "Aucun périphérique connecté"}
            
            address = self.current_device.get("address")
            await self._stop_audio_playback()
            await self._start_audio_playback(address)
            return {"success": True}
            
        elif command == "restart_bluealsa":
            try:
                # Redémarrer le service bluealsa via systemd
                subprocess.run(["sudo", "systemctl", "restart", "bluealsa.service"], check=True)
                return {"success": True, "message": "Service BlueALSA redémarré avec succès"}
            except Exception as e:
                return {"success": False, "error": f"Erreur redémarrage BlueALSA: {str(e)}"}
            
        elif command == "start_direct_audio":
            # Vérifier les PCMs disponibles via D-Bus
            try:
                bluealsa_obj = self.system_bus.get_object("org.bluealsa", "/")
                object_manager = dbus.Interface(bluealsa_obj, "org.freedesktop.DBus.ObjectManager")
                
                # Récupérer tous les objets gérés
                managed_objects = object_manager.GetManagedObjects()
                
                # Trouver le premier PCM A2DP en mode source
                pcm_found = False
                for path, interfaces in managed_objects.items():
                    if "org.bluealsa.PCM1" in interfaces:
                        pcm_props = interfaces["org.bluealsa.PCM1"]
                        if pcm_props.get("Transport", "").startswith("A2DP") and pcm_props.get("Mode") == "source":
                            # Extraire l'adresse du périphérique
                            device_path = str(pcm_props.get("Device", ""))
                            device_parts = device_path.split('/')
                            if len(device_parts) > 0:
                                last_part = device_parts[-1]
                                if last_part.startswith("dev_"):
                                    address = last_part[4:].replace("_", ":")
                                    name = self._get_device_name(device_path)
                                    
                                    # Connecter ce PCM
                                    await self._connect_device(address, name)
                                    pcm_found = True
                                    
                                    return {
                                        "success": True,
                                        "message": f"Audio démarré pour {name}",
                                        "device": {
                                            "address": address,
                                            "name": name
                                        }
                                    }
                
                if not pcm_found:
                    return {"success": False, "error": "Aucun PCM A2DP disponible"}
                    
            except Exception as e:
                self.logger.error(f"Erreur démarrage direct audio: {e}")
                return {"success": False, "error": f"Erreur: {str(e)}"}
            
        elif command == "check_pcms":
            # Liste tous les PCMs disponibles via D-Bus
            try:
                bluealsa_obj = self.system_bus.get_object("org.bluealsa", "/")
                object_manager = dbus.Interface(bluealsa_obj, "org.freedesktop.DBus.ObjectManager")
                
                # Récupérer tous les objets gérés
                managed_objects = object_manager.GetManagedObjects()
                
                # Construire la liste des PCMs
                pcms = []
                for path, interfaces in managed_objects.items():
                    if "org.bluealsa.PCM1" in interfaces:
                        pcm_props = interfaces["org.bluealsa.PCM1"]
                        device_path = str(pcm_props.get("Device", ""))
                        device_parts = device_path.split('/')
                        address = "Unknown"
                        if len(device_parts) > 0:
                            last_part = device_parts[-1]
                            if last_part.startswith("dev_"):
                                address = last_part[4:].replace("_", ":")
                        
                        pcms.append({
                            "path": str(path),
                            "address": address,
                            "name": self._get_device_name(device_path),
                            "transport": str(pcm_props.get("Transport", "")),
                            "mode": str(pcm_props.get("Mode", "")),
                            "codec": str(pcm_props.get("Codec", ""))
                        })
                
                return {
                    "success": True,
                    "pcms": pcms
                }
                
            except Exception as e:
                self.logger.error(f"Erreur récupération PCMs: {e}")
                return {"success": False, "error": f"Erreur: {str(e)}"}
            
        elif command == "set_stop_bluetooth":
            # Définir si le Bluetooth doit être arrêté à la sortie
            try:
                value = bool(data.get("value", False))
                self.stop_bluetooth = value
                return {
                    "success": True,
                    "stop_bluetooth": self.stop_bluetooth
                }
            except Exception as e:
                return {"success": False, "error": f"Erreur: {str(e)}"}
            
        return {"success": False, "error": f"Commande inconnue: {command}"}
    
    async def get_status(self) -> Dict[str, Any]:
        """État actuel du plugin en vérifiant les services systemd"""
        # Vérifier les processus en exécution via systemd
        bt_running = subprocess.run(
            ["systemctl", "is-active", "bluetooth"], 
            capture_output=True, text=True
        ).stdout.strip() == "active"
        
        bluealsa_running = subprocess.run(
            ["systemctl", "is-active", "bluealsa.service"],
            capture_output=True, text=True
        ).stdout.strip() == "active"
        
        playback_running = False
        if self.current_device:
            device_address = self.current_device.get("address")
            if device_address:
                result = subprocess.run(
                    ["systemctl", "is-active", f"bluealsa-aplay@{device_address}.service"],
                    capture_output=True, text=True
                )
                playback_running = result.stdout.strip() == "active"
        
        # Liste des PCMs disponibles via D-Bus
        available_pcms = []
        try:
            if self.system_bus:
                bluealsa_obj = self.system_bus.get_object("org.bluealsa", "/")
                object_manager = dbus.Interface(bluealsa_obj, "org.freedesktop.DBus.ObjectManager")
                
                managed_objects = object_manager.GetManagedObjects()
                
                for path, interfaces in managed_objects.items():
                    if "org.bluealsa.PCM1" in interfaces:
                        pcm_props = interfaces["org.bluealsa.PCM1"]
                        device_path = str(pcm_props.get("Device", ""))
                        transport = str(pcm_props.get("Transport", ""))
                        mode = str(pcm_props.get("Mode", ""))
                        
                        available_pcms.append(f"Path: {path}, Transport: {transport}, Mode: {mode}")
        except:
            # Si BlueALSA n'est pas disponible, utiliser bluealsa-aplay en fallback
            try:
                result = subprocess.run(
                    ["bluealsa-aplay", "-L"],
                    capture_output=True, text=True
                )
                available_pcms = [line.strip() for line in result.stdout.strip().split('\n') if line.strip()]
            except:
                pass
        
        return {
            "device_connected": self.current_device is not None,
            "device_name": self.current_device.get("name") if self.current_device else None,
            "device_address": self.current_device.get("address") if self.current_device else None,
            "bluetooth_running": bt_running,
            "bluealsa_running": bluealsa_running,
            "playback_running": playback_running,
            "available_pcms": available_pcms,
            "stop_bluetooth_on_exit": self.stop_bluetooth
        }
    
    def manages_own_process(self) -> bool:
        """Le plugin gère ses propres processus"""
        return True
    
    def get_process_command(self) -> List[str]:
        """Non utilisé (manages_own_process = True)"""
        return ["true"]
    
    async def get_initial_state(self) -> Dict[str, Any]:
        """État initial du plugin"""
        return await self.get_status()