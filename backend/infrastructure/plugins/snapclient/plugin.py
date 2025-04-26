# backend/infrastructure/plugins/snapclient/plugin.py
"""
Plugin Snapclient adapté au contrôle centralisé des processus
"""
import asyncio
import logging
import time
from typing import Dict, Any, Optional, List

from backend.application.event_bus import EventBus
from backend.infrastructure.plugins.base import UnifiedAudioPlugin
from backend.domain.audio_state import PluginState
from backend.infrastructure.plugins.snapclient.discovery import SnapclientDiscovery
from backend.infrastructure.plugins.snapclient.monitor import SnapcastMonitor
from backend.infrastructure.plugins.snapclient.models import SnapclientServer


class SnapclientPlugin(UnifiedAudioPlugin):
    """Plugin pour la source audio Snapclient - Version avec contrôle centralisé"""

    def __init__(self, event_bus: EventBus, config: Dict[str, Any]):
        super().__init__(event_bus, "snapclient")
        self.config = config
        self.executable_path = config.get("executable_path", "/usr/bin/snapclient")
        self.blacklisted_servers = []
        self.discovery = SnapclientDiscovery()
        self.monitor = SnapcastMonitor(self._handle_monitor_event)
        self.discovered_servers = []
        self._connection_lock = asyncio.Lock()
        self._initialized = False
        self.current_server = None

    def get_process_command(self) -> List[str]:
        """Retourne la commande pour démarrer snapclient"""
        # Si un serveur est configuré, inclure le host dans la commande
        if self.current_server and self.current_server.host:
            return [self.executable_path, "-h", self.current_server.host]
        return [self.executable_path]

    async def initialize(self) -> bool:
        """Initialise le plugin"""
        if self._initialized:
            return True
            
        self.logger.info("Initialisation du plugin Snapclient")
        # Vérifier que l'exécutable existe
        try:
            import os
            if not os.path.exists(self.executable_path):
                raise FileNotFoundError(f"Executable not found: {self.executable_path}")
            
            self.discovery.register_callback(self._handle_server_discovery)
            self._initialized = True
            return True
        except Exception as e:
            self.logger.error(f"Erreur d'initialisation: {e}")
            await self.notify_state_change(PluginState.ERROR, {
                "error": str(e),
                "error_type": "initialization_error"
            })
            return False

    async def start(self) -> bool:
        """Démarre le plugin - le processus est géré par la machine à états"""
        async with self._connection_lock:
            try:
                self.logger.info("Démarrage du plugin Snapclient")
                self.blacklisted_servers = []
                await self.discovery.start()
                await self.notify_state_change(PluginState.READY)
                return True
            except Exception as e:
                self.logger.error(f"Erreur démarrage plugin: {str(e)}")
                await self.notify_state_change(PluginState.ERROR, {
                    "error": str(e),
                    "error_type": "start_error"
                })
                return False

    async def stop(self) -> bool:
        """Arrête le plugin - le processus est géré par la machine à états"""
        async with self._connection_lock:
            try:
                self.logger.info("Arrêt du plugin Snapclient")
                await self.monitor.stop()
                await self.discovery.stop()
                self.current_server = None
                self.discovered_servers = []
                await self.notify_state_change(PluginState.INACTIVE)
                return True
            except Exception as e:
                self.logger.error(f"Erreur arrêt plugin: {str(e)}")
                await self.notify_state_change(PluginState.ERROR, {
                    "error": str(e),
                    "error_type": "stop_error"
                })
                return False

    async def _handle_server_discovery(self, event_type: str, server: SnapclientServer) -> None:
        """Gère les événements de découverte des serveurs"""
        if event_type == "removed":
            async with self._connection_lock:
                if self.current_server and self.current_server.host == server.host:
                    # Le serveur actuel a disparu
                    await self.monitor.stop()
                    self.current_server = None
                    await self.notify_state_change(PluginState.READY, {
                        "server_disappeared": True,
                        "host": server.host
                    })
                
                self.discovered_servers = [s for s in self.discovered_servers if s.host != server.host]
            return

        if event_type in ["added", "updated"]:
            async with self._connection_lock:
                # Mise à jour de la liste des serveurs
                found = False
                for i, s in enumerate(self.discovered_servers):
                    if s.host == server.host:
                        self.discovered_servers[i] = server
                        found = True
                        break
                if not found:
                    self.discovered_servers.append(server)

                # Notifier la découverte
                await self.notify_state_change(PluginState.READY, {
                    "server_discovered": True,
                    "server": server.to_dict(),
                    "discovered_count": len(self.discovered_servers)
                })

                # Auto-connexion si configuré
                if (self.config.get("auto_connect", True) and
                    not self.current_server and
                    server.host not in self.blacklisted_servers):
                    await self._connect_to_server(server)

    async def _handle_monitor_event(self, data: Dict[str, Any]) -> None:
        """Traite les événements du moniteur WebSocket"""
        event_type = data.get("event")
        host = data.get("host")

        if event_type == "monitor_connected":
            device_name = self.current_server.name if self.current_server else None
            
            await self.notify_state_change(PluginState.CONNECTED, {
                "monitor_connected": True,
                "host": host,
                "device_name": device_name
            })

        elif event_type == "monitor_disconnected":
            reason = data.get("reason", "unknown")
            async with self._connection_lock:
                if self.current_server and self.current_server.host == host:
                    await self.monitor.stop()
                    self.current_server = None
                    await self.notify_state_change(PluginState.READY, {
                        "monitor_disconnected": True,
                        "host": host,
                        "reason": reason
                    })

    async def _connect_to_server(self, server: SnapclientServer) -> bool:
        """Connecte à un serveur spécifique"""
        try:
            self.current_server = server
            
            # Notifier la machine à états de redémarrer le processus avec le nouveau serveur
            if self._state_machine and hasattr(self._state_machine, 'process_manager'):
                # Demander à la machine à états de redémarrer le processus
                await self._state_machine.process_manager.stop_process(self._get_audio_source())
                await self._state_machine.process_manager.start_process(
                    self._get_audio_source(),
                    self.get_process_command()
                )
            
            # Démarrer le moniteur
            await self.monitor.start(server.host)
            
            await self.notify_state_change(PluginState.CONNECTED, {
                "connected": True,
                "host": server.host,
                "device_name": server.name
            })
            return True
        except Exception as e:
            self.logger.error(f"Erreur de connexion: {e}")
            return False

    async def get_status(self) -> Dict[str, Any]:
        """Récupère l'état actuel du plugin"""
        try:
            return {
                "device_connected": self.current_server is not None,
                "discovered_servers": [s.to_dict() for s in self.discovered_servers],
                "blacklisted_servers": self.blacklisted_servers,
                "current_server": self.current_server.to_dict() if self.current_server else None
            }
        except Exception as e:
            self.logger.error(f"Erreur récupération statut: {str(e)}")
            return {"error": str(e)}

    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite les commandes spécifiques"""
        try:
            if command == "connect":
                host = data.get("host")
                if not host:
                    return {"success": False, "error": "Host required"}
                
                server = next((s for s in self.discovered_servers if s.host == host), None)
                if not server:
                    server = SnapclientServer(host=host, name=f"Snapserver ({host})")
                
                success = await self._connect_to_server(server)
                return {"success": success}
            
            elif command == "disconnect":
                async with self._connection_lock:
                    if self.current_server:
                        self.blacklisted_servers.append(self.current_server.host)
                    
                    self.current_server = None
                    await self.notify_state_change(PluginState.READY)
                    
                    return {"success": True}
            
            elif command == "discover":
                servers = await self.discovery.discover_servers()
                self.discovered_servers = servers
                return {
                    "success": True,
                    "servers": [s.to_dict() for s in servers],
                    "count": len(servers)
                }
            
            return {"success": False, "error": f"Unknown command: {command}"}
        except Exception as e:
            self.logger.error(f"Error handling command {command}: {e}")
            return {"success": False, "error": str(e)}