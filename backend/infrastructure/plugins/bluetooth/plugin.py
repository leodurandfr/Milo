# backend/infrastructure/plugins/bluetooth/plugin.py
"""
Plugin Bluetooth optimisé pour Milo utilisant bluealsa - Version nettoyée sans EventBus
"""
import asyncio
import subprocess
from typing import Dict, Any

from backend.infrastructure.plugins.base import UnifiedAudioPlugin
from backend.domain.audio_state import PluginState
from backend.infrastructure.plugins.bluetooth.agent import BluetoothAgent
from backend.infrastructure.plugins.bluetooth.bluealsa_monitor import BlueAlsaMonitor
from backend.infrastructure.plugins.bluetooth.bluealsa_playback import BlueAlsaPlayback

class BluetoothPlugin(UnifiedAudioPlugin):
    """Plugin Bluetooth pour Milo - version nettoyée"""
    
    def __init__(self, config: Dict[str, Any], state_machine=None):
        super().__init__("bluetooth", state_machine)
        
        # Configuration
        self.config = config
        self.bluetooth_service = config.get("bluetooth_service", "bluetooth.service")
        self.bluealsa_service = config.get("service_name", "milo-bluealsa.service")
        self.bluealsa_aplay_service = "milo-bluealsa-aplay.service"
        self.stop_bluetooth = config.get("stop_bluetooth_on_exit", True)
        self.auto_agent = config.get("auto_agent", True)
        
        # AJOUT: Définir service_name pour la classe de base
        self.service_name = self.bluealsa_service

        # État - Renommé pour éviter le conflit avec la classe de base
        self.connected_device = None
        self._auto_connecting = False
        self._current_device = "milo_bluetooth"

        # Composants
        self.agent = BluetoothAgent()
        self.monitor = BlueAlsaMonitor()
        self.playback = BlueAlsaPlayback()

        # Surveillance des connexions multiples
        self._monitoring_task = None
        self._first_connected_device = None  # Premier appareil connecté
    
    async def _do_initialize(self) -> bool:
        """Initialisation spécifique au plugin Bluetooth"""
        try:
            # Vérifier les dépendances
            if not await self._check_dependencies():
                return False
            
            # Configurer les callbacks du moniteur
            self.monitor.set_callbacks(self._on_device_connected, self._on_device_disconnected)
            
            return True
        except Exception as e:
            self.logger.error(f"Erreur initialisation Bluetooth: {e}")
            return False
    
    async def _check_dependencies(self) -> bool:
        """Vérifie que les dépendances sont disponibles"""
        for cmd in ["bluealsa-cli", "bluealsa-aplay"]:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "which", cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
                )
                await proc.wait()
                if proc.returncode != 0:
                    self.logger.error(f"Dépendance manquante: {cmd}")
                    return False
            except Exception as e:
                self.logger.error(f"Erreur vérification dépendance {cmd}: {e}")
                return False
        return True
    
    async def _do_start(self) -> bool:
        """Démarrage spécifique au plugin Bluetooth"""
        try:
            # 1. Démarrer les services
            for service in [self.bluetooth_service, self.bluealsa_service]:
                if not await self.control_service(service, "start"):
                    raise RuntimeError(f"Impossible de démarrer {service}")

            # 2. Démarrer le service aplay
            if not await self.control_service(self.bluealsa_aplay_service, "start"):
                raise RuntimeError(f"Impossible de démarrer {self.bluealsa_aplay_service}")

            # 3. Configurer l'adaptateur
            if not await self._configure_adapter():
                self.logger.warning("Erreur configuration adaptateur Bluetooth")

            # 4. Démarrer la surveillance des connexions multiples
            self._monitoring_task = asyncio.create_task(self._monitor_connections())

            # 5. Enregistrer l'agent si demandé
            if self.auto_agent and not await self.agent.register():
                self.logger.warning("Erreur enregistrement agent Bluetooth")

            # 6. Démarrer la surveillance BlueALSA
            if not await self.monitor.start_monitoring():
                raise RuntimeError("Erreur démarrage surveillance BlueALSA")

            return True
        except Exception as e:
            self.logger.error(f"Erreur démarrage Bluetooth: {e}")
            await self._cleanup()
            return False
    
    async def _configure_adapter(self) -> bool:
        """Configure l'adaptateur Bluetooth"""
        try:
            commands = "\n".join([
                "power on",
                "system-alias Milo · Bluetooth",
                "discoverable-timeout 0",
                "discoverable on",
                "pairable on",
                "class 0x200404",
                "quit"
            ])
            
            proc = await asyncio.create_subprocess_exec(
                "bluetoothctl",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            
            await proc.communicate(input=commands.encode())
            return proc.returncode == 0
        except Exception as e:
            self.logger.error(f"Erreur configuration adaptateur: {e}")
            return False
    
    async def restart(self) -> bool:
        """Redémarre uniquement bluealsa-aplay pour garder l'appareil connecté"""
        try:
            self.logger.info("Restarting bluealsa-aplay service (keeping device connected)")
            
            # Redémarrer uniquement le service de lecture audio
            success = await self.control_service(self.bluealsa_aplay_service, "restart")
            
            return success
            
        except Exception as e:
            self.logger.error(f"Error restarting bluealsa-aplay: {e}")
            return False
    
    async def stop(self) -> bool:
        """Arrête le plugin Bluetooth"""
        try:
            await self._cleanup()

            # Désactiver la découvrabilité
            await self._run_bluetoothctl_command("discoverable off\npairable off\nquit")

            # Arrêter les services si configuré
            if self.stop_bluetooth:
                await self.control_service(self.bluealsa_aplay_service, "stop")
                for service in [self.bluealsa_service, self.bluetooth_service]:
                    await self.control_service(service, "stop")

            # Réinitialiser l'état
            self.connected_device = None
            await self.notify_state_change(PluginState.INACTIVE)

            return True
        except Exception as e:
            self.logger.error(f"Erreur arrêt plugin Bluetooth: {e}")
            return False

    async def _run_bluetoothctl_command(self, commands) -> bool:
        """Exécute des commandes bluetoothctl"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "bluetoothctl",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )

            await proc.communicate(input=commands.encode())
            return proc.returncode == 0
        except Exception as e:
            self.logger.error(f"Erreur commande bluetoothctl: {e}")
            return False

    async def _monitor_connections(self):
        """Surveille les connexions Bluetooth et déconnecte les appareils supplémentaires"""
        self.logger.info("Surveillance des connexions Bluetooth démarrée")

        while True:
            try:
                await asyncio.sleep(0.5)  # Vérifier toutes les 500ms

                # Lister les appareils connectés via bluetoothctl
                proc = await asyncio.create_subprocess_exec(
                    "bluetoothctl", "devices", "Connected",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL
                )
                stdout, _ = await proc.communicate()

                if proc.returncode != 0:
                    continue

                connected_devices = []
                for line in stdout.decode().splitlines():
                    if line.startswith("Device "):
                        parts = line.split(" ", 2)
                        if len(parts) >= 3:
                            address = parts[1]
                            connected_devices.append(address)

                # Si aucun appareil connecté, réinitialiser
                if len(connected_devices) == 0:
                    self._first_connected_device = None
                    continue

                # Si c'est le premier appareil, l'enregistrer
                if self._first_connected_device is None and len(connected_devices) == 1:
                    self._first_connected_device = connected_devices[0]
                    self.logger.info(f"Premier appareil Bluetooth: {self._first_connected_device}")
                    continue

                # Si plus d'un appareil connecté, déconnecter tous sauf le premier
                if len(connected_devices) > 1:
                    for address in connected_devices:
                        if address != self._first_connected_device:
                            self.logger.warning(
                                f"REFUS: {address} se connecte alors que "
                                f"{self._first_connected_device} est déjà connecté - déconnexion"
                            )
                            await self._disconnect_device(address)

            except asyncio.CancelledError:
                self.logger.info("Surveillance des connexions arrêtée")
                break
            except Exception as e:
                self.logger.error(f"Erreur surveillance connexions: {e}")
                await asyncio.sleep(1)

    async def _disconnect_device(self, address: str) -> bool:
        """Déconnecte un appareil par son adresse"""
        try:
            self.logger.info(f"Déconnexion de l'appareil {address}")
            proc = await asyncio.create_subprocess_exec(
                "bluetoothctl", "disconnect", address,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()

            if proc.returncode != 0:
                self.logger.error(f"Erreur déconnexion {address}: {stderr.decode().strip()}")
                return False

            self.logger.info(f"Appareil {address} déconnecté avec succès")
            return True
        except Exception as e:
            self.logger.error(f"Erreur déconnexion appareil {address}: {e}")
            return False
    
    async def _cleanup(self) -> None:
        """Nettoie les ressources du plugin"""
        # Arrêter toute lecture audio
        await self.playback.stop_all_playback()

        # Arrêter la surveillance
        await self.monitor.stop_monitoring()

        # Arrêter la tâche de surveillance des connexions
        if self._monitoring_task and not self._monitoring_task.done():
            self._monitoring_task.cancel()
            try:
                await self._monitoring_task
            except asyncio.CancelledError:
                pass

        # Réinitialiser l'état
        self._first_connected_device = None

        # Désenregistrer l'agent Bluetooth
        if self.auto_agent:
            await self.agent.unregister()
    
    async def _on_device_connected(self, address: str, name: str) -> None:
        """Callback appelé lors de la connexion d'un appareil"""
        # La boucle de surveillance s'occupe de bloquer les connexions multiples
        # Ici on enregistre juste l'appareil
        if not self.connected_device:
            self.connected_device = {"address": address, "name": name}
        
        # Notifier l'état et démarrer la lecture
        await self.notify_state_change(
            PluginState.CONNECTED, 
            {
                "device_connected": True,
                "device_name": name,
                "device_address": address
            }
        )
        
        # Démarrer la lecture audio
        await self.playback.start_playback(address)
    
    async def _on_device_disconnected(self, address: str, name: str) -> None:
        """Callback appelé lors de la déconnexion d'un appareil"""
        # Vérifier si c'est l'appareil actuel
        if not self.connected_device or self.connected_device.get("address") != address:
            return
            
        # Arrêter la lecture
        await self.playback.stop_playback(address)
        
        # Réinitialiser l'état
        self.connected_device = None
        
        # Notifier le changement d'état
        await self.notify_state_change(PluginState.READY, {"device_connected": False})
    
    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite les commandes pour le plugin"""
        handlers = {
            "disconnect": self._handle_disconnect,
            "restart_audio": self._handle_restart_audio,
            "restart_bluealsa": self._handle_restart_bluealsa,
            "toggle_agent": self._handle_toggle_agent
        }
        
        if command in handlers:
            return await handlers[command](data)
        
        return self.format_response(False, error=f"Commande inconnue: {command}")
    
    async def _handle_disconnect(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Gère la commande de déconnexion"""
        if not self.connected_device:
            return self.format_response(False, error="Aucun périphérique connecté")
        
        address = self.connected_device.get("address")
        
        try:
            proc = await asyncio.create_subprocess_exec(
                "bluetoothctl", "disconnect", address,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await proc.communicate()
            
            if proc.returncode != 0:
                return self.format_response(False, error=stderr.decode().strip())
                
            return self.format_response(True, message=f"Appareil en cours de déconnexion")
        except Exception as e:
            return self.format_response(False, error=str(e))
    
    async def _handle_restart_audio(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Gère la commande de redémarrage audio"""
        if not self.connected_device:
            return self.format_response(False, error="Aucun périphérique connecté")
        
        address = self.connected_device.get("address")
        await self.playback.stop_playback(address)
        success = await self.playback.start_playback(address)
        
        return self.format_response(
            success, 
            message="Lecture audio redémarrée" if success else "Échec du redémarrage audio"
        )
    
    async def _handle_restart_bluealsa(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Gère la commande de redémarrage de bluealsa"""
        result = await self.control_service(self.bluealsa_service, "restart")
        return self.format_response(
            result, 
            message="Service BlueALSA redémarré avec succès" if result else "Échec du redémarrage"
        )
    
    async def _handle_toggle_agent(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Gère la commande de basculement de l'agent"""
        if self.auto_agent:
            await self.agent.unregister()
            self.auto_agent = False
            return self.format_response(True, auto_agent=False, message="Agent Bluetooth désactivé")
        else:
            success = await self.agent.register()
            self.auto_agent = success
            message = "Agent Bluetooth activé" if success else "Échec de l'activation de l'agent"
            return self.format_response(success, auto_agent=success, message=message)
    
    async def get_status(self) -> Dict[str, Any]:
        """Récupère l'état actuel du plugin"""
        try:
            # Vérifier l'état des services
            bt_active = await self.service_manager.is_active(self.bluetooth_service)
            bluealsa_active = await self.service_manager.is_active(self.bluealsa_service)
            aplay_active = await self.service_manager.is_active(self.bluealsa_aplay_service)
            
            return {
                "device_connected": self.connected_device is not None,
                "device_name": self.connected_device.get("name") if self.connected_device else None,
                "device_address": self.connected_device.get("address") if self.connected_device else None,
                "bluetooth_running": bt_active,
                "bluealsa_running": bluealsa_active,
                "aplay_running": aplay_active,
                "auto_agent": self.auto_agent,
                "current_device": self._current_device
            }
        except Exception as e:
            self.logger.error(f"Erreur status: {e}")
            return {"device_connected": False, "current_device": self._current_device, "error": str(e)}