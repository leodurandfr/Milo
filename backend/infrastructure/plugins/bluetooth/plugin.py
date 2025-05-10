"""
Plugin Bluetooth entièrement asynchrone et optimisé pour oakOS
"""
import asyncio
import logging
from typing import Dict, Any, List, Optional

from backend.application.event_bus import EventBus
from backend.infrastructure.plugins.base import UnifiedAudioPlugin
from backend.domain.audio_state import PluginState
from backend.infrastructure.plugins.bluetooth.agent import BluetoothAgent
from backend.infrastructure.plugins.bluetooth.dbus_manager import BluetoothDBusManager
from backend.infrastructure.plugins.bluetooth.alsa_manager import AlsaManager
from backend.infrastructure.plugins.bluetooth.service_manager import BluetoothServiceManager

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
        self.service_manager = BluetoothServiceManager()
    
    async def initialize(self) -> bool:
        """Initialisation du plugin Bluetooth"""
        self.logger.info("Initialisation du plugin Bluetooth")
        
        try:
            # Initialiser les gestionnaires
            self.dbus_manager.register_device_callback(self._on_device_event)
            self._initialized = True
            return True
        except Exception as e:
            self.logger.error(f"Erreur lors de l'initialisation du plugin Bluetooth: {e}")
            return False
    
    async def _on_device_event(self, event_type: str, device_info: Dict[str, Any]) -> None:
        """Callback pour les événements de périphérique Bluetooth"""
        self.logger.info(f"Événement Bluetooth: {event_type} - {device_info.get('name')} ({device_info.get('address')})")
        
        # Ignorer les événements pendant une déconnexion contrôlée
        if event_type == "disconnected" and self._disconnecting:
            self.logger.info("Ignorant l'événement de déconnexion pendant un arrêt contrôlé")
            return
        
        if event_type == "connected":
            # Gérer la connexion d'un nouvel appareil A2DP
            if device_info.get('a2dp_sink_support', False):
                if not self.current_device:
                    await self._connect_device(device_info.get('address'), device_info.get('name'))
        
        elif event_type == "disconnected":
            # Gérer la déconnexion de l'appareil actuel
            if self.current_device and self.current_device.get('address') == device_info.get('address'):
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
            success = await self.service_manager.start_audio_playback(address)
            
            if success:
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
            else:
                self.logger.error(f"Échec du démarrage de la lecture pour {address}")
                self.current_device = None
                return False
                
        except Exception as e:
            self.logger.error(f"Erreur connexion appareil: {e}")
            self.current_device = None
            return False
    
    async def _handle_device_disconnected(self, address: str, name: str) -> None:
        """Gère la déconnexion d'un appareil"""
        try:
            self.logger.info(f"Traitement déconnexion de l'appareil {name} ({address})")
            await self.service_manager.stop_audio_playback(address)
            self.current_device = None
            await self.notify_state_change(PluginState.READY, {"device_connected": False})
        except Exception as e:
            self.logger.error(f"Erreur traitement déconnexion: {e}")
    
    async def start(self) -> bool:
        """Démarre les services nécessaires pour le plugin Bluetooth"""
        try:
            self.logger.info("Démarrage du plugin Bluetooth")
            
            # 1. Démarrer bluetooth (prérequis pour bluealsa)
            if not await self.service_manager.start_bluetooth():
                raise RuntimeError("Impossible de démarrer le service bluetooth")
                
            # 2. Attendre un peu que BlueZ soit complètement démarré
            await asyncio.sleep(1)
            
            # 3. Initialiser la connexion D-Bus et configurer les signaux avec timeout
            try:
                async with asyncio.timeout(15):  # Timeout global pour l'initialisation D-Bus
                    # Initialiser le gestionnaire D-Bus
                    if not await self.dbus_manager.initialize():
                        raise RuntimeError("Échec de l'initialisation D-Bus")
                    
                    # Configurer les signaux D-Bus séquentiellement
                    await self.dbus_manager._setup_property_changed_signal()
                    await self.dbus_manager._setup_object_manager_signals()
                    await self.dbus_manager._discover_adapter()
                    
                    # 4. Configurer l'adaptateur
                    await self.dbus_manager.configure_adapter("oakOS", True, 0, True, 0)
                    
                    # 5. Démarrer BlueALSA
                    if not await self.service_manager.start_bluealsa():
                        raise RuntimeError("Impossible de démarrer le service BlueALSA")
                    
                    # 6. Configurer la surveillance des PCMs BlueALSA
                    await self.dbus_manager._setup_bluealsa_signals()
            except asyncio.TimeoutError:
                raise RuntimeError("Timeout lors de l'initialisation D-Bus")
                
            # 7. Créer et enregistrer l'agent Bluetooth
            try:
                self.agent = BluetoothAgent(self.dbus_manager.bus)
                await asyncio.wait_for(self.agent.register(), 5.0)
            except (asyncio.TimeoutError, Exception) as e:
                self.logger.warning(f"Échec de l'enregistrement de l'agent Bluetooth: {e}")
                # Non critique, continuer
                
            # 8. Vérifier les périphériques existants
            try:
                await asyncio.wait_for(self.dbus_manager._check_existing_devices(), 5.0)
            except asyncio.TimeoutError:
                self.logger.warning("Timeout lors de la vérification des périphériques existants")
                # Non critique, continuer
                
            # 9. Notifier l'état prêt
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
            
            # Définir le drapeau pour éviter les événements pendant l'arrêt
            self._disconnecting = True
            
            # Si un appareil est connecté, le déconnecter proprement
            if self.current_device:
                address = self.current_device.get("address")
                self.logger.info(f"Déconnexion de l'appareil {address} pendant l'arrêt")
                try:
                    # Arrêter d'abord l'audio
                    await self.service_manager.stop_audio_playback(address)
                    
                    # Déconnecter l'appareil via D-Bus
                    await asyncio.wait_for(
                        self.dbus_manager.disconnect_device(address), 
                        5.0
                    )
                    
                    # Courte attente pour la déconnexion
                    await asyncio.sleep(0.5)
                except Exception as e:
                    self.logger.warning(f"Erreur lors de la déconnexion: {e}")
            
            # Désenregistrer l'agent Bluetooth
            if self.agent:
                try:
                    await asyncio.wait_for(self.agent.unregister(), 5.0)
                except Exception as e:
                    self.logger.warning(f"Erreur lors du désenregistrement de l'agent: {e}")
                self.agent = None
            
            # Arrêter le service bluealsa
            await self.service_manager.stop_bluealsa()
            
            # Désactiver la découvrabilité
            try:
                proc = await asyncio.create_subprocess_exec(
                    "bluetoothctl", 
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
                )
                
                commands = b"discoverable off\npairable off\nquit\n"
                await asyncio.wait_for(proc.communicate(input=commands), 3.0)
            except Exception as e:
                self.logger.warning(f"Erreur désactivation découvrabilité: {e}")
            
            # Arrêter le service Bluetooth si configuré
            if self.stop_bluetooth:
                await self.service_manager.stop_bluetooth()
            
            # Réinitialiser l'état
            self.current_device = None
            await self.notify_state_change(PluginState.INACTIVE)
            
            return True
        except Exception as e:
            self.logger.error(f"Erreur arrêt Bluetooth: {e}")
            return False
        finally:
            # Réinitialiser le drapeau
            self._disconnecting = False
    
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
                
                try:
                    # Arrêter d'abord la lecture audio
                    await self.service_manager.stop_audio_playback(address)
                    
                    # Déconnecter l'appareil via D-Bus
                    success = await asyncio.wait_for(
                        self.dbus_manager.disconnect_device(address),
                        5.0
                    )
                    
                    if not success:
                        error_msg = "Échec de la déconnexion D-Bus"
                        self.logger.error(error_msg)
                        return {"success": False, "error": error_msg}
                    
                    # Mettre à jour l'état interne
                    self.current_device = None
                    await self.notify_state_change(PluginState.READY, {"device_connected": False})
                    
                    return {"success": True, "message": f"Appareil {name} déconnecté avec succès"}
                finally:
                    # Réinitialiser l'indicateur
                    self._disconnecting = False
                
            elif command == "restart_audio":
                if not self.current_device:
                    return {"success": False, "error": "Aucun périphérique connecté"}
                
                address = self.current_device.get("address")
                await self.service_manager.stop_audio_playback(address)
                success = await self.service_manager.start_audio_playback(address)
                
                return {
                    "success": success, 
                    "message": "Lecture audio redémarrée" if success else "Échec du redémarrage audio"
                }
                
            elif command == "restart_bluealsa":
                result = await self.service_manager.restart_bluealsa()
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
                self.service_manager.is_service_active("bluetooth.service"),
                self.service_manager.is_service_active("bluealsa.service"),
                self._check_playback_active() if self.current_device else asyncio.sleep(0, result=False)
            )
            
            return {
                "device_connected": self.current_device is not None,
                "device_name": self.current_device.get("name") if self.current_device else None,
                "device_address": self.current_device.get("address") if self.current_device else None,
                "bluetooth_running": bt_check,
                "bluealsa_running": bluealsa_check,
                "playback_running": playback_check,
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
        return await self.service_manager.is_service_active(service_name)
    
    def manages_own_process(self) -> bool:
        """Le plugin gère ses propres processus"""
        return True
    
    def get_process_command(self) -> List[str]:
        """Non utilisé (manages_own_process = True)"""
        return ["true"]
    
    async def get_initial_state(self) -> Dict[str, Any]:
        """État initial du plugin"""
        return await self.get_status()