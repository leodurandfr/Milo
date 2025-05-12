"""
Plugin Snapclient optimisé pour oakOS
"""
import asyncio
import logging
from typing import Dict, Any, Optional, List

from backend.infrastructure.plugins.base import UnifiedAudioPlugin
from backend.domain.audio_state import PluginState
from backend.infrastructure.services.systemd_manager import SystemdServiceManager
from backend.infrastructure.plugins.snapclient.discovery import SnapclientDiscovery
from backend.infrastructure.plugins.snapclient.monitor import SnapcastMonitor
from backend.infrastructure.plugins.snapclient.models import SnapclientServer
from backend.infrastructure.plugins.snapclient.connection import SnapclientConnection
from backend.infrastructure.plugins.plugin_utils import safely_control_service, format_response


class SnapclientPlugin(UnifiedAudioPlugin):
    """Plugin pour la source audio Snapclient - Version optimisée"""

    def __init__(self, event_bus, config: Dict[str, Any]):
        super().__init__(event_bus, "snapclient")
        self.config = config
        self.service_name = config.get("service_name", "snapclient.service")
        
        self.service_manager = SystemdServiceManager()
        self.discovery = SnapclientDiscovery()
        self.monitor = SnapcastMonitor(self._handle_monitor_event)
        self.connection = SnapclientConnection(self.service_manager, self)
        
        self.discovered_servers = []
        self._connection_lock = asyncio.Lock()
        self._initialized = False
        self._auto_connecting = False

    async def initialize(self) -> bool:
        """Initialise le plugin"""
        if self._initialized:
            return True
            
        try:
            # Vérifier que le service systemd existe
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "list-unit-files", self.service_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            
            if proc.returncode != 0 or self.service_name not in stdout.decode():
                raise FileNotFoundError(f"Service not found: {self.service_name}")
            
            # Configurer les callbacks du moniteur
            self.discovery.register_callback(self._handle_server_discovery)
            self._initialized = True
            return True
        except Exception as e:
            self.logger.error(f"Erreur d'initialisation: {e}")
            await self.notify_state_change(PluginState.ERROR, {"error": str(e)})
            return False

    async def start(self) -> bool:
        """Démarre le plugin"""
        try:
            self.logger.info("Démarrage du plugin Snapclient")
            self._auto_connecting = False
            
            await self.discovery.start()
            await self.notify_state_change(PluginState.READY)
            return True
        except Exception as e:
            self.logger.error(f"Erreur démarrage plugin: {str(e)}")
            await self.notify_state_change(PluginState.ERROR, {"error": str(e)})
            return False

    async def stop(self) -> bool:
        """Arrête le plugin"""
        try:
            self.logger.info("Arrêt du plugin Snapclient")
            self._auto_connecting = False
            
            await self.monitor.stop()
            await self.connection.disconnect()
            await self.discovery.stop()
            self.discovered_servers = []
            
            await self.notify_state_change(PluginState.INACTIVE)
            return True
        except Exception as e:
            self.logger.error(f"Erreur arrêt plugin: {str(e)}")
            await self.notify_state_change(PluginState.ERROR, {"error": str(e)})
            return False

    async def _handle_server_discovery(self, event_type: str, server: SnapclientServer) -> None:
        """Gère les événements de découverte des serveurs"""
        async with self._connection_lock:
            # Si le serveur a disparu
            if event_type == "removed":
                # Si c'est notre serveur actuel
                if self.connection.current_server and self.connection.current_server.host == server.host:
                    self.logger.info(f"Serveur actuel disparu: {server.name}")
                    await self.notify_state_change(PluginState.READY, {
                        "server_disappeared": True,
                        "host": server.host
                    })
                
                # Mettre à jour la liste
                self.discovered_servers = [s for s in self.discovered_servers if s.host != server.host]
                return

            # Si un serveur est ajouté ou mis à jour
            if event_type in ["added", "updated"]:
                # Éviter les auto-connexions en cascade
                if self._auto_connecting:
                    return
                
                # Mettre à jour la liste
                found = False
                for i, s in enumerate(self.discovered_servers):
                    if s.host == server.host:
                        self.discovered_servers[i] = server
                        found = True
                        break
                if not found:
                    self.discovered_servers.append(server)

                # Notifier la découverte
                await self.notify_state_change(
                    self.current_state,
                    {
                        "server_discovered": True,
                        "server": server.to_dict(),
                        "discovered_count": len(self.discovered_servers)
                    }
                )

                # Auto-connexion si nécessaire
                already_connected = (self.connection.current_server and 
                                    self.connection.current_server.host == server.host and
                                    self.monitor.is_connected)
                
                # Si pas déjà connecté à un serveur, se connecter automatiquement
                if not already_connected and not self.connection.current_server:
                    try:
                        self._auto_connecting = True
                        await self.connection.connect(server)
                    finally:
                        self._auto_connecting = False

    async def _handle_monitor_event(self, data: Dict[str, Any]) -> None:
        """Traite les événements du moniteur WebSocket"""
        event_type = data.get("event")
        host = data.get("host")

        if event_type == "monitor_connected":
            device_name = self.connection.current_server.name if self.connection.current_server else None
            
            await self.notify_state_change(PluginState.CONNECTED, {
                "monitor_connected": True,
                "host": host,
                "device_name": device_name,
                "timestamp": data.get("timestamp")
            })

        elif event_type == "monitor_disconnected" and self.connection.current_server and self.connection.current_server.host == host:
            await self.notify_state_change(PluginState.READY, {
                "monitor_disconnected": True,
                "host": host,
                "reason": data.get("reason"),
                "timestamp": data.get("timestamp")
            })

    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite les commandes"""
        try:
            if command == "discover":
                servers = await self.discovery.discover_servers()
                self.discovered_servers = servers
                return format_response(
                    success=True,
                    servers=[s.to_dict() for s in servers],
                    count=len(servers)
                )
                
            elif command == "restart_service":
                success = await safely_control_service(self.service_manager, self.service_name, "restart", self.logger)
                return format_response(
                    success=success,
                    message="Service redémarré avec succès" if success else "Échec du redémarrage"
                )
            
            elif command == "connect" and data.get("host"):
                # Trouver le serveur par son host
                server = next((s for s in self.discovered_servers if s.host == data["host"]), None)
                if not server:
                    return format_response(
                        success=False,
                        error=f"Serveur non trouvé: {data['host']}"
                    )
                    
                success = await self.connection.connect(server)
                return format_response(
                    success=success,
                    message=f"Connecté à {server.name}" if success else f"Échec de la connexion à {server.name}"
                )
            
            return format_response(success=False, error=f"Commande inconnue: {command}")
        except Exception as e:
            self.logger.error(f"Erreur commande {command}: {e}")
            return format_response(success=False, error=str(e))
        
    async def get_status(self) -> Dict[str, Any]:
        """Récupère l'état actuel du plugin"""
        try:
            connection_info = self.connection.get_connection_info()
            service_status = await self.service_manager.get_status(self.service_name)
            
            return {
                "device_connected": connection_info.get("device_connected", False),
                "discovered_servers": [s.to_dict() for s in self.discovered_servers],
                "current_server": (self.connection.current_server.to_dict() 
                                  if self.connection.current_server else None),
                "monitor_connected": self.monitor.is_connected,
                "service_active": service_status.get("active", False),
                "service_state": service_status.get("state", "unknown")
            }
        except Exception as e:
            self.logger.error(f"Erreur récupération statut: {str(e)}")
            return {"error": str(e)}
    
    async def get_initial_state(self) -> Dict[str, Any]:
        """État initial du plugin"""
        status = await self.get_status()
        connection_info = self.connection.get_connection_info()
        
        return {
            **status,
            **connection_info,
            "is_active": self.current_state != PluginState.INACTIVE
        }