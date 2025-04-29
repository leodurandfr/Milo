"""
Plugin Snapclient intégré pour oakOS.
"""
import asyncio
import logging
from typing import Dict, Any, Optional, List

from backend.application.event_bus import EventBus
from backend.infrastructure.plugins.base import UnifiedAudioPlugin
from backend.domain.audio_state import PluginState, AudioSource
from backend.infrastructure.plugins.snapclient.discovery import SnapclientDiscovery
from backend.infrastructure.plugins.snapclient.monitor import SnapcastMonitor
from backend.infrastructure.plugins.snapclient.models import SnapclientServer
from backend.infrastructure.plugins.snapclient.process import SnapclientProcess


class SnapclientPlugin(UnifiedAudioPlugin):
    """Plugin unifié pour la source audio Snapclient."""

    def __init__(self, event_bus: EventBus, config: Dict[str, Any]):
        super().__init__(event_bus, "snapclient")
        self.config = config
        self.executable_path = config.get("executable_path", "/usr/bin/snapclient")
        self.auto_connect = config.get("auto_connect", True)
        
        # État interne
        self.current_server: Optional[SnapclientServer] = None
        self.blacklisted_servers = []
        self.discovered_servers = []
        
        # Composants
        self.discovery = SnapclientDiscovery()
        self.monitor = SnapcastMonitor(self._handle_monitor_event)
        self.process_manager = SnapclientProcess(self.executable_path)
        
        # Verrou pour synchronisation
        self._lock = asyncio.Lock()
        self._initialized = False

    def get_process_command(self) -> List[str]:
        """Retourne la commande pour démarrer snapclient."""
        cmd = [self.executable_path, "-j"]
        if self.current_server:
            cmd.extend(["-h", self.current_server.host])
        return cmd

    async def initialize(self) -> bool:
        """Initialise le plugin."""
        if self._initialized:
            return True
            
        self.logger.info("Initialisation du plugin Snapclient")
        try:
            # Vérifier l'exécutable
            import os
            if not os.path.exists(self.executable_path):
                raise FileNotFoundError(f"Executable not found: {self.executable_path}")
            
            # Configurer la découverte
            self.discovery.register_callback(self._handle_server_discovery)
            
            self._initialized = True
            return True
        except Exception as e:
            self.logger.error(f"Erreur d'initialisation: {e}")
            await self.notify_state_change(PluginState.ERROR, {"error": str(e)})
            return False

    async def start(self) -> bool:
        """Démarre le plugin."""
        async with self._lock:
            try:
                self.logger.info("Démarrage du plugin Snapclient")
                self.blacklisted_servers = []
                
                # Démarrer la découverte
                await self.discovery.start()
                
                await self.notify_state_change(PluginState.READY)
                return True
            except Exception as e:
                self.logger.error(f"Erreur démarrage: {str(e)}")
                await self.notify_state_change(PluginState.ERROR, {"error": str(e)})
                return False

    async def stop(self) -> bool:
        """Arrête le plugin."""
        async with self._lock:
            try:
                self.logger.info("Arrêt du plugin Snapclient")
                
                # Arrêter le moniteur
                await self.monitor.stop()
                
                # Arrêter la découverte
                await self.discovery.stop()
                
                # Réinitialiser l'état
                self.current_server = None
                self.discovered_servers = []
                
                await self.notify_state_change(PluginState.INACTIVE)
                return True
            except Exception as e:
                self.logger.error(f"Erreur arrêt: {str(e)}")
                await self.notify_state_change(PluginState.ERROR, {"error": str(e)})
                return False

    async def _handle_server_discovery(self, event_type: str, server: SnapclientServer) -> None:
        """Gère les événements de découverte des serveurs."""
        async with self._lock:
            if event_type == "removed":
                # Si c'est notre serveur actuel
                if self.current_server and self.current_server.host == server.host:
                    await self.monitor.stop()
                    self.current_server = None
                    await self.notify_state_change(PluginState.READY, {
                        "server_disappeared": True,
                        "host": server.host
                    })
                
                # Mettre à jour la liste
                self.discovered_servers = [s for s in self.discovered_servers if s.host != server.host]
                return

            if event_type in ["added", "updated"]:
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
                    self.current_state,  # Garder l'état actuel
                    {
                        "server_discovered": True,
                        "server": server.to_dict(),
                        "discovered_count": len(self.discovered_servers)
                    }
                )

                # Auto-connexion si configuré et pas de serveur actuel
                if (self.auto_connect and 
                    not self.current_server and 
                    server.host not in self.blacklisted_servers):
                    await self._connect_to_server(server)

    async def _handle_monitor_event(self, data: Dict[str, Any]) -> None:
        """Traite les événements du moniteur WebSocket."""
        event_type = data.get("event")
        
        if event_type == "monitor_connected":
            await self.notify_state_change(PluginState.CONNECTED, {
                "monitor_connected": True,
                "host": data.get("host"),
                "device_name": self.current_server.name if self.current_server else None
            })

        elif event_type == "monitor_disconnected":
            async with self._lock:
                if self.current_server and self.current_server.host == data.get("host"):
                    await self.monitor.stop()
                    
                    # On garde self.current_server pour la reconnexion automatique
                    # mais on notifie que la connexion est perdue
                    await self.notify_state_change(PluginState.READY, {
                        "monitor_disconnected": True,
                        "host": data.get("host"),
                        "reason": data.get("reason", "unknown")
                    })

        elif event_type == "server_event":
            # On peut traiter ici les événements spécifiques du serveur si nécessaire
            pass

    async def _connect_to_server(self, server: SnapclientServer) -> bool:
        """Connecte à un serveur spécifique."""
        try:
            self.current_server = server
            
            # Si on utilise la machine à états pour gérer les processus
            if self._state_machine and hasattr(self._state_machine, 'process_manager'):
                await self._state_machine.process_manager.stop_process(self._get_audio_source())
                await self._state_machine.process_manager.start_process(
                    self._get_audio_source(),
                    self.get_process_command()
                )
            else:
                # Gestion directe du processus si pas de machine à états
                await self.process_manager.start(server.host)
            
            # Démarrer le moniteur WS
            await self.monitor.start(server.host)
            
            # On ne met pas à jour l'état ici car le moniteur le fera
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur connexion: {e}")
            return False

    async def get_status(self) -> Dict[str, Any]:
        """Récupère l'état actuel du plugin."""
        return {
            "device_connected": self.current_server is not None,
            "discovered_servers": [s.to_dict() for s in self.discovered_servers],
            "blacklisted_servers": self.blacklisted_servers,
            "current_server": self.current_server.to_dict() if self.current_server else None,
            "monitor_connected": self.monitor.is_connected
        }

    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite les commandes spécifiques."""
        try:
            async with self._lock:
                if command == "connect":
                    host = data.get("host")
                    if not host:
                        return {"success": False, "error": "Host required"}
                    
                    server = next((s for s in self.discovered_servers if s.host == host), None)
                    if not server:
                        server = SnapclientServer(host=host, name=f"Server ({host})")
                    
                    success = await self._connect_to_server(server)
                    return {"success": success}
                
                elif command == "disconnect":
                    if self.current_server:
                        self.blacklisted_servers.append(self.current_server.host)
                    
                    await self.monitor.stop()
                    
                    if self._state_machine and hasattr(self._state_machine, 'process_manager'):
                        await self._state_machine.process_manager.stop_process(self._get_audio_source())
                    else:
                        await self.process_manager.stop()
                    
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
                
                else:
                    return {"success": False, "error": f"Unknown command: {command}"}
                
        except Exception as e:
            self.logger.error(f"Error handling command {command}: {e}")
            return {"success": False, "error": str(e)}

    async def get_connection_info(self) -> Dict[str, Any]:
        """Récupère des informations sur la connexion actuelle."""
        if not self.current_server:
            return {"device_connected": False, "device_name": None, "host": None}

        return {
            "device_connected": True,
            "device_name": self.current_server.name,
            "host": self.current_server.host,
            "port": self.current_server.port
        }