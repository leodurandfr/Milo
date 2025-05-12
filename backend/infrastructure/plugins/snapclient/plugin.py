"""
Plugin Snapclient optimisé pour oakOS
"""
import asyncio
from typing import Dict, Any, List

from backend.infrastructure.plugins.base import UnifiedAudioPlugin
from backend.domain.audio_state import PluginState
from backend.infrastructure.plugins.snapclient.discovery import SnapclientDiscovery
from backend.infrastructure.plugins.snapclient.monitor import SnapcastMonitor
from backend.infrastructure.plugins.snapclient.connection import SnapclientConnection
from backend.infrastructure.plugins.snapclient.models import SnapclientServer

class SnapclientPlugin(UnifiedAudioPlugin):
    """Plugin pour la source audio Snapclient - Version concise"""

    def __init__(self, event_bus, config: Dict[str, Any]):
        super().__init__(event_bus, "snapclient")
        self.config = config
        self.service_name = config.get("service_name", "snapclient.service")
        
        # Composants
        self.discovery = SnapclientDiscovery()
        self.monitor = SnapcastMonitor(self._handle_monitor_event)
        self.connection = SnapclientConnection(self.service_manager, self)
        
        # État
        self.discovered_servers = []
        self._connection_lock = asyncio.Lock()
        self._auto_connecting = False

    async def initialize(self) -> bool:
        """Initialise le plugin"""
        try:
            # Vérifier que le service systemd existe
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "list-unit-files", self.service_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()
            
            if proc.returncode != 0 or self.service_name not in stdout.decode():
                raise RuntimeError(f"Service {self.service_name} non trouvé")
            
            # Configurer le callback de découverte
            self.discovery.register_callback(self._handle_server_discovery)
            return True
        except Exception as e:
            self.logger.error(f"Erreur initialisation: {e}")
            return False

    async def start(self) -> bool:
        """Démarre le plugin"""
        try:
            # Démarrer la découverte de serveurs
            await self.discovery.start()
            await self.notify_state_change(PluginState.READY)
            return True
        except Exception as e:
            self.logger.error(f"Erreur démarrage: {e}")
            await self.notify_state_change(PluginState.ERROR, {"error": str(e)})
            return False

    async def stop(self) -> bool:
        """Arrête le plugin"""
        try:
            self._auto_connecting = False
            
            # Arrêter tous les composants
            await self.monitor.stop()
            await self.connection.disconnect()
            await self.discovery.stop()
            
            # Réinitialiser l'état
            self.discovered_servers = []
            await self.notify_state_change(PluginState.INACTIVE)
            
            return True
        except Exception as e:
            self.logger.error(f"Erreur arrêt: {e}")
            return False

    async def _handle_server_discovery(self, event_type: str, server: SnapclientServer) -> None:
        """Gère les événements de découverte des serveurs"""
        async with self._connection_lock:
            # Serveur supprimé
            if event_type == "removed":
                if self.connection.current_server and self.connection.current_server.host == server.host:
                    await self.notify_state_change(PluginState.READY, {
                        "server_disappeared": True,
                        "host": server.host
                    })
                
                self.discovered_servers = [s for s in self.discovered_servers if s.host != server.host]
                return

            # Serveur ajouté ou mis à jour
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

                # Auto-connexion si configuré
                if (not self.connection.current_server and 
                        not self._auto_connecting and 
                        self.config.get("auto_connect", False)):
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
            # Serveur connecté
            device_name = self.connection.current_server.name if self.connection.current_server else None
            await self.notify_state_change(PluginState.CONNECTED, {
                "monitor_connected": True,
                "host": host,
                "device_name": device_name
            })
        elif event_type == "monitor_disconnected" and self.connection.current_server and self.connection.current_server.host == host:
            # Serveur déconnecté
            await self.notify_state_change(PluginState.READY, {
                "monitor_disconnected": True,
                "host": host,
                "reason": data.get("reason")
            })

    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite les commandes du plugin"""
        try:
            if command == "discover":
                # Découvrir les serveurs
                servers = await self.discovery.discover_servers()
                self.discovered_servers = servers
                return self.format_response(
                    True,
                    servers=[s.to_dict() for s in servers],
                    count=len(servers)
                )
                
            elif command == "restart_service":
                # Redémarrer le service
                success = await self.control_service(self.service_name, "restart")
                return self.format_response(
                    success,
                    message="Service redémarré avec succès" if success else "Échec du redémarrage"
                )
            
            elif command == "connect" and data.get("host"):
                # Connecter à un serveur
                server = next((s for s in self.discovered_servers if s.host == data["host"]), None)
                if not server:
                    return self.format_response(False, error=f"Serveur non trouvé: {data['host']}")
                    
                success = await self.connection.connect(server)
                return self.format_response(
                    success,
                    message=f"Connecté à {server.name}" if success else f"Échec de la connexion à {server.name}"
                )
            
            return self.format_response(False, error=f"Commande inconnue: {command}")
        except Exception as e:
            self.logger.error(f"Erreur commande {command}: {e}")
            return self.format_response(False, error=str(e))
        
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
            self.logger.error(f"Erreur status: {e}")
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