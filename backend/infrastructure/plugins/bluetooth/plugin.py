"""
Plugin Bluetooth entièrement asynchrone pour oakOS
"""
import asyncio
import logging
import uuid
from typing import Dict, Any, List, Optional, Set

from dbus_next.aio import MessageBus  
from dbus_next import BusType, Variant

from backend.application.event_bus import EventBus
from backend.infrastructure.plugins.base import UnifiedAudioPlugin
from backend.domain.audio_state import PluginState
from backend.infrastructure.plugins.bluetooth.agent import BluetoothAgent
from backend.infrastructure.plugins.bluetooth.dbus_manager import BluetoothDBusManager
from backend.infrastructure.plugins.bluetooth.alsa_manager import AlsaManager

class BluetoothPlugin(UnifiedAudioPlugin):
    """Plugin Bluetooth pour la réception audio via A2DP"""
    
    def __init__(self, event_bus: EventBus, config: Dict[str, Any]):
        super().__init__(event_bus, "bluetooth")
        self.config = config
        self.current_device = None
        self._initialized = False
        self._disconnecting = False
        
        # Configuration
        self.daemon_options = config.get("daemon_options", "--keep-alive=5 --initial-volume=80")
        self.stop_bluetooth = config.get("stop_bluetooth_on_exit", True)
        
        # Composants du plugin
        self.dbus_manager = BluetoothDBusManager()
        self.agent = None
        self.alsa_manager = AlsaManager()
        
        # Services surveillés
        self.bluealsa_running = False
        self.bluetooth_running = False
        self.bluealsa_aplay_running = False
    
    async def initialize(self) -> bool:
        """Initialisation du plugin Bluetooth"""
        self.logger.info("Initialisation du plugin Bluetooth")
        
        # Dans initialize, nous ne voulons que créer les objets, pas vérifier les services
        # car le service bluetooth pourrait ne pas être démarré à ce stade
        try:
            # Initialiser les gestionnaires sans vérifier les services
            self.dbus_manager = BluetoothDBusManager()
            self.alsa_manager = AlsaManager()
            
            # Enregistrer le callback pour les événements de périphérique (sera utilisé plus tard)
            self.dbus_manager.register_device_callback(self._on_device_event)
            
            self._initialized = True
            return True
        except Exception as e:
            self.logger.error(f"Erreur lors de l'initialisation du plugin Bluetooth: {e}")
            return False
    
    async def _on_device_event(self, event_type: str, device_info: Dict[str, Any]) -> None:
        """Callback pour les événements de périphérique Bluetooth"""
        self.logger.info(f"Événement Bluetooth: {event_type} - {device_info.get('name')} ({device_info.get('address')})")
        
        if event_type == "connected":
            # Vérifier si le périphérique est compatible A2DP
            if device_info.get('a2dp_sink_support', False):
                # Si aucun périphérique n'est actuellement connecté, utiliser celui-ci
                if not self.current_device:
                    await self._connect_device(device_info.get('address'), device_info.get('name'))
        
        elif event_type == "disconnected":
            # Vérifier si c'est le périphérique actuel qui s'est déconnecté
            if self.current_device and self.current_device.get('address') == device_info.get('address'):
                if not self._disconnecting:  # Éviter les actions redondantes lors d'une déconnexion manuelle
                    await self._handle_device_disconnected(device_info.get('address'), device_info.get('name'))
    
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
            self.logger.info(f"Traitement déconnexion de l'appareil {name} ({address})")
            await self._stop_audio_playback()
            self.current_device = None
            await self.notify_state_change(PluginState.READY, {"device_connected": False})
        except Exception as e:
            self.logger.error(f"Erreur traitement déconnexion: {e}")
    
    async def _manage_service(self, service_name: str, action: str = "start") -> bool:
        """Gère les opérations de service systemd"""
        try:
            self.logger.info(f"{action.capitalize()} du service {service_name}")
            
            proc = await asyncio.create_subprocess_exec(
                "sudo", "systemctl", action, service_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            
            _, stderr = await proc.communicate()
            
            if proc.returncode != 0:
                error_msg = stderr.decode().strip()
                self.logger.error(f"Échec de {action} pour {service_name}: {error_msg}")
                return False
                
            # Pour les opérations de démarrage, attendre que le service soit actif
            if action == "start":
                for _ in range(10):  # 10 tentatives avec 300ms d'intervalle = max 3 sec
                    if await self._is_service_active(service_name):
                        break
                    await asyncio.sleep(0.3)
                else:
                    self.logger.warning(f"Le service {service_name} ne semble pas complètement démarré")
                    return False
                    
            # Mettre à jour l'état de surveillance des services
            if service_name == "bluetooth.service":
                self.bluetooth_running = (action == "start")
            elif service_name == "bluealsa.service":
                self.bluealsa_running = (action == "start")
            elif "bluealsa-aplay@" in service_name:
                self.bluealsa_aplay_running = (action == "start")
                    
            return True
        except Exception as e:
            self.logger.error(f"Erreur {action} service {service_name}: {e}")
            return False
    
    async def _is_service_active(self, service_name: str) -> bool:
        """Vérifie si un service systemd est actif"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "is-active", service_name, 
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
            
            stdout, _ = await proc.communicate()
            return stdout.decode().strip() == "active"
        except Exception as e:
            self.logger.error(f"Erreur vérification service {service_name}: {e}")
            return False
    
    async def _stop_audio_playback(self) -> bool:
        """Arrête la lecture audio en cours"""
        if not self.current_device:
            return True
            
        try:
            address = self.current_device.get("address")
            return await self._manage_service(f"bluealsa-aplay@{address}.service", "stop")
        except Exception as e:
            self.logger.error(f"Erreur arrêt audio: {e}")
            return False
    
    async def _start_audio_playback(self, device_address: str) -> bool:
        """Démarre la lecture audio"""
        try:
            # Arrêter tout service existant d'abord
            await self._stop_audio_playback()
            
            # Démarrer le service
            service_name = f"bluealsa-aplay@{device_address}.service"
            if await self._manage_service(service_name):
                self.logger.info(f"Lecture audio démarrée avec succès pour {device_address}")
                return True
            else:
                self.logger.error(f"Échec du démarrage de la lecture audio pour {device_address}")
                return False
        except Exception as e:
            self.logger.error(f"Erreur démarrage lecture: {e}")
            return False
    
    async def start(self) -> bool:
        """Démarre les services nécessaires pour le plugin Bluetooth"""
        try:
            self.logger.info("Démarrage du plugin Bluetooth")
            
            # 1. Démarrer bluetooth (prérequis pour bluealsa)
            if not await self._manage_service("bluetooth.service"):
                raise RuntimeError("Impossible de démarrer le service bluetooth")
                
            # 2. Attendre un peu que BlueZ soit complètement démarré
            await asyncio.sleep(1)
                
            # 3. Initialiser la connexion D-Bus et configurer les signaux
            try:
                # Initialiser le gestionnaire D-Bus maintenant que les services sont démarrés
                if not await self.dbus_manager.initialize():
                    raise RuntimeError("Échec de l'initialisation D-Bus après démarrage des services")
                
                # Configuration des signaux et découverte de l'adaptateur
                await self.dbus_manager._setup_property_changed_signal()
                await self.dbus_manager._setup_object_manager_signals()
                await self.dbus_manager._discover_adapter()
                
                # 4. Configurer l'adaptateur et démarrer bluealsa en parallèle
                await asyncio.gather(
                    self.dbus_manager.configure_adapter("oakOS", True, 0, True, 0),
                    self._manage_service("bluealsa.service")
                )
                
                # 5. Configurer la surveillance des PCMs BlueALSA
                await self.dbus_manager._setup_bluealsa_signals()
                
            except Exception as e:
                self.logger.error(f"Erreur lors de l'initialisation D-Bus après démarrage de bluetooth: {e}")
                raise RuntimeError(f"Échec de la configuration D-Bus: {e}")
                
            # 6. Créer et enregistrer l'agent Bluetooth
            self.agent = BluetoothAgent(self.dbus_manager.bus)
            if not await self.agent.register():
                self.logger.error("Échec de l'enregistrement de l'agent Bluetooth")
                # Continuer malgré l'échec (non critique)
                
            # 7. Vérifier les périphériques existants
            await self.dbus_manager._check_existing_devices()
                
            # 8. Notifier l'état prêt
            await self.notify_state_change(PluginState.READY)
                
            self.logger.info("Plugin Bluetooth démarré avec succès")
            return True
        except Exception as e:
            self.logger.error(f"Erreur démarrage Bluetooth: {e}")
            await self.notify_state_change(PluginState.ERROR, {"error": str(e)})
            return False
    
    async def stop(self) -> bool:
        """Arrête tous les services Bluetooth"""
        try:
            self.logger.info("Arrêt du plugin Bluetooth")
            
            # 1. Arrêter l'audio
            await self._stop_audio_playback()
            
            # 2. Désenregistrer l'agent Bluetooth
            if self.agent:
                await self.agent.unregister()
                self.agent = None
            
            # 3. Arrêter le service bluealsa
            await self._manage_service("bluealsa.service", "stop")
            
            # 4. Désactiver la découvrabilité
            try:
                # Commander bluetoothctl en mode batch
                proc = await asyncio.create_subprocess_exec(
                    "bluetoothctl", 
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
                )
                
                commands = b"discoverable off\npairable off\nquit\n"
                await proc.communicate(input=commands)
            except Exception as e:
                self.logger.warning(f"Erreur désactivation découvrabilité: {e}")
            
            # 5. Arrêter le service Bluetooth si configuré
            if self.stop_bluetooth:
                await self._manage_service("bluetooth.service", "stop")
            
            # 6. Réinitialiser l'état
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
                
                # Déconnecter l'appareil via D-Bus
                self.logger.info(f"Déconnexion manuelle de l'appareil {name} ({address})")
                success = await self.dbus_manager.disconnect_device(address)
                
                if not success:
                    error_msg = "Échec de la déconnexion D-Bus"
                    self.logger.error(error_msg)
                    self._disconnecting = False
                    return {"success": False, "error": error_msg}
                
                # Mettre à jour l'état interne
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
                success = await self._start_audio_playback(address)
                
                return {
                    "success": success, 
                    "message": "Lecture audio redémarrée" if success else "Échec du redémarrage audio"
                }
                
            elif command == "restart_bluealsa":
                result = await self._manage_service("bluealsa.service", "restart")
                return {
                    "success": result, 
                    "message": "Service BlueALSA redémarré avec succès" if result else "Échec du redémarrage"
                }
                
            elif command == "set_stop_bluetooth":
                value = bool(data.get("value", False))
                self.stop_bluetooth = value
                return {"success": True, "stop_bluetooth": self.stop_bluetooth}
                
            elif command == "check_pcms" or command == "list_pcms":
                pcms = await self.alsa_manager.get_bluealsa_pcms()
                return {"success": True, "pcms": pcms}
            
            return {"success": False, "error": f"Commande inconnue: {command}"}
        except Exception as e:
            self.logger.error(f"Erreur dans handle_command: {e}")
            return {"success": False, "error": str(e)}
    
    async def get_status(self) -> Dict[str, Any]:
        """État actuel du plugin"""
        try:
            # Vérifier l'état des services en parallèle
            bt_check, bluealsa_check, playback_check = await asyncio.gather(
                self._is_service_active("bluetooth.service"),
                self._is_service_active("bluealsa.service"),
                self._check_playback_active() if self.current_device else asyncio.sleep(0)
            )
            
            self.bluetooth_running = bt_check
            self.bluealsa_running = bluealsa_check
            self.bluealsa_aplay_running = playback_check if self.current_device else False
            
            return {
                "device_connected": self.current_device is not None,
                "device_name": self.current_device.get("name") if self.current_device else None,
                "device_address": self.current_device.get("address") if self.current_device else None,
                "bluetooth_running": self.bluetooth_running,
                "bluealsa_running": self.bluealsa_running,
                "playback_running": self.bluealsa_aplay_running,
                "stop_bluetooth_on_exit": self.stop_bluetooth
            }
        except Exception as e:
            self.logger.error(f"Erreur dans get_status: {e}")
            return {
                "device_connected": False,
                "error": str(e)
            }
    
    async def _check_playback_active(self) -> bool:
        """Vérifie si la lecture audio est active pour le périphérique actuel"""
        if not self.current_device:
            return False
            
        service_name = f"bluealsa-aplay@{self.current_device['address']}.service"
        return await self._is_service_active(service_name)
    
    def manages_own_process(self) -> bool:
        """Le plugin gère ses propres processus"""
        return True
    
    def get_process_command(self) -> List[str]:
        """Non utilisé (manages_own_process = True)"""
        return ["true"]
    
    async def get_initial_state(self) -> Dict[str, Any]:
        """État initial du plugin"""
        return await self.get_status()