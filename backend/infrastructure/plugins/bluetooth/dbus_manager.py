"""
Gestionnaire D-Bus asynchrone pour les interactions avec BlueZ et BlueALSA
"""
import asyncio
import logging
from typing import Dict, Any, Optional, List, Callable, Awaitable, Set

from dbus_next.aio import MessageBus
from dbus_next import BusType, Message, MessageType, Variant
from dbus_next.service import ServiceInterface, method, dbus_property, signal
from dbus_next.constants import PropertyAccess
from dbus_next.errors import DBusError

class BluetoothDBusManager:
    """Gestion des communications D-Bus avec BlueZ et BlueALSA"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.bus = None
        self.device_callbacks = []  # Stockage des callbacks
        self.bluez_objects = {}
        
        # Cache des objets pour éviter des requêtes D-Bus répétitives
        self.adapter_path = None
        self.adapter_interface = None
        
    def register_device_callback(self, callback: Callable[[str, Dict[str, Any]], Awaitable[None]]) -> None:
        """Enregistre un callback pour les événements de périphérique"""
        if callback not in self.device_callbacks:
            self.device_callbacks.append(callback)
            
    async def initialize(self) -> bool:
        """Initialise le gestionnaire D-Bus"""
        try:
            # Connexion au bus système
            self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
            return True
        except Exception as e:
            self.logger.error(f"Erreur lors de l'initialisation D-Bus: {e}")
            return False
    
    async def _check_service(self, service_name: str) -> bool:
        """Vérifie si un service D-Bus est disponible"""
        try:
            introspect = await self.bus.introspect(service_name, '/')
            return True
        except Exception as e:
            self.logger.debug(f"Service {service_name} non disponible: {e}")
            return False
    
    async def _discover_adapter(self) -> bool:
        """Découvre l'adaptateur Bluetooth principal"""
        try:
            # Obtenir l'interface ObjectManager
            introspect = await self.bus.introspect('org.bluez', '/')
            object_manager = self.bus.get_proxy_object('org.bluez', '/', introspect)
            manager_interface = object_manager.get_interface('org.freedesktop.DBus.ObjectManager')
            
            # Récupérer tous les objets gérés
            objects = await manager_interface.call_get_managed_objects()
            
            # Chercher le premier adaptateur
            for path, interfaces in objects.items():
                if 'org.bluez.Adapter1' in interfaces:
                    self.adapter_path = path
                    
                    # Obtenir l'interface Properties pour l'adaptateur
                    adapter_introspect = await self.bus.introspect('org.bluez', path)
                    adapter_proxy = self.bus.get_proxy_object('org.bluez', path, adapter_introspect)
                    self.adapter_interface = adapter_proxy.get_interface('org.freedesktop.DBus.Properties')
                    
                    self.logger.info(f"Adaptateur Bluetooth trouvé: {path}")
                    return True
            
            self.logger.error("Aucun adaptateur Bluetooth trouvé")
            return False
        except Exception as e:
            self.logger.error(f"Erreur lors de la découverte de l'adaptateur: {e}")
            return False
    
    async def _setup_property_changed_signal(self) -> None:
        """Configure la réception du signal PropertiesChanged"""
        def property_changed_handler(message: Message) -> None:
            """Gestionnaire de signal PropertiesChanged"""
            if message.message_type != MessageType.SIGNAL:
                return
                
            if message.interface == 'org.freedesktop.DBus.Properties' and message.member == 'PropertiesChanged':
                interface_name, changed_properties, invalidated = message.body
                
                # Vérifier si c'est un périphérique Bluetooth
                if interface_name == 'org.bluez.Device1' and message.path:
                    device_path = message.path
                    
                    # Vérifier si la propriété Connected a changé
                    if 'Connected' in changed_properties:
                        # Extraire la valeur du booléen Connected
                        is_connected = changed_properties['Connected'].value if isinstance(changed_properties['Connected'], Variant) else changed_properties['Connected']
                        asyncio.create_task(self._handle_connection_change(
                            device_path, 
                            is_connected
                        ))
        
        # S'abonner au signal PropertiesChanged
        self.bus.add_message_handler(property_changed_handler)
        
        # Ajouter un match pour le signal PropertiesChanged spécifique aux périphériques
        await self.bus.call(
            Message(
                destination='org.freedesktop.DBus',
                interface='org.freedesktop.DBus',
                path='/org/freedesktop/DBus',
                member='AddMatch',
                signature='s',
                body=["type='signal',interface='org.freedesktop.DBus.Properties',member='PropertiesChanged',arg0='org.bluez.Device1'"]
            )
        )
        
        self.logger.info("Signal PropertiesChanged configuré")
    
    async def _setup_object_manager_signals(self) -> None:
        """Configure la réception des signaux ObjectManager"""
        def object_manager_handler(message: Message) -> None:
            """Gestionnaire pour InterfacesAdded et InterfacesRemoved"""
            if message.message_type != MessageType.SIGNAL:
                return
                
            if message.interface == 'org.freedesktop.DBus.ObjectManager':
                if message.member == 'InterfacesAdded':
                    path, interfaces = message.body
                    
                    # Si un nouveau périphérique est ajouté
                    if 'org.bluez.Device1' in interfaces:
                        asyncio.create_task(self._handle_new_device(path, interfaces['org.bluez.Device1']))
                
                elif message.member == 'InterfacesRemoved':
                    path, interfaces_list = message.body
                    
                    # Si un périphérique est supprimé
                    if 'org.bluez.Device1' in interfaces_list:
                        asyncio.create_task(self._handle_device_removed(path))
        
        # S'abonner aux signaux ObjectManager
        self.bus.add_message_handler(object_manager_handler)
        
        # Ajouter un match pour les signaux ObjectManager
        await self.bus.call(
            Message(
                destination='org.freedesktop.DBus',
                interface='org.freedesktop.DBus',
                path='/org/freedesktop/DBus',
                member='AddMatch',
                signature='s',
                body=["type='signal',interface='org.freedesktop.DBus.ObjectManager'"]
            )
        )
        
        self.logger.info("Signaux ObjectManager configurés")
    
    async def _setup_bluealsa_signals(self) -> None:
        """Configure la réception des signaux ObjectManager pour BlueALSA"""
        def bluealsa_handler(message: Message) -> None:
            """Gestionnaire pour les signaux BlueALSA"""
            if message.message_type != MessageType.SIGNAL:
                return
                
            if message.interface == 'org.freedesktop.DBus.ObjectManager':
                if message.member == 'InterfacesAdded':
                    path, interfaces = message.body
                    
                    # Si un nouveau PCM est ajouté
                    if 'org.bluealsa.PCM1' in interfaces:
                        asyncio.create_task(self._handle_new_pcm(path, interfaces['org.bluealsa.PCM1']))
        
        # S'abonner aux signaux ObjectManager de BlueALSA
        try:
            # Ajouter un match pour les signaux ObjectManager de BlueALSA
            await self.bus.call(
                Message(
                    destination='org.freedesktop.DBus',
                    interface='org.freedesktop.DBus',
                    path='/org/freedesktop/DBus',
                    member='AddMatch',
                    signature='s',
                    body=["type='signal',interface='org.freedesktop.DBus.ObjectManager',sender='org.bluealsa'"]
                )
            )
            
            # Ajouter le handler
            self.bus.add_message_handler(bluealsa_handler)
            self.logger.info("Surveillance des PCMs BlueALSA configurée")
        except Exception as e:
            self.logger.error(f"Erreur lors de la configuration des signaux BlueALSA: {e}")
    
    async def _handle_connection_change(self, device_path: str, is_connected: bool) -> None:
        """Gère les changements d'état de connexion d'un périphérique"""
        try:
            # Obtenir les propriétés du périphérique
            device_info = await self._get_device_info(device_path)
            
            if device_info:
                event_type = "connected" if is_connected else "disconnected"
                
                # Notifier tous les callbacks
                for callback in self.device_callbacks:
                    try:
                        await callback(event_type, device_info)
                    except Exception as e:
                        self.logger.error(f"Erreur dans le callback: {e}")
        
        except Exception as e:
            self.logger.error(f"Erreur lors du traitement du changement de connexion: {e}")
    
    async def _handle_new_device(self, device_path: str, device_props: Dict[str, Any]) -> None:
        """Gère l'ajout d'un nouveau périphérique Bluetooth"""
        try:
            # Extraire la valeur "Connected" du dictionnaire de propriétés
            is_connected = device_props.get('Connected')
            if isinstance(is_connected, Variant):
                is_connected = is_connected.value
                
            # Si le périphérique est déjà connecté
            if is_connected:
                device_info = await self._get_device_info(device_path)
                
                if device_info:
                    # Notifier tous les callbacks
                    for callback in self.device_callbacks:
                        try:
                            await callback("connected", device_info)
                        except Exception as e:
                            self.logger.error(f"Erreur dans le callback: {e}")
        
        except Exception as e:
            self.logger.error(f"Erreur lors du traitement d'un nouveau périphérique: {e}")
    
    async def _handle_device_removed(self, device_path: str) -> None:
        """Gère la suppression d'un périphérique Bluetooth"""
        # Cette méthode pourrait être étendue si nécessaire
        self.logger.debug(f"Périphérique supprimé: {device_path}")
    
    async def _handle_new_pcm(self, path: str, properties: Dict[str, Any]) -> None:
        """Gère l'ajout d'un nouveau PCM BlueALSA"""
        try:
            self.logger.info(f"Nouveau PCM BlueALSA détecté: {path}")
            
            # Extraire les informations essentielles
            device_path = properties.get('Device')
            if isinstance(device_path, Variant):
                device_path = device_path.value
                
            transport = properties.get('Transport')
            if isinstance(transport, Variant):
                transport = transport.value
                
            mode = properties.get('Mode')
            if isinstance(mode, Variant):
                mode = mode.value
                
            self.logger.info(f"PCM détails: Device={device_path}, Transport={transport}, Mode={mode}")
            
            # Convertir le chemin du périphérique en adresse
            if device_path and device_path.startswith('/org/bluez/'):
                # Extraire l'adresse du chemin
                parts = device_path.split('/')
                if len(parts) > 4:
                    device_part = parts[4]  # dev_XX_XX_XX_XX_XX_XX
                    if device_part.startswith('dev_'):
                        address = device_part[4:].replace('_', ':')
                        
                        # Si c'est un PCM A2DP source (celui qui nous intéresse)
                        if transport and 'A2DP' in transport and mode == 'source':
                            # Notifier l'appareil connecté avec A2DP
                            device_name = await self._get_device_name(device_path)
                            self.logger.info(f"PCM A2DP source trouvé pour {device_name} ({address})")
                            
                            # Créer les infos de l'appareil
                            device_info = {
                                'address': address,
                                'name': device_name,
                                'path': device_path,
                                'a2dp_sink_support': True  # On sait que c'est supporté puisqu'on a un PCM A2DP
                            }
                            
                            # Notifier les callbacks - comme un nouvel appareil connecté
                            for callback in self.device_callbacks:
                                try:
                                    await callback("connected", device_info)
                                except Exception as e:
                                    self.logger.error(f"Erreur dans le callback PCM: {e}")
        except Exception as e:
            self.logger.error(f"Erreur lors du traitement du nouveau PCM: {e}")
    
    async def _get_device_name(self, device_path: str) -> str:
        """Récupère le nom d'un périphérique à partir de son chemin"""
        try:
            # Obtenir l'interface Properties
            introspect = await self.bus.introspect('org.bluez', device_path)
            device_proxy = self.bus.get_proxy_object('org.bluez', device_path, introspect)
            props_interface = device_proxy.get_interface('org.freedesktop.DBus.Properties')
            
            # Récupérer le nom
            name_variant = await props_interface.call_get('org.bluez.Device1', 'Name')
            if isinstance(name_variant, Variant):
                return name_variant.value
            return str(name_variant)
        except Exception as e:
            self.logger.error(f"Erreur lors de la récupération du nom: {e}")
            return "Unknown Device"
    
    async def _get_device_info(self, device_path: str) -> Optional[Dict[str, Any]]:
        """Récupère les informations d'un périphérique Bluetooth"""
        try:
            # Obtenir l'interface Properties pour le périphérique
            introspect = await self.bus.introspect('org.bluez', device_path)
            device_proxy = self.bus.get_proxy_object('org.bluez', device_path, introspect)
            props_interface = device_proxy.get_interface('org.freedesktop.DBus.Properties')
            
            # Récupérer les propriétés essentielles
            address_variant = await props_interface.call_get('org.bluez.Device1', 'Address')
            address = address_variant.value if isinstance(address_variant, Variant) else address_variant
            
            device_info = {
                'address': address,
                'path': device_path
            }
            
            # Propriétés supplémentaires si disponibles
            try:
                name_variant = await props_interface.call_get('org.bluez.Device1', 'Name')
                device_info['name'] = name_variant.value if isinstance(name_variant, Variant) else name_variant
            except:
                device_info['name'] = "Unknown Device"
            
            try:
                uuids_variant = await props_interface.call_get('org.bluez.Device1', 'UUIDs')
                uuids = uuids_variant.value if isinstance(uuids_variant, Variant) else uuids_variant
                device_info['a2dp_sink_support'] = "0000110d-0000-1000-8000-00805f9b34fb" in uuids
            except:
                device_info['a2dp_sink_support'] = False
            
            return device_info
        
        except Exception as e:
            self.logger.error(f"Erreur lors de la récupération des infos du périphérique: {e}")
            return None
    
    async def _check_existing_devices(self) -> None:
        """Vérifie les périphériques déjà connectés"""
        try:
            # Obtenir tous les objets
            introspect = await self.bus.introspect('org.bluez', '/')
            object_manager = self.bus.get_proxy_object('org.bluez', '/', introspect)
            manager_interface = object_manager.get_interface('org.freedesktop.DBus.ObjectManager')
            
            objects = await manager_interface.call_get_managed_objects()
            
            # Vérifier chaque périphérique déjà connecté
            for path, interfaces in objects.items():
                if 'org.bluez.Device1' in interfaces:
                    connected = interfaces['org.bluez.Device1'].get('Connected')
                    if isinstance(connected, Variant):
                        connected = connected.value
                        
                    if connected:
                        await self._handle_new_device(path, interfaces['org.bluez.Device1'])
        
        except Exception as e:
            self.logger.error(f"Erreur lors de la vérification des périphériques existants: {e}")
    
    async def configure_adapter(self, name: str, discoverable: bool = True, 
                             discoverable_timeout: int = 0, pairable: bool = True,
                             pairable_timeout: int = 0) -> bool:
        """Configure l'adaptateur Bluetooth"""
        if not self.adapter_interface:
            self.logger.error("Adaptateur Bluetooth non initialisé")
            return False
            
        try:
            # Configurer l'adaptateur avec les paramètres en spécifiant explicitement les types
            await self.adapter_interface.call_set('org.bluez.Adapter1', 'Alias', Variant('s', name))
            await self.adapter_interface.call_set('org.bluez.Adapter1', 'Discoverable', Variant('b', discoverable))
            await self.adapter_interface.call_set('org.bluez.Adapter1', 'DiscoverableTimeout', Variant('u', discoverable_timeout))
            await self.adapter_interface.call_set('org.bluez.Adapter1', 'Pairable', Variant('b', pairable))
            await self.adapter_interface.call_set('org.bluez.Adapter1', 'PairableTimeout', Variant('u', pairable_timeout))
            
            # Obtenir le nom de l'adaptateur (hci0, etc.)
            adapter_name = self.adapter_path.split('/')[-1]
            
            # Configurer la classe de périphérique via hciconfig (commande externe)
            try:
                # Exécuter les commandes hciconfig en parallèle
                await asyncio.gather(
                    asyncio.create_subprocess_exec(
                        "sudo", "hciconfig", adapter_name, "class", "0x240404",
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL
                    ),
                    asyncio.create_subprocess_exec(
                        "sudo", "hciconfig", adapter_name, "name", "oakOS",
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL
                    )
                )
            except Exception as e:
                self.logger.warning(f"Impossible de définir la classe de périphérique: {e}")
            
            self.logger.info(f"Adaptateur {self.adapter_path} configuré avec succès")
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur lors de la configuration de l'adaptateur: {e}")
            return False
    
    async def get_connected_devices(self) -> List[Dict[str, Any]]:
        """Récupère la liste des périphériques Bluetooth connectés"""
        connected_devices = []
        
        try:
            # Obtenir tous les objets
            introspect = await self.bus.introspect('org.bluez', '/')
            object_manager = self.bus.get_proxy_object('org.bluez', '/', introspect)
            manager_interface = object_manager.get_interface('org.freedesktop.DBus.ObjectManager')
            
            objects = await manager_interface.call_get_managed_objects()
            
            # Filtrer les périphériques connectés
            for path, interfaces in objects.items():
                if 'org.bluez.Device1' in interfaces:
                    connected = interfaces['org.bluez.Device1'].get('Connected')
                    if isinstance(connected, Variant):
                        connected = connected.value
                        
                    if connected:
                        device_info = await self._get_device_info(path)
                        if device_info:
                            connected_devices.append(device_info)
            
            return connected_devices
            
        except Exception as e:
            self.logger.error(f"Erreur lors de la récupération des périphériques connectés: {e}")
            return []
    
    async def disconnect_device(self, address: str) -> bool:
        """Déconnecte un périphérique Bluetooth"""
        try:
            # Obtenir tous les objets
            introspect = await self.bus.introspect('org.bluez', '/')
            object_manager = self.bus.get_proxy_object('org.bluez', '/', introspect)
            manager_interface = object_manager.get_interface('org.freedesktop.DBus.ObjectManager')
            
            objects = await manager_interface.call_get_managed_objects()
            
            # Trouver le périphérique par son adresse
            device_path = None
            for path, interfaces in objects.items():
                if 'org.bluez.Device1' in interfaces:
                    device_address = interfaces['org.bluez.Device1'].get('Address')
                    if isinstance(device_address, Variant):
                        device_address = device_address.value
                        
                    if device_address == address:
                        device_path = path
                        break
            
            if not device_path:
                self.logger.error(f"Périphérique {address} non trouvé")
                return False
            
            # Obtenir l'interface Device1
            device_introspect = await self.bus.introspect('org.bluez', device_path)
            device_proxy = self.bus.get_proxy_object('org.bluez', device_path, device_introspect)
            device_interface = device_proxy.get_interface('org.bluez.Device1')
            
            # Déconnecter le périphérique
            await device_interface.call_disconnect()
            self.logger.info(f"Périphérique {address} déconnecté avec succès")
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur lors de la déconnexion du périphérique {address}: {e}")
            return False