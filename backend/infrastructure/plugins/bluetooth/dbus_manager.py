"""
Gestionnaire D-Bus pour les interactions avec BlueZ et BlueALSA
"""
import asyncio
import logging
from typing import Dict, Any, Optional, List, Callable, Awaitable
import subprocess
import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

class BluetoothDBusManager:
    """Gestion des communications D-Bus avec BlueZ et BlueALSA"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        # Initialiser le mainloop pour les signaux D-Bus
        DBusGMainLoop(set_as_default=True)
        self.system_bus = dbus.SystemBus()
        self.mainloop = GLib.MainLoop()
        self.mainloop_thread = None
        self.device_callbacks = []
        
    async def initialize(self) -> bool:
        """Initialise le gestionnaire D-Bus"""
        try:
            # Vérifier BlueZ et BlueALSA
            if not await self._check_bluez_service():
                self.logger.error("Service BlueZ non disponible")
                return False
                
            if not await self._check_bluealsa_service():
                self.logger.warning("Service BlueALSA non disponible, il sera démarré par la machine à états")
            
            # S'abonner aux signaux de périphérique
            self._setup_device_signals()
            
            # Démarrer le mainloop dans un thread séparé
            self.mainloop_thread = asyncio.create_task(self._run_mainloop())
            
            return True
        except Exception as e:
            self.logger.error(f"Erreur lors de l'initialisation D-Bus: {e}")
            return False
    
    async def _run_mainloop(self):
        """Exécute le mainloop GLib dans une tâche asyncio"""
        try:
            # Créer un thread exécuteur pour le mainloop GLib
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self.mainloop.run)
        except Exception as e:
            self.logger.error(f"Erreur dans le mainloop GLib: {e}")
    
    async def _check_bluez_service(self) -> bool:
        """Vérifie si le service BlueZ est disponible"""
        try:
            bluez_obj = self.system_bus.get_object("org.bluez", "/")
            return True
        except dbus.exceptions.DBusException:
            return False
    
    async def _check_bluealsa_service(self) -> bool:
        """Vérifie si le service BlueALSA est disponible"""
        try:
            bluealsa_obj = self.system_bus.get_object("org.bluealsa", "/")
            return True
        except dbus.exceptions.DBusException:
            return False
    
    def _setup_device_signals(self) -> None:
        """Configure les signaux pour détecter les connexions/déconnexions Bluetooth"""
        self.system_bus.add_signal_receiver(
            self._device_property_changed,
            dbus_interface="org.freedesktop.DBus.Properties",
            signal_name="PropertiesChanged",
            arg0="org.bluez.Device1",
            path_keyword="path"
        )
    
    def _device_property_changed(self, interface, changed, invalidated, path):
        """Callback pour les changements de propriétés des périphériques"""
        if "Connected" in changed:
            try:
                device_obj = self.system_bus.get_object("org.bluez", path)
                props = dbus.Interface(device_obj, "org.freedesktop.DBus.Properties")
                
                is_connected = changed["Connected"]
                address = props.Get("org.bluez.Device1", "Address")
                
                device_info = {
                    "address": address,
                    "path": path
                }
                
                # Ajouter plus d'informations si le périphérique est connecté
                if is_connected:
                    try:
                        device_info["name"] = props.Get("org.bluez.Device1", "Name")
                    except:
                        device_info["name"] = "Unknown Device"
                    
                    # Vérifier les UUIDs pour le support A2DP
                    try:
                        uuids = props.Get("org.bluez.Device1", "UUIDs")
                        device_info["a2dp_sink_support"] = "0000110d-0000-1000-8000-00805f9b34fb" in uuids
                    except:
                        device_info["a2dp_sink_support"] = False
                
                # Notifier les callbacks
                event_type = "connected" if is_connected else "disconnected"
                for callback in self.device_callbacks:
                    asyncio.create_task(callback(event_type, device_info))
                    
            except Exception as e:
                self.logger.error(f"Erreur lors du traitement de l'événement de périphérique: {e}")
    
    def register_device_callback(self, callback: Callable[[str, Dict[str, Any]], Awaitable[None]]) -> None:
        """Enregistre un callback pour les événements de périphérique"""
        if callback not in self.device_callbacks:
            self.device_callbacks.append(callback)
    
    async def configure_adapter(self, name: str, discoverable: bool = True, 
                             discoverable_timeout: int = 0, pairable: bool = True,
                             pairable_timeout: int = 0) -> bool:
        """Configure l'adaptateur Bluetooth"""
        try:
            # Retrouver l'objet de l'adaptateur
            bluez_obj = self.system_bus.get_object("org.bluez", "/")
            manager = dbus.Interface(bluez_obj, "org.freedesktop.DBus.ObjectManager")
            objects = manager.GetManagedObjects()
            
            adapter_path = None
            for path, interfaces in objects.items():
                if "org.bluez.Adapter1" in interfaces:
                    adapter_path = path
                    break
            
            if not adapter_path:
                self.logger.error("Aucun adaptateur Bluetooth trouvé")
                return False
            
            # Configurer l'adaptateur
            adapter = dbus.Interface(
                self.system_bus.get_object("org.bluez", adapter_path),
                "org.freedesktop.DBus.Properties"
            )
            
            adapter.Set("org.bluez.Adapter1", "Alias", name)
            adapter.Set("org.bluez.Adapter1", "Discoverable", discoverable)
            adapter.Set("org.bluez.Adapter1", "DiscoverableTimeout", dbus.UInt32(discoverable_timeout))
            adapter.Set("org.bluez.Adapter1", "Pairable", pairable)
            adapter.Set("org.bluez.Adapter1", "PairableTimeout", dbus.UInt32(pairable_timeout))
            
            # Définir la classe de périphérique pour A2DP Sink
            try:
                hci_name = adapter_path.split('/')[-1]
                subprocess.run(["sudo", "hciconfig", hci_name, "class", "0x41C"], check=True)
            except Exception as e:
                self.logger.warning(f"Impossible de définir la classe de périphérique: {e}")
            
            self.logger.info(f"Adaptateur {adapter_path} configuré avec succès")
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur lors de la configuration de l'adaptateur: {e}")
            return False
    
    async def get_connected_devices(self) -> List[Dict[str, Any]]:
        """Récupère la liste des périphériques Bluetooth connectés"""
        try:
            connected_devices = []
            
            # Récupérer tous les périphériques
            bluez_obj = self.system_bus.get_object("org.bluez", "/")
            manager = dbus.Interface(bluez_obj, "org.freedesktop.DBus.ObjectManager")
            objects = manager.GetManagedObjects()
            
            for path, interfaces in objects.items():
                if "org.bluez.Device1" in interfaces:
                    props = interfaces["org.bluez.Device1"]
                    
                    if props.get("Connected", False):
                        device = {
                            "address": props.get("Address", ""),
                            "name": props.get("Name", "Unknown Device"),
                            "path": path
                        }
                        
                        # Vérifier le support A2DP Sink
                        uuids = props.get("UUIDs", [])
                        device["a2dp_sink_support"] = "0000110d-0000-1000-8000-00805f9b34fb" in uuids
                        
                        connected_devices.append(device)
            
            return connected_devices
            
        except Exception as e:
            self.logger.error(f"Erreur lors de la récupération des périphériques connectés: {e}")
            return []
    
    async def disconnect_device(self, address: str) -> bool:
        """Déconnecte un périphérique Bluetooth"""
        try:
            # Trouver le périphérique
            bluez_obj = self.system_bus.get_object("org.bluez", "/")
            manager = dbus.Interface(bluez_obj, "org.freedesktop.DBus.ObjectManager")
            objects = manager.GetManagedObjects()
            
            device_path = None
            for path, interfaces in objects.items():
                if "org.bluez.Device1" in interfaces:
                    if interfaces["org.bluez.Device1"].get("Address") == address:
                        device_path = path
                        break
            
            if not device_path:
                self.logger.error(f"Périphérique {address} non trouvé")
                return False
            
            # Déconnecter le périphérique
            device = dbus.Interface(
                self.system_bus.get_object("org.bluez", device_path),
                "org.bluez.Device1"
            )
            
            device.Disconnect()
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur lors de la déconnexion du périphérique {address}: {e}")
            return False