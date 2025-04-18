"""
Plugin principal Snapclient pour oakOS - Version minimale.
"""
import asyncio
import logging
import time
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
    Version minimale simplifiée.
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
        
        # N'enregistre que le callback pour la découverte
        self.discovery.register_callback(self._handle_server_discovery)
        
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
            
            # Démarrer la découverte Zeroconf
            await self.discovery.start()
            self.logger.info("Découverte Zeroconf démarrée avec la source snapclient")
            
            return True
        except Exception as e:
            self.logger.error(f"Erreur lors du démarrage du plugin: {str(e)}")
            return False
    
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
            
            # Déconnecter proprement - force l'arrêt du processus
            await self.connection_manager.disconnect(stop_process=True)
            
            # Nettoyer l'état
            self.connection_manager.clear_pending_requests()
            await self.transition_to_state(self.STATE_INACTIVE)
            
            return True
        except Exception as e:
            self.logger.error(f"Erreur lors de l'arrêt du plugin: {str(e)}")
            return False
    
    async def _handle_server_discovery(self, event_type, server):
        """
        Gère les événements de découverte des serveurs.
        
        Args:
            event_type: Type d'événement ('added', 'updated', 'removed')
            server: Le serveur Snapcast concerné
        """
        try:
            if event_type == "added":
                # Mettre à jour la liste des serveurs
                if server not in self.discovered_servers:
                    self.discovered_servers.append(server)
                    
                # Publier un événement de découverte
                await self.event_bus.publish("snapclient_server_discovered", {
                    "source": "snapclient",
                    "server": server.to_dict()
                })
                
                # Si en état READY et pas connecté à un serveur, se connecter
                if (self.is_active and 
                    self.current_state == self.STATE_READY and 
                    not self.connection_manager.current_server and 
                    server.host not in self.blacklisted_servers):
                    
                    self.logger.info(f"Connexion automatique au serveur découvert: {server.name}")
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
                
            elif event_type == "removed":
                # Vérifier si c'est le serveur actuellement connecté
                if (self.connection_manager.current_server and 
                    self.connection_manager.current_server.host == server.host):
                    
                    self.logger.warning(f"Serveur connecté {server.name} a disparu")
                    
                    # Publier l'événement de disparition
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
                    
                    # Se déconnecter proprement SANS arrêter le processus
                    await self.connection_manager.disconnect(stop_process=False)
                
                # Mettre à jour la liste des serveurs découverts
                self.discovered_servers = [s for s in self.discovered_servers 
                                        if s.host != server.host]
                
        except Exception as e:
            self.logger.error(f"Erreur lors du traitement de l'événement de découverte: {str(e)}")
    
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
            
            # Traitement des commandes essentielles
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
        
        # Déconnecter avec arrêt complet du processus seulement si c'est une déconnexion manuelle
        stop_process = data.get("stop_process", True)  # Par défaut, arrêter le processus si c'est une déconnexion manuelle
        success = await self.connection_manager.disconnect(stop_process=stop_process)
        
        if success:
            await self.transition_to_state(self.STATE_READY)
        
        return {
            "success": success,
            "message": f"Déconnexion du serveur {current_server.name if current_server else 'inconnu'}",
            "blacklisted": self.blacklisted_servers
        }
    
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
                    
                    # Se déconnecter proprement SANS arrêter le processus
                    await self.connection_manager.disconnect(stop_process=False)