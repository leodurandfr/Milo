"""
Plugin Bluetooth simplifié pour oakOS utilisant bluealsa-cli et bluealsa-aplay avec systemd
"""
import asyncio
import logging
import shutil
from typing import Dict, Any, List, Optional

from backend.infrastructure.plugins.base import UnifiedAudioPlugin
from backend.domain.audio_state import PluginState
from backend.infrastructure.services.systemd_manager import SystemdServiceManager
from backend.infrastructure.plugins.bluetooth.bluealsa_monitor import BlueAlsaMonitor
from backend.infrastructure.plugins.bluetooth.bluealsa_playback import BlueAlsaPlayback
from backend.infrastructure.plugins.bluetooth.agent import BluetoothAgent

class BluetoothPlugin(UnifiedAudioPlugin):
    """Plugin Bluetooth pour oakOS"""
    
    def __init__(self, event_bus, config: Dict[str, Any]):
        super().__init__(event_bus, "bluetooth")
        self.config = config
        self.bluetooth_service = config.get("bluetooth_service", "bluetooth.service")
        self.bluealsa_service = config.get("service_name", "bluealsa.service")
        self.service_manager = SystemdServiceManager()
        self.monitor = BlueAlsaMonitor()
        self.playback = BlueAlsaPlayback()
        self.agent = BluetoothAgent()
        
        # Configuration
        self.stop_bluetooth = config.get("stop_bluetooth_on_exit", True)
        self.auto_agent = config.get("auto_agent", True)  # Activer l'agent par défaut
        
        # Etat interne
        self.current_device = None
    
    async def initialize(self) -> bool:
        """Initialise le plugin Bluetooth"""
        self.logger.info("Initialisation du plugin Bluetooth")
        
        # Vérifier les dépendances essentielles
        if not shutil.which("bluealsa-cli") or not shutil.which("bluealsa-aplay"):
            self.logger.error("Dépendances manquantes: bluealsa-cli ou bluealsa-aplay")
            return False
        
        # Configurer les callbacks du moniteur
        self.monitor.set_callbacks(
            self._on_device_connected,
            self._on_device_disconnected
        )
        
        return True
    
    async def start(self) -> bool:
        """Démarre le plugin Bluetooth"""
        try:
            self.logger.info("Démarrage du plugin Bluetooth")
            
            # 1. Démarrer bluetooth
            if not await self.service_manager.start(self.bluetooth_service):
                raise RuntimeError("Impossible de démarrer le service bluetooth")
            
            # 2. Démarrer bluealsa
            if not await self.service_manager.start(self.bluealsa_service):
                raise RuntimeError("Impossible de démarrer le service bluealsa")
            
            # 3. Configurer l'adaptateur Bluetooth
            await self._configure_adapter()
            
            # 4. Enregistrer l'agent Bluetooth si activé
            if self.auto_agent:
                if not await self.agent.register():
                    self.logger.warning("Impossible d'enregistrer l'agent Bluetooth, continuera sans acceptation automatique")
            
            # 5. Démarrer la surveillance des PCMs
            if not await self.monitor.start_monitoring():
                raise RuntimeError("Impossible de démarrer la surveillance BlueALSA")
            
            # 6. Notifier l'état prêt
            await self.notify_state_change(PluginState.READY)
            
            self.logger.info("Plugin Bluetooth démarré avec succès")
            return True
        except Exception as e:
            self.logger.error(f"Erreur démarrage plugin Bluetooth: {e}")
            await self.notify_state_change(PluginState.ERROR, {"error": str(e)})
            await self._cleanup()
            return False
    
    async def _configure_adapter(self) -> bool:
        """Configure l'adaptateur Bluetooth"""
        try:
            commands = [
                "power on",
                "discoverable on",
                "pairable on",
                "class 0x240404",  # Audio/Video Device
                "quit"
            ]
            
            process = await asyncio.create_subprocess_exec(
                "bluetoothctl",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            
            await process.communicate(input="\n".join(commands).encode())
            return True
        except Exception as e:
            self.logger.error(f"Erreur configuration adaptateur: {e}")
            return False
    
    async def stop(self) -> bool:
        """Arrête le plugin Bluetooth"""
        try:
            self.logger.info("Arrêt du plugin Bluetooth")
            
            # Nettoyer les ressources
            await self._cleanup()
            
            # Désactiver la découvrabilité
            try:
                commands = [
                    "discoverable off",
                    "pairable off",
                    "quit"
                ]
                
                process = await asyncio.create_subprocess_exec(
                    "bluetoothctl",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
                )
                
                await process.communicate(input="\n".join(commands).encode())
            except Exception as e:
                self.logger.warning(f"Erreur désactivation découvrabilité: {e}")
            
            # Arrêter les services si configuré
            if self.stop_bluetooth:
                await self.service_manager.stop(self.bluealsa_service)
                await self.service_manager.stop(self.bluetooth_service)
            
            # Réinitialiser l'état
            self.current_device = None
            await self.notify_state_change(PluginState.INACTIVE)
            
            return True
        except Exception as e:
            self.logger.error(f"Erreur arrêt plugin Bluetooth: {e}")
            return False
    
    async def _cleanup(self) -> None:
        """Nettoie les ressources du plugin"""
        # Arrêter toute lecture audio
        await self.playback.stop_all_playback()
        
        # Arrêter la surveillance
        await self.monitor.stop_monitoring()
        
        # Désenregistrer l'agent Bluetooth
        if self.auto_agent:
            await self.agent.unregister()
    
    async def _on_device_connected(self, address: str, name: str) -> None:
        """Callback appelé lorsqu'un appareil est connecté"""
        self.logger.info(f"Appareil Bluetooth connecté: {name} ({address})")
        
        # Si aucun appareil n'est actuellement connecté, utiliser celui-ci
        if not self.current_device:
            # Enregistrer l'appareil et notifier immédiatement
            self.current_device = {
                "address": address,
                "name": name
            }
            
            # Notifier tout de suite pour mettre à jour l'interface
            await self.notify_state_change(
                PluginState.CONNECTED, 
                {
                    "device_connected": True,
                    "device_name": name,
                    "device_address": address
                }
            )
            
            # Démarrer la lecture audio (appel au code existant)
            await self.playback.start_playback(address)
    
    async def _on_device_disconnected(self, address: str, name: str) -> None:
        """Callback appelé lorsqu'un appareil est déconnecté"""
        self.logger.info(f"Appareil Bluetooth déconnecté: {name} ({address})")
        
        # Si c'est l'appareil actuellement utilisé, le déconnecter
        if self.current_device and self.current_device.get("address") == address:
            await self._handle_device_disconnected(address, name)
    
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
            success = await self.playback.start_playback(address)
            
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
            
            # Arrêter la lecture audio
            await self.playback.stop_playback(address)
            
            # Réinitialiser l'appareil courant
            self.current_device = None
            
            # Notifier le changement d'état
            await self.notify_state_change(PluginState.READY, {"device_connected": False})
        except Exception as e:
            self.logger.error(f"Erreur traitement déconnexion: {e}")
    
    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite les commandes pour le plugin"""
        try:
            if command == "disconnect":
                if not self.current_device:
                    return {"success": False, "error": "Aucun périphérique connecté"}
                
                address = self.current_device.get("address")
                name = self.current_device.get("name", "Appareil inconnu")
                
                # Déconnecter l'appareil via bluetoothctl
                process = await asyncio.create_subprocess_exec(
                    "bluetoothctl", "disconnect", address,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE
                )
                
                _, stderr = await process.communicate()
                
                if process.returncode != 0:
                    error_msg = stderr.decode().strip()
                    self.logger.error(f"Erreur déconnexion: {error_msg}")
                    return {"success": False, "error": error_msg}
                
                return {"success": True, "message": f"Appareil {name} en cours de déconnexion"}
                
            elif command == "restart_audio":
                if not self.current_device:
                    return {"success": False, "error": "Aucun périphérique connecté"}
                
                address = self.current_device.get("address")
                await self.playback.stop_playback(address)
                success = await self.playback.start_playback(address)
                
                return {
                    "success": success, 
                    "message": "Lecture audio redémarrée" if success else "Échec du redémarrage audio"
                }
                
            elif command == "restart_bluealsa":
                result = await self.service_manager.restart(self.bluealsa_service)
                return {
                    "success": result, 
                    "message": "Service BlueALSA redémarré avec succès" if result else "Échec du redémarrage"
                }
                
            elif command == "toggle_agent":
                # Activer/désactiver l'agent Bluetooth
                if self.auto_agent:
                    await self.agent.unregister()
                    self.auto_agent = False
                    return {"success": True, "auto_agent": False, "message": "Agent Bluetooth désactivé"}
                else:
                    success = await self.agent.register()
                    self.auto_agent = success
                    message = "Agent Bluetooth activé" if success else "Échec de l'activation de l'agent"
                    return {"success": success, "auto_agent": success, "message": message}
            
            return {"success": False, "error": f"Commande inconnue: {command}"}
        except Exception as e:
            self.logger.error(f"Erreur dans handle_command: {e}")
            return {"success": False, "error": str(e)}
    
    async def get_status(self) -> Dict[str, Any]:
        """État actuel du plugin"""
        try:
            # Vérifier l'état des services
            bt_check, bluealsa_check = await asyncio.gather(
                self.service_manager.is_active(self.bluetooth_service),
                self.service_manager.is_active(self.bluealsa_service)
            )
            
            # Vérifier si la lecture est active
            playback_active = False
            if self.current_device:
                playback_active = self.playback.is_playing(self.current_device.get("address", ""))
            
            return {
                "device_connected": self.current_device is not None,
                "device_name": self.current_device.get("name") if self.current_device else None,
                "device_address": self.current_device.get("address") if self.current_device else None,
                "bluetooth_running": bt_check,
                "bluealsa_running": bluealsa_check,
                "playback_running": playback_active,
                "stop_bluetooth_on_exit": self.stop_bluetooth,
                "auto_agent": self.auto_agent and self.agent.registered,
            }
        except Exception as e:
            self.logger.error(f"Erreur dans get_status: {e}")
            return {
                "device_connected": False,
                "error": str(e)
            }
    
    
    async def get_initial_state(self) -> Dict[str, Any]:
        """État initial du plugin"""
        return await self.get_status()