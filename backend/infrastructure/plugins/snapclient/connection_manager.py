"""
Gestionnaire de connexions pour le plugin snapclient.
"""
import asyncio
import logging
import time
from typing import Dict, Any, Optional, List, Set, Callable

class ConnectionManager:
    """
    Gère les connexions entrantes de serveurs Snapcast et les transitions
    entre différents serveurs.
    """
    
    def __init__(self, event_bus, metadata_processor, process_manager):
        self.logger = logging.getLogger("snapclient.connection")
        self.event_bus = event_bus
        self.metadata_processor = metadata_processor
        self.process_manager = process_manager
        
        # État des connexions
        self.active_connection: Optional[Dict[str, Any]] = None
        self.pending_connections: Dict[str, Dict[str, Any]] = {}
        self.rejected_hosts: Set[str] = set()
        self.connection_history: List[Dict[str, Any]] = []
        
        # État interne
        self.connection_task = None
        self.listen_task = None
    
    async def start(self) -> None:
        """Démarre le gestionnaire de connexions"""
        if self.connection_task is None or self.connection_task.done():
            self.connection_task = asyncio.create_task(self._monitor_connections())
            self.logger.info("Gestionnaire de connexions Snapcast démarré")
    
    async def stop(self) -> None:
        """Arrête le gestionnaire de connexions"""
        if self.connection_task and not self.connection_task.done():
            self.connection_task.cancel()
            try:
                await self.connection_task
            except asyncio.CancelledError:
                pass
            self.connection_task = None
            self.logger.info("Gestionnaire de connexions Snapcast arrêté")
    
    async def handle_new_server_discovery(self, server_info: Dict[str, Any]) -> None:
        """
        Gère la découverte d'un nouveau serveur Snapcast.
        Décide si une connexion automatique est appropriée ou si une demande
        utilisateur est nécessaire.
        
        Args:
            server_info: Informations sur le serveur découvert
        """
        host = server_info["host"]
        
        # Ignorer les hôtes rejetés récemment
        if host in self.rejected_hosts:
            self.logger.debug(f"Serveur ignoré (rejeté précédemment): {host}")
            return
        
        # Si aucune connexion active, se connecter automatiquement
        if not self.active_connection:
            await self.connect_to_server(host)
            return
        
        # Si c'est le même serveur, ignorer
        if self.active_connection["host"] == host:
            self.logger.debug(f"Serveur déjà connecté: {host}")
            return
            
        # Sinon, ajouter aux connexions en attente
        self.logger.info(f"Nouvelle connexion demandée de: {host}")
        
        # Créer la demande de connexion
        request = {
            "host": host,
            "timestamp": time.time(),
            "auto_accept_time": time.time() + 60,  # Auto-accepter après 60 secondes (configurable)
            "status": "pending"
        }
        
        # Ajouter aux connexions en attente
        self.pending_connections[host] = request
        
        # Publier un événement pour notifier l'utilisateur
        await self.event_bus.publish("snapclient_connection_request", {
            "source": "snapclient",
            "host": host,
            "request_id": host,  # Utiliser l'hôte comme ID de requête pour simplicité
            "current_host": self.active_connection["host"] if self.active_connection else None
        })
    
    async def accept_connection_request(self, host: str) -> bool:
        """
        Accepte une demande de connexion entrante.
        
        Args:
            host: Hôte à accepter
            
        Returns:
            bool: True si l'acceptation a réussi, False sinon
        """
        if host not in self.pending_connections:
            self.logger.warning(f"Tentative d'accepter une connexion non demandée: {host}")
            return False
        
        # Marquer comme acceptée
        self.pending_connections[host]["status"] = "accepted"
        
        # Se connecter au serveur
        return await self.connect_to_server(host)
    
    async def reject_connection_request(self, host: str) -> bool:
        """
        Rejette une demande de connexion entrante.
        
        Args:
            host: Hôte à rejeter
            
        Returns:
            bool: True si le rejet a réussi, False sinon
        """
        if host not in self.pending_connections:
            self.logger.warning(f"Tentative de rejeter une connexion non demandée: {host}")
            return False
            
        # Supprimer de la liste des demandes en attente
        request = self.pending_connections.pop(host)
        request["status"] = "rejected"
        
        # Ajouter à l'historique
        self.connection_history.append(request)
        
        # Ajouter à la liste des hôtes rejetés (temporairement)
        self.rejected_hosts.add(host)
        
        # Planifier la suppression de l'hôte de la liste des rejetés après un certain temps
        asyncio.create_task(self._remove_from_rejected_after_delay(host, 300))  # 5 minutes
        
        # Publier un événement pour informer du rejet
        await self.event_bus.publish("snapclient_connection_rejected", {
            "source": "snapclient",
            "host": host
        })
        
        return True
    
    async def connect_to_server(self, host: str) -> bool:
        """
        Se connecte à un serveur Snapcast spécifique.
        
        Args:
            host: Hôte du serveur
            
        Returns:
            bool: True si la connexion a réussi, False sinon
        """
        self.logger.info(f"Tentative de connexion au serveur Snapcast: {host}")
        
        if self.active_connection and self.active_connection["host"] == host:
            self.logger.info(f"Déjà connecté à {host}")
            return True
        
        # Publier un événement de changement d'état
        await self.metadata_processor.publish_status("connecting", {
            "host": host,
            "timestamp": time.time()
        })
        
        try:
            # Démarrer/redémarrer le processus snapclient avec le nouvel hôte
            self.logger.info(f"Lancement du processus snapclient vers {host}")
            if await self.process_manager.restart_process(host):
                # Mettre à jour la connexion active
                self.active_connection = {
                    "host": host,
                    "connected_at": time.time(),
                    "status": "connected"
                }
                
                # Supprimer des connexions en attente si présent
                if host in self.pending_connections:
                    request = self.pending_connections.pop(host)
                    request["status"] = "accepted"
                    self.connection_history.append(request)
                
                # Publier un événement de connexion réussie
                await self.metadata_processor.publish_status("connected", {
                    "host": host,
                    "deviceConnected": True,
                    "connected": True,
                    "device_name": host  # Utiliser l'hôte comme nom d'appareil par défaut
                })
                
                self.logger.info(f"Connexion réussie au serveur Snapcast: {host}")
                return True
            else:
                self.logger.error(f"Échec du processus snapclient pour l'hôte {host}")
                
                # Publier un événement d'échec
                await self.metadata_processor.publish_status("error", {
                    "host": host,
                    "error": "connection_failed"
                })
                
                return False
        except Exception as e:
            self.logger.error(f"Exception lors de la connexion au serveur Snapcast {host}: {str(e)}")
            
            # Publier un événement d'échec
            await self.metadata_processor.publish_status("error", {
                "host": host,
                "error": f"exception: {str(e)}"
            })
            
            return False
    
    async def disconnect(self) -> bool:
        """
        Déconnecte du serveur Snapcast actuel.
        
        Returns:
            bool: True si la déconnexion a réussi, False sinon
        """
        if not self.active_connection:
            self.logger.debug("Aucune connexion active à déconnecter")
            return True
        
        host = self.active_connection["host"]
        self.logger.info(f"Déconnexion du serveur Snapcast: {host}")
        
        # Arrêter le processus snapclient
        if await self.process_manager.stop_process():
            # Mettre à jour l'état interne
            self.active_connection = None
            
            # Publier un événement de déconnexion
            await self.metadata_processor.publish_status("disconnected", {
                "deviceConnected": False,
                "connected": False
            })
            
            self.logger.info(f"Déconnexion réussie du serveur Snapcast: {host}")
            return True
        else:
            self.logger.error(f"Échec de la déconnexion du serveur Snapcast: {host}")
            return False
    
    def get_active_connection(self) -> Optional[Dict[str, Any]]:
        """
        Récupère les informations sur la connexion active.
        
        Returns:
            Optional[Dict[str, Any]]: Informations sur la connexion active ou None
        """
        return self.active_connection
    
    def get_pending_connections(self) -> List[Dict[str, Any]]:
        """
        Récupère la liste des connexions en attente.
        
        Returns:
            List[Dict[str, Any]]: Liste des connexions en attente
        """
        return list(self.pending_connections.values())
    
    async def _monitor_connections(self) -> None:
        """Surveille les connexions en attente pour les auto-accepter si nécessaire"""
        try:
            while True:
                try:
                    current_time = time.time()
                    hosts_to_auto_accept = []
                    
                    # Vérifier les connexions en attente
                    for host, request in self.pending_connections.items():
                        # Auto-accepter après le délai configuré si aucune action n'a été prise
                        if request["status"] == "pending" and current_time >= request["auto_accept_time"]:
                            self.logger.info(f"Auto-acceptation de la connexion après délai: {host}")
                            hosts_to_auto_accept.append(host)
                    
                    # Traiter les auto-acceptations
                    for host in hosts_to_auto_accept:
                        await self.accept_connection_request(host)
                        
                except Exception as e:
                    self.logger.error(f"Erreur lors de la surveillance des connexions: {str(e)}")
                
                await asyncio.sleep(5)  # Vérifier toutes les 5 secondes
                
        except asyncio.CancelledError:
            self.logger.debug("Surveillance des connexions annulée")
            raise
    
    async def _remove_from_rejected_after_delay(self, host: str, delay: int) -> None:
        """
        Retire un hôte de la liste des rejetés après un délai.
        
        Args:
            host: Hôte à retirer
            delay: Délai en secondes
        """
        await asyncio.sleep(delay)
        if host in self.rejected_hosts:
            self.rejected_hosts.remove(host)
            self.logger.debug(f"Hôte retiré de la liste des rejetés: {host}")