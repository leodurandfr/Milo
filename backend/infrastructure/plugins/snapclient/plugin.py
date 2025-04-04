"""
Plugin principal Snapclient pour oakOS - Version simplifiée.
"""
import asyncio
import logging
import socket
import time
import os
import json
from typing import Dict, Any, List

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
        self._avoid_reconnect = False
        
        # Créer les sous-composants
        self.process_manager = SnapclientProcess(self.executable_path)
        self.discovery = SnapclientDiscovery()
        self.connection_manager = SnapclientConnection(self.process_manager, self)
        self.monitor = SnapcastMonitor(self._handle_monitor_event)
        
        # État interne
        self.discovered_servers = []
        self.discovery_task = None
    
    async def initialize(self) -> bool:
        """
        Initialise le plugin.
        """
        self.logger.info("Initialisation du plugin Snapclient")
        
        # Vérifier que l'exécutable snapclient existe
        if not await self.process_manager.check_executable():
            self.logger.error(f"L'exécutable snapclient n'existe pas ou n'est pas exécutable: {self.executable_path}")
            return False
        
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
            self._avoid_reconnect = False
            
            # Transition d'état initiale
            await self.transition_to_state(self.STATE_READY_TO_CONNECT)
            
            # Démarrer la découverte immédiatement
            asyncio.create_task(self._perform_discovery_and_connect())
            
            return True
        except Exception as e:
            self.logger.error(f"Erreur lors du démarrage du plugin Snapclient: {str(e)}")
            return False
    
    async def _perform_discovery_and_connect(self):
        """Découvre les serveurs et se connecte au premier disponible."""
        try:
            # Ne pas effectuer de découverte si déjà connecté
            if self.connection_manager.current_server:
                self.logger.info("Déjà connecté, découverte ignorée")
                return
                
            self.logger.info("Découverte des serveurs Snapcast")
            
            # Effectuer la découverte
            servers = await self.discovery.discover_servers() or []
            self.discovered_servers = servers
            
            # Se connecter au premier serveur si disponible
            if servers and not self.connection_manager.current_server:
                # Filtrer les serveurs blacklistés
                available_servers = [s for s in servers if s.host not in self.blacklisted_servers]
                
                if available_servers:
                    server = available_servers[0]
                    self.logger.info(f"Connexion au serveur découvert {server.name} ({server.host})")
                    
                    success = await self.connection_manager.connect(server)
                    
                    if success:
                        await self.transition_to_state(self.STATE_CONNECTED, {
                            "connected": True,
                            "deviceConnected": True,
                            "host": server.host, 
                            "device_name": server.name
                        })
                        
                        # Démarrer le moniteur WebSocket immédiatement
                        await self.monitor.start(server.host)
                        
                        # Enregistrer ce serveur comme dernier serveur connu
                        self._save_last_known_server(server)
                        
                        # Notifier l'UI immédiatement
                        await self.event_bus.publish("snapclient_status_updated", {
                            "source": "snapclient",
                            "plugin_state": self.STATE_CONNECTED,
                            "connected": True,
                            "deviceConnected": True,
                            "host": server.host,
                            "device_name": server.name,
                            "timestamp": time.time()
                        })
                else:
                    self.logger.info("Aucun serveur non blacklisté disponible")
            else:
                self.logger.info(f"Découverte terminée: {len(servers)} serveurs trouvés, connexion manuelle requise")
                
        except Exception as e:
            self.logger.error(f"Erreur lors de la découverte initiale: {str(e)}")
    
    async def stop(self) -> bool:
        """
        Arrête le plugin et désactive la source audio Snapclient.
        """
        try:
            self.logger.info("Arrêt du plugin Snapclient")
            self.is_active = False
            
            # Arrêter la tâche de découverte
            if self.discovery_task and not self.discovery_task.done():
                self.discovery_task.cancel()
                try:
                    await self.discovery_task
                except asyncio.CancelledError:
                    pass
            
            # Arrêter le moniteur
            await self.monitor.stop()
            
            # Déconnecter proprement - cette méthode s'assure que tous les processus sont arrêtés
            await self.connection_manager.disconnect()
            
            # Nettoyer l'état
            self.connection_manager.clear_pending_requests()
            await self.transition_to_state(self.STATE_INACTIVE)
            
            return True
        except Exception as e:
            self.logger.error(f"Erreur lors de l'arrêt du plugin Snapclient: {str(e)}")
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
                "pending_requests": self.connection_manager.get_pending_requests(),
                "process_info": process_info,
                "blacklisted_servers": self.blacklisted_servers,
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
                self.logger.warning(f"Commande {command} ignorée, plugin inactif")
                return {
                    "success": False, 
                    "error": "Plugin inactif, commande ignorée",
                    "inactive": True
                }
            
            # Dispatcher les commandes vers des méthodes dédiées
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
                self.logger.warning(f"Commande inconnue: {command}")
                return {"success": False, "error": f"Commande inconnue: {command}"}
                
        except Exception as e:
            self.logger.error(f"Erreur lors du traitement de la commande {command}: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def _handle_discover_command(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite la commande de découverte des serveurs."""
        try:
            # Si déjà connecté, renvoyer simplement les serveurs connus sauf si force=true
            if self.connection_manager.current_server and not data.get("force", False):
                return {
                    "success": True,
                    "servers": [s.to_dict() for s in self.discovered_servers],
                    "count": len(self.discovered_servers),
                    "message": f"Connecté à {self.connection_manager.current_server.name}, découverte ignorée",
                    "already_connected": True
                }
            
            # Déclencher une découverte immédiate
            servers = await self.discovery.discover_servers()
            
            # Filtrer les serveurs blacklistés
            filtered_servers = [s for s in servers if s.host not in self.blacklisted_servers]
            self.discovered_servers = filtered_servers
            
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
                "error": f"Le serveur {host} a été déconnecté manuellement. Changez de source audio pour pouvoir vous y reconnecter.",
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
            # Optimisation: si le nom est générique, essayer de trouver un meilleur nom
            device_name = server.name
            if device_name == f"Snapserver ({server.host})":
                try:
                    hostname = socket.gethostbyaddr(server.host)
                    if hostname and hostname[0]:
                        device_name = hostname[0]
                except:
                    pass
            
            await self.transition_to_state(self.STATE_CONNECTED, {
                "connected": True,
                "deviceConnected": True,
                "host": server.host,
                "device_name": device_name
            })
            
            # Enregistrer dans le cache
            self._save_last_known_server(server)
            
            # Démarrer le moniteur
            await self.monitor.start(server.host)
        
        return {
            "success": success,
            "message": f"Connexion au serveur {server.name}" if success else f"Échec de la connexion au serveur {server.name}",
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
                self.logger.info(f"Serveur {server_host} ajouté à la blacklist: {self.blacklisted_servers}")
        
        # Arrêter le moniteur
        await self.monitor.stop()
        
        # Déconnecter
        success = await self.connection_manager.disconnect()
        
        if success:
            await self.transition_to_state(self.STATE_READY_TO_CONNECT)
        
        return {
            "success": success,
            "message": f"Déconnexion du serveur {current_server.name if current_server else 'inconnu'}",
            "blacklisted": self.blacklisted_servers
        }
    
    async def _handle_accept_connection_command(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite la commande d'acceptation d'une demande de connexion."""
        # Récupérer l'ID de la demande ou trouver par l'hôte
        request_id = data.get("request_id")
        if not request_id:
            host = data.get("host")
            if not host:
                return {"success": False, "error": "Request ID or host is required"}
            
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
        # Récupérer l'ID de la demande ou trouver par l'hôte
        request_id = data.get("request_id")
        if not request_id:
            host = data.get("host")
            if not host:
                return {"success": False, "error": "Request ID or host is required"}
            
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
                await self.transition_to_state(self.STATE_READY_TO_CONNECT)
        
        return result
    
    async def _handle_restart_command(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite la commande de redémarrage du processus."""
        host = None
        if self.connection_manager.current_server:
            host = self.connection_manager.current_server.host
        
        success = await self.process_manager.restart(host)
        
        return {
            "success": success,
            "message": "Processus snapclient redémarré" if success else "Échec du redémarrage du processus snapclient"
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
            
            # Toujours publier l'événement, peu importe l'état interne
            await self.event_bus.publish("snapclient_monitor_connected", {
                "source": "snapclient",
                "host": host,
                "plugin_state": self.current_state,
                "timestamp": time.time()
            })
        
        elif event_type == "monitor_disconnected":
            host = data.get("host")
            reason = data.get("reason", "raison inconnue")
            self.logger.warning(f"⚡ Moniteur déconnecté du serveur: {host}: {reason}")
            
            # TOUJOURS publier l'événement de déconnexion pour le frontend
            await self.event_bus.publish("snapclient_monitor_disconnected", {
                "source": "snapclient",
                "host": host,
                "reason": reason,
                "plugin_state": self.STATE_READY_TO_CONNECT,
                "connected": False,
                "deviceConnected": False,
                "timestamp": time.time()
            })
            
            # Si on est connecté au serveur qui a disparu, mettre à jour l'état interne
            if self.is_active and self.connection_manager.current_server:
                if self.connection_manager.current_server.host == host:
                    self.logger.warning(f"Serveur {host} déconnecté (détecté via WebSocket)")
                    
                    # Publier l'événement de disparition du serveur
                    await self.event_bus.publish("snapclient_server_disappeared", {
                        "source": "snapclient",
                        "host": host,
                        "plugin_state": self.STATE_READY_TO_CONNECT,
                        "connected": False,
                        "deviceConnected": False,
                        "timestamp": time.time()
                    })
                    
                    # Mettre à jour l'état immédiatement
                    await self.transition_to_state(self.STATE_READY_TO_CONNECT, {
                        "connected": False,
                        "deviceConnected": False,
                        "disconnection_reason": "server_stopped",
                        "disconnected_host": host
                    })
                    
                    # Se déconnecter proprement
                    await self.connection_manager.disconnect()
        
        elif event_type == "server_event":
            # Extraire la méthode de l'événement plus proprement
            data_obj = data.get("data", {})
            method = data_obj.get("method") if isinstance(data_obj, dict) else None
            
            if method:
                self.logger.info(f"Événement serveur reçu du WebSocket: {method}")
            
            # Publier les événements du serveur
            await self.event_bus.publish("snapclient_server_event", {
                "source": "snapclient",
                "method": method,
                "data": data.get("data", {}),
                "timestamp": time.time()
            })
            
            # Si l'événement indique un changement important, forcer une vérification de l'état
            if method in ["Client.OnDisconnect", "Server.OnUpdate"]:
                self.logger.info(f"Événement critique détecté: {method}, vérification de l'état...")
                
                # Si le serveur indique un client déconnecté, vérifier si c'est nous
                if method == "Client.OnDisconnect" and self.connection_manager.current_server:
                    client_data = data_obj.get("params", {})
                    # Vérifier si les données du client correspondent à notre connexion
                    # Cette vérification dépend de la structure des données renvoyées
                    
                    # Forcer une vérification de la connexion
                    current_server = self.connection_manager.current_server
                    if current_server:
                        process_info = await self.process_manager.get_process_info()
                        if not process_info.get("running", False):
                            self.logger.warning("Processus snapclient détecté comme arrêté, forçage de la déconnexion")
                            await self.publish_plugin_state(self.STATE_READY_TO_CONNECT, {
                                "connected": False,
                                "deviceConnected": False,
                            })
    
    def _get_last_known_server(self) -> dict:
        """Récupère les informations du dernier serveur connu depuis le cache."""
        try:
            # Vérifier si nous avons un fichier de cache
            cache_path = os.path.expanduser("~/.cache/oakos/last_snapserver.json")
            
            if os.path.exists(cache_path):
                with open(cache_path, 'r') as f:
                    data = json.load(f)
                    
                # Vérifier que les données sont récentes (moins de 7 jours)
                if data.get('timestamp', 0) > time.time() - 7*24*3600:
                    self.logger.info(f"Serveur en cache trouvé: {data.get('host')}")
                    return data
            
            return None
        except Exception as e:
            self.logger.warning(f"Erreur lors de la lecture du cache: {str(e)}")
            return None

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
                
            self.logger.debug(f"Serveur enregistré dans le cache: {server.host}")
        except Exception as e:
            self.logger.warning(f"Erreur lors de l'enregistrement du cache: {str(e)}")