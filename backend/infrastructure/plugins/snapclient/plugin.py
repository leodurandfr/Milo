"""
Plugin Snapclient adapté au contrôle centralisé des processus - Version avec gestion optimisée des reconnexions.
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
from backend.infrastructure.plugins.snapclient.process import SnapclientProcess
from backend.infrastructure.plugins.snapclient.connection import SnapclientConnection


class SnapclientPlugin(UnifiedAudioPlugin):
    """Plugin pour la source audio Snapclient - Version avec contrôle centralisé"""

    def __init__(self, event_bus: EventBus, config: Dict[str, Any]):
        super().__init__(event_bus, "snapclient")
        self.config = config
        self.executable_path = config.get("executable_path", "/usr/bin/snapclient")
        self.blacklisted_servers = []
        
        # Initialisation des composants
        self.process_manager = SnapclientProcess(self.executable_path)
        self.discovery = SnapclientDiscovery()
        self.monitor = SnapcastMonitor(self._handle_monitor_event)
        self.connection = SnapclientConnection(self.process_manager, self)
        
        # État interne
        self.discovered_servers = []
        self._connection_lock = asyncio.Lock()
        self._initialized = False
        self._auto_connecting = False  # Flag pour éviter les connexions en cascade

    def get_process_command(self) -> List[str]:
        """Retourne la commande pour démarrer snapclient"""
        # Si un serveur est configuré, inclure le host dans la commande
        cmd = [self.executable_path, "-j"]
        if self.connection.current_server:
            cmd.extend(["-h", self.connection.current_server.host])
        return cmd

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
                self._auto_connecting = False
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
                self._auto_connecting = False
                await self.monitor.stop()
                await self.connection.disconnect()
                await self.discovery.stop()
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
        """Gère les événements de découverte des serveurs avec logique améliorée pour éviter les reconnexions inutiles"""
        if event_type == "removed":
            async with self._connection_lock:
                # Si c'est notre serveur actuel qui a disparu
                if self.connection.current_server and self.connection.current_server.host == server.host:
                    self.logger.info(f"Serveur actuel disparu: {server.name}")
                    # Ne pas arrêter le processus ni le moniteur ici - permettra une reconnexion plus rapide
                    # si le serveur réapparaît
                    
                    await self.notify_state_change(PluginState.READY, {
                        "server_disappeared": True,
                        "host": server.host
                    })
                
                # Mettre à jour la liste des serveurs découverts
                self.discovered_servers = [s for s in self.discovered_servers if s.host != server.host]
            return

        if event_type in ["added", "updated"]:
            async with self._connection_lock:
                # Si on est en train de gérer une auto-connexion, éviter les connexions en cascade
                if self._auto_connecting:
                    self.logger.debug(f"Auto-connexion en cours, ignorer {server.name}")
                    return
                
                # Mettre à jour la liste des serveurs
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
                    self.current_state,  # Conserver l'état actuel
                    {
                        "server_discovered": True,
                        "server": server.to_dict(),
                        "discovered_count": len(self.discovered_servers)
                    }
                )

                # Vérifier si on est déjà connecté à ce serveur
                already_connected = (self.connection.current_server and 
                                    self.connection.current_server.host == server.host and
                                    self.monitor.is_connected)
                
                # Auto-connexion si configuré, mais pas de reconnexion si déjà connecté
                if (self.config.get("auto_connect", True) and
                    not already_connected and
                    not self.connection.current_server and
                    server.host not in self.blacklisted_servers):
                    
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
            # IMPORTANT: Toujours notifier la connexion, même lors d'une reconnexion
            device_name = self.connection.current_server.name if self.connection.current_server else None
            
            self.logger.info(f"Moniteur connecté au serveur {device_name}")
            await self.notify_state_change(PluginState.CONNECTED, {
                "monitor_connected": True,
                "host": host,
                "device_name": device_name,
                "timestamp": data.get("timestamp", time.time())
            })

        elif event_type == "monitor_disconnected":
            reason = data.get("reason", "unknown")
            async with self._connection_lock:
                if self.connection.current_server and self.connection.current_server.host == host:
                    self.logger.info(f"Moniteur déconnecté: {reason}")
                    # Passer à l'état READY mais garder le current_server pour la reconnexion
                    await self.notify_state_change(PluginState.READY, {
                        "monitor_disconnected": True,
                        "host": host,
                        "reason": reason,
                        "timestamp": data.get("timestamp", time.time())
                    })

    async def get_status(self) -> Dict[str, Any]:
        """Récupère l'état actuel du plugin"""
        try:
            connection_info = self.connection.get_connection_info()
            process_info = await self.process_manager.get_process_info()
            
            return {
                "device_connected": connection_info.get("device_connected", False),
                "discovered_servers": [s.to_dict() for s in self.discovered_servers],
                "blacklisted_servers": self.blacklisted_servers,
                "current_server": (self.connection.current_server.to_dict() 
                                  if self.connection.current_server else None),
                "monitor_connected": self.monitor.is_connected,
                "process_running": process_info.get("running", False)
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
                
                success = await self.connection.connect(server)
                return {"success": success}
            
            elif command == "disconnect":
                async with self._connection_lock:
                    if self.connection.current_server:
                        self.blacklisted_servers.append(self.connection.current_server.host)
                    
                    await self.connection.disconnect()
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
        
    async def get_connection_info(self) -> Dict[str, Any]:
        """Récupère des informations sur la connexion actuelle"""
        return self.connection.get_connection_info()

    async def get_initial_state(self) -> Dict[str, Any]:
        """
        Récupère l'état initial complet du plugin.
        Utilisé lors de l'initialisation d'une connexion WebSocket.
        """
        status = await self.get_status()
        connection_info = await self.get_connection_info()
        
        return {
            **status,
            **connection_info,
            "is_active": self.current_state != PluginState.INACTIVE
        }