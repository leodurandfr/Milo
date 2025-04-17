"""
Plugin principal Snapclient pour oakOS - Version Zeroconf.
"""
import asyncio
import logging
import time
import os
import json
from typing import Dict, Any, List, Optional

from backend.application.event_bus import EventBus
from backend.infrastructure.plugins.base import BaseAudioPlugin
from backend.infrastructure.plugins.snapclient.process import SnapclientProcess
from backend.infrastructure.plugins.snapclient.discovery import SnapclientDiscovery
from backend.infrastructure.plugins.snapclient.connection import SnapclientConnection
from backend.infrastructure.plugins.snapclient.models import SnapclientServer
from backend.infrastructure.plugins.snapclient.monitor import SnapcastMonitor

class SnapclientPlugin(BaseAudioPlugin):
    """
    Plugin pour la source audio Snapclient (MacOS via Snapcast).
    Version avec Zeroconf.
    """
    
    def __init__(self, event_bus: EventBus, config: Dict[str, Any]):
        """
        Initialise le plugin Snapclient.
        """
        super().__init__(event_bus, "snapclient")
        
        # Configuration du plugin
        self.config = config
        self.executable_path = config.get("executable_path", "/usr/bin/snapclient")
        self.blacklisted_servers = []
        
        # Créer les sous-composants
        self.process_manager = SnapclientProcess(self.executable_path)
        self.discovery = SnapclientDiscovery()
        self.connection_manager = SnapclientConnection(self.process_manager, self)
        self.monitor = SnapcastMonitor(self._handle_monitor_event)
        
        # Établir une référence croisée entre le moniteur et la connexion
        if hasattr(self.monitor, 'set_connection_reference'):
            self.monitor.set_connection_reference(self.connection_manager)
        
        # État interne
        self.discovered_servers = []
    
    async def initialize(self) -> bool:
        """
        Initialise le plugin.
        """
        self.logger.info("Initialisation du plugin Snapclient")
        
        # Vérifier que l'exécutable snapclient existe
        if not await self.process_manager.check_executable():
            self.logger.error(f"L'exécutable snapclient n'existe pas: {self.executable_path}")
            return False
        
        # Démarrer la découverte Zeroconf en arrière-plan
        if hasattr(self.discovery, 'start'):
            self.discovery.register_callback(self._handle_server_discovery)
            asyncio.create_task(self.discovery.start())
        
        return True
    
    async def start(self) -> bool:
        """
        Démarre le plugin et lance la découverte des serveurs.
        """
        try:
            self.logger.info("Démarrage du plugin Snapclient")
            
            # Réinitialiser la blacklist au démarrage
            self.blacklisted_servers = []
            
            # Réinitialiser l'état
            self.is_active = True
            
            # Transition d'état initiale
            await self.transition_to_state(self.STATE_READY)
            
            # Démarrer la découverte Zeroconf APRÈS l'initialisation du plugin
            if hasattr(self.discovery, 'start'):
                # Enregistrer le callback de découverte
                self.discovery.register_callback(self._handle_server_discovery)
                
                # Démarrer la découverte
                asyncio.create_task(self.discovery.start())
                self.logger.info("Tâche de découverte Zeroconf démarrée")
            
            return True
        except Exception as e:
            self.logger.error(f"Erreur lors du démarrage du plugin: {str(e)}")
            return False
    
    async def _try_connect_to_last_server(self):
        """Tente de se connecter au dernier serveur connu."""
        # Ne pas tenter si déjà connecté
        if self.connection_manager.current_server:
            return
            
        # Vérifier s'il y a un fichier de cache pour le dernier serveur
        try:
            cache_dir = os.path.expanduser("~/.cache/oakos")
            cache_path = os.path.join(cache_dir, "last_snapserver.json")
            
            if not os.path.exists(cache_path):
                return
                
            with open(cache_path, 'r') as f:
                data = json.load(f)
                
            host = data.get("host")
            
            if not host or host in self.blacklisted_servers:
                return
                
            # Vérifier si le serveur est dans la liste des serveurs découverts
            server = next((s for s in self.discovered_servers if s.host == host), None)
            
            if server:
                self.logger.info(f"Tentative de connexion au dernier serveur connu: {server.name}")
                await self.connection_manager.connect(server)
        except Exception as e:
            self.logger.warning(f"Échec de la connexion au dernier serveur: {str(e)}")
    
    async def _handle_server_discovery(self, event_type, server):
        """
        Gère les événements de découverte des serveurs.
        
        Args:
            event_type: Type d'événement ('added', 'updated', 'removed')
            server: Le serveur Snapcast concerné
        """
        try:
            if event_type == "added":
                # Mettre à jour la liste des serveurs découverts
                if server not in self.discovered_servers:
                    self.discovered_servers.append(server)
                
                # Se connecter automatiquement si aucun serveur n'est connecté
                # et que l'auto-connexion est activée
                if (not self.connection_manager.current_server and 
                    self.connection_manager.auto_connect and
                    server.host not in self.blacklisted_servers):
                    
                    self.logger.info(f"Connexion automatique au serveur découvert {server.name}")
                    success = await self.connection_manager.connect(server)
                    
                    if success:
                        await self.transition_to_state(self.STATE_CONNECTED, {
                            "connected": True,
                            "deviceConnected": True,
                            "host": server.host, 
                            "device_name": server.name
                        })
                        
                        # Démarrer le moniteur WebSocket
                        await self.monitor.start(server.host)
                
                # Publier un événement de découverte
                await self.event_bus.publish("snapclient_server_discovered", {
                    "source": "snapclient",
                    "server": server.to_dict()
                })
                    
            elif event_type == "removed":
                # Vérifier si c'est le serveur auquel on est connecté
                if (self.connection_manager.current_server and 
                    self.connection_manager.current_server.host == server.host):
                    
                    self.logger.warning(f"Serveur connecté {server.name} a disparu")
                    
                    # Publier l'événement de disparition du serveur
                    await self.event_bus.publish("snapclient_server_disappeared", {
                        "source": "snapclient",
                        "host": server.host,
                        "plugin_state": self.STATE_READY,
                        "connected": False,
                        "deviceConnected": False
                    })
                    
                    # Transition d'état
                    await self.transition_to_state(self.STATE_READY, {
                        "connected": False,
                        "deviceConnected": False,
                        "disconnection_reason": "server_disappeared"
                    })
                    
                    # Se déconnecter proprement
                    await self.connection_manager.disconnect()
                
                # Mettre à jour la liste des serveurs découverts
                self.discovered_servers = [s for s in self.discovered_servers 
                                        if s.host != server.host]
                                        
        except Exception as e:
            self.logger.error(f"Erreur lors du traitement de l'événement de découverte: {str(e)}")
    
    
    async def _handle_discovery_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """
        Traite les événements de découverte Zeroconf.
        
        Args:
            event_type: Type d'événement ('server_added', 'server_updated', 'server_removed')
            data: Données de l'événement
        """
        try:
            if event_type == 'server_added':
                server_dict = data.get("server")
                if not server_dict:
                    return
                    
                # Convertir en objet SnapclientServer
                server = SnapclientServer(
                    host=server_dict["host"],
                    name=server_dict["name"],
                    port=server_dict.get("port", 1704)
                )
                
                # Vérifier si le serveur n'est pas déjà dans la liste
                if not any(s.host == server.host for s in self.discovered_servers):
                    self.discovered_servers.append(server)
                    
                    # Notifier du nouveau serveur
                    await self.event_bus.publish("snapclient_server_discovered", {
                        "source": "snapclient",
                        "server": server_dict,
                        "timestamp": time.time()
                    })
                    
                    # Si pas connecté et que la connexion auto est activée, se connecter
                    if (not self.connection_manager.current_server and 
                        self.connection_manager.auto_connect and
                        server.host not in self.blacklisted_servers):
                        self.logger.info(f"Connexion automatique au serveur découvert: {server.name}")
                        await self.connection_manager.connect(server)
            
            elif event_type == 'server_removed':
                server_dict = data.get("server")
                if not server_dict:
                    return
                    
                # Supprimer de la liste des serveurs découverts
                self.discovered_servers = [s for s in self.discovered_servers if s.host != server_dict["host"]]
                
                # Vérifier si c'est le serveur actuellement connecté
                if (self.connection_manager.current_server and 
                    self.connection_manager.current_server.host == server_dict["host"]):
                    self.logger.warning(f"Le serveur connecté a disparu: {server_dict['name']}")
                    
                    # Publier un événement de disparition
                    await self.event_bus.publish("snapclient_server_disappeared", {
                        "source": "snapclient",
                        "host": server_dict["host"],
                        "name": server_dict["name"],
                        "reason": "server_removed",
                        "plugin_state": self.STATE_READY,
                        "connected": False,
                        "deviceConnected": False,
                        "timestamp": time.time()
                    })
                    
                    # Transition d'état
                    await self.transition_to_state(self.STATE_READY, {
                        "connected": False,
                        "deviceConnected": False,
                        "disconnection_reason": "server_disappeared"
                    })
                    
                    # Se déconnecter proprement
                    await self.connection_manager.disconnect()
        except Exception as e:
            self.logger.error(f"Erreur lors du traitement d'un événement de découverte: {str(e)}")
    
    async def stop(self) -> bool:
        """
        Arrête le plugin et désactive la source audio Snapclient.
        """
        try:
            self.logger.info("Arrêt du plugin Snapclient")
            self.is_active = False
            
            # Arrêter le moniteur
            await self.monitor.stop()
            
            # Arrêter la découverte Zeroconf
            await self.discovery.stop()
            
            # Déconnecter proprement
            await self.connection_manager.disconnect()
            
            # Nettoyer l'état
            self.connection_manager.clear_pending_requests()
            await self.transition_to_state(self.STATE_INACTIVE)
            
            return True
        except Exception as e:
            self.logger.error(f"Erreur lors de l'arrêt du plugin: {str(e)}")
            return False
    
    async def get_status(self) -> Dict[str, Any]:
        """
        Récupère l'état actuel du plugin.
        """
        try:
            # Récupérer les informations de connexion
            connection_info = self.connection_manager.get_connection_info()
            
            # Récupérer les informations sur le processus
            process_info = await self.process_manager.get_process_info()
            
            # Construire le statut global
            status = {
                "source": "snapclient",
                "plugin_state": self.current_state,
                "is_active": self.is_active,
                "device_connected": connection_info.get("device_connected", False),
                "discovered_servers": [s.to_dict() for s in self.discovered_servers],
                "blacklisted_servers": self.blacklisted_servers,
                "process_info": process_info,
                "metadata": {}
            }
            
            # Ajouter les informations de connexion si disponibles
            if connection_info.get("device_connected"):
                status.update({
                    "connected": True,
                    "deviceConnected": True,
                    "host": connection_info.get("host"),
                    "device_name": connection_info.get("device_name")
                })
                
                status["metadata"].update({
                    "device_name": connection_info.get("device_name"),
                    "host": connection_info.get("host")
                })
            
            return status
        except Exception as e:
            self.logger.error(f"Erreur lors de la récupération du statut: {str(e)}")
            return {
                "source": "snapclient",
                "plugin_state": self.current_state,
                "is_active": self.is_active,
                "error": str(e)
            }
    
    async def get_connection_info(self) -> Dict[str, Any]:
        """
        Récupère les informations de connexion.
        
        Returns:
            Dict[str, Any]: Informations sur la connexion
        """
        return self.connection_manager.get_connection_info()
    
    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Traite une commande spécifique pour ce plugin.
        """
        try:
            self.logger.info(f"Traitement de la commande {command}")
            
            # Ne pas exécuter de commandes si le plugin est inactif
            if not self.is_active and command not in ["get_status"]:
                return {"success": False, "error": "Plugin inactif", "inactive": True}
            
            # Mapping des commandes vers les méthodes
            command_handlers = {
                "connect": self._handle_connect_command,
                "disconnect": self._handle_disconnect_command,
                "accept_connection": self._handle_accept_connection_command,
                "reject_connection": self._handle_reject_connection_command,
                "restart": self._handle_restart_command,
                "discover": self._handle_discover_command
            }
            
            handler = command_handlers.get(command)
            if handler:
                return await handler(data)
            else:
                return {"success": False, "error": f"Commande inconnue: {command}"}
                
        except Exception as e:
            self.logger.error(f"Erreur lors du traitement de la commande: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def _handle_discover_command(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite la commande de découverte des serveurs."""
        try:
            # Si déjà connecté et pas de forçage, renvoyer simplement les serveurs connus
            if self.connection_manager.current_server and not data.get("force", False):
                return {
                    "success": True,
                    "servers": [s.to_dict() for s in self.discovered_servers],
                    "count": len(self.discovered_servers),
                    "message": f"Connecté à {self.connection_manager.current_server.name}",
                    "already_connected": True
                }
            
            # Récupérer simplement les serveurs connus par Zeroconf
            servers = await self.discovery.discover_servers()
            
            # Mettre à jour la liste des serveurs
            self.discovered_servers = servers
            
            # Filtrer les serveurs blacklistés
            filtered_servers = [s for s in servers if s.host not in self.blacklisted_servers]
            
            return {
                "success": True,
                "servers": [s.to_dict() for s in filtered_servers],
                "count": len(filtered_servers),
                "message": f"{len(filtered_servers)} serveurs trouvés"
            }
        except Exception as e:
            self.logger.error(f"Erreur lors de la découverte: {str(e)}")
            return {
                "success": False,
                "error": f"Erreur lors de la découverte: {str(e)}"
            }
    
    async def _handle_connect_command(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite la commande de connexion à un serveur."""
        host = data.get("host")
        if not host:
            return {"success": False, "error": "Host is required"}
        
        # Vérifier si le serveur est blacklisté
        if host in self.blacklisted_servers:
            return {
                "success": False,
                "error": f"Le serveur {host} a été déconnecté manuellement.",
                "blacklisted": True
            }
        
        # Trouver ou créer le serveur
        server = next((s for s in self.discovered_servers if s.host == host), None)
        if not server:
            self.logger.warning(f"Serveur non trouvé pour l'hôte {host}, création d'un serveur virtuel")
            server = SnapclientServer(host=host, name=f"Snapserver ({host})")
        
        # Se connecter au serveur
        success = await self.connection_manager.connect(server)
        
        if success:
            await self.transition_to_state(self.STATE_CONNECTED, {
                "connected": True,
                "deviceConnected": True,
                "host": server.host,
                "device_name": server.name
            })
            
            # Enregistrer dans le cache
            self._save_last_known_server(server)
            
            # Démarrer le moniteur
            await self.monitor.start(server.host)
        
        return {
            "success": success,
            "message": f"Connexion au serveur {server.name}" if success else f"Échec de la connexion",
            "server": server.to_dict() if success else None
        }
    
    async def _handle_disconnect_command(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite la commande de déconnexion."""
        current_server = self.connection_manager.current_server
        
        if current_server:
            # Ajouter à la blacklist
            server_host = current_server.host
            if server_host not in self.blacklisted_servers:
                self.blacklisted_servers.append(server_host)
        
        # Arrêter le moniteur
        await self.monitor.stop()
        
        # Déconnecter
        success = await self.connection_manager.disconnect()
        
        if success:
            await self.transition_to_state(self.STATE_READY)
        
        return {
            "success": success,
            "message": f"Déconnexion du serveur {current_server.name if current_server else 'inconnu'}",
            "blacklisted": self.blacklisted_servers
        }
    
    async def _handle_accept_connection_command(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite la commande d'acceptation d'une demande de connexion."""
        request_id = data.get("request_id")
        host = data.get("host")
        
        if not request_id and not host:
            return {"success": False, "error": "Request ID or host is required"}
        
        # Si pas de request_id, chercher par host
        if not request_id and host:
            for rid, req in self.connection_manager.pending_requests.items():
                if req.server.host == host:
                    request_id = rid
                    break
            
            if not request_id:
                return {"success": False, "error": f"No pending request found for host {host}"}
        
        result = await self.connection_manager.handle_connection_request(request_id, True)
        
        if result.get("success"):
            await self.transition_to_state(self.STATE_CONNECTED, {
                "connected": True,
                "deviceConnected": True,
                "host": result.get("server", {}).get("host"),
                "device_name": result.get("server", {}).get("name", "Appareil MacOS")
            })
            
            # Démarrer le moniteur
            if result.get("server") and result.get("server").get("host"):
                await self.monitor.start(result.get("server").get("host"))
        
        return result
    
    async def _handle_reject_connection_command(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite la commande de rejet d'une demande de connexion."""
        request_id = data.get("request_id")
        host = data.get("host")
        
        if not request_id and not host:
            return {"success": False, "error": "Request ID or host is required"}
        
        # Si pas de request_id, chercher par host
        if not request_id and host:
            for rid, req in self.connection_manager.pending_requests.items():
                if req.server.host == host:
                    request_id = rid
                    break
            
            if not request_id:
                return {"success": False, "error": f"No pending request found for host {host}"}
        
        result = await self.connection_manager.handle_connection_request(request_id, False)
        
        if result.get("success"):
            # Mettre à jour l'état après le rejet
            if self.connection_manager.current_server:
                await self.transition_to_state(self.STATE_CONNECTED, {
                    "connected": True,
                    "deviceConnected": True,
                    "host": self.connection_manager.current_server.host,
                    "device_name": self.connection_manager.current_server.name
                })
            else:
                await self.transition_to_state(self.STATE_READY)
        
        return result
    
    async def _handle_restart_command(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite la commande de redémarrage du processus."""
        host = None
        if self.connection_manager.current_server:
            host = self.connection_manager.current_server.host
        
        success = await self.process_manager.restart(host)
        
        return {
            "success": success,
            "message": "Processus snapclient redémarré" if success else "Échec du redémarrage"
        }
    
    async def _handle_monitor_event(self, data: Dict[str, Any]) -> None:
        """
        Traite les événements provenant du moniteur WebSocket.
        Version améliorée avec meilleure gestion des déconnexions.
        
        Args:
            data: Données de l'événement
        """
        event_type = data.get("event")
        
        if event_type == "monitor_connected":
            host = data.get('host')
            self.logger.info(f"⚡ Moniteur connecté au serveur: {host}")
            
            # Publier l'événement
            await self.event_bus.publish("snapclient_monitor_connected", {
                "source": "snapclient",
                "host": host,
                "plugin_state": self.current_state,
                "timestamp": time.time()
            })
        
        elif event_type == "monitor_disconnected":
            host = data.get("host")
            reason = data.get("reason", "raison inconnue")
            error = data.get("error", "")
            self.logger.warning(f"⚡ Moniteur déconnecté du serveur: {host}: {reason} {error}")
            
            # Publier l'événement de déconnexion immédiatement
            await self.event_bus.publish("snapclient_monitor_disconnected", {
                "source": "snapclient",
                "host": host,
                "reason": reason,
                "error": error,
                "plugin_state": self.STATE_READY,
                "connected": False,
                "deviceConnected": False,
                "timestamp": time.time()
            })
            
            # Si on est connecté au serveur qui a disparu, mettre à jour l'état
            if self.is_active and self.connection_manager.current_server:
                if self.connection_manager.current_server.host == host:
                    self.logger.warning(f"Serveur {host} déconnecté (détecté via WebSocket)")
                    
                    # Publier l'événement de disparition du serveur
                    await self.event_bus.publish("snapclient_server_disappeared", {
                        "source": "snapclient",
                        "host": host,
                        "plugin_state": self.STATE_READY,
                        "connected": False,
                        "deviceConnected": False,
                        "timestamp": time.time()
                    })
                    
                    # Transition d'état
                    await self.transition_to_state(self.STATE_READY, {
                        "connected": False,
                        "deviceConnected": False,
                        "disconnection_reason": "server_stopped",
                        "disconnected_host": host
                    })
                    
                    # Se déconnecter proprement
                    await self.connection_manager.disconnect()
    
    def _save_last_known_server(self, server: SnapclientServer) -> None:
        """Enregistre les informations du serveur dans le cache."""
        try:
            cache_dir = os.path.expanduser("~/.cache/oakos")
            if not os.path.exists(cache_dir):
                os.makedirs(cache_dir, exist_ok=True)
                
            cache_path = os.path.join(cache_dir, "last_snapserver.json")
            
            data = {
                "host": server.host,
                "name": server.name,
                "port": server.port,
                "timestamp": time.time()
            }
            
            with open(cache_path, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            self.logger.warning(f"Erreur lors de l'enregistrement du cache: {str(e)}")