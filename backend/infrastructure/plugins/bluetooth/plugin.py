"""
Plugin Bluetooth optimisé pour oakOS - Version systemd avec détection D-Bus complète
"""
import asyncio
import logging
import subprocess
import threading
from typing import Dict, Any, List

import dbus
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
        self._disconnecting = False 
        
        # Agent Bluetooth et D-Bus
        self.agent = None
        self.agent_thread = None
        self.mainloop = None
        self.system_bus = None
        
        # Gestionnaires de signaux D-Bus
        self.signal_match = None
        self.signal_interfaces_added = None
        self.bluealsa_signal_match = None
        self.bluealsa_signal_removed = None
        self.bluealsa_props_match = None
        
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
            dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
            self.system_bus = dbus.SystemBus()
            
            self.agent = BluetoothAgent(self.system_bus)
            self.mainloop = GLib.MainLoop()
            
            # Configurer tous les événements D-Bus
            self._setup_device_signals()
            self._setup_bluealsa_signals()
            
            if self.agent.register_sync():
                self.logger.info(f"Agent Bluetooth enregistré et prêt avec le chemin {self.agent.agent_path}")
            else:
                self.logger.warning("Échec de l'enregistrement de l'agent Bluetooth")
                
            self.mainloop.run()
        except Exception as e:
            self.logger.error(f"Erreur thread agent: {e}")
    
    def _setup_device_signals(self):
        """Configuration des événements D-Bus pour détecter les connexions Bluetooth"""
        try:
            # Signal pour les changements de propriétés des appareils
            self.signal_match = self.system_bus.add_signal_receiver(
                self._device_property_changed,
                dbus_interface="org.freedesktop.DBus.Properties",
                signal_name="PropertiesChanged",
                arg0="org.bluez.Device1",
                path_keyword="path"
            )
            
            # Signal pour les nouveaux appareils
            self.signal_interfaces_added = self.system_bus.add_signal_receiver(
                self._device_interface_added,
                dbus_interface="org.freedesktop.DBus.ObjectManager",
                signal_name="InterfacesAdded"
            )
            
            # Vérifier les appareils déjà connectés
            self._check_existing_devices()
            
            self.logger.info("Événements D-Bus BlueZ configurés avec succès")
        except Exception as e:
            self.logger.error(f"Erreur configuration événements D-Bus BlueZ: {e}")
    
    def _setup_bluealsa_signals(self):
        """Configuration des événements D-Bus pour détecter les PCMs BlueALSA"""
        try:
            # Signal pour les nouveaux PCMs (interfaces ajoutées)
            self.bluealsa_signal_match = self.system_bus.add_signal_receiver(
                self._bluealsa_interfaces_added,
                dbus_interface="org.freedesktop.DBus.ObjectManager", 
                signal_name="InterfacesAdded"
            )
            
            # Signal pour les PCMs supprimés
            self.bluealsa_signal_removed = self.system_bus.add_signal_receiver(
                self._bluealsa_interfaces_removed,
                dbus_interface="org.freedesktop.DBus.ObjectManager", 
                signal_name="InterfacesRemoved"
            )
            
            # Signal pour les changements de propriétés des PCMs existants
            self.bluealsa_props_match = self.system_bus.add_signal_receiver(
                self._bluealsa_properties_changed,
                dbus_interface="org.freedesktop.DBus.Properties",
                signal_name="PropertiesChanged",
                arg0="org.bluealsa.PCM1",
                path_keyword="path"
            )
            
            # Vérifier les PCMs déjà existants
            self._check_existing_pcms()
            
            self.logger.info("Événements D-Bus BlueALSA configurés avec succès")
        except Exception as e:
            self.logger.error(f"Erreur configuration événements D-Bus BlueALSA: {e}")
    
    def _check_existing_devices(self):
        """Vérifie les appareils Bluetooth déjà connectés"""
        try:
            # Récupérer tous les objets BlueZ
            bluez_obj = self.system_bus.get_object("org.bluez", "/")
            manager = dbus.Interface(bluez_obj, "org.freedesktop.DBus.ObjectManager")
            objects = manager.GetManagedObjects()
            
            # Rechercher les appareils connectés
            for path, interfaces in objects.items():
                if "org.bluez.Device1" in interfaces:
                    props = interfaces["org.bluez.Device1"]
                    if props.get("Connected", False):
                        address = str(props.get("Address", ""))
                        name = str(props.get("Name", "Appareil inconnu"))
                        self.logger.info(f"Appareil déjà connecté détecté: {name} ({address})")
                        # Le PCM sera trouvé par _check_existing_pcms()
            
        except Exception as e:
            self.logger.error(f"Erreur vérification appareils existants: {e}")
    
    def _check_existing_pcms(self):
        """Vérifie les PCMs BlueALSA existants au démarrage"""
        try:
            bluealsa_obj = self.system_bus.get_object("org.bluealsa", "/")
            object_manager = dbus.Interface(bluealsa_obj, "org.freedesktop.DBus.ObjectManager")
            managed_objects = object_manager.GetManagedObjects()
            
            pcm_count = 0
            for path, interfaces in managed_objects.items():
                if "org.bluealsa.PCM1" in interfaces:
                    pcm_count += 1
                    self._process_pcm(path, interfaces["org.bluealsa.PCM1"])
            
            self.logger.info(f"{pcm_count} PCMs BlueALSA existants trouvés")
            
        except dbus.exceptions.DBusException:
            # Si BlueALSA n'est pas encore démarré, c'est normal
            self.logger.debug("BlueALSA n'est pas encore disponible")
        except Exception as e:
            self.logger.error(f"Erreur vérification PCMs existants: {e}")
    
    def _device_interface_added(self, object_path, interfaces):
        """Callback lorsqu'un nouvel appareil Bluetooth est ajouté"""
        if "org.bluez.Device1" in interfaces:
            self.logger.info(f"Nouvel appareil détecté: {object_path}")
            try:
                device_props = interfaces["org.bluez.Device1"]
                # Vérifier si l'appareil est connecté
                if device_props.get("Connected", False):
                    address = str(device_props.get("Address", ""))
                    name = str(device_props.get("Name", "Appareil inconnu"))
                    self.logger.info(f"Nouvel appareil déjà connecté: {name} ({address})")
                    # Le reste de la découverte des PCMs sera géré par les événements BlueALSA
            except Exception as e:
                self.logger.error(f"Erreur traitement nouvel appareil: {e}")
    
    def _bluealsa_interfaces_added(self, object_path, interfaces):
        """Callback lorsqu'une nouvelle interface est ajoutée à BlueALSA"""
        if "org.bluealsa.PCM1" in interfaces:
            self.logger.info(f"Nouveau PCM détecté: {object_path}")
            self._process_pcm(object_path, interfaces["org.bluealsa.PCM1"])
    
    def _bluealsa_interfaces_removed(self, object_path, interfaces):
        """Callback lorsqu'une interface est supprimée de BlueALSA"""
        if "org.bluealsa.PCM1" in interfaces:
            self.logger.info(f"PCM supprimé: {object_path}")
            # On pourrait réagir ici à la suppression d'un PCM si nécessaire
    
    def _bluealsa_properties_changed(self, interface, changed, invalidated, path=None):
        """Callback pour les changements de propriétés des PCMs BlueALSA"""
        self.logger.debug(f"Propriétés PCM modifiées: {path}, changements: {changed}")
        
        # Récupérer toutes les propriétés du PCM modifié
        try:
            if path:
                pcm_obj = self.system_bus.get_object("org.bluealsa", path)
                props = dbus.Interface(pcm_obj, "org.freedesktop.DBus.Properties")
                properties = {}
                
                # Créer un dictionnaire des propriétés importantes
                for prop_name in ["Device", "Transport", "Mode", "Codec"]:
                    try:
                        properties[prop_name] = props.Get("org.bluealsa.PCM1", prop_name)
                    except:
                        pass
                
                # Traiter le PCM comme s'il était nouveau si les propriétés essentielles sont présentes
                if "Device" in properties and "Transport" in properties and "Mode" in properties:
                    self.logger.info(f"PCM avec propriétés mises à jour: {path}")
                    self._process_pcm(path, properties)
        except Exception as e:
            self.logger.error(f"Erreur traitement changement de propriétés PCM: {e}")
    
    def _process_pcm(self, path, properties):
        """Traite un PCM BlueALSA"""
        try:
            device_path = str(properties.get("Device", ""))
            if not device_path:
                return
                
            transport = str(properties.get("Transport", ""))
            mode = str(properties.get("Mode", ""))
            codec = str(properties.get("Codec", ""))
            
            self.logger.info(f"PCM traité: {path}, Transport: {transport}, Mode: {mode}, Codec: {codec}")
            
            # A2DP-sink signifie que c'est un périphérique qui envoie de l'audio à notre système
            if transport.startswith("A2DP") and mode == "source":
                device_parts = device_path.split('/')
                if len(device_parts) > 0:
                    last_part = device_parts[-1]
                    if last_part.startswith("dev_"):
                        address = last_part[4:].replace("_", ":")
                        name = self._get_device_name(device_path)
                        
                        # Si pas d'appareil actif, connecter celui-ci
                        if self.current_device is None:
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
            device_obj = self.system_bus.get_object("org.bluez", path)
            props = dbus.Interface(device_obj, "org.freedesktop.DBus.Properties")
            
            address = str(props.Get("org.bluez.Device1", "Address"))
            try:
                name = str(props.Get("org.bluez.Device1", "Name"))
            except:
                name = "Appareil inconnu"
            
            if is_connected:
                self.logger.info(f"Appareil connecté: {name} ({address})")
                # La détection du PCM se fera par les événements BlueALSA
            else:
                self.logger.info(f"Appareil déconnecté: {name} ({address})")
                
                # Ne gérer la déconnexion que si elle n'est pas déjà en cours manuellement
                if self.current_device and self.current_device.get("address") == address and not self._disconnecting:
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
            await self._stop_audio_playback()
            self.current_device = None
            await self.notify_state_change(PluginState.READY, {"device_connected": False})
        except Exception as e:
            self.logger.error(f"Erreur déconnexion appareil: {e}")
    
    async def _stop_audio_playback(self):
        """Arrête la lecture audio en cours via systemd"""
        if not self.current_device:
            return
            
        try:
            address = self.current_device.get("address")
            cmd = ["sudo", "systemctl", "stop", f"bluealsa-aplay@{address}.service"]
            self.logger.info(f"Arrêt du service: {cmd}")
            subprocess.run(cmd, check=True)
            self.logger.info(f"Service bluealsa-aplay arrêté pour {address}")
        except Exception as e:
            self.logger.error(f"Erreur arrêt audio: {e}")
    
    async def _start_audio_playback(self, device_address: str) -> None:
        """Démarre la lecture audio via systemd"""
        try:
            # Arrêter tout service existant d'abord
            await self._stop_audio_playback()
            
            # Démarrer le service
            self.logger.info(f"Démarrage du service bluealsa-aplay pour {device_address}")
            cmd = ["sudo", "systemctl", "start", f"bluealsa-aplay@{device_address}.service"]
            subprocess.run(cmd, check=True)
            self.logger.info(f"Service bluealsa-aplay démarré pour {device_address}")
            
            # Vérifier l'état
            await asyncio.sleep(1)
            if not await self._is_service_active(f"bluealsa-aplay@{device_address}.service"):
                self.logger.error("Le service bluealsa-aplay n'a pas démarré correctement")
                return
                
            self.logger.info("Lecture audio démarrée avec succès")
        except Exception as e:
            self.logger.error(f"Erreur démarrage lecture: {e}")
    
    async def _is_service_active(self, service_name: str) -> bool:
        """Vérifie si un service systemd est actif"""
        cmd = ["systemctl", "is-active", service_name]
        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.stdout.strip() == "active"
    
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
    
    async def start(self) -> bool:
        """Démarre les services nécessaires via systemd"""
        try:
            self.logger.info("Démarrage du plugin Bluetooth")
            
            # 1. Démarrer le service bluetooth si nécessaire
            if not await self._is_service_active("bluetooth.service"):
                self.logger.info("Démarrage du service bluetooth")
                subprocess.run(["sudo", "systemctl", "start", "bluetooth"], check=True)
                await asyncio.sleep(2)
            
            # 2. Configurer l'adaptateur bluetooth
            subprocess.run(["sudo", "hciconfig", "hci0", "class", "0x240404"], check=False)
            subprocess.run(["sudo", "hciconfig", "hci0", "name", "oakOS"], check=False)
            subprocess.run(["sudo", "bluetoothctl", "discoverable", "on"], check=False)
            subprocess.run(["sudo", "bluetoothctl", "pairable", "on"], check=False)
            subprocess.run(["sudo", "bluetoothctl", "discoverable-timeout", "0"], check=False)
            
            # 3. Démarrer BlueALSA via systemd
            self.logger.info("Démarrage du service BlueALSA via systemd")
            subprocess.run(["sudo", "systemctl", "start", "bluealsa.service"], check=True)
            
            # Vérifier que le service a démarré
            if not await self._is_service_active("bluealsa.service"):
                raise RuntimeError("Le service BlueALSA n'a pas pu démarrer")
            
            # 4. Démarrer l'agent Bluetooth
            await self._start_bluetooth_agent()
            
            # 5. Notifier l'état prêt
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
            for signal in [self.signal_match, self.bluealsa_signal_match, 
                           self.bluealsa_props_match, self.signal_interfaces_added,
                           self.bluealsa_signal_removed]:
                if signal:
                    try:
                        signal.remove()
                    except Exception as e:
                        self.logger.warning(f"Erreur suppression signal: {e}")
            
            self.signal_match = None
            self.bluealsa_signal_match = None
            self.bluealsa_props_match = None
            self.signal_interfaces_added = None
            self.bluealsa_signal_removed = None
            
            # 3. Arrêter le service bluealsa
            self.logger.info("Arrêt du service BlueALSA")
            subprocess.run(["sudo", "systemctl", "stop", "bluealsa.service"], check=False)
            
            # 4. Arrêter l'agent Bluetooth
            if self.agent:
                try:
                    self.agent.unregister_sync()
                    if self.mainloop and hasattr(self.mainloop, 'is_running') and self.mainloop.is_running():
                        GLib.idle_add(self.mainloop.quit)
                        
                    # Libérer les références
                    agent_temp = self.agent
                    self.agent = None
                    self.mainloop = None
                    
                    # Nettoyage
                    if hasattr(agent_temp, 'remove_from_connection'):
                        agent_temp.remove_from_connection()
                except Exception as e:
                    self.logger.error(f"Erreur arrêt agent: {e}")
            
            # 5. Désactiver la découvrabilité
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
        try:
            if command == "disconnect":
                if not self.current_device:
                    return {"success": False, "error": "Aucun périphérique connecté"}
                
                address = self.current_device.get("address")
                name = self.current_device.get("name", "Appareil inconnu")
                
                # Marquer le début de la déconnexion
                self._disconnecting = True
                
                # Arrêter d'abord la lecture audio
                await self._stop_audio_playback()
                
                # Déconnecter l'appareil via bluetoothctl
                self.logger.info(f"Déconnexion manuelle de l'appareil {name} ({address})")
                result = subprocess.run(["bluetoothctl", "disconnect", address], 
                                        check=False, capture_output=True, text=True)
                
                if result.returncode != 0:
                    self.logger.error(f"Erreur lors de la déconnexion: {result.stderr}")
                    self._disconnecting = False  # Réinitialiser l'indicateur
                    return {"success": False, "error": f"Erreur de déconnexion: {result.stderr}"}
                
                # Mettre à jour l'état interne directement ici au lieu d'appeler _handle_device_disconnected
                self.logger.info(f"Réinitialisation de l'état après déconnexion manuelle")
                self.current_device = None
                await self.notify_state_change(PluginState.READY, {"device_connected": False})
                
                # Réinitialiser l'indicateur
                self._disconnecting = False
                
                return {"success": True, "message": f"Appareil {name} déconnecté avec succès"}
                
            elif command == "restart_audio":
                if not self.current_device:
                    return {"success": False, "error": "Aucun périphérique connecté"}
                
                address = self.current_device.get("address")
                await self._stop_audio_playback()
                await self._start_audio_playback(address)
                return {"success": True}
                
            elif command == "restart_bluealsa":
                subprocess.run(["sudo", "systemctl", "restart", "bluealsa.service"], check=True)
                return {"success": True, "message": "Service BlueALSA redémarré avec succès"}
                
            elif command == "set_stop_bluetooth":
                value = bool(data.get("value", False))
                self.stop_bluetooth = value
                return {"success": True, "stop_bluetooth": self.stop_bluetooth}
                
            elif command == "check_pcms" or command == "list_pcms":
                pcms = await self._get_pcm_list()
                return {"success": True, "pcms": pcms}
            
            return {"success": False, "error": f"Commande inconnue: {command}"}
        except Exception as e:
            self.logger.error(f"Erreur dans handle_command: {e}")
            return {"success": False, "error": str(e)}
    
    async def _get_pcm_list(self):
        """Récupère la liste des PCMs via D-Bus"""
        pcms = []
        try:
            if self.system_bus:
                bluealsa_obj = self.system_bus.get_object("org.bluealsa", "/")
                object_manager = dbus.Interface(bluealsa_obj, "org.freedesktop.DBus.ObjectManager")
                managed_objects = object_manager.GetManagedObjects()
                
                for path, interfaces in managed_objects.items():
                    if "org.bluealsa.PCM1" in interfaces:
                        pcm_props = interfaces["org.bluealsa.PCM1"]
                        device_path = str(pcm_props.get("Device", ""))
                        address = "Unknown"
                        
                        if device_path:
                            parts = device_path.split('/')
                            if len(parts) > 0 and parts[-1].startswith("dev_"):
                                address = parts[-1][4:].replace("_", ":")
                                
                        pcms.append({
                            "path": str(path),
                            "address": address,
                            "name": self._get_device_name(device_path),
                            "transport": str(pcm_props.get("Transport", "")),
                            "mode": str(pcm_props.get("Mode", "")),
                            "codec": str(pcm_props.get("Codec", ""))
                        })
        except Exception as e:
            self.logger.error(f"Erreur récupération PCMs: {e}")
        
        return pcms
    
    async def get_status(self) -> Dict[str, Any]:
        """État actuel du plugin"""
        try:
            bt_running = await self._is_service_active("bluetooth.service")
            bluealsa_running = await self._is_service_active("bluealsa.service")
            
            playback_running = False
            if self.current_device and self.current_device.get("address"):
                service_name = f"bluealsa-aplay@{self.current_device['address']}.service"
                playback_running = await self._is_service_active(service_name)
            
            # Version simplifiée du statut
            return {
                "device_connected": self.current_device is not None,
                "device_name": self.current_device.get("name") if self.current_device else None,
                "device_address": self.current_device.get("address") if self.current_device else None,
                "bluetooth_running": bt_running,
                "bluealsa_running": bluealsa_running,
                "playback_running": playback_running,
                "stop_bluetooth_on_exit": self.stop_bluetooth
            }
        except Exception as e:
            self.logger.error(f"Erreur dans get_status: {e}")
            return {
                "device_connected": False,
                "error": str(e)
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