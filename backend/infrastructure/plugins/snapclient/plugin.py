"""
Plugin principal Snapclient pour oakOS.
"""
import asyncio
import logging
import socket
import time
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
        self.polling_interval = config.get("polling_interval", 5.0)
        self.auto_discover = config.get("auto_discover", True)
        self.auto_connect = config.get("auto_connect", True)
        
        # Créer les sous-composants
        self.process_manager = SnapclientProcess(self.executable_path)
        self.discovery = SnapclientDiscovery()
        self.connection_manager = SnapclientConnection(self.process_manager, self)
        self.monitor = SnapcastMonitor(self._handle_monitor_event)
        self.connection_manager.set_auto_connect(self.auto_connect)
        
        # État interne
        self.discovered_servers = []
        self.discovery_task = None
        self.blacklisted_servers = []
        self._avoid_reconnect = False
    
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
        Démarre le plugin et active la source audio Snapclient.
        """
        try:
            self.logger.info("Démarrage du plugin Snapclient")
            
            # CORRECTION CRITIQUE: Toujours réinitialiser la blacklist au démarrage
            if self.blacklisted_servers:
                self.logger.info(f"Réinitialisation de la blacklist au démarrage: {self.blacklisted_servers}")
                self.blacklisted_servers = []
            
            # Réinitialiser l'état
            self.is_active = True
            self.auto_connect = self.config.get("auto_connect", True)
            self.connection_manager.set_auto_connect(self.auto_connect)
            self._avoid_reconnect = False
            
            # Transition d'état initiale
            await self.transition_to_state(self.STATE_INACTIVE)
            await self.transition_to_state(self.STATE_READY_TO_CONNECT)
            
            # Connexion immédiate - SIMPLIFIÉE
            if self.auto_discover:
                # Effectuer une découverte immédiate
                servers = await self.discovery.discover_servers() or []
                self.discovered_servers = servers
                
                # Se connecter au premier serveur si disponible
                if servers and self.auto_connect:
                    self.logger.info(f"Découverte initiale: {len(servers)} serveurs trouvés, tentative de connexion")
                    self._avoid_reconnect = True
                    
                    # Mac-mini explicitement: si le Mac-mini est dans la liste, le connecter en priorité
                    mac_mini_server = next((s for s in servers if s.host == "192.168.1.173"), None)
                    server_to_connect = mac_mini_server or servers[0]
                    
                    # Se connecter au serveur sélectionné
                    await self.connection_manager.connect(server_to_connect)
                    self.logger.info(f"Connexion automatique à {server_to_connect.name} ({server_to_connect.host})")
                    
                    # Transition d'état
                    await self.transition_to_state(self.STATE_CONNECTED, {
                        "connected": True,
                        "deviceConnected": True,
                        "host": server_to_connect.host,
                        "device_name": server_to_connect.name
                    })
                    
                    # Réinitialiser le verrou de reconnexion après un délai
                    asyncio.create_task(self._reset_reconnect_lock())
                
                # Démarrer la boucle de découverte avec un délai
                self.discovery_task = asyncio.create_task(self._delayed_discovery_loop())
            
            return True
        except Exception as e:
            self.logger.error(f"Erreur lors du démarrage du plugin Snapclient: {str(e)}")
            return False
    
    async def _reset_reconnect_lock(self):
        """Réinitialise le verrou de reconnexion après un délai."""
        await asyncio.sleep(5)
        self._avoid_reconnect = False
        self.logger.debug("Verrou de reconnexion réinitialisé")

    async def _delayed_discovery_loop(self):
        """Démarre la boucle de découverte après un délai initial."""
        await asyncio.sleep(3)
        await self._run_discovery_loop()
    
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
            
            # Déconnecter proprement - cette méthode s'assure que tous les processus sont arrêtés
            await self.connection_manager.disconnect()
            
            # Nettoyer l'état
            self.connection_manager.clear_pending_requests()
            self.blacklisted_servers = []
            await self.transition_to_state(self.STATE_INACTIVE)
            
            # Court délai pour s'assurer que tout est bien arrêté
            await asyncio.sleep(0.5)
            
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
            
            # Désactiver l'auto-connect
            self.connection_manager.set_auto_connect(False)
        
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
            self.logger.info(f"Moniteur connecté à {data.get('host')}")
            
            # Publier sur le bus d'événements avec des données plus complètes
            await self.event_bus.publish("snapclient_monitor_connected", {
                "source": "snapclient",
                "host": data.get('host'),
                "plugin_state": self.current_state,
                "timestamp": time.time()
            })
        
        elif event_type == "monitor_disconnected":
            host = data.get("host")
            reason = data.get("reason", "raison inconnue")
            self.logger.warning(f"Moniteur déconnecté de {host}: {reason}")
            
            # Publier sur le bus d'événements avec données enrichies
            await self.event_bus.publish("snapclient_monitor_disconnected", {
                "source": "snapclient",
                "host": host,
                "reason": reason,
                "plugin_state": "ready_to_connect",  # État cible après déconnexion
                "timestamp": time.time()
            })
            
            # Si le serveur a disparu, déconnecter le client
            if self.is_active and self.connection_manager.current_server:
                if self.connection_manager.current_server.host == host:
                    self.logger.warning(f"Serveur {host} déconnecté (détecté via WebSocket)")
                    
                    # Éviter les reconnexions automatiques pendant la déconnexion
                    self._avoid_reconnect = True
                    
                    # Déclencher une déconnexion propre
                    asyncio.create_task(self._handle_server_disconnected(host))
        
        elif event_type == "server_event":
            # Extraire la méthode de l'événement plus proprement
            data_obj = data.get("data", {})
            method = data_obj.get("method") if isinstance(data_obj, dict) else None
            
            if method:
                self.logger.info(f"Événement serveur reçu du WebSocket: {method}")
            else:
                self.logger.debug("Événement serveur WebSocket sans méthode identifiée")
            
            # Publier les événements du serveur avec structure améliorée
            await self.event_bus.publish("snapclient_server_event", {
                "source": "snapclient",
                "method": method,
                "data": data.get("data", {}),
                "timestamp": time.time()
            })

    async def _handle_server_disconnected(self, host: str) -> None:
        """
        Gère la déconnexion d'un serveur détectée par le moniteur.
        
        Args:
            host: Adresse du serveur déconnecté
        """
        try:
            # Vérifier que nous sommes bien connectés à ce serveur
            if not self.connection_manager.current_server or self.connection_manager.current_server.host != host:
                return
                
            self.logger.info(f"Déconnexion du serveur {host} détectée par le moniteur")
            
            # Se déconnecter proprement
            await self.connection_manager.disconnect()
            
            # Mettre à jour l'état
            await self.transition_to_state(self.STATE_READY_TO_CONNECT, {
                "connected": False,
                "deviceConnected": False,
                "disconnection_reason": "server_stopped",
                "disconnected_host": host
            })
            
            # Publier un événement spécifique pour cette déconnexion
            await self.event_bus.publish("snapclient_server_disappeared", {
                "source": "snapclient",
                "host": host,
                "plugin_state": self.STATE_READY_TO_CONNECT,
                "timestamp": time.time()
            })
            
            # Réinitialiser le verrou de reconnexion après un court délai
            asyncio.create_task(self._reset_reconnect_lock())
        except Exception as e:
            self.logger.error(f"Erreur lors du traitement de la déconnexion: {str(e)}")
    
    async def _run_discovery_loop(self):
        """Boucle optimisée de découverte des serveurs."""
        try:
            while self.is_active:
                # Quitter si le plugin n'est plus actif
                if not self.is_active:
                    break
                    
                # Ignorer si le verrou de reconnexion est actif
                if self._avoid_reconnect:
                    self.logger.debug("Découverte ignorée (verrou de reconnexion actif)")
                    await asyncio.sleep(self.polling_interval)
                    continue
                    
                # Vérifier la connexion actuelle ou découvrir de nouveaux serveurs
                if self.connection_manager.current_server:
                    await self._check_current_connection()
                elif self.auto_discover:
                    await self._discover_and_connect()
                
                # Attendre l'intervalle de polling
                await asyncio.sleep(self.polling_interval)
        except asyncio.CancelledError:
            self.logger.info("Tâche de découverte annulée")
        except Exception as e:
            self.logger.error(f"Erreur dans la boucle de découverte: {str(e)}")

    async def _check_current_connection(self):
        """Vérifie si la connexion actuelle est toujours valide."""
        server_host = self.connection_manager.current_server.host
        
        if server_host in self.blacklisted_servers or not await self._is_server_alive(server_host):
            self.logger.warning(f"Serveur {server_host} non disponible ou blacklisté, déconnexion")
            await self.connection_manager.disconnect()
            await self.transition_to_state(self.STATE_READY_TO_CONNECT, {
                "connected": False,
                "deviceConnected": False
            })

    async def _discover_and_connect(self):
        """Découvre les serveurs et se connecte automatiquement si possible."""
        servers = await self.discovery.discover_servers() or []
        filtered_servers = [s for s in servers if s.host not in self.blacklisted_servers]
        self.discovered_servers = filtered_servers
        
        if filtered_servers and self.connection_manager.auto_connect:
            self.logger.info(f"Découverte périodique: {len(filtered_servers)} serveurs disponibles")
            result = await self.connection_manager.handle_discovered_servers(filtered_servers)
            
            if result.get("action") == "auto_connected" and result.get("server"):
                # Activer le verrou de reconnexion pour éviter les connexions multiples
                self._avoid_reconnect = True
                asyncio.create_task(self._reset_reconnect_lock())
                
                await self.transition_to_state(self.STATE_CONNECTED, {
                    "connected": True,
                    "deviceConnected": True,
                    "host": result.get("server", {}).get("host"),
                    "device_name": result.get("server", {}).get("name")
                })

    async def _is_server_alive(self, host: str) -> bool:
        """
        Vérifie si un serveur est accessible (méthode optimisée).
        """
        # Vérifier d'abord si le processus snapclient est toujours en cours
        process_info = await self.process_manager.get_process_info()
        if not process_info.get("running", False):
            return False
        
        # Vérification socket rapide
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.5)  # Timeout court
            result = sock.connect_ex((host, 1704))
            sock.close()
            return result == 0  # 0 = connecté avec succès
        except Exception:
            return False