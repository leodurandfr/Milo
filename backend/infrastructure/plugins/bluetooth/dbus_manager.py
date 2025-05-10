"""
Gestionnaire D-Bus pour les interactions avec BlueZ et BlueALSA - Version optimisée
"""
import asyncio
import logging
from typing import Dict, Any, Optional, List, Callable, Awaitable
import subprocess
import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib
from concurrent.futures import ThreadPoolExecutor

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
        
        # Un seul executor pour toutes les opérations D-Bus
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dbus_exec")
        
    async def initialize(self) -> bool:
        """Initialise le gestionnaire D-Bus"""
        try:
            # Vérifier BlueZ et BlueALSA
            if not await self._check_service("org.bluez", "/"):
                self.logger.error("Service BlueZ non disponible")
                return False
                
            if not await self._check_service("org.bluealsa", "/"):
                self.logger.warning("Service BlueALSA non disponible, il sera démarré par la machine à états")
            
            # S'abonner aux signaux de périphérique
            self._setup_device_signals()
            
            # Démarrer le mainloop dans un thread séparé
            self.mainloop_thread = asyncio.create_task(self._run_mainloop())
            
            return True
        except Exception as e:
            self.logger.error(f"Erreur lors de l'initialisation D-Bus: {e}")
            return False
    
    async def _check_service(self, service_name: str, path: str) -> bool:
        """Vérifie si un service D-Bus est disponible"""
        try:
            loop = asyncio.get_running_loop()
            def _check():
                try:
                    self.system_bus.get_object(service_name, path)
                    return True
                except dbus.exceptions.DBusException:
                    return False
                    
            return await loop.run_in_executor(self.executor, _check)
        except Exception as e:
            self.logger.error(f"Erreur vérification service {service_name}: {e}")
            return False
    
    async def _run_mainloop(self):
        """Exécute le mainloop GLib dans une tâche asyncio"""
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(self.executor, self.mainloop.run)
        except Exception as e:
            self.logger.error(f"Erreur dans le mainloop GLib: {e}")
    
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
        if "Connected" not in changed:
            return
            
        try:
            device_obj = self.system_bus.get_object("org.bluez", path)
            props = dbus.Interface(device_obj, "org.freedesktop.DBus.Properties")
            
            is_connected = changed["Connected"]
            address = str(props.Get("org.bluez.Device1", "Address"))
            
            device_info = {
                "address": address,
                "path": path
            }
            
            # Ajouter plus d'informations si le périphérique est connecté
            if is_connected:
                try:
                    device_info["name"] = str(props.Get("org.bluez.Device1", "Name"))
                except:
                    device_info["name"] = "Unknown Device"
                
                # Vérifier les UUIDs pour le support A2DP
                try:
                    uuids = props.Get("org.bluez.Device1", "UUIDs")
                    device_info["a2dp_sink_support"] = "0000110d-0000-1000-8000-00805f9b34fb" in uuids
                except:
                    device_info["a2dp_sink_support"] = False
            
            # Notifier les callbacks - créer une seule tâche asyncio
            event_type = "connected" if is_connected else "disconnected"
            
            # Créer une tâche qui exécutera tous les callbacks
            asyncio.run_coroutine_threadsafe(
                self._notify_callbacks(event_type, device_info),
                asyncio.get_event_loop()
            )
                
        except Exception as e:
            self.logger.error(f"Erreur lors du traitement de l'événement de périphérique: {e}")
    
    async def _notify_callbacks(self, event_type: str, device_info: Dict[str, Any]):
        """Notifie tous les callbacks enregistrés de manière asynchrone"""
        for callback in self.device_callbacks:
            try:
                await callback(event_type, device_info)
            except Exception as e:
                self.logger.error(f"Erreur dans le callback de périphérique: {e}")
    
    def register_device_callback(self, callback: Callable[[str, Dict[str, Any]], Awaitable[None]]) -> None:
        """Enregistre un callback pour les événements de périphérique"""
        if callback not in self.device_callbacks:
            self.device_callbacks.append(callback)
    
    async def configure_adapter(self, name: str, discoverable: bool = True, 
                             discoverable_timeout: int = 0, pairable: bool = True,
                             pairable_timeout: int = 0) -> bool:
        """Configure l'adaptateur Bluetooth"""
        try:
            loop = asyncio.get_running_loop()
            
            def _do_configure():
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
                
                self.logger.info(f"Adaptateur {adapter_path} configuré avec succès")
                return adapter_path
            
            adapter_path = await loop.run_in_executor(self.executor, _do_configure)
            
            if not adapter_path:
                return False
                
            # Configuration hciconfig en parallèle
            hci_name = adapter_path.split('/')[-1]
            try:
                await asyncio.gather(
                    asyncio.create_subprocess_exec(
                        "sudo", "hciconfig", hci_name, "class", "0x240404",
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL
                    ),
                    asyncio.create_subprocess_exec(
                        "sudo", "hciconfig", hci_name, "name", "oakOS",
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL
                    )
                )
            except Exception as e:
                self.logger.warning(f"Impossible de définir la classe de périphérique: {e}")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur lors de la configuration de l'adaptateur: {e}")
            return False
    
    async def get_connected_devices(self) -> List[Dict[str, Any]]:
        """Récupère la liste des périphériques Bluetooth connectés"""
        try:
            loop = asyncio.get_running_loop()
            
            def _get_devices():
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
                                "address": str(props.get("Address", "")),
                                "name": str(props.get("Name", "Unknown Device")),
                                "path": str(path)
                            }
                            
                            # Vérifier le support A2DP Sink
                            uuids = props.get("UUIDs", [])
                            device["a2dp_sink_support"] = "0000110d-0000-1000-8000-00805f9b34fb" in uuids
                            
                            connected_devices.append(device)
                
                return connected_devices
            
            return await loop.run_in_executor(self.executor, _get_devices)
            
        except Exception as e:
            self.logger.error(f"Erreur lors de la récupération des périphériques connectés: {e}")
            return []
    
    async def disconnect_device(self, address: str) -> bool:
        """Déconnecte un périphérique Bluetooth"""
        try:
            loop = asyncio.get_running_loop()
            
            def _disconnect():
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
            
            return await loop.run_in_executor(self.executor, _disconnect)
            
        except Exception as e:
            self.logger.error(f"Erreur lors de la déconnexion du périphérique {address}: {e}")
            return False