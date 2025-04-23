"""
Plugin principal Snapclient pour oakOS - Version optimisée.
"""
import asyncio
import logging
import time
from typing import Dict, Any, List

from backend.application.event_bus import EventBus
from backend.infrastructure.plugins.base import BaseAudioPlugin
from backend.infrastructure.plugins.snapclient.process import SnapclientProcess
from backend.infrastructure.plugins.snapclient.discovery import SnapclientDiscovery
from backend.infrastructure.plugins.snapclient.connection import SnapclientConnection
from backend.infrastructure.plugins.snapclient.monitor import SnapcastMonitor
from backend.infrastructure.plugins.snapclient.models import SnapclientServer

class SnapclientPlugin(BaseAudioPlugin):
    """Plugin pour la source audio Snapclient (MacOS via Snapcast)."""

    def __init__(self, event_bus: EventBus, config: Dict[str, Any]):
        super().__init__(event_bus, "snapclient")
        self.config = config
        self.executable_path = config.get("executable_path", "/usr/bin/snapclient")
        self.blacklisted_servers = []
        self.process_manager = SnapclientProcess(self.executable_path)
        self.discovery = SnapclientDiscovery()
        self.connection_manager = SnapclientConnection(self.process_manager, self)
        self.monitor = SnapcastMonitor(self._handle_monitor_event)
        self.discovered_servers = []
        self._connection_lock = asyncio.Lock()

    async def initialize(self) -> bool:
        """Initialise le plugin."""
        self.logger.info("Initialisation du plugin Snapclient")
        if not await self.process_manager.check_executable():
            self.logger.error(f"Exécutable snapclient non trouvé: {self.executable_path}")
            return False
        self.discovery.register_callback(self._handle_server_discovery)
        return True

    async def start(self) -> bool:
        """Démarre le plugin et lance la découverte des serveurs."""
        async with self._connection_lock:
            if self.is_active:
                return True
                
            try:
                self.logger.info("Démarrage du plugin Snapclient")
                self.blacklisted_servers = []
                self.is_active = True
                await self.transition_to_state(self.STATE_READY)
                await self.discovery.start()
                return True
            except Exception as e:
                self.logger.error(f"Erreur démarrage plugin: {str(e)}")
                self.is_active = False
                await self.transition_to_state(self.STATE_INACTIVE)
                return False

    async def stop(self) -> bool:
        """Arrête le plugin et désactive la source audio Snapclient."""
        async with self._connection_lock:
            if not self.is_active:
                return True
                
            try:
                self.logger.info("Arrêt du plugin Snapclient")
                self.is_active = False
                await self.monitor.stop()
                await self.discovery.stop()
                await self.connection_manager.disconnect(stop_process=True)
                self.discovered_servers = []
                await self.transition_to_state(self.STATE_INACTIVE)
                return True
            except Exception as e:
                self.logger.error(f"Erreur arrêt plugin: {str(e)}")
                await self.transition_to_state(self.STATE_INACTIVE)
                return False

    async def _handle_server_discovery(self, event_type, server):
        """Gère les événements de découverte des serveurs."""
        if event_type == "removed":
            async with self._connection_lock:
                current_server_obj = self.connection_manager.current_server
                if current_server_obj and current_server_obj.host == server.host:
                    await self.event_bus.publish("snapclient_server_disappeared", {
                        "source": "snapclient", "host": server.host, "reason": "zeroconf_removed",
                        "plugin_state": self.STATE_READY, "connected": False, "deviceConnected": False
                    })
                    await self.monitor.stop()
                    await self.transition_to_state(self.STATE_READY)
                    await self.connection_manager.disconnect(stop_process=True)
                self.discovered_servers = [s for s in self.discovered_servers if s.host != server.host]
            return

        if event_type in ["added", "updated"]:
            async with self._connection_lock:
                # Mettre à jour la liste interne
                found = False
                for i, s in enumerate(self.discovered_servers):
                    if s.host == server.host:
                        self.discovered_servers[i] = server
                        found = True
                        break
                if not found:
                    self.discovered_servers.append(server)

                # Publier l'événement de découverte
                await self.event_bus.publish("snapclient_server_discovered", {
                    "source": "snapclient",
                    "server": server.to_dict(),
                    "event_type": event_type
                })

                # Connexion automatique si nécessaire
                should_connect = (
                    self.is_active and
                    self.current_state == self.STATE_READY and
                    not self.connection_manager.current_server and
                    server.host not in self.blacklisted_servers
                )

                if should_connect:
                    success = await self.connection_manager.connect(server)
                    if success:
                        await self.transition_to_state(self.STATE_CONNECTED, {
                            "connected": True, "deviceConnected": True,
                            "host": server.host, "device_name": server.name
                        })

    async def _handle_monitor_event(self, data: Dict[str, Any]) -> None:
        """Traite les événements provenant du moniteur WebSocket."""
        event_type = data.get("event")
        host = data.get("host")

        if event_type == "monitor_connected":
            # Inclure le nom du serveur dans l'événement
            current_server_obj = self.connection_manager.current_server
            device_name = current_server_obj.name if current_server_obj else None
            
            await self.event_bus.publish("snapclient_monitor_connected", {
                "source": "snapclient", 
                "host": host, 
                "device_name": device_name,  # Ajout du nom ici
                "plugin_state": self.current_state,
                "timestamp": time.time()
            })

        elif event_type == "monitor_disconnected":
            reason = data.get("reason", "unknown")
            await self.event_bus.publish("snapclient_monitor_disconnected", {
                "source": "snapclient", "host": host, "reason": reason, 
                "plugin_state": self.current_state, "connected": False, 
                "deviceConnected": False, "timestamp": time.time()
            })

            async with self._connection_lock:
                current_server_obj = self.connection_manager.current_server
                if self.is_active and current_server_obj and current_server_obj.host == host:
                    await self.monitor.stop()
                    await self.event_bus.publish("snapclient_server_disappeared", {
                        "source": "snapclient", "host": host, 
                        "reason": f"monitor_disconnected ({reason})",
                        "plugin_state": self.STATE_READY, "connected": False, 
                        "deviceConnected": False, "timestamp": time.time()
                    })
                    await self.transition_to_state(self.STATE_READY)
                    await self.connection_manager.disconnect(stop_process=True)

        elif event_type == "server_event":
            await self.event_bus.publish("snapclient_server_event", {
                "source": "snapclient", "host": host,
                "event_data": data.get("data", {}),
                "timestamp": data.get("timestamp", time.time())
            })

    async def get_status(self) -> Dict[str, Any]:
        """Récupère l'état actuel du plugin."""
        try:
            connection_info = self.connection_manager.get_connection_info()
            process_info = await self.process_manager.get_process_info()
            status = {
                "source": "snapclient", "plugin_state": self.current_state,
                "is_active": self.is_active,
                "device_connected": connection_info.get("device_connected", False),
                "discovered_servers": [s.to_dict() for s in self.discovered_servers],
                "blacklisted_servers": self.blacklisted_servers,
                "process_info": process_info, "metadata": {}
            }
            if connection_info.get("device_connected"):
                status.update({
                    "connected": True, "deviceConnected": True,
                    "host": connection_info.get("host"),
                    "device_name": connection_info.get("device_name")
                })
                status["metadata"].update({
                    "device_name": connection_info.get("device_name"),
                    "host": connection_info.get("host")
                })
            return status
        except Exception as e:
            self.logger.error(f"Erreur récupération statut: {str(e)}")
            return {"source": "snapclient", "plugin_state": self.current_state,
                    "is_active": self.is_active, "error": str(e)}

    async def get_connection_info(self) -> Dict[str, Any]:
        """Récupère les informations de connexion."""
        return self.connection_manager.get_connection_info()

    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite une commande spécifique pour ce plugin."""
        try:
            if not self.is_active and command not in ["get_status"]:
                return {"success": False, "error": "Plugin inactif", "inactive": True}

            if command == "connect":
                return await self._handle_connect_command(data)
            elif command == "disconnect":
                return await self._handle_disconnect_command(data)
            elif command == "discover":
                return await self._handle_discover_command(data)
            elif command == "restart":
                return await self._handle_restart_command(data)
            else:
                return {"success": False, "error": f"Commande inconnue: {command}"}
        except Exception as e:
            self.logger.error(f"Erreur commande {command}: {str(e)}")
            return {"success": False, "error": str(e)}

    async def _handle_discover_command(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite la commande API de découverte des serveurs."""
        try:
            servers_from_discovery = await self.discovery.discover_servers()
            self.discovered_servers = servers_from_discovery

            current_conn_info = self.connection_manager.get_connection_info()
            if current_conn_info["device_connected"] and not data.get("force", False):
                return {"success": True, "servers": [s.to_dict() for s in servers_from_discovery],
                        "count": len(servers_from_discovery), 
                        "message": f"Connecté à {current_conn_info['device_name']}",
                        "already_connected": True}

            filtered_servers = [s for s in servers_from_discovery if s.host not in self.blacklisted_servers]
            return {"success": True, "servers": [s.to_dict() for s in filtered_servers],
                    "count": len(filtered_servers), 
                    "message": f"{len(filtered_servers)} serveurs trouvés"}
        except Exception as e:
            return {"success": False, "error": f"Erreur découverte: {str(e)}"}

    async def _handle_connect_command(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite la commande API de connexion à un serveur."""
        host = data.get("host")
        if not host: 
            return {"success": False, "error": "Host is required"}

        async with self._connection_lock:
            if host in self.blacklisted_servers:
                return {"success": False, "error": f"Serveur {host} blacklisté",
                        "blacklisted": True}

            server = next((s for s in self.discovered_servers if s.host == host), None)
            if not server:
                server = SnapclientServer(host=host, name=f"Snapserver ({host})")

            success = await self.connection_manager.connect(server)
            if success:
                await self.transition_to_state(self.STATE_CONNECTED, {
                    "connected": True, "deviceConnected": True,
                    "host": server.host, "device_name": server.name
                })

            return {"success": success, 
                    "message": f"Connexion à {server.name}" if success else f"Échec connexion à {server.name}",
                    "server": server.to_dict() if success else None}

    async def _handle_disconnect_command(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite la commande API de déconnexion."""
        async with self._connection_lock:
            current_server = self.connection_manager.current_server
            server_name = current_server.name if current_server else 'inconnu'
            server_host = current_server.host if current_server else None

            if server_host and server_host not in self.blacklisted_servers:
                self.blacklisted_servers.append(server_host)

            success = await self.connection_manager.disconnect(stop_process=True)
            if success:
                await self.transition_to_state(self.STATE_READY)

            return {"success": success, "message": f"Déconnexion de {server_name}",
                    "blacklisted": self.blacklisted_servers}

    async def _handle_restart_command(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite la commande API de redémarrage du processus."""
        host = None
        current_conn_info = self.connection_manager.get_connection_info()
        if current_conn_info["device_connected"]:
            host = current_conn_info["host"]

        success = await self.process_manager.restart(host)

        if success and self.current_state == self.STATE_CONNECTED and host:
            await self.monitor.start(host)

        return {"success": success, 
                "message": "Processus snapclient redémarré" if success else "Échec redémarrage"}